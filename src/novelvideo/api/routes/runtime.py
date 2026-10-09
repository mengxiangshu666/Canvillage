"""Local runtime lifecycle endpoints used by the bundled browser UI."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request

from novelvideo.api.auth import get_api_user
from novelvideo.api.browser_presence import BrowserPresenceTracker

router = APIRouter(prefix="/runtime")

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def _tracker(request: Request) -> BrowserPresenceTracker:
    tracker = getattr(request.app.state, "browser_presence", None)
    if not isinstance(tracker, BrowserPresenceTracker):
        raise HTTPException(status_code=404, detail="local browser lifecycle is disabled")
    return tracker


def _status_payload(status) -> dict[str, object]:
    return {
        "sessionCount": status.session_count,
        "armed": status.armed,
        "idleSeconds": status.idle_seconds,
    }


@router.post("/browser-sessions/{session_id}/heartbeat")
async def heartbeat_browser_session(
    session_id: str,
    request: Request,
    user: dict = Depends(get_api_user),
) -> dict[str, object]:
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=422, detail="invalid browser session id")
    username = str(user.get("username") or user.get("id") or "local").strip() or "local"
    status = _tracker(request).heartbeat(session_id=session_id, username=username)
    return {"ok": True, "data": _status_payload(status)}


@router.post("/browser-sessions/{session_id}/leave")
async def leave_browser_session(
    session_id: str,
    request: Request,
    _user: dict = Depends(get_api_user),
) -> dict[str, object]:
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=422, detail="invalid browser session id")
    status = _tracker(request).leave(session_id=session_id)
    return {"ok": True, "data": _status_payload(status)}
