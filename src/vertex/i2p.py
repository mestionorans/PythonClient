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
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

__all__ = ["I2P", "DEFAULT_BROADCAST_PORT"]

_SEED_PREFIX = b"VERTEX_SEED\x00"

# Local HTTP endpoint for :meth:`I2P.update_broadcast` (``/publickey``, ``/broadcast``).
DEFAULT_BROADCAST_PORT = 28765

_QBITTORRENT_USERNAME = "admin"
_QBITTORRENT_PASSWORD = "adminpassword"

_BITMAGNET_BASE_URL = "http://127.0.0.1:3333"
_TORZNAB_XMLNS = "http://torznab.com/schemas/2015/feed"


def _el_text(item: ET.Element, local: str, *, namespace: str = _TORZNAB_XMLNS) -> str:
    el = item.find(f"{{{namespace}}}{local}")
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _el_int(item: ET.Element, local: str, *, namespace: str = _TORZNAB_XMLNS) -> int:
    t = _el_text(item, local, namespace=namespace)
    if not t:
        return 0
    try:
        return int(t)
    except ValueError:
        return 0


def _torznab_attr(item: ET.Element, attr_name: str) -> str:
    for attr in item.findall(f".//{{{_TORZNAB_XMLNS}}}attr"):
        if str(attr.get("name", "")).strip().lower() == attr_name.lower():
            return str(attr.get("value", "")).strip()
    return ""


def _info_hash_from_magnet_uri(uri: str) -> str:
    parsed = urllib.parse.urlparse(uri.strip())
    if parsed.scheme.lower() != "magnet":
        return ""
    qs = urllib.parse.parse_qs(parsed.query)
    for xt in qs.get("xt", []):
        s = str(xt).strip()
        if s.lower().startswith("urn:btih:"):
            h = s[9:].strip()
            if len(h) == 40 and all(c in "0123456789abcdefABCDEF" for c in h):
                return h.lower()
    return ""


def _peer_ip_to_broadcast_base_url(ip: str, port: int = DEFAULT_BROADCAST_PORT) -> str:
    """Build ``http://host:port`` for a qBittorrent peer ``ip`` (IPv4 or IPv6 string)."""
    ip = ip.strip()
    if not ip:
        return ""
    if ":" in ip and not ip.startswith("["):
        host = f"[{ip}]"
    else:
        host = ip
    return f"http://{host}:{port}"


def _is_ed25519_public_key_hex(s: str) -> bool:
    t = s.strip()
    if len(t) != 64:
        return False
    try:
        bytes.fromhex(t)
    except ValueError:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in t)


