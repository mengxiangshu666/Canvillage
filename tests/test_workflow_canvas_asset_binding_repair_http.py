from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import workflows
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
        inputs={"request": "build storyboard"},
        idempotency_key="t094-http-asset-repair",
        contract_version=2,
        project_context={"requester_user_id": "tester"},
        model_plan_snapshot={},
    )
    assert reused is False

    run = await complete_script_and_production_plan(
        service.store,
        run,
        prefix="t094",
    )
    run, _ = await service.store.record_event(
        run["id"],
        event_id="t094-storyboard-started",
        event_type="step_started",
        step_id="storyboard_images",
        expected_revision=run["revision"],
    )
    assert run is not None
    run, _ = await service.store.record_event(
        run["id"],
        event_id="t094-storyboard-ambiguous",
        event_type="step_failed",
        step_id="storyboard_images",
        error="duplicate scene binding",
        payload={
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
        expected_revision=run["revision"],
    )
    assert run is not None
    assert run["status"] == "failed"
    return run


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


def _asset_node(
    node_id: str,
    *,
    image_url: str = "/static/scene.png",
    **data_overrides: Any,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "imageGenNode",
        "position": {"x": 0, "y": 0},
        "data": {
            "scriptAssetId": "scene:darkroom",
            "scriptAssetOwnerId": "script-1",
            "scriptAssetRevision": 1,
            "scriptAssetContentHash": "a" * 64,
            "imageUrl": image_url,
            **data_overrides,
        },
    }


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
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: {
        "username": "tester",
        "user_id": "user-1",
        "credential_kind": "agent_session",
    }
    return TestClient(app)


@pytest.mark.asyncio
async def test_asset_binding_repair_http_writes_once_and_leaves_run_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = WorkflowRuntimeService(tmp_path, project_id="demo")
    failed = await _failed_asset_run(service)
    _write_canvas(
        tmp_path,
        nodes=[
            _asset_node("asset-a"),
            _asset_node(
                "asset-b",
                image_url="",
                generationError="failed",
            ),
        ],
    )
    client = _client(monkeypatch, service, tmp_path)
    endpoint = (
        f"/api/v1/projects/demo/workflow-runs/{failed['id']}"
        "/canvas-asset-binding-repair"
    )

    repaired = client.post(
        endpoint,
        json={
            "canvas_id": "canvas-1",
            "step_id": "storyboard_images",
            "command_id": "t094-repair-command",
            "expected_run_revision": failed["revision"],
        },
    )

    assert repaired.status_code == 200, repaired.text
    data = repaired.json()["data"]
    assert data["schema"] == "workflow_canvas_asset_binding_repair.v1"
    assert data["status"] == "repaired"
    assert data["kept_node_ids"] == ["asset-a"]
    assert data["detached_node_ids"] == ["asset-b"]
    assert data["media_replay_started"] is False
    assert data["canvas_receipt"]["revision"] == 2
    assert data["canvas_receipt"]["affected_node_ids"] == ["asset-b"]

    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    by_id = {node["id"]: node for node in snapshot["nodes"]}
    assert by_id["asset-a"]["data"]["scriptAssetId"] == "scene:darkroom"
    assert by_id["asset-b"]["data"]["scriptAssetId"] is None
    assert by_id["asset-b"]["data"]["generationError"] == "failed"

    current = await service.store.get(failed["id"])
    assert current is not None
    assert current["status"] == "failed"
    assert current["revision"] == failed["revision"]
    assert current["step_states"]["storyboard_images"]["status"] == "failed"
    assert current["artifacts"]["storyboard_images"]["recovery"]["action"] == (
        "repair_canvas_asset_binding"
    )

    replay = client.post(
        endpoint,
        json={
            "canvas_id": "canvas-1",
            "step_id": "storyboard_images",
            "command_id": "t094-repair-command",
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["status"] == "idempotent_replay"
    assert canvas_store.read_canvas(tmp_path, "canvas-1")["revision"] == 2


@pytest.mark.asyncio
async def test_asset_binding_repair_http_rejects_forged_or_unsafe_plan(
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
        "/canvas-asset-binding-repair"
    )

    forged = client.post(
        endpoint,
        json={
            "canvas_id": "canvas-1",
            "step_id": "script_contract",
            "command_id": "t094-forged-step",
        },
    )
    assert forged.status_code == 409
    assert forged.json()["detail"]["code"] == (
        "workflow_canvas_asset_binding_repair_step_not_failed"
    )

    unsafe = client.post(
        endpoint,
        json={
            "canvas_id": "canvas-1",
            "step_id": "storyboard_images",
            "command_id": "t094-unsafe-plan",
        },
    )
    assert unsafe.status_code == 409
    assert unsafe.json()["detail"]["code"] == (
        "workflow_canvas_asset_binding_repair_not_safe"
    )

    stale = client.post(
        endpoint,
        json={
            "canvas_id": "canvas-1",
            "step_id": "storyboard_images",
            "command_id": "t094-stale-run",
            "expected_run_revision": failed["revision"] + 1,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == (
        "workflow_canvas_asset_binding_repair_run_stale"
    )
    assert canvas_store.read_canvas(tmp_path, "canvas-1")["revision"] == 1
    current = await service.store.get(failed["id"])
    assert current is not None
    assert current["revision"] == failed["revision"]
