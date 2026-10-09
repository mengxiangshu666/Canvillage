import json

from novelvideo.chat.context_checkpoint import (
    CHECKPOINT_SCHEMA,
    build_checkpoint,
    checkpoint_prompt,
    delete_checkpoints,
    load_checkpoint,
    save_checkpoint,
    workflow_run_recovery_checkpoint,
)


def test_checkpoint_round_trip_is_project_scoped_and_bounded(tmp_path) -> None:
    checkpoint = build_checkpoint(
        state_dir=tmp_path,
        project_id="project-a",
        canvas_id="canvas-a",
        turn_id="turn-a",
        logical_session_id="project:project-a:canvas-a",
        goal="做一个片子，api_key=secret-value，不要把这个凭据保存下来",
        completed_steps=["读取画布", "读取画布", "x" * 900],
        pending_steps=["继续执行"],
        active_errors=["上游失败"],
        canvas={
            "revision": 7,
            "node_count": 3,
            "edge_count": 2,
            "selected_node_ids": ["shot-b"],
            "pinned_node_ids": ["portrait", "scene"],
            "reference_candidate_node_ids": ["portrait", "scene"],
        },
        workflow={"run_id": "run-a", "status": "running", "current_step": "step-2"},
        provider_task_ids=["provider-task-a"],
        model_bindings={
            "agent_registry_id": "direct/agent-a",
            "context_length": 1_048_576,
            "max_output_tokens": 16_384,
            "context_source": "metadata:inputTokenLimit",
        },
    )

    path = save_checkpoint(tmp_path, checkpoint)
    loaded = load_checkpoint(tmp_path, "turn-a")

    assert path.exists()
    assert loaded is not None
    assert loaded["schema"] == CHECKPOINT_SCHEMA
    assert loaded["scope"] == {"project_id": "project-a", "canvas_id": "canvas-a"}
    assert loaded["provider_task_ids"] == ["provider-task-a"]
    assert loaded["recovery_contract"]["action"] == "reconcile_provider_tasks"
    assert loaded["recovery_contract"]["allow_new_submission"] is False
    assert loaded["completed_steps"] == ["读取画布", "x" * 320]
    assert loaded["canvas"]["selected_node_ids"] == ["shot-b"]
    assert loaded["canvas"]["pinned_node_ids"] == ["portrait", "scene"]
    assert loaded["canvas"]["reference_candidate_node_ids"] == ["portrait", "scene"]
    assert "secret-value" not in path.read_text(encoding="utf-8")
    assert load_checkpoint(tmp_path, "turn-b") is None


