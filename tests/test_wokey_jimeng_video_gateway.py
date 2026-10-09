"""Contract coverage for Wokey's multimodal Seedance video routes."""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from novelvideo.freezone.video_node import (
    get_freezone_video_model_options,
)
from novelvideo.generators.video import VideoMode
from novelvideo.generators.video.direct_models import (
    DirectVideoModel,
    direct_video_model_option,
)
from novelvideo.generators.video_generator import NewApiVideoGenerator, VideoGenStatus
from novelvideo.model_gateway_settings import save_direct_video_models
from novelvideo.generators.video.direct_video_capability_cache import record_capability
WOKEY_MODELS = [
    "jimeng-seedance-2.0-fast",
    "jimeng-seedance-2.5",
]
WOKEY_MODES = (
    VideoMode.TEXT_TO_VIDEO,
    VideoMode.IMAGE_TO_VIDEO,
    VideoMode.FIRST_LAST_FRAME,
    VideoMode.REFERENCE_TO_VIDEO,
)


class _UsageMeter:
    async def reserve_current_model_call_credit(self, **_kwargs):
        return "reservation"

    async def refund_model_call_credit_reservation(self, *_args, **_kwargs):
        return None

    async def bump_model_call(self, **_kwargs):
        return None


def _completed_video() -> dict[str, str]:
    return {
        "id": "video_test",
        "status": "completed",
        "url": "data:video/mp4;base64," + base64.b64encode(b"mp4").decode(),
    }


def test_wokey_models_expose_the_verified_media_contract() -> None:
    fast_model = DirectVideoModel(
        registry_id="wokey-fast",
        label="Wokey · 即梦 Seedance 2.0 Fast（720P 极速主力）",
        upstream_model="jimeng-seedance-2.0-fast",
        base_url="https://api.wokey.ai/v1",
        api_key="test-key",
        enabled=True,
    )
    long_take_model = DirectVideoModel(
        registry_id="wokey-25",
        label="Wokey · 即梦 Seedance 2.5（高质量长镜头）",
        upstream_model="jimeng-seedance-2.5",
        base_url="https://api.wokey.ai/v1",
        api_key="test-key",
        enabled=True,
    )
    fast = fast_model.capability
    long_take = long_take_model.capability

    assert fast.modes == WOKEY_MODES
    assert fast.duration == tuple(range(4, 16))
    assert fast.resolution == ("720p",)
    assert fast.reference_limits.input_images == 2
    assert fast.reference_limits.reference_images == 9
    assert fast.reference_limits.reference_videos == 3
    assert fast.reference_limits.reference_audios == 3
    assert long_take.duration == tuple(range(4, 31))
    assert long_take.resolution == ("480p", "720p")
    option = direct_video_model_option(fast_model)
    assert option["provider"] == "direct"
    assert option["family"] == "wokey-jimeng"
    assert option["referenceLimits"]["allReference"] == {
        "image": 9,
        "video": 3,
        "audio": 3,
    }


