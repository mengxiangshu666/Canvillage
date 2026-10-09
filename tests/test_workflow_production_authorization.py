from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from novelvideo.workflow_runtime import service as workflow_service
from novelvideo.workflow_runtime import freezone_final_film
from novelvideo.workflow_runtime import freezone_storyboard
from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.workflow_runtime.freezone_final_film import _require_authorized
from novelvideo.workflow_runtime.production_authorization import (
    PRODUCTION_AUTHORIZATION_SCHEMA,
    normalize_production_authorization,
    production_authorization_allows_final_film,
    production_authorization_allows_storyboard,
)
from novelvideo.workflow_runtime.service import (
    WorkflowConfigurationError,
    WorkflowRuntimeService,
)
from novelvideo.workflow_runtime.step_contract import WorkflowStepExecutionError
from novelvideo.workflow_runtime.store import (
    WorkflowRunConflictError,
    WorkflowRunStore,
)
from workflow_plan_support import install_production_plan


def _authorization(**overrides):
    value = {
        "schema": PRODUCTION_AUTHORIZATION_SCHEMA,
        "scope": "workflow_run",
        "project_id": "project-1",
        "canvas_id": "canvas-1",
        "source_turn_id": "turn-1",
        "source": "server_turn_grant",
        "max_paid_starts": 2,
        "max_shots": 4,
        "max_reference_images": 36,
        "max_duration_seconds": 60,
        "allow_final_film": True,
    }
    value.update(overrides)
    return value


def _run(*, videos: list[dict] | None = None, **overrides):
    run = {
        "id": "wfr_production_authorization",
        "workflow_id": "freezone-final-film",
        "run_mode": "auto",
        "project_id": "project-1",
        "canvas_id": "canvas-1",
        "source_turn_id": "turn-1",
        "inputs": {
            "auto_generate_paid_media": True,
            "production_authorization": _authorization(),
        },
        "artifacts": {
            "shot_videos": {
                "status": "completed",
                "shot_count": len(videos or []),
                "result_signature": "a" * 64,
                "videos": videos or [],
            },
            "final_film": {},
        },
    }
    run.update(overrides)
    install_production_plan(run)
    return run


def test_normalize_production_authorization_is_canonical_and_scope_bound() -> None:
    normalized = normalize_production_authorization(
        _authorization(extra="discarded"),
        workflow_id="freezone-final-film",
        run_mode="auto",
        project_id="project-1",
        canvas_id="canvas-1",
        source_turn_id="turn-1",
        auto_generate_paid_media=True,
    )

    assert normalized == {
        **_authorization(),
        "workflow_id": "freezone-final-film",
    }
    with pytest.raises(ValueError, match="canvas"):
        normalize_production_authorization(
            _authorization(canvas_id="canvas-2"),
            workflow_id="freezone-final-film",
            run_mode="auto",
            project_id="project-1",
            canvas_id="canvas-1",
            source_turn_id="turn-1",
            auto_generate_paid_media=True,
        )
    with pytest.raises(ValueError, match="source turn"):
        normalize_production_authorization(
            _authorization(),
            workflow_id="freezone-final-film",
            run_mode="auto",
            project_id="project-1",
            canvas_id="canvas-1",
            source_turn_id="",
            auto_generate_paid_media=True,
        )


def test_final_film_accepts_run_authorization_within_shot_and_duration_limits() -> None:
    run = _run(
        videos=[
            {"shot_id": "shot-1", "duration_seconds": 4.0},
            {"shot_id": "shot-2", "duration_seconds": 5.0},
        ]
    )

    allowed, reason = production_authorization_allows_final_film(
        run,
        shot_count=2,
        duration_seconds=9.0,
    )
    assert allowed is True
    assert reason == "production_authorization"
    _require_authorized(run, step_id="final_film")


def test_final_film_rejects_run_authorization_outside_limits() -> None:
    run = _run(
        videos=[
            {"shot_id": "shot-1", "duration_seconds": 4.0},
            {"shot_id": "shot-2", "duration_seconds": 5.0},
            {"shot_id": "shot-3", "duration_seconds": 6.0},
        ],
        inputs={
            "auto_generate_paid_media": True,
            "production_authorization": _authorization(max_shots=2),
        },
    )

    with pytest.raises(WorkflowStepExecutionError) as raised:
        _require_authorized(run, step_id="final_film")

    assert raised.value.code == "workflow_final_film_not_authorized"
    assert (
        raised.value.details["reason"]
        == "production_authorization_shot_limit_exceeded"
    )


