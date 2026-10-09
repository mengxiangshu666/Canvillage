"""Task backend and cancellation ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from novelvideo.task_backend.receipts import project_task_acceptance_receipt
from novelvideo.task_identity import project_task_state_key


@dataclass(frozen=True)
class QueuedTask:
    task_state: Any
    backend: str
    queue: str | None = None
    celery_id: str | None = None

    @property
    def acceptance_receipt(self) -> dict[str, Any]:
        """Expose one canonical acceptance fact without changing task state."""

        metadata = getattr(self.task_state, "metadata", None)
        if isinstance(metadata, dict):
            return project_task_acceptance_receipt(
                metadata.get("task_acceptance_receipt")
            )
        return {}


def queued_task_receipt_fields(queued: QueuedTask) -> dict[str, Any]:
    """Return the optional public receipt fields for an API task response.

    Older test doubles and legacy adapters may not carry metadata yet, so the
    helper intentionally returns an empty mapping instead of manufacturing a
    second receipt.  Real queued tasks always receive the persisted receipt
    from the task backend.
    """

    receipt = getattr(queued, "acceptance_receipt", None)
    if not isinstance(receipt, dict):
        metadata = getattr(getattr(queued, "task_state", None), "metadata", None)
        receipt = project_task_acceptance_receipt(
            metadata.get("task_acceptance_receipt")
            if isinstance(metadata, dict)
            else None
        )
    return {"task_acceptance_receipt": receipt} if receipt else {}


def queued_task_response(
    queued: QueuedTask,
    *,
    task_type: str,
    project_id: str,
    episode: int = 0,
    beat_num: int | None = None,
    scope: str | None = None,
    data: dict[str, Any] | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """Build the stable task envelope shared by user-facing routes.

    The task key is derived from the same identity function used by TaskState;
    the acceptance receipt is copied only when the backend persisted it.  This
    keeps legacy adapters compatible while preventing each route from inventing
    its own task identity or acceptance semantics.
    """

    task_key = project_task_state_key(
        task_type,
        project_id,
        int(episode),
        beat_num=beat_num,
        scope=scope,
    )
    payload: dict[str, Any] = {
        "task_type": str(task_type),
        "task_id": str(getattr(queued.task_state, "task_id", "")),
        "task_key": task_key,
        "backend": queued.backend,
        "queue": queued.queue,
    }
    if beat_num is not None:
        payload["task_beat_num"] = int(beat_num)
    if scope:
        payload["scope"] = str(scope)
    payload.update(queued_task_receipt_fields(queued))
    if data:
        payload.update(data)
    response: dict[str, Any] = {"ok": True, **payload}
    if message:
        response["message"] = str(message)
    return response


def display_metadata_for_task(
    task_type: str, payload: dict[str, Any] | None
) -> dict[str, str]:
    if not payload:
        return {}
    metadata: dict[str, str] = {}

    for key in (
        "display_name",
        "task_label",
        "task_family",
        "source_label",
        "target_label",
        "canvas_id",
        "node_id",
        "skill_id",
        "workflow_submission_fingerprint",
    ):
        value = str(payload.get(key) or "").strip()
        if value:
            metadata[key] = value

    if task_type == "stage_asset":
        for key in ("scene_name", "step"):
            value = str(payload.get(key) or "").strip()
            if value:
                metadata[key] = value
    return metadata


def cancel_key(
    *,
    project_id: str,
    task_type: str,
    episode: int,
    task_id: str,
    beat_num: int | None = None,
    scope: str | None = None,
) -> str:
    parts = ["task", "cancel", project_id, task_type, str(episode)]
    if beat_num is not None:
        parts.append(str(beat_num))
    if scope:
        parts.append(str(scope))
    parts.append(str(task_id))
    return ":".join(parts)


class TaskBackend(Protocol):
    async def enqueue_project_task(
        self,
        ctx,
        *,
        task_type: str,
        queue_kind: str = "default",
        episode: int = 0,
        beat_num: int | None = None,
        scope: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> QueuedTask: ...

    async def cancel_project_task(self, ctx, task_state) -> bool: ...


class CancellationStore(Protocol):
    async def request_cancel(
        self,
        *,
        project_id: str,
        task_type: str,
        episode: int,
        task_id: str,
        beat_num: int | None = None,
        scope: str | None = None,
        ttl_seconds: int = 86_400,
    ) -> None: ...

    async def is_cancel_requested(
        self,
        *,
        project_id: str,
        task_type: str,
        episode: int,
        task_id: str,
        beat_num: int | None = None,
        scope: str | None = None,
    ) -> bool: ...
