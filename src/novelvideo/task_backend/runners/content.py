"""Project task runner for the ContentRewriter adapter."""

from __future__ import annotations

import asyncio
from typing import Any

from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


async def _run_content_rewrite(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.content_rewrite import execute_content_rewrite
    from novelvideo.services.project_resources import make_sqlite_store_for_context

    payload = envelope.get("payload") or {}
    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    if episode <= 0:
        raise ValueError("episode is required and must be a positive integer")

    task_type = str(envelope.get("task_type") or "content_rewrite")
    scope = str(envelope.get("scope") or "")
    apply = bool(payload.get("apply"))
    manager = get_task_manager()

    def update_progress(progress: float, message: str) -> None:
        manager.update_progress_for_project(
            ctx,
            task_type,
            episode,
            scope=scope,
            progress=progress,
            current_task=message,
            logs=[message],
        )

    update_progress(0.05, f"加载第 {episode} 集原文...")
    store = await make_sqlite_store_for_context(ctx)
    try:
        update_progress(0.15, "调用 ContentRewriter 生成逐行改写稿...")
        result = await execute_content_rewrite(
            store,
            episode,
            target_beats=int(payload.get("target_beats") or 18),
            beat_chars_min=int(payload.get("beat_chars_min") or 14),
            beat_chars_max=int(payload.get("beat_chars_max") or 20),
            narration_style=str(payload.get("narration_style") or "first_person"),
            apply=apply,
        )
        update_progress(
            0.95,
            f"第 {episode} 集改写{'已写回' if apply else '预览'}完成",
        )
        return result
    finally:
        await store.close()


def run_content_rewrite(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_content_rewrite(envelope, ctx),
            envelope,
            task_type="content_rewrite",
        )
    )


register_project_task_runner("content_rewrite", run_content_rewrite)


__all__ = ["run_content_rewrite"]
