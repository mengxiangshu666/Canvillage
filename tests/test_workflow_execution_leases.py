from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from novelvideo.workflow_runtime.definitions import (
    WorkflowDefinition,
    WorkflowStepDefinition,
)
from novelvideo.workflow_runtime.store import (
    WorkflowRunConflictError,
    WorkflowRunStore,
)
from novelvideo.workflow_runtime.executor import (
    WorkflowExecutor,
    _drive_workflow_run,
    _renew_execution_lease_or_mark_lost,
)


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        id="lease-fixture",
        version=1,
        title="lease fixture",
        description="lease fixture",
        starter_workflow_id="lease-fixture",
        steps=(
            WorkflowStepDefinition(
                id="step",
                label="step",
                type="local_task",
                handler="test.handler",
            ),
        ),
        outputs=(),
    )


async def _create(
    store: WorkflowRunStore,
    key: str,
    target: str,
) -> dict:
    run, reused = await store.create(
        definition=_definition(),
        project_id="project-1",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": key, "target_node_ids": [target]},
        project_context={"target_node_ids": [target]},
        idempotency_key=key,
        contract_version=2,
    )
    assert reused is False
    return run


@pytest.mark.asyncio
async def test_target_lease_blocks_parallel_run_and_exposes_original(tmp_path: Path):
    store = WorkflowRunStore(tmp_path)
    first = await _create(store, "first", "shot-1")

    with pytest.raises(WorkflowRunConflictError) as raised:
        await _create(store, "second", "shot-1")

    assert raised.value.code == "workflow_target_lease_conflict"
    assert raised.value.details["existing_run_id"] == first["id"]
    assert raised.value.details["target_node_ids"] == ["shot-1"]
    assert await store.list_target_leases(first["id"])


@pytest.mark.asyncio
async def test_start_idempotency_reuses_only_the_same_request(tmp_path: Path):
    store = WorkflowRunStore(tmp_path)
    first = await _create(store, "same-key", "shot-1")

    replay, reused = await store.create(
        definition=_definition(),
        project_id="project-1",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "same-key", "target_node_ids": ["shot-1"]},
        project_context={"target_node_ids": ["shot-1"]},
        idempotency_key="same-key",
        contract_version=2,
    )

    assert reused is True
    assert replay["id"] == first["id"]

    with pytest.raises(WorkflowRunConflictError) as raised:
        await store.create(
            definition=_definition(),
            project_id="project-1",
            canvas_id="canvas-1",
            run_mode="draft",
            inputs={"request": "different request", "target_node_ids": ["shot-1"]},
            project_context={"target_node_ids": ["shot-1"]},
            idempotency_key="same-key",
            contract_version=2,
        )

    assert raised.value.code == "workflow_run_idempotency_conflict"
    assert raised.value.details["existing_run_id"] == first["id"]


@pytest.mark.asyncio
async def test_expired_target_lease_can_be_taken_over_and_stale_release_is_ignored(
    tmp_path: Path,
):
    store = WorkflowRunStore(tmp_path)
    first = await _create(store, "first", "shot-1")
    stale = await store.get(first["id"])
    assert stale is not None

    with sqlite3.connect(store.db_path) as db:
        db.execute(
            "UPDATE canvas_workflow_target_leases SET lease_until=? WHERE run_id=?",
            ("2000-01-01T00:00:00Z", first["id"]),
        )
        db.execute(
            "UPDATE canvas_workflow_runs SET lease_expires_at=? WHERE id=?",
            ("2000-01-01T00:00:00Z", first["id"]),
        )
        db.commit()

    second = await store.acquire_execution_lease(first["id"], owner="worker-2")
    assert second is not None
    assert second["lease_owner"] == "worker-2"
    assert second["lease_token"] != stale["lease_token"]
    assert await store.release_execution_lease(
        first["id"], owner="worker-1", lease_token=stale["lease_token"]
    ) is False
    assert await store.execution_lease_is_valid(
        first["id"], owner="worker-2", lease_token=second["lease_token"]
    )


@pytest.mark.asyncio
async def test_lease_renewal_requires_exact_owner_and_token(tmp_path: Path):
    store = WorkflowRunStore(tmp_path)
    run = await _create(store, "first", "shot-1")
    leased = await store.acquire_execution_lease(run["id"], owner="worker-1")
    assert leased is not None
    token = leased["lease_token"]

    assert await store.renew_execution_lease(
        run["id"], owner="worker-2", lease_token=token
    ) is False
    assert await store.renew_execution_lease(
        run["id"], owner="worker-1", lease_token="wrong-token"
    ) is False
    assert await store.renew_execution_lease(
        run["id"], owner="worker-1", lease_token=token
    ) is True


