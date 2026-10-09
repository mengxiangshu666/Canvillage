from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.project_context import ProjectContext
from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.task_backend.runners.video import _resolve_compose_media_path
from novelvideo.workflow_runtime import executor as workflow_executor
from novelvideo.workflow_runtime import media_dispatch
from novelvideo.workflow_runtime.store import WorkflowRunStore


def _context(tmp_path: Path) -> ProjectContext:
    output_dir = tmp_path / "output"
    state_dir = tmp_path / "state"
    runtime_dir = tmp_path / "runtime"
    output_dir.mkdir()
    state_dir.mkdir()
    runtime_dir.mkdir()
    return ProjectContext(
        project_id="project-1",
        project_name="compose-test",
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


def _run(*, tmp_path: Path, run_mode: str = "auto") -> dict:
    ctx = _context(tmp_path)
    return {
        "id": "wfr-compose",
        "project_id": ctx.project_id,
        "canvas_id": "canvas-1",
        "run_mode": run_mode,
        "project_context": {
            "requester_user_id": "local",
            "requester_username": "local",
        },
        "inputs": {
            "episode_scope": 2,
            "auto_generate_paid_media": run_mode == "auto",
            "director_intent_contract": {
                "delivery_level": "final_film",
                "compose_required": True,
                "subtitles_required": True,
            },
            "production_pipeline": {
                "delivery_level": "final_film",
                "run_mode": run_mode,
                "auto_generate_paid_media": run_mode == "auto",
                "required_outputs": ["final_compose_artifact"],
            },
        },
        "artifacts": {
            "story_and_shots": {
                "plan": {"title": "最终成片", "shots": [{"title": "镜头一"}]}
            }
        },
        "_test_context": ctx,
    }


def test_workflow_compose_beats_carry_verified_video_paths(tmp_path: Path):
    run = _run(tmp_path=tmp_path)
    run["artifacts"]["media_generation"] = {
        "status": "completed",
        "media_assets": [
            {
                "node_id": "shot-1",
                "node_type": "videoNode",
                "output_rel_path": "freezone/_outputs/freezone_video_gen/job-1.mp4",
                "duration_seconds": 5,
            }
        ],
    }
    beats = media_dispatch._workflow_compose_beats(run)
    assert beats == [
        {
            "beat_number": 1,
            "video_path": "freezone/_outputs/freezone_video_gen/job-1.mp4",
            "title": "镜头一",
            "duration_seconds": 5,
        }
    ]


def test_workflow_compose_beats_preserve_structured_audio_contract(tmp_path: Path):
    run = _run(tmp_path=tmp_path)
    run["artifacts"]["story_and_shots"]["plan"]["shots"] = [
        {
            "title": "对白镜头",
            "dialogue_text": "我知道了。",
            "spoken_dialogue": ["我知道了。"],
            "speaker": "角色A",
            "audio_type": "dialogue",
            "native_audio_strategy": "external",
            "audio_asset_ref": "audio/role-a.wav",
        }
    ]
    run["artifacts"]["media_generation"] = {
        "status": "completed",
        "media_assets": [
            {
                "node_id": "shot-1",
                "node_type": "videoNode",
                "output_rel_path": "freezone/_outputs/freezone_video_gen/job-1.mp4",
                "duration_seconds": 5,
            }
        ],
    }

    beats = media_dispatch._workflow_compose_beats(run)

    assert beats[0]["dialogue_text"] == "我知道了。"
    assert beats[0]["spoken_dialogue"] == ["我知道了。"]
    assert beats[0]["speaker"] == "角色A"
    assert beats[0]["audio_type"] == "dialogue"
    assert beats[0]["native_audio_strategy"] == "external"
    assert beats[0]["audio_asset_ref"] == "audio/role-a.wav"


def test_compose_media_path_rejects_escape_and_accepts_project_relative(tmp_path: Path):
    output = tmp_path / "output"
    output.mkdir()
    media = output / "freezone" / "job.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"video")
    resolved = _resolve_compose_media_path(
        output,
        output / "legacy.mp4",
        {"video_path": "freezone/job.mp4"},
        media_kind="video",
    )
    assert resolved == media.resolve()
    with pytest.raises(RuntimeError, match="越出项目目录"):
        _resolve_compose_media_path(
            output,
            output / "legacy.mp4",
            {"video_path": "../outside.mp4"},
            media_kind="video",
        )


@pytest.mark.asyncio
async def test_delivery_keeps_draft_final_film_deferred(monkeypatch, tmp_path: Path):
    run = _run(tmp_path=tmp_path, run_mode="draft")
    run["inputs"]["auto_generate_paid_media"] = False
    called = False

    async def unexpected(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("compose must stay deferred")

    monkeypatch.setattr(workflow_executor, "dispatch_workflow_compose", unexpected)
    result = await workflow_executor._delivery_handler(run, {"id": "delivery"})

    assert result.event_type == "step_completed"
    assert result.payload["shot_count"] == 1
    assert called is False


def test_final_film_compose_requires_auto_media_gate():
    draft = {
        "run_mode": "draft",
        "inputs": {
            "auto_generate_paid_media": True,
            "director_intent_contract": {"delivery_level": "final_film"},
        },
    }
    assert workflow_executor._final_film_compose_requested(draft) is False
    auto = {
        "run_mode": "auto",
        "inputs": {
            "auto_generate_paid_media": True,
            "director_intent_contract": {"delivery_level": "final_film"},
        },
    }
    assert workflow_executor._final_film_compose_requested(auto) is True


def test_final_film_compose_requires_explicit_episode_scope():
    with pytest.raises(ValueError, match="episode_scope"):
        media_dispatch._compose_episode_scope({"inputs": {}})


@pytest.mark.asyncio
async def test_delivery_dispatches_auto_final_film_once(monkeypatch, tmp_path: Path):
    run = _run(tmp_path=tmp_path, run_mode="auto")
    calls: list[dict] = []

    async def fake_dispatch(current, *, state_dir, step_id):
        calls.append({"run": current, "state_dir": state_dir, "step_id": step_id})
        return {
            "kind": "compose_episode",
            "status": "monitoring",
            "task_id": "compose-task-1",
            "task_key": "compose-key-1",
            "episode": 2,
        }

    monkeypatch.setattr(workflow_executor, "dispatch_workflow_compose", fake_dispatch)
    first = await workflow_executor._delivery_handler(run, {"id": "delivery"})

    assert first.event_type == "step_progress"
    assert first.payload["kind"] == "compose_episode"
    assert calls[0]["step_id"] == "delivery"


@pytest.mark.asyncio
async def test_compose_reconcile_materializes_verified_artifact(monkeypatch, tmp_path: Path):
    run = _run(tmp_path=tmp_path)
    ctx = run["_test_context"]
    output = ctx.output_dir / "videos" / "episodes" / "ep002_final.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"fake-mp4")
    task = SimpleNamespace(
        status="completed",
        progress=1.0,
        result={"video_path": str(output)},
        error="",
        metadata={},
    )
    monkeypatch.setattr(media_dispatch, "resolve_workflow_project_context", lambda _run: _async_value(ctx))
    monkeypatch.setattr(media_dispatch, "get_task_manager", lambda: SimpleNamespace(get_task_for_project=lambda *_args, **_kwargs: task))
    monkeypatch.setattr(media_dispatch, "_probe_video_metadata", lambda _path: {"width": 720, "height": 1280, "duration_seconds": 4.5})
    monkeypatch.setattr(media_dispatch, "make_static_url_for_context", lambda *_args, **_kwargs: "/static/ep002_final.mp4")

    observation = await media_dispatch.reconcile_workflow_compose(
        run,
        state_dir=ctx.state_dir,
        step_id="delivery",
        artifact={
            "kind": "compose_episode",
            "task_type": "compose_episode",
            "task_id": "compose-task-1",
            "scope": "workflow:wfr-compose:compose",
            "episode": 2,
        },
    )

    assert observation["status"] == "completed"
    final = observation["final_compose_artifact"]
    assert final["relative_path"] == "videos/episodes/ep002_final.mp4"
    assert final["url"] == "/static/ep002_final.mp4"
    assert len(final["sha256"]) == 64
    assert final["width"] == 720
    assert final["height"] == 1280
    assert final["duration_seconds"] == 4.5


@pytest.mark.asyncio
async def test_compose_reconcile_rejects_completed_task_without_file(monkeypatch, tmp_path: Path):
    run = _run(tmp_path=tmp_path)
    ctx = run["_test_context"]
    task = SimpleNamespace(
        status="completed",
        progress=1.0,
        result={"video_path": str(ctx.output_dir / "videos" / "episodes" / "missing.mp4")},
        error="",
        metadata={},
    )
    monkeypatch.setattr(media_dispatch, "resolve_workflow_project_context", lambda _run: _async_value(ctx))
    monkeypatch.setattr(media_dispatch, "get_task_manager", lambda: SimpleNamespace(get_task_for_project=lambda *_args, **_kwargs: task))

    observation = await media_dispatch.reconcile_workflow_compose(
        run,
        state_dir=ctx.state_dir,
        step_id="delivery",
        artifact={
            "kind": "compose_episode",
            "task_type": "compose_episode",
            "task_id": "compose-task-1",
            "scope": "workflow:wfr-compose:compose",
            "episode": 2,
        },
    )

    assert observation["status"] == "failed"
    assert observation["error_code"] == "workflow_compose_artifact_missing"


@pytest.mark.asyncio
async def test_compose_reconcile_rejects_film_shorter_than_requested(
    monkeypatch, tmp_path: Path
):
    """要 30 秒却交出 2 秒，必须报失败，不许报完成。"""

    run = _run(tmp_path=tmp_path)
    run["inputs"]["request"] = "做一段 30 秒的打斗片"
    ctx = run["_test_context"]
    output = ctx.output_dir / "videos" / "episodes" / "ep002_final.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"fake-mp4")
    task = SimpleNamespace(
        status="completed",
        progress=1.0,
        result={"video_path": str(output)},
        error="",
        metadata={},
    )
    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", lambda _run: _async_value(ctx)
    )
    monkeypatch.setattr(
        media_dispatch,
        "get_task_manager",
        lambda: SimpleNamespace(get_task_for_project=lambda *_args, **_kwargs: task),
    )
    monkeypatch.setattr(
        media_dispatch,
        "_probe_video_metadata",
        lambda _path: {"width": 720, "height": 1280, "duration_seconds": 2.041},
    )
    monkeypatch.setattr(
        media_dispatch,
        "make_static_url_for_context",
        lambda *_args, **_kwargs: "/static/ep002_final.mp4",
    )
    monkeypatch.setattr(
        media_dispatch,
        "build_final_film_engineering_qc",
        lambda _path, **kwargs: {
            "passed": (
                abs((kwargs.get("expected_duration_seconds") or 0) - 2.041) <= 1.0
            ),
            "checks": {
                "duration": {
                    "passed": abs(
                        (kwargs.get("expected_duration_seconds") or 0) - 2.041
                    )
                    <= 1.0
                }
            },
        },
    )

    observation = await media_dispatch.reconcile_workflow_compose(
        run,
        state_dir=ctx.state_dir,
        step_id="delivery",
        artifact={
            "kind": "compose_episode",
            "task_type": "compose_episode",
            "task_id": "compose-task-1",
            "scope": "workflow:wfr-compose:compose",
            "episode": 2,
        },
    )

    assert observation["status"] == "failed"
    assert observation["error_code"] == "workflow_compose_duration_mismatch"


async def _async_value(value):
    return value


async def _run_persisted_at_delivery(tmp_path: Path) -> tuple[WorkflowRunStore, dict]:
    """Persist a real run with all pre-delivery steps already verified."""

    ctx = _context(tmp_path)
    definition = get_workflow_definition("one-click-film")
    assert definition is not None
    inputs = {
        "request": "生成第 2 集最终成片",
        "episode_scope": 2,
        "auto_generate_paid_media": True,
        "director_intent_contract": {
            "delivery_level": "final_film",
            "compose_required": True,
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
                "video_path": "videos/beats/ep002/beat_01.mp4",
                "duration_seconds": 4,
            }
        ],
    }
    store = WorkflowRunStore(ctx.state_dir)
    run, created = await store.create(
        definition=definition,
        project_id=ctx.project_id,
        canvas_id="canvas-1",
        run_mode="auto",
        inputs=inputs,
        idempotency_key=f"compose-advance-{tmp_path.name}",
        contract_version=2,
        project_context={
            "project_id": ctx.project_id,
            "canvas_id": "canvas-1",
            "requester_user_id": "local",
            "requester_username": "local",
        },
        model_plan_snapshot={
            "schema": "canvas_model_plan_snapshot.v1",
            "model_plan_revision": "compose-test-model-plan",
            "bindings": {
                "director": {"kind": "agent", "registry_id": "director-test"},
                "image": {"kind": "image", "registry_id": "image-test"},
                "vision": {"kind": "vision", "registry_id": "vision-test"},
            },
        },
    )
    assert created is False

    completed_payloads = {
        "canvas_structure": {"kind": "canvas_structure", "created_node_ids": ["root-1"]},
        "story_and_shots": {
            "kind": "storyboard_plan",
            "plan": {
                "title": "第 2 集",
                "shots": [{"title": "镜头一", "duration_seconds": 4}],
            },
        },
        "asset_slots": {"kind": "asset_slots", "slot_count": 1},
        "media_generation": {
            "kind": "media_batch",
            "media_assets": [
                {
                    "node_id": "shot-1",
                    "node_type": "videoNode",
                    "output_rel_path": "videos/beats/ep002/beat_01.mp4",
                    "duration_seconds": 4,
                }
            ],
        },
        "quality_review": {
            "kind": "quality_report",
            "passed": True,
        },
    }
    for step_id, payload in completed_payloads.items():
        run, applied = await store.record_event(
            run["id"],
            event_id=f"prepare-delivery:{step_id}",
            event_type="step_completed",
            step_id=step_id,
            success=True,
            payload=payload,
            expected_revision=run["revision"],
        )
        assert applied is True
        assert run is not None
    assert run["current_frontier"] == ["delivery"]
    assert run["step_states"]["delivery"]["status"] == "running"
    return store, run


