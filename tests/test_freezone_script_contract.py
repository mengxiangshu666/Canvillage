"""脚本科目的合同层：8 段式解析、十条规则、机械修复。

这些断言的价值不在"函数能跑"，而在于把提示词里那三条一直是**口头的**硬约束
（角色卡逐字一致、第 7/8 段全篇唯一、运动稿时长与时长列一致）变成机器判定的。
所以每个用例都刻意构造模型真的会犯的那种错，而不是构造一个完美输入。
"""

from __future__ import annotations

import pytest
import json
from pathlib import Path

from novelvideo.freezone.script_contract import (
    MOTION_PROMPT_SEGMENT_COUNT,
    SCRIPT_CONTRACT_SCHEMA,
    SHOT_PROMPT_SEGMENT_COUNT,
    VIEWABILITY_MIN_SHOTS,
    character_ids_in_card,
    classify_segments,
    camera_direction_text,
    camera_direction_needs_review,
    enforce_story_script_contract,
    repair_script_rows,
    split_prompt_segments,
    framing_family,
    validate_script_rows,
)
from novelvideo.freezone.script_contract import SHOT_SEGMENT_ORDER, MOTION_SEGMENT_ORDER
from novelvideo.freezone.script_repair import (
    GLOBAL_VIEWABILITY_RULE_ORDER,
    MAX_GLOBAL_REPAIR_TARGETS,
    MAX_REPAIR_TARGETS,
    plan_script_contract_repairs,
)

CARD = "[沈昭昭_现代: 28岁女性，面色苍白，神情疲惫，身穿现代简约职业装]"


@pytest.mark.asyncio
async def test_unchanged_first_batch_does_not_starve_later_rows(monkeypatch):
    import novelvideo.freezone.text_node as text_node

    rows = [_row(shot_no=i + 1, video_motion_prompt="[运镜轨迹：镜头前推]") for i in range(15)]
    before = repair_script_rows(rows)
    calls = []

    async def unchanged(**kwargs):
        calls.append(kwargs["target_index"])
        return kwargs["rows"], {}

    monkeypatch.setattr(text_node, "generate_freezone_shot_rewrite", unchanged)
    result, report = await text_node.generate_freezone_script_contract_repair(
        rows=rows, issues=before.as_dict()["issues"], max_passes=3,
    )
    assert sorted(calls) == list(range(15))
    assert result == before.rows
    assert report["repair"]["passes"] == 2
    assert report["repair"]["stop_reason"] == "review_required"
    assert len(report["repair"]["target_results"]) == 15


def test_creative_statistics_remain_visible_without_triggering_automatic_repair():
    from novelvideo.freezone.script_repair import is_model_repairable_issue

    for rule_id in GLOBAL_VIEWABILITY_RULE_ORDER:
        assert not is_model_repairable_issue({"rule_id": rule_id, "severity": "advisory"})
    assert is_model_repairable_issue({"rule_id": "script.motion.segments.v1", "severity": "blocking"})
OTHER_CARD = "[沈昭昭_现代: 28岁女性，面色苍白，疲惫]"
STYLE = "[视觉风格/质感：都市悬疑写实电影感]"
OTHER_STYLE = "[视觉风格/质感：写实电影感]"
TECH = "[技术参数：85mm镜头，f/1.8，浅景深]"


def _shot_prompt(
    composition: str = "[画面构图：近景特写，平视机位]",
    card: str = CARD,
    spatial: str = "[主体/人物空间与互动关系：她独坐在办公桌前]",
    micro: str = "[极具体的微表情：眼下发青，手指微颤]",
    environment: str = "[明确的场景环境元素：深夜办公室、冷掉的咖啡杯]",
    lighting: str = "[光影几何与大气效果：冷蓝主调]",
    style: str = STYLE,
    technical: str = TECH,
) -> str:
    return " + ".join([composition, f"[角色卡/主体描述：{card}]", spatial, micro, environment, lighting, style, technical])


def _motion_prompt(camera: str = "镜头前推", duration: str = "5s", action: str = "她抬眼看屏") -> str:
    return " + ".join(
        [
            f"[明确的摄影机运镜轨迹与速度：{camera}，极慢速]",
            f"[主体极其具体的物理动作细节：{action}]",
            "[环境物理动态：纸张被空调风吹起]",
            "[音效与氛围描述：键盘声、室内低频电流声]",
            "[对话台词与语气：无]",
            f"[时长：{duration}]",
        ]
    )


def _row(**overrides) -> dict:
    base = {
        "shot_no": 1,
        "duration": 5,
        "visual_description": "她抬眼看屏",
        "shot": "近景 / 平视",
        "shot_prompt": _shot_prompt(),
        "video_motion_prompt": _motion_prompt(),
    }
    base.update(overrides)
    return base


CAMERA_CASES = json.loads((Path(__file__).parent / 'fixtures/script-camera-contract.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', CAMERA_CASES, ids=lambda case: case['id'])
def test_shared_camera_contract_cases(case):
    assert camera_direction_text(f"[{case['text']}]") == case['camera']
    assert camera_direction_needs_review(case['camera']) is case['needs_review']


def test_unlabelled_camera_reaches_prompt_bundle_audit_and_existing_repair_without_rewriting():
    from copy import deepcopy
    from novelvideo.freezone.film_prompt_contract import build_film_prompt_bundle, audit_film_prompt_bundle

    camera = CAMERA_CASES[1]['text']
    row = _row(video_motion_prompt=_motion_prompt().replace('[明确的摄影机运镜轨迹与速度：镜头前推，极慢速]', f'[{camera}]'))
    original = deepcopy(row)
    assert classify_segments(row['video_motion_prompt'], MOTION_SEGMENT_ORDER)[0] == 'camera'
    report = validate_script_rows([row])
    issues = [issue for issue in report.issues if issue.rule_id == 'script.camera.contradiction.v1']
    assert len(issues) == 1 and issues[0].severity == 'advisory'
    assert issues[0].row_index == 0 and issues[0].field == 'video_motion_prompt'
    targets = plan_script_contract_repairs([row], [issue.as_dict() for issue in issues])
    assert len(targets) == 1 and '固定构图' in targets[0].instruction
    bundle = build_film_prompt_bundle([row])
    assert camera in bundle['rows'][0]['motion_segments']['camera']
    assert not any(issue['code'] == 'prompt.camera_missing' for issue in audit_film_prompt_bundle(bundle)['issues'])
    assert repair_script_rows([row]).rows == [row]
    assert row == original


def _sequence_repair_rows():
    from novelvideo.ports.story_script import FreezoneStoryScriptRow

    return [FreezoneStoryScriptRow(**_row(shot_no=index + 1, shot_id=f"ID-{index}", cut_reason="揭示新信息",
                 shot="远景 / 平视" if index % 2 else "特写 / 平视", emotion="专注",
                 character_action=f"动作{index}", visual_description=f"画面{index}")).model_dump() for index in range(4)]


@pytest.mark.parametrize("membership", [[2, 3], [1, 3]])
def test_repair_plan_groups_actual_sequence_members_even_with_wrong_row_labels(membership):
    rows = _sequence_repair_rows()
    rows[1]["sequence_ids"] = ["wrong"]
    plan = {"sequences": [{"sequence_id": "S1", "shot_nos": membership}]}
    issues = [_row_issue("script.motion.segments.v1", number - 1) for number in membership]
    targets = plan_script_contract_repairs(rows, issues, director_plan=plan)
    assert len(targets) == 1
    assert targets[0].row_indices == tuple(number - 1 for number in membership)
    assert targets[0].sequence_ids == ("S1",)
    assert "先诊断" in targets[0].instruction and "只改当前这一镜" not in targets[0].instruction


@pytest.mark.parametrize("overlap", [False, True])
def test_repair_plan_merges_overlaps_and_explicit_cross_sequence_handoffs(overlap):
    rows = _sequence_repair_rows()
    plan = {"sequences": [{"sequence_id": "S1", "shot_nos": [1, 2]},
                          {"sequence_id": "S2", "shot_nos": [2, 3] if overlap else [3, 4]}]}
    issue = {**_row_issue("script.continuity.missing_states.v1", 2),
             "detail": {"previous_row_index": 1}}
    targets = plan_script_contract_repairs(rows, [issue], director_plan=plan)
    assert len(targets) == 1 and targets[0].sequence_ids == ("S1", "S2")
    assert targets[0].row_indices == ((0, 1, 2) if overlap else (0, 1, 2, 3))


def test_missing_outgoing_end_state_includes_both_sides_of_sequence_boundary():
    rows = _sequence_repair_rows()
    rows[1]["transition_plan"] = "continuous_action"
    rows[2]["start_state"] = "接住前镜动作"
    plan = {"sequences": [{"sequence_id": "S1", "shot_nos": [1, 2]},
                          {"sequence_id": "S2", "shot_nos": [3, 4]}]}
    issues = validate_script_rows(rows, director_plan=plan).as_dict()["issues"]
    assert len(issues) == 1 and issues[0]["field"] == "end_state"
    targets = plan_script_contract_repairs(rows, issues, director_plan=plan)
    assert len(targets) == 1 and targets[0].row_indices == (0, 1, 2, 3)
    assert targets[0].sequence_ids == ("S1", "S2")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["fixed", "unchanged", "boundary", "new_blocker", "outside", "asset_delete", "failure"])
