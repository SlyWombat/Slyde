"""Processed-image cache + cache-backed served delivery (issue #25)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from memento_backend.frame import Frame
from memento_backend.imagecache import ImageCache
from memento_backend.serving import CachedImageDelivery, PlaceholderDelivery


def test_image_cache_put_get_keys_clear(tmp_path: Path) -> None:
    cache = ImageCache(str(tmp_path / "cache"))
    assert cache.keys("EFRAME-1") == []
    assert cache.current("EFRAME-1") is None

    cache.put("EFRAME-1", "beach.jpg", b"PREPARED-A")
    cache.put("EFRAME-1", "alps.jpg", b"PREPARED-B")
    assert cache.keys("EFRAME-1") == ["alps.jpg", "beach.jpg"]  # sorted
    assert cache.get("EFRAME-1", "beach.jpg") == b"PREPARED-A"
    assert cache.current("EFRAME-1") == b"PREPARED-B"  # first key
    # frames are isolated
    assert cache.keys("OTHER") == []

    cache.clear("EFRAME-1")
    assert cache.keys("EFRAME-1") == []


def test_image_cache_construction_is_side_effect_free(tmp_path: Path) -> None:
    base = tmp_path / "lazycache"
    ImageCache(str(base))
    assert not base.exists()  # no dir created until something is cached


def test_cached_delivery_serves_cache_then_falls_back(tmp_path: Path) -> None:
    cache = ImageCache(str(tmp_path / "c"))
    delivery = CachedImageDelivery(cache, fallback=PlaceholderDelivery())
    frame = Frame.served("EF-9", backend="sungale-cloud")

    # Empty cache -> fallback placeholder (a real JPEG, so the frame still gets something).
    placeholder = asyncio.run(delivery.image_for(frame))
    assert placeholder is not None and placeholder[:2] == b"\xff\xd8"

    # Once a prepared image is cached, the frame is served exactly that.
    cache.put("EF-9", "current.jpg", b"\xff\xd8\xffEDITED\xff\xd9")
    served = asyncio.run(delivery.image_for(frame))
    assert served == b"\xff\xd8\xffEDITED\xff\xd9"


def test_cached_delivery_without_fallback_returns_none(tmp_path: Path) -> None:
    delivery = CachedImageDelivery(ImageCache(str(tmp_path / "c")))
    assert asyncio.run(delivery.image_for(Frame.served("X", backend="sungale-cloud"))) is None
