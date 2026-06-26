"""Slyde's own canonical asset previews — kept per asset, independent of any managed frame.

Like Immich keeps a thumbnail per asset, Slyde keeps its own viewable preview per asset so the
curation UI and library browsing never depend on a frame existing, a frame's format, or re-fetching
Immich every view. This is deliberately **frame-agnostic**: one normalized JPEG per asset, keyed by
``asset_id`` (not ``frame_id``), so it survives frame removal and format changes. A frame-specific
"how it looks on this panel" render layers on top of this, on demand (see ``frame_preview``).

Generated lazily (on first request) and persisted thereafter.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

from PIL import Image, ImageOps

PREVIEW_MAX_EDGE = 1440  # longest side of the canonical preview (downscale only, never upscale)
PREVIEW_QUALITY = 85

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe(asset_id: str) -> str:
    cleaned = _SAFE.sub("_", asset_id).strip("._") or "_"
    return cleaned[:200]


def render_canonical_preview(source: bytes) -> bytes:
    """A frame-agnostic, browser-displayable preview: EXIF-uprighted, fit to the max edge, JPEG."""
    with Image.open(io.BytesIO(source)) as im:
        upright = ImageOps.exif_transpose(im).convert("RGB")
        upright.thumbnail((PREVIEW_MAX_EDGE, PREVIEW_MAX_EDGE))  # contain, no upscale
        out = io.BytesIO()
        upright.save(out, format="JPEG", quality=PREVIEW_QUALITY)
        return out.getvalue()


class AssetPreviewCache:
    """On-disk store of canonical previews, one flat JPEG per asset (``<base>/<asset_id>.jpg``)."""

    def __init__(self, base_dir: str) -> None:
        self._base = Path(base_dir)

    def _path(self, asset_id: str) -> Path:
        return self._base / f"{_safe(asset_id)}.jpg"

    def get(self, asset_id: str) -> bytes | None:
        p = self._path(asset_id)
        return p.read_bytes() if p.is_file() else None

    def put(self, asset_id: str, data: bytes) -> None:
        self._base.mkdir(parents=True, exist_ok=True)
        self._path(asset_id).write_bytes(data)

    def keys(self) -> list[str]:
        if not self._base.is_dir():
            return []
        return sorted(p.stem for p in self._base.iterdir() if p.is_file())

    def delete(self, asset_id: str) -> bool:
        p = self._path(asset_id)
        if p.is_file():
            p.unlink()
            return True
        return False
