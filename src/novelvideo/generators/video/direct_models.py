"""Direct OpenAI-video model registry shared by canvas, workflows, and dispatch.

The registry stores an operator-facing label separately from the exact upstream
model ID.  This is the seam that lets a creator replace a video provider
without patching every canvas node, model picker, or task runner.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping

from novelvideo.model_gateway_settings import get_direct_video_models

from .capabilities import (
    FallbackPolicy,
    Lifecycle,
    ModelCapability,
    VideoMode,
)
from .direct_video_probe import probe_direct_video_model
from .direct_video_profiles import (
    DirectVideoCapabilityProfile,
    resolve_direct_video_profile,
)
from .direct_video_capability_cache import get_cached_capability_for_model
from .direct_video_protocol_contracts import (
    DIRECT_VIDEO_PROTOCOL_AUTO,
    DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI,
    DIRECT_VIDEO_PROTOCOL_OPENAI,
    get_direct_video_protocol_contract,
    normalize_direct_video_protocol,
)
from .runtime_contract import is_generic_video_adapter
from .channel_wire_contract import WIRE_SOURCE_CHANNEL, ResolvedWireContract
from .upstream_profiles import (
    DOLASD_OPENAI_VIDEO_PROFILE_ID,
    relative_wire_route,
    resolve_wire_contract,
)


DIRECT_VIDEO_BACKEND_PREFIX = "direct_"
DIRECT_VIDEO_DEFAULT_BACKEND = f"{DIRECT_VIDEO_BACKEND_PREFIX}default"
DIRECT_VIDEO_CATALOG_REVISION = "direct-video.v1"

_PROVIDER_ORIENTATION_RESOLUTION = re.compile(
    # 方向与像素尺寸可以同时出现：AutoDL 工作流 schema 给的是
    # ``768p竖(768*1344)`` / ``1440p横(2560*1440)``。早先写成 ``(?:横|竖|\(…\))``
    # 的三选一，只认「只有方向」或「只有括号」，于是这些组合标签原样漏进
    # ``ModelCapability``，触发 "resolution entries must use the '<height>p' form"，
    # 合同判定为 invalid-contract，模型被停用（2026-09-15 真机事故：
    # ``minimax_h3_z0901`` 检测通过、保存后提示「模型能力合同无效」）。
    r"^([1-9][0-9]{2,5})p(?:横|竖)?(?:\([^)]*\))?$",
    re.IGNORECASE,
)
#: ``720 x 1280`` / ``480 p`` style labels survive ``.strip()``.
_CAPABILITY_RESOLUTION_WHITESPACE = re.compile(r"\s+")
_CAPABILITY_RECORD_LIMIT = 128
_CAPABILITY_RECORD_KEY_LIMIT = 64
_CAPABILITY_RECORD_DEPTH_LIMIT = 5
_CAPABILITY_RECORD_LIST_LIMIT = 128
_CAPABILITY_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|cookie|authorization)",
    re.IGNORECASE,
)


def _canonical_capability_resolutions(values: tuple[str, ...]) -> tuple[str, ...]:
    """Keep provider orientation suffixes out of the shared capability type.

    Provider schemas spell the same resolution several ways — ``480p竖``,
    ``480p 竖``, ``720×1280``, ``720 x 1280`` — while ``ModelCapability`` only
    accepts ``_RESOLUTION``.  Anything this function fails to rewrite reaches
    ``__post_init__`` unchanged and is rejected as ``invalid-contract``, which
    disables the model in the model center.  So fold the full-width multiply
    sign and all internal whitespace *before* matching: neither form can occur
    in an already-valid ``_RESOLUTION`` label, which makes the rewrite strictly
    additive.
    """

    normalized: list[str] = []
    for value in values:
        item = str(value).replace("×", "x").replace("＊", "*")
        item = _CAPABILITY_RESOLUTION_WHITESPACE.sub("", item)
        match = _PROVIDER_ORIENTATION_RESOLUTION.fullmatch(item)
        canonical = f"{match.group(1)}p" if match else item
        if canonical and canonical not in normalized:
            normalized.append(canonical)
    return tuple(normalized)


def _safe_capability_record_value(value: object, *, depth: int = 0) -> object:
    """Bound cached workflow metadata before exposing it to UI/runtime."""

    if depth >= _CAPABILITY_RECORD_DEPTH_LIMIT:
        return "[truncated]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key, raw_value in list(value.items())[:_CAPABILITY_RECORD_KEY_LIMIT]:
            key = str(raw_key or "").strip()
            if not key or _CAPABILITY_SENSITIVE_KEY.search(key):
                continue
            result[key] = _safe_capability_record_value(raw_value, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        return [
            _safe_capability_record_value(item, depth=depth + 1)
            for item in list(value)[:_CAPABILITY_RECORD_LIST_LIMIT]
        ]
    return str(value)


def _normalize_capability_records(value: object) -> list[dict[str, object]]:
    """Normalize list/object/options shapes into bounded record lists.

    Discovery providers have used all three shapes for workflow rules and
    resolution mappings. Invalid scalar values are discarded rather than
    becoming a misleading one-item capability contract.
    """

    candidates: object
    if isinstance(value, Mapping):
        options = value.get("options")
        candidates = options if isinstance(options, (list, tuple)) else [value]
    elif isinstance(value, (list, tuple)):
        candidates = value
    else:
        return []
    result: list[dict[str, object]] = []
    for item in list(candidates)[:_CAPABILITY_RECORD_LIMIT]:
        if not isinstance(item, Mapping):
            continue
        normalized = _safe_capability_record_value(item)
        if isinstance(normalized, dict) and normalized:
            result.append(normalized)
    return result


def _cached_capability_records(
    cached: Mapping[str, Any], *keys: str
) -> list[dict[str, object]]:
    """Read the first present capability field using camel/snake aliases."""

    for key in keys:
        if key in cached:
            return _normalize_capability_records(cached.get(key))
    return []


def _declared_wire_create_path(
    wire_contract: ResolvedWireContract, base_url: str
) -> str:
    """渠道合同自己写明的创建路径；没有或不该采信时返回空串。

    采用范围只到「渠道合同」和按上游域名固定的那几份合同：源码种子与通用兜底
    仍然走协议默认路径，免得改一条种子把一批已经配好的渠道一起挪走。
    """

    profile_id = str(getattr(wire_contract.profile, "profile_id", "") or "")
    if wire_contract.source != WIRE_SOURCE_CHANNEL and profile_id != (
        DOLASD_OPENAI_VIDEO_PROFILE_ID
    ):
        return ""
    return relative_wire_route(
        getattr(wire_contract.profile, "create_path", ""), base_url
    )


__all__ = [
    "DIRECT_VIDEO_BACKEND_PREFIX",
    "DIRECT_VIDEO_DEFAULT_BACKEND",
    "DIRECT_VIDEO_CATALOG_REVISION",
    "DirectVideoCapabilityProfile",
    "DirectVideoModel",
    "direct_video_model_option",
    "is_direct_video_backend",
    "list_direct_video_models",
    "probe_direct_video_model",
    "resolve_direct_video_model",
]


@dataclass(frozen=True, slots=True)
class DirectVideoModel:
    """One locally managed video endpoint with a discovered lifecycle contract."""

    registry_id: str
    label: str
    upstream_model: str
    base_url: str
    api_key: str
    enabled: bool
    requested_protocol: str = "auto"
    protocol: str = "openai-video"
    is_default: bool = False
    #: 渠道自带的出线合同（``direct_video_models`` 记录里的 ``wireContract``）。
    #: 它是这份渠道的权威合同：渠道删掉，合同一起消失；渠道添回来，合同一起回来。
    wire_contract: Mapping[str, Any] | None = None
    #: 存下来的渠道合同读不动时，解码层写在这里的原因。节点面板照样显示它，
    #: 免得「合同坏了」看起来像「这条渠道本来就没有合同」。
    wire_contract_error: str = ""

    @property
    def resolved_wire_contract(self) -> ResolvedWireContract:
        """按「渠道合同 → 源码种子 → 通用兜底」解析这份渠道的出线合同。"""

        return resolve_wire_contract(
            self.upstream_model,
            self.wire_contract,
            base_url=self.base_url,
            protocol=self.effective_protocol,
        )

    @property
    def backend(self) -> str:
        return f"{DIRECT_VIDEO_BACKEND_PREFIX}{self.registry_id}"

    @property
    def runtime_ready(self) -> bool:
        """Only publish models backed by a resolved executable contract."""
        from .direct_video_capability_cache import get_cached_capability_for_model

        latest = get_cached_capability_for_model(
            base_url=self.base_url,
            upstream_model=self.upstream_model,
        )
        if latest.get("probeStatus") in {"failed", "model-not-found", "stale"}:
            return False
        adapter_family = self.adapter_family
        if is_generic_video_adapter(adapter_family):
            try:
                from .video_provider_adapters import get_video_adapter_contract

                get_video_adapter_contract(adapter_family)
            except ValueError:
                return False
            return bool(
                self.catalog_confirms_model_id
                and self.catalog_confirms_video_capability
                and self.adapter_confidence >= 0.7
            )
        try:
            get_direct_video_protocol_contract(self.effective_protocol)
        except ValueError:
            return False
        from .direct_video_capability_cache import get_cached_capability_for_model

        cached = get_cached_capability_for_model(
            base_url=self.base_url,
            upstream_model=self.upstream_model,
            protocol=self.effective_protocol,
        )
        detected = str(cached.get("detectedProtocol") or "").strip().lower()
        if detected == "unresolved":
            return False
        if detected:
            try:
                get_direct_video_protocol_contract(detected)
            except ValueError:
                return False
        if self.catalog_rejects_model_id:
            return False
        if self.catalog_confirms_model_id and not self.catalog_confirms_video_capability:
            return False
        # OpenAI 兼容渠道的目录接口通常不需要鉴权，缓存里「探测过」不等于
        # 「钥匙验过」。凡是**已经有缓存记录**的渠道，缓存里必须留有一次真实
        # 凭据检测的痕迹，否则说明那是改动前写的旧账 —— 直接失效重测，不许
        # 拿着旧绿灯继续用。明确被拒绝的同样不许。
        if self.effective_protocol == DIRECT_VIDEO_PROTOCOL_OPENAI and cached:
            credential = cached.get("credentialValidation")
            if not isinstance(credential, Mapping) or not credential:
                return False
            if str(credential.get("status") or "").strip().lower() in {
                "rejected",
                "permission_denied",
            }:
                return False
        return bool(
            self.catalog_confirms_model_id or not _requires_verified_catalog()
        )

    @property
    def adapter_family(self) -> str:
        """Return the resolved non-OpenAI lifecycle family from probe evidence."""

        from .direct_video_capability_cache import get_cached_capability_for_model

        cached = get_cached_capability_for_model(
            base_url=self.base_url,
            upstream_model=self.upstream_model,
            protocol=self.effective_protocol,
        )
        family = str(cached.get("adapterFamily") or "").strip().lower()
        if self.effective_protocol == DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI:
            return DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI
        evidence = cached.get("adapterEvidence")
        sources = evidence.get("sources") if isinstance(evidence, dict) else None
        if (
            self.profile.name == "minimax-h3-v2"
            and sources == ["model-profile"]
            and family == "task-query"
        ):
            return "openai-video"
        return family

    @property
    def adapter_confidence(self) -> float:
        from .direct_video_capability_cache import get_cached_capability_for_model

        cached = get_cached_capability_for_model(
            base_url=self.base_url,
            upstream_model=self.upstream_model,
            protocol=self.effective_protocol,
        )
        try:
            return float(cached.get("adapterConfidence") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @property
    def catalog_verification(self) -> str:
        """Return the explicit catalog gate used by settings and generation."""

        from .direct_video_capability_cache import get_cached_capability_for_model

        cached = get_cached_capability_for_model(
            base_url=self.base_url,
            upstream_model=self.upstream_model,
            protocol=self.effective_protocol,
        )
        if not cached:
            return "unverified"
        discovered = int(cached.get("discoveredModelCount") or 0)
        if cached.get("modelFound") is True:
            return "runtime-verified" if (
                str(cached.get("verificationStatus") or "") == "runtime-verified"
            ) else "catalog-confirmed"
        if cached.get("modelFound") is False and discovered > 0:
            return "catalog-mismatch"
        return "unverified"

    @property
    def catalog_confirms_model_id(self) -> bool:
        return self.catalog_verification in {"catalog-confirmed", "runtime-verified"}

    @property
    def catalog_confirms_video_capability(self) -> bool:
        """Require video evidence, not only a model-name match in /models."""

        from .direct_video_capability_cache import get_cached_capability_for_model

        cached = get_cached_capability_for_model(
            base_url=self.base_url,
            upstream_model=self.upstream_model,
            protocol=self.effective_protocol,
        )
        declared = cached.get("declaredCapabilities")
        if isinstance(declared, list):
            declared_set = {str(item).strip() for item in declared}
            if "modes" in declared_set and not any(
                str(item).strip() for item in (cached.get("modes") or [])
            ):
                return False
            if "resolutionOptions" in declared_set and not any(
                str(item).strip() for item in (cached.get("resolutionOptions") or [])
            ):
                return False
        if str(cached.get("verificationStatus") or "") == "runtime-verified":
            return True
        modes = cached.get("modes")
        if isinstance(modes, list) and any(str(item).strip() for item in modes):
            return True
        protocols = cached.get("supportedProtocols")
        if isinstance(protocols, list) and any(str(item).strip() for item in protocols):
            return True
        transport = cached.get("transportContract")
        if isinstance(transport, dict) and bool(transport):
            return True

        # OpenAPI path evidence is a capability contract even when the model
        # directory only exposes bare IDs and no modality metadata.
        if is_generic_video_adapter(self.adapter_family):
            return self.adapter_confidence >= 0.7

        # A known built-in family is already an executable operator contract.
        # Some relays (notably huabu ``sd-2.0-fast-v1``) return only the model
        # ID from /models, so requiring capability tags would leave a valid
        # model permanently unmapped.  Unknown generic IDs remain gated until
        # the provider publishes capability metadata or a richer contract is
        # supplied.
        return self.profile.name != "openai-video-generic"

    @property
    def catalog_rejects_model_id(self) -> bool:
        """Return whether a non-empty upstream catalog excludes this ID."""
        return self.catalog_verification == "catalog-mismatch"

    @property
    def capability(self) -> ModelCapability:
        profile = self.profile
        # Provider-specific resolution labels (AutoDL uses ``480p竖`` and
        # ``768p横``) belong to the transport/UI contract. ModelCapability is
        # shared by all video nodes and intentionally stores canonical height
        # values, so strip only the orientation suffix at this boundary.
        capability_resolutions = _canonical_capability_resolutions(profile.resolution)
        return ModelCapability(
            model_id=f"direct/{self.registry_id}",
            provider="direct",
            model_vendor="operator",
            adapter="openai-video",
            upstream_model=self.upstream_model,
            aliases=(),
            modes=profile.modes,
            duration=profile.duration,
            resolution=capability_resolutions,
            aspect=profile.aspect,
            native_audio=profile.native_audio,
            reference_limits=profile.reference_limits,
            return_last_frame=profile.return_last_frame,
            prompt_profile="openai-video",
            enabled=self.enabled,
            lifecycle=Lifecycle.ACTIVE,
            catalog_revision=DIRECT_VIDEO_CATALOG_REVISION,
            model_revision="operator-configured",
            pricing_revision="operator-configured",
            data_policy_revision="operator-configured",
            fallback_policy=FallbackPolicy.FORBIDDEN,
            last_verified=date.today(),
            supports_custom_aspect_ratio=profile.supports_custom_aspect_ratio,
            supports_custom_resolution=profile.supports_custom_resolution,
        )

    @property
    def family(self) -> str:
        """Return the canvas capability family from the exact upstream model ID."""
        return self.profile.family

    @property
    def profile(self) -> DirectVideoCapabilityProfile:
        return resolve_direct_video_profile(
            self.upstream_model,
            base_url=self.base_url,
            protocol=self.effective_protocol,
            # A relay may write the capability contract into the readable name
            # while the upstream ID stays opaque (``minimax_h3_zm_u24`` vs
            # ``H3多图多音频生视频15秒``), so both are offered to the resolver.
            display_name=self.label,
        )

    @property
    def effective_protocol(self) -> str:
        """Resolve transport from operator choice or endpoint evidence, never model name."""
        try:
            requested = normalize_direct_video_protocol(self.requested_protocol)
        except ValueError:
            requested = DIRECT_VIDEO_PROTOCOL_AUTO
        if requested != DIRECT_VIDEO_PROTOCOL_AUTO:
            return requested

        cached = get_cached_capability_for_model(
            base_url=self.base_url,
            upstream_model=self.upstream_model,
        )
        detected = str(cached.get("detectedProtocol") or "").strip()
        supported = cached.get("supportedProtocols")
        if detected:
            try:
                normalized = normalize_direct_video_protocol(detected)
            except ValueError:
                normalized = "unresolved"
            if normalized == DIRECT_VIDEO_PROTOCOL_OPENAI or (
                normalized != DIRECT_VIDEO_PROTOCOL_AUTO
                and normalized != "unresolved"
                and isinstance(supported, list)
                and bool(supported)
            ):
                return normalized
        try:
            configured = normalize_direct_video_protocol(self.protocol)
        except ValueError:
            configured = DIRECT_VIDEO_PROTOCOL_OPENAI
        if configured != DIRECT_VIDEO_PROTOCOL_AUTO:
            return configured
        return DIRECT_VIDEO_PROTOCOL_OPENAI

    def generator_options(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """Compile one collision-free generator constructor argument mapping.

        The direct-model registry owns credentials, endpoint, model identity,
        protocol path, and capability-derived defaults.  Runtime callers own
        request-scoped values only.  Keeping this boundary in one function
        prevents Python ``multiple values for keyword argument`` errors and
        stops stale canvas values from bypassing the declared capability.
        """
        options = dict(values)
        requested_resolution = options.pop("resolution", None)
        requested_generate_audio = options.pop("generate_audio", None)
        for key in (
            "api_key",
            "endpoint",
            "model",
            "create_path",
            "protocol",
            "query_path_template",
            "cache_runtime_contract",
            "preserve_upstream_model",
            "allow_result_gateway_fallback",
            "allowed_durations",
            "allowed_aspect_ratios",
        ):
            options.pop(key, None)

        profile = self.profile
        adapter_family = self.adapter_family
        cached = get_cached_capability_for_model(
            base_url=self.base_url,
            upstream_model=self.upstream_model,
            protocol=self.effective_protocol,
        )
        raw_parameter_values = options.pop("parameters", None)
        parameter_values = (
            dict(raw_parameter_values)
            if isinstance(raw_parameter_values, Mapping)
            else {}
        )
        requested_provider_mapping = options.pop("provider_mapping", None)
        if not isinstance(requested_provider_mapping, Mapping):
            requested_provider_mapping = options.pop("providerMapping", None)
        cached_parameter_mapping = cached.get("providerMapping")
        if not isinstance(cached_parameter_mapping, Mapping):
            cached_parameter_mapping = cached.get("mapping")
        provider_mapping = (
            dict(requested_provider_mapping)
            if isinstance(requested_provider_mapping, Mapping) and requested_provider_mapping
            else dict(cached_parameter_mapping)
            if isinstance(cached_parameter_mapping, Mapping)
            else {}
        )
        aspect_parameter_enabled = _cached_parameter_support(
            cached,
            declared_key="aspectRatios",
            values_key="aspectRatios",
            custom_key="supportsCustomAspectRatio",
        )
        resolution_parameter_enabled = _cached_parameter_support(
            cached,
            declared_key="resolutionOptions",
            values_key="resolutionOptions",
            custom_key="supportsCustomResolution",
        )
        duration_parameter_enabled = _cached_parameter_support(
            cached,
            declared_key="durationOptions",
            values_key="durationOptions",
            custom_key="supportsCustomDuration",
        )
        cached_duration_options = cached.get("durationOptions")
        if cached_duration_options is None:
            cached_duration_options = cached.get("duration_options")
        allowed_durations = profile.duration
        if duration_parameter_enabled is not None:
            normalized_durations: set[int] = set()
            for value in cached_duration_options or ():
                try:
                    parsed = int(round(float(value)))
                except (TypeError, ValueError):
                    continue
                if 1 <= parsed <= 300:
                    normalized_durations.add(parsed)
            allowed_durations = tuple(sorted(normalized_durations))
        if is_generic_video_adapter(adapter_family):
            resolution_values = profile.resolution
            resolved_resolution = profile.resolve_resolution(requested_resolution)
            if adapter_family == DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI:
                # Accept both the provider labels exposed by the model picker
                # and the standard ``480p`` values used by older canvas nodes.
                resolution_values = tuple(
                    dict.fromkeys(
                        (*profile.resolution, *_canonical_capability_resolutions(profile.resolution))
                    )
                )
                if requested_resolution is not None and str(requested_resolution).strip():
                    # Let the AutoDL payload compiler choose 横/竖 for a plain
                    # height value from the requested aspect ratio.
                    resolved_resolution = str(requested_resolution).strip()
            return {
                **options,
                "api_key": self.api_key,
                "endpoint": self.base_url,
                "model": self.upstream_model,
                "adapter_family": adapter_family,
                "adapter_operations": cached.get("openapiOperations") or (),
                "allowed_durations": allowed_durations,
                "duration_parameter_enabled": duration_parameter_enabled,
                "allowed_resolutions": (
                    resolution_values
                    if resolution_parameter_enabled is not False
                    else ()
                ),
                "allowed_aspect_ratios": (
                    ()
                    if profile.size_slots
                    else profile.aspect
                    if aspect_parameter_enabled is not False
                    else ()
                ),
                "size_slots": profile.size_slots,
                "size_field": profile.size_field or "size",
                "aspect_ratio_parameter_enabled": aspect_parameter_enabled,
                "resolution_parameter_enabled": resolution_parameter_enabled,
                "supports_custom_aspect_ratio": profile.supports_custom_aspect_ratio,
                "supports_custom_resolution": profile.supports_custom_resolution,
                "supported_modes": tuple(item.value for item in profile.modes),
                "reference_limits": profile.reference_limits.to_dict(),
                "native_audio": profile.native_audio.value,
                "resolution": resolved_resolution,
                "generate_audio": profile.resolve_generate_audio(requested_generate_audio),
                "parameter_values": parameter_values,
                "provider_mapping": provider_mapping,
                "capability_parameters": cached.get("parameters")
                if isinstance(cached.get("parameters"), list)
                else [],
                "capability_opaque": cached.get("opaque")
                if isinstance(cached.get("opaque"), list)
                else [],
                "workflow_input_rules": _cached_capability_records(
                    cached,
                    "workflowInputRules",
                    "workflow_input_rules",
                ),
                "media_inputs": _cached_capability_records(
                    cached,
                    "mediaInputs",
                    "media_inputs",
                ),
                "resolution_mappings": _cached_capability_records(
                    cached,
                    "resolutionMappings",
                    "resolution_mappings",
                ),
                "audio_input_semantics": (
                    cached.get("audioInputSemantics")
                    if isinstance(cached.get("audioInputSemantics"), list)
                    else cached.get("audio_input_semantics")
                    if isinstance(cached.get("audio_input_semantics"), list)
                    else []
                ),
                "cache_runtime_contract": True,
                "preserve_upstream_model": True,
            }
        # Keep the legacy constructor compact while carrying the discovered
        # provider fields to NewApiVideoGenerator. Generic adapters consume
        # the same values in the branch above.
        options.pop("parameters", None)
        contract = get_direct_video_protocol_contract(self.effective_protocol)
        wire_contract = self.resolved_wire_contract
        return {
            **options,
            "api_key": self.api_key,
            "endpoint": self.base_url,
            "model": self.upstream_model,
            # 渠道自带的出线合同（权威）或名字种子（未验证）显式交给生成器；
            # 生成器不再自己按名字猜，渠道删掉合同就没了。
            "upstream_profile": wire_contract.profile,
            "resolution": profile.resolve_resolution(requested_resolution),
            "generate_audio": profile.resolve_generate_audio(requested_generate_audio),
            "protocol": self.effective_protocol,
            "create_path": str(
                cached.get("openapiSubmitPath")
                or _declared_wire_create_path(wire_contract, self.base_url)
                or contract.submit_path
            ),
            "query_path_template": str(
                cached.get("openapiQueryPath") or contract.query_path_template
            ),
            "cache_runtime_contract": True,
            "preserve_upstream_model": True,
            "allow_result_gateway_fallback": False,
            "allowed_durations": allowed_durations,
            "duration_parameter_enabled": duration_parameter_enabled,
            # Pixel size slots and aspect-ratio choices are separate upstream
            # fields. Never feed WxH slots into the ratio validator: doing so
            # makes a model with an explicit size contract lose its real
            # orientation options before payload compilation.
            "allowed_aspect_ratios": (
                profile.aspect
                if aspect_parameter_enabled is not False
                else ()
            ),
            "aspect_ratio_parameter_enabled": aspect_parameter_enabled,
            "resolution_parameter_enabled": resolution_parameter_enabled,
            "supports_custom_aspect_ratio": profile.supports_custom_aspect_ratio,
            "supports_custom_resolution": profile.supports_custom_resolution,
            "parameter_values": parameter_values,
            "provider_mapping": provider_mapping,
        }


def list_direct_video_models() -> tuple[DirectVideoModel, ...]:
    """Read valid direct models in the user's saved order."""
    return tuple(
        DirectVideoModel(
            registry_id=str(item["id"]),
            label=str(item["label"]),
            upstream_model=str(item["modelId"]),
            base_url=str(item["baseUrl"]),
            api_key=str(item["apiKey"]),
            enabled=bool(item["enabled"]),
            requested_protocol=str(item.get("requestedProtocol") or "auto"),
            protocol=str(item.get("protocol") or "openai-video"),
            is_default=bool(item.get("isDefault", False)),
            wire_contract=(
                dict(item["wireContract"])
                if isinstance(item.get("wireContract"), Mapping)
                else None
            ),
            wire_contract_error=str(item.get("wireContractError") or ""),
        )
        for item in get_direct_video_models()
    )


