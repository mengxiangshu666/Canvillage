"""Zero-billing discovery for operator-configured video APIs."""

from __future__ import annotations

import json
from math import gcd
import re
from typing import Any, Iterable, Mapping
from urllib.parse import quote, urlparse
from uuid import uuid4

from .direct_video_protocol_contracts import (
    DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI,
    DIRECT_VIDEO_PROTOCOL_MINIMAX_V2,
    DIRECT_VIDEO_PROTOCOL_OPENAI,
    get_direct_video_protocol_contract,
    normalize_direct_video_protocol,
    protocol_capability,
    protocol_base_url,
    query_url,
    resolve_protocol_from_metadata,
)
from .video_provider_adapters import infer_video_protocol_family
from .video_openapi_discovery import discover_video_openapi, select_video_task_routes


_SIZE_SLOT = re.compile(r"^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$", re.IGNORECASE)
_RESOLUTION = re.compile(
    r"^(?:[1-9][0-9]{2,5}p(?:横|竖)?|[1-9][0-9]{0,2}k(?:横|竖)?)$",
    re.IGNORECASE,
)
_ASPECT_RATIO = re.compile(r"^\d+(?:\.\d+)?:\d+(?:\.\d+)?$")
# Some OpenAI-compatible relays publish duration only in human-readable
# metadata. Infer it only when the description explicitly says it is fixed;
# a bare "30 seconds" may mean a maximum and must remain unknown.
_FIXED_DURATION_DESCRIPTION = re.compile(
    r"(?:固定|定长|fixed(?:[-\s]?length)?|constant)"
    r"\s*(?:时长|duration)?\s*[:：]?\s*"
    r"(\d+(?:\.\d+)?)\s*[-\s]*(?:秒|seconds?|secs?|s)",
    re.IGNORECASE,
)
_SENSITIVE_METADATA_KEY = re.compile(
    r"^(?:(?:api|access|refresh|auth|authorization|private|secret|session)[_-]?)?"
    r"(?:key|token|password|cookie|authorization)(?:$|[_-])",
    re.IGNORECASE,
)

_AUTODL_WORKFLOWS: tuple[tuple[str, str], ...] = (
    ("minimax_h3_b99_002", "H3首尾帧生成视频"),
    ("minimax_h3_b99_001", "H3文生视频"),
    ("minimax_h3_b99_003_12s", "H3多图生视频12秒"),
    ("wan2.2animate-v4-motion_retargeting", "动作迁移"),
    ("minimax_h3_image_audio_to_video_v2_15s", "H3多图多音频生视频15秒"),
    ("minimax_h3_lightx2v_v5_15s", "H3多图生视频15秒"),
    ("minimax_h3_image_audio_to_video_v2", "H3多图多音频生视频"),
    ("minimax_h3_image_audio_to_video", "H3图生视频-音频同步"),
    ("minimax_h3_lightx2v_v5", "H3多图参考生视频"),
    ("minimax_h3_lightx2v_no_pic", "H3文生视频"),
    ("minimax_h3_lightx2v", "H3首尾帧生成视频"),
)


def _is_autodl_base_url(base_url: str) -> bool:
    hostname = str(urlparse(base_url).hostname or "").lower()
    return hostname == "autodl.art" or hostname.endswith(".autodl.art")


def _autodl_capability(workflow_id: str) -> dict[str, Any]:
    workflow = str(workflow_id or "").lower()
    if "no_pic" in workflow or "b99_001" in workflow:
        modes = ["textToVideo"]
    elif "b99_002" in workflow or workflow.endswith("_lightx2v"):
        modes = ["firstLastFrame"]
    else:
        modes = ["imageToVideo", "allReference"]
    return {
        "source": "autodl-comfyui-contract",
        "verificationStatus": "contract-resolved",
        "modes": modes,
        "resolutionOptions": ["480p竖", "768p竖", "480p横", "768p横"],
        "aspectRatios": ["9:16", "16:9"],
        "durationRange": [1, 15],
        "nativeAudio": "optional",
        "referenceLimits": {
            "inputImages": 2,
            "referenceImages": 9,
            "referenceVideos": 0,
            "referenceAudios": 3,
        },
        "referenceLimitsKnown": [
            "inputImages",
            "referenceImages",
            "referenceVideos",
            "referenceAudios",
        ],
        "adapterFamily": DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI,
        "adapterConfidence": 0.98,
        "adapterEvidence": {
            "family": DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI,
            "confidence": 0.98,
            "sources": ["provider-profile"],
            "matchedPaths": ["/api/v1/comfyui/comfyui_workflow/{workflow_id}"],
            "resolved": True,
        },
    }


def _autodl_workflow_endpoint(base_url: str, workflow_id: str) -> str:
    """Build the zero-billing workflow metadata endpoint."""

    normalized = normalize_probe_base_url(base_url)
    encoded_id = quote(str(workflow_id or "").strip(), safe="")
    return f"{normalized}/api/v1/comfyui/workflows/{encoded_id}"


def _autodl_workflow_metadata(
    *, base_url: str, api_key: str, workflow_id: str, timeout: float
) -> dict[str, Any]:
    """Read one AutoDL workflow's input contract without submitting a task."""

    workflow_id = str(workflow_id or "").strip()
    if not workflow_id:
        return {"ok": False, "errorCode": "workflow_id_missing"}
    endpoint = _autodl_workflow_endpoint(base_url, workflow_id)
    try:
        import httpx

        headers = {"Accept": "application/json"}
        if str(api_key or "").strip():
            headers["Authorization"] = f"Bearer {str(api_key).strip()}"
        with httpx.Client(
            timeout=max(0.5, min(float(timeout), 5.0)),
            follow_redirects=True,
        ) as client:
            response = client.get(endpoint, headers=headers)
        if response.status_code >= 400:
            return {
                "ok": False,
                "errorCode": "workflow_metadata_http_error",
                "httpStatus": int(response.status_code),
            }
        payload = response.json()
    except (OSError, TimeoutError):
        return {"ok": False, "errorCode": "workflow_metadata_network_error"}
    except ValueError:
        return {"ok": False, "errorCode": "workflow_metadata_invalid_json"}
    except Exception as exc:
        return {
            "ok": False,
            "errorCode": "workflow_metadata_network_error",
            "errorType": type(exc).__name__,
        }
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping):
        return {"ok": False, "errorCode": "workflow_metadata_missing_data"}
    return {
        "ok": True,
        "endpoint": endpoint,
        "workflow": dict(data),
        "capability": _autodl_workflow_capability(workflow_id, data),
    }


