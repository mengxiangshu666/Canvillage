"""Durable storage for user-authored canvas templates.

Starter workflows are shipped with the product.  This module stores the
personal counterpart: a bounded, sanitized subgraph saved by one user and
reusable across canvases without copying generated media or task state.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping, Sequence
import uuid

from novelvideo.sqlite_pragmas import configure_sqlite_connection

CANVAS_TEMPLATE_SCHEMA = "canvas_user_template.v1"
MAX_CANVAS_TEMPLATE_NODES = 100
MAX_CANVAS_TEMPLATE_EDGES = 300
MAX_CANVAS_TEMPLATE_BYTES = 1_500_000
MAX_CANVAS_TEMPLATE_TITLE = 60
MAX_CANVAS_TEMPLATE_DESCRIPTION = 240

_NODE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

_CANVAS_TEMPLATE_NODE_TYPES = frozenset(
    {
        "uploadNode",
        "imageNode",
        "imageGenNode",
        "exportImageNode",
        "beatContextNode",
        "textAnnotationNode",
        "groupNode",
        "storyboardNode",
        "storyboardGenNode",
        "videoNode",
        "audioNode",
        "videoStoryNode",
        "videoComposeNode",
        "scriptNode",
        "pano360ViewerNode",
        "threeDWorldNode",
        "skillNode",
    }
)

# A template is configuration, not a snapshot of production truth.  Saving a
# generated URL or an in-flight job into a reusable template would make the
# template lie as soon as it is inserted into another canvas.
_RUNTIME_NODE_DATA_KEYS = frozenset(
    {
        "agent_command_id",
        "starter_workflow_id",
        "imageUrl",
        "previewImageUrl",
        "videoUrl",
        "resultVideoUrl",
        "generationBatch",
        "dialogueAudioUrl",
        "dialogueAudioCacheKey",
        "resourceMeta",
        "productionMetadata",
        "committed_at",
        "committed_slot_url",
        "isGenerating",
        "generationStartedAt",
        "generationError",
        "generationErrorDetails",
        "generationErrorRequestId",
        "generationErrorStage",
        "generationErrorSuggestedAction",
        "generationErrorCode",
        "generationErrorRetryable",
        "generationRecoveryJobId",
        "generationRecoveryTaskType",
        "isUploading",
        "uploadError",
        "isAnalyzing",
        "analysisResult",
        "analysisError",
        "isSeparatingAv",
        "resultMirroredAt",
        "originNodeId",
        "village_canvas_agent_viewport_placed_command",
    }
)


class CanvasTemplateError(ValueError):
    """Stable validation/storage error for the canvas-template boundary."""

    def __init__(self, message: str, *, code: str = "canvas_template_invalid"):
        super().__init__(message)
        self.code = code


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_size_bytes(value: object) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _finite_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CanvasTemplateError(f"{field} must be a number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise CanvasTemplateError(f"{field} must be finite")
    return number


def _sanitize_node_data(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CanvasTemplateError("template node data must be an object")
    data = deepcopy(dict(value))
    for key in _RUNTIME_NODE_DATA_KEYS:
        data.pop(key, None)
    return data


def _normalize_node_spec(value: Mapping[str, Any]) -> dict[str, Any]:
    key = str(value.get("key") or "").strip()
    node_type = str(value.get("type") or "").strip()
    if not _NODE_KEY_PATTERN.fullmatch(key):
        raise CanvasTemplateError("template node key is invalid")
    if node_type not in _CANVAS_TEMPLATE_NODE_TYPES:
        raise CanvasTemplateError(
            f"template node type is unsupported: {node_type or '<empty>'}"
        )
    offset = value.get("offset")
    if not isinstance(offset, Mapping):
        raise CanvasTemplateError(f"template node {key} requires an offset")
    normalized: dict[str, Any] = {
        "key": key,
        "type": node_type,
        "offset": {
            "x": _finite_number(offset.get("x"), field=f"template node {key} offset.x"),
            "y": _finite_number(offset.get("y"), field=f"template node {key} offset.y"),
        },
        "data": _sanitize_node_data(value.get("data")),
    }
    parent_key = str(value.get("parent_key") or "").strip()
    if parent_key:
        normalized["parent_key"] = parent_key
    for field in ("width", "height"):
        raw = value.get(field)
        if raw is None:
            continue
        number = _finite_number(raw, field=f"template node {key} {field}")
        if number <= 0:
            raise CanvasTemplateError(f"template node {key} {field} must be positive")
        normalized[field] = number
    return normalized


def _normalize_edge_spec(value: Mapping[str, Any]) -> dict[str, Any]:
    source = str(value.get("source") or "").strip()
    target = str(value.get("target") or "").strip()
    if not source or not target or source == target:
        raise CanvasTemplateError("template edge endpoints are invalid")
    normalized: dict[str, Any] = {
        "source": source,
        "target": target,
    }
    for field in ("relation", "source_handle", "target_handle"):
        text = str(value.get(field) or "").strip()
        if text:
            normalized[field] = text[:120]
    return normalized


def normalize_canvas_template_input(
    *,
    title: object,
    description: object,
    nodes: object,
    edges: object,
) -> dict[str, Any]:
    """Validate and sanitize a user template before it reaches SQLite."""

    normalized_title = str(title or "").strip()
    if not normalized_title:
        raise CanvasTemplateError("template title is required")
    if len(normalized_title) > MAX_CANVAS_TEMPLATE_TITLE:
        raise CanvasTemplateError("template title is too long")
    normalized_description = str(description or "").strip()
    if len(normalized_description) > MAX_CANVAS_TEMPLATE_DESCRIPTION:
        raise CanvasTemplateError("template description is too long")
    if not isinstance(nodes, Sequence) or isinstance(nodes, (str, bytes, bytearray)):
        raise CanvasTemplateError("template nodes must be a list")
    if not nodes:
        raise CanvasTemplateError("template requires at least one node")
    if len(nodes) > MAX_CANVAS_TEMPLATE_NODES:
        raise CanvasTemplateError("template has too many nodes")
    if edges is None:
        edges = []
    if not isinstance(edges, Sequence) or isinstance(edges, (str, bytes, bytearray)):
        raise CanvasTemplateError("template edges must be a list")
    if len(edges) > MAX_CANVAS_TEMPLATE_EDGES:
        raise CanvasTemplateError("template has too many edges")

    normalized_nodes: list[dict[str, Any]] = []
    key_set: set[str] = set()
    for raw in nodes:
        if not isinstance(raw, Mapping):
            raise CanvasTemplateError("template node must be an object")
        node = _normalize_node_spec(raw)
        key = str(node["key"])
        if key in key_set:
            raise CanvasTemplateError(f"template node key is duplicated: {key}")
        key_set.add(key)
        normalized_nodes.append(node)

    for node in normalized_nodes:
        parent_key = str(node.get("parent_key") or "")
        if not parent_key:
            continue
        if parent_key not in key_set:
            raise CanvasTemplateError(
                f"template node {node['key']} references missing parent {parent_key}"
            )
        if parent_key == node["key"]:
            raise CanvasTemplateError("template node cannot parent itself")

    # Reject parent cycles before insertion creates an unusable graph.
    parent_by_key = {
        str(node["key"]): str(node.get("parent_key") or "") for node in normalized_nodes
    }
    for start in parent_by_key:
        seen: set[str] = set()
        current = start
        while current:
            if current in seen:
                raise CanvasTemplateError("template node parent cycle detected")
            seen.add(current)
            current = parent_by_key.get(current, "")

    normalized_edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str]] = set()
    for raw in edges:
        if not isinstance(raw, Mapping):
            raise CanvasTemplateError("template edge must be an object")
        edge = _normalize_edge_spec(raw)
        source = str(edge["source"])
        target = str(edge["target"])
        if source not in key_set or target not in key_set:
            raise CanvasTemplateError("template edge references an unknown node")
        edge_key = (source, target)
        if edge_key in seen_edges:
            raise CanvasTemplateError("template edge is duplicated")
        seen_edges.add(edge_key)
        normalized_edges.append(edge)

    payload = {
        "schema": CANVAS_TEMPLATE_SCHEMA,
        "title": normalized_title,
        "description": normalized_description,
        "nodes": normalized_nodes,
        "edges": normalized_edges,
    }
    if _json_size_bytes(payload) > MAX_CANVAS_TEMPLATE_BYTES:
        raise CanvasTemplateError("template payload is too large")
    return payload


class CanvasTemplateStore:
    """SQLite-backed personal template library scoped by owner id."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        configure_sqlite_connection(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS canvas_user_templates (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                schema TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                node_count INTEGER NOT NULL,
                edge_count INTEGER NOT NULL,
                source_project_id TEXT NOT NULL DEFAULT '',
                source_canvas_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_canvas_user_templates_owner_updated
            ON canvas_user_templates(owner_id, updated_at DESC)
            """
        )
        conn.commit()
        return conn

    @staticmethod
    def _summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "title": str(row["title"]),
            "description": str(row["description"] or ""),
            "node_count": int(row["node_count"]),
            "edge_count": int(row["edge_count"]),
            "source_project_id": str(row["source_project_id"] or ""),
            "source_canvas_id": str(row["source_canvas_id"] or ""),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    @staticmethod
    def _detail(row: sqlite3.Row) -> dict[str, Any]:
        payload = json.loads(str(row["payload_json"]))
        return {
            **CanvasTemplateStore._summary(row),
            "schema": str(row["schema"]),
            "nodes": list(payload.get("nodes") or []),
            "edges": list(payload.get("edges") or []),
        }

    def list_for_owner(self, owner_id: str) -> list[dict[str, Any]]:
        normalized_owner = str(owner_id or "").strip()
        if not normalized_owner:
            return []
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM canvas_user_templates
                WHERE owner_id = ?
                ORDER BY updated_at DESC, created_at DESC
                """,
                (normalized_owner,),
            ).fetchall()
            return [self._summary(row) for row in rows]
        finally:
            conn.close()

    def get(self, owner_id: str, template_id: str) -> dict[str, Any] | None:
        normalized_owner = str(owner_id or "").strip()
        normalized_id = str(template_id or "").strip()
        if not normalized_owner or not normalized_id:
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM canvas_user_templates
                WHERE owner_id = ? AND id = ?
                """,
                (normalized_owner, normalized_id),
            ).fetchone()
            return self._detail(row) if row is not None else None
        finally:
            conn.close()

    def create(
        self,
        *,
        owner_id: str,
        title: object,
        description: object = "",
        nodes: object,
        edges: object,
        source_project_id: str = "",
        source_canvas_id: str = "",
    ) -> dict[str, Any]:
        normalized_owner = str(owner_id or "").strip()
        if not normalized_owner:
            raise CanvasTemplateError(
                "template owner is required",
                code="canvas_template_owner_required",
            )
        normalized = normalize_canvas_template_input(
            title=title,
            description=description,
            nodes=nodes,
            edges=edges,
        )
        template_id = f"ct_{uuid.uuid4().hex}"
        now = _utc_now_iso()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO canvas_user_templates (
                    id, owner_id, title, description, schema, payload_json,
                    node_count, edge_count, source_project_id, source_canvas_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    template_id,
                    normalized_owner,
                    normalized["title"],
                    normalized["description"],
                    normalized["schema"],
                    json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
                    len(normalized["nodes"]),
                    len(normalized["edges"]),
                    str(source_project_id or "").strip()[:160],
                    str(source_canvas_id or "").strip()[:160],
                    now,
                    now,
                ),
            )
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise CanvasTemplateError(
                "template could not be saved",
                code="canvas_template_store_error",
            ) from exc
        finally:
            conn.close()
        result = self.get(normalized_owner, template_id)
        if result is None:
            raise CanvasTemplateError(
                "template was not persisted",
                code="canvas_template_store_error",
            )
        return result

    def delete(self, owner_id: str, template_id: str) -> bool:
        normalized_owner = str(owner_id or "").strip()
        normalized_id = str(template_id or "").strip()
        if not normalized_owner or not normalized_id:
            return False
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                DELETE FROM canvas_user_templates
                WHERE owner_id = ? AND id = ?
                """,
                (normalized_owner, normalized_id),
            )
            conn.commit()
            return int(cursor.rowcount or 0) > 0
        finally:
            conn.close()


__all__ = [
    "CANVAS_TEMPLATE_SCHEMA",
    "MAX_CANVAS_TEMPLATE_NODES",
    "MAX_CANVAS_TEMPLATE_EDGES",
    "CanvasTemplateError",
    "CanvasTemplateStore",
    "normalize_canvas_template_input",
]
