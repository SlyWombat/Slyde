"""Slyde's own canonical asset previews — frame-independent render + persistent per-asset cache."""

from __future__ import annotations

import io

from PIL import Image

from slyde_backend.previews import (
    PREVIEW_MAX_EDGE,
    AssetPreviewCache,
    render_canonical_preview,
)


def _img(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (20, 140, 90)).save(buf, format="PNG")
    return buf.getvalue()


def test_render_downscales_large_image_to_the_max_edge() -> None:
    with Image.open(io.BytesIO(render_canonical_preview(_img(4000, 3000)))) as im:
        assert im.format == "JPEG"
        assert max(im.size) == PREVIEW_MAX_EDGE and im.size == (1440, 1080)


def test_render_does_not_upscale_small_images() -> None:
    with Image.open(io.BytesIO(render_canonical_preview(_img(200, 100)))) as im:
        assert im.size == (200, 100)  # contain only; never enlarged


def test_cache_round_trips_keyed_by_asset(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cache = AssetPreviewCache(f"{tmp_path}/previews")
    assert cache.get("asset-1") is None and cache.keys() == []
    cache.put("asset-1", b"\xff\xd8\xffJPEG\xff\xd9")
    assert cache.get("asset-1") == b"\xff\xd8\xffJPEG\xff\xd9"
    assert cache.keys() == ["asset-1"]
    assert cache.delete("asset-1") and cache.get("asset-1") is None
