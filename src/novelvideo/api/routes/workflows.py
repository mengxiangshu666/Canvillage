"""Durable Agent workflow endpoints for infinite-canvas projects."""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from novelvideo.api.auth import get_api_user
from novelvideo.chat.approval_store import consume_paid_media_grant
from novelvideo.chat.agent_events import (
    workflow_event_agent_event,
    workflow_snapshot_agent_event,
)
from novelvideo.chat.workflow_turn_receipts import (
    reconcile_workflow_turn_receipt_async,
)
from novelvideo.freezone.canvas_command_gateway import (
    CanvasCommandError,
    CanvasCommandGateway,
)
from novelvideo.freezone import canvas_store, script_media_gateway
from novelvideo.freezone.script_contract import (
    SCRIPT_MEDIA_ACTION_SHOT_VIDEOS,
    SCRIPT_MEDIA_ACTION_STORYBOARD_IMAGES,
)
from novelvideo.project_context import (
    ProjectContext,
    require_project_home_node,
    resolve_project_context,
)
from novelvideo.workflow_runtime.schemas import (
    AgentActionProfileCreate,
    CanvasCommandApply,
    WorkflowCanvasAssetBindingReadiness,
    WorkflowCanvasAssetBindingRepair,
    WorkflowComposeAuthorizationConsume,
    WorkflowComposeAuthorizationCreate,
    WorkflowRunCommand,
    WorkflowRunCreate,
    WorkflowRunEventCreate,
    WorkflowVisualPreflightDecision,
)
from novelvideo.workflow_runtime.compose_authorization import (
    consume_compose_authorization,
    is_compose_source_signature,
    issue_compose_authorization,
)
from novelvideo.workflow_runtime.action_router import (
    ActionRoute,
    canvas_creation_batch,
    existing_node_mutation_batch,
    profile_from_mapping,
    route_action,
    single_atomic_canvas_creation_batch,
)
from novelvideo.workflow_runtime.target_resolver import (
    resolve_existing_targets,
    should_block_creation,
)
from novelvideo.workflow_runtime.director_ledger import (
    build_director_ledger,
    validate_director_ledger,
)
from novelvideo.production.director_intent import (
    DIRECTOR_INTENT_REQUIRED_FIELDS,
    build_director_intent_contract,
    validate_director_intent_contract,
)
from novelvideo.creative_execution.director_clarification import (
    DirectorClarificationRequiredError,
)
from novelvideo.workflow_runtime.executor import schedule_workflow_run
from novelvideo.workflow_runtime.canvas_recovery import (
    build_canvas_command_recovery,
)
from novelvideo.workflow_runtime.canvas_asset_binding_repair import (
    CANVAS_ASSET_BINDING_REPAIR_SCHEMA,
    canvas_asset_binding_repair_commands,
    plan_canvas_asset_binding_repairs,
    recovery_from_workflow_run,
)
from novelvideo.workflow_runtime.canvas_asset_binding_readiness import (
    build_media_authorization_request,
    build_step_recovery_update_payload,
    evaluate_canvas_asset_binding_readiness,
)
from novelvideo.workflow_runtime.service import (
    WorkflowConfigurationError,
    WorkflowDefinitionInvalidError,
    WorkflowDefinitionNotFoundError,
    WorkflowRuntimeService,
)
from novelvideo.workflow_runtime.release_readiness import (
    attach_workflow_run_release_readiness,
)
from novelvideo.workflow_runtime.store import WorkflowRunConflictError

router = APIRouter()

_WORKFLOW_STREAM_HEARTBEAT_SECONDS = 15.0
_READ_PROJECTION_CACHE_LIMIT = 1024
_READ_PROJECTION_SIGNATURES: OrderedDict[str, tuple[object, ...]] = OrderedDict()


def _read_projection_is_stale(
    ctx: ProjectContext,
    run: dict[str, Any],
) -> bool:
    """Avoid repeating read-triggered writes when a Run has not changed."""
    run_id = str(run.get("id") or "").strip()
    state_dir = str(getattr(ctx, "state_dir", "") or "").strip()
    if not run_id or not state_dir:
        return True
    key = f"{state_dir}::{run_id}"
    signature = (
        run.get("revision"),
        run.get("event_seq"),
        run.get("status"),
        run.get("updated_at"),
    )
    previous = _READ_PROJECTION_SIGNATURES.get(key)
    _READ_PROJECTION_SIGNATURES[key] = signature
    _READ_PROJECTION_SIGNATURES.move_to_end(key)
    while len(_READ_PROJECTION_SIGNATURES) > _READ_PROJECTION_CACHE_LIMIT:
        _READ_PROJECTION_SIGNATURES.popitem(last=False)
    return previous != signature


async def _prepare_workflow_run_read(
    ctx: ProjectContext,
    service: WorkflowRuntimeService,
    run: dict[str, Any],
) -> None:
    """Apply the shared read-side reconciliation rules before serialization."""

    if run.get("status") == "running":
        schedule_workflow_run(service.store, str(run["id"]))
    if _read_projection_is_stale(ctx, run):
        await service.sync_parent_lineage(run)
        await _reconcile_terminal_workflow_turn(ctx, run)


