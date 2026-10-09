from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo import config
from novelvideo.api.routes import model_gateway
from novelvideo.freezone import video_node
from novelvideo.generators.video.direct_models import (
    DirectVideoModel,
    direct_video_model_option,
    list_direct_video_models,
    probe_direct_video_model,
    resolve_direct_video_model,
)
from novelvideo.generators.video.capabilities import ModelCapability, VideoMode
from novelvideo.generators.video.direct_video_capability_cache import (
    get_cached_capability,
    get_cached_capability_for_model,
    record_capability,
    record_runtime_resolution_rejection,
)
from novelvideo.generators.video import direct_video_profiles
from novelvideo.generators.video.direct_video_probe import (
    discover_direct_video_models,
    infer_video_capability,
)
from novelvideo.generators.video_generator import (
    NewApiVideoGenerator,
    VideoGenResult,
    VideoGenStatus,
    create_video_generator,
)
from novelvideo.model_gateway_settings import (
    build_direct_video_models_status,
    get_direct_video_models,
    save_direct_video_models,
)


def _save_one(
    monkeypatch,
    tmp_path,
    *,
    model_id: str = "seedance-direct-fast",
    protocol: str | None = None,
) -> str:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    item = {
        "label": "本地极速视频",
        "modelId": model_id,
        "baseUrl": "http://127.0.0.1:9800/v1",
        "apiKey": "direct-test-key",
        "protocol": "openai-video",
        "enabled": True,
    }
    if protocol is not None:
        item["protocol"] = protocol
    saved = save_direct_video_models([item])
    return saved[0]["id"]


def test_direct_minimax_h3_uses_declared_multi_reference_contract(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="minimax-h3")
    backend = f"direct_{registry_id}"

    contract = video_node.freezone_video_model_contract(backend)

    assert "allReference" in contract["supportedModes"]
    assert video_node.is_freezone_multi_reference_backend(backend) is True


def test_direct_all_reference_demotes_frames_before_h3_submission() -> None:
    first, last, references = NewApiVideoGenerator._filter_explicit_media_inputs(
        mode="allReference",
        image_path="first.png",
        last_frame_path="last.png",
        references=[
            {"type": "image", "path": "first.png", "role": "角色参考"},
            {"type": "audio", "path": "voice.mp3", "role": "音色参考"},
        ],
    )

    assert first == ""
    assert last == ""
    assert [item["path"] if isinstance(item, dict) else item for item in references] == [
        "first.png",
        "last.png",
        "voice.mp3",
    ]


def test_direct_video_models_preserve_secret_and_mask_status(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path)

    saved = save_direct_video_models(
        [
            {
                "id": registry_id,
                "label": "本地高质量视频",
                "modelId": "seedance-direct-fast",
                "baseUrl": "http://127.0.0.1:9800/v1",
                "apiKey": "",
                "enabled": True,
            }
        ]
    )

    assert saved[0]["apiKey"] == "direct-test-key"
    status = build_direct_video_models_status()
    assert len(status) == 1
    assert {
        key: status[0][key]
        for key in (
            "id",
            "label",
            "modelId",
            "baseUrl",
            "enabled",
            "configured",
            "apiKeyPreview",
            "protocol",
        )
    } == {
        "id": registry_id,
        "label": "本地高质量视频",
        "modelId": "seedance-direct-fast",
        "baseUrl": "http://127.0.0.1:9800/v1",
        "enabled": True,
        "configured": True,
        "apiKeyPreview": "dire...-key",
        "protocol": "openai-video",
    }
    assert status[0]["supportedModes"] == [
        "textToVideo",
        "imageToVideo",
        "firstLastFrame",
        "allReference",
        "videoEdit",
    ]
    assert status[0]["useCase"] == "已识别：文生、图生与多参考视频"
    assert status[0]["parameterDefaults"] == {
        "resolution": "720p",
        "aspectRatio": "16:9",
        "durationSeconds": 5,
        "generateAudio": False,
        "strategy": "balanced",
    }


def test_explicit_empty_video_capabilities_do_not_restore_profile_presets(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="vendor-video-vNext")
    record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="openai-video",
        upstream_model="vendor-video-vNext",
        capability={
            "verificationStatus": "metadata",
            "modelFound": True,
            "discoveredModelCount": 1,
            "declaredCapabilities": ["modes", "resolutionOptions"],
            "modes": [],
            "resolutionOptions": [],
        },
    )

    model = resolve_direct_video_model(f"direct_{registry_id}")
    assert model is not None
    option = direct_video_model_option(model)

    assert option["supportedModes"] == []
    assert option["referenceLimits"] == {}
    assert option["resolutionOptions"] == []
    assert option["enabled"] is False
    assert option["disabled"] is True

    generator = NewApiVideoGenerator(**model.generator_options({}))
    assert generator.resolution_parameter_enabled is False
    payload = generator._apply_capability_contract(
        {
            "model": "vendor-video-vNext",
            "prompt": "fixture",
            "seconds": 5,
            "aspect_ratio": "16:9",
            "resolution": "720p",
        }
    )
    assert "resolution" not in payload
    assert payload["aspect_ratio"] == "16:9"


def test_direct_video_option_preserves_discrete_runtime_contract_fields(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="vendor-video-discrete")
    record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="openai-video",
        upstream_model="vendor-video-discrete",
        capability={
            "verificationStatus": "metadata",
            "modelFound": True,
            "discoveredModelCount": 1,
            "declaredCapabilities": [
                "durationOptions",
                "fpsOptions",
                "inputSlots",
                "returnLastFrame",
                "supportsCustomDuration",
            ],
            "durationOptions": [5, 10, 30],
            "fpsOptions": [24, 30],
            "inputSlots": ["image", "video"],
            "returnLastFrame": False,
            "supportsCustomDuration": False,
        },
    )

    model = resolve_direct_video_model(f"direct_{registry_id}")
    assert model is not None
    option = direct_video_model_option(model)

    assert option["durationOptions"] == [5, 10, 30]
    assert option["fpsOptions"] == [24, 30]
    assert option["inputSlots"] == ["image", "video"]
    assert option["returnLastFrame"] is False
    assert option["supportsCustomDuration"] is False
    assert option["durationParameterEnabled"] is True

    generator = NewApiVideoGenerator(**model.generator_options({}))
    payload = generator._apply_capability_contract(
        {
            "model": "vendor-video-discrete",
            "prompt": "fixture",
            "duration_seconds": 12,
        }
    )
    assert payload["duration_seconds"] == 12


def test_explicit_empty_duration_contract_disables_duration_transport(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="vendor-video-fixed")
    record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="openai-video",
        upstream_model="vendor-video-fixed",
        capability={
            "verificationStatus": "metadata",
            "modelFound": True,
            "discoveredModelCount": 1,
            "declaredCapabilities": ["durationOptions"],
            "durationOptions": [],
        },
    )

    model = resolve_direct_video_model(f"direct_{registry_id}")
    assert model is not None
    option = direct_video_model_option(model)
    assert option["durationOptions"] == []
    assert option["durationParameterEnabled"] is False

    generator = NewApiVideoGenerator(**model.generator_options({}))
    payload = generator._apply_capability_contract(
        {
            "model": "vendor-video-fixed",
            "prompt": "fixture",
            "duration_seconds": 12,
        }
    )
    assert "duration_seconds" not in payload


def test_explicit_empty_aspect_capability_is_preserved_through_transport(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="vendor-video-aspectless")
    base_url = "http://127.0.0.1:9800/v1"
    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="vendor-video-aspectless",
        capability={
            "verificationStatus": "metadata",
            "modelFound": True,
            "discoveredModelCount": 1,
            "declaredCapabilities": ["modes", "resolutionOptions", "aspectRatios"],
            "modes": ["textToVideo"],
            "resolutionOptions": ["720p"],
            "aspectRatios": [],
        },
    )

    model = resolve_direct_video_model(f"direct_{registry_id}")
    assert model is not None
    # The family profile remains a complete executable contract; the explicit
    # upstream empty enum is carried as a parameter gate instead of violating
    # ModelCapability's non-empty invariants.
    assert model.profile.aspect
    option = direct_video_model_option(model)
    assert option["aspectRatioOptions"] == []
    assert option["parameterDefaults"]["aspectRatio"] == ""

    generator = NewApiVideoGenerator(**model.generator_options({}))
    assert generator.aspect_ratio_parameter_enabled is False
    payload = generator._apply_capability_contract(
        {
            "model": "vendor-video-aspectless",
            "prompt": "fixture",
            "seconds": 5,
            "aspect_ratio": "16:9",
            "resolution": "720p",
        }
    )
    assert "aspect_ratio" not in payload
    assert payload["resolution"] == "720p"


def test_disabled_video_model_cannot_remain_the_runtime_default(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    saved = save_direct_video_models(
        [
            {
                "label": "已停用旧模型",
                "modelId": "old-video",
                "baseUrl": "https://old.example/v1",
                "apiKey": "old-key",
                "enabled": False,
                "isDefault": True,
            },
            {
                "label": "当前启用模型",
                "modelId": "active-video",
                "baseUrl": "https://active.example/v1",
                "apiKey": "active-key",
                "enabled": True,
                "isDefault": False,
            },
        ]
    )

    assert saved[0]["isDefault"] is False
    assert saved[1]["isDefault"] is True
    assert resolve_direct_video_model("direct_default").upstream_model == "active-video"


def test_direct_video_model_blank_key_is_rejected_after_endpoint_change(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="apiKey is required"):
        save_direct_video_models(
            [
                {
                    "id": registry_id,
                    "label": "新站点视频模型",
                    "modelId": "seedance-direct-fast",
                    "baseUrl": "https://second.example/v1",
                    "apiKey": "",
                    "enabled": True,
                }
            ]
        )

    assert get_direct_video_models()[0]["apiKey"] == "direct-test-key"


def test_direct_video_generator_uses_only_saved_endpoint(monkeypatch, tmp_path) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="custom-video-v1")

    generator = create_video_generator(f"direct_{registry_id}")

    assert isinstance(generator, NewApiVideoGenerator)
    assert generator.model == "custom-video-v1"
    assert generator.upstream_model == "custom-video-v1"
    assert generator.base_url == "http://127.0.0.1:9800/v1"
    assert generator.create_path == "/videos"
    assert generator.allow_result_gateway_fallback is False
    assert generator.gateway_candidates == [
        {
            "name": "override",
            "source": "override",
            "mode": generator.gateway_candidates[0]["mode"],
            "api_key": "direct-test-key",
            "base_url": "http://127.0.0.1:9800/v1",
        }
    ]
    assert generator._result_gateway_candidates() == [
        {
            "name": "override",
            "api_key": "direct-test-key",
            "base_url": "http://127.0.0.1:9800/v1",
        }
    ]


def test_direct_default_resolves_the_first_enabled_model(monkeypatch, tmp_path) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="jimeng-seedance-2.0-fast")

    resolved = resolve_direct_video_model("direct_default")

    assert resolved is not None
    assert resolved.registry_id == registry_id
    assert resolved.upstream_model == "jimeng-seedance-2.0-fast"


def test_freezone_migrates_a_saved_newapi_model_to_the_matching_direct_model(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="jimeng-seedance-2.0-fast")

    assert (
        video_node.resolve_freezone_video_backend("newapi_jimeng-seedance-2.0-fast")
        == f"direct_{registry_id}"
    )


@pytest.mark.parametrize(
    ("model_id", "requested_resolution", "expected_resolution", "expected_audio"),
    [
        ("seedance-direct-fast", "480P", "480p", True),
        ("seedance-direct-fast", "2k", "720p", True),
        ("mini-h3", "720p", "2k", False),
    ],
)
def test_direct_video_generator_solves_parameters_without_constructor_collisions(
    monkeypatch,
    tmp_path,
    model_id: str,
    requested_resolution: str,
    expected_resolution: str,
    expected_audio: bool,
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id=model_id)

    generator = create_video_generator(
        f"direct_{registry_id}",
        resolution=requested_resolution,
        generate_audio=True,
        # Registry-owned options must be absorbed rather than becoming a
        # second constructor value beside the secure saved configuration.
        endpoint="https://stale.example/v1",
        model="stale-model",
        create_path="/stale/videos",
    )

    assert isinstance(generator, NewApiVideoGenerator)
    assert generator.model == model_id
    assert generator.base_url == "http://127.0.0.1:9800/v1"
    assert generator.create_path == "/videos"
    assert generator.resolution == expected_resolution
    assert generator.generate_audio is expected_audio


def test_direct_video_generator_enforces_profile_duration_and_aspect_contract(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="kling-v3-omni-v2v-create")

    generator = create_video_generator(f"direct_{registry_id}")

    assert generator._duration_bounds() == (3, 15)
    assert generator._resolve_allowed_aspect_ratio("1:1") == "16:9"


