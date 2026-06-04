"""Device-side self-update: download an app bundle, verify its md5, stage it, then restart.

Triggered by ``Flow.TriggerUpdate({url, md5})``. The download/verify/extract steps are pure and
unit-tested; the restart is a thin, packaging-specific callback supplied by the caller (e.g. a
systemd ``ExecStart`` re-exec, or just exiting so ``Restart=always`` relaunches the new code).
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Callable
from pathlib import Path
from urllib.request import urlopen


class UpdateError(RuntimeError):
    """Raised when an update can't be verified or safely applied."""


def verify_md5(data: bytes, expected: str) -> None:
    actual = hashlib.md5(data).hexdigest()
    if expected and actual != expected.lower():
        raise UpdateError(f"md5 mismatch: expected {expected.lower()}, got {actual}")


def stage_bundle(data: bytes, target_dir: Path) -> list[str]:
    """Extract a ``.zip`` app bundle into ``target_dir``. Rejects path-traversal members."""
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        unsafe = [n for n in bundle.namelist() if n.startswith("/") or ".." in Path(n).parts]
        if unsafe:
            raise UpdateError(f"unsafe paths in bundle: {unsafe[:3]}")
        target_dir.mkdir(parents=True, exist_ok=True)
        bundle.extractall(target_dir)
        return bundle.namelist()


def download(url: str, *, timeout: float = 60.0) -> bytes:
    with urlopen(url, timeout=timeout) as response:
        return bytes(response.read())


def apply_update(
    url: str,
    md5: str,
    *,
    target_dir: Path,
    restart: Callable[[], None] | None = None,
) -> list[str]:
    """Download → verify md5 → stage into ``target_dir`` → optionally ``restart()``."""
    data = download(url)
    verify_md5(data, md5)
    members = stage_bundle(data, target_dir)
    if restart is not None:
        restart()
    return members
