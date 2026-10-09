"""T-153 真实 8784 实证：逐镜视频的「上一镜承接」参考边。

验的是**真实部署产物**上这条链走不走得通，不是单测里的桩：

1. 在真实 8784 上建一个隔离项目（不碰任何既有项目）；
2. 用公开的画布写入接口播一张最小画布：一个脚本节点（三镜，前两镜同场景、
   第三镜换场）+ 三张已出图的分镜图节点 + 三条 storyboard 血缘边；
3. 用真实浏览器打开这张画布，点脚本节点的「逐镜出视频」→「仅建节点」
   （**不提交任何付费生成**），走的就是用户在用的那条 UI 路径；
4. 回读画布，断言边的角色、方向与**边序**：每条视频节点都有自己的首帧边，
   且首帧边排在承接边之前（提交时第一张图就是首帧）。

跑法：

    .venv\\Scripts\\python.exe scripts\\acceptance\\t153_shot_continuity_8784.py

依赖：本机 8784 已在跑、Google Chrome 已安装（按 `PROGRAMFILES` / `LOCALAPPDATA`
推导，或用 `CHROME_PATH` 指路径；都没有就退回 Playwright 自带浏览器）。
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


BASE_URL = "http://127.0.0.1:8784"
ARTIFACT_DIR = Path("workspace/artifacts/t153")
SCREENSHOT = ARTIFACT_DIR / "shot-continuity-8784.png"
SUMMARY = ARTIFACT_DIR / "shot-continuity-8784.json"


def _chrome_path() -> str | None:
    """找本机 Chrome，找不到就返回 None（退回 Playwright 自带浏览器）。

    不写死盘符：写死了这个脚本就只能在某一台机器上跑，而它是给人复跑的。
    """
    override = os.environ.get("CHROME_PATH") or os.environ.get("T153_CHROME_PATH")
    if override and Path(override).is_file():
        return override
    roots = [
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    for root in roots:
        if not root:
            continue
        candidate = Path(root) / "Google/Chrome/Application/chrome.exe"
        if candidate.is_file():
            return str(candidate)
    return None

CANVAS_ID = "t153verify"
SCRIPT_NODE_ID = "t153-script"

SHOT_VIDEO_ROLE = "scriptShotVideo"
SHOT_CONTINUITY_ROLE = "scriptShotContinuity"

# 1x1 透明 PNG 的 data URL。分镜图节点只需要一个**非空**图片地址就能进派生；
# 用 data URL 是为了让画布渲染干净（不产生 404 噪音），同时不写任何真实素材。
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
    """三镜：1/2 同场景（该接承接边），3 换场景（不该接）。"""
    return [
        {
            "shot_id": "t153_shot_a",
            "shot_no": "1",
            "duration": "4",
            "shot": "中景",
            "scene_tags": "祠堂",
            "character_1": "阿雀",
            "visual_description": "阿雀推门而入",
            "shot_prompt": "中景拍阿雀推门",
            "video_motion_prompt": "门被推开，阿雀侧身走入",
        },
        {
            "shot_id": "t153_shot_b",
            "shot_no": "2",
            "duration": "4",
            "shot": "近景",
            "scene_tags": "祠堂",
            "character_1": "阿雀",
            "visual_description": "阿雀抬眼看向供桌",
            "shot_prompt": "近景拍阿雀抬眼",
            "video_motion_prompt": "镜头缓缓推近，阿雀抬起眼睛",
        },
        {
            "shot_id": "t153_shot_c",
            "shot_no": "3",
            "duration": "4",
            "shot": "全景",
            "scene_tags": "山道",
            "visual_description": "山道上雨丝斜落",
            "shot_prompt": "全景拍山道雨景",
            "video_motion_prompt": "镜头缓缓拉远，雨丝斜落",
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
                "scriptTitle": "T-153 承接边实证",
                "scriptResult": {"title": "T-153 承接边实证", "rows": _rows()},
            },
        }
    ]
    edges: list[dict[str, Any]] = []
    for index, shot_id in enumerate(("t153_shot_a", "t153_shot_b", "t153_shot_c")):
        image_id = f"t153-image-{shot_id}"
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
                "id": f"t153-storyboard-{shot_id}",
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


def _upstream_edges_in_order(
    edges: list[dict[str, Any]], target: str
) -> list[dict[str, Any]]:
    """提交时取图的顺序（与 `upstreamNodesInEdgeOrder` 同一口径：按边序、按源去重）。"""
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for edge in edges:
        if edge.get("target") != target:
            continue
        source = str(edge.get("source"))
        if source in seen:
            continue
        seen.add(source)
        ordered.append(edge)
    return ordered


def _roles_to(edges: list[dict[str, Any]], target: str) -> list[str]:
    return [
        str((edge.get("data") or {}).get("role") or "")
        for edge in _upstream_edges_in_order(edges, target)
    ]


def main() -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d%H%M%S")
    project_name = f"t153_verify_{stamp}"
    project_id = ""
    report: dict[str, Any] = {
        "project": project_name,
        "steps": [],
        "checks": {},
        "ok": False,
    }

    try:
        created = _request("POST", "/api/v1/projects", {"name": project_name})
        project_id = created["data"]["id"]
        report["project_id"] = project_id
        report["steps"].append("project_created")

        _request(
            "PUT",
            f"/api/v1/projects/{project_id}/freezone/canvases/{CANVAS_ID}",
            _canvas_payload(),
        )
        report["steps"].append("canvas_seeded")

        # 画布活在项目作用域路由下（`/?p=..&canvas=..` 是后端旧式外链形态，
        # 前端只在走 buildFreezoneUrl 时才吃它，直接开会被丢回项目管理中心）。
        url = f"{BASE_URL}/projects/{project_id}/freezone?canvas={CANVAS_ID}"
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
            page.wait_for_timeout(6_000)

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
            # 弹层是普通 div（没有 role=dialog），从「仅建节点」往上取面板本身。
            try:
                panel = create_only.locator("xpath=ancestor::div[3]")
                report["dialog_text"] = panel.inner_text()[:600]
            except Exception:  # noqa: BLE001 - 取不到面板就用整页文本兜底
                report["dialog_text"] = page.inner_text("body")[:2_000]
            create_only.click()
            page.wait_for_timeout(4_000)
            page.screenshot(path=str(SCREENSHOT))
            browser.close()

        report["console_errors"] = console_errors
        report["page_errors"] = page_errors
        report["steps"].append("ui_derived")

        canvas = _request(
            "GET", f"/api/v1/projects/{project_id}/freezone/canvases/{CANVAS_ID}"
        )
        payload = _payload_of(canvas)
        nodes = list(payload.get("nodes") or [])
        edges = list(payload.get("edges") or [])
        video_nodes = [node for node in nodes if node.get("type") == "videoNode"]
        report["video_node_count"] = len(video_nodes)
        report["edge_roles"] = sorted(
            {str((edge.get("data") or {}).get("role") or "") for edge in edges}
        )

        by_row = {
            str((node.get("data") or {}).get("scriptShotRowKey") or ""): node
            for node in video_nodes
        }
        report["video_rows"] = sorted(by_row)
        checks = report["checks"]
        checks["three_video_nodes"] = len(video_nodes) == 3

        def roles_for(row_key: str) -> list[str]:
            node = by_row.get(row_key) or {}
            node_id = str(node.get("id") or "")
            return _roles_to(edges, node_id) if node_id else []

        first_roles = roles_for("t153_shot_a")
        second_roles = roles_for("t153_shot_b")
        third_roles = roles_for("t153_shot_c")
        report["roles"] = {
            "shot_a": first_roles,
            "shot_b": second_roles,
            "shot_c": third_roles,
        }

        checks["first_shot_has_no_continuity"] = (
            SHOT_CONTINUITY_ROLE not in first_roles
        )
        checks["second_shot_has_continuity"] = (
            SHOT_CONTINUITY_ROLE in second_roles
        )
        checks["first_frame_edge_is_first"] = (
            bool(second_roles) and second_roles[0] == SHOT_VIDEO_ROLE
        )
        checks["second_shot_upstream_count"] = len(second_roles) == 2
        checks["scene_change_has_no_continuity"] = (
            SHOT_CONTINUITY_ROLE not in third_roles
        )
        checks["every_video_has_first_frame_edge"] = all(
            SHOT_VIDEO_ROLE
            in _roles_to(edges, str(node.get("id") or ""))
            for node in video_nodes
        )
        checks["no_page_errors"] = not page_errors
        # 面向用户的那句话必须真的说出来：这批镜头里有且只有第 2 镜会承接上一镜。
        checks["dialog_states_continuity"] = (
            "1 个同场景的相邻镜头会同时参考上一镜的画面" in str(report["dialog_text"])
        )
        report["ok"] = all(checks.values())
    except Exception as exc:  # noqa: BLE001 - 实证脚本要把失败原因原样带回
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if project_id:
            try:
                _request("POST", f"/api/v1/projects/{project_id}/delete")
                report["steps"].append("project_deleted")
            except Exception as exc:  # noqa: BLE001
                report["cleanup_error"] = str(exc)
        SUMMARY.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
