"""Credential-free capability cache for non-video direct models."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
from typing import Any

from novelvideo.generators.direct_model_capabilities import normalize_direct_model_base_url
from novelvideo.utils.state_index_files import write_json_atomic


_CACHE_FILENAME = "direct_model_capability_cache.json"
_SCHEMA_VERSION = 1
# Runtime probes are part of the persisted contract.  A missing or older
# version must never be interpreted as a current successful verification.
# Keys are canonical families: agent/text/vision are views of ``chat``.
PROBE_CONTRACT_VERSIONS = {
    "chat": 1,
    "embedding": 1,
}
_SAFE_FIELDS = (
    "probeContractVersion",
    "verificationStatus",
    "detectedProtocol",
    "modelFound",
    "discoveredModelCount",
    "modelMetadata",
    "parameters",
    "parameterSchema",
    "parameter_schema",
    "workflowInputRules",
    "workflow_input_rules",
    "workflowDiscovery",
    "workflow_discovery",
    "workflowId",
    "workflow_id",
    "workflowName",
    "workflow_name",
    "mediaInputs",
    "media_inputs",
    "providerMapping",
    "provider_mapping",
    "voiceOptions",
    "voice_options",
    "audioFormats",
    "audio_formats",
    "lastFailure",
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
    "toolCallingVerified",
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
)
_lock = threading.Lock()
_memo: dict[str, Any] | None = None
_memo_path: Path | None = None


def _cache_path() -> Path:
    from novelvideo import config

    return Path(config.STATE_DIR) / _CACHE_FILENAME


def _url_hash(base_url: str) -> str:
    normalized = normalize_direct_model_base_url(base_url).lower()
    return hashlib.sha256(normalized.encode()).hexdigest()


def _fingerprint(*, base_url: str, kind: str, upstream_model: str) -> str:
    """Identity is the endpoint plus the model; the family is only an attribute.

    The family used to be part of the key, so renaming or merging families
    (agent/text/vision -> chat) threw away every saved probe and made live
    models look unverified.  ``kind`` stays in the signature because callers
    still pass it and the legacy fallback below needs it.
    """
    material = f"{_url_hash(base_url)}|{str(upstream_model or '').strip().lower()}".encode()
    return hashlib.sha256(material).hexdigest()


#: The pre-merge key joined its parts with newlines; keep it byte-identical.
_LEGACY_SEP = chr(10)


def _legacy_fingerprint(*, base_url: str, kind: str, upstream_model: str) -> str:
    """The pre-merge key that included the family; read-only compatibility."""
    material = (
        f"{_url_hash(base_url)}{_LEGACY_SEP}{str(kind or '').strip().lower()}"
        f"{_LEGACY_SEP}{str(upstream_model or '').strip().lower()}"
    ).encode()
    return hashlib.sha256(material).hexdigest()


def _family(kind: str) -> str:
    from novelvideo.model_gateway_settings import canonical_direct_model_kind

    try:
        return canonical_direct_model_kind(kind)
    except ValueError:
        return str(kind or "").strip().lower()


def _legacy_family_names(family: str) -> tuple[str, ...]:
    """Legacy key names to read for one family, newest spelling first."""
    if family == "chat":
        return ("chat", "agent", "text", "vision")
    return (family,)


def _entry_matches_family(entry: dict[str, Any], family: str) -> bool:
    stored = str(entry.get("kind") or "").strip().lower()
    return not stored or _family(stored) == family


def _load() -> dict[str, Any]:
    global _memo, _memo_path
    path = _cache_path()
    if _memo is not None and _memo_path == path:
        return _memo
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict) or payload.get("schemaVersion") != _SCHEMA_VERSION:
        payload = {"schemaVersion": _SCHEMA_VERSION, "entries": {}}
    if not isinstance(payload.get("entries"), dict):
        payload["entries"] = {}
    _memo = payload
    _memo_path = path
    return payload


def _save(payload: dict[str, Any]) -> None:
    global _memo, _memo_path
    path = _cache_path()
    write_json_atomic(path, payload)
    _memo = payload
    _memo_path = path


def get_cached_direct_model_capability(
    *, base_url: str, kind: str, upstream_model: str
) -> dict[str, Any]:
    entries = _load()["entries"]
    family = _family(kind)
    entry = entries.get(
        _fingerprint(base_url=base_url, kind=kind, upstream_model=upstream_model)
    )
    if isinstance(entry, dict) and _entry_matches_family(entry, family):
        return dict(entry)
    # Evidence recorded before the identity fix carried the family in its key;
    # keep reading it so existing installs never lose their probes.
    for legacy_kind in _legacy_family_names(family):
        entry = entries.get(
            _legacy_fingerprint(
                base_url=base_url,
                kind=legacy_kind,
                upstream_model=upstream_model,
            )
        )
        if isinstance(entry, dict):
            return dict(entry)
    return {}


def direct_model_runtime_probe_complete(kind: str, capability: dict[str, Any]) -> bool:
    """Return whether the saved evidence proves the selected runtime contract.

    The contract is keyed by canonical family: one chat probe proves the chat
    row for every surface that reads it (node, Agent, model center).  Asking
    per alias used to require a retired ``agent`` contract that the unified
    chat probe no longer writes, which silently emptied the Agent catalog.
    """

    family = _family(kind)
    required_version = PROBE_CONTRACT_VERSIONS.get(family)
    if required_version is None:
        # Image and audio probes intentionally avoid billable generation.  A
        # catalog + transport contract is the complete *non-billing* check for
        # these families; a successful output write may later upgrade the
        # status to runtime-verified without blocking the saved model.
        return bool(
            capability.get("modelFound") is True
            and capability.get("verificationStatus")
            in {"catalog-confirmed", "contract-resolved", "runtime-verified"}
        )
    # Version 2 was the retired Agent contract; it verified strictly more than
    # the current chat contract, so those rows keep their evidence.
    if int(capability.get("probeContractVersion") or 0) < required_version:
        return False
    if family == "chat":
        return bool(
            capability.get("chatProbeStatus") == "passed"
            and capability.get("chatResponseUsable") is True
            and capability.get("streamProbeStatus") == "passed"
            and capability.get("streamResponseUsable") is True
        )
    if family == "embedding":
        return bool(
            capability.get("embeddingProbeStatus") == "passed"
            and capability.get("embeddingResponseUsable") is True
            and int(capability.get("embeddingDimensions") or 0) > 0
        )
    return False


def record_direct_model_capability(
    *,
    base_url: str,
    kind: str,
    upstream_model: str,
    protocol: str,
    capability: dict[str, Any],
) -> dict[str, Any]:
    fingerprint = _fingerprint(
        base_url=base_url,
        kind=kind,
        upstream_model=upstream_model,
    )
    timestamp = datetime.now(timezone.utc).isoformat()
    current = get_cached_direct_model_capability(
        base_url=base_url,
        kind=kind,
        upstream_model=upstream_model,
    )
    sanitized = {key: capability[key] for key in _SAFE_FIELDS if key in capability}
    has_new_failure = bool(
        sanitized.get("lastFailure")
        or sanitized.get("chatProbeStatus") in {"rejected", "probe-failed"}
        or sanitized.get("streamProbeStatus") in {"rejected", "probe-failed"}
        or sanitized.get("toolProbeStatus") in {"rejected", "probe-failed"}
        or sanitized.get("hermesProbeStatus") in {"degraded", "probe-failed"}
        or sanitized.get("embeddingProbeStatus") in {"rejected", "probe-failed"}
    )
    if (
        current.get("verificationStatus") == "runtime-verified"
        and sanitized.get("modelFound") is True
        and str(current.get("protocol") or "").strip().lower()
        == str(protocol or "").strip().lower()
        and not has_new_failure
    ):
        sanitized["verificationStatus"] = "runtime-verified"
    entry = {
        "schemaVersion": _SCHEMA_VERSION,
        "fingerprint": fingerprint,
        "baseUrlHash": _url_hash(base_url),
        "kind": _family(kind),
        "upstreamModel": str(upstream_model or "").strip(),
        "protocol": str(protocol or "").strip().lower(),
        "lastVerifiedAt": timestamp,
        **sanitized,
    }
    with _lock:
        payload = _load()
        entries = dict(payload.get("entries") or {})
        entries[fingerprint] = entry
        _save({"schemaVersion": _SCHEMA_VERSION, "entries": entries})
    return dict(entry)


def invalidate_direct_model_capability(
    *, base_url: str, kind: str, upstream_model: str
) -> None:
    """Drop evidence whose transport protocol no longer matches a saved row."""

    fingerprints = {
        _fingerprint(base_url=base_url, kind=kind, upstream_model=upstream_model),
        *(
            _legacy_fingerprint(
                base_url=base_url,
                kind=legacy_kind,
                upstream_model=upstream_model,
            )
            for legacy_kind in _legacy_family_names(_family(kind))
        ),
    }
    with _lock:
        payload = _load()
        entries = dict(payload.get("entries") or {})
        if not fingerprints & set(entries):
            return
        for fingerprint in fingerprints:
            entries.pop(fingerprint, None)
        _save({"schemaVersion": _SCHEMA_VERSION, "entries": entries})


def record_direct_model_runtime_verified(
    *, base_url: str, kind: str, upstream_model: str, protocol: str
) -> dict[str, Any]:
    current = get_cached_direct_model_capability(
        base_url=base_url,
        kind=kind,
        upstream_model=upstream_model,
    )
    # Keep the historical runtime marker for compatibility with existing
    # cache readers.  Runtime readiness independently requires the complete
    # versioned probe evidence, so an ordinary chat request cannot unlock an
    # Agent that never passed stream/tool checks.
    current["verificationStatus"] = "runtime-verified"
    current["detectedProtocol"] = protocol
    current["modelFound"] = True
    current["discoveredModelCount"] = max(
        1,
        int(current.get("discoveredModelCount") or 0),
    )
    current.pop("lastFailure", None)
    return record_direct_model_capability(
        base_url=base_url,
        kind=kind,
        upstream_model=upstream_model,
        protocol=protocol,
        capability=current,
    )


__all__ = [
    "PROBE_CONTRACT_VERSIONS",
    "direct_model_runtime_probe_complete",
    "invalidate_direct_model_capability",
    "get_cached_direct_model_capability",
    "record_direct_model_capability",
    "record_direct_model_runtime_verified",
]
