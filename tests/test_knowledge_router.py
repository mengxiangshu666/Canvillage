from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from novelvideo.chat import knowledge_router


def test_search_knowledge_federates_local_knowledge_and_obsidian(tmp_path, monkeypatch):
    knowledge_root = tmp_path / "knowledge"
    vault_root = tmp_path / "vault"
    (knowledge_root / "AIGC").mkdir(parents=True)
    (vault_root / "AIGC").mkdir(parents=True)
    (knowledge_root / "AIGC" / "director.md").write_text(
        "# 导演规则\n先读画布事实，再做最小修改并验证回执。\n",
        encoding="utf-8",
    )
    (vault_root / "AIGC" / "memory.md").write_text(
        "---\ntags: AIGC, continuity\n---\n# Vault memory\n角色一致性需要身份锚点和正式媒体证据。\n参考 [[导演规则]]。\n",
        encoding="utf-8",
    )
    (vault_root / "AIGC" / "gateway-token.md").write_text(
        "should never be returned",
        encoding="utf-8",
    )
    monkeypatch.setenv("VILLAGE_CANVAS_KNOWLEDGE_ROOT", str(knowledge_root))
    monkeypatch.setenv("VILLAGE_CANVAS_OBSIDIAN_VAULT", str(vault_root))

    result = asyncio.run(
        knowledge_router.search_knowledge(
            "user-a",
            "project-a",
            "角色一致性 画布 回执",
            sources=("knowledge", "obsidian"),
            limit=10,
        )
    )

    assert result["schema"] == "knowledge.search.v1"
    assert result["sources_used"] == ["knowledge", "obsidian"]
    assert result["retrieval"]["instructions_in_notes"] == "reference_only"
    assert result["evidence_packet"]["schema"] == "evidence.packet.v1"
    assert result["evidence_packet"]["count"] == result["count"]
    assert {item["source"] for item in result["results"]} == {"knowledge", "obsidian"}
    assert all("gateway-token" not in item["path"] for item in result["results"])
    assert all(item["provenance"] == "local_read_only_note" for item in result["results"])
    vault_hit = next(item for item in result["results"] if item["source"] == "obsidian")
    assert "导演规则" in vault_hit["links"]
    assert "AIGC" in vault_hit["tags"]


def test_search_knowledge_keeps_memory_layer_separate(monkeypatch):
    async def fake_packet(username, project, query):
        assert (username, project, query) == ("user-a", "project-a", "连续性")
        return {
            "stage": "creative_planning",
            "memory_ids": [7],
            "records": [
                type(
                    "Record",
                    (),
                    {
                        "id": 7,
                        "kind": "validated_experience",
                        "source_id": "memory-key",
                        "confidence": 0.91,
                        "content": "先锁定身份锚点，再检查正式媒体。",
                        "scope_kind": "professional",
                        "evidence_count": 1,
                        "metadata_json": json.dumps(
                            {
                                "evidence": [
                                    {
                                        "ref": "receipt:freezone_gen:episode-2",
                                        "project": "project-a",
                                        "task_id": "task-episode-2",
                                        "outcome": "positive",
                                        "notes": "两个身份卡和第1集图成功生成第2集。",
                                    },
                                    {
                                        "ref": "receipt:other-project",
                                        "project": "project-b",
                                        "task_id": "private-other-task",
                                        "outcome": "positive",
                                        "notes": "other project",
                                    },
                                ]
                            },
                            ensure_ascii=False,
                        ),
                    },
                )()
            ],
            "used_count": 1,
        }

    monkeypatch.setattr(knowledge_router, "build_knowledge_packet", fake_packet)
    result = asyncio.run(
        knowledge_router.search_knowledge(
            "user-a", "project-a", "连续性", sources=("memory",), limit=5
        )
    )

    assert result["memory_ids"] == [7]
    assert result["sources_used"] == ["memory"]
    assert result["results"][0]["uri"] == "memory://7"
    assert result["results"][0]["evidence"] == [
        {
            "evidence_ref": "receipt:freezone_gen:episode-2",
            "project_id": "project-a",
            "task_id": "task-episode-2",
            "outcome": "positive",
            "notes": "两个身份卡和第1集图成功生成第2集。",
        }
    ]
    assert result["retrieval"]["memory"] == "semantic_plus_lexical_with_evidence"
    assert result["evidence_packet"]["items"][0]["source"] == "memory"


