"""Measure real 8784 WorkflowRun reads while an isolated run reaches its gate.

This wraps the existing no-paid T-151 smoke. It creates and cleans a temporary
project, counts HTTP reads, and opens the Run SSE stream while the normal smoke
polls the Run detail endpoint. No private project or media provider is touched.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[2]
T151_PATH = ROOT / "scripts" / "acceptance" / "t151_production_plan_8784_smoke.py"
API_BASE = "http://127.0.0.1:8784/api/v1"


def _load_t151():
    acceptance_dir = str(T151_PATH.parent)
    if acceptance_dir not in sys.path:
        sys.path.insert(0, acceptance_dir)
    spec = importlib.util.spec_from_file_location("t151_runtime_io", T151_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {T151_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _classify(url: str) -> str:
    path = url.split("?", 1)[0]
    if "/workflow-runs/" in path and path.endswith("/events/stream"):
        return "workflow_run_sse"
    if "/workflow-runs/" in path:
        return "workflow_run_detail_get"
    if "/workflow-runs" in path:
        return "workflow_run_list_get"
    if path.endswith("/tasks"):
        return "tasks_list_get"
    return "other"


def run(output: Path) -> dict[str, Any]:
    module = _load_t151()
    counts: dict[str, int] = {}
    lock = threading.Lock()
    original_http_json = module._http_json

    def counted_http_json(method: str, url: str, **kwargs: Any) -> Any:
        key = _classify(url) if method.upper() == "GET" else "other_write"
        with lock:
            counts[key] = counts.get(key, 0) + 1
        return original_http_json(method, url, **kwargs)

    module._http_json = counted_http_json
    sse_summary: dict[str, Any] = {
        "connections": 0,
        "events": {},
        "lines": 0,
        "error": "",
    }
    stop = threading.Event()
    sse_thread: threading.Thread | None = None

    original_run_until_plan = module._run_until_plan

    def run_until_plan(project_id: str, run_id: str) -> dict[str, Any]:
        nonlocal sse_thread

        def consume_sse() -> None:
            url = (
                f"{API_BASE}/projects/{project_id}/workflow-runs/"
                f"{run_id}/events/stream?after_seq=0"
            )
            try:
                with lock:
                    sse_summary["connections"] += 1
                with httpx.stream("GET", url, timeout=None) as response:
                    response.raise_for_status()
                    current_event = "message"
                    for line in response.iter_lines():
                        if stop.is_set():
                            break
                        if not line:
                            continue
                        with lock:
                            sse_summary["lines"] += 1
                        if line.startswith("event:"):
                            current_event = line.partition(":")[2].strip() or "message"
                            with lock:
                                events = sse_summary["events"]
                                events[current_event] = events.get(current_event, 0) + 1
            except Exception as exc:  # noqa: BLE001 - evidence records the failure
                with lock:
                    sse_summary["error"] = f"{type(exc).__name__}: {exc}"

        sse_thread = threading.Thread(target=consume_sse, daemon=True)
        sse_thread.start()
        return original_run_until_plan(project_id, run_id)

    module._run_until_plan = run_until_plan
    started = time.perf_counter()
    try:
        evidence = module.run_smoke()
    finally:
        stop.set()
        if sse_thread is not None:
            sse_thread.join(timeout=5)
    elapsed = round(time.perf_counter() - started, 3)
    result = {
        "schema": "workflow_run_runtime_io_baseline.v1",
        "base_url": API_BASE,
        "elapsed_seconds": elapsed,
        "request_counts": dict(sorted(counts.items())),
        "sse": sse_summary,
        "smoke": {
            "ok": evidence.get("ok") is True,
            "provider_calls_started": evidence.get("providerCallsStarted"),
            "media_submission_started": evidence.get("mediaSubmissionStarted"),
            "project_id": evidence.get("projectId"),
            "run_id": evidence.get("runId"),
            "cleanup": evidence.get("cleanup"),
        },
        "notes": [
            "The temporary T-151 project is cleaned after the run.",
            "No private project, canvas, media, or provider task is read or written.",
            "SSE and GET counts are runtime HTTP observations, not a provider latency claim.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "workspace" / "artifacts" / "architecture" / "workflow-run-runtime-io-baseline-20261005.json",
    )
    args = parser.parse_args()
    result = run(args.output)
    return 0 if result["smoke"]["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
