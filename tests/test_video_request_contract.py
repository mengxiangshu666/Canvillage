from __future__ import annotations

from types import SimpleNamespace
from novelvideo.generators.video.capabilities import NativeAudio, VideoMode

from novelvideo.freezone.video_request_contract import (
    append_video_no_text_tail,
    extract_prompt_duration_mentions,
    extract_prompt_reference_tokens,
    normalize_video_prompt_for_submission,
    validate_structured_video_capability,
    validate_video_request_contract,
)


def test_video_no_text_tail_is_added_once_for_non_h3_prompts():
    prompt = append_video_no_text_tail("雨夜车站，人物向前走。")
    assert "no subtitles" in prompt
    assert "no on-screen text" in prompt
    assert "No background music" in prompt
    assert append_video_no_text_tail(prompt) == prompt
    empty = append_video_no_text_tail("")
    assert "no subtitles" in empty
    assert "No background music" in empty


def test_extracts_explicit_duration_phrases_without_treating_generic_numbers_as_duration():
    assert extract_prompt_duration_mentions("固定镜头，持续 6 秒，画面有 3 个人") == (6,)
    assert extract_prompt_duration_mentions("A 12-second video") == (12,)
    assert extract_prompt_duration_mentions("镜头中有 6 个人，桌上有 12 本书") == ()
    assert extract_prompt_duration_mentions("镜头在 [0-2s] 推近，随后 [2-6s] 停留") == ()


def test_prompt_duration_mismatch_is_diagnostic_not_blocking():
    issues = validate_video_request_contract(
        prompt="镜头持续 12 秒，人物向前走。",
        duration_seconds=5,
    )

    assert issues == ()


def test_accepts_matching_duration_and_reference_binding():
    issues = validate_video_request_contract(
        prompt="视频持续 6 秒，@图片1 中的人物向镜头走来。",
        duration_seconds=6,
        reference_items=[{"type": "image", "path": "frame.png", "role": "first"}],
    )

    assert issues == ()


def test_submission_normalizer_removes_only_redundant_total_duration():
    prompt = "生成 6 秒视频：雨夜车站，镜头在 [0-2s] 推近。"

    normalized = normalize_video_prompt_for_submission(
        prompt,
        duration_seconds=6,
    )

    assert normalized == "生成视频：雨夜车站，镜头在 [0-2s] 推近。"
    assert validate_video_request_contract(
        prompt=normalized,
        duration_seconds=6,
    ) == ()


def test_submission_normalizer_keeps_mismatched_duration_as_diagnostic():
    prompt = "制作 12 秒视频：雨夜车站。"

    normalized = normalize_video_prompt_for_submission(
        prompt,
        duration_seconds=6,
    )

    assert normalized == prompt
    assert validate_video_request_contract(
        prompt=normalized,
        duration_seconds=6,
    ) == ()


def test_submission_normalizer_strips_unauthorized_speech_cues():
    normalized = normalize_video_prompt_for_submission(
        '人物说：“这是一段画面描述。”，镜头缓慢推近。',
        duration_seconds=5,
    )

    assert "说：" not in normalized
    assert "“" not in normalized and "”" not in normalized
    assert "这是一段画面描述" not in normalized
    assert "说话表演" in normalized


def test_submission_normalizer_keeps_explicit_dialogue_out_of_visual_prompt():
    prompt = '人物说：“你终于来了。”，镜头缓慢推近。'
    normalized = normalize_video_prompt_for_submission(
        prompt,
        duration_seconds=5,
        dialogue_authorized=True,
    )

    assert "你终于来了" not in normalized
    assert "说：" not in normalized
    assert "镜头缓慢推近" in normalized


def test_submission_normalizer_returns_spoken_dialogue_as_a_separate_field():
    from novelvideo.freezone.video_request_contract import (
        normalize_video_prompt_for_submission_result,
    )

    result = normalize_video_prompt_for_submission_result(
        '人物说：“别回头。”，镜头缓慢推近。',
        duration_seconds=5,
    )

    assert result.visual_prompt == "人物说话表演，镜头缓慢推近。"
    assert result.spoken_dialogue == ("别回头。",)
    assert result.dialogue_text == "别回头。"
    assert result.had_dialogue_marker is True


