"""Canonical execution semantics for durable workflow steps.

The runtime already owns idempotency, receipts and recovery in several
modules. This module gives those guarantees one small, serializable contract
so definitions, persisted runs and retry validation use the same vocabulary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


EXECUTION_SEMANTICS_PROTOCOL_VERSION = "workflow.execution-semantics/v1"

SideEffect = Literal["none", "local_mutation", "external_mutation", "paid_generation"]
RetrySafety = Literal["safe", "idempotency_key_required", "unsafe"]
SemanticExecutionMode = Literal["parallel_safe", "sequential", "exclusive"]
ResultLookup = Literal["none", "idempotency_key", "provider_receipt"]
RecoveryMode = Literal["replay", "reconcile", "manual"]
BackoffClass = Literal["none", "bounded_exponential"]
FailureStage = Literal[
    "trigger",
    "input",
    "script_execution",
    "asset_access",
    "agent_authoring",
    "media_generation",
    "assembly",
    "tool_execution",
    "human_interaction",
    "control",
    "artifact_persistence",
    "delivery_verification",
    "export",
    "subworkflow",
    "plugin_execution",
]


@dataclass(frozen=True, slots=True)
class WorkflowExecutionSemantics:
    """Immutable policy needed to execute and recover one workflow step."""

    side_effect: SideEffect = "none"
    retry_safety: RetrySafety = "safe"
    execution_mode: SemanticExecutionMode = "sequential"
    idempotency: Literal["runtime_node", "input", "none"] = "none"
    result_lookup: ResultLookup = "none"
    recovery_mode: RecoveryMode = "replay"
    max_automatic_attempts: int = 1
    backoff_class: BackoffClass = "none"
    failure_stage: FailureStage = "plugin_execution"

    @property
    def protocol_version(self) -> str:
        return EXECUTION_SEMANTICS_PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["protocol_version"] = self.protocol_version
        return value


def validate_execution_semantics(
    semantics: WorkflowExecutionSemantics,
    *,
    step_id: str = "step",
) -> tuple[str, ...]:
    """Return deterministic contract errors instead of relying on executor luck."""

    errors: list[str] = []
    if semantics.max_automatic_attempts < 1 or semantics.max_automatic_attempts > 8:
        errors.append(f"{step_id} 的 max_automatic_attempts 必须在 1..8")
    if semantics.retry_safety == "idempotency_key_required" and semantics.idempotency == "none":
        errors.append(f"{step_id} 声明需要幂等键但没有幂等身份")
    if semantics.result_lookup == "idempotency_key" and semantics.idempotency == "none":
        errors.append(f"{step_id} 通过幂等键查找结果但没有幂等身份")
    if semantics.side_effect == "paid_generation":
        if semantics.retry_safety != "idempotency_key_required":
            errors.append(f"{step_id} 付费生成必须要求幂等键")
        if semantics.result_lookup != "provider_receipt":
            errors.append(f"{step_id} 付费生成必须通过 provider receipt 对账")
        if semantics.recovery_mode != "reconcile":
            errors.append(f"{step_id} 付费生成恢复方式必须是 reconcile")
    if semantics.max_automatic_attempts > 1:
        if semantics.recovery_mode != "reconcile":
            errors.append(f"{step_id} 只有 reconcile 恢复允许有界自动尝试")
        if semantics.result_lookup == "none":
            errors.append(f"{step_id} 自动尝试必须有持久结果查找身份")
        if semantics.retry_safety != "idempotency_key_required":
            errors.append(f"{step_id} 自动尝试必须要求幂等键")
        if semantics.side_effect == "paid_generation" and semantics.result_lookup != "provider_receipt":
            errors.append(f"{step_id} 付费生成自动尝试必须通过 provider receipt 对账")
    if semantics.retry_safety == "unsafe" and semantics.recovery_mode == "replay":
        errors.append(f"{step_id} unsafe 步骤不能使用 replay 恢复")
    return tuple(errors)


def semantics_from_state(state: dict[str, Any]) -> WorkflowExecutionSemantics:
    """Read a persisted snapshot, with a conservative legacy fallback."""

    raw = state.get("execution_semantics")
    if isinstance(raw, dict):
        fields = {
            key: raw[key]
            for key in (
                "side_effect",
                "retry_safety",
                "execution_mode",
                "idempotency",
                "result_lookup",
                "recovery_mode",
                "max_automatic_attempts",
                "backoff_class",
                "failure_stage",
            )
            if key in raw
        }
        try:
            return WorkflowExecutionSemantics(**fields)
        except (TypeError, ValueError):
            pass
    # Old runs predate the explicit contract. Their existing retry policy is
    # preserved, but canvas mutations are conservatively non-replayable.
    execution_mode = str(state.get("execution_mode") or "atomic")
    return WorkflowExecutionSemantics(
        side_effect="local_mutation" if state.get("writes_canvas") else "none",
        retry_safety="idempotency_key_required" if state.get("writes_canvas") else "safe",
        execution_mode="parallel_safe" if execution_mode == "itemized" else "sequential",
        idempotency="runtime_node" if state.get("writes_canvas") else "none",
        result_lookup="idempotency_key" if state.get("writes_canvas") else "none",
        recovery_mode="reconcile" if state.get("writes_canvas") else "replay",
        failure_stage="plugin_execution",
    )


#: 产物停在 pending_dispatch / monitoring 时，driver 应该自轮询继续推进的
#: 媒体 handler。画布链与 freezone 三步的等待态语义一致：reconcile 由
#: advance 重入，等待本身不写 revision，所以必须由 driver 续命。
SELF_POLLING_MEDIA_HANDLERS = frozenset(
    {
        "canvas.run_generation_nodes",
        "freezone.storyboard_images",
        "freezone.shot_videos",
        "freezone.final_film",
    }
)


def media_poll_delay_seconds(
    elapsed_seconds: float,
    *,
    watchdog_seconds: int,
) -> float | None:
    """Return how long the driver should sleep before the next poll.

    ``None`` means the watchdog fired: the media batch has been in flight
    longer than authorised, so the driver must stop polling and hand the run
    back (it stays visible as monitoring; a read or restart re-drives it).
    Below 60 秒每秒看一眼，之后放缓到每 5 秒，避免长任务把后台打满。
    """

    if watchdog_seconds > 0 and elapsed_seconds >= watchdog_seconds:
        return None
    return 1.0 if elapsed_seconds < 60.0 else 5.0