@router.post("/projects/{project}/freezone/canvases/{canvas_id}/actions:route")
async def route_canvas_action(
    project: str,
    canvas_id: str,
    payload: AgentActionProfileCreate,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "editor")
    raw_canvas = canvas_store.read_canvas(ctx.state_dir, canvas_id)
    canvas_nodes = (
        [
            node
            for node in (raw_canvas.get("nodes") or [])
            if isinstance(node, dict) and node.get("id")
        ]
        if isinstance(raw_canvas, dict)
        else []
    )
    canvas_node_ids = [str(node["id"]) for node in canvas_nodes]
    target_resolution = resolve_existing_targets(
        goal=payload.goal or payload.operation,
        operation=payload.operation,
        target_node_ids=payload.target_node_ids,
        commands=payload.commands,
        nodes=canvas_nodes,
        canvas_id=canvas_id,
        declared_target_strategy=payload.target_strategy,
        creation_reason=payload.creation_reason,
    )
    dependency_node_ids = {
        str(item).strip()
        for item in target_resolution.get("dependency_node_ids", [])
        if str(item or "").strip()
    }
    route = route_action(
        profile_from_mapping(
            payload.model_dump(),
            default_operation="agent_action",
        ),
        has_executable_commands=bool(payload.commands),
        existing_node_mutation_only=existing_node_mutation_batch(payload.commands),
        contains_creation=canvas_creation_batch(payload.commands),
        single_atomic_creation=single_atomic_canvas_creation_batch(payload.commands),
        existing_node_ids=[
            node_id for node_id in canvas_node_ids if node_id not in dependency_node_ids
        ],
    )
    if should_block_creation(
        target_strategy=payload.target_strategy,
        resolution=target_resolution,
    ):
        route = ActionRoute(
            lane="blocked",
            reason_code="existing_target_candidate_conflict",
            reason=(
                "当前画布已有高置信度匹配对象，不能按 create_missing 创建替代节点；"
                "请改为 reuse_existing 并绑定建议目标"
            ),
            requires_durable_run=False,
            requires_confirmation=False,
        )
    ledger = None
    if payload.director_ledger is not None:
        try:
            ledger = validate_director_ledger(payload.director_ledger)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "director_ledger_invalid", "message": str(exc)},
            ) from exc
    intent_contract = None
    if payload.director_intent_contract is not None:
        try:
            supplied = payload.director_intent_contract
            if not set(DIRECTOR_INTENT_REQUIRED_FIELDS) <= set(supplied):
                # The Agent states intent; the derived halves (required_assets,
                # quality_gates, graph_contract, contract_revision) are content
                # hashes and inference the model cannot produce. Compile them
                # the same way the director-plan compiler does instead of
                # answering 422 for a contract that was never authorable.
                supplied = build_director_intent_contract(
                    project_goal=payload.goal or payload.operation,
                    contract=supplied,
                )
            intent_contract = validate_director_intent_contract(supplied)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "director_intent_contract_invalid",
                    "message": str(exc),
                },
            ) from exc
    if ledger is None:
        ledger = build_director_ledger(
            goal=payload.goal or payload.operation,
            success_criteria=payload.success_criteria or [route.reason],
            action_profile=payload.model_dump(),
            action_route=route.to_dict(),
            assumptions=payload.assumptions,
            constraints=payload.constraints,
            unknowns=payload.unknowns,
            project_id=project,
            canvas_id=canvas_id,
            canvas_revision=(
                raw_canvas.get("revision") if isinstance(raw_canvas, dict) else None
            ),
            existing_node_ids=canvas_node_ids,
            selected_node_ids=payload.target_node_ids,
            pinned_node_ids=payload.target_node_ids,
        )
    return {
        "ok": True,
        "data": {
            "schema": "canvas_action_route.v1",
            "project_id": project,
            "canvas_id": canvas_id,
            "canvas_revision": (
                raw_canvas.get("revision")
                if isinstance(raw_canvas, dict)
                and isinstance(raw_canvas.get("revision"), int)
                and not isinstance(raw_canvas.get("revision"), bool)
                else None
            ),
            **route.to_dict(),
            "target_resolution": target_resolution,
            "director_ledger": ledger,
            **(
                {"director_intent_contract": intent_contract} if intent_contract else {}
            ),
        },
    }


async def _scope(project: str, user: dict, role: str) -> ProjectContext:
    ctx = await resolve_project_context(
        user=user, project_id=project, required_role=role
    )
    return require_project_home_node(ctx, operation="canvas workflow runtime access")


def _service(ctx: ProjectContext, project: str) -> WorkflowRuntimeService:
    return WorkflowRuntimeService(
        ctx.state_dir,
        project_id=project,
        project_context=ctx,
    )


async def _reconcile_terminal_workflow_turn(
    ctx: ProjectContext | None,
    run: dict[str, Any] | None,
) -> None:
    if ctx is None or not isinstance(run, dict):
        return
    username = str(
        getattr(ctx, "requester_username", "")
        or getattr(ctx, "owner_username", "")
        or getattr(ctx, "requester_user_id", "")
        or ""
    ).strip()
    if not username:
        return
    await reconcile_workflow_turn_receipt_async(
        username=username,
        project_id=str(run.get("project_id") or getattr(ctx, "project_id", "")),
        canvas_id=str(run.get("canvas_id") or ""),
        state_dir=ctx.state_dir,
        run=run,
    )


