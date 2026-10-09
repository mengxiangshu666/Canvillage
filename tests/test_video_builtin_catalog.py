import pytest

from novelvideo.generators.video import (
    NativeAudio,
    VideoMode,
    build_newapi_video_catalog,
)


def legacy_options(
    models: list[str],
    *,
    include_seedance2_variants: bool = False,
) -> dict[str, str]:
    from novelvideo.generators.video.builtin_catalog import (
        NEWAPI_DISABLED_VIDEO_MODELS,
        NEWAPI_MAINLINE_SEEDANCE2_MODELS,
        NEWAPI_VIDEO_DISPLAY_LABELS,
    )

    selected = [model for model in models if model not in NEWAPI_DISABLED_VIDEO_MODELS]
    if include_seedance2_variants:
        for model in NEWAPI_MAINLINE_SEEDANCE2_MODELS:
            if model not in selected:
                selected.append(model)
    return {
        f"newapi_{model}": NEWAPI_VIDEO_DISPLAY_LABELS.get(model, model)
        for model in selected
    }


def test_default_overlay_matches_legacy_backend_options(monkeypatch) -> None:
    from novelvideo import config
    from novelvideo.generators.video_generator import newapi_video_backend_options

    models = [
        "seedance-1.0-pro-fast",
        "seedance-1.5-pro",
        "seedance-2.0",
        "seedance-2.0-fast",
        "seedance-2.0-value",
        "seedance-2.0-fast-value",
        "happyhorse-1.0",
    ]
    monkeypatch.setattr(config, "NEWAPI_VIDEO_MODELS", models)

    snapshot = build_newapi_video_catalog()

    assert snapshot.backend_options() == legacy_options(models)
    assert newapi_video_backend_options() == legacy_options(models)
    assert [item.model_id for item in snapshot.registry.list_enabled()] == models


def test_environment_model_and_duration_audio_overlays_are_declarative() -> None:
    snapshot = build_newapi_video_catalog(
        models=["partner-video-v3"],
        audio_models=["partner-video-v3"],
        duration_bounds="partner-video-v3:5-7",
    )

    model = snapshot.registry.resolve("partner-video-v3")
    assert snapshot.backend_options() == {"newapi_partner-video-v3": "partner-video-v3"}
    assert model.duration == (5, 6, 7)
    assert model.native_audio is NativeAudio.OPTIONAL
    assert model.modes == (VideoMode.TEXT_TO_VIDEO, VideoMode.IMAGE_TO_VIDEO)
    assert snapshot.configured_duration_bounds == {"partner-video-v3": (5, 7)}


def test_minimax_hailuo_capability_does_not_claim_seedance_features() -> None:
    snapshot = build_newapi_video_catalog(
        models=["minimax-hailuo-2.3"],
        audio_models=[],
        duration_bounds="minimax-hailuo-2.3:6-10",
    )

    model = snapshot.registry.resolve("minimax-hailuo-2.3")

    assert snapshot.backend_options() == {
        "newapi_minimax-hailuo-2.3": "MiniMax Hailuo 2.3"
    }
    assert model.model_vendor == "minimax"
    assert model.modes == (VideoMode.TEXT_TO_VIDEO, VideoMode.IMAGE_TO_VIDEO)
    assert model.duration == (6, 7, 8, 9, 10)
    assert model.resolution == ("768p", "1080p")
    assert model.native_audio is NativeAudio.UNSUPPORTED
    assert model.reference_limits.reference_images == 0
    assert model.reference_limits.reference_videos == 0
    assert model.reference_limits.reference_audios == 0
    assert model.return_last_frame is False


def test_kling_omni_is_auditable_but_not_exposed() -> None:
    snapshot = build_newapi_video_catalog(
        models=["kling-3.0-omni"],
        audio_models=[],
        duration_bounds="kling-3.0-omni:5-10",
    )

    assert snapshot.backend_options() == {}
    manifest_model = snapshot.registry.to_manifest()["models"][0]
    assert manifest_model["model_id"] == "kling-3.0-omni"
    assert manifest_model["enabled"] is False
    assert manifest_model["model_vendor"] == "kling"
    assert manifest_model["modes"] == [
        "text_to_video",
        "image_to_video",
        "reference_to_video",
    ]
    assert manifest_model["duration"] == list(range(5, 11))
    assert manifest_model["resolution"] == ["720p", "1080p"]
    assert manifest_model["aspect"] == ["16:9", "9:16"]
    assert manifest_model["reference_limits"]["input_images"] == 1
    assert manifest_model["reference_limits"]["reference_images"] == 1
    assert manifest_model["reference_limits"]["reference_videos"] == 0


