"""Optional TapCanvas agent sidecar HTTP seam.

The sidecar is the agent brain (DeepSeek Harness + skills + delivery
contracts).  This module is the only HTTP door back into the product: a
per-turn MCP endpoint exposing a bounded native tool list, and a turn endpoint
that binds project/canvas credentials to that door.  Canvas, task and workflow
authorities remain the existing Village services.

The actual turn runner and context store live in
``novelvideo.chat.tapcanvas_sidecar`` so the chat adapter can use the same
binding without importing the API layer.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from novelvideo.api.auth import get_api_user
from novelvideo.chat import tapcanvas_sidecar
from novelvideo.project_context import (
    require_project_home_node,
    resolve_project_context,
)


router = APIRouter()


def _jsonrpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


async def _execute_tapcanvas_remote_tool(
    body: Mapping[str, Any], context: Mapping[str, Any]
) -> JSONResponse:
    """Handle TapCanvas ``remoteTools`` execution.

    The sidecar's request-side MCP gateway forwards remote tool calls as a
    plain ``{toolName, args, ...}`` POST, not JSON-RPC.  It treats any 2xx as
    success, so a failed native tool must be surfaced as a non-2xx response.
    Project/canvas binding comes only from the per-turn context, never from
    the request body.
    """

    name = str(body.get("toolName") or "").strip()
    raw_arguments = body.get("args")
    arguments = raw_arguments if isinstance(raw_arguments, Mapping) else {}
    try:
        result = await tapcanvas_sidecar.invoke_native_tool(name, arguments, context)
    except Exception as exc:  # noqa: BLE001 - the sidecar needs a readable failure
        message = f"{type(exc).__name__}: {exc}"
        return JSONResponse(
            status_code=400,
            content={"ok": False, "content": message, "error": message},
        )
    text, failed = tapcanvas_sidecar.tool_result_text(result)
    if failed:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "content": text, "error": text},
        )
    payload: dict[str, Any] = {"ok": True, "content": text}
    if not isinstance(result, str):
        payload["result"] = result
    return JSONResponse(status_code=200, content=payload)


@router.get("/agent-sidecar/health", tags=["agent-sidecar"])
async def agent_sidecar_health() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{tapcanvas_sidecar.sidecar_url()}/health")
        payload = (
            response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        return {"ok": response.status_code == 200, "sidecar": payload}
    except Exception as exc:  # noqa: BLE001 - health is diagnostic
        return {"ok": False, "error": type(exc).__name__}


@router.post("/agent-sidecar/mcp/{turn_token}", tags=["agent-sidecar"])
async def agent_sidecar_mcp(turn_token: str, request: Request) -> Any:
    """Minimal MCP endpoint used only by the sidecar for one turn.

    Supports both JSON-RPC (standard MCP clients) and TapCanvas's plain
    ``{toolName, args}`` remote-tool request.
    """

    context = tapcanvas_sidecar.get_context(turn_token)
    if context is None:
        raise HTTPException(status_code=401, detail="unknown or expired sidecar turn")
    body = await request.json()
    if isinstance(body, Mapping) and isinstance(body.get("toolName"), str):
        return await _execute_tapcanvas_remote_tool(body, context)
    method = str(body.get("method") or "")
    request_id = body.get("id")
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return Response(status_code=202)
    if method == "initialize":
        return _jsonrpc_result(
            request_id,
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "village-canvas-sidecar", "version": "1.0.0"},
            },
        )
    if method == "tools/list":
        return _jsonrpc_result(request_id, {"tools": tapcanvas_sidecar.mcp_tools()})
    if method == "tools/call":
        params = body.get("params") if isinstance(body.get("params"), Mapping) else {}
        name = str(params.get("name") or "")
        arguments = (
            params.get("arguments")
            if isinstance(params.get("arguments"), Mapping)
            else {}
        )
        try:
            result = await tapcanvas_sidecar.invoke_native_tool(
                name, arguments, context
            )
        except Exception as exc:  # noqa: BLE001 - MCP must return a tool error
            return _jsonrpc_result(
                request_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "ok": False,
                                    "error": f"{type(exc).__name__}: {exc}",
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ],
                    "isError": True,
                },
            )
        text, failed = tapcanvas_sidecar.tool_result_text(result)
        return _jsonrpc_result(
            request_id,
            {
                "content": [{"type": "text", "text": text}],
                **({"isError": True} if failed else {}),
            },
        )
    return _jsonrpc_error(request_id, -32601, f"method not found: {method}")


class SidecarTurnBody(BaseModel):
    project_id: str = Field(min_length=1, max_length=256)
    canvas_id: str = Field(default="default", min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=20_000)
    conversation_id: str = Field(default="default", max_length=200)
    model: str | None = None


@router.post("/agent-sidecar/turn", tags=["agent-sidecar"])
async def agent_sidecar_turn(
    body: SidecarTurnBody,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Run one real sidecar turn for the current project/canvas.

    This is the service-level seam used before a feature-flagged chat cutover.
    It creates a short-lived agent token and MCP context, then calls the
    sidecar over loopback.  No provider key is returned to the caller.
    """

    ctx = await resolve_project_context(
        user=user,
        project_id=body.project_id,
        required_role="editor",
    )
    require_project_home_node(ctx, operation="run TapCanvas sidecar")
    username = str(user.get("username") or user.get("sub") or "local")
    try:
        result = await tapcanvas_sidecar.run_turn(
            username=username,
            project_id=ctx.project_id,
            canvas_id=body.canvas_id,
            prompt=body.prompt,
            conversation_id=body.conversation_id,
            model=body.model,
        )
    except tapcanvas_sidecar.SidecarError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, **result}


__all__ = ["router"]
