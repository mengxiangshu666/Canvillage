from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.generators.video.direct_video_probe import (
    discover_direct_video_models,
    probe_direct_video_model,
)
from novelvideo.generators.video.direct_models import (
    DirectVideoModel,
    direct_video_model_option,
)
from novelvideo.generators.video.direct_video_capability_cache import record_capability
from novelvideo.generators.video.generic_video_adapter import (
    GenericVideoAdapterGenerator,
)
from novelvideo.freezone.video_node import (
    freezone_video_resolution_options,
    normalize_video_resolution_for_backend,
)
from novelvideo.generators.video.video_provider_adapters import (
    VideoProtocolFamily,
    get_video_adapter_contract,
    normalize_video_protocol_family,
)
from novelvideo.generators.video.video_openapi_discovery import extract_workflow_inputs
from novelvideo.generators.video_generator import VideoGenStatus
from novelvideo import config
from novelvideo.model_gateway_settings import _normalize_direct_video_base_url


def test_autodl_base_url_is_canonicalized_to_origin():
    assert _normalize_direct_video_base_url(
        "https://autodl.art/api/v1/comfyui/comfyui_workflow/{workflow_id}"
    ) == "https://autodl.art"


def test_autodl_orientation_resolution_labels_do_not_break_shared_capability(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    base_url = "https://autodl.art"
    record_capability(
        base_url=base_url,
        protocol="autodl-comfyui",
        upstream_model="minimax_h3_lightx2v_v5",
        capability={
            "source": "autodl-comfyui-contract",
            "verificationStatus": "contract-resolved",
            "detectedProtocol": "autodl-comfyui",
            "adapterFamily": "autodl-comfyui",
            "adapterConfidence": 0.98,
            "modelFound": True,
            "discoveredModelCount": 11,
            "modes": ["imageToVideo", "allReference"],
            "resolutionOptions": ["480p竖", "768p竖", "480p横", "768p横"],
            "durationRange": [1, 15],
            "nativeAudio": "optional",
        },
    )
    model = DirectVideoModel(
        registry_id="autodl-orientation",
        label="AutoDL H3",
        upstream_model="minimax_h3_lightx2v_v5",
        base_url=base_url,
        api_key="fixture-token",
        enabled=True,
        requested_protocol="autodl-comfyui",
        protocol="autodl-comfyui",
    )

    assert model.capability.resolution == ("480p", "768p")
    option = direct_video_model_option(model)
    assert option["runtimeReady"] is True
    assert option["resolutionOptions"] == [
        "480p竖",
        "768p竖",
        "480p横",
        "768p横",
    ]
    generator_options = model.generator_options(
        {"resolution": "480p", "aspect_ratio": "16:9"}
    )
    assert generator_options["resolution"] == "480p"
    assert "480p" in generator_options["allowed_resolutions"]


def test_autodl_protocol_contract_and_catalog_are_explicit():
    assert normalize_video_protocol_family("autodl") is VideoProtocolFamily.AUTODL_COMFYUI
    contract = get_video_adapter_contract("autodl-comfyui")
    assert contract.submit_path.endswith("/comfyui_workflow/{model}")
    assert contract.query_path_template.endswith("/result/{task_id}")

    result = discover_direct_video_models(
        base_url="https://autodl.art",
        api_key="fixture-token",
        protocol="autodl-comfyui",
    )
    assert result["ok"] is True
    assert result["protocol"] == "autodl-comfyui"
    assert "minimax_h3_lightx2v_no_pic" in {item["id"] for item in result["models"]}
    assert "indextts2-v1" not in {item["id"] for item in result["models"]}


def test_autodl_probe_returns_workflow_capability_without_models_call():
    result = probe_direct_video_model(
        upstream_model="minimax_h3_lightx2v_no_pic",
        base_url="https://autodl.art",
        api_key="fixture-token",
        protocol="autodl-comfyui",
    )
    assert result["ok"] is True
    assert result["modelFound"] is True
    assert result["capability"]["durationRange"] == [1, 15]
    assert result["capability"]["modes"] == ["textToVideo"]


def test_autodl_probe_maps_live_workflow_input_rules(monkeypatch, tmp_path: Path):
    """The provider workflow schema, not the static family profile, drives the node contract."""

    payload = {
        "code": "Success",
        "data": {
            "uuid": "fixture-workflow",
            "name": "Fixture H3 workflow",
            "input_rules": {
                "duration": {
                    "node_id": "172",
                    "field": "inputs.value",
                    "type": "integer",
                    "default": 5,
                    "min": 1,
                    "max": 12,
                },
                "prompt": {
                    "node_id": "173",
                    "field": "inputs.text",
                    "type": "prompt",
                    "required": True,
                },
                "resolution": {
                    "type": "enum",
                    "default": "736p竖",
                    "options": [
                        {
                            "label": "736p竖",
                            "values": {
                                "174.inputs.Number": 736,
                                "175.inputs.Number": 1280,
                            },
                        },
                        {
                            "label": "736p横",
                            "values": {
                                "174.inputs.Number": 1280,
                                "175.inputs.Number": 736,
                            },
                        },
                        {
                            "label": "736p(1:1)",
                            "values": {
                                "174.inputs.Number": 736,
                                "175.inputs.Number": 736,
                            },
                        },
                    ],
                },
                "seed": {
                    "node_id": "129",
                    "field": "inputs.noise_seed",
                    "type": "integer",
                    "default": 42,
                    "min": 1,
                    "max": 999,
                },
            },
        },
    }

    class FakeResponse:
        status_code = 200

        def json(self):
            return payload

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, url, *, headers):
            self.calls.append((url, headers))
            return FakeResponse()

    client = FakeClient()
    monkeypatch.setattr(
        "httpx.Client",
        lambda **kwargs: client,
    )
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))

    result = probe_direct_video_model(
        upstream_model="fixture-workflow",
        base_url="https://autodl.art",
        api_key="fixture-token",
        protocol="autodl-comfyui",
    )

    capability = result["capability"]
    assert result["modelFound"] is True
    assert capability["source"] == "workflow_schema"
    assert capability["durationRange"] == [1, 12]
    assert capability["resolutionOptions"] == ["736p竖", "736p横", "736p(1:1)"]
    assert capability["aspectRatios"] == ["9:16", "16:9", "1:1"]
    assert capability["resolutionMappings"][0] == {
        "label": "736p竖",
        "width": 736,
        "height": 1280,
        "values": {"174.inputs.Number": 736, "175.inputs.Number": 1280},
    }
    parameters = {item["key"]: item for item in capability["parameters"]}
    assert parameters["seed"]["providerKey"] == "seed"
    assert parameters["seed"]["minimum"] == 1
    assert parameters["seed"]["maximum"] == 999
    assert capability["providerMapping"]["resolution"] == "resolution"
    assert capability["mediaInputs"] == capability["media_inputs"]
    assert capability["workflowDiscovery"]["status"] == "discovered"
    assert client.calls[0][0].endswith("/api/v1/comfyui/workflows/fixture-workflow")
    assert client.calls[0][1]["Authorization"] == "Bearer fixture-token"


