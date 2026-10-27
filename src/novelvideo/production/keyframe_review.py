"""Three-state keyframe review bound to the current frame and reference hashes.

A keyframe PASS is only meaningful if it names what was observed and is bound to
the exact frame and reference that are about to be used.  ``UNREVIEWED`` is a
first-class state: pretending it is a pass is the failure this contract exists
to prevent.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

KEYFRAME_REVIEW_SCHEMA = "village.keyframe-review.v1"
REVIEW_PASS = "PASS"
REVIEW_REJECT = "REJECT"
REVIEW_UNREVIEWED = "UNREVIEWED"
REVIEW_STATES = (REVIEW_PASS, REVIEW_REJECT, REVIEW_UNREVIEWED)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


class KeyframeReviewError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _text(value: object, *, limit: int = 1200) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _sha(value: object) -> str:
    normalized = _text(value, limit=64).lower()
    return normalized if _SHA256.fullmatch(normalized) else ""


def validate_keyframe_review(
    value: object,
    *,
    frame_sha256: str = "",
    reference_sha256: str = "",
) -> dict[str, Any]:
    """Validate one review and bind it to the current media hashes."""

    if not isinstance(value, Mapping):
        raise KeyframeReviewError("keyframe_review.invalid", "review must be an object")
    review = dict(value)
    state = _text(review.get("state"), limit=20).upper()
    if state not in REVIEW_STATES:
        raise KeyframeReviewError(
            "keyframe_review.state_invalid",
            f"state must be one of {', '.join(REVIEW_STATES)}",
        )
    note = _text(review.get("note"))
    if state in {REVIEW_REJECT, REVIEW_UNREVIEWED} and not note:
        raise KeyframeReviewError(
            "keyframe_review.note_missing",
            f"{state} requires a note with the defect or the reason it was not reviewed",
        )
    if state == REVIEW_PASS:
        if review.get("identity_matches_reference") is not True:
            raise KeyframeReviewError(
                "keyframe_review.identity_unconfirmed",
                "PASS requires identity_matches_reference=true",
            )
        if review.get("matches_shot_requirement") is not True:
            raise KeyframeReviewError(
                "keyframe_review.requirement_unconfirmed",
                "PASS requires matches_shot_requirement=true",
            )
        if not note:
            raise KeyframeReviewError(
                "keyframe_review.note_missing",
                "PASS requires an observed note, not a bare verdict",
            )
    bound_frame = _sha(review.get("frame_sha256"))
    bound_reference = _sha(review.get("reference_sha256"))
    if state == REVIEW_PASS:
        if not bound_frame or not bound_reference:
            raise KeyframeReviewError(
                "keyframe_review.hash_missing",
                "PASS must bind frame_sha256 and reference_sha256",
            )
        current_frame = _sha(frame_sha256)
        current_reference = _sha(reference_sha256)
        if current_frame and bound_frame != current_frame:
            raise KeyframeReviewError(
                "keyframe_review.frame_stale",
                "review is bound to a different frame than the current keyframe",
            )
        if current_reference and bound_reference != current_reference:
            raise KeyframeReviewError(
                "keyframe_review.reference_stale",
                "review is bound to a different reference than the current keyframe input",
            )
    review["state"] = state
    review["note"] = note
    review["frame_sha256"] = bound_frame
    review["reference_sha256"] = bound_reference
    review["schema"] = KEYFRAME_REVIEW_SCHEMA
    return review


def keyframe_review_issues(
    value: object,
    *,
    frame_sha256: str = "",
    reference_sha256: str = "",
) -> list[dict[str, str]]:
    try:
        validate_keyframe_review(
            value,
            frame_sha256=frame_sha256,
            reference_sha256=reference_sha256,
        )
    except KeyframeReviewError as exc:
        return [{"code": exc.code, "message": exc.message}]
    return []


__all__ = [
    "KEYFRAME_REVIEW_SCHEMA",
    "KeyframeReviewError",
    "REVIEW_PASS",
    "REVIEW_REJECT",
    "REVIEW_STATES",
    "REVIEW_UNREVIEWED",
    "keyframe_review_issues",
    "validate_keyframe_review",
]
