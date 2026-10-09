"""Declared direct tools migrated from the ordered source parts."""

from __future__ import annotations

from .spec import ToolSpec


WORKFLOW_RUN_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="direct.village_canvas_start_workflow_run",
        handler_name="_handle_start_workflow_run",
        tool_name="village_canvas_start_workflow_run",
        description="Compatibility entry for resuming or explicitly replaying an already selected durable workflow. For every new user intent call village_canvas_dispatch_action instead so ActionRouter decides whether a WorkflowRun is needed. Draft builds editable structure without media; auto consumes the server-owned current-turn budget for this project and canvas.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "canvas_id": {
                "type": "string",
                "description": "Defaults to the active canvas.",
            },
            "workflow_id": {"type": "string"},
            "run_mode": {
                "type": "string",
                "enum": ["draft", "auto"],
                "default": "draft",
            },
            "request": {
                "type": "string",
                "description": "The complete user production request.",
            },
            "goal": {"type": "string"},
            "success_criteria": {
                "type": "array",
                "maxItems": 100,
                "items": {"type": "string"},
            },
            "starter_workflow_id": {
                "type": "string",
                "description": "Optional explicit canvas template id. Leave "
                "empty for dynamic composition.",
            },
            "use_starter_workflow": {
                "type": "boolean",
                "default": False,
                "description": "Opt in to the selected workflow's starter "
                "canvas template. Defaults to dynamic "
                "composition.",
            },
            "source_turn_id": {"type": "string"},
            "canvas_revision": {"type": "integer", "minimum": 0},
            "selected_node_ids": {
                "type": "array",
                "maxItems": 500,
                "items": {"type": "string"},
            },
            "pinned_node_ids": {
                "type": "array",
                "maxItems": 500,
                "items": {"type": "string"},
            },
            "model_bindings": {
                "type": "object",
                "description": "Optional direct registry ids for this run. The "
                "server resolves and freezes them without accepting "
                "keys.",
                "additionalProperties": {"type": "string"},
            },
            "idempotency_key": {
                "type": "string",
                "description": "Stable optional retry key; one is derived when "
                "omitted.",
            },
            "inputs": {
                "type": "object",
                "description": "Optional workflow-specific inputs.",
            },
            "task_authorization": {
                "type": "object",
                "description": "Optional current-turn policy facts. The parent "
                "service owns the signed paid-media grant and "
                "resolves it from the authenticated "
                "project/canvas scope; never invent or copy a "
                "grant id. compose_authorization_id is a "
                "separate browser-issued, server-validated "
                "final-compose ticket.",
                "properties": {
                    "scope": {"type": "string", "enum": ["current_turn"]},
                    "run_mode": {"type": "string", "enum": ["draft", "auto"]},
                    "allow_structure": {"type": "boolean"},
                    "allow_paid_media": {"type": "boolean"},
                    "max_paid_starts": {"type": "integer", "minimum": 0, "maximum": 64},
                    "require_video_confirmation": {"type": "boolean", "enum": [False]},
                    "compose_authorization_id": {
                        "type": "string",
                        "description": "Optional "
                        "browser-issued "
                        "one-shot "
                        "final-compose "
                        "ticket "
                        "id. "
                        "Never "
                        "invent "
                        "or "
                        "reuse "
                        "it "
                        "for "
                        "paid "
                        "media.",
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
        required=("workflow_id", "request"),
    ),
    ToolSpec(
        id="direct.village_canvas_update_workflow_run",
        handler_name="_handle_update_workflow_run",
        tool_name="village_canvas_update_workflow_run",
        description="Legacy/manual V1 checkpoint compatibility only. V2 workflow lifecycle is advanced by the server executor, CanvasCommandGateway and verifier; do not forge step completion.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "run_id": {"type": "string"},
            "event_id": {
                "type": "string",
                "description": "Stable idempotency id for this checkpoint result.",
            },
            "event_type": {
                "type": "string",
                "enum": [
                    "step_started",
                    "step_completed",
                    "step_failed",
                    "steering_added",
                ],
            },
            "step_id": {
                "type": "string",
                "enum": [
                    "story_and_shots",
                    "asset_slots",
                    "media_generation",
                    "quality_review",
                    "delivery",
                ],
            },
            "payload": {"type": "object"},
            "error": {"type": "string"},
            "expected_revision": {"type": "integer", "minimum": 0},
        },
        required=("run_id", "event_id", "event_type"),
    ),
)
