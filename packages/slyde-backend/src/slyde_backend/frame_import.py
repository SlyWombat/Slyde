"""Pull the photos already on a connected frame INTO Slyde's library (#frame-import).

A frame managed before Slyde existed already holds photos Slyde knows nothing about. This reads the
frame's album manifest, downloads each original off the device, and ingests it as a Slyde-owned
library member (canonical preview + prepared image) — exactly like an upload, but recorded as
already **delivered** (it's on the frame; we never re-send it).

Deliberately gentle: every control op to one frame is serialized + paced by ``FrameService``, and we
add an extra pause between images, so pulling a whole library never overloads a low-power frame
(some stop answering the control protocol under concurrent/rapid requests).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from .config import Settings
from .frame import Frame
from .frames import FrameService
from .imagecache import ImageCache
from .library import FrameLibrary, LibraryItem
from .previews import AssetPreviewCache, render_canonical_preview
from .processing import prepare, profile_for
from .schemas import SyncResult
from .store import Store

_log = logging.getLogger(__name__)


async def import_frame_photos(
    *,
    frame: Frame,
    frame_service: FrameService,
    settings: Settings,
    image_cache: ImageCache,
    asset_previews: AssetPreviewCache,
    uploads: ImageCache,
    library: FrameLibrary,
    store: Store,
    result: SyncResult | None = None,
) -> SyncResult:
    """Import every photo already on ``frame`` into the library; populates ``result`` for progress.

    Idempotent — photos already in the library are skipped, so a re-run only picks up new ones.
    """
    result = result or SyncResult()
    album_data = await frame_service.get_album_data(frame.id)

    # Unique image filenames across ALL albums, in a stable order. The reserved "Photos" album is
    # the device's full set (every photo lands there), so we include reserved albums — skipping them
    # would miss most/all photos. Deduping by filename folds the same file appearing in many albums.
    names: list[str] = []
    seen: set[str] = set()
    for album in album_data.albums:
        for img in album.images:
            if img not in seen:
                seen.add(img)
                names.append(img)
    result.total = len(names)

    already = {dest for _aid, dest, _src in store.list_library(frame.id)}
    profile = profile_for(frame, settings, canvas=settings.canvas)
    for name in names:
        if name in already:
            result.skipped += 1
            continue
        try:
            data = await frame_service.download_image(frame.id, name)
            if not data:
                raise ValueError("empty image off the frame")
            # Slyde owns the photo now: keep the original, the panel-prepared image, and a preview.
            uploads.put(frame.id, name, data)
            prepared = await asyncio.to_thread(prepare, data, profile)
            image_cache.put(frame.id, name, prepared)
            preview = await asyncio.to_thread(render_canonical_preview, data)
            asset_previews.put(name, preview)
            library.add(frame.id, LibraryItem(asset_id=name, dest_name=name, source="frame"))
            # Record it as delivered (it's already on the frame) — enqueue then mark, with no await
            # between, so the delivery loop can't observe the transient 'pending' and re-send it.
            did = store.enqueue_delivery(
                frame.id, name, name, next_attempt_at=datetime.now(UTC).isoformat()
            )
            store.mark_delivered(did)
            result.prepared += 1
            result.uploaded += 1
        except Exception as exc:  # one bad/slow/undecodable image must not abort the whole pull
            result.failed += 1
            _log.warning("frame import: skipping %s on %s: %s", name, frame.id, exc)
        if settings.frame_import_delay:
            await asyncio.sleep(settings.frame_import_delay)  # stay gentle between images
    return result
