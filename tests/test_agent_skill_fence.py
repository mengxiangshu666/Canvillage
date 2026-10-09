from __future__ import annotations

import json

import pytest

from novelvideo.agent_tools.skills import SKILL_TOOL_SCHEMA
from novelvideo.chat.skill_fence import (
    PAID_MEDIA_FENCE_ERROR,
    SkillFenceRuntime,
    task_authorization_from_prompt,
)


def _prompt(authorization: dict | None = None) -> str:
    payload = {
        "v": 2,
        "request": "fence test",
        "task_authorization": authorization or {},
    }
    return (
        "[CANVAS_AGENT_REQUEST_V2]"
        + json.dumps(payload, ensure_ascii=False)
        + "[/CANVAS_AGENT_REQUEST_V2]"
    )


def _turn_authorization(*, turn_id: str = "turn-1") -> dict:
    return {
        "scope": "current_turn",
        "run_mode": "auto",
        "allow_structure": True,
        "allow_paid_media": True,
        "max_paid_starts": 1,
        "require_video_confirmation": False,
        "grant_id": "pmg_abc123",
        "turn_id": turn_id,
    }


def _skill_load(
    *,
    name: str = "village-canvas-one-click-film",
    flags: list[str] | None = None,
    action: str | None = None,
    reason: str = "",
) -> str:
    payload = {
        "schema": SKILL_TOOL_SCHEMA,
        "ok": True,
        "name": name,
        "sha256": "a" * 64,
        "bytes": 123,
        "activation": {
            "workflow": "one-click-film",
            "agents": [],
            "flags": flags or [],
            "fence": [],
        },
    }
    if action:
        payload["action"] = action
    if reason:
        payload["switch_reason"] = reason
    return json.dumps(payload, ensure_ascii=False)


def _unbound_skill_load(name: str) -> str:
    return json.dumps(
        {
            "schema": SKILL_TOOL_SCHEMA,
            "ok": True,
            "name": name,
            "sha256": "b" * 64,
            "bytes": 456,
            "activation_status": "unbound",
        },
        ensure_ascii=False,
    )


