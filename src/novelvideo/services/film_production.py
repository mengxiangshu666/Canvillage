"""Application-level composition for film production contracts.

The service joins Production-owned contracts with the Freezone prompt bundle
without making either domain import the other's implementation.  The returned
contract is a projection for workflow gating and receipts, not a second
execution state machine.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def compile_film_preproduction_contract(
    *,
    production_id: object,
    rows: object,
    story_contract: Mapping[str, Any] | None = None,
    series_data: Mapping[str, Any] | None = None,
    director_vision: Mapping[str, Any] | None = None,
    project_dna: Mapping[str, Any] | None = None,
    assets: object = None,
    output_spec: Mapping[str, Any] | None = None,
    estimate_inputs: Mapping[str, Any] | None = None,
    rewrite_video_negatives: bool = True,
) -> dict[str, Any]:
    """Compile story, visual, dialogue, prompt, and estimate gates."""

    from novelvideo.freezone.film_prompt_contract import (
        audit_film_prompt_bundle,
        build_film_prompt_bundle,
        compile_film_prompt_rows,
    )
    from novelvideo.production.dialogue_sound_contract import (
        audit_dialogue_sound_contract,
        build_dialogue_sound_contract,
    )
    from novelvideo.production.film_production_contract import (
        compile_film_production_contract,
    )
    from novelvideo.production.production_estimator import (
        audit_production_estimate,
        build_production_estimate,
    )
    from novelvideo.production.story_contract import (
        audit_series_story_contract,
        build_series_story_contract,
    )
    from novelvideo.production.visual_bible import (
        audit_visual_bible,
        build_visual_bible,
    )

    prompt_rows, prompt_bundle = compile_film_prompt_rows(
        rows,
        rewrite_video_negatives=rewrite_video_negatives,
    )
    prompt_audit = audit_film_prompt_bundle(
        build_film_prompt_bundle(
            prompt_rows,
            rewrite_video_negatives=False,
        )
    )
    dialogue_contract = build_dialogue_sound_contract(
        shots=prompt_rows,
        director_vision=director_vision,
        project_dna=project_dna,
    )
    dialogue_audit = audit_dialogue_sound_contract(dialogue_contract)
    visual_bible = build_visual_bible(
        director_vision=director_vision,
        project_dna=project_dna,
        assets=assets,
        shots=prompt_rows,
        output_spec=output_spec,
    )
    visual_audit = audit_visual_bible(visual_bible)

    final_story_contract = dict(story_contract or {})
    story_audit: dict[str, Any] = {}
    if not final_story_contract and isinstance(series_data, Mapping):
        final_story_contract = build_series_story_contract(**dict(series_data))
    if final_story_contract:
        story_audit = audit_series_story_contract(final_story_contract)

    estimate_contract: dict[str, Any] = {}
    estimate_audit: dict[str, Any] = {}
    if isinstance(estimate_inputs, Mapping):
        estimate_contract = build_production_estimate(**dict(estimate_inputs))
        estimate_audit = audit_production_estimate(estimate_contract)

    return {
        "film_production": compile_film_production_contract(
            production_id=production_id,
            story_contract=final_story_contract,
            story_audit=story_audit,
            visual_bible=visual_bible,
            visual_audit=visual_audit,
            dialogue_sound_contract=dialogue_contract,
            dialogue_sound_audit=dialogue_audit,
            prompt_bundle=prompt_bundle,
            prompt_audit=prompt_audit,
            production_estimate=estimate_contract,
            estimate_audit=estimate_audit,
        ),
        "rows": prompt_rows,
    }


def compile_delivery_qc_contract(
    *,
    observations: Mapping[str, Any] | None = None,
    target: Mapping[str, Any] | None = None,
    required_checks: object = None,
) -> dict[str, Any]:
    from novelvideo.production.delivery_qc_contract import (
        build_delivery_qc_contract,
    )

    return build_delivery_qc_contract(
        observations=observations,
        target=target,
        required_checks=required_checks,
    )


def compile_screening_repair_plan(
    *,
    feedback: Mapping[str, Any],
    iteration: int = 1,
    max_iterations: int = 15,
) -> dict[str, Any]:
    from novelvideo.production.screening_repair import plan_screening_repairs

    return plan_screening_repairs(
        feedback,
        iteration=iteration,
        max_iterations=max_iterations,
    )


__all__ = [
    "compile_delivery_qc_contract",
    "compile_film_preproduction_contract",
    "compile_screening_repair_plan",
]
