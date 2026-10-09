"""Provider-neutral image request policy.

This module owns request-size normalization, OpenAI-compatible dimension
resolution, capability probing, and provider payload helpers.  It intentionally
contains no canvas, grid, storage, or prompt-building concerns so every image
entry point follows the same upstream contract.
"""

from __future__ import annotations

import hashlib
import io
import logging
import math
import os
import re

import httpx
import numpy as np

from novelvideo.product_identity import attribution_headers

logger = logging.getLogger(__name__)

_VALID_IMAGE_SIZES = {"512", "1K", "2K", "4K"}
_IMAGE_ASPECT_RATIO_RE = re.compile(
    r"^(?:\d{1,6}(?:\.\d{1,4})?):(?:\d{1,6}(?:\.\d{1,4})?)$"
)
_IMAGE_DIMENSION_RE = re.compile(r"^(\d{2,5})\s*[xX×]\s*(\d{2,5})$")
_IMAGE_K_SIZE_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,3})?)\s*[kK]$")
_OPENROUTER_IMAGE_CAPABILITY_CACHE: dict[str, tuple[bool, str]] = {}
_OPENAI_VALID_QUALITIES = {"low", "medium", "high", "auto"}
_OPENAI_MIN_PIXELS = 655_360
_OPENAI_MAX_PIXELS = 8_294_400
_OPENAI_MAX_EDGE = 3840
_OPENAI_MAX_RATIO = 3.0


def is_valid_image_aspect_ratio(value: str | None, *, allow_auto: bool = False) -> bool:
    """Return whether a canvas ratio is syntactically safe to forward.

    The model contract decides whether a ratio is supported. This helper only
    rejects malformed input so a valid upstream-specific ratio is not lost to
    a local preset allowlist.
    """
    normalized = str(value or "").strip().replace("：", ":")
    if allow_auto and normalized.casefold() == "auto":
        return True
    match = _IMAGE_ASPECT_RATIO_RE.fullmatch(normalized)
    if not match:
        return False
    width, height = (float(part) for part in normalized.split(":", 1))
    return math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0


def is_valid_image_size(value: str | None) -> bool:
    """Return whether a canvas image size is a tier or explicit WxH value."""
    normalized = str(value or "").strip()
    if normalized in {"512", "0.5K", "1K", "2K", "3K", "4K"}:
        return True
    dimensions = _IMAGE_DIMENSION_RE.fullmatch(normalized)
    if dimensions:
        width, height = (int(part) for part in dimensions.groups())
        return width > 0 and height > 0
    k_size = _IMAGE_K_SIZE_RE.fullmatch(normalized)
    if not k_size:
        return False
    return float(k_size.group(1)) > 0


def normalize_image_size(size: str, provider: str = "google") -> str:
    """Normalize internal image-size labels for the selected provider."""
    # Keep one transport spelling for explicit dimensions. The multiplication
    # sign is common in UI input, but OpenAI-compatible routes expect ``x``.
    size = re.sub(r"\s*[xX×]\s*", "x", str(size or "").strip())
    if provider in {"huimeng", "newapi"} and size == "0.5K":
        return "1K"
    if size == "0.5K":
        return "1K" if provider == "openrouter" else "512"
    return size


def newapi_resolution_from_image_size(image_size: str | None) -> str:
    """Return the resolution extension accepted by the NewAPI image route."""
    normalized = normalize_image_size(str(image_size or "").strip(), provider="newapi")
    lower = normalized.lower()
    return lower if lower in {"1k", "2k", "3k", "4k"} else ""


def newapi_image_model_supports_quality(model: str | None) -> bool:
    """Whether the NewAPI logical model accepts the quality payload field."""
    model_name = str(model or "").strip().lower()
    return model_name in {
        "lingshan-g2",
        "village-canvas-image",
        "village-canvas-image-reference",
        "gpt-image-2",
        "image-2",
        "image-2-official",
    } or "gpt-image" in model_name


def image_credit_billing_params(
    *,
    image_size: str | None = None,
    quality: str | None = None,
) -> dict[str, str]:
    """Build normalized, auditable billing dimensions for image usage events."""
    params: dict[str, str] = {}
    clean_size = str(image_size or "").strip().lower()
    if clean_size:
        params["size"] = clean_size
    clean_quality = str(quality or "").strip().lower()
    if clean_quality:
        params["quality"] = clean_quality
    return params


