from __future__ import annotations

import json

from novelvideo.chat.agent_runtime import build_specialist_result
from novelvideo.chat.workflow_stage_receipts import (
    STAGE_ARTIFACT_KIND,
    project_workflow_stage_receipts,
    workflow_stage_receipt_artifacts,
)
from novelvideo.production.delivery_qc_contract import (
    DELIVERY_QC_RELEASE_CHECKS,
)


def _delivery_qc(
    *,
    failed: set[str] | None = None,
    not_run: set[str] | None = None,
) -> dict:
    failed = failed or set()
    not_run = not_run or set()
    checks = {
        name: {
            "name": name,
            "status": (
                "failed" if name in failed else "not_run" if name in not_run else "passed"
            ),
            "passed": False if name in failed else None if name in not_run else True,
            "evidence": (
                None
                if name in not_run
                else "2" * 64
                if name == "sha256"
                else {}
            ),
        }
        for name in DELIVERY_QC_RELEASE_CHECKS
    }
    passed = False if failed else None if not_run else True
    return {
        "schema": "delivery_qc_contract.v1",
        "target": {},
        "checks": checks,
        "failed_checks": [name for name in DELIVERY_QC_RELEASE_CHECKS if name in failed],
        "not_run_checks": [
            name for name in DELIVERY_QC_RELEASE_CHECKS if name in not_run
        ],
        "passed": passed,
        "gate_observations": {"final_delivery_qc_passed": passed},
    }


def _run(
    *,
    storyboard_signature: str = "b" * 64,
    storyboard_status: str = "completed",
    shot_video_signature: str = "e" * 64,
    shot_video_status: str = "completed",
    shot_video_source_signature: str = "",
) -> dict:
    return {
        "id": "wfr_stage_receipts",
        "project_id": "project-stage",
        "canvas_id": "canvas-stage",
        "workflow_id": "freezone-storyboard-images",
        "status": "completed",
        "runtime_phase": "terminal",
        "revision": 4,
        "last_verified_canvas_revision": 4,
        "artifacts": {
            "script_contract": {
                "schema": "workflow_freezone_script_artifact.v1",
                "kind": "freezone_script_contract",
                "status": "completed",
                "workflow_step_id": "script_contract",
                "task_id": "script-task",
                "job_id": "script-job",
                "rows": [
                    {
                        "shot_id": "shot-1",
                        "shot_prompt": "must not cross the receipt boundary",
                    }
                ],
                "contract_report": {
                    "blocking_count": 0,
                    "issue_count": 0,
                    "rows_fingerprint": "a" * 64,
                },
                "result_signature": "a" * 64,
                "output_path": "C:/private/script.json",
                "url": "https://provider.invalid/signed?token=secret",
            },
            "storyboard_images": {
                "schema": "workflow_storyboard_images_artifact.v1",
                "kind": "freezone_storyboard_images",
                "status": storyboard_status,
                "workflow_step_id": "storyboard_images",
                "shot_count": 1,
                "completed_count": 1 if storyboard_status == "completed" else 0,
                "source_script": {
                    "task_id": "script-task",
                    "job_id": "script-job",
                    "result_signature": "a" * 64,
                },
                "images": (
                    [
                        {
                            "output_path": "C:/private/shot.png",
                            "url": "https://provider.invalid/signed?token=secret",
                            "sha256": "c" * 64,
                        }
                    ]
                    if storyboard_status == "completed"
                    else []
                ),
                "result_signature": storyboard_signature,
            },
            "shot_videos": {
                "schema": "workflow_shot_videos_artifact.v1",
                "kind": "freezone_shot_videos",
                "status": shot_video_status,
                "workflow_step_id": "shot_videos",
                "shot_count": 1,
                "completed_count": 1 if shot_video_status == "completed" else 0,
                "source_storyboard": {
                    "result_signature": (
                        shot_video_source_signature or storyboard_signature
                    ),
                },
                "videos": (
                    [
                        {
                            "output_path": "C:/private/shot.mp4",
                            "url": "https://provider.invalid/signed?token=secret",
                            "sha256": "f" * 64,
                            "first_frame_similarity": {
                                "schema": "video_first_frame_similarity.v1",
                                "status": "passed",
                                "ssim": 0.987654,
                                "threshold": 0.72,
                            },
                        }
                    ]
                    if shot_video_status == "completed"
                    else []
                ),
                "result_signature": shot_video_signature,
            },
        },
    }


