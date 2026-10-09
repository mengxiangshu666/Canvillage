"""Mux workflow dialogue audio into a generated shot video."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import shutil
import uuid
from collections.abc import Mapping
from typing import Any

from novelvideo.task_backend.subprocesses import run_project_subprocess

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_BUNDLED_FFMPEG = _PROJECT_ROOT / "runtime" / "ffmpeg"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _media_binary(name: str) -> str | None:
    executable = _BUNDLED_FFMPEG / f"{name}.exe"
    if executable.is_file():
        return str(executable)
    return shutil.which(name)


def _project_file(value: object, *, project_root: Path, label: str) -> Path:
    raw = str(value or "").strip()
    path = Path(raw) if raw else None
    if path is not None and not path.is_absolute():
        path = project_root / path
    if path is None or not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"{label}不存在或为空")
    try:
        resolved = path.resolve()
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise RuntimeError(f"{label}越出当前项目目录") from exc
    return resolved


async def mux_dialogue_audio(
    *,
    output_path: str | Path,
    dialogue_audio: Mapping[str, Any] | None,
    project_dir: str | Path,
    envelope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a hash-bound mux receipt, or ``{}`` when no dialogue is attached."""

    if not isinstance(dialogue_audio, Mapping):
        return {}
    audio_value = str(dialogue_audio.get("audio_path") or "").strip()
    if not audio_value:
        return {}
    if dialogue_audio.get("applied") is not True:
        raise RuntimeError("对白音频回执未标记为可用")

    project_root = Path(project_dir).resolve()
    video_path = _project_file(output_path, project_root=project_root, label="视频产物")
    audio_path = _project_file(audio_value, project_root=project_root, label="对白音频")
    if video_path == audio_path:
        raise RuntimeError("对白音频不能与视频产物是同一文件")

    ffmpeg = _media_binary("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("bundled FFmpeg 不可用，无法混入对白音频")
    temporary = video_path.with_name(
        f".{video_path.stem}.dialogue-{uuid.uuid4().hex}.mp4"
    )
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-af",
        "apad",
        "-shortest",
        "-movflags",
        "+faststart",
        str(temporary),
    ]
    try:
        result = await asyncio.to_thread(
            run_project_subprocess,
            command,
            envelope=envelope,
            timeout=180,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            error = str(result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"FFmpeg 对白混音失败：{error[-1000:] or 'unknown error'}")
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError("FFmpeg 对白混音输出为空")
        output_sha256 = _sha256_file(temporary)
        os.replace(temporary, video_path)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "schema": "dialogue_audio_mux.v1",
        "muxed": True,
        "required": True,
        "job_id": str(dialogue_audio.get("job_id") or ""),
        "audio_path": str(audio_path),
        "audio_sha256": _sha256_file(audio_path),
        "output_path": str(video_path),
        "output_sha256": output_sha256,
        "reference_applied": dialogue_audio.get("reference_applied") is True,
        "reference_reason": str(dialogue_audio.get("reference_reason") or ""),
    }


__all__ = ["mux_dialogue_audio"]