def _workflow_run_response(run: dict[str, Any]) -> dict[str, Any]:
    """Attach the derived release gate without changing persisted run state."""

    return attach_workflow_run_release_readiness(run)


def _conflict(exc: WorkflowRunConflictError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": exc.code,
            "message": str(exc),
            "current_revision": exc.current_revision,
            **exc.details,
        },
    )


def _canvas_command_http_error(exc: CanvasCommandError) -> HTTPException:
    status_code = (
        409
        if exc.code
        in {
            "canvas_revision_conflict",
            "canvas_command_idempotency_conflict",
            "canvas_command_execution_context_conflict",
            "canvas_command_execution_context_scope_mismatch",
            "canvas_command_node_id_conflict",
            "director_clarification_required",
        }
        else 422
    )
    detail = exc.to_dict()
    if exc.code == "director_clarification_required":
        # Surface the common director-gate contract at the HTTP boundary while
        # retaining the standard canvas error envelope for existing clients.
        detail = {**detail, **exc.details}
    return HTTPException(status_code=status_code, detail=detail)


def _stream_cursor(after_seq: int, last_event_id: str | None) -> int:
    try:
        resumed = int(str(last_event_id or "").strip())
    except ValueError:
        resumed = 0
    return max(0, int(after_seq), resumed)


def _compose_source_signature(run: dict[str, Any]) -> str:
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    shot_videos = (
        artifacts.get("shot_videos")
        if isinstance(artifacts.get("shot_videos"), dict)
        else {}
    )
    signature = str(shot_videos.get("result_signature") or "").strip()
    return signature if is_compose_source_signature(signature) else ""


def _canvas_command_receipt(
    snapshot: dict[str, Any],
    command_id: str,
) -> dict[str, Any]:
    metadata = snapshot.get("metadata")
    receipts = (
        metadata.get("village_canvas_command_receipts_v2")
        if isinstance(metadata, dict)
        else {}
    )
    receipt = receipts.get(command_id) if isinstance(receipts, dict) else None
    return dict(receipt) if isinstance(receipt, dict) else {}


def _asset_binding_repair_http_error(
    code: str,
    message: str,
    **details: Any,
) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": code, "message": message, **details},
    )


def _sse_message(
    event: str,
    data: dict[str, Any],
    *,
    event_id: int | None = None,
) -> str:
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    lines.extend(f"data: {line}" for line in encoded.splitlines() or [""])
    return "\n".join(lines) + "\n\n"


async def _workflow_run_event_stream(
    *,
    request: Request,
    ctx: ProjectContext | None = None,
    store,
    run_id: str,
    after_seq: int,
) -> AsyncIterator[str]:
    cursor = after_seq
    snapshot_sent = False
    while True:
        observed_signal = store.event_signal_version(run_id)
        combined_reader = getattr(store, "events_since_with_run", None)
        if callable(combined_reader):
            combined = await combined_reader(
                run_id,
                after_seq=cursor,
                limit=200,
            )
            if combined is None:
                return
            page = combined["page"]
            run = combined["run"]
        else:
            page = await store.events_since(run_id, after_seq=cursor, limit=200)
            if page is None:
                return
            run = await store.get(run_id)
        if run is None:
            return
        response_run = _workflow_run_response(run)

        items = list(page.get("items") or [])
        for item in items:
            cursor = max(cursor, int(item.get("seq") or 0))
            yield _sse_message(
                "workflow.event",
                {
                    "run_id": run_id,
                    "event": item,
                    "agent_event": workflow_event_agent_event(run=run, event=item),
                },
                event_id=cursor,
            )

        if items or not snapshot_sent:
            yield _sse_message(
                "workflow.snapshot",
                {
                    "run": response_run,
                    "cursor": cursor,
                    "agent_event": workflow_snapshot_agent_event(response_run),
                },
                event_id=cursor,
            )
            snapshot_sent = True

        terminal = (
            str(run.get("status") or "") in {"completed", "cancelled"}
            or str(run.get("runtime_phase") or "") == "terminal"
        )
        if terminal and cursor >= int(run.get("event_seq") or 0):
            await _reconcile_terminal_workflow_turn(ctx, run)
            yield _sse_message(
                "workflow.terminal",
                {
                    "run": response_run,
                    "cursor": cursor,
                    "agent_event": workflow_snapshot_agent_event(response_run),
                },
                event_id=cursor,
            )
            return
        if bool(page.get("has_more")):
            continue
        if await request.is_disconnected():
            return

        next_signal = await store.wait_for_event_signal(
            run_id,
            observed_signal,
            timeout=_WORKFLOW_STREAM_HEARTBEAT_SECONDS,
        )
        if next_signal == observed_signal:
            yield ": heartbeat\n\n"


@router.post("/projects/{project}/freezone/canvases/{canvas_id}/commands:apply")
async def apply_canvas_commands(
    project: str,
    canvas_id: str,
    payload: CanvasCommandApply,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "editor")
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "project_id": project,
        "canvas_id": canvas_id,
        "command_id": payload.command_id,
        "commands": payload.commands,
        "turn_id": payload.source_turn_id,
        **(
            {"action_profile": payload.action_profile.model_dump()}
            if payload.action_profile is not None
            else {}
        ),
        **(
            {"execution_context": dict(payload.execution_context)}
            if isinstance(payload.execution_context, dict)
            else {}
        ),
    }
    try:
        receipt = await asyncio.to_thread(
            CanvasCommandGateway(
                project_dir=ctx.state_dir,
                project_id=project,
                actor_id=ctx.requester_user_id,
            ).apply,
            canvas_id=canvas_id,
            envelope=envelope,
            expected_canvas_revision=payload.expected_canvas_revision,
        )
    except CanvasCommandError as exc:
        raise _canvas_command_http_error(exc) from exc
    return {"ok": True, "data": receipt}


