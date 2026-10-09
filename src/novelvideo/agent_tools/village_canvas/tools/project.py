"""Task, pipeline, and production-control capabilities.

The cards are frozen verbatim by ``check_agent_tool_surface.py``.
"""

from __future__ import annotations

from .spec import ToolSpec


PROJECT_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="task.stop",
        handler_name="_handle_stop_canvas_task",
        card={
            "cost": "free",
            "domain": "task",
            "id": "task.stop",
            "purpose": "停止当前节点绑定的真实生成任务并清理运行句柄。",
            "required_args": ["canvas_id", "node_id", "command_id"],
            "search_terms": "停止 终止 取消 节点 任务 stop cancel",
            "side_effect": "task_control",
        },
        tool_name="freezone_stop_task",
        description=(
            "Cancel the real task attached to one canvas node, then clear the "
            "persisted task handle so the node exits generation state immediately. "
            "Replays are idempotent at the task API."
        ),
        properties={
            "project_id": {"type": "string"},
            "canvas_id": {"type": "string"},
            "node_id": {"type": "string"},
            "command_id": {
                "type": "string",
                "description": "Idempotency id for this stop action.",
            },
            "job_id": {
                "type": "string",
                "description": "Optional override; normally read from the node.",
            },
            "task_type": {
                "type": "string",
                "description": "Optional override; normally read from the node.",
            },
        },
        required=("canvas_id", "node_id", "command_id"),
    ),
    ToolSpec(
        id="pipeline.status",
        handler_name="_handle_pipeline_status",
        card={
            "cost": "free",
            "domain": "project",
            "id": "pipeline.status",
            "purpose": "读取当前项目生产管线状态。",
            "required_args": [],
            "search_terms": "项目 管线 状态 pipeline progress",
            "side_effect": "read",
        },
        tool_name="village_canvas_pipeline_status",
        description="Get the current Village Infinite Canvas project pipeline status.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Project id. Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "episode": {
                "type": "integer",
                "description": "Optional episode number.",
            },
        },
    ),
    ToolSpec(
        id="production.control.get",
        handler_name="_handle_get_production_control",
        card={
            "cost": "free",
            "domain": "production",
            "id": "production.control.get",
            "purpose": "读取当前项目生产控制状态与运行信息。",
            "required_args": [],
            "search_terms": "生产 控制 成片 production control",
            "side_effect": "read",
        },
        tool_name="village_canvas_get_production_control",
        description=(
            "Read the durable one-click production driver: current stage, run "
            "status, blockers, settings and next real action."
        ),
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
        },
    ),
)
