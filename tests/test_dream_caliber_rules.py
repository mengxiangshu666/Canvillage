"""Dream-derived caliber rules: prompt coverage, static anchors and speech rate."""

from __future__ import annotations

from novelvideo.freezone.script_contract import validate_script_rows
from novelvideo.production.dialogue_sound_contract import (
    DIALOGUE_RATE_PROFILE,
    audit_dialogue_sound_contract,
    build_dialogue_sound_contract,
)


def _row(**overrides) -> dict:
    row = {
        "shot_no": 1,
        "duration": 4,
        "visual_description": "雨夜门口，他推门进入",
        "character_action": "推门走入",
        "shot": "中景",
        "sound": "",
        "dialogue": "",
        "shot_prompt": "",
        "video_motion_prompt": "雨夜门口，他推门走入，镜头缓慢推进到门内",
        "transition_plan": "",
        "start_state": "",
        "end_state": "",
        "prop_state_end": "",
        "prop_state_change": "",
    }
    row.update(overrides)
    return row


def _rules(rows: list[dict]) -> set[str]:
    return {issue.rule_id for issue in validate_script_rows(rows).issues}


def test_sound_that_never_reaches_the_motion_prompt_is_reported():
    rows = [_row(sound="雨声、门轴声")]
    assert "script.prompt.sound_coverage.v1" in _rules(rows)

    covered = [
        _row(
            sound="雨声、门轴声",
            video_motion_prompt="雨夜门口，雨声贴着屋檐，门轴声响起，他推门走入，镜头缓慢推进",
        )
    ]
    assert "script.prompt.sound_coverage.v1" not in _rules(covered)


def test_prop_end_state_that_never_reaches_the_motion_prompt_is_reported():
    rows = [_row(prop_state_end="刀锋缺口")]
    assert "script.prompt.state_coverage.v1" in _rules(rows)

    covered = [
        _row(
            prop_state_end="刀锋缺口",
            video_motion_prompt="他收刀入鞘，刀锋缺口在灯下可见，镜头缓慢拉远",
        )
    ]
    assert "script.prompt.state_coverage.v1" not in _rules(covered)


def test_static_anchor_before_continuous_handoff_is_reported():
    rows = [
        _row(transition_plan="连续动作", end_state="他站定在门口，双手垂下"),
        _row(shot_no=2, start_state="他抬头看向门内"),
    ]
    issues = [
        issue
        for issue in validate_script_rows(rows).issues
        if issue.rule_id == "script.continuity.static_anchor.v1"
    ]
    assert len(issues) == 1
    assert issues[0].row_index == 0
    assert issues[0].detail["static_marker"] == "站定"


def test_static_anchor_is_not_reported_when_next_shot_starts_moving():
    rows = [
        _row(transition_plan="连续动作", end_state="他站定在门口"),
        _row(shot_no=2, start_state="他转身冲出门口"),
    ]
    assert "script.continuity.static_anchor.v1" not in _rules(rows)


def _dialogue_contract(text: str, *, seconds: float = 4.0) -> dict:
    return build_dialogue_sound_contract(
        shots=[
            {
                "shot_id": "S01",
                "duration_seconds": seconds,
                "dialogue": [f"小明：{text}"],
                "sound": {"ambience": ["雨声"]},
            }
        ]
    )


def test_rate_above_the_evidence_window_blocks():
    report = audit_dialogue_sound_contract(
        _dialogue_contract("今天我们必须马上离开这里", seconds=1.5)
    )
    assert report["passed"] is False
    assert any(
        issue["code"] == "dialogue.rate_too_fast" for issue in report["issues"]
    )


def test_low_density_is_advisory_not_blocking():
    # 12 characters / 4s = 3.0 cps: inside Dream's measured self-noise band.
    report = audit_dialogue_sound_contract(_dialogue_contract("我们走吧现在"))
    assert report["passed"] is True
    assert report["advisory_count"] >= 1
    assert any(
        issue["code"] == "dialogue.rate_low_density" for issue in report["issues"]
    )


def test_rate_profile_is_overridable_per_model():
    contract = _dialogue_contract("我们走吧现在")
    strict = audit_dialogue_sound_contract(
        contract,
        rate_profile={"min": 1.0, "max": 7.0, "slow": 0.5, "source": "channel-profile"},
    )
    assert strict["passed"] is True
    assert not any(
        issue["code"] == "dialogue.rate_low_density" for issue in strict["issues"]
    )
    assert strict["rate_profile"]["source"] == "channel-profile"


def test_default_rate_profile_keeps_its_evidence_source():
    report = audit_dialogue_sound_contract(_dialogue_contract("今天我们必须马上走"))
    assert report["rate_profile"]["max"] == 7.0
    assert "dream-director" in report["rate_profile"]["source"]
    assert DIALOGUE_RATE_PROFILE["min"] == 5.0
