"""SparkCine（hi.hi.run）原生视频协议适配层。

站点除了 OpenAI 兼容的 /api/openai/v1 之外，还在 /api/open/v1 提供原生协议：参考
素材必须先经过 POST /uploads 换成 asset_id，再以 inputs=[{asset_id, role}] 提交；
模型参数放在嵌套的 parameters 里，尺寸用 resolution + aspect_ratio 两个字段表达，
而不是 OpenAI 兼容层的 size。

本模块只做纯数据转换（目录解析、参数归一、约束校验、请求体构造）与素材上传，真正
的提交/轮询仍由 newapi_generate / newapi 的通用流程完成。保持纯函数是为了能离线单
测——这些站点的错误报文只有实际提交才拿得到。
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from novelvideo.shared.safe_media_url import (
    MediaDownloadError,
    download_public_media,
)

PROTOCOL_ID = "sparkcine-native"
NATIVE_PATH_PREFIX = "/api/open/v1"

# 站点 GET /api/open/v1/models 在 2026-10-11 公布的 h3-video 能力（原生目录）。
NATIVE_RESOLUTIONS = ("480p", "720p", "768p", "960p", "1080p")
NATIVE_ASPECT_RATIOS = ("16:9", "9:16", "21:9", "4:3", "1:1", "3:4")
NATIVE_DURATION_RANGE = (4, 30)

# 画布模式 → 站点原生 mode。画布上的「全参考」在站点是一个 mode（多模态参考）。
CANVAS_MODE_TO_NATIVE_MODE = {
    "textToVideo": "text_to_video",
    "imageToVideo": "image_to_video",
    "imageReference": "multimodal_reference",
    "allReference": "multimodal_reference",
}
NATIVE_MODE_TO_CANVAS_MODE = {
    "text_to_video": "textToVideo",
    "image_to_video": "imageToVideo",
    "multimodal_reference": "allReference",
}

REFERENCE_ROLE_BY_KIND = {
    "image": "reference_image",
    "audio": "reference_audio",
    "video": "reference_video",
}
REFERENCE_LIMITS = {
    "inputImages": 0,
    "referenceImages": 9,
    "referenceVideos": 1,
    "referenceAudios": 3,
}

# 分辨率别名：画布上出现的 2k/4k 之类说法落到站点真实档位。
_RESOLUTION_ALIASES = {
    "2k": "1080p",
    "4k": "1080p",
    "1080": "1080p",
    "960": "960p",
    "768": "768p",
    "720": "720p",
    "480": "480p",
}

# 原生请求体 parameters 里允许透传的键（h3-video 全部视频参数）。
NATIVE_PARAMETER_KEYS = (
    "duration",
    "resolution",
    "aspect_ratio",
    "generate_audio",
    "seed",
    "mute_reference_video",
    "output_count",
)
NATIVE_TOP_LEVEL_KEYS = ("optimize_prompt", "route_goal", "note", "metadata")

DECLARED_PARAMETERS: dict[str, dict[str, Any]] = {
    "duration": {
        "default": 6,
        "minimum": 4,
        "maximum": 30,
        "valueType": "integer",
        "billingRelevant": True,
    },
    "resolution": {
        "default": "768p",
        "valueType": "string",
        "allowedValues": list(NATIVE_RESOLUTIONS),
        "billingRelevant": True,
    },
    "aspect_ratio": {
        "default": "16:9",
        "valueType": "string",
        "allowedValues": list(NATIVE_ASPECT_RATIOS),
    },
    "generate_audio": {"default": True, "valueType": "boolean"},
    "seed": {"minimum": 0, "maximum": 9007199254740991, "valueType": "integer"},
    "mute_reference_video": {"default": False, "valueType": "boolean"},
    "output_count": {"default": 1, "minimum": 1, "maximum": 1, "valueType": "integer"},
    "optimize_prompt": {"default": False, "valueType": "boolean"},
    "route_goal": {"valueType": "string", "allowedValues": ["fast", "balanced", "best"]},
}

DECLARED_CONSTRAINTS: list[dict[str, Any]] = [
    {
        "when": {"resolution": "720p"},
        "parameters": {"aspect_ratio": {"allowedValues": ["16:9", "9:16"]}},
    },
    {
        "when": {"resolution": "768p", "aspect_ratio": "21:9"},
        "parameters": {"duration": {"maximum": 20}},
    },
    {"when": {"resolution": "960p"}, "parameters": {"duration": {"maximum": 15}}},
    {"when": {"resolution": "1080p"}, "parameters": {"duration": {"maximum": 15}}},
]

DECLARED_PRICING: dict[str, Any] = {
    "minimumCredits": 6,
    "promptOptimizationCredits": 5,
    "perSecond": {"480p": 1.5, "720p": 1.5, "768p": 1.5, "960p": 12, "1080p": 16},
}

_IMAGE_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}
_AUDIO_TYPES = {
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
}
_VIDEO_TYPES = {
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mov": "video/quicktime",
}


class SparkcineNativeError(RuntimeError):
    """原生协议的上传/提交失败，带足够的上报字段。"""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        error_code: str = "SPARKCINE_NATIVE_REQUEST_FAILED",
        http_status: int | None = None,
        response_text: str = "",
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.error_code = error_code
        self.http_status = http_status
        self.response_text = response_text


def native_base_url(configured_base_url: str) -> str:
    """把渠道里保存的 base_url 解析到站点原生 API 根。

    渠道保存的是可发现的 https://hi.hi.run/api/openai/v1；原生协议在同一个站点根
    下的 /api/open/v1。按 origin 重写而不是要求用户改配置，是为了不打断已保存的
    密钥绑定与渠道记录。
    """

    text = str(configured_base_url or "").strip()
    parsed = urlparse(text)
    if not parsed.scheme or not parsed.netloc:
        return text.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{NATIVE_PATH_PREFIX}"


def declared_capability() -> dict[str, Any]:
    """站点原生目录声明的 h3-video 视频能力（未联网时的合同）。"""

    return {
        "source": "sparkcine-native-contract",
        "verificationStatus": "contract-resolved",
        "modes": ["textToVideo", "imageToVideo", "allReference"],
        "resolutionOptions": list(NATIVE_RESOLUTIONS),
        "aspectRatios": list(NATIVE_ASPECT_RATIOS),
        "durationRange": list(NATIVE_DURATION_RANGE),
        "nativeAudio": "optional",
        "referenceLimits": dict(REFERENCE_LIMITS),
        "parameterDefinitions": {
            key: dict(value) for key, value in DECLARED_PARAMETERS.items()
        },
        "parameterConstraints": [dict(item) for item in DECLARED_CONSTRAINTS],
        "pricing": json.loads(json.dumps(DECLARED_PRICING)),
        "transportContract": {
            "submit": "/generations",
            "query": "/generations/{task_id}",
            "payloadFamily": PROTOCOL_ID,
        },
    }


def parse_catalog_capability(catalog: object, model_id: str) -> dict[str, Any]:
    """从原生目录 JSON 里读出目标模型的能力。

    上游目录是权威：站点改了档位或约束，这里跟着变，而不是继续用写死的常量。
    """

    wanted = str(model_id or "").strip().lower()
    if not wanted:
        return {}
    entries = catalog.get("data") if isinstance(catalog, Mapping) else None
    if not isinstance(entries, list):
        entries = []
    entry: Mapping[str, Any] | None = None
    for item in entries:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("id") or "").strip().lower() == wanted:
            entry = item
            break
    if entry is None:
        return {}

    modes: list[str] = []
    raw_modes = entry.get("modes")
    if isinstance(raw_modes, Mapping):
        for key in raw_modes:
            canvas_mode = NATIVE_MODE_TO_CANVAS_MODE.get(str(key).strip().lower())
            if canvas_mode and canvas_mode not in modes:
                modes.append(canvas_mode)

    parameters = (
        entry.get("parameters") if isinstance(entry.get("parameters"), Mapping) else {}
    )
    capability: dict[str, Any] = {}
    if modes:
        capability["modes"] = modes
    allowed_resolutions = _parameter_allowed_values(parameters, "resolution")
    if allowed_resolutions:
        capability["resolutionOptions"] = allowed_resolutions
    allowed_aspects = _parameter_allowed_values(parameters, "aspect_ratio")
    if allowed_aspects:
        capability["aspectRatios"] = allowed_aspects
    duration = parameters.get("duration") if isinstance(parameters, Mapping) else None
    if isinstance(duration, Mapping):
        minimum = duration.get("minimum")
        maximum = duration.get("maximum")
        if isinstance(minimum, int) and isinstance(maximum, int):
            capability["durationRange"] = [minimum, maximum]
    if isinstance(parameters, Mapping) and "generate_audio" in parameters:
        # 站点自己会出声；画布可以用 generate_audio 关掉，所以是 optional。
        capability["nativeAudio"] = "optional"
    if isinstance(parameters, Mapping) and parameters:
        capability["parameterDefinitions"] = {
            str(key): dict(value)
            for key, value in parameters.items()
            if isinstance(value, Mapping)
        }
    constraints = entry.get("parameter_constraints")
    if isinstance(constraints, list):
        capability["parameterConstraints"] = [
            dict(item) for item in constraints if isinstance(item, Mapping)
        ]
    pricing = entry.get("pricing")
    if isinstance(pricing, Mapping):
        capability["pricing"] = dict(pricing)
    raw_inputs = _mode_inputs(entry)
    if raw_inputs:
        capability["mediaInputs"] = raw_inputs
    if capability:
        capability["source"] = "sparkcine-native-catalog"
        capability["verificationStatus"] = "contract-resolved"
    return capability


def _parameter_allowed_values(parameters: object, key: str) -> list[str]:
    if not isinstance(parameters, Mapping):
        return []
    entry = parameters.get(key)
    if not isinstance(entry, Mapping):
        return []
    values = entry.get("allowedValues")
    if not isinstance(values, list):
        return []
    return [str(item) for item in values]


def _mode_inputs(entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    """把目录里的 mode→inputs 声明整理成一张参考素材上限表。"""

    slots: dict[tuple[str, str], dict[str, Any]] = {}
    raw_modes = entry.get("modes")
    if not isinstance(raw_modes, Mapping):
        return []
    for mode_key, mode in raw_modes.items():
        inputs = mode.get("inputs") if isinstance(mode, Mapping) else None
        if not isinstance(inputs, list):
            continue
        for slot in inputs:
            if not isinstance(slot, Mapping):
                continue
            role = str(slot.get("role") or "").strip()
            media_type = str(slot.get("media_type") or "").strip()
            if not role or not media_type:
                continue
            key = (role, media_type)
            current = slots.get(key)
            candidate = {
                "role": role,
                "mediaType": media_type,
                "min": int(slot.get("min") or 0),
                "max": int(slot.get("max") or 0),
                "modes": [str(mode_key)],
                "label": (
                    str(mode.get("display_name") or "").strip()
                    if isinstance(mode, Mapping)
                    else ""
                ),
            }
            if current is None:
                slots[key] = candidate
            else:
                current["min"] = min(current["min"], candidate["min"])
                current["max"] = max(current["max"], candidate["max"])
                for mode_name in candidate["modes"]:
                    if mode_name not in current["modes"]:
                        current["modes"].append(mode_name)
    return list(slots.values())


def normalize_resolution(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in NATIVE_RESOLUTIONS:
        return text
    if text in _RESOLUTION_ALIASES:
        return _RESOLUTION_ALIASES[text]
    return ""


def normalize_aspect_ratio(value: object) -> str:
    text = str(value or "").strip()
    return text if text in NATIVE_ASPECT_RATIOS else ""


def native_mode_for(
    *,
    canvas_mode: str,
    image_count: int,
    audio_count: int,
    video_count: int,
) -> str:
    """显式模式优先；没有显式模式时按实际素材推断。"""

    explicit = CANVAS_MODE_TO_NATIVE_MODE.get(str(canvas_mode or "").strip())
    if explicit:
        return explicit
    if audio_count or video_count:
        return "multimodal_reference"
    if image_count:
        return "image_to_video"
    return "text_to_video"


def reference_limit_error(
    *, image_count: int, audio_count: int, video_count: int
) -> str | None:
    if image_count > REFERENCE_LIMITS["referenceImages"]:
        return (
            f"参考图 {image_count} 张超过站点上限 "
            f"{REFERENCE_LIMITS['referenceImages']} 张；请删减后重试。"
        )
    if audio_count > REFERENCE_LIMITS["referenceAudios"]:
        return (
            f"参考音频 {audio_count} 段超过站点上限 "
            f"{REFERENCE_LIMITS['referenceAudios']} 段；请删减后重试。"
        )
    if video_count > REFERENCE_LIMITS["referenceVideos"]:
        return (
            f"参考视频 {video_count} 段超过站点上限 "
            f"{REFERENCE_LIMITS['referenceVideos']} 段；请删减后重试。"
        )
    return None


def mode_input_error(
    native_mode: str, *, image_count: int, audio_count: int, video_count: int
) -> str | None:
    total = image_count + audio_count + video_count
    if native_mode == "image_to_video" and image_count < 1:
        return "图生视频模式需要至少 1 张参考图；请补一张图或改用文生视频。"
    if native_mode == "multimodal_reference" and total < 1:
        return "多模态参考模式需要至少 1 个参考素材（图/音频/视频）。"
    return None


def constraint_error(parameters: Mapping[str, Any]) -> str | None:
    """按站点公布的 parameter_constraints 校验组合。

    站点对这些组合不是改参数，而是整包拒绝；本地先说清楚，避免把一次必然失败的
    请求发出去。
    """

    resolution = str(parameters.get("resolution") or "")
    aspect_ratio = str(parameters.get("aspect_ratio") or "")
    duration = parameters.get("duration")
    duration_value = int(duration) if isinstance(duration, (int, float)) else None
    for constraint in DECLARED_CONSTRAINTS:
        when = constraint.get("when") or {}
        if any(
            str(parameters.get(key) or "") != str(value) for key, value in when.items()
        ):
            continue
        rules = constraint.get("parameters") or {}
        for key, rule in rules.items():
            if not isinstance(rule, Mapping):
                continue
            allowed = rule.get("allowedValues")
            if isinstance(allowed, list) and str(parameters.get(key) or "") not in {
                str(item) for item in allowed
            }:
                return (
                    f"{resolution} 只支持 {', '.join(str(item) for item in allowed)} 比例"
                    f"（当前 {aspect_ratio}）；请换比例或换分辨率。"
                )
            maximum = rule.get("maximum")
            if (
                isinstance(maximum, int)
                and duration_value is not None
                and key == "duration"
                and duration_value > maximum
            ):
                limit_label = f"{resolution}"
                if when.get("aspect_ratio"):
                    limit_label += f" + {when['aspect_ratio']}"
                return (
                    f"{limit_label} 最长 {maximum} 秒（当前 {duration_value} 秒）；"
                    "请缩短时长或换分辨率。"
                )
            minimum = rule.get("minimum")
            if (
                isinstance(minimum, int)
                and duration_value is not None
                and key == "duration"
                and duration_value < minimum
            ):
                return f"站点最短 {minimum} 秒（当前 {duration_value} 秒）。"
    return None


def resolve_parameters(
    *,
    resolution: object,
    aspect_ratio: object,
    duration: float,
    generate_audio: bool,
    extra: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], str | None]:
    """把画布选择编译成站点 parameters；返回 (参数, 错误)。"""

    resolved_resolution = normalize_resolution(resolution) or "768p"
    resolved_aspect = normalize_aspect_ratio(aspect_ratio) or "16:9"
    try:
        resolved_duration = int(round(float(duration)))
    except (TypeError, ValueError):
        return {}, f"时长 {duration!r} 不是有效秒数。"
    if (
        resolved_duration < NATIVE_DURATION_RANGE[0]
        or resolved_duration > NATIVE_DURATION_RANGE[1]
    ):
        return {}, (
            f"站点时长范围 {NATIVE_DURATION_RANGE[0]}-{NATIVE_DURATION_RANGE[1]} 秒"
            f"（当前 {resolved_duration} 秒）。"
        )

    parameters: dict[str, Any] = {
        "duration": resolved_duration,
        "resolution": resolved_resolution,
        "aspect_ratio": resolved_aspect,
        "generate_audio": bool(generate_audio),
    }
    if isinstance(extra, Mapping):
        for key in NATIVE_PARAMETER_KEYS:
            if key in parameters or key not in extra:
                continue
            value = extra[key]
            if value is None or value == "":
                continue
            if key == "seed":
                try:
                    parameters["seed"] = int(value)
                except (TypeError, ValueError):
                    return {}, f"seed {value!r} 不是整数。"
            elif key == "mute_reference_video":
                parameters[key] = bool(value)
            elif key == "output_count":
                count = int(value)
                if count != 1:
                    return {}, "站点每单只产出一条视频（output_count 固定为 1）。"
                parameters["output_count"] = 1
            else:
                parameters[key] = value

    error = constraint_error(parameters)
    if error:
        return {}, error
    return parameters, None


def build_generation_payload(
    *,
    model: str,
    prompt: str,
    inputs: Sequence[Mapping[str, str]],
    mode: str,
    parameters: Mapping[str, Any],
    top_level: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 POST /api/open/v1/generations 的请求体。"""

    payload: dict[str, Any] = {
        "model": str(model or "").strip(),
        "prompt": str(prompt or ""),
    }
    if inputs:
        payload["inputs"] = [
            {
                "asset_id": str(item.get("asset_id") or ""),
                "role": str(item.get("role") or ""),
            }
            for item in inputs
        ]
    if mode:
        payload["mode"] = mode
    if parameters:
        payload["parameters"] = dict(parameters)
    if isinstance(top_level, Mapping):
        for key in NATIVE_TOP_LEVEL_KEYS:
            if key not in top_level:
                continue
            value = top_level[key]
            if value in (None, "", False):
                continue
            if key == "note":
                payload["note"] = str(value)[:80]
            elif key == "metadata" and isinstance(value, Mapping):
                payload["metadata"] = {
                    str(k)[:64]: str(v)[:500]
                    for k, v in value.items()
                    if str(k).strip()
                }
            else:
                payload[key] = value
    return payload


