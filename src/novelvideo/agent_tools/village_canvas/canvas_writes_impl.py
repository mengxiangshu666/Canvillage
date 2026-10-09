from __future__ import annotations

import json
import math
import re
import time
from typing import Any
from urllib.parse import quote

from .contracts import (
    _response_payload,
    _skill_contract_error,
)
from .core import (
    CANVAS_PATCH_TIMEOUT_SECONDS,
    _agent_context_value,
    _canvas_id_from_args,
    _default_project_id,
    _freezone_tool_error,
    _local_specialist_result,
    _maybe_json,
    _turn_scoped_command_id,
    _with_tool_trace,
    logger,
)
from .runtime import runtime_handler, runtime_proxy

_project_from_args = runtime_proxy("_project_from_args")
_request = runtime_proxy("_request")
_request_with_timeout = runtime_proxy("_request_with_timeout")
tool_result = runtime_proxy("tool_result")

_CANVAS_DEFAULT_SELECTED = "$selected"
_CANVAS_NODE_REF_ALIASES = {
    "",
    "$selected",
    "selected",
    "@selected",
    "SELECTED",
    "current",
    "$current",
    "$pinned",
    "pinned",
    "@pinned",
    "PINNED",
}


def _normalize_canvas_node_ref(value: object, *, default: str | None = None) -> str:
    """Allow concrete ids or tight-coupling aliases ($selected / $pinned / $pinned:N)."""
    raw = str(value or "").strip()
    if not raw:
        return default or ""
    if raw in _CANVAS_NODE_REF_ALIASES:
        return raw if raw else (default or _CANVAS_DEFAULT_SELECTED)
    if re.fullmatch(r"\$?pinned:\d+", raw, flags=re.I):
        return raw if raw.startswith("$") else f"${raw}"
    return raw


def _normalize_batch_node_ref(value: object) -> str:
    """Keep deterministic references to nodes created in the same batch."""

    raw = str(value or "").strip()
    if re.fullmatch(r"\$created:\d+", raw, flags=re.I):
        return raw.lower()
    return _normalize_canvas_node_ref(value)


_UPDATE_NODE_PROMPT_ALIAS_KEYS = (
    "prompt",
    "content",
    "text",
    "compiledPromptPreview",
)
_UPDATE_NODE_PROMPT_EXPLANATION_KEYS = frozenset({"reason"})


def _normalize_update_node_prompt_command(
    command: dict[str, Any],
) -> dict[str, Any]:
    """Collapse one unambiguous prompt alias shape into the canonical field.

    Models occasionally place the same final prompt under ``node_data`` using
    the node UI's field names. Accept only exact, mutually consistent aliases
    plus a bounded ``reason`` explanation. Any extra mutation field or
    conflicting value must remain fail-closed.
    """

    normalized = dict(command)
    top_prompt = str(normalized.get("prompt") or "").strip()
    node_data = normalized.get("node_data")
    nested_prompt = ""
    if node_data not in (None, {}):
        if not isinstance(node_data, dict):
            raise ValueError("update_node_prompt.node_data must be an object")
        allowed_keys = {
            *_UPDATE_NODE_PROMPT_ALIAS_KEYS,
            *_UPDATE_NODE_PROMPT_EXPLANATION_KEYS,
        }
        unknown_fields = sorted(
            str(key) for key in node_data if str(key) not in allowed_keys
        )
        if unknown_fields:
            raise ValueError(
                "update_node_prompt.node_data contains unsupported fields: "
                + ", ".join(unknown_fields[:8])
            )
        reason = node_data.get("reason")
        if reason not in (None, ""):
            if not isinstance(reason, str):
                raise ValueError("update_node_prompt.node_data.reason must be a string")
            if len(reason.strip()) > 2000:
                raise ValueError("update_node_prompt.node_data.reason is too long")
        alias_values: list[str] = []
        for key in _UPDATE_NODE_PROMPT_ALIAS_KEYS:
            value = node_data.get(key)
            if value in (None, ""):
                continue
            if not isinstance(value, str):
                raise ValueError(f"update_node_prompt.node_data.{key} must be a string")
            text = value.strip()
            if text:
                alias_values.append(text)
        distinct_aliases = set(alias_values)
        if len(distinct_aliases) > 1:
            raise ValueError("update_node_prompt prompt aliases conflict")
        if distinct_aliases:
            nested_prompt = alias_values[0]
        if top_prompt and nested_prompt and top_prompt != nested_prompt:
            raise ValueError("update_node_prompt prompt aliases conflict")
    prompt = top_prompt or nested_prompt
    if not prompt:
        raise ValueError("update_node_prompt requires prompt")
    normalized.pop("node_data", None)
    normalized["prompt"] = prompt
    return normalized


def _normalize_canvas_command_batch(commands: object) -> object:
    """Normalize safe command aliases before routing, checkpoints, and writes."""

    if not isinstance(commands, list):
        return commands
    normalized_commands: list[Any] = []
    changed = False
    for command in commands:
        if (
            isinstance(command, dict)
            and str(command.get("type") or "").strip() == "update_node_prompt"
        ):
            normalized = _normalize_update_node_prompt_command(command)
            normalized_commands.append(normalized)
            changed = changed or normalized != command
            continue
        normalized_commands.append(command)
    return normalized_commands if changed else commands


def _is_alias_node_ref(value: object) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return True
    if raw in _CANVAS_NODE_REF_ALIASES:
        return True
    return bool(re.fullmatch(r"\$?pinned:\d+", raw, flags=re.I))


_CANVAS_AGENT_IMAGE_ASPECT_RATIOS = {
    "auto",
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "4:5",
    "5:4",
    "21:9",
}
_CANVAS_AGENT_VIDEO_ASPECT_RATIOS = {"16:9", "4:3", "1:1", "3:4", "9:16", "21:9"}
_CANVAS_AGENT_IMAGE_SIZES = {"0.5K", "1K", "2K", "4K"}
_CANVAS_AGENT_VIDEO_QUALITIES = {"480P", "720P", "768P", "1080P", "2K", "4K"}
_CANVAS_AGENT_COUNTS = {1, 2, 4, 6, 8, 12}
_CANVAS_AGENT_VIDEO_MODES = {
    "textToVideo",
    "allReference",
    "imageToVideo",
    "firstLastFrame",
    "imageReference",
    "videoEdit",
}
_CANVAS_AGENT_GENERIC_NODE_TYPES = {
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
_CANVAS_COMPATIBILITY_FORBIDDEN_MEDIA_NODE_TYPES = {
    "imageGenNode",
    "videoNode",
    "audioNode",
    "storyboardGenNode",
    "videoStoryNode",
    "videoComposeNode",
}
_CANVAS_COMPATIBILITY_FORBIDDEN_MEDIA_COMMAND_TYPES = {
    "create_image_prompt_node",
    "create_video_prompt_node",
    # 这两条同样会建出 imageGenNode / videoNode（见 canvas_command_gateway 的
    # create_shot_sequence 与 insert_starter_workflow 落点），必须与上面两条
    # 一并禁止走兼容写入，否则一条命令即可绕开导演准入批量建媒体节点（T-217）。
    "create_shot_sequence",
    "insert_starter_workflow",
}


def _compatibility_media_creation_command(commands: object) -> str:
    """Return the first media-creating command that must use dispatch_action."""

    if not isinstance(commands, list):
        return ""
    for command in commands:
        if not isinstance(command, dict):
            continue
        command_type = str(command.get("type") or "").strip()
        if command_type in _CANVAS_COMPATIBILITY_FORBIDDEN_MEDIA_COMMAND_TYPES:
            return command_type
        if command_type != "create_canvas_node":
            continue
        node_type = str(command.get("node_type") or "").strip()
        if node_type in _CANVAS_COMPATIBILITY_FORBIDDEN_MEDIA_NODE_TYPES:
            return f"create_canvas_node:{node_type}"
    return ""


_CANVAS_COMMAND_RECEIPTS_KEY = "village_canvas_agent_command_ids"


def _canvas_command_receipts(doc: dict[str, Any]) -> list[str]:
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    values = (
        metadata.get(_CANVAS_COMMAND_RECEIPTS_KEY) if isinstance(metadata, dict) else []
    )
    if isinstance(values, str):
        return [value for value in re.split(r"[\s,]+", values.strip()) if value]
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if str(value or "").strip()]


