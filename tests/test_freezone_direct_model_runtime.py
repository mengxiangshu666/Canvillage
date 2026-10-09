from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.task_backend.runners import freezone as freezone_runner


def test_direct_runner_preserves_explicit_aspect_ratio_without_rewriting() -> None:
    assert freezone_runner._resolve_image_aspect_ratio_for_payload(
        {"provider": "direct", "model": "direct/image", "aspect_ratio": "7:5"}
    ) == "7:5"


def test_direct_runner_uses_declared_model_ratio_when_request_omits_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = SimpleNamespace(
        default_aspect_ratio="2:3",
        aspect_ratio_options=("2:3", "1:1"),
        default_resolution="3K",
        resolution_options=("3K",),
    )
    monkeypatch.setattr(
        "novelvideo.generators.direct_image_models.resolve_direct_image_model",
        lambda _model: SimpleNamespace(profile=profile),
    )

    assert freezone_runner._resolve_image_aspect_ratio_for_payload(
        {"provider": "direct", "model": "direct/image"}
    ) == "2:3"
    assert freezone_runner._resolve_image_size_for_payload(
        {"provider": "direct", "model": "direct/image"}
    ) == "3K"


def test_direct_runner_keeps_empty_declared_contract_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = SimpleNamespace(
        default_aspect_ratio="",
        aspect_ratio_options=(),
        default_resolution="",
        resolution_options=(),
    )
    monkeypatch.setattr(
        "novelvideo.generators.direct_image_models.resolve_direct_image_model",
        lambda _model: SimpleNamespace(profile=profile),
    )

    payload = {"provider": "direct", "model": "direct/image"}
    assert freezone_runner._resolve_image_aspect_ratio_for_payload(payload) == ""
    assert freezone_runner._resolve_image_size_for_payload(payload) == ""


def test_legacy_runner_defaults_remain_for_non_direct_payloads() -> None:
    payload = {"provider": "newapi", "model": "legacy/image"}
    assert freezone_runner._resolve_image_aspect_ratio_for_payload(payload) == "1:1"
    assert freezone_runner._resolve_image_size_for_payload(payload) == "2K"


def test_direct_image_capability_contract_feeds_generic_catalog() -> None:
    from novelvideo.generators.direct_image_capabilities import (
        direct_image_capability_summary,
    )
    from novelvideo.generators.direct_models import DirectModel, direct_model_option

    summary = direct_image_capability_summary("gpt-image-2")
    assert summary["supportedModes"] == ["textToImage", "imageToImage"]

    option = direct_model_option(
        DirectModel(
            kind="image",
            registry_id="image-primary",
            label="生图模型",
            upstream_model="gpt-image-2",
            base_url="https://example.test/v1",
            api_key="test-key",
            enabled=True,
            is_default=True,
        )
    )

    assert option["id"] == "direct/image-primary"
    assert option["supportedModes"] == ["textToImage", "imageToImage"]
    assert option["aspectRatioOptions"] == [
        "1:1",
        "16:9",
        "9:16",
        "4:3",
        "3:4",
        "3:2",
        "2:3",
        "4:5",
        "5:4",
        "21:9",
    ]
    assert option["resolutionOptions"] == ["1K", "2K", "3K", "4K"]
    assert option["qualityOptions"] == ["low", "medium", "high", "auto"]
    assert option["supportsCustomAspectRatio"] is True
    assert option["supportsCustomResolution"] is True
    assert option["useCase"] == "已识别：文生图、图生图"
    assert option["parameterDefaults"] == {
        "resolution": "2K",
        "aspectRatio": "1:1",
        "strategy": "balanced",
    }


def test_upstream_image_metadata_extends_unknown_model_without_local_clipping() -> None:
    from novelvideo.generators.direct_image_capabilities import (
        direct_image_capability_summary,
    )

    summary = direct_image_capability_summary(
        "vendor-image-vNext",
        metadata={
            "aspect_ratio_options": ["2.39:1", "1:1"],
            "supported_sizes": ["2048x1376", "8K", "garbage"],
            "quality_options": ["high", "auto", "lossless"],
            "supports_any_size": True,
            "supports_arbitrary_aspect_ratio": True,
        },
    )

    assert summary["capabilitySource"] == "upstream"
    assert summary["aspectRatioOptions"] == ["2.39:1", "1:1"]
    assert summary["resolutionOptions"] == ["2048x1376", "8K"]
    assert summary["qualityOptions"] == ["high", "auto"]
    assert summary["supportsCustomAspectRatio"] is True
    assert summary["supportsCustomResolution"] is True


