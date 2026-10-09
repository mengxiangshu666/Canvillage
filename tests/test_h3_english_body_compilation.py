"""H3 的正文必须是英文：中文创作稿要在提交前编译成官方方言。

真机事故（2026-10-04 project 6925 beat 09）：中文运动稿直接进了
``integrated_multimodal_description``，H3 把它当可念剧本，既念成旁白又烧成字幕。
官方规范是「正文英文，台词留原语言在 <d> 里」，本文件把这条钉死。
"""

from __future__ import annotations

import json

import pytest

from novelvideo.freezone.video_request_contract import (
    blocking_h3_prompt_issues,
    build_minimax_h3_provider_prompt,
    compile_h3_picture_prompt,
    compile_h3_provider_prompt,
    lint_h3_provider_prompt,
    sanitize_h3_visual_prompt,
    split_workflow_motion_prompt,
)
from novelvideo.generators.video import h3_body_translation as dialect
from novelvideo.freezone.script_contract import repair_script_rows

ENGLISH_BODY = (
    "[Shot 1] The young man turns away from the offering table and steps forward, "
    "the camera pushing in slowly along his path. His weight settles as his right "
    "foot lands and the hem of his robe swings forward with the movement. He stops "
    "and lowers his gaze to the ground ahead."
)


@pytest.mark.asyncio
async def test_compiler_contract_change_invalidates_cached_translation(monkeypatch):
    from types import SimpleNamespace

    calls = []

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            self.contract = kwargs["system_prompt"]

        async def run(self, payload):
            data = json.loads(payload)
            assert list(data)[-1] == "task"
            assert "持续" in data["shot_description"]
            assert "Apply each state reference at its supplied action phase" in self.contract
            calls.append(self.contract)
            return SimpleNamespace(output=json.dumps({"description": ENGLISH_BODY, "soundscape": "Room tone."}))

    monkeypatch.setattr("pydantic_ai.Agent", FakeAgent)
    monkeypatch.setattr("novelvideo.generators.direct_models.get_direct_pydantic_model", lambda *args, **kwargs: object())
    dialect._CACHE.clear()
    source = "他松开手，栏杆留在身后，持续向前滑行。"
    first = await dialect.translate_h3_body_to_english(source, has_dialogue=False)
    assert await dialect.translate_h3_body_to_english(source, has_dialogue=False) == first
    assert len(calls) == 1
    monkeypatch.setattr(dialect, "H3_TRANSLATOR_SYSTEM_PROMPT", dialect.H3_TRANSLATOR_SYSTEM_PROMPT + "\nPreserve the final held reaction.")
    assert await dialect.translate_h3_body_to_english(source, has_dialogue=False) == first
    assert len(calls) == 2


@pytest.mark.parametrize("source", [
    "保持人物右手握住栏杆，左脚踩在滑板上，随后松手蹬地。",
    "保持构图稳定，人物抬眼看向左侧的门。",
    "保持场景主光从左侧窗户照入，角色迈向门口。",
])
def test_maintained_visual_state_does_not_drop_following_action(source):
    assert sanitize_h3_visual_prompt(source, speech_authorized=False) == source