def test_direct_kling_video_edit_can_start_from_source_video_only(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="kling-v3-omni-v2v-create")
    direct_model = next(
        item for item in list_direct_video_models() if item.registry_id == registry_id
    )

    assert direct_model.capability.modes == (VideoMode.REFERENCE_TO_VIDEO,)
    assert direct_model.capability.reference_limits.reference_videos == 1


@pytest.mark.asyncio
async def test_freezone_direct_kling_accepts_source_video_without_first_image(
    monkeypatch, tmp_path
) -> None:
    from novelvideo.freezone.jobs import run_freezone_video_gen
    from novelvideo.generators import video_generator

    registry_id = _save_one(monkeypatch, tmp_path, model_id="kling-v3-omni-v2v-create")
    captured: dict[str, object] = {}

    class FakeVideoGenerator:
        async def generate(self, **kwargs):
            captured.update(kwargs)
            output_path = Path(str(kwargs["output_path"]))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake mp4")
            return VideoGenResult(
                status=VideoGenStatus.DONE, video_path=str(output_path)
            )

    monkeypatch.setattr(
        video_generator,
        "create_video_generator",
        lambda **_kwargs: FakeVideoGenerator(),
    )
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"video fixture")

    output = await run_freezone_video_gen(
        project_dir=tmp_path,
        job_id="direct-kling-v2v",
        prompt="重绘源视频中的镜头运动",
        reference_items=[
            {"type": "video", "path": str(source_video), "role": "源视频"}
        ],
        backend=f"direct_{registry_id}",
    )

    assert output.exists()
    assert captured["image_path"] is None
    assert len(captured["references"]) == 1
    assert captured["references"][0].type == "video"


@pytest.mark.asyncio
async def test_freezone_direct_text_to_video_accepts_no_first_image(
    monkeypatch, tmp_path
) -> None:
    from novelvideo.freezone.jobs import run_freezone_video_gen
    from novelvideo.generators import video_generator

    registry_id = _save_one(monkeypatch, tmp_path, model_id="mini-h3")

    class FakeVideoGenerator:
        async def generate(self, **kwargs):
            output_path = Path(str(kwargs["output_path"]))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake mp4")
            return VideoGenResult(
                status=VideoGenStatus.DONE, video_path=str(output_path)
            )

    monkeypatch.setattr(
        video_generator,
        "create_video_generator",
        lambda **_kwargs: FakeVideoGenerator(),
    )

    output = await run_freezone_video_gen(
        project_dir=tmp_path,
        job_id="direct-mini-h3-t2v",
        prompt="水果短剧，镜头快速推进",
        reference_items=[],
        backend=f"direct_{registry_id}",
    )

    assert output.exists()


def test_direct_video_option_drives_canvas_capabilities(monkeypatch, tmp_path) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="kling-v3-omni-v2v-create")

    model = list_direct_video_models()[0]
    option = direct_video_model_option(model)
    options = video_node.get_freezone_video_model_options()

    assert option["id"] == f"direct_{registry_id}"
    assert option["provider"] == "direct"
    assert option["family"] == "kacang-kling-v2v"
    assert option["supportedModes"] == ["videoEdit"]
    assert option["referenceLimits"]["videoEdit"]["video"] == 1
    assert option["referenceLimits"]["videoEdit"]["audio"] == 0
    assert option["parameterDefaults"] == {
        "resolution": "720p",
        "durationSeconds": 5,
        "aspectRatio": "16:9",
        "generateAudio": False,
        "strategy": "balanced",
    }
    assert any(item["id"] == option["id"] for item in options)
    assert video_node.freezone_video_edit_contract(option["id"]) == {
        "image": 9,
        "video": 1,
        "audio": 0,
    }
    assert video_node.freezone_video_model_family(option["id"]) == "kacang-kling-v2v"


def test_direct_video_option_publishes_discovered_size_slots(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="operator-video-v1")
    record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="openai-video",
        upstream_model="operator-video-v1",
        capability={
            "verificationStatus": "metadata",
            "modelFound": True,
            "discoveredModelCount": 1,
            "sizeSlots": ["1536x864", "864x1536"],
            "sizeField": "output_size",
        },
    )

    model = resolve_direct_video_model(f"direct_{registry_id}")
    assert model is not None
    option = direct_video_model_option(model)

    assert option["sizeOptions"] == ["1536x864", "864x1536"]
    assert option["sizeField"] == "output_size"


def test_seedance_profile_does_not_inherit_unverified_size_slots(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="seedance-2.5")
    record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="openai-video",
        upstream_model="seedance-2.5",
        capability={
            "source": "models-profile-contract",
            "verificationStatus": "contract-resolved",
            "declaredCapabilities": ["sizeSlots"],
            "sizeSlots": ["1920x1080", "1080x1920"],
            "sizeField": "size",
        },
    )

    model = resolve_direct_video_model(f"direct_{registry_id}")
    assert model is not None
    option = direct_video_model_option(model)

    assert model.profile.size_slots == ()
    assert option["sizeOptions"] == []
    assert option["aspectRatioOptions"] == [
        "16:9",
        "9:16",
        "1:1",
        "4:3",
        "3:4",
        "21:9",
    ]


def test_generic_direct_model_passes_discovered_size_slots_to_adapter(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="operator-video-v1")
    record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="openai-video",
        upstream_model="operator-video-v1",
        capability={
            "verificationStatus": "metadata",
            "modelFound": True,
            "discoveredModelCount": 1,
            "adapterFamily": "prediction",
            "adapterConfidence": 0.9,
            "sizeSlots": ["992x432", "640x640"],
            "sizeField": "output_size",
            "workflowInputRules": [
                {
                    "key": "duration",
                    "rule": {"nodeId": "172", "field": "inputs.value"},
                }
            ],
            "resolutionMappings": [
                {
                    "label": "736p竖",
                    "width": 736,
                    "height": 1280,
                    "values": {
                        "174.inputs.Number": 736,
                        "175.inputs.Number": 1280,
                    },
                }
            ],
        },
    )

    model = resolve_direct_video_model(f"direct_{registry_id}")
    assert model is not None
    options = model.generator_options({})

    assert options["adapter_family"] == "prediction"
    assert options["size_slots"] == ("992x432", "640x640")
    assert options["size_field"] == "output_size"
    assert options["allowed_aspect_ratios"] == ()
    assert options["workflow_input_rules"] == [
        {
            "key": "duration",
            "rule": {"nodeId": "172", "field": "inputs.value"},
        }
    ]
    assert options["resolution_mappings"] == [
        {
            "label": "736p竖",
            "width": 736,
            "height": 1280,
            "values": {
                "174.inputs.Number": 736,
                "175.inputs.Number": 1280,
            },
        }
    ]


@pytest.mark.parametrize(
    ("field", "raw_value", "expected"),
    [
        (
            "workflowInputRules",
            [{"key": "duration", "rule": {"nodeId": "3"}}],
            [{"key": "duration", "rule": {"nodeId": "3"}}],
        ),
        (
            "workflowInputRules",
            {"options": [{"key": "duration", "rule": {"nodeId": "3"}}]},
            [{"key": "duration", "rule": {"nodeId": "3"}}],
        ),
        (
            "workflowInputRules",
            {"key": "duration", "rule": {"nodeId": "3"}},
            [{"key": "duration", "rule": {"nodeId": "3"}}],
        ),
        ("workflowInputRules", "discard-me", []),
        (
            "resolutionMappings",
            [{"label": "720p", "width": 720}],
            [{"label": "720p", "width": 720}],
        ),
        (
            "resolutionMappings",
            {"options": [{"label": "720p", "width": 720}]},
            [{"label": "720p", "width": 720}],
        ),
        (
            "resolutionMappings",
            {"label": "720p", "width": 720},
            [{"label": "720p", "width": 720}],
        ),
        ("resolutionMappings", 42, []),
    ],
)
def test_direct_video_option_normalizes_cached_record_shapes(
    monkeypatch, tmp_path, field, raw_value, expected
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="operator-video-v1")
    record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="openai-video",
        upstream_model="operator-video-v1",
        capability={
            "verificationStatus": "metadata",
            "modelFound": True,
            "discoveredModelCount": 1,
            "adapterFamily": "prediction",
            "adapterConfidence": 0.9,
            "modes": ["textToVideo"],
            field: raw_value,
        },
    )

    model = resolve_direct_video_model(f"direct_{registry_id}")
    assert model is not None
    option = direct_video_model_option(model)

    assert option[field] == expected


def test_direct_video_option_bounds_normalized_capability_records(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="operator-video-v1")
    raw_rules = [{"key": str(index)} for index in range(140)]
    raw_rules.insert(7, "drop-me")
    record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="openai-video",
        upstream_model="operator-video-v1",
        capability={
            "verificationStatus": "metadata",
            "modelFound": True,
            "discoveredModelCount": 1,
            "adapterFamily": "prediction",
            "adapterConfidence": 0.9,
            "modes": ["textToVideo"],
            "workflowInputRules": raw_rules,
        },
    )

    model = resolve_direct_video_model(f"direct_{registry_id}")
    assert model is not None
    option = direct_video_model_option(model)

    assert len(option["workflowInputRules"]) <= 128
    assert len(option["workflowInputRules"]) == 127
    assert all(isinstance(item, dict) for item in option["workflowInputRules"])


@pytest.mark.parametrize(
    ("upstream_model", "family", "resolution", "maximum_duration"),
    [
        ("sd2.0-480p", "prompt-hubs-sd", "480p", 15),
        ("s-videos-f-933-fast-480-2", "kacang-933", "480p", 15),
        ("mini-h3", "kacang-mini-h3", "2k", 15),
        ("seedance-2.5", "direct", "720p", 30),
        ("operator-video-v1", "direct", "720p", 15),
    ],
)
def test_direct_video_profile_publishes_safe_balanced_defaults(
    upstream_model: str,
    family: str,
    resolution: str,
    maximum_duration: int,
) -> None:
    option = direct_video_model_option(
        DirectVideoModel(
            registry_id="profile-test",
            label="Profile test",
            upstream_model=upstream_model,
            base_url="http://127.0.0.1:9800/v1",
            api_key="direct-test-key",
            enabled=True,
        )
    )

    assert option["family"] == family
    assert option["parameterDefaults"]["resolution"] == resolution
    assert option["parameterDefaults"]["durationSeconds"] == 5
    assert option["maxDuration"] == maximum_duration


def test_video_option_carries_a_broken_channel_contract_reason(
    monkeypatch, tmp_path
) -> None:
    """存下来的渠道合同读不动时，节点面板要能看到原因。

    解码层把坏合同摘掉、把原因写在记录上；如果模型选项不带这个原因，节点面板
    就会把「合同坏了」显示成「本来就没有合同」——那是骗人。
    """

    import json
    import sqlite3

    from novelvideo.model_gateway_settings import _settings_db_path

    registry_id = _save_one(monkeypatch, tmp_path, model_id="sd-2.0-fast-v1")
    raw = get_direct_video_models()[0]
    connection = sqlite3.connect(str(_settings_db_path()))
    connection.execute(
        "update runtime_settings set value=? where key='direct_video_models'",
        (
            json.dumps(
                [
                    {
                        **raw,
                        "wireContract": {
                            "profileId": "broken",
                            "source": "channel",
                            "evidence": "operator",
                            "fields": {
                                "profile_id": "broken",
                                "duraton_choices": [5],
                            },
                        },
                    }
                ],
                ensure_ascii=False,
            ),
        ),
    )
    connection.commit()
    connection.close()

    model = resolve_direct_video_model(f"direct_{registry_id}")
    assert model is not None
    option = direct_video_model_option(model)

    assert option["wireContractError"], "读不动的合同必须把原因带出来"
    assert option["wireContractSource"] == "seed", "坏合同不能当成渠道合同用"


def test_s_25db_route_keeps_thirty_second_duration_in_transport(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="S-2.5db-线路三")
    model = resolve_direct_video_model(f"direct_{registry_id}")

    assert model is not None
    assert model.profile.duration == (30,)
    assert direct_video_model_option(model)["parameterDefaults"]["durationSeconds"] == 30
    generator = NewApiVideoGenerator(**model.generator_options({}))
    payload = generator._apply_capability_contract(
        {
            "model": "S-2.5db-线路三",
            "prompt": "fixture",
            "duration_seconds": 30,
        }
    )

    assert generator._duration_bounds() == (30, 30)
    assert payload["duration_seconds"] == 30


