from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.generators.video.generic_video_adapter import (
    GenericVideoAdapterError,
    GenericVideoAdapterGenerator,
)
from novelvideo.generators.video_generator import VideoGenStatus


class _FakeDownloadResponse:
    def __init__(
        self,
        status: int,
        *,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def read(self) -> bytes:
        return self._body


class _FakeDownloadSession:
    def __init__(
        self,
        responses: list[_FakeDownloadResponse],
        calls: list[dict[str, object]],
    ) -> None:
        self._responses = responses
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def get(self, url: str, **kwargs: object) -> _FakeDownloadResponse:
        self.calls.append({"url": url, **kwargs})
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_result_download_follows_same_origin_redirect_with_authorization(
    monkeypatch, tmp_path: Path
):
    calls: list[dict[str, object]] = []
    responses = [
        _FakeDownloadResponse(
            302,
            headers={"Location": "/results/final.mp4"},
        ),
        _FakeDownloadResponse(200, headers={"Content-Type": "video/mp4"}, body=b"video"),
    ]
    monkeypatch.setattr(
        "novelvideo.generators.video.generic_video_adapter.aiohttp.ClientSession",
        lambda **_kwargs: _FakeDownloadSession(responses, calls),
    )
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example:443/api",
        model="video-v1",
        adapter_family="prediction",
    )
    output = tmp_path / "same-origin.mp4"

    await generator._download(
        "https://provider.example:443/results/start.mp4",
        str(output),
    )

    assert output.read_bytes() == b"video"
    assert [call["url"] for call in calls] == [
        "https://provider.example:443/results/start.mp4",
        "https://provider.example:443/results/final.mp4",
    ]
    assert all(call["allow_redirects"] is False for call in calls)
    assert calls[0]["headers"] == {"Authorization": "Bearer fixture-key"}
    assert calls[1]["headers"] == {"Authorization": "Bearer fixture-key"}


@pytest.mark.asyncio
async def test_result_download_strips_authorization_after_cross_origin_redirect(
    monkeypatch, tmp_path: Path
):
    calls: list[dict[str, object]] = []
    responses = [
        _FakeDownloadResponse(
            302,
            headers={"Location": "https://objects.example/results/final.mp4"},
        ),
        _FakeDownloadResponse(200, headers={"Content-Type": "video/mp4"}, body=b"video"),
    ]
    monkeypatch.setattr(
        "novelvideo.generators.video.generic_video_adapter.aiohttp.ClientSession",
        lambda **_kwargs: _FakeDownloadSession(responses, calls),
    )
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example:8443/api",
        model="video-v1",
        adapter_family="prediction",
    )
    output = tmp_path / "cross-origin.mp4"

    await generator._download(
        "https://provider.example:8443/results/start.mp4",
        str(output),
    )

    assert output.read_bytes() == b"video"
    assert calls[0]["headers"] == {"Authorization": "Bearer fixture-key"}
    assert calls[1]["headers"] == {}
    assert all(call["allow_redirects"] is False for call in calls)


@pytest.mark.asyncio
async def test_result_download_fails_after_three_redirects(
    monkeypatch, tmp_path: Path
):
    calls: list[dict[str, object]] = []
    responses = [
        _FakeDownloadResponse(302, headers={"Location": f"/results/{index + 1}"})
        for index in range(4)
    ]
    monkeypatch.setattr(
        "novelvideo.generators.video.generic_video_adapter.aiohttp.ClientSession",
        lambda **_kwargs: _FakeDownloadSession(responses, calls),
    )
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example/api",
        model="video-v1",
        adapter_family="prediction",
    )

    with pytest.raises(GenericVideoAdapterError) as exc_info:
        await generator._download(
            "https://provider.example/results/0",
            str(tmp_path / "too-many-redirects.mp4"),
        )

    error = exc_info.value
    assert error.error_code == "VIDEO_RESULT_DOWNLOAD_FAILED"
    assert error.stage == "download"
    assert error.http_status == 302
    assert len(calls) == 4
    assert all(call["allow_redirects"] is False for call in calls)


@pytest.mark.asyncio
async def test_prediction_adapter_compiles_payload_polls_and_writes_artifact(
    monkeypatch, tmp_path: Path
):
    async def no_reservation(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(
        "novelvideo.generators.video_generator._reserve_video_model_call",
        no_reservation,
    )
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://relay.example",
        model="video-v1",
        adapter_family="prediction",
        poll_interval=0.01,
        max_polls=3,
    )
    requests: list[tuple[str, str, dict[str, object] | None]] = []

    async def fake_request(method, url, *, stage, payload=None, headers=None):
        requests.append((method, url, payload))
        if method == "POST":
            return {"id": "pred-1", "status": "starting"}
        return {
            "id": "pred-1",
            "status": "succeeded",
            "output": "data:video/mp4;base64,ZmFrZS12aWRlbw==",
            "usage": {"cost": {"credits": 5}},
        }

    monkeypatch.setattr(generator, "_request_json", fake_request)

    output = tmp_path / "prediction.mp4"
    events: list[dict[str, object]] = []
    result = await generator.generate(
        prompt="a river at sunrise",
        output_path=str(output),
        duration=6,
        aspect_ratio="16:9",
        resolution="720p",
        idempotency_key="fixture-idempotency",
        on_task_event=events.append,
    )

    assert result.status is VideoGenStatus.DONE
    assert result.provider_task_id == "pred-1"
    assert output.read_bytes() == b"fake-video"
    assert requests[0][0] == "POST"
    assert requests[0][1].endswith("/v1/predictions")
    assert requests[0][2]["input"]["duration"] == 6
    assert requests[0][2]["input"]["prompt"] == "a river at sunrise"
    assert "mode" not in requests[0][2]["input"]
    assert next(event for event in events if event["stage"] == "provider_cost") == {
        "stage": "provider_cost",
        "model": "video-v1",
        "protocol": generator.protocol,
        "provider_task_id": "pred-1",
        "actual_cost": {"credits": 5},
        "cost_source": "result.usage.cost",
    }


