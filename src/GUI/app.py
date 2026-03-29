"""
``vertex-gui``: NiceGUI web UI for Vertex (glassy modern look).

This GUI wraps your existing :mod:`CLI.app` commands:
* `config show|get|set`
* `download`
* `search`
* optional background daemon via `vertex-cli load|start` (log streaming + Stop)
"""

from __future__ import annotations

import asyncio
import io
import queue
import shutil
import subprocess
import sys
import threading
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from CLI.app import main as cli_main

try:
    from nicegui import ui  # type: ignore
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "NiceGUI is required for the web GUI. Install with `pip install -r requirements.txt` "
        "or `pip install vertex-client[gui]`."
    ) from e


def _vertex_cli_argv() -> list[str]:
    exe = shutil.which("vertex-cli")
    if exe:
        return [exe]
    # Fallback for editable dev checkouts.
    return [sys.executable, "-m", "CLI"]


def _run_cli_capture(argv: list[str]) -> tuple[int, str]:
    """
    Run :func:`CLI.app.main` with captured output.

    Returns `(exit_code, combined_text)`; stdout/stderr are combined for display.
    """
    out = io.StringIO()
    err = io.StringIO()
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            cli_main(argv)
    except SystemExit as e:
        if isinstance(e.code, int):
            code = e.code
        else:
            code = 1
    combined = out.getvalue()
    if err.getvalue():
        combined = (combined + "\n" + err.getvalue()).strip()
    return code, combined.strip()


