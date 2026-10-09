"""Credential-free capability contract for all direct canvas models.

The Village Canvas model center stores friendly labels and OpenAI-compatible
endpoints for every model family.  This module is the single browser-safe place
that describes what each configured family can do; it performs no HTTP calls
and reads no settings or credentials.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from novelvideo.generators.direct_image_capabilities import direct_image_capability_summary
from novelvideo.generators.model_contracts import (
    DIRECT_MODEL_PROTOCOLS,
    DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES,
    DIRECT_MODEL_PROTOCOL_AUTO,
    DIRECT_MODEL_PROTOCOL_CUSTOM_HTTP,
    DIRECT_MODEL_PROTOCOL_GEMINI,
    DIRECT_MODEL_PROTOCOL_GEMINI_IMAGE,
    DIRECT_MODEL_PROTOCOL_OLLAMA_OPENAI,
    DIRECT_MODEL_PROTOCOL_OPENAI_AUDIO,
    DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI,
    DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE,
    DIRECT_MODEL_PROTOCOL_OPENAI_EMBEDDINGS,
    DIRECT_MODEL_PROTOCOL_OPENAI_IMAGES,
    get_model_contract,
)


DIRECT_MODEL_KIND_ORDER = (
    "agent",
    "text",
    "vision",
    "image",
    "video",
    "embedding",
    "audio",
)

OPENAI_RUNTIME_PROTOCOLS = frozenset({
    DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE,
    DIRECT_MODEL_PROTOCOL_OLLAMA_OPENAI,
    "openai-chat",
    "openai-chat-vision",
})


def _metadata_record(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Flatten the safe nested capability envelope without trusting extras."""

    if not isinstance(metadata, Mapping):
        return {}
    nested = metadata.get("capabilities")
    result = dict(metadata)
    if isinstance(nested, Mapping):
        result = {**dict(nested), **result}
    return result


def _metadata_values(metadata: Mapping[str, Any], *keys: str) -> tuple[str, ...]:
    for key in keys:
        if key not in metadata:
            continue
        value = metadata.get(key)
        if isinstance(value, str):
            values = (value.strip(),) if value.strip() else ()
        elif isinstance(value, Mapping):
            values = tuple(
                str(item).strip()
                for item, enabled in value.items()
                if enabled and str(item).strip()
            )
        elif isinstance(value, (list, tuple, set)):
            values = tuple(str(item).strip() for item in value if str(item).strip())
        else:
            continue
        if values:
            return tuple(dict.fromkeys(values))
        # Preserve an explicit empty declaration instead of falling through
        # to a stale alias or model-family profile.
        return ()
    return ()


def _metadata_declares(metadata: Mapping[str, Any], *keys: str) -> bool:
    """Return whether the upstream sent a field, including an explicit [].

    Presence matters for capability contracts: an empty enum is a real
    declaration that the feature is unavailable, not permission to restore a
    model-name default.
    """

    return any(key in metadata for key in keys)


