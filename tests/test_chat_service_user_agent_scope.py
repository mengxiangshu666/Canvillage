import errno
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.api.routes import chat as chat_routes
from novelvideo.api.routes.workflows import _workflow_run_event_stream
from novelvideo.chat import backend_sdk
from novelvideo.chat import service as chat_service
from novelvideo.chat.agent_events import attach_agent_event
from novelvideo.chat.store import ChatScope, chat_store
from novelvideo.workflow_runtime.service import WorkflowRuntimeService
from novelvideo.workflow_runtime import service as workflow_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_chat_visible_text_redacts_local_filesystem_paths():
    content = (
        "前端目录 ~/Works/supertale-fe，"
        "后端目录 /Users/tao/Works/SuperTale/state/admin/.hermes。"
    )

    redacted = chat_service._redact_local_filesystem_paths(content)

    assert "~/Works/supertale-fe" not in redacted
    assert "/Users/tao/Works/SuperTale" not in redacted
    assert redacted.count("[本地路径]") == 2


def test_canvas_patch_ws_frame_preserves_authoritative_revision_contract():
    frame = chat_routes._canvas_patch_ws_frame(
        {
            "schema": "canvas_chat_commands.v1",
            "project_id": "project-a",
            "canvas_id": "default",
            "command_id": "cmd-001",
            "revision": 7,
            "snapshot_required": True,
            "ui_reconcile_required": True,
            "server_applied": True,
            "applied_ops": 1,
            "created_node_ids": [],
            "affected_node_ids": ["node-existing"],
            "action_dispatch": {
                "route": {"lane": "canvas", "reason_code": "existing_node_mutation"},
                "decision": {
                    "target_strategy": "reuse_existing",
                    "target_node_ids": ["node-existing"],
                },
            },
            "structure_status": "server_applied_unverified",
            "commands": [{"type": "annotate", "text": "检查连续性"}],
        },
        scope=ChatScope(kind="project", id="project-a"),
        turn_id="turn-1",
    )

    assert frame is not None
    assert frame["type"] == "canvas.patch"
    assert frame["project_id"] == "project-a"
    assert frame["canvas_id"] == "default"
    assert frame["command_id"] == "cmd-001"
    assert frame["revision"] == 7
    assert frame["snapshot_required"] is True
    assert frame["ui_reconcile_required"] is True
    assert frame["applied_ops"] == 1
    assert frame["created_node_ids"] == []
    assert frame["affected_node_ids"] == ["node-existing"]
    assert frame["action_dispatch"]["route"]["reason_code"] == "existing_node_mutation"


def test_terminal_tool_event_restores_cached_call_input_without_overwriting_result():
    result = {"content": "\x00json:{\"ok\":true}"}
    merged = chat_service._terminal_tool_event_with_call_input(
        {"status": "completed", "result": result},
        {
            "project_id": "project-a",
            "canvas_id": "canvas-a",
            "command_id": "command-a",
            "commands": [{"type": "update_node_prompt", "node_id": "node-a"}],
        },
    )

    assert merged["result"] is result
    assert merged["rawInput"]["command_id"] == "command-a"


def test_terminal_tool_event_preserves_terminal_input_when_present():
    merged = chat_service._terminal_tool_event_with_call_input(
        {"status": "completed", "rawInput": {"command_id": "terminal-command"}},
        {"command_id": "cached-command"},
    )

    assert merged["rawInput"] == {"command_id": "terminal-command"}


def test_terminal_tool_event_does_not_mistake_update_input_for_call_arguments():
    merged = chat_service._terminal_tool_event_with_call_input(
        {
            "status": "completed",
            "input": {"kind": "tool_result_wrapper"},
            "result": {"content": "done"},
        },
        {"command_id": "cached-command", "canvas_id": "canvas-a"},
    )

    assert merged["input"] == {"kind": "tool_result_wrapper"}
    assert merged["rawInput"] == {
        "command_id": "cached-command",
        "canvas_id": "canvas-a",
    }


@pytest.mark.parametrize(
    "raw_input",
    [
        {"command_id": "command-a", "canvas_id": "canvas-a"},
        '\x00json:{"command_id":"command-a","canvas_id":"canvas-a"}',
    ],
)
def test_tool_call_input_payload_reads_acp_raw_input_without_display_filter(raw_input):
    assert chat_service._tool_call_input_payload({"rawInput": raw_input}) == {
        "command_id": "command-a",
        "canvas_id": "canvas-a",
    }


def test_dispatch_terminal_outcome_extracts_canvas_business_failure_from_raw_output():
    outcome = chat_service._dispatch_terminal_outcome(
        {
            "sessionUpdate": "tool_call_update",
            "status": "completed",
            "rawOutput": {
                "schema": "canvas_chat_commands.v1",
                "command_id": "command-a",
                "commands": [
                    {"type": "create_image_prompt_node"},
                    {"type": "connect_nodes"},
                ],
                "server_applied": False,
                "applied_ops": 0,
                "error_code": "FZ_SERVER_APPLY_FAILED",
                "server_apply_error": "target node was not resolved",
                "action_dispatch": {
                    "schema": "canvas_action_dispatch.v1",
                    "route": {
                        "lane": "canvas",
                        "reason_code": "direct_canvas_commands",
                    },
                },
            },
        }
    )

    assert outcome["success"] is False
    assert outcome["error_code"] == "FZ_SERVER_APPLY_FAILED"
    assert outcome["error"] == "target node was not resolved"
    assert outcome["result"]["server_applied"] is False
    assert "commands" not in outcome["result"]
    assert outcome["action_dispatch"]["route"]["lane"] == "canvas"


def test_dispatch_terminal_outcome_extracts_nested_plain_tool_error():
    outcome = chat_service._dispatch_terminal_outcome(
        {
            "sessionUpdate": "tool_call_update",
            "status": "completed",
            "rawInput": {
                "command_id": "command-a",
                "commands": [{"type": "update_node_prompt", "node_id": "node-a"}],
            },
            "rawOutput": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "error_code": "FZ_TOOL_ERROR",
                                "error": "update_node_prompt requires prompt",
                            }
                        ),
                    }
                ]
            },
        }
    )

    assert outcome["success"] is False
    assert outcome["error_code"] == "FZ_TOOL_ERROR"
    assert outcome["error"] == "update_node_prompt requires prompt"


@pytest.mark.anyio
async def test_canvas_bridge_event_is_flattened_for_chat_event_router(monkeypatch):
    emitted: list[dict] = []

    async def on_event(event):
        emitted.append(event)

    envelope = {
        "schema": "canvas_chat_commands.v1",
        "project_id": "project-a",
        "canvas_id": "canvas-a",
        "command_id": "command-a",
        "commands": [{"type": "update_node_prompt", "node_id": "node-a"}],
        "server_applied": True,
        "revision": 4,
        "applied_ops": 1,
    }
    await chat_service._emit_chat_event_best_effort(
        on_event,
        {**envelope, "type": "canvas_patch"},
    )

    assert emitted[0]["schema"] == "canvas_chat_commands.v1"
    assert "envelope" not in emitted[0]


def test_canvas_receipt_correlation_keeps_complete_envelope_for_tool_result():
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "command_id": "command-a",
        "revision": 4,
        "server_applied": True,
        "applied_ops": 1,
    }
    correlation = {
        "command_id": envelope["command_id"],
        "revision": envelope["revision"],
        "canvas_receipt": dict(envelope),
    }

    assert correlation["canvas_receipt"]["server_applied"] is True
    assert correlation["canvas_receipt"]["applied_ops"] == 1


def test_tool_lifecycle_frames_emit_one_call_and_truthful_terminal_result():
    call = chat_routes._tool_lifecycle_ws_frame(
        {
            "type": "tool_update",
            "name": "village_canvas_dispatch_action",
            "text": "→ village_canvas_dispatch_action",
            "tool_event_kind": "tool_call",
            "tool_terminal": False,
            "tool_failed": False,
            "tool_call_id": "call-1",
            "input": {"task": {"operation": "workflow_start"}},
        },
        turn_id="turn-1",
    )
    progress = chat_routes._tool_lifecycle_ws_frame(
        {
            "type": "tool_update",
            "name": "village_canvas_dispatch_action",
            "text": "working",
            "tool_event_kind": "tool_call_update",
            "tool_terminal": False,
            "tool_failed": False,
            "tool_call_id": "call-1",
        },
        turn_id="turn-1",
    )
    result = chat_routes._tool_lifecycle_ws_frame(
        {
            "type": "tool_update",
            "name": "village_canvas_dispatch_action",
            "text": "dispatch failed",
            "tool_event_kind": "tool_call_update",
            "tool_terminal": True,
            "tool_failed": True,
            "tool_call_id": "call-1",
            "workflow_run_id": "workflow-run-1",
            "command_id": "command-1",
            "revision": 8,
        },
        turn_id="turn-1",
    )

    assert call is not None and call["type"] == "tool.call"
    assert call["input"] == {"task": {"operation": "workflow_start"}}
    assert progress is None
    assert result is not None and result["type"] == "tool.result"
    assert result["success"] is False
    assert result["error"] == "dispatch failed"
    assert result["workflow_run_id"] == "workflow-run-1"
    assert result["command_id"] == "command-1"
    assert result["revision"] == 8
    assert chat_routes._tool_frame_key(call) != chat_routes._tool_frame_key(result)


def test_tool_lifecycle_frame_prefers_structured_dispatch_business_failure():
    result = chat_routes._tool_lifecycle_ws_frame(
        {
            "type": "tool_update",
            "name": "village_canvas_dispatch_action",
            "text": "completed",
            "tool_event_kind": "tool_call_update",
            "tool_terminal": True,
            "tool_failed": False,
            "tool_success": False,
            "tool_call_id": "call-2",
            "error_code": "FZ_SERVER_APPLY_FAILED",
            "tool_error": "target node was not resolved",
            "tool_result": {
                "success": False,
                "server_applied": False,
                "applied_ops": 0,
                "error_code": "FZ_SERVER_APPLY_FAILED",
                "error": "target node was not resolved",
            },
            "action_dispatch": {"route": {"lane": "canvas"}},
        },
        turn_id="turn-2",
    )

    assert result is not None
    assert result["success"] is False
    assert result["error_code"] == "FZ_SERVER_APPLY_FAILED"
    assert result["error"] == "target node was not resolved"
    assert result["result"]["server_applied"] is False
    assert result["result"]["text"] == "completed"
    assert result["action_dispatch"]["route"]["lane"] == "canvas"


def test_canvas_patch_ws_frame_rejects_cross_project_event():
    assert (
        chat_routes._canvas_patch_ws_frame(
            {
                "schema": "canvas_chat_commands.v1",
                "project_id": "project-b",
                "canvas_id": "default",
                "command_id": "cmd-001",
                "commands": [],
            },
            scope=ChatScope(kind="project", id="project-a"),
            turn_id="turn-1",
        )
        is None
    )


def test_chat_progress_ws_frame_is_structured_and_scoped():
    frame = chat_routes._chat_progress_ws_frame(
        {
            "stage": "tool.waiting",
            "message": "村长工作流正在等待工具返回…",
            "tool_name": "freezone_emit_canvas_command",
            "elapsed_seconds": 12.5,
        },
        scope=ChatScope(kind="project", id="project-a"),
        turn_id="turn-1",
    )

    assert frame == {
        "type": "chat.progress",
        "turn_id": "turn-1",
        "scope": {"kind": "project", "id": "project-a", "canvas_id": "default"},
        "stage": "tool.waiting",
        "message": "村长工作流正在等待工具返回…",
        "tool_name": "freezone_emit_canvas_command",
        "elapsed_seconds": 12.5,
    }


def test_chat_progress_ws_frame_preserves_heartbeat_health_metadata():
    frame = chat_routes._chat_progress_ws_frame(
        {
            "stage": "tool.long_running",
            "message": "工具仍在运行",
            "tool_name": "freezone_emit_canvas_command",
            "elapsed_seconds": 601.0,
            "heartbeat": True,
            "worker_alive": True,
            "last_progress_age_seconds": 300.0,
            "last_event": "session/update",
            "budget_due": "turn",
        },
        scope=ChatScope(kind="project", id="project-a"),
        turn_id="turn-1",
    )

    assert frame["heartbeat"] is True
    assert frame["worker_alive"] is True
    assert frame["last_progress_age_seconds"] == 300.0
    assert frame["last_event"] == "session/update"
    assert frame["budget_due"] == "turn"


def test_village_realtime_events_survive_service_bridge():
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "project_id": "project-a",
        "canvas_id": "default",
        "command_id": "cmd-001",
        "revision": 7,
        "snapshot_required": True,
        "commands": [],
    }
    patch_event = backend_sdk.ChatBackendEvent(type="canvas_patch", raw=envelope)
    progress_event = backend_sdk.ChatBackendEvent(
        type="progress",
        text="正在等待工具",
        name="freezone_emit_canvas_command",
        raw={"stage": "tool.waiting", "elapsed_seconds": 12.5},
    )

    assert chat_service._village_realtime_event(patch_event) == {
        **envelope,
        "type": "canvas_patch",
    }
    assert chat_service._village_realtime_event(progress_event) == {
        "type": "progress",
        "stage": "tool.waiting",
        "message": "正在等待工具",
        "tool_name": "freezone_emit_canvas_command",
        "elapsed_seconds": 12.5,
    }


def test_legacy_dispatch_guard_progress_is_replaced_by_workflow_message():
    progress_event = backend_sdk.ChatBackendEvent(
        type="progress",
        text="统一调度已经返回正式执行路径，本轮不再启动第二条工具链。",
        name="village_canvas_apply_commands",
        raw={
            "stage": "workflow.completed",
            "workflow": {"status": "completed"},
        },
    )

    realtime = chat_service._village_realtime_event(progress_event)

    assert realtime is not None
    assert realtime["message"] == "村长工作流已完成本轮回执核验。"
    assert "统一调度" not in realtime["message"]
    assert "第二条工具链" not in realtime["message"]


def test_workflow_start_tool_receipt_exposes_only_safe_run_fields():
    raw_run = {
        "id": "wfr-1",
        "workflow_id": "one-click-film",
        "workflow_version": 1,
        "project_id": "project-a",
        "canvas_id": "canvas-a",
        "run_mode": "draft",
        "status": "running",
        "contract_version": 2,
        "current_frontier": ["understand"],
        "step_states": {"understand": {"status": "running"}},
        "inputs": {
            "request": "做一个水果短片",
            "starter_workflow_id": "story-continuity-film",
            "api_key": "secret-key",
        },
        "artifacts": {},
        "error": "",
        "revision": 0,
        "event_seq": 0,
        "idempotency_key": "start-1",
        "created_at": "2026-08-16T00:00:00Z",
        "updated_at": "2026-08-16T00:00:00Z",
        "headers": {"Authorization": "Bearer secret"},
        "api_key": "secret-key",
    }
    extracted = chat_service._workflow_run_from_start_tool_update(
        event_name="village_canvas_start_workflow_run",
        raw_event={
            "sessionUpdate": "tool_call_update",
            "status": "completed",
            "content": [
                {
                    "type": "content",
                    "content": {
                        "type": "text",
                        "text": json.dumps(
                            {"ok": True, "data": raw_run}, ensure_ascii=False
                        ),
                    },
                }
            ],
        },
    )

    assert extracted is not None
    assert extracted["id"] == "wfr-1"
    assert extracted["inputs"] == {
        "request": "做一个水果短片",
        "starter_workflow_id": "story-continuity-film",
    }
    assert extracted["artifacts"] == {}
    assert "headers" not in extracted
    assert "api_key" not in extracted
    assert "secret" not in json.dumps(extracted)
    dispatched = chat_service._workflow_run_from_start_tool_update(
        event_name="village_canvas_dispatch_action",
        raw_event={
            "status": "completed",
            "rawOutput": "\x00json:"
            + json.dumps({"ok": True, "data": raw_run}, ensure_ascii=False),
        },
    )
    assert dispatched is not None
    assert dispatched["id"] == "wfr-1"
    assert chat_service._workflow_run_from_start_tool_update(
        event_name="freezone_emit_canvas_command",
        raw_event={"status": "completed", "result": raw_run},
    ) is None

    capability = chat_service._workflow_run_from_start_tool_update(
        event_name="village_canvas_capability",
        raw_event={
            "status": "completed",
            "result": {"ok": True, "data": raw_run},
        },
    )
    assert capability is not None
    assert capability["id"] == "wfr-1"


