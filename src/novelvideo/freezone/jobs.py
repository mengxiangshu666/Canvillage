"""Pure async jobs for freezone single-image gen / edit.

These are called from the task backend runners and from unit-test harnesses.
They never touch the queue backend, the API layer, or task_state.

Provider selection (since v1.1):
- `provider` / `model` / `quality` get threaded into
  `get_grid_generation_config(provider_override=, model_override=)` for the
          image generation/edit path so the caller can pick the supported Village
          providers: `direct` / `newapi` / `huimeng` / `openrouter` / `openai`.
- A legacy `provider="volcengine"` branch remains for old canvases/scripts, but
  the Freezone UI no longer exposes it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field, model_validator

from novelvideo.freezone.paths import (
    ensure_video_source_path,
    output_path_for_job,
    outputs_dir,
)
from .video_helpers import (
    _aspect_to_dims,
    _atempo_filter,
    _fallback_subtitle_box,
    _ffmpeg_error_tail,
    _normalized_box_to_pixels,
    _safe_box_from_pixels,
    normalize_video_cut_segments,
)

logger = logging.getLogger(__name__)

_BUNDLED_FFMPEG_DIR = Path(__file__).resolve().parents[3] / "runtime" / "ffmpeg"


def _bundled_media_binary(name: str) -> str | None:
    """Prefer the shipped media binaries over a possibly broken PATH shim."""

    executable = _BUNDLED_FFMPEG_DIR / f"{name}.exe"
    if executable.is_file():
        return str(executable)
    return shutil.which(name)


class CompletedVideoDownloadPending(RuntimeError):
    """The provider finished rendering, but its result could not be retrieved.

    The durable task backend catches this separately from a generation failure:
    it keeps the existing provider task id and retries only the poll/download
    leg.  No new paid render request is submitted.
    """

    def __init__(self, message: str, *, provider_task_id: str) -> None:
        super().__init__(message)
        self.provider_task_id = str(provider_task_id or "").strip()


class VideoSubmissionPending(RuntimeError):
    """提交响应未知时保留任务，等待同幂等键再次确认。"""

    def __init__(self, message: str, *, idempotency_key: str) -> None:
        super().__init__(message)
        self.idempotency_key = str(idempotency_key or "").strip()


class FreezoneVideoGenerationError(RuntimeError):
    """Video failure that retains the provider's credential-safe diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        provider_error_metadata: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_error_metadata = dict(provider_error_metadata or {})


def _format_video_generation_failure(
    error: object,
    metadata: object,
) -> str:
    """Keep the user-facing error actionable without exposing request data."""

    message = str(error or "unknown error").strip() or "unknown error"
    if not isinstance(metadata, dict):
        return f"freezone video generation failed: {message}"
    code = str(metadata.get("error_code") or "").strip()
    stage = str(metadata.get("stage") or "").strip()
    status = metadata.get("http_status")
    suggested = str(metadata.get("suggested_action") or "").strip()
    context: list[str] = []
    if code:
        context.append(f"诊断码={code}")
    if stage:
        context.append(f"阶段={stage}")
    if isinstance(status, int):
        context.append(f"HTTP={status}")
    if suggested:
        context.append(f"建议={suggested[:240]}")
    suffix = f"（{'；'.join(context)}）" if context else ""
    return f"freezone video generation failed: {message}{suffix}"


class VideoStoryShot(BaseModel):
    """Validated semantic unit returned by the video-story vision model."""

    shot: int = Field(ge=1)
    start_time: float = Field(ge=0)
    end_time: float = Field(ge=0)
    duration: float | None = Field(default=None, ge=0)
    visual_description: str = ""
    narrative: str = ""
    shot_size: str = ""
    camera_angle: str = ""
    camera_movement: str = ""
    focus_depth: str = ""
    lighting: str = ""
    background_music: str = ""
    voice_sound: str = ""
    image_prompt: str = ""
    motion_prompt: str = ""
    keyframes: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_timing(self):
        if self.end_time < self.start_time:
            raise ValueError("end_time must be greater than or equal to start_time")
        if self.duration is None:
            self.duration = round(self.end_time - self.start_time, 3)
        return self


class VideoStoryAnalysis(BaseModel):
    """Structured video-story contract enforced inside the model gateway."""

    title: str = ""
    summary: str = ""
    duration: float | None = Field(default=None, ge=0)
    shots: list[VideoStoryShot] = Field(min_length=1, max_length=12)


async def run_freezone_gen(
    *,
    project_dir: Path,
    job_id: str,
    prompt: str,
    aspect_ratio: str = "1:1",
    image_size: str | None = None,
    reference_paths: Optional[list[str]] = None,
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    quality: Optional[str] = None,
    advanced_settings: Optional[dict[str, Any]] = None,
    output_task_type: str = "freezone_gen",
    on_progress: Callable[[float], None] | None = None,
    on_provider_event: Callable[[dict[str, object]], None] | None = None,
) -> Path:
    """text → image (with optional reference images).

    Routes through nanobanana_grid for the supported Village providers.

    ``on_progress`` 只在上游**真正跨过**一个阶段时报（提交前 / 上游返回），
    不把一次 HTTP 调用拆成假的小数点。没传回调时行为与从前完全一致。
    """
    out = output_path_for_job(project_dir, output_task_type or "freezone_gen", job_id)
    out.parent.mkdir(parents=True, exist_ok=True)

    def _emit(value: float) -> None:
        if on_progress is None:
            return
        try:
            on_progress(value)
        except Exception:
            # 进度是旁路观测，报不出去绝不能把生成搞挂。
            logger.debug("freezone gen on_progress failed", exc_info=True)

    _emit(0.2)

    # Routing (v1.2):
    #   provider == "volcengine"  → Volcengine Seedream (text-only path; refs ignored)
    #   anything else (including None default) → nanobanana_grid:
    #     - with refs → generate_reference_edit_image
    #     - no refs   → generate_text_to_image  (NEW v1.2)
    if (provider or "").lower() == "volcengine":
        result = await _run_volcengine_text_to_image(
            out=out,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            image_size=image_size or "2K",
        )
        _emit(0.8)
        return result

    if (provider or "").lower() == "direct":
        from novelvideo.generators.direct_image_models import (
            generate_direct_image,
            resolve_direct_image_model,
        )

        direct_model = resolve_direct_image_model(model)
        if direct_model is None:
            raise RuntimeError(
                f"configured direct image model not found: {model or 'default'}"
            )
        result = await generate_direct_image(
            model=direct_model,
            prompt=prompt,
            output_path=out,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            quality=quality,
            reference_paths=reference_paths or (),
            advanced_settings=advanced_settings,
            on_provider_event=on_provider_event,
        )
        _emit(0.8)
        return result

    from novelvideo.config import get_grid_generation_config
    from novelvideo.generators.nanobanana_grid import (
        generate_reference_edit_image,
        generate_text_to_image,
    )

    cfg = get_grid_generation_config(
        provider_override=provider,
        model_override=model,
        image_size_override=image_size,
    )
    if reference_paths:
        await generate_reference_edit_image(
            prompt=prompt,
            reference_images=reference_paths,
            output_path=str(out),
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            quality=quality,
            api_key=api_key,
            config=cfg,
        )
    else:
        await generate_text_to_image(
            prompt=prompt,
            output_path=str(out),
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            quality=quality,
            api_key=api_key,
            config=cfg,
        )
    _emit(0.8)
    return out


