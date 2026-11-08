"""Whether someone is home, read from a Home Assistant entity — the slideshow only changes then.

Any entity whose state says "present" works: ``zone.home`` (the number of people in it), a
``person``/``device_tracker`` (``home``), a ``group`` of people (``home``), or a ``binary_sensor`` /
``input_boolean`` (``on``). States are fetched over HA's REST API and cached for a minute, so the
slideshow tick (every few seconds) costs one request per entity per minute.

Fails open: an entity HA can't answer for (HA down, typo, ``unavailable``) never blocks the
slideshow — it just keeps changing as if no presence entity were set.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from .config import Settings

_log = logging.getLogger(__name__)

PRESENT_STATES = frozenset({"home", "on", "true"})
ABSENT_STATES = frozenset({"not_home", "away", "off", "false"})
# Entity domains offered as presence sources in the UI's picker.
PRESENCE_DOMAINS = ("zone", "person", "group", "device_tracker", "binary_sensor", "input_boolean")


def is_present(state: str) -> bool | None:
    """``True``/``False`` for a state that says home/away, ``None`` when it says neither."""
    s = state.strip().lower()
    if s in PRESENT_STATES:
        return True
    if s in ABSENT_STATES:
        return False
    try:
        return float(s) > 0  # zone.home: people in the zone
    except ValueError:
        return None


@dataclass(frozen=True)
class PresenceEntity:
    entity_id: str
    name: str
    state: str


class Presence:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        ttl_seconds: float = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._ttl = ttl_seconds
        self._clock = clock
        self._cache: dict[str, tuple[float, bool | None]] = {}

    @property
    def configured(self) -> bool:
        """Whether Slyde can reach Home Assistant at all."""
        return bool(self._settings.effective_ha_base_url and self._settings.effective_ha_token)

    def home(self, entity_id: str) -> bool | None:
        """The last known answer for ``entity_id``: ``None`` when unknown (never fetched, or HA
        couldn't say). Callers treat ``None`` as home (fail open)."""
        cached = self._cache.get(entity_id)
        return cached[1] if cached else None

    async def refresh(self, entity_ids: set[str]) -> None:
        """Fetch each entity whose cached state is older than the TTL."""
        now = self._clock()
        stale = {e for e in entity_ids if e and now - self._fetched_at(e) >= self._ttl}
        if not stale or not self.configured:
            return
        async with self._client() as client:
            for entity_id in stale:
                self._cache[entity_id] = (now, await self._fetch(client, entity_id))

    async def check(self, entity_id: str) -> bool | None:
        """Fetch ``entity_id`` now (bypassing the cache), e.g. when the user picks it."""
        if not self.configured:
            return None
        async with self._client() as client:
            answer = await self._fetch(client, entity_id)
        self._cache[entity_id] = (self._clock(), answer)
        return answer

    async def entities(self) -> list[PresenceEntity]:
        """Entities that can serve as a presence source, for the UI's picker."""
        if not self.configured:
            return []
        async with self._client() as client:
            r = await client.get("/api/states")
            r.raise_for_status()
            states = r.json()
        return sorted(
            (
                PresenceEntity(
                    s["entity_id"],
                    str(s.get("attributes", {}).get("friendly_name") or s["entity_id"]),
                    str(s.get("state", "")),
                )
                for s in states
                if str(s.get("entity_id", "")).split(".")[0] in PRESENCE_DOMAINS
            ),
            key=lambda e: (PRESENCE_DOMAINS.index(e.entity_id.split(".")[0]), e.entity_id),
        )

    def _fetched_at(self, entity_id: str) -> float:
        cached = self._cache.get(entity_id)
        return cached[0] if cached else float("-inf")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._settings.effective_ha_base_url,
            headers={"Authorization": f"Bearer {self._settings.effective_ha_token}"},
            timeout=10,
            transport=self._transport,
        )

    async def _fetch(self, client: httpx.AsyncClient, entity_id: str) -> bool | None:
        try:
            r = await client.get(f"/api/states/{entity_id}")
            r.raise_for_status()
            return is_present(str(r.json().get("state", "")))
        except (httpx.HTTPError, ValueError) as exc:
            _log.warning("presence: can't read %s from Home Assistant: %s", entity_id, exc)
            return None
