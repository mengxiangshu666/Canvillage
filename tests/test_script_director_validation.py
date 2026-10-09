"""New-generation directing gates do not constrain historical parsing or film technique."""
import json
import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.models.test import TestModel

from novelvideo.freezone.script_director_validation import require_script_director_plan
from novelvideo.ports.story_script import FreezoneStoryScriptGenerateData


def complete_script():
    return FreezoneStoryScriptGenerateData(
        director_plan={
            "story_promise": "认真听完一段解释", "protagonist_goal": "讲清事实", "core_conflict": "概念之间的关系",
            "ending_change": "理解方法", "rhythm_curve": "从概念到示范", "sound_plan": "保留完整对白，无音乐",
            "visual_bible": {"visual_style": "纪实", "texture": "真实材质", "color_progression": "自然色不变", "lighting": "窗光", "camera_language": "固定观察"},
            "sequences": [{"sequence_id": "S1", "dramatic_goal": "理解方法", "shot_nos": [1]}, {"sequence_id": "S2", "dramatic_goal": "回顾方法", "shot_nos": [1]}],
        },
        rows=[{"shot_no": 1, "sequence_ids": ["S1", "S2"], "duration": 4, "visual_description": "讲解示范", "dialogue": "这是方法。", "shot_purpose": "看清示范", "start_state": "手在桌边", "end_state": "手指示图", "cut_reason": "保持结尾"}],
    )


@pytest.mark.parametrize("vision", [False, True])
def test_story_agents_do_not_override_provider_output_budget(monkeypatch, vision):
    from novelvideo.freezone import text_node

    captured = {}

    class CaptureAgent:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def output_validator(self, validator):
            return validator

    monkeypatch.setattr(text_node, "Agent", CaptureAgent)
    monkeypatch.setattr(text_node, "_direct_or_newapi_text_model", lambda **kwargs: (object(), "test"))
    monkeypatch.setattr(text_node, "resolve_freezone_story_script_model", lambda _model: {"id": "test", "provider": "newapi", "model": "test"})
    if vision:
        text_node.create_freezone_vision_story_script_agent("test")
    else:
        text_node.create_freezone_story_script_agent("test", expected_row_count=15)
    assert "max_tokens" not in captured.get("model_settings", {})
    assert captured["output_type"].outputs is FreezoneStoryScriptGenerateData


def test_missing_plan_is_retryable_but_old_results_still_parse():
    old = FreezoneStoryScriptGenerateData(rows=[{"shot_no": 1, "duration": 4, "visual_description": "静物"}])
    with pytest.raises(ModelRetry, match="director_plan.story_promise"):
        require_script_director_plan(old)


def test_overlapping_sequences_and_all_dialogue_are_accepted():
    data = complete_script()
    assert require_script_director_plan(data) is data


@pytest.mark.asyncio
@pytest.mark.parametrize("vision", [False, True])
async def test_unchanged_protagonist_world_effect_survives_text_and_vision_agent(monkeypatch, vision):
    from novelvideo.freezone import text_node

    data = complete_script()
    data.director_plan.protagonist_goal = "始终坚持让路，不改变自身信念"
    data.director_plan.ending_change = "原先堵路的邻居收起箱子，街道恢复通行；主角信念不变"
    data.director_plan.sequences[0].performance_plan = "主角退到墙边让路，邻居看见孩子被挡，收起箱子并招手放行"
    data.rows[0].character_action = "退到墙边；邻居收起箱子"
    data.rows[0].shot_prompt = " + ".join(f"[构图段{i}]" for i in range(8))
    data.rows[0].video_motion_prompt = " + ".join(f"[运动段{i}]" for i in range(6))
    model = TestModel(custom_output_text=data.model_dump_json())
    monkeypatch.setattr(text_node, "_direct_or_newapi_text_model", lambda **kwargs: (model, "test"))
    monkeypatch.setattr(text_node, "resolve_freezone_story_script_model", lambda _model: {"id": "test", "provider": "newapi", "model": "test"})
    agent = text_node.create_freezone_vision_story_script_agent("test") if vision else text_node.create_freezone_story_script_agent("test")
    result = (await agent.run("主角信念不变，通过行动让邻居改变做法")).output
    assert result.director_plan.ending_change == data.director_plan.ending_change
    assert result.director_plan.sequences[0].performance_plan == data.director_plan.sequences[0].performance_plan
    assert result.rows[0].character_action == data.rows[0].character_action
    # This exercises parsing/gates, not whether a real model invents effective drama.
    assert "largely unchanged protagonist" in text_node.FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT


