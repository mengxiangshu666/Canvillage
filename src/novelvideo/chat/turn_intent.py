"""Per-turn state for the Village turn intent freeze contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from novelvideo.agent_tools.turn_intent import (
    TURN_INTENT_RECEIPT_SCHEMA,
    TURN_INTENT_SCHEMA,
    TURN_INTENT_TOOL_NAME,
    freeze_turn_intent,
    infer_turn_intent_contract,
)


_READ_ONLY_TOOLS = frozenset(
    {
        "skill",
        TURN_INTENT_TOOL_NAME,
        "village_canvas_read_compact",
        "village_canvas_wait_receipt",
    }
)


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def tool_has_side_effect(tool_name: object, arguments: Mapping[str, Any]) -> bool:
    """Return whether one model-visible tool can create an external effect."""

    name = str(tool_name or "").strip()
    if not name or name in _READ_ONLY_TOOLS:
        return False
    if name.startswith(("freezone_get_", "village_canvas_get_", "village_canvas_list_")):
        return False
    if name == "village_canvas_capability":
        if str(arguments.get("action") or "").strip().lower() != "invoke":
            return False
        from novelvideo.agent_tools.village_canvas import capability_side_effect

        return capability_side_effect(arguments.get("capability_id")) != "read"
    return True


@dataclass(slots=True)
class TurnIntentRuntime:
    """Freeze one contract before the first side effect in one Agent turn."""

    prompt: str
    turn_id: str = ""
    project_id: str = ""
    canvas_id: str = ""
    contract: dict[str, Any] | None = None
    source: str = ""
    locked: bool = False
    side_effect_tools: list[str] = field(default_factory=list)

    def bind_turn_id(self, turn_id: object) -> None:
        self.turn_id = str(turn_id or "").strip()

    def freeze(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        result = freeze_turn_intent(
            arguments=arguments,
            previous=self.contract,
            locked=self.locked,
        )
        if result.get("ok") is True:
            contract = _mapping(result.get("contract"))
            if contract:
                self.contract = contract
                self.source = "explicit"
        return result

    def before_side_effect(
        self,
        tool_name: object,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        name = str(tool_name or "").strip()
        if not tool_has_side_effect(name, arguments):
            return None
        if self.contract is None:
            inferred = infer_turn_intent_contract(
                prompt=self.prompt,
                tool_name=name,
                tool_arguments=arguments,
                turn_id=self.turn_id,
                project_id=self.project_id,
                canvas_id=self.canvas_id,
            )
            result = freeze_turn_intent(
                arguments={"contract": inferred},
                previous=None,
                locked=False,
            )
            if result.get("ok") is not True:
                return result
            self.contract = _mapping(result.get("contract"))
            self.source = "runtime_side_effect_preflight"
        self.locked = True
        if name not in self.side_effect_tools:
            self.side_effect_tools.append(name)
        return None

    def receipt_for_tool(self, tool_name: object) -> dict[str, Any] | None:
        name = str(tool_name or "").strip()
        if not self.contract or name not in self.side_effect_tools:
            return None
        return self.receipt()

    def receipt(self) -> dict[str, Any]:
        if not self.contract:
            return {
                "schema": TURN_INTENT_RECEIPT_SCHEMA,
                "status": "not_frozen",
                "source": "",
                "contract_hash": "",
                "delivery_mode": "",
                "requirement_count": 0,
            }
        requirement_count = sum(
            len(self.contract.get(field) or [])
            for field in ("must", "forbid", "prefer", "confirmed_facts")
        )
        return {
            "schema": TURN_INTENT_RECEIPT_SCHEMA,
            "status": "locked" if self.locked else "frozen",
            "source": self.source,
            "contract_hash": str(self.contract.get("contract_hash") or ""),
            "delivery_mode": str(
                _mapping(self.contract.get("delivery")).get("mode") or ""
            ),
            "requirement_count": requirement_count,
            "side_effect_tools": list(self.side_effect_tools),
        }


__all__ = [
    "TURN_INTENT_SCHEMA",
    "TURN_INTENT_TOOL_NAME",
    "TurnIntentRuntime",
    "tool_has_side_effect",
]
