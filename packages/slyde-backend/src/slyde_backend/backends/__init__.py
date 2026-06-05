"""Frame-backend registry — select a backend by name (config-driven, never hardcoded)."""

from __future__ import annotations

from .base import (
    ConnectedFrameBackend,
    FrameBackend,
    FrameCapabilities,
    FrameConnection,
    ServedFrameBackend,
)
from .memento_lan import MementoLanBackend
from .sungale_cloud import SungaleCloudBackend

_BACKENDS: dict[str, type[FrameBackend]] = {
    MementoLanBackend.name: MementoLanBackend,
    SungaleCloudBackend.name: SungaleCloudBackend,
}


def get_backend(name: str) -> FrameBackend:
    """Instantiate the backend registered under ``name`` (e.g. the ``FRAME_BACKEND`` setting)."""
    try:
        return _BACKENDS[name]()
    except KeyError:
        raise ValueError(
            f"unknown frame backend {name!r}; available: {', '.join(available_backends())}"
        ) from None


def available_backends() -> list[str]:
    return sorted(_BACKENDS)


__all__ = [
    "ConnectedFrameBackend",
    "FrameBackend",
    "FrameCapabilities",
    "FrameConnection",
    "MementoLanBackend",
    "ServedFrameBackend",
    "SungaleCloudBackend",
    "available_backends",
    "get_backend",
]
