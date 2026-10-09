from __future__ import annotations

import json

import pytest

from novelvideo.agent_tools.native_registry import build_native_registry
from novelvideo.agent_tools.turn_intent import (
    TURN_INTENT_TOOL_NAME,
    TURN_INTENT_TOOL_SCHEMA,
    canonical_turn_intent_hash,
    freeze_turn_intent,
)
from novelvideo.agent_tools.village_canvas import capability_side_effect
from novelvideo.chat.turn_intent import TurnIntentRuntime, tool_has_side_effect


def _requirement(
    requirement_id: str,
    statement: str = "交付一个可验证结果",
) -> dict:
    return {
        "id": requirement_id,
        "statement": statement,
        "source": "user_message",
        "evidence": ["user:explicit"],
    }


def _contract(*, mode: str = "state_change", media_type: str | None = None) -> dict:
    return {
        "version": 1,
        "reference_resolution": {"mode": "current_turn"},
        "delivery": {
            "mode": mode,
            "media_type": media_type,
            "kind": "turn_result",
            "output": "verified result",
        },
        "must": [_requirement("must:deliver")],
        "forbid": [],
        "prefer": [],
        "confirmed_facts": [],
        "unresolved": [],
        "precedence": ["explicit_user_request"],
    }


def test_turn_intent_hash_ignores_key_order_and_contract_hash() -> None:
    first = _contract()
    second = {
        key: first[key]
        for key in reversed(list(first))
    }
    second["contract_hash"] = "caller-supplied-value"

    assert canonical_turn_intent_hash(first) == canonical_turn_intent_hash(second)


def test_turn_intent_same_hash_is_idempotent() -> None:
    frozen = freeze_turn_intent(arguments={"contract": _contract()})
    repeated = freeze_turn_intent(
        arguments={"contract": frozen["contract"]},
        previous=frozen["contract"],
    )

    assert frozen["ok"] is True
    assert repeated["ok"] is True
    assert repeated["action"] == "already_frozen"
    assert repeated["contract"] == frozen["contract"]
    assert repeated["contract_hash"] == frozen["contract_hash"]


def test_turn_intent_requirement_ids_are_unique_across_all_groups() -> None:
    contract = _contract()
    contract["forbid"] = [  # type: ignore[index]
        _requirement("must:deliver", "不要复用同一个 ID")
    ]

    result = freeze_turn_intent(arguments={"contract": contract})

    assert result["ok"] is False
    assert result["error_code"] == "village_turn_intent_invalid"
    assert any("globally unique" in issue for issue in result["issues"])


def test_turn_intent_media_delivery_channel_is_server_owned() -> None:
    """T-212：媒体通道由服务端决定——声明错通道自动归一为 async_artifact，不再拒绝。"""

    result = freeze_turn_intent(
        arguments={"contract": _contract(media_type="video")}
    )

    assert result["ok"] is True
    assert result["contract"]["delivery"]["mode"] == "async_artifact"

    # 连通道写成完全非法的值也一样归一（媒体只有 async_artifact 一条合法通道）
    weird = freeze_turn_intent(
        arguments={"contract": _contract(mode="carrier_pigeon", media_type="image")}
    )
    assert weird["ok"] is True
    assert weird["contract"]["delivery"]["mode"] == "async_artifact"

    # 非媒体交付不归一：mode 非法仍然报错
    non_media = freeze_turn_intent(arguments={"contract": _contract(mode="nope")})
    assert non_media["ok"] is False
    assert any("mode is invalid" in issue for issue in non_media["issues"])


def test_turn_intent_tool_schema_fully_describes_requirements() -> None:
    contract = TURN_INTENT_TOOL_SCHEMA["parameters"]["properties"]["contract"]
    requirement = contract["properties"]["must"]["items"]

    assert requirement["additionalProperties"] is False
    assert requirement["required"] == [
        "id",
        "statement",
        "source",
        "evidence",
    ]
    assert requirement["properties"]["evidence"]["items"] == {"type": "string"}


def test_turn_intent_correction_requires_previous_hash_before_lock() -> None:
    first = freeze_turn_intent(arguments={"contract": _contract()})
    changed = _contract()
    changed["must"][0]["statement"] = "交付修订后的结果"  # type: ignore[index]

    missing = freeze_turn_intent(
        arguments={"contract": changed},
        previous=first["contract"],
    )
    corrected = freeze_turn_intent(
        arguments={
            "contract": changed,
            "authoring_correction": {
                "previous_contract_hash": first["contract_hash"],
                "reason": "用户补充了验收条件",
            },
        },
        previous=first["contract"],
    )

    assert missing["error_code"] == "village_turn_intent_correction_required"
    assert corrected["ok"] is True
    assert corrected["action"] == "corrected"
    assert corrected["contract_hash"] != first["contract_hash"]


def test_turn_intent_cannot_change_or_freeze_after_lock() -> None:
    first = freeze_turn_intent(arguments={"contract": _contract()})
    changed = _contract()
    changed["must"][0]["statement"] = "副作用后改写"  # type: ignore[index]

    locked = freeze_turn_intent(
        arguments={
            "contract": changed,
            "authoring_correction": {
                "previous_contract_hash": first["contract_hash"],
                "reason": "too late",
            },
        },
        previous=first["contract"],
        locked=True,
    )
    retroactive = freeze_turn_intent(
        arguments={"contract": changed},
        locked=True,
    )

    assert locked["error_code"] == "village_turn_intent_locked"
    assert (
        retroactive["error_code"]
        == "village_turn_intent_retroactive_freeze_rejected"
    )


