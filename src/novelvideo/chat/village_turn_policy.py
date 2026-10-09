"""Receipt-driven policy for one native Village Agent turn.

This module owns the bounded turn state that used to live inside the Hermes
adapter. It is transport-neutral: callers feed normalized tool lifecycle
events and read structured phase, budget, and recovery state back out.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from novelvideo.chat.approval_store import paid_media_turn_grant_max_starts
from novelvideo.chat.identity_compat import (
    CANONICAL_WORKFLOW_SCHEMA,
    normalize_tool_name,
)
from novelvideo.chat.recovery_policy import (
    RecoveryContract,
    capability_invocation,
    is_exact_recovery_paid_retry,
    is_exact_recovery_workflow_retry,
    is_recovery_read_tool,
    is_safe_recovery_repair_tool,
    iter_jsonish_values,
    recovery_contract_from_prompt,
    recovery_readiness_arguments,
    recovery_readiness_result_is_ready,
    tool_call_input,
    workflow_run_receipt_from_update,
)
from novelvideo.chat.tool_events import tool_payload_failed

_CANVAS_AGENT_REQUEST_V2_RE = re.compile(
    r"\[CANVAS_AGENT_REQUEST_V2\]\s*(.*?)\s*"
    r"\[/CANVAS_AGENT_REQUEST_V2\]",
    re.IGNORECASE | re.DOTALL,
)


def _env_positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


DIRECTOR_WORKFLOW_WRITE_STEP_LIMIT = _env_positive_int(
    "VILLAGE_CANVAS_DIRECTOR_WORKFLOW_WRITE_STEP_LIMIT", 6
)
DIRECTOR_WORKFLOW_PAID_MEDIA_LIMIT = _env_positive_int(
    "VILLAGE_CANVAS_DIRECTOR_WORKFLOW_PAID_MEDIA_LIMIT", 1
)

VILLAGE_CANVAS_ONE_STEP_STOP_MESSAGE = (
    "当前任务已开始处理。请稍后让我查看当前任务进度，或在任务完成后再继续下一步。"
)
VILLAGE_CANVAS_WRITE_FAILED_STOP_MESSAGE = (
    "刚才这一步没有成功启动任务。请先根据返回的错误补齐前置条件；"
    "如果是配音缺少声线，可以到「声音资产」上传或录制缺失声线后再继续。"
)
VILLAGE_CANVAS_DISPATCH_GUARD_MESSAGE = "当前调度已进入结果核对，系统暂不重复写入。"
VILLAGE_CANVAS_DISPATCH_IN_FLIGHT_MESSAGE = (
    "上一条画布调度仍在返回结果，已暂缓后续写入。"
)
VILLAGE_CANVAS_PROVIDER_RECONCILE_MESSAGE = (
    "已保留原媒体任务并进入状态对账，本轮没有重复提交。"
)
VILLAGE_CANVAS_WORKFLOW_RESUME_MESSAGE = (
    "已锁定原工作流继续处理，本轮没有创建替代工作流或新的媒体任务。"
)
VILLAGE_CANVAS_RECOVERY_INSPECT_MESSAGE = (
    "当前恢复合同要求先核对并修复前置条件；本轮不会重放原付费命令，"
    "也不会启动新的付费媒体。"
)

_VILLAGE_CANVAS_WRITE_TOOLS = {
    "village_canvas_apply_commands",
    "village_canvas_dispatch_action",
    "village_canvas_post",
    "village_canvas_patch",
    "village_canvas_delete",
    "village_canvas_build_characters",
    "village_canvas_plan_episodes",
    "village_canvas_generate_script",
    "village_canvas_update_character_face_prompt",
    "village_canvas_plan_identities",
    "village_canvas_plan_scenes",
    "village_canvas_plan_props",
    "village_canvas_generate_scene_master",
    "village_canvas_generate_scene_reverse",
    "village_canvas_generate_sketches",
    "village_canvas_detect_sketch_identities",
    "village_canvas_optimize_video_global",
    "village_canvas_generate_audio",
    "village_canvas_render_first_frames",
    "village_canvas_compose_episode",
    "village_canvas_generate_portrait",
    "village_canvas_generate_identity_image",
    "village_canvas_start_single_video",
    "village_canvas_start_production_run",
    "village_canvas_command_production_run",
    "village_canvas_story_lab_save",
    "village_canvas_story_lab_generate",
    "village_canvas_story_lab_publish",
    "village_canvas_start_workflow_run",
    "village_canvas_command_workflow_run",
    "freezone_run_node",
    "freezone_retry_node",
    "freezone_stop_task",
}

_VILLAGE_CANVAS_PAID_MEDIA_TOOLS = {
    "village_canvas_generate_scene_master",
    "village_canvas_generate_scene_reverse",
    "village_canvas_generate_sketches",
    "village_canvas_generate_audio",
    "village_canvas_render_first_frames",
    "village_canvas_compose_episode",
    "village_canvas_generate_portrait",
    "village_canvas_generate_identity_image",
    "village_canvas_start_single_video",
    "village_canvas_start_production_run",
    "village_canvas_command_production_run",
    "freezone_run_node",
    "freezone_retry_node",
}


def is_village_canvas_write_tool(name: object) -> bool:
    return normalize_tool_name(name) in _VILLAGE_CANVAS_WRITE_TOOLS


def is_village_canvas_paid_media_tool(name: object) -> bool:
    return normalize_tool_name(name) in _VILLAGE_CANVAS_PAID_MEDIA_TOOLS


def is_village_canvas_observation_tool(name: object) -> bool:
    tool_name = normalize_tool_name(name)
    return (
        tool_name
        in {
            "skill",
            "freezone_get_canvas_snapshot",
            "village_canvas_read_compact",
            "village_canvas_wait_receipt",
            "village_canvas_capability",
        }
        or tool_name.startswith("village_canvas_get_")
        or tool_name.startswith("village_canvas_list_")
        or tool_name
        in {
            "village_canvas_pipeline_status",
            "village_canvas_get_production_control",
            "village_canvas_tavily_search",
            "mcp_tavily_pool_framework_search",
            "mcp_tavily_pool_tavily_search",
            "vision_analyze",
        }
    )


def is_exact_workflow_continuation(
    raw_event: object,
    expected_run_id: str,
) -> bool:
    raw_input = tool_call_input(raw_event)
    task = raw_input.get("task")
    if not expected_run_id or not isinstance(task, dict):
        return False
    return (
        str(task.get("existing_run_id") or "").strip() == expected_run_id
        and task.get("interaction_mode") == "execute"
        and task.get("target_strategy") == "reuse_existing"
        and task.get("requires_recovery") is True
        and not raw_input.get("commands")
        and not str(raw_input.get("generation_node_id") or "").strip()
    )


def is_tool_discovery_tool(name: object) -> bool:
    tool_name = str(name or "").strip().lower()
    return tool_name.startswith(("tool_search", "tool_describe"))


def tool_call_has_side_effect(name: object) -> bool:
    return is_village_canvas_write_tool(name) or str(name or "").strip() in {
        "freezone_emit_canvas_command",
        "village_canvas_apply_commands",
    }


def should_stop_after_write_tool(
    first_write_tool: str | None, next_tool_name: object
) -> bool:
    return (
        first_write_tool is not None
        and is_village_canvas_paid_media_tool(first_write_tool)
        and is_village_canvas_paid_media_tool(next_tool_name)
    )


def is_failed_tool_update(value: object) -> bool:
    return tool_payload_failed(value)


def is_terminal_tool_update(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return str(value.get("status") or "").strip().lower() in {
        "completed",
        "failed",
        "error",
        "cancelled",
        "canceled",
    }


def director_paid_media_limit_from_prompt(
    prompt: str,
    default: int = DIRECTOR_WORKFLOW_PAID_MEDIA_LIMIT,
) -> int:
    match = _CANVAS_AGENT_REQUEST_V2_RE.search(str(prompt or ""))
    if match is None:
        return default
    try:
        payload = json.loads(match.group(1))
    except (TypeError, ValueError):
        return default
    authorization = (
        payload.get("task_authorization") if isinstance(payload, dict) else None
    )
    if not isinstance(authorization, dict):
        return default
    grant_id = authorization.get("grant_id")
    turn_id = authorization.get("turn_id")
    max_starts = paid_media_turn_grant_max_starts(authorization)
    eligible = (
        max_starts > 0
        and isinstance(grant_id, str)
        and grant_id.strip().startswith("pmg_")
        and isinstance(turn_id, str)
        and bool(turn_id.strip())
    )
    return max_starts if eligible else default


def generation_proposal_requires_confirmation(value: object) -> bool | None:
    if isinstance(value, str):
        text = value.removeprefix("\x00json:").strip()
        if not text:
            return None
        try:
            return generation_proposal_requires_confirmation(json.loads(text))
        except (TypeError, ValueError):
            return None
    if isinstance(value, dict):
        flag = value.get("requires_user_confirmation")
        if isinstance(flag, bool):
            return flag
        for item in value.values():
            nested = generation_proposal_requires_confirmation(item)
            if nested is not None:
                return nested
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            nested = generation_proposal_requires_confirmation(item)
            if nested is not None:
                return nested
    return None


def should_mark_first_write_failed(
    first_write_tool: str | None,
    active_tool_name: str | None,
    update: object,
) -> bool:
    return (
        first_write_tool is not None
        and active_tool_name == first_write_tool
        and is_failed_tool_update(update)
    )


def extract_dispatch_lane(value: object) -> str | None:
    if isinstance(value, str):
        text = value.removeprefix("\x00json:").strip()
        if "action_dispatch" not in text and "canvas_action_route.v1" not in text:
            return None
        try:
            return extract_dispatch_lane(json.loads(text))
        except json.JSONDecodeError:
            return None
    if isinstance(value, dict):
        dispatch = value.get("action_dispatch")
        if isinstance(dispatch, dict):
            route = dispatch.get("route")
            if isinstance(route, dict):
                lane = str(route.get("lane") or "").strip()
                if lane in {"canvas", "workflow", "blocked"}:
                    return lane
        if value.get("schema") == "canvas_action_route.v1":
            lane = str(value.get("lane") or "").strip()
            if lane in {"canvas", "workflow", "blocked"}:
                return lane
        for item in value.values():
            lane = extract_dispatch_lane(item)
            if lane:
                return lane
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            lane = extract_dispatch_lane(item)
            if lane:
                return lane
    return None


def _receipt_int(value: object) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


def canvas_receipt_is_authoritative(value: object) -> bool:
    for candidate in iter_jsonish_values(value):
        if not isinstance(candidate, dict):
            continue
        server_applied = candidate.get(
            "server_applied", candidate.get("serverApplied")
        )
        if server_applied is not True:
            continue
        revision = _receipt_int(
            candidate.get(
                "revision",
                candidate.get("canvas_revision", candidate.get("canvasRevision")),
            )
        )
        applied_ops = (
            _receipt_int(candidate.get("applied_ops", candidate.get("appliedOps")))
            or 0
        )
        created_ids = candidate.get(
            "created_node_ids", candidate.get("createdNodeIds")
        )
        if (
            revision is not None
            and revision > 0
            and (applied_ops > 0 or bool(created_ids))
        ):
            return True
    return False


def canvas_result_is_explicit(value: object) -> bool:
    for candidate in iter_jsonish_values(value):
        if not isinstance(candidate, dict):
            continue
        if candidate.get("schema") in {
            "canvas_chat_commands.v1",
            "canvas_command_receipt.v2",
        }:
            return True
        if any(
            key in candidate
            for key in (
                "server_applied",
                "serverApplied",
                "applied_ops",
                "appliedOps",
            )
        ):
            return True
    return False


@dataclass
class DirectorWorkflowRun:
    """Bounded, receipt-driven state for one Village director turn."""

    write_step_limit: int = DIRECTOR_WORKFLOW_WRITE_STEP_LIMIT
    paid_media_limit: int = DIRECTOR_WORKFLOW_PAID_MEDIA_LIMIT
    phase: str = "planning"
    active_tool: str | None = None
    active_capability_id: str = ""
    active_capability_arguments: dict[str, Any] = field(default_factory=dict)
    observation_steps: int = 0
    write_steps: int = 0
    paid_media_steps: int = 0
    verified_receipts: int = 0
    failed_tool: str | None = None
    failed_command_id: str | None = None
    active_command_id: str | None = None
    awaiting_confirmation: bool = False
    dispatch_attempted: bool = False
    dispatch_completed: bool = False
    dispatch_receipt_verified: bool = False
    dispatch_terminal_locked: bool = False
    dispatch_lane: str | None = None
    dispatch_repair_steps: int = 0
    recovery_contract: RecoveryContract | None = None
    recovery_guard_triggered: bool = False
    recovery_blocked_tool: str | None = None
    recovery_revalidation_pending: bool = False
    recovery_retry_available: bool = False
    recovery_retry_consumed: bool = False
    workflow_run_id: str = ""
    workflow_run_status: str = ""
    workflow_receipt_pending: bool = False
    workflow_stage_receipts: list[dict[str, Any]] = field(default_factory=list)
    workflow_run_next_action: str = ""
    workflow_run_error_code: str = ""
    workflow_run_recovery: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        recovery = self.recovery_contract
        if (
            recovery is not None
            and recovery.action == "inspect_before_action"
            and recovery.recovery_action == "retry_failed_items"
            and recovery.requires_paid_media
            and recovery.item_ids
        ):
            self.recovery_retry_available = True
        if (
            recovery is not None
            and recovery.action == "resume_workflow_run"
            and recovery.workflow_run_id
        ):
            self.workflow_run_id = recovery.workflow_run_id
            self.workflow_receipt_pending = True

    def _step_statuses(self) -> list[dict[str, str]]:
        observe = (
            "done"
            if self.observation_steps
            else ("running" if self.phase == "observing" else "pending")
        )
        plan = "running" if self.phase == "planning" else "done"
        act = "pending"
        if self.phase == "acting":
            act = "running"
        elif self.write_steps:
            act = "done"
        verify = "pending"
        if self.phase == "verifying":
            verify = "running"
        elif self.verified_receipts:
            verify = "done"
        if self.phase == "failed":
            target = (
                "act"
                if self.failed_tool and is_village_canvas_write_tool(self.failed_tool)
                else "observe"
            )
            for item in (
                ("observe", observe),
                ("plan", plan),
                ("act", act),
                ("verify", verify),
            ):
                if item[0] == target:
                    if target == "observe":
                        observe = "failed"
                    else:
                        act = "failed"
        return [
            {"id": "observe", "label": "读取真实项目 / 画布状态", "status": observe},
            {"id": "plan", "label": "确定本轮导演计划", "status": plan},
            {"id": "act", "label": "写入结构、资产或生产步骤", "status": act},
            {"id": "verify", "label": "核对工具回执与下一步", "status": verify},
        ]

    def _apply_workflow_run_receipt(
        self,
        tool_name: str,
        update: object,
        *,
        capability_id: str = "",
    ) -> bool:
        is_command = tool_name == "village_canvas_command_workflow_run" or (
            capability_id == "workflow.run.control"
        )
        is_observation = tool_name == "village_canvas_get_workflow_run" or (
            capability_id == "workflow.run.get"
        )
        if not is_command and not (is_observation and self.workflow_receipt_pending):
            return False
        receipt = workflow_run_receipt_from_update(
            update,
            expected_run_id=self.workflow_run_id,
        )
        if receipt is not None:
            if receipt.run_id:
                self.workflow_run_id = receipt.run_id
            self.workflow_run_status = receipt.status
            if receipt.stage_receipts:
                self.workflow_stage_receipts = [
                    dict(item) for item in receipt.stage_receipts[:32]
                ]
            self.workflow_run_next_action = receipt.next_action
            self.workflow_run_error_code = receipt.error_code
            self.workflow_run_recovery = dict(receipt.recovery)
            if receipt.status == "completed":
                self.workflow_receipt_pending = False
                self.verified_receipts += 1
                self.phase = "verifying"
            elif receipt.status == "failed":
                self.workflow_receipt_pending = False
                self.phase = "failed"
            elif receipt.status == "cancelled":
                self.workflow_receipt_pending = False
                self.phase = "cancelled"
            else:
                self.workflow_receipt_pending = True
                self.phase = "observing"
        elif is_command and not is_failed_tool_update(update):
            self.workflow_run_status = "accepted"
            self.workflow_receipt_pending = True
            self.phase = "observing"
        elif is_observation:
            self.phase = "observing"
        else:
            return False
        if is_observation:
            self.observation_steps += 1
        return True

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": CANONICAL_WORKFLOW_SCHEMA,
            "status": self.phase,
            "active_tool": self.active_tool,
            "steps": self._step_statuses(),
            "budget": {
                "observation_steps": self.observation_steps,
                "write_steps": self.write_steps,
                "write_step_limit": self.write_step_limit,
                "paid_media_steps": self.paid_media_steps,
                "paid_media_limit": self.paid_media_limit,
            },
            "awaiting_confirmation": self.awaiting_confirmation,
            "dispatch_attempted": self.dispatch_attempted,
            "dispatch_completed": self.dispatch_completed,
            "dispatch_receipt_verified": self.dispatch_receipt_verified,
            "dispatch_terminal_locked": self.dispatch_terminal_locked,
            "dispatch_lane": self.dispatch_lane,
            "dispatch_repair_steps": self.dispatch_repair_steps,
            **({"failed_tool": self.failed_tool} if self.failed_tool else {}),
            **(
                {"failed_command_id": self.failed_command_id}
                if self.failed_command_id
                else {}
            ),
        }
        if (
            self.workflow_run_id
            or self.workflow_run_status
            or self.workflow_receipt_pending
        ):
            continuation = {
                "run_id": self.workflow_run_id or None,
                "run_status": self.workflow_run_status or None,
                "receipt_pending": self.workflow_receipt_pending,
            }
            if self.workflow_stage_receipts:
                continuation["stage_receipts"] = self.workflow_stage_receipts[:32]
                continuation["stage_receipt_digest"] = hashlib.sha256(
                    json.dumps(
                        self.workflow_stage_receipts[:32],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
            if self.workflow_run_next_action and (
                not self.workflow_receipt_pending
                and self.workflow_run_status in {"failed", "cancelled"}
            ):
                continuation["next_action"] = self.workflow_run_next_action
            if self.workflow_run_error_code:
                continuation["error_code"] = self.workflow_run_error_code
            if self.workflow_run_recovery:
                continuation["recovery"] = self.workflow_run_recovery
            payload["workflow_run_continuation"] = continuation
        if self.recovery_contract is not None:
            payload["recovery"] = {
                "action": self.recovery_contract.action,
                "allow_new_submission": self.recovery_contract.allow_new_submission,
                "workflow_run_id": self.recovery_contract.workflow_run_id or None,
                "provider_task_count": len(self.recovery_contract.provider_task_ids),
                "recovery_action": self.recovery_contract.recovery_action or None,
                "requires_paid_media": self.recovery_contract.requires_paid_media,
                "step_id": self.recovery_contract.step_id or None,
                "item_ids": list(self.recovery_contract.item_ids),
                "media_action": self.recovery_contract.media_action or None,
                "target_node_id": self.recovery_contract.target_node_id or None,
                "retry_authorized": (
                    self.recovery_retry_available
                    and not self.recovery_retry_consumed
                ),
                "retry_consumed": self.recovery_retry_consumed,
                "guard_triggered": self.recovery_guard_triggered,
                "blocked_tool": self.recovery_blocked_tool,
            }
        return payload

    def start_tool(self, tool_name: str, raw_event: object = None) -> str | None:
        incoming_command_id = str(
            tool_call_input(raw_event).get("command_id") or ""
        ).strip()
        recovery = self.recovery_contract
        exact_retry_requested = False
        if recovery is not None and recovery.action in {
            "reconcile_provider_tasks",
            "resume_workflow_run",
        }:
            allowed = is_recovery_read_tool(tool_name, raw_event, recovery)
            if (
                recovery.action == "resume_workflow_run"
                and tool_name == "village_canvas_dispatch_action"
            ):
                allowed = is_exact_workflow_continuation(
                    raw_event,
                    recovery.workflow_run_id,
                )
            if not allowed:
                self.recovery_guard_triggered = True
                self.recovery_blocked_tool = tool_name or None
                return (
                    VILLAGE_CANVAS_PROVIDER_RECONCILE_MESSAGE
                    if recovery.action == "reconcile_provider_tasks"
                    else VILLAGE_CANVAS_WORKFLOW_RESUME_MESSAGE
                )
        if (
            recovery is not None
            and recovery.action == "inspect_before_action"
            and not recovery.allow_new_submission
        ):
            exact_retry_rule = (
                is_exact_recovery_workflow_retry
                if recovery.workflow_run_id
                else is_exact_recovery_paid_retry
            )
            exact_retry = (
                self.recovery_retry_available
                and not self.recovery_retry_consumed
                and exact_retry_rule(tool_name, raw_event, recovery)
            )
            allowed = (
                is_recovery_read_tool(tool_name, raw_event, recovery)
                or is_safe_recovery_repair_tool(tool_name, raw_event, recovery)
                or exact_retry
            )
            if not allowed:
                self.recovery_guard_triggered = True
                self.recovery_blocked_tool = tool_name or None
                return VILLAGE_CANVAS_RECOVERY_INSPECT_MESSAGE
            if exact_retry:
                exact_retry_requested = True
            self.recovery_revalidation_pending = bool(
                recovery_readiness_arguments(tool_name, raw_event) is not None
            )
            if (
                is_village_canvas_write_tool(tool_name)
                and not exact_retry_requested
            ):
                self.recovery_retry_available = False
        if (
            self.failed_tool
            and is_village_canvas_write_tool(tool_name)
            and tool_name == "village_canvas_dispatch_action"
            and incoming_command_id
            and self.failed_command_id
            and incoming_command_id != self.failed_command_id
        ):
            self.failed_tool = None
            self.failed_command_id = None
            self.dispatch_attempted = False
            self.dispatch_completed = False
            self.dispatch_receipt_verified = False
            self.dispatch_terminal_locked = False
            self.dispatch_lane = None
            self.dispatch_repair_steps = 0
        if self.dispatch_attempted:
            if not self.dispatch_completed:
                if (
                    tool_name == "village_canvas_dispatch_action"
                    or not is_village_canvas_observation_tool(tool_name)
                ):
                    return VILLAGE_CANVAS_DISPATCH_IN_FLIGHT_MESSAGE
            is_observation = is_village_canvas_observation_tool(tool_name)
            is_canvas_repair = (
                tool_name == "village_canvas_apply_commands"
                and self.dispatch_completed
                and self.dispatch_lane in {"canvas", None}
                and not self.dispatch_receipt_verified
                and not self.dispatch_terminal_locked
                and self.observation_steps > 0
                and self.dispatch_repair_steps < 1
            )
            if tool_name == "village_canvas_dispatch_action" or not (
                is_observation or is_canvas_repair
            ):
                return VILLAGE_CANVAS_DISPATCH_GUARD_MESSAGE
            if is_canvas_repair:
                self.dispatch_repair_steps += 1
        if self.failed_tool and is_village_canvas_write_tool(tool_name):
            return "前一项写入没有成功，已停止继续写入以保留可恢复现场。"
        if tool_name == "village_canvas_dispatch_action":
            self.dispatch_attempted = True
        self.active_command_id = incoming_command_id or None
        invocation = capability_invocation(tool_name, raw_event)
        self.active_capability_id = invocation[0] if invocation is not None else ""
        self.active_capability_arguments = (
            dict(invocation[1]) if invocation is not None else {}
        )
        if is_village_canvas_write_tool(tool_name):
            if self.write_steps >= self.write_step_limit:
                return "本轮已达到连续写入预算，已保留当前回执和下一步以避免扩大范围。"
            if (
                is_village_canvas_paid_media_tool(tool_name)
                and self.paid_media_steps >= self.paid_media_limit
            ):
                return (
                    "本轮媒体生产启动预算已用完，等待已启动步骤回执或用户下一次确认。"
                )
            if exact_retry_requested:
                self.recovery_retry_consumed = True
                self.recovery_retry_available = False
            self.write_steps += 1
            if is_village_canvas_paid_media_tool(tool_name):
                self.paid_media_steps += 1
            invocation = capability_invocation(tool_name, raw_event)
            is_workflow_control = (
                tool_name == "village_canvas_command_workflow_run"
                or (
                    invocation is not None
                    and invocation[0] == "workflow.run.control"
                )
            )
            if is_workflow_control:
                command_input = (
                    invocation[1]
                    if invocation is not None
                    else tool_call_input(raw_event)
                )
                self.workflow_run_id = (
                    str(command_input.get("run_id") or "").strip()
                    or (
                        recovery.workflow_run_id
                        if recovery is not None
                        else ""
                    )
                    or self.workflow_run_id
                )
                self.workflow_run_status = "submitting"
                self.workflow_receipt_pending = True
            self.phase = "acting"
        elif (
            exact_retry_requested
            and invocation is not None
            and invocation[0] == "workflow.run.control"
        ):
            if self.write_steps >= self.write_step_limit:
                return "本轮已达到连续写入预算，已保留当前回执和下一步以避免扩大范围。"
            if (
                recovery is not None
                and recovery.requires_paid_media
                and self.paid_media_steps >= self.paid_media_limit
            ):
                return (
                    "本轮媒体生产启动预算已用完，等待已启动步骤回执或用户下一次确认。"
                )
            self.recovery_retry_consumed = True
            self.recovery_retry_available = False
            self.write_steps += 1
            if recovery is not None and recovery.requires_paid_media:
                self.paid_media_steps += 1
            self.workflow_run_id = (
                str(invocation[1].get("run_id") or "").strip()
                or (
                    recovery.workflow_run_id
                    if recovery is not None
                    else ""
                )
                or self.workflow_run_id
            )
            self.workflow_run_status = "submitting"
            self.workflow_receipt_pending = True
            self.phase = "acting"
        elif is_village_canvas_observation_tool(tool_name):
            self.phase = "observing"
        else:
            self.phase = "acting"
        self.active_tool = tool_name or None
        return None

    def finish_tool(self, tool_name: str, update: object) -> str:
        active_command_id = self.active_command_id
        active_capability_id = self.active_capability_id
        recovery_revalidation_pending = self.recovery_revalidation_pending
        self.active_tool = None
        self.active_command_id = None
        self.active_capability_id = ""
        self.active_capability_arguments = {}
        self.recovery_revalidation_pending = False
        expected_dispatch_block = False
        if tool_name == "village_canvas_dispatch_action":
            self.dispatch_completed = True
            self.dispatch_lane = extract_dispatch_lane(update)
            expected_dispatch_block = self.dispatch_lane == "blocked" and any(
                isinstance(candidate, dict)
                and str(candidate.get("error_code") or "").strip()
                in {
                    "director_clarification_required",
                    "execution_not_authorized",
                }
                for candidate in iter_jsonish_values(update)
            )
            self.dispatch_receipt_verified = (
                self.dispatch_receipt_verified
                or canvas_receipt_is_authoritative(update)
            )
            self.dispatch_terminal_locked = bool(
                self.dispatch_completed
                and is_terminal_tool_update(update)
                and not is_failed_tool_update(update)
                and not canvas_result_is_explicit(update)
                and any(key in update for key in ("rawOutput", "raw_output"))
            )
        if self._apply_workflow_run_receipt(
            tool_name,
            update,
            capability_id=active_capability_id,
        ):
            return self.phase
        if is_failed_tool_update(update) and not expected_dispatch_block:
            if recovery_revalidation_pending:
                self.recovery_retry_available = False
            if tool_name == "village_canvas_command_workflow_run":
                self.workflow_run_status = "failed"
                self.workflow_receipt_pending = False
            self.failed_tool = tool_name or None
            self.failed_command_id = active_command_id or None
            self.phase = "failed"
            return self.phase
        if recovery_revalidation_pending:
            self.recovery_retry_available = recovery_readiness_result_is_ready(
                update,
                self.recovery_contract,
            )
        if expected_dispatch_block:
            self.phase = "planning"
            return self.phase
        if (
            tool_name == "village_canvas_dispatch_action"
            and self.dispatch_lane == "canvas"
            and canvas_result_is_explicit(update)
            and not canvas_receipt_is_authoritative(update)
        ):
            self.failed_tool = tool_name
            self.phase = "failed"
            return self.phase
        if is_village_canvas_observation_tool(tool_name):
            self.observation_steps += 1
            self.phase = "planning"
        elif tool_name == "freezone_propose_generation":
            requires_confirmation = generation_proposal_requires_confirmation(update)
            self.awaiting_confirmation = requires_confirmation is not False
            self.phase = (
                "awaiting_confirmation" if self.awaiting_confirmation else "planning"
            )
        elif is_village_canvas_write_tool(tool_name):
            self.verified_receipts += 1
            self.phase = "verifying"
        else:
            self.phase = "planning"
        return self.phase

    def complete_turn(self) -> str:
        if self.failed_tool:
            self.phase = "failed"
        elif self.awaiting_confirmation:
            self.phase = "awaiting_confirmation"
        elif self.workflow_run_status == "failed":
            self.phase = "failed"
        elif self.workflow_run_status == "cancelled":
            self.phase = "cancelled"
        elif self.workflow_receipt_pending:
            self.phase = "observing"
        else:
            self.phase = "completed"
        return self.phase


_DirectorWorkflowRun = DirectorWorkflowRun


__all__ = [
    "DIRECTOR_WORKFLOW_PAID_MEDIA_LIMIT",
    "DIRECTOR_WORKFLOW_WRITE_STEP_LIMIT",
    "DirectorWorkflowRun",
    "VILLAGE_CANVAS_DISPATCH_GUARD_MESSAGE",
    "VILLAGE_CANVAS_DISPATCH_IN_FLIGHT_MESSAGE",
    "VILLAGE_CANVAS_ONE_STEP_STOP_MESSAGE",
    "VILLAGE_CANVAS_PROVIDER_RECONCILE_MESSAGE",
    "VILLAGE_CANVAS_RECOVERY_INSPECT_MESSAGE",
    "VILLAGE_CANVAS_WORKFLOW_RESUME_MESSAGE",
    "VILLAGE_CANVAS_WRITE_FAILED_STOP_MESSAGE",
    "canvas_receipt_is_authoritative",
    "canvas_result_is_explicit",
    "director_paid_media_limit_from_prompt",
    "extract_dispatch_lane",
    "generation_proposal_requires_confirmation",
    "is_exact_workflow_continuation",
    "is_failed_tool_update",
    "is_terminal_tool_update",
    "is_tool_discovery_tool",
    "is_village_canvas_observation_tool",
    "is_village_canvas_paid_media_tool",
    "is_village_canvas_write_tool",
    "recovery_contract_from_prompt",
    "should_mark_first_write_failed",
    "should_stop_after_write_tool",
    "tool_call_has_side_effect",
]
