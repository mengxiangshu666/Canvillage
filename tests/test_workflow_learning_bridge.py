from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from novelvideo import config
from novelvideo.chat import memory_index
from novelvideo.workflow_runtime.learning_bridge import (
    record_workflow_verification_learning,
)
from novelvideo.workflow_runtime import learning_bridge
from novelvideo.workflow_runtime.service import WorkflowRuntimeService


@pytest.fixture
def isolated_learning(monkeypatch: pytest.MonkeyPatch, tmp_path):
    state_dir = tmp_path / "memory-state"
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(state_dir))
    monkeypatch.setattr(config, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(memory_index, "schedule_memory_embedding", lambda *_: False)
    return tmp_path


def _run(
    *,
    project_id: str = "project-a",
    source_turn_id: str = "turn-a",
    username: str = "alice",
) -> dict:
    return {
        "id": "wfr-test-1",
        "project_id": project_id,
        "source_turn_id": source_turn_id,
        "project_context": {
            "requester_username": username,
            "owner_username": "owner",
        },
    }


def _candidate(*, project: str = "project-a", task_id: str = "turn-a"):
    return memory_index.record_candidate_experience(
        "alice",
        project=project,
        content="镜头验收必须先核对角色身份和空间锚点，失败时保留成功资产",
        evidence_ref=f"turn:{task_id}",
        task_id=task_id,
    )


def _llm_candidate() -> int:
    return memory_index.upsert_memory(
        "alice",
        scope_kind="professional",
        project=None,
        kind="candidate_experience",
        source="growth_distiller",
        source_id="growth-test-recipe",
        content="镜头提示词先写动作因果，再写运镜和结束状态。",
        status="candidate",
        confidence=0.7,
        metadata={"distillation_level": "llm_candidate"},
        evidence_count=0,
    )


def _growth_result() -> SimpleNamespace:
    return SimpleNamespace(
        decision="candidate",
        candidate=SimpleNamespace(
            title="经 verifier 验证的动作镜头配方",
            task_family="short_action_video",
            summary="先写动作因果，再写运镜和结束状态。",
            reusable_principles=["每个动作必须承接上一身体状态"],
            validation_checks=[{"condition": "时序覆盖完整时长"}],
            confidence=0.86,
        ),
        feedback_kind="positive",
        reason="用户确认采用并进入正式工作流",
    )


def test_verifier_learning_notes_preserve_semantic_edge_evidence():
    notes = learning_bridge._evidence_notes(
        event_type="verification_passed",
        step_id="canvas_structure",
        payload={
            "canvas_revision": 12,
            "verified_semantic_edges": [
                {
                    "source": "hero-v1",
                    "target": "shot-1",
                    "relation": "identity_lock",
                    "sourceRevision": 3,
                    "targetRevision": 7,
                }
            ],
        },
        error="",
    )

    assert "semantic_edges=1" in notes
    assert "edge=identity_lock:hero-v1->shot-1@3->7" in notes


@pytest.mark.asyncio
async def test_verifier_positive_and_negative_evidence_are_idempotent(isolated_learning):
    candidate = _candidate()
    assert candidate is not None
    assert candidate.positive_count == 1

    passed = await record_workflow_verification_learning(
        run=_run(),
        event_id="event-pass",
        event_type="verification_passed",
        step_id="canvas_structure",
        payload={"canvas_revision": 12, "command_id": "command-1"},
        error="",
    )
    assert passed["status"] == "recorded"

    repeated = await record_workflow_verification_learning(
        run=_run(),
        event_id="event-pass",
        event_type="verification_passed",
        step_id="canvas_structure",
        payload={"canvas_revision": 12, "command_id": "command-1"},
        error="",
    )
    assert repeated["status"] == "recorded"

    failed = await record_workflow_verification_learning(
        run=_run(),
        event_id="event-fail",
        event_type="verification_failed",
        step_id="canvas_structure",
        payload={"error_code": "canvas_revision_conflict"},
        error="revision conflict",
    )
    assert failed["status"] == "recorded"

    updated = memory_index.get_memory("alice", candidate.id)
    assert updated is not None
    assert updated.evidence_count == 3
    assert updated.positive_count == 2
    assert updated.negative_count == 1
    assert updated.status == "candidate"


def test_llm_growth_candidate_auto_promotes_after_independent_positive_tasks(isolated_learning):
    candidate_id = _llm_candidate()
    first = memory_index.record_memory_evidence(
        "alice",
        candidate_id,
        outcome="positive",
        evidence_ref="workflow:one:verify",
        project="project-a",
        task_id="turn-a",
    )
    assert first is not None and first.status == "candidate"
    promoted = memory_index.record_memory_evidence(
        "alice",
        candidate_id,
        outcome="positive",
        evidence_ref="workflow:two:verify",
        project="project-b",
        task_id="turn-b",
    )
    assert promoted is not None
    assert promoted.status == "confirmed"
    assert promoted.promoted_from_id == candidate_id
    assert promoted.scope_kind == "user"
    assert memory_index.get_memory("alice", candidate_id).status == "archived"


@pytest.mark.asyncio
async def test_verifier_without_candidate_is_pending_and_deduplicated(isolated_learning):
    run = _run()
    first = await record_workflow_verification_learning(
        run=run,
        event_id="event-no-candidate",
        event_type="verification_passed",
        step_id="canvas_structure",
        payload={"canvas_revision": 2},
        error="",
    )
    second = await record_workflow_verification_learning(
        run=run,
        event_id="event-no-candidate",
        event_type="verification_passed",
        step_id="canvas_structure",
        payload={"canvas_revision": 2},
        error="",
    )

    assert first["status"] == "pending"
    assert second == first
    assert memory_index.memory_stats("alice")["pending_events"] == 1
    assert memory_index.list_memories("alice", status="candidate") == []


@pytest.mark.asyncio
async def test_verifier_evidence_is_bound_to_project_and_task(isolated_learning):
    candidate = _candidate()
    assert candidate is not None

    result = await record_workflow_verification_learning(
        run=_run(project_id="project-b", source_turn_id="turn-a"),
        event_id="event-cross-project",
        event_type="verification_passed",
        step_id="canvas_structure",
        payload={},
        error="",
    )

    assert result["status"] == "pending"
    unchanged = memory_index.get_memory("alice", candidate.id)
    assert unchanged is not None
    assert unchanged.evidence_count == 1
    assert memory_index.memory_stats("alice")["pending_events"] == 1


@pytest.mark.asyncio
async def test_late_verifier_updates_existing_execution_episode(isolated_learning):
    rule_id = memory_index.upsert_memory(
        "alice",
        scope_kind="user",
        project=None,
        kind="learned_rule",
        source="test",
        content="工作流完成后核对 verifier 回执。",
        status="confirmed",
    )
    memory_index.record_execution_episode(
        "alice",
        project="project-a",
        conversation_id="conversation-a",
        turn_id="turn-a",
        objective="执行画布工作流",
        response_summary="工作流已经提交。",
        memory_ids=[rule_id],
    )

    result = await record_workflow_verification_learning(
        run=_run(),
        event_id="event-after-turn",
        event_type="verification_passed",
        step_id="canvas_structure",
        payload={"canvas_revision": 6},
        error="",
    )

    assert result["status"] == "recorded"
    assert result["episode_id"] > 0
    updated = memory_index.get_memory("alice", rule_id)
    assert updated is not None and updated.positive_count == 1
    stats = memory_index.memory_stats("alice")
    assert stats["pending_events"] == 0
    assert stats["verifier_events"] == 1
    assert stats["workflow_evidence"] == 1


@pytest.mark.asyncio
async def test_store_verifier_event_bridges_after_commit(isolated_learning):
    project_context = SimpleNamespace(
        requester_user_id="user-1",
        requester_username="alice",
        owner_username="alice",
        project_name="Bridge Test",
    )
    service = WorkflowRuntimeService(
        isolated_learning / "workflow-state",
        project_id="project-a",
        project_context=project_context,
    )
    candidate = _candidate()
    assert candidate is not None
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-a",
        run_mode="draft",
        inputs={"request": "创建画布结构"},
        idempotency_key="bridge-run",
        contract_version=1,
        source_turn_id="turn-a",
    )

    verified, applied = await service.store.record_event(
        run["id"],
        event_id="bridge-verifier-event",
        event_type="verification_passed",
        step_id="canvas_structure",
        success=True,
        payload={"canvas_revision": 4},
        expected_revision=0,
        source="verifier",
    )

    assert applied is True
    assert verified is not None
    updated = memory_index.get_memory("alice", candidate.id)
    assert updated is not None
    assert updated.evidence_count == 2
    assert updated.positive_count == 2