def test_storyboard_authorization_rejects_shot_and_reference_limits() -> None:
    run = _run(
        inputs={
            "auto_generate_paid_media": True,
            "production_authorization": _authorization(
                max_shots=2,
                max_reference_images=3,
            ),
        }
    )

    allowed, reason = production_authorization_allows_storyboard(
        run,
        shot_count=2,
        reference_count=3,
    )
    assert allowed is True
    assert reason == "production_authorization"

    allowed, reason = production_authorization_allows_storyboard(
        run,
        shot_count=3,
        reference_count=1,
    )
    assert allowed is False
    assert reason == "workflow_production_authorization_shot_limit_exceeded"

    allowed, reason = production_authorization_allows_storyboard(
        run,
        shot_count=2,
        reference_count=4,
    )
    assert allowed is False
    assert reason == "workflow_production_authorization_reference_limit_exceeded"


@pytest.mark.asyncio
async def test_storyboard_rejects_reference_limit_before_model_or_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run(
        inputs={
            "auto_generate_paid_media": True,
            "production_authorization": _authorization(
                max_shots=1,
                max_reference_images=1,
            ),
        }
    )

    def script_rows(_run):
        return (
            [{"shot_id": "shot-1", "shot_prompt": "镜头一"}],
            {"status": "completed"},
            {"assets": []},
        )

    def current(*_args, **_kwargs):
        return {"status": "completed"}

    async def binding(*_args, **_kwargs):
        return {
            "ledger": {
                "assets": [
                    {
                        "asset_id": "character:a",
                        "readiness": "missing",
                    },
                    {
                        "asset_id": "character:b",
                        "readiness": "missing",
                    },
                ]
            }
        }

    monkeypatch.setattr(freezone_storyboard, "_script_rows", script_rows)
    monkeypatch.setattr(
        freezone_storyboard,
        "ensure_reused_canvas_script_contract_current",
        current,
    )
    monkeypatch.setattr(
        freezone_storyboard,
        "_resolve_canvas_asset_binding",
        binding,
    )
    monkeypatch.setattr(
        freezone_storyboard,
        "_image_model",
        lambda _run: (_ for _ in ()).throw(
            AssertionError("model resolution must stay behind authorization")
        ),
    )

    with pytest.raises(WorkflowStepExecutionError) as raised:
        await freezone_storyboard.dispatch_workflow_storyboard_images(
            run,
            state_dir=Path(""),
            step_id="storyboard_images",
        )

    assert (
        raised.value.code
        == "workflow_production_authorization_reference_limit_exceeded"
    )
    assert raised.value.details["reference_count"] == 2
    assert raised.value.details["max_reference_images"] == 1


def test_final_film_auto_media_requires_run_authorization() -> None:
    run = {
        "workflow_id": "freezone-final-film",
        "run_mode": "auto",
        "inputs": {"auto_generate_paid_media": True},
    }

    with pytest.raises(WorkflowStepExecutionError) as raised:
        freezone_storyboard._require_paid_media_authorization(run)

    assert raised.value.code == "workflow_storyboard_paid_media_not_authorized"
    assert raised.value.details["reason"] == "production_authorization_missing"


@pytest.mark.asyncio
async def test_workflow_service_seals_run_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def binding(role: str, kind: str) -> dict:
        return {
            "role": role,
            "kind": kind,
            "registry_id": f"{kind}-test",
            "catalog_id": f"direct/{kind}-test",
            "upstream_model": f"{kind}-upstream-test",
            "protocol": "openai-compatible",
            "endpoint_fingerprint": f"endpoint-{kind}",
            "capability_revision": "direct-model-contract.v2",
            "capabilities": {"runtime_ready": True},
        }

    monkeypatch.setattr(
        workflow_service,
        "build_model_plan_snapshot",
        lambda _bindings=None: {
            "schema": "canvas_model_plan_snapshot.v1",
            "model_plan_revision": "direct-model-plan.v1",
            "bindings": {
                "director": binding("director", "agent"),
                "image": binding("image", "image"),
                "vision": binding("vision", "vision"),
                "video": binding("video", "video"),
            },
            "missing_roles": ["audio", "embedding"],
            "fallback_policy": "explicit-only",
        },
    )
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")

    run, reused = await service.start(
        workflow_id="freezone-final-film",
        canvas_id="canvas-1",
        run_mode="auto",
        inputs={
            "request": "用当前脚本一次授权做成片",
            "director_intent_contract": {"delivery_level": "final_film"},
            "media_start_budget": 2,
            "auto_generate_paid_media": True,
            "production_authorization": _authorization(),
        },
        idempotency_key="production-authorization-start",
        contract_version=2,
        source_turn_id="turn-1",
    )

    assert reused is False
    assert run["inputs"]["production_authorization"] == {
        **_authorization(),
        "workflow_id": "freezone-final-film",
    }
    assert run["inputs"]["auto_generate_paid_media"] is True


