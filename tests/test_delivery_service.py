"""Live delivery wiring: curation -> queue -> prepare -> deliver (issues #25/#26)."""

from __future__ import annotations

import asyncio
import io
from datetime import datetime
from pathlib import Path

from PIL import Image

from slyde_backend.config import Settings
from slyde_backend.delivery_service import DeliveryService
from slyde_backend.frame import Frame
from slyde_backend.frames import FrameUnavailable
from slyde_backend.imagecache import ImageCache
from slyde_backend.library import FrameLibrary, LibraryItem
from slyde_backend.store import Store

T0 = datetime(2026, 1, 1, 12, 0, 0)


class FakeImmich:
    async def __aenter__(self) -> FakeImmich:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def asset_bytes(self, asset_id: str, size: str = "preview") -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (120, 80), (40, 80, 160)).save(buf, format="JPEG")
        return buf.getvalue()


class FakeFrames:
    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []
        self.offline = False

    async def upload_images(self, host, items, album, on_uploaded=None):  # type: ignore[no-untyped-def]
        if self.offline:
            raise FrameUnavailable("frame offline")
        self.pushed.extend((host, dest) for _, dest in items)
        return [dest for _, dest in items]


def _service(
    tmp_path: Path, frames: FakeFrames | None = None
) -> tuple[DeliveryService, Store, ImageCache]:
    store = Store(str(tmp_path / "d.db"))
    cache = ImageCache(str(tmp_path / "cache"))
    library = FrameLibrary(store, cache)
    settings = Settings(frame_canvas="64x48")
    ds = DeliveryService(store, library, cache, frames or FakeFrames(), FakeImmich, settings)
    return ds, store, cache


def test_served_curation_publishes_to_cache_and_marks_delivered(tmp_path: Path) -> None:
    ds, store, cache = _service(tmp_path)
    store.upsert_frame(Frame.served("EF-1", backend="sungale-cloud"))
    ds._library.set_desired("EF-1", [LibraryItem("a1", "one"), LibraryItem("a2", "two")])

    assert ds.enqueue_desired("EF-1", now=T0) == 2
    counts = asyncio.run(ds.reconcile(now=T0))

    assert counts == {"delivered": 2, "retried": 0, "failed": 0}
    assert cache.keys("EF-1") == ["one", "two"]  # prepared images cached, ready to pull
    with Image.open(io.BytesIO(cache.get("EF-1", "one"))) as img:
        assert img.format == "PNG" and img.size == (64, 48)  # e-paper PNG at the frame canvas
    assert {d.state for d in store.list_deliveries("EF-1")} == {"delivered"}


def test_connected_offline_frame_is_retried_then_delivered(tmp_path: Path) -> None:
    frames = FakeFrames()
    frames.offline = True
    ds, store, _ = _service(tmp_path, frames)
    store.upsert_frame(Frame.connected("10.0.0.5", backend="memento-lan"))
    ds._library.set_desired("10.0.0.5", [LibraryItem("a1", "one.jpg")])
    ds.enqueue_desired("10.0.0.5", now=T0)

    # offline -> transient, rescheduled, never failed
    assert asyncio.run(ds.reconcile(now=T0)) == {"delivered": 0, "retried": 1, "failed": 0}
    assert store.list_deliveries("10.0.0.5")[0].state == "pending"

    # frame comes back -> the queued delivery pushes and completes
    frames.offline = False
    due = datetime.fromisoformat(store.list_deliveries("10.0.0.5")[0].next_attempt_at)
    assert asyncio.run(ds.reconcile(now=due))["delivered"] == 1
    assert frames.pushed == [("10.0.0.5", "one.jpg")]
    assert store.list_deliveries("10.0.0.5")[0].state == "delivered"
