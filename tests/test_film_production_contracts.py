from __future__ import annotations

import pytest

from novelvideo.production.delivery_qc_contract import (
    DELIVERY_QC_RELEASE_CHECKS,
    build_delivery_qc_contract,
    project_release_readiness,
)
from novelvideo.production.dialogue_sound_contract import (
    audit_dialogue_sound_contract,
    build_dialogue_sound_contract,
)
from novelvideo.production.film_production_contract import (
    PREPRODUCTION_GATES,
    compile_film_production_contract,
)
from novelvideo.services.film_production import compile_film_preproduction_contract
from novelvideo.production.production_estimator import (
    audit_production_estimate,
    build_production_estimate,
)
from novelvideo.production.screening_repair import (
    build_screening_feedback,
    plan_screening_repairs,
)
from novelvideo.production.story_contract import (
    audit_series_story_contract,
    build_series_story_contract,
)
from novelvideo.production.visual_bible import (
    audit_visual_bible,
    build_visual_bible,
)
from novelvideo.freezone.film_prompt_contract import (
    audit_film_prompt_bundle,
    build_film_prompt_bundle,
    rewrite_video_positive_constraints,
)
from novelvideo.workflow_runtime.cinematic_review import build_cinematic_review


def _vision() -> dict:
    return {
        "style_anchor": {
            "visual_style": "低饱和写实电影感",
            "lighting": "窗外冷光",
            "color_palette": "冷灰与暖木",
        },
        "cinematic": {
            "lighting": {
                "source_direction": "画面左后侧窗光",
                "color_temperature_k": 4300,
                "key_fill_ratio": "4:1",
            },
            "color_look": {
                "look_id": "window-cold-v1",
                "dominant": "冷灰",
                "secondary": "暖木",
                "accent": "深红",
                "ratios": {"dominant": 0.6, "secondary": 0.3, "accent": 0.1},
            },
        },
    }



def _script_row() -> dict:
    return {
        "shot_id": "shot-1",
        "display_shot_no": "1",
        "duration": 5,
        "shot_prompt": " + ".join(
            [
                "[画面构图：中景]",
                "[角色卡/主体描述：[周浩_职场装: 青年]]",
                "[主体/人物空间与互动关系：独自站在电梯里]",
                "[极具体的微表情、主体状态或关键视觉信息：眉头紧锁]",
                "[明确的场景环境元素与前景/背景道具：电梯轿厢]",
                "[光影几何与大气效果：冷白顶光]",
                "[视觉风格/质感：写实]",
                "[技术参数：35mm]",
            ]
        ),
        "video_motion_prompt": " + ".join(
            [
                "[明确的摄影机运镜轨迹与速度：镜头缓慢前推]",
                "[主体极其具体的物理动作细节：他整理领带]",
                "[环境物理动态：楼层数字变化]",
                "[音效与氛围描述：电梯机械声]",
                "[对话台词与语气：无]",
                "[时长：5s]",
            ]
        ),
    }


def _shot(shot_id: str = "S01") -> dict:
    return {
        "shot_id": shot_id,
        "duration_seconds": 5,
        "prompt": "人物全身站在窗前转身，镜头缓慢前推",
        "shot_prompt": " + ".join(
            [
                "[画面构图：近景，平视机位]",
                "[角色卡/主体描述：[角色_001: 黑发青年，灰色大衣]]",
                "[主体/人物空间与互动关系：他站在窗前]",
                "[极具体的微表情：他抿住嘴角]",
                "[明确的场景环境元素：雨夜公寓、玻璃反光]",
                "[光影几何与大气效果：左后侧冷窗光]",
                "[视觉风格/质感：低饱和写实电影感]",
                "[技术参数：50mm镜头，f/2.0]",
            ]
        ),
        "video_motion_prompt": " + ".join(
            [
                "[明确的摄影机运镜轨迹与速度：镜头缓慢前推]",
                "[主体极其具体的物理动作细节：他转身看向窗外]",
                "[环境物理动态：雨滴沿玻璃下滑]",
                "[音效与氛围描述：室内低频环境声、雨声]",
                "[对话台词与语气：无]",
                "[时长：5s]",
            ]
        ),
        "reference_bindings": {"character": ["char-001"]},
    }


