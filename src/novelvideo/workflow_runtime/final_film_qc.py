"""Bundled-FFmpeg engineering QC for a completed final-film artifact."""

from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from novelvideo.services.delivery_audio import (
    DELIVERY_INTEGRATED_LUFS,
    DELIVERY_LOUDNESS_TOLERANCE_LU,
    DELIVERY_TRUE_PEAK_DBTP,
    delivery_loudnorm_filter,
)
from novelvideo.services.production_contracts import compile_delivery_qc_contract
from novelvideo.services.delivery_video import DELIVERY_COLOR_SPACE


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_BUNDLED_FFMPEG = _PROJECT_ROOT / "runtime" / "ffmpeg"
_COMMAND_TIMEOUT_SECONDS = 180
_REQUIRED_CHECKS = (
    "container_allowed",
    "video_stream_present",
    "audio_stream_present",
    "dimensions",
    "frame_rate",
    "duration",
    "black_frames",
    "freeze_frames",
    "av_sync",
    "audio_activity",
    "loudness",
    "true_peak",
    "subtitle_stream",
    "color_space",
    "bitrate",
    "file_readback",
    "sha256",
)


def _text(value: object, *, limit: int = 500) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _finite_number(value: object) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _media_binary(name: str) -> str | None:
    executable = _BUNDLED_FFMPEG / f"{name}.exe"
    if executable.is_file():
        return str(executable)
    return shutil.which(name)


def _run(command: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _rational(value: object) -> float | None:
    text = _text(value, limit=40)
    if not text:
        return None
    try:
        parsed = float(Fraction(text))
    except (ValueError, ZeroDivisionError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _stream_duration(stream: Mapping[str, Any]) -> float | None:
    direct = _finite_number(stream.get("duration"))
    if direct is not None and direct > 0:
        return direct
    duration_ts = _finite_number(stream.get("duration_ts"))
    time_base = _rational(stream.get("time_base"))
    if duration_ts is None or time_base is None:
        return None
    value = duration_ts * time_base
    return value if value > 0 else None


def _parse_json_object(text: str) -> dict[str, Any]:
    start = text.rfind("{")
    if start < 0:
        return {}
    try:
        value, _end = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _probe_streams(path: Path, ffprobe: str | None) -> dict[str, Any]:
    if not ffprobe:
        return {}
    result = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ]
    )
    if result is None or result.returncode != 0:
        return {}
    try:
        payload = json.loads(result.stdout or "{}")
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    streams = payload.get("streams")
    streams = streams if isinstance(streams, list) else []
    video = next(
        (
            item
            for item in streams
            if isinstance(item, Mapping) and item.get("codec_type") == "video"
        ),
        {},
    )
    audio = next(
        (
            item
            for item in streams
            if isinstance(item, Mapping) and item.get("codec_type") == "audio"
        ),
        {},
    )
    subtitle = next(
        (
            item
            for item in streams
            if isinstance(item, Mapping) and item.get("codec_type") == "subtitle"
        ),
        {},
    )
    container = payload.get("format")
    container = container if isinstance(container, Mapping) else {}
    observations: dict[str, Any] = {
        "container_allowed": {
            "container": path.suffix.casefold().lstrip(".")
            or _text(container.get("format_name")).split(",", 1)[0]
        },
        "video_stream_present": bool(video),
        "audio_stream_present": bool(audio),
        "subtitle_stream": {
            "present": bool(subtitle),
            "required": False,
        },
    }
    width = video.get("width")
    height = video.get("height")
    if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
        observations["dimensions"] = {"width": width, "height": height}
    fps = _rational(video.get("avg_frame_rate")) or _rational(video.get("r_frame_rate"))
    if fps is not None:
        observations["frame_rate"] = {"fps": fps}
    duration = _finite_number(container.get("duration"))
    if duration is None or duration <= 0:
        duration = _stream_duration(video)
    if duration is not None and duration > 0:
        observations["duration"] = {"duration_seconds": duration}
    video_duration = _stream_duration(video)
    audio_duration = _stream_duration(audio)
    if video_duration is not None and audio_duration is not None:
        observations["av_sync"] = {
            "max_offset_ms": abs(video_duration - audio_duration) * 1000,
        }
    color_space = _text(video.get("color_space"), limit=80)
    if color_space:
        observations["color_space"] = {"color_space": color_space}
    bit_rate = _finite_number(container.get("bit_rate"))
    if bit_rate is not None and bit_rate > 0:
        observations["bitrate"] = {"kbps": bit_rate / 1000}
    return observations


