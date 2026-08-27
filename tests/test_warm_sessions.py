"""Kept-warm control sessions to connected frames (#71).

The behaviour under test is the real frame's: it services a NEW connection only once per ~21s
tick and tears the session down at the next one. ``_TickingBackend`` reproduces that shape (a
first-call delay, then sub-millisecond calls, then a hang-up) so the pool's contract can be
asserted without waiting 21 real seconds.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from conftest import HOST, PORTS
from memento_core.protocol import JsonDict, Ports
from memento_emulator import EmulatedFrame
from slyde_backend.backends import ConnectedFrameBackend, FrameCapabilities, MementoLanBackend
from slyde_backend.config import Settings
from slyde_backend.frames import FrameService, FrameUnavailable
from slyde_backend.store import Store
from slyde_backend.warm import WarmSessionPool, WarmSessionUnavailable


class _TickingConnection:
    """One session to the fake frame. Hangs up after ``hangup_after`` calls, like the real tick."""

    def __init__(self, backend: _TickingBackend) -> None:
        self._backend = backend
        self.calls = 0
        self.closed = False

    def _call(self, name: str) -> str:
        if self.closed:
            raise ConnectionError("control channel closed by peer")
        self.calls += 1
        self._backend.log.append(name)
        if self._backend.hangup_after and self.calls >= self._backend.hangup_after:
            self.closed = True  # the frame drops us; noticed by closed_by_peer or the next call
        return f"{name}-ok"

    # -- the slice of FrameConnection these tests exercise --------------------
    def get_current_image_name(self) -> str:
        return self._call("get_current_image_name")

    def get_config(self) -> JsonDict:
        return {"Name": self._call("get_config")}

    def closed_by_peer(self, timeout: float = 0.0) -> bool:
        return self.closed


class _TickingBackend(ConnectedFrameBackend):
    """A frame that answers a new connection only after ``first_reply_delay`` seconds."""

    name = "ticking"
    capabilities: FrameCapabilities = MementoLanBackend.capabilities

    def __init__(
        self,
        *,
        first_reply_delay: float = 0.0,
        hangup_after: int = 0,
        connect_error: OSError | None = None,
    ) -> None:
        self.first_reply_delay = first_reply_delay
        self.hangup_after = hangup_after
        self.connect_error = connect_error
        self.sessions = 0
        self.log: list[str] = []
        self.opened = threading.Event()

    def discover(self, *, timeout: float = 4.0, ports: Ports | None = None) -> list:
        return []

    @contextmanager
    def session(
        self, host: str, *, ports: Ports | None = None, timeout: float | None = None
    ) -> Iterator[_TickingConnection]:
        if self.connect_error is not None:
            raise self.connect_error
        if self.first_reply_delay:
            time.sleep(self.first_reply_delay)  # the wait for the frame's next service tick
        self.sessions += 1
        self.opened.set()
        conn = _TickingConnection(self)
        try:
            yield conn
        finally:
            conn.closed = True


def _pool(backend: ConnectedFrameBackend, **overrides: object) -> WarmSessionPool:
    fields: dict[str, object] = {"frame_warm_idle_ttl": 30.0, "frame_settle_delay": 0}
    fields.update(overrides)
    return WarmSessionPool(backend, ports=PORTS, settings=Settings(**fields))  # type: ignore[arg-type]


def _wait_live(pool: WarmSessionPool, host: str, *, timeout: float = 5.0) -> bool:
    pool.warm(host)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pool.status(host) == "live":
            return True
        time.sleep(0.02)
    return False


def test_every_op_runs_on_one_warm_session() -> None:
    """The point of the pool: N ops cost ONE connect, not N. A per-op connect is what made every
    read time out against a frame that only services a new connection once per tick (#71)."""
    backend = _TickingBackend()
    pool = _pool(backend)

    async def scenario() -> list[str]:
        assert _wait_live(pool, "frame-a")
        results = [await pool.run("frame-a", lambda c: c.get_current_image_name(), quick=True)]
        for _ in range(4):
            results.append(await pool.run("frame-a", lambda c: c.get_config()["Name"], quick=False))
        await pool.aclose()
        return results

    results = asyncio.run(scenario())
    assert results[0] == "get_current_image_name-ok"
    assert results[1:] == ["get_config-ok"] * 4
    assert backend.sessions == 1  # one connect for five ops


def test_reconnects_at_once_when_the_frame_hangs_up() -> None:
    """The frame tears the session down every tick. The keeper must notice with no op in flight and
    reopen immediately — reconnecting late is what costs a whole tick on the real frame (#71)."""
    backend = _TickingBackend(hangup_after=1)  # every session dies after its tick-claiming read
    pool = _pool(backend)

    async def scenario() -> None:
        assert _wait_live(pool, "frame-b")
        opened = backend.sessions
        deadline = time.monotonic() + 5.0
        while backend.sessions < opened + 2 and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert backend.sessions >= opened + 2, "keeper did not reconnect on its own"
        await pool.aclose()

    asyncio.run(scenario())


def test_op_cut_off_by_a_hangup_is_retried_once_on_a_fresh_session() -> None:
    """A ~21s session life means a delivery WILL be cut mid-op; that must not lose the photo."""
    backend = _TickingBackend()
    pool = _pool(backend)
    attempts: list[int] = []

    def flaky(conn: _TickingConnection) -> str:
        attempts.append(1)
        if len(attempts) == 1:
            conn.closed = True
            raise ConnectionError("control channel closed by peer")
        return conn.get_current_image_name()

    async def scenario() -> str:
        assert _wait_live(pool, "frame-c")
        result = await pool.run("frame-c", flaky, quick=False)
        await pool.aclose()
        return result

    assert asyncio.run(scenario()) == "get_current_image_name-ok"
    assert len(attempts) == 2  # retried exactly once
    assert backend.sessions == 2  # on a fresh session, not the dead one


def test_quick_read_fails_fast_while_the_session_is_still_connecting() -> None:
    """A UI read must never sit through the frame's tick wait: it reports 'not answering yet' now,
    and the keeper it started serves the next poll from a warm session (#68/#71)."""
    backend = _TickingBackend(first_reply_delay=1.5)
    pool = _pool(backend, frame_quick_timeout=0.3)

    async def scenario() -> None:
        started = time.monotonic()
        with pytest.raises(WarmSessionUnavailable) as caught:
            await pool.run("frame-d", lambda c: c.get_current_image_name(), quick=True)
        waited = time.monotonic() - started
        assert waited < 1.0, "a quick read sat through the frame's tick wait"
        assert "hasn't answered" in str(caught.value)
        assert _wait_live(pool, "frame-d")  # the keeper carried on and warmed up
        assert await pool.run("frame-d", lambda c: c.get_current_image_name(), quick=True)
        await pool.aclose()

    asyncio.run(scenario())


def test_unreachable_frame_reads_as_offline_not_asleep() -> None:
    """#71 point 1: an unreachable frame and a slow-to-answer one are different states, and the
    message has to say which — 'asleep?' fitted both and explained neither."""
    backend = _TickingBackend(connect_error=ConnectionRefusedError("connection refused"))
    pool = _pool(backend)

    async def scenario() -> str:
        deadline = time.monotonic() + 5.0
        while pool.status("frame-e") != "offline" and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        with pytest.raises(WarmSessionUnavailable) as caught:
            await pool.run("frame-e", lambda c: c.get_current_image_name(), quick=True)
        await pool.aclose()
        return str(caught.value)

    message = asyncio.run(scenario())
    assert "offline" in message and "refused" in message
    assert "asleep" not in message


def test_idle_session_is_released_so_another_client_can_connect() -> None:
    """The frame starves a second client, so we must not hold its only slot forever (#71)."""
    backend = _TickingBackend()
    pool = _pool(backend, frame_warm_idle_ttl=0.3)

    async def scenario() -> None:
        assert _wait_live(pool, "frame-f")
        deadline = time.monotonic() + 5.0
        while pool.status("frame-f") != "idle" and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert pool.status("frame-f") == "idle"
        # ...and a later op transparently warms a new session.
        assert await pool.run("frame-f", lambda c: c.get_current_image_name(), quick=False)
        await pool.aclose()

    asyncio.run(scenario())
    assert backend.sessions == 2


def test_frame_service_drives_a_real_frame_over_a_warm_session(
    frame: EmulatedFrame, tmp_path: Path
) -> None:
    """End to end against the emulator: repeated ops reuse one session and still register the
    frame, and shutdown releases it."""
    store = Store(str(tmp_path / "warm.db"))
    settings = Settings(frame_host=HOST, frame_warm_sessions=True, frame_settle_delay=0)
    service = FrameService(settings, ports=PORTS, store=store)

    async def scenario() -> tuple[str, str]:
        cfg = await service.get_config(HOST)
        name = await service.get_current_image(HOST)
        assert service.session_status(HOST) == "live"
        await service.aclose()
        assert service.session_status(HOST) == "idle"
        return str(cfg["Name"]), name

    reported, _current = asyncio.run(scenario())
    assert reported == "Test Frame"
    assert store.get_frame_by_address(HOST) is not None  # still registered on contact


def test_frame_service_reports_an_unreachable_frame_without_blaming_sleep(tmp_path: Path) -> None:
    """A dead address fails as a clean FrameUnavailable, described as offline (#71)."""
    store = Store(str(tmp_path / "down.db"))
    settings = Settings(frame_host="127.0.0.1", frame_discovery=False, frame_warm_retry_delay=0.05)
    service = FrameService(settings, ports=Ports(control=1, file=2), store=store)

    async def scenario() -> str:
        with pytest.raises(FrameUnavailable) as caught:
            await service.get_current_image("127.0.0.1")
        await service.aclose()
        return str(caught.value)

    assert "asleep" not in asyncio.run(scenario())
