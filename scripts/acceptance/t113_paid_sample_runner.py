"""Real-provider runner for the T-113 one-shot L3 acceptance sample.

The module is import-safe and does not read provider credentials or start a
server until ``run_paid_sample`` is called with the exact authorization phrase.
The browser, FastAPI app, Hermes worker, WorkflowRun, generators and task
backend remain the product path; only the provider origins are replaced by
loopback budget proxies.
"""

from __future__ import annotations

import argparse
import hashlib
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
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "workspace"
ARTIFACT_DIR = WORKSPACE / "artifacts" / "t113-single-shot-paid-l3"
TARGET_UI_SMOKE = WORKSPACE / "ui-smoke-t113"
TARGET_STATE = TARGET_UI_SMOKE / "state"
PROJECT_STATE = TARGET_STATE / "local" / "ui_smoke_t091"
WORKFLOW_DATABASE = PROJECT_STATE / "workflow_runs.db"
API_PORT = int(os.environ.get("T113_API_PORT", "8794"))
VITE_PORT = int(os.environ.get("T113_VITE_PORT", "5194"))
API_BASE = f"http://127.0.0.1:{API_PORT}/api/v1"
UI_BASE = f"http://127.0.0.1:{VITE_PORT}"
RUNNER_SCHEMA = "t113_paid_sample_runner.v1"
PROVIDER_CONFIG_SCHEMA = "t113_provider_config_capture.v1"


