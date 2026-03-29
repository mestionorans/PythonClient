"""
SQLite helpers for the Vertex client: schema init, config persistence, storage, neighbors, unhealth.

The ``storage`` table holds ``id``, ``file_location``, ``metadata``, and ``priority`` (integer; higher
values sort first in ``storage_list``).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

__all__ = [
    "connect",
    "init_schema",
    "config_load",
    "config_save",
    "config_set",
    "storage_insert",
    "storage_get",
    "storage_list",
    "storage_delete",
    "storage_update_priority",
    "neighbor_upsert",
    "neighbor_get",
    "neighbors_list",
    "neighbor_delete",
    "neighbor_increment_times",
    "unhealth_upsert",
    "unhealth_get",
    "unhealth_list",
    "unhealth_delete",
]


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS config_kv (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS storage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_location TEXT NOT NULL,
            metadata TEXT,
            priority INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS neighbors (
            public_key TEXT PRIMARY KEY,
            trust INTEGER NOT NULL,
            times_found INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS unhealth (
            id TEXT PRIMARY KEY,
            size INTEGER NOT NULL
        );
        """
    )
    _migrate_storage_priority(conn)


def _migrate_storage_priority(conn: sqlite3.Connection) -> None:
    """Add ``priority`` to ``storage`` when opening DBs created before that column existed."""
    rows = conn.execute("PRAGMA table_info(storage)").fetchall()
    if not rows:
        return
    names = {str(r["name"]) for r in rows}
    if "priority" in names:
        return
    conn.execute("ALTER TABLE storage ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


def _json_loads(raw: str) -> Any:
    return json.loads(raw)


def config_load(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT key, value_json FROM config_kv").fetchall()
    out: dict[str, Any] = {}
    for row in rows:
        out[str(row["key"])] = _json_loads(row["value_json"])
    return out


def config_save(conn: sqlite3.Connection, config: dict[str, Any]) -> None:
    """Replace all config rows with the contents of ``config``."""
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM config_kv")
        for key, value in config.items():
            conn.execute(
                "INSERT INTO config_kv(key, value_json) VALUES(?, ?)",
                (key, _json_dumps(value)),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def config_set(conn: sqlite3.Connection, key: str, value: Any) -> None:
    """Upsert a single config key (used by :meth:`VertexClient.change_parameter`)."""
    conn.execute(
        """
        INSERT INTO config_kv(key, value_json) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
        """,
        (key, _json_dumps(value)),
    )


# --- storage ---


def storage_insert(
    conn: sqlite3.Connection,
    file_location: str,
    metadata: str | None,
    *,
    priority: int = 0,
) -> int:
    cur = conn.execute(
        "INSERT INTO storage(file_location, metadata, priority) VALUES(?, ?, ?)",
        (file_location, metadata, priority),
    )
    return int(cur.lastrowid)


def storage_get(conn: sqlite3.Connection, row_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM storage WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        return None
    return dict(row)


def storage_list(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute("SELECT * FROM storage ORDER BY priority DESC, id ASC")
    ]


def storage_update_priority(conn: sqlite3.Connection, row_id: int, priority: int) -> None:
    conn.execute(
        "UPDATE storage SET priority = ? WHERE id = ?",
        (priority, row_id),
    )


def storage_delete(conn: sqlite3.Connection, row_id: int) -> None:
    conn.execute("DELETE FROM storage WHERE id = ?", (row_id,))


# --- neighbors: trust 1 = trusted, -1 = banned, 0 = pending ---


def neighbor_upsert(
    conn: sqlite3.Connection,
    public_key: str,
    trust: int,
    times_found: int | None = None,
) -> None:
    if times_found is None:
        row = conn.execute(
            "SELECT times_found FROM neighbors WHERE public_key = ?", (public_key,)
        ).fetchone()
        times_found = int(row["times_found"]) if row else 0
    conn.execute(
        """
        INSERT INTO neighbors(public_key, trust, times_found) VALUES(?, ?, ?)
        ON CONFLICT(public_key) DO UPDATE SET
            trust = excluded.trust,
            times_found = excluded.times_found
        """,
        (public_key, trust, times_found),
    )


def neighbor_get(conn: sqlite3.Connection, public_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM neighbors WHERE public_key = ?", (public_key,)
    ).fetchone()
    return dict(row) if row else None


def neighbors_list(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM neighbors ORDER BY public_key")]


def neighbor_delete(conn: sqlite3.Connection, public_key: str) -> None:
    conn.execute("DELETE FROM neighbors WHERE public_key = ?", (public_key,))


def neighbor_increment_times(conn: sqlite3.Connection, public_key: str) -> int:
    conn.execute(
        """
        INSERT INTO neighbors(public_key, trust, times_found) VALUES(?, 0, 1)
        ON CONFLICT(public_key) DO UPDATE SET times_found = times_found + 1
        """,
        (public_key,),
    )
    row = conn.execute(
        "SELECT times_found FROM neighbors WHERE public_key = ?", (public_key,)
    ).fetchone()
    return int(row["times_found"]) if row else 0


# --- unhealth: id + size ---


def unhealth_upsert(conn: sqlite3.Connection, id_: str, size: int) -> None:
    conn.execute(
        """
        INSERT INTO unhealth(id, size) VALUES(?, ?)
        ON CONFLICT(id) DO UPDATE SET size = excluded.size
        """,
        (id_, size),
    )


def unhealth_get(conn: sqlite3.Connection, id_: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM unhealth WHERE id = ?", (id_,)).fetchone()
    return dict(row) if row else None


def unhealth_list(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM unhealth ORDER BY id")]


def unhealth_delete(conn: sqlite3.Connection, id_: str) -> None:
    conn.execute("DELETE FROM unhealth WHERE id = ?", (id_,))