class _Registry:
    def __init__(self, result: str = '{"ok":true}') -> None:
        self.result = result
        self.calls: list[tuple[str, dict]] = []

    async def invoke(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        return self.result


def test_task_authorization_is_read_from_the_real_v2_envelope() -> None:
    authorization = _turn_authorization()

    assert task_authorization_from_prompt(_prompt(authorization)) == authorization
    assert task_authorization_from_prompt("plain prompt") == {}


def test_skill_route_receipt_carries_machine_readable_activation_contract() -> None:
    fence = SkillFenceRuntime(prompt=_prompt())
    fence.activate_skill_result(
        _skill_load(flags=["paid_media_requires_task_authorization"])
    )

    receipt = fence.route_receipt()

    assert receipt["activation"]["workflow"] == "one-click-film"
    assert receipt["activation"]["flags"] == ["paid_media_requires_task_authorization"]

    fence.activate_skill_result(
        _unbound_skill_load("village-canvas-aigc-knowledge"),
        arguments={"action": "switch"},
    )
    assert "activation" not in fence.route_receipt()


def test_skill_load_activates_paid_fence_and_list_does_not() -> None:
    loaded = SkillFenceRuntime(prompt=_prompt())
    loaded.bind_turn_id("turn-1")
    loaded.activate_skill_result(
        _skill_load(flags=["paid_media_requires_task_authorization"])
    )

    denied = loaded.block_tool_call(
        "village_canvas_capability",
        {
            "action": "invoke",
            "capability_id": "creative.generate_sketches",
            "arguments": {"episode": 1},
        },
    )
    assert denied is not None
    assert json.loads(denied)["error_code"] == PAID_MEDIA_FENCE_ERROR

    catalog_only = SkillFenceRuntime(prompt=_prompt())
    catalog_only.bind_turn_id("turn-1")
    catalog_only.activate_skill_result(
        _skill_load(
            flags=["paid_media_requires_task_authorization"],
            action="list",
        )
    )
    assert (
        catalog_only.block_tool_call(
            "village_canvas_capability",
            {
                "action": "invoke",
                "capability_id": "creative.generate_sketches",
                "arguments": {"episode": 1},
            },
        )
        is None
    )


def test_second_distinct_skill_load_is_already_active_without_receipt() -> None:
    fence = SkillFenceRuntime(prompt=_prompt())
    fence.activate_skill_result(_skill_load(name="village-canvas-3d-asset"))

    same = fence.skill_load_override(
        "skill",
        {"action": "load", "name": "village-canvas-3d-asset"},
    )
    different = fence.skill_load_override(
        "skill",
        {"action": "load", "name": "village-canvas-3d-scene-director"},
    )
    catalog = fence.skill_load_override("skill", {"action": "list"})

    assert same is None
    assert catalog is None
    assert different is not None
    payload = json.loads(different)
    assert payload["schema"] == SKILL_TOOL_SCHEMA
    assert payload["ok"] is True
    assert payload["action"] == "already_active"
    assert payload["name"] == "village-canvas-3d-asset"
    assert payload["requested_name"] == "village-canvas-3d-scene-director"
    assert "sha256" not in payload
    assert "bytes" not in payload
    assert fence.loaded_skills == ["village-canvas-3d-asset"]


def test_switch_replaces_the_single_owner_before_any_side_effect() -> None:
    fence = SkillFenceRuntime(prompt=_prompt())
    fence.activate_skill_result(
        _skill_load(
            name="village-canvas-canvas-director",
            flags=["paid_media_requires_task_authorization"],
        )
    )

    allowed = fence.skill_load_override(
        "skill",
        {
            "action": "switch",
            "name": "village-canvas-canvas-commands",
            "reason": "需要批量命令与撤销语义",
        },
    )
    assert allowed is None
    fence.activate_skill_result(
        _skill_load(
            name="village-canvas-canvas-commands",
            action="switch",
            reason="需要批量命令与撤销语义",
        )
    )

    assert fence.loaded_skills == ["village-canvas-canvas-commands"]
    assert fence.active_skill == "village-canvas-canvas-commands"
    assert fence.active_flags == set()
    assert fence.switch_receipts == [
        {
            "schema": "village_agent_skill_switch.v1",
            "from": "village-canvas-canvas-director",
            "to": "village-canvas-canvas-commands",
            "reason": "需要批量命令与撤销语义",
        }
    ]


def test_switch_is_rejected_after_any_side_effect() -> None:
    fence = SkillFenceRuntime(prompt=_prompt())
    fence.activate_skill_result(_skill_load(name="village-canvas-canvas-director"))
    fence.note_tool_invocation("village_canvas_dispatch_action", {})

    denied = fence.skill_load_override(
        "skill",
        {"action": "switch", "name": "village-canvas-canvas-commands"},
    )

    assert denied is not None
    payload = json.loads(denied)
    assert payload["ok"] is False
    assert payload["error_code"] == "skill_switch_after_side_effect"
    assert payload["active_skill"] == "village-canvas-canvas-director"
    assert fence.loaded_skills == ["village-canvas-canvas-director"]


def test_switch_requires_a_real_load_even_when_preactivated() -> None:
    fence = SkillFenceRuntime(prompt=_prompt())
    fence.pre_activate(
        "village-canvas-canvas-commands",
        {
            "schema": "village_agent_skill_route.v1",
            "pre_activated_skill": "village-canvas-canvas-commands",
            "load_receipt": False,
        },
    )

    denied = fence.skill_load_override(
        "skill",
        {"action": "switch", "name": "village-canvas-canvas-director"},
    )

    assert denied is not None
    payload = json.loads(denied)
    assert payload["error_code"] == "skill_switch_requires_load"
    assert payload["pre_activated_skill"] == "village-canvas-canvas-commands"
    assert fence.loaded_skills == []


def test_skill_load_reserves_the_turn_before_the_first_result_arrives() -> None:
    fence = SkillFenceRuntime(prompt=_prompt())

    first = fence.skill_load_override(
        "skill",
        {"action": "load", "name": "village-canvas-aigc-knowledge"},
    )
    same_in_flight = fence.skill_load_override(
        "skill",
        {"action": "load", "name": "village-canvas-aigc-knowledge"},
    )
    different_in_flight = fence.skill_load_override(
        "skill",
        {"action": "load", "name": "village-canvas-prompt-director"},
    )

    assert first is None
    assert json.loads(same_in_flight)["action"] == "already_active"
    assert (
        json.loads(different_in_flight)["requested_name"]
        == "village-canvas-prompt-director"
    )

    fence.finish_skill_load("village-canvas-aigc-knowledge")
    retry = fence.skill_load_override(
        "skill",
        {"action": "load", "name": "village-canvas-aigc-knowledge"},
    )
    assert retry is None


def test_unbound_skill_still_consumes_the_single_activation_slot() -> None:
    fence = SkillFenceRuntime(prompt=_prompt())
    fence.activate_skill_result(_unbound_skill_load("village-canvas-aigc-knowledge"))

    different = fence.skill_load_override(
        "skill",
        {"action": "load", "name": "village-canvas-prompt-director"},
    )

    assert fence.loaded_skills == ["village-canvas-aigc-knowledge"]
    assert json.loads(different)["action"] == "already_active"


@pytest.mark.asyncio
async def test_harness_fence_denies_paid_capability_before_registry_invocation() -> (
    None
):
    from novelvideo.chat.village_harness import _build_tool

    fence = SkillFenceRuntime(prompt=_prompt())
    fence.bind_turn_id("turn-1")
    fence.activate_skill_result(
        _skill_load(flags=["paid_media_requires_task_authorization"])
    )
    registry = _Registry()
    tool = _build_tool(
        registry,
        "village_canvas_capability",
        {
            "name": "village_canvas_capability",
            "description": "test",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "capability_id": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["action"],
            },
        },
        skill_fence=fence,
    )

    result = await tool.function(
        action="invoke",
        capability_id="creative.generate_sketches",
        arguments={"episode": 1},
    )

    assert json.loads(result)["error_code"] == PAID_MEDIA_FENCE_ERROR
    assert registry.calls == []


