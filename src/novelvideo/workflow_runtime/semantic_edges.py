"""Canonical semantic relations shared by canvas, evidence, and impact planning."""

from __future__ import annotations

import re
from collections.abc import Mapping


SEMANTIC_EDGE_SCHEMA = "canvas_semantic_edge.v1"
SEMANTIC_EDGE_RELATIONS = frozenset(
    {
        "canvas_edge",
        "depends_on",
        "references",
        "identity_lock",
        "continuity",
        "execution_input",
        "result_of",
        "supported_by",
        "produces",
        "uses_model",
        "uses_asset",
    }
)

_RELATION_ALIASES = {
    "dependency": "depends_on",
    "depends": "depends_on",
    "reference": "references",
    "identity": "identity_lock",
    "identity_locked_by": "identity_lock",
    "input": "execution_input",
    "execution": "execution_input",
    "result": "result_of",
    "evidence": "supported_by",
    "output": "produces",
    "model": "uses_model",
    "asset": "uses_asset",
    "user": "canvas_edge",
    "system": "canvas_edge",
}


def normalize_edge_relation(
    value: object,
    *,
    default: str = "canvas_edge",
    strict: bool = False,
) -> str:
    raw = str(value or "").strip()
    token = re.sub(r"(?<!^)(?=[A-Z])", "_", raw).casefold()
    token = token.replace("-", "_").replace(" ", "_")
    normalized = _RELATION_ALIASES.get(token, token) if token else default
    if normalized in SEMANTIC_EDGE_RELATIONS:
        return normalized
    if strict:
        raise ValueError(f"unsupported semantic edge relation: {raw or '<empty>'}")
    return default


def edge_relation(edge: Mapping[str, object] | None) -> str:
    raw = edge if isinstance(edge, Mapping) else {}
    return normalize_edge_relation(
        raw.get("relation")
        or raw.get("semanticRelation")
        or raw.get("semantic_relation"),
    )


def propagation_arcs(source: str, target: str, relation: str) -> tuple[tuple[str, str], ...]:
    """Return the dependency directions used by the minimum impact closure."""

    normalized = normalize_edge_relation(relation)
    if normalized == "continuity":
        return ((source, target), (target, source))
    return ((source, target),)


__all__ = [
    "SEMANTIC_EDGE_RELATIONS",
    "SEMANTIC_EDGE_SCHEMA",
    "edge_relation",
    "normalize_edge_relation",
    "propagation_arcs",
]