def content_type_for(filename: str, *, kind: str) -> str:
    ext = Path(str(filename or "")).suffix.lstrip(".").lower()
    table = {
        "image": _IMAGE_TYPES,
        "audio": _AUDIO_TYPES,
        "video": _VIDEO_TYPES,
    }.get(str(kind or "").strip().lower())
    if table is None:
        table = {**_IMAGE_TYPES, **_AUDIO_TYPES, **_VIDEO_TYPES}
    return table.get(ext, "application/octet-stream")


def decode_data_url(media_value: str, *, kind: str) -> tuple[bytes, str, str]:
    header, _, encoded = str(media_value or "").partition(",")
    if ";base64" not in header or not encoded:
        raise SparkcineNativeError(
            "参考素材的 data URL 不是 base64 编码，无法上传到站点。",
            stage="upload",
            error_code="VIDEO_REFERENCE_UPLOAD_FAILED",
        )
    declared = header[len("data:") :].split(";", 1)[0].strip()
    suffix = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "audio/mpeg": "mp3",
        "audio/mp4": "m4a",
        "audio/ogg": "ogg",
        "audio/wav": "wav",
        "video/mp4": "mp4",
        "video/webm": "webm",
        "video/quicktime": "mov",
    }.get(declared, "bin")
    return (
        base64.b64decode(encoded),
        f"reference.{suffix}",
        declared or content_type_for(f"reference.{suffix}", kind=kind),
    )


