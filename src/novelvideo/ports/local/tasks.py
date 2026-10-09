"""Local CE task and cancellation port implementations."""

from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any

from novelvideo.ports import get_cancellation_store
from novelvideo.ports.local.durable_queue import DurableQueueStore
from novelvideo.ports.tasks import QueuedTask, cancel_key, display_metadata_for_task
from novelvideo.project_context import ProjectContext, require_project_home_node
from novelvideo.task_backend.limits import (
    GlobalLaneQueueLimitExceeded,
    global_lane_concurrency,
    global_lane_queue_limit,
    project_lane_effective_active_limit,
)
from novelvideo.task_backend.queues import QUEUE_KINDS, normalize_queue_kind
from novelvideo.task_backend.receipts import (
    build_task_acceptance_receipt,
    project_task_acceptance_receipt,
)
from novelvideo.task_backend.run_core import run_project_task_core_sync
from novelvideo.task_backend.subprocesses import kill_task_processes
from novelvideo.task_state import (
    ACTIVE_PROJECT_TASK_STATUSES,
    INTERRUPTED_INLINE_TASK_ERROR,
    TERMINAL_TASK_STATUSES,
    get_task_manager,
)

logger = logging.getLogger(__name__)

_INLINE_TASK_ERROR_LIMIT = 1000
_FULL_TRACEBACK_ENV = "VILLAGE_CANVAS_INLINE_TASK_FULL_TRACEBACK"
_DOWNLOAD_RECOVERY_MAX_ATTEMPTS = 2
_DOWNLOAD_RECOVERY_DELAY_S = 5.0


def _compact_exception_message(
    exc: BaseException,
    *,
    limit: int = _INLINE_TASK_ERROR_LIMIT,
) -> str:
    """Return one bounded log line while task state keeps the detailed error."""
    text = " ".join(str(exc).split()) or "<no error message>"
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _recoverable_provider_task_id(task_state: Any) -> str:
    """Return a provider id only for Freezone NewAPI jobs safe to resume."""
    if str(getattr(task_state, "task_type", "")) != "freezone_video_gen":
        return ""
    metadata = getattr(task_state, "metadata", None)
    if not isinstance(metadata, dict):
        return ""
    if str(metadata.get("provider") or "").strip() != "newapi":
        return ""
    return str(
        metadata.get("provider_task_id") or metadata.get("newapi_task_id") or ""
    ).strip()


def _is_pending_video_download_recovery(task_state: Any) -> bool:
    if str(getattr(task_state, "status", "")) != "waiting":
        return False
    metadata = getattr(task_state, "metadata", None)
    return (
        _recoverable_provider_task_id(task_state) != ""
        and isinstance(metadata, dict)
        and str(metadata.get("error_code") or "")
        == "provider_result_download_pending"
    )


