"""Compare list-query indexes on synthetic history, without production data."""

from __future__ import annotations

import argparse
import ast
import json
import sqlite3
import statistics
import time
from pathlib import Path

from novelvideo.workflow_runtime.store import _SCHEMA

ROOT = Path(__file__).resolve().parents[2]


def list_query() -> str:
    tree = ast.parse((ROOT / "src/novelvideo/workflow_runtime/store.py").read_text(encoding="utf-8"))
    store = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WorkflowRunStore")
    method = next(node for node in store.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "list")
    return next(
        node.value for node in ast.walk(method)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and "SELECT * FROM canvas_workflow_runs" in node.value
    )


def measure(rows: int, iterations: int) -> dict:
    query = list_query()
    params = ("synthetic", "canvas", "synthetic", "canvas", 20)
    with sqlite3.connect(":memory:") as db:
        db.executescript(_SCHEMA)
        db.executemany(
            """INSERT INTO canvas_workflow_runs
            (id, workflow_id, workflow_version, project_id, canvas_id, run_mode,
             status, idempotency_key, created_at, updated_at)
            VALUES (?, 'synthetic', 1, 'synthetic', 'canvas', 'draft', ?, ?, ?, ?)""",
            (
                (f"run-{index:08d}", "running" if index % 1000 == 0 else "completed",
                 f"key-{index}", "2026-10-01", f"2026-10-{1 + index % 4:02d}")
                for index in range(rows)
            ),
        )
        reports = {}
        expected = None
        for name in ("old", "new"):
            if name == "old":
                db.execute("DROP INDEX idx_canvas_workflow_runs_scope_order")
            else:
                db.execute("CREATE INDEX idx_canvas_workflow_runs_scope_order ON canvas_workflow_runs(project_id, canvas_id, updated_at DESC, id DESC)")
            plan = [row[-1] for row in db.execute("EXPLAIN QUERY PLAN " + query, params)]
            for _ in range(5):
                db.execute(query, params).fetchall()
            samples = []
            for _ in range(iterations):
                started = time.perf_counter()
                result = db.execute(query, params).fetchall()
                samples.append((time.perf_counter() - started) * 1000)
            identities = [row[0] for row in result]
            if expected is None:
                expected = identities
            assert identities == expected, "ordered results changed"
            reports[name] = {
                "median_ms": round(statistics.median(samples), 3),
                "p95_ms": round(sorted(samples)[int(iterations * 0.95) - 1], 3),
                "query_plan": plan,
            }
    return {
        "schema": "workflow_scope_index_comparison.v1", "rows": rows,
        "iterations": iterations, "ordered_results_equal": True,
        "measurements": reports,
        "scope": "synthetic in-memory SQL only; not HTTP latency or production data",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rows < 20 or args.iterations < 5:
        parser.error("rows must be >=20 and iterations >=5")
    report = measure(args.rows, args.iterations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
