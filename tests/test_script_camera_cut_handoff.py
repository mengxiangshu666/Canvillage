from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from novelvideo.freezone import canvas_store
from novelvideo.production.continuity_contract import (
    compile_storyboard_continuity,
    lint_storyboard_continuity,
    shot_transition,
    transition_preserves_frame,
)
from novelvideo.production.shot_contract import build_shot_contract
from novelvideo.project_context import ProjectContext
from novelvideo.services.canvas_commands import read_canvas_snapshot
from novelvideo.workflow_runtime import executor, media_dispatch


def _shots(transition: str = "direct_cut") -> list[dict]:
    return [
        {
            "shot_id": "S01", "title": "女孩离开", "duration_seconds": 5,
            "prompt": "女孩穿过门口跑出画面，冷色顶灯保持清晰", "subject": "女孩",
            "action": "女孩跑出画面", "camera_motion": "固定观察门口",
            "first_frame": "女孩站在门口", "last_frame": "门口无人，女孩已出画面",
            "continuity_out": {"state": "女孩已经出画面", "transition": transition},
            "transition": transition,
        },
        {
            "shot_id": "S02", "title": "老人等待", "duration_seconds": 5,
            "prompt": "反打老人站在门外，暖色路灯从老人右上方照亮侧脸",
            "subject": "老人", "action": "老人抬眼望向门口",
            "camera_motion": "固定观察老人", "first_frame": "门外老人位于画面左侧",
            "last_frame": "老人望向门口", "transition": "continuous_action",
        },
    ]


@pytest.mark.parametrize("transition", ["direct_cut", "scene_change", "自然切，反打老人", "匹配剪辑"])
def test_cut_uses_current_frame_after_workflow_normalization(transition: str) -> None:
    shots = _shots(transition)
    original = deepcopy(shots)
    plan = executor.StoryboardPlan.model_validate(
        {"title": "等待", "creative_direction": "由离开切到等待", "shots": shots}
    )
    normalized = executor._normalize_shot_contracts(plan, None)
    result = compile_storyboard_continuity(normalized.model_dump(mode="json"))
    current = result["plan"]["shots"][1]
    assert result["report"]["passed"] is True
    assert current["continuity_in"]["frame"] == shots[1]["first_frame"]
    assert current["continuity_in"] != shots[0]["continuity_out"]
    assert "暖色路灯从老人右上方" in current["prompt"]
    assert "沿用上一镜空间关系" not in current["prompt"]
    assert shots == original


def test_explicit_incoming_state_is_preserved() -> None:
    shots = _shots()
    shots[1]["continuity_in"] = {"state": "老人已转身，站在门口左侧", "light": "暖色路灯"}
    compiled = compile_storyboard_continuity({"shots": shots})
    assert compiled["plan"]["shots"][1]["continuity_in"] == shots[1]["continuity_in"]


@pytest.mark.parametrize("transition", ["continuous_action，随后硬切", "承接", "未知剪辑意图", ""])
def test_continuous_markers_and_unknown_legacy_preserve_checks(transition: str) -> None:
    assert transition_preserves_frame(transition) is True


def test_transition_plan_and_outgoing_aliases_use_same_policy() -> None:
    assert shot_transition({"transitionPlan": "direct_cut", "transition": "continuous_action"}) == "direct_cut"
    assert shot_transition({"continuityOut": {"transition": "scene_change"}}) == "scene_change"
    shots = _shots("continuous_action")
    shots[0]["transitionPlan"] = "direct_cut"
    compiled = compile_storyboard_continuity({"shots": shots})
    assert compiled["plan"]["shots"][0]["transition"] == "direct_cut"
    assert compiled["report"]["passed"] is True


def test_explicit_cut_without_current_frame_does_not_copy_previous_view() -> None:
    shots = _shots("scene_change")
    shots[1].pop("first_frame")
    current = compile_storyboard_continuity({"shots": shots})["plan"]["shots"][1]
    assert current["continuity_in"] != shots[0]["continuity_out"]
    assert current["continuity_in"]["subject"] == "老人"


def test_direct_compiler_camera_default_uses_current_view() -> None:
    current = compile_storyboard_continuity({"shots": _shots()})["plan"]["shots"][1]
    assert "机位：按本镜首帧与空间关系确定" in current["prompt"]
    assert "不复制上一镜画面位置" in current["prompt"]


@pytest.mark.parametrize("transition", ["", "承接", "continuous_action", "连续动作"])
def test_continuous_or_legacy_missing_frame_keeps_previous_state(transition: str) -> None:
    shots = _shots(transition)
    shots[0]["continuity_out"] = {"state": "女孩停在门外", "transition": transition}
    shots[1].pop("first_frame")
    compiled = compile_storyboard_continuity({"shots": shots})
    assert compiled["plan"]["shots"][1]["continuity_in"] == shots[0]["continuity_out"]


