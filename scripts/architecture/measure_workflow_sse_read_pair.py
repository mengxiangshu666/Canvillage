"""Compare split and combined SSE reads on the same isolated persisted run."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import tempfile
import time

from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.workflow_runtime.store import WorkflowRunStore


class CountingStore(WorkflowRunStore):
    connections = 0

    @asynccontextmanager
    async def _connect(self):
        self.connections += 1
        async with super()._connect() as db:
            yield db


def summary(samples):
    ordered = sorted(samples)
    return {
        "median_ms": ordered[len(ordered) // 2],
        "p95_ms": ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)],
        "samples_ms": samples,
    }


async def measure(iterations):
    with tempfile.TemporaryDirectory(prefix="t255-sse-read-pair-") as directory:
        store = CountingStore(Path(directory))
        definition = get_workflow_definition("one-click-film")
        assert definition is not None
        run, _ = await store.create(
            definition=definition, project_id="isolated", canvas_id="isolated",
            run_mode="draft", inputs={"request": "read measurement"},
            idempotency_key="read-measurement", contract_version=1,
        )
        for index in range(20):
            _, accepted = await store.record_event(
                run["id"], event_id=f"event-{index}", event_type="step_started",
                step_id="canvas_structure",
            )
            assert accepted

        async def split():
            page = await store.events_since(run["id"], after_seq=0, limit=200)
            current = await store.get(run["id"])
            return {"page": page, "run": current}

        async def combined():
            return await store.events_since_with_run(run["id"], after_seq=0, limit=200)

        readers = {"split_current_compatibility": split, "combined": combined}
        samples = {name: [] for name in readers}
        connections = {name: 0 for name in readers}
        for _ in range(5):
            for read in readers.values():
                await read()
        for index in range(iterations):
            names = list(readers)
            if index % 2:
                names.reverse()
            results = {}
            for name in names:
                before = store.connections
                started = time.perf_counter()
                results[name] = await readers[name]()
                samples[name].append((time.perf_counter() - started) * 1000)
                connections[name] += store.connections - before
            assert results[names[0]] == results[names[1]]
            assert len(results[names[0]]["page"]["items"]) == 20
        measurements = {name: summary(values) for name, values in samples.items()}
        split_p95 = measurements["split_current_compatibility"]["p95_ms"]
        result = {
            "schema": "workflow_sse_read_pair.v1", "iterations": iterations,
            "event_count": 20, "measurements": measurements,
            "connection_counts": connections,
            "p95_reduction_percent": 100 * (1 - measurements["combined"]["p95_ms"] / split_p95),
            "equivalent_results": True, "media_submissions": 0,
            "notes": [
                "Alternating order, same database and current code, five warmups each.",
                "Split uses current compatibility events_since plus get, not an archived implementation.",
                "This measures storage reads, not HTTP or an old/new product performance claim.",
            ],
        }
    result["temporary_database_removed"] = not Path(directory).exists()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 20:
        parser.error("at least 20 iterations are required")
    result = asyncio.run(measure(args.iterations))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "measurements"}, indent=2))


if __name__ == "__main__":
    main()
