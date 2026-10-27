"""Content-level delivery guard for a finished film.

The existing ``final_film_qc`` proves the container is technically valid.  It
cannot tell whether the video actually starts on the registered keyframe,
whether a continuous seam keeps its motion, whether a sound bridge broke, or
whether a dialogue shot really has audible speech.  This module checks those
facts against a small manifest and fails closed when the evidence or the media
tools are missing.

Layout:

* :func:`collect_media_facts` does all FFmpeg/Pillow I/O.
* :func:`evaluate_media_facts` is pure and unit-testable.
* :func:`audit_delivery_media` is the convenience entry point.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageFilter, UnidentifiedImageError

MEDIA_GUARD_SCHEMA = "village.delivery-media-guard.v1"
MEDIA_MANIFEST_SCHEMA = "village.delivery-media-manifest.v1"
PASSED = "passed"
FAILED = "failed"
NOT_RUN = "not_run"

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_BUNDLED_FFMPEG = _PROJECT_ROOT / "runtime" / "ffmpeg"
_COMMAND_TIMEOUT_SECONDS = 120
_SILENCE_DB = -55.0
_SOUND_BRIDGE_MAX_DELTA_DB = 12.0
_FRAME_HASH_DISTANCE = 14
_SEAM_HASH_DISTANCE = 18
# Edge-map mean absolute difference.  Real calibration (2026-10-25):
# correct first frame 12.9–14.5, wrong keyframe 39.5+, continuous-but-posed
# seam 27.6, different-start seam 45.9.  22 separates the observed groups.
_FRAME_EDGE_DISTANCE = 22.0
_SEAM_EDGE_DISTANCE = 22.0
_MEAN_VOLUME_RE = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


@dataclass(frozen=True, slots=True)
class MediaFactError(RuntimeError):
    code: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - exception formatting
        return self.message


def _text(value: object, *, limit: int = 2000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _number(value: object, default: float = 0.0) -> float:
    if value in (None, "") or isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed else default


def _binary(name: str) -> str | None:
    executable = _BUNDLED_FFMPEG / f"{name}.exe"
    if executable.is_file():
        return str(executable)
    return shutil.which(name)


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
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
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaFactError("media_guard.command_failed", str(exc)) from exc


def _probe(path: Path) -> dict[str, Any]:
    ffprobe = _binary("ffprobe")
    if not ffprobe:
        raise MediaFactError("media_guard.ffprobe_missing", "ffprobe is not available")
    if not path.is_file() or path.stat().st_size <= 0:
        raise MediaFactError("media_guard.file_missing", f"media file is missing: {path}")
    result = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height,avg_frame_rate",
            "-of",
            "json",
            str(path),
        ]
    )
    if result.returncode != 0:
        raise MediaFactError(
            "media_guard.probe_failed", _text(result.stderr or result.stdout, limit=500)
        )
    try:
        payload = json.loads(result.stdout or "{}")
    except ValueError as exc:
        raise MediaFactError("media_guard.probe_invalid", str(exc)) from exc
    streams = payload.get("streams") if isinstance(payload, dict) else []
    streams = [item for item in streams if isinstance(item, Mapping)]
    duration = _number((payload.get("format") or {}).get("duration"))
    return {
        "duration_seconds": duration,
        "has_video": any(item.get("codec_type") == "video" for item in streams),
        "has_audio": any(item.get("codec_type") == "audio" for item in streams),
        "video_streams": [
            {
                "width": item.get("width"),
                "height": item.get("height"),
                "avg_frame_rate": item.get("avg_frame_rate"),
            }
            for item in streams
            if item.get("codec_type") == "video"
        ],
    }


def _mean_volume(path: Path, *, start: float | None = None, duration: float | None = None) -> float | None:
    ffmpeg = _binary("ffmpeg")
    if not ffmpeg:
        raise MediaFactError("media_guard.ffmpeg_missing", "ffmpeg is not available")
    command = [ffmpeg, "-hide_banner", "-nostats"]
    if start is not None and start > 0:
        command += ["-ss", f"{start:.3f}"]
    if duration is not None and duration > 0:
        command += ["-t", f"{duration:.3f}"]
    command += ["-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"]
    result = _run(command)
    text = f"{result.stderr}\n{result.stdout}"
    match = _MEAN_VOLUME_RE.search(text)
    if not match:
        return None
    return float(match.group(1))


def _frame_signatures(
    path: Path, *, at: float | None = None, last: bool = False
) -> tuple[str | None, bytes | None]:
    """Extract one frame and return both the average hash and the edge map."""

    ffmpeg = _binary("ffmpeg")
    if not ffmpeg:
        raise MediaFactError("media_guard.ffmpeg_missing", "ffmpeg is not available")
    with tempfile.TemporaryDirectory(prefix="village-frame-") as directory:
        target = Path(directory) / "frame.png"
        command = [ffmpeg, "-hide_banner", "-nostats", "-y"]
        if last:
            command += ["-sseof", "-0.2"]
        elif at is not None and at > 0:
            command += ["-ss", f"{at:.3f}"]
        command += ["-i", str(path), "-frames:v", "1", "-f", "image2", str(target)]
        result = _run(command)
        if result.returncode != 0 or not target.is_file():
            raise MediaFactError(
                "media_guard.frame_extract_failed",
                _text(result.stderr or result.stdout, limit=500),
            )
        return _image_signatures(target)


def _image_signatures(path: Path) -> tuple[str, bytes]:
    try:
        with Image.open(path) as image:
            grayscale = image.convert("L")
            small = grayscale.resize((16, 16), Image.Resampling.BILINEAR)
            edge = grayscale.resize((96, 54), Image.Resampling.BILINEAR).filter(
                ImageFilter.FIND_EDGES
            )
    except (OSError, UnidentifiedImageError) as exc:
        raise MediaFactError("media_guard.image_unreadable", str(exc)) from exc
    pixels = list(small.getdata())
    average = sum(pixels) / len(pixels)
    bits = "".join("1" if pixel >= average else "0" for pixel in pixels)
    return f"{int(bits, 2):064x}", edge.tobytes()


def _frame_hash(path: Path, *, at: float | None = None, last: bool = False) -> str | None:
    return _frame_signatures(path, at=at, last=last)[0]


def _image_hash(path: Path) -> str:
    return _image_signatures(path)[0]


def _edge_distance(left: bytes | None, right: bytes | None) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    return round(
        sum(abs(a - b) for a, b in zip(left, right)) / len(left), 2
    )


def _hash_distance(left: str | None, right: str | None) -> int | None:
    if not left or not right:
        return None
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return None


def _require_path(value: object, *, field: str) -> Path:
    raw = _text(value, limit=4000)
    if not raw:
        raise MediaFactError("media_guard.path_missing", f"{field} is missing")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (_PROJECT_ROOT / path).resolve()
    return path


def collect_media_facts(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Read every media fact the evaluator needs.  Missing tools fail closed."""

    final_video = _require_path(manifest.get("final_video"), field="final_video")
    facts: dict[str, Any] = {
        "final": {"probe": _probe(final_video), "mean_volume_db": None, "error": ""},
        "shots": [],
        "seams": [],
    }
    if facts["final"]["probe"]["has_audio"]:
        facts["final"]["mean_volume_db"] = _mean_volume(final_video)
    for raw in manifest.get("shots") or []:
        if not isinstance(raw, Mapping):
            continue
        shot_id = _text(raw.get("shot_id"), limit=160)
        clip = _require_path(raw.get("clip"), field=f"shots[{shot_id}].clip")
        keyframe = _require_path(raw.get("keyframe"), field=f"shots[{shot_id}].keyframe")
        clip_probe = _probe(clip)
        first_hash, first_edge = _frame_signatures(clip, at=0.0)
        keyframe_hash, keyframe_edge = _image_signatures(keyframe)
        shot_fact: dict[str, Any] = {
            "shot_id": shot_id,
            "clip": str(clip),
            "keyframe": str(keyframe),
            "probe": clip_probe,
            "first_frame_hash": first_hash,
            "first_frame_edge": first_edge,
            "keyframe_hash": keyframe_hash,
            "keyframe_edge": keyframe_edge,
            "mean_volume_db": (
                _mean_volume(clip) if clip_probe["has_audio"] else None
            ),
            "has_dialogue": bool(raw.get("has_dialogue")),
        }
        facts["shots"].append(shot_fact)
    for raw in manifest.get("seams") or []:
        if not isinstance(raw, Mapping):
            continue
        timeline_seconds = _number(raw.get("timeline_seconds"), default=-1.0)
        seam = {
            "from_shot": _text(raw.get("from_shot"), limit=160),
            "to_shot": _text(raw.get("to_shot"), limit=160),
            "continuous": bool(raw.get("continuous")),
            "timeline_seconds": timeline_seconds if timeline_seconds >= 0 else None,
            "left_volume_db": None,
            "right_volume_db": None,
        }
        if (
            seam["timeline_seconds"] is not None
            and facts["final"]["probe"].get("has_audio")
        ):
            start = float(seam["timeline_seconds"])
            seam["left_volume_db"] = _mean_volume(
                final_video, start=max(0.0, start - 0.35), duration=0.35
            )
            seam["right_volume_db"] = _mean_volume(
                final_video, start=start, duration=0.35
            )
        facts["seams"].append(seam)
    return facts


