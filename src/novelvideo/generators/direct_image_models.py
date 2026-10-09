"""Direct OpenAI-compatible image model registry and request adapter.

The canvas stores a friendly label separately from the upstream model ID.  This
module turns that saved record into one capability contract shared by the model
picker, text-to-image and image-to-image execution paths.  It deliberately
does not use the NewAPI media relay: direct image edits upload local reference
files straight to the operator-configured endpoint.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import quote

import httpx

from novelvideo.gateway_transport import newapi_httpx_client_kwargs
from novelvideo.generators.direct_image_capabilities import (
    IMAGE_MODE_IMAGE_TO_IMAGE,
    DirectImageCapabilityProfile,
    direct_image_capability_summary,
    resolve_direct_image_profile,
)
from novelvideo.generators.image_request_policy import (
    image_submit_timeout,
    is_valid_image_aspect_ratio,
    is_valid_image_size,
    normalize_image_size,
)
from novelvideo.generators.image_upstream_profiles import compile_newapi_image_payload
from novelvideo.generators.model_contracts import (
    DIRECT_MODEL_PROTOCOL_GEMINI_IMAGE,
    get_model_contract,
    join_contract_endpoint,
)
from novelvideo.model_gateway_settings import get_direct_models
from novelvideo.shared.provider_cost import extract_provider_cost_evidence


DIRECT_IMAGE_MODEL_PREFIX = "direct/"

logger = logging.getLogger(__name__)

# A generic Chinese "安全政策" 400 from an HK relay is usually length/format/
# reference noise, not a verdict on the operator's art.  One immediate retry of
# the identical payload recovers a measurable share of these blocks.  It is
# deliberately bounded at one extra paid request and never applied to an
# explicit provider moderation category.
DIRECT_IMAGE_SAFETY_RETRY_MAX_ATTEMPTS = 2
DIRECT_IMAGE_SAFETY_RETRY_BACKOFF_SECONDS = 1.5
DIRECT_IMAGE_SAFETY_RETRY_ENV = "VILLAGE_CANVAS_DIRECT_IMAGE_SAFETY_RETRY"
# A cross-channel retry is a *second paid request*, so it never happens on its
# own: an operator must name an already-enabled backup registry id.
DIRECT_IMAGE_FALLBACK_ENV = "VILLAGE_CANVAS_DIRECT_IMAGE_FALLBACK"
# Upstream relays (especially HK WAF/Cloudflare fronted ones) drop a submit
# mid-flight often enough that a bare httpx transport error reached the task
# runner as a raw traceback: 2026-09-16 真机 `RemoteProtocolError: Server
# disconnected without sending a response.` killed task
# ``freezone_gen/01M0W7VBRJ2RGE20H20D4VNZ7P/0`` with no retry and no
# task-facing diagnostic.  The NewAPI image path has retried this class of
# failure for a while (``nanobanana_grid._newapi_is_transient_transport_exception``);
# the direct path now shares the same bounded policy.
DIRECT_IMAGE_TRANSIENT_MAX_ATTEMPTS = 3
DIRECT_IMAGE_TRANSIENT_BACKOFF_SECONDS = (0.8, 1.6, 3.2)
#: A synchronous image relay holds the submit connection open for the whole
#: render.  120s was tight enough that a real 12-shot batch lost 2 of 12 shots to
#: ``httpx.ReadTimeout`` while the upstream was still working (2026-09-21,
#: run ``wfr_ad5e917833a445e6a6c2e419891b1987``, error
#: ``direct image API submit result is unknown: ReadTimeout``).  A ReadTimeout is
#: deliberately *not* replayed -- see ``_DIRECT_IMAGE_SAFE_SUBMIT_RETRY_EXCEPTIONS``
#: -- so an under-sized wait converts one slow shot into a failed workflow step.
#: This is a read budget, not a retry budget: it never re-sends a paid payload.
#:
#: 2026-10-03 用户现场后本机不再给「等图」设读取上限：出图提交统一走
#: ``image_request_policy.image_submit_timeout()``（``read=None``）。需要恢复有界
#: 读取时设 ``VILLAGE_CANVAS_IMAGE_READ_TIMEOUT_SECONDS``，不必改代码。
#
#: 另一种失败和中转站有关：长图会先回 5xx，再由上游慢慢跑完（2026-10-03 角色资产
#: 重跑 5 个角色全部 ``HTTP 504 图片生成超时``，而本地读取上限根本没到期）。这类
#: 5xx 是「还没出结果」，不是「判定失败」，所以按 T-165 已对 NewAPI 图像路径生效的
#: 同一条策略原样重发并拉长间隔。每次重发都是一次付费请求，因此次数写死在上限。
DIRECT_IMAGE_RESPONSE_RETRY_MAX_ATTEMPTS = 3
DIRECT_IMAGE_RESPONSE_RETRY_BACKOFF_SECONDS = (20.0, 60.0)
DIRECT_IMAGE_RESPONSE_RETRY_ENV = "VILLAGE_CANVAS_DIRECT_IMAGE_5XX_RETRY"
DIRECT_IMAGE_TRANSIENT_STATUS_CODES = frozenset(
    {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}
)
#: 中转站有时把「它自己内部报错」也包装成 HTTP 400（2026-10-03 身份参考图：
#: ``400 {"message":"由于我这边发生了错误，我未能生成图片。"}``），这条明显不是
#: 我们的请求有问题，而是上游那一跳失败，值得按同一套有限次重发。
_DIRECT_IMAGE_RELAY_SIDE_FAILURE_MARKERS = (
    "由于我这边发生了错误",
    "我未能生成图片",
    "an error occurred on our side",
    "internal error occurred",
)
# A retry re-sends a *paid* payload, so only network-level failures qualify:
# ``LocalProtocolError`` (our own malformed request) and ``UnsupportedProtocol``
# (bad base URL) are configuration verdicts and must fail on the first attempt.
_DIRECT_IMAGE_PERMANENT_TRANSPORT_EXCEPTIONS = frozenset(
    {"LocalProtocolError", "UnsupportedProtocol", "InvalidURL"}
)
#: Submit retries may spend another paid generation.  Only failures that prove
#: no HTTP request reached the upstream are safe to replay without an
#: idempotency key.  Read/write/protocol errors are deliberately excluded:
#: the provider may have accepted and billed the first request before the
#: connection dropped.
_DIRECT_IMAGE_SAFE_SUBMIT_RETRY_EXCEPTIONS = frozenset(
    {"ConnectError", "ConnectTimeout", "PoolTimeout"}
)


def _direct_image_safety_retry_enabled() -> bool:
    """Opt-out only: the retry is on unless an operator disables it."""
    raw = str(os.environ.get(DIRECT_IMAGE_SAFETY_RETRY_ENV) or "").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def _direct_image_response_retry_enabled() -> bool:
    """Opt-out only: 上游 5xx 的原样重发默认开启。"""

    raw = str(os.environ.get(DIRECT_IMAGE_RESPONSE_RETRY_ENV) or "").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def _direct_image_relay_side_failure(body: str) -> bool:
    """中转站自认「它那边出错」时为真（这类失败可以原样重发）。"""

    text = str(body or "").lower()
    return any(
        marker.lower() in text for marker in _DIRECT_IMAGE_RELAY_SIDE_FAILURE_MARKERS
    )


def _direct_image_is_transient_transport_exception(exc: BaseException) -> bool:
    """Whether a submit/read transport failure is worth the same payload again.

    Only ``httpx`` transport failures qualify.  The shared NewAPI predicate is
    reused first so both image paths retire on identical evidence; it misses
    mid-stream ``ReadError`` / ``WriteError`` (empty message, name not in its
    table), which are exactly the abrupt-disconnect shape this path needs.
    """

    from novelvideo.generators.nanobanana_grid import (
        _newapi_is_transient_transport_exception,
    )

    name = type(exc).__name__
    if name in _DIRECT_IMAGE_PERMANENT_TRANSPORT_EXCEPTIONS:
        return False
    if name in {"ReadError", "WriteError", "CloseError"}:
        return True
    return _newapi_is_transient_transport_exception(exc)


def _direct_image_submit_retry_is_safe(exc: BaseException) -> bool:
    """Whether a submit can be replayed without risking duplicate billing."""

    return type(exc).__name__ in _DIRECT_IMAGE_SAFE_SUBMIT_RETRY_EXCEPTIONS


def resolve_direct_image_fallback_model(
    primary: DirectImageModel,
) -> DirectImageModel | None:
    """Return the operator-pinned backup channel, or ``None`` when unset.

    Disabled by default: the fallback spends a second paid request, so it only
    runs when an operator has named a different, already-enabled registry id
    whose capability and price are known locally.
    """

    configured = str(os.environ.get(DIRECT_IMAGE_FALLBACK_ENV) or "").strip()
    if not configured:
        return None
    candidate = resolve_direct_image_model(configured)
    if candidate is None or candidate.registry_id == primary.registry_id:
        return None
    return candidate


def _direct_image_response_body(response: httpx.Response) -> str:
    """Return the upstream body with ``\\uXXXX`` escapes expanded.

    Some relays serialize their Chinese policy wrapper with ASCII escapes, so
    the literal marker ``安全政策`` never appears in the raw bytes.  Classify
    against both views: the raw body for the message, the decoded strings for
    the markers.
    """

    try:
        raw = response.text
    except Exception:  # pragma: no cover - httpx text decoding is defensive
        return ""
    if "\\u" not in raw:
        return raw
    try:
        decoded = json.loads(raw)
    except ValueError:
        return raw
    parts: list[str] = []

    def _walk(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                _walk(item)
        elif isinstance(value, list):
            for item in value:
                _walk(item)

    _walk(decoded)
    return f"{raw} {' '.join(parts)}" if parts else raw


class DirectImageUpstreamError(RuntimeError):
    """A credential-free direct-channel failure carrying task-facing metadata.

    The canvas renders whatever the task runner persists, so the classification
    (generic relay wrapper vs. explicit provider moderation) has to travel as
    structured metadata rather than as prose inside the message.
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        stage: str,
        retryable: bool,
        suggested_action: str,
        http_status: int | None = None,
        protocol: str = "",
        endpoint_class: str = "direct-image",
        request_contract: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = str(error_code)
        self.retryable = bool(retryable)
        metadata: dict[str, object] = {
            "error_code": error_code,
            "endpoint_class": endpoint_class,
            "stage": stage,
            "retryable": retryable,
            "suggested_action": suggested_action,
        }
        if http_status is not None:
            metadata["http_status"] = int(http_status)
        if protocol:
            metadata["protocol"] = protocol
        if request_contract:
            metadata["request_contract"] = request_contract
        self.provider_error_metadata = metadata