def test_submission_normalizer_extracts_singing_lyrics_and_bounds_native_audio():
    from novelvideo.freezone.video_request_contract import (
        normalize_video_prompt_for_submission_result,
    )

    prompt = (
        "@图片1主体 跳起来开始唱歌：哎哟哎哟哎哟花姑娘！！！\n"
        "首帧约束：严格继承输入图片中的主体和场景。\n"
        "输出要求：动作自然，运动平滑。"
    )
    result = normalize_video_prompt_for_submission_result(
        prompt,
        duration_seconds=5,
    )

    assert result.spoken_dialogue == ("哎哟哎哟哎哟花姑娘！！！",)
    assert "哎哟哎哟哎哟花姑娘" not in result.visual_prompt
    assert "演唱表演" in result.visual_prompt
    assert "从第 0 秒直接演唱" in result.provider_prompt
    assert "禁止开场说话" in result.provider_prompt
    assert "哎哟哎哟哎哟花姑娘！！！" in result.provider_prompt


def test_plain_singing_description_without_lyric_marker_is_not_promoted_to_dialogue():
    from novelvideo.freezone.video_request_contract import extract_spoken_dialogue

    assert extract_spoken_dialogue("人物唱歌，镜头缓慢推近。") == ()


def test_mixed_spoken_and_sung_turns_keep_prompt_order():
    from novelvideo.freezone.video_request_contract import extract_spoken_dialogue

    prompt = '女孩说：“先听我说。”\n男孩唱歌：啦啦啦！\n女孩说：“走吧。”'
    assert extract_spoken_dialogue(prompt) == ("先听我说。", "啦啦啦！", "走吧。")


def test_submission_normalizer_accepts_structured_dialogue_without_echoing_it():
    from novelvideo.freezone.video_request_contract import (
        normalize_video_prompt_for_submission_result,
    )

    result = normalize_video_prompt_for_submission_result(
        "角色抬头看向门口，镜头缓慢推近。",
        duration_seconds=5,
        dialogue_text="你终于来了。",
        audio_type="dialogue",
        speaker="谢铮",
    )

    assert result.visual_prompt == "角色抬头看向门口，镜头缓慢推近。"
    assert result.spoken_dialogue == ("你终于来了。",)
    assert "你终于来了" not in result.visual_prompt


def test_optional_native_audio_uses_the_explicit_canvas_switch():
    from novelvideo.freezone.video_request_contract import resolve_video_audio_preference

    assert (
        resolve_video_audio_preference(
            requested=True,
            requested_explicit=None,
            audio_type="dialogue",
            native_audio="optional",
            has_spoken_dialogue=True,
            has_external_audio=False,
        )
        is True
    )
    assert (
        resolve_video_audio_preference(
            requested=True,
            requested_explicit=True,
            audio_type="dialogue",
            native_audio="optional",
            has_spoken_dialogue=True,
            has_external_audio=False,
        )
        is True
    )
    assert (
        resolve_video_audio_preference(
            requested=True,
            requested_explicit=True,
            audio_type="dialogue",
            native_audio="optional",
            native_audio_strategy="native",
            has_spoken_dialogue=True,
            has_external_audio=False,
        )
        is True
    )
    assert (
        resolve_video_audio_preference(
            requested=False,
            requested_explicit=True,
            audio_type="dialogue",
            native_audio="optional",
            native_audio_strategy="native",
            has_spoken_dialogue=True,
        )
        is False
    )
    assert (
        resolve_video_audio_preference(
            requested=True,
            requested_explicit=True,
            audio_type="dialogue",
            native_audio="optional",
            native_audio_strategy="external",
            has_spoken_dialogue=True,
        )
        is True
    )
    assert (
        resolve_video_audio_preference(
            requested=True,
            requested_explicit=None,
            audio_type="dialogue",
            native_audio="optional",
            has_spoken_dialogue=True,
        )
        is True
    )
    assert (
        resolve_video_audio_preference(
            requested=False,
            requested_explicit=False,
            audio_type="silence",
            native_audio="required",
        )
        is True
    )


