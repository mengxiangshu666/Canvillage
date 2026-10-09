from __future__ import annotations

import pytest

from novelvideo.chat.recovery_policy import workflow_run_receipt_from_update
from novelvideo.workflow_runtime.step_recovery import (
    WORKFLOW_STEP_RECOVERY_SCHEMA,
    build_workflow_step_recovery,
)


@pytest.mark.parametrize(
    (
        "error_code",
        "step_id",
        "action",
        "rerun_scope",
        "requires_paid_media",
    ),
    [
        (
            "workflow_script_contract_blocked",
            "script_contract",
            "repair_script_contract",
            "script_contract",
            False,
        ),
        (
            "workflow_script_task_failed",
            "script_contract",
            "retry_script_contract",
            "script_contract",
            False,
        ),
        (
            "workflow_storyboard_paid_media_not_authorized",
            "storyboard_images",
            "request_media_authorization",
            "current_step",
            True,
        ),
        (
            "workflow_shot_video_paid_media_not_authorized",
            "shot_videos",
            "request_media_authorization",
            "current_step",
            True,
        ),
        (
            "workflow_storyboard_image_failed",
            "storyboard_images",
            "retry_failed_items",
            "failed_items_only",
            True,
        ),
        (
            "workflow_storyboard_canvas_snapshot_invalid",
            "storyboard_images",
            "repair_canvas_snapshot",
            "canvas_snapshot",
            False,
        ),
        (
            "workflow_storyboard_canvas_asset_ambiguous",
            "storyboard_images",
            "repair_canvas_asset_binding",
            "canvas_asset_binding",
            False,
        ),
        (
            "workflow_storyboard_canvas_asset_not_ready",
            "storyboard_images",
            "repair_canvas_asset_binding",
            "canvas_asset_binding",
            False,
        ),
        (
            "workflow_storyboard_asset_required_not_ready",
            "storyboard_images",
            "repair_asset_ledger",
            "script_contract",
            False,
        ),
        (
            "workflow_storyboard_asset_identity_stale",
            "storyboard_images",
            "repair_asset_ledger",
            "script_contract",
            False,
        ),
        (
            "workflow_storyboard_asset_reference_unresolvable",
            "storyboard_images",
            "repair_canvas_asset_path",
            "canvas_asset_binding",
            False,
        ),
        (
            "workflow_storyboard_asset_reference_cap_exceeded",
            "storyboard_images",
            "reduce_asset_references",
            "canvas_asset_binding",
            False,
        ),
        (
            "workflow_storyboard_asset_reference_changed",
            "storyboard_images",
            "regenerate_storyboard",
            "current_step",
            True,
        ),
        (
            "workflow_storyboard_canvas_asset_changed",
            "storyboard_images",
            "regenerate_storyboard",
            "current_step",
            True,
        ),
        (
            "workflow_shot_video_failed",
            "shot_videos",
            "retry_failed_items",
            "failed_items_only",
            True,
        ),
        (
            "workflow_production_plan_duration_mismatch",
            "production_plan",
            "rebuild_production_plan",
            "production_plan",
            False,
        ),
        (
            "workflow_production_plan_shot_count_mismatch",
            "production_plan",
            "rebuild_production_plan",
            "production_plan",
            False,
        ),
        (
            "workflow_production_plan_stale",
            "production_plan",
            "rebuild_production_plan",
            "production_plan",
            False,
        ),
        (
            "workflow_production_plan_script_not_ready",
            "production_plan",
            "repair_script_contract",
            "script_contract",
            False,
        ),
        (
            "workflow_production_plan_rows_missing",
            "production_plan",
            "repair_script_contract",
            "script_contract",
            False,
        ),
        (
            "workflow_final_film_input_invalid",
            "final_film",
            "reconcile_final_compose",
            "final_film",
            False,
        ),
        (
            "workflow_final_film_failed",
            "final_film",
            "reconcile_final_compose",
            "final_film",
            False,
        ),
        (
            "workflow_final_film_not_authorized",
            "final_film",
            "request_compose_authorization",
            "final_film",
            False,
        ),
    ],
)
def test_workflow_step_recovery_mapping(
    error_code: str,
    step_id: str,
    action: str,
    rerun_scope: str,
    requires_paid_media: bool,
) -> None:
    recovery = build_workflow_step_recovery(
        error_code=error_code,
        details={},
        step_id=step_id,
        run_id="wfr-t082",
    )

    assert recovery is not None
    assert recovery["schema"] == WORKFLOW_STEP_RECOVERY_SCHEMA
    assert recovery["workflow_run_id"] == "wfr-t082"
    assert recovery["step_id"] == step_id
    assert recovery["error_code"] == error_code
    assert recovery["action"] == action
    assert recovery["next_action"] == f"recover:{action}:{step_id}"
    assert recovery["rerun_scope"] == rerun_scope
    assert recovery["requires_paid_media"] is requires_paid_media
    assert recovery["auto_retry_allowed"] is False
    assert recovery["instruction"]
    if error_code == "workflow_final_film_not_authorized":
        assert recovery["authorization_request"]["schema"] == (
            "workflow_compose_authorization_request.v1"
        )
        assert recovery["authorization_request"]["run_id"] == "wfr-t082"
        assert recovery["authorization_request"]["step_id"] == "final_film"
        assert recovery["authorization_request"]["requires_user_action"] is True


