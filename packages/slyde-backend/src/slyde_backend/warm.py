"""Kept-warm control sessions to connected frames (#71).

A real Memento frame services a **new** control connection only once per ~20.7s tick, and it tears
each session down at the following tick. Measured against the Living Room frame: eight connects at
staggered offsets all had their first reply land at the same point in a 20.7s cycle (phase
concentration R=0.999), so a cold connect's "slow first command" is not work the frame is doing --
it is ``T - (connect_time mod T)``, the wait for the next tick. Two consequences drive this module:

1. Opening a session per operation cannot work. The frame's guaranteed time-to-first-reply is up to
   ~21s (~42s across a missed tick), so a 3.5s quick read or a 10s default times out every time and
   the frame looks permanently wedged, while in fact answering everything.
2. Reconnecting *the instant* the frame hangs up keeps us in phase: four back-to-back sessions,
   each reopened immediately after the frame closed it, had first replies in 0.2/0.0/0.0/0.1s. A
   pause before reconnecting costs a whole tick.

So each frame gets a ``_Keeper``: a daemon thread that holds one session, claims each service tick
with a single cheap read, runs every queued operation on that warm session (sub-second), and
reopens at once when the frame drops it. The queue also gives us serialization and pacing for free,
which is why the warm path takes no asyncio lock -- a quick UI read can no longer be stuck waiting
behind a bulk delivery's lock (the ``frames.py`` lock bounded only the op, never the wait for it).

The session is released after ``frame_warm_idle_ttl`` without work: the frame starves a second
concurrent client, so holding its only slot forever would lock out the vendor phone app.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, TypeVar

from memento_core import Ports

from .backends import ConnectedFrameBackend, FrameConnection, SessionLiveness
from .config import Settings

_log = logging.getLogger(__name__)

T = TypeVar("T")

# The frame hanging up mid-operation is routine here (it happens every ~21s), not a fault: the op
# is retried once on a fresh session. A timeout is NOT in this set -- a frame that accepted the
# request but never answered may well have acted on it, so we surface it instead of repeating it.
_TEARDOWN = (ConnectionError, EOFError)


class WarmSessionUnavailable(RuntimeError):
    """No warm session is available to run an operation on."""


@dataclass
class _Job:
    fn: Callable[[FrameConnection], Any]
    future: Future[Any]


class _Keeper:
    """One frame's warm session, owned by a dedicated daemon thread.

    ``status`` is what the UI needs to stop guessing: ``"live"`` (session up, ops run now),
    ``"connecting"`` (TCP is up but the frame hasn't reached its service tick yet) or ``"offline"``
    (the connection itself failed -- refused, no route, gone). The old code called all three
    "asleep?", which sent you looking for the wrong problem (#71).
    """

    def __init__(
        self,
        backend: ConnectedFrameBackend,
        host: str,
        *,
        ports: Ports,
        connect_timeout: float,
        idle_ttl: float,
        retry_delay: float,
        settle_delay: float = 0.0,
        poll_interval: float = 0.1,
    ) -> None:
        self._backend = backend
        self._host = host
        self._ports = ports
        self._connect_timeout = connect_timeout
        self._idle_ttl = idle_ttl
        self._retry_delay = retry_delay
        self._settle_delay = settle_delay
        self._poll = poll_interval
        self._jobs: queue.Queue[_Job] = queue.Queue()
        self._stop = threading.Event()
        self._live = threading.Event()
        self.status = "connecting"
        self.last_error = ""
        self.sessions = 0  # how many times we've (re)connected — asserted in tests, useful in logs
        self.failures = 0  # consecutive failed connects, reset when the frame comes back
        self._thread = threading.Thread(target=self._run, name=f"frame-session-{host}", daemon=True)
        self._thread.start()

    # -- public surface -------------------------------------------------------
    @property
    def live(self) -> bool:
        return self._live.is_set()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def submit(self, fn: Callable[[FrameConnection], T]) -> Future[T]:
        future: Future[T] = Future()
        if self._stop.is_set():
            future.set_exception(WarmSessionUnavailable(f"frame {self._host}: session pool closed"))
            return future
        self._jobs.put(_Job(fn, future))
        return future

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop.set()
        self._thread.join(timeout)

    def detail(self) -> str:
        """A human explanation of why an op can't run right now — surfaced to the API/UI."""
        if self.status == "offline":
            return f"frame {self._host} is offline ({self.last_error or 'connection failed'})"
        if self.status == "connecting":
            return (
                f"frame {self._host} hasn't answered its control channel yet "
                f"(waiting for its next service tick, up to ~21s)"
            )
        return f"frame {self._host} is not answering right now"

    # -- thread body ----------------------------------------------------------
    def _run(self) -> None:
        conn: FrameConnection | None = None
        session: AbstractContextManager[FrameConnection] | None = None
        idle_since = time.monotonic()
        try:
            while not self._stop.is_set():
                if conn is None:
                    conn, session = self._open()
                    idle_since = time.monotonic()
                    if conn is None:
                        self._fail_queued()
                        self._stop.wait(self._retry_delay)
                    continue
                job = self._take()
                if job is None:
                    if self._hung_up(conn):
                        # Reopen AT ONCE: this is what keeps us in phase with the frame's tick.
                        self._shut(session)
                        conn, session = None, None
                        continue
                    if self._idle_ttl and time.monotonic() - idle_since > self._idle_ttl:
                        _log.info("frame %s: releasing idle warm session", self._host)
                        break
                    continue
                idle_since = time.monotonic()
                conn, session = self._serve(job, conn, session)
        finally:  # pragma: no cover - defensive: the thread must never leave a socket open
            self._shut(session)
            self._live.clear()
            self._fail_queued()

    def _open(
        self,
    ) -> tuple[FrameConnection | None, AbstractContextManager[FrameConnection] | None]:
        started = time.monotonic()
        session = self._backend.session(
            self._host, ports=self._ports, timeout=self._connect_timeout
        )
        try:
            conn = session.__enter__()
        except OSError as exc:  # refused / no route / gone — the frame really is unreachable
            self._went_offline(exc)
            return None, None
        self.status = "connecting"
        try:
            # Claim the frame's next service tick with one cheap read. A frame that services a new
            # connection only once per tick answers this and nothing before it, so completing it is
            # what proves the session is usable — and everything after it is sub-second (#71).
            conn.get_current_image_name()
        except (TimeoutError, OSError) as exc:
            self._went_offline(exc)
            with contextlib.suppress(Exception):
                session.__exit__(type(exc), exc, exc.__traceback__)
            return None, None
        self.sessions += 1
        self.status, self.last_error = "live", ""
        self._live.set()
        if self.failures:
            _log.info(
                "frame %s: back after %d failed attempt(s) (last: %s)",
                self._host,
                self.failures,
                self.last_error,
            )
        self.failures = 0
        _log.info(
            "frame %s: warm session #%d live (first reply after %.1fs)",
            self._host,
            self.sessions,
            time.monotonic() - started,
        )
        return conn, session

    def _went_offline(self, exc: BaseException) -> None:
        """Record — and, on the way down, announce — that the frame stopped taking connections.

        Logged on the transition only: the keeper retries every few seconds for as long as a frame
        is away, and a line per attempt would bury the night's real events. Silence here would be
        worse, though — without it an unreachable frame looks identical to an idle one in the log.
        """
        was = self.status
        self._live.clear()
        self.status, self.last_error = "offline", str(exc)
        self.failures += 1
        if was != "offline":
            _log.warning("frame %s: went offline (%s); retrying until it returns", self._host, exc)

    def _take(self) -> _Job | None:
        try:
            job = self._jobs.get(timeout=self._poll)
        except queue.Empty:
            return None
        # A quick read that already gave up (its caller timed out) must not cost the frame a round
        # trip, nor delay the job behind it.
        return None if job.future.cancelled() else job

    def _serve(
        self,
        job: _Job,
        conn: FrameConnection,
        session: AbstractContextManager[FrameConnection] | None,
    ) -> tuple[FrameConnection | None, AbstractContextManager[FrameConnection] | None]:
        try:
            self._run_job(job, conn)
            return conn, session
        except _TEARDOWN as exc:
            # The frame hung up on us — expected roughly every 21s. Reopen and give the op one more
            # go on the fresh session, so a delivery isn't lost to a tick boundary.
            _log.debug("frame %s: session dropped mid-op, retrying once", self._host)
            self._shut(session)
            fresh, session = self._open()
            if fresh is None:
                _settle_exception(job, exc)
                return None, None
            try:
                self._run_job(job, fresh)
            except Exception as retried:  # one retry only — don't loop against a hostile frame
                _settle_exception(job, retried)
                self._shut(session)
                return None, None
            return fresh, session
        except (TimeoutError, OSError) as exc:
            # Answered nothing in time: the channel may hold a stale reply, so drop the session and
            # reconnect on the next pass rather than desynchronizing every later op.
            _settle_exception(job, exc)
            self._shut(session)
            self._live.clear()
            self.status, self.last_error = "connecting", str(exc)
            return None, None
        except Exception as exc:  # the op itself failed — the session is fine, the caller isn't
            _settle_exception(job, exc)
            return conn, session

    def _run_job(self, job: _Job, conn: FrameConnection) -> None:
        result = job.fn(conn)
        if not job.future.cancelled():
            job.future.set_result(result)
        self._settle()

    def _hung_up(self, conn: FrameConnection) -> bool:
        if not isinstance(conn, SessionLiveness):
            return False  # a transport that can't tell; we find out on the next op instead
        try:
            return conn.closed_by_peer(0.0)
        except OSError:
            return True

    def _settle(self) -> None:
        # Pace ops to one frame the way the old per-frame lock did (low-power frames stop answering
        # under rapid-fire requests). Serialization itself comes from the single keeper thread.
        if self._settle_delay:
            self._stop.wait(self._settle_delay)

    def _shut(self, session: AbstractContextManager[FrameConnection] | None) -> None:
        self._live.clear()
        if session is not None:
            with contextlib.suppress(Exception):
                session.__exit__(None, None, None)

    def _fail_queued(self) -> None:
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                return
            _settle_exception(job, WarmSessionUnavailable(self.detail()))


def _settle_exception(job: _Job, exc: BaseException) -> None:
    """Hand ``exc`` to the waiting caller, unless it already gave up on the job."""
    if not job.future.cancelled():
        job.future.set_exception(exc)


class WarmSessionPool:
    """One warm session per connected frame, keyed by resolved address (#71)."""

    def __init__(self, backend: ConnectedFrameBackend, *, ports: Ports, settings: Settings) -> None:
        self._backend = backend
        self._ports = ports
        self._settings = settings
        self._keepers: dict[str, _Keeper] = {}
        self._lock = threading.Lock()

    def warm(self, host: str) -> None:
        """Start (or keep) a warm session for ``host`` without running anything on it.

        Lets a caller pay the frame's tick wait ahead of the first UI read instead of during it.
        """
        self._keeper(host)

    def status(self, host: str) -> str:
        """``"live"`` | ``"connecting"`` | ``"offline"`` | ``"idle"`` (no session held)."""
        keeper = self._keepers.get(host)
        return keeper.status if keeper is not None and keeper.alive else "idle"

    def _keeper(self, host: str) -> _Keeper:
        with self._lock:
            keeper = self._keepers.get(host)
            if keeper is None or not keeper.alive:  # never started, or released after idling out
                keeper = _Keeper(
                    self._backend,
                    host,
                    ports=self._ports,
                    connect_timeout=self._settings.frame_connect_timeout,
                    idle_ttl=self._settings.frame_warm_idle_ttl,
                    retry_delay=self._settings.frame_warm_retry_delay,
                    settle_delay=self._settings.frame_settle_delay,
                )
                self._keepers[host] = keeper
            return keeper

    async def run(self, host: str, fn: Callable[[FrameConnection], T], *, quick: bool) -> T:
        """Run ``fn`` on ``host``'s warm session.

        ``quick`` is the UI read path, and its whole wall-clock is bounded by
        ``frame_quick_timeout`` — including any wait for the session to come up, which is exactly
        what the old code failed to bound (it capped the op, then queued it behind an unbounded
        lock, so a "fast-fail" read could take 17s, #68). A frame we already know is unreachable
        fails instantly rather than burning that budget every poll.

        On a warm session the op itself is sub-second, so this budget is spent only while a session
        is still claiming the frame's service tick — and the keeper it started carries on warming
        up, so the next poll is served immediately (#71).
        """
        keeper = self._keeper(host)
        if quick and keeper.status == "offline":
            raise WarmSessionUnavailable(keeper.detail())
        future = keeper.submit(fn)
        if not quick:
            return await asyncio.wrap_future(future)
        try:
            return await asyncio.wait_for(
                asyncio.wrap_future(future), self._settings.frame_quick_timeout
            )
        except TimeoutError:
            future.cancel()
            detail = (
                keeper.detail()
                if not keeper.live
                else (
                    f"frame {host} did not answer a quick read in "
                    f"{self._settings.frame_quick_timeout:g}s"
                )
            )
            raise WarmSessionUnavailable(detail) from None

    async def aclose(self) -> None:
        with self._lock:
            keepers = list(self._keepers.values())
            self._keepers.clear()
        for keeper in keepers:
            await asyncio.to_thread(keeper.stop)
