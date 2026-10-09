"""Neutral application facade for Story Lab task execution.

The workflow/task domain should not import Story Lab's Pydantic models or
repository service directly. This facade keeps the existing Story Lab
implementation and returns the stable task-result mapping exposed to the
task system.
"""

from __future__ import annotations

from typing import Any

from novelvideo.project_context import ProjectContext


STORY_LAB_STAGE_NAMES = ("bible", "outline", "draft", "audit")


def normalize_story_lab_stage(value: object) -> str:
    """Return the canonical stage value without exposing the Story Lab enum."""

    from novelvideo.story_lab.models import StoryLabStage

    return StoryLabStage(str(value or "")).value


async def generate_story_lab_stage(
    ctx: ProjectContext,
    stage: object,
    *,
    instructions: str = "",
) -> dict[str, Any]:
    """Run one Story Lab stage and return its serializable task contract."""

    from novelvideo.story_lab.service import (
        generate_story_lab_stage as generate_stage,
    )

    canonical_stage = normalize_story_lab_stage(stage)
    artifact = await generate_stage(
        ctx,
        canonical_stage,
        instructions=instructions,
    )
    return {
        "stage": canonical_stage,
        "revision": artifact.revision,
        "artifact": artifact.model_dump(mode="json"),
    }


__all__ = [
    "STORY_LAB_STAGE_NAMES",
    "generate_story_lab_stage",
    "normalize_story_lab_stage",
]
