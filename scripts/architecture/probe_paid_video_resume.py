"""One CLI-paid video, followed by observed recovery in a fresh process."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback
import uuid

def task_state(project_id, scope):
    completed = subprocess.run([
        sys.executable, "-m", "novelvideo.canvas_cli", "--json", "--project", project_id,
        "task", "inspect", "--task-type", "freezone_video_gen", "--episode", "0", "--scope", scope,
    ], capture_output=True, text=True, encoding="utf-8", check=True, timeout=30)
    return json.loads(completed.stdout)["data"]

def worker(args):
    # Match the formal launcher's state root before any product import. Output
    # remains isolated; credentials are resolved by the existing product loader.
    root = Path(__file__).resolve().parents[2]
    data = root / "项目资产"
    os.environ["NOVELVIDEO_DATA_ROOT"] = str(data)
    os.environ["NOVELVIDEO_STATE_DIR"] = str(data / "state")
    os.environ["NOVELVIDEO_OUTPUT_DIR"] = str(args.project_dir / "output")
    os.environ["NOVELVIDEO_RUNTIME_DIR"] = str(args.project_dir / "runtime")
    # The packaged launcher loads the project dotenv before importing model
    # settings; a fresh Python process must do the same.
    from novelvideo.env import load_project_dotenv
    load_project_dotenv(override=False)
    os.environ.setdefault("ST_EDITION", "ce")
    from novelvideo.generators.video.direct_models import list_direct_video_models
    registry = list_direct_video_models()
    import aiohttp
    from novelvideo.freezone.jobs import run_freezone_video_gen
    import novelvideo.generators.video_generator as module

    original_factory = module.create_video_generator
    counts = {"generate": 0, "recover": 0, "submit_requests": 0, "query_requests": 0}
    network = {"GET": 0, "POST": 0, "other": 0}
    events = []
    original_network_request = aiohttp.ClientSession._request

    async def network_request(session, method, url, **kwargs):
        method = str(method).upper()
        network[method if method in network else "other"] += 1
        if method != "GET":
            counts["submit_requests"] += 1
            raise AssertionError("recovery attempted a mutating network request")
        if args.provider_task_id in str(url):
            counts["query_requests"] += 1
        return await original_network_request(session, method, url, **kwargs)

    aiohttp.ClientSession._request = network_request

    def factory(**kwargs):
        generator = original_factory(**kwargs)
        original_request = getattr(generator, "_request_json", None)
        original_recover = generator.recover_task

        async def request(method, url, **options):
            stage = options.get("stage")
            if stage == "submit":
                counts["submit_requests"] += 1
                raise AssertionError("recovery attempted a paid submit")
            if stage == "query":
                counts["query_requests"] += 1
            return await original_request(method, url, **options)

        async def generate(**_kwargs):
            counts["generate"] += 1
            raise AssertionError("recovery entered generate")

        async def recover(**kwargs):
            counts["recover"] += 1
            return await original_recover(**kwargs)

        if original_request is not None:
            generator._request_json = request
        generator.generate = generate
        generator.recover_task = recover
        return generator

    module.create_video_generator = factory
    try:
        output = asyncio.run(run_freezone_video_gen(
            project_dir=args.project_dir, job_id="paid-recovery",
            prompt="", reference_items=[], duration_seconds=4,
            backend=args.model, resolution="768p", generate_audio=True,
            generate_audio_explicit=True, requested_generate_audio=True,
            resume_provider_task_id=args.provider_task_id,
            on_task_event=lambda event: events.append(str(event.get("stage") or "")),
        ))
        report = {"counts": counts, "network": network, "event_stages": events, "output_exists": output.is_file(),
                  "output_bytes": output.stat().st_size,
                  "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
        if args.retain_video:
            target = args.retain_video.resolve()
            asset_root = (root / "项目资产").resolve()
            if not target.is_relative_to(asset_root):
                raise ValueError("retained media must stay within project assets")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(output, target)
            report["retained_video"] = str(target)
        report["ok"] = output.is_file() and counts["generate"] == 0 and counts["recover"] == 1 and counts["submit_requests"] == 0
    except Exception as exc:
        report = {"ok": False, "counts": counts, "network": network, "error_type": type(exc).__name__,
                  "registry_ids": [item.registry_id for item in registry],
                  "registry_enabled": [item.registry_id for item in registry if item.enabled]}
        report["error_frames"] = [{"file": Path(frame.filename).name, "line": frame.lineno,
                                   "function": frame.name} for frame in traceback.extract_tb(exc.__traceback__)]
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


def run(args):
    from profile_canvas_gesture import cli
    if not args.allow_paid_generation and not args.provider_task_id:
        raise SystemExit("--allow-paid-generation required for one paid sample")
    project_id = ""
    report = {"schema": "paid_video_cross_process_recovery.v1", "model": args.model, "ok": False}
    if args.provider_task_id:
        # A persisted upstream handle is sufficient even after fixture cleanup.
        # This branch must never create a project or enter the submit CLI.
        report["provider_task_id"] = args.provider_task_id
        report["mode"] = "existing_provider_task_only"
        with tempfile.TemporaryDirectory(prefix="t255-real-resume-") as directory:
            worker_output = Path(directory) / "result.json"
            recovered = subprocess.run([
                sys.executable, str(Path(__file__).resolve()), "--worker", "--model", args.model,
                "--provider-task-id", args.provider_task_id, "--project-dir", str(Path(directory) / "project"),
                "--output", str(worker_output),
            ] + (["--retain-video", str(args.retain_video)] if args.retain_video else []),
                capture_output=True, text=True, encoding="utf-8", timeout=1200)
            report["worker_exit_code"] = recovered.returncode
            if worker_output.is_file():
                report["recovery"] = json.loads(worker_output.read_text(encoding="utf-8"))
            report["ok"] = recovered.returncode == 0 and report.get("recovery", {}).get("ok") is True
        report["temporary_output_cleaned"] = True
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    try:
        project_id = args.resume_project or cli("post", "/projects", {"name": f"T255_resume_{uuid.uuid4().hex[:12]}"})["id"]
        command = [sys.executable, "-m", "novelvideo.canvas_cli", "--json", "--project", project_id,
                   "gen", "video", "--model", args.model, "--mode", "textToVideo", "--duration", "4",
                   "--quality", "768p", "--aspect-ratio", "16:9", "--generate-audio", "--prompt",
                   args.prompt]
        if args.resume_project:
            previous = json.loads(args.output.read_text(encoding="utf-8"))
            data = previous["receipt"]
            scope = args.resume_scope
        else:
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=90)
            if completed.returncode:
                raise RuntimeError("CLI submission failed")
            receipt = json.loads(completed.stdout)
            if not receipt.get("ok"):
                raise RuntimeError("CLI submission not accepted")
            data = receipt["data"]
            scope = data.get("job_id") or data.get("scope")
        if not scope:
            raise RuntimeError("receipt missing task scope")
        report["receipt"] = {key: data.get(key) for key in ("job_id", "task_type", "task_key", "task_id")}
        report["project_id"] = project_id
        # Persist the accepted handle before observing any provider progress.
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        deadline = time.monotonic() + 1200
        task = None
        while time.monotonic() < deadline:
            task = task_state(project_id, scope)
            provider_id = (task.get("metadata") or {}).get("provider_task_id")
            if args.active_restart and provider_id and task["status"] not in ("completed", "failed", "cancelled") and "active_restart" not in report:
                report["provider_task_id"] = provider_id
                args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                active_output = args.output.with_name(args.output.stem + "-active.json")
                active = subprocess.run([
                    sys.executable, str(Path(__file__).with_name("probe_durable_process_restart.py")),
                    "--provider-task-id", str(provider_id), "--model", args.model,
                    "--output", str(active_output),
                ], capture_output=True, timeout=1350)
                report["active_restart"] = {"exit_code": active.returncode, "output": str(active_output)}
                if active_output.is_file():
                    report["active_restart"]["evidence"] = json.loads(active_output.read_text(encoding="utf-8"))
                args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            if task["status"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(3)
        if task is None or task["status"] not in ("completed", "failed", "cancelled"):
            raise RuntimeError("accepted task still active; inspect same receipt before cleanup")
        report["original_status"] = task["status"]
        # Keep a bounded terminal receipt before the isolated project is purged.
        # Never dump provider error strings, signed URLs or arbitrary metadata.
        report["terminal_evidence"] = {
            "status": task["status"],
            "error_present": bool(task.get("error") or task.get("error_message")),
            "provider_handle_present": bool(
                (task.get("metadata") or {}).get("provider_task_id")
                or (task.get("result") or {}).get("provider_task_id")
            ),
        }
        provider_id = (task.get("metadata") or {}).get("provider_task_id") or (task.get("result") or {}).get("provider_task_id")
        if not provider_id:
            raise RuntimeError("terminal task missing provider handle")
        report["provider_task_id"] = provider_id
        if args.active_restart:
            report["ok"] = task["status"] == "completed" and report.get("active_restart", {}).get("exit_code") == 0
            report["limitations"] = [
                "Active polling recovery uses the existing provider handle in an isolated durable queue, not a restart of 8784.",
                "Initial CLI submit counters and provider billing ledger are not instrumented.",
            ]
            return 0 if report["ok"] else 1
        with tempfile.TemporaryDirectory(prefix="t255-real-resume-") as directory:
            worker_output = Path(directory) / "result.json"
            recovered = subprocess.run([
                sys.executable, str(Path(__file__).resolve()), "--worker", "--model", args.model,
                "--provider-task-id", provider_id, "--project-dir", str(Path(directory) / "project"),
                "--output", str(worker_output),
            ] + (["--retain-video", str(args.retain_video)] if args.retain_video else []),
                capture_output=True, text=True, encoding="utf-8", timeout=1200)
            report["worker_exit_code"] = recovered.returncode
            if worker_output.is_file():
                report["recovery"] = json.loads(worker_output.read_text(encoding="utf-8"))
            report["ok"] = recovered.returncode == 0 and report.get("recovery", {}).get("ok") is True
        report["limitations"] = [
            "Initial submit uses running CLI/API; recovery exercises product jobs/adapter in a new process, not /recover HTTP.",
            "Counts recovery adapter requests, not initial upstream retries or provider billing ledger.",
            "One sample cannot establish a population duplicate-submit rate.",
        ]
    except Exception as exc:
        report["error_type"] = type(exc).__name__
    finally:
        if project_id and report.get("original_status") in ("completed", "failed", "cancelled"):
            cli("post", f"/projects/{project_id}/delete", {})
            report["cleanup"] = cli("post", f"/projects/{project_id}/purge", {})
        else:
            report["retained_project_id"] = project_id
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--allow-paid-generation", action="store_true")
    parser.add_argument("--active-restart", action="store_true")
    parser.add_argument("--model", default="direct_video-8b01a39b81951012")
    parser.add_argument("--prompt", default=(
        "A wooden pendulum swings slowly inside an empty workshop. Static medium shot, "
        "natural daylight, realistic cinematic texture. Four seconds. Only quiet ticking "
        "and room ambience. No speech, no subtitles, no text, no letters, no logos, "
        "no background music."
    ))
    parser.add_argument("--resume-project")
    parser.add_argument("--resume-scope")
    parser.add_argument("--provider-task-id")
    parser.add_argument("--project-dir", type=Path)
    parser.add_argument("--retain-video", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return worker(args) if args.worker else run(args)


if __name__ == "__main__":
    raise SystemExit(main())
