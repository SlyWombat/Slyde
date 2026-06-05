"""Guaranteed delivery: durable queue + retry/backoff + fallbacks (issue #26)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from slyde_backend.delivery import (
    PermanentDeliveryError,
    RetryPolicy,
    TransientDeliveryError,
    enqueue,
    reconcile,
)
from slyde_backend.store import DeliveryRow, Store

T0 = datetime(2026, 1, 1, 12, 0, 0)


def test_retry_policy_caps_exponential_backoff() -> None:
    p = RetryPolicy(base_seconds=30, factor=2, cap_seconds=3600)
    assert p.delay_for(1) == 30
    assert p.delay_for(2) == 60
    assert p.delay_for(3) == 120
    assert (
        p.delay_for(50) == 3600
    )  # capped, so it keeps retrying ~hourly forever (offline-tolerant)


def test_enqueue_is_durable_and_idempotent(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "d.db"))
    a = enqueue(store, "EF-1", "one.jpg", "asset-a", now=T0)
    b = enqueue(store, "EF-1", "one.jpg", "asset-a", now=T0)  # same key -> same row
    assert a == b
    assert [d.key for d in store.list_deliveries("EF-1")] == ["one.jpg"]


def test_successful_delivery_marks_delivered(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "d.db"))
    enqueue(store, "EF-1", "one.jpg", now=T0)

    async def deliver(item: DeliveryRow) -> None:
        return None

    counts = asyncio.run(reconcile(store, deliver, now=T0))
    assert counts == {"delivered": 1, "retried": 0, "failed": 0}
    assert store.list_deliveries("EF-1")[0].state == "delivered"
    # delivered items are no longer due
    assert asyncio.run(reconcile(store, deliver, now=T0 + timedelta(days=1)))["delivered"] == 0


def test_offline_frame_retries_forever_without_failing(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "d.db"))
    enqueue(store, "EF-1", "one.jpg", now=T0)
    policy = RetryPolicy(base_seconds=30, factor=2, cap_seconds=3600)

    async def offline(item: DeliveryRow) -> None:
        raise TransientDeliveryError("frame offline")

    # Day 1: attempt fails transiently -> rescheduled, still pending (never failed).
    now = T0
    for _ in range(5):  # simulate several days of the frame being unreachable
        counts = asyncio.run(reconcile(store, offline, now=now, policy=policy))
        assert counts == {"delivered": 0, "retried": 1, "failed": 0}
        row = store.list_deliveries("EF-1")[0]
        assert row.state == "pending"  # offline is never a terminal failure
        now = datetime.fromisoformat(row.next_attempt_at)  # jump to when it's next due
    assert store.list_deliveries("EF-1")[0].attempts == 5

    # When the frame finally comes back, the queued delivery completes.
    async def online(item: DeliveryRow) -> None:
        return None

    assert asyncio.run(reconcile(store, online, now=now))["delivered"] == 1
    assert store.list_deliveries("EF-1")[0].state == "delivered"


def test_permanent_failure_goes_terminal_and_runs_fallback(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "d.db"))
    enqueue(store, "EF-1", "bad.jpg", now=T0)
    fell_back: list[str] = []

    async def deliver(item: DeliveryRow) -> None:
        raise PermanentDeliveryError("rejected payload")

    async def on_failed(item: DeliveryRow, exc: Exception) -> None:
        fell_back.append(item.key)

    counts = asyncio.run(reconcile(store, deliver, now=T0, on_failed=on_failed))
    assert counts == {"delivered": 0, "retried": 0, "failed": 1}
    assert store.list_deliveries("EF-1")[0].state == "failed"
    assert fell_back == ["bad.jpg"]  # fallback ran
    # terminal items are not retried
    assert asyncio.run(reconcile(store, deliver, now=T0 + timedelta(days=1)))["failed"] == 0


def test_unexpected_error_is_treated_as_transient(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "d.db"))
    enqueue(store, "EF-1", "one.jpg", now=T0)

    async def flaky(item: DeliveryRow) -> None:
        raise RuntimeError("unexpected hiccup")

    counts = asyncio.run(reconcile(store, flaky, now=T0))
    assert counts["retried"] == 1  # not dropped — retried, never abandoned
    assert store.list_deliveries("EF-1")[0].state == "pending"