def _direct_image_request_contract(response: httpx.Response) -> dict[str, object]:
    """Summarize the failed request without leaking the prompt or the key."""

    return {"content_type": str(response.headers.get("content-type") or "")[:80]}


@dataclass(frozen=True, slots=True)
class DirectImageModel:
    registry_id: str
    label: str
    upstream_model: str
    base_url: str
    api_key: str
    protocol: str
    enabled: bool
    is_default: bool

    @property
    def catalog_id(self) -> str:
        return f"{DIRECT_IMAGE_MODEL_PREFIX}{self.registry_id}"

    @property
    def profile(self) -> DirectImageCapabilityProfile:
        # The model center may have a newer upstream capability contract than
        # the built-in model-name profile.  All execution paths must consume
        # the same effective profile that drives the picker.
        from novelvideo.generators.direct_model_capability_cache import (
            get_cached_direct_model_capability,
        )

        cached = get_cached_direct_model_capability(
            base_url=self.base_url,
            kind="image",
            upstream_model=self.upstream_model,
        )
        metadata = cached.get("modelMetadata")
        return resolve_direct_image_profile(
            self.upstream_model,
            metadata=metadata if isinstance(metadata, dict) else None,
        )


def list_direct_image_models() -> tuple[DirectImageModel, ...]:
    """Read valid direct image models in operator-configured order."""
    return tuple(
        DirectImageModel(
            registry_id=str(item["id"]),
            label=str(item["label"]),
            upstream_model=str(item["modelId"]),
            base_url=str(item["baseUrl"]),
            api_key=str(item["apiKey"]),
            protocol=str(item.get("protocol") or "openai-images"),
            enabled=bool(item["enabled"]),
            is_default=bool(item.get("isDefault", False)),
        )
        for item in get_direct_models("image")
    )


