"""Prove RQ-006 pause/resume recovery without paid media or task submission.

The script uses a temporary workflow-run database and an isolated Uvicorn
process.  It creates one draft WorkflowRun directly in the durable store,
pauses it through the HTTP command route, restarts the isolated service, reads
the run and events back, resumes, and cancels it.

The module-level scheduler is replaced with a recorder.  This keeps the smoke
strictly observational: no Workflow executor, provider call, or media task is
started.
"""

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
from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.workflow_runtime.service import WorkflowRuntimeService


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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


def _stop_server(server: uvicorn.Server, thread: threading.Thread) -> None:
    server.should_exit = True
    thread.join(timeout=5)
    if thread.is_alive():
        raise TimeoutError("isolated uvicorn did not stop")


def _create_run(
    service: WorkflowRuntimeService,
    *,
    project_id: str,
    canvas_id: str,
) -> dict[str, Any]:
    definition = get_workflow_definition("custom-canvas-workflow")
    assert definition is not None

    async def create() -> dict[str, Any]:
        run, reused = await service.store.create(
            definition=definition,
            project_id=project_id,
            canvas_id=canvas_id,
            run_mode="draft",
            inputs={
                "request": "Verify durable pause and resume without paid media.",
                "run_mode": "draft",
            },
            idempotency_key="rq006-no-paid-start",
            contract_version=2,
            goal="Recover the same WorkflowRun after an isolated service restart.",
            success_criteria=[
                "run pauses with a durable run_paused event",
                "run and events remain readable after restart",
                "resume returns to running without starting media work",
                "cancel reaches a terminal state without provider submissions",
            ],
            source_turn_id="rq006-no-paid-turn",
        )
        assert reused is False
        return run

    return asyncio.run(create())


def _build_app(
    service: WorkflowRuntimeService,
    state_dir: Path,
    scheduled: list[str],
) -> FastAPI:
    ctx = SimpleNamespace(
        state_dir=state_dir,
        requester_user_id="user-1",
        requester_username="tester",
    )

    async def fake_scope(*_args, **_kwargs):
        return ctx

    def fake_schedule(_store, run_id: str) -> None:
        scheduled.append(str(run_id))

    workflows._scope = fake_scope
    workflows._service = lambda *_args, **_kwargs: service
    workflows.schedule_workflow_run = fake_schedule
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: {
        "username": "tester",
        "user_id": "user-1",
        "credential_kind": "browser_session",
    }
    return app


