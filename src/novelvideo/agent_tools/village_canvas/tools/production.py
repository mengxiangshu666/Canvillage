"""Declared direct tools migrated from the ordered source parts."""

from __future__ import annotations

from .spec import ToolSpec


PRODUCTION_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="direct.village_canvas_start_production_run",
        handler_name="_handle_start_production_run",
        tool_name="village_canvas_start_production_run",
        description="Start or reuse the durable one-click production driver. Auto-mode task_authorization can run the complete media workflow; confirmed_paid_media remains a direct-call override.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "mode": {"type": "string", "enum": ["best", "next"]},
            "uploaded_filename": {"type": "string"},
            "target_episodes": {"type": "integer", "minimum": 1, "maximum": 100},
            "episode": {"type": "integer", "minimum": 1},
            "image_model": {"type": "string"},
            "video_backend": {"type": "string"},
            "aspect_ratio": {"type": "string", "enum": ["9:16", "16:9", "2:3", "1:1"]},
            "canvas_id": {"type": "string"},
            "goal": {"type": "string"},
            "success_criteria": {
                "type": "array",
                "maxItems": 100,
                "items": {"type": "string"},
            },
            "assumptions": {
                "type": "array",
                "maxItems": 50,
                "items": {"type": "string"},
            },
            "constraints": {
                "type": "array",
                "maxItems": 50,
                "items": {"type": "string"},
            },
            "output_spec": {"type": "object"},
            "world_state_ref": {"type": "string"},
            "asset_plan": {"type": "object"},
            "episode_plan": {
                "type": "array",
                "maxItems": 100,
                "items": {"type": "object"},
            },
            "quality_gates": {
                "type": "array",
                "maxItems": 50,
                "items": {"type": "string"},
            },
            "budget": {"type": "object"},
            "canvas_skeleton_refs": {
                "type": "array",
                "maxItems": 500,
                "items": {"type": "string"},
            },
            "director_plan": {"type": "object"},
            "director_intent_contract": {
                "type": "object",
                "description": "Optional intent facts. State only what "
                "you know (delivery_level, shot_count, "
                "characters, locations, props, style, "
                "reference_policy, spatial_complexity, "
                "audio_required, subtitles_required, "
                "compose_required, output_spec). The "
                "server derives required_assets, "
                "quality_gates, graph_contract, "
                "template_policy and contract_revision; "
                "do not hand-author them.",
            },
            "auto_generate_paid_media": {
                "type": "boolean",
                "description": "False by default. Auto mode may set true "
                "for end-to-end production.",
            },
            "confirmed_paid_media": {
                "type": "boolean",
                "description": "Optional direct-call override when no Canvas "
                "V2 authorization is present.",
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
        required=(),
    ),
    ToolSpec(
        id="direct.village_canvas_command_production_run",
        handler_name="_handle_command_production_run",
        tool_name="village_canvas_command_production_run",
        description="Control a durable production run. Auto-mode task_authorization may resume/retry; confirmed_paid_media is an optional direct-call override.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "run_id": {"type": "string"},
            "command": {
                "type": "string",
                "enum": ["pause", "resume", "cancel", "retry", "skip", "take_over"],
            },
            "confirmed_paid_media": {
                "type": "boolean",
                "description": "Optional direct-call override for resume/retry.",
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
        required=("run_id", "command"),
    ),
)
