"""Compatibility factory for video generator adapters."""

from __future__ import annotations

import os
from typing import Optional

from .base import VideoBackend, VideoGeneratorBase
from .comfyui import ComfyUIVideoGenerator
from .grok import GrokVideoGenerator
from .huimeng import HuimengVideoGenerator
from .mock import MockVideoGenerator
from .newapi import NewApiVideoGenerator
from .newapi_video_routing import (
    parse_newapi_video_backend,
    resolve_configured_newapi_video_model,
)
from .seedance import SeedanceVideoGenerator
from .seedance2 import Seedance2VideoGenerator
from .wan26 import Wan26VideoGenerator

__all__ = ["create_video_generator"]


def _allow_direct_video_provider() -> bool:
    return os.environ.get(
        "VILLAGE_CANVAS_ALLOW_DIRECT_VIDEO_PROVIDER", ""
    ).strip().lower() in {"1", "true", "yes", "on"}


def _coerce_direct_cloud_video_backend_to_newapi(value: str) -> str:
    text = str(value or "").strip().lower()
    if _allow_direct_video_provider():
        return text
    direct_cloud_backends = {
        "seedance_fast",
        "seedance_pro",
        "seedance_pro_silent",
        "seedance_2",
        "wan26",
        "grok_720",
    }
    if text in direct_cloud_backends or text.startswith("huimeng_"):
        return "newapi_jimeng-seedance-2.0-fast"
    return text


def _coerce_video_backend_value(backend: VideoBackend | str | None) -> str:
    if backend is None:
        backend = os.environ.get("VIDEO_BACKEND", "comfyui")
    if isinstance(backend, VideoBackend):
        return _coerce_direct_cloud_video_backend_to_newapi(backend.value)
    value = str(backend).strip().lower()
    value = "comfyui" if value == "jimeng" else value
    return _coerce_direct_cloud_video_backend_to_newapi(value)


def create_video_generator(
    backend: Optional[VideoBackend | str] = None,
    use_mock: bool = False,
    workflow_type: Optional[str] = None,
    **kwargs,
) -> VideoGeneratorBase:
    """创建视频生成器。

    Args:
        backend: 视频后端选择
            - newapi_<model>: newAPI 视频模型，如 newapi_seedance-1.0-pro-fast
            - huimeng_<model>: HuiMeng 视频模型，如 huimeng_seedance-2.0-fast
            - SEEDANCE_FAST: Seedance 1.0 Pro Fast（火山方舟）
            - SEEDANCE_PRO: Seedance 1.5 Pro 有声（火山方舟）
            - SEEDANCE_PRO_SILENT: Seedance 1.5 Pro 无声（火山方舟）
            - COMFYUI: ComfyUI 本地服务（1.0 世代后端）
            - WAN26: 阿里云 DashScope Wan2.6-i2v-flash
            - GROK_720: xAI Grok Imagine Video 720p
        use_mock: 兼容旧接口，True 时使用 MockVideoGenerator
        workflow_type: ComfyUI 工作流类型 ("gguf" 或 "fp8")，默认从环境变量读取
        **kwargs: 传递给具体生成器的参数

    Returns:
        视频生成器实例

    环境变量:
        VIDEO_BACKEND: 默认后端
            (newapi_seedance-1.0-pro-fast/huimeng_seedance-2.0-fast/comfyui/...)
        HUIMENGI_API_KEY: HuiMeng API 密钥
        COMFYUI_WORKFLOW: ComfyUI 工作流类型 (gguf/fp8)
        DASHSCOPE_API_KEY: Wan2.6 API 密钥
        VOLCENGINE_VISUAL_API_KEY: Seedance API 密钥
        COMFYUI_ADDRESS: ComfyUI 服务器地址
        XAI_API_KEY: Grok 视频生成 API 密钥
    """
    from novelvideo.config import SEEDANCE_FAST_MODEL, SEEDANCE_PRO_MODEL

    # 兼容旧接口
    if use_mock:
        return MockVideoGenerator(**kwargs)

    backend_str = _coerce_video_backend_value(backend)
    from novelvideo.generators.video.direct_models import resolve_direct_video_model

    direct_model = resolve_direct_video_model(backend_str)
    if direct_model is not None:
        if not direct_model.enabled:
            raise ValueError(f"Direct video model is disabled: {direct_model.label}")
        if not direct_model.runtime_ready:
            raise ValueError(
                f"Direct video model protocol is not runtime-ready: {direct_model.label}"
            )
        direct_options = direct_model.generator_options(kwargs)
        adapter_family = str(direct_options.pop("adapter_family", "")).strip()
        if adapter_family:
            from novelvideo.generators.video.generic_video_adapter import (
                GenericVideoAdapterGenerator,
            )

            return GenericVideoAdapterGenerator(**direct_options, adapter_family=adapter_family)
        return NewApiVideoGenerator(**direct_options)

    # ``parameters`` is a direct-model capability extension. Legacy built-in
    # generators have no such constructor argument, so discard it only after
    # the direct registry had a chance to consume it.
    kwargs.pop("parameters", None)

    newapi_model = parse_newapi_video_backend(backend_str)
    if newapi_model:
        newapi_model = resolve_configured_newapi_video_model(newapi_model)
        return NewApiVideoGenerator(model=newapi_model, **kwargs)

    from novelvideo.generators.huimengi import parse_huimeng_video_backend

    huimeng_model = parse_huimeng_video_backend(backend_str)
    if huimeng_model:
        return HuimengVideoGenerator(model=huimeng_model, **kwargs)

    try:
        backend_enum = VideoBackend(backend_str)
    except ValueError:
        if backend is None:
            backend_enum = VideoBackend.COMFYUI
        else:
            raise ValueError(f"Unknown video backend: {backend_str}") from None

    # 创建对应的生成器
    if backend_enum == VideoBackend.SEEDANCE_FAST:
        return SeedanceVideoGenerator(
            model=SEEDANCE_FAST_MODEL, generate_audio=False, **kwargs
        )
    elif backend_enum == VideoBackend.SEEDANCE_PRO:
        return SeedanceVideoGenerator(
            model=SEEDANCE_PRO_MODEL, generate_audio=True, **kwargs
        )
    elif backend_enum == VideoBackend.SEEDANCE_PRO_SILENT:
        return SeedanceVideoGenerator(
            model=SEEDANCE_PRO_MODEL, generate_audio=False, **kwargs
        )
    elif backend_enum == VideoBackend.COMFYUI:
        # 从环境变量读取工作流类型
        if workflow_type is None:
            workflow_type = os.environ.get("COMFYUI_WORKFLOW", "gguf")
        return ComfyUIVideoGenerator(workflow_type=workflow_type, **kwargs)
    elif backend_enum == VideoBackend.SEEDANCE_2:
        return Seedance2VideoGenerator(**kwargs)
    elif backend_enum == VideoBackend.LTX23:
        return ComfyUIVideoGenerator(workflow_type="ltx23", **kwargs)
    elif backend_enum == VideoBackend.WAN26:
        return Wan26VideoGenerator(**kwargs)
    elif backend_enum == VideoBackend.GROK_720:
        return GrokVideoGenerator(**kwargs)
    else:
        raise ValueError(f"Unknown video backend: {backend_str}")
