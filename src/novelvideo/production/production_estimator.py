"""Preflight capacity and cost estimates for long-form production.

Cost is a planning fact.  The estimator never invents provider prices or
acceptance rates: missing inputs remain ``not_run`` while attempt counts and
risk are still exposed for the production team.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from copy import deepcopy
from typing import Any


PRODUCTION_ESTIMATE_SCHEMA = "production_estimate.v1"
PRODUCTION_ESTIMATE_AUDIT_SCHEMA = "production_estimate_audit.v1"


def _text(value: object, *, limit: int = 1000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _number(value: object) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _positive_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(parsed, 10_000_000))


def _canonical_payload(value: Mapping[str, Any]) -> str:
    return json.dumps(
        {key: value[key] for key in sorted(value) if key != "contract_revision"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _revision(value: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_payload(value).encode("utf-8")).hexdigest()[:20]
    return f"production-estimate.v1:{digest}"


def _attempts(acceptance_rate: float | None) -> int | None:
    if acceptance_rate is None or acceptance_rate <= 0:
        return None
    return max(1, math.ceil(1.0 / acceptance_rate))


def build_production_estimate(
    *,
    episode_count: object,
    shot_count: object,
    average_shot_seconds: object,
    image_acceptance_rate: object = None,
    video_acceptance_rate: object = None,
    image_unit_cost: object = None,
    video_unit_cost: object = None,
    audio_unit_cost: object = None,
    post_unit_cost_per_minute: object = None,
    currency: object = "",
    reference_rates: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculate attempt volume and a cost band from explicit inputs only."""

    episodes = _positive_int(episode_count)
    shots = _positive_int(shot_count)
    duration = _number(average_shot_seconds) or 0.0
    image_rate = _number(image_acceptance_rate)
    video_rate = _number(video_acceptance_rate)
    image_cost = _number(image_unit_cost)
    video_cost = _number(video_unit_cost)
    audio_cost = _number(audio_unit_cost)
    post_cost = _number(post_unit_cost_per_minute)
    image_attempts = _attempts(image_rate)
    video_attempts = _attempts(video_rate)
    image_volume = shots * image_attempts if image_attempts is not None else None
    video_volume = shots * video_attempts if video_attempts is not None else None
    total_seconds = shots * duration
    total_minutes = total_seconds / 60.0 if total_seconds else 0.0

    cost_items: dict[str, float | None] = {
        "image": image_volume * image_cost if image_volume is not None and image_cost is not None else None,
        "video": video_volume * video_cost if video_volume is not None and video_cost is not None else None,
        "audio": audio_cost,
        "post": post_cost * total_minutes if post_cost is not None else None,
    }
    known_costs = [value for value in cost_items.values() if value is not None]
    cost_complete = all(value is not None for value in cost_items.values())
    subtotal = sum(known_costs) if cost_complete else None
    result: dict[str, Any] = {
        "schema": PRODUCTION_ESTIMATE_SCHEMA,
        "scope": {
            "episode_count": episodes,
            "shot_count": shots,
            "average_shot_seconds": duration,
            "estimated_duration_minutes": round(total_minutes, 2),
        },
        "attempt_policy": {
            "image_acceptance_rate": image_rate,
            "video_acceptance_rate": video_rate,
            "image_attempts_per_shot": image_attempts,
            "video_attempts_per_shot": video_attempts,
            "image_generation_volume": image_volume,
            "video_generation_volume": video_volume,
            "reference_rates": _mapping(reference_rates),
        },
        "cost": {
            "currency": _text(currency, limit=20),
            "unit_costs": {
                "image": image_cost,
                "video": video_cost,
                "audio": audio_cost,
                "post_per_minute": post_cost,
            },
            "items": cost_items,
            "subtotal": subtotal,
            "cost_band": (
                {
                    "low": round(subtotal * 0.85, 2),
                    "expected": round(subtotal, 2),
                    "high": round(subtotal * 1.35, 2),
                }
                if subtotal is not None
                else None
            ),
            "status": "ready" if cost_complete else "not_run",
        },
        "risk": {
            "long_form_shot_count": shots >= 200,
            "high_rework_risk": (
                (video_rate is not None and video_rate < 0.05)
                or (image_rate is not None and image_rate < 0.05)
            ),
            "missing_inputs": [
                label
                for label, value in (
                    ("image_acceptance_rate", image_rate),
                    ("video_acceptance_rate", video_rate),
                    ("image_unit_cost", image_cost),
                    ("video_unit_cost", video_cost),
                    ("audio_unit_cost", audio_cost),
                    ("post_unit_cost_per_minute", post_cost),
                )
                if value is None
            ],
        },
        "unverified_boundary": (
            "reference_rates 是外部产线锚点，不是本仓实测；未提供本通道接受率时，"
            "生成次数只按明确输入计算。"
        ),
    }
    result["contract_revision"] = _revision(result)
    return result


def validate_production_estimate(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("production_estimate must be an object")
    estimate = deepcopy(dict(value))
    if estimate.get("schema") != PRODUCTION_ESTIMATE_SCHEMA:
        raise ValueError("production_estimate schema is unsupported")
    expected = _revision(estimate)
    if _text(estimate.get("contract_revision"), limit=100) != expected:
        raise ValueError("production_estimate contract_revision does not match its contents")
    return estimate


def audit_production_estimate(value: object) -> dict[str, Any]:
    try:
        estimate = validate_production_estimate(value)
    except ValueError as exc:
        return {
            "schema": PRODUCTION_ESTIMATE_AUDIT_SCHEMA,
            "passed": False,
            "issues": [{"code": "estimate.contract_invalid", "message": str(exc)}],
            "gate_observations": {"production_estimate_ready": False},
        }
    cost_status = _mapping(estimate.get("cost")).get("status")
    return {
        "schema": PRODUCTION_ESTIMATE_AUDIT_SCHEMA,
        "passed": cost_status == "ready",
        "issues": (
            []
            if cost_status == "ready"
            else [
                {
                    "code": "estimate.cost_inputs_missing",
                    "message": "成本单价或接受率尚未齐全，预算保持未运行",
                }
            ]
        ),
        "gate_observations": {
            "production_estimate_ready": True if cost_status == "ready" else None
        },
    }


__all__ = [
    "PRODUCTION_ESTIMATE_AUDIT_SCHEMA",
    "PRODUCTION_ESTIMATE_SCHEMA",
    "audit_production_estimate",
    "build_production_estimate",
    "validate_production_estimate",
]