def test_rejects_explicit_image_reference_without_an_image_payload():
    assert extract_prompt_reference_tokens("保持 @图片1 的角色外观") == ("@图片1",)
    issues = validate_video_request_contract(
        prompt="保持 @图片1 的角色外观。",
        duration_seconds=5,
    )

    assert [issue.code for issue in issues] == ["prompt_reference_missing"]
    assert issues[0].details["referenceCounts"] == {
        "image": 0,
        "video": 0,
        "audio": 0,
    }


def test_does_not_reject_plain_image_prose_without_explicit_token():
    issues = validate_video_request_contract(
        prompt="画面展示一张图片风格的海报，持续 5 秒。",
        duration_seconds=5,
    )

    assert issues == ()


def test_structured_capability_preflight_rejects_parameters_before_queue(monkeypatch):
    capability = SimpleNamespace(
        model_id="direct/fixture",
        modes=(VideoMode.IMAGE_TO_VIDEO,),
        duration=(4, 8),
        resolution=("720p",),
        aspect=("16:9",),
        native_audio=NativeAudio.UNSUPPORTED,
        reference_limits=SimpleNamespace(
            input_images=1,
            reference_images=0,
            reference_videos=0,
            reference_audios=0,
            to_dict=lambda: {
                "input_images": 1,
                "reference_images": 0,
                "reference_videos": 0,
                "reference_audios": 0,
            },
        ),
    )
    monkeypatch.setattr(
        "novelvideo.generators.video.direct_models.resolve_direct_video_model",
        lambda _backend: SimpleNamespace(capability=capability),
    )

    issues = validate_structured_video_capability(
        backend="direct_fixture",
        mode="imageToVideo",
        duration_seconds=5,
        resolution="1080p",
        aspect_ratio="9:16",
        generate_audio=True,
        reference_items=[],
    )

    assert [issue.code for issue in issues] == [
        "unsupported_duration",
        "unsupported_resolution",
        "unsupported_aspect_ratio",
        "native_audio_unsupported",
        "reference_role_mismatch",
    ]
    assert all(issue.details["modelId"] == "direct/fixture" for issue in issues)
    assert all(issue.details["capabilityRevision"] for issue in issues)


def test_structured_capability_preflight_keeps_explicit_upstream_custom_values(monkeypatch):
    capability = SimpleNamespace(
        model_id="direct/custom",
        modes=(VideoMode.TEXT_TO_VIDEO,),
        duration=(5,),
        resolution=("720p",),
        aspect=("16:9",),
        supports_custom_resolution=True,
        supports_custom_aspect_ratio=True,
        native_audio=NativeAudio.UNSUPPORTED,
        reference_limits=SimpleNamespace(
            input_images=0,
            reference_images=0,
            reference_videos=0,
            reference_audios=0,
            to_dict=lambda: {},
        ),
    )
    monkeypatch.setattr(
        "novelvideo.generators.video.direct_models.resolve_direct_video_model",
        lambda _backend: SimpleNamespace(capability=capability),
    )

    issues = validate_structured_video_capability(
        backend="direct_custom",
        mode="textToVideo",
        duration_seconds=5,
        resolution="1440p",
        aspect_ratio="2.39:1",
    )

    assert issues == ()


def test_structured_capability_preflight_accepts_provider_orientation_resolution(monkeypatch):
    capability = SimpleNamespace(
        model_id="direct/autodl",
        modes=(VideoMode.IMAGE_TO_VIDEO,),
        duration=tuple(range(1, 16)),
        resolution=("480p", "768p"),
        aspect=("9:16", "16:9"),
        supports_custom_resolution=False,
        supports_custom_aspect_ratio=False,
        native_audio=NativeAudio.OPTIONAL,
        reference_limits=SimpleNamespace(
            input_images=2,
            reference_images=9,
            reference_videos=0,
            reference_audios=3,
            to_dict=lambda: {},
        ),
    )
    profile = SimpleNamespace(resolution=("480p竖", "768p竖", "480p横", "768p横"))
    monkeypatch.setattr(
        "novelvideo.generators.video.direct_models.resolve_direct_video_model",
        lambda _backend: SimpleNamespace(capability=capability, profile=profile),
    )

    issues = validate_structured_video_capability(
        backend="direct_autodl",
        mode="imageToVideo",
        duration_seconds=5,
        resolution="768p竖",
        aspect_ratio="9:16",
        reference_items=[{"type": "image", "path": "frame.png", "role": "first"}],
    )

    assert issues == ()


