"""Project-scoped API for the user's personal canvas template library."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from novelvideo import config
from novelvideo.api.auth import get_api_user
from novelvideo.freezone.canvas_template_store import (
    CanvasTemplateError,
    CanvasTemplateStore,
)
from novelvideo.project_context import (
    ProjectContext,
    require_project_home_node,
    resolve_project_context,
)

router = APIRouter()
TAG_CANVAS_TEMPLATES = "freezone-canvas"


class CanvasTemplateCreateRequest(BaseModel):
    title: str
    description: str = ""
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]] = Field(default_factory=list)


def _template_db_path() -> Path:
    return Path(config.STATE_DIR) / "local" / "canvas_templates.db"


def _store() -> CanvasTemplateStore:
    return CanvasTemplateStore(_template_db_path())


async def _scope(project: str, user: dict) -> ProjectContext:
    ctx = await resolve_project_context(
        user=user,
        project_id=project,
        required_role="editor",
    )
    return require_project_home_node(ctx, operation="canvas template access")


def _http_error(exc: CanvasTemplateError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "code": exc.code,
            "message": str(exc),
        },
    )


@router.get(
    "/projects/{project}/freezone/canvas-templates",
    tags=[TAG_CANVAS_TEMPLATES],
)
async def list_canvas_templates(project: str, user: dict = Depends(get_api_user)):
    ctx = await _scope(project, user)
    return {
        "ok": True,
        "data": {
            "items": _store().list_for_owner(ctx.requester_user_id),
        },
    }


@router.get(
    "/projects/{project}/freezone/canvas-templates/{template_id}",
    tags=[TAG_CANVAS_TEMPLATES],
)
async def get_canvas_template(
    project: str,
    template_id: str,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user)
    template = _store().get(ctx.requester_user_id, template_id)
    if template is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "canvas_template_not_found",
                "message": "画布模板不存在",
            },
        )
    return {"ok": True, "data": template}


@router.post(
    "/projects/{project}/freezone/canvas-templates",
    status_code=status.HTTP_201_CREATED,
    tags=[TAG_CANVAS_TEMPLATES],
)
async def create_canvas_template(
    project: str,
    payload: CanvasTemplateCreateRequest,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user)
    try:
        template = _store().create(
            owner_id=ctx.requester_user_id,
            title=payload.title,
            description=payload.description,
            nodes=payload.nodes,
            edges=payload.edges,
            source_project_id=ctx.project_id,
            source_canvas_id="",
        )
    except CanvasTemplateError as exc:
        raise _http_error(exc) from exc
    return {"ok": True, "data": template}


@router.delete(
    "/projects/{project}/freezone/canvas-templates/{template_id}",
    tags=[TAG_CANVAS_TEMPLATES],
)
async def delete_canvas_template(
    project: str,
    template_id: str,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user)
    deleted = _store().delete(ctx.requester_user_id, template_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "canvas_template_not_found",
                "message": "画布模板不存在",
            },
        )
    return {
        "ok": True,
        "data": {
            "id": template_id,
            "deleted": True,
        },
    }


__all__ = ["router"]
