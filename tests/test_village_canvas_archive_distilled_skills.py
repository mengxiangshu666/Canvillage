"""Contract tests for the second archive-to-native skill distillation batch."""

from pathlib import Path

from novelvideo.agent_tools.skills import list_agent_skills, load_agent_skill


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "agent_skills"

NEW_SKILLS = {
    "village-canvas-casting-design": (
        "ai-casting-director",
        "character-orthographic-prompt-pro",
        "sss-character-concept-architect",
    ),
    "village-canvas-music-score": ("ai-music-songwriting-expert",),
    "village-canvas-style-lock": (
        "unified-aesthetic-film-generator",
        "style-transfer-extractor",
        "multi-style-video-stylizer",
        "video-style-transfer",
    ),
    "village-canvas-method-distiller": ("explain-how-it-made", "skill-creator"),
}


def test_archive_distilled_skills_are_valid_and_have_distinct_boundaries():
    descriptions: list[str] = []
    for name, sources in NEW_SKILLS.items():
        path = SKILL_ROOT / name / "SKILL.md"
        assert path.is_file(), name
        text = path.read_text(encoding="utf-8")
        assert f"name: {name}" in text
        assert len(text) > 1_500
        assert ".hermes" not in text
        assert "Hermes" not in text
        for source in sources:
            assert source in text
        for field in ("description:", "验收清单", "来源与边界"):
            assert field in text
        description = text.split("description:", 1)[1].splitlines()[0].strip()
        assert description
        assert len(description) <= 320
        descriptions.append(description)

    assert len(descriptions) == len(set(descriptions))


def test_archive_distilled_skills_are_visible_in_the_real_native_catalog():
    discovered = {skill.name: skill for skill in list_agent_skills()}
    assert len(discovered) >= 38
    for name in NEW_SKILLS:
        assert name in discovered
        loaded = load_agent_skill(name)
        assert loaded.name == name
        assert loaded.description
        assert loaded.size_bytes > 1_500