async def run_freezone_mask_edit(
    *,
    project_dir: Path,
    job_id: str,
    base_path: str,
    mask_path: str,
    prompt: str,
    aspect_ratio: str = "1:1",
    image_size: str | None = None,
    quality: str = "medium",
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    """Masked erase/edit via the same provider routing used by Freezone image edit."""
    out = output_path_for_job(project_dir, "freezone_mask_edit", job_id)
    out.parent.mkdir(parents=True, exist_ok=True)

    def _emit(value: float) -> None:
        if on_progress is None:
            return
        try:
            on_progress(value)
        except Exception:
            logger.debug("freezone mask edit on_progress failed", exc_info=True)

    _emit(0.2)

    base_p = Path(base_path)
    mask_p = Path(mask_path)
    if not base_p.exists():
        raise FileNotFoundError(f"base not found: {base_p}")
    if not mask_p.exists():
        raise FileNotFoundError(f"mask not found: {mask_p}")

    from novelvideo.config import get_grid_generation_config
    from novelvideo.generators.nanobanana_grid import generate_reference_edit_image
    from novelvideo.utils.error_redaction import redact_secrets

    cfg = get_grid_generation_config(
        provider_override=provider,
        model_override=model,
        image_size_override=image_size,
    )
    provider_name = str(cfg.get("provider") or provider or "newapi").strip().lower()
    mask_prompt = (
        f"{prompt}\n\n"
        "Use Image 1 as the source image. Image 2 is the same image with a translucent RED "
        "highlight painted over the region to edit. Edit ONLY the red-highlighted region; the "
        "red highlight is just an annotation marking where to work and must NOT appear in the "
        "output. Preserve all pixels outside the highlighted region — composition, identity, "
        "lighting, and texture — as much as possible."
    ).strip()
    if (provider or "").lower() == "direct":
        from novelvideo.generators.direct_image_models import (
            generate_direct_image,
            resolve_direct_image_model,
        )

        direct_model = resolve_direct_image_model(model)
        if direct_model is None:
            raise RuntimeError(
                f"configured direct image model not found: {model or 'default'}"
            )
        try:
            result = await generate_direct_image(
                model=direct_model,
                prompt=mask_prompt,
                output_path=out,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                quality=quality,
                reference_paths=[str(base_p), str(mask_p)],
            )
        except Exception as exc:
            from novelvideo.shared.provider_errors import preserve_provider_diagnostics

            # run_core reads provider_error_metadata off the escaping exception,
            # so the re-wrapped failure must carry the direct channel's
            # structured diagnosis forward.
            raise preserve_provider_diagnostics(
                RuntimeError(f"direct 图像擦除失败：{redact_secrets(exc)}"), exc
            ) from exc
        _emit(0.8)
        return result

    try:
        await generate_reference_edit_image(
            prompt=mask_prompt,
            reference_images=[str(base_p), str(mask_p)],
            output_path=str(out),
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            quality=quality,
            api_key=api_key,
            config=cfg,
        )
    except Exception as exc:
        raise RuntimeError(
            f"{provider_name} 图像擦除失败：{redact_secrets(exc)}"
        ) from exc
    if not out.exists():
        raise RuntimeError(f"{provider_name} 图像擦除未生成输出文件")
    _emit(0.8)
    return out


async def _run_volcengine_text_to_image(
    *,
    out: Path,
    prompt: str,
    aspect_ratio: str,
    image_size: str,
) -> Path:
    """Volcengine Seedream 4.0 text→image (no provider/model override)."""
    from novelvideo.generators.image_generator import create_image_generator

    width, height = _aspect_to_dims(aspect_ratio, image_size)
    generator = create_image_generator()
    result = await generator.generate(
        prompt=prompt,
        output_path=str(out),
        width=width,
        height=height,
    )
    if not result or not result.success:
        err = result.error if result else "unknown error"
        raise RuntimeError(f"Volcengine text→image generation failed: {err}")
    if not out.exists():
        if result.image_base64:
            import base64

            out.write_bytes(base64.b64decode(result.image_base64))
        else:
            raise RuntimeError("Volcengine text→image produced no file or bytes")
    return out


async def run_freezone_edit(
    *,
    project_dir: Path,
    job_id: str,
    prompt: str,
    base_path: str,
    extra_reference_paths: Optional[list[str]] = None,
    aspect_ratio: str = "2:3",
    image_size: str | None = None,
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    quality: Optional[str] = None,
    advanced_settings: Optional[dict[str, Any]] = None,
    output_task_type: str = "freezone_edit",
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    """image + reference + prompt → new image.

    v1 doesn't enforce a hard mask — most providers (nanobanana, OpenAI image
    edit) treat reference images plus a prompt as soft guidance. The base
    image is passed first in the references list so the model anchors on it.

    ``on_progress`` 语义与 {@link run_freezone_gen} 一致：只报真实跨过的阶段。
    """
    out = output_path_for_job(project_dir, output_task_type or "freezone_edit", job_id)
    out.parent.mkdir(parents=True, exist_ok=True)

    def _emit(value: float) -> None:
        if on_progress is None:
            return
        try:
            on_progress(value)
        except Exception:
            logger.debug("freezone edit on_progress failed", exc_info=True)

    _emit(0.2)

    refs: list[str] = [base_path]
    if extra_reference_paths:
        refs.extend(extra_reference_paths)

    if (provider or "").lower() == "direct":
        from novelvideo.generators.direct_image_models import (
            generate_direct_image,
            resolve_direct_image_model,
        )

        direct_model = resolve_direct_image_model(model)
        if direct_model is None:
            raise RuntimeError(
                f"configured direct image model not found: {model or 'default'}"
            )
        result = await generate_direct_image(
            model=direct_model,
            prompt=prompt,
            output_path=out,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            quality=quality,
            reference_paths=refs,
            advanced_settings=advanced_settings,
        )
        _emit(0.8)
        return result

    from novelvideo.config import get_grid_generation_config
    from novelvideo.generators.nanobanana_grid import generate_reference_edit_image

    cfg = get_grid_generation_config(
        provider_override=provider,
        model_override=model,
        image_size_override=image_size,
    )
    await generate_reference_edit_image(
        prompt=prompt,
        reference_images=refs,
        output_path=str(out),
        aspect_ratio=aspect_ratio,
        image_size=image_size,
        quality=quality,
        api_key=api_key,
        config=cfg,
    )
    _emit(0.8)
    return out


def ensure_freezone_dirs(project_dir: Path) -> None:
    """Create freezone subdirectories on first use; cheap and idempotent."""
    (project_dir / "freezone" / "_uploads").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_gen").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_edit").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_upscale").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_video_gen").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_video_compose").mkdir(
        parents=True, exist_ok=True
    )
    outputs_dir(project_dir, "freezone_extract").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_analyze").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_mask_edit").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_video_erase").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_video_upscale").mkdir(
        parents=True, exist_ok=True
    )
    outputs_dir(project_dir, "freezone_audio_separate").mkdir(
        parents=True, exist_ok=True
    )
    outputs_dir(project_dir, "freezone_audio_speech").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_audio_eleven_music").mkdir(
        parents=True, exist_ok=True
    )
    outputs_dir(project_dir, "freezone_image_to_3gs").mkdir(parents=True, exist_ok=True)
    outputs_dir(project_dir, "freezone_video_cut").mkdir(parents=True, exist_ok=True)


FREEZONE_VIDEO_RESOLUTION_MAP: dict[str, tuple[int, int]] = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
}

FREEZONE_VIDEO_UPSCALE_LONG_EDGE: dict[str, int] = {
    "1080p": 1920,
    "2k": 2560,
    "4k": 3840,
}


def _video_upscale_filter(resolution: str, denoise_strength: str) -> str:
    target = FREEZONE_VIDEO_UPSCALE_LONG_EDGE.get(resolution.lower())
    if not target:
        raise ValueError(f"unsupported video upscale resolution: {resolution}")
    filters = [
        f"scale='if(gte(iw,ih),{target},-2)':'if(gte(iw,ih),-2,{target})':flags=lanczos"
    ]
    denoise = (denoise_strength or "1x").lower()
    if denoise not in {"1x", "2x", "none"}:
        raise ValueError(f"unsupported denoise_strength: {denoise_strength}")
    if denoise != "none":
        filters.append(_denoise_filter(denoise))
    filters.append("unsharp=5:5:0.55:3:3:0.25")
    filters.append("format=yuv420p")
    return ",".join(filters)


def _denoise_filter(denoise_strength: str) -> str:
    """降噪滤镜 → 当前 ffmpeg 认的那一个。

    hqdn3d 是首选（空间+时间联合降噪，锐化前的标准前置）。便携版把它裁掉了，就落到
    atadenoise：时间维自适应平均，参数是逐平面的阈值而不是 hqdn3d 的「空间强度+时间
    强度」，所以不能照抄数字。1x 用它的默认阈值，2x 把 A/B 两档一起翻倍——
    `2x` 在原先的语义里就是「更狠的降噪」，不是「两遍降噪」。
    """
    strength = 2.0 if denoise_strength == "2x" else 1.0
    if _has_ffmpeg_filter("hqdn3d"):
        # 保持原参数不动：这两组数字是跟着画质调出来的，不是可推导的。
        return "hqdn3d=2.0:2.0:6:6" if strength > 1 else "hqdn3d=1.2:1.2:4:4"
    if _has_ffmpeg_filter("atadenoise"):
        a = 0.02 * strength
        b = 0.04 * strength
        return f"atadenoise=0a={a}:0b={b}:1a={a}:1b={b}:2a={a}:2b={b}"
    raise RuntimeError(
        "当前 ffmpeg 既没有 hqdn3d 也没有 atadenoise，无法执行降噪；"
        "需要降噪请换一个带这两个滤镜之一的 ffmpeg，或把降噪强度设为 none"
    )


