"""FrameLibrary — desired set + publish-to-cache for served frames (issue #23)."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

from PIL import Image

from slyde_backend.imagecache import ImageCache
from slyde_backend.library import FrameLibrary, LibraryItem
from slyde_backend.store import Store


class FakeImmich:
    """Minimal read-only Immich stand-in: returns a generated JPEG for any asset."""

    def __init__(self) -> None:
        self.fetched: list[str] = []

    async def asset_bytes(self, asset_id: str, size: str = "preview") -> bytes:
        self.fetched.append(asset_id)
        buf = io.BytesIO()
        # distinct size per asset so we can tell prepared outputs apart
        Image.new("RGB", (120, 80), (10, 20, 30)).save(buf, format="JPEG")
        return buf.getvalue()


def _lib(tmp_path: Path) -> tuple[FrameLibrary, ImageCache, Store]:
    store = Store(str(tmp_path / "lib.db"))
    cache = ImageCache(str(tmp_path / "cache"))
    return FrameLibrary(store, cache), cache, store


def test_desired_set_is_durable_and_ordered(tmp_path: Path) -> None:
    lib, _, store = _lib(tmp_path)
    lib.set_desired("EF-1", [LibraryItem("a1", "one.jpg"), LibraryItem("a2", "two.jpg")])
    assert lib.desired("EF-1") == [LibraryItem("a1", "one.jpg"), LibraryItem("a2", "two.jpg")]
    # a fresh library over the same store sees the persisted set (durable curation)
    lib2 = FrameLibrary(store, ImageCache(str(tmp_path / "c2")))
    assert [i.asset_id for i in lib2.desired("EF-1")] == ["a1", "a2"]
    # set_desired replaces
    lib.set_desired("EF-1", [LibraryItem("a3", "three.jpg")])
    assert [i.asset_id for i in lib.desired("EF-1")] == ["a3"]


def test_publish_prepares_and_caches_each_desired_image(tmp_path: Path) -> None:
    lib, cache, _ = _lib(tmp_path)
    lib.set_desired("EF-2", [LibraryItem("a1", "one.jpg"), LibraryItem("a2", "two.jpg")])
    immich = FakeImmich()

    published = asyncio.run(lib.publish("EF-2", immich, canvas=(64, 48)))

    assert published == ["one.jpg", "two.jpg"]
    assert immich.fetched == ["a1", "a2"]  # read from Immich
    assert cache.keys("EF-2") == ["one.jpg", "two.jpg"]  # prepared images cached, ready to serve
    prepared = cache.get("EF-2", "one.jpg")
    assert prepared is not None and prepared[:2] == b"\xff\xd8"  # a JPEG
    # prepared to the exact frame canvas
    with Image.open(io.BytesIO(prepared)) as img:
        assert img.size == (64, 48)


def test_publish_skips_failed_items_and_keeps_prior_cache(tmp_path: Path) -> None:
    lib, cache, _ = _lib(tmp_path)
    cache.put("EF-3", "keep.jpg", b"\xff\xd8\xffPRIOR\xff\xd9")  # an already-cached image

    class Flaky(FakeImmich):
        async def asset_bytes(self, asset_id: str, size: str = "preview") -> bytes:
            if asset_id == "bad":
                raise RuntimeError("immich hiccup")
            return await super().asset_bytes(asset_id, size)

    lib.set_desired("EF-3", [LibraryItem("bad", "keep.jpg"), LibraryItem("ok", "new.jpg")])
    published = asyncio.run(lib.publish("EF-3", Flaky(), canvas=(32, 32)))

    assert published == ["new.jpg"]  # only the good one
    # the failed item left the prior cached image untouched (fallback: keep showing something)
    assert cache.get("EF-3", "keep.jpg") == b"\xff\xd8\xffPRIOR\xff\xd9"
