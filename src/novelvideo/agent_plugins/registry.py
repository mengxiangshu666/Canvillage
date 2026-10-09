"""Small, strict plugin registry for the Village Canvas Agent runtime."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class PluginProtocol(StrEnum):
    VILLAGE_NATIVE = "village-native"
    DSH_CORDIS = "dsh-cordis"
    MCP = "mcp"
    OPENAPI = "openapi"
    ACP = "acp"


class PluginState(StrEnum):
    ACTIVE = "active"
    DEGRADED = "degraded"
    STOPPED = "stopped"


class PluginRegistryError(RuntimeError):
    pass


class PluginConflictError(PluginRegistryError):
    pass


class PluginDependencyError(PluginRegistryError):
    pass


@dataclass(frozen=True, slots=True)
class PluginTool:
    name: str
    schema: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class VillagePluginManifest:
    plugin_id: str
    name: str
    version: str
    protocol: PluginProtocol
    capabilities: tuple[str, ...]
    tool_names: tuple[str, ...]
    requires: tuple[str, ...] = ()
    priority: int = 100

    def __post_init__(self) -> None:
        plugin_id = self.plugin_id.strip()
        if not plugin_id or len(plugin_id) > 128:
            raise ValueError("plugin_id must contain 1-128 characters")
        if not self.name.strip() or not self.version.strip():
            raise ValueError("plugin name and version are required")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("plugin capabilities must be unique")
        if not self.tool_names or len(set(self.tool_names)) != len(self.tool_names):
            raise ValueError("plugin tool_names must be non-empty and unique")
        if plugin_id in self.requires:
            raise ValueError("a plugin cannot require itself")


@dataclass(frozen=True, slots=True)
class PluginHealth:
    plugin_id: str
    state: PluginState
    healthy: bool
    tool_count: int
    call_count: int
    failure_count: int
    last_error: str | None


class ToolProvider(Protocol):
    def list_tools(self) -> Sequence[PluginTool]: ...

    def invoke(self, name: str, arguments: Mapping[str, Any]) -> Any: ...


def _run_awaitable_off_loop(awaitable: Awaitable[Any]) -> Any:
    """Run a provider coroutine on a private loop inside the worker thread.

    Native Village tools call back into the local ``8784`` agent API with a
    bounded, blocking HTTP request.  Awaiting such a handler on the caller's
    event loop deadlocks that loop: the loop is blocked on a request only it
    could serve.  Running the coroutine on the worker thread's own loop keeps
    the contract identical to a synchronous handler while leaving the caller
    loop free to answer the tool's request.
    """

    return asyncio.run(awaitable)


@dataclass(slots=True)
class StaticToolProvider:
    """Expose an existing tool dictionary without copying its handlers."""

    tools: Mapping[str, tuple[Mapping[str, Any], Callable[[dict[str, Any]], Any]]]

    def list_tools(self) -> tuple[PluginTool, ...]:
        return tuple(
            PluginTool(name=name, schema=schema)
            for name, (schema, _handler) in sorted(self.tools.items())
        )

    def invoke(self, name: str, arguments: Mapping[str, Any]) -> Any:
        item = self.tools.get(name)
        if item is None:
            raise KeyError(name)
        _schema, handler = item
        return handler(dict(arguments))


@dataclass(slots=True)
class _RuntimePlugin:
    manifest: VillagePluginManifest
    provider: ToolProvider
    tools: dict[str, PluginTool]
    state: PluginState = PluginState.ACTIVE
    call_count: int = 0
    failure_count: int = 0
    last_error: str | None = None


@dataclass(slots=True)
class PluginRegistry:
    """Thread-safe capability and tool registry with atomic hot replacement."""

    _plugins: dict[str, _RuntimePlugin] = field(default_factory=dict, init=False)
    _tool_owners: dict[str, str] = field(default_factory=dict, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)

    def register(
        self,
        manifest: VillagePluginManifest,
        provider: ToolProvider,
        *,
        replace: bool = False,
    ) -> None:
        tools = {tool.name: tool for tool in provider.list_tools()}
        if set(tools) != set(manifest.tool_names):
            raise ValueError("manifest tool_names must exactly match provider tools")

        with self._lock:
            existing = self._plugins.get(manifest.plugin_id)
            if existing is not None and not replace:
                raise PluginConflictError(f"plugin already registered: {manifest.plugin_id}")
            missing = [plugin_id for plugin_id in manifest.requires if plugin_id not in self._plugins]
            if missing:
                raise PluginDependencyError(f"missing plugin dependencies: {', '.join(missing)}")

            replaced_tools = set(existing.tools) if existing is not None else set()
            conflicts = {
                name: owner
                for name in tools
                if (owner := self._tool_owners.get(name)) is not None
                and owner != manifest.plugin_id
                and name not in replaced_tools
            }
            if conflicts:
                details = ", ".join(f"{name} ({owner})" for name, owner in sorted(conflicts.items()))
                raise PluginConflictError(f"tool name conflict: {details}")

            if existing is not None:
                for name in existing.tools:
                    self._tool_owners.pop(name, None)
                existing.state = PluginState.STOPPED
            runtime = _RuntimePlugin(manifest=manifest, provider=provider, tools=tools)
            self._plugins[manifest.plugin_id] = runtime
            for name in tools:
                self._tool_owners[name] = manifest.plugin_id

    def unregister(self, plugin_id: str) -> bool:
        with self._lock:
            runtime = self._plugins.pop(plugin_id, None)
            if runtime is None:
                return False
            runtime.state = PluginState.STOPPED
            for name in runtime.tools:
                self._tool_owners.pop(name, None)
            return True

    def manifests(self) -> tuple[VillagePluginManifest, ...]:
        with self._lock:
            return tuple(
                runtime.manifest
                for runtime in sorted(
                    self._plugins.values(),
                    key=lambda item: (-item.manifest.priority, item.manifest.plugin_id),
                )
            )

    def capabilities(self) -> dict[str, tuple[str, ...]]:
        with self._lock:
            result: dict[str, list[str]] = {}
            for runtime in self._plugins.values():
                for capability in runtime.manifest.capabilities:
                    result.setdefault(capability, []).append(runtime.manifest.plugin_id)
            return {key: tuple(sorted(value)) for key, value in sorted(result.items())}

    def list_tools(self) -> tuple[PluginTool, ...]:
        with self._lock:
            tools = [tool for runtime in self._plugins.values() for tool in runtime.tools.values()]
            return tuple(sorted(tools, key=lambda tool: tool.name))

    async def invoke(self, name: str, arguments: Mapping[str, Any]) -> Any:
        with self._lock:
            owner = self._tool_owners.get(name)
            runtime = self._plugins.get(owner or "")
            if runtime is None or runtime.state is PluginState.STOPPED:
                raise KeyError(name)
            provider = runtime.provider
            runtime.call_count += 1

        try:
            result = await asyncio.to_thread(provider.invoke, name, arguments)
            if inspect.isawaitable(result):
                result = await asyncio.to_thread(_run_awaitable_off_loop, result)
        except Exception as exc:
            with self._lock:
                runtime.failure_count += 1
                runtime.last_error = str(exc)[:500]
                runtime.state = PluginState.DEGRADED
            raise

        with self._lock:
            runtime.last_error = None
            runtime.state = PluginState.ACTIVE
        return result

    def health(self) -> tuple[PluginHealth, ...]:
        with self._lock:
            return tuple(
                PluginHealth(
                    plugin_id=runtime.manifest.plugin_id,
                    state=runtime.state,
                    healthy=runtime.state is PluginState.ACTIVE,
                    tool_count=len(runtime.tools),
                    call_count=runtime.call_count,
                    failure_count=runtime.failure_count,
                    last_error=runtime.last_error,
                )
                for runtime in sorted(self._plugins.values(), key=lambda item: item.manifest.plugin_id)
            )
