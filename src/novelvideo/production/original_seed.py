"""Deterministic seed for the original-production entry mode.

The seed is intentionally small and synchronous in its interpretation: it turns
creative text and selected canvas nodes into the same episode/asset records that
the existing production actions already consume.  No model call and no canvas
write-back happens here.
"""

from __future__ import annotations

import re
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from novelvideo.models import (
    CharacterIdentity,
    NovelCharacter,
    NovelEpisode,
    NovelProp,
    NovelScene,
    NovelVisualBeat,
    PropMenuItem,
    SceneMenuItem,
)
from novelvideo.workflow_runtime.reference_resolver import (
    resolve_video_reference_bindings,
)
from novelvideo.production.emotion_direction import build_emotion_direction


def _text(value: object, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _node_payload(node: Mapping[str, Any]) -> dict[str, Any]:
    data = _mapping(node.get("data"))
    # Canvas node data is the source of truth, while a few imported snapshots
    # keep the useful fields on the node itself.
    merged = dict(node)
    merged.update({key: value for key, value in data.items() if key not in merged})
    return merged


def _node_kind(node: Mapping[str, Any]) -> str:
    payload = _node_payload(node)
    return _text(
        payload.get("role")
        or payload.get("kind")
        or payload.get("nodeType")
        or payload.get("type")
        or "",
        80,
    ).casefold()


def _node_name(node: Mapping[str, Any], fallback: str = "") -> str:
    payload = _node_payload(node)
    return _text(
        payload.get("name")
        or payload.get("title")
        or payload.get("label")
        or payload.get("displayName")
        or fallback,
        200,
    )


def _as_nodes(settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = settings.get("canvas_nodes")
    if not isinstance(raw, list):
        raw = _mapping(settings.get("canvas_snapshot")).get("nodes")
    if not isinstance(raw, list):
        raw = settings.get("selected_canvas_nodes")
    nodes = [dict(item) for item in raw or [] if isinstance(item, Mapping)]
    target_ids = {
        _text(item, 240)
        for item in list(settings.get("target_node_ids") or [])
        if _text(item, 240)
    }
    if target_ids:
        selected = [
            node
            for node in nodes
            if _text(node.get("id"), 240) in target_ids
            or _text(_mapping(node.get("data")).get("id"), 240) in target_ids
        ]
        if not selected:
            raise ValueError("画布选中的节点不存在，无法建立原创单集")
        return selected
    return nodes


def _items(value: object) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, list):
        return [
            item if isinstance(item, Mapping) else {"name": _text(item, 200)}
            for item in value
            if isinstance(item, Mapping) or _text(item, 200)
        ]
    return []


def _unique_text(values: list[object], *, limit: int = 50) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value, 200)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _extract_entities(settings: Mapping[str, Any], nodes: list[dict[str, Any]]) -> tuple[list[dict], list[dict], list[dict]]:
    spec = _mapping(settings.get("output_spec"))
    characters: list[dict] = []
    scenes: list[dict] = []
    props: list[dict] = []

    def add(target: list[dict], item: Mapping[str, Any], fallback: str, role: str) -> None:
        name = _node_name(item, fallback)
        if not name:
            return
        target.append(
            {
                "name": name,
                "description": _text(item.get("description") or item.get("prompt") or item.get("content")),
                "role": _text(item.get("role") or role, 80),
                "aliases": _unique_text(list(item.get("aliases") or [])),
                "owner": _text(item.get("owner") or item.get("owner_identity_id"), 200),
                "visual_prompt": _text(item.get("visual_prompt") or item.get("prompt") or item.get("description")),
                "scene_type": _text(item.get("scene_type") or "interior", 40),
            }
        )

    for item in _items(spec.get("characters")):
        add(characters, item, "", "character")
    for item in _items(spec.get("scenes") or spec.get("locations")):
        add(scenes, item, "", "scene")
    for item in _items(spec.get("props") or spec.get("objects")):
        add(props, item, "", "prop")

    for node in nodes:
        kind = _node_kind(node)
        payload = _node_payload(node)
        if any(token in kind for token in ("character", "角色", "actor", "人物")):
            add(characters, payload, "", "character")
        elif any(token in kind for token in ("scene", "场景", "location", "background")):
            add(scenes, payload, "", "scene")
        elif any(token in kind for token in ("prop", "道具", "object")):
            add(props, payload, "", "prop")

        for item in _items(payload.get("characters")):
            add(characters, item, "", "character")
        for item in _items(payload.get("scenes") or payload.get("locations")):
            add(scenes, item, "", "scene")
        for item in _items(payload.get("props") or payload.get("objects")):
            add(props, item, "", "prop")

    def dedupe(values: list[dict]) -> list[dict]:
        result: list[dict] = []
        seen: set[str] = set()
        for value in values:
            key = value["name"].casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    return dedupe(characters), dedupe(scenes), dedupe(props)


