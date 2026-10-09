from __future__ import annotations

import pytest

from novelvideo.production.cost_receipt import (
    PRODUCTION_COST_RECEIPT_SCHEMA,
    build_production_cost_receipt,
    finalize_production_cost_receipt,
    project_production_cost_receipt,
    summarize_production_cost_receipts,
)


@pytest.mark.parametrize(
    ("task_type", "expected_kind"),
    [
        ("freezone_gen", "image"),
        ("freezone_edit", "image"),
        ("freezone_mask_edit", "image"),
        ("mainline_sketch_from_context", "image"),
        ("mainline_frame_from_context", "image"),
        ("freezone_video_gen", "video"),
        ("freezone_video_compose", "video"),
        ("freezone_audio_speech", "audio"),
        ("freezone_audio_eleven_music", "audio"),
        ("freezone_audio_separate", "audio"),
        ("freezone_story_script", "text"),
        ("freezone_image_reverse_prompt", "text"),
        ("stage_asset", "unknown"),
        ("future_unknown_task", "unknown"),
    ],
)
def test_cost_receipt_media_kind_matches_task_output_contract(
    task_type: str,
    expected_kind: str,
) -> None:
    receipt = build_production_cost_receipt(
        {"project_id": "project-1", "task_type": task_type},
        task_id="task-1",
    )

    assert receipt["media_kind"] == expected_kind


def test_cost_receipt_explicit_media_kind_overrides_task_fallback() -> None:
    receipt = build_production_cost_receipt(
        {"project_id": "project-1", "task_type": "freezone_gen"},
        task_id="task-1",
        metadata={"media_kind": "image"},
    )

    assert receipt["media_kind"] == "image"


def test_cost_receipt_is_bounded_and_credential_free() -> None:
    receipt = build_production_cost_receipt(
        {
            "project_id": "project-1",
            "task_type": "freezone_video_gen",
            "model_id": "video-model",
            "prompt": "secret prompt must not be copied",
        },
        task_id="task-1",
        metadata={
            "estimated_cost": {"credits": 4, "usd": 0.02, "prompt": "no"},
            "reserved_cost": {"credits": 4},
            "api_key": "secret",
        },
        now="2026-09-02T00:00:00Z",
    )

    assert receipt["schema"] == PRODUCTION_COST_RECEIPT_SCHEMA
    assert receipt["media_kind"] == "video"
    assert receipt["estimated_cost"] == {"credits": 4, "usd": 0.02}
    assert "prompt" not in receipt
    assert "api_key" not in receipt


def test_failed_cost_receipt_marks_reserved_cost_as_wasted() -> None:
    initial = build_production_cost_receipt(
        {"project_id": "p", "task_type": "identity_image"},
        task_id="t",
        metadata={"reserved_cost": {"credits": 2}},
        now="2026-09-02T00:00:00Z",
    )
    closed = finalize_production_cost_receipt(
        initial,
        status="failed",
        now="2026-09-02T00:00:01Z",
    )

    assert closed["result_status"] == "failed"
    assert closed["duration_ms"] == 1000
    assert closed["wasted_cost"] == {"credits": 2}


def test_cost_receipt_projection_is_stable_for_cross_layer_consumers() -> None:
    projected = project_production_cost_receipt(
        {
            "schema": PRODUCTION_COST_RECEIPT_SCHEMA,
            "task_id": "task-1",
            "provider_task_id": "provider-1",
            "actual_cost": {"credits": 3, "secret": "drop"},
            "asset_ids": ["asset-1", "asset-1"],
            "prompt": "drop",
        }
    )

    assert projected == {
        "schema": PRODUCTION_COST_RECEIPT_SCHEMA,
        "task_id": "task-1",
        "provider_task_id": "provider-1",
        "actual_cost": {"credits": 3},
        "asset_ids": ["asset-1"],
    }


