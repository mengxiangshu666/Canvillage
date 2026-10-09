"""Run the real execution adapter against a local deterministic HTTP upstream.

This harness deliberately keeps the browser, FastAPI app, Hermes ACP worker,
plugin, ActionRouter, WorkflowRun, generators and task backend on their normal
product paths. Only the upstream LLM is replaced by a local OpenAI-compatible
HTTP server. The browser starts a draft request, so the first paid-media gate
must stop the Run before any media endpoint is called.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default as email_policy
import hashlib
import io
import importlib.util
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "workspace"
SOURCE_UI_SMOKE = WORKSPACE / "ui-smoke-t096"
TARGET_UI_SMOKE = WORKSPACE / "ui-smoke-t112"
SOURCE_STATE = SOURCE_UI_SMOKE / "state"
TARGET_STATE = TARGET_UI_SMOKE / "state"
PROJECT_STATE = TARGET_STATE / "local" / "ui_smoke_t091"

PROJECT_ID = "01M2TNN3XXK2D5CRXDBKTTFM4W"
CANVAS_ID = "ui_smoke_canvas"
SCRIPT_NODE_ID = "script-a"
REQUEST_TEXT = "从当前脚本节点继续到最终成片"
API_PORT = int(os.environ.get("T112_API_PORT", "8793"))
VITE_PORT = int(os.environ.get("T112_VITE_PORT", "5193"))
API_BASE = f"http://127.0.0.1:{API_PORT}/api/v1"
UI_BASE = f"http://127.0.0.1:{VITE_PORT}"
PLAYWRIGHT_NODE_MODULES = WORKSPACE / "ui-smoke-t091" / "browser-runner" / "node_modules"
FFMPEG = ROOT / "runtime" / "ffmpeg" / "ffmpeg.exe"
FFPROBE = ROOT / "runtime" / "ffmpeg" / "ffprobe.exe"
HERMES_CLI = ROOT / "runtime" / "hermes" / "hermes.bat"
AUDIO_SPEECH_PATH = "/v1/audio/speech"
WORKFLOW_DATABASE = PROJECT_STATE / "workflow_runs.db"
EVIDENCE_PATH = TARGET_UI_SMOKE / "t112-browser-evidence.json"
EVIDENCE_ARCHIVE_ROOT = (
    ROOT / "workspace" / "artifacts" / "t112-real-execution-adapter"
)
PREFLIGHT_SCHEMA = "t112_real_execution_adapter_preflight.v1"
STABLE_EVIDENCE_SCHEMA = "t112_stable_evidence_manifest.v1"
DELIVERY_QC_REQUIRED_CHECKS = (
    "container_allowed",
    "video_stream_present",
    "audio_stream_present",
    "dimensions",
    "frame_rate",
    "duration",
    "black_frames",
    "freeze_frames",
    "av_sync",
    "audio_activity",
    "loudness",
    "true_peak",
    "subtitle_stream",
    "color_space",
    "bitrate",
    "file_readback",
    "sha256",
)
VIDEO_DURATION_DEFAULT_SECONDS = 5
VIDEO_DURATION_MIN_SECONDS = 1
VIDEO_DURATION_MAX_SECONDS = 30
SHOT_COUNT_DEFAULT = 1
SHOT_COUNT_MIN = 1
SHOT_COUNT_MAX = 12
_SHOT_FRAME_COLORS = (
    "0xE53935",
    "0x43A047",
    "0x1E88E5",
    "0xFDD835",
    "0xD81B60",
    "0x00ACC1",
    "0xFB8C00",
    "0x8E24AA",
    "0x7CB342",
    "0xEC407A",
    "0x6D4C41",
    "0xF5F5F5",
)


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load acceptance module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


LEGACY_HARNESS_DIR = ROOT / "scripts" / "acceptance"
LEGACY_HARNESS_RETIRED_BY = "T-142"
_LEGACY_HARNESS_MODULES: dict[str, Any] = {}


def _legacy_harness_module(module_stem: str) -> Any:
    """Resolve a retired T-100/T-097 Hermes harness only when one is needed.

    T-142 retired the Hermes acceptance harness together with
    ``novelvideo.chat.hermes_pool``: ``t100_canvas_script_reuse_ui.py`` and
    ``t097_media_authorization_ui.py`` are gone from the repository, while the
    native ``t112_real_execution_adapter.cjs`` chain drives the browser by
    itself. Importing this module must therefore no longer require them, so the
    retired harness is loaded lazily and reports an explicit retirement error
    instead of raising ``FileNotFoundError`` during import.
    """

    cached = _LEGACY_HARNESS_MODULES.get(module_stem)
    if cached is not None:
        return cached
    path = LEGACY_HARNESS_DIR / f"{module_stem}.py"
    if not path.is_file():
        raise RuntimeError(
            "T-112 Hermes acceptance harness was retired by "
            f"{LEGACY_HARNESS_RETIRED_BY}: {path} no longer exists; the native "
            "t112_real_execution_adapter.cjs chain replaced it"
        )
    module = _load_module(f"{module_stem}_for_t112", path)
    _LEGACY_HARNESS_MODULES[module_stem] = module
    return module


def _t100_harness() -> Any:
    return _legacy_harness_module("t100_canvas_script_reuse_ui")


def _t097_harness() -> Any:
    harness = getattr(_t100_harness(), "T097", None)
    if harness is None:
        raise RuntimeError(
            "T-112 Hermes acceptance harness was retired by "
            f"{LEGACY_HARNESS_RETIRED_BY}: the t100 harness no longer exposes T097"
        )
    return harness


def _wait_url(url: str, *, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            from urllib.request import urlopen

            with urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except Exception as exc:  # noqa: BLE001 - startup polling
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"timed out waiting for {url}: {last_error}")


def _start_vite(log_path: Path) -> subprocess.Popen[bytes]:
    log = log_path.open("wb")
    env = os.environ.copy()
    env["VITE_API_URL"] = API_BASE.removesuffix("/api/v1")
    process = subprocess.Popen(
        [
            str(ROOT / "frontend" / "node_modules" / ".bin" / "vite.cmd"),
            "--host",
            "127.0.0.1",
            "--port",
            str(VITE_PORT),
        ],
        cwd=ROOT / "frontend",
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    process._t112_log = log  # type: ignore[attr-defined]
    return process


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def __getattr__(name: str) -> Any:
    """Keep ``T100``/``T097`` addressable for callers that still configure them."""

    if name == "T100":
        return _t100_harness()
    if name == "T097":
        return _t097_harness()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _path_check(name: str, path: Path, *, directory: bool = False) -> dict[str, Any]:
    exists = path.is_dir() if directory else path.is_file()
    return {
        "name": name,
        "ok": bool(exists),
        "path": str(path),
        "expected": "directory" if directory else "file",
    }


def _normalize_video_duration(value: object) -> int:
    """Coerce a requested video duration into the deterministic upstream range."""

    if isinstance(value, bool) or value is None:
        return VIDEO_DURATION_DEFAULT_SECONDS
    try:
        seconds = int(round(float(value)))
    except (TypeError, ValueError):
        return VIDEO_DURATION_DEFAULT_SECONDS
    return max(
        VIDEO_DURATION_MIN_SECONDS,
        min(VIDEO_DURATION_MAX_SECONDS, seconds),
    )


def _normalize_shot_count(value: object) -> int:
    """Coerce a requested shot count into the isolated final-film range."""

    if isinstance(value, bool) or value is None:
        return SHOT_COUNT_DEFAULT
    try:
        count = int(value)
    except (TypeError, ValueError):
        return SHOT_COUNT_DEFAULT
    return max(SHOT_COUNT_MIN, min(SHOT_COUNT_MAX, count))


def _shot_frame_color(shot_index: int) -> str:
    normalized = max(1, int(shot_index))
    return _SHOT_FRAME_COLORS[(normalized - 1) % len(_SHOT_FRAME_COLORS)]


def _deterministic_frame_filter() -> str:
    """Add visible texture so deterministic motion remains machine-detectable."""

    return (
        "drawgrid=w=16:h=16:t=2:c=white@0.85,"
        "drawbox=x=65:y=30:w=30:h=30:c=black@0.9:t=fill"
    )


def _deterministic_motion_filter(frame_count: int) -> str:
    """Return a bounded sawtooth zoom whose final frame returns near the source."""

    frames = max(1, int(frame_count))
    return (
        "scale=800:450,"
        f"zoompan=z='1+0.02*mod(on+1,24)/24':d={frames}:"
        "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        "s=160x90:fps=24,format=yuv420p"
    )


def _video_reference_images(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Return image references from the OpenAI/MiniMax video wire contract."""

    references: list[dict[str, str]] = []
    content = payload.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            media_type = str(item.get("type") or "").strip().lower()
            if not media_type.startswith("image"):
                continue
            holder = item.get("image_url")
            url = ""
            if isinstance(holder, dict):
                url = _first_text(holder.get("url") or holder.get("data"))
            else:
                url = _first_text(holder)
            if not url:
                url = _first_text(item.get("url"))
            if url:
                references.append(
                    {
                        "role": str(item.get("role") or "").strip().lower(),
                        "url": url,
                    }
                )
    media_inputs = payload.get("media_inputs")
    if isinstance(media_inputs, list):
        for item in media_inputs:
            if not isinstance(item, dict) or str(item.get("kind") or "") != "image":
                continue
            url = _first_text(item.get("url"))
            if url:
                references.append(
                    {
                        "role": str(item.get("role") or "").strip().lower(),
                        "url": url,
                    }
                )
    for key in (
        "images",
        "image_url",
        "image",
        "input_image",
        "first_frame",
        "first_frame_url",
    ):
        holder = payload.get(key)
        values = holder if isinstance(holder, list) else [holder]
        for value in values:
            if isinstance(value, dict):
                url = _first_text(value.get("url") or value.get("data"))
            else:
                url = _first_text(value)
            if url:
                references.append({"role": key, "url": url})
    return references


def _data_url_image_bytes(url: str) -> bytes:
    header, separator, encoded = str(url or "").partition(",")
    if (
        not separator
        or not header.lower().startswith("data:image/")
        or ";base64" not in header.lower()
    ):
        return b""
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        return b""


def _shot_frame_rgb(shot_index: int) -> tuple[int, int, int]:
    value = _shot_frame_color(shot_index).removeprefix("0x")
    number = int(value, 16)
    return (number >> 16) & 0xFF, (number >> 8) & 0xFF, number & 0xFF


def _image_mean_rgb(image_bytes: bytes) -> tuple[int, int, int] | None:
    from PIL import Image, ImageStat

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            stat = ImageStat.Stat(image.convert("RGB"))
    except (OSError, ValueError):
        return None
    if not stat.mean:
        return None
    return tuple(int(round(value)) for value in stat.mean[:3])