def _canvas_command_created_ids(doc: dict[str, Any], command_id: str) -> list[str]:
    created: list[str] = []
    for node in doc.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        node_id = str(node.get("id") or "").strip()
        if node_id and str(data.get("agent_command_id") or "") == command_id:
            created.append(node_id)
    return created


def _canvas_command_receipt(doc: dict[str, Any], command_id: str) -> dict[str, Any]:
    """Project one persisted v2 receipt without replaying its large expectation."""
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    receipts = metadata.get("village_canvas_command_receipts_v2")
    raw = receipts.get(command_id) if isinstance(receipts, dict) else None
    if not isinstance(raw, dict):
        return {}
    fields = (
        "schema",
        "success",
        "server_applied",
        "command_id",
        "command_hash",
        "project_id",
        "canvas_id",
        "revision",
        "canvas_revision",
        "created_node_ids",
        "affected_node_ids",
        "applied_ops",
        "presentation_ops",
        "idempotent_replay",
        "conflict_retried",
    )
    result = {key: raw[key] for key in fields if key in raw}
    result["created_node_ids"] = list(result.get("created_node_ids") or [])
    result["affected_node_ids"] = list(result.get("affected_node_ids") or [])
    return result


def _server_apply_canvas_structure(
    *,
    project: str,
    canvas_id: str,
    command_id: str,
    commands: list[dict[str, Any]],
    source_turn_id: str = "",
    action_profile: dict[str, Any] | None = None,
    execution_context: dict[str, Any] | None = None,
    expected_canvas_revision: int | None = None,
    _retry_on_conflict: bool = True,
) -> dict[str, Any]:
    """Apply structure through the authenticated shared CanvasCommandGateway API."""
    _ = _retry_on_conflict
    response = _request(
        "POST",
        (
            f"/api/v1/projects/{project}/freezone/canvases/"
            f"{quote(canvas_id, safe='')}/commands:apply"
        ),
        body={
            "command_id": command_id,
            "commands": commands,
            **(
                {"source_turn_id": source_turn_id}
                if str(source_turn_id).strip()
                else {}
            ),
            **(
                {"action_profile": action_profile}
                if isinstance(action_profile, dict)
                else {}
            ),
            **(
                {"execution_context": dict(execution_context)}
                if isinstance(execution_context, dict)
                else {}
            ),
            **(
                {"expected_canvas_revision": expected_canvas_revision}
                if isinstance(expected_canvas_revision, int)
                and not isinstance(expected_canvas_revision, bool)
                else {}
            ),
        },
    )
    if response.get("ok") is False:
        payload = response.get("data")
        detail = payload.get("detail") if isinstance(payload, dict) else None
        detail_error = detail.get("error") if isinstance(detail, dict) else ""
        return {
            "server_applied": False,
            "server_apply_error": str(
                detail_error or response.get("error") or "canvas_command_apply_failed"
            ),
            "error_code": (
                str(detail.get("error_code") or "") if isinstance(detail, dict) else ""
            ),
            "created_node_ids": [],
            "applied_ops": 0,
            "revision": (
                detail.get("current_revision") if isinstance(detail, dict) else None
            ),
        }
    receipt = response.get("data")
    if not isinstance(receipt, dict):
        return {
            "server_applied": False,
            "server_apply_error": "canvas_command_apply_invalid_response",
            "created_node_ids": [],
            "applied_ops": 0,
        }
    return {
        **receipt,
        "server_applied": bool(receipt.get("server_applied")),
        "server_apply_error": None,
        "created_node_ids": list(receipt.get("created_node_ids") or []),
        "applied_ops": int(receipt.get("applied_ops") or 0),
        "revision": receipt.get("revision"),
        "readback_verified": bool(receipt.get("readback_verified", True)),
        "readback_verification": (
            dict(receipt["readback_verification"])
            if isinstance(receipt.get("readback_verification"), dict)
            else None
        ),
    }


