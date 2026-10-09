from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from novelvideo import config
from novelvideo.chat import memory_index


@pytest.fixture
def isolated_growth_memory(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(memory_index, "schedule_memory_embedding", lambda *_: False)
    monkeypatch.setenv("GROWTH_DISTILLER_MODEL", "direct/test-growth")
    return tmp_path / "state"


@pytest.mark.asyncio
async def test_growth_episode_runs_distiller_and_persists_candidate(monkeypatch):
    from novelvideo.chat import service

    calls: dict[str, object] = {}
    candidate = SimpleNamespace(
        title="动作镜头配方",
        task_family="short_action_video",
        summary="用时间轴组织动作",
        reusable_principles=["动作必须有因果承接"],
        validation_checks=[{"condition": "时间轴完整"}],
        confidence=0.8,
    )
    result = SimpleNamespace(decision="candidate", candidate=candidate)

    async def fake_distill(request):
        calls["request"] = request
        return result

    def fake_save(username, **kwargs):
        calls["save"] = (username, kwargs)
        return object()

    monkeypatch.setattr(
        "novelvideo.chat.growth_distiller.distill_growth_memory", fake_distill
    )
    monkeypatch.setattr(service, "save_growth_distillation_candidate", fake_save)

    await service._distill_growth_episode(
        "local",
        project="project-a",
        turn_id="turn-a",
        conversation_id="conversation-a",
        user_prompt="以后打戏采用这个提示词框架",
        assistant_output="已整理",
        feedback="这版不错，记住这个框架",
        execution_result={"verified": False},
        project_context="项目事实",
        task_family_hint="media_generation",
    )

    assert calls["request"]["task_family_hint"] == "media_generation"
    assert calls["save"][0] == "local"
    assert calls["save"][1]["project"] == "project-a"
    assert calls["save"][1]["conversation_id"] == "conversation-a"


@pytest.mark.asyncio
async def test_growth_episode_passes_preceding_episode_to_distiller(monkeypatch):
    from novelvideo.chat import service

    calls: dict[str, object] = {}

    async def fake_distill(request):
        calls["request"] = request
        return SimpleNamespace(decision="noop", reason="captured")

    monkeypatch.setattr(
        "novelvideo.chat.growth_distiller.distill_growth_memory", fake_distill
    )

    await service._distill_growth_episode(
        "local",
        project="project-a",
        turn_id="turn-feedback",
        conversation_id="conversation-a",
        user_prompt="这版不错，记住这个框架",
        assistant_output="已收到",
        feedback="这版不错，记住这个框架",
        execution_result={"verified": True},
        project_context="项目事实",
        task_family_hint="media_generation",
        preceding_episode={
            "episode_id": 9,
            "objective": "5 秒动作镜头",
            "response_summary": "按时间轴组织动作因果链",
        },
    )

    assert calls["request"]["preceding_episode"]["episode_id"] == 9
    assert calls["request"]["preceding_episode"]["objective"] == "5 秒动作镜头"


def _event_payload() -> dict[str, object]:
    return {
        "conversation_id": "conversation-a",
        "raw_user_prompt": "以后打戏都采用这个提示词框架",
        "assistant_output": "已按时间轴和动作因果链整理",
        "user_feedback": "这版不错，记住这个框架",
        "execution_result": {"verified": False},
        "project_context": "雨夜古刹是当前项目事实",
        "task_family_hint": "short_action_video",
    }


def test_growth_outbox_payload_is_bounded_and_secret_redacted(isolated_growth_memory):
    event_id = memory_index.capture_growth_distillation_event(
        "local",
        project="project-a",
        turn_id="turn-bounded",
        payload={
            **_event_payload(),
            "raw_user_prompt": "TOKEN=" + "sk-" + "a" * 40 + " 大段教学 " * 4_000,
        },
    )

    row = memory_index.list_pending_growth_distillation_events(
        "local", project="project-a", limit=1
    )[0]
    assert row["id"] == event_id
    assert len(row["content"]) <= memory_index.GROWTH_DISTILLATION_EVENT_MAX_CHARS
    assert "sk-" + "a" * 40 not in row["content"]
    assert memory_index.parse_growth_distillation_event(row) is not None


def test_growth_distillation_receipt_is_bounded_and_tracks_terminal_state(
    isolated_growth_memory,
):
    event_id = memory_index.capture_growth_distillation_event(
        "local",
        project="project-a",
        conversation_id="conversation-a",
        turn_id="turn-receipt",
        payload=_event_payload(),
    )

    pending = memory_index.get_growth_distillation_receipt("local", event_id)
    assert pending is not None
    assert pending["schema"] == "growth_distillation_receipt.v1"
    assert pending["status"] == "pending"
    assert pending["terminal"] is False
    assert pending["result_available"] is False
    assert "raw_user_prompt" not in pending

    claimed = memory_index.claim_growth_distillation_events(
        "local", project="project-a", worker_id="worker-receipt", limit=1
    )
    assert [item["id"] for item in claimed] == [event_id]
    assert memory_index.finalize_growth_distillation_event(
        "local",
        event_id,
        worker_id="worker-receipt",
        decision="needs_review",
        reason="needs independent verification",
    )

    terminal = memory_index.get_growth_distillation_receipt("local", event_id)
    assert terminal is not None
    assert terminal["status"] == "needs_review"
    assert terminal["terminal"] is True
    assert terminal["reason"] == "needs independent verification"


def test_growth_outbox_identity_includes_conversation_scope(isolated_growth_memory):
    payload = _event_payload()
    first = memory_index.capture_growth_distillation_event(
        "local",
        project="project-a",
        conversation_id="conversation-a",
        turn_id="turn-same",
        payload=payload,
    )
    second = memory_index.capture_growth_distillation_event(
        "local",
        project="project-a",
        conversation_id="conversation-b",
        turn_id="turn-same",
        payload=payload,
    )

    assert first > 0
    assert second > 0
    assert second != first
    with memory_index._connect("local") as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM learning_events WHERE event_type=?",
            (memory_index.GROWTH_DISTILLATION_EVENT_TYPE,),
        ).fetchone()
    assert int(rows[0]) == 2


