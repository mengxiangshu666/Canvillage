"""Static coverage audit for mutation, workflow, media, and memory entrypoints.

This is a discovery aid, not a runtime policy.  It reports which canonical
contracts are visible in each HTTP route so gaps can be reviewed against live
behavior before changing code.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
ROUTES_DIR = ROOT / "src" / "novelvideo" / "api" / "routes"


def _router_prefix(tree: ast.Module) -> str:
    """Return the prefix declared by this module's router, if any."""

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "router" for target in node.targets):
            continue
        value = node.value
        if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
            continue
        if value.func.id != "APIRouter":
            continue
        for keyword in value.keywords:
            if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                prefix = str(keyword.value.value or "").strip()
                if not prefix or prefix == "/":
                    return ""
                return "/" + prefix.strip("/")
    return ""


def _decorator_route(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    router_prefix: str = "",
) -> tuple[str, str] | None:
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
            continue
        if not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id != "router":
            continue
        if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
            continue
        path = str(decorator.args[0].value)
        if router_prefix:
            path = f"{router_prefix}/{path.lstrip('/')}" if path else router_prefix
        return decorator.func.attr.upper(), path
    return None


def _source(lines: list[str], node: ast.AST) -> str:
    start = getattr(node, "lineno", 1) - 1
    end = getattr(node, "end_lineno", start + 1)
    return "".join(lines[start:end])


def _has(body: str, *tokens: str) -> bool:
    return any(token in body for token in tokens)


def _local_function_index(
    tree: ast.Module,
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _called_local_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    function_index: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
) -> Iterable[str]:
    seen: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Name) and child.func.id in function_index:
            if child.func.id != node.name and child.func.id not in seen:
                seen.add(child.func.id)
                yield child.func.id


def _transitive_source(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    function_index: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    *,
    max_depth: int = 2,
) -> tuple[str, list[str]]:
    """Collect bounded same-module helper source to reduce local false negatives."""

    chunks: list[str] = [_source(lines, node)]
    helpers: list[str] = []
    visited: set[str] = {node.name}
    frontier = [node]
    for _depth in range(max_depth):
        next_frontier: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        for current in frontier:
            for name in _called_local_names(current, function_index):
                if name in visited:
                    continue
                visited.add(name)
                helper = function_index[name]
                helpers.append(name)
                chunks.append(_source(lines, helper))
                next_frontier.append(helper)
        frontier = next_frontier
        if not frontier:
            break
    return "\n".join(chunks), helpers


def _route_kind(method: str, path: str) -> str:
    lowered = path.casefold()
    if method in {"GET", "HEAD", "OPTIONS"}:
        return "read"
    if "/tts/" in lowered:
        return "compatibility"
    if lowered.endswith("/verify-password"):
        return "guard"
    # ``actions:route`` only computes a deterministic plan.  It reads the
    # canvas and returns a route/ledger; the subsequent ``commands:apply`` or
    # ``workflow-runs`` endpoint owns every production side effect.
    if lowered.endswith("actions:route") or lowered.endswith("/route"):
        return "planning"
    if any(token in lowered for token in ("recover", "restore", "resume", "retry")):
        return "recovery"
    if any(token in lowered for token in ("preview", "analyze", "probe", "discover")):
        return "analysis"
    if any(token in lowered for token in ("upload", "import", "ingest")):
        return "ingest"
    if method == "DELETE" or any(token in lowered for token in ("delete", "purge", "remove")):
        return "delete"
    if any(token in lowered for token in ("generate", "execute", "compose", "run", "apply", "patch", "create", "start")):
        return "execution"
    return "mutation"


def _lane(method: str, path: str, body: str) -> str:
    value = f"{method} {path} {body}".casefold()
    if _has(value, "workflow", "schedule_workflow_run", "workflowruntimeservice"):
        return "workflow"
    if _has(value, "canvascommandgateway", "canvas-patch", "canvases", "save_canvas"):
        return "canvas"
    if _has(value, "freezone/video", "generate", "media", "task_type", "provider_task"):
        return "media"
    if _has(value, "memories", "knowledge", "cognee", "growth"):
        return "memory"
    return "other"


