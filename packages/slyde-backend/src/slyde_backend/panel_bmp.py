"""Spectra-6 e-paper panel BMP codec (#11) — the byte format the Sungale/Aluratek frame downloads.

The frame pulls a fixed 960,118-byte file per image: a **4-bit indexed BMP** whose 1200x1600 display
image is stored in a packed **600x3200** buffer. We reverse-engineered the exact format from the
device's own files (round-trips byte-identical on every staged sample); see
``docs/sungale-eframe-integration-plan.md`` §2.

Format, exactly:
- BMP, ``BITMAPINFOHEADER``, ``bpp=4``, ``compression=0``, 16-entry palette, 600x3200 bottom-up.
- Palette (the panel's pure Spectra-6 primaries) at native index order
  ``0=black 1=white 2=yellow 3=red 4=(spare/black) 5=blue 6=green`` (7..15 padded black).
- Packing: the 1200x1600 display image maps to the 600x3200 buffer as the panel's two source-driver
  halves — ``_display_to_rows`` is the exact forward transform, ``_rows_to_display`` its inverse.

Implemented with Pillow only (no numpy), to match the project's imaging stack. So our replacement
cloud feeds the frame a pixel-perfect, byte-compatible image.
"""

from __future__ import annotations

import struct

from PIL import Image

# Display + stored geometry (fixed properties of the EL133UF1 panel).
PANEL_DISPLAY_SIZE = (1200, 1600)  # (width, height) the image is composed at
_BUF_W, _BUF_H = 600, 3200  # packed buffer geometry in the file
_HALF_W = _BUF_W  # each source-driver half is 600 wide
_HALF_H = PANEL_DISPLAY_SIZE[1]  # and 1600 tall
_ROW_BYTES = _BUF_W // 2  # 4bpp -> 2 px per byte
_PIXELS = _ROW_BYTES * _BUF_H  # 960_000
_FLIP = Image.Transpose.FLIP_TOP_BOTTOM

# The six reproducible colours the panel renders (pure primaries, read from the device palette).
SPECTRA6_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),  # black
    (255, 255, 255),  # white
    (255, 255, 0),  # yellow
    (255, 0, 0),  # red
    (0, 0, 255),  # blue
    (0, 255, 0),  # green
)
# Where each of the six colours sits in the panel's 16-slot native palette (index 4 is a spare).
_DEVICE_INDEX = (0, 1, 2, 3, 5, 6)
_PALETTE_16: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),  # 0 black
    (255, 255, 255),  # 1 white
    (255, 255, 0),  # 2 yellow
    (255, 0, 0),  # 3 red
    (0, 0, 0),  # 4 spare (black)
    (0, 0, 255),  # 5 blue
    (0, 255, 0),  # 6 green
) + ((0, 0, 0),) * 9  # 7..15 padding


def _quantize_palette() -> Image.Image:
    pal = Image.new("P", (1, 1))
    flat: list[int] = []
    for r, g, b in SPECTRA6_PALETTE:
        flat += [r, g, b]
    flat += flat[:3] * (256 - len(SPECTRA6_PALETTE))  # pad to 256 (Pillow requirement)
    pal.putpalette(flat)
    return pal


_QUANT_PALETTE = _quantize_palette()
# quantised index (0..5, plus black-padding >=6) -> the panel's native palette index.
_TO_DEVICE_LUT = bytes(_DEVICE_INDEX[i] if i < len(_DEVICE_INDEX) else 0 for i in range(256))
_DISPLAY_PALETTE: list[int] = []
for _r, _g, _b in _PALETTE_16:
    _DISPLAY_PALETTE += [_r, _g, _b]
_DISPLAY_PALETTE += _DISPLAY_PALETTE[:3] * (256 - len(_PALETTE_16))


def _header() -> bytes:
    palette = b"".join(bytes((b, g, r, 0)) for (r, g, b) in _PALETTE_16)  # BGRA
    info = struct.pack("<IiiHHIIiiII", 40, _BUF_W, _BUF_H, 1, 4, 0, _PIXELS, 0, 0, 0, 0)
    file_hdr = struct.pack("<2sIHHI", b"BM", 14 + 40 + 64 + _PIXELS, 0, 0, 14 + 40 + 64)
    return file_hdr + info + palette


_HEADER = _header()


def _pack4(index_bytes: bytes) -> bytes:
    """Pack one-byte-per-pixel indices into 4bpp (first pixel in the high nibble)."""
    hi, lo = index_bytes[0::2], index_bytes[1::2]  # equal length: pixel count is always even
    return bytes((h << 4) | (low & 0x0F) for h, low in zip(hi, lo, strict=True))


def _unpack4(packed: bytes) -> bytes:
    out = bytearray(len(packed) * 2)
    out[0::2] = bytes(byte >> 4 for byte in packed)
    out[1::2] = bytes(byte & 0x0F for byte in packed)
    return bytes(out)


def _display_to_rows(disp: Image.Image) -> Image.Image:
    """Display index image (1200x1600) -> stored buffer (600x3200), bottom-up file order."""
    df = disp.transpose(_FLIP)
    top = df.crop((_HALF_W, 0, _BUF_W * 2, _HALF_H))  # right half
    bot = df.crop((0, 0, _HALF_W, _HALF_H))  # left half
    buf = Image.new("P", (_BUF_W, _BUF_H))
    buf.paste(top, (0, 0))  # top|bot stacked
    buf.paste(bot, (0, _HALF_H))
    return buf.transpose(_FLIP)


def _rows_to_display(rows: Image.Image) -> Image.Image:
    """Inverse of :func:`_display_to_rows` (for decoding / tools)."""
    buf = rows.transpose(_FLIP)
    top = buf.crop((0, 0, _BUF_W, _HALF_H))
    bot = buf.crop((0, _HALF_H, _BUF_W, _BUF_H))
    disp = Image.new("P", PANEL_DISPLAY_SIZE)
    disp.paste(bot, (0, 0))  # bot|top side by side
    disp.paste(top, (_HALF_W, 0))
    return disp.transpose(_FLIP)


def encode_spectra6_bmp(img: Image.Image) -> bytes:
    """Encode an RGB image to the panel's exact 960,118-byte 4bpp BMP (quantize + dither + pack)."""
    if img.size != PANEL_DISPLAY_SIZE:
        img = img.resize(PANEL_DISPLAY_SIZE)
    quant = img.convert("RGB").quantize(palette=_QUANT_PALETTE, dither=Image.Dither.FLOYDSTEINBERG)
    device = Image.frombytes("P", PANEL_DISPLAY_SIZE, quant.tobytes().translate(_TO_DEVICE_LUT))
    rows = _display_to_rows(device)
    return _HEADER + _pack4(rows.tobytes())


def decode_spectra6_bmp(data: bytes) -> Image.Image:
    """Decode a panel BMP back to an RGB display image (the inverse, for tools / tests)."""
    off = struct.unpack("<I", data[10:14])[0]
    rows = Image.frombytes("P", (_BUF_W, _BUF_H), _unpack4(data[off : off + _PIXELS]))
    disp = _rows_to_display(rows)
    disp.putpalette(_DISPLAY_PALETTE)
    return disp.convert("RGB")
