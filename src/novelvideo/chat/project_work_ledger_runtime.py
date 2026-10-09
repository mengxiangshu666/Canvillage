"""Project real tool receipts into the persistent project work ledger.

The ledger module owns the data shape and invariants.  This bridge owns the
translation from the Agent's tool results into ledger events, and deliberately
accepts only authoritative receipts:

* verified workflow-stage receipts from ``workflow_stage_receipts``;
* completed WorkflowRun steps for non-media planning stages;
* server-applied canvas writes.

Anything else stays in the normal Agent transcript and does not become a
``done`` stage.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novelvideo.chat.project_stage_skill_routing import (
    ProjectStageRoute,
    select_project_stage_route,
)
from novelvideo.chat.workflow_stage_receipts import project_workflow_stage_receipts
from novelvideo.workflow_runtime.project_work_ledger import (
    DEFAULT_WORKFLOW_ID,
    build_project_work_ledger,
    deprecate_artifact,
    load_project_work_ledger,
    next_stage,
    record_artifact,
    record_failure,
    render_ledger_briefing,
    save_project_work_ledger,
    set_stage_status,
    validate_project_work_ledger,
)


logger = logging.getLogger(__name__)

_PLANNING_STEPS = frozenset({"understand", "production_plan"})
_MAX_WORKFLOW_DEPTH = 7


def _text(value: object, *, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _find_workflow_run(value: object) -> Mapping[str, Any]:
    queue: deque[tuple[object, int]] = deque([(value, 0)])
    processed = 0
    while queue and processed < 512:
        current, depth = queue.popleft()
        processed += 1
        payload = _mapping(current)
        if not payload:
            continue
        run_id = _text(
            payload.get("run_id")
            or payload.get("workflow_run_id")
            or payload.get("id")
        )
        if run_id and (
            isinstance(payload.get("step_states"), Mapping)
            or isinstance(payload.get("artifacts"), Mapping)
        ):
            return payload
        if depth >= _MAX_WORKFLOW_DEPTH:
            continue
        for key in (
            "data",
            "result",
            "workflow_run",
            "workflowRun",
            "run",
            "receipt",
            "workflow_receipt",
            "canvas_receipt",
        ):
            child = payload.get(key)
            if isinstance(child, (Mapping, str)):
                queue.append((child, depth + 1))
        for child in payload.values():
            if isinstance(child, (list, tuple)):
                queue.extend((item, depth + 1) for item in child[:64])
    return {}


def _step_id_from_run(run: Mapping[str, Any], stage_ids: Sequence[str]) -> str:
    frontier = run.get("current_frontier")
    if isinstance(frontier, (list, tuple)):
        for item in frontier:
            step_id = _text(item, limit=120)
            if step_id in stage_ids:
                return step_id
    return ""


def _string_list(value: object, *, limit: int = 200) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item, limit=240)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _canvas_node_ids(payload: Mapping[str, Any]) -> list[str]:
    candidates: list[object] = [payload]
    for key in ("data", "result", "canvas_receipt", "receipt"):
        child = payload.get(key)
        if isinstance(child, Mapping):
            candidates.append(child)
    node_ids: list[str] = []
    for candidate in candidates:
        raw = candidate.get("created_node_ids") or candidate.get("affected_node_ids")
        for node_id in _string_list(raw):
            if node_id not in node_ids:
                node_ids.append(node_id)
    return node_ids


def _canvas_write_verified(payload: Mapping[str, Any]) -> bool:
    candidates: list[Mapping[str, Any]] = [payload]
    for key in ("data", "result", "canvas_receipt", "receipt"):
        child = payload.get(key)
        if isinstance(child, Mapping):
            candidates.append(child)
    for candidate in candidates:
        if candidate.get("server_applied") is not True:
            continue
        if candidate.get("readback_verified", True) is not True:
            continue
        revision = candidate.get("revision")
        applied_ops = candidate.get("applied_ops")
        if (
            isinstance(revision, int)
            and not isinstance(revision, bool)
            and revision > 0
            and isinstance(applied_ops, int)
            and not isinstance(applied_ops, bool)
            and applied_ops > 0
        ):
            return True
    return False


def _result_failed(payload: Mapping[str, Any]) -> bool:
    if payload.get("ok") is False or payload.get("success") is False:
        return True
    return bool(
        _text(payload.get("error_code"), limit=160)
        or _text(payload.get("error"), limit=600)
    )


def _existing_failure(
    stage: Mapping[str, Any],
    *,
    tool: str,
    error_code: str,
    attempt: int,
) -> bool:
    for item in stage.get("failures") or []:
        if not isinstance(item, Mapping):
            continue
        if (
            _text(item.get("tool"), limit=160) == tool
            and _text(item.get("error_code"), limit=160) == error_code
            and int(item.get("attempt") or 0) == int(attempt or 0)
        ):
            return True
    return False


@dataclass(slots=True)
class ProjectWorkLedgerRuntime:
    """Mutable turn-scoped view of one on-disk project ledger."""

    state_dir: Path
    ledger: dict[str, Any]
    loaded_from_disk: bool = False
    dirty: bool = False
    last_error: str = ""

    @classmethod
    def open(
        cls,
        *,
        state_dir: str | Path | None,
        project_id: str,
        canvas_id: str = "",
        goal: str,
        workflow_id: str = DEFAULT_WORKFLOW_ID,
    ) -> ProjectWorkLedgerRuntime | None:
        if not state_dir or not _text(project_id):
            return None
        directory = Path(state_dir)
        try:
            existing = load_project_work_ledger(directory)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            # A corrupted or hand-edited file is evidence, not something to
            # silently overwrite.  The normal turn continues without ledger
            # memory rather than losing the invalid on-disk record.
            logger.warning(
                "project work ledger could not be loaded state_dir=%s error=%s",
                directory,
                exc,
            )
            return None
        if existing is not None:
            return cls(
                state_dir=directory,
                ledger=existing,
                loaded_from_disk=True,
            )
        ledger = build_project_work_ledger(
            project_id=project_id,
            canvas_id=canvas_id,
            goal=goal,
            workflow_id=workflow_id,
        )
        return cls(state_dir=directory, ledger=ledger)

    @property
    def revision(self) -> str:
        return _text(self.ledger.get("ledger_revision"), limit=120)

    @property
    def next_stage(self) -> str:
        return next_stage(self.ledger)

    def project_stage_route(self, prompt: object) -> ProjectStageRoute | None:
        return select_project_stage_route(self.ledger, prompt)

    @property
    def prompt_block(self) -> str:
        if not self.loaded_from_disk:
            return ""
        return "[PROJECT_WORK_LEDGER]\n" + render_ledger_briefing(self.ledger)

    def receipt(self) -> dict[str, Any]:
        return {
            "schema": _text(self.ledger.get("schema"), limit=120),
            "revision": self.revision,
            "workflow_id": _text(self.ledger.get("workflow_id"), limit=120),
            "next_stage": self.next_stage,
            "updated": bool(self.dirty),
        }

    def flush(self) -> bool:
        if not self.dirty:
            return False
        save_project_work_ledger(self.state_dir, self.ledger)
        self.dirty = False
        self.loaded_from_disk = True
        return True

    def _mark(self, ledger: Mapping[str, Any]) -> bool:
        candidate = validate_project_work_ledger(ledger)
        if candidate == self.ledger:
            return False
        self.ledger = candidate
        self.dirty = True
        return True

    def _record_failure(
        self,
        *,
        stage_id: str,
        tool: str,
        error_code: str,
        disposition: str = "",
        attempt: int = 0,
    ) -> bool:
        if not stage_id:
            return False
        stage = next(
            (
                item
                for item in self.ledger.get("stages") or []
                if _text(item.get("step_id"), limit=120) == stage_id
            ),
            None,
        )
        if not isinstance(stage, Mapping):
            return False
        clean_attempt = max(0, int(attempt or 0)) if str(attempt or "").strip() else 0
        if _existing_failure(
            stage,
            tool=tool,
            error_code=error_code,
            attempt=clean_attempt,
        ):
            return False
        return self._mark(
            record_failure(
                self.ledger,
                step_id=stage_id,
                tool=tool,
                error_code=error_code,
                disposition=disposition,
                attempt=clean_attempt,
            )
        )

    def _record_receipt_artifacts(
        self,
        *,
        run_id: str,
        payload: Mapping[str, Any],
    ) -> bool:
        receipts = project_workflow_stage_receipts(payload)
        changed = False
        for receipt in receipts:
            step_id = _text(receipt.get("step_id"), limit=120)
            kind = _text(receipt.get("artifact_kind"), limit=120)
            digest = _text(receipt.get("receipt_sha256"), limit=120)
            if not step_id or not kind or not digest:
                continue
            node_key = f"workflow:{run_id}:{kind}:{digest[:20]}"
            status = _text(receipt.get("status"), limit=40).casefold()
            verified = status == "verified"
            changed = (
                self._mark(
                    record_artifact(
                        self.ledger,
                        step_id=step_id,
                        kind="run",
                        node_key=node_key,
                        verified=verified,
                        note=f"workflow stage receipt status={status or 'unknown'}",
                    )
                )
                or changed
            )
            stage = next(
                (
                    item
                    for item in self.ledger.get("stages") or []
                    if _text(item.get("step_id"), limit=120) == step_id
                ),
                None,
            )
            if isinstance(stage, Mapping):
                for old in stage.get("artifacts") or []:
                    if not isinstance(old, Mapping):
                        continue
                    old_key = _text(old.get("node_key"), limit=240)
                    if (
                        old_key.startswith(f"workflow:{run_id}:{kind}:")
                        and old_key != node_key
                        and old.get("deprecated") is not True
                    ):
                        changed = (
                            self._mark(
                                deprecate_artifact(
                                    self.ledger,
                                    node_key=old_key,
                                    reason=f"同一阶段产生了更新回执：{node_key}",
                                )
                            )
                            or changed
                        )
            if verified:
                changed = (
                    self._mark(
                        set_stage_status(
                            self.ledger,
                            step_id=step_id,
                            status="done",
                        )
                    )
                    or changed
                )
            elif status == "blocked":
                changed = (
                    self._mark(
                        set_stage_status(
                            self.ledger,
                            step_id=step_id,
                            status="blocked",
                        )
                    )
                    or changed
                )
        return changed

    def _record_run_steps(
        self,
        *,
        run: Mapping[str, Any],
    ) -> bool:
        run_id = _text(
            run.get("run_id") or run.get("workflow_run_id") or run.get("id")
        )
        step_states = run.get("step_states")
        if not run_id or not isinstance(step_states, Mapping):
            return False
        changed = False
        for stage in self.ledger.get("stages") or []:
            if not isinstance(stage, Mapping):
                continue
            step_id = _text(stage.get("step_id"), limit=120)
            state = step_states.get(step_id)
            if not isinstance(state, Mapping):
                continue
            status = _text(state.get("status"), limit=40).casefold()
            if status == "completed":
                if step_id in _PLANNING_STEPS:
                    changed = (
                        self._mark(
                            record_artifact(
                                self.ledger,
                                step_id=step_id,
                                kind="run",
                                node_key=f"workflow:{run_id}:{step_id}:completed",
                                verified=True,
                                note="WorkflowRun step completed",
                            )
                        )
                        or changed
                    )
                    changed = (
                        self._mark(
                            set_stage_status(
                                self.ledger,
                                step_id=step_id,
                                status="done",
                            )
                        )
                        or changed
                    )
                elif stage.get("status") != "done":
                    changed = (
                        self._mark(
                            set_stage_status(
                                self.ledger,
                                step_id=step_id,
                                status="in_progress",
                            )
                        )
                        or changed
                    )
                continue
            if status in {"running", "pending_dispatch", "monitoring"}:
                if stage.get("status") != "done":
                    changed = (
                        self._mark(
                            set_stage_status(
                                self.ledger,
                                step_id=step_id,
                                status="in_progress",
                            )
                        )
                        or changed
                    )
                continue
            if status in {"failed", "recoverable_error"}:
                error_code = _text(
                    state.get("error_code")
                    or state.get("error")
                    or f"workflow_step_{status}",
                    limit=160,
                )
                changed = (
                    self._record_failure(
                        stage_id=step_id,
                        tool=f"workflow_run:{run_id}",
                        error_code=error_code,
                        disposition=_text(state.get("disposition"), limit=40),
                        attempt=int(state.get("attempt") or 0),
                    )
                    or changed
                )
                changed = (
                    self._mark(
                        set_stage_status(
                            self.ledger,
                            step_id=step_id,
                            status="blocked",
                        )
                    )
                    or changed
                )
        return changed

    def observe_tool_result(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        result: object,
    ) -> bool:
        """Project one tool result; return whether the ledger changed."""

        payload = _mapping(result)
        if not payload:
            return False
        run = _find_workflow_run(payload)
        stage_ids = [str(item.get("step_id") or "") for item in self.ledger["stages"]]
        current_step = _step_id_from_run(run, stage_ids) or self.next_stage
        changed = False
        if run:
            run_id = _text(
                run.get("run_id") or run.get("workflow_run_id") or run.get("id")
            )
            changed = self._record_receipt_artifacts(
                run_id=run_id,
                payload=payload,
            ) or changed
            changed = self._record_run_steps(run=run) or changed

        node_ids = _canvas_node_ids(payload)
        if node_ids and current_step and _canvas_write_verified(payload):
            for node_id in node_ids:
                changed = (
                    self._mark(
                        record_artifact(
                            self.ledger,
                            step_id=current_step,
                            kind="file",
                            node_key=f"canvas:{node_id}",
                            verified=True,
                            note="服务端已确认画布写入",
                        )
                    )
                    or changed
                )

        if _result_failed(payload):
            error_code = _text(
                payload.get("error_code") or payload.get("error") or "tool_failed",
                limit=160,
            )
            disposition = _text(
                payload.get("disposition")
                if not isinstance(payload.get("disposition"), Mapping)
                else payload["disposition"].get("action")
                if isinstance(payload.get("disposition"), Mapping)
                else "",
                limit=40,
            )
            changed = (
                self._record_failure(
                    stage_id=current_step,
                    tool=_text(tool_name, limit=160),
                    error_code=error_code,
                    disposition=disposition,
                    attempt=0,
                )
                or changed
            )

        if changed:
            self.flush()
        return changed


__all__ = ["ProjectWorkLedgerRuntime"]