@pytest.mark.asyncio
async def test_preactivation_does_not_grant_paid_media_fence() -> None:
    from novelvideo.chat.village_harness import _build_tool

    fence = SkillFenceRuntime(prompt=_prompt())
    fence.bind_turn_id("turn-1")
    fence.pre_activate(
        "village-canvas-one-click-film",
        {
            "schema": "village_agent_skill_route.v1",
            "pre_activated_skill": "village-canvas-one-click-film",
            "load_receipt": False,
        },
    )
    registry = _Registry()
    tool = _build_tool(
        registry,
        "village_canvas_capability",
        {
            "name": "village_canvas_capability",
            "description": "test",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "capability_id": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["action"],
            },
        },
        skill_fence=fence,
    )

    result = await tool.function(
        action="invoke",
        capability_id="creative.generate_sketches",
        arguments={"episode": 1},
    )

    payload = json.loads(result)
    assert payload["error_code"] == PAID_MEDIA_FENCE_ERROR
    assert payload["skill_fence"]["pre_activated"] is True
    assert payload["skill_fence"]["skills"] == ["village-canvas-one-click-film"]
    assert registry.calls == []


@pytest.mark.asyncio
async def test_harness_fence_passes_read_and_matching_bound_grant() -> None:
    from novelvideo.chat.village_harness import _build_tool

    authorization = _turn_authorization()
    fence = SkillFenceRuntime(prompt=_prompt(authorization))
    fence.bind_turn_id("turn-1")
    fence.activate_skill_result(
        _skill_load(flags=["paid_media_requires_task_authorization"])
    )
    registry = _Registry()
    tool = _build_tool(
        registry,
        "village_canvas_capability",
        {
            "name": "village_canvas_capability",
            "description": "test",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "capability_id": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["action"],
            },
        },
        skill_fence=fence,
    )

    read = await tool.function(
        action="invoke",
        capability_id="script.get",
        arguments={"episode": 1},
    )
    paid = await tool.function(
        action="invoke",
        capability_id="creative.generate_sketches",
        arguments={"episode": 1, "task_authorization": authorization},
    )

    assert json.loads(read)["ok"] is True
    assert json.loads(paid)["ok"] is True
    assert [name for name, _ in registry.calls] == [
        "village_canvas_capability",
        "village_canvas_capability",
    ]


@pytest.mark.asyncio
async def test_harness_fence_uses_server_bound_grant_when_model_omits_it() -> None:
    from novelvideo.chat.village_harness import _build_tool

    authorization = _turn_authorization()
    fence = SkillFenceRuntime(prompt=_prompt(authorization))
    fence.bind_turn_id("turn-1")
    fence.activate_skill_result(
        _skill_load(flags=["paid_media_requires_task_authorization"])
    )
    registry = _Registry()
    tool = _build_tool(
        registry,
        "village_canvas_capability",
        {
            "name": "village_canvas_capability",
            "description": "test",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "capability_id": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["action"],
            },
        },
        skill_fence=fence,
    )

    paid = await tool.function(
        action="invoke",
        capability_id="creative.generate_sketches",
        arguments={"episode": 1},
    )

    assert json.loads(paid)["ok"] is True
    assert [name for name, _ in registry.calls] == ["village_canvas_capability"]


