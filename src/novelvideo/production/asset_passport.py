"""Stable, JSON-friendly identity contract for production assets.

An AssetPassport is the identity boundary between a canvas node and the
artifact it represents.  It keeps enough provenance to detect drift while the
Agent-facing summary intentionally omits local paths, URLs, and credentials.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator


ASSET_PASSPORT_SCHEMA = "village.asset-passport.v1"
AssetMediaKind = Literal["image", "video", "audio", "document", "unknown"]
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


class AssetPassport(BaseModel):
    """Canonical asset identity and provenance, independent of canvas layout."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    schema_version: Literal["village.asset-passport.v1"] = Field(
        default=ASSET_PASSPORT_SCHEMA,
        alias="schema",
    )
    passport_id: str = Field(min_length=1, max_length=120)
    asset_id: str = Field(min_length=1, max_length=300)
    display_name: str = Field(default="", max_length=200)
    media_kind: AssetMediaKind = "unknown"
    source_kind: str = Field(default="native", min_length=1, max_length=80)
    source_ref: str = Field(default="", max_length=300)
    sha256: str = Field(default="", max_length=64)
    mime_type: str = Field(default="", max_length=120)
    width: int | None = Field(default=None, ge=1, le=100_000)
    height: int | None = Field(default=None, ge=1, le=100_000)
    duration_seconds: float | None = Field(default=None, ge=0, le=86_400)
    roles: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    dependencies: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    identity_locks: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    revision: int = Field(default=0, ge=0, le=2_147_483_647)
    artifact_url: str = Field(default="", max_length=4000)
    artifact_path: str = Field(default="", max_length=4000)

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized and not _SHA256.fullmatch(normalized):
            raise ValueError("sha256 must be a 64-character hexadecimal digest")
        return normalized

    @field_validator("roles", "dependencies", "identity_locks")
    @classmethod
    def normalize_tokens(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            str(value).strip()[:240]
            for value in values
            if str(value).strip()
        ))


