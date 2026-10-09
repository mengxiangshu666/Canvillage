"""风格管理端点。"""

import json
import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

logger = logging.getLogger("novelvideo.api.styles")

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import resolve_project_scope
from novelvideo.api.schemas import StylePreviewRequest

router = APIRouter()


async def _selected_style_has_active_run(resolved, style_id: str) -> bool:
    """Keep a run's custom-style definition immutable until the run ends."""

    config_path = Path(resolved.state_dir) / "project_config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    if str(config.get("visual_style") or "").strip() != style_id:
        return False
    from novelvideo.production.control_store import ProductionControlStore

    active_run = await ProductionControlStore(resolved.state_dir).latest()
    return bool(
        active_run
        and active_run.get("status") in {"running", "pausing", "paused", "blocked"}
    )


def _custom_preview_url(project: str, project_dir, style_id: str) -> str | None:
    from novelvideo.services.style_service import StyleService

    preview_path = StyleService.find_style_preview(project_dir, style_id)
    if preview_path is None:
        return None
    relative = preview_path.relative_to(project_dir.resolve()).as_posix()
    return (
        f"/api/v1/projects/{quote(str(project), safe='')}/media/"
        f"{quote(relative, safe='/')}"
    )


def _style_payload(style, *, project: str | None = None, project_dir=None) -> dict:
    payload = (
        style.model_dump() if hasattr(style, "model_dump") else dict(style.__dict__)
    )
    if project and project_dir and not payload.get("is_preset"):
        preview_url = _custom_preview_url(
            project, project_dir, str(payload.get("id") or "")
        )
        if preview_url:
            payload["preview_url"] = preview_url
    return payload


@router.get("/styles")
async def list_styles(
    project: str | None = Query(
        None, description="项目名；提供时返回该项目的自定义风格"
    ),
    user: dict = Depends(get_api_user),
):
    """列出所有风格（预设 + 自定义）。"""
    from novelvideo.services.style_service import StyleService

    username = user["username"]
    project_name = project
    project_dir = None
    if project:
        resolved = await resolve_project_scope(project, user, required_role="viewer")
        username = resolved.username
        project_name = resolved.project_name
        project_dir = resolved.project_dir
    styles = StyleService.list_all_styles(
        username=username,
        project=project_name,
        project_dir=project_dir,
    )
    if project and project_dir:
        for style in styles:
            if style.get("type") == "custom":
                preview_url = _custom_preview_url(
                    project, project_dir, str(style.get("id") or "")
                )
                if preview_url:
                    style["preview_url"] = preview_url
    return {"ok": True, "data": styles}


@router.get("/styles/{style_id}")
async def get_style(
    style_id: str,
    project: str | None = Query(None, description="项目名"),
    user: dict = Depends(get_api_user),
):
    """获取风格详情。"""
    from novelvideo.services.style_service import StyleService

    username = user["username"]
    project_name = project
    project_dir = None
    if project:
        resolved = await resolve_project_scope(project, user, required_role="viewer")
        username = resolved.username
        project_name = resolved.project_name
        project_dir = resolved.project_dir
    style = StyleService.get_style(
        style_id,
        username=username,
        project=project_name,
        project_dir=project_dir,
    )
    if style is None:
        return {"ok": False, "error": f"Style '{style_id}' not found"}

    return {
        "ok": True,
        "data": _style_payload(style, project=project, project_dir=project_dir),
    }