def huimeng_image_resolution_for_model(model: str, image_size: str | None) -> str:
    """Map a local size label to the HuiMeng resolution parameter, when supported."""
    model_name = (model or "").strip()
    image2_family = model_name in {"image-2", "image-2-official"}
    if not (
        model_name.startswith(("nb-", "seedream-")) or image2_family or "gpt-image" in model_name
    ):
        return ""

    normalized = normalize_image_size(str(image_size or "").strip(), provider="huimeng")
    if image2_family:
        lower = normalized.lower()
        return lower if lower in {"1k", "2k", "4k"} else ""
    return normalized if normalized in {"1K", "2K", "3K", "4K"} else ""


def returned_aspect_mismatch(image_bytes: bytes, requested_aspect: str) -> str:
    """回图宽高比与请求差太多时返回错误说明，否则返回空串。

    容差 10%：正常取整和压缩不会触发，4:3 回成 2:3（差 50% 以上）必触发。
    读不了的字节不在这里判，留给落盘环节报自己的错。
    """
    text = str(requested_aspect or "").replace("-", ":")
    try:
        raw_w, raw_h = (float(part) for part in text.split(":", 1))
    except (TypeError, ValueError):
        return ""
    if raw_w <= 0 or raw_h <= 0:
        return ""
    try:
        from PIL import Image as _Image

        with _Image.open(io.BytesIO(image_bytes)) as img:
            width, height = img.size
    except Exception:
        return ""
    if width <= 0 or height <= 0:
        return ""
    requested = raw_w / raw_h
    actual = width / height
    if max(requested, actual) / min(requested, actual) <= 1.10:
        return ""
    return (
        f"回图比例与请求不符：请求 {text}，实际 {width}x{height}。"
        "已拒绝落盘，避免按格子切割成窄条废图，请重试。"
    )


def returned_single_cell_split(image_bytes: bytes, rows: int, cols: int) -> str:
    """单格图被画成上下两格时返回错误说明，否则返回空串。

    判两种：上下两半几乎一样（同一画面被复制），或中部一条横线明显深于两侧
    （被一条分格线切开）。正常单画面里的横线不够深，不会被误伤。
    """
    if int(rows or 1) != 1 or int(cols or 1) != 1:
        return ""
    try:
        from PIL import Image as _Image

        with _Image.open(io.BytesIO(image_bytes)) as img:
            gray = img.convert("L")
    except Exception:
        return ""
    width, height = gray.size
    if height < 200 or width < 100:
        return ""
    pixels = np.asarray(gray)
    half = height // 2
    top = (pixels[:half] < 140).mean(axis=1).astype(float)
    bottom = (pixels[half : half * 2] < 140).mean(axis=1).astype(float)
    if top.std() > 1e-6 and bottom.std() > 1e-6:
        if float(np.corrcoef(top, bottom)[0, 1]) >= 0.9:
            return _split_error()
    # 第二种：中部一条横线明显深于它两侧的画面，把图切成上下两格。
    # 门槛取实测值：真分格线比两侧深 48 以上，正常画面里最深的横线不超过 28。
    seam = float(pixels[half - 1 : half + 2].mean())
    flank = float(pixels[half - 45 : half - 8].mean() + pixels[half + 8 : half + 45].mean()) / 2
    if flank - seam > 40:
        return _split_error()
    return ""


def _split_error() -> str:
    return (
        "单格回图被画成了上下两格，与「一个镜头一个画面」不符。已拒绝落盘，请重试。"
    )


_TRANSIENT_MARKS = (
    "HTTP 502",
    "HTTP 503",
    "HTTP 504",
    "HTTP 429",
    "超时",
    "暂时不可用",
    "timed out",
    "timeout",
    "econnreset",
    "connection error",
    # 中转站把它自己那一跳的失败包装成 400 的说法（2026-10-03 分镜草图现场：
    # ``400 {"message":"由于我这边发生了错误，我未能生成图片。"}``）。这类不是
    # 请求内容有问题，重发即可命中。
    "由于我这边发生了错误",
    "我未能生成图片",
    "an error occurred on our side",
)

#: 本地「等图」的读取上限。同步出图中转会把提交连接一直开着，本地先掐断只会
#: 把已经接单、上游仍在渲染的图丢掉（2026-09-21 真机 12 镜批次在 120s 读超时下
#: 丢了 2 张，且这类错误被刻意排除在可重放集合之外）。所以默认不设读取上限；
#: 连接、写入、连接池仍保留短超时，真正不可达的地址不会无限挂住。
IMAGE_SUBMIT_CONNECT_TIMEOUT_SECONDS = 30.0
IMAGE_SUBMIT_WRITE_TIMEOUT_SECONDS = 120.0
IMAGE_SUBMIT_POOL_TIMEOUT_SECONDS = 30.0
#: 需要恢复旧行为（有界读取）时设这个环境变量，单位秒；0 或非法值按不设上限处理。
IMAGE_SUBMIT_READ_TIMEOUT_ENV = "VILLAGE_CANVAS_IMAGE_READ_TIMEOUT_SECONDS"


