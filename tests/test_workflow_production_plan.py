from __future__ import annotations

import json
from typing import Any

import pytest

from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.workflow_runtime.freezone_storyboard import _script_rows
from novelvideo.workflow_runtime.production_plan import (
    PLAN_SCHEMA,
    build_production_plan,
    compute_plan_revision,
    validate_production_plan_binding,
)
from novelvideo.workflow_runtime.script_asset_ledger import (
    build_script_asset_ledger,
)
from novelvideo.workflow_runtime.step_contract import WorkflowStepExecutionError


def _rows() -> list[dict[str, Any]]:
    return [
        {
            "shot_id": "shot-a",
            "shot_no": 1,
            "duration": 5,
            "visual_description": "阿木在暗房里举起相机。",
            "character_1": "阿木",
            "character_description_1": "[阿木: 短黑发，深蓝外套。]",
            "scene_tags": "旧照相馆、暗房",
            "prop_tags": "相机",
            "shot": "中景",
            "character_action": "举起相机",
            "emotion": "克制",
            "lighting_mood": "红色侧光",
            "sound": "雨声",
            "dialogue": "无",
            "shot_prompt": "暗房红灯中，阿木举起相机，中景，写实电影感。",
            "video_motion_prompt": "镜头缓慢推进，阿木举起相机，雨声持续。",
        },
        {
            "shot_id": "shot-b",
            "shot_no": 2,
            "duration": 4,
            "visual_description": "阿木放下相机，看向门外。",
            "character_1": "阿木",
            "character_description_1": "[阿木: 短黑发，深蓝外套。]",
            "scene_tags": "旧照相馆、暗房",
            "prop_tags": "相机",
            "shot": "近景",
            "character_action": "放下相机",
            "emotion": "警觉",
            "lighting_mood": "红色侧光",
            "sound": "雨声",
            "dialogue": "无",
            "shot_prompt": "暗房红灯中，阿木放下相机看向门外，近景。",
            "video_motion_prompt": "镜头轻微横移，阿木转头看向门外。",
        },
    ]


def _run_with_script() -> dict[str, Any]:
    rows = _rows()
    ledger = build_script_asset_ledger(rows)
    return {
        "id": "run-plan-1",
        "workflow_id": "freezone-final-film",
        "canvas_id": "canvas-plan-1",
        "inputs": {
            "request": "制作一个暗房短片",
            "director_intent_contract": {
                "delivery_level": "final_film",
                "shot_count": 2,
                "audio_required": False,
                "subtitles_required": False,
                "compose_required": True,
                "contract_revision": "director-intent.v1:test",
            },
        },
        "model_plan_revision": "direct-model-plan.v2",
        "model_plan_snapshot": {
            "schema": "canvas_model_plan_snapshot.v1",
            "model_plan_revision": "direct-model-plan.v2",
        },
        "artifacts": {
            "script_contract": {
                "schema": "workflow_freezone_script_artifact.v1",
                "kind": "freezone_script_contract",
                "status": "completed",
                "rows": rows,
                "asset_ledger": ledger,
                "contract_report": {
                    "rows_fingerprint": "a" * 64,
                    "blocking_count": 0,
                },
                "result_signature": "b" * 64,
            }
        },
    }


@pytest.mark.parametrize(
    "workflow_id",
    (
        "freezone-storyboard-images",
        "freezone-shot-videos",
        "freezone-final-film",
    ),
)
def test_plan_is_inserted_before_every_paid_media_stage(workflow_id: str) -> None:
    definition = get_workflow_definition(workflow_id)
    assert definition is not None
    step_ids = [step.id for step in definition.steps]

    assert step_ids.index("script_contract") < step_ids.index("production_plan")
    assert step_ids.index("production_plan") < step_ids.index("storyboard_images")
    plan_step = next(step for step in definition.steps if step.id == "production_plan")
    assert plan_step.handler == "freezone.production_plan"
    assert plan_step.execution_semantics.side_effect == "none"
    assert plan_step.max_attempts == 1
    assert plan_step.retry_policy == "never"


