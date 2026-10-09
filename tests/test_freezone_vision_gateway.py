from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from pydantic_ai import BinaryContent, ImageUrl
from pydantic_ai.models.test import TestModel
from pydantic_ai.output import PromptedOutput

from novelvideo import config
from novelvideo.freezone import vision_gateway
from novelvideo.freezone.vision_gateway import (
    VisionInput,
    call_freezone_vision_model,
    image_media_type,
    resolve_freezone_vision_model,
    resolve_freezone_video_story_model,
)


class StructuredVisionFixture(BaseModel):
    label: str


@pytest.fixture(autouse=True)
def _direct_vision_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    from novelvideo.generators import direct_models

    def resolve(kind: str, _model_ref: str | None = None):
        if kind != "vision":
            return None
        upstream = (
            os.environ.get("FREEZONE_VIDEO_STORY_MODEL")
            or os.environ.get("FREEZONE_VISION_MODEL")
            or "gemini-3-flash"
        )
        return SimpleNamespace(
            catalog_id=f"direct/{upstream.replace('/', '-')}",
            upstream_model=upstream,
            label="Fixture vision",
            base_url="https://vision.example/v1",
            api_key="fixture-key",
            protocol="openai-compatible",
            enabled=True,
        )

    monkeypatch.setattr(direct_models, "resolve_direct_model", resolve)
    monkeypatch.setattr(
        direct_models,
        "get_direct_pydantic_model",
        lambda kind, _model_ref=None, **_kwargs: (
            TestModel(custom_output_text="视觉解析结果") if kind == "vision" else None
        ),
    )


def test_video_story_model_uses_dedicated_quality_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FREEZONE_VISION_MODEL", "gemini-3-flash")
    monkeypatch.setenv("FREEZONE_VIDEO_STORY_MODEL", "gemini-3-flash")

    assert resolve_freezone_vision_model() == "direct/gemini-3-flash"
    assert resolve_freezone_video_story_model() == "direct/gemini-3-flash"


def test_vision_model_without_config_does_not_inject_hidden_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FREEZONE_VISION_MODEL", raising=False)
    monkeypatch.delenv("FREEZONE_VIDEO_STORY_MODEL", raising=False)
    monkeypatch.setattr(
        "novelvideo.generators.direct_models.resolve_direct_model",
        lambda _kind, _model=None: None,
    )

    with pytest.raises(ValueError, match="直连视觉模型"):
        resolve_freezone_vision_model()
    with pytest.raises(ValueError, match="直连视觉模型"):
        resolve_freezone_video_story_model()


@pytest.mark.asyncio
async def test_vision_gateway_uses_pydantic_agent_and_logical_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FREEZONE_VISION_MODEL", "custom-vision-model")
    from novelvideo.generators import direct_models

    monkeypatch.setattr(
        direct_models,
        "get_direct_pydantic_model",
        lambda *_args, **_kwargs: TestModel(custom_output_text="视觉解析结果"),
    )

    model, output = await call_freezone_vision_model(
        prompt="分析图片",
        images=[VisionInput(data=b"image", media_type="image/png")],
    )

    assert model == "direct/custom-vision-model"
    assert output == "视觉解析结果"


@pytest.mark.asyncio
async def test_vision_gateway_uses_validated_prompted_output_for_structured_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("FREEZONE_VISION_MODEL", "configured-vision-model")

    class FakeAgent:
        def __init__(self, *_args, **kwargs) -> None:
            captured.update(kwargs)

        async def run(self, _parts):
            return SimpleNamespace(output=StructuredVisionFixture(label="结构化结果"))

    monkeypatch.setattr("pydantic_ai.Agent", FakeAgent)
    monkeypatch.setattr(
        config,
        "get_newapi_text_pydantic_model",
        lambda *_args, **_kwargs: object(),
    )

    _model, output = await call_freezone_vision_model(
        prompt="分析图片",
        images=[VisionInput(data=b"image", media_type="image/png")],
        structured_output_type=StructuredVisionFixture,
    )

    assert output == StructuredVisionFixture(label="结构化结果")
    assert isinstance(captured["output_type"], PromptedOutput)
    assert captured["output_retries"] == 2


@pytest.mark.asyncio
async def test_vision_gateway_prefers_inline_images_when_relay_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[bytes, str, int | None]] = []
    monkeypatch.setenv("FREEZONE_VISION_MODEL", "configured-vision-model")
    from novelvideo.generators import direct_models

    monkeypatch.setattr(
        direct_models,
        "get_direct_pydantic_model",
        lambda *_args, **_kwargs: TestModel(custom_output_text="公网图像解析结果"),
    )

    monkeypatch.setattr(
        config,
        "get_newapi_text_pydantic_model",
        lambda *_args, **_kwargs: TestModel(custom_output_text="公网图像解析结果"),
    )
    monkeypatch.setenv("VILLAGE_CANVAS_MEDIA_RELAY_UPLOAD_URL", "http://10.66.66.1:8782/upload")
    monkeypatch.setenv("VILLAGE_CANVAS_MEDIA_RELAY_TOKEN", "fixture-token")

    def fake_upload(data: bytes, *, ext: str = "png", ttl: int | None = None, **_kwargs):
        captured.append((data, ext, ttl))
        return "http://156.245.244.194:8782/media/fixture.png"

    monkeypatch.setattr(
        "novelvideo.storage.media_relay.upload_image_bytes",
        fake_upload,
    )

    _model, output = await call_freezone_vision_model(
        prompt="分析图片",
        images=[VisionInput(data=b"image", media_type="image/webp")],
    )

    assert output == "公网图像解析结果"
    assert captured == []


