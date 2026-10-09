from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

import pytest

from novelvideo.chat.store import (
    DEFAULT_CHAT_CONVERSATION_ID,
    ChatScope,
    ChatStore,
)


@pytest.mark.parametrize("kind", ["asset", "task"])
def test_chat_scope_rejects_unimplemented_kinds(kind: str) -> None:
    with pytest.raises(ValueError, match=f"unsupported chat scope: {kind}"):
        ChatScope.from_payload({"kind": kind, "id": "legacy-id"})

    with pytest.raises(ValueError, match=f"unsupported chat scope: {kind}"):
        ChatScope(kind=kind, id="legacy-id")  # type: ignore[arg-type]


def test_chat_store_receipt_idempotency_is_atomic_under_concurrent_replay(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    store = ChatStore()
    scope = ChatScope(kind="project", id="project-a", canvas_id="canvas-a")
    store.append_message("alice", scope, "assistant", "完成", turn_id="turn-a")
    receipt = {
        "type": "agent.canvas.command.receipt",
        "receiptId": "command-a:result:1:success",
        "commandId": "command-a",
        "stage": "result",
        "success": True,
    }
    barrier = threading.Barrier(2)

    def append_receipt():
        barrier.wait()
        return store.append_ui_event("alice", scope, "turn-a", receipt)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: append_receipt(), range(2)))

    assert {result["id"] for result in results} == {results[0]["id"]}
    assert sorted(result["idempotent_replay"] for result in results) == [False, True]
    with sqlite3.connect(store.db_for("alice", scope)) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM chat_ui_events WHERE receipt_id=?",
                (receipt["receiptId"],),
            ).fetchone()[0]
            == 1
        )