def test_sd_25_m_720p_v1_relay_route_keeps_selectable_durations(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="sd-2.5-M-720P-v1")
    model = resolve_direct_video_model(f"direct_{registry_id}")

    assert model is not None
    assert model.profile.name == "sd-2.5-720p-v1"
    assert model.profile.duration == (5, 10, 15, 30)
    option = direct_video_model_option(model)

    assert option["parameterDefaults"]["durationSeconds"] == 5
    assert option["parameterDefaults"]["resolution"] == "720p"
    assert option["maxDuration"] == 30
    # 目录没申报档位时，渠道档案声明的精确档位必须上桌（区间滑块会让用户
    # 以为 5–30 里每个整数秒都能用；真值只有 5/10/15/30）。
    assert option["durationOptions"] == [5, 10, 15, 30]
    assert option["durationParameterEnabled"] is True
    assert option["supportedModes"] == [
        "textToVideo",
        "imageToVideo",
        "imageReference",
        "allReference",
    ]
    assert option["referenceLimits"]["allReference"] == {
        "image": 30,
        "video": 10,
        "audio": 10,
    }
    generator = NewApiVideoGenerator(**model.generator_options({}))
    payload = generator._apply_capability_contract(
        {
            "model": "sd-2.5-M-720P-v1",
            "prompt": "fixture",
            "duration_seconds": 30,
        }
    )

    assert payload["duration_seconds"] == 30


def test_catalog_duration_tiers_follow_the_channel_contract_without_probe(
    monkeypatch, tmp_path
) -> None:
    """huabu 档位（5/10/15）同样来自渠道档案，无需等探测记录。"""
    registry_id = _save_one(monkeypatch, tmp_path, model_id="sd-2.0-fast-v1")
    model = resolve_direct_video_model(f"direct_{registry_id}")

    assert model is not None
    option = direct_video_model_option(model)
    assert option["durationOptions"] == [5, 10, 15]
    assert option["durationParameterEnabled"] is True


def test_sd_25_relay_contract_is_scoped_to_the_v1_sku() -> None:
    target = direct_video_profiles.resolve_direct_video_profile("sd-2.5-M-720P-v1")
    sibling = direct_video_profiles.resolve_direct_video_profile("sd-2.5-M-720p")

    assert target.name == "sd-2.5-720p-v1"
    assert sibling.name == "openai-video-generic"


def test_direct_video_model_rejects_credentials_in_url(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))

    try:
        save_direct_video_models(
            [
                {
                    "label": "Bad URL",
                    "modelId": "video-v1",
                    "baseUrl": "https://secret@example.test/v1",
                    "apiKey": "direct-test-key",
                }
            ]
        )
    except ValueError as exc:
        assert "baseUrl" in str(exc)
    else:
        raise AssertionError("credential-bearing base URL should be rejected")


def test_direct_video_model_api_masks_key_and_feeds_gateway_status(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        model_gateway, "require_ce_direct_video_management", lambda: None
    )
    app = FastAPI()
    app.include_router(model_gateway.router)

    with TestClient(app) as client:
        response = client.post(
            "/model-gateway/direct-video-models",
            json={
                "models": [
                    {
                        "label": "本地视频",
                        "modelId": "local-video-v1",
                        "baseUrl": "http://127.0.0.1:9800/v1",
                        "apiKey": "direct-test-key",
                        "enabled": True,
                    }
                ]
            },
        )
        config_response = client.get("/model-gateway/config")

    assert response.status_code == 200
    assert "direct-test-key" not in response.text
    assert config_response.status_code == 200
    models = config_response.json()["data"]["directVideoModels"]
    assert models[0]["modelId"] == "local-video-v1"
    assert models[0]["apiKeyPreview"] == "dire...-key"


def test_empty_direct_video_save_requires_server_side_confirmation(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    save_direct_video_models(
        [
            {
                "label": "要保留的视频",
                "modelId": "local-video-v1",
                "baseUrl": "http://127.0.0.1:9800/v1",
                "apiKey": "direct-test-key",
                "enabled": True,
                "isDefault": True,
            }
        ]
    )

    with pytest.raises(ValueError, match="confirmClear=true"):
        save_direct_video_models([])
    assert [item["modelId"] for item in get_direct_video_models()] == [
        "local-video-v1"
    ]

    save_direct_video_models([], confirm_clear=True)
    assert get_direct_video_models() == []
    # Saving an empty registry again is idempotent and needs no confirmation.
    save_direct_video_models([])


def test_direct_video_api_maps_unconfirmed_clear_to_409(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    save_direct_video_models(
        [
            {
                "label": "要保留的视频",
                "modelId": "local-video-v1",
                "baseUrl": "http://127.0.0.1:9800/v1",
                "apiKey": "direct-test-key",
                "enabled": True,
                "isDefault": True,
            }
        ]
    )
    monkeypatch.setattr(
        model_gateway, "require_ce_direct_video_management", lambda: None
    )
    app = FastAPI()
    app.include_router(model_gateway.router)

    with TestClient(app) as client:
        rejected = client.post(
            "/model-gateway/direct-video-models",
            json={"models": []},
        )
        assert rejected.status_code == 409
        assert "confirmClear=true" in rejected.json()["detail"]
        assert len(get_direct_video_models()) == 1

        accepted = client.post(
            "/model-gateway/direct-video-models",
            json={"models": [], "confirmClear": True},
        )

    assert accepted.status_code == 200
    assert accepted.json()["data"] == []
    assert get_direct_video_models() == []


def test_direct_video_probe_reads_current_model_contract_without_generation(monkeypatch) -> None:
    seen: dict[str, object] = {}
    urls: list[str] = []

    class FakeClient:
        def __init__(self, **kwargs):
            seen["client"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, endpoint, *, headers):
            seen["url"] = endpoint
            seen["method"] = "GET"
            seen["auth"] = headers.get("Authorization")
            urls.append(endpoint)
            import httpx

            if endpoint.endswith("/models/seedance-direct-fast"):
                return httpx.Response(
                    404,
                    request=httpx.Request("GET", endpoint),
                    json={"error": {"message": "no model detail route"}},
                )
            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={
                    "data": [
                        {
                            "id": "seedance-direct-fast",
                            "input_modalities": ["text", "image"],
                            "capabilities": {
                                "sizeOptions": ["992x432", "640x640"],
                                "resolutionOptions": ["720p"],
                                "aspectRatios": ["16:9", "1:1"],
                                "minDuration": 4,
                                "maxDuration": 12,
                                "supportsAudio": False,
                            },
                        },
                        {"id": "other-video"},
                    ]
                },
            )

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = probe_direct_video_model(
        upstream_model="seedance-direct-fast",
        base_url="http://127.0.0.1:9800/v1/",
        api_key="direct-test-key",
    )

    assert {
        key: result[key]
        for key in ("ok", "modelFound", "discoveredModelCount", "protocol")
    } == {
        "ok": True,
        "modelFound": True,
        "discoveredModelCount": 2,
        "protocol": "openai-video",
    }
    assert result["capability"] == {
        "source": "models-metadata",
        "verificationStatus": "metadata",
        "inputModalities": ["text", "image"],
        "modes": ["textToVideo", "imageToVideo"],
        "sizeSlots": ["992x432", "640x640"],
        "sizeField": "size",
        "resolutionOptions": ["720p"],
        "resolutionSource": "catalog",
        "aspectRatios": ["16:9", "1:1"],
        "aspectRatioSource": "catalog",
        "durationRange": [4, 12],
        "nativeAudio": "unsupported",
        "capabilityDiscovery": {"status": "catalog", "sources": ["models"]},
        "declaredCapabilities": [
            "sizeSlots",
            "resolutionOptions",
            "aspectRatios",
        ],
            "referenceLimits": {
                "inputImages": 1,
                "referenceImages": 0,
                "referenceVideos": 0,
                "referenceAudios": 0,
            },
            "verificationStage": "catalog",
        }
    assert "http://127.0.0.1:9800/v1/models" in urls
    assert "http://127.0.0.1:9800/v1/models/seedance-direct-fast" in urls
    assert all("/videos" not in url and "/generate" not in url for url in urls)
    assert seen["auth"] == "Bearer direct-test-key"


def test_direct_video_probe_rejects_key_when_video_task_read_returns_401(monkeypatch) -> None:
    import httpx
    from novelvideo.generators.video import direct_video_probe

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, endpoint, *, headers):
            request = httpx.Request("GET", endpoint)
            if endpoint.endswith("/models"):
                return httpx.Response(200, request=request, json={"data": [{"id": "seedance-2.5"}]})
            if "/videos/generations/" in endpoint:
                assert headers["Authorization"] == "Bearer saved-test-key"
                return httpx.Response(401, request=request, json={"detail": "invalid api key"})
            return httpx.Response(404, request=request, json={"detail": "not found"})

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(
        direct_video_probe,
        "discover_video_openapi",
        lambda **_kwargs: {
            "found": True,
            "url": "https://video.example/v1/openapi.json",
            "paths": [
                "/v1/videos/{task_id}",
                "/v1/videos/generations/{task_id}",
                "/v1/videos",
                "/v1/videos/generations",
            ],
            "operations": [
                {
                    "method": "post",
                    "path": "/v1/videos",
                    "operationId": "create_video_vinted_alias_v1_videos_post",
                },
                {
                    "method": "post",
                    "path": "/v1/videos/generations",
                    "operationId": "create_video_v1_videos_generations_post",
                },
            ],
            "version": "3.0.0",
        },
    )

    result = probe_direct_video_model(
        upstream_model="seedance-2.5",
        base_url="https://video.example/v1",
        api_key="saved-test-key",
        protocol="openai-video",
    )

    assert result["modelFound"] is True
    assert result["ok"] is False
    assert result["errorCode"] == "VIDEO_AUTH_REJECTED"
    assert result["httpStatus"] == 401
    assert result["credentialValidation"] == {"status": "rejected", "httpStatus": 401}
    assert result["openapiSubmitPath"] == "/videos/generations"
    assert "不能单独判定密钥本身无效" in result["error"]


def test_dolasd_key_info_probe_is_read_only_and_never_returns_response_body(monkeypatch) -> None:
    import httpx
    from novelvideo.generators.video.direct_video_probe import _probe_documented_key_info

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, endpoint, *, headers):
            assert endpoint == "https://dolasd.xyz/v1/key/info"
            assert headers["Authorization"] == "Bearer saved-test-key"
            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={"name": "test", "recent_tasks": [{"prompt": "private"}]},
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = _probe_documented_key_info(
        base_url="https://dolasd.xyz/v1",
        api_key="saved-test-key",
        timeout=2,
    )
    assert result == {"status": "accepted", "httpStatus": 200}


def test_direct_video_probe_leaves_key_unverified_without_task_read_route(monkeypatch) -> None:
    import httpx
    from novelvideo.generators.video import direct_video_probe

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, endpoint, *, headers):
            return httpx.Response(
                200 if endpoint.endswith("/models") else 404,
                request=httpx.Request("GET", endpoint),
                json={"data": [{"id": "seedance-2.5"}]} if endpoint.endswith("/models") else {},
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(
        direct_video_probe,
        "discover_video_openapi",
        lambda **_kwargs: {"found": False, "url": "", "paths": [], "operations": []},
    )

    result = probe_direct_video_model(
        upstream_model="seedance-2.5",
        base_url="https://video.example/v1",
        api_key="saved-test-key",
        protocol="openai-video",
    )

    assert result["ok"] is True
    assert result["credentialValidation"]["status"] == "unverified"


def test_direct_video_probe_accepts_structured_missing_task_response(monkeypatch) -> None:
    import httpx
    from novelvideo.generators.video import direct_video_probe

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, endpoint, *, headers):
            request = httpx.Request("GET", endpoint)
            if "/videos/" in endpoint:
                return httpx.Response(404, request=request, json={"detail": "task not found"})
            if endpoint.endswith("/models"):
                return httpx.Response(200, request=request, json={"data": [{"id": "seedance-2.5"}]})
            return httpx.Response(404, request=request, json={"detail": "not found"})

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(
        direct_video_probe,
        "discover_video_openapi",
        lambda **_kwargs: {
            "found": True,
            "url": "https://video.example/v1/openapi.json",
            "paths": ["/v1/videos/{task_id}"],
            "operations": [],
        },
    )

    result = probe_direct_video_model(
        upstream_model="seedance-2.5",
        base_url="https://video.example/v1",
        api_key="saved-test-key",
        protocol="openai-video",
    )

    assert result["ok"] is True
    assert result["credentialValidation"] == {"status": "accepted", "httpStatus": 404}


def test_direct_video_probe_does_not_treat_auth_404_as_accepted(monkeypatch) -> None:
    import httpx
    from novelvideo.generators.video import direct_video_probe

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, endpoint, *, headers):
            request = httpx.Request("GET", endpoint)
            if "/videos/" in endpoint:
                return httpx.Response(404, request=request, json={"detail": "invalid api key"})
            if endpoint.endswith("/models"):
                return httpx.Response(200, request=request, json={"data": [{"id": "seedance-2.5"}]})
            return httpx.Response(404, request=request, json={"detail": "not found"})

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(
        direct_video_probe,
        "discover_video_openapi",
        lambda **_kwargs: {
            "found": True,
            "url": "https://video.example/v1/openapi.json",
            "paths": ["/v1/videos/{video_id}"],
            "operations": [],
        },
    )

    result = probe_direct_video_model(
        upstream_model="seedance-2.5",
        base_url="https://video.example/v1",
        api_key="saved-test-key",
        protocol="openai-video",
    )

    assert result["ok"] is False
    assert result["errorCode"] == "VIDEO_AUTH_REJECTED"
    assert result["credentialValidation"] == {"status": "rejected", "httpStatus": 404}


