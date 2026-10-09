"""Measure whether an encoded video still starts from its intended first frame."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps


SIMILARITY_SCHEMA = "video_first_frame_similarity.v1"
_NORMALIZED_SIZE = (128, 128)
_BUNDLED_FFMPEG = (
    Path(__file__).resolve().parents[3] / "runtime" / "ffmpeg" / "ffmpeg.exe"
)


class VideoFrameSimilarityError(RuntimeError):
    """Raised when the first frame cannot be measured safely."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_first_frame(
    video_path: Path,
    *,
    width: int,
    height: int,
    ffmpeg_path: str | None,
) -> Image.Image:
    ffmpeg = (
        ffmpeg_path
        or (str(_BUNDLED_FFMPEG) if _BUNDLED_FFMPEG.is_file() else "")
        or shutil.which("ffmpeg")
    )
    if not ffmpeg:
        raise VideoFrameSimilarityError(
            "系统缺少 FFmpeg，无法验证逐镜视频首帧"
        )
    expected_bytes = width * height * 3
    try:
        completed = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                str(video_path),
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ],
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise VideoFrameSimilarityError(
            f"无法启动 FFmpeg 读取逐镜视频首帧：{exc}"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise VideoFrameSimilarityError(
            f"FFmpeg 无法解出逐镜视频首帧：{detail[-500:] or '无诊断输出'}"
        )
    if len(completed.stdout) != expected_bytes:
        raise VideoFrameSimilarityError(
            "逐镜视频首帧字节数与探测尺寸不一致"
        )
    return Image.frombytes("RGB", (width, height), completed.stdout)


def _normalized_rgb(image: Image.Image) -> np.ndarray:
    normalized = ImageOps.exif_transpose(image).convert("RGB")
    normalized = normalized.resize(_NORMALIZED_SIZE, Image.Resampling.LANCZOS)
    return np.asarray(normalized, dtype=np.float64)


def _ssim_rgb(source: np.ndarray, frame: np.ndarray) -> tuple[float, float]:
    c1 = (0.01 * 255.0) ** 2
    c2 = (0.03 * 255.0) ** 2
    source_mean = source.mean(axis=(0, 1))
    frame_mean = frame.mean(axis=(0, 1))
    source_var = source.var(axis=(0, 1))
    frame_var = frame.var(axis=(0, 1))
    covariance = ((source - source_mean) * (frame - frame_mean)).mean(axis=(0, 1))
    numerator = (2.0 * source_mean * frame_mean + c1) * (
        2.0 * covariance + c2
    )
    denominator = (source_mean**2 + frame_mean**2 + c1) * (
        source_var + frame_var + c2
    )
    channel_scores = numerator / denominator
    return float(channel_scores.mean()), float(channel_scores.min())


def compare_video_first_frame(
    source_image_path: str | Path,
    video_path: str | Path,
    *,
    video_width: int,
    video_height: int,
    ffmpeg_path: str | None = None,
) -> dict[str, Any]:
    """Return deterministic first-frame similarity evidence for one MP4."""

    source_path = Path(source_image_path)
    encoded_path = Path(video_path)
    if not source_path.is_file() or source_path.stat().st_size <= 0:
        raise VideoFrameSimilarityError("派发时锁定的逐镜首帧图不存在或为空")
    if not encoded_path.is_file() or encoded_path.stat().st_size <= 0:
        raise VideoFrameSimilarityError("逐镜视频文件不存在或为空")
    if video_width <= 0 or video_height <= 0:
        raise VideoFrameSimilarityError("逐镜视频宽高无效，无法验证首帧")
    try:
        with Image.open(source_path) as opened:
            source = _normalized_rgb(opened)
    except OSError as exc:
        raise VideoFrameSimilarityError(f"无法读取派发时锁定的首帧图：{exc}") from exc
    frame = _extract_first_frame(
        encoded_path,
        width=video_width,
        height=video_height,
        ffmpeg_path=ffmpeg_path,
    )
    score, min_channel_score = _ssim_rgb(source, _normalized_rgb(frame))
    return {
        "schema": SIMILARITY_SCHEMA,
        "ssim": score,
        "ssim_min_channel": min_channel_score,
        "normalized_size": list(_NORMALIZED_SIZE),
        "video_width": int(video_width),
        "video_height": int(video_height),
        "sample_seconds": 0.0,
        "source_image_sha256": _sha256_file(source_path),
    }


__all__ = [
    "SIMILARITY_SCHEMA",
    "VideoFrameSimilarityError",
    "compare_video_first_frame",
]
