"""Source coverage ledger for script generation.

Machine coverage is deliberately separate from human content review. Matching
shot numbers can prove a structural mapping, never that every story event or
line of dialogue was preserved.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

SCHEMA = "script-source-coverage.v1"
_EXPLICIT_SHOT_RE = re.compile(r"^\s*镜\s*(\d+)\s*(?:[：:、.\-]|$)")
_PLACEHOLDERS = {"", "无", "没有", "未知", "unknown", "n/a", "none", "-", "—"}


def _source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _row_shot_no(row: Mapping[str, Any]) -> str:
    display = _text(row.get("display_shot_no"))
    raw = row.get("shot_no")
    canonical = (
        _text(raw) if isinstance(raw, str) else str(raw) if raw is not None else ""
    )
    match = re.search(r"\d+", display or canonical)
    return match.group(0) if match else canonical


def _explicit_units(source_text: str) -> list[dict[str, Any]]:
    lines = source_text.splitlines()
    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = _EXPLICIT_SHOT_RE.match(line)
        if match:
            starts.append((index, match.group(1)))
    units: list[dict[str, Any]] = []
    for position, (start, shot_no) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        body = "\n".join(lines[start:end]).strip()
        units.append(
            {
                "unit_id": f"shot-{shot_no}",
                "kind": "explicit_shot",
                "source_hash": _source_hash(body),
                "line_start": start + 1,
                "line_end": end,
                "declared_shot_no": shot_no,
                "excerpt": body[:400],
                "shot_ids": [],
                "status": "unmapped",
            }
        )
    return units


def _paragraph_units(source_text: str) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    paragraph_index = 0
    for line_number, line in enumerate(source_text.splitlines(), start=1):
        if not line.strip():
            continue
        paragraph_index += 1
        units.append(
            {
                "unit_id": f"paragraph-{paragraph_index}",
                "kind": "paragraph",
                "source_hash": _source_hash(line.strip()),
                "line_start": line_number,
                "line_end": line_number,
                "declared_shot_no": "",
                "excerpt": line.strip()[:400],
                "shot_ids": [],
                "status": "unverifiable",
            }
        )
    return units


def _row_refs(row: Mapping[str, Any]) -> list[str]:
    raw = row.get("source_unit_ids")
    if isinstance(raw, list):
        return [str(item) for item in raw if _text(item) not in _PLACEHOLDERS]
    single = _text(row.get("source_unit_id"))
    return [single] if single else []


def build_source_coverage(
    source_text: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    input_kind: str = "text",
    transcript_available: bool = False,
) -> dict[str, Any]:
    """Build a deterministic coverage ledger without claiming semantic fidelity."""
    text = source_text or ""
    units = _explicit_units(text)
    if not units:
        units = _paragraph_units(text)
    explicit = bool(units) and units[0]["kind"] == "explicit_shot"
    by_shot = {
        unit["declared_shot_no"]: unit for unit in units if unit.get("declared_shot_no")
    }
    by_id = {unit["unit_id"]: unit for unit in units}
    issues: list[dict[str, Any]] = []
    seen_rows: set[int] = set()
    for row_index, row in enumerate(rows):
        refs = _row_refs(row)
        if refs:
            for ref in refs:
                unit = by_id.get(ref)
                if unit is None:
                    issues.append(
                        _issue(
                            "row_anchor_unknown",
                            f"第 {row_index + 1} 行引用的来源单元 {ref} 不存在",
                            row_index=row_index,
                        )
                    )
                    continue
                shot_id = _text(row.get("shot_id")) or _row_shot_no(row)
                if shot_id and shot_id not in unit["shot_ids"]:
                    unit["shot_ids"].append(shot_id)
                seen_rows.add(row_index)
            continue
        if explicit:
            shot_no = _row_shot_no(row)
            unit = by_shot.get(shot_no)
            if unit is None:
                issues.append(
                    _issue(
                        "row_not_in_source",
                        f"第 {row_index + 1} 行的镜号 {shot_no or '(空)'} 在来源里找不到对应镜号",
                        row_index=row_index,
                    )
                )
                continue
            shot_id = _text(row.get("shot_id")) or shot_no
            if shot_id not in unit["shot_ids"]:
                unit["shot_ids"].append(shot_id)
            seen_rows.add(row_index)
        else:
            issues.append(
                _issue(
                    "row_unanchored",
                    f"第 {row_index + 1} 行没有来源单元锚点；纯段落输入无法自动证明覆盖",
                    row_index=row_index,
                )
            )

    for unit in units:
        if len(unit["shot_ids"]) > 1:
            issues.append(
                _issue(
                    "unit_mapped_twice",
                    f"来源单元 {unit['unit_id']} 同时映射到多条分镜：{', '.join(unit['shot_ids'])}",
                    unit_id=unit["unit_id"],
                )
            )
            unit["status"] = "duplicate"
        elif explicit and len(unit["shot_ids"]) == 1:
            unit["status"] = "complete"
        elif explicit and not unit["shot_ids"]:
            issues.append(
                _issue(
                    "unit_unmapped",
                    f"来源单元 {unit['unit_id']} 没有被任何分镜覆盖",
                    unit_id=unit["unit_id"],
                )
            )
            unit["status"] = "unmapped"

    if not units:
        machine_status = "unverifiable"
        issues.append(
            _issue(
                "source_empty", "没有可核验的来源文本；不声称已覆盖剧情", row_index=-1
            )
        )
    elif not explicit:
        machine_status = "unverifiable"
    elif any(unit["status"] != "complete" for unit in units) or len(seen_rows) != len(
        rows
    ):
        machine_status = "partial"
    else:
        machine_status = "complete"

    # A machine-complete ledger only proves structural mapping. Content review
    # starts unverified until a person compares the retained events/dialogue.
    human_content_review = {
        "status": "unverified",
        "notes": "",
        "reviewed_at": None,
    }
    return {
        "schema": SCHEMA,
        "input_kind": input_kind,
        "source_hash": _source_hash(text),
        "transcript_available": bool(transcript_available),
        "units": units,
        "machine_status": machine_status,
        "human_content_review": human_content_review,
        "coverage_status": "partial"
        if machine_status == "complete"
        and not transcript_available
        and input_kind == "video"
        else machine_status,
        "issues": issues,
        "stale": False,
    }


def _issue(
    code: str, message: str, *, row_index: int | None = None, unit_id: str | None = None
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "row_index": row_index,
        "unit_id": unit_id,
    }


def attach_video_frame_units(
    coverage: Mapping[str, Any],
    manifest: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Expose actual extracted frame anchors without claiming shot coverage.

    Sampling timestamps are evidence of what was actually looked at. They do not
    prove which story event, line or shot each frame belongs to.
    """
    updated = dict(coverage)
    frames = manifest.get("frames") if isinstance(manifest, Mapping) else None
    if not isinstance(frames, list) or not frames:
        return updated
    units: list[dict[str, Any]] = []
    for index, frame in enumerate(frames):
        if not isinstance(frame, Mapping):
            continue
        units.append(
            {
                "unit_id": f"frame-{index}",
                "kind": "video_frame",
                "source_hash": str(manifest.get("source_sha256") or ""),
                "time_seconds": frame.get("time_seconds"),
                "estimated": frame.get("estimated") is True,
                "excerpt": str(frame.get("file") or ""),
                "shot_ids": [],
                "status": "unverifiable",
            }
        )
    if not units:
        return updated
    updated["units"] = units
    updated["machine_status"] = "unverifiable"
    updated["coverage_status"] = "unverifiable"
    updated["transcript_available"] = False
    updated["issues"] = [
        *list(updated.get("issues") or []),
        _issue(
            "video_shot_mapping_unverified",
            "抽帧时间锚点已保存，但没有转录或人工逐段核对，不能据此声称镜头与对白已覆盖",
            row_index=-1,
        ),
    ]
    return updated


def refresh_source_coverage(
    coverage: Mapping[str, Any], source_text: str
) -> dict[str, Any]:
    """Invalidate anchors when the source changed; never reuse old conclusions."""
    updated = dict(coverage)
    stale = str(updated.get("source_hash") or "") != _source_hash(source_text or "")
    updated["stale"] = stale
    if stale:
        updated["coverage_status"] = "invalid"
        updated["machine_status"] = "invalid"
        updated["units"] = []
        updated["issues"] = [
            *list(updated.get("issues") or []),
            _issue(
                "source_changed",
                "来源已变化，旧覆盖锚点与结论失效，需要重新生成或重新核对",
                row_index=-1,
            ),
        ]
    return updated


def mark_human_content_review(
    coverage: Mapping[str, Any],
    status: str,
    *,
    notes: str = "",
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    if status not in {"partial", "complete", "rejected"}:
        raise ValueError(
            "human content review status must be partial, complete or rejected"
        )
    updated = dict(coverage)
    updated["human_content_review"] = {
        "status": status,
        "notes": notes.strip(),
        "reviewed_at": reviewed_at,
    }
    if status == "partial" or status == "rejected":
        updated["coverage_status"] = (
            "partial" if status == "partial" else "unverifiable"
        )
    return updated
