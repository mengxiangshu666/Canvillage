"""Versioned contract for compiling a natural-language director request.

The contract is deliberately small and independent from canvas execution.  It
records what the user is asking to receive, which asset roles are needed, and
which graph/output checks are allowed to prove completion.  Templates consume
this contract; they do not define it.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from collections.abc import Mapping
from typing import Any


DIRECTOR_INTENT_SCHEMA = "director_intent_contract.v1"
DIRECTOR_INTENT_REVISION_PREFIX = "director-intent.v1:"
DELIVERY_LEVELS = ("idea", "storyboard", "shot_draft", "media_draft", "final_film")
DELIVERY_LEVEL_ALIASES = {
    "rough_cut": "media_draft",
    "rough-cut": "media_draft",
    "roughcut": "media_draft",
    "assembly_cut": "media_draft",
    "video_draft": "media_draft",
    "粗剪": "media_draft",
    "剪辑草稿": "media_draft",
    "final_cut": "final_film",
    "final-cut": "final_film",
    "final_master": "final_film",
    "final-master": "final_film",
    "release_master": "final_film",
    "release-master": "final_film",
    "成片": "final_film",
    "最终成片": "final_film",
    "母版": "final_film",
}
SPATIAL_COMPLEXITIES = ("flat_2d", "multi_angle", "pano_360", "director_desk", "3d_world")
# A contract is either authored in full (and then its `contract_revision` must
# match its contents) or supplied as partial intent and compiled by
# `build_director_intent_contract`.  Callers need this list to tell the two
# cases apart without matching on validator error strings.
DIRECTOR_INTENT_REQUIRED_FIELDS: tuple[str, ...] = (
    "schema",
    "project_goal",
    "delivery_level",
    "characters",
    "locations",
    "props",
    "style",
    "required_assets",
    "spatial_complexity",
    "reference_policy",
    "shot_count",
    "audio_required",
    "subtitles_required",
    "compose_required",
    "quality_gates",
    "graph_contract",
    "template_policy",
    "contract_revision",
)


def _text(value: object, *, limit: int = 12_000) -> str:
    return str(value or "").strip()[:limit]


def _unique_texts(value: object, *, limit: int = 100, item_limit: int = 500) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        item_text = _text(item, limit=item_limit)
        if item_text and item_text not in result:
            result.append(item_text)
        if len(result) >= limit:
            break
    return result


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _entities(value: object, *, limit: int = 50) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:limit]:
        if isinstance(item, Mapping):
            entity = deepcopy(dict(item))
            entity_id = _text(entity.get("id") or entity.get("asset_id"), limit=160)
            if entity_id:
                entity["id"] = entity_id
            result.append(entity)
        else:
            item_text = _text(item, limit=160)
            if item_text:
                result.append({"id": item_text})
    return result


def _contains(text: str, *markers: str) -> bool:
    return any(marker.casefold() in text.casefold() for marker in markers)


def normalize_delivery_level(value: object) -> str:
    """Return a canonical delivery tier for an Agent-facing synonym."""

    candidate = _text(value, limit=40).casefold()
    if candidate in DELIVERY_LEVELS:
        return candidate
    return DELIVERY_LEVEL_ALIASES.get(candidate, "")


def infer_delivery_level(project_goal: object, output_spec: object = None) -> str:
    """Infer only the delivery tier; explicit draft language wins."""

    text = _text(project_goal)
    spec = _mapping(output_spec)
    explicit = normalize_delivery_level(spec.get("delivery_level"))
    if explicit:
        return explicit
    if _contains(
        text,
        "不生成",
        "不启动媒体",
        "不启动图片",
        "不启动视频",
        "只搭",
        "结构草稿",
        "分镜草稿",
        "shot draft",
    ):
        return "shot_draft"
    if _contains(
        text,
        "完整成片",
        "最终成片",
        "最终视频",
        "完整电影",
        "导出成片",
        "交付成片",
        "final film",
        "final video",
    ):
        return "final_film"
    if _contains(text, "生成视频", "生成媒体", "图生视频", "出片", "media draft", "视频片段"):
        return "media_draft"
    if _contains(text, "分镜", "镜头表", "镜头草稿", "预告片", "短片", "storyboard", "shot"):
        return "shot_draft"
    return "idea"


def _infer_shot_count(project_goal: object, episode_plan: object) -> int:
    text = _text(project_goal)
    for pattern in (
        r"(?<!\d)(\d{1,2})\s*(?:个)?\s*(?:镜头|镜|shots?)",
        r"(?:镜头|shots?)\s*[=:：]?\s*(\d{1,2})",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return max(0, min(12, int(match.group(1))))
    if isinstance(episode_plan, list) and episode_plan:
        return max(0, min(12, len(episode_plan)))
    return 0


def infer_spatial_complexity(project_goal: object) -> str:
    text = _text(project_goal)
    if _contains(text, "3d", "三维", "3d世界", "穿行", "环绕", "自由机位"):
        return "3d_world"
    if _contains(text, "全景", "360", "pano", "环境全景"):
        return "pano_360"
    if _contains(text, "导演台", "多机位", "多角度", "正面", "背面", "四视图"):
        return "director_desk"
    if _contains(text, "转身", "走过", "空间关系", "连续场景"):
        return "multi_angle"
    return "flat_2d"


def _required_roles(
    *,
    project_goal: str,
    delivery_level: str,
    shot_count: int,
    characters: list[dict[str, Any]],
    locations: list[dict[str, Any]],
    props: list[dict[str, Any]],
    audio_required: bool,
    subtitles_required: bool,
    compose_required: bool,
) -> list[str]:
    roles: list[str] = []

    def add(role: str) -> None:
        if role not in roles:
            roles.append(role)

    text = project_goal
    if characters or _contains(text, "角色", "人物", "主角", "character"):
        add("character_asset")
    if locations or _contains(text, "场景", "环境", "地点", "小巷", "城市", "location"):
        add("location_asset")
    if props or _contains(text, "道具", "物品", "核心", "武器", "prop"):
        add("prop_asset")
    if shot_count > 1 or delivery_level in {"storyboard", "shot_draft", "media_draft", "final_film"}:
        add("world_bible")
        add("storyboard")
        add("shot_sequence")
    if delivery_level in {"media_draft", "final_film"}:
        add("video_generation")
    if audio_required:
        add("audio")
    if subtitles_required:
        add("subtitles")
    if compose_required:
        add("final_compose")
    return roles


def _quality_gates(
    *,
    delivery_level: str,
    required_roles: list[str],
    audio_required: bool,
    compose_required: bool,
) -> list[str]:
    gates = ["director_plan_valid", "director_vision_valid", "canvas_structure_receipt"]
    if delivery_level in {"storyboard", "shot_draft", "media_draft", "final_film"}:
        gates.extend(
            [
                "story_and_shots_complete",
                "shot_contracts_valid",
                "required_roles_present",
                "graph_contract_satisfied",
            ]
        )
    if any(role in required_roles for role in ("character_asset", "location_asset", "prop_asset")):
        gates.append("asset_bindings_valid")
    if delivery_level in {"media_draft", "final_film"}:
        gates.extend(["media_assets_ready", "visual_continuity"])
    if audio_required or "subtitles" in required_roles:
        gates.append("audio_subtitles_ready")
    if compose_required:
        gates.extend(["compose_node_present", "final_compose_artifact"])
    if delivery_level == "final_film":
        gates.extend(
            [
                "series_story_contract_valid",
                "visual_bible_locked",
                "asset_view_plan_ready",
                "dialogue_sound_contract_valid",
                "film_prompt_contract_valid",
                "screening_feedback_recorded",
            ]
        )
    return list(dict.fromkeys(gates))


def _canonical_payload(contract: Mapping[str, Any]) -> str:
    return json.dumps(
        {key: contract[key] for key in sorted(contract) if key != "contract_revision"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_contract_revision(contract: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_payload(contract).encode("utf-8")).hexdigest()[:20]
    return f"{DIRECTOR_INTENT_REVISION_PREFIX}{digest}"


def build_director_intent_contract(
    *,
    project_goal: object,
    contract: object = None,
    output_spec: object = None,
    asset_plan: object = None,
    episode_plan: object = None,
) -> dict[str, Any]:
    """Normalize explicit intent and derive only bounded defaults."""

    raw = _mapping(contract)
    goal = _text(raw.get("project_goal") or project_goal)
    if not goal:
        raise ValueError("director intent project_goal is required")
    spec = _mapping(output_spec)
    raw_spec = _mapping(raw.get("output_spec"))
    merged_spec = {**spec, **raw_spec}
    delivery_level = normalize_delivery_level(raw.get("delivery_level")) or infer_delivery_level(
        goal, merged_spec
    )
    if delivery_level not in DELIVERY_LEVELS:
        raise ValueError("director intent delivery_level is unsupported")
    raw_assets = _mapping(asset_plan)
    raw_assets.update(_mapping(raw.get("asset_plan")))
    characters = _entities(raw.get("characters") or raw_assets.get("characters"))
    locations = _entities(raw.get("locations") or raw_assets.get("locations"))
    props = _entities(raw.get("props") or raw_assets.get("props"))
    shot_count = raw.get("shot_count")
    if not isinstance(shot_count, int) or isinstance(shot_count, bool):
        shot_count = _infer_shot_count(goal, episode_plan or raw.get("episode_plan"))
    shot_count = max(0, min(12, shot_count))
    requested_spatial = _text(raw.get("spatial_complexity"), limit=40)
    # Partial intent is compiled server-side.  Spatial complexity is a derived
    # contract field, so an Agent-side synonym such as "low" must not poison a
    # whole structured turn.  Complete contracts still fail closed in
    # ``validate_director_intent_contract``.
    spatial = (
        requested_spatial
        if requested_spatial in SPATIAL_COMPLEXITIES
        else infer_spatial_complexity(goal)
    )
    audio_required = raw.get("audio_required")
    if not isinstance(audio_required, bool):
        audio_required = _contains(goal, "配音", "旁白", "台词", "音乐", "bgm", "音频", "voice", "audio")
    subtitles_required = raw.get("subtitles_required")
    if not isinstance(subtitles_required, bool):
        subtitles_required = _contains(goal, "字幕", "subtitle", "captions")
    compose_required = raw.get("compose_required")
    if not isinstance(compose_required, bool):
        compose_required = delivery_level == "final_film"
    required_roles = _required_roles(
        project_goal=goal,
        delivery_level=delivery_level,
        shot_count=shot_count,
        characters=characters,
        locations=locations,
        props=props,
        audio_required=audio_required,
        subtitles_required=subtitles_required,
        compose_required=compose_required,
    )
    graph_raw = _mapping(raw.get("graph_contract"))
    graph_roles = _unique_texts(graph_raw.get("required_node_roles"), limit=50, item_limit=100) or required_roles
    graph_edges = _unique_texts(graph_raw.get("required_edges"), limit=100, item_limit=200)
    if not graph_edges:
        graph_edges = [
            "character_asset -> shot_sequence",
            "location_asset -> shot_sequence",
            "prop_asset -> shot_sequence",
            "storyboard -> shot_sequence",
            "shot_sequence -> video_generation",
            "video_generation -> final_compose",
            "audio -> final_compose",
        ]
        graph_edges = [
            edge for edge in graph_edges
            if edge.split(" -> ", 1)[0] in graph_roles and edge.split(" -> ", 1)[1] in graph_roles
        ]
    quality_gates = _unique_texts(raw.get("quality_gates"), limit=50, item_limit=120) or _quality_gates(
        delivery_level=delivery_level,
        required_roles=graph_roles,
        audio_required=audio_required,
        compose_required=compose_required,
    )
    required_assets = _unique_texts(raw.get("required_assets"), limit=50, item_limit=120)
    for role in graph_roles:
        if role.endswith("_asset") and role not in required_assets:
            required_assets.append(role)
    reference_policy = {
        "bind_by_asset_id": True,
        "prompt_only_is_insufficient": True,
        "allow_unbound_candidates": delivery_level in {"idea", "storyboard", "shot_draft"},
        **_mapping(raw.get("reference_policy")),
    }
    result: dict[str, Any] = {
        "schema": DIRECTOR_INTENT_SCHEMA,
        "project_goal": goal,
        "delivery_level": delivery_level,
        "characters": characters,
        "locations": locations,
        "props": props,
        "style": _mapping(raw.get("style")),
        "output_spec": merged_spec,
        "required_assets": required_assets,
        "spatial_complexity": spatial,
        "reference_policy": reference_policy,
        "shot_count": shot_count,
        "audio_required": bool(audio_required),
        "subtitles_required": bool(subtitles_required),
        "compose_required": bool(compose_required),
        "quality_gates": quality_gates,
        "graph_contract": {
            "required_node_roles": graph_roles,
            "required_edges": graph_edges,
            "forbidden_node_roles": _unique_texts(
                graph_raw.get("forbidden_node_roles"), limit=50, item_limit=100
            ),
        },
        "template_policy": {
            "selection": "dynamic_composition",
            "starter_workflow_is_scaffold": True,
            "final_film_requires_compose": bool(compose_required),
        },
    }
    result["contract_revision"] = compute_contract_revision(result)
    return result


def validate_director_intent_contract(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("director_intent_contract must be an object")
    contract = deepcopy(dict(value))
    missing = sorted(set(DIRECTOR_INTENT_REQUIRED_FIELDS) - set(contract))
    if missing:
        raise ValueError("director_intent_contract missing fields: " + ", ".join(missing))
    if contract.get("schema") != DIRECTOR_INTENT_SCHEMA:
        raise ValueError("director_intent_contract schema is unsupported")
    if not _text(contract.get("project_goal")):
        raise ValueError("director_intent_contract project_goal is required")
    if contract.get("delivery_level") not in DELIVERY_LEVELS:
        raise ValueError("director_intent_contract delivery_level is unsupported")
    if contract.get("spatial_complexity") not in SPATIAL_COMPLEXITIES:
        raise ValueError("director_intent_contract spatial_complexity is unsupported")
    if not isinstance(contract.get("shot_count"), int) or not 0 <= contract["shot_count"] <= 12:
        raise ValueError("director_intent_contract shot_count is invalid")
    for key in ("characters", "locations", "props", "required_assets", "quality_gates"):
        if not isinstance(contract.get(key), list):
            raise ValueError(f"director_intent_contract {key} must be a list")
    if not isinstance(contract.get("graph_contract"), Mapping):
        raise ValueError("director_intent_contract graph_contract must be an object")
    if "output_spec" in contract and not isinstance(contract.get("output_spec"), Mapping):
        raise ValueError("director_intent_contract output_spec must be an object")
    expected = compute_contract_revision(contract)
    if _text(contract.get("contract_revision"), limit=100) != expected:
        raise ValueError("director_intent_contract contract_revision does not match its contents")
    return contract


__all__ = [
    "DELIVERY_LEVELS",
    "DELIVERY_LEVEL_ALIASES",
    "DIRECTOR_INTENT_REVISION_PREFIX",
    "DIRECTOR_INTENT_REQUIRED_FIELDS",
    "DIRECTOR_INTENT_SCHEMA",
    "SPATIAL_COMPLEXITIES",
    "build_director_intent_contract",
    "compute_contract_revision",
    "infer_delivery_level",
    "infer_spatial_complexity",
    "normalize_delivery_level",
    "validate_director_intent_contract",
]
