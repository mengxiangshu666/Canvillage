"""失败之后该干什么：处置档写进返回体，预算用尽就要求问用户。

做法取自参考库 libtv 的 `disposition` 三档（`FOR_VILLAGE/TOOL_CONTRACT_AND_CONVERSATION_SPEC.md:170-186`
与 `tool_error_protocol.json` HC-9 —— 234 条真实错误归纳出 104 个模板）。第 ② 档那句
「rewriting the prompt text will not help」否掉了「改一遍提示词再试」这条最常见的
错误自愈路径；第 ③ 档的「两次仍败就问用户」给无限重试划了终点。

2026-09-30 的现实：同一个 `checkpoint is not ready_write`，Agent 一天撞了 4 次，
每次同样重试，然后给用户编「已保留恢复点」。本模块就是为终结这一类循环。
"""

from __future__ import annotations

import pytest

from novelvideo.tool_failure_disposition import (
    ASK_USER_NOW,
    FIX_PARAMS,
    MODIFY_RETRY_BUDGET,
    RETRY_AS_IS,
    ToolFailureLedger,
    attach_disposition,
)


def test_transient_failure_is_retryable_as_is():
    advice = ToolFailureLedger().record_and_advise(
        tool_name="t", payload={"ok": False, "error_code": "service_unavailable"}
    )
    assert advice["disposition"] == RETRY_AS_IS
    assert advice["ask_user_now"] is False
    assert "与你的参数无关" in advice["instruction"]


def test_parameter_failure_says_rewriting_the_prompt_will_not_help():
    """这一句是整份协议的关键：否掉「改提示词再试」这条最常见的错误自愈路径。"""

    for code in ("tool_arguments_invalid", "creation_reason_required"):
        advice = ToolFailureLedger().record_and_advise(
            tool_name="t", payload={"ok": False, "error_code": code}
        )
        assert advice["disposition"] == FIX_PARAMS, code
        assert "重写提示词正文不会让这个错误消失" in advice["instruction"], code


@pytest.mark.parametrize(
    "code",
    [
        "creation_not_authorized",
        "existing_target_mismatch",
        "existing_run_target_mismatch",
        "dynamic_command_rejected",
        "skill_switch_requires_load",
        "FZ_EMIT_UNVERIFIED",
        "FZ_SERVER_APPLY_FAILED",
        "dynamic_canvas_revision_missing",
    ],
)
def test_todays_real_failure_codes_all_land_in_a_defined_bucket(code):
    """今天真机出现过的每一类错误都必须落到明确的处置档，不能落到「随便改改」。"""

    advice = ToolFailureLedger().record_and_advise(
        tool_name="village_canvas_dispatch_action",
        payload={"ok": False, "error_code": code},
    )

    assert advice["disposition"] in {RETRY_AS_IS, FIX_PARAMS, "modify_then_ask"}, code
    assert advice["base_disposition"] == FIX_PARAMS, code
    assert advice["retry_budget"] == MODIFY_RETRY_BUDGET


def test_same_failure_escalates_to_ask_user_and_stays_there():
    """回归 2026-09-30：同一错误连撞后不再无限重试，而是要求停下来问用户。"""

    ledger = ToolFailureLedger()
    payload = {
        "ok": False,
        "error_code": "dynamic_checkpoint_blocked",
        "checkpoint": {"reason": "capability_missing_from_runtime_allowlist"},
    }

    first = ledger.record_and_advise(
        tool_name="village_canvas_dispatch_action", payload=payload, turn_id="turn-1"
    )
    assert first["ask_user_now"] is False
    assert first["retry_remaining"] == MODIFY_RETRY_BUDGET - 1

    for _ in range(4):
        again = ledger.record_and_advise(
            tool_name="village_canvas_dispatch_action",
            payload=payload,
            turn_id="turn-1",
        )
    assert again["disposition"] == ASK_USER_NOW
    assert again["ask_user_now"] is True
    assert again["retry_remaining"] == 0
    # 要求它停手的同时，明确禁止再编「已保留 / 已续跑」。
    assert "不要编造" in again["instruction"]
    assert "已保留" in again["instruction"]


def test_counts_are_scoped_by_turn_and_tool():
    ledger = ToolFailureLedger()
    payload = {"ok": False, "error_code": "x"}

    for _ in range(3):
        ledger.record_and_advise(tool_name="tool-a", payload=payload, turn_id="turn-1")
    b = ledger.record_and_advise(tool_name="tool-b", payload=payload, turn_id="turn-1")
    c = ledger.record_and_advise(tool_name="tool-a", payload=payload, turn_id="turn-2")

    assert b["attempt"] == 1
    assert c["attempt"] == 1


def test_only_failures_get_guidance():
    ledger = ToolFailureLedger()

    ok_result = attach_disposition(
        {"ok": True, "revision": 5}, tool_name="t", ledger=ledger
    )
    assert ok_result == {"ok": True, "revision": 5}
    assert ledger.record_and_advise.__self__._counts == {}


def test_disposition_survives_result_truncation_whitelist():
    """disposition 必须在压缩白名单里。

    超长失败结果会被 compact 成 `_RESULT_KEYS` 里那几个键；漏掉它，Agent 恰好在
    最需要指引的时候拿不到指引。"""

    from novelvideo.chat.village_harness import _RESULT_KEYS

    assert "disposition" in _RESULT_KEYS
