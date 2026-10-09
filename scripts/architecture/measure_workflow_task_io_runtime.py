"""Measure read-only WorkflowRun/task HTTP latency on the running 8784 service."""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "acceptance" / "t113_production_8784_runner.py"
DEFAULT_OUTPUT = ROOT / "workspace" / "artifacts" / "architecture" / "workflow-task-io-runtime-20261005.json"


def _load_runner():
    import sys

    acceptance_dir = str(RUNNER_PATH.parent)
    if acceptance_dir not in sys.path:
        sys.path.insert(0, acceptance_dir)
    spec = importlib.util.spec_from_file_location("runtime_io_runner", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _summary(samples: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))
    return {
        "count": len(ordered),
        "min_ms": round(min(ordered) * 1000, 3),
        "median_ms": round(ordered[len(ordered) // 2] * 1000, 3),
        "p95_ms": round(ordered[index] * 1000, 3),
        "max_ms": round(max(ordered) * 1000, 3),
    }


def _measure(call: Callable[[], Any], iterations: int) -> dict[str, float | int]:
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        call()
        samples.append(time.perf_counter() - started)
    return _summary(samples)


def _compare_transports(runner, urls: dict[str, str], iterations: int) -> dict[str, Any]:
    """Alternate connection strategies against the same runtime and project."""
    with (
        httpx.Client(trust_env=False, timeout=20.0) as pooled,
        httpx.Client(
            trust_env=False, timeout=20.0,
            limits=httpx.Limits(max_keepalive_connections=0),
        ) as fresh,
    ):
        def httpx_read(client, url):
            response = client.get(url)
            response.raise_for_status()
            payload = response.json()
            if payload.get("ok") is not True:
                raise RuntimeError("diagnostic GET returned no acceptance marker")
            return payload.get("data")

        readers = {
            "legacy_urllib": lambda url: runner._http_json("GET", url, timeout=20.0),
            "httpx_fresh": lambda url: httpx_read(fresh, url),
            "httpx_pooled": lambda url: httpx_read(pooled, url),
        }
        measurements = {}
        for endpoint, url in urls.items():
            # Warm each path equally, then rotate their order within every
            # sample so a transient load/cold-start does not favor one client.
            for _ in range(3):
                for read in readers.values():
                    read(url)
            samples = {name: [] for name in readers}
            names = list(readers)
            for index in range(iterations):
                order = names[index % len(names):] + names[:index % len(names)]
                for name in order:
                    started = time.perf_counter()
                    readers[name](url)
                    samples[name].append(time.perf_counter() - started)
            measurements[endpoint] = {
                name: {**_summary(values), "samples_ms": [round(v * 1000, 3) for v in values]}
                for name, values in samples.items()
            }
    return {
        "schema": "workflow_http_transport_diagnostic.v1",
        "warmups_per_endpoint_per_transport": 3,
        "ordering": "rotating transport order within each round",
        "measurements": measurements,
        "notes": [
            "Same current server/project; this compares HTTP connection strategies, not source versions.",
            "httpx fresh and pooled disable environment proxies; legacy urllib retains the original runner behavior.",
            "All probes are GET; empty project latency is not an active-run reconciliation benchmark.",
        ],
    }


def run(iterations: int, output: Path, *, compare_transports: bool = False) -> dict[str, Any]:
    runner = _load_runner()
    project_id = runner._create_project("t255_io_")
    canvas_id = "t255_io_canvas"
    base = runner.PRODUCTION_API_BASE
    try:
        workflow_url = (
            f"{base}/projects/{project_id}/workflow-runs"
            f"?canvas_id={quote(canvas_id, safe='')}"
        )
        workflow_runs = _measure(
            lambda: runner._http_json(
                "GET",
                workflow_url,
                timeout=20.0,
            ),
            iterations,
        )
        workflow_runs_repeat = _measure(
            lambda: runner._http_json(
                "GET",
                workflow_url,
                timeout=20.0,
            ),
            iterations,
        )
        tasks = _measure(
            lambda: runner._http_json(
                "GET",
                f"{base}/projects/{project_id}/tasks",
                timeout=20.0,
            ),
            iterations,
        )
        result = {
            "schema": "workflow_task_io_runtime_baseline.v1",
            "base_url": base,
            "iterations": iterations,
            "project_id": project_id,
            "canvas_id": canvas_id,
            "read_only_after_project_creation": True,
            "measurements": {
                "workflow_runs": workflow_runs,
                "workflow_runs_repeat": workflow_runs_repeat,
                "tasks": tasks,
            },
            "provider_media_task_submissions": 0,
            "notes": [
                "Temporary project has no canvas content and no WorkflowRun.",
                "All measured calls are GET requests; no private project or media was read.",
                "This is a current runtime latency baseline, not a claim of improvement versus an old build.",
            ],
        }
        if compare_transports:
            result["transport_comparison"] = _compare_transports(
                runner,
                {"workflow_runs": workflow_url, "tasks": f"{base}/projects/{project_id}/tasks"},
                iterations,
            )
    finally:
        cleanup = runner._cleanup_project(project_id, canvas_id)
    result["cleanup"] = cleanup
    result["ok"] = cleanup.get("remaining") is False
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--compare-transports", action="store_true")
    args = parser.parse_args()
    if args.iterations < 5:
        parser.error("--iterations must be at least 5")
    return 0 if run(args.iterations, args.output, compare_transports=args.compare_transports).get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