def test_failed_item_contract_uses_retry_state_key_not_provider_job_id() -> None:
    recovery = build_workflow_step_recovery(
        error_code="workflow_storyboard_image_failed",
        details={
            "jobs": [
                {"node_id": "storyboard_images:1", "job_id": "provider-job-1"},
                {"node_id": "storyboard_images:2", "job_id": "provider-job-2"},
            ],
            "failed_items": [
                {
                    "shot_no": "2",
                    "job_id": "provider-job-2",
                    "task_id": "task-2",
                }
            ],
        },
        step_id="storyboard_images",
        run_id="wfr-t082",
    )

    assert recovery is not None
    assert recovery["item_ids"] == ["storyboard_images:2"]
    assert recovery["job_ids"] == ["provider-job-2"]


def test_asset_recovery_contract_exposes_bounded_repair_targets() -> None:
    recovery = build_workflow_step_recovery(
        error_code="workflow_storyboard_canvas_asset_ambiguous",
        details={
            "canvas_asset_issues": [
                {
                    "asset_id": "scene:darkroom",
                    "node_ids": ["asset-a", "asset-b"],
                },
                {
                    "asset_id": "scene:darkroom",
                    "node_ids": ["asset-b", "asset-c"],
                },
            ],
            "asset_blockers": [
                {
                    "asset_id": "character:amu",
                    "node_id": "character-node",
                }
            ],
        },
        step_id="storyboard_images",
        run_id="wfr-t091",
    )

    assert recovery is not None
    assert recovery["target_node_ids"] == [
        "asset-a",
        "asset-b",
        "asset-c",
        "character-node",
    ]
    assert recovery["asset_ids"] == ["scene:darkroom", "character:amu"]
    assert recovery["auto_retry_allowed"] is False
    assert recovery["requires_paid_media"] is False


def test_agent_receipt_preserves_asset_recovery_targets() -> None:
    recovery = build_workflow_step_recovery(
        error_code="workflow_storyboard_canvas_asset_ambiguous",
        details={
            "canvas_asset_issues": [
                {
                    "asset_id": "scene:darkroom",
                    "node_ids": ["asset-a", "asset-b"],
                }
            ]
        },
        step_id="storyboard_images",
        run_id="wfr-t091",
    )
    assert recovery is not None

    receipt = workflow_run_receipt_from_update(
        {
            "id": "wfr-t091",
            "status": "failed",
            "step_states": {
                "storyboard_images": {
                    "status": "failed",
                    "error_code": "workflow_storyboard_canvas_asset_ambiguous",
                }
            },
            "artifacts": {
                "storyboard_images": {
                    "status": "failed",
                    "recovery": recovery,
                }
            },
        },
        expected_run_id="wfr-t091",
    )

    assert receipt is not None
    assert receipt.recovery["action"] == "repair_canvas_asset_binding"
    assert receipt.recovery["target_node_ids"] == ["asset-a", "asset-b"]
    assert receipt.recovery["asset_ids"] == ["scene:darkroom"]


def test_unknown_failure_does_not_invent_recovery_action() -> None:
    assert (
        build_workflow_step_recovery(
            error_code="workflow_unknown_failure",
            details={},
            step_id="storyboard_images",
            run_id="wfr-t082",
        )
        is None
    )