@pytest.mark.asyncio
async def test_store_replays_verifier_learning_after_transient_failure(
    isolated_learning,
    monkeypatch: pytest.MonkeyPatch,
):
    project_context = SimpleNamespace(
        requester_user_id="user-1",
        requester_username="alice",
        owner_username="alice",
        project_name="Bridge Replay Test",
    )
    service = WorkflowRuntimeService(
        isolated_learning / "workflow-state",
        project_id="project-a",
        project_context=project_context,
    )
    candidate = _candidate()
    assert candidate is not None
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-a",
        run_mode="draft",
        inputs={"request": "创建画布结构"},
        idempotency_key="bridge-replay-run",
        contract_version=1,
        source_turn_id="turn-a",
    )
    real_bridge = learning_bridge.record_workflow_verification_learning

    async def fail_once(**_kwargs):
        raise RuntimeError("temporary memory database failure")

    monkeypatch.setattr(
        learning_bridge,
        "record_workflow_verification_learning",
        fail_once,
    )
    verified, applied = await service.store.record_event(
        run["id"],
        event_id="bridge-replay-event",
        event_type="verification_passed",
        step_id="canvas_structure",
        success=True,
        payload={"canvas_revision": 4},
        expected_revision=0,
        source="verifier",
    )

    assert applied is True
    assert verified is not None
    unchanged = memory_index.get_memory("alice", candidate.id)
    assert unchanged is not None and unchanged.evidence_count == 1

    monkeypatch.setattr(
        learning_bridge,
        "record_workflow_verification_learning",
        real_bridge,
    )
    replayed = await service.store.replay_verifier_learning(project_id="project-a")
    assert replayed == {
        "queued": 1,
        "recorded": 1,
        "pending": 0,
        "skipped": 0,
        "failed": 0,
    }
    updated = memory_index.get_memory("alice", candidate.id)
    assert updated is not None
    assert updated.evidence_count == 2
    assert updated.positive_count == 2
    assert updated.status == "candidate"
    assert updated.locked is False
    assert await service.store.replay_verifier_learning(project_id="project-a") == {
        "queued": 0,
        "recorded": 0,
        "pending": 0,
        "skipped": 0,
        "failed": 0,
    }


