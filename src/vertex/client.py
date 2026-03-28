"""
Vertex client: orchestration and policy; I2P is injected via :class:`vertex.i2p.I2P`.

Methods are stubs with docstrings matching the design doc; wire persistence and real
health logic later.
"""

from __future__ import annotations

from typing import Any, Mapping

from vertex.i2p import I2P

__all__ = ["VertexClient"]


class VertexClient:
    """
    Decentralized backup vertex: neighbors, signup, health, and backup orchestration.

    ``neighbors`` holds semi-trusted peers the client interacts with; populated at
    signup and updated when choosing to back up from a node source.

    Pass ``i2p`` to use a custom instance (defaults to a fresh :class:`~vertex.i2p.I2P`).
    """

    def __init__(self, *, i2p: I2P | None = None) -> None:
        self._i2p: I2P = i2p if i2p is not None else I2P()
        self.neighbors: list[str] = []

    def signup(
        self,
        node: str,
        *,
        keep_forever_fraction: float = 0.1,
        host_fraction: float = 0.2,
    ) -> None:
        """
        Join the network for this BDFL node.

        Intended behavior (real code later):

        - Resolve the node; use its list of trusted mirrors, or download the node DB.
        - Pick a random subset of data to host and mark ``keep_forever_fraction`` of that
          subset as "keep forever"; seeders of those pieces become initial neighbors.
        - Download node data for the chosen subset.
        - Fetch node settings into **internal** storage only; do not follow live remote
          policy updates for client-side eviction / penalties (use internal settings).

        :param node: BDFL node identity.
        :param keep_forever_fraction: Fraction of hosted subset pinned as keep-forever cohort.
        :param host_fraction: Rough fraction of the catalog this vertex commits to host.
        """
        pass

    def on_launch_or_daily(self, node: str) -> None:
        """
        Run at process start and at least once per day.

        - Refresh from ``node`` the metadata for files this vertex hosts (priority, size,
          hashes, agreed thresholds).
        - Recompute health for all hosted items via :meth:`get_health`.
        - Re-evaluate items that were temporarily unhealthy via neighbor hints; remove
          from the local unhealthy list if health is now above threshold.
        - Optional: verify checksums of a sample of seeded files to detect disk corruption.

        :param node: Node to sync hosted-file metadata from (not client policy).
        """
        pass

    def periodic_neighbor_check(self, node: str) -> None:
        """
        Run on a short interval (minutes / hours).

        For each neighbor, if reports suggest unhealth, locally verify with
        :meth:`get_health`. If still unhealthy:

        - Add to this vertex's unhealthy list.
        - Run :meth:`backup_check` for those files.
        - If backing up, add the node source as a neighbor if not present.

        If verification shows the file is healthy, apply a penalty to the neighbor that
        claimed unhealth; drop and blacklist after too many penalties.

        :param node: Node used when adding a neighbor from backup flow.
        """
        for neighbor in self.neighbors:
            _ = self._i2p.check_neighbor(neighbor)

    def get_health(
        self,
        file_index: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        requesting_seeder: str | None = None,
    ) -> float:
        """
        Compute a health score in ``[0.0, 1.0]`` (convention; tune in implementation).

        Combine priority (or related fields) from trusted metadata with an I2P-side
        seed/availability check (via ``self._i2p`` in a full implementation), using
        ``requesting_seeder`` as the vantage for peer discovery when required.

        If the result is below the unhealthy threshold, add ``file_index`` to the local
        unhealthy list (real implementation).

        :param file_index: File or chunk index to score.
        :param metadata: Trusted metadata snapshot (priority, etc.); may be internal cache.
        :param requesting_seeder: Neighbor context for seed counting / checks.
        :returns: Health score; stub returns ``1.0``.
        """
        return 1.0

    def backup_check(
        self,
        file_index: str,
        *,
        swappiness: float = 0.5,
    ) -> None:
        """
        Contested backup path when a file stays unhealthy after verification.

        Intended behavior:

        - Wait a small random delay, re-check health; if healthy, stop.
        - If still unhealthy, copy into **emergency** space; if no room, skip or defer.
        - After a larger random delay derived from ``swappiness``, evict from "healthy
          enough" hosted data to free emergency capacity (randomized selection among
          eligible files).

        :param file_index: File that may need emergency backup.
        :param swappiness: Higher → more aggressive / faster eviction pressure (semantics TBD).
        """
        pass
