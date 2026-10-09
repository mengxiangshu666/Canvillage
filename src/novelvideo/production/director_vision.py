"""Versioned whole-film creative intent shared by planning and execution.

``DirectorPlan`` describes how a production runs.  ``DirectorVision`` describes
what must remain coherent while that production is split into shots.  The
object is intentionally deterministic: it only carries facts supplied by the
caller plus explicit, inspectable invariants.  Missing creative decisions are
reported in ``clarification_needed`` instead of being invented by a compiler.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .cinematic_contract import build_cinematic_contract
from .emotion_direction import build_emotion_direction


DIRECTOR_VISION_SCHEMA = "director_vision.v1"
DIRECTOR_VISION_REVISION_PREFIX = "director-vision.v1:"


def _text(value: object, *, limit: int = 2_000) -> str:
    return str(value or "").strip()[:limit]


def _list(value: object, *, limit: int = 40, item_limit: int = 400) -> list[str]:
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


def _entities(value: object, *, limit: int = 40) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value[:limit]:
        if isinstance(item, Mapping):
            identifier = _text(item.get("id") or item.get("asset_id"), limit=160)
        else:
            identifier = _text(item, limit=160)
        if identifier and identifier not in result:
            result.append(identifier)
    return result


def _first(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(mapping.get(key), limit=800)
        if value:
            return value
    return ""


def _canonical_payload(value: Mapping[str, Any]) -> str:
    return json.dumps(
        {key: value[key] for key in sorted(value) if key != "vision_revision"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_vision_revision(value: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_payload(value).encode("utf-8")).hexdigest()[:20]
    return f"{DIRECTOR_VISION_REVISION_PREFIX}{digest}"


def _emotional_arc(
    *,
    directives: Mapping[str, Any],
    style: Mapping[str, Any],
) -> tuple[dict[str, str], list[str]]:
    raw = _mapping(directives.get("emotional_arc") or directives.get("emotion_arc"))
    audience_feeling = _first(
        raw,
        "audience_feeling",
        "audience_emotion",
    ) or _first(style, "audience_feeling", "audience_emotion")
    arc = {
        "opening": _first(raw, "opening", "start"),
        "turning_point": _first(raw, "turning_point", "turn", "middle"),
        "ending": _first(raw, "ending", "end"),
        "audience_feeling": audience_feeling,
    }
    missing = [key for key, value in arc.items() if not value]
    return arc, [f"emotional_arc.{key}" for key in missing]


def build_director_vision(
    *,
    project_goal: object,
    intent_contract: object = None,
    output_spec: object = None,
    constraints: object = None,
    episode_plan: object = None,
) -> dict[str, Any]:
    """Compile a deterministic whole-film vision from known director facts."""

    contract = _mapping(intent_contract)
    spec = _mapping(output_spec)
    style = _mapping(contract.get("style"))
    directives = {
        **_mapping(contract.get("output_spec")),
        **spec,
        **_mapping(contract.get("director_directives")),
    }
    goal = _text(contract.get("project_goal") or project_goal, limit=12_000)
    if not goal:
        raise ValueError("director vision project_goal is required")

    emotional_arc, clarification_needed = _emotional_arc(
        directives=directives,
        style=style,
    )
    emotion_direction = build_emotion_direction(
        project_goal=goal,
        output_spec=spec,
        director_directives=directives,
    )
    characters = _entities(contract.get("characters"))
    locations = _entities(contract.get("locations"))
    props = _entities(contract.get("props"))
    continuity = _mapping(directives.get("continuity_locks"))
    continuity_locks = {
        "characters": _list(continuity.get("characters"), limit=40) or characters,
        "locations": _list(continuity.get("locations"), limit=40) or locations,
        "props": _list(continuity.get("props"), limit=40) or props,
        "wardrobe": _list(continuity.get("wardrobe"), limit=40),
        "world_state": _list(continuity.get("world_state"), limit=40),
    }

    style_anchor = {
        "visual_style": _first(style, "visual_style", "style", "art_style"),
        "color_palette": _first(style, "color_palette", "palette", "color"),
        "lighting": _first(style, "lighting", "light"),
        "texture": _first(style, "texture", "material"),
        "composition": _first(style, "composition", "framing"),
        "camera_language": _first(style, "camera_language", "camera"),
    }
    cinematic_source = (
        directives.get("cinematic")
        or spec.get("cinematic")
        or contract.get("cinematic")
        or {}
    )
    cinematic = build_cinematic_contract(
        director_vision={
            "cinematic": cinematic_source,
            "style_anchor": style_anchor,
        }
    )
    visual_motifs = _list(
        directives.get("visual_motifs") or directives.get("motifs") or style.get("visual_motifs"),
        limit=24,
    )
    rhythm_raw = _mapping(directives.get("rhythm") or style.get("rhythm"))
    shot_count = contract.get("shot_count")
    if not isinstance(shot_count, int) or isinstance(shot_count, bool):
        shot_count = len(episode_plan) if isinstance(episode_plan, list) else 0
    duration = rhythm_raw.get("duration_seconds") or spec.get("duration_seconds")
    rhythm = {
        "pace": _first(rhythm_raw, "pace", "tempo"),
        "duration_seconds": duration if isinstance(duration, (int, float)) and not isinstance(duration, bool) else None,
        "beat_count": max(0, min(100, int(shot_count or 0))),
        "transition_rule": _first(rhythm_raw, "transition_rule", "transitions"),
    }

    shot_principles = _list(
        directives.get("shot_principles") or style.get("shot_principles"),
        limit=24,
    )
    if not shot_principles:
        shot_principles = [
            "每个镜头只推进一个可验证动作",
            "镜头起点承接上一镜头终点，不重复已经完成的动作",
            "先锁定主体、空间和道具状态，再允许机位和节奏变化",
        ]
    quality_invariants = _list(
        directives.get("quality_invariants") or style.get("quality_invariants"),
        limit=24,
    )
    if not quality_invariants:
        quality_invariants = [
            "action_causality_preserved",
            "style_anchor_consistent",
        ]
        if characters:
            quality_invariants.append("character_identity_locked")
        if locations:
            quality_invariants.append("location_geometry_locked")
        if props:
            quality_invariants.append("prop_state_locked")

    mutable_slots = _list(
        directives.get("mutable_slots") or style.get("mutable_slots"),
        limit=24,
    ) or ["shot_action", "shot_scale", "camera_motion", "timing"]

    result: dict[str, Any] = {
        "schema": DIRECTOR_VISION_SCHEMA,
        "project_goal": goal,
        "emotional_arc": emotional_arc,
        "emotion_direction": emotion_direction,
        "visual_motifs": visual_motifs,
        "style_anchor": style_anchor,
        "cinematic": cinematic,
        "rhythm": rhythm,
        "continuity_locks": continuity_locks,
        "shot_principles": shot_principles,
        "quality_invariants": quality_invariants,
        "mutable_slots": mutable_slots,
        "clarification_needed": clarification_needed,
        "provenance": {
            "source": "director_intent_contract",
            "intent_revision": _text(contract.get("contract_revision"), limit=100),
        },
    }
    result["vision_revision"] = compute_vision_revision(result)
    return result


def validate_director_vision(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("director_vision must be an object")
    vision = deepcopy(dict(value))
    if vision.get("schema") != DIRECTOR_VISION_SCHEMA:
        raise ValueError("director_vision schema is unsupported")
    required = {
        "project_goal",
        "emotional_arc",
        "visual_motifs",
        "style_anchor",
        "rhythm",
        "continuity_locks",
        "shot_principles",
        "quality_invariants",
        "mutable_slots",
        "clarification_needed",
        "provenance",
        "vision_revision",
    }
    missing = sorted(required - set(vision))
    if missing:
        raise ValueError("director_vision missing fields: " + ", ".join(missing))
    if not _text(vision.get("project_goal"), limit=12_000):
        raise ValueError("director_vision project_goal is required")
    if not isinstance(vision.get("emotional_arc"), Mapping):
        raise ValueError("director_vision emotional_arc must be an object")
    for key in (
        "visual_motifs",
        "shot_principles",
        "quality_invariants",
        "mutable_slots",
        "clarification_needed",
    ):
        if not isinstance(vision.get(key), list):
            raise ValueError(f"director_vision {key} must be a list")
    for key in ("style_anchor", "rhythm", "continuity_locks", "provenance"):
        if not isinstance(vision.get(key), Mapping):
            raise ValueError(f"director_vision {key} must be an object")
    if "cinematic" in vision and not isinstance(vision.get("cinematic"), Mapping):
        raise ValueError("director_vision cinematic must be an object")
    expected = compute_vision_revision(vision)
    if _text(vision.get("vision_revision"), limit=100) != expected:
        raise ValueError("director_vision vision_revision does not match its contents")
    return vision


def vision_from_plan_or_inputs(
    plan: object = None,
    *,
    project_goal: object = "",
    intent_contract: object = None,
    output_spec: object = None,
    constraints: object = None,
    episode_plan: object = None,
) -> dict[str, Any]:
    """Return a validated plan vision, or a deterministic compatibility view."""

    if isinstance(plan, Mapping) and isinstance(plan.get("director_vision"), Mapping):
        return validate_director_vision(plan["director_vision"])
    return build_director_vision(
        project_goal=project_goal,
        intent_contract=intent_contract,
        output_spec=output_spec,
        constraints=constraints,
        episode_plan=episode_plan,
    )


__all__ = [
    "DIRECTOR_VISION_REVISION_PREFIX",
    "DIRECTOR_VISION_SCHEMA",
    "build_director_vision",
    "compute_vision_revision",
    "validate_director_vision",
    "vision_from_plan_or_inputs",
]