def _autodl_workflow_capability(
    workflow_id: str, workflow: Mapping[str, Any]
) -> dict[str, Any]:
    """Normalize AutoDL ``input_rules`` into the shared model contract."""

    raw_rules = workflow.get("input_rules")
    input_rules = raw_rules if isinstance(raw_rules, Mapping) else {}
    parameters: list[dict[str, Any]] = []
    provider_mapping: dict[str, str] = {}
    media_inputs: list[dict[str, Any]] = []
    resolution_options: list[str] = []
    resolution_mappings: list[dict[str, Any]] = []
    aspect_ratios: list[str] = []
    duration_range: tuple[int, int] | None = None
    parameter_defaults: dict[str, Any] = {}
    image_slots = 0
    video_slots = 0
    audio_slots = 0
    required_audio = False
    has_first_frame = False
    has_last_frame = False
    has_prompt = False

    for raw_key, raw_rule in input_rules.items():
        key = str(raw_key or "").strip()
        if not key or _SENSITIVE_METADATA_KEY.search(key):
            continue
        rule = raw_rule if isinstance(raw_rule, Mapping) else {}
        type_name = _autodl_rule_type(rule.get("type"), key)
        descriptor: dict[str, Any] = {
            "key": key,
            "providerKey": key,
            "type": type_name,
            "required": bool(rule.get("required", False)),
            "advanced": key not in {"prompt", "duration", "resolution"},
            "source": "workflow_schema",
        }
        for target, source in (
            ("default", "default"),
            ("minimum", "min"),
            ("maximum", "max"),
            ("nodeId", "node_id"),
            ("field", "field"),
            ("description", "description"),
        ):
            value = rule.get(source)
            if value is not None and value != "":
                descriptor[target] = _autodl_json_value(value)
        options = rule.get("options")
        if isinstance(options, list):
            safe_options: list[dict[str, Any]] = []
            labels: list[str] = []
            for option in options[:100]:
                if not isinstance(option, Mapping):
                    continue
                label = str(option.get("label") or "").strip()
                if not label:
                    continue
                labels.append(label)
                safe_option: dict[str, Any] = {"label": label}
                values = option.get("values")
                if isinstance(values, Mapping):
                    safe_option["values"] = _autodl_json_value(values)
                safe_options.append(safe_option)
            if labels:
                descriptor["enum"] = labels
            if safe_options:
                descriptor["options"] = safe_options
        accept_types = rule.get("accept_types")
        if isinstance(accept_types, list):
            descriptor["acceptTypes"] = [
                str(item).strip()[:80]
                for item in accept_types[:16]
                if str(item).strip()
            ]
        parameters.append(descriptor)
        provider_mapping[key] = key
        lower_key = key.casefold()
        if lower_key == "prompt":
            has_prompt = True
        if lower_key == "duration" or lower_key == "audio_duration":
            low, high = _autodl_rule_range(rule)
            if low is not None and high is not None:
                if duration_range is None:
                    duration_range = (low, high)
                else:
                    duration_range = (
                        min(duration_range[0], low),
                        max(duration_range[1], high),
                    )
            if rule.get("default") is not None:
                parameter_defaults[
                    "durationSeconds" if lower_key == "duration" else "audioDurationSeconds"
                ] = _autodl_json_value(rule["default"])
        if lower_key == "resolution":
            options = rule.get("options")
            for option in options if isinstance(options, list) else ():
                if not isinstance(option, Mapping):
                    continue
                label = str(option.get("label") or "").strip()
                if not label:
                    continue
                if label not in resolution_options:
                    resolution_options.append(label)
                values = option.get("values")
                dimensions = _autodl_dimensions(values)
                item: dict[str, Any] = {"label": label}
                if dimensions is not None:
                    width, height = dimensions
                    item.update({"width": width, "height": height})
                    ratio = _autodl_label_aspect(label) or _autodl_aspect_ratio(width, height)
                    if ratio and ratio not in aspect_ratios:
                        aspect_ratios.append(ratio)
                if isinstance(values, Mapping):
                    item["values"] = _autodl_json_value(values)
                resolution_mappings.append(item)
            if rule.get("default") is not None:
                parameter_defaults["resolution"] = str(rule["default"]).strip()
        if type_name in {"image", "video", "audio"}:
            media = {
                "key": key,
                "providerKey": key,
                "type": type_name,
                "required": bool(rule.get("required", False)),
                "source": "workflow_schema",
            }
            if descriptor.get("acceptTypes"):
                media["acceptTypes"] = descriptor["acceptTypes"]
            media_inputs.append(media)
            if type_name == "image":
                image_slots += 1
            elif type_name == "video":
                video_slots += 1
            else:
                audio_slots += 1
                required_audio = required_audio or bool(rule.get("required", False))
            has_first_frame = has_first_frame or lower_key == "first_frame"
            has_last_frame = has_last_frame or lower_key == "last_frame"

    if has_first_frame and has_last_frame:
        modes = ["firstLastFrame"]
    elif video_slots:
        modes = ["videoEdit"]
    elif image_slots:
        modes = ["imageToVideo", "allReference"] if image_slots > 1 else ["imageToVideo"]
        if image_slots > 1 and not any(item.startswith("ref_image_") for item in input_rules):
            modes = ["imageToVideo"]
    else:
        modes = ["textToVideo"] if has_prompt or not media_inputs else []

    if not aspect_ratios:
        aspect_ratios = _autodl_aspects_from_labels(resolution_options)
    low, high = duration_range or (1, 15)
    reference_limits = {
        "inputImages": 2 if has_first_frame and has_last_frame else 1 if image_slots else 0,
        "referenceImages": 0 if has_first_frame and has_last_frame else image_slots if image_slots else 0,
        "referenceVideos": video_slots,
        "referenceAudios": audio_slots,
    }
    capability: dict[str, Any] = {
        "source": "workflow_schema",
        "verificationStatus": "contract-resolved",
        "workflowId": str(workflow_id).strip(),
        "workflowName": str(workflow.get("name") or "").strip(),
        "workflowInputRules": [
            _autodl_json_value({"key": key, "rule": value})
            for key, value in input_rules.items()
            if str(key).strip() and not _SENSITIVE_METADATA_KEY.search(str(key))
        ][:128],
        "modes": modes,
        "resolutionOptions": resolution_options,
        "advertisedResolutionOptions": list(resolution_options),
        "runtimeResolutionOptions": list(resolution_options),
        "aspectRatios": aspect_ratios,
        "durationRange": [low, high],
        "nativeAudio": "required" if required_audio else "optional" if audio_slots else "unsupported",
        "referenceLimits": reference_limits,
        "referenceLimitsKnown": list(reference_limits),
        "parameters": parameters[:128],
        "providerMapping": provider_mapping,
        "mediaInputs": media_inputs[:64],
        "media_inputs": media_inputs[:64],
        "parameterDefaults": parameter_defaults,
        "supportsCustomResolution": False,
        "supportsCustomAspectRatio": False,
        "adapterFamily": DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI,
        "adapterConfidence": 1.0,
        "adapterEvidence": {
            "family": DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI,
            "confidence": 1.0,
            "sources": ["workflow-schema"],
            "matchedPaths": ["/api/v1/comfyui/workflows/{workflow_id}"],
            "resolved": True,
        },
    }
    if resolution_mappings:
        capability["resolutionMappings"] = resolution_mappings[:100]
    if "seed" in parameter_defaults:
        capability["parameterDefaults"]["seed"] = parameter_defaults["seed"]
    return capability


def _autodl_rule_type(value: object, key: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"int", "integer"}:
        return "integer"
    if normalized in {"float", "double", "number", "decimal"}:
        return "number"
    if normalized in {"bool", "boolean"}:
        return "boolean"
    if normalized in {"image", "video", "audio"}:
        return normalized
    if normalized in {"enum", "string", "prompt"}:
        return "string"
    lowered = str(key or "").casefold()
    if "image" in lowered or "frame" in lowered:
        return "image"
    if "video" in lowered:
        return "video"
    if "audio" in lowered:
        return "audio"
    return "string"


def _autodl_rule_range(rule: Mapping[str, Any]) -> tuple[int | None, int | None]:
    try:
        low = int(rule["min"]) if rule.get("min") is not None else None
        high = int(rule["max"]) if rule.get("max") is not None else None
    except (TypeError, ValueError):
        return None, None
    if low is None or high is None or low < 1 or high < low or high > 300:
        return None, None
    return low, high


def _autodl_dimensions(value: object) -> tuple[int, int] | None:
    if not isinstance(value, Mapping):
        return None
    width = height = None
    fallback: list[int] = []
    for raw_key, raw_value in value.items():
        key = str(raw_key).casefold()
        try:
            number = int(raw_value)
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        fallback.append(number)
        if any(token in key for token in ("宽", "width", "number")) and width is None:
            width = number
        elif any(token in key for token in ("高", "height")) and height is None:
            height = number
    if width and height:
        return width, height
    # Some AutoDL workflows identify the two size nodes only by numeric node
    # IDs (for example ``174.inputs.Number`` / ``175.inputs.Number``).  The
    # API preserves their insertion order, which is the width/height order in
    # the provider workflow. Keep that deterministic pair instead of losing
    # the size mapping at discovery time.
    return (fallback[0], fallback[1]) if len(fallback) >= 2 else None


