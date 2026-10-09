"""Declared direct tools migrated from the ordered source parts."""

from __future__ import annotations

from .spec import ToolSpec


CANVAS_WRITE_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="direct.freezone_emit_canvas_command",
        handler_name="_handle_compatibility_emit_canvas_command",
        tool_name="freezone_emit_canvas_command",
        description="Compatibility writer for replaying or resuming an already selected direct-canvas transaction. For every new user intent call village_canvas_dispatch_action instead so the parent ActionRouter selects the executor. Every supported create/update/delete/connect/move operation persists server-side and auto-applies on UI. Never starts media generation.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "canvas_id": {
                "type": "string",
                "description": "Current canvas id from CURRENT_CANVAS_CONTEXT.",
            },
            "command_id": {
                "type": "string",
                "description": "Batch/idempotency id for this multi-op transaction.",
            },
            "source_turn_id": {
                "type": "string",
                "description": "Current Agent turn id for causal tracing.",
            },
            "expected_canvas_revision": {"type": "integer", "minimum": 0},
            "dynamic_checkpoint": {
                "type": "object",
                "description": "Internal ready_write checkpoint; supplied only "
                "by the dynamic existing-node execution chain.",
            },
            "commands": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": [
                                "focus_node",
                                "select_node",
                                "connect_nodes",
                                "remove_edge",
                                "annotate",
                                "create_canvas_node",
                                "create_image_prompt_node",
                                "create_video_prompt_node",
                                "create_shot_sequence",
                                "insert_starter_workflow",
                                "update_node_prompt",
                                "update_node_label",
                                "update_node_data",
                                "update_node_camera",
                                "move_node",
                                "duplicate_node",
                                "delete_node",
                            ],
                        },
                        "node_id": {
                            "type": "string",
                            "description": "Concrete id, or "
                            "$selected / $pinned "
                            "/ $pinned:N. Omit → "
                            "$selected.",
                        },
                        "node_type": {
                            "type": "string",
                            "enum": [
                                "audioNode",
                                "beatContextNode",
                                "exportImageNode",
                                "groupNode",
                                "imageGenNode",
                                "imageNode",
                                "pano360ViewerNode",
                                "scriptNode",
                                "skillNode",
                                "storyboardGenNode",
                                "storyboardNode",
                                "textAnnotationNode",
                                "threeDWorldNode",
                                "uploadNode",
                                "videoComposeNode",
                                "videoNode",
                                "videoStoryNode",
                            ],
                        },
                        "workflow_id": {
                            "type": "string",
                            "description": "Starter "
                            "workflow id "
                            "from the "
                            "current "
                            "workflow "
                            "manifest.",
                        },
                        "created_node_id": {
                            "type": "string",
                            "description": "Optional "
                            "stable id; "
                            "omit to let "
                            "the server "
                            "mint one.",
                        },
                        "source": {
                            "type": "string",
                            "description": "Edge source; use "
                            "$created:N for a "
                            "node created earlier "
                            "in this batch, "
                            "otherwise a concrete "
                            "id or $selected.",
                        },
                        "target": {
                            "type": "string",
                            "description": "Edge target; use "
                            "$created:N for a "
                            "node created earlier "
                            "in this batch, "
                            "otherwise a concrete "
                            "id or $pinned.",
                        },
                        "text": {"type": "string"},
                        "prompt": {"type": "string"},
                        "prompts": {"type": "array", "items": {"type": "string"}},
                        "display_name": {"type": "string"},
                        "reason": {
                            "type": "string",
                            "maxLength": 2000,
                            "description": "Optional explanation "
                            "for this command; "
                            "never place it "
                            "inside node_data.",
                        },
                        "node_data": {
                            "type": "object",
                            "description": "Partial node data "
                            "patch. Use only "
                            "for real task "
                            "handles and "
                            "node-specific "
                            "settings; never "
                            "put prompt text "
                            "or reason here.",
                        },
                        "aspect_ratio": {
                            "type": "string",
                            "enum": [
                                "auto",
                                "1:1",
                                "16:9",
                                "9:16",
                                "4:3",
                                "3:4",
                                "3:2",
                                "2:3",
                                "4:5",
                                "5:4",
                                "21:9",
                            ],
                            "description": "Actual node "
                            "aspect ratio; "
                            "image nodes "
                            "accept the "
                            "full list, "
                            "video nodes "
                            "accept "
                            "16:9/4:3/1:1/3:4/9:16/21:9. "
                            "Never put a "
                            "requested "
                            "ratio only in "
                            "prompt text.",
                        },
                        "image_size": {
                            "type": "string",
                            "enum": ["0.5K", "1K", "2K", "4K"],
                            "description": "Image node "
                            "resolution tier; "
                            "applies to image "
                            "and "
                            "shot-sequence "
                            "nodes.",
                        },
                        "video_quality": {
                            "type": "string",
                            "enum": ["480P", "720P", "768P", "1080P", "4K"],
                            "description": "Video node resolution tier.",
                        },
                        "duration_sec": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 120,
                        },
                        "model": {
                            "type": "string",
                            "description": "Use only a model id "
                            "present in the "
                            "current canvas/model "
                            "context; omit when "
                            "unknown.",
                        },
                        "generation_mode": {
                            "type": "string",
                            "enum": [
                                "textToVideo",
                                "allReference",
                                "imageToVideo",
                                "firstLastFrame",
                                "imageReference",
                                "videoEdit",
                            ],
                        },
                        "generate_audio": {"type": "boolean"},
                        "count": {"type": "integer", "enum": [1, 2, 4, 6, 8, 12]},
                        "camera": {
                            "type": "object",
                            "properties": {
                                "camera_body_id": {"type": "string"},
                                "lens_id": {"type": "string"},
                                "focal_length_mm": {
                                    "type": "number",
                                    "minimum": 1,
                                    "maximum": 2000,
                                },
                                "aperture": {"type": "string"},
                            },
                        },
                        "camera_movement": {
                            "type": "string",
                            "description": "Video "
                            "camera-preset "
                            "id from "
                            "current "
                            "canvas "
                            "context.",
                        },
                        "clear_camera": {
                            "type": "boolean",
                            "description": "Clear image "
                            "camera "
                            "selection or "
                            "video movement "
                            "on "
                            "update_node_camera.",
                        },
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "placement": {
                            "type": "object",
                            "description": "Semantic "
                            "browser-resolved "
                            "placement. Prefer "
                            "viewport_center + "
                            "grid for new "
                            "nodes; explicit "
                            "x/y take "
                            "priority.",
                            "properties": {
                                "anchor": {
                                    "type": "string",
                                    "enum": [
                                        "viewport_center",
                                        "selected_node",
                                        "absolute",
                                    ],
                                },
                                "layout": {
                                    "type": "string",
                                    "enum": ["stack", "grid", "row", "column"],
                                },
                                "offset": {
                                    "type": "object",
                                    "properties": {
                                        "x": {"type": "number"},
                                        "y": {"type": "number"},
                                    },
                                },
                                "gap": {
                                    "type": "number",
                                    "minimum": 16,
                                    "maximum": 400,
                                },
                            },
                        },
                    },
                    "required": ["type"],
                },
            },
        },
        required=("canvas_id", "commands"),
    ),
    ToolSpec(
        id="direct.freezone_propose_generation",
        handler_name="_handle_propose_generation",
        tool_name="freezone_propose_generation",
        description="Propose an image/video/audio generation plan without starting a task. In auto mode, copy the current V2 task_authorization so the same turn can continue without another confirmation.",
        properties={
            "project_id": {"type": "string"},
            "node_id": {
                "type": "string",
                "description": "Target canvas node id when known.",
            },
            "kind": {"type": "string", "enum": ["image", "video", "audio"]},
            "model": {"type": "string"},
            "summary": {
                "type": "string",
                "description": "What would be generated and why.",
            },
            "prompt_preview": {"type": "string"},
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
        required=("summary",),
    ),
    ToolSpec(
        id="direct.freezone_run_node",
        handler_name="_handle_run_canvas_node",
        tool_name="freezone_run_node",
        description="Start one image, video or audio canvas node through the real Freezone task API. Reads the current node plus incoming edges, selects the matching generation mode, persists the task handle, and immediately updates the live canvas. In auto mode call this after structure and parameter validation.",
        properties={
            "project_id": {"type": "string"},
            "canvas_id": {"type": "string"},
            "node_id": {
                "type": "string",
                "description": "Concrete generation-capable node id.",
            },
            "command_id": {
                "type": "string",
                "description": "Idempotency id for this generation start.",
            },
            "operation": {"type": "string", "enum": ["start", "retry"]},
            "prompt": {
                "type": "string",
                "description": "Optional override; otherwise reads the node or incoming "
                "text.",
            },
            "model": {
                "type": "string",
                "description": "Optional current direct-model id override.",
            },
            "generation_mode": {
                "type": "string",
                "enum": [
                    "textToVideo",
                    "allReference",
                    "imageToVideo",
                    "firstLastFrame",
                    "imageReference",
                    "videoEdit",
                ],
            },
            "aspect_ratio": {"type": "string"},
            "image_size": {"type": "string", "enum": ["0.5K", "1K", "2K", "4K"]},
            "video_quality": {
                "type": "string",
                "enum": [
                    "480p",
                    "720p",
                    "768p",
                    "1080p",
                    "2k",
                    "480P",
                    "720P",
                    "768P",
                    "1080P",
                    "4K",
                ],
            },
            "duration_sec": {"type": "integer", "minimum": 1, "maximum": 120},
            "quality": {"type": "string"},
            "audio_kind": {"type": "string", "enum": ["speech", "music"]},
            "reference_urls": {
                "type": "array",
                "maxItems": 12,
                "items": {"type": "string"},
            },
            "references": {
                "type": "array",
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["image", "video", "audio"]},
                        "url": {"type": "string"},
                        "role": {"type": "string"},
                    },
                    "required": ["type", "url"],
                },
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
        required=("canvas_id", "node_id", "command_id"),
    ),
    ToolSpec(
        id="direct.freezone_retry_node",
        handler_name="_handle_retry_canvas_node",
        tool_name="freezone_retry_node",
        description="Retry one failed or cancelled canvas generation from the node's current prompt, direct model, parameters and incoming references. Persists a new task handle and immediately resumes the node UI.",
        properties={
            "project_id": {"type": "string"},
            "canvas_id": {"type": "string"},
            "node_id": {"type": "string"},
            "command_id": {
                "type": "string",
                "description": "New idempotency id for this retry.",
            },
            "prompt": {"type": "string"},
            "model": {"type": "string"},
            "references": {
                "type": "array",
                "maxItems": 12,
                "items": {"type": "object"},
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
        required=("canvas_id", "node_id", "command_id"),
    ),
    ToolSpec(
        id="direct.village_canvas_dispatch_action",
        handler_name="_handle_dispatch_canvas_action",
        tool_name="village_canvas_dispatch_action",
        description="Canonical execution entry for 小树. Compile the understood user request into task facts and one executable payload, then call this once. The parent ActionRouter deterministically chooses direct CanvasCommandGateway or durable WorkflowRun; do not choose between the legacy write/start tools yourself. Direct routes require commands. For an existing-node/edge mutation set dynamic_execution=true; the server will compile the runtime allowlist, confirm the checkpoint, bind the current canvas revision and then emit a receipt-backed write. Never send or invent an execution_context. Durable routes reuse workflow_id or default to custom-canvas-workflow; they compose from current canvas facts and do not insert a starter graph unless starter_workflow_id or use_starter_workflow=true is explicit. Returns the authoritative canvas receipt or WorkflowRun plus the route decision.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "canvas_id": {"type": "string", "description": "Current canvas id."},
            "request": {
                "type": "string",
                "minLength": 1,
                "description": "Complete understood production request.",
            },
            "task": {
                "type": "object",
                "description": "Structural facts compiled from the understood task. Do not "
                "infer from keywords; count the planned steps, items and "
                "dependencies and declare recovery/delivery needs.",
                "properties": {
                    "operation": {"type": "string", "minLength": 1, "maxLength": 200},
                    "interaction_mode": {
                        "type": "string",
                        "enum": ["discuss", "plan", "execute"],
                        "description": "Only execute authorizes "
                        "a write or WorkflowRun "
                        "command.",
                    },
                    "target_strategy": {
                        "type": "string",
                        "enum": ["reuse_existing", "create_missing"],
                        "description": "Reuse current nodes/run "
                        "unless a missing carrier "
                        "is proven.",
                    },
                    "target_node_ids": {
                        "type": "array",
                        "maxItems": 500,
                        "items": {"type": "string"},
                        "description": "Concrete existing "
                        "targets. Include every "
                        "node referenced by "
                        "mutation commands.",
                    },
                    "existing_run_id": {
                        "type": "string",
                        "maxLength": 200,
                        "description": "Exact failed, paused or "
                        "running WorkflowRun to "
                        "continue; empty for a "
                        "genuinely new task.",
                    },
                    "creation_reason": {
                        "type": "string",
                        "maxLength": 1000,
                        "description": "Optional note on why new nodes "
                        "are being created; advisory only, "
                        "never blocking (T-212).",
                    },
                    "step_count": {"type": "integer", "minimum": 1, "maximum": 1000},
                    "item_count": {"type": "integer", "minimum": 1, "maximum": 50000},
                    "dependency_count": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 200000,
                    },
                    "estimated_duration_seconds": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 604800,
                    },
                    "requires_recovery": {"type": "boolean"},
                    "requires_delivery": {"type": "boolean"},
                    "contains_paid_media": {"type": "boolean"},
                    "commands": {
                        "type": "array",
                        "maxItems": 100,
                        "description": "Optional exact executable canvas "
                        "command batch; when present it "
                        "is the write plan.",
                        "items": {"type": "object"},
                    },
                },
                "required": [
                    "operation",
                    "interaction_mode",
                    "target_strategy",
                    "target_node_ids",
                    "step_count",
                    "item_count",
                    "dependency_count",
                    "estimated_duration_seconds",
                    "requires_recovery",
                    "requires_delivery",
                    "contains_paid_media",
                ],
            },
            "commands": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": [
                                "focus_node",
                                "select_node",
                                "connect_nodes",
                                "remove_edge",
                                "annotate",
                                "create_canvas_node",
                                "create_image_prompt_node",
                                "create_video_prompt_node",
                                "create_shot_sequence",
                                "insert_starter_workflow",
                                "update_node_prompt",
                                "update_node_label",
                                "update_node_data",
                                "update_node_camera",
                                "move_node",
                                "duplicate_node",
                                "delete_node",
                            ],
                        },
                        "node_id": {
                            "type": "string",
                            "description": "Concrete id, or "
                            "$selected / $pinned "
                            "/ $pinned:N. Omit → "
                            "$selected.",
                        },
                        "node_type": {
                            "type": "string",
                            "enum": [
                                "audioNode",
                                "beatContextNode",
                                "exportImageNode",
                                "groupNode",
                                "imageGenNode",
                                "imageNode",
                                "pano360ViewerNode",
                                "scriptNode",
                                "skillNode",
                                "storyboardGenNode",
                                "storyboardNode",
                                "textAnnotationNode",
                                "threeDWorldNode",
                                "uploadNode",
                                "videoComposeNode",
                                "videoNode",
                                "videoStoryNode",
                            ],
                        },
                        "workflow_id": {
                            "type": "string",
                            "description": "Starter "
                            "workflow id "
                            "from the "
                            "current "
                            "workflow "
                            "manifest.",
                        },
                        "created_node_id": {
                            "type": "string",
                            "description": "Optional "
                            "stable id; "
                            "omit to let "
                            "the server "
                            "mint one.",
                        },
                        "source": {
                            "type": "string",
                            "description": "Edge source; use "
                            "$created:N for a "
                            "node created earlier "
                            "in this batch, "
                            "otherwise a concrete "
                            "id or $selected.",
                        },
                        "target": {
                            "type": "string",
                            "description": "Edge target; use "
                            "$created:N for a "
                            "node created earlier "
                            "in this batch, "
                            "otherwise a concrete "
                            "id or $pinned.",
                        },
                        "text": {"type": "string"},
                        "prompt": {"type": "string"},
                        "prompts": {"type": "array", "items": {"type": "string"}},
                        "display_name": {"type": "string"},
                        "reason": {
                            "type": "string",
                            "maxLength": 2000,
                            "description": "Optional explanation "
                            "for this command; "
                            "never place it "
                            "inside node_data.",
                        },
                        "node_data": {
                            "type": "object",
                            "description": "Partial node data "
                            "patch. Use only "
                            "for real task "
                            "handles and "
                            "node-specific "
                            "settings; never "
                            "put prompt text "
                            "or reason here.",
                        },
                        "aspect_ratio": {
                            "type": "string",
                            "enum": [
                                "auto",
                                "1:1",
                                "16:9",
                                "9:16",
                                "4:3",
                                "3:4",
                                "3:2",
                                "2:3",
                                "4:5",
                                "5:4",
                                "21:9",
                            ],
                            "description": "Actual node "
                            "aspect ratio; "
                            "image nodes "
                            "accept the "
                            "full list, "
                            "video nodes "
                            "accept "
                            "16:9/4:3/1:1/3:4/9:16/21:9. "
                            "Never put a "
                            "requested "
                            "ratio only in "
                            "prompt text.",
                        },
                        "image_size": {
                            "type": "string",
                            "enum": ["0.5K", "1K", "2K", "4K"],
                            "description": "Image node "
                            "resolution tier; "
                            "applies to image "
                            "and "
                            "shot-sequence "
                            "nodes.",
                        },
                        "video_quality": {
                            "type": "string",
                            "enum": ["480P", "720P", "768P", "1080P", "4K"],
                            "description": "Video node resolution tier.",
                        },
                        "duration_sec": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 120,
                        },
                        "model": {
                            "type": "string",
                            "description": "Use only a model id "
                            "present in the "
                            "current canvas/model "
                            "context; omit when "
                            "unknown.",
                        },
                        "generation_mode": {
                            "type": "string",
                            "enum": [
                                "textToVideo",
                                "allReference",
                                "imageToVideo",
                                "firstLastFrame",
                                "imageReference",
                                "videoEdit",
                            ],
                        },
                        "generate_audio": {"type": "boolean"},
                        "count": {"type": "integer", "enum": [1, 2, 4, 6, 8, 12]},
                        "camera": {
                            "type": "object",
                            "properties": {
                                "camera_body_id": {"type": "string"},
                                "lens_id": {"type": "string"},
                                "focal_length_mm": {
                                    "type": "number",
                                    "minimum": 1,
                                    "maximum": 2000,
                                },
                                "aperture": {"type": "string"},
                            },
                        },
                        "camera_movement": {
                            "type": "string",
                            "description": "Video "
                            "camera-preset "
                            "id from "
                            "current "
                            "canvas "
                            "context.",
                        },
                        "clear_camera": {
                            "type": "boolean",
                            "description": "Clear image "
                            "camera "
                            "selection or "
                            "video movement "
                            "on "
                            "update_node_camera.",
                        },
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "placement": {
                            "type": "object",
                            "description": "Semantic "
                            "browser-resolved "
                            "placement. Prefer "
                            "viewport_center + "
                            "grid for new "
                            "nodes; explicit "
                            "x/y take "
                            "priority.",
                            "properties": {
                                "anchor": {
                                    "type": "string",
                                    "enum": [
                                        "viewport_center",
                                        "selected_node",
                                        "absolute",
                                    ],
                                },
                                "layout": {
                                    "type": "string",
                                    "enum": ["stack", "grid", "row", "column"],
                                },
                                "offset": {
                                    "type": "object",
                                    "properties": {
                                        "x": {"type": "number"},
                                        "y": {"type": "number"},
                                    },
                                },
                                "gap": {
                                    "type": "number",
                                    "minimum": 16,
                                    "maximum": 400,
                                },
                            },
                        },
                    },
                    "required": ["type"],
                },
            },
            "generation_node_id": {
                "type": "string",
                "description": "Existing generation-capable node for a single "
                "direct media action. When one command creates "
                "exactly one media node, the created id is used "
                "automatically.",
            },
            "command_id": {
                "type": "string",
                "description": "Stable idempotency id for a direct canvas transaction.",
            },
            "expected_canvas_revision": {"type": "integer", "minimum": 0},
            "dynamic_execution": {
                "type": "boolean",
                "default": False,
                "description": "For existing-node/edge mutations only: run the "
                "shared-context, runtime-allowlist and "
                "ready_write checkpoint chain before writing.",
            },
            "workflow_id": {
                "type": "string",
                "enum": [
                    "one-click-film",
                    "storyboard-production",
                    "custom-canvas-workflow",
                    "freezone-final-film",
                ],
                "description": "Used only when the router selects WorkflowRun.",
            },
            "run_mode": {
                "type": "string",
                "enum": ["draft", "auto"],
                "default": "draft",
            },
            "goal": {
                "type": "string",
                "minLength": 1,
                "description": "One concise outcome statement derived from the full request "
                "and current canvas facts.",
            },
            "success_criteria": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "items": {"type": "string", "minLength": 1},
                "description": "Observable completion checks tied to canvas, "
                "task, media, revision or verifier evidence.",
            },
            "constraints": {
                "type": "array",
                "maxItems": 20,
                "items": {"type": "string"},
            },
            "assumptions": {
                "type": "array",
                "maxItems": 20,
                "items": {"type": "string"},
            },
            "unknowns": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
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
            "director_clarification_answers": {
                "type": "object",
                "description": "Answers collected by the director "
                "gate. The gate returns every "
                "remaining gap at once in "
                "`questions`; keep prior answers "
                "and add each answered question_id. "
                "Accepting an offered default also "
                "counts as an answer.",
            },
            "starter_workflow_id": {
                "type": "string",
                "description": "Optional explicit canvas template id. Leave "
                "empty for dynamic composition.",
            },
            "use_starter_workflow": {
                "type": "boolean",
                "default": False,
                "description": "Opt in to the workflow's starter canvas "
                "template; defaults to dynamic composition.",
            },
            "source_turn_id": {"type": "string"},
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
                "additionalProperties": {"type": "string"},
            },
            "parent_run_id": {
                "type": "string",
                "description": "Project-level ProductionControlStore parent run to "
                "continue.",
            },
            "director_plan_revision": {
                "type": "string",
                "description": "Immutable DirectorPlan revision referenced "
                "by this child run.",
            },
            "episode_scope": {"type": "integer", "minimum": 1, "maximum": 1000},
            "concurrency_policy": {
                "type": "object",
                "description": "Server-bounded episode/shot concurrency policy.",
            },
            "idempotency_key": {"type": "string"},
            "inputs": {"type": "object"},
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
        required=("canvas_id", "request", "task", "goal", "success_criteria"),
    ),
    ToolSpec(
        id="direct.village_canvas_read_compact",
        handler_name="_handle_read_canvas_compact",
        tool_name="village_canvas_read_compact",
        description="Fast read-only compact graph projection. Header fields (revision, viewport, counts, selection and paging) are emitted before bounded node summaries, preventing large canvas payloads from hiding viewport facts. The response also includes reference_manifest: 图片N/视频N/音频N are display labels only; bind generation inputs by its concrete node_id or asset_id and preserve the listed order.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "canvas_id": {"type": "string", "description": "Current canvas id."},
            "node_cursor": {"type": "integer", "minimum": 0, "default": 0},
            "node_limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "default": 40,
            },
            "prompt_chars": {
                "type": "integer",
                "minimum": 0,
                "maximum": 500,
                "default": 0,
                "description": "Optional prompt/content excerpt length; zero returns "
                "only has_prompt.",
            },
        },
        required=("canvas_id",),
    ),
    ToolSpec(
        id="direct.village_canvas_apply_commands",
        handler_name="_handle_apply_canvas_commands",
        tool_name="village_canvas_apply_commands",
        description="Compatibility plugin-bus canvas writer for an already routed direct transaction. Apply one idempotent batch through the existing canvas_chat_commands.v1 contract and return authoritative revision, applied_ops, created_node_ids and server_applied. Never starts media generation and cannot create image, video, or audio nodes; those must use village_canvas_dispatch_action so director, authorization, and WorkflowRun gates remain authoritative.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "canvas_id": {
                "type": "string",
                "description": "Current canvas id from CURRENT_CANVAS_CONTEXT.",
            },
            "command_id": {
                "type": "string",
                "description": "Batch/idempotency id for this multi-op transaction.",
            },
            "source_turn_id": {
                "type": "string",
                "description": "Current Agent turn id for causal tracing.",
            },
            "expected_canvas_revision": {"type": "integer", "minimum": 0},
            "dynamic_checkpoint": {
                "type": "object",
                "description": "Internal ready_write checkpoint; supplied only "
                "by the dynamic existing-node execution chain.",
            },
            "commands": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": [
                                "focus_node",
                                "select_node",
                                "connect_nodes",
                                "remove_edge",
                                "annotate",
                                "create_canvas_node",
                                "create_image_prompt_node",
                                "create_video_prompt_node",
                                "create_shot_sequence",
                                "insert_starter_workflow",
                                "update_node_prompt",
                                "update_node_label",
                                "update_node_data",
                                "update_node_camera",
                                "move_node",
                                "duplicate_node",
                                "delete_node",
                            ],
                        },
                        "node_id": {
                            "type": "string",
                            "description": "Concrete id, or "
                            "$selected / $pinned "
                            "/ $pinned:N. Omit → "
                            "$selected.",
                        },
                        "node_type": {
                            "type": "string",
                            "enum": [
                                "audioNode",
                                "beatContextNode",
                                "exportImageNode",
                                "groupNode",
                                "imageGenNode",
                                "imageNode",
                                "pano360ViewerNode",
                                "scriptNode",
                                "skillNode",
                                "storyboardGenNode",
                                "storyboardNode",
                                "textAnnotationNode",
                                "threeDWorldNode",
                                "uploadNode",
                                "videoComposeNode",
                                "videoNode",
                                "videoStoryNode",
                            ],
                        },
                        "workflow_id": {
                            "type": "string",
                            "description": "Starter "
                            "workflow id "
                            "from the "
                            "current "
                            "workflow "
                            "manifest.",
                        },
                        "created_node_id": {
                            "type": "string",
                            "description": "Optional "
                            "stable id; "
                            "omit to let "
                            "the server "
                            "mint one.",
                        },
                        "source": {
                            "type": "string",
                            "description": "Edge source; use "
                            "$created:N for a "
                            "node created earlier "
                            "in this batch, "
                            "otherwise a concrete "
                            "id or $selected.",
                        },
                        "target": {
                            "type": "string",
                            "description": "Edge target; use "
                            "$created:N for a "
                            "node created earlier "
                            "in this batch, "
                            "otherwise a concrete "
                            "id or $pinned.",
                        },
                        "text": {"type": "string"},
                        "prompt": {"type": "string"},
                        "prompts": {"type": "array", "items": {"type": "string"}},
                        "display_name": {"type": "string"},
                        "reason": {
                            "type": "string",
                            "maxLength": 2000,
                            "description": "Optional explanation "
                            "for this command; "
                            "never place it "
                            "inside node_data.",
                        },
                        "node_data": {
                            "type": "object",
                            "description": "Partial node data "
                            "patch. Use only "
                            "for real task "
                            "handles and "
                            "node-specific "
                            "settings; never "
                            "put prompt text "
                            "or reason here.",
                        },
                        "aspect_ratio": {
                            "type": "string",
                            "enum": [
                                "auto",
                                "1:1",
                                "16:9",
                                "9:16",
                                "4:3",
                                "3:4",
                                "3:2",
                                "2:3",
                                "4:5",
                                "5:4",
                                "21:9",
                            ],
                            "description": "Actual node "
                            "aspect ratio; "
                            "image nodes "
                            "accept the "
                            "full list, "
                            "video nodes "
                            "accept "
                            "16:9/4:3/1:1/3:4/9:16/21:9. "
                            "Never put a "
                            "requested "
                            "ratio only in "
                            "prompt text.",
                        },
                        "image_size": {
                            "type": "string",
                            "enum": ["0.5K", "1K", "2K", "4K"],
                            "description": "Image node "
                            "resolution tier; "
                            "applies to image "
                            "and "
                            "shot-sequence "
                            "nodes.",
                        },
                        "video_quality": {
                            "type": "string",
                            "enum": ["480P", "720P", "768P", "1080P", "4K"],
                            "description": "Video node resolution tier.",
                        },
                        "duration_sec": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 120,
                        },
                        "model": {
                            "type": "string",
                            "description": "Use only a model id "
                            "present in the "
                            "current canvas/model "
                            "context; omit when "
                            "unknown.",
                        },
                        "generation_mode": {
                            "type": "string",
                            "enum": [
                                "textToVideo",
                                "allReference",
                                "imageToVideo",
                                "firstLastFrame",
                                "imageReference",
                                "videoEdit",
                            ],
                        },
                        "generate_audio": {"type": "boolean"},
                        "count": {"type": "integer", "enum": [1, 2, 4, 6, 8, 12]},
                        "camera": {
                            "type": "object",
                            "properties": {
                                "camera_body_id": {"type": "string"},
                                "lens_id": {"type": "string"},
                                "focal_length_mm": {
                                    "type": "number",
                                    "minimum": 1,
                                    "maximum": 2000,
                                },
                                "aperture": {"type": "string"},
                            },
                        },
                        "camera_movement": {
                            "type": "string",
                            "description": "Video "
                            "camera-preset "
                            "id from "
                            "current "
                            "canvas "
                            "context.",
                        },
                        "clear_camera": {
                            "type": "boolean",
                            "description": "Clear image "
                            "camera "
                            "selection or "
                            "video movement "
                            "on "
                            "update_node_camera.",
                        },
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "placement": {
                            "type": "object",
                            "description": "Semantic "
                            "browser-resolved "
                            "placement. Prefer "
                            "viewport_center + "
                            "grid for new "
                            "nodes; explicit "
                            "x/y take "
                            "priority.",
                            "properties": {
                                "anchor": {
                                    "type": "string",
                                    "enum": [
                                        "viewport_center",
                                        "selected_node",
                                        "absolute",
                                    ],
                                },
                                "layout": {
                                    "type": "string",
                                    "enum": ["stack", "grid", "row", "column"],
                                },
                                "offset": {
                                    "type": "object",
                                    "properties": {
                                        "x": {"type": "number"},
                                        "y": {"type": "number"},
                                    },
                                },
                                "gap": {
                                    "type": "number",
                                    "minimum": 16,
                                    "maximum": 400,
                                },
                            },
                        },
                    },
                    "required": ["type"],
                },
            },
        },
        required=("canvas_id", "commands"),
    ),
)