def test_plan_freezes_delivery_chapters_prompts_assets_and_estimate_once() -> None:
    run = _run_with_script()

    first = build_production_plan(run)
    second = build_production_plan(run)

    assert first == second
    assert first["schema"] == PLAN_SCHEMA
    assert first["plan_revision"] == compute_plan_revision(first)
    assert first["delivery_contract"]["shot_count"] == 2
    assert first["delivery_contract"]["total_duration_seconds"] == 9.0
    assert first["chapter_plan"]["chapter_count"] == 1
    assert first["chapter_plan"]["chapters"][0]["clip_ids"] == [
        "clip-001",
        "clip-002",
    ]
    assert first["asset_plan"]["required_count"] == 1
    assert first["asset_plan"]["assets"][0]["asset_id"] == "character:阿木"
    assert first["prompt_package"]["item_count"] == 2
    assert first["estimate"] == {
        "schema": "village_production_estimate.v1",
        "image_task_count": 2,
        "video_task_count": 2,
        "compose_task_count": 1,
        "paid_task_count": 4,
        "asset_reference_candidate_count": 4,
        "cost_status": "unverified",
        "amount": None,
        "currency": "",
    }
    assert [
        stage["stage_id"] for stage in first["production_handoff"]["stages"]
    ] == ["storyboard_images", "shot_videos", "final_film"]
    assert first["production_handoff"]["stages"][1]["audio_mode"] == (
        "original_video_track"
    )
    encoded = json.dumps(first, ensure_ascii=False)
    assert encoded.count("暗房红灯中，阿木举起相机") == 1


def test_plan_binding_rejects_stale_asset_or_model_facts() -> None:
    run = _run_with_script()
    plan = build_production_plan(run)

    run["artifacts"]["script_contract"]["asset_ledger"]["signature"] = "c" * 64
    with pytest.raises(WorkflowStepExecutionError) as stale_asset:
        validate_production_plan_binding(run, plan=plan)
    assert stale_asset.value.code == "workflow_production_plan_stale"
    assert stale_asset.value.details["media_submission_started"] is False

    run = _run_with_script()
    plan = build_production_plan(run)
    run["model_plan_snapshot"]["model_plan_revision"] = "direct-model-plan.v3"
    with pytest.raises(WorkflowStepExecutionError) as stale_model:
        validate_production_plan_binding(run, plan=plan)
    assert stale_model.value.code == "workflow_production_plan_stale"
    assert "model_plan_revision" in stale_model.value.details["changed_fields"]


def test_plan_rejects_director_shot_count_mismatch_before_media() -> None:
    run = _run_with_script()
    run["inputs"]["director_intent_contract"]["shot_count"] = 3

    with pytest.raises(WorkflowStepExecutionError) as raised:
        build_production_plan(run)

    assert raised.value.code == "workflow_production_plan_shot_count_mismatch"
    assert raised.value.details == {
        "declared_shot_count": 3,
        "script_shot_count": 2,
        "media_submission_started": False,
    }


# --- requested total duration -------------------------------------------------


def test_plan_blocks_when_the_script_cannot_fill_the_requested_duration() -> None:
    """The measured gap: a 30 s request shipped a 2.041 s final cut.

    This chain never calls ``_normalize_plan_duration``, so unlike
    ``one-click-film`` the plan's own total is what ships.  Measured before this
    guard: the 2.04 s artifact below passed every gate.
    """

    run = _run_with_script()
    run["inputs"]["request"] = "30秒极限打斗，带高燃台词"
    run["artifacts"]["script_contract"]["rows"][0]["duration"] = 2
    run["artifacts"]["script_contract"]["rows"][1]["duration"] = 2

    with pytest.raises(WorkflowStepExecutionError) as raised:
        build_production_plan(run)

    assert raised.value.code == "workflow_production_plan_duration_mismatch"
    assert raised.value.details["media_submission_started"] is False
    assert raised.value.details["requested_seconds"] == 30.0
    assert raised.value.details["total_seconds"] == 4.0
    assert raised.value.details["tolerance_seconds"] == 1.0


def test_plan_records_the_requested_duration_when_it_matches() -> None:
    run = _run_with_script()
    run["inputs"]["request"] = "拍一个9秒的短片"

    plan = build_production_plan(run)

    assert plan["delivery_contract"]["total_duration_seconds"] == 9.0
    assert plan["delivery_contract"]["requested_duration_seconds"] == 9.0


def test_plan_does_not_judge_a_request_that_names_no_duration() -> None:
    """Not judged is not the same as passed; it must simply not fail."""

    run = _run_with_script()
    run["inputs"]["request"] = "制作一个暗房短片"

    plan = build_production_plan(run)

    assert plan["delivery_contract"]["requested_duration_seconds"] is None
    # The fixture is 9.0 s: a named 30 s target would fail, an unnamed one must not.
    assert plan["delivery_contract"]["total_duration_seconds"] == 9.0


def test_storyboard_gate_rejects_stale_plan_before_provider_dispatch() -> None:
    run = _run_with_script()
    plan = build_production_plan(run)
    run["artifacts"]["production_plan"] = plan
    run["artifacts"]["script_contract"]["rows"][0]["shot_prompt"] = (
        "脚本在计划后发生变化，旧计划不得继续开拍。"
    )

    with pytest.raises(WorkflowStepExecutionError) as raised:
        _script_rows(run)

    assert raised.value.code == "workflow_production_plan_stale"
    assert raised.value.details["media_submission_started"] is False
