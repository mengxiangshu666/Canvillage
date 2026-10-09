from novelvideo.research.provenance import (
    GROWTH_PROVENANCE_SCHEMA,
    KNOWLEDGE_PROVENANCE_SCHEMA,
    build_growth_provenance,
    build_knowledge_provenance,
    validate_growth_provenance,
    validate_knowledge_provenance,
)


def test_external_knowledge_provenance_is_content_addressed_and_validated():
    provenance = build_knowledge_provenance(
        source="https://example.invalid/repo",
        source_commit="abc123",
        license_name="MIT",
        content={"recipe_id": "recipe-1", "rule": "one primary motion"},
    )

    assert provenance["schema"] == KNOWLEDGE_PROVENANCE_SCHEMA
    assert len(provenance["content_sha256"]) == 64
    assert validate_knowledge_provenance(provenance) == []
    assert validate_knowledge_provenance({**provenance, "source_commit": ""})


def test_growth_provenance_is_separate_from_external_license_metadata():
    provenance = build_growth_provenance(
        event_id=17,
        project="project-a",
        turn_id="turn-1",
        conversation_id="conversation-a",
        content="按时序组织连续动作并核对终点",
    )

    assert provenance["schema"] == GROWTH_PROVENANCE_SCHEMA
    assert provenance["kind"] == "growth_distillation_event"
    assert "license" not in provenance
    assert validate_growth_provenance(provenance) == []
    assert validate_growth_provenance({**provenance, "kind": "external_repository"})
