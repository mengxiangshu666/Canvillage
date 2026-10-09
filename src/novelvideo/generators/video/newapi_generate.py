"""Large execution mixin for the NewAPI video adapter."""

from __future__ import annotations

import asyncio
import math
import os
from datetime import datetime
from typing import Callable, Optional

from novelvideo.generators.video.direct_video_h3_routing import (
    activate_native_minimax_v2_if_available,
    h3_safe_resolution,
    is_h3_size_rejection,
)
from novelvideo.generators.video.direct_video_protocol_contracts import (
    DIRECT_VIDEO_PROTOCOL_MINIMAX_V2,
    DIRECT_VIDEO_PROTOCOL_OPENAI,
    build_minimax_video_v2_payload,
)
from novelvideo.generators.video.newapi_video_diagnostics import NewApiVideoError
from novelvideo.generators.video.result_provenance import (
    result_belongs_to_provider_task,
    write_result_provider_task,
)
from novelvideo.generators.video.upstream_profiles import (
    compile_payload,
    compiled_aspect_ratio,
    compiled_duration,
    resolve_profile,
)
from novelvideo.shared.billing_errors import is_insufficient_credits_error
from novelvideo.shared.provider_cost import extract_provider_cost_evidence
from novelvideo.storage.media_relay import IMAGE_TRANSFORM_AI_REFERENCE_JPEG
from novelvideo.video_request_usage import (
    record_video_request,
    update_video_request_status,
)

from .base import (
    VideoGenResult,
    VideoGenStatus,
    normalize_video_aspect_ratio as _normalize_video_aspect_ratio,
)
from .runtime import (
    NEWAPI_VIDEO_QUERY_RETRIES,
    NEWAPI_VIDEO_SUBMIT_RETRIES,
    PROMPT_HUBS_EARLY_FAILURE_GRACE_POLLS,
    VIDEO_EARLY_FAILURE_GRACE_POLLS,
    invoke_confirm_video_model_call as _confirm_video_model_call,
    invoke_refund_video_model_call as _refund_video_model_call,
    invoke_reserve_video_model_call as _reserve_video_model_call,
    newapi_video_submit_retry_delay_seconds,
)
from .seedance2 import (
    ShotReference,
    _seedance2_config_mapping,
    _seedance2_duration_from_config,
)

__all__ = ["NewApiGenerateMixin"]


