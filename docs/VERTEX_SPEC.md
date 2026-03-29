# Vertex client — current layout

- **`data_dir/vertex.sqlite`** — SQLite: `config_kv`, `storage` (`id`, `file_location` as `tier/file_id`, `metadata`, `priority`, `size` bytes), `neighbors`, `unhealth` (see `vertex/db_setup.py`).
- **`data_dir/emergency/`**, **`health_ind/`**, **`general/`** — tier folders; usage is counted by :meth:`vertex.client.VertexClient.update_states`.
- **`VertexClient.config`** — Dict merged with `DEFAULT_CONFIG`; keys are documented in the `vertex/client.py` module docstring.
- **No `vertex.state` module** — removed; use SQLite + `db_setup` helpers instead.

## Config keys (`DEFAULT_CONFIG`)

| Key | Role |
|-----|------|
| `NodeAddress` | Subscribed / primary node address (string). |
| `TrustedTracker` | List of trusted trackers (default `[null]` i.e. `[None]` in Python). |
| `Health_Ind_Storage_Perc` | Fraction of total for health-index storage. |
| `Emergency_Storage_Perc` | Fraction of total for emergency / backup. |
| `Swappiness` | Eviction / tradeoff aggressiveness (0–1 scale). |
| `TrustQuickness` | Trust / neighbor policy speed (app-defined). |
| `Timer_Interval_Hours` | Interval for periodic client tasks (hours; default 4). |
| `Directory_Path` | Root directory string for this client. |
| `Total_Storage` | Total budget in bytes. |

## States (`VertexClient.states`)

Runtime-only totals and “used” placeholders (not persisted); see `_initial_states` in `client.py`.

## Signup (`VertexClient.signup`)

`signup(node_address)` sets `NodeAddress`, calls `NodeParser.get_config` (stub), merges keys into `config` and SQLite, runs `update_states`, computes remaining general / health-ind bytes, calls `NodeParser.get_init_files` per tier (stub), then for each id runs `I2P.download_file` to the tier folder, inserts `storage` with `file_location` `general/<id>` or `health_ind/<id>`, and runs `update_metadata` so `fetch_metadata` fills `metadata`, `priority`, and `size` (a default `I2P()` is used when none is passed to `VertexClient(..., i2p=...)`).

## `NodeParser` (`vertex/NodeParser.py`)

Dummy `get_config` / `get_init_files` until the real node API exists.
