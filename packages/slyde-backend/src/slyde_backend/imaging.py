"""Image pipeline: prepare an arbitrary photo for the frame's fixed canvas.

The frame expects images at its exact native resolution. How a source whose aspect ratio differs
from the canvas is fitted is configurable (``FRAME_FIT``):

- ``contain`` — scale to fit, letterbox the remainder with a solid ``fill`` colour.
- ``cover``   — scale to fill and centre-crop the overflow (no bars, loses edges).
- ``blur``    — scale to fit, but fill the bars with a blurred, darkened copy of the image.
- ``smart``   — crop (``cover``) when little is lost, otherwise blur-fill. Mirrors the original
  Memento product: near-aspect photos are cropped to fill, portraits get blurred sides.
"""

from __future__ import annotations

import io
from enum import StrEnum

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

_RESAMPLE = Image.Resampling.LANCZOS


class FitMode(StrEnum):
    CONTAIN = "contain"
    COVER = "cover"
    BLUR = "blur"
    SMART = "smart"


def _letterbox(rgb: Image.Image, size: tuple[int, int], fill: tuple[int, int, int]) -> Image.Image:
    fitted = ImageOps.contain(rgb, size, method=_RESAMPLE)
    canvas_img = Image.new("RGB", size, fill)
    canvas_img.paste(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
    return canvas_img


def _cover(rgb: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(rgb, size, method=_RESAMPLE, centering=(0.5, 0.5))


def _blur_fill(rgb: Image.Image, size: tuple[int, int]) -> Image.Image:
    background = _cover(rgb, size).filter(ImageFilter.GaussianBlur(max(8, min(size) // 24)))
    background = ImageEnhance.Brightness(background).enhance(0.6)  # darken so the photo pops
    fitted = ImageOps.contain(rgb, size, method=_RESAMPLE)
    background.paste(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
    return background


def _cover_loss(src: tuple[int, int], canvas: tuple[int, int]) -> float:
    """Fraction of the long edge a centre-crop-to-fill would discard (0.0 = perfect fit)."""
    sw, sh = src
    cw, ch = canvas
    if not sh or not ch:
        return 1.0
    src_ar, canvas_ar = sw / sh, cw / ch
    return 1.0 - min(src_ar, canvas_ar) / max(src_ar, canvas_ar)


def fit_to_canvas(
    data: bytes,
    canvas: tuple[int, int],
    *,
    fit: FitMode | str = FitMode.SMART,
    crop_tolerance: float = 0.12,
    fill: tuple[int, int, int] = (0, 0, 0),
) -> Image.Image:
    """Return an RGB ``Image`` sized exactly to ``canvas``, fitted per ``fit`` (see module doc)."""
    mode = FitMode(fit)
    with Image.open(io.BytesIO(data)) as src:
        rgb = (ImageOps.exif_transpose(src) or src).convert("RGB")
    if mode is FitMode.SMART:
        small_loss = _cover_loss(rgb.size, canvas) <= crop_tolerance
        mode = FitMode.COVER if small_loss else FitMode.BLUR
    if mode is FitMode.COVER:
        return _cover(rgb, canvas)
    if mode is FitMode.BLUR:
        return _blur_fill(rgb, canvas)
    return _letterbox(rgb, canvas, fill)


def prepare_for_frame(
    data: bytes,
    canvas: tuple[int, int],
    *,
    fit: FitMode | str = FitMode.SMART,
    crop_tolerance: float = 0.12,
    fill: tuple[int, int, int] = (0, 0, 0),
    quality: int = 90,
) -> bytes:
    """Return JPEG bytes sized exactly to ``canvas``, fitted per ``fit`` (see module docstring)."""
    result = fit_to_canvas(data, canvas, fit=fit, crop_tolerance=crop_tolerance, fill=fill)
    out = io.BytesIO()
    result.save(out, format="JPEG", quality=quality)
    return out.getvalue()
