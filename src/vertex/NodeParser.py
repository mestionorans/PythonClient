"""
Fetch node metadata and initial file lists. **Dummy implementations** until real HTTP / RPC exists.
"""

from __future__ import annotations

from typing import Any

__all__ = ["get_config", "get_init_files"]


def get_config(node_address: str) -> dict[str, Any]:
    """
    Return tunables advertised by the node at ``node_address``.

    **Stub:** Ignores ``node_address`` and returns defaults shaped like :data:`vertex.client.DEFAULT_CONFIG`
    keys (percents, swappiness, trust, trackers).

    Expected real fields: ``Health_Ind_Storage_Perc``, ``Emergency_Storage_Perc``, ``Swappiness``,
    ``TrustQuickness``, ``TrustedTracker`` (list; replaces ``TrustedTrackerList`` from the wire).
    """
    del node_address
    return {
        "Health_Ind_Storage_Perc": 0.10,
        "Emergency_Storage_Perc": 0.10,
        "Swappiness": 0.5,
        "TrustQuickness": 1.0,
        "TrustedTracker": ["testtracker1.com"],
    }


def get_init_files(remaining_size_bytes: int) -> list[str]:
    """
    Return file ids to pull for an initial sync, targeting roughly ``remaining_size_bytes`` budget.

    **Stub:** Returns a fixed list of placeholder ids when ``remaining_size_bytes > 0``.
    """
    if remaining_size_bytes <= 0:
        return []
    return ["stub-file-1", "stub-file-2"]
