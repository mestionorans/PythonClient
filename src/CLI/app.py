"""
``vertex-cli``: load or start a node, or run an interactive shell with a background timer.

**Node directory:** ``config``, ``download``, ``search``, and ``interactive`` require an initialized
node (``vertex.sqlite``) — create one with ``start`` first. ``download`` / ``search`` use that
node's :class:`~vertex.i2p.I2P` instance (same data tree as :class:`~vertex.client.VertexClient`).

**Blocking vs interactive:** ``load`` / ``start`` block while the timer runs. For typing commands
without a second terminal, use ``interactive`` — it runs ``on_launch_or_daily`` and periodic
``on_timer`` in a **background thread** while you use ``config`` / ``download`` / ``search`` from
a prompt in the same process.
"""

from __future__ import annotations

import argparse
import json
import logging
import shlex
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from vertex import VertexClient
from vertex import db_setup as _db

_DB_NAME = "vertex.sqlite"
_LOG = logging.getLogger("vertex.cli")

_INTERACTIVE_HELP = """
Commands (same as CLI subcommands, without 'vertex-cli'):
  help
      Show this help.
  config show
  config get KEY
  config set KEY VALUE
  download TORRENT_ID [--tracker URL ...]
  search [QUERY] [--tracker URL ...]
  quit | exit
      Stop the background timer and exit.
""".strip()


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
        force=True,
    )


