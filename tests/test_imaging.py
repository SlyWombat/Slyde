"""Image pipeline: output is always exactly the frame canvas; fit mode controls bars vs crop."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from slyde_backend.imaging import prepare_for_frame

RED = (200, 50, 50)


def _png(width: int, height: int, color: tuple[int, int, int] = RED) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _out(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


def _corner(data: bytes) -> tuple[int, int, int]:
    return _out(data).getpixel((0, 0))  # type: ignore[return-value]


def test_output_is_always_exact_canvas_jpeg() -> None:
    out = prepare_for_frame(_png(1000, 200), (640, 480), fit="contain")
    img = _out(out)
    assert img.size == (640, 480)
    assert Image.open(io.BytesIO(out)).format == "JPEG"


def test_contain_letterboxes_with_solid_bars() -> None:
    # A very wide image leaves black bars top/bottom; the corner is the fill colour.
    out = prepare_for_frame(_png(1000, 200), (640, 480), fit="contain")
    r, g, b = _corner(out)
    assert r < 20 and g < 20 and b < 20  # black bar


def test_cover_crops_to_fill_no_bars() -> None:
    out = prepare_for_frame(_png(1000, 200), (640, 480), fit="cover")
    r, _g, _b = _corner(out)
    assert r > 100  # filled with image, not a black bar


def test_blur_fills_bars_with_blurred_image() -> None:
    # A tall portrait on a landscape canvas: the side bars carry blurred, darkened image content.
    out = prepare_for_frame(_png(200, 1000), (640, 480), fit="blur")
    r, _g, _b = _corner(out)
    assert r > 20  # not a black bar — blurred content present


def test_smart_crops_near_aspect() -> None:
    # 700x480 (AR 1.46) vs 640x480 (AR 1.33): only ~9% lost → crop to fill, no bars.
    out = prepare_for_frame(_png(700, 480), (640, 480))  # default fit=smart
    r, _g, _b = _corner(out)
    assert r > 100


def test_smart_blur_fills_strong_mismatch() -> None:
    # A portrait is far off-aspect → blur-fill rather than crop most of it away.
    out = prepare_for_frame(_png(200, 1000), (640, 480))  # default fit=smart
    assert _out(out).size == (640, 480)
    r, _g, _b = _corner(out)
    assert r > 20  # blurred sides, not black bars


def test_exact_match_passes_through_size() -> None:
    out = prepare_for_frame(_png(640, 480), (640, 480))
    assert _out(out).size == (640, 480)


def test_invalid_fit_mode_rejected() -> None:
    with pytest.raises(ValueError, match="bogus"):
        prepare_for_frame(_png(640, 480), (640, 480), fit="bogus")
