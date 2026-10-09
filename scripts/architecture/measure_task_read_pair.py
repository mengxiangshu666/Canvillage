"""Compare task/current-history projections on one isolated SQLite fixture."""

from __future__ import annotations

import argparse
import asyncio
import cProfile
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import time
import pstats
import socket
import threading
from unittest.mock import patch

from novelvideo.project_context import ProjectContext
from novelvideo.task_state import TaskStateManager

from measure_workflow_sse_read_pair import summary


@asynccontextmanager
async def measurement_client(app, tcp):
    import httpx

    if not tcp:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture", trust_env=False,
        ) as client:
            yield client
        return
    import uvicorn

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="off", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("isolated task HTTP server did not start")
            await asyncio.sleep(0.01)
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=20,
        ) as client:
            yield client
    finally:
        server.should_exit = True
        await asyncio.to_thread(thread.join, 20)
        listener.close()
        if thread.is_alive():
            raise RuntimeError("isolated task HTTP server did not stop")


async def measure_http(manager, ctx, iterations, tcp=False):
    from fastapi import FastAPI
    from novelvideo.api.routes import tasks as route

    app = FastAPI()
    app.include_router(route.router)
    app.add_api_route("/legacy/projects/{project}/tasks", route.list_project_tasks)
    app.dependency_overrides[route.get_api_user] = lambda: {"username": "fixture"}

    async def resolve(**_kwargs):
        return ctx

    combined_reader = manager.list_tasks_and_runs_for_project
    samples = {"split_compatibility": [], "combined_legacy_encoding": [], "combined": []}
    counts = {name: 0 for name in samples}
    expected = None
    with patch.object(route, "resolve_project_context", resolve), patch.object(
        route, "get_task_manager", lambda: manager,
    ):
        async with measurement_client(app, tcp) as client:
            for iteration in range(iterations + 5):
                order = list(samples)
                if iteration % 2:
                    order.reverse()
                for name in order:
                    with patch.object(
                        manager, "list_tasks_and_runs_for_project",
                        None if name == "split_compatibility" else combined_reader,
                    ):
                        before = manager.connections
                        start = time.perf_counter()
                        prefix = "" if name == "combined" else "/legacy"
                        response = await client.get(f"{prefix}/projects/isolated/tasks?include_runs=true")
                        elapsed = (time.perf_counter() - start) * 1000
                        response.raise_for_status()
                        if expected is None:
                            expected = response.content
                        assert response.content == expected
                        if iteration >= 5:
                            samples[name].append(elapsed)
                            counts[name] += manager.connections - before
    measurements = {name: summary(values) for name, values in samples.items()}
    return {
        "measurements": measurements, "connections": counts,
        "all_response_bytes_equal": True,
        "p95_reduction_percent": 100 * (
            1 - measurements["combined"]["p95_ms"]
            / measurements["split_compatibility"]["p95_ms"]
        ),
        "encoding_p95_reduction_percent": 100 * (
            1 - measurements["combined"]["p95_ms"]
            / measurements["combined_legacy_encoding"]["p95_ms"]
        ),
        "transport": "tcp_pooled" if tcp else "asgi",
        "limitations": [
            "Product task route, SQLite and encoding; fixture authentication/project scope, no browser reconciliation.",
            "Current compatibility paths versus optimized path, not an archived whole-product build.",
        ],
    }


