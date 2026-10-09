from __future__ import annotations

from typing import Any

from .core import (
    CANVAS_TOOLSET,
    CAPABILITY_BROKER_TOOL_NAME,
    _available,
    _tool_exposure_mode,
    exposed_canvas_agent_tool_names,
    registered_canvas_toolsets,
)
from .runtime import runtime_attr, runtime_handler

_PATH_PROPS = {
    "path": {
        "type": "string",
        "description": (
            "Village Infinite Canvas relative API path. Must start with /api/v1/ or /projects/. "
            "Absolute URLs are rejected. Ingest routes are only "
            "/projects/{project}/ingest/upload and /projects/{project}/ingest/start; "
            "ingest_fast is a task_type, not an endpoint."
        ),
    },
    "query": {"type": "object", "description": "Optional query parameters."},
}


def _schema(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required or [],
        },
    }


TOOLS = ()


class _NativeToolCollector:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, Any], Any]] = []

    def register_tool(self, **tool: Any) -> None:
        name = str(tool.get("name") or "").strip()
        schema = tool.get("schema")
        handler = tool.get("handler")
        if not name or not isinstance(schema, dict) or not callable(handler):
            raise ValueError(
                "native tool registration requires name, schema, and handler"
            )
        self.entries.append((name, dict(schema), handler))


def native_tool_entries() -> tuple[tuple[str, dict[str, Any], Any], ...]:
    """Return the same bounded surface the Agent runtime registers."""

    collector = _NativeToolCollector()
    register(collector)
    return tuple(collector.entries)


def register(ctx) -> None:
    exposure_mode = _tool_exposure_mode()
    register_toolsets = registered_canvas_toolsets()
    capability_index = runtime_attr("_CAPABILITY_INDEX")
    capability_schema = _schema(
        CAPABILITY_BROKER_TOOL_NAME,
        "Search, describe, or invoke the real Village Canvas capability index. The index contains execution facts only—never story templates, fixed node counts, canned prompts, or keyword-triggered creative output. Creative text/plan capabilities use the Creative Action Adapter with prerequisites and idempotent task receipts; canvas/workflow/media writes still enter through their authoritative routes.",
        {
            "action": {"type": "string", "enum": ["search", "describe", "invoke"]},
            "query": {
                "type": "string",
                "description": "Capability need, not the desired creative output.",
            },
            "domain": {
                "type": "string",
                "enum": sorted(
                    {
                        str(card.get("domain") or "")
                        for card in capability_index
                        if str(card.get("domain") or "")
                    }
                ),
                "description": (
                    "Capability domain from the live index. Use it only to narrow "
                    "search results; omit it when the correct domain is uncertain."
                ),
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 16},
            "capability_id": {
                "type": "string",
                "description": "Exact id returned by search or describe.",
            },
            "arguments": {
                "type": "object",
                "description": "Arguments passed to the capability's validated real handler.",
            },
            "agent_task": {
                "type": "object",
                "description": (
                    "Optional task binding copied from the current agent_execution_plan. "
                    "When present it must name the selected agent_id, handler_id and "
                    "consumer_agent_ids; the broker returns an agent_specialist_result.v1 "
                    "handoff without copying the handler payload."
                ),
                "properties": {
                    "task_id": {"type": "string", "maxLength": 240},
                    "agent_id": {"type": "string", "maxLength": 128},
                    "handler_id": {"type": "string", "maxLength": 200},
                    "invocation": {"type": "string", "maxLength": 80},
                    "plan_revision": {"type": "string", "maxLength": 120},
                    "consumer_agent_ids": {
                        "type": "array",
                        "maxItems": 8,
                        "items": {"type": "string", "maxLength": 128},
                    },
                    "source_refs": {
                        "type": "array",
                        "maxItems": 32,
                        "items": {"type": "object"},
                    },
                },
            },
            "project_id": {
                "type": "string",
                "description": "Current project id; defaults to the active context.",
            },
            "canvas_id": {
                "type": "string",
                "description": "Current canvas id when the capability is canvas-scoped.",
            },
        },
        ["action"],
    )
    vision_schema = _schema(
        "vision_analyze",
        "Analyze a local project image or HTTP(S) image through Village Infinite Canvas's verified multimodal gateway. Local images are relayed through the private HK upload channel before the vision model reads them.",
        {
            "image_url": {
                "type": "string",
                "description": "Absolute/relative local project image path, file:// URI, or HTTP(S) URL.",
            },
            "question": {
                "type": "string",
                "description": "What to inspect or explain.",
            },
            "model": {
                "type": "string",
                "description": "Optional logical vision model override.",
            },
        },
        ["image_url", "question"],
    )
    if exposure_mode == "indexed":
        ctx.register_tool(
            name=CAPABILITY_BROKER_TOOL_NAME,
            toolset=register_toolsets[0],
            schema=capability_schema,
            handler=runtime_handler("_handle_capability_broker"),
            check_fn=_available,
            requires_env=["VILLAGE_CANVAS_API_URL", "VILLAGE_CANVAS_AGENT_TOKEN"],
            is_async=True,
            description=capability_schema["description"],
            emoji="",
            override=True,
        )
    else:
        ctx.register_tool(
            name="vision_analyze",
            toolset=CANVAS_TOOLSET,
            schema=vision_schema,
            handler=runtime_handler("_handle_vision_analyze"),
            check_fn=_available,
            requires_env=["VILLAGE_CANVAS_API_URL", "VILLAGE_CANVAS_AGENT_TOKEN"],
            is_async=True,
            description=vision_schema["description"],
            emoji="",
            override=True,
        )
    exposed_names = exposed_canvas_agent_tool_names()
    for name, schema, handler in runtime_attr("TOOLS"):
        if name not in exposed_names:
            continue
        for toolset in register_toolsets:
            ctx.register_tool(
                name=name,
                toolset=toolset,
                schema=schema,
                handler=handler,
                check_fn=_available,
                requires_env=["VILLAGE_CANVAS_API_URL", "VILLAGE_CANVAS_AGENT_TOKEN"],
                description=schema["description"],
                emoji="",
            )