def test_key_prop_requires_explicit_start_change_and_end_states():
    data = complete_script()
    data.rows[0].prop_tags = "蒲扇"
    data.rows[0].prop_descriptions = {"蒲扇": "竹柄与蒲叶扇面，天然编织纹理"}
    data.rows[0].prop_state_start = "手边合拢"
    data.rows[0].prop_state_change = "拿起并展开"
    data.rows[0].prop_state_end = "手中展开"
    assert require_script_director_plan(data) is data
    data.rows[0].prop_state_end = ""
    with pytest.raises(ModelRetry, match="关键道具 prop_state_end"):
        require_script_director_plan(data)


def test_background_prop_marker_does_not_create_a_new_gate():
    data = complete_script()
    data.rows[0].prop_tags = "无"
    assert require_script_director_plan(data) is data


def test_named_scene_and_prop_assets_require_reusable_baseline_design():
    data = complete_script()
    data.rows[0].scene_tags = "屋顶"
    data.rows[0].prop_tags = "滑板"
    data.rows[0].prop_state_start = "脚踩板面"
    data.rows[0].prop_state_change = "向前滑行"
    data.rows[0].prop_state_end = "滑到屋顶边缘"
    with pytest.raises(ModelRetry, match="场景基准设计缺少：屋顶"):
        require_script_director_plan(data)
    data.rows[0].scene_descriptions = {"屋顶": "东侧排气管，西侧围栏，天窗位于中央，红砖与沥青地面"}
    data.rows[0].prop_descriptions = {"滑板": "青色短板，黑色防滑面，银色桥架与橙色轮子"}
    assert require_script_director_plan(data) is data


def test_asset_names_are_exact_and_empty_markers_do_not_require_maps():
    data = complete_script()
    data.rows[0].scene_tags = "屋顶、仓库"
    data.rows[0].scene_descriptions = {"屋顶": "红砖屋顶，西侧围栏"}
    with pytest.raises(ModelRetry, match="仓库"):
        require_script_director_plan(data)
    data.rows[0].scene_descriptions["仓库"] = "仓库"
    with pytest.raises(ModelRetry, match="仓库"):
        require_script_director_plan(data)
    data.rows[0].scene_tags = "无"
    data.rows[0].scene_descriptions = {}
    data.rows[0].prop_tags = "无"
    data.rows[0].prop_descriptions = {}
    assert require_script_director_plan(data) is data


@pytest.mark.asyncio
@pytest.mark.parametrize("vision", [False, True])
async def test_text_and_vision_generation_reject_named_assets_without_design(monkeypatch, vision):
    from novelvideo.freezone import text_node
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    data = complete_script()
    data.rows[0].scene_tags = "屋顶"
    data.rows[0].shot_prompt = " + ".join(f"[构图段{i}]" for i in range(8))
    data.rows[0].video_motion_prompt = " + ".join(f"[运动段{i}]" for i in range(6))
    model = TestModel(custom_output_text=data.model_dump_json())
    monkeypatch.setattr(text_node, "_direct_or_newapi_text_model", lambda **kwargs: (model, "test"))
    monkeypatch.setattr(text_node, "resolve_freezone_story_script_model", lambda _model: {"id": "test", "provider": "newapi", "model": "test"})
    agent = text_node.create_freezone_vision_story_script_agent("test") if vision else text_node.create_freezone_story_script_agent("test")
    with pytest.raises(UnexpectedModelBehavior, match="output retries"):
        await agent.run("在屋顶发生的小故事")


def test_directed_script_requires_executable_prompts_even_without_numbered_source():
    from novelvideo.freezone.text_node import _require_complete_directed_script

    with pytest.raises(ModelRetry, match="可执行提示词"):
        _require_complete_directed_script(complete_script())


