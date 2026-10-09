"""Open directing plans survive generation, editing and continuity annotation."""

from types import SimpleNamespace

import pytest

from novelvideo.freezone.text_node import (
    _annotate_generation_plan,
    build_freezone_story_script_task,
)
from novelvideo.ports.story_script import FreezoneStoryScriptRow
from novelvideo.ports.story_script import FreezoneStoryScriptGenerateData
from novelvideo.freezone.script_contract import validate_script_rows


def test_director_plan_is_backward_compatible_and_rewrite_receives_full_plan():
    from novelvideo.api.schemas import FreezoneStoryScriptGenerateRequest
    from novelvideo.freezone.text_node import build_freezone_shot_rewrite_task

    row = {"shot_no": 1, "duration": 4, "visual_description": "窗前空椅"}
    old = FreezoneStoryScriptGenerateData(rows=[row])
    assert old.director_plan.sequences == []
    plan = {"story_promise": "观察等待", "sequences": [{"sequence_id": "S1", "shot_nos": [1], "dramatic_goal": "感受空房间"}], "sound_plan": "门外脚步延续到下一镜"}
    request = FreezoneStoryScriptGenerateRequest(current_rows=[row], director_plan=plan)
    assert request.director_plan.story_promise == "观察等待"
    source = "源" * 5000 + "结局事实：空椅属于离家的母亲"
    row["keyframe_plan"] = [{"state": "椅背被雨打湿", "purpose": "显示等待后的结果"}]
    task = build_freezone_shot_rewrite_task(rows=[row], target_index=0, instruction="保持停留", source_text=source, director_plan=plan)
    assert "门外脚步延续到下一镜" in task
    assert "感受空房间" in task
    assert source in task
    assert task.count("显示等待后的结果") == 1
    assert task.rindex("保持停留") > task.index(source)
    assert task.endswith("只输出这一镜的规定字段。")


def test_generation_focus_follows_complete_story_and_identity_context():
    source = "屋顶追逐。" * 1000 + "结局：小雀接住坠落的护目镜，停顿后看向阿波。"
    task = build_freezone_story_script_task(source_text=source, prompt="保留最后的停顿和眼神交流")
    assert source in task
    assert task.rindex("保留最后的停顿和眼神交流") > task.index(source)
    assert task.endswith("按规定字段输出完整结构化结果。")


@pytest.mark.asyncio
async def test_local_rewrite_updates_visible_cut_state_without_changing_other_rows(monkeypatch):
    from novelvideo.freezone import text_node

    async def run(_task):
        return SimpleNamespace(output=text_node.FreezoneShotRewriteRow(
            duration=4, visual_description="她停步回头", shot="中景", character_action="回头",
            emotion="警觉", shot_prompt="", video_motion_prompt="",
            start_state="面向门口", end_state="侧身看窗", cut_reason="切向窗外的声源",
        ))

    monkeypatch.setattr(text_node, "get_freezone_shot_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    rows = [
        {"shot_no": 1, "duration": 4, "visual_description": "她走向门口", "end_state": "手按门把"},
        {"shot_no": 2, "duration": 4, "visual_description": "窗外空巷"},
    ]
    result, _report = await text_node.generate_freezone_shot_rewrite(rows=rows, target_index=0, instruction="停步回头")
    assert result[0]["end_state"] == "侧身看窗"
    assert result[0]["cut_reason"] == "切向窗外的声源"
    assert result[1]["visual_description"] == rows[1]["visual_description"]
    assert rows[0]["end_state"] == "手按门把"


def test_open_film_language_and_transition_survive_binding():
    row = FreezoneStoryScriptRow(
        shot_no=1, duration=4, visual_description="窗外雪地",
        film_language="空镜建立空间；声音延续；椭圆省略；图形匹配",
        shot_purpose="先让观众感到孤独", cut_reason="省略一夜等待",
        start_state="窗前空椅", end_state="空椅仍在窗前",
        transition_plan="声音延续配合椭圆省略",
    )
    _annotate_generation_plan([row])
    restored = FreezoneStoryScriptRow.model_validate(row.model_dump())
    assert restored.film_language == row.film_language
    assert restored.transition_plan == "声音延续配合椭圆省略"
    assert restored.end_state == "空椅仍在窗前"


@pytest.mark.asyncio
@pytest.mark.parametrize("update_props", [True, False])
async def test_local_rewrite_updates_prop_states_only_when_returned(monkeypatch, update_props):
    from novelvideo.freezone import text_node

    captured = []
    states = {"prop_state_start": "右手握展开扇", "prop_state_change": "右手合拢扇骨", "prop_state_end": "右手握合拢扇"}

    async def run(task):
        captured.append(task)
        return SimpleNamespace(output=text_node.FreezoneShotRewriteRow(
            duration=4, visual_description="她合拢扇子", shot="中景", character_action="合扇",
            emotion="平静", shot_prompt="", video_motion_prompt="", prop_tags="蒲扇",
            **(states if update_props else {}),
        ))

    monkeypatch.setattr(text_node, "get_freezone_shot_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    rows = [
        {"shot_no": 1, "duration": 4, "visual_description": "取扇", "transition_plan": "continuous_action", "prop_state_end": "右手握展开扇"},
        {"shot_no": 2, "duration": 4, "visual_description": "展扇", "prop_tags": "蒲扇",
         "prop_state_start": "原起点", "prop_state_change": "原变化", "prop_state_end": "原终点"},
        {"shot_no": 3, "duration": 4, "visual_description": "远处山景", "transition_plan": "椭圆省略"},
    ]
    original = [dict(row) for row in rows]
    result, _report = await text_node.generate_freezone_shot_rewrite(rows=rows, target_index=1, instruction="合扇")
    assert result[0] == original[0]
    assert result[2] == original[2]
    assert rows == original
    for key, value in states.items():
        assert result[1][key] == (value if update_props else original[1][key])
    assert "通向下一镜 continuous_action" in captured[0]
    assert "道具结束 右手握展开扇" in captured[0]
    assert "道具结束 原终点" in captured[0]


