"""Served-backend mounting, identification, and delivery (issue #22).

Drives the real ASGI app (sungale-cloud backend) the way a polling frame would, asserting the
manager mounts the frame's endpoints, identifies + registers the frame, and serves it an image.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import httpx
import pytest

from memento_backend.app import create_app
from memento_backend.config import Settings
from memento_backend.frame import Frame
from memento_backend.serving import resolve_or_register_served_frame
from memento_backend.store import Store


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
