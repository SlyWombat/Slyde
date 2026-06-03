"""Frame discovery, selection, and management (host-scoped)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile

from ..frames import FrameUnavailable
from ..schemas import (
    ConfigPatch,
    CreateAlbumRequest,
    FrameAlbum,
    FrameInfo,
    FrameSummary,
    SubscribeRequest,
    Subscription,
    SyncRequest,
    SyncResult,
)
from .deps import FrameDep, SettingsDep, SyncDep

router = APIRouter(prefix="/frames", tags=["frames"])

_WIFI_KEYS = ("WiFiSSID", "WiFiPSWD")


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


@router.post("/{host}/next", status_code=204)
async def next_image(host: str, frame: FrameDep) -> None:
    await frame.next_image(host)


@router.post("/{host}/previous", status_code=204)
async def previous_image(host: str, frame: FrameDep) -> None:
    await frame.previous_image(host)


@router.get("/{host}/albums", response_model=list[FrameAlbum])
async def list_albums(host: str, frame: FrameDep) -> list[FrameAlbum]:
    try:
        data = await frame.get_album_data(host)
    except FrameUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return [
        FrameAlbum(
            name=a.name,
            display_name=a.display_name,
            reserved=a.reserved,
            image_count=len(a.images),
            images=a.images,
        )
        for a in data.albums
    ]


@router.post("/{host}/albums", response_model=list[FrameAlbum], status_code=201)
async def create_album(host: str, body: CreateAlbumRequest, frame: FrameDep) -> list[FrameAlbum]:
    data = await frame.create_album(host, body.name)
    return [
        FrameAlbum(
            name=a.name,
            display_name=a.display_name,
            reserved=a.reserved,
            image_count=len(a.images),
            images=a.images,
        )
        for a in data.albums
    ]


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
    if not request.album_id and not request.asset_ids:
        raise HTTPException(status_code=400, detail="provide album_id and/or asset_ids")
    return await syncer.sync(host, request)


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


@router.post("/{host}/subscriptions", response_model=SyncResult)
async def subscribe(host: str, body: SubscribeRequest, syncer: SyncDep) -> SyncResult:
    """Mirror an Immich album to a frame album and keep it in sync (an initial sync runs now)."""
    return await syncer.subscribe(host, body.album_id, body.target_album)


@router.delete("/{host}/subscriptions/{album_id}", status_code=204)
async def unsubscribe(host: str, album_id: str, syncer: SyncDep) -> None:
    if not syncer.unsubscribe(host, album_id):
        raise HTTPException(status_code=404, detail="not a subscription")
