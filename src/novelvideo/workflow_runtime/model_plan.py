"""Credential-free model snapshots for durable canvas workflow runs."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from novelvideo.generators.direct_model_capabilities import (
    direct_model_capability_summary,
)
from novelvideo.generators.direct_models import (
    DirectModel,
    direct_model_option,
    is_direct_model_runtime_ready,
    resolve_direct_model,
)
from novelvideo.generators.video.direct_models import (
    DirectVideoModel,
    direct_video_model_option,
    list_direct_video_models,
    resolve_direct_video_model,
)


MODEL_PLAN_SCHEMA = "canvas_model_plan_snapshot.v1"
MODEL_PLAN_REVISION = "direct-model-plan.v2"
MODEL_CAPABILITY_PROJECTION_SCHEMA = "canvas.model-capability-projection.v1"
_ROLE_KIND = {
    "director": "agent",
    "text": "text",
    "vision": "vision",
    "image": "image",
    "video": "video",
    "audio": "audio",
    "embedding": "embedding",
}


def _resolve_director_model(requested_ref: str | None) -> DirectModel | None:
    """Resolve the same dedicated Agent registry used by canvas chat/Hermes."""

    explicit = str(requested_ref or "").strip()
    return resolve_direct_model("agent", explicit or None)


class WorkflowModelPlanError(RuntimeError):
    code = "workflow_model_plan_invalid"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        self.details = dict(details or {})
        super().__init__(message)


class WorkflowModelPlanChangedError(WorkflowModelPlanError):
    code = "workflow_model_plan_changed"


def _endpoint_fingerprint(base_url: str) -> str:
    raw = str(base_url or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    normalized = urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            parsed.path,
            parsed.query,
            "",
        )
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _safe_capability(kind: str, model: DirectModel) -> dict[str, Any]:
    from novelvideo.generators.direct_model_capability_cache import (
        get_cached_direct_model_capability,
    )

    cached = get_cached_direct_model_capability(
        base_url=model.base_url,
        kind=kind,
        upstream_model=model.upstream_model,
    )
    metadata = cached.get("modelMetadata")
    payload = direct_model_capability_summary(
        kind,
        model.upstream_model,
        protocol=model.protocol,
        base_url=model.base_url,
        metadata=metadata if isinstance(metadata, dict) else None,
    )
    metadata_record = metadata if isinstance(metadata, dict) else {}
    nested_metadata = metadata_record.get("capabilities")
    metadata_sources = {
        **(dict(nested_metadata) if isinstance(nested_metadata, dict) else {}),
        **metadata_record,
    }

    def values(*keys: str) -> list[Any]:
        for key in keys:
            candidate = payload.get(key)
            if candidate is None:
                candidate = payload.get(
                    next((alias for alias in keys if alias != key), ""),
                )
            if isinstance(candidate, (list, tuple)):
                return list(candidate)
        return []

    def mapping(*keys: str) -> dict[str, Any]:
        for key in keys:
            candidate = payload.get(key)
            if isinstance(candidate, dict):
                return dict(candidate)
        return {}

    def metadata_declares(*keys: str) -> bool:
        return any(key in metadata_sources for key in keys)

    declared: list[str] = []
    if any(
        key in metadata_sources
        for key in (
            "aspectRatioOptions",
            "aspect_ratio_options",
            "supportedAspectRatios",
            "supported_aspect_ratios",
            "aspectRatios",
            "aspect_ratios",
        )
    ):
        declared.append("aspectRatioOptions")
    if any(
        key in metadata_sources
        for key in (
            "resolutionOptions",
            "resolution_options",
            "supportedResolutions",
            "supported_resolutions",
            "supportedSizes",
            "supported_sizes",
            "supportedDimensions",
            "supported_dimensions",
            "sizes",
        )
    ):
        declared.append("resolutionOptions")
    if metadata_declares(
        "supportedModes", "supported_modes", "modes", "generationModes", "generation_modes"
    ):
        declared.append("supportedModes")
    if metadata_declares(
        "qualityOptions", "quality_options", "supportedQualities", "supported_qualities"
    ):
        declared.append("qualityOptions")
    if metadata_declares(
        "sizeOptions", "size_options", "supportedSizes", "supported_sizes"
    ):
        declared.append("sizeOptions")
    if metadata_declares("providerMapping", "provider_mapping", "mapping"):
        declared.append("providerMapping")
    if metadata_declares("voiceOptions", "voice_options"):
        declared.append("voiceOptions")
    if metadata_declares("audioFormats", "audio_formats"):
        declared.append("audioFormats")
    aspect_options = list(
        payload.get("aspectRatioOptions")
        or payload.get("aspect_ratio_options")
        or []
    )
    resolution_options = list(
        payload.get("resolutionOptions")
        or payload.get("resolution_options")
        or []
    )
    quality_options = values("qualityOptions", "quality_options")
    size_options = values("sizeOptions", "size_options")
    supported_modes = values("supportedModes", "supported_modes")
    parameter_defaults = mapping("parameterDefaults", "parameter_defaults")
    input_slots = values("inputSlots", "input_slots")
    reference_limits = mapping("referenceLimits", "reference_limits")
    provider_mapping = mapping("providerMapping", "provider_mapping", "mapping")
    voice_options = values("voiceOptions", "voice_options")
    audio_formats = values("audioFormats", "audio_formats")
    return {
        "capability_revision": str(
            payload.get("capabilityRevision")
            or payload.get("capability_revision")
            or "direct-model-contract.v2"
        ),
        "runtime_ready": is_direct_model_runtime_ready(model),
        "supported_modes": supported_modes,
        "input_slots": input_slots,
        "reference_limits": reference_limits,
        "provider_mapping": provider_mapping,
        "voice_options": voice_options,
        "audio_formats": audio_formats,
        "aspect_ratio_options": aspect_options,
        "resolution_options": resolution_options,
        "quality_options": quality_options,
        "size_options": size_options,
        "parameter_defaults": parameter_defaults,
        "supports_custom_aspect_ratio": bool(
            payload.get("supportsCustomAspectRatio")
            or payload.get("supports_custom_aspect_ratio")
        ),
        "supports_custom_resolution": bool(
            payload.get("supportsCustomResolution")
            or payload.get("supports_custom_resolution")
        ),
        "protocol": str(payload.get("protocol") or model.protocol or ""),
        "effective_protocol": str(payload.get("protocol") or model.protocol or ""),
        "verification_status": str(
            payload.get("verificationStatus")
            or payload.get("verification_status")
            or "unverified"
        ),
        "declared_capabilities": declared,
        "aspect_ratio_parameter_enabled": (
            bool(aspect_options)
            or bool(
                payload.get("supportsCustomAspectRatio")
                or payload.get("supports_custom_aspect_ratio")
            )
            if "aspectRatioOptions" in declared
            else None
        ),
        "resolution_parameter_enabled": (
            bool(resolution_options)
            or bool(
                payload.get("supportsCustomResolution")
                or payload.get("supports_custom_resolution")
            )
            if "resolutionOptions" in declared
            else None
        ),
    }


def _direct_binding(role: str, model: DirectModel) -> dict[str, Any]:
    capability = _safe_capability(model.kind, model)
    return {
        "role": role,
        "kind": model.kind,
        "registry_id": model.registry_id,
        "catalog_id": model.catalog_id,
        "upstream_model": model.upstream_model,
        "protocol": model.protocol,
        "endpoint_fingerprint": _endpoint_fingerprint(model.base_url),
        "capability_revision": capability["capability_revision"],
        "capabilities": capability,
    }


def _video_binding(role: str, model: DirectVideoModel) -> dict[str, Any]:
    option = direct_video_model_option(model)
    # 工作流必须冻结和画布节点相同的能力合同，否则运行中会出现“模型身份
    # 一致但参数不一致”的隐性漂移。只保存能力与协议，不把 endpoint/key 带入快照。
    def option_value(camel: str, snake: str, default: Any = None) -> Any:
        value = option.get(camel)
        if value is None:
            value = option.get(snake)
        return default if value is None else value

    def option_list(camel: str, snake: str) -> list[Any]:
        value = option.get(camel)
        if value is None:
            value = option.get(snake)
        return list(value) if isinstance(value, (list, tuple)) else []

    def option_dict(camel: str, snake: str) -> dict[str, Any]:
        value = option.get(camel)
        if value is None:
            value = option.get(snake)
        return dict(value) if isinstance(value, dict) else {}

    def option_bool_or_none(camel: str, snake: str) -> bool | None:
        value = option.get(camel)
        if value is None:
            value = option.get(snake)
        return value if isinstance(value, bool) else None

    effective_protocol = str(
        option.get("effectiveProtocol")
        or option.get("effective_protocol")
        or model.effective_protocol
        or option.get("protocol")
        or model.protocol
    )
    aspect_ratios = option_list("aspectRatioOptions", "aspect_ratio_options")
    resolutions = option_list("resolutionOptions", "resolution_options")
    declared_capabilities = option_list(
        "declaredCapabilities", "declared_capabilities"
    )
    size_options = option_list("sizeOptions", "size_options")
    duration_declared = any(
        key in option for key in ("durationOptions", "duration_options")
    ) or "durationOptions" in {str(item).strip() for item in declared_capabilities}
    duration_options = option_list("durationOptions", "duration_options")
    duration_options = [
        int(value)
        for value in duration_options
        if type(value) is int and value > 0
    ]
    fps_options = option_list("fpsOptions", "fps_options")
    fps_options = [
        value
        for value in fps_options
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
    ]
    input_slots = option_list("inputSlots", "input_slots")
    return_last_frame = option_bool_or_none("returnLastFrame", "return_last_frame")
    supports_custom_duration = option_bool_or_none(
        "supportsCustomDuration", "supports_custom_duration"
    )
    duration_parameter_enabled = option_bool_or_none(
        "durationParameterEnabled", "duration_parameter_enabled"
    )
    if duration_parameter_enabled is None and duration_declared:
        duration_parameter_enabled = bool(duration_options) or bool(
            supports_custom_duration
        )
    reference_limits = option_dict("referenceLimits", "reference_limits")
    parameter_defaults = option_dict("parameterDefaults", "parameter_defaults")
    parameters = option_list("parameters", "parameters")
    provider_mapping = option_dict("providerMapping", "provider_mapping")
    media_inputs = option_list("mediaInputs", "media_inputs")
    audio_input_semantics = option_list(
        "audioInputSemantics", "audio_input_semantics"
    )
    opaque = option_list("opaque", "opaque")
    return {
        "role": role,
        "kind": "video",
        "registry_id": model.registry_id,
        "catalog_id": model.backend,
        "upstream_model": model.upstream_model,
        "protocol": model.protocol,
        "effective_protocol": effective_protocol,
        "endpoint_fingerprint": _endpoint_fingerprint(model.base_url),
        "capability_revision": str(
            option.get("capabilityRevision")
            or option.get("capability_revision")
            or
            model.capability.catalog_revision or "direct-video-contract.v1"
        ),
        "capability_source": str(
            option.get("capabilitySource")
            or option.get("capability_source")
            or "profile"
        ),
        "capabilities": {
            "capability_source": str(
                option.get("capabilitySource")
                or option.get("capability_source")
                or "profile"
            ),
            "runtime_ready": bool(option.get("runtimeReady")),
            "supported_modes": option_list("supportedModes", "supported_modes"),
            "reference_limits": reference_limits,
            "parameter_defaults": parameter_defaults,
            "aspect_ratio_options": aspect_ratios,
            "resolution_options": resolutions,
            "declared_capabilities": declared_capabilities,
            "aspect_ratio_parameter_enabled": option_bool_or_none(
                "aspectRatioParameterEnabled", "aspect_ratio_parameter_enabled"
            ),
            "resolution_parameter_enabled": option_bool_or_none(
                "resolutionParameterEnabled", "resolution_parameter_enabled"
            ),
            "supports_custom_aspect_ratio": bool(
                option_value(
                    "supportsCustomAspectRatio", "supports_custom_aspect_ratio", False
                )
            ),
            "supports_custom_resolution": bool(
                option_value(
                    "supportsCustomResolution", "supports_custom_resolution", False
                )
            ),
            "size_options": size_options,
            "size_field": str(
                option_value("sizeField", "size_field", "") or ""
            ),
            "native_audio": str(option_value("nativeAudio", "native_audio", "unknown")),
            "min_duration": option_value("minDuration", "min_duration"),
            "max_duration": option_value("maxDuration", "max_duration"),
            # Preserve an explicit empty duration enum.  Falling back with
            # ``or`` here used to resurrect profile durations after discovery
            # had explicitly ruled the field out.
            "duration_options": tuple(
                duration_options
                if duration_declared
                else getattr(model.capability, "duration", ())
            ),
            "fps_options": tuple(fps_options),
            "input_slots": input_slots,
            "return_last_frame": (
                bool(return_last_frame)
                if return_last_frame is not None
                else bool(getattr(getattr(model, "profile", None), "return_last_frame", False))
            ),
            "supports_custom_duration": (
                bool(supports_custom_duration)
                if supports_custom_duration is not None
                else False
            ),
            "duration_parameter_enabled": duration_parameter_enabled,
            "protocol": str(option.get("protocol") or model.protocol),
            "effective_protocol": effective_protocol,
            "family": str(option.get("family") or model.family),
            "catalog_verification": str(
                option.get("catalogVerification")
                or option.get("catalog_verification")
                or "unverified"
            ),
            # 旧工作流读取这两个摘要名，保留别名以兼容已持久化运行记录。
            "aspect_ratios": aspect_ratios,
            "resolutions": resolutions,
            # Preserve the complete discovered capability envelope in the
            # credential-free snapshot. These are evidence and mapping data,
            # not endpoint credentials.
            "parameters": parameters,
            "provider_mapping": provider_mapping,
            "providerMapping": provider_mapping,
            "media_inputs": media_inputs,
            "mediaInputs": media_inputs,
            **(
                {
                    "audio_input_semantics": audio_input_semantics,
                    "audioInputSemantics": audio_input_semantics,
                }
                if any(
                    key in option
                    for key in ("audioInputSemantics", "audio_input_semantics")
                )
                else {}
            ),
            "opaque": opaque,
        },
    }


def build_model_capability_projection(
    *,
    kind: str,
    model_ref: str = "",
    upstream_model: str = "",
    model_label: str = "",
) -> dict[str, Any]:
    """Project one selected model's executable contract without credentials.

    Prompt compilation and WorkflowRun must reason about the same model
    contract.  This projection deliberately reuses the model-center option
    builders instead of creating a second capability registry.  Legacy model
    IDs remain representable as ``unresolved`` evidence so the optimizer can
    explain uncertainty without inventing support.
    """

    normalized_kind = str(kind or "").strip().lower()
    projection: dict[str, Any] = {
        "schema": MODEL_CAPABILITY_PROJECTION_SCHEMA,
        "kind": normalized_kind,
        "requested_model_id": str(model_ref or "").strip(),
        "requested_upstream_model": str(upstream_model or "").strip(),
        "requested_model_label": str(model_label or "").strip(),
        "model_id": "",
        "upstream_model": str(upstream_model or "").strip(),
        "model_label": str(model_label or "").strip(),
        "capability_revision": "",
        "source": "unresolved",
        "verification_status": "unverified",
        "runtime_ready": None,
        "supported_modes": [],
        "input_slots": [],
        "duration_range": [],
        "duration_options": [],
        "fps_options": [],
        "native_audio": "unknown",
        "return_last_frame": None,
        "supports_custom_duration": False,
        "duration_options_declared": False,
        "aspect_ratio_options": [],
        "resolution_options": [],
        "quality_options": [],
        "size_options": [],
        "supports_custom_aspect_ratio": False,
        "supports_custom_resolution": False,
        "reference_limits": {},
        "parameter_defaults": {},
        "effective_protocol": "",
        "declared_capabilities": [],
        "audio_input_semantics": [],
    }

    def present_list(payload: Mapping[str, Any], *keys: str) -> list[Any]:
        """Return the first present list, preserving an explicit empty value."""
        for key in keys:
            if key not in payload:
                continue
            value = payload.get(key)
            return list(value) if isinstance(value, (list, tuple)) else []
        return []

    option: dict[str, Any] | None = None
    if normalized_kind == "video":
        model = resolve_direct_video_model(model_ref)
        if model is not None:
            option = direct_video_model_option(model)
            projection.update(
                {
                    "model_id": model.backend,
                    "upstream_model": model.upstream_model,
                    "model_label": model.label,
                    "capability_revision": str(
                        option.get("capabilityRevision")
                        or option.get("capability_revision")
                        or model.capability.catalog_revision
                        or "direct-video-contract.v1"
                    ),
                    "source": "direct-registry+capability-cache",
                    "verification_status": str(
                        option.get("catalogVerification")
                        or option.get("catalog_verification")
                        or "unverified"
                    ),
                    "runtime_ready": bool(option.get("runtimeReady")),
                    "supported_modes": present_list(
                        option, "supportedModes", "supported_modes"
                    ),
                    "input_slots": present_list(option, "inputSlots", "input_slots"),
                    "duration_range": [
                        option.get("minDuration") or option.get("min_duration"),
                        option.get("maxDuration") or option.get("max_duration"),
                    ],
                    "duration_options": present_list(
                        option, "durationOptions", "duration_options"
                    ),
                    "fps_options": present_list(option, "fpsOptions", "fps_options"),
                    "native_audio": str(
                        option.get("nativeAudio")
                        or option.get("native_audio")
                        or "unknown"
                    ),
                    "return_last_frame": option.get(
                        "returnLastFrame", option.get("return_last_frame")
                    ),
                    "supports_custom_duration": bool(
                        option.get("supportsCustomDuration")
                        or option.get("supports_custom_duration")
                    ),
                    "duration_options_declared": any(
                        key in option for key in ("durationOptions", "duration_options")
                    ),
                    "aspect_ratio_options": present_list(
                        option, "aspectRatioOptions", "aspect_ratio_options"
                    ),
                    "resolution_options": present_list(
                        option,
                        "runtimeResolutionOptions",
                        "runtime_resolution_options",
                        "resolutionOptions",
                        "resolution_options",
                    ),
                    "quality_options": present_list(
                        option, "qualityOptions", "quality_options"
                    ),
                    "size_options": present_list(option, "sizeOptions", "size_options"),
                    "declared_capabilities": present_list(
                        option, "declaredCapabilities", "declared_capabilities"
                    ),
                    "audio_input_semantics": present_list(
                        option, "audioInputSemantics", "audio_input_semantics"
                    ),
                    "supports_custom_aspect_ratio": bool(
                        option.get("supportsCustomAspectRatio")
                        or option.get("supports_custom_aspect_ratio")
                    ),
                    "supports_custom_resolution": bool(
                        option.get("supportsCustomResolution")
                        or option.get("supports_custom_resolution")
                    ),
                    "reference_limits": dict(
                        option.get("referenceLimits")
                        or option.get("reference_limits")
                        or {}
                    ),
                    "parameter_defaults": dict(
                        option.get("parameterDefaults")
                        or option.get("parameter_defaults")
                        or {}
                    ),
                    "effective_protocol": str(
                        option.get("effectiveProtocol")
                        or option.get("effective_protocol")
                        or option.get("protocol")
                        or model.effective_protocol
                    ),
                }
            )
    elif normalized_kind in {"agent", "text", "vision", "image", "audio", "embedding"}:
        model = resolve_direct_model(normalized_kind, model_ref or None)
        if model is not None:
            option = direct_model_option(model)
            projection.update(
                {
                    "model_id": model.catalog_id,
                    "upstream_model": model.upstream_model,
                    "model_label": model.label,
                    "capability_revision": str(
                        option.get("capabilityRevision")
                        or option.get("capability_revision")
                        or "direct-model-contract.v2"
                    ),
                    "source": "direct-registry+capability-cache",
                    "verification_status": str(
                        option.get("catalogVerification")
                        or option.get("catalog_verification")
                        or option.get("verificationStatus")
                        or "unverified"
                    ),
                    "runtime_ready": bool(option.get("runtimeReady")),
                    "supported_modes": present_list(
                        option, "supportedModes", "supported_modes"
                    ),
                    "input_slots": present_list(option, "inputSlots", "input_slots"),
                    "aspect_ratio_options": present_list(
                        option, "aspectRatioOptions", "aspect_ratio_options"
                    ),
                    "resolution_options": present_list(
                        option, "resolutionOptions", "resolution_options"
                    ),
                    "quality_options": present_list(
                        option, "qualityOptions", "quality_options"
                    ),
                    "declared_capabilities": present_list(
                        option, "declaredCapabilities", "declared_capabilities"
                    ),
                    "supports_custom_aspect_ratio": bool(
                        option.get("supportsCustomAspectRatio")
                        or option.get("supports_custom_aspect_ratio")
                    ),
                    "supports_custom_resolution": bool(
                        option.get("supportsCustomResolution")
                        or option.get("supports_custom_resolution")
                    ),
                    "reference_limits": dict(
                        option.get("referenceLimits")
                        or option.get("reference_limits")
                        or {}
                    ),
                    "parameter_defaults": dict(
                        option.get("parameterDefaults")
                        or option.get("parameter_defaults")
                        or {}
                    ),
                    "effective_protocol": str(
                        option.get("protocol") or model.protocol or ""
                    ),
                }
            )

    material = dict(projection)
    material.pop("snapshot_hash", None)
    projection["snapshot_hash"] = hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()[:24]
    return projection


def _default_video_model() -> DirectVideoModel | None:
    models = tuple(
        model
        for model in list_direct_video_models()
        if model.enabled and model.runtime_ready
    )
    return next((model for model in models if model.is_default), None) or next(
        iter(models), None
    )


def build_model_plan_snapshot(
    requested_bindings: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Capture model identities and capabilities without persisting credentials."""

    requested = {
        str(role).strip().lower(): str(value).strip()
        for role, value in (requested_bindings or {}).items()
        if str(role).strip() and str(value).strip()
    }
    unknown_roles = sorted(set(requested) - set(_ROLE_KIND))
    if unknown_roles:
        raise WorkflowModelPlanError(
            "工作流模型方案包含未知角色：" + "、".join(unknown_roles)
        )
    bindings: dict[str, dict[str, Any]] = {}
    for role, kind in _ROLE_KIND.items():
        if role == "director":
            model = _resolve_director_model(requested.get(role) or None)
            if model is not None and is_direct_model_runtime_ready(model):
                bindings[role] = _direct_binding(role, model)
            continue
        if kind == "video":
            explicit = requested.get(role)
            model = resolve_direct_video_model(explicit) if explicit else _default_video_model()
            if model is not None and model.enabled and model.runtime_ready:
                bindings[role] = _video_binding(role, model)
            continue
        model = resolve_direct_model(kind, requested.get(role) or None)
        if model is not None and is_direct_model_runtime_ready(model):
            bindings[role] = _direct_binding(role, model)
    return {
        "schema": MODEL_PLAN_SCHEMA,
        "model_plan_revision": MODEL_PLAN_REVISION,
        "bindings": bindings,
        "missing_roles": [role for role in _ROLE_KIND if role not in bindings],
        "fallback_policy": "explicit-only",
    }


