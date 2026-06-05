"""Guaranteed delivery (issue #26): durable queue + retry/backoff + fallbacks.

Backend sync is not fire-and-forget. Every intended change to a frame is **durably queued** (in the
store) and retried until it lands, tolerating a frame being **offline for days**:

- **Offline is not a failure.** A ``TransientDeliveryError`` (offline / network)
  with **capped exponential backoff** and is **never abandoned** — a frame offline for days simply
  accumulates pending deliveries that complete when it returns.
- **Permanent errors fall back.** A ``PermanentDeliveryError`` (a genuinely bad item) goes terminal
  (state ``failed``) and triggers the ``on_failed`` fallback instead of retrying forever.

Served (cloud) frames are inherently offline-tolerant: their prepared image waits in the cache and
the frame pulls it on wake — the poll is the delivery. This queue is the reliability layer for the
connected-push path and for tracking delivery state. ``reconcile`` drains due items via an injected
``deliver`` callable; the live wiring (enqueue on change; reconcile on the scheduler) is the
remaining integration of #26.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from .store import DeliveryRow, Store


class TransientDeliveryError(Exception):
    """Retryable failure (frame offline / network). Retried with backoff, never abandoned."""


class PermanentDeliveryError(Exception):
    """Delivery failed for a non-retryable reason (bad item). Goes terminal + triggers fallback."""


class RetryPolicy:
    """Capped exponential backoff. Transient failures retry indefinitely (offline-tolerant)."""

    def __init__(
        self, base_seconds: float = 30.0, factor: float = 2.0, cap_seconds: float = 3600.0
    ):
        self.base = base_seconds
        self.factor = factor
        self.cap = cap_seconds

    def delay_for(self, attempts: int) -> float:
        """Seconds before retry ``attempts`` (1-based); grows exponentially up to the cap."""
        if attempts <= 1:
            return self.base
        return min(self.cap, self.base * (self.factor ** (attempts - 1)))


Deliverer = Callable[[DeliveryRow], Awaitable[None]]
FailureHandler = Callable[[DeliveryRow, Exception], Awaitable[None]]


def enqueue(store: Store, frame_id: str, key: str, payload: str = "", *, now: datetime) -> int:
    """Durably queue a delivery for ``(frame_id, key)``, due at ``now``."""
    return store.enqueue_delivery(frame_id, key, payload, next_attempt_at=now.isoformat())


async def reconcile(
    store: Store,
    deliver: Deliverer,
    *,
    now: datetime,
    policy: RetryPolicy | None = None,
    on_failed: FailureHandler | None = None,
    limit: int = 100,
) -> dict[str, int]:
    """Attempt every due delivery once. Returns counts {delivered, retried, failed}.

    ``deliver(item)`` returns on success, raises ``TransientDeliveryError`` to retry (with
    backoff) or ``PermanentDeliveryError`` to give up (terminal + ``on_failed``). Any other
    exception is treated as transient (offline-tolerant — we never drop work on a hiccup).
    """
    policy = policy or RetryPolicy()
    counts = {"delivered": 0, "retried": 0, "failed": 0}
    for item in store.due_deliveries(now.isoformat(), limit=limit):
        attempts = item.attempts + 1
        try:
            await deliver(item)
        except PermanentDeliveryError as exc:
            store.fail_delivery(item.id, attempts, str(exc))
            counts["failed"] += 1
            if on_failed is not None:
                await on_failed(item, exc)
        except Exception as exc:
            next_at = now + timedelta(seconds=policy.delay_for(attempts))
            store.reschedule_delivery(item.id, next_at.isoformat(), attempts, str(exc))
            counts["retried"] += 1
        else:
            store.mark_delivered(item.id)
            counts["delivered"] += 1
    return counts
