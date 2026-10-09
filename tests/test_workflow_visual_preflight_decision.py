from dataclasses import replace
import hashlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from novelvideo.api.routes import workflows
from novelvideo.workflow_runtime import freezone_videos
from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.workflow_runtime.production_plan import build_production_plan
from novelvideo.workflow_runtime.script_asset_ledger import build_script_asset_ledger
from novelvideo.workflow_runtime.store import WorkflowRunConflictError, WorkflowRunStore


@pytest.mark.asyncio
async def test_visual_decision_is_persisted_scoped_and_resumes_without_retry(tmp_path):
    definition = get_workflow_definition("freezone-shot-videos")
    shot = next(step for step in definition.steps if step.id == "shot_videos")
    definition = replace(definition, steps=(replace(shot, depends_on=()),))
    store = WorkflowRunStore(tmp_path)
    run, _ = await store.create(
        definition=definition,
        project_id="p",
        canvas_id="c",
        run_mode="auto",
        inputs={},
        idempotency_key="test",
    )
    run, _ = await store.record_event(
        run["id"],
        event_id="start",
        event_type="step_started",
        step_id="shot_videos",
        source="executor",
    )
    run, _ = await store.record_event(
        run["id"],
        event_id="failure",
        event_type="step_failed",
        step_id="shot_videos",
        source="executor",
        error="需要核对",
        payload={
            "visual_preflight": {
                "reports": [
                    {
                        "shot_id": "s1",
                        "input_fingerprint": "a" * 64,
                        "status": "check_unavailable",
                        "original_prompt": "原动作",
                        "revised_motion_prompt": "",
                    }
                ]
            }
        },
    )
    attempt = run["step_states"]["shot_videos"]["attempt"]
    with pytest.raises(WorkflowRunConflictError):
        await store.record_event(
            run["id"],
            event_id="stale",
            event_type="visual_preflight_decided",
            step_id="shot_videos",
            source="executor",
            payload={
                "shot_id": "s1",
                "input_fingerprint": "b" * 64,
                "decision": "keep_original",
            },
        )
    assert (await store.get(run["id"]))["status"] == "failed"
    updated, applied = await store.record_event(
        run["id"],
        event_id="decision",
        event_type="visual_preflight_decided",
        step_id="shot_videos",
        source="executor",
        expected_revision=run["revision"],
        payload={
            "shot_id": "s1",
            "input_fingerprint": "a" * 64,
            "decision": "keep_original",
        },
    )
    assert applied and updated["status"] == "running"
    assert updated["step_states"]["shot_videos"]["attempt"] == attempt
    assert (
        updated["artifacts"]["shot_videos"]["visual_preflight"]["reports"][0][
            "decision"
        ]
        == "keep_original"
    )
    replay, applied = await store.record_event(
        run["id"],
        event_id="decision",
        event_type="visual_preflight_decided",
        step_id="shot_videos",
        source="executor",
        expected_revision=run["revision"],
        payload={},
    )
    assert not applied and replay["revision"] == updated["revision"]


@pytest.mark.asyncio
async def test_untrusted_event_cannot_bypass_visual_decision(tmp_path):
    definition = get_workflow_definition("freezone-shot-videos")
    store = WorkflowRunStore(tmp_path)
    run, _ = await store.create(
        definition=definition,
        project_id="p",
        canvas_id="c",
        run_mode="auto",
        inputs={},
        idempotency_key="untrusted",
    )
    with pytest.raises(WorkflowRunConflictError, match="not trusted"):
        await store.record_event(
            run["id"],
            event_id="attack",
            event_type="visual_preflight_decided",
            step_id="shot_videos",
            source="external",
            payload={"decision": "keep_original"},
        )


