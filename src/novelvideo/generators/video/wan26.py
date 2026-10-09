"""Alibaba DashScope Wan 2.6 video generator adapter."""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Callable, Optional

import aiohttp

from .base import VideoGenResult, VideoGenStatus, VideoGeneratorBase

__all__ = ["Wan26VideoGenerator"]


class Wan26VideoGenerator(VideoGeneratorBase):
    """阿里云 DashScope Wan2.6-i2v-flash 视频生成器。

    优势：
    - 时长灵活：2-15 秒，可根据 TTS 时长动态调整
    - 有声视频：audio=True，720P 约 0.3 元/秒，1080P 约 0.5 元/秒
    - 高质量：基于 Wan 2.6 模型

    支持两种模式：
    - 普通 I2V 模式（wan2.6-i2v-flash）：单帧输入，2-15 秒
    - 首尾帧模式（wan2.2-kf2v-flash）：首尾帧输入，固定 5 秒

    API 文档: https://www.alibabacloud.com/help/en/model-studio/image-to-video-api-reference

    示例:
        >>> generator = Wan26VideoGenerator()
        >>> # 普通 I2V 模式
        >>> result = await generator.generate(
        ...     image_path="frame.png",
        ...     prompt="character smiling and waving",
        ...     output_path="output.mp4",
        ...     duration=8.0,
        ... )
        >>> # 首尾帧模式
        >>> result = await generator.generate(
        ...     image_path="frame_start.png",
        ...     prompt="transition motion description",
        ...     output_path="output.mp4",
        ...     last_frame_path="frame_end.png",  # 启用首尾帧模式
        ... )
    """

    # 模型配置
    MODEL_I2V = "wan2.6-i2v-flash"  # 普通单帧模式
    MODEL_KF2V = "wan2.2-kf2v-flash"  # 首尾帧模式
    MODEL = MODEL_I2V  # 默认模型（向后兼容）
    MIN_DURATION = 2.0
    MAX_DURATION = 15.0  # API 限制 2-15 秒（仅普通模式）
    KF2V_DURATION = 5.0  # 首尾帧模式固定 5 秒
    DEFAULT_RESOLUTION = "720P"

    def __init__(
        self,
        api_key: Optional[str] = None,
        region: str = "cn",  # "cn" 或 "intl"
    ):
        """初始化 Wan2.6 生成器。

        Args:
            api_key: DashScope API Key（默认从环境变量读取）
            region: 区域，"cn" 为中国区，"intl" 为国际区（新加坡）
        """
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY")
        self.region = region

        if not self.api_key:
            raise ValueError(
                "DASHSCOPE_API_KEY must be set for Wan2.6 video generation"
            )

    async def _download_video(
        self, url: str, output_path: str, max_retries: int = 3
    ) -> bool:
        """下载视频文件，失败自动重试。"""
        for attempt in range(1, max_retries + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            print(
                                f"[download] attempt {attempt}/{max_retries} failed: HTTP {resp.status}"
                            )
                            if attempt < max_retries:
                                await asyncio.sleep(2 * attempt)
                                continue
                            return False

                        os.makedirs(os.path.dirname(output_path), exist_ok=True)
                        with open(output_path, "wb") as f:
                            f.write(await resp.read())

                        return True
            except Exception as e:
                print(f"[download] attempt {attempt}/{max_retries} error: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2 * attempt)
        return False

    def _compress_image_for_upload(
        self,
        image_path: str,
        quality: int = 95,
        on_log: Optional[Callable[[str], None]] = None,
    ) -> Optional[str]:
        """压缩图片为 JPEG 格式以减少上传时间。

        quality 默认 95：视频模型逐像素参考首帧，需保留足够细节。

        Args:
            image_path: 原始图片路径
            quality: JPEG 压缩质量 (1-100)
            on_log: 日志回调函数

        Returns:
            压缩后的临时文件路径，失败返回 None
        """
        try:
            from PIL import Image

            log = on_log or (lambda x: None)

            # 获取原始文件大小
            original_size = os.path.getsize(image_path)

            # 读取图片并转换为 RGB（JPEG 不支持 RGBA）
            img = Image.open(image_path)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            # 创建临时文件
            fd, temp_path = tempfile.mkstemp(suffix=".jpg")
            os.close(fd)

            # 保存为 JPEG
            img.save(temp_path, "JPEG", quality=quality, optimize=True)

            # 获取压缩后文件大小
            compressed_size = os.path.getsize(temp_path)

            # 格式化文件大小
            def format_size(size_bytes: int) -> str:
                if size_bytes >= 1024 * 1024:
                    return f"{size_bytes / (1024 * 1024):.1f}MB"
                elif size_bytes >= 1024:
                    return f"{size_bytes / 1024:.0f}KB"
                else:
                    return f"{size_bytes}B"

            log(
                f"图片压缩: {format_size(original_size)} → {format_size(compressed_size)}"
            )

            return temp_path

        except Exception as e:
            if on_log:
                on_log(f"图片压缩失败，使用原图: {e}")
            return None

    async def generate(
        self,
        image_path: str,
        prompt: str,
        output_path: str,
        aspect_ratio: str = "9:16",
        duration: float = 5.0,
        poll_interval: float = 5.0,
        max_polls: int = 120,
        on_log: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[float], None]] = None,
        last_frame_path: Optional[str] = None,
        **kwargs,
    ) -> VideoGenResult:
        """完整生成流程：上传 + 提交 + 轮询 + 下载。

        Args:
            image_path: 首帧图像路径（本地路径或 URL）
            prompt: 动作描述
            output_path: 输出视频路径
            aspect_ratio: 宽高比（由输入图像决定）
            duration: 目标时长（普通模式 2-15 秒，首尾帧模式固定 5 秒）
            poll_interval: 轮询间隔（秒）
            max_polls: 最大轮询次数
            on_log: 日志回调函数
            on_progress: 进度回调函数 (0.0 - 1.0)
            last_frame_path: 尾帧图像路径（可选，提供时启用首尾帧模式）

        Returns:
            生成结果
        """

        def log(msg: str):
            if on_log:
                on_log(msg)

        def progress(value: float):
            if on_progress:
                on_progress(value)

        try:
            import dashscope
            from dashscope import VideoSynthesis
            from http import HTTPStatus
        except ImportError:
            log("错误: 请安装 dashscope SDK: pip install dashscope>=1.25.2")
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error="dashscope SDK not installed",
            )

        # 设置区域
        if self.region == "intl":
            dashscope.base_http_api_url = "https://dashscope-intl.aliyuncs.com/api/v1"

        # 判断是否使用首尾帧模式
        use_keyframe_mode = last_frame_path is not None

        if use_keyframe_mode:
            # 首尾帧模式：固定 5 秒
            duration = self.KF2V_DURATION
            model = self.MODEL_KF2V
            log(f"使用首尾帧模式 ({model})，固定时长 {duration:.0f}s")
        else:
            # 普通 I2V 模式：限制时长在有效范围内
            model = self.MODEL_I2V
            original_duration = duration
            duration = max(self.MIN_DURATION, min(duration, self.MAX_DURATION))
            if duration != original_duration:
                log(
                    f"时长已调整: {original_duration:.1f}s -> {duration:.1f}s (API 限制 {self.MIN_DURATION}-{self.MAX_DURATION}s)"
                )

        # 临时压缩文件列表，用于最后清理
        temp_files: list[str] = []

        # 1. 准备首帧图像 URL
        # DashScope 支持本地文件路径、URL 和 Base64
        if image_path.startswith(("http://", "https://")):
            image_url = image_path
        elif os.path.exists(image_path):
            # 本地文件 - 压缩后上传以节省带宽
            compressed_path = self._compress_image_for_upload(image_path, on_log=log)
            if compressed_path:
                image_url = compressed_path
                temp_files.append(compressed_path)
            else:
                image_url = image_path
            log(f"使用本地首帧: {image_path}")
        else:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=f"First frame not found: {image_path}",
            )

        # 2. 如果是首尾帧模式，准备尾帧图像 URL
        last_frame_url = None
        if use_keyframe_mode:
            if last_frame_path.startswith(("http://", "https://")):
                last_frame_url = last_frame_path
            elif os.path.exists(last_frame_path):
                # 本地文件 - 压缩后上传以节省带宽
                compressed_path = self._compress_image_for_upload(
                    last_frame_path, on_log=log
                )
                if compressed_path:
                    last_frame_url = compressed_path
                    temp_files.append(compressed_path)
                else:
                    last_frame_url = last_frame_path
                log(f"使用本地尾帧: {last_frame_path}")
            else:
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error=f"Last frame not found: {last_frame_path}",
                )

        # 3. 提交任务
        mode_desc = (
            f"首尾帧模式 ({model})" if use_keyframe_mode else f"I2V 模式 ({model})"
        )
        log(f"正在提交视频生成任务 ({mode_desc}, {duration:.0f}s)...")
        progress(0.1)

        try:
            if use_keyframe_mode:
                # 首尾帧模式
                rsp = VideoSynthesis.async_call(
                    api_key=self.api_key,
                    model=model,
                    prompt=prompt,
                    first_frame_url=image_url,
                    last_frame_url=last_frame_url,
                    resolution=self.DEFAULT_RESOLUTION,
                    prompt_extend=True,
                    watermark=False,
                    audio=True,
                )
            else:
                # 普通 I2V 模式
                rsp = VideoSynthesis.async_call(
                    api_key=self.api_key,
                    model=model,
                    prompt=prompt,
                    img_url=image_url,
                    resolution=self.DEFAULT_RESOLUTION,
                    duration=int(duration),
                    prompt_extend=True,
                    watermark=False,
                    audio=True,
                )

            if rsp.status_code != 200:
                log(f"任务提交失败: {rsp.code} - {rsp.message}")
                for f in temp_files:
                    if os.path.exists(f):
                        os.remove(f)
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error=f"Submit failed: {rsp.code} - {rsp.message}",
                )

            task_id = rsp.output.task_id
            log(f"任务已提交: {task_id}")

        except Exception as e:
            log(f"任务提交异常: {e}")
            for f in temp_files:
                if os.path.exists(f):
                    os.remove(f)
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=f"Submit exception: {e}",
            )

        # 3. 轮询结果
        progress(0.2)
        for poll_count in range(max_polls):
            try:
                rsp = VideoSynthesis.fetch(task=task_id, api_key=self.api_key)
            except Exception as e:
                log(f"查询状态异常: {e}")
                await asyncio.sleep(poll_interval)
                continue

            # 进度从 0.2 到 0.9
            poll_progress = 0.2 + (poll_count / max_polls) * 0.7
            progress(poll_progress)

            if rsp.status_code == HTTPStatus.OK:
                task_status = rsp.output.task_status

                if task_status == "SUCCEEDED":
                    log("视频生成完成，正在下载...")
                    progress(0.9)

                    # 4. 下载视频
                    video_url = rsp.output.video_url
                    if video_url:
                        success = await self._download_video(video_url, output_path)
                        if success:
                            log(f"视频已保存: {output_path}")
                            progress(1.0)
                            for f in temp_files:
                                if os.path.exists(f):
                                    os.remove(f)
                            return VideoGenResult(
                                status=VideoGenStatus.DONE,
                                video_path=output_path,
                                video_url=video_url,
                                task_id=task_id,
                                duration_seconds=duration,
                            )
                        else:
                            log("视频下载失败")
                            for f in temp_files:
                                if os.path.exists(f):
                                    os.remove(f)
                            return VideoGenResult(
                                status=VideoGenStatus.FAILED,
                                error="Download failed",
                                task_id=task_id,
                            )
                    else:
                        log("API 未返回视频 URL")
                        for f in temp_files:
                            if os.path.exists(f):
                                os.remove(f)
                        return VideoGenResult(
                            status=VideoGenStatus.FAILED,
                            error="No video URL in response",
                            task_id=task_id,
                        )

                elif task_status == "FAILED":
                    error_msg = getattr(rsp.output, "message", "Unknown error")
                    log(f"视频生成失败: {error_msg}")
                    for f in temp_files:
                        if os.path.exists(f):
                            os.remove(f)
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error=f"Generation failed: {error_msg}",
                        task_id=task_id,
                    )

                # PENDING, RUNNING - 继续轮询
                if poll_count % 6 == 0:  # 每 30 秒输出一次
                    log(f"正在生成中... ({task_status}, {poll_count}/{max_polls})")

            await asyncio.sleep(poll_interval)

        log("视频生成超时")
        for f in temp_files:
            if os.path.exists(f):
                os.remove(f)
        return VideoGenResult(
            status=VideoGenStatus.FAILED,
            error="Timeout waiting for video generation",
            task_id=task_id,
        )
