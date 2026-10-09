"""Channel-scoped capability cache for direct video models.

The cache persists metadata discovered from ``GET /models``. Credentials and
raw endpoint URLs are deliberately excluded.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

from novelvideo.utils.state_index_files import write_json_atomic

from .direct_video_probe import normalize_probe_base_url


_CACHE_FILENAME = "video_capability_cache.json"
_SCHEMA_VERSION = 2
_CAPABILITY_FIELDS = (
    "source",
    "verificationStatus",
    "inputModalities",
    "declaredCapabilities",
    # Additive semantic contract for audio references.  Missing on legacy
    # entries means unknown; callers must not infer a voice-cloning mode.
    "audioInputSemantics",
    "audio_input_semantics",
    "modes",
    "sizeSlots",
    "sizeField",
    "resolutionOptions",
    "advertisedResolutionOptions",
    "runtimeResolutionOptions",
    "runtimeRejectedResolutionOptions",
    "runtimeCapabilityStatus",
    "runtimeCapabilityNote",
    "parameterDefaults",
    "aspectRatios",
    # Ratio/resolution provenance markers. A cached value must say whether the
    # upstream declared it (``catalog`` / ``catalog-size-slots``) or whether a
    # local profile/protocol contract filled it (``profile`` /
    # ``protocol-contract``); the node catalog must not present the second as
    # the first.
    "aspectRatioSource",
    "resolutionSource",
    "supportsCustomAspectRatio",
    "supportsCustomResolution",
    "durationRange",
    "durationOptions",
    "duration_options",
    "fpsOptions",
    "fps_options",
    "inputSlots",
    "input_slots",
    "returnLastFrame",
    "return_last_frame",
    "supportsCustomDuration",
    "supports_custom_duration",
    "nativeAudio",
    "referenceLimits",
    "referenceLimitsKnown",
    "promptRules",
    "failureGracePolls",
    "verificationStage",
    "supportedProtocols",
    "detectedProtocol",
    "adapterFamily",
    "adapterConfidence",
    "adapterEvidence",
    "openapiUrl",
    "openapiPaths",
    "openapiOperations",
    "openapiSubmitPath",
    "openapiQueryPath",
    # 「钥匙验过没有」必须和「目录读过没有」分开存：目录接口常常不需要
    # 鉴权，只记探测时间会让旧缓存继续冒充可用渠道。
    "credentialValidation",
    "credentialCheckedAt",
    # AutoDL/ComfyUI workflow metadata is a zero-billing contract. Keep the
    # sanitized input rules and resolution mappings so the node and adapter
    # use the same discovered schema instead of a static family guess.
    "workflowId",
    "workflowName",
    "workflowInputRules",
    "workflowDiscovery",
    "resolutionMappings",
    "transportContract",
    "modelFound",
    "discoveredModelCount",
    "lastFailure",
    "probeStatus",
    # Lossless capability envelope fields. These are additive so existing
    # schema-v2 cache entries remain readable and simply lack the new data.
    "capabilityEnvelopeVersion",
    "parameters",
    "providerMapping",
    "provider_mapping",
    "mapping",
    "mediaInputs",
    "media_inputs",
    "opaque",
)
_lock = threading.Lock()
_memo: dict[str, Any] | None = None
_memo_path: Path | None = None
_cache_sequence = 0


def _cache_path() -> Path:
    from novelvideo import config

    return Path(config.STATE_DIR) / _CACHE_FILENAME


def capability_fingerprint(*, base_url: str, protocol: str, upstream_model: str) -> str:
    normalized_url = normalize_probe_base_url(base_url).lower()
    normalized_protocol = str(protocol or "openai-video").strip().lower()
    normalized_model = str(upstream_model or "").strip().lower()
    material = f"{normalized_url}\n{normalized_protocol}\n{normalized_model}".encode()
    return hashlib.sha256(material).hexdigest()


def load_capability_cache() -> dict[str, Any]:
    """Return the versioned cache envelope without touching the network."""
    global _memo, _memo_path
    path = _cache_path()
    if _memo is not None and _memo_path == path:
        return _memo
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if isinstance(raw, dict) and raw.get("schemaVersion") == 1:
        raw = {
            "schemaVersion": _SCHEMA_VERSION,
            "entries": raw.get("entries") if isinstance(raw.get("entries"), dict) else {},
        }
    if not isinstance(raw, dict) or raw.get("schemaVersion") != _SCHEMA_VERSION:
        raw = {"schemaVersion": _SCHEMA_VERSION, "entries": {}}
    entries = raw.get("entries")
    if not isinstance(entries, dict):
        raw["entries"] = {}
    _memo = raw
    _memo_path = path
    return raw


def save_capability_cache(cache: dict[str, Any]) -> None:
    """Persist the capability cache atomically (temporary file + replace)."""
    global _memo, _memo_path
    path = _cache_path()
    write_json_atomic(path, cache)
    _memo = cache
    _memo_path = path


def get_cached_capability(
    *, base_url: str, protocol: str, upstream_model: str
) -> dict[str, Any]:
    fingerprint = capability_fingerprint(
        base_url=base_url,
        protocol=protocol,
        upstream_model=upstream_model,
    )
    entry = load_capability_cache()["entries"].get(fingerprint)
    return dict(entry) if isinstance(entry, dict) else {}


def _get_cached_capability_for_model_unlocked(
    *, base_url: str, upstream_model: str, protocol: str | None = None
) -> dict[str, Any]:
    """Return the newest matching snapshot while ``_lock`` is held."""
    normalized_url = normalize_probe_base_url(base_url).lower()
    base_url_hash = hashlib.sha256(normalized_url.encode()).hexdigest()
    normalized_model = str(upstream_model or "").strip().lower()
    matches = [
        item
        for item in load_capability_cache()["entries"].values()
        if isinstance(item, dict)
        and item.get("baseUrlHash") == base_url_hash
        and str(item.get("upstreamModel") or "").strip().lower() == normalized_model
        and (
            protocol is None
            or str(item.get("protocol") or "").strip().lower()
            == str(protocol or "").strip().lower()
        )
    ]
    matches.sort(
        key=lambda item: (
            str(item.get("lastVerifiedAt") or item.get("verifiedAt") or ""),
            int(item.get("cacheSequence") or 0),
        ),
        reverse=True,
    )
    return dict(matches[0]) if matches else {}


def get_cached_capability_for_model(
    *, base_url: str, upstream_model: str, protocol: str | None = None
) -> dict[str, Any]:
    """Return a snapshot, optionally restricted to its detected protocol."""
    with _lock:
        return _get_cached_capability_for_model_unlocked(
            base_url=base_url,
            upstream_model=upstream_model,
            protocol=protocol,
        )


def _record_capability_locked(
    *,
    base_url: str,
    protocol: str,
    upstream_model: str,
    capability: dict[str, Any],
    current: dict[str, Any] | None = None,
    replace_snapshot: bool = False,
) -> dict[str, Any]:
    """Replace one capability entry while ``_lock`` is held."""
    fingerprint = capability_fingerprint(
        base_url=base_url,
        protocol=protocol,
        upstream_model=upstream_model,
    )
    normalized_url = normalize_probe_base_url(base_url).lower()
    if current is None:
        current = _get_cached_capability_for_model_unlocked(
            base_url=base_url,
            upstream_model=upstream_model,
            protocol=protocol,
        )
    sanitized = {
        key: capability[key] for key in _CAPABILITY_FIELDS if key in capability
    }
    current_rejected = current.get("runtimeRejectedResolutionOptions")
    if not replace_snapshot and isinstance(current_rejected, list) and current_rejected:
        rejected = list(dict.fromkeys(
            str(item).strip().lower() for item in current_rejected if str(item).strip()
        ))
        advertised = (
            sanitized.get("advertisedResolutionOptions")
            or sanitized.get("resolutionOptions")
            or current.get("advertisedResolutionOptions")
            or current.get("resolutionOptions")
            or []
        )
        if not isinstance(advertised, list):
            advertised = []
        advertised_values = list(dict.fromkeys(
            str(item).strip().lower() for item in advertised if str(item).strip()
        ))
        effective = [item for item in advertised_values if item not in rejected]
        sanitized.update(
            {
                "advertisedResolutionOptions": advertised_values,
                "runtimeResolutionOptions": effective,
                "resolutionOptions": effective,
                "runtimeRejectedResolutionOptions": rejected,
                "runtimeCapabilityStatus": current.get("runtimeCapabilityStatus")
                or "degraded",
                "runtimeCapabilityNote": current.get("runtimeCapabilityNote") or "",
            }
        )
    if not replace_snapshot and (
        current.get("verificationStatus") == "runtime-verified"
        and sanitized.get("modelFound") is True
        and str(current.get("protocol") or "").strip().lower()
        == str(protocol or "").strip().lower()
    ):
        sanitized["verificationStatus"] = "runtime-verified"
        if current.get("verificationStage"):
            sanitized["verificationStage"] = current["verificationStage"]
    global _cache_sequence
    cache = load_capability_cache()
    entries = dict(cache.get("entries") or {})
    if replace_snapshot:
        normalized_model = str(upstream_model or "").strip().lower()
        base_url_hash = hashlib.sha256(normalized_url.encode()).hexdigest()
        entries = {
            key: item
            for key, item in entries.items()
            if not (
                isinstance(item, dict)
                and item.get("baseUrlHash") == base_url_hash
                and str(item.get("upstreamModel") or "").strip().lower()
                == normalized_model
            )
        }
    previous_sequence = max(
        (
            int(item.get("cacheSequence") or 0)
            for item in entries.values()
            if isinstance(item, dict)
        ),
        default=0,
    )
    # ``time.time_ns`` is normally enough, but test clocks and some Windows
    # timer sources can repeat. A process-local monotonic sequence makes newest
    # evidence deterministic even then.
    _cache_sequence = max(_cache_sequence, previous_sequence) + 1
    _cache_sequence = max(_cache_sequence, time.time_ns())
    entry = {
        "schemaVersion": _SCHEMA_VERSION,
        "fingerprint": fingerprint,
        "baseUrlHash": hashlib.sha256(normalized_url.encode()).hexdigest(),
        "protocol": str(protocol or "openai-video").strip().lower(),
        "upstreamModel": str(upstream_model or "").strip(),
        "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "lastVerifiedAt": datetime.now(timezone.utc).isoformat(),
        "cacheSequence": _cache_sequence,
        **sanitized,
    }
    entries[fingerprint] = entry
    cache = {"schemaVersion": _SCHEMA_VERSION, "entries": entries}
    save_capability_cache(cache)
    return dict(entry)


def record_capability(
    *,
    base_url: str,
    protocol: str,
    upstream_model: str,
    capability: dict[str, Any],
    replace_snapshot: bool = False,
) -> dict[str, Any]:
    """Replace one channel/model capability entry and persist it atomically.

    The previous implementation read the current entry before acquiring
    ``_lock`` and only serialized the final write. Two concurrent runtime
    observations (for example a measured submit route and a rejected
    resolution) could therefore both start from the same old value and the
    later write would silently erase the earlier field. Keep the entire
    read-merge-write transaction under one lock.
    """

    with _lock:
        return _record_capability_locked(
            base_url=base_url,
            protocol=protocol,
            upstream_model=upstream_model,
            capability=capability,
            replace_snapshot=replace_snapshot,
        )


def invalidate_capability_for_model(
    *, base_url: str, upstream_model: str, protocol: str, reason: str
) -> dict[str, Any]:
    """Replace a snapshot with a non-usable marker after binding identity changes."""
    with _lock:
        return _record_capability_locked(
            base_url=base_url,
            protocol=protocol,
            upstream_model=upstream_model,
            capability={
                "verificationStatus": "unverified",
                "probeStatus": "stale",
                "modelFound": False,
                "discoveredModelCount": 0,
                "detectedProtocol": "unresolved",
                "lastFailure": str(reason or "渠道配置已变化，请重新检测").strip()[:240],
            },
            replace_snapshot=True,
        )


def record_runtime_verification(
    *, base_url: str, protocol: str, upstream_model: str
) -> dict[str, Any]:
    with _lock:
        current = _get_cached_capability_for_model_unlocked(
            base_url=base_url,
            upstream_model=upstream_model,
            protocol=protocol,
        )
        current["verificationStatus"] = "runtime-verified"
        current["verificationStage"] = "artifact"
        current["detectedProtocol"] = str(protocol or "").strip().lower()
        current["modelFound"] = True
        current["discoveredModelCount"] = max(
            1,
            int(current.get("discoveredModelCount") or 0),
        )
        current.pop("lastFailure", None)
        return _record_capability_locked(
            base_url=base_url,
            protocol=protocol,
            upstream_model=upstream_model,
            capability=current,
            current=current,
        )


def record_runtime_resolution_rejection(
    *,
    base_url: str,
    protocol: str,
    upstream_model: str,
    resolution: str,
    note: str,
) -> dict[str, Any]:
    """Persist a deterministic gateway rejection without hiding catalog truth.

    ``resolutionOptions`` is the effective, currently usable set after the
    observation.  ``advertisedResolutionOptions`` remains the upstream
    catalog claim so settings can explain the discrepancy instead of silently
    presenting a button that is known to fail.
    """
    with _lock:
        current = _get_cached_capability_for_model_unlocked(
            base_url=base_url,
            upstream_model=upstream_model,
            protocol=protocol,
        )
        rejected = str(resolution or "").strip().lower()
        if not rejected:
            return current
        advertised = current.get("advertisedResolutionOptions")
        if not isinstance(advertised, list) or not advertised:
            advertised = current.get("resolutionOptions")
        advertised_values = list(dict.fromkeys(
            str(item).strip().lower() for item in (advertised or ()) if str(item).strip()
        ))
        if not advertised_values:
            from .direct_video_profiles import resolve_direct_video_profile

            advertised_values = list(
                resolve_direct_video_profile(upstream_model).resolution
            )
        rejected_values = current.get("runtimeRejectedResolutionOptions")
        rejected_list = list(dict.fromkeys(
            [*(rejected_values if isinstance(rejected_values, list) else ()), rejected]
        ))
        effective = [item for item in advertised_values if item not in rejected_list]
        if not effective:
            effective = [item for item in advertised_values if item != rejected]
        current.update(
            {
                "advertisedResolutionOptions": advertised_values,
                "runtimeResolutionOptions": effective,
                "resolutionOptions": effective,
                "runtimeRejectedResolutionOptions": rejected_list,
                "runtimeCapabilityStatus": "degraded",
                "runtimeCapabilityNote": str(note or "当前网关拒绝该分辨率").strip()[:240],
            }
        )
        current_defaults = current.get("parameterDefaults")
        if isinstance(current_defaults, dict) and current_defaults.get("resolution") == rejected:
            current["parameterDefaults"] = {
                **current_defaults,
                "resolution": effective[0] if effective else None,
            }
        return _record_capability_locked(
            base_url=base_url,
            protocol=protocol,
            upstream_model=upstream_model,
            capability=current,
            current=current,
        )


def record_runtime_submit_route(
    *,
    base_url: str,
    protocol: str,
    upstream_model: str,
    submit_path: str,
) -> dict[str, Any]:
    """Record the create route this gateway actually accepted.

    Discovery reads ``GET /models`` only, so a relay's registered create route
    stays invisible until a submission proves it — some NewAPI stations serve
    video creation from ``/video/generations`` while the OpenAI-compatible
    default is ``/videos``.  Recording the accepted route keeps the next
    submission from repeating a refused hop.

    The observed path is stored inside the existing ``transportContract``
    envelope under an explicit ``submitObserved`` marker, so the declared
    protocol contract and the measured one stay distinguishable in one place
    instead of becoming a parallel fact.
    """

    path = "/" + str(submit_path or "").strip().strip("/")
    if path == "/":
        return get_cached_capability_for_model(
            base_url=base_url,
            upstream_model=upstream_model,
            protocol=protocol,
        )
    with _lock:
        current = _get_cached_capability_for_model_unlocked(
            base_url=base_url,
            upstream_model=upstream_model,
            protocol=protocol,
        )
        transport = current.get("transportContract")
        transport_contract = dict(transport) if isinstance(transport, dict) else {}
        if (
            transport_contract.get("submitObserved")
            and str(transport_contract.get("submit") or "").strip() == path
        ):
            return current
        transport_contract.update(
            {
                "submit": path,
                "submitObserved": True,
                "submitObservedAt": datetime.now(timezone.utc).isoformat(),
            }
        )
        current["transportContract"] = transport_contract
        return _record_capability_locked(
            base_url=base_url,
            protocol=protocol,
            upstream_model=upstream_model,
            capability=current,
            current=current,
        )
