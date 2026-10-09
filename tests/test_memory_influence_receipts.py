from __future__ import annotations

import pytest

from novelvideo import config
from novelvideo.chat import memory_index


@pytest.fixture
def isolated_memory(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "state"


def _memory(username: str, content: str, **kwargs) -> int:
    return memory_index.upsert_memory(
        username,
        scope_kind="user",
        project=None,
        kind="learned_rule",
        source="test",
        content=content,
        status="confirmed",
        **kwargs,
    )


def _receipt_row(username: str):
    conn = memory_index._connect(username)
    try:
        return conn.execute(
            "SELECT * FROM memory_influence_receipts ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()


def test_influence_receipt_progression_is_idempotent_and_cannot_downgrade(
    isolated_memory,
):
    memory_id = _memory("local", "视频节点先核对模型合同，再提交生成。")
    context = {
        "project_id": "project-a",
        "conversation_id": "conversation-a",
        "turn_id": "turn-a",
        "model_id": "minimax-h3",
    }

    shown = memory_index.record_memory_influence_receipts(
        "local", [memory_id], usage_status="shown", context=context
    )
    used = memory_index.record_memory_influence_receipts(
        "local", [memory_id], usage_status="used", context=context
    )
    assert shown[0].usage_status == "shown"
    assert used[0].usage_status == "used"

    assert (
        memory_index.record_memory_application(
            "local",
            [memory_id],
            verified=True,
            receipt_context=context,
            evidence_ref="workflow:run-a:passed",
        )
        == 1
    )
    assert (
        memory_index.record_memory_application(
            "local",
            [memory_id],
            verified=True,
            receipt_context=context,
            evidence_ref="workflow:run-a:passed",
        )
        == 0
    )
    downgraded = memory_index.record_memory_influence_receipts(
        "local", [memory_id], usage_status="shown", context=context
    )
    assert downgraded[0].usage_status == "verified"
    saved = memory_index.get_memory("local", memory_id)
    assert saved is not None and saved.applied_count == 1
    row = _receipt_row("local")
    assert row is not None and row["usage_status"] == "verified"


@pytest.mark.asyncio
async def test_contract_context_filters_model_modality_and_node(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(memory_index, "resolve_direct_model", lambda _kind: None)
    matching = _memory(
        "local",
        "H3 多参考视频需要首帧和参考视频合同，匹配模型。",
        applies_when={
            "task_stage": "media_generation",
            "task_family": "short_action_video",
            "modality": "video",
            "node_type": "videoNode",
            "model_id": "minimax-h3",
        },
    )
    incompatible = _memory(
        "local",
        "H3 多参考视频需要首帧和参考视频合同，图片分支。",
        applies_when={
            "task_stage": "media_generation",
            "modality": "image",
            "node_type": "imageNode",
            "model_id": "other-model",
        },
    )
    query = "H3 多参考视频首帧参考视频"
    selected = await memory_index.recall_memories(
        "local",
        "project-a",
        query,
        task_stage="media_generation",
        applicability_context={
            "task_family": "short_action_video",
            "modality": "video",
            "node_type": "videoNode",
            "model_id": "minimax-h3",
        },
    )
    assert any(record.id == matching for record in selected)
    assert all(record.id != incompatible for record in selected)

    other_model = await memory_index.recall_memories(
        "local",
        "project-a",
        query,
        task_stage="media_generation",
        applicability_context={
            "task_family": "short_action_video",
            "modality": "video",
            "node_type": "videoNode",
            "model_id": "seedance",
        },
    )
    assert all(record.id != matching for record in other_model)


def test_episode_applies_only_explicit_used_ids_and_legacy_falls_back(
    isolated_memory,
):
    used_id = _memory("local", "已采用的首帧校验规则。")
    shown_id = _memory("local", "只展示但未采用的调色规则。")
    result = memory_index.record_execution_episode(
        "local",
        project="project-a",
        conversation_id="conversation-a",
        turn_id="turn-a",
        objective="配置视频节点",
        outcome="verified_success",
        verified=True,
        memory_ids=[used_id, shown_id],
        used_memory_ids=[used_id],
        memory_context={"model_id": "minimax-h3", "modality": "video"},
        evidence_ref="workflow:run-a:passed",
    )
    assert result["memory_ids"] == [used_id, shown_id]
    assert result["used_memory_ids"] == [used_id]
    used = memory_index.get_memory("local", used_id)
    shown = memory_index.get_memory("local", shown_id)
    assert used is not None and used.applied_count == 1
    assert shown is not None and shown.applied_count == 0

    legacy_id = _memory("local", "旧 episode 仍需保留兼容。")
    legacy = memory_index.record_execution_episode(
        "local",
        project="project-a",
        conversation_id="conversation-a",
        turn_id="legacy-turn",
        objective="旧版本执行任务",
        memory_ids=[legacy_id],
    )
    assert legacy["used_memory_ids"] == [legacy_id]
    conn = memory_index._connect("local")
    try:
        row = conn.execute(
            "SELECT used_memory_ids_json FROM execution_episodes WHERE turn_id=?",
            ("legacy-turn",),
        ).fetchone()
        assert row is not None and row["used_memory_ids_json"] == ""
    finally:
        conn.close()


def test_negative_verification_is_recorded_without_successful_application(
    isolated_memory,
):
    memory_id = _memory("local", "失败时保留原始模型回执。")
    memory_index.record_execution_episode(
        "local",
        project="project-a",
        conversation_id="conversation-a",
        turn_id="turn-negative",
        objective="执行视频生成并验收",
        memory_ids=[memory_id],
        used_memory_ids=[memory_id],
    )
    result = memory_index.record_execution_episode_verification(
        "local",
        project="project-a",
        task_id="turn-negative",
        outcome="negative",
        evidence_ref="workflow:run-b:failed",
    )
    assert result["used_memory_ids"] == [memory_id]
    saved = memory_index.get_memory("local", memory_id)
    assert saved is not None and saved.applied_count == 0
    row = _receipt_row("local")
    assert row is not None
    assert row["usage_status"] == "verified"
    assert row["outcome"] == "negative"
