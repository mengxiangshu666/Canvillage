"""Durable local CE queue regressions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import threading
from types import SimpleNamespace

from novelvideo.ports.local.durable_queue import DurableQueueStore
from novelvideo.ports.local.lifecycle import LocalLifecycle
from novelvideo.ports.local.tasks import DurableInlineTaskBackend, _InlineLaneJob
from novelvideo.project_context import ProjectContext
from novelvideo.task_state import (
    INTERRUPTED_INLINE_TASK_ERROR,
    TaskStateManager,
)


def _ctx(root: Path, *, project_id: str = "project-1") -> ProjectContext:
    return ProjectContext(
        project_id=project_id,
        project_name="demo",
        owner_type="user",
        owner_id="user-1",
        owner_username="local",
        requester_user_id="user-1",
        requester_username="local",
        requester_principals=(("user", "user-1"),),
        effective_role="admin",
        home_node_id="local",
        output_dir=root / "output",
        state_dir=root / "state",
        runtime_dir=root / "runtime",
        is_home_node=True,
    )


@dataclass
class _State:
    task_id: str
    task_type: str = "freezone_gen"
    episode: int = 0
    beat_num: int | None = None
    scope: str | None = None
    status: str = "queued"
    progress: float = 0.0
    error: str | None = None
    metadata: dict | None = None


def _job(root: Path, *, run_task_id: str = "run-1") -> _InlineLaneJob:
    ctx = _ctx(root)
    return _InlineLaneJob(
        envelope={
            "project_id": ctx.project_id,
            "requester_user_id": ctx.requester_user_id,
            "task_type": "freezone_gen",
            "queue_kind": "default",
            "episode": 0,
            "beat_num": None,
            "scope": "node-1",
            "payload": {"task_family": "freezone_canvas", "node_id": "node-1"},
        },
        ctx=ctx,
        manager=object(),
        run_task_id=run_task_id,
        metadata={"backend": "inline", "task_family": "freezone_canvas"},
    )


def test_store_roundtrip_uses_state_dir_default(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    store = DurableQueueStore()
    job = _job(tmp_path)

    assert store.put(run_task_id=job.run_task_id, job=job) is True
    assert store.put(run_task_id=job.run_task_id, job=job) is False

    rows = store.pending()
    assert store.db_path == tmp_path / "durable_project_tasks.sqlite3"
    assert len(rows) == 1
    assert rows[0]["run_task_id"] == "run-1"
    assert rows[0]["envelope"]["payload"]["node_id"] == "node-1"
    assert rows[0]["context"]["project_id"] == "project-1"
    assert rows[0]["context"]["output_dir"] == str(tmp_path / "output")
    assert rows[0]["metadata"]["backend"] == "inline"


def test_store_requeue_preserves_original_provider_envelope(tmp_path):
    store = DurableQueueStore(tmp_path / "queue.sqlite3")
    job = _job(tmp_path, run_task_id="run-download-recovery")

    assert store.put(run_task_id=job.run_task_id, job=job) is True
    assert store.mark_running(job.run_task_id) is True
    assert store.requeue(job.run_task_id) is True

    rows = store.pending()
    assert len(rows) == 1
    assert rows[0]["status"] == "queued"
    assert rows[0]["envelope"] == job.envelope


def test_enqueue_journals_before_lane_submission(monkeypatch, tmp_path):
    store = DurableQueueStore(tmp_path / "queue.sqlite3")
    backend = DurableInlineTaskBackend(durable_store=store)
    state = _State("run-enqueue")

    class Manager:
        def reserve_task_for_project(self, *_args, **_kwargs):
            return state, True

        def update_progress_for_project(self, *_args, **_kwargs):
            return None

        def fail_task_for_project(self, *_args, **_kwargs):
            state.status = "failed"

    submitted: list[_InlineLaneJob] = []

    def submit(job):
        assert store.count_pending() == 1
        assert store.pending()[0]["run_task_id"] == state.task_id
        submitted.append(job)

    monkeypatch.setattr("novelvideo.ports.local.tasks.get_task_manager", lambda: Manager())
    monkeypatch.setattr(backend, "_submit_lane_job", submit)

    queued = asyncio.run(
        backend.enqueue_project_task(
            _ctx(tmp_path),
            task_type="freezone_gen",
            scope="node-1",
            payload={"task_family": "freezone_canvas", "node_id": "node-1"},
        )
    )

    assert queued.task_state.task_id == "run-enqueue"
    assert queued.acceptance_receipt["schema"] == "task_acceptance_receipt.v1"
    assert queued.acceptance_receipt["task_id"] == "run-enqueue"
    assert queued.acceptance_receipt["task_type"] == "freezone_gen"
    assert queued.task_state.metadata["task_acceptance_receipt"] == (
        queued.acceptance_receipt
    )
    assert len(submitted) == 1


def test_repeated_video_enqueue_reuses_task_without_second_provider_submission(
    monkeypatch, tmp_path
):
    """The same task scope must not create a second paid-side-effect lane job."""

    store = DurableQueueStore(tmp_path / "queue.sqlite3")
    backend = DurableInlineTaskBackend(durable_store=store)
    state = _State("run-video-idempotent", task_type="freezone_video_gen", scope="node-1")
    reserve_calls = 0

    class Manager:
        def reserve_task_for_project(self, *_args, **_kwargs):
            nonlocal reserve_calls
            reserve_calls += 1
            return state, reserve_calls == 1

        def update_progress_for_project(self, *_args, **_kwargs):
            return None

    submitted: list[_InlineLaneJob] = []
    monkeypatch.setattr("novelvideo.ports.local.tasks.get_task_manager", lambda: Manager())
    monkeypatch.setattr(backend, "_submit_lane_job", submitted.append)

    first = asyncio.run(
        backend.enqueue_project_task(
            _ctx(tmp_path),
            task_type="freezone_video_gen",
            queue_kind="video",
            scope="node-1",
            payload={"job_id": "job-1", "node_id": "node-1"},
        )
    )
    second = asyncio.run(
        backend.enqueue_project_task(
            _ctx(tmp_path),
            task_type="freezone_video_gen",
            queue_kind="video",
            scope="node-1",
            payload={"job_id": "job-1", "node_id": "node-1"},
        )
    )

    assert first.task_state.task_id == second.task_state.task_id == "run-video-idempotent"
    assert first.task_state is second.task_state
    assert len(submitted) == 1
    assert store.count_pending() == 1


def test_queued_recovery_is_idempotent_after_task_state_restart_sweep(
    monkeypatch, tmp_path
):
    store = DurableQueueStore(tmp_path / "queue.sqlite3")
    stored_job = _job(tmp_path, run_task_id="run-recover")
    store.put(run_task_id=stored_job.run_task_id, job=stored_job)
    state = _State(
        "run-recover",
        scope="node-1",
        status="failed",
        error="服务重启,任务已中断,请重新发起",
        metadata={"backend": "inline"},
    )

    class Manager:
        def get_task_for_project(self, *_args, **_kwargs):
            return state

        def requeue_interrupted_task_for_project(self, *_args, **kwargs):
            assert kwargs["expected_task_id"] == state.task_id
            state.status = "queued"
            state.error = None
            return True

        def fail_task_for_project(self, *_args, **_kwargs):
            raise AssertionError("queued recovery must not fail the task")

    backend = DurableInlineTaskBackend(durable_store=store)
    submitted: list[_InlineLaneJob] = []
    monkeypatch.setattr("novelvideo.ports.local.tasks.get_task_manager", lambda: Manager())
    monkeypatch.setattr(backend, "_submit_lane_job", submitted.append)

    assert asyncio.run(backend.recover_pending_tasks()) == 1
    assert asyncio.run(backend.recover_pending_tasks()) == 0
    assert state.status == "queued"
    assert [job.run_task_id for job in submitted] == ["run-recover"]


def test_running_recovery_fails_safely_and_removes_durable_row(monkeypatch, tmp_path):
    store = DurableQueueStore(tmp_path / "queue.sqlite3")
    stored_job = _job(tmp_path, run_task_id="run-interrupted")
    store.put(run_task_id=stored_job.run_task_id, job=stored_job)
    assert store.mark_running(stored_job.run_task_id) is True
    state = _State(
        "run-interrupted",
        scope="node-1",
        status="failed",
        error="服务重启,任务已中断,请重新发起",
        metadata={"backend": "inline"},
    )

    class Manager:
        def get_task_for_project(self, *_args, **_kwargs):
            return state

        def fail_task_for_project(self, *_args, **kwargs):
            assert kwargs["expected_task_id"] == state.task_id
            state.status = "failed"
            state.error = kwargs["error"]

    backend = DurableInlineTaskBackend(durable_store=store)
    monkeypatch.setattr("novelvideo.ports.local.tasks.get_task_manager", lambda: Manager())
    monkeypatch.setattr(
        backend,
        "_submit_lane_job",
        lambda _job: (_ for _ in ()).throw(AssertionError("running task was re-run")),
    )

    assert asyncio.run(backend.recover_pending_tasks()) == 0
    assert "仍在运行中" in str(state.error)
    assert store.count_pending() == 0


def test_running_video_recovery_requeues_persisted_provider_task(monkeypatch, tmp_path):
    store = DurableQueueStore(tmp_path / "queue.sqlite3")
    stored_job = _job(tmp_path, run_task_id="run-video-recover")
    stored_job.envelope["task_type"] = "freezone_video_gen"
    stored_job.envelope["queue_kind"] = "video"
    stored_job.metadata = {"backend": "inline"}
    store.put(run_task_id=stored_job.run_task_id, job=stored_job)
    assert store.mark_running(stored_job.run_task_id) is True
    state = _State(
        "run-video-recover",
        task_type="freezone_video_gen",
        scope="node-1",
        status="failed",
        error=INTERRUPTED_INLINE_TASK_ERROR,
        metadata={
            "backend": "inline",
            "provider": "newapi",
            "provider_task_id": "provider-video-1",
        },
    )

    class Manager:
        def get_task_for_project(self, *_args, **_kwargs):
            return state

        def requeue_interrupted_task_for_project(self, *_args, **kwargs):
            assert kwargs["expected_task_id"] == state.task_id
            assert kwargs["metadata"]["recovery_provider_task_id"] == "provider-video-1"
            state.status = "queued"
            state.error = None
            return True

        def fail_task_for_project(self, *_args, **_kwargs):
            raise AssertionError("persisted provider task must resume rather than fail")

    backend = DurableInlineTaskBackend(durable_store=store)
    submitted: list[_InlineLaneJob] = []
    monkeypatch.setattr("novelvideo.ports.local.tasks.get_task_manager", lambda: Manager())
    monkeypatch.setattr(backend, "_submit_lane_job", submitted.append)

    assert asyncio.run(backend.recover_pending_tasks()) == 1
    assert state.status == "queued"
    assert [job.run_task_id for job in submitted] == ["run-video-recover"]
    assert submitted[0].metadata["recovery_provider_task_id"] == "provider-video-1"


def test_completed_video_download_waiting_state_keeps_durable_envelope(monkeypatch, tmp_path):
    store = DurableQueueStore(tmp_path / "queue.sqlite3")
    job = _job(tmp_path, run_task_id="run-download-waiting")
    job.envelope["task_type"] = "freezone_video_gen"
    job.envelope["queue_kind"] = "video"
    state = _State(
        "run-download-waiting",
        task_type="freezone_video_gen",
        scope="node-1",
        status="waiting",
        metadata={
            "provider": "newapi",
            "provider_task_id": "provider-video-download-1",
            "error_code": "provider_result_download_pending",
        },
    )

    class Manager:
        def get_task_for_project(self, *_args, **_kwargs):
            return state

    job.manager = Manager()
    backend = DurableInlineTaskBackend(durable_store=store)
    scheduled: list[str] = []
    monkeypatch.setattr(
        backend,
        "_schedule_download_recovery",
        lambda queued_job, queued_state: scheduled.append(
            f"{queued_job.run_task_id}:{queued_state.metadata['provider_task_id']}"
        ),
    )
    store.put(run_task_id=job.run_task_id, job=job)
    assert store.mark_running(job.run_task_id) is True

    backend._after_run(job)

    assert scheduled == ["run-download-waiting:provider-video-download-1"]
    assert store.count_pending() == 1


def test_download_recovery_stops_after_bounded_attempts(tmp_path):
    store = DurableQueueStore(tmp_path / "queue.sqlite3")
    job = _job(tmp_path, run_task_id="run-download-exhausted")
    job.envelope["task_type"] = "freezone_video_gen"
    job.envelope["queue_kind"] = "video"
    state = _State(
        "run-download-exhausted",
        task_type="freezone_video_gen",
        scope="node-1",
        status="waiting",
        metadata={
            "provider": "newapi",
            "provider_task_id": "provider-video-download-2",
            "error_code": "provider_result_download_pending",
            "download_recovery_attempt": 2,
        },
    )
    failed: list[str] = []

    class Manager:
        def fail_task_for_project(self, *_args, **kwargs):
            failed.append(str(kwargs["error"]))

    job.manager = Manager()
    backend = DurableInlineTaskBackend(durable_store=store)
    store.put(run_task_id=job.run_task_id, job=job)
    assert store.mark_running(job.run_task_id) is True

    backend._schedule_download_recovery(job, state)

    assert len(failed) == 1
    assert "连续恢复失败" in failed[0]
    assert store.count_pending() == 0


def test_recovery_submission_failure_terminates_task_and_cleans_journal(
    monkeypatch, tmp_path
):
    store = DurableQueueStore(tmp_path / "queue.sqlite3")
    stored_job = _job(tmp_path, run_task_id="run-submit-failed")
    store.put(run_task_id=stored_job.run_task_id, job=stored_job)
    state = _State(
        "run-submit-failed",
        scope="node-1",
        status="failed",
        error=INTERRUPTED_INLINE_TASK_ERROR,
        metadata={"backend": "inline"},
    )

    class Manager:
        def get_task_for_project(self, *_args, **_kwargs):
            return state

        def requeue_interrupted_task_for_project(self, *_args, **_kwargs):
            state.status = "queued"
            state.error = None
            return True

        def fail_task_for_project(self, *_args, **kwargs):
            state.status = "failed"
            state.error = kwargs["error"]

    backend = DurableInlineTaskBackend(durable_store=store)
    monkeypatch.setattr("novelvideo.ports.local.tasks.get_task_manager", lambda: Manager())
    monkeypatch.setattr(
        backend,
        "_submit_lane_job",
        lambda _job: (_ for _ in ()).throw(RuntimeError("scheduler unavailable")),
    )

    assert asyncio.run(backend.recover_pending_tasks()) == 0
    assert state.status == "failed"
    assert state.error == "Recovered task enqueue failed: scheduler unavailable"
    assert store.count_pending() == 0


def test_recovery_preserves_existing_terminal_task_state(monkeypatch, tmp_path):
    store = DurableQueueStore(tmp_path / "queue.sqlite3")
    stored_job = _job(tmp_path, run_task_id="run-completed")
    store.put(run_task_id=stored_job.run_task_id, job=stored_job)
    store.mark_running(stored_job.run_task_id)
    state = _State("run-completed", scope="node-1", status="completed")

    class Manager:
        def get_task_for_project(self, *_args, **_kwargs):
            return state

        def fail_task_for_project(self, *_args, **_kwargs):
            raise AssertionError("completed task state must remain authoritative")

    backend = DurableInlineTaskBackend(durable_store=store)
    monkeypatch.setattr("novelvideo.ports.local.tasks.get_task_manager", lambda: Manager())

    assert asyncio.run(backend.recover_pending_tasks()) == 0
    assert state.status == "completed"
    assert store.count_pending() == 0


def test_terminal_and_cancel_paths_remove_durable_row(monkeypatch, tmp_path):
    store = DurableQueueStore(tmp_path / "queue.sqlite3")
    backend = DurableInlineTaskBackend(durable_store=store)
    job = _job(tmp_path, run_task_id="run-terminal")
    store.put(run_task_id=job.run_task_id, job=job)
    backend._after_run(job)
    assert store.count_pending() == 0

    cancel_job = _job(tmp_path, run_task_id="run-cancel")
    store.put(run_task_id=cancel_job.run_task_id, job=cancel_job)
    state = _State("run-cancel", scope="node-1")

    class Manager:
        def update_progress_for_project(self, *_args, **_kwargs):
            state.status = "cancelled"

    async def request_cancel(**_kwargs):
        return None

    monkeypatch.setattr(
        "novelvideo.ports.local.tasks.get_cancellation_store",
        lambda: SimpleNamespace(request_cancel=request_cancel),
    )
    monkeypatch.setattr("novelvideo.ports.local.tasks.get_task_manager", lambda: Manager())
    monkeypatch.setattr("novelvideo.ports.local.tasks.kill_task_processes", lambda _task_id: None)

    assert asyncio.run(backend.cancel_project_task(_ctx(tmp_path), state)) is True
    assert state.status == "cancelled"
    assert store.count_pending() == 0


def test_task_state_requeues_only_the_restart_sweep_failure(tmp_path):
    ctx = _ctx(tmp_path)
    manager = TaskStateManager()
    state = manager.create_task_for_project(
        ctx,
        "freezone_gen",
        0,
        scope="node-1",
        metadata={"backend": "inline"},
    )
    manager.fail_task_for_project(
        ctx,
        state.task_type,
        state.episode,
        scope=state.scope,
        error=INTERRUPTED_INLINE_TASK_ERROR,
        expected_task_id=state.task_id,
    )

    assert manager.requeue_interrupted_task_for_project(
        ctx,
        state.task_type,
        state.episode,
        scope=state.scope,
        metadata={"durable_recovery": True},
        expected_task_id=state.task_id,
    )
    recovered = manager.get_task_for_project(
        ctx,
        state.task_type,
        state.episode,
        scope=state.scope,
    )
    assert recovered is not None
    assert recovered.status == "queued"
    assert recovered.error is None
    assert recovered.completed_at == ""
    assert recovered.metadata["durable_recovery"] is True

    manager.fail_task_for_project(
        ctx,
        state.task_type,
        state.episode,
        scope=state.scope,
        error="provider request failed",
        expected_task_id=state.task_id,
    )
    assert not manager.requeue_interrupted_task_for_project(
        ctx,
        state.task_type,
        state.episode,
        scope=state.scope,
        expected_task_id=state.task_id,
    )


def test_cancelled_awaiter_keeps_running_row_for_restart_policy(monkeypatch, tmp_path):
    store = DurableQueueStore(tmp_path / "queue.sqlite3")
    backend = DurableInlineTaskBackend(durable_store=store)
    job = _job(tmp_path, run_task_id="run-shutdown")
    store.put(run_task_id=job.run_task_id, job=job)
    started = threading.Event()
    release = threading.Event()

    def blocking_runner(*_args, **_kwargs):
        started.set()
        release.wait(timeout=5)
        return {"ok": True}

    monkeypatch.setattr(
        "novelvideo.ports.local.tasks.run_project_task_core_sync",
        blocking_runner,
    )

    async def cancel_awaiter():
        lane = backend._lanes["default"]
        task = asyncio.create_task(backend._run_inline(lane, job))
        await asyncio.to_thread(started.wait, 2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert store.pending()[0]["status"] == "running"
        release.set()

    asyncio.run(cancel_awaiter())


def test_local_lifecycle_runs_durable_recovery(monkeypatch):
    calls: list[str] = []

    async def recover_pending_tasks():
        calls.append("recover")
        return 1

    monkeypatch.setattr(
        "novelvideo.ports.get_task_backend",
        lambda: SimpleNamespace(recover_pending_tasks=recover_pending_tasks),
    )

    asyncio.run(LocalLifecycle().on_startup())
    assert calls == ["recover"]


def test_local_lifecycle_closes_village_agent_sessions(monkeypatch):
    calls: list[str] = []

    class _Pool:
        async def close_all(self) -> None:
            calls.append("close_all")

    monkeypatch.setattr("novelvideo.chat.village_harness.pool", _Pool())

    asyncio.run(LocalLifecycle().on_shutdown())

    assert calls == ["close_all"]
