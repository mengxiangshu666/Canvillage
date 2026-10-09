from dataclasses import FrozenInstanceError

import pytest

from novelvideo.generators.video import (
    NativeAudio,
    ReferenceKind,
    ReferenceRole,
    UnknownVideoModelError,
    VideoGenerationRequest,
    VideoMode,
    VideoReference,
    VideoRequestPreflightError,
    build_newapi_video_catalog,
    compile_video_request,
)


def catalog(*models: str, audio_models: tuple[str, ...] = ()):
    return build_newapi_video_catalog(
        models=list(models),
        audio_models=list(audio_models),
        duration_bounds=(
            "seedance-1.0-pro-fast:2-12,seedance-2.0-fast:4-15,"
            "happyhorse-1.0:3-15"
        ),
    ).registry


def request(
    model: str = "seedance-2.0-fast",
    *,
    mode: VideoMode = VideoMode.TEXT_TO_VIDEO,
    references: tuple[VideoReference, ...] = (),
    duration: int = 5,
    resolution: str = "720p",
    aspect: str = "16:9",
    native_audio: bool = False,
    return_last_frame: bool = False,
    prompt: str = "  主角向镜头走来  ",
) -> VideoGenerationRequest:
    return VideoGenerationRequest(
        model_key=model,
        mode=mode,
        prompt=prompt,
        duration_seconds=duration,
        resolution=resolution,
        aspect_ratio=aspect,
        references=references,
        native_audio=native_audio,
        return_last_frame=return_last_frame,
    )


def image(uri: str, role: ReferenceRole) -> VideoReference:
    return VideoReference(ReferenceKind.IMAGE, uri, role)


def test_compile_is_immutable_deterministic_and_pins_revisions() -> None:
    registry = catalog("seedance-2.0-fast")
    compiled = compile_video_request(request(), registry)
    repeated = compile_video_request(request(), registry)

    assert compiled.compiled_prompt == "  主角向镜头走来  "
    assert compiled.model_id == "seedance-2.0-fast"
    assert compiled.provider == "newapi"
    assert compiled.adapter == "newapi"
    assert compiled.catalog_revision.startswith("newapi-builtin-2026-07-18+")
    assert compiled.model_revision.endswith("@newapi-capabilities.v2")
    assert compiled.compiler_revision == "video-prompt-compiler.v1"
    assert compiled.normalized_input_hash == repeated.normalized_input_hash
    assert len(compiled.normalized_input_hash) == 64
    with pytest.raises(FrozenInstanceError):
        compiled.model_id = "other"  # type: ignore[misc]


def test_shadow_modes_match_existing_seedance_request_selection() -> None:
    registry = catalog("seedance-2.0-fast", audio_models=("seedance-2.0-fast",))
    fixtures = [
        request(),
        request(
            mode=VideoMode.IMAGE_TO_VIDEO,
            references=(image("first.png", ReferenceRole.FIRST_FRAME),),
        ),
        request(
            mode=VideoMode.FIRST_LAST_FRAME,
            references=(
                image("first.png", ReferenceRole.FIRST_FRAME),
                image("last.png", ReferenceRole.LAST_FRAME),
            ),
            return_last_frame=True,
        ),
        request(
            mode=VideoMode.REFERENCE_TO_VIDEO,
            references=(
                VideoReference(ReferenceKind.IMAGE, "identity.png"),
                VideoReference(ReferenceKind.VIDEO, "motion.mp4"),
                VideoReference(ReferenceKind.AUDIO, "voice.wav"),
            ),
            native_audio=True,
        ),
    ]

    assert [compile_video_request(item, registry).request.mode for item in fixtures] == [
        VideoMode.TEXT_TO_VIDEO,
        VideoMode.IMAGE_TO_VIDEO,
        VideoMode.FIRST_LAST_FRAME,
        VideoMode.REFERENCE_TO_VIDEO,
    ]