def _handle_emit_canvas_command(args: dict[str, Any], **_: Any) -> str:
    """Emit allowlisted canvas structure commands and persist safe ops server-side.

    Structure work is real work — not chat-only. Every supported operation is
    written to the canvas document so freezone_get_canvas_snapshot can verify it.
    Frontend also applies the same transaction immediately for responsive UI.
    Never starts media generation.
    """
    t0 = time.perf_counter()
    try:
        project = _project_from_args(args)
        canvas_id = _canvas_id_from_args(args)
        commands = _normalize_canvas_command_batch(args.get("commands"))
        if not isinstance(commands, list) or not commands or len(commands) > 20:
            raise ValueError("commands must contain 1-20 operations")
        if args.get("_compatibility_route") is True:
            forbidden_media_command = _compatibility_media_creation_command(commands)
            if forbidden_media_command:
                return _skill_contract_error(
                    "canvas.compatibility.emit",
                    "compatibility_media_route_rejected",
                    (
                        "兼容画布写入不能创建图片、视频或音频节点；"
                        "必须重新调用 village_canvas_dispatch_action，"
                        "由导演、授权和 WorkflowRun 门裁决。"
                    ),
                )
        dynamic_checkpoint = args.get("dynamic_checkpoint")
        checkpoint_required = _compatibility_batch_requires_checkpoint(commands)
        delete_present = any(
            isinstance(command, dict)
            and str(command.get("type") or "").strip() == "delete_node"
            for command in commands
        )
        # 删除节点不可撤销，必须拿到 ready_write 检查点 —— 在任何通道都成立，
        # 不只是在兼容/动态通道（T-217：此前非兼容派发通道可无检查点删除）。
        if dynamic_checkpoint is None and (
            (args.get("_compatibility_route") is True and checkpoint_required)
            or delete_present
        ):
            return _skill_contract_error(
                "canvas.compatibility.emit",
                "checkpoint_required",
                "既有节点/连线修改（含删除）必须先取得 ready_write checkpoint。",
            )
        if dynamic_checkpoint is not None and checkpoint_required:
            checkpoint_error = _dynamic_checkpoint_error(
                "canvas.compatibility.emit",
                {"side_effect": "canvas_write"},
                args,
            )
            if checkpoint_error is not None:
                return checkpoint_error
            dynamic_error = _validate_dynamic_existing_node_commands(commands)
            if dynamic_error:
                raise ValueError(dynamic_error)
        allowed = {
            "focus_node",
            "select_node",
            "connect_nodes",
            "remove_edge",
            "annotate",
            "create_canvas_node",
            "create_image_prompt_node",
            "create_video_prompt_node",
            "create_shot_sequence",
            "insert_starter_workflow",
            "update_node_prompt",
            "update_node_label",
            "update_node_data",
            "update_node_camera",
            "move_node",
            "duplicate_node",
            "delete_node",
        }
        normalized: list[dict[str, Any]] = []
        for item in commands:
            if not isinstance(item, dict):
                raise ValueError("each canvas command must be an object")
            command_type = str(item.get("type") or "").strip()
            if command_type not in allowed:
                raise ValueError(f"unsupported canvas command: {command_type}")
            clean: dict[str, Any] = {"type": command_type}

            if command_type == "create_canvas_node":
                node_type = str(item.get("node_type") or "").strip()
                if node_type not in _CANVAS_AGENT_GENERIC_NODE_TYPES:
                    raise ValueError("create_canvas_node.node_type is unsupported")
                clean["node_type"] = node_type
                for field_name, max_length in (
                    ("text", 50_000),
                    ("prompt", 50_000),
                    ("model", 200),
                ):
                    value = str(item.get(field_name) or "").strip()
                    if value:
                        if len(value) > max_length:
                            raise ValueError(
                                f"create_canvas_node.{field_name} is too long"
                            )
                        clean[field_name] = value
            elif command_type == "insert_starter_workflow":
                workflow_id = str(item.get("workflow_id") or "").strip()
                if not workflow_id:
                    raise ValueError("insert_starter_workflow requires workflow_id")
                if len(workflow_id) > 200:
                    raise ValueError("insert_starter_workflow.workflow_id is too long")
                clean["workflow_id"] = workflow_id
            elif command_type == "create_shot_sequence":
                prompts = item.get("prompts")
                if not isinstance(prompts, list) or not prompts or len(prompts) > 12:
                    raise ValueError(
                        "create_shot_sequence requires prompts: 1-12 non-empty strings"
                    )
                clean_prompts: list[str] = []
                for prompt in prompts:
                    text = str(prompt or "").strip()
                    if not text:
                        raise ValueError(
                            "create_shot_sequence prompts must be non-empty"
                        )
                    if len(text) > 50_000:
                        raise ValueError("create_shot_sequence prompt is too long")
                    clean_prompts.append(text)
                clean["prompts"] = clean_prompts
            elif command_type in {
                "focus_node",
                "select_node",
                "move_node",
                "duplicate_node",
                "delete_node",
            }:
                node_id = _normalize_canvas_node_ref(
                    item.get("node_id"), default=_CANVAS_DEFAULT_SELECTED
                )
                if not node_id:
                    raise ValueError(f"{command_type} requires node_id or $selected")
                if len(node_id) > 512:
                    raise ValueError(f"{command_type}.node_id is too long")
                clean["node_id"] = node_id
            elif command_type in {"connect_nodes", "remove_edge"}:
                source = _normalize_batch_node_ref(item.get("source"))
                target = _normalize_batch_node_ref(item.get("target"))
                if not source:
                    raise ValueError(f"{command_type} requires source or $selected")
                if not target:
                    raise ValueError(
                        f"{command_type} requires target (node id or $pinned)"
                    )
                if len(source) > 512 or len(target) > 512:
                    raise ValueError(f"{command_type} endpoint id is too long")
                clean["source"] = source
                clean["target"] = target
            elif command_type == "annotate":
                text = str(item.get("text") or "").strip()
                if not text:
                    raise ValueError("annotate requires text")
                if len(text) > 20_000:
                    raise ValueError("annotate.text is too long")
                clean["text"] = text
            elif command_type in {
                "create_image_prompt_node",
                "create_video_prompt_node",
            }:
                prompt = str(item.get("prompt") or "").strip()
                if not prompt:
                    raise ValueError(f"{command_type} requires prompt")
                if len(prompt) > 50_000:
                    raise ValueError(f"{command_type}.prompt is too long")
                clean["prompt"] = prompt
            elif command_type == "update_node_prompt":
                node_id = _normalize_canvas_node_ref(
                    item.get("node_id"), default=_CANVAS_DEFAULT_SELECTED
                )
                prompt = str(item.get("prompt") or "").strip()
                if not node_id:
                    raise ValueError("update_node_prompt requires node_id or $selected")
                if not prompt:
                    raise ValueError("update_node_prompt requires prompt")
                if len(node_id) > 512 or len(prompt) > 50_000:
                    raise ValueError("update_node_prompt field is too long")
                clean["node_id"] = node_id
                clean["prompt"] = prompt
            elif command_type == "update_node_label":
                node_id = _normalize_canvas_node_ref(
                    item.get("node_id"), default=_CANVAS_DEFAULT_SELECTED
                )
                display_name = str(item.get("display_name") or "").strip()
                if not node_id:
                    raise ValueError("update_node_label requires node_id or $selected")
                if not display_name:
                    raise ValueError("update_node_label requires display_name")
                if len(node_id) > 512:
                    raise ValueError("update_node_label.node_id is too long")
                clean["node_id"] = node_id
                clean["display_name"] = display_name[:200]
            elif command_type == "update_node_data":
                node_id = _normalize_canvas_node_ref(
                    item.get("node_id"), default=_CANVAS_DEFAULT_SELECTED
                )
                node_id = _normalize_batch_node_ref(node_id)
                node_data = item.get("node_data")
                if not isinstance(node_data, dict):
                    node_data = {}
                node_data = dict(node_data)
                for source, target in (
                    ("camera", "camera"),
                    ("camera_movement", "camera_movement"),
                    ("cameraSelection", "cameraSelection"),
                    ("cameraMovement", "cameraMovement"),
                ):
                    if item.get(source) not in (None, "") and target not in node_data:
                        node_data[target] = item[source]
                if not node_id:
                    raise ValueError("update_node_data requires node_id or $selected")
                if len(node_id) > 512:
                    raise ValueError("update_node_data.node_id is too long")
                if not isinstance(node_data, dict) or not node_data:
                    raise ValueError(
                        "update_node_data requires a non-empty node_data object"
                    )
                try:
                    encoded_node_data = json.dumps(
                        node_data, ensure_ascii=False, allow_nan=False
                    )
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "update_node_data.node_data must be JSON-safe"
                    ) from exc
                if len(encoded_node_data) > 100_000:
                    raise ValueError("update_node_data.node_data is too large")
                clean["node_id"] = node_id
                clean["node_data"] = node_data
            elif command_type == "update_node_camera":
                node_id = _normalize_batch_node_ref(
                    _normalize_canvas_node_ref(
                        item.get("node_id"), default=_CANVAS_DEFAULT_SELECTED
                    )
                )
                if not node_id:
                    raise ValueError("update_node_camera requires node_id or $selected")
                if len(node_id) > 512:
                    raise ValueError("update_node_camera.node_id is too long")
                clean["node_id"] = node_id
                camera = item.get("camera")
                movement = str(item.get("camera_movement") or "").strip()
                clear_camera = item.get("clear_camera") is True
                if camera is not None:
                    if not isinstance(camera, dict) or not camera:
                        raise ValueError(
                            "update_node_camera.camera must be a non-empty object"
                        )
                    clean["camera"] = dict(camera)
                if movement:
                    if len(movement) > 200:
                        raise ValueError(
                            "update_node_camera.camera_movement is too long"
                        )
                    clean["camera_movement"] = movement
                if clear_camera:
                    clean["clear_camera"] = True
                if camera is None and not movement and not clear_camera:
                    raise ValueError(
                        "update_node_camera requires camera, camera_movement, or clear_camera"
                    )

            display_name = str(item.get("display_name") or "").strip()
            if display_name and "display_name" not in clean:
                clean["display_name"] = display_name[:200]
            for axis in ("x", "y"):
                value = item.get(axis)
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                ):
                    clean[axis] = max(-100_000.0, min(100_000.0, float(value)))
            placement = item.get("placement")
            if placement is not None:
                if not isinstance(placement, dict):
                    raise ValueError("placement must be an object")
                clean_placement: dict[str, Any] = {}
                anchor = str(placement.get("anchor") or "").strip()
                if anchor:
                    if anchor not in {"viewport_center", "selected_node", "absolute"}:
                        raise ValueError("placement.anchor is unsupported")
                    clean_placement["anchor"] = anchor
                layout = str(placement.get("layout") or "").strip()
                if layout:
                    if layout not in {"stack", "grid", "row", "column"}:
                        raise ValueError("placement.layout is unsupported")
                    clean_placement["layout"] = layout
                offset = placement.get("offset")
                if offset is not None:
                    if not isinstance(offset, dict):
                        raise ValueError("placement.offset must be an object")
                    clean_offset: dict[str, float] = {}
                    for axis in ("x", "y"):
                        value = offset.get(axis)
                        if value is None:
                            continue
                        if (
                            not isinstance(value, (int, float))
                            or isinstance(value, bool)
                            or not math.isfinite(float(value))
                        ):
                            raise ValueError(f"placement.offset.{axis} must be finite")
                        clean_offset[axis] = max(-10_000.0, min(10_000.0, float(value)))
                    if clean_offset:
                        clean_placement["offset"] = clean_offset
                gap = placement.get("gap")
                if gap is not None:
                    if (
                        not isinstance(gap, (int, float))
                        or isinstance(gap, bool)
                        or not math.isfinite(float(gap))
                    ):
                        raise ValueError("placement.gap must be finite")
                    clean_placement["gap"] = max(16.0, min(400.0, float(gap)))
                if clean_placement:
                    clean["placement"] = clean_placement
            if command_type in {
                "create_image_prompt_node",
                "create_video_prompt_node",
                "create_shot_sequence",
            }:
                aspect_ratio = str(item.get("aspect_ratio") or "").strip()
                if aspect_ratio:
                    allowed_aspect_ratios = (
                        _CANVAS_AGENT_VIDEO_ASPECT_RATIOS
                        if command_type == "create_video_prompt_node"
                        else _CANVAS_AGENT_IMAGE_ASPECT_RATIOS
                    )
                    if aspect_ratio not in allowed_aspect_ratios:
                        raise ValueError("aspect_ratio is unsupported")
                    clean["aspect_ratio"] = aspect_ratio
                model = str(item.get("model") or "").strip()
                if model:
                    if len(model) > 200:
                        raise ValueError("model is too long")
                    clean["model"] = model
                count = item.get("count")
                if count is not None:
                    if (
                        not isinstance(count, int)
                        or isinstance(count, bool)
                        or count not in _CANVAS_AGENT_COUNTS
                    ):
                        raise ValueError("count must be one of 1, 2, 4, 6, 8, 12")
                    clean["count"] = count
                if command_type in {"create_image_prompt_node", "create_shot_sequence"}:
                    image_size = str(item.get("image_size") or "").strip()
                    if image_size:
                        if image_size not in _CANVAS_AGENT_IMAGE_SIZES:
                            raise ValueError("image_size is unsupported")
                        clean["image_size"] = image_size
                    camera = item.get("camera")
                    if camera is not None:
                        if not isinstance(camera, dict):
                            raise ValueError("camera must be an object")
                        clean_camera: dict[str, Any] = {}
                        for source_key, max_length in (
                            ("camera_body_id", 200),
                            ("lens_id", 200),
                            ("aperture", 32),
                        ):
                            value = str(camera.get(source_key) or "").strip()
                            if value:
                                if len(value) > max_length:
                                    raise ValueError(f"camera.{source_key} is too long")
                                clean_camera[source_key] = value
                        focal_length = camera.get("focal_length_mm")
                        if focal_length is not None:
                            if (
                                not isinstance(focal_length, (int, float))
                                or isinstance(focal_length, bool)
                                or not math.isfinite(float(focal_length))
                                or not 1 <= float(focal_length) <= 2000
                            ):
                                raise ValueError(
                                    "camera.focal_length_mm must be between 1 and 2000"
                                )
                            clean_camera["focal_length_mm"] = int(
                                round(float(focal_length))
                            )
                        if clean_camera:
                            clean["camera"] = clean_camera
                if command_type == "create_video_prompt_node":
                    video_quality = str(item.get("video_quality") or "").strip()
                    if video_quality:
                        if video_quality not in _CANVAS_AGENT_VIDEO_QUALITIES:
                            raise ValueError("video_quality is unsupported")
                        clean["video_quality"] = video_quality
                    duration_sec = item.get("duration_sec")
                    if duration_sec is not None:
                        if (
                            not isinstance(duration_sec, int)
                            or isinstance(duration_sec, bool)
                            or not 1 <= duration_sec <= 120
                        ):
                            raise ValueError("duration_sec must be between 1 and 120")
                        clean["duration_sec"] = duration_sec
                    generation_mode = str(item.get("generation_mode") or "").strip()
                    if generation_mode:
                        if generation_mode not in _CANVAS_AGENT_VIDEO_MODES:
                            raise ValueError("generation_mode is unsupported")
                        clean["generation_mode"] = generation_mode
                    if "generate_audio" in item:
                        if not isinstance(item.get("generate_audio"), bool):
                            raise ValueError("generate_audio must be boolean")
                        clean["generate_audio"] = item["generate_audio"]
                    camera_movement = str(item.get("camera_movement") or "").strip()
                    if camera_movement:
                        if len(camera_movement) > 200:
                            raise ValueError("camera_movement is too long")
                        clean["camera_movement"] = camera_movement
            normalized.append(clean)

        command_id = _turn_scoped_command_id(args)
        server_meta: dict[str, Any] = {
            "server_applied": False,
            "server_apply_error": None,
            "created_node_ids": [],
            "applied_ops": 0,
        }
        try:
            server_meta = runtime_handler("_server_apply_canvas_structure")(
                project=project,
                canvas_id=canvas_id,
                command_id=command_id,
                commands=normalized,
                source_turn_id=str(args.get("source_turn_id") or "").strip(),
                action_profile=(
                    dict(args["action_profile"])
                    if isinstance(args.get("action_profile"), dict)
                    else None
                ),
                execution_context=(
                    dict(args["execution_context"])
                    if isinstance(args.get("execution_context"), dict)
                    else None
                ),
                expected_canvas_revision=(
                    args.get("expected_canvas_revision")
                    if isinstance(args.get("expected_canvas_revision"), int)
                    and not isinstance(args.get("expected_canvas_revision"), bool)
                    else None
                ),
            )
        except Exception as apply_exc:  # noqa: BLE001 — soft-fail to UI bridge
            server_meta = {
                "server_applied": False,
                "server_apply_error": str(apply_exc),
                "created_node_ids": [],
                "applied_ops": 0,
            }

        server_applied = bool(server_meta.get("server_applied"))
        created_ids = list(server_meta.get("created_node_ids") or [])
        applied_ops = int(server_meta.get("applied_ops") or 0)
        apply_err = server_meta.get("server_apply_error")
        revision = server_meta.get("revision")
        readback_verified = server_meta.get("readback_verified", True) is True
        authoritative_commit = bool(
            server_applied
            and isinstance(revision, int)
            and revision > 0
            and (created_ids or applied_ops > 0)
            and readback_verified
        )
        if authoritative_commit:
            structure_status = "server_applied_verified"
            handoff_level = "L1"
            error_code = None
        elif server_applied and not readback_verified:
            structure_status = "server_applied_readback_failed"
            handoff_level = "L0"
            error_code = "FZ_READBACK_FAILED"
        elif server_applied:
            structure_status = "server_applied_noop"
            handoff_level = "L0"
            error_code = "FZ_EMIT_NOOP"
        else:
            structure_status = "emit_only"
            handoff_level = "L0"
            error_code = None
            # 服务端返回的具体 error_code 在上面第 358 行接住了，这里必须优先用它。
            # 丢掉它就只剩 FZ_SERVER_APPLY_FAILED 这个笼统码，Agent 和用户都无从判断
            # 到底是版本冲突、画布不存在还是别的，只能归类成「产品不稳定」。
        if not authoritative_commit:
            server_error_code = str(server_meta.get("error_code") or "").strip()[:200]
            error_code = (
                server_error_code
                or error_code
                or ("FZ_SERVER_APPLY_FAILED" if apply_err else "FZ_EMIT_UNVERIFIED")
            )

        result_payload = _with_tool_trace(
            {
                "schema": "canvas_chat_commands.v1",
                "project_id": project,
                "canvas_id": canvas_id,
                "command_id": command_id,
                "commands": normalized,
                "canvas_command_emitted": True,
                "generation_started": False,
                "coupling": "tight-v1",
                "auto_apply_expected": True,
                "server_applied": server_applied,
                "server_apply_error": apply_err,
                "server_apply_reason": str(apply_err or "").strip()[:1200] or None,
                "created_node_ids": created_ids,
                "applied_ops": applied_ops,
                "revision": revision,
                "readback_verified": readback_verified,
                "readback_verification": (
                    server_meta.get("readback_verification")
                    if isinstance(server_meta.get("readback_verification"), dict)
                    else None
                ),
                # Agent verification and browser reconciliation are separate
                # contracts. A complete receipt ends the model/tool loop;
                # the UI still performs one coalesced authoritative pull.
                "snapshot_required": not authoritative_commit,
                "ui_reconcile_required": authoritative_commit,
                "node_count": server_meta.get("node_count"),
                "edge_count": server_meta.get("edge_count"),
                "idempotent_replay": bool(server_meta.get("idempotent_replay")),
                "conflict_retried": bool(server_meta.get("conflict_retried")),
                "camera_applied": bool(server_meta.get("camera_applied")),
                "camera_verified_from_snapshot": bool(
                    server_meta.get("camera_verified_from_snapshot")
                ),
                "camera_updates": list(server_meta.get("camera_updates") or []),
                "structure_status": structure_status,
                "handoff_level": handoff_level,
                "error_code": error_code,
                "next_step": (
                    "When server_applied=true with a valid revision, created_node_ids and applied_ops "
                    "are authoritative and handoff L1 is complete without another snapshot. "
                    "Call freezone_get_canvas_snapshot only after a failed/incomplete receipt, "
                    "revision conflict, missing revision, or when normalized server fields are needed. "
                    "Never use python/search/navigate/browser tools to edit the canvas."
                ),
            },
            tool="freezone_emit_canvas_command",
            t0=t0,
            command_id=command_id,
            structure_status=structure_status,
            server_applied=server_applied,
            applied_ops=applied_ops,
            created_count=len(created_ids),
        )
        if authoritative_commit:
            result_payload["ui_patch_published"] = _publish_canvas_patch(
                result_payload,
                turn_id=str(args.get("source_turn_id") or "").strip() or None,
            )
        return tool_result(result_payload)
    except Exception as exc:
        return _freezone_tool_error(
            exc, tool="freezone_emit_canvas_command", t0=locals().get("t0")
        )