def test_comfyui_workflow_inputs_are_extracted_with_evidence_and_without_links():
    inputs = extract_workflow_inputs(
        {
            "prompt": {
                "3": {
                    "class_type": "VideoSampler",
                    "inputs": {
                        "prompt": "a product turns",
                        "width": 576,
                        "height": 1024,
                        "seed": 42,
                        "image": ["1", 0],
                    },
                }
            }
        }
    )
    by_key = {item["key"]: item for item in inputs}
    assert by_key["width"]["source"] == "workflow_schema"
    assert by_key["height"]["type"] == "integer"
    assert by_key["seed"]["providerKey"] == "seed"
    assert "image" not in by_key


@pytest.mark.asyncio
async def test_autodl_adapter_compiles_wrapper_payload_and_polls(
    monkeypatch, tmp_path: Path
):
    async def no_reservation(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(
        "novelvideo.generators.video_generator._reserve_video_model_call",
        no_reservation,
    )
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-token",
        endpoint="https://autodl.art",
        model="minimax_h3_lightx2v_no_pic",
        adapter_family="autodl-comfyui",
        poll_interval=0.01,
        max_polls=3,
    )
    requests: list[tuple[str, str, dict[str, object] | None, dict[str, str] | None]] = []

    async def fake_request(method, url, *, stage, payload=None, headers=None):
        requests.append((method, url, payload, headers))
        if method == "POST":
            return {"code": "Success", "data": {"task_id": "task-1", "status": "QUEUED"}}
        return {
            "code": "Success",
            "data": {
                "task_id": "task-1",
                "status": "SUCCESS",
                "results": [{"url": "data:video/mp4;base64,ZmFrZS12aWRlbw==", "type": "video"}],
            },
        }

    monkeypatch.setattr(generator, "_request_json", fake_request)
    output = tmp_path / "autodl.mp4"
    result = await generator.generate(
        prompt="a product rotates on a clean studio table",
        output_path=str(output),
        duration=5,
        aspect_ratio="9:16",
        resolution="480p",
        seed=123,
    )

    assert result.status is VideoGenStatus.DONE
    assert result.provider_task_id == "task-1"
    assert output.read_bytes() == b"fake-video"
    assert requests[0][1].endswith(
        "/api/v1/comfyui/comfyui_workflow/minimax_h3_lightx2v_no_pic"
    )
    assert requests[0][2] == {
        "prompt": "a product rotates on a clean studio table",
        "duration": 5,
        "resolution": "480p竖",
        "seed": 123,
    }
    assert generator._headers()["Authorization"] == "fixture-token"