def _autodl_aspect_ratio(width: int, height: int) -> str:
    from math import gcd

    divisor = gcd(int(width), int(height))
    return f"{int(width) // divisor}:{int(height) // divisor}"


def _autodl_label_aspect(label: str) -> str:
    text = str(label or "")
    if "竖" in text:
        return "9:16"
    if "横" in text:
        return "16:9"
    if "1:1" in text:
        return "1:1"
    return ""


def _autodl_aspects_from_labels(labels: Iterable[str]) -> list[str]:
    result: list[str] = []
    for label in labels:
        text = str(label or "")
        ratio = _autodl_label_aspect(text)
        if ratio and ratio not in result:
            result.append(ratio)
    return result


def _autodl_json_value(value: object, *, depth: int = 0) -> object:
    """Bound workflow metadata while preserving deterministic input mappings."""

    if depth > 5:
        return "[truncated]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _autodl_json_value(item, depth=depth + 1)
            for key, item in list(value.items())[:128]
            if not _SENSITIVE_METADATA_KEY.search(str(key))
        }
    if isinstance(value, (list, tuple, set)):
        return [_autodl_json_value(item, depth=depth + 1) for item in list(value)[:128]]
    return str(value)


def discover_direct_video_models(
    *,
    base_url: str,
    api_key: str,
    protocol: str = "auto",
    timeout: float = 7.0,
) -> dict[str, Any]:
    """Read the video model directory without submitting a video task."""

    if _is_autodl_base_url(base_url) or normalize_direct_video_protocol(protocol) == DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI:
        return {
            "ok": True,
            "modelFound": True,
            "discoveredModelCount": len(_AUTODL_WORKFLOWS),
            "protocol": DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI,
            "supportedProtocols": [DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI],
            "models": [
                {"id": workflow_id, "metadata": {"displayName": label}}
                for workflow_id, label in _AUTODL_WORKFLOWS
            ],
        }

    result = probe_direct_video_model(
        upstream_model="",
        base_url=base_url,
        api_key=api_key,
        timeout=timeout,
    )
    if not result.get("ok"):
        return result
    models = result.get("models")
    if not isinstance(models, list) or not models:
        return {
            **result,
            "ok": False,
            "models": [],
            "errorCode": "empty_model_catalog",
            "error": "/models 已响应，但没有返回可导入的视频模型。",
        }
    return result


