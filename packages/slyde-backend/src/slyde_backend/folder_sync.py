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

    async def empty_folder(self, frame_id: str, folder: str) -> int:
        """Remove every photo from a folder and stop keeping it in sync, so the next cycle doesn't
        fill it again; returns how many photos left. Immich itself is not touched. A slideshow
        showing one of them moves on at its next tick."""
        self._unbind_folder(frame_id, folder)
        photos = [i for i in self._library.desired(frame_id) if i.folder == folder]
        self._library.remove(frame_id, photos)
        await self._delivery.remove_from_device(frame_id, [i.dest_name for i in photos])
        return len(photos)

    async def replace_album(
        self,
        frame_id: str,
        immich_album_id: str,
        folder: str,
        *,
        result: SyncResult | None = None,
    ) -> SyncResult:
        """Make a folder mirror ``immich_album_id`` and nothing else: every other photo in it
        leaves, and the album replaces any album the folder was bound to. Photos in both stay on
        the frame. If Immich can't be read, the folder and its binding are left as they were."""
        now = datetime.now(UTC)
        before = {i.dest_name for i in self._library.desired(frame_id) if i.folder == folder}
        result = await self.reconcile(frame_id, immich_album_id, folder, result=result, now=now)
        self._unbind_folder(frame_id, folder)
        self._store.add_subscription(frame_id, immich_album_id, folder)
        self._store.touch_subscription(frame_id, immich_album_id, f"{result.total} photos")
        # The reconcile set the folder's sync rows; what's left outside them is the rest to drop.
        self._library.remove(
            frame_id,
            [
                i
                for i in self._library.desired(frame_id)
                if i.folder == folder and i.source != "sync"
            ],
        )
        after = {i.dest_name for i in self._library.desired(frame_id) if i.folder == folder}
        gone = before - after
        result.removed = len(gone)
        await self._delivery.remove_from_device(frame_id, sorted(gone))
        result.uploaded += self._delivery.enqueue_desired(frame_id, now=now)
        result.skipped = max(result.total - result.uploaded, 0)
        await self._delivery.drain(now=now)  # deliver the new photos now (interactive)
        return result

    def _unbind_folder(self, frame_id: str, folder: str) -> None:
        for sub in self._store.list_subscriptions(frame_id):
            if sub.target_album == folder:
                self._store.remove_subscription(frame_id, sub.immich_album_id)

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

    async def add_album_once(
        self,
        frame_id: str,
        immich_album_id: str,
        folder: str,
        *,
        result: SyncResult | None = None,
    ) -> SyncResult:
        """Add a whole Immich album's images into a Library folder ONCE — no binding (#62).

        Unlike ``bind``, this creates no ``album_sync`` row: it merges the album's current images
        into the folder as ordinary curated (``source='immich'``) rows and delivers them now. A
        later change to the album does nothing — the folder is a snapshot, not a mirror.
        """
        result = result if result is not None else SyncResult()
        async with self._immich_factory() as client:
            assets = [a for a in await client.album_assets(immich_album_id) if a.type == "IMAGE"]
        for a in assets:
            dest = dest_name_for(a.file_name, a.id)
            # MERGE (INSERT OR REPLACE) — never wipe this or other folders' existing items.
            self._library.add(frame_id, LibraryItem(a.id, dest, source="immich", folder=folder))
        result.total = len(assets)
        result.prepared = len(assets)
        now = datetime.now(UTC)
        result.uploaded = self._delivery.enqueue_desired(frame_id, now=now)
        result.skipped = result.total - result.uploaded
        await self._delivery.drain(now=now)  # deliver the new photos now (interactive)
        return result

    async def reconcile(
        self,
        frame_id: str,
        immich_album_id: str,
        folder: str,
        *,
        result: SyncResult | None = None,
        now: datetime | None = None,
    ) -> SyncResult:
        """Set the bound folder's sync rows to the album's current images and queue the delta.

        On a SwitchBot frame running the slideshow, the queue then carries only the photos on air
        (see ``slideshow.py``); the folder still mirrors the whole album."""
        result = result if result is not None else SyncResult()
        now = now or datetime.now(UTC)
        async with self._immich_factory() as client:
            assets = [a for a in await client.album_assets(immich_album_id) if a.type == "IMAGE"]

        prior_items = [
            i for i in self._library.desired(frame_id) if i.folder == folder and i.source == "sync"
        ]
        items = [
            LibraryItem(a.id, dest_name_for(a.file_name, a.id), source="sync", folder=folder)
            for a in assets
        ]

        result.total = len(items)
        result.prepared = len(items)

        prior = {i.dest_name for i in prior_items}
        # Also drops the delivery row of every photo that left the set, so one that is re-added to
        # the album is delivered again.
        self._library.set_folder_sync(frame_id, folder, items)
        departed = prior - {i.dest_name for i in items}

        # The delivery queue's delta-skip (#46) already avoids re-delivering photos that are on the
        # frame, so a steady-state reconcile queues nothing — no separate "on device" bookkeeping.
        result.uploaded = self._delivery.enqueue_desired(frame_id, now=now)
        result.skipped = result.total - result.uploaded
        result.removed = len(departed)
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