def resolve_direct_image_model(model: str | None) -> DirectImageModel | None:
    """Resolve a catalog id without accepting arbitrary direct endpoints."""
    value = str(model or "").strip().lower()
    if value.startswith(DIRECT_IMAGE_MODEL_PREFIX):
        value = value[len(DIRECT_IMAGE_MODEL_PREFIX) :]
    models = list_direct_image_models()
    if value in {"", "default"}:
        return next((item for item in models if item.enabled and item.is_default), None) or next(
            (item for item in models if item.enabled), None
        )
    return next(
        (item for item in models if item.registry_id == value and item.enabled),
        None,
    )


def direct_image_model_option(model: DirectImageModel) -> dict[str, Any]:
    """Return the shared picker payload for a saved direct image model."""
    from novelvideo.generators.direct_model_capability_cache import (
        get_cached_direct_model_capability,
    )

    cached = get_cached_direct_model_capability(
        base_url=model.base_url,
        kind="image",
        upstream_model=model.upstream_model,
    )
    metadata = cached.get("modelMetadata")
    summary = direct_image_capability_summary(
        model.upstream_model,
        metadata=metadata if isinstance(metadata, dict) else None,
    )
    from novelvideo.generators.direct_models import (
        DirectModel,
        direct_model_catalog_verification,
        is_direct_model_runtime_ready,
    )

    runtime_model = DirectModel(
        kind="image",
        registry_id=model.registry_id,
        label=model.label,
        upstream_model=model.upstream_model,
        base_url=model.base_url,
        api_key=model.api_key,
        protocol=model.protocol,
        enabled=model.enabled,
        is_default=model.is_default,
    )
    runtime_ready = is_direct_model_runtime_ready(runtime_model) and bool(
        model.profile.modes
    )
    catalog_verification = direct_model_catalog_verification(runtime_model)
    declared_image_modes = bool(model.profile.modes)
    enabled = model.enabled and runtime_ready
    disabled_reason = (
        "模型已在直连生图模型管理中停用"
        if not model.enabled
        else "上游模型目录未找到该模型 ID，请核对模型 ID 后重新检测"
        if catalog_verification == "catalog-mismatch"
        else "上游能力合同未声明可执行的图片生成模式"
        if not declared_image_modes
        else "模型尚未通过上游目录校验，请先检测连接"
        if not runtime_ready
        else ""
    )
    return {
        "id": model.catalog_id,
        "providerId": "direct",
        "provider": "direct",
        "apiModel": model.catalog_id,
        "api_model": model.catalog_id,
        "label": model.label if enabled else f"{model.label}（已停用）",
        "isDefault": model.is_default,
        "is_default": model.is_default,
        "upstreamModel": model.upstream_model,
        "upstream_model": model.upstream_model,
        "enabled": enabled,
        "disabled": not enabled,
        "disabledReason": disabled_reason,
        "disabled_reason": disabled_reason,
        "runtimeReady": runtime_ready,
        "catalogVerification": catalog_verification,
        "channelLabel": "直连生图 API",
        "channel_label": "直连生图 API",
        "priceHint": "按直连上游计费",
        "price_hint": "按直连上游计费",
        "recommendation": "自动按模型能力合同匹配文生图或图生图",
        "sortRank": -100,
        "sort_rank": -100,
        **summary,
    }


def bind_direct_image_generator_config(
    model: DirectImageModel,
    base_config: dict[str, Any],
) -> dict[str, Any]:
    """把通用网格生成器绑定到用户选中的直连图片模型。"""
    from novelvideo.generators.direct_models import (
        DirectModel,
        ensure_direct_model_runtime_ready,
    )

    ensure_direct_model_runtime_ready(
        DirectModel(
            kind="image",
            registry_id=model.registry_id,
            label=model.label,
            upstream_model=model.upstream_model,
            base_url=model.base_url,
            api_key=model.api_key,
            protocol=model.protocol,
            enabled=model.enabled,
            is_default=model.is_default,
        )
    )
    return {
        **base_config,
        "provider": "newapi",
        "api_key": model.api_key,
        "model": model.upstream_model,
        "base_url": model.base_url,
        "preserve_model_id": True,
    }