def test_federated_results_deduplicate_mirrored_content_and_keep_provenance():
    result = knowledge_router._dedupe_results(
        [
            {
                "source": "knowledge",
                "title": "导演规则",
                "snippet": "先读画布事实，再验证回执。",
                "score": 0.7,
                "uri": "knowledge://director.md",
            },
            {
                "source": "obsidian",
                "title": "导演规则",
                "snippet": "先读画布事实，再验证回执。",
                "score": 0.8,
                "uri": "obsidian://director.md",
            },
        ]
    )

    assert len(result) == 1
    assert result[0]["source"] == "obsidian"
    assert result[0]["source_aliases"] == ["knowledge", "obsidian"]
    assert result[0]["provenance"] == "federated_deduplicated"


def test_search_knowledge_uses_existing_cognee_project_graph(monkeypatch):
    class FakeStore:
        async def search(self, query, mode="graph", top_k=10):
            assert (query, mode, top_k) == ("身份连续性", "chunks", 5)
            return "角色身份锚点来自项目图谱\n场景关系来自项目图谱"

        async def close(self):
            return None

    async def fake_make_store(username, project):
        assert (username, project) == ("user-a", "project-a")
        return FakeStore()


    monkeypatch.setattr(
        "novelvideo.services.project_resources.make_cognee_store", fake_make_store
    )
    result = asyncio.run(
        knowledge_router.search_knowledge(
            "user-a", "project-a", "身份连续性", sources=("cognee",), limit=5
        )
    )

    assert result["sources_used"] == ["cognee"]
    assert result["retrieval"]["cognee"] == "project_graph_chunks"
    assert len(result["results"]) == 2
    assert all(item["provenance"] == "project_cognee_graph" for item in result["results"])
    assert all(item["source"] == "cognee" for item in result["evidence_packet"]["items"])


def test_search_knowledge_prefers_structured_cognee_provenance(monkeypatch):
    class StructuredStore:
        async def search_with_provenance(self, query, mode="chunks", top_k=10):
            assert (query, mode, top_k) == ("身份连续性", "chunks", 5)
            return [
                {
                    "text": "角色身份锚点来自第1章。",
                    "chunk_id": "chunk-7",
                    "document_id": "document-2",
                    "document_name": "故事原稿",
                    "source_uri": "file:///story.txt",
                    "chunk_index": 3,
                    "score": 0.934,
                    "provenance": "project_cognee_chunk_document",
                }
            ]

        async def search(self, query, mode="graph", top_k=10):
            raise AssertionError("legacy text search should not run when provenance exists")

        async def close(self):
            return None

    async def fake_make_store(username, project):
        return StructuredStore()


    monkeypatch.setattr(
        "novelvideo.services.project_resources.make_cognee_store", fake_make_store
    )
    result = asyncio.run(
        knowledge_router.search_knowledge(
            "user-a", "project-a", "身份连续性", sources=("cognee",), limit=5
        )
    )

    assert result["count"] == 1
    hit = result["results"][0]
    assert hit["uri"] == "cognee://project-a/chunk/chunk-7"
    assert hit["title"] == "故事原稿"
    assert hit["chunk_id"] == "chunk-7"
    assert hit["document_id"] == "document-2"
    assert hit["chunk_index"] == 3
    evidence = result["evidence_packet"]["items"][0]
    assert evidence["source_uri"] == "file:///story.txt"
    assert evidence["chunk_id"] == "chunk-7"
    assert evidence["document_id"] == "document-2"