@pytest.mark.asyncio
async def test_vision_gateway_retries_with_relay_when_inline_transport_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs: list[list[object]] = []
    uploaded: list[tuple[bytes, str, int | None]] = []
    monkeypatch.setenv("FREEZONE_VISION_MODEL", "configured-vision-model")

    class FakeAgent:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run(self, parts):
            runs.append(list(parts))
            if len(runs) == 1:
                raise RuntimeError(
                    "Failed to download image, Please ensure that the image_url "
                    "is publicly accessible over the internet."
                )
            return SimpleNamespace(output="公网图像解析结果")

    monkeypatch.setattr("pydantic_ai.Agent", FakeAgent)
    monkeypatch.setattr(
        config,
        "get_newapi_text_pydantic_model",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setenv("VILLAGE_CANVAS_MEDIA_RELAY_UPLOAD_URL", "http://10.66.66.1:8782/upload")
    monkeypatch.setenv("VILLAGE_CANVAS_MEDIA_RELAY_TOKEN", "fixture-token")

    def fake_upload(data: bytes, *, ext: str = "png", ttl: int | None = None, **_kwargs):
        uploaded.append((data, ext, ttl))
        return "http://156.245.244.194:8782/media/fixture.webp"

    monkeypatch.setattr(
        "novelvideo.storage.media_relay.upload_image_bytes",
        fake_upload,
    )

    _model, output = await call_freezone_vision_model(
        prompt="分析图片",
        images=[VisionInput(data=b"image", media_type="image/webp")],
    )

    assert output == "公网图像解析结果"
    assert isinstance(runs[0][0], BinaryContent)
    assert isinstance(runs[1][0], ImageUrl)
    assert runs[0][-1] == runs[1][-1] == "分析图片"
    assert uploaded == [(b"image", "webp", 600)]


@pytest.mark.asyncio
async def test_vision_gateway_retries_a_transient_timeout_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    monkeypatch.setenv("FREEZONE_VISION_MODEL", "configured-vision-model")

    class FakeAgent:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run(self, _parts):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("Request timed out")
            return SimpleNamespace(output="重试成功")

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("pydantic_ai.Agent", FakeAgent)
    monkeypatch.setattr(
        config,
        "get_newapi_text_pydantic_model",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(vision_gateway.asyncio, "sleep", fake_sleep)

    _model, output = await call_freezone_vision_model(
        prompt="分析图片",
        images=[VisionInput(data=b"image", media_type="image/png")],
    )

    assert output == "重试成功"
    assert calls == 2


@pytest.mark.asyncio
async def test_vision_gateway_does_not_use_unregistered_fallback_after_primary_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FREEZONE_VISION_MODEL", "configured-vision-model")

    class FailingAgent:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run(self, _parts):
            raise RuntimeError("Error code: 401 - Invalid token")

    monkeypatch.setattr("pydantic_ai.Agent", FailingAgent)
    monkeypatch.setattr(
        config,
        "get_newapi_text_pydantic_model",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setenv("FREEZONE_VISION_FALLBACK_BASE_URL", "https://api.wokey.ai/v1")
    monkeypatch.setenv("FREEZONE_VISION_FALLBACK_API_KEY", "fixture-token")
    monkeypatch.setenv("FREEZONE_VISION_FALLBACK_MODEL", "claude-haiku-4-5")

    with pytest.raises(RuntimeError, match="Invalid token"):
        await call_freezone_vision_model(
            prompt="分析图片",
            images=[VisionInput(data=b"image", media_type="image/png", label="源视频关键帧 1/1")],
            enable_wokey_fallback=True,
        )


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("frame.png", "image/png"),
        ("frame.jpg", "image/jpeg"),
        ("frame.JPEG", "image/jpeg"),
        ("frame.webp", "image/webp"),
        ("frame.gif", "image/gif"),
        ("frame", "image/png"),
    ],
)
def test_image_media_type(path: str, expected: str) -> None:
    assert image_media_type(path) == expected


def test_vision_input_keeps_each_label_next_to_its_image_and_task_after_sources():
    images = [VisionInput(b"opening", label="图片1：起始站位"),
              VisionInput(b"released", label="图片2：已释放接触")]
    parts = [BinaryContent(data=image.data, media_type=image.media_type) for image in images]
    assert vision_gateway._build_vision_parts("比较接触与支撑变化，不推断速度", images, parts) == [
        images[0].label, parts[0], images[1].label, parts[1], "比较接触与支撑变化，不推断速度",
    ]
