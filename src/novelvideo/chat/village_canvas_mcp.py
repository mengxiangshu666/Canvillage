"""MCP bridge for Village Infinite Canvas native tools."""

from __future__ import annotations

import asyncio
from typing import Any

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from novelvideo.agent_tools import build_native_registry


PLUGIN_REGISTRY = build_native_registry()
SERVER = Server("village-canvas", version="0.1.0")


@SERVER.list_tools()
async def list_tools() -> list[types.Tool]:
    result: list[types.Tool] = []
    for tool in PLUGIN_REGISTRY.list_tools():
        name = tool.name
        schema = tool.schema
        parameters = schema.get("parameters") if isinstance(schema, dict) else None
        result.append(
            types.Tool(
                name=name,
                description=str(schema.get("description") or ""),
                inputSchema=parameters if isinstance(parameters, dict) else {"type": "object"},
            )
        )
    return result


@SERVER.call_tool(validate_input=True)
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    if name not in {tool.name for tool in PLUGIN_REGISTRY.list_tools()}:
        raise ValueError(f"unknown Village Infinite Canvas tool: {name}")
    text = await PLUGIN_REGISTRY.invoke(name, arguments or {})
    return [types.TextContent(type="text", text=str(text or ""))]


async def _main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await SERVER.run(
            read_stream,
            write_stream,
            SERVER.create_initialization_options(),
        )


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
