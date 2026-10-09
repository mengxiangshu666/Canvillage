"""Generate and validate native H3 dialogue candidates in parallel.

Each plan row becomes one H3 job.  The returned video keeps H3's native audio
and mouth motion in the same generation pass; no post-production dubbing is
involved.  Whisper is only a quality gate that decides whether the take is
usable or needs another native H3 attempt.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from types import SimpleNamespace
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import freezone_h3_native_dialogue_probe as probe  # noqa: E402


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _normalize_for_match(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("七", "7")
    return re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE).casefold()


def _match_score(expected: str, transcript: str) -> float:
    left = _normalize_for_match(expected)
    right = _normalize_for_match(transcript)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _extract_audio(video_path: Path, ffmpeg: Path) -> Path:
    audio_path = video_path.with_suffix(".wav")
    subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(audio_path),
        ],
        check=True,
    )
    return audio_path


def _transcribe(
    audio_path: Path,
    *,
    whisper_exe: Path,
    model: str,
    model_dir: Path,
) -> dict[str, Any]:
    output_dir = audio_path.parent / f"{audio_path.stem}-asr"
    output_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(whisper_exe),
            str(audio_path),
            "--model",
            model,
            "--model_dir",
            str(model_dir),
            "--language",
            "Chinese",
            "--task",
            "transcribe",
            "--output_dir",
            str(output_dir),
            "--output_format",
            "json",
            "--fp16",
            "False",
            "--word_timestamps",
            "True",
            "--verbose",
            "False",
        ],
        check=True,
    )
    json_path = output_dir / f"{audio_path.stem}.json"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected Whisper payload: {json_path}")
    return payload


def _run_one(
    item: dict[str, Any],
    *,
    candidate: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    line_id = str(item.get("id") or "").strip()
    turns = [
        str(turn or "").strip()
        for turn in (item.get("turns") or ())
        if str(turn or "").strip()
    ]
    expected = str(item.get("text") or "").strip() or " ".join(turns)
    if not turns:
        turns = [expected]
    label = f"{line_id}-candidate-{candidate}"
    image_path = Path(str(item.get("image") or probe.DEFAULT_IMAGE))
    if not image_path.is_absolute():
        image_path = probe.ROOT / image_path
    namespace = SimpleNamespace(
        base_url=args.base_url,
        video_model=args.video_model,
        image=str(image_path),
        text=expected,
        turns=turns,
        speaker=str(item.get("speaker") or "林默"),
        prompt=str(item.get("prompt") or "").strip(),
        output_label=label,
        duration=int(item.get("duration") or args.duration),
        timeout_seconds=args.timeout_seconds,
        generation_timeout=args.generation_timeout,
        keep_project=args.keep_project,
        allow_paid_generation=args.allow_paid_generation,
        no_voice_reference=True,
    )
    result: dict[str, Any] = {
        "id": line_id,
        "candidate": candidate,
        "expected": expected,
        "speaker": namespace.speaker,
        "duration": namespace.duration,
        "label": label,
        "ok": False,
    }
    try:
        receipt = probe._run(namespace)
        video_path = Path(str(receipt["videoPath"])).resolve()
        result.update(
            {
                "generationOk": True,
                "videoPath": str(video_path),
                "videoSha256": receipt.get("videoSha256"),
                "videoBytes": receipt.get("videoBytes"),
                "generationSeconds": receipt.get("generationSeconds"),
            }
        )
        if args.skip_asr:
            result["ok"] = True
            return result
        audio_path = _extract_audio(video_path, args.ffmpeg)
        asr = _transcribe(
            audio_path,
            whisper_exe=args.whisper_exe,
            model=args.whisper_model,
            model_dir=args.whisper_model_dir,
        )
        transcript = str(asr.get("text") or "").strip()
        score = _match_score(expected, transcript)
        result.update(
            {
                "audioPath": str(audio_path),
                "transcript": transcript,
                "matchScore": round(score, 4),
                "ok": score >= args.threshold,
            }
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _load_plan(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("lines") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("dialogue plan must contain a non-empty list")
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise RuntimeError(f"dialogue plan row {index} is not an object")
        if not str(row.get("text") or "").strip():
            raise RuntimeError(f"dialogue plan row {index} has no text")
        normalized.append(dict(row))
    return normalized


def _select_best(
    rows: list[dict[str, Any]],
    *,
    output_dir: Path,
    stamp: str,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for line_id in dict.fromkeys(str(row.get("id") or "") for row in rows):
        candidates = [row for row in rows if row.get("id") == line_id]
        ranked = sorted(
            candidates,
            key=lambda row: (
                float(row.get("matchScore") or 0.0),
                bool(row.get("generationOk")),
            ),
            reverse=True,
        )
        best = ranked[0]
        record = dict(best)
        source = Path(str(best.get("videoPath") or ""))
        if source.is_file():
            target = output_dir / f"{stamp}-{line_id}-selected.mp4"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            record["selectedVideoPath"] = str(target.resolve())
        record["candidateCount"] = len(candidates)
        selected.append(record)
    return selected


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.allow_paid_generation:
        raise RuntimeError(
            "paid generation is disabled; pass --allow-paid-generation to run"
        )
    started_at = _stamp()
    plan = _load_plan(Path(args.plan).resolve())
    jobs = [
        (item, candidate)
        for item in plan
        for candidate in range(1, max(1, args.candidates) + 1)
    ]
    workers = max(1, min(args.workers, len(jobs)))
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="h3-dialogue") as pool:
        futures = {
            pool.submit(_run_one, item, candidate=candidate, args=args): (
                str(item.get("id") or ""),
                candidate,
            )
            for item, candidate in jobs
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(
                json.dumps(
                    {
                        "id": result.get("id"),
                        "candidate": result.get("candidate"),
                        "ok": result.get("ok"),
                        "score": result.get("matchScore"),
                        "transcript": result.get("transcript"),
                        "error": result.get("error"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    results.sort(key=lambda row: (str(row.get("id") or ""), int(row.get("candidate") or 0)))
    stamp = _stamp()
    selected = _select_best(
        results,
        output_dir=probe.ARTIFACT_DIR / "selected",
        stamp=stamp,
    )
    receipt = {
        "ok": all(bool(row.get("ok")) for row in selected),
        "schema": "h3_native_dialogue_batch.v1",
        "startedAt": started_at,
        "finishedAt": stamp,
        "plan": str(Path(args.plan).resolve()),
        "candidates": args.candidates,
        "workers": workers,
        "threshold": args.threshold,
        "results": results,
        "selected": selected,
    }
    receipt_path = probe.ARTIFACT_DIR / f"{stamp}-native-dialogue-batch.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    receipt["receiptPath"] = str(receipt_path)
    return receipt


def main() -> int:
    root = probe.ROOT
    parser = argparse.ArgumentParser(
        description="Generate and ASR-validate native H3 dialogue candidates."
    )
    parser.add_argument("--plan", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8785")
    parser.add_argument("--video-model", default=probe.DEFAULT_VIDEO_MODEL)
    parser.add_argument("--duration", type=int, default=6)
    parser.add_argument("--candidates", type=int, default=1)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--threshold", type=float, default=0.85)
    parser.add_argument("--skip-asr", action="store_true")
    parser.add_argument("--whisper-exe", default="")
    parser.add_argument("--whisper-model", default="small")
    parser.add_argument("--whisper-model-dir", default="")
    parser.add_argument(
        "--ffmpeg",
        default=str(root / "runtime" / "ffmpeg" / "ffmpeg.exe"),
    )
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument("--generation-timeout", type=float, default=1200)
    parser.add_argument("--keep-project", action="store_true")
    parser.add_argument("--allow-paid-generation", action="store_true")
    args = parser.parse_args()
    if not args.ffmpeg:
        raise RuntimeError("--ffmpeg is required")
    if not args.whisper_exe and not args.skip_asr:
        args.whisper_exe = shutil.which("whisper") or ""
    if not args.whisper_model_dir:
        args.whisper_model_dir = str(Path.home() / ".cache" / "whisper")
    if not args.skip_asr and not args.whisper_exe:
        raise RuntimeError("--whisper-exe is required unless --skip-asr is set")
    args.ffmpeg = Path(args.ffmpeg).resolve()
    args.whisper_exe = Path(args.whisper_exe).resolve() if args.whisper_exe else None
    args.whisper_model_dir = Path(args.whisper_model_dir).resolve()
    print(json.dumps(_run(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
