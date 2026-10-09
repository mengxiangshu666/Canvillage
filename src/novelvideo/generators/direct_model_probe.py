"""Non-billing connectivity probes for direct non-video model APIs."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Mapping
from urllib.parse import quote
from urllib.parse import urlparse

from novelvideo.generators.direct_model_capabilities import (
    DIRECT_MODEL_PROTOCOL_CUSTOM_HTTP,
    DIRECT_MODEL_PROTOCOL_GEMINI,
    DIRECT_MODEL_PROTOCOL_OLLAMA_OPENAI,
    DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE,
    infer_direct_model_protocol,
    normalize_direct_model_base_url,
    normalize_direct_model_protocol,
)
from novelvideo.generators.model_contracts import (
    DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES,
    DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI,
    DIRECT_MODEL_PROTOCOL_GEMINI_IMAGE,
    get_model_contract,
    join_contract_endpoint,
)
from novelvideo.generators.direct_model_capability_cache import (
    PROBE_CONTRACT_VERSIONS,
)


# Bump when the chat/embedding probe contract changes. Cached failures from
# older probes must not permanently disable a model after compatibility fixes.
# Chat is one family: agent/text/vision rows share this contract version.
CHAT_PROBE_CONTRACT_VERSION = PROBE_CONTRACT_VERSIONS["chat"]
EMBEDDING_PROBE_CONTRACT_VERSION = PROBE_CONTRACT_VERSIONS["embedding"]
# Reasoning models can spend dozens of tokens before their first visible
# answer.  A tiny probe budget made a working Gemini route look empty, while a
# 10-second socket cap cut off otherwise healthy non-streaming completions.
CHAT_STREAM_PROBE_MAX_TOKENS = 64
CHAT_PROBE_MAX_TOKENS = 256
CHAT_PROBE_TIMEOUT_CEILING_SECONDS = 75.0
_CHAT_PROBE_REASONING_NONE_PREFIXES = ("gemini-3",)
_SENSITIVE_METADATA_KEY = re.compile(
    r"^(?:(?:api|access|refresh|auth|authorization|private|secret|session)[_-]?)?"
    r"(?:key|token|password|cookie|authorization)(?:$|[_-])",
    re.IGNORECASE,
)


def _is_autodl_base_url(base_url: str) -> bool:
    host = str(urlparse(str(base_url or "")).hostname or "").lower()
    return host == "autodl.art" or host.endswith(".autodl.art")


def _autodl_workflow_endpoint(base_url: str, workflow_id: str) -> str:
    base = normalize_direct_model_base_url(base_url).rstrip("/")
    return f"{base}/api/v1/comfyui/workflows/{quote(str(workflow_id).strip(), safe='')}"


def _autodl_json_value(value: object, *, depth: int = 0) -> object:
    if depth > 5:
        return "[truncated]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {
            str(key): _autodl_json_value(item, depth=depth + 1)
            for key, item in list(value.items())[:128]
            if not _SENSITIVE_METADATA_KEY.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_autodl_json_value(item, depth=depth + 1) for item in list(value)[:128]]
    return str(value)


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
    if "audio" in lowered or "voice" in lowered:
        return "audio"
    return "string"


def _autodl_audio_capability(workflow_id: str, workflow: Mapping[str, Any]) -> dict[str, Any]:
    """Convert AutoDL's workflow input_rules into the shared audio contract."""

    raw_rules = workflow.get("input_rules")
    input_rules = raw_rules if isinstance(raw_rules, Mapping) else {}
    parameters: list[dict[str, Any]] = []
    provider_mapping: dict[str, Any] = {}
    media_inputs: list[dict[str, Any]] = []
    input_slots = ["text"]
    parameter_defaults: dict[str, Any] = {}
    audio_limit = 0
    text_declared = False
    voice_field = ""
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
            "advanced": key not in {"prompt_text", "prompt_simple", "text"},
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
            labels = [
                str(option.get("label") or "").strip()
                for option in options[:100]
                if isinstance(option, Mapping) and str(option.get("label") or "").strip()
            ]
            if labels:
                descriptor["enum"] = labels
                descriptor["options"] = [
                    {
                        "label": label,
                        **(
                            {"values": _autodl_json_value(option.get("values"))}
                            if isinstance(option, Mapping) and isinstance(option.get("values"), Mapping)
                            else {}
                        ),
                    }
                    for option, label in zip(options[:100], labels)
                ]
        accept_types = rule.get("accept_types")
        if isinstance(accept_types, list):
            descriptor["acceptTypes"] = [str(item).strip()[:80] for item in accept_types[:16] if str(item).strip()]
        parameters.append(descriptor)
        provider_mapping[key] = key
        if rule.get("default") is not None:
            parameter_defaults[key] = _autodl_json_value(rule["default"])
        lower_key = key.casefold()
        if lower_key in {"prompt_text", "text", "input_text", "prompt"}:
            text_declared = True
            provider_mapping.setdefault("text", key)
        if lower_key in {"prompt_simple", "voice_reference", "reference_audio", "speaker_audio"} or (
            type_name == "audio" and not voice_field
        ):
            voice_field = key
            provider_mapping.setdefault("voice_reference", {"field": key, "transport": "url"})
            input_slots.append("voice_reference")
        if type_name == "audio":
            audio_limit += 1
            media_inputs.append(
                {
                    "key": key,
                    "providerKey": key,
                    "type": "audio",
                    "required": bool(rule.get("required", False)),
                    "acceptTypes": descriptor.get("acceptTypes", []),
                    "source": "workflow_schema",
                }
            )
    if not text_declared:
        # A workflow with no named text rule cannot be safely invoked as TTS.
        input_slots = []
    # ``prompt_simple`` is the required speaker/voice reference in the
    # IndexTTS2 workflow.  ``emo_ref_audio`` is an optional second audio slot
    # and must not replace the primary voice binding merely because it appears
    # earlier in the provider's rule ordering.
    preferred_voice_field = next(
        (
            candidate
            for candidate in ("prompt_simple", "voice_reference", "reference_audio", "speaker_audio")
            if candidate in input_rules
        ),
        voice_field,
    )
    if preferred_voice_field:
        voice_field = preferred_voice_field
        provider_mapping["voice_reference"] = {
            "field": preferred_voice_field,
            "transport": "url",
        }
        if "voice_reference" not in input_slots:
            input_slots.append("voice_reference")
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
        "supportedModes": ["text_to_speech"] if text_declared else [],
        "inputSlots": list(dict.fromkeys(input_slots)),
        "referenceLimits": {"text_to_speech": {"audio": 1 if voice_field else 0}},
        "parameters": parameters[:128],
        "providerMapping": provider_mapping,
        "mediaInputs": media_inputs[:64],
        "media_inputs": media_inputs[:64],
        "parameterDefaults": parameter_defaults,
        "parameterSchema": {
            item["key"]: {
                key: item[key]
                for key in ("type", "default", "minimum", "maximum", "enum")
                if key in item
            }
            for item in parameters
        },
        "audioFormats": [
            mime
            for item in media_inputs
            for mime in item.get("acceptTypes", [])
            if mime
        ],
        "adapterFamily": DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI,
        "adapterConfidence": 1.0,
        "adapterEvidence": {
            "family": DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI,
            "confidence": 1.0,
            "sources": ["workflow-schema"],
            "matchedPaths": ["/api/v1/comfyui/workflows/{workflow_id}"],
            "resolved": True,
        },
    }
    return capability


