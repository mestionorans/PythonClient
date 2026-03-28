# Vertex client — strict behavior specification

This document is the **contract** for the current Python implementation. If the code disagrees with this file, treat the code as authoritative until you fix one or the other.

**Out of scope (by design in this repo):**

- Any real I2P, BitTorrent, or network I/O inside `I2P` methods (they are stubs or local-only side effects).
- Timers, daemons, cron, or background scheduling. Call `VertexClient` methods from your own runner.
- Parsing or downloading the BDFL node database over the network. You must feed catalog data via `ingest_catalog_priorities` (or direct `VertexStore` use) when you have it, or via [`NODE_API.md`](NODE_API.md) / `NodeClient` once wired (trusted index servers first; full node only when no trusted indexes).
- Parsing signed **health payload bytes** for an embedded numeric health value. After signature verification, health is **only** the **local** `_local_health_score`; the payload content is not interpreted.

---

## 1. On-disk layout

All paths are under a single **`data_dir`** passed to `VertexClient(data_dir)`.

| Path | Purpose |
|------|---------|
| `data_dir/vertex.sqlite` | SQLite database (see §4). |
| `data_dir/db/` | **Hosted payload files** only (copies made by `register_host`). Filenames are opaque; logical index is in SQL. |
| `data_dir/emergency/` | **Emergency backup copies** of hosted blobs (files created by `backup_check`). |

**Creates:** `VertexClient.__init__` creates `data_dir`, `db/`, and `emergency/` if missing. `VertexStore` creates `vertex.sqlite`.

---

## 2. Identity and signing

### 2.1 Vertex identity

- **Format:** 64-character hex string = 32-byte Ed25519 public key (raw).
- **Source:** `VertexClient.identity` == `I2P.identity`.
- **Persistence:** If `VertexClient` is constructed **without** injecting `i2p=`, the Ed25519 **private** key is stored in SQLite `settings.ed25519_private_key_hex` (hex of 32 raw private key bytes). First run generates a key and writes it; later runs reload it.

### 2.2 Neighbor / trusted identities

- **Expected format:** Same as vertex identity: Ed25519 public key hex (64 hex chars for standard Ed25519 public keys). Other strings may cause `from_public_bytes` to fail in `get_health` (returns `0.0`).
- **Neighbors:** Rows in `neighbors` with `blacklisted = 0`. `list_neighbors` / `VertexClient.neighbors` **exclude** blacklisted rows.
- **Trusted:** Rows in `trusted`. Only these identities may have signed health accepted past the first gate in `get_health`.

### 2.3 Seed announcement (I2P stub, local only)

**Canonical message bytes** (verifiers must rebuild identically):

```text
b"VERTEX_SEED\x00" + index.encode("utf-8") + b"\x00" + file_path.encode("utf-8")
```

- `host_file(path, index)` signs this message and stores signature in memory (`I2P._seed_signatures`). **Nothing is broadcast** until you implement I2P.
- `stop_host` removes that entry.

### 2.4 Health broadcast material (for gossip you implement)

**Canonical message bytes:**

```text
b"VERTEX_HEALTH\x00" + file_index.encode("utf-8") + b"\x00" + repr(sorted(metadata.items())).encode("utf-8")
```

- `get_health(..., for_broadcast=True)` signs this with the vertex private key and sets `last_health_broadcast = (payload, signature)`.
- **Stability warning:** `repr(sorted(metadata.items()))` depends on Python’s `repr` for keys/values; peers must use the **same** serialization or signatures will not verify.

---

## 3. Numeric thresholds and settings

Stored in `settings` (string values); defaults applied once in `_defaults()` if missing.