@pytest.mark.asyncio
async def test_numbered_dialogue_script_can_pass_without_removing_required_dialogue(monkeypatch):
    from novelvideo.freezone import text_node

    data = complete_script()
    data.rows[0].shot_prompt = " + ".join(f"[构图段{i}]" for i in range(8))
    data.rows[0].video_motion_prompt = " + ".join(f"[运动段{i}]" for i in range(6))
    model = TestModel(custom_output_text=data.model_dump_json())
    monkeypatch.setattr(text_node, "_direct_or_newapi_text_model", lambda **kwargs: (model, "test"))
    monkeypatch.setattr(text_node, "resolve_freezone_story_script_model", lambda _model: {"id": "test", "provider": "newapi", "model": "test"})
    agent = text_node.create_freezone_story_script_agent("test", expected_row_count=1)
    response = await agent.run("对白教程")
    assert response.output.rows[0].dialogue == "这是方法。"


@pytest.mark.asyncio
async def test_idea_only_generation_runs_real_agent_validation(monkeypatch):
    from novelvideo.freezone import text_node

    data = complete_script()
    data.director_plan.assumptions = ["用户只给想法，采用纪实讲解"]
    data.rows[0].shot_prompt = " + ".join(f"[构图段{i}]" for i in range(8))
    data.rows[0].video_motion_prompt = " + ".join(f"[运动段{i}]" for i in range(6))
    model = TestModel(custom_output_text=data.model_dump_json())
    monkeypatch.setattr(text_node, "_direct_or_newapi_text_model", lambda **kwargs: (model, "test"))
    monkeypatch.setattr(text_node, "resolve_freezone_story_script_model", lambda _model: {"id": "test", "provider": "newapi", "model": "test"})
    monkeypatch.setattr(text_node, "get_freezone_story_script_agent", text_node.create_freezone_story_script_agent)
    result = await text_node.generate_freezone_story_script(source_text="", prompt="做个解释方法的短片", model="test")
    assert result.director_plan.assumptions == ["用户只给想法，采用纪实讲解"]
    assert result.rows[0].shot_purpose == "看清示范"
    with pytest.raises(ValueError, match="创作想法"):
        await text_node.generate_freezone_story_script(source_text=" ", prompt=" ", model="test")


@pytest.mark.asyncio
async def test_mixed_performance_durations_survive_agent_and_rewrite(monkeypatch):
    from novelvideo.freezone import text_node

    data = complete_script()
    durations = [4, 8, 4, 5, 6, 15]
    base = data.rows[0]
    base.shot_prompt = " + ".join(f"[构图段{i}]" for i in range(8))
    base.video_motion_prompt = " + ".join(f"[运动段{i}]" for i in range(6))
    data.rows = [base.model_copy(update={"shot_no": i + 1, "sequence_ids": ["S1"], "duration": duration, "duration_reason": f"完整示范 {duration} 秒，结果看清后切镜"}) for i, duration in enumerate(durations)]
    sequence = data.director_plan.sequences[0]
    sequence.shot_nos = list(range(1, 7))
    sequence.staging_plan = "桌左取工具，桌右演示，视线保持朝向操作区"
    sequence.performance_plan = "观察问题，选择工具，演示后等待观众看清"
    data.director_plan.sequences = [sequence]
    model = TestModel(custom_output_text=data.model_dump_json())
    monkeypatch.setattr(text_node, "_direct_or_newapi_text_model", lambda **kwargs: (model, "test"))
    monkeypatch.setattr(text_node, "resolve_freezone_story_script_model", lambda _model: {"id": "test", "provider": "newapi", "model": "test"})
    result = (await text_node.create_freezone_story_script_agent("test").run("讲解示范")).output
    assert [row.duration for row in result.rows] == durations
    assert result.director_plan.sequences[0].performance_plan == sequence.performance_plan
    assert result.director_plan.sequences[0].staging_plan == sequence.staging_plan
    assert result.rows[-1].duration_reason == data.rows[-1].duration_reason
    rows = [row.model_dump() for row in result.rows]
    rewrite_fields = {key: value for key, value in rows[0].items() if key in text_node.FreezoneShotRewriteRow.model_fields}
    rewrite_fields.pop("duration_reason")
    rewritten = text_node.FreezoneShotRewriteRow(**rewrite_fields)
    assert text_node.merge_freezone_shot_rewrite(rows, 0, rewritten)["duration_reason"] == rows[0]["duration_reason"]
    rewritten.duration_reason = "停留观察四秒后切镜"
    assert text_node.merge_freezone_shot_rewrite(rows, 0, rewritten)["duration_reason"] == rewritten.duration_reason
    assert "duration_reason" in text_node.build_freezone_shot_rewrite_task(rows=rows, target_index=0, instruction="延长观察")


