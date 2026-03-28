"""Quick smoke tests — imports, client lifecycle, health helper."""

from __future__ import annotations

from pathlib import Path

import pytest

from vertex import VertexClient, VertexConfig, health_equation
from vertex.state import VertexState


def test_imports() -> None:
    from vertex import HostedRecord, I2P, UnhealthyRecord  # noqa: F401


def test_health_equation() -> None:
    assert health_equation(1.0, 3) < health_equation(1.0, 10)


def test_vertex_client_roundtrip(tmp_path: Path) -> None:
    d = tmp_path / "data"
    c = VertexClient(d)
    assert c.identity
    assert c.state.ed25519_private_key_hex
    c.save_config(VertexConfig(health_threshold=0.4))
    assert c.config.health_threshold == 0.4
    c.signup("node-example", health_threshold=0.55)
    assert c._node_id() == "node-example"
    c.close()

    c2 = VertexClient(d)
    assert c2.state.ed25519_private_key_hex == c.state.ed25519_private_key_hex
    assert c2.config.health_threshold == 0.55
    c2.close()


def test_state_json_file(tmp_path: Path) -> None:
    d = tmp_path / "sd"
    s = VertexState(d)
    s.subscribed_node = "n"
    s.save()
    p = d / "vertex_state.json"
    assert p.is_file()
    s2 = VertexState(d)
    assert s2.subscribed_node == "n"