def _script_rows_for_shots(
    shot_count: int = SHOT_COUNT_DEFAULT,
    duration_seconds: int = VIDEO_DURATION_DEFAULT_SECONDS,
) -> list[dict[str, Any]]:
    """Return distinct reusable script rows without depending on chat history."""

    normalized_count = _normalize_shot_count(shot_count)
    normalized_duration = _normalize_video_duration(duration_seconds)
    shot_types = ("中景", "特写", "全景", "近景", "过肩", "俯拍")
    emotions = ("克制而怀念", "迟疑", "下定决心", "释然", "警觉", "温柔")
    rows: list[dict[str, Any]] = []
    for index in range(normalized_count):
        row = {
            "shot_no": 1,
            "duration": 2,
            "visual_description": "旧照相馆暗房里，阿木站在红灯下举起相机。",
            "character_1": "阿木",
            "character_description_1": "[阿木: 短黑发，深蓝外套，手里握着相机。]",
            "scene_tags": "旧照相馆、暗房、红灯",
            "prop_tags": "相机、红灯",
            "shot": "中景",
            "character_action": "举起相机",
            "emotion": "克制而怀念",
            "lighting_mood": "红色侧光",
            "sound": "雨声、快门声",
            "dialogue": "无",
        }
        shot_no = index + 1
        shot_type = shot_types[index % len(shot_types)]
        emotion = emotions[index % len(emotions)]
        dialogue_text = (
            os.environ.get("T112_DIALOGUE_TEXT", "").strip()
            if shot_no == 1
            else ""
        )
        dialogue_segment = (
            f"[对话台词] {dialogue_text}。 + "
            if dialogue_text
            else "[对话台词] 无对白。 + "
        )
        row.update(
            {
                "shot_no": shot_no,
                "duration": normalized_duration,
                "shot": shot_type,
                "emotion": emotion,
                "visual_description": (
                    f"旧照相馆暗房里，阿木在第 {shot_no} 个镜头中继续冲洗胶片。"
                ),
                "character_action": (
                    "举起相机" if shot_no == 1 else f"完成第 {shot_no} 个连续动作"
                ),
                "dialogue": dialogue_text or "无",
                "shot_prompt": (
                    f"[画面构图] {shot_type}，第 {shot_no} 镜头，人物略偏左，"
                    "右侧留出暗房空间。 + "
                    "[角色卡] [阿木: 短黑发，深蓝外套，手里握着相机。] + "
                    "[主体/人物空间] 阿木站在暗房中央，相机贴近胸前。 + "
                    f"[微表情] {emotion}。 + "
                    "[场景环境] 旧照相馆暗房，木架上挂着未冲洗的胶片。 + "
                    "[光影几何] 红灯从左侧切过面部，背景沉入阴影。 + "
                    "[视觉风格] 写实电影感，低饱和红色调。 + "
                    "[技术参数] 35mm 胶片质感，浅景深。"
                ),
                "video_motion_prompt": (
                    f"[运镜轨迹] 第 {shot_no} 镜头缓慢{'推进' if shot_no % 2 else '横移'}。 + "
                    f"[主体动作] 完成第 {shot_no} 个连续动作。 + "
                    "[环境动态] 红灯轻微闪烁，雨声从窗外传入。 + "
                    "[音效氛围] 雨声与快门声。 + "
                    f"{dialogue_segment}"
                    f"[时长] [时长：{normalized_duration}s]"
                ),
            }
        )
        rows.append(row)
    return rows


def _requested_video_duration(payload: dict[str, Any]) -> int | None:
    """Read the duration from every request shape used by the local substitute.

    OpenAI-compatible video endpoints use top-level ``seconds`` while other
    relay contracts use ``duration`` or ``duration_seconds``. Legacy relays may
    also nest the value under ``metadata``. The acceptance server must follow
    those real wire shapes instead of silently falling back to its own default.
    """

    containers = [payload]
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        containers.append(metadata)
    for container in containers:
        for key in ("duration", "duration_seconds", "durationSeconds", "seconds"):
            if key not in container:
                continue
            value = container[key]
            if value is None or value == "":
                continue
            return _normalize_video_duration(value)
    return None


def _first_text(value: Any) -> str:
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value).strip()
    return ""


def _nested_text(payload: dict[str, Any], *keys: str) -> str:
    containers = [payload]
    for name in ("extra_fields", "extraFields", "metadata", "parameters"):
        nested = payload.get(name)
        if isinstance(nested, dict):
            containers.append(nested)
    for container in containers:
        for key in keys:
            value = _first_text(container.get(key))
            if value:
                return value
    return ""


def _requested_image_contract(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "size": _nested_text(payload, "size", "image_size", "imageSize"),
            "aspect_ratio": _nested_text(
                payload,
                "aspect_ratio",
                "aspectRatio",
                "image_aspect_ratio",
            ),
            "image_size": _nested_text(payload, "image_size", "imageSize"),
            "quality": _nested_text(payload, "quality"),
        }.items()
        if value
    }


def _request_payload(raw: bytes, content_type: str) -> dict[str, Any]:
    """Decode JSON, urlencoded, or multipart provider requests."""

    normalized = str(content_type or "").split(";", 1)[0].strip().lower()
    if normalized == "application/json":
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    if normalized == "application/x-www-form-urlencoded":
        try:
            fields = parse_qs(
                raw.decode("utf-8"),
                keep_blank_values=True,
                strict_parsing=False,
            )
        except UnicodeDecodeError:
            return {}
        return {key: values[0] if values else "" for key, values in fields.items()}
    if normalized == "multipart/form-data":
        message = BytesParser(policy=email_policy).parsebytes(
            b"Content-Type: "
            + str(content_type).encode("latin-1", errors="replace")
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + raw
        )
        parsed: dict[str, Any] = {}
        if not message.is_multipart():
            return parsed
        for part in message.iter_parts():
            name = str(
                part.get_param("name", header="content-disposition") or ""
            ).strip()
            if not name or part.get_filename():
                continue
            decoded = part.get_payload(decode=True) or b""
            charset = str(part.get_content_charset() or "utf-8")
            try:
                parsed[name] = decoded.decode(charset, errors="replace")
            except LookupError:
                parsed[name] = decoded.decode("utf-8", errors="replace")
        return parsed
    return {}


def _payload_shape(value: Any, *, depth: int = 0) -> Any:
    """Describe a provider payload without persisting prompts or base64 media."""

    if depth >= 3:
        return type(value).__name__
    if isinstance(value, dict):
        return {
            str(key): _payload_shape(item, depth=depth + 1)
            for key, item in list(value.items())[:40]
        }
    if isinstance(value, list):
        shape = [_payload_shape(value[0], depth=depth + 1)] if value else []
        if len(value) > 1:
            shape.append(f"... {len(value) - 1} more")
        return shape
    if isinstance(value, str):
        if value.startswith("data:"):
            return f"data-url[{len(value)}]"
        return f"str[{len(value)}]"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return type(value).__name__


def _requested_storyboard_shot_number(
    payload: dict[str, Any],
    *,
    shot_count: int,
) -> int:
    """Read the shot number from a storyboard image prompt.

    Storyboard image requests run concurrently. An HTTP arrival counter is
    therefore not a shot identity and can silently rotate the colored
    acceptance frames. The product already carries the shot number in the
    image prompt, so bind to that fact instead.
    """

    prompts: list[str] = []
    for key in ("prompt", "input", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            prompts.append(value)
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key in ("prompt", "input", "text"):
            value = metadata.get(key)
            if isinstance(value, str):
                prompts.append(value)
    haystack = "\n".join(prompts)
    patterns = (
        r"第\s*(\d+)\s*(?:个)?镜头",
        r"镜头\s*(\d+)",
        r"\bshot\s*(\d+)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, haystack, flags=re.IGNORECASE)
        if match is None:
            continue
        shot_number = int(match.group(1))
        if 1 <= shot_number <= shot_count:
            return shot_number
    return 0


def _requested_video_contract(payload: dict[str, Any]) -> dict[str, Any]:
    content = payload.get("content")
    roles = (
        [
            str(item.get("role") or "").strip()
            for item in content
            if isinstance(item, dict) and str(item.get("role") or "").strip()
        ]
        if isinstance(content, list)
        else []
    )
    return {
        key: value
        for key, value in {
            "resolution": _nested_text(
                payload,
                "resolution",
                "video_resolution",
                "videoResolution",
            ),
            "ratio": _nested_text(payload, "ratio", "aspect_ratio", "aspectRatio"),
            "duration": _requested_video_duration(payload),
            "content_roles": roles,
        }.items()
        if value not in (None, "", [])
    }


def _canvas_agent_request(messages: object) -> dict[str, Any]:
    """Extract the bounded browser envelope without persisting full prompts."""

    if not isinstance(messages, list):
        return {}
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, list):
            text = "\n".join(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict)
            )
        else:
            text = content if isinstance(content, str) else ""
        match = re.search(
            r"\[CANVAS_AGENT_REQUEST_V2\]\s*([\s\S]*?)\s*"
            r"\[/CANVAS_AGENT_REQUEST_V2\]",
            text,
            flags=re.IGNORECASE,
        )
        if match is None:
            continue
        try:
            envelope = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        return envelope if isinstance(envelope, dict) else {}
    return {}


def _canvas_checkpoint(messages: object) -> dict[str, Any]:
    if not isinstance(messages, list):
        return {}
    # A long-lived ACP session can contain older recovery blocks in history.
    # The current prompt is the last user message, so read newest first.
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, list):
            text = "\n".join(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict)
            )
        elif isinstance(content, str):
            text = content
        else:
            text = ""
        matches = list(
            re.finditer(
                r"\[VILLAGE_AGENT_CONTEXT_CHECKPOINT\]\s*([\s\S]*?)\s*"
                r"\[/VILLAGE_AGENT_CONTEXT_CHECKPOINT\]",
                text,
                flags=re.IGNORECASE,
            )
        )
        if not matches:
            continue
        try:
            checkpoint = json.loads(matches[-1].group(1))
        except json.JSONDecodeError:
            continue
        return checkpoint if isinstance(checkpoint, dict) else {}
    return {}


def _current_turn_needs_tool_call(messages: object) -> bool:
    """Return whether the newest user turn is still waiting for a tool call.

    OpenAI-compatible history keeps tool results from earlier user turns.  The
    local deterministic model must not treat that old evidence as completion of
    the current turn.
    """

    if not isinstance(messages, list):
        return True
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        if role == "user":
            return True
        if role == "tool":
            return False
    return True


def _checkpoint_telemetry(checkpoint: dict[str, Any]) -> dict[str, Any]:
    contract = (
        checkpoint.get("recovery_contract")
        if isinstance(checkpoint.get("recovery_contract"), dict)
        else {}
    )
    recovery = (
        contract.get("recovery") if isinstance(contract.get("recovery"), dict) else {}
    )
    return {
        "present": bool(checkpoint),
        "run_id": str(contract.get("workflow_run_id") or ""),
        "action": str(contract.get("action") or ""),
        "allow_new_submission": contract.get("allow_new_submission") is True,
        "recovery_action": str(recovery.get("action") or ""),
        "recovery_step_id": str(recovery.get("step_id") or ""),
        "recovery_scope": str(recovery.get("rerun_scope") or ""),
        "recovery_item_ids": list(recovery.get("item_ids") or [])[:20],
        "requires_paid_media": recovery.get("requires_paid_media") is True,
        "auto_retry_allowed": recovery.get("auto_retry_allowed") is True,
    }


