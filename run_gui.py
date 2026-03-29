#!/usr/bin/env python3
"""
Run the Vertex GUI from a source checkout without ``pip install -e .``.

Usage (from repo root):

    python run_gui.py

For ``python -m GUI`` to work, install the project in editable mode and the GUI extra:

    pip install -e ".[gui]"
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from GUI.app import main

if __name__ == "__main__":
    main()
