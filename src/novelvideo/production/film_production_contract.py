"""One inspectable control plane for a film or episodic production batch.

This module aggregates existing production contracts.  It does not execute
media, own assets, or replace WorkflowRun.  Its job is to answer the question
the workflow must ask before moving money: are story, visual identity,
dialogue/sound, prompts, screening, and delivery evidence ready for the next
stage?
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any


FILM_PRODUCTION_SCHEMA = "film_production_contract.v1"
FILM_PRODUCTION_REVISION_PREFIX = "film-production.v1:"

PREPRODUCTION_GATES: tuple[str, ...] = (
    "series_story_contract_valid",
    "visual_bible_locked",
    "asset_view_plan_ready",
    "dialogue_sound_contract_valid",
    "film_prompt_contract_valid",
)
DELIVERY_GATES: tuple[str, ...] = (
    "final_delivery_qc_passed",
)


def _text(value: object, *, limit: int = 2000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _list(value: object, *, limit: int = 200) -> list[Any]:
    return list(value[:limit]) if isinstance(value, (list, tuple)) else []


def _canonical_payload(value: Mapping[str, Any]) -> str:
    return json.dumps(
        {key: value[key] for key in sorted(value) if key != "contract_revision"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_film_production_revision(value: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_payload(value).encode("utf-8")).hexdigest()[:20]
    return f"{FILM_PRODUCTION_REVISION_PREFIX}{digest}"


def _collect_gate_observations(
    *,
    story_audit: Mapping[str, Any],
    visual_audit: Mapping[str, Any],
    dialogue_audit: Mapping[str, Any],
    prompt_audit: Mapping[str, Any],
    estimate_audit: Mapping[str, Any],
    delivery_audit: Mapping[str, Any],
    screening_audit: Mapping[str, Any],
) -> dict[str, bool | None]:
    observations: dict[str, bool | None] = {}
    for audit in (
        story_audit,
        visual_audit,
        dialogue_audit,
        prompt_audit,
        estimate_audit,
        delivery_audit,
        screening_audit,
    ):
        for key, value in _mapping(audit.get("gate_observations")).items():
            if key not in observations or observations[key] is None:
                observations[key] = value if isinstance(value, bool) else None
    return observations


def _issues(*audits: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for audit in audits:
        for issue in _list(audit.get("issues"), limit=500):
            if isinstance(issue, Mapping):
                result.append(deepcopy(dict(issue)))
            elif _text(issue):
                result.append({"code": "production.issue", "message": _text(issue)})
    return result


def _gate_ready(observations: Mapping[str, Any], gates: tuple[str, ...]) -> bool:
    return all(observations.get(gate) is True for gate in gates)


def compile_film_production_contract(
    *,
    production_id: object,
    story_contract: object = None,
    story_audit: object = None,
    visual_bible: object = None,
    visual_audit: object = None,
    dialogue_sound_contract: object = None,
    dialogue_sound_audit: object = None,
    prompt_bundle: object = None,
    prompt_audit: object = None,
    production_estimate: object = None,
    estimate_audit: object = None,
    delivery_qc: object = None,
    delivery_audit: object = None,
    screening_feedback: object = None,
    screening_audit: object = None,
) -> dict[str, Any]:
    """Aggregate sub-contracts and expose the next safe production stage."""

    normalized_id = _text(production_id, limit=200)
    if not normalized_id:
        raise ValueError("production_id is required")
    story = _mapping(story_contract)
    visual = _mapping(visual_bible)
    dialogue = _mapping(dialogue_sound_contract)
    prompt = _mapping(prompt_bundle)
    estimate = _mapping(production_estimate)
    delivery = _mapping(delivery_qc)
    screening = _mapping(screening_feedback)
    story_result = _mapping(story_audit)
    visual_result = _mapping(visual_audit)
    dialogue_result = _mapping(dialogue_sound_audit)
    prompt_result = _mapping(prompt_audit)
    estimate_result = _mapping(estimate_audit)
    delivery_result = _mapping(delivery_audit)
    screening_result = _mapping(screening_audit)
    observations = _collect_gate_observations(
        story_audit=story_result,
        visual_audit=visual_result,
        dialogue_audit=dialogue_result,
        prompt_audit=prompt_result,
        estimate_audit=estimate_result,
        delivery_audit=delivery_result,
        screening_audit=screening_result,
    )
    issues = _issues(
        story_result,
        visual_result,
        dialogue_result,
        prompt_result,
        estimate_result,
        delivery_result,
        screening_result,
    )
    stage_contracts = {
        "story": story,
        "visual_bible": visual,
        "dialogue_sound": dialogue,
        "film_prompt": prompt,
        "production_estimate": estimate,
        "delivery_qc": delivery,
        "screening_feedback": screening,
    }
    result: dict[str, Any] = {
        "schema": FILM_PRODUCTION_SCHEMA,
        "production_id": normalized_id,
        "contracts": {
            key: value
            for key, value in stage_contracts.items()
            if value
        },
        "audits": {
            "story": story_result,
            "visual_bible": visual_result,
            "dialogue_sound": dialogue_result,
            "film_prompt": prompt_result,
            "production_estimate": estimate_result,
            "delivery_qc": delivery_result,
            "screening": screening_result,
        },
        "gate_observations": observations,
        "issues": issues,
        "ready_for_media": _gate_ready(observations, PREPRODUCTION_GATES),
        "ready_for_delivery": _gate_ready(observations, DELIVERY_GATES),
        "required_preproduction_gates": list(PREPRODUCTION_GATES),
        "required_delivery_gates": list(DELIVERY_GATES),
        "policy": {
            "asset_lock_before_prompt": True,
            "missing_evidence_is_not_passed": True,
            "one_contract_source_per_stage": True,
            "media_submission_requires_ready_for_media": True,
        },
    }
    result["contract_revision"] = compute_film_production_revision(result)
    return result


def validate_film_production_contract(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("film_production_contract must be an object")
    contract = deepcopy(dict(value))
    if contract.get("schema") != FILM_PRODUCTION_SCHEMA:
        raise ValueError("film_production_contract schema is unsupported")
    if not _text(contract.get("production_id")):
        raise ValueError("film_production_contract production_id is required")
    if not isinstance(contract.get("gate_observations"), Mapping):
        raise ValueError("film_production_contract gate_observations must be an object")
    expected = compute_film_production_revision(contract)
    if _text(contract.get("contract_revision"), limit=100) != expected:
        raise ValueError("film_production_contract contract_revision does not match its contents")
    return contract


__all__ = [
    "DELIVERY_GATES",
    "FILM_PRODUCTION_REVISION_PREFIX",
    "FILM_PRODUCTION_SCHEMA",
    "PREPRODUCTION_GATES",
    "compile_film_production_contract",
    "compute_film_production_revision",
    "validate_film_production_contract",
]