def _check(name: str, status: str, *, message: str = "", **evidence: Any) -> dict[str, Any]:
    return {
        "check": name,
        "status": status,
        "message": message,
        **evidence,
    }


def _fact_error(facts: Mapping[str, Any]) -> str:
    final = facts.get("final")
    if isinstance(final, Mapping):
        return _text(final.get("error"))
    return ""


def evaluate_media_facts(
    facts: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    silence_db: float = _SILENCE_DB,
    sound_bridge_max_delta_db: float = _SOUND_BRIDGE_MAX_DELTA_DB,
    frame_hash_distance: int = _FRAME_HASH_DISTANCE,
    seam_hash_distance: int = _SEAM_HASH_DISTANCE,
    frame_edge_distance: float = _FRAME_EDGE_DISTANCE,
    seam_edge_distance: float = _SEAM_EDGE_DISTANCE,
) -> dict[str, Any]:
    """Turn collected facts into a deterministic, evidence-bearing report."""

    checks: list[dict[str, Any]] = []
    error = _fact_error(facts)
    if error:
        checks.append(_check("media_facts", NOT_RUN, message=error))
        return {
            "schema": MEDIA_GUARD_SCHEMA,
            "status": NOT_RUN,
            "passed": False,
            "checks": checks,
            "failures": 0,
            "not_run": 1,
        }

    final = facts.get("final") if isinstance(facts.get("final"), Mapping) else {}
    probe = final.get("probe") if isinstance(final.get("probe"), Mapping) else {}
    checks.append(
        _check(
            "final_audio_stream",
            PASSED if probe.get("has_audio") else FAILED,
            message="" if probe.get("has_audio") else "成片没有音轨",
            observed=bool(probe.get("has_audio")),
            threshold=True,
        )
    )
    mean_volume = final.get("mean_volume_db")
    checks.append(
        _check(
            "final_audio_activity",
            PASSED
            if isinstance(mean_volume, (int, float)) and mean_volume > silence_db
            else FAILED,
            message=(
                ""
                if isinstance(mean_volume, (int, float)) and mean_volume > silence_db
                else "成片音轨缺失或近似静音"
            ),
            observed=mean_volume,
            threshold=f"> {silence_db} dB mean_volume",
        )
    )

    shots = {
        _text(item.get("shot_id")): item
        for item in (facts.get("shots") or [])
        if isinstance(item, Mapping)
    }
    for shot in facts.get("shots") or []:
        if not isinstance(shot, Mapping):
            continue
        shot_id = _text(shot.get("shot_id"))
        distance = _hash_distance(
            _text(shot.get("first_frame_hash")), _text(shot.get("keyframe_hash"))
        )
        edge = _edge_distance(
            shot.get("first_frame_edge"), shot.get("keyframe_edge")
        )
        first_frame_ok = (
            distance is not None
            and distance <= frame_hash_distance
            and edge is not None
            and edge <= frame_edge_distance
        )
        checks.append(
            _check(
                f"shot_first_frame:{shot_id}",
                PASSED if first_frame_ok else FAILED,
                message=(
                    ""
                    if first_frame_ok
                    else "视频第一帧与登记关键帧不一致"
                ),
                observed=distance,
                edge_distance=edge,
                threshold=(
                    f"hamming <= {frame_hash_distance}; "
                    f"edge <= {frame_edge_distance}"
                ),
            )
        )
        if shot.get("has_dialogue"):
            volume = shot.get("mean_volume_db")
            checks.append(
                _check(
                    f"shot_dialogue_audio:{shot_id}",
                    PASSED
                    if isinstance(volume, (int, float)) and volume > silence_db
                    else FAILED,
                    message=(
                        ""
                        if isinstance(volume, (int, float)) and volume > silence_db
                        else "有台词镜头没有可听音频"
                    ),
                    observed=volume,
                    threshold=f"> {silence_db} dB mean_volume",
                )
            )

    for seam in facts.get("seams") or []:
        if not isinstance(seam, Mapping):
            continue
        from_shot = _text(seam.get("from_shot"))
        to_shot = _text(seam.get("to_shot"))
        if not seam.get("continuous"):
            continue
        left = shots.get(from_shot)
        right = shots.get(to_shot)
        if not left or not right:
            checks.append(
                _check(
                    f"seam:{from_shot}->{to_shot}",
                    NOT_RUN,
                    message="接缝两侧片段未登记",
                )
            )
            continue
        left_hash = _text(left.get("last_frame_hash"))
        right_hash = _text(right.get("first_frame_hash"))
        distance = _hash_distance(left_hash, right_hash)
        edge = _edge_distance(
            left.get("last_frame_edge"), right.get("first_frame_edge")
        )
        seam_ok = (
            distance is not None
            and distance <= seam_hash_distance
            and edge is not None
            and edge <= seam_edge_distance
        )
        checks.append(
            _check(
                f"seam_frame:{from_shot}->{to_shot}",
                PASSED if seam_ok else FAILED,
                message=(
                    ""
                    if seam_ok
                    else "连续接点两侧首尾帧差异过大；可能瞬移、转头或速度归零"
                ),
                observed=distance,
                edge_distance=edge,
                threshold=(
                    f"hamming <= {seam_hash_distance}; "
                    f"edge <= {seam_edge_distance}"
                ),
            )
        )
        left_volume = seam.get("left_volume_db")
        right_volume = seam.get("right_volume_db")
        sample_source = "final_film_window"
        if not isinstance(left_volume, (int, float)) or not isinstance(right_volume, (int, float)):
            left_volume = left.get("mean_volume_db")
            right_volume = right.get("mean_volume_db")
            sample_source = "source_clip_mean"
        if isinstance(left_volume, (int, float)) and isinstance(right_volume, (int, float)):
            delta = abs(float(left_volume) - float(right_volume))
            checks.append(
                _check(
                    f"sound_bridge:{from_shot}->{to_shot}",
                    PASSED if delta <= sound_bridge_max_delta_db else FAILED,
                    message=(
                        ""
                        if delta <= sound_bridge_max_delta_db
                        else "声桥两侧平均电平跳变超过阈值"
                    ),
                    observed=round(delta, 2),
                    threshold=f"<= {sound_bridge_max_delta_db} dB",
                    left_db=round(float(left_volume), 2),
                    right_db=round(float(right_volume), 2),
                    sample_source=sample_source,
                )
            )
        else:
            checks.append(
                _check(
                    f"sound_bridge:{from_shot}->{to_shot}",
                    NOT_RUN,
                    message="接缝一侧没有可用音频样本",
                )
            )

    failures = sum(1 for item in checks if item["status"] == FAILED)
    not_run = sum(1 for item in checks if item["status"] == NOT_RUN)
    return {
        "schema": MEDIA_GUARD_SCHEMA,
        "status": FAILED if failures else (NOT_RUN if not_run else PASSED),
        "passed": failures == 0 and not_run == 0,
        "checks": checks,
        "failures": failures,
        "not_run": not_run,
    }


