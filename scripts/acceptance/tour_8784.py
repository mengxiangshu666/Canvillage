"""8784 巡场截图：把主要页面拍下来给人看，只读、不写业务数据。

用途：指挥官/评审肉眼核对真实界面，而不是靠 HTTP 200 猜页面长什么样。

拍摄范围：
  /                           项目列表（首页）
  /projects/<p>/freezone      无限画布（核心工作台）
  /projects/<p>/characters    资产台账
  /projects/<p>/production    生产/出片
  /projects/<p>/assistant     搭子（Agent）
  /projects/<p>/tasks         任务
  /projects/<p>/making        制作
  /projects/<p>/story-lab     剧本实验室
  /projects/<p>/styles        风格

零付费：只做导航和截图，不点生成、不上传、不建项目。
每页记录 pageerror / console error 和 HTTP >= 400 的响应，作为「界面到底有没有报错」的证据。

跑法：
    .venv\\Scripts\\python.exe scripts\\acceptance\\tour_8784.py [project_id]
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("TOUR_BASE_URL") or "http://127.0.0.1:8784"
REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "workspace" / "artifacts" / "tour"
SUMMARY = OUT_DIR / "tour-8784.json"

VIEWPORT = {"width": 1600, "height": 1000}

MUTE_RELEASE_POPUP = (
    "try { window.localStorage.setItem("
    "'village-canvas:release-notifications:muted', 'true'); } catch (e) {}"
)

PAGES = [
    ("home", "/"),
    ("freezone", "/projects/{p}/freezone"),
    ("characters", "/projects/{p}/characters"),
    ("production", "/projects/{p}/production"),
    ("assistant", "/projects/{p}/assistant"),
    ("tasks", "/projects/{p}/tasks"),
    ("making", "/projects/{p}/making"),
    ("story-lab", "/projects/{p}/story-lab"),
    ("styles", "/projects/{p}/styles"),
]


def _chrome_path() -> str | None:
    override = os.environ.get("CHROME_PATH")
    if override and Path(override).is_file():
        return override
    for root in (
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
    ):
        if not root:
            continue
        candidate = Path(root) / "Google/Chrome/Application/chrome.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def main() -> int:
    project = sys.argv[1] if len(sys.argv) > 1 else None
    if not project:
        project = os.environ.get("TOUR_PROJECT")
    if not project:
        print("需要一个 project_id：tour_8784.py <project_id>")
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    with sync_playwright() as pw:
        chrome = _chrome_path()
        browser = (
            pw.chromium.launch(executable_path=chrome, headless=True)
            if chrome
            else pw.chromium.launch(headless=True)
        )
        context = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
        context.add_init_script(MUTE_RELEASE_POPUP)
        page = context.new_page()

        for name, template in PAGES:
            url = f"{BASE_URL}{template.format(p=project)}"
            errors: list[str] = []
            console_errors: list[str] = []
            http_errors: list[str] = []

            def on_pageerror(exc, _e=errors):
                _e.append(str(exc))

            def on_console(msg, _c=console_errors):
                if msg.type == "error":
                    _c.append(msg.text[:400])

            def on_response(resp, _h=http_errors):
                if resp.status >= 400:
                    _h.append(f"{resp.status} {resp.url[:160]}")

            page.on("pageerror", on_pageerror)
            page.on("console", on_console)
            page.on("response", on_response)

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(3.5)
                # 关掉可能弹出的发布说明遮罩
                for label in ("我知道了", "知道了", "Got it", "关闭"):
                    try:
                        btn = page.get_by_role("button", name=label)
                        if btn.count() and btn.first.is_visible():
                            btn.first.click(timeout=1500)
                            time.sleep(0.8)
                    except Exception:
                        pass

                shot = OUT_DIR / f"{name}.png"
                page.screenshot(path=str(shot), full_page=False)
                text = page.inner_text("body")[:1200]
                results.append(
                    {
                        "name": name,
                        "url": url,
                        "title": page.title(),
                        "screenshot": str(shot.relative_to(REPO_ROOT)),
                        "page_errors": errors,
                        "console_errors": console_errors[:8],
                        "http_errors": http_errors[:8],
                        "text_head": text,
                    }
                )
                print(f"[ok] {name:12s} errors={len(errors)} console={len(console_errors)} http4xx={len(http_errors)}")
            except Exception as exc:
                results.append({"name": name, "url": url, "goto_error": str(exc)[:300]})
                print(f"[FAIL] {name:12s} {exc}"[:200])
            finally:
                page.remove_listener("pageerror", on_pageerror)
                page.remove_listener("console", on_console)
                page.remove_listener("response", on_response)

        browser.close()

    SUMMARY.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n汇总: {SUMMARY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
