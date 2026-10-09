"""Bounded, credential-free stage receipts projected from WorkflowRun artifacts."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any

from novelvideo.verification.agent_artifacts import (
    normalize_agent_artifact_ref,
    project_agent_artifact_ref,
)
from novelvideo.workflow_runtime.release_readiness import (
    project_workflow_run_release_readiness,
)


STAGE_RECEIPT_SCHEMA = "workflow_stage_receipt.v1"
STAGE_ARTIFACT_KIND = "workflow_stage_receipt"
_SHA256_CHARS = frozenset("0123456789abcdef")
_MAX_STAGE_RECEIPTS = 32
_RECEIPT_FACT_KEYS = {
    "rows": "r",
    "blocking_count": "b",
    "issue_count": "i",
    "rows_fingerprint": "rf",
    "shot_count": "sc",
    "completed_count": "cc",
    "image_count": "ic",
    "source_signature": "ss",
    "generated_reference_count": "gr",
    "reference_count": "rc",
    "resolved_asset_ledger_signature": "ls",
    "video_count": "vc",
    "first_frame_verified_count": "fv",
    "first_frame_min_ssim": "fs",
    "final_file_count": "fc",
    "final_file_sha256": "fh",
    "width": "w",
    "height": "h",
    "duration_seconds": "d",
    "release_readiness": "rr",
    "release_reason": "rx",
    "delivery_qc_failed_count": "df",
    "delivery_qc_not_run_count": "dn",
    "delivery_qc_missing_count": "dm",
    "release_failed_checks": "rfc",
    "release_not_run_checks": "rnc",
}
_RECEIPT_FACT_KEYS_REVERSE = {
    compact: full for full, compact in _RECEIPT_FACT_KEYS.items()
}


def _text(value: object, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _sha256(value: object) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(char not in _SHA256_CHARS for char in text):
        return ""
    return text


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _workflow_run_record(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    for candidate in (
        payload.get("data"),
        payload.get("workflow_run"),
        payload.get("run"),
        payload,
    ):
        if not isinstance(candidate, Mapping):
            continue
        run_id = _text(
            candidate.get("run_id")
            or candidate.get("workflow_run_id")
            or candidate.get("id"),
        )
        state = _text(candidate.get("status") or candidate.get("state"), 40)
        if run_id or state:
            return candidate
    return {}


def _find_workflow_run(value: object) -> Mapping[str, Any]:
    queue: list[object] = [value]
    processed = 0
    while queue and processed < 512:
        current = queue.pop(0)
        processed += 1
        if isinstance(current, Mapping):
            run = _workflow_run_record(current)
            if isinstance(run.get("artifacts"), Mapping):
                return run
            for key, child in list(current.items())[:64]:
                if key in {"prompt", "inputs", "payload", "logs", "error", "url"}:
                    continue
                queue.append(child)
        elif isinstance(current, (list, tuple)):
            queue.extend(current[:64])
    return {}


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _compact_receipt_result(receipt: Mapping[str, Any]) -> str:
    facts = receipt.get("facts")
    compact_facts = {
        _RECEIPT_FACT_KEYS[key]: value
        for key, value in (facts.items() if isinstance(facts, Mapping) else ())
        if key in _RECEIPT_FACT_KEYS
    }
    return json.dumps(
        {
            "s": receipt.get("schema"),
            "i": receipt.get("step_id"),
            "k": receipt.get("artifact_kind"),
            "g": receipt.get("result_signature"),
            "h": receipt.get("receipt_sha256"),
            "f": compact_facts,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _receipt_from_result(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    if parsed.get("schema") == STAGE_RECEIPT_SCHEMA:
        return parsed
    if parsed.get("s") != STAGE_RECEIPT_SCHEMA:
        return {}
    compact_facts = (
        parsed.get("f") if isinstance(parsed.get("f"), Mapping) else {}
    )
    return {
        "schema": STAGE_RECEIPT_SCHEMA,
        "step_id": _text(parsed.get("i")),
        "artifact_kind": _text(parsed.get("k")),
        "result_signature": _sha256(parsed.get("g")),
        "receipt_sha256": _sha256(parsed.get("h")),
        "facts": {
            _RECEIPT_FACT_KEYS_REVERSE[key]: fact
            for key, fact in compact_facts.items()
            if key in _RECEIPT_FACT_KEYS_REVERSE
        },
    }


def _script_receipt(
    *,
    run_id: str,
    step_id: str,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    rows = artifact.get("rows") if isinstance(artifact.get("rows"), list) else []
    report = (
        artifact.get("contract_report")
        if isinstance(artifact.get("contract_report"), Mapping)
        else {}
    )
    blocking_count = _integer(report.get("blocking_count"))
    signature = _sha256(artifact.get("result_signature"))
    if not rows or blocking_count != 0 or not signature:
        return {}
    return {
        "schema": STAGE_RECEIPT_SCHEMA,
        "run_id": run_id,
        "step_id": step_id,
        "artifact_schema": "workflow_freezone_script_artifact.v1",
        "artifact_kind": "freezone_script_contract",
        "status": "verified",
        "task_id": _text(artifact.get("task_id")),
        "job_id": _text(artifact.get("job_id")),
        "result_signature": signature,
        "facts": {
            "rows": min(len(rows), 1_000),
            "blocking_count": blocking_count,
            "issue_count": _integer(report.get("issue_count")) or 0,
            "rows_fingerprint": _sha256(report.get("rows_fingerprint")),
        },
    }


def _storyboard_receipt(
    *,
    run_id: str,
    step_id: str,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    shots = _integer(artifact.get("shot_count")) or 0
    completed = _integer(artifact.get("completed_count")) or 0
    images = artifact.get("images") if isinstance(artifact.get("images"), list) else []
    signature = _sha256(artifact.get("result_signature"))
    source_script = (
        artifact.get("source_script")
        if isinstance(artifact.get("source_script"), Mapping)
        else {}
    )
    asset_references = (
        artifact.get("asset_references")
        if isinstance(artifact.get("asset_references"), Mapping)
        else {}
    )
    if shots <= 0 or completed != shots or len(images) != shots or not signature:
        return {}
    reference_count = sum(
        len(image.get("asset_reference_ids") or [])
        for image in images
        if isinstance(image, Mapping)
    )
    return {
        "schema": STAGE_RECEIPT_SCHEMA,
        "run_id": run_id,
        "step_id": step_id,
        "artifact_schema": "workflow_storyboard_images_artifact.v1",
        "artifact_kind": "freezone_storyboard_images",
        "status": "verified",
        "task_id": _text(artifact.get("task_id")),
        "job_id": _text(artifact.get("job_id")),
        "result_signature": signature,
        "facts": {
            "shot_count": min(shots, 1_000),
            "completed_count": min(completed, 1_000),
            "image_count": min(len(images), 1_000),
            "source_signature": _sha256(source_script.get("result_signature")),
            "generated_reference_count": min(
                _integer(asset_references.get("generated_count")) or 0,
                1_000,
            ),
            "reference_count": min(reference_count, 10_000),
            "resolved_asset_ledger_signature": _sha256(
                asset_references.get("resolved_asset_ledger_signature")
            ),
        },
    }


def _shot_video_receipt(
    *,
    run_id: str,
    step_id: str,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    shots = _integer(artifact.get("shot_count")) or 0
    completed = _integer(artifact.get("completed_count")) or 0
    videos = artifact.get("videos") if isinstance(artifact.get("videos"), list) else []
    signature = _sha256(artifact.get("result_signature"))
    source_storyboard = (
        artifact.get("source_storyboard")
        if isinstance(artifact.get("source_storyboard"), Mapping)
        else {}
    )
    if shots <= 0 or completed != shots or len(videos) != shots or not signature:
        return {}
    first_frame_scores: list[float] = []
    for video in videos:
        similarity = (
            video.get("first_frame_similarity")
            if isinstance(video, Mapping)
            else None
        )
        if not isinstance(similarity, Mapping):
            continue
        if _text(similarity.get("status")) != "passed":
            return {}
        try:
            score = float(similarity.get("ssim"))
            threshold = float(similarity.get("threshold"))
        except (TypeError, ValueError):
            return {}
        if score < threshold:
            return {}
        first_frame_scores.append(score)
    facts = {
        "shot_count": min(shots, 1_000),
        "completed_count": min(completed, 1_000),
        "video_count": min(len(videos), 1_000),
        "source_signature": _sha256(
            source_storyboard.get("result_signature"),
        ),
    }
    if len(first_frame_scores) == len(videos):
        facts.update(
            {
                "first_frame_verified_count": len(first_frame_scores),
                "first_frame_min_ssim": round(min(first_frame_scores), 6),
            }
        )
    return {
        "schema": STAGE_RECEIPT_SCHEMA,
        "run_id": run_id,
        "step_id": step_id,
        "artifact_schema": "workflow_shot_videos_artifact.v1",
        "artifact_kind": "freezone_shot_videos",
        "status": "verified",
        "task_id": _text(artifact.get("task_id")),
        "job_id": _text(artifact.get("job_id")),
        "result_signature": signature,
        "facts": facts,
    }


def project_workflow_release_readiness(value: object) -> dict[str, Any]:
    """Return the release gate carried by a workflow's final-film artifact."""

    payload = value if isinstance(value, Mapping) else {}
    run = _workflow_run_record(payload)
    if not run:
        run = payload
    if not isinstance(run.get("artifacts"), Mapping):
        run = _find_workflow_run(payload)
    return project_workflow_run_release_readiness(run)


