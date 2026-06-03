"""Sync service: Immich (or direct uploads) -> image pipeline -> frame, with album assignment."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable

from .config import Settings
from .frames import FrameService
from .imaging import prepare_for_frame
from .immich import ImmichAsset, ImmichClient
from .schemas import SyncItem, SyncRequest, SyncResult
from .store import Store, SyncedPhoto

_MAX_NAME = 64  # frame filename limit (Cadre.Utils.VerifyFilename)


def dest_name_for(file_name: str, unique: str) -> str:
    """A frame-safe, unique .jpg filename (<= 64 chars) from a source name + a unique suffix."""
    stem = re.sub(r"[^a-z0-9]+", "-", file_name.rsplit(".", 1)[0].lower()).strip("-")
    suffix = f"-{unique[:8]}.jpg"
    return (stem[: _MAX_NAME - len(suffix)] or "photo") + suffix


class SyncService:
    def __init__(
        self,
        settings: Settings,
        frame: FrameService,
        store: Store,
        immich_factory: Callable[[], ImmichClient],
    ) -> None:
        self._settings = settings
        self._frame = frame
        self._store = store
        self._immich_factory = immich_factory

    async def _assets_for(self, client: ImmichClient, req: SyncRequest) -> list[ImmichAsset]:
        if req.album_id:
            assets = await client.album_assets(req.album_id)
            if req.asset_ids:
                wanted = set(req.asset_ids)
                assets = [a for a in assets if a.id in wanted]
            return assets
        if req.asset_ids:
            return [await client.get_asset(aid) for aid in req.asset_ids]
        return []

    async def sync(self, host: str, req: SyncRequest) -> SyncResult:
        """Sync Immich assets to ``host``, optionally adding them to ``req.target_album``."""
        result = SyncResult()
        canvas = self._settings.canvas
        to_upload: list[tuple[bytes, str]] = []
        async with self._immich_factory() as client:
            assets = await self._assets_for(client, req)
            for asset in assets:
                if asset.type != "IMAGE":
                    result.skipped += 1
                    result.items.append(
                        SyncItem(
                            asset_id=asset.id, dest_name="", status="skipped", detail="not an image"
                        )
                    )
                    continue
                dest = dest_name_for(asset.file_name, asset.id)
                try:
                    source = await client.asset_bytes(asset.id, self._settings.immich_asset_size)
                    digest = hashlib.sha256(source).hexdigest()
                    existing = self._store.get(asset.id)
                    if existing and existing.content_hash == digest:
                        result.skipped += 1
                        result.items.append(
                            SyncItem(
                                asset_id=asset.id,
                                dest_name=existing.dest_name,
                                status="skipped",
                                detail="unchanged",
                            )
                        )
                        continue
                    prepared = await asyncio.to_thread(prepare_for_frame, source, canvas)
                    to_upload.append((prepared, dest))
                    self._store.upsert(
                        SyncedPhoto(
                            asset_id=asset.id,
                            dest_name=dest,
                            content_hash=digest,
                            album_id=req.target_album or req.album_id,
                            synced_at="",
                        )
                    )
                    result.uploaded += 1
                    result.items.append(
                        SyncItem(asset_id=asset.id, dest_name=dest, status="uploaded")
                    )
                except Exception as exc:
                    result.failed += 1
                    result.items.append(
                        SyncItem(
                            asset_id=asset.id,
                            dest_name=dest,
                            status="failed",
                            detail=str(exc)[:200],
                        )
                    )
        if to_upload:
            await self._frame.upload_images(host, to_upload, req.target_album)
        return result

    async def upload_files(
        self, host: str, files: list[tuple[str, bytes]], target_album: str | None
    ) -> SyncResult:
        """Directly upload raw image files (no Immich), optionally into ``target_album``."""
        result = SyncResult()
        canvas = self._settings.canvas
        to_upload: list[tuple[bytes, str]] = []
        for file_name, raw in files:
            dest = dest_name_for(file_name, hashlib.sha256(raw).hexdigest())
            try:
                prepared = await asyncio.to_thread(prepare_for_frame, raw, canvas)
                to_upload.append((prepared, dest))
                result.uploaded += 1
                result.items.append(SyncItem(asset_id=file_name, dest_name=dest, status="uploaded"))
            except Exception as exc:
                result.failed += 1
                result.items.append(
                    SyncItem(
                        asset_id=file_name, dest_name=dest, status="failed", detail=str(exc)[:200]
                    )
                )
        if to_upload:
            await self._frame.upload_images(host, to_upload, target_album)
        return result

    async def remove(self, host: str, filename: str) -> None:
        await self._frame.delete_photo(host, filename)
        self._store.delete_by_dest(filename)
