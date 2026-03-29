"""
Fetch node metadata and initial file lists. **Dummy implementations** until real HTTP / RPC exists.
"""

from __future__ import annotations

from typing import Any

__all__ = ["get_config", "get_init_files", "fetch_metadata"]


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
    del node_address
    return {
        "Health_Ind_Storage_Perc": 0.10,
        "Emergency_Storage_Perc": 0.10,
        "Swappiness": 0.5,
        "TrustQuickness": 1.0,
        "TrustedTracker": ["testtracker1.com"],
        "Unhealth_Index": 0.5,
        "Priority_Weights": {0: 1, 1: 2, 2: 3},
        "Penalty_Limit": 10,
    }


def get_init_files(remaining_size_bytes: int) -> list[str]:
    """
    Return torrent file ids to pull for an initial sync, targeting roughly
    ``remaining_size_bytes`` budget.

    **Stub:** Returns two placeholder ids per call; each invocation uses a new prefix so ids stay
    unique across general vs health tiers (``storage.id`` is the torrent id).
    """
    if remaining_size_bytes <= 0:
        return []
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
    del torrent_id
    return {"stub_meta": True}, 0, 1024, 0
