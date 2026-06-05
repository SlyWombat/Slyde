"""Frame identity model + registry (issue #20)."""

from __future__ import annotations

from pathlib import Path

from conftest import HOST, PORTS
from memento_backend.config import Settings
from memento_backend.frame import Frame
from memento_backend.frames import FrameService
from memento_backend.store import Store
from memento_emulator import EmulatedFrame


def test_frame_connected_identity_is_its_host() -> None:
    f = Frame.connected("192.168.1.5", backend="memento-lan", name="Living Room")
    assert f.id == "192.168.1.5"
    assert f.address == "192.168.1.5"
    assert f.interaction == "connected"
    assert f.frame_code == ""
    assert f.name == "Living Room"


def test_frame_served_identity_is_its_frame_code() -> None:
    f = Frame.served("ABC123", backend="sungale-cloud")
    assert f.id == "ABC123"
    assert f.frame_code == "ABC123"
    assert f.interaction == "served"
    assert f.address == ""  # never connected to


def test_store_registry_crud(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "reg.db"))
    assert store.list_frames() == []

    store.upsert_frame(Frame.connected("10.0.0.5", backend="memento-lan", name="Frame A"))
    store.upsert_frame(Frame.served("CODE9", backend="sungale-cloud", name="Frame B"))
    frames = {f.id: f for f in store.list_frames()}
    assert set(frames) == {"10.0.0.5", "CODE9"}
    assert frames["10.0.0.5"].interaction == "connected"
    assert frames["CODE9"].interaction == "served"

    # touch sets last_seen; upsert preserves it (COALESCE) when re-recorded without a timestamp.
    assert store.get_frame("10.0.0.5").last_seen is None
    store.touch_frame("10.0.0.5")
    seen = store.get_frame("10.0.0.5").last_seen
    assert seen is not None
    store.upsert_frame(Frame.connected("10.0.0.5", backend="memento-lan", name="Renamed"))
    again = store.get_frame("10.0.0.5")
    assert again.name == "Renamed" and again.last_seen == seen  # last_seen preserved

    assert store.delete_frame("CODE9") is True
    assert store.get_frame("CODE9") is None


def test_frame_service_registers_connected_frame_on_contact(
    frame: EmulatedFrame, tmp_path: Path
) -> None:
    """Reaching a frame records it in the registry (transport-independent identity)."""
    store = Store(str(tmp_path / "svc.db"))
    settings = Settings(frame_host=HOST)
    service = FrameService(settings, ports=PORTS, store=store)

    import asyncio

    cfg = asyncio.run(service.get_config(HOST))
    assert cfg["Name"] == "Test Frame"

    known = service.list_known_frames()
    assert [f.id for f in known] == [HOST]
    assert known[0].interaction == "connected" and known[0].last_seen is not None


def test_frame_service_served_backend_rejects_direct_ops() -> None:
    """A served backend (cloud frame) can't be driven by us connecting to it."""
    import asyncio

    from memento_backend.frames import FrameUnavailable

    service = FrameService(Settings(frame_backend="sungale-cloud", frame_host="1.2.3.4"))
    try:
        asyncio.run(service.get_config("1.2.3.4"))
    except FrameUnavailable as exc:
        assert "served" in str(exc)
    else:
        raise AssertionError("expected FrameUnavailable for a served backend")
