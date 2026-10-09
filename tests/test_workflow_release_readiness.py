from __future__ import annotations

from novelvideo.production.delivery_qc_contract import (
    DELIVERY_QC_RELEASE_CHECKS,
)
from novelvideo.workflow_runtime.release_readiness import (
    attach_workflow_run_release_readiness,
    project_workflow_run_release_readiness,
)


def _delivery_qc(
    *,
    failed: set[str] | None = None,
    not_run: set[str] | None = None,
    artifact_hash: str | None = "a" * 64,
) -> dict:
    failed = failed or set()
    not_run = not_run or set()
    checks = {
        name: {
            "name": name,
            "status": (
                "failed"
                if name in failed
                else "not_run"
                if name in not_run
                else "passed"
            ),
            "passed": False if name in failed else None if name in not_run else True,
            "evidence": (
                None
                if name in not_run
                else artifact_hash
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
        "failed_checks": [
            name for name in DELIVERY_QC_RELEASE_CHECKS if name in failed
        ],
        "not_run_checks": [
            name for name in DELIVERY_QC_RELEASE_CHECKS if name in not_run
        ],
        "passed": passed,
        "gate_observations": {"final_delivery_qc_passed": passed},
    }


def _run(*, qc: dict | None = None, final_status: str = "completed") -> dict:
    return {
        "id": "wfr_release_surface",
        "status": "completed",
        "revision": 9,
        "event_seq": 14,
        "artifacts": {
            "final_film": {
                "schema": "workflow_final_film_artifact.v1",
                "status": final_status,
                "final_compose_artifact": {"sha256": "a" * 64},
                "delivery_qc": qc,
            }
        },
    }


def test_missing_final_film_is_not_applicable() -> None:
    readiness = project_workflow_run_release_readiness(
        {"status": "completed", "artifacts": {}}
    )

    assert readiness["status"] == "not_applicable"
    assert readiness["required"] is False
    assert readiness["can_publish"] is False


def test_release_projection_fails_closed_for_failed_not_run_and_missing_qc() -> None:
    blocked = project_workflow_run_release_readiness(
        _run(qc=_delivery_qc(failed={"freeze_frames"}))
    )
    unverified = project_workflow_run_release_readiness(
        _run(qc=_delivery_qc(not_run={"loudness"}))
    )
    missing = project_workflow_run_release_readiness(_run(qc=None))

    assert blocked["status"] == "blocked"
    assert blocked["failed_checks"] == ["freeze_frames"]
    assert blocked["can_publish"] is False
    assert unverified["status"] == "unverified"
    assert unverified["not_run_checks"] == ["loudness"]
    assert unverified["can_publish"] is False
    assert missing["status"] == "unverified"
    assert missing["reason_code"] == "delivery_qc_missing"
    assert missing["can_publish"] is False


def test_only_complete_passing_qc_can_publish() -> None:
    ready_run = _run(qc=_delivery_qc())
    readiness = project_workflow_run_release_readiness(ready_run)

    assert readiness["status"] == "ready"
    assert readiness["can_publish"] is True

    attached = attach_workflow_run_release_readiness(ready_run)
    assert attached["release_readiness"] == readiness
    assert "release_readiness" not in ready_run
    assert attached["revision"] == ready_run["revision"]
    assert attached["event_seq"] == ready_run["event_seq"]


def test_release_requires_qc_hash_to_match_final_compose_artifact() -> None:
    mismatched = _run(qc=_delivery_qc(artifact_hash="b" * 64))
    readiness = project_workflow_run_release_readiness(mismatched)

    assert readiness["status"] == "blocked"
    assert readiness["reason_code"] == "delivery_artifact_hash_mismatch"
    assert readiness["can_publish"] is False


def test_release_stays_unverified_without_final_artifact_or_qc_hash() -> None:
    missing_artifact = _run(qc=_delivery_qc())
    del missing_artifact["artifacts"]["final_film"]["final_compose_artifact"]
    missing_hash = _run(qc=_delivery_qc(artifact_hash=None))

    assert (
        project_workflow_run_release_readiness(missing_artifact)["reason_code"]
        == "final_compose_artifact_missing"
    )
    unverified = project_workflow_run_release_readiness(missing_hash)
    assert unverified["reason_code"] == "delivery_artifact_hash_missing"
    assert unverified["can_publish"] is False