def _ready_contract(
    *,
    ready: bool = True,
    extra_observations: dict | None = None,
) -> dict:
    gate_by_audit = {
        "story_audit": "series_story_contract_valid",
        "visual_audit": "visual_bible_locked",
        "dialogue_sound_audit": "dialogue_sound_contract_valid",
        "prompt_audit": "film_prompt_contract_valid",
    }
    audits = {
        audit_name: {"gate_observations": {gate: ready}}
        for audit_name, gate in gate_by_audit.items()
    }
    audits["visual_audit"] = {
        "gate_observations": {
            "visual_bible_locked": ready,
            "asset_view_plan_ready": ready,
        }
    }
    if extra_observations:
        audits["screening_audit"] = {
            "gate_observations": extra_observations,
        }
    return compile_film_production_contract(
        production_id="project-1",
        **audits,
    )


def test_story_contract_requires_state_change_and_real_handoff() -> None:
    contract = build_series_story_contract(
        series_id="series-1",
        title="长夜",
        dramatic_promise="主角必须找回被夺走的名字",
        conflict_engine="每夺回一条线索，就失去一个盟友",
        episodes=[
            {
                "episode_index": 1,
                "dramatic_question": "谁拿走了名字？",
                "entering_state": "主角隐姓埋名",
                "main_conflict": "密探封街",
                "turning_point": "他发现密探认识自己的旧名",
                "exit_state": "主角主动进入封锁区",
                "state_change": "从躲避变为追查",
                "hook": {
                    "text": "密探叫出了他的旧名",
                    "kind": "real",
                    "consequence": "一旦回应就会被追踪",
                },
            },
            {
                "episode_index": 2,
                "dramatic_question": "密探为何知道旧名？",
                "entering_state": "主角主动进入封锁区",
                "main_conflict": "他必须取得密探信任",
                "turning_point": "密探交出半张名单",
                "exit_state": "名单暴露了主角的资助人",
                "state_change": "追查目标从密探转向资助人",
                "hook": {
                    "text": "资助人的名字也在名单上",
                    "kind": "real",
                    "consequence": "主角失去了唯一可信任的退路",
                },
            },
        ],
    )

    report = audit_series_story_contract(contract)

    assert report["passed"] is True
    assert report["gate_observations"]["series_story_contract_valid"] is True


def test_empty_visual_bible_does_not_silently_pass_media_gate() -> None:
    compiled = compile_film_preproduction_contract(
        production_id="project-1",
        rows=[_script_row()],
    )
    observations = compiled["film_production"]["gate_observations"]

    assert observations["visual_bible_locked"] is False
    assert compiled["film_production"]["ready_for_media"] is False


def test_visual_bible_derives_required_views_from_actual_exposure() -> None:
    assets = [
        {
            "asset_id": "char-001",
            "asset_kind": "character",
            "asset_revision": 3,
            "sha256": "a" * 64,
            "identity_locks": ["face", "wardrobe"],
            "views": ["front", "close_up", "expression", "full_body"],
        }
    ]
    bible = build_visual_bible(
        director_vision=_vision(),
        assets=assets,
        shots=[_shot()],
    )
    report = audit_visual_bible(bible)

    assert report["gate_observations"]["visual_bible_locked"] is True
    assert report["gate_observations"]["asset_view_plan_ready"] is True
    plan = bible["asset_view_plan"]["assets"][0]
    assert {"front", "full_body"} <= set(plan["required_views"])

    assets[0]["views"] = ["front"]
    missing = audit_visual_bible(
        build_visual_bible(
            director_vision=_vision(),
            assets=assets,
            shots=[_shot()],
        )
    )
    assert missing["gate_observations"]["asset_view_plan_ready"] is False


def test_dialogue_contract_checks_budget_and_meta_direction() -> None:
    contract = build_dialogue_sound_contract(
        shots=[
            {
                **_shot(),
                "dialogue": ["小明：前进", "小红：等我"],
                "sound": {
                    "ambience": ["雨声"],
                    "diegetic_sources": ["脚步声"],
                },
            }
        ]
    )
    assert audit_dialogue_sound_contract(contract)["passed"] is True

    leaked = build_dialogue_sound_contract(
        shots=[
            {
                **_shot(),
                "dialogue": ["小明：台词：必须说这一句"],
                "sound": {"ambience": ["雨声"]},
            }
        ]
    )
    report = audit_dialogue_sound_contract(leaked)
    assert report["passed"] is False
    assert "dialogue.meta_direction_leaked" in {
        issue["code"] for issue in report["issues"]
    }


