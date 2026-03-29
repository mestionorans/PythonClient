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

    ``Directory_Path``
        Root data directory for this client (string). Often mirrors the path passed to
        ``VertexClient(data_dir)`` but may be overridden for relocations.

    ``Total_Storage``
        Total storage budget in **bytes** for quota math.

    Unknown keys may still be stored. :meth:`VertexClient.change_parameter` upserts one key;
    defaults are merged on load; on first run, defaults are written to the DB if the table was empty.

**States** (``VertexClient.states``)

    Runtime-only quota totals (from ``Total_Storage`` and the ``*_Perc`` keys) and measured used
    bytes per tier. Filled by :meth:`VertexClient.update_states` (also run once after init).

    Tier directories under ``data_dir``: ``emergency/``, ``health_ind/``, ``general/``.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from vertex.i2p import I2P

from . import NodeParser
from . import db_setup as _db

__all__ = ["VertexClient", "DEFAULT_CONFIG"]

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
    "Directory_Path": "",
    "Total_Storage": 10 * 1024 * 1024 * 1024,
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


class VertexClient:
    """
    ``config`` persisted; ``states`` ephemeral (filled by :meth:`update_states`); ``db_path`` is
    ``vertex.sqlite``. Tier roots: ``_emergency_dir``, ``_health_ind_dir``, ``_general_dir``.
    ``_i2p`` is a :class:`~vertex.i2p.I2P` instance (pass ``i2p=`` to override the default).
    """

    def __init__(self, data_dir: Path | str, *, i2p: I2P | None = None) -> None:
        self._data_dir = Path(data_dir).resolve()
        self._i2p = i2p if i2p is not None else I2P()
        self._data_dir.mkdir(parents=True, exist_ok=True)
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
        if not loaded:
            _db.config_save(self._conn, self.config)

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

    def signup(self, node_address: str) -> None:
        """
        Subscribe to ``node_address``: persist it, pull node tunables via :func:`vertex.NodeParser.get_config`,
        merge into ``config``, refresh ``states``, then request initial file ids per tier and call
        :meth:`vertex.i2p.I2P.download_file` on ``self._i2p`` for each (default instance if none was
        passed to :meth:`__init__`).
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
        for fid in health_ids:
            dest = str(self._health_ind_dir / fid)
            self._i2p.download_file(dest, fid, trackers)

    def _reload_config_from_db(self) -> None:
        self.config = deepcopy(DEFAULT_CONFIG) | _db.config_load(self._conn)
        self.update_states()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> VertexClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
