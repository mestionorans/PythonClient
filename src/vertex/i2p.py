"""
I2P boundary (stubs). :class:`I2P` holds Ed25519 identity; methods are no-ops or local-only
until wired to the network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

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
        self._seed_signatures: dict[tuple[str, str], bytes] = {}
        self._rpc_session_id: str | None = None

    def _rpc_url(self) -> str:
        return f"http://{self.RPC_IP}/transmission/rpc"

    def _fetch_session_id(self) -> str:
        req = urllib.request.Request(self._rpc_url(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                sid = resp.headers.get("X-Transmission-Session-Id")
        except urllib.error.HTTPError as e:
            if e.code == 409:
                sid = e.headers.get("X-Transmission-Session-Id")
            else:
                raise
        if not sid:
            raise RuntimeError("Transmission RPC: missing X-Transmission-Session-Id")
        return sid.strip()

    def _rpc(self, method: str, arguments: dict) -> dict:
        """
        JSON-RPC POST to Transmission. Returns the ``arguments`` object from the response.
        """
        url = self._rpc_url()
        payload = json.dumps({"method": method, "arguments": arguments}).encode()
        for attempt in range(2):
            if self._rpc_session_id is None:
                self._rpc_session_id = self._fetch_session_id()
            req = urllib.request.Request(
                url,
                data=payload,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "X-Transmission-Session-Id": self._rpc_session_id,
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    body = json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 409 and attempt == 0:
                    self._rpc_session_id = (
                        e.headers.get("X-Transmission-Session-Id") or self._fetch_session_id()
                    )
                    continue
                raise
            else:
                if body.get("result") != "success":
                    raise RuntimeError(f"Transmission RPC error: {body!r}")
                return dict(body.get("arguments") or {})
        raise RuntimeError("Transmission RPC: session retry exhausted")

    def _is_info_hash_hex(self, s: str) -> bool:
        return len(s) == 40 and all(c in "0123456789abcdefABCDEF" for c in s)

    def _resolve_transmission_torrent_id(self, id_str: str) -> int | None:
        s = id_str.strip()
        if s.isdigit():
            return int(s)
        if self._is_info_hash_hex(s):
            args = self._rpc("torrent-get", {"fields": ["id", "hashString"]})
            target = s.lower()
            for t in args.get("torrents", []):
                hs = str(t.get("hashString", "")).lower()
                if hs == target:
                    tid = t.get("id")
                    return int(tid) if tid is not None else None
            return None
        return None

    def is_seeding(self, id: str) -> bool:
        """
        True if this torrent is present in the local client and its status is seeding
        (Transmission status ``6``).
        """
        tid = self._resolve_transmission_torrent_id(id)
        if tid is None:
            return False
        args = self._rpc("torrent-get", {"ids": [tid], "fields": ["status"]})
        torrents = args.get("torrents", [])
        if not torrents:
            return False
        return int(torrents[0].get("status", 0)) == 6

    def download_file(self, file_path: str, id: str, trackers: list[str]) -> None:
        """
        Register the torrent with the client (same as :meth:`add_torrent`). The Vertex client uses
        ``add_torrent`` for all storage-backed downloads so SQLite and the torrent client stay aligned.
        """
        self.add_torrent(file_path, id, trackers)

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

        self._rpc("torrent-add", {"filename": magnet_uri})

    def delete_torrent(self, file_path: str, id: str) -> None:
        """
        Removes a torrent from the torrent client (RPC) and drops any local seed signature.
        """
        self._seed_signatures.pop((file_path, id), None)
        tid = self._resolve_transmission_torrent_id(id)
        if tid is None:
            return
        self._rpc("torrent-remove", {"ids": [tid], "delete-local-data": False})

    def check_neighbors(self, storage_id: str) -> dict[str, str]:
        """
        Neighbor discovery for the torrent identified by ``storage_id`` (same id as ``storage.id`` /
        I2P torrent id). Return a map of neighbor public-key id (hex string) to their latest
        broadcast payload (typically a JSON string of at-risk file entries). **Stub:** empty map
        until wired.
        """
        del storage_id
        return {}

    def update_broadcast(self, message: str) -> None:
        """Updates the broadcast to whatever message is provided."""
        docroot = Path.home() / ".i2p" / "eepsite" / "docroot"
        docroot.mkdir(parents=True, exist_ok=True)

        publickey_path = docroot / "publickey"
        publickey_path.write_text(self.identity)

        signature = self._sk.sign(message.encode())
        broadcast_path = docroot / "broadcast"
        content = json.dumps({"message": message, "signature": signature.hex()})
        broadcast_path.write_text(content)

    def count_seeders(self, id: str, _requesting_seeder: str | None = None) -> int:
        """
        Queries the torrent client for the number of seeders on a torrent.
        ``id`` is a Transmission torrent id (decimal string) or a 40-character info-hash hex.
        """
        tid = self._resolve_transmission_torrent_id(id)
        if tid is None:
            return 0
        args = self._rpc("torrent-get", {"ids": [tid], "fields": ["seeders"]})
        torrents = args.get("torrents", [])
        if not torrents:
            return 0
        return int(torrents[0].get("seeders", 0))
