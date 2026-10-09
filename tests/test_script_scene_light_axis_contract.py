from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.freezone.text_node import build_freezone_shot_rewrite_task, build_freezone_story_script_task
from novelvideo.production.cinematic_contract import audit_cinematic_contracts, cinematic_prompt_lines
from novelvideo.production.director_plan import build_director_plan
from novelvideo.workflow_runtime import executor
from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.workflow_runtime.store import WorkflowRunStore
from tests.test_workflow_runtime import _fake_model_plan_snapshot


def _shot(index: int, scene: str = "room", direction: str = "东墙窗向桌面照明", temperature: int = 4300, axis: str = "table-axis", side: str = "轴线右侧") -> dict:
    return {
        "shot_id": f"S{index:02d}", "title": "等待", "duration_seconds": 5,
        "subject": "老人", "action": "老人抬眼望向门口", "prompt": "老人停在桌旁，抬眼观察门口",
        "first_frame": "老人停在桌旁", "last_frame": "老人望向门口",
        "camera_position": "桌旁观察门口", "camera_motion": "固定观察", "transition": "direct_cut",
        "reference_bindings": {"scene": [scene]},
        "cinematic": {
            "lighting": {"source_direction": direction, "color_temperature_k": temperature, "motivated_source": "场景内实际光源"},
            "screen_direction": {"axis_id": axis, "line_side": side, "eyeline": "看向门口"},
        },
    }


def test_distinct_bound_scenes_do_not_share_light_or_axis() -> None:
    shots = [_shot(1), _shot(2, "street", "路口灯照向门外", 2800, "door-axis", "轴线左侧")]
    original = deepcopy(shots)
    report = audit_cinematic_contracts(shots)
    assert report["gate_observations"]["lighting_continuity_consistent"] is True
    assert report["gate_observations"]["screen_direction_consistent"] is True
    assert shots == original


def test_reverse_views_keep_world_source_and_complementary_gazes() -> None:
    shots = [_shot(1), _shot(2)]
    shots[0]["camera_position"] = "桌右侧看向老人"
    shots[1]["camera_position"] = "门右侧看向女孩"
    shots[1]["subject"] = "女孩"
    shots[0]["cinematic"]["screen_direction"]["eyeline"] = "看向画面右侧女孩"
    shots[1]["cinematic"]["screen_direction"]["eyeline"] = "看向画面左侧老人"
    assert audit_cinematic_contracts(shots)["passed"] is True


@pytest.mark.parametrize("policy", [
    "不允许变化", "不允许越轴", "disallow", "not allow", "do not allow", "allow", "允许",
    "允许：", "allow: !", "允许：不允许变化", "allow: not allowed", "保持原光源，允许一词仅作例子",
])
def test_negative_or_bare_permission_does_not_exempt_drift(policy: str) -> None:
    shots = [_shot(1), _shot(2, direction="西侧台灯照向桌面", temperature=2800, axis="other-axis")]
    shots[1]["cinematic"]["lighting"]["change_policy"] = policy
    shots[1]["cinematic"]["screen_direction"]["crossing_policy"] = policy
    report = audit_cinematic_contracts(shots)
    assert report["gate_observations"]["lighting_continuity_consistent"] is False
    assert report["gate_observations"]["screen_direction_consistent"] is False


@pytest.mark.parametrize("policy", ["允许：门打开后窗光进入", "allow: after the door opens", "allowed when the door opens"])
def test_motivated_change_becomes_next_shot_baseline(policy: str) -> None:
    shots = [_shot(1), _shot(2, direction="西窗来光", temperature=5000), _shot(3, direction="西窗来光", temperature=5000)]
    shots[1]["cinematic"]["lighting"]["change_policy"] = policy
    assert audit_cinematic_contracts(shots)["gate_observations"]["lighting_continuity_consistent"] is True
    shots[2]["cinematic"]["lighting"]["source_direction"] = "凭空变成顶光"
    report = audit_cinematic_contracts(shots)
    assert report["gate_observations"]["lighting_continuity_consistent"] is False
    assert all(item["shot_id"] == "S03" for item in report["issues"])