async def test_sequence_optimization_rechecks_full_scope_and_keeps_original_on_failure(monkeypatch, outcome):
    from copy import deepcopy
    from types import SimpleNamespace
    from novelvideo.freezone import sequence_rewrite, text_node

    rows = _sequence_repair_rows()
    rows[1]["video_motion_prompt"] = "[运镜轨迹：镜头前推]"
    if outcome == "asset_delete":
        rows[0].update(prop_tags="滑板", prop_descriptions={"滑板": "青色板面"})
        rows[2].update(prop_tags="滑板", prop_descriptions={"滑板": "红色板面"})
    plan = {"sequences": [{"sequence_id": "S1", "shot_nos": [2, 3]}]}
    calls = []

    async def run(task):
        calls.append(task)
        assert "先诊断" in task and "段落边界的前后镜保持原样" in task
        if outcome == "failure":
            raise RuntimeError("provider unavailable")
        output = [dict(rows[index]) for index in (1, 2)]
        if outcome != "unchanged":
            output[0]["video_motion_prompt"] = _motion_prompt()
            output[0]["end_state"] = "她听见报警，抬眼看屏"
            output[1]["start_state"] = "她看屏后作出决定"
        if outcome == "boundary":
            output[1]["transition_plan"] = "continuous_action"
            output[1]["end_state"] = ""
        if outcome == "new_blocker":
            output[1]["video_motion_prompt"] = "[运镜轨迹：镜头前推]"
        if outcome == "asset_delete":
            output[1]["prop_descriptions"] = {}
        return SimpleNamespace(output=sequence_rewrite.SequenceRewriteResult(
            rows=[sequence_rewrite.SequenceRewriteShot(**row) for row in output],
            diagnosis="先听见报警，再观察屏幕，最后作出选择。段外镜保留。",
            sequence_plan=None if outcome == "unchanged" else {"sequence_id": "S1", "performance_plan": "报警触发观察与决定"},
        ))

    monkeypatch.setattr(sequence_rewrite, "create_sequence_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    if outcome == "outside":
        original_rewrite = sequence_rewrite.generate_sequence_rewrite

        async def leak(**kwargs):
            candidate, report = await original_rewrite(**kwargs)
            candidate[0]["visual_description"] = "unauthorized change"
            return candidate, report

        monkeypatch.setattr(sequence_rewrite, "generate_sequence_rewrite", leak)
    baseline = repair_script_rows(rows, director_plan=plan)
    before = deepcopy(rows)
    if outcome == "failure":
        with pytest.raises(ValueError, match="所有目标镜头都改写失败"):
            await text_node.generate_freezone_script_contract_repair(rows=rows, director_plan=plan, issues=[], max_passes=3)
    else:
        result, report = await text_node.generate_freezone_script_contract_repair(
            rows=rows, director_plan=plan, issues=[_row_issue("stale.rule", 0)], max_passes=3,
        )
        repair = report["repair"]
        assert repair["targets"] == 1 and repair["scope"] == "sequence"
        assert repair["target_results"][0]["row_indices"] == [1, 2]
        assert repair["target_results"][0]["diagnosis"].startswith("先听见")
        assert repair["applied"] == int(outcome == "fixed")
        assert repair["rejected"] == int(outcome in {"boundary", "new_blocker", "outside", "asset_delete"})
        assert result[0] == baseline.rows[0] and result[3] == baseline.rows[3]
        if outcome == "fixed":
            assert repair["director_plan"]["sequences"][0]["performance_plan"] == "报警触发观察与决定"
            assert repair["stop_reason"] == "complete" and not repair["needs_more_repair"]
        else:
            from novelvideo.ports.story_script import FreezoneStoryDirectorPlan

            assert result == baseline.rows and repair["director_plan"] == FreezoneStoryDirectorPlan.model_validate(plan).model_dump()
            assert repair["stop_reason"] == "review_required"
    assert len(calls) == 1 and rows == before


@pytest.mark.asyncio
async def test_sequence_optimization_does_not_call_model_for_stale_resolved_issue(monkeypatch):
    from novelvideo.freezone import sequence_rewrite, text_node

    async def unexpected(**_kwargs):
        pytest.fail("a stale report must not trigger a rewrite")

    monkeypatch.setattr(sequence_rewrite, "generate_sequence_rewrite", unexpected)
    rows, report = await text_node.generate_freezone_script_contract_repair(
        rows=_sequence_repair_rows(), director_plan={"sequences": [{"sequence_id": "S1", "shot_nos": [2, 3]}]},
        issues=[_row_issue("script.motion.segments.v1", 1)],
    )
    assert len(rows) == 4 and report["repair"]["targets"] == 0


@pytest.mark.asyncio
async def test_failed_sequence_keeps_previously_accepted_sequence_and_plan(monkeypatch):
    import json
    from types import SimpleNamespace
    from novelvideo.freezone import sequence_rewrite, text_node

    rows = _sequence_repair_rows()
    for index in (0, 2):
        rows[index]["video_motion_prompt"] = "[运镜轨迹：镜头前推]"
    plan = {"sequences": [{"sequence_id": "S1", "shot_nos": [1, 2]},
                          {"sequence_id": "S2", "shot_nos": [3, 4]}]}
    calls = []

    async def run(task):
        identities = json.loads(task.split("目标段落编号：", 1)[1].split("\n", 1)[0])
        calls.append(identities)
        if identities == ["S2"]:
            raise RuntimeError("second sequence unavailable")
        output = [dict(rows[index]) for index in (0, 1)]
        output[0]["video_motion_prompt"] = _motion_prompt()
        return SimpleNamespace(output=sequence_rewrite.SequenceRewriteResult(
            rows=[sequence_rewrite.SequenceRewriteShot(**row) for row in output], diagnosis="首段补回动作因果。",
            sequence_plan={"sequence_id": "S1", "performance_plan": "保留观察后的决定"},
        ))

    monkeypatch.setattr(sequence_rewrite, "create_sequence_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    result, report = await text_node.generate_freezone_script_contract_repair(
        rows=rows, director_plan=plan, issues=[], max_passes=3,
    )
    assert calls == [["S1"], ["S2"]]
    assert result[0]["video_motion_prompt"] == _motion_prompt() and result[2] == rows[2]
    assert report["repair"]["applied"] == 1 and report["repair"]["failed"] == 1
    assert report["repair"]["director_plan"]["sequences"][0]["performance_plan"] == "保留观察后的决定"
    assert report["repair"]["target_results"][1]["outcome"] == "failed"


def _rules(report) -> list[str]:
    return [issue.rule_id for issue in report.issues]


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


def test_split_prompt_segments_strips_brackets_and_keeps_inner_ones():
    segments = split_prompt_segments(_shot_prompt())
    assert len(segments) == SHOT_PROMPT_SEGMENT_COUNT
    # 角色卡段内层还有一对括号，不能被剥掉——那是角色 ID 的载体。
    assert segments[1].startswith("角色卡/主体描述：[沈昭昭_现代:")


def test_continuity_reports_adjacent_same_framing_and_repeated_event():
    rows = [
        _row(scene_tags="深夜办公室", character_action="她抬眼看屏幕后伸手拿起咖啡杯"),
        _row(shot_no=2, scene_tags="深夜办公室", character_action="她抬眼看屏幕后伸手拿起咖啡杯"),
    ]
    report = validate_script_rows(rows)
    rule_ids = _rules(report)
    assert "script.continuity.adjacent_framing.v1" in rule_ids
    assert "script.continuity.repeated_visual_event.v1" in rule_ids


def test_continuity_reports_adjacent_framing_family_even_when_size_differs():
    rows = [
        _row(scene_tags="深夜办公室", shot="中景 / 平视"),
        _row(shot_no=2, scene_tags="深夜办公室", shot="近景 / 平视"),
    ]
    report = validate_script_rows(rows)
    issue = next(
        item for item in report.issues if item.rule_id == "script.continuity.adjacent_framing.v1"
    )
    assert issue.detail["framing_distance"] == 1
    assert "相邻" in issue.message


def test_framing_family_supports_the_seventh_middle_wide_tier():
    assert framing_family("中远景 / 低机位") == "中远景"


def test_planning_reports_framing_run_and_static_opening():
    rows = [
        _row(shot_no=1, duration=2, scene_tags="办公室", dialogue="你来了"),
        _row(shot_no=2, duration=2, scene_tags="办公室", dialogue="我来了"),
        _row(shot_no=3, duration=2, scene_tags="办公室", dialogue="坐吧"),
    ]
    report = validate_script_rows(rows)
    rule_ids = _rules(report)
    assert "script.planning.framing_run.v1" in rule_ids
    assert "script.planning.opening_dialogue.v1" in rule_ids


def test_continuity_reports_explicit_screen_direction_reversal():
    rows = [
        _row(character_1="阿波", scene_tags="巷口", visual_description="她向右奔跑"),
        _row(character_1="阿波", shot_no=2, scene_tags="巷口", visual_description="她向左奔跑"),
    ]
    report = validate_script_rows(rows)
    assert "script.continuity.screen_direction.v1" in _rules(report)


@pytest.mark.parametrize("first,second", [
    ("阿波在左侧", "阿波在右侧"),
    ("阿波看左边", "阿波看右边"),
    ("镜头向左移动，阿波站立", "镜头向右移动，阿波站立"),
])
def test_direction_does_not_compare_placement_eyelines_or_camera(first, second):
    rows = [_row(character_1="阿波", scene_tags="巷口", shot=first, character_action="站立"),
            _row(character_1="阿波", shot_no=2, scene_tags="巷口", shot=second, character_action="站立")]
    assert "script.continuity.screen_direction.v1" not in _rules(validate_script_rows(rows))
    for row, text in zip(rows, (first, second)):
        row["character_action"] = ""
        row["visual_description"] = text
    assert "script.continuity.screen_direction.v1" not in _rules(validate_script_rows(rows))


@pytest.mark.parametrize("other", [{"character_1": "阿云"}, {"character_2": "阿云"}, {"character_1": ""}])
def test_direction_does_not_assume_same_subject(other):
    rows = [_row(character_1="阿波", scene_tags="巷口", character_action="向右奔跑"),
            _row(character_1="阿波", shot_no=2, scene_tags="巷口", character_action="向左奔跑")]
    rows[1].update(other)
    assert "script.continuity.screen_direction.v1" not in _rules(validate_script_rows(rows))


@pytest.mark.parametrize("placeholder", ["无", "none", " N/A ", "null", "待定", "待补充"])
def test_continuous_placeholder_is_not_a_cut_state(placeholder):
    rows = [_row(transition_plan="continuous_action", end_state=placeholder),
            _row(shot_no=2, start_state="阿波向右腾空，滑板在脚下")]
    issues = [item for item in validate_script_rows(rows).issues if item.rule_id == "script.continuity.missing_states.v1"]
    assert [(item.row_index, item.field) for item in issues] == [(0, "end_state")]
    assert repair_script_rows(rows).rows[0]["end_state"] == placeholder


@pytest.mark.parametrize("transition", ["continuous_action", "continuous-action", "连续动作", "动作衔接"])
def test_continuous_cut_reports_missing_states_even_across_locations(transition):
    rows = [
        _row(scene_tags="楼缘", transition_plan=transition),
        _row(shot_no=2, scene_tags="隔壁平台"),
    ]
    report = validate_script_rows(rows)
    issue = next(i for i in report.issues if i.rule_id == "script.continuity.missing_states.v1")
    assert issue.severity == "advisory"
    assert issue.detail["missing_states"] == [
        {"row_index": 0, "field": "end_state"}, {"row_index": 1, "field": "start_state"},
    ]
    issues = [i for i in report.issues if i.rule_id == "script.continuity.missing_states.v1"]
    assert [(i.row_index, i.field) for i in issues] == [(0, "end_state"), (1, "start_state")]
    targets = plan_script_contract_repairs(rows, [i.as_dict() for i in issues])
    assert [target.row_index for target in targets] == [0, 1]
    assert all("不修改邻镜" in target.instruction for target in targets)
    repaired = repair_script_rows(rows).rows
    assert "end_state" not in repaired[0]
    assert "start_state" not in repaired[1]


@pytest.mark.parametrize("transition", ["direct_cut", "match_cut", "声音桥与椭圆省略", ""])
def test_other_cut_choices_do_not_require_continuous_states(transition):
    rows = [_row(transition_plan=transition), _row(shot_no=2)]
    assert "script.continuity.missing_states.v1" not in _rules(validate_script_rows(rows))


def test_continuity_checks_presence_without_claiming_textual_state_equality():
    rows = [
        _row(transition_plan="continuous_action", end_state="楼缘向右腾空"),
        _row(shot_no=2, start_state="低机位仰拍飞行姿态", transition_plan="continuous_action"),
    ]
    assert "script.continuity.missing_states.v1" not in _rules(validate_script_rows(rows))
    rows[0]["end_state"] = "  "
    issue = next(i for i in validate_script_rows(rows).issues if i.rule_id == "script.continuity.missing_states.v1")
    assert issue.detail["missing_states"] == [{"row_index": 0, "field": "end_state"}]


def test_split_prompt_segments_tolerates_missing_outer_brackets():
    assert split_prompt_segments("画面构图：近景 + 技术参数：85mm") == [
        "画面构图：近景",
        "技术参数：85mm",
    ]


def test_split_prompt_segments_ignores_plus_without_spaces():
    # 段内容里的「A+B」不是段分隔符；一侧带空白仍然算分隔。
    assert split_prompt_segments("[光影几何与大气效果：冷蓝A+B主调]") == [
        "光影几何与大气效果：冷蓝A+B主调"
    ]
    assert split_prompt_segments("[画面构图：近景] +[技术参数：85mm]") == [
        "画面构图：近景",
        "技术参数：85mm",
    ]


def test_split_prompt_segments_keeps_plus_between_character_cards():
    prompt = (
        "[画面构图：全景] + "
        "[角色卡/主体描述：[周浩_职场装: 青年] + [林总_职业装: 高管]] + "
        "[主体/人物空间与互动关系：两人对峙] + "
        "[极具体的微表情、主体状态或关键视觉信息：两人僵住] + "
        "[明确的场景环境元素与前景/背景道具：电梯门] + "
        "[光影几何与大气效果：冷白顶光] + "
        "[视觉风格/质感：写实] + "
        "[技术参数：35mm]"
    )
    segments = split_prompt_segments(prompt)
    assert len(segments) == SHOT_PROMPT_SEGMENT_COUNT
    assert "[周浩_职场装: 青年]" in segments[1]
    assert "[林总_职业装: 高管]" in segments[1]


def test_classify_segments_follows_position():
    roles = classify_segments(_shot_prompt(), SHOT_SEGMENT_ORDER)
    assert roles == [
        "composition",
        "character_card",
        "spatial",
        "micro",
        "environment",
        "lighting",
        "style",
        "technical",
    ]


def test_character_ids_read_from_card():
    assert character_ids_in_card(CARD) == ["沈昭昭_现代"]
    assert character_ids_in_card("[肖铎_古装: 二十岁男性] [沈昭昭_现代: 28岁女性]") == [
        "肖铎_古装",
        "沈昭昭_现代",
    ]


# --------------------------------------------------------------------------- #
# 真机首次生成暴露的两个缺陷（2026-09-18，12 行真实输出）
#
# 那一次的报告把 4 条 issue 全算在模型头上，实际**全部是这两条自身缺陷产生的**：
# 存储下来的 12 行本身是一致的（林晚的角色卡逐字相同、邮差的卡只出现一次）。
# 所以这一组用例固定的是「不许把自己的比较方式错误报成模型的错」。
# --------------------------------------------------------------------------- #

# 真机第 1 行：不出现角色的空镜。模型按系统提示词写了第 2 段的替代写法。
NO_CHARACTER_ROW_SEGMENT = "[主体/核心对象描述：一栋六层老旧公寓楼立面，砖墙被雨水浸出深色水痕]"
# 真机第 6 行：一张卡片段里装两张卡（分隔符是 ` / `）。
TWO_CARD_SEGMENT = f"[角色卡/主体描述：{CARD} / [邮差_现代: 45岁男性，深色鸭舌帽压得很低遮住眉眼]]"


def _row_with_card(shot_no: int, card_segment: str, duration: int = 4) -> dict:
    """用给定的第 2 段正文拼一行 8 段式。"""

    return {
        "shot_no": shot_no,
        "duration": duration,
        "visual_description": f"第 {shot_no} 镜",
        "shot_prompt": " + ".join(
            [
                "[画面构图：近景，平视]",
                card_segment,
                "[主体/人物空间与互动关系：她独坐桌前]",
                "[极具体的微表情：眼下发青]",
                "[明确的场景环境元素：深夜书房]",
                "[光影几何与大气效果：冷蓝主调]",
                STYLE,
                TECH,
            ]
        ),
        "video_motion_prompt": _motion_prompt(duration=f"{duration}s"),
    }


def test_cardless_shot_segment_is_recognized_not_flagged():
    """`主体/核心对象描述` 是无角色镜头的合法第 2 段，不是段序错误。"""

    report = validate_script_rows([_row_with_card(1, NO_CHARACTER_ROW_SEGMENT)])
    assert "script.shot_prompt.order.v1" not in _rules(report)
    assert report.issues == []


def test_two_card_row_is_not_a_false_positive_when_both_cards_match():
    """双角色行不能因为「整段」变长就被判成角色卡不一致。"""

    rows = [
        _row_with_card(1, f"[角色卡/主体描述：{CARD}]"),
        _row_with_card(2, TWO_CARD_SEGMENT),
    ]
    report = validate_script_rows(rows)
    assert "script.character_card.verbatim.v1" not in _rules(report)
    assert report.issues == []
    # 修复器对一张干净的表必须什么都不做。
    assert repair_script_rows(rows).issues == []


def test_repair_does_not_delete_the_other_card_in_a_two_card_row():
    """**数据丢失回归**：只改不一致的那一张卡，同段里的另一张必须原样留着。

    旧实现按整段替换，会把双角色行换单角色行的段——邮差的角色卡整张消失，
    且报告还会写「已修」。这条用例是那个缺陷的墓碑。
    """

    broken_two_card = (
        f"[角色卡/主体描述：{OTHER_CARD} / [邮差_现代: 45岁男性，深色鸭舌帽压得很低遮住眉眼]]"
    )
    rows = [
        _row_with_card(1, f"[角色卡/主体描述：{CARD}]"),
        _row_with_card(2, broken_two_card),
    ]
    report = repair_script_rows(rows)

    repaired = report.rows[1]["shot_prompt"]
    # 林晚的卡被改回逐字一致。
    assert CARD in repaired
    assert OTHER_CARD not in repaired
    # 邮差的卡一个字都不能少。
    assert "邮差_现代" in repaired
    assert "深色鸭舌帽压得很低遮住眉眼" in repaired
    # 后续校验必须干净（不能修完还报）。
    assert report.blocking == []
    assert repair_script_rows(report.rows).issues == []


def test_repair_unifies_only_the_offending_character_across_rows():
    """A 漂了、B 没漂：只改 A，B 逐字保留。"""

    rows = [
        _row_with_card(1, f"[角色卡/主体描述：{CARD} / [邮差_现代: 45岁男性]]"),
        _row_with_card(2, f"[角色卡/主体描述：{OTHER_CARD} / [邮差_现代: 45岁男性]]"),
    ]
    report = repair_script_rows(rows)
    assert report.fixed_count == 1
    first = report.rows[0]["shot_prompt"]
    second = report.rows[1]["shot_prompt"]
    assert CARD in first and CARD in second
    assert "邮差_现代: 45岁男性" in first and "邮差_现代: 45岁男性" in second
    assert report.blocking == []


# --------------------------------------------------------------------------- #
# 规则：一镜一行能判出来的
# --------------------------------------------------------------------------- #


def test_segment_count_rule_flags_short_prompt():
    report = validate_script_rows([_row(shot_prompt=" + ".join(["[画面构图：近景]", CARD]))])
    assert "script.shot_prompt.segments.v1" in _rules(report)
    assert report.blocking


def test_order_rule_flags_swapped_segments():
    # 第 7/8 段被写到第 1/2 段的位置：段数对，段序错。
    prompt = " + ".join([STYLE, TECH, "[画面构图：近景]", CARD,
                         "[主体/人物空间与互动关系：她独坐]", "[极具体的微表情：眼下发青]",
                         "[明确的场景环境元素：办公室]", "[光影几何与大气效果：冷蓝主调]"])
    report = validate_script_rows([_row(shot_prompt=prompt)])
    assert "script.shot_prompt.order.v1" in _rules(report)


def test_character_card_verbatim_rule_flags_paraphrase():
    rows = [_row(shot_no=1), _row(shot_no=2, shot_prompt=_shot_prompt(card=OTHER_CARD))]
    report = validate_script_rows(rows)
    issue = next(i for i in report.issues if i.rule_id == "script.character_card.verbatim.v1")
    assert issue.severity == "blocking"
    assert issue.detail["character"] == "沈昭昭_现代"
    assert issue.shot_no == "2"


def test_style_remains_shared_but_shot_optics_can_differ():
    rows = [
        _row(shot_no=1),
        _row(shot_no=2, shot_prompt=_shot_prompt(style=OTHER_STYLE, technical="[技术参数：24mm镜头，f/8，深景深]")),
    ]
    report = validate_script_rows(rows)
    assert "script.style.singleton.v1" in _rules(report)
    # 风格需统一，但广角空间镜头不应被迫使用近景的人像参数。
    assert "script.technical.singleton.v1" not in _rules(report)
    assert "script.technical.singleton.v1" not in report.rules_checked


def test_camera_rule_flags_two_primary_moves():
    row = _row(video_motion_prompt=_motion_prompt(camera="镜头前推，随后环绕拍摄"))
    report = validate_script_rows([row])
    issue = next(i for i in report.issues if i.rule_id == "script.camera.single.v1")
    assert set(issue.detail["matched"]) == {"镜头前推", "环绕拍摄"}
    assert issue.severity == "advisory"
    assert not report.blocking


def test_camera_rule_accepts_a_single_table_move():
    report = validate_script_rows([_row(video_motion_prompt=_motion_prompt(camera="镜头后移"))])
    assert "script.camera.single.v1" not in _rules(report)


def test_camera_vocabulary_is_injectable_for_offline_tests():
    row = _row(video_motion_prompt=_motion_prompt(camera="推轨"))
    with_names = validate_script_rows([row], camera_names=("推轨",))
    assert "script.camera.single.v1" not in _rules(with_names)
    without_names = validate_script_rows([row], camera_names=())
    assert "script.camera.single.v1" not in _rules(without_names)


def test_motion_segment_count_rule():
    report = validate_script_rows([_row(video_motion_prompt="[时长：5s]")])
    assert "script.motion.segments.v1" in _rules(report)
    assert MOTION_PROMPT_SEGMENT_COUNT == 6


def test_duration_mismatch_is_advisory():
    report = validate_script_rows([_row(duration=5, video_motion_prompt=_motion_prompt(duration="4.0s"))])
    issue = next(i for i in report.issues if i.rule_id == "script.motion.duration_match.v1")
    assert issue.severity == "advisory"
    assert issue.detail == {"stated": 4.0, "declared": 5.0}


def test_duration_accepts_seconds_unit():
    report = validate_script_rows([_row(duration=4, video_motion_prompt=_motion_prompt(duration="4秒"))])
    assert "script.motion.duration_match.v1" not in _rules(report)


def test_reference_budget_flags_gap_and_overflow():
    gap = validate_script_rows([_row(reference="@图片1 + 图片3")])
    assert "script.reference.budget.v1" in _rules(gap)

    overflow = validate_script_rows([_row(reference=" ".join(f"图片{i}" for i in range(1, 11)))])
    assert "script.reference.budget.v1" in _rules(overflow)


def test_reference_budget_ignores_plain_text_numbers():
    report = validate_script_rows([_row(shot_prompt=_shot_prompt(
        composition="[画面构图：85mm 视角，平视机位]"
    ))])
    # 「85mm」不是参考图编号；`图片N` 才是。
    assert "script.reference.budget.v1" not in _rules(report)


def test_shot_no_sequence_flags_gap():
    rows = [_row(shot_no=1), _row(shot_no=3)]
    report = validate_script_rows(rows)
    issue = next(i for i in report.issues if i.rule_id == "script.shot_no.sequence.v1")
    assert issue.severity == "advisory"
    assert issue.detail == {"expected": 2, "actual": 3}


def test_clean_table_reports_nothing():
    report = validate_script_rows([_row(shot_no=1), _row(shot_no=2)])
    assert report.issues == []
    assert report.as_dict()["blocking_count"] == 0
    assert report.as_dict()["schema"] == SCRIPT_CONTRACT_SCHEMA


# --------------------------------------------------------------------------- #
# 修复：只修机械可修的四类
# --------------------------------------------------------------------------- #


def test_repair_unifies_card_style_and_duration_preserving_shot_optics():
    second_technical = "[技术参数：24mm镜头，f/8，深景深]"
    rows = [
        _row(shot_no=1, duration=5),
        _row(
            shot_no=2,
            duration=4,
            shot_prompt=_shot_prompt(card=OTHER_CARD, style=OTHER_STYLE, technical=second_technical),
            video_motion_prompt=_motion_prompt(duration="3.0s"),
        ),
    ]
    report = repair_script_rows(rows)

    repaired_first = split_prompt_segments(report.rows[0]["shot_prompt"])
    repaired_second = split_prompt_segments(report.rows[1]["shot_prompt"])
    assert repaired_first[1] == repaired_second[1] == f"角色卡/主体描述：{CARD}"
    # 解析会剥掉最外层方括号，所以这里比对的是剥掉括号后的段正文。
    assert repaired_first[6] == repaired_second[6] == STYLE[1:-1]
    assert repaired_first[7] == TECH[1:-1]
    assert repaired_second[7] == second_technical[1:-1]

    # 时长向时长列看齐，且单位写法不被改坏（曾经出现过 `5ss`）。
    assert split_prompt_segments(report.rows[1]["video_motion_prompt"])[-1] == "时长：4s"

    assert {issue.rule_id for issue in report.issues if issue.fixed} == {
        "script.character_card.verbatim.v1",
        "script.style.singleton.v1",
        "script.motion.duration_match.v1",
    }
    assert report.blocking == []


def test_repair_does_not_rewrite_a_differently_spelled_but_equal_duration():
    """`4` 与 `4.0s` 是同一个数，不该为了统一写法去动用户的文本。"""

    rows = [_row(duration=4, video_motion_prompt=_motion_prompt(duration="4.0s"))]
    report = repair_script_rows(rows)
    assert report.issues == []
    assert split_prompt_segments(report.rows[0]["video_motion_prompt"])[-1] == "时长：4.0s"


def test_repair_preserves_original_unit_writing():
    rows = [_row(duration=4, video_motion_prompt=_motion_prompt(duration="3秒"))]
    report = repair_script_rows(rows)
    assert split_prompt_segments(report.rows[0]["video_motion_prompt"])[-1] == "时长：4秒"


def test_repair_leaves_unfixable_issues_alone():
    # 段数不足与一镜多运镜都需要人来决定，机器只报不改。
    rows = [
        _row(shot_no=1, video_motion_prompt=_motion_prompt(camera="镜头前推，同时环绕拍摄")),
        _row(shot_no=2, shot_prompt="[画面构图：特写]"),
    ]
    report = repair_script_rows(rows)
    remaining = {issue.rule_id for issue in report.blocking}
    assert "script.camera.single.v1" in {issue.rule_id for issue in report.advisory}
    assert "script.shot_prompt.segments.v1" in remaining
    # 未修复的行不该被改写。
    assert report.rows[1]["shot_prompt"] == "[画面构图：特写]"


def test_repair_does_not_touch_positional_identity_fields():
    rows = [_row(shot_id="SHOT-A", shot_no=1), _row(shot_id="SHOT-B", shot_no=9)]
    report = repair_script_rows(rows)
    assert [row["shot_id"] for row in report.rows] == ["SHOT-A", "SHOT-B"]
    # 镜号不连续只是提醒，不去重排——身份由服务端分配（T-047）。
    assert [row["shot_no"] for row in report.rows] == [1, 9]


def test_repair_is_idempotent():
    rows = [_row(shot_no=1), _row(shot_no=2, shot_prompt=_shot_prompt(card=OTHER_CARD))]
    once = repair_script_rows(rows)
    twice = repair_script_rows(once.rows)
    assert twice.issues == []
    assert [row["shot_prompt"] for row in twice.rows] == [
        row["shot_prompt"] for row in once.rows
    ]


def test_repair_handles_empty_and_missing_rows():
    assert repair_script_rows([]).rows == []
    assert repair_script_rows([{"shot_no": 1}]).blocking == []
    # 只有一行、且角色卡只有一份时，没有"首次出现"可对齐，不该报错。
    single = repair_script_rows([_row()])
    assert single.blocking == []


def test_keyframe_duplicate_plan_reports_reason_and_preserves_alternate_purpose():
    row = _row(
        start_state="站在门边",
        keyframe_plan=[
            {"role": "action_state", "state": "站在门边", "purpose": "", "required": True},
            {"role": "ending_state", "state": "转身离开", "purpose": "锁定出口方向", "required": False},
            {"role": "ending_state", "state": "转身离开", "purpose": "锁定出口方向", "required": True},
            {"role": "spatial_reveal", "state": "转身离开", "purpose": "锁定与门的间距", "required": False},
        ],
    )
    report = validate_script_rows([row])
    issues = [issue for issue in report.issues if issue.rule_id == "script.keyframe.duplicate_plan.v1"]
    assert len(issues) == 2
    assert issues[0].detail["reason"] == "opening_state"
    assert issues[1].detail["duplicate_of"] == 1
    assert "真实画面差异未检查" in issues[1].message

    repaired = repair_script_rows([row])
    plan = repaired.rows[0]["keyframe_plan"]
    assert len(plan) == 2
    assert plan[0]["required"] is True
    assert plan[1]["purpose"] == "锁定与门的间距"
    fixed = [issue for issue in repaired.issues if issue.rule_id == "script.keyframe.duplicate_plan.v1"]
    assert len(fixed) == 2 and all(issue.fixed for issue in fixed)
    assert not any(issue.rule_id == "script.keyframe.duplicate_plan.v1" and not issue.fixed for issue in repaired.issues)
    assert row["keyframe_plan"][1]["required"] is False
    assert len(row["keyframe_plan"]) == 4
    assert repair_script_rows(repaired.rows).fixed_count == 0


def test_keyframe_plan_preserves_new_purposes_roles_and_signed_geometry():
    plan = [
        {"role": "contact_state", "state": "人物x=-2.5，右手握栏杆", "purpose": ""},
        {"role": "action_state", "state": "人物x=-2.5，右手握栏杆", "purpose": "从侧面揭示支撑关系"},
        {"role": "action_state", "state": "人物x=2.5，右手握栏杆", "purpose": ""},
        {"role": "ending_state", "state": "人物x=2.5，右手松开栏杆", "purpose": ""},
    ]
    row = _row(start_state=plan[0]["state"], keyframe_plan=plan)
    assert repair_script_rows([row]).rows[0]["keyframe_plan"] == plan
    assert not any(issue.rule_id == "script.keyframe.duplicate_plan.v1" for issue in validate_script_rows([row]).issues)


def test_keyframe_plan_preserves_distinct_views_and_refuses_incomplete_independent_input():
    item = {"role": "spatial_reveal", "generation_strategy": "independent", "state": "女孩持剑，敌人化雾",
            "purpose": "揭示两人的位置", "required": True}
    plan = [{**item, "framing": "女孩过肩看向敌人"}, {**item, "framing": "高位俯视两者间距"}]
    row = _row(start_state=item["state"], keyframe_plan=plan)
    report = repair_script_rows([row])
    assert report.rows[0]["keyframe_plan"] == plan
    assert not any(issue.rule_id.startswith("script.keyframe.") for issue in report.issues)
    for patch, reason in (({"framing": "无"}, "缺少景别"), ({"purpose": ""}, "缺少新增信息"),
                          ({"generation_strategy": "unknown"}, "生成方式无效")):
        invalid = [{**plan[0], **patch}]
        report = repair_script_rows([_row(keyframe_plan=invalid)])
        assert report.rows[0]["keyframe_plan"] == invalid
        issues = [issue for issue in report.blocking if issue.rule_id == "script.keyframe.input.v1"]
        assert len(issues) == 1 and reason in issues[0].message


def test_keyframe_legacy_and_explicit_state_edit_have_the_same_duplicate_signature():
    item = {"role": "contact_state", "state": "手握栏杆", "purpose": "锁定支撑", "required": False}
    report = repair_script_rows([_row(keyframe_plan=[item, {**item, "generation_strategy": "state_edit", "required": True}])])
    assert report.rows[0]["keyframe_plan"] == [{**item, "required": True}]


def test_keyframe_refusal_respects_local_rewrite_scope():
    from copy import deepcopy

    item = {"role": "ending_state", "state": "人物已经离开", "purpose": "锁出口方向"}
    rows = [_row(shot_no=index + 1, keyframe_plan=[item, dict(item)]) for index in range(2)]
    original = deepcopy(rows)
    data = {"rows": rows}
    report = enforce_story_script_contract(data, target_index=1)
    assert data["rows"][0] == original[0] and len(data["rows"][1]["keyframe_plan"]) == 1
    issues = [issue for issue in report["issues"] if issue["rule_id"] == "script.keyframe.duplicate_plan.v1"]
    assert {issue["row_index"]: issue["fixed"] for issue in issues} == {0: False, 1: True}
    assert plan_script_contract_repairs(data["rows"], issues) == []


def test_enforce_story_script_contract_writes_repaired_rows_back():
    data = {
        "title": "测试",
        "rows": [
            _row(shot_no=1),
            _row(shot_no=2, shot_prompt=_shot_prompt(card=OTHER_CARD)),
        ],
    }
    report = enforce_story_script_contract(data)
    assert report["fixed_count"] == 1
    cards = [split_prompt_segments(row["shot_prompt"])[1] for row in data["rows"]]
    assert cards[0] == cards[1]


def test_enforce_story_script_contract_is_noop_without_rows():
    assert enforce_story_script_contract({"rows": []}) == {}
    assert enforce_story_script_contract({}) == {}


# --------------------------------------------------------------------------- #
# 生成任务：工艺规则与受限词表必须真的进了提示词
# --------------------------------------------------------------------------- #


def test_story_script_task_carries_craft_rules_and_camera_vocabulary():
    from novelvideo.freezone.text_node import build_freezone_story_script_task
    from novelvideo.production.filmcraft_kb import inject_filmcraft_rules

    task = build_freezone_story_script_task(source_text="她收到一封信。", prompt="")

    assert "工程规则" in task
    # 规则是**动态选中**的，所以断言"命中的每一条都进了任务"，而不是钉住某个条数——
    # 规则库会随其它任务增长（T-056 从 8 条扩到 18 条），任务必须跟着涨。
    selected = inject_filmcraft_rules(node_type="video", params={"shot_count": 2})
    assert selected, "上下文应当命中至少一条工艺规则"
    for rule in selected:
        if rule["rule_id"] in {
            "craft.framing_distance_arc.v1", "craft.dialogue_ratio_budget.v1",
            "craft.standoff_ceiling.v1", "craft.beat_cadence.v1",
            "craft.single_primary_action.v1", "craft.continuity_handoff.v1",
        }:
            assert rule["rule_id"] not in task
            continue
        assert rule["rule_id"] in task, rule["rule_id"]
        assert rule["instruction"] in task

    # 运镜词表：名称与条数都写进任务，模型没有自造词的余地。
    assert "固定镜头" in task and "手持拍摄" in task
    assert "23 条" in task
    # 参考角色的九种声明。
    assert "identity（角色身份锚点）" in task


def test_story_script_task_keeps_source_text_and_user_prompt():
    from novelvideo.freezone.text_node import build_freezone_story_script_task

    task = build_freezone_story_script_task(source_text="源剧本正文", prompt="要竖屏悬疑感")
    assert "源剧本正文" in task
    assert "要竖屏悬疑感" in task


def test_shot_rewrite_task_freezes_card_style_but_allows_motivated_technical_change():
    from novelvideo.freezone.text_node import build_freezone_shot_rewrite_task

    target_technical = "[技术参数：24mm镜头，f/8，深景深]"
    rows = [
        _row(shot_id="SHOT-A", shot_no=1),
        _row(shot_id="SHOT-B", shot_no=2, visual_description="她起身", shot_prompt=_shot_prompt(technical=target_technical)),
    ]
    task = build_freezone_shot_rewrite_task(
        rows=rows, target_index=1, instruction="让她停在原地", source_text=""
    )
    assert "冻结清单" in task
    assert f"第 2 段：[角色卡/主体描述：{CARD}]" in task
    assert f"第 7 段：{STYLE}" in task
    assert f"第 8 段：{target_technical}" not in task
    assert "第 8 段技术参数不是冻结项" in task
    assert f"第 8 段：{TECH}" not in task
    # 必须说清只改哪一行，并点名其余行不许出现在输出里。
    assert "本次要改的就是这一镜" in task
    assert "其余行的内容不得出现在你的输出里" in task
    assert "让她停在原地" in task


@pytest.mark.parametrize("target_card", [
    "[肖铎_古装: 二十岁男性，黑衣]",
    CARD + " / [肖铎_古装: 二十岁男性，黑衣]",
    "空办公室，只有咖啡杯，无人物",
])
def test_shot_rewrite_freezes_target_cast_instead_of_first_shot(target_card):
    from novelvideo.freezone.text_node import build_freezone_shot_rewrite_task

    task = build_freezone_shot_rewrite_task(
        rows=[_row(), _row(shot_prompt=_shot_prompt(card=target_card))],
        target_index=1, instruction="改动作",
    )
    assert f"- 第 2 段：[角色卡/主体描述：{target_card}]" in task


@pytest.mark.asyncio
async def test_shot_rewrite_rejects_missing_cast_segment_without_mutation(monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace
    from novelvideo.freezone import text_node

    rows = [_row()]
    before = deepcopy(rows)

    async def run(_task):
        return SimpleNamespace(output=SimpleNamespace(shot_prompt="[画面构图：远景]"))

    monkeypatch.setattr(text_node, "get_freezone_shot_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    with pytest.raises(ValueError, match="缺少本镜角色卡"):
        await text_node.generate_freezone_shot_rewrite(rows=rows, target_index=0, instruction="改动作")
    assert rows == before


def test_shot_rewrite_freezes_each_cast_member_first_appearance():
    from novelvideo.freezone.text_node import build_freezone_shot_rewrite_task

    other = "[肖铎_古装: 二十岁男性，黑衣]"
    changed = "[肖铎_古装: 老年男性，白衣]"
    task = build_freezone_shot_rewrite_task(
        rows=[_row(), _row(shot_prompt=_shot_prompt(card=other)),
              _row(shot_prompt=_shot_prompt(card=OTHER_CARD + " / " + changed))],
        target_index=2, instruction="改动作",
    )
    assert f"- 第 2 段：[角色卡/主体描述：{CARD} / {other}]" in task


@pytest.mark.asyncio
@pytest.mark.parametrize("target_card", [
    "[肖铎_古装: 二十岁男性，黑衣]",
    CARD + " / [肖铎_古装: 二十岁男性，黑衣]",
    "空办公室，只有咖啡杯，无人物",
])
async def test_shot_rewrite_restores_cast_when_model_substitutes_first_character(monkeypatch, target_card):
    from copy import deepcopy
    from types import SimpleNamespace
    from novelvideo.freezone import text_node

    rows = [_row(), _row(shot_prompt=_shot_prompt(card=target_card))]
    before = deepcopy(rows)

    async def run(_task):
        return SimpleNamespace(output=SimpleNamespace(shot_prompt=_shot_prompt()))

    monkeypatch.setattr(text_node, "get_freezone_shot_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    result, _report = await text_node.generate_freezone_shot_rewrite(
        rows=rows, target_index=1, instruction="改动作",
    )
    assert split_prompt_segments(result[1]["shot_prompt"])[1] == f"角色卡/主体描述：{target_card}"
    assert result[0] == before[0]
    assert rows == before


@pytest.mark.parametrize(
    ("target_index", "message"),
    [(-1, "target_index out of range"), (2, "target_index out of range")],
)
def test_shot_rewrite_task_rejects_out_of_range_index(target_index: int, message: str):
    from novelvideo.freezone.text_node import build_freezone_shot_rewrite_task

    with pytest.raises(ValueError, match=message):
        build_freezone_shot_rewrite_task(
            rows=[_row()], target_index=target_index, instruction="改一下"
        )


@pytest.mark.asyncio
async def test_shot_rewrite_accepts_local_lens_change_without_changing_neighbor(monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace
    from novelvideo.freezone import text_node

    rows = [_row(), _row(shot_no=2)]
    before = deepcopy(rows)
    technical = "[技术参数：24mm镜头，f/8，深景深]"

    async def run(task):
        assert "第 8 段技术参数不是冻结项" in task
        return SimpleNamespace(output=SimpleNamespace(shot_prompt=_shot_prompt(technical=technical)))

    monkeypatch.setattr(text_node, "get_freezone_shot_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    result, _report = await text_node.generate_freezone_shot_rewrite(
        rows=rows, target_index=1, instruction="改用广角深景深，让观众看清人物和远处落点",
    )
    assert split_prompt_segments(result[1]["shot_prompt"])[7] == technical[1:-1]
    assert result[0] == before[0]
    assert rows == before


def test_shot_rewrite_task_requires_rows():
    from novelvideo.freezone.text_node import build_freezone_shot_rewrite_task

    with pytest.raises(ValueError, match="rows is required"):
        build_freezone_shot_rewrite_task(rows=[], target_index=0, instruction="改一下")


def test_shot_rewrite_row_model_cannot_touch_identity_fields():
    """重写结果刻意不含身份与角色图字段：那些由服务端拥有，模型不该有机会改。"""

    from novelvideo.freezone.text_node import FreezoneShotRewriteRow

    fields = set(FreezoneShotRewriteRow.model_fields)
    assert "shot_id" not in fields
    assert "character_image_1" not in fields
    assert "keyframe_index" not in fields
    assert "shot_prompt" in fields and "video_motion_prompt" in fields


@pytest.mark.asyncio
async def test_local_rewrite_repairs_only_target_and_reports_untouched_issues(monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace
    from novelvideo.freezone import text_node
    from novelvideo.freezone.script_contract import script_rows_fingerprint

    original = [
        _row(shot_no=1, shot_id="one"),
        _row(shot_no=2, shot_id="two"),
        _row(shot_no=3, shot_id="three", shot_prompt=_shot_prompt(style=OTHER_STYLE)),
    ]
    before = deepcopy(original)

    async def run(_task):
        return SimpleNamespace(output=SimpleNamespace(
            visual_description="她转身看门", shot_prompt=_shot_prompt(card=OTHER_CARD, style=OTHER_STYLE),
        ))

    monkeypatch.setattr(text_node, "get_freezone_shot_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    result, report = await text_node.generate_freezone_shot_rewrite(rows=original, target_index=1, instruction="转身")
    assert original == before
    assert result[0] == before[0]
    assert result[2] == before[2]
    assert result[1]["shot_id"] == "two"
    assert split_prompt_segments(result[1]["shot_prompt"])[6] == STYLE[1:-1]
    assert report["rows_fingerprint"] == script_rows_fingerprint(result)
    assert all(issue["row_index"] == 1 for issue in report["issues"] if issue["fixed"])
    assert any(issue["row_index"] == 2 and not issue["fixed"] and issue["rule_id"] == "script.style.singleton.v1" for issue in report["issues"])


@pytest.mark.parametrize("scope", [[], [1], [1, 2], [2, 1, 1]])
def test_contract_multi_target_repairs_only_explicit_scope(scope):
    from copy import deepcopy
    from novelvideo.freezone.script_contract import script_rows_fingerprint

    rows = [_row(shot_no=1), *[_row(shot_no=index + 1, shot_prompt=_shot_prompt(style=OTHER_STYLE)) for index in range(1, 4)]]
    before = deepcopy(rows)
    data = {"rows": rows}
    report = enforce_story_script_contract(data, target_indices=scope)
    for index in range(4):
        if index in scope:
            assert split_prompt_segments(data["rows"][index]["shot_prompt"])[6] == STYLE[1:-1]
        else:
            assert data["rows"][index] == before[index]
    assert rows == before
    assert report["rows_fingerprint"] == script_rows_fingerprint(data["rows"])
    assert all(issue["row_index"] in scope for issue in report["issues"] if issue["fixed"])
    assert any(issue["row_index"] == 3 and not issue["fixed"] for issue in report["issues"])


@pytest.mark.parametrize("scope", [[-1], [4], [True], [1.5]])
def test_contract_multi_target_rejects_invalid_scope(scope):
    with pytest.raises(ValueError, match="target_index out of range"):
        enforce_story_script_contract({"rows": [_row()]}, target_indices=scope)


def test_contract_rejects_ambiguous_single_and_multi_scope():
    with pytest.raises(ValueError, match="not both"):
        enforce_story_script_contract({"rows": [_row()]}, target_index=0, target_indices=[0])


# --------------------------------------------------------------------------- #
# 可看性闸门
# --------------------------------------------------------------------------- #
#
# 上面所有规则管的是**对不对**。它们全绿，片子照样可能没法看：实测一部 2 分钟 28 镜的
# 成片，台词镜占 90%（时长口径）、贴身景别 22/28 镜、高潮镜里对手根本不在画面——
# 每一项合同都合规，观众看到的却是"配了插图的广播剧"，而钱已经花完了。
# 这一组用例把那次失败固化成可判定的形状。


def _view_row(
    index: int,
    *,
    dialogue: str = "",
    shot: str = "近景 / 平视",
    duration: int = 4,
    action: str = "她抬眼看屏",
) -> dict:
    return _row(
        shot_no=index,
        duration=duration,
        dialogue=dialogue,
        shot=shot,
        visual_description=action,
        character_action=action,
        content_intent="action",
    )


def _viewability_issues(report) -> list:
    return [issue for issue in report.issues if issue.rule_id.startswith("script.viewability")]


def test_viewability_advises_on_the_radio_play_that_actually_shipped():
    """真机形状：几乎每一镜都在说话，且全是贴身景别。

    这正是《剑炉不熄》第一版：28 镜里 25 镜有台词、22 镜是中景/近景/特写。
    按竞品基线，这两条只报 advisory，由用户决定是否改稿。
    """

    rows = [
        _view_row(
            index + 1,
            dialogue="你不配提他，也不配碰它。" if index < 25 else "",
            shot="中景 / 平视" if index < 22 else "全景 / 平视",
        )
        for index in range(28)
    ]
    report = validate_script_rows(rows)

    advisory = {issue.rule_id for issue in report.advisory}
    assert "script.viewability.dialogue_ratio.v1" in advisory
    assert "script.viewability.framing_mix.v1" in advisory
    assert not [issue for issue in report.blocking if issue.rule_id.startswith("script.viewability")]
    assert report.metrics["dialogue_shot_count"] == 25
    assert report.metrics["portrait_shot_count"] == 22


def test_viewability_accepts_a_shot_driven_script():
    """画面叙事的片子：台词镜不到一半、景别跨档、有动作、有呼吸镜。"""

    rows = [
        _view_row(1, shot="大远景 / 俯角", action="暴雨砸在广场上"),
        _view_row(2, dialogue="交剑。", shot="全景 / 平视", action="他拔剑踏过碎剑"),
        _view_row(3, shot="特写 / 平视", action="剑锋擦过，火星溅起"),
        _view_row(4, dialogue="我不配？", shot="近景 / 平视"),
        _view_row(5, shot="中景 / 俯拍", action="她扑倒护住断剑"),
        _view_row(6, shot="全景 / 仰视", action="血滴进炉心，火柱冲起"),
        _view_row(7, dialogue="这一剑，赔你熄的那炉火。", shot="中近景 / 平视"),
        _view_row(8, shot="特写 / 平视", action="断剑腾起落进掌心"),
    ]
    report = validate_script_rows(rows)

    assert not [issue for issue in _viewability_issues(report) if issue.severity == "blocking"]
    metrics = report.metrics
    assert metrics["dialogue_shot_count"] == 3
    assert metrics["breath_shot_count"] == 5
    assert metrics["action_shot_count"] == 5


def test_viewability_advises_on_a_forty_second_standoff():
    """连续几十秒「有台词、无动作」= 把同一件事说很多遍。

    中间插进一个动作镜，最长静戏段就应当被截断——这正是"对峙里夹着打斗"与"站桩说台词"的区别。
    """

    wall = [
        _view_row(index + 1, dialogue="你怕它认主。", shot="中景 / 平视", action="他站在原地")
        for index in range(10)
    ]
    blocked = validate_script_rows(wall)
    assert any(
        issue.rule_id == "script.viewability.static_standoff.v1"
        and issue.severity == "advisory"
        for issue in blocked.issues
    )

    interrupted = (
        wall[:5]
        + [_view_row(6, shot="特写 / 平视", action="剑锋劈下，火星溅开")]
        + [
            _view_row(index + 7, dialogue="你怕它认主。", shot="中景 / 平视", action="他站在原地")
            for index in range(4)
        ]
    )
    reported = validate_script_rows(interrupted)
    assert (
        reported.metrics["longest_standoff_seconds"]
        < blocked.metrics["longest_standoff_seconds"]
    )


def test_viewability_advises_on_a_script_with_no_physical_events():
    """全是说话、没有任何动作词：打戏只剩台词之间的过场。"""

    rows = [
        _view_row(index + 1, dialogue="你终于来了。", shot="近景 / 平视", action="她坐着")
        for index in range(10)
    ]
    report = validate_script_rows(rows)

    assert any(
        issue.rule_id == "script.viewability.action_share.v1"
        and issue.severity == "advisory"
        for issue in report.issues
    )
    assert report.metrics["action_shot_count"] == 0


def test_viewability_flags_single_framing_family_and_long_takes():
    rows = [
        _view_row(index + 1, dialogue="嗯。", shot="特写 / 平视", duration=8)
        for index in range(VIEWABILITY_MIN_SHOTS)
    ]
    report = validate_script_rows(rows)

    rules = _rules(report)
    assert "script.viewability.framing_mix.v1" in rules
    assert "script.viewability.pacing.v1" in rules
    assert report.metrics["longest_shot_seconds"] == 8
    assert report.metrics["framing_families"] == ["特写"]


def test_viewability_stays_quiet_on_a_table_too_short_to_be_a_film():
    """两三行不是片子：在那个量级上算台词占比与景别多样性只会制造噪音。

    指标照常返回（前端要显示），判定不跑。
    """

    report = validate_script_rows(
        [_view_row(index + 1, dialogue="嗯。", shot="特写 / 平视") for index in range(4)]
    )

    assert _viewability_issues(report) == []
    assert report.metrics["shot_count"] == 4


def test_framing_family_prefers_the_longer_keyword():
    """`大远景` 不能被 `远景` 吃掉，`中近景` 要落到近景档、`中全景` 落到全景档。"""

    from novelvideo.freezone.script_contract import framing_family

    assert framing_family("大远景 / 俯角 30 度") == "大远景"
    assert framing_family("远景 / 平视") == "远景"
    assert framing_family("中全景 / 平视") == "全景"
    assert framing_family("中近景 / 平视略仰") == "近景"
    assert framing_family("大特写") == "特写"
    assert framing_family("特写转中近景") == "特写"
    assert framing_family("") is None


def test_viewability_metrics_are_reported_even_when_clean():
    """指标永远随报告返回：前端要显示"这部片子长什么样"，验收要对账。"""

    report = validate_script_rows(
        [_view_row(1, dialogue="交剑。"), _view_row(2, action="他拔剑")]
    )
    dumped = report.as_dict()

    assert dumped["metrics"]["shot_count"] == 2
    assert dumped["metrics"]["dialogue_shot_count"] == 1
    assert dumped["metrics"]["action_shot_count"] == 1
    assert dumped["metrics"]["dialogue_shot_share"] == 0.5


def test_repair_plan_keeps_row_issues_on_their_existing_rows():
    rows = [_view_row(index + 1) for index in range(3)]
    targets = plan_script_contract_repairs(
        rows,
        [
            {
                "rule_id": "script.camera.single.v1",
                "severity": "blocking",
                "message": "一个镜头写了多个主运镜",
                "row_index": 1,
                "shot_no": "2",
                "field": "video_motion_prompt",
                "fixed": False,
            }
        ],
    )

    assert [target.row_index for target in targets] == [1]
    assert targets[0].rule_ids == ("script.camera.single.v1",)
    assert "只改当前这一镜" in targets[0].instruction
    assert "多个主运镜" in targets[0].instruction


def test_repair_plan_targets_portrait_rows_for_global_framing_issue():
    rows = [
        _view_row(index + 1, shot="中景 / 平视" if index < 8 else "全景 / 平视")
        for index in range(10)
    ]
    targets = plan_script_contract_repairs(
        rows,
        [
            {
                "rule_id": "script.viewability.framing_mix.v1",
                "severity": "blocking",
                "message": "贴身景别占比过高",
                "row_index": -1,
                "shot_no": "",
                "field": "shot",
                "fixed": False,
            }
        ],
    )

    assert targets
    assert len(targets) <= 4
    assert all(target.row_index < 8 for target in targets)
    assert all(
        target.rule_ids == ("script.viewability.framing_mix.v1",)
        for target in targets
    )


def test_repair_plan_ignores_rules_the_text_rewrite_cannot_change():
    targets = plan_script_contract_repairs(
        [_view_row(1)],
        [
            {
                "rule_id": "script.shot_no.sequence.v1",
                "severity": "advisory",
                "message": "镜号不连续",
                "row_index": 0,
                "shot_no": "2",
                "field": "shot_no",
                "fixed": False,
            }
        ],
    )

    assert targets == []


def test_repair_plan_is_bounded_for_large_reports():
    rows = [_view_row(index + 1, dialogue="继续。", action="他坐着") for index in range(30)]
    issues = [
        {
            "rule_id": "script.camera.single.v1",
            "severity": "blocking",
            "message": "一个镜头写了多个主运镜",
            "row_index": index,
            "shot_no": str(index + 1),
            "field": "video_motion_prompt",
            "fixed": False,
        }
        for index in range(30)
    ]

    targets = plan_script_contract_repairs(rows, issues)

    assert len(targets) == MAX_REPAIR_TARGETS


@pytest.mark.asyncio
async def test_contract_repair_calls_the_shot_rewriter_only_for_planned_rows(monkeypatch):
    import novelvideo.freezone.text_node as text_node

    calls: list[tuple[int, str]] = []

    async def fake_rewrite(*, rows, target_index, instruction, source_text, model):
        calls.append((target_index, instruction))
        table = [dict(row) for row in rows]
        table[target_index]["visual_description"] = "她抬手把断剑推向炉火"
        return table, {}

    monkeypatch.setattr(text_node, "generate_freezone_shot_rewrite", fake_rewrite)
    rows = [_view_row(index + 1) for index in range(3)]

    rewritten, report = await text_node.generate_freezone_script_contract_repair(
        rows=rows,
        issues=[
            {
                "rule_id": "script.camera.single.v1",
                "severity": "blocking",
                "message": "一个镜头写了多个主运镜",
                "row_index": 1,
                "shot_no": "2",
                "field": "video_motion_prompt",
                "fixed": False,
            }
        ],
    )

    assert [index for index, _ in calls] == [1]
    assert rewritten[1]["visual_description"] == "她抬手把断剑推向炉火"
    assert report["schema"] == SCRIPT_CONTRACT_SCHEMA


def test_story_script_request_accepts_contract_repair_payload():
    from novelvideo.api.schemas import FreezoneStoryScriptGenerateRequest

    request = FreezoneStoryScriptGenerateRequest.model_validate(
        {
            "current_rows": [_view_row(1), _view_row(2)],
            "repair_mode": "script-contract",
            "repair_issues": [
                {
                    "rule_id": "script.viewability.framing_mix.v1",
                    "severity": "blocking",
                    "message": "贴身景别占比过高",
                    "row_index": -1,
                    "shot_no": "",
                    "field": "shot",
                    "fixed": False,
                    "detail": {"portraitShare": 0.82},
                }
            ],
        }
    )

    assert request.repair_mode == "script-contract"
    assert request.repair_issues[0].rule_id == "script.viewability.framing_mix.v1"
    assert request.repair_issues[0].detail == {"portraitShare": 0.82}


def _global_issue(rule_id: str, message: str) -> dict:
    return {
        "rule_id": rule_id,
        "severity": "blocking",
        "message": message,
        "row_index": -1,
        "shot_no": "",
        "field": "shot",
        "fixed": False,
    }


def _row_issue(rule_id: str, row_index: int, *, severity: str = "blocking") -> dict:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "message": "一个镜头写了多个主运镜",
        "row_index": row_index,
        "shot_no": str(row_index + 1),
        "field": "video_motion_prompt",
        "fixed": False,
    }


def test_repair_plan_spreads_global_issues_instead_of_copying_all_of_them():
    """五条全片问题不能同时压在同一个镜头上，也不能复制到每条指令里。

    复制会造成两次实际损失：用户为同一段全片说明反复付费，模型被要求在一镜里
    同时满足「改景别」「删台词」「加动作」「变节奏」四条互相拉扯的要求。
    """

    rows = [_view_row(index + 1, dialogue="你必须现在告诉我真相。") for index in range(10)]
    issues = [
        _global_issue(rule_id, f"{rule_id} 触发")
        for rule_id in GLOBAL_VIEWABILITY_RULE_ORDER
    ]

    targets = plan_script_contract_repairs(rows, issues)

    per_row_global: list[list[str]] = []
    global_hits: dict[str, int] = {}
    for target in targets:
        hit = [rule_id for rule_id in target.rule_ids if rule_id in GLOBAL_VIEWABILITY_RULE_ORDER]
        per_row_global.append(hit)
        for rule_id in hit:
            global_hits[rule_id] = global_hits.get(rule_id, 0) + 1

    assert all(len(hit) <= 1 for hit in per_row_global)
    assert len(targets) <= MAX_GLOBAL_REPAIR_TARGETS
    assert set(global_hits) == set(GLOBAL_VIEWABILITY_RULE_ORDER[:MAX_GLOBAL_REPAIR_TARGETS])
    assert all(count == 1 for count in global_hits.values())


def test_repair_plan_keeps_row_level_blockers_ahead_of_global_representatives():
    """有硬伤的行优先于「统计上代表性」的行。

    按问题条数排序时，被塞了整片问题的代表镜头会挤掉真正违反行级规则的行，
    让用户点了「优化」却修不到那条硬阻塞。
    """

    rows = [_view_row(index + 1, shot="中景 / 平视") for index in range(30)]
    issues = [_row_issue("script.camera.single.v1", index) for index in range(12)]
    issues.append(
        _global_issue("script.viewability.framing_mix.v1", "贴身景别占比过高")
    )

    targets = plan_script_contract_repairs(rows, issues)

    assert len(targets) == MAX_REPAIR_TARGETS
    assert [target.row_index for target in targets] == list(range(MAX_REPAIR_TARGETS))
    assert all(
        "script.viewability.framing_mix.v1" not in target.rule_ids for target in targets
    )


def test_repair_plan_does_not_assign_global_issue_to_a_row_level_blocker_row():
    """全片问题不准占用已经有行级硬伤的镜头，否则那一镜的指令会失焦。"""

    rows = [
        _view_row(1, shot="近景 / 平视"),
        _view_row(2, shot="全景 / 平视"),
        _view_row(3, shot="全景 / 平视"),
    ]
    issues = [
        _row_issue("script.camera.single.v1", 0),
        _global_issue("script.viewability.framing_mix.v1", "贴身景别占比过高"),
    ]

    targets = plan_script_contract_repairs(rows, issues)

    assert [target.row_index for target in targets] == [0]
    assert targets[0].rule_ids == ("script.camera.single.v1",)


@pytest.mark.asyncio
async def test_contract_repair_keeps_successful_rows_when_one_rewrite_fails(monkeypatch):
    """一镜改失败不能把已经改好的镜一起丢掉——那些是已经付过费的真实调用。"""

    import novelvideo.freezone.text_node as text_node

    calls: list[int] = []

    async def fake_rewrite(*, rows, target_index, instruction, source_text, model):
        calls.append(target_index)
        if target_index == 2:
            raise RuntimeError("上游 503 auth_concurrency_limit")
        table = [dict(row) for row in rows]
        table[target_index]["visual_description"] = f"第 {target_index + 1} 镜已重写"
        return table, {}

    monkeypatch.setattr(text_node, "generate_freezone_shot_rewrite", fake_rewrite)
    rows = [_view_row(index + 1) for index in range(3)]

    rewritten, report = await text_node.generate_freezone_script_contract_repair(
        rows=rows,
        issues=[_row_issue("script.camera.single.v1", index) for index in range(3)],
    )

    assert calls == [0, 1, 2]
    assert rewritten[0]["visual_description"] == "第 1 镜已重写"
    assert rewritten[1]["visual_description"] == "第 2 镜已重写"
    assert report["repair"]["schema"] == "village_script_contract_repair.v1"
    assert report["repair"]["targets"] == 3
    assert report["repair"]["applied"] == 2
    assert report["repair"]["failed"] == 1
    assert report["repair"]["failures"][0]["row_index"] == 2
    assert "503" in report["repair"]["failures"][0]["error"]


@pytest.mark.asyncio
async def test_contract_repair_reports_progress_for_every_target(monkeypatch):
    """串行重写每镜几十秒；任务层需要逐镜心跳，否则界面只有一条冻结的 25%。"""

    import novelvideo.freezone.text_node as text_node

    async def fake_rewrite(*, rows, target_index, instruction, source_text, model):
        table = [dict(row) for row in rows]
        table[target_index]["visual_description"] = "改过了"
        return table, {}

    monkeypatch.setattr(text_node, "generate_freezone_shot_rewrite", fake_rewrite)
    rows = [_view_row(index + 1) for index in range(3)]
    stages: list[tuple[int, int, int]] = []

    await text_node.generate_freezone_script_contract_repair(
        rows=rows,
        issues=[_row_issue("script.camera.single.v1", index) for index in range(3)],
        on_target=lambda position, total, target: stages.append(
            (position, total, target.row_index)
        ),
    )

    assert stages == [(1, 3, 0), (2, 3, 1), (3, 3, 2)]


@pytest.mark.asyncio
async def test_contract_repair_fails_loudly_when_no_row_could_be_rewritten(monkeypatch):
    """全部失败时不能返回一份「成功」的结果让界面报绿灯。"""

    import novelvideo.freezone.text_node as text_node

    async def fake_rewrite(*, rows, target_index, instruction, source_text, model):
        raise RuntimeError("模型网关不可用")

    monkeypatch.setattr(text_node, "generate_freezone_shot_rewrite", fake_rewrite)
    rows = [_view_row(index + 1) for index in range(2)]

    with pytest.raises(ValueError, match="所有目标镜头都改写失败"):
        await text_node.generate_freezone_script_contract_repair(
            rows=rows,
            issues=[_row_issue("script.camera.single.v1", index) for index in range(2)],
        )


@pytest.mark.asyncio
async def test_contract_repair_reports_which_global_issues_were_deferred(monkeypatch):
    """行级硬伤占满目标时，被挤下的全片问题要么本轮说清，要么用户只会觉得没生效。"""

    import novelvideo.freezone.text_node as text_node

    async def fake_rewrite(*, rows, target_index, instruction, source_text, model):
        table = [dict(row) for row in rows]
        table[target_index]["visual_description"] = "改过了"
        return table, {}

    monkeypatch.setattr(text_node, "generate_freezone_shot_rewrite", fake_rewrite)
    rows = [_view_row(index + 1) for index in range(12)]
    issues = [_row_issue("script.camera.single.v1", index) for index in range(12)]
    issues.append(
        _global_issue("script.viewability.framing_mix.v1", "贴身景别占比过高")
    )
    issues.append(
        _global_issue("script.viewability.dialogue_ratio.v1", "台词镜占时长过高")
    )

    _rewritten, report = await text_node.generate_freezone_script_contract_repair(
        rows=rows,
        issues=issues,
    )

    assert report["repair"]["targets"] == MAX_REPAIR_TARGETS
    assert report["repair"]["applied"] == MAX_REPAIR_TARGETS
    assert report["repair"]["deferred_rule_ids"] == [
        "script.viewability.dialogue_ratio.v1",
        "script.viewability.framing_mix.v1",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["fixed", "unchanged", "new_blocker"])
async def test_contract_repair_rechecks_candidates_and_stops(monkeypatch, outcome):
    import novelvideo.freezone.text_node as text_node

    invalid_motion = "[运镜轨迹：镜头前推]"
    rows = [_row(video_motion_prompt=invalid_motion), _row(shot_no=2)]
    before = repair_script_rows(rows)
    assert len(before.blocking) == 1
    calls = []

    async def fake_rewrite(**kwargs):
        calls.append(kwargs["target_index"])
        candidate = [dict(row) for row in kwargs["rows"]]
        if outcome != "unchanged":
            candidate[0]["video_motion_prompt"] = _motion_prompt()
        if outcome == "new_blocker":
            candidate[1]["video_motion_prompt"] = invalid_motion
        return candidate, {}

    monkeypatch.setattr(text_node, "generate_freezone_shot_rewrite", fake_rewrite)
    result, report = await text_node.generate_freezone_script_contract_repair(
        rows=rows, issues=before.as_dict()["issues"], max_passes=3,
    )
    assert calls == [0]
    assert report["repair"]["passes"] == 1
    assert report["repair"]["rejected"] == int(outcome == "new_blocker")
    assert report["repair"]["applied"] == int(outcome == "fixed")
    assert report["repair"]["needs_more_repair"] == (outcome != "fixed")
    assert bool(repair_script_rows(result).blocking) == (outcome != "fixed")
    if outcome != "fixed":
        assert result == before.rows


@pytest.mark.asyncio
async def test_contract_repair_rejects_increased_advisories(monkeypatch):
    import novelvideo.freezone.text_node as text_node

    rows = [
        _row(scene_tags="办公室", shot="远景 / 平视"),
        _row(shot_no=2, scene_tags="办公室", shot="特写 / 平视"),
    ]
    before = repair_script_rows(rows)

    async def fake_rewrite(**kwargs):
        candidate = [dict(row) for row in kwargs["rows"]]
        candidate[1]["shot"] = "远景 / 平视"
        return candidate, {}

    candidate, _ = await fake_rewrite(rows=before.rows)
    assert len(repair_script_rows(candidate).advisory) > len(before.advisory)
    monkeypatch.setattr(text_node, "generate_freezone_shot_rewrite", fake_rewrite)
    result, report = await text_node.generate_freezone_script_contract_repair(
        rows=rows, issues=[_row_issue("script.camera.single.v1", 1)],
    )
    assert result == before.rows
    assert report["repair"]["rejected"] == 1


@pytest.mark.parametrize("intent", ["product", "dialogue", "lyrical", "documentary", "tutorial", "other", ""])
def test_non_action_content_does_not_receive_fight_or_global_ratio_instructions(intent):
    rows = [
        {**_view_row(index + 1, dialogue="这里需要完整讲解。", duration=10), "content_intent": intent}
        for index in range(10)
    ]
    report = validate_script_rows(rows)
    assert not _viewability_issues(report)
    assert report.metrics["action_shot_count"] == 0
    assert report.metrics["longest_shot_seconds"] == 10
    assert report.metrics["dialogue_seconds_share"] == 1


def test_one_action_scene_does_not_make_a_mixed_film_action_focused():
    rows = [{**_view_row(index + 1), "content_intent": "product"} for index in range(10)]
    rows[0]["content_intent"] = "action"
    assert not _viewability_issues(validate_script_rows(rows))


def test_narrative_content_keeps_story_checks_without_required_fighting():
    rows = [{**_view_row(index + 1), "content_intent": "narrative"} for index in range(10)]
    report = validate_script_rows(rows)
    assert "script.viewability.framing_mix.v1" in _rules(report)
    assert "script.viewability.action_share.v1" not in _rules(report)


def test_generator_schema_requests_semantic_content_intent_and_rewrite_preserves_context():
    from novelvideo.freezone.text_node import (
        FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT,
        build_freezone_shot_rewrite_task,
    )
    from novelvideo.ports.story_script import FreezoneStoryScriptRow

    assert "content_intent" in FreezoneStoryScriptRow.model_json_schema()["properties"]
    assert "Explicit user intent takes priority" in FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT
    task = build_freezone_shot_rewrite_task(
        rows=[{**_row(), "content_intent": "product"}], target_index=0, instruction="优化构图",
    )
    assert "content_intent：product" in task
    assert "0.3–0.8" not in task
    assert "打戏节拍" in task
