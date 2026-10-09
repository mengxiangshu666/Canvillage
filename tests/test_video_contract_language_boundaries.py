"""Language boundaries that keep metadata out of paid audiovisual prompts."""

import pytest

from novelvideo.services import _video_request_contract as contract


def document(body):
    return (
        f"integrated_multimodal_description: {body}\n\n"
        "overall_soundscape: Natural room tone.\n\n"
        "non_diegetic_music: N/A\n\n" + contract.H3_AUDIO_TAIL
    )


@pytest.mark.parametrize("quote", ['"Hello there."', "'Hello there.'", "‘Hello there.’", "『Hello there.』", '"『Hello there.』"'])
def test_nested_script_quotes_do_not_become_visible_text_in_dialogue(quote):
    prompt = contract.build_minimax_h3_provider_prompt(
        "[Shot 1] A person opens a door.", [quote], body_language="en",
    )
    assert prompt.count("<d>[English] Hello there.</d>") == 1
    assert prompt.endswith(contract.H3_AUDIO_TAIL)
    assert prompt.count(contract.H3_AUDIO_TAIL) == 1


@pytest.mark.parametrize("identity", ["person_123", "角色_时期", "person", "123", ""])
def test_internal_speaker_identity_is_never_added_to_visual_body(identity):
    prompt = contract.build_minimax_h3_provider_prompt(
        "[Shot 1] A person opens a door.", ["Hello there."],
        body_language="en", speaker=identity,
    )
    assert "The on-screen speaker (S1)" in prompt
    if identity:
        assert identity not in prompt.split("says:", 1)[0].replace("person opens", "subject opens")
    assert prompt.count("Hello there.") == 1


@pytest.mark.parametrize(("body", "code"), [
    ("钟摆摇动。PROJECT SCRIPT-DERIVED STYLE [abc]", "style_block_in_body"),
    ("钟摆摇动。\nThis is a long English instruction for the movie model.", "latin_prose_in_body"),
    ("钟摆摇动。不要朗读。", "negative_instruction_in_body"),
    ("画面里出现字幕。", "on_screen_text_requested"),
    ("钟摆摇动。Hello there.", "dialogue_repeated_in_body"),
])
def test_final_lint_preserves_content_diagnostics_and_blocking_classification(body, code):
    issues = contract.lint_h3_provider_prompt(document(body), dialogue_text="Hello there.")
    assert code in {issue.code for issue in issues}
    assert (code in {issue.code for issue in contract.blocking_h3_prompt_issues(issues)}) is (
        code in contract.H3_BLOCKING_ISSUE_CODES
    )


def test_section_order_and_empty_document_are_blocking():
    swapped = "overall_soundscape: tone\nintegrated_multimodal_description: [Shot 1] A door opens.\nnon_diegetic_music: N/A\n" + contract.H3_AUDIO_TAIL
    assert "section_order" in {issue.code for issue in contract.blocking_h3_prompt_issues(contract.lint_h3_provider_prompt(swapped))}
    assert [issue.code for issue in contract.lint_h3_provider_prompt(None)] == ["empty_prompt"]


@pytest.mark.parametrize("sound", ["", "none", "n/a", "无", "无音乐", "旁白、台词、不要音乐"])
def test_workflow_sound_empty_or_speech_metadata_cannot_invent_native_voice(sound):
    assert contract.workflow_motion_soundscape({"sound": sound}) == ""


def test_workflow_sound_keeps_only_physical_scene_events():
    assert contract.workflow_motion_soundscape({"sound": "雨声，，旁白；衣料摩擦，禁止音乐。"}) == "雨声、衣料摩擦。"


@pytest.mark.parametrize("value", [None, "", "[运镜]", "[未知：内容][台词：你好]", "[：内容][时长：4秒]"])
def test_partial_or_unknown_workflow_slots_do_not_create_a_full_shot_contract(value):
    assert contract.split_workflow_motion_prompt(value) is None


def test_workflow_duplicate_slot_preserves_first_authoritative_camera_instruction():
    result = contract.split_workflow_motion_prompt("[运镜轨迹：推近][运镜轨迹：拉远][主体动作：开门]")
    assert result == {"camera": "推近", "subject_action": "开门"}


@pytest.mark.parametrize("scene_json", ["broken", "[]", '{"scene_id":"木屋"}'])
def test_soundscape_handles_damaged_scene_metadata_without_private_text_leak(scene_json):
    sound = contract.derive_h3_soundscape({"scene_ref_json": scene_json})
    assert scene_json not in sound
    assert "台词" not in sound and "旁白" not in sound
    if "木屋" in scene_json:
        assert "木结构" in sound


