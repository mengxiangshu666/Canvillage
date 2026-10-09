from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from novelvideo import config
from novelvideo.generators.video.direct_models import (
    DirectVideoModel,
    direct_video_model_option,
)
from novelvideo.generators.video.direct_video_capability_cache import (
    get_cached_capability,
    record_capability,
)
from novelvideo.generators.video.direct_video_probe import (
    _safe_model_metadata,
    infer_video_capability,
)
from novelvideo.generators.video.generic_video_adapter import (
    GenericVideoAdapterGenerator,
)
from novelvideo.generators.video.video_capability_envelope import (
    build_video_capability_envelope,
    compile_video_provider_parameters,
)
from novelvideo.generators.video.video_openapi_discovery import _safe_operations
from novelvideo.generators.video_generator import NewApiVideoGenerator
from novelvideo.model_gateway_settings import save_direct_video_models


def test_infer_video_capability_keeps_provider_schema_and_opaque_fields() -> None:
    result = infer_video_capability(
        {
            "id": "vendor-video-v2",
            "capabilities": {
                "parameters": {
                    "generateMode": {
                        "providerKey": "generate_mode",
                        "type": "string",
                        "enum": ["fast", "quality"],
                        "default": "fast",
                    },
                    "output_format": {
                        "type": "string",
                        "enum": ["mp4", "webm"],
                    },
                    "motion_strength": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "step": 0.1,
                    },
                },
                "supportsCameraControl": True,
                "customSizeOptions": ["1440x2560", "2048x1376"],
            },
        }
    )

    assert result["capabilityEnvelopeVersion"] == 1
    parameters = {item["key"]: item for item in result["parameters"]}
    assert parameters["mode"]["providerKey"] == "generate_mode"
    assert parameters["outputFormat"]["enum"] == ["mp4", "webm"]
    assert parameters["motion_strength"]["type"] == "number"
    assert result["providerMapping"]["mode"] == "generate_mode"
    opaque = {item["key"]: item["value"] for item in result["opaque"]}
    assert opaque["supportsCameraControl"] is True
    assert opaque["customSizeOptions"] == ["1440x2560", "2048x1376"]


def test_infer_video_capability_preserves_keyframe_capability_fields_without_exposing_keys() -> None:
    metadata = _safe_model_metadata(
        {
            "id": "keyframe-video-v1",
            "keyframeDuration": {"type": "number", "minimum": 1, "maximum": 12},
            "apiKey": "secret-value",
            "requestToken": "secret-token",
        }
    )

    assert metadata["keyframeDuration"]["type"] == "number"
    assert "apiKey" not in metadata
    assert "requestToken" not in metadata


def test_infer_video_capability_reads_nested_json_schema_properties() -> None:
    result = infer_video_capability(
        {
            "id": "schema-video-v1",
            "inputSchema": {
                "type": "object",
                "required": ["camera_control"],
                "properties": {
                    "camera_control": {
                        "type": "boolean",
                        "title": "Camera control",
                    },
                    "seed": {
                        "type": "integer",
                        "minimum": 0,
                    },
                },
            },
            "providerMapping": {"camera_control": "cameraControl"},
        }
    )

    parameters = {item["key"]: item for item in result["parameters"]}
    assert parameters["camera_control"]["required"] is True
    assert parameters["camera_control"]["providerKey"] == "cameraControl"
    assert parameters["seed"]["type"] == "integer"
    assert result["providerMapping"]["camera_control"] == "cameraControl"


def test_provider_mapping_compiles_canonical_and_unknown_values() -> None:
    assert compile_video_provider_parameters(
        {
            "mode": "quality",
            "outputFormat": "webm",
            "motion_strength": 0.7,
            "api_key": "secret-value",
        },
        {"mode": "generate_mode", "outputFormat": "output_format"},
    ) == {
        "generate_mode": "quality",
        "output_format": "webm",
        "motion_strength": 0.7,
    }


def test_provider_mapping_exposes_first_and_last_frame_slots() -> None:
    result = build_video_capability_envelope(
        {
            "providerMapping": {
                "first_frame": "start_image",
                "lastFrame": "end_image",
            },
            "first_frame": {"type": "image"},
            "lastFrame": {"type": "image"},
        }
    )

    parameters = {item["key"]: item for item in result["parameters"]}
    assert result["providerMapping"] == {
        "firstFrame": "start_image",
        "lastFrame": "end_image",
    }
    assert parameters["firstFrame"]["providerKey"] == "start_image"
    assert parameters["lastFrame"]["providerKey"] == "end_image"
    assert parameters["firstFrame"]["advanced"] is False
    assert parameters["lastFrame"]["advanced"] is False


