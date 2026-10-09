"""Application service audio and final compiled prompt contracts."""

from types import SimpleNamespace

import pytest

from novelvideo.services import video_request_contract as service
from novelvideo.services import _video_request_contract as contract


@pytest.mark.parametrize(("kwargs", "expected"), [
    ({"requested": True, "requested_explicit": True, "native_audio_strategy": "external", "audio_type": "silence"}, (True, False)),
    ({"requested": False, "requested_explicit": True, "native_audio": "required"}, (True, True)),
    ({"requested": False, "requested_explicit": True, "native_audio_strategy": "native"}, (False, True)),
    ({"requested": True, "requested_explicit": False, "audio_type": "dialogue"}, (True, False)),
    ({"requested": True, "native_audio": "unsupported"}, (False, True)),
    ({"requested": True, "requested_explicit": True, "native_audio": "unsupported"}, (False, True)),
    ({"native_audio_strategy": "native"}, (True, False)),
    ({"requested": True, "audio_asset_ref": "voice.wav"}, (False, True)),
    ({"requested": True, "spoken_dialogue": ("Hello.",)}, (True, False)),
    ({"requested": False, "requested_explicit": True, "spoken_dialogue": ("Hello.",)}, (False, True)),
])
def test_audio_service_matrix_preserves_switch_and_legacy_contract(kwargs, expected):
    assert service.resolve_submission_audio(**kwargs) == expected


@pytest.mark.parametrize("audio", [False, True])
async def test_final_h3_compilation_accepts_legal_picture_and_native_documents(monkeypatch, audio):
    monkeypatch.setattr(service, "_is_h3_backend", lambda backend: True)
    normalization = contract.normalize_video_prompt_for_submission_result(
        "A pendulum swings above a wooden floor.", duration_seconds=4,
    )
    compiled = await service.compile_and_enforce_native_video_provider_prompt(
        "fixture", normalization.visual_prompt, normalization,
        generate_audio=audio, audio_type="silence",
    )
    assert "pendulum" in compiled
    assert ("overall_soundscape:" in compiled) is audio


@pytest.mark.parametrize("audio", [False, True])
async def test_final_h3_compilation_still_blocks_text_and_unscripted_speech(monkeypatch, audio):
    monkeypatch.setattr(service, "_is_h3_backend", lambda backend: True)
    normalization = SimpleNamespace(spoken_dialogue=(), dialogue_is_sung=False)
    with pytest.raises(ValueError, match="on_screen_text_requested"):
        await service.compile_and_enforce_native_video_provider_prompt(
            "fixture", "Subtitles appear along the bottom edge.", normalization,
            generate_audio=audio,
        )


def test_reference_audio_keeps_speech_action_in_semantic_visual_contract():
    normalized, visual = service.normalize_submission_prompt(
        "A person speaks beside a window.", duration_seconds=4,
        reference_items=[{"type": "audio", "path": "voice.wav"}],
    )
    assert "speaks" in visual
    assert normalized.spoken_dialogue == ()


@pytest.mark.parametrize(("native", "explicit", "requested", "effective", "strip"), [
    ("optional", True, True, True, False),
    ("optional", True, False, False, True),
    ("required", True, False, True, True),
    ("required", None, False, True, True),
    ("unsupported", None, True, False, True),
    ("optional", False, True, True, False),
])
def test_preflight_audio_matrix_keeps_literal_dialogue_once_and_local_gate(
    native, explicit, requested, effective, strip,
):
    result = service.prepare_video_submission(
        'A person says: "Hello there."', duration_seconds=4,
        dialogue_text="Stale speech.", requested_audio=requested,
        requested_audio_explicit=explicit, native_audio=native,
    )
    assert result.spoken_dialogue == ("Hello there.",)
    assert result.normalization.provider_prompt.count("Hello there.") == 1
    assert "Hello there." not in result.visual_prompt
    assert "Stale speech." not in result.normalization.provider_prompt
    assert result.normalization.dialogue_structured_overridden is True
    assert result.effective_generate_audio is effective
    assert result.strip_provider_audio is strip


@pytest.mark.parametrize(("profile", "upstream", "expected"), [
    ("minimax-h3", "unrelated-id", True),
    ("", "minimax-h3", True),
    ("other", "unrelated-id", False),
])
def test_model_resolution_uses_profile_or_upstream_identity(monkeypatch, profile, upstream, expected):
    model = SimpleNamespace(profile=SimpleNamespace(family=profile), upstream_model=upstream, label="fixture")
    monkeypatch.setattr("novelvideo.generators.video.direct_models.resolve_direct_video_model", lambda backend: model)
    assert service._is_h3_backend("direct_fixture") is expected


