"""HuiMeng video generator adapter."""

from __future__ import annotations

import asyncio
import math
import os
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from novelvideo.generators.video.direct_video_protocol_contracts import (
    DIRECT_VIDEO_PROTOCOL_OPENAI,
)
from novelvideo.generators.video.newapi_video_diagnostics import NewApiVideoError
from novelvideo.generators.video.result_provenance import (
    result_belongs_to_provider_task,
    write_result_provider_task,
)
from novelvideo.video_request_usage import (
    record_video_request,
    update_video_request_status,
)

from .base import VideoGenResult, VideoGenStatus, VideoGeneratorBase
from .seedance2 import (
    ShotReference,
    _seedance2_config_mapping,
    _seedance2_duration_from_config,
)

__all__ = ["HuimengVideoGenerator"]


class HuimengVideoGenerator(VideoGeneratorBase):
    """HuiMeng async-task video generator."""

    DEFAULT_MODEL = "seedance-1.0-pro-fast"

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        model: Optional[str] = None,
        resolution: Optional[str] = None,
        generate_audio: Optional[bool] = None,
        client=None,
    ):
        from novelvideo.config import (
            HUIMENGI_VIDEO_GENERATE_AUDIO,
            HUIMENGI_VIDEO_RESOLUTION,
        )
        from novelvideo.generators.huimengi import HuimengiTaskClient

        self.model = model or self.DEFAULT_MODEL
        self.resolution = resolution or HUIMENGI_VIDEO_RESOLUTION
        self.generate_audio = (
            HUIMENGI_VIDEO_GENERATE_AUDIO if generate_audio is None else generate_audio
        )
        self.client = client or HuimengiTaskClient(api_key=api_key, base_url=endpoint)

    def _duration_bounds(self) -> tuple[int, int]:
        if self._is_happyhorse_model():
            return 3, 15
        if self.model.startswith("seedance-2.0"):
            return 4, 15
        if self.model == "seedance-1.5-pro":
            return 4, 12
        return 2, 12

    def _supports_audio_param(self) -> bool:
        return self.model in {"seedance-2.0", "seedance-2.0-fast", "seedance-1.5-pro"}

    def _is_seedance2_model(self) -> bool:
        return self.model.startswith("seedance-2.0")

    def _is_happyhorse_model(self) -> bool:
        return self.model.strip().lower() == "happyhorse-1.0"

    def _to_upload_url(
        self,
        value: str | None,
        *,
        label: str,
        log: Callable[[str], None],
        require_http_url: bool = False,
    ) -> str | None:
        from novelvideo.generators.huimengi import local_file_to_data_url

        text = str(value or "").strip()
        if not text:
            return None
        if text.startswith(("http://", "https://")):
            return text
        if text.startswith("data:"):
            if require_http_url:
                raise ValueError(
                    "human_review requires HTTP/HTTPS media URLs; "
                    f"unsupported direct media reference for {label}: data:"
                )
            return text
        if os.path.exists(text):
            if require_http_url:
                from novelvideo.utils.oss_client import presign_or_upload_output

                oss_url = presign_or_upload_output(text)
                if not oss_url:
                    raise ValueError(
                        "human_review requires OSS presigned HTTP media URLs. "
                        f"Failed to upload or presign local file: {text}"
                    )
                log(f"{label}已上传/复用 OSS URL")
                return oss_url
            log(f"{label}已转换为 data URL")
            return local_file_to_data_url(text)
        raise FileNotFoundError(f"{label} not found: {text}")

    def _build_reference_params(
        self,
        references: list["ShotReference"] | None,
        *,
        log: Callable[[str], None],
        require_http_url: bool = False,
    ) -> tuple[dict[str, list[str]], dict[str, int]]:
        params: dict[str, list[str]] = {}
        image_urls: list[str] = []
        video_urls: list[str] = []
        audio_urls: list[str] = []

        for ref in references or []:
            path = str(getattr(ref, "path", "") or "").strip()
            if not path:
                continue
            ref_type = str(getattr(ref, "type", "") or "image").strip().lower()
            role = str(getattr(ref, "role", "") or "").strip()
            label = f"{role or ref_type}参考"
            data_url = self._to_upload_url(
                path,
                label=label,
                log=log,
                require_http_url=require_http_url,
            )
            if not data_url:
                continue
            if ref_type == "image":
                image_urls.append(data_url)
            elif ref_type == "video":
                video_urls.append(data_url)
            elif ref_type == "audio":
                audio_urls.append(data_url)

        if image_urls:
            params["reference_images"] = image_urls[:9]
        if video_urls:
            params["reference_videos"] = video_urls[:3]
        if audio_urls:
            params["reference_audios"] = audio_urls[:3]

        return params, {
            "image_count": len(params.get("reference_images", [])),
            "video_count": len(params.get("reference_videos", [])),
            "audio_count": len(params.get("reference_audios", [])),
        }

    async def generate(
        self,
        image_path: Optional[str],
        prompt: str,
        output_path: str,
        aspect_ratio: str = "adaptive",
        duration: float = 5.0,
        poll_interval: float = 5.0,
        max_polls: int = 120,
        on_log: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[float], None]] = None,
        last_frame_path: Optional[str] = None,
        **kwargs,
    ) -> VideoGenResult:
        def log(msg: str):
            if on_log:
                on_log(msg)

        def progress(value: float):
            if on_progress:
                on_progress(value)

        project_output_dir = kwargs.get("project_output_dir")
        tracking_episode = kwargs.get("episode")
        tracking_beat_num = kwargs.get("beat_num")
        tracking_task_type = kwargs.get("task_type", "")
        tracking_cost_estimate = kwargs.get("cost_estimate")

        def record_accepted(request_id: str):
            if not project_output_dir or not request_id:
                return
            try:
                record_video_request(
                    project_output_dir=project_output_dir,
                    request_id=request_id,
                    provider="huimeng",
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

        is_seedance2_model = self._is_seedance2_model()
        seedance2_config = (
            _seedance2_config_mapping(kwargs.get("seedance2_config"))
            if is_seedance2_model
            else {}
        )
        duration = _seedance2_duration_from_config(seedance2_config, duration)
        min_duration, max_duration = self._duration_bounds()
        original_duration = duration
        duration = max(min_duration, min(max_duration, math.ceil(duration)))
        if duration != original_duration:
            log(f"时长已调整: {original_duration:.1f}s -> {duration:.0f}s")

        ratio = aspect_ratio if ":" in aspect_ratio else "adaptive"

        references = kwargs.get("references") or []
        require_http_media = (
            bool(seedance2_config.get("human_review"))
            if "human_review" in seedance2_config
            else bool(kwargs.get("human_review", False))
        )

        try:
            first_frame = self._to_upload_url(
                image_path,
                label="首帧",
                log=log,
                require_http_url=require_http_media,
            )
            last_frame = self._to_upload_url(
                last_frame_path,
                label="尾帧",
                log=log,
                require_http_url=require_http_media,
            )
            reference_params, ref_counts = self._build_reference_params(
                references,
                log=log,
                require_http_url=require_http_media,
            )
        except (FileNotFoundError, ValueError) as exc:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=str(exc),
            )

        has_multimodal_refs = any(ref_counts.values())

        if is_seedance2_model:
            from novelvideo.seedance2_i2v.models import Seedance2I2VMode
            from novelvideo.seedance2_i2v.request import build_seedance2_huimeng_params

            if last_frame:
                mode = Seedance2I2VMode.FIRST_LAST_FRAME
            elif has_multimodal_refs:
                mode = Seedance2I2VMode.MULTIMODAL_REFERENCE
            elif first_frame:
                mode = Seedance2I2VMode.FIRST_FRAME
            else:
                mode = Seedance2I2VMode.TEXT_TO_VIDEO

            config = seedance2_config
            config_resolution = str(
                config.get("resolution") or self.resolution or "720p"
            ).strip()
            config_ratio = str(config.get("ratio") or ratio or "adaptive").strip()
            config.update(
                {
                    "mode": mode.value,
                    "final_prompt": prompt,
                    "duration": duration,
                    "resolution": config_resolution,
                    "ratio": config_ratio,
                }
            )
            if "generate_audio" not in config:
                config["generate_audio"] = bool(self.generate_audio)
                config["generate_audio_user_set"] = True
            elif "generate_audio_user_set" not in config:
                config["generate_audio_user_set"] = True
            if "return_last_frame" not in config:
                config["return_last_frame"] = False
            if "human_review" not in config:
                # Direct generator calls default to no material-review upload unless
                # the caller passes the per-beat Seedance2 config or an explicit kwarg.
                config["human_review"] = bool(kwargs.get("human_review", False))
                config["human_review_user_set"] = True
            elif "human_review_user_set" not in config:
                config["human_review_user_set"] = True
            try:
                params = build_seedance2_huimeng_params(
                    config,
                    first_frame=first_frame or "",
                    last_frame=last_frame or "",
                    reference_images=reference_params.get("reference_images"),
                    reference_videos=reference_params.get("reference_videos"),
                    reference_audios=reference_params.get("reference_audios"),
                )
            except ValueError as exc:
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error=str(exc),
                )
        else:
            params = {
                "prompt": prompt,
                "duration": duration,
                "resolution": self.resolution,
                "ratio": ratio,
                "return_last_frame": False,
            }

            if last_frame:
                if not first_frame:
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error="First frame is required when last_frame_path is provided",
                    )
                params["first_frame_image"] = first_frame
                params["last_frame_image"] = last_frame
            elif has_multimodal_refs:
                params.update(reference_params)
            elif first_frame:
                params["image_url"] = first_frame

            if self._supports_audio_param():
                params["generate_audio"] = bool(self.generate_audio)

        task_id: str | None = None
        try:
            request_resolution = str(
                params.get("resolution") or self.resolution or ""
            ).strip()
            log(
                f"正在提交 HuiMeng 视频任务 "
                f"({self.model}, refs={ref_counts['image_count']}图/{ref_counts['video_count']}视频/{ref_counts['audio_count']}音频, "
                f"{duration}s, {request_resolution})..."
            )
            progress(0.1)
            submitted = await self.client.submit_task(model=self.model, params=params)
            task_id = submitted["task_id"]
            record_accepted(task_id)
            log(f"任务已提交: {task_id}")
            progress(0.2)

            task = await self.client.wait_for_completion(
                task_id,
                poll_interval=poll_interval,
                max_polls=max_polls,
                on_log=on_log,
                on_progress=on_progress,
            )

            from novelvideo.generators.huimengi import (
                extract_huimeng_result_duration,
                extract_huimeng_result_last_frame_url,
                extract_huimeng_result_url,
            )

            result = task.get("result") or {}
            task_result_payload = {**result, **task}
            video_url = extract_huimeng_result_url(result, "video_url", "video_urls")
            if not video_url:
                update_request_status(
                    task_id, "failed", "No video_url in HuiMeng result"
                )
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error=f"No video_url in HuiMeng result: {result}",
                    task_id=task_id,
                )

            log("视频生成完成，正在下载...")
            await self.client.download_url(video_url, output_path)
            last_frame_url = ""
            last_frame_path = ""
            if bool(params.get("return_last_frame")):
                last_frame_url = extract_huimeng_result_last_frame_url(
                    task_result_payload
                )
                if last_frame_url:
                    video_output_path = Path(output_path)
                    parsed_last_frame_url = urllib.parse.urlparse(last_frame_url)
                    last_frame_suffix = Path(parsed_last_frame_url.path).suffix.lower()
                    if last_frame_suffix not in {
                        ".png",
                        ".jpg",
                        ".jpeg",
                        ".webp",
                        ".gif",
                    }:
                        last_frame_suffix = ".png"
                    last_frame_output_path = (
                        video_output_path.parent
                        / "returned_last_frames"
                        / f"{video_output_path.stem}{last_frame_suffix}"
                    )
                    await self.client.download_image_url(
                        last_frame_url,
                        str(last_frame_output_path),
                    )
                    last_frame_path = last_frame_output_path.as_posix()
                    log("已保存 HuiMeng 返回尾帧")
            progress(1.0)
            update_request_status(task_id, "completed")
            return VideoGenResult(
                status=VideoGenStatus.DONE,
                video_url=video_url,
                video_path=output_path,
                last_frame_url=last_frame_url or None,
                last_frame_path=last_frame_path or None,
                task_id=task_id,
                provider_task_id=task_id,
                duration_seconds=extract_huimeng_result_duration(result)
                or float(duration),
            )

        except Exception as exc:
            if task_id:
                update_request_status(task_id, "failed", str(exc))
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=str(exc),
                task_id=task_id,
            )

    async def recover_task(
        self,
        *,
        task_id: str,
        output_path: str,
        duration: float = 5.0,
        poll_interval: float = 5.0,
        max_polls: int = 720,
        on_log: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[float], None]] = None,
        on_task_event: Optional[Callable[[dict[str, object]], None]] = None,
        **_kwargs,
    ) -> VideoGenResult:
        """Resume an already-submitted provider task without submitting again.

        A process restart can interrupt the local poll/download leg after the
        upstream has accepted the job.  Re-query every configured gateway using
        the persisted task id, then download the completed artifact once.  No
        reservation, submit, refund, or duplicate upstream request is created.
        """

        provider_task_id = str(task_id or "").strip()
        if not provider_task_id:
            return VideoGenResult(
                status=VideoGenStatus.FAILED, error="missing provider task id"
            )

        def log(message: str) -> None:
            if on_log:
                on_log(message)

        def progress(value: float) -> None:
            if on_progress:
                on_progress(value)

        def emit(stage: str, **details: object) -> None:
            if not on_task_event:
                return
            try:
                on_task_event(
                    {
                        "stage": stage,
                        "model": self.model,
                        "provider_task_id": provider_task_id,
                        **details,
                    }
                )
            except Exception:
                pass

        candidates = self._result_gateway_candidates()
        log(f"检测到已提交的上游视频任务，开始恢复: {provider_task_id}")
        emit("resuming")

        for poll_count in range(max_polls):
            task: dict | None = None
            last_error: Exception | None = None
            selected_gateway = ""
            for candidate in candidates:
                candidate_api_key = str(candidate.get("api_key") or "").strip()
                candidate_base_url = (
                    str(candidate.get("base_url") or "").strip().rstrip("/")
                )
                if not candidate_api_key or not candidate_base_url:
                    continue
                self.api_key = candidate_api_key
                self.base_url = candidate_base_url
                try:
                    task = await self._get_json(
                        self._query_url(provider_task_id, candidate_base_url)
                    )
                    selected_gateway = str(
                        candidate.get("name") or candidate.get("source") or "newapi"
                    )
                    break
                except (NewApiVideoError, RuntimeError) as exc:
                    last_error = exc

            if task is None:
                if isinstance(last_error, NewApiVideoError):
                    query_contract = last_error.diagnostic_contract(protocol=self.protocol)
                    if query_contract.get("error_code") in {
                        "VIDEO_ENDPOINT_NOT_FOUND",
                        "VIDEO_ENDPOINT_HTML_RESPONSE",
                        "VIDEO_EMPTY_RESPONSE",
                    }:
                        emit(
                            "upstream_failed",
                            error_code=query_contract.get("error_code"),
                            endpoint_class=query_contract.get("endpoint_class"),
                            verification_stage="poll",
                            retryable=query_contract.get("retryable"),
                            suggested_action=query_contract.get("suggested_action"),
                            request_contract=query_contract.get("request_contract"),
                        )
                        return VideoGenResult(
                            status=VideoGenStatus.FAILED,
                            error=str(last_error),
                            task_id=provider_task_id,
                            error_metadata=query_contract,
                        )
                progress(0.2 + (poll_count / max(max_polls, 1)) * 0.7)
                log(
                    "恢复任务暂时无法查询上游状态，保留 task id 并继续重试 "
                    f"({poll_count + 1}/{max_polls})"
                )
                emit(
                    "query_retry",
                    poll_count=poll_count,
                    error_type=type(last_error).__name__ if last_error else "unknown",
                )
                await asyncio.sleep(poll_interval)
                continue

            status = self._task_status(task)
            progress(0.2 + (poll_count / max(max_polls, 1)) * 0.7)
            emit(
                "polling",
                upstream_status=status or "queued",
                poll_count=poll_count,
                gateway=selected_gateway,
            )

            if not status:
                response_keys = self._task_response_keys(task)
                error = "视频任务查询响应缺少 status 字段，无法判断生成状态"
                error_metadata = self._task_diagnostic_contract(
                    error_code="VIDEO_STATUS_MISSING",
                    suggested_action="检查视频任务查询协议的 status 字段映射并重新检测渠道。",
                    retryable=False,
                    response_keys=response_keys,
                )
                emit(
                    "upstream_failed",
                    upstream_status="",
                    error_code="VIDEO_STATUS_MISSING",
                    endpoint_class="video-task-query",
                    verification_stage="poll",
                    retryable=False,
                    suggested_action=error_metadata["suggested_action"],
                    request_contract=error_metadata.get("request_contract"),
                    response_keys=response_keys,
                )
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error=error,
                    task_id=provider_task_id,
                    error_metadata=error_metadata,
                )

            if status in self.protocol_contract.completed_statuses:
                video_url = self._task_result_url(task)
                if (
                    not video_url
                    and self.protocol != DIRECT_VIDEO_PROTOCOL_OPENAI
                    and not result_belongs_to_provider_task(
                        output_path, provider_task_id
                    )
                ):
                    response_keys = self._task_response_keys(task)
                    error = "视频任务已完成，但响应缺少可下载的结果 URL"
                    error_metadata = self._task_diagnostic_contract(
                        error_code="VIDEO_RESULT_URL_MISSING",
                        suggested_action="检查已完成任务的 result/video URL 字段映射并重新检测渠道。",
                        retryable=False,
                        verification_stage="artifact",
                        response_keys=response_keys,
                    )
                    emit(
                        "upstream_failed",
                        upstream_status=status,
                        error_code="VIDEO_RESULT_URL_MISSING",
                        endpoint_class="video-result-download",
                        verification_stage="artifact",
                        retryable=False,
                        suggested_action=error_metadata["suggested_action"],
                        request_contract=error_metadata.get("request_contract"),
                        response_keys=response_keys,
                    )
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error=error,
                        task_id=provider_task_id,
                        error_metadata=error_metadata,
                    )
                progress(0.9)
                emit(
                    "upstream_completed",
                    preview_url=video_url,
                    upstream_status=status,
                    gateway=selected_gateway,
                )
                log("上游视频已完成，正在恢复下载...")
                emit("downloading", gateway=selected_gateway)
                try:
                    if result_belongs_to_provider_task(output_path, provider_task_id):
                        log("本地已有该上游任务的成片，跳过重复下载")
                    else:
                        video_url = await self._download_completed_task_video(
                            task_id=provider_task_id,
                            video_url=video_url,
                            output_path=output_path,
                            on_direct_download_failure=log,
                        )
                        write_result_provider_task(
                            output_path,
                            provider_task_id,
                            protocol=self.protocol,
                            downloaded_at=datetime.now().isoformat(),
                        )
                except Exception as exc:
                    emit("download_retry_required", error_type=type(exc).__name__)
                    error_metadata = (
                        exc.diagnostic_contract(protocol=self.protocol)
                        if isinstance(exc, NewApiVideoError)
                        else None
                    )
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error=f"已完成的上游视频下载失败，可再次恢复: {exc}",
                        task_id=provider_task_id,
                        error_metadata=error_metadata,
                    )
                if (
                    not Path(output_path).exists()
                    or Path(output_path).stat().st_size <= 0
                ):
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error="已完成的上游视频未写入本地文件，可再次恢复",
                        task_id=provider_task_id,
                    )
                progress(1.0)
                emit("downloaded", gateway=selected_gateway)
                return VideoGenResult(
                    status=VideoGenStatus.DONE,
                    video_url=video_url,
                    video_path=output_path,
                    task_id=provider_task_id,
                    provider_task_id=self._extract_provider_task_id(
                        task,
                        fallback=provider_task_id,
                    ),
                    duration_seconds=float(duration),
                )

            if status in self.protocol_contract.failed_statuses:
                error = self._task_failure_message(
                    task,
                    status=status,
                    task_id=provider_task_id,
                )
                response_keys = self._task_response_keys(task)
                task_error = self._task_error(task)
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
                    f"任务 ID={provider_task_id}；响应字段={','.join(response_keys) or 'none'}；"
                    f"原因={error}"
                )
                emit(
                    "upstream_failed",
                    upstream_status=status,
                    provider_task_id=provider_task_id,
                    error_available=bool(self._task_error(task)),
                    response_keys=response_keys,
                    error_code=error_metadata["error_code"],
                    endpoint_class=error_metadata["endpoint_class"],
                    verification_stage=error_metadata["verification_stage"],
                    retryable=error_metadata["retryable"],
                    suggested_action=error_metadata["suggested_action"],
                    request_contract=error_metadata.get("request_contract"),
                )
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error=error,
                    task_id=provider_task_id,
                    error_metadata=error_metadata,
                )

            if poll_count % 6 == 0:
                log(
                    f"正在恢复上游视频任务: {status or 'queued'} "
                    f"({poll_count}/{max_polls})"
                )
            await asyncio.sleep(poll_interval)

        emit("timeout")
        return VideoGenResult(
            status=VideoGenStatus.FAILED,
            error="Timeout waiting for recovered Village Infinite Canvas API video task",
            task_id=provider_task_id,
        )
