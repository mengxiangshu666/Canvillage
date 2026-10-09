"""Measure WebSocket frame sizes from read-only canvas clients."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


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


def _frame_size(payload: object) -> tuple[str, int]:
    if isinstance(payload, bytes):
        return "binary", len(payload)
    return "text", len(str(payload).encode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id")
    parser.add_argument("--canvas", default="user_local_17cvc3s")
    parser.add_argument("--base-url", default="http://127.0.0.1:8784")
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    url = f"{args.base_url}/projects/{args.project_id}/freezone?canvas={args.canvas}"
    frames: list[dict[str, object]] = []
    connections: list[str] = []
    with sync_playwright() as playwright:
        chrome = _chrome_path()
        browser = playwright.chromium.launch(
            executable_path=chrome,
            headless=True,
        ) if chrome else playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        pages = [context.new_page() for _ in range(3)]
        for client_index, page in enumerate(pages):
            def on_websocket(ws, index=client_index):
                connections.append(ws.url)
                ws.on(
                    "framereceived",
                    lambda payload, direction="received", idx=index: frames.append(
                        {"client": idx, "direction": direction, "size": _frame_size(payload)[1], "kind": _frame_size(payload)[0]}
                    ),
                )
                ws.on(
                    "framesent",
                    lambda payload, direction="sent", idx=index: frames.append(
                        {"client": idx, "direction": direction, "size": _frame_size(payload)[1], "kind": _frame_size(payload)[0]}
                    ),
                )
            page.on("websocket", on_websocket)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(max(1.0, args.seconds))
        for page in pages:
            page.close()
        browser.close()
    sizes = [int(frame["size"]) for frame in frames]
    result = {
        "measurement": "runtime_websocket_frame_sizes_read_only_multi_client",
        "measured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "url": url,
        "clients": len(pages),
        "window_seconds": args.seconds,
        "connections": len(connections),
        "frame_count": len(frames),
        "text_frames": sum(frame["kind"] == "text" for frame in frames),
        "binary_frames": sum(frame["kind"] == "binary" for frame in frames),
        "sent_frames": sum(frame["direction"] == "sent" for frame in frames),
        "received_frames": sum(frame["direction"] == "received" for frame in frames),
        "median_bytes": statistics.median(sizes) if sizes else None,
        "p95_bytes": sorted(sizes)[min(len(sizes) - 1, int(len(sizes) * 0.95))] if sizes else None,
        "max_bytes": max(sizes) if sizes else None,
        "frames": frames,
        "limitations": [
            "Clients only navigated and listened; no WebSocket business message was sent.",
            "This measures the current running build and is not a clean old-build comparison.",
        ],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