def _metadata_positive_int(metadata: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = metadata.get(key)
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None


def normalize_direct_model_base_url(base_url: str) -> str:
    """Complete a provider root to the most likely versioned model API root."""

    normalized = str(base_url or "").strip().rstrip("/")
    if not normalized:
        return ""
    parsed = urlsplit(normalized)
    host = parsed.netloc.lower()
    if host == "autodl.art" or host.endswith(".autodl.art"):
        # AutoDL exposes workflow routes below /api, not an OpenAI /v1 root.
        return normalized
    if not parsed.path and "generativelanguage.googleapis.com" in host:
        return f"{normalized}/v1beta"
    if not parsed.path:
        return f"{normalized}/v1"
    return normalized


def _is_official_gemini_endpoint(endpoint_fingerprint: str) -> bool:
    return (
        "generativelanguage.googleapis.com" in endpoint_fingerprint
        or "googleapis.com" in endpoint_fingerprint
    )


def _is_openai_compatible_gateway(
    endpoint_fingerprint: str,
    path: str,
) -> bool:
    return (
        path.endswith("/v1")
        or "/v1/" in path
        or any(
            token in endpoint_fingerprint
            for token in (
                "aiwble",
                "openai",
                "openrouter",
                "newapi",
                "oneapi",
                "opencode",
                "wokey",
                "siliconflow",
                "deepseek",
            )
        )
    )

_DIRECT_KIND_CAPABILITIES: dict[str, dict[str, Any]] = {
    "chat": {
        "protocol": DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE,
        "supportedModes": ["chat", "structured_output", "prompt_optimization", "toolUse"],
        "useCase": "已识别：对话、提示词、脚本与工具调度",
        "parameterDefaults": {"temperature": 0.7, "timeoutSeconds": 120, "strategy": "balanced"},
    },
    "agent": {
        "protocol": DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE,
        "supportedModes": ["agent", "planning", "toolUse", "canvasAutomation"],
        "useCase": "已识别：搭子主脑、规划、工具调度",
        "parameterDefaults": {"temperature": 0.4, "timeoutSeconds": 120, "strategy": "balanced"},
    },
    "text": {
        "protocol": DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE,
        "supportedModes": ["chat", "structured_output", "prompt_optimization"],
        "useCase": "已识别：提示词、脚本、文本推理",
        "parameterDefaults": {"temperature": 0.7, "timeoutSeconds": 120, "strategy": "balanced"},
    },
    "vision": {
        "protocol": DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE,
        "supportedModes": ["image_understanding", "visual_analysis", "structured_output"],
        "useCase": "已识别：识图、主体理解、风格分析",
        "parameterDefaults": {"temperature": 0.3, "timeoutSeconds": 120, "strategy": "balanced"},
    },
    "embedding": {
        "protocol": DIRECT_MODEL_PROTOCOL_OPENAI_EMBEDDINGS,
        "supportedModes": ["embeddings", "semantic_search"],
        "useCase": "已识别：向量化、语义检索",
        "parameterDefaults": {"batchSize": 32, "dimensions": 1024, "strategy": "balanced"},
    },
    "audio": {
        "protocol": DIRECT_MODEL_PROTOCOL_OPENAI_AUDIO,
        "supportedModes": ["text_to_speech"],
        "useCase": "已识别：文字转语音",
        "parameterDefaults": {"format": "mp3", "timeoutSeconds": 180, "strategy": "balanced"},
    },
}


def normalize_direct_model_protocol(value: str | None) -> str:
    """Normalize operator-selected protocol without accepting arbitrary strings."""

    clean = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "": DIRECT_MODEL_PROTOCOL_AUTO,
        "openai": DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE,
        "openai-chat": DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE,
        "openai-compatible-chat": DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE,
        "openai-chat-vision": DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE,
        "ollama": DIRECT_MODEL_PROTOCOL_OLLAMA_OPENAI,
        "ollama-compatible": DIRECT_MODEL_PROTOCOL_OLLAMA_OPENAI,
        "anthropic": DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES,
        "claude": DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES,
        "anthropic-messages-api": DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES,
        "google": DIRECT_MODEL_PROTOCOL_GEMINI,
        "google-gemini": DIRECT_MODEL_PROTOCOL_GEMINI,
        "gemini-api": DIRECT_MODEL_PROTOCOL_GEMINI,
        "gemini-image": DIRECT_MODEL_PROTOCOL_GEMINI_IMAGE,
        "gemini-images": DIRECT_MODEL_PROTOCOL_GEMINI_IMAGE,
        "google-gemini-image": DIRECT_MODEL_PROTOCOL_GEMINI_IMAGE,
        "openai-image": DIRECT_MODEL_PROTOCOL_OPENAI_IMAGES,
        "openai-images-api": DIRECT_MODEL_PROTOCOL_OPENAI_IMAGES,
        "openai-embedding": DIRECT_MODEL_PROTOCOL_OPENAI_EMBEDDINGS,
        "openai-audio-api": DIRECT_MODEL_PROTOCOL_OPENAI_AUDIO,
        "autodl": DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI,
        "autodl-comfyui": DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI,
        "autodl-comfy-ui": DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI,
        "comfyui-autodl": DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI,
        "custom": DIRECT_MODEL_PROTOCOL_CUSTOM_HTTP,
    }
    clean = aliases.get(clean, clean)
    return clean if clean in DIRECT_MODEL_PROTOCOLS else DIRECT_MODEL_PROTOCOL_AUTO


