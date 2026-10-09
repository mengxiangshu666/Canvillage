from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from novelvideo import config
from novelvideo.chat import memory_index
from novelvideo.chat.memory_feedback import (
    classify_execution_feedback,
    has_actionable_teaching_signal,
    is_non_actionable_positive_feedback,
)


@pytest.fixture
def isolated_memory(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "state"


def _direct_embedding(**overrides):
    values = {
        "catalog_id": "direct/embedding-a",
        "upstream_model": "vendor-embedding-a",
        "base_url": "https://embedding-a.example/v1",
        "api_key": "test-key",
        "protocol": "openai-embeddings",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _fingerprint(direct) -> str:
    return memory_index._embedding_config_fingerprint(direct)


def _store_test_embeddings(
    username: str,
    memory_ids: list[int],
    direct,
) -> None:
    conn = memory_index._connect(username)
    try:
        conn.executemany(
            "UPDATE memory_entries SET embedding_model=?, "
            "embedding_dimension=2, embedding=? WHERE id=?",
            [
                (
                    _fingerprint(direct),
                    memory_index._pack_vector((1.0, 0.0)),
                    memory_id,
                )
                for memory_id in memory_ids
            ],
        )
        conn.commit()
    finally:
        conn.close()


def test_embedding_config_fingerprint_excludes_key_and_normalizes_base_url():
    first = _direct_embedding(
        base_url="https://embedding-a.example/v1/",
        api_key="first-secret",
    )
    second = _direct_embedding(
        base_url="https://embedding-a.example/v1",
        api_key="second-secret",
    )

    first_fingerprint = _fingerprint(first)

    assert first_fingerprint == _fingerprint(second)
    assert "first-secret" not in first_fingerprint
    assert "second-secret" not in first_fingerprint


@pytest.mark.asyncio
async def test_background_embedding_failure_is_consumed_and_task_is_released(caplog):
    async def fail_embedding():
        raise PermissionError("simulated cache contention")

    caplog.set_level("INFO")
    memory_index._schedule(fail_embedding())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert not memory_index._BACKGROUND_TASKS
    assert "background memory embedding was skipped: PermissionError" in caplog.text


def test_memory_store_keeps_complete_long_content_without_character_cap(
    isolated_memory,
):
    content = "长期导演经验" * 5_000
    memory_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="preference",
        source="test",
        content=content,
    )

    saved = memory_index.get_memory("local", memory_id)

    assert saved is not None
    assert saved.content == content
    assert len(saved.content) > 20_000


def test_memory_store_redacts_provider_keys(isolated_memory):
    memory_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="preference",
        source="test",
        content="以后使用 sk-1234567890abcdefghijklmnop 这个渠道",
    )

    saved = memory_index.get_memory("local", memory_id)

    assert saved is not None
    assert "sk-1234567890abcdefghijklmnop" not in saved.content
    assert "<redacted-key>" in saved.content


def test_research_result_is_project_scoped_idempotent_and_secret_free(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    scheduled = []
    monkeypatch.setattr(
        memory_index,
        "schedule_memory_embedding",
        lambda username, memory_id: scheduled.append((username, memory_id)),
    )
    result = {
        "answer": "优先先做角色设定，再拆分镜头。",
        "research_contract": {
            "schema": "research.contract.v1",
            "mode": "quick",
            "counter_search": "off",
            "query_roles": ["primary"],
        },
        "research_assessment": {
            "schema": "research.assessment.v1",
            "quality": "adequate",
            "sufficient": True,
            "independent_domain_count": 1,
            "independent_domains": ["example.test"],
        },
        "results": [
            {
                "title": "AIGC 导演方法",
                "url": "https://example.test/guide?token=secret-value",
                "content": "先锁定角色一致性，再生成代表镜头。",
                "published_date": "2026-08-08",
            }
        ],
    }

    first = memory_index.remember_research_result(
        "local",
        "project-a",
        query="怎样提高短剧角色一致性",
        result=result,
        canvas_id="canvas-a",
    )
    second = memory_index.remember_research_result(
        "local",
        "project-a",
        query="怎样提高短剧角色一致性",
        result={**result, "cached": True},
        canvas_id="canvas-a",
    )

    saved = memory_index.get_memory("local", first)
    assert first == second
    assert memory_index.memory_stats("local")["total"] == 1
    assert saved is not None
    assert saved.kind == "research_digest"
    assert saved.source == "xiaoshu_research_digest"
    assert saved.scope_id == "project-a"
    assert "角色一致性" in saved.content
    assert "secret-value" not in saved.content
    saved_metadata = json.loads(saved.metadata_json)
    assert saved_metadata["research_contract"]["mode"] == "quick"
    assert saved_metadata["research_assessment"]["sufficient"] is True
    assert scheduled == [("local", first), ("local", first)]
    archived = memory_index.list_memories("local", status="archived", kind="research_source")
    assert len(archived) == 1
    assert archived[0].source == "tavily"


@pytest.mark.asyncio
async def test_request_embedding_without_model_does_not_open_http_client(
    monkeypatch: pytest.MonkeyPatch,
):
    def unexpected_client(*_args, **_kwargs):
        raise AssertionError(
            "HTTP client must not be created without an embedding model"
        )

    monkeypatch.setattr(memory_index, "resolve_direct_model", lambda _kind: None)
    monkeypatch.setattr(memory_index.httpx, "AsyncClient", unexpected_client)

    assert await memory_index._request_embedding("不发起网络请求") is None


@pytest.mark.asyncio
async def test_request_embedding_returns_config_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
):
    direct = _direct_embedding(api_key="request-secret")
    observed = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"embedding": [1.0, 0.0]}]}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            observed.update(url=url, headers=headers, json=json)
            return FakeResponse()

    monkeypatch.setattr(memory_index, "resolve_direct_model", lambda _kind: direct)
    monkeypatch.setattr(memory_index, "resolved_direct_embedding_dimensions", lambda _model: 2)
    monkeypatch.setattr(memory_index.httpx, "AsyncClient", FakeClient)

    result = await memory_index._request_embedding("镜头节奏")

    assert result == (_fingerprint(direct), (1.0, 0.0))
    assert result[0] != direct.catalog_id
    assert direct.api_key not in result[0]
    assert observed["url"] == "https://embedding-a.example/v1/embeddings"
    assert "dimensions" not in observed["json"]


