"""Frame info, settings, and display controls."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..frames import FrameUnavailable
from ..schemas import ConfigPatch, FrameInfo
from .deps import FrameDep

router = APIRouter(prefix="/frame", tags=["frame"])


@router.get("", response_model=FrameInfo)
async def frame_info(frame: FrameDep) -> FrameInfo:
    try:
        host = await frame.resolve_host()
        config = await frame.get_config()
    except FrameUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    config.pop("WiFiSSID", None)  # never expose the device's Wi-Fi credentials
    config.pop("WiFiPSWD", None)
    return FrameInfo(host=host, config=config)


@router.patch("/config", response_model=FrameInfo)
async def update_config(patch: ConfigPatch, frame: FrameDep) -> FrameInfo:
    try:
        host = await frame.resolve_host()
        config = await frame.update_config(patch.patch())
    except FrameUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    config.pop("WiFiSSID", None)
    config.pop("WiFiPSWD", None)
    return FrameInfo(host=host, config=config)


@router.post("/next", status_code=204)
async def next_image(frame: FrameDep) -> None:
    await frame.next_image()


@router.post("/previous", status_code=204)
async def previous_image(frame: FrameDep) -> None:
    await frame.previous_image()