def probe_direct_video_model(
    *,
    upstream_model: str,
    base_url: str,
    api_key: str,
    protocol: str = "auto",
    timeout: float = 7.0,
) -> dict[str, Any]:
    """Read ``GET /models`` once; media generation is never used for discovery."""
    normalized_base = normalize_probe_base_url(base_url)
    requested_protocol = normalize_direct_video_protocol(protocol)
    if _is_autodl_base_url(normalized_base) or requested_protocol == DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI:
        workflow_id = str(upstream_model or "").strip()
        known = {item[0] for item in _AUTODL_WORKFLOWS}
        metadata = _autodl_workflow_metadata(
            base_url=normalized_base,
            api_key=api_key,
            workflow_id=workflow_id,
            timeout=timeout,
        )
        capability = (
            dict(metadata["capability"])
            if metadata.get("ok") and isinstance(metadata.get("capability"), Mapping)
            else _autodl_capability(workflow_id)
        )
        model_found = bool(workflow_id and (workflow_id in known or metadata.get("ok")))
        capability["workflowDiscovery"] = {
            "status": "discovered" if metadata.get("ok") else "unavailable",
            "source": "workflow_schema",
            "errorCode": metadata.get("errorCode") if not metadata.get("ok") else "",
        }
        return {
            "ok": True,
            "modelFound": model_found,
            "discoveredModelCount": len(_AUTODL_WORKFLOWS),
            "protocol": DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI,
            "supportedProtocols": [DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI],
            "adapterFamily": DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI,
            "adapterConfidence": 0.98,
            "adapterEvidence": capability["adapterEvidence"],
            "models": [
                {"id": item_id, "metadata": {"displayName": label}}
                for item_id, label in _AUTODL_WORKFLOWS
            ],
            "capability": capability if workflow_id else {},
        }
    endpoint = f"{normalized_base}/models"
    try:
        import httpx

        from novelvideo.gateway_transport import newapi_httpx_client_kwargs

        client_kwargs = newapi_httpx_client_kwargs(
            base_url=normalized_base,
            timeout=max(1.0, float(timeout)),
        )
        with httpx.Client(**client_kwargs) as client:
            response = client.get(
                endpoint,
                headers={
                    "Authorization": f"Bearer {str(api_key or '').strip()}",
                    "Accept": "application/json",
                },
            )
        if response.status_code >= 400:
            return probe_failure_for_http_status(
                response.status_code,
                response_body=response.text,
            )
        payload = response.json()
    except TimeoutError:
        return probe_failure(
            "request_timeout",
            "请求超时：请检查上游地址、网络或网关响应速度。",
            http_status=408,
        )
    except OSError as exc:
        return probe_failure(
            "network_error",
            f"网络连接失败（{type(exc).__name__}），请检查 URL、域名和本机网络。",
        )
    except ValueError:
        return probe_failure(
            "invalid_response",
            "接口已响应，但 /models 返回的不是有效 JSON。",
        )
    except Exception as exc:
        return probe_failure(
            "network_error",
            f"网络连接失败（{type(exc).__name__}），请检查 URL、域名和本机网络。",
        )

    entries = openai_model_entries(payload)
    wanted = str(upstream_model or "").strip().lower()
    entry = next(
        (
            item
            for item in entries
            if str(item.get("id") or "").strip().lower() == wanted
        ),
        None,
    )
    # A model mode declaration says nothing about its request parameters.
    # Always refresh the schema after the operator selects a model; this is a
    # read-only metadata request and never creates a media task.
    openapi = (
        discover_video_openapi(
            base_url=normalized_base,
            api_key=api_key,
            timeout=min(float(timeout), 2.0),
        )
        if str(upstream_model or "").strip()
        else {"found": False, "url": "", "paths": [], "operations": [], "version": ""}
    )
    model_detail = (
        _discover_video_model_detail(
            base_url=normalized_base,
            api_key=api_key,
            upstream_model=upstream_model,
            timeout=min(float(timeout), 2.0),
        )
        if str(upstream_model or "").strip()
        else {}
    )
    supported_protocols = (
        _strings(entry.get("supported_protocols") or entry.get("supportedProtocols"))
        if entry is not None
        else []
    )
    protocol = resolve_protocol_from_metadata(
        supported_protocols,
        fallback=DIRECT_VIDEO_PROTOCOL_OPENAI,
    )
    if entry is not None and not supported_protocols and _entry_is_plugin_owned(entry):
        # NewAPI marks plugin-backed models ``owned_by: task plugin``.  Such a
        # model may still be unserviceable through the relay family for this
        # credential's group while the plugin serves it natively, so the native
        # family is verified before the OpenAI-compatible default is kept.
        native_v2 = _native_minimax_v2_available(
            base_url=normalized_base,
            api_key=api_key,
            upstream_model=upstream_model,
            timeout=timeout,
        )
        if native_v2:
            protocol = DIRECT_VIDEO_PROTOCOL_MINIMAX_V2
            supported_protocols = [DIRECT_VIDEO_PROTOCOL_MINIMAX_V2]
    capability_entry = dict(entry or {})
    capability_entry.update(model_detail)
    if openapi.get("operations"):
        capability_entry["openapiOperations"] = openapi.get("operations")
    if openapi.get("workflowInputs"):
        capability_entry["parameters"] = openapi.get("workflowInputs")
    capability = infer_video_capability(capability_entry) if entry is not None else {}
    # 上游这次公开了什么，就以上游为准。本地协议合同和模型档案只能补上游沉默的
    # 字段，不能把「本地写死的档位」盖在「上游刚公布的档位」上——否则节点会把
    # 上游根本没声明的比例/分辨率显示成可用，用户点了就报错。
    catalog_aspects = _strings(capability.get("aspectRatios"))
    catalog_aspect_source = str(capability.get("aspectRatioSource") or "").strip()
    catalog_resolutions = _strings(capability.get("resolutionOptions"))
    catalog_resolution_source = str(capability.get("resolutionSource") or "").strip()

    def _merge_local_fallback(
        fallback: Mapping[str, Any], *, source: str
    ) -> dict[str, Any]:
        merged: dict[str, Any] = {**capability, **fallback}
        if catalog_aspects:
            merged["aspectRatios"] = catalog_aspects
            merged["aspectRatioSource"] = catalog_aspect_source or "catalog"
        elif fallback.get("aspectRatios"):
            merged["aspectRatioSource"] = source
        if catalog_resolutions:
            merged["resolutionOptions"] = catalog_resolutions
            merged["resolutionSource"] = catalog_resolution_source or "catalog"
        elif fallback.get("resolutionOptions"):
            merged["resolutionSource"] = source
        return merged

    contract_capability = protocol_capability(protocol)
    if contract_capability:
        capability = _merge_local_fallback(
            contract_capability, source="protocol-contract"
        )
    if entry is not None and not capability.get("modes"):
        profile_capability = _builtin_video_profile_capability(
            upstream_model=upstream_model,
            base_url=normalized_base,
            protocol=protocol,
        )
        capability = _merge_local_fallback(profile_capability, source="profile")
        if "sizeSlots" not in profile_capability:
            capability.pop("sizeSlots", None)
            capability.pop("sizeField", None)
    adapter_evidence = infer_video_protocol_family(
        protocol_hint=(supported_protocols[0] if supported_protocols else ""),
        model_id=upstream_model,
        model_metadata=entry,
        openapi_paths=openapi.get("paths") or (),
    )
    openapi_submit_path, openapi_task_path = select_video_task_routes(
        base_url=normalized_base,
        paths=openapi.get("paths") or (),
        operations=openapi.get("operations") or (),
    )
    if openapi_submit_path:
        capability["openapiSubmitPath"] = openapi_submit_path
    if openapi_task_path:
        capability["openapiQueryPath"] = openapi_task_path
    task_read_validation = (
        _probe_video_task_read_credential(
            base_url=normalized_base,
            api_key=api_key,
            task_path=openapi_task_path,
            timeout=timeout,
        )
        if protocol == DIRECT_VIDEO_PROTOCOL_OPENAI and entry is not None
        else {"status": "unverified", "reason": "video_task_read_route_unavailable"}
    )
    api_key_validation = (
        _probe_documented_key_info(base_url=normalized_base, api_key=api_key, timeout=timeout)
        if urlparse(normalized_base).hostname == "dolasd.xyz"
        else {"status": "unverified", "reason": "documented_key_info_unavailable"}
    )
    credential_validation = api_key_validation
    if api_key_validation.get("status") == "unverified":
        credential_validation = task_read_validation
    if adapter_evidence.family.value != "unknown" and "model-profile" not in adapter_evidence.sources:
        capability = {
            **capability,
            "adapterFamily": adapter_evidence.family.value,
            "adapterConfidence": adapter_evidence.confidence,
            "adapterEvidence": adapter_evidence.to_dict(),
            "openapiUrl": openapi.get("url") or "",
            "openapiPaths": openapi.get("paths") or [],
            "openapiOperations": openapi.get("operations") or [],
        }
    if supported_protocols:
        capability["supportedProtocols"] = supported_protocols
        capability["detectedProtocol"] = protocol
    if capability:
        capability["capabilityDiscovery"] = {
            "status": (
                "schema"
                if openapi.get("operations") or model_detail.get("parameters")
                else "catalog"
                if capability.get("declaredCapabilities")
                else "unknown"
            ),
            "sources": [
                source
                for source, present in (
                    ("models", entry is not None),
                    ("model-detail", bool(model_detail)),
                    ("openapi", bool(openapi.get("operations"))),
                )
                if present
            ],
        }
        current_status = str(capability.get("verificationStatus") or "").strip().lower()
        capability["verificationStage"] = (
            "artifact"
            if current_status == "runtime-verified"
            else "contract"
            if current_status == "contract-resolved"
            else "catalog"
        )
    result: dict[str, Any] = {
        "ok": not bool(upstream_model) or entry is not None,
        "modelFound": entry is not None,
        "discoveredModelCount": len(entries),
        "protocol": protocol,
        "supportedProtocols": supported_protocols,
        "adapterFamily": adapter_evidence.family.value,
        "adapterConfidence": adapter_evidence.confidence,
        "adapterEvidence": adapter_evidence.to_dict(),
        "openapiUrl": openapi.get("url") or "",
        "openapiPaths": openapi.get("paths") or [],
        "openapiOperations": openapi.get("operations") or [],
        "openapiSubmitPath": openapi_submit_path,
        "openapiQueryPath": openapi_task_path,
        "modelDetail": model_detail,
        "capabilityDiscovery": (
            capability.get("capabilityDiscovery") if capability else {"status": "unknown", "sources": []}
        ),
        "workflowInputs": openapi.get("workflowInputs") or [],
        "models": [
            {"id": str(item.get("id") or "").strip(), "metadata": _safe_model_metadata(item)}
            for item in sorted(entries, key=lambda value: str(value.get("id") or "").casefold())
        ],
        "credentialValidation": credential_validation,
        "taskReadValidation": task_read_validation,
    }
    if api_key_validation.get("status") == "rejected":
        result.update(
            {
                "ok": False,
                "errorCode": "VIDEO_AUTH_REJECTED",
                "httpStatus": credential_validation.get("httpStatus"),
                "error": (
                    "dolasd 的密钥信息接口拒绝了当前 Key "
                    f"（HTTP {api_key_validation.get('httpStatus')}）。"
                    "这与模型目录是否可读无关。"
                ),
            }
        )
    elif (
        task_read_validation.get("status") == "rejected"
        and api_key_validation.get("status") == "accepted"
    ):
        result.update(
            {
                "ok": False,
                "errorCode": "VIDEO_TASK_QUERY_AUTH_REJECTED",
                "httpStatus": task_read_validation.get("httpStatus"),
                "error": (
                    "密钥信息接口验证通过，但视频任务查询接口拒绝了随机任务查询 "
                    f"（HTTP {task_read_validation.get('httpStatus')}）。"
                    "请检查该上游的任务查询接口状态。"
                ),
            }
        )
    elif credential_validation.get("status") == "rejected":
        result.update(
            {
                "ok": False,
                "errorCode": "VIDEO_AUTH_REJECTED",
                "httpStatus": credential_validation.get("httpStatus"),
                "error": (
                    "视频任务接口拒绝了当前保存的认证信息 "
                    f"（HTTP {credential_validation.get('httpStatus')}）。"
                    "该结果不能单独判定密钥本身无效。"
                ),
            }
        )
    elif credential_validation.get("status") == "permission_denied":
        result.update(
            {
                "ok": False,
                "errorCode": "VIDEO_PERMISSION_DENIED",
                "httpStatus": credential_validation.get("httpStatus"),
                "error": (
                    "模型目录可读，但当前 Key 无权访问视频任务接口 "
                    f"（HTTP {credential_validation.get('httpStatus')}）。"
                    "请检查上游账号的视频权限。"
                ),
            }
        )
    if upstream_model and entry is None:
        result["errorCode"] = "model_not_found"
        result["error"] = f"上游模型目录没有列出所选模型 {upstream_model}；连接不能判定为可用。"
    if capability:
        result["capability"] = capability
    return result