@pytest.mark.asyncio
async def test_workflow_service_rejects_forged_run_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflow_service,
        "build_model_plan_snapshot",
        lambda _bindings=None: {
            "schema": "canvas_model_plan_snapshot.v1",
            "model_plan_revision": "direct-model-plan.v1",
            "bindings": {},
            "missing_roles": [],
            "fallback_policy": "explicit-only",
        },
    )
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")

    with pytest.raises(WorkflowConfigurationError) as raised:
        await service.start(
            workflow_id="freezone-final-film",
            canvas_id="canvas-1",
            run_mode="auto",
            inputs={
                "request": "伪造授权",
                "media_start_budget": 2,
                "auto_generate_paid_media": True,
                "production_authorization": _authorization(
                    source_turn_id="another-turn"
                ),
            },
            idempotency_key="production-authorization-forged",
            contract_version=2,
            source_turn_id="turn-1",
        )

    assert raised.value.code == "workflow_production_authorization_invalid"


@pytest.mark.asyncio
async def test_paid_start_budget_is_persistent_idempotent_and_atomic(
    tmp_path: Path,
) -> None:
    definition = get_workflow_definition("freezone-final-film")
    assert definition is not None
    store = WorkflowRunStore(tmp_path)
    run, reused = await store.create(
        definition=definition,
        project_id="project-1",
        canvas_id="canvas-1",
        run_mode="auto",
        inputs={
            "request": "两槽付费预算",
            "auto_generate_paid_media": True,
            "production_authorization": _authorization(max_paid_starts=2),
        },
        idempotency_key="paid-start-budget",
        contract_version=2,
        source_turn_id="turn-1",
    )
    assert reused is False

    reservations = await asyncio.gather(
        *(
            store.reserve_paid_start(
                run["id"],
                step_id="storyboard_images",
                item_id=f"job-{index}",
                provider_kind="image",
            )
            for index in range(6)
        ),
        return_exceptions=True,
    )
    allowed = [item for item in reservations if isinstance(item, dict)]
    denied = [
        item for item in reservations if isinstance(item, WorkflowRunConflictError)
    ]
    assert len(allowed) == 2
    assert len(denied) == 4
    assert {item.code for item in denied} == {
        "workflow_paid_start_budget_exceeded"
    }

    replay = await WorkflowRunStore(tmp_path).reserve_paid_start(
        run["id"],
        step_id="storyboard_images",
        item_id=allowed[0]["reservation"]["item_id"],
        provider_kind="image",
    )
    assert replay["reused"] is True
    assert replay["used"] == 2
    assert len(await store.list_paid_starts(run["id"])) == 2


@pytest.mark.asyncio
async def test_final_film_dispatches_once_with_run_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run(
        videos=[
            {"shot_id": "shot-1", "duration_seconds": 4.0},
            {"shot_id": "shot-2", "duration_seconds": 5.0},
        ]
    )
    calls = 0

    async def ready(_run: dict) -> None:
        return None

    async def dispatch(_run: dict, *, state_dir: Path, step_id: str):
        nonlocal calls
        calls += 1
        assert state_dir == Path("")
        assert step_id == "final_film"
        return {"status": "monitoring", "workflow_step_id": step_id}

    monkeypatch.setattr(freezone_final_film, "_require_ready_input", ready)
    monkeypatch.setattr(
        freezone_final_film,
        "dispatch_workflow_compose",
        dispatch,
    )

    result = await freezone_final_film.handle_workflow_final_film(
        run,
        {"id": "final_film"},
    )

    assert calls == 1
    assert result.event_type == "step_progress"
    assert run["artifacts"]["final_film"] == {}
