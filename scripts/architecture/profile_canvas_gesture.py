"""Profile verified middle-button viewport gestures on an isolated CLI fixture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from playwright.sync_api import sync_playwright

from measure_canvas_browser_interaction import _chrome_path
from measure_workflow_sse_read_pair import summary


def cli(method, path, body=None):
    command = [sys.executable, "-m", "novelvideo.canvas_cli", "--json", "api", method, path]
    if body is not None:
        command += ["--body", json.dumps(body)]
    if method == "delete":
        command += ["--yes"]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(f"CLI {method} {path}: {completed.stdout} {completed.stderr}")
    result = json.loads(completed.stdout)
    if not result.get("ok"):
        raise RuntimeError(result)
    return result["data"]


def metrics(session):
    return {item["name"]: item["value"] for item in session.send("Performance.getMetrics")["metrics"]}


def profile(output, count, rounds, drop_style_selectors=False, selector_filter="[style"):
    project_id = ""
    canvas_id = f"perf_{uuid.uuid4().hex[:12]}"
    result = {"schema": "canvas_verified_gesture.v1", "samples": [], "media_submissions": 0}
    try:
        project = cli("post", "/projects", {"name": f"T255_{canvas_id}"})
        project_id = project["id"]
        path = f"/projects/{project_id}/freezone/canvases/{canvas_id}"
        nodes = [{
            "id": f"probe-{index}", "type": "scriptNode",
            "position": {"x": (index % 8) * 170, "y": (index // 8) * 150},
            "style": {"width": 360, "height": 260},
            "data": {"label": f"Probe {index}", "scriptResult": {
                "rows": [{"shot_no": "1", "duration": "5", "description": "Isolated performance fixture"}],
            }},
        } for index in range(count)]
        cli("put", path, {"nodes": nodes, "edges": [], "viewport": {"x": 10, "y": 10, "zoom": 1}})
        before = cli("get", path)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=_chrome_path(), headless=True)
            try:
                page = browser.new_page(viewport={"width": 1600, "height": 1000})
                # The global project chooser need not read the user's project list.
                page.route("**/api/v1/projects", lambda route: route.fulfill(json=[project]))
                page.goto(f"http://127.0.0.1:8784/projects/{project_id}/freezone?canvas={canvas_id}")
                page.wait_for_function("document.querySelectorAll('.react-flow__node').length > 0")
                page.wait_for_timeout(5000)
                if drop_style_selectors:
                    result["removed_rules"] = page.evaluate("""filter => {
                      const removed = [];
                      const visit = sheet => {
                        for (let i = sheet.cssRules.length - 1; i >= 0; i--) {
                          const rule = sheet.cssRules[i];
                          if (rule.selectorText?.includes(filter)) {
                            removed.push(rule.selectorText); sheet.deleteRule(i);
                          } else if (rule.cssRules) visit(rule);
                        }
                      }; for (const sheet of document.styleSheets) {
                        try { visit(sheet); } catch {}
                      } return removed;
                    }""", selector_filter)
                session = page.context.new_cdp_session(page)
                session.send("Performance.enable")
                result["rendered_nodes"] = page.locator(".react-flow__node").count()
                result["dom_elements"] = page.locator("*").count()
                result["browser_version"] = browser.version
                result["runtime_version"] = page.request.get("http://127.0.0.1:8784/version.json").json()
                node_sizes = page.locator('.react-flow__node').evaluate_all(
                    "nodes => nodes.map(node => ({id:node.dataset.id,w:node.offsetWidth,h:node.offsetHeight}))"
                )
                viewport = page.locator(".react-flow__viewport").first
                for index in range(rounds):
                    page.wait_for_timeout(1000)
                    start_style = viewport.get_attribute("style")
                    start_metrics = metrics(session)
                    page.evaluate("""() => {
                      window.__gestureFrames = []; window.__gestureLast = null;
                      window.__gestureStop = false;
                      window.__gestureMutations = {};
                      window.__gestureObserver = new MutationObserver(records => {
                        for (const r of records) {
                          const key = r.target.tagName + '.' + (r.target.className?.baseVal ?? r.target.className) + ':' + r.attributeName;
                          window.__gestureMutations[key] = (window.__gestureMutations[key] ?? 0) + 1;
                        }
                      }); window.__gestureObserver.observe(document.documentElement, {subtree:true,attributes:true});
                      const tick = now => {
                        if (window.__gestureStop) return;
                        if (window.__gestureLast !== null) window.__gestureFrames.push(now - window.__gestureLast);
                        window.__gestureLast = now; requestAnimationFrame(tick);
                      }; requestAnimationFrame(tick);
                    }""")
                    started = time.perf_counter()
                    page.mouse.move(1000, 850)
                    page.mouse.down(button="middle")
                    direction = 1 if index % 2 == 0 else -1
                    for step in range(1, 61):
                        page.mouse.move(1000 + direction * step * 2, 850)
                    moved_style = viewport.get_attribute("style")
                    page.mouse.up(button="middle")
                    frames = page.evaluate("() => { window.__gestureStop = true; return window.__gestureFrames; }")
                    mutations = page.evaluate("() => { window.__gestureObserver.disconnect(); return window.__gestureMutations; }")
                    elapsed = time.perf_counter() - started
                    end_metrics = metrics(session)
                    if start_style == moved_style:
                        raise RuntimeError("middle-button gesture did not change viewport transform")
                    if len(frames) < 5:
                        raise RuntimeError("insufficient frames during gesture")
                    result["samples"].append({
                        "phase": "pan", "round": index, "elapsed_seconds": elapsed,
                        "frames": summary(frames), "viewport_moved": True,
                        "mutations": mutations,
                        "cpu_seconds": {key: end_metrics[key] - start_metrics[key] for key in (
                            "TaskDuration", "ScriptDuration", "LayoutDuration", "RecalcStyleDuration",
                        )},
                    })
                output.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output.with_suffix(".png")))
                result["screenshot"] = str(output.with_suffix(".png"))
                result["node_sizes_unchanged"] = node_sizes == page.locator('.react-flow__node').evaluate_all(
                    "nodes => nodes.map(node => ({id:node.dataset.id,w:node.offsetWidth,h:node.offsetHeight}))"
                )
                if not result["node_sizes_unchanged"]:
                    raise RuntimeError("gesture changed node sizes")
                result["gallery_css"] = page.evaluate("""() => {
                  const host = document.createElement('div');
                  host.className = 'chat-spec-renderer'; host.dataset.specType = 'sketch_gallery';
                  host.style.cssText = 'position:fixed;top:100px;left:100px;z-index:9999;background:#212121;padding:20px';
                  host.innerHTML = '<div class="jr-spec"><div><div class="jr-sketch-gallery-grid">'
                    + Array.from({length:3}, () => '<div class="jr-tilt-card"><div><div><figure><img alt="fixture" /></figure></div></div></div>').join('')
                    + '</div></div></div>';
                  document.body.append(host);
                  const grid = getComputedStyle(host.querySelector('.jr-sketch-gallery-grid'));
                  const card = getComputedStyle(host.querySelector('.jr-tilt-card'));
                  const result = {display:grid.display,columns:grid.gridTemplateColumns,gap:grid.gap,width:card.width,height:card.height};
                  host.remove(); return result;
                }""")
            finally:
                browser.close()
        after = cli("get", path)
        def positions(canvas):
            return {node["id"]: node["position"] for node in canvas["nodes"]}
        result["node_positions_unchanged"] = positions(before) == positions(after)
        if not result["node_positions_unchanged"]:
            raise RuntimeError("viewport gesture changed node positions")
        result["limitations"] = [
            "Synthetic overlapping script nodes, no edges or media; not a historical build comparison.",
            "Headless browser on this machine; frame timing includes automation overhead.",
            "CPU durations do not attribute GPU raster/compositor cost.",
        ]
    finally:
        if project_id:
            cli("delete", f"/projects/{project_id}/freezone/canvases/{canvas_id}")
            cli("post", f"/projects/{project_id}/delete", {})
            purged = cli("post", f"/projects/{project_id}/purge", {})
            result["cleanup"] = purged
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", type=int, default=40)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--drop-style-selectors", action="store_true", help="Diagnostic only: remove style attribute CSS rules in the isolated browser")
    parser.add_argument("--selector-filter", default="[style")
    args = parser.parse_args()
    if not 1 <= args.nodes <= 40 or not 1 <= args.rounds <= 10:
        parser.error("nodes must be 1..40 and rounds 1..10")
    result = profile(args.output, args.nodes, args.rounds, args.drop_style_selectors, args.selector_filter)
    print(json.dumps({**result, "samples": [
        {**sample, "frames": {key: value for key, value in sample["frames"].items() if key != "samples_ms"}}
        for sample in result["samples"]
    ]}, indent=2))


if __name__ == "__main__":
    main()
