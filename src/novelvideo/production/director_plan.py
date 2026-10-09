"""Versioned contract for a long-running director production.

The plan is intent and coordination state.  It deliberately does not replace
canvas, task, registry, or media records; those systems remain authoritative
for the facts they own.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping

from novelvideo.production.director_intent import (
    build_director_intent_contract,
    validate_director_intent_contract,
)
from novelvideo.production.director_vision import (
    build_director_vision,
    validate_director_vision,
)
from novelvideo.production.project_dna import (
    build_project_dna,
    validate_project_dna,
)
from novelvideo.production.pipeline_contract import (
    compile_production_pipeline_contract,
    validate_production_pipeline_contract,
)


DIRECTOR_PLAN_SCHEMA = "director_plan.v1"
DIRECTOR_PLAN_REVISION_PREFIX = "director-plan.v1:"
DIRECTOR_PLAN_REQUIRED_FIELDS: tuple[str, ...] = (
    "objective",
    "assumptions",
    "constraints",
    "output_spec",
    "world_state_ref",
    "asset_plan",
    "episode_plan",
    "quality_gates",
    "budget",
    "model_plan_revision",
    "canvas_skeleton_refs",
    "concurrency_policy",
    "plan_revision",
)

DEFAULT_CONCURRENCY_POLICY: dict[str, Any] = {
    "episode_mode": "serial",
    "shot_mode": "bounded_parallel",
    "max_parallel_shots": 500,
    "batch_size": 500,
    "retry_scope": "failed_items_only",
}

DEFAULT_QUALITY_GATES: tuple[str, ...] = (
    "director_plan_valid",
    "director_vision_valid",
    "canvas_structure_receipt",
    "story_and_shots_complete",
    "character_identity_consistent",
    "scene_prop_continuity_consistent",
    "audio_subtitles_ready",
    "final_compose_artifact",
)


def _text(value: object, *, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def _list(value: object, *, limit: int = 100, item_limit: int = 1000) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item, limit=item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _positive_int(value: object, *, default: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))


def normalize_concurrency_policy(value: object) -> dict[str, Any]:
    raw = _mapping(value)
    policy = dict(DEFAULT_CONCURRENCY_POLICY)
    if raw.get("episode_mode") in {"serial"}:
        policy["episode_mode"] = "serial"
    if raw.get("shot_mode") in {"serial", "bounded_parallel"}:
        policy["shot_mode"] = raw["shot_mode"]
    policy["max_parallel_shots"] = _positive_int(
        raw.get("max_parallel_shots"),
        default=500,
        maximum=500,
    )
    policy["batch_size"] = _positive_int(raw.get("batch_size"), default=500, maximum=500)
    if raw.get("retry_scope") in {"failed_items_only", "whole_stage"}:
        policy["retry_scope"] = raw["retry_scope"]
    return policy


def _canonical_payload(plan: Mapping[str, Any]) -> str:
    payload = {key: plan[key] for key in sorted(plan) if key != "plan_revision"}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_plan_revision(plan: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_payload(plan).encode("utf-8")).hexdigest()[:20]
    return f"{DIRECTOR_PLAN_REVISION_PREFIX}{digest}"


def build_director_plan(
    *,
    objective: object,
    assumptions: object = (),
    constraints: object = (),
    output_spec: object = None,
    world_state_ref: object = "",
    asset_plan: object = None,
    episode_plan: object = None,
    quality_gates: object = None,
    budget: object = None,
    model_plan_revision: object = "",
    canvas_skeleton_refs: object = None,
    concurrency_policy: object = None,
    director_intent_contract: object = None,
    director_vision: object = None,
    project_id: object = "",
    project_dna: object = None,
) -> dict[str, Any]:
    """Compile one bounded, deterministic plan from Agent facts."""

    intent_contract = build_director_intent_contract(
        project_goal=objective,
        contract=director_intent_contract,
        output_spec=output_spec,
        asset_plan=asset_plan,
        episode_plan=episode_plan,
    )

    plan: dict[str, Any] = {
        "schema": DIRECTOR_PLAN_SCHEMA,
        "objective": {"text": _text(objective, limit=12000)},
        "assumptions": _list(assumptions, limit=50),
        "constraints": _list(constraints, limit=50),
        "output_spec": _mapping(output_spec),
        "world_state_ref": _text(world_state_ref, limit=300),
        "asset_plan": _mapping(asset_plan),
        "episode_plan": deepcopy(episode_plan) if isinstance(episode_plan, list) else [],
        "quality_gates": _list(quality_gates or DEFAULT_QUALITY_GATES, limit=50),
        "budget": _mapping(budget),
        "model_plan_revision": _text(model_plan_revision, limit=200),
        "canvas_skeleton_refs": _list(canvas_skeleton_refs, limit=500, item_limit=300),
        "concurrency_policy": normalize_concurrency_policy(concurrency_policy),
        "director_intent_contract": intent_contract,
        # The stage plan is the bridge between editorial intent and the
        # existing WorkflowRun handlers. It is persisted with the plan so UI,
        # Agent and runtime can inspect the same delivery contract.
        "production_pipeline": compile_production_pipeline_contract(
            intent_contract=intent_contract,
            project_goal=objective,
            workflow_id=_mapping(output_spec).get("workflow_id", ""),
            run_mode=_mapping(output_spec).get("run_mode", "draft"),
            auto_generate_paid_media=_mapping(output_spec).get(
                "auto_generate_paid_media", False
            ),
        ),
        "director_vision": (
            validate_director_vision(director_vision)
            if director_vision is not None
            else build_director_vision(
                project_goal=objective,
                intent_contract=intent_contract,
                output_spec=output_spec,
                constraints=constraints,
                episode_plan=episode_plan,
            )
        ),
    }
    if not plan["objective"]["text"]:
        raise ValueError("director plan objective is required")
    clean_project_id = _text(project_id, limit=200)
    if clean_project_id or project_dna is not None:
        plan["project_dna"] = (
            validate_project_dna(project_dna)
            if project_dna is not None
            else build_project_dna(
                project_id=clean_project_id,
                intent_contract=intent_contract,
            )
        )
    plan["plan_revision"] = compute_plan_revision(plan)
    return plan


def validate_director_plan(value: object) -> dict[str, Any]:
    """Validate and return a copy without silently changing its revision."""

    if not isinstance(value, Mapping):
        raise ValueError("director_plan must be an object")
    plan = deepcopy(dict(value))
    if plan.get("schema") != DIRECTOR_PLAN_SCHEMA:
        raise ValueError("director_plan schema is unsupported")
    required = set(DIRECTOR_PLAN_REQUIRED_FIELDS)
    missing = sorted(required - set(plan))
    if missing:
        raise ValueError("director_plan missing fields: " + ", ".join(missing))
    if not isinstance(plan.get("objective"), Mapping) or not _text(
        plan["objective"].get("text")
    ):
        raise ValueError("director_plan objective.text is required")
    if "director_intent_contract" in plan:
        try:
            plan["director_intent_contract"] = validate_director_intent_contract(
                plan["director_intent_contract"]
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
    if "director_vision" in plan:
        try:
            plan["director_vision"] = validate_director_vision(plan["director_vision"])
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
    if "production_pipeline" in plan:
        try:
            plan["production_pipeline"] = validate_production_pipeline_contract(
                plan["production_pipeline"]
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
    if "project_dna" in plan:
        try:
            plan["project_dna"] = validate_project_dna(plan["project_dna"])
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
    expected = compute_plan_revision(plan)
    if _text(plan.get("plan_revision"), limit=100) != expected:
        raise ValueError("director_plan plan_revision does not match its contents")
    plan["concurrency_policy"] = normalize_concurrency_policy(plan["concurrency_policy"])
    return plan


def compile_director_plan(
    value: object,
    *,
    objective: object,
    assumptions: object = (),
    constraints: object = (),
    output_spec: object = None,
    world_state_ref: object = "",
    asset_plan: object = None,
    episode_plan: object = None,
    quality_gates: object = None,
    budget: object = None,
    model_plan_revision: object = "",
    canvas_skeleton_refs: object = None,
    concurrency_policy: object = None,
    director_intent_contract: object = None,
    director_vision: object = None,
    project_id: object = "",
    project_dna: object = None,
) -> dict[str, Any]:
    """Compile partial Agent intent while keeping complete plans fail-closed.

    A complete contract is identifiable by its full field set or by carrying
    ``plan_revision``.  Any such value must validate byte-for-byte; it is never
    opportunistically repaired.  A genuinely partial contract is treated as
    authored intent and merged with the durable request fields before the
    server computes derived contracts and the final revision.
    """

    if value is None:
        return build_director_plan(
            objective=objective,
            assumptions=assumptions,
            constraints=constraints,
            output_spec=output_spec,
            world_state_ref=world_state_ref,
            asset_plan=asset_plan,
            episode_plan=episode_plan,
            quality_gates=quality_gates,
            budget=budget,
            model_plan_revision=model_plan_revision,
            canvas_skeleton_refs=canvas_skeleton_refs,
            concurrency_policy=concurrency_policy,
            director_intent_contract=director_intent_contract,
            director_vision=director_vision,
            project_id=project_id,
            project_dna=project_dna,
        )
    if not isinstance(value, Mapping):
        raise ValueError("director_plan must be an object")

    partial = deepcopy(dict(value))
    schema = partial.get("schema")
    if schema not in (None, DIRECTOR_PLAN_SCHEMA):
        raise ValueError("director_plan schema is unsupported")
    if (
        set(DIRECTOR_PLAN_REQUIRED_FIELDS).issubset(partial)
        or "plan_revision" in partial
    ):
        return validate_director_plan(partial)

    if "objective" in partial:
        raw_objective = partial["objective"]
        compiled_objective = (
            _text(raw_objective.get("text"), limit=12000)
            if isinstance(raw_objective, Mapping)
            else _text(raw_objective, limit=12000)
        )
    else:
        compiled_objective = _text(objective, limit=12000)

    merged_output_spec = _mapping(output_spec)
    merged_output_spec.update(_mapping(partial.get("output_spec")))
    merged_asset_plan = _mapping(asset_plan)
    merged_asset_plan.update(_mapping(partial.get("asset_plan")))

    return build_director_plan(
        objective=compiled_objective,
        assumptions=(
            partial["assumptions"] if "assumptions" in partial else assumptions
        ),
        constraints=(
            partial["constraints"] if "constraints" in partial else constraints
        ),
        output_spec=merged_output_spec,
        world_state_ref=(
            partial["world_state_ref"]
            if "world_state_ref" in partial
            else world_state_ref
        ),
        asset_plan=merged_asset_plan,
        episode_plan=(
            partial["episode_plan"] if "episode_plan" in partial else episode_plan
        ),
        quality_gates=(
            partial["quality_gates"]
            if "quality_gates" in partial
            else quality_gates
        ),
        budget=partial["budget"] if "budget" in partial else budget,
        # The model plan belongs to the server-resolved bindings, not Agent
        # prose.  Even a partial plan may not replace this revision.
        model_plan_revision=model_plan_revision,
        canvas_skeleton_refs=(
            partial["canvas_skeleton_refs"]
            if "canvas_skeleton_refs" in partial
            else canvas_skeleton_refs
        ),
        concurrency_policy=(
            partial["concurrency_policy"]
            if "concurrency_policy" in partial
            else concurrency_policy
        ),
        director_intent_contract=(
            partial["director_intent_contract"]
            if "director_intent_contract" in partial
            else director_intent_contract
        ),
        director_vision=(
            partial["director_vision"]
            if "director_vision" in partial
            else director_vision
        ),
        project_id=project_id,
        project_dna=(
            partial["project_dna"] if "project_dna" in partial else project_dna
        ),
    )


def plan_from_inputs(
    *,
    inputs: Mapping[str, Any],
    goal: object,
    success_criteria: object = (),
    model_plan_revision: object = "",
    project_id: object = "",
) -> dict[str, Any]:
    """Reuse a caller's valid plan or compile one from the durable run inputs."""

    # Keep the argument for call-site compatibility. User-facing criteria are
    # stored in the Director Ledger and WorkflowRun contract, not compiled into
    # machine quality gates without an explicit mapping.
    del success_criteria
    existing = inputs.get("director_plan")
    if existing is not None:
        return validate_director_plan(existing)
    return build_director_plan(
        objective=goal,
        assumptions=inputs.get("assumptions", ()),
        constraints=inputs.get("constraints", ()),
        output_spec=inputs.get("output_spec"),
        world_state_ref=inputs.get("world_state_ref", ""),
        asset_plan=inputs.get("asset_plan"),
        episode_plan=inputs.get("episode_plan"),
        # Human success criteria belong to the Director Ledger: they describe
        # what the user asked for. Only an explicit ``quality_gates`` field
        # opts into strict machine-verifiable gates. Treating arbitrary prose
        # such as "create the parent Run" as a gate makes a valid draft fail
        # simply because the evaluator has no matching observation key.
        quality_gates=inputs.get("quality_gates"),
        budget=inputs.get("budget"),
        model_plan_revision=model_plan_revision,
        canvas_skeleton_refs=inputs.get("canvas_skeleton_refs"),
        concurrency_policy=inputs.get("concurrency_policy"),
        director_intent_contract=inputs.get("director_intent_contract"),
        director_vision=inputs.get("director_vision"),
        project_id=project_id or inputs.get("project_id", ""),
        project_dna=inputs.get("project_dna"),
    )


__all__ = [
    "DIRECTOR_PLAN_SCHEMA",
    "DIRECTOR_PLAN_REVISION_PREFIX",
    "DIRECTOR_PLAN_REQUIRED_FIELDS",
    "DEFAULT_CONCURRENCY_POLICY",
    "DEFAULT_QUALITY_GATES",
    "build_director_plan",
    "compile_director_plan",
    "compute_plan_revision",
    "normalize_concurrency_policy",
    "plan_from_inputs",
    "validate_director_plan",
]
