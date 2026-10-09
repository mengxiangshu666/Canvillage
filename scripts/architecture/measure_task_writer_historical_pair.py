"""Historical/current progress writes: isolated equal fixtures, ABBA, SQL trace."""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import time

from novelvideo.project_context import ProjectContext
from novelvideo.task_state import TaskStateManager

from measure_task_historical_pair import counting_manager, historical_manager, metrics


def measure(ref="8d83d00", iterations=200, tasks=40):
    if iterations < 20 or iterations % 2 or tasks < 1:
        raise ValueError("iterations must be even and at least 20; tasks must be positive")
    old_type, baseline = historical_manager(ref)
    with tempfile.TemporaryDirectory(prefix="t255-task-writer-pair-") as directory:
        managers = {}
        contexts = {}
        created = {}
        for name, manager_type in (("historical", old_type), ("current", TaskStateManager)):
            root = Path(directory) / name
            ctx = ProjectContext(
                project_id="isolated", project_name="isolated", owner_type="user",
                owner_id="fixture", owner_username="fixture", requester_user_id="fixture",
                requester_username="fixture", requester_principals=(("user", "fixture"),),
                effective_role="owner", home_node_id="fixture", is_home_node=True,
                output_dir=root / "output", state_dir=root / "state", runtime_dir=root / "runtime",
            )
            manager = counting_manager(manager_type)
            created[name] = [manager.create_task_for_project(
                ctx, "ingest_fast", 0, scope=f"sample-{index}",
            ) for index in range(tasks)]
            managers[name], contexts[name] = manager, ctx
            manager.reset_counts()
        samples = {name: [] for name in managers}
        completed = dict.fromkeys(managers, 0)
        for _ in range(iterations // 2):
            for name in ("historical", "current", "current", "historical"):
                index = completed[name]
                task_index = index % tasks
                start = time.perf_counter()
                managers[name].update_progress_for_project(
                    contexts[name], "ingest_fast", 0, scope=f"sample-{task_index}",
                    expected_task_id=created[name][task_index].task_id,
                    progress=(index + 1) / (iterations + 1), current_task=f"step-{index}",
                )
                samples[name].append((time.perf_counter() - start) * 1000)
                completed[name] += 1
        counts = {name: dict(manager.counts) for name, manager in managers.items()}
        projections = {}
        for name, manager in managers.items():
            current = manager.list_tasks_for_project(contexts[name])
            runs = manager.list_task_runs_for_project(contexts[name])
            history = {row.task_id: row for row in runs}
            assert len(current) == len(runs) == tasks
            projection = []
            for row in sorted(current, key=lambda row: row.scope):
                assert asdict(row) == asdict(history[row.task_id])
                last_index = max(index for index in range(iterations) if index % tasks == int(row.scope.split("-")[-1]))
                assert row.progress == (last_index + 1) / (iterations + 1)
                assert row.current_task == f"step-{last_index}"
                value = asdict(row)
                for field in ("task_id", "created_at", "updated_at"):
                    value.pop(field)
                projection.append(value)
            projections[name] = projection
        assert projections["historical"] == projections["current"]
    measurements = {name: metrics(values) for name, values in samples.items()}
    return {
        "schema": "task_writer_historical_pair.v1", "baseline": baseline,
        "current_source_sha256": hashlib.sha256(
            (Path(__file__).resolve().parents[2] / "src/novelvideo/task_state.py").read_bytes()
        ).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "iterations_per_path": iterations, "tasks": tasks, "order": "ABBA",
        "counts": counts, "measurements": measurements,
        "p95_reduction_percent": 100 * (1 - measurements["current"]["p95_ms"] / measurements["historical"]["p95_ms"]),
        "all_final_projections_equal_except_identity_timestamps": True,
        "current_history_equal": True, "temporary_fixture_cleaned": True, "media_submissions": 0,
        "limitations": [
            "Historical task_state module only; imported dependencies are current.",
            "Independent temporary databases, equal 40-task fixtures; real update_progress_for_project writes.",
            "SQL trace counts executed statements, not changed rows; seeding and final verification excluded.",
            "No HTTP, browser, production concurrency or archived dirty runtime reproduction.",
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
    print(json.dumps({key: result[key] for key in ("counts", "p95_reduction_percent")}, indent=2))


if __name__ == "__main__":
    main()