def _handle_apply_canvas_commands(args: dict[str, Any], **_: Any) -> str:
    """Canonical plugin-bus alias for the existing authoritative writer."""
    return runtime_handler("_handle_emit_canvas_command")(
        {**args, "_compatibility_route": True}
    )


def _handle_compatibility_emit_canvas_command(args: dict[str, Any], **_: Any) -> str:
    """Full-mode alias uses the same checkpoint gate as indexed compatibility writes."""

    return runtime_handler("_handle_emit_canvas_command")(
        {**args, "_compatibility_route": True}
    )


def _attach_action_dispatch(
    result: Any,
    *,
    route: dict[str, Any],
    decision: dict[str, Any] | None = None,
) -> Any:
    payload = _maybe_json(result) if isinstance(result, str) else result
    if not isinstance(payload, dict):
        return result
    specialist_result: dict[str, Any] | None = None
    attach_started_at = time.perf_counter()
    logger.info(
        "canvas dispatch stage=attach_entered payload_keys=%s",
        sorted(str(key) for key in payload)[:32],
    )
    try:
        # Keep the canonical dispatch gateway observable as the executor's
        # real handoff.  The bounded local contract avoids importing the full
        # application runtime from a Hermes tool worker.
        logger.info("canvas dispatch stage=attach_local_contract_started")
        specialist_result = _local_specialist_result(
            "village_canvas_dispatch_action",
            payload,
            arguments=payload,
            agent_task={
                "agent_id": "production_executor",
                "handler_id": "village_canvas_dispatch_action",
                "invocation": "dispatch_gateway",
            },
        )
        logger.info(
            "canvas dispatch stage=attach_result_built latency_ms=%d artifact_count=%d",
            int((time.perf_counter() - attach_started_at) * 1000),
            len(specialist_result.get("agent_artifacts") or [])
            if isinstance(specialist_result, dict)
            else 0,
        )
    except Exception:
        # Standalone Hermes workers may not have the project package mounted;
        # dispatch behavior must remain available in that compatibility mode.
        logger.warning(
            "canvas dispatch stage=attach_failed latency_ms=%d",
            int((time.perf_counter() - attach_started_at) * 1000),
            exc_info=True,
        )
        specialist_result = None
    logger.info(
        "canvas dispatch stage=attach_returned latency_ms=%d specialist=%s",
        int((time.perf_counter() - attach_started_at) * 1000),
        bool(specialist_result),
    )
    return tool_result(
        {
            **payload,
            "action_dispatch": {
                "schema": "canvas_action_dispatch.v1",
                "route": route,
                **({"decision": decision} if decision else {}),
            },
            **(
                {"agent_specialist_result": specialist_result}
                if specialist_result
                else {}
            ),
        }
    )


