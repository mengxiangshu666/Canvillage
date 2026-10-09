"""Credential-free facts about the exact arguments passed to video execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

SCHEMA = "video_generation_request.v1"
SETTINGS = (
    "backend", "gen_mode", "aspect_ratio", "resolution", "duration_seconds",
    "generate_audio", "requested_generate_audio", "generate_audio_explicit",
    "audio_type", "native_audio_strategy", "human_review", "scene_optimize",
)
INPUT_FIELDS = {"slot", "type", "role", "source_sha256", "content_sha256"}


def video_execution_arguments(payload: dict[str, Any]) -> dict[str, Any]:
    """Compile the same execution values for queue preflight and the runner."""
    parameters = dict(payload["parameters"]) if isinstance(payload.get("parameters"), dict) else {}
    if payload.get("size"):
        parameters.setdefault("size", payload["size"])
    mapping = payload.get("provider_mapping") or payload.get("providerMapping")
    mapping = dict(mapping) if isinstance(mapping, dict) else {}
    if payload.get("size_field") or payload.get("sizeField"):
        mapping.setdefault("size", payload.get("size_field") or payload.get("sizeField"))
    return dict(
        prompt=str(payload.get("prompt") or ""),
        reference_items=payload.get("reference_items") or None,
        aspect_ratio=str(payload.get("aspect_ratio") or "16:9"),
        resolution=str(payload.get("resolution") or "720p"),
        duration_seconds=int(payload.get("duration_seconds") or 5),
        generate_audio=bool(payload.get("generate_audio")),
        requested_generate_audio=bool(payload["requested_generate_audio"]) if payload.get("requested_generate_audio") is not None else None,
        dialogue_text=str(payload.get("dialogue_text") or ""),
        spoken_dialogue=list(payload["spoken_dialogue"]) if isinstance(payload.get("spoken_dialogue"), (list, tuple)) else None,
        audio_type=str(payload.get("audio_type") or ""),
        speaker=str(payload.get("speaker") or ""),
        native_audio_strategy=str(payload.get("native_audio_strategy") or ""),
        audio_asset_ref=str(payload.get("audio_asset_ref") or ""),
        generate_audio_explicit=bool(payload["generate_audio_explicit"]) if payload.get("generate_audio_explicit") is not None else None,
        human_review=bool(payload.get("human_review")),
        scene_optimize=str(payload.get("scene_optimize") or ""),
        backend=str(payload.get("backend") or ""),
        last_frame_path=payload.get("last_frame_path"),
        audio_setting=payload.get("audio_setting") or None,
        gen_mode=str(payload.get("gen_mode") or "") or None,
        parameters=parameters or None,
        provider_mapping=mapping or None,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _input(path: object, *, slot: str, media_type: str, role: str) -> dict[str, str]:
    source = str(path or "")
    content = ""
    try:
        with Path(source).open("rb") as handle:
            content = hashlib.file_digest(handle, "sha256").hexdigest()
    except (OSError, ValueError):
        pass
    return {"slot": slot, "type": media_type, "role": role,
            "source_sha256": _digest(source), "content_sha256": content}


def build_video_generation_request(arguments: dict[str, Any]) -> dict[str, Any]:
    settings = {key: (
        "" if arguments.get(key) is None else
        str(arguments[key]).lower() if isinstance(arguments[key], bool) else str(arguments[key])
    ) for key in SETTINGS}
    inputs = [_input(item.get("path") or item.get("url"), slot="reference",
                     media_type=str(item.get("type") or "image"), role=str(item.get("role") or ""))
              for item in arguments.get("reference_items") or []]
    if arguments.get("last_frame_path"):
        inputs.append(_input(arguments["last_frame_path"], slot="last_frame", media_type="image", role="last_frame"))
    request = {"schema": SCHEMA, "settings": settings, "inputs": inputs,
               "details_sha256": _digest({key: arguments.get(key) for key in (
                   "prompt", "parameters", "provider_mapping", "dialogue_text", "spoken_dialogue",
                   "speaker", "audio_asset_ref", "audio_setting",
               )})}
    return {**request, "request_sha256": _digest(request)}


def video_generation_request(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != {
        "schema", "settings", "inputs", "details_sha256", "request_sha256",
    } or value.get("schema") != SCHEMA:
        return None
    settings, inputs = value.get("settings"), value.get("inputs")
    if not isinstance(settings, dict) or set(settings) != set(SETTINGS) or any(
        not isinstance(item, str) for item in settings.values()
    ) or not isinstance(inputs, list):
        return None
    for item in inputs:
        if not isinstance(item, dict) or set(item) != INPUT_FIELDS or any(
            not isinstance(field, str) for field in item.values()
        ) or item["slot"] not in {"reference", "last_frame"}:
            return None
        if not re.fullmatch(r"[0-9a-f]{64}", item["source_sha256"]) or (
            item["content_sha256"] and not re.fullmatch(r"[0-9a-f]{64}", item["content_sha256"])
        ):
            return None
    if not re.fullmatch(r"[0-9a-f]{64}", str(value.get("details_sha256") or "")):
        return None
    request = {key: value[key] for key in ("schema", "settings", "inputs", "details_sha256")}
    try:
        digest = _digest(request)
    except (TypeError, ValueError, UnicodeError):
        return None
    if value.get("request_sha256") != digest:
        return None
    return json.loads(json.dumps(value))


def video_generation_request_matches(value: object, expected: object) -> bool:
    request = video_generation_request(value)
    return request is not None and request == video_generation_request(expected)
