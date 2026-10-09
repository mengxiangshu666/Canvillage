from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.chat import village_turn_policy
from novelvideo.chat.agent_events import build_agent_event
from novelvideo.chat.context_checkpoint import build_checkpoint, checkpoint_prompt
from novelvideo.workflow_runtime.canvas_recovery import (
    build_canvas_command_recovery,
    recovery_next_action,
)
from novelvideo.workflow_runtime.step_recovery import build_workflow_step_recovery


@pytest.mark.parametrize(
    ("details", "action", "requires_paid_media"),
    [
        (
            {"reason_code": "script_media_contract_blocking"},
            "repair_script_contract",
            False,
        ),
        (
            {"reason_code": "script_media_prompt_missing"},
            "repair_script_prompt",
            False,
        ),
        (
            {"reason_code": "script_media_shot_identity_missing"},
            "rebuild_shot_binding",
            False,
        ),
        (
            {
                "reason_code": "script_media_shot_row_stale",
                "stale_reason": "row-changed",
                "shot_id": "shot-1",
            },
            "regenerate_storyboard",
            True,
        ),
        (
            {
                "reason_code": "script_media_shot_image_missing",
                "stale_reason": "storyboard-missing",
                "shot_id": "shot-1",
            },
            "generate_storyboard",
            True,
        ),
        (
            {
                "reason_code": "script_media_shot_image_stale",
                "stale_reason": "first-frame-changed",
                "shot_id": "shot-1",
            },
            "requeue_shot_video",
            True,
        ),
        (
            {
                "reason_code": "script_media_shot_image_stale",
                "stale_reason": "reference-changed",
                "shot_id": "shot-1",
            },
            "regenerate_storyboard",
            True,
        ),
    ],
)
def test_script_media_refusal_maps_to_retry_blocking_recovery(
    details: dict[str, str],
    action: str,
    requires_paid_media: bool,
) -> None:
    recovery = build_canvas_command_recovery(
        error_code="canvas_script_media_not_ready",
        details=details,
        step_id="media_generation",
    )

    assert recovery is not None
    assert recovery["schema"] == "canvas_command_recovery.v1"
    assert recovery["action"] == action
    assert recovery["next_action"] == f"recover:{action}:media_generation"
    assert recovery["auto_retry_allowed"] is False
    assert recovery["requires_paid_media"] is requires_paid_media
    assert recovery["step_id"] == "media_generation"


def test_unknown_canvas_failure_does_not_invent_recovery() -> None:
    assert build_canvas_command_recovery(
        error_code="canvas_revision_conflict",
        details={"reason_code": "unrelated"},
        step_id="media_generation",
    ) is None


def test_recovery_survives_workflow_checkpoint_and_agent_prompt(
    tmp_path: Path,
) -> None:
    recovery = build_canvas_command_recovery(
        error_code="canvas_script_media_not_ready",
        details={
            "reason_code": "script_media_shot_image_stale",
            "stale_reason": "reference-changed",
            "action": "shot-videos",
            "shot_id": "shot-1",
            "asset_id": "character:阿雀",
        },
        step_id="media_generation",
    )
    assert recovery is not None
    run = {
        "id": "wfr-1",
        "workflow_id": "one-click-film",
        "status": "failed",
        "runtime_phase": "recoverable_error",
        "error_code": "canvas_script_media_not_ready",
        "current_frontier": ["media_generation"],
        "step_states": {
            "media_generation": {
                "status": "failed",
                "handler": "canvas.run_generation_nodes",
                "error_code": "canvas_script_media_not_ready",
            }
        },
        "artifacts": {
            "media_generation": {
                "status": "receipt_failed",
                "canvas_receipt": {
                    "error_code": "canvas_script_media_not_ready",
                    "recovery": recovery,
                },
            }
        },
        "next_action": recovery["next_action"],
    }

    checkpoint = build_checkpoint(
        state_dir=tmp_path,
        project_id="project-1",
        canvas_id="canvas-1",
        turn_id="turn-1",
        logical_session_id="session-1",
        goal="修复过期分镜",
        workflow=run,
        next_action=run["next_action"],
    )

    assert checkpoint["workflow"]["recovery"]["action"] == "regenerate_storyboard"
    assert checkpoint["workflow"]["recovery"]["media_action"] == "shot-videos"
    assert checkpoint["recovery_contract"]["action"] == "inspect_before_action"
    assert checkpoint["recovery_contract"]["allow_new_submission"] is False
    prompt = checkpoint_prompt(checkpoint)
    assert "regenerate_storyboard" in prompt
    assert "reference-changed" in prompt


def test_agent_event_projection_preserves_canvas_recovery() -> None:
    recovery = build_canvas_command_recovery(
        error_code="canvas_script_media_not_ready",
        details={
            "reason_code": "script_media_shot_image_stale",
            "stale_reason": "first-frame-changed",
            "shot_id": "shot-1",
        },
        step_id="media_generation",
    )
    assert recovery is not None
    event = build_agent_event(
        {
            "type": "canvas.patch",
            "project_id": "project-1",
            "canvas_id": "canvas-1",
            "command_id": "workflow:media:1",
            "canvas_receipt": {
                "command_id": "workflow:media:1",
                "error_code": "canvas_script_media_not_ready",
                "recovery": recovery,
            },
        },
        seq=1,
    )

    assert event is not None
    assert event["payload"]["canvas_receipt"]["recovery"] == recovery
    assert recovery_next_action(
        {"canvas_receipt": {"recovery": recovery}}
    ) == "recover:requeue_shot_video:media_generation"


