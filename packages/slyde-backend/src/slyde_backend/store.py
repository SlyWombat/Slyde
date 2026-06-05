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

from .frame import Frame

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
CREATE TABLE IF NOT EXISTS frame (
    id          TEXT PRIMARY KEY,
    backend     TEXT NOT NULL,
    interaction TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    address     TEXT NOT NULL DEFAULT '',
    frame_code  TEXT NOT NULL DEFAULT '',
    last_seen   TEXT
);
CREATE TABLE IF NOT EXISTS delivery (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    frame_id        TEXT NOT NULL,
    key             TEXT NOT NULL,
    payload         TEXT NOT NULL DEFAULT '',
    state           TEXT NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_error      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (frame_id, key)
);
CREATE TABLE IF NOT EXISTS library_item (
    frame_id  TEXT NOT NULL,
    asset_id  TEXT NOT NULL,
    dest_name TEXT NOT NULL,
    position  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (frame_id, asset_id)
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


@dataclass
class DeliveryRow:
    id: int
    frame_id: str
    key: str
    payload: str
    state: str  # "pending" | "delivered" | "failed"
    attempts: int
    next_attempt_at: str
    last_error: str | None = None


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

    # -- frame registry (transport-independent; see frame.py) ------------------
    def upsert_frame(self, frame: Frame) -> None:
        """Record/refresh a known frame. Keeps existing ``last_seen`` unless a newer one is set."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO frame "
                "(id, backend, interaction, name, address, frame_code, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET backend=excluded.backend, "
                "interaction=excluded.interaction, name=excluded.name, address=excluded.address, "
                "frame_code=excluded.frame_code, "
                "last_seen=COALESCE(excluded.last_seen, frame.last_seen)",
                (
                    frame.id,
                    frame.backend,
                    frame.interaction,
                    frame.name,
                    frame.address,
                    frame.frame_code,
                    frame.last_seen,
                ),
            )

    def touch_frame(self, frame_id: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE frame SET last_seen=datetime('now') WHERE id = ?", (frame_id,))

    def get_frame(self, frame_id: str) -> Frame | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM frame WHERE id = ?", (frame_id,)).fetchone()
        return _frame(row) if row else None

    def list_frames(self) -> list[Frame]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM frame ORDER BY name").fetchall()
        return [_frame(r) for r in rows]

    def delete_frame(self, frame_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM frame WHERE id = ?", (frame_id,))
            return cur.rowcount > 0

    # -- frame library (the desired photo set per frame; see library.py) -------
    def set_library(self, frame_id: str, items: list[tuple[str, str]]) -> None:
        """Replace a frame's desired set with ``items`` (asset_id, dest_name), order preserved."""
        with self._conn() as conn:
            conn.execute("DELETE FROM library_item WHERE frame_id = ?", (frame_id,))
            conn.executemany(
                "INSERT INTO library_item (frame_id, asset_id, dest_name, position) "
                "VALUES (?, ?, ?, ?)",
                [(frame_id, aid, dest, i) for i, (aid, dest) in enumerate(items)],
            )

    def list_library(self, frame_id: str) -> list[tuple[str, str]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT asset_id, dest_name FROM library_item WHERE frame_id = ? ORDER BY position",
                (frame_id,),
            ).fetchall()
        return [(r["asset_id"], r["dest_name"]) for r in rows]

    def clear_library(self, frame_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM library_item WHERE frame_id = ?", (frame_id,))

    # -- delivery queue (guaranteed delivery; see delivery.py) -----------------
    def enqueue_delivery(
        self, frame_id: str, key: str, payload: str, *, next_attempt_at: str
    ) -> int:
        """Queue (or re-queue) a delivery for ``(frame_id, key)``; resets it to pending, due then.

        ``next_attempt_at`` is an ISO timestamp (the delivery layer owns time so it's testable)."""
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO delivery (frame_id, key, payload, next_attempt_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(frame_id, key) DO UPDATE SET payload=excluded.payload, "
                "state='pending', attempts=0, next_attempt_at=excluded.next_attempt_at, "
                "last_error=NULL",
                (frame_id, key, payload, next_attempt_at),
            )
            row = conn.execute(
                "SELECT id FROM delivery WHERE frame_id = ? AND key = ?", (frame_id, key)
            ).fetchone()
            return int(row["id"]) if row else int(cur.lastrowid or 0)

    def due_deliveries(self, now_iso: str, limit: int = 100) -> list[DeliveryRow]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM delivery WHERE state='pending' AND next_attempt_at <= ? "
                "ORDER BY next_attempt_at LIMIT ?",
                (now_iso, limit),
            ).fetchall()
        return [_delivery(r) for r in rows]

    def mark_delivered(self, delivery_id: int) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE delivery SET state='delivered' WHERE id = ?", (delivery_id,))

    def reschedule_delivery(
        self, delivery_id: int, next_attempt_at: str, attempts: int, error: str
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE delivery SET attempts=?, next_attempt_at=?, last_error=? WHERE id = ?",
                (attempts, next_attempt_at, error[:500], delivery_id),
            )

    def fail_delivery(self, delivery_id: int, attempts: int, error: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE delivery SET state='failed', attempts=?, last_error=? WHERE id = ?",
                (attempts, error[:500], delivery_id),
            )

    def delivered_payloads(self, frame_id: str) -> dict[str, str]:
        """``key -> payload`` for already-delivered rows, so a re-curation can skip re-queueing
        photos already on the frame (#46)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key, payload FROM delivery WHERE frame_id = ? AND state='delivered'",
                (frame_id,),
            ).fetchall()
        return {r["key"]: r["payload"] for r in rows}

    def delivery_summary(self, frame_id: str) -> dict[str, int]:
        """Counts of a frame's deliveries by state (pending / delivered / failed)."""
        summary = {"pending": 0, "delivered": 0, "failed": 0}
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT state, COUNT(*) AS n FROM delivery WHERE frame_id = ? GROUP BY state",
                (frame_id,),
            ).fetchall()
        for r in rows:
            summary[r["state"]] = int(r["n"])
        return summary

    def delete_delivery(self, frame_id: str, key: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM delivery WHERE frame_id = ? AND key = ?", (frame_id, key))

    def list_deliveries(self, frame_id: str | None = None) -> list[DeliveryRow]:
        with self._conn() as conn:
            if frame_id is None:
                rows = conn.execute("SELECT * FROM delivery").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM delivery WHERE frame_id = ?", (frame_id,)
                ).fetchall()
        return [_delivery(r) for r in rows]


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


def _delivery(row: sqlite3.Row) -> DeliveryRow:
    return DeliveryRow(
        id=row["id"],
        frame_id=row["frame_id"],
        key=row["key"],
        payload=row["payload"],
        state=row["state"],
        attempts=row["attempts"],
        next_attempt_at=row["next_attempt_at"],
        last_error=row["last_error"],
    )


def _frame(row: sqlite3.Row) -> Frame:
    return Frame(
        id=row["id"],
        backend=row["backend"],
        interaction=row["interaction"],
        name=row["name"],
        address=row["address"],
        frame_code=row["frame_code"],
        last_seen=row["last_seen"],
    )
