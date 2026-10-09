"""Composition helper for the workflow-to-production control boundary."""

from __future__ import annotations

from pathlib import Path

from novelvideo.ports.production_control import ProductionControlPort


def make_production_control_port(state_dir: str | Path) -> ProductionControlPort:
    """Build the existing durable ProductionControl adapter lazily."""
    from novelvideo.production.control_store import ProductionControlStore

    return ProductionControlStore(state_dir)


__all__ = ["make_production_control_port"]
