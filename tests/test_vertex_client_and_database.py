"""
Tests for :class:`vertex.client.VertexClient` and :mod:`vertex.db_setup` (SQLite persistence).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from vertex import DEFAULT_CONFIG, VertexClient
from vertex import db_setup
from vertex.health import health_equation


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
        assert c.config == {**DEFAULT_CONFIG, "Directory_Path": str(d.resolve())}
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
        assert b.config["Directory_Path"] == str(d.resolve())
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
    db_setup.storage_insert(conn, "ta", "/a/x", '{"t":1}')
    db_setup.storage_insert(conn, "tb", "/b/y", None, priority=3, size=99)
    assert db_setup.storage_get(conn, "ta")["file_location"] == "/a/x"
    assert db_setup.storage_get(conn, "ta")["priority"] == 0
    assert db_setup.storage_get(conn, "ta")["size"] == 0
    assert db_setup.storage_get(conn, "tb")["metadata"] is None
    assert db_setup.storage_get(conn, "tb")["priority"] == 3
    assert db_setup.storage_get(conn, "tb")["size"] == 99
    rows = db_setup.storage_list(conn)
    assert len(rows) == 2
    assert rows[0]["id"] == "tb"
    assert rows[1]["id"] == "ta"
    db_setup.storage_update_priority(conn, "ta", 10)
    rows = db_setup.storage_list(conn)
    assert rows[0]["id"] == "ta"
    db_setup.storage_update_metadata(conn, "ta", '{"t":2}')
    assert json.loads(db_setup.storage_get(conn, "ta")["metadata"]) == {"t": 2}
    db_setup.storage_delete(conn, "ta")
    assert db_setup.storage_get(conn, "ta") is None
    assert len(db_setup.storage_list(conn)) == 1
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
    assert row["penalties"] == 0
    db_setup.neighbor_upsert(conn, p, -1, 0)
    assert db_setup.neighbor_get(conn, p)["trust"] == -1
    assert db_setup.neighbor_get(conn, p)["penalties"] == 0
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
    db_setup.neighbor_upsert(conn, p, 1, 7, 3)
    db_setup.neighbor_upsert(conn, p, 0, None, None)
    assert db_setup.neighbor_get(conn, p)["times_found"] == 7
    assert db_setup.neighbor_get(conn, p)["trust"] == 0
    assert db_setup.neighbor_get(conn, p)["penalties"] == 3
    conn.close()


def test_neighbor_increment_penalties(tmp_path: Path) -> None:
    db = tmp_path / "n.sqlite"
    conn = db_setup.connect(db)
    db_setup.init_schema(conn)
    p = _pk(1)
    db_setup.neighbor_upsert(conn, p, 1, 0, 3)
    assert db_setup.neighbor_increment_penalties(conn, p) == 4
    assert db_setup.neighbor_get(conn, p)["penalties"] == 4
    conn.close()


def test_neighbor_increment_times_preserves_penalties(tmp_path: Path) -> None:
    db = tmp_path / "n.sqlite"
    conn = db_setup.connect(db)
    db_setup.init_schema(conn)
    p = _pk(1)
    db_setup.neighbor_upsert(conn, p, 1, 0, 4)
    db_setup.neighbor_increment_times(conn, p)
    assert db_setup.neighbor_get(conn, p)["penalties"] == 4
    assert db_setup.neighbor_get(conn, p)["times_found"] == 1


def test_neighbors_migrates_penalties_on_legacy_db(tmp_path: Path) -> None:
    db = tmp_path / "legacy_n.sqlite"
    raw = sqlite3.connect(db)
    raw.execute(
        """
        CREATE TABLE neighbors (
            public_key TEXT PRIMARY KEY,
            trust INTEGER NOT NULL,
            times_found INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    raw.execute("INSERT INTO neighbors(public_key, trust, times_found) VALUES(?, 0, 2)", (_pk(9),))
    raw.commit()
    raw.close()

    conn = db_setup.connect(db)
    db_setup.init_schema(conn)
    row = db_setup.neighbor_get(conn, _pk(9))
    assert row is not None
    assert row["penalties"] == 0
    assert row["times_found"] == 2
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
        db_setup.storage_insert(c._connection, "blob-id", "/blob", None)
        assert db_setup.storage_get(c._connection, "blob-id")["file_location"] == "/blob"
    finally:
        c.close()


def test_full_workflow_init_then_tables(tmp_path: Path) -> None:
    d = tmp_path / "vd"
    c = VertexClient(d)
    try:
        db_setup.neighbor_upsert(c._connection, _pk(1), 1, 0)
        db_setup.storage_insert(c._connection, "f", "/f", "{}")
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
    from unittest.mock import MagicMock

    def fake_get(addr: str) -> dict:
        assert addr == "https://node/"
        return {
            "Health_Ind_Storage_Perc": 0.2,
            "Emergency_Storage_Perc": 0.15,
            "Swappiness": 0.99,
            "TrustQuickness": 0.5,
            "TrustedTracker": ["tr1"],
            "Unhealth_Index": 0.25,
            "Priority_Weights": {0: 10, 1: 20},
        }

    monkeypatch.setattr("vertex.client.NodeParser.get_config", fake_get)
    c = VertexClient(tmp_path / "vd", i2p=MagicMock())
    try:
        c.signup("https://node/", DEFAULT_CONFIG["Total_Storage"])
        assert c.config["NodeAddress"] == "https://node/"
        assert c.config["Swappiness"] == 0.99
        assert c.config["TrustedTracker"] == ["tr1"]
        assert c.config["Unhealth_Index"] == 0.25
        assert c.config["Priority_Weights"] == {0: 10, 1: 20}
        rows = db_setup.storage_list(c._connection)
        assert len(rows) == 4
        assert len({r["id"] for r in rows}) == 4
        assert sum(1 for r in rows if r["file_location"].startswith("general/")) == 2
        assert sum(1 for r in rows if r["file_location"].startswith("health_ind/")) == 2
        for r in rows:
            tier, name = r["file_location"].split("/", 1)
            assert r["id"] == name
        assert all(r["size"] == 1024 for r in rows)
    finally:
        c.close()


def test_signup_starting_total_storage_sets_quotas(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    total = 5 * 1024 * 1024 * 1024
    c = VertexClient(tmp_path / "vd", i2p=MagicMock())
    try:
        c.signup("https://example/", total)
        assert c.config["Total_Storage"] == total
        assert c.states["CurrentTotalStorage"] == total
    finally:
        c.close()


def test_signup_calls_i2p_add_torrent_when_wired(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unittest.mock import MagicMock

    i2p = MagicMock()
    _wave = [0]

    def fake_init(remaining: int) -> list[str]:
        if remaining <= 0:
            return []
        _wave[0] += 1
        w = _wave[0]
        return [f"only-{w}-x", f"only-{w}-y"]

    monkeypatch.setattr("vertex.client.NodeParser.get_init_files", fake_init)
    c = VertexClient(tmp_path / "vd", i2p=i2p)
    try:
        c.signup("n", DEFAULT_CONFIG["Total_Storage"])
        assert i2p.add_torrent.call_count == 4
        rows = db_setup.storage_list(c._connection)
        assert len(rows) == 4
        assert {r["id"] for r in rows} == {"only-1-x", "only-1-y", "only-2-x", "only-2-y"}
        assert {r["file_location"] for r in rows} == {
            "general/only-1-x",
            "general/only-1-y",
            "health_ind/only-2-x",
            "health_ind/only-2-y",
        }
        assert all(r["size"] == 1024 for r in rows)
    finally:
        c.close()


def test_update_metadata_refreshes_storage_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    def fake_fetch(fid: str) -> tuple[dict, int, int, int]:
        calls.append(fid)
        n = len(calls)
        return ({"rev": n}, n * 10, n * 1000, 99)

    monkeypatch.setattr("vertex.client.NodeParser.fetch_metadata", fake_fetch)
    c = VertexClient(tmp_path / "vd")
    try:
        db_setup.storage_insert(c._connection, "a", "/a", json.dumps({"tag": "a"}))
        db_setup.storage_insert(c._connection, "b", "/b", json.dumps({"tag": "b"}))
        db_setup.storage_insert(c._connection, "c", "/c", None)
        db_setup.storage_insert(c._connection, "d", "/d", "{}")
        c.update_metadata()
        assert sorted(calls) == ["a", "b", "c", "d"]
        by_loc = {
            r["file_location"]: r["metadata"]
            for r in db_setup.storage_list(c._connection)
        }
        assert json.loads(by_loc["/a"])["rev"] == 1
        assert json.loads(by_loc["/b"])["rev"] == 2
        assert json.loads(by_loc["/c"])["rev"] == 3
        assert json.loads(by_loc["/d"])["rev"] == 4
        pri_by_loc = {
            r["file_location"]: r["priority"]
            for r in db_setup.storage_list(c._connection)
        }
        assert pri_by_loc["/a"] == 10
        assert pri_by_loc["/b"] == 20
        assert pri_by_loc["/c"] == 30
        assert pri_by_loc["/d"] == 40
        size_by_loc = {
            r["file_location"]: r["size"]
            for r in db_setup.storage_list(c._connection)
        }
        assert size_by_loc["/a"] == 1000
        assert size_by_loc["/b"] == 2000
        assert size_by_loc["/c"] == 3000
        assert size_by_loc["/d"] == 4000
    finally:
        c.close()


# --- get_health ---


def test_get_health_uses_storage_priority(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    i2p = MagicMock()
    i2p.is_seeding.return_value = True
    i2p.count_seeders.return_value = 5
    c = VertexClient(tmp_path / "vd", i2p=i2p)
    try:
        db_setup.storage_insert(
            c._connection, "fx", "general/x", None, priority=7, size=123
        )
        h, _size = c.get_health("fx")
        assert h == health_equation(7, 5, DEFAULT_CONFIG["Priority_Weights"])
        assert _size == 123
        i2p.is_seeding.assert_called_once_with("fx")
        i2p.count_seeders.assert_called_once_with("fx", [])
    finally:
        c.close()


def test_get_health_uses_metadata_seeders_when_not_seeding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unittest.mock import MagicMock

    i2p = MagicMock()
    i2p.is_seeding.return_value = False

    def fake_fetch(tid: str) -> tuple[dict, int, int, int]:
        assert tid == "fx"
        return ({}, 0, 1000, 8)

    monkeypatch.setattr("vertex.client.NodeParser.fetch_metadata", fake_fetch)
    c = VertexClient(tmp_path / "vd", i2p=i2p)
    try:
        db_setup.storage_insert(
            c._connection, "fx", "general/x", None, priority=7
        )
        h, _size = c.get_health("fx")
        assert h == health_equation(7, 8, DEFAULT_CONFIG["Priority_Weights"])
        assert _size == 1000
        i2p.count_seeders.assert_not_called()
    finally:
        c.close()


# --- check_neighbors ---

_HEALTH_IND_SEED_ID = "health-ind-seed"


def _insert_health_ind_seeding_row(c: VertexClient, i2p: Any, seed_id: str = _HEALTH_IND_SEED_ID) -> None:
    """Storage row under ``health_ind/`` and ``i2p.is_seeding`` true so :meth:`check_neighbors` runs."""
    i2p.is_seeding.return_value = True
    db_setup.storage_insert(c._connection, seed_id, f"health_ind/{seed_id}", None)


def test_check_neighbors_no_seeding_candidate_skips_i2p(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    i2p = MagicMock()
    i2p.is_seeding.return_value = False
    c = VertexClient(tmp_path / "vd", i2p=i2p)
    try:
        db_setup.storage_insert(
            c._connection, "x", "health_ind/x", None
        )
        c.check_neighbors()
        i2p.check_neighbors.assert_not_called()
    finally:
        c.close()


def test_check_neighbors_skips_banned(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    pk = _pk(1)
    i2p = MagicMock()
    i2p.check_neighbors.return_value = {pk: "[]"}
    c = VertexClient(tmp_path / "vd", i2p=i2p)
    try:
        _insert_health_ind_seeding_row(c, i2p)
        db_setup.neighbor_upsert(c._connection, pk, -1, 0, 0)
        c.check_neighbors()
        i2p.check_neighbors.assert_called_once_with(_HEALTH_IND_SEED_ID)
        assert db_setup.neighbor_get(c._connection, pk)["times_found"] == 0
    finally:
        c.close()


def test_check_neighbors_promotes_pending_when_times_exceeds_trust_quickness(
    tmp_path: Path,
) -> None:
    from unittest.mock import MagicMock

    pk = _pk(2)
    i2p = MagicMock()
    i2p.check_neighbors.return_value = {pk: "[]"}
    c = VertexClient(tmp_path / "vd", i2p=i2p)
    try:
        _insert_health_ind_seeding_row(c, i2p)
        c.change_parameter("TrustQuickness", 1.0)
        c.check_neighbors()
        assert db_setup.neighbor_get(c._connection, pk)["times_found"] == 1
        assert db_setup.neighbor_get(c._connection, pk)["trust"] == 0
        c.check_neighbors()
        row = db_setup.neighbor_get(c._connection, pk)
        assert row["times_found"] == 2
        assert row["trust"] == 1
        assert i2p.check_neighbors.call_count == 2
        assert [ca.args[0] for ca in i2p.check_neighbors.call_args_list] == [
            _HEALTH_IND_SEED_ID,
            _HEALTH_IND_SEED_ID,
        ]
    finally:
        c.close()


def test_check_neighbors_unhealthy_broadcast_upserts_unhealth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unittest.mock import MagicMock

    monkeypatch.setattr(VertexClient, "get_health", lambda self, tid: (0.1, 42))
    pk = _pk(3)
    tid = "torrent-unhealthy-1"
    i2p = MagicMock()
    i2p.check_neighbors.return_value = {
        pk: json.dumps([{"id": tid, "size": 999}])
    }
    c = VertexClient(tmp_path / "vd", i2p=i2p)
    try:
        _insert_health_ind_seeding_row(c, i2p)
        db_setup.neighbor_upsert(c._connection, pk, 1, 5, 0)
        db_setup.storage_insert(c._connection, tid, "general/x", None, priority=7, size=100)
        c.check_neighbors()
        row = db_setup.unhealth_get(c._connection, tid)
        assert row is not None
        assert row["size"] == 42
    finally:
        c.close()


def test_check_neighbors_healthy_broadcast_increments_penalties(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unittest.mock import MagicMock

    monkeypatch.setattr(VertexClient, "get_health", lambda self, tid: (0.9, 100))
    pk = _pk(4)
    tid = "torrent-healthy-1"
    i2p = MagicMock()
    i2p.check_neighbors.return_value = {pk: json.dumps([{"id": tid}])}
    c = VertexClient(tmp_path / "vd", i2p=i2p)
    try:
        _insert_health_ind_seeding_row(c, i2p)
        db_setup.neighbor_upsert(c._connection, pk, 1, 5, 0)
        db_setup.storage_insert(c._connection, tid, "general/x", None, priority=7, size=100)
        c.check_neighbors()
        assert db_setup.neighbor_get(c._connection, pk)["penalties"] == 1
    finally:
        c.close()


def test_check_neighbors_healthy_bans_when_penalty_over_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unittest.mock import MagicMock

    monkeypatch.setattr(VertexClient, "get_health", lambda self, tid: (0.9, 100))
    pk = _pk(5)
    tid = "torrent-ban-1"
    i2p = MagicMock()
    i2p.check_neighbors.return_value = {pk: json.dumps([{"id": tid}] * 11)}
    c = VertexClient(tmp_path / "vd", i2p=i2p)
    try:
        _insert_health_ind_seeding_row(c, i2p)
        c.change_parameter("Penalty_Limit", 10)
        db_setup.neighbor_upsert(c._connection, pk, 1, 5, 0)
        db_setup.storage_insert(c._connection, tid, "general/x", None, priority=7, size=100)
        c.check_neighbors()
        assert db_setup.neighbor_get(c._connection, pk)["trust"] == -1
    finally:
        c.close()


def test_get_health_raises_when_torrent_not_in_storage(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    c = VertexClient(tmp_path / "vd", i2p=MagicMock())
    try:
        with pytest.raises(KeyError, match="unknown-torrent"):
            c.get_health("unknown-torrent")
    finally:
        c.close()


# --- update_atrisk_list ---


def test_update_atrisk_list_removes_when_health_at_or_above_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(VertexClient, "get_health", lambda self, tid: (0.9, 0))
    c = VertexClient(tmp_path / "vd")
    try:
        db_setup.storage_insert(c._connection, "tx", "general/x", None)
        db_setup.unhealth_upsert(c._connection, "tx", 50)
        c.update_atrisk_list()
        assert db_setup.unhealth_list(c._connection) == []
    finally:
        c.close()


def test_update_atrisk_list_keeps_unhealth_when_below_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(VertexClient, "get_health", lambda self, tid: (0.1, 0))
    c = VertexClient(tmp_path / "vd")
    try:
        # Size must match storage: update_atrisk_list upserts unhealth from storage.size.
        db_setup.storage_insert(c._connection, "tx", "general/x", None, size=50)
        db_setup.unhealth_upsert(c._connection, "tx", 50)
        c.update_atrisk_list()
        rows = db_setup.unhealth_list(c._connection)
        assert len(rows) == 1
        assert rows[0]["id"] == "tx"
        assert rows[0]["size"] == 50
    finally:
        c.close()


def test_update_atrisk_list_adds_storage_rows_when_unhealthy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(VertexClient, "get_health", lambda self, tid: (0.1, 0))
    c = VertexClient(tmp_path / "vd")
    try:
        db_setup.storage_insert(
            c._connection, "tf", "general/f", None, size=123
        )
        c.update_atrisk_list()
        row = db_setup.unhealth_get(c._connection, "tf")
        assert row is not None
        assert row["size"] == 123
    finally:
        c.close()


def test_update_atrisk_list_skips_storage_when_healthy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(VertexClient, "get_health", lambda self, tid: (0.9, 0))
    c = VertexClient(tmp_path / "vd")
    try:
        db_setup.storage_insert(
            c._connection, "tf", "general/f", None, size=123
        )
        c.update_atrisk_list()
        assert db_setup.unhealth_get(c._connection, "tf") is None
    finally:
        c.close()


# --- backup_engine ---


def test_backup_engine_swappiness_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    monkeypatch.setattr("vertex.client.random.random", lambda: 0.0)

    c = VertexClient(tmp_path / "vd", i2p=MagicMock())
    try:
        c.backup_engine()
    finally:
        c.close()


def test_backup_engine_moves_unhealth_from_general_to_emergency(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unittest.mock import MagicMock

    monkeypatch.setattr("vertex.client.random.random", lambda: 0.99)

    i2p = MagicMock()
    c = VertexClient(tmp_path / "vd", i2p=i2p)
    try:
        fid = "t-backup-1"
        gpath = tmp_path / "vd" / "general" / fid
        gpath.write_bytes(b"y" * 40)
        db_setup.storage_insert(c._connection, fid, f"general/{fid}", None, size=40)
        db_setup.unhealth_upsert(c._connection, fid, 40)
        c.backup_engine()
        assert (tmp_path / "vd" / "emergency" / fid).is_file()
        assert db_setup.storage_get(c._connection, fid)["file_location"] == f"emergency/{fid}"
        epath = tmp_path / "vd" / "emergency" / fid
        i2p.delete_torrent.assert_called_once_with(str(gpath), fid)
        i2p.add_torrent.assert_called_once_with(str(epath), fid, [])
    finally:
        c.close()


def test_backup_engine_evacuates_emergency_to_general_when_over_90_percent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unittest.mock import MagicMock

    monkeypatch.setattr("vertex.client.random.random", lambda: 0.99)

    i2p = MagicMock()
    c = VertexClient(tmp_path / "vd", i2p=i2p)
    try:
        c.change_parameter("Total_Storage", 1000)
        eid = "e1"
        epath = tmp_path / "vd" / "emergency" / eid
        epath.write_bytes(b"z" * 95)
        db_setup.storage_insert(c._connection, eid, f"emergency/{eid}", None, size=95)
        c.update_states()
        total_e = int(c.states["Current_Total_Emergency_Storage"] or 0)
        used_e = int(c.states["Current_Used_Total_Emergency_Storage"] or 0)
        assert total_e == 100
        assert used_e == 95
        assert used_e > total_e * 0.9
        c.backup_engine()
        assert not epath.exists()
        gpath = tmp_path / "vd" / "general" / eid
        assert gpath.is_file()
        assert db_setup.storage_get(c._connection, eid)["file_location"] == f"general/{eid}"
        i2p.delete_torrent.assert_called_once_with(str(epath), eid)
        i2p.add_torrent.assert_called_once_with(str(gpath), eid, [])
    finally:
        c.close()


def test_drop_general_row_deletes_storage_before_delete_torrent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unittest.mock import MagicMock

    order: list[str] = []
    orig_sd = db_setup.storage_delete

    def wrap_storage_delete(conn: sqlite3.Connection, tid: str) -> None:
        order.append("storage_delete")
        orig_sd(conn, tid)

    monkeypatch.setattr("vertex.client._db.storage_delete", wrap_storage_delete)

    i2p = MagicMock()

    def track_delete(_path_str: str, _tid: str) -> None:
        order.append("delete_torrent")

    i2p.delete_torrent.side_effect = track_delete

    c = VertexClient(tmp_path / "vd", i2p=i2p)
    try:
        tid = "drop-order-1"
        (tmp_path / "vd" / "general" / tid).write_bytes(b"x")
        db_setup.storage_insert(c._connection, tid, f"general/{tid}", None, size=1)
        row = db_setup.storage_get(c._connection, tid)
        assert row is not None
        c._drop_general_row(dict(row))
        assert order == ["storage_delete", "delete_torrent"]
        assert db_setup.storage_get(c._connection, tid) is None
    finally:
        c.close()
