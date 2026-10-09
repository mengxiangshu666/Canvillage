"""Guards for the project work ledger (T-216).

The ledger is the Agent's cross-session memory of a production, so the two
properties that matter most here are: a stage cannot claim completion without
verified artifact evidence, and a superseded artifact is marked rather than
removed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novelvideo.workflow_runtime.definitions import list_workflow_definitions
from novelvideo.workflow_runtime.project_work_ledger import (
    DEFAULT_WORKFLOW_ID,
    LEDGER_SCHEMA,
    apply_event,
    build_project_work_ledger,
    compute_ledger_revision,
    deprecate_artifact,
    ledger_path,
    load_project_work_ledger,
    next_stage,
    record_artifact,
    record_failure,
    render_ledger_briefing,
    save_project_work_ledger,
    set_stage_status,
    stage_chain_for_workflow,
    validate_project_work_ledger,
)


def _ledger(**overrides):
    payload = {
        "project_id": "project-1",
        "goal": "做一支 30 秒实验短片",
        "now": "2026-10-01T00:00:00+00:00",
    }
    payload.update(overrides)
    return build_project_work_ledger(**payload)


def test_stage_chain_is_read_from_the_real_workflow_definitions() -> None:
    expected = next(
        [str(step.id) for step in definition.steps]
        for definition in list_workflow_definitions()
        if definition.id == DEFAULT_WORKFLOW_ID
    )
    assert stage_chain_for_workflow(DEFAULT_WORKFLOW_ID) == expected
    assert [stage["step_id"] for stage in _ledger()["stages"]] == expected
    assert expected[0] == "understand"
    assert expected[-1] == "final_film"


def test_unknown_workflow_is_rejected() -> None:
    with pytest.raises(ValueError):
        build_project_work_ledger(
            project_id="p",
            goal="g",
            workflow_id="not-a-real-workflow",
        )


def test_new_ledger_is_all_pending_and_the_revision_recomputes() -> None:
    ledger = _ledger()
    assert ledger["schema"] == LEDGER_SCHEMA
    assert {stage["status"] for stage in ledger["stages"]} == {"pending"}
    assert ledger["events"] == []
    assert ledger["ledger_revision"] == compute_ledger_revision(ledger)
    assert validate_project_work_ledger(ledger) == ledger


def test_tampered_ledger_fails_validation() -> None:
    ledger = _ledger()
    ledger["goal"] = "悄悄换掉的目标"
    with pytest.raises(ValueError, match="ledger_revision"):
        validate_project_work_ledger(ledger)


def test_a_stage_cannot_be_done_without_verified_evidence() -> None:
    ledger = _ledger()
    with pytest.raises(ValueError, match="verified"):
        set_stage_status(ledger, step_id="understand", status="done")

    pending_evidence = record_artifact(
        ledger,
        step_id="understand",
        kind="report",
        node_key="r-1",
        verified=False,
    )
    with pytest.raises(ValueError, match="verified"):
        set_stage_status(pending_evidence, step_id="understand", status="done")


def test_verified_artifact_unlocks_done_and_appends_an_event() -> None:
    ledger = record_artifact(
        _ledger(),
        step_id="understand",
        kind="report",
        node_key="r-1",
        verified=True,
        now="2026-10-01T00:01:00+00:00",
    )
    ledger = set_stage_status(
        ledger,
        step_id="understand",
        status="done",
        now="2026-10-01T00:02:00+00:00",
    )
    stage = ledger["stages"][0]
    assert stage["status"] == "done"
    assert [event["type"] for event in ledger["events"]] == ["artifact", "stage_status"]
    assert next_stage(ledger) == "script_contract"
    assert validate_project_work_ledger(ledger) == ledger


def test_recording_the_same_node_key_updates_instead_of_duplicating() -> None:
    ledger = record_artifact(
        _ledger(), step_id="storyboard_images", kind="image", node_key="i-1"
    )
    ledger = record_artifact(
        ledger,
        step_id="storyboard_images",
        kind="image",
        node_key="i-1",
        verified=True,
        note="补验后确认产物存在",
    )
    stage = next(
        item for item in ledger["stages"] if item["step_id"] == "storyboard_images"
    )
    assert len(stage["artifacts"]) == 1
    assert stage["artifacts"][0]["verified"] is True
    assert stage["artifacts"][0]["note"] == "补验后确认产物存在"


def test_deprecation_keeps_the_entry_but_revokes_the_done_gate() -> None:
    ledger = record_artifact(
        _ledger(), step_id="shot_videos", kind="video", node_key="v-old", verified=True
    )
    ledger = set_stage_status(ledger, step_id="shot_videos", status="done")

    ledger = deprecate_artifact(
        ledger, node_key="v-old", reason="重试成功后换成新节点"
    )
    stage = next(item for item in ledger["stages"] if item["step_id"] == "shot_videos")
    assert len(stage["artifacts"]) == 1
    assert stage["artifacts"][0]["deprecated"] is True
    assert stage["artifacts"][0]["deprecated_reason"] == "重试成功后换成新节点"

    with pytest.raises(ValueError, match="verified"):
        set_stage_status(ledger, step_id="shot_videos", status="done")


def test_deprecating_an_unknown_node_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="no artifact"):
        deprecate_artifact(_ledger(), node_key="v-missing", reason="x")


def test_failure_lands_on_its_stage_and_counts_the_attempt() -> None:
    ledger = record_failure(
        _ledger(),
        step_id="storyboard_images",
        tool="generate_image_v3",
        error_code="tool_arguments_invalid",
        disposition="fix_params",
        attempt=1,
    )
    stage = next(
        item for item in ledger["stages"] if item["step_id"] == "storyboard_images"
    )
    assert stage["failures"][0]["error_code"] == "tool_arguments_invalid"
    assert stage["failures"][0]["disposition"] == "fix_params"
    assert stage["attempts"] == 1
    assert ledger["events"][-1]["type"] == "failure"


def test_unsupported_event_and_stage_are_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported project work ledger event"):
        apply_event(_ledger(), {"type": "nonsense"})
    with pytest.raises(ValueError, match="no stage"):
        set_stage_status(_ledger(), step_id="script_contract2", status="in_progress")


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    ledger = record_artifact(
        _ledger(), step_id="production_plan", kind="file", node_key="f-1", verified=True
    )
    path = save_project_work_ledger(tmp_path, ledger)
    assert path == ledger_path(tmp_path)
    assert path.is_file()
    assert not path.with_name(path.name + ".tmp").exists()
    assert load_project_work_ledger(tmp_path) == ledger
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == LEDGER_SCHEMA


def test_load_returns_none_when_no_ledger_is_recorded(tmp_path: Path) -> None:
    assert load_project_work_ledger(tmp_path) is None


def test_briefing_names_the_next_stage_and_stays_bounded() -> None:
    ledger = record_artifact(
        _ledger(), step_id="understand", kind="report", node_key="r-9", verified=True
    )
    ledger = set_stage_status(ledger, step_id="understand", status="done")
    briefing = render_ledger_briefing(ledger)
    assert "understand" in briefing
    assert "script_contract" in briefing
    assert "r-9" in briefing
    assert "下一步：script_contract" in briefing
    assert len(render_ledger_briefing(ledger, max_chars=200)) <= 200