@pytest.mark.parametrize("prefix", ["首帧中", "输入首帧画面里", "首帧内"])
def test_initial_frame_facts_keep_support_and_following_action(prefix):
    facts = "阿波右手抓住栏杆，左脚踩在滑板上，随后松手蹬地。"
    assert sanitize_h3_visual_prompt(prefix + facts, speech_authorized=False) == "镜头开始时，" + facts
    assert not sanitize_h3_visual_prompt("首帧约束：保持输入主体和场景。", speech_authorized=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("reference_first", [False, True])
async def test_script_state_and_reference_responsibilities_reach_final_h3_body(monkeypatch, reference_first):
    from types import SimpleNamespace

    row = {
        "character_state_start": {"阿波": "赤膊，红色护目镜戴在眼前"},
        "character_state_end": {"阿波": "赤膊，红色护目镜戴在眼前"},
        "video_motion_prompt": "[摄影机运镜：侧跟] + [主体物理动作：阿波左脚踩在板面，右脚蹬地，收回板上站稳] + [环境物理动态：风吹毛发] + [音效：轮声] + [台词：无] + [时长：8s]",
    }
    motion = repair_script_rows([row]).rows[0]["video_motion_prompt"]
    roles = "[视频参考用途：角色阿波引用@图片2，锁定身份；场景屋顶引用@图片3，锁定空间；本镜@图片1用于起始构图、站位与状态；@图片4是本镜状态关键帧，右手释放[栏杆]，只指导释放后的阶段。]"
    persistence = "已释放的接触在后续滑行和结束时持续成立，停顿后望向小雀。"
    geography = "本镜固定空间基准（地标、出入口、尺度与布局）：\n屋顶：红门在水塔西侧两米，南侧平台距栏杆两米。"
    raw = "\n".join([roles, motion, persistence, geography] if reference_first else [motion, roles, persistence, geography])
    translated = "[Shot 1] Reference image 1 sets the starting composition and stance; reference image 2 defines Apo's identity and reference image 3 defines the roof geography. Apo is bare-chested with red goggles over his eyes throughout. His left foot supports him on the board while his right foot pushes against the roof, then returns to the board as his weight settles. The camera tracks alongside. The red door is two meters west of the water tank; the southern platform is two meters from the railing."

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            assert "H3 does not use @reference syntax" in kwargs["system_prompt"]

        async def run(self, payload):
            body = json.loads(payload)["shot_description"]
            for fact in ("赤膊", "红色护目镜戴在眼前", "左脚踩在板面", "右脚蹬地", "镜头开始时", "镜头结束时", "角色阿波引用@图片2", "场景屋顶引用@图片3", "本镜@图片1", "@图片4", "右手释放[栏杆]", "停顿后望向小雀", "持续成立"):
                assert fact in body
            assert "character_state" not in body
            assert '"起始"' not in body
            assert "时长：" not in body
            assert "红门在水塔西侧两米" in body
            assert "南侧平台距栏杆两米" in body
            return SimpleNamespace(output=json.dumps({"description": translated, "soundscape": "Wheels roll over the roof."}))

    monkeypatch.setattr("pydantic_ai.Agent", FakeAgent)
    monkeypatch.setattr("novelvideo.generators.direct_models.get_direct_pydantic_model", lambda *args, **kwargs: object())
    dialect._CACHE.clear()
    result = await compile_h3_provider_prompt(raw)
    assert translated in result
    assert "Wheels roll over the roof." in result
    assert "character_state" not in result
    assert "@图片" not in result
    assert "non_diegetic_music: N/A" in result


@pytest.mark.asyncio
async def test_mixed_language_action_reaches_translation_without_style_metadata(monkeypatch):
    action = "He releases his right hand, shifts his weight onto the board, and pushes off."
    raw = f"阿波握紧栏杆。\n{action}\n\nPROJECT STYLE LOCK:\nPreserve the visual medium and palette."

    async def translate(body, **kwargs):
        assert "阿波握紧栏杆" in body
        assert action.replace(", ", ",") in body
        assert "PROJECT STYLE LOCK" not in body
        assert "Preserve the visual medium" not in body
        return dialect.H3EnglishBody(description=ENGLISH_BODY, soundscape="Quiet ambient air.")

    monkeypatch.setattr(dialect, "translate_h3_body_to_english", translate)
    assert ENGLISH_BODY in await compile_h3_provider_prompt(raw)


def test_long_nested_motion_slots_keep_visual_quality_and_route_audio() -> None:
    environment = "雨滴沿金属护栏滑落，" * 60 + "纹理附着于物体，静止区域帧间稳定，保留自然运动模糊"
    action = "阿波握紧[青色滑板 + 银色桥架]，随后蹬地滑行"
    raw = (
        "[明确的摄影机运镜轨迹与速度：缓慢跟拍] + "
        f"[主体极其具体的物理动作细节或状态变化：{action}] + "
        f"[环境物理动态：{environment}] + "
        "[音效与氛围描述：轮声[近处]、风声] + "
        "[对话台词与语气：阿波[低声]说：冲啊] + [时长：5s]"
    )
    segments = split_workflow_motion_prompt(raw)
    assert segments is not None
    assert segments["subject_action"] == action
    assert segments["environment_motion"] == environment
    assert segments["sound"] == "轮声[近处]、风声"
    cleaned = sanitize_h3_visual_prompt(raw, speech_authorized=False)
    assert action in cleaned
    assert environment in cleaned
    for label in ("主体极其具体", "环境物理动态", "音效与氛围描述", "对话台词", "时长"):
        assert label not in cleaned
    assert "轮声" not in cleaned
    assert "冲啊" not in cleaned
    assert "5s" not in cleaned


@pytest.mark.asyncio
@pytest.mark.parametrize("picture_only", [False, True])
async def test_detailed_motion_quality_reaches_h3_translation(monkeypatch, picture_only) -> None:
    quality = "纹理附着于物体，静止区域帧间稳定，保留自然运动模糊"
    environment = "雨滴沿护栏流动，" * 70 + quality
    raw = (
        "[明确的摄影机运镜轨迹与速度：缓慢跟拍] + "
        "[主体极其具体的物理动作细节或状态变化：阿波握紧[青色滑板]后滑行] + "
        f"[环境物理动态：{environment}] + "
        "[音效与氛围描述：轮声] + [对话台词与语气：无] + [时长：5s]"
    )
    translated = ENGLISH_BODY + " Surface texture stays attached to moving objects and stationary areas stay stable."

    async def fake(body, *, has_dialogue, soundscape="", timeout_seconds=90.0):
        assert quality in body
        assert "青色滑板" in body
        assert "环境物理动态" not in body
        assert "音效与氛围描述" not in body
        assert not has_dialogue
        if not picture_only:
            assert "轮声" in soundscape
        return dialect.H3EnglishBody(description=translated, soundscape="Wheels roll across the surface.")

    monkeypatch.setattr(dialect, "translate_h3_body_to_english", fake)
    result = await compile_h3_picture_prompt(raw) if picture_only else await compile_h3_provider_prompt(raw)
    assert translated in result
    assert ("overall_soundscape:" in result) is not picture_only


def test_workflow_parser_preserves_unknown_and_incomplete_outer_brackets() -> None:
    raw = "[材质：[主体动作：纹理]] [主体动作：抬手] [环境物理动态：未闭合"
    assert split_workflow_motion_prompt(raw) is None
    cleaned = sanitize_h3_visual_prompt(raw, speech_authorized=False)
    assert "[材质：[主体动作：纹理]]" in cleaned
    assert "抬手" in cleaned
    assert "未闭合" in cleaned


@pytest.mark.asyncio
async def test_translation_summary_retains_explicit_script_imaging_facts(monkeypatch):
    class FakeAgent:
        def __init__(self, *args, **kwargs):
            assert "Preserve explicit clean-image" in kwargs["system_prompt"]

        async def run(self, payload):
            return SimpleNamespace(output=json.dumps({"description": ENGLISH_BODY, "soundscape": "Room tone."}))

    from types import SimpleNamespace

    monkeypatch.setattr("pydantic_ai.Agent", FakeAgent)
    monkeypatch.setattr("novelvideo.generators.direct_models.get_direct_pydantic_model", lambda *args, **kwargs: object())
    dialect._CACHE.clear()
    source = "阿波迈步。画面采用低噪声成像，纹理附着于物体并随其运动，静止区域帧间稳定；保留自然运动模糊和真实光影变化。皮肤、毛发、织物、材质纹理和剧情要求的磨损保持可辨识。"
    cleaned = sanitize_h3_visual_prompt(source, speech_authorized=False)
    result = await dialect.translate_h3_body_to_english(cleaned, has_dialogue=False)
    assert result is not None
    for fact in ("Clean low-noise imaging", "Surface texture stays attached", "Stationary areas stay stable", "Natural motion blur", "story-required wear"):
        assert fact in result.description
    assert ENGLISH_BODY in result.description
    assert await dialect.translate_h3_body_to_english(cleaned, has_dialogue=False) == result
    plain = await dialect.translate_h3_body_to_english("阿波迈步。", has_dialogue=False)
    assert plain is not None
    assert plain.description == ENGLISH_BODY
    assert dialect._preserve_imaging_facts(source, result.description) == result.description


def test_english_body_is_not_stripped_by_the_h3_builder() -> None:
    """中文净化器会把整行英文当流程说明删掉；英文正文必须走另一条净化线。"""

    prompt = build_minimax_h3_provider_prompt(
        ENGLISH_BODY,
        ["我就知道。"],
        speaker="小臣_少年时期",
        body_language="en",
    )
    body = prompt.split("overall_soundscape:", 1)[0]

    assert "The young man turns away from the offering table" in body
    assert "lowers his gaze to the ground ahead" in body
    assert "<d>[Chinese] 我就知道。</d>" in body
    assert prompt.endswith("No subtitles, no captions, no text on screen, no dialogue text, no Chinese text overlay appear at any moment in this shot. Dialogue exists only through speech and lip movement. No background music.")


def test_internal_identity_id_and_quotes_never_reach_the_body() -> None:
    """内部身份 id 带下划线，双引号又会被 H3 当画内文字，两者都不进正文。"""

    prompt = build_minimax_h3_provider_prompt(
        ENGLISH_BODY,
        ["我就知道。"],
        speaker="小臣_少年时期",
        body_language="en",
    )
    body = prompt.split("overall_soundscape:", 1)[0]

    assert "小臣" not in body
    assert '"' not in body
    assert "The on-screen speaker (S1)" in body

    named = build_minimax_h3_provider_prompt(
        ENGLISH_BODY,
        ["签收一下。"],
        speaker="邮差",
        body_language="en",
    )
    assert "The on-screen character 邮差 (S1)" in named
    assert '"邮差"' not in named


def test_lint_accepts_an_english_body_but_still_catches_english_mistakes() -> None:
    clean = build_minimax_h3_provider_prompt(
        ENGLISH_BODY,
        ["我就知道。"],
        speaker="小臣_少年时期",
        soundscape="Low room tone with soft footsteps on stone.",
        body_language="en",
    )
    assert lint_h3_provider_prompt(
        clean,
        audio_type="dialogue",
        dialogue_text="我就知道。",
    ) == ()

    # 英文正文里点名画字幕，一样要在送模型前报出来。
    with_subtitles = build_minimax_h3_provider_prompt(
        english_body_with("Subtitles appear along the bottom edge."),
        [],
        body_language="en",
    )
    codes = {issue.code for issue in lint_h3_provider_prompt(with_subtitles, audio_type="silence")}
    assert "on_screen_text_requested" in codes

    # 静默镜头里出现「说话」词，英文词表也要照出来。
    silent_with_speech = build_minimax_h3_provider_prompt(
        english_body_with("He speaks quietly toward the empty room."),
        [],
        body_language="en",
    )
    codes = {
        issue.code
        for issue in lint_h3_provider_prompt(silent_with_speech, audio_type="silence")
    }
    assert "speech_words_in_silent_shot" in codes


def test_known_text_and_silent_speech_findings_block_provider_dispatch() -> None:
    with_subtitles = build_minimax_h3_provider_prompt(
        english_body_with("Subtitles appear along the bottom edge."),
        [],
        body_language="en",
    )
    subtitle_issues = lint_h3_provider_prompt(with_subtitles, audio_type="silence")
    assert {issue.code for issue in blocking_h3_prompt_issues(subtitle_issues)} == {
        "on_screen_text_requested"
    }

    silent_with_speech = build_minimax_h3_provider_prompt(
        english_body_with("He speaks quietly toward the empty room."),
        [],
        body_language="en",
    )
    speech_issues = lint_h3_provider_prompt(silent_with_speech, audio_type="silence")
    assert {issue.code for issue in blocking_h3_prompt_issues(speech_issues)} == {
        "speech_words_in_silent_shot"
    }


def english_body_with(extra: str) -> str:
    return f"{ENGLISH_BODY} {extra}"


@pytest.mark.asyncio
async def test_compile_translates_the_chinese_body_before_submission(monkeypatch) -> None:
    seen: dict[str, object] = {}

    async def _fake(body, *, has_dialogue, soundscape="", timeout_seconds=90.0):
        seen["body"] = body
        seen["has_dialogue"] = has_dialogue
        seen["soundscape"] = soundscape
        return dialect.H3EnglishBody(
            description=ENGLISH_BODY,
            soundscape="Night-time low room tone with cloth movement.",
        )

    monkeypatch.setattr(dialect, "translate_h3_body_to_english", _fake)

    prompt = await compile_h3_provider_prompt(
        (
            "从输入首帧的小臣转过身体背向供桌迈步开始，镜头沿主体方向平滑推近。"
            "说话者从首帧的面部状态开始保持连续表演，随后连续完成停步说话表演。"
            "运动在约4.0秒内沿同一方向延续，保持当前可见主体、空间和光线连续。"
        ),
        ["我就知道。"],
        speaker="小臣_少年时期",
        beat={"visual_description": "祠堂内，小臣停步"},
    )
    body = prompt.split("overall_soundscape:", 1)[0]

    assert seen["has_dialogue"] is True
    # 进翻译的是已经净化过的中文正文：流程说明不能跟着一起翻。
    assert "小臣转过身体背向供桌迈步" in str(seen["body"])
    assert "运动在约" not in str(seen["body"])
    assert "首帧的面部状态" not in str(seen["body"])

    assert body.startswith("integrated_multimodal_description: [Shot 1] The young man")
    assert "小臣" not in body
    assert "<d>[Chinese] 我就知道。</d>" in body
    assert "overall_soundscape: Night-time low room tone" in prompt
    assert prompt.endswith("No subtitles, no captions, no text on screen, no dialogue text, no Chinese text overlay appear at any moment in this shot. Dialogue exists only through speech and lip movement. No background music.")
    assert lint_h3_provider_prompt(
        prompt,
        audio_type="dialogue",
        dialogue_text="我就知道。",
    ) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unavailable", "exception"])
@pytest.mark.parametrize("picture_only", [False, True])
async def test_compile_stops_before_submission_when_translation_is_unavailable(
    monkeypatch, failure, picture_only,
) -> None:
    """The subtitle prohibition wins over the old Chinese fallback."""

    async def _broken(body, *, has_dialogue, soundscape="", timeout_seconds=90.0):
        if failure == "exception":
            raise RuntimeError("private-provider-detail")
        return None

    monkeypatch.setattr(dialect, "translate_h3_body_to_english", _broken)
    logged: list[str] = []

    with pytest.raises(ValueError, match="英文.*编译失败，已阻止提交"):
        if picture_only:
            await compile_h3_picture_prompt(
                "小臣转过身体背向供桌迈步，镜头缓慢推近。", on_log=logged.append,
            )
        else:
            await compile_h3_provider_prompt(
                "小臣转过身体背向供桌迈步，镜头缓慢推近。",
                ["我就知道。"], speaker="小臣_少年时期", on_log=logged.append,
            )
    assert "阻止提交" in logged[-1]
    assert "private-provider-detail" not in " ".join(logged)


@pytest.mark.asyncio
async def test_picture_only_compile_stays_english_without_dialogue_sections(
    monkeypatch,
) -> None:
    async def _fake(body, *, has_dialogue, soundscape="", timeout_seconds=90.0):
        assert has_dialogue is False
        return dialect.H3EnglishBody(description=ENGLISH_BODY)

    monkeypatch.setattr(dialect, "translate_h3_body_to_english", _fake)

    compiled = await compile_h3_picture_prompt(
        "镜头沿主体方向平滑推近，人物背向供桌迈步。说话表演",
        speech_authorized=False,
    )

    assert compiled == ENGLISH_BODY
    assert "overall_soundscape" not in compiled
    assert "<d>" not in compiled


@pytest.mark.asyncio
@pytest.mark.parametrize(("beat", "expected"), [
    (None, "Natural environmental room tone"),
    ({"visual_description": "祠堂内，深夜"}, "Low ambient night-time room tone; Subtle creaks"),
    ({"visual_description": "暴雨中的山道"}, "Continuous rain striking surfaces; Wind passing"),
    ({"visual_description": "石阶旁的树林"}, "Soft footsteps and friction against stone; Leaves rustling"),
    ({"visual_description": "火炉"}, "Low sounds of flames and vibrating air"),
    ({"visual_description": "街市"}, "Natural environmental room tone"),
])
async def test_english_submission_default_sound_needs_no_text_model(monkeypatch, beat, expected) -> None:
    def unavailable(*args, **kwargs):
        raise AssertionError("English video submission must not call a text model")

    monkeypatch.setattr(
        "novelvideo.generators.direct_models.get_direct_pydantic_model", unavailable,
    )
    prompt = await compile_h3_provider_prompt(ENGLISH_BODY, ["Hello there."], beat=beat)
    assert "The young man turns away from the offering table" in prompt
    assert f"overall_soundscape: {expected}" in prompt
    assert "<d>[English] Hello there.</d>" in prompt
    assert "环境" not in prompt
    assert "No background music." in prompt


@pytest.mark.asyncio
async def test_english_submission_preserves_explicit_chinese_sound_translation(monkeypatch) -> None:
    seen = []

    async def translate(body, *, has_dialogue, soundscape="", timeout_seconds=90.0):
        seen.append(soundscape)
        return dialect.H3EnglishBody(ENGLISH_BODY, "Rain strikes the window.")

    monkeypatch.setattr(dialect, "translate_h3_body_to_english", translate)
    prompt = await compile_h3_provider_prompt(ENGLISH_BODY, soundscape="雨打窗户。")
    assert seen == ["雨打窗户。"]
    assert "overall_soundscape: Rain strikes the window." in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("translated_sound", ["", "雨打窗户。"])
async def test_missing_or_untranslated_custom_sound_stops_submission(monkeypatch, translated_sound):
    async def translate(*args, **kwargs):
        return dialect.H3EnglishBody(ENGLISH_BODY, translated_sound)

    monkeypatch.setattr(dialect, "translate_h3_body_to_english", translate)
    with pytest.raises(ValueError, match="声音段英文编译失败"):
        await compile_h3_provider_prompt(ENGLISH_BODY, soundscape="雨打窗户。")


@pytest.mark.asyncio
async def test_missing_generated_sound_keeps_the_scene_through_deterministic_translation(monkeypatch):
    async def translate(*args, **kwargs):
        return dialect.H3EnglishBody(ENGLISH_BODY)

    monkeypatch.setattr(dialect, "translate_h3_body_to_english", translate)
    prompt = await compile_h3_provider_prompt(
        "雨夜，一人走上山道。", beat={"visual_description": "雨夜，山道"},
    )
    assert "Continuous rain striking surfaces; Wind passing through open terrain" in prompt
    assert "雨" not in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("picture_only", [False, True])
async def test_english_visual_body_survives_removing_avoid_metadata(monkeypatch, picture_only):
    def unavailable(*args, **kwargs):
        raise AssertionError("English body must not require a text model")

    monkeypatch.setattr(
        "novelvideo.generators.direct_models.get_direct_pydantic_model", unavailable,
    )
    raw = ENGLISH_BODY + "\nAVOID subtitles, music, watermark"
    result = (
        await compile_h3_picture_prompt(raw)
        if picture_only else await compile_h3_provider_prompt(raw)
    )
    assert "The young man turns away from the offering table" in result
    assert "lowers his gaze to the ground ahead" in result
    assert "AVOID" not in result


@pytest.mark.asyncio
async def test_already_english_body_skips_the_translation_call(monkeypatch) -> None:
    def _boom(*args, **kwargs):  # pragma: no cover - 不该被调用
        raise AssertionError("已经是英文的正文不该再花一次模型调用")

    monkeypatch.setattr(
        "novelvideo.generators.direct_models.get_direct_pydantic_model",
        _boom,
    )
    compiled = await dialect.translate_h3_body_to_english(
        ENGLISH_BODY,
        has_dialogue=True,
    )

    assert compiled is not None
    assert compiled.description.startswith("[Shot 1]")


@pytest.mark.asyncio
async def test_translation_rejects_chinese_or_empty_output(monkeypatch) -> None:
    current: dict[str, str] = {"output": ""}

    class _FakeAgent:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def run(self, payload: str):
            class _Response:
                output = current["output"]

            return _Response()

    monkeypatch.setattr(
        "novelvideo.generators.direct_models.get_direct_pydantic_model",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr("pydantic_ai.Agent", _FakeAgent)
    # 缓存按内容键隔离：先清掉旧条目，保证这一次真的走模型调用。
    dialect._CACHE.clear()

    current["output"] = json.dumps({"description": "小臣背向供桌迈步。", "soundscape": ""})
    assert (
        await dialect.translate_h3_body_to_english("小臣背向供桌迈步。", has_dialogue=False)
        is None
    )

    dialect._CACHE.clear()
    current["output"] = "not json at all"
    assert (
        await dialect.translate_h3_body_to_english("小臣背向供桌迈步。", has_dialogue=False)
        is None
    )


@pytest.mark.asyncio
async def test_native_builder_delegates_non_h3_backends_unchanged() -> None:
    from novelvideo.services.video_request_contract import (
        abuild_native_video_provider_prompt,
    )

    class _Normalization:
        spoken_dialogue = ("我就知道。",)
        provider_prompt = "raw provider prompt"
        dialogue_is_sung = False

    compiled = await abuild_native_video_provider_prompt(
        "direct_video-not-h3",
        "小臣转过身体背向供桌迈步。",
        _Normalization(),
    )

    assert compiled == "raw provider prompt"


def test_scripted_dialogue_quotes_never_reach_the_d_tag() -> None:
    """剧本台词自带双引号；H3 把双引号当画内可见文字，进 <d> 前必须剥掉。

    真机事故（2026-10-04 project 6925 beat 09）：数据库里的台词是
    ``"我就知道。"``，引号跟着进了 ``<d>``，成片里台词被烧成画面字幕。
    """

    prompt = build_minimax_h3_provider_prompt(
        ENGLISH_BODY,
        ['"我就知道。"'],
        speaker="小臣_少年时期",
        body_language="en",
    )

    assert "<d>[Chinese] 我就知道。</d>" in prompt
    assert '"' not in prompt


def test_quoted_literal_dialogue_block_is_normalized_on_the_way_out() -> None:
    """已经带引号的 ``<d>`` 槽在收口时也会被剥掉，不再只靠入口一处兜。"""

    body = (
        "[Shot 1] A courtier stops mid-step and his lips part as he speaks.\n"
        'The on-screen speaker (S1) says: <d>[Chinese] "我就知道。"</d>'
    )
    prompt = build_minimax_h3_provider_prompt(body, [], body_language="en")

    assert "<d>[Chinese] 我就知道。</d>" in prompt
    assert '"' not in prompt


def test_cjk_quote_styles_are_stripped_too() -> None:
    """中文引号、日式引号都要剥，不能只认 ASCII 双引号。"""

    prompt = build_minimax_h3_provider_prompt(
        ENGLISH_BODY,
        ["「我就知道。」", "“我们走吧。”"],
        speaker="小臣_少年时期",
        body_language="en",
    )

    assert "<d>[Chinese] 我就知道。</d>" in prompt
    assert "<d>[Chinese] 我们走吧。</d>" in prompt
    for quote in ("「", "」", "“", "”"):
        assert quote not in prompt