@router.get("/projects/{project}/freezone/canvases/{canvas_id}/script-media/readiness")
async def revalidate_script_media_readiness(
    project: str,
    canvas_id: str,
    node_id: str = Query(..., min_length=1, max_length=240),
    action: str = Query(...),
    step_id: str = Query(default="", max_length=160),
    user: dict = Depends(get_api_user),
):
    """Read current script-media readiness without writing or starting tasks."""

    ctx = await _scope(project, user, "editor")
    if action not in {
        SCRIPT_MEDIA_ACTION_STORYBOARD_IMAGES,
        SCRIPT_MEDIA_ACTION_SHOT_VIDEOS,
    }:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "script_media_action_invalid",
                "message": "unsupported script media action",
            },
        )
    snapshot = canvas_store.read_canvas(ctx.state_dir, canvas_id)
    if not isinstance(snapshot, dict):
        raise HTTPException(status_code=404, detail="canvas not found")
    readiness = script_media_gateway.evaluate_script_media_readiness(
        snapshot,
        node_id=node_id,
        action=action,
    )
    if readiness.get("ready") is not True and step_id:
        recovery = build_canvas_command_recovery(
            error_code="canvas_script_media_not_ready",
            details=readiness,
            step_id=step_id,
        )
        if recovery is not None:
            readiness["recovery"] = recovery
    return {"ok": True, "data": readiness}


@router.get("/projects/{project}/workflows")
async def list_workflows(project: str, user: dict = Depends(get_api_user)):
    ctx = await _scope(project, user, "viewer")
    return {"ok": True, "data": _service(ctx, project).definitions()}


@router.post("/projects/{project}/workflow-runs")
async def start_workflow_run(
    project: str,
    payload: WorkflowRunCreate,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "editor")
    if payload.run_mode == "auto" and user.get("credential_kind") != "agent_session":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "workflow_auto_requires_agent_authorization",
                "message": "自动工作流必须由小树在本轮媒体授权后启动",
            },
        )
    try:
        inputs = dict(payload.inputs)
        if payload.director_ledger is not None:
            inputs["director_ledger"] = payload.director_ledger
        if payload.director_intent_contract is not None:
            inputs["director_intent_contract"] = payload.director_intent_contract
        if payload.production_metadata is not None:
            inputs["production_metadata"] = payload.production_metadata
        if payload.execution_context is not None:
            inputs["execution_context"] = dict(payload.execution_context)
        inputs.setdefault(
            "director_clarification_answers",
            dict(payload.director_clarification_answers),
        )
        run, reused = await _service(ctx, project).start(
            workflow_id=payload.workflow_id,
            canvas_id=payload.canvas_id,
            run_mode=payload.run_mode,
            inputs=inputs,
            idempotency_key=payload.idempotency_key,
            contract_version=payload.contract_version,
            goal=payload.goal,
            success_criteria=payload.success_criteria,
            source_turn_id=payload.source_turn_id,
            canvas_revision=payload.canvas_revision,
            selected_node_ids=payload.selected_node_ids,
            pinned_node_ids=payload.pinned_node_ids,
            model_bindings=payload.model_bindings,
            action_profile=(
                payload.action_profile.model_dump()
                if payload.action_profile is not None
                else None
            ),
            parent_run_id=payload.parent_run_id,
            director_plan_revision=payload.director_plan_revision,
            episode_scope=payload.episode_scope,
            concurrency_policy=payload.concurrency_policy,
        )
    except WorkflowDefinitionNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "workflow_not_found", "message": str(exc)},
        ) from exc
    except DirectorClarificationRequiredError as exc:
        raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
    except WorkflowDefinitionInvalidError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "workflow_definition_invalid",
                "message": str(exc),
                "errors": list(exc.errors),
            },
        ) from exc
    except WorkflowConfigurationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": exc.code,
                "message": str(exc),
                **exc.details,
            },
        ) from exc
    except WorkflowRunConflictError as exc:
        raise _conflict(exc) from exc
    if run.get("status") == "running":
        schedule_workflow_run(_service(ctx, project).store, str(run["id"]))
    return {
        "ok": True,
        "data": {**_workflow_run_response(run), "reused": reused},
    }


@router.get("/projects/{project}/workflow-runs")
async def list_workflow_runs(
    project: str,
    canvas_id: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "viewer")
    runs = await _service(ctx, project).store.list(
        project_id=project, canvas_id=canvas_id, limit=limit
    )
    service = _service(ctx, project)
    for run in runs:
        await _prepare_workflow_run_read(ctx, service, run)
    return {
        "ok": True,
        "data": [_workflow_run_response(run) for run in runs],
    }


