"""T-157 真机 UI 实证：社区版账号菜单里的头像入口 + 上传/移除全链。

后端接口有单独的真机脚本（`t157_account_avatar_8784.py`），这个脚本只验前端这一半：

1. 打开已部署的 8784，悬停账号头像；
2. 断言「更换头像」在**社区版**也出现（此前被 `isCeRuntime()` 藏掉）；
3. 点开弹窗、塞一张真 PNG、保存 → 断言头部真的换成 `<img>`；
4. 再点「移除头像」→ 断言回到首字母；
5. 全程 `pageerror` 必须为 0。

如果这个账号本来就有头像，脚本先取回原始字节，收尾原样传回去（跟后端脚本一个规矩），
所以跑它不会吃掉真头像。

跑法：

    .venv\\Scripts\\python.exe scripts\\acceptance\\t157_avatar_ui_8784.py

零付费：不上传任何用户素材（用的是脚本现画的 64×64 PNG）、不建项目、不调 provider。
"""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image
from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("T157_BASE_URL") or "http://127.0.0.1:8784"
ENDPOINT = f"{BASE_URL}/api/v1/account/avatar"
REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = REPO_ROOT / "workspace" / "artifacts" / "t157"
SUMMARY = ARTIFACT_DIR / "avatar-ui-8784.json"
SCREENSHOT = ARTIFACT_DIR / "avatar-menu-8784.png"
UPLOAD_FILE = ARTIFACT_DIR / "smoke-avatar.png"

ACCOUNT_BUTTON = "打开个人信息"
CHANGE_AVATAR = "更换头像"
REMOVE_AVATAR = "移除头像"
SAVE_AVATAR = "保存头像"

# 全新浏览器配置一进站就会自动弹「新功能已上线」发布说明，它盖住整个头部，悬停
# 账号按钮会被 overlay 拦下来。这里按产品自己的开关语义关掉自动弹出 —— 不是绕过
# 被测行为，头像链路跟发布说明本来就没关系。
MUTE_RELEASE_POPUP = (
    "try { window.localStorage.setItem("
    "'village-canvas:release-notifications:muted', 'true'); } catch (e) {}"
)
DISMISS_LABELS = ("我知道了", "知道了", "Got it", "关闭")


def _chrome_path() -> str | None:
    """找本机 Chrome，找不到就退回 Playwright 自带浏览器。"""

    override = os.environ.get("CHROME_PATH") or os.environ.get("T157_CHROME_PATH")
    if override and Path(override).is_file():
        return override
    for root in (
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
    ):
        if not root:
            continue
        for relative in (
            "Google/Chrome/Application/chrome.exe",
            "Google/Chrome Beta/Application/chrome.exe",
        ):
            candidate = Path(root) / relative
            if candidate.is_file():
                return str(candidate)
    return None


def _write_smoke_png() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 120, 40)).save(buffer, format="PNG")
    UPLOAD_FILE.write_bytes(buffer.getvalue())


