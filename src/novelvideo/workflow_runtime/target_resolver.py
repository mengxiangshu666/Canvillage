"""Deterministic resolution of Agent targets against the current canvas.

The model may describe an object semantically, but only the server can decide
whether that object already exists.  This module deliberately stays pure and
bounded: it never calls a model, reads storage, or invents node ids.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from novelvideo.workflow_runtime.action_router import connection_dependency_refs

_MUTATION_TERMS = (
    "修改", "优化", "调整", "移动", "删除", "重命名", "替换", "继续", "重试", "完善", "修复", "更新", "改",
    "modify", "update", "optimize", "continue", "retry", "refine", "fix",
)
_CREATION_TERMS = (
    "新增", "新建", "创建", "再加", "复制", "另一个", "重新做一个",
    "add", "create", "new", "duplicate", "another",
)
_MUTATION_COMMANDS = {
    "update_node_prompt", "update_node_label", "update_node_data",
    "update_node_camera", "move_node", "delete_node", "connect_nodes",
    "remove_edge",
}
_REPLACEMENT_COMMANDS = {
    "update_node_prompt", "update_node_label", "update_node_data",
    "update_node_camera", "move_node", "delete_node", "remove_edge",
}
_REPLACEMENT_TERMS = ("替代", "替换", "重做", "覆写", "改写", "replace")
_CREATE_MISSING_STRATEGY = "create_missing"
_TYPE_ALIASES = {
    "镜头": {"storyboardNode", "storyboardGenNode", "videoNode", "videoStoryNode"},
    "分镜": {"storyboardNode", "storyboardGenNode", "storyboardSplitNode"},
    "图片": {"imageGenNode", "imageEditNode", "uploadNode", "exportImageNode"},
    "图像": {"imageGenNode", "imageEditNode", "uploadNode", "exportImageNode"},
    "视频": {"videoNode", "videoStoryNode", "videoComposeNode"},
    "剧本": {"scriptNode", "textAnnotationNode"},
    "文字": {"textAnnotationNode", "scriptNode"},
    "脚本": {"scriptNode"},
}
_MAX_CANDIDATES = 8
_MAX_NODES = 500
_MAX_TEXT = 500


def _text(value: object, *, limit: int = _MAX_TEXT) -> str:
    return str(value or "").strip()[:limit]


def _normalize(value: object, *, limit: int = _MAX_TEXT) -> str:
    text = _text(value, limit=limit).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text).strip()


def _tokens(value: object) -> set[str]:
    normalized = _normalize(value)
    # Keep ASCII words intact and use CJK characters as bounded matching
    # units.  Exact phrase matching below handles multi-character labels.
    return set(re.findall(r"[a-z0-9_:-]+|[\u4e00-\u9fff]", normalized))


def _node_texts(node: Mapping[str, Any]) -> tuple[str, str, str]:
    data = node.get("data") if isinstance(node.get("data"), Mapping) else {}
    label = next(
        (
            _text(source.get(key))
            for source in (data, node)
            for key in ("displayName", "display_name", "label", "title", "name")
            if _text(source.get(key))
        ),
        "",
    )
    prompt = next(
        (_text(source.get("prompt")) for source in (data, node) if _text(source.get("prompt"))),
        "",
    )
    node_type = _text(node.get("type"), limit=120)
    return label, prompt, node_type


def _stable_value(value: object, *, limit: int = 240) -> str:
    text = _text(value, limit=limit)
    if not text or text.lower().startswith(("http://", "https://")):
        return ""
    return text


def _node_identity(node: Mapping[str, Any], *, canvas_id: object = "default") -> dict[str, str]:
    data = node.get("data") if isinstance(node.get("data"), Mapping) else {}
    def first(keys: tuple[str, ...], *, limit: int = 240) -> str:
        for source in (data, node):
            for key in keys:
                value = _stable_value(source.get(key), limit=limit)
                if value:
                    return value
        return ""

    node_id = _stable_value(node.get("id"), limit=200)
    resolved_canvas_id = (
        _stable_value(canvas_id, limit=200)
        or first(("canvas_id", "canvasId"), limit=200)
        or "default"
    )
    identity = {
        "asset_id": first(
            (
                "assetId",
                "asset_id",
                "sourceAssetId",
                "source_asset_id",
                "resultAssetId",
                "result_asset_id",
                "identityId",
                "identity_id",
                "sceneId",
                "scene_id",
                "propId",
                "prop_id",
                "mediaId",
                "media_id",
                "fileId",
                "file_id",
            )
        ),
        "asset_uri": first(("asset_uri", "assetUri", "hogi_uri", "hogiUri", "uri")),
        "role": first(
            (
                "reference_role",
                "referenceRole",
                "output_role",
                "outputRole",
                "role",
                "nodeRole",
                "node_role",
                "assetKind",
                "asset_kind",
                "media_role",
            ),
            limit=100,
        ),
        "parent_id": first(("parentId", "parent_id"), limit=200),
    }
    if node_id:
        identity["node_uri"] = (
            f"canvas://{quote(resolved_canvas_id, safe='')}/nodes/{quote(node_id, safe='')}"
        )
    return identity


def _command_refs(commands: object) -> set[str]:
    refs: set[str] = set()
    if not isinstance(commands, (list, tuple)):
        return refs
    for command in commands[:100]:
        if not isinstance(command, Mapping):
            continue
        command_type = _text(command.get("type"), limit=120)
        if command_type == "connect_nodes" and any(
            _text(command.get(key), limit=200).startswith("$created:")
            for key in ("source", "target")
        ):
            # A concrete endpoint connected to a node created in this batch is
            # a dependency of the new node, not the object being replaced.
            continue
        for key in ("node_id", "source", "target"):
            value = _text(command.get(key), limit=200)
            if value and not value.startswith("$created:"):
                refs.add(value)
    return refs


def _replacement_command_refs(commands: object) -> set[str]:
    """Return existing nodes a command batch mutates in place.

    ``connect_nodes`` is deliberately excluded: wiring a new node to an
    existing endpoint is a structural link, not an in-place rewrite of that
    endpoint.  Only rewrite/move/delete commands make an existing node the
    object being replaced.
    """

    refs: set[str] = set()
    if not isinstance(commands, (list, tuple)):
        return refs
    for command in commands[:100]:
        if not isinstance(command, Mapping):
            continue
        if _text(command.get("type"), limit=120) not in _REPLACEMENT_COMMANDS:
            continue
        for key in ("node_id", "source", "target"):
            value = _text(command.get(key), limit=200)
            if value and not value.startswith("$created:"):
                refs.add(value)
    return refs


def _has_term(text: str, terms: tuple[str, ...]) -> bool:
    normalized = _normalize(text)
    return any(term in normalized for term in terms)


def _identity_mentioned(value: object, query_norm: str) -> bool:
    normalized = _normalize(value, limit=500)
    if len(normalized) < 4:
        return False
    if normalized == query_norm:
        return True
    return f" {normalized} " in f" {query_norm} "


def resolve_existing_targets(
    *,
    goal: object,
    operation: object,
    target_node_ids: object = (),
    commands: object = (),
    nodes: object = (),
    canvas_id: object = "default",
    declared_target_strategy: object = "",
    creation_reason: object = "",
) -> dict[str, Any]:
    """Return bounded candidates and a deterministic reuse recommendation."""

    goal_text = _text(goal, limit=2_000)
    operation_text = _text(operation, limit=500)
    query = f"{goal_text} {operation_text}".strip()
    command_refs = _command_refs(commands)
    dependency_refs = connection_dependency_refs(commands)
    declared_ids = {
        _text(item, limit=200)
        for item in (target_node_ids if isinstance(target_node_ids, (list, tuple, set)) else ())
        if _text(item, limit=200)
    }
    if (
        _text(declared_target_strategy, limit=60) == _CREATE_MISSING_STRATEGY
        and not _has_term(
            f"{query} {_text(creation_reason, limit=1_000)}",
            _REPLACEMENT_TERMS,
        )
    ):
        # Under an explicit create_missing contract the declared ids are the
        # existing nodes this batch builds from, not objects being replaced.
        # Rewriting one of them in the same batch, or asking for a
        # replacement in words, still binds it as a replacement target.
        replacement_refs = _replacement_command_refs(commands)
        dependency_refs = dependency_refs | {
            node_id for node_id in declared_ids if node_id not in replacement_refs
        }
    explicit_ids = declared_ids - dependency_refs
    mutation_intent = _has_term(query, _MUTATION_TERMS)
    if isinstance(commands, (list, tuple)):
        mutation_intent = mutation_intent or any(
            isinstance(command, Mapping)
            and _text(command.get("type"), limit=120) in _MUTATION_COMMANDS
            for command in commands[:100]
        )
    explicit_creation_intent = _has_term(query, _CREATION_TERMS)

    candidates: list[dict[str, Any]] = []
    bounded_nodes = nodes[:_MAX_NODES] if isinstance(nodes, (list, tuple)) else ()
    selected_ids = {
        _text(node.get("id"), limit=200)
        for node in bounded_nodes
        if isinstance(node, Mapping)
        and node.get("selected") is True
        and _text(node.get("id"), limit=200)
    }
    if selected_ids and mutation_intent and not explicit_creation_intent:
        explicit_ids.update(selected_ids)
    query_norm = _normalize(query, limit=2_500)
    query_tokens = _tokens(query)
    for raw_node in bounded_nodes:
        if not isinstance(raw_node, Mapping):
            continue
        node_id = _text(raw_node.get("id"), limit=200)
        if not node_id:
            continue
        if node_id in dependency_refs and node_id not in command_refs:
            continue
        label, prompt, node_type = _node_texts(raw_node)
        identity = _node_identity(raw_node, canvas_id=canvas_id)
        label_norm = _normalize(label)
        prompt_norm = _normalize(prompt)
        reasons: list[str] = []
        score = 0.0
        identity_values = {
            identity[key]
            for key in ("asset_id", "asset_uri", "node_uri")
            if identity.get(key)
        }
        if node_id in explicit_ids or node_id in command_refs:
            score = 1.0
            reasons.append("explicit_node_id")
        elif explicit_ids.intersection(identity_values) or command_refs.intersection(identity_values):
            score = 1.0
            reasons.append("explicit_asset_identity")
        elif any(
            _identity_mentioned(identity.get(key), query_norm)
            for key in ("asset_id", "asset_uri", "node_uri")
        ):
            score = 0.99
            reasons.append("identity_mentioned")
        elif any(
            identity_value
            and _normalize(identity_value) == query_norm
            for identity_value in (identity.get("asset_id"), identity.get("asset_uri"), identity.get("node_uri"))
        ):
            score = 0.98
            reasons.append("exact_asset_identity_match")
        elif label_norm and (label_norm in query_norm or query_norm in label_norm):
            score = 0.95
            reasons.append("exact_label_match")
        else:
            label_tokens = _tokens(label)
            overlap = len(label_tokens & query_tokens)
            if overlap >= 2 and overlap / max(len(label_tokens), 1) >= 0.75:
                score = 0.85
                reasons.append("label_token_overlap")
            elif prompt_norm and len(prompt_norm) <= 240:
                prompt_tokens = _tokens(prompt)
                if len(prompt_tokens & query_tokens) >= 3:
                    score = 0.65
                    reasons.append("bounded_prompt_overlap")
        if node_type:
            for alias, types in _TYPE_ALIASES.items():
                if alias in query_norm and node_type in types:
                    if score < 0.85:
                        score = max(score, 0.55)
                    reasons.append("type_alias_match")
                    break
        if score <= 0 or not reasons:
            continue
        if mutation_intent:
            reasons.append("mutation_intent")
        candidates.append(
            {
                "node_id": node_id,
                "node_type": node_type,
                "label": label[:200],
                "asset_id": identity.get("asset_id") or None,
                "asset_uri": identity.get("asset_uri") or None,
                "node_uri": identity.get("node_uri") or None,
                "role": identity.get("role") or None,
                "parent_id": identity.get("parent_id") or None,
                "score": round(score, 3),
                "reasons": list(dict.fromkeys(reasons))[:5],
            }
        )

    candidates.sort(key=lambda item: (-float(item["score"]), item["node_id"]))
    candidates = candidates[:_MAX_CANDIDATES]
    if (
        not candidates
        and mutation_intent
        and not explicit_creation_intent
        and not dependency_refs
    ):
        if selected_ids:
            candidates = [
                {
                    "node_id": node_id,
                    "node_type": _text(next(
                        (
                            node.get("type")
                            for node in bounded_nodes
                            if isinstance(node, Mapping)
                            and _text(node.get("id"), limit=200) == node_id
                        ),
                        "",
                    ), limit=120),
                    "label": "",
                    "asset_id": None,
                    "asset_uri": None,
                    "node_uri": f"canvas://{quote(str(canvas_id), safe='')}/nodes/{quote(node_id, safe='')}",
                    "role": None,
                    "parent_id": None,
                    "score": 1.0,
                    "reasons": ["selected_node", "mutation_intent"],
                }
                for node_id in sorted(selected_ids)
            ]
        elif len(bounded_nodes) == 1 and isinstance(bounded_nodes[0], Mapping):
            node_id = _text(bounded_nodes[0].get("id"), limit=200)
            if node_id:
                label, _prompt, node_type = _node_texts(bounded_nodes[0])
                identity = _node_identity(bounded_nodes[0], canvas_id=canvas_id)
                candidates = [
                    {
                        "node_id": node_id,
                        "node_type": node_type,
                        "label": label[:200],
                        "asset_id": identity.get("asset_id") or None,
                        "asset_uri": identity.get("asset_uri") or None,
                        "node_uri": identity.get("node_uri") or None,
                        "role": identity.get("role") or None,
                        "parent_id": identity.get("parent_id") or None,
                        "score": 0.9,
                        "reasons": ["only_existing_node", "mutation_intent"],
                    }
                ]
    high_confidence = [
        item for item in candidates if float(item["score"]) >= 0.95
    ]
    recommended_strategy = "reuse_existing" if high_confidence else (
        "reuse_existing" if candidates and float(candidates[0]["score"]) >= 0.85 else "create_missing"
    )
    suggested_ids = [item["node_id"] for item in high_confidence[:20]]
    suggested_uris = [
        item["asset_uri"] or item["node_uri"]
        for item in high_confidence[:20]
        if item.get("asset_uri") or item.get("node_uri")
    ]
    confidence = "high" if high_confidence else ("medium" if candidates else "low")
    return {
        "schema": "target_resolution.v1",
        "observed_node_count": min(len(bounded_nodes), _MAX_NODES),
        "mutation_intent": mutation_intent,
        "explicit_creation_intent": explicit_creation_intent,
        "dependency_node_ids": sorted(dependency_refs),
        "recommended_strategy": recommended_strategy,
        "suggested_target_node_ids": suggested_ids,
        "suggested_target_uris": suggested_uris,
        "confidence": confidence,
        "candidates": candidates,
        "identity_policy": {
            "bind_by": ["node_id", "asset_id", "asset_uri", "node_uri"],
            "label_is_display_only": True,
            "ambiguous_identity_requires_confirmation": True,
        },
    }


def should_block_creation(*, target_strategy: object, resolution: Mapping[str, Any]) -> bool:
    """Block only an unexplained high-confidence replacement of an existing node."""

    return (
        str(target_strategy or "").strip() == "create_missing"
        and resolution.get("mutation_intent") is True
        and resolution.get("explicit_creation_intent") is not True
        and resolution.get("confidence") == "high"
        and bool(resolution.get("suggested_target_node_ids"))
    )


__all__ = ["resolve_existing_targets", "should_block_creation"]
