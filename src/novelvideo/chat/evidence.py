"""Bounded, source-neutral evidence contracts for Agent retrieval.

The retrieval backends keep their native payloads for compatibility.  This
module provides one small projection that the Agent, blackboard and research
paths can share without treating a note, memory or web result as executable
instructions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import re
from typing import Any, Iterable


EVIDENCE_PACKET_SCHEMA = "evidence.packet.v1"
_MAX_TEXT = 2_000
_MAX_ITEMS = 32
_MAX_ERRORS = 16
_WHITESPACE_RE = re.compile(r"\s+")


def _text(value: object, limit: int = _MAX_TEXT) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "")).strip()[:limit]


def _bounded_score(value: object) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return round(max(0.0, min(1.0, parsed)), 4)


def _iso_now(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: object) -> datetime | None:
    raw = _text(value, 120)
    if not raw:
        return None
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def freshness_label(published_at: object, *, now: datetime | None = None) -> str:
    """Classify only dates we can parse; unknown stays explicit."""

    published = _parse_datetime(published_at)
    if published is None:
        return "unknown"
    current = _parse_datetime(_iso_now(now)) or datetime.now(timezone.utc)
    age = current - published
    if age < timedelta(days=-1):
        return "future_dated"
    if age <= timedelta(days=30):
        return "fresh"
    if age <= timedelta(days=180):
        return "aging"
    return "stale"


def _canonical_uri(raw: dict[str, Any], source: str, rank: int) -> str:
    uri = _text(
        raw.get("uri")
        or raw.get("source_uri")
        or raw.get("url"),
        2_000,
    )
    if uri:
        return uri
    path = _text(raw.get("path") or raw.get("source_id"), 600)
    if path:
        return f"{source}://{path}"
    return f"{source}://result/{rank}"


def normalize_evidence_item(
    value: object,
    *,
    source: str = "unknown",
    rank: int = 0,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """Project one backend result into the bounded packet item contract."""

    raw = value if isinstance(value, dict) else {}
    clean_source = _text(raw.get("source") or source, 80) or "unknown"
    title = _text(raw.get("title") or raw.get("name") or clean_source, 300)
    snippet = _text(
        raw.get("snippet")
        or raw.get("content")
        or raw.get("text")
        or raw.get("claim"),
        _MAX_TEXT,
    )
    published_at = _text(
        raw.get("published_at") or raw.get("published_date"), 120
    )
    item_fetched_at = _text(raw.get("fetched_at") or fetched_at, 120)
    citation = _text(raw.get("citation") or raw.get("url") or raw.get("uri"), 2_000)
    output: dict[str, Any] = {
        "source": clean_source,
        "title": title,
        "uri": _canonical_uri(raw, clean_source, rank),
        "claim": _text(raw.get("claim") or snippet, _MAX_TEXT),
        "snippet": snippet,
        "provenance": _text(raw.get("provenance") or f"{clean_source}_retrieval", 160),
        "citation": citation,
        "rank": max(1, int(rank or 1)),
        "freshness": freshness_label(published_at),
    }
    score = _bounded_score(raw.get("score"))
    if score is not None:
        output["score"] = score
    if published_at:
        output["published_at"] = published_at
    if item_fetched_at:
        output["fetched_at"] = item_fetched_at
    for key in (
        "memory_id",
        "scope_kind",
        "evidence_count",
        "mode",
        "path",
        "source_aliases",
        "raw_content",
        "chunk_id",
        "document_id",
        "dataset_id",
        "dataset_name",
        "chunk_index",
        "relationship",
        "source_uri",
        "document_name",
        "query_role",
        "query_roles",
        "query_variant",
    ):
        value = raw.get(key)
        if value not in (None, "", [], {}):
            if key in {
                "path",
                "mode",
                "scope_kind",
                "chunk_id",
                "document_id",
                "dataset_id",
                "dataset_name",
                "relationship",
                "source_uri",
                "document_name",
            }:
                output[key] = _text(value, 600)
            elif key == "raw_content":
                output[key] = _text(value, 4_000)
            elif key in {"query_role", "query_variant"}:
                output[key] = _text(value, 2_000)
            else:
                output[key] = value
    return output


def _claim_key(item: dict[str, Any]) -> str:
    title = _text(item.get("title"), 300).casefold()
    return title or _text(item.get("uri"), 600).casefold()


def _conflicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        key = _claim_key(item)
        if key:
            grouped.setdefault(key, []).append(item)
    conflicts: list[dict[str, Any]] = []
    for key, group in grouped.items():
        claims = {
            _text(item.get("claim"), _MAX_TEXT).casefold()
            for item in group
            if _text(item.get("claim"), _MAX_TEXT)
        }
        sources = sorted({_text(item.get("source"), 80) for item in group})
        if len(claims) > 1 and len(sources) > 1:
            conflicts.append(
                {
                    "kind": "exact_title_claim_divergence",
                    "key": key[:300],
                    "sources": sources[:8],
                    "uris": [_text(item.get("uri"), 2_000) for item in group[:8]],
                }
            )
    return conflicts[:16]


def build_evidence_packet(
    query: object,
    items: Iterable[object] = (),
    *,
    sources_requested: Iterable[object] = (),
    source_errors: dict[str, object] | None = None,
    source_warnings: dict[str, object] | None = None,
    answer: object = None,
    retrieval: dict[str, object] | None = None,
    assessment: dict[str, object] | None = None,
    observed_at: str | None = None,
    default_source: str = "unknown",
) -> dict[str, Any]:
    """Build one bounded packet while retaining explicit source failures."""

    observed = _text(observed_at, 120) or _iso_now()
    projected = [
        normalize_evidence_item(
            item,
            source=(item.get("source") if isinstance(item, dict) else "") or default_source,
            rank=index,
            fetched_at=observed,
        )
        for index, item in enumerate(list(items)[:_MAX_ITEMS], start=1)
    ]
    errors = {
        _text(key, 80): _text(value, 500)
        for key, value in (source_errors or {}).items()
        if _text(key, 80) and _text(value, 500)
    }
    warnings = {
        _text(key, 80): _text(value, 500)
        for key, value in (source_warnings or {}).items()
        if _text(key, 80) and _text(value, 500)
    }
    requested = sorted(
        {
            _text(source, 80)
            for source in sources_requested
            if _text(source, 80)
        }
    )[:16]
    used = sorted({_text(item.get("source"), 80) for item in projected})
    packet: dict[str, Any] = {
        "schema": EVIDENCE_PACKET_SCHEMA,
        "query": _text(query, 2_000),
        "observed_at": observed,
        "sources_requested": requested,
        "sources_used": used,
        "items": projected,
        "count": len(projected),
        "conflicts": _conflicts(projected),
        "source_errors": errors,
        "source_warnings": warnings,
        "retrieval": dict(retrieval or {}),
    }
    if isinstance(assessment, dict) and assessment:
        packet["research_assessment"] = dict(assessment)
    clean_answer = _text(answer, 4_000)
    if clean_answer:
        packet["answer"] = clean_answer
    return packet


__all__ = [
    "EVIDENCE_PACKET_SCHEMA",
    "build_evidence_packet",
    "freshness_label",
    "normalize_evidence_item",
]
