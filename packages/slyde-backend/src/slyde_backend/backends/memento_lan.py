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
        color_model="full",  # full-colour LCD panel
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
    def session(
        self, host: str, *, ports: Ports | None = None, timeout: float | None = None
    ) -> Iterator[FrameConnection]:
        # A short ``timeout`` (UI quick read) bounds both channels; ``None`` keeps the client's
        # defaults (10s control / 60s transfer) for management + bulk transfers (#68).
        if timeout is None:
            client = FrameClient(host, ports=ports or Ports())
        else:
            client = FrameClient(
                host, ports=ports or Ports(), timeout=timeout, file_timeout=timeout
            )
        with client as conn:
            yield conn