def _ratio_number(value: object) -> float | None:
    text = str(value or "").strip().lower().replace("×", "x")
    separator = "x" if "x" in text else ":"
    left, found, right = text.partition(separator)
    if not found:
        return None
    try:
        width = float(left)
        height = float(right)
    except ValueError:
        return None
    return width / height if width > 0 and height > 0 else None


def _nearest_ratio_option(value: object, options: list[str]) -> str:
    requested = str(value or "").strip()
    if not options:
        return requested
    if requested in options:
        return requested
    target = _ratio_number(requested)
    if target is None:
        return options[0] if options else requested
    candidates = [
        (abs(ratio - target), option)
        for option in options
        if (ratio := _ratio_number(option)) is not None
    ]
    return min(candidates)[1] if candidates else options[0]


_IMAGE_MODE_ALIASES = {
    "texttoimage": "text_to_image",
    "text_to_image": "text_to_image",
    "t2i": "text_to_image",
    "imagetoimage": "image_to_image",
    "image_to_image": "image_to_image",
    "i2i": "image_to_image",
}


def _normalize_image_mode(value: object) -> str:
    token = str(value or "").strip().casefold().replace("-", "_").replace(" ", "")
    return _IMAGE_MODE_ALIASES.get(token, str(value or "").strip())


_VIDEO_MODE_ALIASES = {
    "texttovideo": "textToVideo",
    "text_to_video": "textToVideo",
    "t2v": "textToVideo",
    "imagetovideo": "imageToVideo",
    "image_to_video": "imageToVideo",
    "i2v": "imageToVideo",
    "firstlastframe": "firstLastFrame",
    "first_last_frame": "firstLastFrame",
    "allreference": "allReference",
    "all_reference": "allReference",
    "imagereference": "imageReference",
    "image_reference": "imageReference",
    "videoedit": "videoEdit",
    "video_edit": "videoEdit",
}

