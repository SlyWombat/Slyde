"""Keep-in-sync as per-folder *bindings* on the delivery queue (#62).

Replaces the legacy device-mirror subscription (``SyncService.sync_subscription``): a binding maps a
Library **folder** to an Immich album. Reconciling it sets that folder's ``source='sync'`` library
rows to the album's current images (add new / drop departed) and lets the **delivery queue** carry
them — so keep-in-sync now works for **connected AND served** frames, with per-photo delivery state,
offline-tolerant, and unified with curation/uploads. The legacy ``album_sync`` table is reused, with
``target_album`` reinterpreted as the Library folder, so existing subscriptions migrate in place.

Migration safety: photos already on a connected device via the legacy engine share the same
canonical ``dest_name`` (Phase 2), so we mark those delivered up front — no re-push, no sync gap.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from .config import Settings
from .delivery_service import DeliveryService
from .immich import ImmichClient
from .library import FrameLibrary, LibraryItem
from .naming import dest_name_for
from .schemas import SyncResult
from .store import Store, Subscription

_log = logging.getLogger(__name__)


class FolderSyncService:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        library: FrameLibrary,
        delivery: DeliveryService,
        immich_factory: Callable[[], ImmichClient],
    ) -> None:
        self._settings = settings
        self._store = store
        self._library = library
        self._delivery = delivery
        self._immich_factory = immich_factory

    def list_bindings(self, frame_id: str) -> list[Subscription]:
        return self._store.list_subscriptions(frame_id)

    def unbind(self, frame_id: str, immich_album_id: str) -> bool:
        """Stop keeping a folder in sync. The already-delivered photos stay in the library."""
        return self._store.remove_subscription(frame_id, immich_album_id)

    async def bind(
        self,
        frame_id: str,
        immich_album_id: str,
        folder: str,
        *,
        result: SyncResult | None = None,
    ) -> SyncResult:
        """Bind a folder to an Immich album (keep-in-sync) and reconcile it once now."""
        self._store.add_subscription(frame_id, immich_album_id, folder)
        result = await self.reconcile(frame_id, immich_album_id, folder, result=result)
        await self._delivery.drain(
            now=datetime.now(UTC)
        )  # deliver the new photos now (interactive)
        return result

    async def reconcile(
        self,
        frame_id: str,
        immich_album_id: str,
        folder: str,
        *,
        result: SyncResult | None = None,
    ) -> SyncResult:
        """Set the bound folder's sync rows to the album's current images and queue the delta."""
        result = result if result is not None else SyncResult()
        async with self._immich_factory() as client:
            assets = [a for a in await client.album_assets(immich_album_id) if a.type == "IMAGE"]
        items = [
            LibraryItem(a.id, dest_name_for(a.file_name, a.id), source="sync", folder=folder)
            for a in assets
        ]
        result.total = len(items)
        result.prepared = len(items)

        prior = {i.dest_name for i in self._library.desired(frame_id) if i.folder == folder}
        self._library.set_folder_sync(frame_id, folder, items)
        now = datetime.now(UTC)
        # The delivery queue's delta-skip (#46) already avoids re-delivering photos that are on the
        # frame, so a steady-state reconcile queues nothing — no separate "on device" bookkeeping.
        result.uploaded = self._delivery.enqueue_desired(frame_id, now=now)
        result.skipped = result.total - result.uploaded
        result.removed = len(prior - {i.dest_name for i in items})
        self._store.touch_subscription(
            frame_id,
            immich_album_id,
            f"{result.total} photos: +{result.uploaded} queued, "
            f"{result.skipped} kept, -{result.removed} removed",
        )
        return result

    async def run_due(self) -> dict[str, int]:
        """Reconcile every bound folder (the scheduler's periodic cycle)."""
        summary = {"subscriptions": 0, "added": 0, "removed": 0, "failed": 0}
        for sub in self._store.list_subscriptions():
            summary["subscriptions"] += 1
            try:
                r = await self.reconcile(sub.host, sub.immich_album_id, sub.target_album)
                summary["added"] += r.uploaded
                summary["removed"] += r.removed
            except Exception:
                summary["failed"] += 1
                _log.exception("folder sync failed: %s -> %s", sub.host, sub.target_album)
        return summary