def test_upstream_image_metadata_accepts_enum_maps_and_string_booleans() -> None:
    from novelvideo.generators.direct_image_capabilities import (
        direct_image_capability_summary,
    )

    summary = direct_image_capability_summary(
        "vendor-image-vNext",
        metadata={
            "supported_sizes": {"1K": True, "8K": True, "16K": False},
            "supports_any_size": "true",
        },
    )

    assert summary["resolutionOptions"] == ["1K", "8K"]
    assert summary["supportsCustomResolution"] is True


def test_image_capability_normalizes_dimension_separator_and_respects_explicit_modes() -> None:
    from novelvideo.generators.direct_image_capabilities import (
        direct_image_capability_summary,
    )

    summary = direct_image_capability_summary(
        "vendor-image-edit-only",
        metadata={
            "supportedModes": ["image-to-image"],
            "supportedSizes": ["2048×1376"],
        },
    )

    assert summary["supportedModes"] == ["imageToImage"]
    assert summary["useCase"] == "已识别：图生图"
    assert summary["resolutionOptions"] == ["2048×1376"]


def test_explicit_empty_image_capability_alias_wins_over_later_alias() -> None:
    from novelvideo.generators.direct_image_capabilities import (
        direct_image_capability_summary,
    )

    summary = direct_image_capability_summary(
        "vendor-image-vNext",
        metadata={
            "resolutionOptions": [],
            "supportedSizes": ["8K"],
            "aspectRatioOptions": [],
            "supportedAspectRatios": ["16:9"],
        },
    )

    assert summary["resolutionOptions"] == []
    assert summary["aspectRatioOptions"] == []
    assert summary["supportsCustomResolution"] is False
    assert summary["supportsCustomAspectRatio"] is False


def test_upstream_image_catalog_field_names_are_preserved_without_profile_clipping() -> None:
    from novelvideo.generators.direct_image_capabilities import (
        direct_image_capability_summary,
    )

    summary = direct_image_capability_summary(
        "gpt-image2-4K-Native",
        metadata={
            "supported_image_sizes": ["4K"],
            "supported_aspect_ratios": ["1:1", "16:9", "21:9"],
            "supported_quality_values": ["auto", "high"],
            "supports_image_generation": True,
            "supports_image_edit": True,
        },
    )

    assert summary["resolutionOptions"] == ["4K"]
    assert summary["aspectRatioOptions"] == ["1:1", "16:9", "21:9"]
    assert summary["qualityOptions"] == ["auto", "high"]
    assert summary["supportedModes"] == ["textToImage", "imageToImage"]
    assert summary["supportsCustomResolution"] is False
    assert summary["supportsCustomAspectRatio"] is False


def test_explicit_image_tier_tags_do_not_restore_unadvertised_presets() -> None:
    from novelvideo.generators.direct_image_capabilities import (
        direct_image_capability_summary,
    )

    summary = direct_image_capability_summary(
        "image2-A",
        metadata={
            "tags": ["image", "image2", "1k", "2k", "4k", "quality"],
        },
    )

    assert summary["resolutionOptions"] == ["1K", "2K", "4K"]
    assert summary["supportsCustomResolution"] is False
    assert summary["capabilitySource"] == "upstream"


def test_image_model_option_reads_cached_upstream_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    from novelvideo.generators import direct_image_models

    monkeypatch.setattr(
        "novelvideo.generators.direct_model_capability_cache.get_cached_direct_model_capability",
        lambda **_kwargs: {
            "modelMetadata": {
                "supported_sizes": ["2048x1376"],
                "supports_any_size": True,
            }
        },
    )
    model = direct_image_models.DirectImageModel(
        registry_id="image-metadata",
        label="上游图片模型",
        upstream_model="vendor-image-vNext",
        base_url="https://image.example/v1",
        api_key="test-key",
        protocol="openai-images",
        enabled=True,
        is_default=True,
    )

    option = direct_image_models.direct_image_model_option(model)

    assert option["resolutionOptions"] == ["2048x1376"]
    assert option["supportsCustomResolution"] is True
    assert option["capabilitySource"] == "upstream"


