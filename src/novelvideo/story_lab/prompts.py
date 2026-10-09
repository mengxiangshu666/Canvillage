"""Versioned Story Lab prompt packages."""

from __future__ import annotations

import json
from typing import Any

from novelvideo.story_lab.models import StoryLabConfig, StoryLabStage

PROMPT_VERSIONS = {stage: f"story-lab.{stage.value}.v1" for stage in StoryLabStage}
PROMPT_VERSIONS[StoryLabStage.DRAFT] = "story-lab.draft.v2"

_COMMON = """你是 Village Infinite Canvas 故事 Agent的首席叙事架构师与影视编剧。
输出必须严格符合给定结构，不写解释性前言。所有人物、世界规则、因果和视觉资产需求必须可追踪。
不要照搬既有作品；围绕当前项目创意进行原创构建。避免空泛套话、重复冲突和无因果转折。
若用户未确认风格模板，根据题材、时代、受众和创意自行推断合适叙事风格；只有 style_mode=confirmed 时才把给定风格作为强约束。
"""

SYSTEM_PROMPTS = {
    StoryLabStage.BIBLE: _COMMON
    + """当前阶段：故事圣经。建立世界观、人物目标与弧光、核心冲突、地点功能、人物关系、世界规则和禁忌；视觉身份描述必须便于后续素材库生成一致角色资产。""",
    StoryLabStage.OUTLINE: _COMMON
    + """当前阶段：宏观大纲。基于故事圣经构建幕结构、主支线 DAG、伏笔回收、因果链、张力曲线与逐集/逐章卡片；每张卡必须有目的、冲突、转折、高潮、结尾钩子和资产需求。""",
    StoryLabStage.DRAFT: _COMMON
    + """当前阶段：成稿。严格按作品类型输出可直接阅读或生产的正文。影视剧本写清场次、时空、人物、动作与对白；微短剧强调强钩子和集尾反转；漫剧解说按可视画面推进；小说保证叙事连续。full_text 必须包含完整成稿，不能只给摘要或结构卡。影视类 full_text 使用稳定生产格式：第N集 标题；每场使用“N-1 地点 时间 内/外”；随后写“人物：...”以及可直接拍摄的动作和对白。units 只同步提供分集、分章或内部结构，不得把幕、场、时间段伪装成多个剧集。""",
    StoryLabStage.AUDIT: _COMMON
    + """当前阶段：审校。检查人物一致性、世界观冲突、伏笔遗漏、因果断裂、文风漂移、张力、视觉可生产性和下游角色/场景/道具/声线资产缺口；问题必须定位到具体分集/分章并给出可执行修复建议。""",
}


def build_stage_request(
    *,
    stage: StoryLabStage,
    config: StoryLabConfig,
    upstream: dict[str, Any],
    instructions: str = "",
) -> str:
    style_rule = (
        "使用已确认风格作为强约束："
        f"{config.style_name or config.style_id}\n{config.style_prompt}"
        if config.style_mode == "confirmed"
        else "未确认固定风格：请依据创意与题材推断叙事和视听风格。"
    )
    payload = {
        "stage": stage.value,
        "config": config.model_dump(mode="json"),
        "upstream": upstream,
        "instructions": instructions,
    }
    return (
        f"{style_rule}\n\n"
        "以下为当前项目的结构化输入。保留已建立的名称、规则与因果：\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
