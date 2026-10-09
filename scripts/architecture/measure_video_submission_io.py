"""Measure the provider lifecycle of one isolated video submission.

The fake upstream is in-process and never reaches a paid gateway.  The output
is a baseline for provider I/O only; it deliberately does not claim anything
about real network latency or production duplicate-submit rates.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

from novelvideo.generators.video.generic_video_adapter import (
    GenericVideoAdapterGenerator,
)


async def _measure_once() -> dict[str, object]:
    request_counts = {"submit": 0, "query": 0}
    download_count = 0
    event_stages: list[str] = []

    async def fake_request(method: str, _url: str, **_kwargs: object) -> dict[str, object]:
        if method == "POST":
            request_counts["submit"] += 1
            return {"id": "baseline-task-1", "status": "starting"}
        request_counts["query"] += 1
        return {"id": "baseline-task-1", "status": "succeeded", "output": "https://fixture/video.mp4"}

    async def fake_download(_url: str, output_path: str) -> None:
        nonlocal download_count
        download_count += 1
        Path(output_path).write_bytes(b"isolated-video-fixture")

    generator = GenericVideoAdapterGenerator(
        api_key="fixture-key",
        endpoint="https://fixture.invalid",
        model="video-baseline",
        adapter_family="prediction",
        poll_interval=0.001,
        max_polls=3,
    )
    generator._request_json = fake_request  # type: ignore[method-assign]
    generator._download = fake_download  # type: ignore[method-assign]

    with tempfile.TemporaryDirectory(prefix="village-video-io-") as temp_dir:
        output_path = str(Path(temp_dir) / "result.mp4")
        with patch(
            "novelvideo.generators.video_generator._reserve_video_model_call",
            new=AsyncMock(return_value=""),
        ):
            result = await generator.generate(
                prompt="isolated provider lifecycle fixture",
                output_path=output_path,
                duration=5,
                aspect_ratio="16:9",
                resolution="720p",
                idempotency_key="video-io-baseline-1",
                on_task_event=lambda event: event_stages.append(str(event.get("stage") or "")),
            )
        output_exists = Path(output_path).is_file()

    return {
        "result_status": str(result.status.value),
        "provider_task_id": result.provider_task_id,
        "counts": {
            "provider_submit": request_counts["submit"],
            "provider_query": request_counts["query"],
            "provider_download": download_count,
        },
        "event_stages": event_stages,
        "output_written": output_exists,
        "duplicate_provider_submit_count": None,
    }


async def _measure(iterations: int) -> dict[str, object]:
    samples: list[float] = []
    runs: list[dict[str, object]] = []
    for _ in range(iterations):
        started = time.perf_counter()
        runs.append(await _measure_once())
        samples.append(time.perf_counter() - started)
    ordered = sorted(samples)
    p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))
    first = runs[0]
    return {
        "schema": "video_submission_io_baseline.v1",
        "mode": "isolated_fake_provider",
        "iterations": iterations,
        "result_status": first["result_status"],
        "provider_task_id": first["provider_task_id"],
        "counts_per_submission": first["counts"],
        "event_stages": first["event_stages"],
        "output_written": all(bool(run["output_written"]) for run in runs),
        "timing_seconds": {
            "min": round(min(samples), 6),
            "median": round(ordered[len(ordered) // 2], 6),
            "p95": round(ordered[p95_index], 6),
            "max": round(max(samples), 6),
        },
        "duplicate_provider_submit_count": None,
        "notes": [
            "No paid provider, 8784 process, database, or private asset was touched.",
            "Timing measures local Python and fake I/O overhead, not production network latency.",
            "Duplicate-submit rate requires a task-backend replay fixture and is not inferred from one adapter call.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--iterations", type=int, default=20)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be at least 1")
    payload = asyncio.run(_measure(args.iterations))
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
