"""Generate a continuity-first short film through the product's own endpoints.

The film is planned as a small number of maximum-length sequence units rather
than many isolated short shots.  Six shared boundary keyframes define every
hand-off; each 15-second MiniMax-H3 unit receives the boundary frame before and
after it through ``/freezone/video/keyframes`` in ``firstLastFrame`` mode.

Paid generation is fail-closed.  Run:

    .venv\\Scripts\\python.exe scripts\\acceptance\\freezone_continuity_film_smoke.py ^
        --allow-paid-generation
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import mimetypes
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import freezone_full_film_smoke as base  # noqa: E402


ROOT = base.ROOT
ARTIFACT_DIR = base.ARTIFACT_DIR
FFMPEG = ROOT / "runtime" / "ffmpeg" / "ffmpeg.exe"
FFPROBE = ROOT / "runtime" / "ffmpeg" / "ffprobe.exe"
DEFAULT_PLAN = SCRIPT_DIR / "fixtures" / "last_ferry_continuity_plan.json"


def _load_plan(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("continuity plan must be a JSON object")
    title = str(payload.get("title") or "").strip()
    keyframes = payload.get("keyframes")
    sequences = payload.get("sequences")
    if not title:
        raise RuntimeError("continuity plan has no title")
    if not isinstance(keyframes, list) or not keyframes:
        raise RuntimeError("continuity plan has no keyframes")
    if not isinstance(sequences, list) or not sequences:
        raise RuntimeError("continuity plan has no sequences")

    keyframe_ids: set[str] = set()
    for item in keyframes:
        if not isinstance(item, dict):
            raise RuntimeError("every keyframe must be an object")
        keyframe_id = str(item.get("id") or "").strip()
        if not keyframe_id or keyframe_id in keyframe_ids:
            raise RuntimeError(f"invalid or duplicate keyframe id: {keyframe_id!r}")
        if not str(item.get("prompt") or "").strip():
            raise RuntimeError(f"keyframe {keyframe_id} has no prompt")
        keyframe_ids.add(keyframe_id)

    sequence_ids: set[str] = set()
    for item in sequences:
        if not isinstance(item, dict):
            raise RuntimeError("every sequence must be an object")
        sequence_id = str(item.get("id") or "").strip()
        if not sequence_id or sequence_id in sequence_ids:
            raise RuntimeError(f"invalid or duplicate sequence id: {sequence_id!r}")
        start = str(item.get("start_keyframe") or "").strip()
        end = str(item.get("end_keyframe") or "").strip()
        if start not in keyframe_ids or end not in keyframe_ids:
            raise RuntimeError(
                f"sequence {sequence_id} references unknown keyframes: {start!r} -> {end!r}"
            )
        if not str(item.get("prompt") or "").strip():
            raise RuntimeError(f"sequence {sequence_id} has no prompt")
        dialogue = item.get("dialogue") or []
        if not isinstance(dialogue, list) or any(
            not str(line or "").strip() for line in dialogue
        ):
            raise RuntimeError(f"sequence {sequence_id} has invalid dialogue")
        sequence_ids.add(sequence_id)
    return payload


def _continuity_context(plan: dict[str, Any]) -> str:
    bible = plan.get("continuity_bible")
    if not isinstance(bible, dict):
        return ""
    parts: list[str] = []
    characters = bible.get("characters")
    if isinstance(characters, list) and characters:
        parts.append("角色卡：" + "；".join(str(item) for item in characters))
    for label, key in (
        ("世界连续性", "world"),
        ("银幕方向", "screen_direction"),
        ("服装道具连续", "wardrobe_props"),
        ("统一视觉", "style"),
        ("声音连续", "audio"),
    ):
        value = str(bible.get(key) or "").strip()
        if value:
            parts.append(f"{label}：{value}")
    return "\n".join(parts)


def _keyframe_rows(plan: dict[str, Any]) -> list[dict[str, Any]]:
    continuity = _continuity_context(plan)
    rows: list[dict[str, Any]] = []
    for item in plan["keyframes"]:
        prompt = str(item["prompt"]).strip()
        if continuity:
            prompt = f"{prompt}\n{continuity}"
        rows.append(
            {
                "keyframe_id": str(item["id"]),
                "label": str(item.get("label") or item["id"]),
                "shot_prompt": prompt,
                "image_model": str(item.get("image_model") or "").strip(),
                "fallback_image_model": str(
                    item.get("fallback_image_model") or ""
                ).strip(),
            }
        )
    return rows


def _sequence_prompt(sequence: dict[str, Any], plan: dict[str, Any]) -> str:
    continuity = _continuity_context(plan)
    prompt = str(sequence["prompt"]).strip()
    return f"{prompt}\n{continuity}" if continuity else prompt


def _reusable_keyframe(prefix: str, index: int) -> Path | None:
    if not prefix:
        return None
    candidate = ARTIFACT_DIR / f"{prefix}-{index:02d}-storyboard.png"
    return candidate if candidate.exists() else None


def _render_keyframe(
    client: base.RuntimeClient,
    project_id: str,
    args: argparse.Namespace,
    *,
    image_model: str,
    canvas_id: str,
    nonce: str,
    stamp: str,
    index: int,
    row: dict[str, Any],
) -> dict[str, Any]:
    keyframe_id = str(row["keyframe_id"])
    label = f"keyframe {keyframe_id}"
    reused = _reusable_keyframe(args.reuse_keyframe_prefix, index)
    if reused is not None:
        started = time.monotonic()
        image_path = reused
        image_url = base._upload_image(client, project_id, reused)
        image_bytes = reused.stat().st_size
        image_sha = hashlib.sha256(reused.read_bytes()).hexdigest()
        base._log(f"{label}: reusing {reused.name}")
    else:
        candidate_models = list(
            dict.fromkeys(
                value
                for value in (
                    str(row.get("image_model") or "").strip(),
                    image_model,
                    str(row.get("fallback_image_model") or "").strip(),
                )
                if value
            )
        )
        last_error: Exception | None = None
        image_result: dict[str, Any] = {}
        for attempt, candidate_model in enumerate(candidate_models, start=1):
            base._log(
                f"{label}: generating with {candidate_model} "
                f"(attempt {attempt}/{len(candidate_models)})"
            )
            started = time.monotonic()
            try:
                image_job = base._data(
                    client.post(
                        base._project_path(project_id, "freezone/gen"),
                        json={
                            "prompt": str(row["shot_prompt"]),
                            "aspect_ratio": args.aspect_ratio,
                            "image_size": args.image_size,
                            "quality": args.image_quality,
                            "provider": "direct",
                            "model": candidate_model,
                            "model_id": candidate_model,
                            "gen_mode": "textToImage",
                            "canvas_id": canvas_id,
                            "node_id": f"film-keyframe-{index}-{nonce}-{attempt}",
                        },
                    )
                )
                base._log(f"{label}: image submitted ({image_job.get('job_id')})")
                image_result = base._poll_job(
                    client,
                    project_id,
                    task_type=str(image_job.get("task_type") or "freezone_gen"),
                    job_id=str(image_job.get("job_id") or ""),
                    label=f"{label} image",
                    timeout_seconds=args.image_timeout,
                )
                break
            except Exception as exc:  # noqa: BLE001 - try the declared fallback
                last_error = exc
                if attempt >= len(candidate_models):
                    raise
                base._log(f"{label}: {candidate_model} failed; trying fallback")
        if not image_result:
            raise RuntimeError(f"{label}: no image model produced a result") from last_error
        image_url = str(image_result.get("url") or "")
        if not image_url:
            raise RuntimeError(f"{label}: image result has no url: {image_result}")
        image_path = ARTIFACT_DIR / f"{stamp}-{index:02d}-storyboard.png"
        image_bytes, image_sha = base._download(client, image_url, image_path)
    elapsed = time.monotonic() - started
    base._log(f"{label}: ready in {elapsed:.0f}s ({image_bytes} bytes)")
    return {
        "index": index,
        "row": row,
        "imageUrl": image_url,
        "imageBytes": image_bytes,
        "imageSha256": image_sha,
        "imagePath": str(image_path),
        "imageSeconds": round(elapsed, 2),
    }


def _upload_file(
    client: base.RuntimeClient,
    project_id: str,
    path: Path,
    *,
    content_type: str = "",
) -> str:
    media_type = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with path.open("rb") as handle:
        response = client.client.post(
            base._project_path(project_id, "freezone/upload"),
            files={"file": (path.name, handle, media_type)},
        )
    payload = base._json(response)
    if response.status_code != 200:
        raise RuntimeError(
            f"upload of {path.name} returned HTTP {response.status_code}: {payload}"
        )
    url = str(base._data(payload).get("url") or "")
    if not url:
        raise RuntimeError(f"upload of {path.name} returned no url: {payload}")
    return url


def _run_keyframes_parallel(
    client: base.RuntimeClient,
    project_id: str,
    args: argparse.Namespace,
    *,
    rows: list[dict[str, Any]],
    image_model: str,
    canvas_id: str,
    nonce: str,
    stamp: str,
) -> list[dict[str, Any]]:
    workers = max(1, min(args.image_concurrency, len(rows)))
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="keyframe") as pool:
        futures = {
            pool.submit(
                _render_keyframe,
                client,
                project_id,
                args,
                image_model=image_model,
                canvas_id=canvas_id,
                nonce=nonce,
                stamp=stamp,
                index=index,
                row=row,
            ): index
            for index, row in enumerate(rows, start=1)
        }
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: item["index"])


def _video_payload(
    args: argparse.Namespace,
    *,
    sequence: dict[str, Any],
    plan: dict[str, Any],
    start_url: str,
    end_url: str,
    canvas_id: str,
    nonce: str,
    index: int,
) -> dict[str, Any]:
    dialogue = [str(line).strip() for line in sequence.get("dialogue") or []]
    payload: dict[str, Any] = {
        "first_frame_url": start_url,
        "last_frame_url": end_url,
        "prompt": _sequence_prompt(sequence, plan),
        "aspect_ratio": args.aspect_ratio,
        "resolution": args.resolution,
        "duration_seconds": args.duration,
        "generate_audio": True,
        "native_audio_strategy": "native",
        "model": args.video_model,
        "model_id": args.video_model,
        "gen_mode": "firstLastFrame",
        "canvas_id": canvas_id,
        "node_id": f"film-sequence-{index}-{nonce}",
    }
    if dialogue:
        payload.update(
            {
                "dialogue_text": " ".join(dialogue),
                "spoken_dialogue": dialogue,
                "audio_type": "dialogue",
                "speaker": str(sequence.get("speaker") or "").strip(),
            }
        )
    return payload


def _submit_sequence(
    client: base.RuntimeClient,
    project_id: str,
    args: argparse.Namespace,
    *,
    sequence: dict[str, Any],
    plan: dict[str, Any],
    keyframe_urls: dict[str, str],
    canvas_id: str,
    nonce: str,
    index: int,
) -> dict[str, Any]:
    sequence_id = str(sequence["id"])
    start_id = str(sequence["start_keyframe"])
    end_id = str(sequence["end_keyframe"])
    payload = _video_payload(
        args,
        sequence=sequence,
        plan=plan,
        start_url=keyframe_urls[start_id],
        end_url=keyframe_urls[end_id],
        canvas_id=canvas_id,
        nonce=nonce,
        index=index,
    )
    started = time.monotonic()
    job = base._data(
        client.post(
            base._project_path(project_id, "freezone/video/keyframes"),
            json=payload,
        )
    )
    elapsed = time.monotonic() - started
    dialogue = " / ".join(payload.get("spoken_dialogue") or [])
    base._log(
        f"{sequence_id}: keyframe video submitted ({job.get('job_id')})"
        + (f", dialogue={dialogue[:56]}" if dialogue else ", no dialogue")
    )
    return {
        "index": index,
        "sequence": sequence,
        "payload": payload,
        "videoJob": job,
        "videoSubmitSeconds": round(elapsed, 2),
    }


def _probe_video(path: Path) -> dict[str, Any]:
    command = [
        str(FFPROBE),
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path.name}: {completed.stderr[:800]}")
    payload = json.loads(completed.stdout or "{}")
    streams = payload.get("streams") if isinstance(payload, dict) else []
    if not isinstance(streams, list):
        streams = []
    video = next(
        (item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"),
        {},
    )
    audio = next(
        (item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"),
        {},
    )
    fmt = payload.get("format") if isinstance(payload, dict) else {}
    if not isinstance(fmt, dict):
        fmt = {}
    try:
        duration = float(fmt.get("duration") or video.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "durationSeconds": round(duration, 6),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "videoCodec": str(video.get("codec_name") or ""),
        "audioCodec": str(audio.get("codec_name") or ""),
        "hasAudio": bool(audio),
        "sizeBytes": path.stat().st_size,
    }


def _wait_sequence(
    client: base.RuntimeClient,
    project_id: str,
    args: argparse.Namespace,
    *,
    stamp: str,
    submitted: dict[str, Any],
) -> dict[str, Any]:
    index = int(submitted["index"])
    sequence = submitted["sequence"]
    sequence_id = str(sequence["id"])
    job = submitted["videoJob"]
    started = time.monotonic()
    result = base._poll_job(
        client,
        project_id,
        task_type=str(job.get("task_type") or "freezone_video_gen"),
        job_id=str(job.get("job_id") or ""),
        label=f"{sequence_id} video",
        timeout_seconds=args.video_timeout,
    )
    video_url = str(result.get("url") or "")
    if not video_url:
        raise RuntimeError(f"{sequence_id}: video result has no url: {result}")
    video_path = ARTIFACT_DIR / f"{stamp}-{index:02d}-{sequence_id}.mp4"
    video_bytes, video_sha = base._download(client, video_url, video_path)
    if not base._is_mp4(video_path):
        raise RuntimeError(f"{sequence_id}: downloaded artifact is not an mp4")
    probe = _probe_video(video_path)
    elapsed = time.monotonic() - started
    base._log(
        f"{sequence_id}: clip saved in {elapsed:.0f}s "
        f"({probe['durationSeconds']:.3f}s, {video_bytes} bytes)"
    )
    return {
        "index": index,
        "sequenceId": sequence_id,
        "startKeyframe": str(sequence["start_keyframe"]),
        "endKeyframe": str(sequence["end_keyframe"]),
        "dialogue": list(sequence.get("dialogue") or []),
        "speaker": str(sequence.get("speaker") or ""),
        "prompt": str(submitted["payload"].get("prompt") or ""),
        "videoJobId": str(job.get("job_id") or ""),
        "videoUrl": video_url,
        "videoBytes": video_bytes,
        "videoSha256": video_sha,
        "videoSeconds": round(elapsed, 2),
        "probe": probe,
        "mp4": str(video_path),
    }


def _reuse_sequence_videos(
    client: base.RuntimeClient,
    project_id: str,
    args: argparse.Namespace,
    *,
    plan: dict[str, Any],
) -> list[dict[str, Any]] | None:
    prefix = str(args.reuse_video_prefix or "").strip()
    if not prefix:
        return None
    pending: list[tuple[int, dict[str, Any], Path]] = []
    for index, sequence in enumerate(plan["sequences"], start=1):
        sequence_id = str(sequence["id"])
        candidate = ARTIFACT_DIR / f"{prefix}-{index:02d}-{sequence_id}.mp4"
        if not candidate.exists():
            return None
        pending.append((index, sequence, candidate))

    reused: list[dict[str, Any]] = []
    for index, sequence, video_path in pending:
        sequence_id = str(sequence["id"])
        video_url = _upload_file(client, project_id, video_path, content_type="video/mp4")
        probe = _probe_video(video_path)
        reused.append(
            {
                "index": index,
                "sequenceId": sequence_id,
                "startKeyframe": str(sequence["start_keyframe"]),
                "endKeyframe": str(sequence["end_keyframe"]),
                "dialogue": list(sequence.get("dialogue") or []),
                "speaker": str(sequence.get("speaker") or ""),
                "prompt": _sequence_prompt(sequence, plan),
                "videoJobId": "",
                "videoUrl": video_url,
                "videoBytes": video_path.stat().st_size,
                "videoSha256": hashlib.sha256(video_path.read_bytes()).hexdigest(),
                "videoSeconds": 0.0,
                "probe": probe,
                "mp4": str(video_path),
            }
        )
    base._log(f"reused {len(reused)} generated sequence videos; no video re-submission")
    return sorted(reused, key=lambda item: item["index"])


def _run_sequences_parallel(
    client: base.RuntimeClient,
    project_id: str,
    args: argparse.Namespace,
    *,
    plan: dict[str, Any],
    keyframe_urls: dict[str, str],
    canvas_id: str,
    nonce: str,
    stamp: str,
) -> list[dict[str, Any]]:
    reused = _reuse_sequence_videos(
        client,
        project_id,
        args,
        plan=plan,
    )
    if reused is not None:
        return reused
    sequences = [item for item in plan["sequences"] if isinstance(item, dict)]
    submitted = [
        _submit_sequence(
            client,
            project_id,
            args,
            sequence=sequence,
            plan=plan,
            keyframe_urls=keyframe_urls,
            canvas_id=canvas_id,
            nonce=nonce,
            index=index,
        )
        for index, sequence in enumerate(sequences, start=1)
    ]
    workers = max(1, min(args.video_concurrency, len(submitted)))
    base._log(
        f"all {len(submitted)} sequence jobs submitted; waiting with "
        f"{workers} concurrent pollers"
    )
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sequence") as pool:
        futures = {
            pool.submit(
                _wait_sequence,
                client,
                project_id,
                args,
                stamp=stamp,
                submitted=item,
            ): str(item["sequence"]["id"])
            for item in submitted
        }
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - aggregate all sequence failures
                errors.append(f"{futures[future]}: {exc}")
    if errors:
        raise RuntimeError("parallel continuity generation failed:\n" + "\n".join(errors))
    return sorted(results, key=lambda item: item["index"])


def _extract_frame(video: Path, target: Path, *, last: bool) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error"]
    if last:
        command.extend(["-sseof", "-0.08"])
    else:
        command.extend(["-ss", "0"])
    command.extend(["-i", str(video), "-frames:v", "1", str(target)])
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0 or not target.exists():
        raise RuntimeError(f"frame extraction failed for {video.name}: {completed.stderr[:500]}")


def _ssim(first: Path, second: Path) -> dict[str, float]:
    command = [
        str(FFMPEG),
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        str(first),
        "-i",
        str(second),
        "-lavfi",
        (
            "[0:v]scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2:black[a];"
            "[1:v]scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2:black[b];"
            "[a][b]ssim"
        ),
        "-f",
        "null",
        "-",
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    match = re.search(r"All:\s*([0-9.]+)", output)
    if completed.returncode != 0 or not match:
        raise RuntimeError(f"SSIM failed for {first.name} / {second.name}: {output[-800:]}")
    all_value = float(match.group(1))
    return {"ssim": round(all_value, 6), "ssimPercent": round(all_value * 100, 3)}


def _run_seam_qc(
    sequences: list[dict[str, Any]],
    keyframe_paths: dict[str, Path],
    stamp: str,
) -> dict[str, Any]:
    qc_dir = ARTIFACT_DIR / f"{stamp}-seams"
    qc_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for index in range(len(sequences) - 1):
        left = sequences[index]
        right = sequences[index + 1]
        boundary_id = str(left["endKeyframe"])
        if str(right["startKeyframe"]) != boundary_id:
            raise RuntimeError(
                f"sequence seam is not shared: {left['sequenceId']} -> {right['sequenceId']}"
            )
        left_video = Path(str(left["mp4"]))
        right_video = Path(str(right["mp4"]))
        left_last = qc_dir / f"{index + 1:02d}-left-last.png"
        right_first = qc_dir / f"{index + 1:02d}-right-first.png"
        _extract_frame(left_video, left_last, last=True)
        _extract_frame(right_video, right_first, last=False)
        boundary = keyframe_paths[boundary_id]
        left_adherence = _ssim(boundary, left_last)
        right_adherence = _ssim(boundary, right_first)
        direct_handoff = _ssim(left_last, right_first)
        rows.append(
            {
                "seam": index + 1,
                "fromSequence": str(left["sequenceId"]),
                "toSequence": str(right["sequenceId"]),
                "boundaryKeyframe": boundary_id,
                "leftLastVsBoundary": left_adherence,
                "rightFirstVsBoundary": right_adherence,
                "directFrameHandoff": direct_handoff,
                "passed": (
                    left_adherence["ssim"] >= 0.72
                    and right_adherence["ssim"] >= 0.72
                    and direct_handoff["ssim"] >= 0.62
                ),
            }
        )
    return {
        "schema": "continuity_seam_qc.v1",
        "passed": all(item["passed"] for item in rows),
        "thresholds": {
            "leftLastVsBoundary": 0.72,
            "rightFirstVsBoundary": 0.72,
            "directFrameHandoff": 0.62,
        },
        "seams": rows,
    }


def _compose(
    client: base.RuntimeClient,
    project_id: str,
    args: argparse.Namespace,
    *,
    title: str,
    canvas_id: str,
    sequences: list[dict[str, Any]],
    stamp: str,
) -> dict[str, Any]:
    cursor = 0.0
    items: list[dict[str, Any]] = []
    for index, sequence in enumerate(sequences, start=1):
        duration = float(sequence["probe"]["durationSeconds"])
        if duration <= 0:
            raise RuntimeError(f"{sequence['sequenceId']} has zero duration")
        items.append(
            {
                "item_id": f"sequence-{index:02d}",
                "source_url": str(sequence["videoUrl"]),
                "timeline_start": round(cursor, 6),
                "source_start": 0,
                "source_end": round(duration, 6),
                "volume": 1,
                "muted": False,
            }
        )
        cursor += duration
    compose_job = base._data(
        client.post(
            base._project_path(project_id, "freezone/video/compose"),
            json={
                "title": title,
                "canvas_id": canvas_id,
                "resolution": args.compose_resolution,
                "fps": args.fps,
                "background_color": "#000000",
                "keep_original_audio": True,
                "tracks": [{"track_id": "video-1", "kind": "video", "items": items}],
            },
        )
    )
    base._log(f"compose submitted ({compose_job.get('job_id')})")
    compose_result = base._poll_job(
        client,
        project_id,
        task_type=str(compose_job.get("task_type") or "freezone_video_compose"),
        job_id=str(compose_job.get("job_id") or ""),
        label="compose",
        timeout_seconds=args.compose_timeout,
    )
    film_url = str(compose_result.get("url") or "")
    if not film_url:
        raise RuntimeError(f"compose result has no url: {compose_result}")
    film_path = ARTIFACT_DIR / f"{stamp}-last-ferry-film.mp4"
    film_bytes, film_sha = base._download(client, film_url, film_path)
    if not base._is_mp4(film_path):
        raise RuntimeError("composed film is not an mp4")
    return {
        "filmUrl": film_url,
        "filmPath": str(film_path),
        "filmBytes": film_bytes,
        "filmSha256": film_sha,
        "expectedFilmSeconds": round(cursor, 6),
        "probe": _probe_video(film_path),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.allow_paid_generation:
        raise RuntimeError(
            "paid generation is disabled; pass --allow-paid-generation to authorize "
            "keyframe and video requests"
        )
    plan_path = Path(args.plan_file).expanduser()
    if not plan_path.is_absolute():
        plan_path = (Path.cwd() / plan_path).resolve()
    plan = _load_plan(plan_path)
    if args.duration > 15:
        raise RuntimeError("MiniMax-H3 sequence duration cannot exceed 15 seconds")

    stamp = base._stamp()
    nonce = base.uuid.uuid4().hex[:10]
    project_name = f"zz_codex_continuity_{stamp.lower()}_{nonce}"
    canvas_id = f"film_continuity_{nonce}"
    client = base.RuntimeClient(args.base_url, args.timeout_seconds)
    project_id = ""
    receipt: dict[str, Any] = {
        "ok": False,
        "startedAt": stamp,
        "title": str(plan["title"]),
        "planPath": str(plan_path),
        "projectName": project_name,
        "videoBackend": args.video_model,
        "resolution": args.resolution,
        "aspectRatio": args.aspect_ratio,
        "sequenceDurationSeconds": args.duration,
        "sequenceCountPlanned": len(plan["sequences"]),
        "keyframeCountPlanned": len(plan["keyframes"]),
    }
    try:
        base._log(f"health {client.get('/healthz')}")
        project = base._data(client.post("/api/v1/projects", json={"name": project_name}))
        project_id = str(project.get("project_id") or project.get("id") or "")
        if not project_id:
            raise RuntimeError(f"project creation returned no id: {project}")
        receipt["projectId"] = project_id
        base._log(f"project {project_id} ({project_name})")

        image_model = base._select_image_model(client)
        image_registry = str(image_model.get("id") or "")
        catalog_model = f"direct/{image_registry}"
        base._log(f"image model {catalog_model}")

        rows = _keyframe_rows(plan)
        keyframes = _run_keyframes_parallel(
            client,
            project_id,
            args,
            rows=rows,
            image_model=catalog_model,
            canvas_id=canvas_id,
            nonce=nonce,
            stamp=stamp,
        )
        keyframe_urls: dict[str, str] = {}
        keyframe_paths: dict[str, Path] = {}
        keyframe_receipts: list[dict[str, Any]] = []
        for item in keyframes:
            keyframe_id = str(item["row"]["keyframe_id"])
            keyframe_urls[keyframe_id] = str(item["imageUrl"])
            keyframe_paths[keyframe_id] = Path(str(item["imagePath"]))
            keyframe_receipts.append(
                {
                    "id": keyframe_id,
                    "label": str(item["row"]["label"]),
                    "imageUrl": str(item["imageUrl"]),
                    "imagePath": str(item["imagePath"]),
                    "imageBytes": int(item["imageBytes"]),
                    "imageSha256": str(item["imageSha256"]),
                    "imageSeconds": float(item["imageSeconds"]),
                }
            )
        receipt["keyframes"] = keyframe_receipts

        sequences = _run_sequences_parallel(
            client,
            project_id,
            args,
            plan=plan,
            keyframe_urls=keyframe_urls,
            canvas_id=canvas_id,
            nonce=nonce,
            stamp=stamp,
        )
        receipt["sequences"] = sequences
        receipt["seamQc"] = _run_seam_qc(sequences, keyframe_paths, stamp)
        composed = _compose(
            client,
            project_id,
            args,
            title=str(plan["title"]),
            canvas_id=canvas_id,
            sequences=sequences,
            stamp=stamp,
        )
        receipt.update(composed)
        receipt.update(
            {
                "ok": True,
                "finishedAt": base._stamp(),
                "sequenceCount": len(sequences),
                "handoffCount": max(0, len(sequences) - 1),
                "imageConcurrency": args.image_concurrency,
                "videoConcurrency": args.video_concurrency,
            }
        )
    finally:
        if project_id and not args.keep_project:
            try:
                client.post(base._project_path(project_id, "delete"))
                client.post(base._project_path(project_id, "purge"))
                receipt["projectPurged"] = True
                base._log(f"project {project_id} deleted and purged")
            except Exception as exc:  # noqa: BLE001 - cleanup must not mask the run
                receipt["projectPurged"] = False
                receipt["purgeError"] = str(exc)
                base._log(f"project cleanup failed: {exc}")
        client.close()

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    latest = ARTIFACT_DIR / "latest-continuity-film.json"
    latest.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return receipt


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8784")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--plan-file", default=str(DEFAULT_PLAN))
    parser.add_argument("--duration", type=int, default=15)
    parser.add_argument("--aspect-ratio", default=base.DEFAULT_ASPECT_RATIO)
    parser.add_argument("--resolution", default=base.DEFAULT_VIDEO_RESOLUTION)
    parser.add_argument("--video-model", default=base.DEFAULT_VIDEO_BACKEND)
    parser.add_argument("--image-size", default="1K")
    parser.add_argument("--image-quality", default="medium")
    parser.add_argument("--compose-resolution", default="720p", choices=["720p", "1080p"])
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--image-timeout", type=float, default=900.0)
    parser.add_argument("--video-timeout", type=float, default=2400.0)
    parser.add_argument("--compose-timeout", type=float, default=900.0)
    parser.add_argument("--image-concurrency", type=int, default=6)
    parser.add_argument("--video-concurrency", type=int, default=5)
    parser.add_argument(
        "--reuse-keyframe-prefix",
        default="",
        help=(
            "reuse exact keyframe files named <prefix>-NN-storyboard.png; "
            "missing indexes are generated"
        ),
    )
    parser.add_argument(
        "--reuse-video-prefix",
        default="",
        help=(
            "reuse exact generated clips named <prefix>-NN-<sequence>.mp4; "
            "all clips must exist or normal paid generation resumes"
        ),
    )
    parser.add_argument("--keep-project", action="store_true")
    parser.add_argument("--allow-paid-generation", action="store_true")
    args = parser.parse_args()
    args.image_concurrency = max(1, int(args.image_concurrency))
    args.video_concurrency = max(1, int(args.video_concurrency))
    return args


def main() -> int:
    args = _parse_args()
    if not args.allow_paid_generation:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "paid_generation_not_authorized",
                    "hint": (
                        "re-run with --allow-paid-generation to authorize real "
                        "keyframe and video requests"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    receipt = run(args)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
