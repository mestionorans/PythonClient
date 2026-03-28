"""Pure health score from priority and seeder count (no I/O)."""

from __future__ import annotations

__all__ = ["health_equation"]


def health_equation(priority: float, seeders: int) -> float:
    """
    Simple combined score in ``[0.0, 1.0]``.

    Both inputs are normalized to ``[0, 1]`` against a nominal max of 10, then multiplied.
    Compare the result to ``VertexConfig.health_threshold``: below threshold ⇒ unhealthy.
    """
    p = max(0.0, min(10.0, priority)) / 10.0
    s = max(0, min(10, seeders)) / 10.0
    return p * s
