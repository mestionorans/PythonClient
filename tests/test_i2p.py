import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from vertex.i2p import DEFAULT_BROADCAST_PORT, I2P

_INFO_HASH = "f83c9c934fe967ded372a5da535d11e56d3807f2"

_TRACKERS = [
    "http://nyaa.tracker.wf:7777/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
]


def _magnet_value(s: str) -> str:
    return s.replace("%", "%25").replace("&", "%26").replace("=", "%3D")


def _expected_magnet(file_path: str, torrent_id: str, trackers: list[str]) -> str:
    h = torrent_id.strip()
    if len(h) == 40 and all(c in "0123456789abcdefABCDEF" for c in h):
        h = h.lower()
    parts = [f"magnet:?xt=urn:btih:{h}", f"dn={_magnet_value(file_path)}"]
    parts.extend(f"tr={_magnet_value(tr)}" for tr in trackers)
    return "&".join(parts)


def _expected_torrents_add_args(file_path: str, torrent_id: str, trackers: list[str]) -> dict:
    p = Path(file_path).expanduser().resolve()
    return {
        "urls": _expected_magnet(file_path, torrent_id, trackers),
        "savepath": str(p.parent),
        "rename": p.name,
    }


def test_add_torrent_matches_manual_qbittorrent_command(monkeypatch, tmp_path):
    """Same shape as: add_torrent(\"/home/greencheetah/test\", info_hash, tracker list)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    calls: list[tuple[str, dict]] = []

    def fake_rpc(self, method: str, arguments: dict) -> dict:
        calls.append((method, arguments))
        return {}

    monkeypatch.setattr(I2P, "_rpc", fake_rpc)
    client = I2P(tmp_path)

    file_path = "/home/greencheetah/test"
    client.add_torrent(file_path, _INFO_HASH, list(_TRACKERS))

    assert calls == [
        ("/torrents/add", _expected_torrents_add_args(file_path, _INFO_HASH, _TRACKERS)),
    ]


def test_add_torrent_builds_magnet_and_calls_rpc(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    file_path = str(tmp_path / "nested" / "example file.txt")
    torrent_id = "343b3e5adda3e7808edc6198961c630104ac3c28"
    tracker = "http://tracker2.postman.i2p/announce.php"

    calls: list[tuple[str, dict]] = []

    def fake_rpc(self, method: str, arguments: dict) -> dict:
        calls.append((method, arguments))
        return {}

    monkeypatch.setattr(I2P, "_rpc", fake_rpc)

    client = I2P(tmp_path)
    client.add_torrent(file_path, torrent_id, [tracker])

    assert len(calls) == 1
    method, args = calls[0]
    assert method == "/torrents/add"
    assert args == _expected_torrents_add_args(file_path, torrent_id, [tracker])


def test_download_file_same_registration_as_add_torrent(monkeypatch, tmp_path):
    calls: list[tuple[str, dict]] = []

    def fake_rpc(self, method: str, arguments: dict) -> dict:
        calls.append((method, arguments))
        return {}

    monkeypatch.setattr(I2P, "_rpc", fake_rpc)

    fp = str(tmp_path / "dl" / "blob.bin")
    th = "a" * 40
    trs = ["http://tr/announce"]
    client = I2P(tmp_path)
    client.download_file(fp, th, trs, wait_complete=False)
    client.add_torrent(fp, th, trs)

    assert len(calls) == 2
    assert calls[0] == calls[1] == ("/torrents/add", _expected_torrents_add_args(fp, th, trs))


def test_download_file_wait_complete_polls_torrent_info(monkeypatch, tmp_path):
    th = "c" * 40
    n_info = {"count": 0}

    def fake_rpc(self, method: str, arguments: dict) -> dict | list:
        if method == "/torrents/add":
            return {}
        if method == "/torrents/info" and arguments.get("hashes") == th.lower():
            n_info["count"] += 1
            if n_info["count"] < 3:
                return [{"hash": th.upper(), "state": "downloading", "progress": 0.4}]
            return [{"hash": th, "state": "uploading", "progress": 1.0}]
        return []

    monkeypatch.setattr(I2P, "_rpc", fake_rpc)
    monkeypatch.setattr("vertex.i2p.time.sleep", lambda _s: None)

    client = I2P(tmp_path)
    client.download_file(str(tmp_path / "f.bin"), th, [], wait_complete=True)

    assert n_info["count"] == 3


def test_delete_torrent_pops_signature_and_calls_rpc(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    calls: list[tuple[str, dict]] = []

    def fake_rpc(self, method: str, arguments: dict) -> dict:
        calls.append((method, arguments))
        return {}

    monkeypatch.setattr(I2P, "_rpc", fake_rpc)

    th = "a" * 40
    client = I2P(tmp_path)
    client._seed_signatures = {("/tmp/fake.bin", th): b"sig"}

    client.delete_torrent("/tmp/fake.bin", th)

    assert client._seed_signatures.get(("/tmp/fake.bin", th)) is None
    assert calls == [
        ("/torrents/delete", {"hashes": th.lower(), "deleteFiles": False}),
    ]


def test_delete_torrent_numeric_id(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    calls: list[tuple[str, dict]] = []

    def fake_rpc(self, method: str, arguments: dict) -> dict:
        calls.append((method, arguments))
        if method == "/torrents/info" and arguments.get("filter") == "all":
            return [{"id": 42, "hash": "B" * 40}]
        return {}

    monkeypatch.setattr(I2P, "_rpc", fake_rpc)

    client = I2P(tmp_path)
    client.delete_torrent("/tmp/fake.bin", "42")

    assert calls == [
        ("/torrents/info", {"filter": "all"}),
        ("/torrents/delete", {"hashes": ("b" * 40), "deleteFiles": False}),
    ]


def test_count_seeders_returns_int_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    def fake_rpc(self, method: str, arguments: dict) -> dict:
        return []

    monkeypatch.setattr(I2P, "_rpc", fake_rpc)

    client = I2P(tmp_path)
    n = client.count_seeders("1")
    assert isinstance(n, int)
    assert n == 0


def test_count_seeders_returns_seeder_count(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    th = "c" * 40

    def fake_rpc(self, method: str, arguments: dict) -> list | dict:
        if method == "/torrents/info" and arguments.get("filter") == "all":
            return [{"id": 1, "hash": th.upper()}]
        if method == "/torrents/info" and arguments.get("hashes") == th.lower():
            return [{"hash": th, "num_seeds": 5}]
        return []

    monkeypatch.setattr(I2P, "_rpc", fake_rpc)

    client = I2P(tmp_path)
    n = client.count_seeders("1")
    assert n == 5


def test_count_seeders_unknown_id_no_rpc(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    calls: list[tuple[str, dict]] = []

    def fake_rpc(self, method: str, arguments: dict) -> dict:
        calls.append((method, arguments))
        return []

    monkeypatch.setattr(I2P, "_rpc", fake_rpc)

    client = I2P(tmp_path)
    n = client.count_seeders("not-a-valid-id")
    assert n == 0
    assert calls == []


_SAMPLE_NYAA_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa" version="2.0">
<channel>
<item>
<title>Example Release</title>
<link>https://nyaa.si/download/1.torrent</link>
<guid isPermaLink="true">https://nyaa.si/view/1</guid>
<pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
<nyaa:infoHash>deadbeefcafebabe0000deadbeefcafebabe0000</nyaa:infoHash>
<nyaa:seeders>5</nyaa:seeders>
<nyaa:leechers>1</nyaa:leechers>
<nyaa:downloads>10</nyaa:downloads>
<nyaa:categoryId>1_2</nyaa:categoryId>
<nyaa:category>Anime - English-translated</nyaa:category>
<nyaa:size>1.0 GiB</nyaa:size>
</item>
</channel>
</rss>
"""


