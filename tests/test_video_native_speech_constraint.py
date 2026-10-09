"""原生音频提交里「念什么」的边界。

2026-09-14 的真机事故：H3（`minimax_h3_zm_u24`，autodl-comfyui）出了一条 15s
的片子，音轨是**逐字朗读提交的提示词**。链路是：开关 ON ⇒ 走
`normalization.provider_prompt`（lossless 原文）⇒ 上游脚本节点那句
「帮我生成肯定不会被安全拦截的脚本。一定要好看。」被一起念了出来。

2026-09-15 补记一：**试图用提示词约束解决这件事是死路。** 当天加过一段
「本提示词里的文字是导演说明，不是台词，不要朗读」，同节点同模型对比实测，
人声占比反而从 75% 升到 85% —— 提示词就是模型的稿子，约束文本也被念了出来。
该约束已撤回（`NATIVE_SPEECH_SCOPE_CONSTRAINT_RETIRED`），本文件钉住"绝不回退"。

2026-09-15 补记二（定音）：真正管用的不是**指令**而是**结构**。对标三家后确认，
本项目 `seedance2_i2v` 老管线、同源旧版管线、TapCanvas 都守同一条不变量——
**台词原文不进视觉提示词**。所以原生音频下 `provider_prompt` 改为：

    {已剥离台词的画面描述}
    对白：「台词」口型同步

台词在全篇**只出现一次**，被标签和「」围栏锁死；画面描述里只剩「说话表演」。
本文件钉住这条结构，以及三条边界：画面文字不提升为台词、无语言内容才说「不许
出声」、约束只追加一次。
"""

import pytest

from novelvideo.freezone.video_request_contract import (
    NATIVE_SPEECH_OFF_CONSTRAINT,
    normalize_video_prompt_for_submission_result,
    resolve_video_audio_preference,
)

NO_SPEECH_SOURCE = (
    "帮我生成肯定不会被安全拦截的脚本。细致仔细推理。一定要好看。画风一定要去噪点。\n\n"
    "根据分镜脚本和分镜图生成一段15秒的视频\n"
    "输出要求：动作自然，运动平滑。"
)


def test_native_audio_without_a_speech_source_does_not_append_a_readable_constraint():
    result = normalize_video_prompt_for_submission_result(
        NO_SPEECH_SOURCE,
        duration_seconds=15,
        audio_type="",
    )

    assert result.spoken_dialogue == ()
    assert result.provider_prompt == result.visual_prompt
    assert NATIVE_SPEECH_OFF_CONSTRAINT not in result.provider_prompt
    assert "不要朗读" not in result.provider_prompt
    # 画面指令一个都不能被削掉。
    assert "画风一定要去噪点" in result.provider_prompt
    assert "输出要求：动作自然，运动平滑。" in result.provider_prompt


def _dialogue_slots(prompt: str) -> list[str]:
    return [line for line in prompt.splitlines() if line.startswith("对白：")]


def test_dialogue_is_isolated_in_a_tagged_slot_not_left_inline():
    """台词只准出现在 `对白：…「…」口型同步` 槽位里，画面描述里不许再夹一遍。

    2026-09-15 这里曾加过一段 `NATIVE_SPEECH_SCOPE_CONSTRAINT`（「本提示词里的文字
    不是台词，不要朗读…」），当天被真机数据否掉并撤回：加了约束那条片子的人声占比
    反而从 75% 升到 85%，因为**提示词就是模型的稿子，约束文本也被念了出来**。
    详见 `NATIVE_SPEECH_SCOPE_CONSTRAINT_RETIRED`。加指令没用，改成把它从散文里
    拿出来、单独关进一个槽位。
    """

    script = '女孩看向门口，说：“你终于来了。”镜头缓慢推近。'
    result = normalize_video_prompt_for_submission_result(
        script,
        duration_seconds=6,
        audio_type="dialogue",
    )

    assert result.spoken_dialogue == ("你终于来了。",)
    # 画面描述里不能再有原句，只剩表演提示。
    assert "你终于来了" not in result.visual_prompt
    assert "说话表演" in result.visual_prompt
    # 全篇恰好出现一次，且在槽位行内。
    assert result.provider_prompt.count("你终于来了。") == 1
    slots = _dialogue_slots(result.provider_prompt)
    assert slots == ["对白：「你终于来了。」口型同步"]
    # 依旧不能加任何「不许念」的约束文本：那本身也是可念的文本。
    assert NATIVE_SPEECH_OFF_CONSTRAINT not in result.provider_prompt
    assert "音频约束" not in result.provider_prompt