def test_film_prompt_contract_preserves_segments_and_rewrites_negatives() -> None:
    bundle = build_film_prompt_bundle([_shot()])
    report = audit_film_prompt_bundle(bundle)

    assert report["passed"] is True
    assert bundle["rows"][0]["shot_segments"]["character_card"]
    assert bundle["rows"][0]["style_segment_hash"]
    rewritten = rewrite_video_positive_constraints("不要出现文字，不要闪烁")
    assert "不要" not in rewritten
    assert "干净无文字" in rewritten


def _delivery_observations() -> dict:
    return {
        "container_allowed": {"container": "mp4"},
        "video_stream_present": True,
        "audio_stream_present": True,
        "dimensions": {"width": 1920, "height": 1080},
        "frame_rate": {"fps": 24},
        "duration": {"duration_seconds": 60},
        "black_frames": {"ratio": 0.0},
        "freeze_frames": {"ratio": 0.0},
        "av_sync": {"max_offset_ms": 40},
        "audio_activity": {
            "has_audio": True,
            "measurable": True,
            "all_silent": False,
        },
        "loudness": {"integrated_lufs": -16},
        "true_peak": {"true_peak_db": -2.3},
        "subtitle_stream": {"present": True},
        "color_space": {"color_space": "bt709"},
        "bitrate": {"kbps": 12000},
        "file_readback": True,
        "sha256": "b" * 64,
    }


def test_delivery_qc_keeps_missing_evidence_as_not_run() -> None:
    target = {
        "width": 1920,
        "height": 1080,
        "fps": 24,
        "duration_seconds": 60,
        "integrated_lufs": -16,
        "true_peak_db": -2,
        "color_space": "bt709",
        "min_kbps": 10000,
    }
    passed = build_delivery_qc_contract(
        observations=_delivery_observations(),
        target=target,
    )
    assert passed["gate_observations"]["final_delivery_qc_passed"] is True

    missing = build_delivery_qc_contract(
        observations={**_delivery_observations(), "av_sync": None},
        target=target,
    )
    assert missing["gate_observations"]["final_delivery_qc_passed"] is None


def test_delivery_qc_requires_only_a_positive_bitrate_without_a_quality_minimum() -> None:
    passed = build_delivery_qc_contract(
        observations={**_delivery_observations(), "bitrate": {"kbps": 107.7}},
        required_checks=["bitrate"],
    )
    assert passed["checks"]["bitrate"]["status"] == "passed"

    missing = build_delivery_qc_contract(
        observations={**_delivery_observations(), "bitrate": None},
        required_checks=["bitrate"],
    )
    assert missing["checks"]["bitrate"]["status"] == "not_run"

    non_positive = build_delivery_qc_contract(
        observations={**_delivery_observations(), "bitrate": {"kbps": 0}},
        required_checks=["bitrate"],
    )
    assert non_positive["checks"]["bitrate"]["status"] == "failed"


def test_delivery_qc_audio_activity_and_optional_subtitles_are_explicit() -> None:
    observations = {
        **_delivery_observations(),
        "subtitle_stream": {"present": False, "required": False},
        "audio_activity": {
            "has_audio": True,
            "measurable": True,
            "all_silent": False,
        },
    }
    passed = build_delivery_qc_contract(
        observations=observations,
        required_checks=["subtitle_stream", "audio_activity"],
    )
    assert passed["gate_observations"]["final_delivery_qc_passed"] is True

    silent = build_delivery_qc_contract(
        observations={
            **observations,
            "audio_activity": {
                "has_audio": True,
                "measurable": False,
                "all_silent": True,
            },
        },
        required_checks=["subtitle_stream", "audio_activity"],
    )
    assert silent["gate_observations"]["final_delivery_qc_passed"] is False
    assert silent["failed_checks"] == ["audio_activity"]


