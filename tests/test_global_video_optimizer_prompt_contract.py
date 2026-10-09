from novelvideo.agents.global_video_optimizer import (
    GlobalVideoPromptOptimizer,
    format_structured_continuity_context,
    resolve_video_strategy_capabilities,
    sanitize_video_prompt_marker_colors,
)
import pytest
from novelvideo.production.shot_contract import build_shot_contract
from novelvideo.agents.global_video_optimizer import format_shot_recipe_context
from novelvideo.agents.global_video_optimizer import (
    build_deterministic_motion_prompt,
    lint_motion_prompt,
)


def _character_map():
    return {
        "#FF00FF FLUORESCENT MAGENTA": {
            "identity": "厉无赦_青年时期",
            "appearance": "男性，黑色短发，古铜肤色，黑色战衣",
            "body_type": "健壮魁梧",
        },
        "#00FFFF FLUORESCENT CYAN": {
            "identity": "应苍极_青年时期",
            "appearance": "男性，银白长发，冷白肤色，九龙金曜重甲",
            "body_type": "高挑挺拔",
        },
    }


def test_identity_marker_context_uses_canonical_appearance_not_marker_colour():
    optimizer = GlobalVideoPromptOptimizer()
    mapping = _character_map()

    identity_map = optimizer._build_identity_to_color(mapping)

    assert "FLUORESCENT" not in identity_map["厉无赦_青年时期"]
    assert "黑色战衣" in identity_map["厉无赦_青年时期"]
    assert "FLUORESCENT" not in identity_map["应苍极_青年时期"]


def test_marker_colour_leak_is_rewritten_only_when_attached_to_a_person():
    prompt = (
        "粉色躯体的年轻男子向前挥刃，蓝色躯体的年轻男子抬手格挡，"
        "蓝色光芒在两人之间爆开。"
    )

    sanitized = sanitize_video_prompt_marker_colors(prompt, _character_map())

    assert "粉色躯体" not in sanitized
    assert "蓝色躯体" not in sanitized
    assert "黑色战衣" in sanitized
    assert "九龙金曜重甲" in sanitized
    assert "蓝色光芒" in sanitized


def test_marker_colour_rewrite_handles_human_shadow_without_leaving_suffix():
    prompt = "青蓝与粉紫炽热光芒的人影交战。"

    sanitized = sanitize_video_prompt_marker_colors(prompt, _character_map())

    # This is an energy silhouette, not a marker-coloured body.  It must stay
    # intact rather than becoming the malformed ``人物影`` phrase.
    assert sanitized == prompt


def test_glow_giant_is_kept_as_scene_effect():
    prompt = "蓝色光芒巨人从阵中浮现，粉紫辉光巨人站在中央，青蓝能量巨人咆哮。"

    sanitized = sanitize_video_prompt_marker_colors(prompt, _character_map())

    assert sanitized == prompt


def test_english_marker_rewrite_does_not_duplicate_article():
    mapping = _character_map()

    assert "a a person" not in sanitize_video_prompt_marker_colors(
        "a cyan figure glows.", mapping
    )
    assert sanitize_video_prompt_marker_colors("a cyan figure glows.", mapping).startswith(
        "a person with"
    )


def test_inserted_canonical_appearance_is_not_reprocessed_by_other_marker():
    mapping = {
        "#FF00FF FLUORESCENT MAGENTA": {
            "identity": "A",
            "appearance": "蓝色躯体，黑衣",
        },
        "#00FFFF FLUORESCENT CYAN": {
            "identity": "B",
            "appearance": "银发白衣",
        },
    }

    sanitized = sanitize_video_prompt_marker_colors("粉色躯体的男子挥手。", mapping)

    assert "蓝色躯体，黑衣" in sanitized
    assert "银发白衣" not in sanitized


def test_appearance_details_is_used_for_production_character_maps():
    mapping = {
        "#FF00FF FLUORESCENT MAGENTA": {
            "identity": "A",
            "appearance_details": "黑色短发，黑色战衣",
            "gender": "男性",
            "body_type": "健壮",
        }
    }

    sanitized = sanitize_video_prompt_marker_colors("粉色躯体的男子挥手。", mapping)

    assert "黑色短发，黑色战衣" in sanitized


def test_blue_white_costume_colour_is_not_treated_as_cyan_marker_alias():
    prompt = "蓝白人物穿着蓝白长袍，蓝白灯光映亮背景。"

    sanitized = sanitize_video_prompt_marker_colors(prompt, _character_map())

    assert sanitized == prompt


