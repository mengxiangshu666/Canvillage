"""Deterministic, hashable execution contract for one cinematic shot."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .cinematic_contract import build_cinematic_contract


SHOT_CONTRACT_SCHEMA = "production.shot-contract.v1"

#: 相邻镜头交接意图：``continuous`` = 下一镜从本镜结束状态开始（首尾帧可钉死接缝）；
#: ``cut`` = 硬切，下一镜自起（保留 first_frame，才能继续挂参考图与音频）。
SEAM_CONTINUOUS = "continuous"
SEAM_CUT = "cut"


def _text(value: object, limit: int | None = 2_000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _bindings(value: object) -> dict[str, list[str]]:
    source = value if isinstance(value, Mapping) else {}
    result: dict[str, list[str]] = {}
    for raw_role, raw_values in source.items():
        role = _text(raw_role, 80).casefold()
        values = raw_values if isinstance(raw_values, (list, tuple, set)) else [raw_values]
        items = list(
            dict.fromkeys(
                _text(item, 300)
                for item in values
                if _text(item, 300)
            )
        )
        if role and items:
            result[role] = items[:32]
    return result


def _creative_handoff(value: object) -> dict[str, Any]:
    """Keep director intent structured so outgoing cut plans stay out of motion prose."""

    source = _mapping(value)
    result: dict[str, Any] = {}
    for field, alias in (
        ("shot_purpose", "shotPurpose"),
        ("film_language", "filmLanguage"),
        ("content_intent", "contentIntent"),
        ("cut_reason", "cutReason"),
    ):
        raw = source.get(field) or source.get(alias)
        text = _text(raw, None) if isinstance(raw, str) else ""
        if text:
            result[field] = text
    raw_sequence_ids = source.get("sequenceIds") or source.get("sequence_ids")
    if isinstance(raw_sequence_ids, (list, tuple)):
        sequence_ids = list(dict.fromkeys(
            item.strip() for item in raw_sequence_ids if isinstance(item, str) and item.strip()
        ))
        if sequence_ids:
            result["sequence_ids"] = sequence_ids
    raw_scenes = source.get("sceneDescriptions") or source.get("scene_descriptions")
    if isinstance(raw_scenes, Mapping):
        scenes = {
            name.strip(): _text(description, None)
            for name, description in raw_scenes.items()
            if isinstance(name, str) and name.strip()
            and isinstance(description, str) and description.strip()
        }
        if scenes:
            result["scene_descriptions"] = scenes
    raw_context = source.get("directorContext") or source.get("director_context")
    if isinstance(raw_context, Mapping):
        context: dict[str, Any] = {}
        for field, alias in (
            ("story_promise", "storyPromise"),
            ("protagonist_goal", "protagonistGoal"),
            ("core_conflict", "coreConflict"),
            ("ending_change", "endingChange"),
            ("rhythm_curve", "rhythmCurve"),
            ("sound_plan", "soundPlan"),
        ):
            raw = raw_context.get(field) or raw_context.get(alias)
            if isinstance(raw, str) and _text(raw, None):
                context[field] = _text(raw, None)
        raw_visual = raw_context.get("visualBible") or raw_context.get("visual_bible")
        if isinstance(raw_visual, Mapping):
            visual: dict[str, str] = {}
            for field, alias in (
                ("visual_style", "visualStyle"), ("texture", "texture"),
                ("color_progression", "colorProgression"), ("lighting", "lighting"),
                ("camera_language", "cameraLanguage"),
            ):
                raw = raw_visual.get(field) or raw_visual.get(alias)
                if isinstance(raw, str) and _text(raw, None):
                    visual[field] = _text(raw, None)
            if visual:
                context["visual_bible"] = visual
        raw_sequences = raw_context.get("sequences")
        if isinstance(raw_sequences, (list, tuple)):
            sequences: list[dict[str, Any]] = []
            for item in raw_sequences:
                if not isinstance(item, Mapping):
                    continue
                sequence_id = item.get("sequenceId", item.get("sequence_id"))
                shot_nos = item.get("shotNos", item.get("shot_nos"))
                if not isinstance(sequence_id, str) or not sequence_id.strip() or not isinstance(shot_nos, (list, tuple)):
                    continue
                numbers = [number for number in shot_nos if type(number) is int and number > 0]
                if not numbers:
                    continue
                sequence: dict[str, Any] = {"sequence_id": _text(sequence_id, None), "shot_nos": list(dict.fromkeys(numbers))}
                for field, alias in (
                    ("title", "title"), ("dramatic_goal", "dramaticGoal"), ("resistance", "resistance"),
                    ("escalation", "escalation"), ("turn", "turn"), ("release", "release"),
                    ("staging_plan", "stagingPlan"), ("performance_plan", "performancePlan"),
                ):
                    raw = item.get(field) or item.get(alias)
                    if isinstance(raw, str) and _text(raw, None):
                        sequence[field] = _text(raw, None)
                sequences.append(sequence)
            if sequences:
                context["sequences"] = sequences
        if context:
            result["director_context"] = context
    raw_plan = source.get("keyframePlan") or source.get("keyframe_plan")
    if isinstance(raw_plan, (list, tuple)):
        plan: list[dict[str, Any]] = []
        for item in raw_plan:
            if not isinstance(item, Mapping):
                continue
            raw_state = item.get("state")
            state = _text(raw_state, None) if isinstance(raw_state, str) else ""
            if not state:
                continue
            plan.append(
                {
                    "role": _text(item.get("role"), None) if isinstance(item.get("role"), str) else "",
                    "state": state,
                    "purpose": _text(item.get("purpose"), None) if isinstance(item.get("purpose"), str) else "",
                    "required": item.get("required") is True,
                }
            )
            strategy = item.get("generation_strategy")
            framing = item.get("framing")
            if strategy in ("independent", "state_edit"):
                plan[-1]["generation_strategy"] = strategy
            if isinstance(framing, str) and _text(framing, None):
                plan[-1]["framing"] = _text(framing, None)
        if plan:
            result["keyframe_plan"] = plan
    raw_references = source.get("referenceResponsibilities") or source.get("reference_responsibilities")
    if isinstance(raw_references, (list, tuple)):
        references: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        for item in raw_references:
            if not isinstance(item, Mapping):
                continue
            scope = item.get("scope")
            number = item.get("imageNumber", item.get("image_number"))
            role = item.get("role")
            if (scope not in ("storyboard", "keyframe", "video") or type(number) is not int or number < 1
                or role not in (
                    "character", "scene", "prop", "reference", "opening_frame", "state_frame",
                    "end_frame", "continuity_frame", "motion", "style", "frame_design",
                )):
                continue
            responsibility = item.get("responsibility")
            prohibited = item.get("prohibited")
            if not isinstance(responsibility, str) or not responsibility.strip() or not isinstance(prohibited, str) or not prohibited.strip():
                continue
            if (scope, number) in seen:
                continue
            seen.add((scope, number))
            reference = {
                "scope": scope, "image_number": number, "role": role,
                "name": _text(item.get("name"), None) if isinstance(item.get("name"), str) else "",
                "responsibility": _text(responsibility, None), "prohibited": _text(prohibited, None),
            }
            node_id = item.get("sourceNodeId") or item.get("source_node_id")
            if isinstance(node_id, str) and node_id.strip():
                reference["source_node_id"] = _text(node_id, 120)
            references.append(reference)
        if references:
            result["reference_responsibilities"] = references
    return result


def build_shot_contract(shot: Mapping[str, Any], *, index: int = 1) -> dict[str, Any]:
    """Compile scattered storyboard fields into one auditable shot contract."""

    shot_id = _text(shot.get("shot_id") or shot.get("shotId"), 120) or f"S{max(1, index):02d}"
    try:
        duration = float(shot.get("duration_seconds") or shot.get("durationSeconds") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    subject = _text(shot.get("subject") or shot.get("title"), None)
    primary_action = _text(shot.get("primary_action") or shot.get("action"), None)
    camera_motion = _text(
        shot.get("primary_camera_motion")
        or shot.get("camera_motion")
        or shot.get("cameraMovement"),
        None,
    )
    start_state = _text(
        shot.get("start_state") or shot.get("first_frame") or shot.get("firstFrame"),
        None,
    )
    end_state = _text(
        shot.get("end_state") or shot.get("last_frame") or shot.get("lastFrame"),
        None,
    )
    continuity_in = _mapping(shot.get("continuity_in") or shot.get("continuityIn"))
    continuity_out = _mapping(shot.get("continuity_out") or shot.get("continuityOut"))
    reference_bindings = _bindings(
        shot.get("reference_bindings") or shot.get("referenceBindings")
    )
    execution_prompt = _text(
        shot.get("execution_prompt") or shot.get("executionPrompt"),
        None,
    )
    creative_handoff = _creative_handoff(
        shot.get("creative_handoff") or shot.get("creativeHandoff")
    )
    sound_source = shot.get("sound_cues") or shot.get("soundCues") or []
    sound_cues = [
        _text(item, None)
        for item in (sound_source if isinstance(sound_source, (list, tuple)) else [sound_source])
        if _text(item, None)
    ][:20]
    director_vision = shot.get("director_vision") or shot.get("directorVision")
    project_dna = shot.get("project_dna") or shot.get("projectDna")
    cinematic = build_cinematic_contract(
        shot=shot,
        director_vision=director_vision if isinstance(director_vision, Mapping) else None,
        project_dna=project_dna if isinstance(project_dna, Mapping) else None,
        creation_stage=shot.get("creation_stage") or shot.get("creationStage") or "",
    )
    issues: list[dict[str, str]] = []
    for field, value, message in (
        ("duration_seconds", duration > 0, "镜头时长必须大于 0"),
        ("subject", bool(subject), "镜头必须有明确主体"),
        ("primary_action", bool(primary_action), "镜头必须有一个主要动作"),
        ("primary_camera_motion", bool(camera_motion), "镜头必须有一个主要运镜"),
        ("start_state", bool(start_state), "镜头必须声明可见起始状态"),
        ("end_state", bool(end_state), "镜头必须声明可见结束状态"),
    ):
        if not value:
            issues.append(
                {
                    "code": "shot_contract_field_missing",
                    "field": field,
                    "message": message,
                }
            )
    contract: dict[str, Any] = {
        "schema": SHOT_CONTRACT_SCHEMA,
        "shot_id": shot_id,
        "duration_seconds": duration,
        "subject": subject,
        "start_state": start_state,
        "primary_action": primary_action,
        "primary_camera_motion": camera_motion,
        "end_state": end_state,
        "reference_bindings": reference_bindings,
        **({"execution_prompt": execution_prompt} if execution_prompt else {}),
        "continuity_in": continuity_in,
        "continuity_out": continuity_out,
        "sound_cues": sound_cues,
        **({"creative_handoff": creative_handoff} if creative_handoff else {}),
        **({"cinematic": cinematic} if cinematic else {}),
        "ready": not issues,
        "issues": issues,
    }
    contract["contract_hash"] = hashlib.sha256(
        json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()[:24]
    return contract


def validate_shot_contract(value: object) -> list[dict[str, str]]:
    """Validate a persisted contract including its content hash."""

    if not isinstance(value, Mapping):
        return [
            {
                "code": "shot_contract_invalid",
                "field": "shot_contract",
                "message": "shot_contract 必须是对象",
            }
        ]
    raw = dict(value)
    if raw.get("schema") != SHOT_CONTRACT_SCHEMA:
        return [
            {
                "code": "shot_contract_schema_invalid",
                "field": "schema",
                "message": "shot_contract schema 不受支持",
            }
        ]
    saved_hash = _text(raw.pop("contract_hash", ""), 64)
    expected_hash = hashlib.sha256(
        json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()[:24]
    issues = [dict(item) for item in raw.get("issues", []) if isinstance(item, Mapping)]
    if "cinematic" in raw and not isinstance(raw.get("cinematic"), Mapping):
        issues.append(
            {
                "code": "shot_contract_cinematic_invalid",
                "field": "cinematic",
                "message": "shot_contract cinematic 必须是对象",
            }
        )
    if not saved_hash or saved_hash != expected_hash:
        issues.append(
            {
                "code": "shot_contract_hash_mismatch",
                "field": "contract_hash",
                "message": "shot_contract 内容与哈希不一致",
            }
        )
    if raw.get("ready") is not True and not issues:
        issues.append(
            {
                "code": "shot_contract_not_ready",
                "field": "ready",
                "message": "shot_contract 尚未通过执行准入",
            }
        )
    return issues


def decode_shot_contract(value: object) -> dict[str, Any] | None:
    """Return a persisted contract object, or ``None`` for legacy beats."""
    if isinstance(value, Mapping):
        return dict(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return dict(decoded) if isinstance(decoded, Mapping) else None


def serialize_shot_contract(value: object) -> str:
    """Serialize only mapping contracts for deterministic DB persistence."""
    contract = decode_shot_contract(value)
    return (
        json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if contract
        else ""
    )


def shot_contract_ready(value: object) -> bool:
    """Whether a contract is complete and its content hash is intact."""
    contract = decode_shot_contract(value)
    return bool(contract) and not validate_shot_contract(contract)


def _beat_contract(beat: object) -> dict[str, Any] | None:
    if not isinstance(beat, Mapping):
        return None
    return decode_shot_contract(
        beat.get("shot_contract") or beat.get("shot_contract_json")
    )


def shot_handoff_seam(beat: object) -> str:
    """本镜交给下一镜的接缝意图（``continuity_out.seam``）。"""

    contract = _beat_contract(beat)
    handoff = contract.get("continuity_out") if contract else None
    if not isinstance(handoff, Mapping):
        return ""
    return _text(handoff.get("seam"), limit=40).casefold()


def shot_incoming_seam(beat: object) -> str:
    """本镜承接上一镜的接缝意图（``continuity_in.seam``）。

    连续接缝才允许消费上一镜的真实尾帧：硬切镜必须保留自己的规划首帧，
    否则会把上一镜的结束构图拉进一个本该自起的镜头。
    """

    contract = _beat_contract(beat)
    handoff = contract.get("continuity_in") if contract else None
    if not isinstance(handoff, Mapping):
        return ""
    return _text(handoff.get("seam"), limit=40).casefold()


__all__ = [
    "SEAM_CONTINUOUS",
    "SEAM_CUT",
    "SHOT_CONTRACT_SCHEMA",
    "build_shot_contract",
    "decode_shot_contract",
    "serialize_shot_contract",
    "shot_contract_ready",
    "shot_handoff_seam",
    "shot_incoming_seam",
    "validate_shot_contract",
]
