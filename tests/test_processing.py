"""Per-frame image-processing profile (issue #19)."""

from __future__ import annotations

import io

from PIL import Image

from slyde_backend.config import Settings
from slyde_backend.frame import Frame
from slyde_backend.imaging import prepare_for_frame
from slyde_backend.processing import (
    SPECTRA6_PALETTE,
    ProcessingProfile,
    prepare,
    profile_for,
)


def _photo(size: tuple[int, int] = (300, 200)) -> bytes:
    buf = io.BytesIO()
    # a smooth gradient so dithering has something to do
    img = Image.new("RGB", size)
    img.putdata([(x % 256, y % 256, 128) for y in range(size[1]) for x in range(size[0])])
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_full_colour_profile_matches_legacy_prepare_for_frame() -> None:
    """The LCD profile must be byte-identical to the old global path (no Memento regression)."""
    data = _photo()
    canvas = (128, 96)
    via_profile = prepare(data, ProcessingProfile(canvas=canvas, fit="cover"))
    legacy = prepare_for_frame(data, canvas, fit="cover")
    assert via_profile == legacy
    with Image.open(io.BytesIO(via_profile)) as img:
        assert img.format == "JPEG" and img.size == canvas


def test_epaper_profile_maps_to_the_palette_with_dither() -> None:
    canvas = (80, 60)
    out = prepare(
        _photo(),
        ProcessingProfile(canvas=canvas, color_model="epaper", palette=SPECTRA6_PALETTE),
    )
    with Image.open(io.BytesIO(out)) as img:
        assert img.format == "PNG" and img.size == canvas
        colours = {c for _, c in img.convert("RGB").getcolors(maxcolors=100000)}
    # every pixel colour must be one of the 6 Spectra-6 palette entries
    assert colours.issubset(set(SPECTRA6_PALETTE))
    assert len(colours) > 1  # the gradient actually used several palette colours (dithered)


def test_profile_for_picks_epaper_for_a_sungale_frame() -> None:
    settings = Settings()
    lcd = profile_for(
        Frame.connected("10.0.0.5", backend="memento-lan"), settings, canvas=(800, 480)
    )
    assert lcd.color_model == "full"
    eink = profile_for(Frame.served("EF-1", backend="sungale-cloud"), settings, canvas=(1600, 1200))
    assert eink.color_model == "epaper" and eink.palette == SPECTRA6_PALETTE
