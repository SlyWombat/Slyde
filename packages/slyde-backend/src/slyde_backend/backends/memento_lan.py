"""Memento LAN backend — the original, reverse-engineered local protocol.

Drives a real Memento Smart Frame (or the emulator / Pi soft-frame, which speak the same protocol)
over UDP discovery + TCP control/file channels. A thin adapter: ``memento_core.FrameClient`` already
implements ``FrameConnection``, so a session is just a connected client.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from memento_core import FrameClient, FrameInfo, Ports, discover

from .base import ConnectedFrameBackend, FrameCapabilities, FrameConnection


class MementoLanBackend(ConnectedFrameBackend):
    name = "memento-lan"
    capabilities = FrameCapabilities(
        interaction="connected",
        transport="lan",
        discovery=True,
        albums=True,
        thumbnails=True,
        upload=True,
        delete=True,
        ota=True,
    )

    def discover(self, *, timeout: float = 4.0, ports: Ports | None = None) -> list[FrameInfo]:
        return discover(timeout=timeout, ports=ports or Ports())

    @contextmanager
    def session(self, host: str, *, ports: Ports | None = None) -> Iterator[FrameConnection]:
        with FrameClient(host, ports=ports or Ports()) as client:
            yield client
