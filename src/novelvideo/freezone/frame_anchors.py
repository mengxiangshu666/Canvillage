"""Extracted frame time-anchor manifest helpers.

Kept outside the already-large jobs module; media probing stays in jobs and is
imported lazily to avoid a circular import at module load.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

FRAME_ANCHOR_SCHEMA = "freezone_frame_anchors.v1"


async def _probe_stream_time_base(video_path: Path) -> tuple[int, int] | None:
    from novelvideo.freezone.jobs import _bundled_media_binary

    ffprobe = _bundled_media_binary("ffprobe")
    if not ffprobe:
        return None
    proc = await asyncio.to_thread(
        subprocess.run,
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=time_base",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        return None
    match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", proc.stdout or "")
    if not match:
        return None
    numerator, denominator = int(match.group(1)), int(match.group(2))
    return (numerator, denominator) if numerator > 0 and denominator > 0 else None


async def _safe_video_duration(video_path: Path) -> float | None:
    try:
        from novelvideo.freezone.jobs import _probe_video_duration

        return await _probe_video_duration(str(video_path))
    except Exception:
        return None


async def _write_frame_anchor_manifest(
    *,
    video_path: Path,
    out_dir: Path,
    frames: list[Path],
    sampling_mode: str,
) -> Path | None:
    """Persist where each extracted frame actually came from.

    Scene-detected filenames carry the source PTS; even-sampled frames get a
    clearly marked estimate from the probed duration. Missing time base stays
    null instead of inventing a precise anchor.
    """

    import hashlib

    time_base = await _probe_stream_time_base(video_path)
    duration = await _safe_video_duration(video_path)

    def _source_sha256() -> str:
        digest = hashlib.sha256()
        with video_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    source_sha256 = await asyncio.to_thread(_source_sha256)
    records: list[dict[str, Any]] = []
    for index, frame in enumerate(frames):
        frame_pts: int | None = None
        time_seconds: float | None = None
        estimated = sampling_mode == "even_sample"
        if sampling_mode == "scene_detect":
            match = re.search(r"(\d+)$", frame.stem)
            if match:
                frame_pts = int(match.group(1))
                if time_base:
                    time_seconds = round(frame_pts * time_base[0] / time_base[1], 6)
        elif duration is not None and frames:
            time_seconds = round(index * duration / len(frames), 6)
        records.append({
            "index": index,
            "file": frame.name,
            "frame_pts": frame_pts,
            "time_seconds": time_seconds,
            "estimated": estimated,
            "source_kind": sampling_mode,
        })
    payload = {
        "schema": FRAME_ANCHOR_SCHEMA,
        "source_file": video_path.name,
        "source_sha256": source_sha256,
        "source_duration_seconds": duration,
        "time_base": f"{time_base[0]}/{time_base[1]}" if time_base else None,
        "sampling_mode": sampling_mode,
        "frames": records,
        "transcript_available": False,
    }
    path = out_dir / "frame_anchors.json"
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
        return path
    except OSError:
        return None


def read_freezone_frame_anchors(project_dir: Path, job_id: str) -> dict[str, Any] | None:
    from novelvideo.freezone.jobs import outputs_dir

    path = outputs_dir(project_dir, "freezone_extract") / job_id / "frame_anchors.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != FRAME_ANCHOR_SCHEMA:
        return None
    if not isinstance(payload.get("frames"), list):
        return None
    return payload