@pytest.mark.asyncio
async def test_autodl_payload_maps_reference_urls_to_numbered_fields():
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-token",
        endpoint="https://autodl.art",
        model="minimax_h3_image_audio_to_video_v2_15s",
        adapter_family="autodl-comfyui",
    )
    payload = await generator._build_payload(
        prompt="sync the product movement to the beat",
        image_path="https://cdn.example/product.png",
        references=(
            SimpleNamespace(path="https://cdn.example/scene.png", type="image"),
            SimpleNamespace(path="https://cdn.example/voice.mp3", type="audio"),
        ),
        last_frame_path=None,
        duration=10,
        aspect_ratio="16:9",
        resolution="768p",
        generate_audio=False,
        kwargs={},
    )
    assert payload["resolution"] == "768p横"
    assert payload["ref_image_0"] == "https://cdn.example/product.png"
    assert payload["ref_image_1"] == "https://cdn.example/scene.png"
    assert payload["ref_audio_0"] == "https://cdn.example/voice.mp3"


@pytest.mark.asyncio
async def test_autodl_payload_keeps_discovered_provider_parameters_and_size_mapping():
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-token",
        endpoint="https://autodl.art",
        model="minimax_h3_lightx2v_v5",
        adapter_family="autodl-comfyui",
        parameter_values={"seed": 42, "motion_strength": 0.4},
        provider_mapping={"seed": "random_seed", "motion_strength": "motion"},
        size_slots=("1024x576", "576x1024"),
        size_field="output_size",
    )
    payload = await generator._build_payload(
        prompt="move naturally",
        image_path=None,
        references=(),
        last_frame_path=None,
        duration=6,
        aspect_ratio="9:16",
        resolution="768p",
        generate_audio=False,
        kwargs={},
    )

    assert payload["random_seed"] == 42
    assert payload["motion"] == 0.4
    assert payload["output_size"] == "576x1024"


@pytest.mark.asyncio
async def test_autodl_payload_drops_internal_capability_gate_but_keeps_provider_parameters():
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-token",
        endpoint="https://autodl.art",
        model="minimax_h3_image_audio_to_video_v2_15s",
        adapter_family="autodl-comfyui",
        duration_parameter_enabled=True,
        parameter_values={"motion_strength": 0.4},
        provider_mapping={"motion_strength": "motion"},
    )

    payload = await generator._build_payload(
        prompt="keep the camera movement smooth",
        image_path="https://cdn.example/frame.png",
        references=(SimpleNamespace(path="https://cdn.example/identity.png", type="image"),),
        last_frame_path=None,
        duration=5,
        aspect_ratio="9:16",
        resolution="480p",
        generate_audio=False,
        kwargs={},
    )

    assert "duration_parameter_enabled" not in payload
    assert payload["prompt"] == "keep the camera movement smooth"
    assert payload["duration"] == 5
    assert payload["resolution"] == "480p竖"
    assert payload["ref_image_0"] == "https://cdn.example/frame.png"
    assert payload["ref_image_1"] == "https://cdn.example/identity.png"
    assert payload["motion"] == 0.4


