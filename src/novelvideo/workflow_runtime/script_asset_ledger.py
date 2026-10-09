"""Server-owned, read-only asset ledger derived from a script contract."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

LEDGER_SCHEMA = "workflow_script_asset_ledger.v1"
ASSET_DEFINITION_SCHEMA = "village.script-asset-definition.v1"
ROLE_ORDER = ("character", "scene", "prop")
# Mirrors the backend reference-image contract; guarded by
# test_workflow_script_asset_ledger.py against drift.
SCRIPT_REFERENCE_IMAGE_CAP = 9
_NO_VALUE = {"", "无", "没有", "none", "n/a", "-", "—"}
_TAG_SPLIT_RE = re.compile(r"[、,，;；/|]+")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _is_no_value(value: Any) -> bool:
    return _text(value).casefold() in _NO_VALUE


def _split_tags(value: Any) -> list[str]:
    return [
        token.strip()
        for token in _TAG_SPLIT_RE.split(_text(value))
        if token.strip() and not _is_no_value(token)
    ]


def _normalise_tokens(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        candidates = [str(item) for item in value]
    else:
        candidates = _split_tags(value)
    seen: set[str] = set()
    tokens: list[str] = []
    for candidate in candidates:
        token = candidate.strip()
        if not token or token in seen or _is_no_value(token):
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _cell_text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if isinstance(value, (list, tuple)):
        return _text(value[0]) if value else ""
    return _text(value)


def _parallel_cell_text_at(
    row: Mapping[str, Any],
    key: str,
    index: int,
) -> str:
    raw = row.get(key)
    if isinstance(raw, (list, tuple)):
        return _text(raw[index]) if index < len(raw) else ""
    text = _cell_text(row, key)
    if not text:
        return ""
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, list):
            return _text(parsed[index]) if index < len(parsed) else ""
    tags = _split_tags(text)
    return tags[index] if index < len(tags) else ""


def _shot_id(row: Mapping[str, Any], index: int) -> str:
    return _text(row.get("shot_id")) or f"shot-{index}"


def _shot_no(row: Mapping[str, Any], index: int) -> str:
    return (
        _text(row.get("display_shot_no"))
        or _text(row.get("shot_no"))
        or str(index)
    )


def _asset_id(role: str, name: str, explicit: str = "") -> str:
    return _text(explicit) or f"{role}:{name.strip().casefold()}"


def _default_identity_locks(role: str, explicit: Sequence[str]) -> list[str]:
    locks = _normalise_tokens(explicit)
    if locks:
        return locks
    if role == "character":
        return ["face", "costume", "age", "temperament"]
    return ["content_hash"]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _asset_content_hash(
    *,
    asset_id: str,
    role: str,
    name: str,
    description: str,
    source_image_url: str,
    identity_locks: Sequence[str],
    dependencies: Sequence[str],
) -> str:
    payload = {
        "schema": ASSET_DEFINITION_SCHEMA,
        "asset_id": asset_id,
        "role": role,
        "name": name.strip(),
        "description": description.strip(),
        "source_image_url": source_image_url,
        "identity_locks": _normalise_tokens(identity_locks),
        "dependencies": _normalise_tokens(dependencies),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _new_asset(
    *,
    role: str,
    asset_id: str,
    name: str,
    description: str,
    explicit_id: str,
    identity_locks: Sequence[str],
    dependencies: Sequence[str],
    reference_url: str,
    expected_content_hash: str,
    revision: int,
    shot_id: str,
    shot_no: str,
) -> dict[str, Any]:
    locks = _default_identity_locks(role, identity_locks)
    dependencies = _normalise_tokens(dependencies)
    source_image_url = reference_url if role == "character" else ""
    content_hash = _asset_content_hash(
        asset_id=asset_id,
        role=role,
        name=name,
        description=description,
        source_image_url=source_image_url,
        identity_locks=locks,
        dependencies=dependencies,
    )
    reasons: list[str] = []
    if role == "character":
        reasons.append("character_default")
    if explicit_id:
        reasons.append("explicit_asset_id")
    if _normalise_tokens(identity_locks):
        reasons.append("identity_locks")
    if dependencies:
        reasons.append("dependencies")
    readiness = "missing"
    missing_reason = "reference_missing"
    if reference_url:
        readiness = "ready"
        missing_reason = ""
    if expected_content_hash and expected_content_hash != content_hash:
        readiness = "stale"
        missing_reason = "content_hash_changed"
    return {
        "asset_id": asset_id,
        "role": role,
        "name": name,
        "description": description,
        "revision": max(1, int(revision or 1)),
        "content_hash": content_hash,
        "identity_locks": locks,
        "dependencies": dependencies,
        "shot_ids": [shot_id],
        "shot_numbers": [shot_no],
        "required": bool(reasons),
        "required_reasons": reasons,
        "readiness": readiness,
        "missing_reason": missing_reason,
        "reference_url": reference_url,
        "reference_source": (
            ("row" if role == "character" else "explicit")
            if reference_url
            else "none"
        ),
        "source_image_url": source_image_url,
    }


def _merge_asset(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key in ("shot_ids", "shot_numbers"):
        for value in incoming.get(key, []):
            if value and value not in target[key]:
                target[key].append(value)
    if not _text(target.get("description")) and _text(incoming.get("description")):
        target["description"] = incoming["description"]
    if not _text(target.get("reference_url")) and _text(incoming.get("reference_url")):
        target["reference_url"] = incoming["reference_url"]
        target["reference_source"] = incoming["reference_source"]
    target["revision"] = max(
        int(target.get("revision") or 1),
        int(incoming.get("revision") or 1),
    )


def _entry_from_row(
    row: Mapping[str, Any],
    *,
    role: str,
    name: str,
    description: str,
    explicit_id: str,
    identity_locks: Sequence[str],
    dependencies: Sequence[str],
    reference_url: str,
    expected_content_hash: str,
    revision: int,
    index: int,
) -> dict[str, Any]:
    fallback_name = name or (
        f"角色{index}" if role == "character" else ""
    )
    asset_id = _asset_id(role, fallback_name, explicit_id)
    return _new_asset(
        role=role,
        asset_id=asset_id,
        name=fallback_name,
        description=description,
        explicit_id=explicit_id,
        identity_locks=identity_locks,
        dependencies=dependencies,
        reference_url=reference_url,
        expected_content_hash=expected_content_hash,
        revision=revision,
        shot_id=_shot_id(row, index),
        shot_no=_shot_no(row, index),
    )


def build_script_asset_ledger(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Derive stable asset identities and readiness from script rows."""

    assets: dict[str, dict[str, Any]] = {}

    def add(entry: dict[str, Any]) -> None:
        asset_id = entry["asset_id"]
        existing = assets.get(asset_id)
        if existing is None:
            assets[asset_id] = entry
            return
        _merge_asset(existing, entry)

    for index, row in enumerate(rows, 1):
        for slot in (1, 2):
            raw_name = _cell_text(row, f"character_{slot}")
            raw_description = _cell_text(row, f"character_description_{slot}")
            image_url = _cell_text(row, f"character_image_{slot}")
            name = "" if _is_no_value(raw_name) else raw_name
            description = (
                "" if _is_no_value(raw_description) else raw_description
            )
            if not name and not description and not image_url:
                continue
            explicit_id = (
                _cell_text(row, f"character_asset_id_{slot}")
                or _cell_text(row, f"character_id_{slot}")
            )
            add(
                _entry_from_row(
                    row,
                    role="character",
                    name=name,
                    description=description,
                    explicit_id=explicit_id,
                    identity_locks=_split_tags(
                        _cell_text(row, f"character_identity_locks_{slot}")
                    ),
                    dependencies=_split_tags(
                        _cell_text(row, f"character_dependencies_{slot}")
                    ),
                    reference_url=image_url,
                    expected_content_hash=_cell_text(
                        row, f"character_content_hash_{slot}"
                    ),
                    revision=int(row.get(f"character_revision_{slot}") or 1),
                    index=index,
                )
            )

        for role, tag_key, ids_key, refs_key, hashes_key in (
            (
                "scene",
                "scene_tags",
                "scene_asset_ids",
                "scene_reference_urls",
                "scene_asset_content_hashes",
            ),
            (
                "prop",
                "prop_tags",
                "prop_asset_ids",
                "prop_reference_urls",
                "prop_asset_content_hashes",
            ),
        ):
            for tag_index, name in enumerate(_split_tags(row.get(tag_key))):
                explicit_id = _parallel_cell_text_at(row, ids_key, tag_index)
                reference_url = _parallel_cell_text_at(
                    row, refs_key, tag_index
                )
                expected_content_hash = _parallel_cell_text_at(
                    row, hashes_key, tag_index
                )
                add(
                    _entry_from_row(
                        row,
                        role=role,
                        name=name,
                        description="",
                        explicit_id=explicit_id,
                        identity_locks=_split_tags(
                            _cell_text(row, f"{role}_identity_locks")
                        ),
                        dependencies=_split_tags(
                            _cell_text(row, f"{role}_dependencies")
                        ),
                        reference_url=reference_url,
                        expected_content_hash=expected_content_hash,
                        revision=max(
                            1,
                            int(row.get(f"{role}_revision") or 1),
                        ),
                        index=index,
                    )
                )

    ordered: list[dict[str, Any]] = []
    for role in ROLE_ORDER:
        ordered.extend(
            asset for asset in assets.values() if asset.get("role") == role
        )
    for asset in ordered:
        asset["required"] = bool(asset.get("required_reasons"))
        if asset.get("reference_source") == "none":
            asset["reference_source"] = "none"
    signature = hashlib.sha256(
        _canonical_json(ordered).encode("utf-8")
    ).hexdigest()
    return {
        "schema": LEDGER_SCHEMA,
        "assets": ordered,
        "counts": {
            role: sum(1 for asset in ordered if asset.get("role") == role)
            for role in ROLE_ORDER
        },
        "signature": signature,
    }


