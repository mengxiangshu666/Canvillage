"""Protocol-neutral video provider adapters.

Video providers do not share one transport contract.  Some expose an
OpenAI-like ``/videos`` resource, while others use prediction objects, queue
requests, Google long-running operations, or workflow graphs.  This module
keeps those lifecycle contracts declarative so discovery and the canvas can
reason about them without adding model-name branches to the generator.

The adapters here are intentionally pure metadata objects.  Runtime I/O stays
in the existing generator until a contract has reached ``contract-resolved``
or ``runtime-verified``; discovery never creates a paid media task.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from .direct_video_protocol_contracts import extract_first, extract_json_path


class VideoProtocolFamily(str, Enum):
    OPENAI_VIDEO = "openai-video"
    PREDICTION = "prediction"
    QUEUE = "queue"
    LONG_RUNNING_OPERATION = "long-running-operation"
    TASK_QUERY = "task-query"
    WORKFLOW = "workflow"
    AUTODL_COMFYUI = "autodl-comfyui"
    MULTIPART_TASK = "multipart-task"
    CUSTOM_OPENAPI = "custom-openapi"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class VideoEndpointContract:
    """External lifecycle contract for one video provider family."""

    family: VideoProtocolFamily
    submit_path: str
    query_path_template: str = ""
    result_path_template: str = ""
    task_id_paths: tuple[str, ...] = ("id", "task_id", "data.id", "data.task_id")
    status_paths: tuple[str, ...] = ("status", "state", "data.status")
    result_paths: tuple[str, ...] = (
        "url",
        "video_url",
        "output",
        "output.url",
        "data.url",
        "data.video_url",
    )
    error_paths: tuple[str, ...] = (
        "error.message",
        "error",
        "message",
        "detail",
        "data.error",
    )
    completed_statuses: frozenset[str] = frozenset(
        {"completed", "succeeded", "success", "done"}
    )
    failed_statuses: frozenset[str] = frozenset(
        {"failed", "failure", "error", "cancelled", "canceled", "expired"}
    )
    polling_style: str = "task-query"
    evidence: tuple[str, ...] = ()

    def submit_url(self, base_url: str, *, model: str = "") -> str:
        return _join_path(base_url, self.submit_path.format(model=model))

    def query_url(self, base_url: str, task_id: str, *, model: str = "") -> str:
        return _join_path(
            base_url,
            self.query_path_template.format(task_id=task_id, model=model),
        )

    def extract_task_id(self, payload: object) -> str:
        value = extract_first(payload, self.task_id_paths)
        return str(value or "").strip()

    def extract_status(self, payload: object) -> str:
        # Google operations use ``done`` instead of a status string.
        if self.family is VideoProtocolFamily.LONG_RUNNING_OPERATION:
            if extract_json_path(payload, "done") is True:
                if extract_json_path(payload, "error"):
                    return "failed"
                return "completed"
            return "running"
        value = extract_first(payload, self.status_paths)
        return str(value or "").strip().lower()

    def extract_result(self, payload: object) -> object:
        return extract_first(payload, self.result_paths)

    def extract_error(self, payload: object) -> str:
        value = extract_first(payload, self.error_paths)
        if isinstance(value, Mapping):
            return str(
                value.get("message")
                or value.get("detail")
                or value.get("reason")
                or value.get("code")
                or ""
            ).strip()
        return str(value or "").strip()


@dataclass(frozen=True, slots=True)
class VideoAdapterEvidence:
    """Evidence used to select a provider family without invoking video."""

    family: VideoProtocolFamily
    confidence: float
    sources: tuple[str, ...]
    matched_paths: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.family is not VideoProtocolFamily.UNKNOWN and self.confidence >= 0.7

    def to_dict(self) -> dict[str, object]:
        return {
            "family": self.family.value,
            "confidence": self.confidence,
            "sources": list(self.sources),
            "matchedPaths": list(self.matched_paths),
            "notes": list(self.notes),
            "resolved": self.resolved,
        }


_CONTRACTS: dict[VideoProtocolFamily, VideoEndpointContract] = {
    VideoProtocolFamily.OPENAI_VIDEO: VideoEndpointContract(
        family=VideoProtocolFamily.OPENAI_VIDEO,
        submit_path="/videos",
        query_path_template="/videos/{task_id}",
        evidence=("protocol:openai-video",),
    ),
    VideoProtocolFamily.TASK_QUERY: VideoEndpointContract(
        family=VideoProtocolFamily.TASK_QUERY,
        submit_path="/video_generation",
        query_path_template="/query/video_generation/{task_id}",
        task_id_paths=("task_id", "data.task_id", "id"),
        status_paths=("task.status", "data.task.status", "status"),
        result_paths=(
            "task.content.url",
            "data.task.content.url",
            "task.url",
            "data.video_url",
        ),
        completed_statuses=frozenset({"succeeded", "completed", "success"}),
        evidence=("protocol:minimax-video-v2",),
    ),
    VideoProtocolFamily.PREDICTION: VideoEndpointContract(
        family=VideoProtocolFamily.PREDICTION,
        submit_path="/v1/predictions",
        query_path_template="/v1/predictions/{task_id}",
        task_id_paths=("id", "prediction_id"),
        status_paths=("status",),
        result_paths=("output", "output.url", "urls.output", "data.output"),
        completed_statuses=frozenset({"succeeded"}),
        failed_statuses=frozenset({"failed", "canceled"}),
        polling_style="prediction",
        evidence=("path:/predictions",),
    ),
    VideoProtocolFamily.QUEUE: VideoEndpointContract(
        family=VideoProtocolFamily.QUEUE,
        submit_path="/queue/{model}/requests",
        query_path_template="/queue/{model}/requests/{task_id}/status",
        result_path_template="/queue/{model}/requests/{task_id}",
        task_id_paths=("request_id", "requestId", "id"),
        status_paths=("status", "state"),
        result_paths=("video.url", "output", "data"),
        polling_style="queue",
        evidence=("path:/queue/",),
    ),
    VideoProtocolFamily.LONG_RUNNING_OPERATION: VideoEndpointContract(
        family=VideoProtocolFamily.LONG_RUNNING_OPERATION,
        submit_path="/v1/{model}:predictLongRunning",
        query_path_template="/v1/{task_id}",
        task_id_paths=("name", "operation.name"),
        status_paths=("done",),
        result_paths=(
            "response.generatedVideos[0].video.uri",
            "response.videos[0].uri",
            "response.output",
        ),
        error_paths=("error.message", "error.details", "error"),
        polling_style="long-running-operation",
        evidence=("path:predictLongRunning",),
    ),
    VideoProtocolFamily.WORKFLOW: VideoEndpointContract(
        family=VideoProtocolFamily.WORKFLOW,
        submit_path="/prompt",
        query_path_template="/history/{task_id}",
        task_id_paths=("prompt_id", "promptId", "id"),
        status_paths=("status.status_str", "status", "state"),
        result_paths=("outputs", "output", "data"),
        polling_style="websocket-or-history",
        evidence=("path:/prompt", "path:/history"),
    ),
    VideoProtocolFamily.AUTODL_COMFYUI: VideoEndpointContract(
        family=VideoProtocolFamily.AUTODL_COMFYUI,
        submit_path="/api/v1/comfyui/comfyui_workflow/{model}",
        query_path_template="/api/v1/comfyui/comfyui_workflow/result/{task_id}",
        task_id_paths=("data.task_id", "task_id"),
        status_paths=("data.status", "status"),
        result_paths=("data.results[0].url", "results[0].url"),
        error_paths=("data.message", "data.error", "message", "error"),
        completed_statuses=frozenset({"success", "completed", "succeeded"}),
        failed_statuses=frozenset({"failed", "failure", "error", "cancelled", "canceled"}),
        polling_style="task-query",
        evidence=("provider:autodl", "path:/api/v1/comfyui/comfyui_workflow"),
    ),
}


def get_video_adapter_contract(family: VideoProtocolFamily | str) -> VideoEndpointContract:
    normalized = normalize_video_protocol_family(family)
    try:
        return _CONTRACTS[normalized]
    except KeyError as exc:
        raise ValueError(f"no built-in video adapter contract for: {normalized.value}") from exc


def normalize_video_protocol_family(value: object) -> VideoProtocolFamily:
    if isinstance(value, VideoProtocolFamily):
        return value
    normalized = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "openai": VideoProtocolFamily.OPENAI_VIDEO,
        "openai-video": VideoProtocolFamily.OPENAI_VIDEO,
        "openai-compatible-video": VideoProtocolFamily.OPENAI_VIDEO,
        "replicate": VideoProtocolFamily.PREDICTION,
        "predictions": VideoProtocolFamily.PREDICTION,
        "fal": VideoProtocolFamily.QUEUE,
        "fal-queue": VideoProtocolFamily.QUEUE,
        "google-veo": VideoProtocolFamily.LONG_RUNNING_OPERATION,
        "vertex": VideoProtocolFamily.LONG_RUNNING_OPERATION,
        "vertex-ai": VideoProtocolFamily.LONG_RUNNING_OPERATION,
        "minimax": VideoProtocolFamily.TASK_QUERY,
        "minimax-video-v2": VideoProtocolFamily.TASK_QUERY,
        "comfyui": VideoProtocolFamily.WORKFLOW,
        "comfy": VideoProtocolFamily.WORKFLOW,
        "autodl": VideoProtocolFamily.AUTODL_COMFYUI,
        "autodl-comfyui": VideoProtocolFamily.AUTODL_COMFYUI,
        "autodl-comfy-ui": VideoProtocolFamily.AUTODL_COMFYUI,
        "comfyui-autodl": VideoProtocolFamily.AUTODL_COMFYUI,
        "": VideoProtocolFamily.UNKNOWN,
    }
    return aliases.get(normalized, VideoProtocolFamily(normalized) if normalized in {item.value for item in VideoProtocolFamily} else VideoProtocolFamily.UNKNOWN)


def infer_video_protocol_family(
    *,
    protocol_hint: object = "",
    model_id: object = "",
    model_metadata: Mapping[str, Any] | None = None,
    openapi_paths: Iterable[str] = (),
) -> VideoAdapterEvidence:
    """Infer a provider family from non-billing endpoint evidence."""

    metadata = model_metadata or {}
    paths = tuple(sorted({str(path).strip() for path in openapi_paths if str(path).strip()}))
    normalized_paths = tuple(path.lower() for path in paths)
    hint = normalize_video_protocol_family(protocol_hint)
    if hint is not VideoProtocolFamily.UNKNOWN:
        return VideoAdapterEvidence(
            family=hint,
            confidence=0.95,
            sources=(f"protocol:{hint.value}",),
            matched_paths=paths,
        )

    if any("predictlongrunning" in path or "/operations/" in path for path in normalized_paths):
        return VideoAdapterEvidence(
            VideoProtocolFamily.LONG_RUNNING_OPERATION,
            0.92,
            ("openapi",),
            paths,
        )
    if any("/queue/" in path for path in normalized_paths):
        return VideoAdapterEvidence(VideoProtocolFamily.QUEUE, 0.9, ("openapi",), paths)
    if any(path.rstrip("/").endswith("/predictions") for path in normalized_paths):
        return VideoAdapterEvidence(VideoProtocolFamily.PREDICTION, 0.9, ("openapi",), paths)
    if any(path.rstrip("/") == "/prompt" for path in normalized_paths) and any(
        path.startswith("/history") or "/history/" in path for path in normalized_paths
    ):
        return VideoAdapterEvidence(VideoProtocolFamily.WORKFLOW, 0.9, ("openapi",), paths)
    if any("video_generation" in path for path in normalized_paths):
        return VideoAdapterEvidence(VideoProtocolFamily.TASK_QUERY, 0.85, ("openapi",), paths)
    if any("autodl.art" in path for path in normalized_paths):
        return VideoAdapterEvidence(
            VideoProtocolFamily.AUTODL_COMFYUI,
            0.92,
            ("openapi",),
            paths,
        )
    if any(path.rstrip("/").endswith("/videos") for path in normalized_paths):
        return VideoAdapterEvidence(VideoProtocolFamily.OPENAI_VIDEO, 0.85, ("openapi",), paths)

    capability_text = " ".join(
        str(metadata.get(key) or "")
        for key in ("protocol", "protocols", "supported_protocols", "capabilities", "tags")
    ).lower()
    if "prediction" in capability_text:
        return VideoAdapterEvidence(VideoProtocolFamily.PREDICTION, 0.75, ("model-metadata",))
    if "queue" in capability_text:
        return VideoAdapterEvidence(VideoProtocolFamily.QUEUE, 0.75, ("model-metadata",))
    if "workflow" in capability_text or "comfy" in capability_text:
        return VideoAdapterEvidence(VideoProtocolFamily.WORKFLOW, 0.75, ("model-metadata",))

    # Built-in model IDs are only a weak hint.  They help explain why a local
    # profile was selected.  A profile with a declared transport is enough to
    # name the adapter family, but not enough to claim runtime verification.
    model_text = str(model_id or "").lower()
    if "minimax" in model_text and "h3" in model_text:
        return VideoAdapterEvidence(VideoProtocolFamily.TASK_QUERY, 0.65, ("model-profile",))
    try:
        from .direct_video_profiles import resolve_direct_video_profile

        profile = resolve_direct_video_profile(model_text)
        if profile.name != "openai-video-generic":
            return VideoAdapterEvidence(
                VideoProtocolFamily.OPENAI_VIDEO,
                0.72,
                ("model-profile",),
                notes=("profile supplies the local transport family; runtime smoke is still required",),
            )
    except Exception:
        pass
    return VideoAdapterEvidence(VideoProtocolFamily.UNKNOWN, 0.0, ())


def _join_path(base_url: str, path: str) -> str:
    return f"{str(base_url or '').rstrip('/')}/{str(path or '').lstrip('/')}"


__all__ = [
    "VideoAdapterEvidence",
    "VideoEndpointContract",
    "VideoProtocolFamily",
    "get_video_adapter_contract",
    "infer_video_protocol_family",
    "normalize_video_protocol_family",
]
