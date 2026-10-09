"""Generation-time completeness checks, separate from historical result parsing."""

from novelvideo.ports.story_script import FreezoneStoryScriptGenerateData
from pydantic_ai import ModelRetry
import re


def _asset_names(value: str) -> set[str]:
    return {
        part.strip()
        for part in re.split(r"[、，,；;\n]+", str(value or ""))
        if part.strip() and part.strip().casefold() not in {"无", "没有", "none", "n/a"}
    }


def require_script_director_plan(data: FreezoneStoryScriptGenerateData) -> FreezoneStoryScriptGenerateData:
    plan = data.director_plan
    issues: list[str] = []
    for key in ("story_promise", "protagonist_goal", "core_conflict", "ending_change", "rhythm_curve", "sound_plan"):
        if not getattr(plan, key).strip():
            issues.append(f"director_plan.{key} 未填写")
    for key in ("visual_style", "texture", "color_progression", "lighting", "camera_language"):
        if not getattr(plan.visual_bible, key).strip():
            issues.append(f"visual_bible.{key} 未填写")
    shots = [row.shot_no for row in data.rows]
    if len(set(shots)) != len(shots) or any(shot <= 0 for shot in shots):
        issues.append("镜号必须为唯一的正整数")
    available = set(shots)
    covered: set[int] = set()
    ids: set[str] = set()
    if not plan.sequences:
        issues.append("sequences 未规划")
    for index, sequence in enumerate(plan.sequences, start=1):
        if not sequence.sequence_id.strip() or sequence.sequence_id in ids:
            issues.append(f"序列 {index} 编号缺失或重复")
        ids.add(sequence.sequence_id)
        if not sequence.dramatic_goal.strip():
            issues.append(f"序列 {index} 缺少观看目标")
        if not sequence.shot_nos:
            issues.append(f"序列 {index} 未指定镜号")
        if len(set(sequence.shot_nos)) != len(sequence.shot_nos):
            issues.append(f"序列 {index} 重复引用镜号")
        if set(sequence.shot_nos) - available:
            issues.append(f"序列 {index} 引用了不存在的镜号")
        covered.update(sequence.shot_nos)
    if available - covered:
        issues.append("部分镜头未归入序列")
    for row in data.rows:
        for key in ("shot_purpose", "cut_reason", "start_state", "end_state"):
            if not getattr(row, key).strip():
                issues.append(f"镜 {row.shot_no} 的 {key} 未填写")
        prop_tags = row.prop_tags.strip().casefold()
        if prop_tags and prop_tags not in {"无", "没有", "none", "n/a"}:
            for key in ("prop_state_start", "prop_state_change", "prop_state_end"):
                if not getattr(row, key).strip():
                    issues.append(f"镜 {row.shot_no} 的关键道具 {key} 未填写")
        for role, tags_field, definitions_field in (
            ("场景", "scene_tags", "scene_descriptions"),
            ("道具", "prop_tags", "prop_descriptions"),
        ):
            names = _asset_names(getattr(row, tags_field))
            definitions = getattr(row, definitions_field) or {}
            missing = sorted(
                name for name in names
                if str(definitions.get(name) or "").strip().casefold()
                in {"", "无", "没有", "none", "n/a", name.casefold()}
            )
            if missing:
                issues.append(
                    f"镜 {row.shot_no} 的{role}基准设计缺少：{'、'.join(missing[:8])}；"
                    "请写可复用的空间/外观、材质和结构说明"
                )
    if issues:
        raise ModelRetry(
            "请补齐导演规划并返回完整结果：" + "；".join(issues[:20])
            + "。非叙事作品可说明表达主体、关系与发展；无对白或音乐可明确写静默计划。"
            "允许序列交叉覆盖，不强制冲突、反转、景别变化、电影手法或对白比例。"
        )
    return data
