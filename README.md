# PythonClient

Vertex client for the Library of Mestionora backup network (I2P layer to be wired in).

## Layout

```text
PythonClient/
  pyproject.toml          # setuptools, package `vertex` under src/
  src/vertex/
    __init__.py           # VertexClient, I2P
    client.py             # VertexClient (policy / orchestration)
    i2p.py                # I2P — fetch/seed/neighbor hooks
```

## Install

From the repo root (requires a Python environment with `pip`):

```bash
pip install -e .
```

Then: `from vertex import VertexClient, I2P`.

For a quick check without installing, use `PYTHONPATH=src`.

## I2P vs `VertexClient`

`I2P` holds fetch/seed/neighbor hooks. Pass `VertexClient(i2p=...)` if you need a shared or test instance; otherwise the client constructs a default `I2P`.
