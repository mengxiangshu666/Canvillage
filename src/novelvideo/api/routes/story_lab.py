"""Story Lab project endpoints."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from novelvideo.api.auth import get_api_user
from novelvideo.api.chapter_preview import count_billable_novel_chars, load_novel_text
from novelvideo.api.deps import resolve_project_scope
from novelvideo.ports import get_task_backend
from novelvideo.ports.tasks import queued_task_receipt_fields
from novelvideo.project_config import save_project_config_in_state_dir
from novelvideo.story_lab.models import (
    StoryLabConfig,
    StoryLabExportRequest,
    StoryLabGenerateRequest,
    StoryLabPublishRequest,
    StoryLabStage,
    StoryLabStageArtifact,
    StoryLabStageEditRequest,
)
from novelvideo.story_lab.persistence import (
    StoryLabNotConfiguredError,
    StoryLabRepository,
    StoryLabStageMissingError,
    safe_story_lab_filename,
)
from novelvideo.story_lab.prompts import PROMPT_VERSIONS
from novelvideo.task_identity import project_task_state_key

router = APIRouter()


def _task_type(stage: StoryLabStage) -> str:
    return f"story_lab_{stage.value}"


def _task_response(*, queued, ctx, stage: StoryLabStage) -> dict:
    task_type = _task_type(stage)
    return {
        "ok": True,
        "data": {
            "task_type": task_type,
            "stage": stage.value,
            "task_id": queued.task_state.task_id,
            "task_key": project_task_state_key(
                task_type,
                ctx.project_id,
                0,
                scope=stage.value,
            ),
            "backend": queued.backend,
            "queue": queued.queue,
            **queued_task_receipt_fields(queued),
        },
        "message": f"故事 Agent {stage.value} 任务已进入队列",
    }


def _state_payload(repository: StoryLabRepository) -> dict:
    payload = repository.load_state().model_dump(mode="json")
    payload["artifacts"] = payload["stages"]
    return payload


def _require_config(repository: StoryLabRepository) -> StoryLabConfig:
    try:
        return repository.require_config()
    except StoryLabNotConfiguredError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "story_lab_not_configured", "message": str(exc)},
        ) from exc


def _export_draft(repository: StoryLabRepository, filename: str) -> Path:
    try:
        return repository.export_draft(filename=filename)
    except StoryLabNotConfiguredError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "story_lab_not_configured", "message": str(exc)},
        ) from exc
    except StoryLabStageMissingError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "story_lab_draft_missing", "message": str(exc)},
        ) from exc


def _require_stage_dependencies(
    repository: StoryLabRepository, stage: StoryLabStage
) -> None:
    dependencies = {
        StoryLabStage.BIBLE: (),
        StoryLabStage.OUTLINE: (StoryLabStage.BIBLE,),
        StoryLabStage.DRAFT: (StoryLabStage.BIBLE, StoryLabStage.OUTLINE),
        StoryLabStage.AUDIT: (
            StoryLabStage.BIBLE,
            StoryLabStage.OUTLINE,
            StoryLabStage.DRAFT,
        ),
    }[stage]
    missing = [item.value for item in dependencies if repository.load_artifact(item) is None]
    if missing:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "story_lab_stage_dependency_missing",
                "stage": stage.value,
                "missing": missing,
            },
        )


@router.get("/projects/{project}/story-lab")
async def get_story_lab(project: str, user: dict = Depends(get_api_user)):
    resolved = await resolve_project_scope(project, user, required_role="viewer")
    repository = StoryLabRepository(resolved.ctx)
    return {"ok": True, "data": _state_payload(repository)}


@router.put("/projects/{project}/story-lab/config")
async def save_story_lab_config(
    project: str,
    body: StoryLabConfig,
    user: dict = Depends(get_api_user),
):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    repository = StoryLabRepository(resolved.ctx)
    repository.save_config(body)
    return {"ok": True, "data": _state_payload(repository)}


@router.post("/projects/{project}/story-lab/generate")
async def generate_story_lab(
    project: str,
    body: StoryLabGenerateRequest,
    user: dict = Depends(get_api_user),
):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    repository = StoryLabRepository(resolved.ctx)
    _require_config(repository)
    _require_stage_dependencies(repository, body.stage)
    task_type = _task_type(body.stage)
    queued = await get_task_backend().enqueue_project_task(
        resolved.ctx,
        task_type=task_type,
        queue_kind="default",
        episode=0,
        scope=body.stage.value,
        payload={
            "stage": body.stage.value,
            "instructions": body.instructions,
            "display_name": f"故事 Agent · {body.stage.value}",
        },
    )
    return _task_response(queued=queued, ctx=resolved.ctx, stage=body.stage)


@router.get("/projects/{project}/story-lab/stages/{stage}")
async def get_story_lab_stage(
    project: str,
    stage: StoryLabStage,
    user: dict = Depends(get_api_user),
):
    resolved = await resolve_project_scope(project, user, required_role="viewer")
    artifact = StoryLabRepository(resolved.ctx).load_artifact(stage)
    return {
        "ok": True,
        "data": artifact.model_dump(mode="json") if artifact is not None else None,
    }


@router.put("/projects/{project}/story-lab/stages/{stage}")
async def save_story_lab_stage(
    project: str,
    stage: StoryLabStage,
    body: StoryLabStageEditRequest,
    user: dict = Depends(get_api_user),
):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    repository = StoryLabRepository(resolved.ctx)
    existing = repository.load_artifact(stage)
    artifact = StoryLabStageArtifact(
        stage=stage,
        prompt_version=(existing.prompt_version if existing else PROMPT_VERSIONS[stage]),
        model=(existing.model if existing else "manual"),
        result=body.result,
        source="manual",
        instructions=body.editor_note,
        revision=(existing.revision + 1 if existing else 1),
    )
    saved = repository.save_artifact(artifact)
    return {"ok": True, "data": saved.model_dump(mode="json")}


@router.get("/projects/{project}/story-lab/results/{stage}")
async def get_story_lab_result(
    project: str,
    stage: StoryLabStage,
    user: dict = Depends(get_api_user),
):
    resolved = await resolve_project_scope(project, user, required_role="viewer")
    artifact = StoryLabRepository(resolved.ctx).load_artifact(stage)
    return {
        "ok": True,
        "data": artifact.model_dump(mode="json") if artifact is not None else None,
    }


@router.put("/projects/{project}/story-lab/results/{stage}")
async def save_story_lab_result(
    project: str,
    stage: StoryLabStage,
    body: StoryLabStageEditRequest,
    user: dict = Depends(get_api_user),
):
    return await save_story_lab_stage(project, stage, body, user)


@router.post("/projects/{project}/story-lab/export")
async def export_story_lab(
    project: str,
    body: StoryLabExportRequest,
    user: dict = Depends(get_api_user),
):
    resolved = await resolve_project_scope(project, user, required_role="viewer")
    export_path = _export_draft(StoryLabRepository(resolved.ctx), body.filename)
    download_url = f"/api/v1/projects/{project}/story-lab/exports/{export_path.name}"
    return {
        "ok": True,
        "data": {
            "filename": export_path.name,
            "url": download_url,
            "download_url": download_url,
        },
    }


@router.get("/projects/{project}/story-lab/exports/{filename}")
async def download_story_lab_export(
    project: str,
    filename: str,
    user: dict = Depends(get_api_user),
):
    resolved = await resolve_project_scope(project, user, required_role="viewer")
    repository = StoryLabRepository(resolved.ctx)
    safe_name = safe_story_lab_filename(filename, fallback="story.txt")
    if safe_name != filename:
        raise HTTPException(status_code=400, detail={"code": "invalid_filename"})
    path = repository.exports_dir / safe_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail={"code": "export_not_found"})
    return FileResponse(path, media_type="text/plain; charset=utf-8", filename=safe_name)


@router.post("/projects/{project}/story-lab/publish")
async def publish_story_lab(
    project: str,
    body: StoryLabPublishRequest,
    user: dict = Depends(get_api_user),
):
    """Export the current draft and enqueue the existing ingest pipeline."""
    resolved = await resolve_project_scope(project, user, required_role="editor")
    repository = StoryLabRepository(resolved.ctx)
    export_path = _export_draft(repository, body.filename)
    target_duration_seconds = repository.resolve_target_duration_seconds()

    uploads_dir = Path(resolved.ctx.output_dir) / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    novel_path = uploads_dir / export_path.name
    temp_path = uploads_dir / f".{export_path.name}.story-lab.tmp"
    try:
        shutil.copyfile(export_path, temp_path)
        temp_path.replace(novel_path)
    finally:
        temp_path.unlink(missing_ok=True)

    billable_chars = count_billable_novel_chars(load_novel_text(novel_path))
    project_updates = {"ingest_source_filename": novel_path.name}
    if target_duration_seconds is not None:
        project_updates["target_duration_total"] = target_duration_seconds
    save_project_config_in_state_dir(resolved.state_dir, config=project_updates)
    queued = await get_task_backend().enqueue_project_task(
        resolved.ctx,
        task_type="ingest_fast",
        queue_kind="default",
        episode=0,
        payload={
            "novel_path": str(novel_path),
            "config": {"rebuild": body.rebuild},
            "billing": {
                "billable_chars": billable_chars,
                "billing_quantity": billable_chars,
            },
            "source_label": "故事 Agent成稿",
        },
    )
    return {
        "ok": True,
        "data": {
            "filename": novel_path.name,
            "task_type": "ingest_fast",
            "task_id": queued.task_state.task_id,
            "task_key": project_task_state_key("ingest_fast", resolved.ctx.project_id, 0),
            "backend": queued.backend,
            "queue": queued.queue,
        },
        "message": "故事 Agent成稿已送入项目素材知识摄入队列",
    }
