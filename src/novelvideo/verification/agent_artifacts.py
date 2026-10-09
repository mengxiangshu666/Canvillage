"""Stable, bounded artifact references exchanged by Agent specialists.

The workflow store already keeps step outputs, but those outputs historically
used a loose mixture of ``artifact_id``, provider URLs and ad-hoc dictionaries.
This module defines the small join contract that lets a specialist hand a
materialized result to the next specialist without copying the result body.

Only stable identifiers and hashes belong in the cross-layer projection.  A
provider URL may remain in an internal task record when required for recovery,
but it is deliberately omitted by :func:`project_agent_artifact_ref` unless it
is an internal ``artifact://``/project reference.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import re
from typing import Any


AGENT_ARTIFACT_SCHEMA = "agent_artifact.v1"
AGENT_ARTIFACT_ID_PREFIX = "artifact:"
AGENT_ARTIFACT_URI_PREFIX = "artifact://"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_STATUSES = frozenset(
    {
        "proposed",
        "pending",
        "materialized",
        "ready",
        "verified",
        "completed",
        "failed",
        "rejected",
    }
)
_INTERNAL_URI_PREFIXES = (
    "artifact://",
    "canvas://",
    "workflow://",
    "hogi://",
)


class AgentArtifactError(ValueError):
    """Raised when an explicit Agent artifact reference is malformed."""


def _text(value: object, *, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _read(raw: Mapping[str, Any], snake: str, camel: str, default: object = None) -> object:
    if snake in raw:
        return raw[snake]
    if camel in raw:
        return raw[camel]
    return default


def _sha256(value: object) -> str:
    candidate = _text(value, limit=64).lower()
    if not candidate:
        return ""
    if not _SHA256_RE.fullmatch(candidate):
        raise AgentArtifactError("artifact_sha256 must be a 64-character hexadecimal SHA-256")
    return candidate


def _source_refs(value: object) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple)):
        raise AgentArtifactError("source_refs must be a list")
    result: list[dict[str, Any]] = []
    for item in value[:32]:
        if isinstance(item, Mapping):
            kind = _text(item.get("kind") or item.get("type"), limit=60)
            ref_id = _text(
                item.get("id") or item.get("ref_id") or item.get("refId"),
                limit=300,
            )
            if not kind or not ref_id:
                raise AgentArtifactError("source_refs entries require kind and id")
            if _is_external_ref(ref_id):
                continue
            entry: dict[str, Any] = {"kind": kind, "id": ref_id}
            revision = item.get("revision")
            if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0:
                entry["revision"] = revision
        else:
            ref_id = _text(item, limit=300)
            if not ref_id:
                continue
            if _is_external_ref(ref_id):
                continue
            entry = {"kind": "reference", "id": ref_id}
        if entry not in result:
            result.append(entry)
    return result


def _is_external_ref(value: str) -> bool:
    lowered = value.casefold()
    if lowered.startswith(("data:", "file:")):
        return True
    return "://" in value and not lowered.startswith(_INTERNAL_URI_PREFIXES)


def _internal_uri(value: object) -> str:
    uri = _text(value, limit=1_000)
    if not uri:
        return ""
    lowered = uri.casefold()
    # Keep only stable, project-owned references in the normalized contract.
    # Signed provider URLs are intentionally not copied into Agent handoffs.
    if lowered.startswith(("data:", "file:")):
        return ""
    if "://" in uri and not lowered.startswith(_INTERNAL_URI_PREFIXES):
        return ""
    return uri


def _verification(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key in ("schema", "status", "result", "verdict", "error_code", "evidence_ref", "verified_at"):
        item = _text(value.get(key), limit=500)
        # External URLs can appear in evidence_ref; keep only internal joins.
        if key == "evidence_ref" and (
            "://" in item or item.casefold().startswith(("data:", "file:"))
        ):
            continue
        if item:
            result[key] = item
    return result


def normalize_agent_artifact_ref(
    value: object,
    *,
    producer_agent_id: str = "",
    consumer_agent_id: str = "",
    default_status: str = "materialized",
) -> dict[str, Any]:
    """Normalize one explicit artifact reference into ``agent_artifact.v1``.

    The function is intentionally strict for callers constructing a new
    artifact.  Use :func:`project_agent_artifact_ref` at transport boundaries
    where malformed legacy payloads should simply be ignored.
    """

    if isinstance(value, Mapping):
        nested = value.get("agent_artifact") or value.get("artifact_ref")
        raw: Mapping[str, Any] = nested if isinstance(nested, Mapping) else value
    else:
        raw = {}

    artifact_sha256 = ""
    for key in ("artifact_sha256", "output_sha256", "content_sha256", "sha256"):
        if raw.get(key) not in (None, ""):
            artifact_sha256 = _sha256(raw.get(key))
            break

    artifact_id = _text(
        raw.get("artifact_id") or raw.get("artifactId") or raw.get("id"),
        limit=300,
    )
    if _is_external_ref(artifact_id):
        raise AgentArtifactError("artifact_id must be a stable project identifier")
    if not artifact_id and artifact_sha256:
        artifact_id = f"{AGENT_ARTIFACT_ID_PREFIX}{artifact_sha256[:32]}"
    if not artifact_id:
        raise AgentArtifactError("artifact reference requires artifact_id or artifact_sha256")

    status = _text(raw.get("status") or default_status, limit=40).lower() or "materialized"
    if status not in _STATUSES:
        raise AgentArtifactError(f"unsupported artifact status: {status}")

    uri = _internal_uri(_read(raw, "artifact_uri", "artifactUri"))
    if not uri and artifact_sha256:
        uri = f"{AGENT_ARTIFACT_URI_PREFIX}{artifact_sha256}"

    result: dict[str, Any] = {
        "schema": AGENT_ARTIFACT_SCHEMA,
        "artifact_id": artifact_id,
        "kind": _text(raw.get("kind") or raw.get("type") or "artifact", limit=80),
        "status": status,
        "source_refs": _source_refs(_read(raw, "source_refs", "sourceRefs", [])),
    }
    if uri:
        result["artifact_uri"] = uri
    if artifact_sha256:
        result["artifact_sha256"] = artifact_sha256

    fields = (
        ("producer_agent_id", producer_agent_id or raw.get("producer_agent_id") or raw.get("producerAgentId")),
        ("consumer_agent_id", consumer_agent_id or raw.get("consumer_agent_id") or raw.get("consumerAgentId")),
        ("task_id", raw.get("task_id") or raw.get("taskId")),
        ("run_id", raw.get("run_id") or raw.get("runId")),
        ("step_id", raw.get("step_id") or raw.get("stepId")),
    )
    for key, candidate in fields:
        clean = _text(candidate, limit=240)
        if clean:
            result[key] = clean

    verification = _verification(raw.get("verification"))
    if verification:
        result["verification"] = verification
    created_at = _text(raw.get("created_at") or raw.get("createdAt"), limit=80)
    if created_at:
        result["created_at"] = created_at
    return result


def project_agent_artifact_ref(
    value: object,
    *,
    include_uri: bool = False,
) -> dict[str, Any]:
    """Return a bounded, transport-safe artifact projection.

    ``artifact_uri`` is omitted by default so event streams and Agent context
    never expose provider URLs or signed credentials.  Internal artifact URIs
    can be requested by persistence/UI code with ``include_uri=True``.
    """

    try:
        normalized = normalize_agent_artifact_ref(value)
    except (AgentArtifactError, TypeError, ValueError):
        return {}
    allowed = {
        "schema",
        "artifact_id",
        "kind",
        "status",
        "source_refs",
        "artifact_sha256",
        "producer_agent_id",
        "consumer_agent_id",
        "task_id",
        "run_id",
        "step_id",
        "verification",
        "created_at",
    }
    if include_uri:
        allowed.add("artifact_uri")
    return {key: normalized[key] for key in normalized if key in allowed}


def agent_artifact_from_store(
    value: object,
    *,
    kind: str = "artifact",
    status: str = "materialized",
    producer_agent_id: str = "",
    consumer_agent_id: str = "",
    source_refs: object = (),
) -> dict[str, Any]:
    """Build a contract ref from ``verification.artifact_store.ArtifactRef``."""

    sha256 = _text(getattr(value, "sha256", ""), limit=64).lower()
    size_bytes = getattr(value, "size_bytes", None)
    result = normalize_agent_artifact_ref(
        {"artifact_sha256": sha256, "kind": kind, "status": status, "source_refs": source_refs},
        producer_agent_id=producer_agent_id,
        consumer_agent_id=consumer_agent_id,
    )
    if isinstance(size_bytes, int) and not isinstance(size_bytes, bool) and size_bytes >= 0:
        result["size_bytes"] = size_bytes
    result.setdefault("created_at", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    return result


def coerce_event_agent_artifact(
    payload: Mapping[str, Any] | None,
    *,
    default_status: str = "materialized",
) -> dict[str, Any]:
    """Derive a contract ref from an event without changing legacy fields."""

    refs = coerce_event_agent_artifacts(payload, default_status=default_status)
    return refs[0] if refs else {}


def coerce_event_agent_artifacts(
    payload: Mapping[str, Any] | None,
    *,
    default_status: str = "materialized",
) -> list[dict[str, Any]]:
    """Collect bounded refs from top-level and nested legacy event fields."""

    if not isinstance(payload, Mapping):
        return []
    candidates: list[Mapping[str, Any]] = []
    seen_objects: set[int] = set()
    fields = (
        "artifact_id",
        "artifact_sha256",
        "output_sha256",
        "content_sha256",
        "sha256",
        "artifact_uri",
        "kind",
        "status",
        "source_refs",
        "producer_agent_id",
        "consumer_agent_id",
        "task_id",
        "run_id",
        "step_id",
    )

    def visit(value: object) -> None:
        if len(candidates) >= 32:
            return
        if isinstance(value, Mapping):
            identity = id(value)
            if identity in seen_objects:
                return
            seen_objects.add(identity)
            explicit = value.get("agent_artifact") or value.get("artifact_ref")
            if isinstance(explicit, Mapping):
                candidates.append(explicit)
            elif any(value.get(key) not in (None, "") for key in fields):
                candidates.append({key: value[key] for key in fields if key in value})
            for key, child in list(value.items())[:32]:
                if key in {"prompt", "inputs", "payload", "logs", "error", "artifact_uri", "url"}:
                    continue
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value[:32]:
                visit(child)

    visit(payload)
    refs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for candidate in candidates:
        projected = project_agent_artifact_ref(
            {**dict(candidate), "status": candidate.get("status") or default_status},
            include_uri=True,
        )
        artifact_id = str(projected.get("artifact_id") or "")
        if projected and artifact_id and artifact_id not in seen_ids:
            seen_ids.add(artifact_id)
            refs.append(projected)
        if len(refs) >= 32:
            break
    return refs


__all__ = [
    "AGENT_ARTIFACT_ID_PREFIX",
    "AGENT_ARTIFACT_SCHEMA",
    "AGENT_ARTIFACT_URI_PREFIX",
    "AgentArtifactError",
    "agent_artifact_from_store",
    "coerce_event_agent_artifact",
    "coerce_event_agent_artifacts",
    "normalize_agent_artifact_ref",
    "project_agent_artifact_ref",
]
