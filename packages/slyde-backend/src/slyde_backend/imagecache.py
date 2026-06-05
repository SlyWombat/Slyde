"""Processed-image cache (issue #25): copies of the prepared/edited images per frame.

The hub prepares each photo for a specific frame once — smart-blur edges, fit to the panel, e-ink
palette/dither — and keeps the result here, **ready to send/serve**. This decouples *processing*
from *delivery*: a served (cloud) frame that wakes on its own schedule is handed an already-prepared
image (the #22 delivery seam reads from here), and connected frames don't re-process on every sync.

On-disk, one directory per frame, one file per cached image key (e.g. a dest filename). The
directory is created lazily on first write, so constructing a cache is cheap and side-effect free.
"""

from __future__ import annotations

import re
from pathlib import Path

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe(part: str) -> str:
    """Filesystem-safe path component (frame ids may be IPs/codes; keys may be filenames)."""
    cleaned = _SAFE.sub("_", part).strip("._") or "_"
    return cleaned[:200]


class ImageCache:
    """On-disk store of prepared image bytes, keyed by ``(frame_id, key)``."""

    def __init__(self, base_dir: str) -> None:
        self._base = Path(base_dir)

    def _dir(self, frame_id: str) -> Path:
        return self._base / _safe(frame_id)

    def _path(self, frame_id: str, key: str) -> Path:
        return self._dir(frame_id) / _safe(key)

    def put(self, frame_id: str, key: str, data: bytes) -> None:
        """Cache a prepared image for a frame (creates the cache dir on first write)."""
        d = self._dir(frame_id)
        d.mkdir(parents=True, exist_ok=True)
        self._path(frame_id, key).write_bytes(data)

    def get(self, frame_id: str, key: str) -> bytes | None:
        p = self._path(frame_id, key)
        return p.read_bytes() if p.is_file() else None

    def keys(self, frame_id: str) -> list[str]:
        """The cached image keys for a frame (sorted), or ``[]`` if none."""
        d = self._dir(frame_id)
        return sorted(p.name for p in d.iterdir() if p.is_file()) if d.is_dir() else []

    def current(self, frame_id: str) -> bytes | None:
        """The image to serve a frame right now (first cached key for the slice; #23 refines)."""
        keys = self.keys(frame_id)
        return self.get(frame_id, keys[0]) if keys else None

    def clear(self, frame_id: str) -> None:
        d = self._dir(frame_id)
        if d.is_dir():
            for p in d.iterdir():
                p.unlink()
            d.rmdir()