def test_wokey_models_use_one_canonical_backend_across_video_selectors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from novelvideo import config
    from novelvideo.api.routes import generation

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    saved = save_direct_video_models([
        {
            "label": "Wokey · 即梦 Seedance 2.0 Fast（720P 极速主力）",
            "modelId": "jimeng-seedance-2.0-fast",
            "baseUrl": "https://api.wokey.ai/v1",
            "apiKey": "test-key",
            "enabled": True,
        },
        {
            "label": "Wokey · 即梦 Seedance 2.5（高质量长镜头）",
            "modelId": "jimeng-seedance-2.5",
            "baseUrl": "https://api.wokey.ai/v1",
            "apiKey": "test-key",
            "enabled": True,
        },
    ])
    for model_id in WOKEY_MODELS:
        record_capability(
            base_url="https://api.wokey.ai/v1",
            protocol="openai-video",
            upstream_model=model_id,
            capability={
                "verificationStatus": "contract-resolved",
                "modelFound": True,
                "discoveredModelCount": len(WOKEY_MODELS),
                "supportedProtocols": ["openai:video_generation"],
                # 真实渠道先点过检测连接才会有凭据记录；缓存缺了它不许冒充可用。
                "credentialValidation": {"status": "accepted", "httpStatus": 200},
                "checkedAt": "2026-10-03T00:00:00+00:00",
            },
        )

    freezone = next(
        item
        for item in get_freezone_video_model_options()
        if item["id"] == f"direct_{saved[0]['id']}"
    )
    assert freezone["label"] == "Wokey · 即梦 Seedance 2.0 Fast（720P 极速主力）"
    assert freezone["resolutionOptions"] == ["720p"]
    assert freezone["channel"] == "Wokey · 即梦"
    assert freezone["useCase"] == "默认主力：批量镜头、快速迭代"
    assert freezone["priceHint"] == "720p · $0.0396/秒"
    assert freezone["recommendation"] == "default"
    assert freezone["supportedModes"] == [
        "textToVideo",
        "imageToVideo",
        "firstLastFrame",
        "allReference",
        "videoEdit",
    ]

    mainline = {item.value: item for item in generation._api_video_backend_options()}[
        f"direct_{saved[1]['id']}"
    ]
    assert mainline.label == "Wokey · 即梦 Seedance 2.5（高质量长镜头）"
    assert mainline.min_duration == 4
    assert mainline.max_duration == 30
    assert mainline.resolution_options == ["480p", "720p"]
    assert mainline.supported_modes == [
        "first_frame",
        "first_last_frame",
        "multimodal_reference",
    ]
    assert mainline.reference_image_max == 9


@pytest.mark.asyncio
async def test_wokey_text_video_uses_the_documented_json_payload(tmp_path) -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="jimeng-seedance-2.0-fast",
        resolution="1080p",
    )
    generator._post_json = AsyncMock(return_value={"id": "video_test"})
    generator._get_json = AsyncMock(return_value=_completed_video())

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path="",
            prompt="雨夜霓虹街道，低机位跟拍一辆缓慢驶过的复古跑车。",
            output_path=str(tmp_path / "video.mp4"),
            aspect_ratio="21:9",
            duration=6,
            idempotency_key="video_text_fixture_01",
        )

    assert result.status is VideoGenStatus.DONE
    create_url, payload = generator._post_json.await_args.args
    assert create_url == "https://gateway.invalid/v1/videos"
    assert payload == {
        "model": "jimeng-seedance-2.0-fast",
        "mode": "text_to_video",
        "prompt": "雨夜霓虹街道，低机位跟拍一辆缓慢驶过的复古跑车。",
        "duration": 6,
        "ratio": "21:9",
        "video_resolution": "720p",
    }
    assert generator._post_json.await_args.kwargs == {
        "idempotency_key": "video_text_fixture_01"
    }


@pytest.mark.asyncio
async def test_completed_video_falls_back_to_unified_content_when_provider_url_is_unreachable(
    tmp_path,
) -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="jimeng-seedance-2.0-fast",
        resolution="720p",
    )
    generator._post_json = AsyncMock(return_value={"id": "video_test"})
    generator._get_json = AsyncMock(
        return_value={
            "id": "video_test",
            "status": "completed",
            "url": "https://newapi.prompt-hubs.com/result.mp4",
        }
    )
    generator._download_video = AsyncMock(side_effect=RuntimeError("direct unavailable"))

    async def save_from_content(task_id: str, output_path: str) -> bytes:
        assert task_id == "video_test"
        Path(output_path).write_bytes(b"mp4-content")
        return b"mp4-content"

    generator._download_task_content = AsyncMock(side_effect=save_from_content)
    logs: list[str] = []

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path="",
            prompt="雨夜街道，人物缓慢前行。",
            output_path=str(tmp_path / "video.mp4"),
            aspect_ratio="16:9",
            duration=5,
            on_log=logs.append,
        )

    assert result.status is VideoGenStatus.DONE
    assert result.video_url == "https://gateway.invalid/v1/videos/video_test/content?download=1"
    assert (tmp_path / "video.mp4").read_bytes() == b"mp4-content"
    generator._download_video.assert_awaited_once_with(
        "https://newapi.prompt-hubs.com/result.mp4", str(tmp_path / "video.mp4")
    )
    generator._download_task_content.assert_awaited_once_with(
        "video_test", str(tmp_path / "video.mp4")
    )
    assert any("统一内容端点" in message for message in logs)


