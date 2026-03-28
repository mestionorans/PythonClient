# Node API — BDFL node communications (specification + CRUN)

This document defines how the Vertex client **should** talk to a **node** (BDFL database / authority) once the wire format exists. The Python module `vertex.node_client` is a **CRUN**: same names and call order as the final implementation, but **no network I/O** and empty or placeholder returns.

**Related:** [VERTEX_SPEC.md](VERTEX_SPEC.md) describes on-disk Vertex behavior. When the real node protocol exists, update this file with URLs, message schemas, and error codes.

---

## 1. Goals

- Fetch **just enough** node data for the Vertex to operate (priorities, mirror lists, policy fields you choose later).
- **Minimize bandwidth and central chokepoints:** prefer **small, targeted** reads via **trusted index servers**.
- **Never download the full node database** while the client has **at least one trusted index server** configured for that node (even if an index returns partial or empty data — retry and fallback **among indexes** is specified later, not by pulling the full node).
- **Full node download** is allowed **only** when there are **no trusted index servers** for that node (cold start, air-gapped import, or explicit operator choice recorded in policy).

---

## 2. Terms

| Term | Meaning |
|------|--------|
| **Node** | A logical BDFL dataset (catalog, priorities, mirrors, settings). Identified by `node_id` (opaque string; format TBD). |
| **Full node** | The complete node payload (e.g. full DB export, large manifest). Expensive; last resort per section 4. |
| **Trusted index server** | A peer or service the operator **configures** as authoritative for **slices** of node data (e.g. priority by `file_index`, mirror list). Identity and transport TBD (I2P destination, public key, etc.). |
| **Index query** | A **bounded** request: specific keys, indices, or hashes — not “give me everything”. |

---

## 3. Control flow (trusted indexes first)

1. If `get_trusted_index_server_ids(node_id)` is **non-empty** → use **only** index APIs (`fetch_*_from_indexes`, etc.). **Do not** call `download_full_node` or equivalent.
2. If that list is **empty** → you **may** use `download_full_node` (or a side-loaded file) to hydrate catalog and priorities.
3. When indexes exist but return **errors or incomplete** data, behavior is **out of scope** for this CRUN; a future spec will define retries, multiple indexes, and staleness — still **without** full node download while trusted indexes remain configured.

---

## 4. Python CRUN surface (`vertex.node_client.NodeClient`)

All methods are **synchronous** stubs today. Names and responsibilities should stay stable so `VertexClient` (or a thin orchestrator) can call them without refactors.

### 4.1 Configuration (local, persistent later)

| Method | Purpose |
|--------|--------|
| `get_trusted_index_server_ids(node_id: str) -> list[str]` | Return configured index server identities (or endpoints-as-strings until identity format is fixed). **Stub: `[]`**. |
| `set_trusted_index_server_ids(node_id: str, server_ids: Sequence[str]) -> None` | Persist preference for this node. **Stub: no-op** (wire to SQLite when you add a table). |

### 4.2 Index-only reads (preferred path)

| Method | Purpose |
|--------|--------|
| `fetch_priorities_from_indexes(node_id, file_indices, index_server_ids) -> dict[str, float]` | Map `file_index` to priority using **only** index servers. **Stub: `{}`**. |
| `fetch_mirrors_from_indexes(node_id, index_server_ids) -> list[str]` | Trusted mirrors / fetch endpoints for the node. **Stub: `[]`**. |
| `fetch_node_settings_slice_from_indexes(node_id, keys, index_server_ids) -> dict[str, str]` | Small settings subset. **Stub: `{}`**. |

Exact parameters for which index to ask first will be specified later (round-robin, quorum, etc.).

### 4.3 Full node (fallback when no trusted indexes)

| Method | Purpose |
|--------|--------|
| `download_full_node(node_id: str) -> bytes` | Retrieve entire node blob. **Legal only** when `get_trusted_index_server_ids` is empty (see section 3). **Stub: `b""`**. |
| `parse_priorities_from_full_node_blob(node_id, blob, file_indices) -> dict[str, float]` | Extract priorities for requested indices from a full blob. **Stub: `{}`**. |

### 4.4 Orchestration helper

| Method | Purpose |
|--------|--------|
| `resolve_priorities(node_id, file_indices) -> dict[str, float]` | If trusted indexes exist → `fetch_priorities_from_indexes` only. Else → `download_full_node` plus `parse_priorities_from_full_node_blob`. **Stub: always `{}`**. |

Use `resolve_priorities` from daily sync when you connect the node layer to `VertexClient.ingest_catalog_priorities`.

---

## 5. Integration sketch (not implemented in CRUN)

```text
on_launch_or_daily(node_id):
    pri = node_client.resolve_priorities(node_id, hosted_file_indices)
    vertex.ingest_catalog_priorities(node_id, pri)
    ... existing Vertex health / checksum logic ...
```

I2P (torrent, tunnels) stays separate; the node API is **about metadata and catalog**, not payload bytes.

---

## 6. Open items (fill in when the protocol exists)

- Canonical `node_id` format and pinning (hash, key, out-of-band).
- Index server wire format (REST-over-I2P, custom RPC, signed responses).
- Authentication of index responses (signatures, TOFU, node key).
- Partial-merge rules when multiple indexes disagree.
- Rate limits and caching on the Vertex side.

---

## 7. Changelog

| Date | Change |
|------|--------|
| 2026-03-28 | Initial NODE_API plus `NodeClient` CRUN with trusted-index-first rule. |
