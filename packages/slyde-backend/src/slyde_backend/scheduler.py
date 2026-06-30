"""Background scheduler that periodically re-mirrors kept-in-sync album subscriptions.

Tracks the last cycle's outcome so a health/KPI endpoint can report sync status to Uptime Kuma.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from .delivery_service import DeliveryService
from .folder_sync import FolderSyncService

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
    def __init__(
        self,
        folder_sync: FolderSyncService,
        interval_minutes: int,
        delivery: DeliveryService | None = None,
        delivery_interval_seconds: int = 0,
        current_preview: Callable[[], Awaitable[object]] | None = None,
        current_preview_interval_seconds: int = 0,
    ) -> None:
        self._folder_sync = folder_sync
        self._delivery = delivery
        self._interval = max(0, interval_minutes) * 60
        self._delivery_interval = max(0, delivery_interval_seconds)
        # Opportunistic refresh of each connected frame's cached current-image preview (#68), on its
        # own gentle cadence so the overview renders the live picture without a per-card live call.
        self._current_preview = current_preview
        self._current_preview_interval = max(0, current_preview_interval_seconds)
        self._task: asyncio.Task[None] | None = None
        self._delivery_task: asyncio.Task[None] | None = None
        self._current_preview_task: asyncio.Task[None] | None = None
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
        # Delivery drains on its own, tighter cadence so a fresh curation syncs in seconds (#47).
        has_delivery = self._delivery is not None and self._delivery_interval > 0
        if has_delivery and self._delivery_task is None:
            self._delivery_task = asyncio.create_task(self._delivery_loop())
            _log.info("delivery drain started (every %ss)", self._delivery_interval)
        has_preview = self._current_preview is not None and self._current_preview_interval > 0
        if has_preview and self._current_preview_task is None:
            self._current_preview_task = asyncio.create_task(self._current_preview_loop())
            _log.info("current-image refresh started (every %ss)", self._current_preview_interval)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                summary = await self._folder_sync.run_due()
                delivered = await self._run_delivery()
                self._last_ok = True
                self._last_message = (
                    f"{summary['subscriptions']} folders: +{summary['added']} added, "
                    f"-{summary['removed']} removed, {summary['failed']} failed; "
                    f"delivery {delivered}"
                )
            except Exception as exc:
                self._last_ok = False
                self._last_message = f"cycle error: {exc}"[:200]
                _log.exception("scheduled sync cycle failed")
            self._last_run = time.monotonic()
            self._last_run_iso = _utc_now_iso()

    async def _delivery_loop(self) -> None:
        """Drain the delivery queue on a tight timer, independent of album mirroring."""
        assert self._delivery is not None
        while True:
            await asyncio.sleep(self._delivery_interval)
            try:
                await self._delivery.drain(now=datetime.now(UTC))
            except Exception:
                _log.exception("delivery drain failed")

    async def _current_preview_loop(self) -> None:
        """Quick-fetch each connected frame's current image on a gentle timer, caching it as that
        frame's preview so the overview never makes a blocking call to render the live picture."""
        assert self._current_preview is not None
        while True:
            await asyncio.sleep(self._current_preview_interval)
            try:
                await self._current_preview()
            except Exception:
                _log.exception("current-image preview refresh failed")

    async def _run_delivery(self) -> dict[str, int]:
        """One delivery batch alongside album re-mirroring; the loop does the full drain."""
        if self._delivery is None:
            return {}
        return await self._delivery.reconcile(now=datetime.now(UTC))

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
        for attr in ("_task", "_delivery_task", "_current_preview_task"):
            task = getattr(self, attr)
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                setattr(self, attr, None)


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")
