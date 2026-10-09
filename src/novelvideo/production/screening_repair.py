"""Screening feedback to surgical revision planning.

"Completed" and "audience wants to keep watching" are different facts.  This
module keeps that distinction and maps observed audience problems to the
smallest revision that can plausibly fix them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any


SCREENING_SCHEMA = "screening_feedback.v1"
REPAIR_PLAN_SCHEMA = "repair_plan.v1"
SCREENING_AUDIT_SCHEMA = "screening_audit.v1"

_CATEGORY_ACTIONS: dict[str, str] = {
    "pacing": "trim",
    "pacing_slow": "trim",
    "dialogue": "rewrite_dialogue",
    "dialogue_exposition": "rewrite_dialogue",
    "story_clarity": "rewrite_story",
    "continuity": "local_repair",
    "identity": "local_repair",
    "wardrobe": "local_repair",
    "prop_state": "local_repair",
    "lighting": "local_repair",
    "color": "local_repair",
    "sound": "local_repair",
    "performance": "regenerate_shot",
    "action": "regenerate_shot",
    "camera": "regenerate_shot",
    "composition": "regenerate_shot",
    "artifact": "local_repair",
    "technical": "local_repair",
}

_SEVERITY_WEIGHT = {
    "blocking": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}


def _text(value: object, *, limit: int = 3000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _number(value: object, *, default: float = 0.0) -> float:
    if value in (None, "") or isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed


def _positive_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(parsed, 100_000))


def _canonical_payload(value: Mapping[str, Any]) -> str:
    return json.dumps(
        {key: value[key] for key in sorted(value) if key != "contract_revision"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _revision(value: Mapping[str, Any], prefix: str) -> str:
    digest = hashlib.sha256(_canonical_payload(value).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}:{digest}"


def _issue(value: object, position: int) -> dict[str, Any]:
    raw = _mapping(value)
    category = _text(raw.get("category") or raw.get("type"), limit=80).casefold()
    severity = _text(raw.get("severity"), limit=40).casefold() or "medium"
    return {
        "issue_id": _text(raw.get("issue_id") or raw.get("id"), limit=160)
        or f"I{position:02d}",
        "category": category or "unknown",
        "severity": severity if severity in _SEVERITY_WEIGHT else "medium",
        "shot_id": _text(raw.get("shot_id") or raw.get("shotId"), limit=160),
        "timestamp_seconds": _number(
            next((raw[key] for key in ("timestamp_seconds", "timestamp", "time") if raw.get(key) is not None), None),
            default=-1.0,
        ),
        "description": _text(raw.get("description") or raw.get("message"), limit=1600),
        "audience_effect": _text(
            raw.get("audience_effect") or raw.get("effect"),
            limit=1200,
        ),
        "localized": raw.get("localized") is True,
        "attempt_count": _positive_int(raw.get("attempt_count")),
    }


def build_screening_feedback(
    *,
    screening_id: object,
    retention_curve: object = None,
    memorable_moments: object = None,
    issues: object = None,
    audience: object = "",
) -> dict[str, Any]:
    """Normalize screening notes without pretending they are objective QC."""

    normalized_id = _text(screening_id, limit=160)
    if not normalized_id:
        raise ValueError("screening_id is required")
    retention: list[dict[str, Any]] = []
    if isinstance(retention_curve, (list, tuple)):
        for raw in retention_curve[:2000]:
            item = _mapping(raw)
            retention.append(
                {
                    "timestamp_seconds": _number(
                        item.get("timestamp_seconds") or item.get("timestamp")
                    ),
                    "retained_ratio": _number(
                        item.get("retained_ratio") or item.get("retention")
                    ),
                }
            )
    moments = [
        _mapping(item)
        for item in (memorable_moments if isinstance(memorable_moments, (list, tuple)) else [])
        if isinstance(item, Mapping)
    ][:200]
    normalized_issues = [
        _issue(item, index)
        for index, item in enumerate(
            issues if isinstance(issues, (list, tuple)) else [],
            start=1,
        )
    ]
    result: dict[str, Any] = {
        "schema": SCREENING_SCHEMA,
        "screening_id": normalized_id,
        "audience": _text(audience, limit=300),
        "retention_curve": retention,
        "memorable_moments": moments,
        "issues": normalized_issues,
    }
    result["contract_revision"] = _revision(result, "screening-feedback.v1")
    return result


def validate_screening_feedback(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("screening_feedback must be an object")
    feedback = deepcopy(dict(value))
    if feedback.get("schema") != SCREENING_SCHEMA:
        raise ValueError("screening_feedback schema is unsupported")
    if not _text(feedback.get("screening_id")):
        raise ValueError("screening_feedback screening_id is required")
    expected = _revision(
        {key: value for key, value in feedback.items() if key != "contract_revision"},
        "screening-feedback.v1",
    )
    if _text(feedback.get("contract_revision"), limit=100) != expected:
        raise ValueError("screening_feedback contract_revision does not match its contents")
    return feedback


def _drop_points(feedback: Mapping[str, Any]) -> list[dict[str, Any]]:
    curve = [
        _mapping(item)
        for item in (feedback.get("retention_curve") or [])
        if isinstance(item, Mapping)
    ]
    result: list[dict[str, Any]] = []
    for previous, current in zip(curve, curve[1:]):
        previous_ratio = _number(previous.get("retained_ratio"))
        current_ratio = _number(current.get("retained_ratio"))
        drop = previous_ratio - current_ratio
        if drop >= 0.05:
            result.append(
                {
                    "timestamp_seconds": _number(current.get("timestamp_seconds")),
                    "drop_ratio": round(drop, 3),
                }
            )
    return result


def _action_for(issue: Mapping[str, Any], *, iteration: int) -> str:
    category = _text(issue.get("category")).casefold()
    action = _CATEGORY_ACTIONS.get(category, "local_repair")
    if iteration >= 10 and issue.get("attempt_count", 0) >= 10:
        return "simplify_shot"
    if action == "regenerate_shot" and not issue.get("localized"):
        return "regenerate_shot"
    return action


def plan_screening_repairs(
    feedback: object,
    *,
    iteration: int = 1,
    max_iterations: int = 15,
) -> dict[str, Any]:
    """Map audience problems to the smallest likely repair."""

    report = validate_screening_feedback(feedback)
    current_iteration = max(1, min(_positive_int(iteration, default=1), 10_000))
    iteration_limit = max(1, min(_positive_int(max_iterations, default=15), 1000))
    ordered = sorted(
        (
            _issue(item, index)
            for index, item in enumerate(report.get("issues") or [], start=1)
        ),
        key=lambda item: (
            -_SEVERITY_WEIGHT.get(_text(item.get("severity")), 2),
            _text(item.get("issue_id")),
        ),
    )
    actions: list[dict[str, Any]] = []
    remaining = max(0, iteration_limit - current_iteration + 1)
    for priority, issue in enumerate(ordered, start=1):
        action = _action_for(issue, iteration=current_iteration)
        if action == "simplify_shot":
            next_step = "删掉次要运动、背景信息和装饰，只留一个主体动作和一个主运镜"
        elif action == "trim":
            next_step = "剪掉等待和重复信息，把切点提前到动作/反应发生的一拍"
        elif action == "rewrite_dialogue":
            next_step = "把说明性台词改成带议程和潜台词的行动型台词"
        elif action == "rewrite_story":
            next_step = "补因果、目标和代价，不用旁白解释观众看不见的事实"
        elif action == "local_repair":
            next_step = "只修有问题的局部，不重做整个片段"
        elif action == "regenerate_shot":
            next_step = "按同一合同重出该镜，并只改一个变量"
        else:
            next_step = "保留其余镜头，定点修正本问题"
        actions.append(
            {
                "priority": priority,
                "issue_id": issue["issue_id"],
                "shot_id": issue["shot_id"],
                "timestamp_seconds": issue["timestamp_seconds"],
                "description": issue["description"],
                "audience_effect": issue["audience_effect"],
                "requires_shot_mapping": not bool(issue["shot_id"]),
                "category": issue["category"],
                "severity": issue["severity"],
                "action": action,
                "next_step": next_step,
                "attempt_count": issue["attempt_count"],
            }
        )
    result: dict[str, Any] = {
        "schema": REPAIR_PLAN_SCHEMA,
        "screening_id": report["screening_id"],
        "iteration": current_iteration,
        "max_iterations": iteration_limit,
        "simplify_after_iteration": 10,
        "stop_after_iteration": 15,
        "drop_points": _drop_points(report),
        "actions": actions,
        "policy": {
            "one_variable_per_iteration": True,
            "prefer_local_repair": True,
            "regenerate_only_when_local_repair_cannot_fix": True,
            "non_converging_shot_must_be_simplified": True,
        },
        "remaining_iterations": remaining,
    }
    result["contract_revision"] = _revision(result, "repair-plan.v1")
    return result


def audit_screening_feedback(
    feedback: object,
    *,
    iteration: int = 1,
    max_iterations: int = 15,
) -> dict[str, Any]:
    try:
        repair = plan_screening_repairs(
            feedback,
            iteration=iteration,
            max_iterations=max_iterations,
        )
    except ValueError as exc:
        return {
            "schema": SCREENING_AUDIT_SCHEMA,
            "passed": False,
            "issues": [{"code": "screening.contract_invalid", "message": str(exc)}],
            "gate_observations": {"screening_feedback_recorded": False},
        }
    blocking_actions = [
        action for action in repair["actions"] if action["severity"] in {"blocking", "high"}
    ]
    return {
        "schema": SCREENING_AUDIT_SCHEMA,
        "passed": not blocking_actions,
        "issues": [],
        "repair_plan": repair,
        "gate_observations": {
            "screening_feedback_recorded": True,
        },
    }


__all__ = [
    "REPAIR_PLAN_SCHEMA",
    "SCREENING_AUDIT_SCHEMA",
    "SCREENING_SCHEMA",
    "audit_screening_feedback",
    "build_screening_feedback",
    "plan_screening_repairs",
    "validate_screening_feedback",
]
