from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime

import pytest

from novelvideo.generators.video import (
    SCHEMA_VERSION,
    DuplicateModelKeyError,
    FallbackPolicy,
    Lifecycle,
    ModelCapability,
    NativeAudio,
    ReferenceLimits,
    UnknownVideoModelError,
    VideoMode,
    VideoModelCatalogError,
    VideoModelRegistry,
    VideoModelUnavailableError,
)


def test_gateway_discovery_is_fail_closed_and_admits_verified_contract_models() -> None:
    from novelvideo.model_catalog import discover_known_video_models

    payload = {
        "data": [
            {"id": "jimeng-seedance-2.0-fast", "supported_endpoint_types": ["openai-video"]},
            {"id": "jimeng-seedance-2.5", "supported_endpoint_types": ["openai-video"]},
            {"id": "S-videos-f-933-fast-480-2", "supported_endpoint_types": ["openai-video"]},
            {"id": "mini-h3", "supported_endpoint_types": ["openai-video"]},
            {"id": "kling-v3-omni-v2v-create", "supported_endpoint_types": ["openai-video"]},
            {"id": "unknown-video", "supported_endpoint_types": ["openai-video"]},
            {"id": "seedance-2.5-text", "supported_endpoint_types": ["openai"]},
        ]
    }

    assert discover_known_video_models(payload) == [
        "S-videos-f-933-fast-480-2",
        "jimeng-seedance-2.0-fast",
        "jimeng-seedance-2.5",
        "kling-v3-omni-v2v-create",
        "mini-h3",
    ]
    assert discover_known_video_models({"data": [{"id": "seedance-2.5"}]}) == []
    assert discover_known_video_models({"data": [{"id": "seedance-2-5"}]}) == []
    assert discover_known_video_models({"data": "not-a-list"}) == []


def test_seedance25_contract_exposes_4_to_30_seconds_only_when_configured() -> None:
    from novelvideo.generators.video import build_newapi_video_catalog

    snapshot = build_newapi_video_catalog(models=["seedance-2.5"], duration_bounds="")
    capability = snapshot.registry.resolve("seedance-2.5")

    assert capability.duration == tuple(range(4, 31))
    assert capability.resolution == ("480p", "720p", "1080p")
    assert capability.reference_limits.reference_images == 9


def capability(model_id: str = "seedance-2") -> ModelCapability:
    return ModelCapability(
        model_id=model_id,
        provider="runninghub",
        model_vendor="bytedance",
        adapter="atlas",
        upstream_model="seedance-2.0",
        aliases=(f"{model_id}-latest",),
        modes=(VideoMode.TEXT_TO_VIDEO, VideoMode.IMAGE_TO_VIDEO),
        duration=(5, 10),
        resolution=("720p", "1080p"),
        aspect=("16:9", "9:16"),
        native_audio=NativeAudio.OPTIONAL,
        reference_limits=ReferenceLimits(input_images=1, reference_audios=1),
        return_last_frame=True,
        prompt_profile="seedance-v2",
        enabled=True,
        lifecycle=Lifecycle.ACTIVE,
        catalog_revision="2026-07-18",
        model_revision="seedance-2.0-2026-06",
        pricing_revision="2026-07-01",
        data_policy_revision="2026-07-10",
        fallback_policy=FallbackPolicy.FORBIDDEN,
        last_verified=date(2026, 7, 18),
    )


def test_capability_is_typed_immutable_and_registry_exact_resolves() -> None:
    model = capability()
    with pytest.raises(FrozenInstanceError):
        model.provider = "atlas"  # type: ignore[misc]

    registry = VideoModelRegistry([model])
    assert registry.resolve("seedance-2") is model
    assert registry.resolve("seedance-2-latest") is model
    assert registry.list_enabled() == (model,)


def test_capability_accepts_positive_decimal_aspect_ratios():
    model = replace(capability(), aspect=("2.39:1", "16:9"))
    assert model.aspect == ("2.39:1", "16:9")


def test_open_provider_tokens_accept_runninghub_and_atlas() -> None:
    runninghub = capability("runninghub-model")
    atlas = replace(
        capability("atlas-model"),
        provider="atlas",
        adapter="runninghub",
        aliases=("atlas-latest",),
    )
    registry = VideoModelRegistry([runninghub, atlas])
    assert registry.resolve("runninghub-model").provider == "runninghub"
    assert registry.resolve("atlas-model").provider == "atlas"
    with pytest.raises(ValueError, match="lowercase token"):
        replace(capability(), provider="RunningHub")


def test_native_audio_output_and_reference_audio_input_roundtrip() -> None:
    model = replace(
        capability(),
        native_audio=NativeAudio.REQUIRED,
        modes=(VideoMode.REFERENCE_TO_VIDEO,),
        reference_limits=ReferenceLimits(reference_audios=2),
    )
    restored = ModelCapability.from_dict(model.to_dict())
    assert restored.native_audio is NativeAudio.REQUIRED
    assert restored.reference_limits.reference_audios == 2


def test_registry_rejects_duplicate_alias_and_model_id_collisions() -> None:
    first = capability("first")
    duplicate_alias = replace(capability("second"), aliases=("first-latest",))
    with pytest.raises(DuplicateModelKeyError):
        VideoModelRegistry([first, duplicate_alias])

    id_collides_with_alias = replace(capability("third"), model_id="first-latest")
    with pytest.raises(DuplicateModelKeyError):
        VideoModelRegistry([first, id_collides_with_alias])


