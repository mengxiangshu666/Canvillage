from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.workflow_runtime.automatic_recovery import (
    WORKFLOW_AUTOMATIC_RECOVERY_SCHEMA,
    automatic_reconcile_marker_matches,
    build_automatic_reconcile_retry,
)
from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.workflow_runtime.failure_persistence import (
    persist_workflow_step_failure,
)
from novelvideo.workflow_runtime.store import WorkflowRunStore
from workflow_plan_support import complete_script_and_production_plan


def _retry_run(*, status: str = "failed") -> dict:
    return {
        "id": "wfr-auto-reconcile",
        "step_states": {
            "final_film": {
                "id": "final_film",
                "handler": "freezone.final_film",
                "status": "running",
                "attempt": 1,
                "max_attempts": 2,
                "retry_policy": "automatic",
                "execution_semantics": {
                    "side_effect": "local_mutation",
                    "retry_safety": "idempotency_key_required",
                    "execution_mode": "sequential",
                    "idempotency": "runtime_node",
                    "result_lookup": "idempotency_key",
                    "recovery_mode": "reconcile",
                    "max_automatic_attempts": 2,
                    "backoff_class": "none",
                    "failure_stage": "export",
                },
            }
        },
        "artifacts": {
            "final_film": {
                "kind": "freezone_final_film",
                "status": status,
                "task_id": "compose-task-1",
                "scope": "workflow:wfr-auto-reconcile:final-film:signature",
                "error_code": "workflow_step_failed",
            }
        },
    }


def test_automatic_reconcile_retry_requires_persisted_final_film_task() -> None:
    payload = build_automatic_reconcile_retry(
        _retry_run(),
        step_id="final_film",
        error_code="workflow_step_failed",
    )

    assert payload is not None
    marker = payload["automatic_recovery"]
    assert marker["schema"] == WORKFLOW_AUTOMATIC_RECOVERY_SCHEMA
    assert marker["mode"] == "reconcile_existing_task"
    assert marker["task_id"] == "compose-task-1"
    assert marker["source_attempt"] == 1
    assert marker["max_automatic_attempts"] == 2

    assert (
        build_automatic_reconcile_retry(
            _retry_run(),
            step_id="final_film",
            error_code="workflow_final_film_input_invalid",
        )
        is None
    )
    missing_task = _retry_run()
    missing_task["artifacts"]["final_film"].pop("task_id")
    assert (
        build_automatic_reconcile_retry(
            missing_task,
            step_id="final_film",
            error_code="workflow_step_failed",
        )
        is None
    )
    exhausted = _retry_run()
    exhausted["step_states"]["final_film"]["attempt"] = 2
    assert (
        build_automatic_reconcile_retry(
            exhausted,
            step_id="final_film",
            error_code="workflow_step_failed",
        )
        is None
    )
    run = _retry_run()
    marker = payload["automatic_recovery"]
    assert automatic_reconcile_marker_matches(
        run,
        step_id="final_film",
        step=run["step_states"]["final_film"],
        artifact=run["artifacts"]["final_film"],
        marker=marker,
        source="executor",
    )
    assert not automatic_reconcile_marker_matches(
        run,
        step_id="final_film",
        step=run["step_states"]["final_film"],
        artifact=run["artifacts"]["final_film"],
        marker={**marker, "task_id": "other-task"},
        source="executor",
    )
    assert not automatic_reconcile_marker_matches(
        run,
        step_id="final_film",
        step=run["step_states"]["final_film"],
        artifact=run["artifacts"]["final_film"],
        marker=marker,
        source="external",
    )


