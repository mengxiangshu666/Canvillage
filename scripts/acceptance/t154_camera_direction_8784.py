"""T-154 真实运行时实证：运镜归口提示词 + 脚本派生的音频合同 + 逐镜体检表。

验的是**真实服务端 + 真实浏览器**上这条路，不是单测里的桩：

1. 在真实后端建一个隔离项目（不碰任何既有项目），播一张最小画布：一个脚本节点
   （三镜：一镜有音效无台词、一镜有台词、一镜两者皆无；其中一镜的运动稿没有运镜段）
   + 三张已出图的分镜图节点 + 三条 `storyboard` 血缘边；
2. 真实浏览器打开这张画布，点脚本节点的「逐镜出视频」，**只读**弹层里的「逐镜核对」
   表：断言三镜的音轨路由（原生声音 / 外部配音 / 静音）、运镜、提示词来源、缺项标记
   都摊在表上；点「取消」后回读画布，断言**审核不产生任何副作用**；
3. 再点一次「仅建节点」（**不提交任何付费生成**），回读画布断言派生结果：
   每镜的 `generateAudio` / `nativeAudioStrategy` / `audioType` / `dialogueText` /
   `spokenDialogue` 与行事实一致，且**没有任何视频节点**带 `cameraMovement` 字段。

跑法：

    .venv\\Scripts\\python.exe scripts\\acceptance\\t154_camera_direction_8784.py

要验**当前源码**（不是线上已部署的旧构建）时，先起前端 dev server 再把地址指过去：

    cd frontend && npx vite --port 5188 --strictPort
    $env:T154_BASE_URL = "http://127.0.0.1:5188"
    .venv\\Scripts\\python.exe scripts\\acceptance\\t154_camera_direction_8784.py

依赖：后端已在跑（dev server 会把 /api/v1 代理过去）、Google Chrome 已安装
（按 `PROGRAMFILES` / `LOCALAPPDATA` 推导，或用 `CHROME_PATH` 指路径）。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("T154_BASE_URL") or "http://127.0.0.1:8784"
ARTIFACT_DIR = Path("workspace/artifacts/t154")
SCREENSHOT = ARTIFACT_DIR / "shot-audit-8784.png"
SUMMARY = ARTIFACT_DIR / "shot-audit-8784.json"

CANVAS_ID = "t154verify"
SCRIPT_NODE_ID = "t154-script"
SHOT_IDS = ("t154_shot_a", "t154_shot_b", "t154_shot_c")


def _chrome_path() -> str | None:
    """找本机 Chrome，找不到就返回 None（退回 Playwright 自带浏览器）。"""
    override = os.environ.get("CHROME_PATH") or os.environ.get("T154_CHROME_PATH")
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


# 1x1 透明 PNG 的 data URL：分镜图节点只需要一个非空图片地址就能进派生，
# 用 data URL 是为了不写任何真实素材、也不产生 404 噪音。
ONE_PIXEL_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _request(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from exc
    return json.loads(raw) if raw else None


def _rows() -> list[dict[str, Any]]:
    """三镜覆盖三种音频路由，外加一镜没有运镜段（体检表该标「无运镜」）。"""
    return [
        {
            "shot_id": "t154_shot_a",
            "shot_no": "1",
            "duration": "4",
            "shot": "中景",
            "scene_tags": "祠堂",
            "character_1": "阿雀",
            "visual_description": "阿雀推门而入",
            "shot_prompt": "中景拍阿雀推门",
            "video_motion_prompt": "[运镜轨迹] 固定机位 + [时长：4.0s]",
            "sound": "雨声、门轴吱呀声",
            "dialogue": "无",
        },
        {
            "shot_id": "t154_shot_b",
            "shot_no": "2",
            "duration": "3",
            "shot": "近景",
            "scene_tags": "祠堂",
            "character_1": "阿雀",
            "visual_description": "阿雀抬眼看向供桌",
            "shot_prompt": "近景拍阿雀抬眼",
            "video_motion_prompt": "[运镜轨迹] 镜头前推 + [时长：3.0s]",
            "sound": "无",
            "dialogue": "你到底来不来？我等到天亮。",
        },
        {
            "shot_id": "t154_shot_c",
            "shot_no": "3",
            "duration": "4",
            "shot": "全景",
            "scene_tags": "山道",
            "visual_description": "山道上雨丝斜落",
            "shot_prompt": "全景拍山道雨景",
            "video_motion_prompt": "[主体极其具体的物理动作：雨珠砸在门环上]",
            "sound": "无",
            "dialogue": "无",
        },
    ]


def _canvas_payload() -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {
            "id": SCRIPT_NODE_ID,
            "type": "scriptNode",
            "position": {"x": 0, "y": 0},
            "style": {"width": 900, "height": 420},
            "data": {
                "scriptTitle": "T-154 运镜与音频实证",
                "scriptResult": {
                    "title": "T-154 运镜与音频实证",
                    "rows": _rows(),
                },
            },
        }
    ]
    edges: list[dict[str, Any]] = []
    for index, shot_id in enumerate(SHOT_IDS):
        image_id = f"t154-image-{shot_id}"
        nodes.append(
            {
                "id": image_id,
                "type": "imageGenNode",
                "position": {"x": 1100, "y": index * 380},
                "style": {"width": 320, "height": 320},
                "data": {
                    "imageUrl": ONE_PIXEL_PNG,
                    "scriptShotId": shot_id,
                    "displayName": f"分镜 #{index + 1}",
                },
            }
        )
        edges.append(
            {
                "id": f"t154-storyboard-{shot_id}",
                "source": SCRIPT_NODE_ID,
                "target": image_id,
                "sourceHandle": "source",
                "targetHandle": "target",
                "type": "disconnectableEdge",
                "data": {
                    "edgeKind": "mainline_data",
                    "propagates": True,
                    "role": "storyboard",
                },
            }
        )
    return {
        "schema_version": 2,
        "canvas_id": CANVAS_ID,
        "save_source": "manual_save",
        "nodes": nodes,
        "edges": edges,
    }


def _payload_of(canvas: dict[str, Any]) -> dict[str, Any]:
    data = canvas.get("data")
    return data if isinstance(data, dict) else canvas


def _video_nodes() -> list[dict[str, Any]]:
    payload = _payload_of(
        _request("GET", f"/api/v1/projects/{PROJECT_ID}/freezone/canvases/{CANVAS_ID}")
    )
    return [
        node for node in (payload.get("nodes") or []) if node.get("type") == "videoNode"
    ]


PROJECT_ID = ""


def main() -> int:
    global PROJECT_ID
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d%H%M%S")
    project_name = f"t154_verify_{stamp}"
    report: dict[str, Any] = {
        "base_url": BASE_URL,
        "project": project_name,
        "steps": [],
        "checks": {},
        "ok": False,
    }

    try:
        created = _request("POST", "/api/v1/projects", {"name": project_name})
        PROJECT_ID = created["data"]["id"]
        report["project_id"] = PROJECT_ID
        report["steps"].append("project_created")

        _request(
            "PUT",
            f"/api/v1/projects/{PROJECT_ID}/freezone/canvases/{CANVAS_ID}",
            _canvas_payload(),
        )
        report["steps"].append("canvas_seeded")
        seeded_nodes = len(_payload_of(_request(
            "GET", f"/api/v1/projects/{PROJECT_ID}/freezone/canvases/{CANVAS_ID}"
        )).get("nodes") or [])

        url = f"{BASE_URL}/projects/{PROJECT_ID}/freezone?canvas={CANVAS_ID}"
        console_errors: list[str] = []
        page_errors: list[str] = []
        with sync_playwright() as playwright:
            launch_args: dict[str, Any] = {"headless": True, "args": ["--no-sandbox"]}
            chrome = _chrome_path()
            if chrome:
                launch_args["executable_path"] = chrome
            report["browser"] = chrome or "playwright-bundled"
            browser = playwright.chromium.launch(**launch_args)
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.on(
                "console",
                lambda message: (
                    console_errors.append(message.text)
                    if message.type == "error"
                    else None
                ),
            )
            page.on("pageerror", lambda error: page_errors.append(str(error)))

            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(7_000)

            release_notes = page.get_by_role("button", name="我知道了")
            if release_notes.count():
                release_notes.click()
                page.wait_for_timeout(500)

            shot_video_button = page.get_by_role("button", name="逐镜出视频")
            shot_video_button.first.wait_for(timeout=30_000)
            report["steps"].append("script_node_rendered")
            shot_video_button.first.click()

            create_only = page.get_by_role("button", name="仅建节点")
            create_only.wait_for(timeout=20_000)
            page.wait_for_timeout(1_000)
            audit_text = page.inner_text("body")
            report["dialog_text"] = audit_text[:4_000]
            page.screenshot(path=str(SCREENSHOT))
            report["steps"].append("audit_table_read")

            checks = report["checks"]
            checks["audit_table_rendered"] = "逐镜核对" in audit_text
            checks["audit_counts_three_shots"] = "共 3 镜" in audit_text
            checks["audit_lists_native_audio"] = "原生声音" in audit_text
            checks["audit_lists_external_audio"] = "外部配音" in audit_text
            checks["audit_lists_silent"] = "静音" in audit_text
            checks["audit_names_prompt_source"] = "运动稿" in audit_text
            checks["audit_shows_shot_dialogue"] = "台词：你到底来不来" in audit_text
            checks["audit_flags_missing_camera"] = "无运镜" in audit_text
            checks["audit_shows_camera_text"] = "镜头前推" in audit_text

            # 只审核、不点生成：画布必须一个节点都没变。
            page.get_by_role("button", name="取消").first.click()
            page.wait_for_timeout(1_500)
            after_review = len(_payload_of(_request(
                "GET", f"/api/v1/projects/{PROJECT_ID}/freezone/canvases/{CANVAS_ID}"
            )).get("nodes") or [])
            checks["review_has_no_side_effects"] = after_review == seeded_nodes
            report["steps"].append("review_cancelled")

            shot_video_button.first.click()
            create_only = page.get_by_role("button", name="仅建节点")
            create_only.wait_for(timeout=20_000)
            create_only.click()
            page.wait_for_timeout(5_000)
            browser.close()

        report["console_errors"] = console_errors
        report["page_errors"] = page_errors
        report["steps"].append("ui_derived")

        video_nodes = _video_nodes()
        report["video_node_count"] = len(video_nodes)
        checks["three_video_nodes"] = len(video_nodes) == 3
        checks["no_page_errors"] = not page_errors

        by_row = {
            str((node.get("data") or {}).get("scriptShotRowKey") or ""): node.get("data")
            or {}
            for node in video_nodes
        }
        report["video_rows"] = sorted(by_row)
        report["audio_contract"] = {
            key: {
                field: data.get(field)
                for field in (
                    "generateAudio",
                    "nativeAudioStrategy",
                    "audioType",
                    "dialogueText",
                    "spokenDialogue",
                )
            }
            for key, data in by_row.items()
        }

        # 行标识优先取脚本行的 `shot_id`（与分镜图节点同一套键）。
        row_a = by_row.get("t154_shot_a") or {}
        row_b = by_row.get("t154_shot_b") or {}
        row_c = by_row.get("t154_shot_c") or {}

        checks["sound_only_shot_requests_native_audio"] = (
            row_a.get("generateAudio") is True
            and row_a.get("nativeAudioStrategy") == "native"
            and row_a.get("audioType") in (None, "")
        )
        checks["dialogue_shot_routes_to_external_dubbing"] = (
            row_b.get("generateAudio") is False
            and row_b.get("audioType") == "dialogue"
            and row_b.get("nativeAudioStrategy") == "external"
            and row_b.get("dialogueText") == "你到底来不来？我等到天亮。"
            and row_b.get("spokenDialogue") == ["你到底来不来", "我等到天亮"]
        )
        checks["silent_shot_stays_silent"] = (
            row_c.get("generateAudio") is False
            and row_c.get("audioType") in (None, "")
            and row_c.get("nativeAudioStrategy") in (None, "")
        )
        checks["no_video_node_carries_camera_movement_field"] = all(
            "cameraMovement" not in (node.get("data") or {}) for node in video_nodes
        )
        checks["derived_prompts_keep_camera_segment"] = (
            "运镜轨迹" in str(row_a.get("prompt") or "")
            and "运镜轨迹" in str(row_b.get("prompt") or "")
        )

        report["ok"] = all(report["checks"].values())
        SUMMARY.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        if PROJECT_ID:
            try:
                _request("POST", f"/api/v1/projects/{PROJECT_ID}/delete")
                report["steps"].append("project_deleted")
            except Exception as exc:  # noqa: BLE001 - 清理失败不影响实证结论
                report["cleanup_error"] = str(exc)

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
