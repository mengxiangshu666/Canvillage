from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.chat.execution_checkpoint import build_execution_checkpoint
from novelvideo.chat.execution_context import build_execution_context
from novelvideo.chat.tool_allowlist import (
    compile_execution_context_allowlist,
    compile_tool_allowlist,
)


def _plan():
    return {
        "schema": "agent_expert_plan.v1",
        "plan_revision": "plan-a",
        "context_revision": "ctx-a",
        "arbiter": {"decision": "reuse_existing_target", "execution_enabled": False},
        "experts": [
            {"expert": "canvas_state", "required_capabilities": ["canvas.snapshot"]},
        ],
        "source_errors": {},
    }


def test_read_capability_checkpoint_is_ready_when_revisions_align():
    plan = _plan()
    allowlist = compile_tool_allowlist(plan, candidates=["canvas.snapshot"])

    result = build_execution_checkpoint(
        expert_plan=plan,
        allowlist=allowlist,
        capability_id="canvas.snapshot",
        expected_plan_revision="plan-a",
        expected_allowlist_revision=allowlist["allowlist_revision"],
    )

    assert result["schema"] == "agent_execution_checkpoint.v1"
    assert result["status"] == "ready_read"
    assert result["ready"] is True
    assert result["execution_enabled"] is False


def test_write_capability_checkpoint_stays_blocked():
    plan = _plan()
    allowlist = compile_tool_allowlist(plan)

    result = build_execution_checkpoint(
        expert_plan=plan,
        allowlist=allowlist,
        capability_id="canvas.compatibility.emit",
        expected_plan_revision="plan-a",
        expected_allowlist_revision=allowlist["allowlist_revision"],
    )

    assert result["status"] == "blocked_write"
    assert result["ready"] is False
    assert result["requires_confirmation"] is True


def test_confirmed_phase2d_write_checkpoint_is_ready():
    plan = _plan()
    allowlist = compile_tool_allowlist(plan, mode="prepare_write")

    result = build_execution_checkpoint(
        expert_plan=plan,
        allowlist=allowlist,
        capability_id="canvas.compatibility.emit",
        expected_plan_revision="plan-a",
        expected_allowlist_revision=allowlist["allowlist_revision"],
        confirm=True,
    )

    assert result["status"] == "ready_write"
    assert result["ready"] is True
    assert result["execution_enabled"] is True
    assert result["requires_confirmation"] is False


def test_execute_mode_write_checkpoint_is_ready_from_active_write_entry():
    plan = _plan()
    allowlist = compile_tool_allowlist(
        plan,
        mode="execute",
        candidates=["canvas.compatibility.emit"],
    )

    result = build_execution_checkpoint(
        expert_plan=plan,
        allowlist=allowlist,
        capability_id="canvas.compatibility.emit",
        expected_plan_revision="plan-a",
        expected_allowlist_revision=allowlist["allowlist_revision"],
        confirm=True,
    )

    assert result["status"] == "ready_write"
    assert result["ready"] is True
    assert result["execution_enabled"] is True


def test_confirmed_write_checkpoint_stays_blocked_when_sources_have_errors():
    plan = {**_plan(), "source_errors": {"cognee": "timeout"}}
    allowlist = compile_tool_allowlist(plan)

    result = build_execution_checkpoint(
        expert_plan=plan,
        allowlist=allowlist,
        capability_id="canvas.compatibility.emit",
        expected_plan_revision="plan-a",
        expected_allowlist_revision=allowlist["allowlist_revision"],
        confirm=True,
    )

    assert result["status"] == "blocked_write"
    assert result["reason"] == "source_errors_present"
    assert result["execution_enabled"] is False


def test_checkpoint_detects_stale_allowlist_revision():
    plan = _plan()
    allowlist = compile_tool_allowlist(plan)
    result = build_execution_checkpoint(
        expert_plan=plan,
        allowlist=allowlist,
        capability_id="canvas.compatibility.emit",
        expected_plan_revision="plan-a",
        expected_allowlist_revision="old-allowlist",
        confirm=True,
    )

    assert result["status"] == "stale_context"
    assert result["requires_replan"] is True
    assert result["execution_enabled"] is False


