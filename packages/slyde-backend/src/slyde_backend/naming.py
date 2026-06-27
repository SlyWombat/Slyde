"""Canonical on-frame filenames (``dest_name``) — the single source of truth for every write path.

``dest_name`` is the file's name on the frame AND its delivery/cache key, so it must be
deterministic per **(frame, asset)** and **folder-independent**: one asset = one file on the device,
referenced by any number of Slyde folders, delivered once. The agreed scheme (#61):

- **Immich-sourced** → ``<slug>-<asset_id[:8]>.jpg`` via :func:`dest_name_for` — readable +
  deterministic, deduped across folders and sync engines. Using this for *both* curation and
  keep-in-sync is the reconciliation that retires the two divergent legacy schemes (curation's
  ``<asset_id>.jpg`` vs the folder-sync slug were different names for one asset → duplicate files).
- **Upload-sourced** → ``up-<id>.jpg`` (:func:`upload_dest_name`) — namespaced so it can't collide
  with an asset-id slug or a device filename.
- **Frame-imported** → the device's own filename, verbatim/lowercased (:func:`frame_dest_name`) —
  mirrors device reality so an already-present file is never renamed or duplicated.

**Grandfathering:** callers preserve an asset's *existing* ``dest_name`` rather than re-key it, so
adopting this scheme never re-pushes photos already on a frame (the old name ages out only when the
item is removed/re-added).
"""

from __future__ import annotations

import re

_MAX_NAME = 64  # frame filename limit (Cadre.Utils.VerifyFilename)


def dest_name_for(file_name: str, unique: str) -> str:
    """A frame-safe, unique ``.jpg`` filename (≤ 64 chars) from a source name + a unique suffix."""
    stem = re.sub(r"[^a-z0-9]+", "-", file_name.rsplit(".", 1)[0].lower()).strip("-")
    suffix = f"-{unique[:8]}.jpg"
    return (stem[: _MAX_NAME - len(suffix)] or "photo") + suffix


def upload_dest_name(upload_id: str) -> str:
    """Canonical ``dest_name`` for an uploaded photo (not in Immich) — namespaced, with an ext."""
    return f"up-{upload_id}.jpg"


def frame_dest_name(device_filename: str) -> str:
    """Canonical ``dest_name`` for a frame-imported photo — the device's own filename, verbatim."""
    return device_filename.lower()
