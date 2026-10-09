"""Runtime model gateway settings.

CE persists the selected official or bundled-NewAPI gateway in local settings,
which are its sole runtime credential source. EE has a control-plane DSN and
keeps its deployment environment as the sole credential source.
"""

from __future__ import annotations

import os
import json
import hashlib
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from novelvideo.generators.video.direct_video_protocol_contracts import (
    DIRECT_VIDEO_PROTOCOL_AUTO,
    DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI,
    DIRECT_VIDEO_PROTOCOL_MINIMAX_V2,
    DIRECT_VIDEO_PROTOCOL_OPENAI,
    DIRECT_VIDEO_PROTOCOL_UNRESOLVED,
    get_direct_video_protocol_contract,
    normalize_direct_video_protocol,
)
from novelvideo.generators.video.channel_wire_contract import (
    WireContractError,
    normalize_wire_contract_record,
)
from novelvideo.official_defaults import (
    DEFAULT_COGNEE_EMBEDDING_DIM,
    DEFAULT_COGNEE_EMBEDDING_MODEL,
    DEFAULT_COGNEE_EMBEDDING_PROVIDER,
    DEFAULT_EMBEDDING_BATCH_SIZE,
    OFFICIAL_NEWAPI_BASE_URL,
)
from novelvideo.shared.runtime_env import is_ce_effective
from novelvideo.sqlite_pragmas import configure_sqlite_connection

MODE_OFFICIAL = "official"
MODE_CUSTOM = "custom"
MODE_UNIFIED = "unified"
VALID_MODES = {MODE_OFFICIAL, MODE_CUSTOM, MODE_UNIFIED}
PLACEHOLDER_API_KEYS = {
    "your_newapi_token",
    "your_model_api_key",
    "your_api_key",
    "your_dc_key",
}
CLOUDINARY_CLOUD_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_LEGACY_VISION_MODEL_ALIASES = {
    "dc-freezone-vision-llm": "gemini-3-flash",
    "dc-freezone-vision-fast-llm": "gemini-3-flash",
}


def normalize_direct_model_id(kind: str, value: object) -> str:
    """Normalize legacy/bad model aliases before any runtime request.

    Direct-model rows are operator-owned, but old workspace builds could persist
    logical DC aliases or an `a/` relay prefix.  Keeping those in runtime
    requests produces avoidable 401/404/timeout failures on OpenAI-compatible
    relays.  This helper is intentionally narrow: slash-bearing provider routes
    such as `google/gemini-*` remain untouched; only the known bad `a/` prefix
    and retired Freezone vision aliases are rewritten.
    """

    normalized_kind = str(kind or "").strip().lower()
    model_id = str(value or "").strip()
    if not model_id:
        return ""
    if normalized_kind == "vision":
        alias = _LEGACY_VISION_MODEL_ALIASES.get(model_id.casefold())
        if alias:
            return alias
    if model_id.casefold().startswith("a/") and model_id[2:].strip():
        return model_id[2:].strip()
    return model_id


def is_valid_cloudinary_cloud_name(value: str | None) -> bool:
    """Reject project labels and other non-Cloudinary values early."""

    return bool(CLOUDINARY_CLOUD_NAME_PATTERN.fullmatch(str(value or "").strip()))


@dataclass(frozen=True)
class EffectiveNewApiConfig:
    mode: str
    source: str
    base_url: str
    api_key: str


@dataclass(frozen=True)
class EffectiveMediaRelayConfig:
    source: str
    provider: str
    ttl_seconds: int
    endpoint: str
    bucket: str
    access_key_id: str
    access_key_secret: str
    cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""
    cloudinary_folder: str = ""


@dataclass(frozen=True)
class EffectiveCogneeEmbeddingConfig:
    source: str
    provider: str
    model: str
    dimensions: str
    upstream_provider: str
    upstream_model: str
    batch_size: str = ""


