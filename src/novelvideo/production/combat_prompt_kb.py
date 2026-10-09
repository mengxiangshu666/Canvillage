"""Conditional combat-prompt knowledge distilled from reviewed skill archives.

The archive material is intentionally represented as typed, triggerable rules.
It is loaded only for an explicit combat intent so normal image/video prompts
do not inherit a fight template or extra negative-token noise.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any


COMBAT_KB_SCHEMA = "combat_prompt_kb.v1"
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DISTILLED_SOURCE = str(_PROJECT_ROOT / "docs" / "knowledge" / "combat_prompt_distilled.md")

SOURCE_RECORDS: tuple[dict[str, str], ...] = (
    {
        "source_id": "wuxi_skill_zip",
        "sha256": "B84BC0A098EB1AC95904362A131F828762DFAF205217F00F3D221FEC2386B033",
        "source_file": "武戏skill.zip",
    },
    {
        "source_id": "seedance_combat_skill_rar",
        "sha256": "DCF8F602CF5000F208768AC83329B32ADD9F4010DCA9199B17354FC677A27B8F",
        "source_file": "skills_base (daodu).rar",
    },
)

_COMBAT_TERMS = (
    "武戏",
    "打戏",
    "战斗",
    "搏斗",
    "打斗",
    "对打",
    "攻防",
    "拳击",
    "肉搏",
    "连招",
    "格挡",
    "闪避",
    "反击",
    "肘击",
    "飞踢",
    "拔刀",
    "挥刀",
    "斩击",
    "刺击",
    "剑气",
    "刺客",
    "擒拿",
    "武术",
)
_FAILURE_TERMS = ("站桩", "跳轴", "果冻", "肢体漂移", "背景消失", "动作过载", "武器变形", "出片失败", "修复")
_EFFECT_TERMS = ("能量", "剑气", "气劲", "特效", "尘土", "碎石", "火花", "血雾", "烟尘", "闪电")


COMBAT_PROFILE_RULES = (
    "仅在明确战斗意图时启用武戏规则。按可见的准备/攻击或防守/接触受力/重心变化/结果组织动作；短镜头只保留一条主动作链。",
    "小动作使用近景放大信息，大动作使用中景或全景交代空间；每个节拍只保留一个主要运镜。",
    "保持站位、屏幕方向、距离、朝向和道具状态；多镜头从 continuity_in 开始并交付 continuity_out。",
    "能量、碎片、布料和声音由接触或运动触发；不凭空添加特效、固定颜色或平台语法。",
)


COMBAT_RULES: tuple[dict[str, Any], ...] = (
    {
        "rule_id": "combat.causal_chain.v1",
        "trigger": "combat",
        "instruction": "按准备/起势→攻击或防守→接触与受力→重心或姿态变化→可见结果写动作。",
        "avoid": "快速连击、强大能量等抽象词替代具体动作，或出现无接触受击。",
    },
    {
        "rule_id": "combat.duration_budget.v1",
        "trigger": "combat",
        "instruction": "把节点时长当作硬预算；短镜头保留一条主动作链，时长不足时只保留最高影响的因果段并说明压缩。",
        "avoid": "把拔刀、转身、命中、收势等多个独立高潮塞在同一瞬间。",
    },
    {
        "rule_id": "combat.opening_readability.v1",
        "trigger": "combat and duration>=5",
        "instruction": "在前段给出可辨的威胁、攻防接触或明确起势；只有用户明确要求铺垫时才延后冲突。",
        "avoid": "长时间空镜或站桩后才开始战斗。",
    },
    {
        "rule_id": "combat.camera_action_match.v1",
        "trigger": "combat",
        "instruction": "拳掌肘等小动作优先近景，腾空/旋转/倒地等大动作使用中景或全景；每个节拍只保留一个主运镜。",
        "avoid": "小动作被远景稀释，或主体与镜头同时做互相冲突的复杂运动。",
    },
    {
        "rule_id": "combat.spatial_continuity.v1",
        "trigger": "combat and continuity",
        "instruction": "锁定双方屏幕方向、站位、距离、朝向和关键道具，并把上一镜结束状态作为下一镜起点。",
        "avoid": "跳轴、站位中途互换、道具凭空出现或重演上一镜。",
    },
    {
        "rule_id": "combat.action_signature.v1",
        "trigger": "combat and signature",
        "instruction": "沿用已提供的角色动作签名、重心、节奏和招式偏好；缺少依据时保持中性，不凭空扩写背景。",
        "avoid": "所有角色共用同一套招式，或为了填模板发明未提供设定。",
    },
    {
        "rule_id": "combat.effect_causality.v1",
        "trigger": "combat and effect",
        "instruction": "把能量、碎片、布料和声音绑定到身体/武器接触或环境运动，并写出来源、轨迹和结果。",
        "avoid": "脱离主体的装饰光球、凭空爆炸或固定颜色特效。",
    },
    {
        "rule_id": "combat.failure_minimal_patch.v1",
        "trigger": "combat and failure",
        "instruction": "只针对当前失败症状添加最小修复约束，优先修站桩、跳轴、漂移、背景消失、武器变化和动作过载。",
        "avoid": "把整套负面词、固定模板或未经验证的效果数字全部复制进提示词。",
    },
)


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _duration(params: Mapping[str, Any]) -> float | None:
    for key in ("duration", "duration_sec", "duration_seconds"):
        try:
            value = float(params.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _has_continuity(params: Mapping[str, Any], director_vision: Mapping[str, Any]) -> bool:
    if params.get("continuity_in") or params.get("continuity_out"):
        return True
    try:
        if int(params.get("shot_count") or 0) > 1:
            return True
    except (TypeError, ValueError):
        pass
    return bool(_mapping(director_vision.get("continuity_locks")))


def is_combat_intent(
    text: str = "",
    *,
    params: Mapping[str, Any] | None = None,
    director_vision: Mapping[str, Any] | None = None,
) -> bool:
    """Detect an explicit combat request without making combat the default."""

    clean_params = _mapping(params)
    mode = str(
        clean_params.get("combat_mode")
        or clean_params.get("action_domain")
        or clean_params.get("genre")
        or ""
    ).strip().lower()
    if mode in {"combat", "fight", "wuxia", "wuxi", "武戏", "打戏", "战斗"}:
        return True
    normalized = str(text or "").strip().lower()
    if any(term.lower() in normalized for term in _COMBAT_TERMS):
        return True
    vision = _mapping(director_vision)
    vision_text = " ".join(
        str(vision.get(key) or "").lower() for key in ("project_goal", "logline", "genre")
    )
    return any(term.lower() in vision_text for term in _COMBAT_TERMS)


def select_combat_rules(
    *,
    text: str = "",
    node_type: str = "video",
    params: Mapping[str, Any] | None = None,
    director_vision: Mapping[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Return only combat rules justified by the current request."""

    if node_type != "video":
        return []
    clean_params = _mapping(params)
    vision = _mapping(director_vision)
    if not is_combat_intent(text, params=clean_params, director_vision=vision):
        return []
    normalized = str(text or "").strip().lower()
    duration = _duration(clean_params)
    has_signature = bool(clean_params.get("action_signature") or clean_params.get("combat_signature"))
    has_signature = has_signature or bool(vision.get("character_action_signatures"))
    has_effect = bool(clean_params.get("effects") or clean_params.get("vfx") or clean_params.get("energy"))
    has_effect = has_effect or any(term.lower() in normalized for term in _EFFECT_TERMS)
    has_failure = bool(clean_params.get("failure_mode") or clean_params.get("repair_mode"))
    has_failure = has_failure or any(term.lower() in normalized for term in _FAILURE_TERMS)
    selected: list[dict[str, str]] = []
    for rule in COMBAT_RULES:
        trigger = str(rule["trigger"])
        applies = trigger == "combat"
        if trigger == "combat and duration>=5":
            applies = duration is not None and duration >= 5
        elif trigger == "combat and continuity":
            applies = _has_continuity(clean_params, vision)
        elif trigger == "combat and signature":
            applies = has_signature
        elif trigger == "combat and effect":
            applies = has_effect
        elif trigger == "combat and failure":
            applies = has_failure
        if applies:
            selected.append(
                {
                    "rule_id": str(rule["rule_id"]),
                    "trigger": trigger,
                    "instruction": str(rule["instruction"]),
                    "avoid": str(rule["avoid"]),
                }
            )
    return selected


__all__ = [
    "COMBAT_KB_SCHEMA",
    "COMBAT_PROFILE_RULES",
    "COMBAT_RULES",
    "DISTILLED_SOURCE",
    "SOURCE_RECORDS",
    "is_combat_intent",
    "select_combat_rules",
]