def test_unfinished_growth_events_remain_visible_in_memory_stats(isolated_growth_memory):
    event_id = memory_index.capture_growth_distillation_event(
        "local", project="project-a", turn_id="turn-status", payload=_event_payload()
    )
    assert memory_index.memory_stats("local")["pending_events"] == 1

    conn = memory_index._connect("local")
    try:
        conn.execute(
            "UPDATE learning_events SET status='retryable' WHERE id=?", (event_id,)
        )
        conn.commit()
    finally:
        conn.close()
    assert memory_index.memory_stats("local")["pending_events"] == 1

    conn = memory_index._connect("local")
    try:
        conn.execute(
            "UPDATE learning_events SET status='processing' WHERE id=?", (event_id,)
        )
        conn.commit()
    finally:
        conn.close()
    assert memory_index.memory_stats("local")["pending_events"] == 1

    conn = memory_index._connect("local")
    try:
        conn.execute(
            "UPDATE learning_events SET status='needs_review' WHERE id=?", (event_id,)
        )
        conn.commit()
    finally:
        conn.close()
    assert memory_index.memory_stats("local")["pending_events"] == 0


def test_growth_outbox_claim_has_single_owner_and_expired_lease_recovery(
    isolated_growth_memory,
):
    event_id = memory_index.capture_growth_distillation_event(
        "local", project="project-a", turn_id="turn-lease", payload=_event_payload()
    )

    first = memory_index.claim_growth_distillation_events(
        "local", project="project-a", worker_id="worker-a", lease_seconds=60
    )
    second = memory_index.claim_growth_distillation_events(
        "local", project="project-a", worker_id="worker-b", lease_seconds=60
    )
    assert [row["id"] for row in first] == [event_id]
    assert second == []

    conn = memory_index._connect("local")
    try:
        conn.execute(
            "UPDATE learning_events SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",
            (event_id,),
        )
        conn.commit()
    finally:
        conn.close()

    recovered = memory_index.claim_growth_distillation_events(
        "local", project="project-a", worker_id="worker-b", lease_seconds=60
    )
    assert [row["id"] for row in recovered] == [event_id]
    assert recovered[0]["claimed_by"] == "worker-b"


