"""Read-only upgrade preview for older script documents.

Upgrading metadata must never silently rewrite creative fields, re-render media
or trigger paid work. This module only reports what *would* change and what
would stay untouched; applying it remains an explicit caller decision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

SCHEMA = "script_upgrade_preview.v1"
DOCUMENT_VERSION = "script.v2"


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _referenced_assets(rows: Sequence[Mapping[str, Any]]) -> set[tuple[str, str]]:
    assets: set[tuple[str, str]] = set()
    for row in rows:
        for field in ("character_1", "character_2"):
            name = _text(row.get(field))
            if name:
                assets.add(("character", name))
        for role, field in (("scene", "scene_tags"), ("prop", "prop_tags")):
            for name in re_split_tags(row.get(field)):
                assets.add((role, name))
    return assets


def re_split_tags(value: Any) -> list[str]:
    import re

    if not isinstance(value, str):
        return []
    return [
        part.strip()
        for part in re.split(r"[、，,；;\n]+", value)
        if part.strip()
        and part.strip().casefold() not in {"无", "没有", "none", "n/a", "-", "—"}
    ]


def preview_script_upgrade(data: Mapping[str, Any]) -> dict[str, Any]:
    rows = [row for row in (data.get("rows") or []) if isinstance(row, Mapping)]
    plan = data.get("director_plan")
    plan = plan if isinstance(plan, Mapping) else {}
    contracts = plan.get("asset_contents")
    contracts = (
        [item for item in contracts if isinstance(item, Mapping)]
        if isinstance(contracts, list)
        else []
    )
    defined = {
        (str(item.get("role") or ""), _text(item.get("name")))
        for item in contracts
        if _text(item.get("name"))
    }
    referenced = _referenced_assets(rows)
    missing_assets = sorted(referenced - defined)
    target = plan.get("target_duration_seconds")
    changes: list[dict[str, Any]] = []
    if missing_assets:
        changes.append(
            {
                "id": "structured-asset-baseline",
                "kind": "metadata_only",
                "description": "为被引用资产补结构化基准；不改变已确认的角色外观，不自动出图",
                "affected_assets": [f"{role}:{name}" for role, name in missing_assets],
                "affected_shots": [],
                "requires_confirmation": True,
            }
        )
    coverage = data.get("source_coverage")
    if not isinstance(coverage, Mapping) or not coverage.get("units"):
        changes.append(
            {
                "id": "rebuild-source-coverage",
                "kind": "metadata_only",
                "description": "按当前来源重建覆盖账本；纯段落或视频无转录时最多标为未核验",
                "affected_assets": [],
                "affected_shots": [
                    _shot_label(row, index)
                    for index, row in enumerate(rows)
                    if not row.get("source_unit_ids")
                ],
                "requires_confirmation": True,
            }
        )
    if (
        isinstance(target, (int, float))
        and not isinstance(target, bool)
        and target > 0
        and not plan.get("target_duration_policy")
    ):
        changes.append(
            {
                "id": "record-target-policy",
                "kind": "metadata_only",
                "description": "记录整片目标是软目标还是硬约束；只影响解释与对账，不裁剪视频",
                "affected_assets": [],
                "affected_shots": [],
                "requires_confirmation": True,
            }
        )
    film = data.get("film_production")
    prompt_bundle = film.get("prompt_bundle") if isinstance(film, Mapping) else None
    if isinstance(prompt_bundle, Mapping) and not isinstance(
        prompt_bundle.get("compiler"), Mapping
    ):
        changes.append(
            {
                "id": "record-compiler-receipt",
                "kind": "metadata_only",
                "description": "补编译版本与输入/输出摘要；不重出旧媒体",
                "affected_assets": [],
                "affected_shots": [],
                "requires_confirmation": True,
            }
        )
    return {
        "schema": SCHEMA,
        "document_version_from": _text(data.get("document_version")) or "legacy",
        "document_version_to": DOCUMENT_VERSION,
        "changes": changes,
        "protected_user_edits": [
            {
                "shot_id": _shot_label(row, index),
                "fields": [
                    field
                    for field in row.get("user_edited_fields") or []
                    if _text(field)
                ],
            }
            for index, row in enumerate(rows)
            if isinstance(row.get("user_edited_fields"), list)
            and any(_text(field) for field in row["user_edited_fields"])
        ],
        "preserve_user_overrides": True,
        "affected_assets": sorted(
            {
                asset
                for change in changes
                for asset in change.get("affected_assets") or []
            }
        ),
        "affected_shots": sorted(
            {shot for change in changes for shot in change.get("affected_shots") or []}
        ),
        "media_repair_triggered": False,
        "paid_generation_triggered": False,
        "requires_confirmation": bool(changes),
        "can_apply_without_media_rerender": True,
    }


def apply_script_upgrade(
    data: Mapping[str, Any],
    *,
    source_text: str = "",
    accepted_change_ids: Sequence[str] | None = None,
    target_duration_policy: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply only metadata changes that can be derived without inventing facts.

    Asset completion and prompt recompilation are deliberately skipped: they need
    their own agents/evidence and must not be faked by a metadata upgrade.
    """
    from copy import deepcopy

    preview = preview_script_upgrade(data)
    accepted = (
        set(accepted_change_ids)
        if accepted_change_ids is not None
        else {str(change.get("id")) for change in preview["changes"]}
    )
    updated = deepcopy(dict(data))
    applied: list[str] = []
    skipped: list[dict[str, str]] = []
    for change in preview["changes"]:
        change_id = str(change.get("id") or "")
        if change_id not in accepted:
            skipped.append({"id": change_id, "reason": "用户未选择"})
            continue
        if change_id == "rebuild-source-coverage":
            from novelvideo.freezone.source_coverage import build_source_coverage

            updated["source_coverage"] = build_source_coverage(
                source_text,
                [row for row in updated.get("rows") or [] if isinstance(row, Mapping)],
            )
            applied.append(change_id)
        elif change_id == "record-target-policy":
            if target_duration_policy not in {"soft", "hard"}:
                skipped.append(
                    {"id": change_id, "reason": "需要用户明确目标类型，不能猜"}
                )
                continue
            plan = updated.get("director_plan")
            if not isinstance(plan, dict):
                skipped.append({"id": change_id, "reason": "没有导演规划"})
                continue
            plan["target_duration_policy"] = target_duration_policy
            applied.append(change_id)
        elif change_id == "structured-asset-baseline":
            skipped.append(
                {"id": change_id, "reason": "需要资产补全 Agent，不能由升级器编造"}
            )
        elif change_id == "record-compiler-receipt":
            skipped.append(
                {"id": change_id, "reason": "需要重新编译提示词并保留新回执"}
            )
        else:
            skipped.append({"id": change_id, "reason": "未知升级项"})
    if applied and "document_version" not in updated:
        updated["document_version"] = DOCUMENT_VERSION
    return updated, {
        "schema": "script_upgrade_receipt.v1",
        "applied": applied,
        "skipped": skipped,
        "rows_changed": False,
        "media_repair_triggered": False,
        "paid_generation_triggered": False,
    }


def _shot_label(row: Mapping[str, Any], index: int) -> str:
    shot_id = _text(row.get("shot_id"))
    if shot_id:
        return shot_id
    display = _text(row.get("display_shot_no")) or str(row.get("shot_no") or index + 1)
    return f"shot:{display}"