@pytest.mark.asyncio
async def test_generic_adapter_compiles_declared_size_slot_instead_of_aspect_ratio():
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://relay.example",
        model="video-v1",
        adapter_family="prediction",
        size_slots=("992x432", "640x640"),
        size_field="output_size",
    )

    payload = await generator._build_payload(
        prompt="a product rotates on a clean studio table",
        image_path=None,
        references=(),
        last_frame_path=None,
        duration=5,
        aspect_ratio="1:1",
        resolution="720p",
        generate_audio=False,
        kwargs={},
    )

    assert payload["input"]["output_size"] == "640x640"
    assert "aspect_ratio" not in payload["input"]


@pytest.mark.asyncio
async def test_generic_adapter_request_controls_override_stale_provider_values():
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="vendor-video-v2",
        adapter_family="prediction",
        allowed_resolutions=("720p", "1080p"),
        allowed_aspect_ratios=("16:9", "9:16"),
        parameter_values={
            "duration": 2,
            "resolution": "1080p",
            "aspectRatio": "9:16",
            "generateAudio": False,
            "seed": 42,
        },
        provider_mapping={
            "duration": "seconds",
            "resolution": "output_resolution",
            "aspectRatio": "output_ratio",
            "generateAudio": "with_audio",
        },
    )

    payload = await generator._build_payload(
        prompt="fixture",
        image_path=None,
        references=(),
        last_frame_path=None,
        duration=8,
        aspect_ratio="16:9",
        resolution="720p",
        generate_audio=True,
        kwargs={},
    )

    assert payload["input"]["seconds"] == 8
    assert payload["input"]["output_resolution"] == "720p"
    assert payload["input"]["output_ratio"] == "16:9"
    assert payload["input"]["with_audio"] is True
    assert payload["input"]["seed"] == 42


@pytest.mark.asyncio
async def test_queue_adapter_preserves_contract_error_for_missing_status(monkeypatch, tmp_path):
    async def no_reservation(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(
        "novelvideo.generators.video_generator._reserve_video_model_call",
        no_reservation,
    )
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://relay.example",
        model="queue-video-v1",
        adapter_family="queue",
        poll_interval=0.01,
        max_polls=1,
    )

    async def fake_request(method, url, *, stage, payload=None, headers=None):
        if method == "POST":
            return {"request_id": "queue-1"}
        return {"request_id": "queue-1", "output": {}}

    monkeypatch.setattr(generator, "_request_json", fake_request)
    events: list[dict[str, object]] = []
    result = await generator.generate(
        prompt="test",
        output_path=str(tmp_path / "queue.mp4"),
        on_task_event=events.append,
    )

    assert result.status is VideoGenStatus.FAILED
    assert result.error_metadata["error_code"] == "VIDEO_STATUS_MISSING"
    assert events[-1]["error_code"] == "VIDEO_STATUS_MISSING"


def test_generic_adapter_uses_provider_specific_lifecycle_paths():
    prediction = GenericVideoAdapterGenerator(
        api_key="key",
        endpoint="https://relay.example/api",
        model="video-v1",
        adapter_family="prediction",
    )
    queue = GenericVideoAdapterGenerator(
        api_key="key",
        endpoint="https://relay.example/api",
        model="video-v1",
        adapter_family="queue",
    )

    assert prediction._submit_url().endswith("/v1/predictions")
    assert prediction._query_url("pred-1").endswith("/v1/predictions/pred-1")
    assert queue._submit_url().endswith("/queue/video-v1/requests")
    assert queue._query_url("req-1").endswith("/queue/video-v1/requests/req-1/status")