@pytest.mark.parametrize("duration", [(), (0,), (-1, 5), (10, 5), (5, 5)])
def test_capability_rejects_illegal_duration(duration: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="duration"):
        replace(capability(), duration=duration)


def test_capability_rejects_illegal_reference_limits_and_mode_mismatches() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        ReferenceLimits(reference_audios=-1)
    with pytest.raises(ValueError, match="image_to_video"):
        replace(capability(), reference_limits=ReferenceLimits())
    with pytest.raises(ValueError, match="first_last_frame"):
        replace(
            capability(),
            modes=(VideoMode.FIRST_LAST_FRAME,),
            reference_limits=ReferenceLimits(input_images=1),
        )
    with pytest.raises(ValueError, match="reference_to_video"):
        replace(
            capability(),
            modes=(VideoMode.REFERENCE_TO_VIDEO,),
            reference_limits=ReferenceLimits(),
        )


def test_disabled_deprecated_and_sunset_lifecycle() -> None:
    disabled = replace(capability("disabled"), enabled=False)
    deprecated = replace(capability("old"), lifecycle=Lifecycle.DEPRECATED)
    sunset = replace(capability("gone"), enabled=False, lifecycle=Lifecycle.SUNSET)
    registry = VideoModelRegistry([disabled, deprecated, sunset])

    assert registry.list_enabled() == (deprecated,)
    assert registry.resolve("old") is deprecated
    for key in ("disabled", "gone"):
        with pytest.raises(VideoModelUnavailableError):
            registry.resolve(key)
    with pytest.raises(ValueError, match="sunset"):
        replace(capability(), lifecycle=Lifecycle.SUNSET)


def test_manifest_v1_roundtrip_revision_fields_and_unknown_fields() -> None:
    manifest = VideoModelRegistry([capability()]).to_dict()
    assert manifest["schema_version"] == SCHEMA_VERSION
    model = manifest["models"][0]
    assert model["model_revision"] == "seedance-2.0-2026-06"
    assert model["data_policy_revision"] == "2026-07-10"
    assert VideoModelRegistry.from_dict(manifest).to_dict() == manifest

    with pytest.raises(VideoModelCatalogError, match="unknown fields"):
        VideoModelRegistry.from_dict({**manifest, "unexpected": True})
    manifest["models"][0]["unexpected"] = True
    with pytest.raises(VideoModelCatalogError, match=r"models\[0\].*unknown fields"):
        VideoModelRegistry.from_dict(manifest)


def test_manifest_rejects_missing_or_unsupported_schema_version() -> None:
    manifest = VideoModelRegistry([capability()]).to_manifest()
    without_schema = dict(manifest)
    without_schema.pop("schema_version")
    with pytest.raises(VideoModelCatalogError, match="missing fields"):
        VideoModelRegistry.from_manifest(without_schema)

    manifest["schema_version"] = "video-model-catalog.v2"
    with pytest.raises(VideoModelCatalogError, match="unsupported schema_version"):
        VideoModelRegistry.from_manifest(manifest)


def test_empty_catalog_requires_explicit_revision_and_roundtrips() -> None:
    with pytest.raises(VideoModelCatalogError, match="explicit catalog_revision"):
        VideoModelRegistry([])
    registry = VideoModelRegistry([], catalog_revision="2026-07-empty")
    assert registry.to_manifest() == {
        "schema_version": SCHEMA_VERSION,
        "catalog_revision": "2026-07-empty",
        "models": [],
    }
    assert VideoModelRegistry.from_manifest(registry.to_manifest()).to_manifest() == registry.to_manifest()


def test_datetime_is_not_accepted_as_a_date() -> None:
    with pytest.raises(ValueError, match="not datetime"):
        replace(capability(), last_verified=datetime(2026, 7, 18, 12, 0))


def test_manifest_model_error_has_index_context() -> None:
    manifest = VideoModelRegistry([capability()]).to_manifest()
    manifest["models"][0]["duration"] = [0]
    with pytest.raises(VideoModelCatalogError, match=r"models\[0\].*duration"):
        VideoModelRegistry.from_manifest(manifest)


def test_catalog_revision_must_match_every_model() -> None:
    manifest = VideoModelRegistry([capability()]).to_manifest()
    manifest["catalog_revision"] = "other"
    with pytest.raises(VideoModelCatalogError, match="do not match"):
        VideoModelRegistry.from_manifest(manifest)


@pytest.mark.parametrize("key", ["", "Seedance-2", " seedance-2", object()])
def test_resolve_rejects_invalid_keys(key: object) -> None:
    registry = VideoModelRegistry([capability()])
    with pytest.raises(UnknownVideoModelError, match="resolve key"):
        registry.resolve(key)  # type: ignore[arg-type]


def test_fallback_policy_is_declarative_and_resolve_has_no_fallback_argument() -> None:
    for policy in FallbackPolicy:
        model = replace(capability(), fallback_policy=policy)
        assert model.fallback_policy is policy

    registry = VideoModelRegistry([capability()])
    with pytest.raises(UnknownVideoModelError):
        registry.resolve("not-present")
    with pytest.raises(TypeError):
        registry.resolve("not-present", fallback_model_id="seedance-2")  # type: ignore[call-arg]