def test_direct_video_probe_uses_supported_protocol_contract(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, endpoint, *, headers):
            import httpx

            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={"data": [{
                    "id": "minimax-h3",
                    "supported_protocols": ["minimax:video_generation_v2"],
                }]},
            )

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = probe_direct_video_model(
        upstream_model="minimax-h3",
        base_url="https://tokendance.space/gateway/v1",
        api_key="direct-test-key",
    )

    assert result["protocol"] == "minimax-video-v2"
    assert result["supportedProtocols"] == ["minimax:video_generation_v2"]
    assert result["capability"]["verificationStatus"] == "contract-resolved"
    assert result["capability"]["resolutionOptions"] == ["768p", "2k"]
    assert result["capability"]["nativeAudio"] == "required"


def test_seedance_probe_drops_size_metadata_when_using_model_profile_fallback(
    monkeypatch,
) -> None:
    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, endpoint, *, headers):
            import httpx

            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={
                    "data": [
                        {
                            "id": "seedance-2.5",
                            "capabilities": {
                                "sizeOptions": ["1920x1080", "1080x1920"]
                            },
                        }
                    ]
                },
            )

    import httpx
    from novelvideo.generators.video import direct_video_probe

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(
        direct_video_probe,
        "discover_video_openapi",
        lambda **_kwargs: {"found": False, "url": "", "paths": [], "version": ""},
    )

    result = probe_direct_video_model(
        upstream_model="seedance-2.5",
        base_url="http://127.0.0.1:9800/v1",
        api_key="direct-test-key",
    )

    assert result["ok"] is True
    assert result["capability"]["source"] == "models-profile-contract"
    assert "sizeSlots" not in result["capability"]
    assert "sizeField" not in result["capability"]


def test_video_probe_labels_ratios_derived_from_upstream_sizes(monkeypatch) -> None:
    """上游只公开精确尺寸时，比例由尺寸推出，且不得被本地档案的比例盖掉。"""

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, endpoint, *, headers):
            import httpx

            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={
                    "data": [
                        {
                            "id": "seedance-2.5",
                            "durations": [30],
                            "durations_all": [10, 30],
                            # dolasd 真机目录的原文形状：只有像素尺寸，没有比例字段。
                            "sizes": [
                                "1280x720",
                                "1920x1080",
                                "720x1280",
                                "1080x1920",
                                "1024x1024",
                                "1440x1080",
                                "1080x1440",
                            ],
                        }
                    ]
                },
            )

    import httpx
    from novelvideo.generators.video import direct_video_probe

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(
        direct_video_probe,
        "discover_video_openapi",
        lambda **_kwargs: {"found": False, "url": "", "paths": [], "version": ""},
    )

    result = probe_direct_video_model(
        upstream_model="seedance-2.5",
        base_url="http://127.0.0.1:9800/v1",
        api_key="direct-test-key",
    )

    capability = result["capability"]
    assert capability["aspectRatioSource"] == "catalog-size-slots"
    assert capability["aspectRatios"] == ["16:9", "9:16", "1:1", "4:3", "3:4"]
    # 21:9 只写在本地档案里，上游这次没有任何尺寸能产出它；节点不能拿它冒充上游声明。
    assert "21:9" not in capability["aspectRatios"]
    # 上游没说分辨率，本地档案补的档位必须自报家门。
    assert capability["resolutionSource"] == "profile"
    assert capability["resolutionOptions"]
    assert capability["durationOptions"] == [30]


def test_direct_video_option_reports_ratio_and_resolution_source(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="seedance-2.5")
    base_url = "http://127.0.0.1:9800/v1"
    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="seedance-2.5",
        capability={
            "verificationStatus": "catalog-confirmed",
            "modelFound": True,
            "discoveredModelCount": 2,
            "aspectRatios": ["16:9", "9:16"],
            "aspectRatioSource": "catalog-size-slots",
            "resolutionOptions": ["480p", "720p"],
            "resolutionSource": "profile",
        },
        replace_snapshot=True,
    )

    model = resolve_direct_video_model(f"direct_{registry_id}")
    assert model is not None
    option = direct_video_model_option(model)
    assert option["aspectRatioOptions"] == ["16:9", "9:16"]
    assert option["aspectRatioSource"] == "catalog-size-slots"
    assert option["resolutionSource"] == "profile"


def test_fresh_video_snapshot_revokes_undeclared_capabilities(
    monkeypatch, tmp_path
) -> None:
    """新一轮探测没说的能力必须消失，不能跟着旧快照继续留在节点目录里。"""

    registry_id = _save_one(monkeypatch, tmp_path, model_id="seedance-2.5")
    base_url = "http://127.0.0.1:9800/v1"
    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="seedance-2.5",
        capability={
            "verificationStatus": "runtime-verified",
            "verificationStage": "artifact",
            "modelFound": True,
            "discoveredModelCount": 2,
            "declaredCapabilities": ["sizeSlots", "aspectRatios", "durationOptions"],
            "sizeSlots": ["1920x1080", "1080x1920"],
            "sizeField": "size",
            "aspectRatios": ["16:9", "9:16", "21:9"],
            "aspectRatioSource": "catalog",
            "durationOptions": [5, 10],
            "parameters": [{"key": "duration", "providerKey": "duration"}],
            "openapiPaths": ["/v1/videos", "/v1/videos/generations"],
            "referenceLimits": {"inputImages": 2},
        },
    )

    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="seedance-2.5",
        capability={
            "verificationStatus": "catalog-confirmed",
            "verificationStage": "catalog",
            "modelFound": True,
            "discoveredModelCount": 1,
            "resolutionOptions": ["720p"],
            "resolutionSource": "catalog",
        },
        replace_snapshot=True,
    )

    current = get_cached_capability_for_model(
        base_url=base_url,
        upstream_model="seedance-2.5",
        protocol="openai-video",
    )
    for stale_key in (
        "sizeSlots",
        "sizeField",
        "aspectRatios",
        "aspectRatioSource",
        "durationOptions",
        "parameters",
        "openapiPaths",
        "referenceLimits",
    ):
        assert stale_key not in current, stale_key
    assert current["resolutionOptions"] == ["720p"]
    assert current["verificationStatus"] == "catalog-confirmed"
    assert current["verificationStage"] == "catalog"

    model = resolve_direct_video_model(f"direct_{registry_id}")
    assert model is not None
    option = direct_video_model_option(model)
    # 上游这次没给比例，节点回落到本地档案时必须如实标 profile，而不是装成上游声明。
    assert option["aspectRatioSource"] == "profile"
    assert option["resolutionSource"] == "catalog"


@pytest.mark.parametrize(
    ("status", "error_code"),
    [
        (401, "authentication_failed"),
        (403, "permission_denied"),
        (503, "upstream_http_error"),
    ],
)
def test_direct_video_probe_masks_transport_errors(
    monkeypatch, status: int, error_code: str
) -> None:
    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, endpoint, *, headers):
            import httpx

            return httpx.Response(
                status,
                request=httpx.Request("GET", endpoint),
                json={"error": {"message": "upstream unavailable"}},
            )

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = probe_direct_video_model(
        upstream_model="seedance-direct-fast",
        base_url="http://127.0.0.1:9800/v1",
        api_key="direct-test-key",
    )

    assert result["ok"] is False
    assert result["modelFound"] is False
    assert result["discoveredModelCount"] == 0
    assert result["protocol"] == "openai-video"
    assert result["errorCode"] == error_code
    assert result["httpStatus"] == status
    assert result["error"]
    assert "direct-test-key" not in str(result)


def test_direct_video_capability_cache_is_scoped_by_endpoint_protocol_and_model(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    record_capability(
        base_url="https://first.example/v1",
        protocol="openai-video",
        upstream_model="same-model",
        capability={"sizeSlots": ["992x432"], "modes": ["textToVideo"]},
    )
    record_capability(
        base_url="https://second.example/v1",
        protocol="openai-video",
        upstream_model="same-model",
        capability={"sizeSlots": ["640x640"], "modes": ["imageToVideo"]},
    )

    assert get_cached_capability(
        base_url="https://first.example/v1",
        protocol="openai-video",
        upstream_model="same-model",
    )["sizeSlots"] == ["992x432"]
    assert get_cached_capability(
        base_url="https://second.example/v1",
        protocol="openai-video",
        upstream_model="same-model",
    )["sizeSlots"] == ["640x640"]
    cache_text = (tmp_path / "state" / "video_capability_cache.json").read_text()
    assert "apiKey" not in cache_text
    assert "https://" not in cache_text


def test_direct_video_capability_cache_uses_shared_atomic_writer(
    monkeypatch, tmp_path
) -> None:
    from novelvideo.generators.video import direct_video_capability_cache as cache
    from novelvideo.utils.state_index_files import write_json_atomic as production_writer

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cache, "_memo", None)
    monkeypatch.setattr(cache, "_memo_path", None)
    writes = []

    def recording_writer(path, payload):
        writes.append(path)
        production_writer(path, payload)

    monkeypatch.setattr(cache, "write_json_atomic", recording_writer)

    record_capability(
        base_url="https://video.example/v1",
        protocol="openai-video",
        upstream_model="video-model",
        capability={"modes": ["textToVideo"]},
    )

    assert writes == [tmp_path / "video_capability_cache.json"]


def test_direct_video_status_uses_detected_protocol_contract(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(
        monkeypatch, tmp_path, model_id="minimax-h3", protocol="auto"
    )
    record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="minimax-video-v2",
        upstream_model="minimax-h3",
        capability={
            "verificationStatus": "contract-resolved",
            "detectedProtocol": "minimax-video-v2",
            "supportedProtocols": ["minimax:video_generation_v2"],
            "resolutionOptions": ["768p", "2k"],
            "durationRange": [4, 15],
            "nativeAudio": "required",
        },
    )

    status = build_direct_video_models_status()[0]

    assert status["id"] == registry_id
    assert status["protocol"] == "minimax-video-v2"
    assert status["protocolLabel"] == "MiniMax Video v2"
    assert status["verificationStatus"] == "contract-resolved"
    assert status["verificationStage"] == "contract"
    assert status["runtimeReady"] is True
    assert status["resolutionOptions"] == ["768p", "2k"]
    assert get_cached_capability_for_model(
        base_url="http://127.0.0.1:9800/v1",
        upstream_model="minimax-h3",
    )["detectedProtocol"] == "minimax-video-v2"


def test_runtime_resolution_rejection_narrows_only_the_exact_endpoint_model(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="minimax_h3")
    base_url = "http://127.0.0.1:9800/v1"
    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="minimax_h3",
        capability={
            "verificationStatus": "runtime-verified",
            "modelFound": True,
            "discoveredModelCount": 1,
            "resolutionOptions": ["768p", "2k"],
        },
    )

    record_runtime_resolution_rejection(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="minimax_h3",
        resolution="2K",
        note="ComfyUI H3 rejects 2K",
    )

    model = resolve_direct_video_model(f"direct_{registry_id}")
    assert model is not None
    option = direct_video_model_option(model)
    assert option["resolutionOptions"] == ["768p"]
    assert option["advertisedResolutionOptions"] == ["768p", "2k"]
    assert option["runtimeRejectedResolutionOptions"] == ["2k"]
    assert option["runtimeCapabilityStatus"] == "degraded"
    assert option["parameterDefaults"]["resolution"] == "768p"

    # A fresh upstream read replaces old runtime observations instead of
    # carrying a rejection forward after the upstream contract changes.
    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="minimax_h3",
        capability={
            "verificationStatus": "contract-resolved",
            "modelFound": True,
            "discoveredModelCount": 1,
            "resolutionOptions": ["768p", "2k"],
        },
        replace_snapshot=True,
    )
    refreshed = direct_video_model_option(model)
    assert refreshed["resolutionOptions"] == ["768p", "2k"]
    assert refreshed["advertisedResolutionOptions"] == ["768p", "2k"]
    assert refreshed["runtimeRejectedResolutionOptions"] == []
    assert refreshed["catalogVerification"] == "catalog-confirmed"


