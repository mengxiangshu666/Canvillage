"""Shared context builders for mainline sketch and render generation.

These helpers are used by both the generation API and Freezone bridge flows.
Keeping them below the API layer prevents one route module from importing
another route module solely for reusable business behavior.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novelvideo.config import OUTPUT_DIR
from novelvideo.services.character_ref_service import build_character_map_for_grid
from novelvideo.utils.path_resolver import compute_identity_path, compute_portrait_path


def resolve_render_image_selection(
    project_config: dict,
    requested_selection: str | None = None,
) -> str:
    from novelvideo.config import normalize_explicit_image_generation_selection

    candidate = (
        requested_selection
        if requested_selection is not None
        else project_config.get("render_image_selection")
    )
    # 排队接口只记录显式的 legacy selection。新模型由 worker 从直连
    # 注册表解析；没有任何配置时保留空值，避免在 API 层伪造模型。
    if not str(candidate or "").strip():
        return ""
    return normalize_explicit_image_generation_selection(candidate)


def resolve_sketch_image_selection(
    project_config: dict,
    requested_selection: str | None = None,
) -> str:
    from novelvideo.config import normalize_explicit_image_generation_selection

    candidate = (
        requested_selection
        if requested_selection is not None
        else project_config.get("sketch_image_selection")
    )
    # 排队接口只记录显式的 legacy selection。新模型由 worker 从直连
    # 注册表解析；没有任何配置时保留空值，避免在 API 层伪造模型。
    if not str(candidate or "").strip():
        return ""
    return normalize_explicit_image_generation_selection(candidate)


def resolve_render_bool_setting(
    project_config: dict,
    key: str,
    requested_value: bool | None,
    default: bool,
) -> bool:
    if requested_value is not None:
        return bool(requested_value)
    return bool(project_config.get(key, default))


async def runtime_prop_menu_with_global_props(
    store: Any,
    episode_obj: Any,
    beats: list[dict],
) -> list[dict]:
    """Resolve episode prop markers against global props without mutating beats."""
    from novelvideo.models import build_prop_menu, collect_prop_marker_ids_from_beat

    prop_menu = (
        [item.model_dump() for item in episode_obj.prop_menu] if episode_obj else []
    )
    marked_prop_ids: list[str] = []
    for beat in beats or []:
        for prop_id in collect_prop_marker_ids_from_beat(beat):
            if prop_id and prop_id not in marked_prop_ids:
                marked_prop_ids.append(prop_id)
    if not marked_prop_ids:
        return prop_menu

    existing = {
        item.prop_id: item.model_dump() for item in build_prop_menu(prop_menu=prop_menu)
    }
    changed = False
    for marker_prop_id in marked_prop_ids:
        global_prop = (
            store.get_cached_prop(marker_prop_id)
            if hasattr(store, "get_cached_prop")
            else None
        )
        if not global_prop:
            continue
        item = dict(existing.get(marker_prop_id) or {"prop_id": marker_prop_id})
        item["is_global_asset"] = True
        item["prop_type"] = (
            item.get("prop_type") or getattr(global_prop, "prop_type", "") or "object"
        )
        item["description"] = (
            item.get("description")
            or getattr(global_prop, "description", "")
            or getattr(global_prop, "visual_prompt", "")
            or marker_prop_id
        )
        existing[marker_prop_id] = item
        changed = True
    if not changed:
        return prop_menu

    ordered_ids: list[str] = []
    for item in build_prop_menu(prop_menu=prop_menu):
        if item.prop_id not in ordered_ids:
            ordered_ids.append(item.prop_id)
    for prop_id in marked_prop_ids:
        if prop_id in existing and prop_id not in ordered_ids:
            ordered_ids.append(prop_id)
    return [existing[prop_id] for prop_id in ordered_ids if prop_id in existing]


def episode_from_store_or_none(store: Any, episode_num: int) -> Any | None:
    get_episode = getattr(store, "get_episode", None)
    if get_episode is None:
        return None
    try:
        return get_episode(episode_num)
    except Exception:
        return None


async def build_character_map(
    store: Any,
    beats: list[dict],
    username: str,
    project: str,
    *,
    episode_num: int | None = None,
    use_detected_identities: bool = False,
) -> dict[str, dict]:
    """Build the project character map used by sketch and render generation."""
    user_output_dir = Path(OUTPUT_DIR) / username
    project_dir = user_output_dir / project
    characters = store.get_all_characters()
    char_dicts = []
    for character in characters:
        char_dicts.append(
            {
                "name": character.name,
                "gender": character.gender,
                "body_type": getattr(character, "body_type", ""),
                "role": character.role,
                "is_main": getattr(character, "is_main", False),
                "portrait_path": compute_portrait_path(project_dir, character.name),
                "face_prompt": character.face_prompt,
                "appearance_details": character.appearance_details,
                "identities": (
                    [
                        {
                            "identity_id": identity.identity_id,
                            "identity_name": identity.identity_name,
                            "appearance_details": identity.appearance_details,
                            "face_prompt": identity.face_prompt,
                            "body_type": identity.body_type,
                            "fish_voice_id": identity.fish_voice_id,
                            "age_group": identity.age_group,
                            "portrait_image": identity.portrait_image,
                            "costume_image": identity.costume_image,
                            "primary_reference": compute_identity_path(
                                project_dir, character.name, identity.identity_name
                            ),
                            "character_tag": identity.character_tag,
                            "source": identity.source,
                        }
                        for identity in character.identities
                    ]
                    if character.identities
                    else []
                ),
            }
        )

    sketch_colors = None
    if episode_num:
        sketch_colors = store.get_sketch_colors(episode_num) or None
        if not sketch_colors:
            from novelvideo.generators.episode_optimizer import EpisodeOptimizer

            sketch_colors = (
                EpisodeOptimizer.assign_sketch_colors(char_dicts, episode_beats=beats)
                or None
            )
            if sketch_colors:
                await store.set_sketch_colors(episode_num, sketch_colors)

    return build_character_map_for_grid(
        grid_beats=beats,
        characters=char_dicts,
        user_output_dir=user_output_dir,
        project=project,
        sketch_colors=sketch_colors,
        use_detected_identities=use_detected_identities,
    )


def director_control_scope(episode_num: int, beat_num: int) -> str:
    return (
        f"director_control_to_sketch:ep{int(episode_num):03d}:beat_{int(beat_num):02d}"
    )


__all__ = [
    "build_character_map",
    "director_control_scope",
    "episode_from_store_or_none",
    "resolve_render_bool_setting",
    "resolve_render_image_selection",
    "resolve_sketch_image_selection",
    "runtime_prop_menu_with_global_props",
]
