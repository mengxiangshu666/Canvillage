from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo import config
from novelvideo.agent_tools import village_canvas
from novelvideo.chat import memory_index
from novelvideo.chat import service as chat_service
from novelvideo.chat import growth_distiller
from novelvideo.freezone import canvas_store
from novelvideo.project_context import ProjectContext
from novelvideo.api.routes import workflows
from novelvideo.workflow_runtime import service as workflow_service
from novelvideo.workflow_runtime.service import WorkflowRuntimeService


def _load_canvas_plugin():
    return village_canvas


class _GrowthResult:
    decision = "candidate"
    feedback_kind = "positive"
    reason = "用户确认该配方进入正式工作流"
    candidate = SimpleNamespace(
        title="正式工作流动作镜头配方",
        task_family="short_action_video",
        summary="先写动作因果，再写运镜和明确结束状态。",
        reusable_principles=["每个动作必须承接上一身体状态"],
        validation_checks=[{"condition": "时序覆盖完整时长"}],
        confidence=0.9,
    )

    def model_dump(self, *, mode: str = "json") -> dict[str, object]:
        del mode
        return {
            "decision": self.decision,
            "feedback_kind": self.feedback_kind,
            "reason": self.reason,
            "candidate": {
                "title": self.candidate.title,
                "task_family": self.candidate.task_family,
                "summary": self.candidate.summary,
                "reusable_principles": self.candidate.reusable_principles,
                "validation_checks": self.candidate.validation_checks,
                "confidence": self.candidate.confidence,
            },
        }