def image_submit_read_timeout() -> float | None:
    """返回本机等待出图结果的读取上限；``None`` 表示不设上限。"""

    raw = str(os.environ.get(IMAGE_SUBMIT_READ_TIMEOUT_ENV) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def image_submit_timeout(read: float | None = None) -> httpx.Timeout:
    """出图提交用的 httpx 超时：只约束握手/发送，不约束上游渲染时间。

    ``read`` 显式传入时按传入值生效（历史调用点用它保留自己的有界读取）。
    """

    return httpx.Timeout(
        connect=IMAGE_SUBMIT_CONNECT_TIMEOUT_SECONDS,
        read=image_submit_read_timeout() if read is None else read,
        write=IMAGE_SUBMIT_WRITE_TIMEOUT_SECONDS,
        pool=IMAGE_SUBMIT_POOL_TIMEOUT_SECONDS,
    )


def is_transient_upstream_error(error_text: str) -> bool:
    """上游图像服务临时故障（超时/限流/5xx/断连）时为真，值得原样重试。

    内容被安全拒绝、提示词超长这类确定性失败不在内，重试也是白花钱。
    """
    text = str(error_text or "").lower()
    return any(mark in text for mark in _TRANSIENT_MARKS)


def _round_openai_edge(value: float) -> int:
    return max(16, int(math.ceil(value / 16.0)) * 16)


def resolve_openai_image_size(
    aspect_ratio: str = "1:1",
    image_size: str = "1K",
    model: str | None = None,
    *,
    allow_dynamic_resolution: bool = False,
    min_pixels: int | None = None,
) -> str:
    """Map local aspect/size labels to a valid GPT Image 2 size string."""
    _ = model

    ratio_text = str(aspect_ratio or "1:1").replace("-", ":")
    try:
        raw_w, raw_h = [float(part) for part in ratio_text.split(":", 1)]
        if raw_w <= 0 or raw_h <= 0:
            raise ValueError
    except Exception:
        raw_w, raw_h = 1.0, 1.0

    ratio = raw_w / raw_h
    if ratio > _OPENAI_MAX_RATIO:
        ratio = _OPENAI_MAX_RATIO
    elif ratio < 1.0 / _OPENAI_MAX_RATIO:
        ratio = 1.0 / _OPENAI_MAX_RATIO

    normalized_size = normalize_image_size(str(image_size or "1K"), provider="openai")
    normalized_size_key = normalized_size.upper()
    long_edge = {
        "512": 1024,
        "0.5K": 1024,
        "1K": 1024,
        "2K": 2048,
        "3K": 3072,
        "4K": 3840,
    }.get(normalized_size_key)

    explicit_dimensions: tuple[int, int] | None = None
    dynamic_max_edge = _OPENAI_MAX_EDGE
    dynamic_max_pixels = _OPENAI_MAX_PIXELS
    if long_edge is None and allow_dynamic_resolution:
        explicit_size = re.fullmatch(r"(\d+)\s*[xX×]\s*(\d+)", normalized_size)
        if explicit_size:
            width_i, height_i = (int(value) for value in explicit_size.groups())
            if width_i <= 0 or height_i <= 0:
                raise ValueError(f"invalid image resolution: {image_size}")
            explicit_dimensions = (width_i, height_i)
        else:
            k_size = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[kK]", normalized_size)
            if k_size:
                long_edge = max(16, _round_openai_edge(float(k_size.group(1)) * 1024))
                dynamic_max_edge = min(_OPENAI_MAX_EDGE, long_edge)
                dynamic_max_pixels = min(_OPENAI_MAX_PIXELS, long_edge * long_edge)

    if long_edge is None:
        long_edge = 1024

    configured_min_pixels = (
        min_pixels
        if type(min_pixels) is int and _OPENAI_MIN_PIXELS <= min_pixels <= _OPENAI_MAX_PIXELS
        else None
    )
    effective_min_pixels = configured_min_pixels or _OPENAI_MIN_PIXELS

    if explicit_dimensions is not None:
        width, height = (float(value) for value in explicit_dimensions)
    elif ratio >= 1:
        width = float(long_edge)
        height = width / ratio
    else:
        height = float(long_edge)
        width = height * ratio

    pixel_count = width * height
    if pixel_count < effective_min_pixels:
        scale = math.sqrt(effective_min_pixels / pixel_count)
        width *= scale
        height *= scale
    elif pixel_count > dynamic_max_pixels:
        scale = math.sqrt(dynamic_max_pixels / pixel_count)
        width *= scale
        height *= scale

    width_i = min(dynamic_max_edge, _round_openai_edge(width))
    height_i = min(dynamic_max_edge, _round_openai_edge(height))

    if width_i * height_i < effective_min_pixels:
        scale = math.sqrt(effective_min_pixels / max(1, width_i * height_i))
        width_i = min(dynamic_max_edge, _round_openai_edge(width_i * scale))
        height_i = min(dynamic_max_edge, _round_openai_edge(height_i * scale))

    while width_i * height_i > dynamic_max_pixels:
        if width_i >= height_i:
            width_i = max(16, width_i - 16)
        else:
            height_i = max(16, height_i - 16)

    return f"{width_i}x{height_i}"


def normalize_openai_quality(value: str | None, default: str = "medium") -> str:
    """Normalize the quality field accepted by OpenAI-compatible routes."""
    quality = str(value or default or "medium").strip().lower()
    return quality if quality in _OPENAI_VALID_QUALITIES else default


def extract_openai_unknown_parameter(error_detail: str) -> str:
    """Extract a rejected optional parameter from an upstream error payload."""
    for pattern in (
        r"Unknown parameter:\s*'([^']+)'",
        r'Unknown parameter:\s*"([^"]+)"',
        r"Unsupported parameter:\s*'([^']+)'",
        r'Unsupported parameter:\s*"([^"]+)"',
        r"'param':\s*'([^']+)'",
        r'"param":\s*"([^"]+)"',
    ):
        match = re.search(pattern, error_detail or "")
        if match:
            return match.group(1)
    for parameter in ("output_format", "quality", "input_fidelity"):
        if parameter in (error_detail or ""):
            return parameter
    return ""


def truncate_openrouter_debug(value: object, limit: int = 240) -> str:
    """Limit debug fragments so provider responses cannot flood local logs."""
    text = str(value or "")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


async def check_openrouter_image_capability(api_key: str, model: str) -> tuple[bool, str]:
    """Read and cache OpenRouter output modality capability without blocking on errors."""
    import httpx

    cache_key = f"{model}:{hashlib.sha1((api_key or '').encode('utf-8')).hexdigest()[:8]}"
    cached = _OPENROUTER_IMAGE_CAPABILITY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    base_url = "https://openrouter.ai/api/v1"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        **attribution_headers(),
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{base_url}/models", headers=headers)
            response.raise_for_status()
            result = response.json()

        models = result.get("data", [])
        model_info = next((item for item in models if item.get("id") == model), None)
        if not model_info:
            detail = f"模型 {model} 不在 OpenRouter /models 列表中，跳过 image capability 预检"
            # 不再静默放行：未知模型（常见于拼错）要留下可检索的告警（T-217）。
            logger.warning(
                "openrouter image capability precheck skipped: model=%s not in /models", model
            )
            print(f"[OpenRouter] {detail}")
            outcome = (True, detail)
            _OPENROUTER_IMAGE_CAPABILITY_CACHE[cache_key] = outcome
            return outcome

        output_modalities = (model_info.get("architecture") or {}).get("output_modalities") or []
        supports_image = "image" in output_modalities
        detail = (
            f"model={model}, output_modalities={output_modalities}"
            if output_modalities
            else f"model={model}, output_modalities=[]"
        )
        outcome = (supports_image, detail)
        _OPENROUTER_IMAGE_CAPABILITY_CACHE[cache_key] = outcome
        return outcome
    except Exception as exc:
        detail = "image capability 预检失败，跳过阻断: " f"{type(exc).__name__}: {exc!r}"
        logger.warning(
            "openrouter image capability precheck error, not blocking: %s", exc, exc_info=True
        )
        print(f"[OpenRouter] {detail}")
        outcome = (True, detail)
        _OPENROUTER_IMAGE_CAPABILITY_CACHE[cache_key] = outcome
        return outcome


def clamp_image_size(size: str) -> str:
    """Clamp image_size to the values accepted by Gemini image APIs."""
    normalized = normalize_image_size(size, provider="google")
    return normalized if normalized in _VALID_IMAGE_SIZES else "1K"