def test_request_rejects_ambiguous_or_incomplete_mode_shapes() -> None:
    with pytest.raises(ValueError, match="text_to_video"):
        request(references=(image("first.png", ReferenceRole.FIRST_FRAME),))
    with pytest.raises(ValueError, match="exactly one first frame"):
        request(mode=VideoMode.IMAGE_TO_VIDEO)
    with pytest.raises(ValueError, match="requires a first frame"):
        request(
            mode=VideoMode.FIRST_LAST_FRAME,
            references=(image("last.png", ReferenceRole.LAST_FRAME),),
        )
    with pytest.raises(ValueError, match="ordinary references"):
        request(mode=VideoMode.REFERENCE_TO_VIDEO)
    with pytest.raises(ValueError, match="no frame-role inputs"):
        request(
            mode=VideoMode.REFERENCE_TO_VIDEO,
            references=(
                image("first.png", ReferenceRole.FIRST_FRAME),
                VideoReference(ReferenceKind.IMAGE, "identity.png"),
            ),
        )
    with pytest.raises(ValueError, match="must be an image"):
        VideoReference(
            ReferenceKind.VIDEO,
            "clip.mp4",
            ReferenceRole.FIRST_FRAME,
        )


@pytest.mark.parametrize(
    ("bad_request", "message"),
    [
        (request(duration=3), "duration"),
        (request(resolution="1080p"), "resolution"),
        (request(aspect="1:1"), "aspect ratio"),
        (request(native_audio=True), "native audio"),
    ],
)
def test_capability_preflight_rejects_unsupported_contract(
    bad_request: VideoGenerationRequest,
    message: str,
) -> None:
    with pytest.raises(VideoRequestPreflightError, match=message):
        compile_video_request(bad_request, catalog("seedance-2.0-fast"))


def test_reference_limits_are_checked_by_media_kind() -> None:
    references = tuple(
        VideoReference(ReferenceKind.VIDEO, f"motion-{index}.mp4") for index in range(4)
    )
    with pytest.raises(VideoRequestPreflightError, match="reference_videos count 4.*limit 3"):
        compile_video_request(
            request(mode=VideoMode.REFERENCE_TO_VIDEO, references=references),
            catalog("seedance-2.0-fast"),
        )


def test_seedance_audio_reference_requires_visual_reference() -> None:
    audio_only = (VideoReference(ReferenceKind.AUDIO, "voice.wav"),)
    with pytest.raises(VideoRequestPreflightError, match="image or video"):
        compile_video_request(
            request(mode=VideoMode.REFERENCE_TO_VIDEO, references=audio_only),
            catalog("seedance-2.0-fast", audio_models=("seedance-2.0-fast",)),
        )


def test_happyhorse_video_edit_enforces_conditional_five_image_limit() -> None:
    references = (
        VideoReference(ReferenceKind.VIDEO, "source.mp4"),
        *(VideoReference(ReferenceKind.IMAGE, f"ref-{index}.png") for index in range(6)),
    )
    with pytest.raises(VideoRequestPreflightError, match="at most 5 reference images"):
        compile_video_request(
            request(
                "happyhorse-1.0",
                mode=VideoMode.REFERENCE_TO_VIDEO,
                references=references,
            ),
            catalog("happyhorse-1.0"),
        )


def test_happyhorse_prompt_compiler_preserves_2500_character_boundary() -> None:
    compiled = compile_video_request(
        request(
            "happyhorse-1.0",
            duration=5,
            prompt="镜" * 2500,
        ),
        catalog("happyhorse-1.0"),
    )

    assert len(compiled.compiled_prompt) == 2500
    assert compiled.warnings == ()


def test_happyhorse_does_not_discard_action_ending_before_hashing() -> None:
    registry = catalog("happyhorse-1.0")
    for ending in ("甲", "乙"):
        with pytest.raises(VideoRequestPreflightError, match="未截断或提交"):
            compile_video_request(request("happyhorse-1.0", prompt="镜" * 2500 + ending), registry)


def test_unknown_model_has_no_fallback_or_provider_side_effect() -> None:
    with pytest.raises(UnknownVideoModelError):
        compile_video_request(request("not-configured"), catalog("seedance-2.0-fast"))


def test_audio_overlay_changes_preflight_contract_without_dispatch() -> None:
    registry = catalog("seedance-2.0-fast", audio_models=("seedance-2.0-fast",))
    capability = registry.resolve("seedance-2.0-fast")
    assert capability.native_audio is NativeAudio.OPTIONAL
    assert compile_video_request(request(native_audio=True), registry).request.native_audio is True