@pytest.mark.asyncio
async def test_generic_adapter_rejects_capability_before_reservation_or_submit(
    monkeypatch, tmp_path: Path
):
    calls = {"reserve": 0, "request": 0}

    async def reserve(*_args, **_kwargs):
        calls["reserve"] += 1
        return "reservation"

    monkeypatch.setattr(
        "novelvideo.generators.video_generator._reserve_video_model_call", reserve
    )
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://relay.example",
        model="video-v1",
        adapter_family="prediction",
        allowed_durations=(4, 8),
        allowed_resolutions=("720p",),
        allowed_aspect_ratios=("16:9",),
    )

    async def request(*_args, **_kwargs):
        calls["request"] += 1
        return {}

    monkeypatch.setattr(generator, "_request_json", request)
    result = await generator.generate(
        prompt="fixture",
        output_path=str(tmp_path / "blocked.mp4"),
        duration=5,
        resolution="1080p",
        aspect_ratio="9:16",
    )

    assert result.status is VideoGenStatus.FAILED
    assert result.error_metadata["error_code"] == "VIDEO_CAPABILITY_CONTRACT_INVALID"
    codes = {
        item["code"]
        for item in result.error_metadata["request_contract"]["violations"]
    }
    assert codes == {"unsupported_duration", "unsupported_resolution", "unsupported_aspect_ratio"}
    resolution_violation = next(
        item
        for item in result.error_metadata["request_contract"]["violations"]
        if item["code"] == "unsupported_resolution"
    )
    assert resolution_violation["details"] == {
        "requestedResolution": "1080p",
        "supportedResolutions": ["720p"],
    }
    assert calls == {"reserve": 0, "request": 0}


@pytest.mark.asyncio
async def test_prediction_adapter_preserves_request_mode_and_mapping_over_stale_value():
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="vendor-video-v2",
        adapter_family="prediction",
        supported_modes=("textToVideo", "imageToVideo"),
        parameter_values={"mode": "textToVideo", "seed": 42},
        provider_mapping={"mode": "generate_mode"},
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
        mode="imageToVideo",
    )

    assert payload["input"]["generate_mode"] == "imageToVideo"
    assert payload["input"]["seed"] == 42
    assert payload["input"].get("mode") is None


@pytest.mark.asyncio
async def test_prediction_adapter_keeps_mode_when_provider_has_no_mode_mapping():
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="vendor-video-v2",
        adapter_family="prediction",
        supported_modes=("text_to_video", "image_to_video"),
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
        mode="image_to_video",
    )

    assert payload["input"]["mode"] == "image_to_video"


def test_generic_adapter_accepts_enum_like_mode_values():
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="vendor-video-v2",
        adapter_family="prediction",
        supported_modes=("image_to_video",),
    )

    assert generator._resolve_mode(SimpleNamespace(value="imageToVideo")) == (
        "image_to_video",
        "image_to_video",
    )


@pytest.mark.asyncio
async def test_prediction_adapter_preserves_first_and_last_frame_roles():
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="vendor-video-v2",
        adapter_family="prediction",
        parameter_values={
            "first_frame": "stale-first-frame",
            "last_frame": "stale-last-frame",
        },
        provider_mapping={
            "first_frame": "start_image",
            "lastFrame": "end_image",
        },
    )

    payload = await generator._build_payload(
        prompt="fixture",
        image_path="https://cdn.example/first.png",
        references=(
            SimpleNamespace(
                path="https://cdn.example/first.png",
                type="image",
                role="first_frame",
            ),
            SimpleNamespace(
                path="https://cdn.example/last.png",
                type="image",
                role="last_frame",
            ),
            SimpleNamespace(
                path="https://cdn.example/reference.png",
                type="image",
                role="reference",
            ),
        ),
        last_frame_path=None,
        duration=5,
        aspect_ratio="16:9",
        resolution="720p",
        generate_audio=False,
        kwargs={},
    )

    assert payload["input"]["start_image"] == "https://cdn.example/first.png"
    assert payload["input"]["end_image"] == "https://cdn.example/last.png"
    assert payload["input"]["images"] == [
        "https://cdn.example/first.png",
        "https://cdn.example/last.png",
        "https://cdn.example/reference.png",
    ]
    assert "first_frame" not in payload["input"]
    assert "last_frame" not in payload["input"]


def test_autodl_payload_preserves_first_and_last_frame_provider_slots():
    payload = GenericVideoAdapterGenerator._build_autodl_payload(
        prompt="fixture",
        images=[
            "https://cdn.example/first.png",
            "https://cdn.example/last.png",
        ],
        audios=[],
        duration=5,
        aspect_ratio="16:9",
        resolution="768p横",
        kwargs={},
        first_frame="https://cdn.example/first.png",
        last_frame="https://cdn.example/last.png",
        parameter_values={"first_frame": "stale-first-frame"},
        provider_mapping={"first_frame": "start_image", "last_frame": "end_image"},
    )

    assert payload["start_image"] == "https://cdn.example/first.png"
    assert payload["end_image"] == "https://cdn.example/last.png"
    assert "first_frame" not in payload
    assert "last_frame" not in payload


def test_autodl_payload_only_emits_explicitly_mapped_mode_and_common_controls():
    payload = GenericVideoAdapterGenerator._build_autodl_payload(
        prompt="fixture",
        images=[],
        audios=[],
        duration=7,
        aspect_ratio="9:16",
        resolution="480p竖",
        kwargs={},
        mode="imageToVideo",
        provider_mapping={
            "mode": "generation_type",
            "duration": "seconds",
            "resolution": "output_resolution",
            "aspectRatio": "output_ratio",
        },
    )

    assert payload["generation_type"] == "imageToVideo"
    assert payload["seconds"] == 7
    assert payload["output_resolution"] == "480p竖"
    assert payload["output_ratio"] == "9:16"

    without_mode_mapping = GenericVideoAdapterGenerator._build_autodl_payload(
        prompt="fixture",
        images=[],
        audios=[],
        duration=7,
        aspect_ratio="9:16",
        resolution="480p竖",
        kwargs={},
        mode="imageToVideo",
    )
    assert "mode" not in without_mode_mapping


