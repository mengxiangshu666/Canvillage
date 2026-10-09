import pytest

from novelvideo.services.video_request_contract import (
    normalize_submission_prompt,
    prepare_video_submission,
)


def test_preflight_keeps_prompt_contract_and_audio_default() -> None:
    prompt = "村长走进雨夜，镜头缓慢推进。"
    expected_normalization, expected_visual = normalize_submission_prompt(
        prompt,
        duration_seconds=5,
        spoken_dialogue=("你好。",),
        reference_items=(),
    )

    result = prepare_video_submission(
        prompt,
        duration_seconds=5,
        spoken_dialogue=("你好。",),
        requested_audio=True,
        native_audio_strategy="native",
    )

    assert result.normalization == expected_normalization
    assert result.visual_prompt == expected_visual
    assert result.spoken_dialogue == ("你好。",)
    assert result.effective_generate_audio is True
    assert result.strip_provider_audio is False


def test_preflight_marks_explicit_silence_for_local_audio_gate() -> None:
    result = prepare_video_submission(
        "无对白的街景。",
        duration_seconds=5,
        requested_audio=False,
        requested_audio_explicit=True,
        native_audio="supported",
        audio_type="silence",
    )

    assert result.effective_generate_audio is False
    assert result.strip_provider_audio is True

@pytest.mark.parametrize(
    ("structured", "flat", "expected"),
    [
        (("Hello.",), "Goodbye.", ("Hello.", "Goodbye.")),
        (("Hello.",), "Hello.", ("Hello.",)),
        (("Hello.", "Goodbye."), "Hello. Goodbye.", ("Hello.", "Goodbye.")),
        ((), "Hello.", ("Hello.",)),
        ((" Hello. ", "", None), "", ("Hello.",)),
        (("Again.", "Again."), "", ("Again.", "Again.")),
    ],
)
def test_preflight_merges_dialogue_once_without_repeating_a_turn(
    structured, flat, expected,
) -> None:
    result = prepare_video_submission(
        "Two people stand beside a window.", duration_seconds=5,
        spoken_dialogue=structured, dialogue_text=flat,
        requested_audio=True, requested_audio_explicit=True,
    )
    assert result.spoken_dialogue == expected
    assert result.normalization.provider_prompt.count("Goodbye.") == expected.count("Goodbye.")
    assert result.effective_generate_audio is True
    assert result.strip_provider_audio is False
    normalization, visual = normalize_submission_prompt(
        "Two people stand beside a window.", duration_seconds=5,
        spoken_dialogue=structured, dialogue_text=flat,
    )
    assert result.normalization == normalization
    assert result.visual_prompt == visual
