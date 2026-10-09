"""One-step paid-media authorization for WorkflowRun recovery."""

from __future__ import annotations

from typing import Any, Mapping

MEDIA_AUTHORIZATION_SCHEMA = "workflow_media_authorization.v1"
MEDIA_AUTHORIZATION_STEPS = frozenset({"storyboard_images", "shot_videos"})
MEDIA_AUTHORIZATION_ERRORS = {
    "storyboard_images": "workflow_storyboard_paid_media_not_authorized",
    "shot_videos": "workflow_shot_video_paid_media_not_authorized",
}
MEDIA_AUTHORIZATION_ERRORS_BY_STEP = {
    "storyboard_images": frozenset(
        {
            "workflow_storyboard_paid_media_not_authorized",
            "workflow_storyboard_image_failed",
        }
    ),
    "shot_videos": frozenset(
        {
            "workflow_shot_video_paid_media_not_authorized",
            "workflow_shot_video_failed",
        }
    ),
}
MEDIA_AUTHORIZATION_RECOVERY_ACTIONS = frozenset(
    {"request_media_authorization", "retry_failed_items"}
)
MEDIA_AUTHORIZATION_RETRY_SCOPES = frozenset(
    {"whole_step", "failed_items_only"}
)


def _text(value: object, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _item_ids(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item_id in value:
        normalized = _text(item_id)
        if normalized and normalized not in result:
            result.append(normalized)
    return result[:500]


def media_authorization_scope_matches(
    value: object,
    *,
    project_id: str,
    canvas_id: str,
    run_id: str,
    step_id: str,
) -> bool:
    """Validate the immutable Run / step scope of one media authorization."""

    if not isinstance(value, Mapping):
        return False
    return (
        _text(value.get("schema")) == MEDIA_AUTHORIZATION_SCHEMA
        and _text(value.get("authorization_id")).startswith("pmg_")
        and _text(value.get("project_id")) == _text(project_id)
        and _text(value.get("canvas_id")) == _text(canvas_id)
        and _text(value.get("run_id")) == _text(run_id)
        and _text(value.get("step_id")) == _text(step_id)
        and _text(value.get("error_code"))
        in MEDIA_AUTHORIZATION_ERRORS_BY_STEP.get(_text(step_id), frozenset())
        and bool(_text(value.get("consume_key")))
    )


def media_authorization_recovery_matches(
    value: object,
    recovery: object,
    *,
    command_retry_scope: str,
    command_item_ids: object,
) -> bool:
    """Bind a media marker to the persisted recovery and requested retry unit."""

    if not isinstance(value, Mapping) or not isinstance(recovery, Mapping):
        return False

    recovery_action = _text(recovery.get("action"))
    marker_action = _text(value.get("recovery_action")) or (
        "request_media_authorization"
    )
    if (
        recovery_action not in MEDIA_AUTHORIZATION_RECOVERY_ACTIONS
        or marker_action != recovery_action
        or recovery.get("auto_retry_allowed") is not False
        or recovery.get("requires_paid_media") is not True
        or _text(value.get("error_code")) != _text(recovery.get("error_code"))
    ):
        return False

    marker_item_ids = _item_ids(value.get("item_ids"))
    requested_item_ids = _item_ids(command_item_ids)
    if marker_item_ids != requested_item_ids:
        return False

    if recovery_action == "request_media_authorization":
        marker_scope = _text(value.get("retry_scope")) or "whole_step"
        return (
            _text(recovery.get("rerun_scope")) == "current_step"
            and marker_scope == "whole_step"
            and _text(command_retry_scope) == "whole_step"
            and not marker_item_ids
        )

    recovery_item_ids = _item_ids(recovery.get("item_ids"))
    marker_scope = _text(value.get("retry_scope"))
    return (
        _text(recovery.get("rerun_scope")) == "failed_items_only"
        and marker_scope == "failed_items_only"
        and _text(command_retry_scope) == "failed_items_only"
        and bool(marker_item_ids)
        and set(marker_item_ids).issubset(recovery_item_ids)
    )


def media_authorization_from_run(
    run: Mapping[str, Any],
    *,
    step_id: str,
) -> dict[str, Any] | None:
    """Read the current step marker without trusting the model or UI."""

    artifacts = run.get("artifacts")
    artifact = (
        artifacts.get(step_id)
        if isinstance(artifacts, Mapping)
        and isinstance(artifacts.get(step_id), Mapping)
        else None
    )
    marker = artifact.get("media_authorization") if isinstance(artifact, Mapping) else None
    if not media_authorization_scope_matches(
        marker,
        project_id=_text(run.get("project_id")),
        canvas_id=_text(run.get("canvas_id")),
        run_id=_text(run.get("id")),
        step_id=step_id,
    ):
        return None
    return dict(marker)  # type: ignore[arg-type]


__all__ = [
    "MEDIA_AUTHORIZATION_ERRORS",
    "MEDIA_AUTHORIZATION_ERRORS_BY_STEP",
    "MEDIA_AUTHORIZATION_RECOVERY_ACTIONS",
    "MEDIA_AUTHORIZATION_RETRY_SCOPES",
    "MEDIA_AUTHORIZATION_SCHEMA",
    "MEDIA_AUTHORIZATION_STEPS",
    "media_authorization_from_run",
    "media_authorization_recovery_matches",
    "media_authorization_scope_matches",
]
