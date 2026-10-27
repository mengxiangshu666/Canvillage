"""角色档案解析器 —— 一句话或一张参考图 → 结构化角色档案。

复刻自参考产品的角色创建链路（见 docs/REFERENCE_CORPUS_REGISTRY.md 登记的
libtv `10_规格/技能与资产/CHARACTER_GEN_SPEC_20260922.md`）：

    输入（二选一）          重 LLM（文本或视觉）
    ├── 文字描述            ─┐
    └── 参考图 URL          ─┴→ 角色档案（结构化字段）
                                    ├── 基础：名字建议 / 性别 / 年龄段 / 时代 / 地域
                                    ├── 外观：脸型 / 肤色 / 发型 / 妆容
                                    ├── 身形：体型 / 头身比 / 比例要点
                                    ├── 签名特征：主特征 / 次特征（跨图一致性的锚点）
                                    ├── 生图提示词：12 段结构（风格/成人声明/审美原型/
                                    │   面部规则/表情规范/签名特征/外观/身形/服装/构图/背景/负面）
                                    ├── 负面约束：17 条防 AI 脸清单
                                    └── 三档证据：用户说的 / 合理推断的 / AI 添加的

区别于 IdentityPlanner：那个从剧集内容**批量规划身份**；这个从用户输入
**解析一个新角色**。字段落在我们已有的 Character / CharacterIdentity 上：
face_prompt ← 面部+签名特征，appearance_details ← 服装造型，description ← 简介，
不新增数据库表。
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_ai import Agent, BinaryContent

from novelvideo.shared.env_guard import preserve_st_env

logger = logging.getLogger(__name__)

# ———————————————————————————————— 输出契约 ————————————————————————————————

NEGATIVE_CONSTRAINTS_TEMPLATE = (
    "避免模板化网红脸、蛇系尖脸、过度上挑窄眼、针尖下巴、病态苍白、"
    "深色僵硬嘴唇、凶狠或空洞表情、头大身小、少年身体、窄肩、短躯干、"
    "侧脸、身体裁切和广角畸变。"
)


class CharacterSignatureFeature(BaseModel):
    """签名特征：跨图一致性的结构化锚点。"""

    feature: str = Field(..., description="特征描述，如「细长微弯的古典柳叶眉」")
    priority: str = Field("primary", description="主特征 primary / 次特征 secondary")
    region: str = Field("", description="部位，如 brow_eye / nose_mouth / hair")
    visual_expression: str = Field(
        "",
        description="视觉表达（怎么呈现，不显凌厉等）",
    )
    identity_function: str = Field("", description="该特征奠定的人物气质/身份作用")


class CharacterProfile(BaseModel):
    """解析出的角色档案。字段都允许为空 —— 解析失败不阻塞建档。"""

    suggested_name: str = Field("", description="建议的角色名（可留空让用户起名）")
    gender: str = Field("", description="男 / 女")
    age_group: str = Field(
        "",
        description="child / youth / middle / elder 之一；解析为中文时归一",
    )
    era: str = Field("", description="现代 / 古代 / 民国 等时代")
    region: str = Field("", description="东亚 / 欧美 等地域")
    brief: str = Field("", description="一句话角色简介（30 字内）")
    face_prompt: str = Field(
        "",
        description="面部特征描述（脸型/五官/皮肤/发型/妆容），可直接作为面部锚点",
    )
    appearance_details: str = Field(
        "",
        description="服装、配饰、发型造型描述（不含动作表情）",
    )
    body_type: str = Field("", description="体型描述（高挑/纤细/健壮等）")
    signature_features: list[CharacterSignatureFeature] = Field(
        default_factory=list,
        description="签名特征（1-3 条，主/次）",
    )
    generation_prompt: str = Field(
        "",
        description="12 段结构的完整生图提示词（可直接拿去生成全身照）",
    )
    negative_constraints: str = Field(
        default=NEGATIVE_CONSTRAINTS_TEMPLATE,
        description="负面约束清单",
    )
    source_facts: list[str] = Field(
        default_factory=list,
        description="三档证据之一：用户原话里明确说的事实",
    )
    reasonable_inferences: list[str] = Field(
        default_factory=list,
        description="三档证据之二：从输入合理推断的内容",
    )
    design_additions: list[str] = Field(
        default_factory=list,
        description="三档证据之三：AI 主动添加的设计（用户可只改这档）",
    )

    @field_validator("age_group", mode="before")
    @classmethod
    def _normalize_age(cls, value: Any) -> str:
        if not isinstance(value, str):
            return ""
        mapping = {
            "儿童": "child", "幼年": "child", "童年": "child",
            "少年": "youth", "青年": "youth", "young": "youth", "adult": "youth",
            "中年": "middle",
            "老年": "elder",
        }
        return mapping.get(value.strip().lower(), value.strip().lower())



PARSER_SYSTEM_PROMPT = """\
你是影视角色的选角导演。用户会给你一句话描述或一张参考图，你把它解析成
一份结构化的「角色生成档案」。

要求：
- 所有推断都要保守、可解释：用户没说的不要当成事实，放进 design_additions 的
  要写清楚是设计选择。