def _detect_static_and_black(
    path: Path,
    *,
    ffmpeg: str | None,
    duration_seconds: float | None,
) -> dict[str, Any]:
    if not ffmpeg:
        return {}
    result = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-vf",
            "blackdetect=d=0.1:pix_th=0.10,"
            "freezedetect=n=0.003:d=0.5",
            "-an",
            "-f",
            "null",
            os.devnull,
        ]
    )
    if result is None:
        return {}
    output = f"{result.stdout}\n{result.stderr}"

    def intervals(tag: str) -> list[tuple[float, float]]:
        starts: list[float] = []
        ends: list[float] = []
        for line in output.splitlines():
            if tag not in line:
                continue
            match = re.search(
                rf"{tag}:\s*(-?(?:\d+(?:\.\d+)?|\.\d+))",
                line,
                flags=re.IGNORECASE,
            )
            if not match:
                continue
            value = float(match.group(1))
            if tag == "black_start":
                starts.append(value)
            elif tag == "black_end":
                ends.append(value)
            else:
                starts.append(value)
        if tag == "black_start":
            return list(zip(starts, ends, strict=False))
        if not starts:
            return []
        frozen_ends = [
            float(match.group(1))
            for line in output.splitlines()
            if "freeze_end" in line
            for match in [
                re.search(
                    r"freeze_end:\s*(-?(?:\d+(?:\.\d+)?|\.\d+))",
                    line,
                    flags=re.IGNORECASE,
                )
            ]
            if match
        ]
        spans: list[tuple[float, float]] = []
        for index, start in enumerate(starts):
            end = (
                frozen_ends[index]
                if index < len(frozen_ends)
                else duration_seconds
                if duration_seconds is not None
                else start
            )
            spans.append((start, end))
        return spans

    def ratio(spans: list[tuple[float, float]]) -> float:
        if duration_seconds is None or duration_seconds <= 0:
            return 0.0
        total = sum(max(0.0, end - start) for start, end in spans)
        return max(0.0, min(total / duration_seconds, 1.0))

    black_spans = intervals("black_start")
    freeze_spans = intervals("freeze_start")
    return {
        "black_frames": {
            "ratio": ratio(black_spans),
            "interval_count": len(black_spans),
        },
        "freeze_frames": {
            "ratio": ratio(freeze_spans),
            "interval_count": len(freeze_spans),
        },
    }


def _probe_loudness(
    path: Path,
    *,
    ffmpeg: str | None,
    has_audio: bool,
) -> dict[str, Any]:
    if not ffmpeg:
        return {}
    if not has_audio:
        return {
            "audio_activity": {
                "has_audio": False,
                "measurable": False,
                "all_silent": True,
            }
        }
    result = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            delivery_loudnorm_filter(
                include_safety_trim=False,
                print_format="json",
            ),
            "-f",
            "null",
            os.devnull,
        ]
    )
    if result is None:
        return {
            "audio_activity": {
                "has_audio": True,
                "measurable": False,
                "all_silent": None,
            }
        }
    payload = _parse_json_object(f"{result.stdout}\n{result.stderr}")
    integrated = _finite_number(payload.get("input_i"))
    true_peak = _finite_number(payload.get("input_tp"))
    silent_marker = _text(payload.get("input_i"), limit=20).casefold() in {
        "-inf",
        "inf",
        "-infinity",
        "infinity",
    }
    return {
        "audio_activity": {
            "has_audio": True,
            "measurable": integrated is not None,
            "all_silent": (
                True
                if silent_marker or (integrated is not None and integrated <= -70)
                else False
                if integrated is not None
                else None
            ),
        },
        "loudness": {
            "integrated_lufs": integrated,
            "input_i": payload.get("input_i"),
        },
        "true_peak": {
            "true_peak_db": true_peak,
            "input_tp": payload.get("input_tp"),
        },
    }


def build_final_film_engineering_qc(
    path: Path,
    *,
    expected_duration_seconds: float | None = None,
    expected_fps: float | None = None,
    width: int | None = None,
    height: int | None = None,
    subtitles_required: bool = False,
) -> dict[str, Any]:
    """Return a delivery QC receipt backed by the actual final MP4."""

    video_path = Path(path)
    if not video_path.is_file() or video_path.stat().st_size <= 0:
        return compile_delivery_qc_contract(
            observations=None,
            target={"allowed_containers": ["mp4"]},
            required_checks=_REQUIRED_CHECKS,
        )
    ffprobe = _media_binary("ffprobe")
    ffmpeg = _media_binary("ffmpeg")
    observations = _probe_streams(video_path, ffprobe)
    duration = _finite_number(
        (observations.get("duration") or {}).get("duration_seconds")
        if isinstance(observations.get("duration"), Mapping)
        else None
    )
    observations.update(
        _detect_static_and_black(
            video_path,
            ffmpeg=ffmpeg,
            duration_seconds=duration,
        )
    )
    observations.update(
        _probe_loudness(
            video_path,
            ffmpeg=ffmpeg,
            has_audio=observations.get("audio_stream_present") is True,
        )
    )
    subtitle_evidence = observations.get("subtitle_stream")
    if isinstance(subtitle_evidence, dict):
        observations["subtitle_stream"] = {
            **subtitle_evidence,
            "required": bool(subtitles_required),
        }
    observations["file_readback"] = True
    observations["sha256"] = _sha256_file(video_path)
    target: dict[str, Any] = {
        "allowed_containers": ["mp4"],
        "width": width,
        "height": height,
        "duration_seconds": expected_duration_seconds,
        "duration_tolerance": 1.0,
        "fps": expected_fps,
        "fps_tolerance": 0.5,
        "integrated_lufs": DELIVERY_INTEGRATED_LUFS,
        "loudness_tolerance": DELIVERY_LOUDNESS_TOLERANCE_LU,
        "true_peak_db": DELIVERY_TRUE_PEAK_DBTP,
        "color_space": DELIVERY_COLOR_SPACE,
        "max_black_frame_ratio": 0.02,
        "max_freeze_frame_ratio": 0.02,
        "max_av_sync_ms": 100.0,
    }
    return compile_delivery_qc_contract(
        observations=observations,
        target=target,
        required_checks=_REQUIRED_CHECKS,
    )


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["build_final_film_engineering_qc"]
