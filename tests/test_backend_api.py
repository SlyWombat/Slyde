"""Backend REST API end-to-end: FastAPI + emulator frame + a fake Immich library.

Drives the ASGI app through httpx's ASGITransport (running the real lifespan) instead of
Starlette's TestClient, which on Starlette 1.x requires the separate ``httpx2`` package.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Iterator

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
    """Stand-in for ImmichClient with one album and one image asset."""

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
        # Inject the fake Immich into both the router factory and the sync service.
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


def test_health(client: ApiHarness) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["immich_configured"] is True


def test_list_albums(client: ApiHarness) -> None:
    albums = client.get("/api/immich/albums").json()
    assert albums == [{"id": "a1", "name": "Trips", "asset_count": 1}]


def test_frame_info_strips_wifi(client: ApiHarness, frame: EmulatedFrame) -> None:
    info = client.get("/api/frame").json()
    assert info["host"] == HOST
    assert info["config"]["Name"] == "Test Frame"
    assert "WiFiSSID" not in info["config"] and "WiFiPSWD" not in info["config"]


def test_sync_uploads_to_frame_and_lists(client: ApiHarness, frame: EmulatedFrame) -> None:
    result = client.post("/api/sync", json={"album_id": "a1"}).json()
    assert result["uploaded"] == 1 and result["failed"] == 0
    dest = result["items"][0]["dest_name"]
    assert dest in frame.state.photos  # the prepared JPEG actually reached the frame

    photos = client.get("/api/photos").json()
    assert len(photos) == 1 and photos[0]["asset_id"] == "x1"

    # Re-sync is idempotent (same content hash -> skipped).
    again = client.post("/api/sync", json={"album_id": "a1"}).json()
    assert again["skipped"] == 1 and again["uploaded"] == 0


def test_delete_photo(client: ApiHarness, frame: EmulatedFrame) -> None:
    client.post("/api/sync", json={"album_id": "a1"})
    assert client.delete("/api/photos/x1").status_code == 204
    assert client.get("/api/photos").json() == []


def test_sync_requires_target(client: ApiHarness) -> None:
    assert client.post("/api/sync", json={}).status_code == 400
