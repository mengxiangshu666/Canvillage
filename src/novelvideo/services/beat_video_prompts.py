"""Application service for generating and persisting Beat video prompts.

The service owns the use case so task runners do not import HTTP route
modules. The route layer can still inject its compatibility helpers, which
keeps existing API tests and extension points intact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from novelvideo.models import sync_beat_asset_refs


def _first_existing_path(*paths: Path) -> str:
    for path in paths:
        if path.exists():
            return str(path)
    return ""


def _video_prompt_input_path(paths: Any, beat_num: int, beat: object = None) -> str:
    """Resolve the image shown to the single-Beat video prompt optimizer.

    Video dispatch uses ``PathResolver.first_frame_for_video`` (including a
    matching derived ``video_inputs`` override and the continuous-seam
    tail-frame relay), so prompt generation must use the same source.  Existing
    projects that only have sketches remain supported as a compatibility
    fallback.
    """
    from novelvideo.production.shot_contract import SEAM_CONTINUOUS, shot_incoming_seam

    first_frame_for_video = getattr(paths, "first_frame_for_video", None)
    if callable(first_frame_for_video):
        video_input = first_frame_for_video(
            beat_num,
            prefer_relay=shot_incoming_seam(beat) == SEAM_CONTINUOUS,
        )
        if video_input.exists():
            return str(video_input)
    return _first_existing_path(paths.frame(beat_num), paths.sketch(beat_num))


async def generate_single_beat_video_prompt(
    *,
    store: Any | None = None,
    output_dir: str | Path,
    project_name: str = "",
    episode: int,
    beat: dict[str, Any],
    all_beats: list[dict[str, Any]] | None = None,
    prev_beat: dict[str, Any] | None = None,
    next_beat: dict[str, Any] | None = None,
    language: str = "en",
) -> str:
    """Generate one first-frame motion prompt using the global optimizer."""
    from novelvideo.agents.global_video_optimizer import (
        _build_color_appearance_map,
        get_global_video_optimizer,
    )
    from novelvideo.utils.path_resolver import PathResolver

    beat_num = int(beat.get("beat_number") or 0)
    paths = PathResolver(str(output_dir), episode)
    sketch_image_path = _video_prompt_input_path(paths, beat_num, beat)
    if not sketch_image_path:
        raise ValueError(f"Beat {beat_num} 缺少草图或首帧，请先生成草图或预览")

    beats = list(all_beats or [beat])
    characters = []
    if store is not None and hasattr(store, "get_all_characters"):
        characters = [
            c.model_dump() if hasattr(c, "model_dump") else dict(c)
            for c in (store.get_all_characters() or [])
        ]

    character_color_map = _build_color_appearance_map(
        beats,
        characters,
        str(output_dir),
        project_name,
        episode=episode,
        cognee_store=store,
    )
    result = await get_global_video_optimizer().optimize_single_beat(
        beat=beat,
        sketch_image_path=sketch_image_path,
        character_color_map=character_color_map,
        language=language,
        prev_beat=prev_beat,
        next_beat=next_beat,
        prev_prompt=None,
        total_beats=len(beats),
    )
    return str(result.get("prompt") or "").strip()


async def generate_single_beat_keyframe_prompt(
    *,
    output_dir: str | Path,
    episode: int,
    beat: dict[str, Any],
    next_beat: dict[str, Any],
    language: str = "en",
) -> str:
    """Generate one first/last-frame transition prompt."""
    from novelvideo.agents.keyframe_prompt_builder import get_keyframe_prompt_builder
    from novelvideo.utils.path_resolver import PathResolver

    beat_num = int(beat.get("beat_number") or 0)
    next_beat_num = int(next_beat.get("beat_number") or beat_num + 1)
    paths = PathResolver(str(output_dir), episode)
    first_frame_path = _first_existing_path(paths.frame(beat_num), paths.sketch(beat_num))
    last_frame_path = _first_existing_path(
        paths.frame(next_beat_num), paths.sketch(next_beat_num)
    )
    if not first_frame_path:
        raise ValueError(f"Beat {beat_num} 缺少首帧或草图，请先生成预览或草图")
    if not last_frame_path:
        raise ValueError(f"Beat {next_beat_num} 缺少首帧或草图，请先生成预览或草图")

    from novelvideo.seedance2_i2v.voice_clone import (
        normalize_seedance2_audio_type,
        resolve_beat_dialogue,
    )

    audio_type = normalize_seedance2_audio_type(beat)
    narration = str(beat.get("narration_segment") or "")
    next_narration = str(next_beat.get("narration_segment") or "")
    # Dialogue is consumed by the audio/subtitle chain. The visual prompt
    # builder receives only a performance instruction, never the line itself.
    narration_text = (
        "独立对白字段；只描述可见口型、下颌和手势表演。"
        if audio_type == "dialogue"
        else narration
    )

    return await get_keyframe_prompt_builder().build(
        first_frame_path=first_frame_path,
        last_frame_path=last_frame_path,
        narration=narration_text,
        next_narration=next_narration,
        language=language,
        visual_description=str(beat.get("visual_description") or ""),
        next_visual_description=str(next_beat.get("visual_description") or ""),
        audio_type=audio_type,
        dialogue_line=resolve_beat_dialogue(beat) or "",
    )


async def resolve_beat_video_prompt_target(
    *,
    store: Any,
    episode_num: int,
    beat_num: int,
) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
    script_data = await store.get_script_as_dict(episode_num)
    if not script_data:
        raise LookupError("Script not found")

    beats = list(script_data.get("beats") or [])
    target = next((beat for beat in beats if int(beat.get("beat_number") or 0) == beat_num), None)
    if target is None:
        raise LookupError(f"Beat {beat_num} not found")

    video_mode = str(target.get("video_mode") or "first_frame")
    next_beat = next(
        (beat for beat in beats if int(beat.get("beat_number") or 0) == beat_num + 1),
        None,
    )
    field = "keyframe_prompt" if video_mode == "keyframe" else "video_prompt"
    if field == "keyframe_prompt" and next_beat is None:
        raise ValueError("这是最后一个 Beat，无法生成首尾帧过渡提示词")
    return target, next_beat, field


PromptGenerator = Callable[..., Awaitable[str]]


async def generate_and_save_beat_video_prompt(
    *,
    store: Any,
    output_dir: str | Path,
    project_name: str = "",
    episode_num: int,
    beat_num: int,
    language: str,
    video_prompt_generator: PromptGenerator | None = None,
    keyframe_prompt_generator: PromptGenerator | None = None,
) -> dict[str, Any]:
    """Generate the selected Beat prompt, persist it, and return its receipt."""
    script_data = await store.get_script_as_dict(episode_num)
    if not script_data:
        raise LookupError("Script not found")

    beats = list(script_data.get("beats") or [])
    target, next_beat, field = await resolve_beat_video_prompt_target(
        store=store,
        episode_num=episode_num,
        beat_num=beat_num,
    )
    prev_beat = next(
        (beat for beat in beats if int(beat.get("beat_number") or 0) == beat_num - 1),
        None,
    )

    video_generator = video_prompt_generator or generate_single_beat_video_prompt
    keyframe_generator = keyframe_prompt_generator or generate_single_beat_keyframe_prompt
    if field == "keyframe_prompt":
        prompt = await keyframe_generator(
            output_dir=output_dir,
            episode=episode_num,
            beat=target,
            next_beat=next_beat,
            language=language,
        )
    else:
        prompt = await video_generator(
            store=store,
            output_dir=output_dir,
            project_name=project_name,
            episode=episode_num,
            beat=target,
            all_beats=beats,
            prev_beat=prev_beat,
            next_beat=next_beat,
            language=language,
        )

    target[field] = prompt
    sync_beat_asset_refs(target)
    saved = await store.update_beat_asset(
        episode_number=episode_num,
        beat_number=beat_num,
        **{field: prompt},
    )
    if not saved:
        raise RuntimeError(f"Beat {beat_num} was not updated")

    return {"beat": target, "field": field, "prompt": prompt}


__all__ = [
    "generate_and_save_beat_video_prompt",
    "generate_single_beat_keyframe_prompt",
    "generate_single_beat_video_prompt",
    "resolve_beat_video_prompt_target",
]
