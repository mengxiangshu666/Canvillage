"""Canonical provenance contract for distilled external knowledge cards."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


KNOWLEDGE_PROVENANCE_SCHEMA = "knowledge.provenance.v1"
GROWTH_PROVENANCE_SCHEMA = "growth.provenance.v1"


def _text(value: object, limit: int = 1_000) -> str:
    return str(value or "").strip()[:limit]


def build_knowledge_provenance(
    *,
    source: object,
    source_commit: object,
    license_name: object,
    content: object,
    source_kind: object = "external_repository",
) -> dict[str, Any]:
    """Build a credential-free, content-addressed source receipt."""

    source_value = _text(source, 2_000)
    commit_value = _text(source_commit, 240)
    license_value = _text(license_name, 240)
    kind_value = _text(source_kind, 80) or "external_repository"
    if isinstance(content, Mapping):
        encoded = json.dumps(
            dict(content),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    else:
        encoded = str(content or "")
    return {
        "schema": KNOWLEDGE_PROVENANCE_SCHEMA,
        "source_kind": kind_value,
        "source": source_value,
        "source_commit": commit_value,
        "license": license_value,
        "content_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


def validate_knowledge_provenance(value: object) -> list[dict[str, str]]:
    """Return deterministic admission issues for one knowledge card."""

    if not isinstance(value, Mapping):
        return [{"code": "knowledge_provenance_missing", "field": "provenance", "message": "知识卡缺少来源合同"}]
    raw = dict(value)
    issues: list[dict[str, str]] = []
    if raw.get("schema") != KNOWLEDGE_PROVENANCE_SCHEMA:
        issues.append({"code": "knowledge_provenance_schema_invalid", "field": "schema", "message": "知识卡来源 schema 不受支持"})
    for field in ("source_kind", "source", "source_commit", "license"):
        if not _text(raw.get(field), 2_000):
            issues.append({"code": "knowledge_provenance_field_missing", "field": field, "message": f"知识卡来源缺少 {field}"})
    digest = _text(raw.get("content_sha256"), 64).casefold()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        issues.append({"code": "knowledge_provenance_hash_invalid", "field": "content_sha256", "message": "知识卡内容哈希无效"})
    return issues


def build_growth_provenance(
    *,
    event_id: object,
    project: object,
    turn_id: object,
    conversation_id: object,
    content: object,
) -> dict[str, Any]:
    """Build provenance for a distilled teaching event.

    Growth events are user/project evidence, not external sources.  They get a
    separate schema so a local feedback event can never accidentally claim an
    upstream repository, commit, or license.
    """

    try:
        normalized_event_id = max(0, int(event_id))
    except (TypeError, ValueError):
        normalized_event_id = 0
    if isinstance(content, Mapping):
        encoded = json.dumps(
            dict(content),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    else:
        encoded = str(content or "")
    return {
        "schema": GROWTH_PROVENANCE_SCHEMA,
        "kind": "growth_distillation_event",
        "event_id": normalized_event_id,
        "project": _text(project, 240),
        "turn_id": _text(turn_id, 200),
        "conversation_id": _text(conversation_id, 200) or "main",
        "content_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


def validate_growth_provenance(value: object) -> list[dict[str, str]]:
    """Return deterministic admission issues for one growth provenance record."""

    if not isinstance(value, Mapping):
        return [{"code": "growth_provenance_missing", "field": "source_provenance", "message": "成长记忆缺少事件来源合同"}]
    raw = dict(value)
    issues: list[dict[str, str]] = []
    if raw.get("schema") != GROWTH_PROVENANCE_SCHEMA:
        issues.append({"code": "growth_provenance_schema_invalid", "field": "schema", "message": "成长记忆来源 schema 不受支持"})
    if raw.get("kind") != "growth_distillation_event":
        issues.append({"code": "growth_provenance_kind_invalid", "field": "kind", "message": "成长记忆来源类型无效"})
    for field in ("project", "turn_id", "conversation_id"):
        if not _text(raw.get(field), 300):
            issues.append({"code": "growth_provenance_field_missing", "field": field, "message": f"成长记忆来源缺少 {field}"})
    try:
        if int(raw.get("event_id") or 0) < 0:
            raise ValueError
    except (TypeError, ValueError):
        issues.append({"code": "growth_provenance_event_invalid", "field": "event_id", "message": "成长记忆事件 ID 无效"})
    digest = _text(raw.get("content_sha256"), 64).casefold()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        issues.append({"code": "growth_provenance_hash_invalid", "field": "content_sha256", "message": "成长记忆内容哈希无效"})
    return issues


__all__ = [
    "GROWTH_PROVENANCE_SCHEMA",
    "KNOWLEDGE_PROVENANCE_SCHEMA",
    "build_growth_provenance",
    "build_knowledge_provenance",
    "validate_growth_provenance",
    "validate_knowledge_provenance",
]
