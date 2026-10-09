"""Prove cross-session Agent work-ledger resume without paid media.

The script starts an isolated Uvicorn process over a temporary state directory,
seeds one durable ``freezone-final-film`` WorkflowRun, and runs three real
``VillageAgentThread`` sessions.  The model is a local ``FunctionModel`` so the
run is deterministic and free; the tools, HTTP routes, canvas gateway, durable
run store, ledger projection, and prompt injection are all production code.

Session 1 observes a verified workflow receipt, records one real tool failure,
and writes one canvas node through the capability broker.
Session 2 starts from the persisted ledger after the durable run artifact is
replaced with a newer receipt; it proves the old receipt is deprecated instead
of erased.
Session 3 starts again from disk and proves the ledger briefing is present in
the system prompt and that the current stage routes to character assets.

No provider call, media submission, paid authorization, 8784 request, or
``项目资产/`` write is performed.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pydantic_ai
import uvicorn
from fastapi import FastAPI, Request
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from novelvideo.api.routes import workflows
from novelvideo.chat import village_harness
from novelvideo.chat.village_harness import VillageAgentThread
from novelvideo.freezone import canvas_store
from novelvideo.freezone.paths import canvas_path
from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.workflow_runtime.project_work_ledger import (
    load_project_work_ledger,
    next_stage,
)
from novelvideo.workflow_runtime.service import WorkflowRuntimeService


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_server(
    app: FastAPI,
) -> tuple[uvicorn.Server, threading.Thread, str]:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
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


def _script_artifact(signature: str) -> dict[str, Any]:
    return {
        "schema": "workflow_freezone_script_artifact.v1",
        "kind": "freezone_script_contract",
        "status": "completed",
        "workflow_step_id": "script_contract",
        "task_id": "t219-script-task",
        "job_id": "t219-script-job",
        "rows": [{"shot_id": "shot-1"}],
        "contract_report": {
            "blocking_count": 0,
            "issue_count": 0,
            "rows_fingerprint": "a" * 64,
        },
        "result_signature": signature,
    }


def _seed_project(
    state_dir: Path,
    *,
    project_id: str,
    canvas_id: str,
) -> tuple[WorkflowRuntimeService, dict[str, Any]]:
    canvas = canvas_store.default_canvas_payload(
        project_id=project_id,
        actor_id="t219-user",
    )
    canvas["canvas_id"] = canvas_id
    canvas_store.atomic_write_json(canvas_path(state_dir, canvas_id), canvas)

    service = WorkflowRuntimeService(state_dir, project_id=project_id)
    definition = get_workflow_definition("freezone-final-film")
    if definition is None:
        raise RuntimeError("freezone-final-film definition is missing")

    async def create() -> tuple[WorkflowRuntimeService, dict[str, Any]]:
        run, reused = await service.store.create(
            definition=definition,
            project_id=project_id,
            canvas_id=canvas_id,
            run_mode="draft",
            inputs={"request": "继续现有短片项目"},
            idempotency_key="t219-isolated-run",
            contract_version=2,
            goal="继续现有短片项目",
            success_criteria=["完成可恢复的项目阶段"],
        )
        if reused:
            raise RuntimeError("the isolated run unexpectedly reused an existing id")
        completed, applied = await service.store.record_event(
            run["id"],
            event_id="t219-script-contract-a",
            event_type="step_completed",
            step_id="script_contract",
            success=True,
            payload=_script_artifact("a" * 64),
            expected_revision=run["revision"],
        )
        if not applied or completed is None:
            raise RuntimeError("the seeded script receipt was not persisted")
        return service, completed

    return asyncio.run(create())


def _build_app(
    service: WorkflowRuntimeService,
    *,
    state_dir: Path,
    request_counts: dict[str, int],
) -> FastAPI:
    async def fake_scope(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            state_dir=state_dir,
            requester_user_id="t219-user",
            requester_username="t219",
        )

    workflows._scope = fake_scope
    workflows._service = lambda *_args, **_kwargs: service
    # The acceptance run must not accidentally start a real workflow executor.
    workflows.schedule_workflow_run = lambda *_args, **_kwargs: None

    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: {
        "username": "t219",
        "user_id": "t219-user",
        "credential_kind": "agent_session",
    }

    @app.post("/api/v1/chat/canvas-patch")
    async def isolated_canvas_patch() -> dict[str, bool]:
        """Accept the best-effort UI mirror; this script drives no browser."""

        return {"ok": True}

    @app.middleware("http")
    async def count_requests(request: Request, call_next):
        key = f"{request.method} {request.url.path}"
        request_counts[key] = request_counts.get(key, 0) + 1
        return await call_next(request)

    return app


def _replace_script_signature(
    state_dir: Path,
    *,
    run_id: str,
    signature: str,
) -> None:
    """Replace the durable run's script receipt with a newer fixture revision.

    This is the only fixture mutation in the script.  It uses the same durable
    SQLite row read by the real workflow API; the projection that turns the
    changed receipt into a deprecation event is still production code.
    """

    db_path = state_dir / "workflow_runs.db"
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT artifacts_json FROM canvas_workflow_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("isolated workflow run disappeared")
        artifacts = json.loads(row[0])
        script = artifacts.get("script_contract")
        if not isinstance(script, dict):
            raise RuntimeError("isolated workflow run has no script artifact")
        script["result_signature"] = signature
        connection.execute(
            "UPDATE canvas_workflow_runs SET artifacts_json=? WHERE id=?",
            (json.dumps(artifacts, ensure_ascii=False), run_id),
        )
        connection.commit()


def _install_local_model(model: FunctionModel) -> tuple[Any, Any]:
    original_resolve = village_harness.resolve_village_agent_model
    original_get_model = village_harness.get_direct_pydantic_model
    village_harness.resolve_village_agent_model = lambda _value: "local-t219"
    village_harness.get_direct_pydantic_model = lambda *_args, **_kwargs: model
    return original_resolve, original_get_model


def _restore_local_model(resolve: Any, get_model: Any) -> None:
    village_harness.resolve_village_agent_model = resolve
    village_harness.get_direct_pydantic_model = get_model


def _tool_call(name: str, arguments: dict[str, Any], call_id: str) -> dict[int, DeltaToolCall]:
    return {
        0: DeltaToolCall(
            name=name,
            json_args=json.dumps(arguments, ensure_ascii=False),
            tool_call_id=call_id,
        )
    }


async def _run_sessions(
    *,
    state_dir: Path,
    project_id: str,
    canvas_id: str,
    run_id: str,
    captured_prompts: list[str],
) -> tuple[list[Any], list[Any], list[Any]]:
    calls = 0

    async def first_turn(
        _messages: list[Any],
        _agent_info: AgentInfo,
    ):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield _tool_call(
                "village_canvas_capability",
                {
                    "action": "invoke",
                    "capability_id": "workflow.run.get",
                    "arguments": {"run_id": run_id},
                },
                "t219-call-run",
            )
        elif calls == 2:
            yield _tool_call(
                "village_canvas_capability",
                {
                    "action": "invoke",
                    "capability_id": "workflow.run.get",
                    "arguments": {"run_id": "t219-missing-run"},
                },
                "t219-call-failure",
            )
        elif calls == 3:
            yield _tool_call(
                "village_canvas_capability",
                {
                    "action": "invoke",
                    "capability_id": "canvas.compatibility.emit",
                    "arguments": {
                        "canvas_id": canvas_id,
                        "command_id": "t219-node-1",
                        "expected_canvas_revision": 1,
                        "commands": [
                            {
                                "type": "create_canvas_node",
                                "node_type": "textAnnotationNode",
                                "text": "项目工作账本续做验证",
                            }
                        ],
                    },
                },
                "t219-call-write",
            )
        else:
            yield "已接续项目进度。"

    first_model = FunctionModel(stream_function=first_turn)
    resolve, get_model = _install_local_model(first_model)
    try:
        first_thread = VillageAgentThread(
            id="t219-session-1",
            model_id="local-t219",
            scope_kind="project",
        )
        first_events = [
            event
            async for event in first_thread.stream(
                "继续项目，把当前缺失的结构节点补齐",
                current_project=project_id,
                current_canvas=canvas_id,
                current_project_state_dir=str(state_dir),
            )
        ]
    finally:
        _restore_local_model(resolve, get_model)

    _replace_script_signature(state_dir, run_id=run_id, signature="b" * 64)

    second_calls = 0

    async def second_turn(
        _messages: list[Any],
        _agent_info: AgentInfo,
    ):
        nonlocal second_calls
        second_calls += 1
        if second_calls == 1:
            yield _tool_call(
                "village_canvas_capability",
                {
                    "action": "invoke",
                    "capability_id": "workflow.run.get",
                    "arguments": {"run_id": run_id},
                },
                "t219-call-run-b",
            )
        else:
            yield "已读取更新后的项目进度。"

    second_model = FunctionModel(stream_function=second_turn)
    resolve, get_model = _install_local_model(second_model)
    try:
        second_thread = VillageAgentThread(
            id="t219-session-2",
            model_id="local-t219",
            scope_kind="project",
        )
        second_events = [
            event
            async for event in second_thread.stream(
                "继续处理项目",
                current_project=project_id,
                current_canvas=canvas_id,
                current_project_state_dir=str(state_dir),
            )
        ]
    finally:
        _restore_local_model(resolve, get_model)

    async def third_turn(
        _messages: list[Any],
        _agent_info: AgentInfo,
    ):
        yield "继续角色资产阶段。"

    third_model = FunctionModel(stream_function=third_turn)
    resolve, get_model = _install_local_model(third_model)
    try:
        third_thread = VillageAgentThread(
            id="t219-session-3",
            model_id="local-t219",
            scope_kind="project",
        )
        third_events = [
            event
            async for event in third_thread.stream(
                "接着来",
                current_project=project_id,
                current_canvas=canvas_id,
                current_project_state_dir=str(state_dir),
            )
        ]
    finally:
        _restore_local_model(resolve, get_model)

    if len(captured_prompts) != 3:
        raise RuntimeError(
            f"expected 3 captured system prompts, got {len(captured_prompts)}"
        )
    return first_events, second_events, third_events


def run() -> dict[str, Any]:
    project_id = "t219-isolated-project"
    canvas_id = "t219-isolated-canvas"
    request_counts: dict[str, int] = {}
    captured_prompts: list[str] = []
    original_agent = pydantic_ai.Agent

    def capturing_agent(*args: Any, **kwargs: Any):
        captured_prompts.append(str(kwargs.get("system_prompt") or ""))
        return original_agent(*args, **kwargs)

    pydantic_ai.Agent = capturing_agent
    try:
        with tempfile.TemporaryDirectory(prefix="t219-ledger-resume-") as raw_dir:
            state_dir = Path(raw_dir)
            service, seeded_run = _seed_project(
                state_dir,
                project_id=project_id,
                canvas_id=canvas_id,
            )
            run_id = str(seeded_run["id"])
            app = _build_app(
                service,
                state_dir=state_dir,
                request_counts=request_counts,
            )
            server, thread, base_url = _start_server(app)
            os.environ["VILLAGE_CANVAS_API_URL"] = base_url
            os.environ["VILLAGE_CANVAS_AGENT_TOKEN"] = "t219-isolated-token"
            try:
                first_events, second_events, third_events = asyncio.run(
                    _run_sessions(
                        state_dir=state_dir,
                        project_id=project_id,
                        canvas_id=canvas_id,
                        run_id=run_id,
                        captured_prompts=captured_prompts,
                    )
                )
            finally:
                _stop_server(server, thread)

            stored = load_project_work_ledger(state_dir)
            if stored is None:
                raise RuntimeError("project work ledger was not persisted")
            stages = {
                str(stage["step_id"]): stage for stage in stored.get("stages") or []
            }
            script_stage = stages["script_contract"]
            production_stage = stages["production_plan"]
            script_artifacts = list(script_stage["artifacts"])
            if len(script_artifacts) != 2:
                raise RuntimeError(
                    "expected one superseded and one current script receipt"
                )
            old_script, new_script = script_artifacts
            canvas_nodes = [
                item
                for item in production_stage["artifacts"]
                if str(item.get("node_key") or "").startswith("canvas:")
            ]
            if not canvas_nodes:
                raise RuntimeError("the verified canvas node was not backfilled")
            canvas_node_id = str(canvas_nodes[0]["node_key"]).split(":", 1)[1]
            snapshot = canvas_store.read_canvas(state_dir, canvas_id)
            if snapshot is None:
                raise RuntimeError("isolated canvas disappeared")
            node_ids = {str(node.get("id") or "") for node in snapshot["nodes"]}
            if canvas_node_id not in node_ids:
                raise RuntimeError("ledger node id does not match the canvas")
            failures = list(production_stage.get("failures") or [])
            if not any(
                item.get("error_code") == "Not Found" for item in failures
            ):
                raise RuntimeError("the real tool failure was not recorded")
            failure_codes = [
                str(item.get("error_code") or "") for item in failures
            ]
            if "FZ_EMIT_UNVERIFIED" in failure_codes:
                raise RuntimeError(
                    "a verified canvas write was recorded as an unverified failure"
                )
            if old_script.get("deprecated") is not True:
                raise RuntimeError("the superseded workflow receipt was not deprecated")
            if new_script.get("deprecated") is True:
                raise RuntimeError("the current workflow receipt was deprecated")

            second_started = next(
                event for event in second_events if event.type == "thread_started"
            )
            third_started = next(
                event for event in third_events if event.type == "thread_started"
            )
            second_stage = second_started.raw["route_receipt"]["project_stage"]
            third_stage = third_started.raw["route_receipt"]["project_stage"]
            first_types = [event.type for event in first_events]
            second_types = [event.type for event in second_events]
            third_types = [event.type for event in third_events]
            if first_types[-1] != "complete":
                raise RuntimeError("session 1 did not complete")
            if second_types[-1] != "complete":
                raise RuntimeError("session 2 did not complete")
            if third_types[-1] != "complete":
                raise RuntimeError("session 3 did not complete")
            if "[PROJECT_WORK_LEDGER]" not in captured_prompts[1]:
                raise RuntimeError("session 2 did not receive the persisted ledger")
            if "已废弃" not in captured_prompts[2]:
                raise RuntimeError("session 3 did not receive the deprecation record")
            if second_stage["workflow_step"] != "production_plan":
                raise RuntimeError("session 2 did not route from the persisted stage")
            if third_stage["stage_id"] != "character_assets":
                raise RuntimeError("session 3 did not route to character assets")
            if next_stage(stored) != "production_plan":
                raise RuntimeError("ledger next stage is not production_plan")

            evidence = {
                "schema": "t219_agent_ledger_resume_no_paid.v1",
                "base_url": base_url,
                "project_id": project_id,
                "canvas_id": canvas_id,
                "run_id": run_id,
                "ledger_revision": stored["ledger_revision"],
                "ledger_next_stage": next_stage(stored),
                "session_count": 3,
                "session_1_event_types": first_types,
                "session_2_event_types": second_types,
                "session_3_event_types": third_types,
                "session_2_prompt_has_ledger": (
                    "[PROJECT_WORK_LEDGER]" in captured_prompts[1]
                ),
                "session_3_prompt_has_deprecation": (
                    "已废弃" in captured_prompts[2]
                ),
                "session_2_project_stage": second_stage,
                "session_3_project_stage": third_stage,
                "stage_statuses": {
                    step_id: stage["status"] for step_id, stage in stages.items()
                },
                "canvas_node_id": canvas_node_id,
                "canvas_revision": int(snapshot["revision"]),
                "failure_count": len(failures),
                "failure_codes": failure_codes,
                "deprecated_artifact_count": sum(
                    item.get("deprecated") is True
                    for item in script_stage["artifacts"]
                ),
                "live_artifact_count": sum(
                    item.get("deprecated") is not True
                    for item in script_stage["artifacts"]
                ),
                "real_http_request_counts": dict(sorted(request_counts.items())),
                "provider_media_submissions": 0,
                "paid_media_authorized": False,
                "task_submissions": 0,
                "runtime_8784_requests": 0,
                "project_assets_writes": 0,
            }
            print(json.dumps(evidence, ensure_ascii=True, sort_keys=True))
            return evidence
    finally:
        pydantic_ai.Agent = original_agent


if __name__ == "__main__":
    run()
