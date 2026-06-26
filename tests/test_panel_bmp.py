"""Spectra-6 panel BMP codec (#11) — the byte-exact format the Sungale/Aluratek frame downloads.

The exact packing was reverse-engineered from the device's own files (round-trips byte-identical on
every staged sample). These tests pin the format so it can't regress: geometry, palette, size, the
pack/unpack round-trip, and that known images survive encode -> decode. A local-only test also
re-verifies byte-exactness against real device files when they're present (skipped in CI).
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from PIL import Image

from slyde_backend.panel_bmp import (
    PANEL_DISPLAY_SIZE,
    SPECTRA6_PALETTE,
    _pack4,
    _unpack4,
    decode_spectra6_bmp,
    encode_spectra6_bmp,
)

PANEL_FILE_SIZE = 960_118


def _solid(color: tuple[int, int, int]) -> Image.Image:
    return Image.new("RGB", PANEL_DISPLAY_SIZE, color)


def test_encode_has_exact_geometry_palette_and_size() -> None:
    data = encode_spectra6_bmp(_solid((0, 0, 0)))
    assert len(data) == PANEL_FILE_SIZE
    assert data[:2] == b"BM"

    pixoff = struct.unpack("<I", data[10:14])[0]
    size, w, h, planes, bpp, comp = struct.unpack("<IiiHHI", data[14:34])
    assert (pixoff, size, w, h, planes, bpp, comp) == (118, 40, 600, 3200, 1, 4, 0)

    # 16-entry BGRA palette with the panel's native index order (0=K 1=W 2=Y 3=R 5=B 6=G).
    palette = [tuple(data[54 + i * 4 + j] for j in (2, 1, 0)) for i in range(16)]  # -> RGB
    assert palette[0] == (0, 0, 0) and palette[1] == (255, 255, 255)
    assert palette[2] == (255, 255, 0) and palette[3] == (255, 0, 0)
    assert palette[5] == (0, 0, 255) and palette[6] == (0, 255, 0)


def test_every_palette_colour_round_trips_to_a_solid_image() -> None:
    # A solid image of each panel colour must decode back to that exact colour everywhere.
    for color in SPECTRA6_PALETTE:
        decoded = decode_spectra6_bmp(encode_spectra6_bmp(_solid(color)))
        assert decoded.size == PANEL_DISPLAY_SIZE
        # extrema per band == (c, c) means every pixel is exactly that one colour.
        assert decoded.getextrema() == tuple((c, c) for c in color)


def test_packing_geometry_keeps_left_and_right_in_place() -> None:
    # Left half red, right half blue: after encode/decode the halves must stay put (no scramble).
    img = Image.new("RGB", PANEL_DISPLAY_SIZE, (255, 0, 0))
    img.paste((0, 0, 255), (600, 0, 1200, 1600))  # right half blue
    decoded = decode_spectra6_bmp(encode_spectra6_bmp(img))
    assert decoded.getpixel((300, 800)) == (255, 0, 0)  # left stays red
    assert decoded.getpixel((900, 800)) == (0, 0, 255)  # right stays blue


def test_pack_unpack_is_an_exact_inverse() -> None:
    raw = bytes(range(16)) * 4  # nibble values 0..15
    assert _unpack4(_pack4(raw)) == raw
    assert len(_pack4(raw)) == len(raw) // 2


def test_oversized_input_is_resized_to_the_panel() -> None:
    data = encode_spectra6_bmp(Image.new("RGB", (400, 300), (0, 255, 0)))
    assert len(data) == PANEL_FILE_SIZE  # resized to the panel, still the exact file size


# --- local-only: re-verify byte-exactness against real device captures, if present ---------------
_STAGED = list(
    (Path(__file__).resolve().parents[1] / "experiments" / "aluratek-eframe" / "staging").rglob(
        "*.bmp"
    )
)


@pytest.mark.skipif(not _STAGED, reason="no staged device BMPs present (local-only check)")
@pytest.mark.parametrize("path", _STAGED, ids=lambda p: p.name)
def test_reencode_of_real_device_bmp_is_byte_identical(path: Path) -> None:
    """encode(decode(real)) == real — proves the codec reproduces the device's exact bytes."""
    from slyde_backend.panel_bmp import _display_to_rows

    real = path.read_bytes()
    # Decode to the display index image, then re-pack through the exact forward transform.
    off = struct.unpack("<I", real[10:14])[0]
    from slyde_backend.panel_bmp import _BUF_H, _BUF_W, _HEADER, _PIXELS, _rows_to_display

    rows = Image.frombytes("P", (_BUF_W, _BUF_H), _unpack4(real[off : off + _PIXELS]))
    disp = _rows_to_display(rows)
    reencoded = _HEADER + _pack4(_display_to_rows(disp).tobytes())
    assert reencoded == real
