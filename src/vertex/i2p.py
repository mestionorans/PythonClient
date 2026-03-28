"""
I2P / overlay integration. Method bodies are no-ops until you wire real I2P.

Uses plain strings for IDs and file keys until you introduce shared types.
"""

from __future__ import annotations

__all__ = ["I2P"]


class I2P:
    """
    I2P-facing API used by :class:`vertex.client.VertexClient` via ``self._i2p``.

    Implement fetch, seeding, and neighbor probes here (or subclass) so network code
    stays out of the client class.
    """

    def grab_file(self, index: str, magnet: str) -> list[str]:
        """
        Resolve and download content identified by ``index`` and ``magnet``.

        Real implementation performs the anonymous fetch over I2P and returns neighbor
        identities that contributed pieces (for expanding the local neighbor graph).

        :param index: Network-specific file or chunk index.
        :param magnet: Magnet link (or equivalent) for the swarm.
        :returns: Neighbor IDs that served data for this grab (empty until implemented).
        """
        return []

    def host_file(self, file_path: str, index: str) -> None:
        """
        Begin seeding ``file_path`` under ``index`` for the network.

        :param file_path: Absolute or data-dir-relative path Vertex owns on disk.
        :param index: Logical file index published to the network.
        """
        pass

    def stop_host(self, file_path: str, index: str) -> None:
        """
        Stop seeding the given file/index. Idempotent: unknown pairs are no-ops.

        :param file_path: Same path convention as :meth:`host_file`.
        :param index: Logical file index.
        """
        pass

    def check_neighbor(self, neighbor: str) -> list[tuple[str, str]]:
        """
        Ask I2P / overlay whether this neighbor exposes files considered "at risk".

        :param neighbor: Opaque neighbor identity.
        :returns: ``(file key, risk hint)`` entries; empty until implemented.
        """
        return []