@pytest.mark.asyncio
async def test_growth_distillation_drain_consumes_pending_event_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """The lifecycle worker must consume the durable outbox, not just unit helpers."""

    state_root = tmp_path / "state"
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(state_root))
    monkeypatch.setattr(config, "STATE_DIR", str(state_root))
    monkeypatch.setattr(memory_index, "schedule_memory_embedding", lambda *_: False)
    monkeypatch.setattr(memory_index, "resolve_direct_model", lambda _kind: None)
    monkeypatch.setattr(growth_distiller, "growth_distiller_model_ref", lambda: "direct/text-test")

    calls: list[dict[str, object]] = []

    async def fake_distill(request, *, agent=None):
        del agent
        calls.append(dict(request))
        return _GrowthResult()

    monkeypatch.setattr(growth_distiller, "distill_growth_memory", fake_distill)
    event_id = memory_index.capture_growth_distillation_event(
        "alice",
        project="project-drain",
        turn_id="turn-drain",
        payload={
            "raw_user_prompt": "记住这套动作镜头提示词框架",
            "assistant_output": "已按动作因果完成草稿",
            "user_feedback": "这版采用，记住这个框架",
            "execution_result": {"verified": True},
            "project_context": "动作镜头项目",
            "task_family_hint": "media_generation",
        },
    )
    assert event_id > 0

    processed = await chat_service._drain_growth_distillation_events(
        "alice", project="project-drain", limit=2
    )
    assert processed == 1
    assert len(calls) == 1
    assert calls[0]["raw_user_prompt"] == "记住这套动作镜头提示词框架"

    candidates = memory_index.list_memories(
        "alice", status="candidate", kind="candidate_experience"
    )
    assert len(candidates) == 1
    assert candidates[0].source == "growth_distiller"

    with memory_index._connect("alice") as conn:
        event = conn.execute(
            "SELECT status, decision, memory_id FROM learning_events WHERE id=?",
            (event_id,),
        ).fetchone()
    assert tuple(event) == ("compiled", "add", candidates[0].id)

    # The durable decision makes a second lifecycle drain a no-op.
    assert await chat_service._drain_growth_distillation_events(
        "alice", project="project-drain", limit=2
    ) == 0
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_agent_dispatch_workflow_verifier_growth_recall_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    state_root = tmp_path / "state"
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(state_root))
    monkeypatch.setattr(config, "STATE_DIR", str(state_root))
    monkeypatch.setattr(memory_index, "schedule_memory_embedding", lambda *_: False)
    monkeypatch.setattr(memory_index, "resolve_direct_model", lambda _kind: None)

    def fake_model_plan(_bindings=None):
        def binding(role, kind):
            return {
                "role": role,
                "kind": kind,
                "registry_id": f"{kind}-test",
                "catalog_id": f"direct/{kind}-test",
                "upstream_model": f"{kind}-upstream-test",
                "protocol": "openai-compatible",
                "endpoint_fingerprint": f"endpoint-{kind}",
                "capability_revision": "direct-model-contract.v2",
                "capabilities": {"runtime_ready": True},
            }

        return {
            "schema": "canvas_model_plan_snapshot.v1",
            "model_plan_revision": "direct-model-plan.v1",
            "bindings": {
                "director": binding("director", "agent"),
                "image": binding("image", "image"),
            },
            "missing_roles": ["video", "audio", "embedding"],
            "fallback_policy": "explicit-only",
        }

    monkeypatch.setattr(workflow_service, "build_model_plan_snapshot", fake_model_plan)

    plugin = _load_canvas_plugin()
    runtime = WorkflowRuntimeService(
        state_root / "alice" / "project-e2e",
        project_id="project-e2e",
        project_context=SimpleNamespace(
            requester_user_id="user-1",
            requester_username="alice",
            owner_username="alice",
            project_name="Agent Workflow E2E",
        ),
    )

    project_dir = state_root / "alice" / "project-e2e"
    output_dir = state_root / "alice" / "project-e2e-output"
    runtime_dir = state_root / "alice" / "project-e2e-runtime"
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    canvas_store.ensure_default_canvas(
        project_dir,
        project_id="project-e2e",
        actor_id="user-1",
    )
    context = ProjectContext(
        project_id="project-e2e",
        project_name="Agent Workflow E2E",
        owner_type="user",
        owner_id="user-1",
        owner_username="alice",
        requester_user_id="user-1",
        requester_username="alice",
        requester_principals=(("user", "user-1"),),
        effective_role="owner",
        home_node_id="local",
        output_dir=output_dir,
        state_dir=project_dir,
        runtime_dir=runtime_dir,
        is_home_node=True,
    )

    async def resolve_context(*, user, project_id, required_role):
        assert user["username"] == "alice"
        assert project_id == "project-e2e"
        assert required_role in {"viewer", "editor"}
        return context

    monkeypatch.setattr(workflows, "resolve_project_context", resolve_context)
    monkeypatch.setattr(workflows, "schedule_workflow_run", lambda *_: None)
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: {
        "username": "alice",
        "user_id": "user-1",
    }
    client = TestClient(app)

    def real_http_request(method, path, *, query=None, body=None):
        response = client.request(method, path, params=query, json=body)
        payload = response.json()
        if isinstance(payload, dict):
            return {"status_code": response.status_code, **payload}
        return {
            "status_code": response.status_code,
            "ok": response.is_success,
            "data": payload,
        }

    monkeypatch.setattr(plugin, "_request", real_http_request)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-e2e")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "default")

    dispatch_result = await asyncio.to_thread(
        plugin._handle_dispatch_canvas_action,
        {
            "request": "为动作镜头建立可恢复工作流",
            "goal": "完成动作镜头结构并保留 verifier 回执",
            "success_criteria": ["WorkflowRun 建立", "verifier 能记录结果"],
            "command_id": "agent-dispatch-e2e",
            "idempotency_key": "agent-dispatch-e2e-key",
            "source_turn_id": "turn-agent-e2e",
            "workflow_id": "custom-canvas-workflow",
            "task": {
                "operation": "create_storyboard",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "creation_reason": "当前画布还没有承载该导演工作流的结构",
                "step_count": 2,
                "item_count": 1,
                "dependency_count": 1,
                "estimated_duration_seconds": 2,
                "requires_recovery": True,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
        },
    )
    if isinstance(dispatch_result, str):
        dispatch_result = json.loads(dispatch_result)
    assert dispatch_result["ok"] is True
    run = dispatch_result["data"]
    assert dispatch_result["action_dispatch"]["route"]["lane"] == "workflow"
    assert run["project_id"] == "project-e2e"
    assert run["source_turn_id"] == "turn-agent-e2e"

    # Verifier commits before the chat turn has materialized its episode or
    # Growth Distiller candidate. The WorkflowRun event must remain replayable.
    external_verifier_response = client.post(
        f"/api/v1/projects/project-e2e/workflow-runs/{run['id']}/events",
        json={
            "event_id": "agent-verifier-e2e",
            "type": "verification_passed",
            "step_id": "canvas_structure",
            "success": True,
            "payload": {
                "canvas_revision": 11,
                "command_id": "agent-dispatch-e2e",
            },
            "expected_revision": run["revision"],
        },
    )
    assert external_verifier_response.status_code == 409
    assert (
        external_verifier_response.json()["detail"]["code"]
        == "workflow_event_source_forbidden"
    )
    verified, applied = await runtime.store.record_event(
        run["id"],
        event_id="agent-verifier-e2e",
        event_type="verification_passed",
        step_id="canvas_structure",
        success=True,
        payload={
            "canvas_revision": 11,
            "command_id": "agent-dispatch-e2e",
        },
        expected_revision=run["revision"],
        source="verifier",
    )
    assert applied is True
    assert verified is not None

    # This is the real Agent turn boundary used by chat/service.py.
    episode = memory_index.record_execution_episode(
        "alice",
        project="project-e2e",
        conversation_id="main",
        canvas_id="default",
        turn_id="turn-agent-e2e",
        run_id=run["id"],
        objective="为动作镜头建立可恢复工作流",
        response_summary="已建立 WorkflowRun，等待 verifier 和成长沉淀。",
        outcome="completed",
        verified=False,
    )
    assert episode["status"] == "recorded"

    growth_event_id = memory_index.capture_growth_distillation_event(
        "alice",
        project="project-e2e",
        turn_id="turn-agent-e2e",
        payload={
            "raw_user_prompt": "记住这套动作镜头提示词框架",
            "assistant_output": "已按动作因果和结束状态完成工作流",
            "user_feedback": "这版采用，记住这个框架",
            "execution_result": {"verified": True},
            "project_context": "动作镜头项目",
            "task_family_hint": "media_generation",
        },
    )
    claimed = memory_index.claim_growth_distillation_events(
        "alice",
        project="project-e2e",
        worker_id="agent-e2e-growth-worker",
        lease_seconds=60,
    )
    assert [row["id"] for row in claimed] == [growth_event_id]

    await chat_service._distill_growth_episode(
        "alice",
        project="project-e2e",
        turn_id="turn-agent-e2e",
        user_prompt="记住这套动作镜头提示词框架",
        assistant_output="已按动作因果和结束状态完成工作流",
        feedback="这版采用，记住这个框架",
        execution_result={"verified": True},
        project_context="动作镜头项目",
        task_family_hint="media_generation",
        event_id=growth_event_id,
        worker_id="agent-e2e-growth-worker",
        materialized_result=_GrowthResult(),
    )

    memory = memory_index.list_memories(
        "alice",
        status="candidate",
        kind="candidate_experience",
    )
    memory = [item for item in memory if item.source == "growth_distiller"]
    assert len(memory) == 1
    candidate = memory[0]
    assert candidate.positive_count == 1
    assert candidate.negative_count == 0
    assert json.loads(candidate.metadata_json)["candidate_recall"] is True

    recalled = await memory_index.recall_memories(
        "alice",
        "project-e2e",
        "动作镜头动作因果运镜结束状态",
        task_stage="media_generation",
    )
    assert any(item.id == candidate.id for item in recalled)

    workflow_events = await runtime.store.events_since(
        run["id"], after_seq=0, limit=20
    )
    assert any(
        item["type"] == "verification_passed"
        and item["source"] == "verifier"
        for item in workflow_events["items"]
    )
    with sqlite3.connect(runtime.store.db_path) as workflow_db:
        learning_status = workflow_db.execute(
            "SELECT learning_status FROM canvas_workflow_events WHERE event_id=?",
            ("agent-verifier-e2e",),
        ).fetchone()[0]
    assert learning_status == "recorded"

    with memory_index._connect("alice") as memory_db:
        episode_row = memory_db.execute(
            """
            SELECT run_id, outcome, verified, evidence_ref
              FROM execution_episodes
             WHERE project_id=? AND turn_id=?
            """,
            ("project-e2e", "turn-agent-e2e"),
        ).fetchone()
        growth_row = memory_db.execute(
            """
            SELECT status, decision, memory_id
              FROM learning_events WHERE id=?
            """,
            (growth_event_id,),
        ).fetchone()
    assert tuple(episode_row) == (
        run["id"],
        "verified_success",
        1,
        "workflow:" + run["id"] + ":agent-verifier-e2e:canvas_structure:passed",
    )
    assert tuple(growth_row) == ("compiled", "add", candidate.id)
