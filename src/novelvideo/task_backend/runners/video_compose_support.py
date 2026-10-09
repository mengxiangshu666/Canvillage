"""Pure helpers for the compose_episode FFmpeg timeline."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess

from novelvideo.media_binaries import bundled_media_binary
from novelvideo.services.delivery_audio import delivery_loudnorm_filter
from novelvideo.services.delivery_video import delivery_color_params_filter


_COMPOSE_VIDEO_ENCODER_PREFERENCE = (
    "libx264",
    "h264_mf",
    "libopenh264",
    "mpeg4",
)


def ffmpeg_xfade_transition(kind: object) -> str:
    """Return an ``xfade`` transition name for a normalized plan kind."""

    normalized = str(kind or "").strip().casefold()
    # FFmpeg exposes ``fade`` and ``dissolve`` but not a separate
    # ``crossfade`` video transition; its fade transition is the visual
    # crossfade counterpart to the audio ``acrossfade`` filter.
    return {
        "crossfade": "fade",
        "dissolve": "dissolve",
        "fade": "fade",
    }.get(normalized, "fade")


def build_transition_subtitle_entries(
    beats: list[dict],
    durations: list[float],
    transition_plans: list[dict[str, object]],
) -> list[tuple[int, float, float, str]]:
    """Build subtitle entries on the same net timeline as the edit graph."""

    output_cursor = 0.0
    sequence = 0
    entries: list[tuple[int, float, float, str]] = []
    for index, beat in enumerate(beats):
        duration = max(0.0, float(durations[index]))
        incoming_overlap = 0.0
        if index > 0:
            plan = transition_plans[index]
            if str(plan.get("transition_kind") or "") != "hard_cut":
                try:
                    incoming_overlap = max(
                        0.0, float(plan.get("duration_seconds") or 0.0)
                    )
                except (TypeError, ValueError):
                    incoming_overlap = 0.0
                incoming_overlap = min(
                    incoming_overlap, max(0.0, float(durations[index - 1]))
                )
        outgoing_overlap = 0.0
        if index + 1 < len(transition_plans):
            plan = transition_plans[index + 1]
            if str(plan.get("transition_kind") or "") != "hard_cut":
                try:
                    outgoing_overlap = max(
                        0.0, float(plan.get("duration_seconds") or 0.0)
                    )
                except (TypeError, ValueError):
                    outgoing_overlap = 0.0
                outgoing_overlap = min(outgoing_overlap, duration)
        start = output_cursor + incoming_overlap
        end = output_cursor + max(0.0, duration - outgoing_overlap)
        audio_type = str(beat.get("audio_type") or "").strip().casefold()
        narration = str(
            beat.get("dialogue_text")
            if audio_type == "dialogue" and beat.get("dialogue_text")
            else beat.get("narration_segment", "")
            or ""
        )
        if narration and end > start:
            sequence += 1
            entries.append((sequence, start, end, narration))
        output_cursor += duration - outgoing_overlap
    return entries


def escape_ffmpeg_subtitle_path(path: Path) -> str:
    value = path.resolve().as_posix().replace("\\", "\\\\")
    return value.replace(":", r"\:").replace("'", r"\'")


def select_compose_video_encoder() -> str:
    """Pick an encoder present in the FFmpeg binary used by the app."""

    ffmpeg = bundled_media_binary("ffmpeg") or "ffmpeg"
    try:
        probe = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"FFmpeg 视频编码器探测失败: {exc}") from exc

    output = "\n".join(str(value or "") for value in (probe.stdout, probe.stderr))
    for encoder in _COMPOSE_VIDEO_ENCODER_PREFERENCE:
        if re.search(rf"(?m)^\s*V.....\s+{re.escape(encoder)}\b", output):
            return encoder
    raise RuntimeError(
        "当前 FFmpeg 没有可用的视频编码器；"
        f"已检查 {', '.join(_COMPOSE_VIDEO_ENCODER_PREFERENCE)}"
    )


def compose_video_encoder_args(encoder: str) -> list[str]:
    """Return quality options supported by the selected encoder."""

    args = ["-c:v", encoder]
    if encoder == "libx264":
        return [*args, "-preset", "fast", "-crf", "23"]
    if encoder in {"h264_mf", "libopenh264"}:
        return [*args, "-b:v", "4M"]
    return [*args, "-q:v", "3"]


def append_delivery_loudness(
    filter_parts: list[str],
    input_label: str,
    *,
    output_label: str = "outloud",
) -> str:
    """Append final-audio loudness normalization and return its output label."""

    filter_parts.append(
        f"{input_label}{delivery_loudnorm_filter()}[{output_label}]"
    )
    return f"[{output_label}]"


def compose_color_params() -> str:
    """Return the BT.709 filter fragment used on every composed video stream."""

    return delivery_color_params_filter()


__all__ = [
    "append_delivery_loudness",
    "build_transition_subtitle_entries",
    "compose_color_params",
    "compose_video_encoder_args",
    "escape_ffmpeg_subtitle_path",
    "ffmpeg_xfade_transition",
    "select_compose_video_encoder",
]
