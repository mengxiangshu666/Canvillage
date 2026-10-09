from __future__ import annotations

import asyncio
import time

import pytest

from novelvideo.agent_plugins import (
    PluginConflictError,
    PluginDependencyError,
    PluginProtocol,
    PluginRegistry,
    PluginState,
    StaticToolProvider,
    VillagePluginManifest,
)


def _manifest(
    plugin_id: str,
    *tool_names: str,
    requires: tuple[str, ...] = (),
) -> VillagePluginManifest:
    return VillagePluginManifest(
        plugin_id=plugin_id,
        name=plugin_id,
        version="1.0.0",
        protocol=PluginProtocol.VILLAGE_NATIVE,
        capabilities=(f"capability.{plugin_id}",),
        tool_names=tuple(tool_names),
        requires=requires,
    )


def _provider(name: str, handler):
    return StaticToolProvider(
        {
            name: (
                {
                    "name": name,
                    "description": "test tool",
                    "parameters": {"type": "object", "properties": {}},
                },
                handler,
            )
        }
    )


def test_plugin_registry_registers_capabilities_and_invokes_tools():
    registry = PluginRegistry()
    registry.register(_manifest("native", "canvas_read"), _provider("canvas_read", lambda args: args))

    assert [tool.name for tool in registry.list_tools()] == ["canvas_read"]
    assert registry.capabilities() == {"capability.native": ("native",)}
    assert asyncio.run(registry.invoke("canvas_read", {"canvas_id": "canvas-a"})) == {
        "canvas_id": "canvas-a"
    }
    assert registry.health()[0].call_count == 1
    assert registry.health()[0].state is PluginState.ACTIVE


def test_plugin_registry_rejects_missing_dependencies_and_tool_conflicts():
    registry = PluginRegistry()
    with pytest.raises(PluginDependencyError):
        registry.register(
            _manifest("dependent", "dependent_tool", requires=("base",)),
            _provider("dependent_tool", lambda _args: None),
        )

    registry.register(_manifest("base", "shared"), _provider("shared", lambda _args: "base"))
    with pytest.raises(PluginConflictError):
        registry.register(_manifest("other", "shared"), _provider("shared", lambda _args: "other"))


def test_plugin_registry_hot_replaces_one_plugin_atomically():
    registry = PluginRegistry()
    registry.register(_manifest("native", "tool_v1"), _provider("tool_v1", lambda _args: "v1"))
    registry.register(
        _manifest("native", "tool_v2"),
        _provider("tool_v2", lambda _args: "v2"),
        replace=True,
    )

    assert [tool.name for tool in registry.list_tools()] == ["tool_v2"]
    with pytest.raises(KeyError):
        asyncio.run(registry.invoke("tool_v1", {}))
    assert asyncio.run(registry.invoke("tool_v2", {})) == "v2"


def test_plugin_registry_isolates_failures_and_recovers_health():
    registry = PluginRegistry()
    calls = {"fail": True}

    def handler(_args):
        if calls["fail"]:
            raise RuntimeError("plugin boom")
        return "ok"

    registry.register(_manifest("native", "unstable"), _provider("unstable", handler))
    with pytest.raises(RuntimeError, match="plugin boom"):
        asyncio.run(registry.invoke("unstable", {}))
    failed = registry.health()[0]
    assert failed.state is PluginState.DEGRADED
    assert failed.failure_count == 1
    assert failed.last_error == "plugin boom"

    calls["fail"] = False
    assert asyncio.run(registry.invoke("unstable", {})) == "ok"
    recovered = registry.health()[0]
    assert recovered.state is PluginState.ACTIVE
    assert recovered.failure_count == 1
    assert recovered.last_error is None


def test_async_tool_handler_does_not_block_the_caller_event_loop():
    """A blocking async handler must run off-loop or the agent API deadlocks.

    Native tools call back into the local agent API over HTTP.  If the handler
    coroutine is awaited on the caller's loop, that loop is blocked on a
    request only it can serve, so the ticker below cannot advance while the
    handler waits.
    """

    registry = PluginRegistry()
    ticks = 0

    async def blocking_handler(_args):
        # Sampled after the block: an off-loop handler lets the ticker finish
        # every tick while it waits, an on-loop handler observes none.
        time.sleep(0.6)
        return {"ticks_observed": ticks}

    registry.register(_manifest("native", "slow_read"), _provider("slow_read", blocking_handler))

    async def run():
        nonlocal ticks

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        ticker_task = asyncio.create_task(ticker())
        result = await registry.invoke("slow_read", {})
        ticker_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await ticker_task
        return result

    result = asyncio.run(run())
    # ~60 ticks fit in the 0.6s wait; an on-loop handler would report 0.
    assert result["ticks_observed"] >= 20


def test_village_canvas_mcp_uses_the_indexed_native_plugin_registry():
    from novelvideo.chat import village_canvas_mcp

    manifests = village_canvas_mcp.PLUGIN_REGISTRY.manifests()
    assert [manifest.plugin_id for manifest in manifests] == [
        "village.agent.turn-intent",
        "village.agent.skills",
        "village.canvas.native",
    ]
    turn_intent_manifest = next(
        manifest
        for manifest in manifests
        if manifest.plugin_id == "village.agent.turn-intent"
    )
    assert turn_intent_manifest.capabilities == ("agent.turn_intent",)
    canvas_manifest = next(
        manifest for manifest in manifests if manifest.plugin_id == "village.canvas.native"
    )
    assert canvas_manifest.protocol is PluginProtocol.VILLAGE_NATIVE
    assert {
        "canvas.read.compact",
        "canvas.commands.apply",
        "canvas.receipts.wait",
    } <= set(canvas_manifest.capabilities)
    assert {
        "skill",
        "village_canvas_capability",
        "village_canvas_dispatch_action",
    } <= {tool.name for tool in village_canvas_mcp.PLUGIN_REGISTRY.list_tools()}
    assert "village_canvas_apply_commands" not in {
        tool.name for tool in village_canvas_mcp.PLUGIN_REGISTRY.list_tools()
    }


def test_full_compatibility_registry_keeps_low_level_canvas_writer(monkeypatch):
    monkeypatch.setenv("VILLAGE_CANVAS_TOOL_EXPOSURE_MODE", "full")

    from novelvideo.agent_tools import build_native_registry

    registry = build_native_registry()
    tool_names = {tool.name for tool in registry.list_tools()}

    assert {
        "village_canvas_read_compact",
        "village_canvas_apply_commands",
        "village_canvas_wait_receipt",
        "village_canvas_dispatch_action",
    } <= tool_names
    assert "village_canvas_capability" not in tool_names
