"""Runtime adapter for discovered non-OpenAI video protocol families.

The adapter deliberately consumes only the bounded contract emitted by the
zero-billing probe.  It keeps provider-specific lifecycle differences out of
the canvas and gives prediction/queue/operation/workflow gateways the same
submit -> poll -> artifact contract as the existing OpenAI video path.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Callable, Iterable, Mapping
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit

import aiohttp

from novelvideo.shared.provider_cost import extract_provider_cost_evidence
from novelvideo.storage.media_relay import (
    IMAGE_TRANSFORM_AI_REFERENCE_JPEG,
    MediaRelayConfigError,
    upload_media_bytes,
)
from novelvideo.utils.error_redaction import redact_secrets

from .video_provider_adapters import (
    VideoEndpointContract,
    VideoProtocolFamily,
    get_video_adapter_contract,
    normalize_video_protocol_family,
)
from .video_capability_envelope import (
    apply_video_common_parameter_mapping,
    canonical_parameter_key,
    compile_video_provider_parameters,
)
from .runtime_contract import is_generic_video_adapter


_INTERNAL_CONSTRUCTOR_KEYS = frozenset(
    {
        # Capability gates and runtime plumbing belong to the local adapter
        # contract. They must never be emitted as provider parameters unless
        # the discovery envelope explicitly carries them in ``parameter_values``.
        "resolution",
        "generate_audio",
        "duration_parameter_enabled",
        "aspect_ratio_parameter_enabled",
        "resolution_parameter_enabled",
        "cache_runtime_contract",
        "preserve_upstream_model",
        "allow_result_gateway_fallback",
        "capability_parameters",
        "capability_opaque",
        "workflow_input_rules",
        "resolution_mappings",
        "media_inputs",
        "mediaInputs",
        "size_slots",
        "size_field",
    }
)


# Canvas-facing mode labels are intentionally normalized at the generic
# adapter boundary.  A discovered provider may expose the same semantic mode
# as snake_case, camelCase, or a short alias; the provider-specific spelling is
# supplied by ``provider_mapping`` when one is known.
_VIDEO_MODE_ALIASES: dict[str, str] = {
    "texttovideo": "text_to_video",
    "text_to_video": "text_to_video",
    "t2v": "text_to_video",
    "imagetovideo": "image_to_video",
    "image_to_video": "image_to_video",
    "i2v": "image_to_video",
    "firstframe": "image_to_video",
    "first_frame": "image_to_video",
    "firstlastframe": "first_last_frame",
    "first_last_frame": "first_last_frame",
    "first_last": "first_last_frame",
    "keyframe": "first_last_frame",
    "flf": "first_last_frame",
    "allreference": "reference_to_video",
    "all_reference": "reference_to_video",
    "imagereference": "reference_to_video",
    "image_reference": "reference_to_video",
    "reference_to_video": "reference_to_video",
    "multimodalreference": "reference_to_video",
    "multimodal_reference": "reference_to_video",
    "videoedit": "reference_to_video",
    "video_edit": "reference_to_video",
}

_FRAME_ROLE_ALIASES: dict[str, str] = {
    "首帧": "firstFrame",
    "first": "firstFrame",
    "firstframe": "firstFrame",
    "first_frame": "firstFrame",
    "first-frame": "firstFrame",
    "尾帧": "lastFrame",
    "last": "lastFrame",
    "lastframe": "lastFrame",
    "last_frame": "lastFrame",
    "last-frame": "lastFrame",
}

_MAX_RESULT_REDIRECTS = 3


def _url_origin(value: str) -> tuple[str, str, int] | None:
    """Return the normalized HTTP origin, including the effective port."""

    try:
        parsed = urlsplit(str(value or "").strip())
        scheme = parsed.scheme.casefold()
        hostname = parsed.hostname
        if scheme not in {"http", "https"} or not hostname:
            return None
        port = parsed.port
    except ValueError:
        return None
    return (
        scheme,
        hostname.casefold(),
        port if port is not None else (443 if scheme == "https" else 80),
    )


def _normalize_video_mode(value: object) -> str:
    """Normalize canvas/provider aliases to the shared video mode vocabulary."""
    raw = str(getattr(value, "value", value) or "").strip()
    if not raw:
        return ""
    token = raw.casefold().replace("-", "_").replace(" ", "")
    return _VIDEO_MODE_ALIASES.get(token, token)


def _normalize_frame_role(value: object) -> str:
    raw = str(getattr(value, "value", value) or "").strip()
    if not raw:
        return "reference"
    token = raw.casefold().replace(" ", "_")
    return _FRAME_ROLE_ALIASES.get(token, "reference")


def _reference_field(value: object, name: str, default: object = "") -> object:
    """Read reference metadata from both dataclass and mapping-shaped inputs."""

    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _reference_kind(value: object) -> str:
    """Normalize the media kind used by the explicit mode contract."""

    raw = _reference_field(value, "type", None)
    if raw in (None, ""):
        raw = _reference_field(value, "kind", "image")
    raw = getattr(raw, "value", raw)
    kind = str(raw or "image").strip().casefold()
    return kind if kind in {"image", "video", "audio"} else "image"


def _reference_path(value: object) -> str:
    """Return a reference URI/path without coercing metadata objects to text."""

    if isinstance(value, (str, Path)):
        return str(value).strip()
    for name in ("path", "url", "uri"):
        candidate = _reference_field(value, name, "")
        if candidate not in (None, ""):
            text = str(candidate).strip()
            if text:
                return text
    return ""


#: How many values ``_build_payload`` can actually place into ``ref_*_N``.
#: The payload builder enumerates the encoded media lists positionally, so a
#: declared slot beyond these counts can never be filled by any caller.
_MEDIA_SLOT_FILL_LIMITS = {"image": 9, "audio": 3}

#: Violation codes whose cause is runnable by the person who owns the node.
#: Everything else keeps the generic capability-contract wording.
_ACTIONABLE_VIOLATION_HINTS = {
    "native_audio_unsupported": "该模型不支持音轨输出，请先关闭「生成声音」。",
    "native_audio_required": "该模型必须生成音轨，请先在节点上开启「生成声音」。",
    "required_audio_slot_missing": (
        "该工作流要求逐个填满音频槽位，请给节点连上足够的参考音频（含台词/驱动音频）。"
    ),
    "required_image_slot_missing": "该工作流要求逐个填满图片槽位，请给节点连上足够的参考图。",
    "required_first_frame_missing": "该模式需要首帧，请先给节点连上一张图片。",
    "required_first_last_frames_missing": "该模式需要首尾两帧，请给节点连上首帧与尾帧。",
    "required_image_reference_missing": "该模式需要参考图，请给节点连上至少一张图片。",
    "required_video_reference_missing": "该模式需要参考视频，请给节点连上至少一段视频。",
    "unsupported_video_mode": "该模式不在当前模型合同里，请改选节点已声明的模式。",
    "unsupported_resolution": "该分辨率不在当前模型合同里，请改选节点上列出的分辨率。",
    "unsupported_duration": "该时长不在当前模型合同里，请改成节点允许的秒数。",
    "unsupported_aspect_ratio": "该画幅不在当前模型合同里，请改选节点上列出的画幅。",
}


def _contract_violation_message(violations: Iterable[Mapping[str, object]]) -> str:
    """Report the first runnable cause instead of a bare contract dismissal.

    A rejected request already carries machine-readable violations. When one of
    them names something the caller can fix on the node, lead the message with
    that guidance; keeping the counts in ``violations`` preserves the contract
    detail the task record and the error card both render.
    """

    for violation in violations or ():
        if not isinstance(violation, Mapping):
            continue
        hint = _ACTIONABLE_VIOLATION_HINTS.get(str(violation.get("code") or ""))
        if hint:
            return f"视频请求不符合已声明的模型能力合同：{hint}"
    return "视频请求不符合已声明的模型能力合同"


def _media_slot_index(slot: Mapping[str, object], *, kind: str) -> int | None:
    """Return N for a discovered ``ref_<kind>_N`` transport slot."""

    prefix = f"ref_{kind}_"
    for key in ("key", "providerKey", "provider_key"):
        raw = str(slot.get(key) or "").strip()
        if not raw.casefold().startswith(prefix):
            continue
        suffix = raw[len(prefix) :]
        if suffix.isdigit():
            return int(suffix)
    return None


def _unfilled_required_media_slots(
    slots: Iterable[Mapping[str, object]],
    *,
    kind: str,
    supplied: int,
) -> tuple[str, ...]:
    """Name the ``required`` slots of one kind this request cannot satisfy.

    Discovery marks ``ref_audio_0`` required for workflows whose first audio
    input has no fallback. The request only carries ``min(supplied, cap)``
    positional values, so a slot above that is a guaranteed upstream
    rejection — worth reporting before a paid submit round trip.
    """
    cap = _MEDIA_SLOT_FILL_LIMITS.get(kind, 0)
    fillable = min(max(0, int(supplied)), cap)
    missing: list[str] = []
    for slot in slots:
        if not isinstance(slot, Mapping):
            continue
        if not bool(slot.get("required")):
            continue
        slot_type = str(slot.get("type") or "").strip().casefold()
        if slot_type and slot_type != kind:
            continue
        index = _media_slot_index(slot, kind=kind)
        if index is None or index < fillable:
            continue
        name = str(slot.get("key") or slot.get("providerKey") or "").strip()
        if name and name not in missing:
            missing.append(name)
    return tuple(missing)


class GenericVideoAdapterError(RuntimeError):
    """Credential-free error with lifecycle stage and diagnostic metadata."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        error_code: str,
        http_status: int | None = None,
        retryable: bool = False,
        request_contract: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.error_code = error_code
        self.http_status = http_status
        self.retryable = retryable
        self.request_contract = dict(request_contract or {})

    @property
    def provider_error_metadata(self) -> dict[str, object]:
        endpoint_class = (
            "video-result-download"
            if self.stage in {"artifact", "download"}
            else f"video-{self.stage}"
        )
        result: dict[str, object] = {
            "error_code": self.error_code,
            "endpoint_class": endpoint_class,
            "stage": self.stage,
            "verification_stage": (
                "artifact" if self.stage == "download" else "poll" if self.stage == "query" else "submit"
            ),
            "retryable": self.retryable,
            "suggested_action": self._suggested_action(),
        }
        if self.http_status is not None:
            result["http_status"] = self.http_status
        if self.request_contract:
            result["request_contract"] = self.request_contract
        return result

    def _suggested_action(self) -> str:
        if self.error_code == "VIDEO_ADAPTER_PROTOCOL_UNRESOLVED":
            return "重新检测上游 OpenAPI/协议族，确认提交、查询和结果字段后再生成。"
        if self.error_code == "VIDEO_TASK_ID_MISSING":
            return "检查提交响应的任务 ID 字段映射并重新检测该渠道。"
        if self.error_code == "VIDEO_UPSTREAM_REQUEST_REJECTED":
            return "按 AutoDL 工作流 API 的输入字段、媒体 URL 和分辨率合同修正后重试。"
        if self.error_code == "VIDEO_STATUS_MISSING":
            return "检查任务查询响应的状态字段映射并重新检测该渠道。"
        if self.error_code == "VIDEO_RESULT_URL_MISSING":
            return "检查完成响应的结果 URL/输出字段映射并重新检测该渠道。"
        if self.stage == "download":
            return "检查结果 URL、统一内容端点和结果网关的响应类型。"
        if self.http_status in {401, 403}:
            return "检查该协议的 URL、Key 和上游账号权限。"
        return "检查该协议的 OpenAPI 合同和上游返回字段后重试。"