def audit_delivery_media(manifest: object) -> dict[str, Any]:
    """Collect facts and evaluate a manifest; missing evidence fails closed."""

    if not isinstance(manifest, Mapping):
        return {
            "schema": MEDIA_GUARD_SCHEMA,
            "status": NOT_RUN,
            "passed": False,
            "checks": [_check("manifest", NOT_RUN, message="manifest 不是对象")],
            "failures": 0,
            "not_run": 1,
        }
    if _text(manifest.get("schema")) not in ("", MEDIA_MANIFEST_SCHEMA):
        return {
            "schema": MEDIA_GUARD_SCHEMA,
            "status": NOT_RUN,
            "passed": False,
            "checks": [
                _check(
                    "manifest",
                    NOT_RUN,
                    message=f"manifest schema 不支持：{manifest.get('schema')}",
                )
            ],
            "failures": 0,
            "not_run": 1,
        }
    try:
        facts = collect_media_facts(manifest)
    except MediaFactError as exc:
        return {
            "schema": MEDIA_GUARD_SCHEMA,
            "status": NOT_RUN,
            "passed": False,
            "checks": [_check("media_facts", NOT_RUN, message=exc.message, code=exc.code)],
            "failures": 0,
            "not_run": 1,
        }
    for shot in manifest.get("shots") or []:
        if not isinstance(shot, Mapping):
            continue
        shot_id = _text(shot.get("shot_id"))
        fact = next(
            (item for item in facts["shots"] if item["shot_id"] == shot_id), None
        )
        if fact is not None:
            last_hash, last_edge = _frame_signatures(Path(fact["clip"]), last=True)
            fact["last_frame_hash"] = last_hash
            fact["last_frame_edge"] = last_edge
    return evaluate_media_facts(facts, manifest)


__all__ = [
    "FAILED",
    "MEDIA_GUARD_SCHEMA",
    "MEDIA_MANIFEST_SCHEMA",
    "MediaFactError",
    "NOT_RUN",
    "PASSED",
    "audit_delivery_media",
    "collect_media_facts",
    "evaluate_media_facts",
]