def test_prompt_hubs_routed_models_are_exposed_with_honest_capabilities() -> None:
    models = [
        "kling-3.0-omni",
        "sd2.0-720p-fast",
        "sd2.0-720p-4img-fast",
        "sd2.0-pro-full-9img-face",
        "sd2.0-1080p-4k-pro",
        "sora-2-flex",
        "veo-3.1-ref-flex",
        "runway-gen4.5",
    ]
    snapshot = build_newapi_video_catalog(
        models=models,
        audio_models=[],
        duration_bounds=(
            "kling-3.0-omni:5-10,sora-2-flex:4-12,"
            "veo-3.1-ref-flex:4-8,runway-gen4.5:5-10"
        ),
    )

    assert list(snapshot.backend_options()) == [
        "newapi_sd2.0-720p-fast",
        "newapi_sd2.0-720p-4img-fast",
        "newapi_s-videos-f-933-fast-480-2",
        "newapi_sd2.0-1080p-4k-pro",
        "newapi_sora-2-flex",
        "newapi_veo-3.1-ref-flex",
        "newapi_runway-gen4.5",
    ]
    sd_fast = snapshot.registry.resolve("sd2.0-720p-fast")
    sd_4img = snapshot.registry.resolve("sd2.0-720p-4img-fast")
    sd_4k = snapshot.registry.resolve("sd2.0-1080p-4k-pro")
    veo_ref = snapshot.registry.resolve("veo-3.1-ref-flex")
    runway = snapshot.registry.resolve("runway-gen4.5")

    assert sd_fast.duration == tuple(range(4, 16))
    assert sd_fast.reference_limits.reference_images == 9
    assert sd_fast.reference_limits.reference_audios == 3
    assert sd_4img.reference_limits.reference_images == 4
    assert sd_4img.reference_limits.reference_audios == 1
    assert sd_4k.resolution == ("1080p", "2160p")
    assert snapshot.registry.resolve("s-videos-f-933-fast-480-2").upstream_model == (
        "S-videos-f-933-fast-480-2"
    )
    assert veo_ref.reference_limits.reference_images == 3
    assert runway.resolution == ("720p",)
    assert runway.aspect == ("16:9", "9:16", "1:1")


def test_default_video_route_requires_the_direct_model_registry() -> None:
    from novelvideo import config

    default_models = list(config.NEWAPI_VIDEO_MODELS)
    assert default_models == []
    assert config.DEFAULT_VIDEO_MODEL == ""
    assert config.NEWAPI_VIDEO_MODEL == ""
    assert config.VIDEO_BACKEND == ""


def test_video_dispatcher_prefers_480p_seedance_variable_duration(monkeypatch) -> None:
    from novelvideo import config
    from novelvideo.generators.video_dispatcher import choose_video_backend_for_task

    monkeypatch.setattr(config, "NEWAPI_VIDEO_MODELS", ["village-canvas-video", "sd2.0-720p-fast"])

    decision = choose_video_backend_for_task(
        "newapi_sd2.0-720p-fast",
        duration=4,
        resolution="480p",
        references=[],
        has_first_frame=True,
        auto_enabled=True,
    )

    assert decision.backend == "newapi_sd2.0-720p-fast"
    assert decision.resolution == "480p"
    assert "per-second/window" in decision.reason


def test_video_dispatcher_keeps_nine_ref_sd_for_video_and_audio_refs(monkeypatch) -> None:
    from novelvideo import config
    from novelvideo.generators.video_dispatcher import choose_video_backend_for_task

    monkeypatch.setattr(config, "NEWAPI_VIDEO_MODELS", ["village-canvas-video", "sd2.0-720p-fast"])

    references = [
        *({"type": "image", "path": f"image-{index}.png"} for index in range(6)),
        *({"type": "video", "path": f"video-{index}.mp4"} for index in range(3)),
        *({"type": "audio", "path": f"audio-{index}.mp3"} for index in range(3)),
    ]
    decision = choose_video_backend_for_task(
        "newapi_kling-3.0-omni",
        duration=5,
        resolution="480p",
        references=references,
        has_first_frame=True,
        auto_enabled=True,
    )

    assert decision.backend == "newapi_sd2.0-720p-fast"
    assert "9img-ref" in decision.reason
    assert "video/audio-ref" in decision.reason


def test_disabled_model_remains_auditable_but_is_not_exposed() -> None:
    snapshot = build_newapi_video_catalog(
        models=["seedance-2.0-fast", "grok-video-channel"],
        audio_models=[],
        duration_bounds="",
    )

    assert snapshot.backend_options() == {"newapi_seedance-2.0-fast": "Seedance2.0 Fast"}
    manifest_models = snapshot.registry.to_manifest()["models"]
    grok = next(item for item in manifest_models if item["model_id"] == "grok-video-channel")
    assert grok["enabled"] is False


def test_seedance2_variant_inclusion_matches_legacy_order_and_deduplicates() -> None:
    models = ["custom-model", "seedance-2.0-fast", "custom-model"]
    snapshot = build_newapi_video_catalog(
        models=models,
        audio_models=[],
        duration_bounds="",
        include_seedance2_variants=True,
    )

    assert snapshot.backend_options() == legacy_options(
        models,
        include_seedance2_variants=True,
    )


