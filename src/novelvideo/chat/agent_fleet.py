"""Dynamic specialist roster for the Village Canvas director.

The roster is an orchestration contract, not a second execution engine.  A
director selects the smallest useful set of roles for the current intent;
existing WorkflowRun, CanvasCommandGateway, and model services remain the
authorities that perform side effects.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
import threading
from typing import Any

from novelvideo.chat.intent_contract import classify_agent_intent


AGENT_FLEET_SCHEMA = "agent_fleet.v1"
AGENT_FLEET_REVISION_PREFIX = "agent-fleet.v1:"
AGENT_REGISTRY_SCHEMA = "agent_registry.v1"
AGENT_REGISTRY_REVISION_PREFIX = "agent-registry.v1:"
AGENT_EXECUTION_PLAN_SCHEMA = "agent_execution_plan.v1"
AGENT_EXECUTION_PLAN_REVISION_PREFIX = "agent-execution-plan.v1:"
AGENT_HANDOFF_SCHEMA = "agent_handoff.v1"
AGENT_RESULT_SCHEMA = "agent_specialist_result.v1"
AGENT_HANDLER_SCHEMA = "agent_handler.v1"
DEFAULT_MAX_AGENTS = 7


@dataclass(frozen=True, slots=True)
class AgentRoleSpec:
    """Stable metadata for one logical specialist role."""

    agent_id: str
    label: str
    phase: str
    triggers: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    always: bool = False
    side_effect: str = "none"


@dataclass(frozen=True, slots=True)
class AgentHandlerSpec:
    """Canonical invocation seam for one logical specialist role.

    Handlers point at existing capability/workflow authorities; they do not
    create a second executor or carry provider credentials.
    """

    agent_id: str
    handler_id: str
    invocation: str
    capability_ids: tuple[str, ...] = ()
    result_schema: str = AGENT_RESULT_SCHEMA


AGENT_ROLE_SPECS: tuple[AgentRoleSpec, ...] = (
    AgentRoleSpec(
        agent_id="director",
        label="总导演",
        phase="plan",
        capabilities=("context.shared_snapshot", "context.expert_plan"),
        always=True,
    ),
    AgentRoleSpec(
        agent_id="canvas_observer",
        label="画布状态专家",
        phase="observe",
        triggers=(r"画布|节点|连线|资产|引用|canvas|node|edge|asset|reference",),
        capabilities=("canvas.snapshot", "canvas.viewport"),
        depends_on=("director",),
        always=False,
    ),
    AgentRoleSpec(
        agent_id="narrative",
        label="叙事与剧本专家",
        phase="domain",
        triggers=(r"剧本|故事|剧情|叙事|节奏|角色弧|编剧|script|story|narrative",),
        capabilities=("script.get", "story.canon", "knowledge.search"),
        depends_on=("director",),
    ),
    AgentRoleSpec(
        agent_id="asset_continuity",
        label="角色场景资产专家",
        phase="domain",
        triggers=(
            r"角色|人物|场景|道具|三视图|设定|身份|连续性|资产|character|scene|prop|identity|continuity",
        ),
        capabilities=("media.character", "canvas.snapshot", "knowledge.load_reference"),
        depends_on=("director", "canvas_observer"),
    ),
    AgentRoleSpec(
        agent_id="shotcraft",
        label="分镜与镜头专家",
        phase="domain",
        triggers=(r"分镜|镜头|机位|运镜|动作|打戏|镜头语言|shot|camera|motion|action",),
        capabilities=("story.canon", "knowledge.search", "media.generation_history"),
        depends_on=("director", "canvas_observer"),
    ),
    AgentRoleSpec(
        agent_id="prompt_compiler",
        label="模型提示词编译专家",
        phase="compile",
        triggers=(
            r"提示词|prompt|模型|生图|图片|视频|音频|渲染|风格|画质|尺寸|image|video|audio|render",
        ),
        capabilities=("knowledge.search", "knowledge.load_reference", "context.shared_snapshot"),
        depends_on=("director",),
    ),
    AgentRoleSpec(
        agent_id="production_executor",
        label="生产执行专家",
        phase="execute",
        triggers=(
            r"生成|创建|提交|执行|运行|重试|恢复|合成|导出|制作|generate|create|submit|execute|run|retry|resume|compose|export",
        ),
        capabilities=(
            "canvas.compatibility.emit",
            "workflow.run.control",
            "workflow.run.get",
            "task.get",
        ),
        depends_on=("director", "canvas_observer"),
        side_effect="delegated",
    ),
    AgentRoleSpec(
        agent_id="quality_recovery",
        label="品控与恢复专家",
        phase="verify",
        triggers=(r"失败|错误|校验|检查|质量|一致性|回执|验证|failed|error|verify|quality|receipt",),
        capabilities=("canvas.wait_receipt", "task.get", "context.execution_checkpoint"),
        depends_on=("director",),
        always=False,
    ),
    AgentRoleSpec(
        agent_id="memory_curator",
        label="成长记忆提炼专家",
        phase="learn",
        triggers=(r"记住|记忆|沉淀|学习|复盘|经验|成长|蒸馏|memory|learn|distill|retrospective",),
        capabilities=("memory.preview", "knowledge.search", "knowledge.load_reference"),
        depends_on=("director",),
    ),
)


AGENT_HANDLER_SPECS: tuple[AgentHandlerSpec, ...] = (
    AgentHandlerSpec(
        agent_id="director",
        handler_id="context.expert_plan",
        invocation="capability_index",
        capability_ids=("context.expert_plan",),
    ),
    AgentHandlerSpec(
        agent_id="canvas_observer",
        handler_id="canvas.snapshot",
        invocation="capability_index",
        capability_ids=("canvas.snapshot", "canvas.viewport"),
    ),
    AgentHandlerSpec(
        agent_id="narrative",
        handler_id="knowledge.search",
        invocation="capability_index",
        capability_ids=("knowledge.search", "script.get", "story.canon"),
    ),
    AgentHandlerSpec(
        agent_id="asset_continuity",
        handler_id="canvas.snapshot",
        invocation="capability_index",
        capability_ids=("canvas.snapshot", "media.character", "knowledge.load_reference"),
    ),
    AgentHandlerSpec(
        agent_id="shotcraft",
        handler_id="knowledge.search",
        invocation="capability_index",
        capability_ids=("knowledge.search", "story.canon", "media.generation_history"),
    ),
    AgentHandlerSpec(
        agent_id="prompt_compiler",
        handler_id="creative.optimize_prompt",
        invocation="capability_index",
        capability_ids=("creative.optimize_prompt", "knowledge.search", "knowledge.load_reference"),
    ),
    AgentHandlerSpec(
        agent_id="production_executor",
        handler_id="village_canvas_dispatch_action",
        invocation="dispatch_gateway",
        capability_ids=(
            "canvas.compatibility.emit",
            "workflow.run.control",
            "workflow.run.get",
            "task.get",
        ),
    ),
    AgentHandlerSpec(
        agent_id="quality_recovery",
        handler_id="canvas.wait_receipt",
        invocation="capability_index",
        capability_ids=("canvas.wait_receipt", "task.get", "context.execution_checkpoint"),
    ),
    AgentHandlerSpec(
        agent_id="memory_curator",
        handler_id="memory.preview",
        invocation="capability_index",
        capability_ids=("memory.preview", "knowledge.search", "knowledge.load_reference"),
    ),
)


class AgentRegistryError(RuntimeError):
    """Base error for specialist roster registration failures."""


class AgentRoleConflictError(AgentRegistryError):
    """Raised when a role id is already owned by another manifest."""


class AgentRoleDependencyError(AgentRegistryError):
    """Raised when a role depends on a role that is not registered."""


class AgentHandlerRegistry:
    """Runtime registry for canonical capability invocation seams."""

    def __init__(self, specs: Iterable[AgentHandlerSpec] = ()) -> None:
        self._handlers: dict[str, AgentHandlerSpec] = {}
        self._lock = threading.RLock()
        for spec in specs:
            self.register(spec)

    @staticmethod
    def _validate(spec: AgentHandlerSpec) -> AgentHandlerSpec:
        if not isinstance(spec, AgentHandlerSpec):
            raise TypeError("agent handler must be an AgentHandlerSpec")
        agent_id = str(spec.agent_id or "").strip()[:128]
        handler_id = str(spec.handler_id or "").strip()[:200]
        invocation = str(spec.invocation or "").strip()[:80]
        capabilities = tuple(
            str(item).strip()[:160]
            for item in spec.capability_ids
            if str(item).strip()
        )
        if not agent_id or not handler_id or not invocation:
            raise ValueError("agent handler requires agent_id, handler_id and invocation")
        if len(set(capabilities)) != len(capabilities):
            raise ValueError("agent handler capabilities must be unique")
        return AgentHandlerSpec(
            agent_id=agent_id,
            handler_id=handler_id,
            invocation=invocation,
            capability_ids=capabilities,
            result_schema=str(spec.result_schema or AGENT_RESULT_SCHEMA).strip()[:120],
        )

    def register(self, spec: AgentHandlerSpec, *, replace: bool = False) -> None:
        normalized = self._validate(spec)
        with self._lock:
            if normalized.agent_id in self._handlers and not replace:
                raise AgentRoleConflictError(
                    f"agent handler already registered: {normalized.agent_id}"
                )
            self._handlers[normalized.agent_id] = normalized

    def get(self, agent_id: object) -> AgentHandlerSpec | None:
        with self._lock:
            return self._handlers.get(str(agent_id or "").strip())

    def specs(self) -> tuple[AgentHandlerSpec, ...]:
        with self._lock:
            return tuple(self._handlers.values())


DEFAULT_AGENT_HANDLER_REGISTRY = AgentHandlerRegistry(AGENT_HANDLER_SPECS)


def _handler_manifest(
    agent_id: str,
    *,
    capabilities: Iterable[object] = (),
    registry: AgentHandlerRegistry | None = None,
) -> dict[str, Any]:
    """Resolve a handler without inventing a role-specific execution engine."""

    handler_registry = registry or DEFAULT_AGENT_HANDLER_REGISTRY
    spec = handler_registry.get(agent_id)
    if spec is None:
        capability_ids = tuple(
            str(item).strip()[:160] for item in capabilities if str(item).strip()
        )
        spec = AgentHandlerSpec(
            agent_id=str(agent_id).strip()[:128],
            handler_id="capability_dispatch",
            invocation="capability_index",
            capability_ids=capability_ids,
        )
    return {
        "schema": AGENT_HANDLER_SCHEMA,
        "handler_id": spec.handler_id,
        "invocation": spec.invocation,
        "capability_ids": list(spec.capability_ids)[:16],
        "result_schema": spec.result_schema,
    }


def _role_payload(spec: AgentRoleSpec) -> dict[str, Any]:
    """Return the canonical, JSON-safe manifest used for registry revisions."""

    return {
        "agent_id": spec.agent_id,
        "label": spec.label,
        "phase": spec.phase,
        "triggers": list(spec.triggers),
        "capabilities": list(spec.capabilities),
        "depends_on": list(spec.depends_on),
        "always": bool(spec.always),
        "side_effect": spec.side_effect,
    }


class AgentRoleRegistry:
    """Thread-safe dynamic registry for logical specialist roles.

    The registry stores *descriptors*, not executable handlers.  This keeps
    role selection deterministic and lets the existing Hermes/WorkflowRun
    execution path remain the only side-effect authority.  Integrations may
    register a new specialist at runtime without changing keyword branches or
    the core dispatcher.
    """

    def __init__(self, specs: Iterable[AgentRoleSpec] = ()) -> None:
        self._roles: dict[str, AgentRoleSpec] = {}
        self._lock = threading.RLock()
        for spec in specs:
            self.register(spec)

    @staticmethod
    def _validate(spec: AgentRoleSpec) -> AgentRoleSpec:
        if not isinstance(spec, AgentRoleSpec):
            raise TypeError("agent role must be an AgentRoleSpec")
        agent_id = str(spec.agent_id or "").strip()
        label = str(spec.label or "").strip()
        phase = str(spec.phase or "").strip()
        side_effect = str(spec.side_effect or "none").strip()
        if not agent_id or len(agent_id) > 128:
            raise ValueError("agent_id must contain 1-128 characters")
        if not label or len(label) > 200:
            raise ValueError("agent label must contain 1-200 characters")
        if not phase or len(phase) > 80:
            raise ValueError("agent phase must contain 1-80 characters")
        if side_effect not in {"none", "delegated"}:
            raise ValueError("agent side_effect must be none or delegated")
        if agent_id in spec.depends_on:
            raise ValueError("an agent role cannot depend on itself")
        if len(set(spec.triggers)) != len(spec.triggers):
            raise ValueError("agent triggers must be unique")
        if len(set(spec.capabilities)) != len(spec.capabilities):
            raise ValueError("agent capabilities must be unique")
        if len(set(spec.depends_on)) != len(spec.depends_on):
            raise ValueError("agent dependencies must be unique")
        return AgentRoleSpec(
            agent_id=agent_id,
            label=label,
            phase=phase,
            triggers=tuple(str(item).strip() for item in spec.triggers if str(item).strip()),
            capabilities=tuple(
                str(item).strip() for item in spec.capabilities if str(item).strip()
            ),
            depends_on=tuple(
                str(item).strip() for item in spec.depends_on if str(item).strip()
            ),
            always=bool(spec.always),
            side_effect=side_effect,
        )

    def register(self, spec: AgentRoleSpec, *, replace: bool = False) -> None:
        normalized = self._validate(spec)
        with self._lock:
            if normalized.agent_id in self._roles and not replace:
                raise AgentRoleConflictError(
                    f"agent role already registered: {normalized.agent_id}"
                )
            missing = [
                dependency
                for dependency in normalized.depends_on
                if dependency not in self._roles and dependency != normalized.agent_id
            ]
            if missing:
                raise AgentRoleDependencyError(
                    "missing agent role dependencies: " + ", ".join(missing)
                )
            self._roles[normalized.agent_id] = normalized

    def unregister(self, agent_id: object) -> bool:
        normalized = str(agent_id or "").strip()
        with self._lock:
            if normalized not in self._roles:
                return False
            dependents = [
                spec.agent_id
                for spec in self._roles.values()
                if normalized in spec.depends_on
            ]
            if dependents:
                raise AgentRoleDependencyError(
                    f"agent role is required by: {', '.join(sorted(dependents))}"
                )
            del self._roles[normalized]
            return True

    def get(self, agent_id: object) -> AgentRoleSpec | None:
        with self._lock:
            return self._roles.get(str(agent_id or "").strip())

    def specs(self) -> tuple[AgentRoleSpec, ...]:
        with self._lock:
            # Registration order is the deterministic priority order used by
            # bounded selection.  The revision hash below remains
            # order-independent so replacing a manifest cannot create a
            # hidden priority change merely from dictionary ordering.
            return tuple(self._roles.values())

    def snapshot(self) -> dict[str, Any]:
        specs = self.specs()
        payload = {
            "schema": AGENT_REGISTRY_SCHEMA,
            "roles": [
                _role_payload(spec)
                for spec in sorted(specs, key=lambda item: item.agent_id)
            ],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        revision = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]
        return {
            **payload,
            "registry_revision": f"{AGENT_REGISTRY_REVISION_PREFIX}{revision}",
        }


DEFAULT_AGENT_REGISTRY = AgentRoleRegistry(AGENT_ROLE_SPECS)


def _execution_plan_revision(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        {key: payload[key] for key in sorted(payload) if key != "plan_revision"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]
    return f"{AGENT_EXECUTION_PLAN_REVISION_PREFIX}{digest}"


def build_fleet_execution_plan(fleet: dict[str, Any]) -> dict[str, Any]:
    """Compile explicit phase handoffs for the selected specialist roster.

    This is a coordination contract, not a second executor.  Each task names
    the facts it may produce and the next phase's handoff; actual writes still
    go through ``village_canvas_dispatch_action`` and WorkflowRun.
    """

    selected = [
        item
        for item in (fleet.get("selected_agents") or [])
        if isinstance(item, dict) and str(item.get("agent_id") or "").strip()
    ]
    groups = [
        [str(agent_id).strip() for agent_id in group if str(agent_id).strip()]
        for group in (fleet.get("dispatch_groups") or [])
        if isinstance(group, list) and group
    ]
    by_id = {str(item["agent_id"]): item for item in selected}
    tasks: list[dict[str, Any]] = []
    for index, group in enumerate(groups):
        previous = groups[index - 1] if index else []
        consumers = groups[index + 1] if index + 1 < len(groups) else []
        for agent_id in group:
            item = by_id.get(agent_id)
            if item is None:
                continue
            dependency_ids = [
                f"agent-task:{dep}"
                for dep in list(item.get("depends_on") or [])[:8]
                if str(dep).strip()
            ]
            for dep in previous[:8]:
                dependency = f"agent-task:{dep}"
                if dependency not in dependency_ids:
                    dependency_ids.append(dependency)
            handler = item.get("handler")
            if not isinstance(handler, dict) or not handler.get("handler_id"):
                handler = _handler_manifest(
                    agent_id,
                    capabilities=item.get("required_capabilities") or (),
                )
            tasks.append(
                {
                    "task_id": f"agent-task:{agent_id}",
                    "agent_id": agent_id,
                    "phase": str(item.get("phase") or "domain")[:80],
                    "depends_on": dependency_ids[:16],
                    "handoff_from": [f"agent-task:{dep}" for dep in previous[:8]],
                    "required_capabilities": [
                        str(capability)[:160]
                        for capability in list(item.get("required_capabilities") or [])[:12]
                        if str(capability).strip()
                    ],
                    "side_effect": str(item.get("side_effect") or "none")[:40],
                    "completion_evidence": (
                        "receipt_or_verifier"
                        if str(item.get("side_effect") or "none") == "delegated"
                        else "structured_agent_result"
                    ),
                    "handler": dict(handler),
                    "output_contract": {
                        "schema": AGENT_RESULT_SCHEMA,
                        "handoff_schema": AGENT_HANDOFF_SCHEMA,
                        "artifact_schema": "agent_artifact.v1",
                        "artifact_required": str(item.get("side_effect") or "none")
                        == "delegated",
                        "required_fields": [
                            "producer_agent_id",
                            "source_refs",
                            "status",
                        ],
                        "consumer_agent_ids": [
                            str(consumer).strip()
                            for consumer in consumers[:8]
                            if str(consumer).strip()
                        ],
                    },
                }
            )
    payload: dict[str, Any] = {
        "schema": AGENT_EXECUTION_PLAN_SCHEMA,
        "mode": "intent_driven_handoffs",
        "planner": "director",
        "executor": "production_executor",
        "handoff_policy": "receipt_backed",
        "tasks": tasks[:32],
    }
    payload["plan_revision"] = _execution_plan_revision(payload)
    return payload


def _matches(spec: AgentRoleSpec, intent: str) -> bool:
    return any(re.search(pattern, intent, flags=re.IGNORECASE) for pattern in spec.triggers)


def _clean_ids(value: Iterable[object] | None, *, limit: int = 16) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        value = [value]
    result: list[str] = []
    for item in value:
        clean = str(item or "").strip()[:200]
        if clean and clean not in result:
            result.append(clean)
        if len(result) >= limit:
            break
    return result


def _revision(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]
    return f"{AGENT_FLEET_REVISION_PREFIX}{digest}"


def _dispatch_groups(selected: list[dict[str, Any]]) -> list[list[str]]:
    by_phase: dict[str, list[str]] = {}
    phase_order = ("plan", "observe", "domain", "compile", "execute", "verify", "learn")
    for item in selected:
        by_phase.setdefault(str(item.get("phase") or "domain"), []).append(str(item["agent_id"]))
    return [
        sorted(by_phase[phase])
        for phase in phase_order
        if by_phase.get(phase)
    ]


def select_agent_fleet(
    intent: object,
    *,
    has_existing_target: bool = False,
    active_run_ids: Iterable[object] = (),
    failed_run_ids: Iterable[object] = (),
    max_agents: int = DEFAULT_MAX_AGENTS,
    registry: AgentRoleRegistry | None = None,
    handler_registry: AgentHandlerRegistry | None = None,
) -> dict[str, Any]:
    """Select a bounded, dependency-aware specialist team for one turn.

    Selection is deterministic and side-effect free.  ``active_run_ids`` and
    ``failed_run_ids`` are facts from the shared blackboard, so recovery can
    select the executor without inventing a replacement workflow.
    """

    clean_intent = " ".join(str(intent or "").split())[:2_000]
    role_registry = registry or DEFAULT_AGENT_REGISTRY
    role_handler_registry = handler_registry or DEFAULT_AGENT_HANDLER_REGISTRY
    role_specs = role_registry.specs()
    registry_snapshot = role_registry.snapshot()
    registry_revision = str(registry_snapshot.get("registry_revision") or "")
    active_runs = _clean_ids(active_run_ids)
    failed_runs = _clean_ids(failed_run_ids)
    try:
        requested_limit = int(max_agents)
    except (TypeError, ValueError):
        requested_limit = DEFAULT_MAX_AGENTS
    limit = max(1, min(requested_limit, len(role_specs)))
    intent = classify_agent_intent(clean_intent)
    execution_requested = intent.action_requested
    canvas_context_needed = bool(
        has_existing_target
        or intent.action_requested
        or intent.target_mutation_requested
        or intent.explicit_creation_requested
        or _matches(
            next(
                (item for item in role_specs if item.agent_id == "canvas_observer"),
                AgentRoleSpec("canvas_observer", "", "observe"),
            ),
            clean_intent,
        )
    )
    quality_context_needed = bool(
        intent.action_requested
        or intent.resume_requested
        or _matches(
            next(
                (item for item in role_specs if item.agent_id == "quality_recovery"),
                AgentRoleSpec("quality_recovery", "", "verify"),
            ),
            clean_intent,
        )
    )

    role_by_id = {spec.agent_id: spec for spec in role_specs}
    base_desired_ids: set[str] = set()
    for spec in role_specs:
        should_select = spec.always or _matches(spec, clean_intent)
        if spec.agent_id == "canvas_observer":
            should_select = canvas_context_needed
        elif spec.agent_id == "quality_recovery":
            should_select = quality_context_needed
        if spec.agent_id == "production_executor":
            should_select = should_select or bool(active_runs or failed_runs)
        if should_select:
            base_desired_ids.add(spec.agent_id)
    # Dependencies are part of the plan, not an accidental side effect of the
    # registry's declaration order.  Close the set before applying the team
    # size bound so an asset/shot specialist always brings its observer.
    desired_ids = set(base_desired_ids)
    changed = True
    while changed:
        changed = False
        for agent_id in tuple(desired_ids):
            spec = role_by_id.get(agent_id)
            if spec is None:
                continue
            for dependency in spec.depends_on:
                if dependency in role_by_id and dependency not in desired_ids:
                    desired_ids.add(dependency)
                    changed = True

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    executor_spec = next(
        (item for item in role_specs if item.agent_id == "production_executor"),
        None,
    )
    for spec in role_specs:
        if spec.agent_id not in desired_ids:
            continue
        if len(selected) >= limit:
            break
        selected_ids.add(spec.agent_id)
        selected.append(
            {
                "agent_id": spec.agent_id,
                "label": spec.label,
                "phase": spec.phase,
                "selection_reason": (
                    "always_required"
                    if spec.always
                    else "active_or_failed_run"
                    if spec.agent_id == "production_executor" and (active_runs or failed_runs)
                    else "intent_match"
                ),
                "required_capabilities": list(spec.capabilities),
                "depends_on": list(spec.depends_on),
                "side_effect": spec.side_effect,
                "handler": _handler_manifest(
                    spec.agent_id,
                    capabilities=spec.capabilities,
                    registry=role_handler_registry,
                ),
            }
        )

    # A media or workflow request needs an executor even when wording only
    # matched a model-specific term. Keep the team bounded and deterministic.
    if execution_requested and "production_executor" not in selected_ids and executor_spec:
        executor = executor_spec
        if len(selected) >= limit:
            selected.pop()
            selected_ids = {item["agent_id"] for item in selected}
        selected_ids.add(executor.agent_id)
        selected.append(
            {
                "agent_id": executor.agent_id,
                "label": executor.label,
                "phase": executor.phase,
                "selection_reason": "execution_intent",
                "required_capabilities": list(executor.capabilities),
                "depends_on": list(executor.depends_on),
                "side_effect": executor.side_effect,
                "handler": _handler_manifest(
                    executor.agent_id,
                    capabilities=executor.capabilities,
                    registry=role_handler_registry,
                ),
            }
        )

    selected = [
        item
        for item in selected
        if all(dependency in selected_ids for dependency in item["depends_on"])
        or item["agent_id"] in {"director", "canvas_observer", "quality_recovery"}
    ]
    deferred = [
        {
            "agent_id": spec.agent_id,
            "label": spec.label,
            "reason": "not_triggered_or_team_limit",
        }
        for spec in role_specs
        if spec.agent_id not in {item["agent_id"] for item in selected}
    ]
    material = {
        "intent": clean_intent,
        "selected": selected,
        "active_run_ids": active_runs,
        "failed_run_ids": failed_runs,
        "has_existing_target": bool(has_existing_target),
        "registry_revision": registry_revision,
    }
    result = {
        "schema": AGENT_FLEET_SCHEMA,
        "fleet_revision": _revision(material),
        "registry_revision": registry_revision,
        "mode": "dynamic",
        "intent": clean_intent,
        "execution_requested": execution_requested,
        "has_existing_target": bool(has_existing_target),
        "active_run_ids": active_runs,
        "failed_run_ids": failed_runs,
        "selected_agents": selected,
        "deferred_agents": deferred,
        "dispatch_groups": _dispatch_groups(selected),
        "policy": {
            "planner": "director",
            "executor": "production_executor",
            "side_effects": "executor_only",
            "max_agents": limit,
            "state_source": "shared_agent_context",
            "registry": "runtime_agent_registry",
        },
    }
    result["execution_plan"] = build_fleet_execution_plan(result)
    return result


__all__ = [
    "AGENT_EXECUTION_PLAN_REVISION_PREFIX",
    "AGENT_EXECUTION_PLAN_SCHEMA",
    "AGENT_HANDOFF_SCHEMA",
    "AGENT_HANDLER_SCHEMA",
    "AGENT_RESULT_SCHEMA",
    "AGENT_REGISTRY_REVISION_PREFIX",
    "AGENT_REGISTRY_SCHEMA",
    "AGENT_FLEET_REVISION_PREFIX",
    "AGENT_FLEET_SCHEMA",
    "AGENT_ROLE_SPECS",
    "AgentRoleSpec",
    "AgentRegistryError",
    "AgentRoleConflictError",
    "AgentRoleDependencyError",
    "AgentRoleRegistry",
    "AgentHandlerRegistry",
    "AgentHandlerSpec",
    "AGENT_HANDLER_SPECS",
    "DEFAULT_AGENT_HANDLER_REGISTRY",
    "DEFAULT_AGENT_REGISTRY",
    "DEFAULT_MAX_AGENTS",
    "build_fleet_execution_plan",
    "select_agent_fleet",
]
