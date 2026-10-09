"""Seedance 2 request and reference contracts."""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from .base import (
    VideoGenResult,
    VideoGenStatus,
    VideoGeneratorBase,
    normalize_video_aspect_ratio as _normalize_video_aspect_ratio,
)

__all__ = [
    "Seedance2VideoGenerator",
    "ShotReference",
    "_seedance2_config_mapping",
    "_seedance2_duration_from_config",
]


@dataclass
class ShotReference:
    """Seedance 2.0 素材引用。"""

    type: str  # "image" / "video" / "audio"
    path: str  # 本地文件路径
    role: str  # "首帧" / "角色参考" / "场景参考" / "配乐" / "音色参考"


class Seedance2VideoGenerator(VideoGeneratorBase):
    """火山方舟 Seedance 2.0 全能参考模式视频生成器。

    支持多模态输入：文本 + 最多9图 + 3视频 + 3音频。
    原生音视频同步生成（对话、BGM、音效）。
    内置智能运镜系统。

    注意：API格式需在API正式开放后确认，以下基于已知信息推断。

    示例:
        >>> generator = Seedance2VideoGenerator()
        >>> result = await generator.generate(
        ...     prompt="古老书房中，少女翻阅古籍...",
        ...     references=[
        ...         ShotReference("image", "frame.png", "首帧"),
        ...         ShotReference("image", "char_ref.png", "角色参考"),
        ...     ],
        ...     output_path="output.mp4",
        ...     duration=10,
        ...     audio=True,
        ... )
    """

    MODEL = "seedance-2.0"
    MODEL_I2V = "seedance-2.0-i2v"

    def _select_generation_model(
        self,
        *,
        image_count: int,
        video_count: int,
        audio_count: int,
        explicit_mode: str = "",
    ) -> str:
        """Choose the Seedance model variant for the current reference mix.

        Mixed-reference omni generation should stay on the general Seedance 2.0
        model. The i2v variant is only used for pure single-image first-frame
        generation.
        """
        # An image-reference request is still a multimodal/reference contract;
        # do not collapse it into the dedicated single-image I2V variant just
        # because the filtered request happens to contain one image.
        if explicit_mode and explicit_mode != "imageToVideo":
            return self.MODEL
        if image_count == 1 and video_count == 0 and audio_count == 0:
            return self.MODEL_I2V
        return self.MODEL

    @staticmethod
    def _normalize_explicit_mode(value: object) -> str:
        """Keep the selected Seedance mode distinct from local frame sentinels."""

        raw = str(getattr(value, "value", value) or "").strip()
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
            "referencetovideo": "allReference",
            "multimodalreference": "allReference",
            "imagereference": "imageReference",
            "videoedit": "videoEdit",
        }.get(token, "")

    @staticmethod
    def _reference_value(reference: object, name: str, default: object = "") -> object:
        if isinstance(reference, Mapping):
            return reference.get(name, default)
        return getattr(reference, name, default)

    @classmethod
    def _reference_kind(cls, reference: object) -> str:
        value = cls._reference_value(reference, "type", "") or cls._reference_value(
            reference, "kind", "image"
        )
        value = getattr(value, "value", value)
        kind = str(value or "image").strip().lower()
        return kind if kind in {"image", "video", "audio"} else "image"

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
    def _filter_explicit_references(
        cls,
        *,
        mode: str,
        image_path: str | None,
        last_frame_path: str | None,
        references: list[ShotReference],
    ) -> tuple[str | None, str | None, list[ShotReference]]:
        """Apply the selected media contract before file checks and upload."""

        if not mode:
            return image_path, last_frame_path, list(references)

        image_refs = [
            reference
            for reference in references
            if cls._reference_kind(reference) == "image" and cls._reference_path(reference)
        ]

        def first_image() -> str:
            if image_path and str(image_path).strip():
                return str(image_path).strip()
            for reference in image_refs:
                role = cls._reference_role(reference)
                if role in {"首帧", "first", "first_frame", "firstframe"}:
                    return cls._reference_path(reference)
            return cls._reference_path(image_refs[0]) if image_refs else ""

        def last_image() -> str:
            if last_frame_path and str(last_frame_path).strip():
                return str(last_frame_path).strip()
            for reference in image_refs:
                role = cls._reference_role(reference)
                if role in {"尾帧", "last", "last_frame", "lastframe"}:
                    return cls._reference_path(reference)
            # A two-image first/last request often arrives without role labels.
            return cls._reference_path(image_refs[1]) if len(image_refs) > 1 else ""

        if mode == "textToVideo":
            return None, None, []

        if mode == "imageToVideo":
            first = first_image()
            return first or None, None, (
                [ShotReference("image", first, "首帧")] if first else []
            )

        if mode == "firstLastFrame":
            first = first_image()
            last = last_image()
            filtered: list[ShotReference] = []
            if first:
                filtered.append(ShotReference("image", first, "首帧"))
            if last:
                filtered.append(ShotReference("image", last, "尾帧"))
            return first or None, last or None, filtered

        if mode == "imageReference":
            filtered: list[ShotReference] = []
            seen: set[str] = set()
            # A stale transition tail belongs only to firstLastFrame; it must
            # not silently become a second ordinary reference.
            for path in (image_path,):
                value = str(path or "").strip()
                if value and value not in seen:
                    filtered.append(ShotReference("image", value, "图片参考"))
                    seen.add(value)
            for reference in image_refs:
                value = cls._reference_path(reference)
                if value and value not in seen:
                    filtered.append(reference)
                    seen.add(value)
            return None, None, filtered

        if mode == "videoEdit":
            return None, None, [
                reference
                for reference in references
                if cls._reference_kind(reference) == "video"
                and cls._reference_path(reference)
            ]

        # allReference keeps all declared media and promotes a standalone
        # image/last-frame argument into the reference list without duplication.
        filtered = list(references)
        seen = {cls._reference_path(reference) for reference in filtered}
        for path, role in ((image_path, "首帧参考"), (last_frame_path, "尾帧参考")):
            value = str(path or "").strip()
            if value and value not in seen:
                filtered.insert(0, ShotReference("image", value, role))
                seen.add(value)
        return None, None, filtered

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
    ):
        self.model = model or self.MODEL
        self.api_key = (
            api_key
            or os.environ.get("VOLCENGINE_VISUAL_API_KEY")
            or os.environ.get("ARK_API_KEY")
        )
        self.endpoint = endpoint or os.environ.get(
            "VOLCENGINE_VISUAL_ENDPOINT", "https://ark.cn-beijing.volces.com/api/v3"
        )
        if not self.api_key:
            raise ValueError("VOLCENGINE_VISUAL_API_KEY or ARK_API_KEY must be set")

    def _file_to_data_url(self, file_path: str) -> str:
        """将本地文件转换为 data URL（base64）。"""
        import base64
        import mimetypes

        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            ext = file_path.lower().rsplit(".", 1)[-1]
            mime_map = {
                "png": "image/png",
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "webp": "image/webp",
                "bmp": "image/bmp",
                "gif": "image/gif",
                "mp4": "video/mp4",
                "mov": "video/quicktime",
                "mp3": "audio/mpeg",
                "wav": "audio/wav",
            }
            mime_type = mime_map.get(ext, "application/octet-stream")

        with open(file_path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        return f"data:{mime_type};base64,{data}"

    async def _download_video(self, url: str, output_path: str) -> bool:
        """下载视频文件。"""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return False
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(resp.content)
                return True
        except Exception:
            return False

    async def submit_task(
        self, image_url: str, prompt: str, aspect_ratio: str = "9:16"
    ) -> str:
        return f"seedance2-{uuid.uuid4().hex[:8]}"

    async def get_result(self, task_id: str) -> VideoGenResult:
        return VideoGenResult(status=VideoGenStatus.PROCESSING, task_id=task_id)

    async def generate(
        self,
        prompt: str,
        output_path: str,
        references: Optional[list[ShotReference]] = None,
        duration: float = 10.0,
        audio: bool = True,
        aspect_ratio: str = "9:16",
        resolution: str = "2k",
        poll_interval: float = 5.0,
        max_polls: int = 120,
        on_log: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[float], None]] = None,
        # 兼容 VideoGeneratorBase 接口
        image_path: Optional[str] = None,
        **kwargs,
    ) -> VideoGenResult:
        """Seedance 2.0 全能参考模式生成。

        Args:
            prompt: 中文自然语言描述（含@素材引用）
            output_path: 输出视频路径
            references: 素材引用列表（图片/视频/音频）
            duration: 目标时长（5-15秒）
            audio: 是否生成原生音频
            aspect_ratio: 宽高比
            resolution: 分辨率（720p/1080p/2k）
            poll_interval: 轮询间隔
            max_polls: 最大轮询次数
            on_log: 日志回调
            on_progress: 进度回调
            image_path: 兼容旧接口的首帧图路径
        """
        import httpx

        def log(msg: str):
            if on_log:
                on_log(msg)

        def progress(value: float):
            if on_progress:
                on_progress(value)

        refs = list(references or [])

        explicit_mode = self._normalize_explicit_mode(
            kwargs.get("gen_mode") or kwargs.get("mode")
        )

        # 兼容旧接口：如果传了 image_path 但没有 references，自动作为首帧
        if image_path and not refs and not explicit_mode:
            refs = [ShotReference("image", image_path, "首帧")]

        image_path, last_frame_path, refs = self._filter_explicit_references(
            mode=explicit_mode,
            image_path=image_path,
            last_frame_path=kwargs.get("last_frame_path"),
            references=refs,
        )

        # 限制 duration 在 5-15 范围
        duration = max(5, min(15, int(duration)))

        # 映射 aspect_ratio 格式
        ratio = _normalize_video_aspect_ratio(aspect_ratio)

        # 构建 content 数组
        content = [{"type": "text", "text": prompt}]

        # 添加素材引用
        image_count, video_count, audio_count = 0, 0, 0
        for ref in refs:
            ref_path = self._reference_path(ref)
            ref_type = self._reference_kind(ref)
            ref_role = self._reference_role(ref)
            if not os.path.exists(ref_path):
                log(f"警告: 素材不存在: {ref_path}")
                continue

            data_url = self._file_to_data_url(ref_path)

            if ref_type == "image" and image_count < 9:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                        "role": ref_role,
                    }
                )
                image_count += 1
            elif ref_type == "video" and video_count < 3:
                content.append(
                    {
                        "type": "video_url",
                        "video_url": {"url": data_url},
                        "role": ref_role,
                    }
                )
                video_count += 1
            elif ref_type == "audio" and audio_count < 3:
                content.append(
                    {
                        "type": "audio_url",
                        "audio_url": {"url": data_url},
                        "role": ref_role,
                    }
                )
                audio_count += 1

        # Explicit media contracts must fail before an upstream task is
        # created.  Without this guard a missing frame/reference silently
        # degenerates into a different generation mode and can consume quota.
        if explicit_mode == "imageToVideo" and image_count < 1:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error="imageToVideo requires one existing image reference",
            )
        if explicit_mode == "firstLastFrame" and image_count < 2:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error="firstLastFrame requires existing first and last frame images",
            )
        if explicit_mode == "imageReference" and image_count < 1:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error="imageReference requires one existing image reference",
            )
        if explicit_mode == "videoEdit" and video_count < 1:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error="videoEdit requires one existing video reference",
            )
        if explicit_mode == "allReference" and not (
            image_count or video_count or audio_count
        ):
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error="allReference requires at least one existing reference",
            )

        model = self._select_generation_model(
            image_count=image_count,
            video_count=video_count,
            audio_count=audio_count,
            explicit_mode=explicit_mode,
        )

        # 构建请求体
        request_body = {
            "model": model,
            "content": content,
            "resolution": resolution,
            "ratio": ratio,
            "duration": duration,
            "watermark": False,
        }
        if audio:
            request_body["audio"] = True

        ref_summary = f"{image_count}图+{video_count}视频+{audio_count}音频"
        log(
            f"正在提交 Seedance 2.0 视频生成任务 "
            f"({ref_summary}, model={model}, {duration}s, audio={audio})..."
        )
        progress(0.1)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self.endpoint}/contents/generations/tasks",
                    json=request_body,
                    headers=headers,
                )
                if resp.status_code != 200:
                    error_text = resp.text
                    log(f"任务提交失败: HTTP {resp.status_code} - {error_text[:500]}")
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error=f"Submit failed: HTTP {resp.status_code} - {error_text[:200]}",
                    )
                data = resp.json()
                task_id = data.get("id")
                if not task_id:
                    log(f"未获取到 task_id: {data}")
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error=f"No task_id in response: {data}",
                    )
                log(f"任务已提交: {task_id}")
        except Exception as e:
            log(f"任务提交异常: {e}")
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=f"Submit exception: {e}",
            )

        # 轮询结果
        progress(0.2)
        for poll_count in range(max_polls):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(
                        f"{self.endpoint}/contents/generations/tasks/{task_id}",
                        headers=headers,
                    )
                    if resp.status_code != 200:
                        log(f"查询状态失败: HTTP {resp.status_code}")
                        await asyncio.sleep(poll_interval)
                        continue

                    data = resp.json()
                    status_str = data.get("status", "")
                    poll_progress = 0.2 + (poll_count / max_polls) * 0.7
                    progress(poll_progress)

                    if status_str == "succeeded":
                        log("视频生成完成，正在下载...")
                        progress(0.9)

                        video_url = None
                        resp_content = data.get("content")
                        if isinstance(resp_content, dict):
                            video_url = resp_content.get("video_url")
                        elif isinstance(resp_content, list):
                            for item in resp_content:
                                if isinstance(item, dict) and item.get("video_url"):
                                    video_url = item["video_url"]
                                    break

                        if video_url:
                            success = await self._download_video(video_url, output_path)
                            if success:
                                log(f"视频已保存: {output_path}")
                                progress(1.0)
                                return VideoGenResult(
                                    status=VideoGenStatus.DONE,
                                    video_path=output_path,
                                    video_url=video_url,
                                    task_id=task_id,
                                    duration_seconds=float(duration),
                                )
                            else:
                                log("视频下载失败")
                                return VideoGenResult(
                                    status=VideoGenStatus.FAILED,
                                    error="Download failed",
                                    task_id=task_id,
                                )
                        else:
                            log(f"API 未返回视频 URL: {data}")
                            return VideoGenResult(
                                status=VideoGenStatus.FAILED,
                                error="No video URL in response",
                                task_id=task_id,
                            )

                    elif status_str == "failed":
                        error_msg = data.get("error", {}).get(
                            "message", "Unknown error"
                        )
                        log(f"视频生成失败: {error_msg}")
                        return VideoGenResult(
                            status=VideoGenStatus.FAILED,
                            error=f"Generation failed: {error_msg}",
                            task_id=task_id,
                        )

                    if poll_count % 6 == 0:
                        log(f"正在生成中... ({status_str}, {poll_count}/{max_polls})")

            except Exception as e:
                log(f"查询状态异常: {e}")

            await asyncio.sleep(poll_interval)

        log("视频生成超时")
        return VideoGenResult(
            status=VideoGenStatus.FAILED,
            error="Timeout waiting for video generation",
            task_id=task_id,
        )

def _seedance2_config_mapping(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"final_prompt": text}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _seedance2_duration_from_config(config: dict, fallback: float) -> float:
    if "duration" not in config:
        return fallback
    try:
        return int(float(config.get("duration") or fallback))
    except (TypeError, ValueError):
        return fallback
