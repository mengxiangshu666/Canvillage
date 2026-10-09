"""Measure split/combined product SSE snapshot reads over isolated real TCP."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import aclosing
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import httpx
import uvicorn

from measure_workflow_sse_read_pair import CountingStore, summary
from novelvideo.api.routes.workflows import _workflow_run_event_stream
from novelvideo.workflow_runtime.definitions import get_workflow_definition


class SplitReader:
    events_since_with_run = None

    def __init__(self, store):
        self.store = store

    def __getattr__(self, name):
        return getattr(self.store, name)


async def prepare(directory):
    store = CountingStore(Path(directory))
    definition = get_workflow_definition("one-click-film")
    assert definition is not None
    run, _ = await store.create(
        definition=definition, project_id="isolated", canvas_id="isolated",
        run_mode="draft", inputs={"request": "HTTP read measurement"},
        idempotency_key="http-read-measurement", contract_version=1,
    )
    for index in range(20):
        _, accepted = await store.record_event(
            run["id"], event_id=f"event-{index}", event_type="step_started",
            step_id="canvas_structure",
        )
        assert accepted
    return store, run["id"]


def historical_store(ref):
    root = Path(__file__).resolve().parents[2]
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        cwd=root, text=True,
    ).strip()
    source = subprocess.check_output(
        ["git", "show", f"{commit}:src/novelvideo/workflow_runtime/store.py"], cwd=root,
    )
    module = types.ModuleType("novelvideo.workflow_runtime._benchmark_historical_store")
    sys.modules[module.__name__] = module
    try:
        exec(compile(source, f"git:{commit}:workflow_runtime/store.py", "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(module.__name__, None)
        raise

    class HistoricalCountingStore(module.WorkflowRunStore):
        connections = 0

        @asynccontextmanager
        async def _connect(self):
            self.connections += 1
            async with super()._connect() as db:
                yield db

    return HistoricalCountingStore, {
        "commit": commit, "path": "src/novelvideo/workflow_runtime/store.py",
        "sha256": hashlib.sha256(source).hexdigest(),
        "scope": "Historical store module with current dependencies and current SSE encoder.",
    }


def measure(iterations, baseline_ref=None):
    with tempfile.TemporaryDirectory(prefix="t255-sse-http-") as directory:
        store, run_id = asyncio.run(prepare(directory))
        app = FastAPI()
        readers = {"split": SplitReader(store), "combined": store}
        baseline = None
        counters = {"split": store, "combined": store}
        if baseline_ref:
            store_type, baseline = historical_store(baseline_ref)
            old_store = store_type(Path(directory))
            readers["split"] = SplitReader(old_store)
            counters["split"] = old_store

        @app.get("/snapshot/{mode}")
        async def snapshot(mode: str, request: Request):
            async def first_snapshot():
                async with aclosing(_workflow_run_event_stream(
                    request=request, store=readers[mode], run_id=run_id, after_seq=0,
                )) as events:
                    async for message in events:
                        yield message
                        if "event: workflow.snapshot\n" in message:
                            break

            return StreamingResponse(first_snapshot(), media_type="text/event-stream")

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(
            app, log_level="critical", lifespan="off", access_log=False,
        ))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
        thread.start()
        try:
            deadline = time.monotonic() + 20
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("isolated HTTP server did not start")
                time.sleep(0.01)
            samples = {name: [] for name in readers}
            counts = {name: 0 for name in readers}
            with httpx.Client(trust_env=False, timeout=20) as client:
                def read(name):
                    response = client.get(f"http://127.0.0.1:{port}/snapshot/{name}")
                    response.raise_for_status()
                    assert response.text.count("event: workflow.event\n") == 20
                    assert response.text.count("event: workflow.snapshot\n") == 1
                    return response.content

                for _ in range(5):
                    for name in readers:
                        read(name)
                for index in range(iterations):
                    order = list(readers)
                    if index % 2:
                        order.reverse()
                    results = {}
                    for name in order:
                        before = counters[name].connections
                        started = time.perf_counter()
                        results[name] = read(name)
                        samples[name].append((time.perf_counter() - started) * 1000)
                        counts[name] += counters[name].connections - before
                    assert results["split"] == results["combined"]
            measurements = {name: summary(values) for name, values in samples.items()}
            result = {
                "schema": "workflow_sse_http_pair.v1", "iterations": iterations,
                "measurements": measurements, "connection_counts": counts,
                "p95_reduction_percent": 100 * (
                    1 - measurements["combined"]["p95_ms"] / measurements["split"]["p95_ms"]
                ),
                "equivalent_sse_bytes": True, "event_count": 20,
                "baseline": baseline,
                "notes": [
                    "Real TCP HTTP, one pooled client, alternating order, five warmups each.",
                    "Uses product _workflow_run_event_stream; stops after first snapshot.",
                    ("Split uses the specified historical store module; other dependencies are current."
                     if baseline else "Split is current compatibility events_since plus get, not an archived build."),
                    "Does not measure auth/project scope, active writes, browser rendering or 8784.",
                    "Isolated synthetic run; no media submissions or private data.",
                ],
            }
        finally:
            server.should_exit = True
            thread.join(timeout=20)
            listener.close()
            if thread.is_alive():
                raise RuntimeError("isolated HTTP server did not stop")
    result["temporary_database_removed"] = not Path(directory).exists()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--baseline-ref", help="Git commit/ref for the historical store module only")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 20:
        parser.error("at least 20 iterations are required")
    result = measure(args.iterations, args.baseline_ref)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "measurements"}, indent=2))


if __name__ == "__main__":
    main()
