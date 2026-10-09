from __future__ import annotations

import asyncio

from novelvideo.chat.tool_allowlist import (
    compile_execution_context_allowlist,
    compile_tool_allowlist,
)


def _plan():
    return {
        "schema": "agent_expert_plan.v1",
        "plan_revision": "plan-a",
        "context_revision": "ctx-a",
        "arbiter": {
            "decision": "reuse_existing_target",
            "execution_enabled": False,
        },
        "experts": [
            {
                "expert": "canvas_state",
                "required_capabilities": [
                    "canvas.snapshot",
                    "canvas.compatibility.emit",
                ],
            },
            {
                "expert": "knowledge_continuity",
                "required_capabilities": [
                    "knowledge.search",
                    "knowledge.load_reference",
                ],
            },
        ],
        "source_errors": {},
    }


def test_tool_allowlist_injects_bounded_read_capabilities_and_defers_writes():
    result = compile_tool_allowlist(_plan())

    active_ids = {item["id"] for item in result["active_capabilities"]}
    deferred_ids = {item["id"] for item in result["deferred_write_capabilities"]}

    assert result["schema"] == "agent_tool_allowlist.v1"
    assert result["injection_mode"] == "runtime_allowlist"
    assert result["execution_enabled"] is False
    assert len(active_ids) <= 6
    assert "context.shared_snapshot" in active_ids
    assert "context.expert_plan" in active_ids
    assert "canvas.snapshot" in active_ids
    assert "knowledge.search" in active_ids
    assert "canvas.compatibility.emit" not in active_ids
    assert deferred_ids == {"canvas.compatibility.emit"}
    assert all(
        item["requires_checkpoint"] == "before_write"
        for item in result["deferred_write_capabilities"]
    )
    assert result["agent_fleet"]["executor"] == "production_executor"


def test_tool_allowlist_candidate_filter_denies_unknown_and_write_candidates():
    result = compile_tool_allowlist(
        _plan(),
        candidates=["canvas.snapshot", "canvas.compatibility.emit", "made.up.tool"],
    )

    assert {item["id"] for item in result["active_capabilities"]} == {"canvas.snapshot"}
    denied = {item["id"]: item["reason"] for item in result["denied_capabilities"]}
    assert denied["made.up.tool"] == "unknown_capability"
    assert denied["canvas.compatibility.emit"] == "capability_not_read_only"


def test_tool_allowlist_revision_changes_with_mode():
    observe = compile_tool_allowlist(_plan(), mode="observe")
    prepare = compile_tool_allowlist(_plan(), mode="prepare_write")

    assert observe["allowlist_revision"] != prepare["allowlist_revision"]
    assert prepare["execution_enabled"] is False


def test_tool_allowlist_execute_mode_opens_only_the_receipt_backed_canvas_writer():
    plan = _plan()
    result = compile_tool_allowlist(
        plan,
        mode="execute",
        candidates=["canvas.snapshot", "canvas.compatibility.emit"],
    )

    active = {item["id"]: item for item in result["active_capabilities"]}
    assert result["mode"] == "execute"
    assert result["execution_enabled"] is True
    assert active["canvas.compatibility.emit"]["side_effect"] == "canvas_write"
    assert active["canvas.compatibility.emit"]["requires_checkpoint"] == "before_write"
    assert "canvas.compatibility.emit" not in {
        item["id"] for item in result["deferred_write_capabilities"]
    }


def test_tool_allowlist_execute_mode_keeps_unwired_writes_deferred():
    result = compile_tool_allowlist(
        _plan(),
        mode="execute",
        candidates=["creative.plan_identities"],
    )

    assert result["execution_enabled"] is False
    assert result["active_capabilities"] == []
    assert result["deferred_write_capabilities"] == [
        {
            "id": "creative.plan_identities",
            "reason": "write_contract_not_ready",
            "side_effect": "write",
            "requires_checkpoint": "before_write",
        }
    ]


def test_tool_allowlist_accepts_bounded_serial_continuity_read_capabilities():
    plan = _plan()
    plan["experts"][1]["required_capabilities"] = [
        "story.canon",
        "knowledge.search",
        "media.character",
        "media.generation_history",
        "memory.preview",
        "script.get",
    ]
    plan["experts"].append(
        {
            "expert": "qa_recovery",
            "required_capabilities": ["context.execution_checkpoint"],
        }
    )

    result = compile_tool_allowlist(plan)
    active_ids = {item["id"] for item in result["active_capabilities"]}

    assert {
        "context.shared_snapshot",
        "context.expert_plan",
        "story.canon",
        "knowledge.search",
        "media.character",
        "media.generation_history",
        "memory.preview",
        "script.get",
        "context.execution_checkpoint",
    } <= active_ids
    assert result["max_capabilities"] < 54


