"""Celery runners for graph-to-SQLite build steps."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from novelvideo.novel_source import require_imported_novel
from novelvideo.services.production_foundation import (
    clear_foundation_stage_evidence,
    record_foundation_stage_complete,
)
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager

logger = logging.getLogger(__name__)


def _run_async(coro, envelope: dict[str, Any], task_type: str):
    return asyncio.run(
        await_envelope_with_cancel_watch(coro, envelope, task_type=task_type)
    )


def _progress(
    ctx: ProjectContext, task_type: str, progress: float | None, task: str
) -> None:
    """上报图谱构建进度和日志。

    图谱层的 ``on_log`` 不携带进度；传入 ``None`` 表示仅更新步骤文案和日志，
    保留任务记录里已有的进度。把日志当成 ``0.0`` 会导致前端进度条在真实进度
    与 0% 之间反复倒退。
    """
    get_task_manager().update_progress_for_project(
        ctx,
        task_type,
        0,
        progress=progress,
        current_task=task,
        logs=[task],
    )


async def _load_store(ctx: ProjectContext):
    from novelvideo.cognee import CogneeStore

    store = CogneeStore(
        ctx.owner_project_label,
        output_dir=str(ctx.output_dir),
        state_dir=str(ctx.state_dir),
    )
    await store.initialize()
    await store.load_graph_state()
    return store


async def _persisted_entity_count(
    store: Any,
    method_name: str,
    fallback: list[Any],
) -> int:
    """Read the post-build total without making lightweight test stores async-aware."""

    sqlite_store = getattr(store, "sqlite_store", None)
    method = getattr(sqlite_store, method_name, None)
    if callable(method):
        rows = await method()
        if rows is not None:
            try:
                return len(rows)
            except TypeError:
                pass
    return len(fallback)


def run_build_characters(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return _run_async(_run_build_characters(ctx), envelope, "build_characters")


async def _run_build_characters(ctx: ProjectContext) -> dict[str, Any]:
    require_imported_novel(ctx.output_dir)
    clear_foundation_stage_evidence(ctx.state_dir, "build_characters")
    store = await _load_store(ctx)
    try:
        characters = await store.build_characters_from_graph(
            on_progress=lambda progress, task: _progress(ctx, "build_characters", progress, task),
            on_log=lambda message: _progress(ctx, "build_characters", None, message),
        )
    finally:
        await store.close()
    result = {"characters": len(characters), "added_characters": len(characters)}
    record_foundation_stage_complete(ctx.state_dir, "build_characters", result)
    return result


def run_build_scenes(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return _run_async(_run_build_scenes(ctx), envelope, "build_scenes")


async def _run_build_scenes(ctx: ProjectContext) -> dict[str, Any]:
    require_imported_novel(ctx.output_dir)
    clear_foundation_stage_evidence(ctx.state_dir, "build_scenes")
    store = await _load_store(ctx)
    total_scenes = 0
    try:
        scenes = await store.build_scenes_from_graph(
            on_progress=lambda progress, task: _progress(ctx, "build_scenes", progress, task),
            on_log=lambda message: _progress(ctx, "build_scenes", None, message),
        )
        total_scenes = await _persisted_entity_count(
            store,
            "list_scenes",
            scenes,
        )
    finally:
        await store.close()
    result = {
        "scenes": total_scenes,
        "added_scenes": len(scenes),
        "total_scenes": total_scenes,
    }
    record_foundation_stage_complete(ctx.state_dir, "build_scenes", result)
    if total_scenes <= 0:
        raise RuntimeError(
            "场景构建未产出任何场景（total_scenes=0）；"
            "请检查导入剧本、知识图谱或场景抽取结果后重试"
        )
    return result


def run_build_props(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return _run_async(_run_build_props(ctx), envelope, "build_props")


async def _run_build_props(ctx: ProjectContext) -> dict[str, Any]:
    require_imported_novel(ctx.output_dir)
    clear_foundation_stage_evidence(ctx.state_dir, "build_props")
    store = await _load_store(ctx)
    total_props = 0
    try:
        props = await store.build_props_from_graph(
            on_progress=lambda progress, task: _progress(ctx, "build_props", progress, task),
            on_log=lambda message: _progress(ctx, "build_props", None, message),
        )
        total_props = await _persisted_entity_count(
            store,
            "list_props",
            props,
        )
    finally:
        await store.close()
    result = {
        "props": total_props,
        "added_props": len(props),
        "total_props": total_props,
        "extraction_status": "completed" if total_props > 0 else "completed_empty",
        "summary": (
            f"已提取 {total_props} 个道具"
            if total_props > 0
            else "本故事无需独立道具"
        ),
    }
    record_foundation_stage_complete(ctx.state_dir, "build_props", result)
    return result


def run_build_episodes(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return _run_async(_run_build_episodes(envelope, ctx), envelope, "build_episodes")


async def _run_build_episodes(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    from novelvideo.agents.episode_planner import EpisodePlannerAgent

    payload = envelope.get("payload") or {}
    config = dict(payload.get("config") or {})
    target = int(config.get("target_episodes", 10))
    if target <= 0:
        raise ValueError("目标集数必须大于 0")
    use_agent = bool(config.get("use_agent_planner", True))
    planning_mode = str(config.get("planning_mode", "ai"))
    generate_metadata = bool(config.get("generate_metadata", False))
    require_imported_novel(ctx.output_dir)
    store = await _load_store(ctx)
    try:
        def update(progress: float | None, task: str) -> None:
            _progress(ctx, "build_episodes", progress, task)

        if planning_mode == "chapters":
            episodes = await store.build_episodes_from_chapters(
                generate_metadata=generate_metadata,
                on_progress=update,
                on_log=lambda message: update(None, message),
            )
        elif planning_mode == "ai_events":
            episodes = await store.build_episodes_from_events(
                target_episodes=target,
                on_progress=update,
                on_log=lambda message: update(None, message),
            )
        elif use_agent:
            try:
                planner = EpisodePlannerAgent(store)
                episodes = await planner.plan_episodes(
                    target_episodes=target,
                    on_progress=update,
                    on_log=lambda message: update(None, message),
                )
            except Exception:
                logger.warning(
                    "EpisodePlannerAgent 分集失败，回退到确定性分集 target_episodes=%s",
                    target,
                    exc_info=True,
                )
                episodes = await store.build_episodes(
                    target_episodes=target,
                    on_progress=update,
                    on_log=lambda message: update(None, message),
                )
            if not episodes:
                raise RuntimeError(
                    "分集规划未产出任何剧集，请重试或检查文字模型的结构化输出。"
                )
            await store.replace_episodes(episodes)
        else:
            episodes = await store.build_episodes(
                target_episodes=target,
                on_progress=update,
                on_log=lambda message: update(None, message),
            )
        if not episodes:
            raise RuntimeError("分集规划未产出任何剧集，请重试或检查文字模型的结构化输出。")
        return {"episodes": len(episodes)}
    finally:
        await store.close()


register_project_task_runner("build_characters", run_build_characters)
register_project_task_runner("build_scenes", run_build_scenes)
register_project_task_runner("build_props", run_build_props)
register_project_task_runner("build_episodes", run_build_episodes)
