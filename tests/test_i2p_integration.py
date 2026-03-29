"""
Live qBittorrent Web API tests.

Enable with::

    VERTEX_QBIT_INTEGRATION=1 pytest tests/test_i2p_integration.py -v

Optional env::

    VERTEX_QBIT_HOST=127.0.0.1:8080
    VERTEX_QBIT_USER=admin
    VERTEX_QBIT_PASS=adminpassword

If the daemon is unreachable or credentials fail, tests are skipped.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import vertex.i2p as i2p_mod
from vertex.i2p import I2P

pytestmark = [
    pytest.mark.qbittorrent_integration,
    pytest.mark.skipif(
        os.environ.get("VERTEX_QBIT_INTEGRATION", "").lower() not in ("1", "true", "yes"),
        reason="Set VERTEX_QBIT_INTEGRATION=1 to run qBittorrent integration tests.",
    ),
]

# Publicly known info-hash used elsewhere in this repo; add/remove is exercised, not full download.
_SAMPLE_INFO_HASH = "f83c9c934fe967ded372a5da535d11e56d3807f2"
_SAMPLE_TRACKERS = ["udp://tracker.opentrackr.org:1337/announce"]


@pytest.fixture
def live_i2p(tmp_path, monkeypatch):
    host = os.environ.get("VERTEX_QBIT_HOST", "127.0.0.1:8080")
    user = os.environ.get("VERTEX_QBIT_USER", "admin")
    password = os.environ.get("VERTEX_QBIT_PASS", "adminpassword")
    monkeypatch.setattr(i2p_mod, "_QBITTORRENT_USERNAME", user)
    monkeypatch.setattr(i2p_mod, "_QBITTORRENT_PASSWORD", password)
    client = I2P(tmp_path / "i2p_data")
    client.RPC_IP = host
    client._rpc_session_id = None
    try:
        client._rpc("/torrents/info", {"filter": "all"})
    except Exception as exc:
        pytest.skip(f"qBittorrent Web API not usable at http://{host}/: {exc}")
    yield client


def _cleanup_hash(client: I2P, info_hash: str) -> None:
    try:
        client.delete_torrent("", info_hash)
    except Exception:
        pass


def test_integration_login_and_torrents_info(live_i2p: I2P) -> None:
    out = live_i2p._rpc("/torrents/info", {"filter": "all"})
    assert isinstance(out, list)


def test_integration_add_torrent_then_delete(live_i2p: I2P, tmp_path: Path) -> None:
    dest = tmp_path / "qb" / "integration.bin"
    _cleanup_hash(live_i2p, _SAMPLE_INFO_HASH)
    try:
        live_i2p.add_torrent(str(dest), _SAMPLE_INFO_HASH, _SAMPLE_TRACKERS)
        rows = live_i2p._rpc("/torrents/info", {"hashes": _SAMPLE_INFO_HASH.lower()})
        assert isinstance(rows, list) and len(rows) >= 1
        assert str(rows[0].get("hash", "")).lower() == _SAMPLE_INFO_HASH.lower()
    finally:
        _cleanup_hash(live_i2p, _SAMPLE_INFO_HASH)


def test_integration_download_file_without_wait(live_i2p: I2P, tmp_path: Path) -> None:
    dest = tmp_path / "qb" / "dl.bin"
    _cleanup_hash(live_i2p, _SAMPLE_INFO_HASH)
    try:
        live_i2p.download_file(str(dest), _SAMPLE_INFO_HASH, _SAMPLE_TRACKERS, wait_complete=False)
        rows = live_i2p._rpc("/torrents/info", {"hashes": _SAMPLE_INFO_HASH.lower()})
        assert len(rows) >= 1
    finally:
        _cleanup_hash(live_i2p, _SAMPLE_INFO_HASH)


def test_integration_count_seeders_and_is_seeding(live_i2p: I2P, tmp_path: Path) -> None:
    dest = tmp_path / "qb" / "seedcheck.bin"
    _cleanup_hash(live_i2p, _SAMPLE_INFO_HASH)
    try:
        live_i2p.add_torrent(str(dest), _SAMPLE_INFO_HASH, _SAMPLE_TRACKERS)
        n = live_i2p.count_seeders(_SAMPLE_INFO_HASH)
        assert isinstance(n, int) and n >= 0
        seeding = live_i2p.is_seeding(_SAMPLE_INFO_HASH)
        assert isinstance(seeding, bool)
    finally:
        _cleanup_hash(live_i2p, _SAMPLE_INFO_HASH)


def test_integration_download_file_wait_times_out_for_stalled_job(live_i2p: I2P, tmp_path: Path) -> None:
    """Exercise `_wait_for_download` against a live daemon (expect timeout, not a full download)."""
    dest = tmp_path / "qb" / "wait.bin"
    _cleanup_hash(live_i2p, _SAMPLE_INFO_HASH)
    try:
        with pytest.raises(TimeoutError):
            live_i2p.download_file(
                str(dest),
                _SAMPLE_INFO_HASH,
                _SAMPLE_TRACKERS,
                wait_complete=True,
                wait_timeout_sec=2.0,
                poll_interval_sec=0.25,
            )
    finally:
        _cleanup_hash(live_i2p, _SAMPLE_INFO_HASH)


def test_integration_delete_torrent_unknown_hash_no_error(live_i2p: I2P) -> None:
    """Unregistered id should not touch RPC delete (covers early return in delete_torrent)."""
    live_i2p.delete_torrent("", "not-a-valid-torrent-id")
