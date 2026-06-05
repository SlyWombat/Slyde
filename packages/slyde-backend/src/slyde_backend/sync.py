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
import re
from collections.abc import Callable

from .config import Settings
from .frames import FrameService
from .imaging import prepare_for_frame
from .immich import ImmichAsset, ImmichClient
from .schemas import SyncItem, SyncRequest, SyncResult
from .store import Store, Subscription, SyncedPhoto

_MAX_NAME = 64  # frame filename limit (Cadre.Utils.VerifyFilename)
_log = logging.getLogger(__name__)


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

    async def _prepare_new(
        self,
        client: ImmichClient,
        host: str,
        assets: list[ImmichAsset],
        result: SyncResult,
        canvas: tuple[int, int],
    ) -> tuple[list[tuple[bytes, str]], dict[str, tuple[str, str]]]:
        """Fetch + prepare assets not already on ``host``; return (to_upload, dest->(id,hash))."""
        to_upload: list[tuple[bytes, str]] = []
        meta: dict[str, tuple[str, str]] = {}
        for asset in assets:
            if asset.type != "IMAGE":
                result.skipped += 1
                continue
            if self._store.get(host, asset.id) is not None:
                result.skipped += 1  # already on the frame — no re-download
                continue
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
                    SyncItem(
                        asset_id=asset.id, dest_name=dest, status="failed", detail=str(exc)[:200]
                    )
                )
                continue
            to_upload.append((prepared, dest))
            meta[dest] = (asset.id, hashlib.sha256(source).hexdigest())
        return to_upload, meta

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
        to_upload: list[tuple[bytes, str]],
        meta: dict[str, tuple[str, str]],
        done: set[str],
    ) -> None:
        for _data, dest in to_upload:
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
        result.failed += len(to_upload) - len(done)

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
        async with self._immich_factory() as client:
            assets = await self._assets_for(client, req)
            result.total = len(assets)
            to_upload, meta = await self._prepare_new(client, host, assets, result, canvas)
        if not to_upload:
            return result
        done: set[str] = set()
        try:
            await self._frame.upload_images(
                host,
                to_upload,
                req.target_album,
                self._recorder(host, meta, album_id, done, result),
            )
        except Exception:  # partial progress preserved via the recorder
            _log.exception("frame upload interrupted for %s", host)
        self._record_outcomes(result, to_upload, meta, done)
        return result

    # -- direct upload --------------------------------------------------------
    async def upload_files(
        self, host: str, files: list[tuple[str, bytes]], target_album: str | None
    ) -> SyncResult:
        result = SyncResult()
        canvas = await self._canvas_for(host)
        to_upload: list[tuple[bytes, str]] = []
        for file_name, raw in files:
            dest = dest_name_for(file_name, hashlib.sha256(raw).hexdigest())
            try:
                prepared = await asyncio.to_thread(
                    prepare_for_frame,
                    raw,
                    canvas,
                    fit=self._settings.frame_fit,
                    crop_tolerance=self._settings.frame_crop_tolerance,
                )
            except Exception as exc:
                result.failed += 1
                result.items.append(
                    SyncItem(
                        asset_id=file_name, dest_name=dest, status="failed", detail=str(exc)[:200]
                    )
                )
                continue
            to_upload.append((prepared, dest))
            result.uploaded += 1
            result.items.append(SyncItem(asset_id=file_name, dest_name=dest, status="uploaded"))
        if to_upload:
            await self._frame.upload_images(host, to_upload, target_album)
        return result

    async def remove(self, host: str, filename: str) -> None:
        await self._frame.delete_photo(host, filename)
        self._store.delete_by_dest(host, filename)

    # -- subscriptions (1:1 album mirror, kept in sync) -----------------------
    def list_subscriptions(self, host: str) -> list[Subscription]:
        return self._store.list_subscriptions(host)

    async def subscribe(
        self,
        host: str,
        immich_album_id: str,
        target_album: str,
        *,
        result: SyncResult | None = None,
    ) -> SyncResult:
        self._store.add_subscription(host, immich_album_id, target_album)
        return await self.sync_subscription(host, immich_album_id, target_album, result=result)

    def unsubscribe(self, host: str, immich_album_id: str) -> bool:
        return self._store.remove_subscription(host, immich_album_id)

    async def sync_subscription(
        self,
        host: str,
        immich_album_id: str,
        target_album: str,
        *,
        result: SyncResult | None = None,
    ) -> SyncResult:
        """Mirror an Immich album 1:1 onto ``target_album``: add new, drop departed."""
        result = result if result is not None else SyncResult()
        keep_dests: list[str] = []
        new_assets: list[ImmichAsset] = []
        desired_ids: set[str] = set()
        canvas = await self._canvas_for(host)

        async with self._immich_factory() as client:
            for asset in await client.album_assets(immich_album_id):
                if asset.type != "IMAGE":
                    continue
                desired_ids.add(asset.id)
                existing = self._store.get(host, asset.id)
                if existing is not None:
                    keep_dests.append(existing.dest_name)
                    result.skipped += 1
                else:
                    new_assets.append(asset)
            result.total = len(desired_ids)
            to_upload, meta = await self._prepare_new(client, host, new_assets, result, canvas)

        done: set[str] = set()
        try:
            await self._frame.mirror_album(
                host,
                keep_dests,
                to_upload,
                target_album,
                self._recorder(host, meta, immich_album_id, done, result),
            )
        except Exception:  # partial progress preserved via the recorder
            _log.exception("subscription mirror interrupted for %s/%s", host, immich_album_id)
        self._record_outcomes(result, to_upload, meta, done)

        # Drop photos that left the Immich album from the frame album (folder mirror).
        for photo in self._store.list_for_album(host, immich_album_id):
            if photo.asset_id not in desired_ids:
                self._store.delete(host, photo.asset_id)
                result.removed += 1

        self._store.touch_subscription(
            host,
            immich_album_id,
            f"+{result.uploaded} added, -{result.removed} removed, {result.skipped} kept",
        )
        return result

    async def run_due_subscriptions(self) -> dict[str, int]:
        """Re-mirror every subscription (used by the scheduler). Returns an aggregate summary."""
        summary = {"subscriptions": 0, "added": 0, "removed": 0, "failed": 0}
        for sub in self._store.list_subscriptions():
            summary["subscriptions"] += 1
            try:
                result = await self.sync_subscription(
                    sub.host, sub.immich_album_id, sub.target_album
                )
                summary["added"] += result.uploaded
                summary["removed"] += result.removed
                summary["failed"] += result.failed
            except Exception:
                summary["failed"] += 1
                _log.exception("subscription sync failed: %s/%s", sub.host, sub.immich_album_id)
        return summary
