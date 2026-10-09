"""T-156 真实运行时实证：状态库只读 SQL 读面。

验的是**已部署的 8784**，不是单测桩：

1. 建一个隔离项目（不碰任何既有项目），用真实产品接口写进一集原文，让它的
   `data.db` 里出现真实行；
2. `GET /api/v1/state/databases` 列出逻辑库；凭据库不出现；
3. `GET /api/v1/state/schema` 回读真实表名与列名；
4. `POST /api/v1/state/query` 跑跨表只读统计并回读**真实行**；
5. 安装级库（`durable_tasks`）可查；缺库返回明确错误且**不建库**；
6. 写语句、未白名单库都被 400 拒绝；
7. 运行版 `runtime/env/novelvideo` 里的插件确实注册了 `state.sql.schema` /
   `state.sql.query` 两张能力卡（证明部署产物带上了这个面）；
8. 收尾：软删 + purge 隔离项目，回读状态目录已消失。

跑法：

    .venv\\Scripts\\python.exe scripts\\acceptance\\t156_state_sql_8784.py

零付费：全部是只读查询，不建任务、不调 provider、不写画布。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


BASE_URL = os.environ.get("T156_BASE_URL") or "http://127.0.0.1:8784"
REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_PYTHON = REPO_ROOT / "runtime" / "python" / "python.exe"
STATE_ROOT = REPO_ROOT / "项目资产" / "state" / "local"
ARTIFACT_DIR = REPO_ROOT / "workspace" / "artifacts" / "t156"
SUMMARY = ARTIFACT_DIR / "state-sql-8784.json"


def _request(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw


def _runtime_plugin_cards() -> list[str]:
    """Read the deployed plugin copy, not the source tree."""

    if not RUNTIME_PYTHON.exists():
        return []
    code = (
        "import json, sys; "
        f"sys.path.insert(0, r'{REPO_ROOT / 'runtime' / 'env'}'); "
        "from novelvideo.agent_tools import village_canvas as p; "
        "print(json.dumps(sorted({c['id'] for c in p._CAPABILITY_INDEX "
        "if str(c.get('domain')) == 'state'})))"
    )
    completed = subprocess.run(
        [str(RUNTIME_PYTHON), "-c", code],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        return []
    return json.loads(completed.stdout.strip().splitlines()[-1])


def main() -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"base_url": BASE_URL, "checks": []}

    def check(name: str, ok: bool, detail: Any = None) -> None:
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})

    project_name = "t156_state_sql_" + time.strftime("%Y%m%d%H%M%S")
    status, created = _request("POST", "/api/v1/projects", {"name": project_name})
    project = str(((created or {}).get("data") or {}).get("id") or "")
    check("isolated_project_created", status == 200 and bool(project), project)
    if not project:
        _write(report)
        return 1
    report["project"] = project
    project_state = STATE_ROOT / project_name

    status, seeded = _request(
        "PUT",
        f"/api/v1/projects/{project}/episodes/1/raw-content",
        {"content": "第一集：主角在雨夜走进旧书店，发现一本会自己翻页的书。"},
    )
    check("project_state_seeded_through_product_api", status == 200, seeded)

    status, listed = _request("GET", f"/api/v1/state/databases?project={project}")
    names = [item["name"] for item in (listed or {}).get("databases", [])]
    check(
        "databases_route_ok",
        status == 200 and {"project_data", "durable_tasks"} <= set(names),
        {"status": status, "names": names},
    )
    check(
        "credential_store_absent",
        bool(names) and not any("settings" in name for name in names),
        names,
    )

    status, schema = _request(
        "GET", f"/api/v1/state/schema?database=project_data&project={project}"
    )
    objects = (schema or {}).get("objects", []) if isinstance(schema, dict) else []
    check(
        "schema_reads_real_tables",
        status == 200 and any(item["name"] == "episodes" for item in objects),
        {"status": status, "object_count": len(objects)},
    )

    status, focused = _request(
        "GET",
        f"/api/v1/state/schema?database=project_data&project={project}&table=episodes",
    )
    focused_objects = (
        (focused or {}).get("objects", []) if isinstance(focused, dict) else []
    )
    check(
        "schema_focuses_one_named_table",
        status == 200
        and [item["name"] for item in focused_objects] == ["episodes"]
        and bool(focused_objects[0]["columns"])
        and focused_objects[0]["columns_truncated"] is False,
        {
            "status": status,
            "names": [item["name"] for item in focused_objects],
            "columns": len(focused_objects[0]["columns"]) if focused_objects else 0,
            "read_mode": (focused or {}).get("read_mode") if isinstance(focused, dict) else None,
        },
    )

    status, unknown = _request(
        "GET",
        f"/api/v1/state/schema?database=project_data&project={project}&table=no_such_table",
    )
    check(
        "schema_rejects_an_unknown_table_name",
        status == 400 and "unknown table or view" in json.dumps(unknown, ensure_ascii=False),
        {"status": status, "body": unknown},
    )

    status, counted = _request(
        "POST",
        "/api/v1/state/query",
        {
            "database": "project_data",
            "project_id": project,
            "sql": (
                "SELECT (SELECT COUNT(*) FROM episodes) AS episodes, "
                "(SELECT COUNT(*) FROM beats) AS beats, "
                "(SELECT COUNT(*) FROM scenes) AS scenes, "
                "(SELECT COUNT(*) FROM characters) AS characters"
            ),
        },
    )
    rows = (counted or {}).get("rows", []) if isinstance(counted, dict) else []
    check(
        "cross_table_read_returns_real_rows",
        status == 200 and len(rows) == 1 and rows[0]["episodes"] == 1,
        {"status": status, "row": rows[0] if rows else None},
    )

    status, tasks = _request(
        "POST",
        "/api/v1/state/query",
        {
            "database": "durable_tasks",
            "sql": "SELECT status, COUNT(*) AS n FROM durable_project_tasks GROUP BY status",
        },
    )
    check(
        "installation_database_is_queryable",
        status == 200 and isinstance((tasks or {}).get("rows"), list),
        {"status": status, "rows": (tasks or {}).get("rows")},
    )

    missing_path = project_state / "workflow_runs.db"
    status, missing = _request(
        "POST",
        "/api/v1/state/query",
        {
            "database": "workflow_runs",
            "project_id": project,
            "sql": "SELECT COUNT(*) FROM canvas_workflow_runs",
        },
    )
    check(
        "missing_database_is_reported_and_not_created",
        status == 400 and "missing" in json.dumps(missing) and not missing_path.exists(),
        {"status": status, "body": missing, "created": missing_path.exists()},
    )

    status, rejected = _request(
        "POST",
        "/api/v1/state/query",
        {
            "database": "project_data",
            "project_id": project,
            "sql": "UPDATE beats SET content = 'x'",
        },
    )
    check("write_statement_rejected", status == 400, {"status": status, "body": rejected})

    status, credentials = _request(
        "POST",
        "/api/v1/state/query",
        {"database": "settings.db", "project_id": project, "sql": "SELECT 1"},
    )
    check(
        "credential_database_rejected",
        status == 400,
        {"status": status, "body": credentials},
    )

    cards = _runtime_plugin_cards()
    check(
        "deployed_plugin_exposes_cards",
        {"state.sql.schema", "state.sql.query"} <= set(cards),
        cards,
    )

    _request("POST", f"/api/v1/projects/{project}/delete")
    status, purged = _request("POST", f"/api/v1/projects/{project}/purge")
    check(
        "isolated_project_cleaned_up",
        status == 200 and not project_state.exists(),
        {"status": status, "body": purged, "state_dir_exists": project_state.exists()},
    )

    _write(report)
    return 0 if report["passed"] == report["total"] else 1


def _write(report: dict[str, Any]) -> None:
    report["passed"] = sum(1 for item in report["checks"] if item["ok"])
    report["total"] = len(report["checks"])
    SUMMARY.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for item in report["checks"]:
        print(f"{'OK ' if item['ok'] else 'FAIL'} {item['name']}: {item['detail']}")
    print(f"{report['passed']}/{report['total']} checks ok -> {SUMMARY}")


if __name__ == "__main__":
    sys.exit(main())