def test_structured_continuity_context_preserves_declared_handoff_fields_only():
    context = format_structured_continuity_context(
        {
            "action_start": "手放在门把上",
            "action_mid": "推门并向前迈步",
            "expected_end_state": {"screen_direction": "left_to_right", "prop": "门已打开"},
            "camera_scale": "中近景",
            "camera_direction": "向右跟拍",
            "cut_reason": "动作匹配切",
        }
    )

    assert "动作起点（action_start）：手放在门把上" in context
    assert "动作中段（action_mid）：推门并向前迈步" in context
    assert '预期结束状态（expected_end_state）：{"screen_direction":"left_to_right","prop":"门已打开"}' in context
    assert "运镜方向（camera_direction）：向右跟拍" in context
    assert "动作终点（action_end）" not in context


def test_legacy_wokey_jimeng_profile_keeps_first_last_frame_capability():
    modes, known = resolve_video_strategy_capabilities(
        "newapi_jimeng-seedance-2.5"
    )

    assert known is True
    assert modes == frozenset({"first_frame", "keyframe"})


def test_unknown_newapi_model_does_not_guess_first_last_frame():
    modes, known = resolve_video_strategy_capabilities("newapi_unknown-video")

    assert known is False
    assert modes == frozenset({"first_frame"})


def test_shot_recipe_context_budgets_causal_motion_and_handoff():
    recipe = format_shot_recipe_context(
        {
            "beat_number": 2,
            "generation_duration_seconds": 6,
            "narrative_function": "action",
            "visual_description": "黑衣剑客在雨巷抬刀",
            "action_description": "向前踏步并格挡来袭的刀",
            "camera_motion": "低机位向前跟拍",
        },
        prev_beat={"visual_description": "剑客冲入雨巷"},
        next_beat={"visual_description": "刀锋停在对手肩前"},
    )
    assert "6.0 秒" in recipe
    assert "准备、执行、结果" not in recipe
    assert "物理反馈" in recipe
    assert "结束落点" in recipe
    assert "下一镜视觉目标" in recipe


def test_motion_prompt_quality_gate_replaces_generic_model_answer():
    assert "generic_motion_placeholder" in lint_motion_prompt(
        "角色自然动作，姿态变化，自然镜头运动"
    )
    prompt = build_deterministic_motion_prompt(
        {
            "visual_description": "黑衣剑客站在雨巷中央，刀尖下垂",
            "action_description": "向前踏步并抬刀格挡来袭的刀",
            "camera_motion": "低机位向前跟拍",
            "generation_duration_seconds": 5,
        },
        next_beat={"visual_description": "刀锋停在对手肩前"},
    )
    assert lint_motion_prompt(prompt) == []
    assert "抬刀格挡" in prompt
    assert "刀锋停在对手肩前" in prompt


def test_motion_prompt_quality_gate_requires_start_physics_and_single_camera_path():
    issues = lint_motion_prompt(
        "人物挥刀，镜头先推近再环绕，最后停在对手面前。"
    )

    assert "start_state_missing" in issues
    assert "physical_feedback_missing" in issues
    assert "multiple_camera_paths" in issues


def test_motion_prompt_quality_gate_reopens_legacy_generic_fallbacks():
    issues = lint_motion_prompt(
        "从首帧中人物开始，主体先以可见的重心变化准备，随后完成动作，"
        "接触、发力或位移带来明确的身体和道具反馈；镜头沿主体运动方向平滑跟拍，"
        "最后落在结束构图。"
    )

    assert "generic_motion_placeholder" in issues

    camera_as_action = lint_motion_prompt(
        "从首帧的空旷大厅开始，镜头平滑推近，主体先准备，随后连续完成镜头极速变焦推近，"
        "产生位移反馈，最后落在门前。"
    )
    assert "camera_used_as_primary_action" in camera_as_action


def test_motion_prompt_quality_gate_allows_concrete_camera_and_style_language():
    prompt = (
        "从首帧的中景构图开始，黑衣人物先调整重心再向前抬刀，刀锋接触木门后门板震动、衣摆随惯性摆动；"
        "镜头沿人物方向平滑跟拍并缓慢推近，最后落在刀锋与门缝的近景，画面保持电影感。"
    )

    assert lint_motion_prompt(prompt) == []


def test_closeup_prompt_uses_visible_micro_motion_and_no_invented_props():
    prompt = build_deterministic_motion_prompt(
        {
            "visual_description": "{{厉无赦_青年时期}} 满嘴液体，双目中战意疯狂翻涌，神情狂傲",
            "detected_props_json": '["__NO_PROP__"]',
            "audio_type": "silence",
            "generation_duration_seconds": 4,
        }
    )

    assert "微幅推近" in prompt
    assert "嘴角、下颌与眼神" in prompt
    assert "脚步" not in prompt
    assert "当前道具" not in prompt
    assert "相邻道具" not in prompt
    assert lint_motion_prompt(prompt) == []