def test_checkpoint_detects_stale_plan_revision():
    plan = _plan()
    allowlist = compile_tool_allowlist(plan)
    result = build_execution_checkpoint(
        expert_plan=plan,
        allowlist=allowlist,
        capability_id="canvas.snapshot",
        expected_plan_revision="old-plan",
        expected_allowlist_revision=allowlist["allowlist_revision"],
    )

    assert result["status"] == "stale_context"
    assert result["requires_replan"] is True


def test_checkpoint_route_recomputes_current_allowlist(monkeypatch):
    from novelvideo.api.routes import agent_memory

    async def fake_plan(**_kwargs):
        return _plan()

    monkeypatch.setattr(agent_memory, "get_shared_agent_expert_plan", fake_plan)
    allowlist = compile_tool_allowlist(_plan(), candidates=["canvas.snapshot"])
    result = asyncio.run(
        agent_memory.get_shared_agent_execution_checkpoint(
            project="project-a",
            canvas_id="canvas-a",
            query="检查当前画布",
            capability_id="canvas.snapshot",
            plan_revision="plan-a",
            allowlist_revision=allowlist["allowlist_revision"],
            sources="memory",
            mode="observe",
            candidates="canvas.snapshot",
            user={"username": "alice"},
        )
    )

    assert result["schema"] == "agent_execution_checkpoint.v1"
    assert result["status"] == "ready_read"


def test_checkpoint_route_adopts_current_revisions_for_legacy_dynamic_write(
    monkeypatch,
):
    from novelvideo.api.routes import agent_memory

    async def fake_plan(**_kwargs):
        return _plan()

    monkeypatch.setattr(agent_memory, "get_shared_agent_expert_plan", fake_plan)
    result = asyncio.run(
        agent_memory.get_shared_agent_execution_checkpoint(
            project="project-a",
            canvas_id="canvas-a",
            query="优化已有节点",
            capability_id="canvas.compatibility.emit",
            plan_revision="",
            allowlist_revision="",
            confirm=True,
            sources="memory",
            mode="execute",
            candidates="canvas.compatibility.emit",
            user={"username": "alice"},
        )
    )

    assert result["status"] == "ready_write"
    assert result["ready"] is True
    assert result["execution_enabled"] is True
    assert result["plan_revision"] == "plan-a"
    assert result["expected_plan_revision"] == "plan-a"
    assert result["allowlist_revision"] == result["expected_allowlist_revision"]


def _http_execution_context(
    *,
    project_id: str = "project-a",
    canvas_id: str = "canvas-a",
    capability_id: str = "canvas.compatibility.emit",
) -> dict:
    return build_execution_context(
        canonical_intent="emit a compatibility receipt",
        project_id=project_id,
        canvas_id=canvas_id,
        plan_revision="plan-a",
        model_plan_revision="model-plan-a",
        selected_handler="canvas_command_gateway",
        capability_id=capability_id,
        side_effect_policy="write",
        idempotency_key="idem-a",
        expected_postconditions=[{"type": "receipt", "status": "success"}],
        recovery_handle={"schema": "workflow_recovery.v1", "workflow_run_id": "run-a"},
    )


def _http_execution_checkpoint_fixture(monkeypatch):
    from novelvideo.api.routes import agent_memory

    async def fake_resolve_project_context(*, user, project_id, required_role):
        assert user["username"] == "alice"
        assert project_id == "project-a"
        assert required_role == "editor"
        return SimpleNamespace(is_home_node=True)

    app = FastAPI()
    app.include_router(agent_memory.router, prefix="/api/v1")
    app.dependency_overrides[agent_memory.get_api_user] = lambda: {
        "username": "alice",
        "user_id": "user-a",
    }
    monkeypatch.setattr(agent_memory, "resolve_project_context", fake_resolve_project_context)
    monkeypatch.setattr(
        agent_memory,
        "require_project_home_node",
        lambda context, **_kwargs: context,
    )

    context = _http_execution_context()
    allowlist = compile_execution_context_allowlist(context, mode="execute")
    body = {
        "project": "project-a",
        "canvas_id": "canvas-a",
        "query": "emit a compatibility receipt",
        "capability_id": "canvas.compatibility.emit",
        "confirm": True,
        "mode": "execute",
        "execution_context": context,
    }
    return TestClient(app), body, context, allowlist


