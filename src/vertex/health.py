"""Pure health score from priority and seeder count (no I/O)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["health_equation"]


def _weight_for_level(weights: Mapping[Any, int], level: int) -> int:
    if level in weights:
        return int(weights[level])
    sk = str(level)
    if sk in weights:
        return int(weights[sk])
    return 1


def health_equation(
    priority: float,
    seeders: int,
    priority_weights: Mapping[Any, int] | None = None,
) -> float:
    """
    Simple combined score in ``[0.0, 1.0]``.

    ``seeders`` is normalized to ``[0, 1]`` against a nominal max of 10.

    If ``priority_weights`` is omitted or empty, ``priority`` is treated as a 0–10 value (legacy).

    Otherwise ``priority`` is interpreted as a discrete **level** (integer part of ``priority``); the
    weight for that level is taken from ``priority_weights`` (keys may be ``int`` or ``str`` after
    JSON), defaulting to ``1`` if missing, then normalized by ``max(weights.values())`` so the
    priority factor lies in ``[0, 1]``.

    Compare the result to the configured health threshold (e.g. ``Unhealth_Index`` in
    ``VertexClient.config``): below threshold ⇒ unhealthy.
    """
    s = max(0, min(10, seeders)) / 10.0
    if not priority_weights:
        p = max(0.0, min(10.0, priority)) / 10.0
    else:
        level = int(priority)
        w = _weight_for_level(priority_weights, level)
        denom = max(int(v) for v in priority_weights.values())
        denom = max(1, denom)
        p = max(0.0, float(w)) / float(denom)
    return p * s
