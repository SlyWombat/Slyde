"""SQLite store for sync state.

Two tables, both keyed by ``host`` so each frame is independent:

* ``synced_photo`` — which Immich asset is on which frame (and as what file). Written only
  *after* a successful upload, so an interrupted sync never leaves phantom rows.
* ``album_sync`` — "keep in sync" subscriptions: an Immich album mirrored 1:1 to a frame album.
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
CREATE TABLE IF NOT EXISTS album_sync (
    host            TEXT NOT NULL,
    immich_album_id TEXT NOT NULL,
    target_album    TEXT NOT NULL,
    last_synced_at  TEXT,
    last_result     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (host, immich_album_id)
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


@dataclass
class Subscription:
    host: str
    immich_album_id: str
    target_album: str
    last_synced_at: str | None = None
    last_result: str | None = None


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

    # -- synced photos --------------------------------------------------------
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
        return _photo(row) if row else None

    def list_for_album(self, host: str, immich_album_id: str) -> list[SyncedPhoto]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM synced_photo WHERE host = ? AND album_id = ?",
                (host, immich_album_id),
            ).fetchall()
        return [_photo(r) for r in rows]

    def delete(self, host: str, asset_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM synced_photo WHERE host = ? AND asset_id = ?", (host, asset_id)
            )

    def delete_by_dest(self, host: str, dest_name: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM synced_photo WHERE host = ? AND dest_name = ?", (host, dest_name)
            )
            return cur.rowcount > 0

    # -- subscriptions --------------------------------------------------------
    def add_subscription(self, host: str, immich_album_id: str, target_album: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO album_sync (host, immich_album_id, target_album) VALUES (?, ?, ?) "
                "ON CONFLICT(host, immich_album_id) "
                "DO UPDATE SET target_album=excluded.target_album",
                (host, immich_album_id, target_album),
            )

    def remove_subscription(self, host: str, immich_album_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM album_sync WHERE host = ? AND immich_album_id = ?",
                (host, immich_album_id),
            )
            return cur.rowcount > 0

    def touch_subscription(self, host: str, immich_album_id: str, result: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE album_sync SET last_synced_at=datetime('now'), last_result=? "
                "WHERE host = ? AND immich_album_id = ?",
                (result, host, immich_album_id),
            )

    def list_subscriptions(self, host: str | None = None) -> list[Subscription]:
        with self._conn() as conn:
            if host is None:
                rows = conn.execute("SELECT * FROM album_sync").fetchall()
            else:
                rows = conn.execute("SELECT * FROM album_sync WHERE host = ?", (host,)).fetchall()
        return [_subscription(r) for r in rows]


def _photo(row: sqlite3.Row) -> SyncedPhoto:
    return SyncedPhoto(
        host=row["host"],
        asset_id=row["asset_id"],
        dest_name=row["dest_name"],
        content_hash=row["content_hash"],
        album_id=row["album_id"],
        synced_at=row["synced_at"],
    )


def _subscription(row: sqlite3.Row) -> Subscription:
    return Subscription(
        host=row["host"],
        immich_album_id=row["immich_album_id"],
        target_album=row["target_album"],
        last_synced_at=row["last_synced_at"],
        last_result=row["last_result"],
    )
