"""
Desktop GUI for Vertex: wraps :mod:`CLI.app` (same behavior as ``vertex-cli``).

Install with the ``gui`` extra: ``pip install vertex-client[gui]``, then run ``vertex-gui``
or ``python -m GUI``.
"""

from __future__ import annotations

from GUI.app import main

__all__ = ["main"]
