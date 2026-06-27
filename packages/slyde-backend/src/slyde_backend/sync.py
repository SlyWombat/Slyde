"""Sync service: Immich (or direct uploads) -> image pipeline -> frame.

- One-off sync: add assets to a target album (no removals).
- Subscription sync: mirror an Immich album 1:1 to a frame album — add new items, remove departed
  ones — and keep doing so on a schedule.

Durable state is recorded only *after* an upload succeeds, and assets already on a frame are
skipped *without* re-downloading, so periodic re-syncs are cheap and interrupted syncs are safe.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable

from .config import Settings
from .frames import FrameService
from .imaging import prepare_for_frame
from .immich import ImmichAsset, ImmichClient
from .naming import dest_name_for  # canonical Immich dest_name (#61), used below
from .schemas import SyncItem, SyncRequest, SyncResult
from .store import Store, SyncedPhoto

_log = logging.getLogger(__name__)


def _chunked(items: list[ImmichAsset], size: int) -> list[list[ImmichAsset]]:
    """Split ``items`` into chunks of ``size`` (so a large album streams with bounded memory)."""
    size = max(1, size)
    return [items[i : i + size] for i in range(0, len(items), size)]


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

    async def _canvas_for(self, host: str) -> tuple[int, int]:
        """The frame's own pixel canvas (Width x Height as it reports), so prepared images match
        its resolution/orientation exactly. Falls back to the configured FRAME_CANVAS."""
        try:
            cfg = await self._frame.get_config(host)
            width, height = int(cfg.get("Width", 0) or 0), int(cfg.get("Height", 0) or 0)
            if width > 0 and height > 0:
                return width, height
        except Exception:  # any read failure just falls back to the default canvas
            _log.warning("could not read canvas from %s; using configured default", host)
        return self._settings.canvas

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

    async def _prepare_one(
        self,
        client: ImmichClient,
        host: str,
        asset: ImmichAsset,
        result: SyncResult,
        canvas: tuple[int, int],
    ) -> tuple[bytes, str, tuple[str, str]] | None:
        """Fetch + prepare a single asset not already on ``host``; return (bytes, dest, (id, hash))
        or None (skipped/failed, counted on ``result``). One image in flight → bounded memory."""
        if asset.type != "IMAGE":
            result.skipped += 1
            return None
        if self._store.get(host, asset.id) is not None:
            result.skipped += 1  # already on the frame — no re-download
            return None
        dest = dest_name_for(asset.file_name, asset.id)
        try:
            source = await client.asset_bytes(asset.id, self._settings.immich_asset_size)
            prepared = await asyncio.to_thread(
                prepare_for_frame,
                source,
                canvas,
                fit=self._settings.frame_fit,
                crop_tolerance=self._settings.frame_crop_tolerance,
            )
        except Exception as exc:
            result.failed += 1
            result.items.append(
                SyncItem(asset_id=asset.id, dest_name=dest, status="failed", detail=str(exc)[:200])
            )
            return None
        return prepared, dest, (asset.id, hashlib.sha256(source).hexdigest())

    def _recorder(
        self,
        host: str,
        meta: dict[str, tuple[str, str]],
        album_id: str | None,
        done: set[str],
        result: SyncResult,
    ) -> Callable[[str], None]:
        def on_uploaded(dest: str) -> None:
            asset_id, digest = meta[dest]
            self._store.upsert(
                SyncedPhoto(
                    host=host,
                    asset_id=asset_id,
                    dest_name=dest,
                    content_hash=digest,
                    album_id=album_id,
                )
            )
            done.add(dest)
            result.uploaded = len(done)  # live progress for background jobs

        return on_uploaded

    @staticmethod
    def _record_outcomes(
        result: SyncResult,
        prepared_dests: list[str],
        meta: dict[str, tuple[str, str]],
        done: set[str],
    ) -> None:
        for dest in prepared_dests:
            asset_id = meta[dest][0]
            if dest in done:
                result.items.append(SyncItem(asset_id=asset_id, dest_name=dest, status="uploaded"))
            else:
                result.items.append(
                    SyncItem(
                        asset_id=asset_id,
                        dest_name=dest,
                        status="failed",
                        detail="upload did not complete",
                    )
                )
        result.uploaded = len(done)
        result.failed += len(prepared_dests) - len(done)

    # -- one-off sync ---------------------------------------------------------
    async def sync(
        self, host: str, req: SyncRequest, *, result: SyncResult | None = None
    ) -> SyncResult:
        """Add Immich assets to ``host`` (and optionally ``req.target_album``). No removals.

        ``result`` may be supplied so a background job can observe live progress.
        """
        result = result if result is not None else SyncResult()
        album_id = req.target_album or req.album_id
        canvas = await self._canvas_for(host)
        done: set[str] = set()
        meta: dict[str, tuple[str, str]] = {}
        prepared_dests: list[str] = []
        recorder = self._recorder(host, meta, album_id, done, result)
        async with self._immich_factory() as client:
            assets = await self._assets_for(client, req)
            result.total = len(assets)
            # Stream in bounded chunks: prepare a chunk, upload it, move on — so only ~chunk_size
            # images are ever in memory and `uploaded` ticks live from the first chunk (#57).
            for batch in _chunked(assets, self._settings.sync_chunk_size):
                to_upload: list[tuple[bytes, str]] = []
                for asset in batch:
                    result.prepared += 1
                    one = await self._prepare_one(client, host, asset, result, canvas)
                    if one is not None:
                        data, dest, m = one
                        to_upload.append((data, dest))
                        meta[dest] = m
                        prepared_dests.append(dest)
                if not to_upload:
                    continue
                try:
                    await self._frame.upload_images(host, to_upload, req.target_album, recorder)
                except Exception:  # partial progress preserved via the recorder
                    _log.exception("frame upload interrupted for %s", host)
        self._record_outcomes(result, prepared_dests, meta, done)
        return result

    async def remove(self, host: str, filename: str) -> None:
        await self._frame.delete_photo(host, filename)
        self._store.delete_by_dest(host, filename)
