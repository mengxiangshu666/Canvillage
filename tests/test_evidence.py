from datetime import datetime, timezone

from novelvideo.chat.evidence import (
    EVIDENCE_PACKET_SCHEMA,
    build_evidence_packet,
    freshness_label,
)


def test_evidence_packet_normalizes_sources_and_keeps_citations():
    packet = build_evidence_packet(
        "角色一致性",
        [
            {
                "source": "obsidian",
                "title": "身份锚点",
                "uri": "obsidian://characters/identity.md",
                "snippet": "角色身份必须保持一致。",
                "score": 0.82,
                "provenance": "local_read_only_note",
            },
            {
                "source": "tavily",
                "title": "官方资料",
                "url": "https://example.test/docs",
                "content": "官方参考内容。",
                "published_date": "2026-08-20T00:00:00Z",
                "score": 0.91,
            },
        ],
        sources_requested=("memory", "obsidian", "tavily"),
        retrieval={"provider": "test"},
    )

    assert packet["schema"] == EVIDENCE_PACKET_SCHEMA
    assert packet["count"] == 2
    assert packet["sources_used"] == ["obsidian", "tavily"]
    assert packet["items"][0]["citation"] == "obsidian://characters/identity.md"
    assert packet["items"][1]["uri"] == "https://example.test/docs"
    assert packet["items"][1]["freshness"] in {"fresh", "aging", "stale", "future_dated"}
    assert packet["retrieval"] == {"provider": "test"}


def test_evidence_packet_reports_cross_source_title_divergence():
    packet = build_evidence_packet(
        "规则",
        [
            {"source": "knowledge", "title": "规则", "snippet": "先读画布。"},
            {"source": "obsidian", "title": "规则", "snippet": "先写新节点。"},
        ],
    )

    assert packet["conflicts"][0]["kind"] == "exact_title_claim_divergence"
    assert packet["conflicts"][0]["sources"] == ["knowledge", "obsidian"]


def test_evidence_packet_preserves_cognee_chunk_document_provenance():
    packet = build_evidence_packet(
        "身份连续性",
        [
            {
                "source": "cognee",
                "title": "故事原稿",
                "source_uri": "file:///story.txt",
                "snippet": "角色身份锚点来自第1章。",
                "chunk_id": "chunk-7",
                "document_id": "document-2",
                "dataset_name": "novelvideo_project-a",
                "chunk_index": 3,
                "relationship": "part_of",
                "score": 0.934,
            }
        ],
        sources_requested=("cognee",),
    )

    item = packet["items"][0]
    assert item["uri"] == "file:///story.txt"
    assert item["source_uri"] == "file:///story.txt"
    assert item["chunk_id"] == "chunk-7"
    assert item["document_id"] == "document-2"
    assert item["chunk_index"] == 3


def test_freshness_is_explicit_for_unknown_and_old_dates():
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    assert freshness_label("", now=now) == "unknown"
    assert freshness_label("2026-08-01T00:00:00Z", now=now) == "fresh"
    assert freshness_label("2025-01-01T00:00:00Z", now=now) == "stale"
