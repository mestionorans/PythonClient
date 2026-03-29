"""
Vertex client — ``config`` (SQLite ``config_kv``), ``states`` (runtime counters), ``vertex.sqlite``.

**Config** (``VertexClient.config``)

    Persistent key–value tunables (JSON values in the database). Documented keys:

    ``NodeAddress``
        Address of the subscribed / primary node (URL, host string, or opaque id — application-defined).

    ``TrustedTracker``
        List of trusted tracker endpoints or ids (strings); entries may be ``null`` until filled.
        Default is a single placeholder: ``[None]``.

    ``Health_Ind_Storage_Perc``
        Fraction of ``Total_Storage`` reserved for health-index tier storage (0.0–1.0).

    ``Emergency_Storage_Perc``
        Fraction of ``Total_Storage`` reserved for emergency / backup space (0.0–1.0).

    ``Swappiness``
        How aggressively to trade non-emergency data for emergency room when under pressure
        (typically 0.0–1.0). Also the probability that :meth:`VertexClient.backup_engine` does
        nothing on a given run.

    ``TrustQuickness``
        How fast trust / neighbor policy reacts (application-defined scale).

    ``Timer_Interval_Hours``
        Interval for periodic client tasks (integer hours; default four hours).

    ``Directory_Path``
        Root data directory for this client (string). On each :class:`VertexClient` construction
        it is set to the resolved ``data_dir`` and written to SQLite so it stays aligned with the
        open database.

    ``Total_Storage``
        Total storage budget in **bytes** for quota math.

    ``Unhealth_Index``
        Fraction or rate threshold in ``[0.0, 1.0]``: unhealthy share above this is treated as
        at-risk (application-defined; compare to health scores or unhealth tallies).

    ``Priority_Weights``
        Map from priority level (integer, as key) to weight (integer), used when combining or
        ranking by tier priority. Persisted as JSON object (keys may load back as strings).

    ``Penalty_Limit``
        Maximum neighbor ``penalties`` before trust is set to banned (``-1``); see
        :meth:`VertexClient.check_neighbors`.

    Unknown keys may still be stored. :meth:`VertexClient.change_parameter` upserts one key;
    defaults are merged on load; on first run, defaults are written to the DB if the table was empty.

**States** (``VertexClient.states``)

    Runtime-only quota totals (from ``Total_Storage`` and the ``*_Perc`` keys) and measured used
    bytes per tier. Filled by :meth:`VertexClient.update_states` (also run once after init).

    Tier directories under ``data_dir``: ``emergency/``, ``health_ind/``, ``general/``.

**Writable data directory**

    The package install location may be read-only. Pass a **user-writable** path to
    ``VertexClient(data_dir=...)`` (often from :func:`default_data_dir`). That same path is given
    to :class:`~vertex.i2p.I2P` so the database, tier storage, and I2P-local state share one tree.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from vertex.i2p import I2P

from . import NodeParser
from . import db_setup as _db
from .health import health_equation

__all__ = ["VertexClient", "DEFAULT_CONFIG", "default_data_dir"]

_DB_NAME = "vertex.sqlite"
_SUB_EMERGENCY = "emergency"
_SUB_HEALTH_IND = "health_ind"
_SUB_GENERAL = "general"
_AT_RISK_BROADCAST_MAX_ENTRIES = 100

# Changing these affects quota totals; :meth:`change_parameter` will rerun :meth:`update_states`.
_CONFIG_KEYS_AFFECTING_QUOTAS = frozenset(
    {"Total_Storage", "Emergency_Storage_Perc", "Health_Ind_Storage_Perc"},
)

DEFAULT_CONFIG: dict[str, Any] = {
    "NodeAddress": "",
    "TrustedTracker": [None],
    "Health_Ind_Storage_Perc": 0.10,
    "Emergency_Storage_Perc": 0.10,
    "Swappiness": 0.5,
    "TrustQuickness": 1.0,
    "Timer_Interval_Hours": 4,
    "Directory_Path": "",
    "Total_Storage": 10 * 1024 * 1024 * 1024,
    "Unhealth_Index": 0.5,
    "Priority_Weights": {0: 1, 1: 2, 2: 3},
    "Penalty_Limit": 10,
}


def _initial_states() -> dict[str, Any | None]:
    return {
        "CurrentTotalStorage": None,
        "Current_Total_Emergency_Storage": None,
        "Current_Total_HealthInd_Storage": None,
        "Current_Total_General_Storage": None,
        "Current_Used_TotalStorage": None,
        "Current_Used_Total_Emergency_Storage": None,
        "Current_Used_Total_HealthInd_Storage": None,
        "Current_Used_Total_General_Storage": None,
    }


def _dir_bytes(root: Path) -> int:
    """Sum of regular-file sizes under ``root`` (recursive)."""
    n = 0
    for p in root.rglob("*"):
        if p.is_file():
            n += p.stat().st_size
    return n


def _storage_file_location(tier_subdir: str, file_id: str) -> str:
    """Path under the data root: ``<tier>/<file_id>`` (e.g. ``general/abc``)."""
    return f"{tier_subdir}/{file_id}"


def default_data_dir(app_name: str = "vertex-client") -> Path:
    """
    Return a **writable** per-user directory for databases and downloads.

    Installed code under ``site-packages`` is often read-only; mutable data goes under the user
    profile, following the same conventions as ``platformdirs.user_data_dir`` (without adding that
    dependency): Windows ``%LOCALAPPDATA%/<app_name>``, macOS
    ``~/Library/Application Support/<app_name>``, Linux ``$XDG_DATA_HOME/<app_name>`` or
    ``~/.local/share/<app_name>``.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / app_name
        return Path.home() / "AppData" / "Local" / app_name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / app_name
    return Path.home() / ".local" / "share" / app_name


