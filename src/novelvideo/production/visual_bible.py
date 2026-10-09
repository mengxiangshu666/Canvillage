"""Visual bible and evidence-derived asset view planning.

The visual bible is a project-level contract, not another asset database.  It
references AssetPassport-compatible identities, keeps the locked look and
lighting facts, and derives which asset views a shot list actually needs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .cinematic_contract import build_cinematic_contract


VISUAL_BIBLE_SCHEMA = "visual_bible.v1"
VISUAL_BIBLE_AUDIT_SCHEMA = "visual_bible_audit.v1"
ASSET_VIEW_PLAN_SCHEMA = "asset_view_plan.v1"

_VIEW_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("back", ("背影", "背面", "背对", "转身离开", "背向", "back view")),
    ("side", ("侧面", "侧脸", "侧身", "侧向", "profile", "side view")),
    ("top", ("俯拍", "俯视", "顶视", "鸟瞰", "地面", "躺", "倒下", "overhead")),
    ("low", ("仰拍", "仰视", "低机位", "低角度", "low angle")),
    ("close_up", ("特写", "面部", "眼睛", "嘴角", "close-up", "close up")),
    ("full_body", ("全身", "远景", "全景", "走过", "奔跑", "跳跃", "full body")),
    ("expression", ("哭", "笑", "怒", "恐惧", "惊讶", "崩溃", "沉默", "表情")),
)


def _text(value: object, *, limit: int = 3000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _list(value: object, *, limit: int = 100, item_limit: int = 800) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item, limit=item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _pick(source: Mapping[str, Any], *keys: str, limit: int = 1000) -> str:
    for key in keys:
        text = _text(source.get(key), limit=limit)
        if text:
            return text
    return ""


def _positive_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(parsed, 2_147_483_647))


def _canonical_payload(value: Mapping[str, Any]) -> str:
    return json.dumps(
        {key: value[key] for key in sorted(value) if key != "contract_revision"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_visual_bible_revision(value: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_payload(value).encode("utf-8")).hexdigest()[:20]
    return f"visual-bible.v1:{digest}"


def _asset_id(asset: Mapping[str, Any]) -> str:
    return _text(
        asset.get("asset_id")
        or asset.get("assetId")
        or asset.get("id")
        or asset.get("identity_id")
        or asset.get("identityId"),
        limit=300,
    )


def _asset_kind(asset: Mapping[str, Any]) -> str:
    value = _pick(
        asset,
        "asset_kind",
        "assetKind",
        "kind",
        "type",
        "role",
        "reference_role",
        "referenceRole",
    ).casefold()
    if any(token in value for token in ("character", "角色", "person")):
        return "character"
    if any(token in value for token in ("scene", "location", "场景", "环境")):
        return "scene"
    if any(token in value for token in ("prop", "道具", "object")):
        return "prop"
    return "unknown"


def _asset_views(asset: Mapping[str, Any]) -> set[str]:
    raw = (
        asset.get("views")
        or asset.get("available_views")
        or asset.get("availableViews")
        or asset.get("view_roles")
        or asset.get("viewRoles")
    )
    return {item.casefold() for item in _list(raw, limit=40, item_limit=80)}


def _asset_lock(asset: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _mapping(asset.get("metadata"))
    merged = {**metadata, **asset}
    return {
        "asset_id": _asset_id(merged),
        "kind": _asset_kind(merged),
        "revision": _positive_int(
            merged.get("asset_revision")
            or merged.get("assetRevision")
            or merged.get("revision")
            or merged.get("version")
        ),
        "sha256": _text(
            merged.get("sha256")
            or merged.get("asset_sha256")
            or merged.get("assetSha256"),
            limit=64,
        ).lower(),
        "identity_locks": _list(
            merged.get("identity_locks")
            or merged.get("identityLocks")
            or merged.get("locked_fields")
            or merged.get("lockedFields"),
            limit=32,
            item_limit=160,
        ),
        "views": sorted(_asset_views(merged)),
    }


def _shot_asset_refs(shot: Mapping[str, Any]) -> set[str]:
    raw = (
        shot.get("reference_bindings")
        or shot.get("referenceBindings")
        or shot.get("asset_bindings")
        or shot.get("assetBindings")
        or {}
    )
    result: set[str] = set()
    if isinstance(raw, Mapping):
        for value in raw.values():
            result.update(_list(value, limit=40, item_limit=300))
    else:
        result.update(_list(raw, limit=40, item_limit=300))
    for key in ("asset_id", "assetId", "character_id", "characterId", "scene_id", "prop_id"):
        value = _text(shot.get(key), limit=300)
        if value:
            result.add(value)
    return result


def _shot_view_requirements(shot: Mapping[str, Any]) -> set[str]:
    text = " ".join(
        _text(shot.get(key))
        for key in (
            "shot",
            "visual_description",
            "primary_action",
            "action",
            "camera_motion",
            "camera_position",
            "prompt",
            "shot_prompt",
        )
    ).casefold()
    return {
        view
        for view, markers in _VIEW_RULES
        if any(marker.casefold() in text for marker in markers)
    }


def _view_plan(
    *,
    assets: list[dict[str, Any]],
    shots: list[dict[str, Any]],
) -> dict[str, Any]:
    by_id = {_asset_lock(asset)["asset_id"]: _asset_lock(asset) for asset in assets if _asset_id(asset)}
    requirements: dict[str, dict[str, Any]] = {}
    for position, shot in enumerate(shots, start=1):
        shot_id = _text(shot.get("shot_id") or shot.get("shotId"), limit=160) or f"S{position:02d}"
        refs = _shot_asset_refs(shot)
        view_hits = _shot_view_requirements(shot)
        for asset_id in refs:
            asset = by_id.get(asset_id)
            kind = _asset_kind(asset or {}) if asset else "unknown"
            required = set(view_hits)
            if kind == "character":
                required.add("front")
                if view_hits & {"full_body", "top", "back", "side"}:
                    required.add("full_body")
                if "back" in view_hits:
                    required.add("back")
                if "side" in view_hits:
                    required.add("side")
                if "expression" in view_hits:
                    required.add("expression")
            elif kind == "scene":
                required.add("wide")
                if "top" in view_hits or "low" in view_hits:
                    required.add("geometry")
            elif kind == "prop":
                required.add("hero")
                if "top" in view_hits or "back" in view_hits:
                    required.add("multi_view")
            item = requirements.setdefault(
                asset_id,
                {
                    "asset_id": asset_id,
                    "kind": kind,
                    "required_views": set(),
                    "shot_ids": set(),
                    "available_views": set(asset.get("views", []) if asset else []),
                    "reason": [],
                },
            )
            item["required_views"].update(required)
            item["shot_ids"].add(shot_id)
        for asset_id, asset in by_id.items():
            if asset_id not in refs:
                continue
            if _asset_kind(asset) == "character":
                requirements[asset_id]["reason"].append(f"{shot_id}：角色身份基准")
            elif _asset_kind(asset) == "scene":
                requirements[asset_id]["reason"].append(f"{shot_id}：场景空间基准")
            elif _asset_kind(asset) == "prop":
                requirements[asset_id]["reason"].append(f"{shot_id}：道具外观基准")

    normalized: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for item in requirements.values():
        required_views = sorted(item["required_views"])
        available = set(item["available_views"])
        missing_views = [
            view for view in required_views if view not in available
        ]
        record = {
            "asset_id": item["asset_id"],
            "kind": item["kind"],
            "required_views": required_views,
            "available_views": sorted(available),
            "missing_views": missing_views,
            "shot_ids": sorted(item["shot_ids"]),
            "reason": list(dict.fromkeys(item["reason"]))[:20],
            "ready": not missing_views,
        }
        normalized.append(record)
        if missing_views:
            missing.append(record)
    return {
        "schema": ASSET_VIEW_PLAN_SCHEMA,
        "assets": normalized,
        "missing_count": len(missing),
        "ready": not missing,
    }


def build_visual_bible(
    *,
    director_vision: Mapping[str, Any] | None = None,
    project_dna: Mapping[str, Any] | None = None,
    assets: object = None,
    shots: object = None,
    output_spec: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile visual locks and asset-view needs from existing facts."""

    vision = _mapping(director_vision)
    dna = _mapping(project_dna)
    spec = _mapping(output_spec)
    asset_list = [
        dict(item)
        for item in (assets if isinstance(assets, (list, tuple)) else [])
        if isinstance(item, Mapping)
    ]
    shot_list = [
        dict(item)
        for item in (shots if isinstance(shots, (list, tuple)) else [])
        if isinstance(item, Mapping)
    ]
    style_anchor = _mapping(vision.get("style_anchor"))
    cinematic = build_cinematic_contract(
        director_vision=vision,
        project_dna=dna,
    )
    color_look = _mapping(cinematic.get("color_look"))
    lighting = _mapping(cinematic.get("lighting"))
    lens_package = _mapping(
        spec.get("lens_package")
        or style_anchor.get("lens_package")
        or dna.get("lens_package")
    )
    look_id = _pick(color_look, "look_id", "lookId") or _pick(
        style_anchor,
        "visual_style",
        "style",
    )
    result: dict[str, Any] = {
        "schema": VISUAL_BIBLE_SCHEMA,
        "look": {
            "look_id": look_id,
            "visual_style": _pick(style_anchor, "visual_style", "style"),
            "texture": _pick(style_anchor, "texture", "material"),
            "composition": _pick(style_anchor, "composition", "framing"),
            "camera_language": _pick(style_anchor, "camera_language", "camera"),
        },
        "color_script": {
            "look_id": look_id,
            "dominant": _pick(color_look, "dominant"),
            "secondary": _pick(color_look, "secondary"),
            "accent": _pick(color_look, "accent"),
            "ratios": _mapping(color_look.get("ratios")),
            "palette": _list(color_look.get("palette") or style_anchor.get("color_palette"), limit=16),
        },
        "lighting_bible": {
            "source_direction": _pick(lighting, "source_direction", "sourceDirection"),
            "color_temperature_k": lighting.get("color_temperature_k"),
            "key_fill_ratio": _pick(lighting, "key_fill_ratio", "keyFillRatio"),
            "motivated_source": _pick(lighting, "motivated_source", "motivatedSource"),
            "change_policy": _pick(lighting, "change_policy", "changePolicy"),
            "description": _pick(lighting, "description")
            or _pick(style_anchor, "lighting", "light"),
        },
        "lens_package": lens_package,
        "world_logic": _list(
            vision.get("world_logic")
            or dna.get("world_logic")
            or spec.get("world_logic"),
            limit=30,
        ),
        "reference_boards": [
            _mapping(item)
            for item in (
                vision.get("reference_boards")
                or dna.get("reference_boards")
                or spec.get("reference_boards")
                or []
            )
            if isinstance(item, Mapping)
        ][:40],
        "asset_locks": [
            _asset_lock(asset) for asset in asset_list if _asset_id(asset)
        ],
        "asset_view_plan": _view_plan(assets=asset_list, shots=shot_list),
        "provenance": {
            "vision_revision": _text(vision.get("vision_revision"), limit=100),
            "project_dna_revision": _text(dna.get("dna_revision"), limit=100),
        },
    }
    result["contract_revision"] = compute_visual_bible_revision(result)
    return result


