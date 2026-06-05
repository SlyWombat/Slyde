"""FirmwareService: GitHub-release check + md5-verified artifact serving."""

from __future__ import annotations

import asyncio
import hashlib
import io
import zipfile

import pytest

from slyde_backend.config import Settings
from slyde_backend.firmware import FirmwareError, FirmwareService

TRACK = "memento-softframe"


def _bundle() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("VERSION", "2.0.0")
    return buf.getvalue()


def _service(release: dict, files: dict[str, bytes]) -> FirmwareService:
    async def fetch(url: str) -> bytes:
        return files[url]

    async def release_fetch() -> dict:
        return release

    return FirmwareService(Settings(firmware_track=TRACK), fetch=fetch, release_fetch=release_fetch)


def test_check_registers_track_and_serve_returns_bundle() -> None:
    data = _bundle()
    md5 = hashlib.md5(data).hexdigest()
    release = {
        "tag_name": "v2.0.0",
        "assets": [
            {"name": f"{TRACK}.zip", "browser_download_url": "http://x/b.zip"},
            {"name": f"{TRACK}.zip.md5", "browser_download_url": "http://x/b.zip.md5"},
        ],
    }
    svc = _service(
        release, {"http://x/b.zip": data, "http://x/b.zip.md5": f"{md5}  {TRACK}.zip\n".encode()}
    )

    tracks = asyncio.run(svc.check())
    assert tracks[0].version == "2.0.0"
    assert tracks[0].md5 == md5
    assert asyncio.run(svc.serve(TRACK)) == data


def test_serve_rejects_corrupt_artifact() -> None:
    data = _bundle()
    release = {
        "tag_name": "v2",
        "assets": [
            {"name": f"{TRACK}.zip", "browser_download_url": "http://x/b.zip"},
            {"name": f"{TRACK}.zip.md5", "browser_download_url": "http://x/m"},
        ],
    }
    svc = _service(release, {"http://x/b.zip": data, "http://x/m": b"deadbeef  b.zip"})
    asyncio.run(svc.check())
    with pytest.raises(FirmwareError, match="mismatch"):
        asyncio.run(svc.serve(TRACK))


def test_check_errors_without_matching_asset() -> None:
    svc = _service({"tag_name": "v1", "assets": []}, {})
    with pytest.raises(FirmwareError, match="no '"):
        asyncio.run(svc.check())


def test_serve_unknown_track() -> None:
    svc = _service({"tag_name": "v1", "assets": []}, {})
    with pytest.raises(FirmwareError, match="unknown"):
        asyncio.run(svc.serve("nope"))


def test_auth_headers_reflect_token() -> None:
    assert FirmwareService(Settings())._auth_headers() == {}
    with_token = FirmwareService(Settings(firmware_github_token="tok"))
    assert with_token._auth_headers() == {"Authorization": "Bearer tok"}
