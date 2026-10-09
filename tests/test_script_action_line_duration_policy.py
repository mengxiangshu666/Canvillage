from novelvideo.freezone.script_repair import build_script_repair_instruction
from novelvideo.freezone.text_node import (
    FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT,
    build_freezone_story_script_task,
)
from novelvideo.freezone.script_video_duration import resolve_script_video_duration_plan
import pytest


def test_generation_and_repair_share_content_led_duration_policy():
    from novelvideo.freezone.script_video_duration import SCRIPT_CONTENT_DURATION_GUIDANCE

    prompt = FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT
    assert SCRIPT_CONTENT_DURATION_GUIDANCE in prompt
    assert "unknown" in prompt
    assert "current maximum tier" not in prompt
    assert "three 15-second clips" not in prompt
    for instruction in (
        build_freezone_story_script_task(source_text="滑板沿桥面前进", prompt=""),
        build_script_repair_instruction([]),
    ):
        assert SCRIPT_CONTENT_DURATION_GUIDANCE in instruction
        assert "默认向当前模型时长上限靠拢" not in instruction


def test_duration_plan_reads_selected_model_capabilities(monkeypatch):
    monkeypatch.setattr(
        "novelvideo.freezone.video_node.get_freezone_video_model_options",
        lambda: [{
            "id": "direct_test_video",
            "enabled": True,
            "runtimeReady": True,
            "durationOptions": [4, 8, 15],
            "maxDuration": 15,
            "minDuration": 4,
            "durationParameterEnabled": True,
        }],
    )
    plan = resolve_script_video_duration_plan("direct_test_video")
    assert plan == {
        "model_id": "direct_test_video",
        "max_seconds": 15.0,
        "min_seconds": 4.0,
        "duration_options": [4.0, 8.0, 15.0],
    }


def test_planning_chooses_complete_clips_by_content_without_fixed_node_quota():
    from novelvideo.freezone.script_video_duration import SCRIPT_CONTENT_DURATION_GUIDANCE, script_video_duration_instruction

    instruction = script_video_duration_instruction({
        "model_id": "test", "max_seconds": 15, "min_seconds": 4, "duration_options": [4, 15],
    })
    assert "3个15秒片段" not in instruction
    assert "模型上限是边界，不是目标" in instruction
    assert "不设节点数量配额" in SCRIPT_CONTENT_DURATION_GUIDANCE
    assert "必要停顿及余波" in SCRIPT_CONTENT_DURATION_GUIDANCE
    assert "仅模型明确支持时" in SCRIPT_CONTENT_DURATION_GUIDANCE
    assert "说话人变化不等于必须新建节点" in SCRIPT_CONTENT_DURATION_GUIDANCE
    assert "不设计提前结束后等待剪辑" in instruction
    assert "不等于一句对白、一个节拍或一个电影切镜点" in FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT


@pytest.mark.parametrize("capabilities,maximum", [
    ({"maxDuration": 15, "supportsCustomDuration": False}, 15),
    ({"maxDuration": 15}, 15),
    ({"durationOptions": [4, 8], "maxDuration": 15}, 15),
    ({"durationOptions": [4, 8]}, 8),
    ({}, None),
    ({"maxDuration": 15, "durationParameterEnabled": False}, None),
])
def test_model_ceiling_does_not_require_custom_duration_flag(monkeypatch, capabilities, maximum):
    monkeypatch.setattr("novelvideo.freezone.video_node.get_freezone_video_model_options",
                        lambda: [{"id": "direct_test", "enabled": True, "runtimeReady": True, **capabilities}])
    plan = resolve_script_video_duration_plan("direct_test")
    assert (plan["max_seconds"] if plan else None) == maximum


def test_invalid_model_is_not_silently_replaced(monkeypatch):
    monkeypatch.setattr("novelvideo.freezone.video_node.get_freezone_video_model_options", lambda: [])
    with pytest.raises(ValueError, match="已失效"):
        resolve_script_video_duration_plan("deleted_model")