def _indexed_recovery_tool_call(
    agent_request: dict[str, Any],
    checkpoint: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the exact indexed workflow retry for one paid recovery turn."""

    authorization = (
        agent_request.get("task_authorization")
        if isinstance(agent_request.get("task_authorization"), dict)
        else {}
    )
    grant_id = str(authorization.get("grant_id") or "").strip()
    workflow_runtime = (
        agent_request.get("workflow_runtime")
        if isinstance(agent_request.get("workflow_runtime"), dict)
        else {}
    )
    run_id = str(workflow_runtime.get("workflow_run_id") or "").strip()
    contract = (
        checkpoint.get("recovery_contract")
        if isinstance(checkpoint.get("recovery_contract"), dict)
        else {}
    )
    recovery = (
        contract.get("recovery") if isinstance(contract.get("recovery"), dict) else {}
    )
    item_ids = [
        str(item_id).strip()
        for item_id in (recovery.get("item_ids") or [])
        if str(item_id or "").strip()
    ]
    if (
        not grant_id.startswith("pmg_")
        or not run_id
        or str(workflow_runtime.get("next_action") or "").strip()
        != "recover:retry_failed_items:shot_videos"
        or str(contract.get("action") or "").strip() != "inspect_before_action"
        or str(contract.get("workflow_run_id") or "").strip() != run_id
        or str(recovery.get("action") or "").strip() != "retry_failed_items"
        or str(recovery.get("step_id") or "").strip() != "shot_videos"
        or recovery.get("requires_paid_media") is not True
        or recovery.get("auto_retry_allowed") is not False
        or not item_ids
    ):
        return None
    digest = hashlib.sha256(
        json.dumps(
            {
                "run_id": run_id,
                "step_id": "shot_videos",
                "item_ids": item_ids,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]
    return {
        "project_id": PROJECT_ID,
        "canvas_id": CANVAS_ID,
        "action": "invoke",
        "capability_id": "workflow.run.control",
        "arguments": {
            "run_id": run_id,
            "command": "retry",
            "step_id": "shot_videos",
            "retry_scope": "failed_items_only",
            "item_ids": item_ids,
            "idempotency_key": f"t115-recover-{digest}",
            "task_authorization": dict(authorization),
        },
    }


def build_preflight() -> dict[str, Any]:
    """Prove the adapter inputs exist without starting a process."""

    checks = [
        _path_check("source_site", SOURCE_UI_SMOKE, directory=True),
        _path_check("source_state", SOURCE_STATE, directory=True),
        _path_check("bundled_ffmpeg", FFMPEG),
        _path_check("bundled_ffprobe", FFPROBE),
        _path_check("browser_driver", Path(__file__).with_suffix(".cjs")),
        {
            "name": "node_runtime",
            "ok": bool(shutil.which("node")),
            "path": str(shutil.which("node") or ""),
            "expected": "executable",
        },
        {
            "name": "pnpm_runtime",
            "ok": bool(shutil.which("pnpm")),
            "path": str(shutil.which("pnpm") or ""),
            "expected": "executable",
        },
    ]
    failed = [item["name"] for item in checks if item["ok"] is not True]
    return {
        "schema": PREFLIGHT_SCHEMA,
        "ok": not failed,
        "executionAdapterImplemented": True,
        "fullChainImplemented": True,
        "executionAdapterConnected": False,
        "paidProvidersConnected": False,
        "providerCallsStarted": False,
        "hermesHarnessRetiredBy": LEGACY_HARNESS_RETIRED_BY,
        "checks": checks,
        "failedChecks": failed,
    }


def _chat_response(
    *,
    model: str,
    tool_call: bool,
    stream: bool,
    recovery_tool_call: dict[str, Any] | None = None,
    run_mode: str = "draft",
    allow_paid_media: bool = False,
    max_paid_starts: int = 0,
    shot_count: int = SHOT_COUNT_DEFAULT,
    video_duration_seconds: int = VIDEO_DURATION_DEFAULT_SECONDS,
) -> tuple[bytes, str]:
    completion_id = f"chatcmpl-t112-{int(time.time() * 1000)}"
    normalized_shot_count = _normalize_shot_count(shot_count)
    normalized_duration = _normalize_video_duration(video_duration_seconds)
    if tool_call:
        tool_name = "village_canvas_dispatch_action"
        arguments = recovery_tool_call or {
            "project_id": PROJECT_ID,
            "canvas_id": CANVAS_ID,
            "request": REQUEST_TEXT,
            "goal": REQUEST_TEXT,
            "success_criteria": [
                "复用当前脚本节点合同",
                "只创建一个 freezone-final-film Run",
                "未授权时在首个媒体门零 provider 请求停住",
            ],
            "task": {
                "operation": "start_final_film_workflow",
                "interaction_mode": "execute",
                "target_strategy": "reuse_existing",
                "target_node_ids": [SCRIPT_NODE_ID],
                "requires_delivery": True,
                "requires_recovery": True,
                # Full-chain mode carries the current-turn media grant.  The
                # action profile must declare the same media intent so the
                # server binds that grant before routing to WorkflowRun.
                "contains_paid_media": allow_paid_media,
                "step_count": 4,
                "item_count": normalized_shot_count,
                "dependency_count": 3,
                "estimated_duration_seconds": max(
                    240,
                    normalized_shot_count * normalized_duration * 12,
                ),
            },
            "director_intent_contract": {
                "delivery_level": "final_film",
                "shot_count": normalized_shot_count,
            },
            "inputs": {
                "aspect_ratio": "16:9",
                "image_size": "1K",
                "quality": "low",
                "video_resolution": "768p",
                "video_duration_seconds": normalized_duration,
                "output_resolution": "1366x768",
            },
            "run_mode": run_mode,
            "task_authorization": {
                "scope": "current_turn",
                "run_mode": run_mode,
                "allow_structure": True,
                "allow_paid_media": allow_paid_media,
                "max_paid_starts": max_paid_starts,
                "require_video_confirmation": False,
            },
        }
        if recovery_tool_call is not None:
            tool_name = "village_canvas_capability"
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_t112_dispatch",
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }
            ],
        }
        finish_reason = "tool_calls"
    else:
        message = {
            "role": "assistant",
            "content": "已创建唯一的成片工作流，当前停在首次媒体授权门。",
        }
        finish_reason = "stop"

    if not stream:
        payload = {
            "id": completion_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json"

    delta = (
        {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_t112_dispatch",
                    "type": "function",
                    "function": {
                        "name": message["tool_calls"][0]["function"]["name"],
                        "arguments": message["tool_calls"][0]["function"]["arguments"],
                    },
                }
            ]
        }
        if tool_call
        else {"content": message["content"]}
    )
    chunks = [
        {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        },
        {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        },
        {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
        },
    ]
    body = "".join(
        f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks
    )
    return f"{body}data: [DONE]\n\n".encode("utf-8"), "text/event-stream"


class _UpstreamHandler(BaseHTTPRequestHandler):
    server: "LocalUpstreamServer"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json")

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        path = urlparse(self.path).path
        self.server.record("GET", path)
        if path.rstrip("/") == "/v1/models":
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": "t112-local-agent", "object": "model"},
                        {"id": "t112-local-image", "object": "model"},
                        {"id": "t112-local-video", "object": "model"},
                        {"id": "t112-local-audio", "object": "model"},
                    ],
                },
            )
            return
        if self.server.full_chain and (
            path in {"/media/t112-shot.mp4", "/t112-shot.mp4"}
            or path.startswith("/media/t112-shot-")
        ):
            duration = self.server.video_duration_from_path(path)
            self._send_bytes(200, self.server.ensure_video(duration), "video/mp4")
            return
        if self.server.full_chain and path.startswith("/media/t112-video/"):
            task_id = path.rsplit("/", 1)[-1]
            if task_id.endswith(".mp4"):
                task_id = task_id[:-4]
            if not self.server.has_video_task(task_id):
                self._send_json(404, {"error": {"message": "video task not found"}})
                return
            self._send_bytes(
                200,
                self.server.ensure_video_for_task(task_id),
                "video/mp4",
            )
            return
        if self.server.full_chain and (
            path.rstrip("/") in {"/videos", "/v1/videos"}
            or path.startswith("/videos/")
            or path.startswith("/v1/videos/")
        ):
            task_id = path.rstrip("/").rsplit("/", 1)[-1]
            self._send_json(
                200,
                {
                    "id": task_id,
                    "status": "completed",
                    "url": self.server.video_url_for_task(task_id),
                    "usage": {"cost": {"credits": 5}},
                },
            )
            return
        if self.server.native_minimax_video and path.startswith(
            "/v2/query/video_generation/"
        ):
            task_id = path.rstrip("/").rsplit("/", 1)[-1]
            if not self.server.has_video_task(task_id):
                self._send_json(
                    404,
                    {"error": {"message": "Task not found"}},
                )
                return
            self._send_json(
                200,
                {
                    "task": {
                        "status": "succeeded",
                        "content": {
                            "url": self.server.video_url_for_task(task_id),
                        },
                        "usage": {"cost": {"credits": 5}},
                    }
                },
            )
            return
        if path.startswith("/v2/"):
            # Native MiniMax discovery treats a structured JSON 404 as proof
            # that the /v2 route exists. This upstream declares OpenAI Video,
            # so unknown /v2 paths must look like an ordinary SPA fallback.
            self._send_bytes(
                404,
                b"<!doctype html><html><body>not found</body></html>",
                "text/html; charset=utf-8",
            )
            return
        self._send_json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        content_type = str(self.headers.get("Content-Type") or "")
        payload = _request_payload(raw, content_type)
        is_image_path = any(
            marker in path for marker in ("/images/", "/images", "/image/")
        )
        is_video_path = any(
            marker in path
            for marker in ("/videos", "/video/", "/video_generation")
        )
        is_audio_path = path.rstrip("/") == AUDIO_SPEECH_PATH
        storyboard_frame_index = (
            _requested_storyboard_shot_number(
                payload,
                shot_count=self.server.shot_count,
            )
            if (
                self.server.full_chain
                and is_image_path
                and path.rstrip("/").endswith("/images/edits")
            )
            else 0
        )
        requested_video_duration = _requested_video_duration(payload) if is_video_path else None
        messages = payload.get("messages")
        messages = messages if isinstance(messages, list) else []
        has_tool_result = any(
            isinstance(item, dict) and str(item.get("role") or "") == "tool"
            for item in messages
        )
        request = {
            "method": "POST",
            "path": path,
            "model": str(payload.get("model") or ""),
            "stream": bool(payload.get("stream")),
            "message_count": len(messages),
            "has_tool_result": has_tool_result,
            "tool_names": sorted(
                str((tool.get("function") or {}).get("name") or "")
                for tool in (payload.get("tools") or [])
                if isinstance(tool, dict)
            )[:24],
        }
        agent_request = _canvas_agent_request(messages)
        checkpoint = _canvas_checkpoint(messages)
        recovery_tool_call = _indexed_recovery_tool_call(agent_request, checkpoint)
        request["current_turn_needs_tool_call"] = _current_turn_needs_tool_call(
            messages
        )
        request["checkpoint"] = _checkpoint_telemetry(checkpoint)
        request["recovery_tool_call"] = str(
            (recovery_tool_call or {}).get("capability_id") or ""
        )
        # Keep the authoritative tool receipt visible in isolated evidence.
        # This is diagnostic only; it never changes the upstream response.
        tool_receipts = []
        for message in messages:
            if not isinstance(message, dict) or str(message.get("role") or "") != "tool":
                continue
            content = message.get("content")
            if isinstance(content, list):
                content = " ".join(
                    str(item.get("text") or "")
                    for item in content
                    if isinstance(item, dict)
                )
            tool_receipts.append(str(content or "")[:8_000])
        if tool_receipts:
            request["tool_receipts"] = tool_receipts[-3:]
        if agent_request:
            task_authorization = (
                agent_request.get("task_authorization")
                if isinstance(agent_request.get("task_authorization"), dict)
                else {}
            )
            grant_id = str(task_authorization.get("grant_id") or "").strip()
            request["canvas_agent_request"] = {
                "request": str(agent_request.get("request") or "")[:1000],
                "run_mode": str(agent_request.get("run_mode") or "")[:80],
                "workflow_runtime": (
                    agent_request.get("workflow_runtime")
                    if isinstance(agent_request.get("workflow_runtime"), dict)
                    else {}
                ),
                "task_authorization": {
                    "grant_present": bool(grant_id),
                    "grant_prefix": "pmg_" if grant_id.startswith("pmg_") else "",
                    "compose_authorization_present": bool(
                        task_authorization.get("compose_authorization_id")
                    ),
                    "scope": str(task_authorization.get("scope") or "")[:80],
                    "run_mode": str(task_authorization.get("run_mode") or "")[:80],
                    "allow_paid_media": (
                        task_authorization.get("allow_paid_media") is True
                    ),
                    "max_paid_starts": task_authorization.get("max_paid_starts"),
                },
            }
        if requested_video_duration is not None:
            request["requested_duration_seconds"] = requested_video_duration
        if is_image_path:
            request.update(_requested_image_contract(payload))
        video_binding_index = 0
        video_binding_diagnostics: list[dict[str, Any]] = []
        if is_video_path:
            request.update(_requested_video_contract(payload))
            request["payload_shape"] = _payload_shape(payload)
            (
                video_binding_index,
                video_binding_diagnostics,
            ) = self.server.inspect_video_shot_index(payload)
            if video_binding_index:
                request["shot_index"] = video_binding_index
            request["video_reference_diagnostics"] = video_binding_diagnostics
        if is_audio_path:
            request["input_chars"] = len(str(payload.get("input") or ""))
        if storyboard_frame_index:
            request["shot_frame_index"] = storyboard_frame_index
        elif self.server.full_chain and is_image_path and path.rstrip("/").endswith(
            "/images/edits"
        ):
            request["storyboard_binding_error"] = (
                "T-112 storyboard image prompt did not expose a valid shot number"
            )
        self.server.record_request(request)

        if request.get("storyboard_binding_error"):
            self._send_json(
                409,
                {"error": {"message": request["storyboard_binding_error"]}},
            )
            return

        if path.rstrip("/") == "/v1/chat/completions":
            model = str(payload.get("model") or "t112-local-agent")
            body, content_type = _chat_response(
                model=model,
                tool_call=request["current_turn_needs_tool_call"],
                stream=bool(payload.get("stream")),
                recovery_tool_call=recovery_tool_call,
                run_mode="auto" if self.server.full_chain else "draft",
                allow_paid_media=self.server.full_chain,
                max_paid_starts=4 if self.server.full_chain else 0,
                shot_count=self.server.shot_count,
                video_duration_seconds=self.server.video_duration_seconds,
            )
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.server.full_chain and is_image_path:
            image_png = (
                self.server.storyboard_image(storyboard_frame_index)
                if storyboard_frame_index is not None
                else self.server.ensure_media_assets()
            )
            encoded = base64.b64encode(image_png).decode("ascii")
            self._send_json(
                200,
                {
                    "data": [{"b64_json": encoded, "revised_prompt": ""}],
                    "usage": {"cost": {"credits": 2}},
                },
            )
            return
        if self.server.full_chain and is_video_path:
            if self.server.consume_video_failure():
                self._send_json(
                    502,
                    {
                        "error": {
                            "message": "T-112 injected video start failure",
                        }
                    },
                )
                return
            if video_binding_index <= 0:
                self._send_json(
                    409,
                    {
                        "error": {
                            "message": (
                                "T-112 video request cannot be bound to a "
                                "storyboard reference image: "
                                f"{json.dumps(video_binding_diagnostics, ensure_ascii=False)}"
                            )
                        }
                    },
                )
                return
            task_id = self.server.register_video_task(
                requested_video_duration,
                shot_index=video_binding_index,
            )
            if self.server.native_minimax_video and "video_generation" in path:
                self._send_json(
                    200,
                    {
                        "task_id": task_id,
                        "usage": {"cost": {"credits": 5}},
                    },
                )
                return
            self._send_json(
                200,
                {
                    "id": task_id,
                    "status": "completed",
                    "url": self.server.video_url_for_task(task_id),
                    "usage": {"cost": {"credits": 5}},
                },
            )
            return
        if self.server.full_chain and is_audio_path:
            self._send_bytes(
                200,
                self.server.ensure_speech_audio(),
                "audio/mpeg",
            )
            return
        if is_image_path or is_video_path or "/audio" in path:
            self._send_json(
                409,
                {
                    "error": {
                        "message": "T-112 draft mode must not reach a media endpoint"
                    }
                },
            )
            return
        self._send_json(404, {"error": {"message": "not found"}})


class LocalUpstreamServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        *,
        full_chain: bool = False,
        native_minimax_video: bool = False,
        video_failures_remaining: int = 0,
        shot_count: int = SHOT_COUNT_DEFAULT,
        video_duration_seconds: int = VIDEO_DURATION_DEFAULT_SECONDS,
    ) -> None:
        super().__init__(("127.0.0.1", 0), _UpstreamHandler)
        self.full_chain = bool(full_chain)
        self.native_minimax_video = bool(native_minimax_video)
        self.shot_count = _normalize_shot_count(shot_count)
        self.video_duration_seconds = _normalize_video_duration(
            video_duration_seconds
        )
        self._video_failures_remaining = max(
            0,
            int(video_failures_remaining),
        )
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._video_lock = threading.Lock()
        self.image_png = b""
        self._image_assets: dict[str, bytes] = {}
        self._video_assets: dict[tuple[int, int], bytes] = {}
        self._video_tasks: dict[str, dict[str, int]] = {}
        self._video_sequence = 0
        self._media_assets_lock = threading.Lock()
        self._media_assets_ready = False
        self._speech_lock = threading.Lock()

    def ensure_media_assets(self) -> bytes:
        media_dir = TARGET_UI_SMOKE / "runtime" / "t112-upstream"
        image_path = media_dir / "frame.png"
        with self._media_assets_lock:
            if not self._media_assets_ready or not image_path.is_file():
                self._build_media_assets()
                self._media_assets_ready = True
            return self.image_png

    def _build_media_assets(self) -> None:
        media_dir = TARGET_UI_SMOKE / "runtime" / "t112-upstream"
        media_dir.mkdir(parents=True, exist_ok=True)
        image_path = media_dir / "frame.png"
        subprocess.run(
            [
                str(FFMPEG),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=160x90",
                "-vf",
                _deterministic_frame_filter(),
                "-frames:v",
                "1",
                str(image_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.image_png = image_path.read_bytes()

    def _ensure_colored_image_asset(self, shot_index: int) -> bytes:
        color = _shot_frame_color(shot_index)
        key = f"shot-{shot_index}-{color}.png"
        with self._media_assets_lock:
            cached = self._image_assets.get(key)
            if cached is not None:
                return cached
            media_dir = TARGET_UI_SMOKE / "runtime" / "t112-upstream"
            media_dir.mkdir(parents=True, exist_ok=True)
            image_path = media_dir / key
            subprocess.run(
                [
                    str(FFMPEG),
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c={color}:s=160x90",
                    "-vf",
                    _deterministic_frame_filter(),
                    "-frames:v",
                    "1",
                    str(image_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            data = image_path.read_bytes()
            self._image_assets[key] = data
            return data

    def storyboard_image(self, shot_index: int) -> bytes:
        return self._ensure_colored_image_asset(shot_index)

    def ensure_speech_audio(self) -> bytes:
        media_dir = TARGET_UI_SMOKE / "runtime" / "t112-upstream"
        audio_path = media_dir / "dialogue.mp3"
        with self._speech_lock:
            if not audio_path.is_file() or audio_path.stat().st_size <= 0:
                media_dir.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    [
                        str(FFMPEG),
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        "sine=frequency=440:sample_rate=44100:duration=1",
                        "-af",
                        "volume=0.25",
                        "-c:a",
                        "libmp3lame",
                        "-b:a",
                        "128k",
                        str(audio_path),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            return audio_path.read_bytes()

    def identify_video_shot_index(self, payload: dict[str, Any]) -> int:
        """Bind a video request to the exact storyboard image it references."""

        shot_index, _diagnostics = self.inspect_video_shot_index(payload)
        return shot_index

    def inspect_video_shot_index(
        self,
        payload: dict[str, Any],
    ) -> tuple[int, list[dict[str, Any]]]:
        """Return the bound shot and non-secret diagnostics for every reference."""

        references = _video_reference_images(payload)
        diagnostics: list[dict[str, Any]] = []
        if not references:
            return 0, diagnostics
        expected_rgb = {
            shot_index: _shot_frame_rgb(shot_index)
            for shot_index in range(1, self.shot_count + 1)
        }
        identified = 0
        for reference in references:
            url = reference["url"]
            image = _data_url_image_bytes(url)
            if not image:
                diagnostics.append(
                    {
                        "role": reference["role"],
                        "url_header": url.partition(",")[0][:80],
                        "url_chars": len(url),
                        "decoded_bytes": 0,
                    }
                )
                continue
            mean_rgb = _image_mean_rgb(image)
            if mean_rgb is None:
                diagnostics.append(
                    {
                        "role": reference["role"],
                        "url_header": url.partition(",")[0][:80],
                        "url_chars": len(url),
                        "decoded_bytes": len(image),
                        "mean_rgb": None,
                    }
                )
                continue
            ranked = sorted(
                (
                    (
                        sum(
                            (actual - expected) ** 2
                            for actual, expected in zip(
                                mean_rgb,
                                expected_color,
                                strict=True,
                            )
                        ),
                        shot_index,
                    )
                    for shot_index, expected_color in expected_rgb.items()
                ),
                key=lambda item: item[0],
            )
            best_distance = ranked[0][0] if ranked else None
            best_shot = ranked[0][1] if ranked else 0
            diagnostics.append(
                {
                    "role": reference["role"],
                    "url_header": url.partition(",")[0][:80],
                    "url_chars": len(url),
                    "decoded_bytes": len(image),
                    "mean_rgb": list(mean_rgb),
                    "best_shot": best_shot,
                    "best_distance_squared": best_distance,
                }
            )
            if not identified and best_distance is not None and best_distance <= 80**2:
                identified = best_shot
        return identified, diagnostics

    def ensure_video(
        self,
        duration: object,
        *,
        shot_index: int = 0,
    ) -> bytes:
        seconds = _normalize_video_duration(duration)
        normalized_shot_index = max(0, int(shot_index))
        with self._video_lock:
            cache_key = (seconds, normalized_shot_index)
            cached = self._video_assets.get(cache_key)
            if cached is not None:
                return cached
            media_dir = TARGET_UI_SMOKE / "runtime" / "t112-upstream"
            if normalized_shot_index:
                image_path = media_dir / (
                    f"shot-{normalized_shot_index}-"
                    f"{_shot_frame_color(normalized_shot_index)}.png"
                )
                self._ensure_colored_image_asset(normalized_shot_index)
            else:
                self.ensure_media_assets()
                image_path = media_dir / "frame.png"
            video_path = media_dir / (
                f"shot-{seconds}s-{normalized_shot_index}.mp4"
            )
            subprocess.run(
                [
                    str(FFMPEG),
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-loop",
                    "1",
                    "-i",
                    str(image_path),
                    "-vf",
                    _deterministic_motion_filter(seconds * 24),
                    "-t",
                    str(seconds),
                    "-r",
                    "24",
                    "-c:v",
                    "mpeg4",
                    "-q:v",
                    "3",
                    "-pix_fmt",
                    "yuv420p",
                    "-an",
                    str(video_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            data = video_path.read_bytes()
            self._video_assets[cache_key] = data
            return data

    def register_video_task(
        self,
        duration: object,
        *,
        shot_index: int,
    ) -> str:
        seconds = _normalize_video_duration(duration)
        normalized_shot_index = int(shot_index)
        if not 1 <= normalized_shot_index <= self.shot_count:
            raise ValueError("T-112 video task shot_index is out of range")
        with self._video_lock:
            self._video_sequence += 1
            task_id = (
                f"t112-local-video-{normalized_shot_index}-"
                f"{self._video_sequence}"
            )
            self._video_tasks[task_id] = {
                "duration_seconds": seconds,
                "shot_index": normalized_shot_index,
            }
        self.ensure_video(seconds, shot_index=normalized_shot_index)
        return task_id

    def ensure_video_for_task(self, task_id: str) -> bytes:
        task = self.video_task(task_id)
        return self.ensure_video(
            task["duration_seconds"],
            shot_index=task["shot_index"],
        )

    def consume_video_failure(self) -> bool:
        with self._video_lock:
            if self._video_failures_remaining <= 0:
                return False
            self._video_failures_remaining -= 1
            return True

    def video_duration_for_task(self, task_id: str) -> int:
        return self.video_task(task_id)["duration_seconds"]

    def video_task(self, task_id: str) -> dict[str, int]:
        with self._video_lock:
            task = self._video_tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            return dict(task)

    def has_video_task(self, task_id: str) -> bool:
        with self._video_lock:
            return task_id in self._video_tasks

    def video_duration_from_path(self, path: str) -> int:
        if path in {"/media/t112-shot.mp4", "/t112-shot.mp4"}:
            return VIDEO_DURATION_DEFAULT_SECONDS
        prefix = "/media/t112-shot-"
        token = path[len(prefix) :] if path.startswith(prefix) else ""
        if token.endswith(".mp4"):
            token = token[:-4]
        return _normalize_video_duration(token)

    def video_url_for_duration(self, duration: object) -> str:
        seconds = _normalize_video_duration(duration)
        return (
            f"http://127.0.0.1:{self.port}"
            f"/media/t112-shot-{seconds}.mp4"
        )

    def video_url_for_task(self, task_id: str) -> str:
        return (
            f"http://127.0.0.1:{self.port}"
            f"/media/t112-video/{task_id}.mp4"
        )

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def record(self, method: str, path: str) -> None:
        self.record_request({"method": method, "path": path})

    def record_request(self, request: dict[str, Any]) -> None:
        with self._lock:
            self.requests.append(dict(request))

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self.requests]


def _clear_isolated_workflow_state() -> None:
    connection = sqlite3.connect(WORKFLOW_DATABASE)
    try:
        for table in (
            "canvas_workflow_target_leases",
            "canvas_workflow_events",
            "canvas_workflow_commands",
            "canvas_workflow_runs",
        ):
            connection.execute(f"DELETE FROM {table}")
        connection.commit()
    finally:
        connection.close()


def _rewrite_isolated_paths() -> None:
    connection = sqlite3.connect(TARGET_STATE / "local" / "projects.db")
    try:
        for column in ("output_dir", "state_dir", "runtime_dir"):
            connection.execute(
                f"UPDATE projects SET {column}=REPLACE({column}, ?, ?)",
                ("ui-smoke-t096", "ui-smoke-t112"),
            )
        connection.commit()
    finally:
        connection.close()


def _seed_isolated_script_contract(
    *,
    shot_count: int,
    duration_seconds: int,
) -> dict[str, Any]:
    from novelvideo.freezone import canvas_store

    normalized_count = _normalize_shot_count(shot_count)
    normalized_duration = _normalize_video_duration(duration_seconds)
    path = canvas_store.canvas_path(PROJECT_STATE, CANVAS_ID)
    existing = json.loads(path.read_text(encoding="utf-8"))
    node = next(
        (
            item
            for item in existing.get("nodes", [])
            if isinstance(item, dict) and item.get("id") == SCRIPT_NODE_ID
        ),
        None,
    )
    if not isinstance(node, dict):
        raise RuntimeError("isolated fixture is missing the script node")
    data = dict(node.get("data") or {})
    data["scriptResult"] = {
        "title": f"画布脚本接管片（{normalized_count} 镜隔离验收）",
        "rows": _script_rows_for_shots(normalized_count, normalized_duration),
    }
    data.pop("scriptContractReport", None)
    node["data"] = data
    saved = canvas_store.save_canvas(
        PROJECT_STATE,
        CANVAS_ID,
        base_revision=int(existing.get("revision") or 0),
        build_payload=lambda _existing: existing,
    )
    return saved.payload


def _reset_isolated_site(
    *,
    shot_count: int = SHOT_COUNT_DEFAULT,
    duration_seconds: int = VIDEO_DURATION_DEFAULT_SECONDS,
) -> None:
    if not SOURCE_UI_SMOKE.is_dir():
        raise RuntimeError(f"T-096 isolated site is missing: {SOURCE_UI_SMOKE}")
    resolved = TARGET_UI_SMOKE.resolve()
    workspace = WORKSPACE.resolve()
    if resolved != workspace and workspace not in resolved.parents:
        raise RuntimeError(f"refusing to replace path outside workspace: {resolved}")
    if TARGET_UI_SMOKE.exists():
        shutil.rmtree(TARGET_UI_SMOKE)
    shutil.copytree(SOURCE_UI_SMOKE, TARGET_UI_SMOKE)
    _rewrite_isolated_paths()
    _clear_isolated_workflow_state()

    if (
        _normalize_shot_count(shot_count) != SHOT_COUNT_DEFAULT
        or _normalize_video_duration(duration_seconds)
        != VIDEO_DURATION_DEFAULT_SECONDS
    ):
        _seed_isolated_script_contract(
            shot_count=shot_count,
            duration_seconds=duration_seconds,
        )


def _seed_local_upstream_models(base_url: str) -> None:
    from novelvideo.generators.direct_model_capability_cache import (
        record_direct_model_capability,
    )
    from novelvideo.generators.video.direct_video_capability_cache import (
        record_capability as record_video_capability,
    )
    from novelvideo.model_gateway_settings import (
        _write_many,
        save_direct_models,
        save_direct_video_models,
    )

    save_direct_models(
        "chat",
        [
            {
                "id": "t112-agent",
                "label": "T-112 本地 Agent 替身",
                "modelId": "t112-local-agent",
                "baseUrl": base_url,
                "apiKey": "t112-local-upstream-key",
                "enabled": True,
                "isDefault": True,
            }
        ],
        confirm_clear=True,
    )
    record_direct_model_capability(
        base_url=base_url,
        kind="chat",
        upstream_model="t112-local-agent",
        protocol="openai-chat",
        capability={
            "probeContractVersion": 1,
            "verificationStatus": "runtime-verified",
            "modelFound": True,
            "discoveredModelCount": 1,
            "chatProbeStatus": "passed",
            "chatResponseUsable": True,
            "streamProbeStatus": "passed",
            "streamResponseUsable": True,
        },
    )

    save_direct_models(
        "image",
        [
            {
                "id": "t112-image",
                "label": "T-112 本地图片替身",
                "modelId": "t112-local-image",
                "baseUrl": base_url,
                "apiKey": "t112-local-upstream-key",
                "enabled": True,
                "isDefault": True,
            }
        ],
        confirm_clear=True,
    )
    record_direct_model_capability(
        base_url=base_url,
        kind="image",
        upstream_model="t112-local-image",
        protocol="openai-image",
        capability={
            "verificationStatus": "catalog-confirmed",
            "modelFound": True,
            "discoveredModelCount": 1,
            "modelMetadata": {
                "supportedModes": ["textToImage", "imageToImage"],
                "aspectRatioOptions": ["1:1", "16:9", "9:16", "4:3", "3:4"],
                "resolutionOptions": ["1K"],
            },
        },
    )

    save_direct_video_models(
        [
            {
                "id": "t112-video",
                "label": "T-112 本地视频替身",
                "modelId": "t112-local-video",
                "baseUrl": base_url,
                "apiKey": "t112-local-upstream-key",
                "enabled": True,
                "isDefault": True,
            }
        ],
        confirm_clear=True,
    )
    record_video_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model="t112-local-video",
        capability={
            "verificationStatus": "contract-resolved",
            "probeStatus": "passed",
            "detectedProtocol": "openai-video",
            "credentialValidation": {
                "status": "passed",
                "checked": True,
            },
            "modelFound": True,
            "discoveredModelCount": 1,
            "supportedProtocols": ["openai:video_generation"],
            "modes": ["textToVideo", "imageToVideo", "firstLastFrame", "allReference"],
            "declaredCapabilities": ["durationOptions"],
            "durationOptions": [2, 4, 5],
        },
    )
    _write_many(
        {
            "direct_audio_models": json.dumps(
                [
                    {
                        "id": "t112-audio",
                        "label": "T-112 本地音频替身",
                        "modelId": "t112-local-audio",
                        "baseUrl": base_url,
                        "apiKey": "t112-local-upstream-key",
                        "enabled": True,
                        "isDefault": True,
                        "supportedModes": ["text_to_speech"],
                        "requestedProtocol": "openai-audio",
                    }
                ],
                ensure_ascii=False,
            )
        }
    )
    record_direct_model_capability(
        base_url=base_url,
        kind="audio",
        upstream_model="t112-local-audio",
        protocol="openai-audio",
        capability={
            "verificationStatus": "contract-resolved",
            "modelFound": True,
            "discoveredModelCount": 1,
            "modelMetadata": {"supportedModes": ["text_to_speech"]},
        },
    )


def _workflow_run_snapshot() -> dict[str, Any]:
    connection = sqlite3.connect(WORKFLOW_DATABASE)
    try:
        row = connection.execute(
            "SELECT id, workflow_id, run_mode, status, revision, event_seq, "
            "error_code, next_action, inputs_json, artifacts_json "
            "FROM canvas_workflow_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return {}
    fields = (
        "id",
        "workflow_id",
        "run_mode",
        "status",
        "revision",
        "event_seq",
        "error_code",
        "next_action",
    )
    result = dict(zip(fields, row[:8], strict=True))
    result["inputs"] = json.loads(row[8])
    result["artifacts"] = json.loads(row[9])
    return result


def _build_stats(upstream: LocalUpstreamServer) -> dict[str, Any]:
    requests = upstream.snapshot()
    chat_requests = [
        item for item in requests if item.get("path", "").endswith("/chat/completions")
    ]
    media_requests = [
        item
        for item in requests
        if any(
            marker in str(item.get("path") or "")
            for marker in (
                "/images/",
                "/image/",
                "/videos",
                "/video/",
                "video_generation",
                "/audio",
            )
        )
    ]
    return {
        "schema": "t112_acceptance_stats.v1",
        "upstream_requests": requests,
        "upstream_chat_requests": len(chat_requests),
        "upstream_media_requests": media_requests,
        "workflow_run": _workflow_run_snapshot(),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_delivery_qc_receipt(
    value: object,
    *,
    final_sha256: str,
    final_width: int,
    final_height: int,
) -> bool:
    if not isinstance(value, dict) or value.get("schema") != "delivery_qc_contract.v1":
        return False
    checks = value.get("checks")
    if not isinstance(checks, dict):
        return False
    normalized: dict[str, dict[str, Any]] = {}
    for name in DELIVERY_QC_REQUIRED_CHECKS:
        check = checks.get(name)
        if (
            not isinstance(check, dict)
            or check.get("name") != name
            or check.get("status") not in {"passed", "failed", "not_run"}
            or "evidence" not in check
            or (check.get("evidence") is None and check["status"] != "not_run")
        ):
            return False
        normalized[name] = check
    failed = [
        name for name, check in normalized.items() if check["status"] == "failed"
    ]
    not_run = [
        name for name, check in normalized.items() if check["status"] == "not_run"
    ]
    expected_passed = False if failed else None if not_run else True
    if (
        value.get("passed") is not expected_passed
        or value.get("failed_checks") != failed
        or value.get("not_run_checks") != not_run
    ):
        return False
    sha_evidence = normalized["sha256"]["evidence"]
    if isinstance(sha_evidence, dict):
        sha_evidence = sha_evidence.get("sha256") or sha_evidence.get("value")
    if str(sha_evidence or "") != final_sha256:
        return False
    dimensions = normalized["dimensions"]["evidence"]
    return (
        isinstance(dimensions, dict)
        and dimensions.get("width") == final_width
        and dimensions.get("height") == final_height
    )


def _archive_key(run_id: str) -> str:
    """Return a filesystem-safe immutable key for one successful Run archive."""

    safe_run_id = re.sub(r"[^A-Za-z0-9._-]+", "-", run_id).strip(".-")
    safe_run_id = (safe_run_id or "unknown-run")[:80]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}-{safe_run_id}"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Replace a JSON pointer without exposing a half-written file."""

    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp_path, path)


