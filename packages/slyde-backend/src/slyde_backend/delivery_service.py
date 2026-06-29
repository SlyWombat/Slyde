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

import httpx

from .backends.switchbot import SwitchBotBackend
from .config import Settings
from .delivery import RetryPolicy, TransientDeliveryError, reconcile
from .frames import FrameService, FrameUnavailable
from .imagecache import ImageCache
from .immich import ImmichClient, ImmichError
from .library import FrameLibrary
from .processing import prepare, profile_for
from .store import DeliveryRow, Store
from .switchbot import SwitchBotClient, SwitchBotError
from .switchbot_service import switchbot_client_factory


class DeliveryService:
    def __init__(
        self,
        store: Store,
        library: FrameLibrary,
        cache: ImageCache,
        frame_service: FrameService,
        immich_factory: Callable[[], ImmichClient],
        settings: Settings,
        uploads: ImageCache | None = None,
        switchbot_factory: Callable[[], SwitchBotClient] | None = None,
    ) -> None:
        self._store = store
        self._library = library
        self._cache = cache
        self._frames = frame_service
        self._immich_factory = immich_factory
        self._settings = settings
        # Originals of app-pushed photos (not in Immich); sourced from here instead of Immich.
        self._uploads = uploads
        # How to reach SwitchBot's cloud for a 'switchbot' frame's push delivery (injectable for
        # tests); defaults to a client built from the configured token/secret.
        self._switchbot_factory = switchbot_factory or switchbot_client_factory(settings)
        self._policy = RetryPolicy()
        self._lock = asyncio.Lock()  # serialise reconcile passes (PUT, scheduler, delivery timer)

    def enqueue_desired(self, frame_id: str, *, now: datetime) -> int:
        """Queue a delivery for each curated photo that isn't already on the frame; returns how many
        were queued. Photos already ``delivered`` with the same asset are skipped, so re-saving a
        library (or adding one photo to a large album) delivers only the delta — not the whole set
        (#46)."""
        delivered = self._store.delivered_payloads(frame_id)
        queued = 0
        for item in self._library.desired(frame_id):
            if delivered.get(item.dest_name) == item.asset_id:
                continue  # already on the frame, unchanged — don't re-queue or re-deliver
            self._store.enqueue_delivery(
                frame_id, item.dest_name, item.asset_id, next_attempt_at=now.isoformat()
            )
            queued += 1
        return queued

    async def reconcile(self, *, now: datetime, limit: int = 100) -> dict[str, int]:
        """Drain one batch of the due delivery queue (bounded by ``limit``)."""
        async with self._lock:
            return await reconcile(
                self._store, self._deliver, now=now, policy=self._policy, limit=limit
            )

    async def drain(self, *, now: datetime, max_batches: int = 200) -> dict[str, int]:
        """Reconcile repeatedly until the due queue is empty (or a safety cap), so a fresh large
        curation first-syncs in one background pass instead of one 100-item batch per cycle (#47).

        Retried (offline) items are rescheduled into the future, so they fall out of ``now``'s due
        set and the loop terminates; they're picked up on a later tick.
        """
        totals = {"delivered": 0, "retried": 0, "failed": 0}
        for _ in range(max_batches):
            counts = await self.reconcile(now=now)
            for key in totals:
                totals[key] += counts[key]
            if counts["delivered"] + counts["retried"] + counts["failed"] == 0:
                break  # nothing left due
        return totals

    async def _deliver(self, item: DeliveryRow) -> None:
        frame = self._store.get_frame(item.frame_id)
        if frame is None:
            raise TransientDeliveryError(f"frame {item.frame_id} not yet registered")
        # Reuse the already-prepared image if cached — no re-fetch/re-process on retry (#25).
        prepared = self._cache.get(frame.id, item.key)
        if prepared is None:
            profile = profile_for(frame, self._settings, canvas=self._settings.canvas)
            # App-uploaded photos aren't in Immich — source their original locally if we have it.
            source = self._uploads.get(frame.id, item.key) if self._uploads else None
            if source is None:
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
        if frame.backend == SwitchBotBackend.name:
            # SwitchBot: push the prepared JPEG to the vendor cloud by deviceId (frame.id). The
            # cloud renders it onto the panel on the frame's next wake (~30s) — we don't block on
            # that confirmation; a transport/API hiccup is transient, so it's retried like any push.
            await self._push_switchbot(frame.id, prepared)
            return
        try:  # connected: push over the LAN — an offline frame is a transient (retried) failure.
            # Pass the frame's stable id (not a fixed address) so delivery resolves its CURRENT IP
            # and self-heals across DHCP changes (#58).
            await self._frames.upload_images(frame.id, [(prepared, item.key)], album=None)
        except FrameUnavailable as exc:
            raise TransientDeliveryError(f"frame offline: {exc}") from exc

    async def _push_switchbot(self, device_id: str, image: bytes) -> None:
        """Deliver ``image`` to a SwitchBot AI Art Frame via its signed cloud API (uploadImage).

        A transport/API failure (cloud down, rate-limited, frame unreachable) is transient — raised
        as ``TransientDeliveryError`` so the queue retries with backoff and never drops the photo.
        """
        try:
            async with self._switchbot_factory() as client:
                await client.upload_image_bytes(device_id, image)
        except (SwitchBotError, httpx.HTTPError, OSError) as exc:
            raise TransientDeliveryError(f"switchbot push failed: {exc}") from exc
