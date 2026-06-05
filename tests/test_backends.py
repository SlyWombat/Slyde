"""Frame-backend abstraction: registry, capabilities, and LAN-backend conformance."""

from __future__ import annotations

import pytest

from conftest import HOST, PORTS
from memento_backend.backends import (
    ConnectedFrameBackend,
    MementoLanBackend,
    ServedFrameBackend,
    SungaleCloudBackend,
    available_backends,
    get_backend,
)
from memento_emulator import EmulatedFrame


def test_registry_resolves_known_backends() -> None:
    assert available_backends() == ["memento-lan", "sungale-cloud"]
    assert isinstance(get_backend("memento-lan"), MementoLanBackend)
    assert isinstance(get_backend("sungale-cloud"), SungaleCloudBackend)


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


def test_backends_classify_by_interaction_model() -> None:
    assert isinstance(get_backend("memento-lan"), ConnectedFrameBackend)
    assert isinstance(get_backend("sungale-cloud"), ServedFrameBackend)
    # The two models are distinct: a connected backend isn't served and vice versa.
    assert not isinstance(get_backend("memento-lan"), ServedFrameBackend)
    assert not isinstance(get_backend("sungale-cloud"), ConnectedFrameBackend)


def test_sungale_served_backend_is_declared_but_not_yet_implemented() -> None:
    backend = SungaleCloudBackend()
    assert backend.discover() == []  # cloud frames don't answer LAN discovery
    # The served surface (router/identify/respond) is declared but pending #22/#23.
    with pytest.raises(NotImplementedError, match="pending"):
        backend.router()


def test_memento_lan_backend_drives_the_emulator(frame: EmulatedFrame) -> None:
    """The LAN backend's session must behave exactly like a direct FrameClient."""
    backend = MementoLanBackend()
    with backend.session(HOST, ports=PORTS) as conn:
        cfg = conn.get_config()
        assert cfg["Name"] == "Test Frame"
        conn.upload_image(b"\xff\xd8\xffPHOTO\xff\xd9", "via_backend.jpg")
        conn.next_image()
    assert "via_backend.jpg" in frame.state.photos
