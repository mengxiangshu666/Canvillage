from novelvideo.freezone.source_coverage import (
    attach_video_frame_units,
    build_source_coverage,
    mark_human_content_review,
    refresh_source_coverage,
)


def test_video_frame_anchors_are_evidence_not_shot_coverage():
    coverage = build_source_coverage("", [{"shot_no": 1}], input_kind="video")
    attached = attach_video_frame_units(coverage, {
        "source_sha256": "abc",
        "frames": [
            {"file": "scene_0001.png", "time_seconds": 0.0, "estimated": False},
            {"file": "scene_0002.png", "time_seconds": 1.5, "estimated": False},
        ],
    })
    assert attached["machine_status"] == "unverifiable"
    assert attached["coverage_status"] == "unverifiable"
    assert [unit["unit_id"] for unit in attached["units"]] == ["frame-0", "frame-1"]
    assert all(unit["status"] == "unverifiable" for unit in attached["units"])
    assert attached["units"][1]["time_seconds"] == 1.5
    assert any(issue["code"] == "video_shot_mapping_unverified" for issue in attached["issues"])


def test_explicit_shot_numbers_map_structurally_complete():
    source = "\n".join(
        [
            "镜1：阿雀推门进屋。",
            "镜2：她把钥匙放在桌上。",
            "镜3：门外雨声变大。",
        ]
    )
    rows = [
        {"shot_no": "1", "shot_id": "s1"},
        {"shot_no": "2", "shot_id": "s2"},
        {"shot_no": "3", "shot_id": "s3"},
    ]
    coverage = build_source_coverage(source, rows)
    assert coverage["machine_status"] == "complete"
    assert coverage["coverage_status"] == "complete"
    assert [unit["status"] for unit in coverage["units"]] == [
        "complete",
        "complete",
        "complete",
    ]
    assert coverage["human_content_review"]["status"] == "unverified"


def test_same_row_count_does_not_mean_content_covered():
    source = "\n".join(["镜1：交钥匙。", "镜2：烧账本。"])
    rows = [{"shot_no": "1", "shot_id": "s1"}, {"shot_no": "2", "shot_id": "s2"}]
    coverage = build_source_coverage(source, rows)
    assert coverage["machine_status"] == "complete"
    # The machine can only prove numbering; a human finds the missing events and
    # downgrades the content status instead of letting the ledger claim success.
    reviewed = mark_human_content_review(
        coverage,
        "partial",
        notes="账本镜头被换成抬眼看屏，烧账本事件缺失",
        reviewed_at="2026-10-23T00:00:00Z",
    )
    assert reviewed["coverage_status"] == "partial"
    assert reviewed["human_content_review"]["status"] == "partial"


def test_missing_and_duplicate_units_are_visible():
    source = "\n".join(["镜1：推门。", "镜2：抬眼。"])
    rows = [
        {"shot_no": "1", "shot_id": "s1"},
        {"shot_no": "1", "shot_id": "s1b"},
        {"shot_no": "9", "shot_id": "s9"},
    ]
    coverage = build_source_coverage(source, rows)
    assert coverage["machine_status"] == "partial"
    codes = {issue["code"] for issue in coverage["issues"]}
    assert "unit_mapped_twice" in codes
    assert "row_not_in_source" in codes
    assert "unit_unmapped" in codes


def test_ungrounded_creative_and_video_inputs_are_unverifiable():
    creative = build_source_coverage(
        "一个雨夜的追逐想法", [{"shot_no": "1", "shot_id": "s1"}]
    )
    assert creative["machine_status"] == "unverifiable"
    assert creative["issues"][0]["code"] == "row_unanchored"

    video = build_source_coverage(
        "", [{"shot_no": "1", "shot_id": "s1"}], input_kind="video"
    )
    assert video["machine_status"] == "unverifiable"
    assert video["coverage_status"] == "unverifiable"
    assert video["transcript_available"] is False


def test_source_change_invalidates_anchors_and_conclusions():
    source = "镜1：推门。\n镜2：抬眼。"
    coverage = build_source_coverage(source, [{"shot_no": "1"}, {"shot_no": "2"}])
    unchanged = refresh_source_coverage(coverage, source)
    assert unchanged["stale"] is False
    changed = refresh_source_coverage(coverage, "镜1：推门。\n镜2：转身。")
    assert changed["stale"] is True
    assert changed["coverage_status"] == "invalid"
    assert changed["units"] == []
    assert any(issue["code"] == "source_changed" for issue in changed["issues"])


def test_unknown_row_anchor_is_rejected_before_mapping():
    coverage = build_source_coverage(
        "第一段。\n第二段。",
        [{"shot_no": "1", "source_unit_ids": ["paragraph-99"]}],
    )
    assert any(issue["code"] == "row_anchor_unknown" for issue in coverage["issues"])