def test_comma_introduced_inline_dialogue_is_extracted_and_fenced():
    """T-218 回归（task 415d6973 真机事故）：H3 乱说话的根因。

    正文里「在说话表演，<台词>。」这种**逗号引导、无引号无冒号**的台词，此前
    三种形态都抽不到，于是留在画面描述里；结构化对白槽又兜底进了另一句，H3
    （原生音频 REQUIRED，关不掉）同报文收到两个说话指令，必然乱说。
    """

    from novelvideo.freezone.video_request_contract import (
        extract_spoken_dialogue,
        strip_dialogue_text_from_visual_prompt,
    )

    prompt = (
        "并且，他在做这些动作的同时在说话表演，遇见你之后，我就一直没有过上好日子。"
    )
    # 提示词里写明的台词优先于可能过期的结构化字段（既有权威顺序）。
    assert extract_spoken_dialogue(
        prompt, dialogue_text="你这个傻瓜", spoken_dialogue=("你这个傻瓜",)
    ) == ("遇见你之后，我就一直没有过上好日子",)

    stripped = strip_dialogue_text_from_visual_prompt(
        prompt, spoken_text="遇见你之后，我就一直没有过上好日子"
    )
    assert "遇见你之后" not in stripped
    assert "说话表演" in stripped


def test_comma_introduced_dialogue_normalizes_to_a_single_fenced_slot():
    from novelvideo.freezone.video_request_contract import (
        normalize_video_prompt_for_submission_result,
    )

    prompt = (
        "@图片1 小猫开始跳。并且，他在做这些动作的同时在说话表演，"
        "遇见你之后，我就一直没有过上好日子。\n"
        "拒绝：画面闪烁、人物变形。"
    )
    result = normalize_video_prompt_for_submission_result(
        prompt,
        duration_seconds=5,
        dialogue_text="你这个傻瓜",
        spoken_dialogue=("你这个傻瓜",),
    )

    # 台词全 prompt 只出现一次：画面描述里没有了，槽位里有一句。
    assert result.visual_prompt.count("遇见你之后") == 0
    assert result.spoken_dialogue == ("遇见你之后，我就一直没有过上好日子",)
    assert result.dialogue_text == "遇见你之后，我就一直没有过上好日子"
    assert "遇见你之后" in result.provider_prompt
    assert "你这个傻瓜" not in result.provider_prompt
    assert "对白：" in result.provider_prompt and "口型同步" in result.provider_prompt


def test_comma_form_does_not_swallow_action_or_camera_continuations():
    from novelvideo.freezone.video_request_contract import extract_spoken_dialogue

    # 逗号后是动作/镜头续写，不是台词。
    assert extract_spoken_dialogue("人物开口说话，镜头推近。") == ()
    assert extract_spoken_dialogue("在说话表演，然后转身离开。") == ()
    assert extract_spoken_dialogue("他在说话表演，画面背景虚化。") == ()
    # 剥离器自己的表演提示（「说话表演」+句末标点）二遍不得被读回成台词。
    assert extract_spoken_dialogue("他在做这些动作的同时在说话表演。") == ()