@pytest.mark.asyncio
async def test_execution_lease_is_invalid_when_any_target_lease_is_missing(
    tmp_path: Path,
):
    store = WorkflowRunStore(tmp_path)
    run = await _create(store, "first", "shot-1")
    leased = await store.acquire_execution_lease(run["id"], owner="worker-1")
    assert leased is not None
    assert await store.execution_lease_is_valid(
        run["id"], owner="worker-1", lease_token=leased["lease_token"]
    )

    with sqlite3.connect(store.db_path) as db:
        db.execute(
            "DELETE FROM canvas_workflow_target_leases WHERE run_id=?",
            (run["id"],),
        )
        db.commit()

    assert not await store.execution_lease_is_valid(
        run["id"], owner="worker-1", lease_token=leased["lease_token"]
    )


@pytest.mark.asyncio
async def test_heartbeat_exception_marks_lease_lost_without_escaping():
    class FailingStore:
        async def renew_execution_lease(self, *_args, **_kwargs):
            raise OSError("sqlite temporarily unavailable")

    lost = asyncio.Event()
    assert not await _renew_execution_lease_or_mark_lost(
        FailingStore(),
        "run-1",
        owner="worker-1",
        token="token-1",
        lease_lost=lost,
    )
    assert lost.is_set()


@pytest.mark.asyncio
async def test_drive_releases_lease_after_safe_stage_handoff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    store = WorkflowRunStore(tmp_path)
    run = await _create(store, "lease-handoff", "shot-1")
    calls = 0

    async def fake_advance(
        _self: WorkflowExecutor,
        run_id: str,
        *,
        lease_owner: str = "",
        lease_token: str = "",
    ) -> dict:
        nonlocal calls
        calls += 1
        current = await store.get(run_id)
        assert current is not None
        assert lease_owner
        assert lease_token
        if calls == 1:
            return {**current, "current_frontier": []}
        return current

    monkeypatch.setattr(WorkflowExecutor, "advance", fake_advance)

    first = await asyncio.wait_for(_drive_workflow_run(store, run["id"]), timeout=2)
    assert first is not None and first["status"] == "running"
    assert calls == 1

    released = await store.get(run["id"])
    assert released is not None
    assert released["lease_owner"] == ""
    assert released["lease_token"] == ""
    assert await store.list_target_leases(run["id"]) == []

    second = await asyncio.wait_for(_drive_workflow_run(store, run["id"]), timeout=2)
    assert second is not None and second["status"] == "running"
    assert calls == 2


@pytest.mark.asyncio
async def test_terminal_event_releases_target_lease(tmp_path: Path):
    store = WorkflowRunStore(tmp_path)
    run = await _create(store, "first", "shot-1")
    updated, applied = await store.record_event(
        run["id"],
        event_id="cancel-1",
        event_type="run_cancelled",
        error="user_cancelled",
        expected_revision=run["revision"],
        source="executor",
    )
    assert applied is True
    assert updated is not None
    assert updated["status"] == "cancelled"
    assert updated["lease_token"] == ""
    assert await store.list_target_leases(run["id"]) == []


@pytest.mark.asyncio
async def test_pause_releases_targets_and_resume_reacquires_them_atomically(
    tmp_path: Path,
):
    store = WorkflowRunStore(tmp_path)
    run = await _create(store, "first", "shot-1")

    paused, applied = await store.record_event(
        run["id"],
        event_id="pause-1",
        event_type="run_paused",
        expected_revision=run["revision"],
    )
    assert applied is True
    assert paused is not None
    assert paused["status"] == "paused"
    assert paused["lease_token"] == ""
    assert await store.list_target_leases(run["id"]) == []

    resumed, applied = await store.record_event(
        run["id"],
        event_id="resume-1",
        event_type="run_resumed",
        expected_revision=paused["revision"],
    )
    assert applied is True
    assert resumed is not None
    assert resumed["status"] == "running"
    assert resumed["lease_owner"].startswith("reservation:run_resumed:")
    assert resumed["lease_token"]
    target_leases = await store.list_target_leases(run["id"])
    assert [item["target_node_id"] for item in target_leases] == ["shot-1"]
    assert target_leases[0]["owner"] == resumed["lease_owner"]


@pytest.mark.asyncio
async def test_resume_stays_paused_when_another_run_owns_the_target(tmp_path: Path):
    store = WorkflowRunStore(tmp_path)
    first = await _create(store, "first", "shot-1")
    paused, applied = await store.record_event(
        first["id"],
        event_id="pause-1",
        event_type="run_paused",
        expected_revision=first["revision"],
    )
    assert applied is True
    assert paused is not None

    second = await _create(store, "second", "shot-1")

    with pytest.raises(WorkflowRunConflictError) as raised:
        await store.record_event(
            first["id"],
            event_id="resume-conflict",
            event_type="run_resumed",
            expected_revision=paused["revision"],
        )

    assert raised.value.code == "workflow_target_lease_conflict"
    assert raised.value.details["existing_run_id"] == second["id"]
    unchanged = await store.get(first["id"])
    assert unchanged is not None
    assert unchanged["status"] == "paused"
    assert unchanged["revision"] == paused["revision"]
    assert await store.list_target_leases(first["id"]) == []