def _probe_video_task_read_credential(
    *,
    base_url: str,
    api_key: str,
    task_path: str,
    timeout: float,
) -> dict[str, Any]:
    """Check video-endpoint auth with a read for a random, nonexistent task."""

    if not task_path:
        return {"status": "unverified", "reason": "video_task_read_route_unavailable"}

    root = urlparse(base_url)
    origin = f"{root.scheme}://{root.netloc}"
    base_path = root.path.rstrip("/")
    if base_path and (task_path == base_path or task_path.startswith(base_path + "/")):
        endpoint_path = task_path
    else:
        endpoint_path = f"{base_path}/{task_path.lstrip('/')}"
    task_path = re.sub(r"\{[^/{}]+\}$", uuid4().hex, endpoint_path)
    endpoint = f"{origin}{task_path}"

    try:
        import httpx

        from novelvideo.gateway_transport import newapi_httpx_client_kwargs

        with httpx.Client(
            **newapi_httpx_client_kwargs(
                base_url=base_url,
                timeout=max(1.0, min(float(timeout), 5.0)),
                follow_redirects=False,
            )
        ) as client:
            response = client.get(
                endpoint,
                headers={
                    "Authorization": f"Bearer {str(api_key or '').strip()}",
                    "Accept": "application/json",
                },
            )
    except Exception:
        return {"status": "unverified", "reason": "video_task_read_probe_failed"}

    status = int(response.status_code)
    if status == 401:
        return {"status": "rejected", "httpStatus": status}
    if status == 403:
        return {"status": "permission_denied", "httpStatus": status}
    if 200 <= status < 300:
        return {"status": "accepted", "httpStatus": status}
    if status == 404:
        try:
            body = response.json()
        except (ValueError, AttributeError):
            body = None
        if isinstance(body, Mapping):
            detail = " ".join(
                str(body.get(key) or "") for key in ("detail", "message", "error")
            ).casefold()
            if any(term in detail for term in ("invalid api key", "invalid key", "unauthorized", "authentication")):
                return {"status": "rejected", "httpStatus": status}
            if any(term in detail for term in ("task not found", "video not found", "does not exist", "not_found")):
                return {"status": "accepted", "httpStatus": status}
    return {"status": "unverified", "httpStatus": status}


def _probe_documented_key_info(
    *, base_url: str, api_key: str, timeout: float
) -> dict[str, Any]:
    """Verify Dola credentials using its documented, non-billing key-info read."""

    root = urlparse(base_url)
    origin = f"{root.scheme}://{root.netloc}"
    endpoint = f"{origin}{root.path.rstrip('/')}/key/info"
    try:
        import httpx

        from novelvideo.gateway_transport import newapi_httpx_client_kwargs

        with httpx.Client(
            **newapi_httpx_client_kwargs(
                base_url=base_url,
                timeout=max(1.0, min(float(timeout), 5.0)),
                follow_redirects=False,
            )
        ) as client:
            response = client.get(
                endpoint,
                headers={
                    "Authorization": f"Bearer {str(api_key or '').strip()}",
                    "Accept": "application/json",
                },
            )
    except Exception:
        return {"status": "unverified", "reason": "key_info_probe_failed"}

    if response.status_code == 401:
        return {
            "status": "rejected",
            "httpStatus": 401,
            "credentialShape": _credential_shape(api_key),
        }
    if response.status_code == 403:
        return {
            "status": "permission_denied",
            "httpStatus": 403,
            "credentialShape": _credential_shape(api_key),
        }
    if 200 <= response.status_code < 300:
        return {"status": "accepted", "httpStatus": int(response.status_code)}
    return {"status": "unverified", "httpStatus": int(response.status_code)}


def _credential_shape(value: object) -> dict[str, Any]:
    """Describe the credential that was sent without revealing any of it."""

    text = str(value or "")
    return {
        "length": len(text),
        "asciiOnly": text.isascii(),
        "hasInnerWhitespace": any(char.isspace() for char in text),
    }


def _discover_video_model_detail(
    *, base_url: str, api_key: str, upstream_model: str, timeout: float
) -> dict[str, Any]:
    """Read common model-detail routes without invoking a generation endpoint."""

    model_id = str(upstream_model or "").strip()
    if not model_id:
        return {}
    root = normalize_probe_base_url(base_url)
    encoded = quote(model_id, safe="")
    candidates = tuple(dict.fromkeys((f"{root}/models/{encoded}", f"{root}/model/{encoded}")))
    try:
        import httpx

        from novelvideo.gateway_transport import newapi_httpx_client_kwargs

        with httpx.Client(
            **newapi_httpx_client_kwargs(
                base_url=root,
                timeout=max(0.5, min(float(timeout), 2.0)),
                follow_redirects=True,
            )
        ) as client:
            for endpoint in candidates:
                try:
                    response = client.get(
                        endpoint,
                        headers={
                            "Authorization": f"Bearer {str(api_key or '').strip()}",
                            "Accept": "application/json",
                        },
                    )
                except httpx.HTTPError:
                    continue
                if response.status_code >= 400:
                    continue
                try:
                    payload = response.json()
                except ValueError:
                    continue
                candidate: object = payload
                if isinstance(payload, Mapping):
                    for key in ("data", "model", "result"):
                        nested = payload.get(key)
                        if isinstance(nested, Mapping):
                            candidate = nested
                            break
                if isinstance(candidate, Mapping):
                    return _safe_model_metadata(candidate)
    except Exception:
        return {}
    return {}


def _entry_is_plugin_owned(entry: Mapping[str, Any]) -> bool:
    """True when the catalog says a task plugin, not a plain channel, owns it."""

    owner = str(entry.get("owned_by") or entry.get("ownedBy") or "").casefold()
    return "plugin" in owner


def _native_minimax_v2_available(
    *,
    base_url: str,
    api_key: str,
    upstream_model: str,
    timeout: float = 7.0,
) -> bool:
    """Verify the station's own native MiniMax v2 family without billing anything.

    Stations like ``dmc.cc`` serve ``MiniMax-H3`` through a task plugin: the
    relay family (``/v1/video/generations``) answers ``503 model_not_found`` /
    ``No available channel`` for every model under the credential's group, while
    the plugin publishes the native contract at ``/v2/video_generation`` and
    ``/v2/query/video_generation/{task_id}`` and accepts the same key.

    Discovery still never creates a task.  It asks the native *read* route for a
    task id that cannot exist: a station without that route answers HTML from
    its SPA catch-all, a route that rejects the credential answers ``401``/
    ``403``, and only the real service answers a structured JSON error about the
    unknown task.  ``upstream_model`` is accepted for call-site symmetry and
    logging; the route evidence is what decides.
    """

    configured = str(base_url or "").strip().rstrip("/")
    if not configured:
        return False
    contract = get_direct_video_protocol_contract(DIRECT_VIDEO_PROTOCOL_MINIMAX_V2)
    native_root = protocol_base_url(configured, contract).rstrip("/")
    if not native_root:
        return False
    probe_url = query_url(native_root, contract, f"probe-{uuid4().hex[:12]}")
    try:
        import httpx

        from novelvideo.gateway_transport import newapi_httpx_client_kwargs

        client_kwargs = newapi_httpx_client_kwargs(
            base_url=native_root,
            timeout=max(1.0, min(float(timeout), 5.0)),
        )
        with httpx.Client(**client_kwargs) as client:
            response = client.get(
                probe_url,
                headers={
                    "Authorization": f"Bearer {str(api_key or '').strip()}",
                    "Accept": "application/json",
                },
            )
        payload = response.json()
    except Exception:
        return False
    if response.status_code in {401, 403} or response.status_code >= 500:
        return False
    if not isinstance(payload, Mapping):
        return False
    error = payload.get("error")
    if isinstance(error, Mapping):
        return True
    return any(key in payload for key in ("code", "message", "status"))


def native_minimax_v2_available(
    *,
    base_url: str,
    api_key: str,
    upstream_model: str,
    timeout: float = 7.0,
) -> bool:
    """Public zero-billing probe for callers outside catalog discovery."""

    return _native_minimax_v2_available(
        base_url=base_url,
        api_key=api_key,
        upstream_model=upstream_model,
        timeout=timeout,
    )