def test_chat_store_replays_bounded_ui_event_tail_by_type(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    store = ChatStore()
    scope = ChatScope(kind="project", id="project-a", canvas_id="canvas-a")
    for index in range(4):
        event_type = "agent.event" if index != 1 else "agent.workflow"
        store.append_ui_event(
            "alice",
            scope,
            f"turn-{index}",
            {
                "type": event_type,
                "event_id": f"event-{index}",
                "agent_event": {"event_id": f"event-{index}", "seq": index},
            },
        )

    replay = store.list_ui_events(
        "alice",
        scope,
        event_type="agent.event",
        limit=2,
    )

    assert [item["event_id"] for item in replay] == ["event-2", "event-3"]
    assert [item["turn_id"] for item in replay] == ["turn-2", "turn-3"]


def test_chat_store_migrates_legacy_schema_and_attaches_turn_events(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    db_path = tmp_path / "alice" / "project-a" / "chat.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE chat_messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              media_json TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO chat_messages(role, content, media_json, created_at) VALUES (?, ?, '[]', ?)",
            ("user", "旧消息", "2026-01-01T00:00:00+00:00"),
        )

    store = ChatStore()
    scope = ChatScope(kind="project", id="project-a")
    store.append_message("alice", scope, "assistant", "已完成", turn_id="turn-1")
    store.append_ui_event(
        "alice",
        scope,
        "turn-1",
        {"type": "canvas_patch", "revision": 2},
    )

    messages = store.list_messages("alice", scope)
    assert [(item["role"], item["content"]) for item in messages] == [
        ("user", "旧消息"),
        ("assistant", "已完成"),
    ]
    assert messages[-1]["turn_id"] == "turn-1"
    assert messages[-1]["ui_events"][0]["revision"] == 2

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(chat_messages)")}
        assert {"turn_id", "metadata_json"} <= columns
        assert conn.execute("SELECT COUNT(*) FROM chat_ui_events").fetchone()[0] == 1


def test_chat_store_never_returns_or_persists_infrastructure_error_as_assistant(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    store = ChatStore()
    scope = ChatScope(kind="project", id="project-a")

    with pytest.raises(ValueError, match="infrastructure"):
        store.append_message(
            "alice",
            scope,
            "assistant",
            "API call failed after 3 retries: HTTP 503: Service temporarily unavailable",
        )

    stored = store.append_message(
        "alice",
        scope,
        "assistant",
        "节点已完成。 API call failed after 3 retries: HTTP 503",
    )
    assert stored["content"] == "节点已完成。 API call failed after 3 retries: HTTP 503"
    assert store.list_messages("alice", scope)[0]["content"] == stored["content"]

    tailed = store.append_message(
        "alice",
        scope,
        "assistant",
        "节点已完成。\nAPI call failed after 3 retries: HTTP 503",
    )
    assert tailed["content"] == "节点已完成。"


def test_chat_store_strips_legacy_director_question_queue_from_metadata(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    store = ChatStore()
    scope = ChatScope(kind="project", id="project-a")
    legacy_metadata = {
        "backend": "director-preflight",
        "director_clarification": {
            "question_id": "audience_or_use",
            "question": "当前问题",
            "suggested_answer": "旧建议",
            "next_questions": ["visual_style", "audio"],
        },
    }

    stored = store.append_message(
        "alice",
        scope,
        "assistant",
        "当前问题",
        metadata=legacy_metadata,
        turn_id="turn-director",
    )
    listed = store.list_messages("alice", scope)

    assert "next_questions" not in stored["metadata"]["director_clarification"]
    assert "suggested_answer" not in stored["metadata"]["director_clarification"]
    assert "next_questions" not in listed[0]["metadata"]["director_clarification"]
    assert "suggested_answer" not in listed[0]["metadata"]["director_clarification"]
    assert listed[0]["metadata"]["director_clarification"]["question_id"] == (
        "audience_or_use"
    )


def test_chat_store_replay_keeps_only_current_director_question(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    store = ChatStore()
    scope = ChatScope(kind="project", id="project-a")
    for index, question in enumerate(("用途？", "风格？", "画幅？", "声音？"), 1):
        store.append_message(
            "alice",
            scope,
            "assistant",
            question,
            turn_id=f"director-{index}",
            metadata={
                "backend": "director-preflight",
                "director_clarification": {
                    "question_id": f"q-{index}",
                    "question": question,
                },
            },
        )

    listed = store.list_messages("alice", scope)

    assert [item["content"] for item in listed] == ["声音？"]


def test_chat_store_replay_hides_superseded_director_questions(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    store = ChatStore()
    scope = ChatScope(kind="project", id="project-a")
    store.append_message(
        "alice",
        scope,
        "assistant",
        "用途？",
        turn_id="director-1",
        metadata={
            "backend": "director-preflight",
            "director_clarification": {"question_id": "audience_or_use"},
        },
    )
    store.append_message("alice", scope, "assistant", "已完成", turn_id="done-1")

    listed = store.list_messages("alice", scope)

    assert [item["content"] for item in listed] == ["已完成"]


def test_chat_store_replay_hides_legacy_director_text_without_metadata(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    store = ChatStore()
    scope = ChatScope(kind="project", id="project-a")
    for index, question in enumerate(
        (
            "这支片主要给谁看、发布在哪里，还是只做内部样片？",
            "你要什么视觉风格和情绪基调？",
            "成片准备使用什么画幅和平台规格？",
            "声音怎么处理：对白、旁白、音乐和字幕分别要不要？",
        ),
        1,
    ):
        store.append_message(
            "alice", scope, "assistant", question, turn_id=f"legacy-{index}"
        )
    assert [item["content"] for item in store.list_messages("alice", scope)] == [
        "声音怎么处理：对白、旁白、音乐和字幕分别要不要？"
    ]


def test_chat_store_upserts_one_assistant_message_per_turn(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    store = ChatStore()
    scope = ChatScope(kind="project", id="project-a", canvas_id="canvas-a")

    first = store.append_message(
        "alice", scope, "assistant", "处理中", turn_id="turn-a"
    )
    final = store.append_message(
        "alice", scope, "assistant", "已经完成", turn_id="turn-a"
    )
    store.append_message("alice", scope, "user", "再说一次", turn_id="turn-a")

    assert final["id"] == first["id"]
    messages = store.list_messages("alice", scope)
    assert [item["role"] for item in messages] == ["assistant", "user"]
    assert messages[0]["content"] == "已经完成"


def test_chat_store_assistant_turn_upsert_is_atomic_under_concurrency(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    store = ChatStore()
    scope = ChatScope(kind="project", id="project-a", canvas_id="canvas-a")
    barrier = threading.Barrier(2)

    def append_reply(content: str):
        barrier.wait()
        return store.append_message(
            "alice", scope, "assistant", content, turn_id="turn-concurrent"
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(append_reply, ["版本 A", "版本 B"]))

    assert len({reply["id"] for reply in replies}) == 1
    messages = store.list_messages("alice", scope)
    assert len(messages) == 1
    assert messages[0]["content"] in {"版本 A", "版本 B"}


def test_chat_store_replay_matching_preserves_semantic_operators():
    from novelvideo.chat import store as chat_store_module

    assert (
        chat_store_module._strip_replayed_assistant_prefix(
            "x > y，因此条件成立。",
            ["x < y"],
        )
        == "x > y，因此条件成立。"
    )


def test_project_chat_scope_isolated_by_canvas_without_moving_legacy_history(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    store = ChatStore()
    default_scope = ChatScope.from_payload({"kind": "project", "id": "project-a"})
    second_scope = ChatScope.from_payload(
        {"kind": "project", "id": "project-a", "canvas_id": "canvas-two"}
    )

    store.append_message("alice", default_scope, "assistant", "默认画布")
    store.append_message("alice", second_scope, "assistant", "第二画布")

    assert store.db_for("alice", default_scope) == (
        tmp_path / "alice" / "project-a" / "chat.db"
    )
    assert store.db_for("alice", second_scope).parent.parent.name == "canvas-chats"
    assert [
        item["content"] for item in store.list_messages("alice", default_scope)
    ] == ["默认画布"]
    assert [item["content"] for item in store.list_messages("alice", second_scope)] == [
        "第二画布"
    ]
    assert second_scope.to_dict()["canvas_id"] == "canvas-two"


def test_chat_store_isolates_conversations_inside_one_canvas(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    store = ChatStore()
    base_scope = ChatScope(kind="project", id="project-a", canvas_id="canvas-a")
    created = store.create_conversation("alice", base_scope, title="第二个任务")
    second_scope = ChatScope(
        kind="project",
        id="project-a",
        canvas_id="canvas-a",
        conversation_id=created["id"],
    )

    store.append_message("alice", base_scope, "user", "默认会话", turn_id="same-turn")
    store.append_message("alice", second_scope, "user", "第二会话", turn_id="same-turn")
    store.append_message("alice", base_scope, "assistant", "默认回复", turn_id="same-turn")
    store.append_message("alice", second_scope, "assistant", "第二回复", turn_id="same-turn")

    assert [item["content"] for item in store.list_messages("alice", base_scope)] == [
        "默认会话",
        "默认回复",
    ]
    assert [item["content"] for item in store.list_messages("alice", second_scope)] == [
        "第二会话",
        "第二回复",
    ]
    conversations = store.list_conversations("alice", base_scope)
    assert {item["id"] for item in conversations} == {
        DEFAULT_CHAT_CONVERSATION_ID,
        created["id"],
    }
    assert next(item for item in conversations if item["id"] == created["id"])[
        "message_count"
    ] == 2


def test_chat_store_deletes_one_conversation_with_messages_and_ui_events(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    store = ChatStore()
    base_scope = ChatScope(kind="project", id="project-a", canvas_id="canvas-a")
    created = store.create_conversation("alice", base_scope, title="待删除")
    deleted_scope = ChatScope(
        kind="project",
        id="project-a",
        canvas_id="canvas-a",
        conversation_id=created["id"],
    )
    store.append_message("alice", base_scope, "user", "保留消息", turn_id="keep-turn")
    store.append_message("alice", deleted_scope, "user", "删除消息", turn_id="delete-turn")
    store.append_ui_event(
        "alice",
        deleted_scope,
        "delete-turn",
        {"type": "agent.canvas.command.receipt", "receiptId": "receipt-delete"},
    )

    result = store.delete_conversation("alice", deleted_scope)

    assert result == {
        "id": created["id"],
        "deleted": True,
        "message_count": 1,
        "ui_event_count": 1,
    }
    assert {item["id"] for item in store.list_conversations("alice", base_scope)} == {
        DEFAULT_CHAT_CONVERSATION_ID,
    }
    assert [item["content"] for item in store.list_messages("alice", base_scope)] == [
        "保留消息"
    ]


def test_chat_store_receipts_are_idempotent_per_conversation(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    store = ChatStore()
    first_scope = ChatScope(
        kind="project",
        id="project-a",
        conversation_id="conversation-a",
    )
    second_scope = ChatScope(
        kind="project",
        id="project-a",
        conversation_id="conversation-b",
    )
    receipt = {
        "type": "agent.canvas.command.receipt",
        "receiptId": "shared-receipt",
    }

    first = store.append_ui_event("alice", first_scope, "shared-turn", receipt)
    second = store.append_ui_event("alice", second_scope, "shared-turn", receipt)

    assert first["id"] != second["id"]
    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is False


def test_chat_store_migrates_legacy_rows_into_default_conversation(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    db_path = tmp_path / "alice" / "project-a" / "chat.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE chat_messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              media_json TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO chat_messages(role, content, created_at) VALUES ('user', '旧会话', ?)",
            ("2026-01-01T00:00:00+00:00",),
        )

    store = ChatStore()
    scope = ChatScope(kind="project", id="project-a")
    assert store.list_messages("alice", scope)[0]["content"] == "旧会话"
    conversations = store.list_conversations("alice", scope)
    assert conversations[0]["id"] == DEFAULT_CHAT_CONVERSATION_ID
    assert conversations[0]["message_count"] == 1
