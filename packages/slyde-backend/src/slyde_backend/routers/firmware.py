"""Firmware/app update registry + artifact serving."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from ..firmware import FirmwareError, FirmwareService
from ..schemas import FirmwareInfo, FirmwareTrackInfo
from .deps import FirmwareDep, SettingsDep

router = APIRouter(prefix="/firmware", tags=["firmware"])


def _info(firmware: FirmwareService, repo: str, track: str) -> FirmwareInfo:
    return FirmwareInfo(
        repo=repo,
        track=track,
        tracks=[
            FirmwareTrackInfo(track=t.track, version=t.version, md5=t.md5)
            for t in firmware.tracks()
        ],
    )


@router.get("", response_model=FirmwareInfo)
async def list_firmware(firmware: FirmwareDep, settings: SettingsDep) -> FirmwareInfo:
    return _info(firmware, settings.firmware_repo, settings.firmware_track)


@router.post("/check", response_model=FirmwareInfo)
async def check_firmware(firmware: FirmwareDep, settings: SettingsDep) -> FirmwareInfo:
    """Refresh the registry from the configured repo's latest release."""
    try:
        await firmware.check()
    except FirmwareError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _info(firmware, settings.firmware_repo, settings.firmware_track)


@router.get("/serve/{track}")
async def serve_firmware(track: str, firmware: FirmwareDep) -> Response:
    """Serve an update bundle to a frame, verifying its md5 before each serve."""
    try:
        data = await firmware.serve(track)
    except FirmwareError as exc:
        code = 404 if "unknown" in str(exc) else 502
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return Response(content=data, media_type="application/zip")