def _signals(method: str, path: str, body: str, lane: str) -> dict[str, bool]:
    """Collect evidence signals without pretending every route needs every signal."""

    # ``enqueue_project_task`` is the canonical project-task port.  Its
    # implementation persists the TaskState row, acceptance receipt, queue
    # binding and production cost receipt outside the route module.  A route
    # audit limited to same-module source must record that boundary explicitly
    # or it will report every well-formed async route as missing receipts.
    shared_task_port = _has(body, "enqueue_project_task")

    return {
        "project_scope": _has(
            body,
            "resolve_project_context",
            "require_project_home_node",
            "_scope(",
            "project_context",
        ),
        "revision_or_cas": _has(
            body,
            "expected_canvas_revision",
            "base_revision",
            "canvas_revision",
            "revision",
            "compare_and_swap",
            "cas",
        ),
        "idempotency": _has(
            body,
            "idempotency",
            "command_id",
            "request_id",
            "event_id",
        ),
        "receipt_or_verification": _has(
            body,
            "receipt",
            "queued_task_receipt_fields",
            "queued_task_response",
            "readback_verified",
            "verification",
            "complete_task",
            "record_event",
        ) or shared_task_port,
        "production_metadata": _has(body, "production_metadata", "productionMetadata"),
        "lease_or_recovery": _has(
            body,
            "lease",
            "recover",
            "resume",
            "checkpoint",
            "retry",
        ),
        "task_binding": _has(body, "task_id", "task_type", "provider_task_id", "run_id")
        or shared_task_port,
        "budget_or_credit": _has(
            body,
            "budget",
            "credit",
            "reserve_feature",
            "usage_meter",
            "paid_media",
        ) or shared_task_port,
        "canonical_canvas_gateway": _has(
            body,
            "CanvasCommandGateway",
            "canvas_store.save_canvas",
            "apply_commands",
            "canvas-patch",
        ),
        "shared_task_port": shared_task_port,
    }


def _risk_flags(
    method: str,
    path: str,
    lane: str,
    signals: dict[str, bool],
    *,
    route_kind: str = "",
) -> list[str]:
    """Return only actionable, lane-specific review flags."""

    mutating = method in {"POST", "PUT", "PATCH", "DELETE"}
    project_route = "/projects/" in path
    lowered = path.casefold()
    if lowered.endswith("actions:route") or lowered.endswith("/route"):
        return []
    flags: list[str] = []
    if not mutating:
        return flags
    if route_kind in {"compatibility", "guard"}:
        return flags
    if route_kind in {"analysis", "planning"}:
        return flags
    if project_route and not signals["project_scope"]:
        flags.append("project_scope_not_visible")
    # These routes are intentionally side-effect free or use a different
    # contract than an asynchronous media submission. Keep project-scope
    # visibility above, but do not demand task/receipt/budget fields here.
    if route_kind == "ingest":
        return flags
    if method == "DELETE" and "/chat/conversations/" in lowered:
        return flags
    if lane == "canvas" and any(
        token in lowered
        for token in (
            "/freezone/canvases/{canvas_id}/restore",
            "/freezone/canvases/{canvas_id}",
            "/freezone/init",
        )
    ) and signals["canonical_canvas_gateway"] is False:
        # History restore/delete/init are canonical canvas-store transactions,
        # not Agent patch commands. Their review class records that boundary.
        return flags
    if lane == "canvas":
        if not signals["canonical_canvas_gateway"]:
            flags.append("canvas_gateway_not_visible")
        if not signals["idempotency"]:
            flags.append("idempotency_not_visible")
        if any(token in lowered for token in ("/canvases/{canvas_id}", "commands:apply", "canvas-patch")) and not signals["revision_or_cas"]:
            flags.append("revision_or_cas_not_visible")
    elif lane == "workflow":
        if any(token in lowered for token in ("workflow-runs", "actions:route")) and not signals["idempotency"]:
            flags.append("idempotency_not_visible")
        if "workflow-runs" in lowered and not signals["task_binding"]:
            flags.append("run_binding_not_visible")
    elif lane == "media":
        submission = any(token in lowered for token in ("generate", "/gen", "execute", "compose", "start", "upload", "recover", "run"))
        if submission and not signals["task_binding"]:
            flags.append("task_binding_not_visible")
        if submission and not signals["receipt_or_verification"]:
            flags.append("receipt_not_visible")
        if submission and not signals["budget_or_credit"]:
            flags.append("budget_or_credit_not_visible")
    elif lane == "memory" and path.endswith("/memories") and method in {"POST", "PATCH", "DELETE"}:
        if not signals["receipt_or_verification"]:
            flags.append("memory_receipt_not_visible")
    return flags