@router.get("/styles/{style_id}/preview")
async def get_style_preview(
    style_id: str,
    project: str | None = Query(None, description="项目名"),
    user: dict = Depends(get_api_user),
):
    """返回预设风格的参考预览图。"""
    from novelvideo.services.style_service import StyleService

    username = user["username"]
    project_name = project
    project_dir = None
    if project:
        resolved = await resolve_project_scope(project, user, required_role="viewer")
        username = resolved.username
        project_name = resolved.project_name
        project_dir = resolved.project_dir

    if not StyleService.get_preset(style_id):
        if StyleService.get_style(
            style_id,
            username=username,
            project=project_name,
            project_dir=project_dir,
        ):
            if not project_dir:
                return {
                    "ok": False,
                    "error": "Project is required for custom style previews",
                }
            custom_path = StyleService.find_style_preview(project_dir, style_id)
            if custom_path is None:
                return {"ok": False, "error": f"自定义风格 '{style_id}' 暂无参考图"}
            media_types = {
                ".webp": "image/webp",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
            }
            return FileResponse(
                path=str(custom_path),
                media_type=media_types.get(
                    custom_path.suffix.lower(), "application/octet-stream"
                ),
                filename=custom_path.name,
                headers={"Cache-Control": "public, max-age=3600"},
            )
        return {"ok": False, "error": f"Style '{style_id}' not found"}

    preview_path = next(
        (
            StyleService.PRESETS_DIR / f"{style_id}{suffix}"
            for suffix in (".webp", ".png", ".jpg", ".jpeg")
            if (StyleService.PRESETS_DIR / f"{style_id}{suffix}").exists()
        ),
        None,
    )
    if preview_path is None:
        return {"ok": False, "error": f"预设风格 '{style_id}' 暂无参考图"}

    media_types = {
        ".webp": "image/webp",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
    }

    return FileResponse(
        path=str(preview_path),
        media_type=media_types[preview_path.suffix.lower()],
        filename=f"preview_{style_id}{preview_path.suffix.lower()}",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


@router.post("/styles")
async def create_style(body: dict, user: dict = Depends(get_api_user)):
    """创建自定义风格。"""
    from novelvideo.services.style_service import StyleService
    from novelvideo.models import StyleConfig

    style_id = body.get("id")
    project = body.get("project")
    if not style_id:
        return {"ok": False, "error": "Style id is required"}
    if not project:
        return {"ok": False, "error": "Project is required"}
    resolved = await resolve_project_scope(project, user, required_role="editor")

    if await _selected_style_has_active_run(resolved, str(style_id)):
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error": "总控运行期间项目风格已锁定；请结束当前 Run 后再修改该风格。",
            },
        )

    # 检查是否与预设冲突
    if StyleService.get_preset(style_id):
        return {"ok": False, "error": f"Cannot override preset style '{style_id}'"}

    preview_token = str(body.get("preview_token") or "").strip() or None
    finalized_preview: str | None = None
    try:
        config_payload = dict(body.get("config", {}) or {})
        config_payload["id"] = style_id
        config_payload["name"] = (
            body.get("name") or config_payload.get("name") or style_id
        )
        if preview_token:
            finalized_preview = StyleService.finalize_style_preview(
                resolved.project_dir,
                style_id,
                preview_token,
            )
            config_payload["preview_path"] = finalized_preview
        elif config_payload.get("preview_path"):
            config_payload["preview_path"] = StyleService.validate_style_preview_path(
                resolved.project_dir,
                style_id,
                str(config_payload["preview_path"]),
            )
        else:
            existing_preview = StyleService.find_style_reference(
                resolved.project_dir, style_id
            )
            if existing_preview:
                config_payload["preview_path"] = existing_preview
        config = StyleConfig(**config_payload)
        success = StyleService.save_custom_style(
            style_id,
            config,
            username=resolved.username,
            project=resolved.project_name,
            project_dir=resolved.project_dir,
        )
        if not success:
            if finalized_preview:
                StyleService.remove_style_previews(resolved.project_dir, style_id)
            return {"ok": False, "error": "保存自定义风格失败"}
    except Exception as e:
        if finalized_preview:
            StyleService.remove_style_previews(resolved.project_dir, style_id)
        return {"ok": False, "error": str(e)}

    return {"ok": True, "data": {"id": style_id, "message": "风格已创建"}}


@router.delete("/styles/{style_id}")
async def delete_style(
    style_id: str,
    project: str | None = Query(None, description="项目名"),
    user: dict = Depends(get_api_user),
):
    """删除自定义风格。"""
    from novelvideo.services.style_service import StyleService

    # 不允许删除预设
    if StyleService.get_preset(style_id):
        return {"ok": False, "error": "Cannot delete preset styles"}

    if not project:
        return {"ok": False, "error": "Project is required"}
    resolved = await resolve_project_scope(project, user, required_role="editor")
    from novelvideo.project_config import load_project_config_file

    current_config = load_project_config_file(resolved.username, resolved.project_name)
    if str(current_config.get("visual_style") or "").strip() == style_id:
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error": "该风格正在作为项目锁定风格使用；请先切回按剧本自动定向。",
            },
        )
    success = StyleService.delete_custom_style(
        style_id,
        username=resolved.username,
        project=resolved.project_name,
        project_dir=resolved.project_dir,
    )
    if not success:
        return {"ok": False, "error": f"Custom style '{style_id}' not found"}

    return {"ok": True, "data": {"id": style_id, "message": "风格已删除"}}