@pytest.mark.asyncio
async def test_harness_fence_still_rejects_a_different_model_grant() -> None:
    from novelvideo.chat.village_harness import _build_tool

    authorization = _turn_authorization()
    fence = SkillFenceRuntime(prompt=_prompt(authorization))
    fence.bind_turn_id("turn-1")
    fence.activate_skill_result(
        _skill_load(flags=["paid_media_requires_task_authorization"])
    )
    registry = _Registry()
    tool = _build_tool(
        registry,
        "village_canvas_capability",
        {
            "name": "village_canvas_capability",
            "description": "test",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "capability_id": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["action"],
            },
        },
        skill_fence=fence,
    )

    denied = await tool.function(
        action="invoke",
        capability_id="creative.generate_sketches",
        arguments={
            "episode": 1,
            "task_authorization": {
                **authorization,
                "grant_id": "pmg_other",
            },
        },
    )

    assert json.loads(denied)["error_code"] == PAID_MEDIA_FENCE_ERROR
    assert registry.calls == []


@pytest.mark.asyncio
async def test_harness_activates_fence_only_from_real_skill_load_result() -> None:
    from novelvideo.chat.village_harness import _build_tool

    registry = _Registry(_skill_load(flags=["paid_media_requires_task_authorization"]))
    fence = SkillFenceRuntime(prompt=_prompt())
    fence.bind_turn_id("turn-1")
    skill_tool = _build_tool(
        registry,
        "skill",
        {
            "name": "skill",
            "description": "test",
            "parameters": {"type": "object", "properties": {}},
        },
        skill_fence=fence,
    )
    capability_tool = _build_tool(
        registry,
        "village_canvas_capability",
        {
            "name": "village_canvas_capability",
            "description": "test",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "capability_id": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["action"],
            },
        },
        skill_fence=fence,
    )

    loaded = await skill_tool.function()
    denied = await capability_tool.function(
        action="invoke",
        capability_id="creative.generate_sketches",
        arguments={"episode": 1},
    )

    assert json.loads(loaded)["ok"] is True
    assert json.loads(denied)["error_code"] == PAID_MEDIA_FENCE_ERROR
    assert len(registry.calls) == 1


@pytest.mark.asyncio
async def test_harness_short_circuits_second_distinct_skill_before_registry() -> None:
    from novelvideo.chat.village_harness import _build_tool

    registry = _Registry(_unbound_skill_load("village-canvas-3d-asset"))
    fence = SkillFenceRuntime(prompt=_prompt())
    skill_tool = _build_tool(
        registry,
        "skill",
        {
            "name": "skill",
            "description": "test",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "name": {"type": "string"},
                },
            },
        },
        skill_fence=fence,
    )

    first = json.loads(
        await skill_tool.function(
            action="load",
            name="village-canvas-3d-asset",
        )
    )
    second = json.loads(
        await skill_tool.function(
            action="load",
            name="village-canvas-3d-scene-director",
        )
    )

    assert first["ok"] is True
    assert second["action"] == "already_active"
    assert "sha256" not in second
    assert len(registry.calls) == 1


@pytest.mark.asyncio
async def test_harness_turns_unexpected_tool_errors_into_receipts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.chat import village_harness
    from novelvideo.chat.village_harness import _build_tool

    def fail_normalization(*_: object, **__: object) -> object:
        raise RuntimeError("normalization exploded")

    monkeypatch.setattr(
        village_harness,
        "_normalize_schema_scalars",
        fail_normalization,
    )
    tool = _build_tool(
        _Registry(),
        "village_canvas_capability",
        {
            "name": "village_canvas_capability",
            "description": "test",
            "parameters": {"type": "object", "properties": {}},
        },
    )

    payload = json.loads(await tool.function())

    assert payload["ok"] is False
    assert payload["error_code"] == "tool_invocation_failed"
    assert "normalization exploded" in payload["error"]
    assert tool.max_retries == 1


def test_write_tools_do_not_retry_without_an_idempotency_contract() -> None:
    from novelvideo.chat.village_harness import _build_tool

    tool = _build_tool(
        _Registry(),
        "village_canvas_dispatch_action",
        {
            "name": "village_canvas_dispatch_action",
            "description": "test",
            "parameters": {"type": "object", "properties": {}},
        },
    )

    assert tool.max_retries == 0