def test_dialogue_prompt_does_not_borrow_next_beat_effect_action():
    current = {
        "visual_description": "{{厉无赦_青年时期}} 满脸液体污渍狂意，双目战意疯涌，咬牙厉声质问",
        "audio_type": "dialogue",
        "narration": "连老子一戟都接得这么费劲，也配定天规？！",
        "detected_props_json": '["__NO_PROP__"]',
        "generation_duration_seconds": 5,
    }
    next_beat = {
        "visual_description": "宏观视界中空间剧烈震颤，升维法相与法则维度开始显现",
    }

    prompt = build_deterministic_motion_prompt(current, next_beat=next_beat)

    assert "嘴唇开合和下颌变化沿台词节奏持续到末拍" in prompt
    assert "升维法相" not in prompt
    assert "法则维度" not in prompt
    assert lint_motion_prompt(prompt, current_beat=current, next_beat=next_beat) == []


def test_dialogue_text_is_not_allowed_inside_visual_prompt():
    current = {
        "audio_type": "dialogue",
        "speaker": "厉无赦",
        "narration_segment": "厉无赦低声说：“别回头。”",
    }
    prompt = (
        "从首帧的面部近景开始，镜头微幅推近，人物嘴唇连续开合，"
        "说：别回头，最后落在眼神近景。"
    )

    issues = lint_motion_prompt(prompt, current_beat=current)

    assert "dialogue_text_in_visual_prompt" in issues
    assert "dialogue_marker_in_visual_prompt" in issues


def test_legacy_video_prompt_builder_fallback_never_appends_dialogue_text():
    from novelvideo.agents.video_prompt_builder import VideoPromptBuilder

    prompt = VideoPromptBuilder()._fallback_build(
        5,
        frame_prompt="人物面部近景，抬眼看向画外。",
        narration="别回头。",
        audio_type="dialogue",
        dialogue_line="别回头。",
    )

    assert "别回头" not in prompt
    assert "说：" not in prompt
    assert "口型" in prompt


def test_cross_beat_action_leak_is_rejected_by_quality_gate():
    current = {"visual_description": "人物近景咬牙质问"}
    next_beat = {"visual_description": "升维法相与法则维度开始显现"}
    prompt = (
        "从首帧的人物近景开始，镜头微幅推近，人物连续完成咬牙质问，"
        "身体产生受力反馈，随后升维法相与法则维度开始显现，"
        "最后落在当前人物近景。"
    )

    assert "cross_beat_action_leak" in lint_motion_prompt(
        prompt,
        current_beat=current,
        next_beat=next_beat,
    )


def test_closeup_full_body_choreography_is_rejected_by_quality_gate():
    current = {
        "visual_description": "{{厉无赦_青年时期}} 面部近景，嘴角带血，双目发亮",
        "detected_props_json": '["__NO_PROP__"]',
    }
    prompt = (
        "从首帧的面部近景开始，镜头微幅推近，人物先调整重心，随后迈步挥刀冲刺，"
        "脚步和衣摆产生受力反馈，最后落在面部近景。"
    )

    assert "shot_scale_mismatch" in lint_motion_prompt(prompt, current_beat=current)