@pytest.mark.asyncio
async def test_dispatch_recovers_workflow_receipt_from_durable_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from novelvideo.workflow_runtime.store import WorkflowRunStore

    async def fake_list(self, *, project_id: str, canvas_id: str, limit: int):
        assert self.state_dir == tmp_path
        assert (project_id, canvas_id, limit) == ("project-a", "canvas-a", 5)
        return [
            {
                "id": "wfr-store-1",
                "workflow_id": "storyboard-production",
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "source_turn_id": "backend-turn-1",
                "status": "running",
                "revision": 3,
                "inputs": {"request": "两镜草稿", "api_key": "secret"},
            }
        ]

    monkeypatch.setattr(WorkflowRunStore, "list", fake_list)

    recovered = await chat_service._workflow_run_from_dispatch_store(
        project_state_dir=tmp_path,
        project_id="project-a",
        canvas_id="canvas-a",
        source_turn_id="backend-turn-1",
    )

    assert recovered is not None
    assert recovered["id"] == "wfr-store-1"
    assert recovered["inputs"] == {"request": "两镜草稿"}

    preferred = await chat_service._workflow_run_from_dispatch_store(
        project_state_dir=tmp_path,
        project_id="project-a",
        canvas_id="canvas-a",
        source_turn_id="different-turn",
        preferred_run_id="wfr-store-1",
    )
    assert preferred is not None
    assert preferred["id"] == "wfr-store-1"


@pytest.mark.asyncio
async def test_dispatch_recovers_workflow_receipt_from_scoped_turn_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from novelvideo.workflow_runtime.store import WorkflowRunStore

    async def fake_list(self, *, project_id: str, canvas_id: str, limit: int):
        assert self.state_dir == tmp_path
        assert (project_id, canvas_id, limit) == ("project-a", "canvas-a", 5)
        return [
            {
                "id": "wfr-store-scoped",
                "workflow_id": "freezone-final-film",
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "source_turn_id": (
                    "backend-turn-1:backend-turn-1:cef4664e"
                ),
                "status": "running",
                "revision": 3,
                "inputs": {"request": "脚本直达成片"},
            }
        ]

    monkeypatch.setattr(WorkflowRunStore, "list", fake_list)

    recovered = await chat_service._workflow_run_from_dispatch_store(
        project_state_dir=tmp_path,
        project_id="project-a",
        canvas_id="canvas-a",
        source_turn_id="different-turn",
        source_thread_id="backend-turn-1",
    )

    assert recovered is not None
    assert recovered["id"] == "wfr-store-scoped"

    missing = await chat_service._workflow_run_from_dispatch_store(
        project_state_dir=tmp_path,
        project_id="project-a",
        canvas_id="canvas-a",
        source_turn_id="different-turn",
    )
    assert missing is None


def test_workflow_run_delivery_key_deduplicates_only_same_snapshot():
    first = {"id": "wfr-1", "revision": 0, "event_seq": 0}
    same = {**first, "status": "running"}
    advanced = {**first, "revision": 1, "event_seq": 2}

    assert chat_service._workflow_run_delivery_key(first) == (
        "wfr-1",
        0,
        0,
    )
    assert chat_service._workflow_run_delivery_key(first) == (
        chat_service._workflow_run_delivery_key(same)
    )
    assert chat_service._workflow_run_delivery_key(first) != (
        chat_service._workflow_run_delivery_key(advanced)
    )


def test_extracts_existing_workflow_run_id_from_nested_dispatch_input():
    assert chat_service._workflow_run_id_from_tool_update(
        {
            "rawInput": {
                "task": {"existing_run_id": "wfr_existing_12345678"},
                "request": "只重试失败质量步骤",
            }
        }
    ) == "wfr_existing_12345678"
    assert chat_service._workflow_run_id_from_tool_update(
        {"rawInput": {"task": {"existing_run_id": "not-a-run"}}}
    ) == ""


def test_workflow_run_ws_frame_rejects_cross_canvas_and_project():
    run = {
        "id": "wfr-1",
        "workflow_id": "one-click-film",
        "project_id": "project-a",
        "canvas_id": "canvas-a",
    }
    scope = ChatScope(kind="project", id="project-a", canvas_id="canvas-a")
    assert chat_routes._workflow_run_ws_frame(
        {"run": run}, scope=scope, turn_id="turn-1"
    ) == {
        "type": "workflow.run",
        "turn_id": "turn-1",
        "scope": scope.to_dict(),
        "run": run,
    }
    assert chat_routes._workflow_run_ws_frame(
        {"run": {**run, "canvas_id": "canvas-b"}},
        scope=scope,
        turn_id="turn-1",
    ) is None
    assert chat_routes._workflow_run_ws_frame(
        {"run": {**run, "project_id": "project-b"}},
        scope=scope,
        turn_id="turn-1",
    ) is None


def test_canvas_patch_ws_frame_rejects_cross_canvas():
    scope = ChatScope(kind="project", id="project-a", canvas_id="canvas-a")
    event = {
        "schema": "canvas_chat_commands.v1",
        "project_id": "project-a",
        "canvas_id": "canvas-b",
        "command_id": "command-1",
        "revision": 1,
        "commands": [],
    }

    assert chat_routes._canvas_patch_ws_frame(
        event,
        scope=scope,
        turn_id="turn-1",
    ) is None


@pytest.mark.anyio
async def test_agent_workflow_receipt_bridges_to_replayable_canvas_sse(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        workflow_service,
        "build_model_plan_snapshot",
        lambda _bindings=None: {
            "schema": "canvas_model_plan_snapshot.v1",
            "model_plan_revision": "direct-model-plan.v1",
            "bindings": {
                role: {
                    "role": role,
                    "kind": kind,
                    "registry_id": f"{kind}-test",
                    "catalog_id": f"direct/{kind}-test",
                    "upstream_model": f"{kind}-test",
                    "protocol": "openai-compatible",
                    "endpoint_fingerprint": f"endpoint-{kind}",
                    "capability_revision": "direct-model-contract.v2",
                    "capabilities": {"runtime_ready": True},
                }
                for role, kind in (("director", "agent"), ("image", "image"))
            },
            "missing_roles": ["vision", "video", "audio", "embedding"],
            "fallback_policy": "explicit-only",
        },
    )
    service = WorkflowRuntimeService(tmp_path, project_id="project-a")
    run, reused = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-a",
        run_mode="draft",
        inputs={
            "request": "做一个两镜水果短片",
            "director_clarification_answers": {
                "creative_subject": "水果角色完成两个连续镜头动作",
                "audience_or_use": "内部样片",
                "visual_style": "写实电影感",
                "aspect_ratio": "16:9",
                "characters_and_reference_assets": "使用水果角色参考图",
                "audio": "无对白，保留环境声",
            },
        },
        idempotency_key="agent-e2e-start",
        contract_version=2,
        source_turn_id="turn-e2e",
    )
    assert reused is False

    extracted = chat_service._workflow_run_from_start_tool_update(
        event_name="village_canvas_start_workflow_run",
        raw_event={
            "status": "completed",
            "result": {"ok": True, "data": run},
        },
    )
    assert extracted is not None
    frame = chat_routes._workflow_run_ws_frame(
        {"run": extracted},
        scope=ChatScope(kind="project", id="project-a", canvas_id="canvas-a"),
        turn_id="turn-e2e",
    )
    assert frame is not None
    assert frame["type"] == "workflow.run"
    assert frame["run"]["id"] == run["id"]
    assert frame["run"]["artifacts"] == {}

    updated, applied = await service.store.record_event(
        run["id"],
        event_id="canvas-receipt-e2e",
        event_type="canvas_applied",
        step_id="canvas_structure",
        success=True,
        payload={"command_id": "command-e2e", "canvas_revision": 7},
        expected_revision=0,
        source="canvas_gateway",
    )
    assert applied is True
    assert updated is not None

    terminal, cancelled = await service.command(
        run["id"],
        command="cancel",
        idempotency_key="cancel-e2e",
        expected_revision=updated["revision"],
    )
    assert cancelled is True
    assert terminal is not None

    async def is_disconnected() -> bool:
        return False

    payload = "".join([
        chunk
        async for chunk in _workflow_run_event_stream(
            request=SimpleNamespace(is_disconnected=is_disconnected),
            store=service.store,
            run_id=run["id"],
            after_seq=0,
        )
    ])
    assert "event: workflow.event" in payload
    assert '"canvas_revision":7' in payload
    assert '"command_id":"command-e2e"' in payload
    assert "event: workflow.snapshot" in payload
    assert "event: workflow.terminal" in payload


@pytest.mark.anyio
@pytest.mark.parametrize(
    "event_name",
    [
        "freezone_emit_canvas_command",
        "village_canvas_apply_commands",
        "village_canvas_dispatch_action",
    ],
)
async def test_emit_canvas_tool_update_creates_one_authoritative_patch(
    monkeypatch, event_name
):
    seen = {"url": "", "token": ""}

    async def create_token(username, project, *, agent_kind):
        assert (username, project, agent_kind) == ("alice", "project-a", "canvas-bridge")
        return "bridge-token"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return (
                b'{"ok":true,"data":{"revision":9,"nodes":[{},{}],"edges":[{}],'
                b'"metadata":{"village_canvas_command_receipts_v2":{"cmd-1":{'
                b'"created_node_ids":["node-1"],"affected_node_ids":["node-1"],'
                b'"applied_ops":1}}}}}'
            )

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["token"] = request.get_header("Authorization")
        assert timeout == 8
        return Response()

    monkeypatch.setenv("DRAMACLAW_API_URL", "http://127.0.0.1:8781")
    monkeypatch.setattr(chat_service, "_create_page_agent_session_token", create_token)
    monkeypatch.setattr(chat_service, "urlopen", fake_urlopen)

    envelope = await chat_service._canvas_patch_envelope_from_emit_tool_update(
        event_name=event_name,
        raw_event={
            "rawInput": {
                "project_id": "project-a",
                "canvas_id": "default",
                "command_id": "cmd-1",
                "commands": [{"type": "create_image_prompt_node", "prompt": "portrait"}],
            }
        },
        username="alice",
        project="project-a",
    )

    assert envelope == {
        "schema": "canvas_chat_commands.v1",
        "project_id": "project-a",
        "canvas_id": "default",
        "command_id": "cmd-1",
        "commands": [{"type": "create_image_prompt_node", "prompt": "portrait"}],
        "server_applied": True,
        "revision": 9,
        "created_node_ids": ["node-1"],
        "affected_node_ids": ["node-1"],
        "applied_ops": 1,
        "generation_started": False,
        "node_count": 2,
        "edge_count": 1,
        "snapshot_required": True,
        "ui_reconcile_required": True,
        "structure_status": "server_applied_verified",
        "handoff_level": "L1",
    }
    assert seen == {
        "url": "http://127.0.0.1:8781/api/v1/projects/project-a/freezone/canvases/default",
        "token": "Bearer bridge-token",
    }


@pytest.mark.anyio
async def test_completed_apply_result_emits_patch_without_snapshot_roundtrip(
    monkeypatch,
):
    monkeypatch.setattr(
        chat_service,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("completed receipt must not refetch"),
    )
    receipt = {
        "schema": "canvas_chat_commands.v1",
        "project_id": "project-a",
        "canvas_id": "canvas-a",
        "command_id": "command-a",
        "commands": [{"type": "annotate", "text": "导演备注"}],
        "server_applied": True,
        "revision": 12,
        "created_node_ids": ["node-a", "node-b"],
        "affected_node_ids": ["node-a", "node-b"],
        "applied_ops": 3,
        "generation_started": False,
        "node_count": 3,
        "edge_count": 2,
        "snapshot_required": False,
        "ui_reconcile_required": True,
        "structure_status": "server_applied_verified",
        "handoff_level": "L1",
    }

    envelope = await chat_service._canvas_patch_envelope_from_emit_tool_update(
        event_name="village_canvas_apply_commands",
        raw_event={
            "status": "completed",
            "result": {"content": "\x00json:" + json.dumps(receipt)},
        },
        username="alice",
        project="project-a",
    )

    assert envelope == receipt


@pytest.mark.anyio
async def test_incomplete_emit_result_waits_for_authoritative_receipt(monkeypatch):
    calls = {"count": 0}

    async def create_token(*_args, **_kwargs):
        return "bridge-token"

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return self.body

    def fake_urlopen(_request, timeout):
        assert timeout == 8
        calls["count"] += 1
        if calls["count"] == 1:
            return Response(b'{"ok":true,"data":{"revision":1,"nodes":[],"edges":[]}}')
        return Response(
            b'{"ok":true,"data":{"revision":2,"nodes":[{},{}],"edges":[{}],'
            b'"metadata":{"village_canvas_command_receipts_v2":{"cmd-race":{'
            b'"created_node_ids":["node-a","node-b"],"affected_node_ids":['
            b'"node-a","node-b"],"applied_ops":2}}}}}'
        )

    monkeypatch.setattr(chat_service, "_create_page_agent_session_token", create_token)
    monkeypatch.setattr(chat_service, "urlopen", fake_urlopen)
    envelope = await chat_service._canvas_patch_envelope_from_emit_tool_update(
        event_name="village_canvas_dispatch_action",
        raw_event={
            "rawInput": {
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "command_id": "cmd-race",
                "commands": [{"type": "annotate", "text": "角色"}],
            },
            "result": {
                "content": "\x00json:" + json.dumps(
                    {
                        "schema": "canvas_chat_commands.v1",
                        "project_id": "project-a",
                        "canvas_id": "canvas-a",
                        "command_id": "cmd-race",
                        "commands": [{"type": "annotate", "text": "角色"}],
                        "server_applied": True,
                        "revision": 1,
                        "created_node_ids": [],
                        "applied_ops": 0,
                    }
                ),
            },
        },
        username="alice",
        project="project-a",
    )

    assert calls["count"] == 2
    assert envelope is not None
    assert envelope["revision"] == 2
    assert envelope["created_node_ids"] == ["node-a", "node-b"]
    assert envelope["applied_ops"] == 2


