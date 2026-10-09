"""Wiring tests: the combat gates must actually be requested at the decision point.

``build_cinematic_review`` has two return paths and both must carry the gates:

* the early return taken when the run carries no ``film_production`` contract
  (the only path a one-click-film run ever takes today), and
* the normal path that merges a validated film-production contract.

Wiring only one of them silently drops the gates for the other.  A gate also
only blocks when its name reaches ``required_gates``; writing
``gate_observations`` alone is measured as non-blocking.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novelvideo.production.director_plan import build_director_plan
from novelvideo.workflow_runtime import executor
from novelvideo.workflow_runtime.cinematic_review import build_cinematic_review

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "combat_film_30s_1v1.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _low_tier_intent() -> dict:
    """The tier ``infer_delivery_level`` actually assigns to a fight sentence."""

    return {
        "delivery_level": "idea",
        "quality_gates": [
            "director_plan_valid",
            "canvas_structure_receipt",
            "story_and_shots_complete",
        ],
    }


def _run(*, shots: list[dict], request: str, run_mode: str = "draft") -> dict:
    intent = _low_tier_intent()
    plan = build_director_plan(objective=request, director_intent_contract=intent)
    return {
        "run_mode": run_mode,
        "contract_version": 1,
        "inputs": {
            "request": request,
            "director_mode": "production",
            "director_plan": plan,
            "director_intent_contract": intent,
        },
        "artifacts": {
            "story_and_shots": {
                "canvas_receipt": {
                    "created_node_ids": [shot["shot_id"] for shot in shots]
                },
                "plan": {"shots": shots},
            },
            "media_generation": {"started": False},
        },
    }


def test_review_early_return_path_carries_the_combat_gates() -> None:
    """No film_production contract in the run: the early return must not drop them."""

    fixture = _fixture()
    review = build_cinematic_review(
        run={"inputs": {"request": fixture["request"]}, "artifacts": {}},
        shots=fixture["shots"],
        director_plan={},
    )

    assert "film_production_contract" not in review
    assert review["media_blocked"] is False
    assert review["required_gates"] == [
        "combat_film_structure_ready",
        "combat_film_declarations_complete",
    ]
    assert review["gate_observations"]["combat_film_structure_ready"] is True
    assert review["gate_observations"]["combat_film_declarations_complete"] is True


def test_review_normal_path_also_carries_the_combat_gates() -> None:
    """A validated film-production contract must not suppress the combat gates."""

    from tests.test_film_production_contracts import _ready_contract

    fixture = _fixture()
    review = build_cinematic_review(
        run={
            "inputs": {
                "request": fixture["request"],
                "film_production": _ready_contract(ready=True),
            }
        },
        shots=fixture["shots"],
        director_plan={},
    )

    assert review["film_production_audit"]["valid"] is True
    assert "combat_film_structure_ready" in review["required_gates"]
    assert "combat_film_declarations_complete" in review["required_gates"]
    assert review["gate_observations"]["combat_film_declarations_complete"] is True


def test_review_does_not_block_paid_media_for_combat_alone() -> None:
    """Judging declarations must not become a paid-media gate."""

    fixture = _fixture()
    for shot in fixture["shots"]:
        shot["cinematic"].pop("combat")
    review = build_cinematic_review(
        run={"inputs": {"request": fixture["request"]}, "artifacts": {}},
        shots=fixture["shots"],
        director_plan={},
    )

    assert review["gate_observations"]["combat_film_declarations_complete"] is False
    assert review["media_blocked"] is False


def test_non_combat_run_is_left_completely_untouched() -> None:
    fixture = _fixture()
    for shot in fixture["shots"]:
        shot["cinematic"].pop("combat")
    review = build_cinematic_review(
        run={
            "inputs": {"request": "一个女孩在雨夜街头缓慢回头"},
            "artifacts": {},
        },
        shots=fixture["shots"],
        director_plan={},
    )

    assert review["required_gates"] == []
    assert "combat_film_contract" not in review
    assert not [key for key in review["gate_observations"] if key.startswith("combat")]


@pytest.mark.asyncio
async def test_quality_review_requests_and_passes_the_combat_gates() -> None:
    fixture = _fixture()
    result = await executor._quality_review_handler(
        _run(shots=fixture["shots"], request=fixture["request"]),
        {"id": "quality_review"},
    )

    report = result.payload["quality_gate_report"]
    assert "combat_film_structure_ready" in report["requested_gates"]
    assert "combat_film_declarations_complete" in report["requested_gates"]
    assert report["gate_statuses"]["combat_film_structure_ready"] == "passed"
    assert report["gate_statuses"]["combat_film_declarations_complete"] == "passed"
    assert report["blocking_gates"] == []


@pytest.mark.asyncio
async def test_quality_review_blocks_a_broken_causality_chain() -> None:
    fixture = _fixture()
    fixture["shots"][2]["cinematic"]["combat"]["causality_chain"] = [
        "contact",
        "compression",
        "failure",
    ]

    with pytest.raises(executor.WorkflowStepExecutionError) as raised:
        await executor._quality_review_handler(
            _run(shots=fixture["shots"], request=fixture["request"]),
            {"id": "quality_review"},
        )

    assert raised.value.code == "workflow_quality_gates_failed"
    report = raised.value.details["quality_gate_report"]
    assert "combat_film_declarations_complete" in report["blocking_gates"]
    assert report["gate_statuses"]["combat_film_declarations_complete"] == "failed"

    # The blocking gate is backed by a per-shot issue, not a bare boolean.
    review = build_cinematic_review(
        run={"inputs": {"request": fixture["request"]}, "artifacts": {}},
        shots=fixture["shots"],
        director_plan={},
    )
    issues = [
        issue
        for issue in review["combat_issues"]
        if issue["code"] == "combat.declarations.causality_chain_invalid"
    ]
    assert issues and issues[0]["shot_id"] == "S03"


@pytest.mark.asyncio
async def test_quality_review_blocks_a_damage_regression() -> None:
    fixture = _fixture()
    fixture["shots"][4]["cinematic"]["combat"]["damage_state"] = "intact"

    with pytest.raises(executor.WorkflowStepExecutionError) as raised:
        await executor._quality_review_handler(
            _run(shots=fixture["shots"], request=fixture["request"]),
            {"id": "quality_review"},
        )

    report = raised.value.details["quality_gate_report"]
    assert "combat_film_declarations_complete" in report["blocking_gates"]

    review = build_cinematic_review(
        run={"inputs": {"request": fixture["request"]}, "artifacts": {}},
        shots=fixture["shots"],
        director_plan={},
    )
    codes = {issue["code"] for issue in review["combat_issues"]}
    assert "combat.damage.regressed" in codes


@pytest.mark.asyncio
async def test_quality_review_blocks_in_auto_mode_too() -> None:
    fixture = _fixture()
    for shot in fixture["shots"]:
        shot["cinematic"].pop("combat")

    with pytest.raises(executor.WorkflowStepExecutionError) as raised:
        await executor._quality_review_handler(
            _run(shots=fixture["shots"], request=fixture["request"], run_mode="auto"),
            {"id": "quality_review"},
        )

    report = raised.value.details["quality_gate_report"]
    assert report["strict"] is True
    assert "combat_film_declarations_complete" in report["blocking_gates"]
