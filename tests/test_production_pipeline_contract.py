from __future__ import annotations

import pytest

from novelvideo.production.director_intent import build_director_intent_contract
from novelvideo.production.director_plan import build_director_plan
from novelvideo.production.pipeline_contract import (
    PIPELINE_CONTRACT_SCHEMA,
    ProductionPipelineContractError,
    compile_production_pipeline_contract,
    validate_production_pipeline_contract,
)
from novelvideo.workflow_runtime.executor import (
    _media_generation_handler,
    _starter_workflow_handler,
)


def test_pipeline_contract_maps_delivery_level_to_real_stages() -> None:
    intent = build_director_intent_contract(
        project_goal="制作一个 8 镜头短片，只搭分镜草稿，不生成媒体",
    )

    contract = compile_production_pipeline_contract(
        intent_contract=intent,
        workflow_id="storyboard-production",
        run_mode="auto",
        auto_generate_paid_media=True,
    )

    assert contract["schema"] == PIPELINE_CONTRACT_SCHEMA
    assert contract["delivery_level"] == "shot_draft"
    assert contract["active_stage_ids"] == [
        "brief",
        "canvas_scaffold",
        "storyboard",
        "assets",
        "delivery",
    ]
    assert "media" in contract["deferred_stage_ids"] or "media" not in contract["active_stage_ids"]
    assert "media_assets" not in contract["required_outputs"]
    assert validate_production_pipeline_contract(contract) == contract


def test_final_film_draft_defers_paid_media_but_keeps_delivery_contract() -> None:
    intent = build_director_intent_contract(
        project_goal="完成最终成片，带旁白、字幕和最终导出",
        output_spec={"delivery_level": "final_film"},
    )

    contract = compile_production_pipeline_contract(
        intent_contract=intent,
        workflow_id="one-click-film",
        run_mode="draft",
        auto_generate_paid_media=False,
    )

    assert contract["delivery_level"] == "final_film"
    assert "media" in contract["deferred_stage_ids"]
    assert "review" in contract["active_stage_ids"]
    assert "delivery" in contract["active_stage_ids"]
    assert contract["policies"]["media_submission"] == "defer"
    review = next(stage for stage in contract["stages"] if stage["id"] == "review")
    delivery = next(stage for stage in contract["stages"] if stage["id"] == "delivery")
    assert {
        "lighting_continuity_consistent",
        "screen_direction_consistent",
        "color_look_consistent",
        "edit_rhythm_ready",
        "sound_design_ready",
        "cross_episode_continuity_valid",
    }.issubset(set(review["quality_gates"]))
    assert "final_delivery_qc_passed" in delivery["quality_gates"]


def test_dynamic_composition_does_not_require_a_starter_template() -> None:
    intent = build_director_intent_contract(
        project_goal="制作一个角色、场景和分镜组成的短片",
    )
    contract = compile_production_pipeline_contract(
        intent_contract=intent,
        workflow_id="storyboard-production",
        run_mode="draft",
        allow_starter_workflow=False,
    )
    scaffold = next(
        stage for stage in contract["stages"] if stage["id"] == "canvas_scaffold"
    )
    assert scaffold["execution"] == "not_requested"
    assert "canvas_scaffold" not in contract["active_stage_ids"]
    assert contract["policies"]["starter_workflow"] == "dynamic_composition"


@pytest.mark.asyncio
async def test_dynamic_composition_skips_starter_handler() -> None:
    pipeline = compile_production_pipeline_contract(
        project_goal="只搭角色、场景和分镜结构",
        workflow_id="storyboard-production",
        allow_starter_workflow=False,
    )
    result = await _starter_workflow_handler(
        {
            "inputs": {"production_pipeline": pipeline},
            "artifacts": {},
        },
        {"id": "canvas_structure"},
    )
    assert result.event_type == "step_completed"
    assert result.payload["kind"] == "dynamic_canvas_composition"
    assert result.payload["status"] == "skipped"


def test_pipeline_contract_revision_rejects_tampering() -> None:
    contract = compile_production_pipeline_contract(project_goal="做一个分镜草稿")
    changed = dict(contract)
    changed["delivery_level"] = "final_film"

    with pytest.raises(ProductionPipelineContractError, match="contract_revision"):
        validate_production_pipeline_contract(changed)


def test_director_plan_carries_the_same_pipeline_contract() -> None:
    plan = build_director_plan(
        objective="制作一个 5 镜头视频片段",
        output_spec={"delivery_level": "media_draft", "workflow_id": "one-click-film"},
    )

    pipeline = plan["production_pipeline"]
    assert pipeline["workflow_id"] == "one-click-film"
    assert pipeline["delivery_level"] == "media_draft"
    assert "media" in pipeline["active_stage_ids"]


@pytest.mark.asyncio
async def test_auto_shot_draft_does_not_start_media_when_contract_excludes_it() -> None:
    result = await _media_generation_handler(
        {
            "run_mode": "auto",
            "inputs": {
                "director_intent_contract": {
                    "delivery_level": "shot_draft",
                },
                "production_pipeline": compile_production_pipeline_contract(
                    intent_contract={"delivery_level": "shot_draft"},
                    project_goal="只做一个分镜草稿",
                    run_mode="auto",
                    auto_generate_paid_media=True,
                )
            },
        },
        {"id": "media_generation"},
    )

    assert result.event_type == "step_completed"
    assert result.payload["started"] is False
    assert result.payload["policy"] == "delivery_level"
