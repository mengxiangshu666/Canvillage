"""Native audio must reach the final provider payload through the real chain.

Only queue persistence, provider/translation I/O and asset writes are replaced. The route,
runner, job, preflight and AutoDL request compiler remain real; no paid calls.

MiniMax H3 的原生 v2 合同不是自定义中文槽位，而是官方三章节：
`integrated_multimodal_description`、`overall_soundscape`、
`non_diegetic_music`。用户台词只能出现在
`<d>[语言] 原文</d>` 内，平台说明和“不要朗读”约束都不再追加。
"""

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from novelvideo.api.routes import freezone as route
from novelvideo.freezone.jobs import run_freezone_video_gen
from novelvideo.generators.video.capabilities import NativeAudio, ReferenceLimits, VideoMode
from novelvideo.generators.video.direct_video_protocol_contracts import (
    build_minimax_video_v2_payload,
)
from novelvideo.generators.video.generic_video_adapter import GenericVideoAdapterGenerator
from novelvideo.generators.video_generator import VideoGenResult, VideoGenStatus
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.runners import video as runner


SCRIPT = (
    '[0-2s] 女孩看向门口，用普通话说：“你终于来了。”\n'
    '[2-4s] 男孩回答：“我们走吧。”\n'
    '[4-6s] 女孩重复：“我们走吧。”\n'
    '镜头缓慢推近，保留脚步和雨声。'
)

#: 脚本里三轮台词，其中「我们走吧」被说了两遍——**重复必须原样保留**。
SCRIPT_DIALOGUE = ["你终于来了。", "我们走吧。", "我们走吧。"]


def _h3_dialogue_values(prompt: str) -> list[str]:
    return re.findall(r"<d>\[[A-Za-z]+\]\s*(.*?)</d>", prompt)


def _assert_h3_prompt_shape(prompt: str, expected_dialogue: list[str]) -> None:
    assert prompt.startswith("integrated_multimodal_description: [Shot 1]")
    assert prompt.count("integrated_multimodal_description:") == 1
    assert prompt.count("overall_soundscape:") == 1
    assert prompt.count("non_diegetic_music:") == 1
    assert prompt.index("integrated_multimodal_description:") < prompt.index(
        "overall_soundscape:"
    ) < prompt.index("non_diegetic_music:")
    assert _h3_dialogue_values(prompt) == expected_dialogue
    for dialogue in set(expected_dialogue):
        assert prompt.count(dialogue) == expected_dialogue.count(dialogue)
    assert "对白：" not in prompt
    assert "说话表演" not in prompt
    assert "不要朗读" not in prompt
    soundscape = prompt.split("overall_soundscape:", 1)[1].split(
        "non_diegetic_music:", 1
    )[0]
    music = prompt.split("non_diegetic_music:", 1)[1]
    for dialogue in expected_dialogue:
        assert dialogue not in soundscape
        assert dialogue not in music