async def run_freezone_video_upscale(
    *,
    project_dir: Path,
    job_id: str,
    source_path: str,
    resolution: str = "1080p",
    frame_interpolation: str = "none",
    denoise_strength: str = "1x",
) -> tuple[Path, dict]:
    """Basic ffmpeg video enhancement: scale, denoise, sharpen, preserve audio."""
    if frame_interpolation != "none":
        raise ValueError("basic video upscale only supports frame_interpolation='none'")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH; install via brew/apt")

    src = Path(source_path)
    if not src.exists():
        raise FileNotFoundError(f"video source not found: {src}")

    out = outputs_dir(project_dir, "freezone_video_upscale") / f"{job_id}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = _video_upscale_filter(resolution, denoise_strength)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-vf",
        vf,
        *_video_encoder_args("slow", crf=18),
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(out),
    ]
    proc = await asyncio.to_thread(
        subprocess.run,
        cmd,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg video upscale failed: {proc.stderr[-1000:]}")
    meta = {
        "backend": "ffmpeg",
        "resolution": resolution,
        "frame_interpolation": frame_interpolation,
        "denoise_strength": denoise_strength,
        "video_filter": vf,
    }
    return out, meta


async def _run_cmd(cmd: list[str]) -> None:
    proc = await asyncio.to_thread(
        subprocess.run,
        cmd,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise RuntimeError(stderr[-1000:] or f"command failed: {' '.join(cmd)}")


# H.264 编码器优先级：优先“质量档 + crf”能表达的那几个，最后才落到兼容档。
# 便携版捆绑的 ffmpeg 是 **--disable-libx264** 构建（实测 2026-09-12：encoders 列表
# 里有 libopenh264 / h264_mf / mpeg4，独缺 libx264），所以写死 libx264 的滤镜链路
# 在真机上会以 "Unknown encoder 'libx264'" 直接失败。这里按可用性探测一次并缓存，
# 与 task_backend/runners/video.py 的 compose 那条链同一套口径。
_H264_ENCODER_PREFERENCE = ("libx264", "libopenh264", "h264_mf", "mpeg4")
_h264_encoder_cache: str | None = None


def _resolve_h264_encoder() -> str:
    """挑一个当前 ffmpeg 真的有的 H.264 编码器（进程内缓存）。"""
    global _h264_encoder_cache
    if _h264_encoder_cache is not None:
        return _h264_encoder_cache
    probe = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    listing = "\n".join(str(value or "") for value in (probe.stdout, probe.stderr))
    for name in _H264_ENCODER_PREFERENCE:
        if re.search(rf"(?m)^\s*V\S*\s+{re.escape(name)}\b", listing):
            _h264_encoder_cache = name
            return name
    raise RuntimeError(
        "当前 ffmpeg 没有可用的 H.264 编码器；已检查 "
        + ", ".join(_H264_ENCODER_PREFERENCE)
    )


def _video_encoder_args(
    quality: str = "veryfast", *, crf: int | None = None
) -> list[str]:
    """视频编码 × 质量档 → 该 ffmpeg 认的参数。

    预设与 `-crf` 只有 libx264 认；其余编码器只吃码率。把口径收在这里，调用点就
    不用各自判断「我给的这个档它认不认」——各调用点原来写死 libx264 与预设，改成
    探测之后，预设 / crf / 码率三个口径都从这一个函数出去。
    """
    encoder = _resolve_h264_encoder()
    if encoder == "libx264":
        args = ["-c:v", "libx264", "-preset", quality]
        return [*args, "-crf", str(crf)] if crf is not None else args
    if encoder in {"libopenh264", "h264_mf"}:
        rate = "6M" if quality in {"slow", "medium"} else "4M"
        return ["-c:v", encoder, "-b:v", rate]
    return ["-c:v", encoder, "-q:v", "3"]


# 滤镜可用性：同一个便携版 ffmpeg 不只是缺 libx264，`--disable-*` 之外还裁掉了
# hqdn3d 与 delogo（实测 2026-09-12：`-h filter=hqdn3d` / `-h filter=delogo` 都报
# Unknown filter）。滤镜不认的表现和缺编码器一样——整条链路直接失败，而不是降级。
# 所以凡是有替代品的滤镜，都先探测再选。
_ffmpeg_filter_cache: dict[str, bool] = {}


def _has_ffmpeg_filter(name: str) -> bool:
    """这个 ffmpeg 认不认该滤镜（进程内缓存）。

    逐滤镜问 `-h filter=<name>`，而不是去解析 `-filters` 的表格：表格的列宽和
    对齐在各构建里并不统一，而 `-h` 的输出在「有」和「没有」两种情况下是完全
    不同的两句话，判起来不会因为多一列少一列而翻车。
    """
    cached = _ffmpeg_filter_cache.get(name)
    if cached is not None:
        return cached
    probe = subprocess.run(
        ["ffmpeg", "-hide_banner", "-h", f"filter={name}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    listing = "\n".join(str(value or "") for value in (probe.stdout, probe.stderr))
    found = bool(re.search(rf"(?m)^\s*Filter\s+{re.escape(name)}\s*$", listing))
    _ffmpeg_filter_cache[name] = found
    return found


async def _probe_has_audio(source_path: str) -> bool:
    proc = await asyncio.to_thread(
        subprocess.run,
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            source_path,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


async def _strip_unrequested_video_audio(
    video_path: Path,
    *,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """Remove a provider-generated audio stream without re-encoding video.

    A few upstream video contracts force native audio on the wire even when
    the canvas selected external narration/dialogue.  Keep the provider video
    frames bit-for-bit and remux only the video stream into a sibling temp
    file, then replace the result atomically.  Invalid/non-video fixtures and
    hosts without ffmpeg are left untouched so test/recovery artifacts remain
    readable; real providers are expected to have both ffmpeg and ffprobe.
    """

    if not video_path.exists() or video_path.stat().st_size <= 0:
        return False
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        if on_log:
            on_log("未找到 ffmpeg/ffprobe，跳过未请求原生音轨清理")
        return False
    try:
        has_audio = await _probe_has_audio(str(video_path))
    except (OSError, RuntimeError, subprocess.SubprocessError):
        # A malformed fixture or an interrupted provider download is not a
        # reason to destroy the only local result.  The normal output checks
        # will report the artifact to the caller as before.
        return False
    if not has_audio:
        return False

    temp_path = video_path.with_name(
        f"{video_path.stem}.audio-sanitized.tmp{video_path.suffix}"
    )
    try:
        await _run_cmd(
            [
                ffmpeg,
                "-y",
                "-i",
                str(video_path),
                "-map",
                "0:v:0",
                "-map_metadata",
                "0",
                "-c:v",
                "copy",
                "-an",
                "-movflags",
                "+faststart",
                str(temp_path),
            ]
        )
        if not temp_path.exists() or temp_path.stat().st_size <= 0:
            raise RuntimeError("ffmpeg produced an empty muted video")
        os.replace(temp_path, video_path)
        if on_log:
            on_log("已移除未请求的模型原生音轨，保留画面并交给外部音频链")
        return True
    finally:
        temp_path.unlink(missing_ok=True)


async def _render_gap_clip(
    *,
    output_path: Path,
    duration: float,
    width: int,
    height: int,
    fps: int,
    background_color: str,
) -> None:
    await _run_cmd(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={background_color}:s={width}x{height}:r={fps}",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t",
            f"{duration:.3f}",
            *_video_encoder_args("veryfast"),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-shortest",
            str(output_path),
        ]
    )


async def _render_video_clip(
    *,
    source_path: str,
    output_path: Path,
    source_start: float,
    duration: float,
    width: int,
    height: int,
    fps: int,
    background_color: str,
    keep_original_audio: bool,
    volume: float,
    muted: bool,
    speed: float = 1.0,
) -> None:
    speed = min(4.0, max(0.25, float(speed or 1.0)))
    output_duration = duration / speed
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={background_color},"
        f"fps={fps},setpts=PTS/{speed:.6f}"
    )
    has_audio = (
        keep_original_audio and (not muted) and await _probe_has_audio(source_path)
    )

    if has_audio:
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{source_start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            source_path,
            "-vf",
            video_filter,
            *_video_encoder_args("veryfast"),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-af",
            f"volume={volume:.4f},{_atempo_filter(speed)}",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{source_start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            source_path,
            "-f",
            "lavfi",
            "-t",
            f"{output_duration:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            video_filter,
            *_video_encoder_args("veryfast"),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    await _run_cmd(cmd)


async def _render_video_segment(
    *,
    source_path: str,
    output_path: Path,
    source_start: float,
    source_end: float,
) -> None:
    """Cut one exact segment out of a source video, geometry untouched.

    Deliberately narrower than `_render_video_clip`: that one scales/pads onto a
    fixed compose canvas and can re-time the clip.  Neither belongs in "give me
    the seconds between A and B of this file" — a shot cut out for reference or
    comparison must not silently come back at a different resolution, and it
    must not be sped up.  The frame rate travels with the source for the same
    reason.
    """
    start = max(0.0, float(source_start))
    duration = float(source_end) - start
    if duration <= 0:
        raise ValueError("segment end must be greater than its start")

    # `-ss` before `-i` is the fast seek; re-encoding still lands on the exact
    # frame instead of the preceding keyframe.  (If a real run ever shows a
    # visible offset, the slow-but-exact fallback is moving `-ss` after `-i`.)
    base = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        source_path,
    ]
    if await _probe_has_audio(source_path):
        cmd = [
            *base,
            *_video_encoder_args("veryfast"),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    else:
        cmd = [
            *base,
            "-f",
            "lavfi",
            "-t",
            f"{duration:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            *_video_encoder_args("veryfast"),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    await _run_cmd(cmd)


async def _render_audio_clip(
    *,
    source_path: str,
    output_path: Path,
    source_start: float,
    duration: float,
    volume: float,
    speed: float = 1.0,
) -> None:
    speed = min(4.0, max(0.25, float(speed or 1.0)))
    await _run_cmd(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{source_start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            source_path,
            "-vn",
            "-af",
            f"volume={volume:.4f},{_atempo_filter(speed)}",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(output_path),
        ]
    )


async def _concat_media_segments(segment_paths: list[Path], output_path: Path) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".txt", delete=False
    ) as handle:
        for path in segment_paths:
            safe_path = str(path).replace("'", "'\\''")
            handle.write(f"file '{safe_path}'\n")
        list_path = Path(handle.name)
    try:
        await _run_cmd(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                *_video_encoder_args("veryfast"),
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
    finally:
        list_path.unlink(missing_ok=True)


async def _mix_audio_tracks(
    *,
    base_video_path: Path,
    final_output_path: Path,
    audio_items: list[dict[str, Any]],
    temp_dir: Path,
) -> None:
    audio_inputs: list[tuple[Path, float]] = []
    for index, item in enumerate(audio_items):
        if bool(item.get("muted")):
            continue
        volume = float(item.get("volume", 1.0))
        if volume <= 0:
            continue
        source_start = float(item.get("source_start", 0.0) or 0.0)
        source_end = float(item.get("source_end", 0.0) or 0.0)
        duration = source_end - source_start
        if duration <= 0:
            continue
        audio_path = temp_dir / f"audio_track_{index:03d}.m4a"
        await _render_audio_clip(
            source_path=str(item["source_path"]),
            output_path=audio_path,
            source_start=source_start,
            duration=duration,
            volume=volume,
            speed=float(item.get("speed", 1.0) or 1.0),
        )
        audio_inputs.append((audio_path, float(item.get("timeline_start", 0.0) or 0.0)))

    if not audio_inputs:
        shutil.move(str(base_video_path), str(final_output_path))
        return

    cmd = ["ffmpeg", "-y", "-i", str(base_video_path)]
    filter_parts: list[str] = []
    labels = ["[0:a]"]
    for idx, (audio_path, timeline_start) in enumerate(audio_inputs, start=1):
        delay_ms = max(0, int(round(timeline_start * 1000.0)))
        cmd.extend(["-i", str(audio_path)])
        filter_parts.append(f"[{idx}:a]adelay={delay_ms}|{delay_ms}[a{idx}]")
        labels.append(f"[a{idx}]")
    filter_parts.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:dropout_transition=0[aout]"
    )
    cmd.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(final_output_path),
        ]
    )
    await _run_cmd(cmd)


async def _attach_video_cover(video_path: Path, cover_path: Path) -> None:
    """Attach one still image as the MP4 cover stream without re-encoding the film."""
    if not cover_path.exists():
        raise FileNotFoundError(f"video compose cover not found: {cover_path}")
    output_path = video_path.with_name(f"{video_path.stem}.with-cover.mp4")
    try:
        await _run_cmd(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-i",
                str(cover_path),
                "-map",
                "0",
                "-map",
                "1:v:0",
                "-c",
                "copy",
                "-c:v:1",
                "mjpeg",
                "-disposition:v:1",
                "attached_pic",
                "-metadata:s:v:1",
                "mimetype=image/jpeg",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        output_path.replace(video_path)
    finally:
        output_path.unlink(missing_ok=True)


async def run_freezone_video_compose(
    *,
    project_dir: Path,
    job_id: str,
    title: str = "",
    canvas_id: str = "",
    resolution: str = "1080p",
    fps: int = 30,
    background_color: str = "#000000",
    keep_original_audio: bool = True,
    cover_path: str | None = None,
    tracks: list[dict[str, Any]],
) -> Path:
    """Compose a minimal timeline JSON into a final mp4."""
    del title, canvas_id

    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH; install via brew/apt")
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe not found on PATH; install via brew/apt")

    width, height = FREEZONE_VIDEO_RESOLUTION_MAP.get(
        resolution, FREEZONE_VIDEO_RESOLUTION_MAP["1080p"]
    )
    output_dir = outputs_dir(project_dir, "freezone_video_compose")
    output_dir.mkdir(parents=True, exist_ok=True)
    final_output_path = output_dir / f"{job_id}.mp4"

    video_items = [
        item
        for track in tracks
        if str(track.get("kind") or "") == "video"
        for item in (track.get("items") or [])
    ]
    audio_items = [
        item
        for track in tracks
        if str(track.get("kind") or "") == "audio"
        for item in (track.get("items") or [])
    ]
    if not video_items:
        raise RuntimeError("video compose requires at least one video clip")

    sorted_video_items = sorted(
        video_items,
        key=lambda item: (
            float(item.get("timeline_start", 0.0) or 0.0),
            str(item.get("item_id") or ""),
        ),
    )

    with tempfile.TemporaryDirectory(
        prefix=f"freezone_compose_{job_id}_"
    ) as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        segment_paths: list[Path] = []
        cursor = 0.0
        for index, item in enumerate(sorted_video_items):
            timeline_start = float(item.get("timeline_start", 0.0) or 0.0)
            source_start = float(item.get("source_start", 0.0) or 0.0)
            source_end = float(item.get("source_end", 0.0) or 0.0)
            duration = source_end - source_start
            if duration <= 0:
                raise RuntimeError(
                    f"compose item {item.get('item_id') or index} has invalid source range"
                )
            if timeline_start < cursor - 1e-6:
                raise RuntimeError(
                    "overlapping video clips are not supported in MVP compose"
                )
            if timeline_start > cursor + 1e-6:
                gap_path = temp_dir / f"gap_{index:03d}.mp4"
                await _render_gap_clip(
                    output_path=gap_path,
                    duration=timeline_start - cursor,
                    width=width,
                    height=height,
                    fps=fps,
                    background_color=background_color,
                )
                segment_paths.append(gap_path)
                cursor = timeline_start

            clip_path = temp_dir / f"video_{index:03d}.mp4"
            await _render_video_clip(
                source_path=str(item["source_path"]),
                output_path=clip_path,
                source_start=source_start,
                duration=duration,
                width=width,
                height=height,
                fps=fps,
                background_color=background_color,
                keep_original_audio=keep_original_audio,
                volume=float(item.get("volume", 1.0)),
                muted=bool(item.get("muted")),
                speed=float(item.get("speed", 1.0) or 1.0),
            )
            segment_paths.append(clip_path)
            speed = min(4.0, max(0.25, float(item.get("speed", 1.0) or 1.0)))
            cursor = timeline_start + duration / speed

        concatenated_path = temp_dir / "concatenated.mp4"
        await _concat_media_segments(segment_paths, concatenated_path)
        await _mix_audio_tracks(
            base_video_path=concatenated_path,
            final_output_path=final_output_path,
            audio_items=audio_items,
            temp_dir=temp_dir,
        )

    if not final_output_path.exists():
        raise RuntimeError("video compose finished without output file")
    if cover_path:
        await _attach_video_cover(final_output_path, Path(cover_path))
    return final_output_path


async def _probe_video_size(source_path: str) -> tuple[int, int]:
    ffprobe = _bundled_media_binary("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe not found on PATH or runtime/ffmpeg")
    proc = await asyncio.to_thread(
        subprocess.run,
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            source_path,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "").strip()[-500:] or "ffprobe size failed")
    text = (proc.stdout or "").strip()
    try:
        width_text, height_text = text.split("x", 1)
        return int(width_text), int(height_text)
    except Exception as exc:
        raise RuntimeError(f"unable to parse video size: {text}") from exc


async def _probe_video_duration(source_path: str) -> float:
    ffprobe = _bundled_media_binary("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe not found on PATH or runtime/ffmpeg")
    proc = await asyncio.to_thread(
        subprocess.run,
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            source_path,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            (proc.stderr or "").strip()[-500:] or "ffprobe duration failed"
        )
    try:
        return max(0.1, float((proc.stdout or "").strip()))
    except ValueError as exc:
        raise RuntimeError("unable to parse video duration") from exc


async def _probe_image_size(source_path: str) -> tuple[int, int]:
    """Read a finished image's pixel dimensions from its header only.

    ``Image.open`` reads the header lazily, so reporting how big a picture is
    never decodes the whole picture.
    """

    def _read() -> tuple[int, int]:
        with Image.open(source_path) as image:
            return int(image.width), int(image.height)

    width, height = await asyncio.to_thread(_read)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"unusable image dimensions: {width}x{height}")
    return width, height


def _expand_mask(mask: np.ndarray, radius: int = 2) -> np.ndarray:
    expanded = mask.copy()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx == 0 and dy == 0:
                continue
            expanded |= np.roll(np.roll(mask, dy, axis=0), dx, axis=1)
    return expanded


def _detect_subtitle_box_from_image(
    image_path: Path,
) -> tuple[int, int, int, int] | None:
    image = Image.open(image_path).convert("RGB")
    arr = np.asarray(image, dtype=np.int16)
    height, width = arr.shape[:2]
    start_y = int(height * 0.55)
    roi = arr[start_y:, :, :]
    gray = (
        (roi[:, :, 0] * 299 + roi[:, :, 1] * 587 + roi[:, :, 2] * 114) // 1000
    ).astype(np.int16)
    edge = np.zeros_like(gray)
    edge[:, 1:] += np.abs(gray[:, 1:] - gray[:, :-1])
    edge[1:, :] += np.abs(gray[1:, :] - gray[:-1, :])
    candidate = ((gray >= 205) | (gray <= 50)) & (edge >= 42)
    candidate = _expand_mask(candidate, radius=2)

    ys, xs = np.where(candidate)
    if len(xs) < max(80, width // 120):
        return None
    x0 = int(xs.min())
    x1 = int(xs.max()) + 1
    y0 = int(ys.min()) + start_y
    y1 = int(ys.max()) + 1 + start_y
    if (x1 - x0) < width * 0.12 or (y1 - y0) < 10:
        return None
    if (y1 - y0) > height * 0.22:
        return None
    return _safe_box_from_pixels(x0, y0, x1, y1, width, height)


async def _extract_sample_frames(
    video_path: str, temp_dir: Path, count: int = 6
) -> list[Path]:
    duration = await _probe_video_duration(video_path)
    sample_paths: list[Path] = []
    for index in range(count):
        ts = duration * (index + 1) / (count + 1)
        output_path = temp_dir / f"sample_{index:02d}.png"
        await _run_cmd(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{ts:.3f}",
                "-i",
                video_path,
                "-frames:v",
                "1",
                str(output_path),
            ]
        )
        if output_path.exists():
            sample_paths.append(output_path)
    return sample_paths


async def _detect_subtitle_box(
    video_path: str, temp_dir: Path
) -> tuple[int, int, int, int]:
    width, height = await _probe_video_size(video_path)
    sample_paths = await _extract_sample_frames(video_path, temp_dir)
    boxes = [
        box
        for box in (_detect_subtitle_box_from_image(path) for path in sample_paths)
        if box
    ]
    if not boxes:
        return _fallback_subtitle_box(width, height)

    left = int(np.median([box[0] for box in boxes]))
    top = int(np.median([box[1] for box in boxes]))
    right = int(np.median([box[0] + box[2] for box in boxes]))
    bottom = int(np.median([box[1] + box[3] for box in boxes]))
    return _safe_box_from_pixels(left, top, right, bottom, width, height)


async def _render_delogo_video(
    *,
    source_path: str,
    output_path: Path,
    x: int,
    y: int,
    w: int,
    h: int,
) -> None:
    """把指定矩形从画面里抹掉。

    `delogo` 是按四周像素插值补洞，最自然，所以优先用它。便携版 ffmpeg 没编进这个
    滤镜，退化成「把该区域抠出来重度模糊再原样盖回去」——对字幕条/角标这类固定浮层
    效果相当，而且只用 crop/gblur/overlay 三个到处都有的滤镜。两条路的输入输出完全
    一致，调用方不需要知道自己走的是哪条。
    """
    if _has_ffmpeg_filter("delogo"):
        await _run_cmd(
            [
                "ffmpeg",
                "-y",
                "-i",
                source_path,
                "-vf",
                f"delogo=x={x}:y={y}:w={w}:h={h}:show=0",
                *_video_encoder_args("medium", crf=18),
                "-c:a",
                "copy",
                str(output_path),
            ]
        )
        return

    # 模糊半径跟区域尺寸走：小字幕条用大 sigma 会把糊斑显得比字还抢眼，大角标用
    # 小 sigma 又盖不住。取短边的 1/4 并夹在 [8, 64]，两端都测过形状正常。
    blur = min(64.0, max(8.0, min(w, h) / 4.0))
    filter_graph = (
        "[0:v]split=2[base][patch];"
        f"[patch]crop={w}:{h}:{x}:{y},gblur=sigma={blur:.1f}[blurred];"
        f"[base][blurred]overlay={x}:{y}"
    )
    await _run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            source_path,
            "-filter_complex",
            filter_graph,
            *_video_encoder_args("medium", crf=18),
            "-c:a",
            "copy",
            str(output_path),
        ]
    )


async def run_freezone_video_erase(
    *,
    project_dir: Path,
    job_id: str,
    source_path: str,
    mode: str,
    box_x: float | None = None,
    box_y: float | None = None,
    box_width: float | None = None,
    box_height: float | None = None,
) -> tuple[Path, dict[str, int | str]]:
    """Erase subtitle-like overlays or a selected box from a video.

    Current MVP uses ffmpeg `delogo`, which is stable and fast for fixed overlay regions.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH; install via brew/apt")
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe not found on PATH; install via brew/apt")

    output_dir = outputs_dir(project_dir, "freezone_video_erase")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{job_id}.mp4"

    width, height = await _probe_video_size(source_path)
    with tempfile.TemporaryDirectory(
        prefix=f"freezone_erase_{job_id}_"
    ) as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        if mode == "smart_subtitle":
            x, y, w, h = await _detect_subtitle_box(source_path, temp_dir)
        elif mode == "box":
            if None in {box_x, box_y, box_width, box_height}:
                raise RuntimeError(
                    "box mode requires box_x, box_y, box_width and box_height"
                )
            x, y, w, h = _normalized_box_to_pixels(
                box_x=float(box_x),
                box_y=float(box_y),
                box_width=float(box_width),
                box_height=float(box_height),
                width=width,
                height=height,
            )
        else:
            raise RuntimeError(f"unsupported erase mode: {mode}")
        await _render_delogo_video(
            source_path=source_path,
            output_path=output_path,
            x=x,
            y=y,
            w=w,
            h=h,
        )
    if not output_path.exists():
        raise RuntimeError("video erase finished without output file")
    return output_path, {"mode": mode, "x": x, "y": y, "width": w, "height": h}


async def run_freezone_audio_separate(
    *,
    project_dir: Path,
    job_id: str,
    source_path: str,
) -> dict[str, Path | None]:
    """Split a video into extracted audio and muted video using ffmpeg only."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH; install via brew/apt")
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe not found on PATH; install via brew/apt")

    output_dir = outputs_dir(project_dir, "freezone_audio_separate")
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = output_dir / f"{job_id}.m4a"
    mute_video_path = output_dir / f"{job_id}_mute.mp4"

    has_audio = await _probe_has_audio(source_path)
    if has_audio:
        await _run_cmd(
            [
                "ffmpeg",
                "-y",
                "-i",
                source_path,
                "-vn",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(audio_path),
            ]
        )

    await _run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            source_path,
            "-c:v",
            "copy",
            "-an",
            str(mute_video_path),
        ]
    )

    if not mute_video_path.exists():
        raise RuntimeError("audio separate finished without muted video output")
    return {
        "audio_path": audio_path if audio_path.exists() else None,
        "mute_video_path": mute_video_path,
    }