@pytest.mark.parametrize(
    ("result", "expected_cost", "expected_source"),
    [
        (
            {"usage": {"cost": {"credits": 2}}},
            {"credits": 2},
            "result.usage.cost",
        ),
        (
            {"billing": {"total_cost": 0.25}},
            {"total_cost": 0.25},
            "result.billing.total_cost",
        ),
        (
            {"cost": {"usd": 1.5}},
            {"usd": 1.5},
            "result.cost",
        ),
    ],
)
def test_finalize_uses_explicit_provider_cost_evidence(
    result: dict[str, object],
    expected_cost: dict[str, int | float],
    expected_source: str,
) -> None:
    initial = build_production_cost_receipt(
        {"project_id": "p", "task_type": "freezone_video_gen"},
        task_id="t",
        now="2026-09-02T00:00:00Z",
    )

    closed = finalize_production_cost_receipt(
        initial,
        status="completed",
        result=result,
        now="2026-09-02T00:00:01Z",
    )

    assert closed["actual_cost"] == expected_cost
    assert closed["cost_source"] == expected_source


def test_explicit_actual_cost_wins_over_nested_provider_usage() -> None:
    closed = finalize_production_cost_receipt(
        build_production_cost_receipt(
            {"project_id": "p", "task_type": "freezone_gen"},
            task_id="t",
            now="2026-09-02T00:00:00Z",
        ),
        status="completed",
        result={
            "actual_cost": {"credits": 4},
            "usage": {"cost": {"credits": 99}},
        },
        now="2026-09-02T00:00:01Z",
    )

    assert closed["actual_cost"] == {"credits": 4}
    assert closed["cost_source"] == "result.actual_cost"


def test_metadata_billing_cost_is_used_when_result_has_no_cost_evidence() -> None:
    closed = finalize_production_cost_receipt(
        build_production_cost_receipt(
            {"project_id": "p", "task_type": "freezone_gen"},
            task_id="t",
            now="2026-09-02T00:00:00Z",
        ),
        status="completed",
        result={"usage": {"input_tokens": 12}},
        metadata={"billing": {"credits": 3}},
        now="2026-09-02T00:00:01Z",
    )

    assert closed["actual_cost"] == {"credits": 3}
    assert closed["cost_source"] == "metadata.billing.credits"


def test_token_duration_and_quantity_are_not_mistaken_for_cost() -> None:
    closed = finalize_production_cost_receipt(
        build_production_cost_receipt(
            {"project_id": "p", "task_type": "freezone_video_gen"},
            task_id="t",
            now="2026-09-02T00:00:00Z",
        ),
        status="completed",
        result={
            "usage": {
                "input_tokens": 12,
                "output_tokens": 8,
                "total_tokens": 20,
                "images": 2,
                "duration_seconds": 5,
            }
        },
        now="2026-09-02T00:00:01Z",
    )

    assert closed["actual_cost"] == {}
    assert "cost_source" not in closed


def test_cost_source_survives_projection() -> None:
    projected = project_production_cost_receipt(
        {
            "schema": PRODUCTION_COST_RECEIPT_SCHEMA,
            "task_id": "task-1",
            "actual_cost": {"credits": 3},
            "cost_source": "result.usage.cost",
        }
    )

    assert projected["cost_source"] == "result.usage.cost"


def test_cost_summary_aggregates_only_projected_receipts() -> None:
    summary = summarize_production_cost_receipts(
        [
            {
                "schema": PRODUCTION_COST_RECEIPT_SCHEMA,
                "task_id": "task-1",
                "quantity": 2,
                "duration_ms": 1200,
                "result_status": "completed",
                "estimated_cost": {"credits": 2},
                "reserved_cost": {"credits": 2},
                "actual_cost": {"credits": 1.5},
            },
            {
                "schema": PRODUCTION_COST_RECEIPT_SCHEMA,
                "task_id": "task-2",
                "quantity": 1,
                "duration_ms": 800,
                "result_status": "failed",
                "reserved_cost": {"credits": 3},
                "wasted_cost": {"credits": 3},
                "prompt": "must not appear",
            },
            {"task_id": "invalid"},
        ]
    )

    assert summary == {
        "schema": "production_cost_summary.v1",
        "receipt_count": 2,
        "quantity": 3,
        "duration_ms": 2000,
        "status_counts": {"completed": 1, "failed": 1},
        "estimated_cost": {"credits": 2},
        "reserved_cost": {"credits": 5},
        "actual_cost": {"credits": 1.5},
        "wasted_cost": {"credits": 3},
    }