def _review_class(
    method: str,
    path: str,
    function: str,
    route_kind: str,
    lane: str,
    body: str,
    signals: dict[str, bool],
) -> dict[str, str]:
    """Explain whether a static flag is an actual gap or a valid boundary.

    The scanner deliberately errs on the side of visibility.  This second
    pass records the runtime-shaped interpretation so a reviewer does not
    mistake a low-level canonical store or an analysis route for a bypass.
    """

    lowered = path.casefold()
    if route_kind == "read":
        return {"category": "read_only", "confidence": "high", "reason": "读取入口不产生副作用"}
    if route_kind == "analysis":
        return {"category": "analysis_only", "confidence": "high", "reason": "分析/探测入口不应写生产状态"}
    if route_kind == "compatibility":
        return {
            "category": "retired_compatibility",
            "confidence": "high",
            "reason": "旧协议入口明确返回 410，不进入当前媒体执行链",
        }
    if route_kind == "guard":
        return {
            "category": "security_gate",
            "confidence": "high",
            "reason": "只校验操作员凭据，不创建任务或写入项目状态",
        }
    if route_kind == "ingest":
        return {
            "category": "asset_ingest",
            "confidence": "high",
            "reason": "文件导入写入项目资产，不是需要 TaskState 的媒体生成提交",
        }
    if route_kind == "planning":
        return {
            "category": "planning_only",
            "confidence": "high",
            "reason": "只读取事实并计算执行计划，副作用由后续正式执行入口承接",
        }
    if function == "create_chat_memory":
        return {
            "category": "memory_event_backed",
            "confidence": "high",
            "reason": "原话进入 growth distillation outbox，不直接升级为长期规则",
        }
    if method == "DELETE" and "/chat/conversations/" in lowered:
        return {
            "category": "scoped_chat_delete",
            "confidence": "high",
            "reason": "会话删除不属于画布命令网关，需由会话作用域和关联状态合同负责",
        }
    if lane == "canvas" and function in {
        "restore_canvas_history",
        "delete_canvas",
        "init_freezone",
    }:
        return {
            "category": "canonical_canvas_store",
            "confidence": "high",
            "reason": "历史恢复/删除/初始化由 canvas_store 原子事务负责，不是 Agent patch 旁路",
        }
    if lane in {"media", "workflow"} and signals["task_binding"] and "enqueue_project_task" in body:
        return {
            "category": "shared_task_port_covered",
            "confidence": "high",
            "reason": "入口通过统一 project task port 进入 TaskState/queue/runner 主链",
        }
    if lane == "media" and "enqueue_project_task" not in body:
        return {
            "category": "legacy_or_direct_media_path",
            "confidence": "medium",
            "reason": "未在本模块看到统一任务端口，需核对同步兼容路径和真实运行调用链",
        }
    if lane == "workflow" and signals["task_binding"]:
        return {
            "category": "workflow_runtime_bound",
            "confidence": "medium",
            "reason": "已有 run/task 绑定，需继续核对幂等键和事件回执",
        }
    return {
        "category": "manual_review",
        "confidence": "low",
        "reason": "静态证据不足，不能据此直接修改实现",
    }


def audit() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(ROUTES_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            rows.append({"file": str(path.relative_to(ROOT)), "parse_error": str(exc)})
            continue
        lines = text.splitlines(keepends=True)
        router_prefix = _router_prefix(tree)
        function_index = _local_function_index(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            route = _decorator_route(node, router_prefix=router_prefix)
            if route is None:
                continue
            method, route_path = route
            body, helper_functions = _transitive_source(node, lines, function_index)
            lane = _lane(method, route_path, body)
            signals = _signals(method, route_path, body, lane)
            route_kind = _route_kind(method, route_path)
            rows.append(
                {
                    "file": str(path.relative_to(ROOT)),
                    "line": int(node.lineno),
                    "function": node.name,
                    "method": method,
                    "path": route_path,
                    "route_kind": route_kind,
                    "lane": lane,
                    "analysis": {
                        "scope": "route_plus_same_module_helpers_depth_2",
                        "router_prefix": router_prefix,
                        "helper_functions": helper_functions,
                    },
                    "signals": signals,
                    "risk_flags": _risk_flags(
                        method,
                        route_path,
                        lane,
                        signals,
                        route_kind=route_kind,
                    ),
                    "review": _review_class(
                        method,
                        route_path,
                        node.name,
                        route_kind,
                        lane,
                        body,
                        signals,
                    ),
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    args = parser.parse_args()
    rows = audit()
    if args.json:
        print(json.dumps({"schema": "execution_entrypoint_audit.v1", "rows": rows}, ensure_ascii=False, indent=2))
        return 0
    counts: dict[str, int] = {}
    flagged = 0
    for row in rows:
        lane = str(row.get("lane") or "parse_error")
        counts[lane] = counts.get(lane, 0) + 1
        flagged += bool(row.get("risk_flags"))
    print("Execution entrypoint coverage audit")
    print(f"routes={len(rows)}")
    print(f"lanes={json.dumps(counts, ensure_ascii=False, sort_keys=True)}")
    print(f"flagged_routes={flagged}")
    for row in rows:
        flags = row.get("risk_flags") or []
        if row.get("lane") in {"canvas", "workflow", "media", "memory"} and flags:
            print(
                f"{row['file']}:{row['line']} {row['method']} {row['path']} "
                f"lane={row['lane']} risks={','.join(flags)}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
