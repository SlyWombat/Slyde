"""Image pipeline: output is always exactly the frame canvas, as JPEG."""

from __future__ import annotations

import io

from PIL import Image

from memento_backend.imaging import prepare_for_frame


def _png(width: int, height: int, color: tuple[int, int, int] = (200, 50, 50)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def test_letterboxes_wide_image_to_canvas() -> None:
    out = prepare_for_frame(_png(1000, 200), (640, 480))
    with Image.open(io.BytesIO(out)) as img:
        assert img.size == (640, 480)
        assert img.format == "JPEG"


def test_letterboxes_tall_image_to_canvas() -> None:
    out = prepare_for_frame(_png(200, 1000), (640, 480))
    with Image.open(io.BytesIO(out)) as img:
        assert img.size == (640, 480)


def test_exact_match_passes_through_size() -> None:
    out = prepare_for_frame(_png(640, 480), (640, 480))
    with Image.open(io.BytesIO(out)) as img:
        assert img.size == (640, 480)