async def run_freezone_video_cut(
    *,
    project_dir: Path,
    job_id: str,
    source_path: str,
    segments: list[dict[str, Any]],
    on_log: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Cut a source video into per-shot segments with ffmpeg only.

    The source of the ranges is the shot table parsed out of the same video
    (`freezone_video_story`), so this is the "keep the original seconds" half of
    that pipeline: no model is called and nothing is re-generated — the output
    is a byte-faithful sub-range of the input, geometry unchanged.

    Returns one entry per segment: `{index, start, end, path, duration_seconds}`.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH; install via brew/apt")
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe not found on PATH; install via brew/apt")

    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"video not found: {source}")

    source_duration = await _probe_video_duration(source_path)
    planned = normalize_video_cut_segments(segments, source_duration=source_duration)

    output_dir = outputs_dir(project_dir, "freezone_video_cut") / job_id
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for position, segment in enumerate(planned):
        # Name by position, not by the caller's index: the index is a label for
        # "which shot is this", and two shots sharing one would collide on disk.
        output_path = output_dir / f"segment_{position:03d}.mp4"
        await _render_video_segment(
            source_path=source_path,
            output_path=output_path,
            source_start=segment["start"],
            source_end=segment["end"],
        )
        if not output_path.exists():
            raise RuntimeError(
                f"segment {segment['index']} finished without an output file"
            )
        actual_duration = await _probe_video_duration(str(output_path))
        if on_log is not None:
            on_log(
                f"已切出第 {position + 1}/{len(planned)} 段"
                f"（{segment['start']:.1f}s – {segment['end']:.1f}s）"
            )
        results.append(
            {
                "index": segment["index"],
                "start": segment["start"],
                "end": segment["end"],
                "path": output_path,
                "duration_seconds": actual_duration,
            }
        )

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "job_id": job_id,
                "source_path": source.as_posix(),
                "source_duration_seconds": source_duration,
                "segments": [
                    {
                        "index": item["index"],
                        "start": item["start"],
                        "end": item["end"],
                        "file": item["path"].name,
                        "duration_seconds": item["duration_seconds"],
                    }
                    for item in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return results


def _video_capacity_fallback_backend(backend: str, error: object) -> str:
    """Return an explicitly selected *direct* fallback, never a legacy gateway.

    A capacity retry can create a second paid request.  The default must
    therefore be no retry; operators may opt in with a second enabled direct
    registry backend after its capability and price are known locally.
    """
    message = str(error or "").strip().lower()
    if not any(
        marker in message
        for marker in (
            "video_capacity_unavailable",
            "capacity_unavailable",
            "capacity unavailable",
        )
    ):
        return ""
    if not str(backend or "").strip().lower().startswith("newapi_jimeng-seedance-"):
        return ""
    configured = os.environ.get("VILLAGE_CANVAS_VIDEO_CAPACITY_FALLBACK", "").strip()
    if not configured or configured == str(backend or "").strip():
        return ""
    from novelvideo.generators.video.direct_models import resolve_direct_video_model

    fallback = resolve_direct_video_model(configured)
    if fallback is None or not fallback.enabled:
        return ""
    return fallback.backend


async def run_freezone_video_gen(
    *,
    project_dir: Path,
    job_id: str,
    prompt: str,
    reference_items: Optional[list[dict[str, str]]] = None,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    duration_seconds: int = 5,
    generate_audio: bool = False,
    requested_generate_audio: bool | None = None,
    human_review: bool = False,
    scene_optimize: str | None = None,
    backend: str = "",
    last_frame_path: Optional[str] = None,
    audio_setting: Optional[str] = None,
    dialogue_text: str = "",
    spoken_dialogue: list[str] | tuple[str, ...] | None = None,
    audio_type: str = "",
    speaker: str = "",
    native_audio_strategy: str = "",
    audio_asset_ref: str = "",
    generate_audio_explicit: bool | None = None,
    gen_mode: str | None = None,
    parameters: dict[str, Any] | None = None,
    provider_mapping: dict[str, str] | None = None,
    on_log: Callable[[str], None] | None = None,
    on_progress: Callable[[float], None] | None = None,
    on_task_event: Callable[[dict[str, object]], None] | None = None,
    resume_provider_task_id: str | None = None,
) -> Path:
    """Freezone 文生视频。

    统一承接 Freezone 视频生成，支持：
    - 纯 prompt 文生视频
    - prompt + 角色参考图
    - 首帧 / 尾帧参考
    - 原生音频开关（由具体模型决定）
    """
    out = outputs_dir(project_dir, "freezone_video_gen") / f"{job_id}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    # Separate spoken content from the provider-facing visual prompt before any
    # provider adapter sees it.  This also covers direct callers that bypass
    # the REST route (workflow runners, recovery, and test harnesses).
    from novelvideo.services.video_request_contract import (
        compile_h3_picture_prompt,
        compile_h3_provider_prompt,
        is_minimax_h3_model_identifier,
        append_video_no_text_tail,
    )
    from novelvideo.services.video_request_contract import (
        prepare_video_submission,
        resolve_submission_audio,
    )
    from novelvideo.services.video_submission_keys import (
        single_video_idempotency_key,
        video_media_input_token,
    )

    requested_generate_audio = bool(
        generate_audio if requested_generate_audio is None else requested_generate_audio
    )
    # ``None`` means the caller predates the explicit canvas audio switch.
    # Preserve that distinction so a historical ``generate_audio=True`` value
    # does not masquerade as a fresh user choice.
    requested_audio_explicit = (
        bool(generate_audio_explicit) if generate_audio_explicit is not None else None
    )

    preflight = prepare_video_submission(
        prompt,
        duration_seconds=duration_seconds,
        dialogue_text=dialogue_text,
        spoken_dialogue=spoken_dialogue or (),
        audio_type=audio_type,
        speaker=speaker,
        requested_audio=requested_generate_audio,
        requested_audio_explicit=requested_audio_explicit,
        native_audio_strategy=native_audio_strategy,
        audio_asset_ref=audio_asset_ref,
        reference_items=reference_items or (),
    )
    normalization = preflight.normalization
    visual_prompt = append_video_no_text_tail(preflight.visual_prompt)
    prompt = visual_prompt
    canonical_dialogue = list(preflight.spoken_dialogue)
    effective_audio_type = preflight.audio_type
    strip_provider_audio = preflight.strip_provider_audio

    # A completed download is already an authoritative local result.  The
    # generator downloads through an atomic .part -> final replace, so a final
    # non-empty file from a previous process can be safely archived instead of
    # re-submitting a paid upstream request after a restart.  It still passes
    # through the local audio gate so a result created before this fix cannot
    # reintroduce an unrequested provider voice.
    if out.exists() and out.stat().st_size > 0:
        if strip_provider_audio:
            await _strip_unrequested_video_audio(out, on_log=on_log)
        if on_log:
            on_log("检测到已落盘的视频结果，直接完成归档")
        if on_progress:
            on_progress(1.0)
        return out

    # A resumed provider task is already accepted upstream.  Re-validating its
    # historical prompt could block recovery after a server-side contract has
    # changed, so only new submissions go through semantic preflight here.
    if not str(resume_provider_task_id or "").strip():
        from novelvideo.services.video_request_contract import (
            VideoRequestContractError,
            validate_video_request_contract,
        )

        contract_issues = validate_video_request_contract(
            prompt=visual_prompt,
            duration_seconds=duration_seconds,
            reference_items=reference_items or (),
            # 台词时长预算门读的是台词本身；`visual_prompt` 里已经只剩「说话表演」。
            spoken_dialogue=canonical_dialogue,
        )
        if contract_issues:
            raise VideoRequestContractError(contract_issues)

    from novelvideo.generators.video_generator import (
        NewApiVideoGenerator,
        ShotReference,
        create_video_generator,
        parse_newapi_video_backend,
    )

    references = [
        ShotReference(
            str(item.get("type") or "image"),
            str(item.get("path") or ""),
            str(item.get("role") or ""),
        )
        for item in (reference_items or [])
        if str(item.get("path") or "").strip()
    ]
    from novelvideo.generators.video.capabilities import VideoMode
    from novelvideo.freezone.video_node import is_freezone_seedance2_backend
    from novelvideo.generators.video.direct_models import resolve_direct_video_model

    # Freeze only an empty/default/direct selector to the real registry
    # identity. Explicit legacy backends remain readable for persisted task
    # recovery; all new API routes already resolve their input to direct IDs.
    backend_value = str(backend or "").strip()
    if not backend_value or backend_value.lower().startswith("direct_"):
        direct_model = resolve_direct_video_model(backend_value or "direct_default")
        if direct_model is None:
            raise ValueError("尚未配置可用的直连视频模型，请先在模型中心配置并检测。")
        backend = direct_model.backend

    direct_model = resolve_direct_video_model(backend)
    native_audio = (
        getattr(getattr(direct_model, "capability", None), "native_audio", None)
        if direct_model is not None
        else None
    )
    provider_family = (
        str(getattr(getattr(direct_model, "profile", None), "family", "") or "")
        .strip()
        .casefold()
    )
    is_h3_provider = provider_family == "minimax-h3" or is_minimax_h3_model_identifier(
        backend,
        getattr(direct_model, "upstream_model", ""),
        getattr(direct_model, "label", ""),
    )
    effective_generate_audio, _ = resolve_submission_audio(
        requested=generate_audio,
        requested_explicit=requested_audio_explicit,
        audio_type=effective_audio_type,
        native_audio=getattr(native_audio, "value", native_audio or "optional"),
        native_audio_strategy=native_audio_strategy,
        spoken_dialogue=canonical_dialogue,
        audio_asset_ref=audio_asset_ref,
    )
    generate_audio = effective_generate_audio
    # Keep dialogue in the provider prompt only for native-audio submissions.
    # The explicit canvas switch is authoritative even for a required-audio
    # provider; its returned track is removed by the local artifact gate.
    if (
        not str(resume_provider_task_id or "").strip()
        and effective_generate_audio
        and requested_audio_explicit is not False
        and not strip_provider_audio
    ):
        if is_h3_provider:
            prompt = await compile_h3_provider_prompt(
                visual_prompt,
                canonical_dialogue,
                speaker=speaker,
                sung=normalization.dialogue_is_sung,
                on_log=on_log,
            )
        else:
            prompt = normalization.provider_prompt
    elif is_h3_provider and not str(resume_provider_task_id or "").strip():
        # 关掉原生音频时不再拼官方三章节，但画面段仍按框架净化：六段式段名与
        # 优化器的流程说明留在正文里，H3 一样会把它念出来或烧成字幕；而且正文
        # 仍要按官方方言编译成英文，字幕风险与是否开原生音频无关。
        prompt = await compile_h3_picture_prompt(
            visual_prompt,
            speech_authorized=bool(canonical_dialogue),
            on_log=on_log,
        )

    direct_can_start_without_first_frame = bool(
        direct_model
        and (
            VideoMode.TEXT_TO_VIDEO in direct_model.capability.modes
            or (
                direct_model.capability.reference_limits.reference_videos > 0
                and any(reference.type == "video" for reference in references)
            )
        )
    )

    video_gen = create_video_generator(
        backend=backend,
        resolution=resolution,
        generate_audio=generate_audio,
        parameters=dict(parameters or {}),
        provider_mapping=dict(provider_mapping or {}),
    )
    async def _dispatch_generation(
        selected_backend: str, selected_generator: object,
        selected_resolution: str, generate_kwargs: dict[str, object],
    ):
        from novelvideo.services.video_dispatch import dispatch_video_generation

        return (await dispatch_video_generation(
            create_generator=lambda **_kwargs: selected_generator,
            backend=selected_backend,
            generator_kwargs={
                "resolution": selected_resolution,
                "generate_audio": generate_audio,
                "parameters": dict(parameters or {}),
                "provider_mapping": dict(provider_mapping or {}),
            },
            generate_kwargs=generate_kwargs,
        )).result

    # ``gen_mode`` is request-scoped. Generic adapters consume it directly;
    # NewAPI built-in families receive it only when explicitly selected and
    # translate it to their documented wire modes.
    generic_mode_kwargs: dict[str, object] = {}
    legacy_mode_kwargs: dict[str, object] = {}
    if gen_mode:
        from novelvideo.generators.video.generic_video_adapter import (
            GenericVideoAdapterGenerator,
        )

        if isinstance(video_gen, GenericVideoAdapterGenerator):
            generic_mode_kwargs["gen_mode"] = str(gen_mode).strip()
        elif isinstance(video_gen, NewApiVideoGenerator):
            legacy_mode_kwargs["gen_mode"] = str(gen_mode).strip()
    first_image_ref = next(
        (
            ref
            for ref in references
            if ref.type == "image" and "首帧" in str(ref.role or "")
        ),
        None,
    ) or next((ref for ref in references if ref.type == "image"), None)
    callback_kwargs: dict[str, object] = {
        "on_log": on_log,
        "on_progress": on_progress,
        "on_task_event": on_task_event,
        "project_output_dir": str(project_dir),
        "task_type": "freezone_video_gen",
        # The provider and relay both use this stable key to make submit
        # retries/restarts idempotent instead of creating another paid task.
        "idempotency_key": single_video_idempotency_key(
            scope=job_id,
            prompt=prompt,
            duration=duration_seconds,
            generation_mode=gen_mode or "",
            generate_audio=bool(generate_audio),
            media_inputs=[
                video_media_input_token(last_frame_path, kind="image", role="last_frame"),
                *[
                    video_media_input_token(
                        item.get("path"),
                        kind=item.get("type"),
                        role=item.get("role"),
                    )
                    for item in (reference_items or [])
                    if isinstance(item, dict)
                ],
            ],
        ),
    }
    # Keep this available for a capacity fallback even when the primary path
    # is a legacy Seedance/recovery branch that does not need audio settings.
    extra_kwargs: dict[str, object] = {}
    if audio_setting:
        extra_kwargs["audio_setting"] = audio_setting
    provider_task_id = str(resume_provider_task_id or "").strip()
    if provider_task_id:
        from novelvideo.generators.video.generic_video_adapter import (
            GenericVideoAdapterGenerator,
        )

        if not isinstance(
            video_gen, (NewApiVideoGenerator, GenericVideoAdapterGenerator)
        ):
            raise RuntimeError(
                "已提交的视频任务只能由可恢复的视频协议通道处理，已阻止重新提交"
            )
        result = await video_gen.recover_task(
            task_id=provider_task_id,
            output_path=str(out),
            duration=float(duration_seconds),
            **callback_kwargs,
        )
    else:
        if backend != "seedance_2" and (
            (first_image_ref is None or not first_image_ref.path)
            and not str(backend).startswith("huimeng_")
            and not parse_newapi_video_backend(backend)
            and not is_freezone_seedance2_backend(backend)
            and not direct_can_start_without_first_frame
        ):
            raise RuntimeError(
                f"backend {backend} requires a first-frame image reference"
            )
        generate_kwargs: dict[str, object] = {
            "image_path": first_image_ref.path
            if first_image_ref and first_image_ref.path
            else None,
            "prompt": prompt,
            "output_path": str(out),
            "references": references,
            "duration": float(duration_seconds),
            "human_review": bool(human_review),
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "last_frame_path": last_frame_path,
            "gen_mode": str(gen_mode).strip() if gen_mode else None,
            **callback_kwargs,
        }
        if backend == "seedance_2":
            generate_kwargs["audio"] = bool(generate_audio)
        else:
            generate_kwargs.update(
                generate_audio=bool(generate_audio),
                seedance2_config={"scene_optimize": scene_optimize}
                if scene_optimize
                else None,
                **extra_kwargs,
                **generic_mode_kwargs,
                **legacy_mode_kwargs,
            )
        result = await _dispatch_generation(backend, video_gen, resolution, generate_kwargs)
    result_error = result.error if result else "unknown error"
    capacity_fallback_backend = (
        "" if provider_task_id else _video_capacity_fallback_backend(backend, result_error)
    )
    if capacity_fallback_backend:
        fallback_resolution = "480p" if "480p" in capacity_fallback_backend.lower() else resolution
        if on_log:
            on_log(
                "Wokey 当前容量不足，自动切换备用视频模型 "
                f"{capacity_fallback_backend} ({fallback_resolution})..."
            )
        fallback_gen = create_video_generator(
            backend=capacity_fallback_backend,
            resolution=fallback_resolution,
            generate_audio=generate_audio,
            parameters=dict(parameters or {}),
            provider_mapping=dict(provider_mapping or {}),
        )
        # Primary and capacity fallback share all request fields except their
        # resolution and provider idempotency scope. Recovery never enters here.
        fallback_kwargs = {
            **generate_kwargs,
            "resolution": fallback_resolution,
            "generate_audio": bool(generate_audio),
            "seedance2_config": {"scene_optimize": scene_optimize} if scene_optimize else None,
            **extra_kwargs,
            "idempotency_key": single_video_idempotency_key(
                scope=f"{job_id}-capacity-fallback",
                prompt=prompt,
                duration=duration_seconds,
                generation_mode=gen_mode or "",
                generate_audio=bool(generate_audio),
                media_inputs=[
                    video_media_input_token(last_frame_path, kind="image", role="last_frame"),
                    *[
                        video_media_input_token(item.get("path"), kind=item.get("type"), role=item.get("role"))
                        for item in (reference_items or []) if isinstance(item, dict)
                    ],
                ],
            ),
        }
        fallback_kwargs.pop("audio", None)
        fallback_kwargs.pop("gen_mode", None)
        if gen_mode:
            from novelvideo.generators.video.generic_video_adapter import GenericVideoAdapterGenerator

            if isinstance(fallback_gen, (GenericVideoAdapterGenerator, NewApiVideoGenerator)):
                fallback_kwargs["gen_mode"] = str(gen_mode).strip()
        result = await _dispatch_generation(
            capacity_fallback_backend, fallback_gen, fallback_resolution, fallback_kwargs,
        )
    if not result or result.status.value != "done":
        err = result.error if result else "unknown error"
        error_metadata = getattr(result, "error_metadata", None) if result else None
        if (
            isinstance(error_metadata, dict)
            and error_metadata.get("error_code") == "VIDEO_SUBMIT_RESULT_UNKNOWN"
        ):
            raise VideoSubmissionPending(
                str(err or "视频提交结果待确认"),
                idempotency_key=str(callback_kwargs["idempotency_key"]),
            )
        completed_task_id = str(
            getattr(result, "provider_task_id", None)
            or getattr(result, "task_id", None)
            or provider_task_id
            or ""
        ).strip()
        normalized_error = str(err or "").lower()
        is_completed_download_failure = completed_task_id and (
            "result download" in normalized_error
            or "content download" in normalized_error
            or "download failed" in normalized_error
            or "上游直链与统一内容端点均下载失败" in str(err or "")
            or "已完成的上游视频下载失败" in str(err or "")
        )
        if is_completed_download_failure:
            raise CompletedVideoDownloadPending(
                "上游视频已完成，但结果下载暂时中断；将自动恢复下载，不会重新生成",
                provider_task_id=completed_task_id,
            )
        raise FreezoneVideoGenerationError(
            _format_video_generation_failure(err, error_metadata),
            provider_error_metadata=(
                dict(error_metadata) if isinstance(error_metadata, dict) else None
            ),
        )
    if not out.exists():
        raise RuntimeError(
            "video generation returned success but no output file was written"
        )
    if strip_provider_audio:
        await _strip_unrequested_video_audio(out, on_log=on_log)
    return out


# ============================================================
# Extract frames (M1a) — 视频拉片
# ============================================================


async def run_freezone_extract_frames(
    *,
    project_dir: Path,
    job_id: str,
    video_path: Path,
    max_frames: int = 20,
    scene_threshold: float = 0.3,
) -> list[Path]:
    """ffmpeg pixel-diff scene detection → up to `max_frames` keyframes.

    Uses ffmpeg's built-in `scene` filter (returns 0-1 confidence per frame
    transition); we pick frames where confidence > threshold. If the video
    has fewer scene cuts than `max_frames` we fall back to evenly-spaced
    sampling to guarantee at least a few frames.

    Returns absolute paths to the saved frame PNGs.
    """
    import asyncio
    import shutil
    import subprocess

    out_dir = outputs_dir(project_dir, "freezone_extract") / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"video not found: {video_path}")
    # A translated-script JSON reaching here used to surface as
    # ``ffmpeg scene detect failed: <libav version banner>``. Name the real
    # problem at the call site that picked the wrong file, before probing the
    # toolchain: the wrong-file mistake is environment-independent.
    ensure_video_source_path(video_path, project_dir=project_dir)
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH; install via brew/apt")

    # Pass 1: scene detection extraction.
    pattern = str(out_dir / "scene_%03d.png")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"select='gt(scene,{scene_threshold})'",
            # ``-vsync`` was removed by FFmpeg 9; ``-fps_mode`` is the stable
            # replacement on every FFmpeg we ship or develop against.
            "-fps_mode",
        "vfr",
        "-frames:v",
        str(max_frames),
        "-frame_pts",
        "true",
        pattern,
    ]
    proc = await asyncio.to_thread(
        subprocess.run, cmd, capture_output=True, text=True, timeout=600
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg scene detect failed: {_ffmpeg_error_tail(proc.stderr)}"
        )

    scene_files = sorted(out_dir.glob("scene_*.png"))

    # Fallback: if too few scene cuts (e.g. talking head static video),
    # sample evenly-spaced frames so the user always gets *something*.
    if len(scene_files) < 3:
        for f in scene_files:
            f.unlink(missing_ok=True)
        scene_files = await _sample_evenly(video_path, out_dir, max_frames)
    if not scene_files:
        raise RuntimeError(
            f"视频中没有可用的画面帧：{video_path.name}（可能是纯音频或损坏的文件）"
        )

    return scene_files


