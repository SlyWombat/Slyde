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
IMAGE_BASE = "/e_frame_image"


def test_multi_backend_hub_mounts_served_alongside_a_connected_primary(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """One hub: a connected primary (memento-lan) + FRAME_SERVED_BACKENDS=sungale-cloud. The served
    endpoints are mounted, so a polled eFrame registers on the same hub/registry."""
    settings = Settings(
        frame_backend="memento-lan",
        frame_served_backends="sungale-cloud",
        frame_discovery=False,
        database_url=f"sqlite:///{tmp_path}/m.db",
        cache_dir=f"{tmp_path}/cache",
    )
    harness = ServedHarness(settings)
    try:
        resp = harness.request("POST", f"{BASE}/frame/ping?serial=EF-MULTI")
        assert resp.status_code == 200 and resp.json()["code"] == "ok"
        frames = {f.id: f for f in harness.app.state.store.list_frames()}
        assert frames["EF-MULTI"].backend == "sungale-cloud"  # the eFrame registered, served
    finally:
        harness.close()


def test_connected_backend_listed_as_served_is_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(
        frame_served_backends="memento-lan",  # connected, not a served backend
        frame_discovery=False,
        database_url=f"sqlite:///{tmp_path}/m.db",
        cache_dir=f"{tmp_path}/cache",
    )
    with pytest.raises(ValueError, match="not a served backend"):
        create_app(settings)


def test_served_endpoints_are_mounted_and_identify_register_the_frame(
    served: ServedHarness,
) -> None:
    # A frame poll with its frame-code is identified and the frame is auto-registered.
    resp = served.request("POST", f"{BASE}/frame/ping", headers={"X-Frame-Code": "EFRAME-001"})
    assert resp.status_code == 200
    # Action endpoints use the observed envelope: a string ``code`` + ``message`` (not wrapped).
    assert resp.json() == {"code": "ok", "message": "ok"}

    known = served.app.state.store.list_frames()
    assert [f.id for f in known] == ["EFRAME-001"]
    assert known[0].interaction == "served" and known[0].backend == "sungale-cloud"
    assert known[0].last_seen is not None


def test_identify_by_serial_query_param_registers_by_serial(served: ServedHarness) -> None:
    # The real protocol carries the frame's identity as a query param (serial), not a header; the
    # account-wide access_token is present but must NOT be used as the frame key.
    resp = served.request(
        "POST", f"{BASE}/frame/ping?serial=AS54S44600647&access_token=deadbeef&client=aluratek"
    )
    assert resp.status_code == 200 and resp.json()["code"] == "ok"
    assert [f.id for f in served.app.state.store.list_frames()] == ["AS54S44600647"]


def test_image_library_list_returns_top_level_list(served: ServedHarness) -> None:
    # No cached images -> empty list at the top level (the observed shape, not data-wrapped).
    resp = served.request(
        "GET", f"{BASE}/image_library/list", headers={"X-Frame-Code": "EFRAME-002"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["list"] == [] and body["total"] == 0


def test_image_list_then_file_download_returns_cached_image(served: ServedHarness) -> None:
    # The frame lists its photos (path/thumbPath URLs), then downloads the full image (#8 flow).
    edited = b"\xff\xd8\xffSMART-BLUR-EDITED\xff\xd9"
    served.app.state.image_cache.put("EFRAME-CACHED", "current.jpg", edited)

    listing = served.request(
        "GET", f"{BASE}/image_library/list", headers={"X-Frame-Code": "EFRAME-CACHED"}
    ).json()
    items = listing["list"]
    assert [i["id"] for i in items] == ["current"]  # the photo id (stem), two URLs per item
    assert items[0]["path"].endswith("/e_frame_image/EFRAME-CACHED/current.bmp")
    assert items[0]["thumbPath"].endswith("/e_frame_image/EFRAME-CACHED/current.jpg")

    img = served.request("GET", urlparse(items[0]["path"]).path)  # frame downloads the full image
    assert img.status_code == 200 and img.content == edited
    assert img.headers["content-type"] == "image/bmp" and img.headers["ETag"]


def test_image_file_supports_etag_not_modified(served: ServedHarness) -> None:
    served.app.state.image_cache.put("EF-ETAG", "current.jpg", b"\xff\xd8\xffX\xff\xd9")
    url = f"{IMAGE_BASE}/EF-ETAG/current.bmp"
    first = served.request("GET", url)
    assert first.status_code == 200
    again = served.request("GET", url, headers={"If-None-Match": first.headers["ETag"]})
    assert again.status_code == 304 and not again.content


def test_frame_list_carries_full_record_and_name(served: ServedHarness) -> None:
    # frame/list returns the device record the app reads, including the carried display name.
    served.request("POST", "/api/frames/register", json={"frame_code": "AS54", "name": "Kazoo"})
    served.app.state.image_cache.put("AS54", "a.jpg", b"\xff\xd8\xffA\xff\xd9")

    rec = served.request("GET", f"{BASE}/frame/list", headers={"X-Frame-Code": "AS54"}).json()
    dev = rec["list"][0]
    assert dev["serialNumber"] == "AS54" and dev["frameUser"]["alias"] == "Kazoo"
    assert dev["album"]["id"] == "AS54" and dev["album"]["total"] == 1
    assert dev["screenModel"] and dev["setting"]["wakeUpInterval"] == "259200"


def test_album_detail_lists_photos_with_path_and_thumb(served: ServedHarness) -> None:
    # The app fetches a frame's photos via album/detail?album_id=<frame id we minted in frame/list>.
    served.app.state.image_cache.put("AS99", "p1.jpg", b"\xff\xd8\xff1\xff\xd9")
    listing = served.request("POST", f"{BASE}/album/detail?album_id=AS99").json()
    items = listing["list"]
    assert listing["total"] == 1 and items[0]["id"] == "p1"
    assert items[0]["path"].endswith("/e_frame_image/AS99/p1.bmp")
    assert items[0]["thumbPath"].endswith("/e_frame_image/AS99/p1.jpg")


def test_photo_upload_ingests_and_frame_can_pull_the_panel_bmp(served: ServedHarness) -> None:
    """#11/#8: the app pushes a photo (multipart); it becomes the frame's prepared panel BMP, and
    Slyde keeps its own canonical preview for the new (non-Immich) asset."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (200, 40, 30)).save(buf, format="JPEG")

    resp = served.request(
        "POST",
        f"{BASE}/photo/upload?client=aluratek&access_token=tok",
        data={"album_id": "EF-UP", "display_orientation": "1"},
        files={"file": ("pushed.jpg", buf.getvalue(), "image/jpeg")},
    )
    assert resp.status_code == 200
    assert resp.json() == {"code": "ok", "message": "Upload photos successfully"}

    # The pushed photo now lists for the frame and downloads as the exact panel BMP.
    listing = served.request("POST", f"{BASE}/album/detail?album_id=EF-UP").json()
    assert listing["total"] == 1
    photo_id = listing["list"][0]["id"]
    img = served.request("GET", urlparse(listing["list"][0]["path"]).path)
    assert img.status_code == 200 and img.content[:2] == b"BM" and len(img.content) == 960118

    # Slyde owns a canonical preview for the uploaded asset (served without touching Immich).
    assert served.app.state.asset_previews.get(photo_id) is not None


def test_uploaded_photo_joins_the_library_and_survives_immich_recuration(
    served: ServedHarness,
) -> None:
    """An app upload becomes a first-class library item; a later Immich 'Set library' PUT curates
    alongside it without wiping it."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (640, 480), (30, 160, 90)).save(buf, format="JPEG")
    served.request(
        "POST",
        f"{BASE}/photo/upload?album_id=EF-MIX",
        data={"album_id": "EF-MIX"},
        files={"file": ("p.jpg", buf.getvalue(), "image/jpeg")},
    )
    lib = served.request("GET", "/api/frames/EF-MIX/library").json()
    assert len(lib["items"]) == 1
    upload_dest = lib["items"][0]["dest_name"]

    # The UI now sets the Immich-curated library — the upload must remain.
    put = served.request("PUT", "/api/frames/EF-MIX/library", json=[{"asset_id": "imm1"}])
    assert put.status_code == 202
    dests = {
        i["dest_name"] for i in served.request("GET", "/api/frames/EF-MIX/library").json()["items"]
    }
    assert upload_dest in dests and "imm1.jpg" in dests  # upload kept alongside the curated photo


def test_photo_upload_without_an_image_is_rejected(served: ServedHarness) -> None:
    resp = served.request(
        "POST", f"{BASE}/photo/upload?album_id=EF-NOIMG", data={"album_id": "EF-NOIMG"}
    )
    assert resp.status_code == 400


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
    items = listing["list"]
    assert len(items) == 1
    img = served.request("GET", urlparse(items[0]["path"]).path)
    assert img.status_code == 200
    with Image.open(io.BytesIO(img.content)) as i:
        assert i.size == (160, 96)  # the image prepared to the frame's canvas


def test_login_registers_frame_and_issues_token(served: ServedHarness) -> None:
    # #8: the frame logs in (with an identifier in the body) and gets back an access_token.
    resp = served.request("POST", f"{BASE}/user/login", json={"sn": "EF-LOGIN"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "ok" and body["access_token"] == "EF-LOGIN"
    assert "EF-LOGIN" in [f.id for f in served.app.state.store.list_frames()]
    # subsequent bearer auth with that token identifies the same frame
    ping = served.request("GET", f"{BASE}/frame/ping", headers={"Authorization": "Bearer EF-LOGIN"})
    assert ping.status_code == 200 and ping.json()["code"] == "ok"


def test_setting_schedule_frame_list_endpoints_respond(served: ServedHarness) -> None:
    # setting/detail -> setting dict; schedule/list -> {list}; frame/list -> {list:[record]}.
    setting = served.request(
        "GET", f"{BASE}/setting/detail", headers={"X-Frame-Code": "EF-EP"}
    ).json()
    assert "wakeUpInterval" in setting
    sched = served.request("GET", f"{BASE}/schedule/list", headers={"X-Frame-Code": "EF-EP"}).json()
    assert sched == {"list": []}
    frames = served.request("GET", f"{BASE}/frame/list", headers={"X-Frame-Code": "EF-EP"}).json()
    assert frames["list"][0]["serialNumber"] == "EF-EP"


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
    assert len(listing["list"]) == 1
    img = served.request("GET", urlparse(listing["list"][0]["path"]).path)
    assert img.status_code == 200
    # The e-paper frame downloads the exact panel BMP we prepared (#11): 'BM', fixed 960,118 bytes.
    assert img.content[:2] == b"BM" and len(img.content) == 960118


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