def _chrome_path() -> Path:
    candidates = (
        Path(os.environ.get("PROGRAMFILES", ""))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", ""))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    discovered = shutil.which("chrome") or shutil.which("chrome.exe")
    return Path(discovered) if discovered else candidates[0]


CHROME = _chrome_path()
FFMPEG = ROOT / "runtime" / "ffmpeg" / "ffmpeg.exe"
PRODUCTION_SETTINGS_DB = ROOT / "项目资产" / "state" / "local" / "settings.db"
MEDIA_RELAY_SETTING_KEYS = (
    "media_relay_provider",
    "media_relay_ttl_seconds",
    "oss_relay_endpoint",
    "oss_relay_bucket",
    "oss_relay_ak",
    "oss_relay_sk",
    "cloudinary_relay_cloud_name",
    "cloudinary_relay_api_key",
    "cloudinary_relay_api_secret",
    "cloudinary_relay_folder",
)


def _read_media_relay_settings(database: Path) -> dict[str, str]:
    if not database.is_file():
        return {}
    connection = sqlite3.connect(str(database))
    try:
        placeholders = ",".join("?" for _ in MEDIA_RELAY_SETTING_KEYS)
        rows = connection.execute(
            f"SELECT key, value FROM runtime_settings WHERE key IN ({placeholders})",
            MEDIA_RELAY_SETTING_KEYS,
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}
    finally:
        connection.close()
    return {
        str(key): str(value or "")
        for key, value in rows
        if str(key or "") in MEDIA_RELAY_SETTING_KEYS
    }


def _media_relay_settings_complete(settings: dict[str, str]) -> bool:
    provider = str(settings.get("media_relay_provider") or "").strip().lower()
    if provider == "cloudinary":
        return all(
            str(settings.get(key) or "").strip()
            for key in (
                "cloudinary_relay_cloud_name",
                "cloudinary_relay_api_key",
                "cloudinary_relay_api_secret",
            )
        )
    if provider == "aliyun_oss":
        return all(
            str(settings.get(key) or "").strip()
            for key in (
                "oss_relay_endpoint",
                "oss_relay_bucket",
                "oss_relay_ak",
                "oss_relay_sk",
            )
        )
    return False


def production_media_relay_status(
    database: Path = PRODUCTION_SETTINGS_DB,
) -> dict[str, Any]:
    settings = _read_media_relay_settings(Path(database))
    return {
        "configured": _media_relay_settings_complete(settings),
        "provider": str(settings.get("media_relay_provider") or ""),
        "ttlSeconds": int(settings.get("media_relay_ttl_seconds") or 0),
        "keyCount": len(settings),
    }


def _copy_production_media_relay_settings(
    destination_state: Path,
    *,
    source_database: Path = PRODUCTION_SETTINGS_DB,
) -> dict[str, Any]:
    settings = _read_media_relay_settings(Path(source_database))
    configured = _media_relay_settings_complete(settings)
    if not configured:
        return {
            "copied": False,
            "configured": False,
            "provider": str(settings.get("media_relay_provider") or ""),
            "copiedKeys": 0,
        }
    destination = Path(destination_state) / "local" / "settings.db"
    destination.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(destination))
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT OR REPLACE INTO runtime_settings (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            """,
            [(key, value) for key, value in settings.items()],
        )
        connection.commit()
    finally:
        connection.close()
    return {
        "copied": True,
        "configured": True,
        "provider": str(settings.get("media_relay_provider") or ""),
        "copiedKeys": len(settings),
    }


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load acceptance module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_cleanup_temp_site(*, force: bool = False) -> dict[str, Any]:
    resolved = TARGET_UI_SMOKE.resolve()
    workspace = WORKSPACE.resolve()
    if resolved.parent != workspace:
        raise RuntimeError(f"refusing to clean path outside workspace: {resolved}")
    removed = resolved.exists()
    error = ""
    if removed and not force and os.environ.get("T113_PRESERVE_FAILED_SITE") == "1":
        return {
            "path": str(resolved),
            "removed": False,
            "remaining": True,
            "preserved": True,
            "error": "",
        }
    if removed:
        logging.shutdown()
        for attempt in range(12):
            try:
                shutil.rmtree(resolved)
                break
            except OSError as exc:
                error = f"{type(exc).__name__}: {str(exc)[:300]}"
                if attempt < 11:
                    time.sleep(min(0.5 * (attempt + 1), 3.0))
    return {
        "path": str(resolved),
        "removed": removed,
        "remaining": resolved.exists(),
        "preserved": False,
        "error": error,
    }


def _apply_cleanup_violation(
    validation: dict[str, Any],
    cleanup: dict[str, Any],
) -> dict[str, Any]:
    if cleanup.get("remaining") is not True:
        return validation
    violations = validation.setdefault("violations", [])
    if "temp_site_cleanup_failed" not in violations:
        violations.append("temp_site_cleanup_failed")
    validation["ok"] = False
    return validation


_PROVIDER_CONFIG_HELPER = r"""
import json
import os
from novelvideo.generators.direct_model_capability_cache import (
    get_cached_direct_model_capability,
)
from novelvideo.generators.video.direct_video_capability_cache import (
    get_cached_capability_for_model,
)
from novelvideo.model_gateway_settings import (
    get_direct_models,
    get_direct_video_models,
)

def pick(items, *, model_id, protocol=""):
    for item in items:
        if not isinstance(item, dict) or item.get("enabled") is not True:
            continue
        if str(item.get("modelId") or "") != model_id:
            continue
        if protocol and str(item.get("protocol") or "") != protocol:
            continue
        return dict(item)
    raise RuntimeError(f"model not found: {model_id} protocol={protocol}")


def pick_text_model(items):
    # Prefer an explicit operator choice, then the official runtime-verified
    # DeepSeek channel. Do not silently fall back to a quota-blocked Gemini row.
    candidates = [
        item
        for item in items
        if isinstance(item, dict) and item.get("enabled") is True
    ]
    preferred = str(os.environ.get("T113_TEXT_MODEL") or "").strip()
    ordered: list[str] = []
    if preferred:
        ordered.append(preferred)
    ordered.extend(
        model_id
        for model_id in (
            "deepseek-flash",
            "deepseek-v4.1-flash",
        )
        if model_id not in ordered
    )
    for model_id in ordered:
        for item in candidates:
            if str(item.get("modelId") or "") == model_id:
                return dict(item)
    available = ", ".join(
        sorted({str(item.get("modelId") or "") for item in candidates})
    ) or "<none>"
    raise RuntimeError(
        "no ready text model selected for T-113; set T113_TEXT_MODEL. "
        f"available={available}"
    )


text = pick_text_model(get_direct_models("chat"))
image = pick(get_direct_models("image"), model_id="gpt-image-2.5-sunburst")
video = pick(
    get_direct_video_models(),
    model_id="MiniMax-H3",
    protocol="minimax-video-v2",
)
payload = {
    "schema": "t113_provider_config_capture.v1",
    "text": {
        **text,
        "capability": get_cached_direct_model_capability(
            base_url=str(text["baseUrl"]),
            kind="chat",
            upstream_model=str(text["modelId"]),
        ),
    },
    "image": {
        **image,
        "capability": get_cached_direct_model_capability(
            base_url=str(image["baseUrl"]),
            kind="image",
            upstream_model=str(image["modelId"]),
        ),
    },
    "video": {
        **video,
        "capability": get_cached_capability_for_model(
            base_url=str(video["baseUrl"]),
            upstream_model=str(video["modelId"]),
        ),
    },
}
print(json.dumps(payload, ensure_ascii=False))
"""


def capture_provider_config(state_dir: Path) -> dict[str, Any]:
    """Read exact provider rows in a child process so keys never reach stdout."""

    environment = os.environ.copy()
    environment["NOVELVIDEO_STATE_DIR"] = str(Path(state_dir).resolve())
    environment["ST_EDITION"] = "ce"
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "src"), environment.get("PYTHONPATH", "")]
    )
    completed = subprocess.run(
        [sys.executable, "-c", _PROVIDER_CONFIG_HELPER],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "provider config capture failed: "
            + (completed.stderr or completed.stdout or "unknown error")[:2000]
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("provider config capture returned invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema") != PROVIDER_CONFIG_SCHEMA:
        raise RuntimeError("provider config capture returned an unexpected schema")
    for role in ("text", "image", "video"):
        item = payload.get(role)
        if (
            not isinstance(item, dict)
            or not str(item.get("baseUrl") or "").startswith(("http://", "https://"))
            or not str(item.get("apiKey") or "")
        ):
            raise RuntimeError(f"provider config capture is incomplete: {role}")
    return payload


def build_runner_preflight() -> dict[str, Any]:
    contract = _load_module(
        "t113_paid_sample_contract",
        Path(__file__).with_name("t113_single_shot_paid_l3.py"),
    )
    checks = []
    for name, path, expected in (
        ("chrome", CHROME, "file"),
        ("bundled_ffmpeg", FFMPEG, "file"),
        ("source_isolated_site", WORKSPACE / "ui-smoke-t096", "directory"),
    ):
        ok = path.is_dir() if expected == "directory" else path.is_file()
        checks.append({"name": name, "ok": ok, "path": str(path)})
    node = shutil.which("node")
    checks.append({"name": "node_runtime", "ok": bool(node), "path": str(node or "")})
    failed = [str(item["name"]) for item in checks if item["ok"] is not True]
    return {
        "schema": RUNNER_SCHEMA,
        "ok": not failed,
        "executionRunnerImplemented": True,
        "executionRunnerConnected": True,
        "paidProvidersConnected": False,
        "providerCallsStarted": False,
        "authorizationRequired": True,
        "authorizationPhrase": contract.AUTHORIZATION_PHRASE,
        "checks": checks,
        "failedChecks": failed,
        "blockingReasons": (
            [f"runner_preflight_failed:{name}" for name in failed]
            or ["t113_paid_authorization_required"]
        ),
    }


def _configure_t112_harness(
    t112: Any,
    *,
    cluster_factory: Any,
    seed_models: Any,
) -> None:
    t112.TARGET_UI_SMOKE = TARGET_UI_SMOKE
    t112.TARGET_STATE = TARGET_STATE
    t112.PROJECT_STATE = PROJECT_STATE
    t112.WORKFLOW_DATABASE = WORKFLOW_DATABASE
    t112.API_PORT = API_PORT
    t112.VITE_PORT = VITE_PORT
    t112.API_BASE = API_BASE
    t112.UI_BASE = UI_BASE
    t112.LocalUpstreamServer = cluster_factory
    t112._seed_local_upstream_models = seed_models

    def rewrite_isolated_paths() -> None:
        connection = sqlite3.connect(TARGET_STATE / "local" / "projects.db")
        try:
            for column in ("output_dir", "state_dir", "runtime_dir"):
                connection.execute(
                    f"UPDATE projects SET {column}=REPLACE({column}, ?, ?)",
                    ("ui-smoke-t096", "ui-smoke-t113"),
                )
            connection.commit()
        finally:
            connection.close()

    original_script_row = t112.T100._script_row

    t112._rewrite_isolated_paths = rewrite_isolated_paths
    t112.T100._script_row = lambda: build_t113_script_row(original_script_row())


def build_t113_base_script_row() -> dict[str, Any]:
    """Return the retired T-100 row as a local T-113 fixture."""

    return {
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
        "shot_prompt": (
            "[画面构图] 中景，人物略偏左，右侧留出暗房空间。 + "
            "[角色卡] [阿木: 短黑发，深蓝外套，手里握着相机。] + "
            "[主体/人物空间] 阿木站在暗房中央，相机贴近胸前。 + "
            "[微表情] 眼神克制，嘴角轻微收紧。 + "
            "[场景环境] 旧照相馆暗房，木架上挂着未冲洗的胶片。 + "
            "[光影几何] 红灯从左侧切过面部，背景沉入阴影。 + "
            "[视觉风格] 写实电影感，低饱和红色调。 + "
            "[技术参数] 35mm 胶片质感，浅景深。"
        ),
        "video_motion_prompt": (
            "[运镜轨迹] 镜头缓慢推进。 + "
            "[主体动作] 阿木抬起相机。 + "
            "[环境动态] 红灯轻微闪烁，雨声从窗外传入。 + "
            "[音效氛围] 雨声与快门声。 + "
            "[对话台词] 无对白。 + "
            "[时长] [时长：2s]"
        ),
    }


def build_t113_script_row(base_row: dict[str, Any]) -> dict[str, Any]:
    """Build the one-image, one-video sample without generating asset references."""

    row = dict(base_row)
    row.update(
        {
            "duration": 5,
            "visual_description": (
                "旧照相馆暗房里，一台旧相机放在铺着尘布的桌上，红灯从左侧照亮相机。"
            ),
            "character_1": "无",
            "character_description_1": "无",
            "scene_tags": "无",
            "prop_tags": "无",
            "shot": "特写",
            "character_action": "无",
            "emotion": "克制而怀念",
            "lighting_mood": "红色侧光",
            "sound": "雨声、快门声",
            "dialogue": "无",
            "shot_prompt": (
                "[画面构图] 低机位近景，旧相机位于画面中心偏左。 + "
                "[核心对象] 一台磨损的旧相机，金属接缝和皮革纹理清晰。 + "
                "[主体/人物空间] 相机位于木桌中央偏左，右侧留出暗房空间。 + "
                "[主体状态] 机身静置，表面覆盖细薄灰尘。 + "
                "[场景环境] 旧照相馆暗房，背景木架沉在阴影里。 + "
                "[光影几何] 红灯从左侧切过机身边缘，背景沉入阴影。 + "
                "[视觉风格] 写实电影感，低饱和红色调。 + "
                "[技术参数] 35mm 胶片质感，浅景深。"
            ),
            "video_motion_prompt": (
                "[运镜轨迹] 镜头缓慢推进。 + "
                "[主体动作] 相机静置不动，红灯亮度轻微起伏。 + "
                "[环境动态] 尘布边缘轻颤，雨声从窗外传入。 + "
                "[音效氛围] 雨声与机械快门声。 + "
                "[对话台词] 无对白。 + "
                "[时长] [时长：5s]"
            ),
        }
    )
    for key in (
        "character_image_1",
        "character_asset_id_1",
        "character_id_1",
        "scene_asset_ids",
        "scene_reference_urls",
        "scene_asset_content_hashes",
        "prop_asset_ids",
        "prop_reference_urls",
        "prop_asset_content_hashes",
    ):
        row.pop(key, None)
    return row


def _local_t113_configs() -> dict[str, Any]:
    return {
        "text": {
            "modelId": "deepseek-flash",
            "label": "T-113 local text",
            "apiKey": "t113-local-upstream-key",
            "capability": {
                "probeContractVersion": 1,
                "verificationStatus": "runtime-verified",
                "modelFound": True,
                "discoveredModelCount": 1,
                "chatProbeStatus": "passed",
                "chatResponseUsable": True,
                "streamProbeStatus": "passed",
                "streamResponseUsable": True,
            },
        },
        "image": {
            "modelId": "gpt-image-2.5-sunburst",
            "label": "T-113 local image",
            "apiKey": "t113-local-upstream-key",
            "capability": {
                "verificationStatus": "catalog-confirmed",
                "modelFound": True,
                "discoveredModelCount": 1,
                "modelMetadata": {
                    "supportedModes": ["textToImage", "imageToImage"],
                    "aspectRatioOptions": ["1:1", "16:9", "9:16", "4:3", "3:4"],
                    "resolutionOptions": ["1K"],
                },
            },
        },
        "video": {
            "modelId": "MiniMax-H3",
            "label": "T-113 local video",
            "apiKey": "t113-local-upstream-key",
            "capability": {
                "verificationStatus": "contract-resolved",
                "modelFound": True,
                "discoveredModelCount": 1,
                "supportedProtocols": ["minimax:video_generation_v2"],
                "modes": ["textToVideo", "imageToVideo"],
                "resolutionOptions": ["768p", "2k"],
                "aspectRatios": [
                    "21:9",
                    "16:9",
                    "4:3",
                    "1:1",
                    "3:4",
                    "9:16",
                ],
                "nativeAudio": "required",
                "referenceLimits": {
                    "inputImages": 2,
                    "referenceImages": 9,
                    "referenceVideos": 3,
                    "referenceAudios": 3,
                },
                "declaredCapabilities": ["durationOptions"],
                "durationOptions": [5],
            },
        },
    }


def reuse_production_text_model(configs: dict[str, Any]) -> dict[str, Any]:
    """Point the isolated sample at the operator's real, usable text model.

    The production registry holds a runtime-verified official
    ``deepseek-flash`` row. Reuse that usable credential instead of a
    quota-blocked Gemini row.
    """

    preferred = str(os.environ.get("T113_TEXT_MODEL") or "").strip()
    database = Path(
        os.environ.get("T113_PRODUCTION_SETTINGS_DB") or PRODUCTION_SETTINGS_DB
    )
    if not database.is_file():
        return configs
    connection = sqlite3.connect(str(database))
    try:
        row = connection.execute(
            "SELECT value FROM runtime_settings WHERE key='direct_chat_models'"
        ).fetchone()
    except sqlite3.DatabaseError:
        return configs
    finally:
        connection.close()
    if not row:
        return configs
    try:
        rows = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return configs
    if not isinstance(rows, list):
        return configs

    def _rank(item: dict[str, Any]) -> tuple[int, str]:
        model_id = str(item.get("modelId") or "")
        if preferred and model_id == preferred:
            return (0, model_id)
        if model_id == "deepseek-flash":
            return (1, model_id)
        if model_id == "deepseek-v4.1-flash":
            return (2, model_id)
        return (9, model_id)

    candidates = [
        item
        for item in rows
        if isinstance(item, dict)
        and item.get("enabled") is True
        and str(item.get("baseUrl") or "").startswith(("http://", "https://"))
        and str(item.get("apiKey") or "").strip()
    ]
    if not candidates:
        return configs
    selected = dict(min(candidates, key=_rank))
    selected.setdefault("protocol", "openai-compatible")
    configs = dict(configs)
    configs["text"] = selected
    return configs


def production_text_endpoint() -> dict[str, str]:
    """Return the operator's usable text row, or an empty mapping."""

    database = Path(
        os.environ.get("T113_PRODUCTION_SETTINGS_DB") or PRODUCTION_SETTINGS_DB
    )
    if not database.is_file():
        return {}
    connection = sqlite3.connect(str(database))
    try:
        row = connection.execute(
            "SELECT value FROM runtime_settings WHERE key='direct_chat_models'"
        ).fetchone()
    except sqlite3.DatabaseError:
        return {}
    finally:
        connection.close()
    if not row:
        return {}
    try:
        rows = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(rows, list):
        return {}
    preferred = str(os.environ.get("T113_TEXT_MODEL") or "").strip()

    def _rank(item: dict[str, Any]) -> tuple[int, str]:
        model_id = str(item.get("modelId") or "")
        if preferred and model_id == preferred:
            return (0, model_id)
        if model_id == "deepseek-flash":
            return (1, model_id)
        if model_id == "deepseek-v4.1-flash":
            return (2, model_id)
        return (9, model_id)

    candidates = [
        item
        for item in rows
        if isinstance(item, dict)
        and item.get("enabled") is True
        and str(item.get("baseUrl") or "").startswith(("http://", "https://"))
        and str(item.get("apiKey") or "").strip()
    ]
    if not candidates:
        return {}
    selected = min(candidates, key=_rank)
    base_url = str(selected.get("baseUrl") or "").rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3].rstrip("/")
    return {
        "modelId": str(selected.get("modelId") or ""),
        "baseUrl": base_url,
        "apiKey": str(selected.get("apiKey") or ""),
    }