_AUDIO_MODE_ALIASES = {
    "texttospeech": "text_to_speech",
    "text_to_speech": "text_to_speech",
    "tts": "text_to_speech",
    "speech": "text_to_speech",
    "texttomusic": "text_to_music",
    "text_to_music": "text_to_music",
    "music": "text_to_music",
    "audiogeneration": "audio_generation",
    "audio_generation": "audio_generation",
}


def _normalize_video_mode(value: object) -> str:
    token = str(value or "").strip().casefold().replace("-", "_").replace(" ", "")
    return _VIDEO_MODE_ALIASES.get(token, str(value or "").strip())


def _normalize_audio_mode(value: object) -> str:
    token = str(value or "").strip().casefold().replace("-", "_").replace(" ", "")
    return _AUDIO_MODE_ALIASES.get(token, str(value or "").strip())


def _reference_counts(
    reference_items: object,
    *,
    last_frame_path: object = "",
) -> dict[str, int]:
    counts = {"image": 0, "video": 0, "audio": 0}
    items = reference_items if isinstance(reference_items, (list, tuple)) else []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        if isinstance(item, Mapping):
            raw_kind = str(item.get("type") or item.get("kind") or "image").strip().casefold()
            identity = str(item.get("path") or item.get("url") or item.get("asset_id") or item.get("assetId") or "").strip()
            role = str(item.get("role") or "").strip().casefold()
        else:
            raw_kind = "image"
            identity = str(item or "").strip()
            role = ""
        kind = (
            "video"
            if raw_kind in {"video", "videos", "motion"}
            else "audio"
            if raw_kind in {"audio", "audios", "voice"}
            else "image"
        )
        dedupe_key = (kind, identity.casefold(), role)
        if identity and dedupe_key not in seen:
            seen.add(dedupe_key)
            counts[kind] += 1
    last_frame = str(last_frame_path or "").strip()
    if last_frame and not any(
        kind == "image" and identity == last_frame.casefold()
        for kind, identity, _role in seen
    ):
        counts["image"] += 1
    return counts


