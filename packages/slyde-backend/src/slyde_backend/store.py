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
    source    TEXT NOT NULL DEFAULT 'immich',  -- 'immich' (curated) | 'upload' | 'frame'
    folder    TEXT NOT NULL DEFAULT '',         -- Phase 2 (#61): folder grouping; '' = All
    PRIMARY KEY (frame_id, asset_id)
);
CREATE TABLE IF NOT EXISTS frame_display (
    -- Per-frame display state for served e-paper frames that poll dev/frame/status: which image is
    -- current, when it changed, and what the frame has acknowledged showing — so the poll returns
    -- action=2 once (fetch+display) then action=0 (idle) after the frame's callback.
    frame_id       TEXT PRIMARY KEY,
    content_key    TEXT NOT NULL DEFAULT '',
    last_update_ms TEXT NOT NULL DEFAULT '0',
    acked_key      TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS frame_setting (
    -- Per-frame device settings the app changes via setting/update* and the frame reads back.
    -- Drives dev/frame/status's wakeUpSchedule and the setting block in frame/list, setting/detail.
    frame_id            TEXT PRIMARY KEY,
    wake_up_interval    TEXT NOT NULL DEFAULT '259200',
    slide_show_interval TEXT NOT NULL DEFAULT '60',
    slide_show_switch   TEXT NOT NULL DEFAULT '0',
    display_orientation TEXT NOT NULL DEFAULT '1',
    timing_type         TEXT NOT NULL DEFAULT '0'
);
CREATE TABLE IF NOT EXISTS frame_alias (
    -- Maps every id the app/frame presents for one device (numeric frame_id/setting_id, device_id,
    -- serial, …) to the single canonical frame.id, so the same device resolves to one Frame.
    alias    TEXT PRIMARY KEY,
    frame_id TEXT NOT NULL
);
"""

# Per-frame setting fields + their defaults (the observed Sungale family values).
_SETTING_FIELDS = (
    "wake_up_interval",
    "slide_show_interval",
    "slide_show_switch",
    "display_orientation",
    "timing_type",
)
_SETTING_DEFAULTS = {
    "wake_up_interval": "259200",  # 3 days
    "slide_show_interval": "60",
    "slide_show_switch": "0",
    "display_orientation": "1",
    "timing_type": "0",
}


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
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Idempotent column migrations for DBs created before a column existed."""
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(library_item)")}
        if "source" not in cols:  # added with app-upload curation; older DBs default to 'immich'
            conn.execute(
                "ALTER TABLE library_item ADD COLUMN source TEXT NOT NULL DEFAULT 'immich'"
            )
        if "folder" not in cols:  # Phase 2 (#61): folder grouping; older rows default to '' (All)
            conn.execute("ALTER TABLE library_item ADD COLUMN folder TEXT NOT NULL DEFAULT ''")

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
                "interaction=excluded.interaction, address=excluded.address, "
                "frame_code=excluded.frame_code, "
                # Never downgrade a known name back to the bare id: the "we reached it" touch in
                # FrameService re-upserts with name==id, which used to clobber the real name (#51).
                "name=CASE WHEN excluded.name != '' AND excluded.name != excluded.id "
                "THEN excluded.name ELSE frame.name END, "
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

    def get_frame_by_address(self, address: str) -> Frame | None:
        """The connected frame currently at ``address`` (used to map a reached IP back to its stable
        id, so a 'we reached it' touch updates the GUID entry instead of making an IP duplicate)."""
        if not address:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM frame WHERE address = ? AND interaction='connected' LIMIT 1",
                (address,),
            ).fetchone()
        return _frame(row) if row else None

    def rekey_frame(self, old_id: str, new_id: str) -> None:
        """Move a frame and its GUID-keyed state (curated library + delivery queue) from ``old_id``
        to ``new_id`` — used once to migrate a legacy IP-keyed entry onto its stable GUID (#58).
        Merges into any existing ``new_id`` rows (UPDATE OR IGNORE + drop leftovers)."""
        if old_id == new_id:
            return
        with self._conn() as conn:
            for tbl in (
                "library_item",
                "delivery",
                "frame_display",
                "frame_setting",
                "frame_alias",
            ):
                conn.execute(
                    f"UPDATE OR IGNORE {tbl} SET frame_id=? WHERE frame_id=?", (new_id, old_id)
                )
                conn.execute(f"DELETE FROM {tbl} WHERE frame_id=?", (old_id,))
            # keep the old id resolvable -> the merged frame (so cached app/device ids still work)
            conn.execute(
                "INSERT OR REPLACE INTO frame_alias (alias, frame_id) VALUES (?, ?)",
                (old_id, new_id),
            )
            conn.execute("DELETE FROM frame WHERE id=?", (old_id,))

    def resolve_alias(self, alias: str) -> str | None:
        """The canonical frame.id an alternate id maps to, or None if unknown."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT frame_id FROM frame_alias WHERE alias = ?", (alias,)
            ).fetchone()
        return row["frame_id"] if row else None

    def link_alias(self, alias: str, frame_id: str) -> None:
        """Record that ``alias`` refers to the canonical ``frame_id`` (idempotent)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO frame_alias (alias, frame_id) VALUES (?, ?) "
                "ON CONFLICT(alias) DO UPDATE SET frame_id=excluded.frame_id",
                (alias, frame_id),
            )

    def list_frames(self) -> list[Frame]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM frame ORDER BY name").fetchall()
        return [_frame(r) for r in rows]

    def delete_frame(self, frame_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM frame WHERE id = ?", (frame_id,))
            return cur.rowcount > 0

    def capture_name(self, frame_id: str, name: str) -> None:
        """Record a frame's self-reported name, but only while the registry name is still a
        placeholder — empty, the bare id, or an IP-shaped string — so a device's Name fills the
        default (and heals a clobbered IP name) without overriding a user rename (#51/#58)."""
        if not name:
            return
        with self._conn() as conn:
            conn.execute(
                "UPDATE frame SET name = ? "
                "WHERE id = ? AND (name = '' OR name = id OR name GLOB '*.*.*.*')",
                (name, frame_id),
            )

    def rename_frame(self, frame_id: str, name: str) -> bool:
        """Set a frame's registry display name (any backend); returns whether the frame existed."""
        with self._conn() as conn:
            cur = conn.execute("UPDATE frame SET name = ? WHERE id = ?", (name, frame_id))
            return cur.rowcount > 0

    def purge_frame(self, frame_id: str) -> bool:
        """Remove a frame and every row keyed to it — registry, delivery queue, curated library, and
        (for connected frames, where ``host == id``) synced photos + album subscriptions. Used to
        deregister a frame; returns whether the frame existed. The physical frame is untouched."""
        with self._conn() as conn:
            existed = conn.execute("DELETE FROM frame WHERE id = ?", (frame_id,)).rowcount > 0
            conn.execute("DELETE FROM delivery WHERE frame_id = ?", (frame_id,))
            conn.execute("DELETE FROM library_item WHERE frame_id = ?", (frame_id,))
            conn.execute("DELETE FROM frame_display WHERE frame_id = ?", (frame_id,))
            conn.execute("DELETE FROM frame_setting WHERE frame_id = ?", (frame_id,))
            conn.execute(
                "DELETE FROM frame_alias WHERE frame_id = ? OR alias = ?", (frame_id, frame_id)
            )
            conn.execute("DELETE FROM synced_photo WHERE host = ?", (frame_id,))
            conn.execute("DELETE FROM album_sync WHERE host = ?", (frame_id,))
        return existed

    # -- frame library (the desired photo set per frame; see library.py) -------
    def set_library(self, frame_id: str, items: list[tuple[str, str, str]]) -> None:
        """Replace a frame's **Immich-curated** set with ``items`` (asset_id, dest_name, folder),
        order preserved. App-uploaded/imported photos (``source != 'immich'``) are kept — not part
        of the Immich curation a 'Set library' PUT manages (see uploads.py)."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM library_item WHERE frame_id = ? AND source = 'immich'", (frame_id,)
            )
            conn.executemany(
                "INSERT INTO library_item "
                "(frame_id, asset_id, dest_name, position, source, folder) "
                "VALUES (?, ?, ?, ?, 'immich', ?)",
                [(frame_id, aid, dest, i, folder) for i, (aid, dest, folder) in enumerate(items)],
            )

    def add_library_item(
        self,
        frame_id: str,
        asset_id: str,
        dest_name: str,
        *,
        source: str = "upload",
        folder: str = "",
    ) -> None:
        """Add (or replace) a single library item, appended after the current set — used for
        app-uploaded/imported photos so they join the same curation/delivery flow as Immich."""
        with self._conn() as conn:
            nxt = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM library_item WHERE frame_id = ?",
                (frame_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT OR REPLACE INTO library_item "
                "(frame_id, asset_id, dest_name, position, source, folder) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (frame_id, asset_id, dest_name, nxt, source, folder),
            )

    def set_folder_sync_library(
        self, frame_id: str, folder: str, items: list[tuple[str, str]]
    ) -> None:
        """Replace a folder's keep-in-sync rows (``source='sync'``) with ``items`` (asset_id,
        dest_name), order preserved (#62). Other folders, other sources, and manually-curated rows
        are untouched. OR REPLACE folds in an asset that was manually curated elsewhere."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM library_item WHERE frame_id = ? AND source = 'sync' AND folder = ?",
                (frame_id, folder),
            )
            nxt = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM library_item WHERE frame_id = ?",
                (frame_id,),
            ).fetchone()[0]
            conn.executemany(
                "INSERT OR REPLACE INTO library_item "
                "(frame_id, asset_id, dest_name, position, source, folder) "
                "VALUES (?, ?, ?, ?, 'sync', ?)",
                [(frame_id, aid, dest, nxt + i, folder) for i, (aid, dest) in enumerate(items)],
            )

    def list_library(self, frame_id: str) -> list[tuple[str, str, str, str]]:
        """The frame's desired photos as (asset_id, dest_name, source, folder), in order."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT asset_id, dest_name, source, folder FROM library_item "
                "WHERE frame_id = ? ORDER BY position",
                (frame_id,),
            ).fetchall()
        return [(r["asset_id"], r["dest_name"], r["source"], r["folder"]) for r in rows]

    def clear_library(self, frame_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM library_item WHERE frame_id = ?", (frame_id,))

    def delete_library_item_by_dest(self, frame_id: str, dest_name: str) -> bool:
        """Remove a single curated/uploaded photo by its dest name; True if it existed."""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM library_item WHERE frame_id = ? AND dest_name = ?",
                (frame_id, dest_name),
            )
            return cur.rowcount > 0

    # -- served e-paper display state (dev/frame/status poll; see backends/sungale_cloud.py) ----
    def get_frame_display(self, frame_id: str) -> tuple[str, str, str]:
        """(content_key, last_update_ms, acked_key) for a frame, or empty defaults if unseen."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT content_key, last_update_ms, acked_key FROM frame_display WHERE frame_id=?",
                (frame_id,),
            ).fetchone()
        return (
            (row["content_key"], row["last_update_ms"], row["acked_key"]) if row else ("", "0", "")
        )

    def set_frame_display(
        self, frame_id: str, *, content_key: str, last_update_ms: str, acked_key: str
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO frame_display (frame_id, content_key, last_update_ms, acked_key) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(frame_id) DO UPDATE SET "
                "content_key=excluded.content_key, last_update_ms=excluded.last_update_ms, "
                "acked_key=excluded.acked_key",
                (frame_id, content_key, last_update_ms, acked_key),
            )

    def get_frame_setting(self, frame_id: str) -> dict[str, str]:
        """A frame's device settings (wake interval, orientation, …), with family defaults."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM frame_setting WHERE frame_id = ?", (frame_id,)
            ).fetchone()
        return {f: row[f] for f in _SETTING_FIELDS} if row else dict(_SETTING_DEFAULTS)

    def set_frame_setting(self, frame_id: str, **fields: str | None) -> None:
        """Update the given setting fields (ignoring ``None``), preserving the rest."""
        merged = self.get_frame_setting(frame_id)
        merged.update(
            {k: str(v) for k, v in fields.items() if k in _SETTING_FIELDS and v is not None}
        )
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO frame_setting "
                "(frame_id, wake_up_interval, slide_show_interval, slide_show_switch, "
                "display_orientation, timing_type) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(frame_id) DO UPDATE SET "
                "wake_up_interval=excluded.wake_up_interval, "
                "slide_show_interval=excluded.slide_show_interval, "
                "slide_show_switch=excluded.slide_show_switch, "
                "display_orientation=excluded.display_orientation, "
                "timing_type=excluded.timing_type",
                (frame_id, *(merged[f] for f in _SETTING_FIELDS)),
            )

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

    def delivery_totals(self) -> dict[str, int]:
        """Delivery counts by state across ALL frames (for /api/metrics)."""
        totals = {"pending": 0, "delivered": 0, "failed": 0}
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT state, COUNT(*) AS n FROM delivery GROUP BY state"
            ).fetchall()
        for r in rows:
            totals[r["state"]] = int(r["n"])
        return totals

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
