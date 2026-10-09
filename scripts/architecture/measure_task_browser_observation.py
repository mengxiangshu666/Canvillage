"""Measure isolated product task commits through a built SPA to visible task rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
import httpx
import uvicorn

from novelvideo.api.routes import tasks as task_routes
from novelvideo.project_context import ProjectContext
from novelvideo.task_state import TaskStateManager


def statistics(samples: list[float]) -> dict:
    if not samples or any(not math.isfinite(value) or value < 0 for value in samples):
        raise ValueError("observations must be finite nonnegative milliseconds")
    ordered = sorted(samples)
    return {"count": len(samples), "p50_ms": ordered[math.ceil(len(samples) * .5) - 1],
            "p95_ms": ordered[math.ceil(len(samples) * .95) - 1], "max_ms": ordered[-1]}


def fixture_context(root: Path) -> ProjectContext:
    return ProjectContext(
        project_id="isolated", project_name="isolated", owner_type="user", owner_id="fixture",
        owner_username="fixture", requester_user_id="fixture", requester_username="fixture",
        requester_principals=(("user", "fixture"),), effective_role="owner", home_node_id="fixture",
        is_home_node=True, output_dir=root / "output", state_dir=root / "state",
        runtime_dir=root / "runtime",
    )


def historical_backend(ref: str):
    root = Path(__file__).resolve().parents[2]
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        cwd=root, text=True,
    ).strip()
    modules, identities = {}, {}
    for name, path in (
        ("novelvideo._observation_task_state", "src/novelvideo/task_state.py"),
        ("novelvideo.api.routes._observation_tasks", "src/novelvideo/api/routes/tasks.py"),
    ):
        source = subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=root)
        module = types.ModuleType(name)
        module.__package__ = name.rpartition(".")[0]
        sys.modules[name] = module
        try:
            exec(compile(source, f"git:{commit}:{path}", "exec"), module.__dict__)
        except BaseException:
            sys.modules.pop(name, None)
            raise
        modules[path] = module
        identities[path] = hashlib.sha256(source).hexdigest()
    return (
        modules["src/novelvideo/task_state.py"].TaskStateManager,
        modules["src/novelvideo/api/routes/tasks.py"],
        {"commit": commit, "source_sha256": identities},
    )


def measure(dist: Path, iterations: int = 60, backend_ref: str | None = None) -> dict:
    if not 50 <= iterations <= 90:
        raise ValueError("iterations must be 50..90 (each update has a unique visible percentage)")
    dist = dist.resolve()
    version = json.loads((dist / "version.json").read_text(encoding="utf-8"))
    if not (dist / "index.html").is_file() or not version.get("buildId"):
        raise ValueError("a real product build with version identity is required")
    from playwright.sync_api import sync_playwright
    try:
        from scripts.architecture.measure_canvas_browser_interaction import _chrome_path
    except ModuleNotFoundError:
        from measure_canvas_browser_interaction import _chrome_path

    manager_type, routes, historical = TaskStateManager, task_routes, None
    if backend_ref:
        manager_type, routes, historical = historical_backend(backend_ref)
    with tempfile.TemporaryDirectory(prefix="t255-task-browser-") as directory:
        ctx = fixture_context(Path(directory))
        manager = manager_type()
        task = manager.create_task_for_project(ctx, "ingest_fast", 0, scope="browser-probe")
        manager.update_progress_for_project(ctx, "ingest_fast", 0, scope="browser-probe",
                                            progress=0, expected_task_id=task.task_id)
        app = FastAPI()
        commits, observed = {}, {}
        counters = {"task_list_requests": 0, "stream_requests": 0, "progress_writes": 0}

        async def resolve(**_kwargs):
            return ctx

        async def valid(_request, last_check):
            return True, last_check

        @app.get("/api/v1/projects/isolated/tasks")
        async def task_list():
            counters["task_list_requests"] += 1
            return await routes.list_project_tasks("isolated", False, 200, {"username": "fixture"})

        @app.get("/api/v1/projects/isolated/tasks/stream")
        async def stream(request: Request, snapshot: bool = True):
            counters["stream_requests"] += 1
            return await routes.stream_project_tasks(
                "isolated", request, 2.0, 15.0, snapshot, {"username": "fixture"},
            )

        @app.post("/__measure/write/{sequence}")
        async def write(sequence: int):
            if not 1 <= sequence <= iterations or sequence in commits:
                return JSONResponse({"error": "invalid or repeated sequence"}, status_code=409)
            manager.update_progress_for_project(
                ctx, "ingest_fast", 0, scope="browser-probe", progress=sequence / 100,
                expected_task_id=task.task_id,
            )
            commits[sequence] = time.perf_counter()
            counters["progress_writes"] += 1
            return {"sequence": sequence}

        @app.post("/__measure/observed/{sequence}")
        async def observe(sequence: int):
            received = time.perf_counter()
            if sequence in commits and sequence not in observed:
                observed[sequence] = (received - commits[sequence]) * 1000
            return {"accepted": sequence in observed}

        @app.get("/__measure/results")
        async def results():
            return {"observed": sorted(observed)}

        @app.api_route("/api/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
        async def fixture(path: str):
            if path == "config":
                data = {"edition": "ce", "auth_required": False}
            elif path == "auth/me":
                data = {"username": "fixture", "role": "admin", "credit_balance": 0}
            elif path == "auth/avatar":
                data = {"avatar_url": None}
            elif path == "projects/summaries":
                data = [{"id": "isolated", "name": "isolated", "status": "active", "effectiveRole": "owner"}]
            elif path == "projects":
                data = ["isolated"]
            elif "freezone" in path and "canvases" in path:
                data = [] if path.endswith("canvases") else {"nodes": [], "edges": [], "revision": 0}
            elif "beat-context" in path:
                data = {"episodes": [], "characters": [], "scenes": [], "props": []}
            else:
                return JSONResponse({"ok": False, "error": "unprovided fixture"}, status_code=404)
            return {"ok": True, "data": data}

        @app.get("/{path:path}")
        async def static(path: str):
            target = (dist / path).resolve()
            if not target.is_relative_to(dist):
                return JSONResponse({"error": "invalid path"}, status_code=400)
            return FileResponse(target if target.is_file() else dist / "index.html")

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        base = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="off", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
        with patch.object(routes, "resolve_project_context", resolve), \
                patch.object(routes, "get_task_manager", lambda: manager), \
                patch.object(routes, "_sse_token_still_valid", valid):
            thread.start()
            try:
                deadline = time.monotonic() + 20
                while not server.started:
                    if not thread.is_alive() or time.monotonic() > deadline:
                        raise RuntimeError("isolated server did not start")
                    time.sleep(.01)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=_chrome_path(), headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 1440, "height": 1000})
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(base + "/projects/isolated/freezone", wait_until="domcontentloaded")
                        page.wait_for_function("() => !!document.querySelector('[role=region]')", timeout=20000)
                        page.keyboard.press("Control+j")
                        page.locator('[role="region"][aria-hidden="false"]').wait_for(timeout=20000)
                        page.wait_for_function("""() => [...document.querySelectorAll('[role=region] span')]
                          .some(e => e.textContent.trim() === '0%')""", timeout=20000)
                        page.evaluate("""() => {
                          window.__taskObservations = [];
                          const seen = new Set();
                          new MutationObserver(() => {
                            const panel = document.querySelector('[role=region][aria-hidden=false]');
                            if (!panel || !panel.getBoundingClientRect().height) return;
                            for (const e of panel.querySelectorAll('span')) {
                              const match = e.textContent.trim().match(/^(\\d+)%$/);
                              if (!match || e.getBoundingClientRect().width === 0) continue;
                              const seq = Number(match[1]);
                              if (!seq || seen.has(seq)) continue;
                              seen.add(seq);
                              fetch('/__measure/observed/' + seq, {method:'POST'})
                                .then(r => r.json()).then(r => {
                                  if (r.accepted) window.__taskObservations.push(seq);
                                });
                            }
                          }).observe(document.body, {subtree:true, childList:true, characterData:true});
                        }""")
                        with httpx.Client(trust_env=False, timeout=15) as client:
                            for sequence in range(1, iterations + 1):
                                client.post(base + f"/__measure/write/{sequence}").raise_for_status()
                                page.wait_for_function("seq => window.__taskObservations.includes(seq)",
                                                       arg=sequence, timeout=12000)
                        browser_version = browser.version
                    except Exception as exc:
                        raise RuntimeError(f"browser fixture failed: {page.url}; {errors}; "
                                           f"{page.locator('body').inner_text()[:1500]}; {counters}") from exc
                    finally:
                        browser.close()
            finally:
                server.should_exit = True
                thread.join(timeout=20)
                listener.close()
                if thread.is_alive():
                    raise RuntimeError("isolated server did not stop")
        if len(observed) != iterations or counters["stream_requests"] < 1:
            raise RuntimeError("did not observe every commit through a real product stream")
        result = {
            "schema": "task_browser_observation.v1", "frontend_version": version,
            "browser_version": browser_version, "iterations": iterations,
            "write_to_dom_ack": statistics(list(observed.values())),
            "samples_ms": [observed[index] for index in range(1, iterations + 1)],
            "counters": counters, "media_submissions": 0,
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "stream_source_sha256": hashlib.sha256(Path(task_routes.__file__).read_bytes()).hexdigest(),
            "task_manager_source_sha256": hashlib.sha256(Path(sys.modules[manager_type.__module__].__file__).read_bytes()).hexdigest()
            if not historical else historical["source_sha256"]["src/novelvideo/task_state.py"],
            "historical_backend": historical,
            "limitations": [
                "Task manager and SSE route are current unless historical_backend identifies git modules; imported dependencies are current.",
                "Actual provided SPA build, TaskCenterProvider, stream client and visible TaskRow DOM.",
                "Product default SSE interval is 2 seconds; sequential writes wait for each observed percentage.",
                "Commit return to visible DOM MutationObserver HTTP acknowledgement, including loopback acknowledgement overhead.",
                "DOM presence and visible geometry, not pixel presentation or screen paint timestamp.",
                "Authentication and project discovery are fixtures; no private product data or 8784 writes.",
                "Single task without media; not historical whole-product A/B or a claimed 30 percent improvement.",
            ],
        }
    result["temporary_database_removed"] = not Path(directory).exists()
    result["temporary_server_stopped"] = not thread.is_alive()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("frontend/dist"))
    parser.add_argument("--iterations", type=int, default=60)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend-ref", help="Load task manager and task routes from one Git commit")
    args = parser.parse_args()
    result = measure(args.dist, args.iterations, args.backend_ref)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"write_to_dom_ack": result["write_to_dom_ack"], "counters": result["counters"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
