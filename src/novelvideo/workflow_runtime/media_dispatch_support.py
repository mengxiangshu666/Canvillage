"""Server-owned media submission and reconciliation for WorkflowRun."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any


from novelvideo.project_context import ProjectContext, resolve_project_context
from novelvideo.production.asset_passport import (
    validate_reference_binding_identities,
)
from novelvideo.production.cost_receipt import (
    project_production_cost_receipt,
)
from novelvideo.production.shot_contract import (
    build_shot_contract,
    validate_shot_contract,
)
from novelvideo.workflow_runtime.model_plan import (
    WorkflowModelPlanError,
    compile_snapshot_image_parameters,
    compile_snapshot_video_parameters,
)
from novelvideo.services.video_request_contract import (
    normalize_video_resolution_value,
    video_execution_prompt_key,
)
from novelvideo.services.delivery_fps import project_delivery_fps_receipt


def _text(value: object) -> str:
    return str(value or "").strip()


def _requested_delivery_seconds(run: dict[str, Any]) -> float | None:
    """请求里点名的秒数。没点名就返回空，不凭空判失败。"""

    from novelvideo.services.production_contracts import (
        requested_combat_duration_seconds,
    )

    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    return requested_combat_duration_seconds(inputs.get("request"))


def _compose_duration_mismatch(
    artifact: dict[str, Any],
    delivery_qc: dict[str, Any],
    requested_seconds: float | None,
) -> dict[str, Any] | None:
    """成片时长跟请求对不上时返回失败结果，否则返回空。"""

    if requested_seconds is None or delivery_qc.get("passed") is True:
        return None
    checks = delivery_qc.get("checks")
    duration_check = checks.get("duration") if isinstance(checks, dict) else None
    if not isinstance(duration_check, dict) or duration_check.get("passed") is not False:
        return None
    return {
        **artifact,
        "kind": "compose_episode",
        "status": "failed",
        "error_code": "workflow_compose_duration_mismatch",
        "error": (
            f"成片实际时长与请求点名的 {requested_seconds:g} 秒对不上，不作为完成交付"
        ),
        "delivery_qc": delivery_qc,
    }


def _video_native_audio_capability(backend: object) -> str:
    """Read the live direct-model audio contract without making network calls."""
    try:
        from novelvideo.generators.video.direct_models import resolve_direct_video_model

        model = resolve_direct_video_model(_text(backend))
        native = getattr(getattr(model, "capability", None), "native_audio", None)
        return _text(getattr(native, "value", native)) or "optional"
    except Exception:
        return "optional"


def _task_cost_receipt(task: object) -> dict[str, Any]:
    """Project the bounded cost receipt carried by one durable task.

    ``TaskState`` exposes metadata while its persisted result keeps the same
    metadata under ``task_metadata``.  Reading both shapes makes recovery
    compatible with workers that were started before the receipt was added.
    The receipt itself is already normalized by ``run_core``; this helper only
    allowlists its stable fields before they cross into WorkflowRun artifacts.
    """

    metadata = getattr(task, "metadata", None)
    result = getattr(task, "result", None)
    result_metadata = result.get("task_metadata") if isinstance(result, dict) else None
    candidates = (
        metadata.get("production_cost_receipt") if isinstance(metadata, dict) else None,
        result_metadata.get("production_cost_receipt")
        if isinstance(result_metadata, dict)
        else None,
    )
    raw = next((value for value in candidates if isinstance(value, dict)), None)
    return project_production_cost_receipt(raw)


def _deterministic_job_id(
    run: dict[str, Any],
    *,
    step_id: str,
    node_id: str,
    retry_seq: int,
) -> str:
    material = ":".join(
        (
            _text(run.get("id")),
            step_id,
            node_id,
            str(max(0, retry_seq)),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


async def resolve_workflow_project_context(run: dict[str, Any]) -> ProjectContext:
    persisted = run.get("project_context")
    persisted = persisted if isinstance(persisted, dict) else {}
    requester_user_id = _text(persisted.get("requester_user_id"))
    requester_username = _text(persisted.get("requester_username"))
    if (
        not requester_user_id
        and os.environ.get("ST_EDITION", "").strip().lower() == "ce"
    ):
        requester_user_id = "local"
        requester_username = requester_username or "local"
    if not requester_user_id:
        raise RuntimeError("工作流缺少服务端媒体调度身份，请新建运行后重试")
    return await resolve_project_context(
        user={
            "id": requester_user_id,
            "user_id": requester_user_id,
            "username": requester_username or requester_user_id,
        },
        project_id=_text(run.get("project_id")),
        required_role="editor",
    )


def _requested_aspect_ratio(run: dict[str, Any], data: dict[str, Any]) -> str:
    value = _text(
        data.get("aspectRatio")
        or data.get("aspect_ratio")
        or (run.get("inputs") or {}).get("aspect_ratio")
    )
    return value if value and value != "auto" else ""


def _requested_image_size(run: dict[str, Any], data: dict[str, Any]) -> str:
    return _text(
        data.get("imageSize")
        or data.get("image_size")
        or (run.get("inputs") or {}).get("image_size")
    )


def _compile_snapshot_image_request(
    run: dict[str, Any],
    data: dict[str, Any],
    model_ref: str,
) -> dict[str, Any] | None:
    """Apply image parameter gates from a frozen direct-model snapshot."""

    snapshot = run.get("model_plan_snapshot")
    if not isinstance(snapshot, dict):
        return None
    bindings = snapshot.get("bindings")
    binding = bindings.get("image") if isinstance(bindings, dict) else None
    if not isinstance(binding, dict):
        return None
    registry_id = _text(binding.get("registry_id")).casefold()
    backend_key = _text(model_ref).casefold()
    if not registry_id or backend_key not in {
        registry_id,
        f"direct_{registry_id}",
        f"direct/{registry_id}",
    }:
        return None
    capabilities = binding.get("capabilities")
    if not isinstance(capabilities, dict):
        return None
    reference_items = _reference_items(data)
    reference_bindings = data.get("referenceBindings") or data.get("reference_bindings")
    reference_count = len(reference_items)
    if isinstance(reference_bindings, dict):
        reference_count += sum(
            len(value) if isinstance(value, (list, tuple)) else 1
            for value in reference_bindings.values()
            if value not in (None, "", [], {})
        )
    requested = {
        "mode": data.get("genMode")
        or data.get("gen_mode")
        or data.get("generationMode")
        or "",
        "aspect_ratio": _requested_aspect_ratio(run, data),
        "image_size": _requested_image_size(run, data),
        "quality": _requested_quality(run, data),
        "reference_count": reference_count,
    }
    if not requested["mode"]:
        requested.pop("mode")
    run_inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    explicit_fields: set[str] = set()
    if any(
        data.get(key) not in (None, "")
        for key in ("genMode", "gen_mode", "generationMode")
    ):
        explicit_fields.add("mode")
    if capabilities.get("aspect_ratio_parameter_enabled") is not False and (
        any(
            data.get(key) not in (None, "", "auto")
            for key in ("aspectRatio", "aspect_ratio")
        )
        or run_inputs.get("aspect_ratio") not in (None, "", "auto")
    ):
        explicit_fields.add("aspect_ratio")
    if capabilities.get("resolution_parameter_enabled") is not False and (
        any(data.get(key) not in (None, "") for key in ("imageSize", "image_size"))
        or run_inputs.get("image_size") not in (None, "")
    ):
        explicit_fields.add("image_size")
    if data.get("quality") not in (None, "") or run_inputs.get("quality") not in (
        None,
        "",
    ):
        explicit_fields.add("quality")
    try:
        return compile_snapshot_image_parameters(
            snapshot,
            requested,
            explicit_fields=explicit_fields,
        )
    except WorkflowModelPlanError as exc:
        error = ValueError("工作流图片节点不符合冻结模型能力合同")
        error.details = {"code": exc.code, **dict(exc.details)}
        raise error from exc


def _requested_quality(run: dict[str, Any], data: dict[str, Any]) -> str:
    return _text(
        data.get("quality") or (run.get("inputs") or {}).get("quality") or "medium"
    )


def _audio_prompt(data: dict[str, Any]) -> str:
    direct = _text(data.get("text") or data.get("prompt"))
    if direct:
        return direct
    parts: list[str] = []
    for segment in data.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        kind = _text(segment.get("type")).casefold()
        if kind == "text":
            value = _text(segment.get("value"))
        elif kind == "filler":
            value = _text(segment.get("token"))
        elif kind == "pause":
            value = f"[pause {_text(segment.get('durationSec') or segment.get('duration_sec'))}s]"
        else:
            value = ""
        if value:
            parts.append(value)
    return " ".join(parts).strip()


def _audio_kind(data: dict[str, Any]) -> str:
    value = _text(data.get("audioKind") or data.get("audio_kind")).casefold()
    return "music" if value == "music" else "speech"


def _audio_mode(data: dict[str, Any]) -> str:
    explicit = _text(
        data.get("generationMode") or data.get("generation_mode") or data.get("mode")
    )
    if explicit:
        return explicit
    return "text_to_music" if _audio_kind(data) == "music" else "text_to_speech"


def _voice_reference_receipt(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("voiceRef") or data.get("voice_ref")
    raw = raw if isinstance(raw, dict) else {"scope": "project_narrator"}
    scope = _text(raw.get("scope")) or "project_narrator"
    identity = _text(
        raw.get("voice_id")
        or raw.get("voiceId")
        or raw.get("identity_id")
        or raw.get("identityId")
        or raw.get("character_name")
        or raw.get("characterName")
        or scope
    )
    sha256 = _text(
        raw.get("sha256") or raw.get("voice_sha256") or raw.get("voiceSha256")
    ).casefold()
    if sha256 and (
        len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256)
    ):
        sha256 = ""
    try:
        revision = max(0, int(raw.get("revision") or 0))
    except (TypeError, ValueError):
        revision = 0
    return {
        "schema": "voice_reference_receipt.v1",
        "scope": scope,
        "identity": identity,
        "sha256": sha256,
        "revision": revision,
    }


def _voice_ref_payload(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("voiceRef") or data.get("voice_ref")
    raw = raw if isinstance(raw, dict) else {"scope": "project_narrator"}
    return {
        "scope": _text(raw.get("scope")) or "project_narrator",
        "character_name": _text(raw.get("character_name") or raw.get("characterName")),
        "identity_id": _text(raw.get("identity_id") or raw.get("identityId")),
        "slot": _text(raw.get("slot")),
        "voice_id": _text(raw.get("voice_id") or raw.get("voiceId")),
    }


def _requested_video_mode(data: dict[str, Any]) -> str:
    return _text(data.get("genMode") or data.get("gen_mode") or "textToVideo")


def _requested_video_duration(run: dict[str, Any], data: dict[str, Any]) -> int:
    value = data.get("durationSec")
    if value is None:
        value = data.get("duration_seconds")
    if value is None:
        value = (run.get("inputs") or {}).get("video_duration_seconds")
    try:
        return max(1, int(round(float(value if value is not None else 5))))
    except (TypeError, ValueError):
        return 5


def _requested_video_resolution(run: dict[str, Any], data: dict[str, Any]) -> str:
    value = _text(
        data.get("resolution")
        or data.get("videoResolution")
        or (run.get("inputs") or {}).get("video_resolution")
    )
    if value:
        return value
    quality = _text(data.get("quality") or (run.get("inputs") or {}).get("quality"))
    return normalize_video_resolution_value(quality) or ""


def _compile_snapshot_video_request(
    run: dict[str, Any],
    data: dict[str, Any],
    backend: str,
) -> dict[str, Any] | None:
    """Apply the frozen WorkflowRun capability contract before queueing.

    Legacy runs and non-direct backends keep their provider-specific defaults.
    Direct runs use the snapshot as the single source of truth, so an explicit
    upstream ``[]`` remains an omitted request field instead of becoming a
    local 16:9/720p default.
    """

    snapshot = run.get("model_plan_snapshot")
    if not isinstance(snapshot, dict):
        return None
    bindings = snapshot.get("bindings")
    binding = bindings.get("video") if isinstance(bindings, dict) else None
    if not isinstance(binding, dict):
        return None
    registry_id = _text(binding.get("registry_id")).casefold()
    backend_key = _text(backend).casefold()
    if not registry_id or backend_key not in {
        registry_id,
        f"direct_{registry_id}",
        f"direct/{registry_id}",
    }:
        return None
    requested: dict[str, Any] = {
        "mode": data.get("genMode") or data.get("gen_mode") or "",
        "duration_seconds": data.get("durationSec")
        if data.get("durationSec") is not None
        else data.get("duration_seconds"),
    }
    if not requested["mode"]:
        requested.pop("mode")
    if requested.get("duration_seconds") is None:
        requested.pop("duration_seconds")
    for target, keys in {
        "aspect_ratio": ("aspectRatio", "aspect_ratio"),
        "resolution": ("resolution", "videoResolution", "video_resolution"),
        "generate_audio": ("generateAudio", "generate_audio"),
        "size": ("size", "sizeSlot", "videoSize", "video_size"),
    }.items():
        for key in keys:
            if key in data and data[key] is not None:
                requested[target] = data[key]
                break
    raw_parameters = data.get("parameters")
    if isinstance(raw_parameters, dict):
        requested["parameters"] = dict(raw_parameters)
    explicit_fields: set[str] = set()
    if any(data.get(key) not in (None, "") for key in ("genMode", "gen_mode")):
        explicit_fields.add("mode")
    capabilities = (
        binding.get("capabilities")
        if isinstance(binding.get("capabilities"), dict)
        else {}
    )
    if capabilities.get("aspect_ratio_parameter_enabled") is not False and any(
        data.get(key) not in (None, "", "auto")
        for key in ("aspectRatio", "aspect_ratio")
    ):
        explicit_fields.add("aspect_ratio")
    if capabilities.get("resolution_parameter_enabled") is not False and any(
        data.get(key) not in (None, "")
        for key in ("resolution", "videoResolution", "video_resolution")
    ):
        explicit_fields.add("resolution")
    if any(
        key in data and data.get(key) is not None
        for key in ("durationSec", "duration_seconds")
    ):
        explicit_fields.add("duration_seconds")
    if any(
        key in data and data.get(key) is not None
        for key in ("generateAudio", "generate_audio")
    ):
        explicit_fields.add("generate_audio")
    if any(
        data.get(key) not in (None, "")
        for key in ("size", "sizeSlot", "videoSize", "video_size")
    ):
        explicit_fields.add("size")
    try:
        compiled = compile_snapshot_video_parameters(
            snapshot,
            requested,
            explicit_fields=explicit_fields,
        )
        if size := _text(compiled.get("size")):
            parameters = dict(compiled.get("parameters") or {})
            parameters.setdefault("size", size)
            compiled["parameters"] = parameters
        return compiled
    except WorkflowModelPlanError as exc:
        error = ValueError("工作流视频节点不符合冻结模型能力合同")
        error.details = {"code": exc.code, **dict(exc.details)}
        raise error from exc


def _reference_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("referenceItems")
    if not isinstance(raw, (list, tuple)):
        raw = data.get("reference_items")
    items: list[dict[str, Any]] = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, dict) and _text(item.get("path") or item.get("url")):
                items.append(dict(item))
    first_frame = _text(data.get("firstFramePath") or data.get("first_frame_path"))
    if first_frame and not any(
        _text(item.get("path") or item.get("url")) == first_frame for item in items
    ):
        items.insert(0, {"type": "image", "path": first_frame, "role": "首帧"})
    return items


def _require_reference_identity_gate(
    run: dict[str, Any],
    *,
    snapshot: dict[str, Any],
    data: dict[str, Any],
    node_id: str,
) -> dict[str, Any]:
    """Require locked semantic asset identities for current v2 production runs."""

    bindings = data.get("referenceBindings") or data.get("reference_bindings")
    if int(run.get("contract_version") or 1) < 2 or not isinstance(bindings, dict):
        return {
            "schema": "asset_identity_gate.v1",
            "passed": True,
            "binding_count": 0,
            "passports": [],
            "issues": [],
            "policy": "legacy_or_unbound",
        }
    gate = validate_reference_binding_identities(snapshot=snapshot, bindings=bindings)
    if gate["passed"]:
        return gate
    error = ValueError(f"工作流媒体节点 {node_id} 的参考资产尚未完成身份锁定")
    error.details = {
        "code": "workflow_asset_identity_gate_failed",
        "node_id": node_id,
        "identity_gate": gate,
        "media_submission_started": False,
    }
    raise error


def _requested_last_frame(data: dict[str, Any]) -> str | None:
    value = _text(data.get("lastFramePath") or data.get("last_frame_path"))
    return value or None


def _actual_aspect_ratio(width: int, height: int) -> str:
    divisor = math.gcd(width, height)
    return f"{width // divisor}:{height // divisor}" if divisor else f"{width}:{height}"


def _aspect_ratio_value(value: object) -> float | None:
    text = _text(value).lower()
    if ":" not in text:
        return None
    left, right = (part.strip() for part in text.split(":", 1))
    try:
        width, height = float(left), float(right)
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width / height


def _video_completion_node_patch(
    job: dict[str, Any],
    output: dict[str, Any],
) -> dict[str, Any]:
    """Project the frozen request and verified media facts onto the node."""
    from novelvideo.services.video_generation_source import video_generation_source

    patch: dict[str, Any] = {"videoGenerationSource": video_generation_source(
        output.get("video_generation_source"), output_url=_text(output.get("output_url") or output.get("url")),
        job_id=_text(job.get("job_id")),
    )}
    resolution = _text(job.get("requested_resolution"))
    if resolution:
        patch.update(
            {
                "resolution": resolution,
                "quality": resolution.replace("p", "P").replace("k", "K"),
                "lastRequestedResolution": resolution,
            }
        )
    duration = job.get("requested_duration_seconds")
    if isinstance(duration, int) and not isinstance(duration, bool) and duration > 0:
        patch.update(
            {
                "durationSec": duration,
                "lastRequestedDurationSeconds": duration,
            }
        )
    aspect_ratio = _text(job.get("requested_aspect_ratio"))
    if aspect_ratio:
        patch.update(
            {
                "aspectRatio": aspect_ratio,
                "lastRequestedAspectRatio": aspect_ratio,
            }
        )
    delivery_spec = _normalize_delivery_spec(
        job.get("delivery_spec") or job.get("deliverySpec")
    )
    if delivery_spec:
        patch["deliverySpec"] = delivery_spec
    requested_fps = job.get("requested_fps") or job.get("fps")
    if isinstance(requested_fps, (int, float)) and not isinstance(requested_fps, bool):
        if requested_fps > 0:
            patch["requestedFps"] = int(requested_fps)
    delivery_fps = project_delivery_fps_receipt(job.get("delivery_fps"))
    if delivery_fps is not None:
        patch["deliveryFps"] = delivery_fps
    if isinstance(job.get("requested_generate_audio"), bool):
        patch.update(
            {
                "generateAudio": job["requested_generate_audio"],
                "lastRequestedGenerateAudio": job["requested_generate_audio"],
            }
        )
    mode = _text(job.get("requested_mode"))
    if mode:
        patch["genMode"] = mode
    width = output.get("width")
    height = output.get("height")
    duration_seconds = output.get("duration_seconds")
    if isinstance(width, int) and width > 0:
        patch["actualWidth"] = width
    if isinstance(height, int) and height > 0:
        patch["actualHeight"] = height
    if isinstance(width, int) and width > 0 and isinstance(height, int) and height > 0:
        actual_ratio = _actual_aspect_ratio(width, height)
        patch.update(
            {
                "widthPx": width,
                "heightPx": height,
                "actualAspectRatio": actual_ratio,
            }
        )
        requested_ratio = _aspect_ratio_value(aspect_ratio)
        actual_ratio_value = _aspect_ratio_value(actual_ratio)
        if requested_ratio is not None and actual_ratio_value is not None:
            patch["aspectRatioMismatch"] = (
                abs(requested_ratio - actual_ratio_value)
                > max(requested_ratio, actual_ratio_value) * 0.01
            )
    if isinstance(duration_seconds, (int, float)) and not isinstance(
        duration_seconds, bool
    ):
        if float(duration_seconds) > 0:
            patch["durationMs"] = round(float(duration_seconds) * 1000)
    return patch


def _normalize_delivery_spec(value: object) -> dict[str, Any] | None:
    """Return a complete film-delivery spec or ``None`` for legacy nodes."""

    if not isinstance(value, dict):
        return None
    if any(isinstance(value.get(key), bool) for key in ("width", "height", "fps")):
        return None
    try:
        width = int(value.get("width"))
        height = int(value.get("height"))
        fps = int(value.get("fps"))
    except (TypeError, ValueError):
        return None
    aspect_ratio = _text(value.get("aspectRatio") or value.get("aspect_ratio"))
    safe_area = value.get("safeArea") or value.get("safe_area")
    if (
        width <= 0
        or height <= 0
        or fps <= 0
        or not aspect_ratio
        or not isinstance(safe_area, dict)
    ):
        return None
    normalized_safe_area: dict[str, float] = {}
    for key in ("top", "right", "bottom", "left"):
        try:
            item = float(safe_area.get(key))
        except (TypeError, ValueError):
            return None
        if item < 0 or item >= 1:
            return None
        normalized_safe_area[key] = item
    return {
        "width": width,
        "height": height,
        "aspectRatio": aspect_ratio,
        "fps": fps,
        "safeArea": normalized_safe_area,
    }


def _delivery_spec_signature(value: object) -> str:
    spec = _normalize_delivery_spec(value)
    return (
        json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if spec
        else ""
    )


def _shot_contract_source(
    data: dict[str, Any],
    *,
    duration_seconds: int,
) -> dict[str, Any]:
    facts = data.get("shotContractFacts") or data.get("shot_contract_facts")
    source = dict(facts) if isinstance(facts, dict) else {}
    for key in (
        "shotId",
        "shot_id",
        "shotSize",
        "shot_size",
        "subject",
        "action",
        "cameraMovement",
        "camera_motion",
        "firstFrame",
        "first_frame",
        "lastFrame",
        "last_frame",
        "continuityIn",
        "continuity_in",
        "continuityOut",
        "continuity_out",
        "transition",
        "referenceBindings",
        "reference_bindings",
        "executionPrompt",
        "execution_prompt",
    ):
        if key not in source and key in data:
            source[key] = data[key]
    source.setdefault("duration_seconds", duration_seconds)
    return source


def _compile_workflow_shot_contract(
    data: dict[str, Any],
    *,
    duration_seconds: int,
    index: int,
    allow_legacy_prompt_fallback: bool = False,
) -> dict[str, Any]:
    persisted = data.get("shotContract") or data.get("shot_contract")
    persisted_issues = validate_shot_contract(persisted)
    facts = data.get("shotContractFacts") or data.get("shot_contract_facts")
    bound_prompt = None
    if isinstance(facts, dict):
        bound_prompt = facts.get("executionPrompt", facts.get("execution_prompt"))
    if not bound_prompt and isinstance(persisted, dict):
        bound_prompt = persisted.get("execution_prompt")
    if data.get("scriptShotSourceNodeId") and bound_prompt is None:
        error = ValueError("脚本镜头尚无正文执行版本，请回脚本同步后重新生成")
        error.details = {
            "code": "workflow_shot_execution_prompt_missing",
            "media_submission_started": False,
        }
        raise error
    if bound_prompt is not None and (
        not isinstance(bound_prompt, str) or not bound_prompt.strip()
        or video_execution_prompt_key(bound_prompt) != video_execution_prompt_key(data.get("prompt"))
    ):
        error = ValueError("工作流镜头正文与已保存执行事实不一致，请回脚本同步后重新生成")
        error.details = {
            "code": "workflow_shot_execution_prompt_drift",
            "media_submission_started": False,
        }
        raise error
    if isinstance(persisted, dict) and not facts and not persisted_issues:
        return dict(persisted)
    compiled = build_shot_contract(
        _shot_contract_source(data, duration_seconds=duration_seconds),
        index=index,
    )
    issues = validate_shot_contract(compiled)
    if (
        issues
        and allow_legacy_prompt_fallback
        and not isinstance(persisted, dict)
        and not facts
    ):
        # Contract-v1 canvas nodes predate shot facts.  Preserve their historical
        # prompt-only submissions with a deterministic contract instead of
        # blocking the whole run; v2 runs still require real cinematic facts.
        prompt = _text(data.get("prompt"))
        legacy_source = _shot_contract_source(data, duration_seconds=duration_seconds)
        legacy_source.setdefault("subject", data.get("title") or prompt)
        legacy_source.setdefault("action", prompt)
        legacy_source.setdefault(
            "camera_motion",
            data.get("cameraMovement")
            or data.get("camera_motion")
            or "按提示词完成镜头运动",
        )
        legacy_source.setdefault(
            "first_frame",
            data.get("firstFrame") or data.get("first_frame") or prompt,
        )
        legacy_source.setdefault(
            "last_frame",
            data.get("lastFrame") or data.get("last_frame") or prompt,
        )
        compiled = build_shot_contract(legacy_source, index=index)
        issues = validate_shot_contract(compiled)
    if issues:
        error = ValueError("工作流镜头合同事实不完整，未提交任何媒体任务")
        error.details = {
            "code": "workflow_shot_contract_facts_invalid",
            "issues": issues,
            "media_submission_started": False,
        }
        raise error
    if isinstance(persisted, dict) and not persisted_issues:
        if _text(persisted.get("contract_hash")) != _text(
            compiled.get("contract_hash")
        ):
            error = ValueError("工作流镜头原始事实与已冻结合同不一致")
            error.details = {
                "code": "workflow_shot_contract_drift",
                "media_submission_started": False,
            }
            raise error
    return compiled


def _node_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(node.get("id")): node
        for node in snapshot.get("nodes", [])
        if isinstance(node, dict) and _text(node.get("id"))
    }


def _patch_signature(patches: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(patches, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:20]


def _safe_output_path(ctx: ProjectContext, value: str) -> tuple[Path, Path] | None:
    path = Path(value)
    if not path.is_absolute():
        path = Path(ctx.output_dir) / path
    if not path.is_file():
        return None
    root = Path(ctx.output_dir).resolve()
    try:
        resolved = path.resolve()
        relative = resolved.relative_to(root)
    except ValueError:
        raise RuntimeError("视频任务产物不在当前项目输出目录内")
    return resolved, relative


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_BUNDLED_FFMPEG_DIR = _PROJECT_ROOT / "runtime" / "ffmpeg"


def _media_binary(name: str) -> str | None:
    """Resolve an ffmpeg-family binary, preferring the bundled one.

    ``shutil.which`` alone breaks whenever the server was started without
    ``runtime\\ffmpeg`` on PATH (the deploy launcher does not set PATH), which
    used to fail every video readback with a misleading "artifact" error.
    ``final_film_qc`` and ``task_backend.runners.dialogue_audio_support`` share
    the same bundled-first convention.
    """

    executable = _BUNDLED_FFMPEG_DIR / f"{name}.exe"
    if executable.is_file():
        return str(executable)
    return shutil.which(name)


def _probe_video_metadata(path: Path) -> dict[str, Any]:
    ffprobe = _media_binary("ffprobe")
    if not ffprobe:
        return {}
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height:format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            return {}
        payload = json.loads(result.stdout or "{}")
        stream = (payload.get("streams") or [{}])[0]
        fmt = payload.get("format") if isinstance(payload.get("format"), dict) else {}
        metadata: dict[str, Any] = {}
        for key in ("width", "height"):
            value = stream.get(key)
            if isinstance(value, int) and value > 0:
                metadata[key] = value
        try:
            duration = float(fmt.get("duration"))
        except (TypeError, ValueError):
            duration = 0.0
        if duration > 0:
            metadata["duration_seconds"] = round(duration, 3)
        return metadata
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _ensure_video_preview_frame(
    ctx: ProjectContext, path: Path, job_id: str
) -> str | None:
    ffmpeg = _media_binary("ffmpeg")
    if not ffmpeg:
        return None
    output_root = Path(ctx.output_dir).resolve()
    preview = output_root / "freezone_video_gen" / f"{job_id}.preview.jpg"
    preview.parent.mkdir(parents=True, exist_ok=True)
    if not preview.is_file():
        temp = preview.with_suffix(".tmp.jpg")
        try:
            result = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "3",
                    str(temp),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0 or not temp.is_file() or temp.stat().st_size <= 0:
                return None
            os.replace(temp, preview)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
    try:
        return preview.relative_to(output_root).as_posix()
    except ValueError:
        return None


def _video_failure_patch(node_id: str, error: str) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "node_data": {
            "isGenerating": False,
            "generationStartedAt": None,
            "generationTaskKey": None,
            "generationTaskType": None,
            "generationTaskJobId": None,
            "generationTaskRefs": None,
            "generationError": error,
            "generationErrorDetails": error,
        },
    }


__all__ = [
    "_text",
    "_requested_delivery_seconds",
    "_compose_duration_mismatch",
    "_video_native_audio_capability",
    "_task_cost_receipt",
    "_deterministic_job_id",
    "resolve_workflow_project_context",
    "_requested_aspect_ratio",
    "_requested_image_size",
    "_compile_snapshot_image_request",
    "_requested_quality",
    "_audio_prompt",
    "_audio_kind",
    "_audio_mode",
    "_voice_reference_receipt",
    "_voice_ref_payload",
    "_requested_video_mode",
    "_requested_video_duration",
    "_requested_video_resolution",
    "_compile_snapshot_video_request",
    "_reference_items",
    "_require_reference_identity_gate",
    "_requested_last_frame",
    "_actual_aspect_ratio",
    "_aspect_ratio_value",
    "_video_completion_node_patch",
    "_normalize_delivery_spec",
    "_delivery_spec_signature",
    "_shot_contract_source",
    "_compile_workflow_shot_contract",
    "_node_map",
    "_patch_signature",
    "_safe_output_path",
    "_probe_video_metadata",
    "_ensure_video_preview_frame",
    "_video_failure_patch",
]