def test_load_reference_reads_project_scoped_cognee_chunk(monkeypatch):
    class StructuredStore:
        async def load_chunk_reference(self, chunk_id):
            assert chunk_id == "chunk-7"
            return {
                "chunk_id": chunk_id,
                "text": "角色身份锚点来自第1章。",
                "document_id": "document-2",
                "document_name": "故事原稿",
                "source_uri": "file:///story.txt",
                "chunk_index": 3,
            }

        async def close(self):
            return None

    async def fake_make_store(username, project):
        assert (username, project) == ("user-a", "project-a")
        return StructuredStore()


    monkeypatch.setattr(
        "novelvideo.services.project_resources.make_cognee_store", fake_make_store
    )
    result = knowledge_router.load_reference(
        "cognee://project-a/chunk/chunk-7",
        project_id="project-a",
        username="user-a",
    )

    assert result["schema"] == "knowledge.reference.v1"
    assert result["source"] == "cognee"
    assert result["content"] == "角色身份锚点来自第1章。"
    assert result["citation"] == "cognee://project-a/chunk/chunk-7"
    assert result["source_uri"] == "file:///story.txt"


def test_load_reference_rejects_cross_project_cognee_chunk():
    with pytest.raises(ValueError, match="project mismatch"):
        knowledge_router.load_reference(
            "cognee://project-a/chunk/chunk-7",
            project_id="project-b",
            username="user-a",
        )


def test_load_reference_requires_user_for_cognee_chunk():
    with pytest.raises(ValueError, match="authenticated user"):
        knowledge_router.load_reference(
            "cognee://project-a/chunk/chunk-7",
            project_id="project-a",
        )


def test_empty_cognee_graph_is_optional_and_does_not_block_other_sources(monkeypatch):
    class EmptyStore:
        async def search(self, query, mode="graph", top_k=10):
            return "搜索出错: NoDataError: No data found in the system, please add data first. (Status code: 404)"

        async def close(self):
            return None

    async def fake_make_store(username, project):
        return EmptyStore()


    monkeypatch.setattr(
        "novelvideo.services.project_resources.make_cognee_store", fake_make_store
    )
    result = asyncio.run(
        knowledge_router.search_knowledge(
            "user-a", "project-a", "角色连续性", sources=("cognee",), limit=5
        )
    )

    assert result["count"] == 0
    assert result["sources_used"] == []
    assert result["source_errors"] == {}


def test_role_question_uses_project_scoped_cognee_facts(monkeypatch):
    class FakeStore:
        async def search(self, query, mode="graph", top_k=10):
            assert query == "这个角色是谁"
            assert mode == "chunks"
            return "角色：秦王\n别名：玄甲君\n来源：project-character-facts"

        async def close(self):
            return None

    async def fake_make_store(username, project):
        assert username == "user-a"
        assert project == "project-a"
        return FakeStore()


    monkeypatch.setattr(
        "novelvideo.services.project_resources.make_cognee_store", fake_make_store
    )
    result = asyncio.run(
        knowledge_router.search_knowledge(
            "user-a", "project-a", "这个角色是谁", sources=("cognee",), limit=8
        )
    )

    assert result["project_id"] == "project-a"
    assert result["sources_used"] == ["cognee"]
    assert result["count"] == 3
    assert "秦王" in result["results"][0]["snippet"]
    assert all(item["provenance"] == "project_cognee_graph" for item in result["results"])


def test_load_reference_reads_only_searchable_note_and_preserves_citation(tmp_path, monkeypatch):
    knowledge_root = tmp_path / "knowledge"
    vault_root = tmp_path / "vault"
    (knowledge_root / "AIGC").mkdir(parents=True)
    (vault_root / "AIGC").mkdir(parents=True)
    (knowledge_root / "AIGC" / "director.md").write_text(
        "---\ntags: director, AIGC\n---\n# 导演规则\n先读取权威画布事实。\n参考 [[回执合同]]。\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VILLAGE_CANVAS_KNOWLEDGE_ROOT", str(knowledge_root))
    monkeypatch.setenv("VILLAGE_CANVAS_OBSIDIAN_VAULT", str(vault_root))

    result = knowledge_router.load_reference(
        "knowledge://AIGC/director.md", project_id="project-a"
    )

    assert result["schema"] == "knowledge.reference.v1"
    assert result["uri"] == "knowledge://AIGC/director.md"
    assert result["relative_path"] == "AIGC/director.md"
    assert result["project_id"] == "project-a"
    assert "权威画布事实" in result["content"]
    assert result["frontmatter"]["tags"] == "director, AIGC"
    assert result["links"] == ["回执合同"]
    assert result["tags"] == ["AIGC", "director"]
    assert result["citation"] == "knowledge:AIGC/director.md"
    assert result["truncated"] is False


