"""Read-only discovery surface for the village-canvas capability cards.

External agents (portable CLI, editor agents) cannot enter the Python tool
layer directly; this route publishes the same assembled card index the
built-in agent searches, so they can discover what the canvas can do. The
cards carry ids, purposes, argument contracts and side-effect metadata only —
never credentials.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from novelvideo.agent_tools.village_canvas.capability_broker import (
    public_capability_index,
)
from novelvideo.api.auth import get_api_user
from novelvideo.api.schemas import OkResponse

router = APIRouter()


def _matches(card: dict[str, Any], query: str) -> bool:
    haystack = " ".join(
        str(card.get(key) or "")
        for key in ("id", "purpose", "domain", "side_effect")
    ).casefold()
    return all(token in haystack for token in query.casefold().split())


@router.get("/village-canvas/capabilities", response_model=OkResponse)
async def list_village_canvas_capabilities(
    query: str | None = Query(default=None, description="按 id/用途/域 过滤"),
    capability_id: str | None = Query(default=None, alias="id", description="精确取一张卡"),
    _user: dict = Depends(get_api_user),
) -> OkResponse:
    cards = public_capability_index()
    if capability_id:
        wanted = str(capability_id).strip()
        cards = tuple(card for card in cards if card.get("id") == wanted)
        if not cards:
            return OkResponse(data={"count": 0, "capabilities": []})
    elif query:
        cards = tuple(card for card in cards if _matches(card, str(query)))
    return OkResponse(data={"count": len(cards), "capabilities": list(cards)})