@pytest.fixture
def capture_chain(monkeypatch, tmp_path):
    captured = {}

    async def translate(body, *, has_dialogue, soundscape="", timeout_seconds=90.0):
        from novelvideo.generators.video.h3_body_translation import H3EnglishBody

        captured["translation_input"] = body
        return H3EnglishBody(
            "[Shot 1] The subject moves through the scene as the camera slowly follows.",
            "Natural room tone and soft footsteps.",
        )

    monkeypatch.setattr(
        "novelvideo.generators.video.h3_body_translation.translate_h3_body_to_english", translate,
    )
    model = SimpleNamespace(
        backend="direct_dialogue-fixture",
        upstream_model="minimax-h3-fixture",
        model_id="minimax-h3-fixture",
        label="MiniMax H3 fixture",
        enabled=True,
        profile=SimpleNamespace(family="minimax-h3"),
        capability=SimpleNamespace(
            native_audio=NativeAudio.OPTIONAL,
            modes=(VideoMode.TEXT_TO_VIDEO,),
            reference_limits=ReferenceLimits(),
        ),
    )
    monkeypatch.setattr(
        "novelvideo.generators.video.direct_models.resolve_direct_video_model",
        lambda backend: model if backend == "direct_dialogue-fixture" else None,
    )
    monkeypatch.setattr(
        route, "freezone_video_model_contract",
        lambda _backend: {"nativeAudio": model.capability.native_audio.value},
    )

    class CapturedQueue(Exception):
        pass

    async def enqueue(_ctx, **kwargs):
        captured["payload"] = kwargs["payload"]
        raise CapturedQueue

    monkeypatch.setattr(
        route, "get_task_backend",
        lambda: SimpleNamespace(enqueue_project_task=enqueue),
    )

    class CaptureGenerator(GenericVideoAdapterGenerator):
        async def recover_task(self, **kwargs):
            captured["recover"] = kwargs
            out = Path(kwargs["output_path"])
            out.write_bytes(b"recovered local request fixture")
            return VideoGenResult(status=VideoGenStatus.DONE, video_path=str(out))

        async def generate(self, **kwargs):
            captured["generate"] = kwargs
            captured["autodl"] = self._build_autodl_payload(
                prompt=kwargs["prompt"], images=[], audios=[],
                duration=kwargs["duration"], aspect_ratio="16:9", resolution="768p",
                kwargs=kwargs,
            )
            captured["minimax_v2_payload"] = build_minimax_video_v2_payload(
                model="minimax-h3-fixture",
                prompt=kwargs["prompt"],
                resolution="768p",
                duration=int(kwargs["duration"]),
                ratio="16:9",
            )
            out = Path(kwargs["output_path"])
            out.write_bytes(b"local request fixture")
            return VideoGenResult(status=VideoGenStatus.DONE, video_path=str(out))

    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda **_kwargs: CaptureGenerator.__new__(CaptureGenerator),
    )
    strip_audio = AsyncMock(return_value=True)
    monkeypatch.setattr("novelvideo.freezone.jobs._strip_unrequested_video_audio", strip_audio)
    manager = Mock()
    manager.get_task_for_project.return_value = None
    monkeypatch.setattr(runner, "get_task_manager", lambda: manager)
    monkeypatch.setattr(runner, "probe_video_size", AsyncMock(return_value=(160, 90)))
    monkeypatch.setattr(runner, "_ensure_video_preview_frame", lambda _out: None)
    monkeypatch.setattr(runner, "_append_freezone_video_node_history", lambda **_kwargs: None)
    monkeypatch.setattr(runner, "commit_media_result_to_canvas", lambda **_kwargs: None)
    ctx = ProjectContext(
        project_id="speech-fixture", project_name="speech-fixture",
        owner_type="user", owner_id="fixture", owner_username="fixture",
        requester_user_id="fixture", requester_username="fixture",
        requester_principals=(("user", "fixture"),), effective_role="owner",
        home_node_id="fixture", output_dir=tmp_path,
        state_dir=tmp_path / "state", runtime_dir=tmp_path / "runtime", is_home_node=True,
    )

    async def run(
        *, prompt=SCRIPT, audio=True, explicit=True, strategy="",
        native=NativeAudio.OPTIONAL, entry="canvas", backend="direct_dialogue-fixture",
        dialogue_text="", spoken=None,
        resume_provider_task_id=None,
    ):
        model.capability.native_audio = native
        common = dict(
            project_dir=tmp_path, job_id="speech-fixture", prompt=prompt,
            reference_items=[], backend=backend, duration_seconds=6,
            generate_audio=audio, generate_audio_explicit=explicit,
            native_audio_strategy=strategy, dialogue_text=dialogue_text,
            spoken_dialogue=list(spoken or []),
            resume_provider_task_id=resume_provider_task_id,
        )
        if entry == "direct":
            await run_freezone_video_gen(**common)
        else:
            with pytest.raises(CapturedQueue):
                await route._start_or_enqueue_freezone_video_gen(
                    **common, ctx=ctx, username="fixture", project="speech-fixture",
                    output_dir=str(tmp_path), aspect_ratio="16:9", resolution="768p",
                    human_review=False, scene_optimize=None,
                    requested_generate_audio=audio if explicit is not None else None,
                )
            await runner._run_freezone_video_gen_async(
                {"payload": captured["payload"]}, ctx,
            )
        return captured, strip_audio

    run.captured = captured
    return run


@pytest.mark.parametrize("entry", ["canvas", "direct"])
@pytest.mark.parametrize("native", [NativeAudio.OPTIONAL, NativeAudio.REQUIRED])
async def test_native_audio_builds_official_h3_prompt(capture_chain, entry, native):
    captured, strip_audio = await capture_chain(entry=entry, native=native)

    assert captured["generate"]["generate_audio"] is True
    provider_prompt = captured["generate"]["prompt"]
    _assert_h3_prompt_shape(provider_prompt, SCRIPT_DIALOGUE)
    assert captured["autodl"]["prompt"] == provider_prompt
    assert captured["minimax_v2_payload"]["content"] == [
        {"type": "text", "text": provider_prompt}
    ]
    strip_audio.assert_not_awaited()