# ============================================================
# Analyze shots (M1b) — Gemini Vision 拆分镜
# ============================================================


SHOT_ANALYSIS_PROMPT = """你是一个专业的电影分镜师。下面给你一组视频关键帧（按时间顺序），请逐帧分析每帧的电影语言。

对每帧输出一个 JSON 对象，字段：
- shot_type: 景别（"特写" | "近景" | "中景" | "全景" | "远景" | "大远景"）
- angle: 镜头角度（"平视" | "俯拍" | "仰拍" | "鸟瞰" | "倾斜" 等）
- camera_movement: 推测的运镜（"静止" | "推镜" | "拉镜" | "摇镜" | "移镜" | "升降" | "跟镜" 等，没有上下文则填"静止"）
- subject_action: 主体动作的简短描述（中文，<= 20 字，没有主体则"环境镜头"）
- mood: 氛围（"温馨" | "紧张" | "压抑" | "明快" | "孤独" 等）
- color_tone: 色调（"暖色调" | "冷色调" | "高饱和" | "低饱和" | "黑白" 等）
- suggested_prompt: 一句中文文生图 prompt，用于让 AI 重现这帧的视觉风格（包含上面所有元素，<= 80 字）

输出格式严格为 JSON 数组（不要任何解释 / markdown 包裹），第 i 个元素对应第 i 帧。例如：
[
  {"shot_type": "近景", "angle": "平视", "camera_movement": "静止", "subject_action": "环境镜头", "mood": "明快", "color_tone": "高饱和", "suggested_prompt": "..."},
  ...
]
"""