@router.get("/projects/{project}/workflow-runs/{run_id}")
async def get_workflow_run(
    project: str,
    run_id: str,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "viewer")
    service = _service(ctx, project)
    run = await service.store.get(run_id)
    if run is None or run.get("project_id") != project:
        raise HTTPException(status_code=404, detail={"code": "workflow_run_not_found"})
    await _prepare_workflow_run_read(ctx, service, run)
    return {"ok": True, "data": _workflow_run_response(run)}


@router.post("/projects/{project}/workflow-runs/{run_id}/canvas-asset-binding-repair")
async def repair_workflow_canvas_asset_binding(
    project: str,
    run_id: str,
    payload: WorkflowCanvasAssetBindingRepair,
    user: dict = Depends(get_api_user),
):
    """Detach duplicate canvas asset claims without advancing the Run."""

    ctx = await _scope(project, user, "editor")
    service = _service(ctx, project)
    run = await service.store.get(run_id)
    if run is None or run.get("project_id") != project:
        raise HTTPException(
            status_code=404,
            detail={"code": "workflow_run_not_found"},
        )
    if str(run.get("canvas_id") or "") != payload.canvas_id:
        raise _asset_binding_repair_http_error(
            "workflow_canvas_asset_binding_repair_scope_mismatch",
            "资产整理请求与 WorkflowRun 画布不一致",
        )
    run_revision = run.get("revision")
    if (
        payload.expected_run_revision is not None
        and run_revision != payload.expected_run_revision
    ):
        raise _asset_binding_repair_http_error(
            "workflow_canvas_asset_binding_repair_run_stale",
            "WorkflowRun 已变化，请重新读取失败步骤",
            expected_run_revision=payload.expected_run_revision,
            current_run_revision=run_revision,
        )
    if str(run.get("status") or "") != "failed":
        raise _asset_binding_repair_http_error(
            "workflow_canvas_asset_binding_repair_run_not_failed",
            "只有失败的 WorkflowRun 可以执行资产绑定整理",
        )
    states = run.get("step_states")
    step_state = states.get(payload.step_id) if isinstance(states, dict) else None
    if not isinstance(step_state, dict) or step_state.get("status") != "failed":
        raise _asset_binding_repair_http_error(
            "workflow_canvas_asset_binding_repair_step_not_failed",
            "指定步骤不是当前 Run 的失败步骤",
        )
    recovery = recovery_from_workflow_run(run, step_id=payload.step_id)
    if (
        str(recovery.get("action") or "") != "repair_canvas_asset_binding"
        or str(recovery.get("error_code") or "")
        != "workflow_storyboard_canvas_asset_ambiguous"
    ):
        raise _asset_binding_repair_http_error(
            "workflow_canvas_asset_binding_repair_contract_mismatch",
            "当前失败步骤不是可安全整理的重复资产绑定",
        )

    snapshot = canvas_store.read_canvas(ctx.state_dir, payload.canvas_id)
    if not isinstance(snapshot, dict):
        raise HTTPException(status_code=404, detail="canvas not found")
    existing_receipt = _canvas_command_receipt(snapshot, payload.command_id)
    if existing_receipt:
        return {
            "ok": True,
            "data": {
                "schema": CANVAS_ASSET_BINDING_REPAIR_SCHEMA,
                "status": "idempotent_replay",
                "run_id": run_id,
                "run_revision": run_revision,
                "step_id": payload.step_id,
                "media_replay_started": False,
                "next_action": "revalidate_readiness",
                "canvas_receipt": {
                    **existing_receipt,
                    "schema": "canvas_command_receipt.v2",
                    "idempotent_replay": True,
                },
            },
        }

    plan = plan_canvas_asset_binding_repairs(recovery, snapshot)
    commands = canvas_asset_binding_repair_commands(plan)
    if not commands:
        raise _asset_binding_repair_http_error(
            "workflow_canvas_asset_binding_repair_not_safe",
            "当前画布不满足重复资产绑定的安全整理条件",
        )
    revision = snapshot.get("revision")
    expected_canvas_revision = (
        revision if isinstance(revision, int) and not isinstance(revision, bool) else 0
    )
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "project_id": project,
        "canvas_id": payload.canvas_id,
        "command_id": payload.command_id,
        "commands": commands,
        **({"turn_id": payload.source_turn_id} if payload.source_turn_id else {}),
    }
    try:
        receipt = await asyncio.to_thread(
            CanvasCommandGateway(
                project_dir=ctx.state_dir,
                project_id=project,
                actor_id=ctx.requester_user_id,
            ).apply,
            canvas_id=payload.canvas_id,
            envelope=envelope,
            expected_canvas_revision=expected_canvas_revision,
        )
    except CanvasCommandError as exc:
        raise _canvas_command_http_error(exc) from exc
    return {
        "ok": True,
        "data": {
            "schema": CANVAS_ASSET_BINDING_REPAIR_SCHEMA,
            "status": "repaired",
            "run_id": run_id,
            "run_revision": run_revision,
            "step_id": payload.step_id,
            "kept_node_ids": plan["kept_node_ids"],
            "detached_node_ids": plan["detached_node_ids"],
            "media_replay_started": False,
            "next_action": "revalidate_readiness",
            "canvas_receipt": receipt,
        },
    }


