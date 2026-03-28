"""
Vertex client — health is a bool (unhealthy or not); numeric score exists only inside
:func:`vertex.health.health_equation` compared to ``config.health_threshold``.
"""

from __future__ import annotations

import json
import random
import re
import secrets
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vertex.health import health_equation
from vertex.i2p import I2P
from vertex.state import HostedRecord, UnhealthyRecord, VertexConfig, VertexState

__all__ = ["VertexClient"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_or_create_i2p(state: VertexState) -> I2P:
    hex_key = state.ed25519_private_key_hex
    if hex_key:
        sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(hex_key))
        return I2P(private_key=sk)
    i2p = I2P()
    raw = i2p._sk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    state.ed25519_private_key_hex = raw.hex()
    state.save()
    return i2p


def _safe_name(s: str) -> str:
    x = re.sub(r"[^a-zA-Z0-9._-]+", "_", s)
    return x[:200] if x else "unnamed"


class VertexClient:
    def __init__(self, data_dir: Path | str, *, i2p: I2P | None = None) -> None:
        self._data_dir = Path(data_dir).resolve()
        self._permanent_dir = self._data_dir / "permanent"
        self._emergency_dir = self._data_dir / "emergency"
        self._non_permanent_dir = self._data_dir / "non_permanent"
        for d in (self._permanent_dir, self._emergency_dir, self._non_permanent_dir):
            d.mkdir(parents=True, exist_ok=True)

        self._state = VertexState(self._data_dir)
        self._i2p = i2p if i2p is not None else _load_or_create_i2p(self._state)
        self.identity = self._i2p.identity

    def _node_id(self) -> str:
        return self._state.subscribed_node or ""

    @property
    def state(self) -> VertexState:
        return self._state

    @property
    def config(self) -> VertexConfig:
        return self._state.config

    def save_config(self, config: VertexConfig) -> None:
        self._state.config = config
        self._state.save()

    @property
    def neighbor_list(self) -> list[str]:
        rows = sorted(
            (n for n in self._state.neighbors if not n.get("blacklisted")),
            key=lambda x: str(x.get("created_at", "")),
        )
        return [str(n["identity"]) for n in rows]

    def permanent_used_bytes(self) -> int:
        return self._dir_bytes(self._permanent_dir)

    def emergency_used_bytes(self) -> int:
        return self._dir_bytes(self._emergency_dir)

    def non_permanent_used_bytes(self) -> int:
        return self._dir_bytes(self._non_permanent_dir)

    def permanent_quota_bytes(self) -> int:
        c = self.config
        return int(c.total_storage_bytes * c.permanent_fraction)

    def emergency_quota_bytes(self) -> int:
        c = self.config
        return int(c.total_storage_bytes * c.emergency_fraction)

    def non_permanent_quota_bytes(self) -> int:
        c = self.config
        return int(c.total_storage_bytes * c.non_permanent_fraction())

    def permanent_used_fraction_of_quota(self) -> float:
        q = self.permanent_quota_bytes()
        return self.permanent_used_bytes() / q if q else 0.0

    def emergency_used_fraction_of_quota(self) -> float:
        q = self.emergency_quota_bytes()
        return self.emergency_used_bytes() / q if q else 0.0

    def non_permanent_used_fraction_of_quota(self) -> float:
        q = self.non_permanent_quota_bytes()
        return self.non_permanent_used_bytes() / q if q else 0.0

    def _dir_bytes(self, root: Path) -> int:
        n = 0
        for p in root.rglob("*"):
            if p.is_file():
                n += p.stat().st_size
        return n

    def _hosted_path(self, rec: HostedRecord) -> Path:
        base = self._permanent_dir if rec.tier == "permanent" else self._non_permanent_dir
        return base / rec.storage_relpath

    def _add_neighbor(self, identity: str) -> None:
        if any(str(n["identity"]) == identity for n in self._state.neighbors):
            return
        self._state.neighbors.append(
            {
                "identity": identity,
                "penalty": 0,
                "blacklisted": False,
                "created_at": _utc_now(),
            }
        )
        self._state.save()

    def _increment_penalty(self, identity: str) -> int:
        for n in self._state.neighbors:
            if str(n["identity"]) == identity:
                n["penalty"] = int(n.get("penalty", 0)) + 1
                self._state.save()
                return int(n["penalty"])
        return 0

    def _blacklist_neighbor(self, identity: str) -> None:
        for n in self._state.neighbors:
            if str(n["identity"]) == identity:
                n["blacklisted"] = True
        self._state.save()

    def _is_trusted(self, identity: str) -> bool:
        return identity in self._state.trusted

    def signup(self, node: str, *, health_threshold: float | None = None) -> None:
        """
        Subscribe to ``node``. Optional ``health_threshold`` persists the node's suggested
        cutoff vs :func:`vertex.health.health_equation` (internal policy).

        STUB: trusted mirrors / full node download (network not implemented).

        STUB: random subset to host, keep-forever fraction, initial neighbors from seeders.

        Pseudocode (subset download): ``for index in chosen: blob = fetch(node, index); write tier dir; store.upsert_hosted(...)``.

        Pseudocode (node settings): ``set_node_settings_mirror(fetch_settings(node))`` — never merge into :class:`~vertex.state.VertexConfig`; internal config only.
        """
        self._state.subscribed_node = node
        self._state.save()
        if health_threshold is not None:
            cfg = self.config
            cfg.health_threshold = float(health_threshold)
            self.save_config(cfg)

    def on_launch_or_daily(self) -> None:
        self.update_internal_data()
        self.update_at_risk_list()

    def update_internal_data(self) -> None:
        """
        STUB: refresh hosted metadata from the node and repair missing local payloads.

        Planned behavior (including re-fetch when a hosted file is missing on disk) is
        specified in ``docs/VERTEX_SPEC.md`` (Metadata update — planned); not implemented yet.
        """
        pass

    def update_at_risk_list(self) -> None:
        """
        After :meth:`update_internal_data`, re-sync the unhealthy table using
        :meth:`get_health` (same bool pipeline, no duplicate float scores exposed).
        """
        for fid in list(self._state.unhealthy.keys()):
            if not self.get_health(fid, record_unhealthy=False):
                self._state.unhealthy.pop(fid, None)
        self._state.save()

        for rec in self._state.hosted.values():
            self.get_health(
                rec.file_index,
                priority=rec.priority,
                reporter=self.identity,
                record_unhealthy=True,
            )

    def check_neighbors(self, node_source: str) -> None:
        cfg = self.config
        for neighbor in self.neighbor_list:
            if not self._is_trusted(neighbor):
                continue
            for file_key, _risk, _payload, _sig in self._i2p.check_neighbor(neighbor):
                if self.get_health(
                    file_key,
                    requesting_seeder=neighbor,
                    reporter=neighbor,
                    record_unhealthy=True,
                ):
                    self.backup_check(file_key, node_source=node_source)
                else:
                    if self._increment_penalty(neighbor) >= cfg.penalty_max:
                        self._blacklist_neighbor(neighbor)

    def get_health(
        self,
        file_index: str,
        *,
        priority: float | None = None,
        requesting_seeder: str | None = None,
        reporter: str | None = None,
        record_unhealthy: bool = True,
    ) -> bool:
        """
        Return ``True`` if this file is **unhealthy** (below threshold), else ``False``.

        Uses :func:`vertex.health.health_equation` with ``priority`` and I2P seeder count
        vs ``config.health_threshold``. When ``record_unhealthy`` is True, updates the
        persisted unhealthy row with ``node_id`` (subscribed node) and ``reporter``
        (defaults to :attr:`identity` for local checks).
        """
        thr = self.config.health_threshold
        rec = self._state.hosted.get(file_index)
        pr = float(priority if priority is not None else (rec.priority if rec else 1.0))
        seeders = self._i2p.count_seeders(file_index, requesting_seeder)

        score = health_equation(pr, seeders)
        is_unhealthy = score < thr

        rep = reporter if reporter is not None else self.identity
        nid = self._node_id()
        if record_unhealthy:
            if is_unhealthy:
                self._state.unhealthy[file_index] = UnhealthyRecord(
                    file_index=file_index,
                    node_id=nid,
                    reporter=rep,
                    since=_utc_now(),
                )
            else:
                self._state.unhealthy.pop(file_index, None)
            self._state.save()
        return is_unhealthy

    def backup_check(
        self,
        file_index: str,
        *,
        swappiness: float = 0.5,
        node_source: str | None = None,
    ) -> bool:
        cfg = self.config
        time.sleep(random.uniform(0.0, cfg.backup_check_small_delay_max_seconds))
        if not self.get_health(file_index, record_unhealthy=False):
            self._state.unhealthy.pop(file_index, None)
            self._state.save()
            return False

        rec = self._state.hosted.get(file_index)
        if rec is None:
            return False
        src = self._hosted_path(rec)
        if not src.is_file():
            return False

        need = src.stat().st_size
        if need > self.emergency_quota_bytes():
            return False

        self._ensure_emergency_room(need, swappiness)
        if self.emergency_used_bytes() + need > self.emergency_quota_bytes():
            return False

        name = f"{_safe_name(file_index)}_{secrets.token_hex(8)}"
        dest = self._emergency_dir / name
        shutil.copy2(src, dest)
        self._state.emergency.append(
            {
                "file_index": file_index,
                "storage_relpath": dest.name,
                "created_at": _utc_now(),
            }
        )
        self._state.save()

        time.sleep(
            random.uniform(
                0.0,
                cfg.backup_check_large_delay_max_seconds * max(0.1, 1.0 - swappiness * 0.9),
            )
        )
        self._evict_healthy_enough_for_emergency(swappiness)
        if node_source:
            self._add_neighbor(node_source)
        return True

    def _emergency_ordered(self) -> list[tuple[str, str]]:
        rows = sorted(self._state.emergency, key=lambda x: str(x.get("created_at", "")))
        return [(str(r["file_index"]), str(r["storage_relpath"])) for r in rows]

    def _ensure_emergency_room(self, need_bytes: int, swappiness: float) -> None:
        while self.emergency_used_bytes() + need_bytes > self.emergency_quota_bytes():
            rows = self._emergency_ordered()
            if rows:
                fi, rel = rows[0]
                p = self._emergency_dir / rel
                self._state.emergency = [
                    e
                    for e in self._state.emergency
                    if str(e["file_index"]) != fi
                ]
                self._state.save()
                if p.is_file():
                    p.unlink()
                continue
            cand = [
                r
                for r in self._state.hosted.values()
                if r.tier == "non_permanent" and not self.get_health(r.file_index, record_unhealthy=False)
            ]
            if not cand:
                break
            random.shuffle(cand)
            r = cand[0]
            for e in list(self._state.emergency):
                if str(e["file_index"]) == r.file_index:
                    ep = self._emergency_dir / str(e["storage_relpath"])
                    self._state.emergency = [
                        x for x in self._state.emergency if str(x["file_index"]) != r.file_index
                    ]
                    self._state.save()
                    if ep.is_file():
                        ep.unlink()
            self._delete_hosted_file(r)

    def _evict_healthy_enough_for_emergency(self, swappiness: float) -> None:
        if self.emergency_used_bytes() <= self.emergency_quota_bytes():
            return
        self._ensure_emergency_room(0, swappiness)

    def _delete_hosted_file(self, rec: HostedRecord) -> None:
        p = self._hosted_path(rec)
        self._i2p.stop_host(str(p), rec.file_index)
        if p.is_file():
            p.unlink()
        self._state.hosted.pop(rec.file_index, None)
        self._state.save()

    def broadcast_at_risk(self) -> None:
        link = self.config.current_node_link or self._node_id() or ""
        for row in self._state.unhealthy.values():
            msg = json.dumps(
                {"file_index": row.file_index, "node_id": row.node_id, "node_link": link}
            ).encode("utf-8")
            self._i2p.broadcast(msg)

    def close(self) -> None:
        self._state.close()