def _extract_beats(settings: Mapping[str, Any], nodes: list[dict[str, Any]], script: str, scene_name: str) -> list[dict[str, Any]]:
    spec = _mapping(settings.get("output_spec"))
    raw_beats = _items(spec.get("beats") or spec.get("shots") or spec.get("storyboard"))
    raw_beats.extend(
        _node_payload(node)
        for node in nodes
        if any(token in _node_kind(node) for token in ("beat", "shot", "分镜", "storyboard", "镜头"))
    )
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw_beats, start=1):
        narration = _text(item.get("narration") or item.get("dialogue") or item.get("script") or item.get("text"))
        visual = _text(item.get("visual_description") or item.get("visual") or item.get("prompt") or item.get("description"))
        if not narration and not visual:
            continue
        result.append(
            {
                "beat_number": int(item.get("beat_number") or item.get("index") or index),
                "narration": narration or visual,
                "visual_description": visual or narration,
                "scene_id": _text(item.get("scene_id") or item.get("scene") or scene_name, 200),
                "speaker": _text(item.get("speaker") or "", 100),
                "audio_type": _text(item.get("audio_type") or "narration", 30),
                "duration_seconds": item.get("duration_seconds"),
            }
        )
    if not result:
        text = script or _text(settings.get("goal")) or "原创短片开场"
        chunks = [part.strip() for part in re.split(r"[。！？!?\n]+", text) if part.strip()]
        for index, chunk in enumerate(chunks[:12] or [text], start=1):
            result.append(
                {
                    "beat_number": index,
                    "narration": chunk,
                    "visual_description": chunk,
                    "scene_id": scene_name,
                    "speaker": "",
                    "audio_type": "narration",
                    "duration_seconds": None,
                }
            )
    return result


def structure_original_seed(settings: Mapping[str, Any], project_dir: str | Path) -> dict[str, Any]:
    """Build an ep000 payload and resolve only explicitly declared references."""

    nodes = _as_nodes(settings)
    spec = _mapping(settings.get("output_spec"))
    script = _text(settings.get("original_script") or spec.get("script") or settings.get("goal"), 12000)
    characters, scenes, props = _extract_entities(settings, nodes)
    if not characters:
        characters = [{"name": "主角", "description": script[:300], "role": "character", "aliases": [], "owner": "", "visual_prompt": script[:300], "scene_type": ""}]
    if not scenes:
        scenes = [{"name": "默认场景", "description": "原创短片默认场景", "role": "scene", "aliases": [], "owner": "", "visual_prompt": "", "scene_type": "interior"}]
    beats = _extract_beats(settings, nodes, script, scenes[0]["name"])
    emotion_direction = build_emotion_direction(
        project_goal=script or settings.get("goal"),
        output_spec=spec,
        clarification_answers=settings.get("director_clarification_answers"),
    )

    resolver_data = dict(spec)
    for key in ("referenceItems", "reference_items", "referenceBindings", "reference_bindings", "firstFramePath", "lastFramePath"):
        if key not in resolver_data and key in settings:
            resolver_data[key] = settings[key]
    declared_bindings = dict(resolver_data.get("referenceBindings") or resolver_data.get("reference_bindings") or {})
    for node in nodes:
        payload = _node_payload(node)
        kind = _node_kind(node)
        asset_id = _text(payload.get("assetId") or payload.get("asset_id"), 500)
        if not asset_id:
            continue
        role = (
            "character"
            if any(token in kind for token in ("character", "角色", "actor", "人物"))
            else "scene"
            if any(token in kind for token in ("scene", "场景", "location", "background"))
            else "prop"
            if any(token in kind for token in ("prop", "道具", "object"))
            else ""
        )
        if role:
            values = declared_bindings.get(role)
            declared_bindings[role] = [*(values if isinstance(values, list) else [values] if values else []), asset_id]
    if declared_bindings:
        resolver_data["referenceBindings"] = declared_bindings
    resolution = resolve_video_reference_bindings(
        project_dir=project_dir,
        data=resolver_data,
        snapshot={"nodes": nodes},
    )
    return {
        "episode": {
            "number": 0,
            "title": _text(spec.get("title") or "原创单集", 200),
            "raw_content": script,
            "beat_source_text": script,
            "content_summary": script[:500],
            "main_conflict": _text(spec.get("main_conflict") or "", 500),
            "character_names": [item["name"] for item in characters],
            "scene_menu": [{"scene_id": item["name"]} for item in scenes],
            "prop_menu": [{"prop_id": item["name"], "prop_type": "object", "description": item.get("description", ""), "visual_prompt": item.get("visual_prompt", "")} for item in props],
        },
        "characters": characters,
        "scenes": scenes,
        "props": props,
        "beats": beats,
        "resolved_references": resolution,
        "emotion_direction": emotion_direction,
        "selected_node_ids": [_text(node.get("id"), 240) for node in nodes if _text(node.get("id"), 240)],
    }