def test_happyhorse_and_grok_builtin_capabilities_preserve_legacy_limits() -> None:
    snapshot = build_newapi_video_catalog(
        models=["happyhorse-1.0", "grok-video-channel"],
        audio_models=[],
        duration_bounds="",
    )
    happyhorse = snapshot.registry.resolve("happyhorse-1.0")
    grok = next(
        item
        for item in snapshot.registry.to_manifest()["models"]
        if item["model_id"] == "grok-video-channel"
    )

    assert happyhorse.resolution == ("720p", "1080p")
    assert happyhorse.aspect == ("16:9", "9:16", "1:1", "4:3", "3:4")
    assert happyhorse.reference_limits.reference_images == 9
    assert happyhorse.reference_limits.reference_videos == 1
    assert grok["resolution"] == ["720p", "480p"]
    assert grok["reference_limits"]["reference_images"] == 7


def test_seedance_resolutions_and_happyhorse_generator_default_match_production() -> None:
    snapshot = build_newapi_video_catalog(
        models=[
            "seedance-1.5-pro",
            "seedance-2.0-fast",
            "seedance-2.0",
            "seedance-2.0-value",
            "seedance-2.0-fast-value",
            "happyhorse-1.0",
        ],
        audio_models=[],
        duration_bounds="",
    )

    assert snapshot.registry.resolve("seedance-1.5-pro").resolution == (
        "480p",
        "720p",
        "1080p",
    )
    assert snapshot.registry.resolve("seedance-2.0-fast").resolution == ("480p", "720p")
    assert snapshot.registry.resolve("seedance-2.0").resolution == (
        "480p",
        "720p",
        "1080p",
    )
    assert snapshot.registry.resolve("seedance-2.0-value").resolution == ("720p", "1080p")
    assert snapshot.registry.resolve("seedance-2.0-fast-value").resolution == (
        "720p",
        "1080p",
    )
    assert snapshot.registry.resolve("happyhorse-1.0").duration == tuple(range(1, 16))


@pytest.mark.asyncio
async def test_personal_canary_status_exposes_only_configured_catalog(
    monkeypatch,
    tmp_path,
) -> None:
    from novelvideo import config
    from novelvideo.api.routes import generation
    from novelvideo.model_gateway_settings import save_direct_video_models

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    saved = save_direct_video_models([
        {
            "label": "Direct Wokey Fast",
            "modelId": "jimeng-seedance-2.0-fast",
            "baseUrl": "https://direct.invalid/v1",
            "apiKey": "test-key",
            "enabled": True,
        }
    ])

    result = await generation.get_mod_video_kernel_status()

    assert result["ok"] is True
    data = result["data"]
    assert data["edition"] == "personal-canary"
    assert data["compiler_revision"] == "video-prompt-compiler.v1"
    assert data["production_dispatch"] == "direct-only"
    assert data["compiler_attached_to_paid_submission"] is False
    assert [model["model_id"] for model in data["models"]] == [f"direct_{saved[0]['id']}"]


def test_api_backend_projection_reads_direct_registry_without_changing_response_shape(monkeypatch, tmp_path) -> None:
    from novelvideo import config
    from novelvideo.api.routes import generation
    from novelvideo.model_gateway_settings import save_direct_video_models

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    saved = save_direct_video_models([
        {
            "label": "Direct Wokey Fast",
            "modelId": "jimeng-seedance-2.0-fast",
            "baseUrl": "https://direct.invalid/v1",
            "apiKey": "test-key",
            "enabled": True,
        }
    ])

    options = {item.value: item.model_dump() for item in generation._api_video_backend_options()}
    fast = options[f"direct_{saved[0]['id']}"]

    assert fast["label"] == "Direct Wokey Fast"
    assert fast["min_duration"] == 4
    assert fast["max_duration"] == 15
    assert fast["resolution_options"] == ["720p"]
    assert fast["supported_modes"] == ["first_frame", "first_last_frame", "multimodal_reference"]
    assert fast["reference_image_max"] == 9
    assert fast["reference_video_max"] == 3
    assert fast["reference_audio_max"] == 3


def test_api_backend_projection_is_empty_without_a_direct_model(monkeypatch, tmp_path) -> None:
    from novelvideo import config
    from novelvideo.api.routes import generation

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))

    options = generation._api_video_backend_options()

    assert options == []


def test_video_request_defaults_follow_runtime_backend(monkeypatch) -> None:
    from novelvideo import config
    from novelvideo.api.schemas import SingleVideoRequest, VideoGenerateRequest

    monkeypatch.setattr(config, "VIDEO_BACKEND", "newapi_minimax-hailuo-2.3")

    assert VideoGenerateRequest().video_backend == "newapi_minimax-hailuo-2.3"
    assert SingleVideoRequest().video_backend == "newapi_minimax-hailuo-2.3"