@pytest.mark.anyio
async def test_missing_canvas_receipt_is_status_only_and_cannot_replay_commands(monkeypatch):
    async def create_token(*_args, **_kwargs):
        return "bridge-token"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"ok":true,"data":{"revision":3,"nodes":[],"edges":[]}}'

    monkeypatch.setattr(chat_service, "_create_page_agent_session_token", create_token)
    monkeypatch.setattr(chat_service, "urlopen", lambda *_args, **_kwargs: Response())

    envelope = await chat_service._canvas_patch_envelope_from_emit_tool_update(
        event_name="village_canvas_dispatch_action",
        raw_event={
            "status": "completed",
            "rawInput": {
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "command_id": "cmd-missing",
                "commands": [{"type": "annotate", "text": "must not replay"}],
            },
        },
        username="alice",
        project="project-a",
    )

    assert envelope is not None
    assert envelope["structure_status"] == "receipt_missing"
    assert envelope["server_applied"] is None
    assert envelope["commands"] == []


@pytest.mark.anyio
async def test_failed_dispatch_tool_update_does_not_emit_canvas_patch(monkeypatch):
    monkeypatch.setattr(
        chat_service,
        "_create_page_agent_session_token",
        lambda *_args, **_kwargs: pytest.fail("failed dispatch must not fetch canvas"),
    )
    monkeypatch.setattr(
        chat_service,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("failed dispatch must not fetch canvas"),
    )

    envelope = await chat_service._canvas_patch_envelope_from_emit_tool_update(
        event_name="village_canvas_dispatch_action",
        raw_event={
            "status": "completed",
            "rawInput": {
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "command_id": "cmd-failed",
                "commands": [
                    {
                        "type": "update_node_prompt",
                        "node_id": "shot-137",
                        "node_data": {"prompt": "must not be replayed"},
                    }
                ],
            },
            "rawOutput": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "error_code": "FZ_TOOL_ERROR",
                                "error": "update_node_prompt requires prompt",
                            }
                        ),
                    }
                ]
            },
        },
        username="alice",
        project="project-a",
    )

    assert envelope is None


@pytest.mark.anyio
async def test_blocked_discussion_dispatch_does_not_emit_canvas_failure(monkeypatch):
    monkeypatch.setattr(
        chat_service,
        "_create_page_agent_session_token",
        lambda *_args, **_kwargs: pytest.fail("blocked dispatch must not fetch canvas"),
    )
    monkeypatch.setattr(
        chat_service,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("blocked dispatch must not fetch canvas"),
    )
    blocked = {
        "ok": False,
        "error_code": "execution_not_authorized",
        "error": "当前交互只允许讨论或规划，不写画布、不启动工作流",
        "writes_applied": 0,
        "action_dispatch": {
            "schema": "canvas_action_dispatch.v1",
            "route": {
                "schema": "canvas_action_route.v1",
                "lane": "blocked",
                "reason_code": "execution_not_authorized",
            },
        },
    }

    envelope = await chat_service._canvas_patch_envelope_from_emit_tool_update(
        event_name="village_canvas_dispatch_action",
        raw_event={
            "status": "completed",
            "rawInput": {
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "commands": [{"type": "annotate", "text": "不应写入"}],
            },
            "result": {"content": "\x00json:" + json.dumps(blocked, ensure_ascii=False)},
        },
        username="alice",
        project="project-a",
    )

    assert envelope is None


@pytest.mark.anyio
async def test_workflow_dispatch_does_not_bridge_commands_as_canvas_patch(monkeypatch):
    monkeypatch.setattr(
        chat_service,
        "_create_page_agent_session_token",
        lambda *_args, **_kwargs: pytest.fail("workflow dispatch must not fetch canvas"),
    )
    monkeypatch.setattr(
        chat_service,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("workflow dispatch must not fetch canvas"),
    )
    workflow = {
        "ok": True,
        "action_dispatch": {
            "schema": "canvas_action_dispatch.v1",
            "route": {
                "schema": "canvas_action_route.v1",
                "lane": "workflow",
                "reason_code": "workflow_started",
            },
        },
    }

    envelope = await chat_service._canvas_patch_envelope_from_emit_tool_update(
        event_name="village_canvas_dispatch_action",
        raw_event={
            "status": "completed",
            "rawInput": {
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "command_id": "cmd-workflow",
                "commands": [{"type": "annotate", "text": "workflow"}],
            },
            "result": {"content": "\x00json:" + json.dumps(workflow)},
        },
        username="alice",
        project="project-a",
    )

    assert envelope is None


