from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import workflows
from novelvideo.chat import village_turn_policy
from novelvideo.workflow_runtime.service import WorkflowRuntimeService

pytestmark = pytest.mark.m03


async def _failed_two_item_media_run(
    service: WorkflowRuntimeService,
):
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="auto",
        inputs={"request": "两镜头批量生成"},
        idempotency_key="http-itemized-media-retry",
        contract_version=1,
    )
    for event_id, event_type, step_id in (
        ("structure-ready", "canvas_applied", ""),
        ("story-ready", "step_completed", "story_and_shots"),
        ("assets-ready", "step_completed", "asset_slots"),
    ):
        run, _ = await service.store.record_event(
            run["id"],
            event_id=event_id,
            event_type=event_type,
            step_id=step_id,
            success=True,
            expected_revision=run["revision"],
        )
        assert run is not None

    run, _ = await service.store.record_event(
        run["id"],
        event_id="media-plan",
        event_type="step_output_ready",
        step_id="media_generation",
        payload={
            "status": "monitoring",
            "completion_mode": "media_tasks",
            "target_node_ids": ["shot-1", "shot-2"],
        },
        expected_revision=run["revision"],
    )
    assert run is not None
    run, _ = await service.store.record_event(
        run["id"],
        event_id="media-started",
        event_type="step_progress",
        step_id="media_generation",
        payload={
            "status": "monitoring",
            "jobs": [
                {"node_id": "shot-1", "task_key": "task-1"},
                {"node_id": "shot-2", "task_key": "task-2"},
            ],
        },
        expected_revision=run["revision"],
    )
    assert run is not None
    run, _ = await service.store.record_event(
        run["id"],
        event_id="media-one-failed",
        event_type="step_failed",
        step_id="media_generation",
        error="上游超时",
        payload={
            "target_node_ids": ["shot-1", "shot-2"],
            "failed_node_id": "shot-1",
        },
        expected_revision=run["revision"],
    )
    assert run is not None
    run, _ = await service.store.record_event(
        run["id"],
        event_id="media-partial-completed",
        event_type="step_items_updated",
        step_id="media_generation",
        payload={
            "items": [
                {"id": "shot-1", "status": "failed", "error": "上游超时"},
                {"id": "shot-2", "status": "completed"},
            ],
        },
        expected_revision=run["revision"],
        source="verifier",
    )
    assert run is not None
    assert run["status"] == "failed"
    return run


@pytest.mark.asyncio
async def test_http_retry_failed_items_only_preserves_completed_items(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run = await _failed_two_item_media_run(service)

    async def fake_scope(*_args, **_kwargs):
        return SimpleNamespace(
            requester_username="tester",
            owner_username="tester",
            project_id="project-1",
            state_dir=tmp_path,
        )

    monkeypatch.setattr(workflows, "_scope", fake_scope)
    monkeypatch.setattr(workflows, "_service", lambda *_args, **_kwargs: service)
    monkeypatch.setattr(workflows, "schedule_workflow_run", lambda *_args, **_kwargs: None)

    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: {
        "username": "tester",
        "user_id": "user-1",
    }
    client = TestClient(app)

    recovery_prompt = (
        "[VILLAGE_AGENT_CONTEXT_CHECKPOINT]\n"
        + json.dumps(
            {
                "recovery_contract": {
                    "schema": "village_agent_recovery_contract.v1",
                    "action": "inspect_before_action",
                    "allow_new_submission": False,
                    "workflow_run_id": run["id"],
                    "recovery": {
                        "action": "requeue_shot_video",
                        "requires_paid_media": True,
                        "step_id": "media_generation",
                        "media_action": "shot-videos",
                        "target_node_id": "shot-1",
                    },
                }
            },
            ensure_ascii=False,
        )
        + "\n[/VILLAGE_AGENT_CONTEXT_CHECKPOINT]"
    )
    contract = village_turn_policy.recovery_contract_from_prompt(recovery_prompt)
    assert contract is not None
    director = village_turn_policy.DirectorWorkflowRun(recovery_contract=contract)
    assert (
        director.start_tool(
            "village_canvas_get_script_media_readiness",
            {
                "rawInput": {
                    "canvas_id": "canvas-1",
                    "node_id": "shot-1",
                    "action": "shot-videos",
                }
            },
        )
        is None
    )
    director.finish_tool(
        "village_canvas_get_script_media_readiness",
        {
            "result": {
                "data": {
                    "schema": "script_media_readiness.v1",
                    "ready": True,
                    "target_node_id": "shot-1",
                    "action": "shot-videos",
                }
            }
        },
    )
    assert director.payload()["recovery"]["retry_authorized"] is True

    retry_payload = {
        "command": "retry",
        "step_id": "media_generation",
        "retry_scope": "failed_items_only",
        "item_ids": ["shot-1"],
        "idempotency_key": "http-retry-shot-1",
        "expected_revision": run["revision"],
    }
    assert (
        director.start_tool(
            "village_canvas_command_workflow_run",
            {
                "rawInput": {
                    **retry_payload,
                    "run_id": "wfr-other",
                }
            },
        )
        == village_turn_policy.VILLAGE_CANVAS_RECOVERY_INSPECT_MESSAGE
    )
    assert director.payload()["recovery"]["retry_authorized"] is True
    assert (
        director.start_tool(
            "village_canvas_command_workflow_run",
            {"rawInput": {**retry_payload, "run_id": run["id"]}},
        )
        is None
    )
    assert director.payload()["recovery"]["retry_authorized"] is False
    assert director.payload()["recovery"]["retry_consumed"] is True

    response = client.post(
        f"/api/v1/projects/project-1/workflow-runs/{run['id']}/command",
        json=retry_payload,
    )

    assert response.status_code == 200, response.text
    retried = response.json()["data"]
    assert (
        director.finish_tool(
            "village_canvas_command_workflow_run",
            {"result": {"data": retried}},
        )
        == "observing"
    )
    assert director.workflow_receipt_pending is True
    assert director.workflow_run_status == "running"
    assert director.complete_turn() == "observing"
    assert director.payload()["status"] == "observing"
    assert director.payload()["workflow_run_continuation"] == {
        "run_id": run["id"],
        "run_status": "running",
        "receipt_pending": True,
    }
    item_states = retried["artifacts"]["media_generation"]["item_states"]
    assert item_states["shot-1"]["status"] == "pending"
    assert item_states["shot-1"]["attempt"] == 2
    assert item_states["shot-2"]["status"] == "completed"
    assert retried["artifacts"]["media_generation"]["retry_item_ids"] == ["shot-1"]
