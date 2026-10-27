"""Asset-state contract: independent images, accumulation and prompt coverage."""

from __future__ import annotations

import pytest

from novelvideo.production.asset_states import (
    AssetStateError,
    normalize_asset_states,
    state_prompt_coverage,
    state_reference,
    transition_issues,
    validate_asset_state_ledger,
)


def _state(state_id: str = "wet", **overrides) -> dict:
    data = {
        "state_id": state_id,
        "path": f"assets/C01-{state_id}-v1.png",
        "sha256": "a" * 64,
        "locked": True,
        "descriptor": "雨水顺着发梢滴落，外套前襟湿透贴在身上",
    }
    data.update(overrides)
    return data


def test_normalizes_a_locked_state():
    states = normalize_asset_states([_state()], asset_id="C01")
    assert states[0].state_id == "wet"
    assert states[0].locked is True
    assert states[0].reference("C01") == "C01@wet"
    assert state_reference("C01", "wet") == "C01@wet"


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        ([_state(state_id="Wet")], "asset_state.id_invalid"),
        ([_state(), _state()], "asset_state.id_duplicate"),
        ([_state(path="")], "asset_state.path_missing"),
        ([_state(sha256="xyz")], "asset_state.sha256_invalid"),
        ("not-a-list", "asset_state.list_invalid"),
    ],
)
def test_invalid_states_fail_with_stable_codes(bad, code):
    with pytest.raises(AssetStateError) as exc:
        normalize_asset_states(bad, asset_id="C01")
    assert exc.value.code == code


def test_ledger_reports_unlocked_and_incomplete_states():
    states = normalize_asset_states(
        [_state(locked=False, descriptor="", sha256="")], asset_id="C01"
    )
    codes = {issue["code"] for issue in validate_asset_state_ledger(states, asset_id="C01")}
    assert codes == {
        "asset_state.not_locked",
        "asset_state.descriptor_missing",
        "asset_state.sha256_missing",
    }


def test_states_only_accumulate_between_shots():
    before = normalize_asset_states([_state("wet")], asset_id="C01")
    after = normalize_asset_states([_state("dirty")], asset_id="C01")
    issues = transition_issues(before, after, asset_id="C01")
    assert any(issue["code"] == "asset_state.regressed" for issue in issues)

    released = transition_issues(
        before, after, released={"wet": "进屋后擦干并换了外套"}, asset_id="C01"
    )
    assert not any(issue["code"] == "asset_state.regressed" for issue in released)
    assert not any(
        issue["code"] == "asset_state.release_without_reason" for issue in released
    )


def test_release_without_reason_and_unknown_release_are_reported():
    before = normalize_asset_states([_state("wet")], asset_id="C01")
    no_reason = transition_issues(before, (), released={"wet": ""}, asset_id="C01")
    assert any(
        issue["code"] == "asset_state.release_without_reason" for issue in no_reason
    )
    unknown = transition_issues((), before, released={"dirty": "换装"}, asset_id="C01")
    assert any(issue["code"] == "asset_state.release_unknown" for issue in unknown)


def test_locked_descriptor_must_reach_the_prompt_verbatim():
    states = normalize_asset_states([_state()], asset_id="C01")
    assert state_prompt_coverage(states, "他站在雨里，外套干爽", asset_id="C01")
    assert not state_prompt_coverage(
        states,
        "他站在雨里，雨水顺着发梢滴落，外套前襟湿透贴在身上",
        asset_id="C01",
    )


def test_state_reference_rejects_bad_ids():
    with pytest.raises(AssetStateError):
        state_reference("C01", "Wet")
    with pytest.raises(AssetStateError):
        state_reference("", "wet")
