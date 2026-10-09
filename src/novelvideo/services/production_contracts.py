"""Neutral application facade for production-stage contracts.

WorkflowRuntime needs to validate and compile director contracts, but it
should not know which ``production`` or ``creative_execution`` module owns the
implementation.  The wrappers keep those implementations in place and make
the dependency direction explicit without introducing a second contract
system.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def require_director_clarification_ready(**kwargs: Any) -> dict[str, Any]:
    from novelvideo.creative_execution.director_clarification import (
        require_director_clarification_ready as require_ready,
    )

    return require_ready(**kwargs)


def normalize_concurrency_policy(value: object) -> dict[str, Any]:
    from novelvideo.production.director_plan import (
        normalize_concurrency_policy as normalize,
    )

    return normalize(value)


def plan_from_inputs(
    *,
    inputs: Mapping[str, Any],
    goal: object,
    success_criteria: object = (),
    model_plan_revision: object = "",
    project_id: object = "",
) -> dict[str, Any]:
    from novelvideo.production.director_plan import plan_from_inputs as compile_plan

    return compile_plan(
        inputs=inputs,
        goal=goal,
        success_criteria=success_criteria,
        model_plan_revision=model_plan_revision,
        project_id=project_id,
    )


def validate_director_plan(value: object) -> dict[str, Any]:
    from novelvideo.production.director_plan import validate_director_plan as validate

    return validate(value)


def validate_director_vision(value: object) -> dict[str, Any]:
    from novelvideo.production.director_vision import validate_director_vision as validate

    return validate(value)


def validate_project_dna(value: object) -> dict[str, Any]:
    from novelvideo.production.project_dna import validate_project_dna as validate

    return validate(value)


def evaluate_quality_gates(
    *,
    requested_gates: object,
    observations: Mapping[str, Any],
    evidence: Mapping[str, Any] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    from novelvideo.production.director_evaluator import evaluate_quality_gates as evaluate

    return evaluate(
        requested_gates=requested_gates,
        observations=observations,
        evidence=evidence,
        strict=strict,
    )


def audit_cinematic_contracts(
    shots: object,
    *,
    director_vision: Mapping[str, Any] | None = None,
    project_dna: Mapping[str, Any] | None = None,
    delivery_qc: object = None,
) -> dict[str, Any]:
    """Audit cinematic contract evidence without exposing its owner module."""

    from novelvideo.production.cinematic_contract import (
        audit_cinematic_contracts as audit,
    )

    return audit(
        shots,
        director_vision=director_vision,
        project_dna=project_dna,
        delivery_qc=delivery_qc,
    )


def cinematic_quality_gates() -> tuple[str, ...]:
    """Read the canonical cinematic gate names lazily."""

    from novelvideo.production.cinematic_contract import CINEMATIC_QUALITY_GATES

    return tuple(CINEMATIC_QUALITY_GATES)


def compile_film_preproduction_contract(**kwargs: Any) -> dict[str, Any]:
    """Compile the film-production control plane through its application service."""

    from novelvideo.services.film_production import (
        compile_film_preproduction_contract as compile_contract,
    )

    return compile_contract(**kwargs)


def validate_film_production_contract(value: object) -> dict[str, Any]:
    from novelvideo.production.film_production_contract import (
        validate_film_production_contract as validate,
    )

    return validate(value)


def film_production_quality_gates(
    *,
    include_delivery: bool = True,
) -> tuple[str, ...]:
    from novelvideo.production.film_production_contract import (
        DELIVERY_GATES,
        PREPRODUCTION_GATES,
    )

    gates = (
        (*PREPRODUCTION_GATES, *DELIVERY_GATES)
        if include_delivery
        else PREPRODUCTION_GATES
    )
    return tuple(dict.fromkeys(gates))


def inject_filmcraft_rules(
    *,
    node_type: str,
    params: Mapping[str, Any] | None = None,
    director_vision: Mapping[str, Any] | None = None,
    project_dna: Mapping[str, Any] | None = None,
    source_text: str = "",
    creation_stage: str = "",
) -> list[dict[str, str]]:
    """Select the filmcraft rules one compile context triggers, lazily.

    WorkflowRuntime is not allowed to import ``production`` implementations
    directly (``domain_boundaries.json`` rule
    ``workflow-to-production-implementation``); this facade is the sanctioned
    route, exactly like ``requested_combat_duration_seconds`` above.
    """

    from novelvideo.production.filmcraft_kb import (
        inject_filmcraft_rules as inject,
    )

    return inject(
        node_type=node_type,
        params=params,
        director_vision=director_vision,
        project_dna=project_dna,
        source_text=source_text,
        creation_stage=creation_stage,
    )


def has_filmcraft_action_context(source_text: str) -> bool:
    """Decide whether a request states an explicit combat/action scene, lazily.

    This is the only workflow-facing action-context decision API.  The
    authoritative cue list and its clause-scoped negation stay in
    ``production.filmcraft_kb``, so WorkflowRuntime never keeps a second copy
    that could drift.  The implementation normalizes the text exactly like
    ``inject_filmcraft_rules`` does (strip, then 12,000 characters), so the
    decision and the rules actually selected always read the same characters;
    a negation only governs a cue inside its own clause.
    """

    from novelvideo.production.filmcraft_kb import (
        _has_filmcraft_action_context,
    )

    return bool(_has_filmcraft_action_context(source_text))


def compile_combat_film_contract(**kwargs: Any) -> dict[str, Any]:
    """Compile the combat-film gates without exposing its production owner."""

    from novelvideo.production.combat_film_contract import (
        compile_combat_film_contract as compile_contract,
    )

    return compile_contract(**kwargs)


def combat_film_quantity_gates() -> tuple[str, ...]:
    """Read the canonical combat gate names lazily."""

    from novelvideo.production.combat_film_contract import COMBAT_FILM_GATES

    return tuple(COMBAT_FILM_GATES)


def requested_combat_duration_seconds(request: object = "") -> float | None:
    """Read the seconds a request names, without exposing its production owner.

    WorkflowRuntime is not allowed to import ``production`` implementations
    directly (``domain_boundaries.json`` rule
    ``workflow-to-production-implementation``); this facade is the sanctioned
    route, exactly like ``compile_combat_film_contract`` above.
    """

    from novelvideo.production.combat_film_contract import (
        requested_duration_seconds,
    )

    return requested_duration_seconds(request)


def compile_delivery_qc_contract(**kwargs: Any) -> dict[str, Any]:
    from novelvideo.services.film_production import compile_delivery_qc_contract as compile_qc

    return compile_qc(**kwargs)


def project_release_readiness(
    value: object,
    *,
    required_checks: object = None,
) -> dict[str, Any]:
    """Project publication readiness without exposing its production owner."""

    from novelvideo.production.delivery_qc_contract import (
        project_release_readiness as project,
    )

    if required_checks is None:
        return project(value)
    return project(value, required_checks=required_checks)


def compile_screening_repair_plan(**kwargs: Any) -> dict[str, Any]:
    from novelvideo.services.film_production import (
        compile_screening_repair_plan as compile_plan,
    )

    return compile_plan(**kwargs)


def normalize_gate_name(value: object) -> str:
    from novelvideo.production.director_evaluator import normalize_gate_name as normalize

    return normalize(value)


def default_quality_gates() -> tuple[str, ...]:
    """Read the canonical defaults lazily so callers do not import production."""

    from novelvideo.production.director_plan import DEFAULT_QUALITY_GATES

    return tuple(DEFAULT_QUALITY_GATES)


def compile_production_pipeline_contract(
    *,
    intent_contract: object = None,
    project_goal: object = "",
    workflow_id: object = "",
    run_mode: object = "draft",
    auto_generate_paid_media: object = False,
    allow_starter_workflow: object = True,
) -> dict[str, Any]:
    """Compile the canonical stage contract without exposing its owner module."""

    from novelvideo.production.pipeline_contract import (
        compile_production_pipeline_contract as compile_contract,
    )

    return compile_contract(
        intent_contract=intent_contract,
        project_goal=project_goal,
        workflow_id=workflow_id,
        run_mode=run_mode,
        auto_generate_paid_media=auto_generate_paid_media,
        allow_starter_workflow=allow_starter_workflow,
    )


def validate_production_pipeline_contract(value: object) -> dict[str, Any]:
    from novelvideo.production.pipeline_contract import (
        validate_production_pipeline_contract as validate_contract,
    )

    return validate_contract(value)


def resolve_authoritative_production_pipeline(
    value: Mapping[str, Any],
    *,
    validate: bool = True,
) -> tuple[dict[str, Any] | None, str]:
    """Resolve the one production pipeline contract a workflow may execute.

    New runs persist the frozen contract at ``inputs.production_pipeline``.
    Older director-planned runs carried the same contract inside
    ``inputs.director_plan``.  Keep that legacy carrier readable at this seam
    so handlers do not each invent their own fallback rules.
    """

    container: Mapping[str, Any] = value
    raw_inputs = value.get("inputs")
    if isinstance(raw_inputs, Mapping):
        container = raw_inputs

    if "production_pipeline" in container and container.get("production_pipeline") is not None:
        selected = container.get("production_pipeline")
        return (
            validate_production_pipeline_contract(selected)
            if validate
            else dict(selected) if isinstance(selected, Mapping) else None,
            "inputs.production_pipeline",
        )

    plan = container.get("director_plan")
    if isinstance(plan, Mapping) and "production_pipeline" in plan:
        nested = plan.get("production_pipeline")
        if nested is not None:
            return (
                validate_production_pipeline_contract(nested)
                if validate
                else dict(nested) if isinstance(nested, Mapping) else None,
                "inputs.director_plan.production_pipeline",
            )

    return None, "compiled"


__all__ = [
    "audit_cinematic_contracts",
    "combat_film_quantity_gates",
    "compile_combat_film_contract",
    "requested_combat_duration_seconds",
    "compile_delivery_qc_contract",
    "compile_film_preproduction_contract",
    "compile_screening_repair_plan",
    "cinematic_quality_gates",
    "film_production_quality_gates",
    "has_filmcraft_action_context",
    "inject_filmcraft_rules",
    "default_quality_gates",
    "compile_production_pipeline_contract",
    "evaluate_quality_gates",
    "normalize_concurrency_policy",
    "normalize_gate_name",
    "plan_from_inputs",
    "project_release_readiness",
    "require_director_clarification_ready",
    "resolve_authoritative_production_pipeline",
    "validate_director_plan",
    "validate_director_vision",
    "validate_film_production_contract",
    "validate_project_dna",
    "validate_production_pipeline_contract",
]
