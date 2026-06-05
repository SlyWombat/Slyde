"""Frame discovery, selection, and management (host-scoped)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile

from ..backends import available_backends, get_backend
from ..frames import FrameUnavailable
from ..jobs import SyncJob
from ..library import LibraryItem
from ..schemas import (
    CapabilitiesInfo,
    ConfigPatch,
    CreateAlbumRequest,
    CurrentImage,
    DeliverySummary,
    FrameAlbum,
    FrameDetailInfo,
    FrameInfo,
    FrameStatus,
    FrameSummary,
    FrameUpdate,
    LibraryItemModel,
    LibraryPhoto,
    LibraryView,
    RegisterFrameRequest,
    SubscribeRequest,
    Subscription,
    SyncJobInfo,
    SyncRequest,
    SyncResult,
)
from ..serving import resolve_or_register_served_frame
from .deps import FirmwareDep, FrameDep, JobsDep, SettingsDep, StoreDep, SyncDep

router = APIRouter(prefix="/frames", tags=["frames"])

_WIFI_KEYS = ("WiFiSSID", "WiFiPSWD")


def _job_info(job: SyncJob) -> SyncJobInfo:
    return SyncJobInfo(
        id=job.id,
        host=job.host,
        label=job.label,
        status=job.status,
        error=job.error,
        result=job.result,
    )


def _strip_wifi(config: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in config.items() if k not in _WIFI_KEYS}


@router.get("", response_model=list[FrameSummary])
async def list_frames(frame: FrameDep, settings: SettingsDep) -> list[FrameSummary]:
    """Discover frames on the LAN (the start screen). Includes a configured FRAME_HOST if set."""
    found = await frame.discover_frames()
    summaries = [
        FrameSummary(
            name=f.name,
            ip=f.ip,
            mac=f.mac,
            softver=f.softver,
            hardver=f.hardver,
            size=f.size,
            orientation=f.orientation,
            guid=f.guid,
        )
        for f in found
    ]
    have = {s.ip for s in summaries}
    for host in settings.configured_hosts:
        if host in have:
            continue
        try:
            cfg = await frame.get_config(host)
        except (FrameUnavailable, OSError):
            continue
        summaries.append(
            FrameSummary(
                name=str(cfg.get("Name", host)),
                ip=host,
                softver=float(cfg.get("SoftwareVersion", 0) or 0),
                size=int(cfg.get("ScreenSize", 0) or 0),
                orientation=str(cfg.get("Orientation", "")),
                guid=str(cfg.get("GUID", "")),
            )
        )
        have.add(host)
    return summaries


@router.get("/status", response_model=list[FrameStatus])
async def frames_status(store: StoreDep) -> list[FrameStatus]:
    """Frame-agnostic, read-only status of every known frame across backends (#24).

    Built from the registry + delivery queue — no connection to the frame is made, so it works the
    same for offline/served frames. This is the disconnected "current state" view the UI reads.
    """
    return [
        FrameStatus(
            id=f.id,
            backend=f.backend,
            interaction=f.interaction,
            name=f.name,
            last_seen=f.last_seen,
            deliveries=DeliverySummary(**store.delivery_summary(f.id)),
        )
        for f in store.list_frames()
    ]


def _served_backends() -> list[str]:
    """Names of backends whose frames poll us (served) — config-driven, never hardcoded."""
    return [n for n in available_backends() if get_backend(n).capabilities.interaction == "served"]


@router.post("/register", response_model=FrameStatus, status_code=201)
async def register_frame(body: RegisterFrameRequest, store: StoreDep) -> FrameStatus:
    """Onboard a served/cloud frame by its frame-code before it has ever polled (#29), so the UI can
    show it in status and curate to it immediately. Idempotent — reuses the serving registration."""
    served = _served_backends()
    backend_name = body.backend or (served[0] if len(served) == 1 else "")
    if not backend_name:
        raise HTTPException(
            status_code=422,
            detail=f"backend required; served backends: {', '.join(served) or 'none'}",
        )
    if backend_name not in served:
        raise HTTPException(status_code=422, detail=f"{backend_name!r} is not a served backend")

    frame = resolve_or_register_served_frame(store, backend_name, body.frame_code)
    if body.name and body.name != frame.name:  # apply a chosen display name
        store.upsert_frame(replace(frame, name=body.name))
        frame = store.get_frame(frame.id) or frame
    return FrameStatus(
        id=frame.id,
        backend=frame.backend,
        interaction=frame.interaction,
        name=frame.name,
        last_seen=frame.last_seen,
        deliveries=DeliverySummary(**store.delivery_summary(frame.id)),
    )


@router.put("/{frame_id}/library", status_code=202)
async def set_library(
    frame_id: str, items: list[LibraryItemModel], request: Request
) -> dict[str, int]:
    """Set a frame's curated photo set, queue guaranteed delivery, and reconcile now (#23/#25/#26).

    The backend owns sync from here: it durably queues a delivery per photo and drains the queue
    (served frames -> prepared image cached, ready to pull; connected frames -> pushed, retried if
    offline). Returns how many were queued + the delivery outcome counts.
    """
    library = request.app.state.library
    delivery = request.app.state.delivery_service
    desired = [LibraryItem(i.asset_id, i.dest_name or f"{i.asset_id}.jpg") for i in items]
    library.set_desired(frame_id, desired)
    now = datetime.now(UTC)
    queued = delivery.enqueue_desired(frame_id, now=now)
    counts = await delivery.reconcile(now=now)
    return {"queued": queued, **counts}


@router.get("/{frame_id}/library", response_model=LibraryView)
async def frame_library(frame_id: str, store: StoreDep) -> LibraryView:
    """A frame's curated set + each photo's delivery state (#28); also for offline/served frames."""
    states = {d.key: d.state for d in store.list_deliveries(frame_id)}
    items = [
        LibraryPhoto(asset_id=aid, dest_name=dest, state=states.get(dest, "unknown"))
        for aid, dest in store.list_library(frame_id)
    ]
    return LibraryView(items=items, deliveries=DeliverySummary(**store.delivery_summary(frame_id)))


@router.get("/{frame_id}/detail", response_model=FrameDetailInfo)
async def frame_detail(frame_id: str, store: StoreDep) -> FrameDetailInfo:
    """Registry detail + backend capabilities for a frame by id (#28)."""
    f = store.get_frame(frame_id)
    if f is None:
        raise HTTPException(status_code=404, detail="frame not found")
    caps = get_backend(f.backend).capabilities
    return FrameDetailInfo(
        id=f.id,
        backend=f.backend,
        interaction=f.interaction,
        name=f.name,
        address=f.address,
        frame_code=f.frame_code,
        last_seen=f.last_seen,
        capabilities=CapabilitiesInfo(**vars(caps)),
    )


@router.get("/{host}", response_model=FrameInfo)
async def frame_info(host: str, frame: FrameDep) -> FrameInfo:
    try:
        config = await frame.get_config(host)
    except FrameUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FrameInfo(host=host, config=_strip_wifi(config))


@router.patch("/{host}/config", response_model=FrameInfo)
async def update_config(host: str, patch: ConfigPatch, frame: FrameDep) -> FrameInfo:
    config = await frame.update_config(host, patch.patch())
    return FrameInfo(host=host, config=_strip_wifi(config))


@router.get("/{host}/current", response_model=CurrentImage)
async def current_image(host: str, frame: FrameDep) -> CurrentImage:
    """The image the frame is currently displaying (for a live preview)."""
    return CurrentImage(image=(await frame.get_current_image(host)) or None)


@router.post("/{host}/next", status_code=204)
async def next_image(host: str, frame: FrameDep) -> None:
    await frame.next_image(host)


@router.post("/{host}/previous", status_code=204)
async def previous_image(host: str, frame: FrameDep) -> None:
    await frame.previous_image(host)


def _albums(data: object) -> list[FrameAlbum]:
    return [
        FrameAlbum(
            name=a.name,
            display_name=a.display_name,
            reserved=a.reserved,
            image_count=len(a.images),
            images=a.images,
        )
        for a in data.albums  # type: ignore[attr-defined]
    ]


@router.get("/{host}/albums", response_model=list[FrameAlbum])
async def list_albums(host: str, frame: FrameDep) -> list[FrameAlbum]:
    try:
        return _albums(await frame.get_album_data(host))
    except FrameUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{host}/albums", response_model=list[FrameAlbum], status_code=201)
async def create_album(host: str, body: CreateAlbumRequest, frame: FrameDep) -> list[FrameAlbum]:
    return _albums(await frame.create_album(host, body.name))


@router.delete("/{host}/albums/{name}", response_model=list[FrameAlbum])
async def delete_album(host: str, name: str, frame: FrameDep) -> list[FrameAlbum]:
    """Delete a folder from the frame (reserved folders can't be deleted; photos are kept)."""
    return _albums(await frame.delete_album(host, name))


@router.delete("/{host}/albums/{name}/images/{filename}", response_model=list[FrameAlbum])
async def remove_from_album(
    host: str, name: str, filename: str, frame: FrameDep
) -> list[FrameAlbum]:
    """Remove a photo from a folder without deleting it from the frame."""
    return _albums(await frame.remove_from_album(host, name, filename))


@router.get("/{host}/thumbnail/{image}")
async def thumbnail(host: str, image: str, frame: FrameDep) -> Response:
    """Proxy a thumbnail (PNG) for an image already on the frame."""
    try:
        data = await frame.get_thumbnail(host, image)
    except FrameUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(content=data, media_type="image/png")


@router.post("/{host}/sync", response_model=SyncResult)
async def sync(host: str, request: SyncRequest, syncer: SyncDep) -> SyncResult:
    """Synchronous sync (small/selected sets). For whole albums prefer the job endpoint below."""
    if not request.album_id and not request.asset_ids:
        raise HTTPException(status_code=400, detail="provide album_id and/or asset_ids")
    return await syncer.sync(host, request)


@router.post("/{host}/sync/jobs", response_model=SyncJobInfo, status_code=202)
async def start_sync_job(
    host: str, request: SyncRequest, syncer: SyncDep, jobs: JobsDep
) -> SyncJobInfo:
    """Start a background sync (won't block the request); poll the GET endpoint for progress."""
    if not request.album_id and not request.asset_ids:
        raise HTTPException(status_code=400, detail="provide album_id and/or asset_ids")
    label = request.target_album or request.album_id or "selected photos"
    job = jobs.start(host, label, lambda result: syncer.sync(host, request, result=result))
    return _job_info(job)


@router.get("/{host}/sync/jobs/{job_id}", response_model=SyncJobInfo)
async def get_sync_job(host: str, job_id: str, jobs: JobsDep) -> SyncJobInfo:
    job = jobs.get(job_id)
    if job is None or job.host != host:
        raise HTTPException(status_code=404, detail="no such job")
    return _job_info(job)


@router.post("/{host}/upload", response_model=SyncResult)
async def upload(
    host: str,
    syncer: SyncDep,
    files: list[UploadFile] = File(...),
    target_album: str | None = Form(None),
) -> SyncResult:
    """Direct upload of image files (no Immich), optionally into a target album."""
    items = [(f.filename or "upload.jpg", await f.read()) for f in files]
    return await syncer.upload_files(host, items, target_album)


@router.delete("/{host}/photos/{filename}", status_code=204)
async def delete_photo(host: str, filename: str, syncer: SyncDep) -> None:
    await syncer.remove(host, filename)


@router.post("/{host}/update", response_model=FrameUpdate)
async def update_frame(
    host: str, request: Request, frame: FrameDep, firmware: FirmwareDep, settings: SettingsDep
) -> FrameUpdate:
    """Tell the frame to pull + apply the registry-pinned update bundle for its track."""
    track = settings.firmware_track
    entry = firmware.get(track)
    if entry is None:
        raise HTTPException(
            status_code=409, detail="no firmware available; check for updates first"
        )
    base = settings.manager_base_url or str(request.base_url).rstrip("/")
    url = f"{base}/api/firmware/serve/{track}"
    await frame.update_firmware(host, url, entry.md5)
    return FrameUpdate(sent=True, track=track, version=entry.version, url=url)


# -- subscriptions (keep an Immich album mirrored 1:1 to a frame album) --------
@router.get("/{host}/subscriptions", response_model=list[Subscription])
async def list_subscriptions(host: str, syncer: SyncDep) -> list[Subscription]:
    return [
        Subscription(
            immich_album_id=s.immich_album_id,
            target_album=s.target_album,
            last_synced_at=s.last_synced_at,
            last_result=s.last_result,
        )
        for s in syncer.list_subscriptions(host)
    ]


@router.post("/{host}/subscriptions", response_model=SyncJobInfo, status_code=202)
async def subscribe(
    host: str, body: SubscribeRequest, syncer: SyncDep, jobs: JobsDep
) -> SyncJobInfo:
    """Mirror an Immich album to a frame album and keep it in sync.

    The initial mirror runs as a background job (poll the sync-job endpoint for progress); the
    subscription itself is recorded as part of that job.
    """
    job = jobs.start(
        host,
        body.target_album,
        lambda result: syncer.subscribe(host, body.album_id, body.target_album, result=result),
    )
    return _job_info(job)


@router.delete("/{host}/subscriptions/{album_id}", status_code=204)
async def unsubscribe(host: str, album_id: str, syncer: SyncDep) -> None:
    if not syncer.unsubscribe(host, album_id):
        raise HTTPException(status_code=404, detail="not a subscription")
