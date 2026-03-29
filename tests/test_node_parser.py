from __future__ import annotations

import json
import urllib.error

import pytest

from vertex import NodeParser


class _Resp:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_a: object) -> None:
        return None


@pytest.fixture(autouse=True)
def _reset_nodeparser_state() -> None:
    NodeParser._node_payload = None
    NodeParser._init_cursor = 0
    if hasattr(NodeParser.get_init_files, "_wave"):
        delattr(NodeParser.get_init_files, "_wave")


def _sample_payload() -> dict:
    return {
        "Settings": {
            "Health_Ind_Storage_Perc": 0.2,
            "Emergency_Storage_Perc": "0.15",
            "Swappiness": "0.99",
            "TrustQuickness": 0.5,
            "TrustedTracker": "tr1, tr2",
            "Unhealth_Index": "0.25",
            "Priority_Weights": "{0: 10, 1: 20}",
            "Penalty_Limit": "12",
        },
        " hash-a ": {
            "priority": "3",
            "seeders": "7",
            "size_bytes": "12345",
            "metadata": '{"name":"A","lang":"en"}',
        },
        "hash-b": {
            "priority": 1,
            "seeders": 0,
            "size": 456,
            "metadata": "not-json",
        },
        "hash-c": {
            "priority": 2,
            "metadata": {"kind": "dict-meta"},
        },
    }


def test_get_config_parses_live_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _sample_payload()
    monkeypatch.setattr(
        "vertex.NodeParser.urllib.request.urlopen",
        lambda *a, **k: _Resp(payload),
    )

    out = NodeParser.get_config("10.10.170.37:8080")

    assert out["Health_Ind_Storage_Perc"] == 0.2
    assert out["Emergency_Storage_Perc"] == 0.15
    assert out["Swappiness"] == 0.99
    assert out["TrustQuickness"] == 0.5
    assert out["TrustedTracker"] == ["tr1", "tr2"]
    assert out["Unhealth_Index"] == 0.25
    assert out["Priority_Weights"] == {0: 10, 1: 20}
    assert out["Penalty_Limit"] == 12
    assert NodeParser._node_payload == payload


def test_get_config_network_error_returns_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: object, **_k: object):
        raise urllib.error.URLError("down")

    monkeypatch.setattr("vertex.NodeParser.urllib.request.urlopen", _raise)

    out = NodeParser.get_config("http://example.invalid/")

    assert out == NodeParser._DEFAULT_CONFIG
    assert NodeParser._node_payload is None


def test_get_init_files_uses_cached_payload_in_two_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _sample_payload()
    monkeypatch.setattr(
        "vertex.NodeParser.urllib.request.urlopen",
        lambda *a, **k: _Resp(payload),
    )
    NodeParser.get_config("http://10.10.170.37:8080/")

    first = NodeParser.get_init_files(1000)
    second = NodeParser.get_init_files(1000)
    third = NodeParser.get_init_files(1000)

    assert first == ["hash-a", "hash-b"]
    assert second == ["hash-c"]
    assert third == []


def test_get_init_files_fallback_wave_ids_without_payload() -> None:
    assert NodeParser.get_init_files(10) == ["stub-1-a", "stub-1-b"]
    assert NodeParser.get_init_files(10) == ["stub-2-a", "stub-2-b"]
    assert NodeParser.get_init_files(0) == []


def test_fetch_metadata_parses_json_string_metadata() -> None:
    NodeParser._node_payload = _sample_payload()

    meta, pri, size_b, seeders = NodeParser.fetch_metadata("hash-a")

    assert meta == {"name": "A", "lang": "en"}
    assert pri == 3
    assert size_b == 12345
    assert seeders == 7


def test_fetch_metadata_handles_non_json_metadata_string() -> None:
    NodeParser._node_payload = _sample_payload()

    meta, pri, size_b, seeders = NodeParser.fetch_metadata("hash-b")

    assert meta == {"raw_metadata": "not-json"}
    assert pri == 1
    assert size_b == 456
    assert seeders == 0


def test_fetch_metadata_accepts_leading_space_key() -> None:
    NodeParser._node_payload = _sample_payload()

    _meta, pri, size_b, _seeders = NodeParser.fetch_metadata("hash-a")

    assert pri == 3
    assert size_b == 12345


def test_fetch_metadata_fallback_when_id_missing() -> None:
    NodeParser._node_payload = _sample_payload()

    out = NodeParser.fetch_metadata("missing-id")

    assert out == ({"stub_meta": True}, 0, 1024, 0)
