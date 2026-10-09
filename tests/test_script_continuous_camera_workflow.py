from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.freezone import canvas_store
from novelvideo.freezone.canvas_command_gateway import CanvasCommandError, CanvasCommandGateway, _node_parameters
from novelvideo.production.continuity_contract import compile_storyboard_continuity
from novelvideo.production.shot_contract import build_shot_contract, validate_shot_contract
from novelvideo.project_context import ProjectContext
from novelvideo.services.freezone_content import get_video_camera_template
from novelvideo.workflow_runtime import executor, media_dispatch
from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.workflow_runtime.store import WorkflowRunStore
from novelvideo.workflow_runtime.verifier import verify_canvas_command
from tests.test_workflow_runtime import _fake_model_plan_snapshot


def _shot(camera: str = "") -> dict:
    return {
        "shot_id": "S01", "title": "等门打开", "duration_seconds": 8,
        "subject": "女孩", "action": "女孩听见门后脚步，停留观察，门开后才向门内迈步",
        "prompt": "女孩听见脚步后等待门开，看到门内的人再迈步",
        "camera_position": "走廊右侧中景，门内起初被门扇遮住", "camera_motion": camera,
        "first_frame": "女孩停在门前，门内尚不可见", "last_frame": "女孩仍在迈过门槛，门内的人进入构图",
        "transition": "direct_cut",
    }


@pytest.mark.parametrize("model", ["", "seedance-2.0", "star-video", "kling", "direct_h3"])
def test_shared_compiler_keeps_declared_performance_without_model_name_timing(model: str) -> None:
    shot = _shot("先固定在走廊右侧，门开后缓慢横移，人物过门槛时推近，让门内人物此时才可见")
    original = deepcopy(shot)
    result = compile_storyboard_continuity({"shots": [shot]}, model_ref=model)
    prompt = result["plan"]["shots"][0]["prompt"]
    assert result["report"]["passed"] is True
    assert shot == original
    assert shot["action"] in prompt and shot["camera_motion"] in prompt
    assert shot["last_frame"] in prompt
    assert "本镜连续表演与主体变化" in prompt
    assert "不强制动作或运镜数量" in prompt
    assert "切点可以仍在运动中" in prompt
    assert not any(text in prompt for text in ("唯一主要动作", "不新增第二个主动作", "动作在本镜内完成"))
    reference = compile_storyboard_continuity({"shots": [shot]}, model_ref="")["plan"]["shots"][0]["prompt"]
    assert prompt == reference


def test_missing_camera_draft_stays_fixed_and_declares_inference() -> None:
    plan = executor.StoryboardPlan.model_validate({"title": "等待", "creative_direction": "保留停留", "shots": [_shot()]})
    normalized = executor._normalize_shot_contracts(plan, None)
    assert normalized.shots[0].camera_motion == "固定观察，保持本镜机位和构图"
    assert "camera_motion" in normalized.shots[0].inferred_defaults


@pytest.mark.parametrize("command,code", [
    ({"camera_movement": "未登记的模板"}, "canvas_camera_option_unsupported"),
    ({"camera_movement": "follow_tracking", "clear_camera": True}, "canvas_camera_ambiguous"),
])
def test_illegal_manual_camera_template_is_still_rejected(command: dict, code: str) -> None:
    with pytest.raises(CanvasCommandError) as raised:
        _node_parameters(command, "videoNode")
    assert raised.value.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize("reuse", [False, True], ids=["create", "reuse"])
