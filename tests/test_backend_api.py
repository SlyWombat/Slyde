"""Backend REST API end-to-end: FastAPI + emulator frame + a fake Immich library.

Drives the ASGI app through httpx's ASGITransport (running the real lifespan) instead of
Starlette's TestClient, which on Starlette 1.x requires the separate ``httpx2`` package.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import zipfile
from collections.abc import Iterator
from typing import ClassVar

import httpx
import pytest
from PIL import Image

from conftest import HOST
from memento_emulator import EmulatedFrame
from slyde_backend.app import create_app
from slyde_backend.config import Settings
from slyde_backend.firmware import FirmwareTrack
from slyde_backend.immich import ImmichAlbum, ImmichAsset


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
        self.app.state.folder_sync._immich_factory = FakeImmich
        self.app.state.delivery_service._immich_factory = FakeImmich
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
    # Make the emulated frame report a small canvas so per-frame-canvas prep stays fast in tests.
    frame.state.config["Width"], frame.state.config["Height"] = 64, 48
    settings = Settings(
        frame_host=HOST,
        frame_discovery=False,
        immich_base_url="http://immich.test",
        immich_api_key="k",
        database_url=f"sqlite:///{tmp_path}/memento.db",
        frame_canvas="640x480",  # fallback only; the frame's reported size wins
        frame_settle_delay=0,  # no gentleness pacing in tests (emulator handles concurrency)
    )
    harness = ApiHarness(settings)
    try:
        yield harness
    finally:
        harness.close()


F = f"/api/frames/{HOST}"


def _await_job(client: ApiHarness, started: dict, tries: int = 300) -> dict:
    """Poll a background sync job to completion (each GET pumps the harness event loop)."""
    job = started
    for _ in range(tries):
        if job["status"] != "running":
            return job
        job = client.get(f"{F}/sync/jobs/{started['id']}").json()
    raise AssertionError(f"job did not finish: {job}")


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


def test_discovery_tolerates_non_float_software_version(
    client: ApiHarness, frame: EmulatedFrame
) -> None:
    """#54: a soft-frame reporting a semver SoftwareVersion must not 500 the whole scan."""
    frame.state.config["SoftwareVersion"] = "0.1.2"  # not float-parseable
    res = client.get("/api/frames")
    assert res.status_code == 200
    me = next(f for f in res.json() if f["ip"] == HOST)
    assert me["softver"] == 0.0  # coerced safely instead of crashing the sweep


def test_registry_captures_reported_name_over_ip(client: ApiHarness) -> None:
    """#51: reading a connected frame's config captures its Name into the registry (not the IP),
    and a later 'seen' touch must not clobber it back to the id."""
    client.get(F)  # GET /frames/{host} -> get_config -> capture_name
    rows = {r["id"]: r for r in client.get("/api/frames/status").json()}
    assert rows[HOST]["name"] == "Test Frame"
    client.post(f"{F}/next")  # another op re-touches the registry
    rows = {r["id"]: r for r in client.get("/api/frames/status").json()}
    assert rows[HOST]["name"] == "Test Frame"  # still the name, not the IP


def test_user_rename_not_overridden_by_config_read(client: ApiHarness) -> None:
    """#51: capture_name only fills the default — a user rename survives later config reads."""
    client.get(F)  # capture "Test Frame"
    client.request("PATCH", F, json={"name": "Living Room"})
    client.get(F)  # a fresh config read must NOT override the user's name
    rows = {r["id"]: r for r in client.get("/api/frames/status").json()}
    assert rows[HOST]["name"] == "Living Room"


def test_frame_info_strips_wifi(client: ApiHarness) -> None:
    info = client.get(F).json()
    assert info["host"] == HOST and info["config"]["Name"] == "Test Frame"
    assert "WiFiSSID" not in info["config"] and "WiFiPSWD" not in info["config"]


def test_web_upload_becomes_a_library_item_with_folder(
    client: ApiHarness, frame: EmulatedFrame
) -> None:
    """#61 (F4): a web upload is now a first-class library item (visible in Library) that delivers
    like a curated photo — prepared to the connected frame's reported canvas, into the chosen
    folder — instead of the old folder-sync path that was invisible to the library."""
    from slyde_backend.frame import Frame

    client.app.state.store.upsert_frame(Frame.connected(HOST, backend="memento-lan"))
    frame.state.config["Width"], frame.state.config["Height"] = 80, 60

    r = client.post(
        f"{F}/upload",
        files={"files": ("snap.png", _png_bytes(), "image/png")},
        data={"folder": "Snaps"},
    )
    assert r.json()["uploaded"] == 1

    item = client.get(f"{F}/library").json()["items"][0]
    assert item["folder"] == "Snaps"  # uploaded straight into the folder
    prepared = client.app.state.image_cache.get(HOST, item["dest_name"])
    with Image.open(io.BytesIO(prepared)) as img:
        assert img.size == (80, 60)  # prepared to the reported canvas, cached for delivery


def test_render_preview_full_colour_is_jpeg(client: ApiHarness) -> None:
    """#30: preview runs an Immich asset through the frame's profile — an LCD frame -> JPEG."""
    from slyde_backend.frame import Frame

    client.app.state.store.upsert_frame(Frame.connected(HOST, backend="memento-lan"))
    res = client.get(f"{F}/preview/x1")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"
    assert res.content[:3] == b"\xff\xd8\xff"  # JPEG magic


