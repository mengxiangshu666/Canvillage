from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from types import SimpleNamespace
from typing import Any

import pytest

from novelvideo.ports.local.tasks import InlineTaskBackend
from novelvideo.ports.registry import ensure_bootstrap
from novelvideo.project_context import ProjectContext
from novelvideo.task_state import get_task_manager
from novelvideo.workflow_runtime import media_dispatch
from workflow_plan_support import complete_script_and_production_plan


ROOT = Path(__file__).resolve().parents[1]
FFMPEG_DIR = ROOT / "runtime" / "ffmpeg"
TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled", "canceled"}


def _local_media_tools() -> tuple[Path, Path]:
    ffmpeg = FFMPEG_DIR / "ffmpeg.exe"
    ffprobe = FFMPEG_DIR / "ffprobe.exe"
    if not ffmpeg.is_file() or not ffprobe.is_file():
        pytest.skip("bundled ffmpeg/ffprobe are not available")
    return ffmpeg, ffprobe


def _prepare_local_media_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    ffmpeg, ffprobe = _local_media_tools()
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    monkeypatch.setenv("ST_LOCAL_USERNAME", "local")
    ensure_bootstrap()
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join([str(FFMPEG_DIR), os.environ.get("PATH", "")]),
    )
    return ffmpeg, ffprobe


def _context(tmp_path: Path) -> ProjectContext:
    output_dir = tmp_path / "output"
    state_dir = tmp_path / "state"
    runtime_dir = tmp_path / "runtime"
    for path in (output_dir, state_dir, runtime_dir):
        path.mkdir(parents=True, exist_ok=True)
    return ProjectContext(
        project_id="project-local-compose",
        project_name="local-compose",
        owner_type="user",
        owner_id="local",
        owner_username="local",
        requester_user_id="local",
        requester_username="local",
        requester_principals=(("user", "local"),),
        effective_role="owner",
        home_node_id="local",
        output_dir=output_dir,
        state_dir=state_dir,
        runtime_dir=runtime_dir,
        is_home_node=True,
    )


def _run(ctx: ProjectContext, *, source_rel_path: str = "") -> dict[str, Any]:
    media_assets = (
        [
            {
                "node_id": "shot-1",
                "node_type": "videoNode",
                "output_rel_path": source_rel_path,
                "duration_seconds": 2,
            }
        ]
        if source_rel_path
        else []
    )
    return {
        "id": "wfr-local-compose",
        "project_id": ctx.project_id,
        "canvas_id": "canvas-1",
        "run_mode": "auto",
        "project_context": {
            "requester_user_id": ctx.requester_user_id,
            "requester_username": ctx.requester_username,
        },
        "inputs": {
            "episode_scope": 1,
            "auto_generate_paid_media": False,
            "add_subtitles": False,
            "resolution": "160x90",
            "director_intent_contract": {
                "delivery_level": "final_film",
                "compose_required": True,
                "subtitles_required": False,
            },
            "compose_beats": [
                {
                    "beat_number": 1,
                    "video_path": source_rel_path,
                    "duration_seconds": 2,
                }
            ]
            if source_rel_path
            else [],
        },
        "artifacts": {
            "story_and_shots": {
                "plan": {"shots": [{"title": "本地合成镜头", "duration_seconds": 2}]}
            },
            "media_generation": {
                "status": "completed",
                "media_assets": media_assets,
            },
        },
        "_test_context": ctx,
    }


