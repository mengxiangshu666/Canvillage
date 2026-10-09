"""Measure two existing frontend builds against one isolated media-free canvas."""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse, urlunparse
import uuid

try:
    from scripts.architecture.measure_canvas_browser_interaction import _chrome_path, _gesture_frames
    from scripts.architecture.serve_spa import SpaHandler
except ModuleNotFoundError:
    from measure_canvas_browser_interaction import _chrome_path, _gesture_frames
    from serve_spa import SpaHandler


def build_versions(old: Path, new: Path) -> tuple[dict, dict]:
    versions = []
    for root in (old, new):
        if not (root / "index.html").is_file():
            raise ValueError(f"dist has no index.html: {root}")
        version = json.loads((root / "version.json").read_text(encoding="utf-8"))
        if not isinstance(version.get("buildId"), str) or not version["buildId"].strip():
            raise ValueError("dist requires nonempty buildId")
        versions.append(version)
    if versions[0]["buildId"] == versions[1]["buildId"]:
        raise ValueError("old/new builds must differ")
    return versions[0], versions[1]


def fixture_nodes() -> list[dict]:
    return [{
        "id": f"probe-{index}", "type": "scriptNode",
        "position": {"x": (index % 8) * 170, "y": (index // 8) * 150},
        "style": {"width": 360, "height": 260},
        "data": {"label": f"Probe {index}", "scriptResult": {"rows": [
            {"shot_no": "1", "duration": "5", "description": "Isolated performance fixture"},
        ]}},
    } for index in range(40)]


def positions(canvas: dict) -> dict:
    return {node["id"]: node["position"] for node in canvas["nodes"]}


def dist_provenance(root: Path) -> dict:
    return {
        "directory": str(root.resolve()),
        "entry_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ("index.html", "version.json")},
    }


def cli(method: str, path: str, body=None):
    command = [sys.executable, "-m", "novelvideo.canvas_cli", "--json", "api", method, path]
    if body is not None:
        command += ["--body", json.dumps(body)]
    if method == "delete":
        command += ["--yes"]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=60)
    if completed.returncode:
        raise RuntimeError(f"isolated CLI {method} failed: {completed.returncode}")
    payload = json.loads(completed.stdout)
    if not payload.get("ok"):
        raise RuntimeError(f"isolated CLI {method} returned failure")
    return payload["data"]


class QuietSpaHandler(SpaHandler):
    def log_message(self, *_args):
        pass


def measure(old: Path, new: Path, output: Path, rounds: int = 3) -> dict:
    if not 3 <= rounds <= 10:
        raise ValueError("rounds must be 3..10")
    old_version, new_version = build_versions(old, new)
    from playwright.sync_api import sync_playwright

    result = {"schema": "canvas_build_pair_gesture.v1", "samples": [], "media_submissions": 0}
    result["dist_provenance"] = {"old": dist_provenance(old), "new": dist_provenance(new)}
    servers = []
    threads = []
    project_id = ""
    canvas_id = f"perf_{uuid.uuid4().hex[:12]}"
    baseline = {"nodes": fixture_nodes(), "edges": [], "viewport": {"x": 10, "y": 10, "zoom": 1}}
    try:
        for root in (old, new):
            server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietSpaHandler, directory=str(root.resolve())))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            servers.append(server)
            threads.append(thread)
        project = cli("post", "/projects", {"name": f"T255_{canvas_id}"})
        project_id = project["id"]
        path = f"/projects/{project_id}/freezone/canvases/{canvas_id}"
        cli("put", path, baseline)
        original_positions = positions(cli("get", path))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=_chrome_path(), headless=True)
            try:
                context = browser.new_context(viewport={"width": 1600, "height": 1000})
                backend = context.request.get("http://127.0.0.1:8784/version.json").json()
                if not backend.get("buildId"):
                    raise RuntimeError("backend build identity unavailable")
                result["metadata"] = {
                    "old_frontend_version": old_version, "new_frontend_version": new_version,
                    "backend_version": backend, "backend_build_id": backend["buildId"],
                    "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    "gesture_script_sha256": hashlib.sha256(Path(sys.modules[_gesture_frames.__module__].__file__).read_bytes()).hexdigest(),
                    "browser_version": browser.version, "viewport": {"width": 1600, "height": 1000},
                    "scenario_id": "40-overlapping-script-nodes-no-media", "canvas_nodes": 40, "canvas_edges": 0,
                    "project_id": project_id, "canvas_id": canvas_id,
                }
                for round_index in range(rounds):
                    for build_index in ((0, 1) if round_index % 2 == 0 else (1, 0)):
                        current = cli("get", path)
                        cli("put", path, {**baseline, "base_revision": current["revision"]})
                        page = context.new_page()
                        base = f"http://127.0.0.1:{servers[build_index].server_port}"

                        def route_request(route):
                            parsed = urlparse(route.request.url)
                            if parsed.path == "/api/v1/projects":
                                route.fulfill(json=[project])
                            elif parsed.path.startswith(("/api/", "/static/")):
                                if route.request.method not in ("GET", "HEAD", "OPTIONS"):
                                    route.abort()
                                    result["browser_write_requests_blocked"] = int(result.get("browser_write_requests_blocked", 0)) + 1
                                else:
                                    route.continue_(url=urlunparse(parsed._replace(netloc="127.0.0.1:8784", scheme="http")))
                            else:
                                route.continue_()

                        page.route("**/*", route_request)
                        try:
                            page.goto(f"{base}/projects/{project_id}/freezone?canvas={canvas_id}")
                            page.wait_for_function("document.querySelectorAll('.react-flow__node').length > 0")
                            page.wait_for_timeout(3000)
                            pane = page.locator(".react-flow__pane").first.bounding_box()
                            if not pane:
                                raise RuntimeError("canvas pane missing")
                            sample = {"round": round_index, "build": "old" if build_index == 0 else "new",
                                      "frontend_build_id": (old_version if build_index == 0 else new_version)["buildId"],
                                      "rendered_nodes": page.locator(".react-flow__node").count(), "gestures": {}}
                            for gesture in ("pan", "zoom"):
                                sample["gestures"][gesture] = _gesture_frames(page, gesture, pane["x"] + pane["width"] * .7, pane["y"] + pane["height"] * .8)
                                sample["gestures"][gesture]["frames_ms"] = page.evaluate("window.__canvasGesture.frames")
                            result["samples"].append(sample)
                        finally:
                            page.close()
                        if positions(cli("get", path)) != original_positions:
                            raise RuntimeError("gesture changed fixture node positions")
                if context.request.get("http://127.0.0.1:8784/version.json").json() != backend:
                    raise RuntimeError("backend changed during build comparison")
                result["node_positions_unchanged"] = True
                result["summary"] = {gesture: {build: statistics.median([
                    sample["gestures"][gesture]["rAF_interval_p95_ms"] for sample in result["samples"] if sample["build"] == build
                ]) for build in ("old", "new")} for gesture in ("pan", "zoom")}
                result["limitations"] = [
                    "Headless automation and shared browser cache influence intervals; descriptive A/B, no general acceleration claim.",
                    "Build IDs and entry hashes identify supplied dist artifacts, not all source commits or individual causal changes.",
                    "An old build marked dirty is not reproducible historical source; results apply only to these supplied artifacts.",
                ]
            finally:
                browser.close()
    finally:
        try:
            if project_id:
                cli("delete", f"/projects/{project_id}/freezone/canvases/{canvas_id}")
                cli("post", f"/projects/{project_id}/delete", {})
                result["cleanup"] = cli("post", f"/projects/{project_id}/purge", {})
        finally:
            for server, thread in zip(servers, threads):
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-dist", type=Path, required=True)
    parser.add_argument("--new-dist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    result = measure(args.old_dist, args.new_dist, args.output, args.rounds)
    print(json.dumps({"summary": result["summary"], "cleanup": result.get("cleanup")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
