"""
Tests for :class:`vertex.client.VertexClient` and :mod:`vertex.db_setup` (SQLite persistence).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from vertex import DEFAULT_CONFIG, VertexClient
from vertex import db_setup


# --- VertexClient: init & config ---


def test_init_creates_sqlite_tier_dirs_and_states(tmp_path: Path) -> None:
    d = tmp_path / "vd"
    c = VertexClient(d)
    try:
        assert c.db_path == d / "vertex.sqlite"
        assert c.db_path.is_file()
        assert (d / "emergency").is_dir()
        assert (d / "health_ind").is_dir()
        assert (d / "general").is_dir()
        assert c.config == DEFAULT_CONFIG
        assert len(c.states) == 8
        total = int(DEFAULT_CONFIG["Total_Storage"])
        e_frac = float(DEFAULT_CONFIG["Emergency_Storage_Perc"])
        h_frac = float(DEFAULT_CONFIG["Health_Ind_Storage_Perc"])
        g_frac = max(0.0, 1.0 - e_frac - h_frac)
        assert c.states["CurrentTotalStorage"] == total
        assert c.states["Current_Total_Emergency_Storage"] == int(total * e_frac)
        assert c.states["Current_Total_HealthInd_Storage"] == int(total * h_frac)
        assert c.states["Current_Total_General_Storage"] == int(total * g_frac)
        assert c.states["Current_Used_TotalStorage"] == 0
        assert c.states["Current_Used_Total_Emergency_Storage"] == 0
    finally:
        c.close()


def test_update_states_sums_usage_from_tier_dirs(tmp_path: Path) -> None:
    d = tmp_path / "vd"
    c = VertexClient(d)
    try:
        (d / "emergency" / "a.bin").write_bytes(b"x" * 10)
        (d / "health_ind" / "b.bin").write_bytes(b"yy" * 5)
        (d / "general" / "c.bin").write_bytes(b"z")
        c.update_states()
        assert c.states["Current_Used_Total_Emergency_Storage"] == 10
        assert c.states["Current_Used_Total_HealthInd_Storage"] == 10
        assert c.states["Current_Used_Total_General_Storage"] == 1
        assert c.states["Current_Used_TotalStorage"] == 21
    finally:
        c.close()


def test_quota_states_refresh_when_storage_config_changes(tmp_path: Path) -> None:
    d = tmp_path / "vd"
    c = VertexClient(d)
    try:
        c.change_parameter("Total_Storage", 1_000_000)
        c.change_parameter("Emergency_Storage_Perc", 0.2)
        c.change_parameter("Health_Ind_Storage_Perc", 0.3)
        assert c.states["CurrentTotalStorage"] == 1_000_000
        assert c.states["Current_Total_Emergency_Storage"] == 200_000
        assert c.states["Current_Total_HealthInd_Storage"] == 300_000
        assert c.states["Current_Total_General_Storage"] == 500_000
    finally:
        c.close()


def test_first_run_writes_full_config_to_db(tmp_path: Path) -> None:
    d = tmp_path / "vd"
    c = VertexClient(d)
    try:
        conn = sqlite3.connect(c.db_path)
        rows = conn.execute("SELECT COUNT(*) FROM config_kv").fetchone()[0]
        conn.close()
        assert rows == len(DEFAULT_CONFIG)
    finally:
        c.close()


def test_config_persists_across_new_client(tmp_path: Path) -> None:
    d = tmp_path / "vd"
    a = VertexClient(d)
    try:
        a.change_parameter("Swappiness", 0.25)
        a.change_parameter("Total_Storage", 999)
    finally:
        a.close()

    b = VertexClient(d)
    try:
        assert b.config["Swappiness"] == 0.25
        assert b.config["Total_Storage"] == 999
        assert b.config["TrustQuickness"] == DEFAULT_CONFIG["TrustQuickness"]
    finally:
        b.close()


def test_custom_config_key_persists(tmp_path: Path) -> None:
    d = tmp_path / "vd"
    c = VertexClient(d)
    try:
        c.change_parameter("experimental_flag", True)
    finally:
        c.close()
    c2 = VertexClient(d)
    try:
        assert c2.config["experimental_flag"] is True
        assert c2.config["Swappiness"] == DEFAULT_CONFIG["Swappiness"]
    finally:
        c2.close()


def test_config_json_values_roundtrip(tmp_path: Path) -> None:
    d = tmp_path / "vd"
    c = VertexClient(d)
    try:
        c.change_parameter("nested", {"a": [1, 2], "b": None})
    finally:
        c.close()
    c2 = VertexClient(d)
    try:
        assert c2.config["nested"] == {"a": [1, 2], "b": None}
    finally:
        c2.close()


def test_reload_config_from_db(tmp_path: Path) -> None:
    d = tmp_path / "vd"
    c = VertexClient(d)
    try:
        c.change_parameter("Swappiness", 0.99)
        c.config["Swappiness"] = 0.01  # stale in-memory only
        c._reload_config_from_db()
        assert c.config["Swappiness"] == 0.99
    finally:
        c.close()


def test_external_db_edit_visible_after_reload(tmp_path: Path) -> None:
    d = tmp_path / "vd"
    c = VertexClient(d)
    try:
        raw = Path(c.db_path)
        conn = sqlite3.connect(raw)
        conn.execute(
            "INSERT INTO config_kv(key, value_json) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
            ("Swappiness", "0.11"),
        )
        conn.commit()
        conn.close()
        c._reload_config_from_db()
        assert c.config["Swappiness"] == 0.11
    finally:
        c.close()


def test_vertex_context_manager_closes_connection(tmp_path: Path) -> None:
    d = tmp_path / "vd"
    with VertexClient(d) as c:
        conn = c._connection
        assert conn is not None
    # Connection closed; further use may fail
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


# --- db_setup: config_load / config_save / config_set ---


def test_config_save_replaces_entire_table(tmp_path: Path) -> None:
    db = tmp_path / "cfg.sqlite"
    conn = db_setup.connect(db)
    db_setup.init_schema(conn)
    db_setup.config_save(conn, {"a": 1, "b": 2})
    assert db_setup.config_load(conn) == {"a": 1, "b": 2}
    db_setup.config_save(conn, {"only": "x"})
    assert db_setup.config_load(conn) == {"only": "x"}
    conn.close()


def test_config_set_upserts_single_key(tmp_path: Path) -> None:
    db = tmp_path / "cfg.sqlite"
    conn = db_setup.connect(db)
    db_setup.init_schema(conn)
    db_setup.config_set(conn, "k", 1)
    db_setup.config_set(conn, "k", 2)
    assert db_setup.config_load(conn) == {"k": 2}
    conn.close()


# --- db_setup: storage ---


def test_storage_crud(tmp_path: Path) -> None:
    db = tmp_path / "s.sqlite"
    conn = db_setup.connect(db)
    db_setup.init_schema(conn)
    i1 = db_setup.storage_insert(conn, "/a/x", '{"t":1}')
    i2 = db_setup.storage_insert(conn, "/b/y", None, priority=3)
    assert db_setup.storage_get(conn, i1)["file_location"] == "/a/x"
    assert db_setup.storage_get(conn, i1)["priority"] == 0
    assert db_setup.storage_get(conn, i2)["metadata"] is None
    assert db_setup.storage_get(conn, i2)["priority"] == 3
    rows = db_setup.storage_list(conn)
    assert len(rows) == 2
    assert rows[0]["id"] == i2
    assert rows[1]["id"] == i1
    db_setup.storage_update_priority(conn, i1, 10)
    rows = db_setup.storage_list(conn)
    assert rows[0]["id"] == i1
    db_setup.storage_delete(conn, i1)
    assert db_setup.storage_get(conn, i1) is None
    assert len(db_setup.storage_list(conn)) == 1
    conn.close()


def test_storage_migrates_priority_on_legacy_db(tmp_path: Path) -> None:
    db = tmp_path / "legacy.sqlite"
    raw = sqlite3.connect(db)
    raw.execute(
        """
        CREATE TABLE storage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_location TEXT NOT NULL,
            metadata TEXT
        )
        """
    )
    raw.execute("INSERT INTO storage(file_location, metadata) VALUES('/legacy', NULL)")
    raw.commit()
    raw.close()

    conn = db_setup.connect(db)
    db_setup.init_schema(conn)
    row = db_setup.storage_get(conn, 1)
    assert row is not None
    assert row["file_location"] == "/legacy"
    assert row["priority"] == 0
    conn.close()


# --- db_setup: neighbors ---


def _pk(n: int) -> str:
    return f"{n:064x}"


def test_neighbors_trust_levels_and_get(tmp_path: Path) -> None:
    db = tmp_path / "n.sqlite"
    conn = db_setup.connect(db)
    db_setup.init_schema(conn)
    p = _pk(1)
    db_setup.neighbor_upsert(conn, p, 1, 0)
    row = db_setup.neighbor_get(conn, p)
    assert row["trust"] == 1
    assert row["times_found"] == 0
    db_setup.neighbor_upsert(conn, p, -1, 0)
    assert db_setup.neighbor_get(conn, p)["trust"] == -1
    conn.close()


def test_neighbors_list_order(tmp_path: Path) -> None:
    db = tmp_path / "n.sqlite"
    conn = db_setup.connect(db)
    db_setup.init_schema(conn)
    db_setup.neighbor_upsert(conn, _pk(3), 0, 0)
    db_setup.neighbor_upsert(conn, _pk(1), 0, 0)
    keys = [r["public_key"] for r in db_setup.neighbors_list(conn)]
    assert keys == sorted(keys)


def test_neighbor_delete(tmp_path: Path) -> None:
    db = tmp_path / "n.sqlite"
    conn = db_setup.connect(db)
    db_setup.init_schema(conn)
    p = _pk(1)
    db_setup.neighbor_upsert(conn, p, 0, 5)
    db_setup.neighbor_delete(conn, p)
    assert db_setup.neighbor_get(conn, p) is None
    conn.close()


def test_neighbor_increment_times(tmp_path: Path) -> None:
    db = tmp_path / "n.sqlite"
    conn = db_setup.connect(db)
    db_setup.init_schema(conn)
    p = _pk(1)
    assert db_setup.neighbor_increment_times(conn, p) == 1
    assert db_setup.neighbor_increment_times(conn, p) == 2
    assert db_setup.neighbor_get(conn, p)["times_found"] == 2
    conn.close()


def test_neighbor_upsert_none_times_preserves_existing(tmp_path: Path) -> None:
    db = tmp_path / "n.sqlite"
    conn = db_setup.connect(db)
    db_setup.init_schema(conn)
    p = _pk(1)
    db_setup.neighbor_upsert(conn, p, 1, 7)
    db_setup.neighbor_upsert(conn, p, 0, None)
    assert db_setup.neighbor_get(conn, p)["times_found"] == 7
    assert db_setup.neighbor_get(conn, p)["trust"] == 0
    conn.close()


# --- db_setup: unhealth ---


def test_unhealth_crud(tmp_path: Path) -> None:
    db = tmp_path / "u.sqlite"
    conn = db_setup.connect(db)
    db_setup.init_schema(conn)
    db_setup.unhealth_upsert(conn, "fid-1", 100)
    db_setup.unhealth_upsert(conn, "fid-1", 200)
    row = db_setup.unhealth_get(conn, "fid-1")
    assert row["size"] == 200
    assert len(db_setup.unhealth_list(conn)) == 1
    db_setup.unhealth_delete(conn, "fid-1")
    assert db_setup.unhealth_get(conn, "fid-1") is None
    assert db_setup.unhealth_list(conn) == []
    conn.close()


# --- Integration: client connection + db helpers ---


def test_client_connection_uses_same_db_file(tmp_path: Path) -> None:
    d = tmp_path / "vd"
    c = VertexClient(d)
    try:
        sid = db_setup.storage_insert(c._connection, "/blob", None)
        assert db_setup.storage_get(c._connection, sid)["file_location"] == "/blob"
    finally:
        c.close()


def test_full_workflow_init_then_tables(tmp_path: Path) -> None:
    d = tmp_path / "vd"
    c = VertexClient(d)
    try:
        db_setup.neighbor_upsert(c._connection, _pk(1), 1, 0)
        db_setup.storage_insert(c._connection, "/f", "{}")
        db_setup.unhealth_upsert(c._connection, "x", 42)
        assert len(db_setup.neighbors_list(c._connection)) == 1
        assert len(db_setup.storage_list(c._connection)) == 1
        assert len(db_setup.unhealth_list(c._connection)) == 1
    finally:
        c.close()

    c2 = VertexClient(d)
    try:
        assert len(db_setup.neighbors_list(c2._connection)) == 1
        assert len(db_setup.storage_list(c2._connection)) == 1
        assert db_setup.unhealth_get(c2._connection, "x")["size"] == 42
        assert c2.config["Swappiness"] == DEFAULT_CONFIG["Swappiness"]
    finally:
        c2.close()


# --- signup ---


def test_signup_sets_node_address_and_merges_node_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_get(addr: str) -> dict:
        assert addr == "https://node/"
        return {
            "Health_Ind_Storage_Perc": 0.2,
            "Emergency_Storage_Perc": 0.15,
            "Swappiness": 0.99,
            "TrustQuickness": 0.5,
            "TrustedTracker": ["tr1"],
        }

    monkeypatch.setattr("vertex.client.NodeParser.get_config", fake_get)
    c = VertexClient(tmp_path / "vd")
    try:
        c.signup("https://node/")
        assert c.config["NodeAddress"] == "https://node/"
        assert c.config["Swappiness"] == 0.99
        assert c.config["TrustedTracker"] == ["tr1"]
    finally:
        c.close()


def test_signup_calls_i2p_download_when_wired(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unittest.mock import MagicMock

    i2p = MagicMock()
    monkeypatch.setattr(
        "vertex.client.NodeParser.get_init_files",
        lambda remaining: ["only-one"] if remaining > 0 else [],
    )
    c = VertexClient(tmp_path / "vd", i2p=i2p)
    try:
        c.signup("n")
        assert i2p.download_file.call_count == 2
    finally:
        c.close()
