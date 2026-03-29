"""
Web GUI for Vertex (NiceGUI): wraps :mod:`CLI.app` (same behavior as ``vertex-cli``).

**Run (after install):** ``pip install ".[gui]"`` (or ``vertex-client[gui]`` from PyPI), then
``vertex-gui`` or ``python -m GUI``.

**Run from a git checkout without installing:** put ``src`` on ``PYTHONPATH``, or use the repo
root ``run_gui.py`` launcher, or ``cd src && python -m GUI``.
"""

from __future__ import annotations

from GUI.app import main

__all__ = ["main"]
