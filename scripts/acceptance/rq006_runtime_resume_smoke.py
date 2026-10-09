"""Exercise RQ-006 through the running 8784 API with one real image job.

The smoke is intentionally isolated: it creates a temporary project and
canvas, pauses and resumes a draft WorkflowRun, submits one low-cost image
generation, verifies the downloaded bytes, and purges the temporary project.
The generated image is copied to ``workspace/artifacts/`` before cleanup.

Paid generation is fail-closed. Passing ``--allow-paid-generation`` is an
explicit authorization for exactly one image request.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import time
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image, UnidentifiedImageError

from novelvideo.chat.execution_context import build_execution_context
from novelvideo.task_backend.receipts import TASK_ACCEPTANCE_RECEIPT_SCHEMA


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_DIR = ROOT / "workspace" / "artifacts" / "rq006-runtime"
LATEST_SMOKE_FILENAME = "latest-smoke.json"
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"{response.request.method} {response.request.url} returned non-JSON "
            f"HTTP {response.status_code}: {response.text[:400]}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"{response.request.method} {response.request.url} returned "
            f"{type(payload).__name__}, expected an object"
        )
    return payload


class RuntimeClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        expected: tuple[int, ...] = (200,),
        **kwargs: Any,
    ) -> dict[str, Any]:
        response = self.client.request(method, path, **kwargs)
        payload = _json(response)
        if response.status_code not in expected:
            raise RuntimeError(
                f"{method} {path} returned HTTP {response.status_code}: "
                f"{json.dumps(payload, ensure_ascii=False)[:1200]}"
            )
        return payload

    def get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self.request("DELETE", path, **kwargs)


def _data(payload: dict[str, Any]) -> Any:
    if payload.get("ok") is not True or "data" not in payload:
        raise RuntimeError(f"runtime response is not successful: {payload}")
    return payload["data"]


def _project_path(project_id: str, suffix: str) -> str:
    return f"/api/v1/projects/{project_id}/{suffix.lstrip('/')}"


def _get_run(client: RuntimeClient, project_id: str, run_id: str) -> dict[str, Any]:
    data = _data(
        client.get(_project_path(project_id, f"workflow-runs/{run_id}"))
    )
    if not isinstance(data, dict):
        raise RuntimeError("workflow run readback was not an object")
    return data


def _wait_for_run(
    client: RuntimeClient,
    project_id: str,
    run_id: str,
    *,
    statuses: set[str] | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = _get_run(client, project_id, run_id)
        status = str(latest.get("status") or "")
        if statuses is None or status in statuses:
            return latest
        if status in TERMINAL_RUN_STATUSES and status not in (statuses or set()):
            break
        time.sleep(0.5)
    raise RuntimeError(
        f"workflow run {run_id} did not reach {sorted(statuses or [])}; "
        f"latest={json.dumps(latest, ensure_ascii=False)[:1000]}"
    )


def _command_run(
    client: RuntimeClient,
    project_id: str,
    run_id: str,
    *,
    command: str,
    idempotency_key: str,
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    last_error: RuntimeError | None = None
    for _ in range(5):
        run = _get_run(client, project_id, run_id)
        body: dict[str, Any] = {
            "command": command,
            "idempotency_key": idempotency_key,
            "expected_revision": int(run.get("revision") or 0),
        }
        if execution_context is not None:
            body["execution_context"] = execution_context
        response = client.client.post(
            _project_path(project_id, f"workflow-runs/{run_id}/command"),
            json=body,
        )
        payload = _json(response)
        if response.status_code == 200:
            data = _data(payload)
            if not isinstance(data, dict):
                raise RuntimeError("workflow command response was not an object")
            return data
        if response.status_code == 409:
            last_error = RuntimeError(
                f"workflow command {command} conflicted: "
                f"{json.dumps(payload, ensure_ascii=False)[:800]}"
            )
            time.sleep(0.2)
            continue
        raise RuntimeError(
            f"workflow command {command} returned HTTP {response.status_code}: "
            f"{json.dumps(payload, ensure_ascii=False)[:1000]}"
        )
    raise RuntimeError(str(last_error or f"workflow command {command} failed"))


def _workflow_events(
    client: RuntimeClient,
    project_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    data = _data(
        client.get(
            _project_path(project_id, f"workflow-runs/{run_id}/events"),
            params={"after_seq": 0, "limit": 500},
        )
    )
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("workflow events response did not contain an items list")
    return [item for item in items if isinstance(item, dict)]


def _task_snapshot(
    client: RuntimeClient,
    project_id: str,
    task_key: str,
) -> dict[str, Any]:
    response = client.get(
        _project_path(project_id, "tasks"),
        params={"include_runs": True, "run_limit": 200},
    )
    data = response.get("data")
    candidates: list[dict[str, Any]] = []
    if isinstance(data, list):
        candidates.extend(item for item in data if isinstance(item, dict))
    runs = response.get("runs")
    if isinstance(runs, list):
        candidates.extend(item for item in runs if isinstance(item, dict))
    for item in candidates:
        if str(item.get("task_key") or "") == task_key:
            return item
    raise RuntimeError(
        f"task snapshot was not readable for {task_key}; "
        f"candidate_count={len(candidates)}"
    )


def _select_image_model(
    client: RuntimeClient,
    *,
    requested_model_id: str,
) -> dict[str, Any]:
    config = _data(client.get("/api/v1/model-gateway/config"))
    direct_models = config.get("directModels") if isinstance(config, dict) else None
    image_models = (
        direct_models.get("image") if isinstance(direct_models, dict) else None
    )
    if not isinstance(image_models, list):
        raise RuntimeError("model gateway config has no image model list")
    usable = [
        item
        for item in image_models
        if isinstance(item, dict)
        and item.get("usable") is True
        and item.get("enabled") is not False
    ]
    if requested_model_id:
        for item in usable:
            if str(item.get("id") or "") == requested_model_id:
                return item
        raise RuntimeError(f"requested image model is not usable: {requested_model_id}")
    for item in usable:
        if item.get("isDefault") is True:
            return item
    if usable:
        return usable[0]
    raise RuntimeError("no usable direct image model is configured")


def _poll_image_result(
    client: RuntimeClient,
    project_id: str,
    job_id: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    path = _project_path(
        project_id,
        f"freezone/jobs/freezone_gen/{job_id}/result",
    )
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.client.get(path)
        payload = _json(response)
        if response.status_code != 200:
            raise RuntimeError(
                f"image result polling returned HTTP {response.status_code}: "
                f"{json.dumps(payload, ensure_ascii=False)[:1000]}"
            )
        latest = payload
        if payload.get("ok") is True and isinstance(payload.get("data"), dict):
            return payload["data"]
        status = str(payload.get("status") or "")
        if status == "failed":
            raise RuntimeError(
                "real image generation failed: "
                f"{json.dumps(payload, ensure_ascii=False)[:1600]}"
            )
        time.sleep(2.0)
    raise RuntimeError(
        f"timed out waiting for image job {job_id}: "
        f"{json.dumps(latest, ensure_ascii=False)[:1200]}"
    )


def _download_artifact(
    client: RuntimeClient,
    artifact_url: str,
) -> tuple[bytes, str, str]:
    absolute_url = urljoin(client.base_url + "/", artifact_url)
    response = client.client.get(absolute_url)
    if response.status_code != 200:
        raise RuntimeError(
            f"artifact download returned HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )
    content = response.content
    if len(content) < 1024:
        raise RuntimeError(f"artifact is unexpectedly small: {len(content)} bytes")
    content_type = (
        response.headers.get("content-type", "").split(";", 1)[0].strip()
        or mimetypes.guess_type(urlparse(absolute_url).path)[0]
        or "application/octet-stream"
    )
    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
            image_format = str(image.format or "").upper()
            width, height = image.size
    except (UnidentifiedImageError, OSError) as exc:
        raise RuntimeError("downloaded artifact is not a valid image") from exc
    dimensions = f"{width}x{height}"
    return content, content_type, f"{image_format}:{dimensions}"


def _artifact_suffix(image_format: str) -> str:
    normalized = image_format.split(":", 1)[0].upper()
    return {
        "JPEG": ".jpg",
        "PNG": ".png",
        "WEBP": ".webp",
    }.get(normalized, ".bin")


def _save_artifact(
    *,
    artifact_dir: Path,
    stamp: str,
    content: bytes,
    image_format: str,
) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / f"rq006-{stamp}{_artifact_suffix(image_format)}"
    path.write_bytes(content)
    return path


def _require_acceptance_receipt(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise RuntimeError(f"{label} task_acceptance_receipt is missing or empty")
    if value.get("schema") != TASK_ACCEPTANCE_RECEIPT_SCHEMA:
        raise RuntimeError(
            f"{label} task_acceptance_receipt has unexpected schema: {value}"
        )
    return value


def _write_latest_smoke(artifact_dir: Path, result: dict[str, Any]) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / LATEST_SMOKE_FILENAME
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def _cleanup_project(client: RuntimeClient, project_id: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        result["delete"] = client.post(
            _project_path(project_id, "delete"),
            expected=(200,),
        )
    except Exception as exc:  # noqa: BLE001 - cleanup evidence must not mask the smoke
        result["delete_error"] = str(exc)
    try:
        result["purge"] = client.post(
            _project_path(project_id, "purge"),
            expected=(200,),
        )
    except Exception as exc:  # noqa: BLE001 - cleanup evidence must not mask the smoke
        result["purge_error"] = str(exc)
    return result


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    if not args.allow_paid_generation:
        raise RuntimeError(
            "paid generation is disabled; pass --allow-paid-generation to submit "
            "exactly one low-cost image request"
        )

    stamp = _utc_stamp()
    nonce = uuid.uuid4().hex[:12]
    project_name = f"zz_rq006_smoke_{stamp.lower()}_{nonce}"
    canvas_id = f"rq006_resume_{nonce}"
    client = RuntimeClient(args.base_url, args.timeout_seconds)
    project_id = ""
    try:
        health = client.get("/healthz")
        version = client.get("/version.json")
        project = _data(
            client.post("/api/v1/projects", json={"name": project_name})
        )
        project_id = str(project.get("project_id") or project.get("id") or "")
        if not project_id:
            raise RuntimeError(f"project creation returned no id: {project}")

        saved_canvas = _data(
            client.put(
            _project_path(project_id, f"freezone/canvases/{canvas_id}"),
            json={
                "schema_version": 2,
                "canvas_id": canvas_id,
                "project_id": project_id,
                "canvas_scope": "default",
                "base_revision": None,
                "nodes": [],
                "edges": [],
                "viewport": {"x": 0, "y": 0, "zoom": 1},
                "metadata": {"smoke": "rq006"},
                "save_source": "manual_save",
                "allow_empty_overwrite": True,
            },
        )
        )
        execution_context = build_execution_context(
            canonical_intent="verify durable WorkflowRun pause and resume",
            project_id=project_id,
            canvas_id=canvas_id,
            observed_canvas_revision=saved_canvas.get("revision"),
            selected_handler="workflow.preflight",
            capability_id="workflow_runtime",
            side_effect_policy="write",
            idempotency_key=f"rq006-start-{nonce}",
            expected_postconditions=[
                {
                    "type": "workflow_step_completed",
                    "step_id": "canvas_structure",
                    "status": "completed",
                }
            ],
        )

        started = _data(
            client.post(
                _project_path(project_id, "workflow-runs"),
                json={
                    "workflow_id": "custom-canvas-workflow",
                    "canvas_id": canvas_id,
                    "run_mode": "draft",
                    "inputs": {
                        "request": "Verify durable pause and resume on an isolated canvas.",
                    },
                    "idempotency_key": f"rq006-start-{nonce}",
                    "goal": "Prove WorkflowRun recovery without paid media in the run.",
                    "success_criteria": [
                        "run can pause",
                        "run can resume from the same checkpoint",
                        "run and events remain readable after refresh",
                    ],
                    "source_turn_id": f"rq006-turn-{nonce}",
                    "execution_context": execution_context,
                },
            )
        )
        run_id = str(started.get("id") or "")
        if not run_id:
            raise RuntimeError(f"workflow start returned no run id: {started}")

        initial = _wait_for_run(
            client,
            project_id,
            run_id,
            statuses={"running", "paused", *TERMINAL_RUN_STATUSES},
            timeout_seconds=15.0,
        )
        if str(initial.get("status") or "") in TERMINAL_RUN_STATUSES:
            raise RuntimeError(
                f"workflow finished before pause/resume could be tested: {initial}"
            )
        if str(initial.get("status") or "") == "running":
            _command_run(
                client,
                project_id,
                run_id,
                command="pause",
                idempotency_key=f"rq006-pause-{nonce}",
            )
        paused = _wait_for_run(
            client,
            project_id,
            run_id,
            statuses={"paused"},
            timeout_seconds=30.0,
        )
        paused_snapshot = {
            "run_id": run_id,
            "status": paused.get("status"),
            "revision": paused.get("revision"),
            "runtime_phase": paused.get("runtime_phase"),
            "checkpoint": paused.get("checkpoint") or {},
            "step_states": paused.get("step_states") or {},
        }

        model = _select_image_model(
            client,
            requested_model_id=args.model_id,
        )
        registry_id = str(model.get("id") or "")
        upstream_model = str(model.get("modelId") or "")
        if not registry_id or not upstream_model:
            raise RuntimeError(f"selected image model has no stable id: {model}")
        catalog_model = f"direct/{registry_id}"
        accepted = _data(
            client.post(
                _project_path(project_id, "freezone/gen"),
                json={
                    "prompt": (
                        "A minimal blue circle centered on a plain white background, "
                        "clean test image, no text, no watermark."
                    ),
                    "aspect_ratio": "1:1",
                    "image_size": "1K",
                    "quality": "low",
                    "provider": "direct",
                    "model": catalog_model,
                    "model_id": catalog_model,
                    "gen_mode": "textToImage",
                    "canvas_id": canvas_id,
                    "node_id": f"rq006-image-{nonce}",
                },
            )
        )
        task_key = str(accepted.get("task_key") or "")
        job_id = str(accepted.get("job_id") or "")
        task_id = str(accepted.get("task_id") or "")
        task_backend = str(accepted.get("backend") or "")
        task_queue = str(accepted.get("queue") or "")
        if not task_key or not job_id or not task_id:
            raise RuntimeError(f"image submission returned no task identity: {accepted}")
        task_receipt = _require_acceptance_receipt(
            accepted.get("task_acceptance_receipt"),
            label="image submission",
        )
        if task_receipt.get("task_id") != task_id:
            raise RuntimeError(
                "immediate acceptance receipt task_id does not match response: "
                f"{task_receipt.get('task_id')!r} != {task_id!r}"
            )
        if task_receipt.get("task_key") != task_key:
            raise RuntimeError(
                "immediate acceptance receipt task_key does not match response: "
                f"{task_receipt.get('task_key')!r} != {task_key!r}"
            )
        image_result = _poll_image_result(
            client,
            project_id,
            job_id,
            timeout_seconds=args.generation_timeout_seconds,
        )
        task_snapshot = _task_snapshot(client, project_id, task_key)
        task_receipt_readback = _require_acceptance_receipt(
            task_snapshot.get("task_acceptance_receipt"),
            label="task readback",
        )
        if task_receipt_readback != task_receipt:
            raise RuntimeError(
                "task readback acceptance receipt differs from the immediate "
                "submission response"
            )
        if str(task_snapshot.get("status") or "") != "completed":
            raise RuntimeError(
                f"task readback is not completed: {task_snapshot.get('status')!r}"
            )
        cost_receipt = task_snapshot.get("production_cost_receipt") or {}
        if not isinstance(cost_receipt, dict):
            raise RuntimeError("production_cost_receipt is not an object")
        if cost_receipt.get("media_kind") != "image":
            raise RuntimeError(
                "production_cost_receipt media_kind is not image: "
                f"{cost_receipt.get('media_kind')!r}"
            )
        if cost_receipt.get("result_status") != "completed":
            raise RuntimeError(
                "production_cost_receipt result_status is not completed: "
                f"{cost_receipt.get('result_status')!r}"
            )
        artifact_url = str(image_result.get("url") or "")
        if not artifact_url:
            raise RuntimeError(f"image result has no artifact url: {image_result}")
        content, content_type, image_format = _download_artifact(
            client,
            artifact_url,
        )
        artifact_path = _save_artifact(
            artifact_dir=args.artifact_dir,
            stamp=stamp,
            content=content,
            image_format=image_format,
        )
        artifact_sha256 = hashlib.sha256(content).hexdigest()

        resumed = _command_run(
            client,
            project_id,
            run_id,
            command="resume",
            idempotency_key=f"rq006-resume-{nonce}",
            execution_context=execution_context,
        )
        resumed_snapshot = _wait_for_run(
            client,
            project_id,
            run_id,
            statuses={"running", *TERMINAL_RUN_STATUSES},
            timeout_seconds=20.0,
        )

        events = _workflow_events(client, project_id, run_id)
        event_types = [str(item.get("type") or "") for item in events]
        if "run_paused" not in event_types or "run_resumed" not in event_types:
            raise RuntimeError(
                "workflow event log does not contain both run_paused and "
                f"run_resumed: {event_types}"
            )

        if str(resumed_snapshot.get("status") or "") not in TERMINAL_RUN_STATUSES:
            _command_run(
                client,
                project_id,
                run_id,
                command="cancel",
                idempotency_key=f"rq006-cancel-{nonce}",
            )
        cancelled = _wait_for_run(
            client,
            project_id,
            run_id,
            statuses=TERMINAL_RUN_STATUSES,
            timeout_seconds=30.0,
        )
        readback = _get_run(client, project_id, run_id)
        readback_events = _workflow_events(client, project_id, run_id)

        result = {
            "schema": "rq006_runtime_resume_smoke.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "base_url": args.base_url,
            "health": health,
            "version": version,
            "project_id": project_id,
            "project_name": project_name,
            "canvas_id": canvas_id,
            "workflow_run": {
                "run_id": run_id,
                "initial_status": initial.get("status"),
                "paused": paused_snapshot,
                "resumed_status": resumed.get("status"),
                "resumed_snapshot": {
                    "status": resumed_snapshot.get("status"),
                    "revision": resumed_snapshot.get("revision"),
                    "runtime_phase": resumed_snapshot.get("runtime_phase"),
                },
                "cancelled_status": cancelled.get("status"),
                "readback_status": readback.get("status"),
                "event_types": event_types,
                "readback_event_count": len(readback_events),
                "readback_event_seq": readback.get("event_seq"),
            },
            "real_generation": {
                "model_registry_id": registry_id,
                "model_id": upstream_model,
                "catalog_model": catalog_model,
                "task_id": task_id,
                "task_key": task_key,
                "job_id": job_id,
                "backend": task_backend,
                "queue": task_queue,
                "task_acceptance_receipt": task_receipt,
                "task_status": task_snapshot.get("status"),
                "task_acceptance_receipt_readback": task_receipt_readback,
                "task_acceptance_receipt_matches_readback": True,
                "production_cost_receipt": cost_receipt,
                "artifact_url": artifact_url,
                "artifact_path": str(artifact_path.resolve()),
                "artifact_sha256": artifact_sha256,
                "artifact_bytes": len(content),
                "content_type": content_type,
                "image_format": image_format,
            },
        }
        result["cleanup"] = _cleanup_project(client, project_id)
        project_id = ""
        latest_path = args.artifact_dir / LATEST_SMOKE_FILENAME
        result["latest_snapshot_path"] = str(latest_path.resolve())
        _write_latest_smoke(args.artifact_dir, result)
        return result
    finally:
        if project_id:
            _cleanup_project(client, project_id)
        client.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8784")
    parser.add_argument("--model-id", default="")
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=DEFAULT_ARTIFACT_DIR,
    )
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--generation-timeout-seconds", type=float, default=300.0)
    parser.add_argument(
        "--allow-paid-generation",
        action="store_true",
        help="Explicitly allow exactly one low-cost real image generation.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = run_smoke(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
