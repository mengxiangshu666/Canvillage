"""Pure evaluation tests for the content-level delivery guard."""

from __future__ import annotations

from novelvideo.production.delivery_media_guard import (
    FAILED,
    NOT_RUN,
    PASSED,
    audit_delivery_media,
    evaluate_media_facts,
)

ZERO = "0" * 64
ONE_BIT = "0" * 63 + "1"
FAR = "f" * 64
FLAT_EDGE = bytes(96 * 54)
OTHER_EDGE = bytes([40]) * (96 * 54)


def _shot(
    shot_id: str,
    *,
    first: str = ZERO,
    keyframe: str = ZERO,
    last: str = ZERO,
    volume: float | None = -20.0,
    dialogue: bool = False,
) -> dict:
    return {
        "shot_id": shot_id,
        "clip": f"{shot_id}.mp4",
        "keyframe": f"{shot_id}.png",
        "probe": {"has_video": True, "has_audio": volume is not None},
        "first_frame_hash": first,
        "first_frame_edge": FLAT_EDGE,
        "keyframe_hash": keyframe,
        "keyframe_edge": FLAT_EDGE,
        "last_frame_hash": last,
        "last_frame_edge": FLAT_EDGE,
        "mean_volume_db": volume,
        "has_dialogue": dialogue,
    }


def _facts(*, final_volume: float | None = -20.0, shots=None, seams=None) -> dict:
    return {
        "final": {
            "probe": {"has_video": True, "has_audio": final_volume is not None},
            "mean_volume_db": final_volume,
            "error": "",
        },
        "shots": shots
        if shots is not None
        else [
            _shot("S01", dialogue=True, last=ZERO),
            _shot("S02", first=ZERO),
        ],
        "seams": seams
        if seams is not None
        else [{"from_shot": "S01", "to_shot": "S02", "continuous": True}],
    }


def test_complete_evidence_passes():
    report = evaluate_media_facts(_facts(), {})
    assert report["passed"] is True
    assert report["status"] == PASSED
    assert report["failures"] == 0
    assert report["not_run"] == 0


def test_final_silence_fails():
    report = evaluate_media_facts(_facts(final_volume=-80.0), {})
    assert report["passed"] is False
    failed = {item["check"] for item in report["checks"] if item["status"] == FAILED}
    assert "final_audio_activity" in failed


def test_keyframe_mismatch_fails():
    report = evaluate_media_facts(
        _facts(shots=[_shot("S01", first=FAR, keyframe=ZERO)]), {}
    )
    assert report["passed"] is False
    failed = {item["check"] for item in report["checks"] if item["status"] == FAILED}
    assert "shot_first_frame:S01" in failed


def test_dialogue_shot_without_audio_fails():
    report = evaluate_media_facts(
        _facts(shots=[_shot("S01", dialogue=True, volume=None)]), {}
    )
    assert report["passed"] is False
    failed = {item["check"] for item in report["checks"] if item["status"] == FAILED}
    assert "shot_dialogue_audio:S01" in failed


def test_continuous_seam_frame_jump_fails():
    report = evaluate_media_facts(
        _facts(
            shots=[_shot("S01", last=FAR), _shot("S02", first=ZERO)],
        ),
        {},
    )
    assert report["passed"] is False
    failed = {item["check"] for item in report["checks"] if item["status"] == FAILED}
    assert "seam_frame:S01->S02" in failed


def test_sound_bridge_level_jump_fails():
    report = evaluate_media_facts(
        _facts(
            shots=[
                _shot("S01", last=ZERO, volume=-10.0),
                _shot("S02", first=ZERO, volume=-35.0),
            ],
        ),
        {},
    )
    assert report["passed"] is False
    failed = {item["check"] for item in report["checks"] if item["status"] == FAILED}
    assert "sound_bridge:S01->S02" in failed


def test_missing_seam_side_is_not_run_and_fails_closed():
    report = evaluate_media_facts(
        _facts(shots=[_shot("S01")], seams=[{"from_shot": "S01", "to_shot": "S99", "continuous": True}]),
        {},
    )
    assert report["passed"] is False
    assert report["status"] == NOT_RUN
    assert report["not_run"] == 1