def validate_snapshot_reference_inputs(
    snapshot: dict[str, Any],
    *,
    role: str,
    mode: object,
    reference_items: object,
    last_frame_path: object = "",
) -> dict[str, Any]:
    """Validate actual media inputs against one frozen model binding.

    The returned receipt is safe to persist with the WorkflowRun.  Unknown
    legacy envelopes remain permissive, while declared limits and current v2
    mode semantics are enforced before any provider task is queued.
    """

    normalized_role = str(role or "").strip().casefold()
    bindings = snapshot.get("bindings")
    binding = bindings.get(normalized_role) if isinstance(bindings, dict) else None
    capabilities = binding.get("capabilities") if isinstance(binding, dict) else None
    if not isinstance(capabilities, dict):
        raise WorkflowModelPlanError(f"工作流模型方案缺少 {normalized_role} 能力合同")

    counts = _reference_counts(reference_items, last_frame_path=last_frame_path)
    total = sum(counts.values())
    if normalized_role == "image":
        normalized_mode = _normalize_image_mode(mode)
        mode_limits = capabilities.get("reference_limits")
        mode_limits = dict(mode_limits) if isinstance(mode_limits, Mapping) else {}
        nested = mode_limits.get(normalized_mode)
        if isinstance(nested, Mapping):
            mode_limits = dict(nested)
        max_images = next(
            (
                int(mode_limits[key])
                for key in ("image", "images", "reference_images", "referenceImages")
                if key in mode_limits and type(mode_limits[key]) is int and mode_limits[key] >= 0
            ),
            None,
        )
        input_slots = {
            str(value).strip().casefold()
            for value in (capabilities.get("input_slots") or [])
            if str(value).strip()
        }
        if normalized_mode == "text_to_image" and total:
            raise WorkflowModelPlanError(
                "文生图模式不接受参考素材",
                details={"code": "image_reference_not_allowed", "mode": normalized_mode, "reference_counts": counts},
            )
        if normalized_mode == "image_to_image" and counts["image"] < 1:
            raise WorkflowModelPlanError(
                "图生图模式至少需要一张参考图",
                details={"code": "image_reference_required", "mode": normalized_mode, "reference_counts": counts},
            )
        if normalized_mode == "image_to_image" and input_slots and not input_slots.intersection(
            {"image", "images", "input_image", "input_images", "reference_image", "reference_images"}
        ):
            raise WorkflowModelPlanError(
                "冻结图片能力合同没有参考图输入槽",
                details={"code": "image_reference_slot_missing", "mode": normalized_mode, "input_slots": sorted(input_slots)},
            )
        if max_images is not None and counts["image"] > max_images:
            raise WorkflowModelPlanError(
                "图片参考数量超过冻结能力合同",
                details={"code": "image_reference_limit_exceeded", "mode": normalized_mode, "reference_counts": counts, "reference_limits": {"image": max_images}},
            )
        limits = {"image": max_images} if max_images is not None else {}
    elif normalized_role == "video":
        normalized_mode = _normalize_video_mode(mode)
        all_limits = capabilities.get("reference_limits")
        all_limits = dict(all_limits) if isinstance(all_limits, Mapping) else {}
        raw_limits = all_limits.get(normalized_mode)
        limits = dict(raw_limits) if isinstance(raw_limits, Mapping) else {}
        normalized_limits = {
            kind: next(
                (
                    int(limits[key])
                    for key in (kind, f"{kind}s")
                    if key in limits and type(limits[key]) is int and limits[key] >= 0
                ),
                None,
            )
            for kind in ("image", "video", "audio")
        }
        required = {
            "textToVideo": {},
            "imageToVideo": {"image": 1},
            "firstLastFrame": {"image": 2},
            "allReference": {"any": 1},
            "imageReference": {"image": 1},
            "videoEdit": {"video": 1},
        }.get(normalized_mode, {})
        if normalized_mode == "textToVideo" and total:
            raise WorkflowModelPlanError(
                "文生视频模式不接受参考素材",
                details={"code": "video_reference_not_allowed", "mode": normalized_mode, "reference_counts": counts},
            )
        if required.get("any") and total < int(required["any"]):
            raise WorkflowModelPlanError(
                "当前视频模式至少需要一个参考素材",
                details={"code": "video_reference_required", "mode": normalized_mode, "reference_counts": counts},
            )
        for kind in ("image", "video", "audio"):
            minimum = int(required.get(kind) or 0)
            if counts[kind] < minimum:
                raise WorkflowModelPlanError(
                    "当前视频模式缺少必需参考输入",
                    details={"code": "video_reference_kind_required", "mode": normalized_mode, "kind": kind, "minimum": minimum, "reference_counts": counts},
                )
            maximum = normalized_limits[kind]
            if maximum is not None and counts[kind] > maximum:
                raise WorkflowModelPlanError(
                    "视频参考数量超过冻结能力合同",
                    details={"code": "video_reference_limit_exceeded", "mode": normalized_mode, "kind": kind, "reference_counts": counts, "reference_limits": {key: value for key, value in normalized_limits.items() if value is not None}},
                )
        limits = {key: value for key, value in normalized_limits.items() if value is not None}
    elif normalized_role == "audio":
        normalized_mode = _normalize_audio_mode(mode)
        supported_modes = {
            _normalize_audio_mode(value)
            for value in (capabilities.get("supported_modes") or [])
            if str(value).strip()
        }
        if supported_modes and normalized_mode not in supported_modes:
            raise WorkflowModelPlanError(
                "冻结音频能力合同不支持当前生成模式",
                details={
                    "code": "audio_mode_not_supported",
                    "mode": normalized_mode,
                    "supported_modes": sorted(supported_modes),
                },
            )
        all_limits = capabilities.get("reference_limits")
        all_limits = dict(all_limits) if isinstance(all_limits, Mapping) else {}
        raw_limits = all_limits.get(normalized_mode)
        limits = dict(raw_limits) if isinstance(raw_limits, Mapping) else all_limits
        max_audio = next(
            (
                int(limits[key])
                for key in ("audio", "audios", "reference_audio", "reference_audios")
                if key in limits and type(limits[key]) is int and limits[key] >= 0
            ),
            None,
        )
        input_slots = {
            str(value).strip().casefold()
            for value in (capabilities.get("input_slots") or [])
            if str(value).strip()
        }
        if normalized_mode == "text_to_music" and total:
            raise WorkflowModelPlanError(
                "文生音乐模式不接受参考素材",
                details={
                    "code": "audio_reference_not_allowed",
                    "mode": normalized_mode,
                    "reference_counts": counts,
                },
            )
        if counts["image"] or counts["video"]:
            raise WorkflowModelPlanError(
                "音频模型只接受音频类参考素材",
                details={
                    "code": "audio_reference_kind_invalid",
                    "mode": normalized_mode,
                    "reference_counts": counts,
                },
            )
        if counts["audio"] and input_slots and not input_slots.intersection(
            {"audio", "audios", "voice", "voice_reference", "reference_audio"}
        ):
            raise WorkflowModelPlanError(
                "冻结音频能力合同没有声线或音频参考输入槽",
                details={
                    "code": "audio_reference_slot_missing",
                    "mode": normalized_mode,
                    "input_slots": sorted(input_slots),
                },
            )
        if max_audio is not None and counts["audio"] > max_audio:
            raise WorkflowModelPlanError(
                "音频参考数量超过冻结能力合同",
                details={
                    "code": "audio_reference_limit_exceeded",
                    "mode": normalized_mode,
                    "reference_counts": counts,
                    "reference_limits": {"audio": max_audio},
                },
            )
        limits = {"audio": max_audio} if max_audio is not None else {}
    else:
        raise WorkflowModelPlanError(f"不支持的媒体能力角色：{normalized_role}")

    return {
        "schema": "model_reference_input_receipt.v1",
        "role": normalized_role,
        "mode": normalized_mode,
        "counts": counts,
        "limits": limits,
        "passed": True,
    }


