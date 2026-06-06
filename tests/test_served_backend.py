"""Served-backend mounting, identification, and delivery (issue #22).

Drives the real ASGI app (sungale-cloud backend) the way a polling frame would, asserting the
manager mounts the frame's endpoints, identifies + registers the frame, and serves it an image.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from urllib.parse import urlparse

import httpx
import pytest

from slyde_backend.app import create_app
from slyde_backend.config import Settings
from slyde_backend.frame import Frame
from slyde_backend.serving import resolve_or_register_served_frame
from slyde_backend.store import Store


class ServedHarness:
    def __init__(self, settings: Settings) -> None:
        self._loop = asyncio.new_event_loop()
        self.app = create_app(settings)
        self._lifespan = self.app.router.lifespan_context(self.app)
        self._loop.run_until_complete(self._lifespan.__aenter__())
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://cloud"
        )

    def request(self, method: str, url: str, **kw: object) -> httpx.Response:
        return self._loop.run_until_complete(self._client.request(method, url, **kw))  # type: ignore[arg-type]

    def close(self) -> None:
        self._loop.run_until_complete(self._client.aclose())
        self._loop.run_until_complete(self._lifespan.__aexit__(None, None, None))
        self._loop.close()


@pytest.fixture
def served(tmp_path) -> Iterator[ServedHarness]:  # type: ignore[no-untyped-def]
    settings = Settings(
        frame_backend="sungale-cloud",
        frame_discovery=False,
        database_url=f"sqlite:///{tmp_path}/memento.db",
        cache_dir=f"{tmp_path}/cache",
    )
    harness = ServedHarness(settings)
    try:
        yield harness
    finally:
        harness.close()


BASE = "/xiaowooya/api/v1"


def test_served_endpoints_are_mounted_and_identify_register_the_frame(
    served: ServedHarness,
) -> None:
    # A frame poll with its frame-code is identified and the frame is auto-registered.
    resp = served.request("POST", f"{BASE}/frame/ping", headers={"X-Frame-Code": "EFRAME-001"})
    assert resp.status_code == 200
    assert resp.json() == {"code": 0, "msg": "ok", "data": {"frame_id": "EFRAME-001"}}

    known = served.app.state.store.list_frames()
    assert [f.id for f in known] == ["EFRAME-001"]
    assert known[0].interaction == "served" and known[0].backend == "sungale-cloud"
    assert known[0].last_seen is not None


def test_image_library_list_returns_json_envelope(served: ServedHarness) -> None:
    # No cached images -> empty list in the vendor envelope (#8 endpoint shape).
    resp = served.request(
        "GET", f"{BASE}/image_library/list", headers={"X-Frame-Code": "EFRAME-002"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0 and body["data"]["list"] == [] and body["data"]["total"] == 0


def test_image_list_then_file_download_returns_cached_image(served: ServedHarness) -> None:
    # The frame lists its photos (URLs), then downloads each from the file endpoint (#8 flow).
    edited = b"\xff\xd8\xffSMART-BLUR-EDITED\xff\xd9"
    served.app.state.image_cache.put("EFRAME-CACHED", "current.jpg", edited)

    listing = served.request(
        "GET", f"{BASE}/image_library/list", headers={"X-Frame-Code": "EFRAME-CACHED"}
    ).json()
    items = listing["data"]["list"]
    assert [i["id"] for i in items] == ["current.jpg"]

    img = served.request("GET", urlparse(items[0]["url"]).path)  # the frame downloads the URL
    assert img.status_code == 200 and img.content == edited


def test_full_served_loop_curate_publish_then_frame_downloads(served: ServedHarness) -> None:
    """#8/#23 end-to-end: curate -> publish (e-ink prepare + cache) -> list -> download."""
    import io

    from PIL import Image

    from slyde_backend.library import LibraryItem
    from slyde_backend.processing import ProcessingProfile

    class FakeImmich:
        async def asset_bytes(self, asset_id: str, size: str = "preview") -> bytes:
            buf = io.BytesIO()
            Image.new("RGB", (200, 100), (40, 80, 160)).save(buf, format="JPEG")
            return buf.getvalue()

    code = "EFRAME-LOOP"
    library = served.app.state.library
    library.set_desired(code, [LibraryItem("asset-1", "current.jpg")])
    served._loop.run_until_complete(
        library.publish(code, FakeImmich(), profile=ProcessingProfile(canvas=(160, 96)))
    )

    listing = served.request(
        "GET", f"{BASE}/image_library/list", headers={"X-Frame-Code": code}
    ).json()
    items = listing["data"]["list"]
    assert len(items) == 1
    img = served.request("GET", urlparse(items[0]["url"]).path)
    assert img.status_code == 200
    with Image.open(io.BytesIO(img.content)) as i:
        assert i.size == (160, 96)  # the image prepared to the frame's canvas


