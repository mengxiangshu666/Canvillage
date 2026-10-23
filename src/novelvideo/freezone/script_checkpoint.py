"""Durable checkpoints for the freezone story-script job.

The model call itself is one structured response, so this cannot checkpoint
inside that call. It does preserve every verified stage after the call: raw
bound output, contract-repaired output, and the frame/source inputs. A retry
with the same source, prompt and model reuses the paid result instead of
silently generating again; any input change invalidates the checkpoint.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "freezone-story-script-checkpoint.v1"
CHECKPOINT_VERSION = "freezone-story-script-stages.v1"


def _rules_digest() -> str:
    try:
        from novelvideo.freezone.script_contract import RULE_CATALOG

        return hashlib.sha256(
            json.dumps(RULE_CATALOG, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
    except Exception:
        return "rules-unavailable"


def checkpoint_fingerprint(
    *,
    source_text: str,
    prompt: str,
    model: str | None,
    video_model: str | None,
    video_path: str = "",
    character_refs: object = None,
    job_id: str = "",
) -> str:
    payload = {
        "version": CHECKPOINT_VERSION,
        "rules": _rules_digest(),
        # A deliberate new generation gets a new job id and must not silently
        # reuse an old answer. Only retries/recovery of the same job reuse it.
        "job_id": job_id or "",
        "source_text": source_text or "",
        "prompt": prompt or "",
        "model": model or "",
        "video_model": video_model or "",
        "video_path": video_path or "",
        "character_refs": character_refs or [],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode(
            "utf-8"
        )
    ).hexdigest()[:40]


def checkpoint_path(project_dir: Path, fingerprint: str) -> Path:
    return (
        project_dir
        / ".village_canvas"
        / "checkpoints"
        / "freezone_story_script"
        / f"{fingerprint}.json"
    )


def load_story_script_checkpoint(
    project_dir: Path, fingerprint: str
) -> dict[str, Any] | None:
    path = checkpoint_path(project_dir, fingerprint)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        return None
    if (
        payload.get("fingerprint") != fingerprint
        or payload.get("version") != CHECKPOINT_VERSION
    ):
        return None
    data = payload.get("data")
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("rows"), list)
        or not data["rows"]
    ):
        return None
    return payload


def save_story_script_checkpoint(
    project_dir: Path,
    fingerprint: str,
    *,
    stage: str,
    data: dict[str, Any],
    frame_urls: list[str] | None = None,
    frame_anchors: dict[str, Any] | None = None,
    contract_report: dict[str, Any] | None = None,
) -> Path | None:
    path = checkpoint_path(project_dir, fingerprint)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": SCHEMA,
            "version": CHECKPOINT_VERSION,
            "fingerprint": fingerprint,
            "stage": stage,
            "saved_at": datetime.now(UTC).isoformat(),
            "data": data,
            "frame_urls": list(frame_urls or []),
            "frame_anchors": frame_anchors,
            "contract_report": contract_report,
        }
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)
        return path
    except OSError:
        # A read-only or full disk must not turn a successful paid generation
        # into a failed job; the caller can still publish the result.
        return None


def discard_story_script_checkpoint(project_dir: Path, fingerprint: str) -> None:
    try:
        checkpoint_path(project_dir, fingerprint).unlink(missing_ok=True)
    except OSError:
        pass