@router.post(
    "/projects/{project}/workflow-runs/{run_id}/canvas-asset-binding-revalidate"
)
async def revalidate_workflow_canvas_asset_binding(
    project: str,
    run_id: str,
    payload: WorkflowCanvasAssetBindingReadiness,
    user: dict = Depends(get_api_user),
):
    """Prove a repaired binding is ready, then hand off to the media grant."""

    ctx = await _scope(project, user, "editor")
    service = _service(ctx, project)
    run = await service.store.get(run_id)
    if run is None or run.get("project_id") != project:
        raise HTTPException(
            status_code=404,
            detail={"code": "workflow_run_not_found"},
        )
    if str(run.get("canvas_id") or "") != payload.canvas_id:
        raise _asset_binding_repair_http_error(
            "workflow_canvas_asset_binding_readiness_scope_mismatch",
            "资产复验请求与 WorkflowRun 画布不一致",
        )
    existing_event = await service.store.get_event(run_id, payload.command_id)
    if existing_event is not None:
        if (
            str(existing_event.get("type") or "") != "step_recovery_updated"
            or str(existing_event.get("step_id") or "") != payload.step_id
        ):
            raise _asset_binding_repair_http_error(
                "workflow_canvas_asset_binding_readiness_replay_mismatch",
                "复验 command_id 已用于不同的恢复动作",
            )
        event_payload = (
            existing_event.get("payload")
            if isinstance(existing_event.get("payload"), dict)
            else {}
        )
        readiness = (
            event_payload.get("readiness")
            if isinstance(event_payload.get("readiness"), dict)
            else {}
        )
        recovery = (
            event_payload.get("recovery")
            if isinstance(event_payload.get("recovery"), dict)
            else {}
        )
        return {
            "ok": True,
            "data": {
                **readiness,
                "status": "idempotent_replay",
                "run_revision": run.get("revision"),
                "recovery": recovery,
                "authorization_request": build_media_authorization_request(
                    run,
                    readiness,
                ),
                "media_submission_started": False,
            },
        }
    run_revision = run.get("revision")
    if (
        payload.expected_run_revision is not None
        and run_revision != payload.expected_run_revision
    ):
        raise _asset_binding_repair_http_error(
            "workflow_canvas_asset_binding_readiness_run_stale",
            "WorkflowRun 已变化，请重新读取失败步骤",
            expected_run_revision=payload.expected_run_revision,
            current_run_revision=run_revision,
        )
    if str(run.get("status") or "") != "failed":
        raise _asset_binding_repair_http_error(
            "workflow_canvas_asset_binding_readiness_run_not_failed",
            "只有失败的 WorkflowRun 可以做资产 readiness 复验",
        )
    states = run.get("step_states")
    step_state = states.get(payload.step_id) if isinstance(states, dict) else None
    if not isinstance(step_state, dict) or step_state.get("status") != "failed":
        raise _asset_binding_repair_http_error(
            "workflow_canvas_asset_binding_readiness_step_not_failed",
            "指定步骤不是当前 Run 的失败步骤",
        )
    recovery = recovery_from_workflow_run(run, step_id=payload.step_id)
    if (
        str(recovery.get("action") or "") != "repair_canvas_asset_binding"
        or str(recovery.get("error_code") or "")
        != "workflow_storyboard_canvas_asset_ambiguous"
    ):
        raise _asset_binding_repair_http_error(
            "workflow_canvas_asset_binding_readiness_contract_mismatch",
            "当前失败步骤不是可复验的重复资产绑定恢复",
        )

    snapshot = canvas_store.read_canvas(ctx.state_dir, payload.canvas_id)
    if not isinstance(snapshot, dict):
        raise HTTPException(status_code=404, detail="canvas not found")
    readiness = evaluate_canvas_asset_binding_readiness(
        run,
        snapshot,
        step_id=payload.step_id,
    )
    if readiness.get("ready") is not True:
        return {
            "ok": True,
            "data": {
                **readiness,
                "status": "not_ready",
                "run_revision": run_revision,
                "media_submission_started": False,
            },
        }
    update_payload = build_step_recovery_update_payload(run, readiness)
    if update_payload is None:
        raise _asset_binding_repair_http_error(
            "workflow_canvas_asset_binding_readiness_handoff_invalid",
            "复验通过但没有可用的付费授权交接合同",
        )
    try:
        updated, applied = await service.store.record_event(
            run_id,
            event_id=payload.command_id,
            event_type="step_recovery_updated",
            step_id=payload.step_id,
            payload={
                **update_payload,
                **(
                    {"source_turn_id": payload.source_turn_id}
                    if payload.source_turn_id
                    else {}
                ),
            },
            expected_revision=payload.expected_run_revision,
        )
    except WorkflowRunConflictError as exc:
        raise _conflict(exc) from exc
    if updated is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "workflow_run_not_found"},
        )
    if not applied:
        raise _asset_binding_repair_http_error(
            "workflow_canvas_asset_binding_readiness_concurrent",
            "WorkflowRun 在复验期间发生变化，请重新读取失败步骤",
            current_run_revision=updated.get("revision"),
        )
    authorization_request = build_media_authorization_request(
        updated,
        readiness,
    )
    return {
        "ok": True,
        "data": {
            **readiness,
            "status": "authorization_required",
            "run_revision_before": run_revision,
            "run_revision": updated.get("revision"),
            "recovery": update_payload["recovery"],
            "authorization_request": authorization_request,
            "media_submission_started": False,
        },
    }


