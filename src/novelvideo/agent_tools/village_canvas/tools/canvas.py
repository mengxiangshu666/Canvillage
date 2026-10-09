"""Canvas state reads and receipt capabilities.

These declarations cover both faces the agent sees for a capability: the
broker card used for discovery and the direct tool schema used when the tool is
already selected. The text and schemas are copied verbatim from the source parts
they replaced and held in place by ``check_agent_tool_surface.py``.
"""

from __future__ import annotations

from .spec import ToolSpec


_PROJECT_ID = {
    "type": "string",
    "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
}
_CANVAS_ID = {
    "type": "string",
    "description": "Current canvas id from CURRENT_CANVAS_CONTEXT.",
}


CANVAS_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="canvas.snapshot",
        handler_name="_handle_get_canvas_snapshot",
        card={
            "id": "canvas.snapshot",
            "domain": "canvas",
            "purpose": (
                "按节点页或指定节点读取权威画布快照与连线；已知目标节点时必须传 "
                "node_ids=[目标节点 id]，不要扫描整张画布。"
            ),
            "required_args": ["canvas_id"],
            "optional_args": [
                "project_id",
                "node_cursor",
                "node_limit",
                "node_ids",
                "include_edges",
            ],
            "cost": "free",
            "side_effect": "read",
            "search_terms": "画布 节点 连线 快照 snapshot graph",
        },
        tool_name="freezone_get_canvas_snapshot",
        description=(
            "read-only: read one bounded page of persisted canvas nodes and "
            "connected edges. viewport/revision/page metadata are returned before "
            "node data, including truncated, omitted_nodes and next_cursor. Use "
            "only for node facts, never for viewport placement."
        ),
        properties={
            "project_id": _PROJECT_ID,
            "canvas_id": _CANVAS_ID,
            "node_cursor": {"type": "integer", "minimum": 0, "default": 0},
            "node_limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 20,
            },
            "node_ids": {
                "type": "array",
                "maxItems": 50,
                "items": {"type": "string"},
            },
            "include_edges": {"type": "boolean", "default": True},
        },
        required=("canvas_id",),
    ),
    ToolSpec(
        id="canvas.viewport",
        handler_name="_handle_get_canvas_viewport",
        card={
            "id": "canvas.viewport",
            "domain": "canvas",
            "purpose": "读取持久化视口、revision 与节点/连线数量。",
            "required_args": ["canvas_id"],
            "cost": "free",
            "side_effect": "read",
            "search_terms": "画布 视口 中心 缩放 viewport revision",
        },
        tool_name="freezone_get_canvas_viewport",
        description=(
            "Read only the persisted canvas viewport, revision and graph counts. "
            "Prefer CURRENT_CANVAS_CONTEXT.viewport_context for live placement; "
            "this tool is a lightweight persisted fallback."
        ),
        properties={
            "project_id": _PROJECT_ID,
            "canvas_id": _CANVAS_ID,
        },
        required=("canvas_id",),
    ),
    ToolSpec(
        id="canvas.wait_receipt",
        handler_name="_handle_wait_canvas_receipt",
        card={
            "id": "canvas.wait_receipt",
            "domain": "canvas",
            "purpose": "等待并读取一个画布命令的权威回执。",
            "required_args": ["canvas_id", "command_id"],
            "cost": "free",
            "side_effect": "read",
            "search_terms": "回执 等待 revision receipt verify",
        },
        tool_name="village_canvas_wait_receipt",
        description=(
            "Wait up to 10 seconds for a persisted canvas command receipt. Use "
            "after an incomplete or asynchronous command result; returns revision "
            "and created node ids without loading chat history."
        ),
        properties={
            "project_id": _PROJECT_ID,
            "canvas_id": {
                "type": "string",
                "description": "Current canvas id.",
            },
            "command_id": {
                "type": "string",
                "description": "Idempotent command id returned by apply_commands.",
            },
            "timeout_ms": {
                "type": "integer",
                "minimum": 0,
                "maximum": 10000,
                "default": 3000,
            },
            "poll_interval_ms": {
                "type": "integer",
                "minimum": 50,
                "maximum": 1000,
                "default": 100,
            },
        },
        required=("canvas_id", "command_id"),
    ),
    ToolSpec(
        id="media.readiness.revalidate",
        handler_name="_handle_get_script_media_readiness",
        card={
            "id": "media.readiness.revalidate",
            "domain": "media",
            "purpose": (
                "按当前画布 revision 与脚本行事实只读复验一个分镜图或逐镜视频节点，"
                "不写画布、不启动任务；未 ready 时返回结构化恢复合同。"
            ),
            "required_args": ["canvas_id", "node_id", "action"],
            "optional_args": ["step_id"],
            "cost": "free",
            "side_effect": "read",
            "search_terms": "脚本 付费媒体 门禁 恢复 复验 stale 分镜 视频 readiness revalidate",
        },
        tool_name="village_canvas_get_script_media_readiness",
        description=(
            "Read-only: revalidate one script-derived storyboard image or shot "
            "video against the current canvas revision, current script row, asset "
            "snapshots and storyboard binding. Never writes the canvas or starts "
            "media. Use this for recovery before asking for a new paid action; "
            "when ready=false, follow the returned recovery contract."
        ),
        properties={
            "project_id": _PROJECT_ID,
            "canvas_id": _CANVAS_ID,
            "node_id": {
                "type": "string",
                "description": "Exact target node from the recovery contract.",
            },
            "action": {
                "type": "string",
                "enum": ["storyboard-images", "shot-videos"],
                "description": "Media action from the recovery contract.",
            },
            "step_id": {
                "type": "string",
                "description": (
                    "Optional workflow step id; when supplied, a not-ready result "
                    "includes the next structured recovery contract."
                ),
            },
        },
        required=("canvas_id", "node_id", "action"),
    ),
)