def test_fact_collection_error_is_not_run_and_fails_closed():
    report = evaluate_media_facts(
        {"final": {"probe": {}, "mean_volume_db": None, "error": "ffprobe is not available"}},
        {},
    )
    assert report["passed"] is False
    assert report["status"] == NOT_RUN
    assert report["checks"][0]["check"] == "media_facts"


def test_non_object_manifest_fails_closed():
    report = audit_delivery_media(["not", "a", "manifest"])
    assert report["passed"] is False
    assert report["status"] == NOT_RUN


def test_missing_media_fails_closed_instead_of_raising():
    report = audit_delivery_media(
        {
            "schema": "village.delivery-media-manifest.v1",
            "final_video": "definitely-missing-final.mp4",
            "shots": [],
            "seams": [],
        }
    )
    assert report["passed"] is False
    assert report["status"] == NOT_RUN
    assert report["checks"][0]["check"] == "media_facts"


def test_hash_distance_tolerates_one_bit():
    report = evaluate_media_facts(
        _facts(shots=[_shot("S01", first=ONE_BIT, keyframe=ZERO)]), {}
    )
    checks = {item["check"]: item for item in report["checks"]}
    assert checks["shot_first_frame:S01"]["status"] == PASSED


def test_cut_seams_are_not_checked_for_sound_bridge():
    report = evaluate_media_facts(
        _facts(seams=[{"from_shot": "S01", "to_shot": "S02", "continuous": False}]),
        {},
    )
    assert not any(
        item["check"].startswith("sound_bridge:") for item in report["checks"]
    )


def test_compose_manifest_is_built_from_the_completed_shot_batch():
    from pathlib import Path

    from novelvideo.workflow_runtime.delivery_content_guard import (
        build_delivery_content_guard_manifest,
    )

    manifest = build_delivery_content_guard_manifest(
        Path("final.mp4"),
        [
            {
                "shot_id": "S01",
                "output_path": "S01.mp4",
                "source_image_path": "S01.png",
                "dialogue_audio": {"path": "S01.wav"},
                "duration_seconds": 4,
                "shot_contract": {"seam": "continuous_action"},
            },
            {
                "shot_id": "S02",
                "output_path": "S02.mp4",
                "source_image_path": "S02.png",
                "duration_seconds": 4,
                "shot_contract": {"seam": "cut"},
            },
        ],
    )
    assert manifest is not None
    assert manifest["schema"] == "village.delivery-media-manifest.v1"
    assert manifest["final_video"] == "final.mp4"
    assert manifest["shots"][0]["has_dialogue"] is True
    assert manifest["seams"][0]["continuous"] is True
    assert manifest["seams"][0]["timeline_seconds"] == 4.0


def test_compose_manifest_refuses_missing_keyframe_evidence():
    from pathlib import Path

    from novelvideo.workflow_runtime.delivery_content_guard import (
        build_delivery_content_guard_manifest,
    )

    assert (
        build_delivery_content_guard_manifest(
            Path("final.mp4"),
            [{"shot_id": "S01", "output_path": "S01.mp4"}],
        )
        is None
    )


def test_sound_bridge_prefers_final_film_window_over_source_clip_means():
    report = evaluate_media_facts(
        _facts(
            shots=[
                _shot("S01", last=ZERO, volume=-10.0),
                _shot("S02", first=ZERO, volume=-35.0),
            ],
            seams=[
                {
                    "from_shot": "S01",
                    "to_shot": "S02",
                    "continuous": True,
                    "timeline_seconds": 4.0,
                    "left_volume_db": -20.0,
                    "right_volume_db": -22.0,
                }
            ],
        ),
        {},
    )
    bridge = next(
        item for item in report["checks"] if item["check"] == "sound_bridge:S01->S02"
    )
    assert bridge["status"] == PASSED
    assert bridge["observed"] == 2.0
    assert bridge["sample_source"] == "final_film_window"


def test_hash_can_pass_while_edge_pose_jump_fails_the_seam():
    left = _shot("S01", last=ZERO)
    right = _shot("S02", first=ZERO)
    left["last_frame_edge"] = FLAT_EDGE
    right["first_frame_edge"] = OTHER_EDGE
    report = evaluate_media_facts(
        _facts(shots=[left, right]), {}
    )
    seam = next(
        item for item in report["checks"] if item["check"] == "seam_frame:S01->S02"
    )
    assert seam["status"] == FAILED
    assert seam["observed"] == 0
    assert seam["edge_distance"] == 40.0