class GenericVideoAdapterGenerator:
    """Protocol-neutral async video generator for discovered adapter families."""

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str,
        model: str,
        adapter_family: str,
        adapter_operations: Iterable[Mapping[str, object]] = (),
        allowed_durations: Iterable[int] = (),
        allowed_resolutions: Iterable[str] = (),
        allowed_aspect_ratios: Iterable[str] = (),
        size_slots: Iterable[str] = (),
        size_field: str = "size",
        supports_custom_aspect_ratio: bool = False,
        supports_custom_resolution: bool = False,
        supported_modes: Iterable[str] = (),
        reference_limits: Mapping[str, int] | None = None,
        native_audio: str = "optional",
        audio_input_semantics: Iterable[str] = (),
        media_inputs: Iterable[Mapping[str, object]] = (),
        parameter_values: Mapping[str, object] | None = None,
        provider_mapping: Mapping[str, object] | None = None,
        workflow_input_rules: Iterable[Mapping[str, object]] = (),
        resolution_mappings: Iterable[Mapping[str, object]] = (),
        poll_interval: float = 5.0,
        max_polls: int = 720,
        **_kwargs: object,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.base_url = str(endpoint or "").strip().rstrip("/")
        self.model = str(model or "").strip()
        self.family = normalize_video_protocol_family(adapter_family)
        if not is_generic_video_adapter(self.family):
            raise ValueError(f"generic adapter requires a generic protocol family: {adapter_family}")
        self.contract: VideoEndpointContract = get_video_adapter_contract(self.family)
        self.adapter_operations = tuple(
            dict(item) for item in adapter_operations if isinstance(item, Mapping)
        )
        self.allowed_durations = tuple(sorted({int(item) for item in allowed_durations if int(item) > 0}))
        self.allowed_resolutions = tuple(
            str(item).strip().casefold() for item in allowed_resolutions if str(item).strip()
        )
        self.allowed_aspect_ratios = tuple(str(item).strip() for item in allowed_aspect_ratios if str(item).strip())
        self.size_slots = tuple(
            str(item).strip().lower()
            for item in size_slots
            if str(item).strip()
        )
        self.size_field = str(size_field or "size").strip() or "size"
        self.supports_custom_aspect_ratio = bool(supports_custom_aspect_ratio)
        self.supports_custom_resolution = bool(supports_custom_resolution)
        self.supported_modes = tuple(
            str(getattr(item, "value", item)).strip()
            for item in supported_modes
            if str(getattr(item, "value", item)).strip()
        )
        self.reference_limits = {
            str(key): max(0, int(value))
            for key, value in (reference_limits or {}).items()
            if str(key).strip()
        }
        self.native_audio = str(native_audio or "optional").strip().lower()
        # Discovered media slots describe the provider's own transport fields
        # (``ref_audio_0`` …). They are the only evidence that a workflow marks
        # a slot ``required``, so the preflight needs them verbatim.
        self.media_inputs = tuple(
            dict(item) for item in media_inputs if isinstance(item, Mapping)
        )
        self.audio_input_semantics = tuple(
            str(item).strip().casefold().replace("-", "_")
            for item in audio_input_semantics
            if str(item).strip()
        )
        constructor_extras = {
            str(key): value
            for key, value in _kwargs.items()
            if str(key) not in _INTERNAL_CONSTRUCTOR_KEYS
        }
        self.parameter_values = {
            **constructor_extras,
            **(
                {str(key): value for key, value in parameter_values.items()}
                if isinstance(parameter_values, Mapping)
                else {}
            ),
        }
        self.provider_mapping = {
            str(key): str(value)
            for key, value in (provider_mapping or {}).items()
            if str(key).strip() and str(value).strip()
        }
        self.workflow_input_rules = tuple(
            dict(item)
            for item in workflow_input_rules
            if isinstance(item, Mapping)
        )
        self.resolution_mappings = tuple(
            dict(item)
            for item in resolution_mappings
            if isinstance(item, Mapping)
        )
        self.poll_interval = max(0.1, min(float(poll_interval), 60.0))
        self.max_polls = max(1, min(int(max_polls), 720))

    @property
    def protocol(self) -> str:
        return self.family.value

    def _headers(self) -> dict[str, str]:
        authorization = (
            self.api_key
            if self.family is VideoProtocolFamily.AUTODL_COMFYUI
            else f"Bearer {self.api_key}"
        )
        return {
            "Authorization": authorization,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _submit_url(self) -> str:
        path = self._operation_path("submit") or self.contract.submit_path
        return self._join_operation_path(path)

    def _query_url(self, task_id: str) -> str:
        path = self._operation_path("query") or self.contract.query_path_template
        return self._join_operation_path(path, task_id=task_id)

    def _operation_path(self, stage: str) -> str:
        candidates: list[str] = []
        for operation in self.adapter_operations:
            path = str(operation.get("path") or "").strip()
            method = str(operation.get("method") or "").strip().lower()
            if not path:
                continue
            lower = path.lower()
            if stage == "submit" and method == "post" and any(
                marker in lower for marker in ("predict", "prediction", "queue", "prompt", "video")
            ):
                candidates.append(path)
            elif stage == "query" and method == "get" and any(
                marker in lower for marker in ("status", "history", "operation", "prediction", "video")
            ):
                candidates.append(path)
        return candidates[0] if candidates else ""

    def _join_operation_path(self, path: str, *, task_id: str = "") -> str:
        resolved = (
            str(path or "")
            .strip()
            .replace("{model}", self.model)
            .replace("{workflow_id}", self.model)
        )
        resolved = resolved.replace("{task_id}", quote(str(task_id), safe=""))
        resolved = resolved.replace("{request_id}", quote(str(task_id), safe=""))
        resolved = resolved.replace("{id}", quote(str(task_id), safe=""))
        if resolved.startswith("http://") or resolved.startswith("https://"):
            return resolved
        base_url = self.base_url
        if self.family is VideoProtocolFamily.AUTODL_COMFYUI:
            parsed = urlsplit(base_url)
            if parsed.path.rstrip("/").lower() in {"/api", "/api/v1"}:
                base_url = urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
        return f"{base_url}/{resolved.lstrip('/')}"

    async def generate(
        self,
        *,
        image_path: str | None = None,
        prompt: str,
        output_path: str,
        aspect_ratio: str = "16:9",
        duration: float = 5.0,
        on_log: Callable[[str], None] | None = None,
        on_progress: Callable[[float], None] | None = None,
        on_task_event: Callable[[dict[str, object]], None] | None = None,
        references: Iterable[object] = (),
        generate_audio: bool = False,
        resolution: str = "720p",
        last_frame_path: str | None = None,
        mode: str | None = None,
        gen_mode: str | None = None,
        **kwargs: object,
    ):
        from novelvideo.generators.video_generator import VideoGenResult, VideoGenStatus

        reference_values = tuple(references or ())
        requested_mode = mode or gen_mode or kwargs.get("gen_mode") or kwargs.get("mode")
        raw_mode = str(getattr(requested_mode, "value", requested_mode) or "").strip()
        normalized_mode = _normalize_video_mode(raw_mode)
        declared_mode = any(
            raw_mode.casefold() == str(item).strip().casefold()
            for item in self.supported_modes
        )
        if raw_mode and normalized_mode not in {
            "text_to_video",
            "image_to_video",
            "first_last_frame",
            "reference_to_video",
        } and not declared_mode:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=f"视频模式 {raw_mode!r} 不在模型能力合同中",
                error_metadata={
                    "error_code": "VIDEO_CAPABILITY_CONTRACT_INVALID",
                    "endpoint_class": "video-request-contract",
                    "stage": "preflight",
                    "verification_stage": "contract",
                    "retryable": False,
                    "suggested_action": "按模型能力合同选择已声明的视频模式后重试。",
                    "request_contract": {
                        "violations": [
                            {
                                "code": "unknown_video_mode",
                                "details": {"requestedMode": raw_mode},
                            }
                        ]
                    },
                },
            )
        wire_mode, canonical_mode = self._resolve_mode(requested_mode)
        filtered_image_path, filtered_last_frame_path, filtered_references = (
            self._filter_media_for_mode(
                requested_mode,
                canonical_mode=canonical_mode,
                image_path=image_path,
                last_frame_path=last_frame_path,
                references=reference_values,
            )
        )
        capability_error = self._validate_generation_contract(
            duration=duration,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            generate_audio=generate_audio,
            image_path=filtered_image_path,
            last_frame_path=filtered_last_frame_path,
            references=filtered_references,
            mode=requested_mode,
            _media_filtered=True,
        )
        if capability_error is not None:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=capability_error["message"],
                error_metadata={
                    "error_code": "VIDEO_CAPABILITY_CONTRACT_INVALID",
                    "endpoint_class": "video-request-contract",
                    "stage": "preflight",
                    "verification_stage": "contract",
                    "retryable": False,
                    "suggested_action": "按模型能力合同修正视频参数后重试。",
                    "request_contract": {"violations": capability_error["violations"]},
                },
            )

        def emit(stage: str, **details: object) -> None:
            if on_task_event:
                try:
                    on_task_event(
                        {"stage": stage, "model": self.model, "protocol": self.protocol, **details}
                    )
                except Exception:
                    pass

        def log(message: str) -> None:
            if on_log:
                on_log(message)

        def progress(value: float) -> None:
            if on_progress:
                on_progress(max(0.0, min(1.0, float(value))))

        idempotency_key = str(kwargs.get("idempotency_key") or uuid.uuid4().hex).strip()
        from novelvideo.generators.video_generator import (
            _confirm_video_model_call,
            _refund_video_model_call,
            _reserve_video_model_call,
        )

        reservation_id = await _reserve_video_model_call(
            self.model,
            source=f"generic_video_{self.family.value}",
            resolution=resolution,
            duration_seconds=duration,
        )
        try:
            payload = await self._build_payload(
                prompt=prompt,
                image_path=filtered_image_path,
                references=filtered_references,
                last_frame_path=filtered_last_frame_path,
                duration=duration,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                generate_audio=generate_audio,
                kwargs=kwargs,
                mode=wire_mode,
                _media_filtered=True,
            )
            submitted = await self._request_json(
                "POST",
                self._submit_url(),
                payload=payload,
                stage="submit",
                headers={"Idempotency-Key": idempotency_key},
            )
            task_id = self.contract.extract_task_id(submitted)
            immediate_result = self.contract.extract_result(submitted)
            if not task_id and self._find_result_url(immediate_result):
                task = submitted
            elif not task_id:
                response_contract = self._response_contract(submitted)
                if self._response_indicates_rejection(submitted):
                    detail = str(
                        response_contract.get("response_message")
                        or response_contract.get("response_code")
                        or "上游未接受该请求"
                    )
                    raise GenericVideoAdapterError(
                        f"视频提交被 AutoDL 拒绝：{detail}",
                        stage="submit",
                        error_code="VIDEO_UPSTREAM_REQUEST_REJECTED",
                        request_contract=response_contract,
                    )
                raise GenericVideoAdapterError(
                    "视频提交响应缺少任务 ID",
                    stage="submit",
                    error_code="VIDEO_TASK_ID_MISSING",
                    request_contract=response_contract,
                )
            else:
                emit("submitted", provider_task_id=task_id)
                task = await self._poll(
                    task_id,
                    emit=emit,
                    log=log,
                    progress=progress,
                )
            provider_cost_fields = extract_provider_cost_evidence(
                task,
                prefix="result",
            ).as_event_fields()
            if provider_cost_fields:
                emit(
                    "provider_cost",
                    provider_task_id=task_id,
                    **provider_cost_fields,
                )
            result_url = self._find_result_url(self.contract.extract_result(task))
            if not result_url and self.family is VideoProtocolFamily.WORKFLOW:
                result_url = self._workflow_result_url(task)
            if not result_url:
                raise GenericVideoAdapterError(
                    "视频任务完成但响应缺少结果 URL",
                    stage="artifact",
                    error_code="VIDEO_RESULT_URL_MISSING",
                    request_contract=self._response_contract(task),
                )
            emit("upstream_completed", provider_task_id=task_id, preview_url=result_url)
            await self._download(result_url, output_path)
            if not Path(output_path).is_file() or Path(output_path).stat().st_size <= 0:
                raise GenericVideoAdapterError(
                    "视频结果未写入有效本地文件",
                    stage="download",
                    error_code="VIDEO_RESULT_DOWNLOAD_FAILED",
                )
            emit("downloaded", provider_task_id=task_id)
            progress(1.0)
            await _confirm_video_model_call(
                model=self.model,
                reservation_id=reservation_id,
                provider_task_id=task_id,
            )
            return VideoGenResult(
                status=VideoGenStatus.DONE,
                video_url=result_url,
                video_path=output_path,
                task_id=task_id or None,
                provider_task_id=task_id or None,
                duration_seconds=float(duration),
            )
        except GenericVideoAdapterError as exc:
            await _refund_video_model_call(
                reservation_id,
                source=f"generic_video_{self.family.value}",
                error=str(exc),
            )
            emit(
                "upstream_failed",
                error_code=exc.error_code,
                endpoint_class=exc.provider_error_metadata["endpoint_class"],
                verification_stage=exc.stage,
                retryable=exc.retryable,
                request_contract=exc.request_contract,
            )
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=str(exc),
                error_metadata=exc.provider_error_metadata,
            )

    def _validate_generation_contract(
        self,
        *,
        duration: float,
        aspect_ratio: str,
        resolution: str,
        generate_audio: bool,
        image_path: str | None,
        last_frame_path: str | None,
        references: Iterable[object],
        mode: object = None,
        _media_filtered: bool = False,
    ) -> dict[str, object] | None:
        """Reject direct-model requests before reservation or upstream submit."""
        if not _media_filtered:
            _, canonical_mode = self._resolve_mode(mode)
            image_path, last_frame_path, references = self._filter_media_for_mode(
                mode,
                canonical_mode=canonical_mode,
                image_path=image_path,
                last_frame_path=last_frame_path,
                references=tuple(references or ()),
            )
        if not (
            self.allowed_durations
            or self.allowed_aspect_ratios
            or self.reference_limits
            or self.media_inputs
            or self.native_audio != "optional"
            or (mode is not None and str(mode).strip() and self.supported_modes)
        ):
            return None
        violations: list[dict[str, object]] = []

        def add_violation(code: str, **details: object) -> None:
            violations.append({"code": code, "details": details})

        duration_value = int(round(float(duration)))
        if self.allowed_durations and duration_value not in self.allowed_durations:
            add_violation(
                "unsupported_duration",
                requestedDuration=duration_value,
                supportedDurations=list(self.allowed_durations),
            )
        normalized_resolution = str(resolution).strip().casefold().replace("×", "x")
        custom_resolution = bool(
            self.supports_custom_resolution
            and re.fullmatch(
                r"(?:[1-9]\d{2,5}p|[1-9]\d{0,2}k|[1-9]\d{2,5}x[1-9]\d{2,5})",
                normalized_resolution,
                re.IGNORECASE,
            )
        )
        if (
            self.allowed_resolutions
            and normalized_resolution not in self.allowed_resolutions
            and not custom_resolution
        ):
            add_violation(
                "unsupported_resolution",
                requestedResolution=str(resolution),
                supportedResolutions=list(self.allowed_resolutions),
            )
        normalized_aspect = str(aspect_ratio).strip()
        custom_aspect = bool(
            self.supports_custom_aspect_ratio
            and re.fullmatch(
                r"^[1-9]\d{0,5}(?:\.\d{1,4})?:[1-9]\d{0,5}(?:\.\d{1,4})?$",
                normalized_aspect,
            )
        )
        if (
            self.allowed_aspect_ratios
            and normalized_aspect.casefold()
            not in {item.casefold() for item in self.allowed_aspect_ratios}
            and not custom_aspect
        ):
            add_violation(
                "unsupported_aspect_ratio",
                requestedAspectRatio=str(aspect_ratio),
                supportedAspectRatios=list(self.allowed_aspect_ratios),
            )
        if self.native_audio == "unsupported" and generate_audio:
            add_violation("native_audio_unsupported", requestedNativeAudio=True)
        if self.native_audio == "required" and not generate_audio:
            add_violation("native_audio_required", requestedNativeAudio=False)
        supplied_media = {"image": 0, "audio": 0}
        if image_path:
            supplied_media["image"] += 1
        if last_frame_path:
            supplied_media["image"] += 1
        for value in references:
            kind = _reference_kind(value)
            if kind in supplied_media and _reference_path(value):
                supplied_media[kind] += 1
        # Only the two kinds ``_build_autodl_payload`` writes positionally are
        # checked. A required ``ref_video`` slot is a real gap, but the AutoDL
        # payload builder never writes video slots at all, so gating on it here
        # would block requests that some other family handles correctly.
        for kind in ("image", "audio"):
            unfilled = _unfilled_required_media_slots(
                self.media_inputs,
                kind=kind,
                supplied=supplied_media[kind],
            )
            if unfilled:
                add_violation(
                    f"required_{kind}_slot_missing",
                    missingSlots=list(unfilled),
                    suppliedCount=supplied_media[kind],
                    fillableSlots=_MEDIA_SLOT_FILL_LIMITS[kind],
                )
        raw_mode = str(getattr(mode, "value", mode) or "").strip()
        if raw_mode and self.supported_modes:
            normalized_mode = _normalize_video_mode(raw_mode)
            supported_modes = tuple(
                _normalize_video_mode(item) for item in self.supported_modes
            )
            if normalized_mode not in supported_modes:
                add_violation(
                    "unsupported_video_mode",
                    requestedMode=raw_mode,
                    supportedModes=list(self.supported_modes),
                )
        media_mode = self._explicit_media_mode(
            raw_mode,
            _normalize_video_mode(raw_mode),
        )
        if media_mode == "image_to_video" and not image_path:
            add_violation(
                "required_first_frame_missing",
                requestedMode=raw_mode,
            )
        elif media_mode == "first_last_frame" and (
            not image_path or not last_frame_path
        ):
            add_violation(
                "required_first_last_frames_missing",
                requestedMode=raw_mode,
                hasFirstFrame=bool(image_path),
                hasLastFrame=bool(last_frame_path),
            )
        elif media_mode == "image_reference" and not any(
            _reference_kind(value) == "image" and _reference_path(value)
            for value in references
        ):
            add_violation(
                "required_image_reference_missing",
                requestedMode=raw_mode,
            )
        elif media_mode == "video_edit" and not any(
            _reference_kind(value) == "video" and _reference_path(value)
            for value in references
        ):
            add_violation(
                "required_video_reference_missing",
                requestedMode=raw_mode,
            )
        items: list[tuple[str, str, str]] = []
        if image_path:
            items.append(("image", "first", str(image_path)))
        if last_frame_path:
            items.append(("image", "last", str(last_frame_path)))
        for value in references:
            kind = _reference_kind(value)
            role = _normalize_frame_role(_reference_field(value, "role", ""))
            role_key = (
                "first"
                if role == "firstFrame"
                else "last"
                if role == "lastFrame"
                else "reference"
            )
            path = _reference_path(value)
            items.append((kind, role_key, path))
        counts = {"input_images": 0, "reference_images": 0, "reference_videos": 0, "reference_audios": 0}
        seen: set[tuple[str, str, str]] = set()
        for kind, role, path in items:
            marker = (kind, role, path)
            if path and marker in seen:
                continue
            seen.add(marker)
            if role in {"first", "last"}:
                counts["input_images"] += int(kind == "image")
            elif kind in {"image", "video", "audio"}:
                counts[f"reference_{kind}s"] += 1
        for name, count in counts.items():
            limit = self.reference_limits.get(name)
            if limit is not None and count > limit:
                add_violation(
                    f"{name}_limit_exceeded",
                    referenceCounts=counts,
                    referenceLimits=dict(self.reference_limits),
                )
        if violations:
            return {
                "message": _contract_violation_message(violations),
                "violations": violations,
            }
        return None

    def _resolve_mode(self, value: object) -> tuple[str, str]:
        """Return the provider wire value and canonical value for one mode.

        Discovery contracts may use either the shared snake_case vocabulary or
        a provider spelling.  Preserve an exact declared value when possible;
        otherwise use the declared alias that has the same canonical meaning.
        """
        raw = str(getattr(value, "value", value) or "").strip()
        if not raw:
            return "", ""
        canonical = _normalize_video_mode(raw)
        for declared in self.supported_modes:
            if raw.casefold() == declared.casefold():
                return declared, canonical
        for declared in self.supported_modes:
            if _normalize_video_mode(declared) == canonical:
                return declared, canonical
        return raw, canonical

    @staticmethod
    def _explicit_media_mode(raw_mode: object, canonical_mode: str) -> str:
        """Return the media contract for one explicitly selected mode.

        ``imageReference`` and ``videoEdit`` intentionally share the generic
        ``reference_to_video`` canonical value, so their raw labels must remain
        available until media selection is complete.
        """

        raw = str(getattr(raw_mode, "value", raw_mode) or "").strip()
        if not raw:
            return ""
        compact = re.sub(r"[^a-z0-9]+", "", raw.casefold())
        explicit = {
            "texttovideo": "text_to_video",
            "imagetovideo": "image_to_video",
            "firstframe": "image_to_video",
            "firstlastframe": "first_last_frame",
            "allreference": "all_reference",
            "referencetovideo": "all_reference",
            "multimodalreference": "all_reference",
            "imagereference": "image_reference",
            "videoedit": "video_edit",
        }
        if compact in explicit:
            return explicit[compact]
        if canonical_mode in {
            "text_to_video",
            "image_to_video",
            "first_last_frame",
        }:
            return canonical_mode
        if canonical_mode == "reference_to_video":
            return "all_reference"
        return ""

    def _filter_media_for_mode(
        self,
        raw_mode: object,
        *,
        canonical_mode: str,
        image_path: str | None,
        last_frame_path: str | None,
        references: Iterable[object],
    ) -> tuple[str | None, str | None, tuple[object, ...]]:
        """Apply one deterministic media contract before validation or relay.

        The no-mode path deliberately returns the original collection.  This
        preserves legacy inference for callers that predate explicit mode
        selection while every recognized mode gets a strict, idempotent view.
        """

        media_mode = self._explicit_media_mode(raw_mode, canonical_mode)
        source_references = tuple(references or ())
        if not media_mode:
            return image_path, last_frame_path, source_references

        def clean(value: object) -> str:
            return str(value or "").strip()

        def reference_path(value: object) -> str:
            return _reference_path(value)

        image_references = [
            value
            for value in source_references
            if _reference_kind(value) == "image" and reference_path(value)
        ]

        if media_mode == "text_to_video":
            return None, None, ()

        if media_mode == "image_to_video":
            # A dedicated image_path is the authoritative first frame.  When
            # it is absent, use the first image reference and promote it to the
            # same first-frame slot so only one relay/upload can occur.
            first = clean(image_path)
            if not first:
                first = next(
                    (
                        reference_path(value)
                        for value in image_references
                        if _normalize_frame_role(_reference_field(value, "role", ""))
                        == "firstFrame"
                    ),
                    "",
                ) or (reference_path(image_references[0]) if image_references else "")
            return first or None, None, ()

        if media_mode == "first_last_frame":
            first = clean(image_path)
            if not first:
                first = next(
                    (
                        reference_path(value)
                        for value in image_references
                        if _normalize_frame_role(_reference_field(value, "role", ""))
                        == "firstFrame"
                    ),
                    "",
                )
            last = clean(last_frame_path)
            if not last:
                last = next(
                    (
                        reference_path(value)
                        for value in image_references
                        if _normalize_frame_role(_reference_field(value, "role", ""))
                        == "lastFrame"
                    ),
                    "",
                )
            return first or None, last or None, ()

        if media_mode == "image_reference":
            # Image-reference mode treats every accepted image uniformly.  A
            # standalone first-frame input is promoted into the ordinary
            # reference list, avoiding an input-frame count mismatch.  A stale
            # transition tail belongs only to firstLastFrame and is dropped.
            selected: list[object] = []
            seen_paths: set[str] = set()
            for value in (image_path,):
                path = clean(value)
                if path and path not in seen_paths:
                    selected.append(path)
                    seen_paths.add(path)
            for value in image_references:
                path = reference_path(value)
                if path and path not in seen_paths:
                    selected.append(value)
                    seen_paths.add(path)
            return None, None, tuple(selected)

        if media_mode == "video_edit":
            return (
                None,
                None,
                tuple(
                    value
                    for value in source_references
                    if _reference_kind(value) == "video" and reference_path(value)
                ),
            )

        # allReference is a multimodal-reference request, not a strict
        # first/last-frame request.  Canvas callers commonly pass the first
        # image both as ``image_path`` and in ``references``; forwarding that
        # value as ``first_frame`` makes MiniMax H3 reject the request because
        # strict frames cannot be mixed with reference media.  Collapse frame
        # slots into ordinary image references and deduplicate before relay.
        selected: list[object] = []
        seen_paths: set[str] = set()
        for value in (image_path, last_frame_path):
            path = clean(value)
            if path and path not in seen_paths:
                selected.append(path)
                seen_paths.add(path)
        for value in source_references:
            path = reference_path(value)
            if path and path not in seen_paths:
                selected.append(value)
                seen_paths.add(path)
        return None, None, tuple(selected)

    def _apply_mode_mapping(self, container: Mapping[str, object], mode: str) -> dict[str, object]:
        """Keep an explicit mode in the provider payload without guessing it."""
        if not mode:
            return dict(container)
        result = apply_video_common_parameter_mapping(
            container,
            {"mode": mode},
            self.provider_mapping,
        )
        has_mode_mapping = any(
            canonical_parameter_key(key) == "mode"
            for key in self.provider_mapping
        )
        if not has_mode_mapping:
            for alias in ("mode", "gen_mode", "genMode", "generation_mode", "generationMode"):
                result.pop(alias, None)
            result["mode"] = mode
        return result

    @staticmethod
    def _apply_frame_mapping(
        container: Mapping[str, object],
        *,
        first_frame: str = "",
        last_frame: str = "",
        provider_mapping: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Map explicit frame roles only when the provider declares them."""

        values: dict[str, object] = {}
        if first_frame:
            values["firstFrame"] = first_frame
        if last_frame:
            values["lastFrame"] = last_frame
        if not values:
            return dict(container)
        return apply_video_common_parameter_mapping(container, values, provider_mapping)

    def _workflow_provider_mapping(self) -> dict[str, str]:
        """Resolve workflow node paths while keeping explicit mappings authoritative.

        ComfyUI-style providers often describe an input as ``nodeId`` plus a
        nested ``field`` instead of exposing a flat request key.  The generic
        adapter keeps that metadata separate from AutoDL's wrapper mapping and
        only combines it for the native ``/prompt`` workflow family.
        """

        mapping = {
            str(key): str(value)
            for key, value in self.provider_mapping.items()
            if str(key).strip() and str(value).strip()
        }
        for item in self.workflow_input_rules:
            key = str(item.get("key") or "").strip()
            rule = item.get("rule")
            if not isinstance(rule, Mapping):
                # OpenAPI-derived workflow inputs may already expose
                # ``nodeId``/``field`` on the descriptor itself.
                rule = item
            if not key or not isinstance(rule, Mapping):
                continue
            node_id = str(rule.get("nodeId") or rule.get("node_id") or "").strip()
            field = str(rule.get("field") or "").strip()
            if not node_id or not field:
                continue
            if field.startswith("/"):
                # A leading slash is already a JSON-pointer-like absolute
                # graph path; prepending the node id would corrupt it.
                target = field
            elif field.startswith("."):
                target = f"{node_id}{field}"
            elif field.startswith((f"{node_id}.", f"{node_id}[")):
                target = field
            else:
                target = f"{node_id}.{field}"
            canonical = canonical_parameter_key(key)
            existing_key = next(
                (
                    raw_key
                    for raw_key in mapping
                    if canonical_parameter_key(raw_key) == canonical
                ),
                "",
            )
            existing_value = str(mapping.get(existing_key) or "").strip()
            # A schema-local identity mapping (duration -> duration) carries
            # no graph location; prefer the richer node/field evidence. An
            # operator-supplied provider path remains authoritative.
            identity_values = {
                canonical.casefold(),
                existing_key.casefold() if existing_key else "",
            }
            if not existing_value or existing_value.casefold() in identity_values:
                mapping[canonical or key] = target
        return mapping

    @staticmethod
    def _workflow_node_id(node: object) -> str:
        if not isinstance(node, Mapping):
            return ""
        for key in ("id", "nodeId", "node_id"):
            value = node.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    @classmethod
    def _workflow_node_index(cls, nodes: object, node_id: str) -> int | None:
        if not isinstance(nodes, list):
            return None
        target = str(node_id or "").strip()
        if not target:
            return None
        for index, node in enumerate(nodes):
            if cls._workflow_node_id(node) == target:
                return index
        return None

    @classmethod
    def _resolve_workflow_graph_tokens(
        cls, container: Mapping[str, object], tokens: list[str]
    ) -> list[str]:
        """Resolve ``nodeId.inputs.field`` against list-shaped graph nodes.

        Providers expose the same graph as either a node-id dictionary,
        ``{"nodes": [{"id": ...}]}``, or a ComfyUI ``{"prompt": {...}}``
        envelope.  Treat a matching node id as an address into the existing
        list instead of creating a duplicate root-level node key.
        """

        if not tokens:
            return tokens
        first = tokens[0]

        nodes_index = cls._workflow_node_index(container.get("nodes"), first)
        if nodes_index is not None:
            return ["nodes", str(nodes_index), *tokens[1:]]

        prompt = container.get("prompt")
        if isinstance(prompt, Mapping):
            prompt_nodes_index = cls._workflow_node_index(prompt.get("nodes"), first)
            if prompt_nodes_index is not None:
                return ["prompt", "nodes", str(prompt_nodes_index), *tokens[1:]]
            if first in prompt:
                return ["prompt", *tokens]

        return tokens

    @staticmethod
    def _workflow_path_tokens(path: object) -> list[str]:
        """Parse bounded dot/bracket workflow paths into traversal tokens."""

        raw = str(path or "").strip()
        if not raw:
            return []
        lowered = raw.casefold()
        for prefix in ("input.", "payload.", "request."):
            if lowered.startswith(prefix):
                raw = raw[len(prefix) :].strip()
                break
        if raw.startswith("/"):
            raw = raw.replace("/", ".")
        raw = re.sub(r"\[\s*(['\"]?)([^\]'\"]+)\1\s*\]", r".\2", raw)
        return [token for token in raw.split(".") if token]

    @classmethod
    def _set_workflow_path(
        cls,
        container: dict[str, object],
        path: object,
        value: object,
        *,
        overwrite: bool,
    ) -> None:
        """Set one workflow input, creating only deterministic JSON nodes."""

        tokens = cls._workflow_path_tokens(path)
        if not tokens:
            return
        tokens = cls._resolve_workflow_graph_tokens(container, tokens)
        current: object = container
        for index, token in enumerate(tokens):
            is_last = index == len(tokens) - 1
            next_is_index = not is_last and tokens[index + 1].isdigit()
            if isinstance(current, dict):
                if is_last:
                    if overwrite or token not in current:
                        current[token] = value
                    return
                if token not in current or not isinstance(current[token], (dict, list)):
                    current[token] = [] if next_is_index else {}
                current = current[token]
                continue
            if isinstance(current, list) and token.isdigit():
                position = int(token)
                while len(current) <= position:
                    current.append([] if next_is_index else {})
                if is_last:
                    if overwrite or current[position] in (None, ""):
                        current[position] = value
                    return
                current = current[position]
                continue
            # Invalid paths are ignored rather than replacing a native graph
            # node with a different container type.
            return

    @staticmethod
    def _resolution_level(value: object) -> str:
        # ``736p竖`` has a CJK character immediately after ``p``; ``\b``
        # would not match there because both sides are Unicode word chars.
        match = re.search(
            r"(\d{2,5})\s*(p|k)(?![a-z])", str(value or ""), re.IGNORECASE
        )
        return f"{match.group(1)}{match.group(2).lower()}" if match else ""

    @staticmethod
    def _ratio_number(value: object) -> float | None:
        text = str(value or "").strip().lower().replace("×", "x")
        match = re.search(r"(\d+(?:\.\d+)?)\s*[:x]\s*(\d+(?:\.\d+)?)", text)
        if not match:
            return None
        try:
            left, right = float(match.group(1)), float(match.group(2))
        except (TypeError, ValueError):
            return None
        return left / right if left > 0 and right > 0 else None

    @classmethod
    def _resolution_mapping_ratio(cls, mapping: Mapping[str, object]) -> float | None:
        width = mapping.get("width")
        height = mapping.get("height")
        try:
            if float(width) > 0 and float(height) > 0:
                return float(width) / float(height)
        except (TypeError, ValueError):
            pass
        return cls._ratio_number(mapping.get("label"))

    @classmethod
    def _resolution_mapping_values(
        cls,
        mappings: Iterable[Mapping[str, object]],
        *,
        resolution: object,
        aspect_ratio: object,
    ) -> dict[str, object]:
        """Select one discovered resolution option and return its node writes."""

        entries = [item for item in mappings if isinstance(item, Mapping)]
        if not entries:
            return {}
        requested = str(resolution or "").strip().casefold()
        requested_level = cls._resolution_level(requested)
        requested_ratio = cls._ratio_number(aspect_ratio)
        scored: list[tuple[int, int, Mapping[str, object]]] = []
        for index, item in enumerate(entries):
            values = item.get("values")
            if not isinstance(values, Mapping):
                continue
            label = str(item.get("label") or "").strip()
            label_key = label.casefold()
            level = cls._resolution_level(label)
            item_ratio = cls._resolution_mapping_ratio(item)
            if requested and label_key == requested:
                score = 0
            elif requested_level and level == requested_level and requested_ratio and item_ratio:
                score = 1 if abs(requested_ratio - item_ratio) < 0.01 else 4
            elif requested_level and level == requested_level:
                score = 2
            elif not requested_level and requested_ratio and item_ratio:
                score = 3 if abs(requested_ratio - item_ratio) < 0.01 else 5
            elif not requested_level and not requested_ratio:
                score = 6
            else:
                continue
            scored.append((score, index, item))
        if not scored:
            return {}
        _, _, selected = min(scored, key=lambda item: (item[0], item[1]))
        return {
            str(key): value
            for key, value in (selected.get("values") or {}).items()
            if str(key).strip()
        }

    @staticmethod
    def _mapped_parameter_key(
        provider_mapping: Mapping[str, object] | None, canonical: str
    ) -> str:
        for key, value in (provider_mapping or {}).items():
            if canonical_parameter_key(key) != canonical:
                continue
            target = str(value or "").strip()
            if not target:
                continue
            lowered = target.casefold()
            for prefix in ("input.", "payload.", "request."):
                if lowered.startswith(prefix):
                    target = target[len(prefix) :].strip()
                    break
            return target
        return ""

    @staticmethod
    def _mapped_parameter_keys(
        provider_mapping: Mapping[str, object] | None, canonical: str
    ) -> set[str]:
        """Return raw and dotted provider keys for request-value cleanup."""

        result: set[str] = set()
        for key, value in (provider_mapping or {}).items():
            if canonical_parameter_key(key) != canonical:
                continue
            target = str(value or "").strip()
            if not target:
                continue
            result.add(target)
            lowered = target.casefold()
            for prefix in ("input.", "payload.", "request."):
                if lowered.startswith(prefix):
                    result.add(target[len(prefix) :].strip())
                    break
        return {item for item in result if item}

    @classmethod
    def _drop_request_scoped_parameter_values(
        cls,
        values: dict[str, object],
        *,
        canonical: str,
        provider_mapping: Mapping[str, object] | None = None,
    ) -> None:
        """Remove persisted common controls before applying request values."""

        canonical_key = canonical_parameter_key(canonical)
        provider_keys = cls._mapped_parameter_keys(provider_mapping, canonical_key)
        for key in list(values):
            if canonical_parameter_key(key) == canonical_key or key in provider_keys:
                values.pop(key, None)

    @classmethod
    def _drop_request_scoped_media_values(
        cls,
        values: dict[str, object],
        *,
        first_frame: str = "",
        last_frame: str = "",
        provider_mapping: Mapping[str, object] | None = None,
    ) -> None:
        """Prevent persisted frame fields from overriding request media."""

        for canonical, current in (
            ("firstFrame", first_frame),
            ("lastFrame", last_frame),
        ):
            if not current:
                continue
            provider_keys = cls._mapped_parameter_keys(provider_mapping, canonical)
            for key in list(values):
                if canonical_parameter_key(key) == canonical or key in provider_keys:
                    values.pop(key, None)

    @classmethod
    def _drop_explicit_mode_media_values(
        cls,
        values: dict[str, object],
        *,
        provider_mapping: Mapping[str, object] | None = None,
    ) -> None:
        """Remove persisted media fields when the node selected an explicit mode."""

        for canonical in ("images", "videos", "audios", "firstFrame", "lastFrame"):
            cls._drop_request_scoped_parameter_values(
                values,
                canonical=canonical,
                provider_mapping=provider_mapping,
            )

    async def recover_task(
        self,
        *,
        task_id: str,
        output_path: str,
        on_log: Callable[[str], None] | None = None,
        on_progress: Callable[[float], None] | None = None,
        on_task_event: Callable[[dict[str, object]], None] | None = None,
        **_kwargs: object,
    ):
        from novelvideo.generators.video_generator import VideoGenResult, VideoGenStatus

        task_id = str(task_id or "").strip()
        if not task_id:
            return VideoGenResult(status=VideoGenStatus.FAILED, error="missing provider task id")
        try:
            task = await self._poll(
                task_id,
                emit=lambda stage, **details: on_task_event(
                    {"stage": stage, "model": self.model, "provider_task_id": task_id, **details}
                )
                if on_task_event
                else None,
                log=on_log or (lambda _message: None),
                progress=on_progress or (lambda _value: None),
            )
            provider_cost_fields = extract_provider_cost_evidence(
                task,
                prefix="result",
            ).as_event_fields()
            if on_task_event and provider_cost_fields:
                try:
                    on_task_event(
                        {
                            "stage": "provider_cost",
                            "model": self.model,
                            "provider_task_id": task_id,
                            **provider_cost_fields,
                        }
                    )
                except Exception:
                    pass
            result_url = self._find_result_url(self.contract.extract_result(task))
            if not result_url and self.family is VideoProtocolFamily.WORKFLOW:
                result_url = self._workflow_result_url(task)
            if not result_url:
                raise GenericVideoAdapterError(
                    "视频任务完成但响应缺少结果 URL",
                    stage="artifact",
                    error_code="VIDEO_RESULT_URL_MISSING",
                )
            await self._download(result_url, output_path)
            return VideoGenResult(
                status=VideoGenStatus.DONE,
                video_url=result_url,
                video_path=output_path,
                task_id=task_id,
                provider_task_id=task_id,
            )
        except GenericVideoAdapterError as exc:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=str(exc),
                task_id=task_id,
                error_metadata=exc.provider_error_metadata,
            )

    async def _poll(
        self,
        task_id: str,
        *,
        emit: Callable[..., None],
        log: Callable[[str], None],
        progress: Callable[[float], None],
    ) -> dict[str, object]:
        for poll_count in range(self.max_polls):
            payload = await self._request_json("GET", self._query_url(task_id), stage="query")
            status = self.contract.extract_status(payload)
            if not status:
                raise GenericVideoAdapterError(
                    "视频任务查询响应缺少状态字段",
                    stage="query",
                    error_code="VIDEO_STATUS_MISSING",
                    request_contract={"response_keys": self._response_keys(payload)},
                )
            emit(
                "polling",
                provider_task_id=task_id,
                upstream_status=status,
                poll_count=poll_count,
            )
            progress(0.2 + (poll_count / max(self.max_polls, 1)) * 0.7)
            if status in self.contract.completed_statuses:
                return payload
            if status in self.contract.failed_statuses:
                detail = self.contract.extract_error(payload)
                raise GenericVideoAdapterError(
                    detail or "上游视频任务失败且没有返回失败原因",
                    stage="query",
                    error_code=(
                        "VIDEO_UPSTREAM_TASK_FAILED" if detail else "VIDEO_UPSTREAM_TASK_FAILED_NO_REASON"
                    ),
                    retryable=not bool(detail),
                    request_contract={"response_keys": self._response_keys(payload)},
                )
            await asyncio.sleep(self.poll_interval)
        raise GenericVideoAdapterError(
            "视频任务轮询超时",
            stage="query",
            error_code="VIDEO_UPSTREAM_TIMEOUT",
            retryable=True,
        )

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        stage: str,
        payload: dict[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        request_headers = {**self._headers(), **dict(headers or {})}
        try:
            timeout = aiohttp.ClientTimeout(total=120)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method,
                    url,
                    headers=request_headers,
                    json=payload if method.upper() != "GET" else None,
                ) as response:
                    text = await response.text()
                    if response.status < 200 or response.status >= 300:
                        raise GenericVideoAdapterError(
                            f"视频{stage}端点返回 HTTP {response.status}",
                            stage=stage,
                            error_code=(
                                "VIDEO_ENDPOINT_NOT_FOUND"
                                if response.status in {404, 405}
                                else "VIDEO_REQUEST_REJECTED"
                            ),
                            http_status=response.status,
                            retryable=response.status in {408, 409, 425, 429} or response.status >= 500,
                        )
                    if not text.strip():
                        raise GenericVideoAdapterError(
                            f"视频{stage}端点返回空响应",
                            stage=stage,
                            error_code="VIDEO_EMPTY_RESPONSE",
                            retryable=True,
                        )
                    try:
                        value = json.loads(text)
                    except json.JSONDecodeError as exc:
                        raise GenericVideoAdapterError(
                            f"视频{stage}端点返回了非 JSON 响应",
                            stage=stage,
                            error_code=(
                                "VIDEO_ENDPOINT_HTML_RESPONSE"
                                if text.lstrip().startswith("<")
                                else "VIDEO_JSON_RESPONSE_INVALID"
                            ),
                        ) from exc
                    if not isinstance(value, dict):
                        raise GenericVideoAdapterError(
                            f"视频{stage}端点返回了非对象 JSON",
                            stage=stage,
                            error_code="VIDEO_JSON_RESPONSE_INVALID",
                        )
                    return value
        except GenericVideoAdapterError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise GenericVideoAdapterError(
                f"视频{stage}端点连接失败：{type(exc).__name__}",
                stage=stage,
                error_code="VIDEO_TRANSPORT_FAILED",
                retryable=True,
            ) from exc

    async def _download(self, url: str, output_path: str) -> None:
        if url.startswith("data:"):
            header, _, encoded = url.partition(",")
            if ";base64" not in header:
                raise GenericVideoAdapterError(
                    "视频结果 data URL 不是 base64",
                    stage="download",
                    error_code="VIDEO_RESULT_DOWNLOAD_FAILED",
                )
            content = base64.b64decode(encoded)
        else:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as session:
                    # A provider may return a signed object-store URL on a
                    # different host.  Never forward this channel's bearer key
                    # to that host; same-origin result URLs still use the
                    # request credential required by private result endpoints.
                    current_url = str(url).strip()
                    initial_origin = _url_origin(current_url)
                    configured_origin = _url_origin(self.base_url)
                    authorization = (
                        self.api_key
                        if self.family is VideoProtocolFamily.AUTODL_COMFYUI
                        else f"Bearer {self.api_key}"
                    )
                    # Authorization is eligible only when the initial result
                    # URL is the configured endpoint's origin.  Once a
                    # redirect crosses that origin, it stays stripped for the
                    # remainder of the chain, even if a later hop returns.
                    authorization_origin = (
                        initial_origin
                        if initial_origin is not None and initial_origin == configured_origin
                        else None
                    )
                    redirects = 0
                    while True:
                        current_origin = _url_origin(current_url)
                        headers: dict[str, str] = {}
                        if authorization_origin is not None and current_origin == authorization_origin:
                            headers["Authorization"] = authorization
                        async with session.get(
                            current_url,
                            headers=headers,
                            allow_redirects=False,
                        ) as response:
                            status = int(response.status)
                            if 300 <= status < 400:
                                location = str(
                                    response.headers.get("Location")
                                    or response.headers.get("location")
                                    or ""
                                ).strip()
                                if not location:
                                    raise GenericVideoAdapterError(
                                        f"视频结果下载失败：HTTP {status} 重定向缺少 Location",
                                        stage="download",
                                        error_code="VIDEO_RESULT_DOWNLOAD_FAILED",
                                        http_status=status,
                                    )
                                if redirects >= _MAX_RESULT_REDIRECTS:
                                    raise GenericVideoAdapterError(
                                        f"视频结果下载重定向超过上限（{_MAX_RESULT_REDIRECTS} 次）",
                                        stage="download",
                                        error_code="VIDEO_RESULT_DOWNLOAD_FAILED",
                                        http_status=status,
                                    )
                                next_url = urljoin(current_url, location)
                                next_origin = _url_origin(next_url)
                                if next_origin is None:
                                    raise GenericVideoAdapterError(
                                        "视频结果下载失败：重定向目标不是 HTTP(S) URL",
                                        stage="download",
                                        error_code="VIDEO_RESULT_DOWNLOAD_FAILED",
                                        http_status=status,
                                    )
                                if next_origin != current_origin:
                                    authorization_origin = None
                                current_url = next_url
                                redirects += 1
                                continue
                            content_type = str(response.headers.get("Content-Type") or "").lower()
                            content = await response.read()
                            if status < 200 or status >= 300:
                                raise GenericVideoAdapterError(
                                    f"视频结果下载失败：HTTP {status}",
                                    stage="download",
                                    error_code="VIDEO_RESULT_DOWNLOAD_FAILED",
                                    http_status=status,
                                    retryable=status >= 500,
                                )
                            if "text/html" in content_type or content.lstrip().startswith(b"<"):
                                raise GenericVideoAdapterError(
                                    "视频结果地址返回了 HTML 页面",
                                    stage="download",
                                    error_code="VIDEO_RESULT_HTML_RESPONSE",
                                )
                            break
            except GenericVideoAdapterError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                raise GenericVideoAdapterError(
                    f"视频结果下载失败：{type(exc).__name__}",
                    stage="download",
                    error_code="VIDEO_RESULT_DOWNLOAD_FAILED",
                    retryable=True,
                ) from exc
        if not content:
            raise GenericVideoAdapterError(
                "视频结果下载为空",
                stage="download",
                error_code="VIDEO_RESULT_DOWNLOAD_FAILED",
                retryable=True,
            )
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(f"{target.suffix}.part")
        partial.write_bytes(content)
        partial.replace(target)

    async def _build_payload(
        self,
        *,
        prompt: str,
        image_path: str | None,
        references: Iterable[object],
        last_frame_path: str | None,
        duration: float,
        aspect_ratio: str,
        resolution: str,
        generate_audio: bool,
        kwargs: Mapping[str, object],
        mode: str | None = None,
        _media_filtered: bool = False,
    ) -> dict[str, object]:
        if not _media_filtered:
            _, canonical_mode = self._resolve_mode(mode)
            image_path, last_frame_path, references = self._filter_media_for_mode(
                mode,
                canonical_mode=canonical_mode,
                image_path=image_path,
                last_frame_path=last_frame_path,
                references=tuple(references or ()),
            )
        images: list[str] = []
        videos: list[str] = []
        audios: list[str] = []
        first_frame = ""
        last_frame = ""
        values: list[tuple[object, str]] = []
        if image_path:
            values.append((image_path, "firstFrame"))
        if last_frame_path:
            values.append((last_frame_path, "lastFrame"))
        values.extend(
            (value, _normalize_frame_role(_reference_field(value, "role", "")))
            for value in references
        )
        seen_media: set[tuple[str, str, str]] = set()
        for value, role in values:
            path = _reference_path(value)
            kind = _reference_kind(value)
            marker = (kind, str(path or "").strip(), role)
            if marker in seen_media:
                continue
            seen_media.add(marker)
            encoded = await self._media_value(path, kind=kind)
            if encoded and kind == "video":
                videos.append(encoded)
            elif encoded and kind == "audio":
                audios.append(encoded)
            elif encoded:
                images.append(encoded)
                if role == "firstFrame" and not first_frame:
                    first_frame = encoded
                elif role == "lastFrame" and not last_frame:
                    last_frame = encoded
        input_payload: dict[str, object] = {
            "prompt": str(prompt or "").strip(),
            "model": self.model,
            "duration": int(round(float(duration))),
            "duration_seconds": int(round(float(duration))),
            "generate_audio": bool(generate_audio),
        }
        if mode:
            input_payload = self._apply_mode_mapping(input_payload, mode)
        size_value = self._resolve_size_slot(aspect_ratio)
        if size_value is not None:
            # Size-slot models use a literal WxH provider field. Keep it
            # separate from aspect_ratio so an upstream validator cannot
            # mistake a pixel slot for a ratio string.
            provider_size_key = self._mapped_parameter_key(
                self.provider_mapping, "size"
            ) or self.size_field
            input_payload[provider_size_key] = size_value
        elif self.allowed_aspect_ratios or self.supports_custom_aspect_ratio:
            input_payload["aspect_ratio"] = str(aspect_ratio or "")
        if self.allowed_resolutions or self.supports_custom_resolution:
            input_payload["resolution"] = str(resolution or "")
        if images:
            input_payload.update({"image": images[0], "images": images, "reference_images": images})
        if videos:
            input_payload.update({"videos": videos, "reference_videos": videos})
        if audios:
            input_payload.update({"audios": audios, "reference_audios": audios})
        input_payload = self._apply_frame_mapping(
            input_payload,
            first_frame=first_frame,
            last_frame=last_frame,
            provider_mapping=self.provider_mapping,
        )
        common_parameters: dict[str, object] = {
            "duration": int(round(float(duration))),
            "aspectRatio": str(aspect_ratio or ""),
            "resolution": str(resolution or ""),
            "generateAudio": bool(generate_audio),
            "images": images,
            "videos": videos,
            "audios": audios,
        }
        if mode:
            common_parameters["mode"] = mode
        input_payload = apply_video_common_parameter_mapping(
            input_payload,
            common_parameters,
            self.provider_mapping,
        )
        if mode:
            # A mapped empty array is still a provider media field.  Explicit
            # text/reference modes must not leak empty or disallowed media
            # slots back into the request after filtering.
            for canonical, present in (
                ("images", bool(images)),
                ("videos", bool(videos)),
                ("audios", bool(audios)),
            ):
                if not present:
                    self._drop_request_scoped_parameter_values(
                        input_payload,
                        canonical=canonical,
                        provider_mapping=self.provider_mapping,
                    )
        # Apply the discovered provider mapping after common controls are
        # compiled. Unknown fields stay in the request instead of vanishing at
        # the generic adapter boundary.
        mapped_parameters = compile_video_provider_parameters(
            self.parameter_values, self.provider_mapping
        )
        if mode:
            # The request-scoped mode is authoritative.  Do not let a stale
            # persisted parameter value overwrite the selected node mode.
            mapped_parameters.pop("mode", None)
            mapped_parameters.pop("gen_mode", None)
            mapped_parameters.pop("genMode", None)
            mapped_parameters.pop("generation_mode", None)
            mapped_parameters.pop("generationMode", None)
            mapped_mode_key = next(
                (
                    str(value).strip()
                    for key, value in self.provider_mapping.items()
                    if canonical_parameter_key(key) == "mode" and str(value).strip()
                ),
                "",
            )
            if mapped_mode_key:
                mapped_parameters.pop(mapped_mode_key, None)
        request_scoped_parameters = {"duration", "generateAudio"}
        if self.allowed_resolutions or self.supports_custom_resolution:
            request_scoped_parameters.add("resolution")
        if self.allowed_aspect_ratios or self.supports_custom_aspect_ratio:
            request_scoped_parameters.add("aspectRatio")
        if size_value is not None:
            request_scoped_parameters.add("size")
        for canonical in request_scoped_parameters:
            self._drop_request_scoped_parameter_values(
                mapped_parameters,
                canonical=canonical,
                provider_mapping=self.provider_mapping,
            )
        if mode:
            self._drop_explicit_mode_media_values(
                mapped_parameters,
                provider_mapping=self.provider_mapping,
            )
        else:
            for canonical, present in (
                ("images", bool(images)),
                ("videos", bool(videos)),
                ("audios", bool(audios)),
            ):
                if present:
                    self._drop_request_scoped_parameter_values(
                        mapped_parameters,
                        canonical=canonical,
                        provider_mapping=self.provider_mapping,
                    )
        self._drop_request_scoped_media_values(
            mapped_parameters,
            first_frame=first_frame,
            last_frame=last_frame,
            provider_mapping=self.provider_mapping,
        )
        input_payload.update(mapped_parameters)
        if isinstance(kwargs.get("workflow"), dict):
            input_payload["workflow"] = kwargs["workflow"]
        if self.family is VideoProtocolFamily.PREDICTION:
            return {"version": self.model, "model": self.model, "input": input_payload}
        if self.family is VideoProtocolFamily.QUEUE:
            return {"input": input_payload}
        if self.family is VideoProtocolFamily.LONG_RUNNING_OPERATION:
            return {"instances": [input_payload], "parameters": input_payload}
        if self.family is VideoProtocolFamily.WORKFLOW:
            workflow = kwargs.get("workflow")
            if isinstance(workflow, dict):
                # Preserve the provider's workflow graph byte-for-byte where
                # possible, while applying discovered controls to their real
                # nested node inputs.  A deep copy prevents a failed submit or
                # retry from mutating the caller's reusable graph in memory.
                workflow_payload = copy.deepcopy(workflow)
                workflow_mapping = self._workflow_provider_mapping()
                resolution_mapping_values = self._resolution_mapping_values(
                    self.resolution_mappings,
                    resolution=resolution,
                    aspect_ratio=aspect_ratio,
                )
                workflow_values: dict[str, object] = {}
                resolution_mapping_is_native = bool(resolution_mapping_values) and (
                    not workflow_mapping.get("resolution")
                    or canonical_parameter_key(workflow_mapping.get("resolution"))
                    == "resolution"
                )
                if (
                    self.allowed_resolutions or self.supports_custom_resolution
                ) and not resolution_mapping_is_native:
                    workflow_values["resolution"] = str(resolution or "")
                if self.allowed_aspect_ratios or self.supports_custom_aspect_ratio:
                    workflow_values["aspectRatio"] = str(aspect_ratio or "")
                if self.size_slots:
                    size_value = self._resolve_size_slot(aspect_ratio)
                    if size_value is not None:
                        workflow_values["size"] = size_value
                workflow_values.update(
                    {
                        "duration": int(round(float(duration))),
                        "generateAudio": bool(generate_audio),
                    }
                )
                if mode:
                    for canonical, media_values in (
                        ("images", images),
                        ("videos", videos),
                        ("audios", audios),
                    ):
                        if media_values:
                            # Native workflow graphs vary in their media input
                            # node shape.  Only an explicitly discovered
                            # mapping may place these values into the graph;
                            # an empty list is never written for an explicit
                            # mode.
                            workflow_values[canonical] = media_values
                if mode:
                    workflow_values["mode"] = mode
                if first_frame:
                    workflow_values["firstFrame"] = first_frame
                if last_frame:
                    workflow_values["lastFrame"] = last_frame
                mapped_workflow_values = apply_video_common_parameter_mapping(
                    {}, workflow_values, workflow_mapping
                )
                for key, value in mapped_workflow_values.items():
                    self._set_workflow_path(
                        workflow_payload,
                        key,
                        value,
                        overwrite=True,
                    )
                # AutoDL/ComfyUI resolution enums often encode width and
                # height as several node writes instead of one scalar field.
                # Apply the selected option after common controls so the
                # user's resolution choice wins over graph defaults.
                for key, value in resolution_mapping_values.items():
                    self._set_workflow_path(
                        workflow_payload,
                        key,
                        value,
                        overwrite=True,
                    )
                # Keep the historical generic-mode fallback when a workflow
                # does not declare a provider key; frame values still require
                # an explicit mapping because native graphs vary widely.
                if mode and not any(
                    canonical_parameter_key(key) == "mode"
                    for key in workflow_mapping
                ):
                    self._set_workflow_path(
                        workflow_payload,
                        "mode",
                        mode,
                        overwrite=True,
                    )
                advanced_parameters = compile_video_provider_parameters(
                    self.parameter_values,
                    workflow_mapping,
                )
                for canonical in (
                    "duration",
                    "resolution",
                    "aspectRatio",
                    "size",
                    "generateAudio",
                    "mode",
                ):
                    self._drop_request_scoped_parameter_values(
                        advanced_parameters,
                        canonical=canonical,
                        provider_mapping=workflow_mapping,
                    )
                if mode:
                    self._drop_explicit_mode_media_values(
                        advanced_parameters,
                        provider_mapping=workflow_mapping,
                    )
                self._drop_request_scoped_media_values(
                    advanced_parameters,
                    first_frame=first_frame,
                    last_frame=last_frame,
                    provider_mapping=workflow_mapping,
                )
                for key, value in advanced_parameters.items():
                    self._set_workflow_path(
                        workflow_payload,
                        key,
                        value,
                        overwrite=False,
                    )
                return {
                    "prompt": workflow_payload,
                    "client_id": str(kwargs.get("idempotency_key") or uuid.uuid4().hex),
                }
            return {
                "prompt": input_payload,
                "client_id": str(kwargs.get("idempotency_key") or uuid.uuid4().hex),
            }
        if self.family is VideoProtocolFamily.AUTODL_COMFYUI:
            return self._build_autodl_payload(
                prompt=prompt,
                images=images,
                audios=audios,
                duration=duration,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                kwargs=kwargs,
                mode=mode,
                first_frame=first_frame,
                last_frame=last_frame,
                parameter_values=self.parameter_values,
                provider_mapping=self.provider_mapping,
                size_slots=self.size_slots,
                size_field=self.size_field,
            )
        return input_payload

    def _resolve_size_slot(self, aspect_ratio: object) -> str | None:
        """Resolve a canvas ratio or literal slot to one declared WxH slot."""
        if not self.size_slots:
            return None
        requested = str(aspect_ratio or "").strip().lower().replace("×", "x")
        if requested in self.size_slots:
            return requested
        parts = requested.split(":", 1) if ":" in requested else requested.split("x", 1)
        try:
            target = float(parts[0]) / float(parts[1]) if len(parts) == 2 else 0.0
        except (TypeError, ValueError, ZeroDivisionError):
            target = 0.0
        if target <= 0:
            return self.size_slots[0]
        best = self.size_slots[0]
        best_delta = float("inf")
        for slot in self.size_slots:
            slot_parts = slot.split("x", 1)
            try:
                ratio = float(slot_parts[0]) / float(slot_parts[1])
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            delta = abs(ratio - target)
            if delta < best_delta:
                best_delta = delta
                best = slot
        return best

    @staticmethod
    def _build_autodl_payload(
        *,
        prompt: str,
        images: list[str],
        audios: list[str],
        duration: float,
        aspect_ratio: str,
        resolution: str,
        kwargs: Mapping[str, object],
        parameter_values: Mapping[str, object] | None = None,
        provider_mapping: Mapping[str, object] | None = None,
        size_slots: Iterable[str] = (),
        size_field: str = "size",
        mode: str | None = None,
        first_frame: str = "",
        last_frame: str = "",
    ) -> dict[str, object]:
        """Compile AutoDL's workflow-wrapper fields, not native ComfyUI JSON."""
        raw_resolution = str(resolution or "").strip()
        normalized_resolution = raw_resolution.casefold().replace("×", "x")
        # Keep a provider-native resolution label intact when discovery
        # returned an aspect-specific enum such as ``480p(1:1)``.  Inferring
        # orientation from the separate aspect-ratio field would otherwise
        # turn square output into ``480p横`` and lose the upstream mapping.
        native_resolution_label = re.fullmatch(
            r"[1-9][0-9]{2,5}p(?:横|竖|\([^)]*\))",
            raw_resolution,
            re.IGNORECASE,
        )
        if native_resolution_label:
            autodl_resolution = raw_resolution
        elif "竖" in raw_resolution or "横" in raw_resolution:
            autodl_resolution = raw_resolution
        else:
            if normalized_resolution.startswith("480"):
                quality = "480p"
            elif normalized_resolution.startswith("768"):
                quality = "768p"
            else:
                quality = "768p"
            try:
                width, height = str(aspect_ratio or "16:9").replace("x", ":").split(":", 1)
                vertical = float(width) < float(height)
            except (TypeError, ValueError):
                vertical = False
            autodl_resolution = f"{quality}{'竖' if vertical else '横'}"
        payload: dict[str, object] = {
            "prompt": str(prompt or "").strip(),
            "duration": max(1, int(round(float(duration)))),
            "resolution": autodl_resolution,
        }
        payload = apply_video_common_parameter_mapping(
            payload,
            {
                "duration": max(1, int(round(float(duration)))),
                "aspectRatio": str(aspect_ratio or ""),
                "resolution": autodl_resolution,
            },
            provider_mapping,
        )
        if mode:
            payload = apply_video_common_parameter_mapping(
                payload,
                {"mode": mode},
                provider_mapping,
            )
        payload = GenericVideoAdapterGenerator._apply_frame_mapping(
            payload,
            first_frame=first_frame,
            last_frame=last_frame,
            provider_mapping=provider_mapping,
        )
        slots = [str(item).strip().lower().replace("×", "x") for item in size_slots if str(item).strip()]
        if slots:
            requested = str(aspect_ratio or "").strip().lower().replace("×", "x")
            if requested in slots:
                size_value = requested
            else:
                try:
                    left, right = requested.replace("x", ":").split(":", 1)
                    target = float(left) / float(right)
                except (TypeError, ValueError, ZeroDivisionError):
                    target = 0.0
                size_value = slots[0]
                if target > 0:
                    size_value = min(
                        slots,
                        key=lambda item: abs(
                            float(item.split("x", 1)[0]) / float(item.split("x", 1)[1]) - target
                        ),
                    )
            size_key = (
                GenericVideoAdapterGenerator._mapped_parameter_key(
                    provider_mapping, "size"
                )
                or str(size_field or "size").strip()
            )
            if size_key:
                payload[size_key] = size_value
        mapped_parameters = compile_video_provider_parameters(
            parameter_values, provider_mapping
        )
        for canonical in ("duration", "resolution", "aspectRatio"):
            GenericVideoAdapterGenerator._drop_request_scoped_parameter_values(
                mapped_parameters,
                canonical=canonical,
                provider_mapping=provider_mapping,
            )
        if slots:
            GenericVideoAdapterGenerator._drop_request_scoped_parameter_values(
                mapped_parameters,
                canonical="size",
                provider_mapping=provider_mapping,
            )
        if mode:
            for alias in (
                "mode",
                "gen_mode",
                "genMode",
                "generation_mode",
                "generationMode",
            ):
                mapped_parameters.pop(alias, None)
            mapped_mode_key = next(
                (
                    str(value).strip()
                    for key, value in (provider_mapping or {}).items()
                    if canonical_parameter_key(key) == "mode" and str(value).strip()
                ),
                "",
            )
            if mapped_mode_key:
                mapped_parameters.pop(mapped_mode_key, None)
            GenericVideoAdapterGenerator._drop_explicit_mode_media_values(
                mapped_parameters,
                provider_mapping=provider_mapping,
            )
        GenericVideoAdapterGenerator._drop_request_scoped_media_values(
            mapped_parameters,
            first_frame=first_frame,
            last_frame=last_frame,
            provider_mapping=provider_mapping,
        )
        payload.update(mapped_parameters)
        seed = kwargs.get("seed")
        if seed is not None and str(seed).strip():
            try:
                payload["seed"] = int(seed)
            except (TypeError, ValueError):
                pass
        for index, value in enumerate(images[:9]):
            if value:
                payload[f"ref_image_{index}"] = value
        for index, value in enumerate(audios[:3]):
            if value:
                payload[f"ref_audio_{index}"] = value
        return payload

    async def _media_value(self, value: object, *, kind: str = "image") -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if text.startswith("https://"):
            return text
        if text.startswith("http://"):
            if self.family is VideoProtocolFamily.TASK_QUERY:
                raise GenericVideoAdapterError(
                    "该视频协议要求公网 HTTPS 素材 URL",
                    stage="prepare",
                    error_code="VIDEO_REFERENCE_URL_REQUIRED",
                    request_contract={
                        "media_input": "http_url",
                        "provider_requires": "https",
                    },
                )
            if self.family is not VideoProtocolFamily.AUTODL_COMFYUI:
                return text
        if text.startswith("data:") and self.family not in {
            VideoProtocolFamily.AUTODL_COMFYUI,
            VideoProtocolFamily.TASK_QUERY,
        }:
            return text
        if self.family in {
            VideoProtocolFamily.AUTODL_COMFYUI,
            VideoProtocolFamily.TASK_QUERY,
        }:
            return await self._public_media_url(text, kind=kind)
        path = Path(text)
        if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
            return ""
        content = await asyncio.to_thread(path.read_bytes)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"

    async def _public_media_url(self, value: str, *, kind: str) -> str:
        """Return a provider-fetchable URL for local or inline media.

        Native MiniMax v2 rejects local/data URLs and requires public HTTPS,
        while AutoDL requires HTTP(S). Reuse the configured public relay for
        both and fail before submit when no acceptable URL can be produced.
        """
        text = str(value or "").strip()
        if text.startswith(("http://", "https://")):
            if (
                self.family is VideoProtocolFamily.TASK_QUERY
                and not text.startswith("https://")
            ):
                raise GenericVideoAdapterError(
                    "该视频协议要求公网 HTTPS 素材 URL",
                    stage="prepare",
                    error_code="VIDEO_REFERENCE_URL_REQUIRED",
                    request_contract={
                        "media_input": "http_url",
                        "provider_requires": "https",
                    },
                )
            return text

        default_ext = {"image": "png", "video": "mp4", "audio": "mp3"}.get(
            str(kind or "image").strip().lower(), "bin"
        )
        ext = default_ext
        if text.startswith("data:"):
            header, separator, encoded = text.partition(",")
            if ";base64" not in header or not separator or not encoded:
                raise GenericVideoAdapterError(
                    "参考素材必须是可访问的媒体 URL",
                    stage="prepare",
                    error_code="VIDEO_REFERENCE_URL_REQUIRED",
                    request_contract={
                        "media_input": "data_url",
                        "provider_requires": (
                            "https"
                            if self.family is VideoProtocolFamily.TASK_QUERY
                            else "http(s)"
                        ),
                    },
                )
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError) as exc:
                raise GenericVideoAdapterError(
                    "参考素材 data URL 无效",
                    stage="prepare",
                    error_code="VIDEO_REFERENCE_URL_REQUIRED",
                    request_contract={"media_input": "invalid_data_url"},
                ) from exc
            mime_type = header.removeprefix("data:").split(";", 1)[0].strip().lower()
            ext = {
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
            }.get(mime_type, default_ext)
        else:
            path = Path(text)
            if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
                raise GenericVideoAdapterError(
                    "参考素材必须是可访问的媒体 URL",
                    stage="prepare",
                    error_code="VIDEO_REFERENCE_URL_REQUIRED",
                    request_contract={
                        "media_input": "local_file_missing_or_too_large",
                        "provider_requires": (
                            "https"
                            if self.family is VideoProtocolFamily.TASK_QUERY
                            else "http(s)"
                        ),
                    },
                )
            content = await asyncio.to_thread(path.read_bytes)
            ext = path.suffix.lstrip(".") or default_ext

        try:
            url = await asyncio.to_thread(
                upload_media_bytes,
                content,
                ext=ext,
                image_transform=(
                    IMAGE_TRANSFORM_AI_REFERENCE_JPEG
                    if str(kind or "image").strip().lower() == "image"
                    else None
                ),
            )
        except MediaRelayConfigError as exc:
            raise GenericVideoAdapterError(
                "参考素材需要已配置的公共媒体中转 URL",
                stage="prepare",
                error_code="VIDEO_REFERENCE_URL_REQUIRED",
                request_contract={
                    "media_input": "local_or_data_url",
                    "provider_requires": (
                        "https"
                        if self.family is VideoProtocolFamily.TASK_QUERY
                        else "http(s)"
                    ),
                },
            ) from exc
        if (
            self.family is VideoProtocolFamily.TASK_QUERY
            and not str(url).startswith("https://")
        ):
            raise GenericVideoAdapterError(
                "媒体中转未返回公网 HTTPS 素材 URL",
                stage="prepare",
                error_code="VIDEO_REFERENCE_URL_REQUIRED",
                request_contract={
                    "media_input": "relayed_url",
                    "provider_requires": "https",
                },
            )
        return str(url)

    def _find_result_url(self, value: object) -> str:
        if isinstance(value, str):
            return value.strip() if value.startswith(("http://", "https://", "data:")) else ""
        if isinstance(value, Mapping):
            for key in ("url", "video_url", "download_url", "uri", "href", "output"):
                found = self._find_result_url(value.get(key))
                if found:
                    return found
            for nested in value.values():
                found = self._find_result_url(nested)
                if found:
                    return found
        if isinstance(value, (list, tuple)):
            for nested in value:
                found = self._find_result_url(nested)
                if found:
                    return found
        return ""

    def _workflow_result_url(self, payload: Mapping[str, object]) -> str:
        filename = self._find_string(payload, ("filename", "file_name"))
        if not filename:
            return ""
        query = urlencode(
            {
                "filename": filename,
                "subfolder": self._find_string(payload, ("subfolder",)) or "",
                "type": self._find_string(payload, ("type",)) or "output",
            }
        )
        return f"{self.base_url}/view?{query}"

    @staticmethod
    def _find_string(payload: Mapping[str, object], keys: tuple[str, ...]) -> str:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            if isinstance(value, Mapping):
                found = GenericVideoAdapterGenerator._find_string(value, keys)
                if found:
                    return found
        return ""

    @staticmethod
    def _response_keys(payload: object) -> list[str]:
        return sorted(str(key) for key in payload)[:24] if isinstance(payload, Mapping) else []

    @classmethod
    def _response_contract(cls, payload: object) -> dict[str, object]:
        """Keep enough submit evidence to distinguish business errors from bad mappings."""
        if not isinstance(payload, Mapping):
            return {"response_keys": cls._response_keys(payload)}
        data = payload.get("data")
        if data is None:
            data_type = "null"
        elif isinstance(data, Mapping):
            data_type = "object"
        elif isinstance(data, list):
            data_type = "array"
        elif isinstance(data, str):
            data_type = "string"
        elif isinstance(data, bool):
            data_type = "boolean"
        elif isinstance(data, (int, float)):
            data_type = "number"
        else:
            data_type = type(data).__name__.lower()
        result: dict[str, object] = {
            "response_keys": cls._response_keys(payload),
            "response_data_type": data_type,
            "response_message_present": bool(str(payload.get("msg") or "").strip()),
        }
        code = str(payload.get("code") or "").strip()
        if code and len(code) <= 80 and re.fullmatch(r"[A-Za-z0-9_.:-]+", code):
            result["response_code"] = code
        message = redact_secrets(str(payload.get("msg") or "")).strip()
        if message:
            result["response_message"] = " ".join(message.split())[:240]
        if isinstance(data, Mapping):
            result["response_data_keys"] = sorted(str(key) for key in data)[:24]
        return result

    @classmethod
    def _response_indicates_rejection(cls, payload: object) -> bool:
        if not isinstance(payload, Mapping):
            return False
        code = str(payload.get("code") or "").strip().casefold()
        message_present = bool(str(payload.get("msg") or "").strip())
        if code and code not in {"success", "ok", "succeeded", "completed", "done", "0", "200"}:
            return True
        return not code and message_present


__all__ = ["GenericVideoAdapterError", "GenericVideoAdapterGenerator"]
