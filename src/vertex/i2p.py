"""
I2P boundary (stubs). :class:`I2P` holds Ed25519 identity; methods are no-ops or local-only
until wired to the network.
"""

from __future__ import annotations

from pathlib import Path

from xmlrpc.client import ServerProxy

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

__all__ = ["I2P"]

_SEED_PREFIX = b"VERTEX_SEED\x00"


class I2P:
    """
    ``data_dir`` is the same writable tree as :class:`~vertex.client.VertexClient` (database,
    tier folders, and any I2P-local state). Installed packages are often read-only; do not use the
    install prefix here.

    ``identity`` is the hex-encoded public key. Seeding signs with the given ``key`` or
    the instance private key. ``add_torrent`` should re-sign on a schedule (see config
    ``host_sign_interval_minutes``) in a full implementation.
    """

    def __init__(
        self,
        data_dir: Path | str,
        *,
        private_key: Ed25519PrivateKey | None = None,
    ) -> None:
        self.data_dir: Path = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._sk = private_key if private_key is not None else Ed25519PrivateKey.generate()
        pk = self._sk.public_key()
        raw = pk.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.RPC_IP = "127.0.0.1:7657"
        self.identity: str = raw.hex()

    def download_file(self, file_path: str, id: str, trackers: list[str]) -> None:
        """Downloads a file given an id and tracker lists. Functions similar to add_torrent but will not seed after downloading"""
        pass

    def add_torrent(self, file_path: str, torrent_id: str, trackers: list[str]) -> None:
        """
        Generate a magnet link from the given info and send it to the torrent client via RPC.
        """
        from urllib.parse import quote

        magnet_parts = [f"magnet:?xt=urn:btih:{torrent_id}"]
        name = quote(file_path)
        magnet_parts.append(f"dn={name}")
        for tracker in trackers:
            magnet_parts.append(f"tr={quote(tracker)}")
        magnet_uri = "&".join(magnet_parts)

        with ServerProxy(f"http://{self.RPC_IP}") as proxy:
            proxy.core.add_torrent_url(magnet_uri, {})

    def delete_torrent(self, _torrent_id: str) -> None:
        """
        Removes a torrent from the torrent client
        """
        pass

    def check_neighbours(self, id: str):
        """
        Get all neighbours on a specific torrent.
        Returns a dictionary of their public key IDs and their at risk list.
        Auto filters our users who fail the public key check.
        """
        pass

    def update_broadcast(self, message: str) -> None:
        """Updates the broadcast to whatever message is provided.
        Will also update the signed time stamp"""
        pass

    def count_seeders(self, id: str, trackers: list[str]) -> int:
        """Number of seeders for the torrent identified by ``id`` (stub: always 0)."""
        return 0
