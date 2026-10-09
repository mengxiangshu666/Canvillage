"""Small, immutable decision ledger shared by Agent, canvas and WorkflowRun.

The ledger records why a route was selected and which existing objects it is
allowed to touch.  It is deliberately separate from execution state: canvas,
workflow and task stores remain authoritative for their own results.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping


DIRECTOR_LEDGER_SCHEMA = "director_ledger.v1"
DIRECTOR_LEDGER_REVISION_PREFIX = "director-ledger.v1:"
_INTERACTION_MODES = {"discuss", "plan", "execute"}
_TARGET_STRATEGIES = {"reuse_existing", "create_missing"}


def _text(value: object, *, limit: int = 2_000) -> str:
    return str(value or "").strip()[:limit]


def _list(value: object, *, limit: int = 100, item_limit: int = 500) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        item_text = _text(item, limit=item_limit)
        if item_text and item_text not in result:
            result.append(item_text)
        if len(result) >= limit:
            break
    return result


def _node_ids(value: object, *, limit: int = 500) -> list[str]:
    return _list(value, limit=limit, item_limit=200)


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _canonical_payload(ledger: Mapping[str, Any]) -> str:
    return json.dumps(
        {key: ledger[key] for key in sorted(ledger) if key != "ledger_revision"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_ledger_revision(ledger: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_payload(ledger).encode("utf-8")).hexdigest()[:20]
    return f"{DIRECTOR_LEDGER_REVISION_PREFIX}{digest}"


def build_director_ledger(
    *,
    goal: object,
    success_criteria: object = (),
    action_profile: Mapping[str, Any] | None = None,
    action_route: Mapping[str, Any] | None = None,
    assumptions: object = (),
    constraints: object = (),
    unknowns: object = (),
    project_id: object = "",
    canvas_id: object = "",
    source_turn_id: object = "",
    canvas_revision: object = None,
    existing_node_ids: object = (),
    selected_node_ids: object = (),
    pinned_node_ids: object = (),
    workflow_run_id: object = "",
    existing_run_id: object = "",
    parent_run_id: object = "",
    evidence_refs: object = (),
) -> dict[str, Any]:
    """Compile one bounded ledger from observed facts and a route decision."""

    profile = _mapping(action_profile)
    interaction_mode = _text(profile.get("interaction_mode"), limit=32) or "execute"
    if interaction_mode not in _INTERACTION_MODES:
        interaction_mode = "execute"
    target_strategy = _text(profile.get("target_strategy"), limit=40)
    target_ids = _node_ids(
        profile.get("target_node_ids")
        if "target_node_ids" in profile
        else selected_node_ids
    )
    if target_strategy not in _TARGET_STRATEGIES:
        target_strategy = "reuse_existing" if target_ids else "create_missing"
    creation_reason = _text(profile.get("creation_reason"), limit=1_000)
    if target_strategy == "create_missing" and not creation_reason:
        creation_reason = "由已选工作流定义承载本次缺失结构"

    ledger: dict[str, Any] = {
        "schema": DIRECTOR_LEDGER_SCHEMA,
        "goal": _text(goal, limit=12_000),
        "success_criteria": _list(success_criteria, limit=100),
        "interaction_mode": interaction_mode,
        "target_strategy": target_strategy,
        "target_node_ids": target_ids,
        "existing_run_id": _text(existing_run_id or profile.get("existing_run_id"), limit=200),
        "workflow_run_id": _text(workflow_run_id, limit=200),
        "parent_run_id": _text(parent_run_id, limit=200),
        "creation_reason": creation_reason,
        "assumptions": _list(assumptions, limit=50),
        "constraints": _list(constraints, limit=50),
        "unknowns": _list(unknowns, limit=50),
        "project_id": _text(project_id, limit=240),
        "canvas_id": _text(canvas_id, limit=240),
        "source_turn_id": _text(source_turn_id, limit=240),
        "canvas_revision": (
            int(canvas_revision)
            if isinstance(canvas_revision, int)
            and not isinstance(canvas_revision, bool)
            and canvas_revision >= 0
            else None
        ),
        "existing_node_ids": _node_ids(existing_node_ids),
        "selected_node_ids": _node_ids(selected_node_ids),
        "pinned_node_ids": _node_ids(pinned_node_ids),
        "action_route": _mapping(action_route),
        "evidence_refs": _list(evidence_refs, limit=50, item_limit=600),
    }
    if not ledger["goal"]:
        raise ValueError("director ledger goal is required")
    if not ledger["success_criteria"]:
        raise ValueError("director ledger success_criteria must contain at least one criterion")
    ledger["ledger_revision"] = compute_ledger_revision(ledger)
    return ledger


def validate_director_ledger(value: object) -> dict[str, Any]:
    """Validate a persisted ledger without silently changing its revision."""

    if not isinstance(value, Mapping):
        raise ValueError("director_ledger must be an object")
    ledger = deepcopy(dict(value))
    if ledger.get("schema") != DIRECTOR_LEDGER_SCHEMA:
        raise ValueError("director_ledger schema is unsupported")
    required = {
        "goal", "success_criteria", "interaction_mode", "target_strategy",
        "target_node_ids", "existing_run_id", "workflow_run_id", "parent_run_id",
        "creation_reason", "assumptions", "constraints", "unknowns", "project_id",
        "canvas_id", "source_turn_id", "canvas_revision", "existing_node_ids",
        "selected_node_ids", "pinned_node_ids", "action_route", "evidence_refs",
        "ledger_revision",
    }
    missing = sorted(required - set(ledger))
    if missing:
        raise ValueError("director_ledger missing fields: " + ", ".join(missing))
    if not _text(ledger.get("goal")):
        raise ValueError("director_ledger goal is required")
    if not _list(ledger.get("success_criteria")):
        raise ValueError("director_ledger success_criteria is required")
    if ledger.get("interaction_mode") not in _INTERACTION_MODES:
        raise ValueError("director_ledger interaction_mode is unsupported")
    if ledger.get("target_strategy") not in _TARGET_STRATEGIES:
        raise ValueError("director_ledger target_strategy is unsupported")
    expected = compute_ledger_revision(ledger)
    if _text(ledger.get("ledger_revision"), limit=100) != expected:
        raise ValueError("director_ledger ledger_revision does not match its contents")
    return ledger


__all__ = [
    "DIRECTOR_LEDGER_SCHEMA",
    "DIRECTOR_LEDGER_REVISION_PREFIX",
    "build_director_ledger",
    "compute_ledger_revision",
    "validate_director_ledger",
]
