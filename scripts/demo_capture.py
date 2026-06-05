"""Render a launch demo GIF: photos cycling on a Slyde frame.

Produces an on-brand animated GIF of a digital frame cycling through photos — the visual for README
/ Show HN / Reddit posts. Self-contained (Pillow only); no emulator, backend, or network needed.

Usage:
    python scripts/demo_capture.py                         # generates scenic placeholder photos
    python scripts/demo_capture.py --images ~/launch-pics  # use your own photos (jpg/png)
    python scripts/demo_capture.py --out assets/demo.gif --hold 1.2

For posts you'll want real photos: point --images at a folder of 5-8 nice landscape shots (e.g.
exported from your Immich "Memento" album). The committed sample uses generated scenics so no
personal photos live in the repo.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

# Canvas + Slyde palette (matches the logo / banner).
W, H = 880, 600
INK = (11, 14, 20)
BEZEL = (20, 25, 37)
EDGE = (34, 42, 58)
ACCENT = (91, 140, 255)
DOT_OFF = (47, 59, 87)
# Frame screen rectangle inside the canvas.
SX, SY, SW, SH = 90, 70, 700, 420


def _scenic(i: int, n: int) -> Image.Image:
    """A stylized 'photo' (sunset/landscape gradient) so the sample GIF looks intentional."""
    w, h = 1200, 720
    img = Image.new("RGB", (w, h))
    px = img.load()
    # sky gradient, hue rotated per image
    base = (i / max(1, n)) * 2 * math.pi
    top = (52 + int(34 * math.sin(base)), 92, 168 + int(50 * math.cos(base)))
    bot = (255, 188 + int(36 * math.sin(base + 1)), 132)
    for y in range(h):
        t = y / h
        px_row = tuple(min(255, int(top[c] + (bot[c] - top[c]) * t)) for c in range(3))
        for x in range(w):
            px[x, y] = px_row
    d = ImageDraw.Draw(img)
    d.ellipse([w * 0.18, h * 0.20, w * 0.18 + 165, h * 0.20 + 165], fill=(255, 231, 176))
    # layered hills (kept above ink so the photo reads full-bleed)
    for layer, col in enumerate([(44, 60, 96), (30, 42, 70), (20, 28, 48)]):
        base_y = int(h * (0.66 + layer * 0.11))
        pts = [(0, h)]
        for x in range(0, w + 1, 120):
            pts.append((x, base_y + int(60 * math.sin(x / 180 + layer + i))))
        pts.append((w, h))
        d.polygon(pts, fill=col)
    return img


def _cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    sw, sh = img.size
    tw, th = size
    scale = max(tw / sw, th / sh)
    img = img.resize((max(tw, int(sw * scale)), max(th, int(sh * scale))), Image.Resampling.LANCZOS)
    left, top = (img.width - tw) // 2, (img.height - th) // 2
    return img.crop((left, top, left + tw, top + th))


def _frame(photo: Image.Image, idx: int, total: int) -> Image.Image:
    """Composite a photo into the Slyde frame bezel with a slideshow-dot indicator."""
    canvas = Image.new("RGB", (W, H), INK)
    d = ImageDraw.Draw(canvas)
    # soft glow
    glow = Image.new("RGB", (W, H), INK)
    ImageDraw.Draw(glow).ellipse([W * 0.1, -120, W * 0.9, 320], fill=(22, 32, 58))
    canvas = Image.blend(canvas, glow.filter(ImageFilter.GaussianBlur(80)), 0.6)
    d = ImageDraw.Draw(canvas)
    # bezel
    d.rounded_rectangle(
        [SX - 40, SY - 40, SX + SW + 40, SY + SH + 70], radius=44, fill=BEZEL, outline=EDGE, width=6
    )
    # screen photo
    shot = _cover(photo.convert("RGB"), (SW, SH))
    mask = Image.new("L", (SW, SH), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SW, SH], radius=14, fill=255)
    canvas.paste(shot, (SX, SY), mask)
    d.rounded_rectangle([SX, SY, SX + SW, SY + SH], radius=14, outline=INK, width=3)
    # slideshow dots
    cx = SX + SW // 2 - (total * 22) // 2
    dy = SY + SH + 34
    for k in range(total):
        c = ACCENT if k == idx else DOT_OFF
        d.ellipse([cx + k * 22, dy, cx + k * 22 + 12, dy + 12], fill=c)
    return canvas


def main() -> int:
    ap = argparse.ArgumentParser(description="Render the Slyde demo GIF.")
    ap.add_argument("--images", default="", help="folder of photos (jpg/png); omit for scenics")
    ap.add_argument("--out", default="assets/demo.gif")
    ap.add_argument("--hold", type=float, default=1.3, help="seconds each photo is shown")
    ap.add_argument("--count", type=int, default=6, help="number of scenic placeholders")
    args = ap.parse_args()

    if args.images:
        paths = sorted(
            p for p in Path(args.images).iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"}
        )
        photos = [Image.open(p) for p in paths] or [
            _scenic(i, args.count) for i in range(args.count)
        ]
    else:
        photos = [_scenic(i, args.count) for i in range(args.count)]

    total = len(photos)
    frames = [_frame(p, i, total) for i, p in enumerate(photos)]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        out,
        save_all=True,
        append_images=frames[1:],
        duration=int(args.hold * 1000),
        loop=0,
        optimize=True,
    )
    print(f"wrote {out} ({total} frames, {out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
