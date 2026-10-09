"""Agent 写入脚本派生媒体节点前的权威门禁。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from novelvideo.freezone.script_contract import (
    SCRIPT_MEDIA_ACTION_SHOT_VIDEOS,
    SCRIPT_MEDIA_ACTION_STORYBOARD_IMAGES,
    script_media_action_gate,
    script_row_fingerprint,
    script_row_key,
)

_MEDIA_NODE_TYPES = {"imageGenNode", "videoNode"}
SCRIPT_MEDIA_READINESS_SCHEMA = "script_media_readiness.v1"
_SCRIPT_CHARACTER_SLOT_COUNT = 2
_SCRIPT_NO_VALUES = frozenset(
    {"无", "没有", "none", "n/a", "-", "—"}
)
_SCRIPT_TAG_SPLIT_RE = re.compile(r"[、,，;；/|]+")


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _linked_script_node(
    node: Mapping[str, Any],
    by_id: Mapping[str, Mapping[str, Any]],
    edges: list[dict[str, Any]],
) -> Mapping[str, Any] | None:
    node_type = str(node.get("type") or "")
    data = _as_dict(node.get("data"))
    if node_type == "videoNode":
        source = by_id.get(str(data.get("scriptShotSourceNodeId") or "").strip())
        return source if source and str(source.get("type") or "") == "scriptNode" else None
    if node_type != "imageGenNode":
        return None

    node_id = str(node.get("id") or "").strip()
    for edge in edges:
        if str(edge.get("target") or "").strip() != node_id:
            continue
        source = by_id.get(str(edge.get("source") or "").strip())
        if source and str(source.get("type") or "") == "scriptNode":
            return source

    parent_id = str(node.get("parentId") or "").strip()
    if not parent_id:
        return None
    for candidate in by_id.values():
        if str(candidate.get("type") or "") != "scriptNode":
            continue
        candidate_data = _as_dict(candidate.get("data"))
        if str(candidate_data.get("linkedImageGroupId") or "").strip() == parent_id:
            return candidate
    return None


def _node_row_key(node: Mapping[str, Any] | None) -> str:
    data = _as_dict(node.get("data")) if node is not None else {}
    for key in ("scriptShotId", "scriptRowKey", "scriptShotRowKey"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def _linked_storyboard_image(
    node: Mapping[str, Any],
    by_id: Mapping[str, Mapping[str, Any]],
    edges: list[dict[str, Any]],
) -> Mapping[str, Any] | None:
    data = _as_dict(node.get("data"))
    linked_id = str(data.get("scriptShotImageNodeId") or "").strip()
    linked = by_id.get(linked_id)
    if linked and str(linked.get("type") or "") == "imageGenNode":
        return linked

    node_id = str(node.get("id") or "").strip()
    for edge in edges:
        if str(edge.get("target") or "").strip() != node_id:
            continue
        source = by_id.get(str(edge.get("source") or "").strip())
        if source and str(source.get("type") or "") == "imageGenNode":
            return source
    return None


def _first_frame_url(node: Mapping[str, Any] | None) -> str:
    data = _as_dict(node.get("data")) if node is not None else {}
    for key in ("imageUrl", "previewImageUrl"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def _normalized_string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for item in value:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def _cell_text(row: Mapping[str, Any], key: str) -> str:
    raw = row.get(key)
    if raw is None or isinstance(raw, bool):
        return ""
    if isinstance(raw, (str, int, float)):
        return str(raw).strip()
    return ""


def _is_script_no_value(value: str) -> bool:
    normalized = value.strip().lower()
    return not normalized or normalized in _SCRIPT_NO_VALUES


def _split_script_tags(value: str) -> list[str]:
    return [
        tag.strip()
        for tag in _SCRIPT_TAG_SPLIT_RE.split(value)
        if tag.strip() and not _is_script_no_value(tag)
    ]


def _parallel_cell_text_at(
    row: Mapping[str, Any],
    key: str,
    index: int,
) -> str:
    raw = row.get(key)
    if isinstance(raw, (list, tuple)):
        if index >= len(raw):
            return ""
        return _cell_text({key: raw[index]}, key)
    text = _cell_text(row, key)
    if not text:
        return ""
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, list):
            if index >= len(parsed):
                return ""
            return _cell_text({key: parsed[index]}, key)
    tags = _split_script_tags(text)
    return tags[index] if index < len(tags) else ""


def _row_characters(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    characters: list[dict[str, Any]] = []
    for slot in range(1, _SCRIPT_CHARACTER_SLOT_COUNT + 1):
        raw_name = _cell_text(row, f"character_{slot}")
        raw_description = _cell_text(row, f"character_description_{slot}")
        name = "" if _is_script_no_value(raw_name) else raw_name
        description = (
            "" if _is_script_no_value(raw_description) else raw_description
        )
        image_url = _cell_text(row, f"character_image_{slot}")
        if not name and not description and not image_url:
            continue
        characters.append(
            {
                "slot": slot,
                "name": name,
                "description": description,
                "image_url": image_url,
            }
        )
    return characters


def _asset_snapshot_line(node: Mapping[str, Any]) -> str | None:
    """Return a strict asset-identity line, or None for a legacy node."""

    data = _as_dict(node.get("data"))
    asset_id = str(data.get("scriptAssetId") or "").strip()
    revision = data.get("scriptAssetRevision")
    content_hash = str(data.get("scriptAssetContentHash") or "").strip()
    if (
        not asset_id
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 1
        or not content_hash
    ):
        return None
    locks = _normalized_string_list(data.get("scriptAssetIdentityLocks"))
    if not locks:
        role = asset_id.split(":", 1)[0]
        locks = (
            ["face", "costume", "age", "temperament"]
            if role == "character"
            else ["content_hash"]
        )
    dependencies = _normalized_string_list(data.get("scriptAssetDependencies"))
    return (
        f"{asset_id}@{revision}@{content_hash}"
        f"@{','.join(locks)}@{','.join(dependencies)}"
    )


def _asset_node_is_usable(node: Mapping[str, Any]) -> bool:
    data = _as_dict(node.get("data"))
    if not _first_frame_url(node):
        return False
    if data.get("canvas_auto_generate_once") is True or data.get("isGenerating") is True:
        return False
    return not bool(data.get("generationError"))


def _asset_node_reference_url(node: Mapping[str, Any] | None) -> str:
    if node is None or not _asset_node_is_usable(node):
        return ""
    return _first_frame_url(node)


def _find_current_asset_node(
    asset_id: str,
    script_node_id: str,
    by_id: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    matches: list[Mapping[str, Any]] = []
    owned: list[Mapping[str, Any]] = []
    for candidate in by_id.values():
        if str(candidate.get("type") or "") != "imageGenNode":
            continue
        data = _as_dict(candidate.get("data"))
        if str(data.get("scriptAssetId") or "").strip() != asset_id:
            continue
        matches.append(candidate)
        owner = str(data.get("scriptAssetOwnerId") or "").strip()
        if owner == script_node_id:
            owned.append(candidate)
    if len(owned) == 1:
        return owned[0]
    if len(owned) > 1:
        return None
    if len(matches) != 1:
        return None
    owner = str(_as_dict(matches[0].get("data")).get("scriptAssetOwnerId") or "").strip()
    return matches[0] if not owner else None


def _current_reference_urls(
    row: Mapping[str, Any],
    script_node_id: str,
    by_id: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def push(value: str) -> None:
        url = str(value or "").strip()
        if not url or url in seen:
            return
        seen.add(url)
        urls.append(url)

    for character in _row_characters(row):
        name = str(character["name"] or f"角色{character['slot']}").strip()
        slot = int(character["slot"])
        explicit_id = _cell_text(row, f"character_asset_id_{slot}") or _cell_text(
            row, f"character_id_{slot}"
        )
        asset_id = explicit_id or f"character:{name.lower()}"
        node = _find_current_asset_node(asset_id, script_node_id, by_id)
        push(_asset_node_reference_url(node) or str(character["image_url"]))

    for role, tag_key, ids_key in (
        ("scene", "scene_tags", "scene_asset_ids"),
        ("prop", "prop_tags", "prop_asset_ids"),
    ):
        for index, name in enumerate(_split_script_tags(_cell_text(row, tag_key))):
            explicit_id = _parallel_cell_text_at(row, ids_key, index)
            asset_id = explicit_id or f"{role}:{name.strip().lower()}"
            node = _find_current_asset_node(asset_id, script_node_id, by_id)
            push(_asset_node_reference_url(node))

    if not urls:
        push(_cell_text(row, "reference"))
    return urls


def _current_script_row_reference_snapshot(
    row: Mapping[str, Any],
    script_node_id: str,
    by_id: Mapping[str, Mapping[str, Any]],
) -> str | None:
    urls = _current_reference_urls(row, script_node_id, by_id)
    return "\n".join(urls) if urls else None


def _current_reference_stale_details(
    image_node: Mapping[str, Any],
    row: Mapping[str, Any],
    script_node_id: str,
    by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    image_data = _as_dict(image_node.get("data"))
    if "scriptRowReference" not in image_data:
        return None
    raw_expected = image_data.get("scriptRowReference")
    expected = raw_expected if isinstance(raw_expected, str) else None
    current = _current_script_row_reference_snapshot(row, script_node_id, by_id)
    if expected == current:
        return None
    return {
        "stale_reason": "reference-changed",
        "expected_reference_count": len(expected.splitlines()) if expected else 0,
        "current_reference_count": len(current.splitlines()) if current else 0,
    }


def _current_asset_stale_reason(
    snapshot: str,
    script_node_id: str,
    by_id: Mapping[str, Mapping[str, Any]],
) -> str | None:
    """Validate the frozen asset lines against current canvas nodes.

    The full ledger is intentionally not rebuilt here.  The storyboard already
    froze the identities that matter to this shot; the server only needs to
    prove that those identities still resolve to a usable, unchanged node.
    Legacy nodes without strict identity fields remain compatible when they
    still exist and still carry an image.
    """

    for raw_line in str(snapshot or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        asset_id = line.split("@", 1)[0].strip()
        if not asset_id:
            return "<empty>"
        current = _find_current_asset_node(asset_id, script_node_id, by_id)
        if current is None or not _asset_node_is_usable(current):
            return asset_id
        current_line = _asset_snapshot_line(current)
        if current_line is not None and current_line != line:
            return asset_id
    return None


def _shot_video_stale_reason(
    script_node: Mapping[str, Any],
    node: Mapping[str, Any],
    data: Mapping[str, Any],
    by_id: Mapping[str, Mapping[str, Any]],
    edges: list[dict[str, Any]],
) -> tuple[str, str, dict[str, Any]] | None:
    script_data = _as_dict(script_node.get("data"))
    rows = _as_dict(script_data.get("scriptResult")).get("rows")
    table = [dict(row) for row in rows] if isinstance(rows, list) else []
    row_key = _node_row_key({"data": data})
    if not row_key:
        return (
            "script_media_shot_identity_missing",
            "这条视频没有稳定镜头身份，不能确认它属于哪一镜。",
            {"stale_reason": "shot-identity-missing"},
        )

    row = next(
        ((index, item) for index, item in enumerate(table) if script_row_key(item, index) == row_key),
        None,
    )
    if row is None:
        return (
            "script_media_shot_row_missing",
            "这条视频对应的脚本行已经不存在，不能出片。",
            {"shot_id": row_key, "stale_reason": "row-missing"},
        )
    row_index, current_row = row
    if str(data.get("scriptShotRowFingerprint") or "").strip() != script_row_fingerprint(
        current_row, row_index
    ):
        return (
            "script_media_shot_row_stale",
            "这一镜的视频来自旧脚本行；先重出分镜图并重新排队，不能直接出片。",
            {"row_index": row_index, "shot_id": row_key, "stale_reason": "row-changed"},
        )

    image_node = _linked_storyboard_image({"id": node.get("id"), "data": data}, by_id, edges)
    if image_node is None:
        return (
            "script_media_shot_image_missing",
            "这一镜找不到对应的分镜图节点，不能出片。",
            {"shot_id": row_key, "stale_reason": "storyboard-missing"},
        )
    if _node_row_key(image_node) != row_key:
        return (
            "script_media_shot_image_stale",
            "这一镜绑定的分镜图不属于当前脚本行，不能出片。",
            {"shot_id": row_key, "stale_reason": "storyboard-row-mismatch"},
        )

    current_frame = _first_frame_url(image_node)
    if not current_frame:
        return (
            "script_media_shot_image_stale",
            "这一镜的分镜图还没有可用首帧，不能出片。",
            {"shot_id": row_key, "stale_reason": "first-frame-missing"},
        )
    if str(data.get("scriptShotFirstFrameUrl") or "").strip() != current_frame:
        return (
            "script_media_shot_image_stale",
            "分镜图已重出，视频节点还拿着旧首帧；先重新排队再出片。",
            {"shot_id": row_key, "stale_reason": "first-frame-changed"},
        )

    image_data = _as_dict(image_node.get("data"))
    video_asset_snapshot = str(data.get("scriptShotAssetRevisionSnapshot") or "").strip() or None
    image_asset_snapshot = str(image_data.get("scriptRowAssetSnapshot") or "").strip() or None
    if video_asset_snapshot != image_asset_snapshot:
        return (
            "script_media_shot_image_stale",
            "这一镜的分镜图已换了资产版本，视频节点还没有重新排队。",
            {"shot_id": row_key, "stale_reason": "asset-revision-changed"},
        )
    if image_asset_snapshot:
        stale_asset_id = _current_asset_stale_reason(
            image_asset_snapshot,
            str(script_node.get("id") or "").strip(),
            by_id,
        )
        if stale_asset_id is not None:
            return (
                "script_media_shot_image_stale",
                "这一镜引用的资产节点已经被删除、改写或重新生成，分镜图需要先刷新。",
                {
                    "shot_id": row_key,
                    "stale_reason": "asset-revision-changed",
                    "asset_id": stale_asset_id,
                },
            )
    reference_stale = _current_reference_stale_details(
        image_node,
        current_row,
        str(script_node.get("id") or "").strip(),
        by_id,
    )
    if reference_stale is not None:
        return (
            "script_media_shot_image_stale",
            "分镜图生成时带的参考图与当前脚本 / 资产状态不一致，先重出分镜图再出片。",
            {"shot_id": row_key, **reference_stale},
        )
    return None


def _readiness_result(
    *,
    ready: bool,
    action: str,
    reason_code: str = "",
    reason: str = "",
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": SCRIPT_MEDIA_READINESS_SCHEMA,
        "ready": ready,
        "action": action,
        "reason_code": reason_code,
        "reason": reason,
    }
    for key, value in dict(details or {}).items():
        if value is not None and value != "":
            result[key] = value
    return result


def evaluate_script_media_action(
    *,
    node: Mapping[str, Any],
    data: Mapping[str, Any],
    by_id: Mapping[str, Mapping[str, Any]],
    edges: list[dict[str, Any]],
    action: str | None = None,
) -> dict[str, Any]:
    """Return the current paid-media readiness for one script-derived node.

    This is the read-only authority shared by the canvas write gate and Agent
    recovery revalidation. It never mutates the nodes and never starts a task.
    """

    node_type = str(node.get("type") or "")
    target_node_id = str(node.get("id") or "").strip()
    resolved_action = str(action or "").strip()
    if not resolved_action:
        resolved_action = (
            SCRIPT_MEDIA_ACTION_SHOT_VIDEOS
            if node_type == "videoNode"
            else SCRIPT_MEDIA_ACTION_STORYBOARD_IMAGES
            if node_type == "imageGenNode"
            else ""
        )
    if resolved_action not in {
        SCRIPT_MEDIA_ACTION_STORYBOARD_IMAGES,
        SCRIPT_MEDIA_ACTION_SHOT_VIDEOS,
    }:
        return _readiness_result(
            ready=False,
            action=resolved_action,
            reason_code="script_media_action_unsupported",
            reason="这条画布动作没有受支持的脚本媒体类型。",
            details={"target_node_id": target_node_id},
        )

    expected_type = (
        "videoNode"
        if resolved_action == SCRIPT_MEDIA_ACTION_SHOT_VIDEOS
        else "imageGenNode"
    )
    if node_type != expected_type:
        return _readiness_result(
            ready=False,
            action=resolved_action,
            reason_code="script_media_node_type_mismatch",
            reason="目标节点类型与脚本媒体动作不一致。",
            details={
                "target_node_id": target_node_id,
                "target_node_type": node_type,
            },
        )

    script_node = _linked_script_node({**node, "data": dict(data)}, by_id, edges)
    if script_node is None:
        return _readiness_result(
            ready=False,
            action=resolved_action,
            reason_code="script_media_binding_missing",
            reason="目标节点还没有绑定当前脚本节点，不能提交付费媒体。",
            details={"target_node_id": target_node_id},
        )

    script_node_id = str(script_node.get("id") or "").strip()
    script_data = _as_dict(script_node.get("data"))
    rows = _as_dict(script_data.get("scriptResult")).get("rows")
    table = [dict(row) for row in rows] if isinstance(rows, list) else []
    from novelvideo.freezone.script_video_duration import explicit_script_duration_target

    plan = _as_dict(_as_dict(script_data.get("scriptResult")).get("director_plan"))
    gate = script_media_action_gate(table, action=resolved_action, director_plan=plan,
        target_duration_seconds=explicit_script_duration_target(str(script_data.get("prompt") or "")))
    common = {
        "script_node_id": script_node_id,
        "target_node_id": target_node_id,
        "rows_fingerprint": str(gate.get("rows_fingerprint") or ""),
        "blocking_count": int(gate.get("blocking_count") or 0),
        "missing_prompt_count": int(gate.get("missing_prompt_count") or 0),
    }
    if gate.get("allowed") is not True:
        return _readiness_result(
            ready=False,
            action=resolved_action,
            reason_code=str(gate.get("reason_code") or "script_media_not_ready"),
            reason=str(gate.get("reason") or "脚本尚未通过付费媒体门禁。"),
            details=common,
        )

    row_key = _node_row_key({"data": data})
    row_match = next(
        (
            (index, row)
            for index, row in enumerate(table)
            if row_key and script_row_key(row, index) == row_key
        ),
        None,
    )
    if row_key and row_match is None:
        return _readiness_result(
            ready=False,
            action=resolved_action,
            reason_code="script_media_shot_row_missing",
            reason="这条媒体节点对应的脚本行已经不存在。",
            details={
                **common,
                "shot_id": row_key,
                "stale_reason": "row-missing",
            },
        )

    if resolved_action == SCRIPT_MEDIA_ACTION_SHOT_VIDEOS:
        stale = _shot_video_stale_reason(script_node, node, data, by_id, edges)
        if stale is not None:
            reason_code, reason, stale_details = stale
            return _readiness_result(
                ready=False,
                action=resolved_action,
                reason_code=reason_code,
                reason=reason,
                details={**common, **stale_details},
            )

    row_index = row_match[0] if row_match is not None else None
    return _readiness_result(
        ready=True,
        action=resolved_action,
        details={
            **common,
            **({"shot_id": row_key} if row_key else {}),
            **({"row_index": row_index} if row_index is not None else {}),
        },
    )


def evaluate_script_media_readiness(
    snapshot: Mapping[str, Any],
    *,
    node_id: str,
    action: str | None = None,
) -> dict[str, Any]:
    """Evaluate one node against the current persisted canvas snapshot."""

    payload = dict(snapshot) if isinstance(snapshot, Mapping) else {}
    revision = payload.get("revision")
    canvas_revision = (
        revision
        if isinstance(revision, int) and not isinstance(revision, bool)
        else None
    )
    raw_nodes = payload.get("nodes")
    nodes = [
        dict(node)
        for node in (raw_nodes if isinstance(raw_nodes, list) else [])
        if isinstance(node, Mapping)
    ]
    raw_edges = payload.get("edges")
    edges = [
        dict(edge)
        for edge in (raw_edges if isinstance(raw_edges, list) else [])
        if isinstance(edge, Mapping)
    ]
    by_id = {
        str(node.get("id") or "").strip(): node
        for node in nodes
        if str(node.get("id") or "").strip()
    }
    target_id = str(node_id or "").strip()
    node = by_id.get(target_id)
    if node is None:
        result = _readiness_result(
            ready=False,
            action=str(action or "").strip(),
            reason_code="script_media_target_missing",
            reason="目标节点已经不在当前画布中。",
            details={"target_node_id": target_id},
        )
    else:
        result = evaluate_script_media_action(
            node=node,
            data=_as_dict(node.get("data")),
            by_id=by_id,
            edges=edges,
            action=action,
        )
    if canvas_revision is not None:
        result["canvas_revision"] = canvas_revision
    return result


def copy_node_data(source: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a node as a draft and never carry a queued paid action with it."""

    data = deepcopy(_as_dict(source.get("data")))
    if str(source.get("type") or "") in _MEDIA_NODE_TYPES:
        data["canvas_auto_generate_once"] = False
        data["generationError"] = None
    return data


