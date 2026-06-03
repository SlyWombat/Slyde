"""SQLite store for sync state — which Immich assets are on which frame, and their content hash.

Keyed by (host, asset_id) so each frame is tracked independently. Records are written only
*after* a successful upload, so an interrupted sync never leaves phantom "already synced" rows.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

_SCHEMA = """
CREATE TABLE IF NOT EXISTS synced_photo (
    host         TEXT NOT NULL,
    asset_id     TEXT NOT NULL,
    dest_name    TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    album_id     TEXT,
    synced_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (host, asset_id)
);
"""


@dataclass
class SyncedPhoto:
    host: str
    asset_id: str
    dest_name: str
    content_hash: str
    album_id: str | None = None
    synced_at: str = ""


class Store:
    """Thin typed wrapper over a SQLite file. Safe across threads (connection per operation)."""

    def __init__(self, path: str) -> None:
        self._path = path
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert(self, photo: SyncedPhoto) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO synced_photo (host, asset_id, dest_name, content_hash, album_id) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(host, asset_id) DO UPDATE SET "
                "dest_name=excluded.dest_name, content_hash=excluded.content_hash, "
                "album_id=excluded.album_id, synced_at=datetime('now')",
                (photo.host, photo.asset_id, photo.dest_name, photo.content_hash, photo.album_id),
            )

    def get(self, host: str, asset_id: str) -> SyncedPhoto | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM synced_photo WHERE host = ? AND asset_id = ?", (host, asset_id)
            ).fetchone()
        return _row(row) if row else None

    def list_for_host(self, host: str) -> list[SyncedPhoto]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM synced_photo WHERE host = ? ORDER BY synced_at DESC", (host,)
            ).fetchall()
        return [_row(r) for r in rows]

    def delete_by_dest(self, host: str, dest_name: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM synced_photo WHERE host = ? AND dest_name = ?", (host, dest_name)
            )
            return cur.rowcount > 0


def _row(row: sqlite3.Row) -> SyncedPhoto:
    return SyncedPhoto(
        host=row["host"],
        asset_id=row["asset_id"],
        dest_name=row["dest_name"],
        content_hash=row["content_hash"],
        album_id=row["album_id"],
        synced_at=row["synced_at"],
    )
