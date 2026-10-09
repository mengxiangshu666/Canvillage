"""Adapt workflow run evidence to the canonical cinematic audit contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from novelvideo.services.production_contracts import (
    audit_cinematic_contracts,
    compile_combat_film_contract,
    film_production_quality_gates,
    validate_film_production_contract,
)
from novelvideo.workflow_runtime.director_inputs import (
    resolve_director_intent_contract,
)


def _film_production_contract(run: Mapping[str, Any]) -> object:
    """Find the film control plane without inventing a second execution state."""

    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), Mapping) else {}
    inputs = run.get("inputs") if isinstance(run.get("inputs"), Mapping) else {}
    containers: list[Mapping[str, Any]] = [inputs, artifacts]
    for key in ("story_and_shots", "media_generation"):
        value = artifacts.get(key)
        if isinstance(value, Mapping):
            containers.append(value)
    for container in containers:
        value = container.get("film_production")
        if isinstance(value, Mapping):
            return value
    result = run.get("result")
    return result.get("film_production") if isinstance(result, Mapping) else None


def _delivery_qc_observation(run: Mapping[str, Any]) -> object:
    """Return an explicit final-delivery QC receipt, never a completion guess."""

    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), Mapping) else {}
    for key in (
        "final_delivery_qc",
        "delivery_qc",
        "final_qc",
    ):
        if key in artifacts:
            return artifacts[key]
    final_film = artifacts.get("final_film")
    if isinstance(final_film, Mapping):
        for key in ("final_delivery_qc", "delivery_qc", "final_qc"):
            if key in final_film:
                return final_film[key]
    delivery = artifacts.get("delivery")
    if isinstance(delivery, Mapping):
        for key in ("final_delivery_qc", "delivery_qc", "final_qc", "quality_report"):
            if key in delivery:
                return delivery[key]
    result = run.get("result")
    if isinstance(result, Mapping):
        for key in ("final_delivery_qc", "delivery_qc", "final_qc"):
            if key in result:
                return result[key]
    return None


def _combat_review(
    *,
    run: Mapping[str, Any],
    shots: object,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile the combat-film contract from the same run evidence."""

    inputs = run.get("inputs") if isinstance(run.get("inputs"), Mapping) else {}
    intent = resolve_director_intent_contract(inputs)
    director_vision = plan.get("director_vision")
    project_dna = plan.get("project_dna")
    return compile_combat_film_contract(
        shots=shots,
        request=inputs.get("request"),
        intent=intent,
        director_vision=director_vision if isinstance(director_vision, Mapping) else None,
        project_dna=project_dna if isinstance(project_dna, Mapping) else None,
    )


