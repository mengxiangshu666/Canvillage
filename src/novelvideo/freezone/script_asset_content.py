"""Structured asset content contracts for the script generator.

Old free-form cards stay readable. A script that opts into ``asset_contents``
must either give each applicable fact or say why it does not apply; unknown
placeholders never count as completeness. This module owns the field rules and
the targeted, definition-only merge helper used by repair flows.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from typing import Any

RULE_ID = "script.asset.content.complete.v1"

# Field names are intentionally stable across roles; the prompt/UI can render
# the same key in different languages.
REQUIRED_FIELDS_BY_ROLE: dict[str, tuple[str, ...]] = {
    "character": (
        "age_stage",
        "body_proportion",
        "face_features",
        "skin_hair",
        "base_wardrobe",
        "stable_marks",
    ),
    "scene": (
        "spatial_boundary",
        "landmarks",
        "relative_layout",
        "materials",
        "light_sources",
    ),
    "prop": (
        "appearance_scale",
        "material_structure",
        "narrative_function",
        "operable_parts",
        "base_state",
    ),
}

# Non-human actors keep the identity gates but not human skin/wardrobe fields.
REQUIRED_FIELDS_BY_CHARACTER_KIND: dict[str, tuple[str, ...]] = {
    "human": REQUIRED_FIELDS_BY_ROLE["character"],
    "child": REQUIRED_FIELDS_BY_ROLE["character"],
    "animal": (
        "age_stage",
        "body_proportion",
        "face_features",
        "skin_hair",
        "stable_marks",
    ),
    "robot": (
        "body_proportion",
        "face_features",
        "surface_materials",
        "stable_marks",
    ),
    "other": (
        "body_proportion",
        "face_features",
        "surface_materials",
        "stable_marks",
    ),
}

_PLACEHOLDERS = {
    "",
    "无",
    "没有",
    "未知",
    "不详",
    "未定",
    "none",
    "n/a",
    "na",
    "unknown",
    "-",
    "—",
}


def normalized_asset_key(role: str, name: str) -> str:
    normalized_name = re.sub(r"\s+", " ", name).strip().casefold()
    return f"{role.strip().lower()}:{normalized_name}"


def _text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _has_fact(value: Any) -> bool:
    text = _text(value)
    return bool(text) and text.casefold() not in _PLACEHOLDERS


def required_fields(role: str, subject_kind: str | None) -> tuple[str, ...]:
    if role == "character":
        return REQUIRED_FIELDS_BY_CHARACTER_KIND.get(
            subject_kind or "other", REQUIRED_FIELDS_BY_CHARACTER_KIND["other"]
        )
    return REQUIRED_FIELDS_BY_ROLE.get(role, ())


def _row_references(row: Mapping[str, Any]) -> dict[str, set[str]]:
    characters = {
        _text(row.get(field))
        for field in ("character_1", "character_2")
        if _has_fact(row.get(field))
    }
    scenes = {
        tag.strip()
        for tag in re.split(r"[、，,；;\n]+", str(row.get("scene_tags") or ""))
        if tag.strip() and tag.strip().casefold() not in _PLACEHOLDERS
    }
    props = {
        tag.strip()
        for tag in re.split(r"[、，,；;\n]+", str(row.get("prop_tags") or ""))
        if tag.strip() and tag.strip().casefold() not in _PLACEHOLDERS
    }
    return {"character": characters, "scene": scenes, "prop": props}


def asset_content_issues(
    rows: Sequence[Mapping[str, Any]],
    director_plan: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return blocking contract issues for a structured asset baseline.

    Empty ``asset_contents`` means a legacy/free-form script: no completeness
    claim is made and no issue is raised. Once a plan provides any structured
    contract, every referenced asset must be covered so a single filled card
    cannot silently stand in for the rest of the cast.
    """
    plan = director_plan if isinstance(director_plan, Mapping) else {}
    raw_contracts = plan.get("asset_contents")
    if not isinstance(raw_contracts, list) or not raw_contracts:
        return []
    contracts = [item for item in raw_contracts if isinstance(item, Mapping)]
    by_key: dict[str, Mapping[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    first_reference: dict[tuple[str, str], int] = {}
    referenced: dict[tuple[str, str], str] = {}
    for index, row in enumerate(rows):
        for role, names in _row_references(row).items():
            for name in names:
                referenced.setdefault((role, name.casefold()), name)
                first_reference.setdefault((role, name.casefold()), index)

    for contract in contracts:
        role = _text(contract.get("role")).lower()
        name = _text(contract.get("name"))
        key = normalized_asset_key(role, name)
        if not name or role not in REQUIRED_FIELDS_BY_ROLE:
            issues.append(
                _issue(
                    "资产基准的角色或名称无效；角色只能是 character / scene / prop",
                    row_index=0,
                    asset_key=key or "unknown",
                    missing_fields=[],
                )
            )
            continue
        if key in by_key:
            issues.append(
                _issue(
                    f"资产「{name}」有重复的结构化基准；同一资产只能有一份定义",
                    row_index=first_reference.get((role, name.casefold()), 0),
                    asset_key=key,
                    missing_fields=[],
                )
            )
            continue
        by_key[key] = contract
        fields = (
            contract.get("fields")
            if isinstance(contract.get("fields"), Mapping)
            else {}
        )
        not_applicable = (
            contract.get("not_applicable")
            if isinstance(contract.get("not_applicable"), Mapping)
            else {}
        )
        missing: list[str] = []
        for field in required_fields(
            role, _text(contract.get("subject_kind")) or "other"
        ):
            if _has_fact(fields.get(field)):
                continue
            if _has_fact(not_applicable.get(field)):
                continue
            missing.append(field)
        if missing:
            issues.append(
                _issue(
                    f"资产「{name}」的完整设定缺少：{'、'.join(missing)}；请补全该资产，或为确实不适用的字段写明理由",
                    row_index=first_reference.get((role, name.casefold()), 0),
                    asset_key=key,
                    missing_fields=missing,
                )
            )
        source = _text(contract.get("source")) or "source_text"
        if source in {"assumption", "mixed"} and not _has_fact(
            contract.get("assumption_reason")
        ):
            issues.append(
                _issue(
                    f"资产「{name}」含创作假设，但没有说明哪部分为空缺补全；不能把假设写成原文事实",
                    row_index=first_reference.get((role, name.casefold()), 0),
                    asset_key=key,
                    missing_fields=["assumption_reason"],
                )
            )

    if by_key:
        for (role, folded_name), name in referenced.items():
            if normalized_asset_key(role, name) in by_key:
                continue
            issues.append(
                _issue(
                    f"分镜引用了资产「{name}」，但完整资产基准里没有它的结构化定义；请补全该资产",
                    row_index=first_reference.get((role, folded_name), 0),
                    asset_key=normalized_asset_key(role, name),
                    missing_fields=list(required_fields(role, "other")),
                )
            )
    return issues


def _issue(
    message: str,
    *,
    row_index: int,
    asset_key: str,
    missing_fields: Sequence[str],
) -> dict[str, Any]:
    return {
        "rule_id": RULE_ID,
        "severity": "blocking",
        "message": message,
        "row_index": row_index,
        "shot_no": "",
        "field": "asset_contents",
        "detail": {
            "asset_key": asset_key,
            "missing_fields": list(missing_fields),
        },
    }


def merge_asset_content_contract(
    director_plan: Mapping[str, Any],
    asset_key: str,
    *,
    fields: Mapping[str, str] | None = None,
    not_applicable: Mapping[str, str] | None = None,
    assumption_reason: str | None = None,
    source: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Update exactly one asset definition; return the new plan and changed keys.

    Only the target contract changes. Existing facts not mentioned by the patch
    stay untouched, and no media revision is bumped here.
    """
    plan = copy.deepcopy(dict(director_plan))
    contracts = plan.get("asset_contents")
    if not isinstance(contracts, list):
        return plan, []
    target = asset_key.strip().casefold()
    changed: list[str] = []
    for contract in contracts:
        if not isinstance(contract, dict):
            continue
        if (
            normalized_asset_key(
                str(contract.get("role") or ""), str(contract.get("name") or "")
            )
            != target
        ):
            continue
        if fields:
            current = contract.get("fields")
            current = dict(current) if isinstance(current, Mapping) else {}
            for key, value in fields.items():
                if (
                    not isinstance(key, str)
                    or not isinstance(value, str)
                    or not value.strip()
                ):
                    continue
                if current.get(key) != value:
                    current[key] = value
                    changed.append(f"fields.{key}")
            contract["fields"] = current
        if not_applicable:
            current = contract.get("not_applicable")
            current = dict(current) if isinstance(current, Mapping) else {}
            for key, value in not_applicable.items():
                if (
                    not isinstance(key, str)
                    or not isinstance(value, str)
                    or not value.strip()
                ):
                    continue
                if current.get(key) != value:
                    current[key] = value
                    changed.append(f"not_applicable.{key}")
            contract["not_applicable"] = current
        if (
            assumption_reason is not None
            and contract.get("assumption_reason") != assumption_reason
        ):
            contract["assumption_reason"] = assumption_reason
            changed.append("assumption_reason")
        if (
            source is not None
            and source in {"source_text", "reference_image", "assumption", "mixed"}
            and contract.get("source") != source
        ):
            contract["source"] = source
            changed.append("source")
        break
    return plan, changed
