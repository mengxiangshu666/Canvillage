"""Regression coverage for the dedicated Prompt-Hubs Firefly video route."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from novelvideo import config
from novelvideo.freezone.video_request_contract import (
    build_minimax_h3_provider_prompt,
)
from novelvideo.generators.video.direct_video_capability_cache import record_capability
from novelvideo.generators.video_generator import (
    NewApiVideoError,
    NewApiVideoGenerator,
    ShotReference,
    VideoGenStatus,
    create_video_generator,
)
from novelvideo.freezone.video_node import (
    freezone_video_model_contract,
    is_freezone_multi_reference_backend,
)


pytestmark = pytest.mark.m09


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["happyhorse-1.0", "kling-3.0-omni", "sd2.0-720p-4img-fast", "firefly-seedance2-fast-480p"])
@pytest.mark.parametrize("configured", [False, True])
async def test_long_video_prompt_is_rejected_before_upload_or_submission(model, configured, tmp_path):
    generator = NewApiVideoGenerator(api_key="test-key", endpoint="https://gateway.invalid/v1", model=model, resolution="720p")
    generator._recheck_stale_credential = AsyncMock(return_value=None)
    generator._relay_frame_input = AsyncMock()
    generator._relay_media_input = AsyncMock()
    generator._post_json = AsyncMock()
    long_prompt = "镜" * 2500 + "松手后稳稳落地，保留材质细节与干净暗部。"
    use_config = configured and model != "happyhorse-1.0"
    result = await generator.generate(
        image_path="first.png", prompt="短镜头" if use_config else long_prompt,
        output_path=str(tmp_path / "video.mp4"),
        seedance2_config=json.dumps({"final_prompt": long_prompt}) if use_config else None,
    )
    assert result.status is VideoGenStatus.FAILED
    assert "未截断或提交" in result.error
    assert result.error_metadata["error_code"] == "VIDEO_PROMPT_LIMIT_EXCEEDED"
    assert result.error_metadata["retryable"] is False
    generator._relay_frame_input.assert_not_awaited()
    generator._relay_media_input.assert_not_awaited()
    generator._post_json.assert_not_awaited()


class _UsageMeter:
    async def reserve_current_model_call_credit(self, **_kwargs):
        return "reservation"

    async def refund_model_call_credit_reservation(self, *_args, **_kwargs):
        return None

    async def bump_model_call(self, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_newapi_post_serializes_nonempty_body_and_keeps_safe_shape_on_400(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status = 400
        headers: dict[str, str] = {}

        async def text(self):
            return '{"code":"invalid_request","message":"bad request"}'

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeSession:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def post(self, url, **kwargs):
            captured.update({"url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(
        "novelvideo.generators.video_generator.aiohttp.ClientSession",
        FakeSession,
    )
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="minimax_h3",
        resolution="2k",
        protocol="minimax-video-v2",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )
    payload = {
        "model": "minimax_h3",
        "prompt": "PRIVATE_PROMPT_MUST_NOT_ENTER_DIAGNOSTICS",
        "reference_images": ["https://media.test/a", "https://media.test/b"],
        "metadata": {"reference_videos": ["https://media.test/v"]},
    }

    with pytest.raises(NewApiVideoError) as exc_info:
        await generator._post_json(
            "https://gateway.invalid/v1/videos",
            payload,
            idempotency_key="job-1",
        )

    body = captured["data"]
    assert isinstance(body, bytes) and len(body) > 0
    parsed_body = json.loads(body.decode("utf-8"))
    assert parsed_body["prompt"] == "PRIVATE_PROMPT_MUST_NOT_ENTER_DIAGNOSTICS"
    assert "json" not in captured
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["Accept"] == "application/json"
    assert headers["Content-Length"] == str(len(body))
    assert headers["Idempotency-Key"] == "job-1"
    contract = exc_info.value.diagnostic_contract(protocol="minimax-video-v2")[
        "request_contract"
    ]
    assert contract["body_bytes"] == len(body)
    assert contract["content_length"] == len(body)
    assert contract["payload_keys"] == [
        "metadata",
        "model",
        "prompt",
        "reference_images",
    ]
    assert contract["metadata_keys"] == ["reference_videos"]
    assert contract["media_counts"] == {"image": 2, "video": 1}
    assert "PRIVATE_PROMPT" not in str(contract)


@pytest.mark.asyncio
async def test_newapi_video_aggregate_error_keeps_body_contract_for_relay_diagnosis(
    tmp_path,
) -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="minimax_h3",
        resolution="2k",
        protocol="openai-video",
        create_path="/videos",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )
    request_contract = {
        "content_type": "application/json; charset=utf-8",
        "body_bytes": 232,
        "content_length": 232,
        "body_sha256": "0123456789abcdef",
        "payload_keys": ["metadata", "model", "prompt"],
    }
    generator._post_json = AsyncMock(
        side_effect=NewApiVideoError(
            'HTTP 400 {"code":"invalid_request","message":"canonicalize JSON request body: unexpected end of JSON input"}',
            http_status=400,
            response_text=(
                '{"code":"invalid_request","message":"canonicalize JSON request body: '
                'unexpected end of JSON input"}'
            ),
            stage="submit",
            url_path="/videos",
            request_contract=request_contract,
        )
    )

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path=None,
            prompt="relay body fixture",
            output_path=str(tmp_path / "relay.mp4"),
            duration=6,
            poll_interval=0,
            max_polls=1,
        )

    assert result.status is VideoGenStatus.FAILED
    assert result.error_metadata is not None
    assert result.error_metadata["error_code"] == "VIDEO_RELAY_JSON_BODY_REJECTED"
    assert result.error_metadata["request_contract"] == request_contract


@pytest.mark.asyncio
async def test_minimax_h3_uses_documented_video_v1_request_shape(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="minimax_h3",
        resolution="2k",
        protocol="openai-video",
        create_path="/videos",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )
    generator._relay_frame_input = AsyncMock(
        side_effect=lambda path: f"https://media.test/{Path(path).name}"
    )
    generator._relay_media_input = AsyncMock(
        side_effect=lambda path, **_kwargs: f"https://media.test/{Path(path).name}"
    )
    generator._post_json = AsyncMock(return_value={"id": "h3-task"})
    generator._get_json = AsyncMock(
        return_value={
            "status": "completed",
            "url": "data:video/mp4;base64," + base64.b64encode(b"h3").decode(),
        }
    )

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path="first.png",
            prompt="让主体自然运动",
            output_path=str(tmp_path / "h3.mp4"),
            aspect_ratio="16:9",
            duration=6,
            references=[
                ShotReference("image", "ref.png", "参考图"),
                ShotReference("video", "source.mp4", "源视频"),
                ShotReference("audio", "sound.mp3", "音频参考"),
            ],
            poll_interval=0,
            max_polls=1,
        )

    assert result.status is VideoGenStatus.DONE
    assert (tmp_path / "h3.mp4").read_bytes() == b"h3"
    submit_url, payload = generator._post_json.await_args.args[:2]
    assert submit_url == "https://gateway.invalid/v1/videos"
    assert payload["version"] == "video.v1"
    assert payload["model"] == "minimax_h3"
    assert payload["operation"] == "image_to_video"
    assert payload["duration_seconds"] == 6
    assert payload["resolution"] == "2K"
    assert payload["aspect_ratio"] == "16:9"
    assert [item["kind"] for item in payload["media_inputs"]] == [
        "image",
        "image",
        "video",
        "audio",
    ]
    assert "metadata" not in payload
    assert "seconds" not in payload
    assert "images" not in payload
    assert "ratio" not in payload

    canonical = generator._apply_capability_contract(dict(payload))
    assert canonical["resolution"] == "2K"
    assert canonical["duration_seconds"] == 6
    assert canonical["aspect_ratio"] == "16:9"
    low_resolution = generator._apply_capability_contract(
        {**payload, "resolution": "768"}
    )
    assert low_resolution["resolution"] == "768"


@pytest.mark.asyncio
async def test_minimax_h3_retries_comfyui_2k_rejection_at_768(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="minimax_h3",
        resolution="2k",
        protocol="openai-video",
        create_path="/videos",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )
    first_error = NewApiVideoError(
        'HTTP 400 {"code":"invalid_request","message":"unsupported ComfyUI H3 size \\"2K\\"; supported aspect ratios are 16:9"}',
        http_status=400,
        response_text=(
            '{"code":"invalid_request","message":"unsupported ComfyUI H3 size '
            '\\"2K\\"; supported aspect ratios are 16:9"}'
        ),
        stage="submit",
        url_path="/videos",
    )
    generator._post_json = AsyncMock(
        side_effect=[first_error, {"id": "h3-768-task"}]
    )
    generator._get_json = AsyncMock(
        return_value={
            "status": "completed",
            "url": "data:video/mp4;base64," + base64.b64encode(b"h3-768").decode(),
        }
    )

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path="https://media.test/reference.jpg",
            prompt="主体自然移动",
            output_path=str(tmp_path / "h3-768.mp4"),
            aspect_ratio="16:9",
            duration=4,
            poll_interval=0,
            max_polls=1,
        )

    assert result.status is VideoGenStatus.DONE
    assert (tmp_path / "h3-768.mp4").read_bytes() == b"h3-768"
    assert generator._post_json.await_count == 2
    assert generator._post_json.await_args_list[0].args[1]["resolution"] == "2K"
    assert generator._post_json.await_args_list[1].args[1]["resolution"] == "768"


@pytest.mark.asyncio
async def test_huabu_sd_hyphen_model_uses_video_route_and_profile_payload(tmp_path) -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="sd-2.0-fast-v1",
        resolution="720p",
        protocol="openai-video",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )
    generator._post_json = AsyncMock(return_value={"id": "task-sd"})
    generator._get_json = AsyncMock(
        return_value={
            "id": "task-sd",
            "status": "completed",
            "url": "data:video/mp4;base64," + base64.b64encode(b"sd").decode(),
        }
    )

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path=None,
            prompt="huabu sd fixture",
            output_path=str(tmp_path / "sd.mp4"),
            duration=10,
            poll_interval=0,
            max_polls=1,
        )

    assert result.status is VideoGenStatus.DONE
    submit_url, payload = generator._post_json.await_args.args
    assert submit_url == "https://gateway.invalid/v1/videos"
    assert payload == {
        "model": "sd-2.0-fast-v1",
        "prompt": "huabu sd fixture",
        "duration": 10,
        "resolution": "720p",
    }


@pytest.mark.asyncio
async def test_huabu_sd_off_grid_duration_is_blocked_before_submit(tmp_path) -> None:
    """「只能真」：不在档位上的时长不再静默对齐，直接拦截并说清楚。"""

    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="sd-2.0-fast-v1",
        resolution="720p",
        protocol="openai-video",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )
    generator._post_json = AsyncMock(return_value={"id": "task-sd"})

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path=None,
            prompt="huabu sd off-grid",
            output_path=str(tmp_path / "sd-off-grid.mp4"),
            duration=7,
            poll_interval=0,
            max_polls=1,
        )

    assert result.status is VideoGenStatus.FAILED
    assert result.error_metadata["error_code"] == "VIDEO_DURATION_MISMATCH"
    assert generator._post_json.await_count == 0


@pytest.mark.asyncio
async def test_minimax_v2_generate_uses_protocol_urls_and_nested_result(
    tmp_path,
) -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://tokendance.space/gateway/v1",
        model="minimax-h3",
        resolution="2k",
        generate_audio=True,
        protocol="minimax-video-v2",
        create_path="/video_generation",
        query_path_template="/query/video_generation/{task_id}",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
        allowed_durations=tuple(range(4, 16)),
        allowed_aspect_ratios=("16:9", "9:16"),
    )
    generator._relay_frame_input = AsyncMock(
        return_value="https://media.test/first.png"
    )
    generator._post_json = AsyncMock(return_value={"task_id": "minimax-task"})
    generator._get_json = AsyncMock(
        return_value={
            "task": {
                "status": "succeeded",
                "content": {
                    "url": "data:video/mp4;base64,"
                    + base64.b64encode(b"minimax").decode()
                },
            }
        }
    )
    prompt = build_minimax_h3_provider_prompt(
        "女孩看向门口，说话表演。",
        ["你终于来了。"],
    )

    with patch("novelvideo.generators.video_generator.get_usage_meter", return_value=_UsageMeter()):
        result = await generator.generate(
            image_path="first.png",
            prompt=prompt,
            output_path=str(tmp_path / "minimax.mp4"),
            aspect_ratio="16:9",
            duration=6,
        )

    assert result.status is VideoGenStatus.DONE
    assert (tmp_path / "minimax.mp4").read_bytes() == b"minimax"
    create_url, payload = generator._post_json.await_args.args
    assert create_url == (
        "https://tokendance.space/gateway/minimax/v2/video_generation"
    )
    assert payload["model"] == "minimax-h3"
    assert payload["resolution"] == "2K"
    assert payload["duration"] == 6
    # Frames/references ride the ``adaptive`` ratio family; a fixed canvas is
    # what the native H3 service rejects as an FL2VA ratio.
    assert payload["ratio"] == "adaptive"
    assert payload["content"][0] == {"type": "text", "text": prompt}
    assert payload["content"][1]["role"] == "first_frame"
    assert generator._get_json.await_args.args[0] == (
        "https://tokendance.space/gateway/minimax/v2/query/video_generation/minimax-task"
    )


@pytest.mark.asyncio
async def test_generic_openai_video_preserves_declared_multi_reference_images(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    endpoint = "https://relay.example/v1"
    record_capability(
        base_url=endpoint,
        protocol="openai-video",
        upstream_model="operator-multi-ref-v1",
        capability={
            "source": "models-metadata",
            "modes": ["textToVideo", "imageToVideo", "allReference"],
            "referenceLimits": {"referenceImages": 4},
            "referenceLimitsKnown": ["referenceImages"],
        },
    )
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint=endpoint,
        model="operator-multi-ref-v1",
        protocol="openai-video",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )
    generator._relay_frame_input = AsyncMock(side_effect=lambda path: path)
    generator._post_json = AsyncMock(return_value={"id": "task-1"})
    generator._get_json = AsyncMock(
        return_value={
            "status": "completed",
            "url": "data:video/mp4;base64,"
            + base64.b64encode(b"generic-multi-ref").decode(),
        }
    )

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path="https://media.test/first.png",
            prompt="multi reference fixture",
            output_path=str(tmp_path / "generic.mp4"),
            duration=5,
            references=[
                ShotReference("image", "https://media.test/ref-a.png", "参考图 A"),
                ShotReference("image", "https://media.test/ref-b.png", "参考图 B"),
            ],
            poll_interval=0,
            max_polls=1,
        )

    assert result.status is VideoGenStatus.DONE
    payload = generator._post_json.await_args.args[1]
    assert payload["images"] == [
        "https://media.test/first.png",
        "https://media.test/ref-a.png",
        "https://media.test/ref-b.png",
    ]
    assert "reference_images" not in payload["metadata"]


def test_firefly_model_contract_helpers_and_limits() -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="firefly-seedance2-fast-480p",
        resolution="480p",
    )
    generator_720 = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="firefly-seedance2-fast-720p",
        resolution="720p",
    )

    assert generator.create_path == "/videos"
    assert generator._is_firefly_seedance2_model()
    assert generator._duration_bounds() == (4, 15)
    assert generator._firefly_resolution() == "480p"
    assert generator_720._firefly_resolution() == "720p"
    assert generator._firefly_ratio("9:16") == "9:16"
    assert generator._firefly_ratio("21:9") == "16:9"
    assert generator._coerce_bool("true") is True
    assert generator._coerce_bool("false") is False
    assert is_freezone_multi_reference_backend(
        "newapi_firefly-seedance2-fast-480p"
    )

    contract = freezone_video_model_contract("newapi_firefly-seedance2-fast-480p")
    assert contract["referenceLimits"]["allReference"] == {
        "image": 9,
        "video": 3,
        "audio": 3,
        "total": 12,
    }


def test_kling_omni_uses_prompt_hubs_videos_endpoint_and_discrete_duration() -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="kling-3.0-omni",
        resolution="720p",
    )

    assert generator.create_path == "/videos"
    assert generator._is_prompt_hubs_flex_video_model()
    assert generator._duration_bounds() == (5, 10)
    assert generator._prompt_hubs_flex_duration(4) == 5
    assert generator._prompt_hubs_flex_duration(6) == 8
    assert generator._prompt_hubs_flex_duration(9) == 10
    assert generator._prompt_hubs_flex_resolution("1080p") == "1080p"
    assert generator._prompt_hubs_flex_ratio("1:1") == "16:9"

    contract = freezone_video_model_contract("newapi_kling-3.0-omni")
    assert contract["family"] == "kling-3.0"
    assert contract["supportedModes"] == [
        "textToVideo",
        "imageToVideo",
        "imageReference",
    ]
    assert contract["referenceLimits"]["imageReference"] == {
        "image": 1,
        "video": 0,
        "audio": 0,
    }
    sd_contract = freezone_video_model_contract("newapi_sd2.0-720p-4img-fast")
    assert sd_contract["family"] == "prompt-hubs-sd"
    assert sd_contract["referenceLimits"]["allReference"] == {
        "image": 4,
        "video": 3,
        "audio": 1,
        "total": 8,
    }


@pytest.mark.asyncio
async def test_prompt_hubs_early_failed_status_keeps_same_task_until_completed(
    tmp_path,
) -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="veo-3.1-lite",
        resolution="720p",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )
    generator._relay_frame_input = AsyncMock(
        return_value="https://media.test/first.png"
    )
    generator._post_json = AsyncMock(return_value={"id": "task-1"})
    generator._get_json = AsyncMock(
        side_effect=[
            {"id": "task-1", "status": "failed", "progress": 0},
            {
                "id": "task-1",
                "status": "completed",
                "url": "https://media.test/result.mp4",
            },
        ]
    )

    async def fake_download(**kwargs):
        Path(kwargs["output_path"]).write_bytes(b"video")
        return str(kwargs["video_url"])

    generator._download_completed_task_video = AsyncMock(side_effect=fake_download)

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path="first.png",
            prompt="cinematic fixture",
            output_path=str(tmp_path / "result.mp4"),
            aspect_ratio="16:9",
            duration=4,
            poll_interval=0,
            max_polls=4,
        )

    assert result.status is VideoGenStatus.DONE
    assert result.task_id == "task-1"
    assert generator._post_json.await_count == 1
    assert generator._get_json.await_count == 2
    assert (tmp_path / "result.mp4").read_bytes() == b"video"


def test_stale_firefly_backend_routes_to_default_village_canvas_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo import config

    monkeypatch.setattr(config, "NEWAPI_VIDEO_MODELS", ["village-canvas-video", "kling-3.0-omni"])
    monkeypatch.setattr(config, "NEWAPI_VIDEO_MODEL", "village-canvas-video")
    monkeypatch.setattr(config, "DEFAULT_VIDEO_MODEL", "village-canvas-video")

    generator = create_video_generator(
        backend="newapi_firefly-seedance2-fast-480p",
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
    )

    assert isinstance(generator, NewApiVideoGenerator)
    assert generator.model == "village-canvas-video"
    assert generator.create_path == "/videos"


def test_legacy_firefly_video_model_is_not_projected_into_mainline_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo import config
    from novelvideo.api.routes import generation

    monkeypatch.setattr(
        config, "NEWAPI_VIDEO_MODELS", ["firefly-seedance2-fast-480p"]
    )
    monkeypatch.setattr(config, "NEWAPI_VIDEO_AUDIO_MODELS", [])
    monkeypatch.setattr(
        config,
        "NEWAPI_VIDEO_DURATION_BOUNDS",
        "firefly-seedance2-fast-480p:4-15",
    )

    options = {
        item.value: item.model_dump()
        for item in generation._api_video_backend_options()
    }
    assert "newapi_firefly-seedance2-fast-480p" not in options
    assert options == {}


@pytest.mark.asyncio
async def test_village_canvas_route_uses_openai_video_payload_and_custom_fallback(
    tmp_path,
) -> None:
    from novelvideo.generators.video_generator import NewApiVideoError

    generator = NewApiVideoGenerator(
        api_key="official-key",
        endpoint="https://official.invalid/v1",
        model="village-canvas-video",
        resolution="720p",
    )
    generator.gateway_candidates = [
        {
            "name": "official",
            "api_key": "official-key",
            "base_url": "https://official.invalid/v1",
        },
        {
            "name": "custom",
            "api_key": "custom-key",
            "base_url": "https://custom.invalid/v1",
        },
    ]
    generator._relay_frame_input = AsyncMock(return_value="https://cdn.invalid/first.png")
    generator._post_json = AsyncMock(
        side_effect=[
            NewApiVideoError("official unavailable"),
            {"id": "video_test"},
        ]
    )
    generator._get_json = AsyncMock(
        return_value={
            "id": "video_test",
            "status": "completed",
            "url": "data:video/mp4;base64," + base64.b64encode(b"mp4").decode(),
        }
    )

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path="first.png",
            prompt="主体自然运动，镜头平稳。",
            output_path=str(tmp_path / "video.mp4"),
            aspect_ratio="9:16",
            duration=6,
            references=[ShotReference("image", "second.png", "")],
        )

    assert result.status is VideoGenStatus.DONE
    assert generator.base_url == "https://custom.invalid/v1"
    assert [call.args[0] for call in generator._post_json.await_args_list] == [
        "https://official.invalid/v1/videos",
        "https://custom.invalid/v1/videos",
    ]
    payload = generator._post_json.await_args_list[0].args[1]
    assert payload == {
        "model": "village-canvas-video",
        "prompt": "主体自然运动，镜头平稳。",
        "duration": 10,
        "size": "9:16",
        "image": "https://cdn.invalid/first.png",
    }
    assert (tmp_path / "video.mp4").read_bytes() == b"mp4"


def test_dedicated_video_gateway_is_explicit_secondary_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo import config

    monkeypatch.setenv("VILLAGE_CANVAS_VIDEO_USE_DEDICATED_GATEWAY", "1")
    monkeypatch.setenv("VILLAGE_CANVAS_ALLOW_EXPLICIT_VIDEO_MODEL", "1")
    monkeypatch.setenv("NEWAPI_VIDEO_BASE_URL", "https://video.invalid/v1/")
    monkeypatch.setenv("NEWAPI_VIDEO_API_KEY", "video-key")
    monkeypatch.setenv("NEWAPI_VIDEO_CREATE_PATH", "videos")
    monkeypatch.setattr(
        config,
        "get_effective_newapi_gateway_config",
        lambda: SimpleNamespace(api_key="main-key", base_url="https://main.invalid/v1"),
    )

    generator = NewApiVideoGenerator(
        model="village-canvas-video",
        resolution="480p",
    )

    assert generator.api_key == "main-key"
    assert generator.base_url == "https://main.invalid/v1"
    assert generator.gateway_candidates == [
        {
            "name": "effective",
            "source": "effective",
            "mode": "",
            "api_key": "main-key",
            "base_url": "https://main.invalid/v1",
        },
        {
            "name": "dedicated-video",
            "source": "dedicated-video",
            "mode": "video",
            "api_key": "video-key",
            "base_url": "https://video.invalid/v1",
        },
    ]
    assert generator.create_path == "/videos"


def test_portable_video_key_is_ignored_without_explicit_dedicated_gateway(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from novelvideo import config

    monkeypatch.setenv("VILLAGE_CANVAS_ALLOW_EXPLICIT_VIDEO_MODEL", "1")
    state_key = tmp_path / "video-api-key.txt"
    state_key.write_text("video-file-key\n", encoding="utf-8")
    monkeypatch.delenv("VILLAGE_CANVAS_VIDEO_USE_DEDICATED_GATEWAY", raising=False)
    monkeypatch.delenv("NEWAPI_VIDEO_BASE_URL", raising=False)
    monkeypatch.delenv("NEWAPI_VIDEO_API_KEY", raising=False)
    monkeypatch.setattr(config, "NEWAPI_VIDEO_BASE_URL", "")
    monkeypatch.setattr(config, "NEWAPI_VIDEO_API_KEY", "")
    monkeypatch.setattr(config, "NEWAPI_VIDEO_API_KEY_FILE", str(state_key))
    monkeypatch.setattr(
        config,
        "get_effective_newapi_gateway_config",
        lambda: SimpleNamespace(api_key="main-key", base_url="http://10.66.66.1:3000/v1"),
    )

    generator = NewApiVideoGenerator(
        model="village-canvas-video",
        resolution="480p",
    )

    assert generator.api_key == "main-key"
    assert generator.base_url == "http://10.66.66.1:3000/v1"
    assert all(candidate["api_key"] != "video-file-key" for candidate in generator.gateway_candidates)


@pytest.mark.asyncio
async def test_kling_omni_generate_uses_prompt_hubs_payload_and_one_reference(
    tmp_path,
) -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="kling-3.0-omni",
        resolution="720p",
    )
    generator._relay_frame_input = AsyncMock(
        side_effect=lambda value: f"https://cdn.invalid/{value}"
    )
    generator._post_json = AsyncMock(return_value={"id": "video_test"})
    generator._get_json = AsyncMock(
        return_value={
            "id": "video_test",
            "status": "completed",
            "url": "data:video/mp4;base64,"
            + base64.b64encode(b"mp4").decode(),
        }
    )

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path="first.png",
            prompt="主体自然运动，镜头平稳。",
            output_path=str(tmp_path / "video.mp4"),
            aspect_ratio="9:16",
            duration=6,
            references=[ShotReference("image", "second.png", "")],
            seedance2_config='{"resolution": "1080p", "final_prompt": "configured motion"}',
        )

    assert result.status is VideoGenStatus.DONE
    create_url, payload = generator._post_json.await_args.args
    assert create_url == "https://gateway.invalid/v1/videos"
    assert payload == {
        "model": "kling-3.0-omni",
        "prompt": "configured motion",
        "duration": 8,
        "ratio": "9:16",
        "resolution": "1080p",
        "referenceImages": ["https://cdn.invalid/first.png"],
    }
    assert "metadata" not in payload
    assert "seconds" not in payload


@pytest.mark.asyncio
async def test_prompt_hubs_sd_generate_uses_videos_payload_and_media_limits(
    tmp_path,
) -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="sd2.0-720p-4img-fast",
        resolution="480p",
    )
    generator._relay_media_input = AsyncMock(
        side_effect=lambda value, **_kwargs: f"https://cdn.invalid/{value}"
    )
    generator._post_json = AsyncMock(return_value={"id": "video_test"})
    generator._get_json = AsyncMock(
        return_value={
            "id": "video_test",
            "status": "completed",
            "url": "data:video/mp4;base64,"
            + base64.b64encode(b"mp4").decode(),
        }
    )
    references = [
        *[ShotReference("image", f"ref-{index}.png", "") for index in range(1, 6)],
        *[ShotReference("video", f"ref-{index}.mp4", "") for index in range(1, 5)],
        *[ShotReference("audio", f"ref-{index}.mp3", "") for index in range(1, 3)],
    ]

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path="first.png",
            prompt="主体自然运动，镜头平稳。",
            output_path=str(tmp_path / "video.mp4"),
            aspect_ratio="9:16",
            duration=16,
            references=references,
            seedance2_config='{"final_prompt": "sd configured motion"}',
        )

    assert result.status is VideoGenStatus.DONE
    create_url, payload = generator._post_json.await_args.args
    assert create_url == "https://gateway.invalid/v1/videos"
    assert payload["model"] == "sd2.0-720p-4img-fast"
    assert payload["prompt"] == "sd configured motion"
    assert payload["duration"] == 15
    assert payload["ratio"] == "9:16"
    assert payload["resolution"] == "480p"
    assert payload["referenceImages"] == [
        "https://cdn.invalid/first.png",
        "https://cdn.invalid/ref-1.png",
        "https://cdn.invalid/ref-2.png",
        "https://cdn.invalid/ref-3.png",
    ]
    assert payload["referenceVideos"] == [
        "https://cdn.invalid/ref-1.mp4",
        "https://cdn.invalid/ref-2.mp4",
        "https://cdn.invalid/ref-3.mp4",
    ]
    assert payload["referenceAudios"] == ["https://cdn.invalid/ref-1.mp3"]
    assert "metadata" not in payload


@pytest.mark.asyncio
async def test_prompt_hubs_face_lock_keeps_identity_reference_and_forces_auto_face(tmp_path) -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="sd2.0-full-933-face",
        resolution="720p",
    )
    generator._relay_media_input = AsyncMock(
        side_effect=lambda value, **_kwargs: f"https://cdn.invalid/{value}"
    )
    generator._post_json = AsyncMock(return_value={"id": "video_test"})
    generator._get_json = AsyncMock(
        return_value={
            "id": "video_test",
            "status": "completed",
            "url": "data:video/mp4;base64," + base64.b64encode(b"mp4").decode(),
        }
    )

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path="control.png",
            prompt="人物自然转头。",
            output_path=str(tmp_path / "video.mp4"),
            aspect_ratio="9:16",
            duration=5,
            references=[ShotReference("image", "identity.png", "角色参考")],
        )

    assert result.status is VideoGenStatus.DONE
    _create_url, payload = generator._post_json.await_args.args
    assert payload["ratio"] == "9:16"
    assert "image" not in payload
    assert payload["referenceImages"] == [
        "https://cdn.invalid/control.png",
        "https://cdn.invalid/identity.png",
    ]
    assert payload["auto_face"] is True


@pytest.mark.asyncio
async def test_firefly_generate_uses_documented_payload_and_reference_total(
    tmp_path,
) -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="firefly-seedance2-fast-480p",
        resolution="480p",
    )
    generator._relay_media_input = AsyncMock(
        side_effect=lambda value, **_kwargs: f"https://cdn.invalid/{value}"
    )
    generator._post_json = AsyncMock(return_value={"id": "video_test"})
    generator._get_json = AsyncMock(
        return_value={
            "id": "video_test",
            "status": "completed",
            "url": "data:video/mp4;base64,"
            + base64.b64encode(b"mp4").decode(),
        }
    )
    references = [
        *[
            ShotReference("image", f"reference-{index}.png", "")
            for index in range(1, 8)
        ],
        *[
            ShotReference("video", f"reference-{index}.mp4", "")
            for index in range(1, 3)
        ],
        *[
            ShotReference("audio", f"reference-{index}.mp3", "")
            for index in range(1, 3)
        ],
    ]

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path="first.png",
            prompt="主体自然运动，镜头平稳。",
            output_path=str(tmp_path / "video.mp4"),
            aspect_ratio="9:16",
            duration=5,
            references=references,
        )

    assert result.status is VideoGenStatus.DONE
    assert (tmp_path / "video.mp4").read_bytes() == b"mp4"
    create_url, payload = generator._post_json.await_args.args
    assert create_url == "https://gateway.invalid/v1/videos"
    assert payload["model"] == "firefly-seedance2-fast-480p"
    assert payload["duration"] == 5
    assert payload["ratio"] == "9:16"
    assert payload["resolution"] == "480p"
    assert payload["auto_face"] is False
    assert len(payload["referenceImages"]) == 8
    assert len(payload["referenceVideos"]) == 2
    assert len(payload["referenceAudios"]) == 2
    assert sum(
        len(payload[field])
        for field in ("referenceImages", "referenceVideos", "referenceAudios")
    ) == 12
    assert "metadata" not in payload
    assert "seconds" not in payload


@pytest.mark.asyncio
async def test_newapi_video_reports_durable_lifecycle_events(tmp_path) -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="village-canvas-video",
        resolution="720p",
    )
    generator._post_json = AsyncMock(return_value={"id": "video_lifecycle"})
    generator._get_json = AsyncMock(
        return_value={
            "id": "video_lifecycle",
            "status": "completed",
            "url": "data:video/mp4;base64," + base64.b64encode(b"mp4").decode(),
            "usage": {"cost": {"credits": 5}},
        }
    )
    events: list[dict[str, object]] = []

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path=None,
            prompt="雨夜街头，镜头缓慢推进。",
            output_path=str(tmp_path / "video.mp4"),
            duration=5,
            on_task_event=events.append,
        )

    assert result.status is VideoGenStatus.DONE
    assert (tmp_path / "video.mp4").read_bytes() == b"mp4"
    stages = [event["stage"] for event in events]
    assert stages == [
        "submitted",
        "polling",
        "provider_cost",
        "upstream_completed",
        "downloading",
        "downloaded",
    ]
    assert all(event["provider_task_id"] == "video_lifecycle" for event in events)
    cost_event = next(event for event in events if event["stage"] == "provider_cost")
    assert cost_event["actual_cost"] == {"credits": 5}
    assert cost_event["cost_source"] == "result.usage.cost"


@pytest.mark.asyncio
async def test_newapi_video_recovers_completed_task_without_resubmitting(tmp_path) -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="village-canvas-video",
        resolution="720p",
    )
    generator._post_json = AsyncMock()
    generator._get_json = AsyncMock(
        return_value={
            "id": "video_recovered",
            "status": "completed",
            "url": "data:video/mp4;base64," + base64.b64encode(b"recovered").decode(),
        }
    )
    events: list[dict[str, object]] = []

    result = await generator.recover_task(
        task_id="video_recovered",
        output_path=str(tmp_path / "recovered.mp4"),
        on_task_event=events.append,
    )

    assert result.status is VideoGenStatus.DONE
    assert (tmp_path / "recovered.mp4").read_bytes() == b"recovered"
    generator._post_json.assert_not_awaited()
    assert [event["stage"] for event in events] == [
        "resuming",
        "polling",
        "upstream_completed",
        "downloading",
        "downloaded",
    ]


@pytest.mark.asyncio
async def test_newapi_video_rerun_replaces_existing_output_with_fresh_cut(
    tmp_path,
) -> None:
    """同一个 Beat 重新生成，新上游任务的成片必须真的覆盖本地旧成片。

    历史实现只看「输出文件在不在」就跳过下载：上游确实又生成了一次（已经
    计费），本地却继续留着上一次的旧视频，任务还报成功。只有来源标记证明
    文件正是当前这个上游任务的产物时，才允许跳过下载。
    """

    from novelvideo.generators.video.result_provenance import (
        read_result_provider_task,
    )

    output = tmp_path / "beat_09.mp4"
    output.write_bytes(b"stale-old-cut")

    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="operator-video-v1",
        resolution="720p",
    )
    generator._post_json = AsyncMock(return_value={"id": "task-fresh-cut"})
    generator._get_json = AsyncMock(
        return_value={"id": "task-fresh-cut", "status": "completed"}
    )
    downloaded: list[str] = []

    async def fake_download(*, output_path, **_kwargs):
        downloaded.append(str(output_path))
        Path(output_path).write_bytes(b"fresh-cut")
        return "https://gateway.invalid/v1/videos/task-fresh-cut/content"

    generator._download_completed_task_video = AsyncMock(side_effect=fake_download)

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        first = await generator.generate(
            image_path=None,
            prompt="fresh fixture",
            output_path=str(output),
            duration=4,
            poll_interval=0,
            max_polls=2,
        )
        second = await generator.generate(
            image_path=None,
            prompt="fresh fixture",
            output_path=str(output),
            duration=4,
            poll_interval=0,
            max_polls=2,
        )

    assert first.status is VideoGenStatus.DONE
    assert second.status is VideoGenStatus.DONE
    assert output.read_bytes() == b"fresh-cut"
    assert read_result_provider_task(output) == "task-fresh-cut"
    # 第一次必须下载；第二次是同一个上游任务的产物，可以跳过重复下载。
    assert downloaded == [str(output)]
    assert generator._download_completed_task_video.await_count == 1


@pytest.mark.asyncio
async def test_newapi_video_failed_task_without_reason_is_actionable(tmp_path) -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="operator-video-v1",
        resolution="720p",
    )
    generator._get_json = AsyncMock(
        return_value={"id": "video_failed_without_reason", "status": "failed"}
    )
    events: list[dict[str, object]] = []
    logs: list[str] = []

    result = await generator.recover_task(
        task_id="video_failed_without_reason",
        output_path=str(tmp_path / "failed.mp4"),
        max_polls=1,
        poll_interval=0,
        on_task_event=events.append,
        on_log=logs.append,
    )

    assert result.status is VideoGenStatus.FAILED
    assert "渠道没有返回失败原因" in str(result.error)
    assert "operator-video-v1" in str(result.error)
    assert "video_failed_without_reason" in str(result.error)
    assert events[-1]["stage"] == "upstream_failed"
    assert events[-1]["error_available"] is False
    assert events[-1]["response_keys"] == ["id", "status"]
    assert any("响应字段=id,status" in message for message in logs)


@pytest.mark.asyncio
async def test_newapi_video_transient_failed_status_keeps_same_task_until_completed(
    tmp_path,
) -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="minimax_h3",
        resolution="2k",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )
    generator._post_json = AsyncMock(return_value={"id": "task-transient"})
    generator._get_json = AsyncMock(
        side_effect=[
            {"id": "task-transient", "status": "failed", "progress": 65},
            {
                "id": "task-transient",
                "status": "completed",
                "progress": 100,
                "url": "data:video/mp4;base64," + base64.b64encode(b"recovered").decode(),
            },
        ]
    )
    events: list[dict[str, object]] = []

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path=None,
            prompt="transient failure fixture",
            output_path=str(tmp_path / "transient.mp4"),
            duration=6,
            poll_interval=0,
            max_polls=4,
            on_task_event=events.append,
        )

    assert result.status is VideoGenStatus.DONE
    assert result.task_id == "task-transient"
    assert generator._post_json.await_count == 1
    assert generator._get_json.await_count == 2
    assert (tmp_path / "transient.mp4").read_bytes() == b"recovered"
    assert any(event["stage"] == "early_failure_grace" for event in events)


@pytest.mark.asyncio
async def test_newapi_video_submit_timeout_reuses_idempotency_key(
    tmp_path,
) -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="minimax_h3",
        resolution="2k",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )
    generator.gateway_candidates = [
        {
            "name": "primary",
            "api_key": "test-key",
            "base_url": "https://gateway.invalid/v1",
        },
        {
            "name": "secondary",
            "api_key": "secondary-key",
            "base_url": "https://secondary.invalid/v1",
        },
    ]
    submit_timeout = NewApiVideoError(
        "submit timed out",
        stage="submit",
        url_path="/videos",
    )
    generator._post_json = AsyncMock(
        side_effect=[
            submit_timeout,
            {"id": "task-after-retry"},
        ]
    )
    generator._get_json = AsyncMock(
        return_value={
            "id": "task-after-retry",
            "status": "completed",
            "url": "data:video/mp4;base64,"
            + base64.b64encode(b"retry-result").decode(),
        }
    )
    idempotency_key = "video-timeout-fixture-01"

    with (
        patch(
            "novelvideo.generators.video_generator.get_usage_meter",
            return_value=_UsageMeter(),
        ),
        patch(
            "novelvideo.generators.video_generator.asyncio.sleep",
            new=AsyncMock(),
        ),
    ):
        result = await generator.generate(
            image_path=None,
            prompt="submit timeout fixture",
            output_path=str(tmp_path / "retry.mp4"),
            duration=6,
            poll_interval=0,
            max_polls=1,
            idempotency_key=idempotency_key,
        )

    assert result.status is VideoGenStatus.DONE
    assert result.task_id == "task-after-retry"
    assert generator.base_url == "https://gateway.invalid/v1"
    assert generator._post_json.await_count == 2
    assert all(
        call.kwargs["idempotency_key"] == idempotency_key
        for call in generator._post_json.await_args_list
    )
    assert all(
        call.args[0] == generator._post_json.await_args_list[0].args[0]
        for call in generator._post_json.await_args_list
    )
    assert (tmp_path / "retry.mp4").read_bytes() == b"retry-result"