def is_direct_video_backend(backend: str | None) -> bool:
    return str(backend or "").strip().lower().startswith(DIRECT_VIDEO_BACKEND_PREFIX)


def resolve_direct_video_model(backend: str | None) -> DirectVideoModel | None:
    value = str(backend or "").strip().lower()
    if not value.startswith(DIRECT_VIDEO_BACKEND_PREFIX):
        return None
    registry_id = value[len(DIRECT_VIDEO_BACKEND_PREFIX) :]
    models = list_direct_video_models()
    if registry_id == "default":
        return next(
            (item for item in models if item.enabled and item.is_default),
            None,
        ) or next((item for item in models if item.enabled), None)
    return next((item for item in models if item.registry_id == registry_id), None)


def _direct_video_capability_revision(material: Mapping[str, Any]) -> str:
    """Return a stable revision for the exact capability facts shown to users."""

    encoded = json.dumps(
        material,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "video-cap." + hashlib.sha256(encoded).hexdigest()[:16]


def direct_video_model_option(model: DirectVideoModel) -> dict[str, Any]:
    """Return the same public model shape consumed by the video node picker."""
    wire_contract = model.resolved_wire_contract
    capability = model.capability
    supported_modes = _canvas_modes(capability, model.profile.exact_canvas_modes)
    reference_limits = _canvas_reference_limits(capability, supported_modes)
    detected = _detection_label(capability)
    enabled = model.enabled and model.runtime_ready
    disabled_reason = _direct_video_disabled_reason(model)
    parameter_defaults = model.profile.parameter_defaults()
    cached = get_cached_capability_for_model(
        base_url=model.base_url,
        upstream_model=model.upstream_model,
        protocol=model.effective_protocol,
    )
    latest_observation = get_cached_capability_for_model(
        base_url=model.base_url,
        upstream_model=model.upstream_model,
    )
    declared_capabilities = cached.get("declaredCapabilities")
    declared_set = (
        {str(item).strip() for item in declared_capabilities}
        if isinstance(declared_capabilities, list)
        else set()
    )
    size_slots_declared = "sizeSlots" in declared_set or "sizeSlots" in cached
    aspect_parameter_enabled = _cached_parameter_support(
        cached,
        declared_key="aspectRatios",
        values_key="aspectRatios",
        custom_key="supportsCustomAspectRatio",
    )
    resolution_parameter_enabled = _cached_parameter_support(
        cached,
        declared_key="resolutionOptions",
        values_key="resolutionOptions",
        custom_key="supportsCustomResolution",
    )
    resolution_declared = (
        "resolutionOptions" in declared_set or "resolutionOptions" in cached
    )
    advertised_resolutions = cached.get("advertisedResolutionOptions")
    if not isinstance(advertised_resolutions, list):
        cached_resolutions = cached.get("resolutionOptions")
        advertised_resolutions = (
            list(cached_resolutions)
            if isinstance(cached_resolutions, list)
            else []
            if resolution_declared
            else list(capability.resolution)
        )
    runtime_resolutions = cached.get("runtimeResolutionOptions")
    if not isinstance(runtime_resolutions, list):
        cached_resolutions = cached.get("resolutionOptions")
        runtime_resolutions = (
            list(cached_resolutions)
            if isinstance(cached_resolutions, list)
            else []
            if resolution_declared
            else list(capability.resolution)
        )
    # Keep explicit empty upstream enums visible as empty, rather than filling
    # them from a static family profile.  The model is disabled by the same
    # contract gate, so stale node state cannot submit a guessed parameter.
    if "modes" in declared_set and not cached.get("modes"):
        supported_modes = []
        reference_limits = {}
    runtime_rejected = cached.get("runtimeRejectedResolutionOptions")
    if not isinstance(runtime_rejected, list):
        runtime_rejected = []
    if runtime_rejected:
        runtime_resolutions = [
            item for item in runtime_resolutions if item not in runtime_rejected
        ]
    if "resolutionOptions" in declared_set and not runtime_resolutions:
        parameter_defaults = {
            **parameter_defaults,
            "resolution": "",
        }
    cached_aspects = cached.get("aspectRatios")
    aspect_declared = "aspectRatios" in declared_set or "aspectRatios" in cached
    if aspect_declared:
        aspect_options = [
            str(item).strip()
            for item in (cached_aspects if isinstance(cached_aspects, list) else [])
            if str(item).strip()
        ]
    else:
        aspect_options = list(capability.aspect)
    # 比例来自哪里要在节点目录里说清楚：catalog=上游直接写了比例；
    # catalog-size-slots=上游只给了精确尺寸，比例由尺寸推出；profile 或
    # protocol-contract=本次上游没说，这份比例是本地合同补的，不能冒充上游声明。
    aspect_ratio_source = str(cached.get("aspectRatioSource") or "").strip().lower()
    if not aspect_ratio_source:
        aspect_ratio_source = (
            "catalog"
            if "aspectRatios" in declared_set
            else "profile"
            if aspect_options
            else ""
        )
    resolution_source = str(cached.get("resolutionSource") or "").strip().lower()
    if not resolution_source:
        resolution_source = (
            "catalog"
            if "resolutionOptions" in declared_set
            else "profile"
            if runtime_resolutions
            else ""
        )
    if latest_observation.get("probeStatus") in {
        "failed",
        "model-not-found",
        "stale",
    }:
        supported_modes = []
        reference_limits = {}
        advertised_resolutions = []
        runtime_resolutions = []
        aspect_options = []
        aspect_ratio_source = ""
        resolution_source = ""
        parameter_defaults = {
            **parameter_defaults,
            "resolution": "",
            "aspectRatio": "",
        }
    runtime_status = str(cached.get("runtimeCapabilityStatus") or "verified").strip()
    runtime_note = str(cached.get("runtimeCapabilityNote") or "").strip()
    verification_stage = str(cached.get("verificationStage") or "").strip().lower()
    if verification_stage not in {"catalog", "contract", "submit", "poll", "artifact"}:
        verification_stage = (
            "artifact"
            if str(cached.get("verificationStatus") or "") == "runtime-verified"
            else "contract"
            if str(cached.get("verificationStatus") or "") == "contract-resolved"
            else "catalog"
            if str(cached.get("verificationStatus") or "") in {"metadata", "catalog-confirmed"}
            else "unknown"
        )
    if parameter_defaults.get("resolution") not in runtime_resolutions and runtime_resolutions:
        parameter_defaults = {
            **parameter_defaults,
            "resolution": str(runtime_resolutions[0]),
        }
    if "aspectRatios" in declared_set and not aspect_options:
        parameter_defaults = {
            **parameter_defaults,
            "aspectRatio": "",
        }
    elif aspect_options and parameter_defaults.get("aspectRatio") not in aspect_options:
        parameter_defaults = {
            **parameter_defaults,
            "aspectRatio": aspect_options[0],
        }
    duration_declared = "durationOptions" in declared_set or any(
        key in cached for key in ("durationOptions", "duration_options")
    )
    raw_duration_options = cached.get("durationOptions")
    if raw_duration_options is None:
        raw_duration_options = cached.get("duration_options")
    duration_options: list[int] = []
    if isinstance(raw_duration_options, (list, tuple)):
        for value in raw_duration_options:
            try:
                parsed = int(round(float(value)))
            except (TypeError, ValueError):
                continue
            if 1 <= parsed <= 300 and parsed not in duration_options:
                duration_options.append(parsed)
    duration_options.sort()
    if not duration_declared:
        # 渠道合同档案也是真值来源：上游档案（渠道文档）声明了精确档位时，把
        # 生效档位原样上桌。只给 min/max 会让节点渲染成连续滑块，让用户以为
        # 区间里每个整数秒都能用（2026-09-12「只能真，不能骗人」）。缓存里实测
        # 到的申报优先于档案；只有缓存沉默时才用档案兜底。
        # 渠道条目自带的合同优先；没有才回落到源码种子（未验证），再落到通用兜底。
        # 只有通用兜底或种子时，档位是「按名字猜的」，节点面板会标出来。
        wire_contract = model.resolved_wire_contract
        upstream_profile = wire_contract.profile
        upstream_choices = {
            int(value)
            for value in (upstream_profile.duration_choices or ())
            if int(value) > 0
        }
        if upstream_choices:
            declared_tiers = sorted(
                {
                    int(value)
                    for value in capability.duration
                    if int(value) in upstream_choices
                }
            ) or sorted(upstream_choices)
            duration_options = declared_tiers
            duration_declared = True
    fps_declared = "fpsOptions" in declared_set or any(
        key in cached for key in ("fpsOptions", "fps_options")
    )
    raw_fps_options = cached.get("fpsOptions")
    if raw_fps_options is None:
        raw_fps_options = cached.get("fps_options")
    fps_options: list[int | float] = []
    if isinstance(raw_fps_options, (list, tuple)):
        for value in raw_fps_options:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if not 1 <= parsed <= 240:
                continue
            normalized: int | float = int(parsed) if parsed.is_integer() else round(parsed, 3)
            if normalized not in fps_options:
                fps_options.append(normalized)
    fps_options.sort(key=float)
    input_slots_declared = "inputSlots" in declared_set or any(
        key in cached for key in ("inputSlots", "input_slots")
    )
    raw_input_slots = cached.get("inputSlots")
    if raw_input_slots is None:
        raw_input_slots = cached.get("input_slots")
    input_slots = (
        [str(value).strip() for value in raw_input_slots if str(value).strip()]
        if isinstance(raw_input_slots, (list, tuple))
        else []
    )
    return_last_frame = cached.get("returnLastFrame")
    if not isinstance(return_last_frame, bool):
        return_last_frame = cached.get("return_last_frame")
    if not isinstance(return_last_frame, bool):
        return_last_frame = model.profile.return_last_frame
    supports_custom_duration = cached.get("supportsCustomDuration")
    if not isinstance(supports_custom_duration, bool):
        supports_custom_duration = cached.get("supports_custom_duration")
    if not isinstance(supports_custom_duration, bool):
        supports_custom_duration = False
    duration_parameter_enabled = _cached_parameter_support(
        cached,
        declared_key="durationOptions",
        values_key="durationOptions",
        custom_key="supportsCustomDuration",
    )
    if duration_parameter_enabled is None and duration_declared:
        duration_parameter_enabled = bool(duration_options) or bool(supports_custom_duration)
    envelope_parameters = cached.get("parameters")
    if not isinstance(envelope_parameters, list):
        envelope_parameters = []
    envelope_mapping = cached.get("providerMapping")
    if not isinstance(envelope_mapping, Mapping):
        envelope_mapping = cached.get("mapping")
    if not isinstance(envelope_mapping, Mapping):
        envelope_mapping = {}
    envelope_opaque = cached.get("opaque")
    if not isinstance(envelope_opaque, list):
        envelope_opaque = []
    envelope_media_inputs = cached.get("mediaInputs")
    if not isinstance(envelope_media_inputs, list):
        envelope_media_inputs = cached.get("media_inputs")
    if not isinstance(envelope_media_inputs, list):
        envelope_media_inputs = []
    audio_input_semantics = cached.get("audioInputSemantics")
    if not isinstance(audio_input_semantics, list):
        audio_input_semantics = cached.get("audio_input_semantics")
    if not isinstance(audio_input_semantics, list):
        audio_input_semantics = []
    # A discovered audio reference slot is a transport-level declaration. In
    # the absence of a richer semantic label, use the conservative complete
    # Beat track (driving_audio), never a voice-cloning interpretation.
    if not audio_input_semantics and capability.reference_limits.reference_audios > 0:
        audio_input_semantics = ["driving_audio"]
    workflow_input_rules = _cached_capability_records(
        cached,
        "workflowInputRules",
        "workflow_input_rules",
    )
    resolution_mappings = _cached_capability_records(
        cached,
        "resolutionMappings",
        "resolution_mappings",
    )
    # Preserve explicit fixed-size metadata, except when this cache entry is
    # only the fallback model-name contract; those dimensions were not part
    # of the resolved model profile.
    cached_size_slots = cached.get("sizeSlots")
    if cached.get("source") == "models-profile-contract":
        size_options = list(model.profile.size_slots)
        size_slots_declared = True
    else:
        size_options = (
            [str(item).strip() for item in cached_size_slots if str(item).strip()]
            if isinstance(cached_size_slots, list)
            else []
        )
    size_field = str(cached.get("sizeField") or "").strip() or model.profile.size_field or "size"
    prompt_rules = cached.get("promptRules")
    if not isinstance(prompt_rules, dict):
        prompt_rules = {
            "durationConsistency": model.profile.prompt_duration_consistency,
            "referenceConsistency": model.profile.prompt_reference_consistency,
        }
    try:
        failure_grace_polls = max(
            0,
            min(120, int(cached.get("failureGracePolls") or model.profile.failure_grace_polls)),
        )
    except (TypeError, ValueError):
        failure_grace_polls = model.profile.failure_grace_polls
    raw_capability_source = str(cached.get("source") or "").strip().lower()
    capability_source = raw_capability_source or (
        "upstream" if cached else "profile"
    )
    minimum_duration = (
        None
        if duration_declared and not duration_options
        else min(capability.duration)
    )
    maximum_duration = (
        None
        if duration_declared and not duration_options
        else max(capability.duration)
    )
    capability_revision = _direct_video_capability_revision(
        {
            "upstreamModel": model.upstream_model,
            "protocol": model.effective_protocol,
            "source": capability_source,
            "verificationStage": verification_stage,
            "runtimeCapabilityStatus": runtime_status,
            "supportedModes": supported_modes,
            "referenceLimits": reference_limits,
            "parameterDefaults": parameter_defaults,
            "aspectRatioOptions": aspect_options,
            "aspectRatioSource": aspect_ratio_source,
            "advertisedResolutionOptions": advertised_resolutions,
            "resolutionSource": resolution_source,
            "runtimeResolutionOptions": runtime_resolutions,
            "runtimeRejectedResolutionOptions": runtime_rejected,
            "supportsCustomAspectRatio": model.profile.supports_custom_aspect_ratio,
            "supportsCustomResolution": model.profile.supports_custom_resolution,
            "sizeOptions": (
                size_options if size_slots_declared else list(model.profile.size_slots)
            ),
            "sizeField": size_field,
            "minDuration": minimum_duration,
            "maxDuration": maximum_duration,
            "durationOptions": duration_options,
            "fpsOptions": fps_options,
            "inputSlots": input_slots,
            "returnLastFrame": return_last_frame,
            "supportsCustomDuration": supports_custom_duration,
            "durationParameterEnabled": duration_parameter_enabled,
            "nativeAudio": capability.native_audio.value,
            "declaredCapabilities": sorted(declared_set),
            "audioInputSemantics": audio_input_semantics,
        }
    )
    return {
        "id": model.backend,
        "providerId": "direct",
        "provider": "direct",
        "apiModel": model.upstream_model,
        "api_model": model.upstream_model,
        "capabilityRevision": capability_revision,
        "capability_revision": capability_revision,
        "capabilitySource": capability_source,
        "capability_source": capability_source,
        # 出线合同从哪来：渠道自带（随渠道增删）／源码种子（未验证）／通用兜底。
        # 节点面板据此显示来源，用户一眼能看出「这份合同是靠得住还是猜的」。
        **wire_contract.to_status(),
        "wireContractError": model.wire_contract_error,
        "wire_contract_error": model.wire_contract_error,
        "label": model.label if enabled else f"{model.label}（已停用）",
        "backend": model.backend,
        "resolutionOptions": list(runtime_resolutions),
        "resolution_options": list(runtime_resolutions),
        "advertisedResolutionOptions": list(advertised_resolutions),
        "advertised_resolution_options": list(advertised_resolutions),
        "runtimeResolutionOptions": list(runtime_resolutions),
        "runtime_resolution_options": list(runtime_resolutions),
        "runtimeRejectedResolutionOptions": list(runtime_rejected),
        "runtime_rejected_resolution_options": list(runtime_rejected),
        # Keep field presence and the resulting transport gate available to
        # WorkflowRun snapshots. An explicit empty enum must not be
        # reconstructed from the family profile later in the pipeline.
        "declaredCapabilities": sorted(declared_set),
        "declared_capabilities": sorted(declared_set),
        "aspectRatioParameterEnabled": aspect_parameter_enabled,
        "aspect_ratio_parameter_enabled": aspect_parameter_enabled,
        "resolutionParameterEnabled": resolution_parameter_enabled,
        "resolution_parameter_enabled": resolution_parameter_enabled,
        "runtimeCapabilityStatus": runtime_status,
        "runtime_capability_status": runtime_status,
        "runtimeCapabilityNote": runtime_note,
        "runtime_capability_note": runtime_note,
        "verificationStage": verification_stage,
        "verification_stage": verification_stage,
        "promptRules": prompt_rules,
        "prompt_rules": prompt_rules,
        "failureGracePolls": failure_grace_polls,
        "failure_grace_polls": failure_grace_polls,
        "sizeOptions": (
            size_options if size_slots_declared else list(model.profile.size_slots)
        ),
        "size_options": (
            size_options if size_slots_declared else list(model.profile.size_slots)
        ),
        "sizeField": size_field,
        "size_field": size_field,
        "aspectRatioOptions": aspect_options,
        "aspect_ratio_options": aspect_options,
        "aspectRatioSource": aspect_ratio_source,
        "aspect_ratio_source": aspect_ratio_source,
        "resolutionSource": resolution_source,
        "resolution_source": resolution_source,
        "supportsCustomAspectRatio": model.profile.supports_custom_aspect_ratio,
        "supports_custom_aspect_ratio": model.profile.supports_custom_aspect_ratio,
        "supportsCustomResolution": model.profile.supports_custom_resolution,
        "supports_custom_resolution": model.profile.supports_custom_resolution,
        "nativeAudio": capability.native_audio.value,
        "native_audio": capability.native_audio.value,
        "minDuration": minimum_duration,
        "min_duration": minimum_duration,
        "maxDuration": maximum_duration,
        "max_duration": maximum_duration,
        **(
            {
                "durationOptions": list(duration_options),
                "duration_options": list(duration_options),
            }
            if duration_declared
            else {}
        ),
        **(
            {"fpsOptions": list(fps_options), "fps_options": list(fps_options)}
            if fps_declared
            else {}
        ),
        **(
            {"inputSlots": list(input_slots), "input_slots": list(input_slots)}
            if input_slots_declared
            else {}
        ),
        "returnLastFrame": bool(return_last_frame),
        "return_last_frame": bool(return_last_frame),
        "supportsCustomDuration": bool(supports_custom_duration),
        "supports_custom_duration": bool(supports_custom_duration),
        "durationParameterEnabled": duration_parameter_enabled,
        "duration_parameter_enabled": duration_parameter_enabled,
        "enabled": enabled,
        "disabled": not enabled,
        "disabledReason": disabled_reason,
        "disabled_reason": disabled_reason,
        "channelEnabled": enabled,
        "channel_enabled": enabled,
        "runtimeReady": model.runtime_ready,
        "catalogVerification": model.catalog_verification,
        "family": model.family,
        "supportedModes": supported_modes,
        "supported_modes": supported_modes,
        "referenceLimits": reference_limits,
        "reference_limits": reference_limits,
        "parameterDefaults": parameter_defaults,
        "parameter_defaults": parameter_defaults,
        "capabilityEnvelopeVersion": cached.get("capabilityEnvelopeVersion", 1),
        "capability_envelope_version": cached.get("capabilityEnvelopeVersion", 1),
        "parameters": envelope_parameters,
        "providerMapping": dict(envelope_mapping),
        "provider_mapping": dict(envelope_mapping),
        "mapping": dict(envelope_mapping),
        "mediaInputs": envelope_media_inputs,
        "media_inputs": envelope_media_inputs,
        "audioInputSemantics": list(audio_input_semantics),
        "audio_input_semantics": list(audio_input_semantics),
        "opaque": envelope_opaque,
        "workflowId": str(cached.get("workflowId") or "").strip(),
        "workflowName": str(cached.get("workflowName") or "").strip(),
        "workflowInputRules": workflow_input_rules,
        "resolutionMappings": resolution_mappings,
        "workflowDiscovery": cached.get("workflowDiscovery")
        if isinstance(cached.get("workflowDiscovery"), Mapping)
        else {},
        "supportsHumanReview": False,
        "channel": "直连视频 API",
        "channelLabel": "直连视频 API",
        "channel_label": "直连视频 API",
        "requestedProtocol": model.requested_protocol,
        "protocol": model.effective_protocol,
        "isDefault": model.is_default,
        "useCase": detected,
        "use_case": detected,
        "priceHint": "按直连上游计费",
        "price_hint": "按直连上游计费",
        "recommendation": "自动按该模型的能力合同匹配节点参数",
        "adapterFamily": _cached_adapter_field(model, "adapterFamily"),
        "adapterConfidence": _cached_adapter_field(model, "adapterConfidence"),
        "adapterEvidence": _cached_adapter_field(model, "adapterEvidence"),
        "sortRank": -100,
        "sort_rank": -100,
    }


def _cached_parameter_support(
    cached: Mapping[str, Any],
    *,
    declared_key: str,
    values_key: str,
    custom_key: str,
) -> bool | None:
    """Translate an explicit upstream enum into a transport parameter gate.

    ``None`` means the upstream did not describe this parameter and the
    built-in profile remains authoritative.  An explicitly empty enum is
    different: it means the parameter must be omitted instead of being
    reconstructed from a family preset.
    """
    declared = cached.get("declaredCapabilities")
    if not isinstance(declared, list) or declared_key not in {
        str(item).strip() for item in declared
    }:
        return None
    values = cached.get(values_key)
    if isinstance(values, list) and any(str(item).strip() for item in values):
        return True
    if cached.get(custom_key) is True:
        return True
    return False


def _canvas_modes(
    capability: ModelCapability, exact_canvas_modes: tuple[str, ...] = ()
) -> list[str]:
    if exact_canvas_modes:
        return list(exact_canvas_modes)
    if capability.modes == (VideoMode.REFERENCE_TO_VIDEO,):
        # A source-video-only model such as Kling V3 Omni must not expose the
        # generic all-reference tab: that tab has different payload semantics
        # and would let the canvas disable the real video-edit submit path.
        return ["videoEdit"]
    mapped = {
        VideoMode.TEXT_TO_VIDEO: "textToVideo",
        VideoMode.IMAGE_TO_VIDEO: "imageToVideo",
        VideoMode.FIRST_LAST_FRAME: "firstLastFrame",
        VideoMode.REFERENCE_TO_VIDEO: "allReference",
    }
    modes = [mapped[mode] for mode in capability.modes]
    if (
        VideoMode.REFERENCE_TO_VIDEO in capability.modes
        and capability.reference_limits.reference_videos
    ):
        modes.append("videoEdit")
    return modes


def _direct_video_model_family(upstream_model: str) -> str:
    """Classify stable model contracts without coupling them to a gateway."""
    return resolve_direct_video_profile(upstream_model).family


def _cached_adapter_field(model: DirectVideoModel, key: str) -> object:
    from .direct_video_capability_cache import get_cached_capability_for_model

    cached = get_cached_capability_for_model(
        base_url=model.base_url,
        upstream_model=model.upstream_model,
        protocol=model.effective_protocol,
    )
    value = cached.get(key)
    evidence = cached.get("adapterEvidence")
    sources = evidence.get("sources") if isinstance(evidence, dict) else None
    # Older probes classified ``minimax_h3`` as task-query from the model name
    # alone.  The current relay documents H3 on ``/v1/videos`` with the
    # unified ``video.v1`` JSON body, so that weak hint must not contradict the
    # executable OpenAI-video transport selected from endpoint evidence.
    if (
        model.profile.name == "minimax-h3-v2"
        and sources == ["model-profile"]
        and (
            value == "task-query"
            or (key == "adapterEvidence" and isinstance(evidence, dict))
        )
    ):
        if key == "adapterFamily":
            return "openai-video"
        if key == "adapterEvidence" and isinstance(evidence, dict):
            return {
                **evidence,
                "family": "openai-video",
                "resolved": True,
                "notes": [
                    *list(evidence.get("notes") or ()),
                    "弱模型名推断已由 /v1/videos 运行合同覆盖",
                ],
            }
    return value


def _canvas_reference_limits(
    capability: ModelCapability, supported_modes: list[str]
) -> dict[str, dict[str, int]]:
    limits = capability.reference_limits
    result: dict[str, dict[str, int]] = {}
    if "allReference" in supported_modes:
        result["allReference"] = {
            "image": limits.reference_images,
            "video": limits.reference_videos,
            "audio": limits.reference_audios,
        }
    if "imageReference" in supported_modes:
        result["imageReference"] = {
            "image": limits.reference_images,
            "video": 0,
            "audio": 0,
        }
    if "imageToVideo" in supported_modes:
        result["imageToVideo"] = {"image": limits.input_images, "video": 0, "audio": 0}
    if "firstLastFrame" in supported_modes:
        result["firstLastFrame"] = {
            "image": limits.input_images,
            "video": 0,
            "audio": 0,
        }
    if "videoEdit" in supported_modes:
        result["videoEdit"] = {
            "image": limits.reference_images,
            "video": limits.reference_videos,
            "audio": limits.reference_audios,
        }
    return result


def _detection_label(capability: ModelCapability) -> str:
    if capability.modes == (VideoMode.TEXT_TO_VIDEO,):
        return "已识别：文生视频"
    if capability.modes == (VideoMode.REFERENCE_TO_VIDEO,):
        return "已识别：源视频重绘"
    if VideoMode.REFERENCE_TO_VIDEO in capability.modes:
        return "已识别：文生、图生与多参考视频"
    return "已识别：通用文生与图生视频"


def _direct_video_disabled_reason(model: DirectVideoModel) -> str:
    if not model.enabled:
        return "模型已在直连视频模型管理中停用"
    if model.catalog_rejects_model_id:
        return "上游模型目录未找到该模型 ID，请核对模型 ID 后重新检测"
    if not model.runtime_ready:
        from .direct_video_capability_cache import get_cached_capability_for_model

        latest = get_cached_capability_for_model(
            base_url=model.base_url,
            upstream_model=model.upstream_model,
        )
        if latest.get("probeStatus") == "stale":
            return "渠道凭据或协议已变化，请重新检测连接"
        if latest.get("probeStatus") == "failed":
            detail = str(latest.get("lastFailure") or "上游暂时无法确认连接").strip()
            return f"实时连接检测失败：{detail}"
    if not model.runtime_ready:
        if not model.catalog_confirms_model_id:
            return "模型尚未通过上游目录校验，请先检测连接"
        if not model.catalog_confirms_video_capability:
            return "模型目录已匹配，但缺少视频生成能力证据，未映射到视频节点"
        return "协议尚未识别为可执行合同，请先检测连接"
    return ""


def _requires_verified_catalog() -> bool:
    return os.environ.get(
        "VILLAGE_CANVAS_REQUIRE_VERIFIED_DIRECT_MODELS",
        "0",
    ).strip().lower() in {"1", "true", "yes", "on"}