- generation_prompt 必须是完整的 12 段中文提示词：风格声明、成人声明（明确
  成年）、性别与审美原型、面部规则、表情规范（不用模板化冷脸）、签名特征、
  外观要素、身形、服装规范（含否定，如不低胸不露腰）、构图规范（单人正面
  全身完整入镜）、背景规范（纯色便于抠图）、负面清单。
- 负面清单固定包含：模板化网红脸、蛇系尖脸、过度上挑窄眼、针尖下巴、病态
  苍白、深色僵硬嘴唇、凶狠或空洞表情、头大身小、少年身体、窄肩、短躯干、
  侧脸、身体裁切、广角畸变。
- signature_features 给 1-3 条：一条 primary（这个角色的识别锚点）、可选的
  secondary。特征要具体可画（「细长微弯的古典柳叶眉」），不要「帅气」「精致」
  这类空词。
- 如果输入是参考图：描述你看到的，不要臆造图中没有的元素。
"""

PARSER_TEXT_TASK = """\
把下面这段角色描述解析成角色生成档案：

{description}
"""

PARSER_IMAGE_TASK = """\
解析这张参考图中的角色，输出角色生成档案。图中没有的元素不要编造。\
"""


def _image_bytes_from_path(path: str | Path) -> bytes:
    from PIL import Image as PILImage

    img = PILImage.open(path)
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    if img.width > 1024 or img.height > 1024:
        ratio = min(1024 / img.width, 1024 / img.height)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=80, optimize=True)
    return buffer.getvalue()


async def parse_character_profile(
    *,
    description: str | None = None,
    image_path: str | Path | None = None,
) -> CharacterProfile:
    """一句话或一张图 → 角色档案。两条入口互斥；都空则报错。

    与参考产品的 `POST /api/user/character/parse` 同位：前端「创建角色」的
    第一步。约 20-40 秒的重 LLM 调用，调用方需把这一步做成异步任务。
    """
    description = (description or "").strip()
    if not description and not image_path:
        raise ValueError("解析角色档案需要一句话描述或一张参考图")
    if description and image_path:
        raise ValueError("一句话描述和参考图只能二选一")

    from novelvideo.generators.direct_models import get_direct_pydantic_model

    if image_path:
        model = get_direct_pydantic_model("vision", None, timeout_seconds=180)
        if model is None:
            raise ValueError("尚未配置可用的直连视觉模型，请先在模型中心配置并检测。")
        prompt: Any = [
            PARSER_IMAGE_TASK,
            BinaryContent(
                data=_image_bytes_from_path(image_path), media_type="image/jpeg"
            ),
        ]
    else:
        model = get_direct_pydantic_model("text", None, timeout_seconds=180)
        if model is None:
            raise ValueError("尚未配置可用的直连文字模型，请先在模型中心配置并检测。")
        prompt = PARSER_TEXT_TASK.format(description=description)

    agent = Agent(
        model,
        system_prompt=PARSER_SYSTEM_PROMPT,
        output_type=CharacterProfile,
        retries={"output": 2},
    )
    result = await agent.run(prompt)
    profile: CharacterProfile = result.output
    if description:
        # 三档证据的第一档必须是用户原话；AI 可能会转述。
        if description not in (profile.source_facts or []):
            profile.source_facts = [description, *(profile.source_facts or [])]
    return profile


def profile_to_character_fields(profile: CharacterProfile) -> dict[str, Any]:
    """档案 → 我们已有 Character 字段（不新增表）。"""
    primary = next(
        (f for f in profile.signature_features if f.priority == "primary"), None
    )
    secondary = [
        f for f in profile.signature_features if f.priority != "primary"
    ][:2]
    signature_text = ""
    if primary:
        signature_text = f"签名特征：{primary.feature}"
        if primary.visual_expression:
            signature_text += f"（{primary.visual_expression}）"
        for f in secondary:
            signature_text += f"；次要特征：{f.feature}"
    face_prompt = "。".join(
        part for part in [profile.face_prompt, signature_text] if part
    )
    return {
        "gender": profile.gender or "",
        "age_group": profile.age_group or "",
        "description": profile.brief or "",
        "face_prompt": face_prompt,
        "body_type": profile.body_type or "",
    }


def profile_to_identity_fields(profile: CharacterProfile) -> dict[str, Any]:
    """档案 → 默认身份（造型）字段。"""
    return {
        "appearance_details": profile.appearance_details or "",
        "face_prompt": profile.face_prompt or "",
        "age_group": profile.age_group or "",
        "body_type": profile.body_type or "",
    }


def build_generation_prompt(profile: CharacterProfile) -> str:
    """档案 → 最终生图提示词（12 段 + 负面清单）。"""
    return "\n".join(
        part
        for part in [profile.generation_prompt, profile.negative_constraints]
        if part and part.strip()
    ).strip()


__all__ = [
    "CharacterProfile",
    "CharacterSignatureFeature",
    "NEGATIVE_CONSTRAINTS_TEMPLATE",
    "build_generation_prompt",
    "parse_character_profile",
    "profile_to_character_fields",
    "profile_to_identity_fields",
]

# 保持与 IdentityPlanner 一致的环境守卫口径：解析器在 ST 环境变量域内运行。
_ = preserve_st_env