def test_autodl_payload_uses_canonical_size_slot_mapping():
    payload = GenericVideoAdapterGenerator._build_autodl_payload(
        prompt="fixture",
        images=[],
        audios=[],
        duration=5,
        aspect_ratio="1:1",
        resolution="768p横",
        kwargs={},
        size_slots=("992x432", "640x640"),
        size_field="size",
        provider_mapping={"size_slot": "output_size"},
    )

    assert payload["output_size"] == "640x640"
    assert "size" not in payload


@pytest.mark.asyncio
async def test_first_last_frame_mode_alias_matches_declared_snake_case_and_rejects_unsupported(
    monkeypatch, tmp_path: Path
):
    calls = {"reserve": 0, "request": 0}

    async def reserve(*_args, **_kwargs):
        calls["reserve"] += 1
        return "reservation"

    monkeypatch.setattr(
        "novelvideo.generators.video_generator._reserve_video_model_call", reserve
    )
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="vendor-video-v2",
        adapter_family="prediction",
        supported_modes=("first_last_frame",),
    )

    assert (
        generator._validate_generation_contract(
            duration=5,
            aspect_ratio="16:9",
            resolution="720p",
            generate_audio=False,
            image_path="first.png",
            last_frame_path="last.png",
            references=(),
            mode="keyframe",
        )
        is None
    )

    async def request(*_args, **_kwargs):
        calls["request"] += 1
        return {}

    monkeypatch.setattr(generator, "_request_json", request)
    result = await generator.generate(
        prompt="fixture",
        output_path=str(tmp_path / "blocked.mp4"),
        gen_mode="videoEdit",
    )

    assert result.status is VideoGenStatus.FAILED
    assert result.error_metadata["error_code"] == "VIDEO_CAPABILITY_CONTRACT_INVALID"
    violation = result.error_metadata["request_contract"]["violations"][0]
    assert violation["code"] == "unsupported_video_mode"
    assert calls == {"reserve": 0, "request": 0}


@pytest.mark.asyncio
async def test_workflow_adapter_puts_explicit_mode_in_native_graph():
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="workflow-v1",
        adapter_family="workflow",
        supported_modes=("firstLastFrame",),
        provider_mapping={"mode": "generation_mode"},
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
        kwargs={"workflow": {"nodes": [{"id": 1}]}},
        mode="firstLastFrame",
    )

    assert payload["prompt"]["generation_mode"] == "firstLastFrame"
    assert payload["prompt"]["nodes"] == [{"id": 1}]


@pytest.mark.asyncio
async def test_workflow_adapter_maps_controls_into_nested_nodes_without_mutating_graph():
    workflow = {
        "3": {
            "class_type": "VideoSampler",
            "inputs": {
                "duration": 2,
                "height": 512,
                "mode": "textToVideo",
                "image": "old-start",
            },
        },
        "4": {"inputs": {}},
    }
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="workflow-v1",
        adapter_family="workflow",
        allowed_resolutions=("720p",),
        provider_mapping={
            "duration": "3.inputs.duration",
            "resolution": "3.inputs.height",
            "mode": "3.inputs.mode",
            "first_frame": "3.inputs.image",
            "motion_strength": "4.inputs.strength",
        },
        parameter_values={"motion_strength": 0.8},
    )

    payload = await generator._build_payload(
        prompt="fixture",
        image_path="https://cdn.example/start.png",
        references=(),
        last_frame_path=None,
        duration=7,
        aspect_ratio="16:9",
        resolution="720p",
        generate_audio=False,
        kwargs={"workflow": workflow},
        mode="imageToVideo",
    )

    graph = payload["prompt"]
    assert graph["3"]["inputs"] == {
        "duration": 7,
        "height": "720p",
        "mode": "imageToVideo",
        "image": "https://cdn.example/start.png",
    }
    assert graph["4"]["inputs"]["strength"] == 0.8
    assert "3.inputs.duration" not in graph
    assert workflow["3"]["inputs"] == {
        "duration": 2,
        "height": 512,
        "mode": "textToVideo",
        "image": "old-start",
    }
    assert workflow["4"]["inputs"] == {}


@pytest.mark.asyncio
async def test_workflow_input_rules_supply_nested_node_paths_when_mapping_is_sparse():
    workflow = {"172": {"inputs": {"value": 2}}}
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="workflow-v1",
        adapter_family="workflow",
        workflow_input_rules=(
            {
                "key": "duration",
                "rule": {"nodeId": "172", "field": "inputs.value"},
            },
        ),
    )

    payload = await generator._build_payload(
        prompt="fixture",
        image_path=None,
        references=(),
        last_frame_path=None,
        duration=9,
        aspect_ratio="16:9",
        resolution="720p",
        generate_audio=False,
        kwargs={"workflow": workflow},
    )

    assert payload["prompt"]["172"]["inputs"]["value"] == 9
    assert "duration" not in payload["prompt"]


