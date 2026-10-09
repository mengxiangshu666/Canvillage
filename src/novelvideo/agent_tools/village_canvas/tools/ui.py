"""Declared direct tools migrated from the ordered source parts."""

from __future__ import annotations

from .spec import ToolSpec


UI_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="direct.village_canvas_ui",
        handler_name="_handle_frontend_ui_tool",
        tool_name="village_canvas_ui",
        description="Operate the current browser's real canvas UI and wait for an ack/progress/result receipt. This tool only selects or focuses nodes, fits the viewport, and opens or closes a node tool panel; it never creates, edits, moves, connects, or deletes canvas data.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "canvas_id": {"type": "string", "description": "Current canvas id."},
            "source_turn_id": {
                "type": "string",
                "description": "Current Agent turn id for event correlation.",
            },
            "action": {
                "type": "string",
                "enum": [
                    "select_node",
                    "focus_node",
                    "fit_view",
                    "open_tool_dialog",
                    "close_tool_dialog",
                ],
            },
            "node_id": {
                "type": "string",
                "description": "Required by select, focus and open-tool actions.",
            },
            "tool_type": {
                "type": "string",
                "enum": ["crop", "annotate", "split-storyboard"],
                "description": "Required only when opening a node tool panel.",
            },
            "duration_ms": {
                "type": "integer",
                "minimum": 0,
                "maximum": 2000,
                "default": 220,
            },
            "max_zoom": {"type": "number", "minimum": 0.1, "maximum": 4.0},
            "timeout_seconds": {
                "type": "number",
                "minimum": 1,
                "maximum": 18,
                "default": 8,
            },
        },
        required=("canvas_id", "action"),
    ),
)
