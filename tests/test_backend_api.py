"""Backend REST API end-to-end: FastAPI + emulator frame + a fake Immich library.

Drives the ASGI app through httpx's ASGITransport (running the real lifespan) instead of
Starlette's TestClient, which on Starlette 1.x requires the separate ``httpx2`` package.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Iterator
from typing import ClassVar

import httpx
import pytest
from PIL import Image

from conftest import HOST
from memento_backend.app import create_app
from memento_backend.config import Settings
from memento_backend.immich import ImmichAlbum, ImmichAsset
from memento_emulator import EmulatedFrame


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), (10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


class FakeImmich:
    asset = ImmichAsset(id="x1", file_name="beach.jpg", type="IMAGE")

    async def __aenter__(self) -> FakeImmich:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def list_albums(self) -> list[ImmichAlbum]:
        return [ImmichAlbum(id="a1", name="Trips", asset_count=1)]

    async def album_assets(self, album_id: str) -> list[ImmichAsset]:
        return [self.asset]

    async def get_asset(self, asset_id: str) -> ImmichAsset:
        return self.asset

    async def asset_bytes(self, asset_id: str, size: str) -> bytes:
        return _png_bytes()


class ApiHarness:
    """A tiny synchronous client over the ASGI app, sharing one event loop and lifespan."""

    def __init__(self, settings: Settings) -> None:
        self._loop = asyncio.new_event_loop()
        self.app = create_app(settings)
        self._lifespan = self.app.router.lifespan_context(self.app)
        self._loop.run_until_complete(self._lifespan.__aenter__())
        self.app.state.immich_factory = FakeImmich
        self.app.state.sync._immich_factory = FakeImmich
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )

    def request(self, method: str, url: str, **kw: object) -> httpx.Response:
        return self._loop.run_until_complete(self._client.request(method, url, **kw))  # type: ignore[arg-type]

    def get(self, url: str, **kw: object) -> httpx.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: object) -> httpx.Response:
        return self.request("POST", url, **kw)

    def delete(self, url: str, **kw: object) -> httpx.Response:
        return self.request("DELETE", url, **kw)

    def close(self) -> None:
        self._loop.run_until_complete(self._client.aclose())
        self._loop.run_until_complete(self._lifespan.__aexit__(None, None, None))
        self._loop.close()


@pytest.fixture
def client(frame: EmulatedFrame, tmp_path) -> Iterator[ApiHarness]:  # type: ignore[no-untyped-def]
    settings = Settings(
        frame_host=HOST,
        frame_discovery=False,
        immich_base_url="http://immich.test",
        immich_api_key="k",
        database_url=f"sqlite:///{tmp_path}/memento.db",
        frame_canvas="64x48",
    )
    harness = ApiHarness(settings)
    try:
        yield harness
    finally:
        harness.close()


F = f"/api/frames/{HOST}"


def test_health(client: ApiHarness) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["immich_configured"] is True


def test_sync_health_kpi(client: ApiHarness) -> None:
    res = client.get("/api/health/sync")
    assert res.status_code == 200
    assert res.text.startswith("OK")  # no subscriptions yet -> healthy


def test_list_frames_includes_configured(client: ApiHarness) -> None:
    frames = client.get("/api/frames").json()
    assert any(f["ip"] == HOST for f in frames)


def test_frame_info_strips_wifi(client: ApiHarness) -> None:
    info = client.get(F).json()
    assert info["host"] == HOST and info["config"]["Name"] == "Test Frame"
    assert "WiFiSSID" not in info["config"] and "WiFiPSWD" not in info["config"]


def test_albums_include_reserved_photos(client: ApiHarness) -> None:
    albums = client.get(f"{F}/albums").json()
    photos = next((a for a in albums if a["display_name"] == "Photos"), None)
    assert photos is not None and photos["reserved"] is True


def test_create_album(client: ApiHarness) -> None:
    albums = client.post(f"{F}/albums", json={"name": "Holidays"}).json()
    assert any(a["name"] == "Holidays" for a in albums)


def test_create_then_delete_folder(client: ApiHarness) -> None:
    client.post(f"{F}/albums", json={"name": "Temp"})
    assert any(a["name"] == "Temp" for a in client.get(f"{F}/albums").json())
    after = client.delete(f"{F}/albums/Temp").json()
    assert not any(a["name"] == "Temp" for a in after)


def test_remove_from_folder_keeps_photo(client: ApiHarness, frame: EmulatedFrame) -> None:
    client.post(f"{F}/sync", json={"album_id": "a1", "target_album": "Trip"})
    trip = next(a for a in client.get(f"{F}/albums").json() if a["name"] == "Trip")
    img = trip["images"][0]
    after = client.delete(f"{F}/albums/Trip/images/{img}").json()
    trip2 = next((a for a in after if a["name"] == "Trip"), None)
    assert trip2 is None or img not in trip2["images"]
    assert img in frame.state.photos  # the photo itself stays on the frame


def test_sync_into_target_album(client: ApiHarness, frame: EmulatedFrame) -> None:
    result = client.post(f"{F}/sync", json={"album_id": "a1", "target_album": "Beach"}).json()
    assert result["uploaded"] == 1
    dest = result["items"][0]["dest_name"]
    assert dest in frame.state.photos
    albums = client.get(f"{F}/albums").json()
    beach = next((a for a in albums if a["name"] == "Beach"), None)
    assert beach is not None and dest in beach["images"]


def test_resync_skips_after_upload(client: ApiHarness, frame: EmulatedFrame) -> None:
    first = client.post(f"{F}/sync", json={"album_id": "a1"}).json()
    assert first["uploaded"] == 1 and first["skipped"] == 0
    # A second identical sync must skip (the record was written after the upload succeeded).
    second = client.post(f"{F}/sync", json={"album_id": "a1"}).json()
    assert second["uploaded"] == 0 and second["skipped"] == 1


def test_direct_upload_and_thumbnail(client: ApiHarness, frame: EmulatedFrame) -> None:
    files = {"files": ("snap.png", _png_bytes(), "image/png")}
    result = client.post(f"{F}/upload", files=files, data={"target_album": "Direct"}).json()
    assert result["uploaded"] == 1
    dest = result["items"][0]["dest_name"]
    thumb = client.get(f"{F}/thumbnail/{dest}")
    assert thumb.status_code == 200 and thumb.content.startswith(b"\x89PNG")


def test_delete_photo(client: ApiHarness, frame: EmulatedFrame) -> None:
    client.post(f"{F}/sync", json={"album_id": "a1"})
    dest = next(iter(frame.state.photos))
    assert client.delete(f"{F}/photos/{dest}").status_code == 204
    assert dest not in frame.state.photos


def test_immich_albums(client: ApiHarness) -> None:
    albums = client.get("/api/immich/albums").json()
    assert albums == [{"id": "a1", "name": "Trips", "asset_count": 1}]


class MutableImmich:
    """Fake Immich whose 'Cats' album contents can change between syncs."""

    assets: ClassVar[list[ImmichAsset]] = []

    async def __aenter__(self) -> MutableImmich:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def album_assets(self, album_id: str) -> list[ImmichAsset]:
        return list(MutableImmich.assets)

    async def asset_bytes(self, asset_id: str, size: str) -> bytes:
        return _png_bytes()


def _cat(n: int) -> ImmichAsset:
    return ImmichAsset(id=f"c{n}", file_name=f"cat{n}.jpg", type="IMAGE")


def _cats_count(client: ApiHarness) -> int:
    albums = client.get(f"{F}/albums").json()
    cats = next((a for a in albums if a["name"] == "Cats"), None)
    return cats["image_count"] if cats else 0


def test_subscription_mirror_lifecycle(client: ApiHarness, frame: EmulatedFrame) -> None:
    client.app.state.sync._immich_factory = MutableImmich

    # Subscribe: mirrors the Immich album onto a frame album and syncs now.
    MutableImmich.assets = [_cat(1)]
    res = client.post(f"{F}/subscriptions", json={"album_id": "a1", "target_album": "Cats"}).json()
    assert res["uploaded"] == 1
    subs = client.get(f"{F}/subscriptions").json()
    assert len(subs) == 1 and subs[0]["target_album"] == "Cats"
    assert _cats_count(client) == 1

    # A new Immich item is mirrored in (existing one is skipped, not re-uploaded).
    MutableImmich.assets = [_cat(1), _cat(2)]
    res = client.post(f"{F}/subscriptions", json={"album_id": "a1", "target_album": "Cats"}).json()
    assert res["uploaded"] == 1 and res["skipped"] == 1
    assert _cats_count(client) == 2

    # A removed Immich item is dropped from the frame album (1:1 mirror).
    MutableImmich.assets = [_cat(2)]
    res = client.post(f"{F}/subscriptions", json={"album_id": "a1", "target_album": "Cats"}).json()
    assert res["removed"] == 1
    assert _cats_count(client) == 1

    # Unsubscribe stops syncing.
    assert client.delete(f"{F}/subscriptions/a1").status_code == 204
    assert client.get(f"{F}/subscriptions").json() == []
