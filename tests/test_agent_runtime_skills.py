from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.agent_tools.native_registry import build_native_registry
from novelvideo.agent_tools.skills import (
    AGENT_SKILLS_DIR_ENV,
    MAX_SKILL_BYTES,
    SKILL_ACTIVATION_SCHEMA,
    SKILL_SOURCE_BUNDLED,
    agent_skills_root,
    build_skill_tool_description,
    list_agent_skills,
    load_agent_skill,
)
from novelvideo.chat.skill_routing import (
    build_skill_preactivation_block,
    route_agent_skill,
    skill_routing_prompt,
)
from novelvideo.chat.skill_routing_cases import SKILL_ROUTING_CASES


def _write_skill(root, name: str, content: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_bytes(content.encode("utf-8"))


ACTIVATION = """---\nname: demo\ndescription: demo\nactivation:\n  workflow: one-click-film\n  agents:\n    - peer\n  flags:\n    - paid_media_requires_task_authorization\n  fence:\n    - never report queued as done\n---\n\n# Demo\n"""


def test_agent_skill_catalog_loads_exact_document_with_receipt(tmp_path, monkeypatch):
    root = tmp_path / "agent_skills"
    root.mkdir()
    content = "---\nname: demo\ndescription: deterministic contract\n---\n\n# Demo\n"
    _write_skill(root, "demo-skill", content)
    monkeypatch.setenv(AGENT_SKILLS_DIR_ENV, str(root))

    assert agent_skills_root() == root
    catalog = list_agent_skills()
    assert [item.name for item in catalog] == ["demo-skill"]
    loaded = load_agent_skill("demo-skill")
    assert loaded.content == content
    assert loaded.description == "deterministic contract"
    assert loaded.size_bytes == len(content.encode("utf-8"))
    assert loaded.sha256 == hashlib.sha256(content.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("name", ["../secret", "demo/skill", "Demo", "", "a" * 81])
def test_agent_skill_names_reject_paths_and_noncanonical_values(
    tmp_path, monkeypatch, name
):
    root = tmp_path / "agent_skills"
    root.mkdir()
    monkeypatch.setenv(AGENT_SKILLS_DIR_ENV, str(root))

    with pytest.raises(ValueError):
        load_agent_skill(name)


def test_agent_skill_oversize_and_missing_fail_explicitly(tmp_path, monkeypatch):
    root = tmp_path / "agent_skills"
    root.mkdir()
    _write_skill(root, "too-large", "x" * (MAX_SKILL_BYTES + 1))
    monkeypatch.setenv(AGENT_SKILLS_DIR_ENV, str(root))

    with pytest.raises(ValueError, match="exceeds"):
        load_agent_skill("too-large")
    with pytest.raises(ValueError, match="not found"):
        load_agent_skill("missing")


async def test_native_registry_exposes_bounded_skill_tool(tmp_path, monkeypatch):
    root = tmp_path / "agent_skills"
    root.mkdir()
    _write_skill(root, "demo-skill", "---\ndescription: demo\n---\n\n# Demo\n")
    _write_skill(root, "peer-skill", "---\ndescription: peer\n---\n\n# Peer\n")
    monkeypatch.setenv(AGENT_SKILLS_DIR_ENV, str(root))

    registry = build_native_registry()
    assert "skill" in {tool.name for tool in registry.list_tools()}
    payload = json.loads(await registry.invoke("skill", {"name": "demo-skill"}))
    assert payload["ok"] is True
    assert payload["schema"] == "village_agent_skill.v1"
    assert payload["content"].startswith("---")
    assert len(payload["sha256"]) == 64
    switched = json.loads(
        await registry.invoke(
            "skill",
            {
                "action": "switch",
                "name": "peer-skill",
                "reason": "更窄的专用技能",
            },
        )
    )
    assert switched["action"] == "switch"
    assert switched["name"] == "peer-skill"
    assert switched["switch_reason"] == "更窄的专用技能"


def test_village_harness_prompt_keeps_hard_constraints_not_route_sop():
    from novelvideo.chat.village_harness import _SYSTEM_PROMPT

    assert "权限" in _SYSTEM_PROMPT
    assert "事实性" in _SYSTEM_PROMPT
    assert "显式失败" in _SYSTEM_PROMPT
    assert "审计" in _SYSTEM_PROMPT
    assert "加载即激活" in _SYSTEM_PROMPT
    assert "village_canvas_dispatch_action" not in _SYSTEM_PROMPT
    assert "village_canvas_capability" not in _SYSTEM_PROMPT
    assert "dynamic_execution" not in _SYSTEM_PROMPT


def test_village_harness_retries_output_but_never_replays_tools():
    from novelvideo.chat.village_harness import (
        _VILLAGE_AGENT_RETRIES,
        _village_agent_usage_limits,
    )

    assert _VILLAGE_AGENT_RETRIES == {"tools": 0, "output": 1}
    assert _village_agent_usage_limits("普通任务") == (32, 32)
    assert _village_agent_usage_limits("[SERIAL_CONTINUITY_EVIDENCE_GATE]") == (32, 32)


def test_recoverable_village_errors_share_retry_contract():
    from novelvideo.chat.village_harness import (
        VillageAgentToolLoopError,
        VillageAgentWorkerLostError,
        _is_transport_disconnect_error,
        _transport_retry_delay_seconds,
        _tool_calls_are_read_only,
    )

    tool_loop = VillageAgentToolLoopError(
        "evidence gate rejected the response",
        turn_id="turn-1",
        pending_tool="village_canvas_read_compact",
        tool_names=["skill", "village_canvas_capability"],
        has_side_effect=False,
        last_event="serial_continuity_validation",
    )
    assert tool_loop.turn_id == "turn-1"
    assert tool_loop.pending_tool == "village_canvas_read_compact"
    assert tool_loop.tool_names == ("skill", "village_canvas_capability")
    assert tool_loop.has_side_effect is False
    assert tool_loop.last_event == "serial_continuity_validation"

    worker_lost = VillageAgentWorkerLostError(
        "worker lost",
        tool_names=("freezone_emit_canvas_command",),
    )
    assert worker_lost.has_side_effect is True
    assert worker_lost.tool_names == ("freezone_emit_canvas_command",)

    default_tool_loop = VillageAgentToolLoopError("unclassified tool loop")
    assert default_tool_loop.has_side_effect is True
    assert _is_transport_disconnect_error(
        RuntimeError("peer closed connection without sending complete message body")
    )
    assert _is_transport_disconnect_error(
        RuntimeError("status_code: 503 auth_concurrency_limit")
    )
    assert _is_transport_disconnect_error(
        RuntimeError(
            "status_code: 520, model_name: gemini-3.8-flash, "
            "retryable: True, retry_after: 60"
        )
    )
    assert _is_transport_disconnect_error(
        RuntimeError("status_code: 504 upstream gateway timeout")
    )
    assert (
        _transport_retry_delay_seconds(RuntimeError("status_code: 520 retry_after: 60"))
        == 60.0
    )
    assert (
        _transport_retry_delay_seconds(
            RuntimeError("status_code: 503 auth_concurrency_limit")
        )
        == 8.0
    )
    assert _tool_calls_are_read_only(())
    assert _tool_calls_are_read_only(("skill", "village_canvas_capability"))
    assert not _tool_calls_are_read_only(("freezone_emit_canvas_command",))


def test_authoritative_canvas_receipt_requires_complete_server_receipt():
    from novelvideo.chat.village_harness import _authoritative_canvas_receipt

    receipt = {
        "server_applied": True,
        "revision": 41,
        "applied_ops": 2,
        "command_id": "turn-demo:dynamic:abc123",
    }
    payload = {"ok": True, "canvas_receipt": receipt}

    assert _authoritative_canvas_receipt(payload) == {
        "revision": 41,
        "applied_ops": 2,
        "command_id": "turn-demo:dynamic:abc123",
    }
    assert (
        _authoritative_canvas_receipt(
            {"ok": True, "canvas_receipt": {**receipt, "command_id": ""}}
        )
        is None
    )
    assert (
        _authoritative_canvas_receipt(
            {"ok": True, "canvas_receipt": {**receipt, "server_applied": False}}
        )
        is None
    )


def _install_fake_village_agent(monkeypatch, frames, error):
    import pydantic_ai

    from novelvideo.chat import village_harness

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def run_stream_events(self, *args, **kwargs):
            class Events:
                def __init__(self):
                    self._frames = iter(frames)

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_args):
                    return False

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    try:
                        return next(self._frames)
                    except StopIteration as exc:
                        raise error from exc

            return Events()

    class Registry:
        def list_tools(self):
            return []

    monkeypatch.setattr(pydantic_ai, "Agent", FakeAgent)
    monkeypatch.setattr(village_harness, "build_native_registry", lambda: Registry())
    monkeypatch.setattr(
        village_harness,
        "resolve_village_agent_model",
        lambda value: value or "test-model",
    )
    monkeypatch.setattr(
        village_harness,
        "get_direct_pydantic_model",
        lambda *_args, **_kwargs: object(),
    )
    return village_harness.VillageAgentThread(
        id="village-test",
        model_id="test-model",
        scope_kind="project",
    )


async def test_village_harness_finishes_committed_write_when_output_transport_drops(
    monkeypatch,
):
    tool_result = json.dumps(
        {
            "ok": True,
            "canvas_receipt": {
                "server_applied": True,
                "revision": 41,
                "applied_ops": 2,
                "command_id": "turn-demo:dynamic:abc123",
            },
        }
    )
    tool_frame = SimpleNamespace(
        event_kind="function_tool_result",
        part=SimpleNamespace(
            tool_call_id="call-1",
            tool_name="village_canvas_dispatch_action",
            outcome="completed",
            content=tool_result,
        ),
        content=tool_result,
    )
    thread = _install_fake_village_agent(
        monkeypatch,
        [tool_frame],
        RuntimeError("Connection error."),
    )

    events = [
        event
        async for event in thread.stream(
            "优化当前节点",
            current_project="project-1",
            current_canvas="canvas-1",
        )
    ]

    assert [event.type for event in events] == [
        "thread_started",
        "tool_update",
        "assistant_delta",
        "complete",
    ]
    assert "revision 41" in str(events[-1].text)
    assert "已真实落盘" in str(events[-1].text)


async def test_village_harness_finishes_pending_durable_run_when_model_drops(
    monkeypatch,
):
    from novelvideo.chat import village_harness

    thread = _install_fake_village_agent(
        monkeypatch,
        [],
        RuntimeError(
            "status_code: 429, model_name: gemini-3.8-flash, "
            "body: {'message': 'Upstream quota exhausted'}"
        ),
    )

    class PendingDelivery:
        def bind_contract(self, contract):
            del contract

        def record(self, *_args, **_kwargs):
            return None

        def receipt(self, *, final_text=""):
            del final_text
            return {
                "schema": "village_turn_delivery_receipt.v1",
                "status": "pending",
                "reason_code": "terminal_delivery_evidence_pending",
                "delivery_mode": "async_artifact",
                "media_type": "video",
                "allow_finish": False,
                "verified_evidence": [],
                "pending_evidence": [
                    {
                        "kind": "tool_receipt",
                        "tool": "village_canvas_dispatch_action",
                        "source_ref": "wfr_test_pending",
                        "status": "running",
                    }
                ],
                "blocked_evidence": [],
                "observation_count": 1,
            }

    monkeypatch.setattr(village_harness, "TurnDeliveryRuntime", PendingDelivery)

    events = [
        event
        async for event in thread.stream(
            "从当前脚本节点继续到最终成片",
            current_project="project-1",
            current_canvas="canvas-1",
        )
    ]

    assert [event.type for event in events] == [
        "thread_started",
        "assistant_delta",
        "complete",
    ]
    assert "wfr_test_pending" in str(events[-1].text)
    assert "持久工作流已受理或启动" in str(events[-1].text)
    closing = events[-1].raw["agent_turn_closing_receipt"]
    assert closing["delivery_status"] == "pending"


async def test_village_harness_does_not_hide_failure_without_authoritative_write(
    monkeypatch,
):
    thread = _install_fake_village_agent(
        monkeypatch,
        [],
        RuntimeError("Connection error."),
    )

    with pytest.raises(RuntimeError, match="Connection error"):
        _ = [
            event
            async for event in thread.stream(
                "优化当前节点",
                current_project="project-1",
                current_canvas="canvas-1",
            )
        ]


def test_tool_boundary_normalizes_only_unambiguous_json_scalars():
    from novelvideo.chat.village_harness import _normalize_schema_scalars

    schema = {
        "type": "object",
        "properties": {
            "task_authorization": {
                "type": "object",
                "properties": {
                    "require_video_confirmation": {"type": "boolean", "enum": [False]},
                    "allow_paid_media": {"type": "boolean"},
                    "max_paid_starts": {"type": "integer", "minimum": 0},
                    "note": {"type": "string"},
                },
            },
            "targets": {"type": "array", "items": {"type": "integer"}},
            "ratio": {"anyOf": [{"type": "number"}, {"type": "string"}]},
        },
    }
    normalized = _normalize_schema_scalars(
        {
            "task_authorization": {
                "require_video_confirmation": "False",
                "allow_paid_media": "true",
                "max_paid_starts": "0",
                "note": "False",
            },
            "targets": ["3", "shot-1"],
            "ratio": "1.5",
        },
        schema,
    )

    assert normalized["task_authorization"]["require_video_confirmation"] is False
    assert normalized["task_authorization"]["allow_paid_media"] is True
    assert normalized["task_authorization"]["max_paid_starts"] == 0
    # A free-text field keeps the model's literal text untouched.
    assert normalized["task_authorization"]["note"] == "False"
    # Only the literal integer is converted; the node id keeps its identity.
    assert normalized["targets"] == [3, "shot-1"]
    assert normalized["ratio"] == 1.5


def test_tool_boundary_rejects_ambiguous_scalar_values():
    from jsonschema import Draft202012Validator

    from novelvideo.chat.village_harness import _normalize_schema_scalars

    schema = {
        "type": "object",
        "properties": {
            "require_video_confirmation": {"type": "boolean", "enum": [False]}
        },
    }
    normalized = _normalize_schema_scalars(
        {"require_video_confirmation": "maybe"}, schema
    )
    assert normalized == {"require_video_confirmation": "maybe"}
    assert list(Draft202012Validator(schema).iter_errors(normalized))


def _catalog_root(tmp_path, monkeypatch, *skills):
    root = tmp_path / "agent_skills"
    root.mkdir()
    for name in skills:
        content = (
            ACTIVATION if name == "demo-skill" else "---\ndescription: peer\n---\n"
        )
        _write_skill(root, name, content)
    monkeypatch.setenv(AGENT_SKILLS_DIR_ENV, str(root))
    return root


def test_activation_contract_is_machine_readable_on_load(tmp_path, monkeypatch):
    _catalog_root(tmp_path, monkeypatch, "demo-skill", "peer")

    skill = load_agent_skill("demo-skill")
    assert skill.activation is not None
    assert skill.activation.workflow == "one-click-film"
    assert skill.activation.agents == ("peer",)
    assert skill.activation.flags == ("paid_media_requires_task_authorization",)
    assert skill.activation.fence == ("never report queued as done",)

    payload = skill.public_load()
    assert payload["activation"]["schema"] == SKILL_ACTIVATION_SCHEMA
    assert payload["activation"]["binding"] == "mandatory"
    assert payload["activation"]["workflow"] == "one-click-film"
    assert "activation_status" not in payload


def test_catalog_surfaces_routing_contract_without_loading(tmp_path, monkeypatch):
    _catalog_root(tmp_path, monkeypatch, "demo-skill", "peer")

    description = build_skill_tool_description()
    assert "workflow=one-click-film" in description
    item = next(item for item in list_agent_skills() if item.name == "demo-skill")
    routing = item.public_item()["activation"]
    assert routing == {
        "workflow": "one-click-film",
        "agents": ["peer"],
        "flags": ["paid_media_requires_task_authorization"],
        "fence_count": 1,
    }
    # The catalog must not ship the whole fence body for every skill.
    assert "never report queued as done" not in description


def test_runtime_route_matches_every_live_semantic_case_without_model() -> None:
    skills = list_agent_skills()

    misses = [
        {
            "expected": case.skill_name,
            "actual": route_agent_skill(case.prompt, skills=skills).skill_name,
        }
        for case in SKILL_ROUTING_CASES
        if route_agent_skill(case.prompt, skills=skills).skill_name != case.skill_name
    ]

    assert misses == []


def test_skill_preactivation_grants_knowledge_not_flags() -> None:
    decision = route_agent_skill(
        next(
            case.prompt
            for case in SKILL_ROUTING_CASES
            if case.skill_name == "village-canvas-one-click-film"
        )
    )
    block = build_skill_preactivation_block(decision)

    assert decision.skill_name == "village-canvas-one-click-film"
    assert "village-canvas-one-click-film" in block
    assert "permissions: pre-activation grants no flags" in block
    assert "paid media still requires a real" in block
    assert "paid_media_requires_task_authorization" not in block
    assert decision.public()["load_receipt"] is False
    assert decision.public()["permissions_granted"] == []


def test_durable_production_guard_survives_live_preactivation_directive() -> None:
    prompt = (
        "这个项目已经有角色、分镜和部分首帧，我想从当前进度自动推进到整集成片，"
        "中途可以暂停、恢复，并保留已经成功的阶段。运行时已经在首次工具调用前"
        "预激活了候选 Skill 的说明性知识；本轮不要调用 skill，也不要修改画布、"
        "创建任务或启动任何媒体。只依据预激活上下文，用两句话回答候选 Skill 名称、"
        "绑定的 workflow id，以及任意一条 fence 原文。"
    )

    decision = route_agent_skill(prompt)

    assert decision.skill_name == "village-canvas-one-click-film"
    assert decision.reason == "durable_production_control"


def test_skill_routing_ignores_injected_canvas_context() -> None:
    prompt = (
        "[VILLAGE_CANVAS_USER_CONTEXT]\n"
        "username: local\n"
        "scope: project:test\n\n"
        "[CURRENT_CANVAS_CONTEXT]\n"
        + ("节点状态、任务、剧本、角色与分镜。" * 2_000)
        + "\n\n[USER_MESSAGE]\n"
        "这个项目已经有角色、分镜和部分首帧，我想从当前进度自动推进到整集成片，"
        "中途可以暂停、恢复，并保留已经成功的阶段。"
    )

    assert skill_routing_prompt(prompt).startswith("这个项目")
    assert route_agent_skill(prompt).skill_name == "village-canvas-one-click-film"


@pytest.mark.parametrize(
    ("label", "contract", "code"),
    [
        (
            "unknown-workflow",
            "activation:\n  workflow: not-a-workflow\n",
            "skill_activation_workflow_unknown",
        ),
        (
            "unresolved-agent",
            "activation:\n  workflow: one-click-film\n  agents:\n    - ghost\n",
            "skill_activation_agent_unresolved",
        ),
        (
            "unknown-key",
            "activation:\n  workflow: one-click-film\n  force_enter: yes\n",
            "skill_activation_invalid",
        ),
        (
            "missing-workflow",
            "activation:\n  flags:\n    - paid_media_requires_task_authorization\n",
            "skill_activation_invalid",
        ),
        (
            "bad-flag",
            "activation:\n  workflow: one-click-film\n  flags:\n    - Paid-Media\n",
            "skill_activation_invalid",
        ),
    ],
)
def test_broken_activation_contracts_are_rejected_at_load(
    tmp_path, monkeypatch, label, contract, code
):
    root = tmp_path / "agent_skills"
    root.mkdir()
    _write_skill(
        root, "demo-skill", f"---\ndescription: {label}\n{contract}---\n\n# Demo\n"
    )
    monkeypatch.setenv(AGENT_SKILLS_DIR_ENV, str(root))

    # Listing stays available so the rest of the catalog remains usable.
    listing = list_agent_skills()
    assert listing[0].activation is None
    assert listing[0].public_item()["activation_error_code"] == code
    # Loading the broken contract is a hard, explicit failure.
    with pytest.raises(ValueError) as excinfo:
        load_agent_skill("demo-skill")
    assert getattr(excinfo.value, "code", "") == code


def test_shipped_one_click_film_contract_resolves_inside_the_real_catalog(monkeypatch):
    monkeypatch.delenv(AGENT_SKILLS_DIR_ENV, raising=False)

    skill = load_agent_skill("village-canvas-one-click-film")
    assert skill.activation is not None
    assert skill.activation.workflow == "one-click-film"
    assert skill.activation.agents, "one-click-film must declare its hand-off roster"
    for agent in skill.activation.agents:
        assert load_agent_skill(agent).name == agent

    # Every shipped contract must resolve, or the catalog reports why.
    broken = [item.name for item in list_agent_skills() if item.activation_error]
    assert broken == []


def test_installed_skill_catalog_exposes_stable_identity(monkeypatch):
    monkeypatch.delenv(AGENT_SKILLS_DIR_ENV, raising=False)

    skills = list_agent_skills()
    assert skills
    for skill in skills:
        item = skill.public_item()
        assert item["name"] == skill.name
        assert item["source"] == SKILL_SOURCE_BUNDLED
        assert isinstance(item["version"], str)
        assert item["bytes"] == skill.size_bytes > 0
        assert item["sha256"] == skill.sha256
        assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"])


def test_skill_identity_reads_top_level_and_metadata_version(tmp_path, monkeypatch):
    root = tmp_path / "agent_skills"
    root.mkdir()
    _write_skill(
        root,
        "top-level-version",
        "---\ndescription: demo\nversion: 2.2.0\n---\n\n# Demo\n",
    )
    _write_skill(
        root,
        "metadata-version",
        "---\ndescription: demo\nmetadata:\n  version: 1.0.0\n---\n\n# Demo\n",
    )
    monkeypatch.setenv(AGENT_SKILLS_DIR_ENV, str(root))

    versions = {
        item.name: item.public_item()["version"] for item in list_agent_skills()
    }

    assert versions == {
        "metadata-version": "1.0.0",
        "top-level-version": "2.2.0",
    }


def test_frontend_skill_keys_resolve_to_installed_skills(monkeypatch):
    monkeypatch.delenv(AGENT_SKILLS_DIR_ENV, raising=False)
    project_root = Path(__file__).resolve().parents[1]
    source = (
        project_root
        / "frontend"
        / "src"
        / "features"
        / "superchat"
        / "canvas-agent-skills.ts"
    ).read_text(encoding="utf-8")

    keys = set(re.findall(r'skillKey:\s*"([^"]+)"', source))
    installed = {skill.name for skill in list_agent_skills()}

    assert len(keys) == 9
    assert keys <= installed


def test_skill_index_document_matches_installed_skills(monkeypatch):
    monkeypatch.delenv(AGENT_SKILLS_DIR_ENV, raising=False)
    project_root = Path(__file__).resolve().parents[1]
    index = (project_root / "agent_skills" / "INDEX.md").read_text(encoding="utf-8")

    documented = set(re.findall(r"^\|\s*`([^`]+)`", index, flags=re.MULTILINE))
    installed = {skill.name for skill in list_agent_skills()}

    assert documented == installed


def test_quoted_const_and_null_enum_literals_are_normalized():
    """模型把 None 写成 "None"、把 1 写成 "1"，必须还原成声明的字面量。

    回归 2026-09-30 真机（截图里的两个错，各出现 5 次）：
      contract.delivery.media_type  'None' is not one of [None, 'image', 'video', 'audio']
      contract.version             1 was expected

    两者都是**同一个根因**：`_normalize_schema_scalars` 只处理「声明了单一 type
    的标量字段」，于是这两类字段从来没被归一化过——
      · `media_type` 的 type 是**数组** `["string", "null"]`，
        `str(schema["type"])` 拼成 "['string', 'null']"，与任何类型名都不匹配；
      · `version` 只有 `const` 没有 `type`，`declared` 是空串。
    结果模型一个引号就让整次工具调用被判 invalid，而它其实完全表达对了意图。
    """

    from novelvideo.chat.village_harness import _normalize_schema_scalars

    schema = {
        "type": "object",
        "properties": {
            "version": {"const": 1},
            "media_type": {
                "type": ["string", "null"],
                "enum": [None, "image", "video", "audio"],
            },
            "note": {"type": "string"},
        },
    }

    normalized = _normalize_schema_scalars(
        {"version": "1", "media_type": "None", "note": "None"}, schema
    )

    assert normalized["version"] == 1
    assert type(normalized["version"]) is int
    assert normalized["media_type"] is None
    # 普通文本字段不许被改：note 没有 const/enum/null 类型声明。
    assert normalized["note"] == "None"


def test_null_enum_normalization_accepts_common_spellings_but_stays_unique():
    from novelvideo.chat.village_harness import _normalize_schema_scalars

    schema = {
        "type": "object",
        "properties": {
            "media_type": {
                "type": ["string", "null"],
                "enum": [None, "image", "video", "audio"],
            },
            "mode": {"type": "string", "enum": ["response", "state_change"]},
        },
    }

    for spelling in ("None", "none", "null", "NULL", ""):
        normalized = _normalize_schema_scalars({"media_type": spelling}, schema)
        assert normalized["media_type"] is None, spelling

    # 枚举里的正常取值不受影响，也不该被误改成 None。
    for value in ("image", "video", "audio"):
        assert (
            _normalize_schema_scalars({"media_type": value}, schema)["media_type"]
            == value
        )

    # 不在枚举里的字符串保持原样，交给严格校验去报错。
    assert (
        _normalize_schema_scalars({"mode": "something-else"}, schema)["mode"]
        == "something-else"
    )
