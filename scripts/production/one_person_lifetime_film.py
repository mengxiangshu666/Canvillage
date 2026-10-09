"""Produce one authorized four-part, one-take-style life film in project 1."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "src" / "novelvideo" / "canvas_cli.py"
PROJECT = "01M43CEMSHDGHMGZ7CA8T9GHTY"
CANVAS = "user_local_17cvc3s"
MODEL = "MiniMax-H3"
CHARACTER = (
    "http://127.0.0.1:8784/static/projects/01M43CEMSHDGHMGZ7CA8T9GHTY/"
    "freezone/_outputs/freezone_gen/fc5fb8be239240b8.png?v=1791115487639731100"
)
OUT = ROOT / "项目资产" / "production" / "one-person-lifetime-20261005"

BASE = (
    "同一个人王强，身份以参考图为准，荒诞纪实电影质感，35mm胶片微颗粒，"
    "冷暖对比，高动态范围，自然光，真实中国北方生活质感。"
    "这是同一条连续长镜头的一个段落，摄影机永远在运动，不能切镜，不能出现字幕或文字。"
)
SEGMENTS = (
    (
        "01少年出发",
        BASE
        + "15秒。清晨河北小城旧街，王强约18岁，穿洗旧的校服外套，背着帆布包从家门冲出。"
        "摄影机从门内低机位贴地向后退，绕过门框跟随他冲下台阶，快速升高越过他的肩膀，"
        "看见他第一次奔向长途车站；最后他跑过一辆驶过镜头前的白色卡车，卡车车身完全遮满画面，"
        "为下一段无缝接入。环境声是脚步、清晨风、远处自行车铃，音乐只做很轻的弦乐脉冲。",
    ),
    (
        "02成年离开",
        BASE
        + "15秒，必须从上一段白色卡车遮满画面开始。卡车移开后，王强约30岁，仍是同一张脸，"
        "穿藏青工装夹克，在高速公路服务区推着沉重行李车快速前进。摄影机从车身后猛然横移出来，"
        "绕车一周，低机位贴着轮胎滑行，再抬升到王强肩后；他停下回头看远方，手里攥紧一封信。"
        "最后摄影机贴近信封白面，信封占满画面，纸面纹理遮住全部画面，作为下一段转场。",
    ),
    (
        "03中年失去",
        BASE
        + "15秒，必须从信封白面开始。信封被王强的手放到医院走廊的长椅上，王强约48岁，"
        "头发有少量白发，穿雨水打湿的深色夹克。他沿走廊快走，摄影机在他前方倒退，经过一扇扇玻璃门，"
        "每经过一扇门，反射里的他都更疲惫；他突然停下，手扶玻璃，呼吸失控又强行压住。"
        "摄影机绕到他背后向上升，玻璃反光变成夜空的星点，最后一滴雨水滑过镜头正中并铺满画面。",
    ),
    (
        "04晚年回望",
        BASE
        + "15秒，必须从镜头正中的雨滴开始。雨滴滑落露出几十年后的王强，约75岁，白发，"
        "穿旧灰色外套，站在同一条旧街的黄昏尽头。摄影机绕他一整圈，从正面近景退到全身，"
        "再沿街道向后升高，王强看见年轻时的自己从远处跑过，却没有追，只是微笑。"
        "他抬头迎着最后一束阳光，摄影机持续升到屋顶高度，整座小城和长长街道进入画面；"
        "最后停在王强仍站在街中央的远景，保留两秒呼吸，不切黑。环境声从雨声过渡到风和远处孩子笑声，音乐温柔收束。",
    ),
)


def cli(*args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(CLI), "--json", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(proc.stdout[-1000:]) from exc
    if proc.returncode or not payload.get("ok"):
        raise RuntimeError(str(payload.get("error", payload)))
    return payload["data"]


def wait_result(project: str, task_type: str, job_id: str) -> dict:
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        result = cli(
            "--project", project, "task", "result", "--task-type", task_type, "--job-id", job_id
        )
        if result.get("url") or result.get("status") in {"failed", "cancelled", "canceled"}:
            return result
        time.sleep(15)
    raise TimeoutError(f"Timed out waiting for {job_id}; no retry was submitted")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    receipt: dict = {"authorization": {"allow_paid_media": True, "max_paid_starts": 4}, "segments": []}
    plan = "一分钟一镜到底《一个人的精彩一生》\n\n" + "\n\n".join(
        f"{title}\n{prompt}" for title, prompt in SEGMENTS
    )
    plan_file = OUT / "plan.md"
    plan_file.write_text(plan, encoding="utf-8")
    # The plan node was already written during the preflight attempt; reusing it
    # avoids creating duplicate canvas plans after a request validation failure.
    receipt["plan_node"] = {
        "node_id": "agent-844bfc9ab742-1",
        "canvas_id": CANVAS,
        "revision": 195,
        "label": "一分钟一镜到底｜一个人的精彩一生",
    }
    for index, (title, prompt) in enumerate(SEGMENTS, start=1):
        body = {
            "prompt": prompt,
            "image_urls": [CHARACTER],
            "aspect_ratio": "16:9",
            "resolution": "768p",
            "duration_seconds": 15,
            "generate_audio": True,
            "generate_audio_explicit": True,
            "native_audio_strategy": "native",
            "audio_type": "narration",
            "dialogue_text": "我这一生，走了很远，也终于回到了这里。",
            "model": MODEL,
            "model_id": MODEL,
            "gen_mode": "imageToVideo",
            "canvas_id": CANVAS,
            "node_id": f"lifetime-shot-{index}",
        }
        request_file = OUT / f"segment-{index:02d}-request.json"
        request_file.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        accepted = cli(
            "--project", PROJECT, "api", "post", "/projects/{project}/freezone/video/i2v",
            "--body", "@" + str(request_file),
        )
        segment = {"index": index, "title": title, "accepted": accepted}
        receipt["segments"].append(segment)
        (OUT / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        result = wait_result(PROJECT, str(accepted.get("task_type") or "freezone_video_gen"), str(accepted["job_id"]))
        segment["result"] = result
        (OUT / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        if not result.get("url"):
            raise RuntimeError(f"{title} failed: {result}")

    tracks = [{
        "track_id": "lifetime-video",
        "kind": "video",
        "items": [
            {
                "item_id": f"segment-{i}",
                "source_url": segment["result"]["url"],
                "timeline_start": (i - 1) * 15.0,
                "source_start": 0.0,
                "source_end": 15.0,
                "volume": 1.0,
                "muted": False,
                "speed": 1.0,
            }
            for i, segment in enumerate(receipt["segments"], start=1)
        ],
    }]
    compose = cli(
        "--project", PROJECT, "api", "post", "/projects/{project}/freezone/video/compose",
        "--body", json.dumps({
            "title": "一个人的精彩一生｜一镜到底",
            "canvas_id": CANVAS,
            "resolution": "1080p",
            "fps": 24,
            "keep_original_audio": True,
            "tracks": tracks,
        }, ensure_ascii=False),
    )
    receipt["compose"] = compose
    compose_result = wait_result(
        PROJECT,
        str(compose.get("task_type") or "freezone_video_compose"),
        str(compose["job_id"]),
    )
    receipt["compose_result"] = compose_result
    (OUT / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
