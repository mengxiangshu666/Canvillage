"""失败之后该干什么：把「下一步」写进工具失败返回体，而不是留在模型的自觉里。

2026-09-30 的教训：同一个「参考图没对上 / 检查点未就绪」的错误，Agent 在一天里
撞了 8 次，每次都用同样的方式重试，然后给用户编一句「已保留恢复点」。
根因不是那个闸门（那部分已单独修），而是**失败返回体里没有告诉它下一步**。

做法取自参考库 libtv 的 `disposition` 三档（`FOR_VILLAGE/TOOL_CONTRACT_AND_CONVERSATION_SPEC.md:170-186`
与 `tool_error_protocol.json` HC-9，234 条真实错误归纳出 104 个模板）：

    ① type=GENERATION_TRANSIENT  disposition=can retry as-is
    ② type=INVALID_PARAMS        disposition=fix the parameters before retrying
                                  (rewriting the prompt text will not help)
    ③ type=UNKNOWN               disposition=modify the prompt/parameters before
                                  retrying; if it still fails after 2 retries with
                                  modifications, ask the user directly instead of
                                  continuing to retry

第 ② 档里那句「rewriting the prompt text will not help」是整份协议的关键：它明确否掉了
「改一遍提示词再试」这条最常见的错误自愈路径。第 ③ 档的「两次仍败就问用户」则给无限重试
划了终点。

本模块把这三档落到本仓的真实错误码上，并按「同一回合 + 同一工具 + 同一错误」计数，
用完预算就把 disposition 升级成「必须问用户」，让 Agent 停手而不是继续撞。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

# 三档：原样可重试 / 必须改参数 / 改了再试仍败就问用户。
RETRY_AS_IS = "retry_as_is"
FIX_PARAMS = "fix_params"
MODIFY_THEN_ASK = "modify_then_ask"
ASK_USER_NOW = "ask_user_now"

# 改参数档最多重试几次就升级给用户。取自 libtv 的「修改后重试 2 次仍未通过」。
MODIFY_RETRY_BUDGET = 2

# 原样可重试没有硬上限——但也要给一个天花板，否则「稍后重试」会变成死循环。
RETRY_AS_IS_BUDGET = 3

# —— ① 原样可重试：这次失败与「你做了什么」无关，是环境抖了 ——
_RETRY_AS_IS_CODES = frozenset(
    {
        "service_unavailable",
        "upstream_busy",
        "upstream_timeout",
        "transport_disconnect",
        "agent_stream_transport",
        "CHAT_QUOTA_EXCEEDED_soft",
    }
)

# —— ② 必须改参数：原样再发一次必然同样失败，改提示词也没用 ——
# 这一档里绝大多数是「字段没填 / 枚举值选错 / 走错了道」。
_FIX_PARAMS_CODES = frozenset(
    {
        # 工具入参没过 schema —— 改字段值去，改正文没用
        "tool_arguments_invalid",
        "tool_result_too_large",
        # 回合合同要求某样东西但没给
        "agent_turn_contract_awaiting_human",
        "agent_turn_contract_irreversible",
        "skill_switch_requires_load",
        # 画布派发：声明与实际不符，补上缺的字段或换枚举值
        "creation_reason_required",
        "creation_not_authorized",
        "existing_target_mismatch",
        "existing_run_target_mismatch",
        "existing_generation_target_mismatch",
        "workflow_reuse_targets_required",
        "director_contract_required",
        "execution_not_authorized",
        "dynamic_command_rejected",
        "dynamic_source_turn_required",
        "dynamic_execution_context_capability_mismatch",
        "dynamic_allowlist_capability_mismatch",
        "dynamic_allowlist_required",
        "dynamic_canvas_revision_missing",
        "dynamic_execution_context_plan_missing",
        "mode_mismatch",
        "prerequisite_failed",
        "prerequisite_unavailable",
        # 画布写入没形成服务端回执：按回执说的重放，不要原样再发
        "FZ_EMIT_UNVERIFIED",
        "FZ_EMIT_NOOP",
        "FZ_READBACK_FAILED",
        "FZ_SERVER_APPLY_FAILED",
    }
)

_INSTRUCTIONS: Mapping[str, str] = {
    RETRY_AS_IS: (
        "本次失败与你的参数无关（服务端或传输抖动）。"
        "可以原样重试一次；如果重试后仍是同一个错误码，改参数没有意义，去做别的或问用户。"
    ),
    FIX_PARAMS: (
        "原样重发一定会以同样方式失败——错误信息里点名了缺什么或哪个值不对。"
        "只改这些地方（补字段、换枚举值、改通道）；"
        "重写提示词正文不会让这个错误消失，别用它代替改参数。"
    ),
    MODIFY_THEN_ASK: (
        "先修改参数或提示词再重试。修改后最多重试 2 次；"
        "两次仍然失败时**停下来直接问用户**，不要继续重试，也不要声称任务已保存或可恢复。"
    ),
    ASK_USER_NOW: (
        "同一个错误已经改过参数重试过多次仍未通过。**停止重试，现在就把情况告诉用户**："
        "你要做什么、卡在哪一步、需要用户提供什么。不要编造「已保留」「已续跑」这类说法。"
    ),
}


def _extract_error_code(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return ""
    for key in ("error_code", "code", "reason_code", "reason"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    # 检查点把真实原因放在 checkpoint.reason 里，外层 error_code 只是笼统码。
    checkpoint = payload.get("checkpoint")
    if isinstance(checkpoint, Mapping):
        for key in ("reason", "status"):
            value = checkpoint.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _base_disposition(error_code: str) -> str:
    # 两张表按各自书写形式登记（画布回执用 FZ_* 大写，其余小写），所以两边都试。
    # 只 casefold 一边会让另一边的码永远匹配不上、静默掉进「随便改改」那一档 ——
    # 2026-09-30 的测试就是这么抓到 FZ_EMIT_UNVERIFIED / FZ_SERVER_APPLY_FAILED 的。
    raw = (error_code or "").strip()
    code = raw.casefold()
    if not raw:
        return MODIFY_THEN_ASK
    if raw in _RETRY_AS_IS_CODES or code in _RETRY_AS_IS_CODES:
        return RETRY_AS_IS
    if raw in _FIX_PARAMS_CODES or code in _FIX_PARAMS_CODES:
        return FIX_PARAMS
    # 「要求先取得 X」这类码一定要改步骤，原样重试没意义。
    if code.endswith("_required") or code.endswith("_invalid") or code.endswith("_mismatch"):
        return FIX_PARAMS
    return MODIFY_THEN_ASK


@dataclass(slots=True)
class ToolFailureLedger:
    """按「回合 + 工具 + 错误码」计数，用完预算就要求 Agent 停手问用户。"""

    turn_id: str = ""
    _counts: dict[tuple[str, str, str], int] = field(default_factory=dict)

    def record_and_advise(
        self,
        *,
        tool_name: str,
        payload: Any,
        turn_id: str = "",
    ) -> dict[str, Any]:
        """Classify one failure and return the guidance to attach to it."""

        code = _extract_error_code(payload)
        disposition = _base_disposition(code)
        budget = (
            RETRY_AS_IS_BUDGET
            if disposition == RETRY_AS_IS
            else MODIFY_RETRY_BUDGET
        )
        key = (turn_id or self.turn_id, str(tool_name or ""), code or disposition)
        attempt = self._counts.get(key, 0) + 1
        self._counts[key] = attempt
        remaining = max(0, budget - attempt)
        # 预算用尽 → 升级：无论原本是哪一档，都必须转为「现在问用户」。
        effective = disposition if remaining > 0 else ASK_USER_NOW
        return {
            "error_code": code or None,
            "disposition": effective,
            "base_disposition": disposition,
            "attempt": attempt,
            "retry_budget": budget,
            "retry_remaining": remaining,
            "ask_user_now": effective == ASK_USER_NOW,
            "instruction": _INSTRUCTIONS[effective],
        }


def attach_disposition(
    payload: Any,
    *,
    tool_name: str,
    ledger: ToolFailureLedger,
    turn_id: str = "",
) -> Any:
    """Attach `disposition` guidance to a failed tool result.

    Only failed results are touched — a success never needs "what to do next".
    """

    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return payload
    if payload.get("ok") is not False and not payload.get("error_code"):
        return payload
    advice = ledger.record_and_advise(
        tool_name=tool_name, payload=payload, turn_id=turn_id
    )
    enriched = dict(payload)
    enriched["disposition"] = advice
    return enriched


__all__ = [
    "ASK_USER_NOW",
    "FIX_PARAMS",
    "MODIFY_RETRY_BUDGET",
    "MODIFY_THEN_ASK",
    "RETRY_AS_IS",
    "ToolFailureLedger",
    "attach_disposition",
]