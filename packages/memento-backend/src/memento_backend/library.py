"""Frame library — the desired photo set per frame, decoupled from delivery (issue #23).

`FrameLibrary` is the one *curation* model: which Immich assets a frame should show. Delivery is
separate and chosen by the frame's interaction kind (see ``docs/framework-design.md`` §2.4):

- **Served** frames (cloud): the manager **publishes** — prepares each desired image for the frame
  (fit/smart-blur, e-ink palette/dither once #19 lands) and writes it to the ``ImageCache`` (#25),
  *ready to serve*. The frame pulls it when it wakes (``CachedImageDelivery``). Because the prepared
  image just sits in the cache, this is inherently tolerant of a frame being offline for days (#26).
- **Connected** frames (LAN): reconcile by **push** — that path stays in ``SyncService`` and is
  unchanged here; it should consult the same desired set as it migrates onto the library.

The library is sourced **read-only** from Immich (the read-only contract is preserved).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .imagecache import ImageCache
from .imaging import prepare_for_frame
from .immich import ImmichClient
from .store import Store


@dataclass(frozen=True)
class LibraryItem:
    asset_id: str
    dest_name: str


class FrameLibrary:
    """The desired set per frame, plus publishing prepared images to the cache for served frames."""

    def __init__(self, store: Store, cache: ImageCache) -> None:
        self._store = store
        self._cache = cache

    def set_desired(self, frame_id: str, items: list[LibraryItem]) -> None:
        """Record (durably) the photos a frame should show. Replaces the previous set."""
        self._store.set_library(frame_id, [(i.asset_id, i.dest_name) for i in items])

    def desired(self, frame_id: str) -> list[LibraryItem]:
        return [LibraryItem(aid, dest) for aid, dest in self._store.list_library(frame_id)]

    async def publish(
        self,
        frame_id: str,
        immich: ImmichClient,
        *,
        canvas: tuple[int, int],
        fit: str = "smart",
        crop_tolerance: float = 0.12,
        asset_size: str = "preview",
    ) -> list[str]:
        """Prepare every desired image for the frame and cache it, *ready to serve* (served frames).

        Reads bytes from Immich (read-only), fits/edits them for the frame's canvas, and writes the
        result into the ``ImageCache`` keyed by dest name. Returns the dests published. Items that
        fail to fetch/prepare are skipped (the prior cached copy, if any, is left in place — a
        fallback that keeps the frame showing something; durable retry is #26).
        """
        published: list[str] = []
        for item in self.desired(frame_id):
            try:
                source = await immich.asset_bytes(item.asset_id, asset_size)
                prepared = await asyncio.to_thread(
                    prepare_for_frame, source, canvas, fit=fit, crop_tolerance=crop_tolerance
                )
            except Exception:
                continue
            self._cache.put(frame_id, item.dest_name, prepared)
            published.append(item.dest_name)
        return published
