"""Script generator must emit identity-board-ready character cards."""

from __future__ import annotations

from novelvideo.freezone.text_node import FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT


def test_script_prompt_requires_structured_character_cards() -> None:
    prompt = FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT

    assert "one-click identity-board assets" in prompt
    assert "9-head/head-to-body ratio" in prompt
    assert "face shape" in prompt
    assert "outer garment" in prompt
    assert "permanent traces" in prompt
    assert "byte-identical" in prompt
    assert "temporary clothing" in prompt


def test_script_prompt_has_pre_output_coverage_and_continuity_checks() -> None:
    prompt = FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT

    assert "Source coverage" in prompt
    assert "Asset completeness" in prompt
    assert "State chain" in prompt
    assert "Continuity: screen direction" in prompt
    assert "Prompt integrity" in prompt
    assert "Coverage over quota" in prompt