def _media_retry_run(
    *,
    step_id: str,
    handler: str,
    artifact_kind: str,
    error_code: str,
) -> dict:
    return {
        "id": f"wfr-{step_id}-auto-reconcile",
        "step_states": {
            step_id: {
                "id": step_id,
                "handler": handler,
                "status": "running",
                "attempt": 1,
                "max_attempts": 2,
                "retry_policy": "automatic",
                "execution_semantics": {
                    "side_effect": "paid_generation",
                    "retry_safety": "idempotency_key_required",
                    "execution_mode": "parallel_safe",
                    "idempotency": "runtime_node",
                    "result_lookup": "provider_receipt",
                    "recovery_mode": "reconcile",
                    "max_automatic_attempts": 2,
                    "backoff_class": "none",
                    "failure_stage": "media_generation",
                },
            }
        },
        "artifacts": {
            step_id: {
                "kind": artifact_kind,
                "status": "failed",
                "error_code": error_code,
                "jobs": [
                    {
                        "job_id": f"{step_id}-job-1",
                        "scope": f"{step_id}-scope-1",
                        "task_key": f"{step_id}-task-key-1",
                        "task_id": f"{step_id}-task-1",
                        "status": "running",
                    },
                    {
                        "job_id": f"{step_id}-job-2",
                        "scope": f"{step_id}-scope-2",
                        "task_key": f"{step_id}-task-key-2",
                        "task_id": f"{step_id}-task-2",
                        "status": "queued",
                    },
                ],
            }
        },
    }


@pytest.mark.parametrize(
    ("step_id", "handler", "artifact_kind", "error_code"),
    [
        (
            "storyboard_images",
            "freezone.storyboard_images",
            "freezone_storyboard_images",
            "workflow_storyboard_reconcile_failed",
        ),
        (
            "shot_videos",
            "freezone.shot_videos",
            "freezone_shot_videos",
            "workflow_shot_video_reconcile_failed",
        ),
    ],
)
def test_automatic_reconcile_retry_requires_closed_media_task_identity(
    step_id: str,
    handler: str,
    artifact_kind: str,
    error_code: str,
) -> None:
    run = _media_retry_run(
        step_id=step_id,
        handler=handler,
        artifact_kind=artifact_kind,
        error_code=error_code,
    )

    payload = build_automatic_reconcile_retry(
        run,
        step_id=step_id,
        error_code=error_code,
    )

    assert payload is not None
    marker = payload["automatic_recovery"]
    assert marker["task_identities"] == [
        {
            "job_id": f"{step_id}-job-1",
            "scope": f"{step_id}-scope-1",
            "task_key": f"{step_id}-task-key-1",
            "task_id": f"{step_id}-task-1",
        },
        {
            "job_id": f"{step_id}-job-2",
            "scope": f"{step_id}-scope-2",
            "task_key": f"{step_id}-task-key-2",
            "task_id": f"{step_id}-task-2",
        },
    ]
    assert len(marker["jobs_fingerprint"]) == 64
    assert automatic_reconcile_marker_matches(
        run,
        step_id=step_id,
        step=run["step_states"][step_id],
        artifact=run["artifacts"][step_id],
        marker=marker,
        source="executor",
    )

    mutated_jobs = _media_retry_run(
        step_id=step_id,
        handler=handler,
        artifact_kind=artifact_kind,
        error_code=error_code,
    )
    mutated_jobs["artifacts"][step_id]["jobs"][0]["task_id"] = "replacement-task"
    assert not automatic_reconcile_marker_matches(
        mutated_jobs,
        step_id=step_id,
        step=run["step_states"][step_id],
        artifact=mutated_jobs["artifacts"][step_id],
        marker=marker,
        source="executor",
    )

    missing_task = _media_retry_run(
        step_id=step_id,
        handler=handler,
        artifact_kind=artifact_kind,
        error_code=error_code,
    )
    missing_task["artifacts"][step_id]["jobs"][1].pop("task_id")
    assert (
        build_automatic_reconcile_retry(
            missing_task,
            step_id=step_id,
            error_code=error_code,
        )
        is None
    )

    provider_failed = _media_retry_run(
        step_id=step_id,
        handler=handler,
        artifact_kind=artifact_kind,
        error_code=error_code,
    )
    provider_failed["artifacts"][step_id]["jobs"][0]["status"] = "failed"
    assert (
        build_automatic_reconcile_retry(
            provider_failed,
            step_id=step_id,
            error_code=error_code,
        )
        is None
    )

    assert (
        build_automatic_reconcile_retry(
            run,
            step_id=step_id,
            error_code=(
                "workflow_storyboard_image_failed"
                if step_id == "storyboard_images"
                else "workflow_shot_video_failed"
            ),
        )
        is None
    )


