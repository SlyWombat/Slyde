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
from dataclasses import dataclass

from PIL import Image

from .config import Settings
from .frame import Frame
from .imaging import fit_to_canvas

# The Spectra-6 palette + the byte-exact panel encoder live in panel_bmp (the codec authority).
# Re-exported here so the generic epaper-PNG path and existing imports keep working.
from .panel_bmp import PANEL_DISPLAY_SIZE, SPECTRA6_PALETTE, encode_spectra6_bmp

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


def _palette_image(palette: tuple[tuple[int, int, int], ...]) -> Image.Image:
    flat: list[int] = []
    for r, g, b in palette:
        flat.extend((r, g, b))
    flat.extend(flat[:3] * (256 - len(palette)))  # pad to 256 colours (Pillow requirement)
    pal = Image.new("P", (1, 1))
    pal.putpalette(flat)
    return pal


def prepare(data: bytes, profile: ProcessingProfile) -> bytes:
    """Prepare ``data`` per ``profile``; returns ready-to-serve bytes (JPEG or palette PNG)."""
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
    return out.getvalue()


def profile_for(frame: Frame, settings: Settings, *, canvas: tuple[int, int]) -> ProcessingProfile:
    """Build the processing profile for ``frame`` from its backend's declared colour model."""
    from .backends import get_backend

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