def test_hermes_guard_consumes_real_checkpoint_recovery(
    tmp_path: Path,
) -> None:
    recovery = build_canvas_command_recovery(
        error_code="canvas_script_media_not_ready",
        details={
            "reason_code": "script_media_shot_image_stale",
            "stale_reason": "reference-changed",
            "action": "shot-videos",
            "target_node_id": "video-1",
            "shot_id": "shot-1",
        },
        step_id="media_generation",
    )
    assert recovery is not None
    checkpoint = build_checkpoint(
        state_dir=tmp_path,
        project_id="project-1",
        canvas_id="canvas-1",
        turn_id="turn-t067",
        logical_session_id="session-t067",
        goal="修复 stale 分镜后再出视频",
        workflow={
            "id": "wfr-t067",
            "status": "failed",
            "current_frontier": ["media_generation"],
            "next_action": recovery["next_action"],
            "artifacts": {
                "media_generation": {
                    "canvas_receipt": {
                        "error_code": "canvas_script_media_not_ready",
                        "recovery": recovery,
                    }
                }
            },
        },
    )
    contract = village_turn_policy.recovery_contract_from_prompt(
        checkpoint_prompt(checkpoint)
    )
    assert contract is not None
    assert contract.action == "inspect_before_action"
    assert contract.allow_new_submission is False
    assert contract.recovery_action == "regenerate_storyboard"
    assert contract.requires_paid_media is True
    assert contract.step_id == "media_generation"
    assert contract.media_action == "shot-videos"
    assert contract.target_node_id == "video-1"

    workflow = village_turn_policy.DirectorWorkflowRun(recovery_contract=contract)
    assert workflow.start_tool("village_canvas_get_workflow_run") is None
    assert workflow.start_tool("village_canvas_start_single_video") == (
        village_turn_policy.VILLAGE_CANVAS_RECOVERY_INSPECT_MESSAGE
    )


@pytest.mark.parametrize("transport", ["direct", "indexed"])
def test_hermes_guard_accepts_exact_failed_item_workflow_retry(
    tmp_path: Path,
    transport: str,
) -> None:
    recovery = build_workflow_step_recovery(
        error_code="workflow_shot_video_failed",
        details={
            "failed_items": [
                {"node_id": "shot-2", "job_id": "job-shot-2"},
                {"node_id": "shot-3", "job_id": "job-shot-3"},
            ]
        },
        step_id="shot_videos",
        run_id="wfr-t115",
    )
    assert recovery is not None
    checkpoint = build_checkpoint(
        state_dir=tmp_path,
        project_id="project-1",
        canvas_id="canvas-1",
        turn_id="turn-t115",
        logical_session_id="session-t115",
        goal="只重试失败视频",
        workflow={
            "id": "wfr-t115",
            "status": "failed",
            "next_action": recovery["next_action"],
            "artifacts": {"shot_videos": {"recovery": recovery}},
        },
    )
    contract = village_turn_policy.recovery_contract_from_prompt(
        checkpoint_prompt(checkpoint)
    )
    assert contract is not None
    assert contract.recovery_action == "retry_failed_items"
    assert contract.step_id == "shot_videos"
    assert contract.item_ids == ("shot-2", "shot-3")

    director = village_turn_policy.DirectorWorkflowRun(recovery_contract=contract)

    def retry_event(*, run_id: str, item_ids: list[str]) -> tuple[str, dict]:
        arguments = {
            "run_id": run_id,
            "command": "retry",
            "step_id": "shot_videos",
            "retry_scope": "failed_items_only",
            "item_ids": item_ids,
            "idempotency_key": "t115-exact-item-retry",
        }
        if transport == "direct":
            return "village_canvas_command_workflow_run", {"rawInput": arguments}
        return (
            "village_canvas_capability",
            {
                "rawInput": {
                    "action": "invoke",
                    "capability_id": "workflow.run.control",
                    "arguments": arguments,
                }
            },
        )

    tool_name, wrong_run = retry_event(run_id="wfr-other", item_ids=["shot-2"])
    assert director.start_tool(
        tool_name,
        wrong_run,
    ) == village_turn_policy.VILLAGE_CANVAS_RECOVERY_INSPECT_MESSAGE
    tool_name, wrong_item = retry_event(run_id="wfr-t115", item_ids=["shot-9"])
    assert director.start_tool(
        tool_name,
        wrong_item,
    ) == village_turn_policy.VILLAGE_CANVAS_RECOVERY_INSPECT_MESSAGE
    tool_name, exact_retry = retry_event(run_id="wfr-t115", item_ids=["shot-2"])
    assert director.start_tool(tool_name, exact_retry) is None
    assert director.payload()["recovery"]["retry_consumed"] is True
