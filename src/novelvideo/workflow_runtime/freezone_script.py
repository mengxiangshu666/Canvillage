"""Durable WorkflowRun bridge for the Freezone structured story-script task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from novelvideo.services.canvas_commands import (
    fingerprint_canvas_script_rows as script_rows_fingerprint,
    read_canvas_snapshot as read_canvas,
    validate_canvas_script_rows as validate_script_rows,
)
from novelvideo.ports import get_task_backend
from novelvideo.task_identity import project_task_state_key
from novelvideo.task_state import get_task_manager
from novelvideo.workflow_runtime.media_dispatch_support import (
    _deterministic_job_id,
    _text,
    resolve_workflow_project_context,
)
from novelvideo.workflow_runtime.model_plan import resolve_snapshot_model_ref
from novelvideo.workflow_runtime.script_asset_ledger import (
    build_script_asset_ledger,
)
from novelvideo.workflow_runtime.step_contract import (
    StepResult,
    WorkflowStepExecutionError,
)


TASK_TYPE = "freezone_story_script"
ARTIFACT_KIND = "freezone_script_contract"
SOURCE_CANVAS_SCRIPT_NODE = "canvas_script_node"
_TERMINAL_FAILURES = {"failed", "cancelled", "canceled"}
_ACTIVE_STATUSES = {
    "submitting",
    "queued",
    "starting",
    "pending",
    "dispatching",
    "waiting",
    "running",
}
_REUSED_CONTRACT_IDENTITY_FIELDS = (
    "canvas_id",
    "canvas_revision",
    "script_node_id",
    "rows_fingerprint",
    "result_signature",
)


def _scope(run: dict[str, Any], *, attempt: int = 1) -> str:
    """Return the durable task scope for one script attempt.

    A manual retry must land on a fresh task key. Reusing the failed attempt's
    scope would keep re-reading the dead task, so "重试" could never generate a
    new script. Attempt 1 keeps the historical key so runs already in flight
    stay reconcilable.
    """

    base = f"workflow:{_text(run.get('id'))}:script"
    normalized = max(1, int(attempt or 1))
    if normalized > 1:
        base = f"{base}:a{normalized}"
    return base[:240]


def _step_attempt(run: dict[str, Any], step_id: str) -> int:
    """Read the current step attempt from the freshly loaded run."""

    step = (run.get("step_states") or {}).get(step_id)
    if not isinstance(step, dict):
        return 1
    return max(1, int(step.get("attempt") or 1))


def _script_model_ref(run: dict[str, Any], inputs: dict[str, Any]) -> str:
    """Return the frozen script model and reject a different live default."""

    snapshot = run.get("model_plan_snapshot")
    bindings = snapshot.get("bindings") if isinstance(snapshot, dict) else None
    binding = bindings.get("director") if isinstance(bindings, dict) else None
    if isinstance(binding, dict):
        try:
            kind, model_ref = resolve_snapshot_model_ref(snapshot, "director")
        except Exception as exc:  # noqa: BLE001 - normalize model contract failures
            raise WorkflowStepExecutionError(
                f"工作流脚本模型合同无效：{exc}",
                code="workflow_script_model_invalid",
                details={"media_submission_started": False},
            ) from exc
        if kind not in {"agent", "text"}:
            raise WorkflowStepExecutionError(
                "脚本合同步骤必须绑定直连 Agent 或文字模型",
                code="workflow_script_model_invalid",
                details={"media_submission_started": False},
            )
        return model_ref
    if int(run.get("contract_version") or 1) >= 2:
        raise WorkflowStepExecutionError(
            "工作流模型方案缺少 director 绑定",
            code="workflow_script_model_invalid",
            details={"media_submission_started": False},
        )
    return _text(inputs.get("script_model") or inputs.get("model"))


def _artifact_base(
    run: dict[str, Any],
    *,
    step_id: str,
    node_id: str,
    scope: str,
) -> dict[str, Any]:
    return {
        "schema": "workflow_freezone_script_artifact.v1",
        "kind": ARTIFACT_KIND,
        "task_type": TASK_TYPE,
        "workflow_run_id": _text(run.get("id")),
        "workflow_step_id": step_id,
        "node_id": node_id,
        "scope": scope,
    }


def _stable_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _target_strategy(run: dict[str, Any]) -> str:
    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    return _text(inputs.get("target_strategy"))


def _explicit_script_node_id(run: dict[str, Any]) -> str:
    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    return _text(inputs.get("script_node_id"))


def _candidate_target_node_ids(run: dict[str, Any]) -> list[str]:
    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    values: list[Any] = []
    if _target_strategy(run) == "reuse_existing":
        values.extend([inputs.get("script_node_id"), inputs.get("node_id")])
    raw_targets = inputs.get("target_node_ids")
    if isinstance(raw_targets, list):
        values.extend(raw_targets)
    result: list[str] = []
    for value in values:
        node_id = _text(value)
        if node_id and node_id not in result:
            result.append(node_id)
    return result


def _raise_script_reuse_error(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> None:
    raise WorkflowStepExecutionError(
        message,
        code=code,
        details=details or {},
    )


def reuse_canvas_script_contract(
    run: dict[str, Any],
    *,
    state_dir: Path,
) -> dict[str, Any] | None:
    """Read one valid script contract from the authoritative canvas.

    The caller's target list can contain dependency nodes, so the branch only
    activates when exactly one existing target is a ``scriptNode``.  A
    ``create_missing`` strategy may leave downstream targets uncreated, but it
    must not discard an authoritative upstream script that is already present.
    The node rows and report are always revalidated from the persisted canvas;
    nothing from the client request is trusted as script content.
    """

    target_node_ids = _candidate_target_node_ids(run)
    if not target_node_ids:
        return None
    canvas_id = _text(run.get("canvas_id"))
    if not canvas_id:
        _raise_script_reuse_error(
            "workflow_script_reuse_canvas_missing",
            "脚本复用缺少画布标识，禁止生成替代脚本。",
        )
    canvas = read_canvas(state_dir, canvas_id)
    if not isinstance(canvas, dict):
        _raise_script_reuse_error(
            "workflow_script_reuse_canvas_missing",
            "无法读取权威画布，禁止生成替代脚本。",
            details={"canvas_id": canvas_id, "target_node_ids": target_node_ids},
        )
    nodes = {
        _text(node.get("id")): node
        for node in (canvas.get("nodes") or [])
        if isinstance(node, dict) and _text(node.get("id"))
    }
    strategy = _target_strategy(run)
    missing = [node_id for node_id in target_node_ids if node_id not in nodes]
    script_nodes = [
        nodes[node_id]
        for node_id in target_node_ids
        if node_id in nodes and _text(nodes[node_id].get("type")) == "scriptNode"
    ]
    if not script_nodes:
        explicit_script_node_id = _explicit_script_node_id(run)
        if explicit_script_node_id and explicit_script_node_id not in nodes:
            _raise_script_reuse_error(
                "workflow_script_reuse_target_not_found",
                "脚本复用目标节点不存在，禁止生成替代脚本。",
                details={
                    "canvas_id": canvas_id,
                    "missing_node_ids": [explicit_script_node_id],
                },
            )
        if strategy == "reuse_existing":
            if missing:
                _raise_script_reuse_error(
                    "workflow_script_reuse_target_not_found",
                    "脚本复用目标节点不存在，禁止生成替代脚本。",
                    details={
                        "canvas_id": canvas_id,
                        "missing_node_ids": missing,
                    },
                )
            _raise_script_reuse_error(
                "workflow_script_reuse_target_not_script",
                "脚本复用目标不是 scriptNode，禁止复用行或生成替代脚本。",
                details={
                    "canvas_id": canvas_id,
                    "target_node_ids": target_node_ids,
                },
            )
        return None
    if missing and strategy != "create_missing":
        _raise_script_reuse_error(
            "workflow_script_reuse_target_not_found",
            "脚本复用目标节点不存在，禁止生成替代脚本。",
            details={
                "canvas_id": canvas_id,
                "missing_node_ids": missing,
            },
        )
    explicit_script_node_id = _explicit_script_node_id(run)
    if explicit_script_node_id and explicit_script_node_id not in nodes:
        _raise_script_reuse_error(
            "workflow_script_reuse_target_not_found",
            "脚本复用目标节点不存在，禁止生成替代脚本。",
            details={
                "canvas_id": canvas_id,
                "missing_node_ids": [explicit_script_node_id],
            },
        )
    if len(script_nodes) != 1:
        _raise_script_reuse_error(
            "workflow_script_reuse_target_ambiguous",
            "脚本复用目标包含多个 scriptNode，必须先绑定唯一脚本节点。",
            details={
                "canvas_id": canvas_id,
                "script_node_ids": [
                    _text(node.get("id")) for node in script_nodes
                ],
            },
        )

    node = script_nodes[0]
    script_node_id = _text(node.get("id"))
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    result = (
        data.get("scriptResult")
        if isinstance(data.get("scriptResult"), dict)
        else {}
    )
    raw_rows = result.get("rows")
    rows = (
        [dict(row) for row in raw_rows if isinstance(row, dict)]
        if isinstance(raw_rows, list)
        else []
    )
    if not rows:
        _raise_script_reuse_error(
            "workflow_script_reuse_rows_missing",
            "脚本节点没有可复用的结构化分镜行。",
            details={"canvas_id": canvas_id, "script_node_id": script_node_id},
        )

    current_fingerprint = script_rows_fingerprint(rows)
    stored_report = (
        data.get("scriptContractReport")
        if isinstance(data.get("scriptContractReport"), dict)
        else None
    )
    if stored_report is None:
        _raise_script_reuse_error(
            "workflow_script_reuse_contract_report_missing",
            "脚本节点缺少合同报告，必须先保存并通过画布合同检查。",
            details={
                "canvas_id": canvas_id,
                "script_node_id": script_node_id,
                "rows_fingerprint": current_fingerprint,
            },
        )
    if _text(stored_report.get("rows_fingerprint")) != current_fingerprint:
        _raise_script_reuse_error(
            "workflow_script_reuse_contract_stale",
            "脚本节点的合同报告已过期，禁止复用旧合同。",
            details={
                "canvas_id": canvas_id,
                "script_node_id": script_node_id,
                "rows_fingerprint": current_fingerprint,
                "report_rows_fingerprint": _text(
                    stored_report.get("rows_fingerprint")
                ),
            },
        )

    contract_report = validate_script_rows(rows).as_dict()
    blocking = _blocking_report(contract_report)
    if blocking:
        _raise_script_reuse_error(
            "workflow_script_contract_blocked",
            f"脚本合同仍有 {len(blocking)} 条阻断问题，禁止进入下游。",
            details={
                "canvas_id": canvas_id,
                "script_node_id": script_node_id,
                "rows_fingerprint": current_fingerprint,
                "contract_report": contract_report,
                "blocking_issues": blocking,
            },
        )

    asset_ledger = build_script_asset_ledger(rows)
    canvas_revision = canvas.get("revision")
    if not isinstance(canvas_revision, int) or isinstance(canvas_revision, bool):
        canvas_revision = None
    url = _text(result.get("output_url") or result.get("url"))
    title = _text(result.get("title"))
    signature = _stable_sha256(
        {
            "schema": "workflow_canvas_script_reuse.v1",
            "source": SOURCE_CANVAS_SCRIPT_NODE,
            "project_id": _text(run.get("project_id")),
            "canvas_id": canvas_id,
            "canvas_revision": canvas_revision,
            "script_node_id": script_node_id,
            "rows": rows,
            "contract_report": contract_report,
            "asset_ledger": asset_ledger,
            "url": url,
        }
    )
    return {
        "schema": "workflow_freezone_script_artifact.v1",
        "kind": ARTIFACT_KIND,
        "task_type": TASK_TYPE,
        "workflow_run_id": _text(run.get("id")),
        "workflow_step_id": "script_contract",
        "node_id": script_node_id,
        "scope": _scope(run),
        "status": "completed",
        "progress": 1.0,
        "reused": True,
        "source": SOURCE_CANVAS_SCRIPT_NODE,
        "canvas_id": canvas_id,
        "canvas_revision": canvas_revision,
        "script_node_id": script_node_id,
        "rows_fingerprint": current_fingerprint,
        "rows": rows,
        "contract_report": contract_report,
        "asset_ledger": asset_ledger,
        "result_signature": signature,
        "job_id": f"canvas-script-{current_fingerprint[:16]}",
        "url": url,
        "title": title,
    }


def ensure_reused_canvas_script_contract_current(
    run: dict[str, Any],
    *,
    state_dir: Path,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    """Reject a canvas-script artifact after any bound canvas fact changes."""

    if _text(artifact.get("source")) != SOURCE_CANVAS_SCRIPT_NODE:
        return artifact
    current = reuse_canvas_script_contract(run, state_dir=state_dir)
    if current is None:
        _raise_script_reuse_error(
            "workflow_script_reuse_target_not_found",
            "脚本复用 Run 缺少可验证的画布目标。",
            details={"media_submission_started": False},
        )
    changed: dict[str, dict[str, Any]] = {}
    for field in _REUSED_CONTRACT_IDENTITY_FIELDS:
        expected = artifact.get(field)
        observed = current.get(field)
        if expected != observed:
            changed[field] = {
                "expected": expected,
                "current": observed,
            }
    if changed:
        _raise_script_reuse_error(
            "workflow_script_reuse_contract_stale",
            "画布脚本合同已变化，拒绝复用旧脚本继续下游。",
            details={
                "canvas_id": _text(run.get("canvas_id")),
                "script_node_id": _text(artifact.get("script_node_id")),
                "changed_fields": changed,
                "media_submission_started": False,
            },
        )
    return current


async def dispatch_workflow_script(
    run: dict[str, Any],
    *,
    state_dir: Path,
    step_id: str,
) -> dict[str, Any]:
    """Submit one idempotent structured-script task for this WorkflowRun."""

    ctx = await resolve_workflow_project_context(run)
    reused = reuse_canvas_script_contract(run, state_dir=state_dir)
    if reused is not None:
        return {**reused, "workflow_step_id": step_id}
    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    source_text = _text(inputs.get("source_text"))
    prompt = _text(inputs.get("prompt") or inputs.get("request"))
    if not source_text:
        # 工作流对外只声明 `request` 入口：一句话启动时没有单独上传的原文，
        # 这句话本身就是源材料。不兜底则持久任务会以 `source_text is required`
        # 在第一步直接失败，整条工作流还没开始就结束。
        source_text = prompt
        prompt = ""
    if not source_text and not prompt:
        raise ValueError("脚本合同工作流缺少 source_text 或 prompt")
    script_model = _script_model_ref(run, inputs)

    node_id = _text(
        inputs.get("script_node_id")
        or inputs.get("node_id")
        or f"script-{_text(run.get('id'))}"
    )
    attempt = _step_attempt(run, step_id)
    scope = _scope(run, attempt=attempt)
    job_id = _deterministic_job_id(
        run,
        step_id=step_id,
        node_id=node_id,
        retry_seq=attempt - 1,
    )
    manager = get_task_manager()
    existing = manager.get_task_for_project(
        ctx,
        TASK_TYPE,
        0,
        scope=scope,
    )
    if existing is not None:
        status = _text(getattr(existing, "status", ""))
        if status not in _TERMINAL_FAILURES:
            return {
                **_artifact_base(
                    run,
                    step_id=step_id,
                    node_id=node_id,
                    scope=scope,
                ),
                "status": "monitoring",
                "task_id": _text(getattr(existing, "task_id", "")),
                "job_id": job_id,
                "task_key": project_task_state_key(
                    TASK_TYPE,
                    ctx.project_id,
                    0,
                    scope=scope,
                ),
                "progress": max(
                    0.0,
                    min(float(getattr(existing, "progress", 0.0) or 0.0), 0.99),
                ),
                "message": (
                    "已回读到脚本合同任务，等待持久产物与资产台账核验。"
                    if status == "completed"
                    else "脚本合同生成中"
                ),
                "reused": True,
            }
        # A terminal failure is not a reusable artifact. Fall through so the task
        # backend starts a fresh run instead of monitoring the dead task again.

    payload = {
        "job_id": job_id,
        "source_text": source_text,
        "prompt": prompt,
        "model": script_model,
        "canvas_id": _text(run.get("canvas_id")),
        "node_id": node_id,
        "project_dir": str(ctx.output_dir),
        "title": _text(inputs.get("title")),
        "character_refs": list(inputs.get("character_refs") or []),
        "task_family": "workflow_runtime",
        "task_label": "工作流生成脚本合同",
        "display_name": "工作流生成脚本合同",
    }
    queued = await get_task_backend().enqueue_project_task(
        ctx,
        task_type=TASK_TYPE,
        queue_kind="script",
        episode=0,
        scope=scope,
        payload=payload,
    )
    task_state = getattr(queued, "task_state", None)
    status = _text(getattr(task_state, "status", "queued")) or "queued"
    acceptance_receipt = getattr(queued, "acceptance_receipt", None)
    return {
        **_artifact_base(
            run,
            step_id=step_id,
            node_id=node_id,
            scope=scope,
        ),
        "status": "completed" if status == "completed" else "monitoring",
        "task_id": _text(getattr(task_state, "task_id", "")),
        "job_id": job_id,
        "task_key": project_task_state_key(
            TASK_TYPE,
            ctx.project_id,
            0,
            scope=scope,
        ),
        "progress": max(
            0.0,
            min(float(getattr(task_state, "progress", 0.0) or 0.0), 0.99),
        ),
        "reused": False,
        **(
            {"task_acceptance_receipt": acceptance_receipt}
            if isinstance(acceptance_receipt, dict) and acceptance_receipt
            else {}
        ),
    }


def _blocking_report(contract_report: Any) -> list[dict[str, Any]]:
    if not isinstance(contract_report, dict):
        return []
    issues = contract_report.get("issues")
    if not isinstance(issues, list):
        return []
    return [
        issue
        for issue in issues
        if isinstance(issue, dict)
        and str(issue.get("severity") or "").strip().casefold() == "blocking"
    ]


async def reconcile_workflow_script(
    run: dict[str, Any],
    *,
    step_id: str,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    """Read back the durable script task and fail closed on contract blockers."""

    ctx = await resolve_workflow_project_context(run)
    if _text(artifact.get("source")) == SOURCE_CANVAS_SCRIPT_NODE:
        try:
            reused = ensure_reused_canvas_script_contract_current(
                run,
                state_dir=ctx.state_dir,
                artifact=artifact,
            )
        except WorkflowStepExecutionError as exc:
            return {
                **artifact,
                "status": "failed",
                "error_code": exc.code,
                "error": str(exc),
                **exc.details,
            }
        return {**artifact, **reused, "workflow_step_id": step_id}
    scope = _text(artifact.get("scope")) or _scope(
        run,
        attempt=_step_attempt(run, step_id),
    )
    node_id = _text(artifact.get("node_id"))
    task_id = _text(artifact.get("task_id"))
    job_id = _text(artifact.get("job_id"))
    manager = get_task_manager()
    task = manager.get_task_for_project(
        ctx,
        TASK_TYPE,
        0,
        scope=scope,
    )
    if task is None and task_id:
        lookup_history = getattr(manager, "get_task_run_for_project", None)
        if callable(lookup_history):
            task = lookup_history(ctx, task_id)
    base = _artifact_base(
        run,
        step_id=step_id,
        node_id=node_id,
        scope=scope,
    )
    if task is None:
        return {
            **artifact,
            **base,
            "status": "monitoring",
            "progress": float(artifact.get("progress") or 0.0),
            "message": "脚本合同任务尚未回读到持久状态。",
        }

    task_status = _text(getattr(task, "status", ""))
    if task_status in _ACTIVE_STATUSES:
        return {
            **artifact,
            **base,
            "status": "monitoring",
            "progress": max(
                0.0,
                min(float(getattr(task, "progress", 0.0) or 0.0), 0.99),
            ),
            "message": str(
                getattr(task, "current_task", "") or "脚本合同生成中"
            ),
        }
    if task_status in _TERMINAL_FAILURES:
        return {
            **artifact,
            **base,
            "status": "failed",
            "error_code": "workflow_script_task_failed",
            "error": _text(getattr(task, "error", ""))
            or f"脚本合同任务{task_status}",
        }
    if task_status != "completed":
        return {
            **artifact,
            **base,
            "status": "monitoring",
            "progress": max(
                0.0,
                min(float(getattr(task, "progress", 0.0) or 0.0), 0.99),
            ),
        }

    result = getattr(task, "result", None)
    result = result if isinstance(result, dict) else {}
    rows = result.get("rows") if isinstance(result.get("rows"), list) else []
    if not rows:
        return {
            **artifact,
            **base,
            "status": "failed",
            "error_code": "workflow_script_rows_missing",
            "error": "脚本任务已完成但没有返回结构化脚本行",
        }
    contract_report = (
        result.get("contract_report")
        if isinstance(result.get("contract_report"), dict)
        else {}
    )
    blocking = _blocking_report(contract_report)
    if blocking:
        return {
            **artifact,
            **base,
            "status": "failed",
            "error_code": "workflow_script_contract_blocked",
            "error": f"脚本合同仍有 {len(blocking)} 条阻断问题，禁止进入下游",
            "contract_report": contract_report,
            "blocking_issues": blocking,
        }

    url = _text(result.get("output_url") or result.get("url"))
    asset_ledger = build_script_asset_ledger(rows)
    signature_payload = {
        "task_id": _text(getattr(task, "task_id", "")) or task_id,
        "job_id": job_id,
        "rows": rows,
        "contract_report": contract_report,
        "asset_ledger": asset_ledger,
        "url": url,
    }
    signature = hashlib.sha256(
        json.dumps(
            signature_payload,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        **artifact,
        **base,
        "status": "completed",
        "progress": 1.0,
        "task_id": signature_payload["task_id"],
        "job_id": job_id or _text(result.get("job_id")),
        "task_key": project_task_state_key(
            TASK_TYPE,
            ctx.project_id,
            0,
            scope=scope,
        ),
        "output_path": _text(result.get("output_path")),
        "url": url,
        "title": _text(result.get("title")),
        "rows": rows,
        "contract_report": contract_report,
        "asset_ledger": asset_ledger,
        "result_signature": signature,
    }


async def handle_workflow_script_contract(
    run: dict[str, Any], step: dict[str, Any]
) -> StepResult:
    """Dispatch or reconcile the durable structured-script task."""

    step_id = str(step.get("id") or "script_contract")
    existing = run.get("artifacts", {}).get(step_id)
    if isinstance(existing, dict) and existing.get("status") in {
        "monitoring",
        "completed",
    }:
        result = await reconcile_workflow_script(
            run,
            step_id=step_id,
            artifact=existing,
        )
        if result.get("status") == "failed":
            raise WorkflowStepExecutionError(
                str(result.get("error") or "脚本合同未通过"),
                code=str(result.get("error_code") or "workflow_script_failed"),
                details={
                    key: value
                    for key, value in result.items()
                    if key not in {"error", "error_code"}
                },
            )
        if result.get("status") == "completed":
            return StepResult("step_completed", result)
        return StepResult("waiting", result)
    payload = await dispatch_workflow_script(
        run,
        state_dir=Path(str(run.get("_state_dir") or "")),
        step_id=step_id,
    )
    return StepResult("step_progress", payload)


__all__ = [
    "ARTIFACT_KIND",
    "SOURCE_CANVAS_SCRIPT_NODE",
    "TASK_TYPE",
    "dispatch_workflow_script",
    "ensure_reused_canvas_script_contract_current",
    "handle_workflow_script_contract",
    "reuse_canvas_script_contract",
    "reconcile_workflow_script",
]
