"""Light smoke tests — imports and :func:`vertex.health.health_equation`."""

from __future__ import annotations

from vertex import I2P, health_equation


def test_imports() -> None:
    assert I2P is not None


def test_health_equation() -> None:
    assert health_equation(1.0, 3) < health_equation(1.0, 10)