def required_asset_blockers(
    ledger: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    assets = ledger.get("assets") if isinstance(ledger, Mapping) else None
    if not isinstance(assets, list):
        return []
    blockers: list[dict[str, Any]] = []
    for raw_asset in assets:
        if not isinstance(raw_asset, Mapping):
            continue
        if raw_asset.get("required") is not True:
            continue
        if _text(raw_asset.get("readiness")) == "ready":
            continue
        blockers.append(
            {
                "asset_id": _text(raw_asset.get("asset_id")),
                "role": _text(raw_asset.get("role")),
                "name": _text(raw_asset.get("name")),
                "readiness": _text(raw_asset.get("readiness")),
                "missing_reason": _text(raw_asset.get("missing_reason")),
                "required_reasons": list(
                    raw_asset.get("required_reasons") or []
                ),
            }
        )
    return blockers


def missing_reference_assets(
    ledger: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return assets without a usable reference in stable production order."""

    assets = ledger.get("assets") if isinstance(ledger, Mapping) else None
    if not isinstance(assets, list):
        return []
    missing = [
        dict(asset)
        for asset in assets
        if isinstance(asset, Mapping)
        and (
            _text(asset.get("readiness")) != "ready"
            or not _text(asset.get("reference_url"))
        )
    ]
    role_index = {role: index for index, role in enumerate(ROLE_ORDER)}
    return sorted(
        missing,
        key=lambda asset: (
            0 if asset.get("required") is True else 1,
            role_index.get(_text(asset.get("role")), len(ROLE_ORDER)),
            _text(asset.get("asset_id")),
        ),
    )


def materialize_asset_references(
    ledger: Mapping[str, Any] | None,
    reference_images: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply verified generated references and recompute the ledger signature."""

    effective = dict(ledger) if isinstance(ledger, Mapping) else {}
    raw_assets = effective.get("assets")
    assets = (
        [dict(asset) for asset in raw_assets if isinstance(asset, Mapping)]
        if isinstance(raw_assets, list)
        else []
    )
    for asset in assets:
        asset_id = _text(asset.get("asset_id"))
        image = reference_images.get(asset_id)
        if not isinstance(image, Mapping):
            continue
        url = _text(image.get("reference_url") or image.get("url"))
        if not url:
            continue
        role = _text(asset.get("role"))
        source_image_url = (
            url
            if role == "character"
            else _text(asset.get("source_image_url"))
        )
        asset.update(
            {
                "reference_url": url,
                "reference_source": _text(image.get("reference_source"))
                or "generated",
                "readiness": "ready",
                "missing_reason": "",
                "source_image_url": source_image_url,
            }
        )
        if role == "character":
            asset["content_hash"] = _asset_content_hash(
                asset_id=asset_id,
                role=role,
                name=_text(asset.get("name")),
                description=_text(asset.get("description")),
                source_image_url=source_image_url,
                identity_locks=asset.get("identity_locks") or [],
                dependencies=asset.get("dependencies") or [],
            )
    effective["assets"] = assets
    effective["counts"] = {
        role: sum(1 for asset in assets if asset.get("role") == role)
        for role in ROLE_ORDER
    }
    effective["signature"] = hashlib.sha256(
        _canonical_json(assets).encode("utf-8")
    ).hexdigest()
    return effective


def asset_references_for_shot(
    ledger: Mapping[str, Any] | None,
    row: Mapping[str, Any],
    index: int,
) -> list[dict[str, Any]]:
    """Return ready reference assets for one script row in stable role order."""

    assets = ledger.get("assets") if isinstance(ledger, Mapping) else None
    if not isinstance(assets, list):
        return []
    shot_id = _shot_id(row, index)
    shot_no = _shot_no(row, index)
    selected: list[dict[str, Any]] = []
    for raw_asset in assets:
        if not isinstance(raw_asset, Mapping):
            continue
        if _text(raw_asset.get("readiness")) != "ready":
            continue
        if not _text(raw_asset.get("reference_url")):
            continue
        shot_ids = raw_asset.get("shot_ids")
        shot_numbers = raw_asset.get("shot_numbers")
        if _text(row.get("shot_id")):
            belongs = isinstance(shot_ids, list) and shot_id in shot_ids
        else:
            belongs = (
                isinstance(shot_numbers, list) and shot_no in shot_numbers
            )
        if not belongs:
            continue
        selected.append(dict(raw_asset))
    return selected


def asset_reference_signature(references: Sequence[Mapping[str, Any]]) -> str:
    payload = [
        {
            "asset_id": _text(reference.get("asset_id")),
            "content_hash": _text(reference.get("content_hash")),
            "reference_url": _text(reference.get("reference_url")),
        }
        for reference in references
    ]
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


__all__ = [
    "LEDGER_SCHEMA",
    "ROLE_ORDER",
    "asset_reference_signature",
    "asset_references_for_shot",
    "build_script_asset_ledger",
    "materialize_asset_references",
    "missing_reference_assets",
    "required_asset_blockers",
]