def _response_data(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(f"unexpected runtime response: {payload}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"runtime response has no object data: {payload}")
    return data


def _get_run(
    client: httpx.Client,
    project_id: str,
    run_id: str,
) -> dict[str, Any]:
    return _response_data(
        client.get(f"/api/v1/projects/{project_id}/workflow-runs/{run_id}")
    )


def _get_events(
    client: httpx.Client,
    project_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    payload = _response_data(
        client.get(
            f"/api/v1/projects/{project_id}/workflow-runs/{run_id}/events",
            params={"after_seq": 0, "limit": 500},
        )
    )
    items = payload.get("items")
    if not isinstance(items, list):
        raise RuntimeError(f"event response has no items list: {payload}")
    return [item for item in items if isinstance(item, dict)]


def _event_types(events: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("type") or "") for item in events]


def _command(
    client: httpx.Client,
    project_id: str,
    run_id: str,
    *,
    command: str,
    idempotency_key: str,
    expected_revision: int,
) -> dict[str, Any]:
    return _response_data(
        client.post(
            f"/api/v1/projects/{project_id}/workflow-runs/{run_id}/command",
            json={
                "command": command,
                "idempotency_key": idempotency_key,
                "expected_revision": expected_revision,
            },
        )
    )


def run() -> dict[str, Any]:
    project_id = "rq006-no-paid-project"
    canvas_id = "rq006-no-paid-canvas"
    scheduled: list[str] = []

    with tempfile.TemporaryDirectory(prefix="rq006-no-paid-") as raw_dir:
        state_dir = Path(raw_dir)
        service = WorkflowRuntimeService(state_dir, project_id=project_id)
        created = _create_run(
            service,
            project_id=project_id,
            canvas_id=canvas_id,
        )
        run_id = str(created["id"])
        assert created["status"] == "running"
        assert created["revision"] == 0

        app = _build_app(service, state_dir, scheduled)
        server, thread, first_base_url = _start_server(app)
        try:
            with httpx.Client(base_url=first_base_url, timeout=10) as client:
                before_pause = _get_run(client, project_id, run_id)
                assert before_pause["status"] == "running"
                assert scheduled == [run_id]
                paused = _command(
                    client,
                    project_id,
                    run_id,
                    command="pause",
                    idempotency_key="rq006-no-paid-pause",
                    expected_revision=int(before_pause["revision"]),
                )
                assert paused["status"] == "paused"
                assert paused["command_applied"] is True
                paused_revision = int(paused["revision"])
                paused_events = _get_events(client, project_id, run_id)
                paused_event_types = _event_types(paused_events)
                assert paused_event_types.count("run_paused") == 1
                assert scheduled == [run_id]
        finally:
            _stop_server(server, thread)

        restarted_service = WorkflowRuntimeService(
            state_dir,
            project_id=project_id,
        )
        restarted_app = _build_app(restarted_service, state_dir, scheduled)
        server, thread, second_base_url = _start_server(restarted_app)
        try:
            with httpx.Client(base_url=second_base_url, timeout=10) as client:
                refreshed = _get_run(client, project_id, run_id)
                assert refreshed["status"] == "paused"
                assert int(refreshed["revision"]) == paused_revision
                refreshed_events = _get_events(client, project_id, run_id)
                refreshed_event_types = _event_types(refreshed_events)
                assert refreshed_event_types.count("run_paused") == 1

                resumed = _command(
                    client,
                    project_id,
                    run_id,
                    command="resume",
                    idempotency_key="rq006-no-paid-resume",
                    expected_revision=paused_revision,
                )
                assert resumed["status"] == "running"
                assert resumed["command_applied"] is True
                resumed_revision = int(resumed["revision"])
                assert scheduled == [run_id, run_id]
                resumed_events = _get_events(client, project_id, run_id)
                resumed_event_types = _event_types(resumed_events)
                assert resumed_event_types.count("run_resumed") == 1

                cancelled = _command(
                    client,
                    project_id,
                    run_id,
                    command="cancel",
                    idempotency_key="rq006-no-paid-cancel",
                    expected_revision=resumed_revision,
                )
                assert cancelled["status"] == "cancelled"
                assert cancelled["command_applied"] is True

                terminal = _get_run(client, project_id, run_id)
                assert terminal["status"] == "cancelled"
                final_events = _get_events(client, project_id, run_id)
                final_event_types = _event_types(final_events)
                assert final_event_types.count("run_paused") == 1
                assert final_event_types.count("run_resumed") == 1
                assert final_event_types.count("run_cancelled") == 1
                assert int(terminal["event_seq"]) == len(final_events)
        finally:
            _stop_server(server, thread)

        assert scheduled == [run_id, run_id]
        evidence = {
            "schema": "rq006_runtime_resume_no_paid.v1",
            "run_id": run_id,
            "project_id": project_id,
            "canvas_id": canvas_id,
            "initial_status": created["status"],
            "initial_revision": int(created["revision"]),
            "paused_status": paused["status"],
            "paused_revision": paused_revision,
            "restart_readback_status": refreshed["status"],
            "restart_readback_revision": int(refreshed["revision"]),
            "resumed_status": resumed["status"],
            "resumed_revision": resumed_revision,
            "terminal_status": terminal["status"],
            "terminal_revision": int(terminal["revision"]),
            "event_types": final_event_types,
            "event_count": len(final_events),
            "run_paused_events": final_event_types.count("run_paused"),
            "run_resumed_events": final_event_types.count("run_resumed"),
            "run_cancelled_events": final_event_types.count("run_cancelled"),
            "isolated_restart_count": 1,
            "scheduler_invocations": len(scheduled),
            "initial_running_read_scheduler_invocations": 1,
            "pause_scheduler_invocations": 0,
            "resume_scheduler_invocations": 1,
            "workflow_executor_invoked": False,
            "provider_media_submissions": 0,
            "task_submissions": 0,
        }
        print(json.dumps(evidence, ensure_ascii=True, sort_keys=True))
        return evidence


if __name__ == "__main__":
    run()
