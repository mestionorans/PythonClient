"""
Command-line interface for :class:`vertex.client.VertexClient`.

Run with ``python -m CLI`` or the ``vertex-cli`` console script.
"""

from __future__ import annotations

from CLI.app import main, parse_config_value, run_daemon

__all__ = ["main", "parse_config_value", "run_daemon"]
