"""Three-state keyframe review contract."""

from __future__ import annotations

import pytest

from novelvideo.production.keyframe_review import (
    KeyframeReviewError,
    REVIEW_PASS,
    REVIEW_REJECT,
    REVIEW_UNREVIEWED,
    keyframe_review_issues,
    validate_keyframe_review,
)

FRAME = "a" * 64
REFERENCE = "b" * 64


def _review(**overrides) -> dict:
    data = {
        "state": REVIEW_PASS,
        "note": "脸部与定妆图一致，领口完整，站位符合分镜",
        "identity_matches_reference": True,
        "matches_shot_requirement": True,
        "frame_sha256": FRAME,
        "reference_sha256": REFERENCE,
    }
    data.update(overrides)
    return data


def test_pass_is_bound_to_current_hashes():
    review = validate_keyframe_review(
        _review(), frame_sha256=FRAME, reference_sha256=REFERENCE
    )
    assert review["state"] == REVIEW_PASS
    assert review["schema"] == "village.keyframe-review.v1"


def test_pass_without_observation_or_booleans_is_rejected():
    with pytest.raises(KeyframeReviewError, match="identity_matches_reference"):
        validate_keyframe_review(_review(identity_matches_reference=False))
    with pytest.raises(KeyframeReviewError, match="matches_shot_requirement"):
        validate_keyframe_review(_review(matches_shot_requirement=False))
    with pytest.raises(KeyframeReviewError, match="observed note"):
        validate_keyframe_review(_review(note=""))


def test_pass_without_hashes_is_rejected():
    with pytest.raises(KeyframeReviewError, match="bind"):
        validate_keyframe_review(_review(frame_sha256="", reference_sha256=""))


def test_stale_frame_or_reference_is_rejected():
    with pytest.raises(KeyframeReviewError, match="different frame"):
        validate_keyframe_review(
            _review(), frame_sha256="c" * 64, reference_sha256=REFERENCE
        )
    with pytest.raises(KeyframeReviewError, match="different reference"):
        validate_keyframe_review(
            _review(), frame_sha256=FRAME, reference_sha256="d" * 64
        )


def test_reject_and_unreviewed_require_a_note():
    reject = validate_keyframe_review(
        _review(
            state=REVIEW_REJECT,
            note="右手多出一根手指，领口开成深 V",
            identity_matches_reference=False,
            matches_shot_requirement=False,
        )
    )
    assert reject["state"] == REVIEW_REJECT
    unreviewed = validate_keyframe_review(_review(state=REVIEW_UNREVIEWED, note="还没看"))
    assert unreviewed["state"] == REVIEW_UNREVIEWED
    with pytest.raises(KeyframeReviewError, match="note"):
        validate_keyframe_review(_review(state=REVIEW_REJECT, note=""))


def test_unknown_state_is_rejected():
    with pytest.raises(KeyframeReviewError, match="state must be one of"):
        validate_keyframe_review(_review(state="OK"))


def test_issue_helper_returns_stable_code():
    issues = keyframe_review_issues({"state": "PASS"})
    assert issues[0]["code"] == "keyframe_review.identity_unconfirmed"
