"""Native Village Canvas tool registry used by the in-process Agent harness."""

from __future__ import annotations

from novelvideo.agent_plugins import (
    PluginProtocol,
    PluginRegistry,
    StaticToolProvider,
    VillagePluginManifest,
)

from .skills import skill_tool_handler, skill_tool_schema
from .turn_intent import (
    TURN_INTENT_TOOL_NAME,
    TURN_INTENT_TOOL_SCHEMA,
    turn_intent_tool_handler,
)
from .village_canvas import native_tool_entries


def build_native_registry() -> PluginRegistry:
    tools = {
        name: (schema, handler)
        for name, schema, handler in native_tool_entries()
    }
    registry = PluginRegistry()
    registry.register(
        VillagePluginManifest(
            plugin_id="village.canvas.native",
            name="Village Canvas Native Tools",
            version="1.0.0",
            protocol=PluginProtocol.VILLAGE_NATIVE,
            capabilities=(
                "canvas.read.compact",
                "canvas.commands.apply",
                "canvas.receipts.wait",
                "canvas.media.tasks",
                "workflow.production",
                "knowledge.search",
                "vision.analyze",
            ),
            tool_names=tuple(sorted(tools)),
            priority=1_000,
        ),
        StaticToolProvider(tools),
    )
    skill_tool = skill_tool_schema()
    registry.register(
        VillagePluginManifest(
            plugin_id="village.agent.skills",
            name="Village Agent Runtime Skills",
            version="1.0.0",
            protocol=PluginProtocol.VILLAGE_NATIVE,
            capabilities=("agent.skills",),
            tool_names=("skill",),
            priority=1_100,
        ),
        StaticToolProvider(
            {
                "skill": (
                    skill_tool,
                    skill_tool_handler,
                )
            }
        ),
    )
    registry.register(
        VillagePluginManifest(
            plugin_id="village.agent.turn-intent",
            name="Village Agent Turn Intent",
            version="1.0.0",
            protocol=PluginProtocol.VILLAGE_NATIVE,
            capabilities=("agent.turn_intent",),
            tool_names=(TURN_INTENT_TOOL_NAME,),
            priority=1_200,
        ),
        StaticToolProvider(
            {
                TURN_INTENT_TOOL_NAME: (
                    TURN_INTENT_TOOL_SCHEMA,
                    turn_intent_tool_handler,
                )
            }
        ),
    )
    return registry


__all__ = ["build_native_registry"]