def test_capability_cache_persists_envelope(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    record_capability(
        base_url="https://provider.example/v1",
        protocol="prediction",
        upstream_model="vendor-video-v2",
        capability={
            "capabilityEnvelopeVersion": 1,
            "parameters": [{"key": "outputFormat", "providerKey": "output_format"}],
            "providerMapping": {"outputFormat": "output_format"},
            "opaque": [{"key": "supportsCameraControl", "value": True}],
        },
    )
    cached = get_cached_capability(
        base_url="https://provider.example/v1",
        protocol="prediction",
        upstream_model="vendor-video-v2",
    )
    assert cached["parameters"][0]["providerKey"] == "output_format"
    assert cached["providerMapping"] == {"outputFormat": "output_format"}
    assert cached["opaque"][0]["value"] is True


def test_direct_model_option_exposes_cached_envelope(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    saved = save_direct_video_models(
        [
            {
                "id": "direct-envelope",
                "label": "Envelope fixture",
                "modelId": "vendor-video-v2",
                "baseUrl": "https://provider.example/v1",
                "apiKey": "fixture-key",
                "enabled": True,
                "protocol": "auto",
            }
        ]
    )
    model = DirectVideoModel(
        registry_id=saved[0]["id"],
        label="Envelope fixture",
        upstream_model="vendor-video-v2",
        base_url="https://provider.example/v1",
        api_key="fixture-key",
        enabled=True,
        requested_protocol="auto",
        protocol="auto",
    )
    record_capability(
        base_url=model.base_url,
        protocol="openai-video",
        upstream_model=model.upstream_model,
        capability={
            "modelFound": True,
            "discoveredModelCount": 1,
            "adapterFamily": "prediction",
            "adapterConfidence": 1.0,
            "modes": ["textToVideo"],
            "parameters": [{"key": "outputFormat", "providerKey": "output_format"}],
            "providerMapping": {"outputFormat": "output_format"},
            "opaque": [{"key": "supportsCameraControl", "value": True}],
        },
    )
    option = direct_video_model_option(model)
    assert option["parameters"][0]["key"] == "outputFormat"
    assert option["providerMapping"] == {"outputFormat": "output_format"}
    assert option["opaque"][0]["key"] == "supportsCameraControl"


@pytest.mark.asyncio
async def test_generic_adapter_sends_mapped_advanced_parameters() -> None:
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="vendor-video-v2",
        adapter_family="prediction",
        parameter_values={"mode": "quality", "outputFormat": "webm", "seed": 42},
        provider_mapping={"mode": "generate_mode", "outputFormat": "output_format"},
    )
    payload = await generator._build_payload(
        prompt="fixture",
        image_path=None,
        references=(),
        last_frame_path=None,
        duration=5,
        aspect_ratio="16:9",
        resolution="720p",
        generate_audio=False,
        kwargs={},
    )
    assert payload["input"]["generate_mode"] == "quality"
    assert payload["input"]["output_format"] == "webm"
    assert payload["input"]["seed"] == 42


@pytest.mark.asyncio
async def test_workflow_adapter_keeps_advanced_parameters_with_native_workflow() -> None:
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="workflow-v1",
        adapter_family="workflow",
        parameter_values={"outputFormat": "webm"},
        provider_mapping={"outputFormat": "output_format"},
    )
    payload = await generator._build_payload(
        prompt="fixture",
        image_path=None,
        references=(),
        last_frame_path=None,
        duration=5,
        aspect_ratio="16:9",
        resolution="720p",
        generate_audio=False,
        kwargs={"workflow": {"nodes": [{"id": 1}], "output_format": "mp4"}},
    )
    assert payload["prompt"] == {"nodes": [{"id": 1}], "output_format": "mp4"}

    payload = await generator._build_payload(
        prompt="fixture",
        image_path=None,
        references=(),
        last_frame_path=None,
        duration=5,
        aspect_ratio="16:9",
        resolution="720p",
        generate_audio=False,
        kwargs={"workflow": {"nodes": [{"id": 1}]}},
    )
    assert payload["prompt"]["output_format"] == "webm"


def test_autodl_payload_sends_mapped_advanced_parameters() -> None:
    payload = GenericVideoAdapterGenerator._build_autodl_payload(
        prompt="fixture",
        images=[],
        audios=[],
        duration=5,
        aspect_ratio="9:16",
        resolution="768p竖",
        kwargs={},
        parameter_values={"outputFormat": "webm", "motion_strength": 0.4},
        provider_mapping={"outputFormat": "output_format"},
    )
    assert payload["output_format"] == "webm"
    assert payload["motion_strength"] == 0.4
    assert payload["resolution"] == "768p竖"


def test_newapi_direct_generator_keeps_advanced_parameters_in_legacy_metadata() -> None:
    generator = NewApiVideoGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example/v1",
        model="vendor-video-v2",
        parameter_values={"outputFormat": "webm", "seed": 42},
        provider_mapping={"outputFormat": "output_format"},
    )
    payload = {
        "model": "vendor-video-v2",
        "prompt": "fixture",
        "seconds": "5",
        "metadata": {"resolution": "720p"},
    }

    generator._apply_provider_parameters(payload, metadata=payload["metadata"])

    assert payload["metadata"]["output_format"] == "webm"
    assert payload["metadata"]["seed"] == 42


