"""Shared runtime contracts for video generator adapters."""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from enum import Enum
import re
from typing import Optional


class VideoGenStatus(Enum):
    """视频生成状态。"""

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class VideoBackend(Enum):
    """视频生成后端。"""

    SEEDANCE_FAST = "seedance_fast"  # Seedance 1.0 Pro Fast
    SEEDANCE_PRO = "seedance_pro"  # Seedance 1.5 Pro 有声
    SEEDANCE_PRO_SILENT = "seedance_pro_silent"  # Seedance 1.5 Pro 无声
    SEEDANCE_2 = "seedance_2"  # Seedance 2.0（v2.0 主力）
    COMFYUI = "comfyui"  # ComfyUI 本地服务（1.0 世代后端）
    WAN26 = "wan26"  # 阿里云 DashScope Wan2.6-i2v-flash
    LTX23 = "ltx23"  # Lightricks LTX-Video 2.3 22B
    GROK_720 = "grok_720"  # xAI Grok Imagine Video 720p


@dataclass
class VideoGenResult:
    """视频生成结果。"""

    status: VideoGenStatus
    video_url: Optional[str] = None
    video_path: Optional[str] = None
    last_frame_url: Optional[str] = None
    last_frame_path: Optional[str] = None
    task_id: Optional[str] = None
    provider_task_id: Optional[str] = None
    error: Optional[str] = None
    error_metadata: Optional[dict[str, object]] = None
    duration_seconds: float = 0.0


class VideoGeneratorBase(ABC):
    """视频生成器基类。

    定义 Image-to-Video 生成的标准接口。
    """

    async def generate(
        self,
        image_path: Optional[str],
        prompt: str,
        output_path: str,
        aspect_ratio: str = "16:9",
        duration: float = 5.0,
        poll_interval: float = 5.0,
        max_polls: int = 60,
    ) -> VideoGenResult:
        """完整生成流程：提交 + 轮询 + 下载。"""

        raise NotImplementedError("Subclass should implement generate()")


def normalize_video_aspect_ratio(value: object) -> str:
    """Keep provider adaptive mode while rejecting malformed ratios."""

    ratio = str(value or "").strip().lower()
    is_pixel_size = bool(re.fullmatch(r"[1-9][0-9]{1,4}x[1-9][0-9]{1,4}", ratio))
    return ratio if ":" in ratio or ratio == "adaptive" or is_pixel_size else "9:16"


__all__ = [
    "VideoBackend",
    "VideoGenResult",
    "VideoGenStatus",
    "VideoGeneratorBase",
    "normalize_video_aspect_ratio",
]