@router.post("/projects/{project}/styles/preview-upload")
async def upload_style_preview(
    project: str,
    file: UploadFile = File(...),
    style_id: str = Form(...),
    user: dict = Depends(get_api_user),
):
    """Stage a validated custom style reference image for a later create."""
    resolved = await resolve_project_scope(project, user, required_role="editor")
    from novelvideo.services.style_service import StyleService

    try:
        token = StyleService.stage_style_preview(
            resolved.project_dir,
            style_id.strip(),
            file.filename or "preview.png",
            await file.read(),
            file.content_type,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "data": {"preview_token": token}}


@router.delete("/projects/{project}/styles/preview-upload")
async def discard_style_preview(
    project: str,
    style_id: str = Query(...),
    preview_token: str = Query(...),
    user: dict = Depends(get_api_user),
):
    """Discard a staged upload after cancel or failed analysis."""
    resolved = await resolve_project_scope(project, user, required_role="editor")
    from novelvideo.services.style_service import StyleService

    try:
        StyleService.discard_staged_style_preview(
            resolved.project_dir,
            style_id.strip(),
            preview_token,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "data": {"discarded": True}}


@router.post("/styles/{style_id}/preview")
async def preview_style(
    style_id: str, body: StylePreviewRequest, user: dict = Depends(get_api_user)
):
    """使用指定风格生成预览图。"""
    from novelvideo.services.style_service import StyleService

    username = user["username"]
    project_name = body.project
    if body.project:
        resolved = await resolve_project_scope(
            body.project, user, required_role="viewer"
        )
        username = resolved.username
        project_name = resolved.project_name
    style = StyleService.get_style(
        style_id,
        username=username,
        project=project_name,
    )
    if style is None:
        return {"ok": False, "error": f"Style '{style_id}' not found"}

    try:
        from novelvideo.config import (
            get_grid_generation_config,
            normalize_explicit_image_generation_selection,
        )
        from novelvideo.generators.direct_image_models import (
            bind_direct_image_generator_config,
            resolve_direct_image_model,
        )
        from novelvideo.generators.nanobanana_grid import create_grid_generator

        direct_model = resolve_direct_image_model(body.model or None)
        if direct_model is not None:
            generator_config = bind_direct_image_generator_config(
                direct_model,
                get_grid_generation_config(selection_override=""),
            )
        else:
            selection = normalize_explicit_image_generation_selection(body.model)
            generator_config = get_grid_generation_config(
                selection_override=selection
            )
        generator = create_grid_generator(config=generator_config)
        image_bytes = await generator.generate_single_preview(
            prompt=body.prompt,
            style_config=style.to_legacy_dict(),
        )
    except Exception as e:
        return {"ok": False, "error": f"Preview generation failed: {e}"}

    if not image_bytes:
        return {"ok": False, "error": "No preview image generated"}

    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="preview_{style_id}.png"'},
    )


@router.post("/projects/{project}/styles/analyze")
async def analyze_style(
    project: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_api_user),
):
    """上传参考图片，AI 分析并提取风格参数。"""
    resolved = await resolve_project_scope(project, user, required_role="editor")
    from novelvideo.generators.style_analyzer import StyleAnalyzer
    from novelvideo.ports import get_usage_meter

    content = await file.read()
    if not content:
        return {"ok": False, "error": "No file uploaded"}

    mime_type = file.content_type or "image/jpeg"

    try:
        if resolved.ctx is not None:
            billing_user_id = (
                str(getattr(resolved.ctx, "requester_user_id", "") or "").strip()
                or str(getattr(resolved.ctx, "owner_id", "") or "").strip()
            )
            get_usage_meter().set_llm_usage_context(
                billing_user_id,
                project_id=resolved.ctx.project_id,
                resource_kind="script",
                billing_metadata={
                    "billing_user_id": billing_user_id,
                    "requester_user_id": str(
                        getattr(resolved.ctx, "requester_user_id", "") or ""
                    ).strip(),
                    "project_owner_id": str(
                        getattr(resolved.ctx, "owner_id", "") or ""
                    ).strip(),
                    "source": "style_analyzer",
                },
            )
        analyzer = StyleAnalyzer()
        result = await analyzer.analyze(content, mime_type=mime_type)
    except Exception as e:
        return {"ok": False, "error": f"Style analysis failed: {e}"}
    finally:
        get_usage_meter().clear_llm_usage_context()

    return {"ok": True, "data": result}