def _direct_image_request_fields(
    *,
    model: DirectImageModel,
    prompt: str,
    aspect_ratio: str,
    image_size: str,
    quality: str | None,
    advanced_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = model.profile
    aspect_parameter_enabled = bool(
        profile.aspect_ratio_options or profile.supports_custom_aspect_ratio
    )
    resolution_parameter_enabled = bool(
        profile.resolution_options or profile.supports_custom_resolution
    )
    compiled = compile_newapi_image_payload(
        model=model.upstream_model,
        prompt=str(prompt or ""),
        # An explicit empty upstream capability is authoritative.  Passing an
        # old node value into the compiler here would recreate a synthetic
        # ``size``/``aspect_ratio`` field before transport filtering.
        aspect_ratio=aspect_ratio if aspect_parameter_enabled else None,
        image_size=image_size if resolution_parameter_enabled else None,
        quality=quality,
        preserve_custom_dimensions=model.profile.supports_custom_resolution,
        preserve_custom_aspect_ratio=model.profile.supports_custom_aspect_ratio,
    )
    fields: dict[str, Any] = {
        "model": model.upstream_model,
        "prompt": str(prompt or "").strip(),
    }
    # An upstream catalog may intentionally omit a resolution contract.  Do
    # not turn that omission into an invalid ``size: ""`` request; the
    # provider can then apply its own native default.
    if resolution_parameter_enabled and compiled.upstream_size:
        # Preserve the selected tier or explicit WxH value after the shared
        # compiler applies only the upstream protocol's dimension constraints.
        fields["size"] = compiled.upstream_size
    normalized_quality = str(quality or "").strip().lower()
    if model.profile.supports_quality and normalized_quality in {
        "low",
        "medium",
        "high",
        "auto",
    }:
        fields["quality"] = normalized_quality
    extra_fields = compiled.payload.get("extra_fields")
    if isinstance(extra_fields, dict) and extra_fields:
        filtered_extra_fields = dict(extra_fields)
        if not aspect_parameter_enabled:
            filtered_extra_fields.pop("aspect_ratio", None)
        if not resolution_parameter_enabled:
            filtered_extra_fields.pop("image_size", None)
            filtered_extra_fields.pop("resolution", None)
        if filtered_extra_fields:
            fields["extra_fields"] = filtered_extra_fields
    if advanced_settings:
        # Flag-driven families (Midjourney) read their switches out of the
        # prompt; a declared parameter without a flag travels in the body.
        _apply_advanced_settings(fields, profile, advanced_settings)
    return fields


def _apply_advanced_settings(
    fields: dict[str, Any],
    profile: "DirectImageCapabilityProfile",
    advanced_settings: dict[str, Any],
) -> None:
    """Place declared advanced parameters on the request they belong to."""
    flags = profile.render_advanced_flags(advanced_settings)
    if flags:
        base_prompt = str(fields.get("prompt") or "").strip()
        fields["prompt"] = f"{base_prompt} {flags}".strip()
    body_params = {
        param.key: advanced_settings[param.key]
        for param in profile.advanced_params
        if param.flag is None and param.key in advanced_settings
    }
    if body_params:
        fields.update(body_params)


def _gemini_image_base_url(base_url: str) -> str:
    """Return the native Gemini API root for either ``/v1`` or ``/v1beta``."""

    base = str(base_url or "").strip().rstrip("/")
    if base.endswith("/v1beta"):
        return base
    if base.endswith("/v1"):
        return f"{base[:-3]}/v1beta"
    return f"{base}/v1beta"


def _gemini_image_endpoint(base_url: str, upstream_model: str) -> str:
    model = str(upstream_model or "").strip().removeprefix("models/")
    encoded_model = quote(model, safe="")
    return (
        f"{_gemini_image_base_url(base_url)}"
        f"/models/{encoded_model}:generateContent"
    )


def _gemini_image_request_fields(
    *,
    model: DirectImageModel,
    prompt: str,
    aspect_ratio: str,
    image_size: str,
    reference_paths: Iterable[str],
) -> dict[str, Any]:
    """Build the native Gemini image request used by New API and Google."""

    parts: list[dict[str, Any]] = []
    for raw_path in reference_paths:
        path = Path(raw_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"direct image reference not found: {path}")
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        parts.append(
            {
                "inlineData": {
                    "mimeType": mime_type,
                    "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                }
            }
        )
    parts.append({"text": str(prompt or "").strip()})

    generation_config: dict[str, Any] = {
        "responseModalities": ["TEXT", "IMAGE"],
    }
    image_config: dict[str, Any] = {}
    profile = model.profile
    if profile.aspect_ratio_options or profile.supports_custom_aspect_ratio:
        normalized_aspect = str(aspect_ratio or "").strip().replace("：", ":")
        if normalized_aspect.casefold() in {"", "auto", "original", "adaptive"}:
            normalized_aspect = profile.default_aspect_ratio or (
                profile.aspect_ratio_options[0]
                if profile.aspect_ratio_options
                else ""
            )
        if normalized_aspect:
            image_config["aspectRatio"] = normalized_aspect
    if profile.resolution_options or profile.supports_custom_resolution:
        normalized_size = str(image_size or "").strip()
        if normalized_size:
            normalized_size = normalize_image_size(normalized_size, provider="newapi")
        if normalized_size:
            image_config["imageSize"] = normalized_size.upper()
    if image_config:
        generation_config["imageConfig"] = image_config
    return {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": generation_config,
    }


def _validate_direct_image_parameters(
    *,
    model: DirectImageModel,
    aspect_ratio: str,
    image_size: str,
) -> None:
    """Enforce the selected model contract before an upstream request.

    Syntax gates prevent malformed values; the profile decides whether a
    custom value is allowed. This keeps stale Agent/node state from silently
    becoming a different ratio or resolution at the transport boundary.
    """
    profile = model.profile
    if profile.aspect_ratio_options or profile.supports_custom_aspect_ratio:
        normalized_aspect = str(aspect_ratio or "").strip().replace("：", ":")
        if normalized_aspect.casefold() in {"", "auto", "original", "adaptive"}:
            normalized_aspect = profile.default_aspect_ratio or (
                profile.aspect_ratio_options[0]
                if profile.aspect_ratio_options
                else ""
            )
        if profile.supports_custom_aspect_ratio:
            if not is_valid_image_aspect_ratio(normalized_aspect):
                raise ValueError(f"{model.label} 不接受图片比例 {aspect_ratio!r}")
        elif normalized_aspect not in profile.aspect_ratio_options:
            raise ValueError(
                f"{model.label} 不支持图片比例 {aspect_ratio!r}，支持值为 {list(profile.aspect_ratio_options)}"
            )

    requested_size = str(image_size or "").strip()
    if requested_size:
        normalized_size = normalize_image_size(requested_size, provider="newapi")
    else:
        normalized_size = profile.default_resolution or (
            profile.resolution_options[0] if profile.resolution_options else ""
        )
    if profile.resolution_options or profile.supports_custom_resolution:
        if profile.supports_custom_resolution:
            if normalized_size and not is_valid_image_size(normalized_size):
                raise ValueError(f"{model.label} 不接受图片尺寸 {image_size!r}")
        elif normalized_size and normalized_size not in profile.resolution_options:
            raise ValueError(
                f"{model.label} 不支持图片尺寸 {image_size!r}，支持值为 {list(profile.resolution_options)}"
            )


def _response_error(response: httpx.Response) -> RuntimeError:
    detail = response.text.strip().replace("\n", " ")
    if len(detail) > 500:
        detail = f"{detail[:497]}..."
    suffix = f": {detail}" if detail else ""
    return RuntimeError(f"direct image API HTTP {response.status_code}{suffix}")


def _direct_image_uploaded_failure(
    response: httpx.Response,
    *,
    model: DirectImageModel,
    attempt: int,
) -> DirectImageUpstreamError | None:
    """Classify a failed submit response into a task-facing diagnostic.

    Returns ``None`` for statuses that are not a content-policy shape; the
    caller then falls back to the generic HTTP error.  A generic relay wrapper
    stays retryable, an explicit provider moderation category never does — the
    provider told us what it objected to and a resend cannot change that.
    """

    from novelvideo.generators.newapi_image_uplink import (
        newapi_explicit_moderation_categories,
        newapi_response_looks_like_generic_safety_block,
        newapi_response_reports_explicit_moderation,
    )

    body = _direct_image_response_body(response)
    status = response.status_code
    contract = _direct_image_request_contract(response)
    detail = body.strip().replace("\n", " ")
    if len(detail) > 500:
        detail = f"{detail[:497]}..."
    suffix = f": {detail}" if detail else ""
    message = f"direct image API HTTP {status}{suffix}"
    logger.info(
        "direct image upstream error classified (model=%s status=%s "
        "explicit_moderation=%s generic_safety_wrapper=%s attempt=%s)",
        model.upstream_model,
        status,
        newapi_response_reports_explicit_moderation(body),
        newapi_response_looks_like_generic_safety_block(body),
        attempt,
    )

    if status == 400 and newapi_response_reports_explicit_moderation(body):
        category = newapi_explicit_moderation_categories(body)
        return DirectImageUpstreamError(
            message,
            error_code="DIRECT_IMAGE_CONTENT_MODERATION_FAILED",
            stage="submit",
            retryable=False,
            suggested_action=(
                f"上游内容审核拦截（category={category}）。请调整画面描述或更换参考素材后重试；"
                "系统不会自动改写或重复提交这条请求。"
            ),
            http_status=status,
            protocol=model.protocol,
            request_contract=contract,
        )

    if status == 400 and newapi_response_looks_like_generic_safety_block(body):
        exhausted = attempt >= DIRECT_IMAGE_SAFETY_RETRY_MAX_ATTEMPTS
        return DirectImageUpstreamError(
            message,
            error_code=(
                "DIRECT_IMAGE_SAFETY_BLOCK" if exhausted else "DIRECT_IMAGE_SAFETY_BLOCK_RETRY"
            ),
            stage="submit",
            retryable=True,
            suggested_action=(
                "上游图像接口返回通用「安全政策」拒绝（常见于长提示词 / 多参考图 / 中转抹平错误，"
                "不代表本机判定内容违规）。"
                + ("已自动重试仍失败，可直接再点一次生成；" if exhausted else "")
                + "如反复出现，请减少参考图数量或更换图像渠道。"
            ),
            http_status=status,
            protocol=model.protocol,
            request_contract=contract,
        )

    if status == 400 and _direct_image_relay_side_failure(body):
        return DirectImageUpstreamError(
            message,
            error_code="DIRECT_IMAGE_UPSTREAM_SIDE_FAILURE",
            stage="submit",
            retryable=True,
            suggested_action=(
                "上游中转自己处理这张图时报错（不是提示词或内容问题）。"
                "系统会自动原样重发；若连续失败可稍后再试或更换图像渠道。"
            ),
            http_status=status,
            protocol=model.protocol,
            request_contract=contract,
        )

    return None


def _image_files(paths: Iterable[str], *, field_name: str) -> list[tuple[str, tuple[str, bytes, str]]]:
    files: list[tuple[str, tuple[str, bytes, str]]] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"direct image reference not found: {path}")
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        files.append((field_name, (path.name, path.read_bytes(), mime_type)))
    return files


async def _write_image_response(
    client: httpx.AsyncClient,
    response: httpx.Response,
    *,
    output_path: Path,
    headers: dict[str, str],
    on_provider_event: Callable[[dict[str, object]], None] | None = None,
) -> Path:
    """Write the first OpenAI-compatible image result and retain cost evidence."""

    if not response.is_success:
        raise _response_error(response)
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("direct image API returned non-JSON response") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("direct image API returned an invalid response payload")
    items = payload.get("data") or payload.get("images") or payload.get("output") or []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise RuntimeError("direct image API returned no image result")
    item = items[0]
    encoded = item.get("b64_json") or item.get("image_base64") or item.get("base64")
    if isinstance(encoded, str) and encoded.strip():
        raw = encoded.split(",", 1)[-1]
        try:
            output_path.write_bytes(base64.b64decode(raw))
        except ValueError as exc:
            raise RuntimeError("direct image API returned invalid base64 image data") from exc
    else:
        image_url = item.get("url") or item.get("image_url") or item.get("download_url")
        if not isinstance(image_url, str) or not image_url.strip():
            raise RuntimeError("direct image API returned no image URL or base64 data")
        download = await client.get(image_url, headers=headers)
        if not download.is_success:
            raise _response_error(download)
        output_path.write_bytes(download.content)
    _emit_provider_cost_event(payload, on_provider_event)
    return output_path


def _write_gemini_image_response(
    response: httpx.Response,
    *,
    output_path: Path,
    on_provider_event: Callable[[dict[str, object]], None] | None = None,
) -> Path:
    """Write the first native Gemini inline image to ``output_path``."""

    if not response.is_success:
        raise _response_error(response)
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Gemini image API returned non-JSON response") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Gemini image API returned an invalid response payload")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise RuntimeError("Gemini image API returned no candidates")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        parts = content.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            inline = part.get("inlineData") or part.get("inline_data")
            if not isinstance(inline, dict):
                continue
            encoded = inline.get("data")
            if not isinstance(encoded, str) or not encoded.strip():
                continue
            raw = encoded.split(",", 1)[-1]
            try:
                output_path.write_bytes(base64.b64decode(raw))
            except ValueError as exc:
                raise RuntimeError(
                    "Gemini image API returned invalid base64 image data"
                ) from exc
            _emit_provider_cost_event(payload, on_provider_event)
            return output_path
    raise RuntimeError("Gemini image API returned no inline image result")