async def seed_original_episode(ctx: Any, settings: Mapping[str, Any]) -> dict[str, Any]:
    """Persist the deterministic seed into the project SQLite store."""

    from novelvideo.sqlite_store import SQLiteStore

    payload = structure_original_seed(settings, ctx.output_dir)
    store = SQLiteStore(
        ctx.owner_project_label,
        output_dir=ctx.output_dir,
        state_dir=ctx.state_dir,
    )
    await store.initialize()
    try:
        # Keep the two entry namespaces isolated: an original retry replaces
        # only its idempotent ep000 checkpoint and never deletes novel episodes.
        for item in payload["characters"]:
            character = NovelCharacter(
                name=item["name"],
                aliases=item.get("aliases") or [],
                role=item.get("role") or "character",
                is_main=True,
                description=item.get("description") or "",
                appearance_details=item.get("description") or "",
                identities=[
                    CharacterIdentity(
                        identity_id=f"{item['name']}_默认",
                        character_name=item["name"],
                        identity_name="默认",
                        character_tag=f"[{re.sub(r'[^A-Za-z0-9一-龥]', '', item['name'])[:24] or 'CHAR'}]",
                        appearance_details=item.get("description") or "",
                        source="user_created",
                    )
                ],
            )
            await store.add_character(character)
        for item in payload["scenes"]:
            await store.add_scene(
                NovelScene(
                    name=item["name"],
                    aliases=item.get("aliases") or [],
                    scene_type=item.get("scene_type") or "interior",
                    environment_prompt=item.get("visual_prompt") or item.get("description") or "",
                    description=item.get("description") or "",
                )
            )
        for item in payload["props"]:
            await store.add_prop(
                NovelProp(
                    name=item["name"],
                    aliases=item.get("aliases") or [],
                    visual_prompt=item.get("visual_prompt") or "",
                    description=item.get("description") or "",
                    owner=item.get("owner") or "",
                )
            )
        identity_ids = [f"{item['name']}_默认" for item in payload["characters"]]
        episode_data = dict(payload["episode"])
        episode_data["identity_ids"] = identity_ids
        episode_data["scene_menu_json"] = json.dumps(
            [SceneMenuItem(**item).model_dump() for item in episode_data.pop("scene_menu")], ensure_ascii=False
        )
        episode_data["prop_menu_json"] = json.dumps(
            [PropMenuItem(**item).model_dump() for item in episode_data.pop("prop_menu")], ensure_ascii=False
        )
        await store.add_episode(NovelEpisode(**episode_data))
        await store.delete_beats_for_episode(0)
        await store.add_visual_beats(
            [
                NovelVisualBeat(
                    episode_number=0,
                    beat_number=int(item["beat_number"]),
                    narration=item["narration"],
                    visual_description=item["visual_description"],
                    audio_type=item.get("audio_type") or "narration",
                    speaker=item.get("speaker") or "",
                    scene_ref_json=json.dumps({"scene_id": item["scene_id"]}, ensure_ascii=False),
                    duration_seconds=item.get("duration_seconds"),
                )
                for item in payload["beats"]
            ]
        )
    finally:
        await store.close()
    return {
        "episode": 0,
        "characters": [item["name"] for item in payload["characters"]],
        "scenes": [item["name"] for item in payload["scenes"]],
        "props": [item["name"] for item in payload["props"]],
        "beat_count": len(payload["beats"]),
        "resolved_references": payload["resolved_references"],
        "emotion_direction": payload["emotion_direction"],
        "selected_node_ids": payload["selected_node_ids"],
    }


__all__ = ["seed_original_episode", "structure_original_seed"]