| Key | Default | Meaning |
|-----|---------|---------|
| `penalty_max` | `5` | When `increment_penalty` reaches this value **after** an increment in `periodic_neighbor_check`, the neighbor is **blacklisted** (`blacklisted = 1`). |
| `emergency_max_bytes` | `104857600` (100 MiB) | Upper bound on **total file size** under `emergency/` (sum of regular files, recursive). |
| `health_ok_threshold` | `0.6` | If `get_health(file_index) >=` this, `backup_check` treats the file as healthy enough to **clear** `unhealthy` and **skip** emergency copy. |
| `unhealthy_local_threshold` | `0.5` | Used in `on_launch_or_daily` (reconcile unhealthy) and `periodic_neighbor_check` (compare local score vs neighbor hints). |

**Health score range:** Conceptually `[0.0, 1.0]` for local scoring. Values outside inputs are clamped where noted below.

---

## 4. SQLite schema (semantic contract)

### `settings`

- Arbitrary string key/value.
- **Reserved keys:** `ed25519_private_key_hex`, `penalty_max`, `emergency_max_bytes`, `health_ok_threshold`, `unhealthy_local_threshold`, `last_daily_sync`, `signup_profile` (JSON).

### `nodes`

- One row per known BDFL node id string (opaque).
- `subscribed_at` updated on every `upsert_node`.

### `neighbors`

- `identity`: primary key; neighbor’s public key hex.
- `penalty`: integer, incremented on false alarm path.
- `blacklisted`: `0` = active, `1` = excluded from `list_neighbors`.
- `add_neighbor`: `INSERT OR IGNORE` — **does not** reset penalty if row exists.

### `trusted`

- Identities allowed to pass signed-health gate in `get_health`.

### `unhealthy`

- At most one row per `file_index` (primary key).
- `reported_by`: optional string (e.g. neighbor identity or `None`).

### `hosted`

- One row per `file_index` you track as hosted.
- `storage_relpath`: **filename only** (relative to `data_dir/db/`).
- `keep_forever`: affects **eligibility** for unhosting during emergency room pressure.
- `priority`: used in `_local_health_score` as `min(1.0, effective_priority / 10.0)` where `effective_priority` defaults to stored `priority`, overridable via `metadata["priority"]` in `get_health` / `_local_health_score`.
- `content_sha256`: if non-null, file on disk must match or local health is `0.0`.

### `emergency`

- At most **one row per `file_index`** (primary key). A new successful `backup_check` **overwrites** the previous emergency row for that index (DB + new file name on disk).
- `created_at` used for **eviction order** (`emergency_oldest_first`).

### `catalog_meta`

- Composite primary key `(node_id, file_index)`.
- `priority` applied to **hosted** rows during `on_launch_or_daily` for the **same** `node` argument.

### Metadata update (`update_internal_data`) — planned (not implemented)

**Current code:** `VertexClient.update_internal_data` is a stub.

**When the node/metadata pipeline exists**, this method (and **not** `get_health`) should own:

1. **Refreshing** hosted metadata from the node / trusted index servers (priorities, magnets, and any other fields you store on `hosted` rows).
2. **Local payload repair:** For each `hosted` row, if the blob path under `permanent/` or `non_permanent/` is **missing** (or fails your integrity policy, if you add one later), **re-fetch** the bytes via I2P / node (e.g. magnet, `grab_file`, or a node chunk API), write the file back to the tier directory, and update the store as needed.

**Rationale:** Missing or corrupt local payloads must **not** be folded into `get_health`, so health you might broadcast to others is not conflated with “my copy disappeared—repairing.”

**Implementation:** Deferred until metadata sync is wired; no re-fetch logic in this repo yet.

---

## 5. Local health score (`_local_health_score`)

**Input:** `file_index`, optional `metadata`.

**Rules (in order):**

1. If no `hosted` row for `file_index` → return **`1.0`** (neutral: “not our problem locally”).
2. Let `path = data_dir/db/storage_relpath`. If not a regular file → **`0.0`**.
3. If `content_sha256` is set and file SHA-256 ≠ stored → **`0.0`**.
4. Let `pr = float(metadata["priority"])` if key present, else `rec.priority`. If `pr <= 0`, force `pr = 1.0`.
5. Return **`min(1.0, pr / 10.0)`**.

