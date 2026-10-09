from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import workflows
from novelvideo.workflow_runtime.compose_authorization import (
    COMPOSE_AUTHORIZATION_SCHEMA,
)
from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.workflow_runtime.service import WorkflowRuntimeService
from workflow_plan_support import complete_script_and_production_plan


SIGNATURE = "a" * 64


async def _failed_compose_run(service: WorkflowRuntimeService) -> dict:
    definition = get_workflow_definition("freezone-final-film")
    assert definition is not None
    run, reused = await service.store.create(
        definition=definition,
        project_id="demo",
        canvas_id="canvas-1",
        run_mode="auto",
        inputs={"auto_generate_paid_media": True},
        idempotency_key="t083-http-compose-authorization",
        contract_version=2,
        project_context={"requester_user_id": "tester"},
        model_plan_snapshot={},
    )
    assert reused is False

    run = await complete_script_and_production_plan(
        service.store,
        run,
        prefix="t083",
    )
    for step_id in ("storyboard_images", "shot_videos"):
        run, _ = await service.store.record_event(
            run["id"],
            event_id=f"t083-{step_id}-started",
            event_type="step_started",
            step_id=step_id,
            expected_revision=run["revision"],
        )
        assert run is not None
        payload = (
            {
                "schema": "workflow_shot_videos_artifact.v1",
                "status": "completed",
                "completed_count": 1,
                "shot_count": 1,
                "result_signature": SIGNATURE,
            }
            if step_id == "shot_videos"
            else {"status": "completed"}
        )
        run, _ = await service.store.record_event(
            run["id"],
            event_id=f"t083-{step_id}-completed",
            event_type="step_completed",
            step_id=step_id,
            success=True,
            payload=payload,
            expected_revision=run["revision"],
        )
        assert run is not None

    run, _ = await service.store.record_event(
        run["id"],
        event_id="t083-final-film-started",
        event_type="step_started",
        step_id="final_film",
        expected_revision=run["revision"],
    )
    assert run is not None
    run, _ = await service.store.record_event(
        run["id"],
        event_id="t083-final-film-not-authorized",
        event_type="step_failed",
        step_id="final_film",
        error="最终合成需要专用票据",
        payload={
            "error_code": "workflow_final_film_not_authorized",
            "source_result_signature": SIGNATURE,
            "recovery": {
                "schema": "workflow_step_recovery.v1",
                "workflow_run_id": run["id"],
                "step_id": "final_film",
                "error_code": "workflow_final_film_not_authorized",
                "action": "request_compose_authorization",
                "next_action": ("recover:request_compose_authorization:final_film"),
                "rerun_scope": "final_film",
                "item_ids": [],
                "job_ids": [],
                "requires_paid_media": False,
                "auto_retry_allowed": False,
                "instruction": "先取得最终合成专用票据。",
                "authorization_request": {
                    "schema": "workflow_compose_authorization_request.v1",
                    "run_id": run["id"],
                    "step_id": "final_film",
                    "source_result_signature": SIGNATURE,
                    "requires_user_action": True,
                },
            },
        },
        expected_revision=run["revision"],
    )
    assert run is not None
    assert run["status"] == "failed"
    return run