@pytest.mark.anyio
async def test_dispatch_bridge_resolves_gateway_scoped_command_receipt(monkeypatch):
    async def create_token(*_args, **_kwargs):
        return "bridge-token"

    scoped_command_id = "turn-12345678901:cmd-existing"
    snapshot = {
        "ok": True,
        "data": {
            "revision": 4,
            "nodes": [{"id": "node-existing"}],
            "edges": [],
            "metadata": {
                "village_canvas_command_receipts_v2": {
                    scoped_command_id: {
                        "success": True,
                        "server_applied": True,
                        "command_id": scoped_command_id,
                        "revision": 4,
                        "created_node_ids": [],
                        "affected_node_ids": ["node-existing"],
                        "applied_ops": 1,
                        "action_route": {
                            "lane": "canvas",
                            "reason_code": "existing_node_mutation",
                            "requires_durable_run": False,
                        },
                        "director_ledger": {
                            "target_strategy": "reuse_existing",
                            "target_node_ids": ["node-existing"],
                            "success_criteria": ["prompt updated"],
                        },
                    }
                }
            },
        },
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps(snapshot).encode()

    monkeypatch.setattr(chat_service, "_create_page_agent_session_token", create_token)
    monkeypatch.setattr(chat_service, "urlopen", lambda *_args, **_kwargs: Response())

    envelope = await chat_service._canvas_patch_envelope_from_emit_tool_update(
        event_name="village_canvas_dispatch_action",
        raw_event={
            "status": "completed",
            "rawInput": {
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "source_turn_id": "turn-12345678901-full",
                "command_id": "cmd-existing",
                "commands": [
                    {
                        "type": "update_node_prompt",
                        "node_id": "node-existing",
                        "prompt": "updated",
                    }
                ],
            },
        },
        username="alice",
        project="project-a",
    )

    assert envelope is not None
    assert envelope["server_applied"] is True
    assert envelope["command_id"] == scoped_command_id
    assert envelope["revision"] == 4
    assert envelope["applied_ops"] == 1
    assert envelope["affected_node_ids"] == ["node-existing"]
    assert envelope["action_dispatch"] == {
        "route": {
            "lane": "canvas",
            "reason_code": "existing_node_mutation",
            "requires_durable_run": False,
        },
        "decision": {
            "target_strategy": "reuse_existing",
            "target_node_ids": ["node-existing"],
            "success_criteria": ["prompt updated"],
        },
    }


def test_canvas_receipt_summary_replaces_internal_dispatch_guard():
    receipt = {
        "server_applied": True,
        "command_id": "command-12",
        "revision": 12,
        "created_node_ids": ["node-a", "node-b"],
        "applied_ops": 3,
        "generation_started": False,
        "commands": [
            {"type": "annotate", "text": "角色"},
            {"type": "annotate", "text": "场景"},
            {"type": "connect_nodes", "source": "node-a", "target": "node-b"},
        ],
    }

    summary = chat_service._canvas_receipt_summary(receipt)

    assert summary == "画布调整已真正保存。\n- 新增节点：2 个\n- 新增连线：1 条"
    assert "command_id" not in summary
    assert "revision" not in summary
    assert "applied_ops" not in summary
    assert "server_applied" not in summary
    assert "统一调度" not in summary


def test_canvas_receipt_summary_hides_unverified_write():
    assert chat_service._canvas_receipt_summary(
        {
            "server_applied": True,
            "readback_verified": False,
            "command_id": "command-unverified",
            "revision": 12,
            "applied_ops": 1,
        }
    ) == ""


def test_internal_dispatch_guard_is_not_user_visible_without_receipt():
    guard = chat_service._INTERNAL_DISPATCH_GUARD_TEXT

    replacement = chat_service._replace_internal_dispatch_guard(f"好的，{guard}", "")

    assert replacement == chat_service._DISPATCH_UNVERIFIED_USER_TEXT
    assert "统一调度" not in replacement
    assert "第二条工具链" not in replacement
    assert chat_service._DISPATCH_GUARD_USER_TEXT not in replacement


def test_internal_dispatch_guard_prefers_verified_receipt_summary():
    guard = chat_service._INTERNAL_DISPATCH_GUARD_TEXT
    receipt_summary = "画布调整已真正保存。\n- 新增节点：1 个"

    assert chat_service._replace_internal_dispatch_guard(guard, receipt_summary) == (
        receipt_summary
    )


def test_current_dispatch_guard_is_replaced_by_verified_receipt_summary():
    receipt_summary = "画布调整已真正保存。"

    assert chat_service._replace_internal_dispatch_guard(
        f"好的，{chat_service._DISPATCH_GUARD_USER_TEXT}",
        receipt_summary,
    ) == receipt_summary


def test_dispatch_guard_scrub_keeps_meaningful_text_and_ui_spec():
    ui_spec = '<ui-spec type="status">核对中</ui-spec>'
    final_text = (
        "已经读取当前画布。\n\n"
        f"{chat_service._INTERNAL_DISPATCH_GUARD_TEXT}\n\n{ui_spec}"
    )

    replacement = chat_service._replace_internal_dispatch_guard(final_text, "")

    assert "已经读取当前画布。" in replacement
    assert ui_spec in replacement
    assert chat_service._DISPATCH_UNVERIFIED_USER_TEXT in replacement
    assert chat_service._DISPATCH_GUARD_USER_TEXT not in replacement
    assert chat_service._INTERNAL_DISPATCH_GUARD_TEXT not in replacement


def test_legacy_dispatch_guard_stream_suppresses_full_and_partial_replay():
    guard = chat_service._INTERNAL_DISPATCH_GUARD_TEXT

    assert chat_service._strip_legacy_dispatch_guard_stream(f"好的，{guard}") == "好的，"
    assert chat_service._strip_legacy_dispatch_guard_stream(
        f"好的，{guard[:12]}"
    ) == "好的，"
    assert chat_service._strip_legacy_dispatch_guard_stream("正常回答") == "正常回答"
    assert chat_service._strip_legacy_dispatch_guard_stream(
        "模型能力映射已经统一"
    ) == "模型能力映射已经统一"
    assert chat_service._strip_legacy_dispatch_guard_stream(
        chat_service._DISPATCH_GUARD_USER_TEXT
    ) == ""


def test_legacy_dispatch_guard_stream_keeps_embedded_marker_text():
    guard = chat_service._INTERNAL_DISPATCH_GUARD_TEXT

    embedded = f"这是一段引用并提到{guard}，后面还有正常说明。"

    assert chat_service._strip_legacy_dispatch_guard_stream(embedded) == embedded


def test_preferred_canvas_receipt_keeps_success_when_followup_fails():
    success = {
        "schema": "canvas_chat_commands.v1",
        "command_id": "cmd-success",
        "server_applied": True,
        "revision": 4,
        "applied_ops": 1,
        "structure_status": "server_applied_verified",
    }
    failure = {
        "schema": "canvas_chat_commands.v1",
        "command_id": "cmd-repair",
        "server_applied": False,
        "revision": 4,
        "applied_ops": 0,
        "structure_status": "emit_only",
    }

    preferred = chat_service._select_preferred_canvas_receipt(success, failure)
    verification = chat_service._delivery_truth(
        write_attempted=True,
        receipt=preferred,
        workflow_run={},
        failed_receipt=failure,
    )

    assert preferred == success
    assert verification["status"] == "verified_success"
    assert verification["command_id"] == "cmd-success"
    assert verification["followup_receipt_failed"] is True


def test_delivery_truth_repairs_stale_failure_prose_after_verified_write():
    text = "本轮没有形成可验证的持久交付：\n- server_applied: `false`"
    repaired = chat_service._enforce_delivery_truth(
        text,
        verification={
            "status": "verified_success",
            "command_id": "cmd-success",
            "revision": 4,
            "applied_ops": 1,
            "followup_receipt_failed": True,
        },
    )

    assert "真正保存" in repaired
    assert "cmd-success" not in repaired
    assert "revision" not in repaired
    assert "applied_ops" not in repaired
    assert "不影响" in repaired


def test_emit_only_discussion_preserves_normal_answer_without_internal_diagnostics():
    answer = "建议先明确要调整的是提示词、节点参数，还是工作流结构。"
    verification = chat_service._delivery_truth(
        write_attempted=False,
        receipt={
            "server_applied": False,
            "structure_status": "emit_only",
            "revision": 33,
            "applied_ops": 0,
        },
        workflow_run={},
    )

    rendered = chat_service._enforce_delivery_truth(answer, verification=verification)

    assert verification["status"] == "not_applicable"
    assert rendered == answer
    for internal_field in (
        "server_applied",
        "structure_status",
        "revision",
        "applied_ops",
    ):
        assert internal_field not in rendered


def test_delivery_truth_prefers_authoritative_run_over_missing_canvas_receipt():
    verification = chat_service._delivery_truth(
        write_attempted=True,
        receipt={},
        workflow_run={
            "id": "wfr_cached_turn_12345678",
            "status": "running",
        },
        dispatch_category="receipt_missing",
    )

    assert verification["status"] == "in_progress"
    assert verification["workflow_run_id"] == "wfr_cached_turn_12345678"


def test_real_canvas_write_failure_is_user_facing_and_does_not_claim_success():
    verification = chat_service._delivery_truth(
        write_attempted=True,
        receipt={
            "server_applied": False,
            "structure_status": "emit_only",
            "revision": 33,
            "applied_ops": 0,
        },
        workflow_run={},
    )

    rendered = chat_service._enforce_delivery_truth(
        "已经完成调整并写入画布。",
        verification=verification,
    )

    assert verification["status"] == "incomplete"
    assert "没有真正写入画布" in rendered
    assert "已经完成" not in rendered
    for internal_field in (
        "server_applied",
        "structure_status",
        "revision",
        "applied_ops",
    ):
        assert internal_field not in rendered


def test_dispatch_attempt_with_unverified_receipt_is_incomplete_not_not_applicable():
    verification = chat_service._delivery_truth(
        write_attempted=True,
        receipt={
            "server_applied": False,
            "structure_status": "emit_only",
            "revision": 33,
            "applied_ops": 0,
        },
        workflow_run={},
    )

    assert verification["status"] == "incomplete"
    assert chat_service._enforce_delivery_truth(
        chat_service._DISPATCH_UNVERIFIED_USER_TEXT,
        verification=verification,
    ) == chat_service._DISPATCH_UNVERIFIED_USER_TEXT


def test_delivery_truth_preserves_batched_director_clarification():
    clarification = chat_service._director_clarification_from_dispatch_input(
        {
            "request": "给我做一个 10 秒视频",
            "goal": "给我做一个 10 秒视频",
            "run_mode": "draft",
            "task": {
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "creation_reason": "新创作尚无承载节点",
            },
        },
        fallback_request="",
        canvas_nodes=[],
    )
    text = chat_service._director_clarification_text(clarification)
    verification = chat_service._delivery_truth(
        write_attempted=True,
        receipt={},
        workflow_run={},
        clarification=clarification,
    )

    assert clarification["question_id"] == "creative_subject"
    # Every remaining gap is asked once, with its default, instead of one
    # question per turn; the subject itself must never carry an invented answer.
    assert clarification["question_count"] == len(clarification["questions"])
    assert text.startswith("1. 这支片具体要表现什么主体或事件？\n")
    assert "（默认：" in text
    assert "你定" in text
    assert "建议：" not in text
    assert verification["status"] == "awaiting_clarification"
    assert verification["clarification_question_id"] == "creative_subject"
    assert chat_service._enforce_delivery_truth(
        text, verification=verification
    ) == text


def test_director_service_admission_keeps_discussion_and_diagnostics_in_chat():
    for request in (
        "或者说应该怎么做呢？你需要进行调整。",
        "视频任务失败了，为什么会这样？",
        "视频先不要提交生成，我们聊聊方案。",
        (
            "在当前画布创建一个咖啡包装概念的图片生成节点，"
            "禁止启动图片、视频、音频或工作流任务。"
        ),
    ):
        assert chat_service._director_service_admission_candidate(
            request,
            request_payload={"execution_lane": "canvas_execute"},
        ) is False
    assert chat_service._director_service_admission_candidate(
        "给我做一个 10 秒视频。",
        request_payload={"execution_lane": "canvas_execute"},
    ) is True


@pytest.mark.anyio
async def test_adaptive_chat_preflight_does_not_open_preset_interview(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    prompt = (
        '[CANVAS_AGENT_REQUEST_V2]{"v":2,"request":"给我做一个 10 秒视频。",'
        '"execution_lane":"canvas_execute","run_mode":"draft"}'
        "[/CANVAS_AGENT_REQUEST_V2]"
    )
    first = await chat_service._director_clarification_preflight(
        username="alice",
        project="project-a",
        prompt=prompt,
        project_state_dir=None,
        conversation_id="conversation-a",
        canvas_id="canvas-a",
        turn_id="turn-1",
        adaptive=True,
    )
    assert first["clarification"]["question_id"] == "creative_subject"

    scope = ChatScope(
        kind="project",
        id="project-a",
        canvas_id="canvas-a",
        conversation_id="conversation-a",
    )
    chat_store.append_ui_event(
        "alice",
        scope,
        "turn-1",
        {
            "type": "agent.event",
            "event_id": "evt-adaptive-1",
            "agent_event": {
                "type": "director.clarification",
                "payload": {
                    "clarification": first["clarification"],
                    "director_clarification_answers": first["answers"],
                    "director_request": first["director_request"],
                    "director_brief_id": first["director_brief_id"],
                    "director_run_mode": first["director_run_mode"],
                },
            },
        },
    )

    second = await chat_service._director_clarification_preflight(
        username="alice",
        project="project-a",
        prompt="雨夜车站里两个人短暂对峙，写实电影感。",
        project_state_dir=None,
        conversation_id="conversation-a",
        canvas_id="canvas-a",
        turn_id="turn-2",
        adaptive=True,
    )

    assert second["status"] == "ready"
    assert second["clarification"]["reason"] == "adaptive_chat_brief_is_executable"
    assert set(second["answers"]) == {"creative_subject"}


@pytest.mark.anyio
async def test_adaptive_chat_preflight_asks_only_explicit_audio_blocker(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    from novelvideo.creative_execution.director_clarification import (
        assess_director_clarification,
    )
    result = assess_director_clarification(
        request="雨夜车站里两个人对峙的视频",
        goal="雨夜车站里两个人对峙的视频",
        run_mode="draft",
        director_intent_contract={"audio_required": True},
        canvas_nodes=[],
        answers={},
        task={"source": "chat_preflight"},
    )
    assert result["question_id"] == "audio"


@pytest.mark.anyio
async def test_director_preflight_persists_subject_and_advances_to_next_question(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    prompt = (
        '[CANVAS_AGENT_REQUEST_V2]{"v":2,"request":"给我做一个 10 秒视频。",'
        '"execution_lane":"canvas_execute","run_mode":"draft"}'
        "[/CANVAS_AGENT_REQUEST_V2]"
    )
    first = await chat_service._director_clarification_preflight(
        username="alice",
        project="project-a",
        prompt=prompt,
        project_state_dir=None,
        conversation_id="conversation-a",
        canvas_id="canvas-a",
        turn_id="turn-1",
    )

    assert first["status"] == "ask"
    assert first["clarification"]["question_id"] == "creative_subject"
    assert first["answers"] == {}
    frame = attach_agent_event(
        {
            "type": "director.clarification",
            "turn_id": "turn-1",
            "project_id": "project-a",
            "canvas_id": "canvas-a",
            "clarification": first["clarification"],
            "director_clarification_answers": first["answers"],
            "director_request": first["director_request"],
            "director_brief_id": first["director_brief_id"],
            "director_run_mode": first["director_run_mode"],
        },
        seq=1,
    )
    scope = ChatScope(
        kind="project",
        id="project-a",
        canvas_id="canvas-a",
        conversation_id="conversation-a",
    )
    chat_store.append_ui_event(
        "alice",
        scope,
        "turn-1",
        {
            "type": "agent.event",
            "event_id": frame["agent_event"]["event_id"],
            "agent_event": frame["agent_event"],
        },
    )

    second = await chat_service._director_clarification_preflight(
        username="alice",
        project="project-a",
        prompt="主体是雨夜古刹檐廊里，黑袍刀客格挡刺客突袭后反击。",
        project_state_dir=None,
        conversation_id="conversation-a",
        canvas_id="canvas-a",
        turn_id="turn-2",
    )

    assert second["status"] == "ask"
    assert second["clarification"]["question_id"] == "visual_style"
    assert second["answers"]["creative_subject"].startswith("主体是雨夜古刹")
    assert second["director_request"] == "给我做一个 10 秒视频。"
    assert second["director_brief_id"] == "turn-1"

    current = second
    remaining = [
        ("写实电影感，冷蓝雨夜。", "aspect_ratio"),
        ("16:9 横屏。", "characters_and_reference_assets"),
        ("使用现有黑袍刀客和白面锦衣刺客参考图。", "audio"),
        ("无对白，只保留雨声和兵器碰撞声。", None),
    ]
    for index, (answer, expected_question_id) in enumerate(remaining, start=2):
        frame = attach_agent_event(
            {
                "type": "director.clarification",
                "turn_id": f"turn-{index}",
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "clarification": current["clarification"],
                "director_clarification_answers": current["answers"],
                "director_request": current["director_request"],
                "director_brief_id": current["director_brief_id"],
                "director_run_mode": current["director_run_mode"],
            },
            seq=index,
        )
        chat_store.append_ui_event(
            "alice",
            scope,
            f"turn-{index}",
            {
                "type": "agent.event",
                "event_id": frame["agent_event"]["event_id"],
                "agent_event": frame["agent_event"],
            },
        )
        current = await chat_service._director_clarification_preflight(
            username="alice",
            project="project-a",
            prompt=answer,
            project_state_dir=None,
            conversation_id="conversation-a",
            canvas_id="canvas-a",
            turn_id=f"turn-{index + 1}",
        )
        if expected_question_id is None:
            assert current["status"] == "ready"
        else:
            assert current["status"] == "ask"
            assert current["clarification"]["question_id"] == expected_question_id

    assert set(current["answers"]) == {
        "creative_subject",
        "visual_style",
        "aspect_ratio",
        "characters_and_reference_assets",
        "audio",
    }
    director_context = chat_service._director_clarification_context(current)
    assert "director_clarification_state.v1" in director_context
    assert "给我做一个 10 秒视频。" in director_context
    assert "不要重复追问" in director_context


@pytest.mark.anyio
async def test_stream_director_preflight_short_circuits_model_and_emits_event(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        chat_service,
        "_chat_backend",
        lambda: pytest.fail("director preflight must run before the model backend"),
    )
    events = []

    async def on_event(event):
        events.append(event)

    result = await chat_service.stream_assistant_reply(
        "alice",
        "project-a",
        (
            '[CANVAS_AGENT_REQUEST_V2]{"v":2,"request":"给我做一个 10 秒视频。",'
            '"execution_lane":"canvas_execute","run_mode":"draft"}'
            "[/CANVAS_AGENT_REQUEST_V2]"
        ),
        on_event,
        project_state_dir=tmp_path / "project-state",
        conversation_id="conversation-a",
        canvas_id="canvas-a",
        turn_id="turn-1",
    )

    assert [event["type"] for event in events] == [
        "director_clarification",
        "assistant_delta",
        "done",
    ]
    assert events[0]["clarification"]["question_id"] == "creative_subject"
    assert result["metadata"]["delivery_verification"]["status"] == (
        "awaiting_clarification"
    )
    assert result["content"].count("？") == 1
    assert "建议：" not in result["content"]
    assert all(event["type"] != "canvas_patch" for event in events)


def test_director_clarification_recovers_from_exact_turn_agent_receipt(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    scope = chat_service.ChatScope(
        kind="project",
        id="project-a",
        canvas_id="canvas-a",
        conversation_id="conversation-a",
    )
    chat_service.chat_store.append_ui_event(
        "alice",
        scope,
        "turn-director-1",
        {
            "type": "agent.event",
            "event_id": "evt-clarification-1",
            "agent_event": {
                "schema": "village_agent_event.v1",
                "type": "director.clarification",
                "payload": {
                    "clarification": {
                        "schema": "director_clarification.v1",
                        "required": True,
                        "ready": False,
                        "question_id": "creative_subject",
                        "question": "这支片具体要表现什么主体或事件？",
                        "suggested_answer": "建议：做一支雨夜车站短片。",
                    },
                    "director_clarification_answers": {"audio": "无对白"},
                },
            },
        },
    )

    recovered = chat_service._director_clarification_from_event_store(
        username="alice",
        project="project-a",
        canvas_id="canvas-a",
        conversation_id="conversation-a",
        turn_id="turn-director-1",
    )

    assert recovered["question_id"] == "creative_subject"
    assert recovered["director_clarification_answers"] == {"audio": "无对白"}
    assert chat_service._director_clarification_from_event_store(
        username="alice",
        project="project-a",
        canvas_id="canvas-a",
        conversation_id="conversation-a",
        turn_id="turn-other",
    ) == {}


def test_chat_recovery_packet_reads_canvas_revision_and_stays_structured():
    prompt = (
        '[CANVAS_AGENT_REQUEST_V2]{"v":2,"request":"继续",'
        '"canvas":{"project_id":"project-a","canvas_id":"canvas-a",'
        '"revision":27}}[/CANVAS_AGENT_REQUEST_V2]'
    )
    canvas = chat_service._canvas_context_from_prompt(prompt, "project-a")
    packet = {
        "schema": "dramaclaw.chat_recovery.v1",
        "turn_id": "turn-1",
        "canvas": canvas,
        "pending_tool": "freezone_emit_canvas_command",
        "retry_reason": "worker_lost",
    }
    recovered = chat_service.recovery_prompt(prompt, packet)

    assert canvas == {
        "project_id": "project-a",
        "canvas_id": "canvas-a",
        "revision": 27,
    }
    assert recovered.count("[CANVAS_AGENT_REQUEST_V2]") == 1
    assert "[VILLAGE_CANVAS_RECOVERY_PACKET]" in recovered
    assert '"schema":"village_canvas.chat_recovery.v2"' in recovered
    assert '"pending_tool":"freezone_emit_canvas_command"' in recovered


def test_completion_notice_appends_without_replacing_existing_reply():
    existing = "我已经检查完前置条件，下一步会启动第 1 个任务。"
    notice = "当前任务已开始处理。请稍后让我查看当前任务进度，或在任务完成后再继续下一步。"

    merged = chat_service._completion_text_or_existing(notice, existing)

    assert merged.startswith(existing)
    assert notice in merged


def test_infer_display_tool_call_recovers_sketch_display_promise():
    inferred = chat_service._infer_display_tool_call_from_text(
        "全部显示",
        "我来为您显示全部37个beat的草图。正在为您展示第1集前12个beat的草图：",
        [],
    )

    assert inferred == ("village_canvas_get_sketches", {"episode": 1})


def test_infer_display_tool_call_uses_recent_context_for_short_reply():
    inferred = chat_service._infer_display_tool_call_from_text(
        "全部显示",
        "正在为您展示前12个。",
        ["如果您需要查看全部37个草图，我可以分页显示。"],
    )

    assert inferred == ("village_canvas_get_sketches", {"episode": 1})


def test_infer_display_tool_call_ignores_progress_status_language():
    inferred = chat_service._infer_display_tool_call_from_text(
        "进度怎样了",
        "当前进度如下：草图生成已完成，下面展示进度表。",
        ["如果您需要查看全部37个草图，我可以分页显示。"],
    )

    assert inferred is None


def test_infer_display_tool_call_requires_user_sketch_display_intent():
    inferred = chat_service._infer_display_tool_call_from_text(
        "看一下第2集草图",
        "正在为您展示第2集草图。",
        [],
    )

    assert inferred == ("village_canvas_get_sketches", {"episode": 2})


def test_infer_display_tool_call_uses_sketch_candidate_tool_for_pool_terms():
    inferred = chat_service._infer_display_tool_call_from_text(
        "看第1集 Beat 3 的草图候选池",
        "正在为您展示 Beat 3 的草图候选。",
        [],
    )

    assert inferred == ("village_canvas_get_sketch_candidates", {"episode": 1, "beat": 3})


def test_extract_display_tool_call_uses_named_tool_field():
    inferred = chat_service._extract_display_tool_call(
        {
            "sessionUpdate": "tool_call",
            "title": "tool",
            "name": "dramaclaw_get_sketches",
            "content": [
                {
                    "type": "content",
                    "content": {"type": "text", "text": '{"episode": 1}'},
                }
            ],
        }
    )

    assert inferred == ("village_canvas_get_sketches", {"episode": 1})


def test_extract_display_tool_call_ignores_result_only_update_without_name():
    assert chat_service._extract_display_tool_call(
        {"result": {"provider_task_id": "provider-video-existing"}}
    ) is None


def test_backend_api_get_default_uses_ipv4_loopback(monkeypatch):
    seen = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self):
            return b'{"ok":true}'

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        return FakeResponse()

    monkeypatch.delenv("DRAMACLAW_API_URL", raising=False)
    monkeypatch.delenv("NOVELVIDEO_API_URL", raising=False)
    monkeypatch.setenv("NOVELVIDEO_API_PORT", "8780")
    monkeypatch.setattr(chat_service, "urlopen", fake_urlopen)

    assert chat_service._backend_api_get("/api/v1/config", "token") == {"ok": True}
    assert seen["url"] == "http://127.0.0.1:8780/api/v1/config"


def test_backend_api_get_honors_legacy_compat_api_url(monkeypatch):
    seen = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self):
            return b'{"ok":true}'

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        return FakeResponse()

    monkeypatch.delenv("DRAMACLAW_API_URL", raising=False)
    monkeypatch.delenv("NOVELVIDEO_API_URL", raising=False)
    monkeypatch.setenv("VILLAGE_CANVAS_API_URL", "http://localhost:7860")
    monkeypatch.setenv("NOVELVIDEO_API_PORT", "8780")
    monkeypatch.setattr(chat_service, "urlopen", fake_urlopen)

    assert chat_service._backend_api_get("/api/v1/config", "token") == {"ok": True}
    assert seen["url"] == "http://localhost:7860/api/v1/config"


@pytest.mark.anyio
async def test_append_chat_notification_persists_project_assistant_message(monkeypatch, tmp_path):
    seen = {}

    async def fake_project_context(user, scope):
        seen["scope"] = scope
        return SimpleNamespace(output_dir=tmp_path / "out", state_dir=tmp_path / "state")

    def fake_add_assistant_message(
        username,
        project,
        content,
        media=None,
        *,
        project_dir=None,
        project_state_dir=None,
        **kwargs,
    ):
        seen.update(
            {
                "username": username,
                "project": project,
                "content": content,
                "project_dir": project_dir,
                "project_state_dir": project_state_dir,
                "canvas_id": kwargs.get("canvas_id"),
            }
        )
        return {"id": "1", "role": "assistant", "content": content}

    monkeypatch.setattr(chat_routes, "_project_context_for_scope", fake_project_context)
    monkeypatch.setattr(
        chat_routes.chat_service,
        "add_assistant_message",
        fake_add_assistant_message,
    )

    result = await chat_routes.append_chat_notification(
        chat_routes.ChatNotificationIn(
            scope=chat_routes.ChatScopePayload(
                kind="project", id="demo", canvas_id="canvas-a"
            ),
            text="  任务已完成。  ",
        ),
        user={"username": "alice"},
    )

    assert result == {
        "ok": True,
        "data": {"id": "1", "role": "assistant", "content": "任务已完成。"},
    }
    assert seen["username"] == "alice"
    assert seen["project"] == "demo"
    assert seen["content"] == "任务已完成。"
    assert seen["project_dir"] == tmp_path / "out"
    assert seen["project_state_dir"] == tmp_path / "state"
    assert seen["canvas_id"] == "canvas-a"


@pytest.mark.anyio
async def test_chat_conversation_endpoints_create_and_list_real_project_sessions(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))

    async def fake_project_context(_user, _scope):
        return SimpleNamespace(output_dir=tmp_path / "out", state_dir=tmp_path / "state")

    monkeypatch.setattr(chat_routes, "_project_context_for_scope", fake_project_context)
    user = {"username": "alice"}
    created = await chat_routes.create_chat_conversation(
        chat_routes.ChatConversationCreateIn(
            scope=chat_routes.ChatScopePayload(
                kind="project",
                id="project-a",
                canvas_id="canvas-a",
            ),
            title="打戏方案",
        ),
        user=user,
    )
    listed = await chat_routes.list_chat_conversations(
        project="project-a",
        canvas_id="canvas-a",
        user=user,
    )

    assert created["data"]["title"] == "打戏方案"
    assert created["data"]["id"] in {
        item["id"] for item in listed["data"]["conversations"]
    }
    assert "main" not in {
        item["id"] for item in listed["data"]["conversations"]
    }

    conversation_id = created["data"]["id"]
    chat_service.add_user_message(
        "alice",
        "project-a",
        "需要删除的消息",
        project_dir=tmp_path / "out",
        project_state_dir=tmp_path / "state",
        conversation_id=conversation_id,
        canvas_id="canvas-a",
        turn_id="turn-delete",
    )
    deleted = await chat_routes.delete_chat_conversation(
        conversation_id=conversation_id,
        project="project-a",
        canvas_id="canvas-a",
        user=user,
    )
    after_delete = await chat_routes.list_chat_conversations(
        project="project-a",
        canvas_id="canvas-a",
        user=user,
    )

    assert deleted["data"]["deleted"] is True
    assert deleted["data"]["message_count"] == 1
    assert conversation_id not in {
        item["id"] for item in after_delete["data"]["conversations"]
    }

    with pytest.raises(chat_routes.HTTPException) as missing:
        await chat_routes.delete_chat_conversation(
            conversation_id=conversation_id,
            project="project-a",
            canvas_id="canvas-a",
            user=user,
        )
    assert missing.value.status_code == 404


@pytest.mark.anyio
async def test_chat_conversation_delete_rejects_only_the_exact_active_session(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "store"))

    async def fake_project_context(_user, _scope):
        return SimpleNamespace(output_dir=tmp_path / "out", state_dir=tmp_path / "state")

    monkeypatch.setattr(chat_routes, "_project_context_for_scope", fake_project_context)
    user = {"username": "alice"}
    created = await chat_routes.create_chat_conversation(
        chat_routes.ChatConversationCreateIn(
            scope=chat_routes.ChatScopePayload(
                kind="project",
                id="project-a",
                canvas_id="canvas-a",
            ),
            title="执行中的会话",
        ),
        user=user,
    )
    conversation_id = created["data"]["id"]
    scope = ChatScope(
        kind="project",
        id="project-a",
        canvas_id="canvas-a",
        conversation_id=conversation_id,
    )
    chat_routes._mark_project_turn_active("alice", scope)
    try:
        with pytest.raises(chat_routes.HTTPException) as active:
            await chat_routes.delete_chat_conversation(
                conversation_id=conversation_id,
                project="project-a",
                canvas_id="canvas-a",
                user=user,
            )
    finally:
        chat_routes._unmark_project_turn_active("alice", scope)

    assert active.value.status_code == 409
    deleted = await chat_routes.delete_chat_conversation(
        conversation_id=conversation_id,
        project="project-a",
        canvas_id="canvas-a",
        user=user,
    )
    assert deleted["data"]["deleted"] is True


@pytest.mark.anyio
async def test_deterministic_stream_redacts_local_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NOVELVIDEO_OUTPUT_DIR", str(tmp_path / "output"))
    events = []

    async def on_event(event):
        events.append(event)

    message = await chat_service._stream_deterministic_assistant_reply(
        "admin",
        "project-a",
        "临时路径：~/Works/supertale-fe/src",
        on_event,
    )

    assert "~/Works/supertale-fe" not in message["content"]
    assert message["content"] == "临时路径：[本地路径]"
    assert events[0]["type"] == "assistant_delta"
    assert events[0]["text"] == "临时路径：[本地路径]"


@pytest.mark.anyio
async def test_fallback_display_does_not_use_pool_sketch_as_current_sketch(
    monkeypatch,
    tmp_path,
):
    project_dir = tmp_path / "project"
    sketch_dir = project_dir / "grids" / "ep001" / "sketch"
    sketch_dir.mkdir(parents=True)
    (sketch_dir / "beat_01_t123.png").write_bytes(b"fake")

    monkeypatch.setattr(
        chat_service,
        "_backend_api_get",
        lambda path, token: {
            "ok": True,
            "beats": [
                {
                    "beat_number": 1,
                    "sketch_url": "",
                    "frame_url": "",
                }
            ],
        },
    )

    specs = await chat_service._fallback_display_tool_ui_specs(
        "admin",
        "project-a",
        "dramaclaw_get_sketches",
        {"episode": 1},
        token="token",
        project_dir=project_dir,
    )

    assert specs == []


@pytest.mark.anyio
async def test_fallback_display_prefers_api_project_id(monkeypatch):
    seen_paths = []

    def fake_backend_api_get(path, token):
        seen_paths.append(path)
        return {
            "ok": True,
            "beats": [
                {
                    "beat_number": 1,
                    "sketch_url": "/static/projects/api-project/sketch.png?v=1",
                    "frame_url": "",
                }
            ],
        }

    monkeypatch.setattr(chat_service, "_backend_api_get", fake_backend_api_get)

    specs = await chat_service._fallback_display_tool_ui_specs(
        "local",
        "chat-scope",
        "dramaclaw_get_sketches",
        {"episode": 1, "project_id": "api-project"},
        token="token",
    )

    assert seen_paths == ["/api/v1/projects/api-project/episodes/1/beats"]
    assert len(specs) == 1
    root = specs[0]["root"]
    first_child = specs[0]["elements"][root]["children"][0]
    assert specs[0]["elements"][first_child]["props"]["src"] == "/static/projects/api-project/sketch.png?v=1"


def test_claude_sessions_are_scope_scoped(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NOVELVIDEO_OUTPUT_DIR", str(tmp_path / "output"))

    chat_service._set_claude_session_id("admin", "project-a", "claude-session-1")
    assert chat_service._get_claude_session_id("admin", "project-b") == "claude-session-1"

    chat_service._set_claude_session_id(
        "admin", "project-a", "claude-conversation-a", "conversation-a"
    )
    assert (
        chat_service._get_claude_session_id(
            "admin", "project-a", "conversation-a"
        )
        == "claude-conversation-a"
    )
    assert (
        chat_service._get_claude_session_id(
            "admin", "project-a", "conversation-b"
        )
        is None
    )

    # Updating the legacy default thread must preserve every named conversation.
    chat_service._set_claude_session_id("admin", "project-a", "claude-main")
    assert (
        chat_service._get_claude_session_id(
            "admin", "project-a", "conversation-a"
        )
        == "claude-conversation-a"
    )

    state_file = tmp_path / "state" / "admin" / "agent_sessions.json"
    assert state_file.exists()


def test_user_agent_workspace_is_not_project_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NOVELVIDEO_OUTPUT_DIR", str(tmp_path / "output"))

    chat_service.ensure_user_claude_workspace("admin", "project-a")

    workspace = chat_service._user_agent_workspace("admin")
    assert workspace == tmp_path / "state" / "admin" / ".chat_agents"
    assert (workspace / ".claude" / "settings.local.json").exists()
    assert (workspace / ".claude" / "skills").is_dir()

    project_workspace = Path(tmp_path / "output" / "admin" / "project-a")
    assert not (project_workspace / ".claude").exists()


def test_pid_is_alive_rejects_invalid_values_without_probing(monkeypatch):
    monkeypatch.setattr(
        chat_service.os,
        "kill",
        lambda *_args: pytest.fail("invalid pid must not be probed"),
    )

    assert chat_service._pid_is_alive(None) is False
    assert chat_service._pid_is_alive(0) is False
    assert chat_service._pid_is_alive(-1) is False
    assert chat_service._pid_is_alive(True) is False


def test_pid_is_alive_uses_windows_process_handle_for_live_pid(monkeypatch):
    class FakeKernel32:
        closed: list[int] = []

        @staticmethod
        def OpenProcess(access, inherit, pid):
            assert access == chat_service._PROCESS_QUERY_LIMITED_INFORMATION
            assert inherit is False
            assert pid == 321
            return 99

        @staticmethod
        def GetExitCodeProcess(handle, exit_code):
            assert handle == 99
            exit_code._obj.value = chat_service._STILL_ACTIVE
            return 1

        @classmethod
        def CloseHandle(cls, handle):
            cls.closed.append(handle)
            return 1

        @staticmethod
        def GetLastError():
            return 0

    monkeypatch.setattr(chat_service.os, "name", "nt")
    monkeypatch.setattr(
        chat_service.ctypes,
        "windll",
        SimpleNamespace(kernel32=FakeKernel32()),
        raising=False,
    )

    assert chat_service._pid_is_alive(321) is True
    assert FakeKernel32.closed == [99]


@pytest.mark.parametrize(
    ("windows_error", "expected"),
    [
        (chat_service._ERROR_ACCESS_DENIED, True),
        (87, False),  # ERROR_INVALID_PARAMETER / dead or invalid PID
        (1168, False),  # ERROR_NOT_FOUND
    ],
)
def test_pid_is_alive_handles_windows_open_process_failures(
    monkeypatch,
    windows_error,
    expected,
):
    class FakeKernel32:
        @staticmethod
        def OpenProcess(_access, _inherit, _pid):
            return 0

        @staticmethod
        def GetLastError():
            return windows_error

    monkeypatch.setattr(chat_service.os, "name", "nt")
    monkeypatch.setattr(
        chat_service.ctypes,
        "windll",
        SimpleNamespace(kernel32=FakeKernel32()),
        raising=False,
    )

    assert chat_service._pid_is_alive(321) is expected


def test_pid_is_alive_never_leaks_probe_errors(monkeypatch):
    monkeypatch.setattr(chat_service.os, "name", "posix")

    def broken_probe(_pid, _signal):
        raise OSError(errno.EINVAL, "invalid probe")

    monkeypatch.setattr(chat_service.os, "kill", broken_probe)
    assert chat_service._pid_is_alive(321) is False


def test_chat_run_lock_is_project_scoped_and_still_user_scoped(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NOVELVIDEO_OUTPUT_DIR", str(tmp_path / "output"))

    project_a_lock_id = chat_service._acquire_chat_run_lock("admin", "project-a")
    project_b_lock_id = chat_service._acquire_chat_run_lock("admin", "project-b")
    try:
        with pytest.raises(RuntimeError, match="当前用户已有 AI 对话"):
            chat_service._acquire_chat_run_lock("admin", "project-a")
    finally:
        chat_service._release_chat_run_lock("admin", "project-a", project_a_lock_id)
        chat_service._release_chat_run_lock("admin", "project-b", project_b_lock_id)

    next_lock_id = chat_service._acquire_chat_run_lock("admin", "project-b")
    chat_service._release_chat_run_lock("admin", "project-b", next_lock_id)


def test_chat_run_lock_uses_named_agent_locks_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))

    lock_path = chat_service._chat_run_lock_path("admin", "project-a")

    assert lock_path.parent == tmp_path / "state" / "admin" / "chat_agent_locks"
    assert lock_path.name.endswith(".lock")


def test_chat_run_lock_file_expires_after_ten_minutes(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    assert chat_service._CHAT_RUN_LOCK_TTL_SECONDS == 10 * 60

    lock_path = chat_service._chat_run_lock_path("admin", "project-a")
    stale_started_at = datetime.now(timezone.utc) - timedelta(seconds=10 * 60 + 1)
    lock_path.write_text(
        json.dumps(
            {
                "lock_id": "stale-lock",
                "owner_pid": os.getpid(),
                "started_at": stale_started_at.isoformat(),
            }
        ),
        encoding="utf-8",
    )

    lock_id = chat_service._acquire_chat_run_lock("admin", "project-a")
    try:
        assert lock_id != "stale-lock"
        assert lock_path.exists()
    finally:
        chat_service._release_chat_run_lock("admin", "project-a", lock_id)


def test_chat_run_lock_uses_updated_at_for_idle_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))

    lock_path = chat_service._chat_run_lock_path("admin", "project-a")
    old_started_at = datetime.now(timezone.utc) - timedelta(seconds=10 * 60 + 1)
    fresh_updated_at = datetime.now(timezone.utc)
    lock_path.write_text(
        json.dumps(
            {
                "lock_id": "active-long-run",
                "owner_pid": os.getpid(),
                "started_at": old_started_at.isoformat(),
                "updated_at": fresh_updated_at.isoformat(),
            }
        ),
        encoding="utf-8",
    )

    assert chat_service.chat_run_lock_is_active("admin", "project-a") is True
    with pytest.raises(RuntimeError, match="当前用户已有 AI 对话"):
        chat_service._acquire_chat_run_lock("admin", "project-a")


