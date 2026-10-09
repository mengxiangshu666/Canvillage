"""Check deployed bundles and canvas rendering while refusing browser writes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8784")
    parser.add_argument("--page-path", required=True)
    parser.add_argument("--dist", type=Path, default=Path("frontend/dist"))
    parser.add_argument("--output", type=Path, default=Path("workspace/artifacts/model-input-quality-20261008/smoke"))
    args = parser.parse_args()
    expected = json.loads((args.dist / "version.json").read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chrome")
        for name, width, height in [("desktop", 1440, 900), ("narrow", 390, 844)]:
            context = browser.new_context(viewport={"width": width, "height": height}, service_workers="block")
            refused_writes = []
            errors = []
            scripts = {}

            def read_only(route):
                if route.request.method not in {"GET", "HEAD", "OPTIONS"}:
                    refused_writes.append({"method": route.request.method, "path": route.request.url.split("?", 1)[0]})
                    route.abort()
                else:
                    route.continue_()

            def record_script(response):
                if response.request.resource_type == "script":
                    scripts[response.url] = {"status": response.status, "body": response.text() if response.ok else ""}

            context.route("**/*", read_only)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("response", record_script)
            page.goto(args.base_url + args.page_path, wait_until="domcontentloaded")
            page.locator(".react-flow__node").first.wait_for(timeout=30000)
            page.wait_for_timeout(1500)
            version_response = page.request.get(args.base_url + "/version.json")
            assert version_response.ok
            assert version_response.json() == expected
            assert any(expected["buildId"] in item["body"] for item in scripts.values())
            assert any("freezone.lazy-" in url for url in scripts)
            assert all(item["status"] == 200 for item in scripts.values())
            overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth")
            assert not overflow and not errors, (overflow, errors)
            page.screenshot(path=str(args.output / f"{name}.png"), full_page=True)
            results.append({"viewport": name, "build": expected, "rendered_nodes": page.locator(".react-flow__node").count(),
                            "horizontal_overflow": overflow, "page_errors": errors, "refused_writes": refused_writes,
                            "scripts": {url: item["status"] for url, item in scripts.items()}})
            context.close()
        browser.close()
    (args.output / "result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps([{key: value for key, value in result.items() if key != "scripts"} for result in results], ensure_ascii=False))


if __name__ == "__main__":
    main()