async def _ready_run(tmp_path):
    definition = get_workflow_definition("freezone-shot-videos")
    steps = []
    for step_id in (
        "script_contract",
        "production_plan",
        "storyboard_images",
        "shot_videos",
    ):
        step = next(step for step in definition.steps if step.id == step_id)
        steps.append(replace(step, depends_on=(steps[-1].id,) if steps else ()))
    store = WorkflowRunStore(tmp_path / "state")
    output = tmp_path / "output"
    output.mkdir()
    frame = output / "frame.png"
    Image.new("RGB", (32, 32), "white").save(frame)
    row = {
        "shot_id": "s1",
        "shot_no": "1",
        "video_motion_prompt": "手腕轻轻扇动蒲扇。",
        "duration": 4,
    }
    run, _ = await store.create(
        definition=replace(definition, steps=tuple(steps)),
        project_id="p",
        canvas_id="c",
        run_mode="auto",
        inputs={"auto_generate_paid_media": True},
        idempotency_key="ready",
    )
    artifacts = {
        "script_contract": {
            "status": "completed",
            "rows": [row],
            "asset_ledger": build_script_asset_ledger([row]),
        },
        "storyboard_images": {
            "status": "completed",
            "shot_count": 1,
            "completed_count": 1,
            "images": [
                {
                    "shot_id": "s1",
                    "shot_no": "1",
                    "shot_index": 0,
                    "output_path": str(frame),
                    "sha256": hashlib.sha256(frame.read_bytes()).hexdigest(),
                }
            ],
        },
    }
    for step_id in ("script_contract", "production_plan", "storyboard_images"):
        payload = (
            build_production_plan(run)
            if step_id == "production_plan"
            else artifacts[step_id]
        )
        run, _ = await store.record_event(
            run["id"],
            event_id=f"start-{step_id}",
            event_type="step_started",
            step_id=step_id,
            source="executor",
        )
        run, _ = await store.record_event(
            run["id"],
            event_id=f"done-{step_id}",
            event_type="step_completed",
            step_id=step_id,
            source="executor",
            payload=payload,
        )
    return (
        store,
        run,
        SimpleNamespace(output_dir=output, state_dir=store.state_dir, project_id="p"),
        frame,
    )


@pytest.mark.asyncio
async def test_visual_decision_http_checks_real_frame_revision_and_replay(
    tmp_path, monkeypatch
):
    store, run, ctx, frame = await _ready_run(tmp_path)
    source = freezone_videos._shot_sources(run)[0][0]
    fingerprint = freezone_videos._preflight_fingerprint(run, source)
    run, _ = await store.record_event(
        run["id"],
        event_id="start-video",
        event_type="step_started",
        step_id="shot_videos",
        source="executor",
    )
    run, _ = await store.record_event(
        run["id"],
        event_id="fail-video",
        event_type="step_failed",
        step_id="shot_videos",
        source="executor",
        error="未验证",
        payload={
            "visual_preflight": {
                "revision": freezone_videos.VISUAL_PREFLIGHT_REVISION,
                "reports": [
                    {
                        "shot_id": "s1",
                        "input_fingerprint": fingerprint,
                        "status": "check_unavailable",
                        "original_prompt": source["prompt"],
                        "source_image_sha256": source["source_image_sha256"],
                    }
                ],
            }
        },
    )

    async def scope(*_args, **_kwargs):
        return ctx

    scheduled = []
    monkeypatch.setattr(workflows, "_scope", scope)
    monkeypatch.setattr(
        workflows, "_service", lambda *_args: SimpleNamespace(store=store)
    )
    monkeypatch.setattr(
        workflows,
        "schedule_workflow_run",
        lambda _store, run_id: scheduled.append(run_id),
    )
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: {"user_id": "local"}
    endpoint = f"/api/v1/projects/p/workflow-runs/{run['id']}/visual-preflight-decision"
    body = {
        "canvas_id": "c",
        "shot_id": "s1",
        "input_fingerprint": fingerprint,
        "decision": "keep_original",
        "command_id": "decision",
        "expected_run_revision": run["revision"],
    }
    with TestClient(app) as client:
        assert (
            client.post(endpoint, json={**body, "canvas_id": "wrong"}).status_code
            == 409
        )
        assert (
            client.post(
                endpoint, json={**body, "expected_run_revision": run["revision"] - 1}
            ).status_code
            == 409
        )
        assert (
            client.post(
                endpoint, json={**body, "input_fingerprint": "b" * 64}
            ).status_code
            == 409
        )
        assert (
            client.post(
                endpoint, json={**body, "decision": "accept_suggestion"}
            ).status_code
            == 409
        )
        original_bytes = frame.read_bytes()
        Image.new("RGB", (32, 32), "black").save(frame)
        assert client.post(endpoint, json=body).status_code == 409
        frame.write_bytes(original_bytes)
        response = client.post(endpoint, json=body)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["status"] == "running"
        assert client.post(endpoint, json=body).status_code == 200
        assert (
            client.post(
                endpoint, json={**body, "decision": "accept_suggestion"}
            ).status_code
            == 409
        )
    resumed = await WorkflowRunStore(store.state_dir).get(run["id"])
    assert (
        resumed["artifacts"]["shot_videos"]["visual_preflight"]["reports"][0][
            "decision"
        ]
        == "keep_original"
    )
    assert (
        resumed["step_states"]["shot_videos"]["attempt"]
        == run["step_states"]["shot_videos"]["attempt"]
    )
    assert scheduled
