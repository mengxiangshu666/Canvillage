from __future__ import annotations

import asyncio
import json
import socket
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI

from novelvideo.api.routes import workflows
from novelvideo.freezone import canvas_store
from novelvideo.freezone.paths import canvas_path
from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.workflow_runtime.service import WorkflowRuntimeService


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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


def _write_canvas(state_dir: Path) -> None:
    canvas = canvas_store.default_canvas_payload(
        project_id="demo",
        actor_id="user-1",
    )
    canvas.update(
        canvas_id="canvas-1",
        nodes=[
            _asset_node("asset-a"),
            _asset_node(
                "asset-b",
                image_url="",
                generationError="failed",
            ),
        ],
        edges=[],
    )
    target = canvas_path(state_dir, "canvas-1")
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas_store.atomic_write_json(target, canvas)


async def _failed_asset_run(service: WorkflowRuntimeService) -> dict[str, Any]:
    definition = get_workflow_definition("freezone-storyboard-images")
    assert definition is not None
    run, reused = await service.store.create(
        definition=definition,
        project_id="demo",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "build storyboard"},
        idempotency_key="t094-live-http-asset-repair",
        contract_version=2,
        project_context={"requester_user_id": "user-1"},
        model_plan_snapshot={},
    )
    assert reused is False

    for event_id, event_type, step_id, payload in (
        ("script-started", "step_started", "script_contract", None),
        (
            "script-completed",
            "step_completed",
            "script_contract",
            {"status": "completed"},
        ),
        ("storyboard-started", "step_started", "storyboard_images", None),
        (
            "storyboard-ambiguous",
            "step_failed",
            "storyboard_images",
            {
                "error_code": "workflow_storyboard_canvas_asset_ambiguous",
                "recovery": {
                    "schema": "workflow_step_recovery.v1",
                    "workflow_run_id": run["id"],
                    "step_id": "storyboard_images",
                    "error_code": (
                        "workflow_storyboard_canvas_asset_ambiguous"
                    ),
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


def _build_app(
    service: WorkflowRuntimeService,
    state_dir: Path,
) -> FastAPI:
    ctx = SimpleNamespace(
        state_dir=state_dir,
        requester_user_id="user-1",
    )

    async def fake_scope(*_args, **_kwargs):
        return ctx

    workflows._scope = fake_scope
    workflows._service = lambda *_args, **_kwargs: service
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: {
        "username": "tester",
        "user_id": "user-1",
        "credential_kind": "agent_session",
    }
    return app


def _start_server(app: FastAPI) -> tuple[uvicorn.Server, threading.Thread, str]:
    port = _free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive():
        if time.monotonic() >= deadline:
            raise TimeoutError("isolated uvicorn did not start")
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("isolated uvicorn stopped before startup")
    return server, thread, f"http://127.0.0.1:{port}"


def run() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="t094-live-http-") as raw_dir:
        state_dir = Path(raw_dir)
        service = WorkflowRuntimeService(state_dir, project_id="demo")
        failed = asyncio.run(_failed_asset_run(service))
        _write_canvas(state_dir)
        before_canvas = canvas_store.read_canvas(state_dir, "canvas-1")
        assert before_canvas is not None

        app = _build_app(service, state_dir)
        server, thread, base_url = _start_server(app)
        endpoint = (
            f"/api/v1/projects/demo/workflow-runs/{failed['id']}"
            "/canvas-asset-binding-repair"
        )
        body = {
            "canvas_id": "canvas-1",
            "step_id": "storyboard_images",
            "command_id": "t094-live-http-repair",
            "expected_run_revision": failed["revision"],
        }
        try:
            with httpx.Client(base_url=base_url, timeout=10) as client:
                repaired = client.post(endpoint, json=body)
                repaired.raise_for_status()
                replay = client.post(endpoint, json=body)
                replay.raise_for_status()
                current_response = client.get(
                    f"/api/v1/projects/demo/workflow-runs/{failed['id']}"
                )
                current_response.raise_for_status()
        finally:
            server.should_exit = True
            thread.join(timeout=5)

        repaired_data = repaired.json()["data"]
        replay_data = replay.json()["data"]
        current = current_response.json()["data"]
        after_canvas = canvas_store.read_canvas(state_dir, "canvas-1")
        assert after_canvas is not None
        nodes = {node["id"]: node for node in after_canvas["nodes"]}
        step_state = current["step_states"]["storyboard_images"]

        assert repaired_data["status"] == "repaired"
        assert repaired_data["next_action"] == "revalidate_readiness"
        assert repaired_data["media_replay_started"] is False
        assert replay_data["status"] == "idempotent_replay"
        assert current["status"] == "failed"
        assert current["revision"] == failed["revision"]
        assert step_state["attempt"] == 1
        assert nodes["asset-a"]["data"]["scriptAssetId"] == "scene:darkroom"
        assert nodes["asset-b"]["data"]["scriptAssetId"] is None
        assert nodes["asset-b"]["data"]["generationError"] == "failed"
        assert after_canvas["revision"] == before_canvas["revision"] + 1

        evidence = {
            "schema": "t094_live_http_asset_binding_repair_evidence.v1",
            "base_url": base_url,
            "run_id": failed["id"],
            "run_revision_before": failed["revision"],
            "run_revision_after": current["revision"],
            "step_attempt_before": failed["step_states"]["storyboard_images"][
                "attempt"
            ],
            "step_attempt_after": step_state["attempt"],
            "canvas_revision_before": before_canvas["revision"],
            "canvas_revision_after": after_canvas["revision"],
            "first_status": repaired_data["status"],
            "replay_status": replay_data["status"],
            "media_replay_started": repaired_data["media_replay_started"],
            "kept_node_ids": repaired_data["kept_node_ids"],
            "detached_node_ids": repaired_data["detached_node_ids"],
            "provider_media_submissions": 0,
        }
        print(json.dumps(evidence, ensure_ascii=True, sort_keys=True))
        return evidence


if __name__ == "__main__":
    run()