@pytest.mark.asyncio
async def test_completed_video_uses_public_get_only_fallback_when_wg_content_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="http://wg.invalid/v1",
        model="s-videos-f-933-fast-480-2",
        resolution="480p",
    )
    monkeypatch.setenv("HK_API_PRIMARY", "https://public.invalid/v1")
    generator._download_video = AsyncMock(side_effect=RuntimeError("direct unavailable"))
    generator._download_task_content = AsyncMock(
        side_effect=RuntimeError("WireGuard content unavailable")
    )

    async def save_from_public_content(
        task_id: str,
        output_path: str,
        *,
        base_url: str,
        api_key: str,
    ) -> bytes:
        assert task_id == "video_public_fallback"
        assert base_url == "https://public.invalid/v1"
        assert api_key == "test-key"
        Path(output_path).write_bytes(b"public-content")
        return b"public-content"

    generator._download_task_content_from_gateway = AsyncMock(
        side_effect=save_from_public_content
    )

    result_url = await generator._download_completed_task_video(
        task_id="video_public_fallback",
        video_url="https://provider.invalid/result.mp4",
        output_path=str(tmp_path / "recovered.mp4"),
    )

    assert result_url == (
        "https://public.invalid/v1/videos/video_public_fallback/content?download=1"
    )
    assert (tmp_path / "recovered.mp4").read_bytes() == b"public-content"
    generator._download_task_content.assert_awaited_once()
    generator._download_task_content_from_gateway.assert_awaited_once()


@pytest.mark.asyncio
async def test_recover_completed_task_queries_public_get_only_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="http://wg.invalid/v1",
        model="s-videos-f-933-fast-480-2",
        resolution="480p",
    )
    monkeypatch.setenv("HK_API_PRIMARY", "https://public.invalid/v1")

    async def get_task(_url: str) -> dict[str, str]:
        if generator.base_url == "http://wg.invalid/v1":
            raise RuntimeError("WireGuard task query unavailable")
        return {
            "id": "completed_public_query",
            "status": "completed",
            "url": "data:video/mp4;base64," + base64.b64encode(b"public-query").decode(),
        }

    generator._get_json = AsyncMock(side_effect=get_task)
    result = await generator.recover_task(
        task_id="completed_public_query",
        output_path=str(tmp_path / "public-query.mp4"),
        max_polls=1,
        poll_interval=0,
    )

    assert result.status is VideoGenStatus.DONE
    assert (tmp_path / "public-query.mp4").read_bytes() == b"public-query"
    assert generator._get_json.await_count == 2
    assert generator.base_url == "https://public.invalid/v1"


@pytest.mark.asyncio
async def test_recover_completed_task_uses_unified_content_after_provider_url_failure(
    tmp_path,
) -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="s-videos-f-933-fast-480-2",
        resolution="480p",
    )
    generator._get_json = AsyncMock(
        return_value={
            "id": "completed_task",
            "status": "completed",
            "url": "https://newapi.prompt-hubs.com/result.mp4",
        }
    )
    generator._download_video = AsyncMock(side_effect=RuntimeError("direct unavailable"))

    async def save_from_content(task_id: str, output_path: str) -> bytes:
        assert task_id == "completed_task"
        Path(output_path).write_bytes(b"mp4-content")
        return b"mp4-content"

    generator._download_task_content = AsyncMock(side_effect=save_from_content)
    logs: list[str] = []

    result = await generator.recover_task(
        task_id="completed_task",
        output_path=str(tmp_path / "recovered.mp4"),
        max_polls=1,
        poll_interval=0,
        on_log=logs.append,
    )

    assert result.status is VideoGenStatus.DONE
    assert result.video_url == (
        "https://gateway.invalid/v1/videos/completed_task/content?download=1"
    )
    assert (tmp_path / "recovered.mp4").read_bytes() == b"mp4-content"
    assert any("统一内容端点" in message for message in logs)


