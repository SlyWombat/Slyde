"""Image pipeline: prepare an arbitrary photo for the frame's fixed canvas."""

from __future__ import annotations

import io

from PIL import Image, ImageOps


def prepare_for_frame(
    data: bytes,
    canvas: tuple[int, int],
    *,
    fill: tuple[int, int, int] = (0, 0, 0),
    quality: int = 90,
) -> bytes:
    """Return JPEG bytes sized exactly to ``canvas``.

    The image is EXIF-oriented, scaled to fit while preserving aspect ratio, then letterboxed
    (centered on a ``fill`` background) so the output is exactly ``canvas`` pixels — the frame
    expects images at its native resolution.
    """
    width, height = canvas
    with Image.open(io.BytesIO(data)) as src:
        oriented = ImageOps.exif_transpose(src) or src
        rgb = oriented.convert("RGB")
        fitted = ImageOps.contain(rgb, (width, height), method=Image.Resampling.LANCZOS)
        canvas_img = Image.new("RGB", (width, height), fill)
        offset = ((width - fitted.width) // 2, (height - fitted.height) // 2)
        canvas_img.paste(fitted, offset)
        out = io.BytesIO()
        canvas_img.save(out, format="JPEG", quality=quality)
        return out.getvalue()