**Note:** Default `priority=10.0` at `register_host` ⇒ local score `1.0` when file is present and hash matches.

---

## 6. `get_health` (full algorithm)

Order of operations:

1. **If `for_broadcast`:** Build health payload (§2.4), sign, set `last_health_broadcast`. (Does not early-return.)
2. **If signed peer triplet present** (`health_payload`, `health_signature`, `requesting_seeder` all non-`None`):
   - If `requesting_seeder` not in `trusted` → **`0.0`**.
   - If `bytes.fromhex(requesting_seeder)` invalid length/content for Ed25519 public key → **`0.0`**.
   - If signature verification fails → **`0.0`**.
   - Else → return **`_local_health_score(file_index, metadata)`** (payload bytes are **not** parsed for a score).
3. **Else** → return **`_local_health_score(file_index, metadata)`**.

---

## 7. `VertexClient` methods (strict)

### `__init__(data_dir, *, i2p=None)`

- Resolves `data_dir`, creates dirs, opens `VertexStore`, loads or creates Ed25519 key (unless `i2p` injected), runs `_defaults()`.

### `close()`

- Closes SQLite connection.

### Trust / neighbors (thin wrappers)

- `add_trusted` / `remove_trusted` / `is_trusted` → `trusted` table.
- `add_neighbor` / `remove_neighbor` → `neighbors` table.
- `neighbors` property → `list_neighbors()` snapshot (non-blacklisted only).
- `list_trusted_identities` / `list_unhealthy_indices` → read-through.

### `pull_neighbors_from_grab(index, magnet)`

- Calls `I2P.grab_file` (stub ⇒ always `[]`).
- For each returned string: `add_neighbor`.
- Returns the list from `grab_file`.

### `ingest_catalog_priorities(node, priorities)`

- For each `(file_index, pr)`: `upsert_catalog_meta(node, file_index, pr)`.

### `register_host(local_path, file_index, *, keep_forever, magnet, priority)`

- **Requires:** `local_path` is an existing regular file.
- **Does:** Read full file, SHA-256 digest, copy to `db/{sanitized_index}__{digest[:16]}`, upsert `hosted`, call `I2P.host_file(abs_dest_path, file_index)`.
- **Idempotency:** Same `file_index` twice **replaces** hosted row and updates paths (old blob may remain orphaned on disk if you change index only — not cleaned).

### `unhost(file_index)`

- If no row: no-op.
- Else: `stop_host`, delete blob if present, `delete_hosted`.
- **Does not** remove `emergency` rows or emergency files for that index (may leave orphan emergency files until trimmed).

### `signup(node, *, keep_forever_fraction, host_fraction)`

- `upsert_node(node)`.
- Stores JSON `signup_profile` with the two fractions.
- **Does not** select subsets, download data, or call `grab_file`.

### `on_launch_or_daily(node)`

- `upsert_node(node)`; sets `last_daily_sync` ISO timestamp (UTC).
- For **each** `hosted` row:
  - If `get_catalog_priority(node, file_index)` is not `None`, **overwrite** `hosted.priority` with catalog value (other hosted fields preserved from current row).
  - `get_health(file_index, metadata={"priority": rec.priority})`.
  - If `file_index` is in `unhealthy` and score **`>`** `unhealthy_local_threshold` → `remove_unhealthy`.
  - If file exists and SHA mismatch and not already unhealthy → `add_unhealthy(..., None)`.
- **Does not** fetch catalog from network; only applies what is already in `catalog_meta`.

### `periodic_neighbor_check(node)`