@pytest.mark.asyncio
async def test_keyframe_builder_falls_back_from_generic_model_answer(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from PIL import Image

    from novelvideo.agents.keyframe_prompt_builder import KeyframePromptBuilder

    first = tmp_path / "first.png"
    last = tmp_path / "last.png"
    Image.new("RGB", (8, 8), color=(20, 30, 40)).save(first)
    Image.new("RGB", (8, 8), color=(40, 30, 20)).save(last)

    class FakeAgent:
        async def run(self, _items):
            return SimpleNamespace(output="角色自然动作，姿态变化，自然镜头运动")

    builder = KeyframePromptBuilder()
    monkeypatch.setattr(builder, "_get_agent", lambda language="en": FakeAgent())

    result = await builder.build(
        first_frame_path=str(first),
        last_frame_path=str(last),
        narration="人物走进门内",
        visual_description="黑衣人物站在门外，手扶门把",
        next_visual_description="黑衣人物走入门内，门已打开",
    )

    assert "角色自然动作" not in result
    assert "从首帧" in result
    assert "最后" in result
    assert "跟拍" in result


@pytest.mark.asyncio
async def test_keyframe_builder_sanitizes_dialogue_from_successful_prompt(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from PIL import Image

    from novelvideo.agents.keyframe_prompt_builder import KeyframePromptBuilder

    first = tmp_path / "first.png"
    last = tmp_path / "last.png"
    Image.new("RGB", (8, 8), color=(20, 30, 40)).save(first)
    Image.new("RGB", (8, 8), color=(40, 30, 20)).save(last)

    class FakeAgent:
        async def run(self, _items):
            return SimpleNamespace(
                output=(
                    "从首帧门外站姿开始，黑衣人物先抬手按下门把，随后推门向前迈入，"
                    "门板受力回弹、衣摆跟随惯性摆动，镜头沿门缝向前推进，最后落在尾帧门内侧，"
                    "人物说：\"别回头。\""
                )
            )

    builder = KeyframePromptBuilder()
    monkeypatch.setattr(builder, "_get_agent", lambda language="en": FakeAgent())

    result = await builder.build(
        first_frame_path=str(first),
        last_frame_path=str(last),
        narration="",
        visual_description="黑衣人物站在门外",
        next_visual_description="黑衣人物走入门内",
        audio_type="dialogue",
        dialogue_line="别回头。",
    )

    assert "别回头" not in result
    assert "说：" not in result
    assert "镜头沿门缝向前推进" in result


@pytest.mark.asyncio
async def test_optimizer_falls_back_when_relay_returns_invalid_json(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from PIL import Image

    image_path = tmp_path / "beat_01.png"
    Image.new("RGB", (8, 8), color=(20, 30, 40)).save(image_path)

    class FakeAgent:
        async def run(self, _items):
            return SimpleNamespace(output="上游返回了格式错误的普通文本")

    from novelvideo.agents import global_video_optimizer

    optimizer = global_video_optimizer.GlobalVideoPromptOptimizer()
    monkeypatch.setattr(optimizer, "_get_agent", lambda language="en": FakeAgent())
    result = await optimizer.optimize_single_beat(
        beat={
            "beat_number": 1,
            "visual_description": "黑衣剑客站在雨巷中央，刀尖下垂",
            "action_description": "向前踏步并抬刀格挡来袭的刀",
            "camera_motion": "低机位向前跟拍",
            "generation_duration_seconds": 5,
        },
        sketch_image_path=str(image_path),
        character_color_map={},
    )

    assert result["video_mode"] == "first_frame"
    assert "抬刀格挡" in result["prompt"]
    assert lint_motion_prompt(result["prompt"]) == []


@pytest.mark.asyncio
async def test_optimizer_preserves_keyframe_mode_only_for_declared_capability(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from PIL import Image

    image_path = tmp_path / "beat_01.png"
    Image.new("RGB", (8, 8), color=(20, 30, 40)).save(image_path)
    contract = build_shot_contract(
        {
            "shot_id": "S01",
            "duration_seconds": 4,
            "subject": "人物",
            "primary_action": "向前迈步",
            "primary_camera_motion": "向前跟拍",
            "start_state": "人物站在门前",
            "end_state": "人物走入门内",
        }
    )

    class FakeAgent:
        async def run(self, _items):
            return SimpleNamespace(
                output='[{"beat_number": 1, "video_mode": "keyframe", "prompt": "人物向前走入门内，镜头跟拍。"}]'
            )

    from novelvideo.agents import global_video_optimizer

    optimizer = global_video_optimizer.GlobalVideoPromptOptimizer()
    monkeypatch.setattr(optimizer, "_get_agent", lambda language="en": FakeAgent())
    result = await optimizer.optimize_single_beat(
        beat={
            "beat_number": 1,
            "visual_description": "人物站在门前",
            "shot_contract": contract,
        },
        sketch_image_path=str(image_path),
        character_color_map={},
        supported_strategy_modes=frozenset({"first_frame", "keyframe"}),
        allow_keyframe=True,
    )

    assert result["video_mode"] == "keyframe"


# ---------------------------------------------------------------------------
# JEV 复核关键词质检（motion_prompt_lint_overruled）
#
# lint_motion_prompt 按固定词表匹配，同义表达会被误报；误报的代价是丢弃
# 已写好的提示词、白花一次多模态重生成。JEV 只在这些发现全被高置信推翻时
# 才否决 lint；其余一切情况（拿不准、混入确定性发现、判断不可用、开关关）
# 都返回 False，调用方照旧重生成。
# ---------------------------------------------------------------------------


def _patch_jev_ask(monkeypatch, handler):
    from novelvideo.services import judgment as jev

    calls: list[dict] = []

    def fake_ask(state, questions, **kwargs):
        calls.append({"state": state, "questions": questions})
        return handler(questions)

    monkeypatch.setattr(jev, "ask_questions", fake_ask)
    return calls


@pytest.mark.asyncio
async def test_lint_overrule_keeps_prompt_when_jev_confidently_acquits(monkeypatch):
    import novelvideo.agents.global_video_optimizer as gvo
    from novelvideo.services import judgment as jev

    logs: list[str] = []
    calls = _patch_jev_ask(
        monkeypatch,
        lambda questions: {
            name: jev.YesNoAnswer(probability=0.9) for name in questions
        },
    )

    overruled = await gvo.motion_prompt_lint_overruled(
        "镜头缓缓推近，她抬手握住门把，指节因用力而发白，最后停在门缝透出的光里。",
        ["camera_path_missing", "physical_feedback_missing"],
        log=logs.append,
    )

    assert overruled is True
    assert len(calls) == 1
    assert set(calls[0]["questions"]) == {
        "camera_path_missing",
        "physical_feedback_missing",
    }
    assert any("误报" in line for line in logs)


@pytest.mark.asyncio
async def test_lint_overrule_rejects_when_any_finding_is_confirmed(monkeypatch):
    """有一条被 JEV 确认（低概率＝确实缺失），就不能整条否决。"""

    import novelvideo.agents.global_video_optimizer as gvo
    from novelvideo.services import judgment as jev

    calls = _patch_jev_ask(
        monkeypatch,
        lambda questions: {
            name: jev.YesNoAnswer(
                probability=0.15 if name == "end_state_missing" else 0.9
            )
            for name in questions
        },
    )

    overruled = await gvo.motion_prompt_lint_overruled(
        "镜头缓缓推近，她抬手握住门把，指节因用力而发白。",
        ["camera_path_missing", "end_state_missing"],
        log=lambda _message: None,
    )

    assert overruled is False
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_lint_overrule_refuses_uncertain_verdict(monkeypatch):
    """0.5 附近＝拿不准，照旧重生成，不凭犹豫的判断改数据。"""

    import novelvideo.agents.global_video_optimizer as gvo
    from novelvideo.services import judgment as jev

    calls = _patch_jev_ask(
        monkeypatch,
        lambda questions: {
            name: jev.YesNoAnswer(probability=0.52) for name in questions
        },
    )

    overruled = await gvo.motion_prompt_lint_overruled(
        "镜头缓缓推近，她抬手握住门把，指节因用力而发白，最后停在门缝透出的光里。",
        ["camera_path_missing"],
        log=lambda _message: None,
    )

    assert overruled is False
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_lint_overrule_skips_deterministic_findings_without_judgment(monkeypatch):
    """长度/占位符/台词泄漏是确定性事实，混在里面就不该交给 JEV。"""

    import novelvideo.agents.global_video_optimizer as gvo

    calls = _patch_jev_ask(monkeypatch, lambda questions: {})

    overruled = await gvo.motion_prompt_lint_overruled(
        "太短的提示词",
        ["prompt_too_short"],
        log=lambda _message: None,
    )

    assert overruled is False
    assert calls == []


@pytest.mark.asyncio
async def test_lint_overrule_falls_back_when_judgment_unavailable(monkeypatch):
    import novelvideo.agents.global_video_optimizer as gvo
    from novelvideo.services import judgment as jev

    def boom(state, questions, **kwargs):
        raise jev.JudgmentError("JEV 判断失败：连接不上判断服务")

    monkeypatch.setattr(jev, "ask_questions", boom)

    overruled = await gvo.motion_prompt_lint_overruled(
        "镜头缓缓推近，她抬手握住门把，指节因用力而发白，最后停在门缝透出的光里。",
        ["camera_path_missing"],
        log=lambda _message: None,
    )

    assert overruled is False


@pytest.mark.asyncio
async def test_lint_overrule_disabled_by_switch(monkeypatch):
    import novelvideo.agents.global_video_optimizer as gvo
    import novelvideo.config as config

    monkeypatch.setattr(config, "MOTION_PROMPT_LINT_JUDGMENT_AUTO", False)
    calls = _patch_jev_ask(monkeypatch, lambda questions: {})

    overruled = await gvo.motion_prompt_lint_overruled(
        "镜头缓缓推近，她抬手握住门把，指节因用力而发白，最后停在门缝透出的光里。",
        ["camera_path_missing"],
        log=lambda _message: None,
    )

    assert overruled is False
    assert calls == []
