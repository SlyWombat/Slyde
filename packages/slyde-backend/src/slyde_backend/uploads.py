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

from .config import Settings
from .frame import Frame
from .imagecache import ImageCache
from .previews import AssetPreviewCache, render_canonical_preview
from .processing import prepare, profile_for


async def ingest_upload(
    *,
    frame: Frame,
    data: bytes,
    settings: Settings,
    image_cache: ImageCache,
    asset_previews: AssetPreviewCache,
) -> str:
    """Prepare a pushed photo for ``frame`` and keep Slyde's own copies; returns the new photo id.

    Heavy image work (panel encode + preview) runs off the event loop. The id mirrors the vendor's
    long numeric ids so it slots into the same list/download flow the frame already uses.
    """
    photo_id = str(time.time_ns())
    profile = profile_for(frame, settings, canvas=settings.canvas)
    prepared = await asyncio.to_thread(prepare, data, profile)
    image_cache.put(frame.id, photo_id, prepared)  # what the frame downloads (e.g. the panel BMP)
    preview = await asyncio.to_thread(render_canonical_preview, data)
    asset_previews.put(photo_id, preview)  # Slyde's canonical, frame-independent preview
    return photo_id
