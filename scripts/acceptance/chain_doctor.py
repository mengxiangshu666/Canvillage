"""Report where the one-script-to-final-film chain stops.

Chain Doctor is deliberately an instrument, not another pipeline.  The stub
profile drives the existing isolated T-075 chain through pytest and inspects
the artifacts it leaves behind.  The real profile reads the T-106 target plan,
then checks the native Village Agent runtime and the exact T-113 paid L3
preflight. Paid provider execution remains fail-closed until a separately
authorized real run exists.

Exit codes:

* 0: every stage is green
* 2: at least one stage is red
* 3: no red stage, but at least one yellow stage
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
FFPROBE = ROOT / "runtime" / "ffmpeg" / "ffprobe.exe"
FFMPEG = ROOT / "runtime" / "ffmpeg" / "ffmpeg.exe"
T075_TEST = ROOT / "tests" / "test_freezone_full_film_local_e2e.py"
T080_TEST = (
    "tests/test_workflow_freezone_storyboard_images.py::"
    "test_workflow_final_film_is_durable_and_recovers"
)
T080_EVIDENCE_SCHEMAS = frozenset(
    {
        "t083_isolated_final_film_evidence.v1",
        "t105_run_level_production_authorization_evidence.v1",
    }
)
T113_PAID_L3_PREFLIGHT = (
    ROOT
    / "scripts"
    / "acceptance"
    / "t113_single_shot_paid_l3.py"
)
ARTIFACT_ROOT = ROOT / "workspace" / "artifacts" / "chain-doctor"
T112_STABLE_MANIFEST = (
    ROOT
    / "workspace"
    / "artifacts"
    / "t112-real-execution-adapter"
    / "full-chain"
    / "manifest.json"
)

STAGE_ORDER = (
    "script_contract",
    "asset_ledger",
    "storyboard",
    "shot_video",
    "final_film",
    "craft",
)
STATUSES = {"green", "yellow", "red"}
DELIVERY_QC_REQUIRED_CHECKS = (
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


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _python_executable() -> Path:
    return VENV_PYTHON if VENV_PYTHON.is_file() else Path(sys.executable)


def _stage(
    stage: str,
    status: str,
    claim: str,
    *,
    evidence: Iterable[str] = (),
    gap: str = "",
    structural: bool = False,
) -> dict[str, Any]:
    if stage not in STAGE_ORDER:
        raise ValueError(f"unknown stage: {stage}")
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status}")
    if status == "green" and gap:
        raise ValueError("green stages cannot carry a gap")
    if status != "green" and not gap:
        raise ValueError("yellow and red stages must explain their gap")
    return {
        "stage": stage,
        "status": status,
        "claim": claim,
        "evidence": [str(item) for item in evidence],
        "gap": gap,
        "structural": bool(structural),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value)
    )


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _valid_delivery_qc_receipt(
    value: Any,
    *,
    final_sha256: str,
    final_width: int,
    final_height: int,
) -> bool:
    if not isinstance(value, dict) or value.get("schema") != "delivery_qc_contract.v1":
        return False
    checks = value.get("checks")
    if not isinstance(checks, dict):
        return False
    normalized: dict[str, dict[str, Any]] = {}
    for name in DELIVERY_QC_REQUIRED_CHECKS:
        check = checks.get(name)
        if (
            not isinstance(check, dict)
            or check.get("name") != name
            or check.get("status") not in {"passed", "failed", "not_run"}
            or "evidence" not in check
            or (check.get("evidence") is None and check["status"] != "not_run")
        ):
            return False
        normalized[name] = check
    failed = [
        name for name, check in normalized.items() if check["status"] == "failed"
    ]
    not_run = [
        name for name, check in normalized.items() if check["status"] == "not_run"
    ]
    expected_passed = False if failed else None if not_run else True
    if (
        value.get("passed") is not expected_passed
        or value.get("failed_checks") != failed
        or value.get("not_run_checks") != not_run
    ):
        return False
    sha_evidence = normalized["sha256"]["evidence"]
    if isinstance(sha_evidence, dict):
        sha_evidence = sha_evidence.get("sha256") or sha_evidence.get("value")
    if str(sha_evidence or "") != final_sha256:
        return False
    dimensions = normalized["dimensions"]["evidence"]
    if not isinstance(dimensions, dict):
        return False
    return (
        dimensions.get("width") == final_width
        and dimensions.get("height") == final_height
    )


def _stable_asset_path(path_value: Any, sha256_value: Any) -> Path | None:
    path = Path(str(path_value or ""))
    sha256 = str(sha256_value or "")
    if (
        not path.is_file()
        or not path.stat().st_size
        or not _is_sha256(sha256)
        or _sha256(path) != sha256
    ):
        return None
    return path


def _load_t112_full_chain_evidence(
    manifest_path: Path = T112_STABLE_MANIFEST,
) -> dict[str, Any] | None:
    """Load hash-matched T-112 full-chain evidence without trusting stale files."""

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != "t112_stable_evidence_manifest.v1"
        or manifest.get("mode") != "full-chain"
        or manifest.get("run_status") != "completed"
        or manifest.get("paidProvidersConnected") is not False
        or manifest.get("providerCallsStarted") is not False
    ):
        return None
    evidence_path = Path(str(manifest.get("evidence_path") or ""))
    expected_sha256 = str(manifest.get("evidence_sha256") or "")
    screenshot_path = Path(str(manifest.get("screenshot_path") or ""))
    screenshot_sha256 = str(manifest.get("screenshot_sha256") or "")
    if (
        not evidence_path.is_file()
        or not _is_sha256(expected_sha256)
        or _sha256(evidence_path) != expected_sha256
        or not screenshot_path.is_file()
        or not _is_sha256(screenshot_sha256)
        or _sha256(screenshot_path) != screenshot_sha256
    ):
        return None
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(evidence, dict)
        or evidence.get("schema") != "t112_real_execution_adapter_full_chain.v1"
        or evidence.get("paidProvidersConnected") is not False
        or evidence.get("providerCallsStarted") is not False
    ):
        return None
    run = evidence.get("run_after")
    run = run if isinstance(run, dict) else {}
    if (
        str(run.get("id") or "") != str(manifest.get("run_id") or "")
        or run.get("status") != "completed"
    ):
        return None
    mode_evidence = evidence.get("mode_evidence")
    mode_evidence = mode_evidence if isinstance(mode_evidence, dict) else {}
    if any(
        not isinstance(mode_evidence.get(stage_name), dict)
        or mode_evidence[stage_name].get("status") != "completed"
        for stage_name in ("storyboard_images", "shot_videos", "final_film")
    ):
        return None
    manifest_shot_count = manifest.get("shot_count")
    manifest_duration = manifest.get("video_duration_seconds")
    expected_total_duration = manifest.get("expected_total_duration_seconds")
    if (
        not isinstance(manifest_shot_count, int)
        or isinstance(manifest_shot_count, bool)
        or not 1 <= manifest_shot_count <= 120
        or not isinstance(manifest_duration, int)
        or isinstance(manifest_duration, bool)
        or not 1 <= manifest_duration <= 30
        or expected_total_duration != manifest_shot_count * manifest_duration
    ):
        return None
    storyboard = mode_evidence.get("storyboard_images")
    shot_videos = mode_evidence.get("shot_videos")
    final_film = mode_evidence.get("final_film")
    final_artifact = (
        final_film.get("final_compose_artifact")
        if isinstance(final_film, dict)
        and isinstance(final_film.get("final_compose_artifact"), dict)
        else {}
    )
    final_artifact_path = Path(str(final_artifact.get("path") or ""))
    final_artifact_sha256 = str(final_artifact.get("sha256") or "")
    final_artifact_width = final_artifact.get("width")
    final_artifact_height = final_artifact.get("height")
    if (
        storyboard.get("shot_count") != manifest_shot_count
        or storyboard.get("completed_count") != manifest_shot_count
        or shot_videos.get("shot_count") != manifest_shot_count
        or shot_videos.get("completed_count") != manifest_shot_count
        or not final_artifact_path.is_file()
        or not _is_sha256(final_artifact_sha256)
        or _sha256(final_artifact_path) != final_artifact_sha256
        or not isinstance(final_artifact_width, int)
        or isinstance(final_artifact_width, bool)
        or final_artifact_width <= 0
        or not isinstance(final_artifact_height, int)
        or isinstance(final_artifact_height, bool)
        or final_artifact_height <= 0
        or not _is_finite_number(final_artifact.get("duration_seconds"))
        or abs(
            float(final_artifact.get("duration_seconds") or 0)
            - float(expected_total_duration)
        )
        > 0.5
    ):
        return None
    delivery_qc = final_film.get("delivery_qc")
    if not _valid_delivery_qc_receipt(
        delivery_qc,
        final_sha256=final_artifact_sha256,
        final_width=final_artifact_width,
        final_height=final_artifact_height,
    ):
        return None
    checks = shot_videos.get("first_frame_checks")
    checks = checks if isinstance(checks, list) else []
    valid_checks = [
        check
        for check in checks
        if isinstance(check, dict)
        and check.get("status") == "passed"
        and check.get("match") is True
        and _is_finite_number(check.get("threshold"))
        and float(check["threshold"]) == 0.72
        and _is_finite_number(check.get("ssim"))
        and float(check["ssim"]) >= 0.72
        and _is_sha256(str(check.get("source_image_sha256") or ""))
        and _stable_asset_path(
            check.get("source_image_path"),
            check.get("source_image_sha256"),
        )
        is not None
        and _is_sha256(str(check.get("video_sha256") or ""))
        and _stable_asset_path(
            check.get("video_path"),
            check.get("video_sha256"),
        )
        is not None
        and isinstance(check.get("video_width"), int)
        and not isinstance(check.get("video_width"), bool)
        and check["video_width"] > 0
        and isinstance(check.get("video_height"), int)
        and not isinstance(check.get("video_height"), bool)
        and check["video_height"] > 0
    ]
    if (
        not checks
        or len(checks) != manifest_shot_count
        or len(valid_checks) != len(checks)
    ):
        return None
    source_image_hashes = {
        str(check["source_image_sha256"]) for check in valid_checks
    }
    if (
        manifest_shot_count > 1
        and len(source_image_hashes) != manifest_shot_count
    ):
        return None
    final_film = mode_evidence.get("final_film")
    final_film = final_film if isinstance(final_film, dict) else {}
    order_checks = final_film.get("final_order_checks")
    order_checks = order_checks if isinstance(order_checks, list) else []
    valid_order_checks = [
        check
        for check in order_checks
        if isinstance(check, dict)
        and check.get("status") == "passed"
        and check.get("match") is True
        and str(check.get("shot_id") or "")
        and _is_sha256(str(check.get("source_image_sha256") or ""))
        and _stable_asset_path(
            check.get("source_image_path"),
            check.get("source_image_sha256"),
        )
        is not None
        and _is_sha256(str(check.get("final_frame_sha256") or ""))
        and _stable_asset_path(
            check.get("final_frame_path"),
            check.get("final_frame_sha256"),
        )
        is not None
        and _is_finite_number(check.get("threshold"))
        and float(check["threshold"]) == 0.72
        and _is_finite_number(check.get("similarity"))
        and float(check["similarity"]) >= 0.72
    ]
    if (
        len(order_checks) != manifest_shot_count
        or len(valid_order_checks) != len(order_checks)
        or final_film.get("final_order_verified") is not True
    ):
        return None
    shot_ids = [str(check.get("shot_id") or "") for check in valid_checks]
    order_shot_ids = [
        str(check.get("shot_id") or "") for check in valid_order_checks
    ]
    source_hashes = [
        str(check.get("source_image_sha256") or "") for check in valid_checks
    ]
    source_paths = [
        str(check.get("source_image_path") or "") for check in valid_checks
    ]
    order_source_hashes = [
        str(check.get("source_image_sha256") or "")
        for check in valid_order_checks
    ]
    order_source_paths = [
        str(check.get("source_image_path") or "")
        for check in valid_order_checks
    ]
    if (
        len(set(shot_ids)) != manifest_shot_count
        or order_shot_ids != shot_ids
        or order_source_hashes != source_hashes
        or order_source_paths != source_paths
        or any(
            not _is_finite_number(check.get("sample_seconds"))
            or float(check["sample_seconds"]) < 0
            or float(check["sample_seconds"]) >= expected_total_duration
            for check in valid_order_checks
        )
    ):
        return None
    return {
        "manifest_path": str(manifest_path),
        "evidence_path": str(evidence_path),
        "evidence_sha256": expected_sha256,
        "run_id": str(run.get("id") or ""),
        "shot_count": manifest_shot_count,
        "video_duration_seconds": manifest_duration,
        "expected_total_duration_seconds": expected_total_duration,
        "first_frame_checks": valid_checks,
        "first_frame_min_ssim": min(
            float(check["ssim"]) for check in valid_checks
        ),
        "final_order_checks": valid_order_checks,
        "final_order_min_similarity": min(
            float(check["similarity"]) for check in valid_order_checks
        ),
        "final_video_path": str(final_artifact_path),
        "final_video_sha256": final_artifact_sha256,
        "final_video_width": final_artifact_width,
        "final_video_height": final_artifact_height,
        "final_video_duration_seconds": float(
            final_artifact["duration_seconds"]
        ),
        "delivery_qc": delivery_qc,
        "delivery_qc_passed": delivery_qc["passed"],
        "delivery_qc_failed_checks": list(delivery_qc["failed_checks"]),
        "delivery_qc_not_run_checks": list(delivery_qc["not_run_checks"]),
    }


def _is_mp4(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            header = handle.read(12)
    except OSError:
        return False
    return len(header) >= 8 and header[4:8] == b"ftyp"


def _probe_video(path: Path, timeout: float = 30.0) -> dict[str, Any]:
    probe = FFPROBE if FFPROBE.is_file() else Path("ffprobe")
    try:
        proc = subprocess.run(
            [
                str(probe),
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip()[:500]}
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        return {"ok": False, "error": f"ffprobe returned non-JSON: {exc}"}
    streams = payload.get("streams")
    if not isinstance(streams, list):
        return {"ok": False, "error": "ffprobe returned no streams"}
    video = next(
        (
            item
            for item in streams
            if isinstance(item, dict) and item.get("codec_type") == "video"
        ),
        None,
    )
    if not isinstance(video, dict):
        return {"ok": False, "error": "ffprobe returned no video stream"}
    container = payload.get("format")
    duration = None
    if isinstance(container, dict):
        duration = container.get("duration")
    try:
        duration_seconds = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_seconds = None
    return {
        "ok": True,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "codec_name": str(video.get("codec_name") or ""),
        "duration_seconds": duration_seconds,
    }


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as image:
            return image.size
    except OSError:
        return None


def _run_logged(
    command: list[str],
    *,
    log_path: Path,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"$ {' '.join(command)}\n"
        f"exit_code={proc.returncode}\n\n"
        f"--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}\n",
        encoding="utf-8",
    )
    return proc


def _parse_json_object_output(output: str) -> dict[str, Any] | None:
    text = output or ""
    try:
        candidate = json.loads(text)
        return candidate if isinstance(candidate, dict) else None
    except ValueError:
        pass

    decoder = json.JSONDecoder()
    for index in range(len(text)):
        if text[index] != "{":
            continue
        try:
            candidate, _end = decoder.raw_decode(text[index:])
        except ValueError:
            continue
        if isinstance(candidate, dict) and "schema" in candidate:
            return candidate
    return None


def _find_artifacts(basetemp: Path) -> dict[str, list[Path]]:
    if not basetemp.is_dir():
        return {"storyboard": [], "shot_video": [], "final_film": []}
    return {
        "storyboard": sorted(basetemp.rglob("freezone_gen/*.png")),
        "shot_video": sorted(basetemp.rglob("freezone_video_gen/*.mp4")),
        "final_film": sorted(basetemp.rglob("freezone_video_compose/*.mp4")),
    }


def _first_file(paths: list[Path]) -> Path | None:
    return paths[0] if paths else None


def classify_stub(
    *,
    returncode: int,
    basetemp: Path,
    log_path: Path,
    t080_returncode: int,
    t080_receipt: dict[str, Any] | None,
    t080_log_path: Path,
    t112_full_chain_evidence: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Classify the isolated T-075 + T-080 chains without stale receipts."""

    if returncode != 0:
        claim = f"pytest exit {returncode}"
        gap = f"isolated chain failed; see {log_path}"
        return [
            _stage(stage, "red", claim, evidence=[str(log_path)], gap=gap)
            for stage in STAGE_ORDER
        ]

    artifacts = _find_artifacts(basetemp)
    stages: list[dict[str, Any]] = []
    stages.append(
        _stage(
            "script_contract",
            "yellow",
            f"isolated pytest passed: {T075_TEST.name}",
            evidence=[str(log_path)],
            gap="text provider is a local deterministic stub; no real fingerprint",
        )
    )
    ledger = t080_receipt.get("asset_ledger") if isinstance(t080_receipt, dict) else None
    ledger = ledger if isinstance(ledger, dict) else {}
    reference_counts = [
        int(value)
        for value in (ledger.get("storyboard_reference_counts") or [])
        if str(value).isdigit()
    ]
    if t080_returncode != 0:
        stages.append(
            _stage(
                "asset_ledger",
                "red",
                f"T-080 workflow chain failed: pytest exit {t080_returncode}",
                evidence=[str(t080_log_path)],
                gap="the workflow ledger chain did not complete",
            )
        )
    elif (
        str(ledger.get("signature") or "")
        and int(ledger.get("asset_count") or 0) > 0
        and int(ledger.get("ready_count") or 0) > 0
        and reference_counts
        and all(count > 0 for count in reference_counts)
    ):
        stages.append(
            _stage(
                "asset_ledger",
                "yellow",
                f"workflow ledger signature={ledger['signature']}",
                evidence=[
                    str(t080_log_path),
                    f"asset_count={ledger['asset_count']}",
                    f"ready_count={ledger['ready_count']}",
                    f"roles={json.dumps(ledger.get('roles') or [], sort_keys=True)}",
                    f"storyboard_reference_counts={reference_counts}",
                ],
                gap=(
                    "local deterministic provider; "
                    f"only {ledger['ready_count']}/{ledger['asset_count']} assets ready; "
                    f"roles={json.dumps(ledger.get('roles') or [], sort_keys=True)}"
                ),
            )
        )
    else:
        stages.append(
            _stage(
                "asset_ledger",
                "red",
                "T-080 receipt has no ready referenced asset ledger",
                evidence=[str(t080_log_path), f"receipt={json.dumps(ledger, sort_keys=True)}"],
                gap="ledger signature, ready assets and per-shot references are required",
            )
        )

    image = _first_file(artifacts["storyboard"])
    if image is None:
        stages.append(
            _stage(
                "storyboard",
                "red",
                "no isolated storyboard artifact",
                evidence=[str(basetemp)],
                gap="freezone_gen produced no PNG under the pytest basetemp",
            )
        )
    else:
        dimensions = _png_dimensions(image)
        stages.append(
            _stage(
                "storyboard",
                "yellow",
                f"PNG sha256={_sha256(image)}",
                evidence=[
                    str(image),
                    f"bytes={image.stat().st_size}",
                    f"dimensions={dimensions}",
                ],
                gap=(
                    "local deterministic image provider; asset references are "
                    f"verified by T-080 receipt: {reference_counts}"
                    if reference_counts
                    else "local deterministic image provider; no asset reference"
                ),
            )
        )

    video = _first_file(artifacts["shot_video"])
    if video is None:
        stages.append(
            _stage(
                "shot_video",
                "red",
                "no isolated shot video artifact",
                evidence=[str(basetemp)],
                gap="freezone_video_gen produced no MP4 under the pytest basetemp",
            )
        )
    else:
        probe = _probe_video(video)
        stages.append(
            _stage(
                "shot_video",
                "yellow",
                f"MP4 sha256={_sha256(video)}",
                evidence=[
                    str(video),
                    f"bytes={video.stat().st_size}",
                    f"probe={json.dumps(probe, sort_keys=True)}",
                ],
                gap="local deterministic video provider; first frame not verified",
            )
        )
    stable = (
        t112_full_chain_evidence
        if isinstance(t112_full_chain_evidence, dict)
        else {}
    )
    if stages[-1]["stage"] == "shot_video" and stable:
        checks = stable.get("first_frame_checks")
        checks = checks if isinstance(checks, list) else []
        if checks:
            stages[-1] = _stage(
                "shot_video",
                "yellow",
                (
                    "T-112 full-chain first-frame QC passed: "
                    f"run={stable.get('run_id')} "
                    f"min_ssim={float(stable.get('first_frame_min_ssim') or 0):.6f}"
                ),
                evidence=[
                    str(stable.get("manifest_path") or ""),
                    str(stable.get("evidence_path") or ""),
                    f"evidence_sha256={stable.get('evidence_sha256')}",
                    f"verified_shots={len(checks)}",
                    f"video_duration_seconds={stable.get('video_duration_seconds')}",
                    (
                        "expected_total_duration_seconds="
                        f"{stable.get('expected_total_duration_seconds')}"
                    ),
                    (
                        "verified_final_order_shots="
                        f"{len(stable.get('final_order_checks') or [])}"
                    ),
                    (
                        "final_order_min_similarity="
                        f"{float(stable.get('final_order_min_similarity') or 0):.6f}"
                    ),
                    "first_frame_threshold=0.72",
                ],
                gap=(
                    "local deterministic provider; first-frame gate passed, "
                    "but motion, performance, sync and pacing are not reviewed"
                ),
            )

    film = _first_file(artifacts["final_film"])
    if film is None:
        stages.append(
            _stage(
                "final_film",
                "red",
                "no composed film artifact",
                evidence=[str(basetemp)],
                gap="freezone_video_compose produced no MP4 under the pytest basetemp",
            )
        )
    else:
        probe = _probe_video(film)
        stages.append(
            _stage(
                "final_film",
                "yellow",
                f"FFmpeg MP4 sha256={_sha256(film)}",
                evidence=[
                    str(film),
                    f"bytes={film.stat().st_size}",
                    f"ftyp={_is_mp4(film)}",
                    f"probe={json.dumps(probe, sort_keys=True)}",
                ],
                gap="stub media inputs; seam and loudness are not reviewed",
            )
        )
    if stages[-1]["stage"] == "final_film" and stable:
        order_checks = stable.get("final_order_checks")
        order_checks = order_checks if isinstance(order_checks, list) else []
        final_video_path = Path(str(stable.get("final_video_path") or ""))
        if order_checks and final_video_path.is_file():
            stages[-1] = _stage(
                "final_film",
                "yellow",
                (
                    "T-112 full-chain final-film order and engineering QC executed: "
                    f"run={stable.get('run_id')} "
                    "min_similarity="
                    f"{float(stable.get('final_order_min_similarity') or 0):.6f}"
                ),
                evidence=[
                    str(stable.get("manifest_path") or ""),
                    str(stable.get("evidence_path") or ""),
                    f"evidence_sha256={stable.get('evidence_sha256')}",
                    f"final_video={final_video_path}",
                    f"final_video_sha256={stable.get('final_video_sha256')}",
                    (
                        "dimensions="
                        f"{stable.get('final_video_width')}x"
                        f"{stable.get('final_video_height')}"
                    ),
                    (
                        "duration_seconds="
                        f"{stable.get('final_video_duration_seconds')}"
                    ),
                    f"verified_final_order_shots={len(order_checks)}",
                    (
                        "expected_total_duration_seconds="
                        f"{stable.get('expected_total_duration_seconds')}"
                    ),
                    "final_order_threshold=0.72",
                    (
                        "engineering_qc_passed="
                        f"{stable.get('delivery_qc_passed')}"
                    ),
                    (
                        "engineering_qc_failed="
                        f"{json.dumps(stable.get('delivery_qc_failed_checks') or [])}"
                    ),
                    (
                        "engineering_qc_not_run="
                        f"{json.dumps(stable.get('delivery_qc_not_run_checks') or [])}"
                    ),
                ],
                gap=(
                    "local deterministic provider; final order mapping passed and "
                    "engineering QC ran, but release blockers are "
                    f"{json.dumps(stable.get('delivery_qc_failed_checks') or [])}; "
                    "unmeasured checks are "
                    f"{json.dumps(stable.get('delivery_qc_not_run_checks') or [])}; "
                    "craft is not reviewed"
                ),
            )

    stages.append(
        _stage(
            "craft",
            "yellow",
            "no human craft review attached to the isolated run",
            evidence=[str(log_path)],
            gap="continuity, performance, sync and pacing were not reviewed",
        )
    )
    return stages