@pytest.mark.asyncio
async def test_compose_authorization_http_issue_consume_and_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = WorkflowRuntimeService(tmp_path, project_id="demo")
    failed = await _failed_compose_run(service)
    ctx = SimpleNamespace(state_dir=tmp_path)
    current_user = {
        "username": "tester",
        "user_id": "user-1",
        "credential_kind": "browser",
    }

    async def fake_scope(*_args, **_kwargs):
        return ctx

    monkeypatch.setattr(workflows, "_scope", fake_scope)
    monkeypatch.setattr(workflows, "_service", lambda *_args, **_kwargs: service)
    monkeypatch.setattr(
        workflows,
        "schedule_workflow_run",
        lambda *_args, **_kwargs: None,
    )
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: dict(current_user)
    client = TestClient(app)

    issue_path = (
        f"/api/v1/projects/demo/workflow-runs/{failed['id']}/compose-authorizations"
    )
    current_user["credential_kind"] = "agent_session"
    missing = client.post(
        f"/api/v1/projects/demo/workflow-runs/{failed['id']}/command",
        json={
            "command": "retry",
            "step_id": "final_film",
            "idempotency_key": "t083-http-compose-missing",
            "expected_revision": failed["revision"],
        },
    )
    assert missing.status_code == 409, missing.text
    assert missing.json()["detail"]["code"] == "workflow_compose_authorization_required"
    still_failed = await service.store.get(failed["id"])
    assert still_failed is not None
    assert still_failed["revision"] == failed["revision"]

    forged = client.post(
        f"/api/v1/projects/demo/workflow-runs/{failed['id']}/command",
        json={
            "command": "retry",
            "step_id": "final_film",
            "idempotency_key": "t083-http-compose-forged",
            "expected_revision": failed["revision"],
            "compose_authorization": {
                "schema": COMPOSE_AUTHORIZATION_SCHEMA,
                "authorization_id": "wca_forged",
                "project_id": "demo",
                "canvas_id": "canvas-1",
                "run_id": failed["id"],
                "step_id": "final_film",
                "source_result_signature": SIGNATURE,
                "consume_key": "t083-http-forged-consume",
            },
        },
    )
    assert forged.status_code == 409, forged.text
    assert forged.json()["detail"]["code"] == "workflow_compose_authorization_not_found"
    unchanged = await service.store.get(failed["id"])
    assert unchanged is not None
    assert unchanged["status"] == "failed"
    assert "compose_authorization" not in unchanged["artifacts"].get(
        "final_film",
        {},
    )

    current_user["credential_kind"] = "browser"
    issued = client.post(
        issue_path,
        json={"canvas_id": "canvas-1", "step_id": "final_film"},
    )
    assert issued.status_code == 200, issued.text
    ticket = issued.json()["data"]
    assert ticket["run_id"] == failed["id"]
    assert ticket["source_result_signature"] == SIGNATURE

    current_user["credential_kind"] = "agent_session"
    consume_key = "t083-http-consume"
    consumed = client.post(
        f"{issue_path}/consume",
        json={
            "canvas_id": "canvas-1",
            "step_id": "final_film",
            "authorization_id": ticket["id"],
            "source_result_signature": SIGNATURE,
            "consume_key": consume_key,
        },
    )
    assert consumed.status_code == 200, consumed.text
    assert consumed.json()["data"]["reason"] == "compose_authorization"

    command_body = {
        "command": "retry",
        "step_id": "final_film",
        "idempotency_key": "t083-http-compose-retry",
        "expected_revision": failed["revision"],
        "compose_authorization": {
            "schema": COMPOSE_AUTHORIZATION_SCHEMA,
            "authorization_id": ticket["id"],
            "project_id": "demo",
            "canvas_id": "canvas-1",
            "run_id": failed["id"],
            "step_id": "final_film",
            "source_result_signature": SIGNATURE,
            "consume_key": consume_key,
        },
    }
    commanded = client.post(
        f"/api/v1/projects/demo/workflow-runs/{failed['id']}/command",
        json=command_body,
    )
    assert commanded.status_code == 200, commanded.text
    assert commanded.json()["data"]["command_applied"] is True

    current = await service.store.get(failed["id"])
    assert current is not None
    marker = current["artifacts"]["final_film"]["compose_authorization"]
    assert marker["authorization_id"] == ticket["id"]
    assert marker["source_result_signature"] == SIGNATURE

    replayed = client.post(
        f"/api/v1/projects/demo/workflow-runs/{failed['id']}/command",
        json=command_body,
    )
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["data"]["command_applied"] is False
    replayed_run = await service.store.get(failed["id"])
    assert replayed_run is not None
    assert (
        replayed_run["artifacts"]["final_film"]["compose_authorization"][
            "authorization_id"
        ]
        == ticket["id"]
    )


@pytest.mark.asyncio
async def test_compose_authorization_http_requires_browser_issue_and_agent_consume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = WorkflowRuntimeService(tmp_path, project_id="demo")
    failed = await _failed_compose_run(service)
    ctx = SimpleNamespace(state_dir=tmp_path)
    current_user = {
        "username": "tester",
        "user_id": "user-1",
        "credential_kind": "agent_session",
    }

    async def fake_scope(*_args, **_kwargs):
        return ctx

    monkeypatch.setattr(workflows, "_scope", fake_scope)
    monkeypatch.setattr(workflows, "_service", lambda *_args, **_kwargs: service)
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: dict(current_user)
    client = TestClient(app)
    path = f"/api/v1/projects/demo/workflow-runs/{failed['id']}/compose-authorizations"

    issued = client.post(
        path,
        json={"canvas_id": "canvas-1", "step_id": "final_film"},
    )
    assert issued.status_code == 403
    assert (
        issued.json()["detail"]["code"]
        == "workflow_compose_authorization_browser_required"
    )

    current_user["credential_kind"] = "browser"
    consumed = client.post(
        f"{path}/consume",
        json={
            "canvas_id": "canvas-1",
            "step_id": "final_film",
            "authorization_id": "wca_forged",
            "source_result_signature": SIGNATURE,
            "consume_key": "t083-http-forged",
        },
    )
    assert consumed.status_code == 403
    assert (
        consumed.json()["detail"]["code"]
        == "workflow_compose_authorization_agent_required"
    )
