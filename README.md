# PythonClient

Vertex client: **`vertex.sqlite`** (config + tables), **`VertexClient.config`** (see ``DEFAULT_CONFIG`` / module docstring in ``client.py``), **I2P** stubs. Legacy ``vertex.state`` module removed.

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
from vertex import DEFAULT_CONFIG, VertexClient

c = VertexClient(Path.home() / ".vertex")
# c.change_parameter("Total_Storage", ...)
# c.close()
```