def test_resolution_only_upstream_metadata_does_not_claim_profile_ratios_are_upstream() -> None:
    from novelvideo.generators.direct_image_capabilities import (
        direct_image_capability_summary,
    )

    summary = direct_image_capability_summary(
        "gpt-image-1k-th",
        metadata={
            "capabilities": {
                "image_generation": True,
                "image_edit": True,
            },
            "resolution_mode": "1K",
            "modalities": ["image"],
        },
    )

    assert summary["aspectRatioOptions"] == [
        "1:1",
        "1:4",
        "1:8",
        "2:3",
        "3:2",
        "3:4",
        "4:1",
        "4:3",
        "4:5",
        "5:4",
        "8:1",
        "9:16",
        "16:9",
        "21:9",
    ]
    assert summary["resolutionOptions"] == ["1K"]
    assert summary["capabilitySource"] == "upstream"
    assert summary["aspectRatioSource"] == "profile"


def test_shared_direct_model_option_keeps_cached_image_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    from novelvideo.generators import direct_models
    from novelvideo.generators.direct_models import DirectModel

    monkeypatch.setattr(
        "novelvideo.generators.direct_model_capability_cache.get_cached_direct_model_capability",
        lambda **_kwargs: {
            "modelMetadata": {
                "supported_modes": ["imageToImage"],
                "supported_sizes": ["2048x1376"],
                "supports_any_size": True,
            }
        },
    )
    model = DirectModel(
        kind="image",
        registry_id="image-shared",
        label="共享图片模型",
        upstream_model="vendor-image-vNext",
        base_url="https://image.example/v1",
        api_key="test-key",
        protocol="openai-images",
        enabled=True,
        is_default=True,
    )

    option = direct_models.direct_model_option(model)

    assert option["supportedModes"] == ["imageToImage"]
    assert option["resolutionOptions"] == ["2048x1376"]
    assert option["supportsCustomResolution"] is True


def test_direct_model_capability_contract_covers_all_canvas_families() -> None:
    from novelvideo.generators.direct_model_capabilities import (
        direct_model_capability_summary,
    )
    from novelvideo.generators.direct_models import DirectModel, direct_model_option

    cases = {
        "agent": "canvas-agent-pro",
        "text": "deepseek-chat",
        "vision": "gpt-vision-pro",
        "image": "gpt-image-2",
        "embedding": "text-embedding-3-large",
        "audio": "tts-1-hd",
    }

    for kind, upstream_model in cases.items():
        summary = direct_model_capability_summary(kind, upstream_model)
        assert summary["protocol"].startswith("openai-")
        assert summary["supportedModes"]
        assert summary["supported_modes"] == summary["supportedModes"]
        assert summary["useCase"].startswith("已识别：")
        assert summary["use_case"] == summary["useCase"]
        assert summary["parameterDefaults"]
        assert summary["parameter_defaults"] == summary["parameterDefaults"]
        assert summary["capabilityRevision"] == "direct-model-contract.v2"
        assert summary["modelKey"] == upstream_model
        assert summary["modality"] == kind
        assert summary["modeType"] == summary["supportedModes"]
        assert summary["defaults"] == summary["parameterDefaults"]
    assert summary["runtimeProbe"]["credentialFree"] is True

    embedding_summary = direct_model_capability_summary("embedding", "text-embedding-3-large")
    assert embedding_summary["parameterDefaults"]["dimensions"] == 3072

    text_option = direct_model_option(
        DirectModel(
            kind="text",
            registry_id="text-primary",
            label="文字模型",
            upstream_model="deepseek-chat",
            base_url="https://example.test/v1",
            api_key="test-key",
            enabled=True,
            is_default=True,
        )
    )
    assert text_option["supportedModes"] == ["chat", "structured_output", "prompt_optimization"]
    assert text_option["useCase"] == "已识别：提示词、脚本、文本推理"
    assert text_option["modelKey"] == "direct/text-primary"
    assert text_option["inputSlots"] == ["text"]


