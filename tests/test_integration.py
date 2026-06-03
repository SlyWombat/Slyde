"""End-to-end: the real client library against the emulated frame (no physical device)."""

from __future__ import annotations

from conftest import HOST, PORTS
from memento_core import FrameClient, discover
from memento_core.albums import ALBUM_PHOTOS
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


def test_get_album_data_includes_uploaded(frame: EmulatedFrame) -> None:
    with _client() as c:
        c.upload_image(b"img1", "one.jpg")
        c.upload_image(b"img2", "two.jpg")
        data = c.get_album_data()
    photos = data.get(ALBUM_PHOTOS)
    assert photos is not None
    assert set(photos.images) == {"one.jpg", "two.jpg"}


def test_thumbnails_list_and_fetch(frame: EmulatedFrame) -> None:
    with _client() as c:
        c.upload_image(b"hello-jpeg-bytes", "pic.jpg")
        listing = c.get_thumbnails_list()
        thumb = c.get_thumbnail("pic.jpg")
    assert "pic.jpg" in [name for name, _md5 in listing]
    assert thumb.startswith(b"\x89PNG")


def test_create_album_and_send_round_trips(frame: EmulatedFrame) -> None:
    with _client() as c:
        c.upload_image(b"x", "holiday1.jpg")
        data = c.get_album_data()
        data.add_album("Holidays")
        data.add_image("Holidays", "holiday1.jpg")
        c.send_album_data(data)
        reread = c.get_album_data()
    holidays = reread.get("Holidays")
    assert holidays is not None and "holiday1.jpg" in holidays.images


def test_discovery_over_loopback(frame: EmulatedFrame) -> None:
    found = discover(host=HOST, timeout=2.0, attempts=3, ports=PORTS)
    assert any(f.name == "Test Frame" and f.ip == HOST for f in found)
