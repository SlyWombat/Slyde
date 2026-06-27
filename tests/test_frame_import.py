"""Pulling a connected frame's existing photos into Slyde's library (#frame-import)."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

from PIL import Image

from conftest import HOST, PORTS
from memento_emulator import EmulatedFrame
from slyde_backend.config import Settings
from slyde_backend.frame import Frame
from slyde_backend.frame_import import import_frame_photos
from slyde_backend.frames import FrameService
from slyde_backend.imagecache import ImageCache
from slyde_backend.library import FrameLibrary
from slyde_backend.previews import AssetPreviewCache
from slyde_backend.store import Store


def _jpeg(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, "JPEG")
    return buf.getvalue()


def _run_import(frame: Frame, svc: FrameService, settings: Settings, store: Store, tmp_path: Path):
    image_cache = ImageCache(str(tmp_path / "img"))
    return asyncio.run(
        import_frame_photos(
            frame=frame,
            frame_service=svc,
            settings=settings,
            image_cache=image_cache,
            asset_previews=AssetPreviewCache(str(tmp_path / "prev")),
            uploads=ImageCache(str(tmp_path / "up")),
            library=FrameLibrary(store, image_cache),
            store=store,
        )
    )


def test_import_pulls_on_frame_photos_into_library(frame: EmulatedFrame, tmp_path: Path) -> None:
    """Reads the frame's albums, downloads each original, and ingests them as Slyde-owned library
    items recorded as already-delivered (on the frame) — and is idempotent on re-run."""
    frame.state.add_photo("a.jpg", _jpeg((200, 0, 0)))
    frame.state.add_photo("b.jpg", _jpeg((0, 120, 0)))

    store = Store(str(tmp_path / "imp.db"))
    f = Frame.connected(HOST, backend="memento-lan", name="Living Room")
    store.upsert_frame(f)
    settings = Settings(
        frame_backend="memento-lan",
        frame_host=HOST,
        frame_settle_delay=0,
        frame_import_delay=0,
    )
    svc = FrameService(settings, ports=PORTS, store=store)

    result = _run_import(f, svc, settings, store, tmp_path)
    assert result.total == 2 and result.uploaded == 2 and result.failed == 0

    lib = {dest: src for _aid, dest, src in store.list_library(f.id)}
    assert lib == {"a.jpg": "frame", "b.jpg": "frame"}
    # recorded as already on the frame (delivered) so the delivery loop never re-sends them
    assert set(store.delivered_payloads(f.id)) == {"a.jpg", "b.jpg"}
    # a Slyde-owned preview exists for each (so the library renders without the frame)
    assert AssetPreviewCache(str(tmp_path / "prev")).get("a.jpg") is not None

    # re-running picks up nothing new — every photo is skipped
    again = _run_import(f, svc, settings, store, tmp_path)
    assert again.total == 2 and again.skipped == 2 and again.uploaded == 0
