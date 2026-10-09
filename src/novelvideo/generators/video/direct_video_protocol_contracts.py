"""Transport contracts for operator-configured direct video APIs.

The model catalog describes *what* a model can do.  A transport contract
describes *how* a task is submitted, polled, and recovered.  Keeping these
concerns separate prevents a model discovered through an OpenAI-compatible
``/models`` directory from being incorrectly submitted to ``/videos``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import SplitResult, urlsplit, urlunsplit

from novelvideo.utils.error_redaction import redact_secrets


DIRECT_VIDEO_PROTOCOL_AUTO = "auto"
DIRECT_VIDEO_PROTOCOL_OPENAI = "openai-video"
DIRECT_VIDEO_PROTOCOL_MINIMAX_V2 = "minimax-video-v2"
DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI = "autodl-comfyui"
DIRECT_VIDEO_PROTOCOL_UNRESOLVED = "unresolved"

_COMMON_VIDEO_ERROR_PATHS = (
    "error.message",
    "error.detail",
    "error",
    "error_message",
    "errorMessage",
    "fail_reason",
    "failReason",
    "failure_reason",
    "failureReason",
    "failure_message",
    "failureMessage",
    "reason",
    "detail",
    "message",
    "data.error.message",
    "data.error.detail",
    "data.error",
    "data.error_message",
    "data.errorMessage",
    "data.fail_reason",
    "data.failReason",
    "data.failure_reason",
    "data.failureReason",
    "data.message",
    "result.error.message",
    "result.error",
    "result.error_message",
    "result.failureReason",
    "result.message",
    "task.error.message",
    "task.error",
    "task.error_message",
    "task.failureReason",
    "task.message",
)

_ERROR_KEY_NAMES = frozenset(
    {
        "error",
        "errors",
        "errormessage",
        "errorcode",
        "failreason",
        "failurereason",
        "failuremessage",
        "failurecode",
        "reason",
        "detail",
        "code",
        "message",
    }
)


@dataclass(frozen=True, slots=True)
class DirectVideoProtocolContract:
    protocol_id: str
    submit_path: str
    query_path_template: str
    task_id_paths: tuple[str, ...]
    status_paths: tuple[str, ...]
    result_url_paths: tuple[str, ...]
    error_paths: tuple[str, ...]
    completed_statuses: frozenset[str]
    failed_statuses: frozenset[str]
    payload_family: str
    base_strategy: str = "configured"
    #: Create routes this protocol may legitimately be served from, in
    #: preference order.  Discovery only reads ``GET /models``, so a relay's
    #: registered create route is invisible until a submission proves it: some
    #: NewAPI stations register video creation at ``/v1/video/generations``
    #: while the OpenAI-compatible default is ``/v1/videos``.  ``submit_path``
    #: remains the primary, operator-visible route; this list is what the
    #: submit loop may walk when the primary route answers with a redirect or
    #: 404/405 — states in which no task can have been created yet.
    submit_routes: tuple[str, ...] = ()

    @property
    def ordered_submit_routes(self) -> tuple[str, ...]:
        return self.submit_routes or (self.submit_path,)


OPENAI_VIDEO_CONTRACT = DirectVideoProtocolContract(
    protocol_id=DIRECT_VIDEO_PROTOCOL_OPENAI,
    submit_path="/videos",
    submit_routes=("/videos", "/video/generations"),
    query_path_template="/videos/{task_id}",
    task_id_paths=("id", "task_id", "data.id", "data.task_id"),
    status_paths=("status", "data.status", "result.status"),
    result_url_paths=(
        "url",
        "video_url",
        "result_url",
        "download_url",
        "data.url",
        "data.video_url",
        "result.url",
        "result.video_url",
        "result.videos[0].url",
        "outputs[0].url",
        "outputs[0]",
    ),
    error_paths=_COMMON_VIDEO_ERROR_PATHS,
    completed_statuses=frozenset({"completed", "succeeded", "success", "done"}),
    failed_statuses=frozenset(
        {"failure", "failed", "error", "canceled", "cancelled", "expired"}
    ),
    payload_family="openai-video",
)


MINIMAX_VIDEO_V2_CONTRACT = DirectVideoProtocolContract(
    protocol_id=DIRECT_VIDEO_PROTOCOL_MINIMAX_V2,
    submit_path="/video_generation",
    query_path_template="/query/video_generation/{task_id}",
    task_id_paths=("task_id", "data.task_id", "id"),
    status_paths=("task.status", "data.task.status", "status"),
    result_url_paths=(
        "task.content.url",
        "data.task.content.url",
        "task.url",
        "data.video_url",
    ),
    error_paths=("data.task.error.message", "data.task.error", *_COMMON_VIDEO_ERROR_PATHS),
    completed_statuses=frozenset({"succeeded"}),
    failed_statuses=frozenset({"failed", "cancelled", "expired"}),
    payload_family="minimax-video-v2",
    base_strategy="minimax-gateway-v2",
)


AUTODL_COMFYUI_CONTRACT = DirectVideoProtocolContract(
    protocol_id=DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI,
    submit_path="/api/v1/comfyui/comfyui_workflow/{model}",
    query_path_template="/api/v1/comfyui/comfyui_workflow/result/{task_id}",
    task_id_paths=("data.task_id", "task_id"),
    status_paths=("data.status", "status"),
    result_url_paths=("data.results[0].url", "results[0].url"),
    error_paths=("data.message", "data.error", *_COMMON_VIDEO_ERROR_PATHS),
    completed_statuses=frozenset({"success", "completed", "succeeded"}),
    failed_statuses=frozenset({"failed", "failure", "error", "cancelled", "canceled"}),
    payload_family="autodl-comfyui",
)


_CONTRACTS = {
    OPENAI_VIDEO_CONTRACT.protocol_id: OPENAI_VIDEO_CONTRACT,
    MINIMAX_VIDEO_V2_CONTRACT.protocol_id: MINIMAX_VIDEO_V2_CONTRACT,
    AUTODL_COMFYUI_CONTRACT.protocol_id: AUTODL_COMFYUI_CONTRACT,
}

_UPSTREAM_PROTOCOL_ALIASES = {
    "openai": DIRECT_VIDEO_PROTOCOL_OPENAI,
    "openai-video": DIRECT_VIDEO_PROTOCOL_OPENAI,
    "openai:video": DIRECT_VIDEO_PROTOCOL_OPENAI,
    "openai:video_generation": DIRECT_VIDEO_PROTOCOL_OPENAI,
    "minimax-video-v2": DIRECT_VIDEO_PROTOCOL_MINIMAX_V2,
    "minimax:video_generation_v2": DIRECT_VIDEO_PROTOCOL_MINIMAX_V2,
    "autodl": DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI,
    "autodl-comfyui": DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI,
    "autodl-comfy-ui": DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI,
    "comfyui-autodl": DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI,
}


def normalize_direct_video_protocol(value: object) -> str:
    normalized = str(value or DIRECT_VIDEO_PROTOCOL_AUTO).strip().lower()
    aliases = {
        "": DIRECT_VIDEO_PROTOCOL_AUTO,
        "openai_compatible": DIRECT_VIDEO_PROTOCOL_OPENAI,
        "openai-compatible": DIRECT_VIDEO_PROTOCOL_OPENAI,
        "openai_video": DIRECT_VIDEO_PROTOCOL_OPENAI,
        **_UPSTREAM_PROTOCOL_ALIASES,
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {
        DIRECT_VIDEO_PROTOCOL_AUTO,
        DIRECT_VIDEO_PROTOCOL_UNRESOLVED,
        *_CONTRACTS,
    }:
        raise ValueError(f"unsupported direct video protocol: {value}")
    return normalized


def resolve_protocol_from_metadata(
    supported_protocols: Iterable[object],
    *,
    fallback: str = DIRECT_VIDEO_PROTOCOL_OPENAI,
) -> str:
    for item in supported_protocols:
        resolved = _UPSTREAM_PROTOCOL_ALIASES.get(str(item or "").strip().lower())
        if resolved:
            return resolved
    if any(str(item or "").strip() for item in supported_protocols):
        return DIRECT_VIDEO_PROTOCOL_UNRESOLVED
    return normalize_direct_video_protocol(fallback)


def get_direct_video_protocol_contract(
    protocol: object,
) -> DirectVideoProtocolContract:
    normalized = normalize_direct_video_protocol(protocol)
    if normalized == DIRECT_VIDEO_PROTOCOL_AUTO:
        normalized = DIRECT_VIDEO_PROTOCOL_OPENAI
    if normalized == DIRECT_VIDEO_PROTOCOL_UNRESOLVED:
        raise ValueError("direct video protocol has no executable contract")
    return _CONTRACTS[normalized]


def protocol_base_url(base_url: str, contract: DirectVideoProtocolContract) -> str:
    configured = str(base_url or "").strip().rstrip("/")
    if contract.base_strategy == "configured":
        return configured
    parsed = urlsplit(configured)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if segments and segments[-1].lower() == "v2" and contract.base_strategy != "minimax-gateway-v2":
        return configured
    if contract.base_strategy == "minimax-gateway-v2":
        return _minimax_v2_base(parsed, segments)
    path = "/" + "/".join(segments)
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def _minimax_v2_base(parsed: SplitResult, segments: list[str]) -> str:
    """Locate the MiniMax video-v2 root for the two shapes seen in the field.

    A gateway that nests the model directory under ``/gateway/v1``
    (``tokendance.space``) publishes MiniMax v2 as a sibling at
    ``/gateway/minimax/v2``.  A station whose own product *is* the H3 service
    (``dmc.cc``) serves the same contract at the origin root as ``/v2``: its
    ``/v1`` family is the OpenAI-compatible relay whose create route routes by
    channel group, so a credential without a channel there answers 503 even
    though the native ``/v2`` endpoints accept it.  Both are separated by the
    ``gateway`` segment, so the configured directory path decides the shape.
    """

    if segments and segments[-1].lower() == "v2":
        path = "/" + "/".join(segments)
    elif "gateway" in segments:
        gateway_index = segments.index("gateway")
        path = "/" + "/".join([*segments[: gateway_index + 1], "minimax", "v2"])
    else:
        path = "/v2"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def submit_url(
    base_url: str,
    contract: DirectVideoProtocolContract,
    *,
    create_path: str | None = None,
) -> str:
    path = str(create_path or contract.submit_path).strip()
    return f"{protocol_base_url(base_url, contract)}{'/' + path.strip('/')}"


def query_url(
    base_url: str,
    contract: DirectVideoProtocolContract,
    task_id: str,
) -> str:
    path = contract.query_path_template.format(task_id=str(task_id).strip())
    return f"{protocol_base_url(base_url, contract)}{'/' + path.strip('/')}"


def extract_json_path(payload: object, path: str) -> Any:
    current = payload
    for segment in str(path or "").split("."):
        name, index = _split_path_segment(segment)
        if name:
            if not isinstance(current, Mapping) or name not in current:
                return None
            current = current[name]
        if index is not None:
            if not isinstance(current, Sequence) or isinstance(current, (str, bytes)):
                return None
            if index >= len(current):
                return None
            current = current[index]
    return current


def extract_first(payload: object, paths: Iterable[str]) -> Any:
    for path in paths:
        value = extract_json_path(payload, path)
        if value is not None and value != "":
            return value
    return None


def extract_task_id(
    payload: object, contract: DirectVideoProtocolContract
) -> str:
    return str(extract_first(payload, contract.task_id_paths) or "").strip()


def extract_task_status(
    payload: object, contract: DirectVideoProtocolContract
) -> str:
    return str(extract_first(payload, contract.status_paths) or "").strip().lower()


def extract_result_url(
    payload: object, contract: DirectVideoProtocolContract
) -> str:
    return str(extract_first(payload, contract.result_url_paths) or "").strip()


def extract_task_error(
    payload: object, contract: DirectVideoProtocolContract
) -> str:
    value = extract_first(payload, contract.error_paths)
    message = _extract_error_text(value)
    if not message:
        message = _find_nested_error(payload)
    return redact_secrets(message).strip()[:1200]


def _extract_error_text(value: object, *, depth: int = 0) -> str:
    if value is None or depth > 5:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, Mapping):
        normalized = {
            str(key).replace("_", "").replace("-", "").lower(): nested
            for key, nested in value.items()
        }
        for key in (
            "message",
            "detail",
            "reason",
            "errormessage",
            "failreason",
            "failurereason",
            "failuremessage",
            "errorcode",
            "failurecode",
            "code",
            "error",
            "errors",
        ):
            if key not in normalized:
                continue
            message = _extract_error_text(normalized[key], depth=depth + 1)
            if message:
                return message
        return ""
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value[:8]:
            message = _extract_error_text(item, depth=depth + 1)
            if message:
                return message
    return ""


def _find_nested_error(payload: object) -> str:
    queue: list[tuple[object, int]] = [(payload, 0)]
    visited = 0
    while queue and visited < 128:
        current, depth = queue.pop(0)
        visited += 1
        if depth > 5:
            continue
        if isinstance(current, Mapping):
            for key, value in current.items():
                normalized_key = (
                    str(key).replace("_", "").replace("-", "").lower()
                )
                if normalized_key in _ERROR_KEY_NAMES:
                    message = _extract_error_text(value, depth=depth + 1)
                    if message:
                        return message
                if isinstance(value, (Mapping, list, tuple)):
                    queue.append((value, depth + 1))
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            queue.extend((item, depth + 1) for item in current[:16])
    return ""


def protocol_capability(protocol: str) -> dict[str, Any]:
    normalized = normalize_direct_video_protocol(protocol)
    if normalized == DIRECT_VIDEO_PROTOCOL_UNRESOLVED:
        return {
            "source": "models-protocol-contract",
            "verificationStatus": "degraded",
            "detectedProtocol": DIRECT_VIDEO_PROTOCOL_UNRESOLVED,
        }
    if normalized != DIRECT_VIDEO_PROTOCOL_MINIMAX_V2:
        if normalized != DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI:
            return {}
        return {
            "source": "models-protocol-contract",
            "verificationStatus": "contract-resolved",
            "detectedProtocol": DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI,
            "transportContract": {
                "submit": AUTODL_COMFYUI_CONTRACT.submit_path,
                "query": AUTODL_COMFYUI_CONTRACT.query_path_template,
                "payloadFamily": AUTODL_COMFYUI_CONTRACT.payload_family,
                "authorization": "raw-token",
            },
        }
    return {
        "source": "models-protocol-contract",
        "verificationStatus": "contract-resolved",
        "detectedProtocol": DIRECT_VIDEO_PROTOCOL_MINIMAX_V2,
        "modes": [
            "textToVideo",
            "imageToVideo",
            "firstLastFrame",
            "imageReference",
            "allReference",
            "videoEdit",
        ],
        "resolutionOptions": ["768p", "2k"],
        "aspectRatios": ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
        "durationRange": [4, 15],
        "nativeAudio": "required",
        "referenceLimits": {
            "inputImages": 2,
            "referenceImages": 9,
            "referenceVideos": 3,
            "referenceAudios": 3,
        },
        "transportContract": {
            "submit": MINIMAX_VIDEO_V2_CONTRACT.submit_path,
            "query": MINIMAX_VIDEO_V2_CONTRACT.query_path_template,
            "payloadFamily": MINIMAX_VIDEO_V2_CONTRACT.payload_family,
        },
    }


def build_minimax_video_v2_payload(
    *,
    model: str,
    prompt: str,
    resolution: str,
    duration: int,
    ratio: str,
    first_frame_url: str = "",
    last_frame_url: str = "",
    reference_images: Iterable[str] = (),
    reference_videos: Iterable[str] = (),
    reference_audios: Iterable[str] = (),
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    clean_prompt = str(prompt or "").strip()
    if clean_prompt:
        content.append({"type": "text", "text": clean_prompt})

    def add_media(kind: str, url: object, role: str) -> None:
        clean_url = str(url or "").strip()
        if clean_url:
            content.append(
                {
                    "type": f"{kind}_url",
                    f"{kind}_url": {"url": clean_url},
                    "role": role,
                }
            )

    add_media("image", first_frame_url, "first_frame")
    add_media("image", last_frame_url, "last_frame")
    for url in reference_images:
        add_media("image", url, "reference_image")
    for url in reference_videos:
        add_media("video", url, "reference_video")
    for url in reference_audios:
        add_media("audio", url, "reference_audio")

    requested_resolution = str(resolution or "").strip().lower()
    payload: dict[str, Any] = {
        "model": str(model or "").strip(),
        "resolution": "2K" if requested_resolution == "2k" else "768P",
        "duration": max(4, min(15, int(duration))),
        "content": content,
    }
    # ``ratio`` is required for every request, but its accepted values differ by
    # modality: a text-only request needs a concrete canvas (``16:9``...), while
    # a request carrying frames or references is validated against the
    # ``adaptive`` family and is rejected as e.g. ``unsupported FL2VA ratio``
    # when a fixed ratio is sent or the field is missing.
    has_media = any(item.get("type") != "text" for item in content)
    if has_media:
        payload["ratio"] = "adaptive"
    else:
        payload["ratio"] = str(ratio or "16:9").strip()
    return payload


def canvas_comes_from_the_media(protocol_id: object) -> bool:
    """该协议带素材时，成片画幅由**素材自己**决定，写的比例不生效。

    MiniMax 原生 v2 就是如此：带图片/视频/音频时必须发 `ratio=adaptive`，写死比例会被
    上游以 `unsupported FL2VA ratio` 拒掉。所以"请求 9:16 + 参考图是方的"出来的就是
    **方片**，节点上那个 9:16 只是显示值——实测踩过：三镜齐出 768×768，画幅不符却找不出原因。

    正确做法不是改参数，而是**把参考图本身做成目标画幅**（裁切或补边到 9:16）。
    """

    return str(protocol_id or "").strip() == DIRECT_VIDEO_PROTOCOL_MINIMAX_V2


def endpoint_fallback_allowed(http_status: int | None) -> bool:
    return http_status in {404, 405}


def _split_path_segment(segment: str) -> tuple[str, int | None]:
    text = str(segment or "").strip()
    if not text.endswith("]") or "[" not in text:
        return text, None
    name, _, raw_index = text[:-1].partition("[")
    try:
        return name, int(raw_index)
    except ValueError:
        return text, None
