"""In-memory background jobs for long-running syncs.

A large album sync takes far longer than a browser will hold an HTTP request open, so the API
starts it as a job and the UI polls progress. Jobs live only for the process lifetime — they are
progress indicators, not durable state (the sync's durable record is the photo store).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .schemas import SyncResult

_log = logging.getLogger(__name__)

# A unit of work: given a live result to populate, run the sync and return it.
SyncRunner = Callable[[SyncResult], Awaitable[SyncResult]]


@dataclass
class SyncJob:
    id: str
    host: str
    label: str
    result: SyncResult
    status: str = "running"  # "running" | "done" | "error"
    error: str | None = None


class JobManager:
    """Tracks background sync jobs, keeping the most recent ``keep`` finished ones."""

    def __init__(self, keep: int = 50) -> None:
        self._jobs: dict[str, SyncJob] = {}
        self._order: list[str] = []
        self._tasks: set[asyncio.Task[None]] = set()
        self._keep = keep

    def start(self, host: str, label: str, run: SyncRunner) -> SyncJob:
        job = SyncJob(id=uuid.uuid4().hex, host=host, label=label, result=SyncResult())
        self._jobs[job.id] = job
        self._order.append(job.id)
        self._prune()

        async def _run() -> None:
            try:
                await run(job.result)
                job.status = "done"
            except Exception as exc:
                job.status = "error"
                job.error = str(exc)[:300]
                _log.exception("sync job %s (%s) failed", job.id, host)

        task = asyncio.create_task(_run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    def get(self, job_id: str) -> SyncJob | None:
        return self._jobs.get(job_id)

    def _prune(self) -> None:
        finished = [j for j in self._order if self._jobs[j].status != "running"]
        while len(self._jobs) > self._keep and finished:
            old = finished.pop(0)
            self._order.remove(old)
            self._jobs.pop(old, None)