def test_release_readiness_is_derived_from_complete_qc_evidence() -> None:
    target = {
        "width": 1920,
        "height": 1080,
        "fps": 24,
        "duration_seconds": 60,
        "integrated_lufs": -16,
        "true_peak_db": -2,
        "color_space": "bt709",
        "min_kbps": 10000,
    }
    ready_qc = build_delivery_qc_contract(
        observations=_delivery_observations(),
        target=target,
        required_checks=DELIVERY_QC_RELEASE_CHECKS,
    )
    ready = project_release_readiness(ready_qc)
    assert ready["status"] == "ready"
    assert ready["can_publish"] is True
    assert ready["reason_code"] == "delivery_qc_passed"

    incomplete_qc = build_delivery_qc_contract(
        observations={**_delivery_observations(), "audio_activity": None},
        target=target,
        required_checks=DELIVERY_QC_RELEASE_CHECKS,
    )
    incomplete = project_release_readiness(incomplete_qc)
    assert incomplete["status"] == "unverified"
    assert incomplete["can_publish"] is False
    assert incomplete["not_run_checks"] == ["audio_activity"]

    blocked_qc = build_delivery_qc_contract(
        observations={
            **_delivery_observations(),
            "audio_activity": {
                "has_audio": True,
                "measurable": True,
                "all_silent": True,
            },
        },
        target=target,
        required_checks=DELIVERY_QC_RELEASE_CHECKS,
    )
    blocked = project_release_readiness(blocked_qc)
    assert blocked["status"] == "blocked"
    assert blocked["can_publish"] is False
    assert blocked["failed_checks"] == ["audio_activity"]

    forged = {**ready_qc, "passed": False}
    assert project_release_readiness(forged)["status"] == "blocked"
    assert project_release_readiness(forged)["reason_code"] == (
        "delivery_qc_inconsistent"
    )
    missing = project_release_readiness(None)
    assert missing["status"] == "unverified"
    assert missing["reason_code"] == "delivery_qc_missing"
    assert missing["can_publish"] is False


def test_screening_repair_simplifies_non_converging_shot() -> None:
    feedback = build_screening_feedback(
        screening_id="screening-1",
        issues=[
            {
                "category": "camera",
                "severity": "high",
                "shot_id": "S03",
                "attempt_count": 11,
            }
        ],
    )
    repair = plan_screening_repairs(feedback, iteration=11)

    assert repair["actions"][0]["action"] == "simplify_shot"


def test_production_estimate_stays_unverified_without_prices() -> None:
    estimate = build_production_estimate(
        episode_count=12,
        shot_count=240,
        average_shot_seconds=5,
        video_acceptance_rate=0.08,
    )
    audit = audit_production_estimate(estimate)

    assert audit["gate_observations"]["production_estimate_ready"] is None
    assert estimate["attempt_policy"]["video_attempts_per_shot"] == 13


def test_aggregate_contract_requires_every_preproduction_gate() -> None:
    ready = _ready_contract(ready=True)
    blocked = _ready_contract(ready=False)

    assert ready["ready_for_media"] is True
    assert blocked["ready_for_media"] is False
    assert set(PREPRODUCTION_GATES) <= set(ready["gate_observations"])


def test_workflow_review_consumes_and_blocks_film_contract() -> None:
    ready = build_cinematic_review(
        run={
            "inputs": {
                "film_production": _ready_contract(
                    ready=True,
                    extra_observations={
                        "screening_feedback_recorded": True,
                        "production_estimate_ready": True,
                    },
                )
            }
        },
        shots=[_shot()],
        director_plan={"director_vision": _vision()},
    )
    assert ready["media_blocked"] is False
    assert ready["film_production_audit"]["valid"] is True
    assert ready["gate_observations"]["film_prompt_contract_valid"] is True
    assert ready["gate_observations"]["screening_feedback_recorded"] is True
    assert "screening_feedback_recorded" not in ready["required_gates"]
    assert "final_delivery_qc_passed" not in ready["required_gates"]

    final = build_cinematic_review(
        run={
            "inputs": {
                "film_production": _ready_contract(ready=True),
            }
        },
        shots=[_shot()],
        director_plan={"director_vision": _vision()},
        include_delivery_gates=True,
    )
    assert "final_delivery_qc_passed" in final["required_gates"]

    blocked = build_cinematic_review(
        run={"inputs": {"film_production": _ready_contract(ready=False)}},
        shots=[_shot()],
        director_plan={"director_vision": _vision()},
    )
    assert blocked["media_blocked"] is True
    assert blocked["gate_observations"]["visual_bible_locked"] is False


