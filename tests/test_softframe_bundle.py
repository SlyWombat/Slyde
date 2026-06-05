"""The OTA bundle builder and that the manager + device consume what it produces."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import io
import zipfile
from pathlib import Path

from memento_emulator.updater import stage_bundle
from slyde_backend.config import Settings
from slyde_backend.firmware import FirmwareService

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "build_softframe_bundle", REPO / "scripts" / "build_softframe_bundle.py"
)
assert _spec and _spec.loader
bsb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bsb)


def test_bundle_contains_packages_and_version() -> None:
    data = bsb.build_bundle("1.2.3", REPO)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        assert "memento_core/__init__.py" in names
        assert "memento_emulator/__init__.py" in names
        assert archive.read("VERSION").decode() == "1.2.3"


def test_write_outputs_md5_sidecar_matches(tmp_path) -> None:  # type: ignore[no-untyped-def]
    zip_path, md5_path = bsb.write_outputs("9.9.9", tmp_path, REPO)
    digest = hashlib.md5(zip_path.read_bytes()).hexdigest()
    parts = md5_path.read_text().split()
    assert parts == [digest, "memento-softframe.zip"]


def test_device_can_stage_the_bundle(tmp_path) -> None:  # type: ignore[no-untyped-def]
    members = stage_bundle(bsb.build_bundle("3.0.0", REPO), tmp_path / "app")
    assert "memento_core/__init__.py" in members
    assert (tmp_path / "app" / "memento_emulator" / "__init__.py").is_file()
    assert (tmp_path / "app" / "VERSION").read_text() == "3.0.0"


def test_manager_consumes_published_bundle(tmp_path) -> None:  # type: ignore[no-untyped-def]
    zip_path, md5_path = bsb.write_outputs("4.5.6", tmp_path, REPO)
    release = {
        "tag_name": "softframe-v4.5.6",
        "assets": [
            {"name": "memento-softframe.zip", "browser_download_url": str(zip_path)},
            {"name": "memento-softframe.zip.md5", "browser_download_url": str(md5_path)},
        ],
    }

    async def fetch(url: str) -> bytes:
        return Path(url).read_bytes()

    async def release_fetch() -> dict:
        return release

    svc = FirmwareService(
        Settings(firmware_track="memento-softframe"), fetch=fetch, release_fetch=release_fetch
    )
    tracks = asyncio.run(svc.check())
    assert tracks[0].version == "4.5.6"  # parsed from the softframe-v… tag
    assert asyncio.run(svc.serve("memento-softframe")) == zip_path.read_bytes()