def test_workflow_input_rules_do_not_override_explicit_provider_field_mapping():
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="workflow-v1",
        adapter_family="workflow",
        provider_mapping={"size": "output_size"},
        workflow_input_rules=(
            {
                "key": "size",
                "nodeId": "7",
                "field": "inputs.width",
            },
        ),
    )

    assert generator._workflow_provider_mapping()["size"] == "output_size"


@pytest.mark.asyncio
async def test_workflow_resolution_option_writes_values_into_list_nodes():
    workflow = {
        "nodes": [
            {"id": "174", "inputs": {"Number": 512}},
            {"id": "175", "inputs": {"Number": 512}},
        ]
    }
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="workflow-v1",
        adapter_family="workflow",
        allowed_resolutions=("736p竖", "736p横"),
        resolution_mappings=(
            {
                "label": "736p竖",
                "width": 736,
                "height": 1280,
                "values": {
                    "174.inputs.Number": 736,
                    "175.inputs.Number": 1280,
                },
            },
            {
                "label": "736p横",
                "width": 1280,
                "height": 736,
                "values": {
                    "174.inputs.Number": 1280,
                    "175.inputs.Number": 736,
                },
            },
        ),
    )

    payload = await generator._build_payload(
        prompt="fixture",
        image_path=None,
        references=(),
        last_frame_path=None,
        duration=5,
        aspect_ratio="9:16",
        resolution="736p",
        generate_audio=False,
        kwargs={"workflow": workflow},
    )

    graph = payload["prompt"]
    assert graph["nodes"][0]["inputs"]["Number"] == 736
    assert graph["nodes"][1]["inputs"]["Number"] == 1280
    assert "174" not in graph
    assert "175" not in graph
    assert workflow["nodes"][0]["inputs"]["Number"] == 512
    assert workflow["nodes"][1]["inputs"]["Number"] == 512


@pytest.mark.asyncio
async def test_workflow_resolution_option_writes_values_into_prompt_nodes():
    workflow = {
        "prompt": {
            "174": {"class_type": "PrimitiveNode", "inputs": {"Number": 512}},
            "175": {"class_type": "PrimitiveNode", "inputs": {"Number": 512}},
        }
    }
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="workflow-v1",
        adapter_family="workflow",
        resolution_mappings=(
            {
                "label": "736p竖",
                "width": 736,
                "height": 1280,
                "values": {
                    "174.inputs.Number": 736,
                    "175.inputs.Number": 1280,
                },
            },
        ),
    )

    payload = await generator._build_payload(
        prompt="fixture",
        image_path=None,
        references=(),
        last_frame_path=None,
        duration=5,
        aspect_ratio="9:16",
        resolution="736p竖",
        generate_audio=False,
        kwargs={"workflow": workflow},
    )

    graph = payload["prompt"]["prompt"]
    assert graph["174"]["inputs"]["Number"] == 736
    assert graph["175"]["inputs"]["Number"] == 1280
    assert "174" not in payload["prompt"]


@pytest.mark.asyncio
async def test_workflow_input_rule_accepts_absolute_graph_path():
    workflow = {"nodes": [{"id": "172", "inputs": {"value": 2}}]}
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="workflow-v1",
        adapter_family="workflow",
        workflow_input_rules=(
            {
                "key": "duration",
                "rule": {"nodeId": "172", "field": "/nodes/0/inputs/value"},
            },
        ),
    )

    payload = await generator._build_payload(
        prompt="fixture",
        image_path=None,
        references=(),
        last_frame_path=None,
        duration=9,
        aspect_ratio="16:9",
        resolution="720p",
        generate_audio=False,
        kwargs={"workflow": workflow},
    )

    assert payload["prompt"]["nodes"][0]["inputs"]["value"] == 9


def test_autodl_payload_keeps_explicit_mode_and_provider_mapping():
    payload = GenericVideoAdapterGenerator._build_autodl_payload(
        prompt="fixture",
        images=[],
        audios=[],
        duration=5,
        aspect_ratio="9:16",
        resolution="768p竖",
        kwargs={},
        mode="imageToVideo",
        parameter_values={"mode": "textToVideo"},
        provider_mapping={"mode": "generation_mode"},
    )

    assert payload["generation_mode"] == "imageToVideo"
    assert "mode" not in payload