@pytest.mark.parametrize("prompt,expected_prose,expected_dialogue", [
    # 冒号式台词：连「回应：」这种非常见动词也要收进来，否则那句就留在散文里。
    ('女孩说：你终于来了，男孩回应：我们走吧。镜头推近。', "镜头推近", [
        "你终于来了",
        "我们走吧",
    ]),
    # 只有画面文字：没有台词可抽，也不能拿结构化字段去兜底，招牌原样留在画面里。
    ('雨落在屋檐上，只有雨声和脚步声；路牌写着“欢迎回家”。', "路牌写着“欢迎回家”", []),
    # 超长画面描述 + 脚本：散文被长度上限截断，但台词槽位必须完整保留、顺序不错。
    (('环境与光线描写。' * 300) + '\n' + SCRIPT, "环境与光线描写", SCRIPT_DIALOGUE),
    # 台词里带数字，不能和时长校验互相污染。
    ('男孩说：“生成 6 秒视频是我的工作；等我 12 秒。”\n镜头推近。', "镜头推近", [
        "生成 6 秒视频是我的工作；等我 12 秒。",
    ]),
])
async def test_native_prompt_keeps_visual_prose_and_official_dialogue(
    capture_chain, prompt, expected_prose, expected_dialogue,
):
    captured, _ = await capture_chain(prompt=prompt, dialogue_text="过期的隐藏台词")
    provider_prompt = captured["generate"]["prompt"]

    # Creative prose reaches the translator; only its English output goes to H3.
    assert expected_prose in captured["translation_input"]
    assert "The subject moves through the scene" in provider_prompt
    _assert_h3_prompt_shape(provider_prompt, expected_dialogue)
    # 一个可能过期的结构化字段绝不能盖过提示词里写明的台词。
    assert "过期的隐藏台词" not in provider_prompt


async def test_performance_cue_is_never_read_back_as_dialogue(capture_chain):
    """剥离器自己产出的「说话表演」不能在二次规范化时被读回成台词。

    真机事故的镜像：`男孩说话表演。` 会被 `说` + `话表演。` 匹配成一句台词。
    一旦成立，第二次规范化就会凭空多出一条 `对白：「话表演。」口型同步`。
    """

    captured, _ = await capture_chain(
        prompt='男孩说：“我们走吧。”镜头推近。',
        entry="direct",
    )
    provider_prompt = captured["generate"]["prompt"]
    _assert_h3_prompt_shape(provider_prompt, ["我们走吧。"])
    assert "话表演" not in provider_prompt


async def test_native_second_normalization_is_idempotent(capture_chain):
    """路由规范化一次、job 再走一遍：同一条提示词过两次机器，产出必须一致。

    槽位行本身带着台词，第二次解析必须把词**收回来再挂回去**；直接丢掉槽位就等于
    把台词弄丢，重复挂一遍就会把同一句说两遍（`说话表演` 那侧已经抽不出词了）。
    """

    first, _ = await capture_chain(entry="canvas")
    second, _ = await capture_chain(
        entry="canvas",
        prompt=first["payload"]["prompt"],
        dialogue_text=first["payload"]["dialogue_text"],
    )

    assert second["payload"]["prompt"] == first["payload"]["prompt"]
    assert second["payload"]["spoken_dialogue"] == first["payload"]["spoken_dialogue"]
    assert second["generate"]["prompt"] == first["generate"]["prompt"]
    _assert_h3_prompt_shape(second["generate"]["prompt"], SCRIPT_DIALOGUE)


async def test_flat_dialogue_text_that_projects_structured_turns_is_not_spoken_twice(
    capture_chain,
):
    """画布节点的台词只该有一个真相：结构化轮次。

    视频任务完成后会把 `dialogue_text` 回写成 `spoken_dialogue` 的拼接投影
    （见 `task_backend/runners/canvas_media.py`）。下一次提交两个字段就会一起到达，
    投影若被当成新的一轮，同一句话会被交给 H3 说两遍。
    """

    captured, _ = await capture_chain(
        entry="canvas",
        prompt="中近景，他抬起头，雨声持续。",
        spoken=["我记着你的话。"],
        dialogue_text="我记着你的话。",
    )
    provider_prompt = captured["generate"]["prompt"]

    _assert_h3_prompt_shape(provider_prompt, ["我记着你的话。"])
    assert captured["payload"]["spoken_dialogue"] == ["我记着你的话。"]


