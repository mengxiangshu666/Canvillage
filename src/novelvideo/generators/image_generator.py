"""火山引擎图像生成模块。

使用 Seedream 4.0 生成图像，SeedEdit 3.0 进行编辑。
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import httpx

from novelvideo.gateway_transport import newapi_httpx_client_kwargs
from pydantic import BaseModel, Field

from novelvideo.config import (
    IMAGE_GENERATION_SELECTIONS,
    get_character_image_selection,
    get_image_config,
    get_style_preset,
    normalize_explicit_image_generation_selection,
    normalize_character_image_selection,
)
from novelvideo.generators.image_request_policy import image_submit_timeout
from novelvideo.ports import get_usage_meter
from novelvideo.shared.billing_errors import is_insufficient_credits_error
from novelvideo.shared.provider_errors import preserve_provider_diagnostics
from novelvideo.styles.prompt_compiler import compile_style_prompt


def _provider_request_id_from_response(response: httpx.Response, result: dict[str, Any]) -> str:
    return (
        response.headers.get("x-request-id")
        or response.headers.get("x-newapi-request-id")
        or response.headers.get("x-oneapi-request-id")
        or str(result.get("request_id") or result.get("requestId") or "").strip()
    )


async def _reserve_image_model_call(
    model: str,
    *,
    source: str,
    billing_params: Optional[dict[str, Any]] = None,
    billing_quantity: int | float | str | None = 1,
) -> str:
    return await get_usage_meter().reserve_current_model_call_credit(
        model=model,
        billing_kind="image",
        billing_params=billing_params,
        billing_quantity=billing_quantity,
        metadata={"source": source},
    )


async def _refund_image_model_call(
    reservation_id: str,
    *,
    source: str,
    error: str,
) -> None:
    if not reservation_id:
        return
    try:
        await get_usage_meter().refund_model_call_credit_reservation(
            reservation_id,
            metadata={"source": source, "error": error[:200]},
        )
    except Exception:
        pass


async def _confirm_image_model_call(
    *,
    model: str,
    reservation_id: str,
    provider_request_id: str = "",
    response_id: str = "",
) -> None:
    try:
        await get_usage_meter().bump_model_call(
            user_id=None,
            model=model,
            provider_request_id=provider_request_id,
            credit_reservation_id=reservation_id,
            metadata={"response_id": response_id} if response_id else None,
        )
    except Exception:
        pass


class ImageGenParams(BaseModel):
    """图像生成参数（旧版，保留兼容）。"""

    prompt: str
    negative_prompt: str = ""
    width: int = 1024
    height: int = 1024
    style: str = "chinese_period_drama"  # 默认写实古装剧风格
    project_dir: str = ""  # 项目目录，用于读取项目级自定义风格

    # 参考图（用于角色一致性）
    reference_image: Optional[str] = None
    reference_strength: float = 0.7  # 参考行业最佳实践 70-85%

    # 其他参数
    seed: Optional[int] = None
    steps: int = 30
    cfg_scale: float = 7.0


class ImageGenerationRequest(BaseModel):
    """图像生成请求（支持多参考图）。

    适配即梦 4.0 API，支持最多 10 张参考图。
    """

    prompt: str = Field(description="中文提示词")
    negative_prompt: str = Field(default="", description="负向提示词")
    width: int = Field(default=720, description="图像宽度")
    height: int = Field(default=1280, description="图像高度（竖屏）")

    # 多参考图支持（最多10张）
    reference_images: list[str] = Field(
        default_factory=list,
        description="参考图路径列表（最多10张）",
    )
    reference_prompts: list[str] = Field(
        default_factory=list,
        description="每张参考图对应的角色描述（与 reference_images 一一对应）",
    )
    reference_scale: float = Field(
        default=0.7,  # 参考行业最佳实践 70-85%
        ge=0,
        le=1,
        description="参考图权重 0-1",
    )

    # 上一帧（用于连贯性）
    previous_frame: Optional[str] = Field(
        default=None,
        description="上一帧图片路径（用于帧间连贯性）",
    )

    # 其他参数
    seed: Optional[int] = None

    # 提示词控制
    skip_prompt_enhancement: bool = Field(
        default=False,
        description="跳过提示词增强，直接使用传入的 prompt",
    )

    # 草图渲染模式标志
    is_sketch_render: bool = Field(
        default=False,
        description="是否为草图渲染模式（用于调整 negative_prompt，移除 sketch 相关词以避免语义冲突）",
    )


class ImageGenResult(BaseModel):
    """图像生成结果。"""

    success: bool
    image_path: Optional[str] = None
    image_base64: Optional[str] = None
    error: Optional[str] = None
    generation_time: float = 0.0


class VolcengineImageGenerator:
    """火山引擎图像生成器。

    封装 Seedream 4.0 和 SeedEdit 3.0 API。

    示例:
        >>> generator = VolcengineImageGenerator()
        >>> result = await generator.generate(
        ...     prompt="anime girl with black hair",
        ...     output_path="output/image.png"
        ... )
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
    ):
        """初始化生成器。

        Args:
            api_key: API Key，默认从环境变量读取
            endpoint: API 端点，默认从配置读取
        """
        config = get_image_config()
        self.api_key = api_key or config["api_key"]
        self.endpoint = endpoint or config["endpoint"]
        self.seedream_model = config["seedream_model"]
        self.seededit_model = config["seededit_model"]
        self.default_width = config["default_width"]
        self.default_height = config["default_height"]
        self.default_style = config["default_style"]

        if not self.api_key:
            raise ValueError(
                "API key not set. "
                "Set VOLCENGINE_VISUAL_API_KEY or ARK_API_KEY environment variable."
            )

    def _compress_image(
        self,
        image_path: str,
        quality: int = 60,
        max_size: tuple = None,
    ) -> tuple[str, str]:
        """压缩参考图并返回 base64 和 mime_type。

        Args:
            image_path: 图像路径
            quality: JPEG 压缩质量 (1-100)
            max_size: 可选的最大尺寸限制 (width, height)

        Returns:
            (base64_data, mime_type)
        """
        from PIL import Image
        import io

        img = Image.open(image_path)
        original_size = os.path.getsize(image_path)

        # 可选：缩放到最大尺寸
        if max_size:
            img.thumbnail(max_size, Image.Resampling.LANCZOS)

        # 转为 RGB（JPEG 不支持 alpha）
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')

        # 压缩到内存
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=quality, optimize=True)
        image_data = buffer.getvalue()

        compressed_size = len(image_data)
        ratio = (1 - compressed_size / original_size) * 100
        print(f"[Seedream压缩] {os.path.basename(image_path)}: "
              f"{original_size/1024:.0f}KB → {compressed_size/1024:.0f}KB "
              f"({ratio:.0f}% 压缩)", flush=True)

        return base64.b64encode(image_data).decode(), "image/jpeg"

    async def generate(
        self,
        prompt: str,
        output_path: Optional[str] = None,
        negative_prompt: str = "",
        width: Optional[int] = None,
        height: Optional[int] = None,
        style: Optional[str] = None,
        project_dir: str = "",
        reference_image: Optional[str] = None,
        reference_strength: float = 0.7,  # 参考行业最佳实践 70-85%
    ) -> ImageGenResult:
        """生成图像。

        Args:
            prompt: 正向提示词
            output_path: 输出路径，如果提供则保存文件
            negative_prompt: 负向提示词
            width: 宽度
            height: 高度
            style: 风格
            reference_image: 参考图路径（用于角色一致性）
            reference_strength: 参考图强度

        Returns:
            生成结果
        """
        start_time = time.time()

        params = ImageGenParams(
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width or self.default_width,
            height=height or self.default_height,
            style=style or self.default_style,
            project_dir=project_dir,
            reference_image=reference_image,
            reference_strength=reference_strength,
        )

        try:
            # 调用 API
            image_base64 = await self._call_seedream_api(params)

            # 保存文件
            if output_path and image_base64:
                output_dir = os.path.dirname(output_path)
                if output_dir:
                    os.makedirs(output_dir, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(base64.b64decode(image_base64))

            generation_time = time.time() - start_time

            return ImageGenResult(
                success=True,
                image_path=output_path,
                image_base64=image_base64,
                generation_time=generation_time,
            )

        except Exception as e:
            if is_insufficient_credits_error(e):
                raise
            return ImageGenResult(
                success=False,
                error=str(e),
                generation_time=time.time() - start_time,
            )

    async def _call_seedream_api(self, params: ImageGenParams) -> str:
        """调用火山方舟 Seedream API。

        API 文档: https://www.volcengine.com/docs/82379/1541523
        """
        # 获取风格预设
        style_preset = get_style_preset(params.style, project_dir=params.project_dir)

        # 按图像模型族编译风格：静态画面使用材质/构图词，视频运动词不混入。
        enhanced_prompt, compiled_negative = compile_style_prompt(
            style_preset,
            modality="image",
            model=self.seedream_model,
            subject_prompt=params.prompt,
        )

        # 构建请求体 (火山方舟格式)
        request_body = {
            "model": self.seedream_model,
            "prompt": enhanced_prompt,
            "size": f"{params.width}x{params.height}",
            "n": 1,
            "response_format": "b64_json",
        }

        # 添加风格对应的负向提示词
        style_negative = compiled_negative
        if params.negative_prompt and style_negative:
            request_body["negative_prompt"] = f"{style_negative}, {params.negative_prompt}"
        elif style_negative:
            request_body["negative_prompt"] = style_negative
        elif params.negative_prompt:
            request_body["negative_prompt"] = params.negative_prompt

        if params.seed:
            request_body["seed"] = params.seed

        # 如果有参考图 (使用 image_urls 参数，格式为 data URL)
        if params.reference_image and os.path.exists(params.reference_image):
            ref_base64, mime_type = self._compress_image(params.reference_image, quality=60)
            data_url = f"data:{mime_type};base64,{ref_base64}"
            request_body["image_urls"] = [data_url]
            request_body["strength"] = params.reference_strength

        reservation_id = ""
        try:
            reservation_id = await _reserve_image_model_call(
                self.seedream_model,
                source="seedream_image_api",
            )
            # 发送请求
            async with httpx.AsyncClient(
                **newapi_httpx_client_kwargs(
                    base_url=self.endpoint, timeout=image_submit_timeout()
                )
            ) as client:
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                }

                # 火山方舟 API 端点
                api_url = f"{self.endpoint}/images/generations"

                response = await client.post(
                    api_url,
                    headers=headers,
                    json=request_body,
                )

                if response.status_code != 200:
                    raise Exception(f"API error: {response.status_code} - {response.text}")

                result = response.json()
                provider_request_id = _provider_request_id_from_response(response, result)
                response_id = str(result.get("id") or "").strip()

                # 提取图像数据
                if "data" in result and len(result["data"]) > 0:
                    image_base64 = result["data"][0].get("b64_json", "")
                    if not image_base64:
                        raise Exception(f"No image data in response: {result}")
                    await _confirm_image_model_call(
                        model=self.seedream_model,
                        reservation_id=reservation_id,
                        provider_request_id=provider_request_id,
                        response_id=response_id,
                    )
                    return image_base64

                raise Exception(f"No image data in response: {result}")
        except Exception as exc:
            await _refund_image_model_call(
                reservation_id,
                source="seedream_image_api",
                error=str(exc),
            )
            raise

    async def generate_with_request(
        self,
        request: ImageGenerationRequest,
        output_path: str,
    ) -> ImageGenResult:
        """使用新版请求格式生成图像（支持多参考图）。

        Args:
            request: 图像生成请求（支持多参考图）
            output_path: 输出路径

        Returns:
            生成结果
        """
        start_time = time.time()
        reservation_id = ""

        try:
            # 收集所有参考图（角色参考图 + 上一帧）
            all_refs = list(request.reference_images[:9])  # 最多9张角色参考图
            if request.previous_frame and os.path.exists(request.previous_frame):
                all_refs.append(request.previous_frame)  # 上一帧放最后

            # 构建 image_urls 数组
            image_urls = []
            for ref_path in all_refs[:10]:  # 最多10张
                if ref_path and os.path.exists(ref_path):
                    ref_base64, mime_type = self._compress_image(ref_path, quality=60)
                    data_url = f"data:{mime_type};base64,{ref_base64}"
                    image_urls.append(data_url)
                    print(f"[Seedream] 添加参考图: {ref_path}", flush=True)

            # 获取风格预设
            style_preset = get_style_preset(self.default_style)
            style_keywords, compiled_negative = compile_style_prompt(
                style_preset,
                modality="image",
                model=self.seedream_model,
            )

            # 构建增强提示词
            if request.skip_prompt_enhancement:
                # 跳过提示词增强，直接使用传入的 prompt
                enhanced_prompt = request.prompt
                print(f"[Seedream] 使用原始提示词（skip_prompt_enhancement=True）", flush=True)
                if image_urls:
                    print(f"[Seedream] 参考图数量: {len(image_urls)}, strength={request.reference_scale}", flush=True)
            elif image_urls and request.reference_prompts:
                # 有参考图和角色描述时：显式映射每张参考图对应的角色
                ref_mapping = "\n".join([
                    f"参考图{i+1}中的人物是：{prompt}"
                    for i, prompt in enumerate(request.reference_prompts)
                ])
                enhanced_prompt = (
                    f"保持人物外观与参考图一致，严格按照参考图中的人物年龄和外貌生成。\n"
                    f"{ref_mapping}\n\n"
                    f"场景描述：{style_keywords}, {request.prompt}"
                )
                print(f"[Seedream] 使用 {len(image_urls)} 张参考图（含角色描述），strength={request.reference_scale}", flush=True)
                print(f"[Seedream] 角色映射:\n{ref_mapping}", flush=True)
            elif image_urls:
                # 有参考图但没有描述（向后兼容）
                enhanced_prompt = (
                    f"保持人物外观与参考图一致，使用参考图中的角色形象。"
                    f"{style_keywords}, {request.prompt}"
                )
                print(f"[Seedream] 使用 {len(image_urls)} 张参考图，strength={request.reference_scale}", flush=True)
            else:
                enhanced_prompt = f"{style_keywords}, {request.prompt}"

            # 构建请求体
            request_body = {
                "model": self.seedream_model,
                "prompt": enhanced_prompt,
                "size": f"{request.width}x{request.height}",
                "n": 1,
                "response_format": "b64_json",
            }

            # 添加负向提示词
            # Seedream 草图渲染模式：完全禁用 negative_prompt
            # 避免任何词汇与草图参考产生语义冲突（仅 Seedream 模型适用此策略）
            if request.is_sketch_render:
                print(f"[Seedream] 草图渲染模式: 完全禁用 negative_prompt (Seedream专用)", flush=True)
                # 不设置 negative_prompt
            else:
                style_negative = compiled_negative
                if request.negative_prompt and style_negative:
                    request_body["negative_prompt"] = f"{style_negative}, {request.negative_prompt}"
                elif style_negative:
                    request_body["negative_prompt"] = style_negative
                elif request.negative_prompt:
                    request_body["negative_prompt"] = request.negative_prompt

            if request.seed:
                request_body["seed"] = request.seed

            if image_urls:
                request_body["image_urls"] = image_urls
                request_body["strength"] = request.reference_scale

            # === DEBUG: 打印完整请求参数 ===
            print("=" * 60, flush=True)
            print("[Seedream DEBUG] 完整 API 请求参数:", flush=True)
            print(f"  model: {request_body.get('model')}", flush=True)
            print(f"  size: {request_body.get('size')}", flush=True)
            print(f"  strength: {request_body.get('strength', 'N/A')}", flush=True)
            print(f"  image_urls 数量: {len(request_body.get('image_urls', []))}", flush=True)
            print(f"  negative_prompt: {request_body.get('negative_prompt', 'N/A')[:100]}...", flush=True)
            print("-" * 60, flush=True)
            print(f"[Seedream DEBUG] 完整 prompt:\n{request_body.get('prompt')}", flush=True)
            print("=" * 60, flush=True)

            reservation_id = await _reserve_image_model_call(
                self.seedream_model,
                source="seedream_image_request_api",
            )

            # 发送请求
            async with httpx.AsyncClient(
                **newapi_httpx_client_kwargs(
                    base_url=self.endpoint, timeout=image_submit_timeout()
                )
            ) as client:
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                }
                api_url = f"{self.endpoint}/images/generations"

                response = await client.post(
                    api_url,
                    headers=headers,
                    json=request_body,
                )

                if response.status_code != 200:
                    print(f"[Seedream] API 错误: {response.status_code}", flush=True)
                    print(f"[Seedream] 错误响应: {response.text[:500]}", flush=True)
                    raise Exception(f"API error: {response.status_code} - {response.text}")

                result = response.json()
                provider_request_id = _provider_request_id_from_response(response, result)
                response_id = str(result.get("id") or "").strip()

                # 诊断日志：打印响应结构
                print(f"[Seedream] API 响应字段: {list(result.keys())}", flush=True)

                if "data" in result and len(result["data"]) > 0:
                    image_data = result["data"][0]
                    print(f"[Seedream] data[0] 字段: {list(image_data.keys())}", flush=True)

                    # 尝试多个可能的字段名
                    image_base64 = (
                        image_data.get("b64_json") or
                        image_data.get("b64") or
                        image_data.get("image") or
                        ""
                    )

                    # 验证 image_base64 不为空
                    if not image_base64:
                        print(f"[Seedream] 警告: API 响应缺少图片数据!", flush=True)
                        print(f"[Seedream] 完整响应: {result}", flush=True)
                        await _refund_image_model_call(
                            reservation_id,
                            source="seedream_image_request_api",
                            error="missing_image_data",
                        )
                        return ImageGenResult(
                            success=False,
                            error="API 响应中没有图片数据（b64_json/b64/image 字段为空）",
                            image_path=output_path,
                            generation_time=time.time() - start_time,
                        )
                else:
                    raise Exception(f"No image data in response: {result}")

            # 保存文件
            if output_path and image_base64:
                output_dir = os.path.dirname(output_path)
                if output_dir:
                    os.makedirs(output_dir, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(base64.b64decode(image_base64))

                # 验证文件已创建且有效
                if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                    print(f"[Seedream] 错误: 文件保存失败或为空: {output_path}", flush=True)
                    await _refund_image_model_call(
                        reservation_id,
                        source="seedream_image_request_api",
                        error="empty_output_file",
                    )
                    return ImageGenResult(
                        success=False,
                        error="图片保存失败或文件为空",
                        image_path=output_path,
                        generation_time=time.time() - start_time,
                    )

                print(f"[Seedream] 图片已保存: {output_path} ({os.path.getsize(output_path)} bytes)", flush=True)

            generation_time = time.time() - start_time
            await _confirm_image_model_call(
                model=self.seedream_model,
                reservation_id=reservation_id,
                provider_request_id=provider_request_id,
                response_id=response_id,
            )

            return ImageGenResult(
                success=True,
                image_path=output_path,
                image_base64=image_base64,
                generation_time=generation_time,
            )

        except Exception as e:
            await _refund_image_model_call(
                reservation_id,
                source="seedream_image_request_api",
                error=str(e),
            )
            if is_insufficient_credits_error(e):
                raise
            return ImageGenResult(
                success=False,
                error=str(e),
                generation_time=time.time() - start_time,
            )

    async def upscale_with_img2img(
        self,
        input_path: str,
        output_path: str,
        target_width: int = 720,
        target_height: int = 1280,
        strength: float = 0.9,
        enhancement_prompt: str = None,
    ) -> ImageGenResult:
        """使用 Seedream 图生图做高清修复。

        原理：用低分辨率图作为参考，生成高分辨率版本。

        注意：Seedream API 中 strength 表示"与参考图的相似度"！
        - strength=0.9 意味着保持 90% 与原图相似（适合高清修复）
        - strength=0.5 意味着只保持 50% 相似，会有较大改变
        - 对于高清修复场景，建议使用 0.85-0.95

        Args:
            input_path: 输入图片路径（低分辨率）
            output_path: 输出路径（高分辨率）
            target_width: 目标宽度
            target_height: 目标高度
            strength: 相似度（0.85-0.95 适合高清修复，保留原图内容）
            enhancement_prompt: 增强提示词（可选）

        Returns:
            ImageGenResult
        """
        start_time = time.time()
        reservation_id = ""

        if not os.path.exists(input_path):
            return ImageGenResult(
                success=False,
                error=f"输入文件不存在: {input_path}",
                generation_time=time.time() - start_time,
            )

        try:
            # 构建增强 prompt
            if enhancement_prompt:
                prompt = enhancement_prompt
            else:
                prompt = (
                    "高清，清晰，细节丰富，保持原图内容和构图，"
                    "8K 分辨率，专业摄影，电影级画质"
                )

            # 获取风格预设
            style_preset = get_style_preset(self.default_style)
            enhanced_prompt, style_negative = compile_style_prompt(
                style_preset,
                modality="image",
                model=self.seedream_model,
                subject_prompt=prompt,
            )

            # 读取输入图作为参考
            with open(input_path, "rb") as f:
                ref_base64 = base64.b64encode(f.read()).decode()

            mime_type = "image/png" if input_path.lower().endswith(".png") else "image/jpeg"
            data_url = f"data:{mime_type};base64,{ref_base64}"

            # 构建请求体
            request_body = {
                "model": self.seedream_model,
                "prompt": enhanced_prompt,
                "size": f"{target_width}x{target_height}",
                "n": 1,
                "response_format": "b64_json",
                "image_urls": [data_url],
                "strength": strength,  # 高 strength = 高相似度 = 保留原图内容
            }

            # 添加负向提示词
            if style_negative:
                request_body["negative_prompt"] = style_negative

            print(f"[Seedream] 高清修复: {input_path} -> {target_width}x{target_height}, strength={strength}")

            reservation_id = await _reserve_image_model_call(
                self.seedream_model,
                source="seedream_upscale_api",
            )

            # 发送请求
            async with httpx.AsyncClient(
                **newapi_httpx_client_kwargs(
                    base_url=self.endpoint, timeout=image_submit_timeout()
                )
            ) as client:
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                }
                api_url = f"{self.endpoint}/images/generations"

                response = await client.post(
                    api_url,
                    headers=headers,
                    json=request_body,
                )

                if response.status_code != 200:
                    raise Exception(f"API error: {response.status_code} - {response.text}")

                result = response.json()
                provider_request_id = _provider_request_id_from_response(response, result)
                response_id = str(result.get("id") or "").strip()

                if "data" in result and len(result["data"]) > 0:
                    image_base64 = result["data"][0].get("b64_json", "")
                    if not image_base64:
                        raise Exception(f"No image data in response: {result}")
                else:
                    raise Exception(f"No image data in response: {result}")

            # 保存文件
            if output_path and image_base64:
                output_dir = os.path.dirname(output_path)
                if output_dir:
                    os.makedirs(output_dir, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(base64.b64decode(image_base64))

            generation_time = time.time() - start_time
            print(f"[Seedream] 高清修复完成: {output_path}, 耗时 {generation_time:.1f}s")
            await _confirm_image_model_call(
                model=self.seedream_model,
                reservation_id=reservation_id,
                provider_request_id=provider_request_id,
                response_id=response_id,
            )

            return ImageGenResult(
                success=True,
                image_path=output_path,
                image_base64=image_base64,
                generation_time=generation_time,
            )

        except Exception as e:
            await _refund_image_model_call(
                reservation_id,
                source="seedream_upscale_api",
                error=str(e),
            )
            if is_insufficient_credits_error(e):
                raise
            return ImageGenResult(
                success=False,
                error=str(e),
                generation_time=time.time() - start_time,
            )

    async def edit_with_seededit(
        self,
        input_path: str,
        output_path: str,
        prompt: str,
        guidance_scale: float = 5.5,
        seed: Optional[int] = None,
    ) -> ImageGenResult:
        """使用 SeedEdit 3.0 编辑图像。

        注意：SeedEdit 模型当前不可用（404），此方法暂时保留备用。

        Args:
            input_path: 输入图片路径
            output_path: 输出路径
            prompt: 编辑指令（描述要做什么修改）
            guidance_scale: 编辑强度（越高越遵循 prompt，推荐 3-7）
            seed: 随机种子（可选）
        """
        start_time = time.time()
        reservation_id = ""

        if not os.path.exists(input_path):
            return ImageGenResult(
                success=False,
                error=f"输入文件不存在: {input_path}",
                generation_time=time.time() - start_time,
            )

        try:
            # 读取图片为 base64 data URL
            with open(input_path, "rb") as f:
                ref_base64 = base64.b64encode(f.read()).decode()
            mime_type = "image/png" if input_path.lower().endswith(".png") else "image/jpeg"
            data_url = f"data:{mime_type};base64,{ref_base64}"

            # SeedEdit API 请求体（与 Seedream 不同：image 而非 image_urls）
            request_body = {
                "model": self.seededit_model,
                "prompt": prompt,
                "image": data_url,           # 单个 image，不是 image_urls 数组
                "size": "adaptive",          # 自适应原图尺寸
                "guidance_scale": guidance_scale,
                "response_format": "url",    # 官方示例用 url 格式
                "n": 1,
            }
            if seed:
                request_body["seed"] = seed

            print(f"[SeedEdit] 图像编辑: {input_path}, prompt={prompt[:50]}...", flush=True)
            print(f"[SeedEdit] model={self.seededit_model}, guidance_scale={guidance_scale}", flush=True)

            reservation_id = await _reserve_image_model_call(
                self.seededit_model,
                source="seededit_image_api",
            )

            async with httpx.AsyncClient(
                **newapi_httpx_client_kwargs(
                    base_url=self.endpoint, timeout=image_submit_timeout()
                )
            ) as client:
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                }
                api_url = f"{self.endpoint}/images/generations"
                response = await client.post(api_url, headers=headers, json=request_body)

                if response.status_code != 200:
                    error_text = response.text[:500]
                    print(f"[SeedEdit] API 错误: {response.status_code} - {error_text}", flush=True)
                    raise Exception(f"API error: {response.status_code} - {error_text}")

                result = response.json()
                provider_request_id = _provider_request_id_from_response(response, result)
                response_id = str(result.get("id") or "").strip()
                print(f"[SeedEdit] API 响应字段: {list(result.keys())}", flush=True)

                if "data" not in result or len(result["data"]) == 0:
                    raise Exception(f"No image data in response: {result}")

                image_data = result["data"][0]
                print(f"[SeedEdit] data[0] 字段: {list(image_data.keys())}", flush=True)

                # 优先用 b64_json，否则从 URL 下载
                image_base64 = image_data.get("b64_json") or image_data.get("b64") or ""
                image_url = image_data.get("url", "")

                if not image_base64 and image_url:
                    print(f"[SeedEdit] 从 URL 下载结果图...", flush=True)
                    dl_response = await client.get(image_url, timeout=image_submit_timeout())
                    if dl_response.status_code != 200:
                        raise Exception(f"下载结果图失败: {dl_response.status_code}")
                    image_bytes = dl_response.content
                    image_base64 = base64.b64encode(image_bytes).decode()
                elif not image_base64:
                    raise Exception(f"API 响应中无图片数据 (无 b64_json/url): {list(image_data.keys())}")

            # 保存文件
            if output_path and image_base64:
                output_dir = os.path.dirname(output_path)
                if output_dir:
                    os.makedirs(output_dir, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(base64.b64decode(image_base64))
                print(f"[SeedEdit] 图片已保存: {output_path} ({os.path.getsize(output_path)} bytes)", flush=True)

            generation_time = time.time() - start_time
            print(f"[SeedEdit] 编辑完成, 耗时 {generation_time:.1f}s", flush=True)
            await _confirm_image_model_call(
                model=self.seededit_model,
                reservation_id=reservation_id,
                provider_request_id=provider_request_id,
                response_id=response_id,
            )

            return ImageGenResult(
                success=True,
                image_path=output_path,
                image_base64=image_base64,
                generation_time=generation_time,
            )

        except Exception as e:
            print(f"[SeedEdit] 异常: {e}", flush=True)
            await _refund_image_model_call(
                reservation_id,
                source="seededit_image_api",
                error=str(e),
            )
            if is_insufficient_credits_error(e):
                raise
            return ImageGenResult(
                success=False,
                error=str(e),
                generation_time=time.time() - start_time,
            )

    async def generate_character_reference(
        self,
        character_name: str,
        appearance_prompt: str,
        output_dir: str,
        count: int = 3,
        style: str = None,
        project_dir: str = "",
    ) -> list[str]:
        """生成角色参考图。

        生成多张参考图供人工选择。

        Args:
            character_name: 角色名
            appearance_prompt: 外貌 Prompt
            output_dir: 输出目录
            count: 生成数量
            style: 风格名称（用于注入项目风格预设）

        Returns:
            生成的图片路径列表
        """
        os.makedirs(output_dir, exist_ok=True)

        style = style or self.default_style
        style_preset = get_style_preset(style, project_dir=project_dir)
        style_keywords, negative_prompt = compile_style_prompt(
            style_preset,
            modality="image",
            model=self.seedream_model,
        )

        paths = []
        views = ["全身正面", "全身侧面", "全身背面"]

        # 单体限定词，避免生成多个主体
        solo_prefix = "solo, single subject, only one character"

        for i in range(count):
            view = views[i] if i < len(views) else f"全身姿势{i + 1}"
            prompt = (
                f"{solo_prefix}, {style_keywords}, {appearance_prompt}，{view}，角色参考图，"
                "仅锁定角色身份与外貌，不要磨皮，不要CG质感，不要插画质感，"
                "保持真实皮肤纹理和自然五官比例"
            )

            output_path = os.path.join(output_dir, f"reference_{i + 1:02d}.png")

            result = await self.generate(
                prompt=prompt,
                output_path=output_path,
                negative_prompt=negative_prompt,
                style=style,
                project_dir=project_dir,
            )

            if result.success and result.image_path:
                paths.append(result.image_path)

        return paths


class MockImageGenerator:
    """模拟图像生成器（用于测试）。

    不调用真实 API，生成占位图像。
    """

    def __init__(self):
        self.default_width = 1024
        self.default_height = 1024

    async def generate(
        self,
        prompt: str,
        output_path: Optional[str] = None,
        **kwargs,
    ) -> ImageGenResult:
        """生成模拟图像。"""
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return ImageGenResult(
                success=False,
                error="Pillow not installed",
            )

        # 创建占位图像
        width = kwargs.get("width", self.default_width)
        height = kwargs.get("height", self.default_height)

        img = Image.new("RGB", (width, height), color=(50, 50, 80))
        draw = ImageDraw.Draw(img)

        # 添加文字
        text = f"Mock Image\n{prompt[:50]}..."
        try:
            font = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 24)
        except Exception:
            font = ImageFont.load_default()

        draw.multiline_text(
            (width // 4, height // 3),
            text,
            fill=(200, 200, 200),
            font=font,
        )

        # 保存
        if output_path:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            img.save(output_path)

        return ImageGenResult(
            success=True,
            image_path=output_path,
        )

    async def generate_with_request(
        self,
        request: ImageGenerationRequest,
        output_path: str,
    ) -> ImageGenResult:
        """使用新版请求格式生成模拟图像。"""
        return await self.generate(
            prompt=f"[{len(request.reference_images)} refs] {request.prompt}",
            output_path=output_path,
            width=request.width,
            height=request.height,
        )

    async def generate_character_reference(
        self,
        character_name: str,
        appearance_prompt: str,
        output_dir: str,
        count: int = 3,
        style: str = None,
        project_dir: str = "",
    ) -> list[str]:
        """生成角色参考图。"""
        os.makedirs(output_dir, exist_ok=True)
        paths = []

        for i in range(count):
            path = os.path.join(output_dir, f"reference_{i + 1:02d}.png")
            result = await self.generate(
                prompt=f"{character_name} - {appearance_prompt}",
                output_path=path,
            )
            if result.success:
                paths.append(path)

        return paths


def create_image_generator(
    use_mock: bool | str = False,
    *,
    provider: str | None = None,
):
    """创建图像生成器。

    Args:
        use_mock: 是否使用模拟生成器。旧调用也可以传入 ``"volcengine"``，
            该值表示提供商而不是 mock 开关。
        provider: 兼容旧调用的提供商名称，目前支持 Volcengine/Seedream。

    Returns:
        图像生成器实例
    """
    # ``nanobanana_grid`` 的历史调用传入了 ``"volcengine"``。此前这个
    # 非空字符串会被当成 True，静默返回占位图，导致生产渲染看似成功但
    # 实际没有调用模型。把旧参数显式归一化，只有明确的布尔开关才允许 mock。
    if isinstance(use_mock, str):
        provider = provider or use_mock
        use_mock = False

    if use_mock:
        return MockImageGenerator()

    provider_name = str(provider or "volcengine").strip().lower()
    if provider_name not in {"volcengine", "seedream"}:
        raise ValueError(f"Unsupported image generator provider: {provider_name}")

    # 生产路径缺少凭据时必须尽早失败，不能把占位图当成真实产物。
    # 测试/演示需要占位图时调用方必须显式传 ``use_mock=True``。
    return VolcengineImageGenerator()


# 角色资产的两套出图口径。每个角色两张图，各司其职：
# - portrait：完整正面全身照，带面部，作为下游唯一的「脸锚」；
# - four_view：四视图设定表（左侧正面大头照 + 右侧严格无头的正 / 侧 / 背三视图），
#   作为服装、体型与发型锚。整张复合表不作为视频模型的 R2V / 全能参考输入。
CHARACTER_PROMPT_TEMPLATE_PORTRAIT = "portrait"
CHARACTER_PROMPT_TEMPLATE_FOUR_VIEW = "four_view"
_CHARACTER_PROMPT_TEMPLATES = (
    CHARACTER_PROMPT_TEMPLATE_PORTRAIT,
    CHARACTER_PROMPT_TEMPLATE_FOUR_VIEW,
)


def normalize_character_prompt_template(value: str | None) -> str:
    """把未知 / 历史坏值收敛到默认的正面全身照口径。"""
    normalized = str(value or "").strip().lower()
    return (
        normalized
        if normalized in _CHARACTER_PROMPT_TEMPLATES
        else CHARACTER_PROMPT_TEMPLATE_PORTRAIT
    )


def _direct_character_style_prompt(
    *,
    style: str | None,
    project_dir: str,
    image_model: str,
    subject_prompt: str,
    identity: bool = False,
    character_name: str = "",
    ethnicity: str = "Chinese",
    character_tag: str = "",
    has_costume_reference: bool = False,
    prompt_template: str = CHARACTER_PROMPT_TEMPLATE_PORTRAIT,
) -> str:
    """编译直连角色图提示词，保持项目风格和角色约束不丢失。"""
    from novelvideo.styles.project_style import build_project_style_snapshot

    prompt_template = normalize_character_prompt_template(prompt_template)
    snapshot = build_project_style_snapshot(
        style,
        project_dir=project_dir,
        image_model=image_model,
    )
    style_prompt = str(snapshot.get("image_prompt") or "").strip()
    negative_prompt = str(snapshot.get("negative_prompt") or "").strip()
    lines = [
        "村长无限画布角色资产生成任务。",
        f"角色：{character_name}。",
        f"身份标签：{character_tag}。" if character_tag else "",
        f"种族/地域外观：{ethnicity}。",
        f"项目视觉风格：{style_prompt}" if style_prompt else "",
        "同项目角色保持同一视觉世界。",
        "角色 Portrait 只锁定身份、面部和稳定外观；剧本动作、事件和场景状态由分镜与视频阶段处理。",
        "年代、题材、服装和材质只按项目视觉锚点与角色描述判断；不要根据角色名或动作刻板印象自行切换时代。",
        "角色描述里出现修士、宗门、灵根、仙侠、修仙、古装等字样时，穿该时代最普通的布袍与布鞋，禁止牛仔裤、卫衣、T恤、西装、夹克等现代服装；描述明确是现代生活时才穿现代服装。描述没有逐件写服装时按这条判断，不要默认现代装。",
        "这是角色资产图：只生成当前角色一人，使用简洁中性背景，不出现其他人物、倒地角色、尸体、群像或互动对象。",
        f"角色描述：{str(subject_prompt or '').strip()}。",
    ]
    if prompt_template == CHARACTER_PROMPT_TEMPLATE_FOUR_VIEW:
        lines.extend(
            [
                "输出一张角色四视图设定表：四组视图画在同一张图内，不是四张独立图片。",
                "布局：左侧 1/3 为角色正面大头照（头顶到胸口），居中，头部端正、双眼水平、眼线不倾斜，"
                "整个画面里只有这一处出现头脸；右侧 2/3 为严格无头、仅颈部以下的全身三视图，"
                "正面、侧面、背面并排站立，在衣领上缘平齐截断，不出现头部、面部、颈部皮肤和颈部残端。",
                "三个全身角度的比例、服装、配色和配饰完全一致，像同一角色原地转身；"
                "三视图之间保留约 5% 间隔，不要挤在一起。",
                "背景为纯色浅灰 #E8E8E8，无杂物杂色、无道具、无多余人物，干净抠图质感；"
                "画面干净通透、无噪点、无脏污纹理、无摩尔纹。",
            ]
        )
    elif identity:
        lines.extend(
            [
                "这是基于参考图的身份一致性生成：保持参考图中的脸型、五官、发型、年龄和体态，只执行身份描述中的服装与场景变化。",
                "身份描述没有来自剧本的明确换装证据时，完整保留参考图服装，不自行换装。",
                "输出单张 3:4 中性站姿角色身份参考图，完整展示人物，不要拼贴面板、不要改变角色身份。",
                "已有服装参考图时，服装材质、颜色、版型和关键配饰以服装参考图为准。"
                if has_costume_reference
                else "",
            ]
        )
    else:
        lines.extend(
            [
                "输出单张 3:4 正面全身角色基准图：完整展示从头顶到脚底的全身，标准正面站姿，"
                "双臂自然放松，人物完整不裁切，面部清晰可辨、姿态自然。",
                "只生成一个角色，保持可复用的面部身份锚点，不要加入其他人物。",
            ]
        )
    if prompt_template == CHARACTER_PROMPT_TEMPLATE_FOUR_VIEW:
        lines.extend(
            [
                "禁止文字、字幕、标签、水印、Logo、海报排版和多余装饰；"
                "不要省略背面视图，不要把四视图拆成多张独立图片。",
                f"必须避免：{negative_prompt}" if negative_prompt else "",
            ]
        )
    else:
        lines.extend(
            [
                "禁止文字、字幕、标签、水印、Logo、拼贴、分屏、海报排版和多余装饰。",
                f"必须避免：{negative_prompt}" if negative_prompt else "",
            ]
        )
    return "\n".join(line for line in lines if line).strip()


def _write_direct_character_prompt(
    *,
    prompt: str,
    project_dir: str,
    character_name: str,
    identity_name: str = "",
    slot: str = CHARACTER_PROMPT_TEMPLATE_PORTRAIT,
) -> Path | None:
    if not project_dir:
        return None
    prompts_dir = Path(project_dir) / "prompts" / "characters"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_identity_{identity_name}" if identity_name else f"_{slot}"
    prompt_path = prompts_dir / f"{character_name}{suffix}.prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    return prompt_path


def _redact_direct_image_error(error: BaseException, api_key: str) -> str:
    message = str(error).strip() or error.__class__.__name__
    return message.replace(api_key, "[redacted]") if api_key else message


async def _generate_direct_character_reference(
    *,
    direct_model,
    character_name: str,
    appearance_prompt: str,
    output_dir: str,
    character_tag: str,
    count: int,
    style: str | None,
    ethnicity: str,
    prompt_only: bool,
    project_dir: str,
    identity_name: str,
    raise_on_error: bool,
    prompt_template: str = CHARACTER_PROMPT_TEMPLATE_PORTRAIT,
    reference_image_path: str = "",
) -> list[str]:
    from novelvideo.generators.direct_image_models import generate_direct_image

    prompt_template = normalize_character_prompt_template(prompt_template)
    prompt = _direct_character_style_prompt(
        style=style,
        project_dir=project_dir,
        image_model=direct_model.upstream_model,
        subject_prompt=appearance_prompt,
        character_name=character_name,
        ethnicity=ethnicity,
        character_tag=character_tag,
        prompt_template=prompt_template,
    )
    if reference_image_path:
        prompt += (
            "\nUse the supplied full-body front reference as the same character. "
            "Preserve the face, hairstyle, body proportions, clothing and colors "
            "in every view; do not redesign the character."
        )
    _write_direct_character_prompt(
        prompt=prompt,
        project_dir=project_dir,
        character_name=character_name,
        identity_name=identity_name,
        slot=prompt_template,
    )
    if prompt_only:
        return []

    # 四视图设定表是横构图（左 1/3 大头照 + 右 2/3 三视图并排），竖构图会挤掉服装细节。
    aspect_ratio = (
        "4:3" if prompt_template == CHARACTER_PROMPT_TEMPLATE_FOUR_VIEW else "3:4"
    )
    paths: list[str] = []
    generation_count = max(1, int(count or 1))
    try:
        for index in range(generation_count):
            filename = (
                "reference_portrait.png"
                if generation_count == 1
                else f"reference_{index + 1:02d}.png"
            )
            path = Path(output_dir) / filename
            await generate_direct_image(
                model=direct_model,
                prompt=prompt,
                output_path=path,
                aspect_ratio=aspect_ratio,
                image_size="1K",
                quality="medium",
                reference_paths=(reference_image_path,) if reference_image_path else (),
            )
            paths.append(str(path))
        return paths
    except Exception as exc:
        message = _redact_direct_image_error(exc, direct_model.api_key)
        if is_insufficient_credits_error(message=message):
            raise RuntimeError("INSUFFICIENT_CREDITS") from exc
        if raise_on_error:
            raise preserve_provider_diagnostics(
                RuntimeError(message or "直连角色图生成失败"), exc
            ) from exc
        return []


async def _generate_direct_identity_image(
    *,
    direct_model,
    character_name: str,
    identity_prompt: str,
    reference_image_path: str,
    output_path: str,
    character_tag: str,
    ethnicity: str,
    style: str | None,
    dry_run: bool,
    project_dir: str,
    costume_image_path: str,
    identity_name: str,
    raise_on_error: bool,
) -> dict:
    from novelvideo.generators.direct_image_models import generate_direct_image

    prompt = _direct_character_style_prompt(
        style=style,
        project_dir=project_dir,
        image_model=direct_model.upstream_model,
        subject_prompt=identity_prompt,
        identity=True,
        character_name=character_name,
        ethnicity=ethnicity,
        character_tag=character_tag,
        has_costume_reference=bool(costume_image_path),
    )
    prompt_path = _write_direct_character_prompt(
        prompt=prompt,
        project_dir=project_dir,
        character_name=character_name,
        identity_name=identity_name,
    )
    if dry_run:
        return {
            "success": True,
            "prompt": prompt,
            "prompt_file": str(prompt_path) if prompt_path else None,
        }

    references = [reference_image_path]
    if costume_image_path:
        references.append(costume_image_path)
    try:
        await generate_direct_image(
            model=direct_model,
            prompt=prompt,
            output_path=Path(output_path),
            aspect_ratio="3:4",
            image_size="1K",
            quality="medium",
            reference_paths=tuple(references),
        )
        return True
    except Exception as exc:
        message = _redact_direct_image_error(exc, direct_model.api_key)
        if is_insufficient_credits_error(message=message):
            raise RuntimeError("INSUFFICIENT_CREDITS") from exc
        if raise_on_error:
            raise preserve_provider_diagnostics(
                RuntimeError(message or "直连身份图生成失败"), exc
            ) from exc
        return False


async def generate_character_reference_unified(
    character_name: str,
    appearance_prompt: str,
    output_dir: str,
    character_tag: str = "",
    count: int = 3,
    use_mock: bool = False,
    style: str = None,
    ethnicity: str = "Chinese",
    prompt_only: bool = False,  # Dry Run 模式：只生成提示词，不调用 API
    model: str = None,  # 模型选择：nanobanana 或 seedream，默认从配置读取
    project_dir: str = "",  # 项目根目录，用于定位 prompts 目录
    usage_task_type: str = "character_portrait",
    usage_scope: str = "",
    identity_name: str = "",
    raise_on_error: bool = False,
    prompt_template: str = CHARACTER_PROMPT_TEMPLATE_PORTRAIT,
    reference_image_path: str = "",
) -> list[str]:
    """统一的角色参考图生成接口。

    根据 model 参数或 CHARACTER_IMAGE_MODEL 配置选择生成器：
    - "nanobanana": 使用 Nano Banana Pro (Gemini)，与网格生成同一模型
    - "seedream": 使用 Seedream 4.0 (火山引擎)

    Args:
        character_name: 角色名称
        appearance_prompt: 外貌 Prompt
        output_dir: 输出目录
        character_tag: 角色唯一短标签（用于 Nano Banana Pro 的身份锁定）
        count: 生成数量（Seedream 模式使用；NanoBanana portrait 路径兼容保留）
        use_mock: 是否使用模拟生成器
        style: 风格名称（如 "post_apocalyptic"），默认使用全局配置
        ethnicity: 角色种族（默认 "Chinese"），用于确保生成正确的面部特征
        prompt_only: Dry Run 模式，只生成提示词文件，不调用 API
        model: 模型选择，"nanobanana" 或 "seedream"，默认从环境变量 CHARACTER_IMAGE_MODEL 读取
        prompt_template: 出图口径。``portrait``（默认）输出单张正面全身照；
            ``four_view`` 输出四视图设定表，仅直连生图模型支持。

    Returns:
        生成的图片路径列表
    """
    prompt_template = normalize_character_prompt_template(prompt_template)
    if reference_image_path:
        reference = Path(reference_image_path)
        if not reference.is_file() or reference.stat().st_size <= 0:
            raise ValueError("角色正面全身照参考图不存在或为空，无法生成关联资产。")
    if use_mock:
        generator = MockImageGenerator()
        return await generator.generate_character_reference(
            character_name=character_name,
            appearance_prompt=appearance_prompt,
            output_dir=output_dir,
            count=count,
            project_dir=project_dir,
        )

    # 空值先解析模型中心默认项；不要先触发 legacy selection fallback。
    selected_model = str(model or "").strip()
    from novelvideo.generators.direct_image_models import resolve_direct_image_model

    direct_model = resolve_direct_image_model(selected_model or None)
    if direct_model is not None:
        return await _generate_direct_character_reference(
            direct_model=direct_model,
            character_name=character_name,
            appearance_prompt=appearance_prompt,
            output_dir=output_dir,
            character_tag=character_tag,
            count=count,
            style=style,
            ethnicity=ethnicity,
            prompt_only=prompt_only,
            project_dir=project_dir,
            identity_name=identity_name,
            raise_on_error=raise_on_error,
            prompt_template=prompt_template,
            reference_image_path=reference_image_path,
        )

    if not selected_model:
        raise ValueError("角色参考图未配置图片模型；请先在模型中心添加并检测生图模型。")

    if reference_image_path:
        raise RuntimeError("关联角色资产需要支持参考图的直连生图模型。")

    # 四视图是横构图的分屏设定表，legacy 生成器各自在内部拼提示词、也钉死了竖构图，
    # 这里继续往下走只会出一张被误当成四视图的竖图。宁可明确报错，也不要静默出错图。
    if prompt_template == CHARACTER_PROMPT_TEMPLATE_FOUR_VIEW:
        raise RuntimeError(
            "四视图角色资产需要直连生图模型；当前配置的图片模型没有四视图出图口径。"
            "请在模型中心改用直连生图模型后重试。"
        )

    # 显式旧模型路径保持读取兼容。
    model = normalize_explicit_image_generation_selection(selected_model)

    if model == "nanobanana":
        # 使用 Nano Banana Pro (Gemini)
        try:
            from novelvideo.generators.nanobanana_character import NanoBananaCharacterGenerator

            generator = NanoBananaCharacterGenerator()

            result = await generator.generate_character_portrait(
                character_name=character_name,
                character_prompt=appearance_prompt,
                character_tag=character_tag,
                output_dir=output_dir,
                style=style,
                ethnicity=ethnicity,
                prompt_only=prompt_only,
                project_dir=project_dir,
                usage_task_type=usage_task_type,
                usage_scope=usage_scope,
                identity_name=identity_name,
            )

            if result.success:
                return result.reference_paths
            else:
                print(f"[Character] NanoBanana 生成失败: {result.error}")
                if is_insufficient_credits_error(message=result.error or ""):
                    raise RuntimeError("INSUFFICIENT_CREDITS")
                if raise_on_error:
                    raise RuntimeError(result.error or "NanoBanana 生成失败")
                return []  # 失败就是失败，不回退
        except ImportError as e:
            print(f"[Character] 无法导入 NanoBanana 生成器: {e}")
            return []
        except ValueError as e:
            print(f"[Character] NanoBanana 配置错误: {e}")
            return []

    if model in IMAGE_GENERATION_SELECTIONS:
        try:
            from novelvideo.generators.nanobanana_character import NanoBananaCharacterGenerator

            generator = NanoBananaCharacterGenerator(selection=model)

            result = await generator.generate_character_portrait(
                character_name=character_name,
                character_prompt=appearance_prompt,
                character_tag=character_tag,
                output_dir=output_dir,
                style=style,
                ethnicity=ethnicity,
                prompt_only=prompt_only,
                project_dir=project_dir,
                usage_task_type=usage_task_type,
                usage_scope=usage_scope,
                identity_name=identity_name,
            )

            if result.success:
                return result.reference_paths
            print(f"[Character] {model} 生成失败: {result.error}")
            if is_insufficient_credits_error(message=result.error or ""):
                raise RuntimeError("INSUFFICIENT_CREDITS")
            if raise_on_error:
                raise RuntimeError(result.error or f"{model} 生成失败")
            return []
        except ImportError as e:
            print(f"[Character] 无法导入统一角色生成器: {e}")
            return []
        except ValueError as e:
            print(f"[Character] {model} 配置错误: {e}")
            return []

    if model == "seedream":
        # 使用 Seedream 4.0 (火山引擎)
        try:
            generator = VolcengineImageGenerator()
            return await generator.generate_character_reference(
                character_name=character_name,
                appearance_prompt=appearance_prompt,
                output_dir=output_dir,
                count=count,
                style=style,
                project_dir=project_dir,
            )
        except ValueError as e:
            if raise_on_error:
                raise RuntimeError(f"Seedream 配置错误: {e}") from e
            return []

    raise ValueError(f"角色参考图模型不可用：{selected_model}")


async def generate_identity_image_unified(
    character_name: str,
    identity_prompt: str,
    reference_image_path: str,
    output_path: str,
    character_tag: str = "",
    ethnicity: str = "Chinese",
    style: str = None,
    dry_run: bool = False,
    model: str = None,  # 模型选择：nanobanana 或 seedream，默认从配置读取
    project_dir: str = "",  # 项目根目录，用于定位 prompts 目录
    costume_image_path: str = "",  # 服装参考图路径
    usage_task_type: str = "identity_image",
    usage_scope: str = "",
    identity_name: str = "",
    raise_on_error: bool = False,
) -> dict:
    """基于角色基准图生成身份参考图（Identity Locking）。

    使用角色的正面基准图作为身份锚点，保持面部一致性，
    只变换服装、背景等身份特定的外观。

    Args:
        character_name: 角色名称
        identity_prompt: 身份外貌描述（服装、场景等）
        reference_image_path: 角色正面基准图路径
        output_path: 输出图片路径
        character_tag: 角色唯一短标签
        ethnicity: 角色种族（默认 "Chinese"），用于确保生成正确的面部特征
        style: 风格名称（如 "post_apocalyptic"），默认使用全局配置
        dry_run: 是否仅生成 Prompt 不生成图片（默认 False）
        model: 模型选择，"nanobanana" 或 "seedream"，默认从环境变量 CHARACTER_IMAGE_MODEL 读取

    Returns:
        dict: {"success": bool, "prompt": str, "prompt_file": str} (dry_run 模式返回 prompt)
              或 bool (兼容旧代码)
    """
    # 空值先解析模型中心默认项，并保留参考图和服装参考图。
    selected_model = str(model or "").strip()
    from novelvideo.generators.direct_image_models import resolve_direct_image_model

    direct_model = resolve_direct_image_model(selected_model or None)
    if direct_model is not None:
        return await _generate_direct_identity_image(
            direct_model=direct_model,
            character_name=character_name,
            identity_prompt=identity_prompt,
            reference_image_path=reference_image_path,
            output_path=output_path,
            character_tag=character_tag,
            ethnicity=ethnicity,
            style=style,
            dry_run=dry_run,
            project_dir=project_dir,
            costume_image_path=costume_image_path,
            identity_name=identity_name,
            raise_on_error=raise_on_error,
        )

    if not selected_model:
        raise ValueError("身份图未配置图片模型；请先在模型中心添加并检测生图模型。")

    # 显式旧模型路径保持读取兼容。
    model = normalize_explicit_image_generation_selection(selected_model)

    if model == "nanobanana":
        try:
            from novelvideo.generators.nanobanana_character import NanoBananaCharacterGenerator

            generator = NanoBananaCharacterGenerator()
            result = await generator.generate_identity_with_reference(
                character_name=character_name,
                identity_prompt=identity_prompt,
                reference_image_path=reference_image_path,
                output_path=output_path,
                character_tag=character_tag,
                ethnicity=ethnicity,
                style=style,
                dry_run=dry_run,
                project_dir=project_dir,
                costume_image_path=costume_image_path,
                usage_task_type=usage_task_type,
                usage_scope=usage_scope,
                identity_name=identity_name,
            )
            if dry_run:
                return {
                    "success": result.success,
                    "prompt": result.prompt,
                    "prompt_file": result.prompt_file,
                }
            if not result.success:
                if is_insufficient_credits_error(message=result.error or ""):
                    raise RuntimeError("INSUFFICIENT_CREDITS")
                if raise_on_error:
                    raise RuntimeError(result.error or "身份图生成失败")
            return result.success

        except ImportError as e:
            print(f"[Identity] 无法导入 NanoBanana 生成器: {e}")
            return False
        except ValueError as e:
            print(f"[Identity] NanoBanana 配置错误: {e}")
            return False

    if model in IMAGE_GENERATION_SELECTIONS:
        try:
            from novelvideo.generators.nanobanana_character import NanoBananaCharacterGenerator

            generator = NanoBananaCharacterGenerator(selection=model)
            result = await generator.generate_identity_with_reference(
                character_name=character_name,
                identity_prompt=identity_prompt,
                reference_image_path=reference_image_path,
                output_path=output_path,
                character_tag=character_tag,
                ethnicity=ethnicity,
                style=style,
                dry_run=dry_run,
                project_dir=project_dir,
                costume_image_path=costume_image_path,
                usage_task_type=usage_task_type,
                usage_scope=usage_scope,
                identity_name=identity_name,
            )
            if dry_run:
                return {
                    "success": result.success,
                    "prompt": result.prompt,
                    "prompt_file": result.prompt_file,
                }
            if not result.success:
                if is_insufficient_credits_error(message=result.error or ""):
                    raise RuntimeError("INSUFFICIENT_CREDITS")
                if raise_on_error:
                    raise RuntimeError(result.error or f"{model} 身份图生成失败")
            return result.success
        except ImportError as e:
            print(f"[Identity] 无法导入统一角色生成器: {e}")
            return False
        except ValueError as e:
            print(f"[Identity] {model} 配置错误: {e}")
            return False

    # Seedream 或其他模型：回退到原来的逻辑（不支持 Identity Locking）
    print(f"[Identity] {model} 不支持 Identity Locking，使用独立生成")
    import os
    output_dir = os.path.dirname(output_path)
    paths = await generate_character_reference_unified(
        character_name=character_name,
        appearance_prompt=identity_prompt,
        output_dir=output_dir,
        character_tag=character_tag,
        count=1,
        style=style,
        model=model,
        project_dir=project_dir,
        usage_task_type=usage_task_type,
        usage_scope=usage_scope,
        identity_name=identity_name,
    )
    if paths:
        import shutil
        from pathlib import Path
        first = Path(paths[0])
        if first.exists() and str(first) != output_path:
            shutil.copy(first, output_path)
        return True
    if raise_on_error:
        raise RuntimeError("身份图生成失败")
    return False