def _builtin_video_profile_capability(
    *, upstream_model: str, base_url: str, protocol: str
) -> dict[str, Any]:
    """Expose a known local adapter contract when ``/models`` has only IDs.

    A number of relays return a bare model directory entry.  That is enough to
    identify a model, but not enough for the canvas to compile a request.  A
    built-in profile is an explicit local transport contract (not a claim that
    the provider advertised every field), so it can safely bridge those sparse
    catalogs while unknown model IDs remain directory-only.
    """

    from .capabilities import VideoMode
    from .direct_video_profiles import resolve_direct_video_profile

    profile = resolve_direct_video_profile(
        upstream_model,
        base_url=base_url,
        protocol=protocol,
    )
    if profile.name == "openai-video-generic":
        return {}
    mode_map = {
        VideoMode.TEXT_TO_VIDEO: "textToVideo",
        VideoMode.IMAGE_TO_VIDEO: "imageToVideo",
        VideoMode.FIRST_LAST_FRAME: "firstLastFrame",
        VideoMode.REFERENCE_TO_VIDEO: "allReference",
    }
    modes = list(profile.exact_canvas_modes)
    if not modes:
        modes = list(dict.fromkeys(mode_map[mode] for mode in profile.modes))
    limits = profile.reference_limits
    return {
        "source": "models-profile-contract",
        "verificationStatus": "contract-resolved",
        "modes": modes,
        "resolutionOptions": list(profile.resolution),
        "aspectRatios": list(profile.aspect),
        "durationRange": [min(profile.duration), max(profile.duration)],
        "nativeAudio": profile.native_audio.value,
        "referenceLimits": {
            "inputImages": limits.input_images,
            "referenceImages": limits.reference_images,
            "referenceVideos": limits.reference_videos,
            "referenceAudios": limits.reference_audios,
        },
        "referenceLimitsKnown": sorted(profile.reference_limits_known),
        "promptRules": {
            "durationConsistency": profile.prompt_duration_consistency,
            "referenceConsistency": profile.prompt_reference_consistency,
        },
        "failureGracePolls": profile.failure_grace_polls,
    }


def normalize_probe_base_url(base_url: str) -> str:
    """Reject malformed probe URLs and URLs embedding credentials."""
    normalized = str(base_url or "").strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("baseUrl must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("baseUrl must not contain credentials")
    return normalized


def probe_failure_for_http_status(
    status: int,
    *,
    response_body: str = "",
) -> dict[str, Any]:
    messages = {
        401: (
            "authentication_failed",
            "认证失败：Key 未被该接口接受，请核对 Key 或认证方式。",
        ),
        403: (
            "permission_denied",
            "接口拒绝访问：Key 已送达，但当前账号、模型权限或网关策略不允许访问 /models。",
        ),
        404: (
            "models_endpoint_not_found",
            "未找到 /models：请检查 URL 是否已包含正确的 API 版本路径（通常是 /v1）。",
        ),
        429: (
            "rate_limited",
            "接口请求过快或额度受限，请稍后再检测。",
        ),
    }
    code, message = messages.get(
        int(status),
        ("upstream_http_error", f"上游接口返回 HTTP {int(status)}。"),
    )
    upstream_message = _safe_upstream_error_message(response_body)
    if upstream_message:
        message = f"{message} 上游信息：{upstream_message}"
    return probe_failure(code, message, http_status=int(status))


def _safe_upstream_error_message(body: str) -> str:
    """Extract one short diagnostic without reflecting credentials or HTML."""

    message = ""
    try:
        payload = json.loads(str(body or ""))
    except (json.JSONDecodeError, TypeError):
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("code") or "").strip()
        if not message:
            message = str(payload.get("message") or payload.get("detail") or "").strip()
    return " ".join(message.split())[:240]


def probe_failure(
    error_code: str,
    error: str,
    *,
    http_status: int | None = None,
) -> dict[str, Any]:
    """Keep failures actionable without echoing endpoint URLs or credentials."""
    result: dict[str, Any] = {
        "ok": False,
        "modelFound": False,
        "discoveredModelCount": 0,
        "protocol": DIRECT_VIDEO_PROTOCOL_OPENAI,
        "errorCode": error_code,
        "error": error,
    }
    if http_status is not None:
        result["httpStatus"] = http_status
    return result


