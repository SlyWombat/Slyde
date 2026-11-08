"""Per-frame image-processing profile (issue #19).

Image preparation is a property *of the frame*, not a global setting (ADR-009). A
``ProcessingProfile`` describes how to prepare a photo for one frame: its canvas, fit mode, colour
model, and output encoding. ``prepare(data, profile)`` runs it.

- **full-colour LCD** (the Memento frame): fit to the canvas, encode JPEG — identical to the old
  global ``prepare_for_frame`` path, so there is no regression.
- **e-paper** (the Sungale/Aluratek Spectra-6 panel): fit, then **map to the panel's limited colour
  palette with Floyd-Steinberg dithering**, encode PNG (lossless, palette-ok).

The profile is chosen per frame from its backend's declared colour model
(``FrameCapabilities.color_model``); ``profile_for`` assembles it.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from PIL import Image

from .config import Settings
from .frame import Frame
from .imaging import fit_to_canvas

# The Spectra-6 palette + the byte-exact panel encoder live in panel_bmp (the codec authority).
# Re-exported here so the generic epaper-PNG path and existing imports keep working.
from .panel_bmp import PANEL_DISPLAY_SIZE, SPECTRA6_PALETTE, encode_spectra6_bmp

_log = logging.getLogger(__name__)

__all__ = [
    "PANEL_DISPLAY_SIZE",
    "SPECTRA6_PALETTE",
    "ProcessingProfile",
    "prepare",
    "profile_for",
]


@dataclass(frozen=True)
class ProcessingProfile:
    canvas: tuple[int, int]
    fit: str = "smart"
    crop_tolerance: float = 0.12
    color_model: str = "full"  # "full" (LCD/JPEG) | "epaper" (palette + dither)
    palette: tuple[tuple[int, int, int], ...] = ()
    dither: bool = True
    quality: int = 90
    encoder: str = "auto"  # "auto" (JPEG/PNG by colour model) | "spectra6-bmp" (panel BMP, #11)
    max_bytes: int = 0  # when >0, step down JPEG quality until output fits under max_bytes


def _palette_image(palette: tuple[tuple[int, int, int], ...]) -> Image.Image:
    flat: list[int] = []
    for r, g, b in palette:
        flat.extend((r, g, b))
    flat.extend(flat[:3] * (256 - len(palette)))  # pad to 256 colours (Pillow requirement)
    pal = Image.new("P", (1, 1))
    pal.putpalette(flat)
    return pal


def prepare(data: bytes, profile: ProcessingProfile) -> bytes:
    """Prepare ``data`` per ``profile``; returns ready-to-serve bytes (JPEG or palette PNG).

    With ``profile.max_bytes`` set, a JPEG over the cap is re-encoded at falling quality (5 points
    per step, down to 20) until it fits; if even quality 20 doesn't, that smallest one is returned
    and a warning logged (the upload will likely be refused).
    """
    fitted = fit_to_canvas(
        data, profile.canvas, fit=profile.fit, crop_tolerance=profile.crop_tolerance
    )
    if profile.encoder == "spectra6-bmp":
        # The exact byte format the Sungale/Aluratek frame downloads (#11): 4bpp indexed BMP,
        # quantised + dithered to the panel palette and packed to its 600x3200 buffer.
        return encode_spectra6_bmp(fitted)
    if profile.color_model == "epaper":
        palette = profile.palette or SPECTRA6_PALETTE
        dither = Image.Dither.FLOYDSTEINBERG if profile.dither else Image.Dither.NONE
        quantized = fitted.quantize(palette=_palette_image(palette), dither=dither)
        out = io.BytesIO()
        quantized.convert("RGB").save(out, format="PNG")
        return out.getvalue()
    out = io.BytesIO()
    fitted.save(out, format="JPEG", quality=profile.quality)
    result = out.getvalue()
    if profile.max_bytes > 0 and len(result) > profile.max_bytes:
        for q in range(profile.quality - 5, 19, -5):
            out = io.BytesIO()
            fitted.save(out, format="JPEG", quality=q, optimize=True)
            result = out.getvalue()
            if len(result) <= profile.max_bytes:
                break
        else:
            _log.warning(
                "JPEG is %d bytes even at quality 20 (cap %d)", len(result), profile.max_bytes
            )
    return result


def profile_for(frame: Frame, settings: Settings, *, canvas: tuple[int, int]) -> ProcessingProfile:
    """Build the processing profile for ``frame`` from its backend's declared colour model."""
    from .backends import get_backend
    from .backends.switchbot import SwitchBotBackend, switchbot_canvas

    if frame.backend == SwitchBotBackend.name:
        # SwitchBot's cloud renders our upload onto the Spectra-6 panel itself (and center-crops to
        # fill), so we DON'T quantize/dither here: we smart-fit to the configured panel resolution
        # (e.g. 1200x1600 portrait on the 13.3" frame, or 480x800 on the 7.3") and emit a JPEG
        # capped below SwitchBot's ~650 KB base64 upload limit (#64).
        return ProcessingProfile(
            canvas=switchbot_canvas(settings.switchbot_frame_size, settings.switchbot_orientation),
            fit=settings.frame_fit,
            crop_tolerance=settings.frame_crop_tolerance,
            color_model="full",
            max_bytes=settings.switchbot_max_image_bytes,
        )
    caps = get_backend(frame.backend).capabilities
    if getattr(caps, "color_model", "full") == "epaper":
        # The e-paper panel has a fixed native resolution and a byte-exact download format, so it
        # composes at PANEL_DISPLAY_SIZE (not the global canvas) and emits the panel BMP (#11).
        return ProcessingProfile(
            canvas=PANEL_DISPLAY_SIZE,
            fit=settings.frame_fit,
            crop_tolerance=settings.frame_crop_tolerance,
            color_model="epaper",
            palette=SPECTRA6_PALETTE,
            dither=True,
            encoder="spectra6-bmp",
        )
    return ProcessingProfile(
        canvas=canvas,
        fit=settings.frame_fit,
        crop_tolerance=settings.frame_crop_tolerance,
        color_model="full",
    )