@pytest.mark.asyncio
async def test_wokey_multimodal_reference_uses_multipart_uploads(tmp_path) -> None:
    reference = tmp_path / "identity.png"
    reference.write_bytes(b"image-reference")
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="jimeng-seedance-2.5",
        resolution="720p",
    )
    generator._post_json = AsyncMock()
    generator._post_multipart = AsyncMock(return_value={"id": "video_test"})
    generator._get_json = AsyncMock(return_value=_completed_video())

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path="",
            prompt="@图片1 的角色向镜头缓慢转头，保持服装和光线一致。",
            output_path=str(tmp_path / "video.mp4"),
            aspect_ratio="16:9",
            duration=5,
            idempotency_key="video_reference_fixture_01",
            references=[
                SimpleNamespace(type="image", path=str(reference), role="角色参考")
            ],
        )

    assert result.status is VideoGenStatus.DONE
    generator._post_json.assert_not_awaited()
    create_url, fields, files = generator._post_multipart.await_args.args
    assert create_url == "https://gateway.invalid/v1/videos"
    assert fields == {
        "model": "jimeng-seedance-2.5",
        "mode": "multimodal_reference",
        "prompt": "@图片1 的角色向镜头缓慢转头，保持服装和光线一致。",
        "duration_seconds": 5,
        "ratio": "16:9",
        "resolution": "720p",
        "idempotency_key": "video_reference_fixture_01",
    }
    assert [(field, content) for field, _name, content, _type in files] == [
        ("image[]", b"image-reference")
    ]
    assert generator._post_multipart.await_args.kwargs == {
        "idempotency_key": "video_reference_fixture_01"
    }


@pytest.mark.asyncio
async def test_wokey_first_frame_compiles_to_supported_multimodal_contract(tmp_path) -> None:
    """A single canvas first frame must not be sent as unsupported image_to_video."""

    first_frame = tmp_path / "first-frame.png"
    first_frame.write_bytes(b"first-frame")
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="jimeng-seedance-2.0-fast",
        resolution="720p",
    )
    generator._post_json = AsyncMock()
    generator._post_multipart = AsyncMock(return_value={"id": "video_test"})
    generator._get_json = AsyncMock(return_value=_completed_video())

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path=str(first_frame),
            prompt="@图片1 作为首帧，小黑猫原地打太极。",
            output_path=str(tmp_path / "video.mp4"),
            aspect_ratio="16:9",
            duration=15,
            idempotency_key="32ad883685d84e89",
        )

    assert result.status is VideoGenStatus.DONE
    generator._post_json.assert_not_awaited()
    create_url, fields, files = generator._post_multipart.await_args.args
    assert create_url == "https://gateway.invalid/v1/videos"
    assert fields == {
        "model": "jimeng-seedance-2.0-fast",
        "mode": "multimodal_reference",
        "prompt": "@图片1 作为首帧，小黑猫原地打太极。",
        "duration_seconds": 15,
        "ratio": "16:9",
        "resolution": "720p",
        "idempotency_key": "32ad883685d84e89",
    }
    assert [(field, content) for field, _name, content, _type in files] == [
        ("image[]", b"first-frame")
    ]


@pytest.mark.asyncio
async def test_wokey_explicit_text_mode_ignores_stale_image_and_uses_json(tmp_path) -> None:
    """An explicit text-to-video mode must win over a stale canvas image."""

    stale_image = tmp_path / "stale-canvas-frame.png"
    stale_image.write_bytes(b"stale-frame")
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="jimeng-seedance-2.0-fast",
        resolution="720p",
    )
    generator._post_json = AsyncMock(return_value={"id": "video_text_mode"})
    generator._post_multipart = AsyncMock()
    generator._get_json = AsyncMock(return_value=_completed_video())

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path=str(stale_image),
            prompt="雨夜霓虹街道，低机位跟拍一辆缓慢驶过的复古跑车。",
            output_path=str(tmp_path / "video.mp4"),
            aspect_ratio="21:9",
            duration=6,
            idempotency_key="video_text_mode_fixture",
            gen_mode="textToVideo",
            references=[
                SimpleNamespace(type="image", path=str(tmp_path / "other.png")),
            ],
        )

    assert result.status is VideoGenStatus.DONE
    generator._post_multipart.assert_not_awaited()
    create_url, payload = generator._post_json.await_args.args
    assert create_url == "https://gateway.invalid/v1/videos"
    assert payload == {
        "model": "jimeng-seedance-2.0-fast",
        "mode": "text_to_video",
        "prompt": "雨夜霓虹街道，低机位跟拍一辆缓慢驶过的复古跑车。",
        "duration": 6,
        "ratio": "21:9",
        "video_resolution": "720p",
    }


