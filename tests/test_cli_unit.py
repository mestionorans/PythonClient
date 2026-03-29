"""Unit tests for :mod:`CLI.app` (argparse, parsing, daemon loop hooks)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from CLI.app import _build_parser, _timer_interval_seconds, parse_config_value, run_daemon


def test_parse_config_value_json() -> None:
    assert parse_config_value("0.5") == 0.5
    assert parse_config_value('{"a": 1}') == {"a": 1}
    assert parse_config_value("[1, 2]") == [1, 2]


def test_parse_config_value_plain_string() -> None:
    assert parse_config_value("hello") == "hello"
    assert parse_config_value("not json {") == "not json {"


def test_timer_interval_seconds_clamps_non_positive() -> None:
    c = MagicMock()
    c.config = {"Timer_Interval_Hours": 0}
    assert _timer_interval_seconds(c) == 4 * 3600.0
    c.config = {"Timer_Interval_Hours": -1}
    assert _timer_interval_seconds(c) == 4 * 3600.0


def test_timer_interval_hours_two() -> None:
    c = MagicMock()
    c.config = {"Timer_Interval_Hours": 2}
    assert _timer_interval_seconds(c) == 2 * 3600.0


def test_run_daemon_launch_then_two_timers() -> None:
    client = MagicMock()
    client.config = {"Timer_Interval_Hours": 4}
    sleeps: list[float] = []

    def should_stop() -> bool:
        return client.on_timer.call_count >= 2

    run_daemon(client, sleep_fn=lambda s: sleeps.append(s), should_stop=should_stop)

    client.on_launch_or_daily.assert_called_once()
    assert client.on_timer.call_count == 2
    assert len(sleeps) == 2


def test_argparse_load() -> None:
    p = _build_parser()
    a = p.parse_args(["load", "/data/node"])
    assert a.command == "load"
    assert str(a.directory) == "/data/node"


def test_argparse_start() -> None:
    p = _build_parser()
    a = p.parse_args(["start", "https://n/", "/d", "4096", "--force"])
    assert a.command == "start"
    assert a.node_address == "https://n/"
    assert a.total_storage == 4096
    assert a.force is True


def test_argparse_config_set() -> None:
    p = _build_parser()
    a = p.parse_args(
        ["config", "-d", "/tmp/v", "set", "Swappiness", "0.25"]
    )
    assert a.command == "config"
    assert a.cfg_action == "set"
    assert a.key == "Swappiness"
    assert a.value == "0.25"


def test_argparse_config_show() -> None:
    p = _build_parser()
    a = p.parse_args(["config", "--data-dir", "/x", "show"])
    assert a.cfg_action == "show"


def test_argparse_download() -> None:
    p = _build_parser()
    h = "a" * 40
    a = p.parse_args(
        [
            "download",
            "-d",
            "/downloads",
            h,
            "--tracker",
            "http://tr.example/announce",
        ]
    )
    assert a.command == "download"
    assert str(a.data_dir) == "/downloads"
    assert a.torrent_id == h
    assert a.trackers == ["http://tr.example/announce"]


def test_argparse_interactive() -> None:
    p = _build_parser()
    a = p.parse_args(["interactive", "-d", "/node"])
    assert a.command == "interactive"
    assert str(a.data_dir) == "/node"


def test_argparse_search() -> None:
    p = _build_parser()
    a = p.parse_args(
        ["search", "linux", "-d", "/tmp/v", "--tracker", "http://t1/", "--tracker", "http://t2/"]
    )
    assert a.command == "search"
    assert a.query == "linux"
    assert str(a.data_dir) == "/tmp/v"
    assert a.trackers == ["http://t1/", "http://t2/"]


def test_argparse_search_empty_query() -> None:
    p = _build_parser()
    a = p.parse_args(["search", "-d", "/x"])
    assert a.query == ""


def test_argparse_verbose_before_subcommand() -> None:
    p = _build_parser()
    a = p.parse_args(["-v", "load", "/n"])
    assert a.verbose is True
    assert a.command == "load"
