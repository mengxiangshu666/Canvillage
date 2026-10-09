"""SQLite-backed workflow runs with revision and event idempotency."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

from novelvideo.workflow_runtime.store_schema import _SCHEMA, _migrate_schema

from novelvideo.sqlite_pragmas import configure_sqlite_connection_async
from novelvideo.workflow_runtime.automatic_recovery import (
    automatic_reconcile_marker_matches,
)
from novelvideo.workflow_runtime.canvas_recovery import recovery_next_action
from novelvideo.workflow_runtime.causal_binding import CausalBinding, binding_from_run
from novelvideo.workflow_runtime.definitions import WorkflowDefinition
from novelvideo.workflow_runtime.execution_semantics import semantics_from_state
from novelvideo.workflow_runtime.item_state import (
    item_id as _item_id,
    item_states_from_payload as _item_states_from_payload,
    merge_artifact as _merge_artifact,
    progress_value as _progress_value,
    summarize_item_states as _summarize_item_states,
)
from novelvideo.workflow_runtime.media_authorization import MEDIA_AUTHORIZATION_STEPS
from novelvideo.workflow_runtime.production_authorization import (
    production_authorization_from_run,
)
from novelvideo.verification.agent_artifacts import coerce_event_agent_artifacts

logger = logging.getLogger(__name__)


class WorkflowRunConflictError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        current_revision: int | None = None,
        code: str = "workflow_revision_conflict",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.current_revision = current_revision
        self.code = code
        self.details = dict(details or {})


_CREATE_LOCKS: dict[str, asyncio.Lock] = {}
_SCHEMA_LOCKS: dict[str, asyncio.Lock] = {}
_SCHEMA_IDENTITIES: dict[str, tuple[int, int, int | None]] = {}


def _database_file_identity(stat: Any) -> tuple[int, int, int | None]:
    # POSIX ctime is metadata-change time, not creation time: SQLite writes
    # change it. Birth time, when available, helps detect reused file IDs.
    birth_ns = getattr(stat, "st_birthtime_ns", None)
    if birth_ns is None and getattr(stat, "st_birthtime", None) is not None:
        birth_ns = int(stat.st_birthtime * 1_000_000_000)
    return stat.st_dev, stat.st_ino, birth_ns


class _WorkflowEventPulse:
    """Wake local event-stream subscribers without replacing SQLite durability."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._version = 0

    @property
    def version(self) -> int:
        return self._version

    async def notify(self) -> None:
        async with self._condition:
            self._version += 1
            self._condition.notify_all()

    async def wait(self, observed_version: int, *, timeout: float) -> int:
        async with self._condition:
            if self._version != observed_version:
                return self._version
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: self._version != observed_version),
                    timeout=max(0.1, timeout),
                )
            except TimeoutError:
                pass
            return self._version


# Run-status vocabulary. Two distinct "terminal" notions coexist on purpose:
#   * _TERMINAL_STATUSES      — the run can make no further progress. ``failed``
#     is excluded because a failed run is retryable (``_runtime_phase`` maps it
#     to ``recoverable_error``, not ``terminal``), so _is_terminal stays False.
#   * _AUDIT_TERMINAL_STATUSES — what an auditor counts as finished, where a
#     failed run does belong. Used by list_terminal_runs' SQL filter.
_EVENT_PULSES: "OrderedDict[str, _WorkflowEventPulse]" = OrderedDict()
_EVENT_PULSES_LIMIT = 512
_EVENT_PULSES_LOCK = threading.Lock()
_TERMINAL_STATUSES = frozenset({"completed", "cancelled"})
_AUDIT_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_LEASE_RELEASING_STATUSES = frozenset({"paused", "completed", "failed", "cancelled"})
DEFAULT_EXECUTION_LEASE_SECONDS = 45
_TRUSTED_V2_EVENT_TYPES = frozenset(
    {
        "verification_passed",
        "verification_failed",
        "checkpoint_saved",
        "step_completed",
        "step_items_dismissed",
        "visual_preflight_decided",
        "run_completed",
        "run_failed",
    }
)
_VERIFIER_LEARNING_EVENT_TYPES = frozenset(
    {"verification_passed", "verification_failed"}
)
# A verifier can legitimately finish before the chat turn has materialized a
# candidate memory. Keep that event replayable so a later candidate can receive
# the authoritative result instead of leaving the causal link permanently open.
_VERIFIER_LEARNING_RETRY_STATUSES = frozenset({"queued", "pending", "failed"})


def workflow_start_idempotency_fingerprint(payload: dict[str, Any]) -> str:
    """Hash the caller-visible start request, excluding derived runtime state."""

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def ensure_workflow_start_idempotency(
    existing: dict[str, Any],
    *,
    fingerprint: str,
    project_id: str,
    canvas_id: str,
    workflow_id: str,
    run_mode: str,
    contract_version: int,
    request: str,
) -> None:
    """Allow exact replays and reject an idempotency key reused for new work."""

    persisted = str(existing.get("idempotency_fingerprint") or "").strip()
    mismatches: list[str] = []
    if persisted:
        if persisted != fingerprint:
            mismatches.append("request_fingerprint")
    else:
        expected = {
            "project_id": project_id,
            "canvas_id": canvas_id,
            "workflow_id": workflow_id,
            "run_mode": run_mode,
            "contract_version": int(contract_version),
        }
        for field, value in expected.items():
            if existing.get(field) != value:
                mismatches.append(field)
        existing_inputs = existing.get("inputs")
        existing_request = (
            str(existing_inputs.get("request") or "").strip()
            if isinstance(existing_inputs, dict)
            else ""
        )
        if request and existing_request != request:
            mismatches.append("request")
    if not mismatches:
        return
    raise WorkflowRunConflictError(
        "工作流幂等键已绑定到另一项任务",
        current_revision=int(existing.get("revision") or 0),
        code="workflow_run_idempotency_conflict",
        details={
            "existing_run_id": str(existing.get("id") or ""),
            "mismatched_fields": mismatches,
        },
    )




def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _lease_deadline(seconds: int) -> str:
    return (
        (
            datetime.now(timezone.utc)
            + timedelta(seconds=max(5, min(int(seconds), 3600)))
        )
        .isoformat()
        .replace("+00:00", "Z")
    )


def _execution_target_node_ids(run: dict[str, Any]) -> list[str]:
    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    context = (
        run.get("project_context")
        if isinstance(run.get("project_context"), dict)
        else {}
    )
    raw = inputs.get("target_node_ids") or context.get("target_node_ids") or []
    result: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        node_id = str(item or "").strip()[:240]
        if node_id and node_id not in result:
            result.append(node_id)
    return result[:500]


