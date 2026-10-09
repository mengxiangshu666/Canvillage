"""Server-side canvas command execution with durable, verifiable receipts."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from importlib import resources
import json
import math
from pathlib import Path
import re
from typing import Any

from novelvideo.freezone import canvas_store, script_media_gateway
from novelvideo.freezone.canvas_agent_ids import mint_agent_node_id
from novelvideo.freezone.camera_catalog import get_image_camera_options
from novelvideo.workflow_runtime.action_router import (
    existing_node_mutation_batch,
    route_canvas_envelope,
)
from novelvideo.workflow_runtime.director_ledger import (
    build_director_ledger,
    validate_director_ledger,
)
from novelvideo.workflow_runtime.causal_binding import binding_from_envelope
from novelvideo.workflow_runtime.impact_planner import plan_canvas_impact
from novelvideo.workflow_runtime.semantic_edges import (
    SEMANTIC_EDGE_SCHEMA,
    edge_relation,
    normalize_edge_relation,
)
from novelvideo.freezone.starter_workflow_contract import (
    StarterWorkflowContractError,
    validate_starter_workflow_catalog,
)
from novelvideo.services.video_request_contract import (
    normalize_video_resolution_value,
    validate_structured_video_capability,
)
from novelvideo.production.continuity_contract import compile_shot_prompt
from novelvideo.production.metadata import (
    ProductionMetadataError,
    stamp_production_metadata,
)
from novelvideo.production.shot_contract import validate_shot_contract
from novelvideo.generators.image_request_policy import (
    is_valid_image_aspect_ratio,
    is_valid_image_size,
)
from novelvideo.creative_execution.director_clarification import (
    DirectorClarificationRequiredError,
    require_director_clarification_ready,
)
from novelvideo.chat.execution_context import validate_execution_context


_CANVAS_COMMAND_RECEIPTS_KEY = "village_canvas_agent_command_ids"
_CANVAS_COMMAND_RECEIPT_DETAILS_KEY = "village_canvas_command_receipts_v2"
_RECEIPT_RETENTION = 500
_PRESENTATION_COMMANDS = frozenset({"focus_node", "select_node"})
_ALIAS_NODE_REFS = frozenset(
    {
        "",
        "$selected",
        "selected",
        "@selected",
        "current",
        "$current",
        "$pinned",
        "pinned",
        "@pinned",
    }
)
_GENERIC_NODE_TYPES = frozenset(
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
_COUNTS = frozenset({1, 2, 4, 6, 8, 12})
_VIDEO_MODES = frozenset(
    {
        "textToVideo",
        "allReference",
        "imageToVideo",
        "firstLastFrame",
        "imageReference",
        "videoEdit",
    }
)
_AGENT_NODE_LAYOUT_SIZES: dict[str, tuple[float, float]] = {
    "imageGenNode": (580.0, 360.0),
    "videoNode": (580.0, 380.0),
    "textAnnotationNode": (440.0, 320.0),
    "audioNode": (480.0, 210.0),
}
_AGENT_NODE_LAYOUT_MARGIN = 48.0


class CanvasCommandError(RuntimeError):
    """Stable command failure that is safe to persist and present to clients."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        op_index: int | None = None,
        current_revision: int | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.op_index = op_index
        self.current_revision = current_revision
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": False,
            "error": str(self),
            "error_code": self.code,
            "op_index": self.op_index,
            "current_revision": self.current_revision,
            "details": self.details,
        }


@dataclass(slots=True)
class _Mutation:
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    normalized_envelope: dict[str, Any]
    expectation: dict[str, Any]
    op_results: list[dict[str, Any]]
    created_node_ids: list[str]
    affected_node_ids: list[str]
    camera_updates: list[dict[str, Any]]
    structural_ops: int
    presentation_ops: int


def _is_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _is_alias(value: object) -> bool:
    raw = str(value or "").strip().lower()
    return raw in _ALIAS_NODE_REFS or bool(re.fullmatch(r"\$?pinned:\d+", raw))


def _resolve_batch_node_ref(value: object, created_ids: list[str]) -> str:
    """Resolve a stable reference to a node created earlier in this batch."""

    raw = str(value or "").strip()
    match = re.fullmatch(r"\$created:(\d+)", raw, flags=re.IGNORECASE)
    if match is None:
        return raw
    index = int(match.group(1))
    if index >= len(created_ids):
        raise CanvasCommandError(
            f"批内节点引用不存在：{raw}",
            code="canvas_command_batch_ref_missing",
            details={"reference": raw, "created_count": len(created_ids)},
        )
    return created_ids[index]


def _edge_id(source: str, target: str) -> str:
    return f"xy-edge__{source}source-{target}target"


def _edge_node_revision(node: dict[str, Any]) -> int:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    for value in (
        node.get("revision"),
        data.get("assetRevision"),
        data.get("asset_revision"),
        data.get("revision"),
    ):
        if type(value) is int and value > 0:
            return value
    return 0


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _resolved_canvas_id(value: object, fallback: object) -> str:
    """Resolve a request canvas id while treating blank values as omitted."""

    candidate = str(value or "").strip()
    return candidate or str(fallback or "").strip()


def _default_node_data(node_type: str) -> dict[str, Any]:
    """Keep server-created nodes renderable without injecting hidden models."""
    defaults: dict[str, dict[str, Any]] = {
        "uploadNode": {
            "imageUrl": None,
            "previewImageUrl": None,
            "aspectRatio": "1:1",
            "isSizeManuallyAdjusted": False,
            "sourceFileName": None,
        },
        "imageNode": {
            "imageUrl": None,
            "previewImageUrl": None,
            "aspectRatio": "1:1",
            "requestAspectRatio": "auto",
            "prompt": "",
            "model": "",
            "size": "2K",
            "extraParams": {},
            "generationMode": "text_to_image",
            "isGenerating": False,
        },
        "imageGenNode": {
            "imageUrl": None,
            "previewImageUrl": None,
            # A newly created node has no model contract yet. Keep media
            # parameters unset until the selected model declares them.
            "aspectRatio": "",
            "requestAspectRatio": "",
            "prompt": "",
            "model": "",
            "size": "",
            "count": 1,
            "isGenerating": False,
        },
        "exportImageNode": {
            "imageUrl": None,
            "previewImageUrl": None,
            "aspectRatio": "1:1",
            "resultKind": "generic",
        },
        "textAnnotationNode": {
            "content": "",
            "text": "",
            "model": "",
            "extraParams": {},
            "isGenerating": False,
        },
        "storyboardGenNode": {
            "gridRows": 2,
            "gridCols": 2,
            "frames": [],
            "ratioControlMode": "cell",
            "model": "",
            "size": "",
            "requestAspectRatio": "",
            "extraParams": {},
            "imageUrl": None,
            "previewImageUrl": None,
            "aspectRatio": "",
            "isGenerating": False,
        },
        "videoNode": {
            "videoUrl": None,
            "previewImageUrl": None,
            "aspectRatio": "",
            "prompt": "",
            "genMode": "textToVideo",
            "model": "",
            "quality": "",
            "durationSec": 5,
            "generateAudio": True,
            "generateAudioUserSet": False,
            "dialogueText": "",
            "spokenDialogue": [],
            "audioType": "",
            "speaker": "",
            "nativeAudioStrategy": "native",
            "audioAssetRef": "",
            "count": 1,
            "isGenerating": False,
        },
        "audioNode": {
            "audioUrl": None,
            "sourceFileName": None,
            "durationMs": None,
            "text": "",
            "emotionPrompt": "",
            "voiceLanguage": "",
            "isGenerating": False,
        },
        "videoComposeNode": {
            "resultVideoUrl": None,
            "previewImageUrl": None,
            "resolution": "1080p",
        },
        "scriptNode": {
            "prompt": "",
            "model": "",
            "scriptResult": None,
            "isGenerating": False,
        },
        "pano360ViewerNode": {
            "imageUrl": None,
            "previewImageUrl": None,
            "sourceNodeId": None,
            "sphereCorrectionDeg": {"roll": 0, "pitch": 0, "yaw": 0},
            "frontYawDeg": 0,
            "fovDeg": 70,
            "lastExportedEntry": None,
        },
        "threeDWorldNode": {
            "prompt": "",
            "model": "",
            "taskKey": None,
            "plyUrl": None,
            "sourceNodeId": None,
            "sourceKind": None,
            "isGenerating": False,
            "errorMessage": None,
        },
    }
    return deepcopy(defaults.get(node_type, {}))


def _camera_error(
    message: str,
    *,
    op_index: int | None,
    field: str,
    value: object = None,
    allowed: list[object] | None = None,
    code: str = "canvas_camera_option_unsupported",
) -> CanvasCommandError:
    details: dict[str, Any] = {"field": field}
    if value is not None:
        details["value"] = value
    if allowed is not None:
        details["allowed"] = allowed
    return CanvasCommandError(
        message,
        code=code,
        op_index=op_index,
        details=details,
    )


def _camera_alias_value(value: dict[str, Any], *keys: str) -> object:
    candidates = [value.get(key) for key in keys if value.get(key) not in (None, "")]
    if not candidates:
        return None
    normalized = {str(candidate).strip() for candidate in candidates}
    if len(normalized) > 1:
        raise ValueError(f"conflicting aliases: {', '.join(keys)}")
    return candidates[0]


