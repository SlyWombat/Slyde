"""Frame identity model + registry (issue #20)."""

from __future__ import annotations

from pathlib import Path

from conftest import HOST, PORTS
from memento_emulator import EmulatedFrame
from slyde_backend.config import Settings
from slyde_backend.frame import Frame
from slyde_backend.frames import FrameService
from slyde_backend.store import Store


def test_frame_connected_identity_is_its_host() -> None:
    f = Frame.connected("192.168.1.5", backend="memento-lan", name="Living Room")
    assert f.id == "192.168.1.5"
    assert f.address == "192.168.1.5"
    assert f.interaction == "connected"
    assert f.frame_code == ""
    assert f.name == "Living Room"


def test_frame_connected_identity_is_guid_when_reported() -> None:
    """#58: a real GUID is the stable id; the address is mutable. A null/blank GUID falls back."""
    g = "a5a82f1d-7599-4c32-b720-bf94bc5c9e3a"
    f = Frame.connected("192.168.1.5", backend="memento-lan", name="Living Room", guid=g)
    assert f.id == g and f.address == "192.168.1.5"
    z = Frame.connected(
        "192.168.1.5", backend="memento-lan", guid="00000000-0000-0000-0000-000000000000"
    )
    assert z.id == "192.168.1.5"  # emulator/blank GUID -> legacy IP identity


def test_capture_name_heals_placeholders_not_user_rename(tmp_path: Path) -> None:
    """#58: a captured Name fills a placeholder (empty / id / IP-shaped) but never a user rename."""
    store = Store(str(tmp_path / "name.db"))
    g = "guid-1"
    store.upsert_frame(Frame.connected("192.168.10.80", backend="memento-lan", guid=g))
    assert store.get_frame(g).name == ""  # not defaulted to the address
    store.capture_name(g, "Living Room")
    assert store.get_frame(g).name == "Living Room"
    store.rename_frame(g, "192.168.10.99")  # simulate an IP-shaped clobber
    store.capture_name(g, "Living Room")
    assert store.get_frame(g).name == "Living Room"  # placeholder healed
    store.rename_frame(g, "Den")  # a real user rename
    store.capture_name(g, "Living Room")
    assert store.get_frame(g).name == "Den"  # preserved


def test_store_rekey_moves_library_and_delivery(tmp_path: Path) -> None:
    """#58: migrating an IP-keyed entry onto its GUID carries the curated set + delivery queue."""
    from datetime import datetime

    from slyde_backend.delivery import enqueue

    store = Store(str(tmp_path / "rk.db"))
    store.upsert_frame(Frame.connected("10.0.0.9", backend="memento-lan"))  # legacy IP-keyed
    store.set_library("10.0.0.9", [("a1", "one.jpg")])
    enqueue(store, "10.0.0.9", "one.jpg", now=datetime(2026, 1, 1))
    assert store.get_frame_by_address("10.0.0.9").id == "10.0.0.9"

    store.rekey_frame("10.0.0.9", "guid-xyz")
    store.upsert_frame(Frame.connected("10.0.0.9", backend="memento-lan", guid="guid-xyz"))
    assert store.list_library("guid-xyz") == [("a1", "one.jpg")]  # library followed
    assert [d.frame_id for d in store.list_deliveries("guid-xyz")] == ["guid-xyz"]  # delivery too
    assert store.list_library("10.0.0.9") == []  # old key emptied


def test_discovery_keys_by_guid_and_survives_dhcp_change(tmp_path: Path) -> None:
    """#58: the registry tracks the frame's GUID; a DHCP IP change updates the address but keeps the
    identity + curated content, with no duplicate entry — and resolve_host returns the new IP."""
    import asyncio

    from memento_core.discovery import FrameInfo

    store = Store(str(tmp_path / "disc.db"))
    svc = FrameService(Settings(frame_discovery=True), store=store)
    g = "a5a82f1d-7599-4c32-b720-bf94bc5c9e3a"

    svc._backend.discover = lambda **kw: [FrameInfo(ip="192.168.10.69", name="Living Room", guid=g)]  # type: ignore[method-assign]
    asyncio.run(svc.discover_frames())
    f = store.get_frame(g)
    assert f is not None and f.address == "192.168.10.69" and f.name == "Living Room"
    store.set_library(g, [("a1", "one.jpg")])  # curate to the stable GUID

    svc._backend.discover = lambda **kw: [
        FrameInfo(ip="192.168.10.142", name="Living Room", guid=g)
    ]  # type: ignore[method-assign]
    asyncio.run(svc.discover_frames())
    f2 = store.get_frame(g)
    assert f2.id == g and f2.address == "192.168.10.142"  # same identity, new address
    assert store.list_library(g) == [("a1", "one.jpg")]  # curation followed the frame
    assert len(store.list_frames()) == 1  # no duplicate from the IP change
    assert asyncio.run(svc.resolve_host(g)) == "192.168.10.142"  # resolves to the current IP


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


def test_scan_finds_and_registers_frame(frame: EmulatedFrame, tmp_path: Path) -> None:
    """#58: the manual LAN scan TCP-probes the subnet, finds the frame on its control port, and
    reads its config to register it (by GUID where reported) — without UDP broadcast discovery."""
    import asyncio

    store = Store(str(tmp_path / "scan.db"))
    svc = FrameService(Settings(frame_scan_cidr=f"{HOST}/32"), ports=PORTS, store=store)
    found = asyncio.run(svc.scan_for_frames())
    assert len(found) == 1 and found[0].address == HOST
    assert store.get_frame_by_address(HOST) is not None  # now in the registry


def test_frame_service_served_backend_rejects_direct_ops() -> None:
    """A served backend (cloud frame) can't be driven by us connecting to it."""
    import asyncio

    from slyde_backend.frames import FrameUnavailable

    service = FrameService(Settings(frame_backend="sungale-cloud", frame_host="1.2.3.4"))
    try:
        asyncio.run(service.get_config("1.2.3.4"))
    except FrameUnavailable as exc:
        assert "served" in str(exc)
    else:
        raise AssertionError("expected FrameUnavailable for a served backend")
