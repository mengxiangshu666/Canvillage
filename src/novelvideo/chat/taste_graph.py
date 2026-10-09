"""Evidence-backed user taste projection over the existing memory index.

The memory database remains authoritative.  This module only turns eligible
preference memories into a bounded graph that the director Agent can consult.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

from novelvideo.chat.memory_index import MemoryRecord, list_memories

TASTE_GRAPH_SCHEMA = "taste_graph.v1"
TASTE_GRAPH_SOURCE = {
    "project": "TasteGraph-Skill",
    "commit": "b53ef2660b363b6e1bc9a6b8f8e2c8b86ee441f6",
    "license": "MIT",
    "integration": "memory_index_projection",
}

_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("motion", ("运镜", "运动", "动作", "节奏", "动画", "motion", "camera")),
    ("color", ("颜色", "色彩", "色温", "调色", "冷色", "暖色", "color", "palette")),
    ("typography", ("字体", "排版", "字号", "typography", "font")),
    ("sound", ("音效", "声音", "配音", "声线", "音乐", "sound", "voice", "music")),
    ("interaction", ("交互", "按钮", "操作", "反馈", "interaction", "hover")),
    ("material", ("材质", "纹理", "布料", "金属", "皮肤", "material", "texture")),
    ("content", ("故事", "剧本", "角色", "对白", "叙事", "content", "story")),
    ("process", ("流程", "工作流", "效率", "先", "必须", "不要空转", "process", "workflow")),
    ("visual", ("画面", "视觉", "美术", "风格", "光影", "构图", "镜头", "visual", "style")),
)
_ANTI_RE = re.compile(
    r"(?:不喜欢|讨厌|禁止|不要|避免|拒绝|反感|不能出现|anti\b|hate\b|avoid\b)",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(r"(?i)\b[a-z]:\\(?:[^\s\\]+\\)*[^\s]*")
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")


def _text(value: object, limit: int = 900) -> str:
    text = " ".join(str(value or "").split())
    text = _PRIVATE_PATH_RE.sub("[local-path]", text)
    text = _EMAIL_RE.sub("[email]", text)
    return text[:limit]


def _metadata(record: MemoryRecord) -> dict[str, Any]:
    try:
        value = json.loads(record.metadata_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _applies_when(record: MemoryRecord) -> dict[str, Any]:
    try:
        value = json.loads(record.applies_when or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _category(*values: object) -> str:
    haystack = " ".join(_text(value, 2_000).lower() for value in values)
    for category, words in _CATEGORIES:
        if any(word in haystack for word in words):
            return category
    return "other"


def _domains(applies_when: dict[str, Any], category: str) -> list[str]:
    domains: list[str] = []
    for key in ("task_family", "task_stage", "creation_stage", "media_kind", "node_type"):
        value = applies_when.get(key)
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in values:
            clean = _text(item, 80).lower().replace(" ", "_")
            if clean and clean not in domains:
                domains.append(clean)
    if not domains:
        domains.append("film_motion" if category in {"motion", "sound", "content"} else "creative")
    return domains[:6]


def _eligible(record: MemoryRecord, metadata: dict[str, Any]) -> bool:
    if record.kind != "preference" or record.source == "profile_file":
        return False
    if record.status not in {"candidate", "validated", "confirmed"}:
        return False
    structured = (
        str(metadata.get("memory_schema") or "") == "xiaoshu.memory.v3"
        or bool(record.locked)
        or int(record.evidence_count) > 0
    )
    if not structured:
        return False
    if record.status == "candidate" and not (
        record.positive_count > record.negative_count and record.evidence_count > 0
    ):
        return False
    if record.negative_count > record.positive_count and not record.locked:
        return False
    return len(_text(record.content)) >= 6


def _confidence(record: MemoryRecord) -> str:
    if record.locked or (record.positive_count >= 2 and record.negative_count == 0):
        return "H"
    if record.status == "confirmed" or record.positive_count >= 1:
        return "M"
    return "L"


def _strength(record: MemoryRecord) -> float:
    value = float(record.confidence)
    value += min(0.18, max(0, record.positive_count) * 0.04)
    value -= min(0.32, max(0, record.negative_count) * 0.08)
    if record.status == "confirmed":
        value = max(value, 0.65)
    if record.locked:
        value = max(value, 0.9)
    return round(max(0.1, min(1.0, value)), 3)


def _evidence_ids(record: MemoryRecord, metadata: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for item in metadata.get("evidence") or []:
        if isinstance(item, dict):
            ref = _text(item.get("ref"), 240)
            if ref and ref not in values:
                values.append(ref)
    provenance = metadata.get("source_provenance")
    if isinstance(provenance, dict):
        event_id = _text(provenance.get("event_id"), 120)
        if event_id:
            values.append(f"growth-event:{event_id}")
    if not values and record.source_id:
        values.append(f"memory-source:{hashlib.sha256(record.source_id.encode('utf-8')).hexdigest()[:16]}")
    return values[:8]


def project_taste_graph(
    records: Iterable[MemoryRecord],
    *,
    project: str = "",
    limit: int = 24,
) -> dict[str, Any]:
    """Build a deterministic, bounded graph from eligible memory records."""

    ranked: list[tuple[float, MemoryRecord, dict[str, Any]]] = []
    rejected = 0
    for record in records:
        metadata = _metadata(record)
        if not _eligible(record, metadata):
            rejected += 1
            continue
        ranked.append((_strength(record), record, metadata))
    ranked.sort(key=lambda item: (item[0], item[1].updated_at, item[1].id), reverse=True)

    loves: list[dict[str, Any]] = []
    antis: list[dict[str, Any]] = []
    graph_nodes: list[dict[str, Any]] = []
    graph_edges: list[dict[str, Any]] = []
    category_ids: set[str] = set()
    for strength, record, metadata in ranked[: max(1, int(limit))]:
        avoids = [_text(item, 600) for item in metadata.get("avoid") or [] if _text(item, 600)]
        action = [_text(item, 600) for item in metadata.get("action") or [] if _text(item, 600)]
        content = _text(record.content, 900)
        is_anti = bool(avoids) and bool(_ANTI_RE.search(content))
        description = avoids[0] if is_anti else content
        category = _category(description, *action, *avoids)
        applies = _applies_when(record)
        node = {
            "id": f"taste.memory.{record.id}.{'anti' if is_anti else 'love'}",
            "memory_id": record.id,
            "label": description[:180],
            "category": category,
            "description": description,
            "polarity": "anti" if is_anti else "love",
            "confidence": _confidence(record),
            "strength": strength,
            "evidence_ids": _evidence_ids(record, metadata),
            "domains": _domains(applies, category),
            "status": record.status,
            "locked": bool(record.locked),
        }
        (antis if is_anti else loves).append(node)
        category_id = f"taste.category.{category}"
        if category_id not in category_ids:
            graph_nodes.append({"id": category_id, "kind": "category", "label": category})
            category_ids.add(category_id)
        graph_nodes.append(
            {
                "id": node["id"],
                "kind": "preference",
                "label": node["label"],
                "polarity": node["polarity"],
                "strength": strength,
                "confidence": node["confidence"],
                "memory_id": record.id,
            }
        )
        graph_edges.append(
            {
                "source": node["id"],
                "target": category_id,
                "relation": "opposes" if is_anti else "supports",
                "weight": strength,
                "evidence_ids": node["evidence_ids"],
            }
        )

    revision_material = [
        [item[1].id, item[1].version, item[1].evidence_count, item[1].updated_at]
        for item in ranked[: max(1, int(limit))]
    ]
    revision = hashlib.sha256(
        json.dumps(revision_material, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return {
        "schema": TASTE_GRAPH_SCHEMA,
        "revision": revision,
        "project_id": _text(project, 256),
        "source": dict(TASTE_GRAPH_SOURCE),
        "hard_loves": loves,
        "hard_antis": antis,
        "consultation": {
            "do": [item["description"] for item in loves[:6]],
            "avoid": [item["description"] for item in antis[:6]],
        },
        "affinity_graph": {"nodes": graph_nodes, "edges": graph_edges},
        "stats": {
            "eligible": len(loves) + len(antis),
            "rejected": rejected,
            "love_count": len(loves),
            "anti_count": len(antis),
        },
    }


def build_taste_graph(username: str, project: str = "", *, limit: int = 24) -> dict[str, Any]:
    # User/professional preferences are global; project preferences must stay
    # inside their owning project or a new project inherits the wrong taste.
    clean_project = _text(project, 256)
    records = [
        record
        for record in list_memories(username, kind="preference", limit=500)
        if record.scope_kind in {"professional", "user"}
        or (clean_project and record.scope_kind == "project" and record.scope_id == clean_project)
    ]
    return project_taste_graph(records, project=project, limit=limit)


__all__ = ["TASTE_GRAPH_SCHEMA", "build_taste_graph", "project_taste_graph"]