def test_chat_run_lock_still_has_max_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))

    lock_path = chat_service._chat_run_lock_path("admin", "project-a")
    too_old_started_at = datetime.now(timezone.utc) - timedelta(
        seconds=chat_service._CHAT_RUN_LOCK_MAX_SECONDS + 1
    )
    lock_path.write_text(
        json.dumps(
            {
                "lock_id": "too-old-lock",
                "owner_pid": os.getpid(),
                "started_at": too_old_started_at.isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    lock_id = chat_service._acquire_chat_run_lock("admin", "project-a")
    try:
        assert lock_id != "too-old-lock"
    finally:
        chat_service._release_chat_run_lock("admin", "project-a", lock_id)


def test_chat_run_lock_heartbeat_refreshes_updated_at(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    atomic_writes = []
    original_atomic_write = chat_service._atomic_write_chat_run_lock_file

    def spy_atomic_write(path, payload):
        atomic_writes.append((path, payload))
        original_atomic_write(path, payload)

    monkeypatch.setattr(chat_service, "_atomic_write_chat_run_lock_file", spy_atomic_write)

    lock_id = chat_service._acquire_chat_run_lock("admin", "project-a")
    lock_path = chat_service._chat_run_lock_path("admin", "project-a")
    try:
        _current_lock_id, _owner_pid, started_at, updated_at = chat_service._read_chat_run_lock_file(
            lock_path
        )
        assert started_at is not None
        assert updated_at is not None
        old_updated_at = started_at - timedelta(seconds=30)
        lock_path.write_text(
            json.dumps(
                {
                    "lock_id": lock_id,
                    "owner_pid": os.getpid(),
                    "started_at": started_at.isoformat(),
                    "updated_at": old_updated_at.isoformat(),
                }
            ),
            encoding="utf-8",
        )

        assert chat_service._heartbeat_chat_run_lock("admin", "project-a", lock_id) is True
        assert len(atomic_writes) == 1
        assert atomic_writes[0][0] == lock_path
        assert json.loads(atomic_writes[0][1])["lock_id"] == lock_id
        refreshed_lock_id, _owner_pid, refreshed_started_at, refreshed_updated_at = (
            chat_service._read_chat_run_lock_file(lock_path)
        )
        assert refreshed_lock_id == lock_id
        assert refreshed_started_at == started_at
        assert refreshed_updated_at is not None
        assert refreshed_updated_at > old_updated_at
    finally:
        chat_service._release_chat_run_lock("admin", "project-a", lock_id)


def test_chat_run_lock_treats_new_empty_lock_as_active(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    lock_path = chat_service._chat_run_lock_path("admin", "project-a")
    lock_path.write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="当前用户已有 AI 对话"):
        chat_service._acquire_chat_run_lock("admin", "project-a")

    assert lock_path.exists()


def test_chat_run_lock_removes_old_invalid_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    lock_path = chat_service._chat_run_lock_path("admin", "project-a")
    lock_path.write_text("", encoding="utf-8")
    old_mtime = (
        datetime.now(timezone.utc).timestamp()
        - chat_service._CHAT_RUN_LOCK_BIRTH_GRACE_SECONDS
        - 1
    )
    os.utime(lock_path, (old_mtime, old_mtime))

    lock_id = chat_service._acquire_chat_run_lock("admin", "project-a")
    try:
        assert lock_path.exists()
        assert chat_service._read_chat_run_lock_file(lock_path)[0] == lock_id
    finally:
        chat_service._release_chat_run_lock("admin", "project-a", lock_id)


@pytest.mark.anyio
async def test_reingest_confirmation_reply_bypasses_agent_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        chat_service,
        "_chat_backend",
        lambda: pytest.fail("reingest confirmation should not call the agent backend"),
    )
    events = []

    async def on_event(event):
        events.append(event)

    result = await chat_service.stream_assistant_reply(
        "admin",
        "project-a",
        """创建视频

[DRAMACLAW_REINGEST_CONFIRMATION]
stage: choose_overwrite
dramaclaw_project_id: project-a
filename: novel.docx
[/DRAMACLAW_REINGEST_CONFIRMATION]""",
        on_event,
    )

    assert "当前项目已有摄入内容" in result["content"]
    assert "覆盖" in result["content"]
    assert "新建项目" not in result["content"]
    assert [event["type"] for event in events] == ["assistant_delta", "done"]


@pytest.mark.anyio
async def test_reingest_final_confirmation_reply_bypasses_agent_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        chat_service,
        "_chat_backend",
        lambda: pytest.fail("reingest confirmation should not call the agent backend"),
    )

    async def on_event(event):
        pass

    result = await chat_service.stream_assistant_reply(
        "admin",
        "project-a",
        """覆盖

[DRAMACLAW_REINGEST_CONFIRMATION]
stage: confirm_clear
dramaclaw_project_id: project-a
filename: novel.docx
[/DRAMACLAW_REINGEST_CONFIRMATION]""",
        on_event,
    )

    assert "会清空/重建当前项目已有角色" in result["content"]
    assert "确定" in result["content"]
    assert "新建项目" not in result["content"]


def test_prompt_injects_json_render_contract(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    preferences = tmp_path / "state" / "admin" / "preferences.md"
    preferences.parent.mkdir(parents=True)
    preferences.write_text("每次都重复注入的旧偏好", encoding="utf-8")

    prompt = chat_service._prompt_with_user_context(
        "admin",
        "project-a",
        "查看肖像图片，用 json-render 显示",
    )

    assert "[RENDERING_CONTRACT]" in prompt
    assert "才需要调用对应的 村长无限画布 展示工具" in prompt
    assert "不要向用户解释内部渲染格式、渲染机制、工具调用过程或工具名" in prompt
    assert "不要用文字列表、文件名列表、Beat 名称列表或 URL 列表替代媒体展示" in prompt
    assert "必须调用对应展示工具" in prompt
    assert "若没有工具返回的可展示媒体，只说明当前暂无可展示媒体" in prompt
    assert "后端会自动把工具结果渲染为 json-render" not in prompt
    assert "不要手写、复制或粘贴 <ui-spec> JSON" not in prompt
    assert "village_canvas_get_character_media" in prompt
    assert "village_canvas_get_sketches" in prompt
    assert "village_canvas_get_scene_images" in prompt
    assert "village_canvas_get_episode_media" in prompt
    assert "dramaclaw_get_" not in prompt
    assert "只有在回复需要展示图片、肖像、身份图、草图、首帧、视频、音频等可视/可播放媒体时" in prompt
    assert "media_json" in prompt
    assert "不要猜测、拼接或改写静态资源路径" in prompt
    assert "禁止自行编造 /static/projects/{project_id}/..." in prompt
    assert "portrait_url" in prompt
    assert "image_url" in prompt
    assert "video_url" in prompt
    assert "不要使用 *_path" in prompt
    assert "发送前自检" in prompt
    assert "角色列表、剧集规划、项目进度、任务状态、脚本/beat 摘要、表格、长篇正文、普通结构化说明默认使用 markdown" in prompt
    assert "不要为纯文本、进度、脚本、表格、角色/剧集清单调用媒体展示工具" in prompt
    assert prompt.rstrip().endswith("查看肖像图片，用 json-render 显示")


def test_prompt_context_wrapper_is_idempotent_on_replayed_payload(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    original = "[CANVAS_AGENT_REQUEST_V1]\nUSER_REQUEST:\n检查画布\nACTIVE_SKILLS:\n- canvas"

    once = chat_service._prompt_with_user_context("admin", "project-a", original)
    twice = chat_service._prompt_with_user_context("admin", "project-a", once)
    thrice = chat_service._prompt_with_user_context("admin", "project-a", twice)

    assert thrice.count("[VILLAGE_CANVAS_USER_CONTEXT]") == 1
    assert "[DRAMACLAW_USER_CONTEXT]" not in thrice
    assert thrice.count("[RENDERING_CONTRACT]") == 1
    assert thrice.count("[CANVAS_AGENT_REQUEST_V1]") == 1
    assert thrice.endswith(original)


def test_persisted_canvas_text_extracts_human_request_without_wire_envelope():
    v2 = (
        '[CANVAS_AGENT_REQUEST_V2]'
        '{"v":2,"request":"读取当前画布","ACTIVE_SKILLS":[],"canvas":{},"pins":[]}'
        '[/CANVAS_AGENT_REQUEST_V2]'
    )
    wrapped = f"[CONTEXT: current_project=project-a]\n\n{v2}"
    v1 = """[CANVAS_AGENT_REQUEST_V1]
USER_REQUEST:
读取旧画布

ACTIVE_SKILLS:
- (none)
[/CANVAS_AGENT_REQUEST_V1]"""

    assert chat_service._human_user_text(v2) == "读取当前画布"
    assert chat_service._human_user_text(wrapped) == "读取当前画布"
    assert chat_service._human_user_text(v1) == "读取旧画布"
    assert chat_service._human_user_text("普通聊天") == "普通聊天"
    assert chat_service._canvas_context_from_prompt(
        "普通聊天", "project-a", "canvas-a"
    ) == {
        "project_id": "project-a",
        "canvas_id": "canvas-a",
        "revision": None,
    }
    assert chat_service._is_infrastructure_error_message(
        "Context length exceeded (1,163 tokens). Cannot compress further."
    )
    assert chat_service._is_infrastructure_error_message(
        "API call failed after 3 retries: HTTP 503: Service temporarily unavailable"
    )
    assert not chat_service._is_infrastructure_error_message("正常的助手回复")


def test_hermes_compact_render_contract_preserves_media_safety(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    preferences = tmp_path / "state" / "admin" / "preferences.md"
    preferences.parent.mkdir(parents=True)
    preferences.write_text("开头偏好" + ("镜头节奏要紧凑" * 300) + "结尾偏好", encoding="utf-8")

    prompt = chat_service._prompt_with_user_context(
        "admin",
        "project-a",
        "查看首帧",
        compact_contract=True,
    )

    assert "[RENDERING_CONTRACT]" in prompt
    assert "[USER_PREFERENCES]" in prompt
    assert "开头偏好" in prompt
    assert "结尾偏好" in prompt
    assert "…" in prompt
    assert "调用对应 村长无限画布 展示工具" in prompt
    assert "不输出媒体 URL" in prompt
    assert len(prompt) < 2_000


def test_project_media_uses_project_id_url_and_explicit_project_dir(tmp_path):
    project_dir = tmp_path / "output" / "admin" / "demo"
    image = project_dir / "frames" / "ep001" / "beat_01.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")

    media = chat_service._extract_media(
        "use frames/ep001/beat_01.png",
        "admin",
        "01KS_PROJECT_ID",
        project_dir=project_dir,
    )

    assert media == [
        {
            "kind": "image",
            "url": f"/static/projects/01KS_PROJECT_ID/frames/ep001/beat_01.png?v={image.stat().st_mtime_ns}",
            "path": "frames/ep001/beat_01.png",
            "label": "beat_01.png",
        }
    ]


def test_markdown_project_image_is_not_duplicated_as_media(tmp_path):
    project_dir = tmp_path / "output" / "admin" / "demo"
    image = project_dir / "frames" / "ep001" / "beat_01.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")

    media = chat_service._extract_media(
        "![frame](/static/projects/01KS_PROJECT_ID/frames/ep001/beat_01.png)",
        "admin",
        "01KS_PROJECT_ID",
        project_dir=project_dir,
    )

    assert media == []


def test_markdown_project_image_filters_normalized_media_item(tmp_path):
    project_dir = tmp_path / "output" / "admin" / "demo"
    image = project_dir / "frames" / "ep001" / "beat_01.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    url = f"/static/projects/01KS_PROJECT_ID/frames/ep001/beat_01.png?v={image.stat().st_mtime_ns}"

    media = chat_service._filter_markdown_duplicate_images(
        "![frame](/static/projects/01KS_PROJECT_ID/frames/ep001/beat_01.png)",
        [
            {
                "kind": "image",
                "url": url,
                "path": "frames/ep001/beat_01.png",
                "label": "beat_01.png",
            }
        ],
    )

    assert media == []


def test_project_chat_storage_uses_resolved_project_state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NOVELVIDEO_OUTPUT_DIR", str(tmp_path / "output"))
    project_dir = tmp_path / "output" / "admin" / "demo"
    project_state_dir = tmp_path / "managed-state" / "projects" / "01KS_PROJECT_ID"
    project_dir.mkdir(parents=True)
    project_state_dir.mkdir(parents=True)

    chat_service.add_user_message(
        "admin",
        "01KS_PROJECT_ID",
        "hello",
        project_dir=project_dir,
        project_state_dir=project_state_dir,
    )

    assert (project_state_dir / "chat.db").exists()
    assert not (tmp_path / "state" / "admin" / "01KS_PROJECT_ID").exists()
    assert not (tmp_path / "output" / "admin" / "01KS_PROJECT_ID").exists()


def test_project_chat_storage_creates_missing_resolved_state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    project_state_dir = tmp_path / "managed-state" / "missing-project"

    chat_service.add_user_message(
        "admin",
        "01KS_PROJECT_ID",
        "hello",
        project_state_dir=project_state_dir,
    )

    assert (project_state_dir / "chat.db").exists()
    assert not (tmp_path / "state" / "admin" / "01KS_PROJECT_ID").exists()


def test_project_chat_service_isolates_named_conversation_history(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    project_state_dir = tmp_path / "managed-state" / "project-a"

    chat_service.add_user_message(
        "admin",
        "project-a",
        "会话 A",
        project_state_dir=project_state_dir,
        conversation_id="conversation-a",
        turn_id="shared-turn",
    )
    chat_service.add_assistant_message(
        "admin",
        "project-a",
        "回复 A",
        project_state_dir=project_state_dir,
        conversation_id="conversation-a",
        turn_id="shared-turn",
    )
    chat_service.add_user_message(
        "admin",
        "project-a",
        "会话 B",
        project_state_dir=project_state_dir,
        conversation_id="conversation-b",
        turn_id="shared-turn",
    )
    chat_service.add_assistant_message(
        "admin",
        "project-a",
        "回复 B",
        project_state_dir=project_state_dir,
        conversation_id="conversation-b",
        turn_id="shared-turn",
    )

    first = chat_service.list_messages(
        "admin",
        "project-a",
        project_state_dir=project_state_dir,
        conversation_id="conversation-a",
    )
    second = chat_service.list_messages(
        "admin",
        "project-a",
        project_state_dir=project_state_dir,
        conversation_id="conversation-b",
    )
    assert [message["content"] for message in first] == ["会话 A", "回复 A"]
    assert [message["content"] for message in second] == ["会话 B", "回复 B"]


def test_project_chat_service_upserts_assistant_replay_per_conversation(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    project_state_dir = tmp_path / "managed-state" / "project-a"

    first = chat_service.add_assistant_message(
        "admin",
        "project-a",
        "第一版",
        project_state_dir=project_state_dir,
        conversation_id="conversation-a",
        turn_id="turn-a",
    )
    replay = chat_service.add_assistant_message(
        "admin",
        "project-a",
        "最终版",
        project_state_dir=project_state_dir,
        conversation_id="conversation-a",
        turn_id="turn-a",
    )

    messages = chat_service.list_messages(
        "admin",
        "project-a",
        project_state_dir=project_state_dir,
        conversation_id="conversation-a",
    )
    assert first["id"] == replay["id"]
    assert [(message["turn_id"], message["content"]) for message in messages] == [
        ("turn-a", "最终版")
    ]


def test_project_chat_service_isolates_same_conversation_per_canvas(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    project_state_dir = tmp_path / "managed-state" / "project-a"

    for canvas_id, suffix in (("canvas-a", "A"), ("canvas-b", "B")):
        chat_service.add_user_message(
            "admin",
            "project-a",
            f"画布 {suffix}",
            project_state_dir=project_state_dir,
            canvas_id=canvas_id,
            turn_id=f"turn-{suffix}",
        )
        chat_service.add_assistant_message(
            "admin",
            "project-a",
            f"回复 {suffix}",
            project_state_dir=project_state_dir,
            canvas_id=canvas_id,
            turn_id=f"turn-{suffix}",
        )

    first = chat_service.list_messages(
        "admin", "project-a", project_state_dir=project_state_dir, canvas_id="canvas-a"
    )
    second = chat_service.list_messages(
        "admin", "project-a", project_state_dir=project_state_dir, canvas_id="canvas-b"
    )
    assert [message["content"] for message in first] == ["画布 A", "回复 A"]
    assert [message["content"] for message in second] == ["画布 B", "回复 B"]


def test_project_history_hides_trace_messages(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NOVELVIDEO_OUTPUT_DIR", str(tmp_path / "output"))

    chat_service.add_user_message("admin", "project-a", "你好")
    chat_service.add_trace_message("admin", "project-a", "→ dramaclaw_pipeline_status\ncompleted")
    chat_service.add_assistant_message("admin", "project-a", "你好！")

    messages = chat_service.list_messages("admin", "project-a")

    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert all("dramaclaw_pipeline_status" not in message["content"] for message in messages)


def test_project_history_strips_legacy_director_question_queue(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    legacy_metadata = {
        "backend": "director-preflight",
        "director_clarification": {
            "question_id": "visual_style",
            "question": "当前问题",
            "suggested_answer": "旧建议",
            "next_questions": ["aspect_ratio", "audio"],
        },
    }
    chat_service.add_assistant_message(
        "admin",
        "project-a",
        "当前问题",
        metadata=legacy_metadata,
        turn_id="turn-director",
    )

    messages = chat_service.list_messages("admin", "project-a")

    clarification = messages[0]["metadata"]["director_clarification"]
    assert "next_questions" not in clarification
    assert "suggested_answer" not in clarification
    assert clarification["question_id"] == "visual_style"


def test_project_history_keeps_only_latest_director_question(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    for index, question in enumerate(("用途？", "风格？", "画幅？", "声音？"), 1):
        chat_service.add_assistant_message(
            "admin",
            "project-a",
            question,
            metadata={
                "backend": "director-preflight",
                "director_clarification": {
                    "question_id": f"q-{index}",
                    "question": question,
                },
            },
            turn_id=f"director-{index}",
        )

    messages = chat_service.list_messages("admin", "project-a")

    assert [message["content"] for message in messages] == ["声音？"]


def test_project_history_defaults_to_last_50_messages(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NOVELVIDEO_OUTPUT_DIR", str(tmp_path / "output"))

    for index in range(60):
        chat_service.add_assistant_message("admin", "project-a", f"message-{index:02d}")

    messages = chat_service.list_messages("admin", "project-a")

    assert len(messages) == 50
    assert messages[0]["content"] == "message-10"
    assert messages[-1]["content"] == "message-59"


def test_home_history_hides_trace_messages(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    scope = ChatScope(kind="home")

    chat_store.append_message("admin", scope, "user", "你好")
    chat_store.append_message("admin", scope, "trace", "→ dramaclaw_pipeline_status\ncompleted")
    chat_store.append_message("admin", scope, "assistant", "你好！")

    messages = chat_store.list_messages("admin", scope)

    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert all("dramaclaw_pipeline_status" not in message["content"] for message in messages)


def test_json_render_reply_normalizer_unwraps_fenced_ui_spec():
    content = """请查看：

```json-render
<ui-spec>
{
  "type": "character_showcase",
  "root": "root",
  "elements": {
    "root": {
      "type": "Stack",
      "props": {},
      "children": ["portrait"]
    },
    "portrait": {
      "type": "Image",
      "props": {"src": "/static/projects/demo/portrait.png", "alt": "肖像"},
      "children": []
    }
  }
}
</ui-spec>
```"""

    normalized = chat_service._normalize_json_render_reply(content)

    assert "```" not in normalized
    assert '<ui-spec type="character_showcase">' in normalized
    assert '"type": "Image"' in normalized


def test_json_render_reply_normalizer_repairs_missing_trailing_brace():
    content = """<ui-spec>{"type":"character_showcase","root":"root","elements":{"root":{"type":"Stack","props":{},"children":[]}}</ui-spec>"""

    normalized = chat_service._normalize_json_render_reply(content)

    assert "格式校验失败" not in normalized
    assert '"elements": {' in normalized
    assert normalized.rstrip().endswith("</ui-spec>")


def test_json_render_reply_normalizer_repairs_legacy_component_children_props():
    content = """<ui-spec>
{
  "type": "script_overview",
  "root": "root",
  "elements": {
    "root": {
      "type": "Stack",
      "props": {"row": false, "gap": 12},
      "children": ["heading", "badge", "body"]
    },
    "heading": {
      "type": "Heading",
      "props": {"level": 3, "children": "第 1 集脚本概览"},
      "children": []
    },
    "badge": {
      "type": "Badge",
      "props": {"children": "completed", "variant": "success"},
      "children": []
    },
    "body": {
      "type": "Text",
      "props": {"children": "脚本已经生成完成。", "variant": "body"},
      "children": []
    }
  }
}
</ui-spec>"""

    normalized = chat_service._normalize_json_render_reply(content)

    assert "格式校验失败" not in normalized
    assert '"direction": "column"' in normalized
    assert '"content": "第 1 集脚本概览"' in normalized
    assert '"label": "completed"' in normalized
    assert '"content": "脚本已经生成完成。"' in normalized
    assert '"children": "脚本已经生成完成。"' not in normalized


def test_json_render_reply_normalizer_blocks_invalid_ui_spec():
    content = "<ui-spec>{not json}</ui-spec>"

    normalized = chat_service._normalize_json_render_reply(content)

    assert "<ui-spec>" not in normalized
    assert "格式校验失败" in normalized


def test_json_render_reply_normalizer_accepts_media_bundle_array():
    spec_a = {
        "type": "character_showcase",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["portrait"]},
            "portrait": {
                "type": "Image",
                "props": {"src": "/static/projects/demo/portrait.png", "alt": "肖像"},
                "children": [],
            },
        },
    }
    spec_b = {
        "type": "sketch_gallery",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["sketch"]},
            "sketch": {
                "type": "Image",
                "props": {"src": "/static/projects/demo/sketch.png", "alt": "草图"},
                "children": [],
            },
        },
    }
    content = f"<ui-spec type=\"media_bundle\">{json.dumps([spec_a, spec_b])}</ui-spec>"

    normalized = chat_service._normalize_json_render_reply(content)

    assert "格式校验失败" not in normalized
    assert normalized.count("<ui-spec") == 1
    assert '<ui-spec type="media_bundle">' in normalized
    assert '"type": "character_showcase"' in normalized
    assert '"type": "sketch_gallery"' in normalized


def test_json_render_reply_normalizer_wraps_embedded_canonical_json():
    spec = {
        "type": "sketch_gallery",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["sketch"]},
            "sketch": {
                "type": "Image",
                "props": {"src": "/static/projects/demo/sketch.png", "alt": "草图"},
                "children": [],
            },
        },
    }
    content = f"已加载草图：\n\n{json.dumps(spec, ensure_ascii=False)}\n\n继续查看请告诉我。"

    normalized = chat_service._normalize_json_render_reply(content)

    assert "已加载草图" in normalized
    assert "继续查看请告诉我" in normalized
    assert '<ui-spec type="sketch_gallery">' in normalized
    assert "/static/projects/demo/sketch.png" in normalized


def test_extract_tool_ui_specs_canonicalizes_tool_payload():
    payload = {
        "content": {
            "result": {
                "ok": True,
                "ui_spec": {
                    "type": "sketch_gallery",
                    "root": "root",
                    "elements": {
                        "root": {
                            "type": "Stack",
                            "props": {"row": True},
                            "children": ["image_1"],
                        },
                        "image_1": {
                            "type": "Image",
                            "props": {
                                "src": "/static/projects/demo/scene.png?v=1",
                                "alt": "场景",
                            },
                        },
                    },
                },
            }
        }
    }

    specs = chat_service._extract_tool_ui_specs(payload)

    assert len(specs) == 1
    assert specs[0]["type"] == "sketch_gallery"
    assert specs[0]["elements"]["root"]["props"]["direction"] == "row"
    assert specs[0]["elements"]["image_1"]["children"] == []


def test_extract_tool_ui_specs_parses_json_string_tool_result():
    payload = {
        "sessionUpdate": "tool_call_update",
        "content": json.dumps(
            {
                "ok": True,
                "ui_spec": {
                    "type": "sketch_gallery",
                    "root": "root",
                    "elements": {
                        "root": {
                            "type": "Stack",
                            "props": {"direction": "column"},
                            "children": ["image_1"],
                        },
                        "image_1": {
                            "type": "Image",
                            "props": {
                                "src": "/static/projects/demo/sketch.png?v=1",
                                "alt": "草图",
                            },
                        },
                    },
                },
            },
            ensure_ascii=False,
        ),
    }

    specs = chat_service._extract_tool_ui_specs(payload)

    assert len(specs) == 1
    assert specs[0]["type"] == "sketch_gallery"
    assert specs[0]["elements"]["image_1"]["props"]["src"] == "/static/projects/demo/sketch.png?v=1"


def test_extract_tool_chat_error_from_nested_tool_result_string():
    payload = {
        "sessionUpdate": "tool_call_update",
        "status": "completed",
        "result": json.dumps(
            {
                "ok": True,
                "data": [
                    {
                        "status": "failed",
                        "error": "Content filter triggered. Finish reason: 'content_filter'",
                        "chat_error": "模型内容安全过滤拦截了本次文本生成，请调整原文后重试。",
                    }
                ],
            },
            ensure_ascii=False,
        ),
    }

    assert (
        chat_service._extract_tool_chat_error(payload)
        == "模型内容安全过滤拦截了本次文本生成，请调整原文后重试。"
    )


def test_extract_tool_chat_error_ignores_raw_provider_error_without_hint():
    payload = {
        "sessionUpdate": "tool_call_update",
        "status": "completed",
        "result": {
            "error": "Content filter triggered. Finish reason: 'content_filter'",
            "provider_response_id": "resp_123",
        },
    }

    assert chat_service._extract_tool_chat_error(payload) is None


def test_extract_tool_chat_error_maps_render_prereq_task_error():
    raw_error = (
        "Render 重生未生成可用图片（mode=1x1_2-3, beats=[1, 2, 3]）："
        "Render 模式需要草图但未找到覆盖 beat 1-1 的草图"
    )
    payload = {
        "sessionUpdate": "tool_call_update",
        "status": "completed",
        "result": {
            "status": "failed",
            "error": raw_error,
        },
    }

    chat_error = chat_service._extract_tool_chat_error(payload)

    assert chat_error is not None
    assert "Render 任务没有生成可用图片" in chat_error
    assert "剧集分镜" in chat_error
    assert raw_error in chat_error


def test_extract_tool_chat_error_maps_generic_failed_task_error():
    payload = {
        "sessionUpdate": "tool_call_update",
        "status": "completed",
        "result": {
            "status": "failed",
            "error": "上游下载失败 token=secret-token provider_response_id=resp_123",
        },
    }

    chat_error = chat_service._extract_tool_chat_error(payload)

    assert chat_error is not None
    assert chat_error.startswith("任务执行失败：")
    assert "上游下载失败" in chat_error
    assert "secret-token" not in chat_error
    assert "resp_123" not in chat_error


def test_extract_tool_chat_error_ignores_status_without_diagnostic_error():
    payload = {
        "sessionUpdate": "tool_call_update",
        "status": "completed",
        "result": {"ok": False},
    }

    assert chat_service._extract_tool_chat_error(payload) is None


def test_extract_tool_chat_error_ignores_failed_status_without_diagnostic_error():
    payload = {
        "sessionUpdate": "tool_call_update",
        "status": "completed",
        "result": {"status": "failed"},
    }

    assert chat_service._extract_tool_chat_error(payload) is None


@pytest.mark.parametrize(
    "error_code",
    ["execution_not_authorized", "director_clarification_required"],
)
def test_extract_tool_chat_error_ignores_expected_dispatch_control_state(error_code):
    payload = {
        "status": "completed",
        "result": {
            "content": json.dumps(
                {
                    "ok": False,
                    "error_code": error_code,
                    "error": "等待正常控制流",
                    "writes_applied": 0,
                },
                ensure_ascii=False,
            )
        },
    }

    assert chat_service._extract_tool_chat_error(payload) is None


def test_append_tool_ui_specs_adds_block_when_model_did_not_write_one():
    spec = {
        "type": "character_showcase",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["portrait"]},
            "portrait": {
                "type": "Image",
                "props": {"src": "/static/projects/demo/portrait.png?v=1", "alt": "肖像"},
                "children": [],
            },
        },
    }

    content = chat_service._append_tool_ui_specs("已展示肖像。", [spec])

    assert content.startswith("已展示肖像。")
    assert '<ui-spec type="character_showcase">' in content
    assert "/static/projects/demo/portrait.png?v=1" in content


def test_append_tool_ui_specs_ignores_placeholder_ui_spec_chatter():
    spec = {
        "type": "character_showcase",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["portrait"]},
            "portrait": {
                "type": "Image",
                "props": {"src": "/static/projects/demo/portrait.png?v=1", "alt": "肖像"},
                "children": [],
            },
        },
    }

    content = chat_service._append_tool_ui_specs(
        "\n".join(
            [
                "首先，调用dramaclaw_get_character_media工具获取角色肖像信息：",
                "<ui-spec> JSON has been generated and will be automatically rendered by the backend.",
                "所有图片都已按规范渲染为UI画廊，您可以直接查看。",
                "如需查看其他内容，请告诉我。",
            ]
        ),
        [spec],
    )

    assert "dramaclaw_get_character_media" not in content
    assert "automatically rendered" not in content
    assert "UI画廊" not in content
    assert "如需查看其他内容" in content
    assert '<ui-spec type="character_showcase">' in content
    assert "/static/projects/demo/portrait.png?v=1" in content


def test_append_tool_ui_specs_replaces_truncated_embedded_media_json():
    spec = {
        "type": "sketch_gallery",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["sketch"]},
            "sketch": {
                "type": "Image",
                "props": {"src": "/static/projects/demo/sketch.png", "alt": "草图"},
                "children": [],
            },
        },
    }
    truncated_json = (
        '{"type": "sketch_gallery", "root": "root", "elements": '
        '{"root": {"type": "Stack", "props": {}, "children": ["sketch"]}}'
    )

    content = chat_service._append_tool_ui_specs(
        f"已为您展示草图：\n\n{truncated_json}\n\n继续查看请告诉我。",
        [spec],
    )

    assert "已为您展示草图" in content
    assert "继续查看请告诉我" in content
    assert truncated_json not in content
    assert '<ui-spec type="sketch_gallery">' in content
    assert "/static/projects/demo/sketch.png" in content


