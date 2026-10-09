"""Shared execution path for episode content rewriting.

The HTTP adapter and the durable project-task runner both use this module so
the existing ``ContentRewriter`` remains the single source of generated text.
"""

from __future__ import annotations

from typing import Any


async def execute_content_rewrite(
    store: Any,
    episode_num: int,
    *,
    target_beats: int = 18,
    beat_chars_min: int = 14,
    beat_chars_max: int = 20,
    narration_style: str = "first_person",
    apply: bool = False,
) -> dict[str, Any]:
    """Generate one episode rewrite and optionally persist it.

    ``apply=False`` is a pure preview: it still runs the real rewriter, but it
    does not mutate SQLite.  The caller can distinguish an empty source with
    ``ContentRewriteInputError`` and map it to its API/task contract.
    """

    raw_content = (await store.load_episode_content(episode_num) or "").strip()
    if not raw_content:
        raise ContentRewriteInputError(
            f"第 {episode_num} 集尚未有原文，请先填写 raw-content"
        )

    await store.load_graph_state()
    episode = store.get_episode(episode_num)
    episode_title = getattr(episode, "title", "") if episode else ""
    protagonist_name = _resolve_narrator_main_name(store)

    from novelvideo.agents.content_rewriter import rewrite_episode_content

    rewritten = await rewrite_episode_content(
        raw_content,
        episode_title=episode_title,
        protagonist_name=protagonist_name,
        target_beats=target_beats,
        beat_chars_range=(beat_chars_min, beat_chars_max),
        narration_style=narration_style or "first_person",
    )
    normalized = str(rewritten or "").strip()
    if normalized == raw_content:
        normalized = ""

    if apply:
        await store.save_adapted_content(episode_num, normalized)
        await store.update_episode(episode_num, beat_source_text=normalized)

    lines = [line for line in normalized.splitlines() if line.strip()]
    return {
        "episode": episode_num,
        "line_count": len(lines),
        "adapted_content": normalized,
        "used_fallback": not bool(normalized),
        "applied": bool(apply),
        "mode": "apply" if apply else "preview",
    }


class ContentRewriteInputError(ValueError):
    """Raised when an episode has no source text to rewrite."""


def _resolve_narrator_main_name(store: Any) -> str:
    for character in store.get_all_characters() or []:
        if getattr(character, "is_main", False):
            return str(getattr(character, "name", "") or "")
    return ""


__all__ = ["ContentRewriteInputError", "execute_content_rewrite"]
