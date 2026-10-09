"""TaskBackend runners for Story Lab generation."""

from __future__ import annotations

import asyncio
from typing import Any

from novelvideo.project_context import ProjectContext
from novelvideo.services.story_pipeline import (
    STORY_LAB_STAGE_NAMES,
    generate_story_lab_stage,
    normalize_story_lab_stage,
)
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager

_TASK_STAGES = {
    f"story_lab_{stage}": stage for stage in STORY_LAB_STAGE_NAMES
}


def _update_progress(
    ctx: ProjectContext,
    task_type: str,
    stage: str,
    progress: float,
    current_task: str,
) -> None:
    get_task_manager().update_progress_for_project(
        ctx,
        task_type,
        0,
        scope=stage,
        progress=progress,
        current_task=current_task,
        logs=[current_task],
    )


async def _run_story_lab_task(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    task_type = str(envelope.get("task_type") or "")
    payload = dict(envelope.get("payload") or {})
    stage = _TASK_STAGES.get(task_type)
    if stage is None:
        stage = normalize_story_lab_stage(payload.get("stage"))
    _update_progress(ctx, task_type, stage, 0.08, "加载故事 Agent项目状态")
    _update_progress(ctx, task_type, stage, 0.20, "构建阶段上下文与风格约束")
    result = await generate_story_lab_stage(
        ctx,
        stage,
        instructions=str(payload.get("instructions") or ""),
    )
    _update_progress(ctx, task_type, stage, 0.95, "校验并保存故事 Agent阶段结果")
    return result


def run_story_lab_task(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any] | None:
    task_type = str(envelope.get("task_type") or "story_lab")
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_story_lab_task(envelope, ctx),
            envelope,
            task_type=task_type,
        )
    )


for _task_type in _TASK_STAGES:
    register_project_task_runner(_task_type, run_story_lab_task)
