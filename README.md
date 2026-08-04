# Library of Mestionora

**3rd place — University of Cincinnati Hackathon (March 28–29, 2026)**

A peer-to-peer archival network designed to back up at-risk content from the Library of Congress, Project Gutenberg, and other important public archives — distributing storage across trusted peers so no single institution can lose, censor, or corrupt the record.

---

## What it does

The internet's most important archives are controlled by single institutions. If those institutions are pressured, defunded, or simply fail, decades of human knowledge disappear. Mestionora builds a resilient, self-healing layer on top of existing torrent infrastructure to protect that content.

Each node in the network:
- Monitors the health of its archived files using a seeder-weighted priority score
- Detects when content becomes at-risk (low seeders, approaching loss)
- Broadcasts signed at-risk alerts to its peer network
- Verifies those alerts cryptographically before acting on them
- Automatically re-registers at-risk content for seeding

Peers that lie about file health are detected and permanently banned.

---

## Architecture

### Trust system

Peers start in a **pending** state. Each health-check round increments their trust score. Once a peer crosses the `TrustQuickness` threshold, it is promoted to **trusted**. Trusted peers that broadcast false at-risk claims — claiming a locally healthy file is at risk — are penalized. After `Penalty_Limit` violations (default: 10), the peer is permanently banned.

This is a distrust-first, Byzantine-signal-detecting design: the network defaults to skepticism and removes bad actors progressively.

### Identity and signed broadcasts

Each node generates an **Ed25519 identity key** at startup. Nodes serve:
- `/broadcast` — signed JSON payload listing at-risk torrents
- `/publickey` — the node's public Ed25519 key

Peers fetch the public key and verify the signature on every broadcast before acting on its contents. A corrupted or forged payload is rejected outright.

### Storage engine

Three-tier self-healing storage:

| Tier | Contents |
|---|---|
| Healthy | Normal archived content |
| Protected | At-risk content moved here for priority seeding |
| Overflow | Spillover when Protected tier is full |

When the Protected tier fills: only the lowest-priority Healthy files are evicted, scored by `health_equation(priority, seeders)`. The score lies in `[0.0, 1.0]`; files below `Unhealth_Index` (default: 0.5) are considered at-risk.

### qBittorrent integration

At-risk files are re-registered with qBittorrent for active seeding. The client communicates with qBittorrent's WebUI API and uses Bitmagnet's Torznab feed to discover and track content health.

---

## Test suite

~1,926 lines of pytest across 7 test files — roughly 1:1 test-to-code ratio.

| File | What it covers |
|---|---|
| `test_i2p.py` | Trust FSM transitions, Ed25519 sign/verify, adversarial signature-corruption |
| `test_i2p_integration.py` | Live `ThreadingHTTPServer` broadcast endpoint tests |
| `test_vertex_client_and_database.py` | Storage engine, health scoring, config persistence |
| `test_node_parser.py` | Bootstrap topology parsing |
| `test_cli_unit.py` / `test_cli_integration.py` | CLI interface |
| `test_smoke.py` | Import and basic sanity checks |

The adversarial test in `test_i2p.py` flips signature bytes and confirms verification rejects the corrupted payload — proving tamper resistance is real, not assumed.

---

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

With Nix:

```bash
nix develop   # or nix-shell
pip install -e ".[dev]"
```

Run tests:

```bash
pytest tests/
```

---

## Status

Built at a 24-hour hackathon. Core trust FSM, Ed25519 identity, signed broadcast protocol, storage engine, and test suite are complete. The bootstrap topology parser (`NodeParser`) is a design stub — the data model is specified but network bootstrapping is not yet wired. Transport is plain HTTP over TCP + qBittorrent (the `I2P` class name is aspirational; actual I2P routing is a future milestone).
