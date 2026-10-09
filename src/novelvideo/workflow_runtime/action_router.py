"""Deterministic routing between direct canvas actions and durable workflows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import math
from typing import Any, Literal


def _created_node_ids(commands: object) -> set[str]:
    """Return concrete node ids minted by the current command batch.

    The gateway normalizes ``$created:N`` aliases before routing the persisted
    envelope, so the helper accepts both aliases (pre-normalization) and the
    concrete ids written onto create commands (post-normalization).
    """

    created: set[str] = set()
    if not isinstance(commands, (list, tuple)):
        return created
    for command in commands[:100]:
        if not isinstance(command, Mapping):
            continue
        for key in ("created_node_id", "created_node_ids"):
            value = command.get(key)
            if isinstance(value, (list, tuple)):
                created.update(
                    str(item).strip()
                    for item in value
                    if str(item or "").strip()
                )
            elif str(value or "").strip():
                created.add(str(value).strip())
    return created


def connection_dependency_refs(commands: object) -> set[str]:
    """Find existing endpoints used only as dependencies of new nodes.

    A create-and-connect batch has two different identities: the new node is
    the creation target, while a concrete endpoint on the other side of a
    connection is an existing reference dependency. Routing must not reject
    that dependency as a replacement target. This function is shared by the
    API resolver and the persisted Gateway route so both layers make the same
    decision before any write occurs.
    """

    dependencies: set[str] = set()
    if not isinstance(commands, (list, tuple)):
        return dependencies
    created_ids = _created_node_ids(commands)
    non_connection_refs: set[str] = set()
    for command in commands[:100]:
        if not isinstance(command, Mapping):
            continue
        if str(command.get("type") or "").strip() == "connect_nodes":
            continue
        for key in ("node_id", "source", "target"):
            value = str(command.get(key) or "").strip()
            if value and not value.startswith("$") and value not in created_ids:
                non_connection_refs.add(value)
    for command in commands[:100]:
        if not isinstance(command, Mapping):
            continue
        command_type = str(command.get("type") or "").strip()
        if command_type == "connect_nodes":
            endpoints = [
                str(command.get(key) or "").strip()
                for key in ("source", "target")
            ]
            has_created_endpoint = any(
                endpoint.startswith("$created:") or endpoint in created_ids
                for endpoint in endpoints
            )
            if has_created_endpoint:
                dependencies.update(
                    endpoint
                    for endpoint in endpoints
                    if endpoint
                    and not endpoint.startswith("$created:")
                    and endpoint not in created_ids
                    and endpoint not in non_connection_refs
                )
    return dependencies


ActionLane = Literal["blocked", "canvas", "workflow"]
InteractionMode = Literal["discuss", "plan", "execute"]
TargetStrategy = Literal["reuse_existing", "create_missing"]

_ATOMIC_CANVAS_OPERATIONS = frozenset(
    {
        "annotate",
        "annotate_canvas",
        "canvas_command",
        "canvas_structure",
        "connect_nodes",
        "create_canvas_node",
        "delete_node",
        "move_nodes",
        "remove_edge",
        "update_node_data",
        "update_node_label",
        "update_node_prompt",
    }
)

_EXISTING_NODE_MUTATION_TYPES = frozenset(
    {
        "update_node_prompt",
        "update_node_label",
        "update_node_data",
        "update_node_camera",
        "move_node",
        "delete_node",
        "connect_nodes",
        "remove_edge",
    }
)

_CANVAS_CREATION_TYPES = frozenset(
    {
        "annotate",
        "create_canvas_node",
        "create_image_prompt_node",
        "create_shot_sequence",
        "create_video_prompt_node",
        "insert_starter_workflow",
    }
)


@dataclass(frozen=True, slots=True)
class ActionProfile:
    operation: str = ""
    requested_lane: ActionLane | None = None
    interaction_mode: InteractionMode = "execute"
    # Direct construction is an internal trusted call. Mappings from Agent or
    # plugin payloads override this in ``profile_from_mapping`` so incomplete
    # director decisions fail closed before lane selection.
    director_contract_complete: bool = True
    target_strategy: TargetStrategy | None = None
    target_node_ids: tuple[str, ...] = ()
    existing_run_id: str = ""
    creation_reason: str = ""
    step_count: int = 1
    item_count: int = 1
    dependency_count: int = 0
    estimated_duration_seconds: float = 0.0
    requires_recovery: bool = False
    requires_delivery: bool = False
    contains_paid_media: bool = False
    execution_context: dict[str, Any] | None = None


def _has_executable_canvas_commands(commands: object) -> bool:
    """Return true when the caller already compiled the exact canvas mutation."""

    return isinstance(commands, (list, tuple)) and bool(commands)


def existing_node_mutation_batch(commands: object) -> bool:
    """Return true only for a batch that cannot create or address new nodes.

    This is deliberately structural rather than semantic: an existing-node
    update must carry concrete node references, and batch aliases such as
    ``$created:0`` are excluded because they imply creation in the same batch.
    """

    if not isinstance(commands, (list, tuple)) or not commands:
        return False

    def existing_ref(value: object) -> bool:
        reference = str(value or "").strip()
        return bool(reference) and not reference.startswith("$created:")

    for command in commands:
        if not isinstance(command, Mapping):
            return False
        command_type = str(command.get("type") or "").strip()
        if command_type not in _EXISTING_NODE_MUTATION_TYPES:
            return False
        if command_type in {
            "update_node_prompt",
            "update_node_label",
            "update_node_data",
            "update_node_camera",
            "move_node",
            "delete_node",
        }:
            if not existing_ref(command.get("node_id")):
                return False
        elif not (
            existing_ref(command.get("source"))
            and existing_ref(command.get("target"))
        ):
            return False
    return True


def canvas_creation_batch(commands: object) -> bool:
    """Return true when a command batch creates nodes or a starter graph."""

    return isinstance(commands, (list, tuple)) and any(
        isinstance(command, Mapping)
        and str(command.get("type") or "").strip() in _CANVAS_CREATION_TYPES
        for command in commands
    )


def single_atomic_canvas_creation_batch(commands: object) -> bool:
    """Return true for one fully compiled, non-workflow node creation.

    A single node command is already an executable canvas transaction.  In
    particular, it must not be promoted to a starter workflow merely because
    an upstream director profile copied a delivery flag into the envelope.
    Starter graphs and multi-prompt shot sequences remain durable candidates.
    """

    if not isinstance(commands, (list, tuple)) or len(commands) != 1:
        return False
    command = commands[0]
    if not isinstance(command, Mapping):
        return False
    command_type = str(command.get("type") or "").strip()
    if command_type == "insert_starter_workflow":
        return False
    if command_type == "create_shot_sequence":
        prompts = command.get("prompts")
        return isinstance(prompts, (list, tuple)) and len(
            [item for item in prompts if str(item or "").strip()]
        ) == 1
    return command_type in {
        "annotate",
        "create_canvas_node",
        "create_image_prompt_node",
        "create_video_prompt_node",
    }


@dataclass(frozen=True, slots=True)
class ActionRoute:
    lane: ActionLane
    reason_code: str
    reason: str
    requires_durable_run: bool
    requires_confirmation: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _bounded_float(
    value: object, *, default: float, minimum: float, maximum: float
) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(minimum, min(maximum, parsed))


def _bounded_node_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        dict.fromkeys(
            str(item).strip()[:200]
            for item in value[:500]
            if str(item or "").strip()
        )
    )


def profile_from_mapping(
    value: Mapping[str, Any] | None,
    *,
    default_operation: str = "",
    requested_lane: ActionLane | None = None,
) -> ActionProfile:
    """Build one bounded profile from an untrusted API or tool payload."""

    raw = value if isinstance(value, Mapping) else {}
    director_contract_complete = all(
        key in raw for key in ("interaction_mode", "target_strategy", "target_node_ids")
    )
    interaction_mode = str(raw.get("interaction_mode") or "execute").strip()
    if interaction_mode not in {"discuss", "plan", "execute"}:
        interaction_mode = "execute"
    raw_target_strategy = str(raw.get("target_strategy") or "").strip()
    target_strategy = (
        raw_target_strategy
        if raw_target_strategy in {"reuse_existing", "create_missing"}
        else None
    )
    return ActionProfile(
        operation=str(raw.get("operation") or default_operation).strip()[:200],
        requested_lane=requested_lane,
        interaction_mode=interaction_mode,
        director_contract_complete=director_contract_complete,
        target_strategy=target_strategy,
        target_node_ids=_bounded_node_ids(raw.get("target_node_ids")),
        existing_run_id=str(raw.get("existing_run_id") or "").strip()[:200],
        creation_reason=str(raw.get("creation_reason") or "").strip()[:1000],
        step_count=_bounded_int(
            raw.get("step_count"), default=1, minimum=1, maximum=1000
        ),
        item_count=_bounded_int(
            raw.get("item_count"), default=1, minimum=1, maximum=50_000
        ),
        dependency_count=_bounded_int(
            raw.get("dependency_count"), default=0, minimum=0, maximum=200_000
        ),
        estimated_duration_seconds=_bounded_float(
            raw.get("estimated_duration_seconds"),
            default=0.0,
            minimum=0.0,
            maximum=604_800.0,
        ),
        requires_recovery=raw.get("requires_recovery") is True,
        requires_delivery=raw.get("requires_delivery") is True,
        contains_paid_media=raw.get("contains_paid_media") is True,
        execution_context=(
            dict(raw.get("execution_context"))
            if isinstance(raw.get("execution_context"), Mapping)
            else None
        ),
    )


def route_action(
    profile: ActionProfile,
    *,
    has_executable_commands: bool = False,
    existing_node_mutation_only: bool = False,
    contains_creation: bool = False,
    single_atomic_creation: bool = False,
    existing_node_ids: object = (),
) -> ActionRoute:
    """Choose the shortest execution lane from structural task facts."""

    if profile.interaction_mode != "execute":
        return ActionRoute(
            lane="blocked",
            reason_code="execution_not_authorized",
            reason="当前交互只允许讨论或规划，不允许写画布或启动工作流",
            requires_durable_run=False,
            requires_confirmation=False,
        )
    if not profile.director_contract_complete:
        if existing_node_mutation_only and not profile.requires_recovery:
            return ActionRoute(
                lane="canvas",
                reason_code="existing_node_mutation_inferred",
                reason="缺少导演合同，但命令只修改明确存在的节点或连线，安全推导为复用已有成果",
                requires_durable_run=False,
                requires_confirmation=False,
            )
        return ActionRoute(
            lane="blocked",
            reason_code="director_contract_required",
            reason="创建、恢复或复杂执行前必须提供完整导演合同",
            requires_durable_run=False,
            requires_confirmation=False,
        )
    # target_strategy / creation_reason 是调用者的声明性元数据，命令批次才是写入
    # 事实（T-208 同一原则：判据是命令形状，不是声明或措辞）。声明与命令不一致、
    # 或声明 create_missing 却绑定已有节点，都由命令批次决定实际写入，路由层不再
    # 拒绝（T-212：三套参考语料均无此类表单闸；今日真机 8 次误拦全部来自这里）。

    if profile.existing_run_id:
        return ActionRoute(
            lane="workflow",
            reason_code="continue_existing_run",
            reason="任务绑定了已有 WorkflowRun，应从原断点继续",
            requires_durable_run=True,
            requires_confirmation=profile.contains_paid_media,
        )

    requested = profile.requested_lane
    if requested == "workflow":
        return ActionRoute(
            lane="workflow",
            reason_code="explicit_workflow",
            reason="调用方明确请求持久工作流",
            requires_durable_run=True,
            requires_confirmation=profile.contains_paid_media,
        )
    if requested == "canvas":
        return ActionRoute(
            lane="canvas",
            reason_code="explicit_canvas",
            reason="调用方明确请求直接画布操作",
            requires_durable_run=False,
            requires_confirmation=profile.contains_paid_media,
        )
    if existing_node_mutation_only and not profile.requires_recovery:
        return ActionRoute(
            lane="canvas",
            reason_code="existing_node_mutation",
            reason="命令批次只修改已有节点或已有连线，不创建替代工作流",
            requires_durable_run=False,
            requires_confirmation=False,
        )

    # A fully compiled one-node create is an atomic canvas mutation even when
    # a model copied ``requires_delivery`` from a broader director contract.
    # The command shape, not topic words, decides this fast path.  Explicit
    # workflow requests and recovery remain authoritative above.
    if (
        single_atomic_creation
        and profile.target_strategy == "create_missing"
        and profile.step_count == 1
        and profile.item_count == 1
        and profile.dependency_count == 0
        and not profile.requires_recovery
        and profile.requires_delivery
    ):
        return ActionRoute(
            lane="canvas",
            reason_code="single_canvas_creation",
            reason="单个已编译节点创建可在一次画布事务内完成，不启动替代工作流",
            requires_durable_run=False,
            requires_confirmation=profile.contains_paid_media,
        )

    # A formal delivery contract owns the durable lane even when the model also
    # emits a small UI/presentation command.  A focus/select/fit command is not
    # evidence that a production plan was executed; it must not downgrade a
    # director task to a transient canvas transaction.
    atomic_operation = profile.operation.strip().lower() in _ATOMIC_CANVAS_OPERATIONS
    durable_reasons = (
        (profile.requires_recovery, "requires_recovery", "任务需要暂停、恢复或断点续跑"),
        (profile.requires_delivery, "requires_delivery", "任务包含正式验收或交付阶段"),
        (
            profile.contains_paid_media and profile.item_count > 1,
            "media_batch",
            "任务包含多个需要持久跟踪的媒体条目",
        ),
        (profile.step_count > 1, "multi_step", "任务包含多个有序执行步骤"),
        (
            profile.dependency_count > 0 and not atomic_operation,
            "has_dependencies",
            "任务包含节点或步骤依赖",
        ),
        (
            profile.estimated_duration_seconds > 300,
            "long_running",
            "任务预计持续时间超过直接操作窗口",
        ),
    )
    for matched, code, reason in durable_reasons:
        if matched:
            return ActionRoute(
                lane="workflow",
                reason_code=code,
                reason=reason,
                requires_durable_run=True,
                requires_confirmation=profile.contains_paid_media,
            )

    # A complete non-delivery command envelope is already the deterministic
    # plan. Do not replace it with a starter workflow that invents nodes.
    if (
        has_executable_commands
        and not profile.contains_paid_media
        and not profile.requires_recovery
    ):
        return ActionRoute(
            lane="canvas",
            reason_code="compiled_canvas_commands",
            reason="调用方已提供完整画布命令批次，直接原子写入",
            requires_durable_run=False,
            requires_confirmation=False,
        )

    if (
        profile.operation.strip().lower() in _ATOMIC_CANVAS_OPERATIONS
        and not profile.requires_recovery
        and not profile.requires_delivery
        and not profile.contains_paid_media
    ):
        return ActionRoute(
            lane="canvas",
            reason_code="single_canvas_action",
            reason="结构化画布命令可在一次原子事务内完成",
            requires_durable_run=False,
            requires_confirmation=False,
        )

    return ActionRoute(
        lane="canvas",
        reason_code="single_canvas_action",
        reason="任务可由一次直接画布命令完成",
        requires_durable_run=False,
        requires_confirmation=profile.contains_paid_media,
    )


def route_canvas_envelope(
    envelope: dict[str, Any],
    *,
    existing_node_ids: object = (),
) -> ActionRoute:
    """Describe the lane already selected by one canvas command envelope."""

    action_profile = envelope.get("action_profile")
    if isinstance(action_profile, dict):
        commands = envelope.get("commands")
        observed_node_ids = {
            str(item).strip()
            for item in (
                existing_node_ids
                if isinstance(existing_node_ids, (list, tuple, set, frozenset))
                else ()
            )
            if str(item or "").strip()
        }
        # Existing references connected to a node created in this same batch
        # are dependencies, not replacement targets. The API route performs
        # the same subtraction against raw ``$created`` commands; doing it
        # again here is required because the Gateway routes the normalized
        # envelope where aliases have already become concrete ids.
        observed_node_ids.difference_update(connection_dependency_refs(commands))
        return route_action(
            profile_from_mapping(
                action_profile,
                default_operation="canvas_command",
            ),
            existing_node_mutation_only=existing_node_mutation_batch(commands),
            contains_creation=canvas_creation_batch(commands),
            single_atomic_creation=single_atomic_canvas_creation_batch(commands),
            existing_node_ids=observed_node_ids,
        )
    commands = envelope.get("commands")
    command_count = len(commands) if isinstance(commands, list) else 0
    workflow_run_id = str(
        envelope.get("run_id") or envelope.get("workflow_run_id") or ""
    ).strip()
    return route_action(
        ActionProfile(
            operation="canvas_command",
            requested_lane="workflow" if workflow_run_id else "canvas",
            item_count=max(1, command_count),
        )
    )


__all__ = [
    "ActionLane",
    "InteractionMode",
    "TargetStrategy",
    "ActionProfile",
    "ActionRoute",
    "profile_from_mapping",
    "route_action",
    "route_canvas_envelope",
    "connection_dependency_refs",
    "existing_node_mutation_batch",
    "canvas_creation_batch",
    "single_atomic_canvas_creation_batch",
]
