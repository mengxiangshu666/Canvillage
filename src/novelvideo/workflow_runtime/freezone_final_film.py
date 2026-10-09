"""Durable WorkflowRun bridge from verified shot videos to final MP4."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novelvideo.workflow_runtime.compose_authorization import (
    COMPOSE_AUTHORIZATION_REQUEST_SCHEMA,
    COMPOSE_AUTHORIZATION_SCHEMA,
    is_compose_source_signature,
)
from novelvideo.workflow_runtime.media_dispatch import (
    _validated_shot_video_compose_beats,
    dispatch_workflow_compose,
    reconcile_workflow_compose,
)
from novelvideo.workflow_runtime.media_dispatch_support import (
    _text,
    resolve_workflow_project_context,
)
from novelvideo.workflow_runtime.production_authorization import (
    production_authorization_allows_final_film,
)
from novelvideo.workflow_runtime.production_plan import (
    validate_production_plan_binding,
)
from novelvideo.workflow_runtime.step_contract import (
    StepResult,
    WorkflowStepExecutionError,
)


def _shot_video_signature(run: dict[str, Any]) -> str:
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    shot_videos = (
        artifacts.get("shot_videos")
        if isinstance(artifacts.get("shot_videos"), dict)
        else {}
    )
    signature = str(shot_videos.get("result_signature") or "").strip()
    return signature if is_compose_source_signature(signature) else ""


def _shot_video_authorization_facts(run: dict[str, Any]) -> tuple[int, float]:
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    shot_videos = (
        artifacts.get("shot_videos")
        if isinstance(artifacts.get("shot_videos"), dict)
        else {}
    )
    videos = (
        [item for item in shot_videos.get("videos") if isinstance(item, dict)]
        if isinstance(shot_videos.get("videos"), list)
        else []
    )
    jobs = (
        [item for item in shot_videos.get("jobs") if isinstance(item, dict)]
        if isinstance(shot_videos.get("jobs"), list)
        else []
    )
    items = videos or jobs
    shot_count = len(items)
    if shot_count <= 0:
        try:
            shot_count = max(0, int(shot_videos.get("shot_count") or 0))
        except (TypeError, ValueError):
            shot_count = 0
    duration_seconds = 0.0
    for item in items:
        try:
            duration_seconds += max(0.0, float(item.get("duration_seconds") or 0.0))
        except (TypeError, ValueError):
            continue
    return shot_count, duration_seconds


def _authorization_request(
    run: dict[str, Any],
    *,
    step_id: str,
    source_signature: str,
) -> dict[str, Any]:
    return {
        "schema": COMPOSE_AUTHORIZATION_REQUEST_SCHEMA,
        "run_id": _text(run.get("id"))[:200],
        "step_id": step_id,
        "source_result_signature": source_signature,
        "requires_user_action": True,
    }


def _require_authorized(
    run: dict[str, Any],
    *,
    step_id: str,
) -> None:
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    artifact = (
        artifacts.get(step_id) if isinstance(artifacts.get(step_id), dict) else {}
    )
    authorization = (
        artifact.get("compose_authorization")
        if isinstance(artifact.get("compose_authorization"), dict)
        else {}
    )
    source_signature = _shot_video_signature(run)
    details = {
        "media_submission_started": False,
        "source_result_signature": source_signature,
        "authorization_request": _authorization_request(
            run,
            step_id=step_id,
            source_signature=source_signature,
        ),
    }
    if (
        str(authorization.get("schema") or "") != COMPOSE_AUTHORIZATION_SCHEMA
        or not str(authorization.get("authorization_id") or "").strip()
        or str(authorization.get("step_id") or "").strip() != step_id
        or str(authorization.get("run_id") or "").strip() != _text(run.get("id"))[:200]
    ):
        shot_count, duration_seconds = _shot_video_authorization_facts(run)
        allowed, authorization_reason = production_authorization_allows_final_film(
            run,
            shot_count=shot_count,
            duration_seconds=duration_seconds,
        )
        if allowed:
            return
        raise WorkflowStepExecutionError(
            "当前运行没有最终合成授权，拒绝启动成片",
            code="workflow_final_film_not_authorized",
            details={
                **details,
                "reason": authorization_reason,
                "authorized_shot_count": shot_count,
                "authorized_duration_seconds": duration_seconds,
            },
        )
    if (
        not source_signature
        or str(authorization.get("source_result_signature") or "").strip()
        != source_signature
    ):
        raise WorkflowStepExecutionError(
            "逐镜视频结果签名已变化，原最终合成授权失效",
            code="workflow_final_film_not_authorized",
            details={
                **details,
                "reason": "compose_authorization_source_stale",
            },
        )


async def _require_ready_input(run: dict[str, Any]) -> None:
    ctx = await resolve_workflow_project_context(run)
    try:
        beats, _metadata = _validated_shot_video_compose_beats(ctx, run)
    except ValueError as exc:
        raise WorkflowStepExecutionError(
            str(exc),
            code="workflow_final_film_input_invalid",
            details={"media_submission_started": False},
        ) from exc
    if not beats:
        raise WorkflowStepExecutionError(
            "逐镜视频尚未完成，拒绝启动最终合成",
            code="workflow_final_film_input_invalid",
            details={"media_submission_started": False},
        )


async def handle_workflow_final_film(
    run: dict[str, Any],
    step: dict[str, Any],
) -> StepResult:
    """Dispatch or reconcile one durable final-film compose task."""

    validate_production_plan_binding(run)
    step_id = _text(step.get("id")) or "final_film"
    existing = run.get("artifacts", {}).get(step_id)
    if isinstance(existing, dict) and existing.get("status") in {
        "monitoring",
        "completed",
    }:
        _require_authorized(run, step_id=step_id)
        try:
            result = await reconcile_workflow_compose(
                run,
                state_dir=Path(str(run.get("_state_dir") or "")),
                step_id=step_id,
                artifact=existing,
            )
        except ValueError as exc:
            raise WorkflowStepExecutionError(
                str(exc),
                code="workflow_final_film_input_invalid",
                details={"media_submission_started": False},
            ) from exc
        if result.get("status") == "failed":
            raise WorkflowStepExecutionError(
                _text(result.get("error")) or "最终合成未通过",
                code=_text(result.get("error_code")) or "workflow_final_film_failed",
                details={
                    key: value
                    for key, value in result.items()
                    if key not in {"error", "error_code"}
                },
            )
        if result.get("status") == "completed":
            return StepResult("step_completed", result)
        return StepResult("waiting", result)
    await _require_ready_input(run)
    _require_authorized(run, step_id=step_id)
    try:
        payload = await dispatch_workflow_compose(
            run,
            state_dir=Path(str(run.get("_state_dir") or "")),
            step_id=step_id,
        )
    except ValueError as exc:
        raise WorkflowStepExecutionError(
            str(exc),
            code="workflow_final_film_input_invalid",
            details={"media_submission_started": False},
        ) from exc
    if payload.get("status") == "completed":
        return StepResult("step_completed", payload)
    return StepResult("step_progress", payload)


__all__ = ["handle_workflow_final_film"]
