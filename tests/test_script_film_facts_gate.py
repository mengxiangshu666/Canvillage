"""Real-film failure cases: total budget, state handoff and planning freshness."""
from copy import deepcopy

import pytest
from pydantic_ai.models.function import FunctionModel

from novelvideo.freezone import text_node
from novelvideo.freezone.canvas_store import annotate_script_contract_report
from novelvideo.freezone.script_contract import repair_script_rows, script_media_action_gate, split_prompt_segments, validate_script_rows
from novelvideo.freezone.script_director_validation import require_script_director_plan
from novelvideo.freezone.script_video_duration import explicit_script_duration_target
from novelvideo.ports.story_script import FreezoneStoryScriptGenerateData


def directed_script():
    return FreezoneStoryScriptGenerateData(
        director_plan={
            "story_promise": "看清行动", "protagonist_goal": "完成挑战", "core_conflict": "越过障碍",
            "ending_change": "站稳", "rhythm_curve": "准备到落地", "sound_plan": "模型原生脚步和风声",
            "visual_bible": {"visual_style": "3D动画", "texture": "洁净材质", "color_progression": "冷暖对照", "lighting": "自然光", "camera_language": "跟随"},
            "sequences": [{"sequence_id": "S1", "dramatic_goal": "看清落地", "shot_nos": [1, 2]}],
        }, rows=[{
            "shot_no": index + 1, "sequence_ids": ["S1"], "duration": duration, "visual_description": "沿屋顶前进",
            "shot_purpose": "看清接触", "cut_reason": "换视点", "character_1": "阿波", "scene_tags": "屋顶",
            "scene_descriptions": {"屋顶": "东侧红色排气管，西侧白色围栏，中央通行"},
            "start_state": "脚接触屋顶", "end_state": "脚接触屋顶", "transition_plan": "continuous_action",
            "character_state_start": {"阿波": "赤膊，红色护目镜戴在眼前"},
            "character_state_end": {"阿波": "赤膊，红色护目镜戴在眼前"},
            "shot_prompt": "[画面构图：全景] + [角色卡/主体描述：[阿波：绿毛，黑色上衣]] + [主体/人物空间与互动关系：沿屋顶前进] + [主体状态：专注] + [场景环境：红色排气管] + [光影：阳光] + [视觉风格：3D动画] + [技术参数：35mm]",
            "video_motion_prompt": f"[摄影机运镜：跟随] + [主体物理动作：沿屋顶前进] + [环境物理动态：风吹毛发] + [音效：风声] + [台词：无] + [时长：{duration}s]",
        } for index, duration in enumerate([30, 20])],
    )


@pytest.mark.parametrize("text,target", [
    ("总时长为50s，模型单次最长15秒", 50), ("整片时长控制在50秒", 50),
    ("做一个45秒的视频", 45), ("制作一部1.5分钟的短片", 90),
    ("镜头1为4秒，镜头2为8秒", None), ("视频模型时长上限15秒", None),
    ("参考视频总长度未知，媒体时长50秒", None), ("总时长不能超过50秒", None),
])
def test_explicit_target_is_not_a_model_limit_or_individual_shot(text, target):
    assert explicit_script_duration_target(text) == target
    assert explicit_script_duration_target("总时长50秒", "改为制作一个60秒的短片") == 60


@pytest.mark.asyncio
async def test_streamed_generation_accepts_overtime_without_retry_and_records_target(monkeypatch):
    data = directed_script()
    data.rows[0].duration = 63
    calls = []

    async def stream(_messages, _info):
        calls.append(1)
        yield data.model_dump_json()

    model = FunctionModel(stream_function=stream)
    monkeypatch.setattr(text_node, "_direct_or_newapi_text_model", lambda **kwargs: (model, "test"))
    monkeypatch.setattr(text_node, "resolve_freezone_story_script_model", lambda _model: {"id": "test", "provider": "newapi", "model": "test"})
    monkeypatch.setattr(text_node, "get_freezone_story_script_agent", text_node.create_freezone_story_script_agent)
    result = await text_node.generate_freezone_story_script(source_text="总时长50秒，完成屋顶挑战")
    assert sum(row.duration for row in result.rows) == 83
    assert result.director_plan.target_duration_seconds == 50
    assert len(calls) == 1