def test_newapi_video_v1_direct_generator_keeps_advanced_parameters_at_top_level() -> None:
    generator = NewApiVideoGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example/v1",
        model="vendor-video-v2",
        parameter_values={"outputFormat": "webm", "seed": 42},
        provider_mapping={"outputFormat": "output_format"},
    )
    payload = {
        "version": "video.v1",
        "model": "vendor-video-v2",
        "prompt": "fixture",
        "duration_seconds": 5,
    }

    generator._apply_provider_parameters(payload)

    assert payload["output_format"] == "webm"
    assert payload["seed"] == 42


def test_openapi_operation_schema_becomes_lossless_model_parameters() -> None:
    result = infer_video_capability(
        {
            "id": "schema-video-v2",
            "openapiOperations": [
                {
                    "path": "/videos",
                    "method": "post",
                    "parameters": [
                        {
                            "key": "seconds",
                            "type": "integer",
                            "minimum": 4,
                            "maximum": 30,
                            "required": True,
                        },
                        {
                            "key": "ratio",
                            "type": "string",
                            "enum": ["16:9", "9:16"],
                        },
                    ],
                }
            ],
        }
    )
    parameters = {item["key"]: item for item in result["parameters"]}
    assert parameters["duration"]["providerKey"] == "seconds"
    assert parameters["duration"]["required"] is True
    assert parameters["aspectRatio"]["providerKey"] == "ratio"
    assert result["providerMapping"]["duration"] == "seconds"
    assert result["providerMapping"]["aspectRatio"] == "ratio"


def test_openapi_discovery_extracts_json_body_schema_without_examples() -> None:
    operations = _safe_operations(
        {
            "/videos": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/VideoRequest"
                                }
                            }
                        }
                    }
                }
            }
        },
        {
            "components": {
                "schemas": {
                    "VideoRequest": {
                        "type": "object",
                        "required": ["seconds"],
                        "properties": {
                            "seconds": {
                                "type": "integer",
                                "minimum": 4,
                                "maximum": 30,
                                "example": 8,
                            },
                            "api_key": {"type": "string"},
                        },
                    }
                }
            }
        },
    )
    assert operations[0]["parameters"] == [
        {
            "key": "seconds",
            "type": "integer",
            "minimum": 4,
            "maximum": 30,
            "required": True,
        }
    ]


def test_openapi_discovery_ignores_admin_video_lists_and_keeps_submit_parameters() -> None:
    operations = _safe_operations(
        {
            "/v1/videos": {
                "post": {
                    "operationId": "createVideo",
                    "tags": ["Video generation"],
                    "parameters": [
                        {
                            "name": "aspect_ratio",
                            "in": "query",
                            "schema": {"type": "string", "enum": ["16:9", "9:16"]},
                        }
                    ],
                },
                "get": {
                    "operationId": "listVideos",
                    "parameters": [
                        {
                            "name": "page",
                            "in": "query",
                            "schema": {"type": "integer", "default": 1},
                        }
                    ],
                },
            },
            "/api/admin/videos/{task_id}": {
                "get": {
                    "operationId": "adminVideoDetail",
                    "parameters": [
                        {
                            "name": "include_deleted",
                            "in": "query",
                            "schema": {"type": "boolean"},
                        }
                    ],
                }
            },
            "/api/admin/accounts/{account_id}": {
                "post": {"operationId": "updateAccount"}
            },
            "/v1/models": {"get": {"operationId": "listModels"}},
        }
    )

    assert [item["path"] for item in operations] == ["/v1/videos"]
    assert operations[0]["parameters"] == [
        {"type": "string", "enum": ["16:9", "9:16"], "key": "aspect_ratio"}
    ]


def test_newapi_common_controls_follow_provider_mapping_without_duplicate_aliases() -> None:
    generator = NewApiVideoGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example/v1",
        model="vendor-video-v2",
        parameter_values={},
        provider_mapping={
            "duration": "seconds",
            "aspectRatio": "ratio",
            "resolution": "video_size",
        },
    )
    payload = {
        "model": "vendor-video-v2",
        "prompt": "fixture",
        "seconds": "5",
        "metadata": {"duration": 5, "ratio": "16:9", "resolution": "720p"},
    }

    generator._apply_provider_parameters(payload, metadata=payload["metadata"])

    assert payload["seconds"] == "5"
    assert payload["metadata"]["ratio"] == "16:9"
    assert payload["metadata"]["video_size"] == "720p"
    assert "duration" not in payload["metadata"]
    assert "resolution" not in payload["metadata"]


def test_generic_adapter_maps_common_controls_to_provider_names() -> None:
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="vendor-video-v2",
        adapter_family="prediction",
        provider_mapping={
            "duration": "seconds",
            "aspectRatio": "ratio",
            "resolution": "video_size",
        },
    )
    payload = asyncio.run(
        generator._build_payload(
            prompt="fixture",
            image_path=None,
            references=(),
            last_frame_path=None,
            duration=12,
            aspect_ratio="9:16",
            resolution="1080p",
            generate_audio=False,
            kwargs={},
        )
    )
    assert payload["input"]["seconds"] == 12
    assert payload["input"]["ratio"] == "9:16"
    assert payload["input"]["video_size"] == "1080p"
    assert "duration" not in payload["input"]
    assert "aspect_ratio" not in payload["input"]
    assert "resolution" not in payload["input"]
