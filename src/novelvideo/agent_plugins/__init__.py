"""Agent plugin contracts shared by native, MCP, ACP, and future adapters."""

from .registry import (
    PluginConflictError,
    PluginDependencyError,
    PluginHealth,
    PluginProtocol,
    PluginRegistry,
    PluginState,
    PluginTool,
    StaticToolProvider,
    VillagePluginManifest,
)

__all__ = [
    "PluginConflictError",
    "PluginDependencyError",
    "PluginHealth",
    "PluginProtocol",
    "PluginRegistry",
    "PluginState",
    "PluginTool",
    "StaticToolProvider",
    "VillagePluginManifest",
]
