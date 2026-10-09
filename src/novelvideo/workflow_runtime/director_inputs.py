"""Resolve persisted director intent carriers into one effective contract."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def resolve_director_intent_contract(
    inputs: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge the legacy nested contract and the canonical top-level carrier.

    The top-level carrier is newer and wins when it states a field. A partial
    top-level object must not hide a complete legacy contract nested in
    ``director_plan``, which is how explicit false values were previously lost.
    """

    container = inputs if isinstance(inputs, Mapping) else {}
    resolved: dict[str, Any] = {}
    director_plan = container.get("director_plan")
    if isinstance(director_plan, Mapping):
        nested = director_plan.get("director_intent_contract")
        if isinstance(nested, Mapping):
            resolved.update(_mapping(nested))
    top_level = container.get("director_intent_contract")
    if isinstance(top_level, Mapping):
        resolved.update(_mapping(top_level))
    return resolved


__all__ = ["resolve_director_intent_contract"]