async def read_media(
    session: Any,
    media_value: str,
    *,
    kind: str,
    timeout: Any = None,
) -> tuple[bytes, str, str]:
    """读出参考素材字节：本地文件、远程 URL、data URL 三种来源。"""

    text = str(media_value or "").strip()
    if not text:
        raise SparkcineNativeError(
            "参考素材路径为空，无法上传到站点。",
            stage="upload",
            error_code="VIDEO_REFERENCE_UPLOAD_FAILED",
        )
    if text.startswith("data:"):
        return decode_data_url(text, kind=kind)
    if text.startswith(("http://", "https://")):
        try:
            data, filename, header_type = await download_public_media(
                session, text, timeout=timeout
            )
        except (MediaDownloadError, ValueError) as exc:
            body = str(getattr(exc, "body", "") or "")
            status = getattr(exc, "status", None)
            message = (
                f"下载参考素材失败：HTTP {status} - {body[:200]}"
                if status is not None
                else f"下载参考素材失败：{exc}"
            )
            raise SparkcineNativeError(
                message,
                stage="upload",
                error_code="VIDEO_REFERENCE_UPLOAD_FAILED",
                http_status=int(status) if status is not None else None,
                response_text=body[:2000],
            ) from exc
        return data, filename, header_type or content_type_for(filename, kind=kind)
    path = Path(text)
    if not path.is_file():
        raise SparkcineNativeError(
            f"参考素材文件不存在：{path.name}",
            stage="upload",
            error_code="VIDEO_REFERENCE_UPLOAD_FAILED",
        )
    return path.read_bytes(), path.name, content_type_for(path.name, kind=kind)


