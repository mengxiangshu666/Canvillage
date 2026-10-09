from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from novelvideo.cognee import store as store_module


def test_cognee_payload_mapping_accepts_json_graph_properties():
    assert store_module._coerce_mapping(
        '{"raw_data_location":"file:///story.txt","name":"故事原稿"}'
    ) == {
        "raw_data_location": "file:///story.txt",
        "name": "故事原稿",
    }


@pytest.mark.asyncio
async def test_search_with_provenance_keeps_vector_id_and_document_mapping(monkeypatch):
    store = store_module.CogneeStore.__new__(store_module.CogneeStore)
    store.dataset_name = "novelvideo_project-a"
    store._set_cognee_context = lambda: None
    store.embedding_model_scope = lambda: nullcontext()

    vector_engine = SimpleNamespace(
        search=lambda *args, **kwargs: None,
    )

    async def vector_search(*args, **kwargs):
        assert args == ("DocumentChunk_text", "角色连续性")
        assert kwargs == {"limit": 3, "include_payload": True}
        return [
            SimpleNamespace(
                id="chunk-7",
                score=0.934,
                payload={"text": "角色身份锚点来自第1章。", "chunk_index": 3},
            )
        ]

    vector_engine.search = vector_search

    async def fake_get_unified_engine():
        return SimpleNamespace(vector=vector_engine)

    async def fake_provenance(chunk_ids):
        assert chunk_ids == ["chunk-7"]
        return {
            "chunk-7": {
                "document_id": "document-2",
                "document_name": "故事原稿",
                "source_uri": "file:///story.txt",
                "chunk_index": 3,
                "relationship": "part_of",
            }
        }

    monkeypatch.setattr(store, "_resolve_chunk_provenance", fake_provenance)
    monkeypatch.setattr(
        "cognee.infrastructure.databases.unified.get_unified_engine",
        fake_get_unified_engine,
    )

    result = await store.search_with_provenance("角色连续性", top_k=3)

    assert result == [
        {
            "text": "角色身份锚点来自第1章。",
            "snippet": "角色身份锚点来自第1章。",
            "chunk_id": "chunk-7",
            "score": 0.934,
            "chunk_index": 3,
            "dataset_name": "novelvideo_project-a",
            "mode": "chunks",
            "provenance": "project_cognee_chunk_document",
            "document_id": "document-2",
            "document_name": "故事原稿",
            "source_uri": "file:///story.txt",
            "relationship": "part_of",
        }
    ]


@pytest.mark.asyncio
async def test_search_with_provenance_keeps_chunk_when_graph_lookup_fails(monkeypatch):
    store = store_module.CogneeStore.__new__(store_module.CogneeStore)
    store.dataset_name = "novelvideo_project-a"
    store._set_cognee_context = lambda: None
    store.embedding_model_scope = lambda: nullcontext()

    async def vector_search(*args, **kwargs):
        return [
            SimpleNamespace(
                id="chunk-8",
                score=0.7,
                payload={"text": "图边缺失时仍应返回片段。"},
            )
        ]

    async def fake_get_unified_engine():
        return SimpleNamespace(vector=SimpleNamespace(search=vector_search))

    async def failing_provenance(chunk_ids):
        raise RuntimeError("graph unavailable")

    monkeypatch.setattr(store, "_resolve_chunk_provenance", failing_provenance)
    monkeypatch.setattr(
        "cognee.infrastructure.databases.unified.get_unified_engine",
        fake_get_unified_engine,
    )

    result = await store.search_with_provenance("图边缺失", top_k=1)

    assert len(result) == 1
    assert result[0]["chunk_id"] == "chunk-8"
    assert result[0]["provenance"] == "project_cognee_chunk"


@pytest.mark.asyncio
async def test_load_chunk_reference_returns_only_the_exact_chunk(monkeypatch):
    store = store_module.CogneeStore.__new__(store_module.CogneeStore)

    async def fake_provenance(chunk_ids):
        assert chunk_ids == ["chunk-7"]
        return {
            "chunk-7": {
                "text": "角色身份锚点来自第1章。",
                "document_id": "document-2",
                "document_name": "故事原稿",
                "source_uri": "file:///story.txt",
                "chunk_index": 3,
            }
        }

    monkeypatch.setattr(store, "_resolve_chunk_provenance", fake_provenance)

    assert await store.load_chunk_reference("chunk-7") == {
        "chunk_id": "chunk-7",
        "text": "角色身份锚点来自第1章。",
        "document_id": "document-2",
        "document_name": "故事原稿",
        "source_uri": "file:///story.txt",
        "chunk_index": 3,
    }
