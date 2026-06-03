"""Browse the configured Immich library."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from ..immich import ImmichError
from ..schemas import Album, Asset
from .deps import SettingsDep, get_immich_factory

router = APIRouter(prefix="/immich", tags=["immich"])

ImmichFactory = Annotated[object, Depends(get_immich_factory)]


def _require_immich(settings: SettingsDep) -> None:
    if not (settings.immich_base_url and settings.immich_api_key):
        raise HTTPException(status_code=503, detail="Immich is not configured")


@router.get("/albums", response_model=list[Album])
async def list_albums(settings: SettingsDep, factory: ImmichFactory) -> list[Album]:
    _require_immich(settings)
    try:
        async with factory() as client:  # type: ignore[operator]
            albums = await client.list_albums()
    except ImmichError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [Album(id=a.id, name=a.name, asset_count=a.asset_count) for a in albums]


@router.get("/albums/{album_id}/assets", response_model=list[Asset])
async def album_assets(album_id: str, settings: SettingsDep, factory: ImmichFactory) -> list[Asset]:
    _require_immich(settings)
    try:
        async with factory() as client:  # type: ignore[operator]
            assets = await client.album_assets(album_id)
    except ImmichError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [Asset(id=a.id, file_name=a.file_name, type=a.type) for a in assets]