@pytest.mark.asyncio
async def test_store_replays_pending_verifier_learning_after_candidate_materializes(
    isolated_learning,
):
    project_context = SimpleNamespace(
        requester_user_id="user-1",
        requester_username="alice",
        owner_username="alice",
        project_name="Bridge Pending Test",
    )
    service = WorkflowRuntimeService(
        isolated_learning / "workflow-state-pending",
        project_id="project-a",
        project_context=project_context,
    )
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-a",
        run_mode="draft",
        inputs={"request": "创建画布结构"},
        idempotency_key="bridge-pending-run",
        contract_version=1,
        source_turn_id="turn-pending",
    )

    verified, applied = await service.store.record_event(
        run["id"],
        event_id="bridge-pending-event",
        event_type="verification_passed",
        step_id="canvas_structure",
        success=True,
        payload={"canvas_revision": 5},
        expected_revision=0,
        source="verifier",
    )

    assert applied is True
    assert verified is not None
    assert memory_index.memory_stats("alice")["pending_events"] == 1

    candidate = _candidate(project="project-a", task_id="turn-pending")
    assert candidate is not None
    replayed = await service.store.replay_verifier_learning(project_id="project-a")

    assert replayed == {
        "queued": 1,
        "recorded": 1,
        "pending": 0,
        "skipped": 0,
        "failed": 0,
    }
    updated = memory_index.get_memory("alice", candidate.id)
    assert updated is not None
    assert updated.evidence_count == 2
    assert updated.positive_count == 2