def compile_snapshot_image_parameters(
    snapshot: dict[str, Any],
    requested: dict[str, Any] | None = None,
    *,
    strict_explicit: bool = False,
    explicit_fields: Collection[str] | None = None,
) -> dict[str, Any]:
    """Compile one image request from the same frozen contract as the UI.

    Image and video nodes must not have separate capability semantics.  This
    compiler keeps the image-specific transport names while enforcing mode,
    ratio, resolution, and quality against the selected snapshot.
    """

    bindings = snapshot.get("bindings")
    binding = bindings.get("image") if isinstance(bindings, dict) else None
    capabilities = binding.get("capabilities") if isinstance(binding, dict) else None
    if not isinstance(capabilities, dict):
        raise WorkflowModelPlanError("工作流模型方案缺少图片能力合同")
    values = dict(requested or {})
    explicit_field_set = {
        str(field).strip()
        for field in (explicit_fields or ())
        if str(field).strip()
    }

    def strict_for(*fields: str) -> bool:
        return strict_explicit or bool(explicit_field_set.intersection(fields))

    defaults = capabilities.get("parameter_defaults")
    defaults = dict(defaults) if isinstance(defaults, dict) else {}
    declared_capabilities = {
        str(value).strip()
        for value in (capabilities.get("declared_capabilities") or [])
        if str(value).strip()
    }
    supported_modes = [
        _normalize_image_mode(value)
        for value in (capabilities.get("supported_modes") or [])
        if str(value).strip()
    ]
    supported_modes = list(dict.fromkeys(supported_modes))
    mode_declared = bool(supported_modes) or "supportedModes" in declared_capabilities
    requested_mode = _normalize_image_mode(
        values.get("mode") or values.get("gen_mode")
    )
    mode_explicit = "mode" in values or "gen_mode" in values
    reference_count = int(values.get("reference_count") or 0)
    if not requested_mode:
        requested_mode = "image_to_image" if reference_count else "text_to_image"
    if mode_declared and not supported_modes:
        raise WorkflowModelPlanError(
            "工作流图片模型没有可用的生成模式",
            details={
                "code": "image_modes_unavailable",
                "requested_mode": requested_mode,
                "supported_modes": [],
            },
        )
    if (
        strict_for("mode", "gen_mode")
        and mode_explicit
        and requested_mode
        and mode_declared
        and requested_mode not in supported_modes
    ):
        raise WorkflowModelPlanError(
            "工作流图片模式不在冻结能力合同中",
            details={
                "code": "unsupported_image_mode",
                "requested_mode": requested_mode,
                "supported_modes": supported_modes,
            },
        )
    mode = (
        requested_mode
        if requested_mode in supported_modes or not mode_declared
        else supported_modes[0]
    )

    aspect_source = capabilities.get("aspect_ratio_options")
    aspect_declared = aspect_source is not None
    aspect_options = [
        str(value).strip()
        for value in (aspect_source if isinstance(aspect_source, (list, tuple)) else [])
        if str(value).strip()
    ]
    aspect_enabled = capabilities.get("aspect_ratio_parameter_enabled")
    aspect_enabled = aspect_enabled if isinstance(aspect_enabled, bool) else None
    requested_aspect = str(
        values.get("aspect_ratio")
        or values.get("ratio")
        or defaults.get("aspectRatio")
        or defaults.get("aspect_ratio")
        or (aspect_options[0] if aspect_options else "" if aspect_declared else "16:9")
    ).strip()
    aspect_explicit = any(key in values for key in ("aspect_ratio", "ratio"))
    custom_aspect = bool(
        capabilities.get("supports_custom_aspect_ratio")
        and _ratio_number(requested_aspect) is not None
    )
    if aspect_enabled is False and strict_for("aspect_ratio", "ratio") and aspect_explicit and requested_aspect:
        raise WorkflowModelPlanError(
            "工作流图片模型不支持比例参数",
            details={"code": "unsupported_image_aspect_ratio", "requested_aspect_ratio": requested_aspect, "supported_aspect_ratios": []},
        )
    if strict_for("aspect_ratio", "ratio") and aspect_explicit and aspect_options and requested_aspect not in aspect_options and not custom_aspect:
        raise WorkflowModelPlanError(
            "工作流图片比例不在冻结能力合同中",
            details={"code": "unsupported_image_aspect_ratio", "requested_aspect_ratio": requested_aspect, "supported_aspect_ratios": aspect_options},
        )
    aspect_ratio = "" if aspect_enabled is False else requested_aspect if custom_aspect else _nearest_ratio_option(requested_aspect, aspect_options)

    resolution_source = capabilities.get("resolution_options")
    resolution_declared = resolution_source is not None
    resolution_options = [
        str(value).strip()
        for value in (resolution_source if isinstance(resolution_source, (list, tuple)) else [])
        if str(value).strip()
    ]
    resolution_enabled = capabilities.get("resolution_parameter_enabled")
    resolution_enabled = resolution_enabled if isinstance(resolution_enabled, bool) else None
    default_resolution = str(
        defaults.get("resolution")
        or (resolution_options[0] if resolution_options else "" if resolution_declared else "2K")
    ).strip()
    requested_resolution = str(values.get("image_size") or values.get("resolution") or default_resolution).strip()
    resolution_explicit = any(key in values for key in ("image_size", "resolution"))
    resolution_keys = {item.casefold().replace("×", "x") for item in resolution_options}
    requested_resolution_key = requested_resolution.casefold().replace("×", "x")
    custom_resolution = bool(
        capabilities.get("supports_custom_resolution")
        and bool(re.fullmatch(r"(?:[1-9]\d{0,2}k|[1-9]\d{2,5}x[1-9]\d{2,5}|[1-9]\d{2,5})", requested_resolution.replace("×", "x"), re.IGNORECASE))
    )
    if resolution_enabled is False and strict_for("image_size", "resolution") and resolution_explicit and requested_resolution:
        raise WorkflowModelPlanError(
            "工作流图片模型不支持清晰度参数",
            details={"code": "unsupported_image_resolution", "requested_resolution": requested_resolution, "supported_resolutions": []},
        )
    if strict_for("image_size", "resolution") and resolution_explicit and resolution_options and requested_resolution_key not in resolution_keys and not custom_resolution:
        raise WorkflowModelPlanError(
            "工作流图片清晰度不在冻结能力合同中",
            details={"code": "unsupported_image_resolution", "requested_resolution": requested_resolution, "supported_resolutions": resolution_options},
        )
    image_size = (
        ""
        if resolution_enabled is False
        else requested_resolution.replace("×", "x")
        if custom_resolution or requested_resolution_key in resolution_keys or (not resolution_options and not resolution_declared)
        else default_resolution if default_resolution in resolution_options else resolution_options[0]
    )

    quality_options = [
        str(value).strip()
        for value in (capabilities.get("quality_options") or [])
        if str(value).strip()
    ]
    quality_declared = bool(quality_options) or "qualityOptions" in declared_capabilities
    quality = str(
        values.get("quality")
        or defaults.get("quality")
        or (quality_options[0] if quality_options else "" if quality_declared else "medium")
    ).strip()
    if (
        strict_for("quality")
        and "quality" in values
        and quality_declared
        and quality.casefold() not in {item.casefold() for item in quality_options}
    ):
        raise WorkflowModelPlanError(
            "工作流图片质量不在冻结能力合同中",
            details={"code": "unsupported_image_quality", "requested_quality": quality, "supported_qualities": quality_options},
        )
    return {
        "mode": mode,
        "aspect_ratio": aspect_ratio,
        "image_size": image_size,
        "quality": quality,
    }


