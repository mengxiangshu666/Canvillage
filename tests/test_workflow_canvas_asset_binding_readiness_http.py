from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import workflows
from novelvideo.chat.approval_store import (
    consume_paid_media_grant,
    register_paid_media_grant,
)
from novelvideo.freezone import canvas_store
from novelvideo.freezone.paths import canvas_path
from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.workflow_runtime.service import WorkflowRuntimeService
from workflow_plan_support import complete_script_and_production_plan


async def _failed_asset_run(service: WorkflowRuntimeService) -> dict[str, Any]:
    definition = get_workflow_definition("freezone-storyboard-images")
    assert definition is not None
    run, reused = await service.store.create(
        definition=definition,
        project_id="demo",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "build storyboard", "script_node_id": "script-1"},
        idempotency_key="t095-http-asset-readiness",
        contract_version=2,
        project_context={"requester_user_id": "tester"},
        model_plan_snapshot={},
    )
    assert reused is False

    run = await complete_script_and_production_plan(
        service.store,
        run,
        prefix="t095",
    )
    for event_id, event_type, step_id, payload in (
        ("t095-storyboard-started", "step_started", "storyboard_images", None),
        (
            "t095-storyboard-ambiguous",
            "step_failed",
            "storyboard_images",
            {
                "error_code": "workflow_storyboard_canvas_asset_ambiguous",
                "recovery": {
                    "schema": "workflow_step_recovery.v1",
                    "workflow_run_id": run["id"],
                    "step_id": "storyboard_images",
                    "error_code": "workflow_storyboard_canvas_asset_ambiguous",
                    "action": "repair_canvas_asset_binding",
                    "next_action": (
                        "recover:repair_canvas_asset_binding:storyboard_images"
                    ),
                    "rerun_scope": "canvas_asset_binding",
                    "requires_paid_media": False,
                    "auto_retry_allowed": False,
                    "instruction": "repair the duplicate asset binding",
                    "target_node_ids": ["asset-a", "asset-b"],
                    "asset_ids": ["scene:darkroom"],
                },
            },
        ),
    ):
        event_kwargs: dict[str, Any] = {
            "event_id": event_id,
            "event_type": event_type,
            "step_id": step_id,
            "payload": payload,
            "expected_revision": run["revision"],
        }
        if event_type in {"step_completed", "step_failed"}:
            event_kwargs["success"] = event_type == "step_completed"
        if event_type == "step_failed":
            event_kwargs["error"] = "duplicate scene binding"
        run, _ = await service.store.record_event(run["id"], **event_kwargs)
        assert run is not None
    assert run["status"] == "failed"
    return run


def _asset_node(
    node_id: str,
    *,
    asset_id: str | None = "scene:darkroom",
    image_url: str = "/static/scene.png",
    **data_overrides: Any,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "imageGenNode",
        "position": {"x": 0, "y": 0},
        "data": {
            "scriptAssetId": asset_id,
            "scriptAssetOwnerId": "script-1",
            "scriptAssetRevision": 1,
            "scriptAssetContentHash": "a" * 64,
            "imageUrl": image_url,
            **data_overrides,
        },
    }


def _write_canvas(
    state_dir: Path,
    *,
    nodes: list[dict[str, Any]],
) -> None:
    canvas = canvas_store.default_canvas_payload(
        project_id="demo",
        actor_id="user-1",
    )
    canvas.update(canvas_id="canvas-1", nodes=nodes, edges=[])
    target = canvas_path(state_dir, "canvas-1")
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas_store.atomic_write_json(target, canvas)


def _client(
    monkeypatch: pytest.MonkeyPatch,
    service: WorkflowRuntimeService,
    state_dir: Path,
) -> TestClient:
    ctx = SimpleNamespace(
        state_dir=state_dir,
        requester_user_id="user-1",
    )

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
    app.dependency_overrides[workflows.get_api_user] = lambda: {
        "username": "tester",
        "user_id": "user-1",
        "credential_kind": "agent_session",
    }
    return TestClient(app)