@router.post("/projects/{project}/workflow-runs/{run_id}/compose-authorizations")
async def issue_workflow_compose_authorization(
    project: str,
    run_id: str,
    payload: WorkflowComposeAuthorizationCreate,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "editor")
    if user.get("credential_kind") == "agent_session":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "workflow_compose_authorization_browser_required",
                "message": "最终合成授权只能由浏览器会话签发",
            },
        )
    service = _service(ctx, project)
    run = await service.store.get(run_id)
    if run is None or run.get("project_id") != project:
        raise HTTPException(
            status_code=404,
            detail={"code": "workflow_run_not_found"},
        )
    if str(run.get("canvas_id") or "") != payload.canvas_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "workflow_compose_authorization_scope_mismatch",
                "message": "最终合成授权与画布不匹配",
            },
        )
    states = run.get("step_states") if isinstance(run.get("step_states"), dict) else {}
    step_state = states.get(payload.step_id)
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    artifact = (
        artifacts.get(payload.step_id)
        if isinstance(artifacts.get(payload.step_id), dict)
        else {}
    )
    recovery = (
        artifact.get("recovery") if isinstance(artifact.get("recovery"), dict) else {}
    )
    if (
        not isinstance(step_state, dict)
        or step_state.get("status") != "failed"
        or str(recovery.get("action") or "") != "request_compose_authorization"
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "workflow_compose_authorization_not_requested",
                "message": "当前 Run 没有等待最终合成授权",
            },
        )
    source_signature = _compose_source_signature(run)
    if not source_signature:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "workflow_compose_source_not_ready",
                "message": "逐镜视频结果签名尚未就绪",
            },
        )
    item = await asyncio.to_thread(
        issue_compose_authorization,
        ctx.state_dir,
        project_id=project,
        canvas_id=payload.canvas_id,
        run_id=run_id,
        step_id=payload.step_id,
        source_result_signature=source_signature,
        ttl_seconds=payload.ttl_seconds,
    )
    return {"ok": True, "data": item}


@router.post(
    "/projects/{project}/workflow-runs/{run_id}/compose-authorizations/consume"
)
async def consume_workflow_compose_authorization(
    project: str,
    run_id: str,
    payload: WorkflowComposeAuthorizationConsume,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "editor")
    if user.get("credential_kind") != "agent_session":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "workflow_compose_authorization_agent_required",
                "message": "最终合成票据只能由当前 Agent 消费",
            },
        )
    service = _service(ctx, project)
    run = await service.store.get(run_id)
    if run is None or run.get("project_id") != project:
        raise HTTPException(
            status_code=404,
            detail={"code": "workflow_run_not_found"},
        )
    source_signature = _compose_source_signature(run)
    allowed, reason, item = await asyncio.to_thread(
        consume_compose_authorization,
        ctx.state_dir,
        authorization_id=payload.authorization_id,
        project_id=project,
        canvas_id=payload.canvas_id,
        run_id=run_id,
        step_id=payload.step_id,
        source_result_signature=source_signature,
        consume_key=payload.consume_key,
    )
    if not allowed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": f"workflow_{reason}",
                "message": "最终合成票据未通过服务端校验",
                "media_submission_started": False,
                "source_result_signature": source_signature,
            },
        )
    return {
        "ok": True,
        "data": {
            "allowed": True,
            "reason": reason,
            "authorization": item,
            "media_submission_started": False,
        },
    }


@router.get("/projects/{project}/workflow-runs/{run_id}/events")
async def list_workflow_run_events(
    project: str,
    run_id: str,
    after_seq: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "viewer")
    service = _service(ctx, project)
    run = await service.store.get(run_id)
    if run is None or run.get("project_id") != project:
        raise HTTPException(status_code=404, detail={"code": "workflow_run_not_found"})
    page = await service.store.events_since(
        run_id,
        after_seq=after_seq,
        limit=limit,
    )
    return {"ok": True, "data": page}