def _seed_t113_local_models(base_url: str) -> None:
    root = str(base_url).rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    cluster = SimpleNamespace(
        text=SimpleNamespace(base_url=root),
        image=SimpleNamespace(base_url=root),
        video=SimpleNamespace(base_url=root),
    )
    _seed_t113_models(cluster, _local_t113_configs())


def _seed_t113_models(
    cluster: Any,
    configs: dict[str, Any],
    *,
    text_base_url: str | None = None,
) -> dict[str, Any]:
    from novelvideo.generators.direct_model_capability_cache import (
        record_direct_model_capability,
    )
    from novelvideo.generators.direct_model_probe import probe_direct_model_endpoint
    from novelvideo.generators.video.direct_video_capability_cache import (
        record_capability,
    )
    from novelvideo.model_gateway_settings import (
        save_direct_models,
        save_direct_video_models,
    )

    text = configs["text"]
    image = configs["image"]
    video = configs["video"]
    resolved_text_base_url = str(
        text_base_url or text.get("baseUrl") or cluster.text.base_url
    ).rstrip("/")
    if not resolved_text_base_url.endswith("/v1"):
        resolved_text_base_url += "/v1"
    text_chat_base_url = resolved_text_base_url
    save_direct_models(
        "chat",
        [
            {
                "id": "t113-agent",
                "label": str(text.get("label") or "T-113 text"),
                "modelId": str(text["modelId"]),
                "baseUrl": text_chat_base_url,
                "apiKey": str(text["apiKey"]),
                "enabled": True,
                "isDefault": True,
                "protocol": "openai-compatible",
            }
        ],
        confirm_clear=True,
    )
    chat_probe = probe_direct_model_endpoint(
        upstream_model=str(text["modelId"]),
        base_url=text_chat_base_url,
        api_key=str(text["apiKey"]),
        protocol="openai-compatible",
        kind="chat",
        timeout=90.0,
    )
    if chat_probe.get("ok") is not True:
        probe_failure = {
            key: chat_probe.get(key)
            for key in (
                "ok",
                "modelId",
                "modelFound",
                "discoveredModelCount",
                "verificationStatus",
                "chatProbeStatus",
                "chatProbeMode",
                "chatHttpStatus",
                "chatResponseUsable",
                "chatFirstTokenLatencyMs",
                "streamProbeStatus",
                "streamHttpStatus",
                "streamResponseUsable",
                "streamFirstEventLatencyMs",
                "error",
                "chatProbeError",
                "streamProbeError",
            )
            if chat_probe.get(key) is not None
        }
        probe_failure["modelId"] = str(text["modelId"])
        raise RuntimeError(
            "T-113 text runtime probe failed: "
            + json.dumps(probe_failure, ensure_ascii=False)
        )
    record_direct_model_capability(
        base_url=text_chat_base_url,
        kind="chat",
        upstream_model=str(text["modelId"]),
        protocol=str(chat_probe.get("protocol") or "openai-compatible"),
        capability={
            key: chat_probe.get(key)
            for key in (
                "probeContractVersion",
                "verificationStatus",
                "detectedProtocol",
                "modelFound",
                "discoveredModelCount",
                "modelMetadata",
                "chatProbeStatus",
                "chatHttpStatus",
                "chatFirstTokenLatencyMs",
                "chatResponseUsable",
                "chatProbeError",
                "streamProbeStatus",
                "streamHttpStatus",
                "streamFirstEventLatencyMs",
                "streamResponseUsable",
                "streamProbeError",
            )
        },
    )

    save_direct_models(
        "image",
        [
            {
                "id": "t113-image",
                "label": str(image.get("label") or "T-113 image"),
                "modelId": str(image["modelId"]),
                "baseUrl": cluster.image.base_url + "/v1",
                "apiKey": str(image["apiKey"]),
                "enabled": True,
                "isDefault": True,
                "protocol": "openai-images",
            }
        ],
        confirm_clear=True,
    )
    record_direct_model_capability(
        base_url=cluster.image.base_url + "/v1",
        kind="image",
        upstream_model=str(image["modelId"]),
        protocol="openai-images",
        capability=dict(image.get("capability") or {}),
    )

    save_direct_video_models(
        [
            {
                "id": "t113-video",
                "label": str(video.get("label") or "T-113 video"),
                "modelId": str(video["modelId"]),
                "baseUrl": cluster.video.base_url + "/v1",
                "apiKey": str(video["apiKey"]),
                "enabled": True,
                "isDefault": True,
                "protocol": "minimax-video-v2",
            }
        ],
        confirm_clear=True,
    )
    record_capability(
        base_url=cluster.video.base_url + "/v1",
        protocol="minimax-video-v2",
        upstream_model=str(video["modelId"]),
        capability=dict(video.get("capability") or {}),
    )
    return chat_probe


def _latest_run() -> dict[str, Any]:
    connection = sqlite3.connect(WORKFLOW_DATABASE)
    try:
        row = connection.execute(
            "SELECT id, workflow_id, run_mode, status, revision, event_seq, "
            "error_code, next_action, project_id, canvas_id, "
            "inputs_json, artifacts_json "
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
        "project_id",
        "canvas_id",
    )
    result = dict(zip(fields, row[:10], strict=True))
    result["inputs"] = json.loads(row[10])
    result["artifacts"] = json.loads(row[11])
    return result


def _copy_final_film(
    run: dict[str, Any],
    *,
    filename: str = "sample.mp4",
) -> dict[str, Any]:
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    final_film = artifacts.get("final_film") if isinstance(artifacts, dict) else {}
    final_film = final_film if isinstance(final_film, dict) else {}
    final_artifact = final_film.get("final_compose_artifact")
    final_artifact = final_artifact if isinstance(final_artifact, dict) else {}
    source = Path(str(final_artifact.get("path") or ""))
    output_root = (TARGET_UI_SMOKE / "output").resolve()
    if not source.is_file() or not source.resolve().is_relative_to(output_root):
        raise RuntimeError(
            f"final artifact is missing or outside isolated output: {source}"
        )
    destination = ARTIFACT_DIR / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    digest = _sha256(destination)
    return {
        "source_path": str(source),
        "archived_path": str(destination),
        "sha256": digest,
        "declared_sha256": str(final_artifact.get("sha256") or ""),
        "size_bytes": destination.stat().st_size,
        "width": int(final_artifact.get("width") or 0),
        "height": int(final_artifact.get("height") or 0),
        "duration_seconds": float(final_artifact.get("duration_seconds") or 0),
    }


