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
    # Make the emulated frame report a small canvas so per-frame-canvas prep stays fast in tests.
    frame.state.config["Width"], frame.state.config["Height"] = 64, 48
    settings = Settings(
        frame_host=HOST,
        frame_discovery=False,
        immich_base_url="http://immich.test",
        immich_api_key="k",
        database_url=f"sqlite:///{tmp_path}/memento.db",
        frame_canvas="640x480",  # fallback only; the frame's reported size wins
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


def test_prepare_uses_frame_reported_canvas(client: ApiHarness, frame: EmulatedFrame) -> None:
    # Prepared image matches the frame's *reported* resolution, not the FRAME_CANVAS default.
    frame.state.config["Width"], frame.state.config["Height"] = 80, 60
    result = client.post(
        f"{F}/upload", files={"files": ("snap.png", _png_bytes(), "image/png")}
    ).json()
    dest = result["items"][0]["dest_name"]
    with Image.open(io.BytesIO(frame.state.photos[dest])) as prepared:
        assert prepared.size == (80, 60)


def test_direct_upload_and_thumbnail(client: ApiHarness, frame: EmulatedFrame) -> None:
    files = {"files": ("snap.png", _png_bytes(), "image/png")}
    result = client.post(f"{F}/upload", files=files, data={"target_album": "Direct"}).json()
    assert result["uploaded"] == 1
    dest = result["items"][0]["dest_name"]
    thumb = client.get(f"{F}/thumbnail/{dest}")
    assert thumb.status_code == 200 and thumb.content.startswith(b"\x89PNG")


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


def test_delete_photo(client: ApiHarness, frame: EmulatedFrame) -> None:
    client.post(f"{F}/sync", json={"album_id": "a1"})
    dest = next(iter(frame.state.photos))
    assert client.delete(f"{F}/photos/{dest}").status_code == 204
    assert dest not in frame.state.photos


def test_current_image_after_sync(client: ApiHarness, frame: EmulatedFrame) -> None:
    client.post(f"{F}/sync", json={"album_id": "a1"})
    body = client.get(f"{F}/current").json()
    assert body["image"] in frame.state.photos


def test_sync_job_runs_in_background(client: ApiHarness, frame: EmulatedFrame) -> None:
    started = client.post(f"{F}/sync/jobs", json={"album_id": "a1"}).json()
    assert started["status"] in {"running", "done"}
    job = _await_job(client, started)
    assert job["status"] == "done"
    assert job["result"]["uploaded"] == 1
    assert job["result"]["total"] >= 1


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


def test_sync_streams_in_chunks_with_prepared_progress(
    client: ApiHarness, frame: EmulatedFrame
) -> None:
    """#57: a multi-photo album syncs in bounded chunks; total/prepared/uploaded each reflect every
    photo (so the UI never sits frozen at 0/N), and all land across multiple chunks."""
    client.app.state.sync._immich_factory = MutableImmich
    client.app.state.settings.sync_chunk_size = 1  # force one chunk per photo
    MutableImmich.assets = [_cat(1), _cat(2), _cat(3)]
    res = client.post(f"{F}/sync", json={"album_id": "a1", "target_album": "Stream"}).json()
    assert res["total"] == 3 and res["prepared"] == 3
    assert res["uploaded"] == 3 and res["failed"] == 0
    assert len([i for i in res["items"] if i["status"] == "uploaded"]) == 3


def _cat(n: int) -> ImmichAsset:
    return ImmichAsset(id=f"c{n}", file_name=f"cat{n}.jpg", type="IMAGE")


def _cats_count(client: ApiHarness) -> int:
    albums = client.get(f"{F}/albums").json()
    cats = next((a for a in albums if a["name"] == "Cats"), None)
    return cats["image_count"] if cats else 0


def test_subscription_mirror_lifecycle(client: ApiHarness, frame: EmulatedFrame) -> None:
    client.app.state.sync._immich_factory = MutableImmich

    def subscribe() -> dict:
        started = client.post(
            f"{F}/subscriptions", json={"album_id": "a1", "target_album": "Cats"}
        ).json()
        job = _await_job(client, started)
        assert job["status"] == "done", job
        return job["result"]

    # Subscribe: mirrors the Immich album onto a frame album and syncs now (in the background).
    MutableImmich.assets = [_cat(1)]
    assert subscribe()["uploaded"] == 1
    subs = client.get(f"{F}/subscriptions").json()
    assert len(subs) == 1 and subs[0]["target_album"] == "Cats"
    assert _cats_count(client) == 1

    # A new Immich item is mirrored in (existing one is skipped, not re-uploaded).
    MutableImmich.assets = [_cat(1), _cat(2)]
    res = subscribe()
    assert res["uploaded"] == 1 and res["skipped"] == 1
    assert _cats_count(client) == 2

    # A removed Immich item is dropped from the frame album (1:1 mirror).
    MutableImmich.assets = [_cat(2)]
    assert subscribe()["removed"] == 1
    assert _cats_count(client) == 1

    # Unsubscribe stops syncing.
    assert client.delete(f"{F}/subscriptions/a1").status_code == 204
    assert client.get(f"{F}/subscriptions").json() == []