@pytest.mark.asyncio
async def test_growth_candidate_replays_verifier_evidence_into_recall(
    isolated_learning,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(memory_index, "resolve_direct_model", lambda _kind: None)
    project_context = SimpleNamespace(
        requester_user_id="user-1",
        requester_username="alice",
        owner_username="alice",
        project_name="Growth Candidate Replay",
    )
    service = WorkflowRuntimeService(
        isolated_learning / "workflow-state-growth",
        project_id="project-a",
        project_context=project_context,
    )
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-a",
        run_mode="draft",
        inputs={"request": "创建动作镜头工作流"},
        idempotency_key="growth-candidate-replay-run",
        contract_version=1,
        source_turn_id="turn-growth",
    )

    verified, applied = await service.store.record_event(
        run["id"],
        event_id="growth-candidate-verifier-event",
        event_type="verification_passed",
        step_id="canvas_structure",
        success=True,
        payload={"canvas_revision": 8},
        expected_revision=0,
        source="verifier",
    )
    assert applied is True
    assert verified is not None
    assert memory_index.memory_stats("alice")["pending_events"] == 1

    candidate = memory_index.save_growth_distillation_candidate(
        "alice",
        project="project-a",
        turn_id="turn-growth",
        result=_growth_result(),
        event_id=42,
    )
    assert candidate is not None
    assert candidate.evidence_count == 0
    assert json.loads(candidate.metadata_json)["candidate_recall"] is False

    replayed = await service.store.replay_verifier_learning(project_id="project-a")
    assert replayed == {
        "queued": 1,
        "recorded": 1,
        "pending": 0,
        "skipped": 0,
        "failed": 0,
    }
    updated = memory_index.get_memory("alice", candidate.id)
    assert updated is not None
    assert updated.evidence_count == 1
    assert updated.positive_count == 1
    assert updated.negative_count == 0
    assert json.loads(updated.metadata_json)["candidate_recall"] is True

    recalled = await memory_index.recall_memories(
        "alice",
        "project-b",
        "动作镜头动作因果运镜结束状态",
        task_stage="media_generation",
    )
    assert any(record.id == candidate.id for record in recalled)

    negative_run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-a",
        run_mode="draft",
        inputs={"request": "复核动作镜头工作流"},
        idempotency_key="growth-candidate-negative-run",
        contract_version=1,
        source_turn_id="turn-growth",
    )
    failed, failed_applied = await service.store.record_event(
        negative_run["id"],
        event_id="growth-candidate-negative-event",
        event_type="verification_failed",
        step_id="canvas_structure",
        success=False,
        payload={"error_code": "structure_mismatch"},
        error="structure mismatch",
        expected_revision=0,
        source="verifier",
    )
    assert failed_applied is True
    assert failed is not None
    updated_negative = memory_index.get_memory("alice", candidate.id)
    assert updated_negative is not None
    assert updated_negative.negative_count == 1
    assert json.loads(updated_negative.metadata_json)["candidate_recall"] is False
    recalled_after_failure = await memory_index.recall_memories(
        "alice",
        "project-b",
        "动作镜头动作因果运镜结束状态",
        task_stage="media_generation",
    )
    assert all(record.id != candidate.id for record in recalled_after_failure)


@pytest.mark.asyncio
async def test_verified_growth_candidate_crosses_conversation_boundary(isolated_learning):
    candidate_id = memory_index.upsert_memory(
        "alice",
        scope_kind="professional",
        project=None,
        kind="candidate_experience",
        source="growth_distiller",
        source_id="growth-conversation-boundary",
        content="镜头提示词先写动作因果，再写运镜和结束状态。",
        status="candidate",
        confidence=0.7,
        metadata={
            "distillation_level": "llm_candidate",
            "candidate_conversation_id": "conversation-a",
        },
        evidence_count=0,
    )

    updated = memory_index.record_memory_evidence(
        "alice",
        candidate_id,
        outcome="positive",
        evidence_ref="workflow:verified-cross-conversation",
        project="project-a",
        task_id="turn-a",
    )
    assert updated is not None
    assert json.loads(updated.metadata_json)["candidate_recall"] is True

    recalled = await memory_index.recall_memories(
        "alice",
        "project-new",
        "动作镜头动作因果运镜结束状态",
        task_stage="media_generation",
        conversation_id="conversation-b",
    )

    assert any(record.id == candidate_id for record in recalled)
