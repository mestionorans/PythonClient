"""
I2P boundary (stubs). :class:`I2P` holds Ed25519 identity; methods are no-ops or local-only
until wired to the network.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

__all__ = ["I2P"]

_SEED_PREFIX = b"VERTEX_SEED\x00"


class I2P:
    """
    ``identity`` is the hex-encoded public key. Seeding signs with the given ``key`` or
    the instance private key. ``host_file`` should re-sign on a schedule (see config
    ``host_sign_interval_minutes``) in a full implementation.
    """

    def __init__(self, private_key: Ed25519PrivateKey | None = None) -> None:
        self._sk = private_key if private_key is not None else Ed25519PrivateKey.generate()
        pk = self._sk.public_key()
        raw = pk.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.identity: str = raw.hex()
        self._seed_signatures: dict[tuple[str, str], bytes] = {}
        self._last_broadcast: bytes | None = None

    def grab_file(self, index: str, magnet: str) -> list[str]:
        """
        Fetch content; returns **verified** neighbor public keys (hex) that served data.
        """
        return []

    def host_file(self, file_path: str, index: str, key: Ed25519PrivateKey | None = None) -> None:
        """
        Host ``file_path`` under ``index``. Sign with ``key`` or the default private key.
        Production: refresh signature every ``host_sign_interval_minutes`` from config.
        """
        sk = key if key is not None else self._sk
        msg = _seed_message(file_path, index)
        self._seed_signatures[(file_path, index)] = sk.sign(msg)

    def stop_host(self, file_path: str, index: str) -> None:
        self._seed_signatures.pop((file_path, index), None)

    def check_neighbor(self, neighbor: str) -> list[tuple[str, str, bytes, bytes]]:
        """
        Files at risk from ``neighbor``.
        ``(file_key, risk_hint, health_payload, health_signature)`` per row; stub ``[]``.
        """
        return []

    def broadcast(self, message: bytes) -> None:
        """Publish the current at-risk broadcast payload (stub stores locally only)."""
        self._last_broadcast = message

    def count_seeders(self, file_index: str, requesting_seeder: str | None) -> int:
        """I2P seed-count probe for :meth:`vertex.client.VertexClient.get_health` (stub: ``0``)."""
        return 0


def _seed_message(file_path: str, index: str) -> bytes:
    return _SEED_PREFIX + index.encode("utf-8") + b"\x00" + file_path.encode("utf-8")