def test_from_to_is_not_evidence_of_continuous_action():
    rows = [
        FreezoneStoryScriptRow(shot_no=1, duration=4, visual_description="房间", scene_tags="房间"),
        FreezoneStoryScriptRow(shot_no=2, duration=4, visual_description="雪山", scene_tags="雪山",
                               video_motion_prompt="从清晨到夜晚的时间省略"),
    ]
    _annotate_generation_plan(rows)
    assert rows[0].transition_plan == "scene_change"
    assert rows[1].transition_plan == "direct_cut"


@pytest.mark.asyncio
@pytest.mark.parametrize("update_plan", [True, False])
async def test_local_rewrite_updates_generation_plan_or_preserves_omitted_fields(monkeypatch, update_plan):
    from copy import deepcopy
    from novelvideo.freezone import text_node

    original_plan = dict(transition_plan="continuous_action", generation_mode="first_last_frame",
                         reference_requirements="本镜尾帧")
    new_plan = dict(transition_plan="声音桥配合有意省略", generation_mode="image_to_video",
                    reference_requirements="角色定妆与当前场景")
    captured = []

    async def run(task):
        captured.append(task)
        return SimpleNamespace(output=text_node.FreezoneShotRewriteRow(
            duration=3.4, visual_description="窗外空巷", shot="全景", character_action="雨水落下",
            emotion="寂静", shot_prompt="", video_motion_prompt="",
            **(new_plan if update_plan else {}),
        ))

    monkeypatch.setattr(text_node, "get_freezone_shot_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    rows = [dict(shot_no=1, duration=4, visual_description="前镜"),
            dict(shot_no=2, duration=4, visual_description="目标", **original_plan),
            dict(shot_no=3, duration=4, visual_description="后镜")]
    before = deepcopy(rows)
    result, _report = await text_node.generate_freezone_shot_rewrite(rows=rows, target_index=1, instruction="改成声音桥")
    for key, value in (new_plan if update_plan else original_plan).items():
        assert result[1][key] == value
    assert result[0] == before[0] and result[2] == before[2] and rows == before
    assert "- generation_mode：first_last_frame" in captured[0]
    assert "- reference_requirements：本镜尾帧" in captured[0]


def test_continuation_annotation_targets_the_outgoing_cut_and_incoming_reference():
    rows = [
        FreezoneStoryScriptRow(shot_no=1, duration=4, visual_description="起跳"),
        FreezoneStoryScriptRow(shot_no=2, duration=4, visual_description="飞过平台",
                               video_motion_prompt="延续上一镜动作，向右飞行"),
        FreezoneStoryScriptRow(shot_no=3, duration=4, visual_description="远处人群"),
    ]
    _annotate_generation_plan(rows)
    assert [row.transition_plan for row in rows] == ["continuous_action", "direct_cut", "direct_cut"]
    assert "上一镜尾帧状态" not in rows[0].reference_requirements
    assert "上一镜尾帧状态" in rows[1].reference_requirements
    assert "上一镜尾帧状态" not in rows[2].reference_requirements


def test_reasoned_reverse_shots_do_not_require_a_scale_jump():
    rows = [
        {"shot_no": 1, "duration": 4, "shot": "中景", "scene_tags": "房间",
         "visual_description": "她问问题", "cut_reason": "切向对方听见后的反应"},
        {"shot_no": 2, "duration": 4, "shot": "中景", "scene_tags": "房间",
         "visual_description": "他迟疑"},
    ]
    report = validate_script_rows(rows)
    assert not any(i.rule_id == "script.continuity.adjacent_framing.v1" for i in report.issues)


def test_generation_task_does_not_force_scale_or_dialogue_quota():
    task = build_freezone_story_script_task(source_text="镜头1：她说话\n镜头2：他回答", prompt="对白戏")
    assert "不强制跨两档" in task
    assert "全片有台词镜不得超过 80%" not in task


def test_repair_instruction_allows_reasoned_same_scale_cuts():
    from novelvideo.freezone.script_repair import build_script_repair_instruction

    instruction = build_script_repair_instruction([])
    assert "同景别正反打" in instruction
    assert "相邻镜头至少跨两档" not in instruction
    assert "不设短镜范围" in instruction
    assert "统计提醒只是观察线索" in instruction
    assert "同步更新 duration_reason" in instruction


def test_system_prompt_allows_per_shot_optics():
    from novelvideo.freezone.text_node import FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT

    assert "Choose segment 8" in FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT
    assert "Never vary focal length" not in FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT
    assert "下一行的第 2、7、8 段必须" not in FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT
