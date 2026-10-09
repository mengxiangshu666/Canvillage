from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import workflows
from novelvideo.chat.approval_store import (
    consume_paid_media_grant,
    register_paid_media_grant,
)
from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.workflow_runtime.freezone_storyboard import (
    _require_paid_media_authorization as require_storyboard_authorization,
)
from novelvideo.workflow_runtime.freezone_videos import (
    _require_paid_media_authorization as require_video_authorization,
)
from novelvideo.workflow_runtime.service import WorkflowRuntimeService
from novelvideo.workflow_runtime.step_contract import WorkflowStepExecutionError
from novelvideo.workflow_runtime.store import WorkflowRunConflictError
from workflow_plan_support import complete_script_and_production_plan


async def _failed_storyboard_run(
    service: WorkflowRuntimeService,
    *,
    failed_item_retry: bool = False,
) -> dict:
    definition = get_workflow_definition("freezone-final-film")
    assert definition is not None
    run, reused = await service.store.create(
        definition=definition,
        project_id="demo",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"auto_generate_paid_media": False},
        idempotency_key="t085-media-authorization",
        contract_version=2,
        project_context={"requester_user_id": "tester"},
        model_plan_snapshot={},
    )
    assert reused is False

    run = await complete_script_and_production_plan(
        service.store,
        run,
        prefix="t085",
    )
    run, _ = await service.store.record_event(
        run["id"],
        event_id="t085-storyboard-started",
        event_type="step_started",
        step_id="storyboard_images",
        expected_revision=run["revision"],
    )
    assert run is not None
    error_code = (
        "workflow_storyboard_image_failed"
        if failed_item_retry
        else "workflow_storyboard_paid_media_not_authorized"
    )
    recovery = {
        "schema": "workflow_step_recovery.v1",
        "workflow_run_id": run["id"],
        "step_id": "storyboard_images",
        "error_code": error_code,
        "action": (
            "retry_failed_items"
            if failed_item_retry
            else "request_media_authorization"
        ),
        "next_action": (
            "recover:retry_failed_items:storyboard_images"
            if failed_item_retry
            else "recover:request_media_authorization:storyboard_images"
        ),
        "rerun_scope": (
            "failed_items_only" if failed_item_retry else "current_step"
        ),
        "item_ids": ["shot-1"] if failed_item_retry else [],
        "job_ids": [],
        "requires_paid_media": True,
        "auto_retry_allowed": False,
        "instruction": (
            "只重试失败分镜 item。"
            if failed_item_retry
            else "先取得本轮媒体授权。"
        ),
    }
    failed_payload: dict = {
        "error_code": error_code,
        "recovery": recovery,
    }
    if failed_item_retry:
        failed_payload["item_states"] = {
            "shot-1": {
                "status": "failed",
                "label": "镜头 1",
                "error": "provider failed",
                "attempt": 1,
            }
        }
    run, _ = await service.store.record_event(
        run["id"],
        event_id="t085-storyboard-not-authorized",
        event_type="step_failed",
        step_id="storyboard_images",
        error=(
            "分镜图任务失败"
            if failed_item_retry
            else "当前运行未显式授权自动付费媒体"
        ),
        payload=failed_payload,
        expected_revision=run["revision"],
    )
    assert run is not None
    assert run["status"] == "failed"
    return run


def _media_marker(
    run: dict,
    *,
    grant_id: str,
    consume_key: str,
) -> dict:
    return {
        "schema": "workflow_media_authorization.v1",
        "authorization_id": grant_id,
        "project_id": "demo",
        "canvas_id": "canvas-1",
        "run_id": run["id"],
        "step_id": "storyboard_images",
        "error_code": "workflow_storyboard_paid_media_not_authorized",
        "recovery_action": "request_media_authorization",
        "retry_scope": "whole_step",
        "item_ids": [],
        "consume_key": consume_key,
        "source_revision": run["revision"],
    }


