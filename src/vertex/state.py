"""Single-file persistence: config plus all durable client state (JSON snapshot)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "HostedRecord",
    "UnhealthyRecord",
    "VertexConfig",
    "VertexState",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class VertexConfig:
    """
    Tunables referenced by the Vertex design doc. Values are **internal**;
    mirrored node download is stored separately and must not override these for policy.
    """

    total_storage_bytes: int = 10 * 1024 * 1024 * 1024
    permanent_fraction: float = 0.10
    emergency_fraction: float = 0.10
    host_sign_interval_minutes: int = 60
    health_threshold: float = 0.5
    penalty_max: int = 5
    backup_check_small_delay_max_seconds: float = 5.0
    backup_check_large_delay_max_seconds: float = 300.0
    neighbor_check_interval_minutes: int = 60
    current_node_link: str = ""

    @classmethod
    def default(cls) -> VertexConfig:
        return cls()

    def non_permanent_fraction(self) -> float:
        return max(0.0, 1.0 - self.permanent_fraction - self.emergency_fraction)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> VertexConfig:
        data: dict[str, Any] = json.loads(raw)
        valid = {f.name for f in fields(cls)}
        kwargs = {k: data[k] for k in data if k in valid}
        return cls(**kwargs)


@dataclass(frozen=True, slots=True)
class HostedRecord:
    file_index: str
    storage_relpath: str
    tier: str
    magnet: str | None
    priority: float


@dataclass(frozen=True, slots=True)
class UnhealthyRecord:
    file_index: str
    node_id: str
    reporter: str
    since: str


def _hosted_from_dict(d: dict[str, Any]) -> HostedRecord:
    return HostedRecord(
        file_index=str(d["file_index"]),
        storage_relpath=str(d["storage_relpath"]),
        tier=str(d["tier"]),
        magnet=d.get("magnet"),
        priority=float(d["priority"]),
    )


def _unhealthy_from_dict(d: dict[str, Any]) -> UnhealthyRecord:
    return UnhealthyRecord(
        file_index=str(d["file_index"]),
        node_id=str(d["node_id"]),
        reporter=str(d["reporter"]),
        since=str(d["since"]),
    )


class VertexState:
    """Load once, mutate in memory, call :meth:`save` after changes (and on close)."""

    _VERSION = 1
    _FILENAME = "vertex_state.json"

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self._path = self.data_dir / self._FILENAME
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.config = VertexConfig.default()
        self.subscribed_node = ""
        self.ed25519_private_key_hex: str | None = None
        self.node_settings_mirror_json: str | None = None
        self.neighbors: list[dict[str, Any]] = []
        self.trusted: list[str] = []
        self.unhealthy: dict[str, UnhealthyRecord] = {}
        self.hosted: dict[str, HostedRecord] = {}
        self.emergency: list[dict[str, str]] = []

        self.load()

    def load(self) -> None:
        if not self._path.is_file():
            self.save()
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if int(raw.get("version", 0)) != self._VERSION:
            raise ValueError(f"Unsupported state version in {self._path}")
        self.config = VertexConfig.from_json(json.dumps(raw["config"]))
        self.subscribed_node = str(raw.get("subscribed_node", ""))
        self.ed25519_private_key_hex = raw.get("ed25519_private_key_hex")
        self.node_settings_mirror_json = raw.get("node_settings_mirror_json")
        self.neighbors = list(raw.get("neighbors", []))
        self.trusted = list(raw.get("trusted", []))
        self.unhealthy = {
            k: _unhealthy_from_dict(v) for k, v in raw.get("unhealthy", {}).items()
        }
        self.hosted = {
            k: _hosted_from_dict(v) for k, v in raw.get("hosted", {}).items()
        }
        self.emergency = list(raw.get("emergency", []))

    def save(self) -> None:
        payload = {
            "version": self._VERSION,
            "config": json.loads(self.config.to_json()),
            "subscribed_node": self.subscribed_node,
            "ed25519_private_key_hex": self.ed25519_private_key_hex,
            "node_settings_mirror_json": self.node_settings_mirror_json,
            "neighbors": self.neighbors,
            "trusted": self.trusted,
            "unhealthy": {k: asdict(v) for k, v in self.unhealthy.items()},
            "hosted": {k: asdict(v) for k, v in self.hosted.items()},
            "emergency": self.emergency,
        }
        text = json.dumps(payload, indent=2, sort_keys=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self._path)

    def close(self) -> None:
        self.save()