def _read_autodl_audio_workflow(
    *, base_url: str, api_key: str, workflow_id: str, timeout: float
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    workflow_id = str(workflow_id or "").strip()
    if not workflow_id:
        return {}, _probe_failure("AutoDL audio workflow id is required", DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI)
    try:
        import httpx

        endpoint = _autodl_workflow_endpoint(base_url, workflow_id)
        with httpx.Client(timeout=max(0.5, min(float(timeout), 10.0)), follow_redirects=True) as client:
            response = client.get(
                endpoint,
                headers={
                    "Authorization": str(api_key or "").strip(),
                    "Accept": "application/json",
                },
            )
        if response.status_code >= 400:
            return {}, _probe_failure(
                f"AutoDL Workflow 元数据请求失败（HTTP {response.status_code}）",
                DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI,
                http_status=response.status_code,
            )
        payload = response.json()
    except (OSError, TimeoutError) as exc:
        return {}, _probe_failure(f"AutoDL Workflow 元数据网络失败（{type(exc).__name__}）", DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}, _probe_failure("AutoDL Workflow 元数据不是有效 JSON", DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI)
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping):
        return {}, _probe_failure("AutoDL Workflow 元数据缺少 data", DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI)
    capability = _autodl_audio_capability(workflow_id, data)
    return capability, None


def discover_direct_models(
    *,
    base_url: str,
    api_key: str,
    protocol: str,
    kind: str,
    timeout: float = 7.0,
) -> dict[str, Any]:
    """Read one upstream model directory without invoking any model."""

    normalized_kind = str(kind or "").strip().lower()
    normalized_protocol = normalize_direct_model_protocol(protocol)
    if normalized_kind == "audio" and (
        normalized_protocol == DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI
        or _is_autodl_base_url(base_url)
    ):
        workflow_id = "indextts2-v1"
        capability, failure = _read_autodl_audio_workflow(
            base_url=base_url,
            api_key=api_key,
            workflow_id=workflow_id,
            timeout=timeout,
        )
        if failure is not None:
            return {**failure, "models": []}
        return {
            "ok": True,
            "models": [{"id": workflow_id, "metadata": capability}],
            "discoveredModelCount": 1,
            "protocol": DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI,
            "detectedProtocol": DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI,
        }

    normalized_protocol, payload, failure = _read_model_catalog(
        upstream_model="",
        base_url=base_url,
        api_key=api_key,
        protocol=protocol,
        kind=kind,
        timeout=timeout,
    )
    if failure is not None:
        return {**failure, "models": []}
    _discovered, metadata, public_models = _model_catalog(
        payload, normalized_protocol
    )
    models = _public_model_catalog(public_models, metadata, kind=kind)
    if not models:
        return {
            "ok": False,
            "models": [],
            "discoveredModelCount": 0,
            "protocol": normalized_protocol,
            "detectedProtocol": normalized_protocol,
            "errorCode": "empty-model-catalog",
            "error": "/models 已响应，但没有返回可导入的模型。",
        }
    return {
        "ok": True,
        "models": models,
        "discoveredModelCount": len(models),
        "protocol": normalized_protocol,
        "detectedProtocol": normalized_protocol,
    }