@pytest.mark.asyncio
async def test_autodl_payload_preserves_square_native_resolution_label(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    record_capability(
        base_url="https://autodl.art",
        protocol="autodl-comfyui",
        upstream_model="minimax_h3_lightx2v_v5",
        capability={
            "verificationStatus": "contract-resolved",
            "modelFound": True,
            "discoveredModelCount": 11,
            "source": "workflow_schema",
            "modes": ["imageToVideo", "allReference"],
            "resolutionOptions": ["480p竖", "480p横", "480p(1:1)"],
            "advertisedResolutionOptions": ["480p竖", "480p横", "480p(1:1)"],
            "runtimeResolutionOptions": ["480p竖", "480p横", "480p(1:1)"],
            "aspectRatios": ["9:16", "16:9", "1:1"],
            "durationRange": [1, 10],
            "nativeAudio": "unsupported",
            "referenceLimits": {"inputImages": 1, "referenceImages": 9},
        },
    )
    backend = "direct_video-5825124957686d89"
    model = DirectVideoModel(
        registry_id="5825124957686d89",
        label="AutoDL H3",
        upstream_model="minimax_h3_lightx2v_v5",
        base_url="https://autodl.art",
        api_key="fixture-token",
        enabled=True,
        requested_protocol="autodl-comfyui",
        protocol="autodl-comfyui",
    )
    monkeypatch.setattr(
        "novelvideo.generators.video.direct_models.resolve_direct_video_model",
        lambda value: model if value == backend else None,
    )
    assert "480p(1:1)" in freezone_video_resolution_options(backend)
    assert normalize_video_resolution_for_backend(backend, "480p(1:1)") == "480p(1:1)"

    generator = GenericVideoAdapterGenerator(
        api_key="fixture-token",
        endpoint="https://autodl.art",
        model="minimax_h3_lightx2v_v5",
        adapter_family="autodl-comfyui",
    )
    payload = await generator._build_payload(
        prompt="square product motion",
        image_path=None,
        references=(),
        last_frame_path=None,
        duration=1,
        aspect_ratio="1:1",
        resolution="480p(1:1)",
        generate_audio=False,
        kwargs={},
    )
    assert payload["resolution"] == "480p(1:1)"


@pytest.mark.asyncio
async def test_autodl_local_reference_is_relayed_to_fetchable_url(monkeypatch, tmp_path: Path):
    source = tmp_path / "scene.png"
    source.write_bytes(b"fixture-image")
    calls: list[dict[str, object]] = []

    def fake_upload(data: bytes, *, ext: str, image_transform: str | None = None) -> str:
        calls.append(
            {"data": data, "ext": ext, "image_transform": image_transform}
        )
        return "https://relay.example/scene.jpg"

    monkeypatch.setattr(
        "novelvideo.generators.video.generic_video_adapter.upload_media_bytes",
        fake_upload,
    )
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-token",
        endpoint="https://autodl.art",
        model="minimax_h3_lightx2v_v5",
        adapter_family="autodl-comfyui",
    )

    payload = await generator._build_payload(
        prompt="move the subject naturally",
        image_path=str(source),
        references=(),
        last_frame_path=None,
        duration=5,
        aspect_ratio="16:9",
        resolution="480p",
        generate_audio=False,
        kwargs={},
    )

    assert payload["ref_image_0"] == "https://relay.example/scene.jpg"
    assert calls == [
        {
            "data": b"fixture-image",
            "ext": "png",
            "image_transform": "ai_reference_jpeg",
        }
    ]


@pytest.mark.asyncio
async def test_autodl_upstream_rejection_keeps_safe_response_shape(monkeypatch, tmp_path: Path):
    async def no_reservation(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(
        "novelvideo.generators.video_generator._reserve_video_model_call",
        no_reservation,
    )
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-token",
        endpoint="https://autodl.art",
        model="minimax_h3_lightx2v_v5",
        adapter_family="autodl-comfyui",
    )

    async def fake_request(method, url, *, stage, payload=None, headers=None):
        assert method == "POST"
        return {
            "code": "InvalidParameter",
            "data": None,
            "msg": "workflow input rejected",
            "request_id": "request-fixture",
        }

    monkeypatch.setattr(generator, "_request_json", fake_request)
    result = await generator.generate(
        prompt="fixture",
        output_path=str(tmp_path / "autodl.mp4"),
        duration=5,
        aspect_ratio="16:9",
        resolution="480p",
    )

    assert result.status is VideoGenStatus.FAILED
    assert result.error_metadata["error_code"] == "VIDEO_UPSTREAM_REQUEST_REJECTED"
    assert result.error_metadata["request_contract"] == {
        "response_keys": ["code", "data", "msg", "request_id"],
        "response_data_type": "null",
        "response_message_present": True,
        "response_code": "InvalidParameter",
        "response_message": "workflow input rejected",
    }