def test_http_execution_checkpoint_allows_confirmed_write_with_valid_identity(monkeypatch):
    client, body, _context, allowlist = _http_execution_checkpoint_fixture(monkeypatch)

    response = client.post("/api/v1/chat/context/execution-checkpoint", json=body)

    assert response.status_code == 200
    result = response.json()
    assert result["schema"] == "agent_execution_checkpoint.v1"
    assert result["status"] == "ready_write"
    assert result["ready"] is True
    assert result["execution_enabled"] is True
    assert result["capability_id"] == "canvas.compatibility.emit"
    assert result["plan_revision"] == "plan-a"
    assert result["allowlist_revision"] == allowlist["allowlist_revision"]
    assert result["blocking_reasons"] == []


def test_http_execution_checkpoint_blocks_digest_mismatch(monkeypatch):
    client, body, context, _allowlist = _http_execution_checkpoint_fixture(monkeypatch)
    body["execution_context"] = {**context, "digest": "digest-tampered"}

    response = client.post("/api/v1/chat/context/execution-checkpoint", json=body)

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "blocked_context"
    assert result["ready"] is False
    assert result["execution_enabled"] is False
    assert result["reason"] == "execution_context_digest_mismatch"
    assert "execution_context_digest_mismatch" in result["blocking_reasons"]


def test_http_execution_checkpoint_blocks_identity_mismatches(monkeypatch):
    cases = (
        ("project", _http_execution_context(project_id="project-b"), "execution_context_project_mismatch"),
        ("canvas", _http_execution_context(canvas_id="canvas-b"), "execution_context_canvas_mismatch"),
        (
            "capability",
            _http_execution_context(capability_id="canvas.snapshot"),
            "execution_context_capability_mismatch",
        ),
    )

    for _label, context, reason in cases:
        client, body, _valid_context, _allowlist = _http_execution_checkpoint_fixture(monkeypatch)
        body["execution_context"] = context

        response = client.post("/api/v1/chat/context/execution-checkpoint", json=body)

        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "blocked_context"
        assert result["ready"] is False
        assert result["execution_enabled"] is False
        assert result["reason"] == reason
        assert reason in result["blocking_reasons"]


def test_http_execution_checkpoint_blocks_stale_allowlist_revision(monkeypatch):
    client, body, _context, allowlist = _http_execution_checkpoint_fixture(monkeypatch)
    body["allowlist_revision"] = "stale-allowlist"

    response = client.post("/api/v1/chat/context/execution-checkpoint", json=body)

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "blocked_context"
    assert result["reason"] == "execution_context_allowlist_revision_mismatch"
    assert result["ready"] is False
    assert result["execution_enabled"] is False
    assert result["allowlist_revision"] == allowlist["allowlist_revision"]


def test_http_execution_checkpoint_blocks_stale_plan_revision(monkeypatch):
    client, body, _context, _allowlist = _http_execution_checkpoint_fixture(monkeypatch)
    body["plan_revision"] = "stale-plan"

    response = client.post("/api/v1/chat/context/execution-checkpoint", json=body)

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "blocked_context"
    assert result["reason"] == "execution_context_plan_revision_mismatch"
    assert result["ready"] is False
    assert result["execution_enabled"] is False


def test_http_execution_checkpoint_rejects_supplied_allowlist_drift(monkeypatch):
    client, body, _context, allowlist = _http_execution_checkpoint_fixture(monkeypatch)
    body["allowlist"] = {**allowlist, "allowlist_revision": "tampered"}

    response = client.post("/api/v1/chat/context/execution-checkpoint", json=body)

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "blocked_context"
    assert result["reason"] == "execution_context_allowlist_digest_mismatch"
    assert result["ready"] is False
    assert result["execution_enabled"] is False
