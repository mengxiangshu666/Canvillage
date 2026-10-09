from __future__ import annotations

import ast

from scripts.architecture.audit_execution_entrypoints import (
    _decorator_route,
    _local_function_index,
    _risk_flags,
    _review_class,
    _route_kind,
    _signals,
    _router_prefix,
    _transitive_source,
)


def test_router_prefix_is_preserved_in_audited_path() -> None:
    tree = ast.parse(
        """
router = APIRouter(prefix='/model-gateway')
@router.post('/direct-models/{kind}')
async def create_model(kind: str):
    return await _create(kind)
"""
    )
    function = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef))

    prefix = _router_prefix(tree)

    assert prefix == "/model-gateway"
    assert _decorator_route(function, router_prefix=prefix) == (
        "POST",
        "/model-gateway/direct-models/{kind}",
    )


def test_transitive_source_finds_same_module_contract_helper() -> None:
    text = """
def _reserve():
    return reserve_feature_start_credits()

def _start():
    return _reserve()

@router.post('/projects/{project}/generate')
async def generate(project: str):
    return _start()
"""
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    index = _local_function_index(tree)
    route = index["generate"]

    body, helpers = _transitive_source(route, lines, index, max_depth=2)

    assert helpers == ["_start", "_reserve"]
    assert "reserve_feature_start_credits" in body


def test_review_class_does_not_treat_canonical_canvas_history_as_gateway_bypass() -> None:
    review = _review_class(
        "POST",
        "/projects/{project}/freezone/canvases/{canvas_id}/restore",
        "restore_canvas_history",
        "recovery",
        "canvas",
        "canvas_store.restore_canvas_version(...)",
        {"task_binding": False},
    )

    assert review["category"] == "canonical_canvas_store"
    assert review["confidence"] == "high"


def test_shared_project_task_port_counts_as_task_receipt_and_budget_boundary() -> None:
    signals = _signals(
        "POST",
        "/projects/{project}/freezone/video/gen",
        "queued = await get_task_backend().enqueue_project_task(ctx, task_type='freezone_video_gen')",
        "media",
    )

    assert signals["shared_task_port"] is True
    assert signals["task_binding"] is True
    assert signals["receipt_or_verification"] is True
    assert signals["budget_or_credit"] is True


def test_action_route_is_planning_only_not_a_mutation_gap() -> None:
    assert _route_kind("POST", "/projects/p/freezone/canvases/c/actions:route") == "planning"


def test_ingest_routes_do_not_require_async_media_receipt() -> None:
    assert _route_kind("POST", "/projects/p/episodes/1/grids/0/upload") == "ingest"
    assert _risk_flags(
        "POST",
        "/projects/{project}/episodes/{episode_num}/grids/{grid_index}/upload",
        "media",
        {
            "project_scope": True,
            "task_binding": False,
            "receipt_or_verification": False,
            "budget_or_credit": False,
        },
        route_kind="ingest",
    ) == []


def test_retired_tts_and_operator_guard_are_not_media_execution_gaps() -> None:
    assert _route_kind("POST", "/projects/p/episodes/1/tts/generate") == "compatibility"
    assert _route_kind(
        "POST", "/projects/p/episodes/1/image-generation-guard/verify-password"
    ) == "guard"
    assert _risk_flags(
        "POST",
        "/projects/{project}/episodes/{episode_num}/tts/generate",
        "media",
        {"project_scope": False},
        route_kind="compatibility",
    ) == []
