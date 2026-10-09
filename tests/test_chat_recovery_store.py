import json

from novelvideo.chat import recovery_store


def _scope() -> dict[str, str]:
    return {
        "kind": "project",
        "id": "project-a",
        "canvas_id": "canvas-a",
        "conversation_id": "conversation-a",
    }


def test_recovery_store_survives_memory_restart_and_claims_once(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    recovery_store.put_recovery(
        recovery_id="recovery-a",
        username="alice",
        scope=_scope(),
        text="继续完成任务",
        attachments=[{"id": "asset-a", "type": "image"}],
        agent_engine="village",
        model="direct/agent-a",
        research_enabled=True,
        message="工作线程已断开",
        packet={
            "schema": "village_canvas.chat_recovery.v2",
            "recovery_id": "recovery-a",
            "turn_id": "turn-original",
            "checkpoint_turn_id": "turn-original",
        },
        attempt=1,
        expires_at=9_999_999_999.0,
    )

    # A new process has no in-memory entry, but the durable card is discoverable.
    pending = recovery_store.list_pending_recoveries("alice", _scope())
    assert len(pending) == 1
    assert json.loads(pending[0]["packet_json"])["checkpoint_turn_id"] == "turn-original"

    claimed = recovery_store.claim_recovery("alice", "recovery-a")
    assert claimed is not None
    assert claimed["status"] == "pending"
    assert recovery_store.claim_recovery("alice", "recovery-a") is None
    assert recovery_store.list_pending_recoveries("alice", _scope()) == []


def test_recovery_store_is_scope_and_user_bound(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    recovery_store.put_recovery(
        recovery_id="recovery-b",
        username="alice",
        scope=_scope(),
        text="任务",
        attachments=[],
        agent_engine="village",
        model="direct/agent-a",
        research_enabled=False,
        message="失败",
        packet={"recovery_id": "recovery-b"},
        attempt=0,
        expires_at=9_999_999_999.0,
    )

    assert recovery_store.claim_recovery("bob", "recovery-b") is None
    assert recovery_store.list_pending_recoveries(
        "alice",
        {**_scope(), "canvas_id": "other-canvas"},
    ) == []
    assert recovery_store.delete_recoveries_for_scope("alice", _scope()) == 1
    assert recovery_store.list_pending_recoveries("alice", _scope()) == []
