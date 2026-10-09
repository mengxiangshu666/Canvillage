"""Compile raw story material into a stronger downstream script input."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from novelvideo.freezone.text_node import (
    _direct_or_newapi_text_model,
    _text_model_cache_key,
)

FreezoneTextPrepareMode = Literal["faithful", "creative"]

FREEZONE_TEXT_PREPARE_SYSTEM_PROMPT = """# Freezone Story Input Compiler

You prepare raw creative input for a downstream storyboard script generator.
Your output is a better story source, not a storyboard and not a video prompt.

## Goal
- Preserve the user's core idea, characters, relationships, world and established facts.
- Turn fragments into a coherent story source with clear progression and filmable action.
- Make scene intentions, cause and effect, character choices and physical actions explicit.
- Keep dialogue only where it appears in the source or where the selected mode allows a short bridge.
- Write in the dominant language of the source material.

## Hard boundaries
- Do not write shot numbers, a shot table, camera angles, lens focal lengths, aperture, film stock,
  generation parameters, model names, image prompts or video-motion prompts.
- Do not turn the story into a synopsis-only list. Keep it usable as narrative/screenplay source text.
- Do not silently change names, ages, relationships, identities, time period or major plot outcomes.
- Do not add props, locations, supernatural rules or backstory that contradict the source.
- Do not expose private reasoning. Return only the requested structured result.

## Modes
- `faithful`: preserve every established fact. You may reorganize, connect and specify visible
  actions, but do not invent major plot events. Put missing facts in `unresolved`.
- `creative`: you may add short connective scenes, physical action, atmosphere and simple dialogue
  needed to make the story playable. Do not replace the source's core premise or ending. Record
  meaningful inferred additions in `change_summary`.

## Output contract
- `prepared_text`: the complete cleaned story source, ready to be passed to the script generator.
  Prefer concrete scene paragraphs. Keep scene changes readable with short headings such as
  `场景 1`, but do not use markdown fences.
- `change_summary`: concise list of real improvements or additions, not praise.
- `unresolved`: facts that still need the author's decision.
- `warnings`: contradictions, ambiguity or risks the downstream generator should notice.
"""


class FreezoneTextPrepareResult(BaseModel):
    """Structured result returned by the model."""

    prepared_text: str = Field(description="Cleaned story source for the downstream script generator.")
    change_summary: list[str] = Field(
        default_factory=list,
        description="Concrete changes made while preparing the source.",
    )
    unresolved: list[str] = Field(
        default_factory=list,
        description="Important story facts that remain unspecified.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Contradictions or risks that should remain visible to the author.",
    )


_text_prepare_agents: dict[str, Agent] = {}


def build_freezone_text_prepare_task(
    *,
    text: str,
    mode: FreezoneTextPrepareMode,
) -> str:
    """Build the deterministic task around the user's source material."""

    mode_instruction = {
        "faithful": (
            "Use faithful mode. Preserve all established facts. Do not invent major plot events. "
            "Clarify structure, causality, scene transitions and visible action only."
        ),
        "creative": (
            "Use creative mode. You may add short connective scenes, visible action and simple "
            "dialogue to complete the story, but keep the premise, identities and outcome consistent."
        ),
    }[mode]

    return "\n\n".join(
        (
            "Prepare the following source for a downstream storyboard script generator.",
            mode_instruction,
            "Delivery target:",
            "- A coherent story/screenplay source, not a storyboard table.",
            "- Concrete settings, character intentions, conflicts, turns, actions and dialogue.",
            "- No shot numbers, lens language, camera package, image prompt segments or video prompt.",
            "- Keep the original language unless the source is already mixed.",
            "Source material:",
            text.strip(),
            "Return the structured result now.",
        )
    )


def create_freezone_text_prepare_agent(model: str | None = None) -> Agent:
    """Create the story-input compiler agent."""

    runtime_model, _resolved_id = _direct_or_newapi_text_model(
        kind="text",
        model_ref=model,
        model_env="FREEZONE_TEXT_PREPARE_MODEL",
        default_model="",
    )
    return Agent(
        runtime_model,
        system_prompt=FREEZONE_TEXT_PREPARE_SYSTEM_PROMPT,
        output_type=FreezoneTextPrepareResult,
        output_retries=3,
        name="Freezone Story Input Compiler",
    )


def get_freezone_text_prepare_agent(model: str | None = None) -> Agent:
    """Get the cached story-input compiler agent."""

    cache_key = _text_model_cache_key(
        kind="text",
        model_ref=model,
        default_model="",
    )
    if cache_key not in _text_prepare_agents:
        _text_prepare_agents[cache_key] = create_freezone_text_prepare_agent(model)
    return _text_prepare_agents[cache_key]


async def prepare_freezone_text(
    *,
    text: str,
    mode: FreezoneTextPrepareMode = "faithful",
    model: str | None = None,
) -> dict[str, object]:
    """Compile raw story material and return text plus auditable metadata."""

    source_text = str(text or "").strip()
    if not source_text:
        raise ValueError("text is required")
    if mode not in {"faithful", "creative"}:
        raise ValueError("mode must be faithful or creative")

    task = build_freezone_text_prepare_task(text=source_text, mode=mode)
    response = await get_freezone_text_prepare_agent(model).run(task)
    output = response.output
    prepared_text = str(getattr(output, "prepared_text", "") or "").strip()
    if not prepared_text:
        raise RuntimeError("text prepare returned empty prepared_text")

    return {
        "prepared_text": prepared_text,
        "source_text": source_text,
        "source_hash": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "mode": mode,
        "model": str(model or ""),
        "change_summary": [
            str(item).strip()
            for item in getattr(output, "change_summary", [])
            if str(item).strip()
        ],
        "unresolved": [
            str(item).strip()
            for item in getattr(output, "unresolved", [])
            if str(item).strip()
        ],
        "warnings": [
            str(item).strip()
            for item in getattr(output, "warnings", [])
            if str(item).strip()
        ],
    }


__all__ = [
    "FREEZONE_TEXT_PREPARE_SYSTEM_PROMPT",
    "FreezoneTextPrepareMode",
    "FreezoneTextPrepareResult",
    "build_freezone_text_prepare_task",
    "create_freezone_text_prepare_agent",
    "get_freezone_text_prepare_agent",
    "prepare_freezone_text",
]
