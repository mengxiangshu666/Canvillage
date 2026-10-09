"""Reachable malformed author inputs at the public video language boundary."""

import pytest

from novelvideo.services import _video_request_contract as contract


@pytest.mark.parametrize("source", ['唱道：   ', '唱道：" "', '唱道："Hello."'])
def test_blank_lyrics_do_not_create_a_spoken_turn(source):
    expected = ("Hello.",) if "Hello" in source else ()
    assert contract.extract_spoken_dialogue(source, allow_structured_fallback=False) == expected


def test_duration_zero_and_duplicate_mentions_do_not_override_selected_duration():
    assert contract.extract_prompt_duration_mentions(
        "duration 0 seconds; duration 4 seconds; duration 4 seconds"
    ) == (4,)


def test_empty_precompiled_literal_dialogue_is_preserved_for_validation():
    source = (
        'integrated_multimodal_description: [Shot 1] A door opens. <d>[English] " "</d>\n\n'
        "overall_soundscape: Room tone.\n\nnon_diegetic_music: N/A"
    )
    result = contract.build_minimax_h3_provider_prompt(source, body_language="en")
    assert '<d>[English] " "</d>' in result
    assert result.endswith(contract.H3_AUDIO_TAIL)
    assert "Hello" not in result


def test_blank_lines_do_not_discard_visible_scene_action():
    result = contract.build_minimax_h3_provider_prompt("钟摆摇动。\n\n窗帘随风晃动。")
    assert "钟摆摇动" in result
    assert "窗帘随风晃动" in result
    assert "<d>" not in result


def test_speech_cue_removal_keeps_the_physical_action_without_inventing_dialogue():
    result = contract.build_minimax_h3_provider_prompt("女孩抬起手臂开口。")
    assert "女孩抬起手臂" in result
    assert "开口" not in result
    assert "<d>" not in result


def test_extra_persisted_speech_placeholders_cannot_repeat_one_authorized_turn():
    result = contract.build_minimax_h3_provider_prompt(
        "[Shot 1] A woman opens the door. 说话表演 说话表演",
        ["Hello there."], body_language="en",
    )
    assert result.count("<d>[English] Hello there.</d>") == 1
    assert "说话表演" not in result
    assert result.endswith(contract.H3_AUDIO_TAIL)


@pytest.mark.parametrize("turns", [(), ("",), ("", "")])
def test_native_dialogue_slot_builder_keeps_picture_when_no_usable_turn_exists(turns):
    assert contract._build_native_dialogue_provider_prompt("A door opens.", turns) == "A door opens."


@pytest.mark.parametrize("empty", [None, ""])
def test_pipeline_metadata_filter_accepts_absent_picture_without_inventing_content(empty):
    assert contract._strip_h3_pipeline_meta(empty) == ""


@pytest.mark.parametrize("empty", [None, "", "  \n"])
def test_empty_h3_audio_tail_input_still_has_the_required_no_text_no_music_rule(empty):
    assert contract._ensure_h3_audio_tail(empty) == contract.H3_AUDIO_TAIL


@pytest.mark.parametrize("empty", ["", "  \n"])
@pytest.mark.parametrize("authorized", [False, True])
def test_visual_clause_cleaner_never_promotes_empty_clause_to_speech(empty, authorized):
    assert contract._clean_h3_visual_clause(empty, speech_authorized=authorized) == ""


def test_empty_colon_dialogue_does_not_create_a_spoken_turn():
    assert contract.extract_spoken_dialogue('女孩说：""', allow_structured_fallback=False) == ()


def test_motion_metadata_without_a_landing_target_does_not_become_visual_copy():
    prompt = "钟摆摇动。运动在约4秒内沿同一方向延续，最后落在   。"
    result = contract.build_minimax_h3_provider_prompt(prompt)
    assert "钟摆摇动" in result
    assert "运动在约" not in result
    assert "末拍停在" not in result


@pytest.mark.parametrize("empty", [None, "", " \n "])
def test_absent_scalar_spoken_field_does_not_authorize_dialogue(empty):
    result = contract.normalize_video_prompt_for_submission_result(
        "钟摆摇动。", duration_seconds=4, spoken_dialogue=empty, dialogue_text=empty,
    )
    assert result.spoken_dialogue == ()
    assert "对白：" not in result.provider_prompt