def compile_snapshot_video_parameters(
    snapshot: dict[str, Any],
    requested: dict[str, Any] | None = None,
    *,
    strict_explicit: bool = False,
    explicit_fields: Collection[str] | None = None,
) -> dict[str, Any]:
    """Compile one workflow video request against its frozen model contract.

    Defaults may be selected by the frozen contract. When ``strict_explicit``
    is enabled, values explicitly supplied by the caller are never silently
    changed to a nearby supported value; they produce a structured preflight
    error instead. ``explicit_fields`` is the legacy-compatible form used by
    production runs where some values come from a saved project default while
    others came directly from the current user request.
    """

    bindings = snapshot.get("bindings")
    binding = bindings.get("video") if isinstance(bindings, dict) else None
    capabilities = binding.get("capabilities") if isinstance(binding, dict) else None
    if not isinstance(capabilities, dict):
        raise WorkflowModelPlanError("工作流模型方案缺少视频能力合同")
    model_id = str(
        binding.get("model_id")
        or binding.get("catalog_id")
        or binding.get("upstream_model")
        or ""
    )
    capability_revision = str(binding.get("capability_revision") or "")
    capability_source = str(
        binding.get("capability_source")
        or capabilities.get("capability_source")
        or ""
    )

    def contract_error(message: str, **details: Any) -> None:
        raise WorkflowModelPlanError(
            message,
            details={
                **details,
                "model_id": model_id,
                "capability_revision": capability_revision,
                "capability_source": capability_source,
            },
        )

    values = dict(requested or {})
    explicit_field_set = {
        str(field).strip()
        for field in (explicit_fields or ())
        if str(field).strip()
    }

    def strict_for(*fields: str) -> bool:
        return strict_explicit or bool(explicit_field_set.intersection(fields))

    defaults = capabilities.get("parameter_defaults")
    defaults = dict(defaults) if isinstance(defaults, dict) else {}
    declared_capabilities = {
        str(value).strip()
        for value in (capabilities.get("declared_capabilities") or [])
        if str(value).strip()
    }
    supported_modes = [
        str(value)
        for value in (capabilities.get("supported_modes") or [])
        if str(value).strip()
    ]
    semantics_declared = any(
        key in capabilities
        for key in ("audio_input_semantics", "audioInputSemantics")
    )
    capability_audio_semantics = capabilities.get("audio_input_semantics")
    if not isinstance(capability_audio_semantics, (list, tuple)):
        capability_audio_semantics = capabilities.get("audioInputSemantics")
    if semantics_declared:
        from novelvideo.audio.video_reference_policy import (
            capability_audio_reference_limit,
            normalize_audio_input_semantics,
        )

        audio_input_semantics = list(
            normalize_audio_input_semantics(capability_audio_semantics)
        )
    else:
        audio_input_semantics = []
    mode_declared = bool(supported_modes) or "modes" in declared_capabilities or "supportedModes" in declared_capabilities
    requested_mode = str(values.get("mode") or values.get("gen_mode") or "").strip()
    mode_explicit = "mode" in values or "gen_mode" in values
    if mode_declared and not supported_modes:
        contract_error(
            "工作流视频模型没有可用的生成模式",
            code="video_modes_unavailable",
            requested_mode=requested_mode,
            supported_modes=[],
        )
    if strict_for("mode", "gen_mode") and mode_explicit and requested_mode and mode_declared and requested_mode not in supported_modes:
        contract_error(
            "工作流视频模式不在冻结能力合同中",
            code="unsupported_video_mode",
            requested_mode=requested_mode,
            supported_modes=supported_modes,
        )
    mode = (
        requested_mode
        if requested_mode in supported_modes
        else supported_modes[0]
        if supported_modes
        else requested_mode
    )
    reference_limits = capabilities.get("reference_limits")
    if not isinstance(reference_limits, Mapping):
        reference_limits = {}
    if audio_input_semantics and not strict_for("mode", "gen_mode"):
        current_audio_limit = capability_audio_reference_limit(
            {"reference_limits": reference_limits},
            mode=mode,
        )
        if current_audio_limit <= 0:
            audio_mode = next(
                (
                    candidate
                    for candidate in ("allReference", "imageToVideo", "imageReference")
                    if candidate in supported_modes
                    and capability_audio_reference_limit(
                        {"reference_limits": reference_limits},
                        mode=candidate,
                    )
                    > 0
                ),
                "",
            )
            if audio_mode:
                mode = audio_mode
    reference_audio_limit = (
        capability_audio_reference_limit(
            {"reference_limits": reference_limits},
            mode=mode,
        )
        if semantics_declared
        else 0
    )

    aspect_source = capabilities.get("aspect_ratio_options")
    if aspect_source is None:
        aspect_source = capabilities.get("aspect_ratios")
    aspect_declared = aspect_source is not None
    aspect_options = [
        str(value)
        for value in (
            aspect_source if isinstance(aspect_source, (list, tuple)) else []
        )
        if str(value).strip()
    ]
    aspect_parameter_enabled = capabilities.get("aspect_ratio_parameter_enabled")
    aspect_parameter_enabled = (
        aspect_parameter_enabled if isinstance(aspect_parameter_enabled, bool) else None
    )
    requested_aspect = (
        values.get("aspect_ratio")
        or values.get("ratio")
        or defaults.get("aspectRatio")
        or defaults.get("aspect_ratio")
        or (
            aspect_options[0]
            if aspect_options
            else ""
            if aspect_declared
            else "16:9"
        )
    )
    aspect_explicit = any(key in values for key in ("aspect_ratio", "ratio"))
    supports_custom_aspect = bool(capabilities.get("supports_custom_aspect_ratio"))
    custom_aspect = bool(
        supports_custom_aspect
        and ":" in str(requested_aspect)
        and _ratio_number(requested_aspect) is not None
    )
    if (
        aspect_parameter_enabled is False
        and strict_for("aspect_ratio", "ratio")
        and aspect_explicit
        and str(requested_aspect).strip()
    ):
        contract_error(
            "工作流视频模型不支持比例参数",
            code="unsupported_aspect_ratio",
            requested_aspect_ratio=str(requested_aspect),
            supported_aspect_ratios=[],
        )
    if (
        strict_for("aspect_ratio", "ratio")
        and aspect_explicit
        and aspect_options
        and str(requested_aspect).strip() not in aspect_options
        and not custom_aspect
    ):
        contract_error(
            "工作流视频比例不在冻结能力合同中",
            code="unsupported_aspect_ratio",
            requested_aspect_ratio=str(requested_aspect),
            supported_aspect_ratios=aspect_options,
        )
    aspect_ratio = (
        ""
        if aspect_parameter_enabled is False
        else str(requested_aspect).strip()
        if custom_aspect
        else _nearest_ratio_option(requested_aspect, aspect_options)
    )

    resolution_source = capabilities.get("resolution_options")
    if resolution_source is None:
        resolution_source = capabilities.get("resolutions")
    resolution_declared = resolution_source is not None
    resolution_options = [
        str(value)
        for value in (
            resolution_source if isinstance(resolution_source, (list, tuple)) else []
        )
        if str(value).strip()
    ]
    resolution_parameter_enabled = capabilities.get("resolution_parameter_enabled")
    resolution_parameter_enabled = (
        resolution_parameter_enabled
        if isinstance(resolution_parameter_enabled, bool)
        else None
    )
    default_resolution = str(
        defaults.get("resolution")
        or (
            resolution_options[0]
            if resolution_options
            else ""
            if resolution_declared
            else "720p"
        )
    )
    requested_resolution = str(values.get("resolution") or default_resolution).strip()
    requested_resolution_key = requested_resolution.casefold().replace("×", "x")
    resolution_keys = {
        str(option).strip().casefold().replace("×", "x") for option in resolution_options
    }
    resolution_explicit = "resolution" in values
    supports_custom_resolution = bool(capabilities.get("supports_custom_resolution"))
    custom_resolution = bool(
        supports_custom_resolution
        and re.fullmatch(
            r"(?:[1-9]\d{2,5}p|[1-9]\d{0,2}k|[1-9]\d{2,5}x[1-9]\d{2,5})",
            requested_resolution.strip().lower().replace("×", "x"),
            re.IGNORECASE,
        )
    )
    if (
        resolution_parameter_enabled is False
        and strict_for("resolution")
        and resolution_explicit
        and requested_resolution
    ):
        contract_error(
            "工作流视频模型不支持清晰度参数",
            code="unsupported_resolution",
            requested_resolution=requested_resolution,
            supported_resolutions=[],
        )
    if (
        strict_for("resolution")
        and resolution_explicit
        and resolution_options
        and requested_resolution_key not in resolution_keys
        and not custom_resolution
    ):
        contract_error(
            "工作流视频清晰度不在冻结能力合同中",
            code="unsupported_resolution",
            requested_resolution=requested_resolution,
            supported_resolutions=resolution_options,
        )
    resolution = (
        ""
        if resolution_parameter_enabled is False
        else requested_resolution.replace("×", "x")
        if custom_resolution
        or (not resolution_options and resolution_parameter_enabled is True)
        or (not resolution_options and not resolution_declared)
        or requested_resolution_key in resolution_keys
        else default_resolution
        if default_resolution in resolution_options
        else resolution_options[0]
    )

    duration_value = (
        values.get("duration_seconds")
        or values.get("duration")
        or defaults.get("durationSeconds")
        or defaults.get("duration_seconds")
        or capabilities.get("min_duration")
        or 5
    )
    try:
        duration_seconds = int(round(float(duration_value)))
    except (TypeError, ValueError):
        duration_seconds = 5
    duration_explicit = any(key in values for key in ("duration_seconds", "duration"))
    duration_options = tuple(
        int(value)
        for value in (capabilities.get("duration_options") or ())
        if type(value) is int and value > 0
    )
    duration_parameter_enabled = capabilities.get("duration_parameter_enabled")
    if duration_parameter_enabled is False:
        if strict_for("duration_seconds", "duration") and duration_explicit:
            contract_error(
                "工作流视频模型不支持时长参数",
                code="unsupported_duration",
                requested_duration_seconds=duration_seconds,
                supported_durations=[],
            )
        duration_seconds: int | None = None
    if strict_for("duration_seconds", "duration") and duration_explicit:
        supported_duration = duration_options or tuple(
            value
            for value in (
                capabilities.get("min_duration"),
                capabilities.get("max_duration"),
            )
            if type(value) is int and value > 0
        )
        minimum = capabilities.get("min_duration")
        maximum = capabilities.get("max_duration")
        invalid_duration = (
            duration_seconds not in duration_options
            if duration_options
            else (
                (type(minimum) is int and duration_seconds < minimum)
                or (type(maximum) is int and duration_seconds > maximum)
            )
        )
        if invalid_duration:
            contract_error(
                "工作流视频时长不在冻结能力合同中",
                code="unsupported_duration",
                requested_duration_seconds=duration_seconds,
                supported_durations=list(supported_duration),
            )
    minimum = capabilities.get("min_duration")
    maximum = capabilities.get("max_duration")
    if duration_seconds is not None:
        if isinstance(minimum, (int, float)) and not isinstance(minimum, bool):
            duration_seconds = max(duration_seconds, int(minimum))
        if isinstance(maximum, (int, float)) and not isinstance(maximum, bool):
            duration_seconds = min(duration_seconds, int(maximum))

    native_audio = str(capabilities.get("native_audio") or "unknown")
    requested_audio = values.get("generate_audio")
    audio_explicit = "generate_audio" in values
    if requested_audio is None:
        requested_audio = True
    generate_audio = (
        True
        if native_audio == "required"
        else False
        if native_audio == "unsupported"
        else bool(requested_audio)
    )
    if strict_for("generate_audio") and audio_explicit:
        if native_audio == "unsupported" and bool(requested_audio):
            contract_error(
                "工作流视频模型不支持原生音频",
                code="native_audio_unsupported",
            )
        if native_audio == "required" and not bool(requested_audio):
            contract_error(
                "工作流视频模型要求开启原生音频",
                code="native_audio_required",
            )

    size_options = [
        str(value)
        for value in (capabilities.get("size_options") or [])
        if str(value).strip()
    ]
    size_declared = bool(size_options) or "sizeSlots" in declared_capabilities or "sizeOptions" in declared_capabilities
    requested_size = values.get("size") or values.get("video_size") or defaults.get("size")
    size_explicit = "size" in values or "video_size" in values
    size = str(requested_size or "").strip()
    if size_options:
        if size and strict_for("size", "video_size") and size_explicit and size not in size_options:
            contract_error(
                "工作流视频尺寸不在冻结能力合同中",
                code="unsupported_size",
                requested_size=size,
                supported_sizes=size_options,
            )
        size = size if size in size_options else _nearest_ratio_option(aspect_ratio, size_options)
    elif size and size_explicit and size_declared and strict_for("size", "video_size"):
        contract_error(
            "工作流视频模型不支持固定尺寸参数",
            code="unsupported_size",
            requested_size=size,
            supported_sizes=[],
        )
    elif not size_explicit or size_declared:
        size = ""
    raw_parameters = values.get("parameters")
    parameters = dict(raw_parameters) if isinstance(raw_parameters, Mapping) else {}
    capability_mapping = capabilities.get("provider_mapping")
    if not isinstance(capability_mapping, Mapping):
        capability_mapping = capabilities.get("providerMapping")
    provider_mapping = dict(capability_mapping) if isinstance(capability_mapping, Mapping) else {}
    capability_opaque = capabilities.get("opaque")
    opaque = list(capability_opaque) if isinstance(capability_opaque, (list, tuple)) else []
    capability_media_inputs = capabilities.get("media_inputs")
    if not isinstance(capability_media_inputs, (list, tuple)):
        capability_media_inputs = capabilities.get("mediaInputs")
    media_inputs = list(capability_media_inputs) if isinstance(capability_media_inputs, (list, tuple)) else []
    compiled = {
        "mode": mode,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "generate_audio": generate_audio,
        "size": size,
        "size_field": str(capabilities.get("size_field") or ""),
        "effective_protocol": str(
            capabilities.get("effective_protocol")
            or binding.get("effective_protocol")
            or binding.get("protocol")
            or ""
        ),
    }
    if duration_seconds is not None:
        compiled["duration_seconds"] = duration_seconds
    # Keep the legacy compiler shape stable when a model has no advanced
    # envelope. Add the lossless fields only when the snapshot/request carries
    # evidence or values that downstream transport must preserve.
    if parameters:
        compiled["parameters"] = parameters
    if provider_mapping:
        compiled["provider_mapping"] = provider_mapping
        compiled["providerMapping"] = provider_mapping
    if opaque:
        compiled["opaque"] = opaque
    if media_inputs:
        compiled["media_inputs"] = media_inputs
    if semantics_declared:
        compiled["audio_input_semantics"] = audio_input_semantics
        compiled["reference_audio_limit"] = reference_audio_limit
    return compiled