def test_future_permission_does_not_hide_earlier_drift_or_scene_return() -> None:
    shots = [_shot(1), _shot(2, "street", "路灯来光", 2800, "street-axis"), _shot(3, direction="错误顶光")]
    report = audit_cinematic_contracts(shots)
    assert report["gate_observations"]["lighting_continuity_consistent"] is False
    assert [item["shot_id"] for item in report["issues"]] == ["S03"]
    shots = [_shot(1), _shot(2, direction="错误顶光"), _shot(3, direction="错误顶光")]
    shots[2]["cinematic"]["lighting"]["change_policy"] = "允许：之后才开灯"
    assert audit_cinematic_contracts(shots)["gate_observations"]["lighting_continuity_consistent"] is False


@pytest.mark.parametrize("binding", [{}, {"scene": ["room", "street"]}, {"scene": [None]}, {"scene": [{"id": "street"}]}])
def test_incomplete_scene_scope_keeps_conservative_comparison(binding: dict) -> None:
    shots = [_shot(1), _shot(2, "street", "路灯来光", 2800, "street-axis")]
    shots[1]["reference_bindings"] = binding
    assert audit_cinematic_contracts(shots)["gate_observations"]["lighting_continuity_consistent"] is False


@pytest.mark.parametrize("side", ["轴线左侧", "left", "左侧"])
def test_same_axis_known_opposite_side_needs_motivated_crossing(side: str) -> None:
    shots = [_shot(1), _shot(2, side=side)]
    shots[1]["cinematic"]["screen_direction"]["crossing_policy"] = "不允许越轴"
    report = audit_cinematic_contracts(shots)
    assert report["gate_observations"]["screen_direction_consistent"] is False
    assert any(item["code"] == "screen_direction.line_side_changed" for item in report["issues"])
    shots[1]["cinematic"]["screen_direction"]["crossing_policy"] = "允许：本镜连续移动经过中轴再落到左侧"
    shots.append(_shot(3, side="left"))
    assert audit_cinematic_contracts(shots)["gate_observations"]["screen_direction_consistent"] is True


def test_camel_scene_bindings_and_unknown_side_keep_authored_facts() -> None:
    shots = [_shot(1), _shot(2, "street", "路灯来光", 2800, "street-axis")]
    for shot in shots:
        shot["referenceBindings"] = shot.pop("reference_bindings")
    assert audit_cinematic_contracts(shots)["passed"] is True
    shots = [_shot(1), _shot(2, side="绕桌后的观察点，待实测")]
    report = audit_cinematic_contracts(shots)
    assert report["contracts"][1]["screen_direction"]["line_side"] == "绕桌后的观察点，待实测"
    assert not any(item["code"] == "screen_direction.line_side_changed" for item in report["issues"])
    shots.append(_shot(3, side="left"))
    assert audit_cinematic_contracts(shots)["gate_observations"]["screen_direction_consistent"] is False


def test_combat_sample_same_side_passes_and_one_side_flip_is_rejected() -> None:
    from novelvideo.production.combat_film_contract import compile_combat_film_contract
    from tests.test_combat_film_contract import _request, _shots

    shots = _shots()
    assert compile_combat_film_contract(shots=shots, request=_request())["ready_for_media"] is True
    shots[3]["camera_position"] = "轴线左侧过肩"
    shots[3]["cinematic"]["screen_direction"]["line_side"] = "轴线左侧"
    report = compile_combat_film_contract(shots=shots, request=_request())
    assert report["ready_for_media"] is False
    assert any(item["code"] == "screen_direction.line_side_changed" for item in report["issues"])


