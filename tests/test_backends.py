"""Frame-backend abstraction: registry, capabilities, and LAN-backend conformance."""

from __future__ import annotations

import pytest

from conftest import HOST, PORTS
from memento_emulator import EmulatedFrame
from slyde_backend.backends import (
    ConnectedFrameBackend,
    MementoLanBackend,
    ServedFrameBackend,
    SungaleCloudBackend,
    SwitchBotBackend,
    available_backends,
    get_backend,
)


def test_registry_resolves_known_backends() -> None:
    assert available_backends() == ["memento-lan", "sungale-cloud", "switchbot"]
    assert isinstance(get_backend("memento-lan"), MementoLanBackend)
    assert isinstance(get_backend("sungale-cloud"), SungaleCloudBackend)
    assert isinstance(get_backend("switchbot"), SwitchBotBackend)


def test_unknown_backend_raises_with_helpful_message() -> None:
    with pytest.raises(ValueError, match="unknown frame backend 'nope'"):
        get_backend("nope")


def test_backend_capabilities_describe_interaction_and_transport() -> None:
    assert MementoLanBackend.capabilities.interaction == "connected"
    assert MementoLanBackend.capabilities.transport == "lan"
    assert MementoLanBackend.capabilities.discovery is True
    assert MementoLanBackend.capabilities.ota is True
    # Cloud frames poll us (served), aren't LAN-discoverable, OTA not characterized yet.
    assert SungaleCloudBackend.capabilities.interaction == "served"
    assert SungaleCloudBackend.capabilities.transport == "cloud"
    assert SungaleCloudBackend.capabilities.discovery is False
    # SwitchBot: we push to the vendor cloud (connected) by deviceId; not LAN-discoverable; e-paper.
    assert SwitchBotBackend.capabilities.interaction == "connected"
    assert SwitchBotBackend.capabilities.transport == "cloud"
    assert SwitchBotBackend.capabilities.color_model == "epaper"
    assert SwitchBotBackend.capabilities.discovery is False
    assert SwitchBotBackend.canvas == (480, 800)  # 7.3" Spectra-6 panel, portrait


def test_switchbot_backend_is_push_not_lan_or_served() -> None:
    """SwitchBot is a third model: we initiate delivery (push to the vendor cloud), but there's no
    LAN session to open and no server the frame polls — so it's neither connected-LAN nor served."""
    backend = get_backend("switchbot")
    assert not isinstance(backend, ConnectedFrameBackend)  # no LAN session() contract
    assert not isinstance(backend, ServedFrameBackend)  # no router()/the frame doesn't poll us
    assert backend.discover() == []  # account-scoped (SwitchBotService), not LAN broadcast


def test_backends_classify_by_interaction_model() -> None:
    assert isinstance(get_backend("memento-lan"), ConnectedFrameBackend)
    assert isinstance(get_backend("sungale-cloud"), ServedFrameBackend)
    # The two models are distinct: a connected backend isn't served and vice versa.
    assert not isinstance(get_backend("memento-lan"), ServedFrameBackend)
    assert not isinstance(get_backend("sungale-cloud"), ConnectedFrameBackend)


def test_sungale_served_backend_exposes_a_router() -> None:
    backend = SungaleCloudBackend()
    assert backend.discover() == []  # cloud frames don't answer LAN discovery
    routes = {r.path for r in backend.router().routes}  # type: ignore[attr-defined]
    assert "/xiaowooya/api/v1/frame/ping" in routes
    assert "/xiaowooya/api/v1/image_library/list" in routes


def test_memento_lan_backend_drives_the_emulator(frame: EmulatedFrame) -> None:
    """The LAN backend's session must behave exactly like a direct FrameClient."""
    backend = MementoLanBackend()
    with backend.session(HOST, ports=PORTS) as conn:
        cfg = conn.get_config()
        assert cfg["Name"] == "Test Frame"
        conn.upload_image(b"\xff\xd8\xffPHOTO\xff\xd9", "via_backend.jpg")
        conn.next_image()
    assert "via_backend.jpg" in frame.state.photos
