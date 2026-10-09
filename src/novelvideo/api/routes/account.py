"""Account-scoped endpoints: the signed-in user's own avatar.

The SPA reads, uploads and clears an avatar through these three routes.  The
storage and validation rules live in :mod:`novelvideo.account_avatar`; this
module only binds them to the authenticated session and the HTTP contract the
frontend already speaks (``{ok, data: {avatar_url}}``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from novelvideo.account_avatar import (
    AvatarError,
    MAX_AVATAR_BYTES,
    avatar_url,
    delete_avatar,
    read_avatar,
    resolve_static_avatar,
    save_avatar,
)
from novelvideo.api.auth import get_api_user
from novelvideo.config import STATE_DIR

router = APIRouter()

# The body limit middleware already caps this route at 5 MB; stop one decode
# earlier so an oversized upload is a clear 400 instead of a 413 from a layer
# the UI cannot explain.
_READ_LIMIT_BYTES = MAX_AVATAR_BYTES + 1


def _username(user: dict) -> str:
    username = str(user.get("username") or "").strip()
    if not username:
        raise HTTPException(status_code=401, detail="authenticated user required")
    return username


def _state_root() -> Path:
    return Path(STATE_DIR)


@router.get("/account/avatar")
async def read_account_avatar(user: dict = Depends(get_api_user)) -> dict[str, Any]:
    """Return the signed-in account's avatar URL, or ``null`` when unset."""

    username = _username(user)
    record = await asyncio.to_thread(
        read_avatar, username, state_root=_state_root()
    )
    return {
        "ok": True,
        "data": {
            "avatar_url": avatar_url(username, record) if record is not None else None
        },
    }


@router.post("/account/avatar")
async def upload_account_avatar(
    file: UploadFile = File(...),
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Store one avatar for the signed-in account, replacing any previous one."""

    username = _username(user)
    payload = await file.read(_READ_LIMIT_BYTES)
    try:
        record = await asyncio.to_thread(
            save_avatar,
            username,
            payload,
            state_root=_state_root(),
            declared_type=file.content_type or "",
        )
    except AvatarError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "data": {"avatar_url": avatar_url(username, record)},
    }


@router.delete("/account/avatar")
async def remove_account_avatar(user: dict = Depends(get_api_user)) -> dict[str, Any]:
    """Clear the avatar so the account falls back to its initial."""

    username = _username(user)
    removed = await asyncio.to_thread(
        delete_avatar, username, state_root=_state_root()
    )
    return {"ok": True, "data": {"avatar_url": None, "removed": removed}}


async def account_avatar_file_response(
    username: str,
    filename: str,
    user: dict,
) -> FileResponse:
    """Serve ``/static/avatars/<user>/<file>`` for the signed-in session.

    Lives here rather than in ``app.py`` so it sits with the rest of the avatar
    contract; the app registers the route, matching how project media is served.
    """

    try:
        record = await asyncio.to_thread(
            resolve_static_avatar,
            username,
            filename,
            session_username=_username(user),
            state_root=_state_root(),
        )
    except AvatarError as exc:
        # Cross-account probes and unknown names answer the same way, so the
        # route never confirms whether another account's avatar exists.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path=str(record.path),
        media_type=record.content_type,
        headers={
            "Cache-Control": "private, max-age=300, must-revalidate",
            "ETag": f'"{record.version}"',
        },
    )