def test_omni_reference_line_never_invents_references():
    """T-221/JEV A1：参考行必须按实际连接生成，不得谎报（真机根因之一）。"""

    from novelvideo.freezone.video_node import build_freezone_omni_video_prompt

    none = build_freezone_omni_video_prompt(user_prompt="小猫跳舞。")
    assert "参考视频" not in none and "参考音频" not in none
    assert "综合文本与已连接的" not in none  # 没有参考就不声称有

    one_image = build_freezone_omni_video_prompt(
        user_prompt="小猫跳舞。", reference_counts={"image_count": 1}
    )
    assert "1 张参考图" in one_image
    assert "参考视频" not in one_image and "参考音频" not in one_image

    mixed = build_freezone_omni_video_prompt(
        user_prompt="小猫跳舞。",
        reference_counts={"image_count": 2, "video_count": 1, "audio_count": 1},
    )
    assert "2 张参考图" in mixed and "1 段参考视频" in mixed and "1 段参考音频" in mixed


def test_required_native_audio_without_dialogue_is_blocked():
    """T-221/JEV A2：必出声模型 + 声明要说话 + 无台词 => 交回用户。"""

    from novelvideo.freezone.video_request_contract import (
        required_native_audio_without_dialogue,
    )

    assert required_native_audio_without_dialogue(
        prompt="小猫跳舞。", audio_type="dialogue", native_audio="required"
    )
    assert not required_native_audio_without_dialogue(
        prompt="小猫说：你好。", audio_type="dialogue", native_audio="required"
    )
    assert not required_native_audio_without_dialogue(
        prompt="小猫跳舞。", audio_type="silence", native_audio="required"
    )
    assert not required_native_audio_without_dialogue(
        prompt="小猫跳舞。", audio_type="dialogue", native_audio="optional"
    )


def test_broken_quote_dialogue_is_extracted_without_formatting_demands():
    """T-221/JEV A3：作者写的引号常是碎的，产品应为任意写法兜底。"""

    from novelvideo.freezone.video_request_contract import extract_spoken_dialogue

    # 半角/全角混用、只写半个、夹省略号：真机 task a149bd2c 的形态。
    assert extract_spoken_dialogue(
        '在说：”你...你这个傻瓜，遇见你之后，我就一直没有过上好日子！"'
    ) == ("你...你这个傻瓜，遇见你之后，我就一直没有过上好日子",)
    # 台词自己的逗号不能被腰斩。
    assert extract_spoken_dialogue(
        "他说：你这个傻瓜，遇见你之后，我就一直没有过上好日子！"
    ) == ("你这个傻瓜，遇见你之后，我就一直没有过上好日子",)
    # 一句里两个说话人拆成两轮。
    assert extract_spoken_dialogue(
        "女孩说：你终于来了，男孩回应：我们走吧。镜头推近。"
    ) == ("你终于来了", "我们走吧")
    # 镜头/动作续写留在画面描述里。
    turns = extract_spoken_dialogue("人物说：“别回头。”，镜头缓慢推近。")
    from novelvideo.freezone.video_request_contract import (
        strip_dialogue_text_from_visual_prompt,
    )

    assert turns == ("别回头。",)
    stripped = strip_dialogue_text_from_visual_prompt(
        "人物说：“别回头。”，镜头缓慢推近。", spoken_text="，".join(turns)
    )
    assert "镜头缓慢推近" in stripped and "别回头" not in stripped


def test_dialogue_source_and_structured_override_are_reported():
    """T-221/JEV A4：来源与「正文覆盖了对白字段」要能被回执披露。"""

    from novelvideo.freezone.video_request_contract import (
        normalize_video_prompt_for_submission_result,
    )

    overridden = normalize_video_prompt_for_submission_result(
        "并且，他在说话表演，遇见你之后，我就一直没有过上好日子。",
        duration_seconds=10,
        dialogue_text="你这个傻瓜",
        spoken_dialogue=("你这个傻瓜",),
    )
    assert overridden.dialogue_source == "prompt"
    assert overridden.dialogue_structured_overridden is True
    assert overridden.spoken_dialogue == ("遇见你之后，我就一直没有过上好日子",)

    plain = normalize_video_prompt_for_submission_result(
        "小猫跳舞，说：你好。", duration_seconds=5
    )
    assert plain.dialogue_source == "prompt"
    assert plain.dialogue_structured_overridden is False
