"""Execute the T-113 one-shot sample against the running production canvas.

This is intentionally not a local-provider harness. It uses the real 8784
process, a real Chromium page, a temporary project under ``t113_l3_`` and the
configured upstream models. The only isolation is data isolation.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = ROOT / "workspace" / "artifacts" / "t113-single-shot-paid-l3"
PRODUCTION_ROOT = "http://127.0.0.1:8784"
PRODUCTION_API_BASE = f"{PRODUCTION_ROOT}/api/v1"
PRODUCTION_UI_BASE = PRODUCTION_ROOT
PLAYWRIGHT_NODE_MODULES = (
    ROOT / "workspace" / "ui-smoke-t091" / "browser-runner" / "node_modules"
)


def _chrome_path() -> Path:
    override = str(os.environ.get("T113_CHROME_PATH") or "").strip()
    candidates = (
        Path(override) if override else None,
        Path(os.environ.get("PROGRAMFILES", ""))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", ""))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
    )
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    discovered = (
        shutil.which("chrome")
        or shutil.which("chrome.exe")
        or shutil.which("google-chrome")
    )
    if discovered:
        return Path(discovered)
    return Path(override) if override else Path("chrome")


CHROME = _chrome_path()
PRODUCTION_SETTINGS_DB = ROOT / "项目资产" / "state" / "local" / "settings.db"
PROVIDER_BUDGET_PATH = ARTIFACT_DIR / "provider-budget.json"
PRODUCTION_CJS = Path(__file__).with_name("t113_production_8784.cjs")
RUNNER_SCHEMA = "t113_production_8784_runner.v1"
HOLLYWOOD_60_SCENARIO = "hollywood60"
HOLLYWOOD_60_SHOT_COUNT = 12
HOLLYWOOD_60_DURATION_SECONDS = 60


def _env_positive_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _production_scenario() -> str:
    return str(os.environ.get("T113_SCRIPT_SCENARIO") or "").strip().casefold()


def _expected_shot_count() -> int:
    default = (
        HOLLYWOOD_60_SHOT_COUNT
        if _production_scenario() == HOLLYWOOD_60_SCENARIO
        else 1
    )
    return _env_positive_int("T113_EXPECTED_SHOT_COUNT", default)


def _expected_duration_seconds() -> float:
    default = (
        float(HOLLYWOOD_60_DURATION_SECONDS)
        if _production_scenario() == HOLLYWOOD_60_SCENARIO
        else 0.0
    )
    raw = str(os.environ.get("T113_EXPECTED_DURATION_SECONDS") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _expected_request_prefix() -> str:
    return str(
        os.environ.get("T113_EXPECTED_REQUEST_PREFIX")
        or "从当前脚本节点继续到最终成片"
    ).strip()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _http_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError(f"{method} {url} failed: {type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{method} {url} did not return a JSON object")
    if payload.get("ok") is not True:
        raise RuntimeError(f"{method} {url} returned ok={payload.get('ok')!r}")
    return payload.get("data")


def _project_exists(project_id: str) -> bool:
    try:
        _http_json(
            "GET",
            f"{PRODUCTION_API_BASE}/projects/{project_id}",
            timeout=10.0,
        )
        return True
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            return False
        raise


def _create_project(prefix: str = "t113_l3_") -> str:
    name = f"{prefix}{_stamp().lower()}"
    data = _http_json(
        "POST",
        f"{PRODUCTION_API_BASE}/projects",
        body={"name": name},
    )
    if not isinstance(data, dict):
        raise RuntimeError("project creation returned no object")
    project_id = str(data.get("id") or data.get("project_id") or "").strip()
    if not project_id:
        raise RuntimeError(f"project creation returned no id: {data}")
    return project_id


def _build_script_node() -> dict[str, Any]:
    paid_runner = _load_module(
        "t113_paid_runner_for_production",
        Path(__file__).with_name("t113_paid_sample_runner.py"),
    )
    base_row = paid_runner.build_t113_script_row(
        paid_runner.build_t113_base_script_row()
    )
    if _production_scenario() == HOLLYWOOD_60_SCENARIO:
        fixture = _load_module(
            "hollywood_60s_fixture",
            Path(__file__).with_name("hollywood_60s_fixture.py"),
        )
        rows = fixture.build_hollywood_60_rows(base_row)
        title = "最后一张底片 · 60 秒短片"
    else:
        rows = [base_row]
        title = "T-113 单镜真实 L3 样片"
    return {
        "id": "script-a",
        "type": "scriptNode",
        "position": {"x": 120.0, "y": 360.0},
        "data": {
            "displayName": "脚本生成器",
            "prompt": "",
            "model": "",
            "scriptResult": {
                "title": title,
                "rows": rows,
            },
            "isGenerating": False,
            "generationStartedAt": None,
            "label": "脚本",
        },
    }


def _seed_canvas(project_id: str, canvas_id: str) -> dict[str, Any]:
    payload = {
        "schema_version": 2,
        "canvas_id": canvas_id,
        "project_id": project_id,
        "canvas_scope": "default",
        "save_source": "manual_save",
        "base_revision": None,
        "nodes": [_build_script_node()],
        "edges": [],
        "viewport": {"x": 0.0, "y": 0.0, "zoom": 1.0},
    }
    _http_json(
        "PUT",
        f"{PRODUCTION_API_BASE}/projects/{project_id}/freezone/canvases/{canvas_id}",
        body=payload,
    )
    canvas = _http_json(
        "GET",
        f"{PRODUCTION_API_BASE}/projects/{project_id}/freezone/canvases/{canvas_id}",
    )
    if not isinstance(canvas, dict):
        raise RuntimeError("seeded canvas readback returned no object")
    nodes = canvas.get("nodes")
    node = next(
        (
            item
            for item in nodes or []
            if isinstance(item, dict) and item.get("id") == "script-a"
        ),
        None,
    )
    report = (node or {}).get("data", {}).get("scriptContractReport")
    if not isinstance(report, dict) or int(report.get("blocking_count") or 0) != 0:
        raise RuntimeError(f"seeded canvas is not a valid reusable script contract: {report}")
    return canvas


def _cleanup_project(project_id: str, canvas_id: str) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    for name, method, url in (
        (
            "delete_canvas",
            "DELETE",
            f"{PRODUCTION_API_BASE}/projects/{project_id}/freezone/canvases/{canvas_id}",
        ),
        (
            "delete_project",
            "POST",
            f"{PRODUCTION_API_BASE}/projects/{project_id}/delete",
        ),
        (
            "purge_project",
            "POST",
            f"{PRODUCTION_API_BASE}/projects/{project_id}/purge",
        ),
    ):
        try:
            _http_json(method, url)
            steps.append({"name": name, "ok": True, "error": ""})
        except RuntimeError as exc:
            steps.append(
                {
                    "name": name,
                    "ok": False,
                    "error": str(exc)[:500],
                }
            )
    remaining = _project_exists(project_id)
    return {
        "project_id": project_id,
        "canvas_id": canvas_id,
        "steps": steps,
        "remaining": remaining,
        "ok": not remaining,
    }


def _run_production_browser(
    project_id: str,
    canvas_id: str,
    script_node_id: str,
    artifact_dir: Path,
) -> tuple[int, dict[str, Any], str]:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node is not available")
    evidence_path = artifact_dir / "production-browser-evidence.json"
    screenshot_path = artifact_dir / "production-browser-complete.png"
    env = os.environ.copy()
    env.update(
        {
            "NODE_PATH": str(PLAYWRIGHT_NODE_MODULES),
            "T113_PROJECT_ID": project_id,
            "T113_CANVAS_ID": canvas_id,
            "T113_SCRIPT_NODE_ID": script_node_id,
            "T113_UI_BASE": PRODUCTION_UI_BASE,
            "T113_API_BASE": PRODUCTION_API_BASE,
            "T113_CHROME_PATH": str(CHROME),
            "T113_EVIDENCE_PATH": str(evidence_path),
            "T113_SCREENSHOT_PATH": str(screenshot_path),
            "T113_REQUEST_TEXT": str(
                os.environ.get("T113_REQUEST_TEXT") or ""
            ).strip(),
            "T113_COMPLETED_WAIT_MS": str(
                os.environ.get(
                    "T113_COMPLETED_WAIT_MS",
                    "5400000" if _production_scenario() == HOLLYWOOD_60_SCENARIO else "900000",
                )
            ),
        }
    )
    completed = subprocess.run(
        [node, str(PRODUCTION_CJS)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if evidence_path.is_file():
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    else:
        evidence = {
            "schema": "t113_production_browser_evidence.v1",
            "failure": {
                "type": "NodeRunnerError",
                "message": output[-4000:] or "browser runner produced no evidence",
            },
        }
    return completed.returncode, evidence, output[-12000:]


def _task_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("items", "tasks", "records"):
            items = value.get(key)
            if isinstance(items, list):
                return [dict(item) for item in items if isinstance(item, dict)]
    return []


def _artifact_counts(run: dict[str, Any]) -> dict[str, int]:
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    storyboard = artifacts.get("storyboard_images")
    storyboard = storyboard if isinstance(storyboard, dict) else {}
    shot_videos = artifacts.get("shot_videos")
    shot_videos = shot_videos if isinstance(shot_videos, dict) else {}
    return {
        "imageJobs": len(
            [item for item in storyboard.get("jobs") or [] if isinstance(item, dict)]
        ),
        "videoJobs": len(
            [item for item in shot_videos.get("jobs") or [] if isinstance(item, dict)]
        ),
    }


def _record_provider_starts(run: dict[str, Any]) -> dict[str, Any]:
    counts = _artifact_counts(run)
    path = PROVIDER_BUDGET_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    counters = payload.get("counters") if isinstance(payload.get("counters"), dict) else {}
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    run_id = str(run.get("id") or "")
    existing_run_events = {
        str(item.get("runId") or "")
        for item in events
        if isinstance(item, dict)
    }
    if not run_id or run_id in existing_run_events:
        return {
            "updated": False,
            "reason": "already_recorded" if run_id else "missing_run_id",
            "counters": counters,
        }
    mappings = (
        ("imageTaskStarts", "imageJobs", "freezone_gen", "image"),
        ("videoTaskStarts", "videoJobs", "freezone_video_gen", "video"),
    )
    added: list[dict[str, Any]] = []
    for counter_key, count_key, task_type, media_kind in mappings:
        count = int(counts.get(count_key) or 0)
        if count <= 0:
            continue
        current = int(counters.get(counter_key) or 0)
        next_value = current + count
        counters[counter_key] = next_value
        event = {
            "at": datetime.now(timezone.utc).isoformat(),
            "kind": counter_key,
            "runId": run_id,
            "count": count,
            "mediaKind": media_kind,
            "taskType": task_type,
            "source": "production-8784-post-run-audit",
            "current": current,
            "next": next_value,
            "limit": 500 if counter_key == "videoTaskStarts" else None,
            "ok": True,
            "reason": "",
        }
        events.append(event)
        added.append(event)
    if not added:
        return {
            "updated": False,
            "reason": "no_provider_task_starts_observed",
            "counters": counters,
        }
    payload.update(
        {
            "schema": "t113_provider_budget.v1",
            "counters": counters,
            "events": events[-200:],
            "providerCallsStarted": True,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return {
        "updated": True,
        "added": added,
        "counters": counters,
    }


def _archive_final_film(run: dict[str, Any], artifact_dir: Path) -> dict[str, Any]:
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    final_film = artifacts.get("final_film")
    final_film = final_film if isinstance(final_film, dict) else {}
    final_artifact = final_film.get("final_compose_artifact")
    final_artifact = final_artifact if isinstance(final_artifact, dict) else {}
    source = Path(str(final_artifact.get("path") or ""))
    if not source.is_file():
        raise RuntimeError(f"final artifact is missing: {source}")
    if not source.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError(f"final artifact is outside the workspace: {source}")
    run_id = str(run.get("id") or "unknown")
    destination = artifact_dir / f"production-{run_id}.mp4"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    observed_sha = _sha256(destination)
    declared_sha = str(final_artifact.get("sha256") or "")
    return {
        "source_path": str(source),
        "archived_path": str(destination),
        "sha256": observed_sha,
        "declared_sha256": declared_sha,
        "sha256_matches": bool(declared_sha) and observed_sha == declared_sha,
        "size_bytes": destination.stat().st_size,
        "width": int(final_artifact.get("width") or 0),
        "height": int(final_artifact.get("height") or 0),
        "duration_seconds": float(final_artifact.get("duration_seconds") or 0),
    }


def _validate_production_execution(
    *,
    browser_evidence: dict[str, Any],
    run: dict[str, Any],
    final_film: dict[str, Any],
    cleanup: dict[str, Any],
) -> dict[str, Any]:
    violations: list[str] = []
    requests = browser_evidence.get("browser_requests")
    requests = requests if isinstance(requests, list) else []
    expected_shot_count = _expected_shot_count()
    if len(requests) != 1:
        violations.append("browser_structured_turns")
    elif not str(requests[0].get("request") or "").startswith(
        _expected_request_prefix()
    ):
        violations.append("browser_request_text")
    if browser_evidence.get("page_errors"):
        violations.append("browser_page_errors")
    canvas_writes = browser_evidence.get("canvas_writes")
    canvas_writes = canvas_writes if isinstance(canvas_writes, list) else []
    baseline = browser_evidence.get("canvas_write_baseline")
    if not isinstance(baseline, int) or len(canvas_writes) != baseline:
        violations.append("workflow_canvas_writes")
    if run.get("status") != "completed":
        violations.append("run_not_completed")
    if run.get("workflow_id") != "freezone-final-film":
        violations.append("wrong_workflow")
    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    target_node_ids = inputs.get("target_node_ids")
    target_node_ids = (
        target_node_ids if isinstance(target_node_ids, list) else []
    )
    if (
        inputs.get("target_strategy") not in {"reuse_existing", "create_missing"}
        or "script-a" not in target_node_ids
    ):
        violations.append("script_node_binding")
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    script = artifacts.get("script_contract")
    script = script if isinstance(script, dict) else {}
    if (
        script.get("status") != "completed"
        or script.get("source") != "canvas_script_node"
        or script.get("script_node_id") != "script-a"
    ):
        violations.append("script_contract")
    storyboard = artifacts.get("storyboard_images")
    storyboard = storyboard if isinstance(storyboard, dict) else {}
    if (
        storyboard.get("status") != "completed"
        or int(storyboard.get("shot_count") or 0) != expected_shot_count
        or int(storyboard.get("completed_count") or 0) != expected_shot_count
    ):
        violations.append("storyboard_images")
    shot_videos = artifacts.get("shot_videos")
    shot_videos = shot_videos if isinstance(shot_videos, dict) else {}
    if (
        shot_videos.get("status") != "completed"
        or int(shot_videos.get("shot_count") or 0) != expected_shot_count
        or int(shot_videos.get("completed_count") or 0) != expected_shot_count
    ):
        violations.append("shot_videos")
    final_state = artifacts.get("final_film")
    final_state = final_state if isinstance(final_state, dict) else {}
    if final_state.get("status") != "completed":
        violations.append("final_film")
    if final_film.get("sha256_matches") is not True:
        violations.append("final_film_sha256")
    if int(final_film.get("width") or 0) <= 0 or int(final_film.get("height") or 0) <= 0:
        violations.append("final_film_dimensions")
    if float(final_film.get("duration_seconds") or 0) <= 0:
        violations.append("final_film_duration")
    expected_duration = _expected_duration_seconds()
    if (
        expected_duration > 0
        and float(final_film.get("duration_seconds") or 0) + 1.0 < expected_duration
    ):
        violations.append("final_film_duration_target")
    counts = _artifact_counts(run)
    if counts["imageJobs"] != expected_shot_count:
        violations.append("image_task_starts")
    if counts["videoJobs"] != expected_shot_count:
        violations.append("video_task_starts")
    if cleanup.get("ok") is not True:
        violations.append("temporary_project_cleanup")
    return {
        "ok": not violations,
        "violations": violations,
        "artifactCounts": counts,
        "structuredTurns": len(requests),
        "finalArtifactSha256": str(final_film.get("sha256") or ""),
    }


def build_preflight() -> dict[str, Any]:
    contract = _load_module(
        "t113_contract_for_production",
        Path(__file__).with_name("t113_single_shot_paid_l3.py"),
    )
    paid_runner = _load_module(
        "t113_paid_runner_for_production_preflight",
        Path(__file__).with_name("t113_paid_sample_runner.py"),
    )
    contract_preflight = contract.build_preflight(
        api_base=PRODUCTION_ROOT,
        timeout=10.0,
    )
    media_relay = paid_runner.production_media_relay_status(PRODUCTION_SETTINGS_DB)
    try:
        budget = json.loads(PROVIDER_BUDGET_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        budget = {}
    counters = budget.get("counters") if isinstance(budget, dict) else {}
    counters = counters if isinstance(counters, dict) else {}
    video_starts = int(counters.get("videoTaskStarts") or 0)
    checks = [
        {
            "name": "real_8784_contract",
            "ok": contract_preflight.get("environmentReady") is True,
            "detail": contract_preflight,
        },
        {
            "name": "playwright_node_modules",
            "ok": PLAYWRIGHT_NODE_MODULES.is_dir(),
            "detail": str(PLAYWRIGHT_NODE_MODULES),
        },
        {
            "name": "chrome",
            "ok": CHROME.is_file(),
            "detail": str(CHROME),
        },
        {
            "name": "media_relay",
            "ok": media_relay.get("configured") is True,
            "detail": media_relay,
        },
        {
            "name": "video_budget",
            "ok": video_starts < 500,
            "detail": {"used": video_starts, "limit": 500},
        },
    ]
    failed = [str(item["name"]) for item in checks if item.get("ok") is not True]
    return {
        "schema": RUNNER_SCHEMA,
        "ok": not failed,
        "productionMode": True,
        "serverIsolation": False,
        "dataIsolationPrefix": "t113_l3_",
        "checks": checks,
        "failedChecks": failed,
        "blockingReasons": [f"production_preflight_failed:{name}" for name in failed],
        "budget": {"videoTaskStarts": video_starts, "videoTaskStartLimit": 500},
    }


def run_production_sample(
    *,
    authorization: str,
    artifact_dir: Path = ARTIFACT_DIR,
) -> dict[str, Any]:
    contract = _load_module(
        "t113_contract_for_production_run",
        Path(__file__).with_name("t113_single_shot_paid_l3.py"),
    )
    if str(authorization or "").strip() != contract.AUTHORIZATION_PHRASE:
        return {
            "schema": RUNNER_SCHEMA,
            "ok": False,
            "productionMode": True,
            "providerCallsStarted": False,
            "blockingReasons": ["t113_authorization_phrase_mismatch"],
        }
    text_runtime_probe = contract.probe_text_model_runtime(
        api_base=PRODUCTION_ROOT,
        timeout=180.0,
    )
    if text_runtime_probe.get("ok") is not True:
        return {
            "schema": RUNNER_SCHEMA,
            "ok": False,
            "productionMode": True,
            "providerCallsStarted": bool(text_runtime_probe.get("attempted")),
            "textRuntimeProbe": text_runtime_probe,
            "blockingReasons": ["t113_text_runtime_probe_failed"],
        }
    preflight = build_preflight()
    if preflight.get("ok") is not True:
        return {
            "schema": RUNNER_SCHEMA,
            "ok": False,
            "productionMode": True,
            "providerCallsStarted": bool(text_runtime_probe.get("attempted")),
            "textRuntimeProbe": text_runtime_probe,
            "preflight": preflight,
            "blockingReasons": list(preflight.get("blockingReasons") or []),
        }

    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    project_id = ""
    canvas_id = "t113_l3_canvas"
    run: dict[str, Any] = {}
    browser_evidence: dict[str, Any] = {}
    final_film: dict[str, Any] = {}
    cleanup: dict[str, Any] = {}
    browser_exit_code: int | None = None
    browser_output = ""
    failure: dict[str, Any] | None = None
    validation: dict[str, Any]
    try:
        try:
            project_id = _create_project()
            _seed_canvas(project_id, canvas_id)
            browser_exit_code, browser_evidence, browser_output = (
                _run_production_browser(
                    project_id,
                    canvas_id,
                    "script-a",
                    artifact_dir,
                )
            )
            candidate_run = browser_evidence.get("run")
            run = candidate_run if isinstance(candidate_run, dict) else {}
            if not run:
                raise RuntimeError(
                    "production browser evidence did not contain a WorkflowRun"
                )
            final_film = _archive_final_film(run, artifact_dir)
        except Exception as exc:
            failure = {
                "schema": "t113_production_execution_failure.v1",
                "type": type(exc).__name__,
                "message": str(exc)[:2000],
            }
            candidate_run = browser_evidence.get("run")
            if not run and isinstance(candidate_run, dict):
                run = candidate_run
            if run and not final_film:
                try:
                    final_film = _archive_final_film(run, artifact_dir)
                except Exception as artifact_exc:
                    failure["finalFilmReadError"] = (
                        f"{type(artifact_exc).__name__}: {str(artifact_exc)[:500]}"
                    )
        cleanup = (
            _cleanup_project(project_id, canvas_id)
            if project_id
            else {"ok": False, "remaining": False, "error": "project_not_created"}
        )
        if not run:
            validation = {
                "ok": False,
                "violations": ["production_run_missing"],
                "artifactCounts": {},
            }
        else:
            validation = _validate_production_execution(
                browser_evidence=browser_evidence,
                run=run,
                final_film=final_film,
                cleanup=cleanup,
            )
        if browser_exit_code not in (None, 0) and "browser_runner_failed" not in validation["violations"]:
            validation["violations"].append("browser_runner_failed")
            validation["ok"] = False
    finally:
        if project_id and not cleanup:
            cleanup = _cleanup_project(project_id, canvas_id)

    budget_update = _record_provider_starts(run) if run else {
        "updated": False,
        "reason": "no_run",
    }
    return {
        "schema": RUNNER_SCHEMA,
        "ok": validation.get("ok") is True,
        "productionMode": True,
        "serverIsolation": False,
        "dataIsolation": {
            "prefix": "t113_l3_",
            "projectId": project_id,
            "canvasId": canvas_id,
            "cleanup": cleanup,
        },
        "textRuntimeProbe": text_runtime_probe,
        "preflight": preflight,
        "browser": {
            "exitCode": browser_exit_code,
            "evidencePath": str(artifact_dir / "production-browser-evidence.json"),
            "screenshotPath": str(artifact_dir / "production-browser-complete.png"),
            "outputTail": browser_output[-4000:],
        },
        "providerCallsStarted": bool(
            text_runtime_probe.get("attempted")
            or (
                _artifact_counts(run).get("imageJobs")
                or _artifact_counts(run).get("videoJobs")
                if run
                else False
            )
        ),
        "budgetUpdate": budget_update,
        "run": run,
        "browserEvidence": browser_evidence,
        "finalFilm": final_film,
        "validation": validation,
        "failure": failure,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorization", default="")
    parser.add_argument(
        "--output",
        type=Path,
        default=ARTIFACT_DIR / "production-execution.json",
    )
    parser.add_argument("--preflight", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = (
        build_preflight()
        if args.preflight
        else run_production_sample(
            authorization=str(args.authorization),
            artifact_dir=ARTIFACT_DIR,
        )
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