@pytest.mark.asyncio
async def test_explicit_text_to_video_filters_media_before_relay_and_payload():
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="vendor-video-v2",
        adapter_family="prediction",
        supported_modes=("textToVideo",),
        parameter_values={
            "images": ["stale-image"],
            "reference_videos": ["stale-video"],
            "audios": ["stale-audio"],
            "first_frame": "stale-first",
            "lastFrame": "stale-last",
        },
        provider_mapping={
            "images": "media.images",
            "videos": "media.videos",
            "audios": "media.audios",
            "firstFrame": "media.first",
            "lastFrame": "media.last",
        },
    )
    relay_calls: list[tuple[str, str]] = []

    async def relay(value, *, kind="image"):
        relay_calls.append((str(value), kind))
        return f"https://relay.example/{value}"

    generator._media_value = relay
    payload = await generator._build_payload(
        prompt="text only",
        image_path="https://cdn.example/first.png",
        references=(
            SimpleNamespace(path="https://cdn.example/ref.mp4", type="video"),
            SimpleNamespace(path="https://cdn.example/voice.mp3", type="audio"),
        ),
        last_frame_path="https://cdn.example/last.png",
        duration=5,
        aspect_ratio="16:9",
        resolution="720p",
        generate_audio=False,
        kwargs={},
        mode="textToVideo",
    )

    assert relay_calls == []
    assert payload["input"]["prompt"] == "text only"
    assert all(
        key not in payload["input"]
        for key in (
            "image",
            "images",
            "reference_images",
            "videos",
            "reference_videos",
            "audios",
            "reference_audios",
            "media.images",
            "media.videos",
            "media.audios",
            "media.first",
            "media.last",
        )
    )


@pytest.mark.asyncio
async def test_explicit_image_to_video_prefers_image_path_and_keeps_one_image():
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="vendor-video-v2",
        adapter_family="prediction",
        supported_modes=("imageToVideo",),
    )
    relay_calls: list[tuple[str, str]] = []

    async def relay(value, *, kind="image"):
        relay_calls.append((str(value), kind))
        return f"relay:{value}"

    generator._media_value = relay
    payload = await generator._build_payload(
        prompt="animate the first image",
        image_path="preferred.png",
        references=(
            SimpleNamespace(path="fallback.png", type="image"),
            SimpleNamespace(path="ignored.mp4", type="video"),
            SimpleNamespace(path="ignored.mp3", type="audio"),
        ),
        last_frame_path="ignored-last.png",
        duration=5,
        aspect_ratio="16:9",
        resolution="720p",
        generate_audio=False,
        kwargs={},
        mode="imageToVideo",
    )

    assert relay_calls == [("preferred.png", "image")]
    assert payload["input"]["images"] == ["relay:preferred.png"]
    assert "videos" not in payload["input"]
    assert "audios" not in payload["input"]


@pytest.mark.asyncio
async def test_explicit_first_last_frame_ignores_ordinary_references():
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="vendor-video-v2",
        adapter_family="prediction",
        supported_modes=("firstLastFrame",),
    )
    relay_calls: list[tuple[str, str]] = []

    async def relay(value, *, kind="image"):
        relay_calls.append((str(value), kind))
        return f"relay:{value}"

    generator._media_value = relay
    payload = await generator._build_payload(
        prompt="interpolate frames",
        image_path=None,
        references=(
            SimpleNamespace(path="ordinary.png", type="image", role="reference"),
            SimpleNamespace(path="first.png", type="image", role="first_frame"),
            SimpleNamespace(path="last.png", type="image", role="last_frame"),
            SimpleNamespace(path="reference.mp4", type="video", role="reference"),
        ),
        last_frame_path=None,
        duration=5,
        aspect_ratio="16:9",
        resolution="720p",
        generate_audio=False,
        kwargs={},
        mode="firstLastFrame",
    )

    assert relay_calls == [("first.png", "image"), ("last.png", "image")]
    assert payload["input"]["images"] == ["relay:first.png", "relay:last.png"]
    assert "videos" not in payload["input"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_kind", "unexpected_kinds"),
    (
        ("imageReference", "image", {"video", "audio"}),
        ("videoEdit", "video", {"image", "audio"}),
    ),
)
async def test_explicit_reference_modes_filter_by_media_kind(
    mode, expected_kind, unexpected_kinds
):
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="vendor-video-v2",
        adapter_family="prediction",
        supported_modes=(mode,),
    )
    relay_calls: list[tuple[str, str]] = []

    async def relay(value, *, kind="image"):
        relay_calls.append((str(value), kind))
        return f"relay:{value}"

    generator._media_value = relay
    payload = await generator._build_payload(
        prompt="use typed references",
        image_path="input.png",
        references=(
            SimpleNamespace(path="ref.png", type="image"),
            SimpleNamespace(path="ref.mp4", type="video"),
            SimpleNamespace(path="ref.mp3", type="audio"),
        ),
        last_frame_path="last.png",
        duration=5,
        aspect_ratio="16:9",
        resolution="720p",
        generate_audio=False,
        kwargs={},
        mode=mode,
    )

    assert relay_calls
    assert {kind for _path, kind in relay_calls} == {expected_kind}
    assert all(kind not in unexpected_kinds for _path, kind in relay_calls)
    media_key = "images" if expected_kind == "image" else "videos"
    assert payload["input"][media_key]
    for key in ("images", "videos", "audios"):
        if key != media_key:
            assert key not in payload["input"]


