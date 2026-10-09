"""Bounded provider-cost evidence shared by generators and production receipts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math


_MAX_COST_KEYS = 12
_MAX_COST_EVIDENCE_DEPTH = 3
_COST_EVIDENCE_CONTAINERS = ("usage", "billing")
_COST_EVIDENCE_KEYS = (
    "cost",
    "total_cost",
    "totalCost",
    "cost_usd",
    "costUsd",
    "credits",
    "credit",
)


def _text(value: object, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _safe_number(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(number) if number.is_integer() else number


def _safe_cost(value: object) -> dict[str, int | float]:
    if isinstance(value, Mapping):
        result: dict[str, int | float] = {}
        for raw_key, raw_value in value.items():
            key = _text(raw_key, 40)
            number = _safe_number(raw_value)
            if key and number is not None and key not in result:
                result[key] = number
            if len(result) >= _MAX_COST_KEYS:
                break
        return result
    number = _safe_number(value)
    return {"units": number} if number is not None else {}


@dataclass(frozen=True, slots=True)
class ProviderCostEvidence:
    """A normalized, bounded cost fact and the path that produced it."""

    actual_cost: dict[str, int | float]
    source: str

    def as_event_fields(self) -> dict[str, object]:
        if not self.actual_cost:
            return {}
        fields: dict[str, object] = {"actual_cost": self.actual_cost}
        if self.source:
            fields["cost_source"] = self.source
        return fields


def extract_provider_cost_evidence(
    value: object,
    *,
    prefix: str = "result",
    depth: int = 0,
) -> ProviderCostEvidence:
    """Read only explicit cost facts; never infer money from usage counts."""

    if depth > _MAX_COST_EVIDENCE_DEPTH or not isinstance(value, Mapping):
        return ProviderCostEvidence({}, "")
    for key in ("actual_cost", "actualCost"):
        if key not in value:
            continue
        cost = _safe_cost(value.get(key))
        if cost:
            return ProviderCostEvidence(cost, f"{prefix}.{key}")
    for container in _COST_EVIDENCE_CONTAINERS:
        nested = value.get(container)
        if not isinstance(nested, Mapping):
            continue
        evidence = extract_provider_cost_evidence(
            nested,
            prefix=f"{prefix}.{container}",
            depth=depth + 1,
        )
        if evidence.actual_cost:
            return evidence
    for key in _COST_EVIDENCE_KEYS:
        if key not in value:
            continue
        raw_cost = value.get(key)
        if isinstance(raw_cost, Mapping):
            cost = _safe_cost(raw_cost)
        else:
            number = _safe_number(raw_cost)
            cost = {key: number} if number is not None else {}
        if cost:
            return ProviderCostEvidence(cost, f"{prefix}.{key}")
    return ProviderCostEvidence({}, "")


def provider_cost_event_fields(event: Mapping[str, object]) -> dict[str, object]:
    """Return safe fields from a provider-cost event or an empty mapping."""

    if not isinstance(event, Mapping):
        return {}
    cost = _safe_cost(event.get("actual_cost"))
    if not cost:
        return {}
    fields: dict[str, object] = {"actual_cost": cost}
    source = _text(event.get("cost_source"), 240)
    if source:
        fields["cost_source"] = source
    return fields


__all__ = [
    "ProviderCostEvidence",
    "extract_provider_cost_evidence",
    "provider_cost_event_fields",
]