def _extract_video_frame(video_path: Path, target_path: Path, seconds: float) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(FFMPEG),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{seconds:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            str(target_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if not target_path.is_file() or target_path.stat().st_size <= 0:
        raise RuntimeError(f"failed to extract video frame: {video_path}")


def _image_similarity(first_path: Path, second_path: Path) -> float:
    from PIL import Image, ImageChops, ImageStat

    size = (128, 128)
    with Image.open(first_path) as first_image, Image.open(second_path) as second_image:
        first = first_image.convert("RGB").resize(size, Image.Resampling.LANCZOS)
        second = second_image.convert("RGB").resize(size, Image.Resampling.LANCZOS)
        difference = ImageChops.difference(first, second)
        mean_difference = sum(ImageStat.Stat(difference).mean) / 3
    return round(max(0.0, min(1.0, 1.0 - mean_difference / 255.0)), 6)


def _verify_final_compose_order(evidence_path: Path) -> dict[str, Any]:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    mode_evidence = evidence.get("mode_evidence")
    mode_evidence = mode_evidence if isinstance(mode_evidence, dict) else {}
    shot_videos = mode_evidence.get("shot_videos")
    shot_videos = shot_videos if isinstance(shot_videos, dict) else {}
    final_film = mode_evidence.get("final_film")
    final_film = final_film if isinstance(final_film, dict) else {}
    final_artifact = final_film.get("final_compose_artifact")
    final_artifact = final_artifact if isinstance(final_artifact, dict) else {}
    checks = shot_videos.get("first_frame_checks")
    checks = checks if isinstance(checks, list) else []
    final_path = Path(str(final_artifact.get("path") or ""))
    shot_duration = float(mode_evidence.get("requested_video_duration_seconds") or 0)
    if not checks or not final_path.is_file() or shot_duration <= 0:
        raise RuntimeError("cannot verify final compose order without complete evidence")

    frame_dir = TARGET_UI_SMOKE / "runtime" / "t112-final-order"
    order_checks: list[dict[str, Any]] = []
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise RuntimeError("invalid shot frame check")
        source_path = Path(str(check.get("source_image_path") or ""))
        if not source_path.is_file():
            raise RuntimeError(f"missing shot source image: {source_path}")
        target_path = frame_dir / f"shot-{index + 1:02d}.png"
        sample_seconds = shot_duration * (index + 0.5)
        _extract_video_frame(final_path, target_path, sample_seconds)
        similarity = _image_similarity(source_path, target_path)
        passed = similarity >= 0.72
        order_checks.append(
            {
                "shot_id": str(check.get("shot_id") or f"shot:{index + 1}"),
                "sample_seconds": round(sample_seconds, 3),
                "source_image_path": str(source_path),
                "final_frame_path": str(target_path),
                "source_image_sha256": str(check.get("source_image_sha256") or ""),
                "final_frame_sha256": _sha256_file(target_path),
                "similarity": similarity,
                "threshold": 0.72,
                "status": "passed" if passed else "failed",
                "match": passed,
            }
        )
    if not all(item["match"] is True for item in order_checks):
        raise RuntimeError(
            "final compose order does not match shot source images: "
            f"{json.dumps(order_checks, ensure_ascii=False)}"
        )
    final_film["final_order_checks"] = order_checks
    final_film["final_order_verified"] = True
    mode_evidence["final_film"] = final_film
    evidence["mode_evidence"] = mode_evidence
    _write_json_atomic(evidence_path, evidence)
    return {
        "verified_shots": len(order_checks),
        "minimum_similarity": min(item["similarity"] for item in order_checks),
    }


def _verify_agent_run_binding(evidence_path: Path) -> dict[str, Any]:
    """Require the persisted assistant turn to bind the Run it really started."""

    chat_db = PROJECT_STATE / "chat.db"
    if not chat_db.is_file():
        raise RuntimeError(f"T-112 chat database is missing: {chat_db}")
    connection = sqlite3.connect(f"file:{chat_db.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            """
            SELECT content, metadata_json
            FROM chat_messages
            WHERE role = 'assistant'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError("T-112 assistant turn was not persisted")
    content = str(row[0] or "")
    try:
        metadata = json.loads(str(row[1] or "{}"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("T-112 assistant metadata is not valid JSON") from exc
    delivery = (
        metadata.get("delivery_verification")
        if isinstance(metadata, dict)
        else {}
    )
    delivery = delivery if isinstance(delivery, dict) else {}
    run_id = str(delivery.get("workflow_run_id") or "").strip()
    status = str(delivery.get("status") or "").strip()
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    run_after = (
        evidence.get("run_after")
        if isinstance(evidence.get("run_after"), dict)
        else {}
    )
    run_status = str(run_after.get("status") or "").strip().casefold()
    release = (
        run_after.get("release_readiness")
        if isinstance(run_after.get("release_readiness"), dict)
        else {}
    )
    release_status = str(release.get("status") or "").strip().casefold()
    can_publish = release.get("can_publish")
    if not re.fullmatch(r"(?:wfr|run)_[A-Za-z0-9_-]{8,}", run_id):
        raise RuntimeError(
            "T-112 assistant turn did not bind the started WorkflowRun: "
            + json.dumps(delivery, ensure_ascii=False)
        )
    if "没有真正写入画布" in content:
        raise RuntimeError("T-112 assistant turn contradicts the started Run")
    expected_terminal_statuses = {
        "completed": {"verified_success", "release_blocked", "release_unverified"},
        "failed": {"verified_failure"},
        "cancelled": {"cancelled"},
    }
    expected = expected_terminal_statuses.get(run_status)
    if expected is not None and status not in expected:
        raise RuntimeError(
            "T-112 terminal assistant receipt does not match the WorkflowRun: "
            + json.dumps(
                {
                    "run_status": run_status,
                    "delivery_status": status,
                    "expected": sorted(expected),
                },
                ensure_ascii=False,
            )
        )
    if expected is not None and "in_progress" in status:
        raise RuntimeError("T-112 assistant turn stayed in_progress after Run terminal")
    binding = {
        "schema": "t112_agent_run_binding.v1",
        "workflow_run_id": run_id,
        "run_status": run_status,
        "delivery_status": status,
        "release_status": release_status,
        "can_publish": can_publish,
        "terminal_message_verified": expected is not None,
        "assistant_mentions_false_canvas_failure": False,
    }
    evidence["agent_run_binding"] = binding
    _write_json_atomic(evidence_path, evidence)
    return binding


def _copy_verified_asset(
    source_path: Path,
    expected_sha256: str,
    target_path: Path,
    *,
    label: str,
) -> None:
    if (
        not source_path.is_file()
        or not source_path.stat().st_size
        or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
        or _sha256_file(source_path) != expected_sha256
    ):
        raise RuntimeError(f"cannot archive unverified {label}: {source_path}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)
    if _sha256_file(target_path) != expected_sha256:
        raise RuntimeError(f"archived {label} hash drifted: {target_path}")


def _rewrite_archived_full_chain_assets(
    *,
    archive_dir: Path,
    evidence: dict[str, Any],
    shot_count: int,
) -> dict[str, int]:
    mode_evidence = evidence.get("mode_evidence")
    mode_evidence = mode_evidence if isinstance(mode_evidence, dict) else {}
    shot_videos = mode_evidence.get("shot_videos")
    shot_videos = shot_videos if isinstance(shot_videos, dict) else {}
    final_film = mode_evidence.get("final_film")
    final_film = final_film if isinstance(final_film, dict) else {}
    checks = shot_videos.get("first_frame_checks")
    checks = checks if isinstance(checks, list) else []
    order_checks = final_film.get("final_order_checks")
    order_checks = order_checks if isinstance(order_checks, list) else []
    if len(checks) != shot_count or len(order_checks) != shot_count:
        raise RuntimeError(
            "cannot archive incomplete shot assets: "
            f"first_frame_checks={len(checks)} "
            f"final_order_checks={len(order_checks)} expected={shot_count}"
        )

    archived_sources: dict[str, Path] = {}
    for index, check in enumerate(checks, 1):
        if not isinstance(check, dict):
            raise RuntimeError("cannot archive invalid first-frame evidence")
        source_path = Path(str(check.get("source_image_path") or ""))
        source_sha256 = str(check.get("source_image_sha256") or "")
        source_suffix = source_path.suffix.casefold() or ".img"
        archived_source = (
            archive_dir / "source-images" / f"shot-{index:02d}{source_suffix}"
        )
        _copy_verified_asset(
            source_path,
            source_sha256,
            archived_source,
            label=f"shot {index} source image",
        )
        archived_sources[str(source_path)] = archived_source
        check["source_image_original_path"] = str(source_path)
        check["source_image_path"] = str(archived_source)

        video_path = Path(str(check.get("video_path") or ""))
        video_sha256 = str(check.get("video_sha256") or "")
        video_width = check.get("video_width")
        video_height = check.get("video_height")
        if (
            not isinstance(video_width, int)
            or isinstance(video_width, bool)
            or video_width <= 0
            or not isinstance(video_height, int)
            or isinstance(video_height, bool)
            or video_height <= 0
        ):
            raise RuntimeError(f"cannot archive invalid shot {index} dimensions")
        archived_video = archive_dir / "shot-videos" / f"shot-{index:02d}.mp4"
        _copy_verified_asset(
            video_path,
            video_sha256,
            archived_video,
            label=f"shot {index} video",
        )
        check["video_original_path"] = str(video_path)
        check["video_path"] = str(archived_video)

    for index, check in enumerate(order_checks, 1):
        if not isinstance(check, dict):
            raise RuntimeError("cannot archive invalid final-order evidence")
        source_path = Path(str(check.get("source_image_path") or ""))
        source_sha256 = str(check.get("source_image_sha256") or "")
        archived_source = archived_sources.get(str(source_path))
        if archived_source is None:
            source_suffix = source_path.suffix.casefold() or ".img"
            archived_source = (
                archive_dir
                / "source-images"
                / f"final-order-source-{index:02d}{source_suffix}"
            )
            _copy_verified_asset(
                source_path,
                source_sha256,
                archived_source,
                label=f"final-order source {index}",
            )
        check["source_image_original_path"] = str(source_path)
        check["source_image_path"] = str(archived_source)

        final_frame_path = Path(str(check.get("final_frame_path") or ""))
        final_frame_sha256 = str(check.get("final_frame_sha256") or "")
        archived_final_frame = (
            archive_dir / "final-frames" / f"shot-{index:02d}.png"
        )
        _copy_verified_asset(
            final_frame_path,
            final_frame_sha256,
            archived_final_frame,
            label=f"final-order frame {index}",
        )
        check["final_frame_original_path"] = str(final_frame_path)
        check["final_frame_path"] = str(archived_final_frame)

    shot_videos["first_frame_checks"] = checks
    final_film["final_order_checks"] = order_checks
    mode_evidence["shot_videos"] = shot_videos
    mode_evidence["final_film"] = final_film
    evidence["mode_evidence"] = mode_evidence
    return {
        "source_image_count": len(checks),
        "shot_video_count": len(checks),
        "final_frame_count": len(order_checks),
    }


def _archive_execution_evidence(
    *,
    mode: str,
    evidence_path: Path,
    screenshot_path: Path,
    shot_count: int = SHOT_COUNT_DEFAULT,
    video_duration_seconds: int = VIDEO_DURATION_DEFAULT_SECONDS,
) -> dict[str, Any]:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    run = evidence.get("run_after")
    run = run if isinstance(run, dict) else {}
    run_id = str(run.get("id") or "")
    if not re.fullmatch(r"wfr_[A-Za-z0-9_-]+", run_id):
        raise RuntimeError(f"refusing to archive invalid workflow run id: {run_id!r}")
    run_status = str(run.get("status") or "")
    if mode == "draft":
        if (
            run_status != "failed"
            or run.get("error_code")
            != "workflow_storyboard_paid_media_not_authorized"
        ):
            raise RuntimeError(
                "refusing to archive draft evidence without the expected "
                "paid-media stop"
            )
    elif run_status != "completed":
        raise RuntimeError("refusing to archive evidence for a non-completed run")

    normalized_shot_count = _normalize_shot_count(shot_count)
    normalized_duration = _normalize_video_duration(video_duration_seconds)
    final_artifact: dict[str, Any] = {}
    final_video_path: Path | None = None
    final_video_sha256 = ""
    archived_asset_counts = {
        "source_image_count": 0,
        "shot_video_count": 0,
        "final_frame_count": 0,
    }
    if mode in {"full-chain", "recovery-chain"}:
        mode_evidence = evidence.get("mode_evidence")
        mode_evidence = mode_evidence if isinstance(mode_evidence, dict) else {}
        final_film = mode_evidence.get("final_film")
        final_film = final_film if isinstance(final_film, dict) else {}
        candidate = final_film.get("final_compose_artifact")
        final_artifact = candidate if isinstance(candidate, dict) else {}
        final_video_path = Path(str(final_artifact.get("path") or ""))
        final_video_sha256 = str(final_artifact.get("sha256") or "")
        if not final_artifact:
            raise RuntimeError(
                "cannot archive full-chain evidence without a hash-matched "
                "final compose artifact"
            )

    mode_root = EVIDENCE_ARCHIVE_ROOT / mode
    archive_key = _archive_key(run_id)
    archive_dir = mode_root / "runs" / archive_key
    archive_dir.mkdir(parents=True, exist_ok=False)
    archived_evidence = archive_dir / evidence_path.name
    archived_screenshot = archive_dir / screenshot_path.name
    shutil.copy2(evidence_path, archived_evidence)
    shutil.copy2(screenshot_path, archived_screenshot)
    archived_final_video: Path | None = None
    if final_video_path is not None:
        archived_final_video = archive_dir / "final-film.mp4"
        _copy_verified_asset(
            final_video_path,
            final_video_sha256,
            archived_final_video,
            label="final compose artifact",
        )
        archived_payload = json.loads(archived_evidence.read_text(encoding="utf-8"))
        archived_mode_evidence = archived_payload.get("mode_evidence")
        archived_mode_evidence = (
            archived_mode_evidence
            if isinstance(archived_mode_evidence, dict)
            else {}
        )
        archived_final_film = archived_mode_evidence.get("final_film")
        archived_final_film = (
            archived_final_film if isinstance(archived_final_film, dict) else {}
        )
        archived_artifact = archived_final_film.get("final_compose_artifact")
        archived_artifact = (
            archived_artifact if isinstance(archived_artifact, dict) else {}
        )
        if not archived_artifact:
            raise RuntimeError("archived evidence lost its final compose artifact")
        archived_artifact["original_path"] = str(final_video_path)
        archived_artifact["path"] = str(archived_final_video)
        archived_asset_counts = _rewrite_archived_full_chain_assets(
            archive_dir=archive_dir,
            evidence=archived_payload,
            shot_count=normalized_shot_count,
        )
        delivery_qc = archived_final_film.get("delivery_qc")
        final_width = archived_artifact.get("width")
        final_height = archived_artifact.get("height")
        if (
            not isinstance(final_width, int)
            or isinstance(final_width, bool)
            or final_width <= 0
            or not isinstance(final_height, int)
            or isinstance(final_height, bool)
            or final_height <= 0
            or not _valid_delivery_qc_receipt(
                delivery_qc,
                final_sha256=final_video_sha256,
                final_width=final_width,
                final_height=final_height,
            )
        ):
            raise RuntimeError(
                "cannot archive full-chain evidence without a valid engineering QC receipt"
            )
        _write_json_atomic(archived_evidence, archived_payload)
    else:
        delivery_qc = {}
    manifest = {
        "schema": STABLE_EVIDENCE_SCHEMA,
        "mode": mode,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "archive_key": archive_key,
        "archive_manifest_path": str(archive_dir / "manifest.json"),
        "run_id": run_id,
        "run_status": str(run.get("status") or ""),
        "shot_count": normalized_shot_count,
        "video_duration_seconds": normalized_duration,
        "expected_total_duration_seconds": (
            normalized_shot_count * normalized_duration
        ),
        "evidence_path": str(archived_evidence),
        "evidence_sha256": _sha256_file(archived_evidence),
        "screenshot_path": str(archived_screenshot),
        "screenshot_sha256": _sha256_file(archived_screenshot),
        "final_video_path": (
            str(archived_final_video) if archived_final_video is not None else None
        ),
        "final_video_sha256": final_video_sha256 or None,
        "final_video_width": final_artifact.get("width"),
        "final_video_height": final_artifact.get("height"),
        "final_video_duration_seconds": final_artifact.get("duration_seconds"),
        "delivery_qc_passed": (
            delivery_qc.get("passed") if isinstance(delivery_qc, dict) else None
        ),
        "delivery_qc_failed_checks": (
            list(delivery_qc.get("failed_checks") or [])
            if isinstance(delivery_qc, dict)
            else []
        ),
        "delivery_qc_not_run_checks": (
            list(delivery_qc.get("not_run_checks") or [])
            if isinstance(delivery_qc, dict)
            else []
        ),
        **archived_asset_counts,
        "paidProvidersConnected": evidence.get("paidProvidersConnected") is True,
        "providerCallsStarted": evidence.get("providerCallsStarted") is True,
    }
    _write_json_atomic(archive_dir / "manifest.json", manifest)
    # The mode-level manifest is only a latest-pointer. Historical Run evidence
    # stays immutable under runs/<archive_key>/ and is never overwritten.
    _write_json_atomic(mode_root / "manifest.json", manifest)
    return manifest


def _final_order_is_verified(evidence: dict[str, Any], shot_count: int) -> bool:
    """Report whether the evidence already froze the composed shot order."""

    mode_evidence = evidence.get("mode_evidence")
    mode_evidence = mode_evidence if isinstance(mode_evidence, dict) else {}
    final_film = mode_evidence.get("final_film")
    final_film = final_film if isinstance(final_film, dict) else {}
    checks = final_film.get("final_order_checks")
    checks = checks if isinstance(checks, list) else []
    return final_film.get("final_order_verified") is True and len(checks) == shot_count


def _archive_evidence(
    *,
    mode: str,
    evidence_path: Path,
    screenshot_path: Path,
    shot_count: int = SHOT_COUNT_DEFAULT,
    video_duration_seconds: int = VIDEO_DURATION_DEFAULT_SECONDS,
) -> dict[str, Any]:
    """Freeze already-produced evidence into a hash-pinned manifest.

    This is the native replacement for the retired Hermes browser step: the
    ``.cjs`` driver writes the same full-chain evidence JSON the old harness
    wrote, but it cannot prove that the composed film really contains the shot
    order it claims. That proof is extracted from the composed film itself, so
    the same verification step the live adapter runs is run here first, and only
    then is the evidence promoted into the manifest Chain Doctor reads.
    """

    for label, path in (
        ("evidence", evidence_path),
        ("screenshot", screenshot_path),
    ):
        if not path.is_file():
            raise RuntimeError(f"cannot archive missing {label}: {path}")
    if mode in {"full-chain", "recovery-chain"}:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        if not isinstance(evidence, dict):
            raise RuntimeError("cannot archive evidence that is not a JSON object")
        if not _final_order_is_verified(
            evidence,
            _normalize_shot_count(shot_count),
        ):
            if not FFMPEG.is_file():
                raise RuntimeError(
                    "cannot verify the final-film order without bundled FFmpeg: "
                    f"{FFMPEG}"
                )
            _verify_final_compose_order(evidence_path)
    return _archive_execution_evidence(
        mode=mode,
        evidence_path=evidence_path,
        screenshot_path=screenshot_path,
        shot_count=shot_count,
        video_duration_seconds=video_duration_seconds,
    )


def _run_browser(
    *,
    full_chain: bool = False,
    recovery_chain: bool = False,
    shot_count: int = SHOT_COUNT_DEFAULT,
    duration_seconds: int = VIDEO_DURATION_DEFAULT_SECONDS,
) -> None:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node is not available")
    evidence_path = (
        TARGET_UI_SMOKE / "t112-full-chain-evidence.json"
        if full_chain
        else EVIDENCE_PATH
    )
    screenshot_path = (
        TARGET_UI_SMOKE / "t112-full-chain-complete.png"
        if full_chain
        else TARGET_UI_SMOKE / "t112-authorization-gate.png"
    )
    env = os.environ.copy()
    env["NODE_PATH"] = str(PLAYWRIGHT_NODE_MODULES)
    env.update(
        {
            "T112_PROJECT_ID": PROJECT_ID,
            "T112_CANVAS_ID": CANVAS_ID,
            "T112_SCRIPT_NODE_ID": SCRIPT_NODE_ID,
            "T112_UI_BASE": UI_BASE,
            "T112_API_BASE": API_BASE,
            "T112_STATS_URL": (
                API_BASE.removesuffix("/api/v1") + "/__acceptance__/stats"
            ),
            "T112_EVIDENCE_PATH": str(evidence_path),
            "T112_SCREENSHOT_PATH": str(screenshot_path),
            "T112_SHOT_COUNT": str(_normalize_shot_count(shot_count)),
            "T112_VIDEO_DURATION_SECONDS": str(
                _normalize_video_duration(duration_seconds)
            ),
            "T112_DIALOGUE_TEXT": os.environ.get("T112_DIALOGUE_TEXT", "").strip(),
        }
    )
    if full_chain:
        env["T112_FULL_CHAIN"] = "1"
    if recovery_chain:
        env["T112_RECOVERY_CHAIN"] = "1"
    subprocess.run(
        [node, str(Path(__file__).with_suffix(".cjs"))],
        cwd=ROOT,
        env=env,
        check=True,
    )


def _configure_process_environment(upstream: LocalUpstreamServer) -> None:
    os.environ.update(
        {
            "ST_EDITION": "ce",
            "ST_LOCAL_USERNAME": "local",
            "NOVELVIDEO_STATE_DIR": str(TARGET_STATE),
            "VILLAGE_CANVAS_API_URL": API_BASE.removesuffix("/api/v1"),
            "VILLAGE_CANVAS_API_TIMEOUT_SECONDS": "60",
            "NOVELVIDEO_API_HOST": "127.0.0.1",
            "NOVELVIDEO_API_PORT": str(API_PORT),
            "VILLAGE_CANVAS_TOOL_EXPOSURE_MODE": "indexed",
            "HERMES_CLI_PATH": str(HERMES_CLI),
            "T112_LOCAL_UPSTREAM_URL": upstream.base_url,
        }
    )
    os.environ["PATH"] = os.pathsep.join(
        [
            str(HERMES_CLI.parent),
            str(FFMPEG.parent),
            os.environ.get("PATH", ""),
        ]
    )


def _run_adapter(
    *,
    full_chain: bool = False,
    recovery_chain: bool = False,
    shot_count: int = SHOT_COUNT_DEFAULT,
    duration_seconds: int = VIDEO_DURATION_DEFAULT_SECONDS,
) -> int:
    normalized_shot_count = _normalize_shot_count(shot_count)
    normalized_duration = _normalize_video_duration(duration_seconds)
    _reset_isolated_site(
        shot_count=normalized_shot_count,
        duration_seconds=normalized_duration,
    )
    upstream = LocalUpstreamServer(
        full_chain=full_chain,
        shot_count=normalized_shot_count,
        video_duration_seconds=normalized_duration,
    )
    upstream_thread = threading.Thread(
        target=upstream.serve_forever,
        name="t112-local-upstream",
        daemon=True,
    )
    upstream_thread.start()
    _configure_process_environment(upstream)

    runtime_dir = TARGET_UI_SMOKE / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    log_handler = logging.FileHandler(
        runtime_dir / "t112-api.log",
        encoding="utf-8",
    )
    log_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(log_handler)

    sys.path.insert(0, str(ROOT / "src"))
    from novelvideo.ports.registry import ensure_bootstrap

    ensure_bootstrap()
    _seed_local_upstream_models(upstream.base_url)

    import novelvideo.task_backend.runners.freezone  # noqa: F401
    from novelvideo.api.app import app

    @app.get("/__acceptance__/stats")
    async def acceptance_stats() -> dict[str, Any]:
        return _build_stats(upstream)

    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=API_PORT,
            log_level="info",
            access_log=True,
        )
    )
    server_thread = threading.Thread(
        target=server.run,
        name="t112-api",
        daemon=True,
    )
    server_thread.start()
    vite = _start_vite(runtime_dir / "t112-vite.log")
    try:
        _wait_url(f"http://127.0.0.1:{API_PORT}/healthz")
        _wait_url(UI_BASE)
        _run_browser(
            full_chain=full_chain,
            recovery_chain=recovery_chain,
            shot_count=normalized_shot_count,
            duration_seconds=normalized_duration,
        )
        evidence_path = (
            TARGET_UI_SMOKE / "t112-full-chain-evidence.json"
            if full_chain
            else EVIDENCE_PATH
        )
        screenshot_path = (
            TARGET_UI_SMOKE / "t112-full-chain-complete.png"
            if full_chain
            else TARGET_UI_SMOKE / "t112-authorization-gate.png"
        )
        agent_run_binding = _verify_agent_run_binding(evidence_path)
        final_order = (
            _verify_final_compose_order(evidence_path)
            if full_chain or recovery_chain
            else None
        )
        manifest = _archive_execution_evidence(
            mode=(
                "recovery-chain"
                if recovery_chain
                else "full-chain"
                if full_chain
                else "draft"
            ),
            evidence_path=evidence_path,
            screenshot_path=screenshot_path,
            shot_count=normalized_shot_count,
            video_duration_seconds=normalized_duration,
        )
        print(
            json.dumps(
                {
                    "archive": manifest,
                    "agent_run_binding": agent_run_binding,
                    "final_order": final_order,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        server.should_exit = True
        _terminate_process_tree(vite)
        log = getattr(vite, "_t112_log", None)
        if log is not None:
            log.close()
        server_thread.join(timeout=10)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=5)
        root_logger.removeHandler(log_handler)
        log_handler.flush()
        log_handler.close()
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="check adapter inputs without starting servers or providers",
    )
    parser.add_argument(
        "--full-chain",
        action="store_true",
        help=(
            "use deterministic image/video HTTP responses and run the complete "
            "workflow through bundled FFmpeg"
        ),
    )
    parser.add_argument(
        "--recovery-chain",
        action="store_true",
        help=(
            "run the full chain with one injected video failure, then click the "
            "browser recovery authorization and verify the same Run completes"
        ),
    )
    parser.add_argument(
        "--archive-only",
        action="store_true",
        help=(
            "freeze evidence already produced by the native .cjs chain into a "
            "hash-pinned manifest, without starting servers or providers"
        ),
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        default=None,
        help="evidence JSON produced by the native .cjs chain (--archive-only)",
    )
    parser.add_argument(
        "--screenshot",
        type=Path,
        default=None,
        help="screenshot referenced by the evidence JSON (--archive-only)",
    )
    parser.add_argument(
        "--mode",
        choices=("draft", "full-chain", "recovery-chain"),
        default="full-chain",
        help="archive mode used by --archive-only",
    )
    parser.add_argument(
        "--shots",
        type=int,
        default=SHOT_COUNT_DEFAULT,
        help="number of script rows in the isolated full-chain run",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=VIDEO_DURATION_DEFAULT_SECONDS,
        help="requested seconds per shot video",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not SHOT_COUNT_MIN <= args.shots <= SHOT_COUNT_MAX:
        raise SystemExit(
            f"--shots must be between {SHOT_COUNT_MIN} and {SHOT_COUNT_MAX}"
        )
    if not VIDEO_DURATION_MIN_SECONDS <= args.duration <= VIDEO_DURATION_MAX_SECONDS:
        raise SystemExit(
            "--duration must be between "
            f"{VIDEO_DURATION_MIN_SECONDS} and {VIDEO_DURATION_MAX_SECONDS}"
        )
    if args.preflight and args.archive_only:
        raise SystemExit("--preflight and --archive-only are mutually exclusive")
    if args.archive_only:
        missing = [
            flag
            for flag, value in (
                ("--evidence", args.evidence),
                ("--screenshot", args.screenshot),
            )
            if value is None
        ]
        if missing:
            raise SystemExit("--archive-only requires " + " and ".join(missing))
        manifest = _archive_evidence(
            mode=args.mode,
            evidence_path=args.evidence,
            screenshot_path=args.screenshot,
            shot_count=args.shots,
            video_duration_seconds=args.duration,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    if args.preflight:
        report = build_preflight()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 2
    return _run_adapter(
        full_chain=args.full_chain or args.recovery_chain,
        recovery_chain=args.recovery_chain,
        shot_count=args.shots,
        duration_seconds=args.duration,
    )


if __name__ == "__main__":
    raise SystemExit(main())