def parse_config_value(raw: str) -> Any:
    """Parse ``VALUE`` for ``config set``: try JSON, otherwise return the string."""
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _require_node_db(data_dir: Path) -> None:
    db = data_dir / _DB_NAME
    if not db.is_file():
        print(
            f"error: no node database at {db}. Run `vertex-cli start ...` first, "
            "or use an existing node directory.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _timer_interval_seconds(client: VertexClient) -> float:
    h = float(client.config.get("Timer_Interval_Hours", 4))
    if h <= 0:
        h = 4.0
    return h * 3600.0


def run_daemon(
    client: VertexClient,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """
    Run :meth:`~vertex.client.VertexClient.on_launch_or_daily` once, then call
    :meth:`~vertex.client.VertexClient.on_timer` every ``Timer_Interval_Hours`` from config.

    ``sleep_fn`` and ``should_stop`` are hooks for tests.
    """
    _LOG.info(
        "Running startup: on_launch_or_daily (metadata, at-risk list, one timer tick)."
    )
    client.on_launch_or_daily()
    _LOG.info("Startup finished.")
    stop = should_stop or (lambda: False)
    while not stop():
        interval = _timer_interval_seconds(client)
        _LOG.info(
            "Next on_timer in %.4g h (Timer_Interval_Hours=%s).",
            interval / 3600.0,
            client.config.get("Timer_Interval_Hours", 4),
        )
        sleep_fn(interval)
        if stop():
            break
        _LOG.info("Running periodic on_timer.")
        client.on_timer()


def _interruptible_sleep(total: float, is_stopped: Callable[[], bool]) -> None:
    end = time.monotonic() + total
    while time.monotonic() < end:
        if is_stopped():
            return
        time.sleep(min(1.0, end - time.monotonic()))


def _run_foreground_daemon(client: VertexClient) -> None:
    """SIGINT/SIGTERM-safe loop: on_launch_or_daily once, then on_timer each interval."""
    stop = {"v": False}

    def _handle(_sig: int, _frame: object) -> None:
        _LOG.info("Shutdown signal received; stopping after current step.")
        stop["v"] = True

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    _LOG.info(
        "Daemon: data_dir=%s (Ctrl+C to stop).",
        client.config.get("Directory_Path"),
    )

    def sleep_fn(t: float) -> None:
        _interruptible_sleep(t, lambda: stop["v"])

    def should_stop() -> bool:
        return bool(stop["v"])

    run_daemon(client, sleep_fn=sleep_fn, should_stop=should_stop)


def _dir_has_existing_node(db_path: Path) -> bool:
    if not db_path.is_file():
        return False
    conn = _db.connect(db_path)
    try:
        _db.init_schema(conn)
        if _db.storage_list(conn):
            return True
        loaded = _db.config_load(conn)
        na = loaded.get("NodeAddress")
        return bool(na and str(na).strip())
    finally:
        conn.close()


def _cmd_load(data_dir: Path) -> None:
    _LOG.info("Loading existing node from %s", data_dir)
    db = data_dir / _DB_NAME
    if not db.is_file():
        print(
            f"error: no database at {db} — use `vertex-cli start` to create a new node.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    with VertexClient(data_dir) as client:
        _run_foreground_daemon(client)


def _cmd_start(
    node_address: str, data_dir: Path, total_storage: int, *, force: bool
) -> None:
    _LOG.info("Initializing node under %s (signup to %s)", data_dir, node_address)
    data_dir.mkdir(parents=True, exist_ok=True)
    db = data_dir / _DB_NAME
    if db.is_file() and not force and _dir_has_existing_node(db):
        print(
            "error: directory already has a configured node; use `vertex-cli load` "
            "or choose an empty directory (see --force).",
            file=sys.stderr,
        )
        raise SystemExit(2)

    with VertexClient(data_dir) as client:
        _LOG.info("Running signup (total_storage=%s bytes).", total_storage)
        client.signup(node_address, int(total_storage))
        _LOG.info("Signup done; entering daemon loop.")
        _run_foreground_daemon(client)


def _run_config_actions(
    client: VertexClient, action: str, key: str | None, value: str | None
) -> None:
    if action == "show":
        print(json.dumps(client.config, indent=2, sort_keys=True))
        return
    if action == "get":
        if not key:
            print("error: KEY required for config get.", file=sys.stderr)
            raise SystemExit(2)
        v = client.config.get(key)
        if isinstance(v, (dict, list)):
            print(json.dumps(v))
        else:
            print(v)
        return
    if action == "set":
        if not key or value is None:
            print("error: KEY and VALUE required for config set.", file=sys.stderr)
            raise SystemExit(2)
        client.change_parameter(key, parse_config_value(value))
        _LOG.info("Updated config key %s", key)
        print(f"ok: {key} updated.")
        return


def _cmd_config(data_dir: Path, action: str, key: str | None, value: str | None) -> None:
    _LOG.info("Config %s (data_dir=%s)", action, data_dir)
    _require_node_db(data_dir)
    with VertexClient(data_dir) as client:
        _run_config_actions(client, action, key, value)


def _run_download(client: VertexClient, torrent_id: str, trackers: list[str]) -> None:
    tid = torrent_id.strip()
    _LOG.info("Download: torrent_id=%s (node %s)", tid, client._data_dir)
    if trackers:
        _LOG.info("Trackers: %s", trackers)
    else:
        _LOG.info("No trackers specified (magnet has hash only).")
    file_path = str(client._data_dir / tid)
    _LOG.info("Queueing torrent in client (download_file → add_torrent / magnet)…")
    client._i2p.download_file(file_path, tid, trackers)
    _LOG.info("Torrent queued; watch your Transmission client for download progress.")


def _cmd_download(data_dir: Path, torrent_id: str, trackers: list[str]) -> None:
    _require_node_db(data_dir)
    with VertexClient(data_dir) as client:
        _run_download(client, torrent_id, trackers)


def _run_search(client: VertexClient, query: str, trackers: list[str]) -> None:
    _LOG.info(
        "Search: query=%r trackers=%s",
        query,
        trackers if trackers else "(any)",
    )
    _LOG.info("Calling I2P.search (bitmagnet Torznab API).")
    result = client._i2p.search(query, trackers)
    _LOG.info("Search returned %d entr(y/ies).", len(result))
    print(json.dumps(result, indent=2, sort_keys=True))


def _cmd_search(data_dir: Path, query: str, trackers: list[str]) -> None:
    _require_node_db(data_dir)
    with VertexClient(data_dir) as client:
        _run_search(client, query, trackers)


def _interactive_shell(client: VertexClient, stop_event: threading.Event) -> None:
    print(_INTERACTIVE_HELP, flush=True)
    while not stop_event.is_set():
        try:
            line = input("vertex> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            stop_event.set()
            break
        if not line:
            continue
        try:
            parts = shlex.split(line)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            continue
        if not parts:
            continue
        cmd = parts[0].lower()
        if cmd in ("quit", "exit"):
            stop_event.set()
            break
        if cmd == "help":
            print(_INTERACTIVE_HELP, flush=True)
            continue
        if cmd == "config" and len(parts) >= 2:
            sub = parts[1].lower()
            if sub == "show":
                _run_config_actions(client, "show", None, None)
            elif sub == "get" and len(parts) >= 3:
                _run_config_actions(client, "get", parts[2], None)
            elif sub == "set" and len(parts) >= 4:
                _run_config_actions(client, "set", parts[2], " ".join(parts[3:]))
            else:
                print("usage: config show | config get KEY | config set KEY VALUE", file=sys.stderr)
            continue
        if cmd == "download" and len(parts) >= 2:
            tid = parts[1]
            trackers: list[str] = []
            i = 2
            while i < len(parts):
                if parts[i] == "--tracker" and i + 1 < len(parts):
                    trackers.append(parts[i + 1])
                    i += 2
                else:
                    i += 1
            _run_download(client, tid, trackers)
            continue
        if cmd == "search":
            query = ""
            trackers = []
            i = 1
            while i < len(parts):
                if parts[i] == "--tracker" and i + 1 < len(parts):
                    trackers.append(parts[i + 1])
                    i += 2
                else:
                    if not query:
                        query = parts[i]
                    else:
                        query = f"{query} {parts[i]}"
                    i += 1
            _run_search(client, query, trackers)
            continue
        print(f"error: unknown command {cmd!r}. Type 'help'.", file=sys.stderr)


def _cmd_interactive(data_dir: Path) -> None:
    _require_node_db(data_dir)
    client = VertexClient(data_dir)
    stop_event = threading.Event()

    def daemon_thread_main() -> None:
        def sleep_fn(t: float) -> None:
            end = time.monotonic() + t
            while time.monotonic() < end:
                if stop_event.is_set():
                    return
                time.sleep(min(1.0, end - time.monotonic()))

        def should_stop() -> bool:
            return stop_event.is_set()

        try:
            _LOG.info(
                "Background: timer loop for data_dir=%s",
                client.config.get("Directory_Path"),
            )
            run_daemon(client, sleep_fn=sleep_fn, should_stop=should_stop)
        except Exception:
            _LOG.exception("Daemon thread error")
        finally:
            _LOG.info("Background timer loop stopped.")

    thr = threading.Thread(target=daemon_thread_main, name="vertex-daemon", daemon=True)
    thr.start()

    def _sigint(_s: int, _f: object) -> None:
        _LOG.info("Interrupt — shutting down.")
        stop_event.set()

    signal.signal(signal.SIGINT, _sigint)

    _LOG.info(
        "Interactive mode: timer runs in a background thread; type commands at vertex> "
        "(see 'help')."
    )
    try:
        _interactive_shell(client, stop_event)
    finally:
        stop_event.set()
        thr.join(timeout=120.0)
        client.close()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vertex-cli",
        description="Vertex client: load/start node, interactive shell, config, download, search.",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose (debug) logging.",
    )

    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "load",
        help="Open an existing node and run the timer loop (blocks; use another terminal or `interactive`).",
    ).add_argument(
        "directory",
        type=Path,
        help="Data directory containing vertex.sqlite",
    )

    p_start = sub.add_parser(
        "start",
        help="Create a node (signup), then run the timer loop (blocks).",
    )
    p_start.add_argument("node_address", help="Node URL or address for signup.")
    p_start.add_argument("directory", type=Path, help="Data directory for this node.")
    p_start.add_argument(
        "total_storage",
        type=int,
        help="Total storage budget in bytes (passed to signup).",
    )
    p_start.add_argument(
        "--force",
        action="store_true",
        help="Allow start even if vertex.sqlite already exists (can duplicate data).",
    )

    p_int = sub.add_parser(
        "interactive",
        help="Load node: run timer in a background thread; type config/download/search at a prompt.",
    )
    p_int.add_argument(
        "-d",
        "--data-dir",
        type=Path,
        required=True,
        help="Initialized node directory (vertex.sqlite).",
    )

    p_cfg = sub.add_parser(
        "config",
        help="Show or change persisted parameters (requires initialized node).",
    )
    p_cfg.add_argument(
        "-d",
        "--data-dir",
        type=Path,
        required=True,
        help="Node data directory (vertex.sqlite).",
    )
    cfg_sub = p_cfg.add_subparsers(dest="cfg_action", required=True)
    cfg_sub.add_parser("show", help="Print all config as JSON.")
    g = cfg_sub.add_parser("get", help="Print one key.")
    g.add_argument("key", help="Config key.")
    s = cfg_sub.add_parser("set", help="Set one key (VALUE as JSON or plain string).")
    s.add_argument("key", help="Config key.")
    s.add_argument("value", help="JSON value or string.")

    p_dl = sub.add_parser(
        "download",
        help="Queue a download using this node's I2P client (requires initialized node).",
    )
    p_dl.add_argument(
        "-d",
        "--data-dir",
        type=Path,
        required=True,
        help="Node data directory (vertex.sqlite).",
    )
    p_dl.add_argument(
        "torrent_id",
        help="40-char info-hash hex (btih) or client torrent id.",
    )
    p_dl.add_argument(
        "--tracker",
        action="append",
        dest="trackers",
        default=[],
        metavar="URL",
        help="Tracker announce URL (repeatable).",
    )

    p_search = sub.add_parser(
        "search",
        help="Search local bitmagnet via I2P (Torznab API).",
    )
    p_search.add_argument(
        "-d",
        "--data-dir",
        type=Path,
        required=True,
        help="Node data directory (vertex.sqlite).",
    )
    p_search.add_argument(
        "query",
        nargs="?",
        default="",
        help="Search string for I2P.search (bitmagnet query).",
    )
    p_search.add_argument(
        "--tracker",
        action="append",
        dest="trackers",
        default=[],
        metavar="URL",
        help="Tracker filter (repeatable).",
    )

    return p


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(bool(getattr(args, "verbose", False)))

    if args.command == "load":
        _cmd_load(Path(args.directory).resolve())
    elif args.command == "start":
        _cmd_start(
            args.node_address,
            Path(args.directory).resolve(),
            args.total_storage,
            force=args.force,
        )
    elif args.command == "interactive":
        _cmd_interactive(Path(args.data_dir).resolve())
    elif args.command == "config":
        _cmd_config(
            Path(args.data_dir).resolve(),
            args.cfg_action,
            getattr(args, "key", None),
            getattr(args, "value", None),
        )
    elif args.command == "download":
        _cmd_download(
            Path(args.data_dir).resolve(),
            args.torrent_id,
            list(args.trackers or []),
        )
    elif args.command == "search":
        _cmd_search(
            Path(args.data_dir).resolve(),
            args.query or "",
            list(args.trackers or []),
        )
    else:
        parser.error("unknown command")


if __name__ == "__main__":
    main()