async def profile_http(manager, ctx, iterations):
    """Attribute combined-route CPU cost without profiling the timing samples."""
    import httpx
    from fastapi import FastAPI
    from novelvideo.api.routes import tasks as route

    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[route.get_api_user] = lambda: {"username": "fixture"}

    async def resolve(**_kwargs):
        return ctx

    profiler = cProfile.Profile()
    with patch.object(route, "resolve_project_context", resolve), patch.object(
        route, "get_task_manager", lambda: manager,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture",
        ) as client:
            for _ in range(5):
                (await client.get("/projects/isolated/tasks?include_runs=true")).raise_for_status()
            profiler.enable()
            try:
                for _ in range(iterations):
                    (await client.get("/projects/isolated/tasks?include_runs=true")).raise_for_status()
            finally:
                profiler.disable()
    stats = pstats.Stats(profiler)
    root = Path(__file__).resolve().parents[2]
    entries = []
    for (filename, line, function), (primitive, calls, own, cumulative, _) in stats.stats.items():
        try:
            filename = Path(filename).resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            filename = Path(filename).name
        entries.append({
            "file": filename, "line": line, "function": function,
            "calls": calls, "primitive_calls": primitive,
            "own_ms": own * 1000, "cumulative_ms": cumulative * 1000,
        })
    return {
        "iterations": iterations, "total_cpu_ms": stats.total_tt * 1000,
        "top_own_time": sorted(entries, key=lambda row: row["own_ms"], reverse=True)[:30],
        "top_cumulative_time": sorted(entries, key=lambda row: row["cumulative_ms"], reverse=True)[:30],
        "limitations": [
            "Separate diagnostic run; profiler overhead is not a latency measurement.",
            "Includes ASGI client and fixture dependencies; excludes worker-thread CPU.",
            "Cumulative function times overlap and must not be added together.",
        ],
    }


class CountingManager(TaskStateManager):
    connections = 0

    @contextmanager
    def _connect_context(self, ctx):
        self.connections += 1
        with super()._connect_context(ctx) as conn:
            yield conn


def measure(iterations: int, tasks: int, tcp=False):
    with tempfile.TemporaryDirectory(prefix="t255-task-read-pair-") as directory:
        root = Path(directory)
        ctx = ProjectContext(
            project_id="isolated", project_name="isolated", owner_type="user",
            owner_id="fixture", owner_username="fixture", requester_user_id="fixture",
            requester_username="fixture", requester_principals=(("user", "fixture"),),
            effective_role="owner", home_node_id="fixture", is_home_node=True,
            output_dir=root / "output", state_dir=root / "state", runtime_dir=root / "runtime",
        )
        manager = CountingManager()
        for index in range(tasks):
            manager.create_task_for_project(ctx, "ingest_fast", 0, scope=f"sample-{index}")

        def split():
            return manager.list_tasks_for_project(ctx), manager.list_task_runs_for_project(ctx)

        def combined():
            return manager.list_tasks_and_runs_for_project(ctx)

        def canonical(result):
            return tuple([asdict(item) for item in rows] for rows in result)

        readers = {"split_compatibility": split, "combined": combined}
        for _ in range(5):
            assert canonical(split()) == canonical(combined())
        samples = {name: [] for name in readers}
        counts = {name: 0 for name in readers}
        expected = canonical(combined())
        for iteration in range(iterations):
            order = list(readers)
            if iteration % 2:
                order.reverse()
            for name in order:
                before = manager.connections
                start = time.perf_counter()
                result = readers[name]()
                samples[name].append((time.perf_counter() - start) * 1000)
                counts[name] += manager.connections - before
                assert canonical(result) == expected
        measurements = {name: summary(values) for name, values in samples.items()}
        reduction = 1 - measurements["combined"]["p95_ms"] / measurements["split_compatibility"]["p95_ms"]
        http = asyncio.run(measure_http(manager, ctx, iterations, tcp=tcp))
        profile = asyncio.run(profile_http(manager, ctx, min(iterations, 50)))
    return {
        "schema": "task_read_pair.v1", "tasks": tasks, "iterations": iterations,
        "measurements": measurements, "connections": counts,
        "p95_reduction_percent": 100 * reduction, "all_projections_equal": True,
        "http_route": http,
        "http_profile": profile,
        "temporary_fixture_cleaned": True, "media_submissions": 0,
        "limitations": ["Current compatibility readers versus combined reader, not historical full-product HTTP A/B."],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--tasks", type=int, default=40)
    parser.add_argument("--tcp", action="store_true", help="Measure a temporary loopback server with one pooled client")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 20 or args.tasks < 1:
        parser.error("Require at least 20 iterations and one task")
    result = measure(args.iterations, args.tasks, tcp=args.tcp)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "storage_p95_reduction_percent": result["p95_reduction_percent"],
        "http_p95_reduction_percent": result["http_route"]["p95_reduction_percent"],
        "http_connections": result["http_route"]["connections"],
        "all_response_bytes_equal": result["http_route"]["all_response_bytes_equal"],
    }, indent=2))


if __name__ == "__main__":
    main()
