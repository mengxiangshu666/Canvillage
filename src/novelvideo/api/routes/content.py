"""原文、改写稿与解说 adapter 端点。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from novelvideo.api.auth import get_api_user, require_project_scope
from novelvideo.api.deps import get_sqlite_store, resolve_project_scope
from novelvideo.api.schemas import ContentUpdateRequest, RewriteGenerateRequest
from novelvideo.ports import get_task_backend
from novelvideo.ports.tasks import queued_task_receipt_fields
from novelvideo.sqlite_store import SQLiteStore
from novelvideo.task_identity import project_task_state_key

logger = logging.getLogger("novelvideo.api.content")

router = APIRouter()


@router.get("/projects/{project}/episodes/{episode_num}/raw-content")
async def get_raw_content(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
    store: SQLiteStore = Depends(get_sqlite_store),
):
    """读取指定集的原文。"""
    content = await store.load_episode_content(episode_num) or ""
    return {"ok": True, "data": {"episode": episode_num, "content": content}}


@router.put("/projects/{project}/episodes/{episode_num}/raw-content")
async def put_raw_content(
    project: str,
    episode_num: int,
    body: ContentUpdateRequest,
    user: dict = Depends(require_project_scope("projects:write")),
    store: SQLiteStore = Depends(get_sqlite_store),
):
    """保存指定集的原文。"""
    logger.info("[%s] EP%d put_raw_content: %d chars", project, episode_num, len(body.content))
    await store.save_episode_content(episode_num, body.content)
    return {"ok": True, "data": {"episode": episode_num, "length": len(body.content)}}


@router.get("/projects/{project}/episodes/{episode_num}/adapted-content")
async def get_adapted_content(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
    store: SQLiteStore = Depends(get_sqlite_store),
):
    """读取指定集的改写稿。未保存时返回空串。"""
    content = await store.load_adapted_content(episode_num)
    return {"ok": True, "data": {"episode": episode_num, "content": content}}


@router.put("/projects/{project}/episodes/{episode_num}/adapted-content")
async def put_adapted_content(
    project: str,
    episode_num: int,
    body: ContentUpdateRequest,
    user: dict = Depends(require_project_scope("projects:write")),
    store: SQLiteStore = Depends(get_sqlite_store),
):
    """保存指定集的改写稿。集不存在时返回 400。"""
    logger.info(
        "[%s] EP%d put_adapted_content: %d chars",
        project,
        episode_num,
        len(body.content),
    )
    try:
        await store.save_adapted_content(episode_num, body.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "data": {"episode": episode_num, "length": len(body.content)}}


@router.delete("/projects/{project}/episodes/{episode_num}/adapted-content")
async def delete_adapted_content(
    project: str,
    episode_num: int,
    user: dict = Depends(require_project_scope("projects:write")),
    store: SQLiteStore = Depends(get_sqlite_store),
):
    """清空指定集的改写稿，回退到原文。"""
    logger.info("[%s] EP%d delete_adapted_content", project, episode_num)
    try:
        await store.save_adapted_content(episode_num, "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "data": {"episode": episode_num}}


@router.post("/projects/{project}/episodes/{episode_num}/rewrite/generate")
async def generate_rewrite(
    project: str,
    episode_num: int,
    body: RewriteGenerateRequest,
    user: dict = Depends(require_project_scope("projects:write")),
    store: SQLiteStore = Depends(get_sqlite_store),
):
    """兼容旧客户端，同步执行并直接保存改写稿。"""
    from novelvideo.services.content_rewrite import (
        ContentRewriteInputError,
        execute_content_rewrite,
    )

    try:
        result = await execute_content_rewrite(
            store,
            episode_num,
            target_beats=body.target_beats,
            beat_chars_min=body.beat_chars_min,
            beat_chars_max=body.beat_chars_max,
            narration_style=body.narration_style or "first_person",
            apply=True,
        )
    except ContentRewriteInputError as exc:
        return {"ok": False, "error": str(exc)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "data": result}


@router.post("/projects/{project}/episodes/{episode_num}/rewrite/generate-async")
async def generate_rewrite_async(
    project: str,
    episode_num: int,
    body: RewriteGenerateRequest = RewriteGenerateRequest(),
    user: dict = Depends(get_api_user),
):
    """Queue the ContentRewriter with an explicit preview/apply boundary."""
    resolved = await resolve_project_scope(project, user, required_role="editor")
    if resolved.ctx is None:
        return {"ok": False, "error": "改写任务需要 project context"}
    if episode_num <= 0:
        return {"ok": False, "error": "episode_num 必须是正整数"}

    scope = "rewrite_apply" if body.apply else "rewrite_preview"
    queued = await get_task_backend().enqueue_project_task(
        resolved.ctx,
        task_type="content_rewrite",
        queue_kind="default",
        episode=episode_num,
        scope=scope,
        payload={
            "episode": episode_num,
            "target_beats": body.target_beats,
            "beat_chars_min": body.beat_chars_min,
            "beat_chars_max": body.beat_chars_max,
            "narration_style": body.narration_style or "first_person",
            "apply": body.apply,
        },
    )
    return {
        "ok": True,
        "task_type": "content_rewrite",
        "task_id": queued.task_state.task_id,
        "task_key": project_task_state_key(
            "content_rewrite",
            resolved.ctx.project_id,
            episode_num,
            scope=scope,
        ),
        "scope": scope,
        "backend": queued.backend,
        "queue": queued.queue,
        **queued_task_receipt_fields(queued),
        "message": f"第 {episode_num} 集改写{'应用' if body.apply else '预览'}任务已入队",
    }
