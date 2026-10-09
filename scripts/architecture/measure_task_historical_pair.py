"""Historical task-state module versus current module on one temporary fixture."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
from unittest.mock import patch

from novelvideo.project_context import ProjectContext
from novelvideo.task_state import TaskStateManager


def statement_kind(sql: str) -> str:
    verb = sql.lstrip().split(None, 1)[0].upper()
    if verb == "SELECT":
        return "reads"
    if verb in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
        return "write_statements"
    if verb in {"CREATE", "ALTER", "DROP"}:
        return "schema_statements"
    return "other_statements"


def historical_manager(ref: str):
    root = Path(__file__).resolve().parents[2]
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        cwd=root, text=True,
    ).strip()
    source = subprocess.check_output(
        ["git", "show", f"{commit}:src/novelvideo/task_state.py"], cwd=root,
    )
    module = types.ModuleType("novelvideo._benchmark_historical_task_state")
    sys.modules[module.__name__] = module
    try:
        exec(compile(source, f"git:{commit}:task_state.py", "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(module.__name__, None)
        raise
    return module.TaskStateManager, {
        "commit": commit, "path": "src/novelvideo/task_state.py",
        "source_sha256": hashlib.sha256(source).hexdigest(),
    }


def counting_manager(base):
    class CountingManager(base):
        def reset_counts(self):
            self.counts = dict.fromkeys(
                ("connections", "reads", "write_statements", "schema_statements", "other_statements"), 0,
            )

        @contextmanager
        def _connect_path(self, db_path):
            original = sqlite3.connect

            def connect(*args, **kwargs):
                connection = original(*args, **kwargs)
                self.counts["connections"] += 1
                connection.set_trace_callback(
                    lambda sql: self.counts.__setitem__(
                        statement_kind(sql), self.counts[statement_kind(sql)] + 1,
                    )
                )
                return connection

            with patch.object(sqlite3, "connect", connect):
                with super()._connect_path(db_path) as connection:
                    yield connection

    manager = CountingManager()
    manager.reset_counts()
    return manager


def canonical(result):
    return tuple([asdict(item) for item in rows] for rows in result)


def metrics(samples):
    ordered = sorted(samples)
    return {
        "samples": len(samples), "median_ms": ordered[len(ordered) // 2],
        "p95_ms": ordered[(95 * len(ordered) + 99) // 100 - 1], "samples_ms": samples,
    }


def measure(ref, iterations=200, tasks=40):
    if iterations < 20 or iterations % 2 or tasks < 1:
        raise ValueError("iterations must be even and at least 20; tasks must be positive")
    old_type, baseline = historical_manager(ref)
    repository = Path(__file__).resolve().parents[2]
    current_source = repository / "src/novelvideo/task_state.py"
    current_identity = {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip(),
        "source_sha256": hashlib.sha256(current_source.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "sqlite_version": sqlite3.sqlite_version,
    }
    old = counting_manager(old_type)
    current = counting_manager(TaskStateManager)
    with tempfile.TemporaryDirectory(prefix="t255-task-historical-") as directory:
        root = Path(directory)
        ctx = ProjectContext(
            project_id="isolated", project_name="isolated", owner_type="user",
            owner_id="fixture", owner_username="fixture", requester_user_id="fixture",
            requester_username="fixture", requester_principals=(("user", "fixture"),),
            effective_role="owner", home_node_id="fixture", is_home_node=True,
            output_dir=root / "output", state_dir=root / "state", runtime_dir=root / "runtime",
        )
        for index in range(tasks):
            old.create_task_for_project(ctx, "ingest_fast", 0, scope=f"sample-{index}")
        seed_counts = dict(old.counts)

        def legacy():
            return old.list_tasks_for_project(ctx), old.list_task_runs_for_project(ctx)

        def combined():
            return current.list_tasks_and_runs_for_project(ctx)

        readers = {"historical_split": legacy, "current_combined": combined}
        expected = canonical(legacy())
        assert len(expected[0]) == tasks and len(expected[1]) == tasks
        for _ in range(5):
            assert canonical(legacy()) == canonical(combined()) == expected
        old.reset_counts()
        current.reset_counts()
        samples = {name: [] for name in readers}
        for _ in range(iterations // 2):
            for name in ("historical_split", "current_combined", "current_combined", "historical_split"):
                start = time.perf_counter()
                result = readers[name]()
                samples[name].append((time.perf_counter() - start) * 1000)
                assert canonical(result) == expected
        measurements = {name: metrics(values) for name, values in samples.items()}
        counts = {"historical_split": old.counts, "current_combined": current.counts}
    return {
        "schema": "task_historical_pair.v1", "baseline": baseline, "current": current_identity,
        "tasks": tasks, "iterations_per_path": iterations, "order": "ABBA",
        "measurements": measurements, "counts": counts, "seed_counts": seed_counts,
        "p95_reduction_percent": 100 * (1 - measurements["current_combined"]["p95_ms"]
                                        / measurements["historical_split"]["p95_ms"]),
        "all_projections_equal": True, "temporary_fixture_cleaned": True,
        "media_submissions": 0,
        "limitations": [
            "Historical task_state module only; both paths use current imported dependencies.",
            "Same database and 40-task fixture, warmed before ABBA; includes SQLite trace overhead on both paths.",
            "Counts are executed SQL statements, including zero-row DELETE, not changed-row counts.",
            "Read workload only; write_statements count incidental writes during reads, not a task-update benchmark.",
            "No HTTP, authentication, browser reconciliation, provider polling or historical dirty runtime reproduction.",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-ref", default="8d83d00")
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--tasks", type=int, default=40)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = measure(args.baseline_ref, args.iterations, args.tasks)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("counts", "p95_reduction_percent", "all_projections_equal")}, indent=2))


if __name__ == "__main__":
    main()
