"""Slideshow for slot-limited SwitchBot AI Art Frames (``SWITCHBOT_SLIDESHOW``).

With the slideshow on, a SwitchBot frame's whole library — curated, uploaded, imported and
kept-in-sync photos alike — is the deck, and only a small batch of it is *on air*: 1 photo in
'slyde' mode, the frame's 10 slots in 'frame' mode. The delivery queue carries just that batch.
Every interval (outside quiet hours, and only while someone is home if a presence entity is set)
the batch is replaced by a fresh draw from the durable Shuffle-Deck in ``Store``, so every photo is
shown once before any repeats. Interval, quiet hours and presence entity are set per frame in the UI
(``slideshow_setting``); the ``SWITCHBOT_*`` settings are their defaults.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime

from .backends.switchbot import SwitchBotBackend
from .config import Settings
from .frame import Frame
from .library import FrameLibrary, LibraryItem
from .presence import Presence
from .store import Store

# The Shuffle-Deck key (shuffle_epoch/shuffle_history ``folder``) of a frame's library-wide deck.
DECK = "*"


@dataclass(frozen=True)
class SlideshowConfig:
    interval_minutes: int
    quiet_hours: str  # "HH:MM-HH:MM" local time, "" = none
    presence_entity: str  # HA entity that says someone is home, "" = always


class Slideshow:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        library: FrameLibrary,
        rng: random.Random | None = None,
        presence: Presence | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._library = library
        self._rng = rng
        self._presence = presence or Presence(settings)

    def active(self, frame: Frame | None) -> bool:
        """Whether ``frame`` runs Slyde's slideshow (a SwitchBot frame with the slideshow on)."""
        return (
            frame is not None
            and frame.backend == SwitchBotBackend.name
            and self._settings.shuffle_batch_size > 0
        )

    def config(self, frame_id: str) -> SlideshowConfig:
        """The frame's slideshow settings from the UI, else the configured defaults."""
        saved = self._store.slideshow_setting(frame_id)
        if saved is not None:
            return SlideshowConfig(*saved)
        return SlideshowConfig(
            self._settings.switchbot_slideshow_interval_minutes,
            self._settings.switchbot_quiet_hours,
            self._settings.switchbot_presence_entity,
        )

    @property
    def presence(self) -> Presence:
        return self._presence

    def someone_home(self, config: SlideshowConfig) -> bool:
        """Whether the presence entity allows a change; unknown counts as home (fails open)."""
        entity = config.presence_entity
        return not entity or self._presence.home(entity) is not False

    async def refresh_presence(self, frame_ids: list[str]) -> None:
        """Re-read the presence entities of ``frame_ids`` from Home Assistant (cached a minute)."""
        entities = {self.config(f).presence_entity for f in frame_ids}
        await self._presence.refresh({e for e in entities if e})

    def on_air(self, frame_id: str, *, now: datetime, advance: bool = False) -> list[LibraryItem]:
        """The library photos the frame should have now: a fresh draw once the interval has
        elapsed (outside quiet hours, while someone is home) or when ``advance`` asks for the next
        one, otherwise the held
        batch — topped up if some of its photos left the library."""
        pool = {i.asset_id: i for i in self._library.desired(frame_id)}
        size = self._settings.shuffle_batch_size
        prior = self._store.slideshow_batch(frame_id)
        config = self.config(frame_id)
        due = (
            not in_quiet_hours(config.quiet_hours, now)
            and self.someone_home(config)
            and self._store.should_rotate_shuffle_batch(
                frame_id, DECK, now=now, interval_minutes=config.interval_minutes
            )
        )
        held = [aid for aid in prior if aid in pool]
        if not held or advance or due:
            # A photo of the outgoing batch that never reached the frame wasn't really shown: put
            # it back in the deck instead of counting it as seen for this epoch.
            delivered = self._store.delivered_payloads(frame_id)
            self._store.forget_shuffle_draws(
                frame_id,
                DECK,
                [aid for aid in held if delivered.get(pool[aid].dest_name) != aid],
            )
            chosen = self._store.draw_shuffle_batch(
                frame_id,
                DECK,
                list(pool),
                size,
                rng=self._rng,
                now_iso=now.isoformat(),
                avoid=prior,
            )
        else:
            chosen = held
            if len(chosen) < size:
                chosen += self._store.draw_shuffle_batch(
                    frame_id,
                    DECK,
                    [aid for aid in pool if aid not in set(held)],
                    size - len(held),
                    rng=self._rng,
                    now_iso=now.isoformat(),
                    touch_rotation=False,
                )
        chosen = chosen[:size]
        self._store.set_slideshow_batch(frame_id, chosen)
        return [pool[aid] for aid in chosen]


def in_quiet_hours(window: str, when: datetime) -> bool:
    """Whether ``when`` (converted to the host's local time) falls in ``window`` ("HH:MM-HH:MM",
    may wrap past midnight like "22:00-07:00"). An empty window is never quiet."""
    if not window:
        return False
    start, _, end = window.partition("-")
    local = when.astimezone().strftime("%H:%M")
    if start <= end:
        return start <= local < end
    return local >= start or local < end
