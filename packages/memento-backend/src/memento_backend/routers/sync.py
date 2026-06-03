"""Sync Immich photos to the frame, and manage what's on the frame."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..schemas import SyncedPhoto, SyncRequest, SyncResult
from .deps import StoreDep, SyncDep

router = APIRouter(tags=["sync"])


@router.post("/sync", response_model=SyncResult)
async def run_sync(request: SyncRequest, sync: SyncDep) -> SyncResult:
    if not request.album_id and not request.asset_ids:
        raise HTTPException(status_code=400, detail="provide album_id and/or asset_ids")
    return await sync.sync(request)


@router.get("/photos", response_model=list[SyncedPhoto])
async def list_photos(store: StoreDep) -> list[SyncedPhoto]:
    return [
        SyncedPhoto(
            asset_id=p.asset_id, dest_name=p.dest_name, album_id=p.album_id, synced_at=p.synced_at
        )
        for p in store.list_all()
    ]


@router.delete("/photos/{asset_id}", status_code=204)
async def remove_photo(asset_id: str, sync: SyncDep) -> None:
    if not await sync.remove(asset_id):
        raise HTTPException(status_code=404, detail="not a synced photo")