def infer_direct_model_protocol(
    kind: str,
    upstream_model: str = "",
    *,
    base_url: str = "",
    requested_protocol: str | None = None,
) -> str:
    """Infer the transport protocol from kind, URL and model ID without network calls."""

    requested = normalize_direct_model_protocol(requested_protocol)
    normalized_kind = str(kind or "").strip().lower()
    model = str(upstream_model or "").strip().lower()
    raw_base = str(base_url or "").strip().lower()
    parsed = urlsplit(raw_base if "://" in raw_base else f"https://{raw_base}")
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/").lower()
    endpoint_fingerprint = f"{host}{path}"

    if requested != DIRECT_MODEL_PROTOCOL_AUTO:
        if (
            requested == DIRECT_MODEL_PROTOCOL_GEMINI
            and normalized_kind in {"chat", "agent", "text", "vision"}
            and model.startswith("gemini")
            and not _is_official_gemini_endpoint(endpoint_fingerprint)
            and _is_openai_compatible_gateway(endpoint_fingerprint, path)
        ):
            return DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE
        return requested

    if "localhost:11434" in endpoint_fingerprint or "127.0.0.1:11434" in endpoint_fingerprint or "ollama" in endpoint_fingerprint:
        return DIRECT_MODEL_PROTOCOL_OLLAMA_OPENAI
    if "anthropic" in endpoint_fingerprint:
        return DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES
    if normalized_kind == "image" and (
        "nano-banana" in model
        or ("gemini" in model and "image" in model)
    ):
        return DIRECT_MODEL_PROTOCOL_GEMINI_IMAGE
    if _is_official_gemini_endpoint(endpoint_fingerprint):
        return DIRECT_MODEL_PROTOCOL_GEMINI
    if host == "autodl.art" or host.endswith(".autodl.art"):
        return DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI
    if normalized_kind == "image":
        return DIRECT_MODEL_PROTOCOL_OPENAI_IMAGES
    if normalized_kind == "embedding":
        return DIRECT_MODEL_PROTOCOL_OPENAI_EMBEDDINGS
    if normalized_kind == "audio":
        return DIRECT_MODEL_PROTOCOL_OPENAI_AUDIO
    if _is_openai_compatible_gateway(endpoint_fingerprint, path):
        return DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE
    if model.startswith("claude"):
        return DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES
    if normalized_kind in {"chat", "agent", "text", "vision"} and model.startswith("gemini"):
        return DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE
    if normalized_kind in {"chat", "agent", "text", "vision"}:
        return DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE
    return DIRECT_MODEL_PROTOCOL_CUSTOM_HTTP


def direct_model_protocol_label(protocol: str) -> str:
    """Human-readable protocol label for the settings UI."""

    normalized = normalize_direct_model_protocol(protocol)
    labels = {
        DIRECT_MODEL_PROTOCOL_AUTO: "自动识别",
        DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE: "OpenAI 兼容",
        DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES: "Anthropic Messages",
        DIRECT_MODEL_PROTOCOL_GEMINI: "Google Gemini",
        DIRECT_MODEL_PROTOCOL_GEMINI_IMAGE: "Gemini 图像",
        DIRECT_MODEL_PROTOCOL_OLLAMA_OPENAI: "Ollama OpenAI 兼容",
        DIRECT_MODEL_PROTOCOL_OPENAI_IMAGES: "OpenAI Images",
        DIRECT_MODEL_PROTOCOL_OPENAI_EMBEDDINGS: "OpenAI Embeddings",
        DIRECT_MODEL_PROTOCOL_OPENAI_AUDIO: "OpenAI Audio",
        DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI: "AutoDL ComfyUI",
        DIRECT_MODEL_PROTOCOL_CUSTOM_HTTP: "Custom HTTP",
    }
    return labels.get(normalized, labels[DIRECT_MODEL_PROTOCOL_AUTO])