def _merge_combat_review(
    review: dict[str, Any],
    combat: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach combat gates without weakening the cinematic audit.

    Non-combat runs are untouched: the contract reports ``applies=False`` and
    carries no gates, so an ordinary request is never judged by this contract.
    """

    if not combat.get("applies"):
        return review
    merged = dict(review.get("gate_observations") or {})
    for gate, value in (combat.get("gate_observations") or {}).items():
        if isinstance(value, bool):
            merged[gate] = value
    review["gate_observations"] = merged
    review["combat_film_contract"] = dict(combat)
    review["combat_issues"] = list(combat.get("issues") or [])
    review["required_gates"] = list(
        dict.fromkeys([*(review.get("required_gates") or []), *(combat.get("required_gates") or [])])
    )
    return review


def aspect_ratio_value(value: str) -> float | None:
    left, separator, right = value.partition(":")
    if not separator:
        return None
    try:
        width = float(left)
        height = float(right)
    except ValueError:
        return None
    return width / height if width > 0 and height > 0 else None


def ordered_media_assets(
    media_assets: list[Any],
    target_node_ids: object,
) -> list[Any]:
    """Restore storyboard order after incremental task observations merge."""

    order = [
        str(node_id).strip()
        for node_id in (target_node_ids if isinstance(target_node_ids, list) else [])
        if str(node_id).strip()
    ]
    if not order:
        return list(media_assets)
    by_node = {
        str(asset.get("node_id") or "").strip(): asset
        for asset in media_assets
        if isinstance(asset, dict) and str(asset.get("node_id") or "").strip()
    }
    ordered = [by_node[node_id] for node_id in order if node_id in by_node]
    ordered_ids = {
        str(asset.get("node_id") or "").strip()
        for asset in ordered
        if isinstance(asset, dict)
    }
    ordered.extend(
        asset
        for asset in media_assets
        if not isinstance(asset, dict)
        or str(asset.get("node_id") or "").strip() not in ordered_ids
    )
    return ordered


def build_cinematic_review(
    *,
    run: Mapping[str, Any],
    shots: object,
    director_plan: object,
    include_delivery_gates: bool = False,
) -> dict[str, Any]:
    """Build the evidence-backed cinematic audit for a workflow stage.

    Pre-production gates always apply. The final delivery gate belongs only
    to the final-film stage; requesting it during a media draft would reject
    work before the delivery QC has had a chance to run.
    """

    plan = director_plan if isinstance(director_plan, Mapping) else {}
    director_vision = plan.get("director_vision")
    project_dna = plan.get("project_dna")
    review = audit_cinematic_contracts(
        shots,
        director_vision=director_vision if isinstance(director_vision, Mapping) else None,
        project_dna=project_dna if isinstance(project_dna, Mapping) else None,
        delivery_qc=_delivery_qc_observation(run),
    )
    # A combat request is judged on its own declared gates.  This runs on both
    # paths below: the early-return branch has no film_production contract and
    # would otherwise drop the combat gates entirely, and the normal branch
    # would lose them if this were only wired into the early return.
    #
    # The combat gates are deliberately NOT in ``quality_stage.CROSS_SHOT_GATES``
    # and this review does not set ``media_blocked`` for them.  ``CROSS_SHOT_GATES``
    # is consulted with a flag derived from ``minimum_shots``, which is 1 for the
    # whole ``idea`` tier -- and ``infer_delivery_level`` puts "30 秒极限打斗" in
    # that tier, so membership would silently erase the gates for the flagship
    # request.  ``media_blocked`` is likewise left alone: this contract judges
    # declarations, and turning it into a paid-media gate would block every fight
    # request before media purely because no producer writes the declarations yet.
    combat = _combat_review(run=run, shots=shots, plan=plan)
    raw_contract = _film_production_contract(run)
    if raw_contract is None:
        review["media_blocked"] = False
        review["required_gates"] = []
        return _merge_combat_review(review, combat)
    contract: dict[str, Any] = {}
    try:
        contract = validate_film_production_contract(raw_contract)
    except (ImportError, ValueError) as exc:
        observations = {
            gate: False for gate in film_production_quality_gates()
        }
        audit = {
            "valid": False,
            "issues": [{"code": "film_production.contract_invalid", "message": str(exc)}],
        }
    else:
        all_gate_names = film_production_quality_gates()
        observations = {
            gate: contract.get("gate_observations", {}).get(gate)
            for gate in all_gate_names
        }
        observations.update(
            {
                gate: value
                for gate, value in contract.get("gate_observations", {}).items()
                if gate not in observations and isinstance(value, bool)
            }
        )
        audit = {"valid": True, "issues": []}
    merged = dict(review.get("gate_observations") or {})
    for gate, value in observations.items():
        if gate not in merged or merged[gate] is None:
            merged[gate] = value
    review["gate_observations"] = merged
    review["film_production_contract"] = contract or dict(raw_contract)
    review["film_production_audit"] = audit
    review["required_gates"] = list(
        film_production_quality_gates(
            include_delivery=include_delivery_gates,
        )
    )
    review["media_blocked"] = not (
        audit["valid"]
        and review["film_production_contract"].get("ready_for_media") is True
    )
    return _merge_combat_review(review, combat)


__all__ = ["aspect_ratio_value", "build_cinematic_review", "ordered_media_assets"]