@router.get("/projects/{project}/workflow-runs/{run_id}/events/stream")
async def stream_workflow_run_events(
    project: str,
    run_id: str,
    request: Request,
    after_seq: int = Query(0, ge=0),
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "viewer")
    service = _service(ctx, project)
    run = await service.store.get(run_id)
    if run is None or run.get("project_id") != project:
        raise HTTPException(status_code=404, detail={"code": "workflow_run_not_found"})
    cursor = _stream_cursor(after_seq, request.headers.get("last-event-id"))
    return StreamingResponse(
        _workflow_run_event_stream(
            request=request,
            ctx=ctx,
            store=service.store,
            run_id=run_id,
            after_seq=cursor,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/projects/{project}/workflow-runs/{run_id}/events")
async def record_workflow_run_event(
    project: str,
    run_id: str,
    payload: WorkflowRunEventCreate,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "editor")
    try:
        run, applied = await _service(ctx, project).store.record_event(
            run_id,
            event_id=payload.event_id,
            event_type=payload.type,
            step_id=payload.step_id,
            success=payload.success,
            payload=payload.payload,
            error=payload.error,
            expected_revision=payload.expected_revision,
            source="external",
        )
    except WorkflowRunConflictError as exc:
        raise _conflict(exc) from exc
    if run is None or run.get("project_id") != project:
        raise HTTPException(status_code=404, detail={"code": "workflow_run_not_found"})
    if applied and run.get("status") == "running":
        schedule_workflow_run(_service(ctx, project).store, run_id)
    await _reconcile_terminal_workflow_turn(ctx, run)
    return {
        "ok": True,
        "data": {**_workflow_run_response(run), "event_applied": applied},
    }


@router.post("/projects/{project}/workflow-runs/{run_id}/command")
async def command_workflow_run(
    project: str,
    run_id: str,
    payload: WorkflowRunCommand,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "editor")
    try:
        compose_authorization = (
            payload.compose_authorization.model_dump(by_alias=True)
            if payload.compose_authorization is not None
            else None
        )
        media_authorization = (
            payload.media_authorization.model_dump(by_alias=True)
            if payload.media_authorization is not None
            else None
        )
        media_authorization_verified = False
        if media_authorization is not None:
            if user.get("credential_kind") != "agent_session":
                raise HTTPException(
                    status_code=403,
                    detail={"code": "workflow_media_authorization_agent_required"},
                )
            allowed, reason, _ = await asyncio.to_thread(
                consume_paid_media_grant,
                str(user.get("username") or ""),
                grant_id=str(media_authorization.get("authorization_id") or ""),
                project_id=project,
                canvas_id=str(media_authorization.get("canvas_id") or ""),
                idempotency_key=str(media_authorization.get("consume_key") or ""),
            )
            if not allowed:
                raise _conflict(
                    WorkflowRunConflictError(
                        "媒体授权 grant 未通过服务端复验",
                        code="workflow_media_authorization_not_verified",
                        details={
                            "reason": reason,
                            "media_submission_started": False,
                        },
                    )
                )
            media_authorization_verified = True
        run, applied = await _service(ctx, project).command(
            run_id,
            command=payload.command,
            idempotency_key=payload.idempotency_key,
            step_id=payload.step_id,
            direction=payload.direction,
            retry_scope=payload.retry_scope,
            item_ids=payload.item_ids,
            expected_revision=payload.expected_revision,
            execution_context=payload.execution_context,
            compose_authorization=compose_authorization,
            media_authorization=media_authorization,
            media_authorization_verified=media_authorization_verified,
        )
    except WorkflowRunConflictError as exc:
        raise _conflict(exc) from exc
    if run is None or run.get("project_id") != project:
        raise HTTPException(status_code=404, detail={"code": "workflow_run_not_found"})
    if applied and run.get("status") == "running":
        schedule_workflow_run(_service(ctx, project).store, run_id)
    await _reconcile_terminal_workflow_turn(ctx, run)
    return {
        "ok": True,
        "data": {**_workflow_run_response(run), "command_applied": applied},
    }


@router.post("/projects/{project}/workflow-runs/{run_id}/visual-preflight-decision")
async def decide_workflow_visual_preflight(
    project: str,
    run_id: str,
    payload: WorkflowVisualPreflightDecision,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "editor")
    service = _service(ctx, project)
    run = await service.store.get(run_id)
    if run is None or run.get("project_id") != project:
        raise HTTPException(status_code=404, detail={"code": "workflow_run_not_found"})
    if run.get("canvas_id") != payload.canvas_id:
        raise HTTPException(
            status_code=409, detail={"code": "workflow_visual_preflight_scope_mismatch"}
        )
    from novelvideo.workflow_runtime.freezone_videos import (
        _preflight_fingerprint,
        _shot_sources,
        _safe_source_image,
    )

    from novelvideo.workflow_runtime.step_contract import WorkflowStepExecutionError

    try:
        sources, _ = _shot_sources(run)
    except WorkflowStepExecutionError as exc:
        raise HTTPException(
            status_code=409, detail={"code": exc.code, "message": str(exc)}
        ) from exc
    source = next((s for s in sources if s["shot_id"] == payload.shot_id), None)
    if (
        source is None
        or _preflight_fingerprint(run, source) != payload.input_fingerprint
    ):
        raise HTTPException(
            status_code=409, detail={"code": "workflow_visual_preflight_stale"}
        )
    try:
        _safe_source_image(ctx, source)
    except WorkflowStepExecutionError as exc:
        raise HTTPException(
            status_code=409, detail={"code": exc.code, "message": str(exc)}
        ) from exc
    decision_payload = {
        "shot_id": payload.shot_id,
        "input_fingerprint": payload.input_fingerprint,
        "decision": payload.decision,
    }
    existing = await service.store.get_event(run_id, payload.command_id)
    if existing is not None and (
        existing.get("type") != "visual_preflight_decided"
        or any(
            existing.get("payload", {}).get(key) != value
            for key, value in decision_payload.items()
        )
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "workflow_visual_preflight_replay_mismatch"},
        )
    try:
        updated, _ = await service.store.record_event(
            run_id,
            event_id=payload.command_id,
            event_type="visual_preflight_decided",
            step_id="shot_videos",
            source="executor",
            expected_revision=payload.expected_run_revision,
            payload=decision_payload,
        )
    except WorkflowRunConflictError as exc:
        raise _conflict(exc) from exc
    if updated and updated.get("status") == "running":
        schedule_workflow_run(service.store, run_id)
    return {"ok": True, "data": _workflow_run_response(updated)}
