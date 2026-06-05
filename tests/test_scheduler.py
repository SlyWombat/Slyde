"""Scheduler drives the frame-agnostic delivery plane each cycle (#24)."""

from __future__ import annotations

import asyncio
from datetime import datetime

from slyde_backend.scheduler import SyncScheduler


class _FakeSync:
    async def run_due_subscriptions(self) -> dict[str, int]:
        return {"subscriptions": 0, "added": 0, "removed": 0, "failed": 0}


class _FakeDelivery:
    def __init__(self) -> None:
        self.calls: list[datetime] = []

    async def reconcile(self, *, now: datetime) -> dict[str, int]:
        self.calls.append(now)
        return {"delivered": 2, "retried": 1, "failed": 0}


def test_scheduler_reconciles_the_delivery_queue() -> None:
    delivery = _FakeDelivery()
    sched = SyncScheduler(_FakeSync(), 0, delivery)  # type: ignore[arg-type]
    counts = asyncio.run(sched._run_delivery())
    assert counts == {"delivered": 2, "retried": 1, "failed": 0}
    assert len(delivery.calls) == 1  # the delivery plane was driven, frame-agnostically


def test_scheduler_without_delivery_is_a_noop() -> None:
    sched = SyncScheduler(_FakeSync(), 0)  # type: ignore[arg-type]
    assert asyncio.run(sched._run_delivery()) == {}


def test_delivery_loop_drains_on_its_own_cadence() -> None:
    """#47: the delivery queue drains on its own tight timer, decoupled from album re-mirroring."""

    class _DrainSpy:
        def __init__(self) -> None:
            self.drains = 0

        async def reconcile(self, *, now: datetime) -> dict[str, int]:
            return {}

        async def drain(self, *, now: datetime) -> dict[str, int]:
            self.drains += 1
            return {"delivered": 1, "retried": 0, "failed": 0}

    async def run() -> int:
        spy = _DrainSpy()
        # sync interval 0 (subscription loop off); only the delivery loop runs.
        sched = SyncScheduler(_FakeSync(), 0, spy, delivery_interval_seconds=1)  # type: ignore[arg-type]
        sched._delivery_interval = 0.01  # tighten for the test
        sched.start()
        await asyncio.sleep(0.05)
        await sched.stop()
        return spy.drains

    assert asyncio.run(run()) >= 1