def test_search_nyaa_parses_rss(monkeypatch, tmp_path):
    class _Resp:
        def read(self) -> bytes:
            return _SAMPLE_NYAA_RSS

        def __enter__(self):
            return self

        def __exit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr("vertex.i2p.urllib.request.urlopen", lambda *a, **k: _Resp())

    client = I2P(tmp_path)
    out = client.search("my query", ["http://t/"])
    assert len(out) == 1
    hid = "deadbeefcafebabe0000deadbeefcafebabe0000"
    assert hid in out
    meta = out[hid]
    assert meta["name"] == "Example Release"
    assert meta["indexer"] == "nyaa.si"
    assert meta["matched_query"] == "my query"
    assert meta["trackers_filter"] == ["http://t/"]
    assert meta["torrent_url"] == "https://nyaa.si/download/1.torrent"
    assert meta["page_url"] == "https://nyaa.si/view/1"
    assert meta["seeders"] == 5
    assert meta["leechers"] == 1
    assert meta["downloads"] == 10


def test_search_empty_query_returns_empty_without_network(tmp_path):
    client = I2P(tmp_path)
    assert client.search("  \t", []) == {}


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", {}),
        ("   \n", {}),
        ('{"x": 1}', {"x": 1}),
        ("[]", []),
        ("Ok.", {}),
        ("ok", {}),
        ("OK.", {}),
    ],
)
def test_parse_rpc_response_json_or_empty_or_ok(tmp_path, raw, expected):
    client = I2P(tmp_path)
    assert client._parse_rpc_response(raw) == expected


