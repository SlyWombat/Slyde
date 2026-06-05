"""Live delivery wiring: curation -> queue -> prepare -> deliver (issues #25/#26).

Ties the framework into an autonomous backend service:

- ``enqueue_desired(frame_id)`` turns a frame's curated set (``FrameLibrary``) into durable delivery
  queue entries (one per photo).
- ``reconcile(now)`` drains the due queue via the guaranteed-delivery engine. For each item it reads
  the photo from Immich (read-only), prepares it for the frame's panel (``ProcessingProfile``), and:
  - **served** frame  -> writes the prepared image into the ``ImageCache`` (the frame pulls it on
    wake; inherently offline-tolerant).
  - **connected** frame -> pushes it to the frame over the LAN; a ``FrameUnavailable`` is transient,
    so an offline frame is retried with backoff and never abandoned.

The scheduler calls ``reconcile`` every cycle, so sync runs entirely in the backend (the UI never
drives it — issue #25).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime

from .config import Settings
from .delivery import RetryPolicy, TransientDeliveryError, reconcile
from .frames import FrameService, FrameUnavailable
from .imagecache import ImageCache
from .immich import ImmichClient, ImmichError
from .library import FrameLibrary
from .processing import prepare, profile_for
from .store import DeliveryRow, Store


class DeliveryService:
    def __init__(
        self,
        store: Store,
        library: FrameLibrary,
        cache: ImageCache,
        frame_service: FrameService,
        immich_factory: Callable[[], ImmichClient],
        settings: Settings,
    ) -> None:
        self._store = store
        self._library = library
        self._cache = cache
        self._frames = frame_service
        self._immich_factory = immich_factory
        self._settings = settings
        self._policy = RetryPolicy()

    def enqueue_desired(self, frame_id: str, *, now: datetime) -> int:
        """Queue a delivery for every curated photo of ``frame_id``; returns the count."""
        items = self._library.desired(frame_id)
        for item in items:
            self._store.enqueue_delivery(
                frame_id, item.dest_name, item.asset_id, next_attempt_at=now.isoformat()
            )
        return len(items)

    async def reconcile(self, *, now: datetime) -> dict[str, int]:
        """Drain the due delivery queue once (called by the scheduler each cycle)."""
        return await reconcile(self._store, self._deliver, now=now, policy=self._policy)

    async def _deliver(self, item: DeliveryRow) -> None:
        frame = self._store.get_frame(item.frame_id)
        if frame is None:
            raise TransientDeliveryError(f"frame {item.frame_id} not yet registered")
        # Reuse the already-prepared image if cached — no re-fetch/re-process on retry (#25).
        prepared = self._cache.get(frame.id, item.key)
        if prepared is None:
            profile = profile_for(frame, self._settings, canvas=self._settings.canvas)
            try:
                async with self._immich_factory() as client:
                    source = await client.asset_bytes(
                        item.payload, self._settings.immich_asset_size
                    )
            except (ImmichError, OSError) as exc:  # Immich down/unreachable -> retry later
                raise TransientDeliveryError(f"immich fetch failed: {exc}") from exc
            prepared = await asyncio.to_thread(prepare, source, profile)
            self._cache.put(
                frame.id, item.key, prepared
            )  # cache for reuse (served serves from here)
        if frame.interaction == "served":
            return  # the prepared image waits in the cache; the frame pulls it on wake
        try:  # connected: push over the LAN — an offline frame is a transient (retried) failure
            await self._frames.upload_images(frame.address, [(prepared, item.key)], album=None)
        except FrameUnavailable as exc:
            raise TransientDeliveryError(f"frame offline: {exc}") from exc