def openai_model_entries(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return []
    return [
        item
        for item in payload["data"]
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]


def openai_model_ids(payload: object) -> set[str]:
    return {str(item["id"]).strip() for item in openai_model_entries(payload)}


def infer_video_capability(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize common `/models` metadata shapes into one capability contract."""
    sources = _metadata_sources(entry)
    tags = _lower_strings(_first_value(sources, "tags", "capability_tags"))
    modalities = _lower_strings(
        _first_value(sources, "input_modalities", "inputModalities", "modalities")
    )
    explicit_modes = _lower_strings(
        _first_value(sources, "modes", "supported_modes", "supportedModes")
    )
    modes = _infer_modes(tags, modalities, explicit_modes)
    size_slots = _matching_strings(
        _first_value(
            sources,
            "size_options",
            "sizeOptions",
            "supported_sizes",
            "supportedSizes",
            "sizes",
        ),
        _SIZE_SLOT,
    )
    resolutions = _matching_strings(
        _first_value(
            sources,
            "resolution_options",
            "resolutionOptions",
            "supported_resolutions",
            "supportedResolutions",
            "supported_sizes",
            "supportedSizes",
            "resolutions",
        ),
        _RESOLUTION,
    )
    aspects = _matching_strings(
        _first_value(
            sources,
            "aspect_ratios",
            "aspectRatios",
            "supported_aspect_ratios",
            "supportedAspectRatios",
            "ratios",
        ),
        _ASPECT_RATIO,
    )
    # 比例从哪来必须能说清：上游自己写了比例枚举，还是只用精确尺寸暗示了比例。
    # 两者都是上游声明，但证据强度不同，节点目录要能分开标。
    aspect_ratio_source = "catalog" if aspects else ""
    if not aspects and size_slots:
        # Exact WxH outputs imply a ratio. Keep the provider size slots in the
        # execution contract, while publishing only the ratio control to the
        # canvas.
        aspect_values: list[str] = []
        for slot in size_slots:
            match = re.fullmatch(
                r"([1-9][0-9]{1,4})x([1-9][0-9]{1,4})", slot, re.IGNORECASE
            )
            if not match:
                continue
            width, height = (int(value) for value in match.groups())
            divisor = gcd(width, height)
            if divisor:
                ratio = f"{width // divisor}:{height // divisor}"
                if ratio not in aspect_values:
                    aspect_values.append(ratio)
        aspects = aspect_values
        if aspects:
            aspect_ratio_source = "catalog-size-slots"
    duration_range = _duration_range(sources)
    duration_fields = (
        "duration_range",
        "durationRange",
        "min_duration",
        "minDuration",
        "max_duration",
        "maxDuration",
    )
    duration_inferred_from_description = bool(duration_range) and not any(
        key in source for source in sources for key in duration_fields
    )
    duration_options = _duration_options(sources)
    fps_options = _fps_options(sources)
    input_slots = _input_slots(sources)
    return_last_frame = _metadata_bool(
        sources,
        "return_last_frame",
        "returnLastFrame",
        "supports_return_last_frame",
        "supportsReturnLastFrame",
    )
    supports_custom_duration = _metadata_bool(
        sources,
        "supports_custom_duration",
        "supportsCustomDuration",
        "supports_arbitrary_duration",
        "supportsArbitraryDuration",
    )
    native_audio = _native_audio(sources, modalities)
    audio_input_semantics = _audio_input_semantics(sources)
    reference_limits, reference_limits_known = _reference_limits(sources, modes)
    supports_custom_aspect = _metadata_bool(
        sources,
        "supports_custom_aspect_ratio",
        "supportsCustomAspectRatio",
        "supports_arbitrary_aspect_ratio",
        "supportsArbitraryAspectRatio",
    )
    supports_custom_resolution = _metadata_bool(
        sources,
        "supports_custom_resolution",
        "supportsCustomResolution",
        "supports_arbitrary_resolution",
        "supportsArbitraryResolution",
        "supports_any_size",
        "supportsAnySize",
    )

    # Presence is part of the contract.  An explicit empty enum means the
    # upstream has ruled that capability out; it must not be replaced by the
    # model-name profile downstream.
    declared_capabilities: list[str] = []
    for capability_name, keys in {
        "modes": ("modes", "supported_modes", "supportedModes"),
        "sizeSlots": (
            "size_options",
            "sizeOptions",
            "supported_sizes",
            "supportedSizes",
            "sizes",
        ),
        "resolutionOptions": (
            "resolution_options",
            "resolutionOptions",
            "supported_resolutions",
            "supportedResolutions",
            "supported_sizes",
            "supportedSizes",
            "resolutions",
        ),
        "aspectRatios": (
            "aspect_ratios",
            "aspectRatios",
            "supported_aspect_ratios",
            "supportedAspectRatios",
            "ratios",
        ),
        "durationRange": ("duration_range", "durationRange"),
        "durationOptions": (
            "duration_options",
            "durationOptions",
            "supported_durations",
            "supportedDurations",
            "durations",
        ),
        "fpsOptions": (
            "fps_options",
            "fpsOptions",
            "supported_fps",
            "supportedFps",
            "frame_rates",
            "frameRates",
            "fps",
        ),
        "inputSlots": ("input_slots", "inputSlots"),
        "returnLastFrame": (
            "return_last_frame",
            "returnLastFrame",
            "supports_return_last_frame",
            "supportsReturnLastFrame",
        ),
        "supportsCustomDuration": (
            "supports_custom_duration",
            "supportsCustomDuration",
            "supports_arbitrary_duration",
            "supportsArbitraryDuration",
        ),
    }.items():
        if (
            any(key in source for source in sources for key in keys)
            or (
                capability_name == "durationRange"
                and duration_inferred_from_description
            )
        ):
            declared_capabilities.append(capability_name)

    result: dict[str, Any] = {
        "source": "models-metadata",
        "verificationStatus": "metadata"
        if any(
            (
                modes,
                size_slots,
                resolutions,
                aspects,
                duration_range,
                duration_options,
                fps_options,
                input_slots,
                native_audio,
            )
        )
        else "directory-only",
        "inputModalities": modalities,
        "declaredCapabilities": declared_capabilities,
    }
    if audio_input_semantics:
        result["audioInputSemantics"] = audio_input_semantics
        result["audio_input_semantics"] = audio_input_semantics
    if modes:
        result["modes"] = modes
    size_slot_declared = "sizeSlots" in declared_capabilities
    if size_slot_declared:
        result["sizeSlots"] = size_slots
        result["sizeField"] = str(
            _first_value(sources, "size_field", "sizeField") or "size"
        ).strip()
    elif size_slots:
        result["sizeSlots"] = size_slots
        result["sizeField"] = str(
            _first_value(sources, "size_field", "sizeField") or "size"
        ).strip()
    if resolutions:
        result["resolutionOptions"] = resolutions
        result["resolutionSource"] = "catalog"
    if aspects:
        result["aspectRatios"] = aspects
        result["aspectRatioSource"] = aspect_ratio_source
    if duration_range:
        result["durationRange"] = duration_range
    if "durationOptions" in declared_capabilities:
        result["durationOptions"] = duration_options
    if "fpsOptions" in declared_capabilities:
        result["fpsOptions"] = fps_options
    if "inputSlots" in declared_capabilities:
        result["inputSlots"] = input_slots
    if return_last_frame is not None:
        result["returnLastFrame"] = return_last_frame
    if supports_custom_duration is not None:
        result["supportsCustomDuration"] = supports_custom_duration
    if native_audio:
        result["nativeAudio"] = native_audio
    if supports_custom_aspect is not None:
        result["supportsCustomAspectRatio"] = supports_custom_aspect
    if supports_custom_resolution is not None:
        result["supportsCustomResolution"] = supports_custom_resolution
    if reference_limits:
        result["referenceLimits"] = reference_limits
    if reference_limits_known:
        result["referenceLimitsKnown"] = sorted(reference_limits_known)
    # Keep the normalized public fields above for old callers, and attach a
    # lossless envelope for provider-specific controls.  The envelope is pure
    # data transformation: it performs no network call and never carries keys.
    from .video_capability_envelope import build_video_capability_envelope

    envelope = build_video_capability_envelope(entry, capability=result)
    # Preserve the historical compact capability shape when the upstream
    # entry exposes only the legacy common fields.  A non-empty envelope is
    # attached additively as soon as provider-specific controls are present.
    if any(
        envelope.get(key)
        for key in ("parameters", "providerMapping", "mediaInputs", "opaque")
    ):
        result.update(envelope)
    return result


def _safe_model_metadata(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only capability-like fields from a video catalog entry."""

    safe_keys = (
        "displayName",
        "description",
        "tags",
        "capabilities",
        "input_modalities",
        "output_modalities",
        "supported_protocols",
        "supportedProtocols",
        "supported_modes",
        "supportedModes",
        "size_options",
        "sizeOptions",
        "resolution_options",
        "resolutionOptions",
        "aspect_ratios",
        "aspectRatios",
        "duration_range",
        "durationRange",
        "native_audio",
        "nativeAudio",
        "audio_input_semantics",
        "audioInputSemantics",
        "audio_reference_semantics",
        "audioReferenceSemantics",
        "supports_custom_aspect_ratio",
        "supportsCustomAspectRatio",
        "supports_arbitrary_aspect_ratio",
        "supportsArbitraryAspectRatio",
        "supports_custom_resolution",
        "supportsCustomResolution",
        "supports_arbitrary_resolution",
        "supportsArbitraryResolution",
        "supports_any_size",
        "supportsAnySize",
        "modes",
        "generationModes",
        "generation_modes",
        "inputSlots",
        "input_slots",
        "parameters",
        "parameterSchema",
        "parameter_schema",
        "inputSchema",
        "input_schema",
        "requestSchema",
        "request_schema",
        "providerMapping",
        "provider_mapping",
        "mapping",
        "outputFormats",
        "output_formats",
        "qualityOptions",
        "quality_options",
        "supportedQualities",
        "supported_qualities",
        "supportedDurations",
        "supported_durations",
        "durationOptions",
        "duration_options",
        "fpsOptions",
        "fps_options",
        "supportedFps",
        "supported_fps",
        "frameRates",
        "frame_rates",
        "minDuration",
        "min_duration",
        "maxDuration",
        "max_duration",
        "supportsCustomDuration",
        "supports_custom_duration",
        "supportsArbitraryDuration",
        "supports_arbitrary_duration",
        "returnLastFrame",
        "return_last_frame",
        "supportsReturnLastFrame",
        "supports_return_last_frame",
    )
    result = {key: entry[key] for key in safe_keys if key in entry}
    # Providers frequently add a capability field before the adapter knows its
    # spelling. Preserve those fields in metadata; credential-like keys remain
    # excluded so the browser-safe catalog contract is unchanged.
    capability_hints = (
        "mode",
        "resolution",
        "size",
        "aspect",
        "ratio",
        "duration",
        "audio",
        "image",
        "video",
        "reference",
        "format",
        "quality",
        "seed",
        "frame",
        "motion",
        "camera",
        "parameter",
        "schema",
    )
    for key, value in entry.items():
        lowered = str(key).casefold()
        if key in result or _SENSITIVE_METADATA_KEY.match(
            re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
        ):
            continue
        if any(token in lowered for token in capability_hints):
            result[key] = value
    return result


def _metadata_sources(entry: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    sources: list[Mapping[str, Any]] = [entry]
    for key in ("capabilities", "metadata", "video"):
        nested = entry.get(key)
        if isinstance(nested, Mapping):
            sources.append(nested)
    return sources


def _first_value(sources: Iterable[Mapping[str, Any]], *keys: str) -> Any:
    for source in sources:
        for key in keys:
            if key in source and source[key] is not None:
                return source[key]
    return None


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,\s]+", value) if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _lower_strings(value: Any) -> list[str]:
    return list(dict.fromkeys(item.lower() for item in _strings(value)))


def _matching_strings(value: Any, pattern: re.Pattern[str]) -> list[str]:
    return list(
        dict.fromkeys(
            item.lower().replace("×", "x")
            for item in _strings(value)
            if pattern.fullmatch(item.replace("×", "x"))
        )
    )


def _infer_modes(
    tags: list[str], modalities: list[str], explicit_modes: list[str]
) -> list[str]:
    material = set(tags) | set(explicit_modes)
    modes: list[str] = []

    def add(mode: str) -> None:
        if mode not in modes:
            modes.append(mode)

    if (
        material & {"text-to-video", "text_to_video", "texttovideo", "t2v"}
        or "text" in modalities
    ):
        add("textToVideo")
    if (
        material & {"image-to-video", "image_to_video", "imagetovideo", "i2v"}
        or "image" in modalities
    ):
        add("imageToVideo")
    if material & {"first-last-frame", "first_last_frame", "firstlastframe", "flf"}:
        add("firstLastFrame")
    if material & {
        "multi-reference",
        "multi_reference",
        "reference-to-video",
        "reference_to_video",
        "r2v",
    }:
        add("allReference")
    if (
        material & {"video-to-video", "video_to_video", "videoedit", "v2v"}
        or "video" in modalities
    ):
        add("videoEdit")
    return modes


def _duration_range(sources: list[Mapping[str, Any]]) -> list[int]:
    raw = _first_value(sources, "duration_range", "durationRange")
    values = _strings(raw)
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        values = [str(raw[0]), str(raw[1])]
    if len(values) >= 2:
        try:
            low, high = int(float(values[0])), int(float(values[1]))
            if 1 <= low <= high <= 300:
                return [low, high]
        except (TypeError, ValueError):
            pass
    raw_min = _first_value(sources, "min_duration", "minDuration")
    raw_max = _first_value(sources, "max_duration", "maxDuration")
    try:
        low, high = int(float(raw_min)), int(float(raw_max))
    except (TypeError, ValueError):
        low = high = 0
    if 1 <= low <= high <= 300:
        return [low, high]

    # A few relays expose a precise fixed duration in ``description`` while
    # omitting structured duration fields. Keep this inference deliberately
    # narrow so prose such as "up to 30 seconds" is not a false contract.
    for source in sources:
        description = source.get("description")
        if not isinstance(description, str):
            continue
        match = _FIXED_DURATION_DESCRIPTION.search(description)
        if not match:
            continue
        try:
            seconds = float(match.group(1))
        except (TypeError, ValueError):
            continue
        if seconds.is_integer() and 1 <= seconds <= 300:
            fixed = int(seconds)
            return [fixed, fixed]
    return []


def _duration_options(sources: list[Mapping[str, Any]]) -> list[int]:
    """Normalize discrete duration declarations without inventing values."""

    raw = _first_value(
        sources,
        "duration_options",
        "durationOptions",
        "supported_durations",
        "supportedDurations",
        "durations",
    )
    values: list[int] = []
    for item in _strings(raw):
        try:
            value = int(round(float(item)))
        except (TypeError, ValueError):
            continue
        if 1 <= value <= 300 and value not in values:
            values.append(value)
    return sorted(values)


def _fps_options(sources: list[Mapping[str, Any]]) -> list[int | float]:
    """Normalize frame-rate options while retaining fractional rates."""

    raw = _first_value(
        sources,
        "fps_options",
        "fpsOptions",
        "supported_fps",
        "supportedFps",
        "frame_rates",
        "frameRates",
        "fps",
    )
    values: list[int | float] = []
    for item in _strings(raw):
        try:
            parsed = float(item)
        except (TypeError, ValueError):
            continue
        if not 1 <= parsed <= 240:
            continue
        value: int | float = int(parsed) if parsed.is_integer() else round(parsed, 3)
        if value not in values:
            values.append(value)
    return sorted(values, key=float)


def _input_slots(sources: list[Mapping[str, Any]]) -> list[str]:
    """Project provider input-slot declarations to stable string identifiers."""

    raw = _first_value(sources, "input_slots", "inputSlots")
    if raw is None:
        raw = _first_value(sources, "media_inputs", "mediaInputs")
    if isinstance(raw, Mapping):
        raw = list(raw.values())
    values: list[str] = []
    for item in raw if isinstance(raw, (list, tuple, set)) else _strings(raw):
        if isinstance(item, Mapping):
            value = next(
                (
                    item.get(key)
                    for key in ("slot", "name", "id", "key", "type", "kind")
                    if item.get(key) is not None
                ),
                "",
            )
        else:
            value = item
        normalized = str(value or "").strip()
        if normalized and normalized not in values:
            values.append(normalized)
    return values


def _native_audio(sources: list[Mapping[str, Any]], modalities: list[str]) -> str:
    raw = _first_value(
        sources, "native_audio", "nativeAudio", "supports_audio", "supportsAudio"
    )
    if isinstance(raw, bool):
        return "optional" if raw else "unsupported"
    normalized = str(raw or "").strip().lower()
    if normalized in {"required", "optional", "unsupported"}:
        return normalized
    return "optional" if "audio" in modalities else ""


def _audio_input_semantics(sources: Iterable[Mapping[str, Any]]) -> list[str]:
    """Normalize explicit input-audio meaning without guessing from modality."""

    raw = _first_value(
        list(sources),
        "audio_input_semantics",
        "audioInputSemantics",
        "audio_reference_semantics",
        "audioReferenceSemantics",
    )
    values = (raw,) if isinstance(raw, str) else raw if isinstance(raw, (list, tuple, set)) else ()
    allowed = {"driving_audio", "voice_profile", "soundtrack", "audio_prompt"}
    result: list[str] = []
    for value in values:
        item = str(value or "").strip().casefold().replace("-", "_")
        if item in allowed and item not in result:
            result.append(item)
    return result


def _metadata_bool(sources: Iterable[Mapping[str, Any]], *keys: str) -> bool | None:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if isinstance(value, bool):
                return value
    return None


def _reference_limits(
    sources: list[Mapping[str, Any]], modes: list[str]
) -> tuple[dict[str, int], set[str]]:
    raw = _first_value(sources, "reference_limits", "referenceLimits")
    raw_mapping = raw if isinstance(raw, Mapping) else {}

    known: set[str] = set()

    def number(name: str, *keys: str) -> int:
        value = _first_value([raw_mapping, *sources], *keys)
        if value is None:
            return 0
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 0
        known.add(name)
        return max(0, min(parsed, 100))

    limits = {
        "inputImages": number("inputImages",
            "input_images", "inputImages", "max_input_images", "maxInputImages"
        ),
        "referenceImages": number("referenceImages",
            "reference_images",
            "referenceImages",
            "max_reference_images",
            "maxReferenceImages",
        ),
        "referenceVideos": number("referenceVideos",
            "reference_videos",
            "referenceVideos",
            "max_reference_videos",
            "maxReferenceVideos",
        ),
        "referenceAudios": number("referenceAudios",
            "reference_audios",
            "referenceAudios",
            "max_reference_audios",
            "maxReferenceAudios",
        ),
    }
    if "imageToVideo" in modes:
        limits["inputImages"] = max(1, limits["inputImages"])
    if "firstLastFrame" in modes:
        limits["inputImages"] = max(2, limits["inputImages"])
    if "videoEdit" in modes:
        if "referenceVideos" not in known:
            limits["referenceVideos"] = 1
    return (limits if known or any(limits.values()) else {}), known