def _decode(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _runtime_phase(status: str, value: object) -> str:
    phase = str(value or "").strip()
    if phase:
        return phase
    return {
        "paused": "waiting_user",
        "failed": "recoverable_error",
        "completed": "terminal",
        "cancelled": "terminal",
    }.get(status, "acting")


def _is_terminal(run: dict[str, Any]) -> bool:
    if str(run.get("status") or "") in _TERMINAL_STATUSES:
        return True
    return (
        int(run.get("contract_version") or 1) >= 2
        and str(run.get("runtime_phase") or "") == "terminal"
    )


def _next_action(
    states: dict[str, dict[str, Any]],
    *,
    status: str,
    runtime_phase: str,
    artifacts: dict[str, Any] | None = None,
) -> str:
    if runtime_phase == "terminal":
        return ""
    if status == "paused":
        return "resume"
    if runtime_phase == "verifying":
        running = _frontier(states)
        return f"verify:{running[0]}" if running else "verify"
    failed = next(
        (
            step_id
            for step_id, state in states.items()
            if state.get("status") == "failed"
            and not (
                isinstance(state.get("item_summary"), dict)
                and int(state["item_summary"].get("failed") or 0) == 0
                and int(state["item_summary"].get("dismissed") or 0) > 0
            )
        ),
        "",
    )
    if failed:
        recovery_action = recovery_next_action((artifacts or {}).get(failed))
        if recovery_action:
            return recovery_action
        return f"retry:{failed}"
    frontier = _frontier(states)
    return f"execute:{frontier[0]}" if frontier else ""


def _row(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    value = dict(row)
    for field, fallback in (
        ("current_frontier", []),
        ("step_states", {}),
        ("inputs", {}),
        ("artifacts", {}),
        ("success_criteria", []),
        ("checkpoint", {}),
        ("project_context", {}),
        ("model_plan_snapshot", {}),
    ):
        value[field] = _decode(value.pop(f"{field}_json", None), fallback)
    value["revision"] = int(value.get("revision") or 0)
    value["event_seq"] = int(value.get("event_seq") or 0)
    value["contract_version"] = int(value.get("contract_version") or 1)
    step_states = value.get("step_states")
    if isinstance(step_states, dict):
        for state in step_states.values():
            if not isinstance(state, dict):
                continue
            state.setdefault("requires", [])
            state.setdefault("produces", [])
            state.setdefault("writes_canvas", False)
            state.setdefault("checkpoint", True)
            state.setdefault("max_attempts", 3)
            state.setdefault("retry_policy", "manual")
            state.setdefault("execution_mode", "atomic")
            state.setdefault("failure_policy", "stop_run")
            state.setdefault("retry_scope", "whole_step")
            state.setdefault(
                "execution_semantics",
                semantics_from_state(state).to_dict(),
            )
            state.setdefault(
                "progress", 1.0 if state.get("status") == "completed" else 0.0
            )
            state.setdefault("progress_message", "")
            state.setdefault("item_summary", {})
            state.setdefault("item_retry_seq", 0)
            state.setdefault("retry_scope_used", "whole_step")
    status = str(value.get("status") or "running")
    value["runtime_phase"] = _runtime_phase(status, value.get("runtime_phase"))
    verified_revision = value.get("last_verified_canvas_revision")
    value["last_verified_canvas_revision"] = (
        int(verified_revision)
        if isinstance(verified_revision, int)
        and not isinstance(verified_revision, bool)
        else None
    )
    for field in (
        "goal",
        "next_action",
        "error_code",
        "terminal_reason",
        "source_turn_id",
        "model_plan_revision",
        "lease_owner",
        "lease_token",
        "lease_expires_at",
        "heartbeat_at",
        "completed_at",
        "idempotency_fingerprint",
    ):
        value[field] = str(value.get(field) or "")
    return value


def _event_row(row: aiosqlite.Row) -> dict[str, Any]:
    value = dict(row)
    value["seq"] = int(value.get("seq") or 0)
    value["payload"] = _decode(value.pop("payload_json", None), {})
    value["source"] = str(value.get("source") or "internal")
    return value


def _command_row(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    value = dict(row)
    for field in ("envelope", "expectation", "receipt"):
        value[field] = _decode(value.pop(f"{field}_json", None), {})
    for field in ("expected_canvas_revision", "observed_canvas_revision"):
        raw = value.get(field)
        value[field] = (
            int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else None
        )
    return value


def _paid_start_row(row: aiosqlite.Row) -> dict[str, Any]:
    value = dict(row)
    value["ordinal"] = int(value.get("ordinal") or 0)
    return value




def _initial_step_states(definition: WorkflowDefinition) -> dict[str, dict[str, Any]]:
    now = _now()
    states: dict[str, dict[str, Any]] = {}
    for step in definition.steps:
        completed = step.handler == "workflow.preflight"
        states[step.id] = {
            "id": step.id,
            "label": step.label,
            "type": step.type,
            "handler": step.handler,
            "depends_on": list(step.depends_on),
            "requires": list(step.requires),
            "produces": list(step.produces),
            "writes_canvas": step.writes_canvas,
            "checkpoint": step.checkpoint,
            "max_attempts": step.max_attempts,
            "retry_policy": step.retry_policy,
            "execution_mode": step.execution_mode,
            "failure_policy": step.failure_policy,
            "retry_scope": step.retry_scope,
            "execution_semantics": step.execution_semantics.to_dict(),
            "status": "completed" if completed else "pending",
            "attempt": 1 if completed else 0,
            "progress": 1.0 if completed else 0.0,
            "progress_message": "" if not completed else "运行前校验已完成",
            "item_summary": {},
            "item_retry_seq": 0,
            "retry_scope_used": "whole_step",
            "error": "",
            "started_at": now if completed else "",
            "completed_at": now if completed else "",
        }
    _advance_ready_steps(states, now)
    return states


def _advance_ready_steps(states: dict[str, dict[str, Any]], now: str) -> None:
    for state in states.values():
        if state["status"] != "pending":
            continue
        if all(states[dep]["status"] == "completed" for dep in state["depends_on"]):
            state["status"] = "running"
            state["attempt"] = int(state.get("attempt") or 0) + 1
            state["started_at"] = now


def _frontier(states: dict[str, dict[str, Any]]) -> list[str]:
    return [
        step_id for step_id, state in states.items() if state["status"] == "running"
    ]


def _descendants(states: dict[str, dict[str, Any]], step_id: str) -> set[str]:
    found: set[str] = set()
    pending = [step_id]
    while pending:
        parent = pending.pop()
        for candidate, state in states.items():
            if candidate not in found and parent in state["depends_on"]:
                found.add(candidate)
                pending.append(candidate)
    return found


class WorkflowRunStore:
    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.db_path = self.state_dir / "workflow_runs.db"

    def _event_pulse(self, run_id: str) -> _WorkflowEventPulse:
        key = f"{self.db_path.resolve()}\0{run_id}"
        # Bound the module-level pulse registry: without an upper limit every
        # run ever seen would keep its waiter entry forever. The pulse is just
        # an in-process wakeup token, so evicting the least recently used key is
        # safe — a late waiter simply recreates it (with a version mismatch)
        # instead of deadlocking.
        with _EVENT_PULSES_LOCK:
            pulse = _EVENT_PULSES.get(key)
            if pulse is None:
                pulse = _WorkflowEventPulse()
                _EVENT_PULSES[key] = pulse
            _EVENT_PULSES.move_to_end(key)
            while len(_EVENT_PULSES) > _EVENT_PULSES_LIMIT:
                _EVENT_PULSES.popitem(last=False)
            return pulse

    def event_signal_version(self, run_id: str) -> int:
        return self._event_pulse(run_id).version

    async def wait_for_event_signal(
        self,
        run_id: str,
        observed_version: int,
        *,
        timeout: float = 15.0,
    ) -> int:
        """Wait for an in-process write, with timeout for heartbeat/cross-worker checks."""

        return await self._event_pulse(run_id).wait(
            observed_version,
            timeout=timeout,
        )

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:
            empty_before_connect = self.db_path.stat().st_size == 0
        except FileNotFoundError:
            empty_before_connect = True
        db = await aiosqlite.connect(self.db_path)
        db.row_factory = aiosqlite.Row
        await configure_sqlite_connection_async(db)
        schema_key = str(self.db_path.resolve())
        schema_lock = _SCHEMA_LOCKS.setdefault(schema_key, asyncio.Lock())
        async with schema_lock:
            stat = self.db_path.stat()
            identity = _database_file_identity(stat)
            if (
                empty_before_connect
                or stat.st_size == 0
                or _SCHEMA_IDENTITIES.get(schema_key) != identity
            ):
                await db.executescript(_SCHEMA)
                await _migrate_schema(db)
                await db.commit()
                _SCHEMA_IDENTITIES[schema_key] = _database_file_identity(
                    self.db_path.stat()
                )
        try:
            yield db
        finally:
            await db.close()

    async def _fetch(
        self, db: aiosqlite.Connection, run_id: str
    ) -> dict[str, Any] | None:
        async with db.execute(
            "SELECT * FROM canvas_workflow_runs WHERE id=?", (run_id,)
        ) as cursor:
            return _row(await cursor.fetchone())

    async def _active_target_conflicts(
        self,
        db: aiosqlite.Connection,
        *,
        project_id: str,
        canvas_id: str,
        target_node_ids: list[str],
        exclude_run_id: str = "",
    ) -> list[dict[str, Any]]:
        if not target_node_ids:
            return []
        placeholders = ",".join("?" for _ in target_node_ids)
        now = _now()
        params: list[Any] = [project_id, canvas_id, *target_node_ids, now]
        where = (
            "project_id=? AND canvas_id=? "
            f"AND target_node_id IN ({placeholders}) AND lease_until>?"
        )
        if exclude_run_id:
            where += " AND run_id<>?"
            params.append(exclude_run_id)
        async with db.execute(
            f"""SELECT project_id, canvas_id, target_node_id, run_id, owner,
                       lease_until, heartbeat_at
                  FROM canvas_workflow_target_leases
                 WHERE {where}
                 ORDER BY target_node_id ASC""",
            tuple(params),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    @staticmethod
    def _target_lease_conflict(
        conflicts: list[dict[str, Any]],
    ) -> WorkflowRunConflictError:
        run_ids = sorted({str(item.get("run_id") or "") for item in conflicts})
        target_ids = sorted(
            {str(item.get("target_node_id") or "") for item in conflicts}
        )
        return WorkflowRunConflictError(
            "目标节点正由已有 WorkflowRun 执行，请继续原任务而不是新建平行任务",
            code="workflow_target_lease_conflict",
            details={
                "existing_run_id": run_ids[0] if len(run_ids) == 1 else "",
                "conflicting_run_ids": run_ids,
                "target_node_ids": target_ids,
                "lease_until": max(
                    (str(item.get("lease_until") or "") for item in conflicts),
                    default="",
                ),
            },
        )

    async def _reserve_targets(
        self,
        db: aiosqlite.Connection,
        *,
        run: dict[str, Any],
        owner: str,
        lease_token: str,
        lease_until: str,
        now: str,
    ) -> None:
        target_node_ids = _execution_target_node_ids(run)
        conflicts = await self._active_target_conflicts(
            db,
            project_id=str(run.get("project_id") or ""),
            canvas_id=str(run.get("canvas_id") or ""),
            target_node_ids=target_node_ids,
            exclude_run_id=str(run.get("id") or ""),
        )
        if conflicts:
            raise self._target_lease_conflict(conflicts)
        await db.execute(
            "DELETE FROM canvas_workflow_target_leases WHERE lease_until<=?",
            (now,),
        )
        for target_node_id in target_node_ids:
            await db.execute(
                """INSERT INTO canvas_workflow_target_leases(
                       project_id, canvas_id, target_node_id, run_id, owner,
                       lease_token, lease_until, heartbeat_at, created_at, updated_at
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_id, canvas_id, target_node_id) DO UPDATE SET
                       run_id=excluded.run_id,
                       owner=excluded.owner,
                       lease_token=excluded.lease_token,
                       lease_until=excluded.lease_until,
                       heartbeat_at=excluded.heartbeat_at,
                       updated_at=excluded.updated_at""",
                (
                    str(run.get("project_id") or ""),
                    str(run.get("canvas_id") or ""),
                    target_node_id,
                    str(run.get("id") or ""),
                    owner,
                    lease_token,
                    lease_until,
                    now,
                    now,
                    now,
                ),
            )

    async def create(
        self,
        *,
        definition: WorkflowDefinition,
        project_id: str,
        canvas_id: str,
        run_mode: str,
        inputs: dict[str, Any],
        idempotency_key: str,
        contract_version: int = 2,
        goal: str = "",
        success_criteria: list[str] | None = None,
        source_turn_id: str = "",
        project_context: dict[str, Any] | None = None,
        model_plan_snapshot: dict[str, Any] | None = None,
        initial_step_artifacts: dict[str, dict[str, Any]] | None = None,
        idempotency_fingerprint: str = "",
    ) -> tuple[dict[str, Any], bool]:
        request_fingerprint = str(idempotency_fingerprint or "").strip() or (
            workflow_start_idempotency_fingerprint(
                {
                    "workflow_id": definition.id,
                    "workflow_version": definition.version,
                    "project_id": project_id,
                    "canvas_id": canvas_id,
                    "run_mode": run_mode,
                    "inputs": inputs,
                    "contract_version": int(contract_version),
                    "goal": goal,
                    "success_criteria": success_criteria or [],
                    "source_turn_id": source_turn_id,
                }
            )
        )
        lock = _CREATE_LOCKS.setdefault(str(self.db_path.resolve()), asyncio.Lock())
        async with lock, self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT * FROM canvas_workflow_runs WHERE idempotency_key=?",
                (idempotency_key,),
            ) as cursor:
                existing = _row(await cursor.fetchone())
            if existing is not None:
                ensure_workflow_start_idempotency(
                    existing,
                    fingerprint=request_fingerprint,
                    project_id=project_id,
                    canvas_id=canvas_id,
                    workflow_id=definition.id,
                    run_mode=run_mode,
                    contract_version=contract_version,
                    request=str(inputs.get("request") or "").strip(),
                )
                await db.commit()
                return existing, True
            run_id = f"wfr_{uuid.uuid4().hex}"
            now = _now()
            states = _initial_step_states(definition)
            runtime_phase = "acting"
            persisted_context = dict(project_context or {})
            persisted_context.update(
                {
                    "project_id": project_id,
                    "canvas_id": canvas_id,
                    "workflow_run_id": run_id,
                    "source_turn_id": source_turn_id,
                }
            )
            persisted_model_plan = dict(model_plan_snapshot or {})
            model_plan_revision = str(
                persisted_model_plan.get("model_plan_revision") or ""
            )
            root_origin = "agent" if source_turn_id else "workflow"
            persisted_context["causal_binding"] = CausalBinding(
                project_id=project_id,
                canvas_id=canvas_id,
                origin=root_origin,
                source_turn_id=source_turn_id,
                workflow_run_id=run_id,
                canvas_revision=persisted_context.get("observed_canvas_revision"),
                input_revision=persisted_context.get("canvas_revision"),
                model_plan_revision=model_plan_revision,
            ).to_dict()
            preflight_artifact = {
                "kind": "preflight_receipt",
                "status": "completed",
                **(
                    initial_step_artifacts.get("understand", {})
                    if isinstance(initial_step_artifacts, dict)
                    else {}
                ),
            }
            preflight_artifact["project_context"] = persisted_context
            # The service resolves a starter only for explicit/template runs.
            # Falling back to the definition here would leak the legacy
            # starter identity into dynamic production runs that intentionally
            # skipped the scaffold stage.
            artifacts = {
                "starter_workflow_id": str(inputs.get("starter_workflow_id") or ""),
                "understand": preflight_artifact,
            }
            artifacts["understand"].setdefault(
                "model_plan_revision", model_plan_revision
            )
            artifacts["understand"].setdefault(
                "bound_model_roles",
                sorted(
                    (
                        persisted_model_plan.get("bindings")
                        if isinstance(persisted_model_plan.get("bindings"), dict)
                        else {}
                    ).keys()
                ),
            )
            await db.execute(
                """INSERT INTO canvas_workflow_runs(
                       id, workflow_id, workflow_version, project_id, canvas_id,
                       run_mode, status, current_frontier_json, step_states_json,
                       inputs_json, artifacts_json, contract_version, goal,
                       success_criteria_json, runtime_phase, checkpoint_json,
                       next_action, source_turn_id, project_context_json,
                       model_plan_snapshot_json, model_plan_revision,
                       idempotency_key, idempotency_fingerprint, created_at, updated_at
                   ) VALUES(?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    definition.id,
                    definition.version,
                    project_id,
                    canvas_id,
                    run_mode,
                    json.dumps(_frontier(states), ensure_ascii=False),
                    json.dumps(states, ensure_ascii=False),
                    json.dumps(inputs, ensure_ascii=False),
                    json.dumps(artifacts, ensure_ascii=False),
                    contract_version,
                    goal,
                    json.dumps(success_criteria or [], ensure_ascii=False),
                    runtime_phase,
                    _next_action(
                        states,
                        status="running",
                        runtime_phase=runtime_phase,
                        artifacts=artifacts,
                    ),
                    source_turn_id,
                    json.dumps(persisted_context, ensure_ascii=False),
                    json.dumps(persisted_model_plan, ensure_ascii=False),
                    model_plan_revision,
                    idempotency_key,
                    request_fingerprint,
                    now,
                    now,
                ),
            )
            pending_run = {
                "id": run_id,
                "project_id": project_id,
                "canvas_id": canvas_id,
                "inputs": inputs,
                "project_context": persisted_context,
            }
            if int(contract_version) >= 2 and _execution_target_node_ids(pending_run):
                reservation_owner = f"reservation:{source_turn_id or idempotency_key}"[
                    :240
                ]
                reservation_token = uuid.uuid4().hex
                reservation_until = _lease_deadline(DEFAULT_EXECUTION_LEASE_SECONDS)
                try:
                    await self._reserve_targets(
                        db,
                        run=pending_run,
                        owner=reservation_owner,
                        lease_token=reservation_token,
                        lease_until=reservation_until,
                        now=now,
                    )
                except WorkflowRunConflictError:
                    await db.rollback()
                    raise
                await db.execute(
                    """UPDATE canvas_workflow_runs
                       SET lease_owner=?, lease_token=?, lease_expires_at=?,
                           heartbeat_at=? WHERE id=?""",
                    (
                        reservation_owner,
                        reservation_token,
                        reservation_until,
                        now,
                        run_id,
                    ),
                )
            await db.commit()
            created = await self.get(run_id)
            if created is None:
                raise RuntimeError("workflow run was not persisted")
            return created, False

    async def get(self, run_id: str) -> dict[str, Any] | None:
        async with self._connect() as db:
            return await self._fetch(db, run_id)

    async def reserve_paid_start(
        self,
        run_id: str,
        *,
        step_id: str,
        item_id: str,
        provider_kind: str,
    ) -> dict[str, Any]:
        """Atomically reserve one durable paid provider start for a run item."""

        clean_run_id = str(run_id or "").strip()[:240]
        clean_step_id = str(step_id or "").strip()[:240]
        clean_item_id = str(item_id or "").strip()[:400]
        clean_kind = str(provider_kind or "").strip()[:40]
        if not clean_run_id or not clean_step_id or not clean_item_id:
            raise ValueError("paid start reservation identity is incomplete")
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            run = await self._fetch(db, clean_run_id)
            if run is None:
                await db.rollback()
                raise WorkflowRunConflictError(
                    "付费启动额度绑定的 WorkflowRun 不存在",
                    code="workflow_paid_start_run_missing",
                    details={"run_id": clean_run_id},
                )
            marker = production_authorization_from_run(run)
            if marker is None:
                await db.commit()
                return {
                    "schema": "workflow_paid_start_reservation.v1",
                    "tracked": False,
                    "allowed": True,
                    "run_id": clean_run_id,
                    "step_id": clean_step_id,
                    "item_id": clean_item_id,
                    "provider_kind": clean_kind,
                    "used": 0,
                    "limit": None,
                    "reused": False,
                }
            async with db.execute(
                """SELECT run_id, step_id, item_id, provider_kind, ordinal, created_at
                     FROM canvas_workflow_paid_starts
                    WHERE run_id=? AND step_id=? AND item_id=?""",
                (clean_run_id, clean_step_id, clean_item_id),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing is not None:
                async with db.execute(
                    """SELECT COUNT(*) AS used
                         FROM canvas_workflow_paid_starts
                        WHERE run_id=?""",
                    (clean_run_id,),
                ) as cursor:
                    used = int((await cursor.fetchone())["used"] or 0)
                await db.commit()
                return {
                    **{"schema": "workflow_paid_start_reservation.v1"},
                    "tracked": True,
                    "allowed": True,
                    "run_id": clean_run_id,
                    "used": used,
                    "limit": int(marker["max_paid_starts"]),
                    "reused": True,
                    "reservation": _paid_start_row(existing),
                }
            async with db.execute(
                """SELECT COUNT(*) AS used
                     FROM canvas_workflow_paid_starts
                    WHERE run_id=?""",
                (clean_run_id,),
            ) as cursor:
                used = int((await cursor.fetchone())["used"] or 0)
            limit = int(marker["max_paid_starts"])
            if used >= limit:
                await db.rollback()
                raise WorkflowRunConflictError(
                    "当前 Run 的付费媒体启动额度已耗尽，拒绝进入 provider 派发",
                    code="workflow_paid_start_budget_exceeded",
                    details={
                        "run_id": clean_run_id,
                        "step_id": clean_step_id,
                        "item_id": clean_item_id,
                        "provider_kind": clean_kind,
                        "used": used,
                        "limit": limit,
                    },
                )
            ordinal = used + 1
            created_at = _now()
            await db.execute(
                """INSERT INTO canvas_workflow_paid_starts(
                       run_id, step_id, item_id, provider_kind, ordinal, created_at
                   ) VALUES(?, ?, ?, ?, ?, ?)""",
                (
                    clean_run_id,
                    clean_step_id,
                    clean_item_id,
                    clean_kind,
                    ordinal,
                    created_at,
                ),
            )
            await db.commit()
            return {
                "schema": "workflow_paid_start_reservation.v1",
                "tracked": True,
                "allowed": True,
                "run_id": clean_run_id,
                "used": ordinal,
                "limit": limit,
                "reused": False,
                "reservation": {
                    "run_id": clean_run_id,
                    "step_id": clean_step_id,
                    "item_id": clean_item_id,
                    "provider_kind": clean_kind,
                    "ordinal": ordinal,
                    "created_at": created_at,
                },
            }

    async def list_paid_starts(self, run_id: str) -> list[dict[str, Any]]:
        async with self._connect() as db:
            async with db.execute(
                """SELECT run_id, step_id, item_id, provider_kind, ordinal, created_at
                     FROM canvas_workflow_paid_starts
                    WHERE run_id=? ORDER BY ordinal ASC""",
                (str(run_id or "").strip()[:240],),
            ) as cursor:
                return [_paid_start_row(row) for row in await cursor.fetchall()]

    async def acquire_execution_lease(
        self,
        run_id: str,
        *,
        owner: str,
        lease_seconds: int = DEFAULT_EXECUTION_LEASE_SECONDS,
    ) -> dict[str, Any] | None:
        """Claim one run and every target it mutates under one durable token."""

        clean_owner = str(owner or "").strip()[:240]
        if not clean_owner:
            raise ValueError("workflow execution lease owner is required")
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            current = await self._fetch(db, run_id)
            if current is None or current.get("status") != "running":
                await db.commit()
                return current
            now = _now()
            current_owner = str(current.get("lease_owner") or "")
            current_token = str(current.get("lease_token") or "")
            current_until = str(current.get("lease_expires_at") or "")
            reservation = current_owner.startswith("reservation:")
            if (
                current_token
                and current_until > now
                and current_owner not in {"", clean_owner}
                and not reservation
            ):
                await db.commit()
                raise WorkflowRunConflictError(
                    "WorkflowRun 正由另一个执行器处理",
                    current_revision=int(current.get("revision") or 0),
                    code="workflow_execution_lease_conflict",
                    details={
                        "existing_run_id": run_id,
                        "lease_owner": current_owner,
                        "lease_until": current_until,
                    },
                )
            token = (
                current_token
                if current_owner == clean_owner and current_token
                else uuid.uuid4().hex
            )
            lease_until = _lease_deadline(lease_seconds)
            try:
                await self._reserve_targets(
                    db,
                    run=current,
                    owner=clean_owner,
                    lease_token=token,
                    lease_until=lease_until,
                    now=now,
                )
            except WorkflowRunConflictError:
                await db.rollback()
                raise
            await db.execute(
                """UPDATE canvas_workflow_runs
                   SET lease_owner=?, lease_token=?, lease_expires_at=?, heartbeat_at=?
                   WHERE id=?""",
                (clean_owner, token, lease_until, now, run_id),
            )
            updated = await self._fetch(db, run_id)
            await db.commit()
            return updated

    async def renew_execution_lease(
        self,
        run_id: str,
        *,
        owner: str,
        lease_token: str,
        lease_seconds: int = DEFAULT_EXECUTION_LEASE_SECONDS,
    ) -> bool:
        clean_owner = str(owner or "").strip()[:240]
        clean_token = str(lease_token or "").strip()
        if not clean_owner or not clean_token:
            return False
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            current = await self._fetch(db, run_id)
            if (
                current is None
                or current.get("status") != "running"
                or str(current.get("lease_owner") or "") != clean_owner
                or str(current.get("lease_token") or "") != clean_token
            ):
                await db.commit()
                return False
            now = _now()
            lease_until = _lease_deadline(lease_seconds)
            await db.execute(
                """UPDATE canvas_workflow_runs
                   SET lease_expires_at=?, heartbeat_at=?
                   WHERE id=? AND lease_owner=? AND lease_token=?""",
                (lease_until, now, run_id, clean_owner, clean_token),
            )
            await db.execute(
                """UPDATE canvas_workflow_target_leases
                   SET lease_until=?, heartbeat_at=?, updated_at=?
                   WHERE run_id=? AND owner=? AND lease_token=?""",
                (lease_until, now, now, run_id, clean_owner, clean_token),
            )
            await db.commit()
            return True

    async def execution_lease_is_valid(
        self,
        run_id: str,
        *,
        owner: str,
        lease_token: str,
    ) -> bool:
        clean_owner = str(owner or "").strip()[:240]
        clean_token = str(lease_token or "").strip()
        if not clean_owner or not clean_token:
            return False
        async with self._connect() as db:
            current = await self._fetch(db, run_id)
            now = _now()
            if not (
                current
                and current.get("status") == "running"
                and str(current.get("lease_owner") or "") == clean_owner
                and str(current.get("lease_token") or "") == clean_token
                and str(current.get("lease_expires_at") or "") > now
            ):
                return False

            # The run lease and target leases are one write boundary. Checking
            # only the parent row would let a stale worker write after a target
            # lease was removed or expired by another process.
            target_node_ids = _execution_target_node_ids(current)
            if not target_node_ids:
                return True
            placeholders = ",".join("?" for _ in target_node_ids)
            async with db.execute(
                f"""SELECT target_node_id
                       FROM canvas_workflow_target_leases
                      WHERE run_id=? AND owner=? AND lease_token=?
                        AND lease_until>? AND target_node_id IN ({placeholders})""",
                (
                    run_id,
                    clean_owner,
                    clean_token,
                    now,
                    *target_node_ids,
                ),
            ) as cursor:
                active_targets = {str(row[0] or "") for row in await cursor.fetchall()}
            return active_targets == set(target_node_ids)

    async def release_execution_lease(
        self,
        run_id: str,
        *,
        owner: str,
        lease_token: str,
    ) -> bool:
        clean_owner = str(owner or "").strip()[:240]
        clean_token = str(lease_token or "").strip()
        if not clean_owner or not clean_token:
            return False
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            current = await self._fetch(db, run_id)
            if (
                current is None
                or str(current.get("lease_owner") or "") != clean_owner
                or str(current.get("lease_token") or "") != clean_token
            ):
                await db.commit()
                return False
            await db.execute(
                """DELETE FROM canvas_workflow_target_leases
                   WHERE run_id=? AND owner=? AND lease_token=?""",
                (run_id, clean_owner, clean_token),
            )
            await db.execute(
                """UPDATE canvas_workflow_runs
                   SET lease_owner='', lease_token='', lease_expires_at='', heartbeat_at=''
                   WHERE id=? AND lease_owner=? AND lease_token=?""",
                (run_id, clean_owner, clean_token),
            )
            await db.commit()
            return True

    async def list_target_leases(self, run_id: str) -> list[dict[str, Any]]:
        async with self._connect() as db:
            async with db.execute(
                """SELECT project_id, canvas_id, target_node_id, run_id, owner,
                          lease_until, heartbeat_at
                     FROM canvas_workflow_target_leases
                    WHERE run_id=? ORDER BY target_node_id ASC""",
                (run_id,),
            ) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        async with self._connect() as db:
            async with db.execute(
                "SELECT * FROM canvas_workflow_runs WHERE idempotency_key=?",
                (idempotency_key,),
            ) as cursor:
                return _row(await cursor.fetchone())

    async def list(
        self, *, project_id: str, canvas_id: str, limit: int
    ) -> list[dict[str, Any]]:
        async with self._connect() as db:
            async with db.execute(
                """SELECT * FROM canvas_workflow_runs
                   WHERE project_id=? AND canvas_id=?
                     AND (
                         status IN ('running', 'paused', 'failed')
                         OR id IN (
                             SELECT id FROM canvas_workflow_runs
                             WHERE project_id=? AND canvas_id=?
                             ORDER BY updated_at DESC, id DESC LIMIT ?
                         )
                   )
                   ORDER BY updated_at DESC, id DESC""",
                (project_id, canvas_id, project_id, canvas_id, limit),
            ) as cursor:
                return [
                    value for row in await cursor.fetchall() if (value := _row(row))
                ]

    async def list_terminal_runs(
        self,
        *,
        project_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Read terminal runs without creating or migrating the database."""

        if not self.db_path.is_file():
            return []
        # Audit-facing terminal set (_AUDIT_TERMINAL_STATUSES) intentionally
        # includes ``failed``: for reporting a failed run is finished, even
        # though _is_terminal treats it as retryable. Keep both notions here so
        # the three call sites cannot drift apart again.
        placeholders = ", ".join("?" for _ in _AUDIT_TERMINAL_STATUSES)
        where = f"status IN ({placeholders})"
        params: list[Any] = sorted(_AUDIT_TERMINAL_STATUSES)
        if project_id:
            where += " AND project_id=?"
            params.append(project_id)
        params.append(max(1, min(int(limit), 2000)))
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        async with aiosqlite.connect(uri, uri=True) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"""SELECT * FROM canvas_workflow_runs
                    WHERE {where}
                    ORDER BY updated_at DESC, id DESC LIMIT ?""",
                tuple(params),
            ) as cursor:
                return [
                    value for row in await cursor.fetchall() if (value := _row(row))
                ]

    async def list_resumable_runs(
        self,
        *,
        project_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        where = "status='running' AND contract_version>=2"
        params: list[Any] = []
        if project_id:
            where += " AND project_id=?"
            params.append(project_id)
        params.append(max(1, min(int(limit), 2000)))
        async with self._connect() as db:
            async with db.execute(
                f"""SELECT * FROM canvas_workflow_runs
                    WHERE {where}
                    ORDER BY updated_at ASC, id ASC LIMIT ?""",
                tuple(params),
            ) as cursor:
                return [
                    run
                    for row in await cursor.fetchall()
                    if (run := _row(row)) is not None
                ]

    async def events_since(
        self,
        run_id: str,
        *,
        after_seq: int,
        limit: int,
    ) -> dict[str, Any] | None:
        result = await self.events_since_with_run(
            run_id,
            after_seq=after_seq,
            limit=limit,
        )
        return None if result is None else result["page"]

    async def events_since_with_run(
        self,
        run_id: str,
        *,
        after_seq: int,
        limit: int,
    ) -> dict[str, Any] | None:
        """Read a run and its next events on one connection for SSE consumers."""

        async with self._connect() as db:
            # Keep the run row and its event page on one read snapshot. WAL
            # permits concurrent writers, so without an explicit transaction
            # the two SELECTs could observe different revisions.
            await db.execute("BEGIN")
            run = await self._fetch(db, run_id)
            if run is None:
                await db.rollback()
                return None
            async with db.execute(
                """SELECT run_id, event_id, seq, type, step_id, payload_json,
                          error, source, created_at
                   FROM canvas_workflow_events
                   WHERE run_id=? AND seq>?
                   ORDER BY seq ASC LIMIT ?""",
                (run_id, after_seq, limit),
            ) as cursor:
                items = [_event_row(row) for row in await cursor.fetchall()]
            latest_seq = int(run.get("event_seq") or 0)
            next_seq = int(items[-1]["seq"]) if items else after_seq
            result = {
                "run": run,
                "page": {
                    "items": items,
                    "after_seq": after_seq,
                    "next_seq": next_seq,
                    "latest_seq": latest_seq,
                    "has_more": next_seq < latest_seq,
                },
            }
            await db.commit()
            return result

    async def _set_verifier_learning_status(
        self,
        run_id: str,
        event_id: str,
        *,
        status: str,
        error: str = "",
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """UPDATE canvas_workflow_events
                   SET learning_status=?,
                       learning_attempts=learning_attempts + 1,
                       learning_error=?, learning_updated_at=?
                   WHERE run_id=? AND event_id=?""",
                (
                    status,
                    str(error or "")[:1_000],
                    _now(),
                    run_id,
                    event_id,
                ),
            )
            await db.commit()

    async def _deliver_verifier_learning(
        self,
        run_id: str,
        event_id: str,
    ) -> dict[str, Any]:
        try:
            async with self._connect() as db:
                run = await self._fetch(db, run_id)
                async with db.execute(
                    """SELECT event_id, type, step_id, payload_json, error, source,
                              learning_status
                         FROM canvas_workflow_events
                        WHERE run_id=? AND event_id=?""",
                    (run_id, event_id),
                ) as cursor:
                    event = await cursor.fetchone()
            if run is None or event is None:
                return {"status": "skipped", "reason": "event_missing"}
            event_type = str(event["type"] or "")
            source = str(event["source"] or "")
            learning_status = str(event["learning_status"] or "legacy")
            if (
                source != "verifier"
                or event_type not in _VERIFIER_LEARNING_EVENT_TYPES
                or learning_status not in _VERIFIER_LEARNING_RETRY_STATUSES
            ):
                return {"status": "skipped", "reason": "not_queued"}
            from novelvideo.workflow_runtime.learning_bridge import (
                record_workflow_verification_learning,
            )

            result = await record_workflow_verification_learning(
                run=run,
                event_id=str(event["event_id"] or ""),
                event_type=event_type,
                step_id=str(event["step_id"] or ""),
                payload=_decode(event["payload_json"], {}),
                error=str(event["error"] or ""),
            )
            result_status = str(result.get("status") or "skipped")
            persisted_status = (
                result_status
                if result_status in {"recorded", "pending", "skipped"}
                else "skipped"
            )
            await self._set_verifier_learning_status(
                run_id,
                event_id,
                status=persisted_status,
            )
            return result
        except Exception as exc:  # noqa: BLE001 - evidence never changes run outcome
            try:
                await self._set_verifier_learning_status(
                    run_id,
                    event_id,
                    status="failed",
                    error=type(exc).__name__,
                )
            except Exception:  # noqa: BLE001 - queued source event remains replayable
                logger.warning(
                    "workflow verifier learning status write failed run=%s event=%s",
                    run_id,
                    event_id,
                    exc_info=True,
                )
            logger.warning(
                "workflow verifier learning bridge failed run=%s event=%s",
                run_id,
                event_id,
                exc_info=True,
            )
            return {"status": "failed", "reason": type(exc).__name__}

    async def replay_verifier_learning(
        self,
        *,
        project_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, int]:
        """Replay committed verifier evidence left queued by a crash or transient error."""

        where = (
            "event.source='verifier' "
            "AND event.type IN ('verification_passed', 'verification_failed') "
            "AND event.learning_status IN ('queued', 'pending', 'failed')"
        )
        params: list[Any] = []
        if project_id:
            where += " AND run.project_id=?"
            params.append(project_id)
        params.append(max(1, min(int(limit), 2_000)))
        async with self._connect() as db:
            async with db.execute(
                f"""SELECT event.run_id, event.event_id
                      FROM canvas_workflow_events AS event
                      JOIN canvas_workflow_runs AS run ON run.id=event.run_id
                     WHERE {where}
                     ORDER BY event.created_at ASC, event.seq ASC LIMIT ?""",
                tuple(params),
            ) as cursor:
                queued = [dict(row) for row in await cursor.fetchall()]
        counts = {
            "queued": len(queued),
            "recorded": 0,
            "pending": 0,
            "skipped": 0,
            "failed": 0,
        }
        for item in queued:
            result = await self._deliver_verifier_learning(
                str(item["run_id"]),
                str(item["event_id"]),
            )
            status = str(result.get("status") or "skipped")
            key = status if status in counts else "skipped"
            counts[key] += 1
        return counts

    async def persist_command(
        self,
        run_id: str,
        *,
        step_id: str,
        command_id: str,
        kind: str,
        envelope: dict[str, Any],
        expectation: dict[str, Any] | None = None,
        expected_canvas_revision: int | None = None,
    ) -> tuple[dict[str, Any] | None, bool]:
        now = _now()
        encoded_envelope = json.dumps(envelope, ensure_ascii=False, sort_keys=True)
        encoded_expectation = json.dumps(
            expectation or {}, ensure_ascii=False, sort_keys=True
        )
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            run = await self._fetch(db, run_id)
            if run is None:
                await db.commit()
                return None, False
            if _is_terminal(run):
                await db.commit()
                return None, False
            async with db.execute(
                """SELECT * FROM canvas_workflow_commands
                   WHERE run_id=? AND command_id=?""",
                (run_id, command_id),
            ) as cursor:
                existing = _command_row(await cursor.fetchone())
            if existing is not None:
                if (
                    str(existing.get("step_id") or "") != step_id
                    or str(existing.get("kind") or "") != kind
                    or json.dumps(
                        existing.get("envelope") or {},
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    != encoded_envelope
                    or (
                        expectation is not None
                        and json.dumps(
                            existing.get("expectation") or {},
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        != encoded_expectation
                    )
                    or (
                        expected_canvas_revision is not None
                        and existing.get("expected_canvas_revision")
                        != expected_canvas_revision
                    )
                ):
                    await db.commit()
                    raise WorkflowRunConflictError(
                        "workflow command idempotency conflict",
                        current_revision=run["revision"],
                        code="workflow_command_idempotency_conflict",
                    )
                await db.commit()
                return existing, False
            await db.execute(
                """INSERT INTO canvas_workflow_commands(
                       run_id, command_id, step_id, kind, envelope_json,
                       expectation_json, expected_canvas_revision, created_at, updated_at
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    command_id,
                    step_id,
                    kind,
                    encoded_envelope,
                    encoded_expectation,
                    expected_canvas_revision,
                    now,
                    now,
                ),
            )
            async with db.execute(
                """SELECT * FROM canvas_workflow_commands
                   WHERE run_id=? AND command_id=?""",
                (run_id, command_id),
            ) as cursor:
                created = _command_row(await cursor.fetchone())
            await db.commit()
            return created, True

    async def record_command_result(
        self,
        run_id: str,
        command_id: str,
        *,
        status: str,
        receipt: dict[str, Any] | None = None,
        expectation: dict[str, Any] | None = None,
        observed_canvas_revision: int | None = None,
        error_code: str = "",
        error: str = "",
    ) -> dict[str, Any] | None:
        allowed = {"pending", "receipt_recorded", "verified", "recoverable_error"}
        if status not in allowed:
            raise ValueError(f"unsupported workflow command status: {status}")
        now = _now()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                """SELECT * FROM canvas_workflow_commands
                   WHERE run_id=? AND command_id=?""",
                (run_id, command_id),
            ) as cursor:
                current = _command_row(await cursor.fetchone())
            if current is None:
                await db.commit()
                return None
            current_status = str(current.get("status") or "pending")
            if current_status == "verified" and status != "verified":
                await db.commit()
                return current
            next_receipt = (
                receipt if receipt is not None else current.get("receipt") or {}
            )
            next_expectation = (
                expectation
                if expectation is not None
                else current.get("expectation") or {}
            )
            next_observed_revision = (
                observed_canvas_revision
                if observed_canvas_revision is not None
                else current.get("observed_canvas_revision")
            )
            await db.execute(
                """UPDATE canvas_workflow_commands
                   SET status=?, expectation_json=?, receipt_json=?,
                       observed_canvas_revision=?, error_code=?, error=?, updated_at=?
                   WHERE run_id=? AND command_id=?""",
                (
                    status,
                    json.dumps(next_expectation, ensure_ascii=False, sort_keys=True),
                    json.dumps(next_receipt, ensure_ascii=False, sort_keys=True),
                    next_observed_revision,
                    error_code,
                    error[:4000],
                    now,
                    run_id,
                    command_id,
                ),
            )
            async with db.execute(
                """SELECT * FROM canvas_workflow_commands
                   WHERE run_id=? AND command_id=?""",
                (run_id, command_id),
            ) as cursor:
                updated = _command_row(await cursor.fetchone())
            await db.commit()
            return updated

    async def get_command(self, run_id: str, command_id: str) -> dict[str, Any] | None:
        async with self._connect() as db:
            async with db.execute(
                """SELECT * FROM canvas_workflow_commands
                   WHERE run_id=? AND command_id=?""",
                (run_id, command_id),
            ) as cursor:
                return _command_row(await cursor.fetchone())

    async def get_event(self, run_id: str, event_id: str) -> dict[str, Any] | None:
        async with self._connect() as db:
            async with db.execute(
                """SELECT run_id, event_id, seq, type, step_id, payload_json,
                          error, source, created_at
                   FROM canvas_workflow_events
                   WHERE run_id=? AND event_id=?""",
                (run_id, event_id),
            ) as cursor:
                row = await cursor.fetchone()
                return _event_row(row) if row is not None else None

    async def list_pending_commands(
        self,
        run_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        async with self._connect() as db:
            async with db.execute(
                """SELECT * FROM canvas_workflow_commands
                   WHERE run_id=? AND status IN (
                       'pending', 'receipt_recorded', 'recoverable_error'
                   )
                   ORDER BY created_at ASC, command_id ASC LIMIT ?""",
                (run_id, limit),
            ) as cursor:
                return [
                    command
                    for row in await cursor.fetchall()
                    if (command := _command_row(row)) is not None
                ]

    async def record_event(
        self,
        run_id: str,
        *,
        event_id: str,
        event_type: str,
        step_id: str = "",
        success: bool | None = None,
        payload: dict[str, Any] | None = None,
        error: str = "",
        expected_revision: int | None = None,
        source: str = "internal",
    ) -> tuple[dict[str, Any] | None, bool]:
        event_payload = dict(payload or {})
        # Keep the legacy event fields intact while adding one bounded,
        # structured handoff for specialist consumers.  The contract strips
        # provider URLs and is only materialized when the event carries an
        # artifact identity or an explicit ``agent_artifact`` object.
        artifact_status = (
            "verified"
            if event_type == "verification_passed"
            else "failed"
            if event_type == "verification_failed"
            else "materialized"
        )
        agent_artifacts = coerce_event_agent_artifacts(
            event_payload,
            default_status=artifact_status,
        )
        if agent_artifacts:
            event_payload.setdefault("agent_artifacts", agent_artifacts)
            event_payload.setdefault("agent_artifact", agent_artifacts[0])
        now = _now()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            current = await self._fetch(db, run_id)
            if current is None:
                await db.commit()
                return None, False
            async with db.execute(
                """SELECT event_id, source, type, learning_status
                     FROM canvas_workflow_events
                   WHERE run_id=? AND event_id=?""",
                (run_id, event_id),
            ) as cursor:
                existing_event = await cursor.fetchone()
                if existing_event is not None:
                    await db.commit()
                    if str(existing_event["learning_status"] or "") in (
                        _VERIFIER_LEARNING_RETRY_STATUSES
                    ):
                        await self._deliver_verifier_learning(run_id, event_id)
                    return current, False
            if (
                expected_revision is not None
                and current["revision"] != expected_revision
            ):
                await db.commit()
                raise WorkflowRunConflictError(
                    "workflow run revision conflict",
                    current_revision=current["revision"],
                )
            if _is_terminal(current):
                await db.commit()
                return current, False

            contract_version = int(current.get("contract_version") or 1)
            if (
                contract_version >= 2
                and source == "external"
                and event_type in _TRUSTED_V2_EVENT_TYPES
            ):
                await db.commit()
                raise WorkflowRunConflictError(
                    "workflow event source is not trusted",
                    current_revision=current["revision"],
                    code="workflow_event_source_forbidden",
                )

            if contract_version >= 2 and event_type == "canvas_applied":
                event_type = "receipt_recorded"

            states = dict(current["step_states"])
            artifacts = dict(current["artifacts"])
            status = str(current["status"])
            run_error = str(current.get("error") or "")
            runtime_phase = _runtime_phase(status, current.get("runtime_phase"))
            checkpoint = dict(current.get("checkpoint") or {})
            last_verified_canvas_revision = current.get("last_verified_canvas_revision")
            error_code = str(current.get("error_code") or "")
            terminal_reason = str(current.get("terminal_reason") or "")
            completed_at = str(current.get("completed_at") or "")
            reactivation_lease: tuple[str, str, str] | None = None
            target_step_id = step_id or (
                "canvas_structure"
                if event_type in {"canvas_applied", "receipt_recorded"}
                else ""
            )
            event_payload["causal_binding"] = binding_from_run(
                current,
                step_id=target_step_id,
                item_id=str(
                    event_payload.get("item_id")
                    or event_payload.get("failed_node_id")
                    or ""
                ),
                command_id=str(event_payload.get("command_id") or ""),
                task_id=str(
                    event_payload.get("task_id")
                    or event_payload.get("task_key")
                    or event_payload.get("job_id")
                    or ""
                ),
            ).to_dict()
            target = states.get(target_step_id) if target_step_id else None
            previous_artifact = (
                artifacts.get(target_step_id) if target_step_id else None
            )
            if event_type in {
                "step_output_ready",
                "step_progress",
                "step_completed",
                "step_failed",
            } and isinstance(previous_artifact, dict):
                previous_artifact = dict(previous_artifact)
                previous_artifact.pop("automatic_recovery", None)
                if target_step_id in MEDIA_AUTHORIZATION_STEPS:
                    previous_artifact.pop("media_authorization", None)
            if event_type in {
                "step_output_ready",
                "step_progress",
                "step_completed",
                "step_failed",
            }:
                event_payload.pop("automatic_recovery", None)
            incoming_progress: float | None = None
            if "progress" in event_payload:
                try:
                    incoming_progress = _progress_value(event_payload["progress"])
                except ValueError as exc:
                    await db.commit()
                    raise WorkflowRunConflictError(
                        str(exc),
                        current_revision=current["revision"],
                        code="workflow_step_progress_invalid",
                    ) from exc
            item_states: dict[str, dict[str, Any]] | None = None
            item_summary: dict[str, int] | None = None
            preserve_automatic_reconcile = False

            if event_type in {
                "step_started",
                "step_output_ready",
                "step_progress",
                "step_items_updated",
                "step_completed",
                "step_failed",
                "step_recovery_updated",
                "step_retried",
                "step_items_dismissed",
                "canvas_applied",
                "receipt_recorded",
                "verification_passed",
                "verification_failed",
            }:
                if target is None:
                    await db.commit()
                    raise WorkflowRunConflictError(
                        f"workflow step not found: {target_step_id}",
                        current_revision=current["revision"],
                    )
                automatic_reconcile = event_payload.get("automatic_recovery")
                if event_type == "step_retried" and isinstance(
                    automatic_reconcile, dict
                ):
                    if not automatic_reconcile_marker_matches(
                        current,
                        step_id=target_step_id,
                        step=target,
                        artifact=(
                            previous_artifact
                            if isinstance(previous_artifact, dict)
                            else None
                        ),
                        marker=automatic_reconcile,
                        source=source,
                    ):
                        await db.commit()
                        raise WorkflowRunConflictError(
                            f"workflow automatic recovery is invalid: {target_step_id}",
                            current_revision=current["revision"],
                            code="workflow_automatic_recovery_invalid",
                        )
                    preserve_automatic_reconcile = True
                dependencies_ready = all(
                    states[dependency]["status"] == "completed"
                    for dependency in target["depends_on"]
                )
                if event_type == "step_started" and not dependencies_ready:
                    await db.commit()
                    raise WorkflowRunConflictError(
                        f"workflow step dependencies are incomplete: {target_step_id}",
                        current_revision=current["revision"],
                    )
                retry_scope = str(event_payload.get("retry_scope") or "whole_step")
                if (
                    event_type == "step_retried"
                    and retry_scope != "failed_items_only"
                    and target["status"] != "failed"
                ):
                    await db.commit()
                    raise WorkflowRunConflictError(
                        f"workflow step is not failed: {target_step_id}",
                        current_revision=current["revision"],
                    )
                if (
                    event_type == "step_retried"
                    and retry_scope == "failed_items_only"
                    and str(target.get("execution_mode") or "atomic") != "itemized"
                ):
                    await db.commit()
                    raise WorkflowRunConflictError(
                        f"workflow step does not support item retry: {target_step_id}",
                        current_revision=current["revision"],
                        code="workflow_item_retry_unsupported",
                    )
                if event_type == "step_retried" and retry_scope == "failed_items_only":
                    # 条目级重试必须真的落在未解决的失败条目上：否则对已经收口
                    # 的步骤（含 failure_policy=continue_run 下部分失败仍标
                    # completed 的情形）也能发 failed_items_only，重新派发付费
                    # 任务而没有任何失败条目可修。产物里既没有 failed/cancelled
                    # 条目时，一律拒绝。
                    prior_item_states = (
                        previous_artifact.get("item_states")
                        if isinstance(previous_artifact, dict)
                        else None
                    )
                    unresolved_item_count = (
                        sum(
                            1
                            for item_state in prior_item_states.values()
                            if isinstance(item_state, dict)
                            and str(item_state.get("status") or "")
                            in {"failed", "cancelled"}
                        )
                        if isinstance(prior_item_states, dict)
                        else 0
                    )
                    if unresolved_item_count == 0:
                        await db.commit()
                        raise WorkflowRunConflictError(
                            f"workflow failed items are unavailable: {target_step_id}",
                            current_revision=current["revision"],
                            code="workflow_item_retry_unavailable",
                        )
                if (
                    event_type == "step_retried"
                    and str(target.get("retry_policy") or "manual") == "never"
                ):
                    await db.commit()
                    raise WorkflowRunConflictError(
                        f"workflow step retry is forbidden: {target_step_id}",
                        current_revision=current["revision"],
                        code="workflow_step_retry_forbidden",
                    )
                if event_type == "step_retried":
                    semantics = semantics_from_state(target)
                    if semantics.retry_safety == "unsafe":
                        await db.commit()
                        raise WorkflowRunConflictError(
                            f"workflow step retry is unsafe: {target_step_id}",
                            current_revision=current["revision"],
                            code="workflow_step_retry_unsafe",
                        )
                    if semantics.side_effect == "paid_generation" and (
                        semantics.recovery_mode != "reconcile"
                        or semantics.result_lookup != "provider_receipt"
                    ):
                        await db.commit()
                        raise WorkflowRunConflictError(
                            f"paid workflow step requires provider reconciliation: {target_step_id}",
                            current_revision=current["revision"],
                            code="workflow_paid_step_reconcile_required",
                        )
                if event_type == "step_retried" and int(
                    target.get("attempt") or 0
                ) >= int(target.get("max_attempts") or 1):
                    await db.commit()
                    raise WorkflowRunConflictError(
                        f"workflow step attempts exhausted: {target_step_id}",
                        current_revision=current["revision"],
                        code="workflow_step_attempts_exhausted",
                    )
                if (
                    event_type == "step_items_dismissed"
                    and str(target.get("execution_mode") or "atomic") != "itemized"
                ):
                    await db.commit()
                    raise WorkflowRunConflictError(
                        f"workflow step does not support item dismissal: {target_step_id}",
                        current_revision=current["revision"],
                        code="workflow_item_dismissal_unsupported",
                    )
                if (
                    event_type
                    in {
                        "step_output_ready",
                        "step_progress",
                        "step_items_updated",
                        "step_completed",
                        "step_failed",
                        "canvas_applied",
                        "receipt_recorded",
                        "verification_passed",
                        "verification_failed",
                    }
                    and target["status"] != "running"
                ):
                    await db.commit()
                    raise WorkflowRunConflictError(
                        f"workflow step is not running: {target_step_id}",
                        current_revision=current["revision"],
                    )

            if event_type in {
                "step_output_ready",
                "step_progress",
                "step_items_updated",
                "step_completed",
                "step_failed",
            }:
                item_result = _item_states_from_payload(
                    previous_artifact if isinstance(previous_artifact, dict) else None,
                    event_payload,
                    step_attempt=max(1, int(target.get("attempt") or 1))
                    if target
                    else 1,
                    now=now,
                )
                if item_result is not None:
                    item_states, item_summary = item_result
                    event_payload["item_states"] = item_states
                    event_payload["item_summary"] = item_summary
                    target["item_summary"] = item_summary

            if event_type == "step_items_dismissed" and target is not None:
                prior_item_states = (
                    previous_artifact.get("item_states")
                    if isinstance(previous_artifact, dict)
                    else None
                )
                if not isinstance(prior_item_states, dict):
                    await db.commit()
                    raise WorkflowRunConflictError(
                        f"workflow item dismissal state is missing: {target_step_id}",
                        current_revision=current["revision"],
                        code="workflow_item_dismissal_unavailable",
                    )
                requested_item_ids = list(
                    dict.fromkeys(
                        _item_id(item_id)
                        for item_id in event_payload.get("item_ids", [])
                        if _item_id(item_id)
                    )
                )
                invalid_items = [
                    item_id
                    for item_id in requested_item_ids
                    if not isinstance(prior_item_states.get(item_id), dict)
                    or str(prior_item_states[item_id].get("status") or "") != "failed"
                ]
                if not requested_item_ids or invalid_items:
                    await db.commit()
                    raise WorkflowRunConflictError(
                        f"workflow failed items are unavailable: {target_step_id}",
                        current_revision=current["revision"],
                        code="workflow_item_dismissal_unavailable",
                    )
                dismissed_states = {
                    str(item_id): dict(state)
                    for item_id, state in prior_item_states.items()
                    if str(item_id).strip() and isinstance(state, dict)
                }
                dismissed_reason = str(
                    event_payload.get("dismissed_reason")
                    or "user_removed_failure_record"
                )
                for item_id in requested_item_ids:
                    dismissed_states[item_id].update(
                        status="dismissed",
                        dismissed_at=now,
                        dismissed_reason=dismissed_reason,
                        updated_at=now,
                    )
                item_summary = _summarize_item_states(dismissed_states)
                target["item_summary"] = item_summary
                dismissed_item_ids = list(
                    dict.fromkeys(
                        [
                            *(
                                previous_artifact.get("dismissed_item_ids", [])
                                if isinstance(previous_artifact, dict)
                                and isinstance(
                                    previous_artifact.get("dismissed_item_ids"), list
                                )
                                else []
                            ),
                            *requested_item_ids,
                        ]
                    )
                )
                artifacts[target_step_id] = _merge_artifact(
                    previous_artifact if isinstance(previous_artifact, dict) else None,
                    {
                        "item_states": dismissed_states,
                        "item_summary": item_summary,
                        "dismissed_item_ids": dismissed_item_ids,
                        "last_item_dismissal_at": now,
                    },
                )
                event_payload.update(
                    item_ids=requested_item_ids,
                    item_states=dismissed_states,
                    item_summary=item_summary,
                    dismissed_at=now,
                    dismissed_reason=dismissed_reason,
                )
                # 失败条目被全部 dismiss 后，步骤不再有未解决的失败：必须在这里
                # 收口为 completed，否则步骤会永远停在 failed，而 _next_action 又
                # 会把「failed==0 且 dismissed>0」的步骤从候选里剔除，导致
                # next_action 为空、整个 run 卡在 failed 走不到 completed。
                remaining_active = int(item_summary.get("pending") or 0) + int(
                    item_summary.get("running") or 0
                )
                if (
                    int(item_summary.get("failed") or 0) == 0
                    and remaining_active == 0
                    and str(target.get("status") or "") in {"failed", "running"}
                ):
                    target["status"] = "completed"
                    target["completed_at"] = now
                    target["error"] = ""
                    target["progress"] = 1.0
                    artifacts[target_step_id].update(
                        status="completed",
                        progress=1.0,
                        partial_failure=True,
                    )
                    _advance_ready_steps(states, now)
                    status = "running"
                    run_error = ""
                    runtime_phase = "acting"
                    error_code = ""
            elif event_type in {"step_started", "step_retried"} and target is not None:
                retry_scope = str(event_payload.get("retry_scope") or "whole_step")
                partial_retry = (
                    event_type == "step_retried" and retry_scope == "failed_items_only"
                )
                if partial_retry:
                    prior_item_states = (
                        previous_artifact.get("item_states")
                        if isinstance(previous_artifact, dict)
                        else None
                    )
                    if not isinstance(prior_item_states, dict):
                        await db.commit()
                        raise WorkflowRunConflictError(
                            f"workflow item retry state is missing: {target_step_id}",
                            current_revision=current["revision"],
                            code="workflow_item_retry_unavailable",
                        )
                    requested_item_ids = [
                        _item_id(item_id)
                        for item_id in event_payload.get("item_ids", [])
                        if _item_id(item_id)
                    ]
                    if not requested_item_ids:
                        requested_item_ids = [
                            str(item_id)
                            for item_id, state in prior_item_states.items()
                            if isinstance(state, dict)
                            and str(state.get("status") or "")
                            in {"failed", "cancelled"}
                        ]
                    requested_item_ids = list(dict.fromkeys(requested_item_ids))
                    invalid_items = [
                        item_id
                        for item_id in requested_item_ids
                        if not isinstance(prior_item_states.get(item_id), dict)
                        or str(prior_item_states[item_id].get("status") or "")
                        not in {"failed", "cancelled"}
                    ]
                    exhausted_items = [
                        item_id
                        for item_id in requested_item_ids
                        if isinstance(prior_item_states.get(item_id), dict)
                        and int(prior_item_states[item_id].get("attempt") or 1)
                        >= int(target.get("max_attempts") or 1)
                    ]
                    if not requested_item_ids or invalid_items:
                        await db.commit()
                        raise WorkflowRunConflictError(
                            f"workflow failed items are unavailable: {target_step_id}",
                            current_revision=current["revision"],
                            code="workflow_item_retry_unavailable",
                        )
                    if exhausted_items:
                        await db.commit()
                        raise WorkflowRunConflictError(
                            f"workflow item attempts exhausted: {', '.join(exhausted_items)}",
                            current_revision=current["revision"],
                            code="workflow_item_attempts_exhausted",
                        )
                    retried_states = {
                        str(item_id): dict(state)
                        for item_id, state in prior_item_states.items()
                        if str(item_id).strip() and isinstance(state, dict)
                    }
                    for item_id in requested_item_ids:
                        item_state = retried_states[item_id]
                        item_state.update(
                            status="pending",
                            attempt=int(item_state.get("attempt") or 1) + 1,
                            progress=0.0,
                            error="",
                            updated_at=now,
                        )
                    item_summary = _summarize_item_states(retried_states)
                    retry_seq = int(target.get("item_retry_seq") or 0) + 1
                    target.update(
                        status="running",
                        completed_at="",
                        error="",
                        item_summary=item_summary,
                        item_retry_seq=retry_seq,
                        retry_scope_used="failed_items_only",
                        retry_item_ids=requested_item_ids,
                    )
                    retry_artifact = _merge_artifact(
                        previous_artifact
                        if isinstance(previous_artifact, dict)
                        else None,
                        {
                            "status": "retrying",
                            "retry_scope": "failed_items_only",
                            "retry_item_ids": requested_item_ids,
                            "item_retry_seq": retry_seq,
                            "item_states": retried_states,
                            "item_summary": item_summary,
                        },
                    )
                    retry_artifact.pop("command_envelope", None)
                    for stale_key in (
                        "recovery",
                        "failed_items",
                        "failed_item_ids",
                        "partial_failure",
                        "error_code",
                        "error",
                    ):
                        retry_artifact.pop(stale_key, None)
                    media_authorization = event_payload.get("media_authorization")
                    retry_artifact.pop("media_authorization", None)
                    if target_step_id in MEDIA_AUTHORIZATION_STEPS and isinstance(
                        media_authorization, dict
                    ):
                        retry_artifact["media_authorization"] = dict(
                            media_authorization
                        )
                    artifacts[target_step_id] = retry_artifact
                    event_payload.update(
                        retry_scope="failed_items_only",
                        item_ids=requested_item_ids,
                        item_retry_seq=retry_seq,
                        item_states=retried_states,
                        item_summary=item_summary,
                    )
                else:
                    was_running = target["status"] == "running"
                    target["status"] = "running"
                    if event_type == "step_retried" or not was_running:
                        target["attempt"] = int(target.get("attempt") or 0) + 1
                        target["started_at"] = now
                    target.update(
                        completed_at="",
                        error="",
                        progress=0.0,
                        progress_message="",
                        item_summary={},
                        retry_scope_used="whole_step",
                        retry_item_ids=[],
                    )
                    if event_type == "step_retried":
                        if preserve_automatic_reconcile and isinstance(
                            previous_artifact, dict
                        ):
                            preserved_artifact = dict(previous_artifact)
                            preserved_artifact.update(
                                status="monitoring",
                                automatic_recovery=dict(
                                    event_payload["automatic_recovery"]
                                ),
                            )
                            for stale_key in (
                                "recovery",
                                "failed_items",
                                "failed_item_ids",
                                "partial_failure",
                                "error_code",
                                "error",
                            ):
                                preserved_artifact.pop(stale_key, None)
                            artifacts[target_step_id] = preserved_artifact
                        else:
                            artifacts.pop(target_step_id, None)
                            media_authorization = event_payload.get(
                                "media_authorization"
                            )
                            if (
                                target_step_id in MEDIA_AUTHORIZATION_STEPS
                                and isinstance(media_authorization, dict)
                            ):
                                artifacts[target_step_id] = {
                                    "status": "retrying",
                                    "media_authorization": dict(media_authorization),
                                }
                            compose_authorization = event_payload.get(
                                "compose_authorization"
                            )
                            if target_step_id == "final_film" and isinstance(
                                compose_authorization, dict
                            ):
                                artifacts[target_step_id] = {
                                    "status": "retrying",
                                    "compose_authorization": dict(
                                        compose_authorization
                                    ),
                                }
                            if target_step_id == "shot_videos" and isinstance(
                                previous_artifact, dict
                            ):
                                retained = {
                                    key: previous_artifact[key]
                                    for key in ("visual_preflight", "jobs")
                                    if key in previous_artifact
                                }
                                if retained:
                                    artifacts[target_step_id] = {
                                        **artifacts.get(target_step_id, {}),
                                        **retained,
                                        "status": "preflight",
                                    }
                if event_type == "step_retried":
                    for child_id in _descendants(states, target_step_id):
                        artifacts.pop(child_id, None)
                        child = states[child_id]
                        child.update(
                            status="pending",
                            error="",
                            started_at="",
                            completed_at="",
                            progress=0.0,
                            progress_message="",
                            item_summary={},
                            retry_item_ids=[],
                        )
                status = "running"
                run_error = ""
                runtime_phase = "acting"
                error_code = ""
            elif event_type == "step_output_ready" and target is not None:
                artifacts[target_step_id] = _merge_artifact(
                    previous_artifact if isinstance(previous_artifact, dict) else None,
                    event_payload,
                )
                if incoming_progress is not None:
                    target["progress"] = max(
                        float(target.get("progress") or 0.0),
                        incoming_progress,
                    )
                if event_payload.get("message"):
                    target["progress_message"] = str(event_payload["message"])
                status = "running"
                run_error = ""
                runtime_phase = "waiting_executor"
            elif (
                event_type in {"step_progress", "step_items_updated"}
                and target is not None
            ):
                artifacts[target_step_id] = _merge_artifact(
                    previous_artifact if isinstance(previous_artifact, dict) else None,
                    event_payload,
                )
                if item_states and item_summary and item_summary["total"] > 0:
                    item_progress = (
                        sum(
                            float(item_state.get("progress") or 0.0)
                            for item_state in item_states.values()
                        )
                        / item_summary["total"]
                    )
                    target["progress"] = max(
                        float(target.get("progress") or 0.0),
                        item_progress,
                    )
                    event_payload["progress"] = target["progress"]
                    artifacts[target_step_id]["progress"] = target["progress"]
                if incoming_progress is not None:
                    target["progress"] = max(
                        float(target.get("progress") or 0.0),
                        incoming_progress,
                    )
                    event_payload["progress"] = target["progress"]
                    artifacts[target_step_id]["progress"] = target["progress"]
                if event_payload.get("message"):
                    target["progress_message"] = str(event_payload["message"])
                all_items_terminal = bool(
                    item_summary
                    and item_summary["total"] > 0
                    and not item_summary["running"]
                    and not item_summary["pending"]
                )
                if (
                    event_type == "step_items_updated"
                    and source != "external"
                    and all_items_terminal
                    and item_summary is not None
                ):
                    can_continue = item_summary["failed"] == 0 or (
                        str(target.get("failure_policy") or "stop_run")
                        == "continue_run"
                        and item_summary["completed"] > 0
                    )
                    if can_continue:
                        target["status"] = "completed"
                        target["completed_at"] = now
                        target["error"] = ""
                        target["progress"] = 1.0
                        artifacts[target_step_id].update(
                            status="completed",
                            progress=1.0,
                            partial_failure=item_summary["failed"] > 0,
                        )
                        _advance_ready_steps(states, now)
                        status = "running"
                        run_error = ""
                        runtime_phase = "acting"
                        error_code = ""
                    else:
                        target["status"] = "failed"
                        target["completed_at"] = now
                        target["error"] = error or "all workflow items failed"
                        artifacts[target_step_id]["status"] = "failed"
                        status = "failed"
                        run_error = target["error"]
                        runtime_phase = "recoverable_error"
                        error_code = str(
                            event_payload.get("error_code") or "workflow_items_failed"
                        )
                else:
                    status = "running"
                    run_error = ""
                    runtime_phase = "acting"
            elif event_type == "receipt_recorded" and target is not None:
                previous_artifact = artifacts.get(target_step_id)
                if success is False:
                    target["status"] = "failed"
                    target["completed_at"] = now
                    target["error"] = error or "canvas_receipt_failed"
                    artifacts[target_step_id] = {
                        **(
                            previous_artifact
                            if isinstance(previous_artifact, dict)
                            else {}
                        ),
                        "status": "receipt_failed",
                        "canvas_receipt": event_payload,
                    }
                    status = "failed"
                    run_error = target["error"]
                    runtime_phase = "recoverable_error"
                    error_code = str(
                        event_payload.get("error_code") or "canvas_receipt_failed"
                    )
                else:
                    artifacts[target_step_id] = {
                        **(
                            previous_artifact
                            if isinstance(previous_artifact, dict)
                            else {}
                        ),
                        "status": "verifying",
                        "canvas_receipt": event_payload,
                    }
                    status = "running"
                    run_error = ""
                    runtime_phase = "verifying"
                    error_code = ""
            elif event_type == "verification_passed" and target is not None:
                target["status"] = "completed"
                target["completed_at"] = now
                target["error"] = ""
                target["progress"] = 1.0
                artifacts[target_step_id] = {
                    **(
                        previous_artifact if isinstance(previous_artifact, dict) else {}
                    ),
                    "status": "completed",
                    "progress": 1.0,
                    "verification": event_payload,
                }
                verified_revision = event_payload.get("canvas_revision")
                if isinstance(verified_revision, int) and not isinstance(
                    verified_revision, bool
                ):
                    last_verified_canvas_revision = verified_revision
                checkpoint = {
                    "step_id": target_step_id,
                    "event_id": event_id,
                    "canvas_revision": last_verified_canvas_revision,
                    "verified_at": now,
                }
                _advance_ready_steps(states, now)
                status = "running"
                run_error = ""
                runtime_phase = "acting"
                error_code = ""
            elif event_type == "verification_failed" and target is not None:
                target["status"] = "failed"
                target["completed_at"] = now
                target["error"] = error or "verification_failed"
                previous_artifact = artifacts.get(target_step_id)
                artifacts[target_step_id] = {
                    **(
                        previous_artifact if isinstance(previous_artifact, dict) else {}
                    ),
                    "status": "verification_failed",
                    "verification": event_payload,
                }
                status = "failed"
                run_error = target["error"]
                runtime_phase = "recoverable_error"
                error_code = str(
                    event_payload.get("error_code") or "verification_failed"
                )
            elif (
                event_type in {"step_completed", "canvas_applied"}
                and target is not None
            ):
                event_success = success is not False
                target["status"] = "completed" if event_success else "failed"
                target["completed_at"] = now
                target["error"] = "" if event_success else (error or "step_failed")
                target["progress"] = (
                    1.0
                    if event_success
                    else max(
                        float(target.get("progress") or 0.0),
                        incoming_progress or 0.0,
                    )
                )
                if (
                    event_type == "canvas_applied"
                    and isinstance(previous_artifact, dict)
                    and previous_artifact.get("kind") == "canvas_command"
                ):
                    artifacts[target_step_id] = {
                        **previous_artifact,
                        "status": "completed" if event_success else "failed",
                        "progress": target["progress"],
                        "canvas_receipt": event_payload,
                    }
                else:
                    artifacts[target_step_id] = _merge_artifact(
                        previous_artifact
                        if isinstance(previous_artifact, dict)
                        else None,
                        {
                            **event_payload,
                            "status": "completed" if event_success else "failed",
                            "progress": target["progress"],
                        },
                    )
                if event_success:
                    _advance_ready_steps(states, now)
                    status = "running"
                    run_error = ""
                else:
                    status = "failed"
                    run_error = target["error"]
                    runtime_phase = "recoverable_error"
                    error_code = "step_failed"
            elif event_type == "step_failed" and target is not None:
                partial_item_failure = (
                    str(target.get("execution_mode") or "atomic") == "itemized"
                    and item_states is not None
                    and item_summary is not None
                    and item_summary["failed"] > 0
                )
                if partial_item_failure:
                    event_payload["partial_failure"] = True
                    event_payload["failed_item_ids"] = [
                        item_id
                        for item_id, item_state in item_states.items()
                        if item_state.get("status") == "failed"
                    ]
                    if item_summary["running"] or item_summary["pending"]:
                        target["status"] = "running"
                        target["completed_at"] = ""
                        target["error"] = ""
                        artifacts[target_step_id] = _merge_artifact(
                            previous_artifact
                            if isinstance(previous_artifact, dict)
                            else None,
                            {**event_payload, "status": "monitoring"},
                        )
                        status = "running"
                        run_error = ""
                        runtime_phase = "acting"
                        error_code = ""
                    elif item_summary["completed"] > 0:
                        target["status"] = "failed"
                        target["completed_at"] = now
                        target["error"] = error or "部分工作流条目失败"
                        artifacts[target_step_id] = _merge_artifact(
                            previous_artifact
                            if isinstance(previous_artifact, dict)
                            else None,
                            {
                                **event_payload,
                                "status": "failed",
                                "partial_failure": True,
                            },
                        )
                        status = "failed"
                        run_error = target["error"]
                        runtime_phase = "recoverable_error"
                        error_code = str(
                            event_payload.get("error_code") or "workflow_items_failed"
                        )
                    else:
                        target["status"] = "failed"
                        target["completed_at"] = now
                        target["error"] = error or "step_failed"
                        artifacts[target_step_id] = _merge_artifact(
                            previous_artifact
                            if isinstance(previous_artifact, dict)
                            else None,
                            {**event_payload, "status": "failed"},
                        )
                        status = "failed"
                        run_error = target["error"]
                        runtime_phase = "recoverable_error"
                        error_code = str(
                            event_payload.get("error_code") or "step_failed"
                        )
                else:
                    target["status"] = "failed"
                    target["completed_at"] = now
                    target["error"] = error or "step_failed"
                    artifacts[target_step_id] = _merge_artifact(
                        previous_artifact
                        if isinstance(previous_artifact, dict)
                        else None,
                        {**event_payload, "status": "failed"},
                    )
                    status = "failed"
                    run_error = target["error"]
                    runtime_phase = "recoverable_error"
                    error_code = str(event_payload.get("error_code") or "step_failed")
            elif event_type == "visual_preflight_decided" and target is not None:
                preflight = (previous_artifact or {}).get("visual_preflight", {})
                reports = [dict(item) for item in preflight.get("reports", [])]
                report = next(
                    (
                        item
                        for item in reports
                        if item.get("shot_id") == event_payload.get("shot_id")
                        and item.get("input_fingerprint")
                        == event_payload.get("input_fingerprint")
                    ),
                    None,
                )
                decision = event_payload.get("decision")
                if (
                    target_step_id != "shot_videos"
                    or target.get("status") != "failed"
                    or report is None
                    or decision not in {"keep_original", "accept_suggestion"}
                    or (
                        decision == "accept_suggestion"
                        and not report.get("revised_motion_prompt")
                    )
                ):
                    await db.rollback()
                    raise WorkflowRunConflictError(
                        "预检决定已过期或没有可接受建议",
                        code="workflow_visual_preflight_decision_invalid",
                    )
                report["decision"] = decision
                preflight = {**preflight, "reports": reports}
                artifacts[target_step_id] = {
                    **previous_artifact,
                    "status": "preflight",
                    "visual_preflight": preflight,
                }
                for key in ("recovery", "error", "error_code"):
                    artifacts[target_step_id].pop(key, None)
                target.update(status="running", error="", completed_at="")
                status, run_error, error_code = "running", "", ""
                runtime_phase = "acting"
            elif event_type == "step_recovery_updated" and target is not None:
                if status != "failed" or str(target.get("status") or "") != "failed":
                    await db.commit()
                    raise WorkflowRunConflictError(
                        "only a failed workflow step can update recovery",
                        current_revision=current["revision"],
                        code="workflow_step_recovery_update_not_failed",
                    )
                recovery = event_payload.get("recovery")
                if not isinstance(recovery, dict):
                    await db.commit()
                    raise WorkflowRunConflictError(
                        "workflow step recovery update is missing recovery",
                        current_revision=current["revision"],
                        code="workflow_step_recovery_update_invalid",
                    )
                if (
                    str(recovery.get("action") or "") != "request_media_authorization"
                    or str(recovery.get("step_id") or "") != target_step_id
                    or str(recovery.get("workflow_run_id") or "") != run_id
                ):
                    await db.commit()
                    raise WorkflowRunConflictError(
                        "workflow step recovery update is out of scope",
                        current_revision=current["revision"],
                        code="workflow_step_recovery_update_scope_mismatch",
                    )
                artifacts[target_step_id] = _merge_artifact(
                    previous_artifact if isinstance(previous_artifact, dict) else None,
                    {
                        "status": "failed",
                        "recovery": recovery,
                        "readiness": event_payload.get("readiness"),
                    },
                )
                run_error = str(recovery.get("instruction") or run_error)
                runtime_phase = "recoverable_error"
                error_code = str(recovery.get("error_code") or error_code)
            elif event_type == "run_paused":
                if status != "running":
                    await db.commit()
                    raise WorkflowRunConflictError(
                        "only a running workflow can be paused",
                        current_revision=current["revision"],
                    )
                status = "paused"
                runtime_phase = "waiting_user"
            elif event_type == "run_resumed":
                if status != "paused":
                    await db.commit()
                    raise WorkflowRunConflictError(
                        "only a paused workflow can be resumed",
                        current_revision=current["revision"],
                    )
                status = "running"
                run_error = ""
                runtime_phase = "acting"
            elif event_type == "run_cancelled":
                status = "cancelled"
                run_error = error or "cancelled"
                runtime_phase = "terminal"
                terminal_reason = "cancelled"
                completed_at = now
            elif event_type == "steering_added":
                steering = list(artifacts.get("steering") or [])
                steering.append({"at": now, **event_payload})
                artifacts["steering"] = steering[-50:]

            if (
                status == "running"
                and str(current.get("status") or "") != "running"
                and contract_version >= 2
                and _execution_target_node_ids(current)
            ):
                reservation_owner = f"reservation:{event_type}:{event_id}"[:240]
                reservation_token = uuid.uuid4().hex
                reservation_until = _lease_deadline(DEFAULT_EXECUTION_LEASE_SECONDS)
                try:
                    await self._reserve_targets(
                        db,
                        run=current,
                        owner=reservation_owner,
                        lease_token=reservation_token,
                        lease_until=reservation_until,
                        now=now,
                    )
                except WorkflowRunConflictError:
                    await db.rollback()
                    raise
                reactivation_lease = (
                    reservation_owner,
                    reservation_token,
                    reservation_until,
                )

            if states and all(
                state["status"] == "completed" for state in states.values()
            ):
                status = "completed"
                run_error = ""
                runtime_phase = "terminal"
                terminal_reason = "completed"
                completed_at = now
            frontier = _frontier(states)
            next_action = _next_action(
                states,
                status=status,
                runtime_phase=runtime_phase,
                artifacts=artifacts,
            )
            next_seq = current["event_seq"] + 1
            cursor = await db.execute(
                """UPDATE canvas_workflow_runs
                   SET status=?, current_frontier_json=?, step_states_json=?,
                       artifacts_json=?, error=?, runtime_phase=?, checkpoint_json=?,
                       last_verified_canvas_revision=?, next_action=?, error_code=?,
                       terminal_reason=?, completed_at=?, revision=revision+1,
                       event_seq=?, updated_at=?
                   WHERE id=? AND revision=?""",
                (
                    status,
                    json.dumps(frontier, ensure_ascii=False),
                    json.dumps(states, ensure_ascii=False),
                    json.dumps(artifacts, ensure_ascii=False),
                    run_error,
                    runtime_phase,
                    json.dumps(checkpoint, ensure_ascii=False),
                    last_verified_canvas_revision,
                    next_action,
                    error_code,
                    terminal_reason,
                    completed_at,
                    next_seq,
                    now,
                    run_id,
                    current["revision"],
                ),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                raise WorkflowRunConflictError(
                    "workflow run changed during event write",
                    current_revision=current["revision"],
                )
            if reactivation_lease is not None and status == "running":
                await db.execute(
                    """UPDATE canvas_workflow_runs
                       SET lease_owner=?, lease_token=?, lease_expires_at=?,
                           heartbeat_at=? WHERE id=?""",
                    (*reactivation_lease, now, run_id),
                )
            if status in _LEASE_RELEASING_STATUSES:
                await db.execute(
                    "DELETE FROM canvas_workflow_target_leases WHERE run_id=?",
                    (run_id,),
                )
                await db.execute(
                    """UPDATE canvas_workflow_runs
                       SET lease_owner='', lease_token='', lease_expires_at='',
                           heartbeat_at='' WHERE id=?""",
                    (run_id,),
                )
            await db.execute(
                """INSERT INTO canvas_workflow_events(
                       event_id, run_id, seq, type, step_id, payload_json, error,
                       source, learning_status, learning_updated_at, created_at
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    run_id,
                    next_seq,
                    event_type,
                    target_step_id,
                    json.dumps(event_payload, ensure_ascii=False),
                    error,
                    source,
                    (
                        "queued"
                        if source == "verifier"
                        and event_type in _VERIFIER_LEARNING_EVENT_TYPES
                        else "not_applicable"
                    ),
                    now,
                    now,
                ),
            )
            updated = await self._fetch(db, run_id)
            await db.commit()
            await self._event_pulse(run_id).notify()
            if (
                updated is not None
                and source == "verifier"
                and event_type in _VERIFIER_LEARNING_EVENT_TYPES
            ):
                await self._deliver_verifier_learning(run_id, event_id)
            return updated, True
