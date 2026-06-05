"""Served-backend mounting, identification, and delivery (issue #22).

Drives the real ASGI app (sungale-cloud backend) the way a polling frame would, asserting the
manager mounts the frame's endpoints, identifies + registers the frame, and serves it an image.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

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
    assert resp.json() == {"status": "ok", "frame": "EFRAME-001"}

    known = served.app.state.store.list_frames()
    assert [f.id for f in known] == ["EFRAME-001"]
    assert known[0].interaction == "served" and known[0].backend == "sungale-cloud"
    assert known[0].last_seen is not None


def test_served_image_poll_returns_an_image(served: ServedHarness) -> None:
    resp = served.request(
        "GET", f"{BASE}/image_library/list", headers={"X-Frame-Code": "EFRAME-002"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content[:2] == b"\xff\xd8"  # JPEG magic — a real (placeholder) image was served


def test_served_poll_returns_the_cached_prepared_image(served: ServedHarness) -> None:
    # The hub has a prepared (edited) image cached for this frame, ready to send (#25).
    edited = b"\xff\xd8\xffSMART-BLUR-EDITED\xff\xd9"
    served.app.state.image_cache.put("EFRAME-CACHED", "current.jpg", edited)

    resp = served.request(
        "GET", f"{BASE}/image_library/list", headers={"X-Frame-Code": "EFRAME-CACHED"}
    )
    assert resp.status_code == 200
    assert resp.content == edited  # the cached edited image, not the placeholder


def test_full_served_loop_curate_publish_then_frame_pulls(served: ServedHarness) -> None:
    """#23 end-to-end: curate a desired set -> publish (prepare+cache) -> the frame's poll
    returns its prepared image, all through the real app."""
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

    resp = served.request("GET", f"{BASE}/image_library/list", headers={"X-Frame-Code": code})
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(resp.content)) as img:
        assert img.size == (160, 96)  # served the image prepared to the frame's canvas


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