def direct_model_protocol_runtime_ready(protocol: str) -> bool:
    """Return whether current runtime can execute the selected direct protocol."""

    normalized = normalize_direct_model_protocol(protocol)
    return bool(get_model_contract(normalized).supported_kinds)




def _direct_model_runtime_ready_for_kind(kind: str, protocol: str) -> bool:
    normalized_kind = str(kind or "").strip().lower()
    normalized_protocol = normalize_direct_model_protocol(protocol)
    return get_model_contract(normalized_protocol).runtime_ready(normalized_kind)

def _with_snake_case_aliases(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    if "supportedModes" in result:
        result["supported_modes"] = result["supportedModes"]
    if "useCase" in result:
        result["use_case"] = result["useCase"]
    if "parameterDefaults" in result:
        result["parameter_defaults"] = result["parameterDefaults"]
    if "protocolLabel" in result:
        result["protocol_label"] = result["protocolLabel"]
    if "runtimeReady" in result:
        result["runtime_ready"] = result["runtimeReady"]
    for camel, snake in (
        ("modelKey", "model_key"),
        ("modeType", "mode_type"),
        ("declaredCapabilities", "declared_capabilities"),
        ("inputSlots", "input_slots"),
        ("referenceLimits", "reference_limits"),
        ("parameterSchema", "parameter_schema"),
        ("runtimeProbe", "runtime_probe"),
        ("capabilityRevision", "capability_revision"),
        ("aspectRatioOptions", "aspect_ratio_options"),
        ("resolutionOptions", "resolution_options"),
        ("qualityOptions", "quality_options"),
        ("supportsCustomAspectRatio", "supports_custom_aspect_ratio"),
        ("supportsCustomResolution", "supports_custom_resolution"),
        ("capabilitySource", "capability_source"),
        ("providerMapping", "provider_mapping"),
        ("voiceOptions", "voice_options"),
        ("audioFormats", "audio_formats"),
    ):
        if camel in result:
            result[snake] = result[camel]
    return result


def _unified_model_contract(
    kind: str,
    upstream_model: str,
    payload: dict[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the stable Tool-Spec fields consumed by every model picker.

    Existing provider-specific fields remain intact. These fields form the
    common denominator used by nodes, parameter forms and runtime probes so a
    model switch cannot silently change one surface while leaving another on
    name-based inference.
    """

    metadata_record = _metadata_record(metadata)
    declared_capabilities: list[str] = []
    declaration_groups = (
        (
            "supportedModes",
            (
                "supportedModes",
                "supported_modes",
                "modes",
                "generationModes",
                "generation_modes",
            ),
        ),
        (
            "aspectRatioOptions",
            (
                "aspectRatioOptions",
                "aspect_ratio_options",
                "supportedAspectRatios",
                "supported_aspect_ratios",
                "supportedImageAspectRatios",
                "supported_image_aspect_ratios",
                "supportedImageRatios",
                "supported_image_ratios",
                "aspectRatios",
                "aspect_ratios",
            ),
        ),
        (
            "resolutionOptions",
            (
                "resolutionOptions",
                "resolution_options",
                "supportedResolutions",
                "supported_resolutions",
                "supportedSizes",
                "supported_sizes",
                "supportedImageSizes",
                "supported_image_sizes",
                "imageSizes",
                "image_sizes",
                "supportedDimensions",
                "supported_dimensions",
                "resolutionMode",
                "resolution_mode",
                "sizes",
            ),
        ),
        (
            "qualityOptions",
            (
                "qualityOptions",
                "quality_options",
                "supportedQualities",
                "supported_qualities",
                "supportedQualityValues",
                "supported_quality_values",
            ),
        ),
        ("sizeOptions", ("sizeOptions", "size_options")),
        ("inputSlots", ("inputSlots", "input_slots")),
        ("referenceLimits", ("referenceLimits", "reference_limits")),
        ("providerMapping", ("providerMapping", "provider_mapping", "mapping")),
        ("voiceOptions", ("voiceOptions", "voice_options")),
        ("audioFormats", ("audioFormats", "audio_formats")),
        ("parameters", ("parameters",)),
        ("parameterSchema", ("parameterSchema", "parameter_schema")),
        ("workflowInputRules", ("workflowInputRules", "workflow_input_rules")),
        ("workflowDiscovery", ("workflowDiscovery", "workflow_discovery")),
        ("workflowId", ("workflowId", "workflow_id")),
        ("workflowName", ("workflowName", "workflow_name")),
        ("mediaInputs", ("mediaInputs", "media_inputs")),
    )
    for canonical, keys in declaration_groups:
        if _metadata_declares(metadata_record, *keys):
            declared_capabilities.append(canonical)
    modes = list(payload.get("supportedModes") or [])
    defaults = dict(payload.get("parameterDefaults") or {})
    input_slots: dict[str, list[str]] = {
        "agent": ["text", "canvas_context", "tool_results"],
        "text": ["text"],
        "vision": ["text", "images"],
        "image": ["prompt"],
        "embedding": ["text"],
        # A protocol's ability to accept an uploaded voice sample is not
        # implied by text-to-speech support.  It must be declared by the
        # upstream model metadata and mapped to a provider field.
        "audio": ["text"],
    }
    if kind == "image" and "imageToImage" in modes:
        input_slots["image"] = ["prompt", "reference_images"]
    reference_limits = {
        "images": 9 if kind in {"vision", "image"} else 0,
        "videos": 0,
        "audio": 0,
    }
    declared_input_slots = _metadata_values(
        metadata_record, "inputSlots", "input_slots"
    )
    input_slots = list(declared_input_slots) if _metadata_declares(
        metadata_record, "inputSlots", "input_slots"
    ) else input_slots.get(kind, [kind] if kind else [])
    raw_reference_limits = metadata_record.get("referenceLimits")
    if raw_reference_limits is None:
        raw_reference_limits = metadata_record.get("reference_limits")
    if isinstance(raw_reference_limits, Mapping):
        if kind == "audio":
            # Preserve mode-specific audio limits such as
            # {"text_to_speech": {"audio": 1}} instead of dropping them into
            # the generic image/video shape.
            normalized_audio_limits: dict[str, Any] = {}
            for mode_key, raw_value in raw_reference_limits.items():
                if isinstance(raw_value, Mapping):
                    value = raw_value.get("audio")
                    try:
                        number = int(value)
                    except (TypeError, ValueError):
                        continue
                    if number >= 0:
                        normalized_audio_limits[str(mode_key)] = {"audio": number}
                elif str(mode_key) in {"audio", "audio_reference", "reference_audio"}:
                    try:
                        number = int(raw_value)
                    except (TypeError, ValueError):
                        continue
                    if number >= 0:
                        normalized_audio_limits["audio"] = number
            if normalized_audio_limits:
                reference_limits = normalized_audio_limits
        else:
            for key in reference_limits:
                value = raw_reference_limits.get(key)
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    continue
                if number >= 0:
                    reference_limits[key] = number
    provider_mapping = _metadata_record(metadata_record).get("providerMapping")
    if provider_mapping is None:
        provider_mapping = metadata_record.get("provider_mapping")
    voice_options = _metadata_record(metadata_record).get("voiceOptions")
    if voice_options is None:
        voice_options = metadata_record.get("voice_options")
    audio_formats = _metadata_record(metadata_record).get("audioFormats")
    if audio_formats is None:
        audio_formats = metadata_record.get("audio_formats")
    parameters = metadata_record.get("parameters")
    parameter_schema = metadata_record.get("parameterSchema")
    if parameter_schema is None:
        parameter_schema = metadata_record.get("parameter_schema")
    workflow_input_rules = metadata_record.get("workflowInputRules")
    if workflow_input_rules is None:
        workflow_input_rules = metadata_record.get("workflow_input_rules")
    workflow_discovery = metadata_record.get("workflowDiscovery")
    if workflow_discovery is None:
        workflow_discovery = metadata_record.get("workflow_discovery")
    workflow_id = metadata_record.get("workflowId")
    if workflow_id is None:
        workflow_id = metadata_record.get("workflow_id")
    workflow_name = metadata_record.get("workflowName")
    if workflow_name is None:
        workflow_name = metadata_record.get("workflow_name")
    media_inputs = metadata_record.get("mediaInputs")
    if media_inputs is None:
        media_inputs = metadata_record.get("media_inputs")
    if not isinstance(parameter_schema, Mapping) or not parameter_schema:
        parameter_schema = {
            key: {"default": value, "type": type(value).__name__}
            for key, value in defaults.items()
        }
    protocol = normalize_direct_model_protocol(str(payload.get("protocol") or ""))
    transport = get_model_contract(protocol)
    return {
        "capabilityRevision": "direct-model-contract.v2",
        "modelKey": str(upstream_model or "").strip(),
        "modality": kind,
        "modeType": modes,
        "declaredCapabilities": declared_capabilities,
        "inputSlots": input_slots,
        "referenceLimits": reference_limits,
        "providerMapping": dict(provider_mapping) if isinstance(provider_mapping, Mapping) else {},
        "voiceOptions": list(voice_options) if isinstance(voice_options, (list, tuple)) else [],
        "audioFormats": list(audio_formats) if isinstance(audio_formats, (list, tuple)) else [],
        "parameters": list(parameters) if isinstance(parameters, (list, tuple)) else [],
        "parameterSchema": dict(parameter_schema) if isinstance(parameter_schema, Mapping) else {},
        "workflowInputRules": list(workflow_input_rules)
        if isinstance(workflow_input_rules, (list, tuple))
        else [],
        "workflowDiscovery": dict(workflow_discovery)
        if isinstance(workflow_discovery, Mapping)
        else {},
        "workflowId": str(workflow_id or ""),
        "workflowName": str(workflow_name or ""),
        "mediaInputs": list(media_inputs) if isinstance(media_inputs, (list, tuple)) else [],
        "media_inputs": list(media_inputs) if isinstance(media_inputs, (list, tuple)) else [],
        "defaults": defaults,
        "fallback": {"strategy": "explicit-only", "allowSilentModelSwitch": False},
        "runtimeProbe": {
            "required": True,
            "method": "model-catalog",
            "credentialFree": True,
        },
        "verificationStatus": (
            "degraded"
            if transport.verification_status == "degraded"
            else "directory-only"
        ),
        "transportContract": transport.browser_safe(kind=kind),
    }


def direct_embedding_dimensions(upstream_model: str) -> int:
    """Return a conservative dimension contract for common embedding families."""

    model = str(upstream_model or "").strip().lower()
    if "text-embedding-3-large" in model:
        return 3072
    if "text-embedding-3-small" in model:
        return 1536
    if "bge-m3" in model or "qwen3-embedding" in model:
        return 1024
    if "text-embedding-ada-002" in model:
        return 1536
    return 1024


def direct_model_capability_summary(
    kind: str,
    upstream_model: str = "",
    *,
    protocol: str | None = None,
    base_url: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the shared browser-safe capability payload for one direct model.

    ``metadata`` is the credential-free record captured by the runtime model
    probe.  Keeping it on this common entry point prevents the model center,
    canvas nodes, Agent bridge, and workflow snapshots from silently falling
    back to a name-based profile after a successful upstream capability read.
    """

    normalized_kind = str(kind or "").strip().lower()
    metadata_record = _metadata_record(metadata)
    inferred_protocol = infer_direct_model_protocol(
        normalized_kind,
        upstream_model,
        base_url=base_url,
        requested_protocol=protocol,
    )
    if normalized_kind == "image":
        image_payload = direct_image_capability_summary(
            upstream_model,
            metadata=metadata,
        )
        if inferred_protocol in {DIRECT_MODEL_PROTOCOL_GEMINI, DIRECT_MODEL_PROTOCOL_CUSTOM_HTTP}:
            image_payload = {
                **image_payload,
                "useCase": f"已识别：{direct_model_protocol_label(inferred_protocol)} 生图接口，按自定义协议记录",
            }
        contract_payload = {
            "protocol": inferred_protocol,
            "protocolLabel": direct_model_protocol_label(inferred_protocol),
            "runtimeReady": _direct_model_runtime_ready_for_kind(normalized_kind, inferred_protocol),
            **image_payload,
        }
        return _with_snake_case_aliases({
            **contract_payload,
            **_unified_model_contract(
                normalized_kind,
                upstream_model,
                contract_payload,
                metadata=metadata_record,
            ),
        })
    payload = dict(_DIRECT_KIND_CAPABILITIES.get(normalized_kind) or {})
    declared_modes = _metadata_values(
        metadata_record,
        "supportedModes",
        "supported_modes",
        "modes",
    )
    if _metadata_declares(
        metadata_record,
        "supportedModes",
        "supported_modes",
        "modes",
    ):
        payload["supportedModes"] = list(declared_modes)
    declared_defaults = metadata_record.get("parameterDefaults")
    if declared_defaults is None:
        declared_defaults = metadata_record.get("parameter_defaults")
    if isinstance(declared_defaults, Mapping):
        payload["parameterDefaults"] = {
            **dict(payload.get("parameterDefaults") or {}),
            **dict(declared_defaults),
        }
    if normalized_kind == "embedding":
        metadata_dimensions = _metadata_positive_int(
            metadata_record,
            "dimensions",
            "embeddingDimensions",
            "embedding_dimensions",
            "outputDimensions",
            "output_dimensions",
        )
        payload["parameterDefaults"] = {
            **payload["parameterDefaults"],
            "dimensions": metadata_dimensions or direct_embedding_dimensions(upstream_model),
        }
    if not payload:
        payload = {
            "supportedModes": [normalized_kind] if normalized_kind else [],
            "useCase": "已识别：通用直连接口",
            "parameterDefaults": {"strategy": "balanced"},
        }
    protocol_use_case = {
        DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE: payload.get("useCase") or "已识别：OpenAI 兼容直连模型",
        DIRECT_MODEL_PROTOCOL_OLLAMA_OPENAI: "已识别：Ollama 本地/局域网 OpenAI 兼容模型",
        DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES: "已识别：Anthropic Claude Messages 接口",
        DIRECT_MODEL_PROTOCOL_GEMINI: "已识别：Google Gemini 原生接口",
        DIRECT_MODEL_PROTOCOL_GEMINI_IMAGE: "已识别：Gemini 原生生图接口",
        DIRECT_MODEL_PROTOCOL_CUSTOM_HTTP: "已识别：Custom HTTP 接口，需自定义适配模板",
        DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI: "已识别：AutoDL ComfyUI 异步音频工作流",
    }
    payload["protocol"] = inferred_protocol
    payload["protocolLabel"] = direct_model_protocol_label(inferred_protocol)
    payload["runtimeReady"] = _direct_model_runtime_ready_for_kind(normalized_kind, inferred_protocol)
    payload["useCase"] = protocol_use_case.get(inferred_protocol, payload.get("useCase", "已识别：直连接口"))
    return _with_snake_case_aliases({
        **payload,
        **_unified_model_contract(
            normalized_kind,
            upstream_model,
            payload,
            metadata=metadata_record,
        ),
    })


__all__ = [
    "DIRECT_MODEL_KIND_ORDER",
    "DIRECT_MODEL_PROTOCOLS",
    "OPENAI_RUNTIME_PROTOCOLS",
    "direct_embedding_dimensions",
    "direct_model_capability_summary",
    "direct_model_protocol_label",
    "direct_model_protocol_runtime_ready",
    "infer_direct_model_protocol",
    "normalize_direct_model_base_url",
    "normalize_direct_model_protocol",
]
