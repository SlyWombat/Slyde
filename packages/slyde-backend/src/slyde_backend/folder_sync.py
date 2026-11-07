"""Keep-in-sync as per-folder *bindings* on the delivery queue (#62).

Replaces the legacy device-mirror subscription (``SyncService.sync_subscription``): a binding maps a
Library **folder** to an Immich album. Reconciling it sets that folder's ``source='sync'`` library
rows to the album's current images (add new / drop departed) and lets the **delivery queue** carry
them — so keep-in-sync now works for **connected AND served** frames, with per-photo delivery state,
offline-tolerant, and unified with curation/uploads. The legacy ``album_sync`` table is reused, with
``target_album`` reinterpreted as the Library folder, so existing subscriptions migrate in place.

Migration safety: photos already on a connected device via the legacy engine share the same
canonical ``dest_name`` (Phase 2), so we mark those delivered up front — no re-push, no sync gap.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from datetime import UTC, datetime

from .backends.switchbot import SwitchBotBackend
from .config import Settings
from .delivery_service import DeliveryService
from .immich import ImmichAsset, ImmichClient
from .library import FrameLibrary, LibraryItem
from .naming import dest_name_for
from .schemas import SyncResult
from .store import Store, Subscription

_log = logging.getLogger(__name__)


class FolderSyncService:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        library: FrameLibrary,
        delivery: DeliveryService,
        immich_factory: Callable[[], ImmichClient],
    ) -> None:
        self._settings = settings
        self._store = store
        self._library = library
        self._delivery = delivery
        self._immich_factory = immich_factory

    def list_bindings(self, frame_id: str) -> list[Subscription]:
        return self._store.list_subscriptions(frame_id)

    def unbind(self, frame_id: str, immich_album_id: str) -> bool:
        """Stop keeping a folder in sync. The already-delivered photos stay in the library."""
        return self._store.remove_subscription(frame_id, immich_album_id)

    async def bind(
        self,
        frame_id: str,
        immich_album_id: str,
        folder: str,
        *,
        result: SyncResult | None = None,
    ) -> SyncResult:
        """Bind a folder to an Immich album (keep-in-sync) and reconcile it once now."""
        self._store.add_subscription(frame_id, immich_album_id, folder)
        result = await self.reconcile(frame_id, immich_album_id, folder, result=result)
        await self._delivery.drain(
            now=datetime.now(UTC)
        )  # deliver the new photos now (interactive)
        return result

    async def add_album_once(
        self,
        frame_id: str,
        immich_album_id: str,
        folder: str,
        *,
        result: SyncResult | None = None,
    ) -> SyncResult:
        """Add a whole Immich album's images into a Library folder ONCE — no binding (#62).

        Unlike ``bind``, this creates no ``album_sync`` row: it merges the album's current images
        into the folder as ordinary curated (``source='immich'``) rows and delivers them now. A
        later change to the album does nothing — the folder is a snapshot, not a mirror.
        """
        result = result if result is not None else SyncResult()
        async with self._immich_factory() as client:
            assets = [a for a in await client.album_assets(immich_album_id) if a.type == "IMAGE"]
        for a in assets:
            dest = dest_name_for(a.file_name, a.id)
            # MERGE (INSERT OR REPLACE) — never wipe this or other folders' existing items.
            self._library.add(frame_id, LibraryItem(a.id, dest, source="immich", folder=folder))
        result.total = len(assets)
        result.prepared = len(assets)
        now = datetime.now(UTC)
        result.uploaded = self._delivery.enqueue_desired(frame_id, now=now)
        result.skipped = result.total - result.uploaded
        await self._delivery.drain(now=now)  # deliver the new photos now (interactive)
        return result

    async def reconcile(
        self,
        frame_id: str,
        immich_album_id: str,
        folder: str,
        *,
        result: SyncResult | None = None,
        now: datetime | None = None,
        rng: random.Random | None = None,
        advance: bool = False,
    ) -> SyncResult:
        """Set the bound folder's sync rows to the album's current images and queue the delta.

        When the target frame is a SwitchBot AI Art Frame with ``switchbot_slideshow`` on, reconcile
        draws a non-repeating random batch (1 photo for 'slyde', 10 for 'frame') from the Immich
        album using the durable Shuffle-Deck in ``Store`` whenever
        ``switchbot_slideshow_interval_minutes`` has elapsed.
        """
        result = result if result is not None else SyncResult()
        now = now or datetime.now(UTC)
        async with self._immich_factory() as client:
            assets = [a for a in await client.album_assets(immich_album_id) if a.type == "IMAGE"]

        frame = self._store.get_frame(frame_id)
        max_slots = self._settings.shuffle_batch_size
        prior_items = [
            i for i in self._library.desired(frame_id) if i.folder == folder and i.source == "sync"
        ]
        if frame is not None and frame.backend == SwitchBotBackend.name and max_slots > 0:
            items = self._shuffle_items(
                frame_id, folder, assets, prior_items, max_slots, now=now, rng=rng, advance=advance
            )
        else:
            items = [
                LibraryItem(a.id, dest_name_for(a.file_name, a.id), source="sync", folder=folder)
                for a in assets
            ]

        result.total = len(items)
        result.prepared = len(items)

        prior = {i.dest_name for i in prior_items}
        # Also drops the delivery row of every photo that left the set, so one that returns in a
        # later Shuffle-Deck epoch (or is re-added to the album) is delivered again.
        self._library.set_folder_sync(frame_id, folder, items)
        departed = prior - {i.dest_name for i in items}

        # The delivery queue's delta-skip (#46) already avoids re-delivering photos that are on the
        # frame, so a steady-state reconcile queues nothing — no separate "on device" bookkeeping.
        result.uploaded = self._delivery.enqueue_desired(frame_id, now=now)
        result.skipped = result.total - result.uploaded
        result.removed = len(departed)
        self._store.touch_subscription(
            frame_id,
            immich_album_id,
            f"{result.total} photos: +{result.uploaded} queued, "
            f"{result.skipped} kept, -{result.removed} removed",
        )
        return result

    def _shuffle_items(
        self,
        frame_id: str,
        folder: str,
        assets: list[ImmichAsset],
        prior_items: list[LibraryItem],
        max_slots: int,
        *,
        now: datetime,
        rng: random.Random | None,
        advance: bool = False,
    ) -> list[LibraryItem]:
        """The Shuffle-Deck batch for a slot-limited frame: a fresh draw once the rotation interval
        has elapsed (outside quiet hours) or when ``advance`` asks for the next one now, otherwise
        the held batch — topped up if some of its photos left the album."""
        by_id = {a.id: a for a in assets}
        prior_ids = [i.asset_id for i in prior_items]
        due = not in_quiet_hours(
            self._settings.switchbot_quiet_hours, now
        ) and self._store.should_rotate_shuffle_batch(
            frame_id,
            folder,
            now=now,
            interval_minutes=self._settings.switchbot_slideshow_interval_minutes,
        )
        if not prior_items or advance or due:
            # A photo of the outgoing batch that never reached the frame wasn't really shown: put
            # it back in the deck instead of counting it as seen for this epoch.
            delivered = self._store.delivered_payloads(frame_id)
            self._store.forget_shuffle_draws(
                frame_id,
                folder,
                [i.asset_id for i in prior_items if delivered.get(i.dest_name) != i.asset_id],
            )
            chosen = self._store.draw_shuffle_batch(
                frame_id,
                folder,
                list(by_id),
                max_slots,
                rng=rng,
                now_iso=now.isoformat(),
                avoid=prior_ids,
            )
        else:
            chosen = [aid for aid in prior_ids if aid in by_id]
            if len(chosen) < max_slots:
                held = set(chosen)
                chosen += self._store.draw_shuffle_batch(
                    frame_id,
                    folder,
                    [aid for aid in by_id if aid not in held],
                    max_slots - len(chosen),
                    rng=rng,
                    now_iso=now.isoformat(),
                    touch_rotation=False,
                )
        return [
            LibraryItem(aid, dest_name_for(by_id[aid].file_name, aid), source="sync", folder=folder)
            for aid in chosen
            if aid in by_id
        ]

    async def advance(self, frame_id: str) -> int:
        """Draw the next Shuffle-Deck photo/batch for every folder bound on ``frame_id`` right now
        (the UI's "next photo"), ignoring the interval and quiet hours. Returns photos queued."""
        queued = 0
        for sub in self._store.list_subscriptions(frame_id):
            r = await self.reconcile(sub.host, sub.immich_album_id, sub.target_album, advance=True)
            queued += r.uploaded
        return queued

    async def run_due(self) -> dict[str, int]:
        """Reconcile every bound folder (the scheduler's periodic cycle)."""
        summary = {"subscriptions": 0, "added": 0, "removed": 0, "failed": 0}
        for sub in self._store.list_subscriptions():
            summary["subscriptions"] += 1
            try:
                r = await self.reconcile(sub.host, sub.immich_album_id, sub.target_album)
                summary["added"] += r.uploaded
                summary["removed"] += r.removed
            except Exception:
                summary["failed"] += 1
                _log.exception("folder sync failed: %s -> %s", sub.host, sub.target_album)
        return summary


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