def _final_film_receipt(
    *,
    run_id: str,
    step_id: str,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    shots = _integer(artifact.get("shot_count")) or 0
    completed = _integer(artifact.get("completed_count")) or 0
    final_file = (
        artifact.get("final_compose_artifact")
        if isinstance(artifact.get("final_compose_artifact"), Mapping)
        else {}
    )
    source_shot_videos = (
        artifact.get("source_shot_videos")
        if isinstance(artifact.get("source_shot_videos"), Mapping)
        else {}
    )
    signature = _sha256(artifact.get("result_signature"))
    file_sha256 = _sha256(final_file.get("sha256"))
    width = _integer(final_file.get("width")) or 0
    height = _integer(final_file.get("height")) or 0
    duration = final_file.get("duration_seconds")
    try:
        duration_seconds = round(max(float(duration), 0.0), 3)
    except (TypeError, ValueError):
        duration_seconds = 0.0
    if (
        shots <= 0
        or completed != shots
        or not signature
        or not file_sha256
        or width <= 0
        or height <= 0
        or duration_seconds <= 0
    ):
        return {}
    release = project_workflow_release_readiness(
        {"artifacts": {"final_film": artifact}}
    )
    release_status = _text(release.get("status"), 40) or "unverified"
    receipt_status = (
        "verified"
        if release_status == "ready"
        else "blocked"
        if release_status == "blocked"
        else "unverified"
    )
    failed_checks = (
        release.get("failed_checks")
        if isinstance(release.get("failed_checks"), list)
        else []
    )
    not_run_checks = (
        release.get("not_run_checks")
        if isinstance(release.get("not_run_checks"), list)
        else []
    )
    missing_checks = (
        release.get("missing_checks")
        if isinstance(release.get("missing_checks"), list)
        else []
    )
    return {
        "schema": STAGE_RECEIPT_SCHEMA,
        "run_id": run_id,
        "step_id": step_id,
        "artifact_schema": "workflow_final_film_artifact.v1",
        "artifact_kind": "freezone_final_film",
        "status": receipt_status,
        "task_id": _text(final_file.get("task_id") or artifact.get("task_id")),
        "job_id": _text(artifact.get("job_id")),
        "result_signature": signature,
        "facts": {
            "shot_count": min(shots, 1_000),
            "completed_count": min(completed, 1_000),
            "final_file_count": 1,
            "final_file_sha256": file_sha256,
            "width": min(width, 100_000),
            "height": min(height, 100_000),
            "duration_seconds": duration_seconds,
            "source_signature": _sha256(
                source_shot_videos.get("result_signature"),
            ),
            "release_readiness": release_status,
            "release_reason": _text(release.get("reason_code"), 80),
            "delivery_qc_failed_count": min(len(failed_checks), 64),
            "delivery_qc_not_run_count": min(len(not_run_checks), 64),
            "delivery_qc_missing_count": min(len(missing_checks), 64),
            "release_failed_checks": [
                _text(item, 80) for item in failed_checks[:16] if _text(item, 80)
            ],
            "release_not_run_checks": [
                _text(item, 80) for item in not_run_checks[:16] if _text(item, 80)
            ],
        },
    }


def project_workflow_stage_receipts(value: object) -> list[dict[str, Any]]:
    """Return stable receipts for completed, evidence-backed workflow stages."""

    payload = value if isinstance(value, Mapping) else {}
    run = _workflow_run_record(payload)
    if not isinstance(run.get("artifacts"), Mapping):
        run = _find_workflow_run(payload)
    if not run:
        return _receipts_from_agent_artifacts(payload)
    run_id = _text(
        run.get("run_id") or run.get("workflow_run_id") or run.get("id"),
    )
    artifacts = run.get("artifacts")
    if not run_id or not isinstance(artifacts, Mapping):
        return _receipts_from_agent_artifacts(payload)
    receipts: list[dict[str, Any]] = []
    for raw_step_id, raw_artifact in list(artifacts.items())[:_MAX_STAGE_RECEIPTS]:
        if not isinstance(raw_artifact, Mapping):
            continue
        if raw_artifact.get("status") != "completed":
            continue
        step_id = _text(raw_artifact.get("workflow_step_id") or raw_step_id)
        schema = _text(raw_artifact.get("schema"), 120)
        if schema == "workflow_freezone_script_artifact.v1":
            receipt = _script_receipt(
                run_id=run_id,
                step_id=step_id,
                artifact=raw_artifact,
            )
        elif schema == "workflow_storyboard_images_artifact.v1":
            receipt = _storyboard_receipt(
                run_id=run_id,
                step_id=step_id,
                artifact=raw_artifact,
            )
        elif schema == "workflow_shot_videos_artifact.v1":
            receipt = _shot_video_receipt(
                run_id=run_id,
                step_id=step_id,
                artifact=raw_artifact,
            )
        elif schema == "workflow_final_film_artifact.v1":
            receipt = _final_film_receipt(
                run_id=run_id,
                step_id=step_id,
                artifact=raw_artifact,
            )
        else:
            receipt = {}
        if receipt:
            receipt["receipt_sha256"] = _digest(receipt)
            receipts.append(receipt)
    return _require_parent_receipts(receipts)


def _require_parent_receipts(
    receipts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop a child receipt when its declared parent evidence is absent or stale."""

    signatures = {
        _text(receipt.get("artifact_kind")): _sha256(
            receipt.get("result_signature"),
        )
        for receipt in receipts
    }
    accepted: list[dict[str, Any]] = []
    for receipt in receipts:
        kind = _text(receipt.get("artifact_kind"))
        facts = (
            receipt.get("facts")
            if isinstance(receipt.get("facts"), Mapping)
            else {}
        )
        parent_kind = ""
        if kind == "freezone_storyboard_images":
            parent_kind = "freezone_script_contract"
        elif kind == "freezone_shot_videos":
            parent_kind = "freezone_storyboard_images"
        elif kind == "freezone_final_film":
            parent_kind = "freezone_shot_videos"
        if parent_kind:
            source_signature = _sha256(facts.get("source_signature"))
            if not source_signature or signatures.get(parent_kind) != source_signature:
                continue
        accepted.append(receipt)
    return accepted


def workflow_stage_receipt_artifacts(
    value: object,
    *,
    source_refs: object = (),
    producer_agent_id: str = "",
    consumer_agent_id: str = "",
) -> list[dict[str, Any]]:
    """Project receipts into the existing Agent artifact handoff contract."""

    refs = list(source_refs) if isinstance(source_refs, (list, tuple)) else []
    artifacts: list[dict[str, Any]] = []
    for receipt in project_workflow_stage_receipts(value)[:_MAX_STAGE_RECEIPTS]:
        digest = _sha256(receipt.get("receipt_sha256"))
        if not digest:
            continue
        receipt_status = _text(receipt.get("status")) or "verified"
        artifact_status = {
            "verified": "verified",
            "blocked": "rejected",
            "unverified": "materialized",
        }.get(receipt_status, "materialized")
        run_id = _text(receipt.get("run_id"))
        step_id = _text(receipt.get("step_id"))
        task_id = _text(receipt.get("task_id"))
        stage_refs = list(refs)
        run_ref = {"kind": "workflow_run", "id": run_id}
        if run_id and run_ref not in stage_refs:
            stage_refs.append(run_ref)
        task_ref = {"kind": "task", "id": task_id}
        if task_id and task_ref not in stage_refs:
            stage_refs.append(task_ref)
        projected = project_agent_artifact_ref(
            normalize_agent_artifact_ref(
                {
                    "artifact_id": f"artifact:workflow-stage:{digest[:32]}",
                    "artifact_sha256": digest,
                    "kind": STAGE_ARTIFACT_KIND,
                    "status": artifact_status,
                    "source_refs": stage_refs,
                    "run_id": run_id,
                    "step_id": step_id,
                    "task_id": task_id,
                    "verification": {
                        "schema": STAGE_RECEIPT_SCHEMA,
                        "status": receipt_status,
                        "result": _compact_receipt_result(receipt),
                        "evidence_ref": f"workflow://{run_id}/{step_id}",
                    },
                },
                producer_agent_id=producer_agent_id,
                consumer_agent_id=consumer_agent_id,
            )
        )
        if projected:
            artifacts.append(projected)
    return artifacts


def _receipts_from_agent_artifacts(value: object) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    seen: set[str] = set()
    queue: list[object] = [value]
    processed = 0
    while queue and processed < 512:
        current = queue.pop(0)
        processed += 1
        if isinstance(current, Mapping):
            candidates = current.get("agent_artifacts") or current.get("agent_artifact")
            if isinstance(candidates, Mapping):
                candidates = [candidates]
            if isinstance(candidates, (list, tuple)):
                for candidate in candidates[:_MAX_STAGE_RECEIPTS]:
                    projected = project_agent_artifact_ref(candidate)
                    if projected.get("kind") != STAGE_ARTIFACT_KIND:
                        continue
                    identity = _text(projected.get("artifact_id"))
                    if not identity or identity in seen:
                        continue
                    verification = projected.get("verification")
                    result = (
                        verification.get("result")
                        if isinstance(verification, Mapping)
                        else ""
                    )
                    receipt = _receipt_from_result(result)
                    if not receipt:
                        continue
                    receipt.setdefault(
                        "run_id",
                        _text(projected.get("run_id")),
                    )
                    receipt.setdefault(
                        "task_id",
                        _text(projected.get("task_id")),
                    )
                    receipt.setdefault("job_id", "")
                    receipt.setdefault(
                        "status",
                        _text(projected.get("status")) or "verified",
                    )
                    seen.add(identity)
                    receipts.append(receipt)
            for key, child in list(current.items())[:64]:
                if key in {"prompt", "inputs", "payload", "logs", "error", "url"}:
                    continue
                queue.append(child)
        elif isinstance(current, (list, tuple)):
            queue.extend(current[:_MAX_STAGE_RECEIPTS])
    return _require_parent_receipts(receipts[:_MAX_STAGE_RECEIPTS])


__all__ = [
    "STAGE_ARTIFACT_KIND",
    "STAGE_RECEIPT_SCHEMA",
    "project_workflow_release_readiness",
    "project_workflow_stage_receipts",
    "workflow_stage_receipt_artifacts",
]