def _generate_source_video(ffmpeg: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(ffmpeg),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x90:r=24",
            "-t",
            "2",
            "-c:v",
            "mpeg4",
            "-q:v",
            "3",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _probe_video(ffprobe: Path, path: Path) -> dict[str, float]:
    result = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate:format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    stream = (payload.get("streams") or [{}])[0]
    numerator, denominator = str(stream["avg_frame_rate"]).split("/", 1)
    return {
        "width": float(stream["width"]),
        "height": float(stream["height"]),
        "duration_seconds": float(payload["format"]["duration"]),
        "fps": float(numerator) / float(denominator),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _CountingInlineTaskBackend(InlineTaskBackend):
    def __init__(self) -> None:
        super().__init__()
        self.submitted_task_types: list[str] = []

    def _before_submit(self, job) -> None:
        self.submitted_task_types.append(str(job.envelope.get("task_type") or ""))


class _DeferredInlineTaskBackend(_CountingInlineTaskBackend):
    """Hold the real lane job until the first executor advance has returned."""

    def __init__(self) -> None:
        super().__init__()
        self._defer_start = True

    def _start_lane_job(self, lane, job) -> None:
        if self._defer_start:
            lane.queued.append(job)
            return
        super()._start_lane_job(lane, job)

    def release(self) -> None:
        self._defer_start = False
        self._drain_lane("ffmpeg")


async def _wait_for_terminal_task(ctx: ProjectContext, scope: str):
    manager = get_task_manager()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        task = manager.get_task_for_project(
            ctx,
            "compose_episode",
            1,
            scope=scope,
        )
        if task is not None and str(task.status) in TERMINAL_TASK_STATUSES:
            return task
        await asyncio.sleep(0.1)
    raise AssertionError("compose task did not reach a terminal state within 60s")


async def _persist_workflow_at_delivery(
    ctx: ProjectContext,
    *,
    source_rel_path: str,
):
    from novelvideo.workflow_runtime.definitions import get_workflow_definition
    from novelvideo.workflow_runtime.store import WorkflowRunStore

    definition = get_workflow_definition("one-click-film")
    assert definition is not None
    store = WorkflowRunStore(ctx.state_dir)
    run, created = await store.create(
        definition=definition,
        project_id=ctx.project_id,
        canvas_id="canvas-1",
        run_mode="auto",
        inputs={
            "request": "本地真实合成恢复验收",
            "episode_scope": 1,
            "auto_generate_paid_media": True,
            "add_subtitles": False,
            "resolution": "160x90",
            "director_intent_contract": {
                "delivery_level": "final_film",
                "compose_required": True,
                "subtitles_required": False,
            },
            "production_pipeline": {
                "delivery_level": "final_film",
                "run_mode": "auto",
                "auto_generate_paid_media": True,
                "required_outputs": ["final_compose_artifact"],
            },
            "compose_beats": [
                {
                    "beat_number": 1,
                    "video_path": source_rel_path,
                    "duration_seconds": 2,
                }
            ],
        },
        idempotency_key=f"t073-compose-recovery-{ctx.project_id}",
        contract_version=2,
        project_context={
            "project_id": ctx.project_id,
            "canvas_id": "canvas-1",
            "requester_user_id": ctx.requester_user_id,
            "requester_username": ctx.requester_username,
        },
        model_plan_snapshot={
            "schema": "canvas_model_plan_snapshot.v1",
            "model_plan_revision": "t073-local-compose",
            "bindings": {
                "director": {"kind": "agent", "registry_id": "director-test"},
                "image": {"kind": "image", "registry_id": "image-test"},
                "vision": {"kind": "vision", "registry_id": "vision-test"},
            },
        },
    )
    assert created is False

    completed_payloads = {
        "canvas_structure": {
            "kind": "canvas_structure",
            "created_node_ids": ["root-1"],
        },
        "story_and_shots": {
            "kind": "storyboard_plan",
            "plan": {
                "title": "本地合成恢复",
                "shots": [{"title": "镜头一", "duration_seconds": 2}],
            },
        },
        "asset_slots": {"kind": "asset_slots", "slot_count": 1},
        "media_generation": {
            "kind": "media_batch",
            "media_assets": [
                {
                    "node_id": "shot-1",
                    "node_type": "videoNode",
                    "output_rel_path": source_rel_path,
                    "duration_seconds": 2,
                }
            ],
        },
        "quality_review": {"kind": "quality_report", "passed": True},
    }
    for step_id, payload in completed_payloads.items():
        run, applied = await store.record_event(
            run["id"],
            event_id=f"t073-prepare-delivery:{step_id}",
            event_type="step_completed",
            step_id=step_id,
            success=True,
            payload=payload,
            expected_revision=run["revision"],
        )
        assert applied is True
        assert run is not None
    assert run["current_frontier"] == ["delivery"]
    return store, run


async def _persist_workflow_at_final_film(
    ctx: ProjectContext,
    *,
    source_rel_path: str,
    production_authorization: dict[str, Any] | None = None,
    source_turn_id: str = "",
):
    from novelvideo.workflow_runtime.definitions import get_workflow_definition
    from novelvideo.workflow_runtime.store import WorkflowRunStore

    definition = get_workflow_definition("freezone-final-film")
    assert definition is not None
    store = WorkflowRunStore(ctx.state_dir)
    run, created = await store.create(
        definition=definition,
        project_id=ctx.project_id,
        canvas_id="canvas-1",
        run_mode="auto",
        inputs={
            "request": "最终合成自动 reconcile 验收",
            "episode_scope": 1,
            "auto_generate_paid_media": True,
            "add_subtitles": False,
            "resolution": "160x90",
            "director_intent_contract": {
                "delivery_level": "final_film",
                "compose_required": True,
                "subtitles_required": False,
            },
            **(
                {"production_authorization": production_authorization}
                if production_authorization is not None
                else {}
            ),
        },
        idempotency_key=(
            f"t086-auto-reconcile-{ctx.project_id}-"
            f"{'run-auth' if production_authorization else 'compose-ticket'}"
        ),
        contract_version=2,
        source_turn_id=source_turn_id,
        project_context={
            "project_id": ctx.project_id,
            "canvas_id": "canvas-1",
            "requester_user_id": ctx.requester_user_id,
            "requester_username": ctx.requester_username,
        },
        model_plan_snapshot={
            "schema": "canvas_model_plan_snapshot.v1",
            "model_plan_revision": "t086-local-reconcile",
            "bindings": {
                "director": {"kind": "agent", "registry_id": "director-test"},
                "image": {"kind": "image", "registry_id": "image-test"},
                "vision": {"kind": "vision", "registry_id": "vision-test"},
            },
        },
    )
    assert created is False

    source_path = ctx.output_dir / source_rel_path
    source_sha256 = _sha256(source_path)
    signature = "a" * 64
    completed_payloads = {
        "understand": {"kind": "preflight", "status": "completed"},
        "storyboard_images": {
            "kind": "freezone_storyboard_images",
            "status": "completed",
            "shot_count": 1,
            "completed_count": 1,
            "result_signature": "c" * 64,
        },
        "shot_videos": {
            "schema": "workflow_shot_videos_artifact.v1",
            "kind": "freezone_shot_videos",
            "status": "completed",
            "shot_count": 1,
            "completed_count": 1,
            "result_signature": signature,
            "videos": [
                {
                    "shot_id": "shot-1",
                    "shot_index": 1,
                    "output_path": source_rel_path,
                    "sha256": source_sha256,
                    "duration_seconds": 2,
                }
            ],
        },
    }
    for step_id in ("understand",):
        payload = completed_payloads[step_id]
        run, applied = await store.record_event(
            run["id"],
            event_id=f"t086-prepare-final-film:{step_id}:started",
            event_type="step_started",
            step_id=step_id,
            expected_revision=run["revision"],
        )
        assert applied is True
        assert run is not None
        run, applied = await store.record_event(
            run["id"],
            event_id=f"t086-prepare-final-film:{step_id}:completed",
            event_type="step_completed",
            step_id=step_id,
            success=True,
            payload=payload,
            expected_revision=run["revision"],
        )
        assert applied is True
        assert run is not None

    run = await complete_script_and_production_plan(
        store,
        run,
        prefix="t086-prepare-final-film",
    )
    for step_id in ("storyboard_images", "shot_videos"):
        payload = completed_payloads[step_id]
        run, applied = await store.record_event(
            run["id"],
            event_id=f"t086-prepare-final-film:{step_id}:started",
            event_type="step_started",
            step_id=step_id,
            expected_revision=run["revision"],
        )
        assert applied is True
        assert run is not None
        run, applied = await store.record_event(
            run["id"],
            event_id=f"t086-prepare-final-film:{step_id}:completed",
            event_type="step_completed",
            step_id=step_id,
            success=True,
            payload=payload,
            expected_revision=run["revision"],
        )
        assert applied is True
        assert run is not None
    assert run["current_frontier"] == ["final_film"]
    return store, run, signature


def _workflow_http_client(monkeypatch: pytest.MonkeyPatch, ctx: ProjectContext):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from novelvideo.api.routes import workflows

    async def resolve_context(*, user, project_id, required_role):
        assert user["username"] == ctx.requester_username
        assert project_id == ctx.project_id
        assert required_role in {"viewer", "editor"}
        return ctx

    monkeypatch.setattr(workflows, "resolve_project_context", resolve_context)
    monkeypatch.setattr(
        workflows,
        "schedule_workflow_run",
        lambda *_args, **_kwargs: None,
    )
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: {
        "username": ctx.requester_username,
        "user_id": ctx.requester_user_id,
    }
    return TestClient(app)


def _load_canvas_plugin():
    from novelvideo.agent_tools import village_canvas

    return village_canvas


@pytest.mark.asyncio
async def test_final_film_run_authorization_completes_without_compose_ticket(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from novelvideo.workflow_runtime import freezone_final_film
    from novelvideo.workflow_runtime.executor import WorkflowExecutor

    ffmpeg, _ffprobe = _prepare_local_media_runtime(monkeypatch)
    ctx = _context(tmp_path)
    source_rel_path = "videos/beats/ep001/source.mp4"
    _generate_source_video(ffmpeg, ctx.output_dir / source_rel_path)
    authorization = {
        "schema": "workflow_production_authorization.v1",
        "scope": "workflow_run",
        "project_id": ctx.project_id,
        "canvas_id": "canvas-1",
        "source_turn_id": "turn-run-authorization",
        "source": "server_turn_grant",
        "max_paid_starts": 1,
        "max_shots": 1,
        "max_reference_images": 9,
        "max_duration_seconds": 60,
        "allow_final_film": True,
    }
    store, run, _signature = await _persist_workflow_at_final_film(
        ctx,
        source_rel_path=source_rel_path,
        production_authorization=authorization,
        source_turn_id="turn-run-authorization",
    )
    backend = _CountingInlineTaskBackend()

    async def resolve_context(_run):
        return ctx

    monkeypatch.setattr(
        media_dispatch,
        "resolve_workflow_project_context",
        resolve_context,
    )
    monkeypatch.setattr(
        freezone_final_film,
        "resolve_workflow_project_context",
        resolve_context,
    )
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: backend)

    dispatched = await WorkflowExecutor(store).advance(run["id"])

    assert dispatched is not None
    assert dispatched["status"] == "running", {
        "status": dispatched["status"],
        "error": dispatched.get("error"),
        "final_film": dispatched.get("artifacts", {}).get("final_film"),
    }
    final_artifact = dispatched["artifacts"]["final_film"]
    assert final_artifact["status"] == "monitoring"
    assert "compose_authorization" not in final_artifact
    assert backend.submitted_task_types == ["compose_episode"]

    task = await _wait_for_terminal_task(ctx, str(final_artifact["scope"]))
    assert task.status == "completed", {
        "status": task.status,
        "error": task.error,
        "result": task.result,
    }

    completed = await WorkflowExecutor(store).advance(run["id"])

    assert completed is not None
    assert completed["status"] == "completed"
    final_artifact = completed["artifacts"]["final_film"]
    assert "compose_authorization" not in final_artifact
    assert final_artifact["status"] == "completed"
    assert final_artifact["final_compose_artifact"]["task_id"] == task.task_id
    assert final_artifact["final_compose_artifact"]["sha256"] == _sha256(
        Path(final_artifact["final_compose_artifact"]["path"])
    )
    assert backend.submitted_task_types == ["compose_episode"]


@pytest.mark.asyncio
async def test_real_compose_is_idempotent_and_reconciles_verified_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ffmpeg, ffprobe = _prepare_local_media_runtime(monkeypatch)
    ctx = _context(tmp_path)
    source_rel_path = "videos/beats/ep001/source.mp4"
    _generate_source_video(ffmpeg, ctx.output_dir / source_rel_path)
    run = _run(ctx, source_rel_path=source_rel_path)
    backend = _CountingInlineTaskBackend()

    async def resolve_context(_run):
        return ctx

    monkeypatch.setattr(
        media_dispatch,
        "resolve_workflow_project_context",
        resolve_context,
    )
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: backend)

    first = await media_dispatch.dispatch_workflow_compose(
        run,
        state_dir=ctx.state_dir,
        step_id="delivery",
    )
    second = await media_dispatch.dispatch_workflow_compose(
        run,
        state_dir=ctx.state_dir,
        step_id="delivery",
    )

    assert first["status"] == "monitoring"
    assert second["reused"] is True
    assert second["task_id"] == first["task_id"]
    assert backend.submitted_task_types == ["compose_episode"]

    task = await _wait_for_terminal_task(ctx, first["scope"])
    assert task.status == "completed", {
        "status": task.status,
        "error": task.error,
        "result": task.result,
    }
    result_path = Path(str(task.result["video_path"]))
    assert result_path.is_file()
    assert result_path.stat().st_size > 0

    observation = await media_dispatch.reconcile_workflow_compose(
        run,
        state_dir=ctx.state_dir,
        step_id="delivery",
        artifact=first,
    )

    assert observation["status"] == "completed"
    artifact = observation["final_compose_artifact"]
    delivery_qc = observation["delivery_qc"]
    assert delivery_qc["schema"] == "delivery_qc_contract.v1"
    assert delivery_qc["checks"]["file_readback"]["status"] == "passed"
    assert delivery_qc["checks"]["sha256"]["evidence"] == artifact["sha256"]
    final_path = Path(artifact["path"])
    relative_path = Path(artifact["relative_path"])
    assert final_path == result_path
    assert final_path.is_relative_to(ctx.output_dir.resolve())
    assert not relative_path.is_absolute()
    assert artifact["schema"] == "workflow_final_compose_artifact.v1"
    assert artifact["kind"] == "final_compose_artifact"
    assert artifact["task_id"] == task.task_id
    assert artifact["mime_type"] == "video/mp4"
    assert artifact["url"]
    assert artifact["sha256"] == _sha256(final_path)
    assert artifact["options"]["fps"] == 30
    assert artifact["options"]["delivery_fps"]["source"] == "server_default"

    probed = _probe_video(ffprobe, final_path)
    assert artifact["width"] == int(probed["width"])
    assert artifact["height"] == int(probed["height"])
    assert artifact["duration_seconds"] == pytest.approx(
        probed["duration_seconds"],
        abs=0.05,
    )
    assert probed["fps"] == pytest.approx(30, abs=0.01)

    restarted_backend = _CountingInlineTaskBackend()
    monkeypatch.setattr(
        media_dispatch,
        "get_task_backend",
        lambda: restarted_backend,
    )
    third = await media_dispatch.dispatch_workflow_compose(
        run,
        state_dir=ctx.state_dir,
        step_id="delivery",
    )
    assert third["reused"] is True
    assert third["task_id"] == first["task_id"]
    assert backend.submitted_task_types == ["compose_episode"]
    assert restarted_backend.submitted_task_types == []

    second_observation = await media_dispatch.reconcile_workflow_compose(
        run,
        state_dir=ctx.state_dir,
        step_id="delivery",
        artifact=third,
    )
    assert second_observation["status"] == "completed"
    assert (
        second_observation["final_compose_artifact"]["sha256"]
        == artifact["sha256"]
    )
    assert artifact["sha256"] == _sha256(final_path)


@pytest.mark.asyncio
async def test_workflow_executor_recovers_real_compose_across_restart_and_http(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from novelvideo.workflow_runtime import executor as workflow_executor
    from novelvideo.workflow_runtime.executor import (
        WorkflowExecutor,
        resume_workflow_runs_for_projects,
    )
    from novelvideo.workflow_runtime.store import WorkflowRunStore

    ffmpeg, _ffprobe = _prepare_local_media_runtime(monkeypatch)
    ctx = _context(tmp_path)
    source_rel_path = "videos/beats/ep001/source.mp4"
    _generate_source_video(ffmpeg, ctx.output_dir / source_rel_path)
    store, run = await _persist_workflow_at_delivery(
        ctx,
        source_rel_path=source_rel_path,
    )
    first_backend = _DeferredInlineTaskBackend()

    async def resolve_context(_run):
        return ctx

    monkeypatch.setattr(
        media_dispatch,
        "resolve_workflow_project_context",
        resolve_context,
    )
    monkeypatch.setattr(
        media_dispatch,
        "get_task_backend",
        lambda: first_backend,
    )

    dispatched = await WorkflowExecutor(store).advance(run["id"])

    assert dispatched is not None
    assert dispatched["status"] == "running", {
        "status": dispatched["status"],
        "error": dispatched.get("error"),
        "delivery": dispatched.get("artifacts", {}).get("delivery"),
        "submitted": first_backend.submitted_task_types,
    }
    assert dispatched["current_frontier"] == ["delivery"]
    delivery = dispatched["artifacts"]["delivery"]
    assert delivery["kind"] == "compose_episode"
    assert delivery["status"] == "monitoring"
    assert first_backend.submitted_task_types == ["compose_episode"]

    first_backend.release()
    task = await _wait_for_terminal_task(ctx, str(delivery["scope"]))
    assert task.status == "completed", {
        "status": task.status,
        "error": task.error,
        "result": task.result,
    }

    restarted_backend = _CountingInlineTaskBackend()
    monkeypatch.setattr(
        media_dispatch,
        "get_task_backend",
        lambda: restarted_backend,
    )
    restarted_store = WorkflowRunStore(ctx.state_dir)
    abandoned = await restarted_store.acquire_execution_lease(
        run["id"], owner="executor:stopped-process"
    )
    assert abandoned is not None
    with sqlite3.connect(restarted_store.db_path) as db:
        db.execute(
            "UPDATE canvas_workflow_target_leases SET lease_until=? WHERE run_id=?",
            ("2000-01-01T00:00:00Z", run["id"]),
        )
        db.execute(
            "UPDATE canvas_workflow_runs SET lease_expires_at=? WHERE id=?",
            ("2000-01-01T00:00:00Z", run["id"]),
        )
        db.commit()

    resumed = await resume_workflow_runs_for_projects(
        [
            SimpleNamespace(
                id=ctx.project_id,
                state_dir=str(ctx.state_dir),
                home_node_id="local",
            )
        ]
    )
    assert resumed == 1
    driver_task = workflow_executor._RUN_TASKS.get(
        f"{restarted_store.db_path.resolve()}::{run['id']}"
    )
    assert driver_task is not None
    await asyncio.wait_for(driver_task, timeout=5)
    for _ in range(200):
        completed = await restarted_store.get(run["id"])
        if completed is not None and completed["status"] != "running":
            break
        await asyncio.sleep(0.01)

    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["current_frontier"] == []
    assert completed["step_states"]["delivery"]["status"] == "completed"
    final_artifact = completed["artifacts"]["delivery"]["final_compose_artifact"]
    assert final_artifact["schema"] == "workflow_final_compose_artifact.v1"
    assert final_artifact["task_id"] == task.task_id
    assert final_artifact["sha256"] == _sha256(Path(final_artifact["path"]))
    assert restarted_backend.submitted_task_types == []

    client = _workflow_http_client(monkeypatch, ctx)
    with client:
        response = client.get(
            f"/api/v1/projects/{ctx.project_id}/workflow-runs/{run['id']}"
        )
    assert response.status_code == 200, response.text
    returned_run = response.json()["data"]
    assert returned_run["status"] == "completed"
    assert (
        returned_run["artifacts"]["delivery"]["final_compose_artifact"]
        == final_artifact
    )

    unchanged = await WorkflowExecutor(store).advance(run["id"])
    assert unchanged is not None
    assert unchanged["revision"] == completed["revision"]
    assert restarted_backend.submitted_task_types == []


@pytest.mark.asyncio
async def test_final_film_auto_reconciles_one_transient_failure_without_redispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from novelvideo.workflow_runtime import freezone_final_film
    from novelvideo.workflow_runtime.compose_authorization import (
        COMPOSE_AUTHORIZATION_SCHEMA,
    )
    from novelvideo.workflow_runtime.executor import WorkflowExecutor

    ffmpeg, _ffprobe = _prepare_local_media_runtime(monkeypatch)
    ctx = _context(tmp_path)
    source_rel_path = "videos/beats/ep001/source.mp4"
    _generate_source_video(ffmpeg, ctx.output_dir / source_rel_path)
    store, run, source_signature = await _persist_workflow_at_final_film(
        ctx,
        source_rel_path=source_rel_path,
    )
    backend = _DeferredInlineTaskBackend()

    async def resolve_context(_run):
        return ctx

    monkeypatch.setattr(
        media_dispatch,
        "resolve_workflow_project_context",
        resolve_context,
    )
    monkeypatch.setattr(
        media_dispatch,
        "get_task_backend",
        lambda: backend,
    )
    artifact = await media_dispatch.dispatch_workflow_compose(
        run,
        state_dir=store.state_dir,
        step_id="final_film",
    )
    artifact["compose_authorization"] = {
        "schema": COMPOSE_AUTHORIZATION_SCHEMA,
        "authorization_id": "wca-t086",
        "project_id": ctx.project_id,
        "canvas_id": "canvas-1",
        "run_id": run["id"],
        "step_id": "final_film",
        "source_result_signature": source_signature,
        "consume_key": "consume-t086",
        "consumed_at_ms": 1,
    }
    run, applied = await store.record_event(
        run["id"],
        event_id="t086-final-film-monitoring",
        event_type="step_output_ready",
        step_id="final_film",
        success=True,
        payload=artifact,
        expected_revision=run["revision"],
        source="executor",
    )
    assert applied is True
    assert run is not None
    assert backend.submitted_task_types == ["compose_episode"]

    original_reconcile = freezone_final_film.reconcile_workflow_compose
    reconcile_calls = 0

    async def transient_then_real(*args, **kwargs):
        nonlocal reconcile_calls
        reconcile_calls += 1
        if reconcile_calls == 1:
            raise RuntimeError("temporary compose observation failure")
        return await original_reconcile(*args, **kwargs)

    monkeypatch.setattr(
        freezone_final_film,
        "reconcile_workflow_compose",
        transient_then_real,
    )

    retried = await WorkflowExecutor(store).advance(run["id"])

    assert retried is not None
    assert retried["status"] == "running"
    assert retried["step_states"]["final_film"]["attempt"] == 2
    retrying_artifact = retried["artifacts"]["final_film"]
    assert retrying_artifact["status"] == "monitoring"
    assert retrying_artifact["task_id"] == artifact["task_id"]
    assert retrying_artifact["automatic_recovery"]["mode"] == (
        "reconcile_existing_task"
    )
    assert reconcile_calls == 1
    assert backend.submitted_task_types == ["compose_episode"]

    backend.release()
    task = await _wait_for_terminal_task(ctx, str(artifact["scope"]))
    assert task.status == "completed", {
        "status": task.status,
        "error": task.error,
        "result": task.result,
    }

    completed = await WorkflowExecutor(store).advance(run["id"])

    assert completed is not None
    assert completed["status"] == "completed"
    final_artifact = completed["artifacts"]["final_film"]
    assert final_artifact["status"] == "completed"
    assert "automatic_recovery" not in final_artifact
    assert final_artifact["final_compose_artifact"]["task_id"] == task.task_id
    assert final_artifact["final_compose_artifact"]["sha256"] == _sha256(
        Path(final_artifact["final_compose_artifact"]["path"])
    )
    assert reconcile_calls == 2
    assert backend.submitted_task_types == ["compose_episode"]

    client = _workflow_http_client(monkeypatch, ctx)
    with client:
        response = client.get(
            f"/api/v1/projects/{ctx.project_id}/workflow-runs/{run['id']}"
        )
    assert response.status_code == 200, response.text
    returned = response.json()["data"]
    assert returned["status"] == "completed"
    assert (
        returned["artifacts"]["final_film"]["final_compose_artifact"]
        == final_artifact["final_compose_artifact"]
    )


@pytest.mark.asyncio
async def test_agent_recovery_turn_waits_for_real_workflow_terminal_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from novelvideo.chat import village_turn_policy
    from novelvideo.workflow_runtime.executor import WorkflowExecutor

    ffmpeg, _ffprobe = _prepare_local_media_runtime(monkeypatch)
    ctx = _context(tmp_path)
    source_rel_path = "videos/beats/ep001/source.mp4"
    _generate_source_video(ffmpeg, ctx.output_dir / source_rel_path)
    store, run = await _persist_workflow_at_delivery(
        ctx,
        source_rel_path=source_rel_path,
    )
    first_backend = _DeferredInlineTaskBackend()

    async def resolve_context(_run):
        return ctx

    monkeypatch.setattr(
        media_dispatch,
        "resolve_workflow_project_context",
        resolve_context,
    )
    monkeypatch.setattr(
        media_dispatch,
        "get_task_backend",
        lambda: first_backend,
    )

    dispatched = await WorkflowExecutor(store).advance(run["id"])
    assert dispatched is not None
    assert dispatched["status"] == "running"
    delivery = dispatched["artifacts"]["delivery"]
    assert delivery["status"] == "monitoring"
    assert first_backend.submitted_task_types == ["compose_episode"]

    client = _workflow_http_client(monkeypatch, ctx)
    plugin = _load_canvas_plugin()

    def real_http_request(method, path, *, query=None, body=None):
        response = client.request(method, path, params=query, json=body)
        payload = response.json()
        if isinstance(payload, dict):
            return {"status_code": response.status_code, **payload}
        return {
            "status_code": response.status_code,
            "ok": response.is_success,
            "data": payload,
        }

    monkeypatch.setattr(plugin, "_request", real_http_request)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", ctx.project_id)
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    recovery_prompt = (
        "[VILLAGE_AGENT_CONTEXT_CHECKPOINT]\n"
        + json.dumps(
            {
                "recovery_contract": {
                    "schema": "village_agent_recovery_contract.v1",
                    "action": "resume_workflow_run",
                    "allow_new_submission": False,
                    "workflow_run_id": run["id"],
                }
            },
            ensure_ascii=False,
        )
        + "\n[/VILLAGE_AGENT_CONTEXT_CHECKPOINT]"
    )
    contract = village_turn_policy.recovery_contract_from_prompt(recovery_prompt)
    assert contract is not None
    director = village_turn_policy.DirectorWorkflowRun(recovery_contract=contract)
    assert director.workflow_run_id == run["id"]
    assert director.workflow_receipt_pending is True

    with client:
        assert (
            director.start_tool(
                "village_canvas_get_workflow_run",
                {"rawInput": {"run_id": run["id"]}},
            )
            is None
        )
        running_response = json.loads(
            plugin._handle_get_workflow_run({"run_id": run["id"]})
        )
        assert (
            director.finish_tool(
                "village_canvas_get_workflow_run",
                {"result": running_response},
            )
            == "observing"
        )
        assert director.workflow_run_status == "running"
        assert director.workflow_receipt_pending is True
        assert director.complete_turn() == "observing"
        assert director.payload()["workflow_run_continuation"] == {
            "run_id": run["id"],
            "run_status": "running",
            "receipt_pending": True,
        }

        first_backend.release()
        task = await _wait_for_terminal_task(ctx, str(delivery["scope"]))
        assert task.status == "completed", {
            "status": task.status,
            "error": task.error,
            "result": task.result,
        }

        restarted_backend = _CountingInlineTaskBackend()
        monkeypatch.setattr(
            media_dispatch,
            "get_task_backend",
            lambda: restarted_backend,
        )
        completed = await WorkflowExecutor(store).advance(run["id"])
        assert completed is not None
        assert completed["status"] == "completed"
        assert restarted_backend.submitted_task_types == []

        assert (
            director.start_tool(
                "village_canvas_get_workflow_run",
                {"rawInput": {"run_id": run["id"]}},
            )
            is None
        )
        completed_response = json.loads(
            plugin._handle_get_workflow_run({"run_id": run["id"]})
        )
        assert (
            director.finish_tool(
                "village_canvas_get_workflow_run",
                {"result": completed_response},
            )
            == "verifying"
        )

    assert director.workflow_run_status == "completed"
    assert director.workflow_receipt_pending is False
    assert director.verified_receipts == 1
    assert director.complete_turn() == "completed"
    assert director.payload()["workflow_run_continuation"] == {
        "run_id": run["id"],
        "run_status": "completed",
        "receipt_pending": False,
    }
    returned_run = completed_response["data"]
    final_artifact = returned_run["artifacts"]["delivery"]["final_compose_artifact"]
    assert returned_run["id"] == run["id"]
    assert returned_run["status"] == "completed"
    assert final_artifact["task_id"] == task.task_id
    assert final_artifact["sha256"] == _sha256(Path(final_artifact["path"]))


@pytest.mark.parametrize(
    ("case", "error_code"),
    [
        ("missing", "workflow_compose_artifact_missing"),
        ("empty", "workflow_compose_artifact_missing"),
        ("non_mp4", "workflow_compose_artifact_missing"),
        ("outside", "workflow_compose_artifact_outside_project"),
        ("metadata", "workflow_compose_metadata_missing"),
    ],
)
@pytest.mark.asyncio
async def test_compose_reconcile_invalid_artifacts_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
    error_code: str,
) -> None:
    ctx = _context(tmp_path)
    run = _run(ctx)
    output_path = ctx.output_dir / "videos" / "episodes" / "ep001_final.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if case == "empty":
        output_path.touch()
    elif case == "non_mp4":
        output_path = output_path.with_suffix(".mov")
        output_path.write_bytes(b"not-mp4")
    elif case == "outside":
        output_path = tmp_path / "outside.mp4"
        output_path.write_bytes(b"not-mp4")
    elif case == "metadata":
        output_path.write_bytes(b"not-video")
    task = type(
        "ComposeTask",
        (),
        {
            "status": "completed",
            "progress": 1.0,
            "result": {"video_path": str(output_path)},
            "error": "",
            "metadata": {},
        },
    )()

    async def resolve_context(_run):
        return ctx

    monkeypatch.setattr(
        media_dispatch,
        "resolve_workflow_project_context",
        resolve_context,
    )
    monkeypatch.setattr(
        media_dispatch,
        "get_task_manager",
        lambda: type(
            "TaskManager",
            (),
            {"get_task_for_project": lambda *_args, **_kwargs: task},
        )(),
    )
    if case == "metadata":
        monkeypatch.setattr(media_dispatch, "_probe_video_metadata", lambda _path: {})

    observation = await media_dispatch.reconcile_workflow_compose(
        run,
        state_dir=ctx.state_dir,
        step_id="delivery",
        artifact={
            "kind": "compose_episode",
            "task_type": "compose_episode",
            "task_id": "compose-task-invalid",
            "scope": f"workflow:{run['id']}:compose",
            "episode": 1,
            "options": {},
        },
    )

    assert observation["status"] == "failed"
    assert observation["error_code"] == error_code
