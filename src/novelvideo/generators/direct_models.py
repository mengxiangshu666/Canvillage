"""Saved direct-model registry shared by Village Canvas runtime adapters.

Every non-video model family is configured once in the local model-gateway
settings store.  Canvas nodes only persist the opaque ``direct/<registry-id>``
selection; this module resolves that selection to the server-side endpoint and
credential when a task actually runs.  Browser clients therefore never need to
carry direct-provider API keys.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal

from pydantic_ai.models.wrapper import WrapperModel

from novelvideo.generators.direct_model_capabilities import (
    DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES,
    DIRECT_MODEL_PROTOCOL_GEMINI,
    direct_embedding_dimensions,
    direct_model_capability_summary,
    direct_model_protocol_label,
    infer_direct_model_protocol,
)
from novelvideo.generators.model_contracts import get_model_contract
from novelvideo.model_gateway_settings import (
    DIRECT_MODEL_ALIAS_KINDS,
    canonical_direct_model_kind,
    get_direct_models,
)


DirectModelKind = Literal[
    "chat",
    "agent",
    "text",
    "vision",
    "image",
    "embedding",
    # Retired from the model center but still returned for saved audio
    # bindings; see ``model_gateway_settings.DIRECT_MODEL_RETIRED_KINDS``.
    "audio",
]
DIRECT_MODEL_PREFIX = "direct/"
_LEGACY_AGENT_PREFIX = "direct-agent:"
_DIRECT_REF_RE = re.compile(r"^direct/[a-z0-9][a-z0-9-]{1,62}$")


@dataclass(frozen=True, slots=True)
class DirectModel:
    """One server-owned OpenAI-compatible endpoint from the direct registry."""

    kind: DirectModelKind
    registry_id: str
    label: str
    upstream_model: str
    base_url: str
    api_key: str
    enabled: bool
    is_default: bool
    protocol: str = "openai-compatible"
    supported_modes: tuple[str, ...] = ()
    #: Registry ids this row absorbed when the retired chat families merged.
    registry_aliases: tuple[str, ...] = ()
    supports_tools: bool = True
    supports_vision: bool = True

    @property
    def catalog_id(self) -> str:
        return f"{DIRECT_MODEL_PREFIX}{self.registry_id}"

    @property
    def resolvable_ids(self) -> tuple[str, ...]:
        return (self.registry_id, *self.registry_aliases)


def resolved_direct_embedding_dimensions(model: DirectModel) -> int:
    """Return the verified native vector size for one direct embedding model."""

    model_kind = str(getattr(model, "kind", "embedding") or "embedding").strip().lower()
    if model_kind != "embedding":
        raise ValueError(f"expected embedding model, got {model_kind}")
    from novelvideo.generators.direct_model_capability_cache import (
        get_cached_direct_model_capability,
    )

    cached = get_cached_direct_model_capability(
        base_url=model.base_url,
        kind=model_kind,
        upstream_model=model.upstream_model,
    )
    candidates = [cached.get("embeddingDimensions")]
    metadata = cached.get("modelMetadata")
    if isinstance(metadata, dict):
        candidates.extend(
            metadata.get(key)
            for key in (
                "embeddingDimensions",
                "embedding_dimensions",
                "outputDimensions",
                "output_dimensions",
                "dimensions",
            )
        )
    for value in candidates:
        try:
            dimensions = int(value)
        except (TypeError, ValueError):
            continue
        if dimensions > 0:
            return dimensions
    return direct_embedding_dimensions(model.upstream_model)


def resolved_direct_model_modes(model: DirectModel) -> tuple[str, ...]:
    """Resolve configured modes against any explicit upstream declaration.

    ``audio`` is the one family whose upstream rarely publishes modes, so its
    saved per-row ``supportedModes`` (written by the retired audio registry) is
    the only way a text-to-music row is distinguishable from a speech-only one.
    Dropping it left ``ensure_direct_model_supports_mode`` comparing against the
    static default ``("text_to_speech",)`` and rejecting every music request.
    """

    from novelvideo.generators.direct_model_capability_cache import (
        get_cached_direct_model_capability,
    )

    cached = get_cached_direct_model_capability(
        base_url=model.base_url,
        kind=model.kind,
        upstream_model=model.upstream_model,
    )
    metadata = cached.get("modelMetadata")
    capabilities = direct_model_capability_summary(
        model.kind,
        model.upstream_model,
        protocol=model.protocol,
        base_url=model.base_url,
        metadata=metadata if isinstance(metadata, dict) else None,
    )
    provider_modes = tuple(
        str(mode or "").strip() for mode in capabilities.get("supportedModes", ()) if str(mode or "").strip()
    )
    if model.kind != "audio":
        return provider_modes
    configured = model.supported_modes or ("text_to_speech",)
    if "supportedModes" in set(capabilities.get("declaredCapabilities") or ()):
        provider_set = set(provider_modes)
        return tuple(mode for mode in configured if mode in provider_set)
    return tuple(configured)


def ensure_direct_model_supports_mode(model: DirectModel, mode: str) -> DirectModel:
    required = str(mode or "").strip()
    if required in resolved_direct_model_modes(model):
        return model
    raise ValueError(f"直连{model.kind}模型不支持当前模式：{required}")


def _normalize_kind(kind: str) -> DirectModelKind:
    """Validate one requested family, keeping the caller's own name.

    ``agent`` / ``text`` / ``vision`` still resolve here (and onto the shared
    chat registry) because saved node bindings and runtime adapters pass them.
    """

    clean = str(kind or "").strip().lower()
    if clean in DIRECT_MODEL_ALIAS_KINDS:
        return clean  # type: ignore[return-value]
    return canonical_direct_model_kind(clean)  # type: ignore[return-value]


def list_direct_models(kind: str, *, include_disabled: bool = True) -> tuple[DirectModel, ...]:
    """Return locally configured models in the exact operator-defined order."""

    normalized_kind = _normalize_kind(kind)
    models = tuple(
        DirectModel(
            kind=normalized_kind,
            registry_id=str(item["id"]),
            label=str(item["label"]),
            upstream_model=str(item["modelId"]),
            base_url=str(item["baseUrl"]),
            api_key=str(item["apiKey"]),
            protocol=infer_direct_model_protocol(
                normalized_kind,
                str(item["modelId"]),
                base_url=str(item["baseUrl"]),
                requested_protocol=str(
                    item.get("requestedProtocol") or item.get("protocol") or "auto"
                ),
            ),
            enabled=bool(item.get("enabled", True)),
            is_default=bool(item.get("isDefault", False)),
            # Only ``audio`` rows carry a saved per-row mode list: it is what
            # separates a speech model from a music model (see
            # ``resolved_direct_model_modes``).
            supported_modes=(
                tuple(item.get("supportedModes") or ()) if normalized_kind == "audio" else ()
            ),
            registry_aliases=tuple(str(alias) for alias in item.get("aliases") or ()),
            supports_tools=bool(item.get("supportsTools", True)),
            supports_vision=bool(item.get("supportsVision", True)),
        )
        for item in get_direct_models(normalized_kind)
    )
    if normalized_kind == "agent":
        models = tuple(item for item in models if item.supports_tools)
    elif normalized_kind == "vision":
        models = tuple(item for item in models if item.supports_vision)
    return models if include_disabled else tuple(item for item in models if item.enabled)


def _default_enabled_model(models: tuple[DirectModel, ...]) -> DirectModel | None:
    return next((item for item in models if item.enabled and item.is_default), None) or next(
        (item for item in models if item.enabled),
        None,
    )


def resolve_direct_model(kind: str, model_ref: str | None = None) -> DirectModel | None:
    """Resolve a catalog ID, legacy Agent ID, or empty default without guessing.

    An unrecognised non-direct string deliberately returns ``None`` so existing
    legacy NewAPI routes continue to work.  An explicit ``direct/...`` id that
    is missing or disabled resolves to ``None`` and the caller can produce a
    precise configuration error instead of silently switching providers.
    """

    normalized_kind = _normalize_kind(kind)
    models = list_direct_models(normalized_kind)
    raw = str(model_ref or "").strip()
    if not raw:
        return _default_enabled_model(models)

    requested = raw.lower()
    if normalized_kind == "agent" and requested.startswith(_LEGACY_AGENT_PREFIX):
        upstream = raw[len(_LEGACY_AGENT_PREFIX) :].strip()
        return next(
            (
                item
                for item in models
                if item.enabled and item.upstream_model.casefold() == upstream.casefold()
            ),
            None,
        )

    if requested.startswith(DIRECT_MODEL_PREFIX):
        requested = requested[len(DIRECT_MODEL_PREFIX) :]
    elif not any(
        requested in item.resolvable_ids for item in models
    ):
        return None

    return next(
        (
            item
            for item in models
            if requested in item.resolvable_ids and item.enabled
        ),
        None,
    )


def require_direct_model(kind: str, model_ref: str | None = None) -> DirectModel:
    """Resolve a direct model or raise a useful configuration error."""

    model = resolve_direct_model(kind, model_ref)
    if model is not None:
        return model
    requested = str(model_ref or "").strip() or "默认模型"
    raise ValueError(f"直连{_normalize_kind(kind)}模型不可用：{requested}")


def is_direct_model_ref(value: str | None) -> bool:
    """Return whether a value explicitly targets the saved direct registry."""

    return bool(_DIRECT_REF_RE.fullmatch(str(value or "").strip().lower()))


def get_direct_pydantic_model(
    kind: str,
    model_ref: str | None = None,
    *,
    timeout_seconds: float = 120.0,
):
    """Build a PydanticAI model for a resolved direct chat/vision endpoint.

    Returns ``None`` when a legacy model was explicitly selected and lets the
    caller retain its existing NewAPI compatibility path.
    """

    direct = resolve_direct_model(kind, model_ref)
    if direct is None:
        return None
    ensure_direct_model_runtime_ready(direct)
    normalized_kind = _normalize_kind(kind)
    contract = get_model_contract(direct.protocol)
    if not contract.runtime_ready(normalized_kind):
        raise ValueError(
            f"直连{normalized_kind}模型 {direct.label} 已识别为"
            f"{direct_model_protocol_label(direct.protocol)}，当前执行器不会按 OpenAI 兼容协议乱调。"
        )

    if direct.protocol == DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES:
        return _anthropic_pydantic_model(
            direct,
            timeout_seconds=timeout_seconds,
        )
    if direct.protocol == DIRECT_MODEL_PROTOCOL_GEMINI:
        return _gemini_pydantic_model(
            direct,
            timeout_seconds=timeout_seconds,
        )

    from novelvideo.config import get_openai_compatible_pydantic_model
    return _RuntimeVerifiedModel(
        get_openai_compatible_pydantic_model(
            model_name=direct.upstream_model,
            api_key=direct.api_key,
            base_url=direct.base_url,
            timeout_seconds=timeout_seconds,
            # Chat completions are idempotent from the caller's perspective;
            # let the SDK absorb transient 429/5xx gateway failures instead of
            # turning one upstream hiccup into a failed workflow step.
            max_retries=2,
        ),
        direct,
    )


class _RuntimeVerifiedModel(WrapperModel):
    """Delegate to a PydanticAI model and record only successful requests."""

    def __init__(self, model, direct: DirectModel) -> None:
        super().__init__(model)
        self._direct = direct

    async def request(self, *args, **kwargs):
        result = await self.wrapped.request(*args, **kwargs)
        self._record_success()
        return result

    def request_stream(self, *args, **kwargs):
        return _VerifiedStreamContext(
            self.wrapped.request_stream(*args, **kwargs),
            self._record_success,
        )

    def _record_success(self) -> None:
        try:
            from novelvideo.generators.direct_model_capability_cache import (
                record_direct_model_runtime_verified,
            )

            record_direct_model_runtime_verified(
                base_url=self._direct.base_url,
                kind=self._direct.kind,
                upstream_model=self._direct.upstream_model,
                protocol=self._direct.protocol,
            )
        except OSError:
            pass


class _VerifiedStreamContext:
    def __init__(self, context, on_success) -> None:
        self._context = context
        self._on_success = on_success

    async def __aenter__(self):
        response = await self._context.__aenter__()
        self._on_success()
        return response

    async def __aexit__(self, exc_type, exc, traceback):
        return await self._context.__aexit__(exc_type, exc, traceback)


def _wrap_runtime_model(model, direct: DirectModel):
    return _RuntimeVerifiedModel(model, direct)


def _direct_http_client(*, base_url: str, timeout_seconds: float):
    import httpx

    from novelvideo.gateway_transport import newapi_httpx_client_kwargs

    return httpx.AsyncClient(
        **newapi_httpx_client_kwargs(
            base_url=base_url,
            timeout=max(float(timeout_seconds), 1.0),
        )
    )


def _anthropic_pydantic_model(direct: DirectModel, *, timeout_seconds: float):
    from contextlib import asynccontextmanager
    from typing import Any

    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    class _AutoClosingAnthropicModel(AnthropicModel):
        async def request(self, *args: Any, **kwargs: Any) -> Any:
            async with self:
                return await super().request(*args, **kwargs)

        @asynccontextmanager
        async def request_stream(self, *args: Any, **kwargs: Any):
            async with self:
                async with super().request_stream(*args, **kwargs) as response:
                    yield response

    sdk_base_url = direct.base_url.rstrip("/")
    if sdk_base_url.lower().endswith("/v1"):
        sdk_base_url = sdk_base_url[:-3]
    provider = AnthropicProvider(
        api_key=direct.api_key,
        base_url=sdk_base_url,
        http_client=_direct_http_client(
            base_url=direct.base_url,
            timeout_seconds=timeout_seconds,
        ),
    )
    return _wrap_runtime_model(
        _AutoClosingAnthropicModel(direct.upstream_model, provider=provider),
        direct,
    )


def _gemini_pydantic_model(direct: DirectModel, *, timeout_seconds: float):
    from contextlib import asynccontextmanager
    from typing import Any

    from pydantic_ai.models.gemini import GeminiModel
    from pydantic_ai.providers.google_gla import GoogleGLAProvider

    class _AutoClosingGeminiModel(GeminiModel):
        async def request(self, *args: Any, **kwargs: Any) -> Any:
            async with self:
                return await super().request(*args, **kwargs)

        @asynccontextmanager
        async def request_stream(self, *args: Any, **kwargs: Any):
            async with self:
                async with super().request_stream(*args, **kwargs) as response:
                    yield response

    provider = GoogleGLAProvider(
        api_key=direct.api_key,
        http_client=_direct_http_client(
            base_url=direct.base_url,
            timeout_seconds=timeout_seconds,
        ),
    )
    return _wrap_runtime_model(
        _AutoClosingGeminiModel(direct.upstream_model, provider=provider),
        direct,
    )


def direct_model_option(model: DirectModel) -> dict[str, object]:
    """Return the browser-safe catalog item shared by node model pickers."""

    from novelvideo.generators.direct_model_capability_cache import (
        get_cached_direct_model_capability,
    )

    cached_capability = get_cached_direct_model_capability(
        base_url=model.base_url,
        kind=model.kind,
        upstream_model=model.upstream_model,
    )
    metadata = cached_capability.get("modelMetadata")
    if isinstance(metadata, dict):
        metadata = dict(metadata)
    else:
        metadata = {}
    if model.kind == "embedding" and cached_capability.get("embeddingDimensions"):
        metadata["embeddingDimensions"] = cached_capability["embeddingDimensions"]

    capabilities = direct_model_capability_summary(
        model.kind,
        model.upstream_model,
        protocol=model.protocol,
        base_url=model.base_url,
        metadata=metadata or None,
    )
    if model.kind == "audio":
        modes = list(resolved_direct_model_modes(model))
        capabilities["supportedModes"] = modes
        capabilities["supported_modes"] = modes
        capabilities["modeType"] = modes
        capabilities["mode_type"] = modes
        capabilities["useCase"] = (
            "已识别：配音与音乐生成"
            if len(modes) == 2
            else "已识别：音乐生成"
            if modes == ["text_to_music"]
            else "已识别：文字转语音"
        )
        capabilities["use_case"] = capabilities["useCase"]
    catalog_verification = direct_model_catalog_verification(model)
    from novelvideo.generators.direct_model_capability_cache import (
        direct_model_runtime_probe_complete,
    )
    runtime_probe_required = model.kind in {
        "chat",
        "agent",
        "text",
        "vision",
        "image",
        "embedding",
    }
    runtime_probe_complete = bool(
        cached_capability
        and str(cached_capability.get("protocol") or "").strip().lower()
        == str(model.protocol or "").strip().lower()
        and direct_model_runtime_probe_complete(model.kind, cached_capability)
    )
    chat_probe_rejected = (
        model.kind == "agent"
        and cached_capability.get("chatProbeStatus") in {"rejected", "probe-failed"}
    )
    runtime_ready = is_direct_model_runtime_ready(model)
    if not runtime_ready:
        capabilities["runtimeReady"] = False
        capabilities["runtime_ready"] = False
        if catalog_verification == "catalog-mismatch":
            capabilities["verificationStatus"] = "metadata"
        elif chat_probe_rejected:
            capabilities["verificationStatus"] = "degraded"
    return {
        "id": model.catalog_id,
        "label": model.label if model.enabled else f"{model.label}（已停用）",
        "modelId": model.upstream_model,
        "kind": model.kind,
        "enabled": model.enabled,
        "disabled": not model.enabled,
        "disabledReason": (
            "模型已在直连模型管理中停用"
            if not model.enabled
            else "上游模型目录未找到该模型 ID，请核对模型 ID 后重新检测"
            if catalog_verification == "catalog-mismatch"
            else "Agent 聊天接口检测失败，请检查模型权限或切换模型"
            if chat_probe_rejected
            else "模型需要重新检测连接以完成运行合同"
            if runtime_probe_required and not runtime_probe_complete
            else "模型尚未通过上游目录校验，请先检测连接"
            if not runtime_ready
            else ""
        ),
        "catalogVerification": catalog_verification,
        "runtimeProbeRequired": runtime_probe_required,
        "runtimeProbeComplete": runtime_probe_complete,
        "probeContractVersion": cached_capability.get("probeContractVersion"),
        "isDefault": model.is_default,
        "provider": "direct",
        **capabilities,
        "modelKey": model.catalog_id,
        "model_key": model.catalog_id,
    }


def _catalog_rejects_model_id(model: DirectModel) -> bool:
    from novelvideo.generators.direct_model_capability_cache import (
        get_cached_direct_model_capability,
    )

    cached = get_cached_direct_model_capability(
        base_url=model.base_url,
        kind=model.kind,
        upstream_model=model.upstream_model,
    )
    return (
        cached.get("modelFound") is False
        and int(cached.get("discoveredModelCount") or 0) > 0
    )


def _catalog_confirms_model_id(model: DirectModel) -> bool:
    from novelvideo.generators.direct_model_capability_cache import (
        get_cached_direct_model_capability,
    )

    cached = get_cached_direct_model_capability(
        base_url=model.base_url,
        kind=model.kind,
        upstream_model=model.upstream_model,
    )
    return cached.get("modelFound") is True


def direct_model_catalog_verification(model: DirectModel) -> str:
    from novelvideo.generators.direct_model_capability_cache import (
        get_cached_direct_model_capability,
    )

    cached = get_cached_direct_model_capability(
        base_url=model.base_url,
        kind=model.kind,
        upstream_model=model.upstream_model,
    )
    if not cached:
        return "unverified"
    cached_protocol = str(cached.get("protocol") or "").strip().lower()
    if not cached_protocol or cached_protocol != str(model.protocol or "").strip().lower():
        return "unverified"
    if cached.get("modelFound") is True:
        return "runtime-verified" if (
            str(cached.get("verificationStatus") or "") == "runtime-verified"
        ) else "catalog-confirmed"
    if str(cached.get("verificationStatus") or "") == "runtime-verified":
        # Preview/experimental models are callable without appearing in the
        # catalog; an actually-usable response is the stronger evidence.
        from novelvideo.generators.direct_model_capability_cache import (
            direct_model_runtime_probe_complete,
        )

        if direct_model_runtime_probe_complete(str(model.kind), cached):
            return "runtime-verified"
    if (
        cached.get("modelFound") is False
        and int(cached.get("discoveredModelCount") or 0) > 0
    ):
        return "catalog-mismatch"
    return "unverified"


def require_verified_direct_model_catalog() -> bool:
    return os.environ.get(
        "VILLAGE_CANVAS_REQUIRE_VERIFIED_DIRECT_MODELS",
        "0",
    ).strip().lower() in {"1", "true", "yes", "on"}


def is_direct_model_runtime_ready(model: DirectModel) -> bool:
    """Return whether a saved direct model has a usable, confirmed route.

    A reachable ``/models`` endpoint is not enough when it returns a
    non-empty catalog: the configured upstream model ID must be present in
    that catalog, otherwise the first real request fails later as a provider
    ``401``/``ModelError``.
    """

    catalog_verification = direct_model_catalog_verification(model)
    from novelvideo.generators.direct_model_capability_cache import (
        direct_model_runtime_probe_complete,
        get_cached_direct_model_capability,
    )

    cached = get_cached_direct_model_capability(
        base_url=model.base_url,
        kind=model.kind,
        upstream_model=model.upstream_model,
    )
    if model.kind in {"chat", "agent", "text", "vision", "image", "embedding"}:
        # A missing probe is intentionally distinct from a failed probe.  Both
        # require the operator to press Detect again before the node can run;
        # this prevents old catalog-only/runtime-success cache entries from
        # masquerading as a complete current contract.
        if cached and not direct_model_runtime_probe_complete(model.kind, cached):
            return False
    catalog_ready = catalog_verification in {
        "catalog-confirmed",
        "runtime-verified",
    }
    if catalog_verification == "catalog-mismatch":
        return False
    return bool(
        model.enabled
        and get_model_contract(model.protocol).runtime_ready(model.kind)
        and (catalog_ready or not require_verified_direct_model_catalog())
    )


def ensure_direct_model_runtime_ready(model: DirectModel) -> DirectModel:
    """Stop a node before it sends a known-invalid direct-model request."""

    if is_direct_model_runtime_ready(model):
        return model
    verification = direct_model_catalog_verification(model)
    if not model.enabled:
        raise ValueError(f"直连{model.kind}模型已停用：{model.label}")
    if verification == "catalog-mismatch":
        raise ValueError(
            f"直连{model.kind}模型的上游目录未匹配模型 ID：{model.upstream_model}"
        )
    if verification == "unverified" and require_verified_direct_model_catalog():
        raise ValueError(
            f"直连{model.kind}模型尚未通过上游目录校验：{model.upstream_model}"
        )
    raise ValueError(
        f"直连{model.kind}模型协议不可执行：{model.protocol}"
    )


__all__ = [
    "DIRECT_MODEL_PREFIX",
    "DirectModel",
    "DirectModelKind",
    "direct_embedding_dimensions",
    "direct_model_catalog_verification",
    "ensure_direct_model_supports_mode",
    "ensure_direct_model_runtime_ready",
    "direct_model_option",
    "get_direct_pydantic_model",
    "is_direct_model_runtime_ready",
    "is_direct_model_ref",
    "list_direct_models",
    "require_direct_model",
    "require_verified_direct_model_catalog",
    "resolved_direct_embedding_dimensions",
    "resolved_direct_model_modes",
    "resolve_direct_model",
]