def build_video_story_analysis_prompt(
    *,
    frame_count: int,
    duration_sec: Optional[float] = None,
    source_frame_count: int | None = None,
    source_frame_indices: list[int] | None = None,
) -> str:
    duration_hint = (
        f"视频总时长约 {duration_sec:.2f} 秒。"
        f"请把 start_time/end_time 分配在 0 到 {duration_sec:.2f} 秒之间。"
        if duration_sec and duration_sec > 0
        else (
            "未知视频总时长。请根据关键帧顺序给出相对合理的 "
            "start_time/end_time，第一镜从 0 开始。"
        )
    )
    source_frame_hint = (
        "- 本次展示的是从全部 "
        f"{source_frame_count} 张源关键帧中挑出的 {frame_count} 张代表帧；"
        f"它们按时间顺序对应的源帧序号是：{'、'.join(str(index) for index in source_frame_indices or [])}。\n"
        "- `keyframes` 只能填写上面列出的源帧序号，不能填写未展示的帧。"
        if source_frame_count and source_frame_indices
        else f"- 关键帧使用输入帧序号，1 到 {frame_count}。"
    )
    return f"""你是专业影视导演和分镜解析师。下面给你 {frame_count} 张
按时间顺序抽取的视频关键帧，请解析成 libtv 风格的“视频故事”表。

{duration_hint}

要求：
- 不要逐帧机械描述，要把连续关键帧归纳成 3-12 个叙事镜头/动作段落。
- 保持同一视频内部的故事连续性：谁在做什么，发生了什么变化，
  镜头如何推进。
- 时间字段使用数字秒，duration = end_time - start_time。
- 画面描述写清主体、动作、环境、构图、情绪、重要道具。
- 叙事内容写这一镜在故事中的作用，而不是重复画面描述。
- 图生视频提示词和视频运动提示词用英文，适合直接用于视频生成。
- 背景音乐、人声/音效用中文，简洁描述。
{source_frame_hint}
- 如果看不出声音，不要编对白，只写可由画面推断的音效/氛围。
- 严格输出 JSON 对象，不要 markdown，不要解释。
- 输出前自检 JSON 语法：`shots` 里的每一镜都必须是完整的 `{{...}}` 对象，
  相邻镜头之间必须有逗号；没有完整字段时不要输出半截字段。

JSON schema:
{{
  "title": "中文短标题",
  "summary": "中文一句话概括视频故事",
  "duration": 数字秒或 null,
  "shots": [
    {{
      "shot": 1,
      "start_time": 0.0,
      "end_time": 1.2,
      "duration": 1.2,
      "visual_description": "中文画面描述",
      "narrative": "中文叙事内容",
      "shot_size": "特写/近景/中近景/中景/全景/远景/大远景",
      "camera_angle": "平视/俯拍/仰拍/倾斜/高角度/低角度",
      "camera_movement": "固定/推镜/拉镜/摇镜/移镜/跟镜/手持/缓慢推进",
      "focus_depth": "浅景深/中等景深/深景深",
      "lighting": "中文光线描述",
      "background_music": "中文背景音乐建议",
      "voice_sound": "中文人声或音效",
      "image_prompt": "English image-to-video visual prompt",
      "motion_prompt": "English motion prompt",
      "keyframes": [1, 2]
    }}
  ]
}}
"""


