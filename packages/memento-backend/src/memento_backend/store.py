"""SQLite store for sync state (which Immich assets are on the frame, and their content hash)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

_SCHEMA = """
CREATE TABLE IF NOT EXISTS synced_photo (
    asset_id     TEXT PRIMARY KEY,
    dest_name    TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    album_id     TEXT,
    synced_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@dataclass
class SyncedPhoto:
    asset_id: str
    dest_name: str
    content_hash: str
    album_id: str | None
    synced_at: str


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
                "INSERT INTO synced_photo (asset_id, dest_name, content_hash, album_id) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(asset_id) DO UPDATE SET "
                "dest_name=excluded.dest_name, content_hash=excluded.content_hash, "
                "album_id=excluded.album_id, synced_at=datetime('now')",
                (photo.asset_id, photo.dest_name, photo.content_hash, photo.album_id),
            )

    def get(self, asset_id: str) -> SyncedPhoto | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM synced_photo WHERE asset_id = ?", (asset_id,)
            ).fetchone()
        return _row(row) if row else None

    def list_all(self) -> list[SyncedPhoto]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM synced_photo ORDER BY synced_at DESC").fetchall()
        return [_row(r) for r in rows]

    def delete(self, asset_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM synced_photo WHERE asset_id = ?", (asset_id,))
            return cur.rowcount > 0

    def delete_by_dest(self, dest_name: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM synced_photo WHERE dest_name = ?", (dest_name,))
            return cur.rowcount > 0


def _row(row: sqlite3.Row) -> SyncedPhoto:
    return SyncedPhoto(
        asset_id=row["asset_id"],
        dest_name=row["dest_name"],
        content_hash=row["content_hash"],
        album_id=row["album_id"],
        synced_at=row["synced_at"],
    )