async def upload_media_asset(
    session: Any,
    *,
    base_url: str,
    authorization: str,
    media_value: str,
    kind: str,
    idempotency_key: str = "",
    timeout: Any = None,
) -> str:
    """上传参考素材并返回 asset_id（POST /uploads → PUT → /complete）。"""

    data, filename, content_type = await read_media(
        session, media_value, kind=kind, timeout=timeout
    )
    root = str(base_url or "").rstrip("/")
    key = str(idempotency_key or "").strip() or uuid.uuid4().hex
    body = {
        "filename": filename[:255],
        "content_type": content_type,
        "byte_length": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "reuse_existing": True,
    }
    headers = {
        "Authorization": authorization,
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "Idempotency-Key": key,
    }
    async with session.post(
        f"{root}/uploads", data=json.dumps(body).encode("utf-8"), headers=headers
    ) as resp:
        text = await resp.text()
        if resp.status < 200 or resp.status >= 300:
            raise SparkcineNativeError(
                f"站点素材上传登记失败：HTTP {resp.status} - {text[:300]}",
                stage="upload",
                error_code="VIDEO_REFERENCE_UPLOAD_FAILED",
                http_status=resp.status,
                response_text=text[:2000],
            )
        created = _decode_json(text, stage="upload")
    asset_id = str(created.get("asset_id") or "").strip()
    if not asset_id:
        raise SparkcineNativeError(
            "站点素材上传登记没有返回 asset_id。",
            stage="upload",
            error_code="VIDEO_REFERENCE_UPLOAD_FAILED",
        )
    upload = created.get("upload")
    if isinstance(upload, Mapping):
        put_url = str(upload.get("url") or "").strip()
        put_headers = {
            str(name): str(value)
            for name, value in (upload.get("headers") or {}).items()
        }
        if not put_url:
            raise SparkcineNativeError(
                "站点素材上传登记没有返回签名 PUT 地址。",
                stage="upload",
                error_code="VIDEO_REFERENCE_UPLOAD_FAILED",
            )
        async with session.put(put_url, data=data, headers=put_headers) as resp:
            if resp.status < 200 or resp.status >= 300:
                text = await resp.text()
                raise SparkcineNativeError(
                    f"素材上传到站点对象存储失败：HTTP {resp.status} - {text[:200]}",
                    stage="upload",
                    error_code="VIDEO_REFERENCE_UPLOAD_FAILED",
                    http_status=resp.status,
                    response_text=text[:2000],
                )
        async with session.post(
            f"{root}/uploads/{asset_id}/complete",
            headers={
                "Authorization": authorization,
                "Accept": "application/json",
                "Idempotency-Key": uuid.uuid4().hex,
            },
        ) as resp:
            text = await resp.text()
            if resp.status < 200 or resp.status >= 300:
                raise SparkcineNativeError(
                    f"站点素材上传确认失败：HTTP {resp.status} - {text[:300]}",
                    stage="upload",
                    error_code="VIDEO_REFERENCE_UPLOAD_FAILED",
                    http_status=resp.status,
                    response_text=text[:2000],
                )
    return asset_id


def _decode_json(text: str, *, stage: str) -> dict[str, Any]:
    try:
        payload = json.loads(text or "{}")
    except ValueError as exc:
        raise SparkcineNativeError(
            f"站点返回的不是有效 JSON（{exc}）。",
            stage=stage,
            error_code="VIDEO_REFERENCE_UPLOAD_FAILED",
        ) from exc
    if not isinstance(payload, Mapping):
        raise SparkcineNativeError(
            "站点返回的 JSON 不是对象。",
            stage=stage,
            error_code="VIDEO_REFERENCE_UPLOAD_FAILED",
        )
    return dict(payload)