- Iterates `list_neighbors()`; skips if `is_neighbor_blacklisted` (redundant with list filter unless DB changes mid-loop).
- For each tuple from `check_neighbor` (stub ⇒ no tuples):
  - **Branch A** (`payload` and `sig` truthy):  
    - `get_health` with signed args → if `<= 0`, skip.  
    - Else `get_health` **unsigned** with `metadata={"risk": risk}`.  
    - If local **`>`** `unhealthy_local_threshold` → `increment_penalty`; if penalty **`>=` penalty_max** → `blacklist_neighbor`.  
    - Else → `add_unhealthy`, `backup_check(..., node=node)`.
  - **Branch B** (no payload/sig):  
    - Local `get_health` with `metadata={"risk": risk}`.  
    - If local **`<=`** `unhealthy_local_threshold` → `add_unhealthy`, `backup_check(..., node=node)`.
- **Does not** add `node` as neighbor unless `backup_check` runs with `node` and successfully writes emergency copy.

### `backup_check(file_index, *, swappiness, node=None)`

- If `get_health(file_index)` **`>=` health_ok_threshold** → `remove_unhealthy` and **return** (no disk copy).
- Requires `hosted` row and existing source file; else return.
- If `file_size > emergency_max_bytes` → **return** (cannot ever fit quota).
- `_ensure_emergency_room` (see §8).
- If still `emergency_used + need > emergency_max_bytes` → **return**.
- Copy to `emergency/{sanitized_index}_{random_hex}`; `add_emergency` (replaces prior row for same `file_index`).
- If `node` is not `None` → `add_neighbor(node)`.

### `_ensure_emergency_room(need_bytes, swappiness)`

- While `emergency_used + need_bytes > emergency_max_bytes`:
  - If any emergency rows: delete **oldest** (`ORDER BY created_at ASC`), remove file, `delete_emergency`.
  - Else: build **candidates** = hosted with `keep_forever=0` and local `get_health >= health_ok_threshold`. If empty, **break**.
  - Pick one candidate (shuffle + sort by `get_health * (1 - 0.5*swappiness)`), remove any emergency files for that `file_index`, **`unhost`** that index.

---

## 8. `VertexStore` (persistence rules)

- **Transactions:** `set_setting`, neighbor/trusted/unhealthy/hosted/emergency/catalog mutations use `BEGIN`/`COMMIT`/`ROLLBACK`.
- **`increment_penalty`:** If identity missing from `neighbors`, `UPDATE` affects 0 rows; `SELECT` returns `NULL` → method returns **`0`**. Callers should ensure the neighbor exists if penalties matter.
- **`upsert_hosted`:** On conflict, **`added_at` is refreshed** to now (implementation detail).

---

## 9. `I2P` class (current stub behavior)

| Method | Network | Persistent state |
|--------|---------|------------------|
| `grab_file` | No | — |
| `host_file` | No | In-memory signature map |
| `stop_host` | No | Removes in-memory map entry |
| `check_neighbor` | No | Returns `[]` |

---

## 10. Invariants and sharp edges (checklist for reviewers)

1. **Unhosted `file_index`:** Local health is always `1.0` — intentional neutral, not “network healthy.”
2. **Signed health:** Payload is authenticated but **not** used to compute score; only local disk/catalog state matters after trust+sig OK.
3. **Emergency table:** One row per `file_index`; new backup replaces DB pointer; old emergency file may remain on disk if not deleted (current code uses new random name).
4. **`signup_profile` fractions:** Stored but **not read** by any client logic yet — you must use them in your own orchestration or future code.
5. **Blacklist:** Blacklisted neighbors are omitted from `list_neighbors`; penalties no longer matter for them until you reset row manually.
6. **Threading:** Single SQLite connection per `VertexStore`; not thread-safe across threads without external locking.
7. **No replay window:** Health signatures are not checked for freshness (timestamp/nonce) in this codebase.

---

## 11. File reference map

| Module | Role |
|--------|------|
| `vertex/client.py` | Orchestration, thresholds, health math, emergency logic |
| `vertex/store.py` | SQLite schema and CRUD |
| `vertex/i2p.py` | Identity + stub I2P + seed signing |
| `vertex/__init__.py` | Public exports |