def resolve_snapshot_model_ref(
    snapshot: dict[str, Any],
    role: str,
) -> tuple[str, str]:
    """Resolve one frozen binding and reject identity drift before model use."""

    bindings = snapshot.get("bindings")
    binding = bindings.get(role) if isinstance(bindings, dict) else None
    if not isinstance(binding, dict):
        raise WorkflowModelPlanError(f"工作流模型方案缺少 {role} 绑定")
    kind = str(binding.get("kind") or "").strip().lower()
    registry_id = str(binding.get("registry_id") or "").strip().lower()
    if not kind or not registry_id:
        raise WorkflowModelPlanError(f"工作流模型方案的 {role} 绑定不完整")

    if kind == "video":
        current = resolve_direct_video_model(f"direct_{registry_id}")
        current_ref = f"direct_{registry_id}"
    else:
        current = resolve_direct_model(kind, f"direct/{registry_id}")
        current_ref = f"direct/{registry_id}"
    current_ready = (
        current is not None
        and current.enabled
        and (
            current.runtime_ready
            if kind == "video"
            else is_direct_model_runtime_ready(current)
        )
    )
    if not current_ready:
        raise WorkflowModelPlanChangedError(
            f"工作流绑定的 {role} 模型已停用或删除：{registry_id}"
        )
    current_effective_protocol = (
        current.effective_protocol if kind == "video" else current.protocol
    )
    expected_effective_protocol = str(
        binding.get("effective_protocol") or binding.get("protocol") or ""
    )
    if (
        str(current.upstream_model) != str(binding.get("upstream_model") or "")
        or str(current.protocol) != str(binding.get("protocol") or "")
        or str(current_effective_protocol) != expected_effective_protocol
        or _endpoint_fingerprint(current.base_url)
        != str(binding.get("endpoint_fingerprint") or "")
    ):
        raise WorkflowModelPlanChangedError(
            f"工作流绑定的 {role} 模型配置已变化，请新建运行或恢复原配置"
        )
    return kind, current_ref


__all__ = [
    "MODEL_CAPABILITY_PROJECTION_SCHEMA",
    "MODEL_PLAN_REVISION",
    "MODEL_PLAN_SCHEMA",
    "WorkflowModelPlanChangedError",
    "WorkflowModelPlanError",
    "build_model_plan_snapshot",
    "build_model_capability_projection",
    "compile_snapshot_image_parameters",
    "compile_snapshot_video_parameters",
    "resolve_snapshot_model_ref",
    "validate_snapshot_reference_inputs",
]