def _emit_provider_cost_event(
    payload: Mapping[str, Any],
    callback: Callable[[dict[str, object]], None] | None,
) -> None:
    if callback is None:
        return
    fields = extract_provider_cost_evidence(payload, prefix="result").as_event_fields()
    if not fields:
        return
    try:
        callback({"stage": "provider_cost", **fields})
    except Exception:
        logger.debug("direct image provider cost callback failed", exc_info=True)


async def generate_direct_image(
    *,
    model: DirectImageModel,
    prompt: str,
    output_path: Path,
    aspect_ratio: str,
    image_size: str,
    quality: str | None,
    reference_paths: Iterable[str] = (),
    advanced_settings: dict[str, Any] | None = None,
    on_provider_event: Callable[[dict[str, object]], None] | None = None,
) -> Path:
    """Generate or edit one image, resolving the backup channel when pinned.

    The fallback is opt-in through ``VILLAGE_CANVAS_DIRECT_IMAGE_FALLBACK``: a
    channel switch spends a second paid request, so the default stays single
    channel even when a policy block is the failure.
    """

    try:
        return await _generate_direct_image_once(
            model=model,
            prompt=prompt,
            output_path=output_path,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            quality=quality,
            reference_paths=reference_paths,
            advanced_settings=advanced_settings,
            on_provider_event=on_provider_event,
        )
    except DirectImageUpstreamError as exc:
        fallback = (
            resolve_direct_image_fallback_model(model)
            if exc.error_code == "DIRECT_IMAGE_SAFETY_BLOCK"
            else None
        )
        if fallback is None:
            raise
        logger.warning(
            "direct image channel %s is still blocked after the bounded retry; "
            "switching to the operator-pinned backup channel %s",
            model.registry_id,
            fallback.registry_id,
        )
        try:
            return await _generate_direct_image_once(
                model=fallback,
                prompt=prompt,
                output_path=output_path,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                quality=quality,
                reference_paths=reference_paths,
                advanced_settings=advanced_settings,
                on_provider_event=on_provider_event,
            )
        except DirectImageUpstreamError as fallback_exc:
            metadata = dict(fallback_exc.provider_error_metadata)
            metadata["suggested_action"] = (
                f"{metadata.get('suggested_action') or ''}"
                f"（已自动切换到备源 {fallback.label} 仍未通过）"
            ).strip()
            raise DirectImageUpstreamError(
                str(fallback_exc),
                error_code=fallback_exc.error_code,
                stage=str(metadata.get("stage") or "submit"),
                retryable=fallback_exc.retryable,
                suggested_action=str(metadata["suggested_action"]),
                http_status=fallback_exc.provider_error_metadata.get("http_status"),
                protocol=fallback.protocol,
                request_contract=fallback_exc.provider_error_metadata.get("request_contract"),
            ) from fallback_exc