def _run_with_final_film(
    *,
    source_signature: str = "e" * 64,
    final_signature: str = "1" * 64,
    delivery_qc: dict | None = None,
) -> dict:
    run = _run()
    run["artifacts"]["final_film"] = {
        "schema": "workflow_final_film_artifact.v1",
        "kind": "freezone_final_film",
        "status": "completed",
        "workflow_step_id": "final_film",
        "shot_count": 1,
        "completed_count": 1,
        "source_shot_videos": {
            "result_signature": source_signature,
        },
        "delivery_qc": delivery_qc if delivery_qc is not None else _delivery_qc(),
        "result_signature": final_signature,
        "final_compose_artifact": {
            "schema": "workflow_final_compose_artifact.v1",
            "task_id": "compose-task",
            "output_path": "C:/private/final.mp4",
            "url": "https://provider.invalid/signed?token=secret",
            "sha256": "2" * 64,
            "width": 160,
            "height": 90,
            "duration_seconds": 4.0,
        },
    }
    return run


def test_stage_receipts_only_project_completed_evidence_backed_stages() -> None:
    receipts = project_workflow_stage_receipts(_run())

    assert [item["step_id"] for item in receipts] == [
        "script_contract",
        "storyboard_images",
        "shot_videos",
    ]
    assert receipts[0]["facts"] == {
        "rows": 1,
        "blocking_count": 0,
        "issue_count": 0,
        "rows_fingerprint": "a" * 64,
    }
    assert receipts[1]["facts"]["image_count"] == 1
    assert receipts[1]["facts"]["source_signature"] == "a" * 64
    assert receipts[2]["facts"]["video_count"] == 1
    assert receipts[2]["facts"]["source_signature"] == "b" * 64
    assert receipts[2]["facts"]["first_frame_verified_count"] == 1
    assert receipts[2]["facts"]["first_frame_min_ssim"] == 0.987654
    assert all(len(item["receipt_sha256"]) == 64 for item in receipts)
    assert "prompt" not in json.dumps(receipts)
    assert "private" not in json.dumps(receipts)
    assert "provider.invalid" not in json.dumps(receipts)

    assert project_workflow_stage_receipts(_run(storyboard_status="monitoring")) == [
        receipts[0]
    ]
    assert project_workflow_stage_receipts(_run(shot_video_status="monitoring")) == [
        receipts[0],
        receipts[1],
    ]
    assert project_workflow_stage_receipts(
        _run(shot_video_signature="c" * 64)
    )[2]["result_signature"] == "c" * 64
    assert project_workflow_stage_receipts(
        _run(shot_video_source_signature="d" * 64)
    ) == receipts[:2]

    failed_qc = _run()
    failed_qc["artifacts"]["shot_videos"]["videos"][0]["first_frame_similarity"][
        "status"
    ] = "failed"
    assert project_workflow_stage_receipts(failed_qc) == receipts[:2]


def test_final_film_receipt_requires_live_shot_video_parent() -> None:
    receipts = project_workflow_stage_receipts(_run_with_final_film())

    assert [item["step_id"] for item in receipts] == [
        "script_contract",
        "storyboard_images",
        "shot_videos",
        "final_film",
    ]
    final = receipts[-1]
    assert final["artifact_kind"] == "freezone_final_film"
    assert final["task_id"] == "compose-task"
    assert final["facts"] == {
        "shot_count": 1,
        "completed_count": 1,
        "final_file_count": 1,
        "final_file_sha256": "2" * 64,
        "width": 160,
        "height": 90,
        "duration_seconds": 4.0,
        "source_signature": "e" * 64,
        "release_readiness": "ready",
        "release_reason": "delivery_qc_passed",
        "delivery_qc_failed_count": 0,
        "delivery_qc_not_run_count": 0,
        "delivery_qc_missing_count": 0,
        "release_failed_checks": [],
        "release_not_run_checks": [],
    }
    assert final["status"] == "verified"
    assert "private" not in json.dumps(receipts)
    assert "provider.invalid" not in json.dumps(receipts)

    stale = project_workflow_stage_receipts(
        _run_with_final_film(source_signature="d" * 64)
    )
    assert [item["step_id"] for item in stale] == [
        "script_contract",
        "storyboard_images",
        "shot_videos",
    ]