def validate_visual_bible(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("visual_bible must be an object")
    bible = deepcopy(dict(value))
    if bible.get("schema") != VISUAL_BIBLE_SCHEMA:
        raise ValueError("visual_bible schema is unsupported")
    expected = compute_visual_bible_revision(bible)
    if _text(bible.get("contract_revision"), limit=100) != expected:
        raise ValueError("visual_bible contract_revision does not match its contents")
    return bible


def _duplicate_lock_issues(asset_locks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    for lock in asset_locks:
        asset_id = _text(lock.get("asset_id"), limit=300)
        if not asset_id:
            continue
        signature = (
            lock.get("revision"),
            lock.get("sha256"),
            tuple(lock.get("identity_locks") or []),
        )
        previous = seen.get(asset_id)
        if previous is not None and previous != signature:
            issues.append(
                {
                    "code": "visual.asset_lock_conflict",
                    "asset_id": asset_id,
                    "message": f"资产 {asset_id} 同时存在多个 revision/hash 事实",
                    "fix": "只保留一个权威 AssetPassport 版本，旧版本作为新文件保留。",
                }
            )
        seen[asset_id] = signature
    return issues


def audit_visual_bible(value: object) -> dict[str, Any]:
    try:
        bible = validate_visual_bible(value)
    except ValueError as exc:
        return {
            "schema": VISUAL_BIBLE_AUDIT_SCHEMA,
            "passed": False,
            "issues": [{"code": "visual.contract_invalid", "message": str(exc)}],
            "gate_observations": {
                "visual_bible_locked": False,
                "asset_view_plan_ready": False,
            },
        }

    look = _mapping(bible.get("look"))
    color = _mapping(bible.get("color_script"))
    lighting = _mapping(bible.get("lighting_bible"))
    asset_locks = [
        _mapping(item)
        for item in (bible.get("asset_locks") or [])
        if isinstance(item, Mapping)
    ]
    view_plan = _mapping(bible.get("asset_view_plan"))
    issues = _duplicate_lock_issues(asset_locks)
    visual_applicable = bool(look or color or lighting)
    if visual_applicable and not _text(look.get("look_id")):
        issues.append(
            {
                "code": "visual.look_id_missing",
                "message": "视觉圣经缺少稳定的 look_id",
                "fix": "为全片 Look 指定稳定 ID；后续镜头只能引用，不得逐镜重建。",
            }
        )
    if visual_applicable and not _text(lighting.get("source_direction")) and not _text(
        lighting.get("description")
    ):
        issues.append(
            {
                "code": "visual.lighting_missing",
                "message": "视觉圣经缺少有来源的光线事实",
                "fix": "写清主光来源、方向、色温和光比，而不是写氛围词。",
            }
        )
    for lock in asset_locks:
        asset_id = _text(lock.get("asset_id"), limit=300)
        if not lock.get("revision") or not _text(lock.get("sha256")):
            issues.append(
                {
                    "code": "visual.asset_lock_incomplete",
                    "asset_id": asset_id,
                    "message": f"资产 {asset_id} 缺少正版本号或 sha256",
                    "fix": "资产未锁定前不得生成下游提示词。",
                }
            )
        if not lock.get("identity_locks"):
            issues.append(
                {
                    "code": "visual.identity_lock_missing",
                    "asset_id": asset_id,
                    "message": f"资产 {asset_id} 没有声明确实锁定的身份字段",
                    "fix": "至少锁定脸部/服装/场景几何/道具外观中的一项。",
                }
            )
    assets = [
        _mapping(item)
        for item in (view_plan.get("assets") or [])
        if isinstance(item, Mapping)
    ]
    missing_views = [
        item for item in assets if _list(item.get("missing_views"), limit=40)
    ]
    observations: dict[str, bool | None] = {
        "visual_bible_locked": not issues if visual_applicable or asset_locks else None,
        "asset_view_plan_ready": (
            not missing_views if assets else None
        ),
    }
    failed = any(value is False for value in observations.values())
    return {
        "schema": VISUAL_BIBLE_AUDIT_SCHEMA,
        "passed": not failed,
        "issues": issues,
        "missing_view_count": len(missing_views),
        "gate_observations": observations,
    }


__all__ = [
    "ASSET_VIEW_PLAN_SCHEMA",
    "VISUAL_BIBLE_AUDIT_SCHEMA",
    "VISUAL_BIBLE_SCHEMA",
    "audit_visual_bible",
    "build_visual_bible",
    "compute_visual_bible_revision",
    "validate_visual_bible",
]
