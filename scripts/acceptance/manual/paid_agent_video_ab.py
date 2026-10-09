from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import websockets


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "pyproject.toml").is_file())
PROJECT = "01M0T85BVF1TQYEH2T4F18V9H4"
CANVAS = "default"
NODE = "agent-c6cdf17bd016-1"
OUT_DIR = REPO_ROOT / "项目资产" / "state" / "agent_paid_eval" / "20260825-direct-h3"
TURN_ID = f"paid-ab-{int(time.time())}"

REQUEST = (
    "上一版真实视频已经生成。现在只复用现有视频节点 "
    f"{NODE}，禁止创建新节点、禁止创建新工作流、禁止停在提案或 emit_only。"
    "请基于上一版反馈优化同一镜头：保持 6 秒、16:9、768p、当前默认视频模型 direct_video-3157c550f1188060（上游 minimax_h3）、文生视频、无音频；"
    "强化第三秒格挡接触的清晰度和第四秒刺客失衡后退的身体反馈，保持雨夜破败古刹檐廊、"
    "黑袍刀客与白面锦衣刺客的外貌服装武器和场景结构稳定。请直接启动 1 条真实付费视频，"
    "拿到真实 task id、任务完成回执和落盘视频后才结束。"
)


def envelope() -> str:
    payload = {
        "v": 2,
        "request": REQUEST,
        "execution_lane": "canvas_execute",
        "run_mode": "auto",
        "task_authorization": {
            "scope": "current_turn",
            "run_mode": "auto",
            "allow_structure": True,
            "allow_paid_media": True,
            "max_paid_starts": 1,
            "require_video_confirmation": False,
        },
        "canvas": {"project_id": PROJECT, "canvas_id": CANVAS, "revision": 29},
        "success_criteria": [
            "只复用现有节点，不创建新节点",
            "只启动 1 条视频任务",
            "返回真实 provider task id",
            "model=direct_video-3157c550f1188060、resolution=768p、aspect_ratio=16:9、duration_seconds=6、generate_audio=false",
            "非空视频产物落盘并回写现有节点",
        ],
    }
    return "[CANVAS_AGENT_REQUEST_V2]" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "[/CANVAS_AGENT_REQUEST_V2]"


async def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frames: list[dict] = []
    terminal = {"chat.done", "chat.recoverable", "error"}
    async with websockets.connect(
        "ws://127.0.0.1:8784/api/v1/chat/ws",
        open_timeout=10,
        ping_interval=20,
        ping_timeout=30,
        max_size=20 * 1024 * 1024,
    ) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "scope.set", "scope": {"kind": "project", "id": PROJECT, "canvas_id": CANVAS, "conversation_id": "paid-ab"}}, ensure_ascii=False))
        while True:
            frame = json.loads(await asyncio.wait_for(ws.recv(), 30))
            frames.append(frame)
            print(json.dumps({"type": frame.get("type"), "stage": frame.get("stage"), "tool": frame.get("tool_name") or frame.get("name"), "message": frame.get("message"), "turn_id": frame.get("turn_id")}, ensure_ascii=False), flush=True)
            if frame.get("type") == "scope.changed":
                await ws.send(json.dumps({"type": "chat.message", "scope": {"kind": "project", "id": PROJECT, "canvas_id": CANVAS, "conversation_id": "paid-ab"}, "text": envelope(), "turn_id": TURN_ID, "agent_engine": "village", "research_enabled": False}, ensure_ascii=False))
            if frame.get("type") in terminal and frame.get("turn_id") == TURN_ID:
                break
    (OUT_DIR / "frames.json").write_text(json.dumps(frames, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"turn_id": TURN_ID, "frames": len(frames), "saved": str(OUT_DIR)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