def test_tool_allowlist_route_compiles_from_the_same_expert_plan(monkeypatch):
    from novelvideo.api.routes import agent_memory

    async def fake_plan(**_kwargs):
        return _plan()

    monkeypatch.setattr(agent_memory, "get_shared_agent_expert_plan", fake_plan)
    result = asyncio.run(
        agent_memory.get_shared_agent_tool_allowlist(
            project="project-a",
            canvas_id="canvas-a",
            query="优化这个节点",
            sources="memory",
            mode="observe",
            candidates="canvas.snapshot,canvas.compatibility.emit",
            user={"username": "alice"},
        )
    )

    assert result["schema"] == "agent_tool_allowlist.v1"
    assert result["context_revision"] == "ctx-a"
    assert [item["id"] for item in result["active_capabilities"]] == ["canvas.snapshot"]


def test_explicit_write_candidate_is_honoured_in_execute_mode():
    """显式点名的写能力在 execute 档生效 —— **这条测试曾断言相反的行为**。

    它原先叫 `test_discussion_plan_never_gets_a_write_candidate_in_execute_mode`，
    断言「讨论轮即使显式点名写能力也必须清空」。这编码的是「按用户那句话判权限」的
    旧哲学，而 2026-09-30 的全部工作推翻了它：

      · T-194：可撤销的写入不该被「那句话像不像命令」拦住。
      · T-206：删掉 `side_effect_policy` 的两处消费（同样按那句话判）。
      · 参考库：libtv / tapcanvas / open-storyboard-canvas / Canvas-Director
        **没有一家**按用户话术判权限；open-storyboard 的 auto 模式**放行全部画布写**，
        唯一要人工确认的是删除节点。

    保守这道否决不保护任何东西 —— 真正的保护是「必须 execute 档 + 走写检查点
    （revision 对齐/幂等键/事后条件）+ 付费独立闸门 + 画布写全部可回退」，
    而它制造了 8 次真机失败（最近一次 17:36 `checkpoint is not ready_write`）。
    """

    plan = _plan()
    plan["intent_classification"] = {
        "schema": "agent_intent.v1",
        "action_requested": False,
    }

    result = compile_tool_allowlist(
        plan,
        mode="execute",
        candidates=["canvas.compatibility.emit"],
    )

    assert result["execution_enabled"] is True
    assert "canvas.compatibility.emit" in [
        item["id"] for item in result["active_capabilities"]
    ]

    # 仲裁没给出可写决策时（被阻塞的 route），仍然不给写候选 ——
    # 这是替代「那句话像不像命令」的真实约束：没有可执行的路就别开写。
    blocked = {
        "plan_revision": "plan-1",
        "arbiter": {"decision": "hold_for_evidence_recovery"},
    }
    assert (
        compile_tool_allowlist(blocked, mode="execute")["execution_enabled"] is False
    ), "被阻塞的 route 不该开写"

    # 有目标时兜底给写候选（不依赖那句话），空画布待建结构同理。
    for decision in ("reuse_existing_target", "inspect_then_propose"):
        plan_with_decision = {
            "plan_revision": "plan-1",
            "arbiter": {"decision": decision},
        }
        assert (
            compile_tool_allowlist(plan_with_decision, mode="execute")[
                "execution_enabled"
            ]
            is True
        ), decision


def test_execution_context_allowlist_opens_only_the_bound_canvas_writer():
    result = compile_execution_context_allowlist(
        {
            "execution_id": "execctx:abc",
            "digest": "digest-a",
            "plan_revision": "plan-a",
            "selected_handler": "canvas_command_gateway",
            "capability_id": "canvas.compatibility.emit",
            "side_effect_policy": "write",
        },
        mode="execute",
    )

    assert result["schema"] == "agent_tool_allowlist.v1"
    assert result["injection_mode"] == "execution_context"
    assert result["plan_revision"] == "plan-a"
    assert result["execution_enabled"] is True
    assert result["active_capabilities"] == [
        {
            "id": "canvas.compatibility.emit",
            "side_effect": "canvas_write",
            "source_experts": [],
            "reason": "execution_context_explicit_write",
            "requires_checkpoint": "before_write",
        }
    ]
    assert result["agent_fleet"]["executor"] == "canvas_command_gateway"


def test_execution_context_allowlist_never_promotes_a_read_context():
    result = compile_execution_context_allowlist(
        {
            "execution_id": "execctx:abc",
            "digest": "digest-a",
            "plan_revision": "plan-a",
            "capability_id": "canvas.snapshot",
            "side_effect_policy": "read",
        },
        mode="execute",
    )

    assert result["execution_enabled"] is False
    assert [item["id"] for item in result["active_capabilities"]] == ["canvas.snapshot"]
    assert result["active_capabilities"][0]["side_effect"] == "read"


