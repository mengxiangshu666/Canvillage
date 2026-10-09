from __future__ import annotations

import pytest

from novelvideo.production.director_intent import build_director_intent_contract
from novelvideo.production.project_dna import (
    build_project_dna,
    compute_dna_revision,
    validate_project_dna,
)


def test_project_dna_is_project_scoped_and_versioned() -> None:
    intent = build_director_intent_contract(
        project_goal="做一部冷峻的雨夜连载短片",
        asset_plan={
            "characters": [{"id": "hero"}],
            "locations": [{"id": "temple"}],
            "props": [{"id": "blade"}],
        },
    )
    dna = build_project_dna(
        project_id="project-a",
        intent_contract=intent,
        explicit={
            "positive_preferences": ["冷色弱光", "动作真实"],
            "vetoes": ["不要塑料 CG 感"],
            "locked_rules": ["角色服装不可随机变化"],
        },
    )

    assert dna["schema"] == "project_dna.v1"
    assert dna["project_id"] == "project-a"
    assert dna["character_ids"] == ["hero"]
    assert dna["vetoes"] == ["不要塑料 CG 感"]
    assert dna["dna_revision"] == compute_dna_revision(dna)
    assert validate_project_dna(dna) == dna


def test_project_dna_revision_rejects_mutation() -> None:
    dna = build_project_dna(project_id="project-a")
    changed = dict(dna)
    changed["vetoes"] = ["不要随机换装"]
    with pytest.raises(ValueError, match="dna_revision"):
        validate_project_dna(changed)
