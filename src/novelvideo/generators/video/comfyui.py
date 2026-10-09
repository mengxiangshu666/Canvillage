"""ComfyUI local video generator adapter."""

from __future__ import annotations

import asyncio
import json
import os
import random
import urllib.parse
import uuid
from pathlib import Path
from typing import Callable, Optional

import aiohttp
import websockets

from .base import VideoGenResult, VideoGenStatus, VideoGeneratorBase

__all__ = ["ComfyUIVideoGenerator"]


class ComfyUIVideoGenerator(VideoGeneratorBase):
    """ComfyUI Wan2.2 5B 视频生成器。

    直接与 ComfyUI 服务器通信进行图生视频。
    使用 WebSocket 实时监听生成进度。

    支持三种工作流模式：
    - GGUF: 低显存模式，使用 GGUF 量化模型（~8GB VRAM）
    - fp8 I2V: 标准质量模式，单帧输入生成视频（~16GB VRAM）
    - fp8 FLF: 首尾帧模式，两帧输入生成过渡视频（~16GB VRAM）

    示例:
        >>> # GGUF 模式（默认，低显存）
        >>> generator = ComfyUIVideoGenerator(workflow_type="gguf")
        >>> result = await generator.generate(
        ...     image_path="frame.png",
        ...     prompt="角色微笑点头",
        ...     output_path="output.mp4",
        ...     duration=5.0,
        ... )

        >>> # fp8 I2V 模式（高质量）
        >>> generator = ComfyUIVideoGenerator(workflow_type="fp8")
        >>> result = await generator.generate(
        ...     image_path="frame.png",
        ...     prompt="角色微笑点头",
        ...     output_path="output.mp4",
        ...     duration=5.0,
        ... )

        >>> # fp8 FLF 模式（首尾帧过渡）
        >>> generator = ComfyUIVideoGenerator(workflow_type="fp8")
        >>> result = await generator.generate(
        ...     image_path="frame_start.png",
        ...     prompt="smooth transition",
        ...     output_path="output.mp4",
        ...     last_frame_path="frame_end.png",  # 触发 FLF 模式
        ... )
    """

    # ComfyUI 服务器配置
    DEFAULT_ADDRESS = "u864639-76942a5c8e3b.westd.seetacloud.com:8443"
    LTX23_DEFAULT_ADDRESS = "u864639-7730b46a98f9.westd.seetacloud.com:8443"
    DEFAULT_USE_SSL = True  # 云服务器使用 HTTPS/WSS
    FPS = 24

    # FLF 模式固定帧数（~3.3 秒）
    FLF_FRAMES = 81

    # LTX 2.3 帧率（与 Wan 2.2 的 24fps 不同）
    LTX23_FPS = 25

    # 工作流模板路径
    GGUF_WORKFLOW_PATH = Path(__file__).parent / "wan2-2-I2V-GGUF-LightX2V.json"
    FP8_I2V_WORKFLOW_PATH = Path(__file__).parent / "wan2-2-I2V-LightX2V.json"
    FP8_FLF_WORKFLOW_PATH = Path(__file__).parent / "wan2-2-FLF-LightX2V.json"
    LTX23_I2V_WORKFLOW_PATH = Path(__file__).parent / "ltx2-3-I2V.json"

    # 节点映射配置（不同工作流的节点 ID）
    NODE_MAPPING = {
        "gguf": {
            "input_image": "62",
            "frame_count": "63",  # WanImageToVideo.length
            "positive_prompt": "6",
            "negative_prompt": "7",
            "seed": "57",
            "video_output": "61",
        },
        "fp8_i2v": {
            "input_image": "34",
            "frame_count": "20",  # Int node
            "positive_prompt": "29",
            "negative_prompt": "3",
            "seed_high": "94",
            "seed_low": "96",
            "video_output": "19",
        },
        "fp8_flf": {
            "first_image": "119",
            "last_image": "125",
            "positive_prompt": "6",
            "negative_prompt": "7",
            "seed": "57",
            "video_output": "67",
        },
        "ltx23": {
            "input_image": "98",
            "frame_count": "167:146",  # PrimitiveInt.value
            "positive_prompt": "167:164",  # TextGenerateLTX2Prompt.prompt
            "negative_prompt": "167:159",  # CLIPTextEncode.text
            "seed_high": "167:135",  # RandomNoise
            "seed_low": "167:165",  # RandomNoise
            "video_output": "75",  # SaveVideo
        },
    }

    def __init__(
        self,
        server_address: Optional[str] = None,
        timeout: float = 600.0,
        workflow_type: str = "gguf",
        use_ssl: Optional[bool] = None,
    ):
        """初始化 ComfyUI 生成器。

        Args:
            server_address: ComfyUI 服务器地址（默认从环境变量读取）
            timeout: 请求超时时间（秒）
            workflow_type: 工作流类型 ("gguf" 或 "fp8")，默认 "gguf"
            use_ssl: 是否使用 HTTPS/WSS（默认从环境变量读取）
        """
        # LTX 2.3 使用独立 ComfyUI 服务器（有 LTX 节点）
        if server_address:
            self.server_address = server_address
        elif workflow_type.lower() == "ltx23":
            self.server_address = os.environ.get(
                "COMFYUI_LTX23_ADDRESS", self.LTX23_DEFAULT_ADDRESS
            )
        else:
            self.server_address = os.environ.get(
                "COMFYUI_ADDRESS", self.DEFAULT_ADDRESS
            )
        self.timeout = timeout
        self.workflow_type = workflow_type.lower()

        # SSL 配置
        if use_ssl is None:
            use_ssl_env = os.environ.get("COMFYUI_USE_SSL", "").lower()
            self.use_ssl = use_ssl_env in ("true", "1", "yes")
        else:
            self.use_ssl = use_ssl

        # 构建 URL
        http_scheme = "https" if self.use_ssl else "http"
        ws_scheme = "wss" if self.use_ssl else "ws"
        self.http_url = f"{http_scheme}://{self.server_address}"
        self.ws_url = f"{ws_scheme}://{self.server_address}"

        # 加载工作流模板
        self._workflow_templates = {}
        for name, path in [
            ("gguf", self.GGUF_WORKFLOW_PATH),
            ("fp8_i2v", self.FP8_I2V_WORKFLOW_PATH),
            ("fp8_flf", self.FP8_FLF_WORKFLOW_PATH),
            ("ltx23", self.LTX23_I2V_WORKFLOW_PATH),
        ]:
            if path.exists():
                with open(path, "r") as f:
                    self._workflow_templates[name] = json.load(f)

        # 兼容旧代码
        if self.workflow_type == "ltx23":
            self._workflow_template = self._workflow_templates.get("ltx23")
        else:
            self._workflow_template = self._workflow_templates.get(
                "fp8_i2v" if self.workflow_type == "fp8" else "gguf"
            )

    async def _upload_image(self, image_bytes: bytes, filename: str) -> dict:
        """上传图片到 ComfyUI input 文件夹。"""
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            data = aiohttp.FormData()
            data.add_field(
                "image", image_bytes, filename=filename, content_type="image/png"
            )
            async with session.post(
                f"{self.http_url}/upload/image", data=data
            ) as response:
                if response.status != 200:
                    raise Exception(f"上传图片失败: {await response.text()}")
                return await response.json()

    async def _queue_prompt(self, workflow: dict, client_id: str) -> dict:
        """提交工作流到 ComfyUI 队列。"""
        p = {"prompt": workflow, "client_id": client_id}
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{self.http_url}/prompt", json=p) as response:
                if response.status != 200:
                    raise Exception(f"提交工作流失败: {await response.text()}")
                return await response.json()

    async def _get_history(self, prompt_id: str) -> dict:
        """获取执行历史。"""
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{self.http_url}/history/{prompt_id}") as response:
                if response.status != 200:
                    raise Exception(f"获取历史失败: {await response.text()}")
                return await response.json()

    async def _download_video(self, filename: str, subfolder: str = "") -> bytes:
        """从 ComfyUI 下载视频文件。"""
        params = {"filename": filename, "subfolder": subfolder, "type": "output"}
        url = f"{self.http_url}/view?{urllib.parse.urlencode(params)}"
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    raise Exception(f"下载视频失败: {await response.text()}")
                return await response.read()

    async def generate(
        self,
        image_path: str,
        prompt: str,
        output_path: str,
        aspect_ratio: str = "9:16",
        duration: float = 5.0,
        on_log: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[float], None]] = None,
        last_frame_path: Optional[str] = None,
        **kwargs,
    ) -> VideoGenResult:
        """生成视频。

        直接与 ComfyUI 通信，通过 WebSocket 监听进度。
        自动选择工作流：
        - 有 last_frame_path → FLF 模式 (fp8_flf)
        - 无 last_frame_path → I2V 模式 (gguf 或 fp8_i2v)

        Args:
            image_path: 首帧图像路径
            prompt: 动作描述
            output_path: 输出视频路径
            aspect_ratio: 宽高比（未使用，保留兼容）
            duration: 视频时长（秒），默认 5.0，最大 10.0（FLF 模式固定 ~3.3s）
            on_log: 日志回调函数
            on_progress: 进度回调函数 (0.0 ~ 1.0)
            last_frame_path: 尾帧图像路径（可选，提供时启用 FLF 模式）

        Returns:
            生成结果
        """

        def log(msg: str):
            if on_log:
                on_log(msg)

        def progress(value: float):
            if on_progress:
                on_progress(value)

        # 判断是否使用 FLF 模式
        use_flf_mode = last_frame_path is not None

        # 选择工作流
        if use_flf_mode:
            workflow_key = "fp8_flf"
            mode_desc = "FLF (首尾帧过渡)"
        elif self.workflow_type == "ltx23":
            workflow_key = "ltx23"
            mode_desc = "LTX 2.3 I2V"
        elif self.workflow_type == "fp8":
            workflow_key = "fp8_i2v"
            mode_desc = "fp8 I2V"
        else:
            workflow_key = "gguf"
            mode_desc = "GGUF I2V"

        # 检查工作流模板
        workflow_template = self._workflow_templates.get(workflow_key)
        if workflow_template is None:
            workflow_path = {
                "gguf": self.GGUF_WORKFLOW_PATH,
                "fp8_i2v": self.FP8_I2V_WORKFLOW_PATH,
                "fp8_flf": self.FP8_FLF_WORKFLOW_PATH,
                "ltx23": self.LTX23_I2V_WORKFLOW_PATH,
            }.get(workflow_key, "unknown")
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=f"工作流模板不存在: {workflow_path}",
            )

        # 获取节点映射
        node_map = self.NODE_MAPPING.get(workflow_key, {})

        # 检查首帧图片
        if not os.path.exists(image_path):
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=f"首帧图片不存在: {image_path}",
            )

        # 检查尾帧图片（FLF 模式）
        if use_flf_mode and not os.path.exists(last_frame_path):
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=f"尾帧图片不存在: {last_frame_path}",
            )

        client_id = str(uuid.uuid4())
        first_image_filename = f"first_{client_id}.png"
        last_image_filename = f"last_{client_id}.png" if use_flf_mode else None

        # FLF 模式固定帧数
        if use_flf_mode:
            frames = self.FLF_FRAMES
            actual_duration = frames / self.FPS
            log(
                f"开始生成视频 | 模式: {mode_desc} | 固定帧数: {frames} (~{actual_duration:.1f}s)"
            )
        else:
            fps = self.LTX23_FPS if workflow_key == "ltx23" else self.FPS
            frames = int(duration * fps) + 1  # +1 确保时长足够
            log(f"开始生成视频 | 模式: {mode_desc} | 时长: {duration}s")

        try:
            # 1. 读取并上传图片
            log("上传图片到 ComfyUI...")
            with open(image_path, "rb") as f:
                first_image_bytes = f.read()
            await self._upload_image(first_image_bytes, first_image_filename)
            log(f"首帧已上传: {first_image_filename}")

            # FLF 模式：上传尾帧
            if use_flf_mode:
                with open(last_frame_path, "rb") as f:
                    last_image_bytes = f.read()
                await self._upload_image(last_image_bytes, last_image_filename)
                log(f"尾帧已上传: {last_image_filename}")

            # 2. 准备工作流
            log("准备工作流...")
            workflow = json.loads(json.dumps(workflow_template))

            # 设置输入图片（根据工作流类型）
            if workflow_key == "fp8_flf":
                # FLF 模式：设置首尾帧
                workflow[node_map["first_image"]]["inputs"]["image"] = (
                    first_image_filename
                )
                workflow[node_map["last_image"]]["inputs"]["image"] = (
                    last_image_filename
                )
            elif workflow_key == "ltx23":
                # LTX 2.3 I2V 模式
                workflow[node_map["input_image"]]["inputs"]["image"] = (
                    first_image_filename
                )
                # 设置帧数（PrimitiveInt.value）
                workflow[node_map["frame_count"]]["inputs"]["value"] = frames
                log(f"帧数: {frames} (duration={duration}s, fps={self.LTX23_FPS})")
            elif workflow_key == "fp8_i2v":
                # fp8 I2V 模式
                workflow[node_map["input_image"]]["inputs"]["image"] = (
                    first_image_filename
                )
                # 设置帧数
                workflow[node_map["frame_count"]]["inputs"]["Number"] = frames
                log(f"帧数: {frames} (duration={duration}s, fps={self.FPS})")
            else:
                # GGUF 模式
                workflow[node_map["input_image"]]["inputs"]["image"] = (
                    first_image_filename
                )
                # 设置帧数（WanImageToVideo.length）
                workflow[node_map["frame_count"]]["inputs"]["length"] = frames
                log(f"帧数: {frames} (duration={duration}s, fps={self.FPS})")

            # 设置提示词（LTX23 用 "prompt" 字段，其余用 "text"）
            prompt_field = "prompt" if workflow_key == "ltx23" else "text"
            workflow[node_map["positive_prompt"]]["inputs"][prompt_field] = prompt or ""
            # 负向提示词保持默认（已在模板中设置）
            if prompt:
                log(f"提示词: {prompt[:80]}{'...' if len(prompt) > 80 else ''}")

            # 设置随机种子
            seed = random.randint(0, 0xFFFFFFFFFFFF)
            if workflow_key in ("fp8_i2v", "ltx23"):
                # fp8 I2V / LTX 2.3 有两个采样器
                workflow[node_map["seed_high"]]["inputs"]["noise_seed"] = seed
                workflow[node_map["seed_low"]]["inputs"]["noise_seed"] = seed
            else:
                workflow[node_map["seed"]]["inputs"]["noise_seed"] = seed
            log(f"随机种子: {seed}")

            # 3. 连接 WebSocket
            log("连接 WebSocket...")
            ws_connect_url = f"{self.ws_url}/ws?clientId={client_id}"
            ws = await websockets.connect(
                ws_connect_url,
                max_size=500 * 1024 * 1024,
                ping_interval=None,
                ping_timeout=None,
                proxy=None,  # 禁用代理检测
            )

            try:
                # 4. 提交工作流
                log("提交工作流到队列...")
                result = await self._queue_prompt(workflow, client_id)
                prompt_id = result.get("prompt_id")
                if not prompt_id:
                    raise Exception("未获取到 prompt_id")
                log(f"prompt_id: {prompt_id}")

                # 5. 监听 WebSocket 消息
                log("等待 ComfyUI 执行...")
                current_node = None
                while True:
                    try:
                        out = await asyncio.wait_for(ws.recv(), timeout=self.timeout)
                    except asyncio.TimeoutError as exc:
                        raise TimeoutError(
                            f"ComfyUI 无响应: {self.timeout:.0f} 秒内未收到进度消息"
                        ) from exc
                    if isinstance(out, str):
                        message = json.loads(out)
                        msg_type = message.get("type")

                        if msg_type == "executing":
                            data = message.get("data", {})
                            if data.get("prompt_id") == prompt_id:
                                node = data.get("node")
                                if node is None:
                                    log("推理完成!")
                                    break
                                elif node != current_node:
                                    current_node = node
                                    node_title = (
                                        workflow.get(node, {})
                                        .get("_meta", {})
                                        .get("title", node)
                                    )
                                    log(f"执行节点: {node_title}")

                        elif msg_type == "progress":
                            data = message.get("data", {})
                            value = data.get("value", 0)
                            max_val = data.get("max", 100)
                            pct = value / max_val * 100 if max_val > 0 else 0
                            log(f"进度: {value}/{max_val} ({pct:.0f}%)")
                            # 更新进度 (0.0 ~ 1.0)
                            progress(value / max_val if max_val > 0 else 0)

                        elif msg_type == "execution_error":
                            error_data = message.get("data", {})
                            raise Exception(f"执行错误: {error_data}")

            finally:
                await ws.close()

            # 6. 获取输出文件名
            log("获取输出文件...")
            await asyncio.sleep(0.5)

            # 获取输出节点 ID
            output_node_id = node_map.get("video_output", "61")

            for retry in range(3):
                history = await self._get_history(prompt_id)
                if prompt_id in history:
                    outputs = history[prompt_id].get("outputs", {})
                    # VHS_VideoCombine 的输出在 "gifs" 字段
                    video_output = outputs.get(output_node_id, {}).get("gifs", [])
                    # SaveVideo 的输出在 "images" 字段
                    if not video_output:
                        video_output = outputs.get(output_node_id, {}).get("images", [])
                    if video_output:
                        break
                log(f"重试 {retry + 1}/3: 等待历史记录...")
                await asyncio.sleep(1)
            else:
                raise Exception(f"未找到视频输出 (节点 {output_node_id})")

            video_info = video_output[0]
            video_filename = video_info.get("filename")
            video_subfolder = video_info.get("subfolder", "")
            log(
                f"输出文件: {video_subfolder}/{video_filename}"
                if video_subfolder
                else f"输出文件: {video_filename}"
            )

            if not video_filename:
                raise Exception("未找到视频文件名")

            # 7. 下载视频
            log("下载视频...")
            video_bytes = await self._download_video(video_filename, video_subfolder)

            # 8. 保存到本地
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(video_bytes)

            log(f"视频已保存: {output_path} ({len(video_bytes) / 1024 / 1024:.2f} MB)")

            return VideoGenResult(
                status=VideoGenStatus.DONE,
                video_path=output_path,
                duration_seconds=duration,
            )

        except Exception as e:
            log(f"错误: {e}")
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=str(e),
            )