async def _generate_direct_image_once(
    *,
    model: DirectImageModel,
    prompt: str,
    output_path: Path,
    aspect_ratio: str,
    image_size: str,
    quality: str | None,
    reference_paths: Iterable[str] = (),
    advanced_settings: dict[str, Any] | None = None,
    on_provider_event: Callable[[dict[str, object]], None] | None = None,
) -> Path:
    """Generate or edit one image using only the selected direct endpoint."""
    from novelvideo.generators.direct_models import (
        DirectModel,
        ensure_direct_model_runtime_ready,
    )

    runtime_model = DirectModel(
        kind="image",
        registry_id=model.registry_id,
        label=model.label,
        upstream_model=model.upstream_model,
        base_url=model.base_url,
        api_key=model.api_key,
        protocol=model.protocol,
        enabled=model.enabled,
        is_default=model.is_default,
    )
    ensure_direct_model_runtime_ready(runtime_model)
    references = [str(path) for path in reference_paths if str(path).strip()]
    profile = model.profile
    if references and IMAGE_MODE_IMAGE_TO_IMAGE not in profile.modes:
        raise RuntimeError(f"{model.label} 已识别为仅支持文生图，不能用于图生图")
    _validate_direct_image_parameters(
        model=model,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
    )
    # Normalize here as well as at the transport boundary so the request that
    # actually leaves the process shows the values the panel displayed.
    resolved_advanced = profile.resolve_advanced_settings(advanced_settings)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    contract = get_model_contract(model.protocol)
    if not contract.runtime_ready("image"):
        raise RuntimeError(f"{model.label} 的协议尚未形成可执行生图合同")
    headers = contract.auth.headers(model.api_key)
    is_gemini_image = model.protocol == DIRECT_MODEL_PROTOCOL_GEMINI_IMAGE
    fields = (
        _gemini_image_request_fields(
            model=model,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            reference_paths=references,
        )
        if is_gemini_image
        else _direct_image_request_fields(
            model=model,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            quality=quality,
            advanced_settings=resolved_advanced,
        )
    )

    async with httpx.AsyncClient(
        **newapi_httpx_client_kwargs(
            base_url=model.base_url,
            timeout=image_submit_timeout(),
            follow_redirects=True,
        )
    ) as client:

        async def _submit() -> httpx.Response:
            if is_gemini_image:
                return await client.post(
                    _gemini_image_endpoint(model.base_url, model.upstream_model),
                    headers={**headers, "Content-Type": "application/json"},
                    json=fields,
                )
            if references:
                return await client.post(
                    join_contract_endpoint(
                        model.base_url,
                        contract.endpoints.edit_path or "",
                    ),
                    headers=headers,
                    data={
                        key: json.dumps(value, ensure_ascii=False)
                        if isinstance(value, (dict, list))
                        else str(value)
                        for key, value in fields.items()
                    },
                    files=_image_files(references, field_name=profile.edit_file_field),
                )
            return await client.post(
                join_contract_endpoint(
                    model.base_url,
                    contract.endpoints.invoke_path,
                ),
                headers={**headers, "Content-Type": "application/json"},
                json=fields,
            )

        attempt = 1
        # 收到过 HTTP 响应的付费提交次数，与纯网络重试分开计数，避免一次传输
        # 重试把安全拒绝的「已重试」判定提前用掉。
        response_attempt = 1
        while True:
            try:
                response = await _submit()
            except httpx.TransportError as exc:
                safe_to_retry = _direct_image_submit_retry_is_safe(exc)
                permanent_request_failure = (
                    type(exc).__name__ in _DIRECT_IMAGE_PERMANENT_TRANSPORT_EXCEPTIONS
                )
                if (
                    attempt < DIRECT_IMAGE_TRANSIENT_MAX_ATTEMPTS
                    and safe_to_retry
                ):
                    delay = DIRECT_IMAGE_TRANSIENT_BACKOFF_SECONDS[
                        min(attempt - 1, len(DIRECT_IMAGE_TRANSIENT_BACKOFF_SECONDS) - 1)
                    ]
                    logger.warning(
                        "direct image upstream transport failure (%s: %s); "
                        "retry %s/%s after %.1fs (model=%s protocol=%s)",
                        type(exc).__name__,
                        exc,
                        attempt + 1,
                        DIRECT_IMAGE_TRANSIENT_MAX_ATTEMPTS,
                        delay,
                        model.upstream_model,
                        model.protocol,
                    )
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                ambiguous_result = not safe_to_retry and not permanent_request_failure
                raise DirectImageUpstreamError(
                    (
                        "direct image API submit result is unknown: "
                        if ambiguous_result
                        else "direct image API transport failure: "
                    )
                    + f"{type(exc).__name__}: {exc}".strip(),
                    error_code=(
                        "DIRECT_IMAGE_SUBMIT_RESULT_UNKNOWN"
                        if ambiguous_result
                        else "DIRECT_IMAGE_TRANSPORT_FAILURE"
                    ),
                    stage="submit",
                    retryable=not ambiguous_result,
                    suggested_action=(
                        "上游连接在收到响应前中断，无法确认是否已经生成或计费。"
                        "请先检查任务输出与上游账单，确认没有结果后再手动重试，"
                        "不要立即重复提交。"
                        if ambiguous_result
                        else "请求在本地协议校验阶段失败，未形成可提交的上游请求；"
                        "请检查 Base URL、协议与请求参数后再试。"
                        if permanent_request_failure
                        else "上游连接尚未建立即中断，可直接重试；"
                        "若连续出现请检查该渠道的网络与可用性。"
                    ),
                    protocol=model.protocol,
                ) from exc
            if response.is_success:
                break
            logger.warning(
                "direct image upstream rejected the request "
                "(model=%s protocol=%s status=%s attempt=%s response_attempt=%s "
                "references=%s prompt_chars=%s)",
                model.upstream_model,
                model.protocol,
                response.status_code,
                attempt,
                response_attempt,
                len(references),
                len(str(prompt or "")),
            )
            failure = _direct_image_uploaded_failure(
                response,
                model=model,
                attempt=response_attempt,
            )
            retryable_block = (
                failure is not None
                and failure.provider_error_metadata.get("error_code")
                == "DIRECT_IMAGE_SAFETY_BLOCK_RETRY"
                and response_attempt < DIRECT_IMAGE_SAFETY_RETRY_MAX_ATTEMPTS
                and _direct_image_safety_retry_enabled()
            )
            transient_server_error = (
                response.status_code in DIRECT_IMAGE_TRANSIENT_STATUS_CODES
            )
            # 中转站把「它自己那一跳失败」包装成 400 的情况也走同一套有限次重发。
            relay_side_failure = (
                failure is not None
                and failure.provider_error_metadata.get("error_code")
                == "DIRECT_IMAGE_UPSTREAM_SIDE_FAILURE"
            )
            retryable_server_error = (
                (transient_server_error or relay_side_failure)
                and response_attempt < DIRECT_IMAGE_RESPONSE_RETRY_MAX_ATTEMPTS
                and _direct_image_response_retry_enabled()
            )
            if retryable_block:
                logger.warning(
                    "direct image upstream returned a generic safety wrapper; "
                    "resending the identical payload once (model=%s status=%s)",
                    model.upstream_model,
                    response.status_code,
                )
                await asyncio.sleep(DIRECT_IMAGE_SAFETY_RETRY_BACKOFF_SECONDS)
            elif retryable_server_error:
                delay = DIRECT_IMAGE_RESPONSE_RETRY_BACKOFF_SECONDS[
                    min(
                        response_attempt - 1,
                        len(DIRECT_IMAGE_RESPONSE_RETRY_BACKOFF_SECONDS) - 1,
                    )
                ]
                logger.warning(
                    "direct image upstream returned a transient HTTP %s%s; "
                    "resending the identical payload %s/%s after %.1fs "
                    "(model=%s protocol=%s)",
                    response.status_code,
                    " (relay-side failure)" if relay_side_failure else "",
                    response_attempt + 1,
                    DIRECT_IMAGE_RESPONSE_RETRY_MAX_ATTEMPTS,
                    delay,
                    model.upstream_model,
                    model.protocol,
                )
                await asyncio.sleep(delay)
            else:
                raise failure if failure is not None else _response_error(response)
            response_attempt += 1

        # The result hop is a separate request: a relay that answers the submit
        # and then drops the `url` download raises the same transport errors.
        # Retrying here reuses the already-received body, so it never spends a
        # second paid generation.
        download_attempt = 1
        while True:
            try:
                result = (
                    _write_gemini_image_response(
                        response,
                        output_path=output_path,
                        on_provider_event=on_provider_event,
                    )
                    if is_gemini_image
                    else await _write_image_response(
                        client,
                        response,
                        output_path=output_path,
                        headers=headers,
                        on_provider_event=on_provider_event,
                    )
                )
                break
            except httpx.TransportError as exc:
                if (
                    download_attempt < DIRECT_IMAGE_TRANSIENT_MAX_ATTEMPTS
                    and _direct_image_is_transient_transport_exception(exc)
                ):
                    delay = DIRECT_IMAGE_TRANSIENT_BACKOFF_SECONDS[
                        min(
                            download_attempt - 1,
                            len(DIRECT_IMAGE_TRANSIENT_BACKOFF_SECONDS) - 1,
                        )
                    ]
                    logger.warning(
                        "direct image result download failed (%s: %s); "
                        "retry %s/%s after %.1fs (model=%s)",
                        type(exc).__name__,
                        exc,
                        download_attempt + 1,
                        DIRECT_IMAGE_TRANSIENT_MAX_ATTEMPTS,
                        delay,
                        model.upstream_model,
                    )
                    await asyncio.sleep(delay)
                    download_attempt += 1
                    continue
                raise DirectImageUpstreamError(
                    f"direct image API result download failure: "
                    f"{type(exc).__name__}: {exc}".strip(),
                    error_code="DIRECT_IMAGE_TRANSPORT_FAILURE",
                    stage="download",
                    retryable=True,
                    suggested_action=(
                        "图片已生成但下载被中断，可直接重试；"
                        "若连续出现请检查该渠道的出图存储与网络。"
                    ),
                    protocol=model.protocol,
                ) from exc
        try:
            from novelvideo.generators.direct_model_capability_cache import (
                record_direct_model_runtime_verified,
            )

            record_direct_model_runtime_verified(
                base_url=model.base_url,
                kind="image",
                upstream_model=model.upstream_model,
                protocol=model.protocol,
            )
        except OSError:
            pass
        return result
