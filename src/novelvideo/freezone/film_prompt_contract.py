"""Compile the canonical 8/6 prompt arrays into auditable prompt bundles.

The product prompt format is intentionally preserved:

* ``shot_prompt``: eight ordered segments joined by `` + ``.
* ``video_motion_prompt``: six ordered segments joined by `` + ``.

This module adds deterministic inspection and positive-constraint rewriting on
top of the existing script contract.  It does not replace the generator or
invent a second prompt format.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from novelvideo.freezone.script_contract import (
    MOTION_SEGMENT_ORDER,
    SHOT_SEGMENT_ORDER,
    character_ids_in_card,
    classify_segments,
    split_prompt_segments,
    validate_script_rows,
)


FILM_PROMPT_SCHEMA = "film_prompt_bundle.v1"
FILM_PROMPT_AUDIT_SCHEMA = "film_prompt_audit.v1"


def append_script_shot_visual_context(prompt: str, row: Mapping[str, Any]) -> str:
    """Project only authored shot light/style into video; never infer from free prose."""
    def visual_text(value: object) -> str:
        text = value.strip() if isinstance(value, str) else ""
        return "" if text.casefold() in {"", "无", "没有", "none", "n/a", "-", "—"} else text

    if not prompt.strip():
        return prompt
    image_prompt = visual_text(row.get("shot_prompt")) or visual_text(row.get("visual_description"))
    lines: list[str] = []
    bodies: list[str] = []
    for segment in split_prompt_segments(image_prompt):
        parts = re.split(r"[:：\]]", segment, maxsplit=1)
        label = parts[0].lstrip("[").strip()
        body = visual_text(parts[1]) if len(parts) == 2 else ""
        if label.startswith(("光影", "视觉风格", "质感")) and body:
            lines.append(f"{label}：{body}")
            bodies.append(body)
    mood = visual_text(row.get("lighting_mood"))
    if mood and mood not in bodies:
        lines.append(f"本镜光影氛围：{mood}")
    if not lines:
        return prompt
    context = (
        "本镜已定美术（光色与质感按以下设计；运动稿中有因果的光线变化继续发生，"
        "不重置参考中的身份、空间或姿态）：\n" + "\n".join(lines)
    )
    return prompt if context in prompt else f"{prompt}\n{context}"

_NEGATIVE_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"不要(?:出现)?文字", re.IGNORECASE), "画面中所有表面干净无文字"),
    (re.compile(r"不要(?:出现)?字幕", re.IGNORECASE), "画面中不承载字幕文字"),
    (re.compile(r"不要(?:出现)?水印", re.IGNORECASE), "画面表面干净、无水印"),
    (re.compile(r"不要(?:出现)?logo", re.IGNORECASE), "画面表面干净、无品牌标识"),
    (re.compile(r"不要(?:出现)?多余人物", re.IGNORECASE), "画面人数与合同一致"),
    (re.compile(r"不要(?:出现)?重复人物", re.IGNORECASE), "画面中每个角色只出现一次"),
    (re.compile(r"不要变形", re.IGNORECASE), "人体结构、肢体比例和接触点保持真实"),
    (re.compile(r"不要崩坏", re.IGNORECASE), "人体结构、材质和空间关系保持完整"),
    (re.compile(r"不要闪烁", re.IGNORECASE), "画面光照与曝光保持连续稳定"),
    (re.compile(r"不要糊", re.IGNORECASE), "主体边缘、面部和关键道具保持清晰"),
)

_NEGATIVE_MARKERS = (
    "不要",
    "不准",
    "禁止",
    "避免",
    "no ",
    "not ",
    "without ",
)


def _text(value: object, *, limit: int = 5000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _row_id(row: Mapping[str, Any], index: int) -> str:
    return _text(
        row.get("shot_id") or row.get("shotId") or row.get("display_shot_no"),
        limit=160,
    ) or f"S{index:02d}"


def _segment_map(
    text: object,
    order: tuple[tuple[str, tuple[str, ...]], ...],
) -> dict[str, str]:
    source = _text(text, limit=12_000)
    segments = split_prompt_segments(source)
    roles = classify_segments(source, order)
    result: dict[str, str] = {}
    for position, role in enumerate(roles):
        if role and position < len(segments):
            result[role] = segments[position]
    return result


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24] if value else ""


def _join_segment_map(
    segments: Mapping[str, str],
    order: tuple[tuple[str, tuple[str, ...]], ...],
) -> str:
    return " + ".join(
        f"[{segments[role]}]"
        for role, _ in order
        if _text(segments.get(role))
    )


def rewrite_video_positive_constraints(value: object) -> str:
    """Turn common negative video instructions into positive visible facts."""

    text = _text(value, limit=12_000)
    for pattern, replacement in _NEGATIVE_REWRITES:
        text = pattern.sub(replacement, text)
    return text


def _negative_marker_count(value: str) -> int:
    lowered = value.casefold()
    return sum(lowered.count(marker) for marker in _NEGATIVE_MARKERS)


def build_film_prompt_bundle(
    rows: object,
    *,
    rewrite_video_negatives: bool = True,
) -> dict[str, Any]:
    """Compile prompt rows while preserving the existing 8/6 format."""

    source_rows = [
        dict(row)
        for row in (rows if isinstance(rows, (list, tuple)) else [])
        if isinstance(row, Mapping)
    ]
    script_report = validate_script_rows(source_rows)
    records: list[dict[str, Any]] = []
    for index, row in enumerate(source_rows, start=1):
        shot_prompt = _text(row.get("shot_prompt") or row.get("shotPrompt"), limit=20_000)
        motion_prompt = _text(
            row.get("video_motion_prompt") or row.get("videoMotionPrompt"),
            limit=20_000,
        )
        shot_segments = _segment_map(shot_prompt, SHOT_SEGMENT_ORDER)
        motion_segments = _segment_map(motion_prompt, MOTION_SEGMENT_ORDER)
        if rewrite_video_negatives and motion_prompt:
            rewritten = rewrite_video_positive_constraints(motion_prompt)
            if rewritten != motion_prompt:
                row = {**row, "video_motion_prompt": rewritten}
                motion_prompt = rewritten
                motion_segments = _segment_map(motion_prompt, MOTION_SEGMENT_ORDER)
        identity_card = _text(shot_segments.get("character_card"))
        records.append(
            {
                "shot_id": _row_id(row, index),
                "display_shot_no": _text(row.get("display_shot_no") or row.get("shot_no"), limit=80),
                "duration_seconds": row.get("duration_seconds") or row.get("duration"),
                "shot_prompt": shot_prompt,
                "video_motion_prompt": motion_prompt,
                "shot_segments": shot_segments,
                "motion_segments": motion_segments,
                "character_ids": character_ids_in_card(identity_card),
                "identity_card_hash": _hash_text(identity_card),
                "style_segment_hash": _hash_text(_text(shot_segments.get("style"))),
                "technical_segment_hash": _hash_text(_text(shot_segments.get("technical"))),
                "negative_marker_count": _negative_marker_count(motion_prompt),
            }
        )
    result: dict[str, Any] = {
        "schema": FILM_PROMPT_SCHEMA,
        "rows": records,
        "script_contract": script_report.as_dict(),
        "rewrite_policy": "positive_visible_facts_for_video",
    }
    revision_payload = {
        "schema": result["schema"],
        "rows": records,
        "rewrite_policy": result["rewrite_policy"],
    }
    result["contract_revision"] = (
        "film-prompt.v1:"
        + hashlib.sha256(
            json.dumps(
                revision_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:20]
    )
    return result


def validate_film_prompt_bundle(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("film_prompt_bundle must be an object")
    bundle = deepcopy(dict(value))
    if bundle.get("schema") != FILM_PROMPT_SCHEMA:
        raise ValueError("film_prompt_bundle schema is unsupported")
    rows = bundle.get("rows")
    if not isinstance(rows, list):
        raise ValueError("film_prompt_bundle rows must be a list")
    payload = {
        "schema": bundle.get("schema"),
        "rows": rows,
        "rewrite_policy": bundle.get("rewrite_policy"),
    }
    expected = (
        "film-prompt.v1:"
        + hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:20]
    )
    if _text(bundle.get("contract_revision"), limit=100) != expected:
        raise ValueError("film_prompt_bundle contract_revision does not match its contents")
    return bundle


def audit_film_prompt_bundle(value: object) -> dict[str, Any]:
    try:
        bundle = validate_film_prompt_bundle(value)
    except ValueError as exc:
        return {
            "schema": FILM_PROMPT_AUDIT_SCHEMA,
            "passed": False,
            "issues": [{"code": "prompt.contract_invalid", "message": str(exc)}],
            "gate_observations": {"film_prompt_contract_valid": False},
        }

    issues: list[dict[str, Any]] = []
    script_report = _mapping(bundle.get("script_contract"))
    for issue in script_report.get("issues") or []:
        if isinstance(issue, Mapping) and not issue.get("fixed"):
            issues.append(
                {
                    "code": f"prompt.{issue.get('rule_id') or 'script_contract'}",
                    "message": _text(issue.get("message"), limit=1200),
                    "shot_id": _text(issue.get("shot_no"), limit=120),
                    "fix": _text(issue.get("fix"), limit=1200),
                }
            )
    for record in bundle.get("rows") or []:
        if not isinstance(record, Mapping):
            continue
        shot_id = _text(record.get("shot_id"), limit=160)
        shot_segments = _mapping(record.get("shot_segments"))
        motion_segments = _mapping(record.get("motion_segments"))
        if not shot_segments:
            issues.append(
                {
                    "code": "prompt.shot_segments_missing",
                    "shot_id": shot_id,
                    "message": "没有可解析的 8 段式 shot_prompt",
                    "fix": "按 8 段顺序重写，并用 ` + ` 连接。",
                }
            )
        if not motion_segments:
            issues.append(
                {
                    "code": "prompt.motion_segments_missing",
                    "shot_id": shot_id,
                    "message": "没有可解析的 6 段式 video_motion_prompt",
                    "fix": "按 6 段顺序重写，并用 ` + ` 连接。",
                }
            )
        if not _text(motion_segments.get("camera")):
            issues.append(
                {
                    "code": "prompt.camera_missing",
                    "shot_id": shot_id,
                    "message": "运动稿缺少摄影观察与运动安排",
                    "fix": "按观看目的写清起始机位与取景、固定观察或相对路线、速度与变化触发、揭示时点和结束构图；连续变化有可见动机，避免同时发出矛盾指令。",
                }
            )
        if not _text(motion_segments.get("sound")):
            issues.append(
                {
                    "code": "prompt.sound_missing",
                    "shot_id": shot_id,
                    "message": "运动稿缺少声音层",
                    "fix": "补环境声、画内声源或与动作绑定的音效。",
                }
            )
        if not _text(motion_segments.get("duration")):
            issues.append(
                {
                    "code": "prompt.duration_missing",
                    "shot_id": shot_id,
                    "message": "运动稿缺少时长段",
                    "fix": "按本镜内容、自然表演与所选模型能力确认生成时长，与节点秒数一致写入时长段；不照抄固定秒数或用空动作填时长。",
                }
            )
        if _negative_marker_count(_text(record.get("video_motion_prompt"))) > 2:
            issues.append(
                {
                    "code": "prompt.video_negative_list",
                    "shot_id": shot_id,
                    "message": "视频运动稿仍堆积负向禁止词",
                    "fix": "把禁止项改成画面中必须出现的正向事实。",
                }
            )
    failed = bool(issues)
    return {
        "schema": FILM_PROMPT_AUDIT_SCHEMA,
        "passed": not failed,
        "issues": issues,
        "row_count": len(bundle.get("rows") or []),
        "gate_observations": {"film_prompt_contract_valid": not failed},
    }


def compile_film_prompt_rows(
    rows: object,
    *,
    rewrite_video_negatives: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return rewritten rows and an inspectable bundle for the task runner."""

    bundle = build_film_prompt_bundle(
        rows,
        rewrite_video_negatives=rewrite_video_negatives,
    )
    rewritten = []
    source_rows = rows if isinstance(rows, (list, tuple)) else []
    for row, record in zip(source_rows, bundle["rows"], strict=False):
        if not isinstance(row, Mapping) or not isinstance(record, Mapping):
            continue
        updated = dict(row)
        updated["shot_prompt"] = record.get("shot_prompt") or updated.get("shot_prompt")
        updated["video_motion_prompt"] = (
            record.get("video_motion_prompt") or updated.get("video_motion_prompt")
        )
        rewritten.append(updated)
    return rewritten, bundle


__all__ = [
    "FILM_PROMPT_AUDIT_SCHEMA",
    "FILM_PROMPT_SCHEMA",
    "audit_film_prompt_bundle",
    "build_film_prompt_bundle",
    "compile_film_prompt_rows",
    "rewrite_video_positive_constraints",
    "validate_film_prompt_bundle",
]