def apply_node_data(
    command: Mapping[str, Any],
    node: Mapping[str, Any],
    data: dict[str, Any],
    patch: Mapping[str, Any],
    by_id: Mapping[str, Mapping[str, Any]],
    edges: list[dict[str, Any]],
    op_index: int,
    node_id: str,
    error_type: type[Exception],
) -> None:
    """Apply a node patch, then fail closed if it queues a gated script media action."""

    data.update(deepcopy(dict(patch)))
    if (
        str(command.get("type") or "") != "update_node_data"
        or not isinstance(command.get("node_data"), dict)
        or command["node_data"].get("canvas_auto_generate_once") is not True
        or str(node.get("type") or "") not in _MEDIA_NODE_TYPES
    ):
        return

    script_node = _linked_script_node({**node, "data": data}, by_id, edges)
    if script_node is None:
        return
    action = (
        SCRIPT_MEDIA_ACTION_SHOT_VIDEOS
        if str(node.get("type") or "") == "videoNode"
        else SCRIPT_MEDIA_ACTION_STORYBOARD_IMAGES
    )
    readiness = evaluate_script_media_action(
        node=node,
        data=data,
        by_id=by_id,
        edges=edges,
        action=action,
    )
    if readiness.get("ready") is True:
        return
    details = {
        key: readiness.get(key)
        for key in (
            "action",
            "reason_code",
            "script_node_id",
            "target_node_id",
            "blocking_count",
            "missing_prompt_count",
            "rows_fingerprint",
            "shot_id",
            "row_index",
            "stale_reason",
            "asset_id",
            "expected_reference_count",
            "current_reference_count",
        )
        if readiness.get(key) not in (None, "")
    }
    details["target_node_id"] = str(
        details.get("target_node_id") or node_id
    )
    raise error_type(
        str(readiness.get("reason") or "脚本尚未通过付费媒体门禁"),
        code="canvas_script_media_not_ready",
        op_index=op_index,
        details=details,
    )


__all__ = [
    "SCRIPT_MEDIA_READINESS_SCHEMA",
    "apply_node_data",
    "copy_node_data",
    "evaluate_script_media_action",
    "evaluate_script_media_readiness",
]