def test_server_dispatch_write_candidate_survives_a_non_action_sentence():
    """服务端派发器点名的写能力，不该被「用户那句话没有动词」清空。

    回归 2026-09-30 17:36 真机（`checkpoint is not ready_write` 的第二次复现，
    原因已从 `execution_context_write_policy_missing` 变成
    `capability_missing_from_runtime_allowlist`）：用户发的是修复型请求，
    整句无动作动词 → 清单编译器把服务端派发器**写死**的写能力一并清空 →
    清单里没有写能力 → 检查点拒。

    `candidates` 并非只由派发器使用：`context.execution_checkpoint` 能力卡把
    `candidates` 列为可选参数，**模型可以自己传**。所以放宽必须有边界 ——
    只有 `trusted_candidates=True`（服务端派发器）才绕过那句否决。
    """

    from novelvideo.chat.execution_checkpoint import build_execution_checkpoint
    from novelvideo.chat.execution_context import build_execution_context
    from novelvideo.chat.tool_allowlist import compile_tool_allowlist

    context = build_execution_context(
        canonical_intent="在被连接的分镜节点提示词中显式引用参考图",
        project_id="project-1",
        canvas_id="canvas-1",
        observed_canvas_revision=89,
        target_node_ids=[],
        selected_handler="village_canvas_dispatch_action",
        capability_id="canvas.snapshot",
        side_effect_policy="read",
        idempotency_key="agent:compat:abc",
        expected_postconditions=[
            {
                "type": "canvas_revision_observed",
                "field": "canvas.revision",
                "equals": 89,
            }
        ],
        plan_revision="plan-1",
    )
    plan = {
        "plan_revision": "plan-1",
        "intent_classification": {"action_requested": False},
        "arbiter": {"decision": "inspect_then_propose"},
        "execution_context": context,
    }
    candidates = ["canvas.snapshot", "canvas.compatibility.emit"]

    allowlist = compile_tool_allowlist(plan, candidates=candidates, mode="execute")

    assert allowlist["execution_enabled"] is True
    assert "canvas.compatibility.emit" in [
        item["id"] for item in allowlist["active_capabilities"]
    ]

    checkpoint = build_execution_checkpoint(
        expert_plan=plan,
        allowlist=allowlist,
        capability_id="canvas.compatibility.emit",
        expected_plan_revision="plan-1",
        expected_allowlist_revision=str(allowlist.get("allowlist_revision") or ""),
        confirm=True,
        execution_context=context,
    )
    assert checkpoint["status"] == "ready_write"
    assert checkpoint["ready"] is True


def test_only_execute_mode_gates_writes_now():
    """写能力只剩一道硬门：`execute` 档。那句话不再是判据。

    上限仍然清楚：`observe` / `prepare_write` 一律不激活写能力，
    与「用户说了什么」无关。
    """

    from novelvideo.chat.tool_allowlist import compile_tool_allowlist

    plan = {
        "plan_revision": "plan-1",
        "intent_classification": {"action_requested": False},
        "arbiter": {"decision": "inspect_then_propose"},
    }
    candidates = ["canvas.compatibility.emit"]

    for mode in ("observe", "prepare_write"):
        result = compile_tool_allowlist(plan, candidates=candidates, mode=mode)
        assert result["execution_enabled"] is False, mode

    assert (
        compile_tool_allowlist(plan, candidates=candidates, mode="execute")[
            "execution_enabled"
        ]
        is True
    )


def test_execution_context_allowlist_ignores_the_sentence_derived_policy():
    """`compile_execution_context_allowlist` 也不该看 `side_effect_policy`。

    回归 2026-09-30：**同一个文件里有两个函数**做能力清单 ——
    `compile_tool_allowlist`（从 expert plan 推，GET 检查点路径走它）与
    `compile_execution_context_allowlist`（从已签发的 context 推，POST 检查点路径走它）。
    我先只改了前者，漏了后者，于是同一天里 GET 路径通了、POST 路径仍报
    `checkpoint is not ready_write`。两条路径同样放行才是真的修好。

    这条测试原先也不存在：我当时的验证只覆盖了 GET 那条路径，
    于是在 POST 路径上「测过等于没测」。
    """

    from novelvideo.chat.execution_context import build_execution_context
    from novelvideo.chat.tool_allowlist import compile_execution_context_allowlist

    def context(policy: str, mode: str = "execute"):
        return compile_execution_context_allowlist(
            build_execution_context(
                canonical_intent="把这些节点连起来",
                project_id="project-1",
                canvas_id="canvas-1",
                observed_canvas_revision=68,
                target_node_ids=["n1"],
                selected_handler="village_canvas_dispatch_action",
                capability_id="canvas.compatibility.emit",
                side_effect_policy=policy,
                idempotency_key="k1",
                expected_postconditions=[
                    {
                        "type": "canvas_revision_observed",
                        "field": "canvas.revision",
                        "equals": 68,
                    }
                ],
                plan_revision="r1",
            ),
            mode=mode,
        )

    # 那句话被分类成什么，与能不能执行无关。
    for policy in ("read", "write"):
        assert context(policy)["execution_enabled"] is True, policy

    # 保护仍在：非 execute 档一律不激活写能力。
    for mode in ("observe", "prepare_write"):
        assert context("write", mode=mode)["execution_enabled"] is False, mode
