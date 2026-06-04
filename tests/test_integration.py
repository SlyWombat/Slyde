"""End-to-end: the real client library against the emulated frame (no physical device)."""

from __future__ import annotations

import time

from conftest import HOST, PORTS
from memento_core import FrameClient, Setup, discover
from memento_core.albums import ALBUM_PHOTOS
from memento_core.protocol import Ports
from memento_emulator import EmulatedFrame, FrameState


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


def test_config_then_transfer_on_same_frame(frame: EmulatedFrame) -> None:
    # Mirrors the UI: a control-only call (config) followed by a file-transfer call (albums)
    # via separate connections to the same frame. Regression for the lazy-file-connect fix —
    # an eagerly-opened-but-unused file socket used to poison the emulator's pairing queue.
    with _client() as c:
        c.get_config()
    with _client() as c:
        data = c.get_album_data()
    assert data.get(ALBUM_PHOTOS) is not None


def test_download_image_round_trips_back(frame: EmulatedFrame) -> None:
    blob = b"\xff\xd8\xff" + b"ORIGINAL" * 4000 + b"\xff\xd9"
    with _client() as c:
        c.upload_image(blob, "orig.jpg")
        got = c.download_image("orig.jpg")
    assert got == blob


def test_display_image_sets_current(frame: EmulatedFrame) -> None:
    frame.state.add_photo("one.jpg", b"a")
    frame.state.add_photo("two.jpg", b"b")
    with _client() as c:
        c.display_image("two.jpg")
        assert c.get_current_image_name() == "two.jpg"


def test_set_current_album_selects_and_filters(frame: EmulatedFrame) -> None:
    frame.state.add_photo("trip1.jpg", b"a")
    data = frame.state.albums
    data.add_album("Trip")
    data.add_image("Trip", "trip1.jpg")
    with _client() as c:
        c.set_current_album("Trip")
        album = c.get_current_album()
    assert frame.state.current_album == "Trip"
    assert isinstance(album, dict) and album["Images"] == ["trip1.jpg"]


def test_change_orientation_keeps_dimensions_consistent(frame: EmulatedFrame) -> None:
    with _client() as c:
        c.change_setup(Setup.ChangeOrientation, {"Orientation": "Portrait"})
        cfg = c.get_config()
    assert cfg["Orientation"] == "Portrait" and cfg["PortraitMode"] is True
    assert cfg["Width"] < cfg["Height"]  # portrait swaps the canvas


def test_factory_reset_wipes_state(frame: EmulatedFrame) -> None:
    frame.state.add_photo("doomed.jpg", b"data")
    frame.state.update_config({"DisplayTime": 5})
    with _client() as c:
        c.factory_reset()
    assert frame.state.photos == {}
    assert frame.state.config["DisplayTime"] == 60  # back to default


def test_trigger_update_records_request(frame: EmulatedFrame) -> None:
    with _client() as c:
        c.trigger_update("http://mgr/api/firmware/serve/memento-softframe", "abc123")
    assert frame.last_update == ("http://mgr/api/firmware/serve/memento-softframe", "abc123")


def test_trigger_update_invokes_updater() -> None:
    got: list[tuple[str, str]] = []
    ports = Ports(broadcast=3015, broadcast_response=3016, control=3017, file=3018)
    emu = EmulatedFrame(
        FrameState(name="U", ip=HOST),
        host=HOST,
        ports=ports,
        on_update=lambda url, md5: got.append((url, md5)),
    ).start()
    time.sleep(0.1)
    try:
        with FrameClient(HOST, ports=ports) as c:
            c.trigger_update("http://mgr/serve/x", "deadbeef")
        for _ in range(50):  # the updater runs on a spawned thread
            if got:
                break
            time.sleep(0.02)
        assert got == [("http://mgr/serve/x", "deadbeef")]
    finally:
        emu.stop()


def test_discovery_over_loopback(frame: EmulatedFrame) -> None:
    found = discover(host=HOST, timeout=2.0, attempts=3, ports=PORTS)
    assert any(f.name == "Test Frame" and f.ip == HOST for f in found)