def test_final_film_receipt_carries_release_gate_failures() -> None:
    blocked_run = _run_with_final_film(
        delivery_qc=_delivery_qc(failed={"freeze_frames", "audio_activity"})
    )
    blocked = project_workflow_stage_receipts(blocked_run)[-1]

    assert blocked["status"] == "blocked"
    assert blocked["facts"]["release_readiness"] == "blocked"
    assert blocked["facts"]["release_reason"] == "delivery_qc_failed"
    assert blocked["facts"]["delivery_qc_failed_count"] == 2
    assert blocked["facts"]["release_failed_checks"] == [
        "freeze_frames",
        "audio_activity",
    ]
    blocked_artifact = workflow_stage_receipt_artifacts(blocked_run)[-1]
    assert blocked_artifact["status"] == "rejected"
    assert blocked_artifact["verification"]["status"] == "blocked"

    unverified_run = _run_with_final_film(
        delivery_qc=_delivery_qc(not_run={"frame_rate", "loudness"})
    )
    unverified = project_workflow_stage_receipts(unverified_run)[-1]
    assert unverified["status"] == "unverified"
    assert unverified["facts"]["release_readiness"] == "unverified"
    assert unverified["facts"]["delivery_qc_not_run_count"] == 2
    assert unverified["facts"]["release_not_run_checks"] == [
        "frame_rate",
        "loudness",
    ]


def test_stage_receipt_artifacts_are_standard_and_change_with_signature() -> None:
    first = workflow_stage_receipt_artifacts(
        _run(),
        producer_agent_id="production_executor",
    )
    repeated = workflow_stage_receipt_artifacts(
        _run(),
        producer_agent_id="production_executor",
    )
    changed = workflow_stage_receipt_artifacts(
        _run(storyboard_signature="d" * 64),
        producer_agent_id="production_executor",
    )

    assert first == repeated
    assert [item["kind"] for item in first] == [
        STAGE_ARTIFACT_KIND,
        STAGE_ARTIFACT_KIND,
        STAGE_ARTIFACT_KIND,
    ]
    assert first[0]["artifact_id"].endswith(first[0]["artifact_sha256"][:32])
    assert first[2]["artifact_sha256"] != changed[2]["artifact_sha256"]
    assert all(
        len(item["verification"]["result"]) <= 500
        for item in first
    )
    assert "prompt" not in json.dumps(first)
    assert "private" not in json.dumps(first)
    assert "provider.invalid" not in json.dumps(first)

    artifact_only = project_workflow_stage_receipts({"agent_artifacts": first})
    assert [item["step_id"] for item in artifact_only] == [
        "script_contract",
        "storyboard_images",
        "shot_videos",
    ]
    assert artifact_only[0]["run_id"] == "wfr_stage_receipts"
    assert artifact_only[0]["task_id"] == "script-task"
    assert artifact_only[1]["facts"]["image_count"] == 1


def test_workflow_specialist_result_carries_stage_receipts() -> None:
    result = build_specialist_result(
        "workflow.run.get",
        _run(),
        arguments={"run_id": "wfr_stage_receipts"},
    )

    stage_artifacts = [
        item
        for item in result["agent_artifacts"]
        if item.get("kind") == STAGE_ARTIFACT_KIND
    ]
    assert result["status"] == "completed"
    assert [item["step_id"] for item in stage_artifacts] == [
        "script_contract",
        "storyboard_images",
        "shot_videos",
    ]
