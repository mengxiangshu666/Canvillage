"""User-controlled durable memory for Xiaoshu."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from novelvideo.api.auth import get_api_user
from novelvideo.chat.knowledge_router import load_reference, search_knowledge
from novelvideo.chat.expert_arbitration import build_expert_plan
from novelvideo.chat.execution_checkpoint import build_execution_checkpoint
from novelvideo.chat.execution_context import validate_execution_context
from novelvideo.chat.tool_allowlist import (
    compile_execution_context_allowlist,
    compile_tool_allowlist,
)
from novelvideo.chat.shared_context import build_shared_agent_context
from novelvideo.chat.taste_graph import build_taste_graph
from novelvideo.chat.memory_compiler import compile_memory
from novelvideo.chat.growth_distiller import growth_distiller_contract
from novelvideo.chat.memory_hooks import preview_memory_hooks
from novelvideo.chat.memory_index import (
    MemoryRecord,
    capture_growth_distillation_event,
    delete_memory,
    get_growth_distillation_receipt,
    get_memory,
    list_memories,
    memory_stats,
    promote_memory,
    record_memory_evidence,
    save_compiled_memory,
    update_memory,
)
from novelvideo.project_context import require_project_home_node, resolve_project_context
from novelvideo.freezone import canvas_store
from novelvideo.workflow_runtime.store import WorkflowRunStore
from novelvideo.freezone.paths import CANVAS_ID_RE

router = APIRouter()


class MemoryCreateIn(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    kind: str = "preference"
    locked: bool = False
    applies_when: dict[str, Any] = Field(default_factory=dict)


class MemoryManualCreateIn(BaseModel):
    """A user-authored memory that should be usable immediately."""

    content: str = Field(min_length=1, max_length=20_000)
    kind: str = "learned_rule"
    locked: bool = True
    applies_when: dict[str, Any] = Field(default_factory=dict)


class MemoryLearningAccepted(BaseModel):
    accepted: bool = True
    event_id: int
    status: str = "pending_distillation"
    receipt: dict[str, Any] = Field(default_factory=dict)


class MemoryUpdateIn(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=20_000)
    status: str | None = None
    locked: bool | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    applies_when: dict[str, Any] | None = None


class MemoryEvidenceIn(BaseModel):
    outcome: str
    evidence_ref: str = Field(min_length=1, max_length=2_000)
    project: str = Field(default="", max_length=256)
    task_id: str = Field(default="", max_length=256)
    notes: str = Field(default="", max_length=4_000)


class MemoryPreviewIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    task_stage: str = Field(default="", max_length=80)
    node_type: str = Field(default="", max_length=80)
    duration_sec: float | None = Field(default=None, ge=0.0, le=600.0)
    references: list[dict[str, Any]] = Field(default_factory=list, max_length=32)
    project_id: str = Field(default="", max_length=256)


class ExecutionCheckpointIn(BaseModel):
    """Replay a planner-owned checkpoint without rebuilding a second plan."""

    project: str = Field(min_length=1, max_length=256)
    canvas_id: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=2_000)
    capability_id: str = Field(min_length=1, max_length=160)
    plan_revision: str = Field(default="", max_length=80)
    allowlist_revision: str = Field(default="", max_length=80)
    confirm: bool = False
    mode: str = Field(default="observe", max_length=40)
    allowlist: dict[str, Any] = Field(default_factory=dict)
    execution_context: dict[str, Any]


def _username(user: dict[str, Any]) -> str:
    username = str(user.get("username") or "").strip()
    if not username:
        raise HTTPException(status_code=401, detail="authenticated user required")
    return username


def _memory_payload(record: MemoryRecord) -> dict[str, Any]:
    try:
        applies_when = json.loads(record.applies_when or "{}")
    except (TypeError, ValueError):
        applies_when = {}
    try:
        metadata = json.loads(record.metadata_json or "{}")
    except (TypeError, ValueError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "id": record.id,
        "scope_kind": record.scope_kind,
        "scope_id": record.scope_id or None,
        "kind": record.kind,
        "source": record.source,
        "content": record.content,
        "status": record.status,
        "confidence": record.confidence,
        "locked": record.locked,
        "applies_when": applies_when,
        "provenance": {
            "source": record.source,
            "source_id": record.source_id,
            "evidence": metadata.get("evidence", []),
            "origin_project": metadata.get("origin_project"),
            "origin_event_id": metadata.get("origin_event_id"),
            "distilled": bool(metadata.get("distilled", False)),
            "compiled": bool(metadata.get("compiled", False)),
            "normalized": bool(metadata.get("normalized", False)),
            "distillation_level": metadata.get("distillation_level"),
            "memory_schema": metadata.get("memory_schema"),
            "memory_key": metadata.get("memory_key"),
            "rule_type": metadata.get("rule_type", "general_rule"),
            "hook_id": metadata.get("hook_id", ""),
            "hook_mode": metadata.get("hook_mode", "none"),
            "executable": bool(metadata.get("executable", False)),
            "validation": metadata.get("validation", {}),
            "action": metadata.get("action", []),
            "avoid": metadata.get("avoid", []),
            "compile_reason": metadata.get("compile_reason"),
            "episode_id": metadata.get("episode_id"),
            "feedback_event_id": metadata.get("feedback_event_id"),
            "feedback_outcome": metadata.get("feedback_outcome"),
            "origin_turn_id": metadata.get("origin_turn_id"),
            "run_id": metadata.get("run_id"),
        },
        "evidence_count": record.evidence_count,
        "retrieved_count": record.retrieved_count,
        "applied_count": record.applied_count,
        "positive_count": record.positive_count,
        "negative_count": record.negative_count,
        "last_verified_at": record.last_verified_at or None,
        "version": record.version,
        "promoted_from_id": record.promoted_from_id or None,
        "supersedes_id": record.supersedes_id or None,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


@router.get("/chat/memories")
async def get_chat_memories(
    status_filter: str | None = Query(default=None, alias="status"),
    scope_kind: str | None = None,
    kind: str | None = None,
    query: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    try:
        records = list_memories(
            _username(user),
            status=status_filter,
            scope_kind=scope_kind,
            kind=kind,
            query=query,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": [_memory_payload(record) for record in records]}


@router.get("/chat/memories/stats")
async def get_chat_memory_stats(
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    return memory_stats(_username(user))


@router.get("/chat/memories/taste-graph")
async def get_chat_taste_graph(
    project: str = Query(default="", max_length=256),
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Return the bounded evidence-backed taste projection for this user."""

    return await asyncio.to_thread(build_taste_graph, _username(user), project)


