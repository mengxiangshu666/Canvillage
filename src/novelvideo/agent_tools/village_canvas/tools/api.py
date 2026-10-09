"""Generic relative-path HTTP tools for the Village canvas API."""

from __future__ import annotations

from .spec import ToolSpec


_PATH_PROPS = {
    "path": {
        "type": "string",
        "description": (
            "Village Infinite Canvas relative API path. Must start with "
            "/api/v1/ or /projects/. Absolute URLs are rejected. Ingest routes "
            "are only /projects/{project}/ingest/upload and "
            "/projects/{project}/ingest/start; ingest_fast is a task_type, not "
            "an endpoint."
        ),
    },
    "query": {"type": "object", "description": "Optional query parameters."},
}


API_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="api.get",
        handler_name="_handle_get",
        tool_name="village_canvas_get",
        description="Call a Village Infinite Canvas GET API path without using curl.",
        properties=dict(_PATH_PROPS),
        required=("path",),
    ),
    ToolSpec(
        id="api.post",
        handler_name="_handle_post",
        tool_name="village_canvas_post",
        description="Call a Village Infinite Canvas POST API path without using curl.",
        properties={**_PATH_PROPS, "body": {"type": "object"}},
        required=("path",),
    ),
    ToolSpec(
        id="api.patch",
        handler_name="_handle_patch",
        tool_name="village_canvas_patch",
        description=(
            "Call a Village Infinite Canvas PATCH API path without using curl."
        ),
        properties={**_PATH_PROPS, "body": {"type": "object"}},
        required=("path",),
    ),
    ToolSpec(
        id="api.delete",
        handler_name="_handle_delete",
        tool_name="village_canvas_delete",
        description=(
            "Call a Village Infinite Canvas DELETE API path without using curl."
        ),
        properties={**_PATH_PROPS, "body": {"type": "object"}},
        required=("path",),
    ),
)