class VertexClient:
    """
    ``config`` persisted; ``states`` ephemeral (filled by :meth:`update_states`); ``db_path`` is
    ``vertex.sqlite``. Tier roots: ``_emergency_dir``, ``_health_ind_dir``, ``_general_dir``.
    ``_i2p`` is a :class:`~vertex.i2p.I2P` instance (pass ``i2p=`` to override the default).
    """

    def __init__(self, data_dir: Path | str, *, i2p: I2P | None = None) -> None:
        self._data_dir = Path(data_dir).resolve()
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._i2p = i2p if i2p is not None else I2P(self._data_dir)
        self._emergency_dir = self._data_dir / _SUB_EMERGENCY
        self._health_ind_dir = self._data_dir / _SUB_HEALTH_IND
        self._general_dir = self._data_dir / _SUB_GENERAL
        for d in (self._emergency_dir, self._health_ind_dir, self._general_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.db_path = self._data_dir / _DB_NAME

        self._conn = _db.connect(self.db_path)
        _db.init_schema(self._conn)

        loaded = _db.config_load(self._conn)
        self.config: dict[str, Any] = deepcopy(DEFAULT_CONFIG) | loaded
        self.config["Directory_Path"] = str(self._data_dir)
        if not loaded:
            _db.config_save(self._conn, self.config)
        else:
            _db.config_set(self._conn, "Directory_Path", str(self._data_dir))

        self.states: dict[str, Any | None] = _initial_states()
        self.update_states()

    @property
    def _connection(self):
        return self._conn

    def change_parameter(self, key: str, value: Any) -> None:
        self.config[key] = value
        _db.config_set(self._conn, key, value)
        if key in _CONFIG_KEYS_AFFECTING_QUOTAS:
            self.update_states()

    def _trusted_trackers(self) -> list[str]:
        trackers_raw = self.config.get("TrustedTracker") or []
        return [str(t) for t in trackers_raw if t is not None]

    def update_states(self) -> None:
        """
        Recompute ``states`` from ``config`` (quota totals) and disk usage under each tier folder.

        Emergency / HealthInd / General quotas are ``Total_Storage`` times the respective percents;
        general is the remainder after emergency and health-index slices (never negative).
        Used bytes are the sum of regular-file sizes under ``emergency/``, ``health_ind/``, and
        ``general/``. ``Current_Used_TotalStorage`` is the sum of the three used values.
        """
        cfg = self.config
        total = int(cfg["Total_Storage"])
        e_frac = float(cfg["Emergency_Storage_Perc"])
        h_frac = float(cfg["Health_Ind_Storage_Perc"])
        g_frac = max(0.0, 1.0 - e_frac - h_frac)

        self.states["CurrentTotalStorage"] = total
        self.states["Current_Total_Emergency_Storage"] = int(total * e_frac)
        self.states["Current_Total_HealthInd_Storage"] = int(total * h_frac)
        self.states["Current_Total_General_Storage"] = int(total * g_frac)

        ue = _dir_bytes(self._emergency_dir)
        uh = _dir_bytes(self._health_ind_dir)
        ug = _dir_bytes(self._general_dir)

        self.states["Current_Used_Total_Emergency_Storage"] = ue
        self.states["Current_Used_Total_HealthInd_Storage"] = uh
        self.states["Current_Used_Total_General_Storage"] = ug
        self.states["Current_Used_TotalStorage"] = ue + uh + ug

    def update_metadata(self) -> None:
        """
        For each row in ``storage``, normalize ``metadata`` to a dict (empty if missing, invalid
        JSON, or non-object JSON), call :func:`vertex.NodeParser.fetch_metadata` with the torrent
        file ``id`` (same id used for download), merge the returned metadata dict into the JSON,
        and persist ``metadata``, ``priority``, and ``size`` (bytes). The fourth return value from
        ``fetch_metadata`` (seeders) is not read for persistence here.
        """
        for row in _db.storage_list(self._conn):
            raw = row.get("metadata")
            meta: dict[str, Any]
            if not raw:
                meta = {}
            else:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    meta = {}
                else:
                    meta = parsed if isinstance(parsed, dict) else {}
            tid = str(row["id"])
            remote_meta, priority, size_b, _ = NodeParser.fetch_metadata(tid)
            merged = {**meta, **remote_meta}
            _db.storage_update_metadata(self._conn, tid, json.dumps(merged))
            _db.storage_update_priority(self._conn, tid, int(priority))
            _db.storage_update_size(self._conn, tid, int(size_b))

    def get_health(self, torrent_id: str) -> tuple[float, int]:
        """
        Return ``(health_score, size_bytes)`` for the torrent file ``torrent_id`` (the ``storage.id``
        and I2P download id).

        If :meth:`vertex.i2p.I2P.is_seeding` is true (torrent is on this device and seeding), the
        seeder count comes from :meth:`vertex.i2p.I2P.count_seeders` and ``size_bytes`` is
        ``storage.size``. Otherwise seeders and ``size_bytes`` come from a single call to
        :func:`vertex.NodeParser.fetch_metadata` (fourth and third return values respectively), so
        sizes reflect the node when the file is not present locally as an active seed.

        Priority from the ``storage`` row and ``Priority_Weights`` from ``config`` are combined via
        :func:`vertex.health.health_equation`.

        Raises ``KeyError`` if there is no ``storage`` row for ``torrent_id``.
        """
        row = _db.storage_get(self._conn, torrent_id)
        if row is None:
            raise KeyError(torrent_id)
        trackers = self._trusted_trackers()
        if self._i2p.is_seeding(torrent_id):
            seeders = int(self._i2p.count_seeders(torrent_id, trackers))
            size_bytes = int(row["size"])
        else:
            _m, _p, size_b, seeders_meta = NodeParser.fetch_metadata(torrent_id)
            seeders = int(seeders_meta)
            size_bytes = int(size_b)
        priority = float(row["priority"])
        weights = self.config.get("Priority_Weights") or {}
        score = health_equation(priority, seeders, weights)
        return score, size_bytes

    def update_atrisk_list(self) -> None:
        """
        Reconcile ``unhealth`` with :meth:`get_health` and ``Unhealth_Index``.

        ``unhealth.id`` is the torrent file id (same as ``storage.id``). Rows whose health is **at
        or above** ``Unhealth_Index`` are removed. Then each ``storage`` row is checked; scores
        **below** the threshold upsert that torrent id and ``size``.
        """
        threshold = float(self.config.get("Unhealth_Index", 0.5))

        for tid in [str(r["id"]) for r in _db.unhealth_list(self._conn)]:
            h, _ = self.get_health(tid)
            if h >= threshold:
                _db.unhealth_delete(self._conn, tid)

        for row in _db.storage_list(self._conn):
            tid = str(row["id"])
            h, _ = self.get_health(tid)
            if h < threshold:
                _db.unhealth_upsert(self._conn, tid, int(row["size"]))

    def broadcast_at_risk(self) -> None:
        """
        Take at most the first 100 rows from ``unhealth`` (see module constant
        ``_AT_RISK_BROADCAST_MAX_ENTRIES``), encode them as a JSON array string
        ``[{"id": ..., "size": ...}, ...]``, and pass it to
        :meth:`vertex.i2p.I2P.update_broadcast`.
        """
        rows = _db.unhealth_list(self._conn)[:_AT_RISK_BROADCAST_MAX_ENTRIES]
        payload = json.dumps(
            [{"id": str(r["id"]), "size": int(r["size"])} for r in rows]
        )
        self._i2p.update_broadcast(payload)

    def _random_health_ind_seeding_storage_id(self) -> str | None:
        """
        Pick a random ``storage.id`` whose ``file_location`` is under the health-ind tier and that
        :meth:`vertex.i2p.I2P.is_seeding` reports as seeding locally. ``None`` if there is no such row.
        """
        prefix = f"{_SUB_HEALTH_IND}/"
        candidates: list[str] = []
        for row in _db.storage_list(self._conn):
            if not str(row.get("file_location", "")).startswith(prefix):
                continue
            tid = str(row["id"])
            if self._i2p.is_seeding(tid):
                candidates.append(tid)
        if not candidates:
            return None
        return random.choice(candidates)

    def check_neighbors(self) -> None:
        """
        Choose a random health-tier torrent id that is in ``storage`` and seeding locally, then call
        :meth:`vertex.i2p.I2P.check_neighbors` with that id. If none qualify, return without doing
        anything. Otherwise reconcile trust and penalties in ``neighbors``, and update ``unhealth``
        from broadcast file ids.

        Pending neighbors (``trust`` 0) get ``times_found`` incremented each time they appear; when
        ``times_found`` exceeds ``TrustQuickness`` they become trusted (1). Banned (-1) neighbors
        are skipped.

        For trusted neighbors, the broadcast string is parsed as JSON (same shape as
        :meth:`broadcast_at_risk`: a list of ``{"id", "size"}`` objects). For each id that exists in
        ``storage``, :meth:`get_health` runs. If the score is **at or above** ``Unhealth_Index`` the
        neighbor is penalized; if ``penalties`` exceeds ``Penalty_Limit`` they are banned. If the
        score is **below** ``Unhealth_Index``, the torrent id is upserted into ``unhealth`` using the
        second value from :meth:`get_health` as size.
        """
        storage_id = self._random_health_ind_seeding_storage_id()
        if storage_id is None:
            return
        broadcasts = self._i2p.check_neighbors(storage_id)
        tq = float(self.config.get("TrustQuickness", 1.0))
        threshold = float(self.config.get("Unhealth_Index", 0.5))
        penalty_limit = int(self.config.get("Penalty_Limit", 10))

        for public_key, _msg in broadcasts.items():
            row = _db.neighbor_get(self._conn, public_key)
            if row is None:
                _db.neighbor_upsert(self._conn, public_key, 0, 0, 0)
                row = _db.neighbor_get(self._conn, public_key)
            if row is None:
                continue
            trust = int(row["trust"])
            if trust == -1:
                continue
            if trust == 0:
                _db.neighbor_increment_times(self._conn, public_key)
                row = _db.neighbor_get(self._conn, public_key)
                if row is not None and float(row["times_found"]) > tq:
                    _db.neighbor_upsert(
                        self._conn,
                        public_key,
                        1,
                        int(row["times_found"]),
                        int(row["penalties"]),
                    )

        for public_key, message in broadcasts.items():
            row = _db.neighbor_get(self._conn, public_key)
            if row is None or int(row["trust"]) != 1:
                continue
            if not (message or "").strip():
                entries: list[Any] = []
            else:
                try:
                    parsed = json.loads(message)
                except json.JSONDecodeError:
                    parsed = []
                entries = parsed if isinstance(parsed, list) else []

            for item in entries:
                row = _db.neighbor_get(self._conn, public_key)
                if row is None or int(row["trust"]) != 1:
                    break
                if not isinstance(item, dict):
                    continue
                tid = str(item.get("id", ""))
                if not tid:
                    continue
                if _db.storage_get(self._conn, tid) is None:
                    continue
                try:
                    h, sz = self.get_health(tid)
                except KeyError:
                    continue
                if h >= threshold:
                    p = _db.neighbor_increment_penalties(self._conn, public_key)
                    if p > penalty_limit:
                        row = _db.neighbor_get(self._conn, public_key)
                        if row is not None:
                            _db.neighbor_upsert(
                                self._conn,
                                public_key,
                                -1,
                                int(row["times_found"]),
                                int(row["penalties"]),
                            )
                        break
                else:
                    _db.unhealth_upsert(self._conn, tid, int(sz))

    def signup(self, node_address: str, starting_total_storage: int) -> None:
        """
        Subscribe to ``node_address``: persist it, pull node tunables via :func:`vertex.NodeParser.get_config`,
        merge into ``config``, set ``Total_Storage`` to ``starting_total_storage`` (bytes) so the first
        quota refresh matches local capacity before any later :meth:`change_parameter` calls, then
        refresh ``states``, then request initial file ids per tier. For each torrent id it calls
        :meth:`vertex.i2p.I2P.add_torrent` on ``self._i2p`` (so the torrent client registers the
        magnet) and inserts a ``storage`` row whose ``id`` is that torrent id and ``file_location``
        is ``<tier>/<torrent_id>``; ``size`` is filled by :meth:`update_metadata` from the node.
        Those ids are the BitTorrent **info hash** (40 hex characters), the same string qBittorrent
        uses as ``hash`` for that torrent in its Web API.
        """
        self.change_parameter("NodeAddress", node_address)

        remote = NodeParser.get_config(node_address)
        for key, value in remote.items():
            self.config[key] = value
            _db.config_set(self._conn, key, value)
        self.change_parameter("Total_Storage", int(starting_total_storage))

        total_g = int(self.states["Current_Total_General_Storage"] or 0)
        used_g = int(self.states["Current_Used_Total_General_Storage"] or 0)
        total_h = int(self.states["Current_Total_HealthInd_Storage"] or 0)
        used_h = int(self.states["Current_Used_Total_HealthInd_Storage"] or 0)
        remaining_general = max(0, total_g - used_g)
        remaining_health_ind = max(0, total_h - used_h)

        general_ids = NodeParser.get_init_files(remaining_general)
        health_ids = NodeParser.get_init_files(remaining_health_ind)

        trackers = self._trusted_trackers()

        for fid in general_ids:
            dest = str(self._general_dir / fid)
            self._i2p.add_torrent(dest, fid, trackers)
            loc = _storage_file_location(_SUB_GENERAL, fid)
            _db.storage_insert(self._conn, fid, loc, None)
        for fid in health_ids:
            dest = str(self._health_ind_dir / fid)
            self._i2p.add_torrent(dest, fid, trackers)
            loc = _storage_file_location(_SUB_HEALTH_IND, fid)
            _db.storage_insert(self._conn, fid, loc, None)

        self.update_metadata()


    



    def on_launch_or_daily(self) -> None:
        """
        Hook for scheduled runs: metadata refresh (stub) then reconcile unhealthy list.
        """
        self.update_states()
        self.update_metadata()
        self.update_atrisk_list()
        self.on_timer()

    def on_timer(self) -> None:
        self.update_atrisk_list()
        self.check_neighbors()
        self.broadcast_at_risk()
        self.backup_engine()

    def backup_engine(self) -> None:
        """
        Emergency backup: with probability ``Swappiness``, do nothing. Otherwise move one ``unhealth``
        item from ``general/`` or ``health_ind/`` into ``emergency/`` if it fits the remaining
        emergency quota (file move, then :meth:`~vertex.i2p.I2P.delete_torrent` on the old path,
        then :meth:`~vertex.i2p.I2P.add_torrent` on the new path, then update ``storage``). If
        emergency usage exceeds 90% of its quota, drop healthy ``general/`` files (lowest priority
        first) until general free space is at least the used emergency bytes — each drop removes
        SQLite rows and local data first, then :meth:`~vertex.i2p.I2P.delete_torrent` — then move
        each ``emergency/`` file to ``general/`` using the same move → ``delete_torrent`` (old) →
        ``add_torrent`` (new) sequence.
        """
        self.update_states()
        sw = float(self.config.get("Swappiness", 0.5))
        sw = max(0.0, min(1.0, sw))
        if random.random() < sw:
            return

        self._try_backup_one_from_unhealth()
        self.update_states()

        total_e = int(self.states.get("Current_Total_Emergency_Storage") or 0)
        used_e = int(self.states.get("Current_Used_Total_Emergency_Storage") or 0)
        if total_e <= 0:
            return
        if used_e <= total_e * 0.9:
            return

        self._evacuate_emergency_via_general()
        self.update_states()

    def _try_backup_one_from_unhealth(self) -> None:
        total_e = int(self.states.get("Current_Total_Emergency_Storage") or 0)
        used_e = int(self.states.get("Current_Used_Total_Emergency_Storage") or 0)
        free_e = max(0, total_e - used_e)
        if free_e <= 0:
            return

        ep = f"{_SUB_EMERGENCY}/"
        for urow in _db.unhealth_list(self._conn):
            tid = str(urow["id"])
            srow = _db.storage_get(self._conn, tid)
            if srow is None:
                continue
            loc = str(srow.get("file_location", ""))
            if loc.startswith(ep):
                continue
            if not (loc.startswith(f"{_SUB_GENERAL}/") or loc.startswith(f"{_SUB_HEALTH_IND}/")):
                continue
            need = int(srow.get("size") or 0)
            if need <= 0:
                need = int(urow.get("size") or 0)
            if need > free_e:
                continue

            src = self._data_dir / loc
            dst_loc = f"{_SUB_EMERGENCY}/{tid}"
            dst = self._emergency_dir / tid
            if not src.exists():
                continue
            old_path_str = str(src)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            self._i2p.delete_torrent(old_path_str, tid)
            self._i2p.add_torrent(str(dst), tid, self._trusted_trackers())
            _db.storage_update_file_location(self._conn, tid, dst_loc)
            return

    def _evacuate_emergency_via_general(self) -> None:
        self.update_states()
        threshold = float(self.config.get("Unhealth_Index", 0.5))
        total_g = int(self.states.get("Current_Total_General_Storage") or 0)
        used_g = int(self.states.get("Current_Used_Total_General_Storage") or 0)
        used_e = int(self.states.get("Current_Used_Total_Emergency_Storage") or 0)
        need_free = used_e

        gp = f"{_SUB_GENERAL}/"
        general_rows = [
            r
            for r in _db.storage_list(self._conn)
            if str(r.get("file_location", "")).startswith(gp)
        ]
        general_rows.sort(key=lambda r: (int(r.get("priority") or 0), str(r["id"])))

        free_g = max(0, total_g - used_g)
        while free_g < need_free and general_rows:
            row = general_rows.pop(0)
            tid = str(row["id"])
            try:
                h, _ = self.get_health(tid)
            except KeyError:
                continue
            if h < threshold:
                continue
            self._drop_general_row(row)
            self.update_states()
            total_g = int(self.states.get("Current_Total_General_Storage") or 0)
            used_g = int(self.states.get("Current_Used_Total_General_Storage") or 0)
            free_g = max(0, total_g - used_g)

        if free_g < need_free:
            return

        ep = f"{_SUB_EMERGENCY}/"
        emergency_rows = [
            r
            for r in _db.storage_list(self._conn)
            if str(r.get("file_location", "")).startswith(ep)
        ]
        emergency_rows.sort(key=lambda r: str(r["id"]))

        trackers = self._trusted_trackers()
        for row in emergency_rows:
            tid = str(row["id"])
            loc = str(row["file_location"])
            src = self._data_dir / loc
            dst_loc = f"{_SUB_GENERAL}/{tid}"
            dst = self._general_dir / tid
            old_path_str = str(src)
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                self._i2p.delete_torrent(old_path_str, tid)
                self._i2p.add_torrent(str(dst), tid, trackers)
            _db.storage_update_file_location(self._conn, tid, dst_loc)

    def _drop_general_row(self, row: dict) -> None:
        tid = str(row["id"])
        loc = str(row["file_location"])
        path = self._data_dir / loc
        path_str = str(path)
        _db.unhealth_delete(self._conn, tid)
        _db.storage_delete(self._conn, tid)
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        self._i2p.delete_torrent(path_str, tid)

















    def _reload_config_from_db(self) -> None:
        self.config = deepcopy(DEFAULT_CONFIG) | _db.config_load(self._conn)
        self.update_states()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> VertexClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
