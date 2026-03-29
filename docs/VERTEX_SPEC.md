# Vertex client — current layout

- **`data_dir/vertex.sqlite`** — SQLite: `config_kv`, `storage` (`id`, `file_location` as `tier/file_id`, `metadata`, `priority`, `size` bytes), `neighbors` (`public_key`, `trust`, `times_found`, `penalties`), `unhealth` (see `vertex/db_setup.py`).
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

`signup(node_address, starting_total_storage)` sets `NodeAddress`, calls `NodeParser.get_config` (stub), merges keys into `config` and SQLite, sets `Total_Storage` to `starting_total_storage` (bytes), runs `update_states`, computes remaining general / health-ind bytes, calls `NodeParser.get_init_files` per tier (stub), then for each id calls `I2P.add_torrent` (magnet / RPC) and inserts `storage` with `file_location` `general/<id>` or `health_ind/<id>` so the DB row matches a torrent registered in the client, then runs `update_metadata` so `fetch_metadata` fills `metadata`, `priority`, and `size` (it also returns a seeders count for non-local health checks; that value is not stored). Tier moves in `backup_engine` (e.g. unhealth → `emergency/`, or `emergency/` → `general/` during evacuation) physically move the file first, then `I2P.delete_torrent` on the old path and `I2P.add_torrent` on the new path, then update `storage.file_location`. Dropping a `general/` row to free space removes SQLite rows and local data first, then `I2P.delete_torrent`. A default `I2P()` is used when none is passed to `VertexClient(..., i2p=...)`.

## `NodeParser` (`vertex/NodeParser.py`)

Dummy `get_config` / `get_init_files` until the real node API exists.