def test_ui_spec_json_is_generated_before_wrapping_tags():
    spec = {
        "type": "character_showcase",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["portrait"]},
            "portrait": {
                "type": "Image",
                "props": {"src": "/static/projects/demo/portrait.png?v=1", "alt": "肖像"},
                "children": [],
            },
        },
    }

    spec_type, json_text = chat_service._ui_spec_json(spec)
    wrapped = chat_service._wrap_ui_spec_json(spec_type, json_text)

    assert spec_type == "character_showcase"
    assert "<ui-spec" not in json_text
    assert "</ui-spec>" not in json_text
    assert wrapped.startswith('<ui-spec type="character_showcase">')
    assert wrapped.endswith("</ui-spec>")


def test_append_tool_ui_specs_keeps_image_specs_separate_and_ordered():
    portrait_spec = {
        "type": "character_showcase",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["portrait"]},
            "portrait": {
                "type": "Image",
                "props": {
                    "src": "/static/projects/demo/portrait.png?v=1",
                    "alt": "肖像",
                    "overlayTitle": "江念",
                },
                "children": [],
            },
        },
    }
    sketch_spec = {
        "type": "sketch_gallery",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["sketch"]},
            "sketch": {
                "type": "Image",
                "props": {
                    "src": "/static/projects/demo/sketch.png?v=1",
                    "alt": "草图",
                    "overlayTitle": "Beat 1 草图",
                },
                "children": [],
            },
        },
    }

    content = chat_service._append_tool_ui_specs("已展示媒体。", [portrait_spec, sketch_spec])

    assert content.count("<ui-spec") == 2
    assert '<ui-spec type="character_showcase">' in content
    assert '<ui-spec type="sketch_gallery">' in content
    assert '"type": "character_showcase"' in content
    assert '"type": "sketch_gallery"' in content
    assert content.index('<ui-spec type="character_showcase">') < content.index(
        '<ui-spec type="sketch_gallery">'
    )
    assert content.index("/static/projects/demo/portrait.png?v=1") < content.index(
        "/static/projects/demo/sketch.png?v=1"
    )