def test_generation_prompts_have_no_short_shot_default():
    from novelvideo.freezone.text_node import FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT, build_freezone_story_script_task

    task = build_freezone_story_script_task(source_text="", prompt="观察人物犹豫")
    assert "2-5" not in task + FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT
    assert "performance_plan" in task
    assert "duration_reason" in task


@pytest.mark.parametrize("refs", [[2], [], [1, 1]])
def test_invalid_sequence_refs_are_retryable(refs):
    data = complete_script()
    data.director_plan.sequences = data.director_plan.sequences[:1]
    data.director_plan.sequences[0].shot_nos = refs
    with pytest.raises(ModelRetry):
        require_script_director_plan(data)


@pytest.mark.asyncio
@pytest.mark.parametrize("vision", [False, True])
async def test_text_and_vision_agents_reject_missing_director_plan(monkeypatch, vision):
    from novelvideo.freezone import text_node
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    model = TestModel(custom_output_text=json.dumps({"title": "空规划", "rows": [{"shot_no": 1, "duration": 4, "visual_description": "静物"}]}))
    monkeypatch.setattr(text_node, "_direct_or_newapi_text_model", lambda **kwargs: (model, "test"))
    monkeypatch.setattr(text_node, "resolve_freezone_story_script_model", lambda _model: {"id": "test", "provider": "newapi", "model": "test"})
    agent = text_node.create_freezone_vision_story_script_agent("test") if vision else text_node.create_freezone_story_script_agent("test")
    with pytest.raises(UnexpectedModelBehavior, match="output retries"):
        await agent.run("生成完整脚本")


@pytest.mark.asyncio
@pytest.mark.parametrize("vision", [False, True])
@pytest.mark.parametrize("corrects", [False, True])
async def test_nested_director_plan_is_checked_and_corrected_in_streamed_json(monkeypatch, vision, corrects):
    from pydantic_ai.models.function import FunctionModel
    from novelvideo.freezone import text_node
    from novelvideo.freezone.script_video_duration import run_duration_planned_script

    data = complete_script()
    data.rows[0].shot_prompt = " + ".join(f"[构图段{i}]" for i in range(8))
    data.rows[0].video_motion_prompt = " + ".join(f"[运动段{i}]" for i in range(6))
    invalid = dict(data.model_dump(), director_plan="PRIVATE_DIRECTOR_INPUT")
    calls = []
    async def stream(_messages, info):
        calls.append(1)
        assert not info.output_tools
        assert '"director_plan"' in info.instructions
        response = data.model_dump_json() if corrects and len(calls) > 1 else json.dumps(invalid)
        yield "```json\n"
        for offset in range(0, len(response), 100):
            yield response[offset:offset + 100]
        yield "\n```"

    model = FunctionModel(stream_function=stream)
    monkeypatch.setattr(text_node, "_direct_or_newapi_text_model", lambda **kwargs: (model, "test"))
    monkeypatch.setattr(text_node, "resolve_freezone_story_script_model", lambda _model: {"id": "test", "provider": "newapi", "model": "test"})
    agent = text_node.create_freezone_vision_story_script_agent("test") if vision else text_node.create_freezone_story_script_agent("test")
    if corrects:
        result = await run_duration_planned_script(agent, "生成完整脚本", None)
        assert result.output.director_plan.story_promise == data.director_plan.story_promise
        assert len(calls) == 2
    else:
        with pytest.raises(ValueError, match="director_plan.*model_type") as caught:
            await run_duration_planned_script(agent, "生成完整脚本", None)
        assert "PRIVATE_DIRECTOR_INPUT" not in str(caught.value)
        assert len(calls) == 4