def _real_unauthorized(
    *,
    paid_l3_preflight: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    stages: list[dict[str, Any]] = []
    paid_l3 = (
        paid_l3_preflight
        if isinstance(paid_l3_preflight, dict)
        else {}
    )
    paid_l3_report = (
        paid_l3.get("plan") if isinstance(paid_l3.get("plan"), dict) else {}
    )
    paid_l3_plan = (
        paid_l3_report.get("plan")
        if isinstance(paid_l3_report.get("plan"), dict)
        else {}
    )
    paid_l3_reached = (
        paid_l3_report.get("schema") == "t113_single_shot_paid_l3_preflight.v1"
    )
    paid_l3_passed = (
        paid_l3.get("returncode") == 0
        and paid_l3_report.get("schema") == "t113_single_shot_paid_l3_preflight.v1"
        and paid_l3_report.get("environmentReady") is True
        and paid_l3_report.get("executionRunnerImplemented") is True
        and paid_l3_report.get("executionRunnerConnected") is True
        and paid_l3_report.get("executionRunnerReady") is True
        and paid_l3_report.get("failedChecks") == []
        and paid_l3_report.get("providerCallsStarted") is False
    )
    for stage in STAGE_ORDER:
        if stage == "asset_ledger":
            if paid_l3_passed:
                stages.append(
                    _stage(
                        stage,
                        "yellow",
                        "T-113 fixed paid L3 environment and exact models are ready",
                        evidence=[
                            str(paid_l3.get("logPath") or ""),
                            "target_chain=freezone-final-film",
                            "orchestration=workflow_run",
                            "agent=village",
                            f"t113_workflow_id={paid_l3_plan.get('workflowId') or 'freezone-final-film'}",
                            f"t113_text_model={paid_l3_plan.get('textModel') or 'deepseek-flash'}",
                            f"t113_image_model={paid_l3_plan.get('imageModel') or 'gpt-image-2.5-sunburst'}",
                            f"t113_video_model={paid_l3_plan.get('videoModel') or 'MiniMax-H3'}",
                            f"provider_calls_started={paid_l3_report.get('providerCallsStarted')}",
                            f"blocking_reasons={json.dumps(paid_l3_report.get('blockingReasons') or [], sort_keys=True)}",
                        ],
                        gap=(
                            "8784 and the exact T-113 models are ready, but the "
                            "execution runner is waiting for the exact paid "
                            "authorization phrase; no project, Run, or provider "
                            "call has been created"
                        ),
                    )
                )
            elif paid_l3_reached:
                stages.append(
                    _stage(
                        stage,
                        "red",
                        "T-113 target-chain runtime preflight is not ready",
                        evidence=[
                            str(paid_l3.get("logPath") or ""),
                            f"paid_l3_exit={paid_l3.get('returncode')}",
                            "paid_l3_failed_checks="
                            f"{json.dumps(paid_l3_report.get('failedChecks') or [], sort_keys=True)}",
                        ],
                        gap=(
                            "the target chain is structurally runnable, but the "
                            "production runtime or exact paid L3 models are not "
                            "ready; paid generation also remains unauthorized"
                        ),
                        structural=False,
                    )
                )
            else:
                gap = (
                    "the T-113 target-chain preflight could not run; "
                    "paid generation also remains unauthorized"
                )
                stages.append(
                    _stage(
                        stage,
                        "red",
                        "real-chain preflight could not run",
                        evidence=[
                            str(paid_l3.get("logPath") or ""),
                            "paid_l3_failed_checks="
                            f"{json.dumps(paid_l3_report.get('failedChecks') or [], sort_keys=True)}",
                        ],
                        gap=gap,
                        structural=True,
                    )
                )
        else:
            gap = (
                "the native target chain and paid L3 preflight are isolated from "
                "this doctor run; real paid provider execution still requires "
                "bounded authorization"
                if paid_l3_passed
                else "make the native target chain and T-113 paid L3 runtime "
                "preflight ready; one-time paid approval alone is not enough"
            )
            stages.append(
                _stage(
                        stage,
                        "red",
                        "paid generation is not authorized",
                        gap=gap,
                    )
                )
    return stages


def _artifact_check(path_value: Any, expected_sha: Any) -> tuple[Path | None, str]:
    if not path_value:
        return None, "receipt did not include an artifact path"
    path = Path(str(path_value))
    if not path.is_file():
        return None, f"artifact is missing: {path}"
    if expected_sha:
        actual = _sha256(path)
        if str(expected_sha).lower() != actual.lower():
            return None, f"artifact sha256 mismatch: {path}"
    return path, ""


def classify_real(
    *,
    receipt: dict[str, Any] | None,
    log_path: Path,
    authorized: bool,
    paid_l3_preflight: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not authorized:
        return _real_unauthorized(
            paid_l3_preflight=paid_l3_preflight,
        )
    if receipt is None:
        gap = f"real smoke produced no receipt; see {log_path}"
        return [
            _stage(stage, "red", "real smoke failed", evidence=[str(log_path)], gap=gap)
            for stage in STAGE_ORDER
        ]
    if receipt.get("ok") is not True:
        error = str(receipt.get("error") or receipt.get("purgeError") or "unknown error")
        return [
            _stage(
                stage,
                "red",
                f"real smoke failed: {error[:160]}",
                evidence=[str(log_path)],
                gap="the real chain did not complete",
            )
            for stage in STAGE_ORDER
        ]

    story_job = receipt.get("storyJob")
    story_job_id = ""
    if isinstance(story_job, dict):
        story_job_id = str(story_job.get("jobId") or "")
    row_count = int(receipt.get("rowCount") or 0)
    stages = [
        _stage(
            "script_contract",
            "yellow",
            f"real text job {story_job_id}" if story_job_id else "real text job completed",
            evidence=[str(log_path), f"rowCount={row_count}"],
            gap="receipt has no recomputed row fingerprint; green requires a recheck",
        )
    ]

    ledger = receipt.get("assetLedger")
    ledger = ledger if isinstance(ledger, dict) else {}
    reference_counts = [
        int(value)
        for value in (ledger.get("shotReferenceCounts") or [])
        if str(value).isdigit()
    ]
    reference_images = [
        item
        for item in (ledger.get("referenceImages") or [])
        if isinstance(item, dict)
    ]
    verified_reference_images = 0
    reference_image_evidence: list[str] = []
    for image in reference_images:
        path, error = _artifact_check(image.get("path"), image.get("sha256"))
        if path is None:
            reference_image_evidence.append(error)
            continue
        verified_reference_images += 1
        reference_image_evidence.append(f"{path} sha256={_sha256(path)}")
    if (
        str(ledger.get("signature") or "")
        and int(ledger.get("assetCount") or 0) > 0
        and int(ledger.get("readyCount") or 0) > 0
        and verified_reference_images > 0
        and reference_counts
        and all(count > 0 for count in reference_counts)
    ):
        stages.append(
            _stage(
                "asset_ledger",
                "yellow",
                f"real ledger signature={ledger['signature']}",
                evidence=[
                    str(log_path),
                    f"asset_count={ledger['assetCount']}",
                    f"ready_count={ledger['readyCount']}",
                    f"roles={json.dumps(ledger.get('roles') or {}, sort_keys=True)}",
                    f"shot_reference_counts={reference_counts}",
                    *reference_image_evidence,
                ],
                gap=(
                    "real reference images were generated and attached; "
                    "identity fidelity still needs human craft review"
                ),
            )
        )
    else:
        stages.append(
            _stage(
                "asset_ledger",
                "red",
                "real smoke produced no referenced asset ledger",
                evidence=[
                    str(log_path),
                    f"ledger={json.dumps(ledger, ensure_ascii=False, sort_keys=True)[:1200]}",
                    *reference_image_evidence,
                ],
                gap=(
                    "a ledger signature, verified reference images and "
                    "per-shot references are required"
                ),
                structural=True,
            )
        )

    storyboards = receipt.get("shots")
    valid_storyboards = 0
    storyboard_evidence: list[str] = []
    if isinstance(storyboards, list):
        for shot in storyboards:
            if not isinstance(shot, dict):
                continue
            path, error = _artifact_check(
                shot.get("imagePath"),
                shot.get("imageSha256"),
            )
            if path is not None:
                valid_storyboards += 1
                storyboard_evidence.append(f"{path} sha256={_sha256(path)}")
            elif error:
                storyboard_evidence.append(error)
    if valid_storyboards:
        stages.append(
            _stage(
                "storyboard",
                "yellow",
                f"{valid_storyboards} real storyboard image(s) on disk",
                evidence=storyboard_evidence,
                gap="direct smoke is text-to-image; no asset reference was used",
            )
        )
    else:
        stages.append(
            _stage(
                "storyboard",
                "red",
                "no verified real storyboard image",
                evidence=storyboard_evidence or [str(log_path)],
                gap="real smoke produced no downloadable storyboard artifact",
            )
        )

    valid_videos = 0
    video_evidence: list[str] = []
    if isinstance(storyboards, list):
        for shot in storyboards:
            if not isinstance(shot, dict):
                continue
            video_path, error = _artifact_check(shot.get("mp4"), shot.get("videoSha256"))
            if video_path is None:
                if error:
                    video_evidence.append(error)
                continue
            if not _is_mp4(video_path):
                video_evidence.append(f"not an MP4: {video_path}")
                continue
            probe = _probe_video(video_path)
            if probe.get("ok") is not True:
                video_evidence.append(f"probe failed: {video_path}: {probe}")
                continue
            valid_videos += 1
            video_evidence.append(f"{video_path} sha256={_sha256(video_path)}")
    if valid_videos:
        stages.append(
            _stage(
                "shot_video",
                "yellow",
                f"{valid_videos} real shot video(s) on disk",
                evidence=video_evidence,
                gap="real MP4 verified, but first-frame equality was not rechecked",
            )
        )
    else:
        stages.append(
            _stage(
                "shot_video",
                "red",
                "no verified real shot video",
                evidence=video_evidence or [str(log_path)],
                gap="real smoke produced no playable shot MP4",
            )
        )

    film_path, film_error = _artifact_check(
        receipt.get("filmPath"),
        receipt.get("filmSha256"),
    )
    if film_path is None:
        stages.append(
            _stage(
                "final_film",
                "red",
                "no verified real final film",
                evidence=[film_error or str(log_path)],
                gap="real smoke produced no verified final MP4",
            )
        )
    else:
        probe = _probe_video(film_path)
        stages.append(
            _stage(
                "final_film",
                "yellow",
                f"FFmpeg MP4 sha256={_sha256(film_path)}",
                evidence=[
                    str(film_path),
                    f"bytes={film_path.stat().st_size}",
                    f"ftyp={_is_mp4(film_path)}",
                    f"probe={json.dumps(probe, sort_keys=True)}",
                ],
                gap="width and duration are probed; seam and loudness are not reviewed",
            )
        )

    stages.append(
        _stage(
            "craft",
            "yellow",
            "real artifacts exist but no human review is attached",
            evidence=[str(film_path) if film_path else str(log_path)],
            gap="continuity, performance, sync and pacing were not reviewed",
        )
    )
    return stages


def _summary(stages: list[dict[str, Any]]) -> dict[str, int]:
    return {
        status: sum(1 for item in stages if item["status"] == status)
        for status in ("green", "yellow", "red")
    }


def exit_code_for(stages: list[dict[str, Any]]) -> int:
    statuses = {str(item.get("status")) for item in stages}
    if "red" in statuses:
        return 2
    if "green" in statuses and len(statuses) == 1:
        return 0
    return 3


def _next_action(
    stages: list[dict[str, Any]],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    structural_red = next(
        (
            str(item.get("stage"))
            for item in stages
            if item.get("status") == "red" and item.get("structural")
        ),
        None,
    )
    if structural_red:
        return {
            "kind": "repair_structural_chain",
            "stage": structural_red,
            "requiresUserAuthorization": False,
        }

    paid_l3 = evidence.get("t113PaidL3PreflightEvidence")
    paid_l3 = paid_l3 if isinstance(paid_l3, dict) else {}
    authorization_required = paid_l3.get("authorizationRequired") is True
    authorization_phrase = str(paid_l3.get("authorizationPhrase") or "")
    if authorization_required and paid_l3.get("environmentReady") is not True:
        return {
            "kind": "start_runtime_and_request_paid_authorization",
            "stage": "asset_ledger",
            "requiresUserAuthorization": True,
            "failedChecks": list(paid_l3.get("failedChecks") or []),
            "authorizationPhrase": authorization_phrase,
        }
    if authorization_required:
        return {
            "kind": "request_paid_authorization",
            "stage": "asset_ledger",
            "requiresUserAuthorization": True,
            "failedChecks": [],
            "authorizationPhrase": authorization_phrase,
        }

    first_red = next(
        (
            str(item.get("stage"))
            for item in stages
            if item.get("status") == "red"
        ),
        None,
    )
    if first_red:
        return {
            "kind": "resolve_red_stage",
            "stage": first_red,
            "requiresUserAuthorization": False,
        }

    first_yellow = next(
        (
            str(item.get("stage"))
            for item in stages
            if item.get("status") == "yellow"
        ),
        None,
    )
    if first_yellow:
        return {
            "kind": "advance_stage",
            "stage": first_yellow,
            "requiresUserAuthorization": False,
        }
    return {
        "kind": "complete",
        "stage": None,
        "requiresUserAuthorization": False,
    }


def _write_report(
    *,
    profile: str,
    stages: list[dict[str, Any]],
    evidence: dict[str, Any],
) -> Path:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    next_structural_red = next(
        (
            str(item.get("stage"))
            for item in stages
            if item.get("status") == "red" and item.get("structural")
        ),
        None,
    )
    next_action = _next_action(stages, evidence)
    payload = {
        "schema": "chain_doctor.v1",
        "generatedAt": stamp,
        "profile": profile,
        "exitCode": exit_code_for(stages),
        "summary": _summary(stages),
        "nextStructuralRed": next_structural_red,
        "nextAction": next_action,
        "stages": stages,
        "evidence": evidence,
    }
    target = ARTIFACT_ROOT / f"{stamp}-{profile}.json"
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (ARTIFACT_ROOT / f"latest-{profile}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def _print_report(payload: dict[str, Any], report_path: Path) -> None:
    print(f"Chain Doctor: {payload['profile']}")
    for item in payload["stages"]:
        marker = str(item["status"]).upper()
        print(f"[{marker:6}] {item['stage']:16} {item['claim']}")
        if item.get("gap"):
            print(f"         gap: {item['gap']}")
    summary = payload["summary"]
    print(
        "Summary: "
        f"green={summary['green']} yellow={summary['yellow']} red={summary['red']} "
        f"exit={payload['exitCode']}"
    )
    if payload.get("nextStructuralRed"):
        print(f"Next structural red: {payload['nextStructuralRed']}")
    action = payload.get("nextAction")
    if isinstance(action, dict):
        stage = f" stage={action['stage']}" if action.get("stage") else ""
        print(f"Next action: {action.get('kind')}{stage}")
    print(f"JSON: {report_path}")


def _parse_t080_receipt(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except ValueError:
            continue
        if (
            isinstance(payload, dict)
            and payload.get("schema") in T080_EVIDENCE_SCHEMAS
        ):
            return payload
    return None


def _run_stub(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stamp = _stamp()
    run_dir = ARTIFACT_ROOT / f"{stamp}-stub"
    t075_basetemp = run_dir / "pytest-tmp-t075"
    t080_basetemp = run_dir / "pytest-tmp-t080"
    t075_log_path = run_dir / "pytest-t075.log"
    t080_log_path = run_dir / "pytest-t080.log"
    run_dir.mkdir(parents=True, exist_ok=True)
    for basetemp in (t075_basetemp, t080_basetemp):
        if basetemp.exists():
            shutil.rmtree(basetemp)
    t075_command = [
        str(_python_executable()),
        "-m",
        "pytest",
        str(T075_TEST),
        "-q",
        f"--basetemp={t075_basetemp}",
    ]
    t080_command = [
        str(_python_executable()),
        "-m",
        "pytest",
        T080_TEST,
        "-q",
        "-s",
        f"--basetemp={t080_basetemp}",
    ]
    started = time.monotonic()
    t075 = _run_logged(
        t075_command,
        log_path=t075_log_path,
        timeout=args.timeout_seconds,
    )
    t080 = _run_logged(
        t080_command,
        log_path=t080_log_path,
        timeout=args.timeout_seconds,
    )
    elapsed = round(time.monotonic() - started, 2)
    t080_receipt = _parse_t080_receipt(t080.stdout)
    t112_full_chain_evidence = _load_t112_full_chain_evidence()
    stages = classify_stub(
        returncode=t075.returncode,
        basetemp=t075_basetemp,
        log_path=t075_log_path,
        t080_returncode=t080.returncode,
        t080_receipt=t080_receipt,
        t080_log_path=t080_log_path,
        t112_full_chain_evidence=t112_full_chain_evidence,
    )
    evidence = {
        "runDir": str(run_dir),
        "t075Basetemp": str(t075_basetemp),
        "t075LogPath": str(t075_log_path),
        "t075ExitCode": t075.returncode,
        "t075Command": t075_command,
        "t080Basetemp": str(t080_basetemp),
        "t080LogPath": str(t080_log_path),
        "t080ExitCode": t080.returncode,
        "t080Command": t080_command,
        "t080Receipt": t080_receipt,
        "t112FullChainEvidence": t112_full_chain_evidence,
        "elapsedSeconds": elapsed,
    }
    return stages, evidence


def _run_real(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stamp = _stamp()
    run_dir = ARTIFACT_ROOT / f"{stamp}-real"
    log_path = run_dir / "real-smoke.log"
    run_dir.mkdir(parents=True, exist_ok=True)
    paid_l3_log_path = run_dir / "t113-paid-l3-preflight.log"
    paid_l3_command = [
        str(_python_executable()),
        str(T113_PAID_L3_PREFLIGHT),
        "--output",
        str(run_dir / "t113-paid-l3-preflight.json"),
    ]
    started = time.monotonic()
    paid_l3 = _run_logged(
        paid_l3_command,
        log_path=paid_l3_log_path,
        timeout=args.timeout_seconds,
    )
    elapsed = round(time.monotonic() - started, 2)
    paid_l3_plan = _parse_json_object_output(paid_l3.stdout)
    paid_l3_preflight = {
        "returncode": paid_l3.returncode,
        "logPath": str(paid_l3_log_path),
        "plan": paid_l3_plan,
    }
    evidence = {
        "runDir": str(run_dir),
        "logPath": str(log_path),
        "providerCallsStarted": False,
        "paidGenerationAuthorized": bool(args.allow_paid_generation),
        "t113PaidL3PreflightLogPath": str(paid_l3_log_path),
        "t113PaidL3PreflightExitCode": paid_l3.returncode,
        "t113PaidL3PreflightEvidence": paid_l3_plan,
        "elapsedSeconds": elapsed,
        "commands": [paid_l3_command],
    }
    if args.allow_paid_generation:
        environment_ready = (
            isinstance(paid_l3_plan, dict)
            and paid_l3_plan.get("schema")
            == "t113_single_shot_paid_l3_preflight.v1"
            and paid_l3_plan.get("environmentReady") is True
        )
        l3_ready = (
            isinstance(paid_l3_plan, dict)
            and paid_l3_plan.get("schema")
            == "t113_single_shot_paid_l3_preflight.v1"
            and paid_l3_plan.get("environmentReady") is True
            and paid_l3_plan.get("executionRunnerConnected") is True
        )
        stages = classify_real(
            receipt=None,
            log_path=log_path,
            authorized=False,
            paid_l3_preflight=paid_l3_preflight,
        )
        evidence.update(
            {
                "authorizedExecutionBlocked": True,
                "environmentReady": environment_ready,
                "blockingReason": (
                    "t113_paid_authorization_required"
                    if l3_ready
                    else "t113_paid_l3_runtime_preflight_not_ready"
                ),
            }
        )
        return stages, evidence
    stages = classify_real(
        receipt=None,
        log_path=log_path,
        authorized=False,
        paid_l3_preflight=paid_l3_preflight,
    )
    return stages, evidence


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("stub", "real"), required=True)
    parser.add_argument(
        "--allow-paid-generation",
        action="store_true",
        help="authorize real image and video provider calls (real profile only)",
    )
    parser.add_argument("--shots", type=int, default=1)
    parser.add_argument("--duration", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.shots < 1:
        raise SystemExit("--shots must be at least 1")
    if args.duration < 1:
        raise SystemExit("--duration must be at least 1")
    if args.profile == "stub":
        stages, evidence = _run_stub(args)
        report_path = _write_report(profile="stub", stages=stages, evidence=evidence)
    else:
        stages, evidence = _run_real(args)
        report_path = _write_report(profile="real", stages=stages, evidence=evidence)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    _print_report(payload, report_path)
    return int(payload["exitCode"])


if __name__ == "__main__":
    raise SystemExit(main())
