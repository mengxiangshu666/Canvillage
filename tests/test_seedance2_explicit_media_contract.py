import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from novelvideo.generators.video_generator import ShotReference


class _UsageMeter:
    async def reserve_current_model_call_credit(self, **_kwargs):
        return "reservation"

    async def refund_model_call_credit_reservation(self, *_args, **_kwargs):
        return None

    async def bump_model_call(self, **_kwargs):
        return None


def _completed_video_payload(content: bytes = b"video") -> dict[str, str]:
    return {
        "status": "completed",
        "url": "data:video/mp4;base64," + base64.b64encode(content).decode(),
    }


def test_seedance2_filter_accepts_mapping_and_uri_references(tmp_path: Path) -> None:
    from novelvideo.generators.video_generator import Seedance2VideoGenerator

    image = tmp_path / "reference.png"
    video = tmp_path / "reference.mp4"
    uri_image = tmp_path / "uri-reference.png"
    for path in (image, video, uri_image):
        path.write_bytes(b"fixture")

    generator = object.__new__(Seedance2VideoGenerator)
    _first, _last, references = generator._filter_explicit_references(
        mode="allReference",
        image_path=None,
        last_frame_path=None,
        references=[
            {"type": "image", "uri": str(image), "role": "角色参考"},
            {"kind": "video", "path": str(video), "role": "动作参考"},
            str(uri_image),
        ],
    )

    assert [generator._reference_kind(value) for value in references] == [
        "image",
        "video",
        "image",
    ]
    assert [generator._reference_path(value) for value in references] == [
        str(image),
        str(video),
        str(uri_image),
    ]


@pytest.mark.asyncio
async def test_seedance2_all_reference_without_media_fails_before_submit() -> None:
    from novelvideo.generators.video_generator import (
        Seedance2VideoGenerator,
        VideoGenStatus,
    )

    generator = Seedance2VideoGenerator(api_key="test-key")
    result = await generator.generate(
        image_path=None,
        prompt="按参考素材生成视频。",
        output_path="unused.mp4",
        references=[],
        gen_mode="allReference",
    )

    assert result.status is VideoGenStatus.FAILED
    assert result.error == "allReference requires at least one existing reference"


@pytest.mark.asyncio
async def test_newapi_image_reference_drops_stale_tail_from_video_v1_payload(
    tmp_path: Path,
) -> None:
    from novelvideo.generators.video_generator import NewApiVideoGenerator, VideoGenStatus

    image = tmp_path / "first.png"
    tail = tmp_path / "stale-tail.png"
    reference = tmp_path / "identity.png"
    for path in (image, tail, reference):
        path.write_bytes(b"fixture")

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
    generator._post_json = AsyncMock(return_value={"id": "image-reference-task"})
    generator._get_json = AsyncMock(return_value=_completed_video_payload())

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path=str(image),
            last_frame_path=str(tail),
            prompt="主体自然运动。",
            output_path=str(tmp_path / "image-reference.mp4"),
            duration=5,
            gen_mode="imageReference",
            references=[ShotReference("image", str(reference), "角色参考")],
            poll_interval=0,
            max_polls=1,
        )

    assert result.status is VideoGenStatus.DONE
    _submit_url, payload = generator._post_json.await_args.args[:2]
    assert payload["operation"] == "image_to_video"
    assert [item["url"] for item in payload["media_inputs"]] == [
        "https://media.test/first.png",
        "https://media.test/identity.png",
    ]
    assert all("stale-tail.png" not in item["url"] for item in payload["media_inputs"])


@pytest.mark.asyncio
async def test_newapi_preflight_rejects_undeclared_mode_before_relay_or_submit(
    tmp_path: Path,
) -> None:
    from novelvideo.generators.video_generator import NewApiVideoGenerator, VideoGenStatus

    image = tmp_path / "first.png"
    image.write_bytes(b"fixture")
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="mini-h3",
        resolution="2k",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )
    generator._relay_frame_input = AsyncMock()
    generator._relay_media_input = AsyncMock()
    generator._post_json = AsyncMock()

    result = await generator.generate(
        image_path=str(image),
        prompt="主体自然运动。",
        output_path=str(tmp_path / "unsupported.mp4"),
        duration=5,
        gen_mode="imageToVideo",
    )

    assert result.status is VideoGenStatus.FAILED
    assert result.error_metadata is not None
    assert result.error_metadata["error_code"] == "VIDEO_CAPABILITY_CONTRACT_INVALID"
    assert result.error_metadata["stage"] == "preflight"
    assert result.error_metadata["verification_stage"] == "contract"
    assert result.error_metadata["requested_mode"] == "imageToVideo"
    assert result.error_metadata["supported_modes"] == ["textToVideo"]
    generator._relay_frame_input.assert_not_awaited()
    generator._relay_media_input.assert_not_awaited()
    generator._post_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_firefly_image_to_video_uses_dedicated_image_field(tmp_path: Path) -> None:
    from novelvideo.generators.video_generator import NewApiVideoGenerator, VideoGenStatus

    image = tmp_path / "first.png"
    image.write_bytes(b"fixture")
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="firefly-seedance2-fast-480p",
        resolution="480p",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )
    generator._relay_frame_input = AsyncMock(return_value="https://media.test/first.png")
    generator._post_json = AsyncMock(return_value={"id": "firefly-image-task"})
    generator._get_json = AsyncMock(return_value=_completed_video_payload())

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path=str(image),
            prompt="主体自然运动。",
            output_path=str(tmp_path / "firefly-image.mp4"),
            duration=5,
            gen_mode="imageToVideo",
            references=[ShotReference("image", "other.png", "参考图")],
            poll_interval=0,
            max_polls=1,
        )

    assert result.status is VideoGenStatus.DONE
    _submit_url, payload = generator._post_json.await_args.args[:2]
    assert payload["image"] == "https://media.test/first.png"
    assert "referenceImages" not in payload