def _blocked_action_dispatch(
    *,
    decision: dict[str, Any],
    error_code: str,
    error: str,
    target_resolution: dict[str, Any] | None = None,
    clarification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _attach_action_dispatch(
        {
            "ok": False,
            "error_code": error_code,
            "error": error,
            "writes_applied": 0,
            **(
                {"clarification": clarification}
                if isinstance(clarification, dict)
                else {}
            ),
            **(
                {"target_resolution": target_resolution}
                if isinstance(target_resolution, dict)
                else {}
            ),
        },
        route={
            "schema": "canvas_action_route.v1",
            "lane": "blocked",
            "reason_code": error_code,
            "reason": error,
            "requires_durable_run": False,
            "requires_confirmation": False,
            **(
                {"target_resolution": target_resolution}
                if isinstance(target_resolution, dict)
                else {}
            ),
        },
        decision=decision,
    )


def _director_clarification_gate(
    *,
    request: str,
    goal: str,
    run_mode: str,
    args: dict[str, Any],
    task: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a blocking clarification result before any executable lane."""

    try:
        from novelvideo.creative_execution.director_clarification import (
            assess_director_clarification,
        )
    except Exception:
        # The plugin is also loaded by standalone Hermes workers.  If that
        # worker cannot import the project package, preserve the existing
        # route rather than turning a read-only gate into an infrastructure
        # failure; the normal API process always has the package available.
        return None

    canvas_facts = args.get("canvas_facts")
    if not isinstance(canvas_facts, dict):
        canvas_facts = args.get("current_canvas")
    canvas_nodes = (
        [item for item in canvas_facts.get("nodes", []) if isinstance(item, dict)]
        if isinstance(canvas_facts, dict)
        else []
    )
    raw_nodes = args.get("canvas_nodes")
    if isinstance(raw_nodes, list):
        canvas_nodes = [item for item in raw_nodes if isinstance(item, dict)]
    assessment = assess_director_clarification(
        request=request,
        goal=goal,
        run_mode=run_mode,
        director_intent_contract=(
            args.get("director_intent_contract")
            if isinstance(args.get("director_intent_contract"), dict)
            else None
        ),
        canvas_nodes=canvas_nodes,
        answers=(
            args.get("director_clarification_answers")
            if isinstance(args.get("director_clarification_answers"), dict)
            else None
        ),
        commands=args.get("commands")
        if isinstance(args.get("commands"), list)
        else None,
        task=task,
    )
    if not assessment.get("required"):
        return None
    authoritative_nodes = _clarification_canvas_nodes(args, canvas_nodes)
    if authoritative_nodes and authoritative_nodes != canvas_nodes:
        assessment = assess_director_clarification(
            request=request,
            goal=goal,
            run_mode=run_mode,
            director_intent_contract=(
                args.get("director_intent_contract")
                if isinstance(args.get("director_intent_contract"), dict)
                else None
            ),
            canvas_nodes=authoritative_nodes,
            answers=(
                args.get("director_clarification_answers")
                if isinstance(args.get("director_clarification_answers"), dict)
                else None
            ),
            commands=args.get("commands")
            if isinstance(args.get("commands"), list)
            else None,
            task=task,
        )
        if not assessment.get("required"):
            return None
    # T-212（用户指令「把闸门清理出去」+ oiioii §6.3「一键补齐，不硬停」）：
    # 澄清缺项优先用产品既定默认值补齐并放行回合，回执里披露采用了哪些假设；
    # 只有 creative_subject 这类没有合理默认值的内容缺口才继续拦。
    try:
        from novelvideo.creative_execution.director_clarification import (
            _ADAPTIVE_QUESTION_DEFAULTS as _CLARIFICATION_DEFAULTS,
        )
    except Exception:
        _CLARIFICATION_DEFAULTS = {}
    # 用户指令（2026-09-30）：这族闸门在 agent 通道一律不拦。连 creative_subject
    # 这类没有预设文案的字段也以「由小树自行确定」补齐——agent 的回复里仍可
    # 向用户确认，但工具不再中断回合。生产 API 路径不受影响。
    _FALLBACK_DEFAULT = "由小树按本轮请求与画布素材自行确定"
    pending_ids: list[str] = []
    raw_questions = assessment.get("questions")
    for item in raw_questions if isinstance(raw_questions, list) else []:
        if isinstance(item, dict):
            question_id = str(item.get("question_id") or "").strip()
            if question_id:
                pending_ids.append(question_id)
    if not pending_ids:
        single = str(assessment.get("question_id") or "").strip()
        if single:
            pending_ids = [single]
    if not pending_ids:
        return assessment
    supplied_answers = args.get("director_clarification_answers")
    answers = dict(supplied_answers) if isinstance(supplied_answers, dict) else {}
    assumed: dict[str, str] = {}
    for question_id in pending_ids:
        default = str(_CLARIFICATION_DEFAULTS.get(question_id) or _FALLBACK_DEFAULT)
        if not str(answers.get(question_id) or "").strip():
            answers[question_id] = default
            assumed[question_id] = default
    if not assumed:
        return None
    args["director_clarification_answers"] = answers
    args["assumed_clarification_defaults"] = assumed
    recheck = assess_director_clarification(
        request=request,
        goal=goal,
        run_mode=run_mode,
        director_intent_contract=(
            args.get("director_intent_contract")
            if isinstance(args.get("director_intent_contract"), dict)
            else None
        ),
        canvas_nodes=authoritative_nodes or canvas_nodes,
        answers=answers,
        commands=args.get("commands")
        if isinstance(args.get("commands"), list)
        else None,
        task=task,
    )
    if not recheck.get("required"):
        return None
    return recheck


def _clarification_canvas_nodes(
    args: dict[str, Any],
    supplied_nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Read the authoritative canvas before blocking a turn on a creative question.

    Script rows are authored canvas facts.  Whether the model happened to read
    the canvas first must not decide whether the operator is asked to answer a
    decision the canvas already spells out -- 2026-09-21 真机两发同题请求中，
    先读画布的一发直接开拍，没读的一发被"声音怎么处理"拦下，纯粹取决于
    模型当次的取数顺序。只有即将拦下时才补这一读，正常路径不增加往返。
    """

    try:
        project = str(args.get("project") or "").strip() or _default_project_id()
        canvas_id = _canvas_id_from_args(args)
    except Exception:
        return []
    try:
        response = _request(
            "GET",
            f"/api/v1/projects/{quote(project, safe='')}"
            f"/freezone/canvases/{quote(canvas_id, safe='')}",
        )
    except Exception:
        return []
    payload = _response_payload(response)
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        return []
    return [item for item in nodes if isinstance(item, dict)]


def _publish_canvas_patch(
    payload: dict[str, Any],
    *,
    turn_id: str | None,
) -> bool:
    """Mirror a verified write to open canvas peers when ACP hides tool bodies."""

    if not _agent_context_value("VILLAGE_CANVAS_AGENT_TOKEN"):
        return False
    project = str(payload.get("project_id") or "").strip()
    canvas_id = str(payload.get("canvas_id") or "").strip()
    command_id = str(payload.get("command_id") or "").strip()
    revision = payload.get("revision")
    commands = payload.get("commands")
    if (
        not project
        or not canvas_id
        or not command_id
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision <= 0
        or not isinstance(commands, list)
    ):
        return False
    started_at = time.perf_counter()
    logger.info(
        "canvas patch publish start command_id=%s revision=%s",
        command_id[:128],
        revision,
    )
    try:
        response = _request_with_timeout(
            "POST",
            "/api/v1/chat/canvas-patch",
            body={
                "project_id": project,
                "canvas_id": canvas_id,
                "command_id": command_id,
                "revision": revision,
                "commands": commands,
                "turn_id": turn_id,
                "server_applied": bool(payload.get("server_applied")),
                "snapshot_required": bool(payload.get("snapshot_required", True)),
                "ui_reconcile_required": bool(
                    payload.get("ui_reconcile_required", True)
                ),
                "structure_status": payload.get("structure_status"),
            },
            timeout_seconds=CANVAS_PATCH_TIMEOUT_SECONDS,
        )
        published = bool(isinstance(response, dict) and response.get("ok") is True)
        logger.info(
            "canvas patch publish returned command_id=%s latency_ms=%d published=%s",
            command_id[:128],
            int((time.perf_counter() - started_at) * 1000),
            published,
        )
        return published
    except Exception as exc:
        logger.warning(
            "canvas patch publish failed command_id=%s latency_ms=%d error=%s",
            command_id[:128],
            int((time.perf_counter() - started_at) * 1000),
            type(exc).__name__,
        )
        return False


def _publish_director_clarification(
    clarification: dict[str, Any],
    *,
    project_id: str,
    canvas_id: str,
    turn_id: str,
    answers: dict[str, Any] | None = None,
    request: str = "",
    run_mode: str = "draft",
) -> bool:
    """Persist the blocked director decision when ACP omits tool output bodies."""

    if not _agent_context_value("VILLAGE_CANVAS_AGENT_TOKEN"):
        return False
    project = str(project_id or "").strip()
    canvas = str(canvas_id or "").strip() or "default"
    source_turn = str(turn_id or "").strip()
    if not project or not source_turn or not isinstance(clarification, dict):
        return False
    safe_answers = {
        str(key)[:80]: str(value)[:4_000]
        for key, value in (answers or {}).items()
        if str(key).strip() and str(value).strip()
    }
    try:
        response = _request(
            "POST",
            "/api/v1/chat/director-clarification",
            body={
                "project_id": project,
                "canvas_id": canvas,
                "turn_id": source_turn,
                "conversation_id": (
                    _agent_context_value("VILLAGE_CANVAS_CONVERSATION_ID") or "main"
                ),
                "clarification": clarification,
                "director_clarification_answers": safe_answers,
                "director_request": str(request or "").strip()[:12_000],
                "director_brief_id": source_turn,
                "director_run_mode": (
                    run_mode if run_mode in {"draft", "auto"} else "draft"
                ),
            },
        )
        return bool(isinstance(response, dict) and response.get("ok") is True)
    except Exception:
        return False


_EXISTING_NODE_MUTATION_TYPES = frozenset(
    {
        "update_node_prompt",
        "update_node_label",
        "update_node_data",
        "update_node_camera",
        "move_node",
        "delete_node",
        "connect_nodes",
        "remove_edge",
    }
)

_DYNAMIC_EXISTING_NODE_COMMAND_TYPES = frozenset(
    {
        "update_node_prompt",
        "update_node_label",
        "update_node_data",
        "update_node_camera",
        "move_node",
        "connect_nodes",
        "remove_edge",
        # delete_node 不可撤销：纳进来是为了让 ready_write 检查点能授权它，
        # 而不是让它无门槛通过（T-217）。
        "delete_node",
    }
)

_CANVAS_CREATION_COMMAND_TYPES = frozenset(
    {
        "annotate",
        "create_canvas_node",
        "create_image_prompt_node",
        "create_shot_sequence",
        "create_video_prompt_node",
        "insert_starter_workflow",
    }
)
_ATOMIC_NODE_CREATION_COMMAND_TYPES = frozenset(
    {
        "annotate",
        "create_canvas_node",
        "create_image_prompt_node",
        "create_video_prompt_node",
    }
)


def _canvas_creation_batch(commands: object) -> bool:
    return isinstance(commands, list) and any(
        isinstance(command, dict)
        and str(command.get("type") or "").strip() in _CANVAS_CREATION_COMMAND_TYPES
        for command in commands
    )


def _single_created_node_transaction(commands: object) -> bool:
    """Recognize one node plus dependency edges as one atomic canvas write."""

    if not isinstance(commands, list) or not commands:
        return False
    creation_count = 0
    for command in commands:
        if not isinstance(command, dict):
            return False
        command_type = str(command.get("type") or "").strip()
        if command_type in _ATOMIC_NODE_CREATION_COMMAND_TYPES:
            creation_count += 1
            continue
        if command_type != "connect_nodes" or not any(
            str(command.get(field) or "").strip().startswith("$created:")
            for field in ("source", "target")
        ):
            return False
    return creation_count == 1


def _existing_node_references(commands: object) -> set[str]:
    if not isinstance(commands, list):
        return set()
    references: set[str] = set()
    for command in commands:
        if not isinstance(command, dict):
            continue
        command_type = str(command.get("type") or "").strip()
        fields = (
            ("source", "target")
            if command_type in {"connect_nodes", "remove_edge"}
            else ("node_id",)
        )
        for field in fields:
            reference = str(command.get(field) or "").strip()
            if reference and not reference.startswith("$"):
                references.add(reference)
    return references


def _validate_dynamic_existing_node_commands(commands: object) -> str | None:
    """Keep the receipt-backed Phase 2D writer on concrete existing targets."""
    if not isinstance(commands, list) or not commands:
        return "dynamic execution requires 1-20 commands"
    for command in commands:
        if not isinstance(command, dict):
            return "dynamic canvas commands must be objects"
        command_type = str(command.get("type") or "").strip()
        if command_type not in _DYNAMIC_EXISTING_NODE_COMMAND_TYPES:
            return f"dynamic execution does not allow command: {command_type}"
        if command_type in {
            "update_node_prompt",
            "update_node_label",
            "update_node_data",
            "update_node_camera",
            "move_node",
            "delete_node",
        }:
            reference = str(command.get("node_id") or "").strip()
            if not reference or reference.startswith("$"):
                return f"{command_type} requires a concrete existing node_id"
        else:
            for field in ("source", "target"):
                reference = str(command.get(field) or "").strip()
                if not reference or reference.startswith("$"):
                    return f"{command_type} requires a concrete existing {field}"
    return None


def _dynamic_command_batch_requires_checkpoint(commands: object) -> bool:
    """Identify a narrow existing-node mutation batch before target validation.

    The capability broker is callable without the parent ActionRouter. Require
    the same checkpoint for its mutation lane while leaving explicit structure
    creation available on the compatibility path.
    """

    if not isinstance(commands, list) or not commands:
        return False
    return all(
        isinstance(command, dict)
        and str(command.get("type") or "").strip()
        in _DYNAMIC_EXISTING_NODE_COMMAND_TYPES
        for command in commands
    )


def _dynamic_checkpoint_error(
    capability_id: str,
    card: dict[str, Any],
    arguments: dict[str, Any],
) -> Any | None:
    """Enforce the receipt-backed gate for dynamic canvas writes."""

    if card.get("side_effect") != "canvas_write":
        return None
    requires_checkpoint = _dynamic_command_batch_requires_checkpoint(
        arguments.get("commands")
    )
    if "dynamic_checkpoint" not in arguments:
        if not requires_checkpoint:
            return None
        return _skill_contract_error(
            capability_id,
            "checkpoint_required",
            "既有节点/连线修改必须先取得 ready_write checkpoint。",
        )
    checkpoint = arguments.get("dynamic_checkpoint")
    if not isinstance(checkpoint, dict):
        return _skill_contract_error(
            capability_id,
            "checkpoint_required",
            "动态画布写入必须携带 ready_write checkpoint。",
        )
    required = {
        "status": "ready_write",
        "ready": True,
        "execution_enabled": True,
        "capability_id": capability_id,
    }
    if any(checkpoint.get(key) != value for key, value in required.items()):
        return _skill_contract_error(
            capability_id,
            "checkpoint_required",
            "动态画布写入的 checkpoint 未达到 ready_write。",
        )
    if (
        not str(checkpoint.get("plan_revision") or "").strip()
        or not str(checkpoint.get("allowlist_revision") or "").strip()
    ):
        return _skill_contract_error(
            capability_id,
            "checkpoint_required",
            "动态画布写入的 checkpoint 缺少 revision。",
        )
    if not str(arguments.get("command_id") or "").strip():
        return _skill_contract_error(
            capability_id,
            "checkpoint_required",
            "动态画布写入必须显式提供 command_id。",
        )
    if not str(arguments.get("source_turn_id") or "").strip():
        return _skill_contract_error(
            capability_id,
            "checkpoint_required",
            "动态画布写入必须显式提供 source_turn_id。",
        )
    bindings = {
        "source_turn_id": str(arguments.get("source_turn_id") or "").strip(),
        "command_id": str(arguments.get("command_id") or "").strip(),
        "expected_canvas_revision": arguments.get("expected_canvas_revision"),
    }
    for key, expected in bindings.items():
        if checkpoint.get(key) != expected:
            return _skill_contract_error(
                capability_id,
                "checkpoint_mismatch",
                f"动态画布写入的 checkpoint 与本轮 {key} 不一致。",
            )
    expected_revision = arguments.get("expected_canvas_revision")
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
        return _skill_contract_error(
            capability_id,
            "checkpoint_required",
            "动态画布写入必须携带 expected_canvas_revision。",
        )
    command_error = _validate_dynamic_existing_node_commands(arguments.get("commands"))
    if command_error:
        return _skill_contract_error(
            capability_id, "dynamic_command_rejected", command_error
        )
    return None


def _legacy_camera_patch_batch(commands: object) -> bool:
    """Recognize the narrow camera compatibility transaction kept for old clients."""

    if not isinstance(commands, list) or not commands:
        return False
    saw_camera = False
    for command in commands:
        if not isinstance(command, dict):
            return False
        command_type = str(command.get("type") or "").strip()
        if command_type == "update_node_camera":
            saw_camera = True
            continue
        if command_type != "update_node_data":
            return False
        node_data = command.get("node_data")
        camera_keys = (
            "camera",
            "camera_movement",
            "cameraSelection",
            "cameraMovement",
        )
        if not (
            isinstance(node_data, dict) and any(key in node_data for key in camera_keys)
        ) and not any(command.get(key) not in (None, "") for key in camera_keys):
            return False
    return saw_camera


def _compatibility_batch_requires_checkpoint(commands: object) -> bool:
    """Gate concrete existing-target mutations at the legacy writer boundary."""

    if (
        not isinstance(commands, list)
        or not commands
        or _legacy_camera_patch_batch(commands)
    ):
        return False
    for command in commands:
        if not isinstance(command, dict):
            continue
        command_type = str(command.get("type") or "").strip()
        if command_type not in _EXISTING_NODE_MUTATION_TYPES:
            continue
        fields = (
            ("node_id",)
            if command_type
            in {
                "update_node_prompt",
                "update_node_label",
                "update_node_data",
                "update_node_camera",
                "move_node",
                "delete_node",
            }
            else ("source", "target")
        )
        if command_type == "connect_nodes" and any(
            str(command.get(field) or "").strip().startswith("$created:")
            for field in fields
        ):
            continue
        if any(
            str(command.get(field) or "").strip()
            and not str(command.get(field) or "").strip().startswith("$created:")
            for field in fields
        ):
            return True
    return False


def _existing_node_mutation_batch(commands: object) -> bool:
    """Keep auto-mode updates on the direct canvas lane.

    A concrete existing node/edge reference is required. Creation aliases are
    intentionally excluded so a mixed or new batch can still route normally.
    """

    if not isinstance(commands, list) or not commands:
        return False

    def existing_ref(value: object) -> bool:
        reference = str(value or "").strip()
        return bool(reference) and not reference.startswith("$created:")

    for command in commands:
        if not isinstance(command, dict):
            return False
        command_type = str(command.get("type") or "").strip()
        if command_type not in _EXISTING_NODE_MUTATION_TYPES:
            return False
        if command_type in {
            "update_node_prompt",
            "update_node_label",
            "update_node_data",
            "update_node_camera",
            "move_node",
            "delete_node",
        }:
            if not existing_ref(command.get("node_id")):
                return False
        elif not (
            existing_ref(command.get("source")) and existing_ref(command.get("target"))
        ):
            return False
    return True
