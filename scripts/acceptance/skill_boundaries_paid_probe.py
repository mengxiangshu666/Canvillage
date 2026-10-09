"""One explicitly authorized paid sample through the product CLI, never auto-retry."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "src/novelvideo/canvas_cli.py"


def call(*args: str) -> dict:
    process = subprocess.run(
        [sys.executable, str(CLI), "--json", *args],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    payload = json.loads(process.stdout)
    if process.returncode or not payload.get("ok"):
        raise RuntimeError(str(payload.get("error", "CLI failed")))
    return payload["data"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-paid", action="store_true")
    args = parser.parse_args()
    if not args.allow_paid:
        parser.error("--allow-paid requires explicit human authorization")
    out = ROOT / "项目资产" / "skill_boundary_probe" / uuid.uuid4().hex[:12]
    out.mkdir(parents=True)
    receipt: dict = {"max_paid_starts": 1, "cost": "unknown", "output_dir": str(out)}
    def save() -> None:
        (out / "receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    try:
        rows = call("model", "capabilities", "--kind", "video")
        model = next(row for row in rows if row.get("isDefault") and row.get("runtimeReady"))
        if not (model.get("minDuration", 999) <= 4 <= model.get("maxDuration", 0)):
            raise RuntimeError("Default model does not support this 4-second probe")
        if "768p" not in model.get("resolutionOptions", []):
            raise RuntimeError("Default model does not support 768p")
        receipt["model"] = {k: model.get(k) for k in (
            "id", "label", "minDuration", "maxDuration", "resolutionOptions", "priceHint",
        )}
        project = call("api", "post", "/projects", "--body", json.dumps({
            "name": "zz_codex_skill_boundary_" + out.name,
        }))
        project_id = str(project.get("project_id") or project.get("id") or "")
        if not project_id:
            raise RuntimeError("No isolated project id")
        receipt["project_id"] = project_id
        prompt = (
            "4秒，16:9，写实。单个成年虚构男性，短黑发，灰色衬衫，"
            "白天安静的室内窗边，固定中近景，无其他人。"
            "0到0.5秒他静静看向窗外，嘴唇闭合，无人说话。"
            "0.5秒他轻声说一句台词，1.8秒前说完。"
            "1.8到4秒他闭上嘴，轻轻呼吸，目光停留在窗外，无人说话，只有轻微室内环境声。"
            "台词仅说一次，不增添话语，不重复，不要字幕，不要BGM。"
        )
        model_id = str(model.get("modelId") or model.get("model_id") or "")
        if not model_id:
            # The capability row id is an internal catalog key, not the wire model.
            model_id = "direct_video-" + str(model["id"]).removeprefix("video-")
        body = {
            "prompt": prompt, "dialogue_text": "我回来了。", "speaker": "窗边男子",
            "audio_type": "dialogue", "native_audio_strategy": "native",
            "generate_audio": True, "generate_audio_explicit": True,
            "duration_seconds": 4, "aspect_ratio": "16:9", "resolution": "768p",
            "model": model_id, "model_id": model_id, "gen_mode": "textToVideo",
        }
        receipt["request"] = body
        request_file = out / "request.json"
        request_file.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        save()
        # Exactly one submit; failures and unknown states only permit read-back.
        accepted = call("--project", project_id, "api", "post",
                        "/projects/{project}/freezone/video/gen", "--body", "@" + str(request_file))
        receipt["accepted"] = accepted
        save()
        print(json.dumps({"project_id": project_id, "accepted": accepted, "receipt": str(out / "receipt.json")}), flush=True)
        task_type = str(accepted.get("task_type") or "freezone_video_gen")
        job_id = str(accepted.get("job_id") or "")
        if not job_id:
            raise RuntimeError("No job id; inspect receipt, never resubmit")
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline:
            result = call("--project", project_id, "task", "result", "--task-type", task_type, "--job-id", job_id)
            receipt["result"] = result
            save()
            status = str(result.get("status") or "")
            print(json.dumps({"status": status, "job_id": job_id}), flush=True)
            if result.get("url") or status in {"completed", "failed", "cancelled", "canceled"}:
                break
            time.sleep(15)
        else:
            raise TimeoutError("Read-back deadline; no resubmission")
    except Exception as exc:
        receipt["error"] = str(exc)
        save()
        raise


if __name__ == "__main__":
    main()
