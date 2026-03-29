"""Integration-style tests for ``vertex-cli`` with real SQLite + :class:`vertex.client.VertexClient`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vertex import VertexClient
from vertex import db_setup

from CLI.app import main


@pytest.mark.integration
def test_cli_config_show_get_set_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("vertex.client.I2P", lambda *a, **k: MagicMock())
    d = tmp_path / "vd"
    c = VertexClient(d)
    try:
        c.change_parameter("Swappiness", 0.4)
    finally:
        c.close()

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        main(["config", "-d", str(d), "show"])
    out = buf.getvalue()
    assert "Swappiness" in out
    assert "0.4" in out

    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        main(["config", "-d", str(d), "get", "Swappiness"])
    assert buf2.getvalue().strip() == "0.4"

    main(["config", "-d", str(d), "set", "Swappiness", "0.9"])

    c2 = VertexClient(d)
    try:
        assert float(c2.config["Swappiness"]) == 0.9
    finally:
        c2.close()


@pytest.mark.integration
def test_cli_start_signup_then_db_has_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("CLI.app._run_foreground_daemon", lambda _c: None)
    monkeypatch.setattr("vertex.client.I2P", lambda *a, **k: MagicMock())

    def fake_get(addr: str) -> dict:
        return {
            "Health_Ind_Storage_Perc": 0.1,
            "Emergency_Storage_Perc": 0.1,
            "TrustedTracker": ["t1"],
        }

    monkeypatch.setattr("vertex.client.NodeParser.get_config", fake_get)

    d = tmp_path / "newnode"
    main(["start", "https://example.invalid/node", str(d), str(8 * 1024 * 1024)])

    c = VertexClient(d)
    try:
        assert c.config["NodeAddress"] == "https://example.invalid/node"
        rows = db_setup.storage_list(c._connection)
        assert len(rows) >= 1
    finally:
        c.close()


@pytest.mark.integration
def test_cli_start_refuses_existing_populated_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("CLI.app._run_foreground_daemon", lambda _c: None)
    monkeypatch.setattr("vertex.client.I2P", lambda *a, **k: MagicMock())

    def fake_get(addr: str) -> dict:
        return {"TrustedTracker": ["t"]}

    monkeypatch.setattr("vertex.client.NodeParser.get_config", fake_get)

    d = tmp_path / "n2"
    main(["start", "https://a/", str(d), "1000000"])

    with pytest.raises(SystemExit) as ei:
        main(["start", "https://b/", str(d), "1000000"])
    assert ei.value.code == 2


@pytest.mark.integration
def test_cli_load_requires_database(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as ei:
        main(["load", str(tmp_path / "no_db_here")])
    assert ei.value.code == 2


@pytest.mark.integration
def test_cli_load_runs_with_existing_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("vertex.client.I2P", lambda *a, **k: MagicMock())
    d = tmp_path / "vd"
    c = VertexClient(d)
    c.close()

    called = {"n": 0}

    def fake_daemon(client: VertexClient) -> None:
        called["n"] += 1
        assert client.db_path == d / "vertex.sqlite"

    monkeypatch.setattr("CLI.app._run_foreground_daemon", fake_daemon)
    main(["load", str(d)])
    assert called["n"] == 1


@pytest.mark.integration
def test_cli_download_calls_i2p_download_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = MagicMock()

    def _i2p_factory(*_a: object, **_k: object) -> MagicMock:
        return mock

    monkeypatch.setattr("vertex.client.I2P", _i2p_factory)
    d = tmp_path / "node"
    c = VertexClient(d)
    c.close()
    tid = "a" * 40
    main(
        [
            "download",
            "-d",
            str(d),
            tid,
            "--tracker",
            "http://tr.example/announce",
        ]
    )
    mock.download_file.assert_called_once()
    args, _kwargs = mock.download_file.call_args
    assert args[1] == tid
    assert args[2] == ["http://tr.example/announce"]


@pytest.mark.integration
def test_cli_search_calls_i2p_and_prints_sample_json(tmp_path: Path) -> None:
    d = tmp_path / "s"
    c = VertexClient(d)
    c.close()
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        main(["search", "test-query", "-d", str(d)])
    out = buf.getvalue()
    assert "deadbeefcafebabe0000deadbeefcafebabe0000" in out
    assert "stub" in out
    assert "test-query" in out
