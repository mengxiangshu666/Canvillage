"""Measure the running canvas HUD during idle, pan, and zoom interactions."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from urllib.parse import urlparse, urlunparse
import time
from pathlib import Path

def _script_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def version_metadata(frontend_version: object, backend_version: object) -> dict:
    return {
        "backend_version": backend_version,
        "backend_build_id": backend_version.get("buildId") if isinstance(backend_version, dict) else None,
        "frontend_build_id": frontend_version.get("buildId") if isinstance(frontend_version, dict) else None,
        "build_id": frontend_version.get("buildId") if isinstance(frontend_version, dict) else None,
    }


def summarize_gesture_frames(frames: list[float], *, before: str | None, after: str | None) -> dict:
    if not before or not after or before == after:
        raise RuntimeError("gesture did not move viewport")
    if len(frames) < 5 or any(isinstance(frame, bool) or not isinstance(frame, (int, float)) or not math.isfinite(frame) or frame <= 0 for frame in frames):
        raise RuntimeError("insufficient or invalid rAF frames during gesture")
    ordered = sorted(frames)
    return {
        "frame_count": len(frames),
        "rAF_interval_p95_ms": round(float(ordered[math.ceil(len(ordered) * 0.95) - 1]), 3),
        "viewport_moved": True,
        "sampling": "gesture_in_progress_rAF",
    }


def _gesture_frames(page, action: str, center_x: float, center_y: float) -> dict:
    """Sample rAF intervals while the input gesture is still active."""
    page.evaluate("""() => {
      window.__canvasGesture = {frames: [], last: null, stop: false};
      const tick = now => {
        const state = window.__canvasGesture;
        if (state.stop) return;
        if (state.last !== null) state.frames.push(now - state.last);
        state.last = now;
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    }""")
    before = page.locator('.react-flow__viewport').first.get_attribute('style')
    page.mouse.move(center_x, center_y)
    if action == "pan":
        page.mouse.down(button="middle")
        for step in range(1, 61):
            page.mouse.move(center_x + step * 2, center_y + step)
            page.wait_for_timeout(16)
        page.mouse.up(button="middle")
    else:
        for _step in range(30):
            page.mouse.wheel(0, -20)
            page.wait_for_timeout(16)
    after = page.locator('.react-flow__viewport').first.get_attribute('style')
    frames = page.evaluate("""() => {
      window.__canvasGesture.stop = true;
      return window.__canvasGesture.frames;
    }""")
    return summarize_gesture_frames(frames, before=before, after=after)


def _chrome_path() -> str | None:
    override = os.environ.get("CHROME_PATH")
    if override and Path(override).is_file():
        return override
    for root in (
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
    ):
        if root:
            candidate = Path(root) / "Google/Chrome/Application/chrome.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def _read_hud(page) -> dict[str, int | float] | None:
    try:
        text = page.locator('[data-testid="canvas-performance-hud"]').inner_text()
    except Exception:
        return None
    values: dict[str, int | float] = {}
    for label in ("FPS", "P95", "掉帧", "节点", "边", "可视", "渲染"):
        match = re.search(rf"(?:{re.escape(label)}\s*)(\d+(?:\.\d+)?)", text)
        if not match:
            match = re.search(rf"(\d+(?:\.\d+)?)\s*{re.escape(label)}", text)
        if match:
            raw = match.group(1)
            values[label] = float(raw) if "." in raw else int(raw)
    if "FPS" not in values or "P95" not in values:
        return None
    return {
        "fps": values["FPS"],
        "p95_ms": values["P95"],
        "dropped_frames": values.get("掉帧", 0),
        "canvas_nodes": values.get("节点", 0),
        "canvas_edges": values.get("边", 0),
        "visible_nodes": values.get("可视", 0),
        "canvas_render_count": values.get("渲染", 0),
    }


def _sample(page, seconds: float = 2.5) -> dict[str, int | float | None]:
    deadline = time.monotonic() + seconds
    samples: list[dict[str, int | float]] = []
    while time.monotonic() < deadline:
        sample = _read_hud(page)
        if sample:
            samples.append(sample)
        time.sleep(0.25)
    if not samples:
        return {"sample_count": 0, "sample": None}
    keys = samples[0].keys()
    summary: dict[str, int | float] = {"sample_count": len(samples)}
    for key in keys:
        values = [sample[key] for sample in samples]
        summary[key] = max(values) if key in {"fps", "dropped_frames"} else min(values)
    summary["p95_ms_max"] = max(sample["p95_ms"] for sample in samples)
    return summary


def main() -> int:
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser()
    parser.add_argument("project_id")
    parser.add_argument("--canvas", required=True, help="Explicit isolated fixture canvas ID")
    parser.add_argument("--base-url", default="http://127.0.0.1:8784")
    parser.add_argument("--api-base-url", default="")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    url = f"{args.base_url}/projects/{args.project_id}/freezone?__canvas_perf=1&canvas={args.canvas}"
    with sync_playwright() as playwright:
        chrome = _chrome_path()
        browser = playwright.chromium.launch(
            executable_path=chrome,
            headless=True,
        ) if chrome else playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        if args.api_base_url:
            page_base = urlparse(args.base_url)
            api_base = urlparse(args.api_base_url)

            def rewrite_api(route):
                request_url = urlparse(route.request.url)
                static_path = (
                    request_url.path == "/"
                    or request_url.path.startswith("/assets/")
                    or request_url.path.startswith("/locales/")
                    or request_url.path.startswith("/favicon")
                    or request_url.path == "/index.html"
                )
                if (
                    request_url.netloc == page_base.netloc
                    and route.request.resource_type != "document"
                    and not static_path
                ):
                    route.continue_(
                        url=urlunparse(
                            api_base._replace(
                                path=request_url.path,
                                query=request_url.query,
                            )
                        )
                    )
                else:
                    route.continue_()

            page.route("**/*", rewrite_api)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)
        toggle = page.get_by_role("button", name="开启 FPS 显示")
        if toggle.count() and toggle.first.is_visible():
            toggle.first.click()
        page.wait_for_timeout(1500)
        samples: dict[str, object] = {"idle": _sample(page)}
        gesture_samples: dict[str, object] = {}
        pane = page.locator(".react-flow__pane").first
        pane_count = page.locator(".react-flow__pane").count()
        box = pane.bounding_box(timeout=3000) if pane_count else None
        if box:
            center_x = box["x"] + box["width"] / 2
            center_y = box["y"] + box["height"] / 2
            gesture_samples["pan"] = _gesture_frames(page, "pan", center_x, center_y)
            samples["pan"] = _sample(page)
            gesture_samples["zoom"] = _gesture_frames(page, "zoom", center_x, center_y)
            samples["zoom"] = _sample(page)
        else:
            raise RuntimeError("canvas pane missing; no valid gesture measurement")
        version = None
        try:
            version = page.request.get(f"{args.base_url}/version.json").json()
        except Exception:
            version = None
        backend_version = None
        try:
            backend_version = page.request.get(f"{args.api_base_url or args.base_url}/version.json").json()
        except Exception:
            backend_version = None
        node_counts = {
            "canvas_nodes": samples["idle"].get("canvas_nodes") if isinstance(samples["idle"], dict) else None,
            "canvas_edges": samples["idle"].get("canvas_edges") if isinstance(samples["idle"], dict) else None,
            "visible_nodes": samples["idle"].get("visible_nodes") if isinstance(samples["idle"], dict) else None,
        }
        result = {
            "measurement": "runtime_canvas_browser_interaction_gesture_rAF_v2",
            "schema": "canvas_browser_interaction.v2",
            "measured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "url": url,
            "page_title": page.title(),
            "hud_count": page.locator('[data-testid="canvas-performance-hud"]').count(),
            "pane_count": page.locator(".react-flow__pane").count(),
            "mode": "read-only HUD; no node selection, task submission, or canvas command",
            "samples": samples,
            "gesture_samples": gesture_samples,
            "metadata": {
                "metric_schema": "legacy_post_gesture_hud.v1",
                **version_metadata(version, backend_version),
                "browser_version": browser.version,
                "interaction_script_sha256": _script_sha256(),
                "viewport": {"width": 1600, "height": 1000},
                "scenario_id": "hud_canvas_runtime",
                **node_counts,
            },
            "limitations": [
                "Browser cache and media decode state are not a clean old-build A/B.",
                "Legacy samples are post-gesture HUD windows; gesture_samples are the in-progress rAF metric.",
            ],
        }
        browser.close()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
