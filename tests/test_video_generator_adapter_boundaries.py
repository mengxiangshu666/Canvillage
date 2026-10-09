from __future__ import annotations

import pytest

from novelvideo.generators import video_generator as legacy
from novelvideo.generators.video.base import (
    VideoBackend,
    VideoGenResult,
    VideoGenStatus,
    VideoGeneratorBase,
)
from novelvideo.generators.video.grok import GrokVideoGenerator
from novelvideo.generators.video.seedance import SeedanceVideoGenerator


def test_video_contracts_keep_legacy_module_compatibility() -> None:
    assert legacy.VideoGenStatus is VideoGenStatus
    assert legacy.VideoBackend is VideoBackend
    assert legacy.VideoGenResult is VideoGenResult
    assert legacy.VideoGeneratorBase is VideoGeneratorBase


def test_grok_generator_is_owned_by_dedicated_adapter_module() -> None:
    assert legacy.GrokVideoGenerator is GrokVideoGenerator
    assert GrokVideoGenerator.__module__ == "novelvideo.generators.video.grok"


def test_seedance_generator_is_owned_by_dedicated_adapter_module() -> None:
    assert legacy.SeedanceVideoGenerator is SeedanceVideoGenerator
    assert SeedanceVideoGenerator.__module__ == "novelvideo.generators.video.seedance"


def test_video_factory_preserves_grok_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    monkeypatch.setenv("VILLAGE_CANVAS_ALLOW_DIRECT_VIDEO_PROVIDER", "1")

    generator = legacy.create_video_generator(VideoBackend.GROK_720)

    assert isinstance(generator, GrokVideoGenerator)


def test_video_factory_preserves_seedance_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VILLAGE_CANVAS_ALLOW_DIRECT_VIDEO_PROVIDER", "1")

    generator = legacy.create_video_generator(
        "seedance_fast",
        api_key="test-key",
    )

    assert isinstance(generator, SeedanceVideoGenerator)


@pytest.mark.asyncio
async def test_grok_last_frame_rejection_is_unchanged() -> None:
    generator = GrokVideoGenerator(api_key="test-key")

    result = await generator.generate(
        image_path="first.png",
        prompt="move forward",
        output_path="output.mp4",
        last_frame_path="last.png",
    )

    assert result.status is VideoGenStatus.FAILED
    assert result.error == "Grok 720 does not support keyframe/首尾帧模式"


@pytest.mark.asyncio
async def test_seedance_request_and_download_contract_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import httpx

    calls: list[dict[str, object]] = []
    video_url = "https://cdn.example/video.mp4"

    class FakeResponse:
        def __init__(
            self,
            *,
            status_code: int = 200,
            payload: dict[str, object] | None = None,
            content: bytes = b"",
        ):
            self.status_code = status_code
            self._payload = payload or {}
            self.content = content
            self.text = ""

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, json, headers):
            calls.append({"method": "post", "url": url, "json": json, "headers": headers})
            return FakeResponse(payload={"id": "task-1"})

        async def get(self, url, *, headers=None):
            calls.append({"method": "get", "url": url, "headers": headers})
            if url == video_url:
                return FakeResponse(content=b"seedance-video")
            return FakeResponse(
                payload={
                    "status": "succeeded",
                    "content": {"video_url": video_url},
                }
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    output_path = tmp_path / "seedance.mp4"
    generator = SeedanceVideoGenerator(
        model="doubao-seedance-test",
        generate_audio=True,
        api_key="test-key",
    )

    result = await generator.generate(
        image_path="https://assets.example/first.png",
        prompt="人物缓慢向前走",
        output_path=str(output_path),
        aspect_ratio="adaptive",
        duration=5,
        poll_interval=0,
    )

    assert result.status is VideoGenStatus.DONE
    assert result.task_id == "task-1"
    assert output_path.read_bytes() == b"seedance-video"
    submit = next(call for call in calls if call["method"] == "post")
    assert submit["url"].endswith("/contents/generations/tasks")
    assert submit["json"]["model"] == "doubao-seedance-test"
    assert submit["json"]["ratio"] == "adaptive"
    assert submit["json"]["duration"] == 5
    assert submit["json"]["generate_audio"] is True