def test_checkpoint_prompt_excludes_storage_path_and_keeps_recovery_contract(tmp_path) -> None:
    checkpoint = build_checkpoint(
        state_dir=tmp_path,
        project_id="project-a",
        canvas_id="canvas-a",
        turn_id="turn-a",
        logical_session_id="session-a",
        goal="完成当前任务",
    )

    rendered = checkpoint_prompt(checkpoint)

    assert rendered.startswith("[VILLAGE_AGENT_CONTEXT_CHECKPOINT]")
    assert rendered.endswith("不要重复已有 command_id，也不要伪造执行结果。")
    assert str(tmp_path) not in rendered
    payload = json.loads(
        rendered.splitlines()[1]
    )
    assert payload["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert payload["provider_task_ids"] == []
    assert payload["recovery_contract"]["action"] == "inspect_before_action"


def test_checkpoint_keeps_bounded_workflow_frontier(tmp_path) -> None:
    checkpoint = build_checkpoint(
        state_dir=tmp_path,
        project_id="project-a",
        canvas_id="canvas-a",
        turn_id="turn-a",
        logical_session_id="session-a",
        goal="继续工作流",
        pending_steps=["media_generation"],
        workflow={
            "id": "wfr-a",
            "workflow_id": "final_film",
            "status": "waiting_external",
            "revision": 9,
            "current_frontier": ["media_generation"],
            "step_states": {
                "media_generation": {
                    "status": "waiting",
                    "handler": "canvas.run_generation_nodes",
                    "recovery_mode": "reconcile",
                    "depends_on": ["structure"],
                    "attempt": 2,
                }
            },
        },
    )

    assert checkpoint["workflow"]["run_id"] == "wfr-a"
    assert checkpoint["workflow"]["current_frontier"] == ["media_generation"]
    assert checkpoint["workflow"]["step_states"]["media_generation"]["recovery_mode"] == "reconcile"
    assert checkpoint["recovery_contract"]["action"] == "resume_workflow_run"


def test_checkpoint_preserves_asset_recovery_targets(tmp_path) -> None:
    checkpoint = build_checkpoint(
        state_dir=tmp_path,
        project_id="project-a",
        canvas_id="canvas-a",
        turn_id="turn-a",
        logical_session_id="session-a",
        goal="修复画布资产绑定",
        workflow={
            "id": "wfr-asset-recovery",
            "status": "failed",
            "artifacts": {
                "storyboard_images": {
                    "status": "failed",
                    "recovery": {
                        "schema": "workflow_step_recovery.v1",
                        "action": "repair_canvas_asset_binding",
                        "error_code": (
                            "workflow_storyboard_canvas_asset_ambiguous"
                        ),
                        "instruction": "保留唯一资产节点后重试。",
                        "next_action": (
                            "recover:repair_canvas_asset_binding:"
                            "storyboard_images"
                        ),
                        "auto_retry_allowed": False,
                        "requires_paid_media": False,
                        "target_node_ids": ["asset-a", "asset-b"],
                        "asset_ids": ["scene:darkroom"],
                    },
                }
            },
        },
    )

    recovery = checkpoint["workflow"]["recovery"]
    assert recovery["target_node_ids"] == ["asset-a", "asset-b"]
    assert recovery["asset_ids"] == ["scene:darkroom"]
    assert checkpoint["recovery_contract"]["action"] == "inspect_before_action"
    assert checkpoint["recovery_contract"]["allow_new_submission"] is False


def test_checkpoint_persists_bounded_orchestration_identity(tmp_path) -> None:
    checkpoint = build_checkpoint(
        state_dir=tmp_path,
        project_id="project-a",
        canvas_id="canvas-a",
        turn_id="turn-a",
        logical_session_id="session-a",
        goal="继续当前导演任务",
        orchestration={
            "schema": "agent_expert_plan.v1",
            "plan_revision": "plan-a",
            "context_revision": "context-a",
            "intent": "不要把大段提示词写入 checkpoint",
            "agent_fleet": {
                "schema": "agent_fleet.v1",
                "fleet_revision": "fleet-a",
                "mode": "dynamic",
                "selected_agents": [
                    {"agent_id": "director"},
                    {"agent_id": "prompt_compiler"},
                ],
                "dispatch_groups": [["director"], ["prompt_compiler"]],
                "ignored_payload": "x" * 5000,
            },
            "runtime_allowlist": {
                "allowlist_revision": "allow-a",
                "mode": "observe",
                "execution_enabled": False,
                "active_capabilities": ["context.shared_snapshot"],
            },
        },
    )

    orchestration = checkpoint["orchestration"]
    assert orchestration["plan_revision"] == "plan-a"
    assert orchestration["agent_fleet"]["fleet_revision"] == "fleet-a"
    assert orchestration["agent_fleet"]["selected_agent_ids"] == [
        "director",
        "prompt_compiler",
    ]
    assert orchestration["runtime_allowlist"] == {
        "allowlist_revision": "allow-a",
        "mode": "observe",
        "execution_enabled": False,
    }
    assert "ignored_payload" not in orchestration

    rendered = checkpoint_prompt(checkpoint)
    assert '"orchestration"' in rendered
    assert "不要把大段提示词写入 checkpoint" not in rendered


def test_workflow_run_recovery_checkpoint_rebuilds_exact_failed_items() -> None:
    failed_item_id = "shot_videos:shot:1:2b92ab968fac"
    checkpoint = workflow_run_recovery_checkpoint(
        {
            "id": "wfr-failed",
            "project_id": "project-a",
            "canvas_id": "canvas-a",
            "status": "failed",
            "step_states": {
                "shot_videos": {
                    "status": "failed",
                    "error_code": "workflow_shot_video_failed",
                }
            },
            "artifacts": {
                "shot_videos": {
                    "status": "failed",
                    "error_code": "workflow_shot_video_failed",
                    "failed_items": [
                        {"item_id": failed_item_id, "job_id": "job-video-1"}
                    ],
                }
            },
        },
        project_id="project-a",
        canvas_id="canvas-a",
        requested_run_id="wfr-failed",
    )

    contract = checkpoint["recovery_contract"]
    assert contract["action"] == "inspect_before_action"
    assert contract["allow_new_submission"] is False
    assert contract["workflow_run_id"] == "wfr-failed"
    assert contract["recovery"] == {
        "schema": "workflow_step_recovery.v1",
        "workflow_run_id": "wfr-failed",
        "step_id": "shot_videos",
        "error_code": "workflow_shot_video_failed",
        "action": "retry_failed_items",
        "next_action": "recover:retry_failed_items:shot_videos",
        "rerun_scope": "failed_items_only",
        "item_ids": [failed_item_id],
        "job_ids": ["job-video-1"],
        "requires_paid_media": True,
        "auto_retry_allowed": False,
        "instruction": "只重试失败视频 item；已完成视频、首帧哈希和任务身份必须保持不变。",
    }
    assert checkpoint["workflow"]["run_id"] == "wfr-failed"


def test_workflow_run_recovery_checkpoint_fails_closed_for_wrong_scope() -> None:
    checkpoint = workflow_run_recovery_checkpoint(
        {
            "id": "wfr-other",
            "project_id": "project-other",
            "canvas_id": "canvas-other",
            "status": "failed",
        },
        project_id="project-a",
        canvas_id="canvas-a",
        requested_run_id="wfr-other",
    )

    assert checkpoint["recovery_contract"]["action"] == "inspect_before_action"
    assert checkpoint["recovery_contract"]["allow_new_submission"] is False
    assert "recovery" not in checkpoint["recovery_contract"]


def test_checkpoint_persists_bounded_workflow_stage_receipts(tmp_path) -> None:
    checkpoint = build_checkpoint(
        state_dir=tmp_path,
        project_id="project-a",
        canvas_id="canvas-a",
        turn_id="turn-a",
        logical_session_id="session-a",
        goal="继续消费已完成阶段",
        workflow={
            "id": "wfr-stage-receipts",
            "status": "completed",
            "artifacts": {
                "script_contract": {
                    "schema": "workflow_freezone_script_artifact.v1",
                    "kind": "freezone_script_contract",
                    "status": "completed",
                    "workflow_step_id": "script_contract",
                    "task_id": "script-task",
                    "job_id": "script-job",
                    "rows": [{"shot_prompt": "不要写入 checkpoint 的提示词"}],
                    "contract_report": {
                        "blocking_count": 0,
                        "issue_count": 0,
                        "rows_fingerprint": "a" * 64,
                    },
                    "result_signature": "b" * 64,
                    "output_path": "C:/private/script.json",
                    "url": "https://provider.invalid/signed?token=secret",
                },
                "storyboard_images": {
                    "schema": "workflow_storyboard_images_artifact.v1",
                    "kind": "freezone_storyboard_images",
                    "status": "completed",
                    "workflow_step_id": "storyboard_images",
                    "shot_count": 1,
                    "completed_count": 1,
                    "source_script": {"result_signature": "b" * 64},
                    "images": [{"url": "/static/private/shot.png", "sha256": "c" * 64}],
                    "result_signature": "d" * 64,
                },
            },
        },
    )

    assert [
        item["step_id"] for item in checkpoint["workflow"]["stage_receipts"]
    ] == ["script_contract", "storyboard_images"]
    rendered = checkpoint_prompt(checkpoint)
    assert '"stage_receipts"' in rendered
    assert "不要写入 checkpoint 的提示词" not in rendered
    assert "C:/private" not in rendered
    assert "provider.invalid" not in rendered


def test_delete_checkpoints_removes_only_exact_turns_and_latest_pointer(tmp_path) -> None:
    first = build_checkpoint(
        state_dir=tmp_path,
        project_id="project-a",
        canvas_id="canvas-a",
        turn_id="turn-a",
        logical_session_id="session-a",
        goal="第一轮",
    )
    second = build_checkpoint(
        state_dir=tmp_path,
        project_id="project-a",
        canvas_id="canvas-a",
        turn_id="turn-b",
        logical_session_id="session-b",
        goal="第二轮",
    )
    save_checkpoint(tmp_path, first)
    save_checkpoint(tmp_path, second)

    assert delete_checkpoints(tmp_path, ["turn-a"]) == 1
    assert load_checkpoint(tmp_path, "turn-a") is None
    assert load_checkpoint(tmp_path, "turn-b") is not None
    assert (tmp_path / "agent_checkpoints" / "latest.json").exists()

    assert delete_checkpoints(tmp_path, ["turn-b"]) == 2
    assert load_checkpoint(tmp_path, "turn-b") is None
    assert not (tmp_path / "agent_checkpoints" / "latest.json").exists()
