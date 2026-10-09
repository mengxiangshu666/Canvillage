"""Project terminal WorkflowRun state back onto its bound assistant turn."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
import logging
from pathlib import Path
from typing import Any

from novelvideo.chat.store import ChatScope, chat_store
from novelvideo.workflow_runtime.release_readiness import (
    project_workflow_run_release_readiness,
)
from novelvideo.workflow_runtime.store import WorkflowRunStore

logger = logging.getLogger(__name__)

TERMINAL_WORKFLOW_STATUSES = frozenset({"completed", "failed", "cancelled"})
_IN_PROGRESS_MARKER = "任务已经进入后台执行，目前仍在进行中"
_STARTUP_RECEIPT_SCAN_LIMIT = 500
_WATCH_TASKS: dict[str, asyncio.Task[None]] = {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [clean for item in value if (clean := _text(item))]


def _release_readiness(run: Mapping[str, Any]) -> dict[str, Any]:
    provided = run.get("release_readiness")
    if isinstance(provided, Mapping):
        status = _text(provided.get("status")).casefold()
        if status in {"ready", "blocked", "unverified", "not_applicable"}:
            return dict(provided)
    projected = project_workflow_run_release_readiness(run)
    return dict(projected) if isinstance(projected, Mapping) else {}


def workflow_terminal_receipt(run: Mapping[str, Any]) -> dict[str, Any]:
    """Return the canonical delivery projection for one terminal Run."""

    run_id = _text(run.get("run_id") or run.get("id"))
    run_status = _text(run.get("status")).casefold()
    readiness = _release_readiness(run)
    release_status = _text(readiness.get("status")).casefold()
    release_required = bool(readiness.get("required")) or release_status in {
        "ready",
        "blocked",
        "unverified",
    }
    workflow_verified = run_status in {"completed", "succeeded", "success", "verified"}
    workflow_failed = run_status == "failed"
    release_blocked = (
        workflow_verified and release_required and release_status == "blocked"
    )
    release_unverified = (
        workflow_verified and release_required and release_status == "unverified"
    )
    delivery_success = bool(
        workflow_verified and not release_blocked and not release_unverified
    )
    if release_blocked or release_unverified:
        status = f"release_{release_status}"
    elif workflow_verified:
        status = "verified_success"
    elif workflow_failed:
        status = "verified_failure"
    elif run_status == "cancelled":
        status = "cancelled"
    else:
        return {}
    return {
        "schema": "workflow_terminal_turn_receipt.v1",
        "workflow_run_id": run_id,
        "workflow_status": run_status,
        "status": status,
        "workflow_verified": workflow_verified,
        "workflow_failed": workflow_failed,
        "delivery_success": delivery_success,
        "release_readiness": readiness,
        "release_required": release_required,
        "release_status": release_status,
        "release_ready": bool(release_required and release_status == "ready"),
        "error_code": _text(run.get("error_code")),
        "revision": int(run.get("revision") or 0),
        "event_seq": int(run.get("event_seq") or 0),
    }


def _terminal_text(receipt: Mapping[str, Any]) -> str:
    status = _text(receipt.get("status"))
    readiness = (
        receipt.get("release_readiness")
        if isinstance(receipt.get("release_readiness"), Mapping)
        else {}
    )
    failed = _string_list(readiness.get("failed_checks"))
    pending = [
        *_string_list(readiness.get("missing_checks")),
        *_string_list(readiness.get("not_run_checks")),
    ]
    if status == "verified_success":
        if receipt.get("release_ready"):
            return "成片已通过发布门，可以发布。"
        return "工作流已完成，最终产物与运行回执已核验。"
    if status == "release_blocked":
        detail = f"失败检查：{'、'.join(failed)}。" if failed else "发布门存在失败检查。"
        return (
            f"成片文件已经生成，但发布门未通过。{detail}"
            "当前结果不能标记为可交付或可发布；修复后必须重新执行终片 QC。"
        )
    if status == "release_unverified":
        detail = f"缺少或未测检查：{'、'.join(pending)}。" if pending else "发布门证据尚未补齐。"
        return (
            f"成片文件已经生成，但发布门尚未通过。{detail}"
            "当前只能确认合成任务已完成，不能标记为可发布。"
        )
    if status == "cancelled":
        return "WorkflowRun 已取消，当前不会继续执行；已完成产物和恢复点保持可查。"
    error_code = _text(receipt.get("error_code"))
    detail = f"错误代码：{error_code}。" if error_code else ""
    return f"WorkflowRun 执行失败，已保留恢复点。{detail}"


def terminal_turn_update(
    *,
    content: object,
    metadata: object,
    run: Mapping[str, Any],
) -> tuple[str, dict[str, Any], bool]:
    """Return idempotent (content, metadata, changed) for a bound assistant turn."""

    receipt = workflow_terminal_receipt(run)
    if not receipt:
        raise ValueError("workflow run is not terminal")
    current_content = str(content or "").strip()
    current_metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    existing = current_metadata.get("workflow_terminal_receipt")
    if (
        isinstance(existing, Mapping)
        and _text(existing.get("workflow_run_id")) == receipt["workflow_run_id"]
        and _text(existing.get("status")) == receipt["status"]
        and int(existing.get("revision") or 0) == receipt["revision"]
    ):
        return current_content, current_metadata, False

    lines = [
        line
        for line in current_content.splitlines()
        if _IN_PROGRESS_MARKER not in line
        and "我会沿着现有任务继续，不会把它误报为已经完成" not in line
    ]
    terminal_text = _terminal_text(receipt)
    next_content = "\n".join(lines).strip()
    if terminal_text not in next_content:
        next_content = "\n\n".join(
            part for part in (next_content, terminal_text) if part
        )
    delivery = (
        dict(current_metadata.get("delivery_verification"))
        if isinstance(current_metadata.get("delivery_verification"), Mapping)
        else {}
    )
    delivery.update(receipt)
    next_metadata = {
        **current_metadata,
        "delivery_verification": delivery,
        "workflow_terminal_receipt": receipt,
    }
    return next_content, next_metadata, True


def reconcile_workflow_turn_receipt(
    *,
    username: str,
    project_id: str,
    canvas_id: str,
    state_dir: str | Path | None = None,
    run: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Update the assistant turn bound to one terminal Run, if present."""

    if not isinstance(run, Mapping):
        return None
    receipt = workflow_terminal_receipt(run)
    run_id = _text(receipt.get("workflow_run_id"))
    if not run_id:
        return None
    scope = ChatScope(kind="project", id=project_id, canvas_id=canvas_id)
    if state_dir is not None:
        db_path = Path(state_dir) / "chat.db"
        message = chat_store.find_assistant_message_for_workflow_run_in_db(
            db_path,
            workflow_run_id=run_id,
        )
    else:
        message = chat_store.find_assistant_message_for_workflow_run(
            username,
            scope,
            workflow_run_id=run_id,
        )
    if message is None:
        return None
    content, metadata, changed = terminal_turn_update(
        content=message.get("content"),
        metadata=message.get("metadata"),
        run=run,
    )
    if not changed:
        return message
    if state_dir is not None:
        return chat_store.update_assistant_message_in_db(
            Path(state_dir) / "chat.db",
            message_id=int(message["id"]),
            content=content,
            metadata=metadata,
        )
    return chat_store.update_assistant_message(
        username,
        scope,
        message_id=int(message["id"]),
        content=content,
        metadata=metadata,
    )