@pytest.mark.asyncio
async def test_store_preserves_final_film_task_for_one_automatic_reconcile(
    tmp_path: Path,
) -> None:
    definition = get_workflow_definition("freezone-final-film")
    assert definition is not None
    store = WorkflowRunStore(tmp_path)
    run, _ = await store.create(
        definition=definition,
        project_id="project-1",
        canvas_id="canvas-1",
        run_mode="auto",
        inputs={"auto_generate_paid_media": True},
        idempotency_key="auto-reconcile-store",
        contract_version=2,
    )
    assert run is not None

    run, applied = await store.record_event(
        run["id"],
        event_id="prepare-auto-reconcile:understand:started",
        event_type="step_started",
        step_id="understand",
        expected_revision=run["revision"],
    )
    assert applied is True
    assert run is not None
    run, applied = await store.record_event(
        run["id"],
        event_id="prepare-auto-reconcile:understand",
        event_type="step_completed",
        step_id="understand",
        success=True,
        payload={"status": "completed"},
        expected_revision=run["revision"],
    )
    assert applied is True
    assert run is not None
    run = await complete_script_and_production_plan(
        store,
        run,
        prefix="prepare-auto-reconcile",
    )

    for step_id in ("storyboard_images", "shot_videos"):
        run, applied = await store.record_event(
            run["id"],
            event_id=f"prepare-auto-reconcile:{step_id}:started",
            event_type="step_started",
            step_id=step_id,
            expected_revision=run["revision"],
        )
        assert applied is True
        assert run is not None
        run, applied = await store.record_event(
            run["id"],
            event_id=f"prepare-auto-reconcile:{step_id}",
            event_type="step_completed",
            step_id=step_id,
            success=True,
            payload={"status": "completed"},
            expected_revision=run["revision"],
        )
        assert applied is True
        assert run is not None

    run, applied = await store.record_event(
        run["id"],
        event_id="prepare-auto-reconcile:final-film-monitoring",
        event_type="step_output_ready",
        step_id="final_film",
        success=True,
        payload={
            "kind": "freezone_final_film",
            "status": "monitoring",
            "task_id": "compose-task-1",
            "scope": "workflow:auto-reconcile:final-film",
            "compose_authorization": {
                "schema": "workflow_compose_authorization.v1",
                "authorization_id": "wca-test",
                "run_id": run["id"],
                "step_id": "final_film",
                "source_result_signature": "a" * 64,
            },
        },
        expected_revision=run["revision"],
        source="executor",
    )
    assert applied is True
    assert run is not None
    step = run["step_states"]["final_film"]

    retried = await persist_workflow_step_failure(
        store,
        run,
        step,
        "temporary reconcile failure",
        error_code="workflow_step_failed",
    )

    assert retried is not None
    assert retried["status"] == "running"
    assert retried["step_states"]["final_film"]["status"] == "running"
    assert retried["step_states"]["final_film"]["attempt"] == 2
    artifact = retried["artifacts"]["final_film"]
    assert artifact["status"] == "monitoring"
    assert artifact["task_id"] == "compose-task-1"
    assert artifact["scope"] == "workflow:auto-reconcile:final-film"
    assert artifact["compose_authorization"]["authorization_id"] == "wca-test"
    assert (
        artifact["automatic_recovery"]["schema"]
        == WORKFLOW_AUTOMATIC_RECOVERY_SCHEMA
    )
    assert "recovery" not in artifact

    failed = await persist_workflow_step_failure(
        store,
        retried,
        retried["step_states"]["final_film"],
        "temporary reconcile failure again",
        error_code="workflow_step_failed",
    )

    assert failed is not None
    assert failed["status"] == "failed"
    final_artifact = failed["artifacts"]["final_film"]
    assert final_artifact["status"] == "failed"
    assert "automatic_recovery" not in final_artifact
