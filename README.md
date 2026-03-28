# PythonClient

Vertex client per the in-repo design doc: **JSON** snapshot persistence (`vertex_state.json`), **`permanent/` / `emergency/` / `non_permanent/`** quotas from **config**, **I2P** stubs (`grab_file`, `host_file`, `check_neighbor`, `broadcast`, …).

## Setup

Dependencies are declared in **`pyproject.toml`** (runtime: `cryptography`; dev optional: `pytest`). **`requirements.txt`** mirrors runtime deps for plain `pip install -r` workflows.

**Anywhere with Python 3.10+ and pip:**

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"     # editable install + dev deps
# or: pip install -r requirements-dev.txt && pip install -e .
```

**Nix:** use either `nix develop` (Flakes; run `nix flake lock` once if `flake.lock` is missing) or `nix-shell` (uses `shell.nix`). Both provide Python and pip; then create a venv and `pip install -e ".[dev]"` as above.

```python
from pathlib import Path
from vertex import VertexClient, VertexConfig

c = VertexClient(Path.home() / ".vertex")
# c.save_config(VertexConfig(...))
# c.on_launch_or_daily()
# c.close()
```

Scheduling (e.g. “every hour” `check_neighbors` + `broadcast_at_risk`) is **out of scope**—call those methods from your own runner.
