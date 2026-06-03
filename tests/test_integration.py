"""End-to-end: the real client library against the emulated frame (no physical device)."""

from __future__ import annotations

import json

from conftest import HOST, PORTS
from memento_core import FrameClient, discover
from memento_emulator import EmulatedFrame


def _client() -> FrameClient:
    return FrameClient(HOST, ports=PORTS)


def test_get_config(frame: EmulatedFrame) -> None:
    with _client() as c:
        cfg = c.get_config()
    assert cfg["Name"] == "Test Frame"
    assert cfg["SoftwareVersion"] == 6.02
    assert cfg["Width"] == 3240 and cfg["Height"] == 2160


def test_get_frame_time(frame: EmulatedFrame) -> None:
    with _client() as c:
        t = c.get_frame_time()
    assert "DateTime" in t and t["ServerTime"] == "False"


def test_upload_image_round_trips_to_frame_store(frame: EmulatedFrame) -> None:
    blob = b"\xff\xd8\xff" + b"JPEGDATA" * 5000 + b"\xff\xd9"  # ~40 KB, spans multiple chunks
    with _client() as c:
        c.upload_image(blob, "vacation01.jpg")
    assert frame.state.photos["vacation01.jpg"] == blob


def test_upload_then_current_image_name(frame: EmulatedFrame) -> None:
    with _client() as c:
        c.upload_image(b"a-tiny-image", "first.jpg")
        name = c.get_current_image_name()
    assert name == "first.jpg"


def test_delete_image(frame: EmulatedFrame) -> None:
    frame.state.add_photo("gone.jpg", b"data")
    with _client() as c:
        c.delete_image("gone.jpg")
    assert "gone.jpg" not in frame.state.photos


def test_get_albums_lists_uploaded_photos(frame: EmulatedFrame) -> None:
    with _client() as c:
        c.upload_image(b"img1", "one.jpg")
        c.upload_image(b"img2", "two.jpg")
        raw = c.get_albums()
    payload = json.loads(raw.decode())
    assert set(payload["images"]) == {"one.jpg", "two.jpg"}


def test_discovery_over_loopback(frame: EmulatedFrame) -> None:
    found = discover(host=HOST, timeout=2.0, attempts=3, ports=PORTS)
    assert any(f.name == "Test Frame" and f.ip == HOST for f in found)