@pytest.mark.parametrize("audio", [False, True])
async def test_final_non_h3_keeps_native_dialogue_and_visual_only_contract(monkeypatch, audio):
    monkeypatch.setattr(service, "_is_h3_backend", lambda backend: False)
    normalized = contract.normalize_video_prompt_for_submission_result(
        'A person says: "Hello there."', duration_seconds=4,
    )
    compiled = await service.compile_and_enforce_native_video_provider_prompt(
        "fixture", normalized.visual_prompt, normalized, generate_audio=audio,
    )
    assert compiled == (normalized.provider_prompt if audio else normalized.visual_prompt)
    assert compiled.count("Hello there.") == int(audio)


@pytest.mark.parametrize("audio", [False, True])
async def test_translation_error_propagates_through_final_service_before_provider(monkeypatch, audio):
    from novelvideo.generators.video import h3_body_translation

    monkeypatch.setattr(service, "_is_h3_backend", lambda backend: True)

    async def unavailable(*args, **kwargs):
        raise RuntimeError("private upstream error")

    monkeypatch.setattr(h3_body_translation, "translate_h3_body_to_english", unavailable)
    logs = []
    with pytest.raises(ValueError, match="编译失败，已阻止提交"):
        await service.compile_and_enforce_native_video_provider_prompt(
            "fixture", "钟摆在木地板上方缓慢摇动。", SimpleNamespace(spoken_dialogue=()),
            generate_audio=audio, on_log=logs.append,
        )
    assert "private upstream error" not in " ".join(logs)
    assert any("阻止提交" in item for item in logs)


def test_public_dialogue_facade_preserves_repeat_source_and_visual_separation():
    prompt = '女孩说：“我们走吧。”女孩重复：“我们走吧。”'
    normalized = service.normalize_video_prompt_for_submission_result(
        prompt, duration_seconds=4, dialogue_text="outdated", audio_type="dialogue",
    )
    assert normalized.spoken_dialogue == ("我们走吧。", "我们走吧。")
    assert service.extract_spoken_dialogue(prompt) == normalized.spoken_dialogue
    assert service.normalize_video_prompt_for_submission(prompt, duration_seconds=4) == normalized.visual_prompt
    assert "我们走吧" not in service.strip_dialogue_text_from_visual_prompt(prompt)
    assert service.required_native_audio_without_dialogue(
        prompt="A person stands by a door.", audio_type="dialogue", native_audio="required",
    ) is True
    assert service.required_native_audio_without_dialogue(
        spoken_dialogue=normalized.spoken_dialogue, audio_type="dialogue", native_audio="required",
    ) is False


def test_public_h3_facade_preserves_language_dialogue_and_tail():
    assert service.is_minimax_h3_model_identifier("minimax-h3") is True
    cleaned = service.sanitize_h3_visual_prompt("钟摆摇动。口型同步。", speech_authorized=False)
    assert "口型同步" not in cleaned
    prompt = service.build_minimax_h3_provider_prompt(
        "[Shot 1] A person opens a door.", ["你好。"],
        speaker="Person", soundscape="Door creaks.", body_language="en",
    )
    assert prompt.count("<d>[Chinese] 你好。</d>") == 1
    assert prompt.endswith(contract.H3_AUDIO_TAIL)


def test_public_resolution_and_audio_facades_accept_upstream_values():
    assert service.normalize_video_resolution_value("864×480") == "864x480"
    assert service.normalize_video_resolution_value("invalid") is None
    assert service.explicit_audio_type_requests_silence(" action ") is True
    assert service.explicit_audio_type_requests_silence("dialogue") is False
    assert service.should_strip_unrequested_native_audio(requested=True, requested_explicit=True) is False


def test_empty_structured_turns_allow_flat_dialogue_fallback():
    normalized, _ = service.normalize_submission_prompt(
        "A person stands beside a door.", duration_seconds=4,
        spoken_dialogue=(), dialogue_text="Hello there.",
    )
    assert normalized.spoken_dialogue == ("Hello there.",)


def test_native_prompt_falls_back_to_visual_when_old_normalization_has_no_payload(monkeypatch):
    monkeypatch.setattr(service, "_is_h3_backend", lambda backend: False)
    assert service.build_native_video_provider_prompt(
        "fixture", "A pendulum swings.", SimpleNamespace(provider_prompt=""),
    ) == "A pendulum swings."


def test_legacy_unstructured_dialogue_field_is_not_mistaken_for_turn_list():
    normalized, _ = service.normalize_submission_prompt(
        "A person stands by a door.", duration_seconds=4,
        spoken_dialogue=None, dialogue_text="Hello there.",
    )
    assert normalized.spoken_dialogue == ("Hello there.",)


def test_sync_h3_builder_retains_dialogue_in_native_contract(monkeypatch):
    monkeypatch.setattr(service, "_is_h3_backend", lambda backend: True)
    normalized = contract.normalize_video_prompt_for_submission_result(
        '女孩说：“我们走吧。”', duration_seconds=4,
    )
    prompt = service.build_native_video_provider_prompt(
        "fixture", normalized.visual_prompt, normalized, generate_audio=True,
    )
    assert prompt.count("<d>[Chinese] 我们走吧。</d>") == 1
    assert prompt.endswith(contract.H3_AUDIO_TAIL)
