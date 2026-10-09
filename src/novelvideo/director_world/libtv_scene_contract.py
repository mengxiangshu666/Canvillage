"""Deterministic LibTV-inspired director-scene contract.

The external LibTV material is used as a data contract, not as a second
runtime.  This module normalizes a scene description into the coordinate,
pose, asset and camera shape already understood by the Village Canvas
director-world layer.  It deliberately has no model call and no canvas side
effect, so the capability broker can use it during planning and validation.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping


SCENE_CONTRACT_SCHEMA = "village_canvas.director_scene_contract.v1"
SCENE_CONTRACT_REVISION_PREFIX = "director-scene.v1:"
_LIBRARY_PATH = Path(__file__).with_name("libtv_scene_asset_library.json")


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _text(value: object, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _number(value: object, default: float = 0.0, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool):
        result = default
    else:
        try:
            result = float(value)
        except (TypeError, ValueError):
            result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _vector(value: object, *, defaults: tuple[float, float, float] = (0.0, 0.0, 0.0), minimum: float | None = None, maximum: float | None = None) -> dict[str, float]:
    raw = _mapping(value)
    return {
        axis: _number(raw.get(axis), default, minimum=minimum, maximum=maximum)
        for axis, default in zip(("x", "y", "z"), defaults)
    }


def _bbox(value: object) -> dict[str, float] | None:
    raw = _mapping(value)
    if not raw:
        return None
    result = {
        key: _number(raw.get(key), 0.0, minimum=0.0, maximum=1.0)
        for key in ("x1", "y1", "x2", "y2")
    }
    if result["x1"] >= result["x2"] or result["y1"] >= result["y2"]:
        return None
    return result


def _joint_angles(value: object) -> dict[str, dict[str, float]]:
    raw = _mapping(value)
    result: dict[str, dict[str, float]] = {}
    for joint, fields in (
        ("body", ("bend", "turn", "tilt")),
        ("torso", ("bend", "turn", "tilt")),
        ("head", ("nod", "turn", "tilt")),
        ("l_arm", ("raise", "straddle", "turn")),
        ("r_arm", ("raise", "straddle", "turn")),
        ("l_elbow", ("bend",)),
        ("r_elbow", ("bend",)),
        ("l_leg", ("raise", "straddle", "turn")),
        ("r_leg", ("raise", "straddle", "turn")),
        ("l_knee", ("bend",)),
        ("r_knee", ("bend",)),
    ):
        values = _mapping(raw.get(joint))
        result[joint] = {
            field: _number(values.get(field), 0.0, minimum=-180.0, maximum=180.0)
            for field in fields
        }
    return result


def _clean_character(value: object, index: int) -> dict[str, Any]:
    raw = _mapping(value)
    return {
        "id": _text(raw.get("id") or raw.get("name") or f"character-{index + 1}", 160),
        "label": _text(raw.get("label") or raw.get("name") or f"角色 {index + 1}", 200),
        "bodyType": _text(raw.get("bodyType") or raw.get("body_type") or "mannequin", 80),
        "imageBBox": _bbox(raw.get("imageBBox") or raw.get("image_bbox")),
        "position": _vector(raw.get("position")),
        "rotation": _vector(raw.get("rotation")),
        "scale": _vector(raw.get("scale"), defaults=(1.0, 1.0, 1.0), minimum=0.01, maximum=20.0),
        "jointAngles": _joint_angles(raw.get("jointAngles") or raw.get("joint_angles")),
    }


def _clean_prop(value: object, index: int) -> dict[str, Any]:
    raw = _mapping(value)
    return {
        "assetId": _text(raw.get("assetId") or raw.get("asset_id") or "mesh_cube", 120),
        "label": _text(raw.get("label") or raw.get("name") or f"道具 {index + 1}", 200),
        "position": _vector(raw.get("position")),
        "rotation": _vector(raw.get("rotation")),
        "scale": _vector(raw.get("scale"), defaults=(1.0, 1.0, 1.0), minimum=0.01, maximum=20.0),
    }


def _clean_camera(value: object) -> dict[str, Any]:
    raw = _mapping(value)
    return {
        "position": _vector(raw.get("position"), defaults=(0.0, 1.5, 4.0)),
        "lookAt": _vector(raw.get("lookAt") or raw.get("look_at"), defaults=(0.0, 1.2, 0.0)),
        "fov": _number(raw.get("fov"), 50.0, minimum=15.0, maximum=90.0),
    }


def _library() -> dict[str, Any]:
    try:
        return json.loads(_LIBRARY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _revision(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        {key: payload[key] for key in sorted(payload) if key != "scene_revision"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return SCENE_CONTRACT_REVISION_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def compile_libtv_scene_contract(scene: object = None, *, prompt: object = "", scene_id: object = "") -> dict[str, Any]:
    """Normalize a director-console scene and return an auditable contract."""

    raw = _mapping(scene)
    characters = [_clean_character(item, index) for index, item in enumerate(raw.get("characters") or [])]
    props = [_clean_prop(item, index) for index, item in enumerate(raw.get("props") or [])]
    cameras_raw = raw.get("cameras") or []
    cameras = [_clean_camera(item) for item in cameras_raw if isinstance(item, Mapping)]
    if not cameras:
        cameras = [_clean_camera({})]

    groups: list[dict[str, Any]] = []
    seen_members: set[int] = set()
    for group in raw.get("characterGroups") or raw.get("character_groups") or []:
        item = _mapping(group)
        members: list[int] = []
        for value in item.get("members") or []:
            if isinstance(value, bool):
                continue
            try:
                member = int(value)
            except (TypeError, ValueError):
                continue
            if (
                0 <= member < len(characters)
                and member not in seen_members
                and member not in members
            ):
                members.append(member)
        if len(members) >= 2:
            seen_members.update(members)
            groups.append({"label": _text(item.get("label") or "背景人群", 120), "members": members})

    library = _library()
    body_types = [item.get("id") for item in library.get("bodyTypes", []) if isinstance(item, Mapping)]
    prop_assets = [item.get("id") for item in library.get("propAssets", []) if isinstance(item, Mapping)]
    invalid_body_types = sorted({item["bodyType"] for item in characters if item["bodyType"] not in body_types})
    invalid_props = sorted({item["assetId"] for item in props if item["assetId"] not in prop_assets})
    clean_scene = {
        "sceneId": _text(raw.get("sceneId") or raw.get("scene_id") or scene_id, 200),
        "prompt": _text(raw.get("prompt") or prompt, 4_000),
        "characters": characters,
        "characterGroups": groups,
        "props": props,
        "cameras": cameras,
    }
    validation = {
        "ok": not invalid_body_types and not invalid_props,
        "invalid_body_types": invalid_body_types,
        "invalid_prop_assets": invalid_props,
        "warnings": [
            "characterGroups 中已过滤少于 2 人或重复成员的分组。"
            if (raw.get("characterGroups") or raw.get("character_groups")) and len(groups) != len(raw.get("characterGroups") or raw.get("character_groups") or [])
            else "",
            "未提供角色图像框，后续图像映射阶段仍需补齐 imageBBox。"
            if characters and any(item["imageBBox"] is None for item in characters)
            else "",
        ],
    }
    validation["warnings"] = [item for item in validation["warnings"] if item]
    payload: dict[str, Any] = {
        "schema": SCENE_CONTRACT_SCHEMA,
        "scene": clean_scene,
        "validation": validation,
        "capability_catalog": {
            "body_types": body_types,
            "prop_assets": prop_assets,
            "pose_ids": sorted((_mapping(library.get("poseLibrary")).get("poses") or {}).keys()),
            "coordinate_system": _mapping(_mapping(library.get("_meta")).get("coordinate_system")),
            "camera_spec": _mapping(library.get("cameraSpec")),
        },
        "features": [
            "scene_json",
            "character_groups",
            "joint_angles",
            "image_bbox",
            "camera_fov",
            "grounded_y_up_coordinates",
        ],
        "provenance": {
            "source": "libtv_director_console_contract",
            "source_artifact": "src/novelvideo/director_world/libtv_scene_asset_library.json",
            "source_sha256": hashlib.sha256(_LIBRARY_PATH.read_bytes()).hexdigest() if _LIBRARY_PATH.is_file() else "",
            "side_effect": "none",
        },
    }
    payload["scene_revision"] = _revision(payload)
    return payload


def validate_libtv_scene_contract(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema") != SCENE_CONTRACT_SCHEMA:
        raise ValueError("director scene contract schema is unsupported")
    result = deepcopy(dict(value))
    if not isinstance(result.get("scene"), Mapping) or not isinstance(result.get("validation"), Mapping):
        raise ValueError("director scene contract is incomplete")
    if result.get("scene_revision") != _revision(result):
        raise ValueError("director scene contract revision does not match its contents")
    return result


__all__ = [
    "SCENE_CONTRACT_REVISION_PREFIX",
    "SCENE_CONTRACT_SCHEMA",
    "compile_libtv_scene_contract",
    "validate_libtv_scene_contract",
]
