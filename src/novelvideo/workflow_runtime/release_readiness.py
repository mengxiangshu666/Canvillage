"""Canonical release-readiness projection for durable workflow runs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from novelvideo.services.production_contracts import project_release_readiness


def project_workflow_run_release_readiness(value: object) -> dict[str, Any]:
    """Project publication readiness from one WorkflowRun snapshot."""

    run = value if isinstance(value, Mapping) else {}
    artifacts = (
        run.get("artifacts") if isinstance(run.get("artifacts"), Mapping) else {}
    )
    final_film = (
        artifacts.get("final_film")
        if isinstance(artifacts.get("final_film"), Mapping)
        else {}
    )
    if not final_film:
        return {
            "schema": "release_readiness_contract.v1",
            "status": "not_applicable",
            "reason_code": "final_film_absent",
            "can_publish": False,
            "required": False,
            "failed_checks": [],
            "not_run_checks": [],
            "missing_checks": [],
        }
    if final_film.get("status") != "completed":
        return {
            "schema": "release_readiness_contract.v1",
            "status": "unverified",
            "reason_code": "final_film_not_completed",
            "can_publish": False,
            "required": True,
            "failed_checks": [],
            "not_run_checks": [],
            "missing_checks": [],
        }
    final_compose = final_film.get("final_compose_artifact")
    if not isinstance(final_compose, Mapping):
        return {
            "schema": "release_readiness_contract.v1",
            "status": "unverified",
            "reason_code": "final_compose_artifact_missing",
            "can_publish": False,
            "required": True,
            "failed_checks": [],
            "not_run_checks": [],
            "missing_checks": ["final_compose_artifact"],
        }
    readiness = project_release_readiness(final_film.get("delivery_qc"))
    qc = final_film.get("delivery_qc")
    checks = qc.get("checks") if isinstance(qc, Mapping) else None
    hash_check = checks.get("sha256") if isinstance(checks, Mapping) else None
    qc_hash = hash_check.get("evidence") if isinstance(hash_check, Mapping) else None
    if isinstance(qc_hash, Mapping):
        qc_hash = qc_hash.get("sha256") or qc_hash.get("value")
    artifact_hash = final_compose.get("sha256")
    if readiness.get("can_publish") and (
        not _valid_sha256(artifact_hash) or not _valid_sha256(qc_hash)
    ):
        readiness = {
            **readiness,
            "status": "unverified",
            "reason_code": "delivery_artifact_hash_missing",
            "can_publish": False,
            "missing_checks": list(
                dict.fromkeys([*readiness.get("missing_checks", []), "artifact_sha256_match"])
            ),
        }
    elif readiness.get("can_publish") and (
        artifact_hash.strip().lower() != qc_hash.strip().lower()
    ):
        readiness = {
            **readiness,
            "status": "blocked",
            "reason_code": "delivery_artifact_hash_mismatch",
            "can_publish": False,
            "failed_checks": list(
                dict.fromkeys([*readiness.get("failed_checks", []), "artifact_sha256_match"])
            ),
        }
    readiness["required"] = True
    return readiness


def _valid_sha256(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    return len(normalized) == 64 and all(
        character in "0123456789abcdef" for character in normalized
    )


def attach_workflow_run_release_readiness(value: object) -> dict[str, Any]:
    """Return a response copy with the derived release gate attached."""

    run = dict(value) if isinstance(value, Mapping) else {}
    run["release_readiness"] = project_workflow_run_release_readiness(run)
    return run


__all__ = [
    "attach_workflow_run_release_readiness",
    "project_workflow_run_release_readiness",
]