def _get(url: str) -> tuple[int, bytes, str]:
    request = urllib.request.Request(url, method="GET", headers={"Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "")


def _read_current_avatar() -> tuple[str | None, bytes | None, str]:
    """取现场头像的 URL 与原始字节，收尾时用来原样还原。"""

    status, raw, _ = _get(ENDPOINT)
    if status != 200:
        return None, None, ""
    try:
        url = (json.loads(raw.decode("utf-8")).get("data") or {}).get("avatar_url")
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None, None, ""
    if not url:
        return None, None, ""
    blob_status, blob, blob_type = _get(f"{BASE_URL}{url}")
    if blob_status != 200:
        return None, None, ""
    return url, blob, blob_type or "image/png"


def _upload_avatar(payload: bytes, filename: str, content_type: str) -> tuple[int, Any]:
    boundary = "----t157avatarui"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8") + payload + f"\r\n--{boundary}--\r\n".encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Accept": "*/*",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return status, None


def _dismiss_leftover_overlays(page) -> list[str]:
    """兜底：万一还有别的模态盖住头部，点掉认识的关闭键。

    主路径是靠预置的静音开关，这里只是防止脚本将来被新弹层顶掉。
    """

    dismissed: list[str] = []
    for label in DISMISS_LABELS:
        button = page.get_by_role("button", name=label, exact=True).first
        try:
            if button.count() == 0 or not button.is_visible():
                continue
            button.click(timeout=3_000)
            dismissed.append(label)
        except Exception:  # noqa: BLE001 - 兜底逻辑，点不动就继续
            continue
    if dismissed:
        page.wait_for_timeout(400)
    return dismissed


def main() -> int:
    _write_smoke_png()
    before_url, before_blob, before_type = _read_current_avatar()
    report: dict = {
        "base_url": BASE_URL,
        "pre_existing_avatar": bool(before_url),
        "checks": [],
    }

    def check(name: str, ok: bool, detail=None) -> None:
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})

    page_errors: list[str] = []
    with sync_playwright() as playwright:
        launch: dict = {"headless": True}
        chrome = _chrome_path()
        if chrome:
            launch["executable_path"] = chrome
        browser = playwright.chromium.launch(**launch)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.add_init_script(MUTE_RELEASE_POPUP)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        try:
            page.goto(BASE_URL, wait_until="domcontentloaded")
            report["dismissed_overlays"] = _dismiss_leftover_overlays(page)
            account = page.get_by_label(ACCOUNT_BUTTON).first
            account.wait_for(state="visible", timeout=30_000)
            account.hover()

            change_row = page.get_by_text(CHANGE_AVATAR, exact=True).first
            change_row.wait_for(state="visible", timeout=15_000)
            check("community_edition_shows_the_avatar_entry", True, CHANGE_AVATAR)
            page.screenshot(path=str(SCREENSHOT))

            change_row.click()
            page.get_by_text(SAVE_AVATAR, exact=True).first.wait_for(
                state="visible", timeout=15_000
            )
            page.set_input_files("input[type=file]", str(UPLOAD_FILE))
            page.get_by_text(SAVE_AVATAR, exact=True).first.click()

            header_avatar = page.locator(
                'header img[src*="/static/avatars/"]'
            ).first
            header_avatar.wait_for(state="visible", timeout=30_000)
            check(
                "upload_replaces_the_initial_with_the_real_avatar",
                True,
                header_avatar.get_attribute("src"),
            )

            account.hover()
            remove_row = page.get_by_text(REMOVE_AVATAR, exact=True).first
            remove_row.wait_for(state="visible", timeout=15_000)
            remove_row.click()

            page.wait_for_function(
                "() => !document.querySelector('header img[src*=\"/static/avatars/\"]')",
                timeout=30_000,
            )
            check("removal_restores_the_initial_avatar", True, "header img gone")

            check("no_page_errors", not page_errors, page_errors[:3])
        except Exception as exc:  # noqa: BLE001 - the report carries the reason
            check("ui_flow_completed", False, f"{type(exc).__name__}: {exc}")
            page.screenshot(path=str(SCREENSHOT))
        finally:
            browser.close()

    # `removeAvatar` 会把账号清空；跑之前如果本来有头像，这里原样传回去。
    if before_url and before_blob:
        suffix = Path(before_url.split("?")[0]).suffix or ".png"
        status, body = _upload_avatar(before_blob, f"restore{suffix}", before_type)
        restored_url = ((body or {}).get("data") or {}).get("avatar_url")
        check(
            "pre_existing_avatar_restored",
            status == 200 and bool(restored_url),
            {"status": status, "url": restored_url},
        )

    report["passed"] = sum(1 for item in report["checks"] if item["ok"])
    report["total"] = len(report["checks"])
    SUMMARY.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in report["checks"]:
        print(f"{'OK ' if item['ok'] else 'FAIL'} {item['name']}: {item['detail']}")
    print(f"{report['passed']}/{report['total']} checks ok -> {SUMMARY}")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
