import json
from pathlib import Path

import pytest

from vertex.i2p import I2P

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


def test_search_stub_returns_sample_echoing_args(tmp_path):
    client = I2P(tmp_path)
    out = client.search("my query", ["http://t/"])
    assert len(out) == 1
    hid = "deadbeefcafebabe0000deadbeefcafebabe0000"
    assert hid in out
    meta = out[hid]
    assert meta["stub"] is True
    assert meta["matched_query"] == "my query"
    assert meta["trackers_filter"] == ["http://t/"]


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


def test_update_broadcast_writes_signed_json(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    client = I2P(tmp_path)
    client.update_broadcast("hello world")

    broadcast_file = tmp_path / ".i2p" / "eepsite" / "docroot" / "broadcast"
    content = json.loads(broadcast_file.read_text())
    assert content["message"] == "hello world"
    assert len(content["signature"]) == 128

    publickey_file = tmp_path / ".i2p" / "eepsite" / "docroot" / "publickey"
    assert publickey_file.read_text() == client.identity


def test_update_broadcast_creates_parent_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    client = I2P(tmp_path)
    client.update_broadcast("test message")

    broadcast_path = tmp_path / ".i2p" / "eepsite" / "docroot" / "broadcast"
    content = json.loads(broadcast_path.read_text())
    assert content["message"] == "test message"

    publickey_path = tmp_path / ".i2p" / "eepsite" / "docroot" / "publickey"
    assert publickey_path.read_text() == client.identity