@pytest.mark.parametrize(
    ("step_id", "error_code", "require_authorization"),
    [
        (
            "storyboard_images",
            "workflow_storyboard_paid_media_not_authorized",
            require_storyboard_authorization,
        ),
        (
            "storyboard_images",
            "workflow_storyboard_image_failed",
            require_storyboard_authorization,
        ),
        (
            "shot_videos",
            "workflow_shot_video_paid_media_not_authorized",
            require_video_authorization,
        ),
        (
            "shot_videos",
            "workflow_shot_video_failed",
            require_video_authorization,
        ),
    ],
)
def test_media_handlers_accept_only_the_current_step_marker(
    step_id: str,
    error_code: str,
    require_authorization,
) -> None:
    run = {
        "id": "wfr-media-handler",
        "project_id": "demo",
        "canvas_id": "canvas-1",
        "run_mode": "draft",
        "inputs": {"auto_generate_paid_media": False},
        "artifacts": {
            step_id: {
                "media_authorization": {
                    "schema": "workflow_media_authorization.v1",
                    "authorization_id": "pmg_handler",
                    "project_id": "demo",
                    "canvas_id": "canvas-1",
                    "run_id": "wfr-media-handler",
                    "step_id": step_id,
                    "error_code": error_code,
                    "consume_key": "handler-consume",
                    "source_revision": 4,
                }
            }
        },
    }

    require_authorization(run)

    run["artifacts"][step_id]["media_authorization"]["run_id"] = "wfr-other"
    with pytest.raises(WorkflowStepExecutionError):
        require_authorization(run)


@pytest.mark.asyncio
async def test_runtime_persists_and_consumes_media_marker_once(
    tmp_path: Path,
) -> None:
    service = WorkflowRuntimeService(tmp_path, project_id="demo")
    run = await _failed_storyboard_run(service)
    marker = _media_marker(
        run,
        grant_id="pmg_runtime_test",
        consume_key="t085-runtime-consume",
    )

    with pytest.raises(WorkflowRunConflictError) as captured:
        await service.command(
            run["id"],
            command="retry",
            step_id="storyboard_images",
            idempotency_key="t085-runtime-unverified",
            media_authorization=marker,
        )
    assert captured.value.code == "workflow_media_authorization_not_verified"

    retried, applied = await service.command(
        run["id"],
        command="retry",
        step_id="storyboard_images",
        idempotency_key="t085-runtime-retry",
        media_authorization=marker,
        media_authorization_verified=True,
    )
    assert applied is True
    assert retried is not None
    assert (
        retried["artifacts"]["storyboard_images"]["media_authorization"]
        == marker
    )

    output, output_applied = await service.store.record_event(
        run["id"],
        event_id="t085-storyboard-progress",
        event_type="step_progress",
        step_id="storyboard_images",
        payload={"status": "monitoring"},
        expected_revision=retried["revision"],
    )
    assert output_applied is True
    assert output is not None
    assert "media_authorization" not in output["artifacts"]["storyboard_images"]

    replayed, replay_applied = await service.command(
        run["id"],
        command="retry",
        step_id="storyboard_images",
        idempotency_key="t085-runtime-retry",
        media_authorization=marker,
        media_authorization_verified=True,
    )
    assert replay_applied is False
    assert replayed is not None
    assert (
        "media_authorization"
        not in replayed["artifacts"]["storyboard_images"]
    )


