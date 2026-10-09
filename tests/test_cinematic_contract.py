from __future__ import annotations

from novelvideo.production.cinematic_contract import (
    audit_cinematic_contracts,
    build_cinematic_contract,
    cinematic_prompt_lines,
    compute_cinematic_revision,
)


def _vision() -> dict:
    return {
        "schema": "director_vision.v1",
        "style_anchor": {
            "lighting": "傍晚窗光",
            "color_palette": "低饱和冷灰配暖木",
        },
        "cinematic": {
            "lighting": {
                "source_direction": "画面左后侧窗光",
                "color_temperature_k": 4300,
                "key_fill_ratio": "4:1",
                "motivated_source": "左侧落地窗",
            },
            "color_look": {
                "look_id": "muted-window-v1",
                "dominant": "冷灰",
                "secondary": "暖木",
                "accent": "深红",
                "ratios": {"dominant": 0.6, "secondary": 0.3, "accent": 0.1},
            },
            "screen_direction": {
                "axis_id": "room-window-axis",
                "line_side": "轴线右侧",
                "crossing_policy": "不越轴",
            },
            "edit": {
                "cut_on": "抬眼动作完成时",
                "match_cut": "杯口弧线",
                "beat_seconds": 0.5,
            },
        },
    }


def _shot(shot_id: str, *, light_direction: str = "") -> dict:
    value = {
        "shot_id": shot_id,
        "subject": "服务员",
        "action": "把杯子放回桌面",
        "screen_direction": {
            "subject_facing": "面向画面左",
            "eyeline": "看向桌面",
        },
        "sound": {
            "ambience": ["餐厅低频环境声"],
            "diegetic_sources": ["杯底接触桌面"],
            "sfx_cues": ["杯底落桌的短促声"],
        },
    }
    if light_direction:
        value["lighting"] = {
            "source_direction": light_direction,
            "color_temperature_k": 4300,
        }
    return value


def test_cinematic_contract_inherits_global_and_keeps_shot_override() -> None:
    contract = build_cinematic_contract(
        shot=_shot("S01"),
        director_vision=_vision(),
    )

    assert contract["lighting"]["source_direction"] == "画面左后侧窗光"
    assert contract["screen_direction"]["axis_id"] == "room-window-axis"
    assert contract["screen_direction"]["subject_facing"] == "面向画面左"
    assert contract["color_look"]["ratios"] == {
        "dominant": 0.6,
        "secondary": 0.3,
        "accent": 0.1,
    }
    assert contract["contract_revision"] == compute_cinematic_revision(contract)


def test_cinematic_audit_passes_consistent_evidence_without_inventing_final_qc() -> None:
    report = audit_cinematic_contracts(
        [_shot("S01"), _shot("S02")],
        director_vision=_vision(),
    )

    observations = report["gate_observations"]
    assert observations["lighting_continuity_consistent"] is True
    assert observations["screen_direction_consistent"] is True
    assert observations["color_look_consistent"] is True
    assert observations["edit_rhythm_ready"] is True
    assert observations["sound_design_ready"] is True
    assert observations["cross_episode_continuity_valid"] is None
    assert observations["final_delivery_qc_passed"] is None
    assert report["failed_gates"] == []


def test_cinematic_audit_blocks_light_and_axis_flip() -> None:
    report = audit_cinematic_contracts(
        [
            _shot("S01", light_direction="画面左后侧窗光"),
            {
                **_shot("S02", light_direction="画面右侧台灯"),
                "screen_direction": {
                    "axis_id": "opposite-axis",
                    "line_side": "轴线左侧",
                    "crossing_policy": "不越轴",
                },
            },
        ],
        director_vision=_vision(),
    )

    assert report["gate_observations"]["lighting_continuity_consistent"] is False
    assert report["gate_observations"]["screen_direction_consistent"] is False
    codes = {item["code"] for item in report["issues"]}
    assert "lighting.source_direction_inconsistent" in codes
    assert "screen_direction.axis_changed" in codes


def test_cross_episode_gate_requires_version_references() -> None:
    missing_revision = audit_cinematic_contracts(
        [
            {
                **_shot("S01"),
                "continuity": {
                    "series_id": "series-01",
                    "episode_index": 3,
                },
            }
        ]
    )
    assert missing_revision["gate_observations"]["cross_episode_continuity_valid"] is False

    ready = audit_cinematic_contracts(
        [
            {
                **_shot("S02"),
                "continuity": {
                    "series_id": "series-01",
                    "episode_index": 3,
                    "asset_revisions": {"hero": 7},
                    "locked_fields": ["wardrobe", "hairstyle"],
                },
            }
        ]
    )
    assert ready["gate_observations"]["cross_episode_continuity_valid"] is True


def test_cinematic_prompt_lines_render_only_declared_facts() -> None:
    contract = build_cinematic_contract(shot=_shot("S01"), director_vision=_vision())
    rendered = "\n".join(cinematic_prompt_lines(contract))

    assert "光线合同" in rendered
    assert "4300K" in rendered
    assert "屏幕方向" in rendered
    assert "配额：dominant 60%" in rendered
    assert "声音合同" in rendered
    assert "final_delivery_qc" not in rendered


def test_final_delivery_qc_only_uses_explicit_receipt() -> None:
    report = audit_cinematic_contracts(
        [_shot("S01")],
        director_vision=_vision(),
        delivery_qc={"status": "passed", "sha256": "abc"},
    )
    assert report["gate_observations"]["final_delivery_qc_passed"] is True

    failed = audit_cinematic_contracts(
        [_shot("S01")],
        director_vision=_vision(),
        delivery_qc={"status": "failed"},
    )
    assert failed["gate_observations"]["final_delivery_qc_passed"] is False


def test_shots_without_any_declaration_are_opt_out_not_a_pass() -> None:
    """Pin the silent-green-light behaviour the combat contract has to compensate.

    ``audit_cinematic_contracts`` reports ``applicable_gates=[]`` and
    ``passed=True`` when no shot declares a family at all.  Downstream that
    becomes ``not_run`` gates that do not block in non-strict mode.  This test
    documents the existing contract, not a desired one: the combat contract
    checks declaration presence itself before delegating here.
    """

    report = audit_cinematic_contracts(
        [
            {"shot_id": "S01", "action": "出拳"},
            {"shot_id": "S02", "action": "格挡"},
        ]
    )

    assert report["passed"] is True
    assert report["applicable_gates"] == []
    assert set(report["gate_observations"].values()) == {None}


def test_declaration_presence_check_prevents_the_silent_pass() -> None:
    from novelvideo.production.combat_film_contract import (
        audit_combat_cinematic_consistency,
    )

    report = audit_combat_cinematic_consistency(
        [
            {"shot_id": "S01", "action": "出拳"},
            {"shot_id": "S02", "action": "格挡"},
        ]
    )

    assert report["passed"] is False
    assert "combat.cinematic.family_missing" in {item["code"] for item in report["issues"]}