def _grant_usage_snapshot() -> dict[str, Any]:
    """Read grant consumption without persisting one-time grant identifiers."""

    database = TARGET_STATE / "local" / "_approvals" / "approvals.db"
    if not database.is_file():
        return {"database_present": False, "grants": [], "use_count": 0}
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        grants = connection.execute(
            "SELECT id, max_starts, used_starts, project_id, canvas_id "
            "FROM chat_paid_media_grants ORDER BY created_at DESC"
        ).fetchall()
        uses = connection.execute(
            "SELECT grant_id, COUNT(*) AS use_count "
            "FROM chat_paid_media_grant_uses GROUP BY grant_id"
        ).fetchall()
    finally:
        connection.close()
    uses_by_grant = {str(row["grant_id"]): int(row["use_count"] or 0) for row in uses}
    return {
        "database_present": True,
        "grants": [
            {
                "id_prefix": str(row["id"])[:4],
                "max_starts": int(row["max_starts"] or 0),
                "used_starts": int(row["used_starts"] or 0),
                "use_count": uses_by_grant.get(str(row["id"]), 0),
                "project_id": str(row["project_id"] or ""),
                "canvas_id": str(row["canvas_id"] or ""),
            }
            for row in grants
        ],
        "use_count": sum(uses_by_grant.values()),
    }


def _media_request_counts(
    browser_evidence: dict[str, Any],
) -> dict[str, Any]:
    requests = browser_evidence.get("upstream_media_requests")
    requests = requests if isinstance(requests, list) else []
    image_starts: list[str] = []
    video_starts: list[str] = []
    video_queries: list[str] = []
    image_requests: list[dict[str, Any]] = []
    video_requests: list[dict[str, Any]] = []
    for item in requests:
        if not isinstance(item, dict):
            continue
        method = str(item.get("method") or "").upper()
        path = str(item.get("path") or "")
        if method == "POST" and any(
            marker in path for marker in ("/images/", "/images", "/image/")
        ):
            image_starts.append(path)
            image_requests.append(dict(item))
        if method == "POST" and ("/videos" in path or "video_generation" in path):
            video_starts.append(path)
            video_requests.append(dict(item))
        if method == "GET" and "/query/video_generation/" in path:
            video_queries.append(path)
    return {
        "image_start_paths": image_starts,
        "video_start_paths": video_starts,
        "video_query_paths": video_queries,
        "image_requests": image_requests,
        "video_requests": video_requests,
    }


def _image_request_matches_aspect_ratio(
    request: dict[str, Any],
    expected: str,
) -> bool:
    declared = str(request.get("aspect_ratio") or "").strip()
    if declared == expected:
        return True

    expected_match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*",
        expected,
    )
    size_match = re.fullmatch(
        r"\s*(\d+)\s*[xX×]\s*(\d+)\s*",
        str(request.get("size") or ""),
    )
    if expected_match is None or size_match is None:
        return False
    expected_width, expected_height = (
        float(value) for value in expected_match.groups()
    )
    width, height = (int(value) for value in size_match.groups())
    if expected_width <= 0 or expected_height <= 0 or width <= 0 or height <= 0:
        return False
    expected_ratio = expected_width / expected_height
    actual_ratio = width / height
    return abs(actual_ratio - expected_ratio) <= expected_ratio * 0.02