def probe_direct_model_endpoint(
    *,
    upstream_model: str,
    base_url: str,
    api_key: str,
    protocol: str,
    kind: str | None = None,
    timeout: float = 90.0,
    declared_capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate model-list connectivity without submitting generation work.

    ``declared_capabilities`` (supportsTools / supportsVision) asks the probe to
    produce *evidence* for those claims.  Failing them never fails the model:
    the row stays usable and only loses the capability badge.
    """

    requested_protocol = normalize_direct_model_protocol(protocol)
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind == "audio" and (
        requested_protocol == DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI
        or _is_autodl_base_url(base_url)
    ):
        detected = DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI
        capability, failure = _read_autodl_audio_workflow(
            base_url=base_url,
            api_key=api_key,
            workflow_id=upstream_model,
            timeout=timeout,
        )
        if failure is not None:
            return failure
        model_found = bool(capability.get("inputSlots"))
        return {
            "ok": model_found,
            "modelFound": model_found,
            "discoveredModelCount": 1 if model_found else 0,
            "protocol": detected,
            "detectedProtocol": detected,
            "models": [{"id": upstream_model, "metadata": capability}] if model_found else [],
            "modelMetadata": capability,
            "verificationStatus": "contract-resolved" if model_found else "degraded",
            **({} if model_found else {"error": "AutoDL Workflow 未声明可用的文本或参考音频输入"}),
        }
    normalized_protocol, payload, failure = _read_model_catalog(
        upstream_model=upstream_model,
        base_url=base_url,
        api_key=api_key,
        protocol=requested_protocol,
        kind=str(kind or ""),
        timeout=timeout,
    )
    if failure is not None:
        return failure

    discovered, metadata, public_models = _model_catalog(payload, normalized_protocol)
    wanted = str(upstream_model or "").strip()
    image_schema_probe: dict[str, Any] = {}
    if normalized_kind == "image":
        from novelvideo.generators.direct_image_openapi_discovery import (
            discover_image_openapi,
        )

        image_schema_probe = discover_image_openapi(
            base_url=base_url,
            api_key=api_key,
            protocol=normalized_protocol,
            timeout=min(float(timeout), 3.0),
        )
        discovered_ratios = image_schema_probe.get("aspectRatioOptions")
        if isinstance(discovered_ratios, list) and discovered_ratios:
            target_metadata = metadata.setdefault(wanted, {})
            target_metadata["supported_aspect_ratios"] = discovered_ratios
    model_catalog_found = wanted in discovered
    model_found = model_catalog_found and _model_is_compatible_with_kind(
        wanted,
        metadata.get(wanted) or metadata.get(f"models/{wanted}") or {},
        str(kind or ""),
    )
    chat_probe: dict[str, Any] = {}
    embedding_probe: dict[str, Any] = {}
    # The upstream catalog is advisory, not authoritative: preview/experimental
    # models (and some relay catalogs) are callable without being listed, so a
    # missing id must never skip the probe that would prove the row works.
    if (
        normalized_kind in {"chat", "agent", "text", "vision"}
        and get_model_contract(normalized_protocol).runtime_ready(normalized_kind)
    ):
        chat_probe = _probe_direct_chat_contract(
            upstream_model=wanted,
            base_url=base_url,
            api_key=api_key,
            protocol=normalized_protocol,
            kind=normalized_kind,
            timeout=timeout,
        )
        declared = declared_capabilities or {}
        if normalized_protocol in {
            DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE,
            DIRECT_MODEL_PROTOCOL_OLLAMA_OPENAI,
        }:
            if declared.get("supportsTools"):
                chat_probe.update(
                    _probe_agent_tool_contract(
                        upstream_model=wanted,
                        base_url=base_url,
                        api_key=api_key,
                        protocol=normalized_protocol,
                        timeout=timeout,
                    )
                )
            if declared.get("supportsVision"):
                chat_probe.update(
                    _probe_openai_vision_contract(
                        upstream_model=wanted,
                        base_url=base_url,
                        api_key=api_key,
                        protocol=normalized_protocol,
                        timeout=timeout,
                    )
                )
    if (
        normalized_kind == "embedding"
        and get_model_contract(normalized_protocol).runtime_ready(normalized_kind)
    ):
        embedding_probe = _probe_embedding_contract(
            upstream_model=wanted,
            base_url=base_url,
            api_key=api_key,
            protocol=normalized_protocol,
            timeout=timeout,
        )
    contract_ready = (
        True
        if not normalized_kind
        else get_model_contract(normalized_protocol).runtime_ready(normalized_kind)
    )
    if not normalized_kind:
        chat_ok = True
    elif normalized_kind == "agent":
        # Hermes requires all three contracts; a plain non-streaming chat
        # response is not enough to mark an Agent model usable.
        chat_ok = contract_ready and chat_probe.get("hermesProbeStatus") == "passed"
    elif normalized_kind in {"chat", "text", "vision"}:
        chat_ok = contract_ready and chat_probe.get("chatProbeStatus") == "passed" and (
            chat_probe.get("streamProbeStatus") == "passed"
        )
    elif normalized_kind == "embedding":
        chat_ok = contract_ready
    else:
        # Image/audio validation is deliberately catalog-only to avoid a
        # hidden billable generation.  The transport contract still has to
        # support the selected kind.
        chat_ok = contract_ready
    embedding_ok = (
        True
        if not normalized_kind
        else contract_ready
        and (
            normalized_kind != "embedding"
            or embedding_probe.get("embeddingProbeStatus") == "passed"
        )
    )
    catalog_ok = model_found or not discovered
    runtime_probe_passed = (
        chat_probe.get("hermesProbeStatus") == "passed"
        if normalized_kind == "agent"
        else chat_probe.get("chatProbeStatus") == "passed"
        and chat_probe.get("streamProbeStatus") == "passed"
        if normalized_kind in {"chat", "text", "vision"}
        else embedding_probe.get("embeddingProbeStatus") == "passed"
        if normalized_kind == "embedding"
        else False
    )
    # A usable response proves more than a catalog row, so a missing listing
    # only downgrades the evidence label instead of blocking the model.
    catalog_missing = bool(discovered) and not model_found
    if not chat_ok or not embedding_ok:
        verification_status = "degraded"
    elif runtime_probe_passed:
        verification_status = "runtime-verified"
    elif model_found:
        verification_status = "contract-resolved"
    else:
        verification_status = "metadata"
    result = {
        "ok": (catalog_ok or runtime_probe_passed) and chat_ok and embedding_ok,
        "modelFound": model_found,
        "catalogMissing": catalog_missing,
        "discoveredModelCount": len(discovered),
        "protocol": normalized_protocol,
        "verificationStatus": verification_status,
        "detectedProtocol": normalized_protocol,
        "modelMetadata": metadata.get(wanted) or metadata.get(f"models/{wanted}") or {},
        "models": _public_model_catalog(public_models, metadata, kind=normalized_kind),
        **({"imageSchemaProbe": image_schema_probe} if normalized_kind == "image" else {}),
        **chat_probe,
        **embedding_probe,
    }
    if model_catalog_found and not model_found:
        result["capabilityMismatch"] = True
        result["error"] = (
            f"模型 {wanted} 出现在上游目录，但其目录能力与当前 {normalized_kind or '模型'} 类型不匹配"
        )
    elif catalog_missing and runtime_probe_passed:
        result["catalogMissingNotice"] = (
            f"上游目录没有列出 {wanted}，但实测调用已通过；预览/实验模型常见这种情况。"
        )
    if normalized_kind in {"chat", "agent", "text", "vision"}:
        result["probeContractVersion"] = CHAT_PROBE_CONTRACT_VERSION
    elif normalized_kind == "embedding":
        result["probeContractVersion"] = EMBEDDING_PROBE_CONTRACT_VERSION
    if not chat_ok and normalized_kind in {"chat", "agent", "text", "vision"}:
        result["error"] = str(
            chat_probe.get("chatProbeError")
            or (
                "Agent Hermes 流式/工具合同未通过探测"
                if normalized_kind == "agent"
                else "文字/视觉 Chat 与流式合同未通过最小响应探测"
            )
        )
    if not embedding_ok and normalized_kind == "embedding":
        result["error"] = str(
            embedding_probe.get("embeddingProbeError")
            or "向量接口未通过最小执行探测"
        )
    if not contract_ready and not result.get("error"):
        result["error"] = (
            f"协议 {normalized_protocol} 当前不支持直连 {normalized_kind or '模型'} 执行合同"
        )
    return result


def _read_model_catalog(
    *,
    upstream_model: str,
    base_url: str,
    api_key: str,
    protocol: str,
    kind: str,
    timeout: float,
) -> tuple[str, object, dict[str, Any] | None]:
    normalized_protocol = normalize_direct_model_protocol(protocol)
    if normalized_protocol == "auto":
        normalized_protocol = infer_direct_model_protocol(
            str(kind or "").strip().lower(),
            upstream_model,
            base_url=base_url,
            requested_protocol=normalized_protocol,
        )
    if normalized_protocol == DIRECT_MODEL_PROTOCOL_CUSTOM_HTTP:
        return (
            normalized_protocol,
            {},
            _probe_failure(
                "custom-http protocol needs a request template",
                normalized_protocol,
            ),
        )
    try:
        endpoint, headers = _probe_request(base_url, api_key, normalized_protocol)
        import httpx

        from novelvideo.gateway_transport import newapi_httpx_client_kwargs

        client_kwargs = newapi_httpx_client_kwargs(
            base_url=base_url,
            timeout=max(1.0, float(timeout)),
        )
        with httpx.Client(**client_kwargs) as client:
            response = client.get(endpoint, headers=headers)
        if response.status_code >= 400:
            return (
                normalized_protocol,
                {},
                _probe_failure(
                    _http_error_message(response.status_code, response.text),
                    normalized_protocol,
                    http_status=response.status_code,
                ),
            )
        return normalized_protocol, response.json(), None
    except TimeoutError:
        return normalized_protocol, {}, _probe_failure(
            "请求超时：请检查上游地址、网络或网关响应速度。",
            normalized_protocol,
            http_status=408,
        )
    except OSError as exc:
        return normalized_protocol, {}, _probe_failure(
            f"网络连接失败（{type(exc).__name__}）。",
            normalized_protocol,
        )
    except ValueError:
        return normalized_protocol, {}, _probe_failure(
            "/models 已响应，但返回的不是有效 JSON。",
            normalized_protocol,
        )
    except Exception as exc:
        return normalized_protocol, {}, _probe_failure(
            _safe_exception_message(exc), normalized_protocol
        )


def _probe_agent_chat_contract(
    *,
    upstream_model: str,
    base_url: str,
    api_key: str,
    protocol: str,
    timeout: float,
) -> dict[str, Any]:
    """Verify basic chat plus the stream/tool contracts required by Hermes."""

    endpoint = join_contract_endpoint(
        normalize_direct_model_base_url(base_url),
        "/chat/completions",
    )
    payload = {
        "model": upstream_model,
        "messages": [{"role": "user", "content": "Reply with only OK."}],
        # Reasoning models may spend the first tokens in reasoning_content.
        "max_tokens": CHAT_PROBE_MAX_TOKENS,
        "temperature": 0,
        "stream": False,
    }
    started_at = time.perf_counter()
    try:
        import httpx

        from novelvideo.gateway_transport import newapi_httpx_client_kwargs

        client_kwargs = newapi_httpx_client_kwargs(
            base_url=base_url,
            timeout=max(
                1.0,
                min(float(timeout), CHAT_PROBE_TIMEOUT_CEILING_SECONDS),
            ),
        )
        headers = get_model_contract(protocol).auth.headers(api_key)
        with httpx.Client(**client_kwargs) as client:
            response = client.post(endpoint, headers=headers, json=payload)
    except Exception as exc:
        basic = {
            "chatProbeStatus": "probe-failed",
            "chatResponseUsable": False,
            "chatFirstTokenLatencyMs": round((time.perf_counter() - started_at) * 1000),
            "chatProbeError": _safe_exception_message(exc),
        }
        return {**basic, "hermesProbeStatus": "not-run"}

    elapsed_ms = round((time.perf_counter() - started_at) * 1000)
    if response.status_code >= 400:
        basic = {
            "chatProbeStatus": "rejected",
            "chatHttpStatus": response.status_code,
            "chatResponseUsable": False,
            "chatFirstTokenLatencyMs": elapsed_ms,
            "chatProbeError": _http_error_message(response.status_code, response.text),
        }
        return {**basic, "hermesProbeStatus": "not-run"}
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        body = None
    usable = _chat_response_has_usable_content(body)
    basic = {
        "chatProbeStatus": "passed" if usable else "probe-failed",
        "chatHttpStatus": response.status_code,
        "chatResponseUsable": usable,
        "chatFirstTokenLatencyMs": elapsed_ms,
        **({} if usable else {"chatProbeError": "Chat Completions 返回了不可用的响应结构"}),
    }
    if not usable:
        return {**basic, "hermesProbeStatus": "not-run"}

    stream = _probe_agent_stream_contract(
        upstream_model=upstream_model,
        base_url=base_url,
        api_key=api_key,
        protocol=protocol,
        timeout=timeout,
    )
    tool = _probe_agent_tool_contract(
        upstream_model=upstream_model,
        base_url=base_url,
        api_key=api_key,
        protocol=protocol,
        timeout=timeout,
    )
    hermes_ok = (
        stream.get("streamProbeStatus") == "passed"
        and tool.get("toolProbeStatus") == "passed"
    )
    return {
        **basic,
        **stream,
        **tool,
        "hermesProbeStatus": "passed" if hermes_ok else "degraded",
        **({}
           if hermes_ok
           else {"hermesProbeError": "流式输出或工具调用探测未通过，不能启动 Hermes 长任务"}),
    }


def _probe_direct_chat_contract(
    *,
    upstream_model: str,
    base_url: str,
    api_key: str,
    protocol: str,
    kind: str,
    timeout: float,
) -> dict[str, Any]:
    """Dispatch chat verification to the selected provider protocol."""

    if protocol == DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES:
        return _probe_anthropic_chat_contract(
            upstream_model=upstream_model,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
        )
    if protocol == DIRECT_MODEL_PROTOCOL_GEMINI:
        return _probe_gemini_chat_contract(
            upstream_model=upstream_model,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
        )
    if kind == "agent":
        return _probe_agent_chat_contract(
            upstream_model=upstream_model,
            base_url=base_url,
            api_key=api_key,
            protocol=protocol,
            timeout=timeout,
        )
    return _probe_openai_text_chat_contract(
        upstream_model=upstream_model,
        base_url=base_url,
        api_key=api_key,
        protocol=protocol,
        timeout=timeout,
    )


def _probe_openai_text_chat_contract(
    *,
    upstream_model: str,
    base_url: str,
    api_key: str,
    protocol: str,
    timeout: float,
) -> dict[str, Any]:
    """Verify non-Agent OpenAI-compatible chat and SSE without tool calls."""

    endpoint = join_contract_endpoint(
        normalize_direct_model_base_url(base_url),
        "/chat/completions",
    )
    base_payload = {
        "model": upstream_model,
        "messages": [{"role": "user", "content": "Reply with only OK."}],
        "temperature": 0,
    }
    stream_payload = {
        **base_payload,
        "max_tokens": CHAT_STREAM_PROBE_MAX_TOKENS,
        "stream": True,
    }
    if str(upstream_model or "").strip().lower().startswith(
        _CHAT_PROBE_REASONING_NONE_PREFIXES
    ):
        # Gemini 3 on OpenAI-compatible relays can spend more than a reverse
        # proxy's 60-second idle budget before the first SSE frame.  This is a
        # transport probe, not a reasoning benchmark; disable reasoning only
        # for the probe so a healthy stream is not misclassified as dead.
        stream_payload["reasoning_effort"] = "none"
    started_at = time.perf_counter()
    try:
        import httpx

        from novelvideo.gateway_transport import newapi_httpx_client_kwargs

        client_kwargs = newapi_httpx_client_kwargs(
            base_url=base_url,
            timeout=max(
                1.0,
                min(float(timeout), CHAT_PROBE_TIMEOUT_CEILING_SECONDS),
            ),
        )
        headers = get_model_contract(protocol).auth.headers(api_key)
        with httpx.Client(**client_kwargs) as client:
            # The product runtime consumes streaming chat completions.  Probe
            # that path first so reasoning relays are not misclassified just
            # because their non-streaming response is slow or disconnected.
            stream_started = time.perf_counter()
            try:
                with client.stream(
                    "POST",
                    endpoint,
                    headers={**headers, "Accept": "text/event-stream"},
                    json=stream_payload,
                ) as stream_response:
                    stream_status = stream_response.status_code
                    stream_usable = _stream_response_has_usable_content(
                        stream_response
                    )
                stream_evidence = {
                    "streamProbeStatus": (
                        "rejected"
                        if stream_status >= 400
                        else "passed"
                        if stream_usable
                        else "probe-failed"
                    ),
                    "streamHttpStatus": stream_status,
                    "streamResponseUsable": stream_usable,
                    "streamFirstEventLatencyMs": round(
                        (time.perf_counter() - stream_started) * 1000
                    ),
                    **(
                        {}
                        if stream_status < 400 and stream_usable
                        else {
                            "streamProbeError": (
                                _http_error_message(stream_status, "")
                                if stream_status >= 400
                                else "SSE 没有返回可用 assistant delta"
                            )
                        }
                    ),
                }
            except Exception as exc:
                stream_evidence = {
                    "streamProbeStatus": "probe-failed",
                    "streamResponseUsable": False,
                    "streamFirstEventLatencyMs": round(
                        (time.perf_counter() - stream_started) * 1000
                    ),
                    "streamProbeError": _safe_exception_message(exc),
                }

            if stream_evidence["streamProbeStatus"] == "passed":
                return {
                    "chatProbeStatus": "passed",
                    "chatProbeMode": "stream",
                    "chatHttpStatus": stream_evidence["streamHttpStatus"],
                    "chatResponseUsable": True,
                    "chatFirstTokenLatencyMs": stream_evidence[
                        "streamFirstEventLatencyMs"
                    ],
                    **stream_evidence,
                }

            # Some OpenAI-compatible relays do not implement SSE reliably but
            # still support ordinary JSON completions.  Keep that as a real
            # fallback instead of treating a transport-mode mismatch as a
            # dead model.
            response = client.post(
                endpoint,
                headers={**headers, "Accept": "application/json"},
                json={
                    **base_payload,
                    "max_tokens": CHAT_PROBE_MAX_TOKENS,
                    "stream": False,
                },
            )
            if response.status_code >= 400:
                return {
                    "chatProbeStatus": "rejected",
                    "chatProbeMode": "non-stream",
                    "chatHttpStatus": response.status_code,
                    "chatResponseUsable": False,
                    "chatFirstTokenLatencyMs": round((time.perf_counter() - started_at) * 1000),
                    "chatProbeError": _http_error_message(response.status_code, response.text),
                    **stream_evidence,
                }
            try:
                body = response.json()
            except (ValueError, json.JSONDecodeError):
                body = None
            usable = _chat_response_has_usable_content(body)
            basic = {
                "chatProbeStatus": "passed" if usable else "probe-failed",
                "chatProbeMode": "non-stream",
                "chatHttpStatus": response.status_code,
                "chatResponseUsable": usable,
                "chatFirstTokenLatencyMs": round((time.perf_counter() - started_at) * 1000),
            }
            if not usable:
                return {
                    **basic,
                    "chatProbeError": "Chat Completions 返回了不可用的响应结构",
                    **stream_evidence,
                }
            return {**basic, **stream_evidence}
    except Exception as exc:
        return {
            "chatProbeStatus": "probe-failed",
            "chatResponseUsable": False,
            "streamProbeStatus": "probe-failed",
            "streamResponseUsable": False,
            "chatFirstTokenLatencyMs": round((time.perf_counter() - started_at) * 1000),
            "chatProbeError": _safe_exception_message(exc),
            "streamProbeError": _safe_exception_message(exc),
        }


def _probe_anthropic_chat_contract(
    *,
    upstream_model: str,
    base_url: str,
    api_key: str,
    timeout: float,
) -> dict[str, Any]:
    endpoint = join_contract_endpoint(
        normalize_direct_model_base_url(base_url),
        "/messages",
    )
    payload = {
        "model": upstream_model,
        "max_tokens": CHAT_PROBE_MAX_TOKENS,
        "messages": [{"role": "user", "content": "Reply with only OK."}],
    }
    started_at = time.perf_counter()
    try:
        import httpx

        from novelvideo.gateway_transport import newapi_httpx_client_kwargs

        headers = {
            **get_model_contract(DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES).auth.headers(api_key),
            "Content-Type": "application/json",
        }
        client_kwargs = newapi_httpx_client_kwargs(
            base_url=base_url,
            timeout=max(
                1.0,
                min(float(timeout), CHAT_PROBE_TIMEOUT_CEILING_SECONDS),
            ),
        )
        with httpx.Client(**client_kwargs) as client:
            response = client.post(endpoint, headers=headers, json={**payload, "stream": False})
            if response.status_code >= 400:
                return {
                    "chatProbeStatus": "rejected",
                    "chatHttpStatus": response.status_code,
                    "chatResponseUsable": False,
                    "chatFirstTokenLatencyMs": round((time.perf_counter() - started_at) * 1000),
                    "chatProbeError": _http_error_message(response.status_code, response.text),
                }
            try:
                body = response.json()
            except (ValueError, json.JSONDecodeError):
                body = None
            usable = _anthropic_response_has_usable_content(body)
            basic = {
                "chatProbeStatus": "passed" if usable else "probe-failed",
                "chatHttpStatus": response.status_code,
                "chatResponseUsable": usable,
                "chatFirstTokenLatencyMs": round((time.perf_counter() - started_at) * 1000),
            }
            if not usable:
                return {**basic, "chatProbeError": "Anthropic Messages 返回了不可用的响应结构"}
            stream_started = time.perf_counter()
            with client.stream(
                "POST",
                endpoint,
                headers=headers,
                json={**payload, "stream": True},
            ) as stream_response:
                stream_status = stream_response.status_code
                stream_usable = _anthropic_stream_has_usable_content(stream_response)
            return {
                **basic,
                "streamProbeStatus": (
                    "rejected"
                    if stream_status >= 400
                    else "passed"
                    if stream_usable
                    else "probe-failed"
                ),
                "streamHttpStatus": stream_status,
                "streamResponseUsable": stream_usable,
                "streamFirstEventLatencyMs": round((time.perf_counter() - stream_started) * 1000),
                **(
                    {}
                    if stream_status < 400 and stream_usable
                    else {"streamProbeError": "Anthropic SSE 没有返回文本 delta"}
                ),
            }
    except Exception as exc:
        message = _safe_exception_message(exc)
        return {
            "chatProbeStatus": "probe-failed",
            "chatResponseUsable": False,
            "streamProbeStatus": "probe-failed",
            "streamResponseUsable": False,
            "chatProbeError": message,
            "streamProbeError": message,
        }


def _probe_gemini_chat_contract(
    *,
    upstream_model: str,
    base_url: str,
    api_key: str,
    timeout: float,
) -> dict[str, Any]:
    base = normalize_direct_model_base_url(base_url)
    model_path = quote(str(upstream_model or "").strip().removeprefix("models/"), safe="")
    model_endpoint = join_contract_endpoint(base, f"/models/{model_path}")
    endpoint = f"{model_endpoint}:generateContent"
    stream_endpoint = f"{model_endpoint}:streamGenerateContent?alt=sse"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": "Reply with only OK."}]}],
        "generationConfig": {
            "maxOutputTokens": CHAT_PROBE_MAX_TOKENS,
            "temperature": 0,
        },
    }
    started_at = time.perf_counter()
    try:
        import httpx

        from novelvideo.gateway_transport import newapi_httpx_client_kwargs

        headers = {
            **get_model_contract(DIRECT_MODEL_PROTOCOL_GEMINI).auth.headers(api_key),
            "Content-Type": "application/json",
        }
        client_kwargs = newapi_httpx_client_kwargs(
            base_url=base_url,
            timeout=max(
                1.0,
                min(float(timeout), CHAT_PROBE_TIMEOUT_CEILING_SECONDS),
            ),
        )
        with httpx.Client(**client_kwargs) as client:
            response = client.post(endpoint, headers=headers, json=payload)
            if response.status_code >= 400:
                return {
                    "chatProbeStatus": "rejected",
                    "chatHttpStatus": response.status_code,
                    "chatResponseUsable": False,
                    "chatFirstTokenLatencyMs": round((time.perf_counter() - started_at) * 1000),
                    "chatProbeError": _http_error_message(response.status_code, response.text),
                }
            try:
                body = response.json()
            except (ValueError, json.JSONDecodeError):
                body = None
            usable = _gemini_response_has_usable_content(body)
            basic = {
                "chatProbeStatus": "passed" if usable else "probe-failed",
                "chatHttpStatus": response.status_code,
                "chatResponseUsable": usable,
                "chatFirstTokenLatencyMs": round((time.perf_counter() - started_at) * 1000),
            }
            if not usable:
                return {**basic, "chatProbeError": "Gemini generateContent 返回了不可用的响应结构"}
            stream_started = time.perf_counter()
            with client.stream("POST", stream_endpoint, headers=headers, json=payload) as stream_response:
                stream_status = stream_response.status_code
                stream_usable = _gemini_stream_has_usable_content(stream_response)
            return {
                **basic,
                "streamProbeStatus": (
                    "rejected"
                    if stream_status >= 400
                    else "passed"
                    if stream_usable
                    else "probe-failed"
                ),
                "streamHttpStatus": stream_status,
                "streamResponseUsable": stream_usable,
                "streamFirstEventLatencyMs": round((time.perf_counter() - stream_started) * 1000),
                **(
                    {}
                    if stream_status < 400 and stream_usable
                    else {"streamProbeError": "Gemini SSE 没有返回可用候选文本"}
                ),
            }
    except Exception as exc:
        message = _safe_exception_message(exc)
        return {
            "chatProbeStatus": "probe-failed",
            "chatResponseUsable": False,
            "streamProbeStatus": "probe-failed",
            "streamResponseUsable": False,
            "chatProbeError": message,
            "streamProbeError": message,
        }


def _anthropic_response_has_usable_content(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    content = payload.get("content")
    return isinstance(content, list) and any(
        isinstance(item, dict) and str(item.get("text") or "").strip()
        for item in content
    )


def _anthropic_stream_has_usable_content(response: Any) -> bool:
    for line in response.iter_lines():
        text = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line or "")
        if not text.strip().startswith("data:"):
            continue
        try:
            payload = json.loads(text.split(":", 1)[1].strip())
        except (ValueError, json.JSONDecodeError):
            continue
        delta = payload.get("delta") if isinstance(payload, dict) else None
        if isinstance(delta, dict) and str(delta.get("text") or "").strip():
            return True
        content_block = payload.get("content_block") if isinstance(payload, dict) else None
        if isinstance(content_block, dict) and str(content_block.get("text") or "").strip():
            return True
    return False


def _gemini_response_has_usable_content(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return False
    return any(
        isinstance(candidate, dict)
        and isinstance(candidate.get("content"), dict)
        and any(
            isinstance(part, dict) and str(part.get("text") or "").strip()
            for part in candidate["content"].get("parts") or []
        )
        for candidate in candidates
    )


def _gemini_stream_has_usable_content(response: Any) -> bool:
    for line in response.iter_lines():
        text = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line or "")
        if not text.strip().startswith("data:"):
            continue
        try:
            payload = json.loads(text.split(":", 1)[1].strip())
        except (ValueError, json.JSONDecodeError):
            continue
        if _gemini_response_has_usable_content(payload):
            return True
    return False


def _probe_embedding_contract(
    *,
    upstream_model: str,
    base_url: str,
    api_key: str,
    protocol: str,
    timeout: float,
) -> dict[str, Any]:
    """Verify the exact embedding route with one minimal provider request."""

    endpoint = join_contract_endpoint(
        normalize_direct_model_base_url(base_url),
        get_model_contract(protocol).endpoints.invoke_path,
    )
    payload = {
        "model": upstream_model,
        "input": ["health"],
    }
    started_at = time.perf_counter()
    try:
        import httpx

        from novelvideo.gateway_transport import newapi_httpx_client_kwargs

        client_kwargs = newapi_httpx_client_kwargs(
            base_url=base_url,
            timeout=max(
                1.0,
                min(float(timeout), CHAT_PROBE_TIMEOUT_CEILING_SECONDS),
            ),
        )
        headers = get_model_contract(protocol).auth.headers(api_key)
        with httpx.Client(**client_kwargs) as client:
            response = client.post(endpoint, headers=headers, json=payload)
    except Exception as exc:
        return {
            "embeddingProbeStatus": "probe-failed",
            "embeddingResponseUsable": False,
            "embeddingProbeLatencyMs": round(
                (time.perf_counter() - started_at) * 1000
            ),
            "embeddingProbeError": _safe_exception_message(exc),
        }

    elapsed_ms = round((time.perf_counter() - started_at) * 1000)
    if response.status_code < 200 or response.status_code >= 300:
        return {
            "embeddingProbeStatus": "rejected",
            "embeddingHttpStatus": response.status_code,
            "embeddingResponseUsable": False,
            "embeddingProbeLatencyMs": elapsed_ms,
            "embeddingProbeError": _http_error_message(
                response.status_code, response.text
            ),
        }
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        body = None
    data = body.get("data") if isinstance(body, dict) else None
    vector = data[0].get("embedding") if isinstance(data, list) and data else None
    received_dimensions = len(vector) if isinstance(vector, list) else 0
    usable = received_dimensions > 0
    return {
        "embeddingProbeStatus": "passed" if usable else "probe-failed",
        "embeddingHttpStatus": response.status_code,
        "embeddingResponseUsable": usable,
        "embeddingProbeLatencyMs": elapsed_ms,
        "embeddingDimensions": received_dimensions,
        **(
            {}
            if usable
            else {
                "embeddingProbeError": "向量响应没有返回可用维度"
            }
        ),
    }


def _probe_agent_stream_contract(
    *,
    upstream_model: str,
    base_url: str,
    api_key: str,
    protocol: str,
    timeout: float,
) -> dict[str, Any]:
    endpoint = join_contract_endpoint(
        normalize_direct_model_base_url(base_url),
        "/chat/completions",
    )
    payload = {
        "model": upstream_model,
        "messages": [{"role": "user", "content": "Reply with only OK."}],
        # Thinking models may emit reasoning deltas before visible content.
        # Leave enough room to prove that the stream reaches a final answer.
        "max_tokens": 256,
        "temperature": 0,
        "stream": True,
    }
    started_at = time.perf_counter()
    try:
        import httpx

        from novelvideo.gateway_transport import newapi_httpx_client_kwargs

        client_kwargs = newapi_httpx_client_kwargs(
            base_url=base_url,
            timeout=max(
                1.0,
                min(float(timeout), CHAT_PROBE_TIMEOUT_CEILING_SECONDS),
            ),
        )
        headers = get_model_contract(protocol).auth.headers(api_key)
        with httpx.Client(**client_kwargs) as client:
            with client.stream("POST", endpoint, headers=headers, json=payload) as response:
                status = response.status_code
                usable = _stream_response_has_usable_content(response)
    except Exception as exc:
        return {
            "streamProbeStatus": "probe-failed",
            "streamResponseUsable": False,
            "streamFirstEventLatencyMs": round((time.perf_counter() - started_at) * 1000),
            "streamProbeError": _safe_exception_message(exc),
        }
    elapsed_ms = round((time.perf_counter() - started_at) * 1000)
    if status >= 400:
        return {
            "streamProbeStatus": "rejected",
            "streamHttpStatus": status,
            "streamResponseUsable": False,
            "streamFirstEventLatencyMs": elapsed_ms,
            "streamProbeError": _http_error_message(status, ""),
        }
    return {
        "streamProbeStatus": "passed" if usable else "probe-failed",
        "streamHttpStatus": status,
        "streamResponseUsable": usable,
        "streamFirstEventLatencyMs": elapsed_ms,
        **({} if usable else {"streamProbeError": "SSE 没有返回可用 assistant delta"}),
    }


def _stream_response_has_usable_content(response: Any) -> bool:
    for line in response.iter_lines():
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        text = str(line or "").strip()
        if not text.startswith("data:"):
            continue
        data = text[5:].strip()
        if data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except (ValueError, json.JSONDecodeError):
            continue
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list):
            continue
        for choice in choices:
            delta = choice.get("delta") if isinstance(choice, dict) else None
            if isinstance(delta, dict) and (
                str(delta.get("content") or "").strip()
                or str(delta.get("reasoning_content") or "").strip()
                or isinstance(delta.get("tool_calls"), list)
            ):
                return True
    return False


#: An 8x8 solid red PNG plus a question whose answer only a real image reader
#: can produce; a provider that silently ignores image parts answers without it.
_VISION_PROBE_IMAGE_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAEUlEQVR42mO4IyKCFTEMLQkAmD9B"
    "AeEqE6gAAAAASUVORK5CYII="
)
_VISION_PROBE_QUESTION = "这张图片的主色调是什么？只回答颜色名称，不要解释。"
_VISION_PROBE_ANSWER_MARKERS = ("红", "red", "crimson", "scarlet")


def _chat_response_text(payload: object) -> str:
    from novelvideo.gateway_transport import unwrap_openai_chat_completion_payload

    payload = unwrap_openai_chat_completion_payload(payload)
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return ""
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if isinstance(message, dict):
            content = str(message.get("content") or "").strip()
            if content:
                return content
    return ""


def _probe_openai_vision_contract(
    *,
    upstream_model: str,
    base_url: str,
    api_key: str,
    protocol: str,
    timeout: float,
) -> dict[str, Any]:
    """Prove the model really accepts image input instead of trusting a tick."""

    endpoint = join_contract_endpoint(
        normalize_direct_model_base_url(base_url),
        "/chat/completions",
    )
    payload = {
        "model": upstream_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _VISION_PROBE_QUESTION},
                    {
                        "type": "image_url",
                        "image_url": {"url": _VISION_PROBE_IMAGE_DATA_URL},
                    },
                ],
            }
        ],
        # Reasoning models spend the budget on reasoning before any content, so
        # a small cap leaves the answer empty and looks like a failure.
        "max_tokens": 256,
        "temperature": 0,
    }
    try:
        import httpx

        from novelvideo.gateway_transport import newapi_httpx_client_kwargs

        client_kwargs = newapi_httpx_client_kwargs(
            base_url=base_url,
            timeout=max(
                1.0,
                min(float(timeout), CHAT_PROBE_TIMEOUT_CEILING_SECONDS),
            ),
        )
        headers = get_model_contract(protocol).auth.headers(api_key)
        with httpx.Client(**client_kwargs) as client:
            response = client.post(endpoint, headers=headers, json=payload)
    except Exception as exc:
        return {
            "visionProbeStatus": "probe-failed",
            "visionResponseUsable": False,
            "visionProbeError": _safe_exception_message(exc),
        }
    if response.status_code >= 400:
        return {
            "visionProbeStatus": "rejected",
            "visionHttpStatus": response.status_code,
            "visionResponseUsable": False,
            "visionProbeError": _http_error_message(response.status_code, response.text),
        }
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        body = None
    answer = _chat_response_text(body)
    usable = any(marker in answer.casefold() for marker in _VISION_PROBE_ANSWER_MARKERS)
    return {
        "visionProbeStatus": "passed" if usable else "probe-failed",
        "visionHttpStatus": response.status_code,
        "visionResponseUsable": usable,
        **(
            {}
            if usable
            else {
                "visionProbeError": (
                    "模型接受了图片但没有读出画面内容"
                    if _chat_response_has_usable_content(body)
                    else "带图片的请求没有返回可用内容"
                )
            }
        ),
    }


def _probe_agent_tool_contract(
    *,
    upstream_model: str,
    base_url: str,
    api_key: str,
    protocol: str,
    timeout: float,
) -> dict[str, Any]:
    endpoint = join_contract_endpoint(
        normalize_direct_model_base_url(base_url),
        "/chat/completions",
    )
    payload = {
        "model": upstream_model,
        "messages": [{"role": "user", "content": "Call report_ready now."}],
        "max_tokens": 32,
        "temperature": 0,
        "tools": [_chat_probe_tool()],
        "tool_choice": {"type": "function", "function": {"name": "report_ready"}},
    }
    try:
        import httpx

        from novelvideo.gateway_transport import newapi_httpx_client_kwargs

        client_kwargs = newapi_httpx_client_kwargs(
            base_url=base_url,
            timeout=max(
                1.0,
                min(float(timeout), CHAT_PROBE_TIMEOUT_CEILING_SECONDS),
            ),
        )
        headers = get_model_contract(protocol).auth.headers(api_key)
        with httpx.Client(**client_kwargs) as client:
            response = client.post(endpoint, headers=headers, json=payload)
            probe_mode = "forced-function"
            if _should_retry_tool_probe_with_auto(response):
                # Several reasoning endpoints support tools but reject forced
                # tool_choice while thinking mode is enabled. Hermes uses the
                # ordinary auto tool loop, so retry that exact compatible mode
                # before classifying the model as text-only.
                fallback_payload = {
                    **payload,
                    "max_tokens": 256,
                    "tool_choice": "auto",
                }
                response = client.post(endpoint, headers=headers, json=fallback_payload)
                probe_mode = "auto-fallback"
    except Exception as exc:
        return {
            "toolProbeStatus": "probe-failed",
            "toolCallingVerified": False,
            "toolProbeError": _safe_exception_message(exc),
        }
    verified = 200 <= response.status_code < 300 and _chat_has_probe_tool_call(response)
    return {
        "toolProbeStatus": "passed" if verified else "rejected" if response.status_code >= 400 else "probe-failed",
        "toolHttpStatus": response.status_code,
        "toolCallingVerified": verified,
        "toolProbeMode": probe_mode,
        **({}
           if verified
           else {"toolProbeError": _http_error_message(response.status_code, response.text)
                 if response.status_code >= 400
                 else "Chat Completions 未返回指定工具调用"}),
    }


def _chat_probe_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "report_ready",
            "description": "Confirm that the model can invoke tools.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    }


def _chat_has_probe_tool_call(response: Any) -> bool:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return False
    from novelvideo.gateway_transport import unwrap_openai_chat_completion_payload

    payload = unwrap_openai_chat_completion_payload(payload)
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list):
        return False
    return any(
        isinstance(choice, dict)
        and isinstance(choice.get("message"), dict)
        and any(
            isinstance(call, dict)
            and isinstance(call.get("function"), dict)
            and call["function"].get("name") == "report_ready"
            for call in (choice["message"].get("tool_calls") or [])
        )
        for choice in choices
    )


def _should_retry_tool_probe_with_auto(response: Any) -> bool:
    if response.status_code not in {400, 409, 422}:
        return False
    detail = str(getattr(response, "text", "") or "").casefold()
    return "tool_choice" in detail or "tool choice" in detail


def _chat_response_has_usable_content(payload: object) -> bool:
    from novelvideo.gateway_transport import unwrap_openai_chat_completion_payload

    payload = unwrap_openai_chat_completion_payload(payload)
    if not isinstance(payload, dict):
        return False
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return False
    return any(
        isinstance(choice, dict)
        and isinstance(choice.get("message"), dict)
        and bool(
            str(choice["message"].get("content") or "").strip()
            or str(choice["message"].get("reasoning_content") or "").strip()
            or isinstance(choice["message"].get("tool_calls"), list)
        )
        for choice in choices
    )


def _probe_request(base_url: str, api_key: str, protocol: str) -> tuple[str, dict[str, str]]:
    base = _normalize_base_url(base_url)
    clean_key = str(api_key or "").strip()
    contract = get_model_contract(protocol)
    if not contract.endpoints.catalog_path:
        raise ValueError(f"unsupported direct model protocol: {protocol}")
    return (
        join_contract_endpoint(base, contract.endpoints.catalog_path),
        contract.auth.headers(clean_key),
    )


def _normalize_base_url(base_url: str) -> str:
    normalized = str(base_url or "").strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("baseUrl must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("baseUrl must not contain credentials")
    return normalize_direct_model_base_url(normalized)


def _probe_failure(
    error: str,
    protocol: str,
    *,
    http_status: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "modelFound": False,
        "discoveredModelCount": 0,
        "protocol": protocol,
        "error": error,
    }
    if http_status is not None:
        payload["httpStatus"] = http_status
        payload["errorCode"] = {
            401: "authentication-rejected",
            403: "permission-rejected",
            404: "endpoint-not-found",
            408: "upstream-timeout",
            429: "rate-limited",
        }.get(http_status, "upstream-http-error")
    return payload


def _http_error_message(status_code: int, body: str) -> str:
    message = ""
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("code") or "").strip()
        if not message:
            message = str(parsed.get("message") or parsed.get("detail") or "").strip()
    if not message:
        message = str(body or "").strip().replace("\n", " ")[:240]
    diagnosis = {
        401: "认证被拒绝：检查 Key 是否属于该接口，以及协议认证方式是否匹配",
        403: "访问被拒绝：Key 可能缺少模型权限、额度、来源或区域权限",
        404: "接口不存在：检查 Base URL；系统已自动补全版本路径",
        429: "请求受限：上游额度或速率限制已触发",
    }.get(status_code, "")
    suffix = message or diagnosis
    if diagnosis and message:
        suffix = f"{diagnosis}；上游：{message}"
    return f"HTTP {status_code}" + (f": {suffix}" if suffix else "")


def _safe_exception_message(exc: Exception) -> str:
    message = str(exc).strip().replace("\n", " ")
    if not message:
        return type(exc).__name__
    return f"{type(exc).__name__}: {message[:240]}"


def _model_catalog(
    payload: object, protocol: str
) -> tuple[set[str], dict[str, dict[str, Any]], set[str]]:
    if not isinstance(payload, dict):
        return set(), {}, set()
    if protocol == DIRECT_MODEL_PROTOCOL_GEMINI or (
        protocol == DIRECT_MODEL_PROTOCOL_GEMINI_IMAGE
        and isinstance(payload.get("models"), list)
    ):
        items = payload.get("models")
        if not isinstance(items, list):
            return set(), {}, set()
        result: set[str] = set()
        public_models: set[str] = set()
        metadata: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            display = str(item.get("displayName") or "").strip()
            canonical = name.removeprefix("models/")
            if canonical:
                public_models.add(canonical)
            for value in (name, name.removeprefix("models/"), display):
                if value:
                    result.add(value)
                    metadata[value] = _safe_model_metadata(item)
            if canonical:
                metadata[canonical] = _safe_model_metadata(item)
        return result, metadata, public_models
    items = payload.get("data") or payload.get("models") or payload.get("items")
    if not isinstance(items, list):
        return set(), {}, set()
    result: set[str] = set()
    metadata: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        if isinstance(item, str):
            model_id = item.strip()
            item = {"id": model_id}
        elif isinstance(item, dict):
            model_id = str(
                item.get("id")
                or item.get("model")
                or item.get("modelId")
                or item.get("name")
                or ""
            ).strip()
        else:
            model_id = ""
        if not model_id:
            continue
        result.add(model_id)
        metadata[model_id] = _safe_model_metadata(item)
    return result, metadata, set(result)


def _safe_model_metadata(item: dict[str, Any]) -> dict[str, Any]:
    """Keep only capability-like catalog fields; exclude provider internals."""

    safe_keys = (
        "displayName",
        "description",
        "inputTokenLimit",
        "contextLength",
        "context_length",
        "contextWindow",
        "context_window",
        "maxContextTokens",
        "max_context_tokens",
        "outputTokenLimit",
        "maxOutputTokens",
        "max_output_tokens",
        "supportedGenerationMethods",
        # Keep the provider's capability contract intact.  These fields are
        # consumed by the shared model contract after probing; dropping them
        # here made a successful /models response look like a name-only
        # profile and silently hid valid modes, slots, and dimensions.
        "supportedModes",
        "supported_modes",
        "modes",
        "generationModes",
        "generation_modes",
        "inputSlots",
        "input_slots",
        "referenceLimits",
        "reference_limits",
        "parameterDefaults",
        "parameter_defaults",
        "dimensions",
        "embeddingDimensions",
        "embedding_dimensions",
        "outputDimensions",
        "output_dimensions",
        "batchSize",
        "batch_size",
        "qualityOptions",
        "quality_options",
        "supportedQualities",
        "supported_qualities",
        "voiceOptions",
        "voice_options",
        "audioFormats",
        "audio_formats",
        "providerMapping",
        "provider_mapping",
        "mapping",
        "parameters",
        "parameterSchema",
        "parameter_schema",
        "workflowInputRules",
        "workflow_input_rules",
        "workflowDiscovery",
        "workflow_discovery",
        "workflowId",
        "workflow_id",
        "workflowName",
        "workflow_name",
        "mediaInputs",
        "media_inputs",
        "supportsCustomDuration",
        "supports_custom_duration",
        "minDuration",
        "min_duration",
        "maxDuration",
        "max_duration",
        "input_modalities",
        "output_modalities",
        "modalities",
        "tags",
        "capabilities",
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
        "supportedImageSizes",
        "supported_image_sizes",
        "imageSizes",
        "image_sizes",
        "supportedDimensions",
        "supported_dimensions",
        "resolutionMode",
        "resolution_mode",
        "supportedQualityValues",
        "supported_quality_values",
        "supportedInvocationModes",
        "supported_invocation_modes",
        "supportedEndpointTypes",
        "supported_endpoint_types",
        "resolutionOptions",
        "resolution_options",
        "supportedResolutions",
        "supported_resolutions",
        "supportedSizes",
        "supported_sizes",
        "sizes",
        "qualityOptions",
        "quality_options",
        "supportedQualities",
        "supported_qualities",
        "supportsCustomAspectRatio",
        "supports_custom_aspect_ratio",
        "supportsArbitraryAspectRatio",
        "supports_arbitrary_aspect_ratio",
        "supportsCustomResolution",
        "supports_custom_resolution",
        "supportsArbitraryResolution",
        "supports_arbitrary_resolution",
        "supportsAnySize",
        "supports_any_size",
    )
    return {key: item[key] for key in safe_keys if key in item}


def _public_model_catalog(
    public_models: set[str],
    metadata: dict[str, dict[str, Any]],
    *,
    kind: str = "",
) -> list[dict[str, Any]]:
    """Expose only model IDs and capability-shaped catalog metadata."""

    return [
        {"id": model_id, "metadata": metadata.get(model_id, {})}
        for model_id in sorted(public_models, key=str.casefold)
        if _model_is_compatible_with_kind(model_id, metadata.get(model_id, {}), kind)
    ]


def _model_is_compatible_with_kind(
    model_id: str,
    model_metadata: dict[str, Any],
    kind: str,
) -> bool:
    """Hide deterministic cross-family catalog mismatches during auto-fill."""
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind not in {"chat", "agent", "text", "vision", "image", "embedding", "audio"}:
        return True
    values: list[str] = [str(model_id or "").casefold()]
    declared_inputs: set[str] = set()
    declared_outputs: set[str] = set()
    for key in ("tags", "input_modalities", "output_modalities", "modalities", "capabilities"):
        raw = model_metadata.get(key)
        if isinstance(raw, dict):
            enabled_values = {str(name).casefold() for name, enabled in raw.items() if enabled}
            values.extend(enabled_values)
            if key in {"input_modalities", "modalities"}:
                declared_inputs.update(enabled_values)
            if key in {"output_modalities", "modalities"}:
                declared_outputs.update(enabled_values)
        elif isinstance(raw, list):
            normalized_values = {str(item).casefold() for item in raw}
            values.extend(normalized_values)
            if key in {"input_modalities", "modalities"}:
                declared_inputs.update(normalized_values)
            if key in {"output_modalities", "modalities"}:
                declared_outputs.update(normalized_values)
        elif isinstance(raw, str):
            values.append(raw.casefold())
    haystack = " ".join(values)
    # Explicit provider metadata outranks name heuristics.  This is the
    # important guard against importing a text or embedding model into an
    # image/audio row merely because the upstream directory is flat.
    declared = declared_inputs | declared_outputs
    if declared:
        if normalized_kind == "image":
            return bool({"image", "images", "image_generation"} & (declared_outputs | declared))
        if normalized_kind == "embedding":
            return bool({"embedding", "embeddings", "vector"} & (declared_outputs | declared))
        if normalized_kind == "audio":
            return bool({"audio", "speech", "voice", "music"} & (declared_outputs | declared))
        if normalized_kind == "vision":
            return bool({"image", "images", "vision"} & declared_inputs)
        if normalized_kind in {"chat", "text"}:
            return not bool({"embedding", "embeddings", "audio", "speech", "image_generation", "video"} & declared_outputs)
        if normalized_kind == "agent":
            return not bool({"embedding", "embeddings", "audio", "image_generation", "video"} & declared_outputs)
    markers = {
        "chat": ("gemini", "gpt-", "deepseek", "qwen", "claude", "glm-", "llama", "mistral", "ernie", "doubao"),
        "image": ("image", "dall-e", "seedream", "nano-banana", "flux", "midjourney"),
        "embedding": ("embedding", "bge-", "e5-", "gte-", "jina-embeddings", "text-embedding"),
        "audio": ("audio", "tts", "speech", "voice", "chattts", "indextts", "eleven", "mureka", "music"),
        "video": ("video", "kling", "seedance", "minimax-h3", "sora", "veo"),
    }
    if normalized_kind in {"chat", "agent", "text", "vision"}:
        return not any(
            marker in haystack
            for family, family_markers in markers.items()
            if family not in {"agent", "text", "vision", "chat"}
            for marker in family_markers
        )
    if any(marker in haystack for marker in markers[normalized_kind]):
        return True
    return not any(
        marker in haystack
        for family, family_markers in markers.items()
        if family != normalized_kind
        for marker in family_markers
    )


__all__ = ["discover_direct_models", "probe_direct_model_endpoint"]
