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
        (typically 0.0–1.0).

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
        and persist ``metadata``, ``priority``, and ``size`` (bytes).
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
            remote_meta, priority, size_b = NodeParser.fetch_metadata(tid)
            merged = {**meta, **remote_meta}
            _db.storage_update_metadata(self._conn, tid, json.dumps(merged))
            _db.storage_update_priority(self._conn, tid, int(priority))
            _db.storage_update_size(self._conn, tid, int(size_b))

    def get_health(self, torrent_id: str) -> float:
        """
        Health score for the torrent file ``torrent_id`` (the ``storage.id`` and I2P download id).
        Seeder count from :meth:`vertex.i2p.I2P.count_seeders`; priority from the ``storage`` row;
        ``Priority_Weights`` from ``config``; combined via :func:`vertex.health.health_equation`.

        Raises ``KeyError`` if there is no ``storage`` row for ``torrent_id``.
        """
        row = _db.storage_get(self._conn, torrent_id)
        if row is None:
            raise KeyError(torrent_id)
        trackers_raw = self.config.get("TrustedTracker") or []
        trackers = [str(t) for t in trackers_raw if t is not None]
        seeders = self._i2p.count_seeders(torrent_id, trackers)
        priority = float(row["priority"])
        weights = self.config.get("Priority_Weights") or {}
        return health_equation(priority, int(seeders), weights)

    def update_atrisk_list(self) -> None:
        """
        Reconcile ``unhealth`` with :meth:`get_health` and ``Unhealth_Index``.

        ``unhealth.id`` is the torrent file id (same as ``storage.id``). Rows whose health is **at
        or above** ``Unhealth_Index`` are removed. Then each ``storage`` row is checked; scores
        **below** the threshold upsert that torrent id and ``size``.
        """
        threshold = float(self.config.get("Unhealth_Index", 0.5))

        for tid in [str(r["id"]) for r in _db.unhealth_list(self._conn)]:
            if self.get_health(tid) >= threshold:
                _db.unhealth_delete(self._conn, tid)

        for row in _db.storage_list(self._conn):
            tid = str(row["id"])
            if self.get_health(tid) < threshold:
                _db.unhealth_upsert(self._conn, tid, int(row["size"]))

    def signup(self, node_address: str) -> None:
        """
        Subscribe to ``node_address``: persist it, pull node tunables via :func:`vertex.NodeParser.get_config`,
        merge into ``config``, refresh ``states``, then request initial file ids per tier. For each
        torrent id it calls :meth:`vertex.i2p.I2P.download_file` on ``self._i2p`` and inserts a
        ``storage`` row whose ``id`` is that torrent id and ``file_location`` is
        ``<tier>/<torrent_id>``; ``size`` is filled by :meth:`update_metadata` from the node.
        """
        self.change_parameter("NodeAddress", node_address)

        remote = NodeParser.get_config(node_address)
        for key, value in remote.items():
            self.config[key] = value
            _db.config_set(self._conn, key, value)
        self.update_states()

        total_g = int(self.states["Current_Total_General_Storage"] or 0)
        used_g = int(self.states["Current_Used_Total_General_Storage"] or 0)
        total_h = int(self.states["Current_Total_HealthInd_Storage"] or 0)
        used_h = int(self.states["Current_Used_Total_HealthInd_Storage"] or 0)
        remaining_general = max(0, total_g - used_g)
        remaining_health_ind = max(0, total_h - used_h)

        general_ids = NodeParser.get_init_files(remaining_general)
        health_ids = NodeParser.get_init_files(remaining_health_ind)

        trackers_raw = self.config.get("TrustedTracker") or []
        trackers = [str(t) for t in trackers_raw if t is not None]

        for fid in general_ids:
            dest = str(self._general_dir / fid)
            self._i2p.download_file(dest, fid, trackers)
            loc = _storage_file_location(_SUB_GENERAL, fid)
            _db.storage_insert(self._conn, fid, loc, None)
        for fid in health_ids:
            dest = str(self._health_ind_dir / fid)
            self._i2p.download_file(dest, fid, trackers)
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
        

















    def _reload_config_from_db(self) -> None:
        self.config = deepcopy(DEFAULT_CONFIG) | _db.config_load(self._conn)
        self.update_states()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> VertexClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
