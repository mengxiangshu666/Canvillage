"""Neutral task failure and provider-pending classification.

Task orchestration should react to stable failure semantics rather than import
optional implementation modules directly.  The concrete exception imports
remain lazy so importing the task service does not require optional extras.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


HandledFailure = tuple[str, dict[str, Any], bool]


@dataclass(frozen=True)
class VideoPendingResolution:
    """A provider task that needs recovery instead of a new render request."""

    kind: Literal["download", "submission"]
    message: str
    provider_task_id: str = ""
    idempotency_key: str = ""


def classify_optional_dependency_failure(exc: BaseException) -> HandledFailure | None:
    """Return the stable task failure payload for optional world dependencies."""

    try:
        from novelvideo.director_world.pano_sharp import Sharp3DUnavailable

        if isinstance(exc, Sharp3DUnavailable):
            return str(exc), {"error_code": exc.error_code}, True
    except Exception:
        pass

    try:
        from novelvideo.director_world.block_world_builder import BlockWorldUnavailable

        if isinstance(exc, BlockWorldUnavailable):
            return str(exc), {"error_code": exc.error_code}, True
    except Exception:
        pass

    return None


def classify_video_pending(exc: BaseException) -> VideoPendingResolution | None:
    """Map provider recovery exceptions to a task-facing state contract."""

    try:
        from novelvideo.freezone.jobs import CompletedVideoDownloadPending

        if isinstance(exc, CompletedVideoDownloadPending):
            return VideoPendingResolution(
                kind="download",
                message=str(exc),
                provider_task_id=exc.provider_task_id,
            )
    except Exception:
        pass

    try:
        from novelvideo.freezone.jobs import VideoSubmissionPending

        if isinstance(exc, VideoSubmissionPending):
            return VideoPendingResolution(
                kind="submission",
                message=str(exc),
                idempotency_key=exc.idempotency_key,
            )
    except Exception:
        pass

    return None


__all__ = [
    "HandledFailure",
    "VideoPendingResolution",
    "classify_optional_dependency_failure",
    "classify_video_pending",
]