def _parse_torznab_rss(xml: bytes, query: str, trackers: list[str]) -> dict[str, dict[str, Any]]:
    """Parse a Torznab RSS response into ``{info_hash: metadata}``."""
    root = ET.fromstring(xml)
    out: dict[str, dict[str, Any]] = {}
    for item in root.findall(".//item"):
        ih_raw = _torznab_attr(item, "infohash")
        if not ih_raw:
            ih_raw = _info_hash_from_magnet_uri(_torznab_attr(item, "magneturl"))
        if not ih_raw:
            ih_raw = _info_hash_from_magnet_uri((item.findtext("guid") or "").strip())
        if not ih_raw:
            ih_raw = _info_hash_from_magnet_uri((item.findtext("link") or "").strip())
        if len(ih_raw) != 40 or not all(c in "0123456789abcdefABCDEF" for c in ih_raw):
            continue
        ih = ih_raw.lower()
        title = (item.findtext("title") or "").strip()
        magnet_url = _torznab_attr(item, "magneturl")
        tlink = magnet_url or (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        category = _torznab_attr(item, "category")
        category_id = _torznab_attr(item, "categoryid")
        seeders = _torznab_attr(item, "seeders")
        leechers = _torznab_attr(item, "peers")
        if not leechers:
            leechers = _torznab_attr(item, "leechers")
        downloads = _torznab_attr(item, "grabs")
        size = _torznab_attr(item, "size")
        if not size:
            size = _torznab_attr(item, "filesize")
        out[ih] = {
            "name": title,
            "torrent_url": tlink,
            "page_url": guid,
            "published": pub,
            "seeders": int(seeders) if seeders.isdigit() else _el_int(item, "seeders"),
            "leechers": int(leechers) if leechers.isdigit() else _el_int(item, "peers"),
            "downloads": int(downloads) if downloads.isdigit() else _el_int(item, "grabs"),
            "size": size,
            "category": category,
            "category_id": category_id,
            "matched_query": query,
            "trackers_filter": list(trackers),
            "indexer": "bitmagnet",
        }
    return out


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

    def _rpc_get(self, method: str, arguments: dict) -> dict | list:
        """GET to qBittorrent API (e.g. ``/sync/torrentPeers``)."""
        qs = urllib.parse.urlencode(arguments)
        url = self._rpc_url() + method + "?" + qs
        for attempt in range(2):
            if self._rpc_session_id is None:
                self._login()
            cookie = f"SID={self._rpc_session_id}"
            req = urllib.request.Request(
                url,
                method="GET",
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
        Search a local bitmagnet indexer via Torznab API: **in** — ``search_str``, ``trackers``
        (echoed per result); **out** — ``{info_hash: metadata}`` with ``name``, ``torrent_url``,
        ``page_url``, swarm counts, ``indexer`` (``\"bitmagnet\"``), etc.

        ``trackers`` does not filter the bitmagnet query; it is recorded for Vertex callers that
        pass preferred announce URLs for later download.
        """
        q = search_str.strip()
        if not q:
            return {}
        qs = urllib.parse.urlencode({"t": "search", "q": q})
        url = f"{_BITMAGNET_BASE_URL}/torznab/api?{qs}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "vertex-client/0.1"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"bitmagnet search failed: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"bitmagnet search failed: {e.reason!r}") from e
        try:
            return _parse_torznab_rss(raw, q, trackers)
        except ET.ParseError as e:
            raise RuntimeError(f"bitmagnet returned invalid RSS: {e}") from e

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

    def _neighbor_broadcast_base_urls_for_storage(self, storage_id: str) -> list[str]:
        """
        qBittorrent peer IPs for ``storage_id`` (info hash), as ``http://ip:port`` broadcast URLs
        using :data:`DEFAULT_BROADCAST_PORT`. Peers with no usable IP (e.g. some I2P rows) are skipped.
        """
        torrent_hash = self._resolve_torrent_hash(storage_id)
        if torrent_hash is None:
            return []
        try:
            raw = self._rpc_get("/sync/torrentPeers", {"hash": torrent_hash, "rid": 0})
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            return []
        except (urllib.error.URLError, OSError, TimeoutError):
            return []
        if not isinstance(raw, dict):
            return []
        peers = raw.get("peers")
        if peers is None:
            return []
        if isinstance(peers, dict):
            peer_rows = list(peers.values())
        elif isinstance(peers, list):
            peer_rows = peers
        else:
            return []
        seen: set[str] = set()
        out: list[str] = []
        for p in peer_rows:
            if not isinstance(p, dict):
                continue
            ip = p.get("ip")
            if not ip or not isinstance(ip, str):
                continue
            ip = ip.strip()
            if not ip:
                continue
            base = _peer_ip_to_broadcast_base_url(ip)
            if not base or base in seen:
                continue
            seen.add(base)
            out.append(base)
        return out

    def _fetch_verified_neighbor_broadcast(self, base_url: str) -> tuple[str, str] | None:
        """
        ``GET /publickey`` (canonical key), ``GET /broadcast``, verify Ed25519 signature on
        ``message`` with that key. Returns ``(pubkey_hex, message)`` or ``None``.
        """
        base = base_url.rstrip("/")
        try:
            publickey_req = urllib.request.Request(
                f"{base}/publickey",
                headers={"User-Agent": "vertex-client/0.1"},
            )
            with urllib.request.urlopen(publickey_req, timeout=15) as resp:
                pk_text = resp.read().decode().strip()
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError):
            return None
        if not _is_ed25519_public_key_hex(pk_text):
            return None
        pubkey_hex = pk_text.lower()
        if pubkey_hex == self.identity.lower():
            return None
        try:
            broadcast_req = urllib.request.Request(
                f"{base}/broadcast",
                headers={"User-Agent": "vertex-client/0.1"},
            )
            with urllib.request.urlopen(broadcast_req, timeout=15) as resp:
                broadcast_raw = resp.read().decode()
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError):
            return None
        try:
            obj = json.loads(broadcast_raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        message = obj.get("message")
        sig_hex = obj.get("signature")
        if not isinstance(message, str) or not isinstance(sig_hex, str):
            return None
        sig_hex = sig_hex.strip()
        if not sig_hex:
            return None
        try:
            pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex))
            pk.verify(bytes.fromhex(sig_hex), message.encode())
        except (InvalidSignature, ValueError, TypeError):
            return None
        return (pubkey_hex, message)

    def check_neighbors(self, storage_id: str) -> dict[str, str]:
        """
        Neighbor discovery for the torrent identified by ``storage_id`` (same as ``storage.id``,
        the info hash matching qBittorrent's ``hash``). Uses qBittorrent ``/sync/torrentPeers`` to
        list peer IPs, probes ``http://<ip>:DEFAULT_BROADCAST_PORT`` for each peer's Vertex broadcast
        HTTP server, and returns a map of neighbor public key (from ``GET /publickey``) to the inner
        ``message`` string from a signed ``GET /broadcast`` payload (same shape as
        :meth:`update_broadcast`). Unverifiable or unreachable neighbors are omitted.
        """
        out: dict[str, str] = {}
        for base in self._neighbor_broadcast_base_urls_for_storage(storage_id):
            got = self._fetch_verified_neighbor_broadcast(base)
            if got is None:
                continue
            pk, msg = got
            out[pk] = msg
        return out

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