def test_script_generation_and_rewrite_share_landmark_light_guidance() -> None:
    tasks = [
        build_freezone_story_script_task(source_text="屋内窗光切到街头路灯", prompt="手绘"),
        build_freezone_shot_rewrite_task(rows=[{"shot_no": 1, "duration": 5, "visual_description": "观察门口"}], target_index=0, instruction="保留世界光源"),
    ]
    for task in tasks:
        assert "光源以固定地标为基准" in task
        assert "屏幕左右随本镜观察方向改变" in task
        assert "换场分别落实本场光色" in task


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", [False, True])
async def test_isolated_workflow_compiler_quality_and_persistent_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, drift: bool,
) -> None:
    import pydantic_ai
    from novelvideo.generators import direct_models

    shots = [_shot(1), _shot(2, "street", "路灯向门外照明", 2800, "door-axis", "轴线左侧")]
    if drift:
        shots[1]["reference_bindings"] = {"scene": ["room"]}
        shots[1]["cinematic"]["lighting"]["change_policy"] = "不允许变化"
        shots[1]["cinematic"]["screen_direction"]["crossing_policy"] = "disallow"
    captured = {}

    class Agent:
        def __init__(self, *_args, **kwargs):
            captured["system_prompt"] = kwargs["system_prompt"]

        async def run(self, _request):
            return SimpleNamespace(output=json.dumps({"title": "等待", "creative_direction": "通过视点和光色表达等待", "shots": shots}, ensure_ascii=False))

    monkeypatch.setattr(pydantic_ai, "Agent", Agent)
    monkeypatch.setattr(direct_models, "get_direct_pydantic_model", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(executor, "resolve_snapshot_model_ref", lambda _snapshot, _role: ("agent", "direct/agent-test"))
    intent = {"delivery_level": "storyboard", "quality_gates": ["director_plan_valid", "canvas_structure_receipt", "story_and_shots_complete", "lighting_continuity_consistent", "screen_direction_consistent"]}
    plan = build_director_plan(objective="两个场景中的等待", director_intent_contract=intent)
    store = WorkflowRunStore(tmp_path)
    run, reused = await store.create(
        definition=get_workflow_definition("one-click-film"), project_id="light-test", canvas_id="light-canvas", run_mode="draft",
        inputs={"request": "两个场景中的等待分镜", "director_mode": "production", "director_plan": plan, "director_intent_contract": intent},
        idempotency_key="light-test", contract_version=1, model_plan_snapshot=_fake_model_plan_snapshot(),
    )
    assert not reused
    run, _ = await store.record_event(run["id"], event_id="canvas", event_type="canvas_applied", step_id="canvas_structure", success=True, payload={"created_node_ids": []}, expected_revision=run["revision"])
    result = await executor._storyboard_handler(run, run["step_states"]["story_and_shots"])
    assert "光源以固定地标为基准" in captured["system_prompt"]
    compiled = result.payload["plan"]["shots"]
    assert "路灯向门外照明" in compiled[1]["prompt"] and "2800K" in compiled[1]["prompt"]
    assert "世界光源与本镜观察关系" in "\n".join(cinematic_prompt_lines(compiled[1]["cinematic"]))
    run, _ = await store.record_event(run["id"], event_id="story-output", event_type="step_output_ready", step_id="story_and_shots", success=True, payload=result.payload, expected_revision=run["revision"])
    run, _ = await store.record_event(run["id"], event_id="story-canvas", event_type="canvas_applied", step_id="story_and_shots", success=True, payload={"created_node_ids": ["S01", "S02"]}, expected_revision=run["revision"])
    for step_id in ("asset_slots", "media_generation"):
        run, _ = await store.record_event(run["id"], event_id=step_id, event_type="step_completed", step_id=step_id, success=True, payload={"started": False}, expected_revision=run["revision"])
    if drift:
        with pytest.raises(executor.WorkflowStepExecutionError) as raised:
            await executor._quality_review_handler(run, run["step_states"]["quality_review"])
        assert raised.value.code == "workflow_quality_gates_failed"
        report = raised.value.details["quality_gate_report"]
        assert {"lighting_continuity_consistent", "screen_direction_consistent"} <= set(report["blocking_gates"])
        run, _ = await store.record_event(run["id"], event_id="quality", event_type="step_failed", step_id="quality_review", success=False, payload=raised.value.details, error=str(raised.value), expected_revision=run["revision"])
        assert (await store.get(run["id"]))["step_states"]["quality_review"]["status"] == "failed"
    else:
        result = await executor._quality_review_handler(run, run["step_states"]["quality_review"])
        assert result.payload["quality_gate_report"]["blocking_gates"] == []
        run, _ = await store.record_event(run["id"], event_id="quality", event_type=result.event_type, step_id="quality_review", success=True, payload=result.payload, expected_revision=run["revision"])
        stored = await store.get(run["id"])
        assert stored["artifacts"]["quality_review"]["quality_gate_report"]["gate_statuses"]["lighting_continuity_consistent"] == "passed"
        assert stored["artifacts"]["quality_review"]["cinematic_contract"]["gate_observations"]["final_delivery_qc_passed"] is None