def _clean(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _first(data: Mapping[str, Any], *keys: str, limit: int = 240) -> str:
    for key in keys:
        value = _clean(data.get(key), limit)
        if value:
            return value
    return ""


def _number(data: Mapping[str, Any], *keys: str) -> int | float | None:
    for key in keys:
        value = data.get(key)
        if value in (None, "") or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        return int(number) if number.is_integer() else number
    return None


def _tokens(data: Mapping[str, Any], *keys: str, limit: int = 32) -> tuple[str, ...]:
    values: list[str] = []
    for key in keys:
        raw = data.get(key)
        if isinstance(raw, str):
            raw_values = [raw]
        elif isinstance(raw, (list, tuple, set)):
            raw_values = list(raw)
        else:
            continue
        for value in raw_values:
            token = _clean(value, 240)
            if token and token not in values:
                values.append(token)
            if len(values) >= limit:
                return tuple(values)
    return tuple(values)


def _canonical_identity(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _canonical_fields(
    *,
    asset_id: str,
    display_name: str,
    media_kind: AssetMediaKind,
    source_kind: str,
    source_ref: str,
    sha256: str,
    mime_type: str,
    width: int | None,
    height: int | None,
    duration_seconds: float | None,
    roles: tuple[str, ...],
    dependencies: tuple[str, ...],
    identity_locks: tuple[str, ...],
    revision: int,
) -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "display_name": display_name,
        "media_kind": media_kind,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "sha256": sha256,
        "mime_type": mime_type,
        "width": width,
        "height": height,
        "duration_seconds": duration_seconds,
        "roles": roles,
        "dependencies": dependencies,
        "identity_locks": identity_locks,
        "revision": revision,
    }


def build_asset_passport(
    data: Mapping[str, Any],
    *,
    asset_id: str = "",
    display_name: str = "",
    media_kind: AssetMediaKind = "unknown",
    role: str = "",
) -> AssetPassport | None:
    """Build a passport from node/asset metadata without reading the network."""

    raw_asset_id = asset_id or _first(
        data,
        "assetId",
        "asset_id",
        "sourceAssetId",
        "source_asset_id",
        "resultAssetId",
        "result_asset_id",
        "identityId",
        "identity_id",
        "sceneId",
        "scene_id",
        "propId",
        "prop_id",
        "mediaId",
        "media_id",
        limit=300,
    )
    if not raw_asset_id:
        return None
    nested = data.get("metadata")
    metadata = nested if isinstance(nested, Mapping) else {}
    merged: dict[str, Any] = {**metadata, **data}
    resolved_name = display_name or _first(
        merged, "displayName", "display_name", "label", "title", "name", limit=200
    )
    resolved_kind = media_kind
    if resolved_kind == "unknown":
        kind_value = _first(merged, "mediaKind", "media_kind", "assetKind", "asset_kind", limit=40).lower()
        if kind_value in {"image", "video", "audio", "document"}:
            resolved_kind = kind_value  # type: ignore[assignment]
    resolved_role = role or _first(
        merged, "reference_role", "referenceRole", "output_role", "role", limit=80
    )
    roles = _tokens(merged, "roles", "asset_roles", "assetRoles")
    if resolved_role and resolved_role not in roles:
        roles = (*roles, resolved_role)
    dependencies = _tokens(
        merged,
        "dependencies",
        "dependency_ids",
        "dependencyIds",
        "parent_asset_ids",
        "parentAssetIds",
    )
    identity_locks = _tokens(
        merged,
        "identity_locks",
        "identityLocks",
        "locked_fields",
        "lockedFields",
    )
    sha256 = _first(
        merged,
        "sha256",
        "asset_sha256",
        "assetSha256",
        "media_sha256",
        "mediaSha256",
        "output_sha256",
        "outputSha256",
        "image_sha256",
        "imageSha256",
        "video_sha256",
        "videoSha256",
        "audio_sha256",
        "audioSha256",
        limit=64,
    ).lower()
    if sha256 and not _SHA256.fullmatch(sha256):
        sha256 = ""
    width_raw = _number(merged, "width", "media_width", "mediaWidth", "pixel_width", "pixelWidth")
    height_raw = _number(merged, "height", "media_height", "mediaHeight", "pixel_height", "pixelHeight")
    duration_raw = _number(merged, "duration_seconds", "durationSeconds", "duration")
    width = int(width_raw) if isinstance(width_raw, (int, float)) and width_raw >= 1 else None
    height = int(height_raw) if isinstance(height_raw, (int, float)) and height_raw >= 1 else None
    duration = float(duration_raw) if isinstance(duration_raw, (int, float)) and duration_raw >= 0 else None
    revision_raw = _number(merged, "asset_revision", "assetRevision", "revision", "version")
    revision = int(revision_raw) if isinstance(revision_raw, (int, float)) and revision_raw >= 0 else 0
    source_kind = _first(merged, "source_kind", "sourceKind", "origin_kind", "originKind", limit=80) or "native"
    source_ref = _first(merged, "source_ref", "sourceRef", "origin_id", "originId", limit=300)
    mime_type = _first(merged, "mime_type", "mimeType", "content_type", "contentType", limit=120)
    artifact_url = _first(
        merged,
        "artifact_url",
        "artifactUrl",
        "mediaUrl",
        "media_url",
        "fileUrl",
        "file_url",
        "imageUrl",
        "image_url",
        "videoUrl",
        "video_url",
        "audioUrl",
        "audio_url",
        limit=4000,
    )
    artifact_path = _first(
        merged,
        "artifact_path",
        "artifactPath",
        "filePath",
        "file_path",
        "output_path",
        "outputPath",
        limit=4000,
    )
    identity = _canonical_fields(
        asset_id=raw_asset_id,
        display_name=resolved_name,
        media_kind=resolved_kind,
        source_kind=source_kind,
        source_ref=source_ref,
        sha256=sha256,
        mime_type=mime_type,
        width=width,
        height=height,
        duration_seconds=duration,
        roles=roles,
        dependencies=dependencies,
        identity_locks=identity_locks,
        revision=revision,
    )
    return AssetPassport(
        passport_id=f"asset-passport:{_canonical_identity(identity)}",
        asset_id=raw_asset_id,
        display_name=resolved_name,
        media_kind=resolved_kind,
        source_kind=source_kind,
        source_ref=source_ref,
        sha256=sha256,
        mime_type=mime_type,
        width=width,
        height=height,
        duration_seconds=duration,
        roles=roles,
        dependencies=dependencies,
        identity_locks=identity_locks,
        revision=revision,
        artifact_url=artifact_url,
        artifact_path=artifact_path,
    )


def validate_asset_passport(value: object) -> list[str]:
    """Return deterministic validation messages for API/WorkflowRun gates."""

    try:
        passport = value if isinstance(value, AssetPassport) else AssetPassport.model_validate(value)
    except Exception as exc:  # pydantic aggregates field errors for callers
        return [str(exc)]
    canonical_fields = _canonical_fields(
        asset_id=passport.asset_id,
        display_name=passport.display_name,
        media_kind=passport.media_kind,
        source_kind=passport.source_kind,
        source_ref=passport.source_ref,
        sha256=passport.sha256,
        mime_type=passport.mime_type,
        width=passport.width,
        height=passport.height,
        duration_seconds=passport.duration_seconds,
        roles=passport.roles,
        dependencies=passport.dependencies,
        identity_locks=passport.identity_locks,
        revision=passport.revision,
    )
    expected = f"asset-passport:{_canonical_identity(canonical_fields)}"
    return [] if passport.passport_id == expected else ["passport_id does not match canonical asset identity"]


def validate_asset_execution_identity(
    value: object,
    *,
    require_lock: bool = True,
) -> list[dict[str, str]]:
    """Validate the minimum identity evidence required before media execution.

    A passport can be useful for display while still being too weak to bind a
    generated result to a stable asset.  Execution therefore requires a
    canonical passport plus a content digest, positive revision, and at least
    one explicitly locked identity field.  The helper is intentionally pure so
    Canvas, WorkflowRun, and provider adapters can share the same gate.
    """

    try:
        passport = value if isinstance(value, AssetPassport) else AssetPassport.model_validate(value)
    except Exception as exc:  # pydantic keeps the detailed field errors
        return [{"code": "asset_passport_invalid", "field": "passport", "message": str(exc)}]

    issues: list[dict[str, str]] = []
    for message in validate_asset_passport(passport):
        issues.append(
            {
                "code": "asset_passport_identity_invalid",
                "field": "passport_id",
                "message": message,
            }
        )
    if not require_lock:
        return issues
    if not passport.sha256:
        issues.append(
            {
                "code": "asset_passport_digest_missing",
                "field": "sha256",
                "message": "执行前资产必须有稳定 sha256",
            }
        )
    if passport.revision < 1:
        issues.append(
            {
                "code": "asset_passport_revision_missing",
                "field": "revision",
                "message": "执行前资产必须有正版本号",
            }
        )
    if not passport.identity_locks:
        issues.append(
            {
                "code": "asset_identity_lock_missing",
                "field": "identity_locks",
                "message": "执行前资产必须至少锁定一个身份字段",
            }
        )
    return issues


def asset_execution_identity_ready(value: object, *, require_lock: bool = True) -> bool:
    """Return whether an asset can enter a media execution lane."""

    return not validate_asset_execution_identity(value, require_lock=require_lock)


def validate_reference_binding_identities(
    *,
    snapshot: Mapping[str, Any] | None,
    bindings: object,
) -> dict[str, Any]:
    """Resolve semantic reference IDs to locked passports in one canvas snapshot."""

    raw_bindings = bindings if isinstance(bindings, Mapping) else {}
    nodes = [
        node
        for node in ((snapshot or {}).get("nodes") or [])
        if isinstance(node, Mapping)
    ]

    def identities(node: Mapping[str, Any]) -> set[str]:
        data = node.get("data") if isinstance(node.get("data"), Mapping) else {}
        values: set[str] = set()
        for source in (node, data):
            for key in (
                "id",
                "assetId",
                "asset_id",
                "identityId",
                "identity_id",
                "sceneId",
                "scene_id",
                "propId",
                "prop_id",
            ):
                value = _clean(source.get(key), 300).casefold()
                if value:
                    values.add(value)
        return values

    issues: list[dict[str, str]] = []
    passports: list[dict[str, Any]] = []
    for raw_role, raw_values in raw_bindings.items():
        role = _clean(raw_role, 80).casefold()
        values = raw_values if isinstance(raw_values, (list, tuple, set)) else [raw_values]
        for raw_asset_id in values:
            asset_id = _clean(raw_asset_id, 300)
            if not asset_id:
                continue
            matches = [node for node in nodes if asset_id.casefold() in identities(node)]
            if len(matches) != 1:
                issues.append(
                    {
                        "code": (
                            "asset_identity_binding_missing"
                            if not matches
                            else "asset_identity_binding_ambiguous"
                        ),
                        "field": f"reference_bindings.{role or 'reference'}",
                        "message": f"参考资产 {asset_id!r} 必须唯一绑定到画布节点",
                        "asset_id": asset_id,
                        "role": role,
                    }
                )
                continue
            node = matches[0]
            data = node.get("data") if isinstance(node.get("data"), Mapping) else {}
            node_type = _clean(node.get("type"), 80).casefold()
            media_kind: AssetMediaKind = (
                "video"
                if "video" in node_type
                else "audio"
                if "audio" in node_type
                else "image"
            )
            passport = build_asset_passport(
                data,
                asset_id=asset_id,
                media_kind=media_kind,
                role=role,
            )
            if passport is None:
                issues.append(
                    {
                        "code": "asset_passport_missing",
                        "field": f"reference_bindings.{role or 'reference'}",
                        "message": f"参考资产 {asset_id!r} 缺少 AssetPassport",
                        "asset_id": asset_id,
                        "role": role,
                    }
                )
                continue
            passport_issues = validate_asset_execution_identity(passport)
            for issue in passport_issues:
                issues.append(
                    {
                        **issue,
                        "asset_id": asset_id,
                        "role": role,
                        "node_id": _clean(node.get("id"), 300),
                    }
                )
            passports.append(passport_summary(passport))
    return {
        "schema": "asset_identity_gate.v1",
        "passed": not issues,
        "binding_count": len(passports),
        "passports": passports,
        "issues": issues,
    }


def passport_summary(value: AssetPassport | Mapping[str, Any]) -> dict[str, Any]:
    """Return the Agent-safe identity summary; paths and URLs never cross this boundary."""

    passport = value if isinstance(value, AssetPassport) else AssetPassport.model_validate(value)
    summary = {
        "schema": passport.schema_version,
        "passport_id": passport.passport_id,
        "asset_id": passport.asset_id,
        "display_name": passport.display_name or None,
        "media_kind": passport.media_kind,
        "source_kind": passport.source_kind,
        "source_ref": passport.source_ref or None,
        "sha256": passport.sha256 or None,
        "mime_type": passport.mime_type or None,
        "width": passport.width,
        "height": passport.height,
        "duration_seconds": passport.duration_seconds,
        "roles": list(passport.roles),
        "dependencies": list(passport.dependencies),
        "identity_locks": list(passport.identity_locks),
        "revision": passport.revision,
    }
    return {key: value for key, value in summary.items() if value not in (None, "", [], {})}


__all__ = [
    "ASSET_PASSPORT_SCHEMA",
    "AssetPassport",
    "build_asset_passport",
    "asset_execution_identity_ready",
    "passport_summary",
    "validate_asset_execution_identity",
    "validate_asset_passport",
    "validate_reference_binding_identities",
]