def test_contract_error_rejects_empty_violations_and_serializes_safe_issue_shape():
    with pytest.raises(ValueError, match="at least one issue"):
        contract.VideoRequestContractError([None, "invalid"])
    issue = contract.VideoRequestIssue("fixture", "controlled message", {"count": 1})
    error = contract.VideoRequestContractError([None, issue])
    assert error.issues == (issue,)
    assert issue.as_dict() == {"code": "fixture", "message": "controlled message", "count": 1}
    assert error.provider_error_metadata["request_contract"] == {
        "violations": [{"code": "fixture", "details": {"count": 1}}],
    }


def test_reference_asset_label_is_metadata_but_explicit_speech_still_has_priority():
    prompt = '参考图1提供「客人」的脸型，女孩说：“你好。”'
    result = contract.normalize_video_prompt_for_submission_result(
        prompt, duration_seconds=4, spoken_dialogue="过期台词", dialogue_text=["另一句"],
    )
    assert result.spoken_dialogue == ("你好。",)
    assert "客人" in result.visual_prompt
    assert "你好" not in result.visual_prompt
    assert result.dialogue_structured_overridden is True


def test_whitespace_quotes_do_not_create_spoken_turns():
    assert contract.extract_spoken_dialogue('女孩看向门上的“  ”。', allow_structured_fallback=False) == ()
    prompt = contract.build_minimax_h3_provider_prompt("女孩看向门口。", ['" "', "你好。"])
    assert prompt.count("<d>") == 1
    assert "<d>[Chinese] 你好。</d>" in prompt


@pytest.mark.parametrize("turns", [None, "", "你好。"])
def test_h3_scalar_and_absent_dialogue_keep_audio_tail_and_exact_speech(turns):
    prompt = contract.build_minimax_h3_provider_prompt("钟摆摇动。", turns)
    assert prompt.endswith(contract.H3_AUDIO_TAIL)
    assert prompt.count("你好。") == int(bool(turns))
    assert prompt.count("<d>") == int(bool(turns))


def test_excess_performance_placeholders_cannot_invent_extra_dialogue():
    prompt = contract.build_minimax_h3_provider_prompt(
        "女孩说话表演。女孩说话表演。", ["你好。"],
    )
    assert prompt.count("<d>") == 1
    assert prompt.count("你好。") == 1
    assert "说话表演" not in prompt


def test_precompiled_speech_quotes_survive_english_visual_cleaning():
    prompt = contract.build_minimax_h3_provider_prompt(
        '[Shot 1] A person opens a door. <d>[Chinese] 你好。</d>\nAVOID subtitles',
        body_language="en",
    )
    assert "AVOID" not in prompt
    assert "<d>[Chinese] 你好。</d>" in prompt


@pytest.mark.parametrize("body", ["", "AVOID captions"])
def test_empty_or_metadata_only_english_document_uses_safe_scene_fallback(body):
    prompt = contract.build_minimax_h3_provider_prompt(body, body_language="en")
    assert "A continuous cinematic audiovisual scene." in prompt
    assert "AVOID" not in prompt


def test_english_emotion_lint_does_not_downgrade_content_blocking():
    issues = contract.lint_h3_provider_prompt(document("[Shot 1] A fearful person speaks beside subtitles."))
    codes = {issue.code for issue in issues}
    assert {"emotion_label_in_body", "on_screen_text_requested", "speech_words_in_silent_shot"} <= codes


@pytest.mark.parametrize("picture_only", [False, True])
async def test_compiler_exception_without_logger_still_closes_new_submission(monkeypatch, picture_only):
    from novelvideo.generators.video import h3_body_translation

    async def fail(*args, **kwargs):
        raise RuntimeError("private translator detail")

    monkeypatch.setattr(h3_body_translation, "translate_h3_body_to_english", fail)
    compile_prompt = contract.compile_h3_picture_prompt if picture_only else contract.compile_h3_provider_prompt
    with pytest.raises(ValueError, match="编译失败，已阻止提交") as raised:
        await compile_prompt("钟摆在木地板上方摇动。")
    assert "private translator detail" not in str(raised.value)


async def test_empty_picture_compile_has_no_translation_side_effect(monkeypatch):
    from novelvideo.generators.video import h3_body_translation

    def forbidden(*args, **kwargs):
        raise AssertionError("empty picture cannot call translator")

    monkeypatch.setattr(h3_body_translation, "translate_h3_body_to_english", forbidden)
    assert await contract.compile_h3_picture_prompt(None) == ""


def test_legacy_structured_document_replaces_boilerplate_with_current_scene_sound():
    prompt = document("钟摆摇动。").replace(
        "Natural room tone.", "Natural ambient sound and physical action sounds consistent with the visible scene.",
    )
    result = contract.build_minimax_h3_provider_prompt(prompt, soundscape="雨声。")
    assert "overall_soundscape: 雨声。" in result
    assert "consistent with" not in result
    assert result.count(contract.H3_AUDIO_TAIL) == 1


