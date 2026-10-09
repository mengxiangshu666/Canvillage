"""Versioned project identity assembled from explicit director decisions.

ProjectDNA is deliberately narrower than the global memory store.  It holds
facts that define this project's visual identity and user vetoes; it does not
silently turn a one-off scene detail into a cross-project rule.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any


PROJECT_DNA_SCHEMA = "project_dna.v1"
PROJECT_DNA_REVISION_PREFIX = "project-dna.v1:"


def _text(value: object, *, limit: int = 2_000) -> str:
    return str(value or "").strip()[:limit]


def _list(value: object, *, limit: int = 50, item_limit: int = 500) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item, limit=item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _entity_ids(value: object, *, limit: int = 50) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value[:limit]:
        identifier = (
            _text(item.get("id") or item.get("asset_id"), limit=160)
            if isinstance(item, Mapping)
            else _text(item, limit=160)
        )
        if identifier and identifier not in result:
            result.append(identifier)
    return result


def _canonical_payload(value: Mapping[str, Any]) -> str:
    return json.dumps(
        {key: value[key] for key in sorted(value) if key != "dna_revision"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_dna_revision(value: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_payload(value).encode("utf-8")).hexdigest()[:20]
    return f"{PROJECT_DNA_REVISION_PREFIX}{digest}"


def build_project_dna(
    *,
    project_id: object = "",
    intent_contract: object = None,
    explicit: object = None,
    feedback: object = None,
) -> dict[str, Any]:
    """Compile project-scoped identity without promoting global memories."""

    intent = _mapping(intent_contract)
    raw = _mapping(explicit)
    style = _mapping(intent.get("style"))
    raw_style = _mapping(raw.get("style_anchor") or raw.get("style"))
    style_anchor = {
        "visual_style": _text(raw_style.get("visual_style") or style.get("visual_style") or style.get("style"), limit=600),
        "color_palette": _text(raw_style.get("color_palette") or style.get("color_palette"), limit=600),
        "lighting": _text(raw_style.get("lighting") or style.get("lighting"), limit=600),
        "texture": _text(raw_style.get("texture") or style.get("texture"), limit=600),
        "composition": _text(raw_style.get("composition") or style.get("composition"), limit=600),
    }
    explicit_feedback = _mapping(feedback)
    dna: dict[str, Any] = {
        "schema": PROJECT_DNA_SCHEMA,
        "project_id": _text(project_id, limit=200),
        "character_ids": _entity_ids(raw.get("character_ids") or intent.get("characters")),
        "location_ids": _entity_ids(raw.get("location_ids") or intent.get("locations")),
        "prop_ids": _entity_ids(raw.get("prop_ids") or intent.get("props")),
        "style_anchor": style_anchor,
        "positive_preferences": _list(
            raw.get("positive_preferences") or explicit_feedback.get("positive"),
            limit=30,
        ),
        "vetoes": _list(
            raw.get("vetoes") or raw.get("negative_preferences") or explicit_feedback.get("negative"),
            limit=30,
        ),
        "locked_rules": _list(raw.get("locked_rules"), limit=30),
        "source_refs": _list(raw.get("source_refs"), limit=30, item_limit=240),
        "provenance": {
            "source": "director_intent_contract",
            "intent_revision": _text(intent.get("contract_revision"), limit=100),
        },
    }
    dna["dna_revision"] = compute_dna_revision(dna)
    return dna


def validate_project_dna(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("project_dna must be an object")
    dna = deepcopy(dict(value))
    if dna.get("schema") != PROJECT_DNA_SCHEMA:
        raise ValueError("project_dna schema is unsupported")
    required = {
        "schema",
        "project_id",
        "character_ids",
        "location_ids",
        "prop_ids",
        "style_anchor",
        "positive_preferences",
        "vetoes",
        "locked_rules",
        "source_refs",
        "provenance",
        "dna_revision",
    }
    missing = sorted(required - set(dna))
    if missing:
        raise ValueError("project_dna missing fields: " + ", ".join(missing))
    for key in (
        "character_ids",
        "location_ids",
        "prop_ids",
        "positive_preferences",
        "vetoes",
        "locked_rules",
        "source_refs",
    ):
        if not isinstance(dna.get(key), list):
            raise ValueError(f"project_dna {key} must be a list")
    for key in ("style_anchor", "provenance"):
        if not isinstance(dna.get(key), Mapping):
            raise ValueError(f"project_dna {key} must be an object")
    if _text(dna.get("project_id"), limit=200) == "":
        raise ValueError("project_dna project_id is required")
    if _text(dna.get("dna_revision"), limit=100) != compute_dna_revision(dna):
        raise ValueError("project_dna dna_revision does not match its contents")
    return dna


__all__ = [
    "PROJECT_DNA_REVISION_PREFIX",
    "PROJECT_DNA_SCHEMA",
    "build_project_dna",
    "compute_dna_revision",
    "validate_project_dna",
]