def test_direct_video_capability_lookup_can_isolate_protocol(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    base_url = "https://protocol-scope.example/v1"
    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="same-model",
        capability={"detectedProtocol": "openai-video", "resolutionOptions": ["720p"]},
    )
    record_capability(
        base_url=base_url,
        protocol="minimax-video-v2",
        upstream_model="same-model",
        capability={
            "detectedProtocol": "minimax-video-v2",
            "resolutionOptions": ["2k"],
        },
    )

    assert get_cached_capability_for_model(
        base_url=base_url,
        upstream_model="same-model",
        protocol="openai-video",
    )["resolutionOptions"] == ["720p"]
    assert get_cached_capability_for_model(
        base_url=base_url,
        upstream_model="same-model",
        protocol="minimax-video-v2",
    )["resolutionOptions"] == ["2k"]

    record_capability(
        base_url=base_url,
        protocol="minimax-video-v2",
        upstream_model="same-model",
        capability={"detectedProtocol": "minimax-video-v2", "resolutionOptions": ["540p"]},
        replace_snapshot=True,
    )
    assert get_cached_capability_for_model(
        base_url=base_url,
        upstream_model="same-model",
    )["resolutionOptions"] == ["540p"]
    assert get_cached_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="same-model",
    ) == {}


def test_direct_video_option_revision_identifies_visible_capability_snapshot(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="operator-video-v1")
    base_url = "http://127.0.0.1:9800/v1"
    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="operator-video-v1",
        capability={
            "source": "upstream",
            "verificationStatus": "contract-resolved",
            "modelFound": True,
            "discoveredModelCount": 1,
            "declaredCapabilities": [
                "modes",
                "aspectRatios",
                "resolutionOptions",
                "durationOptions",
            ],
            "modes": ["textToVideo"],
            "aspectRatios": ["16:9"],
            "resolutionOptions": ["720p"],
            "durationOptions": [5],
        },
    )

    model = resolve_direct_video_model(f"direct_{registry_id}")
    assert model is not None
    first = direct_video_model_option(model)
    second = direct_video_model_option(model)

    assert first["capabilitySource"] == "upstream"
    assert first["capabilityRevision"].startswith("video-cap.")
    assert second["capabilityRevision"] == first["capabilityRevision"]
    assert second["durationOptions"] == [5]

    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="operator-video-v1",
        capability={
            "source": "upstream",
            "verificationStatus": "contract-resolved",
            "modelFound": True,
            "discoveredModelCount": 1,
            "declaredCapabilities": [
                "modes",
                "aspectRatios",
                "resolutionOptions",
                "durationOptions",
            ],
            "modes": ["textToVideo"],
            "aspectRatios": ["16:9"],
            "resolutionOptions": ["720p"],
            "durationOptions": [5, 10],
        },
    )

    changed = direct_video_model_option(model)
    assert changed["durationOptions"] == [5, 10]
    assert changed["capabilityRevision"] != first["capabilityRevision"]


def test_direct_video_status_rejects_directory_only_non_video_model(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(
        monkeypatch,
        tmp_path,
        model_id="gemini-3-pro-image-preview",
    )
    record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="openai-video",
        upstream_model="gemini-3-pro-image-preview",
        capability={
            "verificationStatus": "directory-only",
            "detectedProtocol": "openai-video",
            "modelFound": True,
            "discoveredModelCount": 27,
            "inputModalities": [],
        },
    )

    status = build_direct_video_models_status()[0]

    assert status["id"] == registry_id
    assert status["catalogVerification"] == "catalog-confirmed"
    assert status["runtimeReady"] is False
    assert "视频生成能力" in status["disabledReason"]


def test_direct_video_status_enables_known_huabu_contract_when_catalog_has_only_id(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="sd-2.0-fast-v1")
    # The huabu catalog exposes only the ID.  The explicit openai-video
    # selection plus the built-in huabu profile is the durable contract.
    record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="openai-video",
        upstream_model="sd-2.0-fast-v1",
        capability={
            "verificationStatus": "directory-only",
            "detectedProtocol": "openai-video",
            "modelFound": True,
            "discoveredModelCount": 1,
            # 目录只有一个 ID 也能定合同，但「能不能用」还要凭据真的验过：
            # 没有这条记录就只是旧缓存里的绿灯，按新规则必须失效重测。
            "credentialValidation": {"status": "accepted", "httpStatus": 200},
            "credentialCheckedAt": "2026-10-03T00:00:00+00:00",
        },
    )

    status = build_direct_video_models_status()[0]

    assert status["id"] == registry_id
    assert status["catalogVerification"] == "catalog-confirmed"
    assert status["runtimeReady"] is True
    assert status["family"] == "prompt-hubs-sd"
    assert status["supportedModes"] == ["textToVideo", "imageToVideo"]
    assert status["disabledReason"] == ""


def test_cached_pass_without_credential_evidence_is_not_runtime_ready(
    monkeypatch, tmp_path
) -> None:
    """改动前写的旧缓存只有「探测过」没有「钥匙验过」，不许继续冒充可用。"""

    registry_id = _save_one(monkeypatch, tmp_path, model_id="sd-2.0-fast-v1")
    record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="openai-video",
        upstream_model="sd-2.0-fast-v1",
        capability={
            "verificationStatus": "directory-only",
            "probeStatus": "fresh",
            "detectedProtocol": "openai-video",
            "modelFound": True,
            "discoveredModelCount": 1,
        },
        replace_snapshot=True,
    )

    status = build_direct_video_models_status()[0]

    assert status["id"] == registry_id
    assert status["runtimeReady"] is False
    assert status["disabled"] is True
    assert "检测" in status["disabledReason"]


def test_cached_rejected_credential_blocks_runtime_ready(monkeypatch, tmp_path) -> None:
    """上游明确拒绝过的钥匙，即使目录匹配也不许判为可用。"""

    registry_id = _save_one(monkeypatch, tmp_path, model_id="sd-2.0-fast-v1")
    record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="openai-video",
        upstream_model="sd-2.0-fast-v1",
        capability={
            "verificationStatus": "probe-failed",
            "probeStatus": "failed",
            "detectedProtocol": "openai-video",
            "modelFound": True,
            "discoveredModelCount": 1,
            "credentialValidation": {"status": "rejected", "httpStatus": 401},
            "credentialCheckedAt": "2026-10-03T00:00:00+00:00",
            "lastFailure": "上游拒绝了当前 Key（HTTP 401）。",
        },
        replace_snapshot=True,
    )

    status = build_direct_video_models_status()[0]

    assert status["id"] == registry_id
    assert status["runtimeReady"] is False
    assert "401" in status["disabledReason"]