@pytest.mark.parametrize("meta", ["【对白】", "对白语气：压抑。", "口型同步。", "不要说话。", "[音频：旁白]"])
def test_legacy_structured_document_removes_director_metadata_without_losing_literal_speech(meta):
    source = document(f"女孩抬头。{meta}<d>[Chinese] 我回来了。</d>")
    result = contract.build_minimax_h3_provider_prompt(source)
    assert result.count("<d>[Chinese] 我回来了。</d>") == 1
    assert "女孩抬头" in result
    assert meta not in result


def test_partial_workflow_brackets_retain_unknown_visual_notes_and_remove_empty_known_slots():
    source = "人物站立。[运镜][未知：石墙][运镜轨迹： ][主体动作：开门]"
    result = contract.sanitize_h3_visual_prompt(source, speech_authorized=False)
    assert "人物站立" in result
    assert "[未知：石墙]" in result
    assert "[运镜]" in result
    assert "运镜轨迹" not in result
    assert "开门" in result


def test_audio_sections_survive_a_missing_picture_section_in_sanitizer():
    source = "overall_soundscape: 雨声。\nnon_diegetic_music: N/A"
    assert contract.sanitize_h3_visual_prompt(source) == source


def test_persisted_dialogue_slots_remain_authoritative_after_repeated_normalization():
    first = contract.normalize_video_prompt_for_submission_result(
        '女孩说：“你好。”女孩重复：“你好。”', duration_seconds=4,
    )
    restored = contract.normalize_video_prompt_for_submission_result(
        first.provider_prompt, duration_seconds=4, spoken_dialogue=["过期台词"],
    )
    assert restored.spoken_dialogue == ("你好。", "你好。")
    assert restored.provider_prompt == first.provider_prompt
    assert restored.dialogue_source == "restored"


def test_action_continuation_stays_visual_in_unquoted_dialogue():
    source = "女孩说：你好，镜头推近。"
    result = contract.normalize_video_prompt_for_submission_result(source, duration_seconds=4)
    assert result.spoken_dialogue == ("你好",)
    assert "镜头推近" in result.visual_prompt
    assert "镜头推近" not in result.spoken_dialogue


async def test_metadata_only_native_compile_reaches_translator_then_closes_on_failure(monkeypatch):
    from novelvideo.generators.video import h3_body_translation

    seen = []

    async def unavailable(body, **kwargs):
        seen.append(body)
        return None

    monkeypatch.setattr(h3_body_translation, "translate_h3_body_to_english", unavailable)
    with pytest.raises(ValueError, match="已阻止提交"):
        await contract.compile_h3_provider_prompt("[音频：旁白]")
    assert seen == ["[音频：旁白]"]


def test_reference_only_quote_allows_explicit_dialogue_fallback():
    result = contract.normalize_video_prompt_for_submission_result(
        "参考图1提供「客人」的脸型。", duration_seconds=4, spoken_dialogue=["你好。"],
    )
    assert result.spoken_dialogue == ("你好。",)
    assert "客人" in result.visual_prompt


def test_singing_with_quoted_lyrics_never_duplicates_the_turn():
    assert contract.extract_spoken_dialogue('女孩唱道：“你好世界。”') == ("你好世界。",)


def test_style_snapshot_ends_at_blank_line_or_chinese_visual_content():
    source = "女孩抬头。\nPROJECT SCRIPT-DERIVED STYLE [abc]:\nRich cinematic warm lighting.\nAVOID subtitles\n\n男孩伸手。"
    result = contract.sanitize_h3_visual_prompt(source)
    assert "女孩抬头" in result and "男孩伸手" in result
    assert "STYLE" not in result and "AVOID" not in result
    source = source.replace("\n\n男孩", "\n男孩")
    assert "男孩伸手" in contract.sanitize_h3_visual_prompt(source)


def test_multiline_legacy_document_drops_generation_instruction():
    source = document("女孩抬头。\n请生成一个片段\n男孩伸手。")
    result = contract.build_minimax_h3_provider_prompt(source)
    assert "请生成" not in result
    assert "男孩伸手" in result


def test_silent_speech_cleanup_preserves_a_long_visible_action_clause():
    result = contract.sanitize_h3_visual_prompt("女孩缓慢抬起双手向门口走去，口型同步。", speech_authorized=False)
    assert "女孩缓慢抬起双手向门口走去" in result
    assert "口型同步" not in result


def test_workflow_without_sound_uses_scene_sound_fallback():
    source = "[主体动作：开门][运镜轨迹：推近]"
    sound = contract.resolve_h3_soundscape(source, beat={"visual_description": "雨夜"})
    assert "雨声" in sound
