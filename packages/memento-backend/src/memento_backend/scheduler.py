"""Background scheduler that periodically re-mirrors kept-in-sync album subscriptions."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from .sync import SyncService

_log = logging.getLogger(__name__)


class SyncScheduler:
    def __init__(self, sync: SyncService, interval_minutes: int) -> None:
        self._sync = sync
        self._interval = max(0, interval_minutes) * 60
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._interval > 0 and self._task is None:
            self._task = asyncio.create_task(self._loop())
            _log.info("sync scheduler started (every %ss)", self._interval)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self._sync.run_due_subscriptions()
            except Exception:
                _log.exception("scheduled sync cycle failed")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