@pytest.mark.parametrize("raw", ["Fails.", "fails", "FAILS."])
def test_parse_rpc_response_fails_raises(tmp_path, raw):
    client = I2P(tmp_path)
    with pytest.raises(RuntimeError, match="qBittorrent rejected"):
        client._parse_rpc_response(raw)


def test_parse_rpc_response_unknown_text_raises(tmp_path):
    client = I2P(tmp_path)
    with pytest.raises(RuntimeError, match="non-JSON body"):
        client._parse_rpc_response("<html>not json</html>")


def test_i2p_default_broadcast_port(tmp_path):
    assert I2P(tmp_path)._broadcast_port == DEFAULT_BROADCAST_PORT


def test_update_broadcast_serves_signed_json_over_http(tmp_path):
    client = I2P(tmp_path, broadcast_port=0)
    client.update_broadcast("hello world")
    base = client.broadcast_base_url
    with urllib.request.urlopen(f"{base}/broadcast", timeout=5) as resp:
        content = json.loads(resp.read().decode())
    assert content["message"] == "hello world"
    assert len(content["signature"]) == 128

    with urllib.request.urlopen(f"{base}/publickey", timeout=5) as resp:
        assert resp.read().decode() == client.identity


def test_update_broadcast_second_call_updates_http_payload(tmp_path):
    client = I2P(tmp_path, broadcast_port=0)
    client.update_broadcast("first")
    base = client.broadcast_base_url
    with urllib.request.urlopen(f"{base}/broadcast", timeout=5) as resp:
        assert json.loads(resp.read().decode())["message"] == "first"
    client.update_broadcast("second")
    with urllib.request.urlopen(f"{base}/broadcast", timeout=5) as resp:
        assert json.loads(resp.read().decode())["message"] == "second"


def test_update_broadcast_unknown_path_404(tmp_path):
    client = I2P(tmp_path, broadcast_port=0)
    client.update_broadcast("x")
    req = urllib.request.Request(f"{client.broadcast_base_url}/nope")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5)
    assert exc_info.value.code == 404


def test_broadcast_base_url_before_start_raises(tmp_path):
    client = I2P(tmp_path)
    with pytest.raises(RuntimeError, match="update_broadcast"):
        _ = client.broadcast_base_url


def test_check_neighbors_empty_when_no_broadcast_urls(monkeypatch, tmp_path):
    client = I2P(tmp_path)
    monkeypatch.setattr(I2P, "_neighbor_broadcast_base_urls_for_storage", lambda self, sid: [])
    assert client.check_neighbors("a" * 40) == {}


def test_check_neighbors_returns_verified_message(monkeypatch, tmp_path):
    neighbor = I2P(tmp_path / "neighbor", broadcast_port=0)
    payload = json.dumps([{"id": "a" * 40, "size": 42}])
    neighbor.update_broadcast(payload)
    base = neighbor.broadcast_base_url

    client = I2P(tmp_path / "client")
    monkeypatch.setattr(
        I2P,
        "_neighbor_broadcast_base_urls_for_storage",
        lambda self, sid: [base],
    )

    out = client.check_neighbors("deadbeefcafebabe0000deadbeefcafebabe0000")
    assert out == {neighbor.identity.lower(): payload}


def test_check_neighbors_skips_invalid_signature(monkeypatch, tmp_path):
    neighbor = I2P(tmp_path / "neighbor", broadcast_port=0)
    neighbor.update_broadcast("[]")
    base = neighbor.broadcast_base_url
    # Valid publickey but corrupt the signed body so Ed25519 verification fails.
    with neighbor._broadcast_lock:
        neighbor._broadcast_body = json.dumps(
            {"message": "[]", "signature": "ab" * 64}
        ).encode()

    client = I2P(tmp_path / "client")
    monkeypatch.setattr(
        I2P,
        "_neighbor_broadcast_base_urls_for_storage",
        lambda self, sid: [base],
    )
    assert client.check_neighbors("c" * 40) == {}


def test_check_neighbors_skips_own_publickey(monkeypatch, tmp_path):
    client = I2P(tmp_path, broadcast_port=0)
    client.update_broadcast("[]")
    base = client.broadcast_base_url
    monkeypatch.setattr(
        I2P,
        "_neighbor_broadcast_base_urls_for_storage",
        lambda self, sid: [base],
    )
    assert client.check_neighbors("b" * 40) == {}


def test_peer_ip_to_broadcast_base_url_ipv6():
    from vertex.i2p import _peer_ip_to_broadcast_base_url

    assert _peer_ip_to_broadcast_base_url("2001:db8::1") == "http://[2001:db8::1]:28765"
    assert _peer_ip_to_broadcast_base_url("192.168.0.1") == "http://192.168.0.1:28765"