@pytest.mark.asyncio
async def test_executor_advance_composes_once_and_completes_from_persisted_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    store, run = await _run_persisted_at_delivery(tmp_path)
    dispatch_calls: list[dict] = []
    reconcile_calls = 0
    final_artifact = {
        "schema": "workflow_final_compose_artifact.v1",
        "kind": "final_compose_artifact",
        "task_id": "compose-task-1",
        "relative_path": "videos/episodes/ep002_final.mp4",
        "sha256": "a" * 64,
    }

    async def fake_dispatch(current, *, state_dir, step_id):
        dispatch_calls.append(
            {"run_id": current["id"], "state_dir": state_dir, "step_id": step_id}
        )
        return {
            "kind": "compose_episode",
            "task_type": "compose_episode",
            "status": "monitoring",
            "upstream_status": "queued",
            "task_id": "compose-task-1",
            "task_key": "compose-key-1",
            "episode": 2,
            "scope": f"workflow:{current['id']}:compose",
        }

    async def fake_reconcile(*_args, **_kwargs):
        nonlocal reconcile_calls
        reconcile_calls += 1
        if reconcile_calls < 3:
            return {
                "kind": "compose_episode",
                "status": "monitoring",
                "progress": 0.4,
                "message": "合成任务运行中",
            }
        return {
            "kind": "compose_episode",
            "status": "completed",
            "progress": 1.0,
            "final_compose_artifact": final_artifact,
        }

    monkeypatch.setattr(workflow_executor, "dispatch_workflow_compose", fake_dispatch)
    monkeypatch.setattr(workflow_executor, "reconcile_workflow_compose", fake_reconcile)

    first = await workflow_executor.WorkflowExecutor(store).advance(run["id"])

    assert first is not None
    assert first["status"] == "running"
    assert first["current_frontier"] == ["delivery"]
    assert first["artifacts"]["delivery"]["status"] == "monitoring"
    assert dispatch_calls == [
        {
            "run_id": run["id"],
            "state_dir": store.state_dir,
            "step_id": "delivery",
        }
    ]

    # A second advance only reconciles the existing task; it must not enqueue it again.
    completed = await workflow_executor.WorkflowExecutor(store).advance(run["id"])

    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["current_frontier"] == []
    assert completed["artifacts"]["delivery"]["final_compose_artifact"] == final_artifact
    assert completed["step_states"]["delivery"]["status"] == "completed"
    assert len(dispatch_calls) == 1
    assert reconcile_calls == 3


