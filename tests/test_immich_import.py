"""Importing a frame's on-device albums into Immich (#72)."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
from datetime import UTC, datetime

import httpx
import pytest
from PIL import Image

from memento_core.albums import ALBUM_EVENING, ALBUM_PHOTOS, Album, AlbumData
from slyde_backend.config import Settings
from slyde_backend.frame import Frame
from slyde_backend.immich_import import (
    import_frame_albums_to_immich,
    plan_albums,
    taken_at,
)
from slyde_backend.immich_write import ImmichWriter


class _FakeImmich:
    """Enough of Immich v3 to exercise the importer: upload with checksum dedupe, albums by name."""

    def __init__(self) -> None:
        self.assets: dict[str, str] = {}
        self.albums: dict[str, str] = {}  # name -> id
        self.members: dict[str, list[str]] = {}  # album id -> asset ids
        self.uploads: list[dict[str, str]] = []
        self.album_creates = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/assets":
            body = request.content
            # Dedupe on the file's own bytes, the way Immich's checksum dedupe does — NOT on the
            # raw request, whose multipart boundary is random per call.
            content = re.search(rb'name="assetData".*?\r\n\r\n(.*?)\r\n--', body, re.S)
            marker = hashlib.sha1(content.group(1) if content else body).hexdigest()
            self.uploads.append(
                {
                    name.decode(): value.decode()
                    for name, value in re.findall(rb'name="([^"]+)"\r\n\r\n(.*?)\r\n--', body, re.S)
                }
            )
            if marker in self.assets:
                return httpx.Response(200, json={"id": self.assets[marker], "status": "duplicate"})
            asset_id = f"asset-{len(self.assets) + 1}"
            self.assets[marker] = asset_id
            return httpx.Response(201, json={"id": asset_id, "status": "created"})
        if request.method == "GET" and path == "/api/albums":
            return httpx.Response(
                200, json=[{"id": i, "albumName": n} for n, i in self.albums.items()]
            )
        if request.method == "POST" and path == "/api/albums":
            name = json.loads(request.content)["albumName"]
            self.album_creates += 1
            album_id = f"album-{len(self.albums) + 1}"
            self.albums[name] = album_id
            self.members[album_id] = []
            return httpx.Response(201, json={"id": album_id, "albumName": name})
        if request.method == "PUT" and path.endswith("/assets"):
            album_id = path.split("/")[-2]
            ids = json.loads(request.content)["ids"]
            out = []
            for asset_id in ids:
                fresh = asset_id not in self.members[album_id]
                if fresh:
                    self.members[album_id].append(asset_id)
                out.append({"id": asset_id, "success": fresh})
            return httpx.Response(200, json=out)
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})

    def writer(self) -> ImmichWriter:
        return ImmichWriter(
            "http://immich.test", "secret", transport=httpx.MockTransport(self.handler)
        )


class _FakeFrameService:
    """Serves album data and per-filename bytes, standing in for a real frame."""

    def __init__(self, album_data: AlbumData, *, broken: set[str] | None = None) -> None:
        self._album_data = album_data
        self._broken = broken or set()
        self.downloads: list[str] = []

    async def get_album_data(self, frame_id: str) -> AlbumData:
        return self._album_data

    async def download_image(self, frame_id: str, name: str) -> bytes:
        self.downloads.append(name)
        if name in self._broken:
            raise TimeoutError("frame stopped answering")
        return _jpeg(name)


def _jpeg(name: str, *, taken: str | None = None) -> bytes:
    buf = io.BytesIO()
    image = Image.new("RGB", (8, 8), (len(name) * 7 % 255, 40, 90))
    exif = image.getexif()
    if taken:
        exif[36867] = taken
    image.save(buf, format="JPEG", exif=exif)
    # Pad so each filename yields distinct trailing bytes (the fake's dedupe marker).
    return buf.getvalue() + name.encode().ljust(64, b"\0")


def _album_data() -> AlbumData:
    return AlbumData(
        albums=[
            Album(name=ALBUM_PHOTOS, images=["a.jpg", "b.jpg", "c.jpg", "slyde-interlude-0.jpg"]),
            Album(name=ALBUM_EVENING, images=["a.jpg"]),
            Album(name="Mexico", images=["a.jpg", "b.jpg"]),
            Album(name="Louvre", images=["c.jpg"]),
        ]
    )


def _frame() -> Frame:
    return Frame.connected("10.0.0.5", backend="memento-lan", name="Living Room", guid="guid-1")


def test_plan_puts_every_photo_in_all_and_mirrors_only_user_folders() -> None:
    """Reserved albums are the device's own set, not user folders: their contents reach '- All'
    but they get no album. A file in several albums is pulled once and filed in each (#72)."""
    names, folders = plan_albums(_album_data(), "Memento")
    assert names == ["a.jpg", "b.jpg", "c.jpg"]  # deduped, interlude buffer excluded
    assert sorted(folders) == ["Memento - Louvre", "Memento - Mexico"]
    assert folders["Memento - Mexico"] == ["a.jpg", "b.jpg"]
    assert "Memento - Photos" not in folders and "Memento - Evening" not in folders


def test_taken_at_prefers_exif_then_filename_then_now() -> None:
    """#72's dates trap: without this every import piles up at one end of the Immich timeline."""
    assert taken_at(_jpeg("x.jpg", taken="2019:04:05 06:07:08"), "x.jpg") == datetime(
        2019, 4, 5, 6, 7, 8, tzinfo=UTC
    )
    assert taken_at(_jpeg("pxl_20221010_232749871.jpg"), "pxl_20221010_232749871.jpg") == datetime(
        2022, 10, 10, 23, 27, 49, tzinfo=UTC
    )
    assert taken_at(_jpeg("dscn1141.jpg"), "dscn1141.jpg").date() == datetime.now(UTC).date()


def test_import_uploads_once_and_files_each_photo_into_every_album() -> None:
    immich = _FakeImmich()
    frames = _FakeFrameService(_album_data())

    async def run() -> object:
        async with immich.writer() as writer:
            return await import_frame_albums_to_immich(
                frame=_frame(),
                frame_service=frames,  # type: ignore[arg-type]
                settings=Settings(frame_import_delay=0),
                prefix="Memento",
                writer=writer,
            )

    result = asyncio.run(run())
    assert frames.downloads == ["a.jpg", "b.jpg", "c.jpg"]  # pulled once each, not once per album
    assert (result.total, result.uploaded, result.failed) == (3, 3, 0)
    assert sorted(immich.albums) == ["Memento - All", "Memento - Louvre", "Memento - Mexico"]
    all_id = immich.albums["Memento - All"]
    assert len(immich.members[all_id]) == 3
    assert len(immich.members[immich.albums["Memento - Mexico"]]) == 2
    assert len(immich.members[immich.albums["Memento - Louvre"]]) == 1


def test_rerun_adds_nothing_new_and_makes_no_duplicate_albums() -> None:
    """Idempotence (#72): re-running after a partial import must fill gaps, not duplicate."""
    immich = _FakeImmich()

    async def run() -> object:
        frames = _FakeFrameService(_album_data())
        async with immich.writer() as writer:
            return await import_frame_albums_to_immich(
                frame=_frame(),
                frame_service=frames,  # type: ignore[arg-type]
                settings=Settings(frame_import_delay=0),
                prefix="Memento",
                writer=writer,
            )

    asyncio.run(run())
    second = asyncio.run(run())
    assert immich.album_creates == 3  # albums matched by name the second time, not recreated
    assert len(immich.assets) == 3  # Immich deduped the re-uploads
    assert second.skipped == 3 and second.uploaded == 0
    assert len(immich.members[immich.albums["Memento - All"]]) == 3  # no double-add


def test_one_unreadable_photo_does_not_abort_the_import() -> None:
    """A 1000-photo pull off a low-power frame will hit a bad read; it must carry on."""
    immich = _FakeImmich()
    frames = _FakeFrameService(_album_data(), broken={"b.jpg"})

    async def run() -> object:
        async with immich.writer() as writer:
            return await import_frame_albums_to_immich(
                frame=_frame(),
                frame_service=frames,  # type: ignore[arg-type]
                settings=Settings(frame_import_delay=0),
                prefix="Memento",
                writer=writer,
            )

    result = asyncio.run(run())
    assert (result.uploaded, result.failed) == (2, 1)
    assert len(immich.members[immich.albums["Memento - All"]]) == 2
    assert len(immich.members[immich.albums["Memento - Mexico"]]) == 1  # b.jpg simply absent


def test_writer_is_the_only_thing_that_writes_and_it_is_not_the_delivery_client() -> None:
    """The read-only contract on ImmichClient still means what it said: the class used by the
    automatic path has no write methods, and writing lives in a separate, explicitly-built one."""
    from slyde_backend.immich import ImmichClient

    assert not hasattr(ImmichClient, "upload_asset")
    assert not hasattr(ImmichClient, "ensure_album")
    assert not hasattr(ImmichClient, "add_assets")
    for name in ("upload_asset", "ensure_album", "add_assets", "find_album"):
        assert hasattr(ImmichWriter, name)


def test_upload_sends_the_fields_immich_v3_requires() -> None:
    """v3.0.1 rejects an upload without fileCreatedAt/fileModifiedAt — pin the wire shape."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode("latin-1")
        seen["multipart"] = request.headers["content-type"].startswith("multipart/form-data")
        for field in ("deviceAssetId", "deviceId", "fileCreatedAt", "fileModifiedAt", "assetData"):
            seen[field] = f'name="{field}"' in body
        return httpx.Response(201, json={"id": "a1", "status": "created"})

    async def run() -> None:
        async with ImmichWriter(
            "http://immich.test", "k", transport=httpx.MockTransport(handler)
        ) as writer:
            await writer.upload_asset(
                b"bytes",
                filename="a.jpg",
                device_asset_id="a.jpg",
                device_id="slyde-frame-x",
                created_at=datetime(2020, 1, 2, 3, 4, 5, tzinfo=UTC),
            )

    asyncio.run(run())
    assert all(seen.values()), seen


def test_upload_failure_is_reported_not_swallowed() -> None:
    from slyde_backend.immich import ImmichError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(413, json={"message": "too big"})

    async def run() -> None:
        async with ImmichWriter(
            "http://immich.test", "k", transport=httpx.MockTransport(handler)
        ) as writer:
            await writer.upload_asset(
                b"x",
                filename="a.jpg",
                device_asset_id="a.jpg",
                device_id="d",
                created_at=datetime.now(UTC),
            )

    with pytest.raises(ImmichError) as caught:
        asyncio.run(run())
    assert "413" in str(caught.value)