def test_runtime_freezes_before_the_first_side_effect() -> None:
    runtime = TurnIntentRuntime(
        prompt="生成一条 5 秒视频",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )

    denied = runtime.before_side_effect(
        "village_canvas_dispatch_action",
        {
            "request": "生成一条 5 秒视频",
            "commands": [{"type": "create_video_prompt_node"}],
        },
    )

    assert denied is None
    assert runtime.locked is True
    assert runtime.source == "runtime_side_effect_preflight"
    assert runtime.contract is not None
    assert runtime.contract["delivery"]["mode"] == "async_artifact"
    assert runtime.contract["delivery"]["media_type"] == "video"
    assert runtime.receipt()["status"] == "locked"
    assert runtime.receipt()["contract_hash"] == runtime.contract["contract_hash"]


def test_capability_side_effect_uses_the_registered_card_and_fails_closed() -> None:
    assert capability_side_effect("canvas.snapshot") == "read"
    assert capability_side_effect("canvas.compatibility.emit") == "canvas_write"
    assert capability_side_effect("made.up.capability") == "unknown"


def test_capability_invocation_freezes_only_for_real_side_effects() -> None:
    assert (
        tool_has_side_effect(
            "village_canvas_capability",
            {
                "action": "invoke",
                "capability_id": "canvas.snapshot",
            },
        )
        is False
    )
    assert (
        tool_has_side_effect(
            "village_canvas_capability",
            {
                "action": "invoke",
                "capability_id": "canvas.compatibility.emit",
            },
        )
        is True
    )
    assert (
        tool_has_side_effect(
            "village_canvas_capability",
            {
                "action": "invoke",
                "capability_id": "made.up.capability",
            },
        )
        is True
    )


async def test_native_registry_exposes_turn_intent_tool() -> None:
    registry = build_native_registry()
    names = {tool.name for tool in registry.list_tools()}
    payload = json.loads(
        await registry.invoke(
            TURN_INTENT_TOOL_NAME,
            {"contract": _contract()},
        )
    )

    assert TURN_INTENT_TOOL_NAME in names
    assert payload["ok"] is True
    assert payload["schema"] == "village_turn_intent.v1"
    assert len(payload["contract_hash"]) == 64


class _Registry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def invoke(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        return json.dumps({"ok": True, "tool": name})


class _DenyFence:
    def __init__(self, runtime: TurnIntentRuntime) -> None:
        self.runtime = runtime
        self.locked_at_denial: bool | None = None

    def skill_load_override(self, name: str, arguments: dict) -> None:
        return None

    def block_tool_call(self, name: str, arguments: dict) -> dict:
        self.locked_at_denial = self.runtime.locked
        return {
            "ok": False,
            "error_code": "test_paid_media_denied",
            "error": "blocked before registry",
        }

    def note_tool_invocation(self, name: str, arguments: dict) -> None:
        return None


@pytest.mark.asyncio
async def test_harness_freezes_and_projects_receipt_before_registry_result() -> None:
    from novelvideo.chat.village_harness import _build_tool

    runtime = TurnIntentRuntime(
        prompt="优化当前节点",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    registry = _Registry()
    tool = _build_tool(
        registry,
        "village_canvas_dispatch_action",
        {
            "name": "village_canvas_dispatch_action",
            "description": "test",
            "parameters": {
                "type": "object",
                "properties": {
                    "request": {"type": "string"},
                    "commands": {"type": "array", "items": {"type": "object"}},
                },
            },
        },
        turn_intent=runtime,
    )

    payload = json.loads(
        await tool.function(
            request="优化当前节点",
            commands=[{"type": "update_node_prompt"}],
        )
    )

    assert registry.calls == [
        (
            "village_canvas_dispatch_action",
            {
                "request": "优化当前节点",
                "commands": [{"type": "update_node_prompt"}],
            },
        )
    ]
    assert payload["turn_intent_receipt"]["status"] == "locked"
    assert payload["turn_intent_receipt"]["source"] == (
        "runtime_side_effect_preflight"
    )


@pytest.mark.asyncio
async def test_harness_freezes_before_skill_fence_denial_and_projects_receipt() -> None:
    from novelvideo.chat.village_harness import _build_tool

    runtime = TurnIntentRuntime(
        prompt="生成一条视频",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    fence = _DenyFence(runtime)
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
            },
        },
        skill_fence=fence,
        turn_intent=runtime,
    )

    payload = json.loads(
        await tool.function(
            action="invoke",
            capability_id="creative.generate_sketches",
            arguments={"episode": 1},
        )
    )

    assert fence.locked_at_denial is True
    assert registry.calls == []
    assert payload["error_code"] == "test_paid_media_denied"
    assert payload["turn_intent_receipt"]["status"] == "locked"
    assert payload["turn_intent_receipt"]["source"] == (
        "runtime_side_effect_preflight"
    )


@pytest.mark.asyncio
async def test_harness_turn_intent_tool_does_not_enter_registry() -> None:
    from novelvideo.chat.village_harness import _build_tool

    runtime = TurnIntentRuntime(prompt="交付结果", turn_id="turn-1")
    registry = _Registry()
    tool = _build_tool(
        registry,
        TURN_INTENT_TOOL_NAME,
        {
            "name": TURN_INTENT_TOOL_NAME,
            "description": "test",
            "parameters": {
                "type": "object",
                "properties": {"contract": {"type": "object"}},
                "required": ["contract"],
            },
        },
        turn_intent=runtime,
    )

    payload = json.loads(
        await tool.function(contract=_contract())
    )

    assert payload["ok"] is True
    assert payload["action"] == "frozen"
    assert payload["turn_intent_receipt"]["status"] == "frozen"
    assert payload["turn_intent_receipt"]["source"] == "explicit"
    assert len(payload["turn_intent_receipt"]["contract_hash"]) == 64
    assert registry.calls == []