@pytest.mark.asyncio
async def test_flex_image_to_video_uses_dedicated_image_field(tmp_path: Path) -> None:
    from novelvideo.generators.video_generator import NewApiVideoGenerator, VideoGenStatus

    image = tmp_path / "first.png"
    image.write_bytes(b"fixture")
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="kling-3.0-omni",
        resolution="720p",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )
    generator._relay_frame_input = AsyncMock(return_value="https://media.test/first.png")
    generator._post_json = AsyncMock(return_value={"id": "flex-image-task"})
    generator._get_json = AsyncMock(return_value=_completed_video_payload())

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path=str(image),
            prompt="主体自然运动。",
            output_path=str(tmp_path / "flex-image.mp4"),
            duration=5,
            gen_mode="imageToVideo",
            poll_interval=0,
            max_polls=1,
        )

    assert result.status is VideoGenStatus.DONE
    _submit_url, payload = generator._post_json.await_args.args[:2]
    assert payload["image"] == "https://media.test/first.png"
    assert "referenceImages" not in payload


@pytest.mark.asyncio
async def test_firefly_text_to_video_omits_all_media(tmp_path: Path) -> None:
    from novelvideo.generators.video_generator import NewApiVideoGenerator, VideoGenStatus

    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="firefly-seedance2-fast-480p",
        resolution="480p",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )
    generator._relay_frame_input = AsyncMock()
    generator._relay_media_input = AsyncMock()
    generator._post_json = AsyncMock(return_value={"id": "firefly-text-task"})
    generator._get_json = AsyncMock(return_value=_completed_video_payload())

    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        result = await generator.generate(
            image_path="stale.png",
            last_frame_path="stale-tail.png",
            prompt="只使用文字生成。",
            output_path=str(tmp_path / "firefly-text.mp4"),
            duration=5,
            gen_mode="textToVideo",
            references=[
                SimpleNamespace(type="image", path="identity.png"),
                SimpleNamespace(type="video", path="motion.mp4"),
            ],
            poll_interval=0,
            max_polls=1,
        )

    assert result.status is VideoGenStatus.DONE
    _submit_url, payload = generator._post_json.await_args.args[:2]
    assert "image" not in payload
    assert "referenceImages" not in payload
    generator._relay_frame_input.assert_not_awaited()
    generator._relay_media_input.assert_not_awaited()


def test_profile_mode_projection_keeps_exact_and_source_video_contracts() -> None:
    from novelvideo.generators.video.direct_video_profiles import (
        resolve_direct_video_profile,
    )
    from novelvideo.generators.video_generator import NewApiVideoGenerator

    exact_profile = resolve_direct_video_profile("minimax_h3")
    source_profile = resolve_direct_video_profile("kling-v3-omni-v2v-create")

    assert NewApiVideoGenerator._profile_canvas_modes(exact_profile) == (
        "textToVideo",
        "imageToVideo",
        "firstLastFrame",
        "imageReference",
        "allReference",
        "videoEdit",
    )
    assert NewApiVideoGenerator._profile_canvas_modes(source_profile) == (
        "videoEdit",
    )


@pytest.mark.asyncio
async def test_firefly_first_last_mode_is_rejected_before_relay(tmp_path: Path) -> None:
    from novelvideo.generators.video_generator import NewApiVideoGenerator, VideoGenStatus

    image = tmp_path / "first.png"
    tail = tmp_path / "tail.png"
    image.write_bytes(b"fixture")
    tail.write_bytes(b"fixture")
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="firefly-seedance2-fast-480p",
        resolution="480p",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )
    generator._relay_frame_input = AsyncMock()
    generator._post_json = AsyncMock()

    result = await generator.generate(
        image_path=str(image),
        last_frame_path=str(tail),
        prompt="连续运动。",
        output_path=str(tmp_path / "firefly-first-last.mp4"),
        duration=5,
        gen_mode="firstLastFrame",
    )

    assert result.status is VideoGenStatus.FAILED
    assert result.error_metadata is not None
    assert result.error_metadata["requested_mode"] == "firstLastFrame"
    assert result.error_metadata["supported_modes"] == [
        "textToVideo",
        "imageToVideo",
        "allReference",
        "imageReference",
        "videoEdit",
    ]
    generator._relay_frame_input.assert_not_awaited()
    generator._post_json.assert_not_awaited()
