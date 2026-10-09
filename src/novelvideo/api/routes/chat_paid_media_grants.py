"""Server-owned paid-media grant endpoints for Agent turns."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from novelvideo.api.auth import get_api_user
from novelvideo.chat.approval_store import (
    consume_paid_media_grant,
    register_paid_media_grant,
    resolve_active_paid_media_grant,
)
from novelvideo.chat.store import ChatScope
from novelvideo.project_context import resolve_project_context

router = APIRouter()


class PaidMediaGrantScopeIn(BaseModel):
    kind: str = "home"
    id: str | None = None
    canvas_id: str | None = None
    conversation_id: str | None = None


class ChatPaidMediaGrantIn(BaseModel):
    scope: PaidMediaGrantScopeIn
    turn_id: str
    max_starts: int


class ChatPaidMediaGrantConsumeIn(BaseModel):
    project_id: str
    canvas_id: str
    grant_id: str
    idempotency_key: str


def _require_agent_approval_scope(user: dict[str, Any], project_id: str) -> None:
    if user.get("credential_kind") != "agent_session":
        raise HTTPException(status_code=403, detail="agent session required")
    if (
        str(user.get("current_scope_kind") or "") != "project"
        or str(user.get("current_project_id") or "") != project_id
    ):
        raise HTTPException(status_code=403, detail="agent approval scope mismatch")


@router.post("/chat/paid-media-grants")
async def register_chat_paid_media_grant(
    payload: ChatPaidMediaGrantIn,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    if user.get("credential_kind") == "agent_session":
        raise HTTPException(status_code=403, detail="browser session required")
    scope = ChatScope.from_payload(payload.scope.model_dump())
    if scope.kind != "project" or not scope.id:
        raise HTTPException(status_code=400, detail="project canvas scope required")
    turn_id = payload.turn_id.strip()
    if not turn_id:
        raise HTTPException(status_code=400, detail="turn_id is required")
    await resolve_project_context(
        user=user,
        project_id=str(scope.id),
        required_role="editor",
    )
    try:
        item = await asyncio.to_thread(
            register_paid_media_grant,
            str(user["username"]),
            turn_id=turn_id,
            project_id=str(scope.id),
            canvas_id=str(scope.canvas_id or "default"),
            max_starts=payload.max_starts,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "data": {
            "grant_id": str(item["id"]),
            "turn_id": str(item["turn_id"]),
            "project_id": str(item["project_id"]),
            "canvas_id": str(item["canvas_id"]),
            "max_starts": int(item["max_starts"]),
            "used_starts": int(item["used_starts"]),
            "expires_at_ms": int(float(item["expires_at"]) * 1000),
        },
    }


@router.get("/chat/paid-media-grants/active")
async def get_active_chat_paid_media_grant(
    project_id: str,
    canvas_id: str = "default",
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Read the current server-owned grant without consuming its budget."""

    project_id = project_id.strip()
    canvas_id = canvas_id.strip() or "default"
    _require_agent_approval_scope(user, project_id)
    await resolve_project_context(user=user, project_id=project_id, required_role="editor")
    item = await asyncio.to_thread(
        resolve_active_paid_media_grant,
        str(user["username"]),
        project_id=project_id,
        canvas_id=canvas_id,
    )
    if item is None:
        return {"ok": True, "data": {"active": False}}
    max_starts = int(item.get("max_starts") or 0)
    used_starts = int(item.get("used_starts") or 0)
    return {
        "ok": True,
        "data": {
            "active": True,
            "grant_id": str(item.get("id") or ""),
            "turn_id": str(item.get("turn_id") or ""),
            "project_id": str(item.get("project_id") or ""),
            "canvas_id": str(item.get("canvas_id") or ""),
            "max_starts": max_starts,
            "used_starts": used_starts,
            "remaining_starts": max(0, max_starts - used_starts),
            "expires_at_ms": int(float(item.get("expires_at") or 0) * 1000),
        },
    }


@router.post("/chat/paid-media-grants/consume")
async def consume_chat_paid_media_grant(
    payload: ChatPaidMediaGrantConsumeIn,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    project_id = payload.project_id.strip()
    canvas_id = payload.canvas_id.strip() or "default"
    _require_agent_approval_scope(user, project_id)
    await resolve_project_context(user=user, project_id=project_id, required_role="editor")
    grant_id = payload.grant_id.strip()
    if not grant_id:
        active = await asyncio.to_thread(
            resolve_active_paid_media_grant,
            str(user["username"]),
            project_id=project_id,
            canvas_id=canvas_id,
        )
        grant_id = str((active or {}).get("id") or "").strip()
    allowed, reason, item = await asyncio.to_thread(
        consume_paid_media_grant,
        str(user["username"]),
        grant_id=grant_id,
        project_id=project_id,
        canvas_id=canvas_id,
        idempotency_key=payload.idempotency_key.strip(),
    )
    return {
        "ok": True,
        "data": {
            "allowed": allowed,
            "reason": reason,
            "used_starts": int(item.get("used_starts") or 0) if item else 0,
            "max_starts": int(item.get("max_starts") or 0) if item else 0,
        },
    }