@pytest.mark.parametrize(
    ("recorded_at", "expected"),
    [
        ("2026-10-03T00:00:00+00:00", True),
        ("2020-01-01T00:00:00+00:00", False),
        ("", False),
        ("not-a-timestamp", False),
    ],
)
def test_credential_freshness_window(
    monkeypatch, tmp_path, recorded_at: str, expected: bool
) -> None:
    """凭据记录有存活时长；读不懂或缺失一律算过期，不能永久免检。"""

    import datetime as _datetime

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    fixed_now = _datetime.datetime(2026, 10, 3, 1, 0, tzinfo=_datetime.timezone.utc)

    class _FrozenDatetime(_datetime.datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D102 - 测试替身
            return fixed_now

    monkeypatch.setattr(
        "novelvideo.generators.video.newapi.datetime", _FrozenDatetime, raising=False
    )

    assert (
        NewApiVideoGenerator.credential_record_is_fresh(recorded_at) is expected
    )


@pytest.mark.asyncio
async def test_stale_credential_is_rechecked_before_a_paid_submit(
    monkeypatch, tmp_path
) -> None:
    """记录过期时提交前自动重验；明确被拒就拦下并且不碰任何付费路径。"""

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    direct = DirectVideoModel(
        registry_id="stale-credential",
        label="Seedance 2.5",
        upstream_model="seedance-2.5",
        base_url="https://dolasd.xyz/v1",
        api_key="test-key",
        enabled=True,
        protocol="openai-video",
        requested_protocol="openai-video",
    )
    record_capability(
        base_url="https://dolasd.xyz/v1",
        protocol="openai-video",
        upstream_model="seedance-2.5",
        capability={
            "verificationStatus": "catalog-confirmed",
            "probeStatus": "fresh",
            "modelFound": True,
            "discoveredModelCount": 2,
            "detectedProtocol": "openai-video",
            "openapiQueryPath": "/videos/{task_id}",
            "credentialValidation": {"status": "accepted", "httpStatus": 200},
            "credentialCheckedAt": "2020-01-01T00:00:00+00:00",
        },
        replace_snapshot=True,
    )
    options = direct.generator_options({})
    generator = NewApiVideoGenerator(**options)
    monkeypatch.setattr(
        generator,
        "_probe_credential_for_runtime",
        lambda _base, _cached: {"status": "rejected", "httpStatus": 401},
    )

    message = await generator._recheck_stale_credential("https://dolasd.xyz/v1")

    assert "401" in message
    stored = get_cached_capability_for_model(
        base_url="https://dolasd.xyz/v1", upstream_model="seedance-2.5"
    )
    # 能力字段不能被这次凭据写入抹掉。
    assert stored["credentialValidation"]["status"] == "rejected"
    assert stored["modelFound"] is True


@pytest.mark.asyncio
async def test_fresh_credential_record_skips_the_extra_round_trip(
    monkeypatch, tmp_path
) -> None:
    """记录还新鲜时不额外发请求，正常生成不该多付一次延迟。"""

    import datetime as _datetime

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    direct = DirectVideoModel(
        registry_id="fresh-credential",
        label="Seedance 2.5",
        upstream_model="seedance-2.5",
        base_url="https://dolasd.xyz/v1",
        api_key="test-key",
        enabled=True,
        protocol="openai-video",
        requested_protocol="openai-video",
    )
    record_capability(
        base_url="https://dolasd.xyz/v1",
        protocol="openai-video",
        upstream_model="seedance-2.5",
        capability={
            "verificationStatus": "catalog-confirmed",
            "modelFound": True,
            "discoveredModelCount": 2,
            "detectedProtocol": "openai-video",
            "credentialValidation": {"status": "accepted", "httpStatus": 200},
            "credentialCheckedAt": _datetime.datetime.now(
                _datetime.timezone.utc
            ).isoformat(),
        },
        replace_snapshot=True,
    )
    generator = NewApiVideoGenerator(**direct.generator_options({}))
    calls: list[str] = []

    def _unexpected(*_args, **_kwargs):
        calls.append("called")
        return {"status": "rejected", "httpStatus": 401}

    monkeypatch.setattr(generator, "_probe_credential_for_runtime", _unexpected)

    assert await generator._recheck_stale_credential("https://dolasd.xyz/v1") == ""
    assert calls == []


def test_sparse_metadata_reference_modes_keep_safe_reference_envelope(
    monkeypatch,
) -> None:
    metadata = {
        "source": "models-metadata",
        "modes": ["textToVideo", "imageToVideo", "allReference", "videoEdit"],
        "referenceLimits": {
            "inputImages": 1,
            "referenceImages": 0,
            "referenceVideos": 1,
            "referenceAudios": 0,
        },
    }
    monkeypatch.setattr(
        "novelvideo.generators.video.direct_video_capability_cache.get_cached_capability",
        lambda **_kwargs: dict(metadata),
    )
    monkeypatch.setattr(
        "novelvideo.generators.video.direct_video_capability_cache.get_cached_capability_for_model",
        lambda **_kwargs: dict(metadata),
    )

    profile = direct_video_profiles.resolve_direct_video_profile(
        "gemini-omni-flash",
        base_url="http://127.0.0.1:9800/v1",
        protocol="openai-video",
    )

    assert "reference_to_video" in {mode.value for mode in profile.modes}
    assert profile.reference_limits.reference_images == 9
    assert profile.reference_limits.reference_videos == 1


def test_model_center_isolates_one_invalid_video_capability(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(
        monkeypatch,
        tmp_path,
        model_id="broken-video-contract",
    )

    def invalid_option(_model):
        raise ValueError("fixture capability mismatch")

    monkeypatch.setattr(
        "novelvideo.generators.video.direct_models.direct_video_model_option",
        invalid_option,
    )

    status = build_direct_video_models_status()

    assert len(status) == 1
    assert status[0]["id"] == registry_id
    assert status[0]["verificationStatus"] == "invalid-contract"
    assert status[0]["runtimeReady"] is False
    assert status[0]["disabled"] is True


def test_video_probe_enriches_sparse_catalog_with_known_profile_contract(monkeypatch):
    from novelvideo.generators.video import direct_video_probe

    class FakeResponse:
        status_code = 200
        text = '{"data":[{"id":"sd-2.0-fast-v1"}]}'

        def json(self):
            return {"data": [{"id": "sd-2.0-fast-v1"}]}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(
        "httpx.Client",
        lambda **_kwargs: FakeClient(),
    )

    result = direct_video_probe.probe_direct_video_model(
        upstream_model="sd-2.0-fast-v1",
        base_url="https://video.example/v1",
        api_key="fixture-key",
    )

    assert result["modelFound"] is True
    assert result["capability"]["verificationStatus"] == "contract-resolved"
    assert result["capability"]["modes"] == ["textToVideo", "imageToVideo"]
    assert result["capability"]["durationRange"] == [5, 15]


def test_catalog_refresh_preserves_video_runtime_verification(
    monkeypatch, tmp_path
) -> None:
    from novelvideo.generators.video.direct_video_capability_cache import (
        record_runtime_verification,
    )

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    identity = {
        "base_url": "https://video.example/v1",
        "protocol": "openai-video",
        "upstream_model": "video-model",
    }
    record_runtime_verification(**identity)
    record_capability(
        **identity,
        capability={
            "verificationStatus": "metadata",
            "modelFound": True,
            "discoveredModelCount": 5,
            "modes": ["textToVideo"],
        },
    )

    cached = get_cached_capability_for_model(
        base_url=identity["base_url"],
        upstream_model=identity["upstream_model"],
    )
    assert cached["verificationStatus"] == "runtime-verified"
    assert cached["verificationStage"] == "artifact"


def test_direct_video_status_blocks_model_missing_from_non_empty_catalog(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="missing-video-model")
    record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="openai-video",
        upstream_model="missing-video-model",
        capability={
            "verificationStatus": "metadata",
            "detectedProtocol": "openai-video",
            "modelFound": False,
            "discoveredModelCount": 4,
        },
    )

    status = build_direct_video_models_status()[0]

    assert status["id"] == registry_id
    assert status["runtimeReady"] is False
    assert status["enabled"] is True
    assert "模型 ID" in status["disabledReason"]


def test_direct_video_runtime_requires_catalog_confirmation_when_strict(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("VILLAGE_CANVAS_REQUIRE_VERIFIED_DIRECT_MODELS", "1")
    registry_id = _save_one(monkeypatch, tmp_path, model_id="pending-video-model")

    status = build_direct_video_models_status()[0]

    assert status["id"] == registry_id
    assert status["catalogVerification"] == "unverified"
    assert status["runtimeReady"] is False
    assert "检测连接" in status["disabledReason"]


def test_direct_video_probe_api_persists_catalog_mismatch(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="missing-video-model")
    monkeypatch.setattr(
        model_gateway, "require_ce_direct_video_management", lambda: None
    )
    monkeypatch.setattr(
        model_gateway,
        "probe_direct_video_model",
        lambda **_kwargs: {
            "ok": True,
            "modelFound": False,
            "discoveredModelCount": 7,
            "protocol": "openai-video",
            "supportedProtocols": [],
        },
    )
    app = FastAPI()
    app.include_router(model_gateway.router)

    with TestClient(app) as client:
        response = client.post(
            "/model-gateway/direct-video-models/probe",
            json={
                "id": registry_id,
                "label": "本地极速视频",
                "modelId": "missing-video-model",
                "baseUrl": "http://127.0.0.1:9800/v1",
                "apiKey": "",
                "enabled": True,
            },
        )

    assert response.status_code == 200
    cached = get_cached_capability_for_model(
        base_url="http://127.0.0.1:9800/v1",
        upstream_model="missing-video-model",
    )
    assert cached["modelFound"] is False
    assert cached["discoveredModelCount"] == 7
    assert cached["verificationStatus"] == "catalog-mismatch"


@pytest.mark.parametrize(
    ("cached_modes", "expected_canvas_modes", "expected_reference_limits"),
    [
        (
            ["allReference"],
            ["allReference"],
            {"allReference": {"image": 9, "video": 0, "audio": 0}},
        ),
        (
            ["videoEdit"],
            ["videoEdit"],
            {"videoEdit": {"image": 0, "video": 1, "audio": 0}},
        ),
        (
            ["imageReference"],
            ["imageReference"],
            {"imageReference": {"image": 9, "video": 0, "audio": 0}},
        ),
        (
            ["allReference", "videoEdit", "allReference"],
            ["allReference", "videoEdit"],
            {
                "allReference": {"image": 9, "video": 1, "audio": 0},
                "videoEdit": {"image": 9, "video": 1, "audio": 0},
            },
        ),
    ],
)
def test_cached_reference_modes_preserve_exact_canvas_contract(
    monkeypatch,
    tmp_path,
    cached_modes: list[str],
    expected_canvas_modes: list[str],
    expected_reference_limits: dict[str, dict[str, int]],
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    base_url = "https://reference-modes.example/v1"
    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="operator-video-v1",
        capability={"modes": cached_modes},
    )

    model = DirectVideoModel(
        registry_id="dynamic-mode",
        label="Dynamic mode",
        upstream_model="operator-video-v1",
        base_url=base_url,
        api_key="test-key",
        enabled=True,
    )
    capability = model.capability
    option = direct_video_model_option(model)

    assert capability.modes == (VideoMode.REFERENCE_TO_VIDEO,)
    assert option["supportedModes"] == expected_canvas_modes
    assert option["referenceLimits"] == expected_reference_limits
    assert ModelCapability.from_dict(capability.to_dict()) == capability


def test_cached_image_reference_publishes_image_only_limits(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    base_url = "https://image-reference.example/v1"
    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="operator-video-v1",
        capability={
            "modes": ["imageReference"],
            "referenceLimits": {
                "referenceImages": 4,
                "referenceVideos": 2,
                "referenceAudios": 1,
            },
        },
    )

    model = DirectVideoModel(
        registry_id="dynamic-image-reference",
        label="Dynamic image reference",
        upstream_model="operator-video-v1",
        base_url=base_url,
        api_key="test-key",
        enabled=True,
    )

    assert model.profile.exact_canvas_modes == ("imageReference",)
    assert direct_video_model_option(model)["referenceLimits"] == {
        "imageReference": {"image": 4, "video": 0, "audio": 0}
    }


def test_cached_first_last_frame_without_limits_forms_a_valid_capability(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    base_url = "https://first-last-frame.example/v1"
    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="operator-video-v1",
        capability={"modes": ["firstLastFrame"]},
    )

    capability = DirectVideoModel(
        registry_id="dynamic-first-last-frame",
        label="Dynamic first/last frame",
        upstream_model="operator-video-v1",
        base_url=base_url,
        api_key="test-key",
        enabled=True,
    ).capability

    assert capability.modes == (VideoMode.FIRST_LAST_FRAME,)
    assert capability.reference_limits.input_images == 2


def test_partial_cached_reference_limits_preserve_profile_defaults(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    base_url = "https://partial-limits.example/v1"
    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="jimeng-seedance-2.0-fast",
        capability={"referenceLimits": {"referenceVideos": 1}},
    )

    capability = DirectVideoModel(
        registry_id="partial-limits",
        label="Partial limits",
        upstream_model="jimeng-seedance-2.0-fast",
        base_url=base_url,
        api_key="test-key",
        enabled=True,
    ).capability

    assert capability.reference_limits.to_dict() == {
        "input_images": 2,
        "reference_images": 9,
        "reference_videos": 1,
        "reference_audios": 3,
    }


def test_models_metadata_without_numeric_limits_preserves_known_profile(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    base_url = "https://relay-a.example/v1"
    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="minimax_h3",
        capability={
            "source": "models-metadata",
            "modes": ["textToVideo", "imageToVideo", "allReference", "videoEdit"],
            # 模拟旧探测器写入的伪上限；没有 Known 证据时不得覆盖精确 profile。
            "referenceLimits": {
                "inputImages": 1,
                "referenceImages": 1,
                "referenceVideos": 1,
                "referenceAudios": 0,
            },
            "supportedProtocols": [],
            "detectedProtocol": "openai-video",
        },
    )
    model = DirectVideoModel(
        registry_id="minimax-a",
        label="MiniMax A",
        upstream_model="minimax_h3",
        base_url=base_url,
        api_key="test-key",
        enabled=True,
        requested_protocol="auto",
        protocol="openai-video",
    )

    assert model.effective_protocol == "openai-video"
    assert model.profile.reference_limits.to_dict() == {
        "input_images": 2,
        "reference_images": 9,
        "reference_videos": 3,
        "reference_audios": 3,
    }
    assert model.generator_options({})["protocol"] == "openai-video"


def test_weak_minimax_adapter_hint_does_not_contradict_openai_video_runtime(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    record_capability(
        base_url="https://relay.example/v1",
        protocol="openai-video",
        upstream_model="minimax_h3",
        capability={
            "verificationStatus": "runtime-verified",
            "modelFound": True,
            "adapterFamily": "task-query",
            "adapterConfidence": 0.65,
            "adapterEvidence": {
                "family": "task-query",
                "confidence": 0.65,
                "sources": ["model-profile"],
                "resolved": False,
            },
            "detectedProtocol": "openai-video",
        },
    )
    model = DirectVideoModel(
        registry_id="weak-hint",
        label="H3 relay",
        upstream_model="minimax_h3",
        base_url="https://relay.example/v1",
        api_key="test-key",
        enabled=True,
    )

    option = direct_video_model_option(model)
    assert option["protocol"] == "openai-video"
    assert option["adapterFamily"] == "openai-video"
    assert option["adapterEvidence"]["resolved"] is True


def test_explicit_openai_video_protocol_wins_over_minimax_model_name(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    model = DirectVideoModel(
        registry_id="relay-minimax",
        label="Relay MiniMax",
        upstream_model="minimax_h3",
        base_url="https://relay.example/v1",
        api_key="test-key",
        enabled=True,
        requested_protocol="openai-video",
        protocol="minimax-video-v2",
    )

    assert model.effective_protocol == "openai-video"
    options = model.generator_options({})
    assert options["protocol"] == "openai-video"
    assert options["create_path"] == "/videos"
    assert options["query_path_template"] == "/videos/{task_id}"


def test_direct_video_generator_uses_documented_non_alias_submit_path(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    base_url = "https://relay.example/v1"
    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="seedance-2.5",
        capability={
            "verificationStatus": "catalog-confirmed",
            "probeStatus": "fresh",
            "modelFound": True,
            "discoveredModelCount": 1,
            "openapiSubmitPath": "/videos/generations",
            "openapiQueryPath": "/videos/generations/{task_id}",
            "transportContract": {
                "submit": "/videos",
                "submitObserved": True,
            },
        },
        replace_snapshot=True,
    )
    model = DirectVideoModel(
        registry_id="documented-route",
        label="Seedance",
        upstream_model="seedance-2.5",
        base_url=base_url,
        api_key="test-key",
        enabled=True,
        protocol="openai-video",
        requested_protocol="openai-video",
    )

    options = model.generator_options({})
    assert options["create_path"] == "/videos/generations"
    assert options["query_path_template"] == "/videos/generations/{task_id}"
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint=base_url,
        model="seedance-2.5",
        protocol="openai-video",
        create_path=options["create_path"],
        query_path_template=options["query_path_template"],
        cache_runtime_contract=True,
        preserve_upstream_model=True,
    )
    assert generator._submit_route_paths() == (
        "/videos/generations",
        "/videos",
        "/video/generations",
    )
    assert generator._submit_url() == f"{base_url}/videos/generations"
    assert generator._query_url("task-1") == f"{base_url}/videos/generations/task-1"


def test_dolasd_openai_model_uses_documented_public_payload_profile(
    monkeypatch, tmp_path
) -> None:
    from novelvideo.generators.video.upstream_profiles import compile_payload

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    model = DirectVideoModel(
        registry_id="dolasd-seedance",
        label="Seedance 2.5",
        upstream_model="seedance-2.5",
        base_url="https://dolasd.xyz/v1",
        api_key="test-key",
        enabled=True,
        protocol="openai-video",
        requested_protocol="openai-video",
    )

    options = model.generator_options({})
    profile = options["upstream_profile"]
    assert profile.profile_id == "dolasd_openai_video"
    payload = compile_payload(
        model_key="seedance-2.5",
        prompt="test",
        duration_seconds=30,
        aspect_ratio="9:16",
        profile=profile,
    )
    assert payload == {
        "model": "seedance-2.5",
        "prompt": "test",
        "duration": 30,
        "ratio": "9:16",
    }


def test_dolasd_seedance_uses_openai_fields_while_other_relays_keep_the_seed(
    monkeypatch, tmp_path
) -> None:
    """同一个 seedance-2.5 名字，Dola 走公开 OpenAI 报文，别家不被牵连。"""

    from novelvideo.generators.video.upstream_profiles import resolve_wire_contract

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    dolasd = resolve_wire_contract(
        "seedance-2.5",
        base_url="https://dolasd.xyz/v1",
        protocol="openai-video",
    )
    assert dolasd.profile_id == "dolasd_openai_video"
    assert dolasd.profile.duration_field == "duration"
    assert dolasd.profile.ratio_field == "ratio"
    assert dolasd.profile.ref_field == "reference_images"
    assert dolasd.profile.create_path == "/v1/videos/generations"

    huimeng = resolve_wire_contract(
        "seedance-2.5",
        base_url="https://relay.example/v1",
        protocol="openai-video",
    )
    assert huimeng.profile_id == "seedance2_native"
    assert huimeng.profile.duration_field == "duration_seconds"


def test_dolasd_generator_sends_documented_body_for_text_to_video(
    monkeypatch, tmp_path
) -> None:
    """节点请求走真实生成器时，不能再发 seconds + metadata 包装。"""

    import asyncio

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    model = DirectVideoModel(
        registry_id="dolasd-body",
        label="Seedance 2.5",
        upstream_model="seedance-2.5",
        base_url="https://dolasd.xyz/v1",
        api_key="test-key",
        enabled=True,
        protocol="openai-video",
        requested_protocol="openai-video",
    )
    options = model.generator_options({})
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://dolasd.xyz/v1",
        model="seedance-2.5",
        protocol="openai-video",
        upstream_profile=options["upstream_profile"],
    )

    assert generator._uses_documented_openai_video_payload() is True
    payload = asyncio.run(
        generator._build_documented_openai_video_payload(
            image_path="",
            last_frame_path=None,
            prompt="一只猫在草地上追蝴蝶",
            duration=30,
            ratio="9:16",
            references=(),
            log=lambda _message: None,
        )
    )
    assert payload == {
        "model": "seedance-2.5",
        "prompt": "一只猫在草地上追蝴蝶",
        "duration": 30,
        "ratio": "9:16",
    }
    assert generator._submit_url() == "https://dolasd.xyz/v1/videos/generations"


def test_dolasd_generator_without_cached_route_uses_documented_path() -> None:
    """没跑过探测、也没有缓存时，也不能回落到按名字猜的旧路径。"""

    from novelvideo.generators.video.upstream_profiles import (
        DOLASD_OPENAI_VIDEO_PROFILE,
    )

    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://dolasd.xyz/v1",
        model="seedance-2.5",
        protocol="openai-video",
        upstream_profile=DOLASD_OPENAI_VIDEO_PROFILE,
    )

    assert generator.create_path == "/videos/generations"
    assert generator._submit_url() == "https://dolasd.xyz/v1/videos/generations"


def test_model_center_reports_recorded_probe_failure_as_disabled_reason(
    monkeypatch, tmp_path
) -> None:
    """目录能读但 Key 被拒时，模型中心要说真实原因，而不是「协议没识别」。"""

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    saved = save_direct_video_models(
        [
            {
                "label": "Seedance 2.5",
                "modelId": "seedance-2.5",
                "baseUrl": "https://dolasd.xyz/v1",
                "apiKey": "direct-test-key",
                "protocol": "openai-video",
                "enabled": True,
            }
        ]
    )
    record_capability(
        base_url="https://dolasd.xyz/v1",
        protocol="openai-video",
        upstream_model="seedance-2.5",
        capability={
            "verificationStatus": "probe-failed",
            "probeStatus": "failed",
            "modelFound": True,
            "discoveredModelCount": 2,
            "detectedProtocol": "openai-video",
            "lastFailure": "dolasd 的密钥信息接口拒绝了当前 Key（HTTP 401）。",
        },
        replace_snapshot=True,
    )

    status = [
        item
        for item in build_direct_video_models_status()
        if item["id"] == saved[0]["id"]
    ][0]

    assert status["runtimeReady"] is False
    assert status["disabledReason"] == (
        "实时连接检测失败：dolasd 的密钥信息接口拒绝了当前 Key（HTTP 401）。"
    )


def test_models_metadata_explicit_numeric_limit_overrides_profile(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    base_url = "https://relay-b.example/v1"
    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="minimax_h3",
        capability={
            "source": "models-metadata",
            "referenceLimits": {"referenceImages": 4},
            "referenceLimitsKnown": ["referenceImages"],
        },
    )
    profile = DirectVideoModel(
        registry_id="minimax-b",
        label="MiniMax B",
        upstream_model="minimax_h3",
        base_url=base_url,
        api_key="test-key",
        enabled=True,
    ).profile

    assert profile.reference_limits.reference_images == 4
    assert profile.reference_limits.reference_videos == 3


def test_probe_marks_only_upstream_numeric_reference_limits_as_known() -> None:
    capability = infer_video_capability(
        {
            "id": "multi-reference-video",
            "tags": ["multi-reference", "text-to-video"],
            "max_reference_images": 6,
        }
    )

    assert capability["referenceLimits"]["referenceImages"] == 6
    assert capability["referenceLimitsKnown"] == ["referenceImages"]

    explicitly_unsupported = infer_video_capability(
        {
            "id": "single-frame-video",
            "input_modalities": ["text", "image"],
            "max_reference_images": 0,
        }
    )
    assert explicitly_unsupported["referenceLimits"]["referenceImages"] == 0
    assert explicitly_unsupported["referenceLimitsKnown"] == ["referenceImages"]


def test_probe_reads_explicit_fixed_duration_from_model_description() -> None:
    capability = infer_video_capability(
        {
            "id": "S-2.5db-线路三",
            "description": "固定 30 秒视频生成模型；720p",
            "tags": ["video"],
        }
    )

    assert capability["durationRange"] == [30, 30]
    assert "durationRange" in capability["declaredCapabilities"]


def test_probe_does_not_treat_bare_maximum_duration_as_fixed() -> None:
    capability = infer_video_capability(
        {
            "id": "vendor-video-vnext",
            "description": "视频生成，最多 30 秒",
        }
    )

    assert "durationRange" not in capability


def test_probe_preserves_upstream_custom_video_dimensions_and_ratios() -> None:
    capability = infer_video_capability(
        {
            "id": "vendor-video-vnext",
            "supported_sizes": ["1440p", "2160p"],
            "supported_aspect_ratios": ["2.39:1", "16:9"],
            "supports_any_size": True,
            "supports_arbitrary_aspect_ratio": True,
        }
    )

    assert capability["resolutionOptions"] == ["1440p", "2160p"]
    assert capability["aspectRatios"] == ["2.39:1", "16:9"]
    assert capability["supportsCustomResolution"] is True
    assert capability["supportsCustomAspectRatio"] is True


def test_probe_normalizes_unicode_dimension_separator() -> None:
    capability = infer_video_capability(
        {
            "id": "vendor-video-vnext",
            "supported_sizes": ["2048×1376"],
        }
    )

    assert capability["sizeSlots"] == ["2048x1376"]


def test_probe_preserves_explicit_empty_size_contract() -> None:
    capability = infer_video_capability(
        {
            "id": "size-less-video",
            "capabilities": {"sizeOptions": []},
        }
    )

    assert capability["sizeSlots"] == []
    assert "sizeSlots" in capability["declaredCapabilities"]


def test_explicit_empty_size_contract_does_not_restore_profile_slots(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="gemini-veo-sized")
    record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="openai-video",
        upstream_model="gemini-veo-sized",
        capability={
            "verificationStatus": "metadata",
            "modelFound": True,
            "discoveredModelCount": 1,
            "declaredCapabilities": ["sizeSlots"],
            "sizeSlots": [],
        },
    )

    model = resolve_direct_video_model(f"direct_{registry_id}")
    assert model is not None
    option = direct_video_model_option(model)

    assert model.profile.size_slots == ()
    assert option["sizeOptions"] == []