VIDEO_STORY_VISION_MAX_FRAMES = 8
VIDEO_STORY_VISION_MAX_EDGE = 1024
VIDEO_STORY_VISION_MAX_IMAGE_BYTES = 700_000
VIDEO_STORY_VISION_JPEG_QUALITIES = (85, 78, 70)


def _select_evenly_spaced_video_story_frames(
    frame_paths: list[str], *, max_frames: int = VIDEO_STORY_VISION_MAX_FRAMES
) -> list[tuple[int, Path]]:
    """Select chronological representatives while always retaining the first and last frame."""
    existing = [Path(path) for path in frame_paths if Path(path).exists()]
    if len(existing) <= max_frames:
        return list(enumerate(existing, start=1))

    selected_indexes = {
        round(position * (len(existing) - 1) / (max_frames - 1))
        for position in range(max_frames)
    }
    return [(index + 1, existing[index]) for index in sorted(selected_indexes)]


def _encode_video_story_vision_frame(
    path: Path, *, source_index: int, source_frame_count: int
):
    """Convert one extracted PNG into a bounded high-quality JPEG model input."""
    from io import BytesIO

    from novelvideo.freezone.vision_gateway import VisionInput, image_media_type

    label = f"源视频关键帧 {source_index}/{source_frame_count}"
    try:
        with Image.open(path) as source:
            rgb = source.convert("RGB")
            encoded: bytes | None = None
            for max_edge in (VIDEO_STORY_VISION_MAX_EDGE, 896, 768):
                candidate = rgb.copy()
                candidate.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
                for quality in VIDEO_STORY_VISION_JPEG_QUALITIES:
                    buffer = BytesIO()
                    candidate.save(
                        buffer,
                        format="JPEG",
                        quality=quality,
                        optimize=True,
                        progressive=True,
                    )
                    encoded = buffer.getvalue()
                    if len(encoded) <= VIDEO_STORY_VISION_MAX_IMAGE_BYTES:
                        return VisionInput(
                            data=encoded,
                            media_type="image/jpeg",
                            label=label,
                        )
            if encoded:
                return VisionInput(data=encoded, media_type="image/jpeg", label=label)
    except Exception:
        logger.warning(
            "Unable to compact video story frame %s; using source bytes", path
        )

    return VisionInput(
        data=path.read_bytes(),
        media_type=image_media_type(str(path)),
        label=label,
    )


def _prepare_video_story_vision_inputs(frame_paths: list[str]):
    """Bound the remote request without discarding the full local keyframe preview set."""
    selected = _select_evenly_spaced_video_story_frames(frame_paths)
    source_frame_count = sum(1 for path in frame_paths if Path(path).exists())
    inputs = [
        _encode_video_story_vision_frame(
            path,
            source_index=source_index,
            source_frame_count=source_frame_count,
        )
        for source_index, path in selected
    ]
    return (
        inputs,
        [source_index for source_index, _path in selected],
        source_frame_count,
    )