def test_append_tool_ui_specs_merges_adjacent_character_showcase_specs():
    first_spec = {
        "type": "character_showcase",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["portrait"]},
            "portrait": {
                "type": "Image",
                "props": {
                    "src": "/static/projects/demo/jiang-nian.png?v=1",
                    "alt": "江念",
                    "overlayTitle": "江念",
                },
                "children": [],
            },
        },
    }
    second_spec = {
        "type": "character_showcase",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["portrait"]},
            "portrait": {
                "type": "Image",
                "props": {
                    "src": "/static/projects/demo/luo-xi.png?v=1",
                    "alt": "洛曦",
                    "overlayTitle": "洛曦",
                },
                "children": [],
            },
        },
    }

    content = chat_service._append_tool_ui_specs("已展示角色。", [first_spec, second_spec])

    assert content.count('<ui-spec type="character_showcase">') == 1
    assert "/static/projects/demo/jiang-nian.png?v=1" in content
    assert "/static/projects/demo/luo-xi.png?v=1" in content
    assert '"portrait_2"' in content
    assert content.index("/static/projects/demo/jiang-nian.png?v=1") < content.index(
        "/static/projects/demo/luo-xi.png?v=1"
    )


def test_append_tool_ui_specs_merges_same_category_video_and_audio_specs():
    video_a = {
        "type": "keyframe_video",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["video"]},
            "video": {
                "type": "Video",
                "props": {"src": "/static/projects/demo/beat-1.mp4", "title": "Beat 1"},
                "children": [],
            },
        },
    }
    video_b = {
        "type": "keyframe_video",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["video"]},
            "video": {
                "type": "Video",
                "props": {"src": "/static/projects/demo/beat-2.mp4", "title": "Beat 2"},
                "children": [],
            },
        },
    }
    audio_a = {
        "type": "audio_list",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["audio"]},
            "audio": {
                "type": "Audio",
                "props": {"src": "/static/projects/demo/beat-1.mp3", "title": "Beat 1"},
                "children": [],
            },
        },
    }
    audio_b = {
        "type": "audio_list",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["audio"]},
            "audio": {
                "type": "Audio",
                "props": {"src": "/static/projects/demo/beat-2.mp3", "title": "Beat 2"},
                "children": [],
            },
        },
    }

    content = chat_service._append_tool_ui_specs("已展示媒体。", [video_a, video_b, audio_a, audio_b])

    assert content.count('<ui-spec type="keyframe_video">') == 1
    assert content.count('<ui-spec type="audio_list">') == 1
    assert content.index("/static/projects/demo/beat-1.mp4") < content.index(
        "/static/projects/demo/beat-2.mp4"
    )
    assert content.index("/static/projects/demo/beat-2.mp4") < content.index(
        "/static/projects/demo/beat-1.mp3"
    )
    assert content.index("/static/projects/demo/beat-1.mp3") < content.index(
        "/static/projects/demo/beat-2.mp3"
    )