def test_direct_video_profile_and_payload_use_only_the_matching_channel_capability(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    record_capability(
        base_url="https://sized.example/v1",
        protocol="openai-video",
        upstream_model="operator-video-v1",
        capability={
            "sizeSlots": ["992x432", "640x640"],
            "modes": ["textToVideo"],
            "durationRange": [4, 8],
            "nativeAudio": "unsupported",
            "declaredCapabilities": [
                "sizeSlots",
                "resolutionOptions",
                "aspectRatios",
            ],
        },
    )
    sized = DirectVideoModel(
        registry_id="sized",
        label="Sized",
        upstream_model="operator-video-v1",
        base_url="https://sized.example/v1",
        api_key="test-key",
        enabled=True,
    )
    other = DirectVideoModel(
        registry_id="other",
        label="Other",
        upstream_model="operator-video-v1",
        base_url="https://other.example/v1",
        api_key="test-key",
        enabled=True,
    )

    assert sized.profile.size_slots == ("992x432", "640x640")
    assert other.profile.size_slots == ()

    generator = NewApiVideoGenerator(
        **sized.generator_options({"generate_audio": True})
    )
    payload = generator._apply_capability_contract(
        {
            "model": "operator-video-v1",
            "prompt": "fixture",
            "seconds": "99",
            "metadata": {
                "ratio": "1:1",
                "resolution": "1080p",
                "generate_audio": True,
            },
        }
    )
    assert payload["size"] == "640x640"
    assert payload["seconds"] == "8"
    assert payload["metadata"] == {}

    landscape_payload = generator._apply_capability_contract(
        {
            "model": "operator-video-v1",
            "prompt": "fixture",
            "aspect_ratio": "16:9",
        }
    )
    assert landscape_payload["size"] == "992x432"
    assert "aspect_ratio" not in landscape_payload


def test_direct_video_probe_rejects_credential_bearing_url() -> None:
    with pytest.raises(ValueError, match="credentials"):
        probe_direct_video_model(
            upstream_model="seedance-direct-fast",
            base_url="https://secret@example.test/v1",
            api_key="direct-test-key",
        )


def test_direct_video_probe_api_reuses_saved_key_without_exposing_it(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="probe-video-v1")
    monkeypatch.setattr(
        model_gateway, "require_ce_direct_video_management", lambda: None
    )
    captured: dict[str, str] = {}

    def fake_probe(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "modelFound": False,
            "discoveredModelCount": 1,
            "protocol": "openai-video",
        }

    monkeypatch.setattr(model_gateway, "probe_direct_video_model", fake_probe)
    app = FastAPI()
    app.include_router(model_gateway.router)

    with TestClient(app) as client:
        response = client.post(
            "/model-gateway/direct-video-models/probe",
            json={
                "id": registry_id,
                "label": "本地极速视频",
                "modelId": "probe-video-v1",
                "baseUrl": "http://127.0.0.1:9800/v1",
                "apiKey": "",
                "enabled": True,
            },
        )

    assert response.status_code == 200
    assert response.json()["data"]["modelFound"] is False
    assert captured["api_key"] == "direct-test-key"
    assert "direct-test-key" not in response.text


def test_direct_video_probe_failure_replaces_previous_success(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="probe-video-v1")
    base_url = "http://127.0.0.1:9800/v1"
    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="probe-video-v1",
        capability={
            "verificationStatus": "runtime-verified",
            "modelFound": True,
            "discoveredModelCount": 2,
            "resolutionOptions": ["1080p"],
            "aspectRatios": ["16:9"],
        },
    )
    monkeypatch.setattr(
        model_gateway, "require_ce_direct_video_management", lambda: None
    )
    monkeypatch.setattr(
        model_gateway,
        "probe_direct_video_model",
        lambda **_kwargs: {
            "ok": False,
            "protocol": "openai-video",
            "errorCode": "network_error",
            "error": "上游连接失败",
        },
    )
    app = FastAPI()
    app.include_router(model_gateway.router)

    with TestClient(app) as client:
        response = client.post(
            "/model-gateway/direct-video-models/probe",
            json={
                "id": registry_id,
                "label": "本地视频模型",
                "modelId": "probe-video-v1",
                "baseUrl": base_url,
                "apiKey": "",
                "protocol": "openai-video",
                "enabled": True,
            },
        )

    assert response.status_code == 200
    current = get_cached_capability_for_model(
        base_url=base_url,
        upstream_model="probe-video-v1",
    )
    assert current["probeStatus"] == "failed"
    assert current["verificationStatus"] == "probe-failed"
    assert current["modelFound"] is False
    assert current["lastFailure"] == "上游连接失败"
    assert "resolutionOptions" not in current
    model = resolve_direct_video_model(f"direct_{registry_id}")
    assert model is not None
    assert model.runtime_ready is False
    option = direct_video_model_option(model)
    assert option["resolutionOptions"] == []
    assert option["aspectRatioOptions"] == []


def test_video_auth_rejection_preserves_catalog_truth_and_blocks_runtime(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="probe-video-auth-v1")
    base_url = "http://127.0.0.1:9800/v1"
    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="probe-video-auth-v1",
        capability={
            "verificationStatus": "runtime-verified",
            "probeStatus": "fresh",
            "modelFound": True,
            "discoveredModelCount": 1,
            "modes": ["textToVideo"],
        },
    )
    monkeypatch.setattr(
        model_gateway, "require_ce_direct_video_management", lambda: None
    )
    monkeypatch.setattr(
        model_gateway,
        "probe_direct_video_model",
        lambda **_kwargs: {
            "ok": False,
            "modelFound": True,
            "discoveredModelCount": 1,
            "protocol": "openai-video",
            "errorCode": "VIDEO_AUTH_REJECTED",
            "httpStatus": 401,
            "error": "模型目录可读，但视频任务接口拒绝了当前 Key（HTTP 401）。",
        },
    )
    app = FastAPI()
    app.include_router(model_gateway.router)

    with TestClient(app) as client:
        response = client.post(
            "/model-gateway/direct-video-models/probe",
            json={
                "id": registry_id,
                "label": "本地视频模型",
                "modelId": "probe-video-auth-v1",
                "baseUrl": base_url,
                "apiKey": "",
                "protocol": "openai-video",
                "enabled": True,
            },
        )

    assert response.status_code == 200
    assert response.json()["data"]["modelFound"] is True
    assert response.json()["data"]["ok"] is False
    current = get_cached_capability_for_model(
        base_url=base_url,
        upstream_model="probe-video-auth-v1",
    )
    assert current["probeStatus"] == "failed"
    assert current["modelFound"] is True
    assert current["discoveredModelCount"] == 1
    model = resolve_direct_video_model(f"direct_{registry_id}")
    assert model is not None
    assert model.runtime_ready is False