def test_optional_row_ownership_and_character_states_do_not_block_generation():
    data = directed_script()
    assert require_script_director_plan(data) is data
    data.rows[0].sequence_ids = ["wrong-sequence"]
    data.rows[0].character_state_start = {}
    assert require_script_director_plan(data) is data
    report = validate_script_rows([row.model_dump() for row in data.rows], director_plan=data.director_plan.model_dump())
    assert any(issue.rule_id == "script.plan.sequence_membership.v1" for issue in report.advisory)
    assert not report.blocking
    assert FreezoneStoryScriptGenerateData(rows=[{"shot_no": 1, "duration": 4, "visual_description": "旧镜头"}]).rows


def test_same_scene_goggles_reset_warns_without_blocking_and_respects_ellipsis():
    data = directed_script()
    data.rows[1].character_state_start = {"阿波": "黑色上衣，无护目镜"}
    rows = [row.model_dump() for row in data.rows]
    report = validate_script_rows(rows)
    assert any(issue.rule_id == "script.continuity.character_state.v1" for issue in report.advisory)
    assert not report.blocking
    assert require_script_director_plan(data) is data
    assert script_media_action_gate(rows, action="shot-videos")["allowed"]
    rows[0]["transition_plan"] = "硬切，时间省略，换装后继续"
    assert not any(issue.rule_id == "script.continuity.character_state.v1" for issue in validate_script_rows(rows).issues)
    rows[0]["transition_plan"] = "蒙太奇换地点"
    rows[1]["scene_tags"] = "雪山"
    assert not validate_script_rows(rows).blocking


def test_state_compiler_is_idempotent_preserves_prompt_shape_and_replaces_changed_state():
    row = directed_script().rows[0].model_dump()
    repaired = repair_script_rows([row]).rows
    assert repair_script_rows(repaired).rows == repaired
    assert len(split_prompt_segments(repaired[0]["shot_prompt"])) == 8
    assert len(split_prompt_segments(repaired[0]["video_motion_prompt"])) == 6
    for key in ("shot_prompt", "video_motion_prompt"):
        assert "赤膊，红色护目镜戴在眼前" in repaired[0][key]
        assert "优先于角色卡和参考图中的基准服装" in repaired[0][key]
        assert "沿屋顶前进" in repaired[0][key]
    repaired[0]["character_state_start"] = {"阿波": "穿蓝色上衣，护目镜戴在眼前"}
    repaired[0]["character_state_end"] = dict(repaired[0]["character_state_start"])
    updated = repair_script_rows(repaired).rows[0]
    assert "赤膊" not in updated["shot_prompt"]
    assert updated["shot_prompt"].count("<character_state>") == 1


def test_changing_budget_invalidates_cached_report_but_allows_generation():
    data = directed_script().model_dump()
    data["director_plan"]["target_duration_seconds"] = 50
    payload = {"nodes": [{"type": "scriptNode", "data": {"scriptResult": data}}]}
    annotate_script_contract_report(payload)
    first = deepcopy(payload["nodes"][0]["data"]["scriptContractReport"])
    assert first["blocking_count"] == 0
    data["director_plan"]["target_duration_seconds"] = 40
    annotate_script_contract_report(payload)
    second = payload["nodes"][0]["data"]["scriptContractReport"]
    assert second["rows_fingerprint"] == first["rows_fingerprint"]
    assert second["planning_fingerprint"] != first["planning_fingerprint"]
    assert second["blocking_count"] == 0
    assert any(issue["rule_id"] == "script.duration.total_budget.v1" for issue in second["issues"])
    gate = script_media_action_gate(data["rows"], action="shot-videos", director_plan=data["director_plan"])
    assert gate["allowed"]


@pytest.mark.asyncio
async def test_local_optimizer_does_not_claim_full_budget_fixed_or_silently_cut_shots():
    data = directed_script().model_dump()
    data["rows"][0]["duration"] = 63
    data["director_plan"]["target_duration_seconds"] = 50
    report = validate_script_rows(data["rows"], director_plan=data["director_plan"])
    rows, result = await text_node.generate_freezone_script_contract_repair(
        rows=data["rows"], issues=[issue.as_dict() for issue in report.issues], director_plan=data["director_plan"],
    )
    assert sum(row["duration"] for row in rows) == 83
    assert result["repair"]["targets"] == 0
    assert any(issue["rule_id"] == "script.duration.total_budget.v1" for issue in result["issues"])
    assert result["blocking_count"] == 0