def test_render_preview_epaper_is_palette_png(client: ApiHarness) -> None:
    """#30: an e-ink frame's preview is the palette+dither PNG (same pipeline as delivery)."""
    from slyde_backend.frame import Frame

    client.app.state.store.upsert_frame(Frame.served("EF-PRV", backend="sungale-cloud"))
    res = client.get("/api/frames/EF-PRV/preview/x1")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_preview_unknown_frame_404(client: ApiHarness) -> None:
    assert client.get("/api/frames/nope/preview/x1").status_code == 404


def test_asset_preview_is_generated_persisted_and_frame_independent(client: ApiHarness) -> None:
    # Slyde renders its own canonical preview from Immich once, with no frame involved at all.
    res = client.get("/api/assets/x1/preview")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg" and res.content[:3] == b"\xff\xd8\xff"
    # ...and keeps it in its own store, keyed by asset (not by any frame).
    assert client.app.state.asset_previews.get("x1") is not None


def test_asset_preview_served_from_slyde_store_even_when_immich_unavailable(
    client: ApiHarness,
) -> None:
    client.get("/api/assets/x1/preview")  # populate Slyde's preview store

    class _BoomImmich:  # Immich must NOT be touched once Slyde has its own preview
        async def __aenter__(self) -> _BoomImmich:
            raise AssertionError("Immich should not be called on a preview cache hit")

        async def __aexit__(self, *exc: object) -> None:
            return None

    client.app.state.immich_factory = _BoomImmich
    res = client.get("/api/assets/x1/preview")
    assert res.status_code == 200 and res.content[:3] == b"\xff\xd8\xff"


def test_sync_job_404_for_unknown_id(client: ApiHarness) -> None:
    assert client.get(f"{F}/sync/jobs/nope").status_code == 404


def test_immich_albums(client: ApiHarness) -> None:
    albums = client.get("/api/immich/albums").json()
    assert albums == [{"id": "a1", "name": "Trips", "asset_count": 1}]


def test_firmware_serve_verifies_and_404s(client: ApiHarness) -> None:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("VERSION", "2.0.0")
    blob = data.getvalue()
    svc = client.app.state.firmware
    svc._registry["memento-softframe"] = FirmwareTrack(
        "memento-softframe", "2.0.0", "http://x/b.zip", hashlib.md5(blob).hexdigest()
    )
    svc._cache["memento-softframe"] = blob  # avoid a real network fetch
    ok = client.get("/api/firmware/serve/memento-softframe")
    assert ok.status_code == 200 and ok.content == blob
    assert client.get("/api/firmware/serve/nope").status_code == 404


def test_frame_update_sends_trigger(client: ApiHarness, frame: EmulatedFrame) -> None:
    client.app.state.firmware._registry["memento-softframe"] = FirmwareTrack(
        "memento-softframe", "2.0.0", "http://x/b.zip", "abc123"
    )
    res = client.post(f"{F}/update").json()
    assert res["sent"] and res["track"] == "memento-softframe"
    assert "/api/firmware/serve/memento-softframe" in res["url"]
    assert frame.last_update is not None and frame.last_update[1] == "abc123"


def test_frame_update_409_without_firmware(client: ApiHarness) -> None:
    assert client.post(f"{F}/update").status_code == 409


def test_spa_cache_headers(tmp_path) -> None:  # type: ignore[no-untyped-def]
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><html></html>")
    (static / "assets" / "app-abc123.js").write_text("console.log(1)")
    settings = Settings(
        frame_discovery=False,
        immich_base_url="http://immich.test",
        immich_api_key="k",
        database_url=f"sqlite:///{tmp_path}/spa.db",
        static_dir=str(static),
    )
    harness = ApiHarness(settings)
    try:
        # index.html must always revalidate so a deploy is picked up without a hard refresh.
        assert harness.get("/").headers["cache-control"] == "no-cache"
        # content-hashed assets can be cached forever.
        assert "immutable" in harness.get("/assets/app-abc123.js").headers["cache-control"]
    finally:
        harness.close()


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


def test_keep_in_sync_binding_reconciles_into_the_library(
    client: ApiHarness, frame: EmulatedFrame
) -> None:
    """#62: binding a Library FOLDER to an Immich album keeps that folder's source='sync' rows
    mirrored to the album (add new / drop departed), on the delivery queue (not the device)."""
    from slyde_backend.frame import Frame

    client.app.state.store.upsert_frame(Frame.connected(HOST, backend="memento-lan"))
    client.app.state.folder_sync._immich_factory = MutableImmich

    def cats() -> set[str]:
        items = client.get(f"{F}/library").json()["items"]
        return {i["asset_id"] for i in items if i["folder"] == "Cats" and i["dest_name"]}

    def bind() -> dict:
        started = client.post(
            f"{F}/subscriptions", json={"album_id": "a1", "target_album": "Cats"}
        ).json()
        job = _await_job(client, started)
        assert job["status"] == "done", job
        return job["result"]

    # Bind: the folder's library rows mirror the Immich album.
    MutableImmich.assets = [_cat(1)]
    bind()
    assert cats() == {"c1"}
    subs = client.get(f"{F}/subscriptions").json()
    assert len(subs) == 1 and subs[0]["target_album"] == "Cats"

    # A new Immich item is added to the folder.
    MutableImmich.assets = [_cat(1), _cat(2)]
    bind()
    assert cats() == {"c1", "c2"}

    # A removed Immich item is dropped from the folder.
    MutableImmich.assets = [_cat(2)]
    assert bind()["removed"] == 1
    assert cats() == {"c2"}

    # Unbinding stops syncing (the library rows it placed remain).
    assert client.delete(f"{F}/subscriptions/a1").status_code == 204
    assert client.get(f"{F}/subscriptions").json() == []