async def reconcile_workflow_turn_receipt_async(
    *,
    username: str,
    project_id: str,
    canvas_id: str,
    state_dir: str | Path | None = None,
    run: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        reconcile_workflow_turn_receipt,
        username=username,
        project_id=project_id,
        canvas_id=canvas_id,
        state_dir=state_dir,
        run=run,
    )


async def recover_terminal_workflow_turn_receipts_for_projects(
    projects: Iterable[Any],
    *,
    limit_per_project: int = _STARTUP_RECEIPT_SCAN_LIMIT,
) -> dict[str, int]:
    """Project persisted terminal runs after a process restart.

    This recovery path is deliberately read-only for ``workflow_runs.db``.
    It only rewrites the assistant row already bound to a terminal run.
    """

    summary = {
        "projects_scanned": 0,
        "runs_scanned": 0,
        "messages_updated": 0,
        "errors": 0,
    }
    for project in projects:
        project_id = _text(getattr(project, "id", ""))
        state_dir = _text(getattr(project, "state_dir", ""))
        home_node_id = _text(getattr(project, "home_node_id", ""))
        if not project_id or not state_dir or home_node_id not in {"", "local"}:
            continue

        workflow_db_path = Path(state_dir) / "workflow_runs.db"
        chat_db_path = Path(state_dir) / "chat.db"
        if not workflow_db_path.is_file() or not chat_db_path.is_file():
            continue
        summary["projects_scanned"] += 1

        store = WorkflowRunStore(state_dir)
        try:
            runs = await store.list_terminal_runs(
                project_id=project_id,
                limit=limit_per_project,
            )
        except Exception:  # noqa: BLE001 - one corrupt project must not block startup
            summary["errors"] += 1
            logger.warning(
                "workflow terminal turn recovery scan failed project=%s",
                project_id,
                exc_info=True,
            )
            continue

        run_ids = {
            _text(run.get("id") or run.get("run_id"))
            for run in runs
            if _text(run.get("id") or run.get("run_id"))
        }
        try:
            messages_by_run = (
                chat_store.find_assistant_messages_for_workflow_runs_in_db(
                    chat_db_path,
                    workflow_run_ids=run_ids,
                )
            )
        except Exception:  # noqa: BLE001 - one corrupt chat db must not block startup
            summary["errors"] += 1
            logger.warning(
                "workflow terminal turn recovery chat scan failed project=%s",
                project_id,
                exc_info=True,
            )
            continue

        for run in runs:
            summary["runs_scanned"] += 1
            canvas_id = _text(run.get("canvas_id"))
            if not canvas_id:
                continue
            try:
                message = messages_by_run.get(
                    _text(run.get("id") or run.get("run_id"))
                )
                if message is None:
                    continue
                content, metadata, changed = terminal_turn_update(
                    content=message.get("content"),
                    metadata=message.get("metadata"),
                    run=run,
                )
                if not changed:
                    continue
                updated = chat_store.update_assistant_message_in_db(
                    chat_db_path,
                    message_id=int(message["id"]),
                    content=content,
                    metadata=metadata,
                )
                if updated is not None:
                    summary["messages_updated"] += 1
            except Exception:  # noqa: BLE001 - one bad row must not block startup
                summary["errors"] += 1
                logger.warning(
                    "workflow terminal turn recovery failed project=%s run=%s",
                    project_id,
                    _text(run.get("id") or run.get("run_id")),
                    exc_info=True,
                )
    return summary


