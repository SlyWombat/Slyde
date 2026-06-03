"""Background scheduler that periodically re-mirrors kept-in-sync album subscriptions.

Tracks the last cycle's outcome so a health/KPI endpoint can report sync status to Uptime Kuma.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass

from .sync import SyncService

_log = logging.getLogger(__name__)


@dataclass
class SchedulerStatus:
    enabled: bool
    interval_seconds: int
    last_ok: bool
    last_message: str
    last_run_iso: str | None
    age_seconds: float | None  # since the last run, or since start if it hasn't run yet


class SyncScheduler:
    def __init__(self, sync: SyncService, interval_minutes: int) -> None:
        self._sync = sync
        self._interval = max(0, interval_minutes) * 60
        self._task: asyncio.Task[None] | None = None
        self._started: float | None = None
        self._last_run: float | None = None
        self._last_run_iso: str | None = None
        self._last_ok = True
        self._last_message = "no cycle yet"

    def start(self) -> None:
        if self._interval > 0 and self._task is None:
            self._started = time.monotonic()
            self._task = asyncio.create_task(self._loop())
            _log.info("sync scheduler started (every %ss)", self._interval)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                summary = await self._sync.run_due_subscriptions()
                self._last_ok = True
                self._last_message = (
                    f"{summary['subscriptions']} albums: +{summary['added']} added, "
                    f"-{summary['removed']} removed, {summary['failed']} failed"
                )
            except Exception as exc:
                self._last_ok = False
                self._last_message = f"cycle error: {exc}"[:200]
                _log.exception("scheduled sync cycle failed")
            self._last_run = time.monotonic()
            self._last_run_iso = _utc_now_iso()

    def status(self) -> SchedulerStatus:
        reference = self._last_run if self._last_run is not None else self._started
        age = None if reference is None else time.monotonic() - reference
        return SchedulerStatus(
            enabled=self._interval > 0,
            interval_seconds=self._interval,
            last_ok=self._last_ok,
            last_message=self._last_message,
            last_run_iso=self._last_run_iso,
            age_seconds=age,
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")