def test_login_registers_frame_and_issues_token(served: ServedHarness) -> None:
    # #8: the frame logs in (with an identifier in the body) and gets a token == its frame-code.
    resp = served.request("POST", f"{BASE}/user/login", json={"sn": "EF-LOGIN"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0 and body["data"]["token"] == "EF-LOGIN"
    assert "EF-LOGIN" in [f.id for f in served.app.state.store.list_frames()]
    # subsequent bearer auth with that token identifies the same frame
    ping = served.request("GET", f"{BASE}/frame/ping", headers={"Authorization": "Bearer EF-LOGIN"})
    assert ping.status_code == 200 and ping.json()["data"]["frame_id"] == "EF-LOGIN"


def test_setting_schedule_frame_list_endpoints_respond(served: ServedHarness) -> None:
    for ep in ("setting/detail", "schedule/list", "frame/list"):
        r = served.request("GET", f"{BASE}/{ep}", headers={"X-Frame-Code": "EF-EP"})
        assert r.status_code == 200 and r.json()["code"] == 0


def test_frames_status_is_frame_agnostic(served: ServedHarness) -> None:
    """#24: read-only status from the registry + delivery queue, no connection to the frame."""
    from datetime import datetime

    from slyde_backend.delivery import enqueue

    # a served frame registers itself by polling
    served.request("POST", f"{BASE}/frame/ping", headers={"X-Frame-Code": "EF-STAT"})
    # and has some queued deliveries
    store = served.app.state.store
    enqueue(store, "EF-STAT", "a.jpg", now=datetime(2026, 1, 1))
    store.mark_delivered(enqueue(store, "EF-STAT", "b.jpg", now=datetime(2026, 1, 1)))

    resp = served.request("GET", "/api/frames/status")
    assert resp.status_code == 200
    rows = {r["id"]: r for r in resp.json()}
    assert "EF-STAT" in rows
    row = rows["EF-STAT"]
    assert row["interaction"] == "served" and row["backend"] == "sungale-cloud"
    assert row["last_seen"] is not None
    assert row["deliveries"] == {"pending": 1, "delivered": 1, "failed": 0}


def test_curation_endpoint_drives_delivery_then_frame_pulls(served: ServedHarness) -> None:
    """#25/#26 live wiring through the real app: PUT a frame's library -> the backend queues +
    delivers (prepares + caches) -> the frame's poll returns its prepared image."""
    import io

    from PIL import Image

    class FakeImmich:
        async def __aenter__(self) -> FakeImmich:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def asset_bytes(self, asset_id: str, size: str = "preview") -> bytes:
            buf = io.BytesIO()
            Image.new("RGB", (200, 100), (40, 80, 160)).save(buf, format="JPEG")
            return buf.getvalue()

    served.request("POST", f"{BASE}/frame/ping", headers={"X-Frame-Code": "EF-WIRE"})
    served.app.state.delivery_service._immich_factory = FakeImmich  # no real Immich in tests

    put = served.request(
        "PUT",
        "/api/frames/EF-WIRE/library",
        json=[{"asset_id": "a1", "dest_name": "current"}],
    )
    assert put.status_code == 202
    assert put.json() == {"queued": 1}  # returns immediately; delivery runs in the background (#50)

    # delivery happens off the request path (a fire-and-forget background drain) — once it lands the
    # frame can pull its prepared image, with no UI involvement.
    listing = served.request(
        "GET", f"{BASE}/image_library/list", headers={"X-Frame-Code": "EF-WIRE"}
    ).json()
    assert len(listing["data"]["list"]) == 1
    img = served.request("GET", urlparse(listing["data"]["list"][0]["url"]).path)
    assert img.status_code == 200
    assert img.content[:8] == b"\x89PNG\r\n\x1a\n"  # the e-paper-prepared PNG, downloaded


def test_library_readback_and_detail(served: ServedHarness) -> None:
    """#28: set a library by asset-id alone, read it back with delivery state, and get detail."""
    code = "EF-LIB"
    served.request("POST", f"{BASE}/frame/ping", headers={"X-Frame-Code": code})  # register frame
    put = served.request(
        "PUT", f"/api/frames/{code}/library", json=[{"asset_id": "a1"}, {"asset_id": "a2"}]
    )
    assert put.status_code == 202

    lib = served.request("GET", f"/api/frames/{code}/library").json()
    assert [i["asset_id"] for i in lib["items"]] == ["a1", "a2"]
    assert [i["dest_name"] for i in lib["items"]] == ["a1.jpg", "a2.jpg"]  # derived from asset id
    assert all(i["state"] in {"pending", "delivered", "failed", "unknown"} for i in lib["items"])
    assert "pending" in lib["deliveries"]

    detail = served.request("GET", f"/api/frames/{code}/detail").json()
    assert detail["interaction"] == "served" and detail["backend"] == "sungale-cloud"
    assert detail["capabilities"]["color_model"] == "epaper"  # e-ink panel


def test_register_served_frame_before_first_poll(served: ServedHarness) -> None:
    """#29: onboard a cloud frame by code -> it shows in status before it has ever polled."""
    resp = served.request(
        "POST", "/api/frames/register", json={"frame_code": "EF-NEW", "name": "Kitchen"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "EF-NEW" and body["name"] == "Kitchen" and body["interaction"] == "served"
    assert body["backend"] == "sungale-cloud"  # the sole served backend, chosen by default

    rows = {r["id"]: r for r in served.request("GET", "/api/frames/status").json()}
    assert "EF-NEW" in rows and rows["EF-NEW"]["name"] == "Kitchen"  # visible without a poll

    again = served.request("POST", "/api/frames/register", json={"frame_code": "EF-NEW"})
    assert again.status_code == 201  # idempotent
    ids = [f.id for f in served.app.state.store.list_frames()]
    assert ids.count("EF-NEW") == 1 and again.json()["name"] == "Kitchen"  # name kept


def test_rename_frame_sets_registry_name(served: ServedHarness) -> None:
    """#55: PATCH /frames/{id} renames any frame in the registry; 404 for unknown."""
    served.request("POST", "/api/frames/register", json={"frame_code": "EF-REN", "name": "Old"})
    res = served.request("PATCH", "/api/frames/EF-REN", json={"name": "Living Room"})
    assert res.status_code == 200 and res.json()["name"] == "Living Room"
    rows = {r["id"]: r for r in served.request("GET", "/api/frames/status").json()}
    assert rows["EF-REN"]["name"] == "Living Room"
    assert served.request("PATCH", "/api/frames/ghost", json={"name": "X"}).status_code == 404


def test_metrics_rollup(served: ServedHarness) -> None:
    """#55: /api/metrics returns frame + delivery rollups and firmware info."""
    from datetime import datetime

    from slyde_backend.delivery import enqueue

    served.request("POST", "/api/frames/register", json={"frame_code": "EF-M1"})
    store = served.app.state.store
    enqueue(store, "EF-M1", "a.jpg", now=datetime(2026, 1, 1))
    store.mark_delivered(enqueue(store, "EF-M1", "b.jpg", now=datetime(2026, 1, 1)))

    m = served.request("GET", "/api/metrics").json()
    assert m["frames"]["total"] >= 1 and m["frames"]["served"] >= 1
    assert m["deliveries"]["pending"] >= 1 and m["deliveries"]["delivered"] >= 1
    assert m["firmware_track"] == "memento-softframe" and "version" in m


def test_library_404_for_unknown_frame(served: ServedHarness) -> None:
    """#53: the library GET 404s for an unknown/detached frame, mirroring DELETE (not 200-empty)."""
    assert served.request("GET", "/api/frames/ghost/library").status_code == 404


def test_deregister_frame_purges_registry_queue_and_library(served: ServedHarness) -> None:
    """Deregister drops the frame + everything keyed to it; the device itself is untouched."""
    code = "EF-GONE"
    served.request("POST", "/api/frames/register", json={"frame_code": code})
    served.request(
        "PUT", f"/api/frames/{code}/library", json=[{"asset_id": "a1"}]
    )  # queue + library
    store = served.app.state.store
    assert store.list_library(code) and store.list_deliveries(code)  # there is state to purge

    resp = served.request("DELETE", f"/api/frames/{code}")
    assert resp.status_code == 204
    assert code not in [f.id for f in store.list_frames()]  # gone from the registry/status
    assert store.list_library(code) == []  # curated set purged
    assert store.list_deliveries(code) == []  # delivery queue purged

    assert served.request("DELETE", f"/api/frames/{code}").status_code == 404  # already gone


def test_unidentified_frame_is_rejected(served: ServedHarness) -> None:
    resp = served.request("POST", f"{BASE}/frame/ping")  # no frame-code
    assert resp.status_code == 401


def test_resolve_or_register_is_idempotent_and_touches(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = Store(str(tmp_path / "r.db"))
    f1 = resolve_or_register_served_frame(store, "sungale-cloud", "CODE")
    assert isinstance(f1, Frame) and f1.interaction == "served" and f1.last_seen is not None
    f2 = resolve_or_register_served_frame(store, "sungale-cloud", "CODE")
    assert f2.id == f1.id
    assert [f.id for f in store.list_frames()] == ["CODE"]  # not duplicated