def test_append_tool_ui_specs_keeps_same_src_across_different_categories():
    shared_src = "/static/projects/demo/shared.png?v=1"
    portrait_spec = {
        "type": "character_showcase",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["portrait"]},
            "portrait": {
                "type": "Image",
                "props": {"src": shared_src, "alt": "肖像", "overlayTitle": "角色肖像"},
                "children": [],
            },
        },
    }
    sketch_spec = {
        "type": "sketch_gallery",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["sketch"]},
            "sketch": {
                "type": "Image",
                "props": {"src": shared_src, "alt": "草图", "overlayTitle": "草图候选"},
                "children": [],
            },
        },
    }

    content = chat_service._append_tool_ui_specs("已展示媒体。", [portrait_spec, sketch_spec])

    assert content.count("<ui-spec") == 2
    assert content.count(shared_src) == 2
    assert "角色肖像" in content
    assert "草图候选" in content


def test_split_ui_specs_from_text_extracts_model_written_blocks():
    spec = {
        "type": "sketch_gallery",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["image"]},
            "image": {
                "type": "Image",
                "props": {"src": "/static/projects/demo/sketch.png", "alt": "草图"},
                "children": [],
            },
        },
    }
    content = (
        "以下是草图：\n\n"
        f"<ui-spec>{json.dumps(spec, ensure_ascii=False)}</ui-spec>\n\n"
        "展示完成。"
    )

    text, specs = chat_service._split_ui_specs_from_text(content)

    assert "<ui-spec" not in text
    assert text == "以下是草图：\n\n展示完成。"
    assert len(specs) == 1
    assert specs[0]["type"] == "sketch_gallery"
    assert specs[0]["elements"]["image"]["children"] == []


def test_append_tool_ui_specs_does_not_duplicate_existing_ui_spec():
    existing_spec = {
        "type": "character_showcase",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["portrait"]},
            "portrait": {
                "type": "Image",
                "props": {"src": "/static/projects/demo/portrait.png", "alt": "肖像"},
                "children": [],
            },
        },
    }
    tool_spec = {
        "type": "sketch_gallery",
        "root": "root",
        "elements": {
            "root": {"type": "Stack", "props": {}, "children": ["sketch"]},
            "sketch": {
                "type": "Image",
                "props": {"src": "/static/projects/demo/sketch.png", "alt": "草图"},
                "children": [],
            },
        },
    }

    content = chat_service._append_tool_ui_specs(
        f"已有展示\n<ui-spec>{json.dumps(existing_spec, ensure_ascii=False)}</ui-spec>",
        [tool_spec],
    )

    assert content.count("<ui-spec") == 1
    assert "已有展示" in content
    assert "/static/projects/demo/portrait.png" in content
    assert "/static/projects/demo/sketch.png" not in content


@pytest.mark.anyio
async def test_director_preflight_delegation_fills_defaults_and_releases_the_gate(
    tmp_path,
    monkeypatch,
):
    from novelvideo.creative_execution.creative_contract import load_creative_contract

    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    state_dir = chat_service._project_state_dir("alice", "project-a")
    prompt = (
        '[CANVAS_AGENT_REQUEST_V2]{"v":2,"request":"给我做一个 10 秒视频。",'
        '"execution_lane":"canvas_execute","run_mode":"draft"}'
        "[/CANVAS_AGENT_REQUEST_V2]"
    )
    scope = ChatScope(
        kind="project",
        id="project-a",
        canvas_id="canvas-a",
        conversation_id="conversation-a",
    )

    def _remember(state: dict, turn: str, seq: int) -> None:
        frame = attach_agent_event(
            {
                "type": "director.clarification",
                "turn_id": turn,
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "clarification": state["clarification"],
                "director_clarification_answers": state["answers"],
                "director_request": state["director_request"],
                "director_brief_id": state["director_brief_id"],
                "director_run_mode": state["director_run_mode"],
            },
            seq=seq,
        )
        chat_store.append_ui_event(
            "alice",
            scope,
            turn,
            {
                "type": "agent.event",
                "event_id": frame["agent_event"]["event_id"],
                "agent_event": frame["agent_event"],
            },
        )

    first = await chat_service._director_clarification_preflight(
        username="alice",
        project="project-a",
        prompt=prompt,
        project_state_dir=state_dir,
        conversation_id="conversation-a",
        canvas_id="canvas-a",
        turn_id="turn-1",
    )
    assert first["status"] == "ask"
    _remember(first, "turn-1", 1)

    second = await chat_service._director_clarification_preflight(
        username="alice",
        project="project-a",
        prompt="主体是雨夜古刹檐廊里，黑袍刀客格挡刺客突袭后反击。",
        project_state_dir=state_dir,
        conversation_id="conversation-a",
        canvas_id="canvas-a",
        turn_id="turn-2",
    )
    assert second["status"] == "ask"
    assert second["clarification"]["question_count"] >= 3
    _remember(second, "turn-2", 2)

    # "你定" is a decision: take every offered default and stop interviewing.
    third = await chat_service._director_clarification_preflight(
        username="alice",
        project="project-a",
        prompt="你定",
        project_state_dir=state_dir,
        conversation_id="conversation-a",
        canvas_id="canvas-a",
        turn_id="turn-3",
    )

    assert third["status"] == "ready"
    assert third["answers"]["visual_style"]
    assert third["answers"]["aspect_ratio"]
    contract = load_creative_contract(state_dir)
    assert contract["fields"]["visual_style"]["source"] == "ai_default"
    assert contract["fields"]["creative_subject"]["source"] == "user"


@pytest.mark.anyio
@pytest.mark.parametrize("with_receipt", [True, False])
async def test_village_provider_failure_after_verified_canvas_receipt_finishes_turn(
    monkeypatch,
    tmp_path,
    with_receipt,
) -> None:
    from novelvideo.chat.backend_sdk import ChatBackendEvent
    from novelvideo.chat.village_harness import pool as village_pool

    receipt = {
        "schema": "canvas_chat_commands.v1",
        "project_id": "project-a",
        "canvas_id": "canvas-a",
        "command_id": "turn-provider-failure:update-shot-137",
        "revision": 7,
        "server_applied": True,
        "applied_ops": 1,
        "structure_status": "server_applied_verified",
        "commands": [
            {
                "type": "update_node_prompt",
                "node_id": "shot-137",
                "prompt": "updated",
            }
        ],
    }

    class ProviderFailingThread:
        id = "village-provider-failure"

        async def stream(self, *_args, **kwargs):
            turn_id = str(kwargs.get("turn_id") or "turn-provider-failure")
            yield ChatBackendEvent(
                type="thread_started",
                thread_id=self.id,
                turn_id=turn_id,
            )
            if with_receipt:
                yield ChatBackendEvent(
                    type="canvas_patch",
                    thread_id=self.id,
                    turn_id=turn_id,
                    raw=receipt,
                )
            raise RuntimeError(
                "status_code: 503, model_name: gemini-3.8-flash, "
                "body: {'message': 'auth_concurrency_limit'}"
            )

    async def get_for_user(*_args, **_kwargs):
        return ProviderFailingThread()

    async def knowledge_packet(*_args, **_kwargs):
        assert _kwargs["semantic_applicability"] is True
        assert _kwargs["applicability_context"]["model_id"] == "test-video-model"
        assert _kwargs["applicability_context"]["node_type"] == "videoNode"
        assert _kwargs["applicability_context"]["generation_mode"] == "imageToVideo"
        return {
            "context": "",
            "execution_context": "",
            "execution_rule_ids": [],
            "memory_ids": [],
            "applicability_context": {},
        }

    async def current_canvas_facts(*_args, **_kwargs):
        return {
            "project_id": "project-a",
            "canvas_id": "canvas-a",
            "revision": 6,
            "node_count": 1,
            "edge_count": 0,
            "focus": {"selected_node_ids": ["video-target"]},
            "nodes": [{"id": "video-target", "type": "videoNode", "model_id": "test-video-model", "generation_mode": "imageToVideo"}],
        }

    async def live_director_context(*_args, **_kwargs):
        return {}

    persisted: list[str] = []

    def add_assistant_message(*args, **_kwargs):
        content = str(args[2] if len(args) > 2 else "")
        persisted.append(content)
        return {"content": content}

    monkeypatch.setattr(village_pool, "get_for_user", get_for_user)
    monkeypatch.setattr(chat_service, "build_knowledge_packet", knowledge_packet)
    monkeypatch.setattr(chat_service, "_current_canvas_facts", current_canvas_facts)
    monkeypatch.setattr(chat_service, "_build_live_director_context", live_director_context)
    monkeypatch.setattr(
        chat_service,
        "_prompt_with_user_context",
        lambda _username, _project, prompt, **_kwargs: prompt,
    )
    monkeypatch.setattr(
        chat_service,
        "_assistant_history_contents",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        chat_service,
        "_trace_history_contents",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        chat_service,
        "_director_clarification_from_event_store",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(chat_service, "add_assistant_message", add_assistant_message)
    monkeypatch.setattr(chat_service, "_extract_media", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(chat_service, "save_checkpoint", lambda *_args, **_kwargs: None)

    events: list[dict] = []

    async def on_event(event):
        events.append(event)

    if not with_receipt:
        with pytest.raises(RuntimeError, match="auth_concurrency_limit"):
            await chat_service._stream_assistant_reply_village(
                "alice",
                "project-a",
                "优化 shot-137 的提示词",
                on_event,
                project_state_dir=tmp_path,
                canvas_id="canvas-a",
                turn_id="turn-provider-failure",
                model="test-model",
            )
        assert all(event["type"] != "done" for event in events)
        assert persisted == []
        return

    message = await chat_service._stream_assistant_reply_village(
        "alice",
        "project-a",
        "优化 shot-137 的提示词",
        on_event,
        project_state_dir=tmp_path,
        canvas_id="canvas-a",
        turn_id="turn-provider-failure",
        model="test-model",
    )

    assert "已经由服务端确认保存" in message["content"]
    assert "没有自动重试" in message["content"]
    assert events[-1]["type"] == "done"
    assert events[-1]["message"] == message
    assert persisted == [message["content"]]
