"""TapCanvas sidecar bridge: bounded tools and both remote-tool protocols."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI

from novelvideo.agent_tools.village_canvas import TOOLS
from novelvideo.api.routes import agent_sidecar
from novelvideo.chat import tapcanvas_sidecar


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(agent_sidecar.router, prefix="/api/v1")
    return app


@pytest.fixture(autouse=True)
def _clear_contexts():
    tapcanvas_sidecar.CONTEXTS.clear()
    yield
    tapcanvas_sidecar.CONTEXTS.clear()


def _register(token: str, project_id: str = "project-a") -> None:
    tapcanvas_sidecar.register_context(
        token,
        api_url="http://127.0.0.1:8784",
        agent_token="token",
        project_id=project_id,
        canvas_id="default",
    )


def test_every_exposed_tool_exists_in_the_native_registry() -> None:
    registered = {name for name, _schema, _handler in TOOLS}
    assert tapcanvas_sidecar.SIDECAR_TOOL_NAMES <= registered


def test_read_only_tools_are_declared_to_the_sidecar() -> None:
    definitions = {
        item["name"]: item for item in tapcanvas_sidecar.remote_tool_definitions()
    }

    assert (
        definitions["village_canvas_read_compact"]["execution"]["sideEffect"] == "none"
    )
    assert definitions["village_canvas_list_tasks"]["execution"]["sideEffect"] == "none"
    assert (
        definitions["village_canvas_apply_commands"]["execution"]["sideEffect"]
        == "unknown"
    )
    assert definitions["freezone_run_node"]["execution"]["sideEffect"] == "unknown"


@pytest.mark.asyncio
async def test_plain_remote_tool_success_is_a_2xx_payload(monkeypatch) -> None:
    async def fake_invoke(name, arguments, context):
        assert name == "village_canvas_read_compact"
        assert arguments == {"canvas_id": "default"}
        assert context["project_id"] == "project-a"
        return json.dumps({"ok": True, "revision": 3})

    monkeypatch.setattr(tapcanvas_sidecar, "invoke_native_tool", fake_invoke)
    _register("turn-a")

    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent-sidecar/mcp/turn-a",
            json={
                "toolName": "village_canvas_read_compact",
                "args": {"canvas_id": "default"},
                "canvasProjectId": "project-b",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert json.loads(payload["content"])["revision"] == 3


@pytest.mark.asyncio
async def test_plain_remote_tool_failure_is_non_2xx(monkeypatch) -> None:
    async def fake_invoke(name, arguments, context):
        return json.dumps({"ok": False, "error": "no such node"})

    monkeypatch.setattr(tapcanvas_sidecar, "invoke_native_tool", fake_invoke)
    _register("turn-b")

    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent-sidecar/mcp/turn-b",
            json={"toolName": "village_canvas_apply_commands", "args": {}},
        )

    assert response.status_code == 400
    assert response.json()["ok"] is False


@pytest.mark.asyncio
async def test_plain_remote_tool_cannot_override_the_bound_project(monkeypatch) -> None:
    seen: list[str] = []

    async def fake_invoke(name, arguments, context):
        seen.append(context["project_id"])
        return json.dumps({"ok": True})

    monkeypatch.setattr(tapcanvas_sidecar, "invoke_native_tool", fake_invoke)
    _register("turn-c", project_id="bound-project")

    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent-sidecar/mcp/turn-c",
            json={
                "toolName": "village_canvas_read_compact",
                "args": {"project_id": "other-project"},
                "canvasProjectId": "other-project",
            },
        )

    assert response.status_code == 200
    assert seen == ["bound-project"]


@pytest.mark.asyncio
async def test_jsonrpc_tools_list_stays_bounded() -> None:
    _register("turn-d")

    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent-sidecar/mcp/turn-d",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )

    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert names == set(tapcanvas_sidecar.SIDECAR_TOOL_NAMES)


@pytest.mark.asyncio
async def test_unknown_turn_token_is_rejected() -> None:
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent-sidecar/mcp/missing",
            json={"toolName": "village_canvas_read_compact", "args": {}},
        )

    assert response.status_code == 401


def test_chat_backend_requires_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("VILLAGE_CHAT_AGENT_BACKEND", raising=False)
    assert tapcanvas_sidecar.chat_backend_enabled() is False
    monkeypatch.setenv("VILLAGE_CHAT_AGENT_BACKEND", "xiaoshu")
    assert tapcanvas_sidecar.chat_backend_enabled() is True
    monkeypatch.setenv("VILLAGE_CHAT_AGENT_BACKEND", "tapcanvas")
    assert tapcanvas_sidecar.chat_backend_enabled() is True
    monkeypatch.setenv("VILLAGE_CHAT_AGENT_BACKEND", "village")
    assert tapcanvas_sidecar.chat_backend_enabled() is False


def test_vendor_names_are_replaced_in_user_visible_text() -> None:
    assert (
        tapcanvas_sidecar.product_text("TapCanvas Harness / DeepSeek Harness 已完成")
        == "小树 / 小树 已完成"
    )
    assert tapcanvas_sidecar.product_text("我是 TapCanvas") == "小树"
    # User-authored labels are data, not the assistant's identity.
    assert tapcanvas_sidecar.product_text("TapCanvas 隔离测试") == "TapCanvas 隔离测试"
    assert (
        tapcanvas_sidecar.display_tool_name("report_delivery")
        == "village_delivery_check"
    )
    assert (
        tapcanvas_sidecar.display_tool_name("village_canvas_read_compact")
        == "village_canvas_read_compact"
    )


def test_sidecar_read_timeout_is_disabled() -> None:
    assert tapcanvas_sidecar._SIDECAR_HTTP_TIMEOUT.read is None
    assert tapcanvas_sidecar._SIDECAR_HTTP_TIMEOUT.connect == 10.0


def test_server_task_authorization_is_parsed_and_injected() -> None:
    prompt = (
        "[CANVAS_AGENT_REQUEST_V2]"
        '{"request":"画一张","task_authorization":{"scope":"current_turn",'
        '"run_mode":"auto","allow_structure":true,"allow_paid_media":true,'
        '"max_paid_starts":1,"grant_id":"grant-1"}}'
        "[/CANVAS_AGENT_REQUEST_V2]"
    )
    authorization = tapcanvas_sidecar.task_authorization_from_prompt(prompt)
    assert authorization == {
        "scope": "current_turn",
        "run_mode": "auto",
        "allow_structure": True,
        "allow_paid_media": True,
        "max_paid_starts": 1,
        "grant_id": "grant-1",
    }
    context = {"task_authorization": authorization}
    arguments = tapcanvas_sidecar.authorized_tool_arguments(
        "freezone_run_node",
        {"task_authorization": {"allow_paid_media": False, "grant_id": "model-made"}},
        context,
    )
    assert arguments["task_authorization"] == authorization
    read_arguments = tapcanvas_sidecar.authorized_tool_arguments(
        "village_canvas_read_compact", {"canvas_id": "default"}, context
    )
    assert "task_authorization" not in read_arguments


def test_media_results_become_terminal_delivery_receipts() -> None:
    receipt = tapcanvas_sidecar.delivery_receipt_for_tool(
        "village_canvas_read_compact",
        {
            "schema": "village_canvas_compact.v1",
            "project_id": "p",
            "canvas_id": "default",
            "revision": 7,
            "nodes": [
                {
                    "id": "node-1",
                    "type": "imageGenNode",
                    "has_image": True,
                    "image_url": "/static/projects/p/freezone/_outputs/x.png",
                }
            ],
        },
        api_url="http://127.0.0.1:8784",
    )
    assert receipt is not None
    assert receipt["protocolVersion"] == "tapcanvas.workflow-execution-receipt/v1"
    assert receipt["terminal"] is True
    assert receipt["acceptedAsync"] is False
    assert receipt["workflowOutputs"][0]["ports"]["output"]["image"] == [
        "http://127.0.0.1:8784/static/projects/p/freezone/_outputs/x.png"
    ]


def test_started_media_task_is_async_not_terminal() -> None:
    receipt = tapcanvas_sidecar.delivery_receipt_for_tool(
        "freezone_run_node",
        {
            "generation_started": True,
            "job": {"task_id": "task-1", "task_type": "freezone_gen"},
        },
        api_url="http://127.0.0.1:8784",
    )
    assert receipt is not None
    assert receipt["terminal"] is False
    assert receipt["acceptedAsync"] is True
    assert receipt["executionId"] == "task:task-1"


def test_workflow_tools_are_hidden_without_intent() -> None:
    names = {
        item["name"]
        for item in tapcanvas_sidecar.remote_tool_definitions(include_workflow=False)
    }
    assert "village_canvas_start_workflow_run" not in names
    assert "village_canvas_command_workflow_run" not in names
    assert "village_canvas_read_compact" in names
    assert "village_canvas_start_workflow_run" in {
        item["name"] for item in tapcanvas_sidecar.remote_tool_definitions()
    }


def test_workflow_scaffolding_is_blocked_for_plain_asset_requests() -> None:
    context = {"workflow_authorized": False}
    with pytest.raises(ValueError, match="没有要求工作流"):
        tapcanvas_sidecar.authorized_tool_arguments(
            "village_canvas_start_workflow_run",
            {"workflow_id": "custom-canvas-workflow"},
            context,
        )
    with pytest.raises(ValueError, match="没有要求工作流"):
        tapcanvas_sidecar.authorized_tool_arguments(
            "village_canvas_dispatch_action",
            {"commands": [{"type": "create_shot_sequence"}]},
            context,
        )
    with pytest.raises(ValueError, match="没有要求工作流"):
        tapcanvas_sidecar.authorized_tool_arguments(
            "village_canvas_command_workflow_run",
            {"run_id": "run-1", "command": "cancel"},
            context,
        )
    allowed = tapcanvas_sidecar.authorized_tool_arguments(
        "village_canvas_dispatch_action",
        {"commands": [{"type": "create_canvas_node"}]},
        context,
    )
    assert allowed["commands"][0]["type"] == "create_canvas_node"
    assert tapcanvas_sidecar.workflow_requested("帮我做两张角色卡") is False
    assert tapcanvas_sidecar.workflow_requested("不要启动工作流或分镜") is False
    assert tapcanvas_sidecar.workflow_requested("做一个三镜分镜工作流") is True
    authorized = tapcanvas_sidecar.authorized_tool_arguments(
        "village_canvas_start_workflow_run",
        {"workflow_id": "custom-canvas-workflow"},
        {"workflow_authorized": True},
    )
    assert authorized["workflow_id"] == "custom-canvas-workflow"


@pytest.mark.asyncio
async def test_sse_stream_projects_progress_and_result() -> None:
    class FakeStream:
        async def aiter_lines(self):
            for line in (
                "event: thread.started",
                'data: {"threadId":"t"}',
                "",
                "event: content",
                'data: {"delta":"你"}',
                "",
                "event: content",
                'data: {"delta":"好"}',
                "",
                "event: result",
                'data: {"response":{"text":"你好","trace":{"runOutcome":{"status":"succeeded"}}}}',
                "",
                "event: done",
                'data: {"reason":"logical_succeeded"}',
                "",
            ):
                yield line

    seen: list[tuple[str, dict]] = []

    async def sink(name, data):
        seen.append((name, dict(data)))

    result = await tapcanvas_sidecar._consume_sidecar_stream(FakeStream(), sink)

    assert result["text"] == "你好"
    assert [name for name, _ in seen] == [
        "thread.started",
        "content",
        "content",
        "result",
        "done",
    ]
    assert seen[1][1]["delta"] == "你"


@pytest.mark.asyncio
async def test_stream_chat_turn_projects_trace_and_canvas_patch(monkeypatch) -> None:
    from novelvideo.chat._service_parts import _service_context

    canvas_payload = {
        "schema": "canvas_chat_commands.v1",
        "project_id": "project-a",
        "canvas_id": "default",
        "command_id": "cmd-1",
        "commands": [{"type": "create_canvas_node", "node_type": "textAnnotationNode"}],
        "revision": 2,
        "server_applied": True,
        "applied_ops": 1,
        "created_node_ids": ["node-1"],
    }

    async def fake_run_turn(**kwargs):
        assert kwargs["turn_id"] == "turn-1"
        return {
            "text": "已完成",
            "trace": {"runOutcome": {"status": "succeeded"}},
            "tool_calls": [
                {
                    "toolCallId": "call-1",
                    "name": "village_canvas_dispatch_action",
                    "status": "succeeded",
                    "input": {"canvas_id": "default"},
                    "outputPreview": json.dumps(canvas_payload),
                }
            ],
        }

    def fake_add_assistant_message(*args, **kwargs):
        assert kwargs["metadata"]["backend"] == "xiaoshu-sidecar"
        assert kwargs["metadata"]["backend_thread_id"].startswith("xiaoshu:")
        return {"role": "assistant", "content": "已完成", "turn_id": "turn-1"}

    monkeypatch.setattr(tapcanvas_sidecar, "run_turn", fake_run_turn)
    monkeypatch.setattr(
        _service_context, "add_assistant_message", fake_add_assistant_message
    )
    events: list[dict] = []

    async def collect(event):
        events.append(event)

    message = await tapcanvas_sidecar.stream_chat_turn(
        "local",
        "project-a",
        "创建文本标注",
        collect,
        conversation_id="c1",
        canvas_id="default",
        turn_id="turn-1",
    )

    assert message["content"] == "已完成"
    kinds = [event["type"] for event in events]
    assert kinds[0] == "thread_started"
    assert "canvas_patch" in kinds
    assert kinds[-2:] == ["assistant_message", "done"]
    patch = next(event for event in events if event["type"] == "canvas_patch")
    assert patch["command_id"] == "cmd-1"
    tool_result = next(
        event for event in events if event.get("tool_event_kind") == "tool_call_update"
    )
    assert tool_result["tool_success"] is True


@pytest.mark.asyncio
async def test_village_chat_delegates_only_when_flag_is_enabled(monkeypatch) -> None:
    from novelvideo.chat._service_parts import _service_village

    calls: list[dict] = []

    async def fake_stream_chat_turn(*args, **kwargs):
        calls.append(kwargs)
        return {"role": "assistant", "content": "sidecar"}

    monkeypatch.setattr(tapcanvas_sidecar, "stream_chat_turn", fake_stream_chat_turn)
    monkeypatch.setenv("VILLAGE_CHAT_AGENT_BACKEND", "tapcanvas")

    async def noop(_event):
        return None

    result = await _service_village._stream_assistant_reply_village(
        "local",
        "project-a",
        "你好",
        noop,
        canvas_id="default",
        turn_id="turn-a",
    )

    assert result["content"] == "sidecar"
    assert calls and calls[0]["canvas_id"] == "default"