@pytest.mark.asyncio
async def test_media_handler_rejects_unready_film_contract_before_model_dispatch() -> None:
    from novelvideo.workflow_runtime import executor

    with pytest.raises(executor.WorkflowStepExecutionError) as raised:
        await executor._media_generation_handler(
            {
                "run_mode": "auto",
                "inputs": {
                    "director_intent_contract": {"delivery_level": "media_draft"},
                    "film_production": _ready_contract(ready=False),
                },
                "artifacts": {
                    "story_and_shots": {
                        "plan": {"shots": [_shot()]},
                        "canvas_receipt": {"created_node_ids": ["shot-1"]},
                    }
                },
            },
            {"id": "media_generation"},
        )

    assert raised.value.code == "workflow_film_production_not_ready"


@pytest.mark.asyncio
async def test_quality_review_blocks_unready_film_contract_even_when_not_final() -> None:
    from novelvideo.production.director_plan import build_director_plan
    from novelvideo.workflow_runtime import executor

    intent = {
        "delivery_level": "media_draft",
        "quality_gates": [
            "director_plan_valid",
            "canvas_structure_receipt",
            "story_and_shots_complete",
        ],
    }
    plan = build_director_plan(
        objective="生成媒体草稿",
        director_intent_contract=intent,
    )
    with pytest.raises(executor.WorkflowStepExecutionError) as raised:
        await executor._quality_review_handler(
            {
                "run_mode": "auto",
                "contract_version": 1,
                "inputs": {
                    "director_mode": "production",
                    "director_plan": plan,
                    "director_intent_contract": intent,
                    "film_production": _ready_contract(ready=False),
                },
                "artifacts": {
                    "story_and_shots": {
                        "canvas_receipt": {"created_node_ids": ["shot-1", "shot-2"]},
                        "plan": {
                            "shots": [
                                {"shot_id": "shot-1", "prompt": "镜头一"},
                                {"shot_id": "shot-2", "prompt": "镜头二"},
                            ]
                        },
                    },
                    "media_generation": {
                        "media_assets": [{"node_id": "shot-1"}, {"node_id": "shot-2"}]
                    },
                },
            },
            {"id": "quality_review"},
        )

    assert raised.value.code == "workflow_quality_gates_failed"
    assert "visual_bible_locked" in raised.value.details["quality_gate_report"][
        "blocking_gates"
    ]


@pytest.mark.asyncio
async def test_final_film_quality_review_requires_final_delivery_gate() -> None:
    from novelvideo.production.director_plan import build_director_plan
    from novelvideo.workflow_runtime import executor

    intent = {
        "delivery_level": "final_film",
        "quality_gates": [
            "director_plan_valid",
            "canvas_structure_receipt",
            "story_and_shots_complete",
        ],
    }
    plan = build_director_plan(
        objective="生成最终成片",
        director_intent_contract=intent,
    )
    with pytest.raises(executor.WorkflowStepExecutionError) as raised:
        await executor._quality_review_handler(
            {
                "run_mode": "auto",
                "contract_version": 1,
                "inputs": {
                    "director_mode": "production",
                    "director_plan": plan,
                    "director_intent_contract": intent,
                    "film_production": _ready_contract(ready=True),
                },
                "artifacts": {
                    "story_and_shots": {
                        "canvas_receipt": {"created_node_ids": ["shot-1", "shot-2"]},
                        "plan": {
                            "shots": [
                                {"shot_id": "shot-1", "prompt": "镜头一"},
                                {"shot_id": "shot-2", "prompt": "镜头二"},
                            ]
                        },
                    },
                    "media_generation": {
                        "media_assets": [{"node_id": "shot-1"}, {"node_id": "shot-2"}]
                    },
                },
            },
            {"id": "quality_review"},
        )

    assert raised.value.code == "workflow_quality_gates_failed"
    assert "final_delivery_qc_passed" in raised.value.details[
        "quality_gate_report"
    ]["blocking_gates"]
