"""
I2P boundary (stubs). :class:`I2P` holds Ed25519 identity; methods are no-ops or local-only
until wired to the network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

__all__ = ["I2P"]

_SEED_PREFIX = b"VERTEX_SEED\x00"

_QBITTORRENT_USERNAME = "admin"
_QBITTORRENT_PASSWORD = "adminpassword"


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
        self.RPC_IP = "127.0.0.1:8080"
        self.identity: str = raw.hex()
        self._seed_signatures: dict[tuple[str, str], bytes] = {}
        self._rpc_session_id: str | None = None

    def _rpc_url(self) -> str:
        return f"http://{self.RPC_IP}/api/v2"

    def _login(self) -> None:
        data = urllib.parse.urlencode({
            "username": _QBITTORRENT_USERNAME,
            "password": _QBITTORRENT_PASSWORD,
        }).encode()
        req = urllib.request.Request(
            f"{self._rpc_url()}/auth/login",
            data=data,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
            if body != "Ok.":
                raise RuntimeError(f"qBittorrent login failed: {body}")
            sid = resp.headers.get("Set-Cookie", "").split("SID=")[1].split(";")[0]
            self._rpc_session_id = sid

    def _parse_rpc_response(self, raw: str) -> dict | list:
        """Many qBittorrent v2 endpoints return empty bodies or plain text (e.g. ``Ok.`` / ``Fails.``), not JSON."""
        text = raw.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        norm = text.lower().rstrip(".")
        if norm == "ok":
            return {}
        if norm == "fails":
            raise RuntimeError(
                "qBittorrent rejected the request (e.g. duplicate torrent, invalid link, or bad parameters): "
                f"{text!r}"
            )
        raise RuntimeError(f"qBittorrent API returned non-JSON body: {text!r}")

    def _rpc(self, method: str, arguments: dict) -> dict | list:
        """
        POST to qbittorrent API. Returns parsed JSON (object or array), or an empty dict when the
        body is empty or a plain ``Ok.``-style reply.
        """
        url = self._rpc_url() + method
        if arguments:
            data = urllib.parse.urlencode(arguments).encode()
        else:
            data = None
        for attempt in range(2):
            if self._rpc_session_id is None:
                self._login()
            cookie = f"SID={self._rpc_session_id}"
            req = urllib.request.Request(
                url,
                data=data,
                method="POST",
                headers={
                    "Cookie": cookie,
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    body = self._parse_rpc_response(resp.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 403 and attempt == 0:
                    self._rpc_session_id = None
                    continue
                raise
            else:
                return body
        raise RuntimeError("qBittorrent API: retry exhausted")

    def _is_info_hash_hex(self, s: str) -> bool:
        return len(s) == 40 and all(c in "0123456789abcdefABCDEF" for c in s)

    def _resolve_torrent_hash(self, id_str: str) -> str | None:
        s = id_str.strip()
        if self._is_info_hash_hex(s):
            return s.lower()
        if s.isdigit():
            args = self._rpc("/torrents/info", {"filter": "all"})
            target = int(s)
            for t in args:
                if t.get("id") == target:
                    return t.get("hash").lower()
            return None
        return None

    def is_seeding(self, id: str) -> bool:
        """
        True if this torrent is present in the local client and its status is seeding
        (qBittorrent state includes " uploading").
        """
        torrent_hash = self._resolve_torrent_hash(id)
        if torrent_hash is None:
            return False
        args = self._rpc("/torrents/info", {"hashes": torrent_hash})
        for t in args:
            if t.get("hash", "").lower() == torrent_hash:
                state = t.get("state", "")
                return "uploading" in state or state == "pausedUP"
        return False

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

        self._rpc("/torrents/add", {"urls": magnet_uri})

    def search(self, search_str: str, trackers: list[str]) -> dict[str, Any]:
        """
        Search for torrents: **in** — ``search_str``, ``trackers``; **out** — ``{id: metadata}``.

        **Stub:** returns a fixed sample map (one entry) so UIs and :mod:`CLI` can be exercised;
        echoes ``search_str`` and ``trackers`` in the metadata. Replace with real indexer /
        network results when implemented.
        """
        sample_id = "deadbeefcafebabe0000deadbeefcafebabe0000"
        return {
            sample_id: {
                "name": "[stub] Sample torrent title",
                "stub": True,
                "matched_query": search_str,
                "trackers_filter": list(trackers),
            }
        }

    def delete_torrent(self, file_path: str, id: str) -> None:
        """
        Removes a torrent from the torrent client (RPC) and drops any local seed signature.
        """
        self._seed_signatures.pop((file_path, id), None)
        torrent_hash = self._resolve_torrent_hash(id)
        if torrent_hash is None:
            return
        self._rpc("/torrents/delete", {"hashes": torrent_hash, "deleteFiles": False})

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
        ``id`` is a qBittorrent torrent id (decimal string) or a 40-character info-hash hex.
        """
        torrent_hash = self._resolve_torrent_hash(id)
        if torrent_hash is None:
            return 0
        args = self._rpc("/torrents/info", {"hashes": torrent_hash})
        for t in args:
            if t.get("hash", "").lower() == torrent_hash:
                return int(t.get("num_seeds", 0))
        return 0
