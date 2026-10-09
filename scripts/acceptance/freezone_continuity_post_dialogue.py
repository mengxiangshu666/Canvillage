"""Rebuild dialogue for the continuity film without trusting native video speech.

The video model is treated as a picture source only.  Its native audio is
discarded, fixed Edge voices are generated per line, and the dialogue is mixed
over a simple ambience bed.  This is the deterministic fallback for H3 runs
that improvise, skip, or garble spoken lines.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FFMPEG = ROOT / "runtime" / "ffmpeg" / "ffmpeg.exe"
FFPROBE = ROOT / "runtime" / "ffmpeg" / "ffprobe.exe"
ARTIFACT_DIR = ROOT / "workspace" / "artifacts" / "freezone-film"


@dataclass(frozen=True)
class DialogueLine:
    text: str
    voice: str
    start_seconds: float
    rate: str = "-8%"
    pitch: str = "+0Hz"
    volume: float = 1.0


DEFAULT_LINES = (
    DialogueLine(
        text="水位越过警戒线，七分钟后封港。",
        voice="zh-CN-YunyangNeural",
        start_seconds=5.2,
        rate="-6%",
        pitch="-6Hz",
        volume=0.78,
    ),
    DialogueLine(
        text="林默，船不等人。",
        voice="zh-CN-XiaoxiaoNeural",
        start_seconds=17.0,
        rate="-8%",
        pitch="-2Hz",
    ),
    DialogueLine(
        text="她十年前没有走，对吗？",
        voice="zh-CN-XiaoxiaoNeural",
        start_seconds=26.6,
        rate="-10%",
        pitch="-2Hz",
        volume=0.92,
    ),
    DialogueLine(
        text="这是我姐的。",
        voice="zh-CN-YunxiNeural",
        start_seconds=31.2,
        rate="-10%",
        pitch="-4Hz",
        volume=0.94,
    ),
    DialogueLine(
        text="别让这些名字沉下去。",
        voice="zh-CN-YunxiNeural",
        start_seconds=39.0,
        rate="-8%",
        pitch="-4Hz",
    ),
    DialogueLine(
        text="如果钟声能让人记住。",
        voice="zh-CN-YunxiNeural",
        start_seconds=51.8,
        rate="-9%",
        pitch="-4Hz",
        volume=0.96,
    ),
    DialogueLine(
        text="那就让全城听见。后来，夜航船都能听见它。",
        voice="zh-CN-XiaoxiaoNeural",
        start_seconds=63.3,
        rate="-8%",
        pitch="-2Hz",
        volume=0.94,
    ),
)


def _now_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stderr[-2000:]}"
        )
    return completed


def _probe_duration(path: Path) -> float:
    completed = _run(
        [
            str(FFPROBE),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    return float(completed.stdout.strip())


async def _synthesize_line(line: DialogueLine, output_path: Path) -> float:
    import edge_tts

    communicate = edge_tts.Communicate(
        line.text,
        line.voice,
        rate=line.rate,
        pitch=line.pitch,
        volume="+0%",
    )
    await communicate.save(str(output_path))
    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise RuntimeError(f"edge-tts produced no audio for {line.text!r}")
    return _probe_duration(output_path)


def _resolve_videos(root: Path, prefix: str) -> list[Path]:
    videos = sorted(root.glob(f"{prefix}-*-S*.mp4"))
    if len(videos) != 5:
        raise RuntimeError(
            f"expected 5 sequence videos under {root} using prefix {prefix!r}, "
            f"found {len(videos)}"
        )
    return videos


def _build_mix_command(
    *,
    videos: list[Path],
    voice_paths: list[Path],
    lines: tuple[DialogueLine, ...],
    total_duration: float,
    output_path: Path,
) -> list[str]:
    command = [str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error"]
    for video in videos:
        command.extend(["-i", str(video)])
    for voice_path in voice_paths:
        command.extend(["-i", str(voice_path)])

    filters: list[str] = []
    filters.append(
        "".join(f"[{index}:v]" for index in range(len(videos)))
        + f"concat=n={len(videos)}:v=1:a=0[vout]"
    )

    mix_labels: list[str] = []
    for index, line in enumerate(lines):
        input_index = len(videos) + index
        delay_ms = max(0, round(line.start_seconds * 1000))
        label = f"voice{index}"
        filters.append(
            f"[{input_index}:a]adelay={delay_ms}|{delay_ms},"
            f"volume={line.volume:.3f}[{label}]"
        )
        mix_labels.append(f"[{label}]")

    filters.append(
        f"anoisesrc=color=pink:duration={total_duration:.6f}:"
        "sample_rate=48000:amplitude=0.22,"
        "highpass=f=450,lowpass=f=9000,volume=0.12[rain]"
    )
    filters.append(
        f"anoisesrc=color=brown:duration={total_duration:.6f}:"
        "sample_rate=48000:amplitude=0.35,"
        "lowpass=f=180,volume=0.08[rumble]"
    )
    mix_labels.extend(["[rain]", "[rumble]"])
    filters.append(
        "".join(mix_labels)
        + f"amix=inputs={len(mix_labels)}:duration=longest:normalize=0,"
        + f"atrim=0:{total_duration:.6f},"
        + "loudnorm=I=-16:LRA=11:TP=-1.5[aout]"
    )

    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libopenh264",
            "-b:v",
            "8M",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            "-shortest",
            str(output_path),
        ]
    )
    return command


def run(args: argparse.Namespace) -> dict[str, object]:
    artifact_root = Path(args.artifact_root).resolve()
    videos = _resolve_videos(artifact_root, args.video_prefix)
    stamp = _now_stamp()
    work_dir = artifact_root / f"{stamp}-post-dialogue"
    work_dir.mkdir(parents=True, exist_ok=True)

    lines = DEFAULT_LINES
    voice_paths: list[Path] = []
    voice_receipts: list[dict[str, object]] = []
    for index, line in enumerate(lines, start=1):
        voice_path = work_dir / f"line-{index:02d}.mp3"
        duration = asyncio.run(_synthesize_line(line, voice_path))
        voice_paths.append(voice_path)
        voice_receipts.append(
            {
                "index": index,
                "text": line.text,
                "voice": line.voice,
                "startSeconds": line.start_seconds,
                "durationSeconds": round(duration, 3),
                "path": str(voice_path),
                "sha256": hashlib.sha256(voice_path.read_bytes()).hexdigest(),
            }
        )

    total_duration = sum(_probe_duration(video) for video in videos)
    output_path = artifact_root / f"{stamp}-last-ferry-dialogue-rebuild.mp4"
    _run(
        _build_mix_command(
            videos=videos,
            voice_paths=voice_paths,
            lines=lines,
            total_duration=total_duration,
            output_path=output_path,
        )
    )

    receipt = {
        "ok": True,
        "schema": "continuity_external_dialogue.v1",
        "generatedAt": stamp,
        "sourceVideos": [
            {
                "path": str(path),
                "durationSeconds": round(_probe_duration(path), 3),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in videos
        ],
        "dialogue": voice_receipts,
        "audioPolicy": {
            "nativeVideoAudio": "discarded",
            "dialogueSource": "edge-tts",
            "ambience": "synthetic rain and low rumble",
        },
        "output": {
            "path": str(output_path),
            "durationSeconds": round(_probe_duration(output_path), 3),
            "bytes": output_path.stat().st_size,
            "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        },
    }
    receipt_path = artifact_root / f"{stamp}-last-ferry-dialogue-rebuild.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    receipt["receiptPath"] = str(receipt_path)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replace unreliable H3 dialogue with fixed post-produced voices."
    )
    parser.add_argument(
        "--artifact-root",
        default=str(ARTIFACT_DIR),
        help="Directory containing the five generated sequence MP4 files.",
    )
    parser.add_argument(
        "--video-prefix",
        default="20260917T170502Z",
        help="Prefix shared by the five sequence MP4 files.",
    )
    args = parser.parse_args()
    receipt = run(args)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
