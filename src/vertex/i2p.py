"""
I2P boundary (stubs). :class:`I2P` holds Ed25519 identity; methods are no-ops or local-only
until wired to the network.

Torrent identifiers in Vertex are the **BitTorrent info hash** (40 hex characters). That string is
the same value qBittorrent exposes as ``hash`` in ``torrents/info`` and accepts in ``hashes`` for
delete and info calls—no separate “internal id” is used at the application layer.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import urlparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

__all__ = ["I2P", "DEFAULT_BROADCAST_PORT"]

_SEED_PREFIX = b"VERTEX_SEED\x00"

# Local HTTP endpoint for :meth:`I2P.update_broadcast` (``/publickey``, ``/broadcast``).
DEFAULT_BROADCAST_PORT = 28765

_QBITTORRENT_USERNAME = "admin"
_QBITTORRENT_PASSWORD = "adminpassword"


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class I2P:
    """
    ``data_dir`` is the same writable tree as :class:`~vertex.client.VertexClient` (database,
    tier folders, and any I2P-local state). Installed packages are often read-only; do not use the
    install prefix here.

    **Torrent ids:** Callers should pass the **info hash** (40-character hex), identical to
    qBittorrent's ``hash`` field for that torrent. ``storage.id`` / ``torrent_id`` parameters are
    defined to match that hash one-to-one.

    ``identity`` is the hex-encoded public key. Seeding signs with the given ``key`` or
    the instance private key. ``add_torrent`` should re-sign on a schedule (see config
    ``host_sign_interval_minutes``) in a full implementation.
    """

    def __init__(
        self,
        data_dir: Path | str,
        *,
        private_key: Ed25519PrivateKey | None = None,
        broadcast_host: str = "127.0.0.1",
        broadcast_port: int = DEFAULT_BROADCAST_PORT,
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
        self._broadcast_host = broadcast_host
        self._broadcast_port = broadcast_port
        self._broadcast_lock = threading.Lock()
        self._broadcast_publickey_text = ""
        self._broadcast_body = b"{}"
        self._broadcast_server: _ThreadingHTTPServer | None = None

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
            # qBittorrent 4.x included `id` on each torrents/info row; 5.x list items are hash-keyed
            # and typically omit `id`, so decimal ids may not resolve—prefer the 40-char info hash.
            args = self._rpc("/torrents/info", {"filter": "all"})
            target = int(s)
            for t in args:
                qid = t.get("id")
                if qid is not None and int(qid) == target:
                    h = t.get("hash")
                    return str(h).lower() if h else None
            return None
        return None

    def is_seeding(self, id: str) -> bool:
        """
        True if this torrent is present in the local client and its status is seeding
        (qBittorrent state includes " uploading").

        ``id`` is the torrent **info hash** (same string as qBittorrent's ``hash``), not a UI-only
        numeric id.
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

    def _magnet_uri(self, file_path: str, torrent_id: str, trackers: list[str]) -> str:
        """Magnet for ``urls``; values are not pre-percent-encoded so ``urlencode`` is only one layer."""
        h = torrent_id.strip()
        if self._is_info_hash_hex(h):
            h = h.lower()
        magnet_parts = [f"magnet:?xt=urn:btih:{h}"]

        def _magnet_value(s: str) -> str:
            return s.replace("%", "%25").replace("&", "%26").replace("=", "%3D")

        magnet_parts.append(f"dn={_magnet_value(file_path)}")
        for tracker in trackers:
            magnet_parts.append(f"tr={_magnet_value(tracker)}")
        return "&".join(magnet_parts)

    def _torrents_add_at_path(self, file_path: str, torrent_id: str, trackers: list[str]) -> None:
        """
        Tell qBittorrent to add the magnet and store payload under the parent of ``file_path``,
        using ``rename`` so the torrent root / single-file name matches the destination basename.
        """
        dest = Path(file_path).expanduser().resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        magnet_uri = self._magnet_uri(file_path, torrent_id, trackers)
        self._rpc(
            "/torrents/add",
            {
                "urls": magnet_uri,
                "savepath": str(dest.parent),
                "rename": dest.name,
            },
        )

    def _wait_for_download(
        self,
        id_str: str,
        *,
        timeout_sec: float,
        poll_interval_sec: float,
    ) -> None:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            torrent_hash = self._resolve_torrent_hash(id_str)
            if torrent_hash is None:
                time.sleep(poll_interval_sec)
                continue
            args = self._rpc("/torrents/info", {"hashes": torrent_hash})
            t = next((x for x in args if str(x.get("hash", "")).lower() == torrent_hash), None)
            if t is None:
                time.sleep(poll_interval_sec)
                continue
            state = str(t.get("state", ""))
            progress = float(t.get("progress", 0.0))
            if state in ("missingFiles", "error"):
                raise RuntimeError(f"qBittorrent torrent {torrent_hash!r} is in state {state!r}")
            if progress >= 1.0 or state in ("stalledUP", "uploading", "pausedUP", "queuedUP"):
                return
            time.sleep(poll_interval_sec)
        raise TimeoutError(f"Timed out after {timeout_sec}s waiting for torrent {id_str!r} to finish.")

    def download_file(
        self,
        file_path: str,
        id: str,
        trackers: list[str],
        *,
        wait_complete: bool = False,
        wait_timeout_sec: float = 86400.0,
        poll_interval_sec: float = 2.0,
    ) -> None:
        """
        Register the magnet with qBittorrent and set **save path** and **rename** so data is aimed
        at ``file_path`` (parent directory + basename). Optionally block until the client reports
        the download finished (progress 1.0 or seeding-like state).

        ``id`` is the **info hash** (40 hex chars), aligned with qBittorrent's ``hash`` for the job.

        :meth:`add_torrent` uses the same RPC shape; Vertex can keep calling ``add_torrent`` for
        parity with storage paths.
        """
        self._torrents_add_at_path(file_path, id, trackers)
        if wait_complete:
            self._wait_for_download(id_str=id, timeout_sec=wait_timeout_sec, poll_interval_sec=poll_interval_sec)

    def add_torrent(self, file_path: str, torrent_id: str, trackers: list[str]) -> None:
        """
        Generate a magnet link from the given info and send it to the torrent client via RPC with
        ``savepath`` / ``rename`` derived from ``file_path`` so downloads land next to the
        storage path the client uses.

        ``torrent_id`` is the **info hash** (40 hex chars), same value as qBittorrent's ``hash``.
        """
        self._torrents_add_at_path(file_path, torrent_id, trackers)

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

        ``id`` is the **info hash** (40 hex chars), matching qBittorrent's ``hash`` for RPC
        ``hashes=``.
        """
        self._seed_signatures.pop((file_path, id), None)
        torrent_hash = self._resolve_torrent_hash(id)
        if torrent_hash is None:
            return
        self._rpc("/torrents/delete", {"hashes": torrent_hash, "deleteFiles": False})

    def check_neighbors(self, storage_id: str) -> dict[str, str]:
        """
        Neighbor discovery for the torrent identified by ``storage_id`` (same as ``storage.id``,
        the info hash matching qBittorrent's ``hash``). Return a map of neighbor public-key id
        (hex string) to their latest broadcast payload (typically a JSON string of at-risk file
        entries). **Stub:** empty map until wired.
        """
        del storage_id
        return {}

    def update_broadcast(self, message: str) -> None:
        """
        Sign ``message`` and serve the same payloads as the former ``~/.i2p/eepsite/docroot`` files:
        ``GET /publickey`` (plaintext identity) and ``GET /broadcast`` (signed JSON).
        """
        signature = self._sk.sign(message.encode())
        body = json.dumps({"message": message, "signature": signature.hex()}).encode()
        with self._broadcast_lock:
            self._broadcast_publickey_text = self.identity
            self._broadcast_body = body
            if self._broadcast_server is None:
                handler = _broadcast_handler_factory(self)
                srv = _ThreadingHTTPServer((self._broadcast_host, self._broadcast_port), handler)
                self._broadcast_server = srv
                threading.Thread(target=srv.serve_forever, daemon=True).start()

    @property
    def broadcast_base_url(self) -> str:
        """``http://host:port`` for the broadcast HTTP server (after :meth:`update_broadcast`)."""
        if self._broadcast_server is None:
            raise RuntimeError("Broadcast server not running; call update_broadcast first.")
        host, port = self._broadcast_server.server_address
        host_s = str(host)
        if ":" in host_s and not host_s.startswith("["):
            host_s = f"[{host_s}]"
        return f"http://{host_s}:{port}"

    def count_seeders(self, id: str, _requesting_seeder: str | None = None) -> int:
        """
        Queries the torrent client for the number of seeders on a torrent.

        In normal Vertex usage, ``id`` is the **info hash** (40 hex chars), identical to
        qBittorrent's ``hash``. A decimal string is accepted only as a legacy fallback when the Web
        API exposes a per-torrent ``id`` (older clients); prefer the hash.
        """
        torrent_hash = self._resolve_torrent_hash(id)
        if torrent_hash is None:
            return 0
        args = self._rpc("/torrents/info", {"hashes": torrent_hash})
        for t in args:
            if t.get("hash", "").lower() == torrent_hash:
                return int(t.get("num_seeds", 0))
        return 0


def _broadcast_handler_factory(i2p: I2P) -> type[BaseHTTPRequestHandler]:
    class BroadcastHTTPRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            path = path.rstrip("/") or "/"
            with i2p._broadcast_lock:
                publickey = i2p._broadcast_publickey_text
                broadcast = i2p._broadcast_body
            if path == "/publickey":
                raw = publickey.encode()
                ctype = "text/plain; charset=utf-8"
            elif path == "/broadcast":
                raw = broadcast
                ctype = "application/json; charset=utf-8"
            else:
                self.send_error(404, "Not Found")
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    return BroadcastHTTPRequestHandler