@router.get("/chat/memories/growth-contract")
async def get_growth_memory_contract(
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Expose the growth role contract without exposing gateway credentials."""

    _username(user)
    return growth_distiller_contract()


@router.get("/chat/knowledge/search")
async def search_chat_knowledge(
    query: str = Query(..., min_length=1, max_length=2_000),
    project: str = Query(default="", max_length=256),
    sources: str = Query(default="memory,knowledge,obsidian,cognee", max_length=128),
    limit: int = Query(default=8, ge=1, le=20),
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Federated read-only retrieval over memory, local knowledge and Obsidian."""
    username = _username(user)
    source_list = [item.strip() for item in sources.split(",") if item.strip()]
    return await search_knowledge(
        username,
        project,
        query,
        sources=source_list,
        limit=limit,
    )


@router.post("/chat/memories/preview")
async def preview_chat_memory_hooks(
    payload: MemoryPreviewIn,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Preview deterministic memory hooks without persistence or generation side effects."""
    _username(user)
    return preview_memory_hooks(payload.model_dump())


@router.get("/chat/knowledge/reference")
async def get_chat_knowledge_reference(
    uri: str = Query(..., min_length=1, max_length=2_000),
    project: str = Query(default="", max_length=256),
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Load one bounded note, memory, or Cognee chunk returned by ``knowledge.search``."""
    username = _username(user)
    try:
        return await asyncio.to_thread(
            load_reference,
            uri,
            project_id=project,
            username=username,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="knowledge reference not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="knowledge reference is not readable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/chat/context/blackboard")
async def get_shared_agent_context(
    project: str = Query(..., min_length=1, max_length=256),
    canvas_id: str = Query(..., min_length=1, max_length=200),
    query: str = Query(default="", max_length=2_000),
    sources: str = Query(default="memory,knowledge,obsidian,cognee", max_length=128),
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Return one bounded, read-only Agent blackboard projection."""
    username = _username(user)
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(status_code=400, detail="invalid canvas_id")
    ctx = await resolve_project_context(
        user=user,
        project_id=project,
        required_role="viewer",
    )
    require_project_home_node(ctx, operation="read Agent shared context")
    source_errors: dict[str, str] = {}
    try:
        canvas_snapshot = await asyncio.to_thread(
            canvas_store.read_canvas,
            Path(ctx.state_dir),
            canvas_id,
        )
    except Exception as exc:  # noqa: BLE001 - source errors remain visible in the blackboard
        canvas_snapshot = None
        source_errors["canvas"] = f"{type(exc).__name__}: {exc}"

    workflow_runs: list[dict[str, Any]] = []
    try:
        workflow_runs = await WorkflowRunStore(ctx.state_dir).list(
            project_id=project,
            canvas_id=canvas_id,
            limit=20,
        )
    except Exception as exc:  # noqa: BLE001 - a missing workflow DB is a readable source error
        source_errors["workflow"] = f"{type(exc).__name__}: {exc}"

    knowledge: dict[str, Any] = {
        "schema": "knowledge.search.v1",
        "query": query.strip(),
        "project_id": project,
        "sources_requested": [item.strip() for item in sources.split(",") if item.strip()],
        "sources_used": [],
        "results": [],
        "count": 0,
        "memory_ids": [],
        "source_errors": {},
    }
    if query.strip():
        knowledge = await search_knowledge(
            username,
            project,
            query,
            sources=[item.strip() for item in sources.split(",") if item.strip()],
            limit=8,
        )

    model_plan: dict[str, Any] = {}
    try:
        from novelvideo.workflow_runtime.model_plan import build_model_plan_snapshot

        model_plan = build_model_plan_snapshot()
    except Exception as exc:  # noqa: BLE001 - context remains useful without model metadata
        source_errors["model_plan"] = f"{type(exc).__name__}: {exc}"

    director_recipes: list[dict[str, Any]] = []
    try:
        from novelvideo.research.aigc_director_recipes import select_director_recipes

        director_recipes = select_director_recipes(
            creation_stage="planning",
            prompt_guidance=query,
            request_params={"canvas_id": canvas_id},
            limit=4,
        )
    except Exception as exc:  # noqa: BLE001 - research is an optional context source
        source_errors["director_recipes"] = f"{type(exc).__name__}: {exc}"

    taste_graph: dict[str, Any] = {}
    try:
        taste_graph = await asyncio.to_thread(build_taste_graph, username, project)
    except Exception as exc:  # noqa: BLE001 - preference context must not block chat
        source_errors["taste_graph"] = f"{type(exc).__name__}: {exc}"

    return build_shared_agent_context(
        project_id=project,
        project_name=ctx.project_name,
        canvas_id=canvas_id,
        source_turn_id=str(user.get("current_turn_id") or ""),
        canvas_snapshot=canvas_snapshot,
        workflow_runs=workflow_runs,
        knowledge=knowledge,
        source_errors=source_errors,
        model_plan_snapshot=model_plan,
        director_recipes=director_recipes,
        taste_graph=taste_graph,
    )


@router.get("/chat/context/expert-plan")
async def get_shared_agent_expert_plan(
    project: str = Query(..., min_length=1, max_length=256),
    canvas_id: str = Query(..., min_length=1, max_length=200),
    query: str = Query(..., min_length=1, max_length=2_000),
    sources: str = Query(default="memory,knowledge,obsidian,cognee", max_length=128),
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Return the five-expert shadow plan without invoking an executor."""
    blackboard = await get_shared_agent_context(
        project=project,
        canvas_id=canvas_id,
        query=query,
        sources=sources,
        user=user,
    )
    return build_expert_plan(blackboard, query)


@router.get("/chat/context/tool-allowlist")
async def get_shared_agent_tool_allowlist(
    project: str = Query(..., min_length=1, max_length=256),
    canvas_id: str = Query(..., min_length=1, max_length=200),
    query: str = Query(..., min_length=1, max_length=2_000),
    sources: str = Query(default="memory,knowledge,obsidian,cognee", max_length=128),
    mode: str = Query(default="observe", max_length=40),
    candidates: str = Query(default="", max_length=1_000),
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Compile a bounded runtime capability allowlist from the shadow plan."""
    plan = await get_shared_agent_expert_plan(
        project=project,
        canvas_id=canvas_id,
        query=query,
        sources=sources,
        user=user,
    )
    candidate_ids = [item.strip() for item in candidates.split(",") if item.strip()]
    return compile_tool_allowlist(plan, candidates=candidate_ids, mode=mode)


@router.get("/chat/context/execution-checkpoint")
async def get_shared_agent_execution_checkpoint(
    project: str = Query(..., min_length=1, max_length=256),
    canvas_id: str = Query(..., min_length=1, max_length=200),
    query: str = Query(..., min_length=1, max_length=2_000),
    capability_id: str = Query(..., min_length=1, max_length=160),
    plan_revision: str = Query(default="", max_length=80),
    allowlist_revision: str = Query(default="", max_length=80),
    confirm: bool = Query(default=False),
    sources: str = Query(default="memory,knowledge,obsidian,cognee", max_length=128),
    mode: str = Query(default="observe", max_length=40),
    candidates: str = Query(default="", max_length=1_000),
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Validate revisions and capability scope without executing the capability."""
    plan = await get_shared_agent_expert_plan(
        project=project,
        canvas_id=canvas_id,
        query=query,
        sources=sources,
        user=user,
    )
    candidate_ids = [item.strip() for item in candidates.split(",") if item.strip()]
    allowlist = compile_tool_allowlist(plan, candidates=candidate_ids, mode=mode)
    effective_plan_revision = str(plan_revision or plan.get("plan_revision") or "")
    effective_allowlist_revision = str(
        allowlist_revision or allowlist.get("allowlist_revision") or ""
    )
    return build_execution_checkpoint(
        expert_plan=plan,
        allowlist=allowlist,
        capability_id=capability_id,
        expected_plan_revision=effective_plan_revision,
        expected_allowlist_revision=effective_allowlist_revision,
        confirm=confirm,
        execution_context=plan.get("execution_context"),
    )


@router.post("/chat/context/execution-checkpoint")
async def replay_shared_agent_execution_checkpoint(
    payload: ExecutionCheckpointIn,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Validate an existing execution identity without creating a new plan.

    Dynamic plugin dispatch already has the planner-owned context and the
    current allowlist.  Reusing both here closes the old second-planning path
    while retaining the same checkpoint implementation and capability gate.
    """

    if not CANVAS_ID_RE.match(payload.canvas_id):
        raise HTTPException(status_code=400, detail="invalid canvas_id")
    ctx = await resolve_project_context(
        user=user,
        project_id=payload.project,
        required_role="editor",
    )
    require_project_home_node(ctx, operation="replay Agent execution checkpoint")
    context = dict(payload.execution_context)
    reasons = validate_execution_context(
        context,
        require_write_fields=payload.capability_id
        not in {"canvas.snapshot", "canvas.wait_receipt", "context.execution_checkpoint"},
    )
    if str(context.get("project_id") or "").strip() != payload.project:
        reasons.append("execution_context_project_mismatch")
    if str(context.get("canvas_id") or "").strip() != payload.canvas_id:
        reasons.append("execution_context_canvas_mismatch")
    if str(context.get("capability_id") or "").strip() != payload.capability_id:
        reasons.append("execution_context_capability_mismatch")
    if reasons:
        return {
            "schema": "agent_execution_checkpoint.v1",
            "status": "blocked_context",
            "reason": reasons[0],
            "ready": False,
            "execution_enabled": False,
            "capability_id": payload.capability_id,
            "plan_revision": str(context.get("plan_revision") or ""),
            "allowlist_revision": str(payload.allowlist_revision or ""),
            "execution_context": context,
            "blocking_reasons": reasons,
        }
    allowlist = compile_execution_context_allowlist(context, mode=payload.mode)
    effective_plan_revision = str(context.get("plan_revision") or "").strip()
    effective_allowlist_revision = str(
        allowlist.get("allowlist_revision") or ""
    ).strip()
    if payload.plan_revision and payload.plan_revision != effective_plan_revision:
        reasons.append("execution_context_plan_revision_mismatch")
    if (
        payload.allowlist_revision
        and payload.allowlist_revision != effective_allowlist_revision
    ):
        reasons.append("execution_context_allowlist_revision_mismatch")
    supplied_allowlist = dict(payload.allowlist)
    if supplied_allowlist:
        if supplied_allowlist.get("schema") != "agent_tool_allowlist.v1":
            reasons.append("execution_context_allowlist_schema_mismatch")
        if str(supplied_allowlist.get("plan_revision") or "").strip() != effective_plan_revision:
            reasons.append("execution_context_allowlist_plan_mismatch")
        if (
            str(supplied_allowlist.get("allowlist_revision") or "").strip()
            != effective_allowlist_revision
        ):
            reasons.append("execution_context_allowlist_digest_mismatch")
    if reasons:
        return {
            "schema": "agent_execution_checkpoint.v1",
            "status": "blocked_context",
            "reason": reasons[0],
            "ready": False,
            "execution_enabled": False,
            "capability_id": payload.capability_id,
            "plan_revision": effective_plan_revision,
            "allowlist_revision": effective_allowlist_revision,
            "execution_context": context,
            "blocking_reasons": reasons,
        }
    return build_execution_checkpoint(
        expert_plan={
            "schema": "agent_expert_plan.v1",
            "plan_revision": effective_plan_revision,
            "source_errors": {},
        },
        allowlist=allowlist,
        capability_id=payload.capability_id,
        expected_plan_revision=effective_plan_revision,
        expected_allowlist_revision=effective_allowlist_revision,
        confirm=payload.confirm,
        execution_context=context,
    )


@router.post(
    "/chat/memories",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=MemoryLearningAccepted,
)
async def create_chat_memory(
    payload: MemoryCreateIn,
    user: dict = Depends(get_api_user),
) -> MemoryLearningAccepted:
    kind = payload.kind.strip().lower()
    allowed_kinds = {"learned_rule", "preference", "verified_experience"}
    if kind not in allowed_kinds:
        raise HTTPException(status_code=400, detail="unsupported memory kind")
    username = _username(user)
    turn_id = f"memory-ui-{uuid.uuid4().hex}"
    event_id = capture_growth_distillation_event(
        username,
        project="",
        turn_id=turn_id,
        conversation_id="memory-ui",
        payload={
            "conversation_id": "memory-ui",
            "raw_user_prompt": payload.content,
            "assistant_output": "",
            "user_feedback": payload.content,
            "execution_result": {
                "verified": False,
                "source": "memory_ui",
                "requested_kind": kind,
                "locked_requested": bool(payload.locked),
                "applies_when": payload.applies_when,
            },
            "project_context": "",
            "task_family_hint": "user_preference" if kind == "preference" else "",
        },
    )
    if event_id <= 0:
        raise HTTPException(status_code=500, detail="learning event persistence failed")
    from novelvideo.chat.service import schedule_growth_distillation_drain

    schedule_growth_distillation_drain(username, limit=2)
    receipt = await asyncio.to_thread(
        get_growth_distillation_receipt,
        username,
        event_id,
    ) or {
        "schema": "growth_distillation_receipt.v1",
        "event_id": event_id,
        "status": "pending",
        "terminal": False,
    }
    return MemoryLearningAccepted(event_id=event_id, receipt=receipt)


@router.post("/chat/memories/manual")
async def create_manual_chat_memory(
    payload: MemoryManualCreateIn,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Persist one explicit memory without waiting for model distillation."""

    kind = payload.kind.strip().lower()
    allowed_kinds = {"learned_rule", "preference", "verified_experience"}
    if kind not in allowed_kinds:
        raise HTTPException(status_code=400, detail="unsupported memory kind")

    username = _username(user)
    compilation = compile_memory(
        payload.content,
        kind_hint=kind,
    )
    if compilation.decision != "add" or not compilation.content:
        raise HTTPException(
            status_code=422,
            detail="请写成具体、可执行的规则或偏好，包含对象和动作。",
        )
    if payload.applies_when:
        compilation = replace(
            compilation,
            applies_when={**(compilation.applies_when or {}), **payload.applies_when},
        )

    memory_id = await asyncio.to_thread(
        save_compiled_memory,
        username,
        compilation,
        source="memory_ui_manual",
        status="confirmed",
        locked=payload.locked,
        metadata={
            "manual_entry": True,
            "origin_turn_id": f"memory-manual-{uuid.uuid4().hex}",
        },
        evidence_count=1,
    )
    if memory_id <= 0:
        raise HTTPException(status_code=500, detail="manual memory persistence failed")
    record = await asyncio.to_thread(get_memory, username, memory_id)
    if record is None:
        raise HTTPException(status_code=500, detail="manual memory readback failed")
    return _memory_payload(record)


@router.get("/chat/memories/events/{event_id}")
async def get_chat_memory_event_receipt(
    event_id: int,
    user: dict[str, Any] = Depends(get_api_user),
) -> dict[str, Any]:
    """Read the durable distillation state without exposing raw payloads."""

    receipt = await asyncio.to_thread(
        get_growth_distillation_receipt,
        _username(user),
        event_id,
    )
    if receipt is None:
        raise HTTPException(status_code=404, detail="growth event not found")
    return {"ok": True, "data": receipt}


@router.patch("/chat/memories/{memory_id}")
async def patch_chat_memory(
    memory_id: int,
    payload: MemoryUpdateIn,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    username = _username(user)
    current = get_memory(username, memory_id)
    if current is None:
        raise HTTPException(status_code=404, detail="memory not found")
    compiled_content = payload.content
    compiled_applies_when = payload.applies_when
    metadata_patch: dict[str, Any] | None = None
    if payload.content is not None:
        compilation = compile_memory(
            payload.content,
            kind_hint=current.kind,
            scope_kind=current.scope_kind,
        )
        if compilation.decision != "add":
            raise HTTPException(
                status_code=422,
                detail="这段内容缺少可执行对象或动作，请补充具体要求。",
            )
        compiled_content = compilation.content
        compiled_applies_when = payload.applies_when or compilation.applies_when
        metadata_patch = compilation.metadata()
    try:
        record = update_memory(
            username,
            memory_id,
            content=compiled_content,
            status=payload.status,
            locked=payload.locked,
            confidence=payload.confidence,
            applies_when=compiled_applies_when,
            metadata_patch=metadata_patch,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return _memory_payload(record)


@router.post("/chat/memories/{memory_id}/promote")
async def promote_chat_memory(
    memory_id: int,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    record = promote_memory(_username(user), memory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return _memory_payload(record)


@router.post("/chat/memories/{memory_id}/evidence")
async def add_chat_memory_evidence(
    memory_id: int,
    payload: MemoryEvidenceIn,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    try:
        record = record_memory_evidence(
            _username(user),
            memory_id,
            outcome=payload.outcome,
            evidence_ref=payload.evidence_ref,
            project=payload.project,
            task_id=payload.task_id,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return _memory_payload(record)


@router.delete("/chat/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_chat_memory(
    memory_id: int,
    user: dict = Depends(get_api_user),
) -> Response:
    if not delete_memory(_username(user), memory_id):
        raise HTTPException(status_code=404, detail="memory not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