def _inline_task_full_traceback_enabled() -> bool:
    return os.environ.get(_FULL_TRACEBACK_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _log_inline_task_failure(lane_name: str, exc: BaseException) -> None:
    logger.error(
        "Inline project task background runner failed: "
        "lane=%s error_type=%s error=%s",
        lane_name,
        type(exc).__name__,
        _compact_exception_message(exc),
        exc_info=(type(exc), exc, exc.__traceback__)
        if _inline_task_full_traceback_enabled()
        else None,
    )


@dataclass
class _InlineLaneJob:
    envelope: dict[str, Any]
    ctx: Any
    manager: Any
    run_task_id: str
    metadata: dict[str, Any]

    @property
    def project_id(self) -> str:
        return str(self.envelope.get("project_id") or "")


@dataclass
class _InlineLane:
    name: str
    concurrency: int
    queue_limit: int
    executor: ThreadPoolExecutor
    queued: deque[_InlineLaneJob] = field(default_factory=deque)
    active: int = 0
    last_started_project_id: str | None = None


class InlineTaskBackend:
    def __init__(self) -> None:
        self._background_tasks: set[asyncio.Task] = set()
        self._lanes: dict[str, _InlineLane] = {
            lane: _InlineLane(
                name=lane,
                concurrency=global_lane_concurrency(lane),
                queue_limit=global_lane_queue_limit(lane),
                executor=ThreadPoolExecutor(
                    max_workers=global_lane_concurrency(lane),
                    thread_name_prefix=f"inline-{lane}",
                ),
            )
            for lane in sorted(QUEUE_KINDS)
        }

    def _before_submit(self, job: _InlineLaneJob) -> None:
        return None

    def close(self) -> None:
        """Shut down every lane executor so its worker threads do not outlive us.

        Lanes are created once per process; without this the thread pool (and
        anything queued behind it) is never reclaimed. ``wait=False`` keeps
        shutdown non-blocking and ``cancel_futures=False`` lets already-queued
        jobs run to completion instead of being silently dropped.
        """
        for lane in self._lanes.values():
            lane.executor.shutdown(wait=False, cancel_futures=False)

    shutdown = close

    def _after_submit_failure(self, job: _InlineLaneJob) -> None:
        return None

    def _before_run(self, job: _InlineLaneJob) -> None:
        return None

    def _after_run(self, job: _InlineLaneJob) -> None:
        return None

    def _after_cancel(self, run_task_id: str) -> None:
        return None

    async def enqueue_project_task(
        self,
        ctx,
        *,
        task_type: str,
        queue_kind: str = "default",
        episode: int = 0,
        beat_num: int | None = None,
        scope: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> QueuedTask:
        require_project_home_node(ctx, operation="enqueue project task")
        manager = get_task_manager()
        payload = payload or {}
        lane_name = normalize_queue_kind(queue_kind)
        metadata = {
            "backend": "inline",
            "queue_kind": lane_name,
            "project_id": ctx.project_id,
            **display_metadata_for_task(task_type, payload),
        }
        project_lane_limit = project_lane_effective_active_limit(
            lane_name,
            eligible_user_count=1,
        )
        state, reserved = manager.reserve_task_for_project(
            ctx,
            task_type,
            episode,
            beat_num=beat_num,
            scope=scope,
            metadata=metadata,
            queue_kind=lane_name,
            project_lane_limit=project_lane_limit,
        )
        # Reservation creates the one acceptance fact shared by API, task
        # center, WorkflowRun and Agent.  It is persisted with task metadata
        # before the worker can report progress or completion.
        acceptance_receipt = build_task_acceptance_receipt(
            task_id=state.task_id,
            task_type=task_type,
            project_id=ctx.project_id,
            episode=episode,
            beat_num=beat_num,
            scope=scope,
            queue_kind=lane_name,
            backend="inline",
            status="accepted",
            accepted_at=getattr(state, "created_at", ""),
            trace_id=(
                payload.get("trace_id")
                or payload.get("request_id")
                or payload.get("idempotency_key")
            ),
            run_id=payload.get("run_id") or payload.get("workflow_run_id"),
            command_id=payload.get("command_id"),
            source_turn_id=payload.get("source_turn_id"),
        )
        metadata["task_acceptance_receipt"] = acceptance_receipt
        if not reserved and state.status in ACTIVE_PROJECT_TASK_STATUSES:
            existing_metadata = dict(state.metadata or {})
            if not project_task_acceptance_receipt(
                existing_metadata.get("task_acceptance_receipt")
            ):
                # Backfill tasks created before the receipt contract without
                # changing their lifecycle state or creating a second task.
                existing_metadata["task_acceptance_receipt"] = acceptance_receipt
                manager.update_progress_for_project(
                    ctx,
                    task_type,
                    episode,
                    beat_num=beat_num,
                    scope=scope,
                    progress=state.progress,
                    current_task=state.current_task,
                    metadata=existing_metadata,
                    status=state.status,
                    expected_task_id=state.task_id,
                )
                state.metadata = existing_metadata
            return QueuedTask(
                task_state=state,
                backend=str(existing_metadata.get("backend") or "inline"),
                queue=None,
                celery_id=None,
            )

        manager.update_progress_for_project(
            ctx,
            task_type,
            episode,
            beat_num=beat_num,
            scope=scope,
            progress=0.0,
            current_task="任务已进入队列",
            metadata=metadata,
            status="queued",
            expected_task_id=state.task_id,
        )
        # The reservation snapshot predates the persisted receipt.  Return the
        # same metadata the worker and task center will read back.
        state.metadata = metadata
        envelope = {
            "project_id": ctx.project_id,
            "requester_user_id": ctx.requester_user_id,
            "task_type": task_type,
            "episode": episode,
            "beat_num": beat_num,
            "scope": scope,
            "queue_kind": lane_name,
            "payload": payload,
        }
        job = _InlineLaneJob(
            envelope=envelope,
            ctx=ctx,
            manager=manager,
            run_task_id=state.task_id,
            metadata=metadata,
        )
        try:
            self._before_submit(job)
            self._submit_lane_job(job)
        except Exception as exc:
            try:
                self._after_submit_failure(job)
            except Exception as cleanup_exc:
                logger.error(
                    "Inline task submission cleanup failed: task_id=%s error=%s",
                    state.task_id,
                    _compact_exception_message(cleanup_exc),
                )
            if not isinstance(exc, GlobalLaneQueueLimitExceeded):
                manager.fail_task_for_project(
                    ctx,
                    task_type,
                    episode,
                    beat_num=beat_num,
                    scope=scope,
                    error=f"Task enqueue failed: {exc}",
                    metadata=metadata,
                    expected_task_id=state.task_id,
                )
            raise
        return QueuedTask(task_state=state, backend="inline")

    def lane_snapshot(self) -> dict[str, dict[str, int]]:
        return {
            name: {
                "active": lane.active,
                "queued": len(lane.queued),
                "concurrency": lane.concurrency,
            }
            for name, lane in sorted(self._lanes.items())
        }

    def _submit_lane_job(self, job: _InlineLaneJob) -> None:
        lane = self._lanes[normalize_queue_kind(job.envelope.get("queue_kind"))]
        if lane.active < lane.concurrency:
            self._start_lane_job(lane, job)
            return
        if len(lane.queued) >= lane.queue_limit:
            job.manager.fail_task_for_project(
                job.ctx,
                str(job.envelope["task_type"]),
                int(job.envelope.get("episode") or 0),
                beat_num=job.envelope.get("beat_num"),
                scope=job.envelope.get("scope"),
                error=f"{lane.name} lane queue is full",
                metadata=job.metadata,
                expected_task_id=job.run_task_id,
            )
            raise GlobalLaneQueueLimitExceeded(
                project_id=job.project_id,
                queue_kind=lane.name,
                limit=lane.queue_limit,
                queued=len(lane.queued),
            )
        lane.queued.append(job)

    def _start_lane_job(self, lane: _InlineLane, job: _InlineLaneJob) -> None:
        lane.active += 1
        lane.last_started_project_id = job.project_id
        task = asyncio.create_task(self._run_inline(lane, job))
        self._background_tasks.add(task)
        task.add_done_callback(
            lambda done, lane_name=lane.name: self._on_background_task_done(done, lane_name)
        )

    def _pop_next_lane_job(self, lane: _InlineLane) -> _InlineLaneJob | None:
        if not lane.queued:
            return None
        if len(lane.queued) == 1:
            return lane.queued.popleft()
        for index, job in enumerate(lane.queued):
            if job.project_id != lane.last_started_project_id:
                del lane.queued[index]
                return job
        return lane.queued.popleft()

    def _drain_lane(self, lane_name: str) -> None:
        lane = self._lanes[lane_name]
        while lane.active < lane.concurrency:
            job = self._pop_next_lane_job(lane)
            if job is None:
                return
            self._start_lane_job(lane, job)

    def _remove_queued_task(self, task_id: str) -> bool:
        for lane in self._lanes.values():
            for index, job in enumerate(lane.queued):
                if job.run_task_id == task_id:
                    del lane.queued[index]
                    return True
        return False

    async def _run_inline(
        self,
        lane: _InlineLane,
        job: _InlineLaneJob,
    ) -> None:
        self._before_run(job)
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                lane.executor,
                partial(
                    run_project_task_core_sync,
                    job.envelope,
                    job.ctx,
                    job.manager,
                    run_task_id=job.run_task_id,
                    metadata=job.metadata,
                ),
            )
        except asyncio.CancelledError:
            # Process shutdown cancels the awaiting coroutine but does not stop
            # the executor thread. Keep the running journal row so the next
            # process can apply the explicit interrupted-running policy.
            raise
        except BaseException:
            self._after_run(job)
            raise
        else:
            self._after_run(job)

    def _on_background_task_done(self, task: asyncio.Task, lane_name: str) -> None:
        self._background_tasks.discard(task)
        lane = self._lanes[lane_name]
        lane.active = max(lane.active - 1, 0)
        if task.cancelled():
            self._drain_lane(lane_name)
            return
        try:
            exc = task.exception()
        except Exception as exc:
            _log_inline_task_failure(lane_name, exc)
        else:
            if exc is not None:
                _log_inline_task_failure(lane_name, exc)
        finally:
            self._drain_lane(lane_name)

    async def cancel_project_task(self, ctx, task_state) -> bool:
        await get_cancellation_store().request_cancel(
            project_id=ctx.project_id,
            task_type=task_state.task_type,
            episode=task_state.episode,
            task_id=task_state.task_id,
            beat_num=task_state.beat_num,
            scope=task_state.scope,
        )
        self._remove_queued_task(task_state.task_id)
        get_task_manager().update_progress_for_project(
            ctx,
            task_state.task_type,
            task_state.episode,
            beat_num=task_state.beat_num,
            scope=task_state.scope,
            progress=task_state.progress,
            current_task="任务已取消",
            status="cancelled",
            expected_task_id=task_state.task_id,
        )
        self._after_cancel(task_state.task_id)
        # taskkill/killpg 是阻塞调用(Windows 上可达秒级),不得占事件循环
        await asyncio.get_running_loop().run_in_executor(
            None, kill_task_processes, task_state.task_id
        )
        return True


def _project_context_from_durable_payload(payload: dict[str, Any]) -> ProjectContext:
    principals = tuple(
        (str(item[0]), str(item[1]))
        for item in payload.get("requester_principals") or []
        if isinstance(item, (list, tuple)) and len(item) >= 2
    )
    return ProjectContext(
        project_id=str(payload["project_id"]),
        project_name=str(payload["project_name"]),
        owner_type=str(payload["owner_type"]),
        owner_id=str(payload["owner_id"]),
        owner_username=str(payload["owner_username"]),
        requester_user_id=str(payload["requester_user_id"]),
        requester_username=str(payload["requester_username"]),
        requester_principals=principals,
        effective_role=str(payload["effective_role"]),
        home_node_id=str(payload["home_node_id"]),
        output_dir=Path(str(payload["output_dir"])),
        state_dir=Path(str(payload["state_dir"])),
        runtime_dir=Path(str(payload["runtime_dir"])),
        is_home_node=bool(payload["is_home_node"]),
    )


class DurableInlineTaskBackend(InlineTaskBackend):
    """CE inline scheduler with a process-restart-safe SQLite envelope journal."""

    def __init__(self, *, durable_store: DurableQueueStore | None = None) -> None:
        super().__init__()
        self._durable_store = durable_store or DurableQueueStore()
        self._scheduled_task_ids: set[str] = set()
        self._download_recovery_timers: dict[str, asyncio.TimerHandle] = {}

    def _before_submit(self, job: _InlineLaneJob) -> None:
        self._durable_store.put(run_task_id=job.run_task_id, job=job)
        self._scheduled_task_ids.add(job.run_task_id)

    def _after_submit_failure(self, job: _InlineLaneJob) -> None:
        self._scheduled_task_ids.discard(job.run_task_id)
        self._durable_store.remove(job.run_task_id)

    def _before_run(self, job: _InlineLaneJob) -> None:
        if not self._durable_store.mark_running(job.run_task_id):
            self._scheduled_task_ids.discard(job.run_task_id)
            job.manager.fail_task_for_project(
                job.ctx,
                str(job.envelope["task_type"]),
                int(job.envelope.get("episode") or 0),
                beat_num=job.envelope.get("beat_num"),
                scope=job.envelope.get("scope"),
                error="Durable task row missing before inline execution",
                metadata=job.metadata,
                expected_task_id=job.run_task_id,
            )
            raise RuntimeError(
                f"durable task row missing before execution: {job.run_task_id}"
            )

    def _after_run(self, job: _InlineLaneJob) -> None:
        self._scheduled_task_ids.discard(job.run_task_id)
        get_task = getattr(job.manager, "get_task_for_project", None)
        task_state = (
            get_task(
                job.ctx,
                str(job.envelope["task_type"]),
                int(job.envelope.get("episode") or 0),
                beat_num=job.envelope.get("beat_num"),
                scope=job.envelope.get("scope"),
            )
            if callable(get_task)
            else None
        )
        if _is_pending_video_download_recovery(task_state):
            self._schedule_download_recovery(job, task_state)
            return
        self._durable_store.remove(job.run_task_id)

    def _after_cancel(self, run_task_id: str) -> None:
        self._scheduled_task_ids.discard(str(run_task_id))
        timer = self._download_recovery_timers.pop(str(run_task_id), None)
        if timer is not None:
            timer.cancel()
        self._durable_store.remove(str(run_task_id))

    def _schedule_download_recovery(self, job: _InlineLaneJob, task_state: Any) -> None:
        """Queue at most two result-only recovery attempts with bounded backoff."""
        metadata = dict(getattr(task_state, "metadata", None) or {})
        try:
            prior_attempts = max(int(metadata.get("download_recovery_attempt") or 0), 0)
        except (TypeError, ValueError):
            prior_attempts = 0
        if prior_attempts >= _DOWNLOAD_RECOVERY_MAX_ATTEMPTS:
            job.manager.fail_task_for_project(
                job.ctx,
                str(job.envelope["task_type"]),
                int(job.envelope.get("episode") or 0),
                beat_num=job.envelope.get("beat_num"),
                scope=job.envelope.get("scope"),
                error=(
                    "上游视频已生成，但结果下载连续恢复失败；"
                    "未重新提交生成任务，可稍后手动恢复该已完成任务"
                ),
                logs=["自动下载恢复次数已用尽，停止重试"],
                metadata={
                    **metadata,
                    "provider_stage": "download_recovery_exhausted",
                },
                expected_task_id=job.run_task_id,
            )
            self._durable_store.remove(job.run_task_id)
            return

        attempt = prior_attempts + 1
        if not self._durable_store.requeue(job.run_task_id):
            job.manager.fail_task_for_project(
                job.ctx,
                str(job.envelope["task_type"]),
                int(job.envelope.get("episode") or 0),
                beat_num=job.envelope.get("beat_num"),
                scope=job.envelope.get("scope"),
                error="已完成视频的下载恢复队列写入失败",
                metadata=metadata,
                expected_task_id=job.run_task_id,
            )
            self._durable_store.remove(job.run_task_id)
            return

        delay_s = _DOWNLOAD_RECOVERY_DELAY_S * attempt
        job.manager.update_progress_for_project(
            job.ctx,
            str(job.envelope["task_type"]),
            int(job.envelope.get("episode") or 0),
            beat_num=job.envelope.get("beat_num"),
            scope=job.envelope.get("scope"),
            progress=0.95,
            current_task=f"上游视频已完成，{delay_s:.0f} 秒后自动恢复下载（{attempt}/{_DOWNLOAD_RECOVERY_MAX_ATTEMPTS}）",
            logs=["仅恢复已完成视频的下载，不会重新提交生成"],
            metadata={
                **metadata,
                "download_recovery_attempt": attempt,
                "provider_stage": "download_recovery_scheduled",
            },
            status="waiting",
            expected_task_id=job.run_task_id,
        )
        self._scheduled_task_ids.add(job.run_task_id)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Keep the durable row queued; startup recovery will resume it.
            return
        previous = self._download_recovery_timers.pop(job.run_task_id, None)
        if previous is not None:
            previous.cancel()
        self._download_recovery_timers[job.run_task_id] = loop.call_later(
            delay_s,
            self._dispatch_download_recovery,
            job,
        )

    def _dispatch_download_recovery(self, job: _InlineLaneJob) -> None:
        self._download_recovery_timers.pop(job.run_task_id, None)
        try:
            self._submit_lane_job(job)
        except Exception as exc:
            self._scheduled_task_ids.discard(job.run_task_id)
            job.manager.fail_task_for_project(
                job.ctx,
                str(job.envelope["task_type"]),
                int(job.envelope.get("episode") or 0),
                beat_num=job.envelope.get("beat_num"),
                scope=job.envelope.get("scope"),
                error=f"已完成视频的下载恢复排队失败: {_compact_exception_message(exc)}",
                metadata=job.metadata,
                expected_task_id=job.run_task_id,
            )
            self._durable_store.remove(job.run_task_id)

    async def recover_pending_tasks(self) -> int:
        """Recover queued envelopes and fail interrupted running work safely."""
        manager = get_task_manager()
        recovered = 0
        for row in self._durable_store.pending():
            run_task_id = str(row["run_task_id"])
            if run_task_id in self._scheduled_task_ids:
                continue
            try:
                ctx = _project_context_from_durable_payload(row["context"])
                envelope = dict(row["envelope"])
                metadata = {
                    **dict(row.get("metadata") or {}),
                    "backend": "inline",
                    "durable_recovery": True,
                }
                task_type = str(envelope["task_type"])
                episode = int(envelope.get("episode") or 0)
                beat_num = envelope.get("beat_num")
                scope = envelope.get("scope")
                task_state = manager.get_task_for_project(
                    ctx,
                    task_type,
                    episode,
                    beat_num=beat_num,
                    scope=scope,
                )
                if task_state is None or task_state.task_id != run_task_id:
                    self._durable_store.remove(run_task_id)
                    continue
                swept_restart_failure = (
                    task_state.status == "failed"
                    and task_state.error == INTERRUPTED_INLINE_TASK_ERROR
                )
                if (
                    task_state.status in TERMINAL_TASK_STATUSES
                    and not swept_restart_failure
                ):
                    self._durable_store.remove(run_task_id)
                    continue
                provider_task_id = _recoverable_provider_task_id(task_state)
                if str(row["status"]) == "running":
                    # Requeue only the provider-poll/download leg for an
                    # already-submitted Freezone NewAPI task.  All other
                    # running work remains terminal after restart so its
                    # original side effects cannot be repeated.
                    if provider_task_id and swept_restart_failure:
                        metadata["recovery_provider_task_id"] = provider_task_id
                    else:
                        manager.fail_task_for_project(
                            ctx,
                            task_type,
                            episode,
                            beat_num=beat_num,
                            scope=scope,
                            error=(
                                "服务重启时任务仍在运行中，为避免重复计费或重复写入，"
                                "本次运行已终结，可从任务中心重新发起"
                            ),
                            current_task="运行中任务已安全终结",
                            metadata=metadata,
                            expected_task_id=run_task_id,
                        )
                        self._durable_store.remove(run_task_id)
                        continue
                claimed = self._durable_store.claim_recovery(
                    run_task_id,
                    expected_status=str(row["status"]),
                    expected_updated_at=str(row["updated_at"]),
                )
                if not claimed:
                    continue
                if not manager.requeue_interrupted_task_for_project(
                    ctx,
                    task_type,
                    episode,
                    beat_num=beat_num,
                    scope=scope,
                    metadata=metadata,
                    expected_task_id=run_task_id,
                ):
                    self._durable_store.remove(run_task_id)
                    continue
                job = _InlineLaneJob(
                    envelope=envelope,
                    ctx=ctx,
                    manager=manager,
                    run_task_id=run_task_id,
                    metadata=metadata,
                )
                self._scheduled_task_ids.add(run_task_id)
                try:
                    self._submit_lane_job(job)
                except Exception as exc:
                    try:
                        self._after_submit_failure(job)
                    except Exception as cleanup_exc:
                        logger.error(
                            "Recovered task submission cleanup failed: "
                            "task_id=%s error=%s",
                            run_task_id,
                            _compact_exception_message(cleanup_exc),
                        )
                    manager.fail_task_for_project(
                        ctx,
                        task_type,
                        episode,
                        beat_num=beat_num,
                        scope=scope,
                        error=f"Recovered task enqueue failed: {exc}",
                        current_task="恢复任务重新入队失败",
                        metadata=metadata,
                        expected_task_id=run_task_id,
                    )
                    raise
                recovered += 1
            except Exception as exc:
                logger.error(
                    "Durable inline task recovery failed task_id=%s error=%s",
                    run_task_id,
                    _compact_exception_message(exc),
                )
        return recovered


class InMemoryCancellationStore:
    def __init__(self) -> None:
        self._keys: dict[str, float] = {}
        self._lock = threading.Lock()

    async def request_cancel(
        self,
        *,
        project_id: str,
        task_type: str,
        episode: int,
        task_id: str,
        beat_num: int | None = None,
        scope: str | None = None,
        ttl_seconds: int = 86_400,
    ) -> None:
        key = cancel_key(
            project_id=project_id,
            task_type=task_type,
            episode=episode,
            task_id=task_id,
            beat_num=beat_num,
            scope=scope,
        )
        with self._lock:
            self._keys[key] = time.time() + max(int(ttl_seconds), 0)

    async def is_cancel_requested(
        self,
        *,
        project_id: str,
        task_type: str,
        episode: int,
        task_id: str,
        beat_num: int | None = None,
        scope: str | None = None,
    ) -> bool:
        key = cancel_key(
            project_id=project_id,
            task_type=task_type,
            episode=episode,
            task_id=task_id,
            beat_num=beat_num,
            scope=scope,
        )
        with self._lock:
            expires_at = self._keys.get(key)
            if expires_at is None:
                return False
            if expires_at < time.time():
                self._keys.pop(key, None)
                return False
            return True
