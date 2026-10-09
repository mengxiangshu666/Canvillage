"""Task-facing facade for Freezone text, prompt and audio-node helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any


async def optimize_freezone_prompt(**kwargs: Any) -> dict[str, Any]:
    from novelvideo.freezone.prompt_optimizer import (
        optimize_freezone_prompt as optimize,
    )

    return await optimize(**kwargs)


def compile_freezone_prompt_strategy(**kwargs: Any) -> dict[str, Any]:
    from novelvideo.freezone.prompt_optimizer import compile_prompt_strategy_contract

    return compile_prompt_strategy_contract(**kwargs).model_dump(mode="json")


async def translate_freezone_text(**kwargs: Any) -> tuple[str, str, str]:
    from novelvideo.freezone.text_node import translate_freezone_text as translate

    return await translate(**kwargs)


async def prepare_freezone_text(**kwargs: Any) -> dict[str, Any]:
    from novelvideo.freezone.text_prepare import prepare_freezone_text as prepare

    return await prepare(**kwargs)


async def generate_freezone_story_script(**kwargs: Any) -> dict[str, Any]:
    from novelvideo.freezone.text_node import generate_freezone_story_script as generate

    return await generate(**kwargs)


async def generate_freezone_story_script_with_vision(**kwargs: Any) -> dict[str, Any]:
    from novelvideo.freezone.text_node import (
        generate_freezone_story_script_with_vision as generate,
    )

    return await generate(**kwargs)


def bind_story_script_assets(
    data: dict[str, Any], *, frame_urls: list[str], character_refs: list[Any], preserve_existing: bool = False, target_indices: list[int] | None = None
) -> None:
    from novelvideo.freezone.text_node import bind_story_script_assets as bind

    bind(data, frame_urls=frame_urls, character_refs=character_refs, preserve_existing=preserve_existing, target_indices=target_indices)


def enforce_story_script_contract(data: dict[str, Any], *, target_index: int | None = None, target_indices: list[int] | None = None) -> dict[str, Any]:
    """把生成出来的脚本表过一遍合同：能机械修的就修，剩下的照实报出来。

    实现放在 `freezone/script_contract.py`（纯函数、可单测）；这里只是任务层入口。
    """

    from novelvideo.freezone.script_contract import (
        enforce_story_script_contract as enforce,
    )

    return enforce(data, target_index=target_index, target_indices=target_indices)


def explicit_script_duration_target(source_text: str) -> float | None:
    from novelvideo.freezone.script_video_duration import explicit_script_duration_target as resolve

    return resolve(source_text)


def script_rows_fingerprint(rows: list[dict[str, Any]]) -> str:
    from novelvideo.freezone.script_contract import script_rows_fingerprint as fingerprint

    return fingerprint(rows)


def append_script_shot_visual_context(prompt: str, row: dict[str, Any]) -> str:
    from novelvideo.freezone.film_prompt_contract import append_script_shot_visual_context as append

    return append(prompt, row)


def get_video_camera_template(template_id: str | None) -> dict[str, str] | None:
    from novelvideo.freezone.video_node import get_video_camera_template as resolve

    return resolve(template_id)


async def rewrite_freezone_story_script_shot(
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from novelvideo.freezone.text_node import (
        generate_freezone_shot_rewrite as rewrite,
    )

    return await rewrite(**kwargs)


def prepare_script_video_feedback(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    from novelvideo.freezone.script_video_feedback import prepare_script_video_feedback as prepare

    return prepare(payload)


def validate_sequence_rewrite_request(**kwargs: Any) -> list[int]:
    from novelvideo.freezone.sequence_rewrite import validate_sequence_rewrite_request as validate

    return validate(**kwargs)


async def rewrite_freezone_story_script_sequence(**kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from novelvideo.freezone.sequence_rewrite import generate_sequence_rewrite

    return await generate_sequence_rewrite(**kwargs)


async def repair_freezone_story_script_issues(
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from novelvideo.freezone.text_node import (
        generate_freezone_script_contract_repair as repair,
    )

    return await repair(**kwargs)


async def reverse_prompt_from_image(**kwargs: Any) -> str:
    from novelvideo.freezone.image_node import reverse_prompt_from_image as reverse

    return await reverse(**kwargs)


def audio_speech_output_path(project_dir: str | Path, job_id: str) -> Path:
    from novelvideo.freezone.audio_node import (
        freezone_audio_speech_output_path as resolve,
    )

    return resolve(Path(project_dir), job_id)


async def generate_freezone_audio_speech(**kwargs: Any) -> Any:
    from novelvideo.freezone.audio_node import (
        generate_freezone_audio_speech as generate,
    )

    return await generate(**kwargs)


async def generate_freezone_audio_eleven_music(**kwargs: Any) -> Any:
    from novelvideo.freezone.audio_node import (
        generate_freezone_audio_eleven_music as generate,
    )

    return await generate(**kwargs)


__all__ = [
    "get_video_camera_template",
    "append_script_shot_visual_context",
    "explicit_script_duration_target",
    "script_rows_fingerprint",
    "audio_speech_output_path",
    "bind_story_script_assets",
    "enforce_story_script_contract",
    "generate_freezone_audio_eleven_music",
    "generate_freezone_audio_speech",
    "generate_freezone_story_script",
    "generate_freezone_story_script_with_vision",
    "optimize_freezone_prompt",
    "prepare_freezone_text",
    "repair_freezone_story_script_issues",
    "reverse_prompt_from_image",
    "rewrite_freezone_story_script_shot",
    "rewrite_freezone_story_script_sequence",
    "validate_sequence_rewrite_request",
    "translate_freezone_text",
]