async def test_h3_meta_direction_is_removed_at_the_final_provider_prompt(capture_chain):
    prompt = (
        "钟楼顶层，林默只负责倾听和反应，不开口。"
        "苏岚说：“那就让全城听见。”"
        "她抬起头望向铜钟说出这句话，口型同步。"
        "镜头缓慢上扬。"
    )

    captured, _ = await capture_chain(prompt=prompt, entry="canvas")
    provider_prompt = captured["generate"]["prompt"]

    _assert_h3_prompt_shape(provider_prompt, ["那就让全城听见。"])
    assert "只负责倾听和反应" not in provider_prompt
    assert "不开口" not in provider_prompt
    assert "说出这句话" not in provider_prompt
    assert "口型同步" not in provider_prompt
    assert "抬起头望向铜钟" in captured["translation_input"]
    assert "镜头缓慢上扬" in captured["translation_input"]


@pytest.mark.parametrize("audio", [False, True])
@pytest.mark.parametrize("entry", ["canvas", "direct"])
async def test_h3_translation_failure_never_calls_video_provider(
    capture_chain, monkeypatch, audio, entry,
):
    async def unavailable(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "novelvideo.generators.video.h3_body_translation.translate_h3_body_to_english",
        unavailable,
    )
    with pytest.raises(ValueError, match="编译失败，已阻止提交"):
        await capture_chain(audio=audio, entry=entry)
    assert "generate" not in capture_chain.captured
    assert "autodl" not in capture_chain.captured


@pytest.mark.parametrize("audio", [False, True])
async def test_h3_resume_does_not_require_translation(capture_chain, monkeypatch, audio):
    async def unavailable(*args, **kwargs):
        raise AssertionError("Recovery must not recompile an accepted prompt")

    monkeypatch.setattr(
        "novelvideo.generators.video.h3_body_translation.translate_h3_body_to_english",
        unavailable,
    )
    captured, _ = await capture_chain(
        audio=audio, entry="direct", resume_provider_task_id="accepted-h3-task",
    )
    assert captured["recover"]["task_id"] == "accepted-h3-task"
    assert "generate" not in captured


async def test_legacy_seedance_native_branch_isolates_words(capture_chain):
    captured, _ = await capture_chain(entry="direct", backend="seedance_2")
    assert captured["generate"]["audio"] is True
    generic_prompt = captured["generate"]["prompt"]
    assert generic_prompt.count("你终于来了。") == 1
    assert generic_prompt.count("我们走吧。") == 2
    assert "speech-fixture" not in generic_prompt


@pytest.mark.parametrize("native", [NativeAudio.OPTIONAL, NativeAudio.REQUIRED])
@pytest.mark.parametrize("audio,explicit,strategy", [
    (False, True, "native"),
    (True, None, "external"),
])
async def test_silent_and_external_requests_keep_speech_isolation(
    capture_chain, native, audio, explicit, strategy,
):
    captured, strip_audio = await capture_chain(
        native=native, audio=audio, explicit=explicit, strategy=strategy,
    )
    assert "你终于来了" not in captured["generate"]["prompt"]
    assert "我们走吧" not in captured["generate"]["prompt"]
    assert captured["generate"]["generate_audio"] is (native == NativeAudio.REQUIRED)
    strip_audio.assert_awaited_once()


async def test_explicit_native_on_overrides_stale_external_fields(capture_chain):
    captured, strip_audio = await capture_chain(strategy="external")
    _assert_h3_prompt_shape(captured["generate"]["prompt"], SCRIPT_DIALOGUE)
    strip_audio.assert_not_awaited()


async def test_singing_marker_reaches_native_provider_as_one_bounded_lyric(capture_chain):
    prompt = (
        "主体跳起来开始唱歌：哎哟哎哟哎哟花姑娘！！！\n"
        "首帧约束：保持输入主体和场景。\n"
        "输出要求：动作自然，运动平滑。"
    )

    captured, strip_audio = await capture_chain(prompt=prompt)

    provider_prompt = captured["generate"]["prompt"]
    assert captured["payload"]["spoken_dialogue"] == ["哎哟哎哟哎哟花姑娘！！！"]
    assert "哎哟哎哟哎哟花姑娘！！！" in provider_prompt
    assert "sings: <d>[Chinese] 哎哟哎哟哎哟花姑娘！！！</d>" in provider_prompt
    assert "不要朗读" not in provider_prompt
    assert "禁止开场说话" not in provider_prompt
    strip_audio.assert_not_awaited()
