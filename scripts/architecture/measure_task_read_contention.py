"""Measure isolated task reads while real product progress writes run."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import threading
import time

from novelvideo.project_context import ProjectContext
from novelvideo.task_state import TaskStateManager

from measure_workflow_sse_read_pair import summary


def measure(iterations: int, tasks: int, interval: float) -> dict:
    phases = []
    for reader_enabled in (False, True, True, False):
        with tempfile.TemporaryDirectory(prefix="t255-task-contention-") as directory:
            root = Path(directory)
            ctx = ProjectContext(
                project_id="isolated", project_name="isolated", owner_type="user",
                owner_id="fixture", owner_username="fixture", requester_user_id="fixture",
                requester_username="fixture", requester_principals=(("user", "fixture"),),
                effective_role="owner", home_node_id="fixture", is_home_node=True,
                output_dir=root / "output", state_dir=root / "state", runtime_dir=root / "runtime",
            )
            manager = TaskStateManager()
            created = [manager.create_task_for_project(
                ctx, "ingest_fast", 0, scope=f"sample-{index}",
            ) for index in range(tasks)]
            write_samples, read_samples, failures = [], [], []
            start = threading.Event()

            def writer():
                start.wait()
                try:
                    for index in range(iterations):
                        task_index = index % tasks
                        began = time.perf_counter()
                        manager.update_progress_for_project(
                            ctx, "ingest_fast", 0, scope=f"sample-{task_index}",
                            expected_task_id=created[task_index].task_id,
                            progress=(index + 1) / (iterations + 1),
                        )
                        write_samples.append((time.perf_counter() - began) * 1000)
                        time.sleep(interval)
                except Exception as exc:
                    failures.append(type(exc).__name__)

            thread = threading.Thread(target=writer)
            thread.start()
            start.set()
            try:
                if reader_enabled:
                    for _ in range(iterations):
                        began = time.perf_counter()
                        current, history = manager.list_tasks_and_runs_for_project(ctx)
                        read_samples.append((time.perf_counter() - began) * 1000)
                        current_rows = {row.task_id: asdict(row) for row in current}
                        history_rows = {row.task_id: asdict(row) for row in history}
                        assert len(current_rows) == len(history_rows) == tasks
                        for task_id, row in current_rows.items():
                            for field in ("progress", "status", "updated_at", "current_task"):
                                assert row[field] == history_rows[task_id][field]
                        time.sleep(interval)
            finally:
                thread.join(timeout=30)
            if thread.is_alive() or failures or len(write_samples) != iterations:
                raise RuntimeError(f"writer did not finish cleanly: {failures}")
            phases.append({
                "reader_enabled": reader_enabled,
                "writer": summary(write_samples),
                "reader": summary(read_samples) if read_samples else None,
                "writer_failures": failures,
                "consistent_projection_checks": len(read_samples),
            })
    return {
        "schema": "task_read_contention.v1", "iterations": iterations,
        "tasks": tasks, "interval_seconds": interval, "phases": phases,
        "temporary_fixture_cleaned": True, "media_submissions": 0,
        "limitations": [
            "Current product manager with isolated databases; not HTTP or historical A/B.",
            "One progress writer and one reader; does not model all production concurrency.",
            "ABBA phase order bounds ordering effects but does not prove causal whole-product speedup.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--tasks", type=int, default=40)
    parser.add_argument("--interval", type=float, default=0.01)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 20 or args.tasks < 1 or not 0 <= args.interval <= 1:
        parser.error("Require at least 20 iterations, one task and interval in [0, 1]")
    result = measure(args.iterations, args.tasks, args.interval)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "phases": [{
            "reader_enabled": phase["reader_enabled"],
            "writer_p95_ms": phase["writer"]["p95_ms"],
            "reader_p95_ms": phase["reader"]["p95_ms"] if phase["reader"] else None,
            "consistent_projection_checks": phase["consistent_projection_checks"],
            "writer_failures": phase["writer_failures"],
        } for phase in result["phases"]],
        "temporary_fixture_cleaned": result["temporary_fixture_cleaned"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