def _normalize_image_camera(
    value: object,
    *,
    op_index: int | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(value, dict) or not value:
        raise _camera_error(
            "图片摄像机参数必须是非空对象",
            op_index=op_index,
            field="camera",
            code="canvas_camera_invalid",
        )
    options = get_image_camera_options()
    camera_body_ids = [str(item["id"]) for item in options["camera_bodies"]]
    lens_ids = [str(item["id"]) for item in options["lenses"]]
    focal_lengths = [int(item) for item in options["focal_lengths_mm"]]
    apertures = [str(item) for item in options["apertures"]]
    try:
        camera_body = _camera_alias_value(
            value,
            "camera_body_id",
            "cameraBodyId",
            "camera_body",
        )
        lens = _camera_alias_value(value, "lens_id", "lensId", "lens")
        focal_length = _camera_alias_value(
            value,
            "focal_length_mm",
            "focalLengthMm",
        )
        aperture = _camera_alias_value(value, "aperture")
    except ValueError as exc:
        raise _camera_error(
            "摄像机参数包含互相冲突的字段",
            op_index=op_index,
            field="camera",
            code="canvas_camera_ambiguous",
        ) from exc

    command_camera: dict[str, Any] = {}
    selection: dict[str, Any] = {}
    if camera_body not in (None, ""):
        camera_body_id = str(camera_body).strip()
        if camera_body_id not in camera_body_ids:
            raise _camera_error(
                "摄像机机身不在当前画布目录中",
                op_index=op_index,
                field="camera_body_id",
                value=camera_body_id,
                allowed=camera_body_ids,
            )
        command_camera["camera_body_id"] = camera_body_id
        selection["cameraBodyId"] = camera_body_id
    if lens not in (None, ""):
        lens_id = str(lens).strip()
        if lens_id not in lens_ids:
            raise _camera_error(
                "镜头不在当前画布目录中",
                op_index=op_index,
                field="lens_id",
                value=lens_id,
                allowed=lens_ids,
            )
        command_camera["lens_id"] = lens_id
        selection["lensId"] = lens_id
    if focal_length not in (None, ""):
        if not _is_number(focal_length) or not float(focal_length).is_integer():
            raise _camera_error(
                "焦距必须是当前目录中的整数毫米值",
                op_index=op_index,
                field="focal_length_mm",
                value=focal_length,
                allowed=focal_lengths,
                code="canvas_camera_invalid",
            )
        focal_length_mm = int(float(focal_length))
        if focal_length_mm not in focal_lengths:
            raise _camera_error(
                "焦距不在当前画布目录中",
                op_index=op_index,
                field="focal_length_mm",
                value=focal_length_mm,
                allowed=focal_lengths,
            )
        command_camera["focal_length_mm"] = focal_length_mm
        selection["focalLengthMm"] = focal_length_mm
    if aperture not in (None, ""):
        aperture_value = str(aperture).strip()
        if aperture_value not in apertures:
            raise _camera_error(
                "光圈不在当前画布目录中",
                op_index=op_index,
                field="aperture",
                value=aperture_value,
                allowed=apertures,
            )
        command_camera["aperture"] = aperture_value
        selection["aperture"] = aperture_value
    if not selection:
        raise _camera_error(
            "图片摄像机参数没有可执行字段",
            op_index=op_index,
            field="camera",
            code="canvas_camera_invalid",
        )
    return command_camera, selection


def _normalize_video_camera_movement(
    value: object,
    *,
    op_index: int | None,
) -> str:
    movement = str(value or "").strip()
    from novelvideo.freezone.video_node import (
        get_video_camera_template,
        get_video_camera_templates,
    )

    allowed = [str(item.get("id") or "") for item in get_video_camera_templates()]
    if not movement:
        raise _camera_error(
            "视频运镜模板不能为空",
            op_index=op_index,
            field="camera_movement",
            allowed=allowed,
            code="canvas_camera_invalid",
        )
    template = get_video_camera_template(movement)
    if template is None:
        raise _camera_error(
            "视频运镜模板不在当前画布目录中",
            op_index=op_index,
            field="camera_movement",
            value=movement,
            allowed=allowed,
        )
    # Persist the canonical backend id even when an older frontend fallback
    # submits its kebab-case alias (for example ``dolly-in``).
    return str(template["id"])


def _nested_camera_changed_fields(
    field: str,
    before: object,
    after: object,
) -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        keys = sorted(
            key for key in set(before) | set(after) if before.get(key) != after.get(key)
        )
        return [f"{field}.{key}" for key in keys]
    return [] if before == after else [field]


def _verify_camera_updates(
    snapshot: dict[str, Any],
    camera_updates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    nodes = {
        str(node.get("id") or "").strip(): node
        for node in (snapshot.get("nodes") or [])
        if isinstance(node, dict) and str(node.get("id") or "").strip()
    }
    verified_updates: list[dict[str, Any]] = []
    applied_updates = 0
    for raw_update in camera_updates:
        update = deepcopy(raw_update)
        applied = update.get("camera_applied") is True
        verified = False
        if applied:
            applied_updates += 1
            node = nodes.get(str(update.get("node_id") or "").strip())
            data = _as_dict(node.get("data")) if node is not None else {}
            field = str(update.get("camera_field") or "")
            verified = field in data and data.get(field) == update.get("camera_value")
        update["camera_verified_from_snapshot"] = verified
        verified_updates.append(update)
    return verified_updates, bool(applied_updates) and all(
        update.get("camera_verified_from_snapshot") is True
        for update in verified_updates
        if update.get("camera_applied") is True
    )


def _camera_update_for_node(
    *,
    node_id: str,
    node_type: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any] | None:
    field = (
        "cameraSelection"
        if node_type == "imageGenNode"
        else "cameraMovement"
        if node_type == "videoNode"
        else ""
    )
    if not field:
        return None
    changed_fields = _nested_camera_changed_fields(
        field,
        before.get(field),
        after.get(field),
    )
    if not changed_fields:
        return None
    return {
        "node_id": node_id,
        "node_type": node_type,
        "camera_field": field,
        "camera_changed_fields": changed_fields,
        "camera_value": deepcopy(after.get(field)),
        "camera_applied": True,
        "camera_verified_from_snapshot": False,
    }


def _normalize_camera_patch(
    *,
    command: dict[str, Any],
    node_type: str,
    patch: dict[str, Any] | None,
    current_data: dict[str, Any] | None = None,
    op_index: int | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    normalized_patch = deepcopy(patch or {})
    clear_camera = command.get("clear_camera") is True
    # ``apply`` runs this function twice on the same command: the envelope pass
    # first, then the apply pass. The envelope pass turns ``clear_camera`` into an
    # explicit ``None`` selection and drops the flag, so the apply pass sees only
    # that encoded form. An explicitly present null selection must therefore still
    # count as a clear request -- otherwise clearing a camera is rejected as
    # "missing camera parameters".
    if (
        not clear_camera
        and str(command.get("type") or "").strip() == "update_node_camera"
    ):
        clear_camera = any(
            key in normalized_patch and normalized_patch.get(key) in (None, "")
            for key in ("cameraSelection", "cameraMovement")
        )
    camera_sources = [
        value
        for value in (
            command.get("camera"),
            normalized_patch.pop("camera", None),
            normalized_patch.get("cameraSelection"),
        )
        if value not in (None, "")
    ]
    movement_sources = [
        value
        for value in (
            command.get("camera_movement"),
            normalized_patch.pop("camera_movement", None),
            normalized_patch.get("cameraMovement"),
        )
        if value not in (None, "")
    ]
    has_camera_request = bool(camera_sources or movement_sources or clear_camera)
    if not has_camera_request:
        return normalized_patch, None
    if node_type == "imageGenNode":
        if movement_sources:
            raise _camera_error(
                "图片节点不接受视频运镜模板",
                op_index=op_index,
                field="camera_movement",
                code="canvas_camera_node_type_mismatch",
            )
        if clear_camera:
            if camera_sources:
                raise _camera_error(
                    "清除摄像机时不能同时提交摄像机参数",
                    op_index=op_index,
                    field="camera",
                    code="canvas_camera_ambiguous",
                )
            normalized_patch["cameraSelection"] = None
            return normalized_patch, {
                "node_type": node_type,
                "requested": {"clear_camera": True},
                "field": "cameraSelection",
                "normalized": None,
            }
        requested_camera: dict[str, Any] = {}
        requested_selection: dict[str, Any] = {}
        for source in camera_sources:
            command_camera, selection = _normalize_image_camera(
                source,
                op_index=op_index,
            )
            for key, value in selection.items():
                if key in requested_selection and requested_selection[key] != value:
                    raise _camera_error(
                        "同一命令包含不一致的图片摄像机参数",
                        op_index=op_index,
                        field="camera",
                        code="canvas_camera_ambiguous",
                    )
                requested_selection[key] = value
            requested_camera.update(command_camera)
        current_selection = _as_dict(_as_dict(current_data).get("cameraSelection"))
        next_selection = {**current_selection, **requested_selection}
        normalized_patch["cameraSelection"] = next_selection
        return normalized_patch, {
            "node_type": node_type,
            "requested": {"camera": requested_camera},
            "field": "cameraSelection",
            "normalized": next_selection,
        }
    if node_type == "videoNode":
        if camera_sources:
            raise _camera_error(
                "视频节点不接受图片机身、镜头、焦距或光圈参数",
                op_index=op_index,
                field="camera",
                code="canvas_camera_node_type_mismatch",
            )
        if clear_camera:
            if movement_sources:
                raise _camera_error(
                    "清除运镜时不能同时提交运镜模板",
                    op_index=op_index,
                    field="camera_movement",
                    code="canvas_camera_ambiguous",
                )
            normalized_patch["cameraMovement"] = None
            return normalized_patch, {
                "node_type": node_type,
                "requested": {"clear_camera": True},
                "field": "cameraMovement",
                "normalized": None,
            }
        movements = [
            _normalize_video_camera_movement(value, op_index=op_index)
            for value in movement_sources
        ]
        movement = movements[0]
        if any(value != movement for value in movements[1:]):
            raise _camera_error(
                "同一命令包含不一致的视频运镜模板",
                op_index=op_index,
                field="camera_movement",
                code="canvas_camera_ambiguous",
            )
        normalized_patch["cameraMovement"] = movement
        return normalized_patch, {
            "node_type": node_type,
            "requested": {"camera_movement": movement},
            "field": "cameraMovement",
            "normalized": movement,
        }
    raise _camera_error(
        "当前节点类型不支持摄像机控制",
        op_index=op_index,
        field="node_type",
        value=node_type,
        allowed=["imageGenNode", "videoNode"],
        code="canvas_camera_node_type_mismatch",
    )


def _node_parameters(
    command: dict[str, Any],
    node_type: str,
    *,
    op_index: int | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {}
    aspect_ratio = str(command.get("aspect_ratio") or "").strip()
    model = str(command.get("model") or "").strip()
    count = command.get("count")
    if node_type == "imageGenNode":
        _validate_image_prompt_command(command, op_index=op_index)
        if is_valid_image_aspect_ratio(aspect_ratio, allow_auto=True):
            data["requestAspectRatio"] = aspect_ratio
            if aspect_ratio != "auto":
                data["aspectRatio"] = aspect_ratio
        image_size = str(command.get("image_size") or "").strip()
        if is_valid_image_size(image_size):
            data["size"] = image_size
        if model:
            data["model"] = model
        if isinstance(count, int) and not isinstance(count, bool) and count in _COUNTS:
            data["count"] = count
        camera = command.get("camera")
        if camera not in (None, ""):
            _, selection = _normalize_image_camera(camera, op_index=op_index)
            data["cameraSelection"] = selection
        if command.get("camera_movement") not in (None, ""):
            raise _camera_error(
                "图片节点不接受视频运镜模板",
                op_index=op_index,
                field="camera_movement",
                code="canvas_camera_node_type_mismatch",
            )
        return data

    raw_parameters = command.get("parameters")
    if not isinstance(raw_parameters, dict):
        raw_parameters = command.get("parameter_values")
    if isinstance(raw_parameters, dict):
        # Keep provider-specific values on the node for direct canvas
        # dispatch. Secret-like keys are excluded before persistence.
        data["parameters"] = {
            str(key): value
            for key, value in raw_parameters.items()
            if str(key).strip()
            and not re.search(
                r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|cookie|authorization)",
                str(key),
                re.IGNORECASE,
            )
        }
    raw_mapping = command.get("provider_mapping")
    if not isinstance(raw_mapping, dict):
        raw_mapping = command.get("providerMapping")
    if isinstance(raw_mapping, dict) and raw_mapping:
        data["providerMapping"] = {
            str(key): str(value)
            for key, value in raw_mapping.items()
            if str(key).strip() and str(value).strip()
        }
    if isinstance(command.get("opaque"), list) and command["opaque"]:
        data["opaque"] = deepcopy(command["opaque"])
    size = str(command.get("size") or "").strip()
    if size:
        data["size"] = size
        data["sizeSlot"] = size
    size_field = str(
        command.get("size_field") or command.get("sizeField") or ""
    ).strip()
    if size_field:
        data["sizeField"] = size_field

    if is_valid_image_aspect_ratio(aspect_ratio, allow_auto=False):
        data["aspectRatio"] = aspect_ratio
    quality = str(command.get("video_quality") or "").strip()
    quality_resolution = normalize_video_resolution_value(quality)
    if quality_resolution:
        data["quality"] = (
            f"{quality_resolution[:-1]}P"
            if quality_resolution.endswith("p")
            else quality_resolution.upper()
        )
    resolution = normalize_video_resolution_value(command.get("resolution"))
    if resolution is None and quality_resolution:
        resolution = quality_resolution
    if resolution:
        data["resolution"] = resolution
    duration = command.get("duration_sec")
    if (
        isinstance(duration, int)
        and not isinstance(duration, bool)
        and 1 <= duration <= 120
    ):
        data["durationSec"] = duration
    mode = str(command.get("generation_mode") or "").strip()
    if mode in _VIDEO_MODES:
        data["genMode"] = mode
    if model:
        data["model"] = _normalize_video_model_backend(model)
    shot_id = str(command.get("shot_id") or command.get("shotId") or "").strip()
    if shot_id:
        data["shotId"] = shot_id
    for source_key, target_key, limit in (
        ("shot_type", "shotType", 120),
        ("lens", "lens", 120),
        ("camera_position", "cameraPosition", 500),
        ("subject", "subject", 500),
        ("action", "action", 1000),
        ("prompt_source", "promptSource", None),
        ("transition", "transition", 300),
    ):
        value = str(command.get(source_key) or command.get(target_key) or "").strip()
        if value:
            data[target_key] = value[:limit]
    for source_key, target_key in (
        ("continuity_in", "continuityIn"),
        ("continuity_out", "continuityOut"),
    ):
        value = command.get(source_key)
        if isinstance(value, dict) and value:
            data[target_key] = value
    if isinstance(command.get("generate_audio"), bool):
        data["generateAudio"] = command["generate_audio"]
        if "generate_audio_explicit" in command:
            data["generateAudioUserSet"] = bool(command["generate_audio_explicit"])
        elif "generateAudioExplicit" in command:
            data["generateAudioUserSet"] = bool(command["generateAudioExplicit"])
        else:
            data["generateAudioUserSet"] = True
    dialogue_text = str(
        command.get("dialogue_text") or command.get("dialogueText") or ""
    ).strip()
    if dialogue_text:
        data["dialogueText"] = dialogue_text
    raw_spoken_dialogue = command.get("spoken_dialogue")
    if not isinstance(raw_spoken_dialogue, (list, tuple)):
        raw_spoken_dialogue = command.get("spokenDialogue")
    if isinstance(raw_spoken_dialogue, (list, tuple)):
        spoken = [
            str(item).strip() for item in raw_spoken_dialogue if str(item).strip()
        ]
        if spoken:
            data["spokenDialogue"] = spoken
    audio_type = str(
        command.get("audio_type") or command.get("audioType") or ""
    ).strip()
    if audio_type:
        data["audioType"] = audio_type
    speaker = str(command.get("speaker") or "").strip()
    if speaker:
        data["speaker"] = speaker
    native_strategy = str(
        command.get("native_audio_strategy") or command.get("nativeAudioStrategy") or ""
    ).strip()
    if native_strategy:
        data["nativeAudioStrategy"] = native_strategy
    audio_asset_ref = str(
        command.get("audio_asset_ref") or command.get("audioAssetRef") or ""
    ).strip()
    if audio_asset_ref:
        data["audioAssetRef"] = audio_asset_ref
    raw_references = command.get("reference_items")
    if not isinstance(raw_references, (list, tuple)):
        raw_references = command.get("references")
    if isinstance(raw_references, (list, tuple)):
        references = [
            {
                "type": str(item.get("type") or "image"),
                "path": str(item.get("path") or item.get("url") or ""),
                "role": str(item.get("role") or ""),
            }
            for item in raw_references
            if isinstance(item, dict)
            and str(item.get("path") or item.get("url") or "").strip()
        ]
        if references:
            data["referenceItems"] = references
    raw_bindings = command.get("reference_bindings")
    if not isinstance(raw_bindings, (dict,)):
        raw_bindings = command.get("referenceBindings")
    if isinstance(raw_bindings, dict):
        bindings = {
            str(role).strip(): [
                str(asset_id).strip()
                for asset_id in values
                if str(asset_id or "").strip()
            ]
            for role, values in raw_bindings.items()
            if isinstance(values, (list, tuple))
            and any(str(asset_id or "").strip() for asset_id in values)
        }
        if bindings:
            data["referenceBindings"] = bindings
    first_frame = str(
        command.get("first_frame") or command.get("firstFrame") or ""
    ).strip()
    if first_frame:
        data["firstFrame"] = first_frame
    last_frame = str(
        command.get("last_frame") or command.get("lastFrame") or ""
    ).strip()
    if last_frame:
        data["lastFrame"] = last_frame
    first_frame_path = str(command.get("first_frame_path") or "").strip()
    if first_frame_path:
        data["firstFramePath"] = first_frame_path
    last_frame_path = str(command.get("last_frame_path") or "").strip()
    if last_frame_path:
        data["lastFramePath"] = last_frame_path
    shot_contract = command.get("shot_contract") or command.get("shotContract")
    if isinstance(shot_contract, dict):
        contract_issues = validate_shot_contract(shot_contract)
        if contract_issues:
            raise CanvasCommandError(
                "视频节点的 shot_contract 未通过执行合同",
                code="canvas_shot_contract_invalid",
                op_index=op_index,
                details={"issues": contract_issues},
            )
        data["shotContract"] = deepcopy(shot_contract)
    if isinstance(count, int) and not isinstance(count, bool) and count in _COUNTS:
        data["count"] = count
    camera_patch, _ = _normalize_camera_patch(
        command=command, node_type="videoNode", patch={}, op_index=op_index,
    )
    data.update(camera_patch)
    return data


def _validate_image_prompt_command(
    command: dict[str, Any],
    *,
    op_index: int | None = None,
) -> None:
    """Validate explicit direct-image node parameters before persistence.

    The image execution adapter and the model picker both consume the cached
    upstream capability profile.  Canvas drafts must use that same profile so
    an impossible mode, ratio, size, or reference count is rejected before it
    becomes durable node state.  Legacy non-direct model IDs remain untouched.
    """

    model_ref = str(command.get("model") or "").strip()
    if not model_ref:
        return
    if not model_ref.casefold().startswith("direct/"):
        return

    try:
        from novelvideo.generators.direct_image_models import resolve_direct_image_model

        model = resolve_direct_image_model(model_ref)
    except Exception as exc:
        raise CanvasCommandError(
            "直连生图模型能力读取失败，无法创建图片节点",
            code="canvas_image_capability_unavailable",
            op_index=op_index,
            details={"model": model_ref, "error": type(exc).__name__},
        ) from exc
    if model is None:
        raise CanvasCommandError(
            "画布命令引用的直连生图模型不存在或已停用",
            code="canvas_image_model_unavailable",
            op_index=op_index,
            details={"model": model_ref},
        )

    profile = model.profile
    references = command.get("reference_items")
    if not isinstance(references, (list, tuple)):
        references = command.get("references")
    if not isinstance(references, (list, tuple)):
        references = ()
    reference_count = sum(
        1
        for item in references
        if isinstance(item, dict)
        and str(item.get("path") or item.get("url") or "").strip()
    )
    bindings = command.get("reference_bindings")
    if not isinstance(bindings, dict):
        bindings = command.get("referenceBindings")
    if isinstance(bindings, dict):
        reference_count = max(
            reference_count,
            sum(
                len(
                    [
                        str(asset_id).strip()
                        for asset_id in values
                        if str(asset_id or "").strip()
                    ]
                )
                for values in bindings.values()
                if isinstance(values, (list, tuple, set))
            ),
        )

    mode = str(command.get("generation_mode") or "").strip()
    mode_aliases = {
        "texttoimage": "text_to_image",
        "text_to_image": "text_to_image",
        "t2i": "text_to_image",
        "imagetoimage": "image_to_image",
        "image_to_image": "image_to_image",
        "i2i": "image_to_image",
    }
    normalized_mode = mode_aliases.get(
        mode.casefold().replace("-", "").replace(" ", "")
    )
    if normalized_mode == "image_to_image" and not reference_count:
        raise CanvasCommandError(
            "图生图节点必须绑定至少一张参考图",
            code="canvas_image_reference_missing",
            op_index=op_index,
            details={"model": model_ref, "mode": mode},
        )
    if normalized_mode == "text_to_image" and reference_count:
        raise CanvasCommandError(
            "文生图节点不能携带参考图，请切换为图生图模式",
            code="canvas_image_mode_reference_mismatch",
            op_index=op_index,
            details={
                "model": model_ref,
                "mode": mode,
                "reference_count": reference_count,
            },
        )
    if reference_count and "image_to_image" not in profile.modes:
        raise CanvasCommandError(
            "当前直连生图模型不支持图生图参考输入",
            code="canvas_image_mode_unsupported",
            op_index=op_index,
            details={"model": model_ref, "reference_count": reference_count},
        )
    if normalized_mode and normalized_mode not in profile.modes:
        raise CanvasCommandError(
            "图片节点的生成模式不符合当前模型能力合同",
            code="canvas_image_mode_unsupported",
            op_index=op_index,
            details={
                "model": model_ref,
                "mode": mode,
                "supported_modes": list(profile.modes),
            },
        )

    aspect_ratio = str(command.get("aspect_ratio") or "").strip().replace("：", ":")
    if aspect_ratio and aspect_ratio.casefold() != "auto":
        if profile.supports_custom_aspect_ratio:
            aspect_ok = is_valid_image_aspect_ratio(aspect_ratio)
        else:
            aspect_ok = (
                bool(profile.aspect_ratio_options)
                and aspect_ratio in profile.aspect_ratio_options
            )
        if not aspect_ok:
            raise CanvasCommandError(
                "图片节点的比例不符合当前模型能力合同",
                code="canvas_image_aspect_unsupported",
                op_index=op_index,
                details={
                    "model": model_ref,
                    "aspect_ratio": aspect_ratio,
                    "supported": list(profile.aspect_ratio_options),
                    "supports_custom": profile.supports_custom_aspect_ratio,
                },
            )

    image_size = str(command.get("image_size") or "").strip()
    if image_size:
        from novelvideo.generators.image_request_policy import normalize_image_size

        normalized_size = normalize_image_size(image_size, provider="newapi")
        if profile.supports_custom_resolution:
            size_ok = is_valid_image_size(normalized_size)
        else:
            supported_sizes = {
                normalize_image_size(value, provider="newapi").casefold()
                for value in profile.resolution_options
            }
            size_ok = (
                bool(supported_sizes) and normalized_size.casefold() in supported_sizes
            )
        if not size_ok:
            raise CanvasCommandError(
                "图片节点的尺寸不符合当前模型能力合同",
                code="canvas_image_size_unsupported",
                op_index=op_index,
                details={
                    "model": model_ref,
                    "image_size": image_size,
                    "supported": list(profile.resolution_options),
                    "supports_custom": profile.supports_custom_resolution,
                },
            )

    if reference_count:
        from novelvideo.generators.direct_model_capability_cache import (
            get_cached_direct_model_capability,
        )
        from novelvideo.generators.direct_model_capabilities import (
            direct_model_capability_summary,
        )

        cached = get_cached_direct_model_capability(
            base_url=model.base_url,
            kind="image",
            upstream_model=model.upstream_model,
        )
        metadata = cached.get("modelMetadata")
        summary = direct_model_capability_summary(
            "image",
            model.upstream_model,
            protocol=model.protocol,
            base_url=model.base_url,
            metadata=metadata if isinstance(metadata, dict) else None,
        )
        limits = summary.get("referenceLimits")
        max_references = (
            int(limits.get("images") or 0) if isinstance(limits, dict) else 0
        )
        if max_references > 0 and reference_count > max_references:
            raise CanvasCommandError(
                "图片节点参考图数量超过当前模型能力上限",
                code="canvas_image_reference_limit_exceeded",
                op_index=op_index,
                details={
                    "model": model_ref,
                    "reference_count": reference_count,
                    "max_references": max_references,
                },
            )


def _normalize_video_model_backend(value: object) -> str:
    """Resolve an upstream model name to the persisted direct backend id."""

    model_id = str(value or "").strip()
    if not model_id or model_id.casefold().startswith("direct_"):
        return model_id
    try:
        from novelvideo.generators.video.direct_models import list_direct_video_models

        normalized = model_id.casefold()
        matches = [
            model
            for model in list_direct_video_models()
            if normalized
            in {
                model.registry_id.casefold(),
                model.label.casefold(),
                model.upstream_model.casefold(),
            }
        ]
        if len(matches) > 1:
            matches = [
                model for model in matches if model.enabled and model.is_default
            ] or matches
        if matches:
            return matches[0].backend
    except Exception:
        pass
    return model_id


def _validate_video_prompt_command(
    command: dict[str, Any],
    *,
    op_index: int,
) -> None:
    """Reject explicit direct-model video mismatches before draft persistence.

    A prompt node is still a draft and never submits media. The same declared
    capability contract must nevertheless guard its explicit parameters so a
    later WorkflowRun cannot inherit an impossible node silently.
    """

    backend = _normalize_video_model_backend(command.get("model"))
    if not backend:
        return
    references = command.get("reference_items")
    if not isinstance(references, (list, tuple)):
        references = command.get("references")
    if not isinstance(references, (list, tuple)):
        references = ()
    issues = validate_structured_video_capability(
        backend=backend,
        mode=(
            str(command.get("generation_mode") or "").strip()
            if "generation_mode" in command
            else None
        ),
        duration_seconds=(
            command.get("duration_sec") if "duration_sec" in command else None
        ),
        resolution=(
            str(command.get("resolution") or command.get("video_quality") or "")
            .strip()
            .lower()
            if "resolution" in command or "video_quality" in command
            else None
        ),
        aspect_ratio=(
            str(command.get("aspect_ratio") or "").strip()
            if "aspect_ratio" in command
            else None
        ),
        generate_audio=(
            command.get("generate_audio")
            if isinstance(command.get("generate_audio"), bool)
            else None
        ),
        reference_items=references,
        last_frame_path=(
            str(command.get("last_frame_path") or "").strip()
            if "last_frame_path" in command
            else None
        ),
    )
    if not issues:
        return
    raise CanvasCommandError(
        "视频草稿参数不符合当前模型能力合同",
        code="canvas_video_capability_contract_invalid",
        op_index=op_index,
        details={
            "model": backend,
            "issues": [issue.as_dict() for issue in issues],
            "media_submission_started": False,
        },
    )


def _compile_video_prompt_if_contractual(
    command: dict[str, Any],
    *,
    op_index: int,
) -> tuple[str, str]:
    """Compile a raw Canvas shot only when it carries a continuity contract."""

    prompt = _required_text(command, "prompt", op_index=op_index)
    source = str(
        command.get("prompt_source") or command.get("promptSource") or ""
    ).strip()
    has_contract = any(
        isinstance(command.get(key), dict) and command.get(key)
        for key in ("continuity_in", "continuity_out", "continuityIn", "continuityOut")
    )
    if source or not has_contract or prompt.startswith("[导演镜头合同]"):
        return prompt, source
    compiled = compile_shot_prompt(
        {**command, "prompt": prompt},
        shot_index=int(command.get("shot_index") or 1),
        continuity_in=(
            command.get("continuity_in") or command.get("continuityIn") or {}
        ),
        continuity_out=(
            command.get("continuity_out") or command.get("continuityOut") or {}
        ),
        anchors=command.get("continuity_anchors")
        if isinstance(command.get("continuity_anchors"), dict)
        else None,
        model_ref=str(command.get("model") or ""),
    )
    return compiled, prompt


def _build_node(
    *,
    node_id: str,
    node_type: str,
    x: float,
    y: float,
    data: dict[str, Any],
) -> dict[str, Any]:
    node_data = {**_default_node_data(node_type), **data}
    try:
        stamp_production_metadata(
            node_data,
            node_type=node_type,
            actor="agent",
            turn_id=str(node_data.get("agent_command_id") or ""),
        )
    except ProductionMetadataError as exc:
        raise ValueError(f"invalid production metadata for {node_type}: {exc}") from exc
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": float(x), "y": float(y)},
        "data": node_data,
    }


def _stamp_node_metadata(
    data: dict[str, Any],
    *,
    node_type: str,
    command_id: str,
    op_index: int,
) -> None:
    """Turn an explicit metadata patch into the canonical node contract."""

    try:
        stamp_production_metadata(
            data,
            node_type=node_type,
            actor="agent",
            turn_id=command_id,
        )
    except ProductionMetadataError as exc:
        raise CanvasCommandError(
            f"画布节点生产元数据无效：{exc}",
            code="canvas_production_metadata_invalid",
            op_index=op_index,
            details={"node_type": node_type},
        ) from exc


_STARTER_WORKFLOW_ASSETS = (
    # 人工设计的骨架（17 条）。导入管线不许改它一个字节。
    "canvas_starter_workflows.json",
    # 社区共识配方，由 scripts/maintenance/import_community_assets.py 从社区画布
    # 快照推导生成。前端 starterWorkflows.ts import 的是同一个文件，所以后端
    # Agent 的 insert_starter_workflow 与前端起步器看到的是同一份 id。
    "community_starter_workflows.json",
)


def _starter_workflows() -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for name in _STARTER_WORKFLOW_ASSETS:
        try:
            text = (
                resources.files("novelvideo.assets")
                .joinpath(name)
                .read_text(encoding="utf-8")
            )
            values = json.loads(text)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CanvasCommandError(
                f"画布起步工作流目录损坏：{name}",
                code="canvas_starter_workflow_catalog_invalid",
            ) from exc
        try:
            catalog.update(validate_starter_workflow_catalog(values))
        except StarterWorkflowContractError as exc:
            raise CanvasCommandError(
                f"画布起步工作流合同无效：{name}：{exc}",
                code="canvas_starter_workflow_contract_invalid",
            ) from exc
    return catalog


def _command_receipt_ids(snapshot: dict[str, Any]) -> list[str]:
    metadata = snapshot.get("metadata")
    values = (
        metadata.get(_CANVAS_COMMAND_RECEIPTS_KEY) if isinstance(metadata, dict) else []
    )
    if isinstance(values, str):
        return [item for item in re.split(r"[\s,]+", values.strip()) if item]
    if not isinstance(values, list):
        return []
    return [str(item) for item in values if str(item or "").strip()]


def _command_receipt_details(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    metadata = snapshot.get("metadata")
    raw = (
        metadata.get(_CANVAS_COMMAND_RECEIPT_DETAILS_KEY)
        if isinstance(metadata, dict)
        else {}
    )
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): dict(value)
        for key, value in raw.items()
        if str(key).strip() and isinstance(value, dict)
    }


def _current_revision(snapshot: dict[str, Any] | None) -> int:
    value = snapshot.get("revision") if isinstance(snapshot, dict) else None
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _base_position(nodes: list[dict[str, Any]]) -> tuple[float, float]:
    if not nodes:
        return 160.0, 160.0
    position = nodes[-1].get("position")
    if not isinstance(position, dict):
        return 160.0, 160.0
    try:
        return float(position.get("x") or 0) + 420.0, float(position.get("y") or 0)
    except (TypeError, ValueError):
        return 160.0, 160.0


def _layout_node_type(command: dict[str, Any]) -> str:
    command_type = str(command.get("type") or "").strip()
    if command_type == "create_image_prompt_node":
        return "imageGenNode"
    if command_type == "create_video_prompt_node":
        return "videoNode"
    if command_type == "annotate":
        return "textAnnotationNode"
    if command_type == "create_canvas_node":
        return str(command.get("node_type") or "").strip()
    return ""


def _layout_size(node_type: str, value: dict[str, Any]) -> tuple[float, float]:
    fallback = _AGENT_NODE_LAYOUT_SIZES.get(node_type, (320.0, 200.0))
    width = value.get("width")
    height = value.get("height")
    return (
        float(width) if _is_number(width) and float(width) > 0 else fallback[0],
        float(height) if _is_number(height) and float(height) > 0 else fallback[1],
    )


def _rectangles_overlap(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    margin = _AGENT_NODE_LAYOUT_MARGIN
    return not (
        lx + lw + margin <= rx
        or rx + rw + margin <= lx
        or ly + lh + margin <= ry
        or ry + rh + margin <= ly
    )


def _spread_large_create_batch(
    commands: list[dict[str, Any]],
    snapshot: dict[str, Any],
) -> None:
    create_commands = [command for command in commands if _layout_node_type(command)]
    if not create_commands:
        return
    placed: list[tuple[float, float, float, float]] = []
    for node in snapshot.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        position = node.get("position")
        if not isinstance(position, dict):
            continue
        node_type = str(node.get("type") or "").strip()
        width, height = _layout_size(node_type, node)
        placed.append(
            (
                float(position.get("x") or 0),
                float(position.get("y") or 0),
                width,
                height,
            )
        )
    for command in create_commands:
        node_type = _layout_node_type(command)
        width, height = _layout_size(node_type, command)
        x = float(command.get("x") or 0)
        y = float(command.get("y") or 0)
        for _ in range(64):
            overlaps = [
                rect
                for rect in placed
                if _rectangles_overlap((x, y, width, height), rect)
            ]
            if not overlaps:
                break
            right_x = max(
                rect[0] + rect[2] + _AGENT_NODE_LAYOUT_MARGIN for rect in overlaps
            )
            down_y = max(
                rect[1] + rect[3] + _AGENT_NODE_LAYOUT_MARGIN for rect in overlaps
            )
            if right_x - x <= down_y - y:
                x = right_x
            else:
                y = down_y
        command["x"] = x
        command["y"] = y
        placed.append((x, y, width, height))


def _required_text(
    command: dict[str, Any],
    field: str,
    *,
    op_index: int,
    code: str = "canvas_command_invalid",
) -> str:
    value = str(command.get(field) or "").strip()
    if not value or len(value) > 50000:  # 与工具层一致；网关是共享收口，须自设限
        raise CanvasCommandError(
            f"画布命令缺少 {field} 或超过 50000 字符上限",
            code=code,
            op_index=op_index,
            details={"field": field},
        )
    return value


def _concrete_node_id(command: dict[str, Any], field: str, *, op_index: int) -> str:
    value = _required_text(command, field, op_index=op_index)
    if _is_alias(value):
        raise CanvasCommandError(
            "服务端画布命令必须使用具体节点 ID",
            code="canvas_command_alias_requires_ui",
            op_index=op_index,
            details={"field": field, "value": value},
        )
    return value


def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return sorted(
        key for key in set(before) | set(after) if before.get(key) != after.get(key)
    )


class CanvasCommandGateway:
    """Apply one allowlisted command batch to the authoritative canvas snapshot."""

    def __init__(
        self,
        *,
        project_dir: str | Path,
        project_id: str,
        actor_id: str = "xiaoshu-runtime",
    ):
        self.project_dir = Path(project_dir)
        self.project_id = str(project_id).strip()
        self.actor_id = str(actor_id).strip() or "xiaoshu-runtime"

    def _gate_answers(self, raw_profile: Mapping[str, Any]) -> dict[str, Any]:
        """Answers the creative gate may rely on, including the project contract.

        ``project_dir`` is the registry state dir on every gateway call site, so
        the project's locked style, aspect ratio and stored operator answers all
        satisfy the gate here instead of being re-asked.
        """

        from novelvideo.creative_execution.creative_contract import (
            merge_project_contract_answers,
        )

        supplied = raw_profile.get("director_clarification_answers")
        return merge_project_contract_answers(
            supplied if isinstance(supplied, dict) else None,
            state_dir=self.project_dir,
        )

    @staticmethod
    def _context_identity(value: object) -> tuple[str, str, str] | None:
        if not isinstance(value, dict):
            return None
        execution_id = str(value.get("execution_id") or "").strip()
        digest = str(value.get("digest") or value.get("context_digest") or "").strip()
        idempotency_key = str(value.get("idempotency_key") or "").strip()
        if not execution_id and not digest and not idempotency_key:
            return None
        return execution_id, digest, idempotency_key

    @classmethod
    def _receipt_context_identity(
        cls,
        value: object,
    ) -> tuple[str, str, str] | None:
        """Read context identity from current and legacy receipt layouts."""

        direct = cls._context_identity(value)
        if direct is not None or not isinstance(value, dict):
            return direct
        nested = value.get("execution_context")
        direct = cls._context_identity(nested)
        if direct is not None:
            return direct
        normalized = value.get("normalized_envelope")
        if isinstance(normalized, dict):
            return cls._context_identity(normalized.get("execution_context"))
        return None

    def _validate_receipt_context_replay(
        self,
        previous: dict[str, Any],
        envelope: dict[str, Any],
    ) -> None:
        current = self._context_identity(envelope.get("execution_context"))
        if current is None:
            return
        prior = self._receipt_context_identity(previous)
        if prior is not None and prior != current:
            raise CanvasCommandError(
                "同一 command_id 被用于不同 execution_context",
                code="canvas_command_execution_context_conflict",
                current_revision=None,
                details={"previous_identity": prior, "current_identity": current},
            )

    @staticmethod
    def _validate_context_revision(
        context: object,
        *,
        current_revision: int,
    ) -> None:
        """Reject a fresh write built from an older canvas snapshot."""

        if not isinstance(context, dict):
            return
        observed = context.get("observed_canvas_revision")
        if not isinstance(observed, int) or isinstance(observed, bool):
            return
        if observed == current_revision:
            return
        raise CanvasCommandError(
            "execution_context 绑定的画布 revision 已过期",
            code="canvas_command_execution_context_stale",
            current_revision=current_revision,
            details={
                "observed_canvas_revision": observed,
                "current_revision": current_revision,
            },
        )

    def apply(
        self,
        *,
        canvas_id: str,
        envelope: dict[str, Any],
        expected_canvas_revision: int | None = None,
    ) -> dict[str, Any]:
        return self._apply(
            canvas_id=canvas_id,
            envelope=envelope,
            expected_canvas_revision=expected_canvas_revision,
            conflict_retried=False,
        )

    def _apply(
        self,
        *,
        canvas_id: str,
        envelope: dict[str, Any],
        expected_canvas_revision: int | None,
        conflict_retried: bool,
    ) -> dict[str, Any]:
        snapshot = canvas_store.read_canvas(self.project_dir, canvas_id)
        if snapshot is None:
            snapshot = canvas_store.default_canvas_payload(
                project_id=self.project_id,
                actor_id=self.actor_id,
            )
            snapshot["canvas_id"] = canvas_id
            snapshot["revision"] = 0
        revision = _current_revision(snapshot)
        if (
            expected_canvas_revision is not None
            and revision != expected_canvas_revision
        ):
            raise CanvasCommandError(
                "画布版本已变化，请基于最新版本重试",
                code="canvas_revision_conflict",
                current_revision=revision,
                details={"expected_revision": expected_canvas_revision},
            )

        raw_command_hash = canvas_store.canvas_request_hash(
            {
                "schema": str(envelope.get("schema") or ""),
                "project_id": str(envelope.get("project_id") or self.project_id),
                "canvas_id": _resolved_canvas_id(envelope.get("canvas_id"), canvas_id),
                "command_id": str(envelope.get("command_id") or ""),
                "commands": deepcopy(envelope.get("commands") or []),
                "execution_context": deepcopy(envelope.get("execution_context") or {}),
            }
        )
        normalized = self._normalize_envelope(
            envelope,
            snapshot=snapshot,
            canvas_id=canvas_id,
        )
        command_id = str(normalized["command_id"])
        command_hash = raw_command_hash
        action_route = route_canvas_envelope(
            normalized,
            existing_node_ids=[
                node.get("id")
                for node in (snapshot.get("nodes") or [])
                if isinstance(node, dict)
            ],
        ).to_dict()
        if action_route.get("lane") == "blocked":
            raise CanvasCommandError(
                str(action_route.get("reason") or "当前交互没有画布写入权限"),
                code=str(
                    action_route.get("reason_code")
                    or "canvas_action_execution_not_authorized"
                ),
                current_revision=revision,
                details={"action_route": action_route},
            )
        self._validate_existing_mutation_contract(
            normalized,
            snapshot=snapshot,
            expected_canvas_revision=expected_canvas_revision,
        )
        director_ledger: dict[str, Any] | None = None
        raw_profile = normalized.get("action_profile")
        if isinstance(raw_profile, dict):
            raw_ledger = raw_profile.get("director_ledger")
            if raw_ledger is not None:
                try:
                    director_ledger = validate_director_ledger(raw_ledger)
                except ValueError as exc:
                    raise CanvasCommandError(
                        str(exc),
                        code="director_ledger_invalid",
                        current_revision=revision,
                    ) from exc
            else:
                director_ledger = build_director_ledger(
                    goal=raw_profile.get("goal")
                    or raw_profile.get("operation")
                    or "canvas command",
                    success_criteria=raw_profile.get("success_criteria")
                    or ["画布命令通过权威回执验收"],
                    action_profile=raw_profile,
                    action_route=action_route,
                    assumptions=raw_profile.get("assumptions"),
                    constraints=raw_profile.get("constraints"),
                    unknowns=raw_profile.get("unknowns"),
                    project_id=self.project_id,
                    canvas_id=canvas_id,
                    source_turn_id=normalized.get("turn_id"),
                    canvas_revision=revision,
                    existing_node_ids=[
                        node.get("id")
                        for node in (snapshot.get("nodes") or [])
                        if isinstance(node, dict)
                    ],
                    selected_node_ids=raw_profile.get("target_node_ids"),
                    pinned_node_ids=raw_profile.get("target_node_ids"),
                )
        if (
            isinstance(normalized.get("action_profile"), dict)
            and action_route.get("lane") != "canvas"
        ):
            raise CanvasCommandError(
                "任务画像需要持久工作流，不应通过直接画布通道执行",
                code="canvas_action_requires_workflow",
                current_revision=revision,
                details={"action_route": action_route},
            )
        if isinstance(raw_profile, dict):
            gate_task = dict(raw_profile)
            # Only a durable runtime envelope can prove that an existing run
            # is actually being resumed. A public client string is not enough
            # to bypass the creative-start gate.
            envelope_run_id = str(normalized.get("run_id") or "").strip()
            if (
                not envelope_run_id
                or envelope_run_id
                != str(gate_task.get("existing_run_id") or "").strip()
            ):
                gate_task["existing_run_id"] = ""
            try:
                require_director_clarification_ready(
                    request=str(
                        raw_profile.get("goal")
                        or raw_profile.get("operation")
                        or "canvas command"
                    ),
                    goal=str(raw_profile.get("goal") or ""),
                    run_mode=(
                        "auto"
                        if raw_profile.get("contains_paid_media") is True
                        else "draft"
                    ),
                    director_intent_contract=(
                        raw_profile.get("director_intent_contract")
                        if isinstance(raw_profile.get("director_intent_contract"), dict)
                        else None
                    ),
                    canvas_nodes=[
                        node
                        for node in (snapshot.get("nodes") or [])
                        if isinstance(node, dict)
                    ],
                    answers=(self._gate_answers(raw_profile)),
                    commands=(
                        normalized.get("commands")
                        if isinstance(normalized.get("commands"), list)
                        else None
                    ),
                    task=gate_task,
                )
            except DirectorClarificationRequiredError as exc:
                raise CanvasCommandError(
                    str(exc),
                    code=exc.code,
                    current_revision=revision,
                    details=exc.to_detail(),
                ) from exc
        causal_binding = binding_from_envelope(
            normalized,
            default_project_id=self.project_id,
            default_canvas_id=canvas_id,
        ).with_updates(input_revision=revision, canvas_revision=revision)
        receipt_ids = _command_receipt_ids(snapshot)
        details = _command_receipt_details(snapshot)
        if command_id in receipt_ids:
            previous = dict(details.get(command_id) or {})
            # Keep context identity diagnostics ahead of the generic payload
            # hash conflict. A changed context is a replay-boundary violation,
            # even when the command body itself is otherwise unchanged.
            self._validate_receipt_context_replay(previous, normalized)
            previous_hash = str(previous.get("command_hash") or "")
            if previous_hash and previous_hash != command_hash:
                raise CanvasCommandError(
                    "同一 command_id 被用于不同画布命令",
                    code="canvas_command_idempotency_conflict",
                    current_revision=revision,
                )
            return {
                **previous,
                "schema": "canvas_command_receipt.v2",
                "success": True,
                "server_applied": True,
                "command_id": command_id,
                "project_id": self.project_id,
                "canvas_id": canvas_id,
                "revision": revision,
                "canvas_revision": revision,
                "node_count": len(snapshot.get("nodes") or []),
                "edge_count": len(snapshot.get("edges") or []),
                "normalized_envelope": normalized,
                "action_route": previous.get("action_route") or action_route,
                "causal_binding": previous.get("causal_binding")
                or causal_binding.to_dict(),
                "idempotent_replay": True,
                "conflict_retried": conflict_retried,
            }

        # A retried transport may mint a different command id while carrying
        # the same planner-owned idempotency key.  Return the authoritative
        # prior receipt instead of applying the mutation a second time.
        context = normalized.get("execution_context")
        if isinstance(context, dict):
            identity = self._context_identity(context)
            if identity:
                for previous in details.values():
                    if not isinstance(previous, dict):
                        continue
                    if self._receipt_context_identity(previous) != identity:
                        continue
                    return {
                        **previous,
                        "schema": "canvas_command_receipt.v2",
                        "success": True,
                        "server_applied": True,
                        "project_id": self.project_id,
                        "canvas_id": canvas_id,
                        "revision": revision,
                        "canvas_revision": revision,
                        "idempotent_replay": True,
                        "context_replay": True,
                        "conflict_retried": conflict_retried,
                    }

        self._validate_context_revision(
            context,
            current_revision=revision,
        )
        mutation = self._simulate(snapshot=snapshot, envelope=normalized)
        next_revision = revision + 1
        camera_updates, camera_verified = _verify_camera_updates(
            {"nodes": mutation.nodes},
            mutation.camera_updates,
        )
        impact_plan = plan_canvas_impact(
            {"nodes": mutation.nodes, "edges": mutation.edges},
            mutation.affected_node_ids,
            previous_canvas=snapshot,
        ).to_dict()
        receipt = {
            "schema": "canvas_command_receipt.v2",
            "success": True,
            "server_applied": True,
            "command_id": command_id,
            "command_hash": command_hash,
            "project_id": self.project_id,
            "canvas_id": canvas_id,
            "revision": next_revision,
            "canvas_revision": next_revision,
            "created_node_ids": mutation.created_node_ids,
            "affected_node_ids": mutation.affected_node_ids,
            "camera_applied": any(
                update.get("camera_applied") is True for update in camera_updates
            ),
            "camera_verified_from_snapshot": camera_verified,
            "camera_updates": camera_updates,
            "applied_ops": mutation.structural_ops,
            "presentation_ops": mutation.presentation_ops,
            "op_results": mutation.op_results,
            "expectation": mutation.expectation,
            "normalized_envelope": mutation.normalized_envelope,
            "action_route": action_route,
            "causal_binding": causal_binding.with_updates(
                canvas_revision=next_revision
            ).to_dict(),
            "impact_plan": impact_plan,
            "node_count": len(mutation.nodes),
            "edge_count": len(mutation.edges),
            "idempotent_replay": False,
            "conflict_retried": conflict_retried,
        }
        if isinstance(context, dict):
            receipt.update(
                {
                    "execution_id": context.get("execution_id"),
                    "context_digest": context.get("digest"),
                    "idempotency_key": context.get("idempotency_key"),
                    "plan_revision": context.get("plan_revision"),
                    "capability_id": context.get("capability_id"),
                    "selected_handler": context.get("selected_handler"),
                    "observed_canvas_revision": context.get("observed_canvas_revision"),
                    "expected_postconditions": deepcopy(
                        context.get("expected_postconditions") or []
                    ),
                }
            )
        if director_ledger is not None:
            receipt["director_ledger"] = director_ledger
        from novelvideo.workflow_runtime.verifier import verify_canvas_command

        metadata = _as_dict(snapshot.get("metadata"))
        receipt_ids.append(command_id)
        metadata[_CANVAS_COMMAND_RECEIPTS_KEY] = receipt_ids[-_RECEIPT_RETENTION:]
        # Verify the exact payload that is about to be persisted and retain the
        # result inside the receipt details. The post-save check below then
        # confirms that the authoritative store returned the same state.
        pre_save_snapshot = {
            **snapshot,
            "revision": next_revision,
            "nodes": mutation.nodes,
            "edges": mutation.edges,
            "metadata": metadata,
        }
        readback_verification = verify_canvas_command(
            snapshot=pre_save_snapshot,
            envelope=receipt["normalized_envelope"],
            expectation=receipt["expectation"],
        )
        receipt["readback_verification"] = readback_verification
        receipt["readback_verified"] = bool(readback_verification.get("passed"))
        detail_values = _command_receipt_details(snapshot)
        detail_values[command_id] = {
            key: deepcopy(value)
            for key, value in receipt.items()
            if key not in {"normalized_envelope"}
        }
        metadata[_CANVAS_COMMAND_RECEIPT_DETAILS_KEY] = dict(
            list(detail_values.items())[-_RECEIPT_RETENTION:]
        )

        def build_payload(existing: dict | None) -> dict:
            current = dict(existing or {})
            current_revision = _current_revision(existing)
            now = canvas_store.utc_now_iso()
            current.update(
                {
                    "schema_version": 2,
                    "canvas_id": canvas_id,
                    "project_id": self.project_id,
                    "canvas_scope": str(current.get("canvas_scope") or "default"),
                    "nodes": mutation.nodes,
                    "edges": mutation.edges,
                    "viewport": current.get("viewport"),
                    "metadata": metadata,
                    "owner_principal_type": str(
                        current.get("owner_principal_type") or "user"
                    ),
                    "owner_principal_id": str(
                        current.get("owner_principal_id") or self.actor_id
                    ),
                    "access_model": str(current.get("access_model") or "project_role"),
                    "min_project_role": str(
                        current.get("min_project_role") or "editor"
                    ),
                    "created_by": str(current.get("created_by") or self.actor_id),
                    "created_at": str(current.get("created_at") or now),
                    "updated_by": self.actor_id,
                    "updated_at": now,
                    "save_source": "agent_gateway",
                    "revision": current_revision + 1,
                }
            )
            return current

        try:
            saved = canvas_store.save_canvas(
                self.project_dir,
                canvas_id,
                base_revision=revision if revision > 0 else None,
                build_payload=build_payload,
                client_save_id=f"canvas-command:{command_id}",
                request_hash=command_hash,
                save_source=(
                    "projection_remove"
                    if bool(snapshot.get("nodes")) and not mutation.nodes
                    else "agent_gateway"
                ),
                allow_empty_overwrite=bool(snapshot.get("nodes"))
                and not mutation.nodes,
            )
        except canvas_store.CanvasRevisionConflict as exc:
            if conflict_retried or expected_canvas_revision is not None:
                raise CanvasCommandError(
                    "画布版本连续冲突，命令已保留等待重试",
                    code="canvas_revision_conflict",
                    current_revision=exc.current_revision,
                    details={"expected_revision": exc.base_revision},
                ) from exc
            return self._apply(
                canvas_id=canvas_id,
                envelope=normalized,
                expected_canvas_revision=None,
                conflict_retried=True,
            )
        except canvas_store.CanvasIdempotencyConflict as exc:
            raise CanvasCommandError(
                "同一 command_id 被用于不同画布命令",
                code="canvas_command_idempotency_conflict",
                current_revision=revision,
            ) from exc
        except canvas_store.CanvasStoreError as exc:
            raise CanvasCommandError(
                str(exc),
                code="canvas_write_failed",
                current_revision=revision,
            ) from exc

        persisted_revision = _current_revision(saved.payload)
        if persisted_revision <= 0:
            reread = canvas_store.read_canvas(self.project_dir, canvas_id) or {}
            persisted_revision = _current_revision(reread)
        receipt["revision"] = persisted_revision
        receipt["canvas_revision"] = persisted_revision
        receipt["idempotent_replay"] = bool(saved.idempotent)
        persisted_snapshot = saved.payload
        if not isinstance(persisted_snapshot, dict):
            persisted_snapshot = (
                canvas_store.read_canvas(self.project_dir, canvas_id) or {}
            )
        verified_updates, camera_verified = _verify_camera_updates(
            persisted_snapshot,
            mutation.camera_updates,
        )
        receipt["camera_updates"] = verified_updates
        receipt["camera_verified_from_snapshot"] = camera_verified
        if receipt["camera_applied"] and not camera_verified:
            raise CanvasCommandError(
                "摄像机参数已写入但权威画布回读校验失败",
                code="canvas_camera_verification_failed",
                current_revision=persisted_revision,
                details={"camera_updates": verified_updates},
            )
        persisted_readback_verification = verify_canvas_command(
            snapshot=persisted_snapshot,
            envelope=receipt["normalized_envelope"],
            expectation=receipt["expectation"],
        )
        receipt["readback_verification"] = persisted_readback_verification
        receipt["readback_verified"] = bool(
            persisted_readback_verification.get("passed")
        )
        return receipt

    def _validate_existing_mutation_contract(
        self,
        envelope: dict[str, Any],
        *,
        snapshot: dict[str, Any],
        expected_canvas_revision: int | None,
    ) -> None:
        """Require a versioned, explicitly bound contract for existing-node writes."""

        profile = envelope.get("action_profile")
        if not isinstance(profile, dict):
            return
        if str(profile.get("target_strategy") or "").strip() != "reuse_existing":
            return
        commands = envelope.get("commands")
        if not existing_node_mutation_batch(commands):
            return
        if expected_canvas_revision is None:
            raise CanvasCommandError(
                "复用已有节点的修改必须绑定当前画布 revision",
                code="canvas_existing_mutation_revision_required",
                current_revision=_current_revision(snapshot),
            )
        bound_targets = {
            str(item).strip()
            for item in (profile.get("target_node_ids") or [])
            if str(item or "").strip()
        }
        referenced_targets: set[str] = set()
        for command in commands if isinstance(commands, list) else []:
            if not isinstance(command, dict):
                continue
            command_type = str(command.get("type") or "").strip()
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
            referenced_targets.update(
                str(command.get(field) or "").strip()
                for field in fields
                if str(command.get(field) or "").strip()
                and not str(command.get(field) or "").strip().startswith("$")
            )
        missing_bindings = sorted(referenced_targets - bound_targets)
        if missing_bindings:
            raise CanvasCommandError(
                "复用已有节点的修改必须绑定命令涉及的全部目标节点",
                code="canvas_existing_target_binding_missing",
                current_revision=_current_revision(snapshot),
                details={"missing_target_node_ids": missing_bindings},
            )

    def _normalize_envelope(
        self,
        envelope: dict[str, Any],
        *,
        snapshot: dict[str, Any],
        canvas_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(envelope, dict):
            raise CanvasCommandError(
                "画布命令信封不是对象",
                code="canvas_command_invalid_envelope",
            )
        schema = str(envelope.get("schema") or "").strip()
        if schema != "canvas_chat_commands.v1":
            raise CanvasCommandError(
                "画布命令 schema 不受支持",
                code="canvas_command_schema_unsupported",
                details={"schema": schema},
            )
        command_id = str(envelope.get("command_id") or "").strip()
        if not command_id or len(command_id) > 512:
            raise CanvasCommandError(
                "画布命令缺少有效 command_id",
                code="canvas_command_invalid_envelope",
            )
        resolved_canvas_id = _resolved_canvas_id(
            envelope.get("canvas_id"),
            canvas_id or snapshot.get("canvas_id"),
        )
        execution_context = envelope.get("execution_context")
        if execution_context is not None:
            if not isinstance(execution_context, dict):
                raise CanvasCommandError(
                    "execution_context 必须是对象",
                    code="canvas_command_execution_context_invalid",
                )
            context_reasons = validate_execution_context(
                execution_context,
                require_write_fields=True,
            )
            if context_reasons:
                raise CanvasCommandError(
                    "execution_context 无效：" + ",".join(context_reasons),
                    code="canvas_command_execution_context_invalid",
                    details={"blocking_reasons": context_reasons},
                )
            if (
                str(execution_context.get("project_id") or "").strip()
                != self.project_id
            ):
                raise CanvasCommandError(
                    "execution_context 与项目不匹配",
                    code="canvas_command_execution_context_scope_mismatch",
                )
            envelope_canvas_id = _resolved_canvas_id(
                envelope.get("canvas_id"), resolved_canvas_id
            )
            if (
                str(execution_context.get("canvas_id") or "").strip()
                != envelope_canvas_id
            ):
                raise CanvasCommandError(
                    "execution_context 与画布不匹配",
                    code="canvas_command_execution_context_scope_mismatch",
                )
        raw_commands = envelope.get("commands")
        if not isinstance(raw_commands, list) or not raw_commands:
            raise CanvasCommandError(
                "画布命令批次为空",
                code="canvas_command_empty_batch",
            )
        if len(raw_commands) > 100:
            raise CanvasCommandError(
                "单批画布命令最多 100 项",
                code="canvas_command_batch_too_large",
            )
        commands = [
            dict(command) if isinstance(command, dict) else command
            for command in raw_commands
        ]
        sequence = 0
        created_ids: list[str] = []
        base_x, base_y = _base_position(
            [node for node in (snapshot.get("nodes") or []) if isinstance(node, dict)]
        )
        catalog = _starter_workflows()
        for index, command in enumerate(commands):
            if not isinstance(command, dict):
                raise CanvasCommandError(
                    "画布命令项不是对象",
                    code="canvas_command_invalid",
                    op_index=index,
                )
            command_type = str(command.get("type") or "").strip()
            if command_type in _PRESENTATION_COMMANDS:
                continue
            if command_type in {
                "create_canvas_node",
                "create_image_prompt_node",
                "create_video_prompt_node",
                "annotate",
                "duplicate_node",
            }:
                sequence += 1
                command.setdefault(
                    "created_node_id", mint_agent_node_id(command_id, sequence)
                )
                created_ids.append(str(command["created_node_id"]))
            elif command_type == "create_shot_sequence":
                prompts = [
                    str(value).strip()
                    for value in (command.get("prompts") or [])
                    if str(value or "").strip()
                ]
                ids: list[str] = []
                for _prompt in prompts:
                    sequence += 1
                    ids.append(mint_agent_node_id(command_id, sequence))
                command["prompts"] = prompts
                command["created_node_ids"] = ids
                if ids:
                    command["created_node_id"] = ids[-1]
                created_ids.extend(ids)
            elif command_type == "insert_starter_workflow":
                workflow_id = str(command.get("workflow_id") or "").strip()
                definition = catalog.get(workflow_id)
                if definition is None:
                    raise CanvasCommandError(
                        f"未知画布起步工作流：{workflow_id or '<empty>'}",
                        code="canvas_starter_workflow_not_found",
                        op_index=index,
                    )
                ids = []
                for _spec in definition.get("nodes") or []:
                    sequence += 1
                    ids.append(mint_agent_node_id(command_id, sequence))
                command["created_node_ids"] = ids
                created_ids.extend(ids)
            if command_type in {"connect_nodes", "remove_edge"}:
                command["source"] = _resolve_batch_node_ref(
                    command.get("source"), created_ids
                )
                command["target"] = _resolve_batch_node_ref(
                    command.get("target"), created_ids
                )
            if command_type in {"update_node_data", "update_node_camera"}:
                node_id = _resolve_batch_node_ref(command.get("node_id"), created_ids)
                command["node_id"] = node_id
                if not node_id:
                    raise CanvasCommandError(
                        f"{command_type} 缺少 node_id",
                        code="canvas_command_invalid",
                        op_index=index,
                    )
                if not _is_alias(node_id):
                    node = next(
                        (
                            value
                            for value in (snapshot.get("nodes") or [])
                            if isinstance(value, dict)
                            and str(value.get("id") or "").strip() == node_id
                        ),
                        None,
                    )
                    if node is None and node_id in created_ids:
                        inferred_type = ""
                        for prior in commands[:index]:
                            prior_ids = [
                                str(value)
                                for value in (prior.get("created_node_ids") or [])
                            ]
                            if str(prior.get("created_node_id") or "") == node_id:
                                prior_ids.append(node_id)
                            if node_id not in prior_ids:
                                continue
                            prior_type = str(prior.get("type") or "")
                            inferred_type = {
                                "create_image_prompt_node": "imageGenNode",
                                "create_video_prompt_node": "videoNode",
                                "create_shot_sequence": "imageGenNode",
                            }.get(
                                prior_type,
                                str(prior.get("node_type") or ""),
                            )
                            if prior_type == "insert_starter_workflow":
                                prior_definition = (
                                    catalog.get(str(prior.get("workflow_id") or ""))
                                    or {}
                                )
                                for prior_spec, prior_id in zip(
                                    prior_definition.get("nodes") or [],
                                    prior_ids,
                                    strict=False,
                                ):
                                    if str(prior_id) == node_id and isinstance(
                                        prior_spec, dict
                                    ):
                                        inferred_type = str(
                                            prior_spec.get("type") or ""
                                        )
                                        break
                            break
                        if not inferred_type:
                            raise CanvasCommandError(
                                f"批内节点类型无法解析：{node_id}",
                                code="canvas_command_batch_ref_missing",
                                op_index=index,
                                details={"node_id": node_id},
                            )
                        node = {"id": node_id, "type": inferred_type, "data": {}}
                    if node is None:
                        raise CanvasCommandError(
                            f"画布节点不存在：{node_id}",
                            code="canvas_command_target_missing",
                            op_index=index,
                            details={"node_id": node_id},
                        )
                    patch = (
                        command.get("node_data")
                        if command_type == "update_node_data"
                        else {}
                    )
                    if command_type == "update_node_data" and not isinstance(
                        patch, dict
                    ):
                        patch = {}
                    normalized_patch, camera_request = _normalize_camera_patch(
                        command=command,
                        node_type=str(node.get("type") or ""),
                        patch=patch,
                        current_data=None,
                        op_index=index,
                    )
                    if command_type == "update_node_camera":
                        if camera_request is None:
                            raise CanvasCommandError(
                                "update_node_camera 缺少摄像机参数",
                                code="canvas_camera_invalid",
                                op_index=index,
                            )
                        command["node_data"] = normalized_patch
                        command.pop("camera", None)
                        command.pop("camera_movement", None)
                        command.pop("clear_camera", None)
                    elif camera_request is not None:
                        command["node_data"] = normalized_patch
                        command.pop("camera", None)
                        command.pop("camera_movement", None)
                        command.pop("clear_camera", None)
            x = command.get("x")
            y = command.get("y")
            if command_type in {
                "create_canvas_node",
                "create_image_prompt_node",
                "create_video_prompt_node",
                "annotate",
                "create_shot_sequence",
                "insert_starter_workflow",
            }:
                command["x"] = float(x) if _is_number(x) else base_x + index * 40
                command["y"] = float(y) if _is_number(y) else base_y + index * 40
        _spread_large_create_batch(commands, snapshot)
        return {
            **deepcopy(envelope),
            "schema": "canvas_chat_commands.v1",
            "project_id": str(envelope.get("project_id") or self.project_id),
            "canvas_id": resolved_canvas_id,
            "command_id": command_id,
            "commands": commands,
        }

    def _simulate(
        self,
        *,
        snapshot: dict[str, Any],
        envelope: dict[str, Any],
    ) -> _Mutation:
        nodes = [
            deepcopy(value)
            for value in (snapshot.get("nodes") or [])
            if isinstance(value, dict)
        ]
        edges = [
            deepcopy(value)
            for value in (snapshot.get("edges") or [])
            if isinstance(value, dict)
        ]
        by_id = {
            str(node.get("id") or "").strip(): node
            for node in nodes
            if str(node.get("id") or "").strip()
        }
        command_id = str(envelope["command_id"])
        op_results: list[dict[str, Any]] = []
        expectations: list[dict[str, Any]] = []
        created_ids: list[str] = []
        affected_ids: list[str] = []
        camera_updates: list[dict[str, Any]] = []
        structural_ops = 0
        presentation_ops = 0
        catalog = _starter_workflows()

        def remember(node_id: str) -> None:
            if node_id and node_id not in affected_ids:
                affected_ids.append(node_id)

        def require_node(node_id: str, index: int) -> dict[str, Any]:
            node = by_id.get(node_id)
            if node is None:
                raise CanvasCommandError(
                    f"画布节点不存在：{node_id}",
                    code="canvas_command_target_missing",
                    op_index=index,
                    details={"node_id": node_id},
                )
            return node

        def append_edge(
            source: str,
            target: str,
            index: int,
            *,
            relation: str = "canvas_edge",
        ) -> dict[str, Any]:
            source_node = require_node(source, index)
            target_node = require_node(target, index)
            if source == target:
                raise CanvasCommandError(
                    "节点不能连接到自身",
                    code="canvas_command_invalid_edge",
                    op_index=index,
                )
            existing = next(
                (
                    edge
                    for edge in edges
                    if str(edge.get("source") or "") == source
                    and str(edge.get("target") or "") == target
                ),
                None,
            )
            if existing is not None:
                raise CanvasCommandError(
                    "目标连线已经存在",
                    code="canvas_command_noop",
                    op_index=index,
                    details={"source": source, "target": target},
                )
            normalized_relation = normalize_edge_relation(
                relation,
                strict=True,
            )
            if normalized_relation != "canvas_edge":
                adjacency: dict[str, list[str]] = {}
                for current in edges:
                    current_source = str(current.get("source") or "").strip()
                    current_target = str(current.get("target") or "").strip()
                    if not current_source or not current_target:
                        continue
                    if edge_relation(current) == "canvas_edge":
                        continue
                    adjacency.setdefault(current_source, []).append(current_target)
                frontier = [target]
                visited: set[str] = set()
                while frontier:
                    current = frontier.pop()
                    if current == source:
                        raise CanvasCommandError(
                            "语义依赖连线会形成循环",
                            code="canvas_semantic_edge_cycle",
                            op_index=index,
                            details={
                                "source": source,
                                "target": target,
                                "relation": normalized_relation,
                            },
                        )
                    if current in visited:
                        continue
                    visited.add(current)
                    frontier.extend(adjacency.get(current, ()))
            edge = {
                "id": _edge_id(source, target),
                "source": source,
                "target": target,
                "sourceHandle": "source",
                "targetHandle": "target",
                "type": "disconnectableEdge",
                "semanticSchema": SEMANTIC_EDGE_SCHEMA,
                "relation": normalized_relation,
            }
            source_revision = _edge_node_revision(source_node)
            target_revision = _edge_node_revision(target_node)
            if source_revision:
                edge["sourceRevision"] = source_revision
            if target_revision:
                edge["targetRevision"] = target_revision
            edges.append(edge)
            return edge

        def semantic_edge_expectation(edge: dict[str, Any]) -> dict[str, Any]:
            expectation = {
                "source": str(edge.get("source") or ""),
                "target": str(edge.get("target") or ""),
                "relation": str(edge.get("relation") or "canvas_edge"),
                "semanticSchema": str(
                    edge.get("semanticSchema") or SEMANTIC_EDGE_SCHEMA
                ),
            }
            for field in ("sourceRevision", "targetRevision"):
                if field in edge:
                    expectation[field] = edge[field]
            return expectation

        def append_reference_binding_edges(
            target_id: str,
            command: dict[str, Any],
            index: int,
        ) -> list[dict[str, Any]]:
            """Materialize only resolvable asset/node bindings as references."""

            raw_bindings = command.get("reference_bindings")
            if not isinstance(raw_bindings, dict):
                raw_bindings = command.get("referenceBindings")
            if not isinstance(raw_bindings, dict):
                return []

            def resolve_source(reference_id: str) -> str:
                if reference_id in by_id:
                    return reference_id
                for candidate_id, candidate in by_id.items():
                    data = candidate.get("data")
                    if not isinstance(data, dict):
                        continue
                    for key in (
                        "assetId",
                        "asset_id",
                        "identityId",
                        "identity_id",
                        "sceneId",
                        "scene_id",
                        "propId",
                        "prop_id",
                        "mediaId",
                        "media_id",
                    ):
                        if str(data.get(key) or "").strip() == reference_id:
                            return candidate_id
                return ""

            materialized: list[dict[str, Any]] = []
            seen_sources: set[str] = set()
            for values in raw_bindings.values():
                if not isinstance(values, (list, tuple, set)):
                    continue
                for raw_reference in values:
                    reference_id = str(raw_reference or "").strip()
                    source_id = resolve_source(reference_id)
                    if (
                        not source_id
                        or source_id == target_id
                        or source_id in seen_sources
                    ):
                        continue
                    seen_sources.add(source_id)
                    if any(
                        str(edge.get("source") or "") == source_id
                        and str(edge.get("target") or "") == target_id
                        for edge in edges
                    ):
                        continue
                    materialized.append(
                        append_edge(source_id, target_id, index, relation="references")
                    )
            return materialized

        for index, raw_command in enumerate(envelope["commands"]):
            command = dict(raw_command)
            command_type = str(command.get("type") or "").strip()
            if command_type in _PRESENTATION_COMMANDS:
                presentation_ops += 1
                op_results.append(
                    {
                        "index": index,
                        "type": command_type,
                        "target_ids": [str(command.get("node_id") or "")],
                        "status": "presentation_only",
                        "changed_fields": [],
                        "error_code": "",
                    }
                )
                continue

            expectation: dict[str, Any]
            target_ids: list[str] = []
            changed_fields: list[str] = []
            camera_request: dict[str, Any] | None = None
            if command_type == "create_canvas_node":
                node_type = _required_text(command, "node_type", op_index=index)
                if node_type not in _GENERIC_NODE_TYPES:
                    raise CanvasCommandError(
                        f"不支持的画布节点类型：{node_type}",
                        code="canvas_command_node_type_unsupported",
                        op_index=index,
                    )
                node_id = _required_text(command, "created_node_id", op_index=index)
                if node_id in by_id:
                    raise CanvasCommandError(
                        f"画布节点 ID 已存在：{node_id}",
                        code="canvas_command_node_id_conflict",
                        op_index=index,
                    )
                data = {
                    "displayName": str(command.get("display_name") or "小树节点")[:200],
                    "agent_command_id": command_id,
                }
                for source, target in (
                    ("prompt", "prompt"),
                    ("text", "text"),
                    ("model", "model"),
                ):
                    value = str(command.get(source) or "").strip()
                    if value:
                        data[target] = value
                if data.get("prompt"):
                    data["compiledPromptPreview"] = data["prompt"]
                if node_type == "videoNode":
                    _validate_video_prompt_command(command, op_index=index)
                data.update(_node_parameters(command, node_type, op_index=index))
                node = _build_node(
                    node_id=node_id,
                    node_type=node_type,
                    x=float(command["x"]),
                    y=float(command["y"]),
                    data=data,
                )
                nodes.append(node)
                by_id[node_id] = node
                reference_edges = append_reference_binding_edges(
                    node_id, command, index
                )
                created_ids.append(node_id)
                target_ids = [node_id]
                changed_fields = ["node", "edges"] if reference_edges else ["node"]
                expectation = {
                    "kind": "node_present",
                    "node_id": node_id,
                    "node_type": node_type,
                    "position": deepcopy(node["position"]),
                    "data": deepcopy(node["data"]),
                    "semantic_edges": [
                        semantic_edge_expectation(edge) for edge in reference_edges
                    ],
                }
            elif command_type in {
                "create_image_prompt_node",
                "create_video_prompt_node",
            }:
                prompt = _required_text(command, "prompt", op_index=index)
                node_id = _required_text(command, "created_node_id", op_index=index)
                if node_id in by_id:
                    raise CanvasCommandError(
                        f"画布节点 ID 已存在：{node_id}",
                        code="canvas_command_node_id_conflict",
                        op_index=index,
                    )
                node_type = (
                    "videoNode"
                    if command_type == "create_video_prompt_node"
                    else "imageGenNode"
                )
                if node_type == "videoNode":
                    _validate_video_prompt_command(command, op_index=index)
                    prompt, prompt_source = _compile_video_prompt_if_contractual(
                        command,
                        op_index=index,
                    )
                else:
                    prompt_source = ""
                data = {
                    **_node_parameters(command, node_type, op_index=index),
                    "displayName": str(
                        command.get("display_name")
                        or (
                            "小树视频方案"
                            if node_type == "videoNode"
                            else "小树图片方案"
                        )
                    )[:200],
                    "prompt": prompt,
                    "compiledPromptPreview": prompt,
                    "canvas_auto_generate_once": False,
                    "agent_command_id": command_id,
                }
                if prompt_source:
                    data["promptSource"] = prompt_source
                node = _build_node(
                    node_id=node_id,
                    node_type=node_type,
                    x=float(command["x"]),
                    y=float(command["y"]),
                    data=data,
                )
                nodes.append(node)
                by_id[node_id] = node
                reference_edges = append_reference_binding_edges(
                    node_id, command, index
                )
                created_ids.append(node_id)
                target_ids = [node_id]
                changed_fields = ["node", "edges"] if reference_edges else ["node"]
                expectation = {
                    "kind": "node_present",
                    "node_id": node_id,
                    "node_type": node_type,
                    "position": deepcopy(node["position"]),
                    "data": deepcopy(node["data"]),
                    "semantic_edges": [
                        semantic_edge_expectation(edge) for edge in reference_edges
                    ],
                }
            elif command_type == "annotate":
                text = _required_text(command, "text", op_index=index)
                node_id = _required_text(command, "created_node_id", op_index=index)
                if node_id in by_id:
                    raise CanvasCommandError(
                        f"画布节点 ID 已存在：{node_id}",
                        code="canvas_command_node_id_conflict",
                        op_index=index,
                    )
                data = {
                    "displayName": str(command.get("display_name") or "小树导演备注")[
                        :200
                    ],
                    "text": text,
                    "content": text,
                    "agent_command_id": command_id,
                }
                node = _build_node(
                    node_id=node_id,
                    node_type="textAnnotationNode",
                    x=float(command["x"]),
                    y=float(command["y"]),
                    data=data,
                )
                nodes.append(node)
                by_id[node_id] = node
                created_ids.append(node_id)
                target_ids = [node_id]
                changed_fields = ["node"]
                expectation = {
                    "kind": "node_present",
                    "node_id": node_id,
                    "node_type": "textAnnotationNode",
                    "position": deepcopy(node["position"]),
                    "data": deepcopy(node["data"]),
                }
            elif command_type == "create_shot_sequence":
                prompts = list(command.get("prompts") or [])
                ids = [str(value) for value in (command.get("created_node_ids") or [])]
                if not prompts or len(prompts) != len(ids) or len(prompts) > 12:
                    raise CanvasCommandError(
                        "分镜序列缺少有效提示词或镜数超过 12",
                        code="canvas_command_invalid",
                        op_index=index,
                    )
                expected_nodes: list[dict[str, Any]] = []
                expected_edges: list[dict[str, Any]] = []
                root = str(command.get("display_name") or "小树分镜")[:180]
                for shot_index, (node_id, prompt) in enumerate(
                    zip(ids, prompts, strict=True)
                ):
                    if node_id in by_id:
                        raise CanvasCommandError(
                            f"画布节点 ID 已存在：{node_id}",
                            code="canvas_command_node_id_conflict",
                            op_index=index,
                        )
                    data = {
                        **_node_parameters(command, "imageGenNode", op_index=index),
                        "displayName": f"{root} · 镜{shot_index + 1}",
                        "prompt": str(prompt),
                        "compiledPromptPreview": str(prompt),
                        "canvas_auto_generate_once": False,
                        "agent_command_id": command_id,
                    }
                    node = _build_node(
                        node_id=node_id,
                        node_type="imageGenNode",
                        x=float(command["x"]) + shot_index * 40,
                        y=float(command["y"])
                        + shot_index
                        * (
                            _AGENT_NODE_LAYOUT_SIZES["imageGenNode"][1]
                            + _AGENT_NODE_LAYOUT_MARGIN
                        ),
                        data=data,
                    )
                    nodes.append(node)
                    by_id[node_id] = node
                    reference_edges = append_reference_binding_edges(
                        node_id, command, index
                    )
                    expected_edges.extend(
                        semantic_edge_expectation(edge) for edge in reference_edges
                    )
                    created_ids.append(node_id)
                    expected_nodes.append(
                        {
                            "node_id": node_id,
                            "node_type": "imageGenNode",
                            "position": deepcopy(node["position"]),
                            "data": deepcopy(node["data"]),
                        }
                    )
                    if shot_index > 0:
                        edge = append_edge(
                            ids[shot_index - 1],
                            node_id,
                            index,
                            relation="continuity",
                        )
                        expected_edges.append(semantic_edge_expectation(edge))
                target_ids = ids
                changed_fields = ["nodes", "edges"]
                expectation = {
                    "kind": "graph_present",
                    "nodes": expected_nodes,
                    "edges": expected_edges,
                }
            elif command_type == "insert_starter_workflow":
                workflow_id = _required_text(command, "workflow_id", op_index=index)
                definition = catalog.get(workflow_id)
                if definition is None:
                    raise CanvasCommandError(
                        f"未知画布起步工作流：{workflow_id}",
                        code="canvas_starter_workflow_not_found",
                        op_index=index,
                    )
                specs = [
                    spec
                    for spec in (definition.get("nodes") or [])
                    if isinstance(spec, dict)
                ]
                ids = [str(value) for value in (command.get("created_node_ids") or [])]
                if len(specs) != len(ids) or not ids:
                    raise CanvasCommandError(
                        "起步工作流节点定义不完整",
                        code="canvas_starter_workflow_catalog_invalid",
                        op_index=index,
                    )
                key_to_id: dict[str, str] = {}
                expected_nodes = []
                for spec, node_id in zip(specs, ids, strict=True):
                    if node_id in by_id:
                        raise CanvasCommandError(
                            f"画布节点 ID 已存在：{node_id}",
                            code="canvas_command_node_id_conflict",
                            op_index=index,
                        )
                    key = str(spec.get("key") or "").strip()
                    node_type = str(spec.get("type") or "").strip()
                    if not key or node_type not in _GENERIC_NODE_TYPES:
                        raise CanvasCommandError(
                            "起步工作流包含无效节点定义",
                            code="canvas_starter_workflow_catalog_invalid",
                            op_index=index,
                        )
                    offset = _as_dict(spec.get("offset"))
                    data = {
                        **_as_dict(spec.get("data")),
                        "agent_command_id": command_id,
                        "starter_workflow_id": workflow_id,
                    }
                    node = _build_node(
                        node_id=node_id,
                        node_type=node_type,
                        x=float(command["x"]) + float(offset.get("x") or 0),
                        y=float(command["y"]) + float(offset.get("y") or 0),
                        data=data,
                    )
                    nodes.append(node)
                    by_id[node_id] = node
                    key_to_id[key] = node_id
                    created_ids.append(node_id)
                    expected_nodes.append(
                        {
                            "node_id": node_id,
                            "node_type": node_type,
                            "position": deepcopy(node["position"]),
                            "data": deepcopy(node["data"]),
                        }
                    )
                expected_edges = []
                for edge_spec in definition.get("edges") or []:
                    if not isinstance(edge_spec, dict):
                        continue
                    source = key_to_id.get(str(edge_spec.get("source") or ""), "")
                    target = key_to_id.get(str(edge_spec.get("target") or ""), "")
                    if not source or not target:
                        raise CanvasCommandError(
                            "起步工作流包含无效连线定义",
                            code="canvas_starter_workflow_catalog_invalid",
                            op_index=index,
                        )
                    edge = append_edge(
                        source,
                        target,
                        index,
                        relation=str(edge_spec.get("relation") or "depends_on"),
                    )
                    expected_edges.append(semantic_edge_expectation(edge))
                target_ids = ids
                changed_fields = ["nodes", "edges"]
                expectation = {
                    "kind": "graph_present",
                    "nodes": expected_nodes,
                    "edges": expected_edges,
                    "workflow_id": workflow_id,
                }
            elif command_type == "connect_nodes":
                source = _concrete_node_id(command, "source", op_index=index)
                target = _concrete_node_id(command, "target", op_index=index)
                try:
                    relation = normalize_edge_relation(
                        command.get("relation"),
                        default="depends_on",
                        strict=True,
                    )
                except ValueError as exc:
                    raise CanvasCommandError(
                        "不支持的语义连线关系",
                        code="canvas_semantic_edge_relation_invalid",
                        op_index=index,
                        details={"relation": str(command.get("relation") or "")},
                    ) from exc
                edge = append_edge(source, target, index, relation=relation)
                target_ids = [source, target]
                changed_fields = ["edge"]
                expectation = {
                    "kind": "semantic_edge",
                    **semantic_edge_expectation(edge),
                }
            elif command_type == "remove_edge":
                edge_id = str(command.get("edge_id") or "").strip()
                source = str(command.get("source") or "").strip()
                target = str(command.get("target") or "").strip()
                if edge_id:
                    matched = [
                        edge for edge in edges if str(edge.get("id") or "") == edge_id
                    ]
                else:
                    source = _concrete_node_id(command, "source", op_index=index)
                    target = _concrete_node_id(command, "target", op_index=index)
                    matched = [
                        edge
                        for edge in edges
                        if str(edge.get("source") or "") == source
                        and str(edge.get("target") or "") == target
                    ]
                if not matched:
                    raise CanvasCommandError(
                        "待删除的画布连线不存在",
                        code="canvas_command_edge_missing",
                        op_index=index,
                    )
                matched_ids = {id(edge) for edge in matched}
                edges[:] = [edge for edge in edges if id(edge) not in matched_ids]
                target_ids = [value for value in (source, target) if value]
                changed_fields = ["edge"]
                expectation = {
                    "kind": "edge_absent",
                    "edge_id": edge_id,
                    "source": source,
                    "target": target,
                }
            elif command_type in {
                "update_node_label",
                "update_node_prompt",
                "update_node_data",
                "update_node_camera",
            }:
                node_id = _concrete_node_id(command, "node_id", op_index=index)
                node = require_node(node_id, index)
                before = _as_dict(node.get("data"))
                data = dict(before)
                if command_type == "update_node_label":
                    value = _required_text(command, "display_name", op_index=index)
                    data["displayName"] = value[:200]
                elif command_type == "update_node_prompt":
                    value = _required_text(command, "prompt", op_index=index)
                    data["prompt"] = value
                    data["compiledPromptPreview"] = value
                    if str(node.get("type") or "") == "textAnnotationNode":
                        data["text"] = value
                        data["content"] = value
                    display_name = str(command.get("display_name") or "").strip()
                    if display_name:
                        data["displayName"] = display_name[:200]
                    if str(node.get("type") or "") == "videoNode":
                        # Prompt revisions may carry the complete media
                        # contract. Persist it with the prompt so a later
                        # real run cannot inherit stale node controls.
                        _validate_video_prompt_command(command, op_index=index)
                        data.update(
                            _node_parameters(command, "videoNode", op_index=index)
                        )
                    elif str(node.get("type") or "") == "imageGenNode":
                        data.update(
                            _node_parameters(command, "imageGenNode", op_index=index)
                        )
                else:
                    patch = command.get("node_data")
                    if not isinstance(patch, dict):
                        patch = {}
                    has_camera_fields = any(
                        command.get(key) not in (None, "")
                        for key in ("camera", "camera_movement", "clear_camera")
                    )
                    if not patch and not has_camera_fields:
                        raise CanvasCommandError(
                            f"{command_type} 缺少非空 node_data",
                            code="canvas_command_invalid",
                            op_index=index,
                        )
                    patch, camera_request = _normalize_camera_patch(
                        command=command,
                        node_type=str(node.get("type") or ""),
                        patch=patch,
                        current_data=before,
                        op_index=index,
                    )
                    if command_type == "update_node_camera" and camera_request is None:
                        raise CanvasCommandError(
                            "update_node_camera 缺少摄像机参数",
                            code="canvas_camera_invalid",
                            op_index=index,
                        )
                    script_media_gateway.apply_node_data(
                        command,
                        node,
                        data,
                        patch,
                        by_id,
                        edges,
                        index,
                        node_id,
                        CanvasCommandError,
                    )
                data["agent_command_id"] = command_id
                _stamp_node_metadata(
                    data,
                    node_type=str(node.get("type") or ""),
                    command_id=command_id,
                    op_index=index,
                )
                changed_fields = _changed_fields(before, data)
                if not changed_fields:
                    raise CanvasCommandError(
                        "画布节点数据没有变化",
                        code="canvas_command_noop",
                        op_index=index,
                    )
                node["data"] = data
                camera_update = _camera_update_for_node(
                    node_id=node_id,
                    node_type=str(node.get("type") or ""),
                    before=before,
                    after=data,
                )
                if camera_update is not None:
                    camera_updates.append(camera_update)
                remember(node_id)
                target_ids = [node_id]
                expectation = {
                    "kind": "node_data",
                    "node_id": node_id,
                    "data": {key: deepcopy(data[key]) for key in changed_fields},
                }
            elif command_type == "move_node":
                node_id = _concrete_node_id(command, "node_id", op_index=index)
                node = require_node(node_id, index)
                before = _as_dict(node.get("position"))
                x = (
                    float(command["x"])
                    if _is_number(command.get("x"))
                    else float(before.get("x") or 0) + 40.0
                )
                y = (
                    float(command["y"])
                    if _is_number(command.get("y"))
                    else float(before.get("y") or 0) + 40.0
                )
                after = {"x": x, "y": y}
                if before == after:
                    raise CanvasCommandError(
                        "画布节点位置没有变化",
                        code="canvas_command_noop",
                        op_index=index,
                    )
                node["position"] = after
                data = _as_dict(node.get("data"))
                data["agent_command_id"] = command_id
                _stamp_node_metadata(
                    data,
                    node_type=str(node.get("type") or ""),
                    command_id=command_id,
                    op_index=index,
                )
                node["data"] = data
                remember(node_id)
                target_ids = [node_id]
                changed_fields = ["position"]
                expectation = {
                    "kind": "node_position",
                    "node_id": node_id,
                    "position": after,
                }
            elif command_type == "duplicate_node":
                source_id = _concrete_node_id(command, "node_id", op_index=index)
                source = require_node(source_id, index)
                node_id = _required_text(command, "created_node_id", op_index=index)
                if node_id in by_id:
                    raise CanvasCommandError(
                        f"画布节点 ID 已存在：{node_id}",
                        code="canvas_command_node_id_conflict",
                        op_index=index,
                    )
                source_position = _as_dict(source.get("position"))
                data = script_media_gateway.copy_node_data(source)
                data["agent_command_id"] = command_id
                data["agent_duplicate_source_id"] = source_id
                node = _build_node(
                    node_id=node_id,
                    node_type=str(source.get("type") or "imageGenNode"),
                    x=float(source_position.get("x") or 0) + 40.0,
                    y=float(source_position.get("y") or 0) + 40.0,
                    data=data,
                )
                nodes.append(node)
                by_id[node_id] = node
                created_ids.append(node_id)
                remember(source_id)
                target_ids = [source_id, node_id]
                changed_fields = ["node"]
                expectation = {
                    "kind": "node_present",
                    "node_id": node_id,
                    "node_type": str(node["type"]),
                    "position": deepcopy(node["position"]),
                    "data": deepcopy(node["data"]),
                }
            elif command_type == "delete_node":
                node_id = _concrete_node_id(command, "node_id", op_index=index)
                require_node(node_id, index)
                by_id.pop(node_id, None)
                nodes[:] = [
                    node for node in nodes if str(node.get("id") or "") != node_id
                ]
                edges[:] = [
                    edge
                    for edge in edges
                    if str(edge.get("source") or "") != node_id
                    and str(edge.get("target") or "") != node_id
                ]
                remember(node_id)
                target_ids = [node_id]
                changed_fields = ["node", "incident_edges"]
                expectation = {"kind": "node_absent", "node_id": node_id}
            else:
                raise CanvasCommandError(
                    f"未知画布命令：{command_type or '<empty>'}",
                    code="canvas_command_unknown_operation",
                    op_index=index,
                    details={"type": command_type},
                )

            structural_ops += 1
            for node_id in target_ids:
                remember(node_id)
            expectations.append({"index": index, "type": command_type, **expectation})
            op_results.append(
                {
                    "index": index,
                    "type": command_type,
                    "target_ids": target_ids,
                    "status": "applied",
                    "changed_fields": changed_fields,
                    "error_code": "",
                }
            )

        if structural_ops <= 0:
            raise CanvasCommandError(
                "命令批次只包含界面展示操作，不能完成生产步骤",
                code="canvas_command_no_structural_ops",
            )
        # Verifier reads one authoritative post-transaction snapshot. When a
        # batch creates a node and then updates or moves it, the create
        # expectation must describe that final state rather than the
        # intermediate state at the moment of creation.
        for operation in expectations:
            if operation.get("kind") == "node_present":
                node = by_id.get(str(operation.get("node_id") or ""))
                if node is not None:
                    operation["node_type"] = str(node.get("type") or "")
                    operation["position"] = deepcopy(_as_dict(node.get("position")))
                    operation["data"] = deepcopy(_as_dict(node.get("data")))
            elif operation.get("kind") == "graph_present":
                for spec in operation.get("nodes") or []:
                    if not isinstance(spec, dict):
                        continue
                    node = by_id.get(str(spec.get("node_id") or ""))
                    if node is None:
                        continue
                    spec["node_type"] = str(node.get("type") or "")
                    spec["position"] = deepcopy(_as_dict(node.get("position")))
                    spec["data"] = deepcopy(_as_dict(node.get("data")))
        expectation = {
            "schema": "canvas_command_expectation.v1",
            "command_id": command_id,
            "receipt_metadata_key": _CANVAS_COMMAND_RECEIPTS_KEY,
            "operations": expectations,
        }
        return _Mutation(
            nodes=nodes,
            edges=edges,
            normalized_envelope=deepcopy(envelope),
            expectation=expectation,
            op_results=op_results,
            created_node_ids=created_ids,
            affected_node_ids=affected_ids,
            camera_updates=camera_updates,
            structural_ops=structural_ops,
            presentation_ops=presentation_ops,
        )


__all__ = ["CanvasCommandError", "CanvasCommandGateway"]