def test_explicit_mode_preflight_uses_the_same_filtered_media_counts():
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="vendor-video-v2",
        adapter_family="prediction",
        supported_modes=("textToVideo", "videoEdit"),
        reference_limits={
            "input_images": 1,
            "reference_images": 1,
            "reference_videos": 1,
            "reference_audios": 1,
        },
    )
    references = (
        SimpleNamespace(path="ref.png", type="image"),
        SimpleNamespace(path="ref.mp4", type="video"),
        SimpleNamespace(path="ref.mp3", type="audio"),
    )

    text_error = generator._validate_generation_contract(
        duration=5,
        aspect_ratio="16:9",
        resolution="720p",
        generate_audio=False,
        image_path="first.png",
        last_frame_path="last.png",
        references=references,
        mode="textToVideo",
    )
    video_error = generator._validate_generation_contract(
        duration=5,
        aspect_ratio="16:9",
        resolution="720p",
        generate_audio=False,
        image_path="first.png",
        last_frame_path="last.png",
        references=references,
        mode="videoEdit",
    )

    assert text_error is None
    assert video_error is None


def test_explicit_all_reference_demotes_deduplicated_frames_to_references():
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="vendor-video-v2",
        adapter_family="prediction",
        supported_modes=("allReference",),
    )

    first, last, references = generator._filter_media_for_mode(
        "allReference",
        canonical_mode="reference_to_video",
        image_path="first.png",
        last_frame_path="last.png",
        references=(
            SimpleNamespace(path="first.png", type="image"),
            SimpleNamespace(path="voice.mp3", type="audio"),
        ),
    )

    assert first is None
    assert last is None
    assert [
        (item if isinstance(item, str) else item.path)
        for item in references
    ] == ["first.png", "last.png", "voice.mp3"]


@pytest.mark.asyncio
async def test_workflow_explicit_all_reference_maps_filtered_media_to_nested_nodes():
    workflow = {
        "3": {"class_type": "ImageReferences", "inputs": {}},
        "4": {"class_type": "VideoReferences", "inputs": {}},
        "5": {"class_type": "AudioReferences", "inputs": {}},
    }
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="workflow-v1",
        adapter_family="workflow",
        supported_modes=("allReference",),
        provider_mapping={
            "images": "3.inputs.image_refs",
            "videos": "4.inputs.video_refs",
            "audios": "5.inputs.audio_refs",
        },
    )

    async def relay(value, *, kind="image"):
        return f"relay:{kind}:{value}"

    generator._media_value = relay
    payload = await generator._build_payload(
        prompt="sync all references",
        image_path="first.png",
        references=(
            SimpleNamespace(path="scene.png", type="image"),
            SimpleNamespace(path="motion.mp4", type="video"),
            SimpleNamespace(path="voice.mp3", type="audio"),
        ),
        last_frame_path=None,
        duration=5,
        aspect_ratio="16:9",
        resolution="720p",
        generate_audio=False,
        kwargs={"workflow": workflow},
        mode="allReference",
    )

    graph = payload["prompt"]
    assert graph["3"]["inputs"]["image_refs"] == [
        "relay:image:first.png",
        "relay:image:scene.png",
    ]
    assert graph["4"]["inputs"]["video_refs"] == ["relay:video:motion.mp4"]
    assert graph["5"]["inputs"]["audio_refs"] == ["relay:audio:voice.mp3"]
    assert "images" not in graph
    assert "videos" not in graph
    assert "audios" not in graph


@pytest.mark.asyncio
async def test_workflow_explicit_text_to_video_does_not_write_media_nodes():
    workflow = {
        "3": {"class_type": "ImageReferences", "inputs": {}},
        "4": {"class_type": "VideoReferences", "inputs": {}},
        "5": {"class_type": "AudioReferences", "inputs": {}},
    }
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="workflow-v1",
        adapter_family="workflow",
        supported_modes=("textToVideo",),
        provider_mapping={
            "images": "3.inputs.image_refs",
            "videos": "4.inputs.video_refs",
            "audios": "5.inputs.audio_refs",
        },
    )
    relay_calls: list[tuple[str, str]] = []

    async def relay(value, *, kind="image"):
        relay_calls.append((str(value), kind))
        return f"relay:{kind}:{value}"

    generator._media_value = relay
    payload = await generator._build_payload(
        prompt="text only",
        image_path="ignored.png",
        references=(
            SimpleNamespace(path="ignored.mp4", type="video"),
            SimpleNamespace(path="ignored.mp3", type="audio"),
        ),
        last_frame_path="ignored-last.png",
        duration=5,
        aspect_ratio="16:9",
        resolution="720p",
        generate_audio=False,
        kwargs={"workflow": workflow},
        mode="textToVideo",
    )

    assert relay_calls == []
    graph = payload["prompt"]
    assert graph["3"]["inputs"] == {}
    assert graph["4"]["inputs"] == {}
    assert graph["5"]["inputs"] == {}


@pytest.mark.asyncio
async def test_workflow_omitted_mode_keeps_legacy_graph_media_behavior():
    workflow = {"3": {"class_type": "ImageReferences", "inputs": {}}}
    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://provider.example",
        model="workflow-v1",
        adapter_family="workflow",
        provider_mapping={"images": "3.inputs.image_refs"},
    )

    async def relay(value, *, kind="image"):
        return f"relay:{kind}:{value}"

    generator._media_value = relay
    payload = await generator._build_payload(
        prompt="legacy media inference",
        image_path="legacy.png",
        references=(),
        last_frame_path=None,
        duration=5,
        aspect_ratio="16:9",
        resolution="720p",
        generate_audio=False,
        kwargs={"workflow": workflow},
    )

    assert payload["prompt"]["3"]["inputs"] == {}