def test_load_reference_rejects_unknown_source_traversal_and_sensitive_names(tmp_path, monkeypatch):
    knowledge_root = tmp_path / "knowledge"
    knowledge_root.mkdir()
    (knowledge_root / "safe.md").write_text("safe", encoding="utf-8")
    (knowledge_root / "api-token.md").write_text("secret", encoding="utf-8")
    monkeypatch.setenv("VILLAGE_CANVAS_KNOWLEDGE_ROOT", str(knowledge_root))

    for uri in (
        "memory://7",
        "knowledge://../safe.md",
        "knowledge://api-token.md",
        "knowledge://safe.md?raw=1",
    ):
        try:
            knowledge_router.load_reference(uri)
        except (ValueError, FileNotFoundError):
            pass
        else:
            raise AssertionError(f"reference should be rejected: {uri}")


def test_load_reference_bounds_large_text(tmp_path, monkeypatch):
    knowledge_root = tmp_path / "knowledge"
    knowledge_root.mkdir()
    (knowledge_root / "large.md").write_text("x" * 100, encoding="utf-8")
    monkeypatch.setenv("VILLAGE_CANVAS_KNOWLEDGE_ROOT", str(knowledge_root))
    monkeypatch.setattr(knowledge_router, "_MAX_REFERENCE_CHARS", 16)

    result = knowledge_router.load_reference("knowledge://large.md")

    assert len(result["content"]) == 16
    assert result["text"] == result["content"]
    assert result["truncated"] is True


def test_load_reference_resolves_scoped_memory_and_preserves_bounded_evidence(monkeypatch):
    record = SimpleNamespace(
        id=374,
        scope_kind="professional",
        scope_id="",
        kind="verified_experience",
        content="身份卡必须与上一集成图一起作为连续性参考。",
        evidence_count=1,
        metadata_json=json.dumps(
            {
                "evidence": [
                    {
                        "ref": "receipt:freezone_gen:episode-2",
                        "project": "project-a",
                        "task_id": "task-episode-2",
                        "outcome": "positive",
                        "notes": "Episode 2 verified.",
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(knowledge_router, "get_memory", lambda username, memory_id: record)

    result = knowledge_router.load_reference(
        "memory://374", project_id="project-a", username="alice"
    )

    assert result["uri"] == "memory://374"
    assert result["citation"] == "memory:374"
    assert result["evidence"][0]["task_id"] == "task-episode-2"
    with pytest.raises(ValueError, match="authenticated user"):
        knowledge_router.load_reference("memory://374", project_id="project-a")


def test_cognee_timeout_does_not_block_federated_search(monkeypatch):
    class SlowStore:
        async def search(self, query, mode="graph", top_k=10):
            await asyncio.sleep(1)
            return "late result"

        async def close(self):
            return None

    async def fake_make_store(username, project):
        return SlowStore()


    monkeypatch.setattr(
        "novelvideo.services.project_resources.make_cognee_store", fake_make_store
    )
    monkeypatch.setattr(knowledge_router, "_COGNEE_TIMEOUT_SECONDS", 0.01)
    result = asyncio.run(
        knowledge_router.search_knowledge(
            "user-a", "project-a", "角色", sources=("cognee",), limit=3
        )
    )

    assert result["count"] == 0
    assert result["sources_used"] == []
    assert result["source_errors"] == {}
    assert "TimeoutError" in result["source_warnings"]["cognee"]
