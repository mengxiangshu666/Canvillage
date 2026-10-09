"""Small, project-scoped checkpoints for recovering long Agent turns."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping
import uuid

from novelvideo.utils.error_redaction import redact_secrets
from novelvideo.utils.state_index_files import write_json_atomic
from novelvideo.verification.agent_artifacts import project_agent_artifact_ref
from novelvideo.chat.workflow_stage_receipts import project_workflow_stage_receipts
from novelvideo.workflow_runtime.canvas_recovery import recovery_from_artifact
from novelvideo.workflow_runtime.step_recovery import build_workflow_step_recovery


CHECKPOINT_SCHEMA = "village_agent_checkpoint.v1"
MAX_PROMPT_CHARS = 8_000
_MAX_TEXT_CHARS = 1_200
_MAX_LIST_ITEMS = 32
_MAX_LIST_ITEM_CHARS = 320
_BARE_SECRET_RE = re.compile(
    r"(?i)\b(?:sk|tvly|key|token)-[A-Za-z0-9][A-Za-z0-9_-]{8,}\b"
)


def _compact_canvas_recovery(value: object) -> dict[str, Any]:
    recovery = value if isinstance(value, Mapping) else {}
    if not recovery:
        return {}
    compact: dict[str, Any] = {}
    for key in (
        "schema",
        "workflow_run_id",
        "action",
        "title",
        "instruction",
        "next_action",
        "error_code",
        "step_id",
        "rerun_scope",
        "reason_code",
        "stale_reason",
        "script_node_id",
        "target_node_id",
        "media_action",
        "shot_id",
        "asset_id",
    ):
        text = _text(recovery.get(key))
        if text:
            compact[key] = text
    for key in ("auto_retry_allowed", "requires_paid_media"):
        if isinstance(recovery.get(key), bool):
            compact[key] = recovery[key]
    for key in ("item_ids", "job_ids", "target_node_ids", "asset_ids"):
        values = _bounded_list(recovery.get(key))
        if values:
            compact[key] = values
    return compact


def _compact_workflow(workflow: Mapping[str, object]) -> dict[str, Any]:
    """Keep the recovery frontier while excluding workflow artifacts/payloads."""

    payload: dict[str, Any] = {}
    run_id = _text(workflow.get("run_id") or workflow.get("id"), limit=200)
    if run_id:
        payload["run_id"] = run_id
    for key in (
        "workflow_id",
        "status",
        "current_step",
        "runtime_phase",
        "error",
        "error_code",
        "terminal_reason",
        "next_action",
    ):
        value = _text(workflow.get(key))
        if value:
            payload[key] = value
    for key in ("revision", "event_seq", "last_verified_canvas_revision"):
        value = _bounded_int(workflow.get(key))
        if value is not None:
            payload[key] = value
    frontier = _bounded_list(workflow.get("current_frontier"))
    if frontier:
        payload["current_frontier"] = frontier

    raw_states = workflow.get("step_states")
    if isinstance(raw_states, Mapping):
        states: dict[str, dict[str, Any]] = {}
        for raw_step_id, raw_state in list(raw_states.items())[:32]:
            step_id = _text(raw_step_id, limit=160)
            if not step_id or not isinstance(raw_state, Mapping):
                continue
            state: dict[str, Any] = {}
            for key in (
                "status",
                "handler",
                "recovery_mode",
                "retry_policy",
                "execution_mode",
                "error",
                "error_code",
            ):
                value = _text(raw_state.get(key))
                if value:
                    state[key] = value
            for key in ("attempt", "progress"):
                value = raw_state.get(key)
                if key == "attempt":
                    parsed = _bounded_int(value)
                    if parsed is not None:
                        state[key] = parsed
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    state[key] = max(0.0, min(1.0, float(value)))
            dependencies = _bounded_list(raw_state.get("depends_on"))
            if dependencies:
                state["depends_on"] = dependencies
            if state:
                states[step_id] = state
        if states:
            payload["step_states"] = states
    artifact_refs = _compact_artifact_refs(workflow.get("artifacts"))
    if artifact_refs:
        payload["agent_artifacts"] = artifact_refs
    stage_receipts = project_workflow_stage_receipts(workflow)
    if stage_receipts:
        payload["stage_receipts"] = stage_receipts[:32]
    raw_artifacts = workflow.get("artifacts")
    if isinstance(raw_artifacts, Mapping):
        for artifact in list(raw_artifacts.values())[:32]:
            recovery = _compact_canvas_recovery(
                recovery_from_artifact(artifact)
            )
            if recovery:
                payload["recovery"] = recovery
                break
    return payload


def _compact_artifact_refs(value: object) -> list[dict[str, Any]]:
    """Collect stable Agent artifact refs without copying workflow payloads."""

    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def visit(item: object) -> None:
        if len(found) >= 32:
            return
        if isinstance(item, Mapping):
            candidate = item.get("agent_artifact") or item.get("artifact_ref")
            if isinstance(candidate, Mapping):
                projected = project_agent_artifact_ref(candidate)
                identity = str(projected.get("artifact_id") or "")
                if projected and identity and identity not in seen:
                    seen.add(identity)
                    found.append(projected)
            elif any(
                item.get(key) not in (None, "")
                for key in ("artifact_id", "artifact_sha256", "output_sha256", "content_sha256", "sha256")
            ):
                projected = project_agent_artifact_ref(item)
                identity = str(projected.get("artifact_id") or "")
                if projected and identity and identity not in seen:
                    seen.add(identity)
                    found.append(projected)
            for key, child in list(item.items())[:32]:
                if key in {"prompt", "inputs", "payload", "logs", "error", "artifact_uri", "url"}:
                    continue
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item[:32]:
                visit(child)

    visit(value)
    return found


def _recovery_contract(
    workflow: Mapping[str, object],
    provider_task_ids: object,
    pending_steps: object,
) -> dict[str, Any]:
    """Derive a deterministic recovery action from durable identities."""

    provider_ids = _bounded_list(provider_task_ids)
    run_id = _text(workflow.get("run_id") or workflow.get("id"), limit=200)
    status = _text(workflow.get("status"), limit=80).lower()
    pending = _bounded_list(pending_steps)
    recovery = _compact_canvas_recovery(workflow.get("recovery"))
    if provider_ids:
        action = "reconcile_provider_tasks"
        reason = "已存在上游任务身份，恢复只能查询/对账，禁止再次提交。"
    elif status == "failed" and recovery:
        action = "inspect_before_action"
        reason = _text(
            recovery.get("instruction")
            or "付费步骤已被门禁拒绝，先修复前置条件，禁止重放原命令。"
        )
    elif run_id and status in {"running", "waiting", "waiting_external", "recovering"}:
        action = "resume_workflow_run"
        reason = "已有活动 WorkflowRun，恢复应继续原运行并从当前 frontier 前进。"
    elif pending:
        action = "replay_pending_steps"
        reason = "没有已接受的上游任务，恢复只允许重放未完成步骤。"
    else:
        action = "inspect_before_action"
        reason = "恢复身份不足，先读取权威状态再决定下一动作。"
    active_run = bool(
        run_id and status in {"running", "waiting", "waiting_external", "recovering"}
    )
    return {
        "schema": "village_agent_recovery_contract.v1",
        "action": action,
        "allow_new_submission": not bool(
            provider_ids
            or active_run
            or (status == "failed" and recovery)
        ),
        "workflow_run_id": run_id,
        "provider_task_ids": provider_ids,
        "pending_steps": pending,
        "reason": reason,
        **({"recovery": recovery} if recovery else {}),
    }


def workflow_run_recovery_checkpoint(
    workflow: Mapping[str, object],
    *,
    project_id: object,
    canvas_id: object = "",
    requested_run_id: object = "",
) -> dict[str, Any]:
    """Project one client-identified Run into a server-owned recovery contract.

    A browser may name an existing Run, but it never owns the retry scope.  This
    function either derives the exact failed-item contract from the durable step
    artifact or returns a fail-closed contract with no submission permission.
    """

    run_id = _text(
        requested_run_id or workflow.get("run_id") or workflow.get("id"),
        limit=200,
    )
    actual_project_id = _text(
        workflow.get("project_id"),
        limit=200,
    )
    actual_canvas_id = _text(
        workflow.get("canvas_id"),
        limit=200,
    )
    expected_project_id = _text(project_id, limit=200)
    expected_canvas_id = _text(canvas_id, limit=200)
    status = _text(workflow.get("status"), limit=80).lower()
    scope_matches = bool(
        run_id
        and actual_project_id == expected_project_id
        and (not expected_canvas_id or actual_canvas_id == expected_canvas_id)
    )

    def checkpoint(
        *,
        action: str,
        allow_new_submission: bool,
        reason: str,
        recovery: Mapping[str, object] | None = None,
        include_workflow: bool = False,
    ) -> dict[str, Any]:
        contract: dict[str, Any] = {
            "schema": "village_agent_recovery_contract.v1",
            "action": action,
            "allow_new_submission": allow_new_submission,
            "workflow_run_id": run_id,
            "provider_task_ids": [],
            "pending_steps": [],
            "reason": reason,
        }
        if recovery:
            contract["recovery"] = dict(recovery)
        payload: dict[str, Any] = {
            "schema": CHECKPOINT_SCHEMA,
            "scope": {
                "project_id": expected_project_id,
                "canvas_id": expected_canvas_id or actual_canvas_id or "default",
            },
            "recovery_contract": contract,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if include_workflow:
            payload["workflow"] = _compact_workflow(workflow)
        return payload

    if not scope_matches:
        return checkpoint(
            action="inspect_before_action",
            allow_new_submission=False,
            reason="指定 Run 不存在或不属于当前项目 / 画布，禁止启动新的付费工作流。",
        )
    if status in {"running", "waiting", "waiting_external", "recovering", "paused"}:
        return checkpoint(
            action="resume_workflow_run",
            allow_new_submission=False,
            reason="已有原 Run，只能查询或恢复当前 frontier，禁止创建平行 Run。",
            include_workflow=True,
        )
    if status != "failed":
        return checkpoint(
            action="inspect_before_action",
            allow_new_submission=False,
            reason="原 Run 不是可恢复的失败状态，先读取权威状态再决定。",
            include_workflow=True,
        )

    states = (
        workflow.get("step_states")
        if isinstance(workflow.get("step_states"), Mapping)
        else {}
    )
    failed_step_ids = [
        _text(step_id, limit=160)
        for step_id, state in states.items()
        if isinstance(state, Mapping) and state.get("status") == "failed"
    ]
    failed_step_ids = [step_id for step_id in failed_step_ids if step_id]
    if len(failed_step_ids) != 1:
        return checkpoint(
            action="inspect_before_action",
            allow_new_submission=False,
            reason="失败范围不是唯一的原 step，先读取权威 Run，禁止猜测重试范围。",
            include_workflow=True,
        )

    step_id = failed_step_ids[0]
    artifacts = (
        workflow.get("artifacts")
        if isinstance(workflow.get("artifacts"), Mapping)
        else {}
    )
    artifact = artifacts.get(step_id)
    artifact = artifact if isinstance(artifact, Mapping) else {}
    state = states.get(step_id)
    state = state if isinstance(state, Mapping) else {}
    error_code = _text(
        artifact.get("error_code") or state.get("error_code"),
        limit=160,
    )
    rebuilt = build_workflow_step_recovery(
        error_code=error_code,
        details=artifact,
        step_id=step_id,
        run_id=run_id,
    )
    raw_recovery = rebuilt or recovery_from_artifact(artifact)
    valid_retry = bool(
        raw_recovery.get("action") == "retry_failed_items"
        and raw_recovery.get("requires_paid_media") is True
        and raw_recovery.get("rerun_scope") == "failed_items_only"
        and raw_recovery.get("error_code")
        and _text(raw_recovery.get("step_id"), limit=160) == step_id
        and raw_recovery.get("item_ids")
    )
    recovery = _compact_canvas_recovery(raw_recovery)
    if not valid_retry:
        return checkpoint(
            action="inspect_before_action",
            allow_new_submission=False,
            reason="未从原失败 step 得到精确失败项，禁止扩为 whole-step 或新建 Run。",
            recovery=recovery,
            include_workflow=True,
        )
    return checkpoint(
        action="inspect_before_action",
        allow_new_submission=False,
        reason=_text(recovery.get("instruction")) or "只重试原失败 item。",
        recovery=recovery,
        include_workflow=True,
    )


def _compact_orchestration(value: Mapping[str, object]) -> dict[str, Any]:
    """Persist the identities needed to replay one director turn.

    The full blackboard is rebuilt from authoritative sources on recovery. Only
    stable revisions and bounded role IDs belong in a checkpoint, so a resumed
    worker can detect stale planning without duplicating prompts or payloads.
    """

    result: dict[str, Any] = {}
    for key in ("schema", "plan_revision", "context_revision"):
        text = _text(value.get(key), limit=120)
        if text:
            result[key] = text

    fleet = value.get("agent_fleet")
    if isinstance(fleet, Mapping):
        fleet_payload: dict[str, Any] = {}
        for key in ("schema", "fleet_revision", "mode"):
            text = _text(fleet.get(key), limit=120)
            if text:
                fleet_payload[key] = text
        registry_revision = _text(fleet.get("registry_revision"), limit=120)
        if registry_revision:
            fleet_payload["registry_revision"] = registry_revision
        selected = _bounded_list(fleet.get("selected_agent_ids"))
        if not selected:
            selected = _bounded_list(
                [
                    item.get("agent_id")
                    for item in (fleet.get("selected_agents") or [])
                    if isinstance(item, Mapping)
                ]
            )
        if selected:
            fleet_payload["selected_agent_ids"] = selected[:16]
        groups = fleet.get("dispatch_groups")
        if isinstance(groups, (list, tuple)):
            compact_groups = [
                _bounded_list(group)[:16]
                for group in groups[:8]
                if isinstance(group, (list, tuple)) and _bounded_list(group)
            ]
            if compact_groups:
                fleet_payload["dispatch_groups"] = compact_groups
        if fleet_payload:
            result["agent_fleet"] = fleet_payload

        execution_plan = fleet.get("execution_plan")
        if isinstance(execution_plan, Mapping):
            execution_payload: dict[str, Any] = {}
            for key in ("schema", "plan_revision", "mode", "planner", "executor", "handoff_policy"):
                text = _text(execution_plan.get(key), limit=120)
                if text:
                    execution_payload[key] = text
            task_ids = _bounded_list(
                [
                    item.get("task_id")
                    for item in (execution_plan.get("tasks") or [])
                    if isinstance(item, Mapping)
                ]
            )
            if task_ids:
                execution_payload["task_ids"] = task_ids[:32]
            contracts: list[dict[str, Any]] = []
            for item in (execution_plan.get("tasks") or [])[:32]:
                if not isinstance(item, Mapping):
                    continue
                task_id = _text(item.get("task_id"), limit=160)
                output_contract = item.get("output_contract")
                if not task_id or not isinstance(output_contract, Mapping):
                    continue
                contract: dict[str, Any] = {"task_id": task_id}
                schema = _text(output_contract.get("schema"), limit=120)
                artifact_schema = _text(output_contract.get("artifact_schema"), limit=120)
                if schema:
                    contract["schema"] = schema
                if artifact_schema:
                    contract["artifact_schema"] = artifact_schema
                if "artifact_required" in output_contract:
                    contract["artifact_required"] = bool(output_contract.get("artifact_required"))
                consumers = _bounded_list(output_contract.get("consumer_agent_ids"))
                if consumers:
                    contract["consumer_agent_ids"] = consumers[:8]
                handler = item.get("handler")
                if isinstance(handler, Mapping):
                    handler_id = _text(handler.get("handler_id"), limit=200)
                    if handler_id:
                        contract["handler_id"] = handler_id
                contracts.append(contract)
            if contracts:
                execution_payload["task_contracts"] = contracts[:32]
            if execution_payload:
                result["execution_plan"] = execution_payload

    ledger = value.get("handoff_ledger")
    if isinstance(ledger, Mapping):
        ledger_payload: dict[str, Any] = {}
        for key in ("schema", "plan_revision"):
            text = _text(ledger.get(key), limit=120)
            if text:
                ledger_payload[key] = text
        for key in ("completed_task_ids", "pending_task_ids"):
            values = _bounded_list(ledger.get(key))
            if values:
                ledger_payload[key] = values[:32]
        accepted_count = ledger.get("accepted_count")
        if isinstance(accepted_count, int) and not isinstance(accepted_count, bool):
            ledger_payload["accepted_count"] = max(0, accepted_count)
        rejections: list[dict[str, Any]] = []
        for item in (ledger.get("rejections") or [])[:16]:
            if not isinstance(item, Mapping):
                continue
            projected = {
                key: _text(item.get(key), limit=240)
                for key in ("schema", "status", "reason", "task_id", "agent_id")
                if _text(item.get(key), limit=240)
            }
            missing = _bounded_list(item.get("missing_dependencies"))
            if missing:
                projected["missing_dependencies"] = missing[:16]
            if projected:
                rejections.append(projected)
        if rejections:
            ledger_payload["rejections"] = rejections
        if ledger_payload:
            result["handoff_ledger"] = ledger_payload

    allowlist = value.get("runtime_allowlist")
    if isinstance(allowlist, Mapping):
        allowlist_payload: dict[str, Any] = {}
        for key in ("allowlist_revision", "mode"):
            text = _text(allowlist.get(key), limit=120)
            if text:
                allowlist_payload[key] = text
        if "execution_enabled" in allowlist:
            allowlist_payload["execution_enabled"] = bool(allowlist.get("execution_enabled"))
        if allowlist_payload:
            result["runtime_allowlist"] = allowlist_payload
    return result


def _compact_recovery_contract(value: object) -> dict[str, Any]:
    """Preserve one already-authoritative recovery contract without widening it."""

    if not isinstance(value, Mapping):
        return {}
    if value.get("schema") != "village_agent_recovery_contract.v1":
        return {}
    action = _text(value.get("action"), limit=80)
    if not action:
        return {}
    contract: dict[str, Any] = {
        "schema": "village_agent_recovery_contract.v1",
        "action": action,
        "allow_new_submission": value.get("allow_new_submission") is True,
    }
    run_id = _text(value.get("workflow_run_id"), limit=200)
    if run_id:
        contract["workflow_run_id"] = run_id
    provider_task_ids = _bounded_list(value.get("provider_task_ids"))
    if provider_task_ids:
        contract["provider_task_ids"] = provider_task_ids
    pending_steps = _bounded_list(value.get("pending_steps"))
    if pending_steps:
        contract["pending_steps"] = pending_steps
    reason = _text(value.get("reason"))
    if reason:
        contract["reason"] = reason
    recovery = _compact_canvas_recovery(value.get("recovery"))
    if recovery:
        contract["recovery"] = recovery
    return contract


def _text(value: object, *, limit: int = _MAX_TEXT_CHARS) -> str:
    text = redact_secrets(str(value or "")).strip()
    return _BARE_SECRET_RE.sub("[redacted]", text)[:limit]


def _bounded_list(values: object) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    result: list[str] = []
    for value in values:
        item = _text(value, limit=_MAX_LIST_ITEM_CHARS)
        if item and item not in result:
            result.append(item)
        if len(result) >= _MAX_LIST_ITEMS:
            break
    return result


def _bounded_errors(values: object) -> list[object]:
    """Keep structured provider failures intact across recovery.

    Provider failures need a stable ``type`` for resume decisions, while
    free-form tool errors remain bounded strings.  Encoding every entry with
    ``_text`` collapses that structure and turns a balance outage into a
    generic tool failure, so keep a narrow mapping shape here.
    """

    if not isinstance(values, (list, tuple, set)):
        return []
    result: list[object] = []
    for value in values:
        if isinstance(value, Mapping):
            item = {
                str(key): _text(value.get(key), limit=_MAX_LIST_ITEM_CHARS)
                for key in ("type", "message", "error_code", "provider")
                if _text(value.get(key), limit=_MAX_LIST_ITEM_CHARS)
            }
            if not item:
                continue
            item.setdefault("type", "provider_error")
            if item not in result:
                result.append(item)
        else:
            item = _text(value, limit=_MAX_LIST_ITEM_CHARS)
            if item and item not in result:
                result.append(item)
        if len(result) >= _MAX_LIST_ITEMS:
            break
    return result


def _bounded_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _checkpoint_file(state_dir: str | Path, turn_id: str) -> Path:
    digest = hashlib.sha256(_text(turn_id, limit=256).encode("utf-8")).hexdigest()[:24]
    return Path(state_dir) / "agent_checkpoints" / f"{digest}.json"


def build_checkpoint(
    *,
    state_dir: str | Path,
    project_id: str,
    canvas_id: str | None,
    turn_id: str | None,
    logical_session_id: str,
    goal: str,
    completed_steps: object = (),
    pending_steps: object = (),
    active_errors: object = (),
    canvas: Mapping[str, object] | None = None,
    workflow: Mapping[str, object] | None = None,
    provider_task_ids: object = (),
    model_bindings: Mapping[str, object] | None = None,
    orchestration: Mapping[str, object] | None = None,
    recovery_contract_override: Mapping[str, object] | None = None,
    next_action: str = "继续当前任务，不重复已完成的命令。",
    checkpoint_id: str | None = None,
) -> dict[str, Any]:
    """Create a bounded, credential-free checkpoint payload."""

    canvas_values = canvas if isinstance(canvas, Mapping) else {}
    workflow_values = workflow if isinstance(workflow, Mapping) else {}
    model_values = model_bindings if isinstance(model_bindings, Mapping) else {}
    canvas_payload: dict[str, Any] = {
        "project_id": _text(canvas_values.get("project_id") or project_id),
        "canvas_id": _text(canvas_values.get("canvas_id") or canvas_id or "default"),
    }
    revision = _bounded_int(canvas_values.get("revision"))
    if revision is not None:
        canvas_payload["revision"] = revision
    for key in ("node_count", "edge_count"):
        value = _bounded_int(canvas_values.get(key))
        if value is not None:
            canvas_payload[key] = value

    # Keep focus IDs across recovery; every resumed turn still re-reads the server.
    for key in (
        "selected_node_ids",
        "pinned_node_ids",
        "reference_candidate_node_ids",
    ):
        if key in canvas_values:
            canvas_payload[key] = _bounded_list(canvas_values[key])

    workflow_payload = _compact_workflow(workflow_values)
    model_payload = {
        key: model_values[key]
        for key in ("agent_registry_id", "context_length", "max_output_tokens", "context_source")
        if key in model_values and model_values[key] not in (None, "")
    }
    orchestration_payload = _compact_orchestration(
        orchestration if isinstance(orchestration, Mapping) else {}
    )
    return {
        "schema": CHECKPOINT_SCHEMA,
        "checkpoint_id": _text(checkpoint_id, limit=128) or uuid.uuid4().hex,
        "scope": {
            "project_id": _text(project_id),
            "canvas_id": _text(canvas_id or "default"),
        },
        "logical_session_id": _text(logical_session_id, limit=256),
        "turn_id": _text(turn_id, limit=256),
        "goal": _text(goal, limit=2_000),
        "completed_steps": _bounded_list(completed_steps),
        "pending_steps": _bounded_list(pending_steps),
        "active_errors": _bounded_errors(active_errors),
        "canvas": canvas_payload,
        "workflow": workflow_payload,
        "provider_task_ids": _bounded_list(provider_task_ids),
        "recovery_contract": (
            _compact_recovery_contract(recovery_contract_override)
            or _recovery_contract(
                workflow_payload,
                provider_task_ids,
                pending_steps,
            )
        ),
        "model_bindings": model_payload,
        "orchestration": orchestration_payload,
        "next_action": _text(next_action),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def save_checkpoint(state_dir: str | Path, checkpoint: Mapping[str, Any]) -> Path:
    """Atomically persist one turn checkpoint and update the project pointer."""

    payload = dict(checkpoint)
    turn_id = _text(payload.get("turn_id"), limit=256) or _text(
        payload.get("checkpoint_id"), limit=128
    )
    destination = _checkpoint_file(state_dir, turn_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(destination, payload)
    write_json_atomic(destination.parent / "latest.json", payload)
    return destination


def load_checkpoint(state_dir: str | Path, turn_id: str | None) -> dict[str, Any] | None:
    """Load only the checkpoint belonging to the requested logical turn."""

    normalized_turn = _text(turn_id, limit=256)
    if not normalized_turn:
        return None
    path = _checkpoint_file(state_dir, normalized_turn)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != CHECKPOINT_SCHEMA:
        return None
    if _text(payload.get("turn_id"), limit=256) != normalized_turn:
        return None
    return payload


def delete_checkpoints(state_dir: str | Path, turn_ids: object) -> int:
    """Delete checkpoints for an exact set of deleted chat turns."""

    if not isinstance(turn_ids, (list, tuple, set, frozenset)):
        return 0
    normalized_turns = {
        normalized
        for value in turn_ids
        if (normalized := _text(value, limit=256))
    }
    if not normalized_turns:
        return 0

    deleted = 0
    for turn_id in normalized_turns:
        path = _checkpoint_file(state_dir, turn_id)
        try:
            path.unlink()
            deleted += 1
        except OSError:
            pass

    latest_path = Path(state_dir) / "agent_checkpoints" / "latest.json"
    try:
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        latest = None
    if isinstance(latest, dict) and _text(latest.get("turn_id"), limit=256) in normalized_turns:
        try:
            latest_path.unlink()
            deleted += 1
        except OSError:
            pass
    return deleted


def checkpoint_prompt(checkpoint: Mapping[str, Any] | None) -> str:
    """Render a bounded recovery block safe to prepend to a model prompt."""

    if not isinstance(checkpoint, Mapping):
        return ""
    public_payload = {
        key: checkpoint.get(key)
        for key in (
            "schema",
            "checkpoint_id",
            "scope",
            "logical_session_id",
            "turn_id",
            "goal",
            "completed_steps",
            "pending_steps",
            "active_errors",
            "canvas",
            "workflow",
            "provider_task_ids",
            "recovery_contract",
            "model_bindings",
            "orchestration",
            "next_action",
            "updated_at",
        )
        if key in checkpoint
    }
    encoded = json.dumps(public_payload, ensure_ascii=False, separators=(",", ":"))
    encoded = encoded[: MAX_PROMPT_CHARS - 180]
    return (
        "[VILLAGE_AGENT_CONTEXT_CHECKPOINT]\n"
        f"{encoded}\n"
        "[/VILLAGE_AGENT_CONTEXT_CHECKPOINT]\n"
        "这是当前项目的结构化恢复点。保留已完成状态，先核对当前画布/工作流事实，"
        "再继续下一动作；不要重复已有 command_id，也不要伪造执行结果。"
    )


__all__ = [
    "CHECKPOINT_SCHEMA",
    "build_checkpoint",
    "checkpoint_prompt",
    "delete_checkpoints",
    "load_checkpoint",
    "save_checkpoint",
    "workflow_run_recovery_checkpoint",
]
