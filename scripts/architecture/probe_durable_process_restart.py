"""Kill a running isolated worker and exercise real durable startup recovery.

The provider-facing runner is a fixture. Queue, task state, execution core,
receipt persistence, lifecycle and process boundaries are product code.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def worker(root: Path, phase: str, kind: str, provider_task_id: str = "", model: str = "") -> None:
    # Configure isolation before the first product import.
    for key, directory in (
        ("NOVELVIDEO_DATA_ROOT", root),
        ("NOVELVIDEO_STATE_DIR", root / "state"),
        ("NOVELVIDEO_OUTPUT_DIR", root / "output"),
        ("NOVELVIDEO_RUNTIME_DIR", root / "runtime"),
    ):
        directory.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(directory)
    os.environ["ST_EDITION"] = "ce"

    if provider_task_id:
        product_data = Path(__file__).resolve().parents[2] / "项目资产"
        os.environ["NOVELVIDEO_DATA_ROOT"] = str(product_data)
        os.environ["NOVELVIDEO_STATE_DIR"] = str(product_data / "state")
        from novelvideo.env import load_project_dotenv
        load_project_dotenv(override=False)

    from novelvideo.ports.local.durable_queue import DurableQueueStore
    from novelvideo.ports.local.lifecycle import LocalLifecycle
    from novelvideo.ports.local.tasks import DurableInlineTaskBackend
    from novelvideo.ports.registry import ensure_bootstrap, register_port
    from novelvideo.project_context import ProjectContext
    from novelvideo.task_backend.registry import register_project_task_runner
    from novelvideo.task_backend.run_core import _ensure_builtin_runners_registered
    from novelvideo.task_state import get_task_manager

    if provider_task_id:
        # Only the existing model loader reads the formal registry. Queue and
        # task state below use explicit fixture paths; no formal ports bootstrap.
        from novelvideo.ports.local.tasks import InMemoryCancellationStore
        register_port("cancellation_store", InMemoryCancellationStore())
    else:
        ensure_bootstrap()

    ctx = ProjectContext(
        project_id="restart-fixture", project_name="restart-fixture",
        owner_type="user", owner_id="fixture", owner_username="fixture",
        requester_user_id="fixture", requester_username="fixture",
        requester_principals=(("user", "fixture"),), effective_role="editor",
        home_node_id="local", is_home_node=True,
        output_dir=root / "output", state_dir=root / "state",
        runtime_dir=root / "runtime",
    )
    task_type = "freezone_video_gen" if kind == "recoverable_video" else "compose_episode"
    manager = get_task_manager()
    store = DurableQueueStore(root / "queue.sqlite3")
    backend = DurableInlineTaskBackend(durable_store=store)
    _ensure_builtin_runners_registered()

    if provider_task_id:
        from novelvideo.freezone.jobs import run_freezone_video_gen
        import aiohttp
        original_request = aiohttp.ClientSession._request
        network = {"GET": 0, "mutations": 0}

        async def guarded_request(session, method, url, **kwargs):
            if str(method).upper() != "GET":
                network["mutations"] += 1
                raise AssertionError("resume must not submit a mutating request")
            network["GET"] += 1
            return await original_request(session, method, url, **kwargs)

        aiohttp.ClientSession._request = guarded_request

        def real_recovery_runner(envelope, context):
            state = manager.get_task_for_project(context, task_type, 0, scope="same-job")
            assert state is not None
            if phase == "recover":
                assert state.metadata.get("recovery_provider_task_id") == provider_task_id

            def event_observed(event):
                status = str(event.get("upstream_status") or "")
                manager.update_progress_for_project(
                    context, task_type, 0, scope="same-job", progress=0.2,
                    metadata={"provider": "newapi", "provider_task_id": provider_task_id},
                    expected_task_id=state.task_id,
                )
                if phase == "initial" and event.get("stage") == "polling" and status in {
                    "queued", "pending", "running", "processing", "in_progress",
                }:
                    current = manager.get_task_for_project(context, task_type, 0, scope="same-job")
                    _write(root / "ready.json", {
                        "pid": os.getpid(), "task_id": current.task_id,
                        "receipt": current.metadata["task_acceptance_receipt"],
                        "status": current.status, "durable_status": store.pending()[0]["status"],
                        "upstream_status": status, "network": network,
                    })
                    threading.Event().wait(120)
                    raise AssertionError("active polling process was not terminated")

            output_path = asyncio.run(run_freezone_video_gen(
                project_dir=context.output_dir, job_id="same-job", prompt="",
                reference_items=[], duration_seconds=4, backend=model,
                resolution="768p", generate_audio=True,
                resume_provider_task_id=provider_task_id, on_task_event=event_observed,
            ))
            assert phase == "recover", "provider completed before active interruption was observed"
            _write(root / "media.json", {
                "bytes": output_path.stat().st_size,
                "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
                "network": network,
            })
            with (root / "calls.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"operation": "recover", "task_id": state.task_id}) + "\n")
            return {"provider_task_id": provider_task_id}

        fixture_runner_override = real_recovery_runner
    else:
        fixture_runner_override = None

    def fixture_runner(envelope, context):
        state = manager.get_task_for_project(context, task_type, 0, scope="same-job")
        assert state is not None
        if phase == "initial":
            metadata = {"provider": "newapi", "provider_task_id": "fixture-provider-1"}
            manager.update_progress_for_project(
                context, task_type, 0, scope="same-job", progress=0.2,
                metadata=metadata, expected_task_id=state.task_id,
            )
            state = manager.get_task_for_project(context, task_type, 0, scope="same-job")
            _write(root / "ready.json", {
                "pid": os.getpid(), "task_id": state.task_id,
                "receipt": state.metadata["task_acceptance_receipt"],
                "status": state.status, "durable_status": store.pending()[0]["status"],
            })
            # Parent terminates the process while this worker is still running.
            threading.Event().wait(120)
            raise AssertionError("initial process was not terminated in time")
        assert kind == "recoverable_video", "non-resumable running work repeated"
        assert state.metadata.get("recovery_provider_task_id") == "fixture-provider-1"
        assert state.metadata.get("provider_task_id") == "fixture-provider-1"
        assert envelope["__run_task_id"] == state.task_id
        with (root / "calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"operation": "recover", "task_id": state.task_id}) + "\n")
        return {"provider_task_id": "fixture-provider-1", "fixture_recovered": True}

    register_project_task_runner(task_type, fixture_runner_override or fixture_runner)

    async def run() -> None:
        if phase == "initial":
            await backend.enqueue_project_task(
                ctx, task_type=task_type, queue_kind="video", scope="same-job",
                payload={"job_id": "same-job"},
            )
            while True:
                await asyncio.sleep(0.05)
        else:
            with patch("novelvideo.ports.get_task_backend", return_value=backend):
                await LocalLifecycle().on_startup()
                # A second startup in this process must not schedule a duplicate.
                await LocalLifecycle().on_startup()
            deadline = time.monotonic() + (1200 if provider_task_id else 30)
            while backend._background_tasks:
                if time.monotonic() > deadline:
                    raise TimeoutError("recovery worker did not settle")
                await asyncio.sleep(0.02)
            state = manager.get_task_for_project(ctx, task_type, 0, scope="same-job")
            _write(root / "recovered.json", {
                "pid": os.getpid(), "task_id": state.task_id,
                "status": state.status, "error": state.error,
                "receipt": state.metadata.get("task_acceptance_receipt"),
                "provider_task_id": state.metadata.get("provider_task_id"),
                "pending": store.count_pending(),
            })
            backend.close()

    asyncio.run(run())


def run(output: Path, provider_task_id: str = "", model: str = "") -> None:
    results = []
    with tempfile.TemporaryDirectory(prefix="t255-durable-restart-") as temporary:
        for kind in (("recoverable_video",) if provider_task_id else ("recoverable_video", "non_resumable_running")):
            root = Path(temporary) / kind
            root.mkdir()
            command = [sys.executable, str(Path(__file__).resolve()), "--worker",
                       "--root", str(root), "--kind", kind]
            if provider_task_id:
                command.extend(["--provider-task-id", provider_task_id, "--model", model])
            with (root / "initial.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(command + ["--phase", "initial"], stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 40
                    while not (root / "ready.json").is_file():
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError(f"initial worker failed for {kind}; inspect fixture log")
                        time.sleep(0.05)
                    initial = json.loads((root / "ready.json").read_text(encoding="utf-8"))
                    assert initial["status"] == "running"
                    assert initial["durable_status"] == "running"
                finally:
                    if process.poll() is None:
                        if os.name == "nt":
                            # A venv launcher may own a child interpreter. Kill
                            # the entire test process tree, never the API PID.
                            subprocess.run(
                                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                capture_output=True, check=True, timeout=10,
                            )
                        else:
                            process.kill()
                    process.wait(timeout=10)
            # Preserve the same files, create a genuinely new Python process.
            completed = subprocess.run(command + ["--phase", "recover"],
                                       capture_output=True, timeout=1250 if provider_task_id else 45)
            if completed.returncode:
                raise RuntimeError(f"recovery worker failed for {kind}: {completed.stderr.decode(errors='replace')[-2000:]}")
            recovered = json.loads((root / "recovered.json").read_text(encoding="utf-8"))
            calls_path = root / "calls.jsonl"
            calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()] if calls_path.is_file() else []
            assert initial["pid"] != recovered["pid"]
            assert recovered["task_id"] == initial["task_id"]
            assert recovered["receipt"] == initial["receipt"]
            assert recovered["pending"] == 0
            assert recovered["provider_task_id"] == (provider_task_id or "fixture-provider-1")
            if kind == "recoverable_video":
                assert recovered["status"] == "completed"
                assert calls == [{"operation": "recover", "task_id": initial["task_id"]}]
            else:
                assert recovered["status"] == "failed"
                assert "重复计费" in recovered["error"]
                assert calls == []
            results.append({"kind": kind, "initial": initial, "recovered": recovered,
                            "runner_calls_after_restart": calls, "ok": True})
            if provider_task_id:
                results[-1]["media"] = json.loads((root / "media.json").read_text(encoding="utf-8"))
    report = {
        "schema": "durable_process_restart.v1", "ok": True,
        "cases": results, "temporary_fixture_cleaned": True,
        "real_provider_calls": bool(provider_task_id),
        "limitations": [
            "Provider-facing runner is a local fixture; no real upstream or media output is verified.",
            "Real subprocess termination and LocalLifecycle startup are exercised, not the full HTTP server launcher.",
            "Recovery counts cover this fixture only, not provider billing or product-wide duplicate rate.",
        ],
    }
    if provider_task_id:
        report["limitations"] = [
            "Initial paid generation belongs to a separate CLI project; this isolated queue tests its active polling leg only.",
            "LocalLifecycle, queue, task state and jobs/adapter are real; the runner wrapper supplies a fixed isolated ProjectContext.",
            "The 8784 service is not restarted; no provider billing ledger or whole-product duplicate rate is measured.",
        ]
    output.parent.mkdir(parents=True, exist_ok=True)
    _write(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--phase", choices=("initial", "recover"))
    parser.add_argument("--kind", choices=("recoverable_video", "non_resumable_running"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--provider-task-id", default="")
    parser.add_argument("--model", default="direct_video-8b01a39b81951012")
    args = parser.parse_args()
    if args.worker:
        worker(args.root, args.phase, args.kind, args.provider_task_id, args.model)
    elif args.output:
        run(args.output, args.provider_task_id, args.model)
    else:
        parser.error("--output is required")
