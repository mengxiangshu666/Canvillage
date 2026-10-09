"""技能蒸馏路由 —— 内置到 Village Infinite Canvas API（单进程单端口）。

端点：
  POST   /api/v1/skills/traces          提交录制事件 → 返回 trace_id
  POST   /api/v1/skills/distill/{id}    触发蒸馏 → 技能安装到村长 Agent
  GET    /api/v1/skills/traces          列出 traces
  GET    /api/v1/skills                 列出已安装技能
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from novelvideo.skills_distill import pipeline
from novelvideo.skills_distill.install import list_installed
from novelvideo.skills_distill import store as skill_store

logger = logging.getLogger("novelvideo.api.routes.skills")

router = APIRouter(prefix="/skills", tags=["skills"])


class TraceEvent(BaseModel):
    kind: str = "action"
    url: str = ""
    timestamp: int = 0
    action: dict = Field(default_factory=dict)
    annotation: dict = Field(default_factory=dict)


class TraceSubmit(BaseModel):
    label: str = ""
    description: str = ""
    events: list[TraceEvent]


@router.post("/traces")
def submit_trace(body: TraceSubmit):
    if not body.events:
        raise HTTPException(400, "events required")
    events = [e.model_dump() for e in body.events]
    trace_id = pipeline.save_trace(body.label, body.description, events)
    return {"trace_id": trace_id, "events": len(events)}


@router.post("/distill/{trace_id}")
def distill(trace_id: str):
    try:
        result = pipeline.run_distill(trace_id)
    except FileNotFoundError:
        raise HTTPException(404, f"trace not found: {trace_id}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("distill failed")
        raise HTTPException(500, f"distill failed: {e}")
    return result


@router.get("/traces")
def traces():
    return {"traces": pipeline.list_traces()}


@router.get("/store")
def store_catalog(
    query: str = Query(default="", max_length=200),
    source: str = Query(default="", max_length=80),
    category: str = Query(default="", max_length=80),
    installed: bool | None = Query(default=None),
):
    items = skill_store.list_store_items(
        query=query,
        source=source,
        category=category,
        installed=installed,
    )
    all_items = skill_store.list_store_items()
    return {
        "items": items,
        "total": len(all_items),
        "installed": sum(1 for item in all_items if item["installed"]),
        "custom": sum(1 for item in all_items if item["source"] == "custom"),
        "categories": sorted({str(item["category"]) for item in all_items}),
    }


@router.post("/store/import")
async def import_store_skill(file: UploadFile = File(...)):
    try:
        data = await file.read(skill_store.MAX_UPLOAD_BYTES + 1)
        items = skill_store.import_skill_file(file.filename or "skill", data)
    except skill_store.SkillStoreError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"items": items, "imported": len(items)}


@router.get("/store/{skill_id}")
def store_detail(skill_id: str):
    try:
        return skill_store.get_store_item(skill_id)
    except KeyError as exc:
        raise HTTPException(404, "skill not found") from exc


@router.post("/store/{skill_id}/install")
def install_store_skill(skill_id: str):
    try:
        return skill_store.install_store_item(skill_id)
    except KeyError as exc:
        raise HTTPException(404, "skill not found") from exc


@router.delete("/store/{skill_id}/install")
def uninstall_store_skill(skill_id: str):
    try:
        return skill_store.uninstall_store_item(skill_id)
    except KeyError as exc:
        raise HTTPException(404, "skill not found") from exc


@router.delete("/store/{skill_id}")
def delete_store_skill(skill_id: str):
    try:
        deleted = skill_store.delete_custom_item(skill_id)
    except KeyError as exc:
        raise HTTPException(404, "skill not found") from exc
    except skill_store.SkillStoreError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"deleted": deleted, "skill_id": skill_id}


@router.get("")
def skills():
    return {"skills": list_installed()}