def mask_secret(value: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        return ""
    if len(clean) <= 10:
        return "*" * len(clean)
    return f"{clean[:4]}...{clean[-4:]}"


def normalize_api_key(value: str | None) -> str:
    """Return the credential exactly as an upstream gateway should receive it.

    Keys copied from a web page often carry zero-width characters or a pair of
    quotes.  Both make the Authorization header invalid upstream while looking
    identical to the operator, so they are removed before the value is stored
    or sent.  Nothing is replaced or truncated beyond those artifacts.
    """

    clean = str(value or "")
    if not clean:
        return ""
    clean = "".join(
        char for char in clean if unicodedata.category(char) != "Cf"
    ).strip()
    if len(clean) >= 2 and clean[0] == clean[-1] and clean[0] in {'"', "'"}:
        clean = clean[1:-1].strip()
    if not clean:
        return ""
    lowered = clean.lower()
    if lowered in PLACEHOLDER_API_KEYS:
        return ""
    if lowered.startswith("your_") or lowered.startswith("<your_"):
        return ""
    return clean


def normalize_gateway_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in VALID_MODES else MODE_OFFICIAL


def normalize_relay_base_url(value: str | None) -> str:
    base = str(value or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def get_unified_gateway_environment() -> tuple[str, str]:
    """Read the sole deployment-level gateway seed without choosing a route.

    ``VILLAGE_CANVAS_GATEWAY_*`` is the canonical operator-facing configuration.
    The NewAPI names remain compatibility aliases for older workers and are
    intentionally read only when the canonical values are absent.
    """

    base_url = normalize_relay_base_url(
        os.environ.get("VILLAGE_CANVAS_GATEWAY_BASE_URL", "")
        or os.environ.get("NEWAPI_BASE_URL", "")
    )
    api_key = normalize_api_key(
        os.environ.get("VILLAGE_CANVAS_GATEWAY_API_KEY", "")
        or os.environ.get("NEWAPI_API_KEY", "")
    )
    return base_url, api_key


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _settings_db_path() -> Path:
    from novelvideo import config

    return Path(config.STATE_DIR) / "local" / "settings.db"


def _connect() -> sqlite3.Connection:
    path = _settings_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    configure_sqlite_connection(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runtime_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)
    conn.commit()
    return conn


def _read_all() -> dict[str, str]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT key, value FROM runtime_settings").fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}
    finally:
        conn.close()


def _uses_ce_gateway_settings() -> bool:
    """Return whether this process owns the CE-local gateway settings database."""
    return is_ce_effective()


def _write_many(values: dict[str, str]) -> None:
    now = _now_iso()
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for key, value in values.items():
            conn.execute(
                """
                INSERT INTO runtime_settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, str(value or ""), now),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_model_gateway_mode(mode: str) -> None:
    _write_many({"model_gateway_mode": normalize_gateway_mode(mode)})


def save_official_newapi_key(
    *,
    api_key: str,
    activate: bool = True,
) -> None:
    values = {
        "official_newapi_api_key": str(api_key or "").strip(),
    }
    if activate:
        values["model_gateway_mode"] = MODE_OFFICIAL
    _write_many(values)


def save_custom_newapi_gateway(
    *,
    base_url: str,
    api_key: str,
    admin_base_url: str = "",
    token_name: str = "",
    token_id: int | str = "",
    activate: bool = True,
) -> None:
    values = {
        "custom_newapi_base_url": normalize_relay_base_url(base_url),
        "custom_newapi_api_key": str(api_key or "").strip(),
        "custom_newapi_admin_base_url": str(admin_base_url or "").strip().rstrip("/"),
        "custom_newapi_token_name": str(token_name or "").strip(),
        "custom_newapi_token_id": str(token_id or "").strip(),
    }
    if activate:
        values["model_gateway_mode"] = MODE_CUSTOM
    _write_many(values)


def save_unified_gateway(
    *,
    base_url: str,
    api_key: str,
    activate: bool = True,
) -> None:
    """Persist the one active Village Infinite Canvas gateway without provider duality."""

    values = {
        "unified_gateway_base_url": normalize_relay_base_url(base_url),
        "unified_gateway_api_key": normalize_api_key(api_key),
    }
    if activate:
        values["model_gateway_mode"] = MODE_UNIFIED
    _write_many(values)


def ensure_unified_gateway_migration() -> EffectiveNewApiConfig:
    """Make the current installation use one gateway, preserving legacy values.

    This explicit migration is invoked by the local launcher, never from a
    passive read path. It prevents stale ``official`` settings from silently
    overriding the HK gateway while keeping the old values available for a
    reversible rollback.
    """

    if not _uses_ce_gateway_settings():
        base_url, api_key = get_unified_gateway_environment()
        return EffectiveNewApiConfig(
            mode=MODE_UNIFIED,
            source="unified-environment",
            base_url=base_url,
            api_key=api_key,
        )

    settings = get_model_gateway_settings()
    stored_base_url = normalize_relay_base_url(settings.get("unified_gateway_base_url", ""))
    stored_api_key = normalize_api_key(settings.get("unified_gateway_api_key", ""))
    env_base_url, env_api_key = get_unified_gateway_environment()
    base_url = env_base_url or stored_base_url or normalize_relay_base_url(
        settings.get("custom_newapi_base_url", "")
    )
    api_key = env_api_key or stored_api_key or normalize_api_key(
        settings.get("custom_newapi_api_key", "")
    ) or normalize_api_key(settings.get("official_newapi_api_key", ""))
    if base_url and api_key:
        # Environment wins for the portable local launcher so an old public
        # custom URL never supersedes the current WireGuard-only endpoint.
        save_unified_gateway(base_url=base_url, api_key=api_key, activate=True)
    else:
        set_model_gateway_mode(MODE_UNIFIED)
    return get_ce_newapi_config_for_mode(MODE_UNIFIED)


def save_newapi_database_config(
    *,
    sql_dsn: str,
    sqlite_path: str = "",
    admin_username: str = "",
) -> None:
    _write_many(
        {
            "custom_newapi_db_sql_dsn": str(sql_dsn or "").strip(),
            "custom_newapi_db_sqlite_path": str(sqlite_path or "").strip(),
            "custom_newapi_admin_username": str(admin_username or "").strip(),
        }
    )


def _decode_provider_channels(value: str | None) -> list[dict[str, str]]:
    if not value:
        return []
    try:
        raw = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []

    channels: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "").strip().lower()
        if not provider or provider in seen:
            continue
        seen.add(provider)
        channels.append(
            {
                "provider": provider,
                "upstreamKey": str(item.get("upstreamKey") or "").strip(),
                "baseUrl": str(item.get("baseUrl") or "").strip().rstrip("/"),
            }
        )
    return channels


def _decode_media_model_mappings(value: str | None) -> dict[str, dict[str, str]]:
    if not value:
        return {}
    try:
        raw = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}

    mappings: dict[str, dict[str, str]] = {}
    for model, item in raw.items():
        model_name = str(model or "").strip()
        if not model_name or not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "").strip().lower()
        if not provider:
            continue
        mappings[model_name] = {
            "provider": provider,
            "upstreamModel": str(item.get("upstreamModel") or "").strip(),
        }
    return mappings


_DIRECT_VIDEO_MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
#: Canonical local model families.  ``video`` keeps its dedicated registry.
DIRECT_MODEL_KINDS = frozenset({"chat", "image", "embedding"})
#: Retired families folded into ``chat``; accepted on read and on the API.
LEGACY_CHAT_KINDS = ("agent", "text", "vision")
DIRECT_MODEL_ALIAS_KINDS = frozenset(LEGACY_CHAT_KINDS)
#: Withdrawn families that saved bindings and credit quotes still name.
#
# 7e92f79 把音频直连模型从模型中心撤下（DIRECT_MODEL_KINDS 不再收 ``audio``），
# 但节点绑定、额度报价与语音/音乐任务入参仍在传 ``audio``：``model_credits`` 的
# ``beat_tts`` / ``freezone_audio_music``、``freezone/audio_node`` 的三条生成入口、
# ``workflow_runtime/model_plan`` 的角色投影。它们只做读取，所以这里保留只读身份，
# 让各自落到既有的「未配置」分支（400 / 明确报错），而不是在
# ``canonical_direct_model_kind`` 抛 ValueError 变成 HTTP 500。
# 写入侧不受影响：``DIRECT_MODEL_KINDS`` 不含 ``audio``，模型状态列表不会重新出现
# 音频族，``model_gateway`` 的保存/检测路径 Literal 也已不含 ``audio``。
DIRECT_MODEL_RETIRED_KINDS = frozenset({"audio"})
#: Modes the retired audio registry accepted; still the vocabulary read back.
DIRECT_AUDIO_MODES = frozenset({"text_to_speech", "text_to_music"})
DIRECT_MODEL_CAPABILITY_KEYS = ("supportsTools", "supportsVision")
_RETIRED_DIRECT_MODEL_CHANNELS_SETTING_KEY = "direct_model_channels_v1"


def canonical_direct_model_kind(kind: str) -> str:
    """Resolve one requested family onto its canonical storage kind.

    ``agent`` / ``text`` / ``vision`` were three separate registries for what is
    the same chat model.  They stay accepted everywhere (callers, saved node
    bindings, API paths) but every one of them now reads and writes the single
    ``chat`` family, so the model center shows one list.

    ``audio`` stays accepted *only* as a retired read-only family: its settings
    key is still read by the TTS/music callers that reject an empty result with
    their own configuration error.  ``DIRECT_MODEL_KINDS`` (not this function)
    is what the model center, the save path and the status payload enumerate.
    """

    normalized = str(kind or "").strip().lower()
    if normalized in DIRECT_MODEL_ALIAS_KINDS:
        return "chat"
    if normalized not in DIRECT_MODEL_KINDS and normalized not in DIRECT_MODEL_RETIRED_KINDS:
        raise ValueError(f"unsupported direct model kind: {kind}")
    return normalized


def _migrate_retired_direct_model_channels() -> dict[str, str]:
    """Inline credentials from the retired shared-channel experiment.

    The migration is idempotent and intentionally silent: it never logs a
    credential, and it preserves unresolved rows verbatim instead of dropping
    an operator's model configuration.
    """

    settings = get_model_gateway_settings()
    encoded_channels = settings.get(_RETIRED_DIRECT_MODEL_CHANNELS_SETTING_KEY)
    if not encoded_channels:
        return settings
    try:
        raw_channels = json.loads(encoded_channels)
    except json.JSONDecodeError:
        return settings
    if not isinstance(raw_channels, list):
        return settings

    channels: dict[str, tuple[str, str]] = {}
    for raw in raw_channels:
        if not isinstance(raw, dict):
            continue
        channel_id = str(raw.get("id") or "").strip().lower()
        base_url = str(raw.get("baseUrl") or "").strip().rstrip("/")
        api_key = normalize_api_key(raw.get("apiKey"))
        if channel_id and base_url and api_key:
            channels[channel_id] = (base_url, api_key)
    if not channels:
        return settings

    writes: dict[str, str] = {}
    unresolved_reference = False
    storage_keys = (
        "direct_video_models",
        *(
            f"direct_{kind}_models"
            for kind in sorted(DIRECT_MODEL_KINDS | DIRECT_MODEL_RETIRED_KINDS)
        ),
        *(_raw_direct_model_setting_key(kind) for kind in LEGACY_CHAT_KINDS),
    )
    for setting_key in storage_keys:
        encoded_models = settings.get(setting_key)
        if not encoded_models:
            continue
        try:
            raw_models = json.loads(encoded_models)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw_models, list):
            continue

        changed = False
        migrated: list[object] = []
        for raw in raw_models:
            if not isinstance(raw, dict):
                migrated.append(raw)
                continue
            item = dict(raw)
            channel_id = str(item.get("channelId") or "").strip().lower()
            credentials = channels.get(channel_id)
            if channel_id and credentials is None and not (
                str(item.get("baseUrl") or "").strip()
                and normalize_api_key(item.get("apiKey"))
            ):
                unresolved_reference = True
                migrated.append(item)
                continue
            if credentials is not None:
                item["baseUrl"], item["apiKey"] = credentials
            for retired_key in (
                "channelId",
                "channelLabel",
                "channelEnabled",
                "verificationRequired",
                "legacyCompatibility",
            ):
                item.pop(retired_key, None)
            changed = changed or item != raw
            migrated.append(item)
        if changed:
            writes[setting_key] = json.dumps(
                migrated, ensure_ascii=False, separators=(",", ":")
            )

    if not unresolved_reference:
        writes[_RETIRED_DIRECT_MODEL_CHANNELS_SETTING_KEY] = ""
    if writes:
        _write_many(writes)
        settings = {**settings, **writes}
    return settings


def _normalize_direct_video_protocol(value: object) -> str:
    """Normalize every transport that currently has an executable contract."""
    return normalize_direct_video_protocol(value)


def _infer_direct_video_protocol(
    requested_protocol: str,
    *,
    base_url: str = "",
    model_id: str = "",
) -> str:
    requested = _normalize_direct_video_protocol(requested_protocol)
    if requested != DIRECT_VIDEO_PROTOCOL_AUTO:
        return requested
    if base_url and model_id:
        parsed = urlsplit(base_url)
        if parsed.hostname and parsed.hostname.lower().endswith("autodl.art"):
            return DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI
        from novelvideo.generators.video.direct_video_capability_cache import (
            get_cached_capability_for_model,
        )

        cached = get_cached_capability_for_model(
            base_url=base_url,
            upstream_model=model_id,
        )
        detected = str(cached.get("detectedProtocol") or cached.get("protocol") or "")
        supported = cached.get("supportedProtocols")
        try:
            normalized = _normalize_direct_video_protocol(detected)
        except ValueError:
            normalized = DIRECT_VIDEO_PROTOCOL_AUTO
        if normalized != DIRECT_VIDEO_PROTOCOL_AUTO and (
            normalized == DIRECT_VIDEO_PROTOCOL_OPENAI
            or (isinstance(supported, list) and supported)
        ):
            return normalized
    # An unprobed endpoint is not evidence of the OpenAI video lifecycle.
    # Keep automatic rows unavailable until discovery resolves a contract.
    return DIRECT_VIDEO_PROTOCOL_UNRESOLVED


def _direct_video_protocol_label(protocol: str) -> str:
    labels = {
        DIRECT_VIDEO_PROTOCOL_AUTO: "自动识别",
        DIRECT_VIDEO_PROTOCOL_OPENAI: "OpenAI Video",
        DIRECT_VIDEO_PROTOCOL_MINIMAX_V2: "MiniMax Video v2",
        DIRECT_VIDEO_PROTOCOL_UNRESOLVED: "协议待适配",
        DIRECT_VIDEO_PROTOCOL_AUTODL_COMFYUI: "AutoDL ComfyUI API",
        "prediction": "Prediction",
        "queue": "Queue",
        "long-running-operation": "Long-running Operation",
        "workflow": "Workflow / ComfyUI",
    }
    return labels.get(protocol, protocol)


def _direct_video_protocol_runtime_ready(protocol: object) -> bool:
    try:
        get_direct_video_protocol_contract(protocol)
    except ValueError:
        return False
    return True


def _direct_video_cached_failure_reason(cached: Mapping[str, Any]) -> str:
    """最近一次连接检测失败的上游原话；没有失败记录就返回空串。

    目录能读不等于 Key 能用，所以这里要把检测时记下的失败原样交给用户，
    不能再用「协议没识别」这种和真实原因无关的话糊过去。
    """

    status = str(cached.get("probeStatus") or "").strip().lower()
    if status == "stale":
        return "渠道凭据或协议已变化，请重新检测连接"
    if status != "failed":
        return ""
    detail = str(cached.get("lastFailure") or "").strip()
    return f"实时连接检测失败：{detail}" if detail else ""


def _cached_runtime_verified(
    *, base_url: str, model_id: str, protocol: str | None = None
) -> bool:
    """这条渠道有没有**真实提交过**的记录（能力缓存里的 runtime-verified）。"""

    cached = _cached_capability(base_url=base_url, model_id=model_id, protocol=protocol)
    return str(cached.get("verificationStatus") or "").strip().lower() == "runtime-verified"


def _cached_capability(
    *, base_url: str, model_id: str, protocol: str | None = None
) -> dict[str, Any]:
    """读这条渠道的能力缓存；读不到就返回空字典（不是错误）。"""

    try:
        from novelvideo.generators.video.direct_video_capability_cache import (
            get_cached_capability_for_model,
        )

        return dict(
            get_cached_capability_for_model(
                base_url=base_url, upstream_model=model_id, protocol=protocol
            )
            or {}
        )
    except (OSError, ValueError):
        return {}


def _direct_video_record_id(
    value: object,
    *,
    model_id: str,
    base_url: str,
) -> str:
    """Return a stable, internal key without exposing an upstream model ID."""
    requested = str(value or "").strip().lower()
    if _DIRECT_VIDEO_MODEL_ID.fullmatch(requested):
        return requested
    seed = f"{base_url}\n{model_id}".encode("utf-8")
    return f"video-{hashlib.sha256(seed).hexdigest()[:16]}"


def _normalize_direct_video_base_url(value: object) -> str:
    base_url = str(value or "").strip().rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("baseUrl must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("baseUrl must not include credentials, query, or fragment")
    # AutoDL puts the workflow ID in the path of the submit endpoint. Keep
    # the registry endpoint at the origin so the adapter can append the
    # contract path exactly once, including for rows saved by the old UI.
    hostname = str(parsed.hostname or "").lower()
    if hostname == "autodl.art" or hostname.endswith(".autodl.art"):
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
    return base_url


def _saved_api_key_for_endpoint(
    existing: dict[str, Any],
    *,
    base_url: str,
) -> str:
    """Reuse a stored secret only while it remains bound to the same endpoint."""
    if str(existing.get("baseUrl") or "") != base_url:
        return ""
    return normalize_api_key(existing.get("apiKey"))


def _decode_direct_video_models(value: str | None) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        raw = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []

    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            label = str(item.get("label") or "").strip()
            model_id = normalize_direct_model_id("video", item.get("modelId"))
            base_url = _normalize_direct_video_base_url(item.get("baseUrl"))
            api_key = normalize_api_key(item.get("apiKey"))
            requested_protocol = _normalize_direct_video_protocol(
                item.get("requestedProtocol") or item.get("protocol")
            )
            protocol = _infer_direct_video_protocol(
                requested_protocol,
                base_url=base_url,
                model_id=model_id,
            )
            record_id = _direct_video_record_id(
                item.get("id"), model_id=model_id, base_url=base_url
            )
        except ValueError:
            continue
        if not label or not model_id or not api_key or record_id in seen:
            continue
        seen.add(record_id)
        # 渠道自带的出线合同：读不动就**不能静默当成没有**，把原因挂在记录上让
        # 模型中心显示出来。能读动就原样留着，它随这条渠道一起生灭。
        wire_contract: dict[str, Any] | None = None
        wire_contract_error = ""
        raw_contract = item.get("wireContract")
        if raw_contract:
            try:
                wire_contract = normalize_wire_contract_record(raw_contract)
            except WireContractError as exc:
                wire_contract_error = str(exc)
        models.append(
            {
                "id": record_id,
                "label": label,
                "modelId": model_id,
                "baseUrl": base_url,
                "apiKey": api_key,
                "requestedProtocol": requested_protocol,
                "protocol": protocol,
                "enabled": bool(item.get("enabled", True)),
                "isDefault": bool(item.get("isDefault", False)),
                "wireContract": wire_contract,
                "wireContractError": wire_contract_error,
            }
        )
    default_index = next(
        (
            idx
            for idx, item in enumerate(models)
            if item["isDefault"] and item["enabled"]
        ),
        -1,
    )
    if default_index < 0:
        default_index = next(
            (idx for idx, item in enumerate(models) if item["enabled"]),
            -1,
        )
    return [
        {**item, "isDefault": index == default_index}
        for index, item in enumerate(models)
    ]


def _raw_direct_model_setting_key(kind: str) -> str:
    return f"direct_{str(kind or '').strip().lower()}_models"


def _direct_model_setting_key(kind: str) -> str:
    return _raw_direct_model_setting_key(canonical_direct_model_kind(kind))


def _direct_model_record_id(
    kind: str,
    value: object,
    *,
    model_id: str,
    base_url: str,
) -> str:
    """Return a stable, non-secret identifier for a locally managed model."""
    requested = str(value or "").strip().lower()
    if _DIRECT_VIDEO_MODEL_ID.fullmatch(requested):
        return requested
    seed = f"{kind}\n{base_url}\n{model_id}".encode("utf-8")
    return f"{kind}-{hashlib.sha256(seed).hexdigest()[:16]}"


def _normalize_direct_model_base_url(value: object) -> str:
    base_url = str(value or "").strip().rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("baseUrl must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("baseUrl must not include credentials, query, or fragment")
    from novelvideo.generators.direct_model_capabilities import (
        normalize_direct_model_base_url,
    )

    return normalize_direct_model_base_url(base_url)


def _decode_direct_models(value: str | None, *, kind: str) -> list[dict[str, Any]]:
    """Decode one direct-model family while discarding incomplete old records."""
    if not value:
        return []
    try:
        raw = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []

    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    chat_family = kind == "chat" or kind in DIRECT_MODEL_ALIAS_KINDS
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            label = str(item.get("label") or "").strip()
            model_id = normalize_direct_model_id(kind, item.get("modelId"))
            base_url = _normalize_direct_model_base_url(item.get("baseUrl"))
            api_key = normalize_api_key(item.get("apiKey"))
            from novelvideo.generators.direct_model_capabilities import (
                infer_direct_model_protocol,
                normalize_direct_model_protocol,
            )

            requested_protocol = normalize_direct_model_protocol(
                str(item.get("requestedProtocol") or item.get("protocol") or "auto")
            )
            protocol = infer_direct_model_protocol(
                kind,
                model_id,
                base_url=base_url,
                requested_protocol=requested_protocol,
            )
            record_id = _direct_model_record_id(
                kind,
                item.get("id"),
                model_id=model_id,
                base_url=base_url,
            )
        except ValueError:
            continue
        if not label or not model_id or not api_key or record_id in seen:
            continue
        seen.add(record_id)
        # The retired audio registry stored a per-row ``supportedModes``; it is
        # what separates a saved text-to-music row from a speech-only one, so
        # keep reading it instead of downgrading every row to the static default.
        supported_modes: list[str] | None = None
        if kind == "audio":
            raw_modes = item.get("supportedModes")
            if not isinstance(raw_modes, list):
                raw_modes = ["text_to_speech"]
            supported_modes = list(
                dict.fromkeys(
                    str(mode or "").strip().lower()
                    for mode in raw_modes
                    if str(mode or "").strip().lower() in DIRECT_AUDIO_MODES
                )
            ) or ["text_to_speech"]
        models.append(
            {
                "id": record_id,
                "label": label,
                "modelId": model_id,
                "baseUrl": base_url,
                "apiKey": api_key,
                "requestedProtocol": requested_protocol,
                "protocol": protocol,
                "enabled": bool(item.get("enabled", True)),
                "isDefault": bool(item.get("isDefault", False)),
                **({"supportedModes": supported_modes} if supported_modes else {}),
                **(
                    {
                        "supportsTools": bool(
                            item.get("supportsTools", kind in {"chat", "agent", "text"})
                        ),
                        "supportsVision": bool(
                            item.get("supportsVision", kind in {"chat", "vision"})
                        ),
                    }
                    if chat_family
                    else {}
                ),
            }
        )

    default_index = next(
        (
            idx
            for idx, item in enumerate(models)
            if item["isDefault"] and item["enabled"]
        ),
        -1,
    )
    if default_index < 0:
        default_index = next((idx for idx, item in enumerate(models) if item["enabled"]), -1)
    return [
        {**item, "isDefault": index == default_index}
        for index, item in enumerate(models)
    ]


def _decode_embedding_model_config(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        raw = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}

    provider = str(raw.get("provider") or "").strip().lower()
    upstream_model = str(raw.get("upstreamModel") or "").strip()
    dimensions = _int_setting(
        str(raw.get("dimension") or raw.get("dimensions") or ""), 0
    )
    batch_size = _int_setting(
        str(raw.get("batchSize") or raw.get("batch_size") or ""), 0
    )
    if not provider or not upstream_model or dimensions <= 0:
        return {}
    result: dict[str, Any] = {
        "provider": provider,
        "upstreamModel": upstream_model,
        "dimension": dimensions,
        # Retain the field for API compatibility, but request behavior is an
        # internal model contract and cannot be disabled by saved CE settings.
        "sendDimensions": True,
        "internalModel": "DC-cognee-embedding",
    }
    if batch_size > 0:
        result["batchSize"] = batch_size
    return result


def get_newapi_provider_channels() -> list[dict[str, str]]:
    settings = get_model_gateway_settings()
    return _decode_provider_channels(settings.get("custom_newapi_provider_channels"))


def get_newapi_provider_channel(provider: str) -> dict[str, str] | None:
    wanted = str(provider or "").strip().lower()
    if not wanted:
        return None
    for channel in get_newapi_provider_channels():
        if channel["provider"] == wanted:
            return channel
    return None


def save_newapi_provider_channels(
    channels: list[dict[str, str]],
) -> list[dict[str, str]]:
    existing_by_provider = {
        channel["provider"]: channel for channel in get_newapi_provider_channels()
    }
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in channels:
        provider = str(item.get("provider") or "").strip().lower()
        if not provider or provider in seen:
            continue
        seen.add(provider)
        previous = existing_by_provider.get(provider, {})
        upstream_key = str(item.get("upstreamKey") or "").strip() or previous.get(
            "upstreamKey",
            "",
        )
        base_url = str(item.get("baseUrl") or "").strip().rstrip("/")
        if not upstream_key:
            raise ValueError(f"upstreamKey is required for provider {provider}")
        normalized.append(
            {
                "provider": provider,
                "upstreamKey": upstream_key,
                "baseUrl": base_url,
            }
        )
    _write_many(
        {
            "custom_newapi_provider_channels": json.dumps(
                normalized,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        }
    )
    return normalized


def get_newapi_media_model_mappings() -> dict[str, dict[str, str]]:
    settings = get_model_gateway_settings()
    return _decode_media_model_mappings(
        settings.get("custom_newapi_media_model_mappings")
        or settings.get("newapi_media_model_mappings")
    )


def save_newapi_media_model_mappings(
    mappings: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    normalized: dict[str, dict[str, str]] = {}
    for model, item in mappings.items():
        model_name = str(model or "").strip()
        if not model_name:
            continue
        provider = str(item.get("provider") or "").strip().lower()
        if not provider:
            raise ValueError(f"provider is required for media model {model_name}")
        normalized[model_name] = {
            "provider": provider,
            "upstreamModel": str(item.get("upstreamModel") or "").strip(),
        }
    _write_many(
        {
            "custom_newapi_media_model_mappings": json.dumps(
                normalized,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        }
    )
    return normalized


def build_newapi_media_model_mappings_status() -> dict[str, dict[str, str]]:
    return get_newapi_media_model_mappings()


def get_newapi_embedding_model_config() -> dict[str, Any]:
    settings = get_model_gateway_settings()
    return _decode_embedding_model_config(
        settings.get("custom_newapi_embedding_model")
        or settings.get("newapi_embedding_model_config")
    )


def save_newapi_embedding_model_config(
    *,
    provider: str,
    upstream_model: str,
    dimension: int | str,
    batch_size: int | str | None = None,
    send_dimensions: bool = True,
) -> dict[str, Any]:
    normalized_provider = str(provider or "").strip().lower()
    normalized_upstream_model = str(upstream_model or "").strip()
    normalized_dimension = _int_setting(str(dimension), 0)
    normalized_batch_size = _int_setting(str(batch_size or ""), 0)
    if not normalized_provider:
        raise ValueError("provider is required for embedding model")
    if not normalized_upstream_model:
        raise ValueError("upstreamModel is required for embedding model")
    if normalized_dimension <= 0:
        raise ValueError("dimension must be positive")
    if batch_size not in (None, "") and normalized_batch_size <= 0:
        raise ValueError("batchSize must be positive")
    config = {
        "provider": normalized_provider,
        "upstreamModel": normalized_upstream_model,
        "dimension": normalized_dimension,
        "sendDimensions": True,
        "internalModel": "DC-cognee-embedding",
    }
    if normalized_batch_size > 0:
        config["batchSize"] = normalized_batch_size
    _write_many(
        {
            "custom_newapi_embedding_model": json.dumps(
                config,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        }
    )
    return config


def build_newapi_embedding_model_status() -> dict[str, Any]:
    return get_newapi_embedding_model_config()


def build_newapi_provider_channels_status() -> list[dict[str, Any]]:
    return [
        {
            "provider": channel["provider"],
            "configured": bool(channel["upstreamKey"]),
            "upstreamKeyPreview": mask_secret(channel["upstreamKey"]),
            "baseUrl": channel["baseUrl"],
        }
        for channel in get_newapi_provider_channels()
    ]


def save_media_relay_config(
    *,
    provider: str,
    ttl_seconds: int,
    endpoint: str = "",
    bucket: str = "",
    access_key_id: str = "",
    access_key_secret: str = "",
    cloud_name: str = "",
    cloudinary_api_key: str = "",
    cloudinary_api_secret: str = "",
    cloudinary_folder: str = "",
) -> None:
    _write_many(
        {
            "media_relay_provider": str(provider or "").strip().lower(),
            "media_relay_ttl_seconds": str(int(ttl_seconds)),
            "oss_relay_endpoint": str(endpoint or "").strip(),
            "oss_relay_bucket": str(bucket or "").strip(),
            "oss_relay_ak": str(access_key_id or "").strip(),
            "oss_relay_sk": str(access_key_secret or "").strip(),
            "cloudinary_relay_cloud_name": str(cloud_name or "").strip(),
            "cloudinary_relay_api_key": str(cloudinary_api_key or "").strip(),
            "cloudinary_relay_api_secret": str(cloudinary_api_secret or "").strip(),
            "cloudinary_relay_folder": str(cloudinary_folder or "").strip().strip("/"),
        }
    )


def get_model_gateway_settings() -> dict[str, str]:
    data = _read_all()
    data.setdefault("model_gateway_mode", MODE_UNIFIED)
    return data


def get_effective_newapi_config(
    *,
    official_base_url: str | None = None,
    official_api_key: str | None = None,
) -> EffectiveNewApiConfig:
    if not _uses_ce_gateway_settings():
        return EffectiveNewApiConfig(
            mode=MODE_OFFICIAL,
            source="environment",
            base_url=normalize_relay_base_url(
                os.environ.get("NEWAPI_BASE_URL", "")
                or official_base_url
                or OFFICIAL_NEWAPI_BASE_URL
            ),
            api_key=normalize_api_key(
                official_api_key
                if official_api_key is not None
                else os.environ.get("NEWAPI_API_KEY", "")
            ),
        )

    settings = get_model_gateway_settings()
    mode = normalize_gateway_mode(settings.get("model_gateway_mode"))
    return get_ce_newapi_config_for_mode(mode)


def get_ce_newapi_config_for_mode(mode: str) -> EffectiveNewApiConfig:
    """Return CE credentials for one gateway without changing the active mode.

    Embedding projects can remain bound to the gateway that created their vector
    space even when the installation's general model gateway is switched later.
    """

    if not _uses_ce_gateway_settings():
        raise RuntimeError("CE model gateway settings are not available in EE")

    settings = get_model_gateway_settings()
    mode = normalize_gateway_mode(mode)
    if mode == MODE_UNIFIED:
        env_base_url, env_api_key = get_unified_gateway_environment()
        return EffectiveNewApiConfig(
            mode=MODE_UNIFIED,
            # The portable launcher is the one operator-facing source of
            # truth.  Give it precedence so an old settings.db endpoint cannot
            # silently pin one subsystem to a retired relay after a migration.
            source="unified-environment" if env_base_url and env_api_key else "unified",
            base_url=env_base_url
            or normalize_relay_base_url(settings.get("unified_gateway_base_url", "")),
            api_key=env_api_key
            or normalize_api_key(settings.get("unified_gateway_api_key", "")),
        )
    if mode == MODE_CUSTOM:
        return EffectiveNewApiConfig(
            mode=MODE_CUSTOM,
            source="custom",
            base_url=normalize_relay_base_url(settings.get("custom_newapi_base_url", "")),
            api_key=normalize_api_key(settings.get("custom_newapi_api_key", "")),
        )
    db_official_api_key = normalize_api_key(settings.get("official_newapi_api_key", ""))
    return EffectiveNewApiConfig(
        mode=MODE_OFFICIAL,
        source="official",
        base_url=normalize_relay_base_url(OFFICIAL_NEWAPI_BASE_URL),
        api_key=db_official_api_key,
    )


def _int_setting(value: str | None, default: int) -> int:
    try:
        return int(str(value or "").strip())
    except ValueError:
        return default


def _bool_setting(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def get_effective_media_relay_config(
    *,
    env_provider: str | None = None,
    env_ttl_seconds: int | str | None = None,
    env_endpoint: str | None = None,
    env_bucket: str | None = None,
    env_access_key_id: str | None = None,
    env_access_key_secret: str | None = None,
    env_cloud_name: str | None = None,
    env_cloudinary_api_key: str | None = None,
    env_cloudinary_api_secret: str | None = None,
    env_cloudinary_folder: str | None = None,
) -> EffectiveMediaRelayConfig:
    settings = get_model_gateway_settings() if _uses_ce_gateway_settings() else {}
    db_provider = str(settings.get("media_relay_provider", "")).strip().lower()
    db_endpoint = str(settings.get("oss_relay_endpoint", "")).strip()
    db_bucket = str(settings.get("oss_relay_bucket", "")).strip()
    db_access_key_id = str(settings.get("oss_relay_ak", "")).strip()
    db_access_key_secret = str(settings.get("oss_relay_sk", "")).strip()
    db_cloud_name = str(settings.get("cloudinary_relay_cloud_name", "")).strip()
    db_cloudinary_api_key = str(settings.get("cloudinary_relay_api_key", "")).strip()
    db_cloudinary_api_secret = str(
        settings.get("cloudinary_relay_api_secret", "")
    ).strip()
    db_cloudinary_folder = (
        str(settings.get("cloudinary_relay_folder", "")).strip().strip("/")
    )
    has_db_config = any(
        [
            db_provider,
            db_endpoint,
            db_bucket,
            db_access_key_id,
            db_access_key_secret,
            db_cloud_name,
            db_cloudinary_api_key,
            db_cloudinary_api_secret,
            db_cloudinary_folder,
        ]
    )
    if has_db_config:
        return EffectiveMediaRelayConfig(
            source="database",
            provider=db_provider or "aliyun_oss",
            ttl_seconds=_int_setting(settings.get("media_relay_ttl_seconds"), 1800),
            endpoint=db_endpoint,
            bucket=db_bucket,
            access_key_id=db_access_key_id,
            access_key_secret=db_access_key_secret,
            cloud_name=db_cloud_name,
            cloudinary_api_key=db_cloudinary_api_key,
            cloudinary_api_secret=db_cloudinary_api_secret,
            cloudinary_folder=db_cloudinary_folder,
        )

    raw_ttl = (
        env_ttl_seconds
        if env_ttl_seconds is not None
        else os.environ.get(
            "MEDIA_RELAY_TTL_SECONDS",
            "1800",
        )
    )
    return EffectiveMediaRelayConfig(
        source="environment",
        provider=str(
            env_provider or os.environ.get("MEDIA_RELAY_PROVIDER", "aliyun_oss")
        )
        .strip()
        .lower(),
        ttl_seconds=_int_setting(str(raw_ttl), 1800),
        endpoint=str(env_endpoint or os.environ.get("OSS_RELAY_ENDPOINT", "")).strip(),
        bucket=str(env_bucket or os.environ.get("OSS_RELAY_BUCKET", "")).strip(),
        access_key_id=str(
            env_access_key_id or os.environ.get("OSS_RELAY_AK", "")
        ).strip(),
        access_key_secret=str(
            env_access_key_secret or os.environ.get("OSS_RELAY_SK", "")
        ).strip(),
        cloud_name=str(
            env_cloud_name or os.environ.get("CLOUDINARY_RELAY_CLOUD_NAME", "")
        ).strip(),
        cloudinary_api_key=str(
            env_cloudinary_api_key or os.environ.get("CLOUDINARY_RELAY_API_KEY", "")
        ).strip(),
        cloudinary_api_secret=str(
            env_cloudinary_api_secret
            or os.environ.get("CLOUDINARY_RELAY_API_SECRET", "")
        ).strip(),
        cloudinary_folder=str(
            env_cloudinary_folder or os.environ.get("CLOUDINARY_RELAY_FOLDER", "")
        )
        .strip()
        .strip("/"),
    )


def get_effective_cognee_embedding_config(
    *,
    env_provider: str | None = None,
    env_model: str | None = None,
    env_dimensions: str | int | None = None,
    llm_provider: str | None = None,
) -> EffectiveCogneeEmbeddingConfig:
    from novelvideo.generators.direct_models import (
        resolved_direct_embedding_dimensions,
        resolve_direct_model,
    )

    direct = resolve_direct_model("embedding")
    if direct is not None:
        from novelvideo.generators.direct_models import is_direct_model_runtime_ready

        if not is_direct_model_runtime_ready(direct):
            direct = None
    if direct is not None:
        return EffectiveCogneeEmbeddingConfig(
            source="direct-registry",
            provider="openai",
            model=direct.catalog_id,
            dimensions=str(resolved_direct_embedding_dimensions(direct)),
            upstream_provider="direct",
            upstream_model=direct.upstream_model,
            batch_size=DEFAULT_EMBEDDING_BATCH_SIZE,
        )
    saved: dict[str, Any] = {}
    if _uses_ce_gateway_settings():
        gateway = get_effective_newapi_config()
        if gateway.mode == MODE_CUSTOM:
            saved = get_newapi_embedding_model_config()
    if saved:
        saved_batch_size = str(
            saved.get("batchSize")
            or os.environ.get("EMBEDDING_BATCH_SIZE", DEFAULT_EMBEDDING_BATCH_SIZE)
        ).strip()
        if saved_batch_size:
            saved_batch_size = str(_int_setting(saved_batch_size, 0) or "")
        return EffectiveCogneeEmbeddingConfig(
            source="database",
            provider="newapi",
            model=str(saved["internalModel"]),
            dimensions=str(saved["dimension"]),
            upstream_provider=str(saved["provider"]),
            upstream_model=str(saved["upstreamModel"]),
            batch_size=saved_batch_size or DEFAULT_EMBEDDING_BATCH_SIZE,
        )

    # CE 的新项目只允许使用模型中心的 direct/embedding 注册项。保留
    # environment 分支给旧版 EE 运行时与历史项目兼容，但不再让 CE 的
    # 环境变量或隐藏默认制造一个新的向量模型。
    del env_provider, llm_provider
    if is_ce_effective():
        return EffectiveCogneeEmbeddingConfig(
            source="unconfigured",
            provider="",
            model="",
            dimensions="",
            upstream_provider="",
            upstream_model="",
            batch_size="",
        )
    provider = DEFAULT_COGNEE_EMBEDDING_PROVIDER
    default_model = DEFAULT_COGNEE_EMBEDDING_MODEL
    model = str(
        env_model or os.environ.get("COGNEE_EMBEDDING_MODEL", default_model)
    ).strip()
    dimensions = (
        str(
            env_dimensions
            if env_dimensions is not None
            else os.environ.get("COGNEE_EMBEDDING_DIM", DEFAULT_COGNEE_EMBEDDING_DIM)
        ).strip()
        or DEFAULT_COGNEE_EMBEDDING_DIM
    )
    batch_size = str(
        os.environ.get("EMBEDDING_BATCH_SIZE", DEFAULT_EMBEDDING_BATCH_SIZE)
    ).strip()
    if batch_size:
        batch_size = str(_int_setting(batch_size, 0) or "")
    if not batch_size:
        batch_size = DEFAULT_EMBEDDING_BATCH_SIZE
    return EffectiveCogneeEmbeddingConfig(
        source="environment",
        provider=provider,
        model=model,
        dimensions=dimensions,
        upstream_provider="",
        upstream_model="",
        batch_size=batch_size,
    )


def build_model_gateway_status(
    *,
    official_base_url: str | None = None,
    official_api_key: str | None = None,
) -> dict[str, Any]:
    uses_ce_settings = _uses_ce_gateway_settings()
    settings = get_model_gateway_settings() if uses_ce_settings else {}
    official_base_url_value = normalize_relay_base_url(
        OFFICIAL_NEWAPI_BASE_URL
        if uses_ce_settings
        else (
            os.environ.get("NEWAPI_BASE_URL", "")
            or official_base_url
            or OFFICIAL_NEWAPI_BASE_URL
        )
    )
    env_official_api_key = (
        ""
        if uses_ce_settings
        else normalize_api_key(
            official_api_key
            if official_api_key is not None
            else os.environ.get("NEWAPI_API_KEY", "")
        )
    )
    db_official_api_key = (
        normalize_api_key(settings.get("official_newapi_api_key", ""))
        if uses_ce_settings
        else ""
    )
    official_api_key_value = (
        db_official_api_key if uses_ce_settings else env_official_api_key
    )
    custom_base_url = (
        normalize_relay_base_url(settings.get("custom_newapi_base_url", ""))
        if uses_ce_settings
        else ""
    )
    custom_api_key = (
        normalize_api_key(settings.get("custom_newapi_api_key", ""))
        if uses_ce_settings
        else ""
    )

    env_unified_base_url, env_unified_api_key = get_unified_gateway_environment()
    unified_base_url = (
        normalize_relay_base_url(settings.get("unified_gateway_base_url", ""))
        if uses_ce_settings
        else ""
    ) or env_unified_base_url
    unified_api_key = (
        normalize_api_key(settings.get("unified_gateway_api_key", ""))
        if uses_ce_settings
        else ""
    ) or env_unified_api_key
    effective = get_effective_newapi_config(
        official_base_url=official_base_url,
        official_api_key=official_api_key,
    )
    return {
        "mode": effective.mode,
        "effective": {
            "source": effective.source,
            "baseUrl": effective.base_url,
            "apiKeyPreview": mask_secret(effective.api_key),
            "configured": bool(effective.base_url and effective.api_key),
        },
        "unified": {
            "baseUrl": unified_base_url,
            "apiKeyPreview": mask_secret(unified_api_key),
            "configured": bool(unified_base_url and unified_api_key),
            "source": "database" if uses_ce_settings else "environment",
            "environment": {
                "baseUrl": env_unified_base_url,
                "apiKeyPreview": mask_secret(env_unified_api_key),
                "configured": bool(env_unified_base_url and env_unified_api_key),
            },
        },
        "official": {
            "baseUrl": official_base_url_value,
            "apiKeyPreview": mask_secret(official_api_key_value),
            "configured": bool(official_base_url_value and official_api_key_value),
            "source": "database" if uses_ce_settings else "environment",
            "environment": {
                "baseUrl": official_base_url_value,
                "apiKeyPreview": mask_secret(env_official_api_key),
                "configured": bool(official_base_url_value and env_official_api_key),
            },
        },
        "custom": {
            "baseUrl": custom_base_url,
            "apiKeyPreview": mask_secret(custom_api_key),
            "configured": bool(custom_base_url and custom_api_key),
            "adminBaseUrl": settings.get("custom_newapi_admin_base_url", ""),
            "tokenName": settings.get("custom_newapi_token_name", ""),
            "tokenId": settings.get("custom_newapi_token_id", ""),
        },
    }


def get_direct_video_models() -> list[dict[str, Any]]:
    """Load operator-configured video APIs from the local CE settings store."""
    settings = _migrate_retired_direct_model_channels()
    return _decode_direct_video_models(settings.get("direct_video_models"))


class DirectModelClearNotConfirmed(ValueError):
    """Raised when a destructive registry replacement was not confirmed."""


def _require_clear_confirmation(
    *,
    family_label: str,
    existing_count: int,
    replacement: list[dict[str, Any]],
    confirm_clear: bool,
) -> None:
    """Keep an accidental empty PUT from deleting a configured model family."""
    if replacement or confirm_clear or existing_count <= 0:
        return
    raise DirectModelClearNotConfirmed(
        f"refusing to clear {existing_count} saved {family_label} "
        "without confirmClear=true"
    )


def save_direct_video_models(
    models: list[dict[str, Any]],
    *,
    confirm_clear: bool = False,
) -> list[dict[str, Any]]:
    """Replace the direct-video registry while preserving omitted existing keys.

    The UI submits a blank ``apiKey`` when a previously stored credential is
    unchanged, so routine name/capability updates do not reveal or erase it.
    Replacing a non-empty registry with an empty one requires an explicit
    ``confirm_clear`` from the caller.
    """
    existing_by_id = {item["id"]: item for item in get_direct_video_models()}
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in models:
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label") or "").strip()
        model_id = normalize_direct_model_id("video", raw.get("modelId"))
        if not label:
            raise ValueError("label is required for direct video model")
        if not model_id:
            raise ValueError("modelId is required for direct video model")
        base_url = _normalize_direct_video_base_url(raw.get("baseUrl"))
        record_id = _direct_video_record_id(
            raw.get("id"), model_id=model_id, base_url=base_url
        )
        if record_id in seen:
            raise ValueError(f"duplicate direct video model id: {record_id}")
        seen.add(record_id)
        existing = existing_by_id.get(record_id, {})
        api_key = normalize_api_key(raw.get("apiKey")) or _saved_api_key_for_endpoint(
            existing,
            base_url=base_url,
        )
        if not api_key:
            raise ValueError(f"apiKey is required for direct video model: {label}")
        requested_protocol = _normalize_direct_video_protocol(
            raw.get("protocol")
            or raw.get("requestedProtocol")
            or existing.get("requestedProtocol")
            or DIRECT_VIDEO_PROTOCOL_AUTO
        )
        protocol = _infer_direct_video_protocol(
            requested_protocol,
            base_url=base_url,
            model_id=model_id,
        )
        identity_changed = bool(
            existing
            and existing.get("baseUrl") == base_url
            and existing.get("modelId") == model_id
            and (
                str(existing.get("apiKey") or "") != api_key
                or str(existing.get("requestedProtocol") or "auto")
                != requested_protocol
            )
        )
        if identity_changed:
            from novelvideo.generators.video.direct_video_capability_cache import (
                invalidate_capability_for_model,
            )

            invalidate_capability_for_model(
                base_url=base_url,
                upstream_model=model_id,
                protocol=requested_protocol,
                reason="渠道凭据或协议已变化，请重新检测连接",
            )
        # 渠道自带的出线合同：写进来时严格校验，拼错字段/类型错一律 400，
        # 不许半对半错地存进去，也不许悄悄当成「没有合同」。
        #
        # 没带合同的新渠道在这里就地补一份：探测过的渠道按协议推得出来，就自动有；
        # 推不出来（比如 MiniMax 原生 v2、AutoDL）就留空 —— 那两条路的传输合同本来
        # 就按协议挂在渠道上，不靠档案编译。
        raw_contract = raw.get("wireContract") or existing.get("wireContract")
        wire_contract: dict[str, Any] | None = None
        if raw_contract:
            wire_contract = normalize_wire_contract_record(raw_contract)
        elif _cached_capability(base_url=base_url, model_id=model_id, protocol=protocol):
            # 只有**探测过**的渠道才自动补一份合同。没探测过就补，等于把「协议字段
            # 的默认值」当成事实写进渠道，会盖掉按名字命中的那份种子档位 —— 那正是
            # 本次要消掉的「第二份真相」。
            from novelvideo.generators.video.upstream_profiles import (
                channel_contract_from_probe,
            )

            wire_contract = channel_contract_from_probe(
                protocol=protocol,
                runtime_verified=_cached_runtime_verified(
                    base_url=base_url, model_id=model_id, protocol=protocol
                ),
            )
        is_default = bool(raw.get("isDefault", False))
        record = {
            "id": record_id,
            "label": label,
            "modelId": model_id,
            "baseUrl": base_url,
            "apiKey": api_key,
            "requestedProtocol": requested_protocol,
            "protocol": protocol,
            "enabled": bool(raw.get("enabled", True)),
            "isDefault": is_default,
        }
        if wire_contract is not None:
            # 没有合同就不写这个键。理由是现场验收要能逐字节断言「原样保存＝一个字节都
            # 没变」：老记录本来没有这个键，凭空补一个 ``null`` 会让这句话不成立。
            record["wireContract"] = wire_contract
        normalized.append(record)
    default_index = next(
        (
            idx
            for idx, item in enumerate(normalized)
            if item["isDefault"] and item["enabled"]
        ),
        -1,
    )
    if default_index < 0:
        default_index = next(
            (idx for idx, item in enumerate(normalized) if item["enabled"]),
            -1,
        )
    normalized = [
        {**item, "isDefault": index == default_index}
        for index, item in enumerate(normalized)
    ]
    _require_clear_confirmation(
        family_label="direct video models",
        existing_count=len(existing_by_id),
        replacement=normalized,
        confirm_clear=confirm_clear,
    )
    _write_many(
        {
            "direct_video_models": json.dumps(
                normalized, ensure_ascii=False, separators=(",", ":")
            )
        }
    )
    return normalized


def persist_probed_wire_contract(
    record_id: str,
    *,
    protocol: object,
    runtime_verified: bool = False,
) -> dict[str, Any] | None:
    """把探测推导出的渠道合同写回**那一条渠道**，其它渠道一个字不动。

    写回规则：只在「本来没有合同」或「新证据不比旧的弱」时覆盖。已经带真实提交
    证据的渠道不会被一次目录探测降级 —— 证据只许升不许降。

    返回写回后的合同记录；这条渠道不存在、或这份协议推不出合同时返回 ``None``。
    """

    from novelvideo.generators.video.upstream_profiles import (
        channel_contract_from_probe,
    )

    target = str(record_id or "").strip().lower()
    if not target:
        return None
    models = get_direct_video_models()
    index = next(
        (position for position, item in enumerate(models) if item.get("id") == target),
        -1,
    )
    if index < 0:
        return None
    candidate = channel_contract_from_probe(
        protocol=protocol,
        runtime_verified=runtime_verified,
    )
    if candidate is None:
        return None
    existing = models[index].get("wireContract")
    if isinstance(existing, dict):
        from novelvideo.generators.video.channel_wire_contract import (
            WIRE_EVIDENCE_PROBE,
        )

        existing_evidence = str(existing.get("evidence") or "").strip().lower()
        if (
            candidate.get("evidence") == WIRE_EVIDENCE_PROBE
            and existing_evidence
            and existing_evidence != WIRE_EVIDENCE_PROBE
        ):
            return existing
    models[index] = {**models[index], "wireContract": candidate}
    save_direct_video_models(models, confirm_clear=True)
    return candidate


def build_direct_video_models_status() -> list[dict[str, Any]]:
    """Return browser-safe video settings plus the runtime capability contract."""
    from novelvideo.generators.video.direct_models import (
        DirectVideoModel,
        direct_video_model_option,
    )
    from novelvideo.generators.video.direct_video_capability_cache import (
        get_cached_capability_for_model,
    )

    status: list[dict[str, Any]] = []
    capability_keys = (
        "family",
        "supportedModes",
        "supported_modes",
        "referenceLimits",
        "reference_limits",
        "parameterDefaults",
        "parameter_defaults",
        "resolutionOptions",
        "resolution_options",
        "advertisedResolutionOptions",
        "advertised_resolution_options",
        "runtimeResolutionOptions",
        "runtime_resolution_options",
        "runtimeRejectedResolutionOptions",
        "runtime_rejected_resolution_options",
        "runtimeCapabilityStatus",
        "runtime_capability_status",
        "runtimeCapabilityNote",
        "runtime_capability_note",
        "verificationStage",
        "verification_stage",
        "promptRules",
        "prompt_rules",
        "failureGracePolls",
        "failure_grace_polls",
        "sizeOptions",
        "size_options",
        "sizeField",
        "size_field",
        "aspectRatioOptions",
        "aspect_ratio_options",
        "nativeAudio",
        "native_audio",
        "minDuration",
        "min_duration",
        "maxDuration",
        "max_duration",
        "useCase",
        "use_case",
        "priceHint",
        "price_hint",
        "recommendation",
        "adapterFamily",
        "adapterConfidence",
        "adapterEvidence",
        "capabilityEnvelopeVersion",
        "capability_envelope_version",
        "parameters",
        "providerMapping",
        "provider_mapping",
        "mapping",
        "mediaInputs",
        "media_inputs",
        "audioInputSemantics",
        "audio_input_semantics",
        "opaque",
        "workflowId",
        "workflowName",
        "workflowInputRules",
        "resolutionMappings",
        "workflowDiscovery",
        # 出线合同的来源与证据级别：模型中心据此显示「合同是渠道自带的、还是按名字猜的」。
        "wireContractSource",
        "wireContractSourceLabel",
        "wireContractProfileId",
        "wireContractEvidence",
        "wireContractVerified",
        "wireContractEvidenceAt",
        "wireContractSeedProfileId",
        "wireContractConflictsWithSeed",
    )
    for item in get_direct_video_models():
        model = DirectVideoModel(
            registry_id=str(item["id"]),
            label=str(item["label"]),
            upstream_model=str(item["modelId"]),
            base_url=str(item["baseUrl"]),
            api_key=str(item["apiKey"]),
            enabled=bool(item["enabled"]),
            requested_protocol=str(item.get("requestedProtocol") or DIRECT_VIDEO_PROTOCOL_AUTO),
            protocol=str(item.get("protocol") or DIRECT_VIDEO_PROTOCOL_UNRESOLVED),
            is_default=bool(item.get("isDefault", False)),
            # 渠道自带的出线合同必须跟着一起进来；少了它，模型中心显示的档位与
            # 运行时报文就会用「按名字猜」的那份，两处对不上。
            wire_contract=(
                dict(item["wireContract"])
                if isinstance(item.get("wireContract"), Mapping)
                else None
            ),
        )
        try:
            capability = direct_video_model_option(model)
        except (TypeError, ValueError) as exc:
            # One stale or incomplete upstream capability record must not make
            # the whole model-center response fail. Keep the operator's row
            # visible and actionable while the model is re-probed.
            status.append(
                {
                    "id": item["id"],
                    "label": item["label"],
                    "modelId": item["modelId"],
                    "baseUrl": item["baseUrl"],
                    "enabled": item["enabled"],
                    "configured": bool(item["apiKey"]),
                    "apiKeyPreview": mask_secret(item["apiKey"]),
                    "requestedProtocol": item.get(
                        "requestedProtocol", DIRECT_VIDEO_PROTOCOL_AUTO
                    ),
                    "protocol": str(
                        item.get("protocol") or DIRECT_VIDEO_PROTOCOL_UNRESOLVED
                    ),
                    "protocolLabel": _direct_video_protocol_label(
                        str(item.get("protocol") or DIRECT_VIDEO_PROTOCOL_UNRESOLVED)
                    ),
                    "runtimeReady": False,
                    "disabled": True,
                    "disabledReason": (
                        "模型能力合同无效，请重新检测该模型；"
                        f"{type(exc).__name__}"
                    ),
                    "verificationStatus": "invalid-contract",
                    "catalogVerification": "unverified",
                    "modelFound": None,
                    "discoveredModelCount": 0,
                    "detectedProtocol": "",
                    "effectiveProtocol": str(
                        item.get("protocol") or DIRECT_VIDEO_PROTOCOL_UNRESOLVED
                    ),
                    "supportedProtocols": [],
                    "verificationStage": "invalid",
                    "isDefault": bool(item.get("isDefault", False)),
                    "family": "direct",
                    "supportedModes": [],
                    "referenceLimits": {},
                    "reference_limits": {},
                    "parameterDefaults": {},
                    "resolutionOptions": [],
                    "resolution_options": [],
                    "advertisedResolutionOptions": [],
                    "advertised_resolution_options": [],
                    "runtimeResolutionOptions": [],
                    "runtime_resolution_options": [],
                    "runtimeRejectedResolutionOptions": [],
                    "runtime_rejected_resolution_options": [],
                    "runtimeCapabilityStatus": "invalid",
                    "runtime_capability_status": "invalid",
                    "runtimeCapabilityNote": "请重新检测连接以重建能力合同",
                    "runtime_capability_note": "请重新检测连接以重建能力合同",
                    "promptRules": [],
                    "prompt_rules": [],
                    "failureGracePolls": 0,
                    "failure_grace_polls": 0,
                    "sizeOptions": [],
                    "size_options": [],
                    "sizeField": "",
                    "size_field": "",
                    "aspectRatioOptions": [],
                    "aspect_ratio_options": [],
                    "nativeAudio": "unsupported",
                    "native_audio": "unsupported",
                    "minDuration": None,
                    "min_duration": None,
                    "maxDuration": None,
                    "max_duration": None,
                    "useCase": "能力合同无效，暂未映射到视频节点",
                    "use_case": "能力合同无效，暂未映射到视频节点",
                    "priceHint": "",
                    "price_hint": "",
                    "recommendation": "重新检测连接",
                    "adapterFamily": "",
                    "adapterConfidence": 0.0,
                    "adapterEvidence": {},
                    "wireContractError": str(item.get("wireContractError") or ""),
                }
            )
            continue
        cached = get_cached_capability_for_model(
            base_url=str(item["baseUrl"]),
            upstream_model=str(item["modelId"]),
            protocol=model.effective_protocol,
        )
        verification_status = str(
            cached.get("verificationStatus") or "unverified"
        )
        detected_protocol = str(cached.get("detectedProtocol") or "")
        runtime_ready = model.runtime_ready
        adapter_family = str(model.adapter_family or "").strip()
        display_protocol = adapter_family or model.effective_protocol
        status.append(
            {
                "id": item["id"],
                "label": item["label"],
                "modelId": item["modelId"],
                "baseUrl": item["baseUrl"],
                "enabled": item["enabled"],
                "configured": bool(item["apiKey"]),
                "apiKeyPreview": mask_secret(item["apiKey"]),
                "requestedProtocol": item.get("requestedProtocol", DIRECT_VIDEO_PROTOCOL_AUTO),
                "protocol": model.effective_protocol,
                "protocolLabel": _direct_video_protocol_label(display_protocol),
                "runtimeReady": runtime_ready
                and _direct_video_protocol_runtime_ready(model.effective_protocol),
                "disabled": not bool(item["enabled"]) or not runtime_ready,
                "disabledReason": (
                    "模型已在直连视频模型管理中停用"
                    if not item["enabled"]
                    else
                    "上游模型目录未找到该模型 ID，请核对模型 ID 后重新检测"
                    if model.catalog_rejects_model_id
                    else "模型尚未通过上游目录校验，请先检测连接"
                    if not model.catalog_confirms_model_id
                    else "模型目录已匹配，但缺少视频生成能力证据，未映射到视频节点"
                    if not model.catalog_confirms_video_capability
                    else _direct_video_cached_failure_reason(cached)
                    or "协议尚未识别为可执行合同，请先检测连接"
                    if not runtime_ready
                    or not _direct_video_protocol_runtime_ready(model.effective_protocol)
                    else ""
                ),
                "verificationStatus": verification_status,
                "catalogVerification": model.catalog_verification,
                "modelFound": cached.get("modelFound"),
                "discoveredModelCount": int(
                    cached.get("discoveredModelCount") or 0
                ),
                "detectedProtocol": detected_protocol,
                "effectiveProtocol": model.effective_protocol,
                "supportedProtocols": cached.get("supportedProtocols") or [],
                "verificationStage": capability.get("verificationStage") or cached.get("verificationStage") or "unknown",
                # 这次「可用」到底是刚验的还是翻旧账：把最后一次真实检测时间
                # 一起交给前端，缓存结果不许冒充本次检测。
                "lastCheckedAt": str(
                    cached.get("credentialCheckedAt")
                    or cached.get("lastVerifiedAt")
                    or cached.get("verifiedAt")
                    or ""
                ),
                "credentialValidation": cached.get("credentialValidation") or {},
                "isDefault": bool(item.get("isDefault", False)),
                # 出线合同从哪来、验证到哪一步：模型中心与节点面板据此显示来源。
                "wireContract": item.get("wireContract"),
                "wireContractError": str(item.get("wireContractError") or ""),
                **{key: capability[key] for key in capability_keys},
            }
        )
    return status


def _merge_chat_family_models(
    groups: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Fold the retired agent/text/vision registries into one chat list.

    Rows that point at the same endpoint and model are the same model: the
    first row keeps its registry id and label, every duplicate contributes its
    capability flags and stays resolvable through ``aliases`` so canvas nodes
    bound to a retired id keep working.
    """

    merged: list[dict[str, Any]] = []
    index_by_endpoint: dict[tuple[str, str], int] = {}
    for group in groups:
        for item in group:
            endpoint = (
                str(item.get("baseUrl") or "").strip().lower(),
                str(item.get("modelId") or "").strip().lower(),
            )
            existing_index = index_by_endpoint.get(endpoint)
            if existing_index is None:
                index_by_endpoint[endpoint] = len(merged)
                merged.append(dict(item))
                continue
            existing = merged[existing_index]
            for key in DIRECT_MODEL_CAPABILITY_KEYS:
                existing[key] = bool(existing.get(key)) or bool(item.get(key))
            aliases = [*(existing.get("aliases") or ()), str(item["id"])]
            existing["aliases"] = list(
                dict.fromkeys(alias for alias in aliases if alias != existing["id"])
            )
    return merged


def get_direct_models(kind: str) -> list[dict[str, Any]]:
    """Load one non-video direct model family from the local CE settings store.

    Video remains a dedicated registry because its runtime needs a richer
    generation contract.  The remaining model families share this durable
    registry so their UI labels, node selectors and runtime adapters never
    depend on browser-local storage.  ``chat`` additionally absorbs the retired
    agent/text/vision registries; those names stay accepted everywhere.
    """
    normalized = canonical_direct_model_kind(kind)
    settings = _migrate_retired_direct_model_channels()
    if normalized == "chat":
        return _merge_chat_family_models(
            [
                _decode_direct_models(
                    settings.get(_raw_direct_model_setting_key(name)),
                    kind=name,
                )
                for name in ("chat", *LEGACY_CHAT_KINDS)
            ]
        )
    return _decode_direct_models(
        settings.get(_raw_direct_model_setting_key(normalized)),
        kind=normalized,
    )


def get_bound_direct_model_api_key(
    kind: str,
    record_id: object,
    base_url: object,
) -> str:
    """Return a saved key only when the requested registry row and endpoint match."""
    normalized_kind = str(kind or "").strip().lower()
    requested_id = str(record_id or "").strip().lower()
    if not requested_id:
        return ""
    if normalized_kind == "video":
        normalized_base_url = _normalize_direct_video_base_url(base_url)
        models = get_direct_video_models()
    else:
        _direct_model_setting_key(normalized_kind)
        normalized_base_url = _normalize_direct_model_base_url(base_url)
        models = get_direct_models(normalized_kind)
    existing = next(
        (
            item
            for item in models
            if requested_id
            in {
                str(item.get("id") or ""),
                *(str(alias or "") for alias in item.get("aliases") or ()),
            }
        ),
        {},
    )
    return _saved_api_key_for_endpoint(existing, base_url=normalized_base_url)


def _resolve_capability_flag(
    raw: dict[str, Any],
    existing: dict[str, Any],
    key: str,
) -> bool:
    """Keep a chat capability when the client omitted it instead of nulling it."""

    value = raw.get(key)
    if value is None:
        value = existing.get(key, True)
    return bool(value)


def save_direct_models(
    kind: str,
    models: list[dict[str, Any]],
    *,
    confirm_clear: bool = False,
) -> list[dict[str, Any]]:
    """Replace one direct-model family while retaining masked existing keys.

    Clearing a family that already has rows is a destructive operation and
    requires ``confirm_clear=True``. This guard lives at the settings-service
    boundary so non-UI callers cannot bypass the browser confirmation.
    """
    normalized_kind = canonical_direct_model_kind(kind)
    if normalized_kind in DIRECT_MODEL_RETIRED_KINDS:
        # ``_normalize_kind`` cannot raise for a retired family (the read paths
        # need it), so the write path re-checks here.  Otherwise widening the
        # read gate above would silently re-open saving into a withdrawn
        # family — the same Literal-drift shape that turned this into a 500.
        raise ValueError(f"direct model kind is retired: {kind}")
    # Model-id aliases are still keyed by the retired family the caller asked
    # for (a vision row's DC alias must keep rewriting), so keep both names.
    requested_kind = str(kind or "").strip().lower()
    chat_family = normalized_kind == "chat"
    setting_key = _direct_model_setting_key(normalized_kind)
    existing_by_id = {item["id"]: item for item in get_direct_models(normalized_kind)}
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    requested_default_index: int | None = None

    for index, raw in enumerate(models):
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label") or "").strip()
        model_id = normalize_direct_model_id(
            requested_kind or normalized_kind, raw.get("modelId")
        )
        if not label:
            raise ValueError(f"label is required for direct {normalized_kind} model")
        if not model_id:
            raise ValueError(f"modelId is required for direct {normalized_kind} model")
        base_url = _normalize_direct_model_base_url(raw.get("baseUrl"))
        record_id = _direct_model_record_id(
            normalized_kind,
            raw.get("id"),
            model_id=model_id,
            base_url=base_url,
        )
        if record_id in seen:
            raise ValueError(f"duplicate direct {normalized_kind} model id: {record_id}")
        seen.add(record_id)
        existing = existing_by_id.get(record_id, {})
        api_key = normalize_api_key(raw.get("apiKey")) or _saved_api_key_for_endpoint(
            existing,
            base_url=base_url,
        )
        if not api_key:
            raise ValueError(f"apiKey is required for direct {normalized_kind} model: {label}")
        from novelvideo.generators.direct_model_capabilities import (
            infer_direct_model_protocol,
            normalize_direct_model_protocol,
        )

        requested_protocol = normalize_direct_model_protocol(
            str(
                raw.get("protocol")
                or raw.get("requestedProtocol")
                or existing.get("requestedProtocol")
                or "auto"
            )
        )
        protocol = infer_direct_model_protocol(
            normalized_kind,
            model_id,
            base_url=base_url,
            requested_protocol=requested_protocol,
        )
        enabled = bool(raw.get("enabled", True))
        is_default = bool(raw.get("isDefault", False))
        if is_default and requested_default_index is None:
            requested_default_index = index
        normalized.append(
            {
                "id": record_id,
                "label": label,
                "modelId": model_id,
                "baseUrl": base_url,
                "apiKey": api_key,
                "requestedProtocol": requested_protocol,
                "protocol": protocol,
                "enabled": enabled,
                "isDefault": is_default,
                **(
                    {
                        "supportsTools": _resolve_capability_flag(
                            raw, existing, "supportsTools"
                        ),
                        "supportsVision": _resolve_capability_flag(
                            raw, existing, "supportsVision"
                        ),
                    }
                    if chat_family
                    else {}
                ),
            }
        )

    default_index = next(
        (
            idx
            for idx, item in enumerate(normalized)
            if item["isDefault"] and item["enabled"]
        ),
        -1,
    )
    if default_index < 0:
        default_index = next(
            (idx for idx, item in enumerate(normalized) if item["enabled"]),
            -1,
        )
    normalized = [
        {**item, "isDefault": index == default_index}
        for index, item in enumerate(normalized)
    ]
    _require_clear_confirmation(
        family_label=f"direct {normalized_kind} models",
        existing_count=len(existing_by_id),
        replacement=normalized,
        confirm_clear=confirm_clear,
    )
    # A protocol switch reuses the same endpoint/model fingerprint.  Remove
    # the old transport evidence so the row must be detected under the new
    # adapter before it is treated as current.
    from novelvideo.generators.direct_model_capability_cache import (
        invalidate_direct_model_capability,
    )

    for item in normalized:
        previous = existing_by_id.get(item["id"])
        if previous and str(previous.get("protocol") or "").strip().lower() != str(
            item.get("protocol") or ""
        ).strip().lower():
            invalidate_direct_model_capability(
                base_url=item["baseUrl"],
                kind=normalized_kind,
                upstream_model=item["modelId"],
            )
    _write_many(
        {
            setting_key: json.dumps(
                normalized,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            # The three retired chat registries were merged into this list, so
            # leaving them behind would resurrect stale rows on the next read.
            **(
                {_raw_direct_model_setting_key(name): "" for name in LEGACY_CHAT_KINDS}
                if chat_family
                else {}
            ),
        }
    )
    return normalized


def _direct_model_status_item(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    """Browser-safe direct-model payload shared by settings and pickers."""
    status: dict[str, Any] = {
        "id": item["id"],
        "label": item["label"],
        "modelId": item["modelId"],
        "baseUrl": item["baseUrl"],
        "enabled": item["enabled"],
        "isDefault": item["isDefault"],
        "configured": bool(item["apiKey"]),
        "apiKeyPreview": mask_secret(item["apiKey"]),
        "requestedProtocol": str(item.get("requestedProtocol") or "auto"),
        **(
            {
                "aliases": [str(alias) for alias in item.get("aliases") or ()],
                "supportsTools": bool(item.get("supportsTools", True)),
                "supportsVision": bool(item.get("supportsVision", True)),
            }
            if kind == "chat"
            else {}
        ),
    }
    from novelvideo.generators.direct_model_capabilities import (
        direct_model_capability_summary,
    )

    from novelvideo.generators.direct_model_capability_cache import (
        get_cached_direct_model_capability,
    )

    cached = get_cached_direct_model_capability(
        base_url=str(item.get("baseUrl") or ""),
        kind=kind,
        upstream_model=str(item.get("modelId") or ""),
    )
    metadata = cached.get("modelMetadata")
    status.update(
        direct_model_capability_summary(
            kind,
            str(item["modelId"]),
            protocol=str(item.get("requestedProtocol") or item.get("protocol") or "auto"),
            base_url=str(item.get("baseUrl") or ""),
            metadata=metadata if isinstance(metadata, dict) else None,
        )
    )
    if cached:
        for key in (
            "probeContractVersion",
            "verificationStatus",
            "detectedProtocol",
            "modelFound",
            "discoveredModelCount",
            "modelMetadata",
            "responsesProbeStatus",
            "responsesProbeError",
            "toolCallingVerified",
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
            "toolProbeStatus",
            "toolProbeMode",
            "toolHttpStatus",
            "toolProbeError",
            "visionProbeStatus",
            "visionHttpStatus",
            "visionResponseUsable",
            "visionProbeError",
            "hermesProbeStatus",
            "hermesProbeError",
            "embeddingProbeStatus",
            "embeddingHttpStatus",
            "embeddingResponseUsable",
            "embeddingProbeLatencyMs",
            "embeddingDimensions",
            "embeddingProbeError",
        ):
            if key in cached:
                status[key] = cached[key]
        catalog_missing = bool(
            cached.get("modelFound") is False
            and int(cached.get("discoveredModelCount") or 0) > 0
        )
        if catalog_missing:
            # Advisory only: a preview/experimental model can be callable
            # without being listed, so keep the notice but trust the probe.
            status["catalogMissing"] = True
            if str(cached.get("verificationStatus") or "") != "runtime-verified":
                status["runtimeReady"] = False
                status["runtime_ready"] = False
    from novelvideo.generators.direct_models import (
        DirectModel,
        direct_model_catalog_verification,
        is_direct_model_runtime_ready,
    )

    runtime_model = DirectModel(
        kind=kind,  # type: ignore[arg-type]
        registry_id=str(item["id"]),
        label=str(item["label"]),
        upstream_model=str(item["modelId"]),
        base_url=str(item["baseUrl"]),
        api_key=str(item["apiKey"]),
        protocol=str(status.get("protocol") or item.get("protocol") or ""),
        enabled=bool(item["enabled"]),
        is_default=bool(item["isDefault"]),
        supported_modes=(),
    )
    status["catalogVerification"] = direct_model_catalog_verification(runtime_model)
    status["runtimeReady"] = is_direct_model_runtime_ready(runtime_model)
    status["runtime_ready"] = status["runtimeReady"]
    from novelvideo.generators.direct_model_capability_cache import (
        direct_model_runtime_probe_complete,
    )

    status["runtimeProbeRequired"] = kind in {
        "chat",
        "agent",
        "text",
        "vision",
        "image",
        "embedding",
    }
    status["runtimeProbeComplete"] = bool(
        cached
        and str(cached.get("protocol") or "").strip().lower()
        == str(runtime_model.protocol or "").strip().lower()
        and direct_model_runtime_probe_complete(kind, cached)
    )
    if status["runtimeProbeRequired"] and not status["runtimeProbeComplete"]:
        # A legacy cache may still say runtime-verified because an ordinary
        # request succeeded.  Surface the missing contract instead of showing
        # a false green state in the model center.
        status["verificationStatus"] = (
            "degraded" if cached else status.get("verificationStatus") or "unverified"
        )
    if not item["enabled"]:
        status["disabledReason"] = "模型已在直连模型管理中停用"
    elif not status["runtimeReady"]:
        if status["catalogVerification"] == "catalog-mismatch":
            status["disabledReason"] = (
                "上游模型目录未找到该模型 ID，请核对模型 ID 后重新检测"
            )
        elif status["catalogVerification"] == "unverified":
            status["disabledReason"] = "模型尚未通过上游目录校验，请先检测连接"
        elif status["runtimeProbeRequired"] and not status["runtimeProbeComplete"]:
            status["disabledReason"] = "需要重新检测连接以完成当前运行合同"
    # One server-side verdict every consumer renders instead of re-deriving.
    status["usable"] = bool(status["runtimeReady"])
    status["usableReason"] = "" if status["usable"] else str(
        status.get("disabledReason") or "模型当前不可用"
    )
    return status


def build_direct_models_status() -> dict[str, list[dict[str, Any]]]:
    """Return every non-video direct family without exposing credentials.

    The retired ``agent`` / ``text`` / ``vision`` keys are still emitted as
    aliases of ``chat`` so older node pickers and bookmarked clients keep
    reading the same list instead of an empty one.
    """

    status: dict[str, list[dict[str, Any]]] = {}
    for kind in sorted(DIRECT_MODEL_KINDS):
        payload = [_direct_model_status_item(kind, item) for item in get_direct_models(kind)]
        status[kind] = payload
        if kind == "chat":
            for alias in LEGACY_CHAT_KINDS:
                status[alias] = payload
    return status


def build_newapi_database_status(
    *,
    sql_dsn: str | None = None,
    sqlite_path: str | None = None,
    admin_username: str | None = None,
) -> dict[str, Any]:
    settings = get_model_gateway_settings()
    db_sql_dsn = str(settings.get("custom_newapi_db_sql_dsn", "")).strip()
    db_sqlite_path = str(settings.get("custom_newapi_db_sqlite_path", "")).strip()
    db_admin_username = str(settings.get("custom_newapi_admin_username", "")).strip()
    env_sql_dsn = str(
        sql_dsn if sql_dsn is not None else os.environ.get("NEWAPI_SQL_DSN", "")
    )
    env_sql_dsn = env_sql_dsn.strip()
    env_sqlite_path = str(
        sqlite_path
        if sqlite_path is not None
        else os.environ.get("NEWAPI_SQLITE_PATH", "")
    ).strip()
    if not db_sql_dsn and not env_sql_dsn:
        from novelvideo.config import STATE_DIR

        env_sql_dsn = "local"
        env_sqlite_path = env_sqlite_path or str(
            Path(STATE_DIR) / "newapi" / "one-api.db"
        )
    effective_sql_dsn = db_sql_dsn or env_sql_dsn
    effective_sqlite_path = db_sqlite_path or env_sqlite_path
    source = (
        "database"
        if any([db_sql_dsn, db_sqlite_path, db_admin_username])
        else "environment"
    )
    configured = bool(
        effective_sql_dsn
        and (effective_sql_dsn != "local" or effective_sqlite_path)
    )
    available = configured
    if effective_sql_dsn == "local":
        available = bool(
            effective_sqlite_path
            and Path(effective_sqlite_path).expanduser().is_file()
        )
    return {
        "configured": configured,
        "available": available,
        "source": source,
        "databaseType": "sqlite" if effective_sql_dsn == "local" else "external",
    }


def build_media_relay_status(
    *,
    env_provider: str | None = None,
    env_ttl_seconds: int | str | None = None,
    env_endpoint: str | None = None,
    env_bucket: str | None = None,
    env_access_key_id: str | None = None,
    env_access_key_secret: str | None = None,
    env_cloud_name: str | None = None,
    env_cloudinary_api_key: str | None = None,
    env_cloudinary_api_secret: str | None = None,
    env_cloudinary_folder: str | None = None,
) -> dict[str, Any]:
    effective = get_effective_media_relay_config(
        env_provider=env_provider,
        env_ttl_seconds=env_ttl_seconds,
        env_endpoint=env_endpoint,
        env_bucket=env_bucket,
        env_access_key_id=env_access_key_id,
        env_access_key_secret=env_access_key_secret,
        env_cloud_name=env_cloud_name,
        env_cloudinary_api_key=env_cloudinary_api_key,
        env_cloudinary_api_secret=env_cloudinary_api_secret,
        env_cloudinary_folder=env_cloudinary_folder,
    )
    aliyun_configured = bool(
        effective.endpoint
        and effective.bucket
        and effective.access_key_id
        and effective.access_key_secret
    )
    cloudinary_name_valid = is_valid_cloudinary_cloud_name(effective.cloud_name)
    cloudinary_configured = bool(
        cloudinary_name_valid
        and effective.cloud_name
        and effective.cloudinary_api_key
        and effective.cloudinary_api_secret
    )
    configuration_error = ""
    if effective.provider == "cloudinary" and effective.cloud_name and not cloudinary_name_valid:
        configuration_error = (
            "Cloudinary Cloud Name 格式无效，只允许 ASCII 字母、数字、下划线和连字符"
        )
    return {
        "source": effective.source,
        "provider": effective.provider,
        "ttlSeconds": effective.ttl_seconds,
        "endpoint": effective.endpoint,
        "bucket": effective.bucket,
        "accessKeyIdPreview": mask_secret(effective.access_key_id),
        "accessKeySecretPreview": mask_secret(effective.access_key_secret),
        "cloudName": effective.cloud_name,
        "cloudinaryApiKeyPreview": mask_secret(effective.cloudinary_api_key),
        "cloudinaryApiSecretPreview": mask_secret(effective.cloudinary_api_secret),
        "apiFolder": effective.cloudinary_folder,
        "configurationError": configuration_error,
        "configured": (
            cloudinary_configured
            if effective.provider == "cloudinary"
            else aliyun_configured
        ),
    }
