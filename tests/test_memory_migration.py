import sqlite3

from novelvideo import config
from novelvideo.chat import memory_index
from novelvideo.chat.memory_migration import apply_memory_recompile, plan_memory_recompile


def test_memory_recompile_archives_raw_records_and_keeps_a_backup(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(memory_index, "schedule_memory_embedding", lambda *_: False)

    first = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="learned_rule",
        source="user_explicit",
        source_id="legacy-1",
        content="用户长期要求：觉得每次你跟我说话废话特多，永久记住这点",
        status="confirmed",
        locked=True,
    )
    second = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="learned_rule",
        source="user_explicit",
        source_id="legacy-2",
        content="用户长期要求：说人话，别重复解释",
        status="confirmed",
        locked=True,
    )
    raw_turn = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="turn_observation",
        source="xiaoshu",
        source_id="legacy-turn",
        content="目标：创建一个节点；工具已调用，但没有可复用经验",
        status="candidate",
    )
    memory_index.capture_learning_event(
        "local",
        project="project-a",
        task_id="turn-old",
        event_type="turn_completed",
        content="一次已完成的旧回合",
        evidence_ref="turn-old",
    )
    memory_index.capture_learning_event(
        "local",
        project="project-a",
        task_id="turn-rule",
        event_type="explicit_user_rule",
        content="以后所有项目都要优先保证角色连续性",
        evidence_ref="turn-rule",
    )

    actions = plan_memory_recompile("local")
    assert any(action.object_id == first and action.action == "compile" for action in actions)
    assert any(action.object_id == raw_turn and action.action == "archive_evidence" for action in actions)

    result = apply_memory_recompile("local")

    assert result["compiled_memories"] >= 2
    assert result["archived_evidence"] == 1
    assert result["processed_events"] == 2
    assert result["backup_path"]
    assert sqlite3.connect(result["backup_path"]).execute("SELECT 1").fetchone() == (1,)

    active_rules = memory_index.list_memories(
        "local",
        status="confirmed",
        scope_kind="user",
    )
    assert any("结论先行" in item.content for item in active_rules)
    assert any("角色连续性" in item.content for item in active_rules)
    assert all("用户长期要求" not in item.content for item in active_rules)
    assert memory_index.get_memory("local", first).status == "archived"
    assert memory_index.get_memory("local", second).status == "archived"
    assert memory_index.get_memory("local", raw_turn).status == "archived"
    assert memory_index.memory_stats("local")["pending_events"] == 0


def test_memory_recompile_keeps_uncertain_feedback_as_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(memory_index, "schedule_memory_embedding", lambda *_: False)
    source_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="learned_rule",
        source="xiaoshu_compiler",
        source_id="user:reference.explicit_role_mapping",
        content="你每次都把姥爷跟姥姥的图片引用反了",
        status="confirmed",
        locked=True,
        metadata={"compiled": True, "compiler": "deterministic-v4", "explicit": True},
    )

    result = apply_memory_recompile("local")
    assert result["compiled_memories"] == 1
    active = memory_index.list_memories("local", status="candidate")
    assert any(item.id != source_id and item.kind == "candidate_experience" for item in active)
