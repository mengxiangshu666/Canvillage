"""Runtime enforcement for flags declared by loaded Agent skills.

The skill document remains the source of intent. This module only executes the
small machine-readable subset that already has a real product policy, so a
loaded skill can tighten the current turn before a paid handler is entered.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from novelvideo.agent_tools.tool_contract import tool_result
from novelvideo.chat.approval_store import paid_media_turn_grant_max_starts

PAID_MEDIA_AUTH_FLAG = "paid_media_requires_task_authorization"
PAID_MEDIA_FENCE_ERROR = "skill_fence_paid_media_requires_task_authorization"

_V2_REQUEST_RE = re.compile(
    r"\[CANVAS_AGENT_REQUEST_V2\]\s*(.*?)\s*"
    r"\[/CANVAS_AGENT_REQUEST_V2\]",
    re.IGNORECASE | re.DOTALL,
)


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def task_authorization_from_prompt(prompt: object) -> dict[str, Any]:
    match = _V2_REQUEST_RE.search(str(prompt or ""))
    if match is None:
        return {}
    payload = _mapping(match.group(1))
    return _mapping(payload.get("task_authorization"))


def _bound_turn_grant(
    authorization: object,
    *,
    turn_id: str,
) -> str:
    """Return the grant id only for an eligible grant bound to this turn."""

    value = authorization if isinstance(authorization, Mapping) else {}
    grant_id = str(value.get("grant_id") or "").strip()
    eligible = paid_media_turn_grant_max_starts(value) > 0
    if (
        not eligible
        or not turn_id
        or grant_id == ""
        or not grant_id.startswith("pmg_")
        or str(value.get("turn_id") or "").strip() != turn_id
    ):
        return ""
    return grant_id


def _capability_requires_paid_media(capability_id: str) -> bool:
    if not capability_id:
        return False
    # Import lazily so tests that only exercise the turn policy do not need to
    # execute the full canvas tool bundle.
    from novelvideo.agent_tools.village_canvas import capability_requires_paid_media

    return capability_requires_paid_media(capability_id)


def _dispatch_requests_paid_media(arguments: Mapping[str, Any]) -> bool:
    action_profile = _mapping(arguments.get("action_profile"))
    if action_profile.get("contains_paid_media") is True:
        return True
    if str(arguments.get("generation_node_id") or "").strip():
        return True
    commands = arguments.get("commands")
    if not isinstance(commands, list):
        return False
    for command in commands:
        if not isinstance(command, Mapping):
            continue
        command_type = str(command.get("type") or "").strip()
        if command_type in {"create_video_prompt_node", "run_canvas_node"}:
            return True
        node_data = _mapping(command.get("node_data"))
        node_type = str(
            command.get("node_type")
            or command.get("nodeType")
            or node_data.get("nodeType")
            or node_data.get("type")
            or ""
        ).casefold()
        if node_type in {
            "imagegennode",
            "imageeditnode",
            "videonode",
            "audionode",
        }:
            return True
        if any(
            key in command or key in node_data
            for key in (
                "aspect_ratio",
                "duration_sec",
                "generation_mode",
                "video_quality",
            )
        ):
            return True
    return False


def _paid_invocation(
    tool_name: object,
    arguments: Mapping[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """Return ``(capability_id, authorization)`` for a paid media route."""

    name = str(tool_name or "").strip()
    if name == "village_canvas_capability":
        if str(arguments.get("action") or "").strip().lower() != "invoke":
            return None
        capability_id = str(arguments.get("capability_id") or "").strip()
        if not _capability_requires_paid_media(capability_id):
            return None
        forwarded = _mapping(arguments.get("arguments"))
        return capability_id, _mapping(forwarded.get("task_authorization"))

    from novelvideo.chat.village_turn_policy import is_village_canvas_paid_media_tool

    if is_village_canvas_paid_media_tool(name):
        return "", _mapping(arguments.get("task_authorization"))
    if name == "village_canvas_dispatch_action" and _dispatch_requests_paid_media(
        arguments
    ):
        return "", _mapping(arguments.get("task_authorization"))
    return None


@dataclass(slots=True)
class SkillFenceRuntime:
    """Per-turn state built from the prompt and successful skill loads."""

    prompt: str = ""
    turn_id: str = ""
    active_flags: set[str] = field(default_factory=set)
    loaded_skills: list[str] = field(default_factory=list)
    pre_activated_skill: str = ""
    pre_activated_flags: set[str] = field(default_factory=set)
    activation_contract: dict[str, Any] = field(default_factory=dict)
    route_receipt_data: dict[str, Any] = field(default_factory=dict)
    switch_receipts: list[dict[str, Any]] = field(default_factory=list)
    _turn_authorization: dict[str, Any] = field(init=False, repr=False)
    _pending_skill: str = field(default="", init=False, repr=False)
    _pending_skill_action: str = field(default="", init=False, repr=False)
    _pending_switch_reason: str = field(default="", init=False, repr=False)
    _side_effect_started: bool = field(default=False, init=False, repr=False)
    _side_effect_tools: list[str] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._turn_authorization = task_authorization_from_prompt(self.prompt)

    def bind_turn_id(self, turn_id: object) -> None:
        self.turn_id = str(turn_id or "").strip()

    def pre_activate(self, skill_name: object, receipt: Mapping[str, Any]) -> None:
        """Record explanatory route context without creating a load receipt."""

        name = str(skill_name or "").strip()
        if not name:
            return
        self.pre_activated_skill = name
        try:
            from novelvideo.agent_tools.skills import load_agent_skill

            skill = load_agent_skill(name)
        except Exception:  # noqa: BLE001 - routing metadata must not block the turn
            self.pre_activated_flags = set()
            self.activation_contract = {}
        else:
            self.pre_activated_flags = set(
                skill.activation.flags if skill.activation is not None else ()
            )
            self.activation_contract = (
                skill.activation.public() if skill.activation is not None else {}
            )
        self.route_receipt_data = dict(receipt)
        self.route_receipt_data.setdefault("schema", "village_agent_skill_route.v1")
        self.route_receipt_data["pre_activated_skill"] = name
        self.route_receipt_data.setdefault("permissions_granted", [])
        self.route_receipt_data.setdefault("load_receipt", False)

    @property
    def active_skill(self) -> str:
        if self.loaded_skills:
            return self.loaded_skills[-1]
        return self.pre_activated_skill

    def route_receipt(self) -> dict[str, Any]:
        receipt = dict(self.route_receipt_data)
        receipt.update(
            {
                "active_skill": self.active_skill,
                "loaded_skills": list(self.loaded_skills),
                "switch_receipts": [dict(item) for item in self.switch_receipts],
                "permissions_granted": [],
                "load_receipt": False,
                **(
                    {"activation": dict(self.activation_contract)}
                    if self.activation_contract
                    else {}
                ),
            }
        )
        return receipt

    def note_tool_invocation(
        self,
        tool_name: object,
        arguments: Mapping[str, Any],
    ) -> None:
        """Remember the first side-effect-capable tool entered this turn."""

        name = str(tool_name or "").strip()
        if not name or name == "skill":
            return
        if name == "village_canvas_read_compact" or name.startswith("freezone_get_"):
            return
        if name == "village_canvas_capability":
            action = str(arguments.get("action") or "").strip().lower()
            if action != "invoke":
                return
        self._side_effect_started = True
        if name not in self._side_effect_tools:
            self._side_effect_tools.append(name)

    def activate_skill_result(
        self,
        result: object,
        *,
        arguments: Mapping[str, Any] | None = None,
    ) -> None:
        """Activate flags only from a successful real ``skill load`` envelope."""

        payload = _mapping(result)
        action = str(payload.get("action") or "load").strip().lower()
        if (
            str(payload.get("schema") or "") != "village_agent_skill.v1"
            or payload.get("ok") is not True
            or action == "list"
            or action == "already_active"
        ):
            return
        name = str(payload.get("name") or "").strip()
        if not name:
            return
        previous = self.loaded_skills[-1] if self.loaded_skills else ""
        if action == "switch":
            self.loaded_skills = [name]
            self.active_flags.clear()
            reason = str(
                payload.get("switch_reason")
                or (arguments or {}).get("reason")
                or self._pending_switch_reason
                or ""
            ).strip()[:240]
            self.switch_receipts.append(
                {
                    "schema": "village_agent_skill_switch.v1",
                    "from": previous,
                    "to": name,
                    "reason": reason,
                }
            )
        elif name not in self.loaded_skills:
            self.loaded_skills.append(name)
        if name == self._pending_skill:
            self._pending_skill = ""
            self._pending_skill_action = ""
            self._pending_switch_reason = ""
        activation = _mapping(payload.get("activation"))
        self.activation_contract = activation
        flags = activation.get("flags")
        if not isinstance(flags, list):
            return
        self.active_flags.update(
            str(flag).strip() for flag in flags if str(flag).strip()
        )

    def finish_skill_load(self, name: object) -> None:
        if str(name or "").strip() == self._pending_skill:
            self._pending_skill = ""
            self._pending_skill_action = ""
            self._pending_switch_reason = ""

    def skill_load_override(
        self,
        tool_name: object,
        arguments: Mapping[str, Any],
    ) -> str | None:
        """Keep one real skill activation per turn without failing the tool call."""

        if str(tool_name or "").strip() != "skill":
            return None
        action = str(arguments.get("action") or "load").strip().lower()
        requested = str(arguments.get("name") or "").strip()
        if action == "switch":
            if not requested:
                return None
            if not self.loaded_skills:
                return json.dumps(
                    {
                        "ok": False,
                        "error_code": "skill_switch_requires_load",
                        "error": (
                            "当前回合还没有真实 Skill load 回执；"
                            '请先用 skill(action="load") 加载正确技能。'
                        ),
                        "pre_activated_skill": self.pre_activated_skill,
                        "requested_name": requested,
                    },
                    ensure_ascii=False,
                )
            if self._side_effect_started:
                return json.dumps(
                    {
                        "ok": False,
                        "error_code": "skill_switch_after_side_effect",
                        "error": (
                            "当前回合已经发生副作用，禁止切换 Skill；"
                            "请在新回合重新路由。"
                        ),
                        "active_skill": self.active_skill,
                        "requested_name": requested,
                        "side_effect_tools": list(self._side_effect_tools),
                    },
                    ensure_ascii=False,
                )
            if requested == self.active_skill:
                return json.dumps(
                    {
                        "schema": "village_agent_skill.v1",
                        "ok": True,
                        "action": "already_active",
                        "name": requested,
                        "requested_name": requested,
                        "loaded_skills": list(self.loaded_skills),
                    },
                    ensure_ascii=False,
                )
            self._pending_skill = requested
            self._pending_skill_action = "switch"
            self._pending_switch_reason = str(arguments.get("reason") or "").strip()[
                :240
            ]
            return None
        if action != "load" or not requested or not self.loaded_skills:
            if action != "load" or not requested:
                return None
            if self._pending_skill:
                active = self._pending_skill
            else:
                self._pending_skill = requested
                return None
        else:
            if requested in self.loaded_skills:
                return None
            active = self.loaded_skills[0]
        return json.dumps(
            {
                "schema": "village_agent_skill.v1",
                "ok": True,
                "action": "already_active",
                "name": active,
                "requested_name": requested,
                "loaded_skills": list(self.loaded_skills),
                "message": (
                    f"当前回合已激活 Skill {active}；"
                    "如需改写，请在任何副作用前调用 "
                    f'skill(action="switch", name="{requested}", reason="...")。'
                ),
            },
            ensure_ascii=False,
        )

    def block_tool_call(
        self,
        tool_name: object,
        arguments: Mapping[str, Any],
    ) -> str | None:
        """Return a structured denial before the real handler can run."""

        if PAID_MEDIA_AUTH_FLAG not in self.active_flags | self.pre_activated_flags:
            return None
        invocation = _paid_invocation(tool_name, arguments)
        if invocation is None:
            return None
        capability_id, supplied_authorization = invocation
        supplied_grant = _bound_turn_grant(
            supplied_authorization,
            turn_id=self.turn_id,
        )
        current_grant = _bound_turn_grant(
            self._turn_authorization,
            turn_id=self.turn_id,
        )
        if current_grant and not supplied_grant:
            # The grant id is a server-owned fact bound to this turn. The model
            # may omit it instead of copying it through every nested tool call;
            # the paid handler still resolves and consumes the active grant.
            return None
        if supplied_grant and current_grant and supplied_grant == current_grant:
            return None
        return tool_result(
            {
                "ok": False,
                "error_code": PAID_MEDIA_FENCE_ERROR,
                "error": "当前 Skill 要求付费媒体必须携带本轮 task_authorization。",
                "skill_fence": {
                    "flag": PAID_MEDIA_AUTH_FLAG,
                    "skills": list(self.loaded_skills)
                    or ([self.pre_activated_skill] if self.pre_activated_skill else []),
                    "pre_activated": not bool(self.loaded_skills),
                    "tool": str(tool_name or ""),
                    **({"capability_id": capability_id} if capability_id else {}),
                    "authorization_bound": bool(
                        supplied_grant
                        and current_grant
                        and supplied_grant == current_grant
                    ),
                },
                "retryable": False,
            }
        )


__all__ = [
    "PAID_MEDIA_AUTH_FLAG",
    "PAID_MEDIA_FENCE_ERROR",
    "SkillFenceRuntime",
    "task_authorization_from_prompt",
]
