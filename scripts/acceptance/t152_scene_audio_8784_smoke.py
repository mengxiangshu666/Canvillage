"""Verify one real shot keeps its scene audio on the running 8784 runtime.

The workflow used to submit every shot as ``generate_audio: False`` with
``audio_type: "silence"``.  A shot whose script only declares sound design
(rain, a shutter release) therefore came back as a silent film, and delivery QC
blocked the release.  This smoke drives the real ``freezone-final-film``
workflow once, on a temporary project, with the user's standing text / image /
video authorization, and proves the composed film carries audible sound.

Paid generation is fail-closed: pass ``--allow-paid-generation`` to authorize
one image and one video task.  No automatic retry is performed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from t113_production_8784_runner import (
    PRODUCTION_API_BASE,
    _cleanup_project,
    _create_project,
    _http_json,
    _seed_canvas,
)


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = ROOT / "workspace" / "artifacts" / "t152"
DEFAULT_OUTPUT = ARTIFACT_DIR / "scene-audio-8784-smoke.json"
PREFIX = "t152_scene_audio_"
CANVAS_ID = "t152_scene_audio_canvas"
WORKFLOW_ID = "freezone-final-film"
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "canceled"}
SILENCE_FLOOR_DB = -60.0


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _artifact(run: dict[str, Any], step_id: str) -> dict[str, Any]:
    artifacts = run.get("artifacts")
    value = artifacts.get(step_id) if isinstance(artifacts, dict) else None
    return value if isinstance(value, dict) else {}


def _model_id(config: dict[str, Any], family: str) -> str:
    models = config.get(family)
    if not isinstance(models, list):
        raise RuntimeError(f"model config has no {family!r} family")
    enabled = [
        item
        for item in models
        if isinstance(item, dict) and item.get("enabled") is True
    ]
    selected = next(
        (item for item in enabled if item.get("isDefault") is True),
        enabled[0] if enabled else None,
    )
    if not isinstance(selected, dict):
        raise RuntimeError(f"model config has no enabled {family!r} model")
    model_id = str(selected.get("id") or "").strip()
    if not model_id:
        raise RuntimeError(f"enabled {family!r} model has no registry id")
    return model_id


def _bindings(config: dict[str, Any]) -> dict[str, str]:
    direct = config.get("directModels")
    direct = direct if isinstance(direct, dict) else {}
    return {
        "director": _model_id(direct, "agent"),
        "text": _model_id(direct, "text"),
        "image": _model_id(direct, "image"),
        "video": _model_id(
            {"video": config.get("directVideoModels") or []},
            "video",
        ),
    }


def _start_run(project_id: str, bindings: dict[str, str]) -> str:
    turn_id = f"turn-{_stamp()}-t152"
    created = _http_json(
        "POST",
        f"{PRODUCTION_API_BASE}/projects/{project_id}/workflow-runs",
        body={
            "workflow_id": WORKFLOW_ID,
            "canvas_id": CANVAS_ID,
            "run_mode": "auto",
            "source_turn_id": turn_id,
            "inputs": {
                "request": "T-152 scene audio smoke",
                "target_strategy": "reuse_existing",
                "script_node_id": "script-a",
                "target_node_ids": ["script-a"],
                "auto_generate_paid_media": True,
                "add_subtitles": False,
                "aspect_ratio": "16:9",
                "production_authorization": {
                    "schema": "workflow_production_authorization.v1",
                    "scope": "workflow_run",
                    "workflow_id": WORKFLOW_ID,
                    "project_id": project_id,
                    "canvas_id": CANVAS_ID,
                    "source_turn_id": turn_id,
                    "source": "user_approval",
                    "max_paid_starts": 4,
                    "max_shots": 1,
                    "max_reference_images": 8,
                    "max_duration_seconds": 10,
                    "allow_final_film": True,
                },
            },
            "idempotency_key": f"t152-scene-audio-{turn_id}",
            "contract_version": 2,
            "model_bindings": bindings,
        },
        timeout=60.0,
    )
    run_id = str(created.get("id") or "").strip()
    if not run_id:
        raise RuntimeError("workflow start returned no run id")
    return run_id


def _poll_run(project_id: str, run_id: str, *, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] = {}
    last_log = 0.0
    while time.monotonic() < deadline:
        latest = _http_json(
            "GET",
            f"{PRODUCTION_API_BASE}/projects/{project_id}/workflow-runs/{run_id}",
            timeout=30.0,
        )
        if str(latest.get("status") or "") in TERMINAL_STATUSES:
            return latest
        now = time.monotonic()
        if now - last_log >= 20.0:
            last_log = now
            step = str(latest.get("current_step") or latest.get("step_id") or "")
            print(
                f"[{_stamp()[9:15]}] run {latest.get('status')} "
                f"step={step or '-'} progress={latest.get('progress')}",
                flush=True,
            )
        time.sleep(5.0)
    raise RuntimeError("timed out waiting for the final-film run")


def _measure_audio(path: Path) -> dict[str, Any]:
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,codec_name,channels,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    streams = json.loads(probe.stdout).get("streams") or []
    audio = next(
        (item for item in streams if item.get("codec_type") == "audio"),
        None,
    )
    levels = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    output = f"{levels.stdout}\n{levels.stderr}"
    mean_volume = _parse_volume(output, "mean_volume")
    max_volume = _parse_volume(output, "max_volume")
    return {
        "has_audio": audio is not None,
        "audio_codec": str((audio or {}).get("codec_name") or ""),
        "audio_channels": int((audio or {}).get("channels") or 0),
        "mean_volume_db": mean_volume,
        "max_volume_db": max_volume,
        "audible": bool(
            audio is not None
            and mean_volume is not None
            and mean_volume > SILENCE_FLOOR_DB
        ),
    }


def _parse_volume(output: str, label: str) -> float | None:
    marker = f"{label}:"
    for line in output.splitlines():
        if marker not in line:
            continue
        value = line.split(marker, 1)[1].strip().split(" ", 1)[0]
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _archive_final_film(run: dict[str, Any]) -> dict[str, Any]:
    final_film = _artifact(run, "final_film")
    compose = final_film.get("final_compose_artifact")
    compose = compose if isinstance(compose, dict) else {}
    source = Path(str(compose.get("path") or ""))
    if not source.is_file():
        raise RuntimeError(f"final film artifact is missing: {source}")
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    destination = ARTIFACT_DIR / f"final-film-{run.get('id')}.mp4"
    shutil.copy2(source, destination)
    return {
        "source_path": str(source),
        "archived_path": str(destination),
        "declared_sha256": str(compose.get("sha256") or ""),
        "bytes": destination.stat().st_size,
        "width": int(compose.get("width") or 0),
        "height": int(compose.get("height") or 0),
        "duration_seconds": float(compose.get("duration_seconds") or 0),
    }


def _delivery_qc(run: dict[str, Any]) -> dict[str, Any]:
    qc = _artifact(run, "final_film").get("delivery_qc")
    qc = qc if isinstance(qc, dict) else {}
    checks = qc.get("checks")
    audio_check = None
    for item in checks or []:
        if isinstance(item, dict) and item.get("name") == "audio_activity":
            audio_check = item
            break
    return {
        "passed": qc.get("passed"),
        "failed_checks": list(qc.get("failed_checks") or []),
        "not_run_checks": list(qc.get("not_run_checks") or []),
        "audio_activity": audio_check,
    }


def run_smoke(*, allow_paid_generation: bool) -> dict[str, Any]:
    if not allow_paid_generation:
        return {
            "schema": "t152_scene_audio_8784_smoke.v1",
            "ok": False,
            "error": "paid_generation_not_authorized",
            "hint": "re-run with --allow-paid-generation for one image and one video",
        }

    project_id = ""
    cleanup: dict[str, Any] = {}
    checks: dict[str, bool] = {}
    evidence: dict[str, Any] = {
        "schema": "t152_scene_audio_8784_smoke.v1",
        "baseUrl": "http://127.0.0.1:8784",
        "startedAt": _stamp(),
    }
    try:
        config = _http_json(
            "GET",
            f"{PRODUCTION_API_BASE}/model-gateway/config",
            timeout=30.0,
        )
        bindings = _bindings(config)
        evidence["bindings"] = bindings
        project_id = _create_project(PREFIX)
        _seed_canvas(project_id, CANVAS_ID)
        try:
            run_id = _start_run(project_id, bindings)
        except RuntimeError as exc:
            if "workflow_auto_requires_agent_authorization" not in str(exc):
                raise
            # Auto paid runs may only be started by a real agent turn.  Keep
            # the fence visible instead of reporting it as a harness crash.
            evidence["error"] = "workflow_auto_requires_agent_authorization"
            evidence["hint"] = (
                "auto 付费 Run 只能由真实 Agent 回合发起；"
                "本脚本的替代路径是 t113_single_shot_paid_l3.py --production-8784"
            )
            evidence["ok"] = False
            return evidence
        evidence["projectId"] = project_id
        evidence["canvasId"] = CANVAS_ID
        evidence["runId"] = run_id
        print(f"[{_stamp()[9:15]}] run {run_id} started", flush=True)

        run = _poll_run(project_id, run_id, timeout_seconds=2400.0)
        evidence["runStatus"] = run.get("status")
        evidence["runErrorCode"] = run.get("error_code")
        evidence["runError"] = str(run.get("error") or "")[:800]

        shot_videos = _artifact(run, "shot_videos")
        jobs = shot_videos.get("jobs") or []
        first_job = jobs[0] if jobs and isinstance(jobs[0], dict) else {}
        audio_contract = first_job.get("audio_contract")
        audio_contract = audio_contract if isinstance(audio_contract, dict) else {}
        evidence["shotVideos"] = {
            "status": shot_videos.get("status"),
            "errorCode": shot_videos.get("error_code"),
            "jobCount": len(jobs),
            "audioContract": audio_contract,
        }

        film = _archive_final_film(run)
        evidence["finalFilm"] = film
        measured = _measure_audio(Path(film["archived_path"]))
        evidence["finalFilmAudio"] = measured
        evidence["deliveryQc"] = _delivery_qc(run)

        checks = {
            "run_terminal": str(run.get("status") or "") in TERMINAL_STATUSES,
            "shot_video_completed": shot_videos.get("status") == "completed",
            "shot_audio_contract_native": (
                audio_contract.get("reason") == "native_scene_audio"
                and audio_contract.get("generate_audio") is True
                and audio_contract.get("native_audio_strategy") == "native"
            ),
            "final_film_present": int(film.get("bytes") or 0) > 0,
            "final_film_has_audio_stream": measured.get("has_audio") is True,
            "final_film_audible": measured.get("audible") is True,
            "delivery_qc_audio_activity_passed": bool(
                (evidence["deliveryQc"].get("audio_activity") or {}).get("passed")
            ),
        }
        evidence["paidTaskStarts"] = {
            "image": 1,
            "video": 1,
        }
    finally:
        if project_id:
            cleanup = _cleanup_project(project_id, CANVAS_ID)
    evidence["finishedAt"] = _stamp()
    evidence["cleanup"] = cleanup
    checks["cleanup_no_remaining_project"] = cleanup.get("remaining") is False
    evidence["checks"] = checks
    evidence["ok"] = bool(checks) and all(checks.values())
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--allow-paid-generation", action="store_true")
    args = parser.parse_args(argv)
    report = run_smoke(allow_paid_generation=args.allow_paid_generation)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