@pytest.mark.asyncio
async def test_wokey_explicit_image_mode_submits_only_the_first_image(tmp_path) -> None:
    """Image-to-video must not promote unrelated references or a tail frame."""

    first_frame = tmp_path / "first-frame.png"
    first_frame.write_bytes(b"first-frame")
    second_frame = tmp_path / "second-frame.png"
    second_frame.write_bytes(b"second-frame")
    tail_frame = tmp_path / "tail-frame.png"
    tail_frame.write_bytes(b"tail-frame")
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="jimeng-seedance-2.5",
        resolution="720p",
    )
    generator._post_json = AsyncMock()
    generator._post_multipart = AsyncMock(return_value={"id": "video_image_mode"})
    generator._get_json = AsyncMock(return_value=_completed_video())

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path=str(first_frame),
            last_frame_path=str(tail_frame),
            prompt="首帧中的角色抬头看向镜头。",
            output_path=str(tmp_path / "video.mp4"),
            aspect_ratio="16:9",
            duration=5,
            idempotency_key="video_image_mode_fixture",
            gen_mode="imageToVideo",
            references=[
                SimpleNamespace(type="image", path=str(second_frame)),
                SimpleNamespace(type="video", path=str(tmp_path / "source.mp4")),
            ],
        )

    assert result.status is VideoGenStatus.DONE
    generator._post_json.assert_not_awaited()
    create_url, fields, files = generator._post_multipart.await_args.args
    assert create_url == "https://gateway.invalid/v1/videos"
    assert fields == {
        "model": "jimeng-seedance-2.5",
        "mode": "multimodal_reference",
        "prompt": "首帧中的角色抬头看向镜头。",
        "duration_seconds": 5,
        "ratio": "16:9",
        "resolution": "720p",
        "idempotency_key": "video_image_mode_fixture",
    }
    assert [(field, content) for field, _name, content, _type in files] == [
        ("image[]", b"first-frame")
    ]


@pytest.mark.asyncio
async def test_h3_explicit_text_mode_omits_all_media_from_video_v1_payload(tmp_path) -> None:
    """H3's video.v1 text mode stays text-only despite stale media inputs."""

    stale_image = tmp_path / "stale-frame.png"
    stale_image.write_bytes(b"stale-frame")
    tail_frame = tmp_path / "tail-frame.png"
    tail_frame.write_bytes(b"tail-frame")
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="minimax_h3",
        resolution="768",
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
    generator._post_json = AsyncMock(return_value={"id": "h3-text-mode"})
    generator._get_json = AsyncMock(
        return_value={
            "status": "completed",
            "url": "data:video/mp4;base64," + base64.b64encode(b"h3-text").decode(),
        }
    )

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path=str(stale_image),
            last_frame_path=str(tail_frame),
            prompt="只根据文字描述生成一段镜头。",
            output_path=str(tmp_path / "h3-text.mp4"),
            aspect_ratio="16:9",
            duration=6,
            gen_mode="textToVideo",
            references=[
                SimpleNamespace(type="image", path=str(tmp_path / "reference.png")),
                SimpleNamespace(type="video", path=str(tmp_path / "reference.mp4")),
                SimpleNamespace(type="audio", path=str(tmp_path / "reference.mp3")),
            ],
            poll_interval=0,
            max_polls=1,
        )

    assert result.status is VideoGenStatus.DONE
    _, payload = generator._post_json.await_args.args[:2]
    assert payload["version"] == "video.v1"
    assert payload["operation"] == "text_to_video"
    assert "media_inputs" not in payload
    assert "image_path" not in payload
    assert "reference_images" not in payload