@pytest.mark.asyncio
async def test_asset_binding_readiness_handoff_is_idempotent_and_enters_grant_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    service = WorkflowRuntimeService(tmp_path, project_id="demo")
    failed = await _failed_asset_run(service)
    _write_canvas(
        tmp_path,
        nodes=[
            _asset_node("asset-a"),
            _asset_node("asset-b", asset_id=None, image_url=""),
        ],
    )
    client = _client(monkeypatch, service, tmp_path)
    endpoint = (
        f"/api/v1/projects/demo/workflow-runs/{failed['id']}"
        "/canvas-asset-binding-revalidate"
    )
    body = {
        "canvas_id": "canvas-1",
        "step_id": "storyboard_images",
        "command_id": "t095-readiness-handoff",
        "expected_run_revision": failed["revision"],
    }

    response = client.post(endpoint, json=body)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["schema"] == "workflow_canvas_asset_binding_readiness.v1"
    assert data["status"] == "authorization_required"
    assert data["ready"] is True
    assert data["resolved_node_ids"] == ["asset-a"]
    assert data["media_submission_started"] is False
    assert data["recovery"]["action"] == "request_media_authorization"
    assert data["recovery"]["requires_paid_media"] is True
    assert data["recovery"]["auto_retry_allowed"] is False
    assert data["authorization_request"]["requires_user_action"] is True
    assert data["authorization_request"]["error_code"] == (
        "workflow_storyboard_paid_media_not_authorized"
    )

    current = await service.store.get(failed["id"])
    assert current is not None
    assert current["status"] == "failed"
    assert current["revision"] == failed["revision"] + 1
    assert current["error_code"] == "workflow_storyboard_paid_media_not_authorized"
    assert current["next_action"] == (
        "recover:request_media_authorization:storyboard_images"
    )
    artifact = current["artifacts"]["storyboard_images"]
    assert artifact["recovery"]["action"] == "request_media_authorization"
    assert artifact["readiness"]["ready"] is True
    assert "media_authorization" not in artifact

    replay = client.post(endpoint, json=body)
    replay.raise_for_status()
    replay_data = replay.json()["data"]
    assert replay_data["status"] == "idempotent_replay"
    assert replay_data["run_revision"] == current["revision"]
    repeated = await service.store.get(failed["id"])
    assert repeated is not None
    assert repeated["revision"] == current["revision"]

    grant = register_paid_media_grant(
        "tester",
        turn_id="turn-t095",
        project_id="demo",
        canvas_id="canvas-1",
        max_starts=1,
    )
    marker = {
        "schema": "workflow_media_authorization.v1",
        "authorization_id": str(grant["id"]),
        "project_id": "demo",
        "canvas_id": "canvas-1",
        "run_id": failed["id"],
        "step_id": "storyboard_images",
        "error_code": "workflow_storyboard_paid_media_not_authorized",
        "consume_key": "t095-consume",
        "source_revision": current["revision"],
    }
    allowed, reason, _ = consume_paid_media_grant(
        "tester",
        grant_id=str(grant["id"]),
        project_id="demo",
        canvas_id="canvas-1",
        idempotency_key=str(marker["consume_key"]),
    )
    assert allowed is True
    assert reason == "server_turn_grant"

    command = client.post(
        f"/api/v1/projects/demo/workflow-runs/{failed['id']}/command",
        json={
            "command": "retry",
            "step_id": "storyboard_images",
            "idempotency_key": "t095-granted-retry",
            "media_authorization": marker,
            "expected_revision": current["revision"],
        },
    )
    assert command.status_code == 200, command.text
    retried = command.json()["data"]
    assert retried["command_applied"] is True
    assert retried is not None
    assert retried["status"] == "running"
    assert retried["artifacts"]["storyboard_images"]["media_authorization"] == {
        **marker,
        "recovery_action": "request_media_authorization",
        "retry_scope": "whole_step",
        "item_ids": [],
    }
    assert retried["step_states"]["storyboard_images"]["attempt"] == 2


@pytest.mark.asyncio
async def test_asset_binding_readiness_not_ready_does_not_change_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = WorkflowRuntimeService(tmp_path, project_id="demo")
    failed = await _failed_asset_run(service)
    _write_canvas(
        tmp_path,
        nodes=[_asset_node("asset-a"), _asset_node("asset-b")],
    )
    client = _client(monkeypatch, service, tmp_path)
    endpoint = (
        f"/api/v1/projects/demo/workflow-runs/{failed['id']}"
        "/canvas-asset-binding-revalidate"
    )

    response = client.post(
        endpoint,
        json={
            "canvas_id": "canvas-1",
            "step_id": "storyboard_images",
            "command_id": "t095-not-ready",
            "expected_run_revision": failed["revision"],
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "not_ready"
    assert data["ready"] is False
    assert data["issues"][0]["reason_code"] == "binding_duplicate"
    current = await service.store.get(failed["id"])
    assert current is not None
    assert current["revision"] == failed["revision"]
    assert current["artifacts"]["storyboard_images"]["recovery"]["action"] == (
        "repair_canvas_asset_binding"
    )


@pytest.mark.asyncio
async def test_asset_binding_readiness_rejects_forged_recovery_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = WorkflowRuntimeService(tmp_path, project_id="demo")
    failed = await _failed_asset_run(service)
    _write_canvas(tmp_path, nodes=[_asset_node("asset-a")])
    client = _client(monkeypatch, service, tmp_path)
    endpoint = (
        f"/api/v1/projects/demo/workflow-runs/{failed['id']}"
        "/canvas-asset-binding-revalidate"
    )

    forged = client.post(
        endpoint,
        json={
            "canvas_id": "canvas-1",
            "step_id": "script_contract",
            "command_id": "t095-forged-step",
        },
    )
    assert forged.status_code == 409
    assert forged.json()["detail"]["code"] == (
        "workflow_canvas_asset_binding_readiness_step_not_failed"
    )

    current = await service.store.get(failed["id"])
    assert current is not None
    assert current["revision"] == failed["revision"]