@pytest.mark.asyncio
async def test_executor_compose_failure_can_retry_without_parallel_enqueue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    store, run = await _run_persisted_at_delivery(tmp_path)
    dispatch_calls = 0
    reconcile_calls = 0

    async def fake_dispatch(*_args, **_kwargs):
        nonlocal dispatch_calls
        dispatch_calls += 1
        return {
            "kind": "compose_episode",
            "task_type": "compose_episode",
            "status": "monitoring",
            "task_id": f"compose-task-{dispatch_calls}",
            "task_key": f"compose-key-{dispatch_calls}",
            "episode": 2,
            "scope": f"workflow:{run['id']}:compose",
        }

    async def fake_reconcile(*_args, **_kwargs):
        nonlocal reconcile_calls
        reconcile_calls += 1
        if reconcile_calls == 1:
            return {
                "kind": "compose_episode",
                "status": "failed",
                "error_code": "workflow_compose_task_failed",
                "error": "上游合成任务失败",
            }
        return {
            "kind": "compose_episode",
            "status": "completed",
            "progress": 1.0,
            "final_compose_artifact": {
                "kind": "final_compose_artifact",
                "task_id": "compose-task-2",
                "relative_path": "videos/episodes/ep002_final.mp4",
                "sha256": "b" * 64,
            },
        }

    monkeypatch.setattr(workflow_executor, "dispatch_workflow_compose", fake_dispatch)
    monkeypatch.setattr(workflow_executor, "reconcile_workflow_compose", fake_reconcile)
    executor = workflow_executor.WorkflowExecutor(store)

    failed = await executor.advance(run["id"])

    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["error_code"] == "workflow_compose_task_failed"
    assert failed["step_states"]["delivery"]["status"] == "failed"
    assert dispatch_calls == 1

    retried, applied = await store.record_event(
        run["id"],
        event_id="manual-compose-retry",
        event_type="step_retried",
        step_id="delivery",
        payload={"retry_scope": "whole_step"},
        expected_revision=failed["revision"],
    )
    assert applied is True
    assert retried is not None
    assert retried["status"] == "running"
    assert retried["current_frontier"] == ["delivery"]

    completed = await executor.advance(run["id"])

    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["artifacts"]["delivery"]["final_compose_artifact"]["task_id"] == "compose-task-2"
    assert dispatch_calls == 2
    assert reconcile_calls == 2