@pytest.mark.asyncio
async def test_retry_stays_failed_when_another_run_owns_the_target(tmp_path: Path):
    store = WorkflowRunStore(tmp_path)
    first = await _create(store, "first", "shot-1")
    failed, applied = await store.record_event(
        first["id"],
        event_id="step-failed",
        event_type="step_failed",
        step_id="step",
        error="upstream_failed",
        expected_revision=first["revision"],
        source="executor",
    )
    assert applied is True
    assert failed is not None
    assert failed["status"] == "failed"
    assert await store.list_target_leases(first["id"]) == []

    second = await _create(store, "second", "shot-1")

    with pytest.raises(WorkflowRunConflictError) as raised:
        await store.record_event(
            first["id"],
            event_id="retry-conflict",
            event_type="step_retried",
            step_id="step",
            expected_revision=failed["revision"],
        )

    assert raised.value.code == "workflow_target_lease_conflict"
    assert raised.value.details["existing_run_id"] == second["id"]
    unchanged = await store.get(first["id"])
    assert unchanged is not None
    assert unchanged["status"] == "failed"
    assert unchanged["revision"] == failed["revision"]
    assert await store.list_target_leases(first["id"]) == []


@pytest.mark.asyncio
async def test_concurrent_target_reservation_allows_one_run(tmp_path: Path):
    store = WorkflowRunStore(tmp_path)

    results = await asyncio.gather(
        _create(store, "first", "shot-1"),
        _create(store, "second", "shot-1"),
        return_exceptions=True,
    )
    successful = [result for result in results if isinstance(result, dict)]
    conflicts = [result for result in results if isinstance(result, Exception)]
    assert len(successful) == 1
    assert len(conflicts) == 1
    assert isinstance(conflicts[0], WorkflowRunConflictError)
    assert conflicts[0].code == "workflow_target_lease_conflict"


# ---------------------------------------------------------------------------
# 媒体 monitoring 看门狗（T-172）：driver 自轮询不能无限挂住。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_media_monitor_watchdog_stops_polling_and_keeps_run_visible(
    tmp_path: Path,
    monkeypatch,
    caplog,
):
    """在途媒体超时后 driver 撒手：不无限轮询，run 也不被误判成失败。"""

    import novelvideo.workflow_runtime.executor as executor_module
    from novelvideo.workflow_runtime.step_contract import StepResult

    calls = {"n": 0}

    async def fake_storyboard(run, step):
        calls["n"] += 1
        if calls["n"] == 1:
            return StepResult(
                "step_progress",
                {"kind": "freezone_storyboard_batch", "status": "monitoring"},
            )
        return StepResult("waiting", {"status": "monitoring"})

    monkeypatch.setitem(
        executor_module.HANDLERS, "freezone.storyboard_images", fake_storyboard
    )
    monkeypatch.setattr(
        executor_module, "WORKFLOW_MEDIA_MONITOR_WATCHDOG_SECONDS", 0.2
    )

    store = WorkflowRunStore(tmp_path)
    run, _reused = await store.create(
        definition=WorkflowDefinition(
            id="watchdog-fixture",
            version=1,
            title="watchdog fixture",
            description="watchdog fixture",
            starter_workflow_id="watchdog-fixture",
            steps=(
                WorkflowStepDefinition(
                    id="storyboard_images",
                    label="生成分镜图",
                    type="media_batch",
                    handler="freezone.storyboard_images",
                ),
            ),
            outputs=(),
        ),
        project_id="project-1",
        canvas_id="canvas-1",
        run_mode="auto",
        inputs={"request": "watchdog"},
        project_context={},
        idempotency_key="watchdog-1",
        contract_version=2,
    )

    driven = await asyncio.wait_for(
        _drive_workflow_run(store, run["id"]), timeout=15
    )

    # 看门狗触发：driver 返回而不是无限轮询；handler 至少重入一次 waiting。
    assert calls["n"] >= 2
    assert driven is not None
    assert driven.get("status") == "running"
    step_state = driven.get("step_states", {}).get("storyboard_images")
    assert isinstance(step_state, dict)
    assert step_state.get("status") != "failed"
    artifact = driven.get("artifacts", {}).get("storyboard_images")
    assert isinstance(artifact, dict)
    assert artifact.get("status") == "monitoring"