@pytest.mark.asyncio
async def test_missing_duration_capability_does_not_block_optimization(monkeypatch):
    from types import SimpleNamespace
    from novelvideo.freezone.script_video_duration import run_duration_planned_script

    monkeypatch.setattr("novelvideo.freezone.video_node.get_freezone_video_model_options",
                        lambda: [{"id": "direct_unknown", "enabled": True, "runtimeReady": True}])
    calls = []
    async def run(task):
        calls.append(task)
        return SimpleNamespace(output="optimized")
    result = await run_duration_planned_script(SimpleNamespace(run=run), "修复本镜", "direct_unknown")
    assert result.output == "optimized"
    assert "保留原镜头时长" in calls[0]
    assert "不要虚构上限" in calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,duration,valid", [
    ("single_action_line", 15, True),
    ("single_action_line", 8, True),
    ("single_action_line", 4, True),
    ("multi_beat", 15, True),
    ("multi_beat", 4, True),
    ("multi_beat", 6, False),
    ("multi_beat", 2, False),
    ("fixed_timing", 2, True),
    ("multi_beat", 16, False),
    ("", 15, False),
])
async def test_agent_checks_duration_using_request_local_capabilities(policy, duration, valid):
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.exceptions import UnexpectedModelBehavior
    from novelvideo.freezone.text_node import FreezoneShotRewriteRow
    from novelvideo.freezone.script_video_duration import validate_script_video_duration

    output = dict(duration=duration, duration_policy=policy, duration_reason="动作完成后切镜",
                  visual_description="沿桥滑行", shot="全景", character_action="滑行",
                  emotion="专注", shot_prompt="桥面", video_motion_prompt=f"滑行 {duration}s")
    agent = Agent(TestModel(custom_output_args=output), output_type=FreezoneShotRewriteRow, retries=1)
    agent.output_validator(validate_script_video_duration)
    if valid:
        result = await agent.run("规划镜头", deps={"video_duration_plan": {"max_seconds": 15, "min_seconds": 4, "duration_options": [4, 8, 15]}})
        assert result.output.duration == duration
    else:
        with pytest.raises(UnexpectedModelBehavior):
            await agent.run("规划镜头", deps={"video_duration_plan": {"max_seconds": 15, "min_seconds": 4, "duration_options": [4, 8, 15]}})
    # Cached agents must not retain a preceding request's model capabilities.
    legacy = await agent.run("旧请求")
    assert legacy.output.duration == duration


def test_duration_plan_infers_minimum_when_only_tiers_are_declared(monkeypatch):
    monkeypatch.setattr("novelvideo.freezone.video_node.get_freezone_video_model_options",
                        lambda: [{"id": "tiers", "durationOptions": [5, 10, 15]}])
    assert resolve_script_video_duration_plan("tiers")["min_seconds"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["correctable", "validation", "transport"])
async def test_script_stream_preserves_validation_retries_and_does_not_replay_timeout(failure):
    from contextlib import asynccontextmanager
    from pydantic_ai import Agent, ModelRetry
    from pydantic_ai.exceptions import ModelHTTPError
    from pydantic_ai.models.test import TestModel
    from novelvideo.freezone.script_video_duration import run_duration_planned_script
    from novelvideo.freezone.text_node import FreezoneShotRewriteRow

    streams = []
    class StreamOnlyModel(TestModel):
        async def request(self, *args, **kwargs):
            raise AssertionError("script must not wait for a non-streaming completion")

        @asynccontextmanager
        async def request_stream(self, *args, **kwargs):
            streams.append(1)
            if failure == "transport":
                raise ModelHTTPError(status_code=524, model_name="test", body={"error": "timeout"})
            async with super().request_stream(*args, **kwargs) as response:
                yield response

    agent = Agent(StreamOnlyModel(custom_output_args=dict(
        duration=8, visual_description="监听后选择", shot="中景", character_action="转头", emotion="坚定",
        shot_prompt="画面", video_motion_prompt="8s",
    )), output_type=FreezoneShotRewriteRow, retries=1)
    checks = []
    @agent.output_validator
    def validate(output):
        checks.append(1)
        if failure == "validation" or len(checks) == 1:
            raise ModelRetry("请补齐导演规划并返回完整结果：director_plan.story_promise 未填写")
        return output

    if failure == "correctable":
        result = await run_duration_planned_script(agent, "优化脚本", None)
        assert result.output.duration == 8
        assert len(checks) == len(streams) == 2
    elif failure == "validation":
        with pytest.raises(ValueError, match="director_plan.story_promise 未填写"):
            await run_duration_planned_script(agent, "优化脚本", None)
        assert len(checks) == len(streams) == 2
    else:
        with pytest.raises(ModelHTTPError) as caught:
            await run_duration_planned_script(agent, "优化脚本", None)
        assert caught.value.status_code == 524
        assert streams == [1] and checks == []


@pytest.mark.asyncio
@pytest.mark.parametrize("plain_text", [False, True])
async def test_script_retry_error_preserves_schema_or_missing_output_reason(plain_text):
    from pydantic_ai import Agent, ToolOutput
    from pydantic_ai.models.function import FunctionModel, DeltaToolCall
    from novelvideo.freezone.script_video_duration import run_duration_planned_script
    from novelvideo.freezone.text_node import FreezoneShotRewriteRow

    calls = []
    async def stream(_messages, info):
        calls.append(1)
        if plain_text:
            yield '这是普通文本，尚未调用输出工具'
        else:
            yield {0: DeltaToolCall(name=info.output_tools[0].name, json_args='{"duration":"PRIVATE_INPUT"}')}

    agent = Agent(FunctionModel(stream_function=stream), output_type=ToolOutput(FreezoneShotRewriteRow), retries=1)
    with pytest.raises(ValueError) as caught:
        await run_duration_planned_script(agent, "生成脚本", None)
    message = str(caught.value)
    assert "PRIVATE_INPUT" not in message
    assert "Exceeded maximum" not in message
    assert ("约定的脚本数据" if plain_text else "duration:") in message
    assert len(calls) == 2