class NewApiGenerateMixin:
        def _wire_profile(self):
            """出线合同：渠道条目带了自己的那份就用它，否则按模型名字猜。

            渠道自带的合同存在渠道记录里，渠道删掉它就没了；名字猜出来的那份来自源码
            种子，只是「未验证建议」。
            """

            declared = getattr(self, "wire_profile", None)
            if declared is not None:
                return declared
            return resolve_profile(self.upstream_model)

        async def generate(
            self,
            image_path: Optional[str],
            prompt: str,
            output_path: str,
            aspect_ratio: str = "9:16",
            duration: float = 5.0,
            poll_interval: float = 5.0,
            max_polls: int = 720,
            on_log: Optional[Callable[[str], None]] = None,
            on_progress: Optional[Callable[[float], None]] = None,
            on_task_event: Optional[Callable[[dict[str, object]], None]] = None,
            last_frame_path: Optional[str] = None,
            **kwargs,
        ) -> VideoGenResult:
            def log(msg: str):
                if on_log:
                    on_log(msg)

            def progress(value: float):
                if on_progress:
                    on_progress(value)

            def emit_task_event(stage: str, **details: object) -> None:
                """Best-effort lifecycle hand-off for durable task state.

                The UI state writer lives above the provider adapter.  An observer
                must never make a paid generation fail, so callback errors are
                intentionally isolated here.
                """
                if not on_task_event:
                    return
                try:
                    on_task_event({"stage": stage, "model": self.model, **details})
                except Exception:
                    pass

            if await activate_native_minimax_v2_if_available(self):
                log("MiniMax-H3 原生路由已确认，使用站点原生 /v2 协议提交。")

            project_output_dir = kwargs.get("project_output_dir")
            tracking_episode = kwargs.get("episode")
            tracking_beat_num = kwargs.get("beat_num")
            tracking_task_type = kwargs.get("task_type", "")
            tracking_cost_estimate = kwargs.get("cost_estimate")
            raw_explicit_video_mode = str(
                getattr(
                    kwargs.get("gen_mode") or kwargs.get("mode"),
                    "value",
                    kwargs.get("gen_mode") or kwargs.get("mode") or "",
                )
                or ""
            ).strip()
            explicit_video_mode = self._normalize_explicit_video_mode(
                raw_explicit_video_mode
            )
            if raw_explicit_video_mode and not explicit_video_mode:
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error=f"视频模式 {raw_explicit_video_mode!r} 不在模型能力合同中",
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
                                    "details": {"requestedMode": raw_explicit_video_mode},
                                }
                            ]
                        },
                    },
                )

            # 花钱之前先确认凭据记录还作数：目录接口不校验 Key，所以「上次检测
            # 通过」可能在密钥被吊销后仍然亮着。这里只在记录过期时重验一次，
            # 通过就静默刷新，拿不到结论照常提交，只有上游明确拒绝才拦。
            stale_credential_error = await self._recheck_stale_credential(self.base_url)
            if stale_credential_error:
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error=f"视频生成失败：{stale_credential_error}",
                    error_metadata={
                        "error_code": "VIDEO_AUTH_REJECTED",
                        "endpoint_class": "video-auth",
                        "stage": "preflight",
                        "verification_stage": "credential",
                        "retryable": False,
                        "suggested_action": "在模型中心重新检测该渠道并确认密钥有效后重试。",
                    },
                )

            def record_accepted(request_id: str):
                if not project_output_dir or not request_id:
                    return
                try:
                    record_video_request(
                        project_output_dir=project_output_dir,
                        request_id=request_id,
                        provider="newapi",
                        model_name=self.model,
                        episode=tracking_episode,
                        beat_num=tracking_beat_num,
                        task_type=tracking_task_type,
                        duration_seconds=float(duration),
                        cost_estimate=tracking_cost_estimate,
                    )
                except Exception as exc:
                    log(f"记账失败(accepted): {exc}")

            def update_request_status(
                request_id: str,
                status: str,
                error_message: str | None = None,
            ):
                if not project_output_dir or not request_id:
                    return
                try:
                    update_video_request_status(
                        project_output_dir=project_output_dir,
                        request_id=request_id,
                        status=status,
                        error_message=error_message,
                    )
                except Exception as exc:
                    log(f"记账失败({status}): {exc}")

            is_village_canvas_routed_video_model = self._is_village_canvas_routed_video_model()
            is_wokey_jimeng_seedance2_model = self._is_wokey_jimeng_seedance2_model()
            is_seedance2_model = self._is_seedance2_model()
            is_firefly_seedance2_model = self._is_firefly_seedance2_model()
            is_kacang_mini_h3_model = self._is_kacang_mini_h3_model()
            is_prompt_hubs_flex_video_model = self._is_prompt_hubs_flex_video_model()
            is_prompt_hubs_sd_video_model = self._is_prompt_hubs_sd_video_model()
            is_prompt_hubs_face_lock_video_model = (
                self._is_prompt_hubs_face_lock_video_model()
            )
            is_newapi_video_v1_model = self._uses_newapi_video_v1_payload()
            seedance2_config = (
                _seedance2_config_mapping(kwargs.get("seedance2_config"))
                if (
                    is_seedance2_model
                    or is_wokey_jimeng_seedance2_model
                    or is_firefly_seedance2_model
                    or is_prompt_hubs_flex_video_model
                    or is_prompt_hubs_sd_video_model
                )
                else {}
            )
            duration = _seedance2_duration_from_config(seedance2_config, duration)
            limited_prompt = str(seedance2_config.get("final_prompt") or prompt or "").strip() if (is_firefly_seedance2_model or is_prompt_hubs_flex_video_model or is_prompt_hubs_sd_video_model) else prompt
            if (self._is_happyhorse_model() or is_firefly_seedance2_model or is_prompt_hubs_flex_video_model or is_prompt_hubs_sd_video_model) and len(limited_prompt) > 2500:
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error="视频提示词超过2500字符上限；请压缩重复描述并保留动作结局、资产和画质要求，或更换模型。未截断或提交。",
                    error_metadata={
                        "error_code": "VIDEO_PROMPT_LIMIT_EXCEEDED",
                        "endpoint_class": "video-request-contract",
                        "stage": "preflight",
                        "verification_stage": "contract",
                        "retryable": False,
                        "suggested_action": "压缩重复描述并保留动作结局、资产和画质要求，或选择更高提示词容量的模型。",
                    },
                )
            min_duration, max_duration = self._duration_bounds()
            original_duration = duration
            duration = max(min_duration, min(max_duration, math.ceil(duration)))
            if is_village_canvas_routed_video_model:
                duration = self._village_canvas_routed_duration(duration)
            if duration != original_duration:
                log(f"时长已调整: {original_duration:.1f}s -> {duration:.0f}s")

            ratio = self._resolve_allowed_aspect_ratio(
                _normalize_video_aspect_ratio(aspect_ratio)
            )
            image_path = str(image_path or "").strip()
            image_path, last_frame_path, request_references = (
                self._filter_explicit_media_inputs(
                    mode=explicit_video_mode,
                    image_path=image_path,
                    last_frame_path=last_frame_path,
                    references=kwargs.get("references") or (),
                )
            )
            explicit_media_error = self._explicit_media_contract_error(
                mode=explicit_video_mode,
                image_path=image_path,
                last_frame_path=last_frame_path,
                references=request_references,
            )
            if explicit_media_error:
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error=explicit_media_error,
                    error_metadata={
                        "error_code": "VIDEO_CAPABILITY_CONTRACT_INVALID",
                        "endpoint_class": "video-request-contract",
                        "stage": "preflight",
                        "verification_stage": "contract",
                        "retryable": False,
                        "suggested_action": "按模型能力合同补齐所选模式的媒体输入后重试。",
                    },
                )

            # Capability discovery and built-in profiles are the authority for a
            # selected explicit mode.  Run this check before any relay upload,
            # billing reservation, or provider submission so an unsupported mode
            # cannot silently degrade inside a legacy NewAPI branch.
            if explicit_video_mode:
                from novelvideo.generators.video.direct_video_profiles import (
                    resolve_direct_video_profile,
                )

                profile = resolve_direct_video_profile(
                    self.upstream_model,
                    base_url=self.base_url,
                    protocol=self.protocol,
                )
                mode_contract_error = self._explicit_video_mode_contract_error(
                    explicit_video_mode,
                    profile=profile,
                )
                if mode_contract_error:
                    error, error_metadata = mode_contract_error
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error=error,
                        error_metadata=error_metadata,
                    )

            metadata: dict[str, object] = {
                "resolution": self.resolution,
                "ratio": ratio,
                "watermark": False,
                "generate_audio": bool(self.generate_audio),
            }
            payload: dict[str, object] = {
                "model": self.model,
                "prompt": prompt,
                "seconds": str(duration),
                "metadata": metadata,
            }
            wokey_multipart_fields: dict[str, object] | None = None
            wokey_multipart_files: list[tuple[str, str, bytes, str]] = []
            submit_idempotency_key = self._resolve_submit_idempotency_key(
                kwargs.get("idempotency_key"),
                model=self.upstream_model or self.model,
                output_path=output_path,
                prompt=prompt,
            )
            wokey_idempotency_key = ""

            if self.protocol == DIRECT_VIDEO_PROTOCOL_MINIMAX_V2:
                reference_images: list[str] = []
                reference_videos: list[str] = []
                reference_audios: list[str] = []
                first_frame_url = ""
                last_frame_url = ""
                try:
                    if image_path:
                        first_frame_url = await self._relay_frame_input(
                            image_path,
                            require_public_https=True,
                        )
                    if last_frame_path:
                        last_frame_url = await self._relay_frame_input(
                            last_frame_path,
                            require_public_https=True,
                        )
                    for reference in request_references:
                        ref_type = self._reference_kind(reference)
                        ref_path = self._reference_path(reference)
                        if not ref_path:
                            continue
                        if ref_type == "video":
                            reference_videos.append(
                                await self._relay_media_input(
                                    ref_path,
                                    default_ext="mp4",
                                    require_public_https=True,
                                )
                            )
                        elif ref_type == "audio":
                            reference_audios.append(
                                await self._relay_media_input(
                                    ref_path,
                                    default_ext="mp3",
                                    require_public_https=True,
                                )
                            )
                        elif ref_type == "image" and ref_path not in {
                            image_path,
                            last_frame_path,
                        }:
                            reference_images.append(
                                await self._relay_media_input(
                                    ref_path,
                                    default_ext="png",
                                    image_transform=IMAGE_TRANSFORM_AI_REFERENCE_JPEG,
                                    require_public_https=True,
                                )
                            )
                except Exception as exc:
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error=f"media relay upload failed: {exc}",
                    )
                payload = build_minimax_video_v2_payload(
                    model=self.upstream_model,
                    prompt=prompt,
                    resolution=self.resolution,
                    duration=int(duration),
                    ratio=ratio,
                    first_frame_url=first_frame_url,
                    last_frame_url=last_frame_url,
                    reference_images=reference_images,
                    reference_videos=reference_videos,
                    reference_audios=reference_audios,
                )
                metadata["resolution"] = payload["resolution"]
                metadata["duration"] = payload["duration"]
                metadata["ratio"] = payload.get("ratio") or "adaptive"

            elif is_newapi_video_v1_model:
                try:
                    payload = await self._build_newapi_video_v1_payload(
                        image_path=image_path,
                        last_frame_path=last_frame_path,
                        prompt=prompt,
                        duration=duration,
                        ratio=ratio,
                        references=request_references,
                        log=log,
                        mode=explicit_video_mode,
                    )
                except Exception as exc:
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error=f"media relay upload failed: {exc}",
                    )

            elif is_village_canvas_routed_video_model:
                prompt = str(prompt or "").strip()
                # 万能橡皮泥：按上游 profile 编译（huabu 5/10/15 对齐、size、image 首帧）
                upstream_profile = self._wire_profile()
                payload = compile_payload(
                    model_key=self.upstream_model,
                    prompt=prompt,
                    duration_seconds=duration,
                    aspect_ratio=ratio,
                    first_frame_uri=None,
                    profile=upstream_profile,
                )
                duration_int = compiled_duration(payload, duration)
                metadata["ratio"] = compiled_aspect_ratio(payload, ratio)
                metadata["duration"] = duration_int
                first_frame_path = image_path
                ignored_references = 0
                for ref in request_references:
                    ref_type = self._reference_kind(ref)
                    ref_path = self._reference_path(ref)
                    if ref_type != "image" or not ref_path:
                        if ref_path:
                            ignored_references += 1
                        continue
                    if not first_frame_path:
                        first_frame_path = ref_path
                    elif ref_path != first_frame_path:
                        ignored_references += 1
                if last_frame_path:
                    ignored_references += 1
                    log("官方 Key 视频路由当前按 OpenAI Video 兼容格式提交，已忽略尾帧输入")
                if ignored_references:
                    log("官方 Key 视频路由当前使用首图/单图输入，其余参考素材已忽略")
                if first_frame_path:
                    try:
                        first_frame_url = await self._relay_frame_input(first_frame_path)
                        if not first_frame_path.startswith(("http://", "https://")):
                            log("视频首图已上传到媒体中转")
                        payload["image"] = first_frame_url
                    except Exception as exc:
                        return VideoGenResult(
                            status=VideoGenStatus.FAILED,
                            error=f"media relay upload failed: {exc}",
                        )

            elif is_wokey_jimeng_seedance2_model:
                prompt = str(seedance2_config.get("final_prompt") or prompt or "").strip()
                request_ratio = self._wokey_jimeng_ratio(
                    seedance2_config.get("ratio") or ratio
                )
                request_resolution = self._wokey_jimeng_resolution(
                    seedance2_config.get("resolution") or self.resolution
                )
                raw_references = request_references
                wokey_idempotency_key = submit_idempotency_key

                def reference_value(reference: object, *names: str) -> str:
                    for name in names:
                        value = self._reference_value(reference, name, "")
                        if value:
                            return str(value).strip()
                    return ""

                image_references: list[str] = []
                video_references: list[str] = []
                audio_references: list[str] = []
                for reference in raw_references:
                    reference_type = reference_value(reference, "type", "kind").lower()
                    reference_path = reference_value(reference, "path", "uri", "url")
                    if not reference_path:
                        continue
                    if reference_type == "video":
                        video_references.append(reference_path)
                    elif reference_type == "audio":
                        audio_references.append(reference_path)
                    elif reference_type == "image":
                        image_references.append(reference_path)

                if explicit_video_mode:
                    if explicit_video_mode == "textToVideo":
                        request_mode = "text_to_video"
                        wokey_sources = []
                    elif explicit_video_mode == "firstLastFrame":
                        first_frame = image_path or (
                            image_references[0] if image_references else ""
                        )
                        if not first_frame or not last_frame_path:
                            return VideoGenResult(
                                status=VideoGenStatus.FAILED,
                                error="Wokey 首尾帧模式需要首帧和尾帧",
                            )
                        request_mode = "multimodal_reference"
                        wokey_sources = [
                            ("image[]", first_frame, "png"),
                            ("image[]", str(last_frame_path), "png"),
                        ]
                    elif explicit_video_mode == "imageToVideo":
                        first_frame = image_path or (
                            image_references[0] if image_references else ""
                        )
                        if not first_frame:
                            return VideoGenResult(
                                status=VideoGenStatus.FAILED,
                                error="Wokey 图生视频模式需要一张首帧图片",
                            )
                        request_mode = "multimodal_reference"
                        wokey_sources = [("image[]", first_frame, "png")]
                    else:
                        request_mode = "multimodal_reference"
                        if explicit_video_mode == "videoEdit" and not video_references:
                            return VideoGenResult(
                                status=VideoGenStatus.FAILED,
                                error="Wokey 视频编辑模式需要视频参考素材",
                            )
                        if image_path and image_path not in image_references:
                            image_references.insert(0, image_path)
                        wokey_sources = [
                            *(("image[]", path, "png") for path in image_references[:9]),
                            *(("video[]", path, "mp4") for path in video_references[:3]),
                            *(("audio[]", path, "mp3") for path in audio_references[:3]),
                        ]
                        if explicit_video_mode == "imageReference":
                            wokey_sources = [
                                ("image[]", path, "png")
                                for path in image_references[:9]
                            ]
                        elif explicit_video_mode == "videoEdit":
                            wokey_sources = [
                                ("video[]", path, "mp4")
                                for path in video_references[:3]
                            ]
                        if not wokey_sources:
                            return VideoGenResult(
                                status=VideoGenStatus.FAILED,
                                error="Wokey 参考模式需要至少一个参考素材",
                            )
                        wokey_sources = wokey_sources[:12]
                elif last_frame_path:
                    first_frame = image_path or (
                        image_references[0] if image_references else ""
                    )
                    if not first_frame:
                        return VideoGenResult(
                            status=VideoGenStatus.FAILED,
                            error="Wokey 首尾帧模式需要首帧和尾帧",
                        )
                    request_mode = "multimodal_reference"
                    wokey_sources = [
                        ("image[]", first_frame, "png"),
                        ("image[]", str(last_frame_path), "png"),
                    ]
                    log("Wokey 当前统一按多模态参考协议提交首尾两帧")
                elif image_references or video_references or audio_references:
                    request_mode = "multimodal_reference"
                    if image_path and image_path not in image_references:
                        image_references.insert(0, image_path)
                    wokey_sources = [
                        *(("image[]", path, "png") for path in image_references[:9]),
                        *(("video[]", path, "mp4") for path in video_references[:3]),
                        *(("audio[]", path, "mp3") for path in audio_references[:3]),
                    ]
                    if len(wokey_sources) > 12:
                        wokey_sources = wokey_sources[:12]
                elif image_path:
                    request_mode = "multimodal_reference"
                    wokey_sources = [("image[]", image_path, "png")]
                else:
                    request_mode = "text_to_video"
                    wokey_sources = []

                metadata["resolution"] = request_resolution
                metadata["ratio"] = request_ratio
                metadata["duration"] = int(duration)
                if wokey_sources:
                    try:
                        for field_name, source, default_extension in wokey_sources:
                            (
                                filename,
                                content,
                                content_type,
                            ) = await self._wokey_multipart_file(
                                source,
                                default_extension=default_extension,
                            )
                            wokey_multipart_files.append(
                                (field_name, filename, content, content_type)
                            )
                    except Exception as exc:
                        return VideoGenResult(
                            status=VideoGenStatus.FAILED,
                            error=f"Wokey reference preparation failed: {exc}",
                        )
                    wokey_multipart_fields = {
                        "model": self.upstream_model,
                        "mode": request_mode,
                        "prompt": prompt,
                        "duration_seconds": int(duration),
                        "ratio": request_ratio,
                        "resolution": request_resolution,
                        "idempotency_key": wokey_idempotency_key,
                    }
                else:
                    payload = {
                        "model": self.upstream_model,
                        "mode": request_mode,
                        "prompt": prompt,
                        "duration": int(duration),
                        "ratio": request_ratio,
                        "video_resolution": request_resolution,
                    }

            elif self._is_happyhorse_model():
                duration_int = int(math.ceil(duration))
                metadata.pop("generate_audio", None)
                metadata["ratio"] = self._happyhorse_ratio(ratio)
                metadata["resolution"] = self._happyhorse_resolution(self.resolution)
                payload["duration"] = duration_int
                payload["seconds"] = str(duration_int)

                first_frame_path = ""
                reference_image_paths: list[str] = []
                video_reference_paths: list[str] = []
                for ref in request_references:
                    ref_type = self._reference_kind(ref)
                    path = self._reference_path(ref)
                    if not path:
                        continue
                    if ref_type == "video":
                        video_reference_paths.append(path)
                        continue
                    if ref_type != "image":
                        continue
                    role = str(getattr(ref, "role", "") or "").strip()
                    if "首帧" in role and not first_frame_path:
                        first_frame_path = path
                    else:
                        reference_image_paths.append(path)

                # image_path 是 run_freezone_video_gen 无条件传进来的「首张图」。仅当它
                # 还没作为参考图存在时，才把它被动提升为首帧——否则参考模式(所有图都在
                # reference_image_paths 里)会把首张图重复算一次（首帧位 + 参考位）。
                if (
                    not first_frame_path
                    and image_path
                    and image_path not in reference_image_paths
                ):
                    first_frame_path = image_path

                # HappyHorse 没有尾帧能力，且把尾帧混进 reference_images 会误触发 r2v，
                # 与首帧的 image_url 冲突（上游 INVALID_PARAMS）。这里直接忽略尾帧。
                if last_frame_path:
                    log("HappyHorse 不支持尾帧，已忽略尾帧输入")

                # HappyHorse 的 image_url(i2v) 与 reference_images(r2v/视频编辑) 互斥，
                # 不能出现在同一次请求里。策略：参考优先——只要带了参考图或参考视频，就走
                # r2v/视频编辑。此时首帧不能再走 image_url，但它的画面仍是有效参考，
                # 应降级为 reference_images 的首位（保持身份优先），而不是整张丢弃，
                # 否则「2 张图」会只发出去 1 张。纯首帧（无其它参考）才走 i2v。
                if (video_reference_paths or reference_image_paths) and first_frame_path:
                    log("HappyHorse 参考/视频编辑模式不支持首帧，已将首帧降级为参考图")
                    reference_image_paths.insert(0, first_frame_path)
                    first_frame_path = ""

                # 文档：ratio 仅 t2v/r2v 生效。首帧(i2v, image_url)与视频编辑(video_url)
                # 的画幅由输入媒体决定，不接受 ratio，误带会触发上游 INVALID_PARAMS。
                if first_frame_path or video_reference_paths:
                    metadata.pop("ratio", None)

                try:
                    if video_reference_paths:
                        video_url = await self._relay_media_input(
                            video_reference_paths[0],
                            default_ext="mp4",
                        )
                        if not video_reference_paths[0].startswith(("http://", "https://")):
                            log("视频参考已上传到媒体中转")
                        metadata["video_url"] = video_url
                        metadata["audio_setting"] = self._happyhorse_audio_setting(
                            kwargs.get("audio_setting")
                        )

                    relayed_references: list[str] = []
                    for path in reference_image_paths[: 5 if video_reference_paths else 9]:
                        url = await self._relay_frame_input(path)
                        if not path.startswith(("http://", "https://")):
                            log("图片参考已上传到媒体中转")
                        relayed_references.append(url)

                    if first_frame_path:
                        first_frame_url = await self._relay_frame_input(first_frame_path)
                        if not first_frame_path.startswith(("http://", "https://")):
                            log("首帧已上传到媒体中转")
                        metadata["image_url"] = first_frame_url
                        payload["images"] = [first_frame_url]

                    if relayed_references:
                        metadata["reference_images"] = relayed_references
                except Exception as exc:
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error=f"media relay upload failed: {exc}",
                    )

            elif self._is_minimax_hailuo_model():
                metadata["resolution"] = self._minimax_hailuo_resolution(self.resolution)
                if last_frame_path:
                    log("MiniMax Hailuo 2.3 不支持尾帧，已忽略尾帧输入")
                if image_path:
                    try:
                        first_frame_url = await self._relay_frame_input(image_path)
                        if not image_path.startswith(("http://", "https://")):
                            log("首帧已上传到媒体中转")
                        payload["images"] = [first_frame_url]
                    except Exception as exc:
                        return VideoGenResult(
                            status=VideoGenStatus.FAILED,
                            error=f"media relay upload failed: {exc}",
                        )
            elif self._is_grok_video_channel_model():
                metadata.pop("generate_audio", None)
                metadata.pop("watermark", None)
                duration_int = int(math.ceil(duration))
                payload["duration"] = duration_int
                payload["seconds"] = str(duration_int)
                first_frame_path = image_path
                reference_image_paths: list[str] = []
                for ref in request_references:
                    ref_type = self._reference_kind(ref)
                    path = self._reference_path(ref)
                    if not path or ref_type != "image":
                        continue
                    role = str(self._reference_value(ref, "role", "") or "").strip()
                    if not first_frame_path and "首帧" in role:
                        first_frame_path = path
                        continue
                    if path != first_frame_path:
                        reference_image_paths.append(path)

                try:
                    if first_frame_path:
                        first_frame_url = await self._relay_frame_input(first_frame_path)
                        if not first_frame_path.startswith(("http://", "https://")):
                            log("首帧已上传到媒体中转")
                        metadata["image_url"] = first_frame_url
                        payload["images"] = [first_frame_url]

                    relayed_references: list[str] = []
                    for path in reference_image_paths[:7]:
                        url = await self._relay_frame_input(path)
                        if not path.startswith(("http://", "https://")):
                            log("参考图片已上传到媒体中转")
                        relayed_references.append(url)
                    if relayed_references:
                        metadata["reference_images"] = relayed_references
                except Exception as exc:
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error=f"media relay upload failed: {exc}",
                    )

            elif is_prompt_hubs_flex_video_model:
                prompt = str(seedance2_config.get("final_prompt") or prompt or "").strip()
                flex_duration = self._prompt_hubs_flex_duration(duration)
                if flex_duration != int(duration):
                    log(
                        f"视频模型时长已对齐到可用档位: {int(duration)}s -> {flex_duration}s"
                    )
                duration = flex_duration
                resolution = self._prompt_hubs_flex_resolution(
                    seedance2_config.get("resolution") or self.resolution
                )
                request_ratio = self._prompt_hubs_flex_ratio(
                    seedance2_config.get("ratio") or ratio
                )
                upstream_profile = self._wire_profile()
                payload = compile_payload(
                    model_key=self.upstream_model,
                    prompt=prompt,
                    duration_seconds=duration,
                    aspect_ratio=request_ratio,
                    profile=upstream_profile,
                )
                payload["resolution"] = resolution
                metadata["resolution"] = resolution
                metadata["ratio"] = compiled_aspect_ratio(payload, request_ratio)
                metadata.pop("generate_audio", None)
                metadata.pop("watermark", None)
                if last_frame_path:
                    log("Prompt-Hubs 当前视频模型不支持尾帧，已忽略尾帧输入")

                reference_image_paths: list[str] = []
                source_video_path = ""
                # The explicit image-to-video contract has a dedicated first-frame
                # field.  Keep it out of ``referenceImages``; the latter is for
                # ordinary reference material and changes the upstream operation.
                first_frame_path = (
                    image_path
                    if explicit_video_mode == "imageToVideo" and image_path
                    else ""
                )
                if image_path and not first_frame_path:
                    reference_image_paths.append(image_path)
                for ref in request_references:
                    ref_type = self._reference_kind(ref)
                    ref_path = self._reference_path(ref)
                    if (
                        self.model.strip().lower() == "kling-v3-omni-v2v-create"
                        and ref_type == "video"
                        and not source_video_path
                    ):
                        source_video_path = ref_path
                        continue
                    if (
                        not ref_path
                        or ref_type != "image"
                        or ref_path in reference_image_paths
                    ):
                        continue
                    reference_image_paths.append(ref_path)

                if (
                    self.model.strip().lower() == "kling-v3-omni-v2v-create"
                    and not source_video_path
                ):
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error="Kling V3 Omni 视频重绘需要接入一条源视频",
                    )

                try:
                    if first_frame_path:
                        first_frame_field = upstream_profile.first_frame_field
                        if not first_frame_field:
                            return VideoGenResult(
                                status=VideoGenStatus.FAILED,
                                error=(
                                    "imageToVideo is not supported by the selected "
                                    "video model contract"
                                ),
                                error_metadata={
                                    "error_code": "VIDEO_CAPABILITY_CONTRACT_INVALID",
                                    "endpoint_class": "video-request-contract",
                                    "stage": "preflight",
                                    "verification_stage": "contract",
                                    "retryable": False,
                                    "suggested_action": "按模型能力合同选择支持首帧输入的模式后重试。",
                                },
                            )
                        first_frame_url = await self._relay_frame_input(first_frame_path)
                        if not first_frame_path.startswith(("http://", "https://")):
                            log("首帧已上传到媒体中转")
                        payload[first_frame_field] = first_frame_url
                    if source_video_path:
                        source_video_url = await self._relay_media_input(
                            source_video_path,
                            default_ext="mp4",
                        )
                        if not source_video_path.startswith(("http://", "https://")):
                            log("源视频已上传到媒体中转")
                        payload["video_url"] = source_video_url
                    relayed_references: list[str] = []
                    for path in reference_image_paths[
                        : self._prompt_hubs_flex_reference_image_limit()
                    ]:
                        url = await self._relay_frame_input(path)
                        if not path.startswith(("http://", "https://")):
                            log("视频参考图已上传到媒体中转")
                        relayed_references.append(url)
                    if relayed_references:
                        payload["referenceImages"] = relayed_references
                except Exception as exc:
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error=f"media relay upload failed: {exc}",
                    )

            elif is_kacang_mini_h3_model:
                if image_path or request_references or last_frame_path:
                    log("Mini H3 当前为文生视频模式，已忽略参考素材")
                payload = compile_payload(
                    model_key=self.upstream_model,
                    prompt=str(prompt or "").strip(),
                    duration_seconds=duration,
                    aspect_ratio=ratio,
                    profile=self._wire_profile(),
                )
                metadata["ratio"] = compiled_aspect_ratio(payload, ratio)
                metadata["duration"] = compiled_duration(payload, duration)
                metadata["resolution"] = "2k"

            elif is_firefly_seedance2_model or is_prompt_hubs_sd_video_model:
                # Prompt-Hubs Seedance/SD video models use the documented /v1/videos
                # contract.  It does not accept the legacy ``metadata`` wrapper or
                # Seedance2's snake_case reference fields, so build the public
                # camelCase payload explicitly.
                prompt = str(seedance2_config.get("final_prompt") or prompt or "").strip()
                if last_frame_path:
                    log("Prompt-Hubs SD 视频模型不支持尾帧，已忽略尾帧输入")
                request_resolution = (
                    self._prompt_hubs_sd_resolution(
                        seedance2_config.get("resolution") or self.resolution
                    )
                    if is_prompt_hubs_sd_video_model
                    else self._firefly_resolution()
                )
                # 万能橡皮泥：按上游 profile 编译（按秒计费自由时长、ratio、auto_face）。
                # 两个 Prompt-Hubs 系列都只接受 16:9 / 9:16 / 1:1；先保留原有
                # 归一化契约，避免新 profile 让旧项目的 ``21:9`` 直达上游。
                request_ratio = self._firefly_ratio(seedance2_config.get("ratio") or ratio)
                upstream_profile = self._wire_profile()
                payload = compile_payload(
                    model_key=self.upstream_model,
                    prompt=prompt,
                    duration_seconds=duration,
                    aspect_ratio=request_ratio,
                    auto_face=self._coerce_bool(
                        seedance2_config.get(
                            "auto_face",
                            os.environ.get("NEWAPI_VIDEO_AUTO_FACE", "false"),
                        ),
                        default=False,
                    ),
                    profile=upstream_profile,
                )
                payload["resolution"] = request_resolution
                metadata["resolution"] = request_resolution
                metadata["ratio"] = compiled_aspect_ratio(payload, request_ratio)
                if is_firefly_seedance2_model and str(
                    self.resolution or ""
                ).strip().lower() not in {
                    "",
                    request_resolution,
                }:
                    log("Firefly Seedance2 已忽略与模型不兼容的清晰度设置")

                reference_values: dict[str, list[str]] = {
                    "referenceImages": [],
                    "referenceVideos": [],
                    "referenceAudios": [],
                }
                seen_sources: set[str] = set()
                total_references = 0

                async def relay_reference(path: str, ref_type: str, label: str) -> None:
                    nonlocal total_references
                    source = str(path or "").strip()
                    if not source or source in seen_sources or total_references >= 12:
                        return
                    key_by_type = {
                        "image": "referenceImages",
                        "video": "referenceVideos",
                        "audio": "referenceAudios",
                    }
                    key = key_by_type.get(ref_type)
                    if not key:
                        return
                    limits = (
                        self._prompt_hubs_sd_reference_limits()
                        if is_prompt_hubs_sd_video_model
                        else {
                            "referenceImages": 9,
                            "referenceVideos": 3,
                            "referenceAudios": 3,
                        }
                    )
                    if len(reference_values[key]) >= limits[key]:
                        return
                    url = await self._relay_media_input(
                        source,
                        default_ext={"image": "png", "video": "mp4", "audio": "mp3"}[
                            ref_type
                        ],
                        image_transform=IMAGE_TRANSFORM_AI_REFERENCE_JPEG
                        if ref_type == "image"
                        else None,
                    )
                    if not source.startswith(("http://", "https://")):
                        log(f"{label}已上传到媒体中转")
                    reference_values[key].append(url)
                    seen_sources.add(source)
                    total_references += 1

                first_frame_path = (
                    image_path
                    if explicit_video_mode == "imageToVideo" and image_path
                    else ""
                )
                try:
                    # The first frame is the first reference image, preserving
                    # identity while remaining compatible with the public API.  A
                    # selected image-to-video mode uses the profile's dedicated
                    # first-frame field rather than the ordinary reference slot.
                    if first_frame_path:
                        first_frame_field = upstream_profile.first_frame_field
                        if not first_frame_field:
                            return VideoGenResult(
                                status=VideoGenStatus.FAILED,
                                error=(
                                    "imageToVideo is not supported by the selected "
                                    "video model contract"
                                ),
                                error_metadata={
                                    "error_code": "VIDEO_CAPABILITY_CONTRACT_INVALID",
                                    "endpoint_class": "video-request-contract",
                                    "stage": "preflight",
                                    "verification_stage": "contract",
                                    "retryable": False,
                                    "suggested_action": "按模型能力合同选择支持首帧输入的模式后重试。",
                                },
                            )
                        first_frame_url = await self._relay_frame_input(first_frame_path)
                        if not first_frame_path.startswith(("http://", "https://")):
                            log("首帧已上传到媒体中转")
                        payload[first_frame_field] = first_frame_url
                    elif image_path:
                        await relay_reference(image_path, "image", "首帧")
                    for ref in request_references:
                        ref_type = self._reference_kind(ref)
                        ref_path = self._reference_path(ref)
                        await relay_reference(
                            ref_path,
                            ref_type,
                            {
                                "image": "图片参考",
                                "video": "视频参考",
                                "audio": "音频参考",
                            }.get(ref_type, "参考素材"),
                        )
                except Exception as exc:
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error=f"media relay upload failed: {exc}",
                    )

                for key, values in reference_values.items():
                    if values:
                        payload[key] = values
                if (
                    is_prompt_hubs_face_lock_video_model
                    and reference_values["referenceImages"]
                ):
                    # The current upstream rejects portrait material in its image
                    # field but accepts it in the card-face reference slot.  Keep
                    # the identity/control frame in referenceImages and explicitly
                    # turn on the model's face-consistency mode.
                    payload["auto_face"] = True

            elif self._uses_documented_openai_video_payload():
                # 该渠道的公开文档就是 OpenAI 报文，模型名里的 Seedance 只是
                # SKU 叫法；不能让按名字命中的即梦分支接管报文编译。
                payload = await self._build_documented_openai_video_payload(
                    image_path=image_path,
                    last_frame_path=last_frame_path,
                    prompt=prompt,
                    duration=duration,
                    ratio=ratio,
                    references=request_references,
                    log=log,
                )
                metadata["duration"] = compiled_duration(payload, duration)
                metadata["ratio"] = compiled_aspect_ratio(payload, ratio)

            elif is_seedance2_model:
                from novelvideo.seedance2_i2v.models import (
                    Seedance2I2VMode,
                    normalize_seedance2_generation_mode,
                )
                from novelvideo.seedance2_i2v.request import build_seedance2_huimeng_params

                selected_seedance_mode = normalize_seedance2_generation_mode(
                    explicit_video_mode
                )
                source_references = list(request_references)

                def reference_kind(reference: object) -> str:
                    value = self._reference_value(reference, "type", None) or self._reference_value(
                        reference, "kind", None
                    )
                    value = getattr(value, "value", value)
                    kind = str(value or "image").strip().lower()
                    return kind if kind in {"image", "video", "audio"} else "image"

                def reference_path(reference: object) -> str:
                    return self._reference_path(reference)

                def first_image_source() -> str:
                    if image_path:
                        return str(image_path)
                    for reference in source_references:
                        if reference_kind(reference) != "image":
                            continue
                        source = reference_path(reference)
                        if source:
                            return str(source)
                    return ""

                try:
                    # An explicit mode is resolved before any relay call. This is
                    # important for retries: stale canvas media must not turn a
                    # text request into a failed upload or a different provider
                    # contract.
                    if selected_seedance_mode is None:
                        # Preserve legacy inference when no recognized explicit
                        # mode was supplied: actual media determines the request.
                        first_frame = (
                            await self._relay_frame_input(image_path) if image_path else ""
                        )
                        if image_path and not image_path.startswith(("http://", "https://")):
                            log("首帧已上传到媒体中转")
                        last_frame = (
                            await self._relay_frame_input(str(last_frame_path))
                            if last_frame_path
                            else ""
                        )
                        if last_frame_path and not str(last_frame_path).startswith(
                            ("http://", "https://")
                        ):
                            log("尾帧已上传到媒体中转")
                        reference_params = await self._relay_seedance2_references(
                            source_references,
                            log=log,
                        )
                    elif selected_seedance_mode == Seedance2I2VMode.TEXT_TO_VIDEO:
                        first_frame = ""
                        last_frame = ""
                        reference_params = {}
                    elif selected_seedance_mode == Seedance2I2VMode.FIRST_FRAME:
                        first_source = first_image_source()
                        first_frame = (
                            await self._relay_frame_input(first_source)
                            if first_source
                            else ""
                        )
                        if first_source and not first_source.startswith(("http://", "https://")):
                            log("首帧已上传到媒体中转")
                        last_frame = ""
                        reference_params = {}
                    elif selected_seedance_mode == Seedance2I2VMode.FIRST_LAST_FRAME:
                        first_source = first_image_source()
                        first_frame = (
                            await self._relay_frame_input(first_source)
                            if first_source
                            else ""
                        )
                        if first_source and not first_source.startswith(("http://", "https://")):
                            log("首帧已上传到媒体中转")
                        last_source = str(last_frame_path or "").strip()
                        last_frame = (
                            await self._relay_frame_input(last_source)
                            if last_source
                            else ""
                        )
                        if last_source and not last_source.startswith(("http://", "https://")):
                            log("尾帧已上传到媒体中转")
                        reference_params = {}
                    else:
                        # Keep the raw provider mode here: the typed config uses
                        # one multimodal enum for allReference/imageReference/
                        # videoEdit, but their media contracts are different.
                        if explicit_video_mode == "imageReference":
                            source_references = [
                                reference
                                for reference in source_references
                                if reference_kind(reference) == "image"
                            ]
                        elif explicit_video_mode == "videoEdit":
                            source_references = [
                                reference
                                for reference in source_references
                                if reference_kind(reference) == "video"
                            ]

                        # allReference and imageReference treat a standalone
                        # image_path as the first image reference.  videoEdit must
                        # not inherit that path because it is an image, not an edit
                        # source.  De-duplicate before relay to keep prompt labels
                        # and upstream counts deterministic.
                        if explicit_video_mode in {"allReference", "imageReference"} and image_path:
                            image_text = str(image_path).strip()
                            if image_text and not any(
                                reference_path(reference) == image_text
                                for reference in source_references
                            ):
                                source_references.insert(
                                    0,
                                    ShotReference("image", image_text, "首帧参考"),
                                )
                        first_frame = ""
                        last_frame = ""
                        reference_params = await self._relay_seedance2_references(
                            source_references,
                            log=log,
                        )
                except Exception as exc:
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error=f"media relay upload failed: {exc}",
                    )

                has_references = any(reference_params.values())
                if explicit_video_mode == "textToVideo":
                    mode = Seedance2I2VMode.TEXT_TO_VIDEO
                elif explicit_video_mode == "imageToVideo":
                    mode = Seedance2I2VMode.FIRST_FRAME
                elif explicit_video_mode == "firstLastFrame":
                    mode = Seedance2I2VMode.FIRST_LAST_FRAME
                elif explicit_video_mode in {
                    "allReference",
                    "imageReference",
                    "videoEdit",
                }:
                    mode = Seedance2I2VMode.MULTIMODAL_REFERENCE
                elif last_frame:
                    mode = Seedance2I2VMode.FIRST_LAST_FRAME
                elif has_references:
                    mode = Seedance2I2VMode.MULTIMODAL_REFERENCE
                elif first_frame:
                    mode = Seedance2I2VMode.FIRST_FRAME
                else:
                    mode = Seedance2I2VMode.TEXT_TO_VIDEO

                config_resolution = str(
                    seedance2_config.get("resolution") or self.resolution or "720p"
                ).strip()
                config_ratio = str(seedance2_config.get("ratio") or ratio or "9:16").strip()
                seedance2_config.update(
                    {
                        "mode": mode.value,
                        "final_prompt": prompt,
                        "duration": duration,
                        "resolution": config_resolution,
                        "ratio": config_ratio,
                    }
                )
                if "generate_audio" not in seedance2_config:
                    seedance2_config["generate_audio"] = bool(self.generate_audio)
                    seedance2_config["generate_audio_user_set"] = True
                elif "generate_audio_user_set" not in seedance2_config:
                    seedance2_config["generate_audio_user_set"] = True
                if "return_last_frame" not in seedance2_config:
                    seedance2_config["return_last_frame"] = False
                if "human_review" not in seedance2_config:
                    seedance2_config["human_review"] = bool(
                        kwargs.get("human_review", False)
                    )
                    seedance2_config["human_review_user_set"] = True
                elif "human_review_user_set" not in seedance2_config:
                    seedance2_config["human_review_user_set"] = True

                try:
                    seedance2_params = build_seedance2_huimeng_params(
                        seedance2_config,
                        first_frame=first_frame,
                        last_frame=last_frame,
                        reference_images=reference_params.get("reference_images"),
                        reference_videos=reference_params.get("reference_videos"),
                        reference_audios=reference_params.get("reference_audios"),
                    )
                except ValueError as exc:
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error=str(exc),
                    )

                payload["prompt"] = seedance2_params.pop("prompt")
                payload["seconds"] = str(seedance2_params.pop("duration"))
                metadata.update(seedance2_params)
            else:
                if last_frame_path:
                    if not image_path:
                        return VideoGenResult(
                            status=VideoGenStatus.FAILED,
                            error="First frame is required when last_frame_path is provided",
                        )
                    try:
                        first_frame = await self._relay_frame_input(image_path)
                        if not image_path.startswith(("http://", "https://")):
                            log("首帧已上传到媒体中转")
                    except Exception as exc:
                        return VideoGenResult(
                            status=VideoGenStatus.FAILED,
                            error=f"media relay upload failed: {exc}",
                        )
                    try:
                        last_frame = await self._relay_frame_input(str(last_frame_path))
                        if not str(last_frame_path).startswith(("http://", "https://")):
                            log("尾帧已上传到媒体中转")
                    except Exception as exc:
                        return VideoGenResult(
                            status=VideoGenStatus.FAILED,
                            error=f"media relay upload failed: {exc}",
                        )
                    metadata["content"] = [
                        {
                            "type": "image_url",
                            "image_url": {"url": first_frame},
                            "role": "first_frame",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": last_frame},
                            "role": "last_frame",
                        },
                    ]
                else:
                    from novelvideo.generators.video.direct_video_profiles import (
                        resolve_direct_video_profile,
                    )

                    profile = resolve_direct_video_profile(
                        self.upstream_model,
                        base_url=self.base_url,
                        protocol=self.protocol,
                    )
                    reference_image_paths: list[str] = []
                    if image_path:
                        reference_image_paths.append(image_path)
                    for reference in request_references:
                        ref_type = self._reference_kind(reference)
                        ref_path = self._reference_path(reference)
                        if ref_type == "image" and ref_path and ref_path not in reference_image_paths:
                            reference_image_paths.append(ref_path)
                    if reference_image_paths:
                        if "allReference" in profile.exact_canvas_modes or "imageReference" in profile.exact_canvas_modes:
                            reference_limit = profile.reference_limits.reference_images or 9
                        else:
                            reference_limit = max(1, profile.reference_limits.input_images)
                        try:
                            relayed_images: list[str] = []
                            for path in reference_image_paths[:reference_limit]:
                                relayed = await self._relay_frame_input(path)
                                if not path.startswith(("http://", "https://")):
                                    log("视频参考图已上传到媒体中转")
                                relayed_images.append(relayed)
                        except Exception as exc:
                            return VideoGenResult(
                                status=VideoGenStatus.FAILED,
                                error=f"media relay upload failed: {exc}",
                            )
                        payload["images"] = relayed_images

            self._apply_provider_parameters(
                payload,
                metadata=metadata,
                multipart_fields=wokey_multipart_fields,
            )

            # 「只能真，不能骗人」：提交前对账。编译/转写层任何静默改动都会在这里
            # 现形——宁可带着明确错误停下，也不把和所选时长不符的请求发出去。
            fidelity_error = self._duration_fidelity_error(
                body=wokey_multipart_fields
                if wokey_multipart_fields is not None
                else payload,
                requested=duration,
                contract_applies=wokey_multipart_fields is None,
            )
            if fidelity_error is not None:
                log(str(fidelity_error["message"]))
                emit_task_event(
                    "duration_contract_violation",
                    requested_duration=fidelity_error["requested"],
                    would_send_duration=fidelity_error["would_send"],
                )
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error=str(fidelity_error["message"]),
                    error_metadata={
                        "error_code": "VIDEO_DURATION_MISMATCH",
                        "endpoint_class": "video-request-contract",
                        "stage": "preflight",
                        "verification_stage": "contract",
                        "retryable": False,
                        "suggested_action": "按该模型真实支持的时长档位重新选择后重试。",
                        "request_contract": {
                            "violations": [
                                {
                                    "code": "duration_mismatch",
                                    "details": {
                                        "requestedDuration": fidelity_error["requested"],
                                        "wouldSendDuration": fidelity_error["would_send"],
                                    },
                                }
                            ]
                        },
                    },
                )

            task_id: str | None = None
            provider_request_id = ""
            reservation_id = ""
            accepted_gateway_name = ""
            try:
                model_label = self._model_label(self.model)
                request_resolution = str(
                    metadata.get("resolution") or self.resolution or ""
                ).strip()
                log(
                    f"正在提交 Village Infinite Canvas API 视频任务 ({model_label}, {duration}s, {request_resolution})..."
                )
                progress(0.1)
                reservation_id = await _reserve_video_model_call(
                    self.model,
                    source="newapi_video_generation",
                    resolution=request_resolution,
                    duration_seconds=duration,
                )
                submitted = None
                submit_errors: list[str] = []
                h3_resolution_fallback_used = False
                fallback_candidates = self.gateway_candidates or [
                    {
                        "name": "current",
                        "api_key": self.api_key,
                        "base_url": self.base_url,
                    }
                ]
                for candidate_index, candidate in enumerate(fallback_candidates):
                    candidate_name = str(
                        candidate.get("name") or candidate.get("source") or "newapi"
                    )
                    candidate_api_key = str(candidate.get("api_key") or "").strip()
                    candidate_base_url = (
                        str(candidate.get("base_url") or "").strip().rstrip("/")
                    )
                    if not candidate_api_key or not candidate_base_url:
                        continue
                    self.api_key = candidate_api_key
                    self.base_url = candidate_base_url
                    display_name = self._gateway_display_name(candidate_name)
                    submit_result_unknown = False
                    idempotency_conflict_retry_used = False
                    try:
                        if candidate_index > 0:
                            log(f"正在使用 {display_name} 兜底提交视频任务...")
                        route_paths = self._submit_route_paths(candidate_base_url)
                        attempted_paths: list[str] = []
                        last_route_error: NewApiVideoError | None = None
                        refused_path = ""
                        for route_index, submit_path in enumerate(route_paths):
                            submit_url = self._submit_url_for(candidate_base_url, submit_path)
                            if route_index > 0:
                                log(
                                    f"{display_name} 提交路由 {refused_path} 未被该网关注册，"
                                    f"使用同一幂等键改试 {submit_path}..."
                                )
                            attempted_paths.append(submit_path)
                            route_settled = False
                            for submit_attempt in range(NEWAPI_VIDEO_SUBMIT_RETRIES + 1):
                                try:
                                    if wokey_multipart_fields is not None:
                                        submitted = await self._post_multipart(
                                            submit_url,
                                            wokey_multipart_fields,
                                            wokey_multipart_files,
                                            idempotency_key=submit_idempotency_key,
                                        )
                                    else:
                                        submitted = await self._post_json(
                                            submit_url,
                                            payload,
                                            idempotency_key=submit_idempotency_key,
                                        )
                                    route_settled = True
                                    break
                                except NewApiVideoError as exc:
                                    last_route_error = exc
                                    if (
                                        not h3_resolution_fallback_used
                                        and is_h3_size_rejection(exc, payload)
                                    ):
                                        from novelvideo.generators.video.direct_video_capability_cache import (
                                            record_runtime_resolution_rejection,
                                        )

                                        try:
                                            record_runtime_resolution_rejection(
                                                base_url=candidate_base_url,
                                                protocol=self.protocol,
                                                upstream_model=self.upstream_model,
                                                resolution=str(payload.get("resolution") or ""),
                                                note="当前网关的 ComfyUI H3 适配器拒绝该清晰度；已验证可用清晰度已同步到节点。",
                                            )
                                        except (OSError, ValueError) as cache_exc:
                                            log(f"H3 运行能力缓存写入失败：{cache_exc}")
                                        safe_resolution = h3_safe_resolution(self.protocol)
                                        payload = {
                                            **payload,
                                            "resolution": safe_resolution,
                                        }
                                        h3_resolution_fallback_used = True
                                        metadata["resolution"] = "768p"
                                        request_resolution = "768p"
                                        log(
                                            "当前网关的 H3 2K 路由被适配器拒绝；"
                                            f"已使用同一幂等键降级到 {safe_resolution}，"
                                            "避免重复创建任务。"
                                        )
                                        continue
                                    if self._submit_route_is_refused(exc):
                                        # A redirect or 404/405 on submit means the
                                        # gateway never reached a create handler, so no
                                        # task exists under this idempotency key and
                                        # another registered route is safe to try.  The
                                        # key is deliberately reused.
                                        refused_path = submit_path
                                        break
                                    if (
                                        not idempotency_conflict_retry_used
                                        and submit_attempt
                                        < NEWAPI_VIDEO_SUBMIT_RETRIES
                                        and self._is_idempotency_key_conflict(exc)
                                    ):
                                        # 上游说这个键已经绑定了另一份不同内容的
                                        # 请求。旧键下不可能存在本次这份请求体对应的
                                        # 任务，换一个新键重试一次就能继续；如果旧键
                                        # 下确实已经产生过被接受的任务，则可能多一次
                                        # 计费，所以这里只重试一次并明确记日志。
                                        idempotency_conflict_retry_used = True
                                        previous_key = submit_idempotency_key
                                        submit_idempotency_key = (
                                            self._refresh_submit_idempotency_key(
                                                previous_key,
                                                attempt=submit_attempt + 1,
                                            )
                                        )
                                        if wokey_multipart_fields is not None:
                                            wokey_multipart_fields[
                                                "idempotency_key"
                                            ] = submit_idempotency_key
                                        log(
                                            "上游提示幂等键 "
                                            f"{previous_key} 已绑定另一份不同内容的请求，"
                                            f"已换用新键 {submit_idempotency_key} 重试一次。"
                                            "如果旧键下已有被接受的任务，可能产生一次额外计费。"
                                        )
                                        continue
                                    if not self._submission_result_is_unknown(exc):
                                        raise
                                    if submit_attempt >= NEWAPI_VIDEO_SUBMIT_RETRIES:
                                        raise NewApiVideoError(
                                            "视频提交结果未知：渠道可能已经接收任务，但响应超时。"
                                            "请稍后使用任务恢复，不要立即再次点击生成。"
                                            f"幂等键：{submit_idempotency_key}",
                                            request_id=exc.request_id,
                                            stage="submit",
                                            url_path=exc.url_path,
                                            submission_result_unknown=True,
                                        ) from exc
                                    retry_number = submit_attempt + 1
                                    log(
                                        f"{display_name} 视频提交响应超时，使用同一幂等键重试 "
                                        f"({retry_number}/{NEWAPI_VIDEO_SUBMIT_RETRIES})..."
                                    )
                                    await asyncio.sleep(
                                        newapi_video_submit_retry_delay_seconds()
                                        * retry_number
                                    )
                            if route_settled:
                                if route_index > 0:
                                    log(f"{display_name} 提交路由已确认: {submit_path}")
                                try:
                                    from novelvideo.generators.video.direct_video_capability_cache import (
                                        record_runtime_submit_route,
                                    )

                                    record_runtime_submit_route(
                                        base_url=candidate_base_url,
                                        protocol=self.protocol,
                                        upstream_model=self.upstream_model,
                                        submit_path=submit_path,
                                    )
                                except (OSError, ValueError) as cache_exc:
                                    log(f"提交路由运行缓存写入失败：{cache_exc}")
                                self._follow_accepted_route(submit_path)
                                break
                            if not self._submit_route_is_refused(last_route_error):
                                # The route answered something other than "no such
                                # route"; propagating keeps the existing
                                # gateway-fallback and auth diagnostics intact.
                                if last_route_error is not None:
                                    raise last_route_error
                                raise NewApiVideoError(
                                    "视频提交失败：未收到可用响应",
                                    stage="submit",
                                )
                        else:
                            # Every declared route refused this submit.  Report the
                            # routes actually walked instead of asking the operator
                            # to re-run a probe that only re-reads ``GET /models``.
                            refusal = last_route_error
                            raise NewApiVideoError(
                                str(refusal or "视频提交路由均不可用"),
                                request_id=refusal.request_id if refusal else "",
                                http_status=refusal.http_status if refusal else None,
                                response_text=refusal.response_text if refusal else "",
                                stage="submit",
                                url_path=refusal.url_path if refusal else "",
                                request_contract=(
                                    refusal.request_contract if refusal else None
                                ),
                                redirect_location=(
                                    refusal.redirect_location if refusal else ""
                                ),
                                attempted_routes=attempted_paths,
                            ) from refusal
                        accepted_gateway_name = candidate_name
                        if candidate_index > 0:
                            log(f"{display_name} 视频路由已接管任务提交")
                        break
                    except NewApiVideoError as exc:
                        if exc.request_id:
                            provider_request_id = exc.request_id
                        submit_errors.append(f"{display_name}: {exc}")
                        if submit_result_unknown or self._submission_result_is_unknown(exc):
                            raise
                        has_next_gateway = any(
                            str(next_candidate.get("api_key") or "").strip()
                            and str(next_candidate.get("base_url") or "").strip()
                            for next_candidate in fallback_candidates[candidate_index + 1 :]
                        )
                        if has_next_gateway:
                            log(
                                f"{display_name} 视频路由失败，切换下一个 NewAPI 网关兜底..."
                            )
                        else:
                            failure_scope = (
                                "configured NewAPI video gateway"
                                if len(submit_errors) == 1
                                else "all configured NewAPI video gateways"
                            )
                            raise NewApiVideoError(
                                "Village Infinite Canvas API submit failed on "
                                f"{failure_scope}: "
                                + "；".join(submit_errors),
                                request_id=exc.request_id or provider_request_id,
                                http_status=exc.http_status,
                                response_text=exc.response_text,
                                stage=exc.stage or "submit",
                                url_path=exc.url_path,
                                request_contract=exc.request_contract,
                                redirect_location=exc.redirect_location,
                                attempted_routes=exc.attempted_routes,
                            ) from exc
                if submitted is None:
                    raise NewApiVideoError(
                        "Village Infinite Canvas API submit failed: no configured NewAPI video gateway",
                        request_id=provider_request_id,
                    )
                task_id = self._task_id_from_response(submitted)
                provider_request_id = str(submitted.get("_newapi_request_id") or "").strip()
                if not task_id:
                    await _refund_video_model_call(
                        reservation_id,
                        source="newapi_video_generation",
                        error="missing_task_id",
                        provider_request_id=provider_request_id,
                    )
                    response_keys = self._task_response_keys(submitted)
                    error_metadata = self._task_diagnostic_contract(
                        error_code="VIDEO_TASK_ID_MISSING",
                        suggested_action="检查视频提交协议的 task_id 字段映射并重新检测渠道。",
                        retryable=False,
                        verification_stage="submit",
                        response_keys=response_keys,
                    )
                    emit_task_event(
                        "upstream_failed",
                        provider_request_id=provider_request_id,
                        error_code="VIDEO_TASK_ID_MISSING",
                        endpoint_class="video-submit",
                        verification_stage="submit",
                        retryable=False,
                        suggested_action=error_metadata["suggested_action"],
                        request_contract=error_metadata.get("request_contract"),
                        response_keys=response_keys,
                    )
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error="视频提交响应缺少 task_id，无法进入任务轮询",
                        error_metadata=error_metadata,
                    )
                record_accepted(task_id)
                log(f"任务已提交: {task_id}")
                emit_task_event(
                    "submitted",
                    provider_task_id=task_id,
                    provider_request_id=provider_request_id,
                    gateway=accepted_gateway_name,
                    **extract_provider_cost_evidence(
                        submitted,
                        prefix="result",
                    ).as_event_fields(),
                )
                progress(0.2)

                for poll_count in range(max_polls):
                    task: dict | None = None
                    last_query_error: Exception | None = None
                    for query_attempt in range(1, NEWAPI_VIDEO_QUERY_RETRIES + 1):
                        try:
                            task = await self._get_json(self._query_url(task_id))
                            break
                        except (NewApiVideoError, RuntimeError) as exc:
                            last_query_error = exc
                            if query_attempt < NEWAPI_VIDEO_QUERY_RETRIES:
                                await asyncio.sleep(float(query_attempt))
                    if task is None:
                        if isinstance(last_query_error, NewApiVideoError):
                            query_contract = last_query_error.diagnostic_contract(
                                protocol=self.protocol
                            )
                            if query_contract.get("error_code") in {
                                "VIDEO_ENDPOINT_NOT_FOUND",
                                "VIDEO_ENDPOINT_HTML_RESPONSE",
                                "VIDEO_EMPTY_RESPONSE",
                            }:
                                emit_task_event(
                                    "upstream_failed",
                                    provider_task_id=task_id,
                                    provider_request_id=provider_request_id,
                                    error_code=query_contract.get("error_code"),
                                    endpoint_class=query_contract.get("endpoint_class"),
                                    verification_stage="poll",
                                    retryable=query_contract.get("retryable"),
                                    suggested_action=query_contract.get("suggested_action"),
                                    request_contract=query_contract.get("request_contract"),
                                )
                                error = str(last_query_error)
                                update_request_status(task_id, "failed", error)
                                await _refund_video_model_call(
                                    reservation_id,
                                    source="newapi_video_generation",
                                    error=error,
                                    provider_request_id=provider_request_id,
                                    provider_task_id=task_id,
                                )
                                return VideoGenResult(
                                    status=VideoGenStatus.FAILED,
                                    error=error,
                                    task_id=task_id,
                                    error_metadata=query_contract,
                                )
                        progress(0.2 + (poll_count / max(max_polls, 1)) * 0.7)
                        log(
                            "上游任务查询暂时不可用，保留任务并重试 "
                            f"({poll_count + 1}/{max_polls})"
                        )
                        emit_task_event(
                            "query_retry",
                            provider_task_id=task_id,
                            poll_count=poll_count,
                            error_type=type(last_query_error).__name__
                            if last_query_error
                            else "unknown",
                        )
                        await asyncio.sleep(poll_interval)
                        continue
                    status = self._task_status(task)
                    progress(0.2 + (poll_count / max(max_polls, 1)) * 0.7)
                    emit_task_event(
                        "polling",
                        provider_task_id=task_id,
                        provider_request_id=provider_request_id,
                        upstream_status=status or "queued",
                        poll_count=poll_count,
                    )

                    if not status:
                        response_keys = self._task_response_keys(task)
                        error_metadata = self._task_diagnostic_contract(
                            error_code="VIDEO_STATUS_MISSING",
                            suggested_action="检查视频任务查询协议的 status 字段映射并重新检测渠道。",
                            retryable=False,
                            response_keys=response_keys,
                        )
                        error = "视频任务查询响应缺少 status 字段，无法判断生成状态"
                        emit_task_event(
                            "upstream_failed",
                            provider_task_id=task_id,
                            provider_request_id=provider_request_id,
                            upstream_status="",
                            error_code="VIDEO_STATUS_MISSING",
                            endpoint_class="video-task-query",
                            verification_stage="poll",
                            retryable=False,
                            suggested_action=error_metadata["suggested_action"],
                            request_contract=error_metadata.get("request_contract"),
                            response_keys=response_keys,
                        )
                        update_request_status(task_id, "failed", error)
                        await _refund_video_model_call(
                            reservation_id,
                            source="newapi_video_generation",
                            error=error,
                            provider_request_id=provider_request_id,
                            provider_task_id=task_id,
                        )
                        return VideoGenResult(
                            status=VideoGenStatus.FAILED,
                            error=error,
                            task_id=task_id,
                            error_metadata=error_metadata,
                        )

                    if status in self.protocol_contract.completed_statuses:
                        video_url = self._task_result_url(task)
                        if (
                            not video_url
                            and self.protocol != DIRECT_VIDEO_PROTOCOL_OPENAI
                            and not result_belongs_to_provider_task(output_path, task_id)
                        ):
                            response_keys = self._task_response_keys(task)
                            error_metadata = self._task_diagnostic_contract(
                                error_code="VIDEO_RESULT_URL_MISSING",
                                suggested_action="检查已完成任务的 result/video URL 字段映射并重新检测渠道。",
                                retryable=False,
                                verification_stage="artifact",
                                response_keys=response_keys,
                            )
                            error = "视频任务已完成，但响应缺少可下载的结果 URL"
                            emit_task_event(
                                "upstream_failed",
                                provider_task_id=task_id,
                                provider_request_id=provider_request_id,
                                upstream_status=status,
                                error_code="VIDEO_RESULT_URL_MISSING",
                                endpoint_class="video-result-download",
                                verification_stage="artifact",
                                retryable=False,
                                suggested_action=error_metadata["suggested_action"],
                                request_contract=error_metadata.get("request_contract"),
                                response_keys=response_keys,
                            )
                            update_request_status(task_id, "failed", error)
                            await _refund_video_model_call(
                                reservation_id,
                                source="newapi_video_generation",
                                error=error,
                                provider_request_id=provider_request_id,
                                provider_task_id=task_id,
                            )
                            return VideoGenResult(
                                status=VideoGenStatus.FAILED,
                                error=error,
                                task_id=task_id,
                                error_metadata=error_metadata,
                            )
                        progress(0.9)
                        provider_cost_fields = extract_provider_cost_evidence(
                            task,
                            prefix="result",
                        ).as_event_fields()
                        if provider_cost_fields:
                            emit_task_event(
                                "provider_cost",
                                provider_task_id=task_id,
                                provider_request_id=provider_request_id,
                                **provider_cost_fields,
                            )
                        emit_task_event(
                            "upstream_completed",
                            provider_task_id=task_id,
                            provider_request_id=provider_request_id,
                            preview_url=video_url,
                            upstream_status=status,
                        )
                        log("视频生成完成，正在下载...")
                        emit_task_event(
                            "downloading",
                            provider_task_id=task_id,
                            provider_request_id=provider_request_id,
                        )
                        # 同一个 Beat 重新生成时输出路径不变，「文件已存在」
                        # 不等于「这次的新成片已经落盘」。只有来源标记证明这个
                        # 文件就是当前上游任务的产物时才跳过下载；否则会把已经
                        # 付过钱的成片静默丢掉，界面继续显示上一次的旧视频。
                        if result_belongs_to_provider_task(output_path, task_id):
                            log("本地已有本次上游任务的成片，跳过重复下载")
                        else:
                            try:
                                video_url = await self._download_completed_task_video(
                                    task_id=task_id,
                                    video_url=video_url,
                                    output_path=output_path,
                                    on_direct_download_failure=log,
                                )
                            except Exception as exc:
                                # Preserve the already-accepted provider id in
                                # task state.  The durable queue can now retry the
                                # download leg without creating another render.
                                emit_task_event(
                                    "download_retry_required",
                                    provider_task_id=task_id,
                                    provider_request_id=provider_request_id,
                                    error_type=type(exc).__name__,
                                )
                                raise
                            write_result_provider_task(
                                output_path,
                                task_id,
                                protocol=self.protocol,
                                downloaded_at=datetime.now().isoformat(),
                            )
                        provider_task_id = self._extract_provider_task_id(
                            task,
                            fallback=task_id,
                        )
                        last_frame_url = ""
                        last_frame_path = ""
                        if bool(metadata.get("return_last_frame")):
                            last_frame_url = self._extract_returned_last_frame_url(task)
                            if last_frame_url:
                                last_frame_output_path = (
                                    self._returned_last_frame_output_path(
                                        output_path,
                                        last_frame_url,
                                    )
                                )
                                await self._download_video(
                                    last_frame_url,
                                    str(last_frame_output_path),
                                )
                                last_frame_path = last_frame_output_path.as_posix()
                                log("已保存 Village Infinite Canvas API 返回尾帧")
                        progress(1.0)
                        emit_task_event(
                            "downloaded",
                            provider_task_id=task_id,
                            provider_request_id=provider_request_id,
                        )
                        # A task id only proves upstream acceptance.  Upgrade the
                        # capability cache after a completed task has produced a
                        # non-empty local artifact.
                        try:
                            from novelvideo.generators.video.direct_video_capability_cache import (
                                record_runtime_verification,
                            )

                            if self.cache_runtime_contract:
                                record_runtime_verification(
                                    base_url=self.base_url,
                                    protocol=self.protocol,
                                    upstream_model=self.upstream_model,
                                )
                        except OSError:
                            log("视频产物已落盘，但本地能力缓存暂时不可写")
                        update_request_status(task_id, "completed")
                        await _confirm_video_model_call(
                            model=self.model,
                            reservation_id=reservation_id,
                            provider_request_id=provider_request_id,
                            provider_task_id=task_id,
                        )
                        return VideoGenResult(
                            status=VideoGenStatus.DONE,
                            video_url=video_url,
                            video_path=output_path,
                            last_frame_url=last_frame_url or None,
                            last_frame_path=last_frame_path or None,
                            task_id=task_id,
                            provider_task_id=provider_task_id,
                            duration_seconds=float(duration),
                        )

                    if status in self.protocol_contract.failed_statuses:
                        # A gateway can expose a transient ``failed`` status
                        # before the same accepted task becomes completed. Only
                        # grace a failure with no provider reason; an explicit
                        # error is already actionable and should finish promptly.
                        task_error = self._task_error(task)
                        model_grace_polls = (
                            PROMPT_HUBS_EARLY_FAILURE_GRACE_POLLS
                            if self._is_prompt_hubs_flex_video_model()
                            else VIDEO_EARLY_FAILURE_GRACE_POLLS
                        )
                        try:
                            from novelvideo.generators.video.direct_video_capability_cache import (
                                get_cached_capability_for_model,
                            )
                            from novelvideo.generators.video.direct_video_profiles import (
                                resolve_direct_video_profile,
                            )

                            profile = resolve_direct_video_profile(
                                self.upstream_model,
                                base_url=self.base_url,
                                protocol=self.protocol,
                            )
                            cached = get_cached_capability_for_model(
                                base_url=self.base_url,
                                upstream_model=self.upstream_model,
                                protocol=self.protocol,
                            )
                            configured_grace = cached.get("failureGracePolls")
                            if configured_grace is None:
                                configured_grace = profile.failure_grace_polls
                            if configured_grace is not None:
                                model_grace_polls = max(
                                    0,
                                    min(120, int(configured_grace)),
                                )
                        except (OSError, TypeError, ValueError):
                            # Keep the historical provider defaults if capability
                            # metadata is unavailable or malformed.
                            pass
                        grace_polls = min(model_grace_polls, max(0, int(max_polls) - 1))
                        if not task_error and poll_count < grace_polls:
                            log(
                                "上游视频任务暂态返回 failed 且没有失败详情，"
                                "保留原 task id 并继续核验 "
                                f"({poll_count + 1}/{grace_polls})"
                            )
                            emit_task_event(
                                "early_failure_grace",
                                provider_task_id=task_id,
                                provider_request_id=provider_request_id,
                                upstream_status=status,
                                poll_count=poll_count,
                            )
                            await asyncio.sleep(poll_interval)
                            continue
                        error = self._task_failure_message(
                            task,
                            status=status,
                            task_id=task_id,
                        )
                        response_keys = self._task_response_keys(task)
                        error_metadata = self._task_diagnostic_contract(
                            error_code=(
                                "VIDEO_UPSTREAM_TASK_FAILED"
                                if task_error
                                else "VIDEO_UPSTREAM_TASK_FAILED_NO_REASON"
                            ),
                            suggested_action=(
                                "根据上游返回的失败原因修正请求或渠道配置后重试。"
                                if task_error
                                else "重新检测该渠道的任务查询协议，并检查上游是否返回失败详情。"
                            ),
                            retryable=not bool(task_error),
                            response_keys=response_keys,
                        )
                        log(
                            "上游视频任务失败："
                            f"状态={status or 'failed'}；模型={self.upstream_model or self.model}；"
                            f"任务 ID={task_id}；响应字段={','.join(response_keys) or 'none'}；"
                            f"原因={error}"
                        )
                        emit_task_event(
                            "upstream_failed",
                            provider_task_id=task_id,
                            provider_request_id=provider_request_id,
                            upstream_status=status,
                            error_available=bool(task_error),
                            response_keys=response_keys,
                            error_code=error_metadata["error_code"],
                            endpoint_class=error_metadata["endpoint_class"],
                            verification_stage=error_metadata["verification_stage"],
                            retryable=error_metadata["retryable"],
                            suggested_action=error_metadata["suggested_action"],
                            request_contract=error_metadata.get("request_contract"),
                        )
                        update_request_status(task_id, "failed", str(error))
                        await _refund_video_model_call(
                            reservation_id,
                            source="newapi_video_generation",
                            error=str(error),
                            provider_request_id=provider_request_id,
                            provider_task_id=task_id,
                        )
                        return VideoGenResult(
                            status=VideoGenStatus.FAILED,
                            error=str(error),
                            task_id=task_id,
                            error_metadata=error_metadata,
                        )

                    if poll_count % 6 == 0:
                        log(
                            f"Village Infinite Canvas API task {task_id} status: "
                            f"{status or 'queued'} ({poll_count}/{max_polls})"
                        )
                    await asyncio.sleep(poll_interval)

                update_request_status(
                    task_id, "failed", "Timeout waiting for Village Infinite Canvas API video task"
                )
                emit_task_event(
                    "timeout",
                    provider_task_id=task_id,
                    provider_request_id=provider_request_id,
                )
                await _refund_video_model_call(
                    reservation_id,
                    source="newapi_video_generation",
                    error="timeout",
                    provider_request_id=provider_request_id,
                    provider_task_id=task_id,
                )
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error="Timeout waiting for Village Infinite Canvas API video task",
                    task_id=task_id,
                )
            except NewApiVideoError as exc:
                if exc.request_id:
                    log(f"Village Infinite Canvas API request_id: {exc.request_id}")
                contract = exc.request_contract
                if contract:
                    payload_keys = ",".join(
                        str(value) for value in (contract.get("payload_keys") or ())
                    )
                    media_counts = contract.get("media_counts") or {}
                    log(
                        "视频提交诊断："
                        f"path={exc.url_path or 'unknown'}；protocol={self.protocol}；"
                        f"body_bytes={contract.get('body_bytes', 'unknown')}；"
                        f"content_length={contract.get('content_length', 'unknown')}；"
                        f"payload_keys={payload_keys or 'none'}；"
                        f"media_counts={media_counts or 'none'}"
                    )
                if exc.submission_result_unknown:
                    emit_task_event(
                        "submit_result_unknown",
                        provider_request_id=exc.request_id or provider_request_id,
                        idempotency_key=str(kwargs.get("idempotency_key") or "").strip(),
                    )
                    log("视频提交结果待确认：保留同一幂等键，不退款、不切换备用网关")
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error=str(exc),
                        error_metadata=exc.diagnostic_contract(protocol=self.protocol),
                        task_id=task_id,
                    )
                if task_id:
                    log(f"Village Infinite Canvas API task_id: {task_id}")
                    update_request_status(task_id, "failed", str(exc))
                await _refund_video_model_call(
                    reservation_id,
                    source="newapi_video_generation",
                    error=str(exc),
                    provider_request_id=exc.request_id or provider_request_id,
                    provider_task_id=task_id or "",
                )
                if is_insufficient_credits_error(exc):
                    raise
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error=str(exc),
                    error_metadata=exc.diagnostic_contract(protocol=self.protocol),
                    task_id=task_id,
                )
            except Exception as exc:
                if task_id:
                    update_request_status(task_id, "failed", str(exc))
                await _refund_video_model_call(
                    reservation_id,
                    source="newapi_video_generation",
                    error=str(exc),
                    provider_request_id=provider_request_id,
                    provider_task_id=task_id or "",
                )
                if is_insufficient_credits_error(exc):
                    raise
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error=str(exc),
                    task_id=task_id,
                )