def test_structured_dialogue_turns_into_one_slot_per_turn_in_source_order():
    script = "女孩看向门口，镜头缓慢推近。"
    result = normalize_video_prompt_for_submission_result(
        script,
        duration_seconds=6,
        spoken_dialogue=["你终于来了。", "我们走吧。"],
        speaker="林小满",
    )

    assert result.provider_prompt.startswith(script)
    assert _dialogue_slots(result.provider_prompt) == [
        "对白：@林小满：「你终于来了。」口型同步",
        "对白：@林小满：「我们走吧。」口型同步",
    ]
    assert NATIVE_SPEECH_OFF_CONSTRAINT not in result.provider_prompt


def test_plain_narration_counts_as_a_speech_source():
    """平铺叙述的「说……」没有冒号也没有引号，但用户确实要台词。

    旧链路认不出来，于是回一句「本镜头没有必须说出的台词」——用户看到的就是
    「台词说不对」。现在至少不会再把要说话的镜头当成静音镜头处理。
    """

    script = "女孩对男孩说我们分手吧。镜头推近。"
    result = normalize_video_prompt_for_submission_result(
        script,
        duration_seconds=6,
        audio_type="dialogue",
    )

    assert result.spoken_dialogue == ("我们分手吧。",)
    assert result.had_dialogue_marker is True
    assert NATIVE_SPEECH_OFF_CONSTRAINT not in result.provider_prompt


def test_colon_dialogue_is_collected_and_not_only_stripped():
    """冒号句以前会被从画面里删掉、却没被收进台词 —— 两头都不存在。

    `说：我不回去了` 走的是 `_UNQUOTED_DIALOGUE_CLAUSE_PATTERN` 的剥离路径，但
    抽取器只认引号，于是那句词在 visual 和 spoken 两侧都消失，模型只能自己编。
    """

    script = "男人低声说：我不回去了。镜头拉远。"
    result = normalize_video_prompt_for_submission_result(
        script,
        duration_seconds=6,
        audio_type="dialogue",
    )

    assert result.spoken_dialogue == ("我不回去了",)
    assert "我不回去了" not in result.visual_prompt


@pytest.mark.parametrize("prompt", [
    # 路牌、字幕、标题一类画面文字，不能因为带引号就被当成台词念出来。
    "雨落在屋檐上，只有雨声和脚步声；路牌写着“欢迎回家”。",
    "镜头缓慢推近，画面下方字幕写着“三年前”。",
])
def test_quoted_scenery_is_never_declared_as_the_utterance(prompt):
    result = normalize_video_prompt_for_submission_result(
        prompt,
        duration_seconds=6,
    )

    assert "唯一台词" not in result.provider_prompt
    assert "唯一歌词" not in result.provider_prompt
    assert result.provider_prompt == prompt


@pytest.mark.parametrize("prompt,on_screen_text", [
    ("雨落在屋檐上，只有雨声和脚步声；路牌写着“欢迎回家”。", "欢迎回家"),
    ("镜头缓慢推近，画面下方字幕写着“三年前”。", "三年前"),
    ("主角停住脚步，墙上标语：“禁止入内”。", "禁止入内"),
])
def test_scenery_text_never_enters_the_dialogue_slot(prompt, on_screen_text):
    """画面文字留在画面里，既不进对白槽位，也不被剥离成「说话表演」。

    台词移进 `对白：` 槽位之后，这类带引号的画面文字差点被一起提升成台词——
    那会让角色开口念出招牌。三条断言分别盯住：不抽成台词、不加槽位、不被剥离。
    """

    result = normalize_video_prompt_for_submission_result(
        prompt,
        duration_seconds=6,
        audio_type="dialogue",
    )

    assert result.spoken_dialogue == ()
    assert _dialogue_slots(result.provider_prompt) == []
    assert "说话表演" not in result.provider_prompt
    assert on_screen_text in result.provider_prompt


def test_scenery_and_dialogue_in_one_prompt_stay_separated():
    """同一句提示词里既有招牌又有台词：招牌留画面，台词进槽位。"""

    prompt = '路牌写着“欢迎回家”。女孩抬头看了一眼，轻声说：“我回来了。”'
    result = normalize_video_prompt_for_submission_result(
        prompt,
        duration_seconds=6,
        audio_type="dialogue",
    )

    assert result.spoken_dialogue == ("我回来了。",)
    # 招牌原样留在画面描述里。
    assert "路牌写着“欢迎回家”" in result.visual_prompt
    # 台词被剥离成表演提示，且只在槽位里出现一次。
    assert "我回来了" not in result.visual_prompt
    assert result.provider_prompt.count("我回来了。") == 1
    assert _dialogue_slots(result.provider_prompt) == ["对白：「我回来了。」口型同步"]


