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

try:
    import customtkinter as ctk  # type: ignore[import-untyped]
except ImportError as e:  # pragma: no cover - import guard for optional dep
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


def main() -> None:
    app = VertexGuiApp()
    app.mainloop()


if __name__ == "__main__":
    main()
