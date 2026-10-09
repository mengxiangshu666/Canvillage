"""NewAPI-compatible video generator adapter."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import os
import re
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import aiohttp

from novelvideo.generators.video import (
    NEWAPI_VIDEO_DISPLAY_LABELS,
    normalize_newapi_video_model_id,
    resolve_newapi_video_upstream_model,
)
from novelvideo.generators.video.direct_video_protocol_contracts import (
    DIRECT_VIDEO_PROTOCOL_MINIMAX_V2,
    DIRECT_VIDEO_PROTOCOL_OPENAI,
    extract_result_url,
    extract_task_error,
    extract_task_id,
    extract_task_status,
    get_direct_video_protocol_contract,
    protocol_base_url,
    query_url as direct_video_query_url,
    submit_url as direct_video_submit_url,
)
from novelvideo.generators.video.newapi_video_diagnostics import (
    NewApiVideoError,
    extract_request_id as _extract_newapi_request_id,
    safe_url_for_log as _safe_newapi_url_for_log,
    transport_error_message as _newapi_transport_error_message,
)
from novelvideo.generators.video.newapi_video_result_recovery import (
    download_completed_task_video as _recover_completed_newapi_video,
    result_gateway_candidates as _newapi_result_gateway_candidates,
    task_content_url as _newapi_task_content_url,
)
from novelvideo.generators.video.newapi_video_routing import (
    allow_explicit_newapi_video_model as _allow_explicit_newapi_video_model,
    resolve_configured_newapi_video_model,
)
from novelvideo.generators.video.video_capability_envelope import (
    canonical_parameter_key,
    compile_video_provider_parameters,
)
from novelvideo.storage.media_relay import (
    IMAGE_TRANSFORM_AI_REFERENCE_JPEG,
    MediaRelayConfigError,
)

from novelvideo.utils.error_redaction import redact_secrets

from .base import (
    VideoGenResult,
    VideoGeneratorBase,
)
from .huimeng import HuimengVideoGenerator
from .newapi_generate import NewApiGenerateMixin
from .runtime import (
    NEWAPI_VIDEO_DOWNLOAD_RETRIES,
    NEWAPI_VIDEO_DOWNLOAD_TIMEOUT_SECONDS,
    NEWAPI_VIDEO_HTTP_TIMEOUT_SECONDS,
    invoke_build_inline_media_url,
    invoke_extract_wire_duration_seconds as _extract_wire_duration_seconds,
    invoke_upload_media_bytes,
)
from .seedance2 import ShotReference
from .upstream_profiles import (
    DOLASD_OPENAI_VIDEO_PROFILE_ID,
    VideoUpstreamProfile,
)

__all__ = ["NewApiVideoGenerator"]

#: 凭据记录能存活多久。超过这个时长，提交前自动做一次只读重验：用户无感，
#: 上游明确拒绝才拦下来。这样「上次检测通过」不会变成永久免检的绿灯。
DIRECT_VIDEO_CREDENTIAL_FRESHNESS_SECONDS = 24 * 60 * 60

# Bound any raw upstream body embedded in an error string so a hostile or
# misconfigured gateway cannot blow up memory/logs with a megabyte response.
_UPSTREAM_BODY_LOG_LIMIT = 2000


def _bounded_upstream_body(text: str) -> str:
    """Truncate and redact a raw upstream body before it reaches an error string."""
    return redact_secrets(str(text or ""))[:_UPSTREAM_BODY_LOG_LIMIT]


class NewApiVideoGenerator(NewApiGenerateMixin, VideoGeneratorBase):
    """newAPI OpenAI-style async video task generator."""

    @staticmethod
    def _normalize_explicit_video_mode(value: object) -> str:
        """Normalize canvas mode labels without changing omitted-mode behavior."""

        raw = str(value or "").strip()
        if not raw:
            return ""
        token = re.sub(r"[^a-z0-9]+", "", raw.casefold())
        return {
            "texttovideo": "textToVideo",
            "textvideo": "textToVideo",
            "t2v": "textToVideo",
            "imagetovideo": "imageToVideo",
            "imagevideo": "imageToVideo",
            "i2v": "imageToVideo",
            "firstframe": "imageToVideo",
            "firstlastframe": "firstLastFrame",
            "firstlast": "firstLastFrame",
            "keyframe": "firstLastFrame",
            "flf": "firstLastFrame",
            "allreference": "allReference",
            "imagereference": "imageReference",
            "referencetovideo": "allReference",
            "multimodalreference": "allReference",
            "videoedit": "videoEdit",
        }.get(token, "")

    @staticmethod
    def _reference_value(
        reference: object, name: str, default: object = ""
    ) -> object:
        if isinstance(reference, Mapping):
            return reference.get(name, default)
        return getattr(reference, name, default)

    @classmethod
    def _reference_kind(cls, reference: object) -> str:
        raw = cls._reference_value(reference, "type", "") or cls._reference_value(
            reference, "kind", "image"
        )
        raw = getattr(raw, "value", raw)
        value = str(raw or "image").strip().lower()
        return value if value in {"image", "video", "audio"} else "image"

    @classmethod
    def _reference_path(cls, reference: object) -> str:
        if isinstance(reference, (str, os.PathLike)):
            return str(reference).strip()
        for name in ("path", "url", "uri"):
            value = cls._reference_value(reference, name, "")
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    @classmethod
    def _reference_role(cls, reference: object) -> str:
        return str(cls._reference_value(reference, "role", "") or "").strip().lower()

    @classmethod
    def _filter_explicit_media_inputs(
        cls,
        *,
        mode: str,
        image_path: object,
        last_frame_path: object,
        references: object,
    ) -> tuple[str, str, tuple[object, ...]]:
        """Return one strict media view shared by every NewAPI protocol branch."""

        normalized = cls._normalize_explicit_video_mode(mode)
        if normalized not in {
            "textToVideo",
            "imageToVideo",
            "firstLastFrame",
            "allReference",
            "imageReference",
            "videoEdit",
        }:
            return (
                str(image_path or "").strip(),
                str(last_frame_path or "").strip(),
                tuple(references or ()),
            )

        source_references = tuple(references or ())
        image_references = tuple(
            reference
            for reference in source_references
            if cls._reference_kind(reference) == "image"
            and cls._reference_path(reference)
        )

        def first_image() -> str:
            direct = str(image_path or "").strip()
            if direct:
                return direct
            for reference in image_references:
                role = cls._reference_role(reference)
                if any(
                    token in role
                    for token in ("首帧", "first", "first_frame", "firstframe")
                ):
                    return cls._reference_path(reference)
            return cls._reference_path(image_references[0]) if image_references else ""

        def last_image() -> str:
            direct = str(last_frame_path or "").strip()
            if direct:
                return direct
            for reference in image_references:
                role = cls._reference_role(reference)
                if any(
                    token in role
                    for token in ("尾帧", "last", "last_frame", "lastframe")
                ):
                    return cls._reference_path(reference)
            return (
                cls._reference_path(image_references[1])
                if len(image_references) > 1
                else ""
            )

        if normalized == "textToVideo":
            return "", "", ()
        if normalized == "imageToVideo":
            return first_image(), "", ()
        if normalized == "firstLastFrame":
            return first_image(), last_image(), ()
        if normalized == "imageReference":
            selected: list[object] = []
            seen: set[str] = set()
            # A standalone tail frame belongs only to firstLastFrame.  The
            # canvas can leave it populated while switching modes; promoting
            # it here silently changes imageReference into a different
            # reference set and may make the provider reject the request.
            for path in (image_path,):
                value = str(path or "").strip()
                if value and value not in seen:
                    selected.append(
                        {"type": "image", "path": value, "role": "图片参考"}
                    )
                    seen.add(value)
            for reference in image_references:
                path = cls._reference_path(reference)
                if path and path not in seen:
                    selected.append(reference)
                    seen.add(path)
            return "", "", tuple(selected)
        if normalized == "videoEdit":
            return (
                "",
                "",
                tuple(
                    reference
                    for reference in source_references
                    if cls._reference_kind(reference) == "video"
                    and cls._reference_path(reference)
                ),
            )
        if normalized == "allReference":
            selected: list[object] = []
            seen: set[str] = set()
            # H3 rejects a strict first/last frame when any reference media is
            # present.  The canvas often supplies its primary character image
            # through ``image_path`` as well as through ``references``; demote
            # both frame slots to ordinary references and deduplicate them
            # before the relay upload.
            for path, role in (
                (image_path, "首帧参考"),
                (last_frame_path, "尾帧参考"),
            ):
                value = str(path or "").strip()
                if value and value not in seen:
                    selected.append(
                        {"type": "image", "path": value, "role": role}
                    )
                    seen.add(value)
            for reference in source_references:
                path = cls._reference_path(reference)
                if path and path not in seen:
                    selected.append(reference)
                    seen.add(path)
            return "", "", tuple(selected)
        return (
            str(image_path or "").strip(),
            str(last_frame_path or "").strip(),
            source_references,
        )

    @classmethod
    def _explicit_media_contract_error(
        cls,
        *,
        mode: str,
        image_path: object,
        last_frame_path: object,
        references: object,
    ) -> str | None:
        """Reject a selected media mode before any relay or paid submission."""

        normalized = cls._normalize_explicit_video_mode(mode)
        image_present = bool(str(image_path or "").strip())
        last_present = bool(str(last_frame_path or "").strip())
        kinds = {
            cls._reference_kind(reference)
            for reference in references or ()
            if cls._reference_path(reference)
        }
        if normalized == "imageToVideo" and not image_present:
            return "imageToVideo requires one existing image reference"
        if normalized == "firstLastFrame" and not (image_present and last_present):
            return "firstLastFrame requires existing first and last frame images"
        if normalized == "imageReference" and "image" not in kinds:
            return "imageReference requires one existing image reference"
        if normalized == "videoEdit" and "video" not in kinds:
            return "videoEdit requires one existing video reference"
        if normalized == "allReference" and not kinds and not (image_present or last_present):
            return "allReference requires at least one existing reference"
        return None

    @staticmethod
    def _profile_canvas_modes(profile: object) -> tuple[str, ...]:
        """Project a direct-video profile onto the canvas mode vocabulary.

        ``exact_canvas_modes`` is populated by capability discovery when the
        upstream distinguishes modes that share one provider enum.  Built-in
        profiles predate that field, so their coarse ``VideoMode`` values are
        expanded conservatively: a normal reference contract exposes
        ``allReference`` and, when video references are declared, ``videoEdit``;
        a source-video redraw profile exposes only ``videoEdit``.
        """

        exact = tuple(
            str(value).strip()
            for value in (getattr(profile, "exact_canvas_modes", ()) or ())
            if str(value).strip()
        )
        if exact:
            return exact

        # The generic fallback is intentionally open-ended.  A newly added
        # upstream model can still receive a mode until capability discovery
        # records an exact contract for that endpoint/model pair.
        if getattr(profile, "name", "") == "openai-video-generic":
            return ()

        supported: list[str] = []
        declared_values = {
            str(getattr(value, "value", value) or "").strip().lower()
            for value in (getattr(profile, "modes", ()) or ())
        }
        if "text_to_video" in declared_values:
            supported.append("textToVideo")
        if "image_to_video" in declared_values:
            supported.append("imageToVideo")
        if "first_last_frame" in declared_values:
            supported.append("firstLastFrame")
        if "reference_to_video" in declared_values:
            reference_limits = getattr(profile, "reference_limits", None)
            reference_videos = int(
                getattr(reference_limits, "reference_videos", 0) or 0
            )
            profile_family = str(getattr(profile, "family", "") or "").strip().lower()
            profile_name = str(getattr(profile, "name", "") or "").strip().lower()
            source_video_only = profile_family == "kacang-kling-v2v" or (
                "v2v" in profile_name or "video-edit" in profile_name
            )
            if not source_video_only:
                supported.append("allReference")
                # Older direct profiles expose one coarse
                # ``reference_to_video`` enum, while the canvas has a
                # narrower image-only variant that the existing direct
                # Seedance branch already implements.  Keep that compatibility
                # alias when image references are declared.
                reference_images = int(
                    getattr(reference_limits, "reference_images", 0) or 0
                )
                if reference_images > 0 and profile_family == "direct":
                    supported.append("imageReference")
            if reference_videos > 0:
                supported.append("videoEdit")
        return tuple(supported)

    def _explicit_video_mode_contract_error(
        self,
        mode: str,
        *,
        profile: object,
    ) -> tuple[str, dict[str, object]] | None:
        """Return a deterministic preflight error for an undeclared mode."""

        normalized = self._normalize_explicit_video_mode(mode)
        if not normalized:
            return None
        supported = self._profile_canvas_modes(profile)
        model_key = self.model.strip().lower()

        # A few legacy NewAPI branches have a more precise transport contract
        # than the model-name profile they match.  Project those known routes
        # here so explicit first/last-frame or edit requests cannot be silently
        # discarded by the branch below.  Unknown generic models deliberately
        # keep the open-ended behavior from ``_profile_canvas_modes``.
        if not getattr(profile, "exact_canvas_modes", ()):
            if self._is_village_canvas_routed_video_model():
                supported = ("textToVideo", "imageToVideo")
            elif self._is_firefly_seedance2_model():
                supported = (
                    "textToVideo",
                    "imageToVideo",
                    "allReference",
                    "imageReference",
                    "videoEdit",
                )
            elif self._is_prompt_hubs_sd_video_model():
                supported = (
                    "textToVideo",
                    "imageToVideo",
                    "allReference",
                    "imageReference",
                    "videoEdit",
                )
            elif self._is_prompt_hubs_flex_video_model():
                profile_family = str(
                    getattr(profile, "family", "") or ""
                ).strip().lower()
                profile_name = str(
                    getattr(profile, "name", "") or ""
                ).strip().lower()
                if (
                    model_key == "kling-v3-omni-v2v-create"
                    or profile_family == "kacang-kling-v2v"
                    or "v2v" in model_key
                    or "video-edit" in model_key
                    or "v2v" in profile_name
                    or "video-edit" in profile_name
                ):
                    supported = ("videoEdit",)
                else:
                    supported = (
                        "textToVideo",
                        "imageToVideo",
                        "allReference",
                        "imageReference",
                    )
            elif self._is_happyhorse_model():
                supported = (
                    "textToVideo",
                    "imageToVideo",
                    "allReference",
                    "imageReference",
                    "videoEdit",
                )
            elif self._is_minimax_hailuo_model():
                supported = ("textToVideo", "imageToVideo")
            elif self._is_grok_video_channel_model():
                supported = (
                    "textToVideo",
                    "imageToVideo",
                    "allReference",
                    "imageReference",
                )
        if not supported or normalized in supported:
            return None
        model = str(self.upstream_model or self.model).strip() or "unknown"
        error = (
            f"视频模式 {normalized} 不符合模型 {model} 的能力合同；"
            f"支持模式：{', '.join(supported)}"
        )
        metadata: dict[str, object] = {
            "error_code": "VIDEO_CAPABILITY_CONTRACT_INVALID",
            "endpoint_class": "video-request-contract",
            "stage": "preflight",
            "verification_stage": "contract",
            "retryable": False,
            "requested_mode": normalized,
            "supported_modes": list(supported),
            "model": model,
            "suggested_action": "按模型能力合同选择支持的视频模式后重试。",
        }
        return error, metadata

    async def recover_task(self, **kwargs) -> VideoGenResult:
        """Resume a durable NewAPI task without issuing a second submission."""
        return await HuimengVideoGenerator.recover_task(self, **kwargs)

    @staticmethod
    def _model_label(model: str) -> str:
        return NEWAPI_VIDEO_DISPLAY_LABELS.get(model, model)

    @staticmethod
    def _submission_result_is_unknown(exc: NewApiVideoError) -> bool:
        """判断提交是否在没有收到上游响应时中断。"""
        return exc.stage == "submit" and exc.http_status is None

    @staticmethod
    def _is_idempotency_key_conflict(exc: NewApiVideoError | None) -> bool:
        """判断上游是否把「同一个幂等键配了另一份请求体」判成拒单。

        NewAPI 兼容网关返回 HTTP 400 + ``2013``，正文形如
        ``Idempotency-Key was already used for a different request``。
        这类拒单说明请求体变了，但键还是旧键，换新键即可提交；
        它与「任务可能已创建但响应丢了」的未知结果不同。
        """

        if exc is None or exc.stage != "submit":
            return False
        if exc.http_status is not None and exc.http_status != 400:
            return False
        text = f"{exc.response_text or ''} {exc}"
        lowered = text.lower()
        if "idempotency" not in lowered:
            return False
        return (
            "already used for a different request" in lowered
            or "idempotency_key_used" in lowered
            or "(2013)" in text
        )

    @staticmethod
    def _refresh_submit_idempotency_key(key: str, *, attempt: int) -> str:
        """在保住键格式的前提下换一个键，用于幂等键冲突后的重试。"""

        normalized = re.sub(r"[^A-Za-z0-9_-]", "_", str(key or "").strip())
        suffix = f"-r{max(1, int(attempt))}"
        trimmed = normalized[: max(0, 128 - len(suffix))]
        candidate = f"{trimmed}{suffix}" if trimmed else ""
        if len(candidate) < 16 or len(candidate) > 128:
            return "video_" + uuid.uuid4().hex
        return candidate

    @staticmethod
    def _submit_route_is_refused(exc: NewApiVideoError | None) -> bool:
        """Return whether the gateway never reached a create handler.

        A redirect, or a 404/405, means this path is not registered for task
        creation on this gateway — no task can have been created, so walking to
        another declared route under the same idempotency key cannot duplicate
        a paid render.  ``307``/``308`` are included because a gateway that maps
        the route elsewhere answers with those instead of a 404.
        """

        if exc is None or exc.stage != "submit":
            return False
        return exc.http_status in {301, 302, 303, 307, 308, 404, 405}

    @staticmethod
    def _resolve_submit_idempotency_key(
        raw_key: object, *, model: str, output_path: str, prompt: str
    ) -> str:
        """生成所有提交重试共用的、符合渠道格式的幂等键。"""
        supplied = str(raw_key or "").strip()
        normalized = re.sub(r"[^A-Za-z0-9_-]", "_", supplied)
        if 16 <= len(normalized) <= 128:
            return normalized
        if supplied:
            stable_material = f"{model}|{output_path}|{prompt}"
            return "video_" + uuid.uuid5(uuid.NAMESPACE_URL, stable_material).hex
        return "video_" + uuid.uuid4().hex

    @staticmethod
    def _parse_duration_bounds_config(raw: str) -> dict[str, tuple[int, int]]:
        bounds: dict[str, tuple[int, int]] = {}
        for item in raw.split(","):
            entry = item.strip()
            if not entry or ":" not in entry or "-" not in entry:
                continue
            model, raw_bounds = entry.split(":", 1)
            raw_min, raw_max = raw_bounds.split("-", 1)
            try:
                min_seconds = int(raw_min.strip())
                max_seconds = int(raw_max.strip())
            except ValueError:
                continue
            if min_seconds > 0 and max_seconds >= min_seconds:
                bounds[model.strip()] = (min_seconds, max_seconds)
        return bounds

    @classmethod
    def _default_generate_audio_for_model(cls, model: str) -> bool:
        from novelvideo.config import NEWAPI_VIDEO_AUDIO_MODELS

        return model.strip() in set(NEWAPI_VIDEO_AUDIO_MODELS)

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        model: Optional[str] = None,
        resolution: Optional[str] = None,
        generate_audio: Optional[bool] = None,
        protocol: str = DIRECT_VIDEO_PROTOCOL_OPENAI,
        create_path: Optional[str] = None,
        query_path_template: Optional[str] = None,
        cache_runtime_contract: bool = False,
        preserve_upstream_model: bool = False,
        allow_result_gateway_fallback: bool = True,
        allowed_durations: tuple[int, ...] | None = None,
        allowed_aspect_ratios: tuple[str, ...] | None = None,
        duration_parameter_enabled: bool | None = None,
        supports_custom_aspect_ratio: bool = False,
        supports_custom_resolution: bool = False,
        aspect_ratio_parameter_enabled: bool | None = None,
        resolution_parameter_enabled: bool | None = None,
        parameter_values: Mapping[str, object] | None = None,
        provider_mapping: Mapping[str, object] | None = None,
        upstream_profile: VideoUpstreamProfile | None = None,
    ):
        from novelvideo.config import (
            NEWAPI_VIDEO_CREATE_PATH,
            NEWAPI_VIDEO_MODEL,
            NEWAPI_VIDEO_RESOLUTION,
            get_newapi_video_runtime_gateway_candidates,
        )

        self.model = str(model or NEWAPI_VIDEO_MODEL).strip()
        if not preserve_upstream_model:
            self.model = normalize_newapi_video_model_id(self.model)
        if not (api_key or endpoint) and not _allow_explicit_newapi_video_model():
            self.model = resolve_configured_newapi_video_model(self.model)
        self.upstream_model = (
            self.model
            if preserve_upstream_model
            else resolve_newapi_video_upstream_model(self.model)
        )
        self.resolution = resolution or NEWAPI_VIDEO_RESOLUTION
        self.protocol_contract = get_direct_video_protocol_contract(protocol)
        self.protocol = self.protocol_contract.protocol_id
        # 出线合同由调用方（渠道条目）显式传入；没传才按模型名字猜。渠道删掉，
        # 传进来的这份就没了；渠道添回来，它又跟着回来。
        self.wire_profile = upstream_profile
        self.cache_runtime_contract = bool(cache_runtime_contract)
        self.allow_result_gateway_fallback = allow_result_gateway_fallback
        self.allowed_durations = tuple(
            sorted({int(value) for value in (allowed_durations or ()) if int(value) > 0})
        )
        # ``None`` means legacy/range behavior.  ``False`` is an explicit
        # upstream declaration that the duration field must be omitted.
        self.duration_parameter_enabled = duration_parameter_enabled
        self.allowed_aspect_ratios = tuple(
            str(value).strip()
            for value in (allowed_aspect_ratios or ())
            if str(value).strip()
        )
        # Submit through ordered candidates: official key first, then custom
        # NewAPI fallback. Explicit constructor arguments stay test-isolated.
        self.gateway_candidates = get_newapi_video_runtime_gateway_candidates(
            api_key_override=api_key,
            base_url_override=endpoint,
        )
        if self.gateway_candidates:
            first_gateway = self.gateway_candidates[0]
            self.api_key = first_gateway["api_key"]
            self.base_url = first_gateway["base_url"]
        else:
            self.api_key = ""
            self.base_url = ""
        configured_create_path = str(create_path or "").strip() or (
            os.environ.get("NEWAPI_VIDEO_CREATE_PATH", "").strip()
            or str(NEWAPI_VIDEO_CREATE_PATH or "").strip()
        )
        if configured_create_path:
            self.create_path = "/" + configured_create_path.strip("/")
        else:
            # 渠道条目带进来的出线合同写明了创建路径就用它：「生成器不再自己按
            # 名字猜」这条规则对路径同样成立；合同没说才回落到按名字的默认值。
            declared_create_path = self._declared_wire_create_path()
            if declared_create_path:
                self.create_path = declared_create_path
            elif self.model_uses_prompt_hubs_videos_endpoint(self.model):
                self.create_path = "/videos"
            else:
                self.create_path = "/video/generations"
        self.query_path_template = str(query_path_template or "").strip() or (
            self.protocol_contract.query_path_template
        )
        self.supports_custom_aspect_ratio = bool(supports_custom_aspect_ratio)
        self.supports_custom_resolution = bool(supports_custom_resolution)
        self.aspect_ratio_parameter_enabled = aspect_ratio_parameter_enabled
        self.resolution_parameter_enabled = resolution_parameter_enabled
        self.parameter_values = {
            str(key): value
            for key, value in (parameter_values or {}).items()
            if str(key).strip()
        }
        self.provider_mapping = {
            str(key): str(value)
            for key, value in (provider_mapping or {}).items()
            if str(key).strip() and str(value).strip()
        }
        raw_generate_audio = (
            os.environ.get("NEWAPI_VIDEO_GENERATE_AUDIO", "").strip().lower()
        )
        if generate_audio is not None:
            self.generate_audio = generate_audio
        elif raw_generate_audio == "auto" or not raw_generate_audio:
            self.generate_audio = self._default_generate_audio_for_model(self.model)
        else:
            self.generate_audio = raw_generate_audio in {"true", "1", "yes", "on"}

        if not self.api_key:
            raise ValueError(
                "Village Infinite Canvas API key must be set for Village Infinite Canvas API video generation"
            )

    def _declared_wire_create_path(self) -> str:
        """出线合同声明的创建路径，已对齐到本渠道的 base URL。"""

        profile = getattr(self, "wire_profile", None)
        if profile is None:
            return ""
        from .upstream_profiles import relative_wire_route

        return relative_wire_route(
            getattr(profile, "create_path", ""), str(getattr(self, "base_url", ""))
        )

    @staticmethod
    def credential_record_is_fresh(value: object) -> bool:
        """这条凭据记录是不是最近验过的（时间缺失或读不懂都算过期）。"""

        raw = str(value or "").strip()
        if not raw:
            return False
        try:
            checked = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return False
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - checked).total_seconds()
        return 0 <= age <= DIRECT_VIDEO_CREDENTIAL_FRESHNESS_SECONDS

    async def _recheck_stale_credential(self, base_url: str) -> str:
        """提交前把过期的凭据记录重验一次；只在明确被拒时返回错误文本。

        目录接口不校验 Key，所以「上次检测通过」可能已经过期。真正要花钱的
        提交前重新读一次按 Key 鉴权的接口：通过就静默刷新记录，拿不到结论
        就照常提交（不能因为上游一时抽风就不让干活），只有上游明确说这个
        Key 不认才拦。
        """

        if not self.cache_runtime_contract:
            return ""
        from novelvideo.generators.video.direct_video_capability_cache import (
            get_cached_capability_for_model,
        )

        normalized_base = str(base_url or self.base_url or "").strip()
        try:
            cached = get_cached_capability_for_model(
                base_url=normalized_base,
                upstream_model=self.upstream_model,
                protocol=self.protocol,
            )
        except (OSError, ValueError):
            return ""
        if not cached:
            return ""
        if self.credential_record_is_fresh(cached.get("credentialCheckedAt")):
            return ""

        result = await asyncio.to_thread(
            self._probe_credential_for_runtime, normalized_base, cached
        )
        status = str((result or {}).get("status") or "").strip().lower()
        if status not in {"accepted", "rejected", "permission_denied"}:
            return ""
        self._remember_credential_check(normalized_base, cached, result)
        if status == "accepted":
            return ""
        http_status = (result or {}).get("httpStatus")
        return (
            f"上游拒绝了当前保存的密钥（HTTP {http_status}）。"
            "这与模型目录是否可读无关，请在模型中心重新检测该渠道。"
        )

    def _probe_credential_for_runtime(
        self, base_url: str, cached: Mapping[str, Any]
    ) -> dict[str, Any]:
        """单次只读凭据检查；两条路都拿不到回执就返回 unverified。"""

        from .direct_video_probe import (
            _probe_documented_key_info,
            _probe_video_task_read_credential,
        )

        task_path = str(cached.get("openapiQueryPath") or "").strip()
        if task_path:
            return _probe_video_task_read_credential(
                base_url=base_url,
                api_key=str(self.api_key or ""),
                task_path=task_path,
                timeout=5.0,
            )
        return _probe_documented_key_info(
            base_url=base_url,
            api_key=str(self.api_key or ""),
            timeout=5.0,
        )

    @staticmethod
    def _remember_credential_check(
        base_url: str, cached: Mapping[str, Any], result: Mapping[str, Any]
    ) -> None:
        """把这次重验写回缓存，保留原有能力字段（不能只写凭据那两项）。"""

        from novelvideo.generators.video.direct_video_capability_cache import (
            record_capability,
        )

        merged = dict(cached)
        merged["credentialValidation"] = {
            key: result.get(key)
            for key in ("status", "httpStatus", "credentialShape")
            if result.get(key) is not None
        }
        merged["credentialCheckedAt"] = datetime.now(timezone.utc).isoformat()
        try:
            record_capability(
                base_url=base_url,
                protocol=str(cached.get("protocol") or "openai-video"),
                upstream_model=str(cached.get("upstreamModel") or ""),
                capability=merged,
            )
        except (OSError, ValueError):
            # 写缓存失败不能让一次本来能成的生成失败。
            pass

    @staticmethod
    def _extract_request_id(text: str = "", headers: object | None = None) -> str:
        return _extract_newapi_request_id(text, headers)

    @staticmethod
    def _gateway_display_name(value: object) -> str:
        text = str(value or "").strip().lower()
        return {
            "official": "官方 Key",
            "custom": "custom NewAPI",
            "dedicated-video": "视频专用 NewAPI",
            "environment-video": "环境变量视频 NewAPI",
            "override": "指定 NewAPI",
        }.get(text, text or "NewAPI")

    def _headers_for(self, api_key: str | None = None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {str(api_key if api_key is not None else self.api_key).strip()}",
            "Content-Type": "application/json",
        }

    @property
    def headers(self) -> dict[str, str]:
        return self._headers_for()

    @staticmethod
    def _client_timeout() -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(total=NEWAPI_VIDEO_HTTP_TIMEOUT_SECONDS)

    @staticmethod
    def _download_timeout() -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(total=NEWAPI_VIDEO_DOWNLOAD_TIMEOUT_SECONDS)

    @staticmethod
    def _safe_url_for_log(url: str) -> str:
        return _safe_newapi_url_for_log(url)

    @staticmethod
    def _redirect_location(response: object) -> str:
        """Return the ``Location`` of an unfollowed redirect response."""
        headers = getattr(response, "headers", None)
        if headers is None:
            return ""
        for name in ("Location", "location"):
            value = headers.get(name)
            if value:
                return str(value).strip()
        return ""

    def _apply_capability_contract(self, payload: dict) -> dict:
        """Enforce the selected endpoint/model contract immediately before POST."""
        # ``video.v1`` is already compiled against the relay's public contract.
        # Re-running the legacy profile normalizer here would translate the
        # canonical ``768`` value back to the profile default ``2k`` and make
        # H3 reject an otherwise valid low-resolution request.
        if str(payload.get("version") or "").strip().lower() == "video.v1":
            return payload
        model_key = str(payload.get("model") or "").strip()
        if not model_key:
            return payload
        from novelvideo.generators.video.capabilities import NativeAudio
        from novelvideo.generators.video.direct_video_profiles import (
            resolve_direct_video_profile,
        )

        profile = resolve_direct_video_profile(
            model_key,
            base_url=self.base_url,
            protocol=self.protocol,
        )
        if self.protocol == DIRECT_VIDEO_PROTOCOL_MINIMAX_V2:
            return payload
        metadata_value = payload.get("metadata")
        metadata = metadata_value if isinstance(metadata_value, dict) else {}

        if profile.size_slots:
            requested_size = (
                payload.get(profile.size_field or "size")
                or payload.get("size")
                or payload.get("ratio")
                or payload.get("aspect_ratio")
                or metadata.get("size")
                or metadata.get("ratio")
                or profile.default_aspect_ratio
            )
            resolved_size = profile.resolve_size(requested_size)
            if resolved_size is None:
                raise ValueError(
                    f"No legal size slot declared for video model: {model_key}"
                )
            for key in ("ratio", "aspect_ratio", "resolution", "video_resolution"):
                payload.pop(key, None)
                metadata.pop(key, None)
            payload.pop("size", None)
            payload[profile.size_field or "size"] = resolved_size
        else:
            aspect_parameter_enabled = (
                self.aspect_ratio_parameter_enabled
                if self.aspect_ratio_parameter_enabled is not None
                else bool(profile.aspect or profile.supports_custom_aspect_ratio)
            )
            resolution_parameter_enabled = (
                self.resolution_parameter_enabled
                if self.resolution_parameter_enabled is not None
                else bool(profile.resolution or profile.supports_custom_resolution)
            )
            for container in (payload, metadata):
                for key in ("ratio", "aspect_ratio"):
                    if key not in container:
                        continue
                    if aspect_parameter_enabled:
                        container[key] = profile.resolve_aspect_ratio(container[key])
                    else:
                        # An explicit empty capability is authoritative: do
                        # not send a guessed ratio to an endpoint that does
                        # not expose an aspect-ratio parameter.
                        container.pop(key, None)
                for key in ("resolution", "video_resolution"):
                    if key not in container:
                        continue
                    if resolution_parameter_enabled:
                        container[key] = profile.resolve_resolution(container[key])
                    else:
                        container.pop(key, None)

        for container in (payload, metadata):
            for key in ("duration", "duration_seconds", "seconds"):
                if key not in container:
                    continue
                if self.duration_parameter_enabled is False:
                    container.pop(key, None)
                    continue
                raw_duration = container[key]
                if self.duration_parameter_enabled is True:
                    # A discovered runtime contract owns the transport gate,
                    # while its advertised options remain UI guidance. Keep
                    # an explicit caller value intact instead of silently
                    # snapping it to a stale family profile duration.
                    try:
                        numeric_duration = float(raw_duration)
                        normalized_duration: int | float = (
                            int(numeric_duration)
                            if numeric_duration.is_integer()
                            else numeric_duration
                        )
                    except (TypeError, ValueError):
                        normalized_duration = raw_duration
                    container[key] = (
                        str(normalized_duration)
                        if isinstance(raw_duration, str)
                        else normalized_duration
                    )
                else:
                    resolved_duration = profile.resolve_duration(raw_duration)
                    container[key] = (
                        str(resolved_duration)
                        if isinstance(raw_duration, str)
                        else resolved_duration
                    )

        if profile.native_audio is NativeAudio.UNSUPPORTED:
            payload.pop("generate_audio", None)
            metadata.pop("generate_audio", None)
        elif profile.native_audio is NativeAudio.REQUIRED:
            target = metadata if metadata_value is not None else payload
            target["generate_audio"] = True

        if metadata_value is not None:
            payload["metadata"] = metadata
        return payload

    @staticmethod
    def _split_provider_parameter_target(key: str) -> tuple[str, str]:
        """Resolve an optional deterministic destination prefix.

        Catalogs normally expose a bare provider field name. A catalog that
        needs to distinguish the request wrapper can opt into ``payload.`` or
        ``metadata.`` without introducing another model-specific adapter.
        """
        normalized = str(key or "").strip()
        lowered = normalized.casefold()
        for prefix, target in (
            ("payload.", "payload"),
            ("request.", "payload"),
            ("metadata.", "metadata"),
            ("options.", "metadata"),
        ):
            if lowered.startswith(prefix):
                return target, normalized[len(prefix) :].strip()
        return "", normalized

    def _apply_provider_parameters(
        self,
        payload: dict[str, object],
        *,
        metadata: dict[str, object] | None = None,
        multipart_fields: dict[str, object] | None = None,
    ) -> None:
        """Place discovered provider fields into the active request shape.

        The direct registry already validated the model and protocol. This
        method only maps user-facing keys to their discovered provider names;
        it never guesses a spelling or silently drops an ordinary field.
        """
        if multipart_fields is not None:
            default_target = "multipart"
        elif (
            str(payload.get("version") or "").strip().lower() == "video.v1"
            or self.protocol == DIRECT_VIDEO_PROTOCOL_MINIMAX_V2
            or "metadata" not in payload
        ):
            default_target = "payload"
        else:
            default_target = "metadata"

        if metadata is None:
            raw_metadata = payload.get("metadata")
            metadata = raw_metadata if isinstance(raw_metadata, dict) else None

        # Common canvas controls are authored with stable names, but an
        # upstream schema may call them ``seconds``, ``ratio`` or another
        # provider-specific field.  Apply explicit mappings to the same
        # request container that currently owns each control.  Advanced
        # parameter compilation below deliberately excludes these keys so a
        # mapped common value is emitted exactly once.
        common_aliases = {
            "duration": ("duration", "duration_seconds", "durationSeconds", "seconds"),
            "resolution": ("resolution", "video_resolution", "output_resolution", "outputResolution"),
            "size": ("size", "size_slot", "sizeSlot"),
            "aspectRatio": ("aspect_ratio", "aspectRatio", "ratio"),
            "generateAudio": ("generate_audio", "generateAudio", "audio"),
            "images": ("image", "images", "reference_images", "referenceImages"),
            "videos": ("videos", "reference_videos", "referenceVideos"),
            "audios": ("audios", "reference_audios", "referenceAudios"),
        }

        def find_value(aliases: tuple[str, ...]) -> tuple[dict[str, object] | None, object]:
            for container in (payload, metadata):
                if not isinstance(container, dict):
                    continue
                for alias in aliases:
                    if alias in container:
                        return container, container[alias]
            return None, None

        common_parameter_keys: set[str] = set()
        for canonical, aliases in common_aliases.items():
            provider_raw = next(
                (
                    value
                    for key, value in self.provider_mapping.items()
                    if canonical_parameter_key(key) == canonical
                ),
                None,
            )
            if not provider_raw:
                continue
            provider_key = str(provider_raw).strip()
            explicit_target, clean_key = self._split_provider_parameter_target(provider_key)
            if not clean_key:
                continue
            source_container, value = find_value(aliases)
            explicit_value = next(
                (
                    item_value
                    for item_key, item_value in self.parameter_values.items()
                    if canonical_parameter_key(item_key) == canonical
                ),
                None,
            )
            if explicit_value is not None:
                value = explicit_value
            if source_container is None and explicit_value is None:
                continue
            for container in (payload, metadata):
                if isinstance(container, dict):
                    for alias in aliases:
                        container.pop(alias, None)
            if explicit_target:
                target_name = explicit_target
            elif provider_key in payload:
                target_name = "payload"
            elif isinstance(metadata, dict) and provider_key in metadata:
                target_name = "metadata"
            elif canonical in {"duration", "size", "images", "videos", "audios"}:
                target_name = "payload"
            else:
                target_name = "metadata" if metadata is not None else "payload"
            if target_name == "multipart" and multipart_fields is not None:
                multipart_fields[clean_key] = value
            elif target_name == "metadata" and metadata is not None:
                metadata[clean_key] = value
            else:
                payload[clean_key] = value
            common_parameter_keys.add(canonical)

        advanced_values = {
            key: value
            for key, value in self.parameter_values.items()
            if canonical_parameter_key(key) not in common_parameter_keys
        }
        parameters = compile_video_provider_parameters(
            advanced_values,
            self.provider_mapping,
        )
        if not parameters:
            return

        protected_payload_keys = {
            "model",
            "prompt",
            "version",
            "operation",
            "seconds",
            "duration",
            "duration_seconds",
            "metadata",
            "content",
            "media_inputs",
        }
        for provider_key, value in parameters.items():
            explicit_target, clean_key = self._split_provider_parameter_target(provider_key)
            if not clean_key:
                continue
            target_name = explicit_target or default_target
            if target_name == "multipart" and multipart_fields is not None:
                multipart_fields[clean_key] = value
            elif target_name == "metadata" and metadata is not None:
                metadata[clean_key] = value
            elif target_name == "payload":
                if clean_key in protected_payload_keys and metadata is not None:
                    metadata[clean_key] = value
                else:
                    payload[clean_key] = value
            elif target_name == "metadata":
                payload[clean_key] = value

    @classmethod
    def _transport_error_message(cls, stage: str, url: str, exc: BaseException) -> str:
        return _newapi_transport_error_message(
            stage,
            url,
            exc,
            redact_url=cls._safe_url_for_log,
        )

    @staticmethod
    def _json_request_contract(
        payload: dict[str, object], body: bytes | None = None
    ) -> dict[str, object]:
        """Describe a video request without persisting prompts or media URLs."""

        metadata = payload.get("metadata")
        media_counts = {"image": 0, "video": 0, "audio": 0}

        def visit(value: object, *, key: str = "", depth: int = 0) -> None:
            if depth > 4:
                return
            normalized_key = key.replace("_", "").replace("-", "").casefold()
            for media_kind in media_counts:
                if media_kind not in normalized_key:
                    continue
                if isinstance(value, (list, tuple)):
                    media_counts[media_kind] += len(value)
                elif isinstance(value, str) and value.strip():
                    media_counts[media_kind] += 1
            if isinstance(value, dict):
                for nested_key, nested_value in value.items():
                    visit(nested_value, key=str(nested_key), depth=depth + 1)

        visit(payload)
        contract: dict[str, object] = {
            "content_type": "application/json; charset=utf-8",
            "payload_keys": sorted(str(key) for key in payload),
            "metadata_keys": (
                sorted(str(key) for key in metadata)
                if isinstance(metadata, dict)
                else []
            ),
            "media_counts": {
                key: count for key, count in media_counts.items() if count > 0
            },
        }
        if body is not None:
            contract["body_bytes"] = len(body)
            contract["content_length"] = len(body)
            contract["body_sha256"] = hashlib.sha256(body).hexdigest()[:16]
        return contract

    async def _post_json(
        self,
        url: str,
        payload: dict,
        *,
        idempotency_key: str = "",
    ) -> dict:
        payload = self._apply_capability_contract(payload)
        try:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise NewApiVideoError(
                f"Village Infinite Canvas API request serialization failed: {exc}",
                stage="serialize",
                url_path=urllib.parse.urlsplit(url).path,
                request_contract=self._json_request_contract(payload),
            ) from exc
        request_contract = self._json_request_contract(payload, body)
        headers = self.headers
        headers["Content-Type"] = str(request_contract["content_type"])
        headers["Accept"] = "application/json"
        headers["Content-Length"] = str(len(body))
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            async with aiohttp.ClientSession(timeout=self._client_timeout()) as session:
                # Never follow a refusal redirect here.  aiohttp rewrites
                # ``POST`` to ``GET`` on 301/302 and drops the body, so a
                # gateway that moved its create route answers an unfollowed
                # GET the caller never sent — which then reads as a 404 on a
                # path we did not ask for.  Observing the redirect lets the
                # submit loop walk to another registered route instead.
                async with session.post(
                    url, data=body, headers=headers, allow_redirects=False
                ) as resp:
                    text = await resp.text()
                    if 300 <= resp.status < 400:
                        raise NewApiVideoError(
                            f"视频提交路由被重定向：HTTP {resp.status} -> "
                            f"{self._safe_url_for_log(self._redirect_location(resp))}",
                            request_id=self._extract_request_id(text, resp.headers),
                            http_status=resp.status,
                            response_text=text,
                            stage="submit",
                            url_path=urllib.parse.urlsplit(url).path,
                            request_contract=request_contract,
                            redirect_location=self._safe_url_for_log(
                                self._redirect_location(resp)
                            ),
                        )
                    if resp.status < 200 or resp.status >= 300:
                        request_id = self._extract_request_id(text, resp.headers)
                        raise NewApiVideoError(
                            f"Village Infinite Canvas API submit failed: HTTP {resp.status} - {_bounded_upstream_body(text)}",
                            request_id=request_id,
                            http_status=resp.status,
                            response_text=_bounded_upstream_body(text),
                            stage="submit",
                            url_path=urllib.parse.urlsplit(url).path,
                            request_contract=request_contract,
                        )
                    try:
                        data = json.loads(text)
                        if isinstance(data, dict):
                            data["_newapi_request_id"] = self._extract_request_id(
                                text, resp.headers
                            )
                        return data
                    except json.JSONDecodeError as exc:
                        content_type = str(resp.headers.get("Content-Type") or "").lower()
                        if "text/html" in content_type or text.lstrip().startswith("<"):
                            detail = (
                                "视频提交地址返回了 HTML 页面，当前 Base URL 可能指向管理页面 "
                                "而不是视频 API 路由"
                            )
                        elif not text.strip():
                            detail = "视频提交响应为空，渠道可能截断了响应或返回了错误路由"
                        else:
                            detail = "视频提交响应不是有效 JSON"
                        raise NewApiVideoError(
                            f"{detail}；content_type={content_type or 'unknown'}",
                            http_status=resp.status,
                            response_text=text[:500],
                            stage="submit",
                            url_path=urllib.parse.urlsplit(url).path,
                            request_contract=request_contract,
                        ) from exc
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise NewApiVideoError(
                self._transport_error_message("submit", url, exc),
                stage="submit",
                url_path=urllib.parse.urlsplit(url).path,
                request_contract=request_contract,
            ) from exc

    async def _post_multipart(
        self,
        url: str,
        fields: dict[str, object],
        files: list[tuple[str, str, bytes, str]],
        *,
        idempotency_key: str = "",
    ) -> dict:
        """Submit a Wokey multimodal-video request without leaking JSON headers."""

        form = aiohttp.FormData()
        for name, value in fields.items():
            form.add_field(name, str(value))
        for field_name, filename, content, content_type in files:
            form.add_field(
                field_name,
                content,
                filename=filename,
                content_type=content_type,
            )
        headers = {"Authorization": f"Bearer {self.api_key.strip()}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            async with aiohttp.ClientSession(timeout=self._client_timeout()) as session:
                # See ``_post_json``: following the redirect would resend this
                # as a GET without the multipart form.
                async with session.post(
                    url,
                    data=form,
                    headers=headers,
                    allow_redirects=False,
                ) as resp:
                    text = await resp.text()
                    if 300 <= resp.status < 400:
                        raise NewApiVideoError(
                            f"视频提交路由被重定向：HTTP {resp.status} -> "
                            f"{self._safe_url_for_log(self._redirect_location(resp))}",
                            request_id=self._extract_request_id(text, resp.headers),
                            http_status=resp.status,
                            response_text=text,
                            stage="submit",
                            url_path=urllib.parse.urlsplit(url).path,
                            redirect_location=self._safe_url_for_log(
                                self._redirect_location(resp)
                            ),
                        )
                    if resp.status < 200 or resp.status >= 300:
                        request_id = self._extract_request_id(text, resp.headers)
                        raise NewApiVideoError(
                            f"Village Infinite Canvas API submit failed: HTTP {resp.status} - {_bounded_upstream_body(text)}",
                            request_id=request_id,
                            http_status=resp.status,
                            response_text=_bounded_upstream_body(text),
                            stage="submit",
                            url_path=urllib.parse.urlsplit(url).path,
                        )
                    try:
                        data = json.loads(text)
                        if isinstance(data, dict):
                            data["_newapi_request_id"] = self._extract_request_id(
                                text,
                                resp.headers,
                            )
                        return data
                    except json.JSONDecodeError as exc:
                        content_type = str(resp.headers.get("Content-Type") or "").lower()
                        detail = (
                            "视频提交地址返回了 HTML 页面，当前 Base URL 可能指向管理页面而不是视频 API 路由"
                            if "text/html" in content_type or text.lstrip().startswith("<")
                            else "视频提交响应为空，渠道可能截断了响应或返回了错误路由"
                            if not text.strip()
                            else "视频提交响应不是有效 JSON"
                        )
                        raise NewApiVideoError(
                            f"{detail}；content_type={content_type or 'unknown'}",
                            http_status=resp.status,
                            response_text=text[:500],
                            stage="submit",
                            url_path=urllib.parse.urlsplit(url).path,
                        ) from exc
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise NewApiVideoError(
                self._transport_error_message("submit", url, exc),
                stage="submit",
                url_path=urllib.parse.urlsplit(url).path,
            ) from exc

    @classmethod
    async def _wokey_multipart_file(
        cls,
        value: str,
        *,
        default_extension: str,
    ) -> tuple[str, bytes, str]:
        """Materialize a local, data-URL, or public reference for Wokey upload."""

        import base64
        import mimetypes

        source = str(value or "").strip()
        if not source:
            raise ValueError("empty Wokey media input")
        parsed = urllib.parse.urlsplit(source)
        if source.startswith("data:"):
            header, separator, encoded = source.partition(",")
            if not separator or ";base64" not in header:
                raise ValueError("unsupported Wokey data URL media input")
            content = base64.b64decode(encoded)
            extension = cls._ext_from_data_url_header(header, default_extension)
            filename = f"reference.{extension}"
        elif parsed.scheme in {"http", "https"}:
            async with aiohttp.ClientSession(timeout=cls._client_timeout()) as session:
                async with session.get(source) as response:
                    if response.status < 200 or response.status >= 300:
                        raise RuntimeError(
                            "Wokey reference download failed: "
                            f"HTTP {response.status}; url={cls._safe_url_for_log(source)}"
                        )
                    content = await response.read()
                    response_type = response.headers.get("Content-Type", "")
            filename = (
                Path(urllib.parse.unquote(parsed.path)).name
                or f"reference.{default_extension}"
            )
            extension = Path(filename).suffix.lstrip(".") or default_extension
            if response_type:
                content_type = response_type.split(";", 1)[0].strip()
            else:
                content_type = (
                    mimetypes.guess_type(filename)[0] or "application/octet-stream"
                )
            if not content:
                raise RuntimeError("Wokey reference download returned an empty file")
            return filename, content, content_type
        else:
            path = Path(source)
            if not path.is_file():
                raise FileNotFoundError(f"Wokey reference file not found: {source}")
            content = await asyncio.to_thread(path.read_bytes)
            filename = path.name
            extension = path.suffix.lstrip(".") or default_extension

        if not content:
            raise RuntimeError("Wokey reference input is empty")
        filename = filename if Path(filename).suffix else f"{filename}.{extension}"
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return filename, content, content_type

    async def _get_json(self, url: str) -> dict:
        try:
            async with aiohttp.ClientSession(timeout=self._client_timeout()) as session:
                async with session.get(url, headers=self.headers) as resp:
                    text = await resp.text()
                    if resp.status < 200 or resp.status >= 300:
                        request_id = self._extract_request_id(text, resp.headers)
                        raise NewApiVideoError(
                            f"Village Infinite Canvas API task query failed: HTTP {resp.status} - {text}",
                            request_id=request_id,
                            http_status=resp.status,
                            response_text=text,
                            stage="query",
                            url_path=urllib.parse.urlsplit(url).path,
                        )
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError as exc:
                        content_type = str(resp.headers.get("Content-Type") or "").lower()
                        detail = (
                            "视频任务查询地址返回了 HTML 页面，当前查询路由可能指向管理页面"
                            if "text/html" in content_type or text.lstrip().startswith("<")
                            else "视频任务查询响应为空"
                            if not text.strip()
                            else "视频任务查询响应不是有效 JSON"
                        )
                        raise NewApiVideoError(
                            f"{detail}；content_type={content_type or 'unknown'}",
                            http_status=resp.status,
                            response_text=text[:500],
                            stage="query",
                            url_path=urllib.parse.urlsplit(url).path,
                        ) from exc
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise NewApiVideoError(
                self._transport_error_message("task query", url, exc),
                stage="query",
                url_path=urllib.parse.urlsplit(url).path,
            ) from exc

    @staticmethod
    def _write_video_bytes_atomically(output_path: str, content: bytes) -> None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial_path = path.with_suffix(f"{path.suffix}.part")
        partial_path.write_bytes(content)
        partial_path.replace(path)

    async def _download_video(self, url: str, output_path: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(1, NEWAPI_VIDEO_DOWNLOAD_RETRIES + 1):
            try:
                if url.startswith("data:"):
                    header, _, encoded = url.partition(",")
                    if ";base64" not in header or not encoded:
                        raise RuntimeError("Unsupported data URL video response")
                    import base64

                    content = base64.b64decode(encoded)
                else:
                    async with aiohttp.ClientSession(
                        timeout=self._download_timeout()
                    ) as session:
                        async with session.get(url) as resp:
                            if resp.status != 200:
                                response_text = (await resp.text())[:500]
                                raise NewApiVideoError(
                                    f"视频结果下载失败：HTTP {resp.status}",
                                    http_status=resp.status,
                                    response_text=response_text,
                                    stage="download",
                                    url_path=urllib.parse.urlsplit(url).path,
                                )
                            content_type = str(
                                resp.headers.get("Content-Type") or ""
                            ).lower()
                            content = await resp.read()
                            if "text/html" in content_type or content.lstrip().startswith(b"<"):
                                raise NewApiVideoError(
                                    "视频结果下载地址返回了 HTML 页面",
                                    http_status=resp.status,
                                    response_text=content[:500].decode(
                                        "utf-8", errors="replace"
                                    ),
                                    stage="download",
                                    url_path=urllib.parse.urlsplit(url).path,
                                )
                if not content:
                    raise RuntimeError(
                        "Village Infinite Canvas API result download returned an empty video"
                    )
                self._write_video_bytes_atomically(output_path, content)
                return content
            except NewApiVideoError:
                raise
            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
                OSError,
                RuntimeError,
            ) as exc:
                last_error = exc
                if attempt < NEWAPI_VIDEO_DOWNLOAD_RETRIES:
                    await asyncio.sleep(0.5 * attempt)
        raise RuntimeError(
            "Village Infinite Canvas API result download failed after "
            f"{NEWAPI_VIDEO_DOWNLOAD_RETRIES} attempts: {last_error}"
        ) from last_error

    def _result_gateway_candidates(self) -> list[dict[str, str]]:
        """Return GET-only gateways for provider-status and result recovery.

        Video creation keeps its configured submit gateway exactly as selected by
        the user.  A WireGuard route may accept submissions while a completed
        artifact is only queryable through the same public NewAPI service, so
        ``HK_API_PRIMARY`` is appended solely for GET polling/content recovery.
        """
        # 注意：这是一条兜底（fallback）读取地址，正常应来自模型中心
        # (model_gateway_settings) 的唯一下发；保留环境变量是为兼容仍依赖
        # HK_API_PRIMARY / VILLAGE_CANVAS_VIDEO_RESULT_FALLBACK_BASE_URL 的部署。
        # 它只会追加在已配置网关之后（见 result_gateway_candidates 的先配置后兜底
        # 去重），不会覆盖已配置的地址。
        public_result_base = (
            os.environ.get("VILLAGE_CANVAS_VIDEO_RESULT_FALLBACK_BASE_URL", "").strip()
            or os.environ.get("HK_API_PRIMARY", "").strip()
        )
        return _newapi_result_gateway_candidates(
            self.gateway_candidates,
            api_key=self.api_key,
            base_url=self.base_url,
            allow_fallback=self.allow_result_gateway_fallback,
            fallback_base_url=public_result_base,
        )

    async def _download_task_content(self, task_id: str, output_path: str) -> bytes:
        """Download through the current NewAPI content endpoint."""
        return await self._download_task_content_from_gateway(
            task_id,
            output_path,
            base_url=self.base_url,
            api_key=self.api_key,
        )

    async def _download_task_content_from_gateway(
        self,
        task_id: str,
        output_path: str,
        *,
        base_url: str,
        api_key: str,
    ) -> bytes:
        """Download a completed task through one authenticated content gateway."""
        base = str(base_url or "").strip().rstrip("/")
        url = f"{base}/videos/{urllib.parse.quote(str(task_id), safe='')}/content?download=1"
        last_error: Exception | None = None
        for attempt in range(1, NEWAPI_VIDEO_DOWNLOAD_RETRIES + 1):
            try:
                async with aiohttp.ClientSession(
                    timeout=self._download_timeout()
                ) as session:
                    async with session.get(
                        url, headers=self._headers_for(api_key)
                    ) as resp:
                        if resp.status < 200 or resp.status >= 300:
                            response_text = (await resp.text())[:500]
                            raise NewApiVideoError(
                                f"视频统一内容端点下载失败：HTTP {resp.status}",
                                http_status=resp.status,
                                response_text=response_text,
                                stage="download",
                                url_path=urllib.parse.urlsplit(url).path,
                            )
                        content_type = str(
                            resp.headers.get("Content-Type") or ""
                        ).lower()
                        content = await resp.read()
                        if "text/html" in content_type or content.lstrip().startswith(b"<"):
                            raise NewApiVideoError(
                                "视频统一内容端点返回了 HTML 页面",
                                http_status=resp.status,
                                response_text=content[:500].decode(
                                    "utf-8", errors="replace"
                                ),
                                stage="download",
                                url_path=urllib.parse.urlsplit(url).path,
                            )
                if not content:
                    raise RuntimeError(
                        "Village Infinite Canvas API content download returned an empty video"
                    )
                self._write_video_bytes_atomically(output_path, content)
                return content
            except NewApiVideoError:
                raise
            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
                OSError,
                RuntimeError,
            ) as exc:
                last_error = exc
                if attempt < NEWAPI_VIDEO_DOWNLOAD_RETRIES:
                    await asyncio.sleep(0.5 * attempt)
        raise RuntimeError(
            "Village Infinite Canvas API content download failed after "
            f"{NEWAPI_VIDEO_DOWNLOAD_RETRIES} attempts: {last_error}"
        ) from last_error

    def _task_content_url(self, task_id: str, *, base_url: str | None = None) -> str:
        return _newapi_task_content_url(
            task_id,
            base_url=str(base_url or self.base_url),
        )

    async def _download_completed_task_video(
        self,
        *,
        task_id: str,
        video_url: str | None,
        output_path: str,
        on_direct_download_failure: Callable[[str], None] | None = None,
    ) -> str:
        """Persist a completed video without relying solely on a provider CDN URL.

        Some upstreams return a direct asset URL that is reachable from their
        workers but not from the desktop runtime. The unified NewAPI content
        endpoint is authenticated and retains the task's upstream context, so
        it becomes the recovery route whenever the direct transfer fails.
        """

        if self.protocol != DIRECT_VIDEO_PROTOCOL_OPENAI:
            direct_url = str(video_url or "").strip()
            if not direct_url:
                raise RuntimeError(
                    f"{self.protocol} completed without a downloadable video URL"
                )
            await self._download_video(direct_url, output_path)
            return direct_url
        return await _recover_completed_newapi_video(
            task_id=task_id,
            video_url=video_url,
            output_path=output_path,
            base_url=self.base_url,
            api_key=self.api_key,
            gateway_candidates=self._result_gateway_candidates(),
            download_video=self._download_video,
            download_task_content=self._download_task_content,
            download_task_content_from_gateway=self._download_task_content_from_gateway,
            on_direct_download_failure=on_direct_download_failure,
        )

    @staticmethod
    def _ext_from_data_url_header(header: str, default: str = "png") -> str:
        mime_type = header.removeprefix("data:").split(";", 1)[0].strip().lower()
        return {
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
            "video/mp4": "mp4",
            "video/webm": "webm",
            "audio/mpeg": "mp3",
            "audio/mp3": "mp3",
            "audio/wav": "wav",
            "audio/x-wav": "wav",
            "audio/mp4": "m4a",
        }.get(mime_type, default)

    @classmethod
    async def _relay_frame_input(
        cls,
        image_value: str,
        *,
        default_ext: str = "png",
        require_public_https: bool = False,
    ) -> str:
        return await cls._relay_media_input(
            image_value,
            default_ext=default_ext,
            image_transform=IMAGE_TRANSFORM_AI_REFERENCE_JPEG,
            require_public_https=require_public_https,
        )

    @classmethod
    async def _relay_media_input(
        cls,
        media_value: str,
        *,
        default_ext: str = "png",
        image_transform: str | None = None,
        require_public_https: bool = False,
    ) -> str:
        media_value = str(media_value or "").strip()
        if not media_value:
            raise ValueError("empty media input")
        if media_value.startswith(("http://", "https://")):
            if require_public_https and not media_value.startswith("https://"):
                raise ValueError("MiniMax video-v2 requires a public HTTPS media URL")
            return media_value

        if media_value.startswith("data:"):
            header, _, encoded = media_value.partition(",")
            if ";base64" not in header or not encoded:
                raise ValueError("unsupported data URL media input")
            import base64

            data = base64.b64decode(encoded)
            ext = cls._ext_from_data_url_header(header, default_ext)
            try:
                url = await asyncio.to_thread(
                    invoke_upload_media_bytes,
                    data,
                    ext=ext,
                    image_transform=image_transform,
                )
            except MediaRelayConfigError:
                if require_public_https:
                    raise
                return await asyncio.to_thread(
                    invoke_build_inline_media_url,
                    data,
                    ext=ext,
                    image_transform=image_transform,
                )
            if require_public_https and not str(url).startswith("https://"):
                raise ValueError("MiniMax video-v2 requires a public HTTPS media URL")
            return url

        media_path = Path(media_value)
        ext = media_path.suffix.lstrip(".") or default_ext
        data = media_path.read_bytes()
        try:
            url = await asyncio.to_thread(
                invoke_upload_media_bytes,
                data,
                ext=ext,
                image_transform=image_transform,
            )
        except MediaRelayConfigError:
            if require_public_https:
                raise
            return await asyncio.to_thread(
                invoke_build_inline_media_url,
                data,
                ext=ext,
                image_transform=image_transform,
            )
        if require_public_https and not str(url).startswith("https://"):
            raise ValueError("MiniMax video-v2 requires a public HTTPS media URL")
        return url

    def _duration_bounds(self) -> tuple[int, int]:
        from novelvideo.config import NEWAPI_VIDEO_DURATION_BOUNDS

        if self.allowed_durations:
            return min(self.allowed_durations), max(self.allowed_durations)
        configured = self._parse_duration_bounds_config(NEWAPI_VIDEO_DURATION_BOUNDS)
        if self._is_village_canvas_routed_video_model():
            return configured.get(self.model.strip(), (4, 15))
        if self._is_wokey_jimeng_seedance2_model():
            default = (
                (4, 30)
                if self.model.strip().lower() == "jimeng-seedance-2.5"
                else (4, 15)
            )
            return configured.get(self.model.strip(), default)
        if self._is_happyhorse_model():
            return configured.get(self.model.strip(), (1, 15))
        if self._is_minimax_hailuo_model():
            return configured.get(self.model.strip(), (6, 10))
        if self._is_prompt_hubs_flex_video_model():
            options = self._prompt_hubs_flex_duration_options(self.model)
            return configured.get(self.model.strip(), (min(options), max(options)))
        if self._is_prompt_hubs_sd_video_model():
            if "15s" in self.model.strip().lower():
                return configured.get(self.model.strip(), (15, 15))
            return configured.get(self.model.strip(), (4, 15))
        if self._is_firefly_seedance2_model():
            return configured.get(self.model.strip(), (4, 15))
        if self._is_kacang_mini_h3_model():
            return configured.get(self.model.strip(), (5, 15))
        if self.model.strip() in {"seedance-2.5", "seedance-2-5"}:
            return configured.get(self.model.strip(), (4, 30))
        return configured.get(self.model.strip(), (2, 12))

    def _is_village_canvas_routed_video_model(self) -> bool:
        return self.model.strip().lower() == "village-canvas-video"

    def _uses_newapi_video_v1_payload(self) -> bool:
        """Return whether this model uses the relay's documented ``video.v1`` body.

        The relay exposes MiniMax H3 through ``POST /v1/videos`` but does not
        use the legacy OpenAI body.  Its public contract requires
        ``duration_seconds``/``aspect_ratio`` and ``media_inputs``.  Keep the
        decision attached to capability evidence so an unrelated OpenAI
        compatible gateway is not silently sent a provider-specific payload.
        """
        if self.protocol != DIRECT_VIDEO_PROTOCOL_OPENAI:
            return False
        try:
            from novelvideo.generators.video.direct_video_profiles import (
                resolve_direct_video_profile,
            )

            profile = resolve_direct_video_profile(
                self.upstream_model,
                base_url=self.base_url,
                protocol=self.protocol,
            )
            if profile.name == "minimax-h3-v2":
                return True
        except Exception:
            return False
        return False

    def _uses_documented_openai_video_payload(self) -> bool:
        """Return whether this channel's documented body is the OpenAI shape.

        Dola publishes Seedance through an OpenAI-compatible contract
        (``duration`` / ``ratio`` / ``reference_images``).  The model name still
        looks like the Huimeng Seedance SKU, so without this check the
        name-based branch would send ``seconds`` plus a ``metadata`` wrapper the
        gateway does not accept.
        """

        if self.protocol != DIRECT_VIDEO_PROTOCOL_OPENAI:
            return False
        try:
            profile = self._wire_profile()
        except Exception:
            return False
        return (
            str(getattr(profile, "profile_id", "") or "")
            == DOLASD_OPENAI_VIDEO_PROFILE_ID
        )

    async def _build_documented_openai_video_payload(
        self,
        *,
        image_path: str,
        last_frame_path: str | None,
        prompt: str,
        duration: float,
        ratio: str,
        references: object,
        log: Callable[[str], None],
    ) -> dict[str, object]:
        """Compile the documented OpenAI-video request body for this channel."""

        from .upstream_profiles import compile_payload

        profile = self._wire_profile()
        first_frame_url = ""
        if image_path:
            first_frame_url = await self._relay_frame_input(image_path)
            if not image_path.startswith(("http://", "https://")):
                log("首帧已上传到媒体中转")
        reference_urls: list[str] = []
        seen: set[str] = set()
        ignored = 1 if last_frame_path else 0
        for reference in references or ():
            kind = self._reference_kind(reference)
            source = self._reference_path(reference)
            if not source:
                continue
            if (
                kind != "image"
                or source in seen
                or source in {image_path, str(last_frame_path or "")}
                or len(reference_urls) >= 30
            ):
                ignored += 1
                continue
            seen.add(source)
            reference_urls.append(await self._relay_frame_input(source))
            if not source.startswith(("http://", "https://")):
                log("参考图已上传到媒体中转")
        if ignored:
            log("该渠道按公开文档只接受首帧与参考图，其余素材已忽略")
        return compile_payload(
            model_key=self.upstream_model,
            prompt=str(prompt or "").strip(),
            duration_seconds=duration,
            aspect_ratio=ratio,
            first_frame_uri=first_frame_url or None,
            reference_uris=reference_urls,
            profile=profile,
        )

    @staticmethod
    def _newapi_video_v1_resolution(value: object) -> str:
        """Normalize local resolution labels to the relay's public values."""
        normalized = str(value or "").strip().lower()
        return {
            "768p": "768",
            "768": "768",
            "1080": "1080p",
            "1080p": "1080p",
            "2k": "2K",
            "4k": "4K",
        }.get(normalized, str(value or "").strip())

    @staticmethod
    def _newapi_video_v1_role(reference: object, kind: str) -> str:
        if isinstance(reference, Mapping):
            role = str(reference.get("role") or "").strip().lower()
        else:
            role = str(getattr(reference, "role", "") or "").strip().lower()
        if "首帧" in role or "first_frame" in role or "first frame" in role:
            return "first_frame"
        if "尾帧" in role or "last_frame" in role or "last frame" in role:
            return "last_frame"
        if kind == "video":
            return "source_video"
        if kind == "audio":
            return "audio_reference"
        return "reference"

    async def _build_newapi_video_v1_payload(
        self,
        *,
        image_path: str,
        last_frame_path: str | None,
        prompt: str,
        duration: float,
        ratio: str,
        references: object,
        log: Callable[[str], None],
        mode: str = "",
    ) -> dict[str, object]:
        """Compile the documented NewAPI ``video.v1`` request shape."""
        from novelvideo.generators.video.direct_video_profiles import (
            resolve_direct_video_profile,
        )

        profile = resolve_direct_video_profile(
            self.upstream_model,
            base_url=self.base_url,
            protocol=self.protocol,
        )
        limits = profile.reference_limits
        per_kind_limit = {
            "image": max(0, int(limits.reference_images or limits.input_images or 1)),
            "video": max(0, int(limits.reference_videos)),
            "audio": max(0, int(limits.reference_audios)),
        }
        media_inputs: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        counts = {"image": 0, "video": 0, "audio": 0}
        explicit_mode = self._normalize_explicit_video_mode(mode)
        image_path, last_frame_path, references = self._filter_explicit_media_inputs(
            mode=explicit_mode,
            image_path=image_path,
            last_frame_path=last_frame_path,
            references=references,
        )

        async def add_media(path: object, kind: str, role: str) -> None:
            source = str(path or "").strip()
            if not source or kind not in counts or counts[kind] >= per_kind_limit[kind]:
                return
            marker = (kind, source)
            if marker in seen or len(media_inputs) >= 12:
                return
            if kind == "image":
                url = await self._relay_frame_input(source)
            else:
                url = await self._relay_media_input(
                    source,
                    default_ext="mp4" if kind == "video" else "mp3",
                )
            if not source.startswith(("http://", "https://")):
                log(f"{kind}参考已上传到媒体中转")
            media_inputs.append({"kind": kind, "role": role, "url": str(url)})
            counts[kind] += 1
            seen.add(marker)

        if explicit_mode == "textToVideo":
            # An explicit text-to-video selection is authoritative.  Do not
            # let a stale canvas frame or any leftover references silently
            # change the wire operation.  The media loops below stay inside
            # the non-text branch for the same reason.
            pass
        elif explicit_mode == "imageToVideo":
            first_image = image_path
            if not first_image:
                for reference in references or ():
                    kind = self._reference_kind(reference)
                    if kind == "image":
                        first_image = self._reference_path(reference)
                        if first_image:
                            break
            if first_image:
                await add_media(first_image, "image", "first_frame")
        elif image_path:
            await add_media(
                image_path,
                "image",
                "first_frame" if last_frame_path else "reference",
            )
        if explicit_mode != "textToVideo":
            if last_frame_path:
                await add_media(last_frame_path, "image", "last_frame")
            for reference in references or ():
                kind = self._reference_kind(reference)
                if kind not in {"image", "video", "audio"}:
                    continue
                path = self._reference_path(reference)
                await add_media(path, kind, self._newapi_video_v1_role(reference, kind))

        resolution_parameter_enabled = (
            self.resolution_parameter_enabled
            if self.resolution_parameter_enabled is not None
            else bool(profile.resolution or profile.supports_custom_resolution)
        )
        aspect_parameter_enabled = (
            self.aspect_ratio_parameter_enabled
            if self.aspect_ratio_parameter_enabled is not None
            else bool(profile.aspect or profile.supports_custom_aspect_ratio)
        )
        resolved_resolution = (
            profile.resolve_resolution(self.resolution)
            if resolution_parameter_enabled
            else ""
        )
        resolution = self._newapi_video_v1_resolution(resolved_resolution)
        if not media_inputs and resolution in {"2K", "4K"}:
            # H3 requires reference material for its high-resolution modes;
            # keep text-to-video usable instead of sending a request the relay
            # will reject after parsing the body.
            resolution = "768"
            log("H3 文生视频未提供参考素材，清晰度已自动降为 768")

        operation = "image_to_video" if media_inputs else "text_to_video"
        if explicit_mode == "textToVideo":
            operation = "text_to_video"
        elif explicit_mode in {
            "imageToVideo",
            "firstLastFrame",
            "allReference",
            "imageReference",
            "videoEdit",
        }:
            operation = "image_to_video"
        payload: dict[str, object] = {
            "version": "video.v1",
            "model": self.upstream_model,
            "operation": operation,
            "prompt": str(prompt or "").strip(),
        }
        if self.duration_parameter_enabled is not False:
            payload["duration_seconds"] = profile.resolve_duration(duration)
        if resolved_resolution:
            payload["resolution"] = resolution
        resolved_aspect = (
            profile.resolve_aspect_ratio(ratio)
            if aspect_parameter_enabled
            else ""
        )
        if resolved_aspect:
            payload["aspect_ratio"] = resolved_aspect
        if media_inputs:
            payload["media_inputs"] = media_inputs
        return payload

    def _resolve_allowed_aspect_ratio(self, value: str) -> str:
        """Keep direct models inside their declared aspect-ratio contract."""
        if not self.allowed_aspect_ratios or value.casefold() in {
            item.casefold() for item in self.allowed_aspect_ratios
        }:
            return value
        if self.supports_custom_aspect_ratio and re.fullmatch(
            r"^[1-9]\d{0,5}(?:\.\d{1,4})?:[1-9]\d{0,5}(?:\.\d{1,4})?$",
            value,
        ):
            return value
        if "16:9" in self.allowed_aspect_ratios:
            return "16:9"
        return self.allowed_aspect_ratios[0]

    def _duration_fidelity_error(
        self,
        *,
        body: dict,
        requested: float,
        contract_applies: bool = True,
    ) -> dict | None:
        """Reconcile the selected duration against the body about to ship.

        「只能真，不能骗人」——当编译层（档位对齐、能力转写、分支默认值）会
        改变用户选择的时长时，这里在花钱之前拦下提交，并说清楚差在哪，绝不
        再偷偷换个数发出去。
        """
        try:
            requested_value = int(round(float(requested)))
        except (TypeError, ValueError):
            return None
        wire_body: object = body
        if contract_applies:
            try:
                wire_body = self._apply_capability_contract(copy.deepcopy(body))
            except Exception:
                wire_body = body
        sent = _extract_wire_duration_seconds(wire_body)
        if sent is None:
            return None
        if abs(sent - requested_value) <= 0.01:
            return None
        sent_display: int | float = (
            int(round(sent)) if float(sent).is_integer() else round(sent, 3)
        )
        return {
            "requested": requested_value,
            "would_send": sent_display,
            "model": self.model,
            "message": (
                f"时长对账失败：请求 {requested_value}s，提交体里是 {sent_display}s"
                f"（{self.model}）。为避免与所选时长不符的出片，已拦截本次提交；"
                "请按该模型真实支持的时长档位重新选择。"
            ),
        }

    def _is_wokey_jimeng_seedance2_model(self) -> bool:
        return self.model.strip().lower().startswith("jimeng-seedance-")

    def _wokey_jimeng_resolution(self, value: str | None) -> str:
        model = self.model.strip().lower()
        requested = str(value or "").strip().lower()
        if self.supports_custom_resolution and re.fullmatch(
            r"(?:[1-9]\d{2,5}p|[1-9]\d{0,2}k|[1-9]\d{2,5}x[1-9]\d{2,5})",
            requested,
            re.IGNORECASE,
        ):
            return requested
        allowed = {
            "jimeng-seedance-2.5": {"480p", "720p"},
        }.get(model, {"720p"})
        return requested if requested in allowed else "720p"

    @staticmethod
    def _village_canvas_routed_duration(duration: float) -> int:
        requested = int(math.ceil(float(duration or 5)))
        for option in (5, 10, 15):
            if requested <= option:
                return option
        return 15

    def _is_seedance2_model(self) -> bool:
        return self.model.strip().startswith("seedance-2.0") or self.model.strip() in {
            "seedance-2.5",
            "seedance-2-5",
        }

    def _is_firefly_seedance2_model(self) -> bool:
        return self.model.strip().lower().startswith(
            ("firefly-seedance2-", "s-videos-f-933-fast-480-2")
        )

    def _is_kacang_mini_h3_model(self) -> bool:
        return self.model.strip().lower() == "mini-h3"

    @staticmethod
    def model_uses_prompt_hubs_videos_endpoint(model: str) -> bool:
        text = str(model or "").strip().lower()
        return (
            text == "village-canvas-video"
            or text.startswith("jimeng-seedance-")
            or text.startswith(
                (
                    "firefly-seedance2-",
                    "kling-3.0",
                    "kling-v3-omni-v2v-create",
                    "runway-gen4.5",
                    "veo-3.1",
                    "sora-2",
                    "sd2",
                    "sd-2",
                    "sd_2",
                )
            )
        )

    def _is_prompt_hubs_flex_video_model(self) -> bool:
        text = self.model.strip().lower()
        return text.startswith(
            (
                "kling-3.0",
                "kling-v3-omni-v2v-create",
                "runway-gen4.5",
                "veo-3.1",
                "sora-2",
            )
        )

    def _is_prompt_hubs_sd_video_model(self) -> bool:
        return self.model.strip().lower().startswith(("sd2", "sd-2", "sd_2"))

    def _is_prompt_hubs_face_lock_video_model(self) -> bool:
        """Whether this Prompt-Hubs SD route consumes an image as a locked first frame."""

        return (
            self._is_prompt_hubs_sd_video_model()
            and "face" in self.model.strip().lower()
        )

    @staticmethod
    def _prompt_hubs_flex_duration_options(model: str) -> tuple[int, ...]:
        text = str(model or "").strip().lower()
        if text == "kling-v3-omni-v2v-create":
            return tuple(range(3, 16))
        if text.startswith("kling-3.0"):
            if text in {"kling-3.0", "kling-3.0-pro"}:
                return (15,)
            if text.endswith("-omni-ref"):
                return (5, 8, 10, 15)
            return (5, 8, 10)
        if text.startswith("runway-gen4.5"):
            return (5, 10)
        if text.startswith("veo-3.1"):
            return (4, 6, 8)
        if text.startswith("sora-2"):
            return (4, 8, 12)
        return (5,)

    def _prompt_hubs_flex_duration(self, duration: float) -> int:
        options = self._prompt_hubs_flex_duration_options(self.model)
        requested = int(math.ceil(float(duration or options[0])))
        for option in options:
            if requested <= option:
                return option
        return options[-1]

    def _prompt_hubs_flex_resolution(self, value: str | None) -> str:
        text = str(value or "").strip().lower()
        model = self.model.strip().lower()
        if self.supports_custom_resolution and re.fullmatch(
            r"(?:[1-9]\d{2,5}p|[1-9]\d{0,2}k|[1-9]\d{2,5}x[1-9]\d{2,5})",
            text,
            re.IGNORECASE,
        ):
            return text
        if model.startswith("runway-gen4.5"):
            return "720p"
        if text in {"1080p", "1080"}:
            return "1080p"
        return "720p"

    def _prompt_hubs_flex_ratio(self, value: str | None) -> str:
        text = str(value or "").strip().lower()
        model = self.model.strip().lower()
        if self.supports_custom_aspect_ratio and re.fullmatch(
            r"^[1-9]\d{0,5}(?:\.\d{1,4})?:[1-9]\d{0,5}(?:\.\d{1,4})?$",
            text,
        ):
            return text
        allowed = {"16:9", "9:16", "1:1"}
        if model.startswith(("kling-3.0", "kling-v3-omni-v2v-create", "veo-3.1", "sora-2")):
            allowed = {"16:9", "9:16"}
        return text if text in allowed else "16:9"

    def _prompt_hubs_flex_reference_image_limit(self) -> int:
        model = self.model.strip().lower()
        if model == "kling-v3-omni-v2v-create":
            return 9
        if model.endswith("-ref-flex") or model.endswith("-omni-ref"):
            return 3
        return 1

    def _prompt_hubs_sd_resolution(self, value: str | None) -> str:
        text = str(value or "").strip().lower()
        model = self.model.strip().lower()
        if self.supports_custom_resolution and re.fullmatch(
            r"(?:[1-9]\d{2,5}p|[1-9]\d{0,2}k|[1-9]\d{2,5}x[1-9]\d{2,5})",
            text,
            re.IGNORECASE,
        ):
            return text
        if "1080p-4k" in model:
            return "4k" if text in {"4k", "2160p"} else "1080p"
        if "1080" in text and ("15s" in model or "满血" in model or "full" in model):
            return "1080p"
        if "480" in text and (
            "720p" in model or "15s" in model or "满血" in model or "full" in model
        ):
            return "480p"
        return "720p"

    def _prompt_hubs_sd_reference_limits(self) -> dict[str, int]:
        model = self.model.strip().lower()
        if "4img" in model:
            return {"referenceImages": 4, "referenceVideos": 3, "referenceAudios": 1}
        return {"referenceImages": 9, "referenceVideos": 3, "referenceAudios": 3}

    def _firefly_resolution(self) -> str:
        """Return the fixed resolution encoded by a Firefly model identifier."""
        return "720p" if self.model.strip().lower().endswith("-720p") else "480p"

    def _is_happyhorse_model(self) -> bool:
        return self.model.strip().lower() == "happyhorse-1.0"

    def _is_minimax_hailuo_model(self) -> bool:
        return self.model.strip().lower() == "minimax-hailuo-2.3"

    @staticmethod
    def _openai_video_size(value: str | None) -> str:
        text = str(value or "").strip().lower()
        if text in {"16:9", "9:16", "1:1"}:
            return text
        if text in {"portrait", "vertical", "竖屏"}:
            return "9:16"
        if text in {"square", "正方形"}:
            return "1:1"
        return "16:9"

    @staticmethod
    def _firefly_ratio(value: str | None) -> str:
        text = str(value or "").strip().lower()
        return text if text in {"16:9", "9:16", "1:1"} else "16:9"

    def _wokey_jimeng_ratio(self, value: str | None) -> str:
        text = str(value or "").strip().lower()
        if self.supports_custom_aspect_ratio and re.fullmatch(
            r"^[1-9]\d{0,5}(?:\.\d{1,4})?:[1-9]\d{0,5}(?:\.\d{1,4})?$",
            text,
        ):
            return text
        allowed = {"16:9", "9:16", "1:1", "4:3", "3:4", "21:9"}
        return text if text in allowed else "16:9"

    @staticmethod
    def _coerce_bool(value: object, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _minimax_hailuo_resolution(value: str | None) -> str:
        return "1080P" if "1080" in str(value or "").lower() else "768P"

    def _is_grok_video_channel_model(self) -> bool:
        return self.model.strip().lower() == "grok-video-channel"

    @staticmethod
    def _happyhorse_ratio(value: str | None) -> str:
        text = str(value or "").strip()
        return text if text in {"16:9", "9:16", "1:1", "4:3", "3:4"} else "16:9"

    @staticmethod
    def _happyhorse_resolution(value: str | None) -> str:
        text = str(value or "").strip().lower()
        if "720" in text:
            return "720P"
        return "1080P"

    @staticmethod
    def _happyhorse_audio_setting(value: str | None) -> str:
        text = str(value or "").strip().lower()
        return text if text in {"auto", "origin"} else "auto"

    async def _relay_seedance2_references(
        self,
        references: list["ShotReference"] | None,
        *,
        log: Callable[[str], None],
    ) -> dict[str, list[str]]:
        reference_urls: dict[str, list[str]] = {}
        for ref in references or []:
            ref_type = str(getattr(ref, "type", "") or "image").strip().lower()
            path = str(getattr(ref, "path", "") or "").strip()
            if not path:
                continue
            if ref_type == "image":
                key = "reference_images"
                default_ext = "png"
                label = "图片参考"
                image_transform = IMAGE_TRANSFORM_AI_REFERENCE_JPEG
            elif ref_type == "video":
                key = "reference_videos"
                default_ext = "mp4"
                label = "视频参考"
                image_transform = None
            elif ref_type == "audio":
                key = "reference_audios"
                default_ext = "mp3"
                label = "音频参考"
                image_transform = None
            else:
                continue
            url = await self._relay_media_input(
                path,
                default_ext=default_ext,
                image_transform=image_transform,
            )
            if not path.startswith(("http://", "https://")):
                log(f"{label}已上传到媒体中转")
            reference_urls.setdefault(key, []).append(url)
        return reference_urls

    @staticmethod
    def _extract_video_url(task: dict) -> str:
        if not isinstance(task, dict):
            return ""
        for container_key in ("metadata", "video", "result", "output", "data"):
            container = task.get(container_key)
            if not isinstance(container, dict):
                continue
            for key in ("url", "video_url", "result_url", "download_url"):
                value = container.get(key)
                if isinstance(value, str) and value:
                    return value
        for key in ("url", "video_url", "result_url", "download_url"):
            value = task.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    def _submit_url(self, base_url: str | None = None) -> str:
        return self._submit_url_for(str(base_url or self.base_url), self.create_path)

    def _submit_route_paths(self, base_url: str | None = None) -> tuple[str, ...]:
        """Return this gateway's create routes in the order they should be tried.

        A route explicitly documented by this upstream outranks an older
        measured route. Otherwise, a previously accepted route outranks the
        protocol default so future submits avoid a refused hop.
        """

        primary = str(self.create_path or "").strip()
        declared = [
            str(path).strip()
            for path in self.protocol_contract.ordered_submit_routes
            if str(path).strip()
        ]
        observed = ""
        cached: dict[str, Any] = {}
        if self.cache_runtime_contract:
            from novelvideo.generators.video.direct_video_capability_cache import (
                get_cached_capability_for_model,
            )

            try:
                cached = get_cached_capability_for_model(
                    base_url=str(base_url or self.base_url),
                    upstream_model=self.upstream_model,
                    protocol=self.protocol,
                )
            except (OSError, ValueError):
                cached = {}
            transport = cached.get("transportContract")
            if isinstance(transport, dict) and transport.get("submitObserved"):
                observed = str(transport.get("submit") or "").strip()
        documented = str(cached.get("openapiSubmitPath") or "").strip()
        ordered = [path for path in (documented, observed, primary, *declared) if path]
        return tuple(dict.fromkeys(ordered))

    def _submit_url_for(self, base_url: str, route_path: str) -> str:
        return direct_video_submit_url(
            str(base_url or self.base_url),
            self.protocol_contract,
            create_path=route_path,
        )

    def _follow_accepted_route(self, submit_path: str) -> None:
        """Keep this generator's primary route on the one the gateway accepted.

        The polling leg keeps using ``query_path_template``, which the relay
        answered on both route families, so only the create route needs to move.
        """
        path = "/" + str(submit_path or "").strip().strip("/")
        if path != "/" and path != self.create_path:
            self.create_path = path

    def _query_url(self, task_id: str, base_url: str | None = None) -> str:
        if self.query_path_template == self.protocol_contract.query_path_template:
            return direct_video_query_url(
                str(base_url or self.base_url),
                self.protocol_contract,
                task_id,
            )
        path = self.query_path_template.format(task_id=str(task_id).strip())
        return (
            f"{protocol_base_url(str(base_url or self.base_url), self.protocol_contract)}"
            f"/{path.strip('/')}"
        )

    def _task_id_from_response(self, payload: object) -> str:
        return extract_task_id(payload, self.protocol_contract)

    def _task_status(self, payload: object) -> str:
        return extract_task_status(payload, self.protocol_contract)

    def _task_result_url(self, payload: object) -> str:
        return extract_result_url(payload, self.protocol_contract) or self._extract_video_url(
            payload if isinstance(payload, dict) else {}
        )

    def _task_error(self, payload: object) -> str:
        return extract_task_error(payload, self.protocol_contract)

    @staticmethod
    def _task_response_keys(payload: object) -> list[str]:
        if not isinstance(payload, dict):
            return []
        return sorted(str(key) for key in payload)[:24]

    def _task_failure_message(
        self,
        payload: object,
        *,
        status: str,
        task_id: str,
    ) -> str:
        error = self._task_error(payload)
        if error:
            return error
        model = str(getattr(self, "upstream_model", "") or self.model).strip()
        return (
            "上游视频任务失败，但渠道没有返回失败原因。"
            f"状态：{status or 'failed'}；模型：{model or 'unknown'}；"
            f"任务 ID：{task_id or 'unknown'}。"
            "请在模型中心重新检测该渠道的任务查询协议。"
        )

    def _task_diagnostic_contract(
        self,
        *,
        error_code: str,
        suggested_action: str,
        retryable: bool,
        verification_stage: str = "poll",
        response_keys: list[str] | None = None,
    ) -> dict[str, object]:
        """Build a credential-free task-center contract for poll failures."""

        request_contract: dict[str, object] = {}
        if response_keys:
            request_contract["response_keys"] = [str(key)[:80] for key in response_keys[:24]]
        result: dict[str, object] = {
            "error_code": error_code,
            "endpoint_class": "video-task-query",
            "stage": "query",
            "verification_stage": verification_stage,
            "retryable": bool(retryable),
            "suggested_action": suggested_action,
        }
        if request_contract:
            result["request_contract"] = request_contract
        return result

    @staticmethod
    def _extract_returned_last_frame_url(task: dict) -> str:
        if not isinstance(task, dict):
            return ""
        from novelvideo.generators.huimengi import extract_huimeng_result_last_frame_url

        containers: list[dict] = []
        for value in (
            task.get("metadata"),
            task.get("response"),
            task.get("result"),
            task.get("output"),
            task.get("data"),
            task,
        ):
            if not isinstance(value, dict):
                continue
            containers.append(value)
            for nested_key in ("result", "output", "data", "response", "metadata"):
                nested = value.get(nested_key)
                if isinstance(nested, dict):
                    containers.append(nested)
        seen: set[int] = set()
        for container in containers:
            ident = id(container)
            if ident in seen:
                continue
            seen.add(ident)
            found = extract_huimeng_result_last_frame_url(container)
            if found:
                return found
        return ""

    @staticmethod
    def _extract_provider_task_id(task: dict, *, fallback: str = "") -> str:
        if not isinstance(task, dict):
            return fallback
        containers = [
            task.get("metadata"),
            task.get("response"),
            task.get("result"),
            task.get("output"),
            task.get("data"),
            task,
        ]
        keys = (
            "provider_task_id",
            "huimeng_task_id",
            "upstream_task_id",
            "upstream_id",
            "task_id",
        )
        for container in containers:
            if not isinstance(container, dict):
                continue
            for key in keys:
                value = container.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return fallback

    @classmethod
    def _returned_last_frame_output_path(
        cls, output_path: str, last_frame_url: str
    ) -> Path:
        video_output_path = Path(output_path)
        suffix = ""
        if last_frame_url.startswith("data:"):
            header, _, _encoded = last_frame_url.partition(",")
            suffix = f".{cls._ext_from_data_url_header(header, default='png')}"
        else:
            parsed_url = urllib.parse.urlparse(last_frame_url)
            suffix = Path(parsed_url.path).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            suffix = ".png"
        return (
            video_output_path.parent
            / "returned_last_frames"
            / f"{video_output_path.stem}{suffix}"
        )