@pytest.mark.asyncio
async def test_vector_recall_prefers_semantically_matching_memory(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    fruit = memory_index.upsert_memory(
        "local",
        scope_kind="project",
        project="fruit-film",
        kind="workflow",
        source="test",
        content="水果短剧应该在前两秒进入冲突",
    )
    portrait = memory_index.upsert_memory(
        "local",
        scope_kind="project",
        project="fruit-film",
        kind="workflow",
        source="test",
        content="人物肖像需要保持稳定正脸",
    )

    direct = _direct_embedding(catalog_id="direct/test-embedding")

    async def fake_embedding(text: str):
        vector = (1.0, 0.0) if "节奏" in text or "水果" in text else (0.0, 1.0)
        return _fingerprint(direct), vector

    monkeypatch.setattr(
        memory_index,
        "resolve_direct_model",
        lambda _kind: direct,
    )
    monkeypatch.setattr(memory_index, "_request_embedding", fake_embedding)
    await memory_index.ensure_memory_embedding("local", fruit)
    await memory_index.ensure_memory_embedding("local", portrait)

    recalled = await memory_index.recall_memories(
        "local", "fruit-film", "水果节奏怎么做"
    )

    assert recalled[0].id == fruit


@pytest.mark.asyncio
async def test_ensure_embedding_skips_request_for_same_direct_model(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    memory_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="preference",
        source="test",
        content="保持角色外观一致",
    )
    direct = _direct_embedding()
    requests = []

    async def fake_embedding(text: str):
        requests.append(text)
        return _fingerprint(direct), (1.0, 0.0)

    monkeypatch.setattr(memory_index, "resolve_direct_model", lambda _kind: direct)
    monkeypatch.setattr(memory_index, "_request_embedding", fake_embedding)

    assert await memory_index.ensure_memory_embedding("local", memory_id)
    assert await memory_index.ensure_memory_embedding("local", memory_id)
    assert requests == ["保持角色外观一致"]


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("upstream_model", "vendor-embedding-b"),
        ("base_url", "https://embedding-b.example/v1"),
        ("protocol", "custom-http"),
    ],
)
@pytest.mark.asyncio
async def test_ensure_embedding_rebuilds_after_same_registry_config_change(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
    changed_field: str,
    changed_value: str,
):
    memory_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="preference",
        source="test",
        content="镜头先交代人物关系",
    )
    active = {"direct": _direct_embedding()}
    requests = []

    async def fake_embedding(_text: str):
        fingerprint = _fingerprint(active["direct"])
        requests.append(fingerprint)
        return fingerprint, (float(len(requests)), 0.0)

    monkeypatch.setattr(
        memory_index,
        "resolve_direct_model",
        lambda _kind: active["direct"],
    )
    monkeypatch.setattr(memory_index, "_request_embedding", fake_embedding)

    first_fingerprint = _fingerprint(active["direct"])
    assert await memory_index.ensure_memory_embedding("local", memory_id)
    active["direct"] = _direct_embedding(**{changed_field: changed_value})
    second_fingerprint = _fingerprint(active["direct"])
    assert await memory_index.ensure_memory_embedding("local", memory_id)

    conn = memory_index._connect("local")
    try:
        row = conn.execute(
            "SELECT embedding_model, embedding_dimension, embedding "
            "FROM memory_entries WHERE id=?",
            (memory_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert first_fingerprint != second_fingerprint
    assert requests == [first_fingerprint, second_fingerprint]
    assert row["embedding_model"] == second_fingerprint
    assert memory_index._unpack_vector(
        row["embedding"], row["embedding_dimension"]
    ) == (
        2.0,
        0.0,
    )


@pytest.mark.asyncio
async def test_ensure_embedding_rebuilds_after_dimension_contract_change(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    memory_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="preference",
        source="test",
        content="保持场景轴线一致",
    )
    direct = _direct_embedding()
    dimensions = {"value": 2}
    requests = []

    monkeypatch.setattr(
        memory_index,
        "resolved_direct_embedding_dimensions",
        lambda _model: dimensions["value"],
    )
    monkeypatch.setattr(memory_index, "resolve_direct_model", lambda _kind: direct)

    async def fake_embedding(_text: str):
        current_dimensions = dimensions["value"]
        requests.append(current_dimensions)
        vector = tuple(float(index) for index in range(current_dimensions))
        return _fingerprint(direct), vector

    monkeypatch.setattr(memory_index, "_request_embedding", fake_embedding)

    first_fingerprint = _fingerprint(direct)
    assert await memory_index.ensure_memory_embedding("local", memory_id)
    dimensions["value"] = 3
    second_fingerprint = _fingerprint(direct)
    assert await memory_index.ensure_memory_embedding("local", memory_id)

    conn = memory_index._connect("local")
    try:
        row = conn.execute(
            "SELECT embedding_model, embedding_dimension FROM memory_entries WHERE id=?",
            (memory_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert first_fingerprint != second_fingerprint
    assert requests == [2, 3]
    assert row["embedding_model"] == second_fingerprint
    assert row["embedding_dimension"] == 3


@pytest.mark.asyncio
async def test_ensure_embedding_rebuilds_legacy_catalog_id_record(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    memory_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="preference",
        source="test",
        content="旧向量需要重建",
    )
    direct = _direct_embedding()
    conn = memory_index._connect("local")
    try:
        conn.execute(
            "UPDATE memory_entries SET embedding_model=?, embedding_dimension=?, embedding=? "
            "WHERE id=?",
            (direct.catalog_id, 2, memory_index._pack_vector((0.0, 1.0)), memory_id),
        )
        conn.commit()
    finally:
        conn.close()
    requests = []

    async def fake_embedding(text: str):
        requests.append(text)
        return _fingerprint(direct), (1.0, 0.0)

    monkeypatch.setattr(memory_index, "resolve_direct_model", lambda _kind: direct)
    monkeypatch.setattr(memory_index, "_request_embedding", fake_embedding)

    assert await memory_index.ensure_memory_embedding("local", memory_id)
    assert requests == ["旧向量需要重建"]


@pytest.mark.asyncio
async def test_ensure_embedding_discards_result_if_same_registry_config_changes_during_request(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    memory_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="preference",
        source="test",
        content="长镜头保持空间连续",
    )
    active = {"direct": _direct_embedding()}

    async def fake_embedding(_text: str):
        requested_fingerprint = _fingerprint(active["direct"])
        active["direct"] = _direct_embedding(base_url="https://embedding-b.example/v1")
        return requested_fingerprint, (1.0, 0.0)

    monkeypatch.setattr(
        memory_index,
        "resolve_direct_model",
        lambda _kind: active["direct"],
    )
    monkeypatch.setattr(memory_index, "_request_embedding", fake_embedding)

    assert not await memory_index.ensure_memory_embedding("local", memory_id)

    conn = memory_index._connect("local")
    try:
        row = conn.execute(
            "SELECT embedding_model, embedding_dimension, embedding "
            "FROM memory_entries WHERE id=?",
            (memory_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["embedding_model"] == ""
    assert row["embedding_dimension"] == 0
    assert row["embedding"] is None


@pytest.mark.asyncio
async def test_ensure_embedding_does_not_write_stale_vector_after_content_update(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    memory_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="preference",
        source="test",
        source_id="content-race",
        content="旧内容",
    )
    active = {"direct": _direct_embedding()}

    async def fake_embedding(text: str):
        direct = active["direct"]
        if direct.upstream_model == "vendor-embedding-b":
            updated_id = memory_index.upsert_memory(
                "local",
                scope_kind="user",
                project=None,
                kind="preference",
                source="test",
                source_id="content-race",
                content="并发写入的新内容",
            )
            assert updated_id == memory_id
        return _fingerprint(direct), (1.0, 0.0) if text == "旧内容" else (0.0, 1.0)

    monkeypatch.setattr(
        memory_index,
        "resolve_direct_model",
        lambda _kind: active["direct"],
    )
    monkeypatch.setattr(memory_index, "_request_embedding", fake_embedding)

    assert await memory_index.ensure_memory_embedding("local", memory_id)
    active["direct"] = _direct_embedding(upstream_model="vendor-embedding-b")
    assert not await memory_index.ensure_memory_embedding("local", memory_id)

    conn = memory_index._connect("local")
    try:
        row = conn.execute(
            """
            SELECT content, embedding_model, embedding_dimension, embedding
              FROM memory_entries
             WHERE id=?
            """,
            (memory_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["content"] == "并发写入的新内容"
    assert row["embedding_model"] == ""
    assert row["embedding_dimension"] == 0
    assert row["embedding"] is None


@pytest.mark.asyncio
async def test_recall_refreshes_stale_embeddings_once_after_config_change(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    production_scheduler = memory_index.schedule_memory_embedding
    monkeypatch.setattr(
        memory_index,
        "schedule_memory_embedding",
        lambda _username, _memory_id: False,
    )
    memory_id = memory_index.upsert_memory(
        "local",
        scope_kind="project",
        project="demo",
        kind="decision",
        source="test",
        content="cinematic portrait continuity",
    )
    monkeypatch.setattr(
        memory_index,
        "schedule_memory_embedding",
        production_scheduler,
    )
    active = {"direct": _direct_embedding()}
    refresh_started = asyncio.Event()
    allow_refresh = asyncio.Event()
    refresh_requests = []

    async def fake_embedding(text: str):
        fingerprint = _fingerprint(active["direct"])
        if text == "cinematic portrait continuity" and refresh_requests:
            refresh_requests.append(fingerprint)
            refresh_started.set()
            await allow_refresh.wait()
        elif text == "cinematic portrait continuity":
            refresh_requests.append(fingerprint)
        return fingerprint, (1.0, 0.0)

    monkeypatch.setattr(
        memory_index,
        "resolve_direct_model",
        lambda _kind: active["direct"],
    )
    monkeypatch.setattr(memory_index, "_request_embedding", fake_embedding)

    assert await memory_index.ensure_memory_embedding("local", memory_id)
    previous_fingerprint = _fingerprint(active["direct"])
    active["direct"] = _direct_embedding(
        base_url="https://embedding-b.example/v1",
        upstream_model="vendor-embedding-b",
    )
    current_fingerprint = _fingerprint(active["direct"])

    assert not await memory_index.recall_memories("local", "demo", "identity lock")
    await refresh_started.wait()
    assert not await memory_index.recall_memories("local", "demo", "identity lock")
    assert refresh_requests == [previous_fingerprint, current_fingerprint]

    allow_refresh.set()
    await asyncio.gather(*tuple(memory_index._BACKGROUND_TASKS))

    recalled = await memory_index.recall_memories(
        "local",
        "demo",
        "identity lock",
    )
    assert [record.id for record in recalled] == [memory_id]

    conn = memory_index._connect("local")
    try:
        row = conn.execute(
            "SELECT embedding_model FROM memory_entries WHERE id=?",
            (memory_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["embedding_model"] == current_fingerprint


@pytest.mark.asyncio
async def test_new_confirmed_rule_wins_first_recall_while_embedding_is_pending(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        memory_index,
        "schedule_memory_embedding",
        lambda _username, _memory_id: False,
    )
    direct = _direct_embedding()
    competitors = [
        memory_index.upsert_memory(
            "local",
            scope_kind="user",
            project=None,
            kind="decision",
            source="test",
            content=f"旧的语义经验 {index}",
        )
        for index in range(3)
    ]
    _store_test_embeddings("local", competitors, direct)
    marker = "ZC_MEM_EVAL_20260827"
    rule_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="learned_rule",
        source="xiaoshu_compiler",
        content=(
            f"创建咖啡包装概念时必须包含 {marker}，"
            "并复用画布中的咖啡品牌主Logo。"
        ),
        status="confirmed",
        metadata={
            "memory_schema": "xiaoshu.memory.v3",
            "rule_type": "general_rule",
            "action": ["复用并连接咖啡品牌主Logo"],
        },
    )

    async def current_embedding(_text: str):
        return _fingerprint(direct), (1.0, 0.0)

    monkeypatch.setattr(memory_index, "resolve_direct_model", lambda _kind: direct)
    monkeypatch.setattr(memory_index, "_request_embedding", current_embedding)

    packet = await memory_index.build_knowledge_packet(
        "local",
        "new-project",
        f"{marker}：在当前画布创建咖啡包装概念图片节点",
    )

    assert rule_id in packet["memory_ids"]
    assert rule_id in packet["execution_rule_ids"]


@pytest.mark.asyncio
async def test_promoted_rule_is_recalled_immediately_in_a_new_project(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(memory_index, "resolve_direct_model", lambda _kind: None)
    monkeypatch.setattr(
        memory_index,
        "schedule_memory_embedding",
        lambda _username, _memory_id: False,
    )
    candidate_id = memory_index.upsert_memory(
        "local",
        scope_kind="project",
        project="teaching-project",
        kind="candidate_experience",
        source="xiaoshu_compiler",
        content="咖啡包装概念必须复用并连接咖啡品牌主Logo",
        status="candidate",
        metadata={"candidate_recall": True},
    )

    promoted = memory_index.promote_memory("local", candidate_id)
    packet = await memory_index.build_knowledge_packet(
        "local",
        "new-project",
        "创建咖啡包装概念节点并复用咖啡品牌主Logo",
    )

    assert promoted is not None
    assert promoted.id in packet["memory_ids"]


@pytest.mark.asyncio
async def test_stale_confirmed_rule_keeps_lexical_recall_during_refresh(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        memory_index,
        "schedule_memory_embedding",
        lambda _username, _memory_id: False,
    )
    previous = _direct_embedding()
    current = _direct_embedding(
        base_url="https://embedding-b.example/v1",
        upstream_model="vendor-embedding-b",
    )
    competitors = [
        memory_index.upsert_memory(
            "local",
            scope_kind="user",
            project=None,
            kind="decision",
            source="test",
            content=f"当前向量经验 {index}",
        )
        for index in range(3)
    ]
    rule_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="learned_rule",
        source="test",
        content="identity lock 必须在角色生成前执行",
        status="confirmed",
    )
    _store_test_embeddings("local", competitors, current)
    _store_test_embeddings("local", [rule_id], previous)

    async def current_embedding(_text: str):
        return _fingerprint(current), (1.0, 0.0)

    monkeypatch.setattr(memory_index, "resolve_direct_model", lambda _kind: current)
    monkeypatch.setattr(memory_index, "_request_embedding", current_embedding)

    recalled = await memory_index.recall_memories(
        "local",
        "new-project",
        "identity lock before rendering",
        limit=3,
    )

    assert rule_id in [record.id for record in recalled]


@pytest.mark.asyncio
async def test_recall_bounds_stale_embedding_refresh_batch(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        memory_index,
        "schedule_memory_embedding",
        lambda _username, _memory_id: False,
    )
    memory_ids = [
        memory_index.upsert_memory(
            "local",
            scope_kind="project",
            project="demo",
            kind="decision",
            source="test",
            content=f"stale memory {index}",
        )
        for index in range(memory_index.DEFAULT_RECALL_EMBEDDING_REFRESH_LIMIT + 2)
    ]
    previous = _direct_embedding()
    current = _direct_embedding(
        base_url="https://embedding-b.example/v1",
        upstream_model="vendor-embedding-b",
    )
    conn = memory_index._connect("local")
    try:
        conn.executemany(
            "UPDATE memory_entries SET embedding_model=?, "
            "embedding_dimension=2, embedding=? WHERE id=?",
            [
                (
                    _fingerprint(previous),
                    memory_index._pack_vector((1.0, 0.0)),
                    memory_id,
                )
                for memory_id in memory_ids
            ],
        )
        conn.commit()
    finally:
        conn.close()

    scheduled = []

    async def current_embedding(_text: str):
        return _fingerprint(current), (1.0, 0.0)

    monkeypatch.setattr(memory_index, "resolve_direct_model", lambda _kind: current)
    monkeypatch.setattr(
        memory_index,
        "_request_embedding",
        current_embedding,
    )
    monkeypatch.setattr(
        memory_index,
        "schedule_memory_embedding",
        lambda username, memory_id: scheduled.append((username, memory_id)) or True,
    )

    await memory_index.recall_memories("local", "demo", "semantic query")

    assert len(scheduled) == memory_index.DEFAULT_RECALL_EMBEDDING_REFRESH_LIMIT
    assert {memory_id for _, memory_id in scheduled}.issubset(memory_ids)


@pytest.mark.asyncio
async def test_recall_isolates_project_memory_but_keeps_user_preferences(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(memory_index, "_request_embedding", lambda _text: None)
    memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="preference",
        source="test",
        content="所有项目都偏好电影感灯光",
    )
    memory_index.upsert_memory(
        "local",
        scope_kind="project",
        project="project-a",
        kind="decision",
        source="test",
        content="项目A使用红色水果角色",
    )
    memory_index.upsert_memory(
        "local",
        scope_kind="project",
        project="project-b",
        kind="decision",
        source="test",
        content="项目B使用蓝色机器人角色",
    )

    recalled = await memory_index.recall_memories(
        "local",
        "project-a",
        "电影感灯光和水果角色",
    )
    contents = [record.content for record in recalled]

    assert any("电影感灯光" in content for content in contents)
    assert any("红色水果角色" in content for content in contents)
    assert all("蓝色机器人角色" not in content for content in contents)


@pytest.mark.asyncio
async def test_embedding_failure_falls_back_to_lexical_recall(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    memory_index.upsert_memory(
        "local",
        scope_kind="project",
        project="demo",
        kind="failure",
        source="test",
        content="视频任务出现超时后从上次回执继续",
    )

    async def failed_embedding(_text: str):
        return None

    monkeypatch.setattr(memory_index, "_request_embedding", failed_embedding)
    recalled = await memory_index.recall_memories("local", "demo", "视频超时怎么继续")

    assert recalled
    assert "上次回执继续" in recalled[0].content


@pytest.mark.asyncio
async def test_candidate_memory_is_bound_to_origin_conversation_until_promoted(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(memory_index, "schedule_memory_embedding", lambda *_: False)
    candidate_id = memory_index.upsert_memory(
        "local",
        scope_kind="professional",
        project=None,
        kind="candidate_experience",
        source="test",
        content="动作镜头先写动作因果，再写运镜和结束状态。",
        status="candidate",
        confidence=0.65,
        metadata={
            "candidate_recall": True,
            "candidate_conversation_id": "conversation-a",
        },
        evidence_count=0,
    )

    same_conversation = await memory_index.recall_memories(
        "local",
        "project-a",
        "动作因果运镜结束状态",
        conversation_id="conversation-a",
    )
    other_conversation = await memory_index.recall_memories(
        "local",
        "project-a",
        "动作因果运镜结束状态",
        conversation_id="conversation-b",
    )

    assert [record.id for record in same_conversation] == [candidate_id]
    assert all(record.id != candidate_id for record in other_conversation)

    promoted = memory_index.promote_memory("local", candidate_id)
    assert promoted is not None and promoted.status == "confirmed"
    after_promotion = await memory_index.recall_memories(
        "local",
        "project-b",
        "动作因果运镜结束状态",
        conversation_id="conversation-b",
    )
    assert any(record.id == promoted.id for record in after_promotion)


@pytest.mark.asyncio
async def test_knowledge_packet_forwards_conversation_scope(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, str] = {}

    async def fake_recall(*_args, **kwargs):
        captured["conversation_id"] = str(kwargs["conversation_id"])
        return []

    monkeypatch.setattr(memory_index, "recall_memories", fake_recall)
    packet = await memory_index.build_knowledge_packet(
        "local",
        "project-a",
        "规划一个动作镜头",
        conversation_id="conversation-z",
    )

    assert packet["used_count"] == 0
    assert captured == {"conversation_id": "conversation-z"}


def test_explicit_long_term_rule_waits_for_growth_distillation(
    isolated_memory,
):
    memory_id = memory_index.remember_successful_turn(
        "local",
        "project-a",
        user_text="以后所有项目都要优先保证角色连续性",
        assistant_text="已按角色锁定流程执行",
        turn_id="turn-a",
    )

    assert memory_id == 0
    assert memory_index.list_memories("local") == []
    conn = sqlite3.connect(memory_index._db_path("local"))
    row = conn.execute(
        "SELECT status, decision, reason FROM learning_events WHERE task_id=?",
        ("turn-a",),
    ).fetchone()
    conn.close()
    assert row == ("evidence_only", "evidence_only", "awaiting_growth_distillation")


def test_unresolved_explicit_feedback_stays_out_of_formal_memory(isolated_memory):
    memory_id = memory_index.remember_successful_turn(
        "local",
        "project-a",
        user_text="以后不能再犯这种低级错误",
        assistant_text="收到",
        turn_id="turn-unresolved",
    )

    assert memory_id == 0
    assert memory_index.memory_stats("local")["total"] == 0
    assert memory_index.memory_stats("local")["pending_events"] == 0
    conn = sqlite3.connect(memory_index._db_path("local"))
    row = conn.execute(
        "SELECT status, decision, reason FROM learning_events WHERE task_id=?",
        ("turn-unresolved",),
    ).fetchone()
    conn.close()
    assert row == ("evidence_only", "evidence_only", "awaiting_growth_distillation")


def test_semantic_feedback_variants_remain_raw_events_until_distilled(isolated_memory):
    first = memory_index.remember_successful_turn(
        "local",
        "project-a",
        user_text="每次对话都为了快速生成冲动，什么都没有问就开始搭节点",
        assistant_text="收到",
        turn_id="turn-first",
    )
    second = memory_index.remember_successful_turn(
        "local",
        "project-b",
        user_text="每次跟你交流都很急躁，什么都没问就直接开始搭建节点",
        assistant_text="收到",
        turn_id="turn-second",
    )

    assert first == 0
    assert second == 0
    assert memory_index.list_memories("local") == []
    conn = sqlite3.connect(memory_index._db_path("local"))
    rows = conn.execute(
        "SELECT decision, reason FROM learning_events ORDER BY id"
    ).fetchall()
    conn.close()
    assert rows == [
        ("evidence_only", "awaiting_growth_distillation"),
        ("evidence_only", "awaiting_growth_distillation"),
    ]


@pytest.mark.asyncio
async def test_successful_turn_without_reusable_rule_stays_only_as_task_evidence(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    async def no_embedding(_text: str):
        return None

    monkeypatch.setattr(memory_index, "_request_embedding", no_embedding)
    memory_index.remember_successful_turn(
        "local",
        "project-a",
        user_text="制作水果短剧时先拆分冲突镜头",
        assistant_text="按冲突、升级、反转创建了三个节点",
        turn_id="turn-success",
    )

    recalled = await memory_index.recall_memories(
        "local",
        "project-b",
        "水果短剧冲突镜头怎么拆",
    )

    assert not recalled
    assert not memory_index.list_memories("local", status="candidate")
    assert memory_index.memory_stats("local")["pending_events"] == 0


def test_memory_uses_portable_knowledge_directory(isolated_memory):
    memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="preference",
        source="test",
        content="偏好克制的悬疑氛围",
    )

    assert (
        isolated_memory / "local" / "knowledge" / "knowledge.db"
    ).is_file()


@pytest.mark.asyncio
async def test_project_only_instruction_is_not_promoted_to_user_memory(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(memory_index, "schedule_memory_embedding", lambda *_: False)

    memory_index.remember_successful_turn(
        "local",
        "project-a",
        user_text="这个项目以后都使用冷色调",
        assistant_text="已记录当前项目色调",
        turn_id="turn-project-only",
    )

    assert not memory_index.list_memories(
        "local",
        status="confirmed",
        scope_kind="user",
    )


def test_canvas_request_default_switch_is_not_misclassified_as_experience(isolated_memory):
    memory_index.remember_successful_turn(
        "local",
        "project-a",
        user_text=(
            "创建一个 12 秒视频草稿节点，9:16，带声音开关默认打开，"
            "不要提交视频生成任务"
        ),
        assistant_text="已创建视频草稿节点",
        turn_id="turn-canvas-default",
        canvas_summary='{"tool_activity":true}',
    )

    assert not memory_index.list_memories(
        "local",
        status="confirmed",
        scope_kind="user",
    )
    assert not memory_index.list_memories("local", status="candidate")
    assert memory_index.memory_stats("local")["pending_events"] == 0


def test_successful_turn_requires_authoritative_verification_before_distilling(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(memory_index, "schedule_memory_embedding", lambda *_: False)
    summary = (
        '{"tool_activity":true,"verified":true,'
        '"receipt":{"command_id":"cmd-1","revision":2,"applied_ops":1}}'
    )

    memory_id = memory_index.remember_successful_turn(
        "local",
        "project-a",
        user_text="这个动作方案更稳定，建议先固定角色身份再拆镜头",
        assistant_text="已通过画布回执验收",
        turn_id="turn-verified",
        canvas_summary=summary,
    )

    assert memory_id == 0
    assert not memory_index.list_memories("local")
    conn = sqlite3.connect(memory_index._db_path("local"))
    row = conn.execute(
        "SELECT decision, reason FROM learning_events WHERE task_id=?",
        ("turn-verified",),
    ).fetchone()
    conn.close()
    assert row == ("evidence_only", "awaiting_growth_distillation")


def test_verified_canvas_receipt_counts_application_without_tool_display_text(
    isolated_memory,
):
    memory_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="learned_rule",
        source="test",
        content="结构化画布操作完成后核对权威回执。",
        status="confirmed",
    )

    memory_index.remember_successful_turn(
        "local",
        "project-a",
        user_text="创建一个文字节点并返回真实画布回执",
        assistant_text="已完成",
        turn_id="turn-receipt-only",
        recalled_memory_ids=[memory_id],
        canvas_summary=(
            '{"tool_activity":false,"verified":true,'
            '"verification_source":"canvas_receipt",'
            '"receipt":{"server_applied":true,"revision":2,"applied_ops":1}}'
        ),
    )

    saved = memory_index.get_memory("local", memory_id)
    assert saved is not None
    assert saved.applied_count == 1
    assert saved.last_verified_at


def test_candidate_can_be_promoted_edited_locked_and_deleted(isolated_memory):
    memory_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="experience",
        source="test",
        content="候选经验",
        status="candidate",
        confidence=0.4,
    )

    promoted = memory_index.promote_memory("local", memory_id, locked=True)
    assert promoted is not None
    assert promoted.status == "confirmed"
    assert promoted.locked is True
    assert promoted.confidence == 1.0
    assert promoted.promoted_from_id == memory_id
    archived_source = memory_index.get_memory("local", memory_id)
    assert archived_source is not None
    assert archived_source.status == "archived"
    assert archived_source.supersedes_id == promoted.id

    updated = memory_index.update_memory(
        "local",
        promoted.id,
        content="已确认的经验",
        applies_when={"genre": "suspense"},
    )
    assert updated is not None
    assert updated.content == "已确认的经验"
    assert "suspense" in updated.applies_when
    assert memory_index.delete_memory("local", promoted.id) is True
    assert memory_index.get_memory("local", promoted.id) is None


def test_memory_store_does_not_promote_learned_rules_on_reopen(isolated_memory):
    memory_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="learned_rule",
        source="test",
        content="一条未经验证的候选规则",
        status="candidate",
        confidence=0.4,
        locked=False,
    )

    memory_index._connect("local").close()
    saved = memory_index.get_memory("local", memory_id)
    assert saved is not None
    assert saved.status == "candidate"
    assert saved.locked is False
    assert saved.confidence == 0.4


def test_memory_metadata_uses_v3_rule_fields(isolated_memory):
    compilation = memory_index.compile_memory("执行节点前核对节点参数")
    memory_id = memory_index.save_compiled_memory(
        "local", compilation, status="candidate", locked=False, schedule_embedding=False
    )
    saved = memory_index.get_memory("local", memory_id)
    assert saved is not None
    assert '"memory_schema": "xiaoshu.memory.v3"' in saved.metadata_json
    assert '"rule_type": "prompt_convention"' in saved.metadata_json
    assert '"executable": false' in saved.metadata_json


@pytest.mark.asyncio
async def test_knowledge_packet_is_stage_bounded_and_uses_confirmed_records(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(memory_index, "schedule_memory_embedding", lambda *_: False)
    for index in range(6):
        memory_index.upsert_memory(
            "local",
            scope_kind="user",
            project=None,
            kind="preference",
            source="test",
            source_id=f"rule-{index}",
            content=f"节点操作规则 {index}",
            status="confirmed",
            locked=index == 0,
        )
    memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="experience",
        source="test",
        content="尚未确认的节点候选经验",
        status="candidate",
    )

    packet = await memory_index.build_knowledge_packet(
        "local",
        "project-a",
        "删除这个节点",
    )

    assert packet["stage"] == "simple_canvas"
    assert packet["used_count"] <= 3
    assert "尚未确认" not in packet["context"]
    assert packet["context"].startswith("[XIAOSHU_KNOWLEDGE_PACKET]")
    assert packet["budget"] == {"record_limit": 3, "char_limit": 1800}
    assert packet["rendered_chars"] == len(packet["context"])
    assert packet["sources"] == ["test"]


@pytest.mark.asyncio
async def test_knowledge_packet_separates_v3_execution_rules_from_legacy_context(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(memory_index, "schedule_memory_embedding", lambda *_: False)
    compilation = memory_index.compile_memory("以后视频提示词不要写时长秒数")
    memory_index.save_compiled_memory(
        "local", compilation, status="confirmed", locked=True, schedule_embedding=False
    )

    packet = await memory_index.build_knowledge_packet(
        "local", "project-a", "视频提示词时长"
    )

    assert "XIAOSHU_EXECUTION_RULES" in packet["execution_context"]
    assert "video_prompt.remove_redundant_duration" in packet["execution_context"]
    assert packet["execution_rule_ids"]
    assert "XIAOSHU_EXECUTION_RULES" not in packet["context"]


@pytest.mark.asyncio
async def test_knowledge_packet_profile_sources_do_not_consume_recall_budget(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(memory_index, "resolve_direct_model", lambda _kind: None)
    for kind in memory_index.PROFILE_MEMORY_KINDS:
        memory_index.upsert_memory(
            "local",
            scope_kind="user",
            project=None,
            kind=kind,
            source="profile_file",
            source_id=kind,
            content="节点操作规则来自常驻画像，不应重复进入语义知识包。",
            status="confirmed",
            confidence=1.0,
        )
    experience_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="validated_experience",
        source="xiaoshu_verifier",
        content="节点操作验收后必须核对 revision 和 applied_ops。",
        status="validated",
        confidence=1.0,
    )

    packet = await memory_index.build_knowledge_packet(
        "local", "project-a", "创建节点后如何验收节点操作"
    )

    assert packet["memory_ids"] == [experience_id]
    assert packet["sources"] == ["xiaoshu_verifier"]
    assert "常驻画像" not in packet["context"]


@pytest.mark.asyncio
async def test_knowledge_packet_keeps_locked_rules_relevant_and_project_scoped(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(memory_index, "resolve_direct_model", lambda _kind: None)
    relevant_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="preference",
        source="test",
        content="心理悬疑偏好克制留白、异常细节和单一光源，避免血腥堆砌。",
        status="confirmed",
        confidence=1.0,
        locked=True,
        applies_when={"task_stage": "creative_planning"},
    )
    irrelevant_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="learned_rule",
        source="test",
        content="角色四视图必须使用浅灰背景并保持三视图比例一致。",
        status="confirmed",
        confidence=1.0,
        locked=True,
    )
    private_id = memory_index.upsert_memory(
        "local",
        scope_kind="project",
        project="project-a",
        kind="project_fact",
        source="test",
        content="项目A古寺中的提灯少女发现佛像流泪。",
        status="confirmed",
        confidence=0.95,
    )

    project_b = await memory_index.build_knowledge_packet(
        "local",
        "project-b",
        "为无人医院走廊设计克制留白、异常细节和单一光源的心理悬疑短片",
    )
    project_a = await memory_index.build_knowledge_packet(
        "local",
        "project-a",
        "继续设计古寺提灯少女发现佛像流泪的心理悬疑短片",
    )

    assert relevant_id in project_b["memory_ids"]
    assert irrelevant_id not in project_b["memory_ids"]
    assert private_id not in project_b["memory_ids"]
    assert private_id in project_a["memory_ids"]
    assert project_b["scope_counts"]["project"] == 0


def test_project_purge_removes_private_memory_but_keeps_user_rules(isolated_memory):
    user_rule = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="learned_rule",
        source="test",
        content="所有项目都保持真实回执",
        status="confirmed",
        locked=True,
    )
    project_memory = memory_index.upsert_memory(
        "local",
        scope_kind="project",
        project="project-a",
        kind="decision",
        source="test",
        content="古寺少女角色设定",
    )
    memory_index.capture_learning_event(
        "local",
        project="project-a",
        event_type="turn_completed",
        content="古寺项目事件",
    )

    cleaned = memory_index.purge_project_memories("local", "project-a")

    assert cleaned == {"memories": 1, "events": 1, "episodes": 0, "feedback": 0}
    assert memory_index.get_memory("local", project_memory) is None
    assert memory_index.get_memory("local", user_rule) is not None


def test_small_talk_does_not_create_fake_learning(isolated_memory):
    memory_id = memory_index.remember_successful_turn(
        "local",
        "project-a",
        user_text="嗨",
        assistant_text="你好",
        turn_id="turn-small-talk",
        canvas_summary='{"tool_activity":false}',
    )

    assert memory_id == 0
    assert memory_index.memory_stats("local")["total"] == 0
    assert memory_index.memory_stats("local")["pending_events"] == 0


def test_reusable_experience_keeps_three_unique_positive_evidence_items_as_candidate(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(memory_index, "schedule_memory_embedding", lambda *_: False)
    content = "动作场景先固定角色身份和场景空间锚点，再拆分连续镜头"

    first = memory_index.record_candidate_experience(
        "local",
        project="project-a",
        content=content,
        evidence_ref="receipt-a",
        task_id="task-a",
        applies_when={"task_stage": "creative_planning"},
    )
    duplicate = memory_index.record_candidate_experience(
        "local",
        project="project-a",
        content=content,
        evidence_ref="receipt-a",
        task_id="task-a",
        applies_when={"task_stage": "creative_planning"},
    )
    second = memory_index.record_candidate_experience(
        "local",
        project="project-b",
        content=content,
        evidence_ref="receipt-b",
        task_id="task-b",
        applies_when={"task_stage": "creative_planning"},
    )
    third = memory_index.record_candidate_experience(
        "local",
        project="project-c",
        content=content,
        evidence_ref="receipt-c",
        task_id="task-c",
        applies_when={"task_stage": "creative_planning"},
    )

    assert first is not None and first.status == "candidate"
    assert duplicate is not None and duplicate.positive_count == 1
    assert second is not None and second.status == "candidate"
    assert third is not None
    assert third.id == first.id
    assert third.status == "candidate"
    assert third.kind == "candidate_experience"
    assert third.evidence_count == 3
    assert third.positive_count == 3
    assert third.last_verified_at


def test_reusable_experience_requires_evidence_from_multiple_projects(
    isolated_memory,
):
    content = "动作场景先固定角色身份和场景空间锚点，再拆分连续镜头"
    record = None
    for index in range(1, 4):
        record = memory_index.record_candidate_experience(
            "local",
            project="project-a",
            content=content,
            evidence_ref=f"receipt-{index}",
            task_id=f"task-{index}",
        )

    assert record is not None
    assert record.positive_count == 3
    assert record.status == "candidate"
    assert '"positive_projects": 1' in record.metadata_json


def test_verified_turn_counts_injected_memory_application(isolated_memory):
    memory_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="learned_rule",
        source="test",
        content="节点写入后核对 revision 和 applied_ops。",
        status="confirmed",
    )

    updated = memory_index.record_memory_application(
        "local",
        [memory_id, memory_id, 0],
        verified=True,
    )

    assert updated == 1
    saved = memory_index.get_memory("local", memory_id)
    assert saved is not None
    assert saved.applied_count == 1
    assert saved.last_verified_at


@pytest.mark.asyncio
async def test_unpromoted_candidate_experience_is_not_recalled_cross_project(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(memory_index, "resolve_direct_model", lambda _kind: None)
    content = "动作场景先固定角色身份和场景空间锚点，再拆分连续镜头"
    for index, project in enumerate(("project-a", "project-b", "project-c"), start=1):
        memory_index.record_candidate_experience(
            "local",
            project=project,
            content=content,
            evidence_ref=f"receipt-{index}",
            task_id=f"task-{index}",
        )

    recalled = await memory_index.recall_memories(
        "local",
        "project-d",
        "动作镜头如何保证角色和空间连续",
    )

    assert all(record.kind != "candidate_experience" for record in recalled)


def test_project_purge_removes_candidate_evidence_without_erasing_other_projects(
    isolated_memory,
):
    content = "人物出场前先建立可复用的视觉身份锚点"
    first = memory_index.record_candidate_experience(
        "local",
        project="project-a",
        content=content,
        evidence_ref="receipt-a",
    )
    second = memory_index.record_candidate_experience(
        "local",
        project="project-b",
        content=content,
        evidence_ref="receipt-b",
    )
    assert first is not None and second is not None

    memory_index.purge_project_memories("local", "project-a")
    remaining = memory_index.get_memory("local", first.id)

    assert remaining is not None
    assert remaining.status == "candidate"
    assert remaining.evidence_count == 1
    assert remaining.positive_count == 1


def test_user_promotion_turns_candidate_into_cross_project_rule(isolated_memory):
    candidate = memory_index.record_candidate_experience(
        "local",
        project="project-a",
        content="分镜完成必须检查动作方向和轴线连续性",
        evidence_ref="receipt-a",
    )
    assert candidate is not None

    promoted = memory_index.promote_memory("local", candidate.id, locked=True)

    assert promoted is not None
    assert promoted.scope_kind == "user"
    assert promoted.kind == "learned_rule"
    assert promoted.status == "confirmed"
    assert promoted.locked is True
    assert promoted.version == candidate.version + 1


def test_fts_content_drift_is_rebuilt_before_memory_updates(isolated_memory):
    memory_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="preference",
        source="test",
        content="索引真实内容",
    )
    path = isolated_memory / "local" / "knowledge" / "knowledge.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        INSERT INTO memory_entries_fts(memory_entries_fts, rowid, content)
        VALUES('delete', ?, ?)
        """,
        (memory_id, "索引真实内容"),
    )
    conn.execute(
        "INSERT INTO memory_entries_fts(rowid, content) VALUES (?, ?)",
        (memory_id, "错误索引内容"),
    )
    conn.commit()
    conn.close()

    memory_index.memory_stats("local")

    conn = sqlite3.connect(path)
    matched = conn.execute(
        "SELECT COUNT(*) FROM memory_entries_fts WHERE memory_entries_fts MATCH ?",
        ("索引真实内容",),
    ).fetchone()[0]
    conn.close()
    assert matched == 1


def test_long_but_single_canvas_command_still_uses_fast_memory_budget():
    stage = memory_index.knowledge_task_stage(
        "请在当前隔离画布新增且只新增一个文字节点，标题为闭环基线，"
        "正文为真实工具回执才算完成。这是简单画布操作，直接执行并核对"
        "节点、revision 和回执，不启动任何媒体任务。"
    )

    assert stage == "simple_canvas"
    assert memory_index.knowledge_budget_for_stage(stage) == (3, 1_800)


def test_serial_continuity_dry_run_is_not_misclassified_by_negated_canvas_actions():
    stage = memory_index.knowledge_task_stage(
        "继续制作《拾光邮局》第3集，先找角色身份卡、上一集成图和连载经验。"
        "这是 dry-run，不要生成图片，不要创建或修改节点。"
    )

    assert stage == "media_generation"
    assert memory_index.knowledge_budget_for_stage(stage) == (8, 6_000)


def test_serial_continuity_classifier_is_reusable_by_runtime_evidence_gate():
    assert memory_index.is_serial_continuity_task(
        "继续制作《拾光邮局》第3集，核对身份卡、上一集和正史。"
    )
    assert not memory_index.is_serial_continuity_task(
        "第2集男主的眉疤和外套在三个镜头中不断变化，请逐镜标出视觉漂移。"
    )
    assert memory_index.is_serial_continuity_task(
        "这部12集短剧改了七轮，旧钥匙伏笔也和第8集冲突，请建立事实账本。"
    )
    assert not memory_index.is_serial_continuity_task(
        "第3集茶馆对峙要预演：三个人围桌，请落成可执行的3D场景数据。"
    )
    assert not memory_index.is_serial_continuity_task(
        "第3集第5到7场的剧本已经定稿，请把它翻译成逐镜合同。"
    )
    assert not memory_index.is_serial_continuity_task(
        "成片已经导出，请检查缺镜、音画同步、字幕和角色连续性。"
    )
    assert not memory_index.is_serial_continuity_task("把当前选中节点向右移动一点。")


def test_natural_feedback_classifier_requires_an_explicit_evaluation():
    assert classify_execution_feedback("可以") is None
    assert classify_execution_feedback("好的，继续") is None
    assert classify_execution_feedback("这个很好，保留这个").outcome == "positive"
    correction = classify_execution_feedback(
        "参数不对，视频节点执行前应该先核对模型参数，再重新做"
    )
    assert correction is not None
    assert correction.outcome == "negative"
    assert correction.actionable_correction is True


def test_natural_feedback_classifier_captures_memory_regressions_without_promoting_praise():
    for text in (
        "你又忘了，之前已经教过你了，打戏提示词必须按时间轴拆解",
        "老是记不住，视频镜头应该先写清楚动作和镜头衔接",
        "这版还是不行，角色参考图要按 asset ID 显式映射",
    ):
        signal = classify_execution_feedback(text)
        assert signal is not None
        assert signal.outcome == "negative"
        assert signal.actionable_correction is True

    assert is_non_actionable_positive_feedback("这次生成的视频特别棒，记住这个")
    assert not has_actionable_teaching_signal("这次生成的视频特别棒，记住这个")
    assert has_actionable_teaching_signal("这次不错，记住这个提示词框架")
    assert has_actionable_teaching_signal("以后写打戏都采用这个提示词框架")


def test_praise_does_not_become_an_executable_rule_without_a_teaching_action(
    isolated_memory,
):
    memory_id = memory_index.remember_successful_turn(
        "local",
        "project-a",
        user_text="这次生成的视频特别棒，记住这个",
        assistant_text="已记录本次成功反馈。",
        turn_id="turn-praise-only",
    )

    assert memory_id == 0
    assert not memory_index.list_memories("local", scope_kind="user")
    conn = sqlite3.connect(memory_index._db_path("local"))
    events = conn.execute(
        "SELECT content FROM learning_events WHERE project_id=?", ("project-a",)
    ).fetchall()
    conn.close()
    assert not any("这次生成的视频特别棒" in str(event[0]) for event in events)


@pytest.mark.asyncio
async def test_historical_praise_learned_rule_is_excluded_from_recall(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(memory_index, "resolve_direct_model", lambda _kind: None)
    praise_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="learned_rule",
        source="legacy",
        content="这次生成的视频特别棒。",
        status="confirmed",
        locked=True,
    )

    recalled = await memory_index.recall_memories(
        "local",
        "project-a",
        "这次生成的视频特别棒",
    )

    assert all(record.id != praise_id for record in recalled)


def test_explicit_prompt_teaching_stays_raw_until_growth_model_runs(isolated_memory):
    memory_id = memory_index.remember_successful_turn(
        "local",
        "project-a",
        user_text="以后写打戏都采用这个提示词框架，记住这个方法",
        assistant_text="我会按时间轴、动作因果链和镜头承接提炼这套方法。",
        turn_id="turn-prompt-teaching",
        canvas_summary='{"tool_activity":false,"verified":false}',
    )

    assert memory_id == 0
    assert memory_index.list_memories("local") == []
    conn = sqlite3.connect(memory_index._db_path("local"))
    row = conn.execute(
        "SELECT event_type, status, decision, reason FROM learning_events WHERE task_id=?",
        ("turn-prompt-teaching",),
    ).fetchone()
    conn.close()
    assert row == (
        "prompt_teaching_candidate",
        "evidence_only",
        "evidence_only",
        "awaiting_growth_distillation",
    )


def test_explicit_prompt_teaching_does_not_create_session_scoped_memory(isolated_memory):
    memory_id = memory_index.remember_successful_turn(
        "local",
        "project-a",
        user_text="以后写打戏都采用这个提示词框架，记住这个方法",
        assistant_text="已提炼为可执行的时间轴和动作因果链。",
        turn_id="turn-prompt-teaching-scoped",
        conversation_id="conversation-a",
        canvas_summary='{"tool_activity":false,"verified":false}',
    )

    assert memory_id == 0
    assert memory_index.list_memories("local") == []

    conn = sqlite3.connect(memory_index._db_path("local"))
    events = conn.execute(
        "SELECT event_type, status, decision, memory_id, content "
        "FROM learning_events WHERE project_id=?",
        ("project-a",),
    ).fetchall()
    conn.close()
    assert any(
        event[0] == "prompt_teaching_candidate"
        and event[1] == "evidence_only"
        and event[2] == "evidence_only"
        and event[3] == 0
        and "提示词框架" in str(event[4])
        for event in events
    )
    assert not memory_index.list_memories("local", status="confirmed")


def test_project_prompt_teaching_stays_raw_for_project_distillation(isolated_memory):
    memory_id = memory_index.remember_successful_turn(
        "local",
        "project-a",
        user_text="这个项目以后都采用这套打戏提示词框架，记住这个方法",
        assistant_text="已记录本项目的动作镜头方法。",
        turn_id="turn-project-prompt-teaching",
        canvas_summary='{"tool_activity":false,"verified":false}',
    )

    assert memory_id == 0
    assert memory_index.list_memories("local") == []
    conn = sqlite3.connect(memory_index._db_path("local"))
    row = conn.execute(
        "SELECT project_id, event_type, status, decision FROM learning_events WHERE task_id=?",
        ("turn-project-prompt-teaching",),
    ).fetchone()
    conn.close()
    assert row == (
        "project-a",
        "prompt_teaching_candidate",
        "evidence_only",
        "evidence_only",
    )


def test_llm_growth_recipe_is_persisted_as_candidate_only(isolated_memory, monkeypatch):
    monkeypatch.setattr(memory_index, "schedule_memory_embedding", lambda *_: False)
    candidate = SimpleNamespace(
        title="短时长电影化近身打戏",
        task_family="short_action_video",
        summary="用时间轴和动作因果链组织短时长打戏。",
        reusable_principles=["每个动作导致下一步身体状态变化"],
        trigger_conditions=["短时长多人近身打斗，需要逐秒组织动作因果"],
        prompt_structure=["时长与叙事总纲", "逐秒动作链", "镜头与动力学", "负面规约"],
        slots=[{"name": "角色", "purpose": "可替换的角色与参考资产", "required": True}],
        execution_actions=["先锁定角色和场景参考资产"],
        avoid=["不要把雨夜古刹固化到其他项目"],
        transferable_elements=["时间轴", "动作因果链", "连续性约束"],
        project_specific_elements=["雨夜古刹", "黑袍刀客"],
        validation_checks=[
            {"check_id": "timeline.coverage", "condition": "覆盖完整时长"}
        ],
        confidence=0.88,
    )
    result = SimpleNamespace(
        decision="candidate",
        candidate=candidate,
        feedback_kind="positive",
        reason="用户明确采用框架",
    )

    saved = memory_index.save_growth_distillation_candidate(
        "local",
        project="project-a",
        turn_id="turn-growth",
        result=result,
    )

    assert saved is not None
    assert saved.kind == "candidate_experience"
    assert saved.scope_kind == "professional"
    assert saved.status == "candidate"
    metadata = json.loads(saved.metadata_json)
    assert metadata["distillation_level"] == "llm_candidate"
    assert metadata["recipe"]["title"] == "短时长电影化近身打戏"
    assert len(metadata["candidate_content_sha256"]) == 64
    assert metadata["source_provenance"]["kind"] == "growth_distillation_event"
    assert metadata["source_provenance"]["project"] == "project-a"
    assert metadata["source_provenance"]["turn_id"] == "turn-growth"
    assert metadata["source_provenance"]["conversation_id"] == "main"
    assert metadata["prompt_structure"] == [
        "时长与叙事总纲",
        "逐秒动作链",
        "镜头与动力学",
        "负面规约",
    ]
    assert metadata["trigger_conditions"] == [
        "短时长多人近身打斗，需要逐秒组织动作因果"
    ]
    applies_when = json.loads(saved.applies_when)
    assert applies_when == {
        "task_family": "short_action_video",
        "trigger_conditions": ["短时长多人近身打斗，需要逐秒组织动作因果"],
    }
    assert "触发：短时长多人近身打斗，需要逐秒组织动作因果" in saved.content
    assert "结构：时长与叙事总纲 → 逐秒动作链 → 镜头与动力学 → 负面规约" in saved.content
    assert "执行：先锁定角色和场景参考资产" in saved.content
    assert "可迁移：时间轴；动作因果链；连续性约束" in saved.content
    assert "雨夜古刹" not in saved.content
    assert not memory_index.list_memories("local", status="confirmed", kind="candidate_experience")


def test_positive_feedback_binds_to_the_previous_episode_and_is_idempotent(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(memory_index, "schedule_memory_embedding", lambda *_: False)
    rule_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="learned_rule",
        source="test",
        content="画布写入后核对 revision 和真实回执。",
        status="confirmed",
    )
    episode = memory_index.record_execution_episode(
        "local",
        project="project-a",
        conversation_id="conversation-a",
        canvas_id="canvas-a",
        turn_id="turn-a",
        objective="新增一个文字节点并核对画布回执",
        response_summary="已新增文字节点，revision 为 2。",
        outcome="verified_success",
        verified=True,
        memory_ids=[rule_id],
        evidence_ref="command:one",
    )

    feedback = memory_index.apply_execution_feedback(
        "local",
        project="project-a",
        conversation_id="conversation-a",
        canvas_id="canvas-a",
        feedback_turn_id="turn-feedback",
        feedback_text="这个很好，保留这个",
    )
    duplicate = memory_index.apply_execution_feedback(
        "local",
        project="project-a",
        conversation_id="conversation-a",
        canvas_id="canvas-a",
        feedback_turn_id="turn-feedback",
        feedback_text="这个很好，保留这个",
    )

    assert episode["status"] == "recorded"
    assert feedback["status"] == "recorded"
    assert feedback["outcome"] == "positive"
    assert feedback["preceding_episode"]["objective"] == "新增一个文字节点并核对画布回执"
    assert feedback["preceding_episode"]["response_summary"] == "已新增文字节点，revision 为 2。"
    assert duplicate["status"] == "duplicate"
    assert duplicate["preceding_episode"]["episode_id"] == feedback["episode_id"]
    saved_rule = memory_index.get_memory("local", rule_id)
    assert saved_rule is not None and saved_rule.positive_count == 1
    examples = memory_index.list_memories(
        "local", status="confirmed", kind="episodic_example"
    )
    assert len(examples) == 1
    assert "新增一个文字节点" in examples[0].content
    stats = memory_index.memory_stats("local")
    assert stats["episode_count"] == 1
    assert stats["feedback_count"] == 1
    assert stats["positive_feedback"] == 1


def test_feedback_is_isolated_by_conversation_and_canvas(isolated_memory):
    memory_index.record_execution_episode(
        "local",
        project="project-a",
        conversation_id="conversation-a",
        canvas_id="canvas-a",
        turn_id="turn-a",
        objective="调整当前画布的镜头节奏",
        response_summary="已完成节奏调整。",
    )

    receipt = memory_index.apply_execution_feedback(
        "local",
        project="project-a",
        conversation_id="conversation-b",
        canvas_id="canvas-b",
        feedback_turn_id="turn-b",
        feedback_text="这个很好，保留这个",
    )

    assert receipt == {"status": "ignored", "reason": "previous_episode_missing"}
    assert memory_index.memory_stats("local")["feedback_count"] == 0


def test_negative_feedback_creates_failure_episode_without_bypassing_distiller(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(memory_index, "schedule_memory_embedding", lambda *_: False)
    used_memory_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="learned_rule",
        source="test",
        content="视频任务提交前核对首帧是否已经上传。",
        status="confirmed",
    )
    memory_index.record_execution_episode(
        "local",
        project="project-a",
        conversation_id="conversation-a",
        canvas_id="canvas-a",
        turn_id="turn-a",
        objective="配置一个首帧图生视频节点",
        response_summary="已按默认参数完成配置。",
        memory_ids=[used_memory_id],
    )

    feedback = memory_index.apply_execution_feedback(
        "local",
        project="project-a",
        conversation_id="conversation-a",
        canvas_id="canvas-a",
        feedback_turn_id="turn-feedback",
        feedback_text="参数不对，视频节点执行前应该先核对模型参数，再重新做",
    )

    assert feedback["status"] == "recorded"
    assert feedback["outcome"] == "negative"
    assert feedback["candidate_memory_id"] == 0
    used = memory_index.get_memory("local", used_memory_id)
    assert used is not None and used.negative_count == 1
    failures = memory_index.list_memories(
        "local", status="confirmed", kind="failure_episode"
    )
    assert len(failures) == 1
    assert failures[0].positive_count == 0
    assert failures[0].negative_count == 1
    candidates = memory_index.list_memories(
        "local", status="candidate", kind="candidate_experience"
    )
    assert candidates == []


def test_execution_episode_reconciles_deferred_workflow_verifier_evidence(
    isolated_memory,
):
    memory_id = memory_index.upsert_memory(
        "local",
        scope_kind="user",
        project=None,
        kind="learned_rule",
        source="test",
        content="画布任务完成后读取服务器回执。",
        status="confirmed",
    )
    memory_index.capture_learning_event(
        "local",
        project="project-a",
        task_id="turn-a",
        event_type="verification_passed",
        subject="workflow_step:verify",
        content="WorkflowRun verifier passed",
        evidence_ref="workflow:run-a:event-a:verify:passed",
    )

    receipt = memory_index.record_execution_episode(
        "local",
        project="project-a",
        conversation_id="conversation-a",
        turn_id="turn-a",
        objective="执行并验收一个画布步骤",
        response_summary="已完成。",
        run_id="run-a",
        memory_ids=[memory_id],
    )

    assert receipt["reconciled_evidence"] == 1
    saved = memory_index.get_memory("local", memory_id)
    assert saved is not None and saved.positive_count == 1
    stats = memory_index.memory_stats("local")
    assert stats["pending_events"] == 0
    assert stats["verifier_events"] == 1
    assert stats["workflow_evidence"] == 1


def test_execution_episode_reconciles_same_turn_candidate_without_recall_ids(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(memory_index, "schedule_memory_embedding", lambda *_: False)
    candidate = memory_index.record_candidate_experience(
        "local",
        project="project-a",
        task_id="turn-a",
        content="分镜执行后必须读取服务器 verifier 回执，再沉淀镜头方法。",
        evidence_ref="turn:turn-a",
    )
    assert candidate is not None
    memory_index.capture_learning_event(
        "local",
        project="project-a",
        task_id="turn-a",
        event_type="verification_passed",
        subject="workflow_step:verify",
        content="WorkflowRun verifier passed",
        evidence_ref="workflow:run-a:event-a:verify:passed",
    )

    receipt = memory_index.record_execution_episode(
        "local",
        project="project-a",
        conversation_id="conversation-a",
        turn_id="turn-a",
        objective="执行并验收一个画布步骤",
        response_summary="已完成。",
        run_id="run-a",
        memory_ids=[],
    )

    assert receipt["reconciled_evidence"] == 1
    saved = memory_index.get_memory("local", candidate.id)
    assert saved is not None
    assert saved.evidence_count == 2
    assert saved.positive_count == 2
    stats = memory_index.memory_stats("local")
    assert stats["pending_events"] == 0
    assert stats["workflow_evidence"] == 1


@pytest.mark.asyncio
async def test_research_digest_is_only_recalled_for_research_tasks(
    isolated_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(memory_index, "resolve_direct_model", lambda _kind: None)
    memory_index.remember_research_result(
        "local",
        "project-a",
        query="查询最新视频模型首帧参数",
        result={
            "answer": "该模型支持首帧参考。",
            "results": [
                {
                    "title": "官方模型文档",
                    "url": "https://example.test/model",
                    "content": "首帧参数名称为 first_frame。",
                }
            ],
        },
    )

    creative = await memory_index.recall_memories(
        "local",
        "project-a",
        "帮我设计一个悬疑角色",
        task_stage="creative_planning",
    )
    research = await memory_index.recall_memories(
        "local",
        "project-a",
        "查询视频模型首帧参数的官方资料",
        task_stage="research",
    )

    assert all(record.kind != "research_digest" for record in creative)
    assert any(record.kind == "research_digest" for record in research)