@pytest.mark.asyncio
async def test_runtime_requires_exact_grant_for_paid_failed_item_retry(
    tmp_path: Path,
) -> None:
    service = WorkflowRuntimeService(tmp_path, project_id="demo")
    run = await _failed_storyboard_run(service, failed_item_retry=True)

    with pytest.raises(WorkflowRunConflictError) as missing:
        await service.command(
            run["id"],
            command="retry",
            step_id="storyboard_images",
            retry_scope="failed_items_only",
            item_ids=["shot-1"],
            idempotency_key="t115-runtime-missing-grant",
        )
    assert missing.value.code == "workflow_media_authorization_required"

    marker = _media_marker(
        run,
        grant_id="pmg_t115_runtime",
        consume_key="t115-runtime-consume",
    )
    marker.update(
        {
            "error_code": "workflow_storyboard_image_failed",
            "recovery_action": "retry_failed_items",
            "retry_scope": "failed_items_only",
            "item_ids": ["shot-other"],
        }
    )
    with pytest.raises(WorkflowRunConflictError) as forged:
        await service.command(
            run["id"],
            command="retry",
            step_id="storyboard_images",
            retry_scope="failed_items_only",
            item_ids=["shot-1"],
            idempotency_key="t115-runtime-forged-item",
            media_authorization=marker,
            media_authorization_verified=True,
        )
    assert forged.value.code == "workflow_media_authorization_not_requested"

    marker["item_ids"] = ["shot-1"]
    retried, applied = await service.command(
        run["id"],
        command="retry",
        step_id="storyboard_images",
        retry_scope="failed_items_only",
        item_ids=["shot-1"],
        idempotency_key="t115-runtime-item-retry",
        media_authorization=marker,
        media_authorization_verified=True,
    )
    assert applied is True
    assert retried is not None
    persisted = retried["artifacts"]["storyboard_images"]["media_authorization"]
    assert persisted == marker
    assert persisted["recovery_action"] == "retry_failed_items"
    assert persisted["retry_scope"] == "failed_items_only"
    assert persisted["item_ids"] == ["shot-1"]
    assert (
        retried["step_states"]["storyboard_images"]["retry_scope_used"]
        == "failed_items_only"
    )

    progressed, applied = await service.store.record_event(
        run["id"],
        event_id="t115-storyboard-progress",
        event_type="step_progress",
        step_id="storyboard_images",
        payload={"status": "monitoring"},
        expected_revision=retried["revision"],
    )
    assert applied is True
    assert progressed is not None
    assert (
        "media_authorization"
        not in progressed["artifacts"]["storyboard_images"]
    )


@pytest.mark.asyncio
async def test_http_media_authorization_revalidates_server_grant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    service = WorkflowRuntimeService(tmp_path, project_id="demo")
    run = await _failed_storyboard_run(service)
    grant = register_paid_media_grant(
        "tester",
        turn_id="turn-t085",
        project_id="demo",
        canvas_id="canvas-1",
        max_starts=1,
    )
    consume_key = "t085-http-consume"
    allowed, reason, _ = consume_paid_media_grant(
        "tester",
        grant_id=str(grant["id"]),
        project_id="demo",
        canvas_id="canvas-1",
        idempotency_key=consume_key,
    )
    assert allowed is True
    assert reason == "server_turn_grant"

    async def fake_scope(*_args, **_kwargs):
        return SimpleNamespace(
            state_dir=tmp_path,
            project_id="demo",
            requester_username="tester",
            owner_username="tester",
        )

    monkeypatch.setattr(workflows, "_scope", fake_scope)
    monkeypatch.setattr(workflows, "_service", lambda *_args, **_kwargs: service)
    monkeypatch.setattr(
        workflows,
        "schedule_workflow_run",
        lambda *_args, **_kwargs: None,
    )
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: {
        "username": "tester",
        "user_id": "user-1",
        "credential_kind": "agent_session",
    }
    client = TestClient(app)
    marker = _media_marker(
        run,
        grant_id=str(grant["id"]),
        consume_key=consume_key,
    )

    forged = client.post(
        f"/api/v1/projects/demo/workflow-runs/{run['id']}/command",
        json={
            "command": "retry",
            "step_id": "storyboard_images",
            "idempotency_key": "t085-http-forged",
            "media_authorization": {
                **marker,
                "authorization_id": "pmg_forged",
            },
        },
    )
    assert forged.status_code == 409
    assert (
        forged.json()["detail"]["code"]
        == "workflow_media_authorization_not_verified"
    )

    commanded = client.post(
        f"/api/v1/projects/demo/workflow-runs/{run['id']}/command",
        json={
            "command": "retry",
            "step_id": "storyboard_images",
            "idempotency_key": "t085-http-retry",
            "media_authorization": marker,
        },
    )
    assert commanded.status_code == 200, commanded.text
    assert commanded.json()["data"]["command_applied"] is True
