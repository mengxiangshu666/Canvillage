"""Shared runtime-selection primitives for the video model framework.

The catalog, direct registry, and executors all need to answer the same small
question: is this adapter a generic non-OpenAI lifecycle or one of the legacy
specialized transports?  Keeping that decision here prevents string sets from
drifting between UI readiness and generator dispatch.
"""

from __future__ import annotations

from .video_provider_adapters import VideoProtocolFamily, normalize_video_protocol_family


GENERIC_VIDEO_PROTOCOL_FAMILIES = frozenset(
    {
        VideoProtocolFamily.PREDICTION.value,
        VideoProtocolFamily.QUEUE.value,
        VideoProtocolFamily.LONG_RUNNING_OPERATION.value,
        VideoProtocolFamily.WORKFLOW.value,
        VideoProtocolFamily.AUTODL_COMFYUI.value,
    }
)

SPECIALIZED_VIDEO_PROTOCOL_FAMILIES = frozenset(
    {
        VideoProtocolFamily.OPENAI_VIDEO.value,
        VideoProtocolFamily.TASK_QUERY.value,
    }
)


def normalize_adapter_family(value: object) -> str:
    """Normalize an adapter family without promoting unknown values."""

    family = normalize_video_protocol_family(value)
    return family.value if family is not VideoProtocolFamily.UNKNOWN else ""


def is_generic_video_adapter(value: object) -> bool:
    return normalize_adapter_family(value) in GENERIC_VIDEO_PROTOCOL_FAMILIES


__all__ = [
    "GENERIC_VIDEO_PROTOCOL_FAMILIES",
    "SPECIALIZED_VIDEO_PROTOCOL_FAMILIES",
    "is_generic_video_adapter",
    "normalize_adapter_family",
]
