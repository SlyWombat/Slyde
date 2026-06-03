"""Shared pytest fixtures: a running emulated frame on loopback."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from memento_core.protocol import Ports
from memento_emulator import EmulatedFrame, FrameState

HOST = "127.0.0.1"
# Use the real default ports; tests run on loopback so they don't collide with a real frame.
PORTS = Ports()


@pytest.fixture
def frame() -> Iterator[EmulatedFrame]:
    state = FrameState(name="Test Frame", ip=HOST)
    emu = EmulatedFrame(state, host=HOST, ports=PORTS).start()
    # Give the accept/UDP threads a moment to bind.
    time.sleep(0.1)
    try:
        yield emu
    finally:
        emu.stop()
