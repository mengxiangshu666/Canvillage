"""Small, deterministic preview hooks for validated Xiaoshu rules.

Hooks are intentionally pure: they do not persist memory, mutate a canvas, or
start a generation task. Callers decide whether a preview is accepted.
"""

from __future__ import annotations

import re
from typing import Any

HOOK_PREVIEW_SCHEMA = "xiaoshu.memory_hook_preview.v1"
DURATION_HOOK_ID = "video_prompt.remove_redundant_duration"
REFERENCE_HOOK_ID = "reference.require_explicit_mapping"

_GENERIC_DURATION_RE = re.compile(
    r"(?P<verb>生成|制作|输出)\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>秒|s)\s*(?P<kind>视频|片段)?",
    re.IGNORECASE,
)
_TOTAL_DURATION_RE = re.compile(
    r"(?P<label>视频总时长|总时长|视频时长)\s*(?P<link>为|是|设为)?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>秒|s)",
    re.IGNORECASE,
)
_TIMELINE_RE = re.compile(r"(?:\[\s*\d+(?:\.\d+)?\s*[-~至]\s*\d+(?:\.\d+)?\s*s?\s*\])")


def _text(value: object, limit: int = 2_000) -> str:
    return str(value or "").strip()[:limit]


def _base_result(prompt: str, hook_id: str) -> dict[str, Any]:
    return {
        "schema": HOOK_PREVIEW_SCHEMA,
        "original": prompt,
        "transformed": prompt,
        "applied_rules": [],
        "warnings": [],
        "requires_review": False,
        "diff_summary": "",
        "hook_id": hook_id,
    }


def preview_remove_redundant_duration(
    prompt: object,
    *,
    node_type: object = "",
    duration_sec: object = None,
) -> dict[str, Any]:
    """Remove only a provably redundant total-duration declaration."""
    text = _text(prompt)
    result = _base_result(text, DURATION_HOOK_ID)
    if not text or str(node_type or "").strip() != "videoNode":
        return result
    try:
        duration = float(duration_sec)
    except (TypeError, ValueError):
        return result
    if duration <= 0:
        return result

    transformed = text
    changed = False

    def replace_generic(match: re.Match[str]) -> str:
        nonlocal changed
        if abs(float(match.group("value")) - duration) > 1e-6:
            return match.group(0)
        changed = True
        return f"{match.group('verb')}视频"

    transformed = _GENERIC_DURATION_RE.sub(replace_generic, transformed)

    def replace_total(match: re.Match[str]) -> str:
        nonlocal changed
        if abs(float(match.group("value")) - duration) > 1e-6:
            return match.group(0)
        changed = True
        return match.group("label")

    transformed = _TOTAL_DURATION_RE.sub(replace_total, transformed)
    transformed = re.sub(r"\s{2,}", " ", transformed).strip()
    if changed and transformed != text:
        result.update(
            {
                "transformed": transformed,
                "applied_rules": [DURATION_HOOK_ID],
                "diff_summary": f"移除与节点 duration_sec={duration:g} 重复的总时长声明；保留镜头时间轴。",
            }
        )
    elif _TIMELINE_RE.search(text):
        result["warnings"].append("检测到镜头时间轴，未修改时序标记。")
    return result


def preview_explicit_reference_mapping(
    prompt: object,
    *,
    references: object = None,
) -> dict[str, Any]:
    """Validate explicit reference-to-role mappings without guessing bindings."""
    text = _text(prompt)
    result = _base_result(text, REFERENCE_HOOK_ID)
    rows = references if isinstance(references, list) else []
    if not rows:
        result["warnings"].append("缺少 reference metadata，无法验证图号与角色映射。")
        result["requires_review"] = True
        return result
    seen_refs: set[str] = set()
    seen_roles: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            result["warnings"].append("reference metadata 含非对象项。")
            result["requires_review"] = True
            continue
        reference_id = _text(row.get("id") or row.get("asset_id") or row.get("index"), 200)
        role = _text(row.get("role") or row.get("character") or row.get("character_name"), 200)
        if not reference_id or not role:
            result["warnings"].append("每条 reference 必须同时提供稳定 ID 和角色名。")
            result["requires_review"] = True
            continue
        if reference_id in seen_refs:
            result["warnings"].append(f"reference {reference_id} 重复。")
            result["requires_review"] = True
        if role in seen_roles:
            result["warnings"].append(f"角色 {role} 存在多个未区分映射。")
            result["requires_review"] = True
        seen_refs.add(reference_id)
        seen_roles.add(role)
    if not result["requires_review"]:
        result["applied_rules"] = [REFERENCE_HOOK_ID]
        result["diff_summary"] = "reference metadata 已提供明确的 ID↔角色映射；未自动改写绑定。"
    return result


def preview_memory_hooks(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the two narrow hooks and combine their side-effect-free preview."""
    prompt = _text(payload.get("text"))
    duration = preview_remove_redundant_duration(
        prompt,
        node_type=payload.get("node_type"),
        duration_sec=payload.get("duration_sec"),
    )
    mapping = preview_explicit_reference_mapping(
        str(duration.get("transformed") or prompt),
        references=payload.get("references"),
    )
    transformed = str(mapping.get("transformed") or duration.get("transformed") or prompt)
    warnings = list(duration.get("warnings") or []) + list(mapping.get("warnings") or [])
    applied = list(dict.fromkeys([*(duration.get("applied_rules") or []), *(mapping.get("applied_rules") or [])]))
    return {
        "schema": HOOK_PREVIEW_SCHEMA,
        "original": prompt,
        "transformed": transformed,
        "applied_rules": applied,
        "warnings": warnings,
        "requires_review": bool(duration.get("requires_review") or mapping.get("requires_review")),
        "diff_summary": "；".join(
            item for item in (duration.get("diff_summary"), mapping.get("diff_summary")) if item
        ),
        "project_id": _text(payload.get("project_id"), 256),
        "task_stage": _text(payload.get("task_stage"), 80),
        "node_type": _text(payload.get("node_type"), 80),
    }


__all__ = [
    "DURATION_HOOK_ID",
    "HOOK_PREVIEW_SCHEMA",
    "REFERENCE_HOOK_ID",
    "preview_explicit_reference_mapping",
    "preview_memory_hooks",
    "preview_remove_redundant_duration",
]