def test_non_image_runtime_metadata_overrides_explicit_shared_contract_fields() -> None:
    from novelvideo.generators.direct_model_capabilities import (
        direct_model_capability_summary,
    )

    text_summary = direct_model_capability_summary(
        "text",
        "vendor-text-vNext",
        metadata={
            "supported_modes": ["chat", "structured_output"],
            "parameter_defaults": {"temperature": 0.2},
            "input_slots": ["text", "canvas_context"],
        },
    )
    assert text_summary["supportedModes"] == ["chat", "structured_output"]
    assert text_summary["parameterDefaults"]["temperature"] == 0.2
    assert text_summary["inputSlots"] == ["text", "canvas_context"]

    embedding_summary = direct_model_capability_summary(
        "embedding",
        "vendor-embedding-vNext",
        metadata={"dimensions": 4096},
    )
    assert embedding_summary["parameterDefaults"]["dimensions"] == 4096


def test_aiwble_openai_probe_uses_v1_bearer_endpoint() -> None:
    from novelvideo.generators.direct_model_probe import _probe_request

    endpoint, headers = _probe_request(
        "https://aiwble.com",
        "test-key",
        "openai-compatible",
    )

    assert endpoint == "https://aiwble.com/v1/models"
    assert headers == {
        "Authorization": "Bearer test-key",
        "Accept": "application/json",
    }


def test_text_agent_cache_key_changes_after_direct_model_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.freezone import text_node
    from novelvideo.generators.direct_models import DirectModel

    current = DirectModel(
        kind="text",
        registry_id="text-primary",
        label="文字模型",
        upstream_model="vendor-text-v1",
        base_url="https://one.example/v1",
        api_key="first-key",
        enabled=True,
        is_default=True,
    )
    monkeypatch.setattr(
        "novelvideo.generators.direct_models.resolve_direct_model",
        lambda _kind, _model=None: current,
    )

    first = text_node._text_model_cache_key(
        kind="text",
        model_ref="direct/text-primary",
        default_model="unused",
    )
    current = DirectModel(
        kind="text",
        registry_id="text-primary",
        label="文字模型",
        upstream_model="vendor-text-v2",
        base_url="https://two.example/v1",
        api_key="second-key",
        enabled=True,
        is_default=True,
    )
    second = text_node._text_model_cache_key(
        kind="text",
        model_ref="direct/text-primary",
        default_model="unused",
    )

    assert first.startswith("direct/text-primary@")
    assert second.startswith("direct/text-primary@")
    assert first != second


@pytest.mark.asyncio
async def test_background_audio_runner_preserves_selected_direct_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The durable task path must not discard the picker model after enqueue."""

    calls: dict[str, object] = {}
    project_dir = tmp_path / "project"
    output_path = project_dir / "freezone" / "_outputs" / "audio_speech" / "job-1.mp3"

    class FakeStore:
        async def close(self) -> None:
            return None

    async def fake_store(_ctx):
        return FakeStore()

    async def fake_generate(**kwargs):
        calls.update(kwargs)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"audio")
        return SimpleNamespace(
            audio_path=output_path,
            duration_ms=123,
            mime_type="audio/mpeg",
            model="vendor-tts",
            voice_source="project_narrator",
            voice_sha256="sha",
        )

    monkeypatch.setattr(freezone_runner, "_update", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "novelvideo.services.project_resources.make_sqlite_store_for_context", fake_store
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_static_url_for_context",
        lambda *_args, **_kwargs: "/static/test/audio.mp3",
    )
    monkeypatch.setattr(
        "novelvideo.freezone.audio_node.generate_freezone_audio_speech",
        fake_generate,
    )

    result = await freezone_runner._run_freezone_audio_speech_async(
        {
            "payload": {
                "job_id": "job-1",
                "project_dir": str(project_dir),
                "text": "旁白",
                "model": "direct/audio-primary",
            }
        },
        SimpleNamespace(
            output_dir=project_dir,
            owner_username="owner",
            requester_username="requester",
            project_name="project",
            project_id="project-id",
        ),
    )

    assert calls["model"] == "direct/audio-primary"
    assert result["model"] == "vendor-tts"