def _video_start_requests(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [
        dict(item)
        for item in items
        if isinstance(item, dict)
        and str(item.get("method") or "").upper() == "POST"
        and (
            "/videos" in str(item.get("path") or "")
            or "video_generation" in str(item.get("path") or "")
        )
    ]


def _budget_count_ok(value: Any, limit: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return False
    return limit is None or (
        not isinstance(limit, bool) and isinstance(limit, int) and value <= limit
    )


def _budget_counter_delta(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    """Return only the provider starts attributable to the current run."""

    before_counters = (
        before.get("counters") if isinstance(before.get("counters"), dict) else {}
    )
    after_counters = (
        after.get("counters") if isinstance(after.get("counters"), dict) else {}
    )
    counters: dict[str, Any] = {}
    for key in ("textRequests", "imageTaskStarts", "videoTaskStarts"):
        start = before_counters.get(key, 0)
        end = after_counters.get(key, 0)
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or start < 0
            or isinstance(end, bool)
            or not isinstance(end, int)
            or end < start
        ):
            counters[key] = None
        else:
            counters[key] = end - start
    return {
        "schema": "t113_provider_run_budget.v1",
        "counters": counters,
    }


def _validate_video_start_failure(
    *,
    adapter_exception: Exception | None,
    run: dict[str, Any],
    browser_failure_evidence: dict[str, Any],
    budget: dict[str, Any],
    proxy_snapshot: list[dict[str, Any]],
    upstream_snapshot: list[dict[str, Any]],
    contract: Any,
) -> dict[str, Any]:
    """Prove a forwarded video 502 fails closed without a second paid start."""

    violations: list[str] = []
    if adapter_exception is None:
        violations.append("failure_was_not_observed")
    if browser_failure_evidence.get("schema") != (
        "t112_real_execution_adapter_failure.v1"
    ):
        violations.append("browser_failure_evidence_missing")
    browser_requests = browser_failure_evidence.get("browserRequests")
    browser_requests = browser_requests if isinstance(browser_requests, list) else []
    if len(browser_requests) != 1:
        violations.append("browser_structured_request_count")

    if run.get("workflow_id") != "freezone-final-film":
        violations.append("run_workflow_id")
    if run.get("status") != "failed":
        violations.append("run_not_failed")
    if run.get("error_code") != "workflow_shot_video_failed":
        violations.append("run_error_code")
    if run.get("next_action") != "recover:retry_failed_items:shot_videos":
        violations.append("run_recovery_action")

    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    shot_videos = (
        artifacts.get("shot_videos")
        if isinstance(artifacts.get("shot_videos"), dict)
        else {}
    )
    if shot_videos.get("status") != "failed":
        violations.append("shot_videos_not_failed")
    if not shot_videos.get("failed_items"):
        violations.append("shot_videos_failed_items_missing")

    counters = (
        budget.get("counters") if isinstance(budget.get("counters"), dict) else {}
    )
    text_requests = counters.get("textRequests")
    image_task_starts = counters.get("imageTaskStarts")
    video_task_starts = counters.get("videoTaskStarts")
    if not _budget_count_ok(text_requests, contract.TEXT_REQUEST_LIMIT):
        violations.append("text_request_budget")
    if image_task_starts != contract.EXPECTED_IMAGE_TASK_STARTS:
        violations.append("image_task_start_count")
    if video_task_starts != contract.EXPECTED_VIDEO_TASK_STARTS:
        violations.append("video_task_start_count")

    upstream_video_starts = _video_start_requests(upstream_snapshot)
    proxy_video_starts = _video_start_requests(proxy_snapshot)
    forwarded_video_starts = [
        item for item in proxy_video_starts if item.get("budgetReserved") is True
    ]
    if len(upstream_video_starts) != 1:
        violations.append("upstream_video_start_count")
    if len(forwarded_video_starts) != 1:
        violations.append("proxy_forwarded_video_start_count")
    if len(upstream_video_starts) == 1 and upstream_video_starts[0].get(
        "status"
    ) not in (None, 502):
        violations.append("upstream_video_failure_not_injected")

    return {
        "ok": not violations,
        "violations": violations,
        "failureHandledSafely": not violations,
        "textRequests": text_requests,
        "imageTaskStarts": image_task_starts,
        "videoTaskStarts": video_task_starts,
        "upstreamVideoStarts": len(upstream_video_starts),
        "proxyForwardedVideoStarts": len(forwarded_video_starts),
    }


def _validate_recovery_execution(
    *,
    browser_evidence: dict[str, Any],
    run: dict[str, Any],
    final_film: dict[str, Any],
    budget: dict[str, Any],
    contract: Any,
    proxy_snapshot: list[dict[str, Any]],
    upstream_snapshot: list[dict[str, Any]],
    grant_state: dict[str, Any],
) -> dict[str, Any]:
    """Prove the second video start is the exact failed item on the same Run."""

    violations: list[str] = []
    recovery = (
        browser_evidence.get("recovery_chain")
        if isinstance(browser_evidence.get("recovery_chain"), dict)
        else {}
    )
    failed_run = (
        recovery.get("failed_run")
        if isinstance(recovery.get("failed_run"), dict)
        else {}
    )
    failed_item_ids = [
        str(item_id).strip()
        for item_id in (recovery.get("failed_item_ids") or [])
        if str(item_id or "").strip()
    ]
    if browser_evidence.get("schema") != (
        "t112_real_execution_adapter_recovery_chain.v1"
    ):
        violations.append("recovery_browser_evidence_missing")
    if str(failed_run.get("id") or "") != str(run.get("id") or ""):
        violations.append("failed_and_completed_run_id")
    if recovery.get("same_run_id") is not True:
        violations.append("run_id_changed_after_recovery")
    if failed_run.get("status") != "failed":
        violations.append("first_turn_not_failed")
    if failed_run.get("error_code") != "workflow_shot_video_failed":
        violations.append("first_turn_error_code")
    if failed_run.get("next_action") != "recover:retry_failed_items:shot_videos":
        violations.append("first_turn_recovery_action")
    if len(failed_item_ids) != 1:
        violations.append("failed_item_scope")
    browser_requests = browser_evidence.get("browser_structured_requests")
    browser_requests = browser_requests if isinstance(browser_requests, list) else []
    if len(browser_requests) != 2:
        violations.append("browser_recovery_turn_count")
    elif (
        str(
            browser_requests[1].get("workflow_runtime", {}).get("workflow_run_id") or ""
        )
        != str(run.get("id") or "")
        or browser_requests[1].get("task_authorization", {}).get("max_paid_starts") != 1
    ):
        violations.append("browser_recovery_scope")
    if run.get("workflow_id") != "freezone-final-film":
        violations.append("wrong_workflow")
    if run.get("status") != "completed":
        violations.append("run_not_completed")
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    shot_videos = (
        artifacts.get("shot_videos")
        if isinstance(artifacts.get("shot_videos"), dict)
        else {}
    )
    if shot_videos.get("status") != "completed":
        violations.append("shot_videos_not_completed")
    if shot_videos.get("media_authorization"):
        violations.append("media_authorization_marker_not_cleared")
    item_states = (
        shot_videos.get("item_states")
        if isinstance(shot_videos.get("item_states"), dict)
        else {}
    )
    completed_items = [
        item_state
        for item_state in item_states.values()
        if isinstance(item_state, dict) and item_state.get("status") == "completed"
    ]
    if len(completed_items) != 1 or len(item_states) != 1:
        violations.append("completed_failed_item_scope")
    videos = (
        shot_videos.get("videos") if isinstance(shot_videos.get("videos"), list) else []
    )
    if not videos:
        violations.append("shot_video_artifact_missing")
    elif len(failed_item_ids) == 1 and str(
        videos[0].get("item_id") or videos[0].get("node_id") or ""
    ) not in {failed_item_ids[0], ""}:
        violations.append("recovered_video_identity")

    counters = (
        budget.get("counters") if isinstance(budget.get("counters"), dict) else {}
    )
    text_requests = counters.get("textRequests")
    image_task_starts = counters.get("imageTaskStarts")
    video_task_starts = counters.get("videoTaskStarts")
    if not _budget_count_ok(text_requests, contract.TEXT_REQUEST_LIMIT):
        violations.append("text_request_budget")
    if image_task_starts != contract.EXPECTED_IMAGE_TASK_STARTS:
        violations.append("image_task_start_count")
    if video_task_starts != 2:
        violations.append("video_task_start_count")

    proxy_video_starts = [
        item
        for item in _video_start_requests(proxy_snapshot)
        if item.get("budgetReserved") is True
    ]
    upstream_video_starts = _video_start_requests(upstream_snapshot)
    if [item.get("status") for item in proxy_video_starts] != [502, 200]:
        violations.append("proxy_video_recovery_sequence")
    if len(upstream_video_starts) != 2:
        violations.append("upstream_video_start_count")
    if len(_video_start_requests(proxy_snapshot)) != 2:
        violations.append("proxy_video_start_count")

    grants = (
        grant_state.get("grants") if isinstance(grant_state.get("grants"), list) else []
    )
    recovery_grants = [
        grant
        for grant in grants
        if isinstance(grant, dict) and grant.get("max_starts") == 1
    ]
    if (
        len(recovery_grants) != 1
        or recovery_grants[0].get("used_starts") != 1
        or recovery_grants[0].get("use_count") != 1
    ):
        violations.append("paid_grant_consumption")
    else:
        recovery_grant = recovery_grants[0]
        if str(recovery_grant.get("project_id") or "") != str(
            run.get("project_id") or ""
        ):
            violations.append("paid_grant_project")
        if str(recovery_grant.get("canvas_id") or "") != str(
            run.get("canvas_id") or ""
        ):
            violations.append("paid_grant_canvas")
    allocated_starts = sum(
        int(grant.get("used_starts") or 0)
        for grant in grants
        if isinstance(grant, dict)
    )
    if int(grant_state.get("use_count") or 0) != allocated_starts:
        violations.append("paid_grant_consumption")

    if final_film.get("sha256") != final_film.get("declared_sha256"):
        violations.append("final_film_sha256")
    if (
        int(final_film.get("size_bytes") or 0) <= 0
        or int(final_film.get("width") or 0) <= 0
        or int(final_film.get("height") or 0) <= 0
        or float(final_film.get("duration_seconds") or 0) <= 0
    ):
        violations.append("final_film_metadata")
    if browser_evidence.get("browser_errors"):
        violations.append("browser_errors")
    if browser_evidence.get("workflow_canvas_writes"):
        violations.append("workflow_canvas_writes")

    return {
        "ok": not violations,
        "violations": violations,
        "sameRun": not (
            "failed_and_completed_run_id" in violations
            or "run_id_changed_after_recovery" in violations
        ),
        "failedItemIds": failed_item_ids,
        "videoTaskStarts": video_task_starts,
        "proxyVideoStatuses": [item.get("status") for item in proxy_video_starts],
        "grantUsage": grant_state,
    }


def run_local_full_chain(
    *,
    through_proxy: bool = False,
    video_failures_remaining: int = 0,
    recovery_chain: bool = False,
    real_text: bool = False,
) -> dict[str, Any]:
    """Exercise the exact T-113 fixture and native MiniMax contract for free."""

    if real_text and not through_proxy:
        return {
            "schema": "t113_real_text_proxy_media.v1",
            "ok": False,
            "paidProvidersConnected": False,
            "providerCallsStarted": False,
            "blockingReasons": ["real_text_requires_proxy_media"],
        }

    expect_video_failure = int(video_failures_remaining) > 0 and not recovery_chain
    if expect_video_failure and not through_proxy:
        return {
            "schema": "t113_local_video_failure.v1",
            "ok": False,
            "failureInjected": True,
            "failureHandledSafely": False,
            "paidProvidersConnected": False,
            "providerCallsStarted": False,
            "blockingReasons": ["video_failure_requires_proxy"],
        }
    preflight = build_runner_preflight()
    if preflight.get("ok") is not True:
        return {
            "schema": (
                "t113_local_proxy_video_failure.v1"
                if expect_video_failure
                else "t113_local_proxy_full_chain.v1"
                if through_proxy
                else "t113_local_full_chain.v1"
            ),
            "ok": False,
            "failureInjected": expect_video_failure,
            "failureHandledSafely": False,
            "paidProvidersConnected": False,
            "providerCallsStarted": False,
            "preflight": preflight,
            "blockingReasons": list(preflight.get("blockingReasons") or []),
        }

    t112 = _load_module(
        "t112_real_execution_adapter_for_t113_local",
        Path(__file__).with_name("t112_real_execution_adapter.py"),
    )
    local_upstream_class = t112.LocalUpstreamServer
    proxy_module = None
    contract = None
    budget = None
    local_upstream = None
    local_upstream_thread = None
    if through_proxy:
        proxy_module = _load_module(
            "t113_real_provider_proxy_for_local_proxy",
            Path(__file__).with_name("t113_real_provider_proxy.py"),
        )
        contract = _load_module(
            "t113_paid_sample_contract_for_local_proxy",
            Path(__file__).with_name("t113_single_shot_paid_l3.py"),
        )
        budget_path = ARTIFACT_DIR / "provider-budget.local-proxy.json"
        budget_path.parent.mkdir(parents=True, exist_ok=True)
        budget_path.unlink(missing_ok=True)
        plan = contract.build_plan()
        if recovery_chain:
            plan["providerStartLimits"]["videoTaskStarts"] = 2
            plan["expectedPaidTaskStarts"]["video"] = 2
            plan["automaticProviderRetry"] = False
            plan["isolatedRecoveryFixture"] = True
        budget = proxy_module.BudgetJournal(
            budget_path,
            plan=plan,
            reserve_start=contract.reserve_provider_start,
        )
        local_upstream = local_upstream_class(
            full_chain=True,
            native_minimax_video=True,
            video_failures_remaining=(
                1 if recovery_chain else int(video_failures_remaining)
            ),
        )
        local_upstream_thread = threading.Thread(
            target=local_upstream.serve_forever,
            name="t113-local-provider-upstream",
            daemon=True,
        )
        local_upstream_thread.start()
    proxy_holder: dict[str, Any] = {}

    def cluster_factory(
        full_chain: bool = True,
        shot_count: int = 1,
        video_duration_seconds: int = 5,
    ) -> Any:
        if int(shot_count) != 1 or int(video_duration_seconds) != 5:
            raise RuntimeError("T-113 paid L3 is single-shot and fixed at 5 seconds")
        if through_proxy:
            assert proxy_module is not None
            assert budget is not None
            assert local_upstream is not None
            text_endpoint = production_text_endpoint() if real_text else {}
            cluster = proxy_module.ProviderProxyCluster(
                text_base_url=(
                    text_endpoint.get("baseUrl")
                    or f"http://127.0.0.1:{local_upstream.port}"
                ),
                text_api_key=(text_endpoint.get("apiKey") or "t113-local-proxy-key"),
                image_base_url=f"http://127.0.0.1:{local_upstream.port}",
                image_api_key="t113-local-proxy-key",
                video_base_url=f"http://127.0.0.1:{local_upstream.port}",
                video_api_key="t113-local-proxy-key",
                budget_journal=budget,
                full_chain=full_chain,
            )
            proxy_holder["cluster"] = cluster
            proxy_holder["text_endpoint"] = text_endpoint
            return cluster
        return local_upstream_class(
            full_chain=full_chain,
            native_minimax_video=True,
        )

    def seed_models(base_url: str) -> None:
        if through_proxy:
            cluster = proxy_holder.get("cluster")
            if cluster is None:
                raise RuntimeError("T-113 local proxy cluster was not created")
            configs = _local_t113_configs()
            text_endpoint = proxy_holder.get("text_endpoint") or {}
            if real_text and text_endpoint.get("modelId"):
                configs["text"]["modelId"] = str(text_endpoint["modelId"])
                configs["text"]["label"] = "T-113 real text"
                configs["text"]["apiKey"] = str(text_endpoint["apiKey"])
            _seed_t113_models(
                cluster,
                configs,
                text_base_url=str(cluster.text.base_url),
            )
            return
        _seed_t113_local_models(str(base_url))

    _configure_t112_harness(
        t112,
        cluster_factory=cluster_factory,
        seed_models=seed_models,
    )
    os.environ["T112_MAX_PAID_STARTS"] = "4"
    os.environ["T112_COMPLETED_WAIT_MS"] = "900000"
    adapter_exception: Exception | None = None
    run: dict[str, Any] = {}
    browser_evidence: dict[str, Any] = {}
    browser_failure_evidence: dict[str, Any] = {}
    final_film: dict[str, Any] = {}
    counts: dict[str, Any] = {}
    proxy_snapshot: list[dict[str, Any]] = []
    upstream_snapshot: list[dict[str, Any]] = []
    budget_snapshot: dict[str, Any] = {}
    grant_snapshot: dict[str, Any] = {}
    validation: dict[str, Any] = {
        "ok": False,
        "violations": ["failure_harness_not_completed"],
    }
    cleanup: dict[str, Any] = {}
    try:
        try:
            exit_code = t112._run_adapter(
                full_chain=True,
                recovery_chain=recovery_chain,
            )
            if exit_code != 0:
                raise RuntimeError(f"T-113 local full chain exit={exit_code}")
        except Exception as exc:
            adapter_exception = exc

        if recovery_chain:
            if adapter_exception is not None:
                raise adapter_exception
            run = _latest_run()
            browser_evidence = json.loads(
                (TARGET_UI_SMOKE / "t112-full-chain-evidence.json").read_text(
                    encoding="utf-8"
                )
            )
            final_film = _copy_final_film(
                run,
                filename="local-proxy-recovery.mp4",
            )
            counts = _media_request_counts(browser_evidence)
            assert contract is not None
            assert budget is not None
            proxy_snapshot = list(proxy_holder["cluster"].snapshot())
            upstream_snapshot = list(local_upstream.snapshot())
            budget_snapshot = budget.snapshot()
            grant_snapshot = _grant_usage_snapshot()
            validation = _validate_recovery_execution(
                browser_evidence=browser_evidence,
                run=run,
                final_film=final_film,
                budget=budget_snapshot,
                contract=contract,
                proxy_snapshot=proxy_snapshot,
                upstream_snapshot=upstream_snapshot,
                grant_state=grant_snapshot,
            )
        elif expect_video_failure:
            if adapter_exception is None:
                raise RuntimeError(
                    "video failure injection unexpectedly completed the full chain"
                )
            cluster = proxy_holder.get("cluster")
            if cluster is not None:
                proxy_snapshot = list(cluster.snapshot())
            if local_upstream is not None:
                upstream_snapshot = list(local_upstream.snapshot())
            if budget is not None:
                budget_snapshot = budget.snapshot()
            assert contract is not None
            try:
                run = _latest_run()
            except Exception as exc:
                adapter_exception = adapter_exception or exc
                run = {}
            try:
                browser_failure_evidence = json.loads(
                    (
                        TARGET_UI_SMOKE / "t112-full-chain-evidence-failure.json"
                    ).read_text(encoding="utf-8")
                )
            except Exception:
                browser_failure_evidence = {}
            validation = _validate_video_start_failure(
                adapter_exception=adapter_exception,
                run=run,
                browser_failure_evidence=browser_failure_evidence,
                budget=budget_snapshot,
                proxy_snapshot=proxy_snapshot,
                upstream_snapshot=upstream_snapshot,
                contract=contract,
            )
        else:
            if adapter_exception is not None:
                raise adapter_exception
            run = _latest_run()
            browser_evidence = json.loads(
                (TARGET_UI_SMOKE / "t112-full-chain-evidence.json").read_text(
                    encoding="utf-8"
                )
            )
            final_film = _copy_final_film(
                run,
                filename=(
                    "local-proxy-full-chain.mp4"
                    if through_proxy
                    else "local-full-chain.mp4"
                ),
            )
            counts = _media_request_counts(browser_evidence)
            if through_proxy:
                assert contract is not None
                assert budget is not None
                validation = _validate_execution(
                    browser_evidence=browser_evidence,
                    run=run,
                    budget=budget.snapshot(),
                    contract=contract,
                )
            else:
                task_types = [
                    str(item.get("task_type") or "")
                    for item in (browser_evidence.get("task_submissions") or [])
                    if isinstance(item, dict)
                ]
                violations: list[str] = []
                run_inputs = (
                    run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
                )
                expected_inputs = {
                    "aspect_ratio": "16:9",
                    "image_size": "1K",
                    "quality": "low",
                    "video_resolution": "768p",
                    "video_duration_seconds": 5,
                    "output_resolution": "1366x768",
                }
                for key, expected in expected_inputs.items():
                    if run_inputs.get(key) != expected:
                        violations.append(f"run_input_{key}")
                if run.get("status") != "completed":
                    violations.append("run_not_completed")
                if task_types.count("freezone_gen") != 1:
                    violations.append("image_task_start_count")
                if task_types.count("freezone_video_gen") != 1:
                    violations.append("video_task_start_count")
                if counts["image_start_paths"] != ["/v1/images/generations"]:
                    violations.append("image_provider_path")
                if counts["video_start_paths"] != ["/v2/video_generation"]:
                    violations.append("native_minimax_video_path")
                if not counts["video_query_paths"]:
                    violations.append("native_minimax_query_path")
                image_requests = counts.get("image_requests") or []
                if len(image_requests) != 1:
                    violations.append("image_provider_request_record")
                else:
                    image_request = image_requests[0]
                    if not _image_request_matches_aspect_ratio(
                        image_request,
                        "16:9",
                    ):
                        violations.append("image_provider_aspect_ratio")
                    image_size = str(image_request.get("image_size") or "")
                    if image_size != "1K" and "x" not in str(
                        image_request.get("size") or ""
                    ):
                        violations.append("image_provider_image_size")
                video_requests = counts.get("video_requests") or []
                if len(video_requests) != 1:
                    violations.append("video_provider_request_record")
                else:
                    video_request = video_requests[0]
                    if str(video_request.get("resolution") or "").casefold() != "768p":
                        violations.append("video_provider_resolution")
                    if str(video_request.get("ratio") or "").casefold() != "adaptive":
                        violations.append("video_provider_ratio")
                    if int(video_request.get("duration") or 0) != 5:
                        violations.append("video_provider_duration")
                    if "first_frame" not in (video_request.get("content_roles") or []):
                        violations.append("video_provider_first_frame_role")
                if browser_evidence.get("browser_errors"):
                    violations.append("browser_errors")
                if browser_evidence.get("workflow_canvas_writes"):
                    violations.append("workflow_canvas_writes")
                validation = {
                    "ok": not violations,
                    "violations": violations,
                    "taskTypes": task_types,
                }
    finally:
        cluster = proxy_holder.get("cluster")
        if cluster is not None and not proxy_snapshot:
            proxy_snapshot = list(cluster.snapshot())
        if local_upstream is not None and not upstream_snapshot:
            upstream_snapshot = list(local_upstream.snapshot())
        if budget is not None and not budget_snapshot:
            budget_snapshot = budget.snapshot()
        if local_upstream is not None:
            local_upstream.shutdown()
            local_upstream.server_close()
        if local_upstream_thread is not None:
            local_upstream_thread.join(timeout=5)
        cleanup = _safe_cleanup_temp_site(force=adapter_exception is None)
    _apply_cleanup_violation(validation, cleanup)

    return {
        "schema": (
            "t113_local_proxy_video_recovery.v1"
            if recovery_chain
            else "t113_local_proxy_video_failure.v1"
            if expect_video_failure
            else "t113_local_proxy_full_chain.v1"
            if through_proxy
            else "t113_local_full_chain.v1"
        ),
        "ok": validation["ok"],
        "failureInjected": recovery_chain or expect_video_failure,
        "recoveryChain": recovery_chain,
        "failureHandledSafely": (validation.get("failureHandledSafely") is True),
        "paidProvidersConnected": False,
        "providerCallsStarted": False,
        "isolation": {
            "targetUiSmoke": str(TARGET_UI_SMOKE),
            "cleanup": cleanup,
        },
        "run": run,
        "browserEvidence": (
            browser_failure_evidence if expect_video_failure else browser_evidence
        ),
        "browserFailureEvidence": browser_failure_evidence,
        "finalFilm": final_film,
        "mediaRequests": counts,
        "proxySnapshot": proxy_snapshot,
        "upstreamSnapshot": upstream_snapshot,
        "validation": validation,
        "budget": budget_snapshot
        or (budget.snapshot() if budget is not None else None),
        "grantUsage": grant_snapshot,
        "adapterError": (
            {
                "type": type(adapter_exception).__name__,
                "message": str(adapter_exception)[:1000],
            }
            if adapter_exception is not None
            else None
        ),
    }


def _first_task_submission(
    tasks: list[Any],
    *,
    task_type: str,
) -> dict[str, Any]:
    for item in tasks:
        if not isinstance(item, dict):
            continue
        if str(item.get("task_type") or "") == task_type:
            return item
    return {}


def _task_receipt_violations(
    *,
    task: dict[str, Any],
    task_type: str,
    media_kind: str,
    run_id: str,
    require_provider_task_id: bool,
) -> list[str]:
    """Require receipts that make one paid media task auditable and billable."""

    prefix = "image" if media_kind == "image" else "video"
    violations: list[str] = []
    if not task or str(task.get("task_type") or "") != task_type:
        return [f"{prefix}_task_receipt"]

    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    task_id = str(task.get("task_id") or "").strip()
    job_id = str(task.get("job_id") or result.get("job_id") or "").strip()
    task_key = str(task.get("task_key") or "").strip()
    if not task_id or not job_id or not task_key:
        violations.append(f"{prefix}_task_identity")
    if str(task.get("status") or "") != "completed":
        violations.append(f"{prefix}_task_status")

    acceptance = task.get("task_acceptance_receipt")
    acceptance = acceptance if isinstance(acceptance, dict) else {}
    if (
        str(acceptance.get("schema") or "") != "task_acceptance_receipt.v1"
        or str(acceptance.get("status") or "") != "accepted"
        or str(acceptance.get("task_id") or "") != task_id
        or str(acceptance.get("task_key") or "") != task_key
        or str(acceptance.get("run_id") or "") != str(run_id or "")
    ):
        violations.append(f"{prefix}_task_acceptance_receipt")

    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    top_level_cost = task.get("production_cost_receipt")
    metadata_cost = metadata.get("production_cost_receipt")
    top_level_cost = top_level_cost if isinstance(top_level_cost, dict) else {}
    metadata_cost = metadata_cost if isinstance(metadata_cost, dict) else {}
    if not top_level_cost or not metadata_cost:
        violations.append(f"{prefix}_production_cost_receipt")
    cost = metadata_cost or top_level_cost
    if (
        str(cost.get("schema") or "") != "production_cost_receipt.v1"
        or str(cost.get("task_id") or "") != task_id
        or str(cost.get("media_kind") or "") != media_kind
        or str(cost.get("result_status") or "") != "completed"
        or isinstance(cost.get("quantity"), bool)
        or not isinstance(cost.get("quantity"), int)
        or int(cost.get("quantity") or 0) < 1
        or not str(cost.get("started_at") or "").strip()
        or not str(cost.get("completed_at") or "").strip()
        or isinstance(cost.get("duration_ms"), bool)
        or not isinstance(cost.get("duration_ms"), int)
        or int(cost.get("duration_ms") or -1) < 0
    ):
        violations.append(f"{prefix}_production_cost_fields")

    if not isinstance(metadata_cost.get("actual_cost"), dict):
        violations.append(f"{prefix}_actual_cost_field")
    if not isinstance(metadata_cost.get("wasted_cost"), dict):
        violations.append(f"{prefix}_wasted_cost_field")

    provider_task_id = str(
        result.get("provider_task_id")
        or metadata.get("provider_task_id")
        or metadata_cost.get("provider_task_id")
        or ""
    ).strip()
    if require_provider_task_id and not provider_task_id:
        violations.append(f"{prefix}_provider_task_id")
    return violations


def _validate_execution(
    *,
    browser_evidence: dict[str, Any],
    run: dict[str, Any],
    budget: dict[str, Any],
    contract: Any,
    text_probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tasks = browser_evidence.get("task_submissions")
    tasks = tasks if isinstance(tasks, list) else []
    task_types = [
        str(item.get("task_type") or "") for item in tasks if isinstance(item, dict)
    ]
    counters = (
        budget.get("counters") if isinstance(budget.get("counters"), dict) else {}
    )
    text_requests = counters.get("textRequests")
    image_task_starts = counters.get("imageTaskStarts")
    video_task_starts = counters.get("videoTaskStarts")
    counts = _media_request_counts(browser_evidence)
    violations: list[str] = []
    run_inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    expected_inputs = {
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "quality": "low",
        "video_resolution": "768p",
        "video_duration_seconds": 5,
        "output_resolution": "1366x768",
    }
    for key, expected in expected_inputs.items():
        if run_inputs.get(key) != expected:
            violations.append(f"run_input_{key}")
    if run.get("workflow_id") != "freezone-final-film":
        violations.append("wrong_workflow")
    if run.get("status") != "completed":
        violations.append("run_not_completed")
    if not _budget_count_ok(text_requests, contract.TEXT_REQUEST_LIMIT):
        violations.append("text_request_budget")
    if text_probe is not None and not (
        text_probe.get("ok") is True
        and text_probe.get("streamProbeStatus") == "passed"
        and text_probe.get("streamResponseUsable") is True
    ):
        violations.append("text_runtime_probe")
    if task_types.count("freezone_gen") != contract.EXPECTED_IMAGE_TASK_STARTS:
        violations.append("image_task_start_count")
    if task_types.count("freezone_video_gen") != contract.EXPECTED_VIDEO_TASK_STARTS:
        violations.append("video_task_start_count")
    if image_task_starts != contract.EXPECTED_IMAGE_TASK_STARTS:
        violations.append("image_provider_start_budget")
    if video_task_starts != contract.EXPECTED_VIDEO_TASK_STARTS:
        violations.append("video_provider_start_budget")
    if task_types.count("compose_episode") != 1:
        violations.append("compose_task_count")
    violations.extend(
        _task_receipt_violations(
            task=_first_task_submission(tasks, task_type="freezone_gen"),
            task_type="freezone_gen",
            media_kind="image",
            run_id=str(run.get("id") or ""),
            require_provider_task_id=False,
        )
    )
    violations.extend(
        _task_receipt_violations(
            task=_first_task_submission(tasks, task_type="freezone_video_gen"),
            task_type="freezone_video_gen",
            media_kind="video",
            run_id=str(run.get("id") or ""),
            require_provider_task_id=True,
        )
    )
    if counts["image_start_paths"] != ["/v1/images/generations"]:
        violations.append("image_provider_path")
    if counts["video_start_paths"] != ["/v2/video_generation"]:
        violations.append("native_minimax_video_path")
    if not counts["video_query_paths"]:
        violations.append("native_minimax_query_path")
    image_requests = counts.get("image_requests") or []
    if len(image_requests) != 1:
        violations.append("image_provider_request_record")
    else:
        image_request = image_requests[0]
        if not _image_request_matches_aspect_ratio(image_request, "16:9"):
            violations.append("image_provider_aspect_ratio")
        image_size = str(image_request.get("image_size") or "")
        if image_size != "1K" and "x" not in str(image_request.get("size") or ""):
            violations.append("image_provider_image_size")
    video_requests = counts.get("video_requests") or []
    if len(video_requests) != 1:
        violations.append("video_provider_request_record")
    else:
        video_request = video_requests[0]
        if str(video_request.get("resolution") or "").casefold() != "768p":
            violations.append("video_provider_resolution")
        if str(video_request.get("ratio") or "").casefold() != "adaptive":
            violations.append("video_provider_ratio")
        if int(video_request.get("duration") or 0) != 5:
            violations.append("video_provider_duration")
        if "first_frame" not in (video_request.get("content_roles") or []):
            violations.append("video_provider_first_frame_role")
        first_frame_media = [
            item
            for item in (video_request.get("media_urls") or [])
            if isinstance(item, dict)
            and item.get("type") == "image_url"
            and item.get("role") == "first_frame"
        ]
        if not first_frame_media:
            violations.append("video_provider_first_frame_missing")
        elif not any(
            item.get("scheme") == "https"
            for item in first_frame_media
        ):
            violations.append("video_provider_first_frame_not_https")
    if browser_evidence.get("browser_errors"):
        violations.append("browser_errors")
    if browser_evidence.get("workflow_canvas_writes"):
        violations.append("workflow_canvas_writes")
    return {
        "ok": not violations,
        "violations": violations,
        "textRequests": text_requests,
        "imageTaskStarts": image_task_starts,
        "videoTaskStarts": video_task_starts,
        "taskTypes": task_types,
        "mediaRequests": counts,
    }


def run_paid_sample(
    *,
    authorization: str,
    state_dir: Path,
    artifact_dir: Path = ARTIFACT_DIR,
) -> dict[str, Any]:
    """Execute the bounded paid sample. Call only after user authorization."""

    contract = _load_module(
        "t113_paid_sample_contract",
        Path(__file__).with_name("t113_single_shot_paid_l3.py"),
    )
    if str(authorization or "").strip() != contract.AUTHORIZATION_PHRASE:
        return {
            "schema": RUNNER_SCHEMA,
            "ok": False,
            "paidProvidersConnected": False,
            "providerCallsStarted": False,
            "blockingReasons": ["t113_authorization_phrase_mismatch"],
        }
    preflight = build_runner_preflight()
    if preflight.get("ok") is not True:
        return {
            "schema": RUNNER_SCHEMA,
            "ok": False,
            "paidProvidersConnected": False,
            "providerCallsStarted": False,
            "preflight": preflight,
            "blockingReasons": list(preflight.get("blockingReasons") or []),
        }
    media_relay = production_media_relay_status(
        Path(state_dir) / "local" / "settings.db"
    )
    if media_relay.get("configured") is not True:
        return {
            "schema": RUNNER_SCHEMA,
            "ok": False,
            "paidProvidersConnected": False,
            "providerCallsStarted": False,
            "preflight": preflight,
            "mediaRelay": media_relay,
            "blockingReasons": ["t113_media_relay_not_configured"],
        }

    proxy = _load_module(
        "t113_real_provider_proxy_for_runner",
        Path(__file__).with_name("t113_real_provider_proxy.py"),
    )
    configs = capture_provider_config(state_dir)
    configs = reuse_production_text_model(configs)
    budget_path = Path(artifact_dir) / "provider-budget.json"
    budget = proxy.BudgetJournal(
        budget_path,
        plan=contract.build_plan(),
        reserve_start=contract.reserve_provider_start,
    )
    budget_before = budget.snapshot()
    holder: dict[str, Any] = {}

    def cluster_factory(
        full_chain: bool = True,
        shot_count: int = 1,
        video_duration_seconds: int = 5,
    ) -> Any:
        if int(shot_count) != 1 or int(video_duration_seconds) != 5:
            raise RuntimeError("T-113 paid L3 is single-shot and fixed at 5 seconds")
        cluster = proxy.ProviderProxyCluster(
            text_base_url=str(configs["text"]["baseUrl"]),
            text_api_key=str(configs["text"]["apiKey"]),
            image_base_url=str(configs["image"]["baseUrl"]),
            image_api_key=str(configs["image"]["apiKey"]),
            video_base_url=proxy.proxy_target_base_url(
                str(configs["video"]["baseUrl"]),
                protocol=str(configs["video"].get("protocol") or ""),
            ),
            video_api_key=str(configs["video"]["apiKey"]),
            budget_journal=budget,
            full_chain=full_chain,
        )
        holder["cluster"] = cluster
        return cluster

    def seed_models(_base_url: str) -> None:
        cluster = holder.get("cluster")
        if cluster is None:
            raise RuntimeError("T-113 provider proxy cluster was not created")
        holder["textProbe"] = _seed_t113_models(
            cluster,
            configs,
            text_base_url=str(cluster.text.base_url),
        )
        holder["mediaRelay"] = _copy_production_media_relay_settings(
            TARGET_STATE,
            source_database=Path(state_dir) / "local" / "settings.db",
        )

    t112 = _load_module(
        "t112_real_execution_adapter_for_t113",
        Path(__file__).with_name("t112_real_execution_adapter.py"),
    )
    _configure_t112_harness(
        t112,
        cluster_factory=cluster_factory,
        seed_models=seed_models,
    )
    os.environ["T112_MAX_PAID_STARTS"] = "4"
    # A single structured turn may legitimately spend several minutes in
    # director clarification before the first paid start. Use the same bounded
    # wait as the local full-chain harness so timeout evidence reflects the
    # product path, not the adapter's 5-minute default.
    os.environ["T112_COMPLETED_WAIT_MS"] = "900000"
    run: dict[str, Any] = {}
    browser_evidence: dict[str, Any] = {}
    browser_failure_evidence: dict[str, Any] = {}
    final_film: dict[str, Any] = {}
    failure: dict[str, Any] | None = None
    validation: dict[str, Any]
    run_budget: dict[str, Any] = {}
    cleanup: dict[str, Any]
    try:
        try:
            exit_code = t112._run_adapter(full_chain=True)
            if exit_code != 0:
                raise RuntimeError(f"T-113 paid adapter exit={exit_code}")
            run = _latest_run()
            browser_evidence = json.loads(
                (TARGET_UI_SMOKE / "t112-full-chain-evidence.json").read_text(
                    encoding="utf-8"
                )
            )
            final_film = _copy_final_film(run)
            run_budget = _budget_counter_delta(budget_before, budget.snapshot())
            validation = _validate_execution(
                browser_evidence=browser_evidence,
                run=run,
                budget=run_budget,
                contract=contract,
                text_probe=holder.get("textProbe"),
            )
        except Exception as exc:
            failure = {
                "schema": "t113_paid_execution_failure.v1",
                "type": type(exc).__name__,
                "message": str(exc)[:1000],
            }
            try:
                run = _latest_run()
            except Exception as run_exc:
                failure["runReadError"] = (
                    f"{type(run_exc).__name__}: {str(run_exc)[:300]}"
                )
            try:
                browser_evidence = json.loads(
                    (TARGET_UI_SMOKE / "t112-full-chain-evidence.json").read_text(
                        encoding="utf-8"
                    )
                )
            except Exception as evidence_exc:
                failure["browserEvidenceReadError"] = (
                    f"{type(evidence_exc).__name__}: {str(evidence_exc)[:300]}"
                )
            try:
                browser_failure_evidence = json.loads(
                    (
                        TARGET_UI_SMOKE / "t112-full-chain-evidence-failure.json"
                    ).read_text(encoding="utf-8")
                )
                failure["browserFailureEvidence"] = browser_failure_evidence
            except Exception as failure_evidence_exc:
                failure["browserFailureEvidenceReadError"] = (
                    f"{type(failure_evidence_exc).__name__}: "
                    f"{str(failure_evidence_exc)[:300]}"
                )
            if run and not final_film:
                try:
                    final_film = _copy_final_film(run)
                except Exception as artifact_exc:
                    failure["finalFilmReadError"] = (
                        f"{type(artifact_exc).__name__}: {str(artifact_exc)[:300]}"
                    )
            validation = {
                "ok": False,
                "violations": ["execution_exception"],
            }
    finally:
        cleanup = _safe_cleanup_temp_site()
    _apply_cleanup_violation(validation, cleanup)

    report = {
        "schema": RUNNER_SCHEMA,
        "ok": validation["ok"],
        "authorizationPhrase": contract.AUTHORIZATION_PHRASE,
        "providerCallsStarted": bool(
            budget.snapshot().get("providerCallsStarted") is True
        ),
        "paidProvidersConnected": True,
        "isolation": {
            "targetUiSmoke": str(TARGET_UI_SMOKE),
            "cleanup": cleanup,
        },
        "models": {
            role: {
                "modelId": str(configs[role]["modelId"]),
                "baseUrl": str(configs[role]["baseUrl"]),
                "protocol": str(configs[role].get("protocol") or ""),
            }
            for role in ("text", "image", "video")
        },
        "textRouting": "direct-upstream",
        "mediaRelay": holder.get("mediaRelay") or media_relay,
        "textProbe": holder.get("textProbe") or {},
        "budget": budget.snapshot(),
        "runBudget": run_budget,
        "run": run,
        "browserEvidence": browser_evidence,
        "browserFailureEvidence": browser_failure_evidence,
        "finalFilm": final_film,
        "validation": validation,
        "failure": failure,
    }
    return report


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorization", default="")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=ROOT / "项目资产" / "state",
    )
    parser.add_argument("--output", type=Path, default=ARTIFACT_DIR / "execution.json")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument(
        "--local-full-chain",
        action="store_true",
        help=(
            "run the exact T-113 fixture against deterministic local HTTP "
            "providers, including the native MiniMax video-v2 path"
        ),
    )
    parser.add_argument(
        "--local-proxy-full-chain",
        action="store_true",
        help=(
            "run the paid execution path through the three real provider "
            "proxies while their upstream is a deterministic local server"
        ),
    )
    parser.add_argument(
        "--local-proxy-video-failure",
        action="store_true",
        help=(
            "inject one video-start 502 after the real budget proxy forwards "
            "it, then prove the workflow fails closed without a second start"
        ),
    )
    parser.add_argument(
        "--local-proxy-video-recovery",
        action="store_true",
        help=(
            "inject one video 502, click the browser recovery authorization, "
            "and require the same Run to finish after one exact failed-item retry"
        ),
    )
    parser.add_argument(
        "--real-text-proxy-media",
        action="store_true",
        help=(
            "drive the Agent with the operator's real text model while image "
            "and video stay on the free local proxy"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.preflight:
        report = build_runner_preflight()
    elif args.local_proxy_video_recovery:
        report = run_local_full_chain(
            through_proxy=True,
            recovery_chain=True,
        )
    elif args.local_proxy_video_failure:
        report = run_local_full_chain(
            through_proxy=True,
            video_failures_remaining=1,
        )
    elif args.local_proxy_full_chain:
        report = run_local_full_chain(through_proxy=True)
    elif args.real_text_proxy_media:
        report = run_local_full_chain(through_proxy=True, real_text=True)
    elif args.local_full_chain:
        report = run_local_full_chain()
    else:
        report = run_paid_sample(
            authorization=str(args.authorization),
            state_dir=Path(args.state_dir),
        )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