#: These preflight cases never reach the network, so they intentionally carry
#: no credential at all.
_REQUIRED_MEDIA_SLOTS = (
    {"key": "ref_image_0", "providerKey": "ref_image_0", "type": "image", "required": True},
    {"key": "ref_audio_0", "providerKey": "ref_audio_0", "type": "audio", "required": True},
    {"key": "ref_audio_1", "providerKey": "ref_audio_1", "type": "audio", "required": False},
)


def test_required_audio_slot_is_reported_before_a_paid_submit():
    """AutoDL rejected this submit with 「缺少必填参数：ref_audio_0」."""

    generator = GenericVideoAdapterGenerator(
        api_key="",
        endpoint="https://autodl.example",
        model="minimax_h3_z0903",
        adapter_family="autodl-comfyui",
        native_audio="required",
        media_inputs=_REQUIRED_MEDIA_SLOTS,
    )

    error = generator._validate_generation_contract(
        duration=5,
        aspect_ratio="9:16",
        resolution="768p竖",
        generate_audio=True,
        image_path="first.png",
        last_frame_path=None,
        references=(),
    )

    assert error is not None
    violation = next(
        item for item in error["violations"] if item["code"] == "required_audio_slot_missing"
    )
    assert violation["details"]["missingSlots"] == ["ref_audio_0"]
    assert "参考音频" in error["message"]


def test_required_audio_slot_accepts_one_supplied_audio_reference():
    generator = GenericVideoAdapterGenerator(
        api_key="",
        endpoint="https://autodl.example",
        model="minimax_h3_z0903",
        adapter_family="autodl-comfyui",
        native_audio="required",
        media_inputs=_REQUIRED_MEDIA_SLOTS,
    )

    error = generator._validate_generation_contract(
        duration=5,
        aspect_ratio="9:16",
        resolution="768p竖",
        generate_audio=True,
        image_path="first.png",
        last_frame_path=None,
        references=(SimpleNamespace(path="voice.mp3", type="audio"),),
    )

    assert error is None


def test_required_slot_beyond_the_payload_capacity_is_not_silently_ignored():
    """``_build_payload`` writes at most three ``ref_audio_N`` values."""

    generator = GenericVideoAdapterGenerator(
        api_key="",
        endpoint="https://autodl.example",
        model="minimax_h3_z0903",
        adapter_family="autodl-comfyui",
        native_audio="required",
        media_inputs=(
            {"key": "ref_audio_0", "type": "audio", "required": True},
            {"key": "ref_audio_1", "type": "audio", "required": True},
            {"key": "ref_audio_2", "type": "audio", "required": True},
            {"key": "ref_audio_3", "type": "audio", "required": True},
        ),
    )

    error = generator._validate_generation_contract(
        duration=5,
        aspect_ratio="9:16",
        resolution="768p竖",
        generate_audio=True,
        image_path=None,
        last_frame_path=None,
        references=tuple(
            SimpleNamespace(path=f"voice{index}.mp3", type="audio") for index in range(3)
        ),
    )

    assert error is not None
    violation = next(
        item for item in error["violations"] if item["code"] == "required_audio_slot_missing"
    )
    assert violation["details"]["missingSlots"] == ["ref_audio_3"]
    assert violation["details"]["suppliedCount"] == 3


def test_required_slots_do_not_fire_when_discovery_declared_none():
    generator = GenericVideoAdapterGenerator(
        api_key="",
        endpoint="https://autodl.example",
        model="fixture",
        adapter_family="autodl-comfyui",
        native_audio="optional",
        media_inputs=(
            {"key": "ref_audio_0", "type": "audio", "required": False},
            {"key": "ref_image_0", "type": "image", "required": False},
        ),
    )

    assert (
        generator._validate_generation_contract(
            duration=5,
            aspect_ratio="9:16",
            resolution="768p竖",
            generate_audio=False,
            image_path=None,
            last_frame_path=None,
            references=(),
        )
        is None
    )


def test_contract_message_leads_with_the_runnable_cause():
    generator = GenericVideoAdapterGenerator(
        api_key="",
        endpoint="https://provider.example",
        model="vendor-video-v2",
        adapter_family="prediction",
        native_audio="required",
    )

    error = generator._validate_generation_contract(
        duration=5,
        aspect_ratio="16:9",
        resolution="720p",
        generate_audio=False,
        image_path=None,
        last_frame_path=None,
        references=(),
    )

    assert error is not None
    assert error["message"] == (
        "视频请求不符合已声明的模型能力合同：该模型必须生成音轨，请先在节点上开启「生成声音」。"
    )
    # The machine-readable detail survives the friendlier headline.
    assert {item["code"] for item in error["violations"]} == {"native_audio_required"}