def main() -> None:
    # Global CSS for a "glassy" look.
    ui.add_head_html(
        """
<style>
  body {
    background:
      radial-gradient(1000px circle at 10% 10%, rgba(99,102,241,.35), transparent 45%),
      radial-gradient(800px circle at 80% 30%, rgba(34,211,238,.20), transparent 40%),
      radial-gradient(900px circle at 40% 90%, rgba(168,85,247,.18), transparent 45%),
      linear-gradient(180deg, rgba(0,0,0,.65), rgba(0,0,0,.80));
  }
  .q-card, .q-dialog__inner, .q-expansion-item__content {
    background: rgba(255,255,255,.06) !important;
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    border: 1px solid rgba(255,255,255,.14);
    border-radius: 18px;
  }
  .q-input__control, .q-field, .q-field__control, .q-btn {
    border-radius: 14px !important;
  }
  .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
    font-size: 12px;
  }
</style>
"""
    )

    log = ui.log(max_lines=4000).classes("mono")

    # Daemon process state (optional background timer loop).
    daemon_state: dict[str, Any] = {
        "proc": None,
        "queue": queue.Queue[str](),
        "thread": None,
    }

    def _append_log_line(line: str) -> None:
        line = line.rstrip("\n")
        if not line:
            return
        log.push(line)

    def _drain_daemon_queue() -> None:
        q: queue.Queue[str] = daemon_state["queue"]
        while True:
            try:
                line = q.get_nowait()
            except queue.Empty:
                break
            _append_log_line(line)

    ui.timer(0.2, _drain_daemon_queue)

    async def _run_cli(argv: list[str], *, banner: str | None = None) -> None:
        if banner:
            log.push(banner)

        code, text = await asyncio.to_thread(_run_cli_capture, argv)
        if text:
            # NiceGUI log handles multi-line strings as-is.
            for part in text.splitlines():
                _append_log_line(part)
        log.push(f"[exit {code}]")

    def _vertex_data_dir_from_ui(ui_value: str) -> str:
        # CLI expects a filesystem path; we keep it permissive.
        return ui_value.strip()

    async def _daemon_start_load() -> None:
        if daemon_state["proc"] is not None:
            return
        d = _vertex_data_dir_from_ui(data_dir.value)
        if not d:
            ui.notify("Enter node data directory first.", color="negative")
            return
        cmd = _vertex_cli_argv() + ["load", d]
        _daemon_spawn(cmd)

    async def _daemon_start_signup() -> None:
        if daemon_state["proc"] is not None:
            return
        d = _vertex_data_dir_from_ui(data_dir.value)
        addr = node_address.value.strip()
        if not addr:
            ui.notify("Enter node address for signup first.", color="negative")
            return
        try:
            bytes_total = int(total_storage.value.strip())
        except Exception:
            ui.notify("Total storage must be an integer (bytes).", color="negative")
            return
        cmd = _vertex_cli_argv() + ["start", addr, d, str(bytes_total)]
        if start_force.value:
            cmd.append("--force")
        _daemon_spawn(cmd)

    def _daemon_spawn(cmd: list[str]) -> None:
        log.push(f"starting daemon: {' '.join(cmd)}")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as e:
            ui.notify(f"Failed to start daemon: {e}", color="negative")
            return

        daemon_state["proc"] = proc
        daemon_state["thread"] = threading.Thread(
            target=_daemon_reader_thread, args=(proc,), daemon=True
        )
        daemon_state["thread"].start()

    def _daemon_reader_thread(proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            daemon_state["queue"].put(line)
        daemon_state["queue"].put("[daemon exited]")
        daemon_state["proc"] = None

    def _daemon_stop() -> None:
        proc = daemon_state.get("proc")
        if proc is None:
            return
        log.push("stopping daemon...")
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        daemon_state["proc"] = None
        daemon_state["queue"].put("[daemon stopped]")

    with ui.column().classes("w-full items-stretch q-gutter-y-md"):
        ui.label("Vertex GUI").classes("text-h4 text-weight-bold")
        ui.label("Actions run via your existing `vertex-cli`. Daemon mode streams logs here.").classes(
            "text-subtitle1"
        )

        with ui.card().classes("q-pa-md"):
            with ui.row().classes("q-col-gutter-md w-full"):
                # Left side: controls.
                with ui.column().classes("w-3/5 q-gutter-y-md"):
                    data_dir = ui.input(
                        label="Node data directory (contains vertex.sqlite)",
                        placeholder=str(Path.home() / "vertex-node"),
                        value=str(Path.home() / "vertex-node"),
                    )

                    with ui.expansion("Config"):
                        cfg_key = ui.input(label="Key", placeholder="Swappiness")
                        cfg_value = ui.input(
                            label="Value (JSON or string)",
                            placeholder='e.g. 0.5 or "hello world"',
                        )
                        with ui.row():
                            ui.button(
                                "Show config",
                                on_click=lambda: _run_cli(
                                    ["config", "-d", data_dir.value or "" , "show"],
                                    banner="config show",
                                ),
                            )
                            ui.button(
                                "Get key",
                                on_click=lambda: _run_cli(
                                    ["config", "-d", data_dir.value or "" , "get", (cfg_key.value or "").strip()],
                                    banner="config get",
                                ),
                            )
                            ui.button(
                                "Set key",
                                on_click=lambda: _run_cli(
                                    [
                                        "config",
                                        "-d",
                                        data_dir.value or "",
                                        "set",
                                        (cfg_key.value or "").strip(),
                                        cfg_value.value or "",
                                    ],
                                    banner="config set",
                                ),
                            )

                    with ui.expansion("Download"):
                        dl_torrent_id = ui.input(label="Torrent id / info-hash (40 hex or numeric)")
                        dl_trackers = ui.textarea(
                            label="Trackers (one per line, optional)",
                            placeholder="http://tracker1/announce\nudp://tracker2/announce",
                            rows=4,
                        )
                        ui.button(
                            "Queue download",
                            on_click=lambda: _download_action(
                                dl_torrent_id.value or "",
                                dl_trackers.value or "",
                            ),
                        )

                    with ui.expansion("Search"):
                        sr_query = ui.input(label="Query (stub returns sample JSON)")
                        sr_trackers = ui.textarea(
                            label="Tracker filters (one per line, optional)",
                            placeholder="http://tracker/announce",
                            rows=3,
                        )
                        ui.button(
                            "Search",
                            on_click=lambda: _search_action(
                                sr_query.value or "",
                                sr_trackers.value or "",
                            ),
                        )

                    with ui.expansion("Daemon (background timer loop)"):
                        node_address = ui.input(label="Node address (for `start` only)", placeholder="https://example/node")
                        total_storage = ui.input(label="Total storage (bytes, for start)", placeholder="8388608")
                        start_force = ui.checkbox(label="--force (allow start even if already initialized)")

                        with ui.row():
                            ui.button("Start load", on_click=_daemon_start_load)
                            ui.button("Start signup (start)", on_click=_daemon_start_signup)
                        ui.button("Stop daemon", color="negative", on_click=_daemon_stop)

                # Right side: output log.
                with ui.column().classes("w-2/5"):
                    ui.label("Output log").classes("text-subtitle2 q-mb-sm")
                    log

        ui.label("Note: daemon + one-shot actions may run concurrently.").classes("text-caption")

    def _parse_lines(raw: str) -> list[str]:
        return [ln.strip() for ln in raw.splitlines() if ln.strip()]

    async def _download_action(torrent_id: str, trackers_raw: str) -> None:
        d = data_dir.value or ""
        if not d:
            ui.notify("Enter node data directory.", color="negative")
            return
        tid = torrent_id.strip()
        if not tid:
            ui.notify("Enter torrent id / info-hash.", color="negative")
            return
        trackers = _parse_lines(trackers_raw)
        argv = ["download", "-d", d, tid]
        for tr in trackers:
            argv += ["--tracker", tr]
        await _run_cli(argv, banner="download ...")

    async def _search_action(query: str, trackers_raw: str) -> None:
        d = data_dir.value or ""
        if not d:
            ui.notify("Enter node data directory.", color="negative")
            return
        q = query.strip()
        trackers = _parse_lines(trackers_raw)
        argv = ["search", "-d", d, q]
        for tr in trackers:
            argv += ["--tracker", tr]
        await _run_cli(argv, banner=f"search {q!r}")

    # NiceGUI needs a synchronous entrypoint; all handlers are async-capable.
    ui.run(title="Vertex GUI", dark=True)


if __name__ == "__main__":
    main()

"""
``vertex-gui``: CustomTkinter front-end that delegates to :func:`CLI.app.main` and ``vertex-cli``.

Quick actions (config / download / search) run in a worker thread by calling the same entry
point as the CLI. Long-running daemon modes (``load`` / ``start`` / ``interactive``) run in a
subprocess so the UI stays responsive and **Stop** can terminate the process.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import threading
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from CLI.app import main as cli_main


def _tk_missing_in_chain(err: BaseException | None) -> bool:
    while err is not None:
        if isinstance(err, ModuleNotFoundError) and err.name in ("_tkinter", "tkinter"):
            return True
        if "no module named '_tkinter'" in str(err).lower():
            return True
        err = err.__cause__ or err.__context__
    return False


try:
    import customtkinter as ctk  # type: ignore[import-untyped]
except ImportError as e:  # pragma: no cover - import guard for optional dep
    if _tk_missing_in_chain(e):
        raise ImportError(
            "The GUI needs Tcl/Tk support in your Python (the _tkinter extension). "
            "pip install customtkinter is not enough on its own. On Debian/Ubuntu: "
            "sudo apt install python3-tk. Fedora: sudo dnf install python3-tkinter. "
            "Arch: sudo pacman -S tk. NixOS: use python3Full or add tkinter to your shell/env. "
            "Then run again."
        ) from e
    raise ImportError(
        "The GUI requires customtkinter. Install with: pip install vertex-client[gui]"
    ) from e

from tkinter import filedialog


def _vertex_cli_argv() -> list[str]:
    exe = shutil.which("vertex-cli")
    if exe:
        return [exe]
    return [sys.executable, "-m", "CLI"]


def run_cli_sync(argv: list[str]) -> tuple[int, str]:
    """Invoke :func:`CLI.app.main` with ``argv``; capture stdout/stderr; return exit code and text."""
    out = io.StringIO()
    err = io.StringIO()
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            cli_main(argv)
    except SystemExit as e:
        c = e.code
        code = int(c) if isinstance(c, int) else 1
    text = out.getvalue()
    e_txt = err.getvalue()
    if e_txt:
        text = text + ("\n" if text and not text.endswith("\n") else "") + e_txt
    return code, text.rstrip() or "(no output)"


class VertexGuiApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Vertex")
        self.geometry("920x640")
        self.minsize(720, 520)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._daemon_proc: subprocess.Popen | None = None

        self._build_header()
        self._build_tabs()
        self._build_log()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Hyprland/Wayland sometimes leaves Tk windows without proper focus on open,
        # which makes widgets ignore mouse clicks. This forces a raise/focus once.
        self.after(50, self._fixup_focus)

    def _build_header(self) -> None:
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(16, 8))

        ctk.CTkLabel(top, text="Node data directory", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w"
        )
        row = ctk.CTkFrame(top, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))
        self._data_dir = ctk.CTkEntry(row, placeholder_text=str(Path.home() / "vertex-node"))
        self._data_dir.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(row, text="Browse…", width=100, command=self._browse_data_dir).pack(
            side="right"
        )

    def _browse_data_dir(self) -> None:
        d = filedialog.askdirectory(title="Vertex node directory (vertex.sqlite)")
        if d:
            self._data_dir.delete(0, "end")
            self._data_dir.insert(0, d)

    def _build_tabs(self) -> None:
        self._tabs = ctk.CTkTabview(self)
        self._tabs.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        self._tabs.add("Config")
        self._tabs.add("Download")
        self._tabs.add("Search")
        self._tabs.add("Daemon")

        self._tab_config(self._tabs.tab("Config"))
        self._tab_download(self._tabs.tab("Download"))
        self._tab_search(self._tabs.tab("Search"))
        self._tab_daemon(self._tabs.tab("Daemon"))

    def _tab_config(self, parent: ctk.CTkFrame) -> None:
        f = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=8, pady=8)

        ctk.CTkButton(f, text="Show full config (JSON)", command=lambda: self._run_cli(["config", "-d", self._dd(), "show"])).pack(
            fill="x", pady=(0, 12)
        )

        ctk.CTkLabel(f, text="Get one key").pack(anchor="w")
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.pack(fill="x", pady=(4, 12))
        self._cfg_get_key = ctk.CTkEntry(row, placeholder_text="Key")
        self._cfg_get_key.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(row, text="Get", width=80, command=self._config_get).pack(side="right")

        ctk.CTkLabel(f, text="Set key / value").pack(anchor="w")
        self._cfg_set_key = ctk.CTkEntry(f, placeholder_text="Key")
        self._cfg_set_key.pack(fill="x", pady=(4, 4))
        self._cfg_set_val = ctk.CTkEntry(f, placeholder_text='Value (JSON or plain string, e.g. 0.5 or "hello")')
        self._cfg_set_val.pack(fill="x", pady=(0, 8))
        ctk.CTkButton(f, text="Set", command=self._config_set).pack(fill="x")

    def _config_get(self) -> None:
        k = self._cfg_get_key.get().strip()
        if not k:
            self._append_log("error: enter a config key.")
            return
        self._run_cli(["config", "-d", self._dd(), "get", k])

    def _config_set(self) -> None:
        k = self._cfg_set_key.get().strip()
        v = self._cfg_set_val.get()
        if not k:
            self._append_log("error: enter a config key.")
            return
        self._run_cli(["config", "-d", self._dd(), "set", k, v])

    def _tab_download(self, parent: ctk.CTkFrame) -> None:
        f = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=8, pady=8)

        ctk.CTkLabel(f, text="Torrent id (40-char hex or client id)").pack(anchor="w")
        self._dl_id = ctk.CTkEntry(f, placeholder_text="info-hash hex…")
        self._dl_id.pack(fill="x", pady=(4, 8))

        ctk.CTkLabel(f, text="Tracker URLs (one per line, optional)").pack(anchor="w")
        self._dl_trackers = ctk.CTkTextbox(f, height=100)
        self._dl_trackers.pack(fill="x", pady=(4, 8))

        ctk.CTkButton(f, text="Queue download", command=self._download).pack(fill="x")

    def _download(self) -> None:
        tid = self._dl_id.get().strip()
        if not tid:
            self._append_log("error: enter a torrent id.")
            return
        raw = self._dl_trackers.get("1.0", "end").strip()
        trackers = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        argv = ["download", "-d", self._dd(), tid]
        for t in trackers:
            argv.extend(["--tracker", t])
        self._run_cli(argv)

    def _tab_search(self, parent: ctk.CTkFrame) -> None:
        f = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=8, pady=8)

        ctk.CTkLabel(f, text="Query").pack(anchor="w")
        self._search_q = ctk.CTkEntry(f, placeholder_text="Search string…")
        self._search_q.pack(fill="x", pady=(4, 8))

        ctk.CTkLabel(f, text="Tracker filters (one per line, optional)").pack(anchor="w")
        self._search_tr = ctk.CTkTextbox(f, height=100)
        self._search_tr.pack(fill="x", pady=(4, 8))

        ctk.CTkButton(f, text="Search", command=self._search).pack(fill="x")

    def _search(self) -> None:
        q = self._search_q.get().strip()
        raw = self._search_tr.get("1.0", "end").strip()
        trackers = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        argv = ["search", "-d", self._dd(), q]
        for t in trackers:
            argv.extend(["--tracker", t])
        self._run_cli(argv)

    def _tab_daemon(self, parent: ctk.CTkFrame) -> None:
        f = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=8, pady=8)

        ctk.CTkLabel(
            f,
            text="Long-running modes run as a separate process (same as vertex-cli). Use Stop to end.",
            wraplength=800,
            justify="left",
        ).pack(anchor="w", pady=(0, 12))

        ctk.CTkLabel(f, text="Load — run timer loop for existing node").pack(anchor="w")
        ctk.CTkButton(f, text="Start load", command=self._daemon_load).pack(fill="x", pady=(4, 16))

        ctk.CTkLabel(f, text="Start — create node (signup) then timer").pack(anchor="w")
        self._start_url = ctk.CTkEntry(f, placeholder_text="Node URL / address")
        self._start_url.pack(fill="x", pady=(4, 4))
        self._start_dir = ctk.CTkEntry(f, placeholder_text="Directory (defaults to node data dir above)")
        self._start_dir.pack(fill="x", pady=(0, 4))
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))
        self._start_bytes = ctk.CTkEntry(row, placeholder_text="Total storage (bytes)")
        self._start_bytes.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._start_force = ctk.CTkCheckBox(row, text="--force")
        self._start_force.pack(side="right")
        ctk.CTkButton(f, text="Start signup + daemon", command=self._daemon_start).pack(fill="x", pady=(0, 16))

        ctk.CTkLabel(f, text="Interactive — timer in background + shell (output in terminal)").pack(anchor="w")
        ctk.CTkButton(f, text="Start interactive (subprocess)", command=self._daemon_interactive).pack(
            fill="x", pady=(4, 16)
        )

        row2 = ctk.CTkFrame(f, fg_color="transparent")
        row2.pack(fill="x", pady=(8, 0))
        ctk.CTkButton(row2, text="Stop daemon process", fg_color="#8B3A3A", hover_color="#6B2A2A", command=self._daemon_stop).pack(
            side="left", fill="x", expand=True, padx=(0, 8)
        )

    def _dd(self) -> str:
        return self._data_dir.get().strip() or str(Path.home() / "vertex-node")

    def _append_log(self, text: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", text + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _run_cli(self, argv: list[str]) -> None:
        def work() -> None:
            code, out = run_cli_sync(argv)
            self.after(0, lambda: self._append_log(f"[exit {code}]\n{out}"))

        threading.Thread(target=work, daemon=True).start()

    def _daemon_load(self) -> None:
        self._spawn_daemon(_vertex_cli_argv() + ["load", self._dd()])

    def _daemon_start(self) -> None:
        url = self._start_url.get().strip()
        if not url:
            self._append_log("error: enter node address for start.")
            return
        d = self._start_dir.get().strip() or self._dd()
        bs = self._start_bytes.get().strip()
        if not bs.isdigit():
            self._append_log("error: total storage must be an integer (bytes).")
            return
        cmd = _vertex_cli_argv() + ["start", url, d, bs]
        if self._start_force.get():
            cmd.append("--force")
        self._spawn_daemon(cmd)

    def _daemon_interactive(self) -> None:
        self._spawn_daemon(_vertex_cli_argv() + ["interactive", "-d", self._dd()])

    def _spawn_daemon(self, cmd: list[str]) -> None:
        self._daemon_stop()
        try:
            self._daemon_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as e:
            self._append_log(f"error: could not start process: {e}")
            return

        self._append_log(f"started: {' '.join(cmd)}")

        def reader() -> None:
            assert self._daemon_proc and self._daemon_proc.stdout
            for line in self._daemon_proc.stdout:
                self.after(0, lambda l=line: self._append_log(l.rstrip()))

        threading.Thread(target=reader, daemon=True).start()

    def _daemon_stop(self) -> None:
        if self._daemon_proc is None:
            return
        p = self._daemon_proc
        self._daemon_proc = None
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        self._append_log("daemon process stopped.")

    def _build_log(self) -> None:
        ctk.CTkLabel(self, text="Output", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=16, pady=(0, 4)
        )
        self._log = ctk.CTkTextbox(self, height=180, font=ctk.CTkFont(family="monospace", size=12))
        self._log.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self._log.configure(state="disabled")

    def _on_close(self) -> None:
        self._daemon_stop()
        self.destroy()

    def _fixup_focus(self) -> None:
        try:
            self.lift()
            self.focus_force()
            # Temporary topmost toggle can help some compositors deliver events.
            self.attributes("-topmost", True)
            self.attributes("-topmost", False)
        except Exception:
            # If the platform doesn't support the attribute, ignore and continue.
            pass


def main() -> None:
    app = VertexGuiApp()
    app.mainloop()


if __name__ == "__main__":
    main()
