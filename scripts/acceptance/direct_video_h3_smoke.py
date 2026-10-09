"""Verify one real direct-video submission against a configured H3 station.

This drives the same adapter the canvas uses (`NewApiVideoGenerator` with the
registry's own `generator_options`), so a pass means the model picker's wiring,
the transport contract, the payload family, the poll route, and the download
step all worked against the live station.

Paid generation is fail-closed: pass ``--allow-paid-generation`` to authorize
exactly one video request.

Usage:

    .venv\\Scripts\\python.exe scripts\\acceptance\\direct_video_h3_smoke.py `
        --registry-id video-8b01a39b81951012 --allow-paid-generation
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from novelvideo.generators.video.direct_models import (  # noqa: E402
    resolve_direct_video_model,
)
from novelvideo.generators.video_generator import (  # noqa: E402
    NewApiVideoGenerator,
    VideoGenStatus,
)


ARTIFACT_DIR = ROOT / "workspace" / "artifacts" / "direct-video-h3"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _is_mp4(path: Path) -> bool:
    with path.open("rb") as handle:
        header = handle.read(12)
    return len(header) >= 8 and header[4:8] == b"ftyp"


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    model = resolve_direct_video_model(f"direct_{args.registry_id}")
    if model is None:
        return {"ok": False, "error": "model_not_found", "registryId": args.registry_id}

    output_path = ARTIFACT_DIR / f"h3-{_stamp()}-{model.registry_id}.mp4"
    options = model.generator_options({"resolution": args.resolution})
    generator = NewApiVideoGenerator(**options)

    result = await generator.generate(
        image_path=None,
        prompt=args.prompt,
        output_path=str(output_path),
        aspect_ratio=args.aspect_ratio,
        duration=args.duration,
        poll_interval=args.poll_interval,
        max_polls=args.max_polls,
    )

    report: dict[str, Any] = {
        "ok": result.status is VideoGenStatus.DONE,
        "probedAt": _stamp(),
        "registryId": model.registry_id,
        "upstreamModel": model.upstream_model,
        "baseUrl": model.base_url,
        "protocol": generator.protocol,
        "resolution": args.resolution,
        "duration": args.duration,
        "aspectRatio": args.aspect_ratio,
        "status": str(result.status),
        "error": result.error or "",
        "videoUrl": (result.video_url or "").split("?")[0],
    }
    if result.status is VideoGenStatus.DONE and output_path.exists():
        payload = output_path.read_bytes()
        report.update(
            {
                "artifact": str(output_path),
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "isMp4": _is_mp4(output_path),
            }
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-id", required=True)
    parser.add_argument("--prompt", default="A calm blue circle pulsing on white")
    parser.add_argument("--resolution", default="768p")
    parser.add_argument("--duration", type=int, default=4)
    parser.add_argument("--aspect-ratio", default="16:9")
    parser.add_argument("--poll-interval", type=float, default=10.0)
    parser.add_argument("--max-polls", type=int, default=90)
    parser.add_argument("--allow-paid-generation", action="store_true")
    args = parser.parse_args()

    if not args.allow_paid_generation:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "paid_generation_not_authorized",
                    "hint": "re-run with --allow-paid-generation for one real video",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    report = asyncio.run(_run(args))
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_DIR / "latest-smoke.json").write_text(payload, encoding="utf-8")
    print(payload)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