def test_growth_outbox_concurrent_capture_produces_one_row(isolated_growth_memory):
    conn = memory_index._connect("local")
    conn.close()

    def capture() -> int:
        return memory_index.capture_growth_distillation_event(
            "local",
            project="project-a",
            turn_id="turn-concurrent",
            payload=_event_payload(),
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        event_ids = list(pool.map(lambda _index: capture(), range(8)))

    assert len(set(event_ids)) == 1
    conn = memory_index._connect("local")
    try:
        count = conn.execute(
            """
            SELECT COUNT(*) FROM learning_events
             WHERE event_type=? AND task_id='turn-concurrent'
            """,
            (memory_index.GROWTH_DISTILLATION_EVENT_TYPE,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_growth_claim_lease_covers_configured_provider_timeout(monkeypatch):
    from novelvideo.chat import service

    monkeypatch.setenv("GROWTH_DISTILLER_TIMEOUT_SECONDS", "240")
    monkeypatch.setenv("GROWTH_DISTILLER_LEASE_MARGIN_SECONDS", "45")
    assert service._growth_distiller_lease_seconds() == 285


def test_knowledge_receipt_items_explain_selected_memories_without_secrets():
    from novelvideo.chat import service

    records = [
        SimpleNamespace(
            id=7,
            content="使用时间轴组织镜头；TOKEN=sk-" + "a" * 40,
            metadata_json='{"title":"动作镜头配方","candidate_recall":true,"memory_schema":"xiaoshu.memory.v3","rule_type":"prompt"}',
            kind="experience_recipe",
            scope_kind="professional",
            status="candidate",
            source="opennana",
            confidence=0.83,
            evidence_count=3,
            retrieved_count=4,
            applied_count=1,
            positive_count=2,
            negative_count=0,
            last_verified_at="2026-08-27T00:00:00+00:00",
        )
    ]

    items = service._knowledge_receipt_items({"records": records})

    assert items[0]["memory_id"] == 7
    assert items[0]["title"] == "动作镜头配方"
    assert "sk-" + "a" * 40 not in items[0]["summary"]
    assert items[0]["candidate_recall"] is True
    assert items[0]["execution_rule"] is True
    assert items[0]["influence"] == "execution_rule"


@pytest.mark.asyncio
async def test_growth_outbox_replay_is_idempotent_across_repeated_drain(
    isolated_growth_memory, monkeypatch
):
    from novelvideo.chat import service

    event_id = memory_index.capture_growth_distillation_event(
        "local", project="project-a", turn_id="turn-outbox", payload=_event_payload()
    )
    assert event_id > 0
    assert (
        memory_index.capture_growth_distillation_event(
            "local", project="project-a", turn_id="turn-outbox", payload=_event_payload()
        )
        == event_id
    )

    candidate = SimpleNamespace(
        title="短打配方",
        task_family="short_action_video",
        summary="时间轴组织动作",
        reusable_principles=["动作必须承接"],
        validation_checks=[{"condition": "时间轴完整"}],
        confidence=0.8,
    )
    calls = 0

    async def fake_distill(_request):
        nonlocal calls
        calls += 1
        return SimpleNamespace(decision="candidate", candidate=candidate)

    monkeypatch.setattr("novelvideo.chat.growth_distiller.distill_growth_memory", fake_distill)

    assert await service._drain_growth_distillation_events("local", project="project-a") == 1
    assert await service._drain_growth_distillation_events("local", project="project-a") == 0
    assert calls == 1

    conn = memory_index._connect("local")
    try:
        row = conn.execute(
            "SELECT status, decision, memory_id FROM learning_events WHERE id=?", (event_id,)
        ).fetchone()
    finally:
        conn.close()
    assert tuple(row) == ("compiled", "add", row["memory_id"])
    assert int(row["memory_id"]) > 0
    saved = memory_index.get_memory("local", int(row["memory_id"]))
    assert saved is not None
    assert '"candidate_conversation_id": "conversation-a"' in saved.metadata_json


@pytest.mark.asyncio
async def test_growth_outbox_replays_materialized_result_without_second_provider_call(
    isolated_growth_memory, monkeypatch
):
    from novelvideo.chat import service

    event_id = memory_index.capture_growth_distillation_event(
        "local", project="project-a", turn_id="turn-materialized", payload=_event_payload()
    )
    claimed = memory_index.claim_growth_distillation_events(
        "local", project="project-a", worker_id="worker-before-crash", lease_seconds=60
    )
    assert [row["id"] for row in claimed] == [event_id]
    materialized_result = {
        "decision": "candidate",
        "feedback_kind": "positive",
        "reason": "用户确认采用时间轴动作框架。",
        "candidate": {
            "title": "短时长动作镜头配方",
            "task_family": "short_action_video",
            "summary": "按时序、动力学和连续性组织短动作镜头。",
            "reusable_principles": ["每个动作必须承接前一身体状态"],
            "trigger_conditions": ["3 到 12 秒双角色动作视频"],
            "prompt_structure": ["时序事件", "镜头语言", "动力学约束"],
            "execution_actions": ["锁定角色和场景参考资产"],
            "validation_checks": [
                {
                    "check_id": "timeline.coverage",
                    "condition": "时序覆盖总时长",
                    "pass_when": "没有空白动作段",
                }
            ],
            "transferable_elements": ["时间轴", "动作因果链"],
            "project_specific_elements": ["雨夜古刹"],
            "confidence": 0.88,
            "evidence_basis": ["用户明确要求记住采用后的框架"],
        },
    }
    assert memory_index.materialize_growth_distillation_result(
        "local",
        event_id,
        worker_id="worker-before-crash",
        result=materialized_result,
    )

    # Simulate the process stopping after the provider result checkpoint but
    # before candidate creation and event finalization.
    conn = memory_index._connect("local")
    try:
        conn.execute(
            "UPDATE learning_events SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",
            (event_id,),
        )
        conn.commit()
    finally:
        conn.close()

    provider_calls = 0

    async def provider_must_not_run(_request):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("materialized replay must not call the provider")

    monkeypatch.setattr(
        "novelvideo.chat.growth_distiller.distill_growth_memory", provider_must_not_run
    )

    assert await service._drain_growth_distillation_events("local", project="project-a") == 1
    assert provider_calls == 0
    conn = memory_index._connect("local")
    try:
        event = conn.execute(
            "SELECT status, decision, memory_id FROM learning_events WHERE id=?", (event_id,)
        ).fetchone()
        candidate_count = conn.execute(
            """SELECT COUNT(*) FROM memory_entries
                 WHERE source='growth_distiller' AND source_id=?""",
            (f"growth-event:{event_id}",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert tuple(event) == ("compiled", "add", event["memory_id"])
    assert int(event["memory_id"]) > 0
    assert candidate_count == 1


@pytest.mark.asyncio
async def test_growth_outbox_failure_stays_pending_then_replays_after_restart(
    isolated_growth_memory, monkeypatch
):
    from novelvideo.chat import service

    event_id = memory_index.capture_growth_distillation_event(
        "local", project="project-a", turn_id="turn-retry", payload=_event_payload()
    )
    attempts = 0

    async def flaky_distill(_request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("provider temporarily down")
        return SimpleNamespace(decision="noop", reason="no reusable method")

    monkeypatch.setattr("novelvideo.chat.growth_distiller.distill_growth_memory", flaky_distill)

    assert await service._drain_growth_distillation_events("local", project="project-a") == 1
    conn = memory_index._connect("local")
    try:
        first = conn.execute(
            "SELECT status, reason FROM learning_events WHERE id=?", (event_id,)
        ).fetchone()
    finally:
        conn.close()
    assert first["status"] == "retryable"
    assert "exception" in first["reason"]

    # A fresh service process would execute the same durable pending row.
    conn = memory_index._connect("local")
    try:
        conn.execute(
            "UPDATE learning_events SET next_attempt_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (event_id,),
        )
        conn.commit()
    finally:
        conn.close()
    assert await service._drain_growth_distillation_events("local", project="project-a") == 1
    assert attempts == 2
    conn = memory_index._connect("local")
    try:
        final = conn.execute(
            "SELECT status, decision, memory_id FROM learning_events WHERE id=?", (event_id,)
        ).fetchone()
    finally:
        conn.close()
    assert final["status"] == "evidence_only"
    assert final["decision"] == "evidence_only"
    assert final["memory_id"] == 0


@pytest.mark.asyncio
async def test_growth_outbox_provider_retry_has_bounded_terminal_review(
    isolated_growth_memory, monkeypatch
):
    from novelvideo.chat import service

    monkeypatch.setenv("GROWTH_DISTILLER_MAX_ATTEMPTS", "1")
    event_id = memory_index.capture_growth_distillation_event(
        "local", project="project-a", turn_id="turn-retry-limit", payload=_event_payload()
    )

    async def always_fails(_request):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr("novelvideo.chat.growth_distiller.distill_growth_memory", always_fails)
    assert await service._drain_growth_distillation_events("local", project="project-a") == 1

    conn = memory_index._connect("local")
    try:
        row = conn.execute(
            "SELECT status, decision, reason FROM learning_events WHERE id=?", (event_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "needs_review"
    assert row["decision"] == "needs_review"
    assert "retry_exhausted" in row["reason"]
