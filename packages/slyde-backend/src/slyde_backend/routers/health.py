"""Health, readiness, and the sync KPI (for Uptime Kuma)."""

from __future__ import annotations

from fastapi import APIRouter, Response

from .. import __version__
from ..schemas import DeliverySummary, FrameCounts, Health, Metrics
from .deps import FirmwareDep, SchedulerDep, SettingsDep, StoreDep

router = APIRouter(tags=["health"])


@router.get("/health", response_model=Health)
async def health(settings: SettingsDep) -> Health:
    return Health(
        version=__version__,
        immich_configured=bool(settings.immich_base_url and settings.immich_api_key),
    )


@router.get("/metrics", response_model=Metrics)
async def metrics(store: StoreDep, firmware: FirmwareDep, settings: SettingsDep) -> Metrics:
    """Lightweight operational rollups for monitoring (#55): frame + delivery counts, firmware."""
    frames = store.list_frames()
    counts = FrameCounts(
        total=len(frames),
        connected=sum(1 for f in frames if f.interaction == "connected"),
        served=sum(1 for f in frames if f.interaction == "served"),
    )
    track = settings.firmware_track
    entry = firmware.get(track)
    return Metrics(
        version=__version__,
        frames=counts,
        deliveries=DeliverySummary(**store.delivery_totals()),
        firmware_track=track,
        firmware_version=entry.version if entry else "",
    )


@router.get("/health/sync", response_class=Response)
async def sync_health(scheduler: SchedulerDep, store: StoreDep) -> Response:
    """Sync KPI for an Uptime Kuma HTTP keyword monitor (keyword ``OK``).

    Returns ``OK <details>`` (200) when scheduled mirroring is healthy, or ``FAIL <reason>``
    (503) when the last cycle errored or no successful cycle has run within the grace window.
    """
    status = scheduler.status()
    subscriptions = len(store.list_subscriptions())

    def text(body: str, code: int = 200) -> Response:
        return Response(content=body, media_type="text/plain", status_code=code)

    if not status.enabled:
        return text("OK scheduler disabled")
    if subscriptions == 0:
        return text("OK no subscriptions")
    if status.last_run_iso and not status.last_ok:
        return text(f"FAIL last sync cycle errored: {status.last_message}", 503)

    grace = status.interval_seconds * 2 + 120
    if status.age_seconds is not None and status.age_seconds > grace:
        return text(
            f"FAIL no successful sync in {int(status.age_seconds)}s (grace {grace}s); "
            f"last: {status.last_message}",
            503,
        )
    last = status.last_run_iso or "pending first cycle"
    return text(f"OK {subscriptions} subscription(s); {status.last_message}; last run {last}")
