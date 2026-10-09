"""Run one authorized Gemini image ``generateContent`` acceptance call.

The default path is read-only preflight. A real provider call requires both
``--allow-paid-generation`` and the exact authorization phrase below.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
DATA_ROOT = ROOT / "项目资产"
ARTIFACT_DIR = ROOT / "workspace" / "artifacts" / "t049-gemini-image"
DEFAULT_MODEL_ID = "direct/image-4bc525941b78d1fb"
DEFAULT_PROMPT = (
    "A cinematic close-up of an old film camera on a dusty wooden table "
    "in a darkroom, red side light from the left, subtle film grain."
)
AUTHORIZATION_PHRASE = (
    "批准 T-049：调用一次当前配置的 Gemini 图像直连渠道执行 generateContent；"
    "记录模型、任务、产物、SHA-256、尺寸和费用；禁止自动重试；临时文件用完清理。"
)

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _apply_runtime_env() -> None:
    os.environ.setdefault("NOVELVIDEO_DATA_ROOT", str(DATA_ROOT))
    os.environ.setdefault("NOVELVIDEO_STATE_DIR", str(DATA_ROOT / "state"))
    os.environ.setdefault("NOVELVIDEO_OUTPUT_DIR", str(DATA_ROOT / "output"))
    os.environ.setdefault("NOVELVIDEO_RUNTIME_DIR", str(DATA_ROOT / "runtime"))
    os.environ.setdefault("ST_EDITION", "ce")
    os.environ.setdefault("VILLAGE_CANVAS_REQUIRE_VERIFIED_DIRECT_MODELS", "1")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _redacted_endpoint(base_url: str, upstream_model: str) -> dict[str, str]:
    parsed = urlsplit(base_url)
    return {
        "scheme": parsed.scheme,
        "host": parsed.hostname or "",
        "path": f"/v1beta/models/{upstream_model}:generateContent",
    }


def _runtime_identity() -> dict[str, Any]:
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        head = ""
    pid_file = DATA_ROOT / "logs" / "api.pid"
    try:
        pid = int(pid_file.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        pid = None
    build_id = ""
    try:
        build_id = str(
            json.loads(
                (ROOT / "frontend" / "dist" / "version.json").read_text(
                    encoding="utf-8"
                )
            ).get("buildId")
            or ""
        )
    except (OSError, ValueError, TypeError):
        pass
    return {"git_head": head, "runtime_pid": pid, "runtime_build_id": build_id}


def build_preflight(model_id: str) -> dict[str, Any]:
    _apply_runtime_env()
    from novelvideo.generators.direct_image_models import (
        list_direct_image_models,
        resolve_direct_image_model,
    )

    models = list_direct_image_models()
    target = resolve_direct_image_model(model_id)
    checks = [
        {
            "name": "configured_image_models",
            "ok": bool(models),
            "detail": len(models),
        },
        {
            "name": "target_model",
            "ok": target is not None and target.enabled,
            "detail": model_id,
        },
    ]
    if target is not None:
        checks.extend(
            [
                {
                    "name": "gemini_image_protocol",
                    "ok": target.protocol == "gemini-image",
                    "detail": target.protocol,
                },
                {
                    "name": "runtime_ready",
                    "ok": bool(target.profile.modes),
                    "detail": list(target.profile.modes),
                },
            ]
        )
    failed = [str(item["name"]) for item in checks if item["ok"] is not True]
    detail: dict[str, Any] = {}
    if target is not None:
        detail = {
            "registry_id": target.registry_id,
            "catalog_id": target.catalog_id,
            "upstream_model": target.upstream_model,
            "protocol": target.protocol,
            "endpoint": _redacted_endpoint(target.base_url, target.upstream_model),
            "default": target.is_default,
        }
    return {
        "schema": "t049_gemini_image_live_preflight.v1",
        "generatedAt": _stamp(),
        "ok": not failed,
        "readOnly": True,
        "paidGenerationAuthorized": False,
        "providerCallsStarted": False,
        "runtime": _runtime_identity(),
        "authorizationPhrase": AUTHORIZATION_PHRASE,
        "model": detail,
        "checks": checks,
        "failedChecks": failed,
        "blockingReasons": (
            [f"preflight_failed:{name}" for name in failed]
            or ["t049_paid_authorization_required"]
        ),
    }


def _image_evidence(path: Path) -> dict[str, Any]:
    from PIL import Image

    with Image.open(path) as image:
        return {
            "filename": path.name,
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
        }


async def _execute_once(
    *,
    model_id: str,
    prompt: str,
    output_path: Path,
) -> dict[str, Any]:
    _apply_runtime_env()
    from novelvideo.generators import direct_image_models as direct_images
    from novelvideo.generators.direct_image_models import (
        generate_direct_image,
        resolve_direct_image_model,
    )

    model = resolve_direct_image_model(model_id)
    if model is None or not model.enabled:
        raise RuntimeError(f"enabled direct image model not found: {model_id}")
    if model.protocol != "gemini-image":
        raise RuntimeError(
            f"model {model_id} resolves to {model.protocol}, expected gemini-image"
        )

    os.environ[direct_images.DIRECT_IMAGE_SAFETY_RETRY_ENV] = "0"
    os.environ.pop(direct_images.DIRECT_IMAGE_FALLBACK_ENV, None)
    direct_images.DIRECT_IMAGE_TRANSIENT_MAX_ATTEMPTS = 1

    events: list[dict[str, object]] = []
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = await generate_direct_image(
        model=model,
        prompt=prompt,
        output_path=output_path,
        aspect_ratio="16:9",
        image_size="1K",
        quality=None,
        on_provider_event=lambda event: events.append(dict(event)),
    )
    return {
        "task_type": "image.generateContent",
        "request": {
            "aspect_ratio": "16:9",
            "image_size": "1K",
        },
        "model": {
            "registry_id": model.registry_id,
            "catalog_id": model.catalog_id,
            "upstream_model": model.upstream_model,
            "protocol": model.protocol,
            "endpoint": _redacted_endpoint(model.base_url, model.upstream_model),
        },
        "artifact": _image_evidence(Path(result)),
        "provider_events": events,
        "automatic_retry_enabled": False,
    }


def execute(*, model_id: str, prompt: str, output: Path) -> dict[str, Any]:
    started_at = _stamp()
    started_monotonic = time.monotonic()
    report = build_preflight(model_id)
    report.update(
        {
            "readOnly": False,
            "paidGenerationAuthorized": True,
            "startedAt": started_at,
        }
    )
    try:
        execution = asyncio.run(
            _execute_once(model_id=model_id, prompt=prompt, output_path=output)
        )
        report.update(
            {
                "ok": True,
                "providerCallsStarted": True,
                "completedAt": _stamp(),
                "elapsed_ms": round(
                    (time.monotonic() - started_monotonic) * 1000,
                    3,
                ),
                "execution": execution,
                "blockingReasons": [],
            }
        )
    except Exception as exc:
        report.update(
            {
                "ok": False,
                "providerCallsStarted": True,
                "completedAt": _stamp(),
                "elapsed_ms": round(
                    (time.monotonic() - started_monotonic) * 1000,
                    3,
                ),
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc)[:2000],
                },
                "blockingReasons": ["t049_live_execution_failed"],
            }
        )
    return report


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--authorization", default="")
    parser.add_argument("--allow-paid-generation", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    stamp = _stamp()
    evidence_path = args.evidence or ARTIFACT_DIR / f"live-{stamp}.json"
    image_path = args.output or ARTIFACT_DIR / f"gemini-{stamp}.png"

    if args.allow_paid_generation:
        if str(args.authorization or "").strip() != AUTHORIZATION_PHRASE:
            report = build_preflight(str(args.model_id))
            report["blockingReasons"] = ["t049_authorization_phrase_mismatch"]
            report["authorizationAccepted"] = False
        else:
            report = execute(
                model_id=str(args.model_id),
                prompt=str(args.prompt),
                output=image_path,
            )
            report["authorizationAccepted"] = True
    else:
        report = build_preflight(str(args.model_id))
        report["authorizationAccepted"] = False

    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