def test_direct_video_binding_change_invalidates_cached_success(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="rotating-key-model")
    base_url = "http://127.0.0.1:9800/v1"
    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="rotating-key-model",
        capability={
            "verificationStatus": "runtime-verified",
            "modelFound": True,
            "discoveredModelCount": 1,
            "resolutionOptions": ["1080p"],
            "aspectRatios": ["16:9"],
        },
    )

    save_direct_video_models(
        [
            {
                "id": registry_id,
                "label": "轮换凭据的视频模型",
                "modelId": "rotating-key-model",
                "baseUrl": base_url,
                "apiKey": "rotated-test-key",
                "protocol": "openai-video",
                "enabled": True,
            }
        ]
    )

    current = get_cached_capability_for_model(
        base_url=base_url,
        upstream_model="rotating-key-model",
    )
    assert current["probeStatus"] == "stale"
    assert current["verificationStatus"] == "unverified"
    assert "resolutionOptions" not in current
    model = resolve_direct_video_model(f"direct_{registry_id}")
    assert model is not None
    assert model.runtime_ready is False
    option = direct_video_model_option(model)
    assert option["resolutionOptions"] == []
    assert option["aspectRatioOptions"] == []


def test_direct_video_probe_api_does_not_reuse_key_for_another_endpoint(
    monkeypatch, tmp_path
) -> None:
    registry_id = _save_one(monkeypatch, tmp_path, model_id="probe-video-v1")
    monkeypatch.setattr(
        model_gateway, "require_ce_direct_video_management", lambda: None
    )
    monkeypatch.setattr(
        model_gateway,
        "probe_direct_video_model",
        lambda **_kwargs: pytest.fail("probe must not run with a stale key"),
    )
    app = FastAPI()
    app.include_router(model_gateway.router)

    with TestClient(app) as client:
        response = client.post(
            "/model-gateway/direct-video-models/probe",
            json={
                "id": registry_id,
                "label": "本地极速视频",
                "modelId": "probe-video-v1",
                "baseUrl": "https://second.example/v1",
                "apiKey": "",
                "enabled": True,
            },
        )

    assert response.status_code == 400
    assert "apiKey is required" in response.text


def test_direct_video_discovery_keeps_only_capability_metadata(monkeypatch) -> None:
    import httpx

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, endpoint: str, *, headers: dict[str, str]):
            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={
                    "data": [
                        {
                            "id": "video-pro",
                            "tags": ["text-to-video"],
                            "resolutionOptions": ["720p"],
                            "private_provider_id": 19,
                        }
                    ]
                },
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = discover_direct_video_models(
        base_url="https://video.example/v1",
        api_key="video-secret",
    )

    assert result["ok"] is True
    assert result["models"] == [
        {
            "id": "video-pro",
            "metadata": {
                "tags": ["text-to-video"],
                "resolutionOptions": ["720p"],
            },
        }
    ]
    assert "video-secret" not in str(result)


def test_direct_video_discovery_api_does_not_save_or_expose_key(monkeypatch) -> None:
    monkeypatch.setattr(
        model_gateway, "require_ce_direct_video_management", lambda: None
    )
    captured: dict[str, str] = {}

    def fake_discover(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "models": [{"id": "video-pro", "metadata": {}}],
            "discoveredModelCount": 1,
            "protocol": "openai-video",
        }

    monkeypatch.setattr(model_gateway, "discover_direct_video_models", fake_discover)
    app = FastAPI()
    app.include_router(model_gateway.router)

    with TestClient(app) as client:
        response = client.post(
            "/model-gateway/direct-video-models/discover",
            json={
                "baseUrl": "https://video.example/v1",
                "apiKey": "video-secret",
                "protocol": "auto",
            },
        )

    assert response.status_code == 200
    assert response.json()["data"]["models"][0]["id"] == "video-pro"
    assert captured["api_key"] == "video-secret"
    assert "video-secret" not in response.text


def test_direct_sd25_m_720p_sends_requested_30_seconds_verbatim(
    monkeypatch, tmp_path
) -> None:
    """回归：选中 30 秒时，提交体必须原样是 30（30→15 静默改写事故）。"""

    import asyncio

    from novelvideo.generators import video_generator as video_generator_module

    registry_id = _save_one(monkeypatch, tmp_path, model_id="sd-2.5-M-720P-v1")
    generator = create_video_generator(f"direct_{registry_id}")
    captured: list[dict] = []

    async def fake_post_json(self, url, payload, *, idempotency_key=""):
        captured.append(dict(payload))
        raise RuntimeError("captured")

    async def fake_reserve(*args, **kwargs):
        return "test-reservation"

    async def fake_refund(*args, **kwargs):
        return None

    monkeypatch.setattr(NewApiVideoGenerator, "_post_json", fake_post_json)
    monkeypatch.setattr(
        video_generator_module, "_reserve_video_model_call", fake_reserve
    )
    monkeypatch.setattr(
        video_generator_module, "_refund_video_model_call", fake_refund
    )
    if hasattr(video_generator_module, "_confirm_video_model_call"):
        monkeypatch.setattr(
            video_generator_module, "_confirm_video_model_call", fake_refund
        )

    asyncio.run(
        generator.generate(
            None,
            prompt="30 秒回归",
            output_path=str(tmp_path / "out.mp4"),
            aspect_ratio="16:9",
            duration=30.0,
            resolution="720p",
        )
    )

    assert captured, "提交体没有被捕获——说明在提交前就被拦截了"
    assert captured[0]["duration"] == 30


def test_autodl_workflow_resolution_labels_canonicalize_to_height_form():
    """AutoDL 工作流 schema 的分辨率是「方向 + 像素」组合标签。

    2026-09-15 真机事故：``minimax_h3_z0901``（autodl.art 的 ComfyUI 工作流
    「H3文生视频（高质量创意直出）」）在模型中心检测通过、保存后却显示
    「模型能力合同无效，请重新检测该模型；ValueError」，模型被停用、节点用不了。

    链路：工作流 schema 给出 ``768p竖(768*1344)`` 这类标签 → 去后缀的正则当时写成
    三选一，只认「只有方向」或「只有括号」→ 组合标签原样漏进 ``ModelCapability``
    → ``__post_init__`` 抛 "resolution entries must use the '<height>p' form"。
    """

    from novelvideo.generators.video.direct_models import (
        _canonical_capability_resolutions,
    )

    autodl_labels = (
        "480p竖(480*864)",
        "480p横(864*480)",
        "768p竖(768*1344)",
        "768p横(1344*768)",
        "1088p竖(1088*1920)",
        "1088p横(1920*1088)",
        "1440p竖(1440*2560)",
        "1440p横(2560*1440)",
    )

    canonical = _canonical_capability_resolutions(autodl_labels)

    assert canonical == ("480p", "768p", "1088p", "1440p")
    # 旧写法留下的既有形态必须继续成立。
    assert _canonical_capability_resolutions(
        ("480p横", "480p竖", "480p(1:1)")
    ) == ("480p",)


def test_workflow_resolution_labels_do_not_break_the_capability_contract():
    """合同构建不得因为「方向 + 像素」标签抛错，否则模型被判 invalid-contract。

    直接复用 ``ModelCapability.__post_init__`` 用的那条校验：它一旦不通过就会抛
    "resolution entries must use the '<height>p' form"，真机上表现为模型被停用。
    """

    from novelvideo.generators.video.capabilities import _RESOLUTION
    from novelvideo.generators.video.direct_models import (
        _canonical_capability_resolutions,
    )

    canonical = _canonical_capability_resolutions(
        ("480p竖(480*864)", "1440p横(2560*1440)", "1088p竖(1088*1920)")
    )

    assert canonical == ("480p", "1440p", "1088p")
    for entry in canonical:
        assert _RESOLUTION.fullmatch(entry), f"{entry} 会被 ModelCapability 拒绝"


def test_alternate_resolution_spellings_canonicalize_before_the_contract_check():
    """同一分辨率的其它写法也要先归一，否则模型被判 invalid-contract。

    上游 schema 不只有 ``768p竖(768*1344)`` 一种写法：还有带空格的
    ``480p 竖``、全角乘号的 ``720×1280``、以及 ``720 x 1280``。这些标签一旦原样
    漏进 ``ModelCapability``，模型就在模型中心显示「能力合同无效」并被停用。
    """

    from novelvideo.generators.video.capabilities import _RESOLUTION
    from novelvideo.generators.video.direct_models import (
        _canonical_capability_resolutions,
    )

    canonical = _canonical_capability_resolutions(
        ("480p 竖", "720×1280", "720 x 1280", "1080×1920", "2K", "1024x1024")
    )

    assert canonical == ("480p", "720x1280", "1080x1920", "2K", "1024x1024")
    for entry in canonical:
        assert _RESOLUTION.fullmatch(entry), f"{entry} 会被 ModelCapability 拒绝"


def test_plugin_owned_entries_are_recognized_from_catalog_metadata() -> None:
    from novelvideo.generators.video import direct_video_probe as probe_module

    assert probe_module._entry_is_plugin_owned({"owned_by": "task plugin"}) is True
    assert probe_module._entry_is_plugin_owned({"ownedBy": "Task-Plugin"}) is True
    assert probe_module._entry_is_plugin_owned({"owned_by": "openai"}) is False
    assert probe_module._entry_is_plugin_owned({}) is False


def test_native_minimax_v2_needs_structured_route_evidence(monkeypatch) -> None:
    """HTML from the SPA catch-all is not evidence that the native family exists."""

    from novelvideo.generators.video import direct_video_probe as probe_module

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, endpoint, *, headers):
            import httpx

            return httpx.Response(
                200,
                text="<!doctype html><html></html>",
                headers={"content-type": "text/html"},
                request=httpx.Request("GET", endpoint),
            )

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)

    assert (
        probe_module._native_minimax_v2_available(
            base_url="https://dmc.cc/v1",
            api_key="direct-test-key",
            upstream_model="MiniMax-H3",
        )
        is False
    )


def test_native_minimax_v2_accepts_an_unknown_task_error_envelope(monkeypatch) -> None:
    from novelvideo.generators.video import direct_video_probe as probe_module

    seen: list[str] = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, endpoint, *, headers):
            import httpx

            seen.append(endpoint)
            return httpx.Response(
                404,
                request=httpx.Request("GET", endpoint),
                json={
                    "error": {
                        "http_code": "404",
                        "message": "Task not found",
                        "type": "bad_request_error",
                    },
                    "type": "error",
                },
            )

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)

    assert (
        probe_module._native_minimax_v2_available(
            base_url="https://dmc.cc/v1",
            api_key="direct-test-key",
            upstream_model="MiniMax-H3",
        )
        is True
    )
    assert seen == [
        f"https://dmc.cc/v2/query/video_generation/{seen[0].rsplit('/', 1)[-1]}"
    ]


def test_direct_video_probe_resolves_plugin_backed_h3_to_native_v2(monkeypatch) -> None:
    """dmc.cc 形态：目录由 task plugin 提供，中转 /v1 无渠道，原生 /v2 可用。"""

    seen: list[str] = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, endpoint, *, headers):
            import httpx

            seen.append(endpoint)
            if endpoint.endswith("/v1/models"):
                return httpx.Response(
                    200,
                    request=httpx.Request("GET", endpoint),
                    json={
                        "data": [
                            {
                                "id": "MiniMax-H3",
                                "object": "model",
                                "owned_by": "task plugin",
                                "supported_endpoint_types": ["openai"],
                            }
                        ]
                    },
                )
            if "/v2/query/video_generation/" in endpoint:
                return httpx.Response(
                    404,
                    request=httpx.Request("GET", endpoint),
                    json={
                        "error": {"message": "Task not found", "type": "bad_request_error"}
                    },
                )
            return httpx.Response(
                404,
                request=httpx.Request("GET", endpoint),
                json={"error": {"message": "not found"}},
            )

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = probe_direct_video_model(
        upstream_model="MiniMax-H3",
        base_url="https://dmc.cc/v1",
        api_key="direct-test-key",
    )

    assert result["protocol"] == "minimax-video-v2"
    assert result["supportedProtocols"] == ["minimax-video-v2"]
    assert any("/v2/query/video_generation/probe-" in url for url in seen)