def _decode_complete_json_object(
    text: str, start: int
) -> tuple[dict[str, Any], int] | None:
    """Decode one fully closed JSON object without guessing missing content."""

    if start >= len(text) or text[start] != "{":
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    decoded = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return (decoded, index + 1) if isinstance(decoded, dict) else None
            if depth < 0:
                return None
    return None


def _repair_unescaped_json_control_characters(text: str) -> str:
    """Escape model-emitted control characters only while inside JSON strings.

    Some VLMs emit a literal line break before closing a final string field.
    When the next non-whitespace token is a structural closer, the string was
    plainly left open; close it without fabricating any value. Other controls
    are preserved as their JSON escape equivalents.
    """

    repaired: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            if escaped:
                repaired.append(char)
                escaped = False
                index += 1
                continue
            if char == "\\\\":
                repaired.append(char)
                escaped = True
                index += 1
                continue
            if char == '"':
                repaired.append(char)
                in_string = False
                index += 1
                continue
            if ord(char) < 0x20:
                next_token = index + 1
                while next_token < len(text) and text[next_token].isspace():
                    next_token += 1
                if next_token < len(text) and text[next_token] in "}]":
                    repaired.append('"')
                    in_string = False
                    repaired.append(char)
                    index += 1
                    continue
                if char == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
                    repaired.append("\\\\n")
                    index += 2
                    continue
                escape = {"\n": "\\\\n", "\r": "\\\\r", "\t": "\\\\t"}.get(
                    char, f"\\\\u{ord(char):04x}"
                )
                repaired.append(escape)
                index += 1
                continue
            repaired.append(char)
            index += 1
            continue
        repaired.append(char)
        if char == '"':
            in_string = True
        index += 1
    return "".join(repaired)


def _normalize_missing_json_values(text: str) -> str:
    """Replace an omitted scalar value with JSON null outside string literals."""

    normalized: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        normalized.append(char)
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            index += 1
            continue
        if char == ":":
            next_token = index + 1
            while next_token < len(text) and text[next_token].isspace():
                normalized.append(text[next_token])
                next_token += 1
            if next_token < len(text) and text[next_token] in ",}]":
                normalized.append("null")
            index = next_token
            continue
        index += 1
    return "".join(normalized)


def _salvage_complete_video_story_shots(text: str) -> dict[str, Any] | None:
    """Retain complete leading shots when a VLM corrupts only a later shot.

    This deliberately does not invent commas, braces, or field values.  It only
    accepts individually valid objects already present in the model response,
    so a malformed tail cannot make a finished video analysis unusable.
    """

    shots_key = text.find('"shots"')
    if shots_key < 0:
        return None
    array_start = text.find("[", shots_key)
    if array_start < 0:
        return None

    shots: list[dict[str, Any]] = []
    position = array_start + 1
    while position < len(text):
        while position < len(text) and text[position] in " \t\r\n,":
            position += 1
        if position >= len(text) or text[position] == "]":
            break
        decoded = _decode_complete_json_object(text, position)
        if decoded is None:
            break
        shot, position = decoded
        if "shot" not in shot:
            break
        shots.append(shot)

    # The prompt contract requires 3-12 shots. A smaller partial result would
    # be misleading and should still follow the existing failure path.
    if len(shots) < 3:
        return None
    return {"shots": shots}


def _decode_legacy_vision_text(
    *,
    text: str,
    mode: str,
    used_provider: str,
    out_dir: Path,
) -> tuple[Any, dict[str, Any] | None]:
    """Compatibility decoder for providers that still return plain text."""

    if not text:
        raise RuntimeError(f"{used_provider} Vision returned no text")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(
            line for line in cleaned.splitlines() if not line.strip().startswith("```")
        ).strip()
    try:
        return json.loads(cleaned), None
    except json.JSONDecodeError as exc:
        decode_error = exc
        (out_dir / "raw_response.txt").write_text(text, encoding="utf-8")

    control_repaired = _repair_unescaped_json_control_characters(cleaned)
    value_repaired = _normalize_missing_json_values(control_repaired)
    try:
        analyses = json.loads(value_repaired)
    except json.JSONDecodeError:
        analyses = None
    if analyses is not None:
        response_recovery: dict[str, Any] | None = None
        if mode == "video_story" and isinstance(analyses, dict):
            parsed_shots = analyses.get("shots")
            response_recovery = {
                "strategy": (
                    "normalized_missing_values"
                    if value_repaired != control_repaired
                    else "escaped_control_characters"
                ),
                "shot_count": len(parsed_shots)
                if isinstance(parsed_shots, list)
                else 0,
            }
        logger.warning(
            "Recovered malformed %s Vision JSON by escaping control characters; raw saved at %s",
            used_provider,
            out_dir / "raw_response.txt",
        )
        return analyses, response_recovery

    recovered = (
        _salvage_complete_video_story_shots(cleaned) if mode == "video_story" else None
    )
    if recovered is None:
        raise RuntimeError(
            f"{used_provider} returned non-JSON: {decode_error}; raw saved"
        ) from decode_error
    response_recovery = {
        "strategy": "salvaged_complete_shots",
        "shot_count": len(recovered["shots"]),
    }
    logger.warning(
        "Recovered %s complete video-story shots from malformed %s Vision JSON; raw saved at %s",
        response_recovery["shot_count"],
        used_provider,
        out_dir / "raw_response.txt",
    )
    return recovered, response_recovery


async def run_freezone_analyze_shots(
    *,
    project_dir: Path,
    job_id: str,
    frame_paths: list[str],
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    analysis_mode: str = "shots",
    duration_sec: Optional[float] = None,
) -> dict:
    """Send N frames to a Vision model and parse a structured JSON response.

    Product requests always use the effective NewAPI gateway. ``provider`` is
    retained only for payload compatibility with older saved canvases.
    """
    from novelvideo.freezone.vision_gateway import (
        VisionInput,
        call_freezone_vision_model,
        image_media_type,
        resolve_freezone_video_story_model,
    )

    if not frame_paths:
        raise ValueError("no frames to analyze")

    out_dir = outputs_dir(project_dir, "freezone_analyze") / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    del provider
    mode = (analysis_mode or "shots").strip().lower()
    if mode not in {"shots", "video_story"}:
        raise ValueError(f"unsupported analysis_mode: {analysis_mode}")
    existing_frame_paths = [path for path in frame_paths if Path(path).exists()]
    if not existing_frame_paths:
        raise ValueError("no readable frames to analyze")

    if mode == "video_story":
        vision_inputs, frame_indices, source_frame_count = await asyncio.to_thread(
            _prepare_video_story_vision_inputs,
            existing_frame_paths,
        )
        prompt = build_video_story_analysis_prompt(
            frame_count=len(vision_inputs),
            duration_sec=duration_sec,
            source_frame_count=source_frame_count,
            source_frame_indices=frame_indices,
        )
    else:
        vision_inputs = [
            VisionInput(
                data=Path(path).read_bytes(),
                media_type=image_media_type(path),
            )
            for path in existing_frame_paths
        ]
        frame_indices = list(range(1, len(vision_inputs) + 1))
        source_frame_count = len(vision_inputs)
        prompt = SHOT_ANALYSIS_PROMPT

    del api_key
    vision_model, model_output = await call_freezone_vision_model(
        prompt=prompt,
        images=vision_inputs,
        model_override=(
            resolve_freezone_video_story_model(model)
            if mode == "video_story"
            else model
        ),
        structured_output_type=VideoStoryAnalysis if mode == "video_story" else None,
    )
    used_provider, separator, vision_model_name = vision_model.partition("/")
    if separator:
        vision_model = vision_model_name
    used_provider = "direct"

    response_recovery: dict[str, Any] | None = None
    if isinstance(model_output, VideoStoryAnalysis):
        analyses = model_output.model_dump(mode="json")
    else:
        analyses, response_recovery = _decode_legacy_vision_text(
            text=str(model_output or ""),
            mode=mode,
            used_provider=used_provider,
            out_dir=out_dir,
        )

    if mode == "video_story":
        if not isinstance(analyses, dict):
            raise RuntimeError(f"{used_provider} response is not an object")
    elif not isinstance(analyses, list):
        raise RuntimeError(f"{used_provider} response is not a list")

    payload = {
        "provider": used_provider,
        "model": vision_model,
        "analysis_mode": mode,
        "frame_count": len(vision_inputs),
        "source_frame_count": source_frame_count,
        "frame_indices": frame_indices,
    }
    if response_recovery is not None:
        payload["response_recovery"] = response_recovery
    if mode == "video_story":
        payload["video_story"] = analyses
        payload["analyses"] = analyses.get("shots", [])
    else:
        payload["analyses"] = analyses
    out_file = out_dir / "analysis.json"
    out_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    payload["output_path"] = str(out_file)
    return payload


async def _sample_evenly(
    video_path: Path, out_dir: Path, max_frames: int
) -> list[Path]:
    """Fallback when scene detection finds nothing — sample at regular intervals."""
    import asyncio
    import json
    import subprocess

    probe = await asyncio.to_thread(
        subprocess.run,
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=duration,nb_frames",
            "-of",
            "json",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    duration = 60.0
    if probe.returncode == 0:
        try:
            payload = json.loads(probe.stdout)
            duration = float(payload["streams"][0].get("duration") or 60.0)
        except (json.JSONDecodeError, KeyError, IndexError, ValueError):
            pass

    n = min(max(3, max_frames // 2), max_frames)
    fps_expr = f"1/{max(1.0, duration / n)}"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"fps={fps_expr}",
        "-frames:v",
        str(n),
        str(out_dir / "even_%03d.png"),
    ]
    proc = await asyncio.to_thread(
        subprocess.run, cmd, capture_output=True, text=True, timeout=300
    )
    if proc.returncode != 0:
        # The caller reports "no usable frames"; without this line the real
        # decoder complaint is lost with the discarded stderr.
        logger.warning(
            "ffmpeg even sampling failed rc=%s video=%s: %s",
            proc.returncode,
            video_path,
            _ffmpeg_error_tail(proc.stderr),
        )
    return sorted(out_dir.glob("even_*.png"))
