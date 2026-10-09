"""Workflow inspection, control, and canvas asset-binding recovery capabilities.

The cards are frozen verbatim by ``check_agent_tool_surface.py``.
"""

from __future__ import annotations

from .spec import ToolSpec


WORKFLOW_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="workflow.asset_binding.repair",
        handler_name="_handle_repair_workflow_canvas_asset_binding",
        card={
            "authorization_mode": "project_write",
            "cost": "free",
            "domain": "workflow",
            "failure_policy": "stop_on_error",
            "id": "workflow.asset_binding.repair",
            "idempotency": "command_id",
            "optional_args": [
                "project_id",
                "canvas_id",
                "source_turn_id",
                "expected_run_revision",
            ],
            "purpose": "只对原 failed WorkflowRun 的重复资产绑定恢复合同重读服务端画布，执行保守的重复认领整理；不删除节点、不出图、不重放 Workflow。",
            "required_args": ["run_id", "step_id", "command_id"],
            "resume_policy": "wait_or_replay_command_id",
            "route_policy": "workflow_canvas_repair",
            "search_terms": "工作流 恢复 重复 资产 绑定 整理 ambiguous canvas asset repair",
            "side_effect": "canvas_write",
            "verifier_contract": "canvas_command_receipt.v2",
        },
    ),
    ToolSpec(
        id="workflow.asset_binding.revalidate",
        handler_name="_handle_revalidate_workflow_canvas_asset_binding",
        card={
            "authorization_mode": "project_write",
            "cost": "free",
            "domain": "workflow",
            "failure_policy": "stop_on_error",
            "id": "workflow.asset_binding.revalidate",
            "idempotency": "command_id",
            "optional_args": [
                "project_id",
                "canvas_id",
                "source_turn_id",
                "expected_run_revision",
            ],
            "purpose": "对已安全整理的重复资产绑定按当前画布做只读复验；通过后只把原失败步骤转交现有一次性媒体授权门，不提交 provider 任务。",
            "required_args": ["run_id", "step_id", "command_id"],
            "resume_policy": "wait_or_replay_command_id",
            "route_policy": "workflow_canvas_repair",
            "search_terms": "工作流 恢复 资产 绑定 复验 readiness 授权交接 revalidate",
            "side_effect": "workflow_write",
            "verifier_contract": "workflow_canvas_asset_binding_readiness.v1",
        },
    ),
    ToolSpec(
        id="workflow.list",
        handler_name="_handle_list_workflows",
        card={
            "cost": "free",
            "domain": "workflow",
            "id": "workflow.list",
            "purpose": "列出当前可执行的真实工作流定义。",
            "required_args": [],
            "search_terms": "工作流 定义 列表 workflow list",
            "side_effect": "read",
        },
        tool_name="village_canvas_list_workflows",
        description=(
            "List the real durable workflow definitions before choosing one. "
            "Returns ids, versions, steps, starter templates and declared outputs."
        ),
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
        },
    ),
    ToolSpec(
        id="workflow.runs.list",
        handler_name="_handle_list_workflow_runs",
        card={
            "cost": "free",
            "domain": "workflow",
            "id": "workflow.runs.list",
            "purpose": "列出当前画布的工作流运行记录。",
            "required_args": ["canvas_id"],
            "search_terms": "工作流 运行 进度 历史 workflow run",
            "side_effect": "read",
        },
        tool_name="village_canvas_list_workflow_runs",
        description=(
            "List workflow runs on the current canvas. Resume a running, paused or "
            "failed run instead of creating a duplicate."
        ),
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "canvas_id": {
                "type": "string",
                "description": "Defaults to the active canvas.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 20,
            },
        },
    ),
    ToolSpec(
        id="workflow.run.get",
        handler_name="_handle_get_workflow_run",
        card={
            "cost": "free",
            "domain": "workflow",
            "id": "workflow.run.get",
            "purpose": "读取一个工作流运行的当前 frontier、步骤和回执。",
            "required_args": ["run_id"],
            "search_terms": "工作流 运行 状态 frontier receipt",
            "side_effect": "read",
        },
        tool_name="village_canvas_get_workflow_run",
        description=(
            "Read a durable canvas workflow run, its current frontier, checkpoints, "
            "artifacts and revision."
        ),
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "run_id": {"type": "string"},
        },
        required=("run_id",),
    ),
    ToolSpec(
        id="workflow.run.control",
        handler_name="_handle_command_workflow_run",
        card={
            "cost": "free",
            "domain": "workflow",
            "id": "workflow.run.control",
            "purpose": "暂停、继续、取消或重试一个已有工作流运行。",
            "required_args": ["run_id", "command"],
            "search_terms": "工作流 暂停 继续 取消 重试 pause resume cancel retry",
            "side_effect": "task_control",
        },
        tool_name="village_canvas_command_workflow_run",
        description=(
            "Pause, resume, cancel, retry a failed checkpoint, or add a steering "
            "direction without discarding completed workflow assets. Browser-issued "
            "one-shot tickets may be carried in task_authorization; the plugin "
            "consumes them before retrying final_film, storyboard_images or "
            "shot_videos."
        ),
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "canvas_id": {
                "type": "string",
                "description": (
                    "Optional canvas scope. The plugin verifies it against the "
                    "durable run."
                ),
            },
            "run_id": {"type": "string"},
            "command": {
                "type": "string",
                "enum": ["pause", "resume", "cancel", "retry", "steer"],
            },
            "idempotency_key": {"type": "string"},
            "expected_revision": {"type": "integer", "minimum": 0},
            "step_id": {"type": "string"},
            "item_ids": {
                "type": "array",
                "maxItems": 500,
                "items": {"type": "string"},
            },
            "retry_scope": {
                "type": "string",
                "enum": ["whole_step", "failed_items_only"],
                "default": "whole_step",
            },
            "direction": {"type": "string"},
            "execution_context": {
                "type": "object",
                "description": (
                    "Planner-owned context forwarded unchanged when recovering a "
                    "workflow step."
                ),
            },
            "task_authorization": {
                "type": "object",
                "description": (
                    "Optional current-turn policy facts. The parent service owns the "
                    "signed paid-media grant and resolves it from the authenticated "
                    "project/canvas scope; never invent or copy a grant id. "
                    "compose_authorization_id is a separate browser-issued, "
                    "server-validated final-compose ticket."
                ),
                "properties": {
                    "scope": {"type": "string", "enum": ["current_turn"]},
                    "run_mode": {"type": "string", "enum": ["draft", "auto"]},
                    "allow_structure": {"type": "boolean"},
                    "allow_paid_media": {"type": "boolean"},
                    "max_paid_starts": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 64,
                    },
                    "require_video_confirmation": {
                        "type": "boolean",
                        "enum": [False],
                    },
                    "compose_authorization_id": {
                        "type": "string",
                        "description": (
                            "Optional browser-issued one-shot final-compose ticket id. "
                            "Never invent or reuse it for paid media."
                        ),
                    },
                },
                "required": [
                    "scope",
                    "run_mode",
                    "allow_structure",
                    "allow_paid_media",
                    "max_paid_starts",
                    "require_video_confirmation",
                ],
            },
        },
        required=("run_id", "command", "idempotency_key"),
    ),
)