@pytest.mark.parametrize("camera", [
    "先固定在走廊右侧，门开后缓慢横移，人物过门槛时推近，让门内人物此时才可见，结束保留门槛与两人关系",
    "固定观察，摄影机和取景保持稳定；门开后仍不移动，让人物沿既有空间进入构图",
    "follow_tracking",
    "dolly-in",
    "",
], ids=["compound", "fixed", "preset", "preset-alias", "missing-rejected"])
async def test_workflow_camera_compiler_gateway_persistence_and_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, camera: str, reuse: bool,
) -> None:
    import pydantic_ai
    from novelvideo.generators import direct_models

    for folder in ("state", "output", "runtime"):
        (tmp_path / folder).mkdir()
    ctx = ProjectContext(
        project_id="camera-test", project_name="camera-test", owner_type="user", owner_id="local",
        owner_username="local", requester_user_id="local", requester_username="local",
        requester_principals=(("user", "local"),), effective_role="owner", home_node_id="local",
        output_dir=tmp_path / "output", state_dir=tmp_path / "state", runtime_dir=tmp_path / "runtime", is_home_node=True,
    )
    captured = {}
    shot = _shot(camera)
    shot["prompt"] += "；蓝绿墙面保持哑光，门内暖光只在开门后露出"

    class Agent:
        def __init__(self, *_args, **kwargs):
            captured["system_prompt"] = kwargs["system_prompt"]

        async def run(self, _request):
            return SimpleNamespace(output=json.dumps({"title": "等门打开", "creative_direction": "延迟揭示门内的人", "shots": [shot]}, ensure_ascii=False))

    snapshot = _fake_model_plan_snapshot()
    snapshot["bindings"]["video"] = {
        "kind": "video", "registry_id": "video-test",
        "capabilities": {"supported_modes": ["textToVideo"], "aspect_ratio_options": ["16:9"],
                         "resolution_options": ["720p"], "duration_options": [8], "native_audio": "optional"},
    }
    monkeypatch.setattr(pydantic_ai, "Agent", Agent)
    monkeypatch.setattr(direct_models, "get_direct_pydantic_model", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(executor, "resolve_snapshot_model_ref", lambda _snapshot, role: ("video", "legacy/video-test") if role == "video" else ("agent", "direct/agent-test"))
    store = WorkflowRunStore(ctx.state_dir)
    run, reused = await store.create(
        definition=get_workflow_definition("one-click-film"), project_id=ctx.project_id, canvas_id="camera-canvas", run_mode="draft",
        inputs={"request": "做视频草稿：女孩等待门开再迈步", "director_intent_contract": {"delivery_level": "media_draft"},
                **({"target_strategy": "reuse_existing", "target_node_ids": ["camera-existing"]} if reuse else {})},
        idempotency_key="camera-test", contract_version=2, model_plan_snapshot=snapshot,
    )
    assert not reused
    run, _ = await store.record_event(run["id"], event_id="start-canvas", event_type="step_completed", step_id="canvas_structure", success=True, payload={"started": False, "reason": "No starter graph requested"}, expected_revision=run["revision"])
    gateway = CanvasCommandGateway(project_dir=ctx.state_dir, project_id=ctx.project_id, actor_id="test")
    before = None
    if reuse:
        old_shot = _shot("follow_tracking")
        gateway.apply(canvas_id=run["canvas_id"], envelope={
            "schema": "canvas_chat_commands.v1", "command_id": "seed-existing",
            "commands": [{"type": "create_video_prompt_node", "created_node_id": "camera-existing",
                          "x": 71, "y": 92, "prompt": "旧版红色金属墙面，女孩关门",
                          "camera_movement": "follow_tracking", "shot_contract": build_shot_contract(old_shot),
                          "model": "legacy/video-test", "generation_mode": "textToVideo", "duration_sec": 5},
                         {"type": "update_node_data", "node_id": "camera-existing", "node_data": {
                             "videoUrl": "/old.mp4", "generationBatchSources": {"/old.mp4": {"old": True}},
                         }}],
        })
        before = canvas_store.read_canvas(ctx.state_dir, run["canvas_id"])
        assert before["nodes"][0]["data"]["cameraMovement"] == "follow_tracking"
        assert before["nodes"][0]["data"]["shotContract"] == build_shot_contract(old_shot)
    if not camera:
        with pytest.raises(executor.WorkflowStepExecutionError) as raised:
            await executor._storyboard_handler(run, run["step_states"]["story_and_shots"])
        assert raised.value.code == "workflow_shot_contract_inferred_defaults"
        assert raised.value.details["media_submission_started"] is False
        assert canvas_store.read_canvas(ctx.state_dir, run["canvas_id"]) == before
        return
    result = await executor._storyboard_handler(run, run["step_states"]["story_and_shots"])
    run, _ = await store.record_event(run["id"], event_id="story", event_type="step_output_ready", step_id="story_and_shots", success=True, payload=result.payload, expected_revision=run["revision"])
    receipt = gateway.apply(canvas_id=run["canvas_id"], envelope=result.payload["command_envelope"])
    assert receipt["server_applied"] is True
    assert len(receipt["created_node_ids"]) == (0 if reuse else 1)
    run, _ = await store.record_event(run["id"], event_id="canvas", event_type="canvas_applied", step_id="story_and_shots", success=True, payload=receipt, expected_revision=run["revision"])
    canvas = canvas_store.read_canvas(ctx.state_dir, run["canvas_id"])
    verification = verify_canvas_command(snapshot=canvas, envelope=receipt["normalized_envelope"], expectation=receipt["expectation"])
    assert verification["passed"] is True
    run, _ = await store.record_event(run["id"], event_id="verified", event_type="verification_passed", step_id="story_and_shots", success=True, payload=verification, expected_revision=run["revision"])
    data = canvas["nodes"][0]["data"]
    assert camera in data["prompt"] and data["shotContract"]["primary_camera_motion"] == camera
    template = get_video_camera_template(camera)
    assert data.get("cameraMovement") == (template["id"] if template else None)
    assert validate_shot_contract(data["shotContract"]) == []
    assert data["promptSource"] == shot["prompt"]
    assert data["action"] == shot["action"] and data["cameraPosition"] == shot["camera_position"]
    assert data["firstFrame"] == shot["first_frame"] and data["lastFrame"] == shot["last_frame"]
    assert data["durationSec"] == 8 and data["genMode"] == "textToVideo"
    assert "蓝绿墙面保持哑光" in data["prompt"] and "旧版红色金属墙面" not in data["prompt"]
    if reuse:
        assert len(canvas["nodes"]) == 1 and canvas["nodes"][0]["id"] == "camera-existing"
        assert canvas["nodes"][0]["position"] == before["nodes"][0]["position"]
        assert data["videoUrl"] == "/old.mp4" and data["generationBatchSources"] == {"/old.mp4": {"old": True}}
    assert "不强制动作或运镜数量" in captured["system_prompt"]
    assert "观看目的" in captured["system_prompt"] and "转向或揭示的触发" in captured["system_prompt"]
    assert "camera_motion\":\"单一运镜" not in captured["system_prompt"]
    queued = []

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            queued.append(kwargs["payload"])
            return SimpleNamespace(task_state=SimpleNamespace(status="queued", progress=0.0, task_id="camera-job"))

    async def resolve_context(_run):
        return ctx

    monkeypatch.setattr(media_dispatch, "resolve_workflow_project_context", resolve_context)
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: Backend())
    jobs = await media_dispatch.dispatch_workflow_video_batch(run, state_dir=ctx.state_dir, step_id="media_generation", node_ids=[canvas["nodes"][0]["id"]], model_ref="legacy/video-test")
    assert len(jobs) == len(queued) == 1
    assert camera in queued[0]["prompt"] and shot["action"] in queued[0]["prompt"]
    assert queued[0]["shot_contract"]["primary_camera_motion"] == camera
    assert "蓝绿墙面保持哑光" in queued[0]["prompt"]
    stored = await store.get(run["id"])
    assert stored["step_states"]["story_and_shots"]["status"] == "completed"
    assert stored["artifacts"]["story_and_shots"]["plan"]["shots"][0]["camera_motion"] == camera
    final_canvas = canvas_store.read_canvas(ctx.state_dir, run["canvas_id"])
    assert final_canvas["revision"] == canvas["revision"] + 1
    assert final_canvas["nodes"][0]["data"]["generationTaskJobId"] == jobs[0]["job_id"]
