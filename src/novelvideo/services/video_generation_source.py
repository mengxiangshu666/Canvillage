"""Bind a video artifact to the authored request, before provider compilation."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from novelvideo.services.video_request_contract import video_execution_prompt_key
from novelvideo.services.video_generation_request import video_generation_request, video_generation_request_matches

SCHEMA = "video_generation_source.v1"
logger = logging.getLogger(__name__)


def video_prompt_digest(prompt: object) -> str:
    key = video_execution_prompt_key(prompt)
    return hashlib.sha256(key.encode("utf-8")).hexdigest() if key else ""


def video_generation_source(
    value: object, *, output_url: str, job_id: str | None = None
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if (
        value.get("schema") != SCHEMA
        or not isinstance(output_url, str) or not output_url.strip()
        or value.get("output_url") != output_url
        or value.get("task_type") != "freezone_video_gen"
        or not isinstance(value.get("job_id"), str)
        or not value["job_id"].strip()
        or (job_id is not None and value["job_id"] != job_id)
        or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("execution_prompt_sha256") or ""))
    ):
        return None
    source = {key: value[key] for key in (
        "schema", "output_url", "task_type", "job_id", "execution_prompt_sha256"
    )}
    if "generation_request" in value:
        request = video_generation_request(value["generation_request"])
        if request is None:
            return None
        source["generation_request"] = request
    if "provider_model" in value:
        if not isinstance(value["provider_model"], str) or not value["provider_model"].strip():
            return None
        source["provider_model"] = value["provider_model"]
    return source


def persist_video_generation_source(
    output_path: Path, *, output_url: str, job_id: str, prompt_digest: str,
    generation_request: object = None,
    provider_model: object = None,
) -> dict[str, Any] | None:
    source = video_generation_source({
        "schema": SCHEMA, "output_url": output_url, "task_type": "freezone_video_gen",
        "job_id": job_id, "execution_prompt_sha256": prompt_digest,
        **({"generation_request": generation_request} if generation_request is not None else {}),
        **({"provider_model": provider_model} if provider_model else {}),
    }, output_url=output_url, job_id=job_id)
    path = output_path.with_suffix(".generation.json")
    try:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(source), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        logger.warning("video source receipt write failed for %s", job_id, exc_info=True)
    return source


def video_generation_source_matches(
    value: object, *, output_url: str, prompt: str, expected_request: object = ...,
) -> bool:
    source = video_generation_source(value, output_url=output_url)
    if not source:
        return False
    if prompt or expected_request is ...:
        if source["execution_prompt_sha256"] != video_prompt_digest(prompt):
            return False
    return expected_request is ... or video_generation_request_matches(source.get("generation_request"), expected_request)


def read_video_generation_source(
    output_path: Path, *, output_url: str, job_id: str
) -> dict[str, Any] | None:
    try:
        value: Any = json.loads(output_path.with_suffix(".generation.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    # Static URL migration changes presentation, while the sidecar stays with the artifact.
    original_url = value.get("output_url") if isinstance(value, dict) else ""
    source = video_generation_source(value, output_url=original_url, job_id=job_id)
    return {**source, "output_url": output_url} if source else None