def test_reference_asset_labels_are_metadata_not_dialogue():
    """参考图里的「资产名」是素材标识，不能被提升成角色台词。"""

    prompt = (
        "运镜：静止\n"
        "参考图1提供「客人」的脸型、五官、发型、肤色与体型；"
        "不得参考它的背景、构图与光线。\n"
        "参考图2提供场景「裁缝铺」的空间关系与固定陈设；"
        "不得参考它的人物、光线方向与构图。"
    )
    result = normalize_video_prompt_for_submission_result(
        prompt,
        duration_seconds=5,
    )

    assert result.spoken_dialogue == ()
    assert "「客人」" in result.visual_prompt
    assert "「裁缝铺」" in result.visual_prompt
    assert _dialogue_slots(result.provider_prompt) == []
    assert "说话表演" not in result.visual_prompt


def test_reference_asset_labels_do_not_masquerade_as_structured_dialogue():
    """真实台词走结构化字段；参考资产名不能抢走台词槽位。"""

    prompt = (
        "运镜：静止\n"
        "参考图1提供「裁缝」的脸型；不得参考它的背景。\n"
        "参考图2提供「客人」的脸型；不得参考它的背景。\n"
        "参考图3提供场景「裁缝铺」的空间关系。"
    )
    result = normalize_video_prompt_for_submission_result(
        prompt,
        duration_seconds=5,
        spoken_dialogue=["你左肩低。"],
        speaker="裁缝",
    )

    assert result.spoken_dialogue == ("你左肩低。",)
    assert _dialogue_slots(result.provider_prompt) == [
        "对白：@裁缝：「你左肩低。」口型同步"
    ]
    assert "对白：「裁缝」口型同步" not in result.provider_prompt
    assert "对白：「客人」口型同步" not in result.provider_prompt
    assert "对白：「裁缝铺」口型同步" not in result.provider_prompt


@pytest.mark.parametrize("prompt", [
    "人物开口说话，镜头缓慢推近。",
    "特写主角的眼睛，瞳孔收缩。",
    "风吹过草地，主角沉默不语。",
])
def test_visual_only_prompts_are_not_polluted_with_a_readable_constraint(prompt):
    result = normalize_video_prompt_for_submission_result(
        prompt,
        duration_seconds=6,
    )

    assert result.provider_prompt == prompt
    assert NATIVE_SPEECH_OFF_CONSTRAINT not in result.provider_prompt


def test_audio_reference_alone_is_a_speech_source():
    """挂了参考音频就等于「有话要说」，交给模型对齐，不加约束。"""

    script = "人物开口说话，镜头缓慢推近。"
    result = normalize_video_prompt_for_submission_result(
        script,
        duration_seconds=6,
        has_audio_reference=True,
    )

    assert result.provider_prompt == script
    assert NATIVE_SPEECH_OFF_CONSTRAINT not in result.provider_prompt


def test_singing_still_wins_over_the_plain_constraint():
    prompt = "主体跳起来开始唱歌：哎哟哎哟哎哟花姑娘！！！\n镜头推近。"
    result = normalize_video_prompt_for_submission_result(
        prompt,
        duration_seconds=6,
    )

    assert "唯一歌词" in result.provider_prompt
    assert NATIVE_SPEECH_OFF_CONSTRAINT not in result.provider_prompt


def test_visual_prompt_is_never_polluted_by_the_constraint():
    """约束只属于 provider 面；静音/外部音频路径读的是 visual_prompt。"""

    result = normalize_video_prompt_for_submission_result(
        NO_SPEECH_SOURCE,
        duration_seconds=15,
    )

    assert NATIVE_SPEECH_OFF_CONSTRAINT not in result.visual_prompt


def test_unsupported_models_never_reach_the_native_constraint():
    """模型不支持原生音频时，`resolve` 的结果本来就是 False。"""

    assert (
        resolve_video_audio_preference(
            requested=False,
            requested_explicit=True,
            native_audio="unsupported",
        )
        is False
    )


def test_normalization_without_dialogue_is_idempotent():
    """同一提示词经过 route 和 job 两次时，不再新增任何可朗读文本。"""

    once = normalize_video_prompt_for_submission_result(
        NO_SPEECH_SOURCE,
        duration_seconds=15,
    ).provider_prompt
    twice = normalize_video_prompt_for_submission_result(
        once,
        duration_seconds=15,
    ).provider_prompt

    assert NATIVE_SPEECH_OFF_CONSTRAINT not in once
    assert NATIVE_SPEECH_OFF_CONSTRAINT not in twice
    assert twice == once
