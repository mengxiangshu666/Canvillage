"""Acceptance: startup converges a terminal Run without another Run read."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
os.environ.setdefault("ST_EDITION", "ce")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_evidence(payload: dict) -> tuple[Path, str]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir = (
        ROOT
        / "workspace"
        / "artifacts"
        / "t135-startup-terminal-receipt-recovery"
        / "runs"
        / f"{stamp}-{payload['workflow_run_id']}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    path = output_dir / "evidence.json"
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    path.write_bytes(encoded + b"\n")
    return path, hashlib.sha256(encoded + b"\n").hexdigest()


async def _seed_isolated_state(state_root: Path):
    from novelvideo.chat import service as chat_service
    from novelvideo.ports.local.project import SQLiteProjectRegistry
    from novelvideo.workflow_runtime.definitions import get_workflow_definition
    from novelvideo.workflow_runtime.store import WorkflowRunStore

    project_root = state_root / "project"
    project_state_dir = project_root / "state"
    project_output_dir = project_root / "output"
    project_runtime_dir = project_root / "runtime"
    registry = SQLiteProjectRegistry()
    project = await registry.create_project(
        owner_user_id="local",
        owner_username="local",
        name="t135-startup-receipt",
        output_dir=str(project_output_dir),
        state_dir=str(project_state_dir),
        runtime_dir=str(project_runtime_dir),
    )
    definition = get_workflow_definition("freezone-script-contract")
    if definition is None:
        raise RuntimeError("freezone-script-contract definition is missing")
    store = WorkflowRunStore(project_state_dir)
    run, _ = await store.create(
        definition=definition,
        project_id=project.id,
        canvas_id="canvas-t135",
        run_mode="draft",
        inputs={"request": "隔离启动回执验收"},
        idempotency_key=f"t135:{uuid.uuid4().hex}",
        contract_version=1,
    )
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE canvas_workflow_runs SET status='completed' WHERE id=?",
            (run["id"],),
        )
        conn.commit()
    chat_service.add_assistant_message(
        "local",
        project.id,
        "任务已经进入后台执行，目前仍在进行中。",
        project_state_dir=project_state_dir,
        conversation_id="main",
        canvas_id="canvas-t135",
        turn_id="turn-t135",
        metadata={
            "delivery_verification": {
                "status": "in_progress",
                "workflow_run_id": run["id"],
            }
        },
    )
    before = await store.get(run["id"])
    if before is None:
        raise RuntimeError("seeded WorkflowRun is missing")
    return project, project_state_dir, run["id"], before


def _wait_for_health(
    *,
    process: subprocess.Popen[bytes],
    base_url: str,
    timeout_seconds: float,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"isolated API exited early with code {process.returncode}"
            )
        try:
            with urllib.request.urlopen(
                f"{base_url}/healthz",
                timeout=1.5,
            ) as response:
                body = response.read().decode("utf-8")
                return {
                    "status_code": int(response.status),
                    "body": json.loads(body),
                }
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            time.sleep(0.25)
    raise TimeoutError(f"isolated API did not become healthy: {last_error}")


async def _run() -> dict:
    with tempfile.TemporaryDirectory(
        prefix="village-canvas-t135-",
        ignore_cleanup_errors=True,
    ) as temp:
        state_root = Path(temp)
        os.environ["NOVELVIDEO_STATE_DIR"] = str(state_root)
        from novelvideo.chat import service as chat_service
        from novelvideo.workflow_runtime.store import WorkflowRunStore

        project, project_state_dir, run_id, before = await _seed_isolated_state(
            state_root
        )

        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        log_path = state_root / "api.log"
        env = os.environ.copy()
        env["NOVELVIDEO_STATE_DIR"] = str(state_root)
        env["ST_EDITION"] = "ce"
        env["PYTHONPATH"] = os.pathsep.join(
            [str(SRC), str(ROOT), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        creationflags = (
            subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        )
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [
                    str(ROOT / ".venv" / "Scripts" / "python.exe")
                    if os.name == "nt"
                    else sys.executable,
                    "-m",
                    "uvicorn",
                    "novelvideo.api.app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--log-level",
                    "warning",
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
            try:
                health = _wait_for_health(
                    process=process,
                    base_url=base_url,
                    timeout_seconds=90.0,
                )
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)

        store = WorkflowRunStore(project_state_dir)
        after = await store.get(run_id)
        messages = chat_service.list_messages(
            "local",
            project.id,
            project_state_dir=project_state_dir,
            conversation_id="main",
            canvas_id="canvas-t135",
        )
        if after is None or len(messages) != 1:
            raise AssertionError("isolated recovery result is missing")
        message = messages[0]
        receipt = message.get("metadata", {}).get("workflow_terminal_receipt", {})
        delivery = message.get("metadata", {}).get("delivery_verification", {})
        if delivery.get("status") == "in_progress":
            raise AssertionError("assistant turn stayed in_progress after restart")
        if receipt.get("workflow_run_id") != run_id:
            raise AssertionError("terminal receipt is not bound to the seeded Run")
        for key in ("status", "revision", "event_seq", "updated_at"):
            if before.get(key) != after.get(key):
                raise AssertionError(f"startup recovery changed WorkflowRun {key}")

        log_handle = log_path.open("r", encoding="utf-8", errors="replace")
        try:
            api_log = log_handle.read()
        finally:
            log_handle.close()
        return {
            "schema": "t135_startup_terminal_receipt_recovery.v1",
            "status": "passed",
            "project_id": project.id,
            "canvas_id": "canvas-t135",
            "workflow_run_id": run_id,
            "health": health,
            "run_before": {
                "status": before.get("status"),
                "revision": before.get("revision"),
                "event_seq": before.get("event_seq"),
                "updated_at": before.get("updated_at"),
            },
            "run_after": {
                "status": after.get("status"),
                "revision": after.get("revision"),
                "event_seq": after.get("event_seq"),
                "updated_at": after.get("updated_at"),
            },
            "assistant_message": {
                "message_id": message["id"],
                "delivery_status": delivery.get("status"),
                "terminal_status": receipt.get("status"),
                "contains_in_progress_marker": "目前仍在进行中"
                in message["content"],
            },
            "paid_provider_call_expected": False,
            "api_log_tail": api_log[-4000:],
        }


def main() -> int:
    try:
        evidence = asyncio.run(_run())
    except Exception as exc:
        evidence = {
            "schema": "t135_startup_terminal_receipt_recovery.v1",
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "workflow_run_id": "unavailable",
        }
    path, sha256 = _write_evidence(evidence)
    print(
        json.dumps(
            {
                "status": evidence["status"],
                "evidence": str(path),
                "sha256": sha256,
                "workflow_run_id": evidence.get("workflow_run_id"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if evidence["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
