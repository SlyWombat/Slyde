"""Ingest a photo the app pushes to a served frame (the ``photo/upload`` endpoint).

App-uploaded photos are NOT in Immich — they are pushed straight to the (impersonated) cloud. Slyde
owns them like any other asset: it keeps its own canonical preview (so the UI/library work without
a frame, see ``previews.py``) and the frame-prepared image the frame pulls on its next wake.

One-way and local: nothing is sent back to the device; the photo simply becomes available to the
frame the way an Immich-curated photo would be.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

from .config import Settings
from .frame import Frame
from .imagecache import ImageCache
from .library import FrameLibrary, LibraryItem
from .previews import AssetPreviewCache, render_canonical_preview
from .processing import prepare, profile_for
from .store import Store


async def ingest_upload(
    *,
    frame: Frame,
    data: bytes,
    settings: Settings,
    image_cache: ImageCache,
    asset_previews: AssetPreviewCache,
    uploads: ImageCache,
    library: FrameLibrary,
    store: Store,
) -> str:
    """Make a pushed photo a first-class, Slyde-owned member of the frame's library; returns its id.

    The photo is not in Immich, so Slyde keeps the original (``uploads``) and a canonical preview,
    prepares the frame image into the cache, and adds it to the curation library + delivery queue so
    it travels the same path as an Immich-curated photo. Heavy work runs off the event loop. The id
    mirrors the vendor's long numeric ids so it slots into the frame's existing list/download flow.
    """
    photo_id = str(time.time_ns())
    uploads.put(frame.id, photo_id, data)  # durable original (re-preparable; survives cache wipe)
    profile = profile_for(frame, settings, canvas=settings.canvas)
    prepared = await asyncio.to_thread(prepare, data, profile)
    image_cache.put(frame.id, photo_id, prepared)  # what the frame downloads (e.g. the panel BMP)
    preview = await asyncio.to_thread(render_canonical_preview, data)
    asset_previews.put(photo_id, preview)  # Slyde's canonical, frame-independent preview
    # Join the curation library + delivery queue (delivery reuses the cached image — no Immich).
    library.add(frame.id, LibraryItem(asset_id=photo_id, dest_name=photo_id, source="upload"))
    store.enqueue_delivery(
        frame.id, photo_id, photo_id, next_attempt_at=datetime.now(UTC).isoformat()
    )
    return photo_id
