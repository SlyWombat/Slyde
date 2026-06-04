"""Build the soft-frame OTA bundle: a zip of the device packages + a VERSION file, and its .md5.

The bundle is "app-only" (Python source for ``memento_core`` + ``memento_emulator``, no deps) — the
device stages it onto its PYTHONPATH and restarts. Output names match what FirmwareService.check()
expects: ``memento-softframe.zip`` and ``memento-softframe.zip.md5``.

Usage:  python scripts/build_softframe_bundle.py <version> [out_dir]
"""

from __future__ import annotations

import hashlib
import io
import sys
import zipfile
from pathlib import Path

TRACK = "memento-softframe"
# (package src dir relative to repo root, importable package name)
PACKAGES = [
    ("packages/memento-core/src", "memento_core"),
    ("packages/memento-emulator/src", "memento_emulator"),
]


def build_bundle(version: str, repo_root: Path) -> bytes:
    """Return the zip bytes: each package at the archive root, plus a VERSION file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for src_rel, package in PACKAGES:
            package_dir = repo_root / src_rel / package
            if not package_dir.is_dir():
                raise FileNotFoundError(f"package not found: {package_dir}")
            for path in sorted(package_dir.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    archive.write(path, f"{package}/{path.relative_to(package_dir).as_posix()}")
        archive.writestr("VERSION", version)
    return buf.getvalue()


def write_outputs(version: str, out_dir: Path, repo_root: Path) -> tuple[Path, Path]:
    data = build_bundle(version, repo_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{TRACK}.zip"
    zip_path.write_bytes(data)
    digest = hashlib.md5(data).hexdigest()
    md5_path = out_dir / f"{TRACK}.zip.md5"
    md5_path.write_text(f"{digest}  {TRACK}.zip\n")
    return zip_path, md5_path


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: build_softframe_bundle.py <version> [out_dir]", file=sys.stderr)
        return 2
    version = args[0].lstrip("vV")
    out_dir = Path(args[1]) if len(args) > 1 else Path("dist")
    repo_root = Path(__file__).resolve().parent.parent
    zip_path, md5_path = write_outputs(version, out_dir, repo_root)
    print(f"built {zip_path} ({zip_path.stat().st_size} bytes) + {md5_path.name} (v{version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
