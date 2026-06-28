"""Frame discovery, selection, and management (host-scoped)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)

from ..backends import available_backends, get_backend
from ..delivery_service import DeliveryService
from ..frame_import import import_frame_photos
from ..frames import FrameUnavailable
from ..immich import ImmichError
from ..jobs import SyncJob
from ..library import LibraryItem
from ..naming import dest_name_for
from ..processing import prepare, profile_for
from ..schemas import (
    AddFrameRequest,
    CapabilitiesInfo,
    ConfigPatch,
    CurrentImage,
    DeliverySummary,
    FrameDetailInfo,
    FrameInfo,
    FrameStatus,
    FrameSummary,
    FrameUpdate,
    LibraryItemModel,
    LibraryPhoto,
    LibraryView,
    RegisterFrameRequest,
    RenameFrameRequest,
    SubscribeRequest,
    Subscription,
    SyncJobInfo,
    SyncResult,
)
from ..serving import resolve_or_register_served_frame
from ..uploads import ingest_upload
from .deps import (
    FirmwareDep,
    FrameDep,
    JobsDep,
    SettingsDep,
    StoreDep,
    get_immich_factory,
)

router = APIRouter(prefix="/frames", tags=["frames"])

_log = logging.getLogger(__name__)

# An Immich client factory (async context manager) from app state, for read-only asset fetches.
ImmichFactory = Annotated[Any, Depends(get_immich_factory)]

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


def _to_float(value: object) -> float:
    """Coerce a frame-reported version to float, tolerating semver/garbage. A discovery sweep must
    never 500 because one frame reports a non-float SoftwareVersion (e.g. a soft-frame's '0.1.2') —
    mirrors memento_core's discovery parsing (#54)."""
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


@router.get("", response_model=list[FrameSummary])
async def list_frames(frame: FrameDep, settings: SettingsDep) -> list[FrameSummary]:
    """List frames on the LAN (the onboarding picker). Discover-only — nothing is added to the
    registry here; the user adds a frame explicitly via POST /add. Includes a configured host."""
    found = await frame.discover_frames(register=False)
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
            cfg = await frame.get_config(host, register=False)
        except (FrameUnavailable, OSError):
            continue
        summaries.append(
            FrameSummary(
                name=str(cfg.get("Name", host)),
                ip=host,
                softver=_to_float(cfg.get("SoftwareVersion", 0)),
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


@router.post("/scan", response_model=list[FrameSummary])
async def scan_frames(frame: FrameDep) -> list[FrameSummary]:
    """Actively scan the LAN for connected frames (the manual 'Scan' button) — TCP-probe the control
    port across the subnet and read each responder's config. **Discover-only**: returns candidates
    and adds nothing; the user adds one explicitly via POST /add. Manual-only (#58)."""
    return [
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
        for f in await frame.scan_for_frames()
    ]


@router.post("/add", response_model=FrameStatus, status_code=201)
async def add_frame(body: AddFrameRequest, frame: FrameDep, store: StoreDep) -> FrameStatus:
    """Add a discovered/scanned connected frame to the registry by its host/IP — the explicit
    "Add" the onboarding picker calls (discovery itself no longer auto-adds)."""
    added = await frame.add_frame(body.host)
    if added is None:
        raise HTTPException(status_code=404, detail="frame not reachable at that host")
    return FrameStatus(
        id=added.id,
        backend=added.backend,
        interaction=added.interaction,
        name=added.name,
        last_seen=added.last_seen,
        deliveries=DeliverySummary(**store.delivery_summary(added.id)),
    )


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
    _log.info("registered served frame %s (backend %s)", frame.id, backend_name)
    return FrameStatus(
        id=frame.id,
        backend=frame.backend,
        interaction=frame.interaction,
        name=frame.name,
        last_seen=frame.last_seen,
        deliveries=DeliverySummary(**store.delivery_summary(frame.id)),
    )


async def _drain_delivery(delivery: DeliveryService) -> None:
    """Drain the delivery queue off the request path (fire-and-forget); errors are swallowed and
    surfaced via the queue's own retry/state — the request must never block on delivery (#50)."""
    with contextlib.suppress(Exception):
        await delivery.drain(now=datetime.now(UTC))


@router.put("/{frame_id}/library", status_code=202)
async def set_library(
    frame_id: str,
    items: list[LibraryItemModel],
    request: Request,
    background: BackgroundTasks,
) -> dict[str, int]:
    """Set a frame's curated photo set and durably queue delivery of the delta, then return
    immediately (#23/#25/#26/#50).

    The backend owns sync from here: it queues a delivery per *new/changed* photo (#46) and drains
    the queue in the background — after the response and on the scheduler's tight timer (#47) — so a
    large 'Set library' (e.g. a whole 1000-photo album, #48) never blocks the request. Live progress
    shows in the Activity view, not in this call. Returns how many deliveries were queued.
    """
    library = request.app.state.library
    delivery = request.app.state.delivery_service
    store = request.app.state.store
    # Canonical dest_name (#61), grandfathered: an explicit dest_name wins; else an asset already
    # in the library keeps its name (no re-key/re-push); else a new asset gets the readable slug
    # from its Immich filename (asset id as fallback). Folder is grandfathered the same way.
    existing = {aid: (dest, folder) for aid, dest, _src, folder in store.list_library(frame_id)}

    def _dest(i: LibraryItemModel) -> str:
        prior = existing.get(i.asset_id)
        return (
            i.dest_name
            or (prior[0] if prior else None)
            or dest_name_for(i.file_name or i.asset_id, i.asset_id)
        )

    def _folder(i: LibraryItemModel) -> str:
        if i.folder is not None:
            return i.folder
        prior = existing.get(i.asset_id)
        return prior[1] if prior else ""

    desired = [LibraryItem(i.asset_id, _dest(i), folder=_folder(i)) for i in items]
    library.set_desired(frame_id, desired)
    queued = delivery.enqueue_desired(frame_id, now=datetime.now(UTC))
    background.add_task(_drain_delivery, delivery)  # kick delivery without blocking the response
    _log.info("frame %s library set: %d desired, %d newly queued", frame_id, len(desired), queued)
    return {"queued": queued}


@router.get("/{frame_id}/library", response_model=LibraryView)
async def frame_library(frame_id: str, store: StoreDep) -> LibraryView:
    """A frame's curated set + each photo's delivery state (#28); also for offline/served frames."""
    if store.get_frame(frame_id) is None:  # 404 for unknown/detached frames, mirroring DELETE (#53)
        raise HTTPException(status_code=404, detail="frame not found")
    states = {d.key: d.state for d in store.list_deliveries(frame_id)}
    items = [
        LibraryPhoto(asset_id=aid, dest_name=dest, folder=folder, state=states.get(dest, "unknown"))
        for aid, dest, _source, folder in store.list_library(frame_id)
    ]
    return LibraryView(items=items, deliveries=DeliverySummary(**store.delivery_summary(frame_id)))


@router.delete("/{frame_id}/library/{asset_id}", status_code=204)
async def remove_library_item(frame_id: str, asset_id: str, request: Request) -> None:
    """Remove one photo from a frame's library (any source) + its cached image and queued delivery
    (#61). The device file (connected) is removed separately via DELETE /photos."""
    store = request.app.state.store
    dest = next((d for a, d, _s, _f in store.list_library(frame_id) if a == asset_id), None)
    if dest is None:
        raise HTTPException(status_code=404, detail="not in the library")
    store.delete_library_item_by_dest(frame_id, dest)
    request.app.state.image_cache.delete(frame_id, dest)
    store.delete_delivery(frame_id, dest)


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


@router.get("/{frame_id}/preview/{asset_id}")
async def frame_preview(
    frame_id: str,
    asset_id: str,
    settings: SettingsDep,
    store: StoreDep,
    factory: ImmichFactory,
) -> Response:
    """Render how an Immich asset will look on this frame's panel (#30): the frame's processing
    profile — full-colour LCD (JPEG) vs e-ink Spectra-6 palette + dither (PNG) — applied to the
    asset, reusing the exact fit + quantize + dither pipeline delivery uses. Powers the curation
    preview (#39). A preview is a *viewable* image, so for an e-ink frame whose delivery format is
    the packed panel BMP we emit the equivalent palette PNG (same appearance, browser-displayable).
    """
    frame = store.get_frame(frame_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="frame not found")
    if not (settings.immich_base_url and settings.immich_api_key):
        raise HTTPException(status_code=503, detail="Immich is not configured")
    profile = profile_for(frame, settings, canvas=settings.canvas)
    if profile.encoder != "auto":  # preview as a viewable image, not the raw panel bytes
        profile = replace(profile, encoder="auto")
    try:
        async with factory() as client:
            source = await client.asset_bytes(asset_id, settings.immich_asset_size)
    except ImmichError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    prepared = await asyncio.to_thread(prepare, source, profile)
    media = "image/png" if profile.color_model == "epaper" else "image/jpeg"
    return Response(content=prepared, media_type=media)


@router.patch("/{frame_id}", response_model=FrameStatus)
async def rename_frame(frame_id: str, body: RenameFrameRequest, store: StoreDep) -> FrameStatus:
    """Set a frame's registry display name, for any backend (#55) — the clean rename path (served
    frames previously had to re-register). 404 if the frame isn't registered."""
    if not store.rename_frame(frame_id, body.name):
        raise HTTPException(status_code=404, detail="frame not found")
    f = store.get_frame(frame_id)
    assert f is not None  # just renamed it
    _log.info("renamed frame %s -> %r", frame_id, body.name)
    return FrameStatus(
        id=f.id,
        backend=f.backend,
        interaction=f.interaction,
        name=f.name,
        last_seen=f.last_seen,
        deliveries=DeliverySummary(**store.delivery_summary(f.id)),
    )


@router.delete("/{frame_id}", status_code=204)
async def deregister_frame(frame_id: str, request: Request, store: StoreDep) -> Response:
    """Deregister a frame: drop it from the registry and purge everything keyed to it — delivery
    queue, curated library, cached prepared images, and (for connected frames) synced photos +
    album subscriptions. The physical frame is NOT touched; a connected frame still on the LAN can
    be re-added from discovery. 404 if the frame isn't registered."""
    if not store.purge_frame(frame_id):
        raise HTTPException(status_code=404, detail="frame not found")
    request.app.state.image_cache.clear(frame_id)
    _log.info("deregistered frame %s", frame_id)
    return Response(status_code=204)


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


@router.get("/{host}/thumbnail/{image}")
async def thumbnail(host: str, image: str, frame: FrameDep) -> Response:
    """Proxy a thumbnail (PNG) for an image already on the frame."""
    try:
        data = await frame.get_thumbnail(host, image)
    except FrameUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(content=data, media_type="image/png")


@router.post("/{frame_id}/import/jobs", response_model=SyncJobInfo, status_code=202)
async def start_frame_import(
    frame_id: str,
    request: Request,
    frame: FrameDep,
    store: StoreDep,
    settings: SettingsDep,
    jobs: JobsDep,
) -> SyncJobInfo:
    """Pull the photos already ON a connected frame into Slyde's library — gentle + idempotent.

    Reads the frame's albums, downloads each original off the device (serialized + paced so the
    frame keeps cycling), and ingests them as Slyde-owned, already-delivered library items. A
    background job; poll GET /{frame_id}/sync/jobs/{job_id} for progress.
    """
    f = store.get_frame(frame_id)
    if f is None:
        raise HTTPException(status_code=404, detail="frame not found")
    if f.interaction != "connected":
        raise HTTPException(status_code=422, detail="only connected frames hold photos to import")
    state = request.app.state

    def runner(result: SyncResult) -> object:
        return import_frame_photos(
            frame=f,
            frame_service=frame,
            settings=settings,
            image_cache=state.image_cache,
            asset_previews=state.asset_previews,
            uploads=state.uploads,
            library=state.library,
            store=store,
            result=result,
        )

    job = jobs.start(frame_id, f"Import from {f.name or frame_id}", runner)  # type: ignore[arg-type]
    return _job_info(job)


@router.get("/{host}/sync/jobs/{job_id}", response_model=SyncJobInfo)
async def get_sync_job(host: str, job_id: str, jobs: JobsDep) -> SyncJobInfo:
    job = jobs.get(job_id)
    if job is None or job.host != host:
        raise HTTPException(status_code=404, detail="no such job")
    return _job_info(job)


@router.post("/{frame_id}/upload")
async def upload(
    frame_id: str,
    request: Request,
    frame: FrameDep,
    store: StoreDep,
    settings: SettingsDep,
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    folder: str | None = Form(None),
) -> dict[str, int]:
    """Upload image files (not in Immich) straight into the frame's LIBRARY — first-class items that
    deliver like curated photos (connected push / served pull) and show in the Library tab. Optional
    folder (#61). Unifies on the curation engine; closes F4."""
    f = store.get_frame(frame_id)
    if f is None:
        raise HTTPException(status_code=404, detail="frame not found")
    # For a connected frame, prepare to its reported canvas if we can reach it; else the default.
    canvas: tuple[int, int] | None = None
    if f.interaction == "connected":
        with contextlib.suppress(Exception):
            cfg = await frame.get_config(frame_id)
            w, h = int(cfg.get("Width", 0) or 0), int(cfg.get("Height", 0) or 0)
            if w and h:
                canvas = (w, h)
    state = request.app.state
    for uf in files:
        data = await uf.read()
        await uf.close()
        await ingest_upload(
            frame=f,
            data=data,
            settings=settings,
            image_cache=state.image_cache,
            asset_previews=state.asset_previews,
            uploads=state.uploads,
            library=state.library,
            store=store,
            folder=folder or "",
            canvas=canvas,
        )
    background.add_task(_drain_delivery, state.delivery_service)
    return {"uploaded": len(files)}


@router.delete("/{host}/photos/{filename}", status_code=204)
async def delete_photo(host: str, filename: str, frame: FrameDep) -> None:
    """Delete a photo file from a connected frame's device storage (the library is managed via the
    library endpoints; this is the device-side delete the Library tab's remove uses)."""
    await frame.delete_photo(host, filename)


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


# -- keep-in-sync: bind a Library FOLDER to an Immich album, on the delivery queue (#62) --------
@router.get("/{frame_id}/subscriptions", response_model=list[Subscription])
async def list_subscriptions(frame_id: str, request: Request) -> list[Subscription]:
    return [
        Subscription(
            immich_album_id=s.immich_album_id,
            target_album=s.target_album,  # the bound Library folder
            last_synced_at=s.last_synced_at,
            last_result=s.last_result,
        )
        for s in request.app.state.folder_sync.list_bindings(frame_id)
    ]


@router.post("/{frame_id}/subscriptions", response_model=SyncJobInfo, status_code=202)
async def subscribe(
    frame_id: str, body: SubscribeRequest, request: Request, jobs: JobsDep
) -> SyncJobInfo:
    """Bind a Library folder (``target_album``) to an Immich album and keep it in sync — works for
    connected AND served frames (#62). The first reconcile runs as a background job (poll the
    sync-job endpoint); it sets the folder's library rows and queues the delta for delivery."""
    folder_sync = request.app.state.folder_sync
    job = jobs.start(
        frame_id,
        body.target_album,
        lambda result: folder_sync.bind(frame_id, body.album_id, body.target_album, result=result),
    )
    return _job_info(job)


@router.delete("/{frame_id}/subscriptions/{album_id}", status_code=204)
async def unsubscribe(frame_id: str, album_id: str, request: Request) -> None:
    if not request.app.state.folder_sync.unbind(frame_id, album_id):
        raise HTTPException(status_code=404, detail="not a binding")