@pytest.mark.parametrize("transition,passes", [("direct_cut", True), ("continuous_action", False), ("", False)])
def test_reentry_check_uses_previous_transition(transition: str, passes: bool) -> None:
    shots = _shots(transition)
    shots[1]["transition"] = "direct_cut"
    shots[1]["continuity_in"] = {"state": shots[1]["first_frame"]}
    shots[1]["continuity_out"] = {"state": shots[1]["last_frame"]}
    report = lint_storyboard_continuity(shots)
    assert report["passed"] is passes
    assert ("subject_reentry_missing" in {issue["code"] for issue in report["errors"]}) is not passes
    if not passes:
        shots[1]["action"] = "女孩重新进入画面，老人抬眼"
        assert lint_storyboard_continuity(shots)["passed"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transition,tail,own_frame,reenters,expected_error",
    [
        ("direct_cut", False, False, False, ""),
        ("scene_change", True, False, False, ""),
        ("自然切，反打老人", True, True, False, ""),
        ("natural_cut", False, True, False, ""),
        ("未知剪辑意图", False, False, False, "workflow_previous_tail_frame_missing"),
        ("continuous_action", False, False, False, "workflow_previous_tail_frame_missing"),
        ("continuous_action", True, False, False, "workflow_video_continuity_contract_invalid"),
        ("continuous_action", True, True, False, "workflow_video_continuity_contract_invalid"),
        ("continuous_action", True, False, True, ""),
        ("continuous_action", True, True, True, ""),
    ],
)
async def test_persisted_workflow_cut_and_continuous_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    transition: str, tail: bool, own_frame: bool, reenters: bool, expected_error: str,
) -> None:
    for folder in ("output", "state", "runtime"):
        (tmp_path / folder).mkdir()
    ctx = ProjectContext(
        project_id="cut-test", project_name="cut-test", owner_type="user", owner_id="local",
        owner_username="local", requester_user_id="local", requester_username="local",
        requester_principals=(("user", "local"),), effective_role="owner", home_node_id="local",
        output_dir=tmp_path / "output", state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "runtime", is_home_node=True,
    )
    shots = _shots(transition)
    if reenters:
        shots[1]["action"] = "女孩重新进入画面，老人抬眼"
        shots[1]["first_frame"] = "女孩重新进入画面，门外老人位于画面左侧"
    nodes = []
    delivery = {
        "width": 1280, "height": 720, "aspectRatio": "16:9", "fps": 24,
        "safeArea": {"top": 0.05, "right": 0.05, "bottom": 0.08, "left": 0.05},
    }
    for index, shot in enumerate(shots, 1):
        shot["continuity_in"] = {"state": shot["first_frame"]}
        shot.setdefault("continuity_out", {"state": shot["last_frame"]})
        nodes.append({
            "id": f"video-{index}", "type": "videoNode", "position": {"x": index * 300, "y": 0},
            "data": {
                "prompt": shot["prompt"], "model": "legacy/video-test", "genMode": "textToVideo",
                "durationSec": 5, "aspectRatio": "16:9", "resolution": "720p",
                "generateAudio": False, "generateAudioUserSet": True,
                "deliverySpec": delivery, "shotContract": build_shot_contract(shot),
                "continuityIn": shot["continuity_in"], "continuityOut": shot["continuity_out"],
            },
        })
    # Exercise the persisted outgoing fallback; the current shot's outgoing
    # cut must not bypass the previous shot's continuous entry checks.
    nodes[1]["data"]["transitionPlan"] = "direct_cut"
    if transition == "natural_cut":
        nodes[0]["data"].pop("continuityOut")
    media_dir = ctx.output_dir / "media"
    media_dir.mkdir()
    if tail:
        Image.new("RGB", (16, 16), "cyan").save(media_dir / "previous-tail.png")
        nodes[0]["data"]["tailFrameUrl"] = "media/previous-tail.png"
    if own_frame:
        Image.new("RGB", (16, 16), "yellow").save(media_dir / "own-frame.png")
        nodes[1]["data"]["firstFramePath"] = "media/own-frame.png"
        nodes[1]["data"]["genMode"] = "imageToVideo"
    canvas_store.save_canvas(
        ctx.state_dir, "cut-canvas", base_revision=None,
        build_payload=lambda _existing: {
            "schema_version": 2, "canvas_id": "cut-canvas", "project_id": ctx.project_id,
            "revision": 1, "nodes": nodes, "edges": [],
        }, enforce_revision=False, save_source="test", allow_empty_overwrite=True,
    )
    queued = []

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            queued.append(kwargs["payload"])
            return SimpleNamespace(task_state=SimpleNamespace(
                status="queued", progress=0.0, task_id=f"cut-job-{len(queued)}",
            ))

    monkeypatch.setattr(media_dispatch, "resolve_workflow_project_context", resolve_context)
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: Backend())
    run = {"id": "cut-run", "project_id": ctx.project_id, "canvas_id": "cut-canvas", "contract_version": 2}
    if expected_error:
        with pytest.raises(ValueError) as raised:
            await media_dispatch.dispatch_workflow_video_batch(
                run, state_dir=ctx.state_dir, step_id="video", node_ids=["video-1", "video-2"],
                model_ref="legacy/video-test",
            )
        assert raised.value.details["code"] == expected_error
        assert raised.value.details["media_submission_started"] is False
        assert queued == []
        assert read_canvas_snapshot(ctx.state_dir, "cut-canvas")["revision"] == 1
        return
    jobs = await media_dispatch.dispatch_workflow_video_batch(
        run, state_dir=ctx.state_dir, step_id="video", node_ids=["video-1", "video-2"],
        model_ref="legacy/video-test",
    )
    assert len(jobs) == len(queued) == 2
    assert "暖色路灯从老人右上方" in queued[1]["prompt"]
    assert queued[1]["shot_contract"]["start_state"] == shots[1]["first_frame"]
    expected_frame = (
        media_dir / "own-frame.png" if own_frame else media_dir / "previous-tail.png"
        if reenters else None
    )
    assert queued[1]["first_frame_path"] == (str(expected_frame.resolve()) if expected_frame else None)
    assert queued[1]["gen_mode"] == ("imageToVideo" if own_frame else "textToVideo")
    snapshot = read_canvas_snapshot(ctx.state_dir, "cut-canvas")
    assert snapshot["revision"] == 2
    assert all(node["data"]["isGenerating"] is True for node in snapshot["nodes"])
    assert snapshot["nodes"][1]["data"]["generationTaskJobId"] == jobs[1]["job_id"]
    assert snapshot["nodes"][1]["data"]["prompt"] == shots[1]["prompt"]
