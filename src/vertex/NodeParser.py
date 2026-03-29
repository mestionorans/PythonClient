"""
Fetch node metadata and initial file lists from a node JSON endpoint.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

__all__ = ["get_config", "get_init_files", "fetch_metadata"]

_DEFAULT_NODE_ADDRESS = "http://10.10.170.37:8080/"
_HTTP_TIMEOUT_SEC = 30

# Module cache set by `get_config`; allows `get_init_files` and `fetch_metadata` to resolve against
# the same subscribed node without changing existing call signatures.
_node_address: str = _DEFAULT_NODE_ADDRESS
_node_payload: dict[str, Any] | None = None
_init_cursor: int = 0

_DEFAULT_CONFIG: dict[str, Any] = {
    "Health_Ind_Storage_Perc": 0.10,
    "Emergency_Storage_Perc": 0.10,
    "Swappiness": 0.5,
    "TrustQuickness": 1.0,
    "TrustedTracker": ["testtracker1.com"],
    "Unhealth_Index": 0.5,
    "Priority_Weights": {0: 1, 1: 2, 2: 3},
    "Penalty_Limit": 10,
}


def _normalize_node_address(node_address: str) -> str:
    t = (node_address or "").strip()
    if not t:
        return _DEFAULT_NODE_ADDRESS
    parsed = urllib.parse.urlparse(t)
    if not parsed.scheme:
        t = f"http://{t}"
    return t.rstrip("/") + "/"


def _fetch_node_payload(node_address: str) -> dict[str, Any]:
    req = urllib.request.Request(
        _normalize_node_address(node_address),
        headers={"User-Agent": "vertex-client/0.1"},
    )
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SEC) as resp:
        raw = resp.read().decode()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("Node payload must be a JSON object")
    return data


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_priority_weights(value: Any) -> dict[int, int]:
    if isinstance(value, dict):
        src = value
    elif isinstance(value, str):
        t = value.strip()
        if not t:
            return {}
        # Node may send Python-ish mapping text: "{0: 1, 1: 2, 2: 3}".
        json_like = t.replace("'", '"')
        if json_like.startswith("{") and ":" in json_like:
            parts = []
            inner = json_like[1:-1].strip()
            if not inner:
                return {}
            for item in inner.split(","):
                if ":" not in item:
                    continue
                k, v = item.split(":", 1)
                parts.append((k.strip().strip('"'), v.strip().strip('"')))
            out: dict[int, int] = {}
            for k, v in parts:
                try:
                    out[int(k)] = int(v)
                except ValueError:
                    continue
            return out
        try:
            parsed = json.loads(t)
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        src = parsed
    else:
        return {}
    out2: dict[int, int] = {}
    for k, v in src.items():
        try:
            out2[int(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out2


def _entry_key(k: str) -> str:
    return k.strip()


def _entry_candidates(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for k, v in payload.items():
        key = _entry_key(str(k))
        if key.lower() == "settings":
            continue
        if not isinstance(v, dict):
            continue
        out.append((key, v))
    return out


def get_config(node_address: str) -> dict[str, Any]:
    """
    Return tunables advertised by the node at ``node_address``.

    **Stub:** Ignores ``node_address`` and returns defaults shaped like :data:`vertex.client.DEFAULT_CONFIG`
    keys (percents, swappiness, trust, trackers, unhealth index, priority weights).

    Expected real fields: ``Health_Ind_Storage_Perc``, ``Emergency_Storage_Perc``, ``Swappiness``,
    ``TrustQuickness``, ``Timer_Interval_Hours``, ``TrustedTracker`` (list; replaces
    ``TrustedTrackerList`` from the wire), ``Unhealth_Index``, ``Priority_Weights`` (object mapping
    priorit level to weight), ``Penalty_Limit`` (max penalties before a neighbor is banned).
    """
    global _node_address, _node_payload, _init_cursor

    _node_address = _normalize_node_address(node_address)
    _init_cursor = 0
    try:
        payload = _fetch_node_payload(_node_address)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, RuntimeError):
        _node_payload = None
        return dict(_DEFAULT_CONFIG)

    _node_payload = payload
    settings = payload.get("Settings")
    if not isinstance(settings, dict):
        return dict(_DEFAULT_CONFIG)

    trackers_raw = settings.get("TrustedTracker", "")
    trackers = [t.strip() for t in str(trackers_raw).split(",") if t.strip()]
    weights = _parse_priority_weights(settings.get("Priority_Weights"))

    out = dict(_DEFAULT_CONFIG)
    out["Health_Ind_Storage_Perc"] = _as_float(
        settings.get("Health_Ind_Storage_Perc"),
        float(_DEFAULT_CONFIG["Health_Ind_Storage_Perc"]),
    )
    out["Emergency_Storage_Perc"] = _as_float(
        settings.get("Emergency_Storage_Perc"),
        float(_DEFAULT_CONFIG["Emergency_Storage_Perc"]),
    )
    out["Swappiness"] = _as_float(settings.get("Swappiness"), float(_DEFAULT_CONFIG["Swappiness"]))
    out["TrustQuickness"] = _as_float(
        settings.get("TrustQuickness"),
        float(_DEFAULT_CONFIG["TrustQuickness"]),
    )
    out["TrustedTracker"] = trackers or list(_DEFAULT_CONFIG["TrustedTracker"])
    out["Unhealth_Index"] = _as_float(
        settings.get("Unhealth_Index"),
        float(_DEFAULT_CONFIG["Unhealth_Index"]),
    )
    out["Priority_Weights"] = weights or dict(_DEFAULT_CONFIG["Priority_Weights"])
    out["Penalty_Limit"] = _as_int(settings.get("Penalty_Limit"), int(_DEFAULT_CONFIG["Penalty_Limit"]))
    return out


def get_init_files(remaining_size_bytes: int) -> list[str]:
    """
    Return torrent file ids to pull for an initial sync, targeting roughly
    ``remaining_size_bytes`` budget.

    **Stub:** Returns two placeholder ids per call; each invocation uses a new prefix so ids stay
    unique across general vs health tiers (``storage.id`` is the torrent id).
    """
    if remaining_size_bytes <= 0:
        return []

    global _init_cursor
    payload = _node_payload
    if isinstance(payload, dict):
        entries = _entry_candidates(payload)
        if entries:
            # Keep deterministic paging across the two signup calls (general + health_ind).
            start = _init_cursor
            end = min(len(entries), start + 2)
            _init_cursor = end
            return [tid for tid, _meta in entries[start:end]]

    # Fallback keeps existing test and offline behavior.
    get_init_files._wave = getattr(get_init_files, "_wave", 0) + 1
    w = get_init_files._wave
    return [f"stub-{w}-a", f"stub-{w}-b"]


def fetch_metadata(torrent_id: str) -> tuple[dict[str, Any], int, int, int]:
    """
    Return ``(metadata, priority, size_bytes, seeders)`` for the torrent file ``torrent_id`` (same as
    ``storage.id`` and the id passed to I2P). ``metadata`` is merged into the row's JSON;
    ``priority`` updates ``storage.priority``; ``size_bytes`` updates ``storage.size``.

    The ``seeders`` count is for health when the torrent is **not** being seeded locally (see
    :meth:`vertex.client.VertexClient.get_health`); it is not persisted by :meth:`vertex.client.VertexClient.update_metadata`.

    **Stub:** Ignores ``torrent_id`` and returns fixed values for local testing.
    """
    tid = (torrent_id or "").strip()
    payload = _node_payload
    if isinstance(payload, dict) and tid:
        row = payload.get(tid)
        if row is None:
            # Node keys can include stray whitespace in serialized JSON.
            for k, v in payload.items():
                if str(k).strip() == tid:
                    row = v
                    break
        if isinstance(row, dict):
            priority = _as_int(row.get("priority"), 0)
            seeders = _as_int(row.get("seeders"), 0)
            size_bytes = _as_int(row.get("size_bytes"), _as_int(row.get("size"), 1024))
            metadata_raw = row.get("metadata")
            if isinstance(metadata_raw, dict):
                metadata = dict(metadata_raw)
            elif isinstance(metadata_raw, str):
                m = metadata_raw.strip()
                if not m:
                    metadata = {}
                else:
                    try:
                        parsed = json.loads(m)
                    except json.JSONDecodeError:
                        metadata = {"raw_metadata": m}
                    else:
                        metadata = parsed if isinstance(parsed, dict) else {"raw_metadata": m}
            else:
                metadata = {}
            return metadata, priority, size_bytes, seeders

    # Fallback keeps existing test and offline behavior.
    return {"stub_meta": True}, 0, 1024, 0
