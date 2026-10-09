"""Contract tests for the distilled upstream engineering governance skill."""

from pathlib import Path

from novelvideo.agent_tools.skills import list_agent_skills


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = (
    ROOT
    / "agent_skills"
    / "village-canvas-engineering-governance"
    / "SKILL.md"
)


def test_governance_skill_is_repo_pinned_and_contains_operational_contract():
    text = SKILL_PATH.read_text(encoding="utf-8")

    assert "name: village-canvas-engineering-governance" in text
    assert "974d940a1c5344210874150b98ff0d2c861fab6a" in text
    assert "e04ea0b9cc8248686edf5ac751cadff550e162b8" in text
    for marker in (
        "Observe",
        "Plan",
        "Dry Run",
        "Act",
        "Wait",
        "Verify",
        "Writeback",
        "contract-first",
        "agent_specialist_result.v1",
        "revision",
        "server_applied",
        "WorkflowRun",
        "context_budget.py",
        "memory_compiler.py",
    ):
        assert marker in text


def test_governance_skill_is_discoverable_from_the_native_catalog():
    assert SKILL_PATH.is_file()
    discovered = {skill.name for skill in list_agent_skills()}
    assert "village-canvas-engineering-governance" in discovered


def test_governance_skill_does_not_add_an_external_runtime_dependency():
    text = SKILL_PATH.read_text(encoding="utf-8").lower()

    assert "npm install" not in text
    assert "pip install" not in text
    assert "ecc memory" not in text
    assert "ponytail plugins" not in text
    assert "第二个 agent" in text
    assert "第二套 memory vault" in text
    assert "控制面" in text