def schedule_workflow_turn_receipt_watch(
    *,
    username: str,
    project_id: str,
    canvas_id: str,
    state_dir: str | Path,
    run_id: str,
) -> asyncio.Task[None] | None:
    """Keep a live turn convergent after the chat stream has closed."""

    normalized_run_id = _text(run_id)
    if not username or not project_id or not normalized_run_id:
        return None
    store = WorkflowRunStore(state_dir)
    key = (
        f"{store.db_path.resolve()}::{username}::{project_id}::"
        f"{canvas_id}::{normalized_run_id}"
    )
    existing = _WATCH_TASKS.get(key)
    if existing is not None and not existing.done():
        return existing

    async def watch() -> None:
        while True:
            run = await store.get(normalized_run_id)
            if run is None:
                return
            if _text(run.get("status")).casefold() in TERMINAL_WORKFLOW_STATUSES:
                await reconcile_workflow_turn_receipt_async(
                    username=username,
                    project_id=project_id,
                    canvas_id=canvas_id,
                    state_dir=state_dir,
                    run=run,
                )
                return
            signal = store.event_signal_version(normalized_run_id)
            await store.wait_for_event_signal(
                normalized_run_id,
                signal,
                timeout=15.0,
            )

    task = asyncio.create_task(watch())
    _WATCH_TASKS[key] = task

    def cleanup(completed: asyncio.Task[None]) -> None:
        if _WATCH_TASKS.get(key) is completed:
            _WATCH_TASKS.pop(key, None)
        try:
            completed.result()
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001 - terminal projection must not crash the app
            logger.exception(
                "workflow terminal turn projection failed user=%s project=%s run=%s",
                username,
                project_id,
                normalized_run_id,
            )

    task.add_done_callback(cleanup)
    return task


__all__ = [
    "TERMINAL_WORKFLOW_STATUSES",
    "reconcile_workflow_turn_receipt",
    "reconcile_workflow_turn_receipt_async",
    "recover_terminal_workflow_turn_receipts_for_projects",
    "schedule_workflow_turn_receipt_watch",
    "terminal_turn_update",
    "workflow_terminal_receipt",
]
