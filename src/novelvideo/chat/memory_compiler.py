"""Compile raw feedback into compact, executable Xiaoshu memories.

Raw chat is evidence.  A durable memory must tell the Agent what to do,
when to do it, and what to avoid.  This module is intentionally deterministic:
memory quality must not collapse when a configured model is temporarily down.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal

MEMORY_SCHEMA_VERSION = "xiaoshu.memory.v3"
MEMORY_COMPILER_VERSION = "deterministic-v6"

MemoryDecision = Literal["add", "noop"]

_SMALL_TALK = {
    "hi",
    "hello",
    "嗨",
    "你好",
    "哈喽",
    "在吗",
    "谢谢",
    "好的",
    "可以",
    "确认",
    "确定",
}
_COMMUNICATION_STYLE_RE = re.compile(
    r"废话|啰嗦|说人话|简洁(?:一点)?|简短(?:一点)?|结论先行|"
    r"不要复述|别复述|不要寒暄|少说(?:一点)?|特别.{0,8}详细|"
    r"主要的东西",
    re.IGNORECASE,
)
_WANTS_MORE_DETAIL_RE = re.compile(
    r"不要太简洁|别太简洁|不要过于简短|需要详细|展开说明",
    re.IGNORECASE,
)
_EXECUTION_READINESS_RE = re.compile(
    r"什么都没(?:有)?问|没有问|不问|直接开始|马上开始|快速生成|"
    r"急躁|冲动|关键词.{0,16}(?:节点|模板)|固定.{0,12}(?:节点|模板)|"
    r"假装(?:聪明|完成)|为了(?:快速|速度).{0,16}(?:生成|干活)|"
    r"信息充分|缺失信息会改变|只询问一个影响",
    re.IGNORECASE,
)
_UNRESOLVED_REFERENCE_RE = re.compile(
    r"^(?:以后|下次|今后)?(?:你)?(?:一定)?(?:不能|不要|别|禁止)"
    r"(?:再)?(?:这个|这种|同样|类似)?(?:犯)?"
    r"(?:这个|这种|同样|类似)?(?:低级)?错误(?:了)?$",
    re.IGNORECASE,
)
_UNRESOLVED_PRONOUN_RE = re.compile(
    r"^(?:他|她|它)(?:就)?(?:应该|应当|需要|默认|必须).{0,24}$",
    re.IGNORECASE,
)
_CHARACTER_SHEET_RE = re.compile(
    r"角色设定表|角色正面大头照|头顶到胸口|浅灰背景",
    re.IGNORECASE,
)
_NODE_PARAMETER_RE = re.compile(
    r"节点.{0,12}参数|参数.{0,12}节点",
    re.IGNORECASE,
)
_DURATION_CONVENTION_RE = re.compile(
    r"(?:视频|提示词|时长).{0,24}(?:不要|不需要|禁止).{0,16}(?:写|声明|出现).{0,12}(?:时长|秒数|秒)",
    re.IGNORECASE,
)
_REFERENCE_MAPPING_RE = re.compile(
    r"(?:角色|人物|姥爷|姥姥).{0,40}(?:参考图|图片|引用|图号).{0,40}(?:反|错|混|搞反|对应|映射)",
    re.IGNORECASE,
)
_EMOTION_ONLY_RE = re.compile(
    r"^(?:你|小树)?(?:真|太|特别|非常)?(?:笨|蠢|废|没用|垃圾)(?:了|啊|呀|吧|啦)*$",
    re.IGNORECASE,
)
_ACTION_SIGNAL_RE = re.compile(
    r"必须|应当|应该|建议|需要|优先|默认|保持|禁止|避免|不要|别|"
    r"先|再|只有.+才|信息充分|缺失信息|直接执行|询问",
    re.IGNORECASE,
)
_PREFERENCE_SIGNAL_RE = re.compile(
    r"我的偏好|我喜欢|我不喜欢|我希望|偏好|默认使用|默认采用|默认保持",
    re.IGNORECASE,
)
_CONCRETE_SUBJECT_RE = re.compile(
    r"画布|节点|连线|模型|参数|提示词|剧本|分镜|镜头|角色|场景|道具|"
    r"图片|视频|音频|工作流|任务|回执|生成|回复|回答|沟通|上下文|记忆",
    re.IGNORECASE,
)
_TASK_REQUEST_RE = re.compile(
    r"(?:创建|新增|配置|提交|不要提交|点击生成|只配置).{0,100}"
    r"(?:节点|项目|视频|图片|工作流|任务)",
    re.IGNORECASE,
)
_STABLE_RULE_RE = re.compile(
    r"以后|长期|所有项目|跨项目|永远|永久|记住|我的偏好|长期规则|"
    r"用户要求|复盘|经验|规律|下次|每次",
    re.IGNORECASE,
)
_EXPLICIT_PERSISTENCE_RE = re.compile(
    r"以后|长期|所有项目|跨项目|永远|永久|记住|我的偏好|长期规则|用户要求",
    re.IGNORECASE,
)
_LEADING_WRAPPERS = (
    re.compile(r"^用户长期要求\s*[:：]\s*", re.IGNORECASE),
    re.compile(r"^(?:兄弟|小树)[，,、\s]*", re.IGNORECASE),
    re.compile(r"^(?:请|一定要|你要|你得)?记住(?:这个|这一点|这点)?[，,：:\s]*", re.IGNORECASE),
    re.compile(r"^(?:我)?(?:希望|要求)(?:你)?[，,：:\s]*", re.IGNORECASE),
    re.compile(r"^(?:以后|下次|今后)(?:你)?[，,：:\s]*", re.IGNORECASE),
)
_TRAILING_WRAPPERS = (
    re.compile(r"[，,。\s]*(?:永久|长期)?记住(?:这个|这一点|这点)?[。！!\s]*$", re.IGNORECASE),
    re.compile(r"[，,。\s]*(?:知道吗|明白吗|能明白吗|对吧|行吗)[？?。！!\s]*$", re.IGNORECASE),
)


@dataclass(frozen=True, slots=True)
class MemoryCompilation:
    decision: MemoryDecision
    kind: str
    scope_kind: str
    memory_key: str = ""
    content: str = ""
    applies_when: dict[str, Any] | None = None
    action: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    confidence: float = 0.0
    reason: str = ""
    rule_type: str = "general_rule"
    hook_id: str = ""
    hook_mode: str = "none"
    executable: bool = False

    def metadata(self) -> dict[str, Any]:
        return {
            "compiled": self.decision == "add",
            "distilled": self.decision == "add",
            "normalized": self.decision == "add",
            "distillation_level": "deterministic_structured" if self.decision == "add" else "none",
            "memory_schema": MEMORY_SCHEMA_VERSION,
            "memory_key": self.memory_key,
            "rule_type": self.rule_type,
            "hook_id": self.hook_id,
            "hook_mode": self.hook_mode,
            "executable": bool(self.executable),
            "rule": self.content,
            "action": list(self.action),
            "avoid": list(self.avoid),
            "compiler": MEMORY_COMPILER_VERSION,
            "compile_reason": self.reason,
        }


def _clean(value: object) -> str:
    text = " ".join(str(value or "").strip().split())
    return text.strip(" ，,。.!！?？；;\t")


def _strip_wrappers(text: str) -> str:
    result = text
    for _ in range(3):
        before = result
        for pattern in _LEADING_WRAPPERS:
            result = pattern.sub("", result).strip()
        if result == before:
            break
    for pattern in _TRAILING_WRAPPERS:
        result = pattern.sub("", result).strip()
    result = re.sub(r"^(?:你)?(?:千万|一定|务必)?不要", "禁止", result)
    result = re.sub(r"^(?:你)?(?:必须|一定要|务必)", "必须", result)
    result = re.sub(r"^(?:你)?(?:应该|应当|需要|得)", "应", result)
    result = re.sub(r"^(?:我觉得|我认为|说实话)[，,、\s]*", "", result)
    result = result.replace("你千万不要", "禁止").replace("你不要", "不要")
    result = result.replace("你必须", "必须").replace("你应该", "应")
    return result.strip(" ，,。.!！?？；;\t")


def _instruction_fragments(text: str) -> list[str]:
    fragments = [
        _strip_noninstruction_prefix(_strip_wrappers(fragment))
        # Commas often bind a condition to its consequence. Splitting
        # ``如果只有无关资产，禁止强行连线`` would turn a scoped guard into
        # a global prohibition, so only sentence-level separators are safe.
        for fragment in re.split(r"[。.!！?？；;\n]", text)
        if _strip_noninstruction_prefix(_strip_wrappers(fragment))
    ]
    actionable = [fragment for fragment in fragments if _ACTION_SIGNAL_RE.search(fragment)]
    return actionable or fragments


def _strip_noninstruction_prefix(fragment: str) -> str:
    """Drop prose before an instruction while retaining conditional guards."""

    parts = re.split(r"[，,]", fragment, maxsplit=1)
    if len(parts) != 2:
        return fragment
    prefix, consequence = (item.strip() for item in parts)
    conditional = re.match(r"^(?:当|如果|若|只有|除非|凡是|每当)", prefix)
    if (
        prefix
        and consequence
        and conditional is None
        and not _ACTION_SIGNAL_RE.search(prefix)
        and _ACTION_SIGNAL_RE.search(consequence)
    ):
        return consequence
    return fragment


def _bounded_instruction_content(fragments: list[str], *, limit: int = 600) -> str:
    """Join whole instruction clauses without cutting a condition in half."""

    selected: list[str] = []
    used = 0
    for fragment in fragments:
        clean = fragment.strip(" ，,。.!！?？；;\t")
        if not clean:
            continue
        separator = 1 if selected else 0
        if used + separator + len(clean) > limit:
            if not selected:
                selected.append(clean[:limit].rstrip(" ，,：:"))
            break
        selected.append(clean)
        used += separator + len(clean)
    return "；".join(selected)


def _memory_key(prefix: str, content: str) -> str:
    identity = re.sub(r"[\s，,。.!！?？；;：:]", "", content).casefold()
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}.{digest}"


def _finalize_sentence(text: str) -> str:
    compact = re.sub(r"\s+", " ", text).strip(" ，,。.!！?？；;\t")
    return f"{compact}。" if compact else ""


def compile_memory(
    raw_content: object,
    *,
    kind_hint: str = "learned_rule",
    scope_kind: str = "user",
    task_stage: str = "",
) -> MemoryCompilation:
    """Return an executable memory or a reasoned no-op decision."""

    raw = _clean(raw_content)
    normalized = raw.casefold()
    if not raw or normalized in _SMALL_TALK:
        return MemoryCompilation("noop", kind_hint, scope_kind, reason="small_talk")
    if _UNRESOLVED_REFERENCE_RE.fullmatch(raw):
        return MemoryCompilation(
            "noop",
            kind_hint,
            scope_kind,
            reason="unresolved_reference",
        )
    if _EMOTION_ONLY_RE.fullmatch(raw):
        return MemoryCompilation("noop", kind_hint, scope_kind, reason="emotion_only")
    if _UNRESOLVED_PRONOUN_RE.fullmatch(raw):
        return MemoryCompilation("noop", kind_hint, scope_kind, reason="unresolved_reference")
    if "低级错误" in raw and not _CONCRETE_SUBJECT_RE.search(raw):
        return MemoryCompilation("noop", kind_hint, scope_kind, reason="unresolved_reference")
    if _TASK_REQUEST_RE.search(raw) and not _STABLE_RULE_RE.search(raw):
        return MemoryCompilation("noop", kind_hint, scope_kind, reason="task_request")

    if _COMMUNICATION_STYLE_RE.search(raw) and not _WANTS_MORE_DETAIL_RE.search(raw):
        content = (
            "默认使用简体中文，结论先行；省略寒暄、复述和重复解释；"
            "必要技术术语保留原文，并用一句话说明。"
        )
        return MemoryCompilation(
            "add",
            "preference",
            "user",
            memory_key="user.communication_style",
            content=content,
            applies_when={"interaction": "all"},
            action=("结论先行并使用日常中文", "只保留完成当前任务所需的信息"),
            avoid=("寒暄", "复述用户原话", "重复解释"),
            confidence=1.0,
            reason="communication_style",
            rule_type="communication_preference",
        )

    if _EXECUTION_READINESS_RE.search(raw):
        content = (
            "先判断创作需求是否具备可执行条件：信息充分时直接执行；"
            "缺失信息会改变作品方向时，只询问一个影响最大的条件，再开始规划和搭建节点。"
        )
        experience = kind_hint in {"candidate_experience", "validated_experience", "verified_experience"}
        return MemoryCompilation(
            "add",
            "candidate_experience" if experience else "learned_rule",
            "professional" if experience else "user",
            memory_key=(
                "professional.experience.canvas.execution_readiness"
                if experience
                else "canvas.execution_readiness"
            ),
            content=content,
            applies_when={
                "task_domain": "canvas_creation",
                "decision": "readiness_gate",
            },
            action=("信息充分时直接执行", "信息不足时只询问一个最高影响条件"),
            avoid=("仅凭关键词套固定节点模板", "未理解目标就写入画布"),
            confidence=0.45 if experience else 1.0,
            reason="execution_readiness",
            rule_type="execution_readiness",
        )

    if _CHARACTER_SHEET_RE.search(raw):
        content = (
            "制作角色设定表时，保持角色外观、比例、颜色和细节一致；"
            "左侧三分之一放置头顶至胸口的角色正面大头照；使用浅灰背景并避免元素拥挤。"
        )
        experience = kind_hint in {"candidate_experience", "validated_experience", "verified_experience"}
        return MemoryCompilation(
            "add",
            "candidate_experience" if experience else "learned_rule",
            "professional" if experience else "user",
            memory_key=(
                "professional.experience.creative.character_sheet_layout"
                if experience
                else "creative.character_sheet_layout"
            ),
            content=content,
            applies_when={"task_domain": "character_design"},
            action=("保持角色视觉身份一致", "使用清晰的角色设定表布局"),
            avoid=("角色元素拥挤", "外观、比例、颜色或细节漂移"),
            confidence=0.45 if experience else 0.95,
            reason="character_sheet_layout",
            rule_type="creative_layout",
        )

    if _DURATION_CONVENTION_RE.search(raw):
        explicit = bool(_EXPLICIT_PERSISTENCE_RE.search(raw))
        return MemoryCompilation(
            "add",
            "learned_rule" if explicit else "candidate_experience",
            "user" if explicit else "professional",
            memory_key="video.prompt.duration_convention",
            content="视频提示词不重复声明由节点 duration 参数控制的总时长；保留镜头时间轴。",
            applies_when={"task_stage": "media_generation", "node_type": "videoNode"},
            action=("只移除与节点 duration 参数重复的总时长声明",),
            avoid=("不要删除 [0-2s] 等镜头时间轴",),
            confidence=0.95 if explicit else 0.35,
            reason="duration_convention" if explicit else "duration_feedback_candidate",
            rule_type="prompt_convention",
            hook_id="video_prompt.remove_redundant_duration",
            hook_mode="preview_only",
        )

    if _REFERENCE_MAPPING_RE.search(raw):
        explicit = bool(_EXPLICIT_PERSISTENCE_RE.search(raw))
        return MemoryCompilation(
            "add",
            "learned_rule" if explicit else "candidate_experience",
            "user" if explicit else "professional",
            memory_key="reference.explicit_role_mapping",
            content="多角色参考图必须显式建立图号或 asset ID 与角色名的映射，生成前核对绑定；禁止凭位置猜测或自动交换。",
            applies_when={"task_stage": "media_generation"},
            action=("生成前核对参考图与角色映射",),
            avoid=("根据模糊反馈猜测姥爷或姥姥的 asset ID",),
            confidence=0.95 if explicit else 0.35,
            reason="reference_mapping" if explicit else "reference_mapping_feedback_candidate",
            rule_type="reference_consistency",
            hook_id="reference.require_explicit_mapping",
            hook_mode="preview_only",
        )

    if _NODE_PARAMETER_RE.search(raw):
        content = (
            "执行节点前核对所选模型的能力合同与节点参数；"
            "只提交该模型支持的画幅、分辨率、时长、声音和参考素材数量。"
        )
        experience = kind_hint in {"candidate_experience", "validated_experience", "verified_experience"}
        return MemoryCompilation(
            "add",
            "candidate_experience" if experience else "learned_rule",
            "professional" if experience else "user",
            memory_key=(
                "professional.experience.canvas.model_parameter_contract"
                if experience
                else "canvas.model_parameter_contract"
            ),
            content=content,
            applies_when={"task_domain": "canvas_execution"},
            action=("先读取所选模型能力", "按能力合同填写节点参数"),
            avoid=("提交模型不支持的参数", "用隐藏默认值覆盖用户配置"),
            confidence=0.45 if experience else 0.95,
            reason="model_parameter_contract",
            rule_type="prompt_convention",
        )

    fragments = _instruction_fragments(raw)
    canonical = _bounded_instruction_content(fragments)
    canonical = _strip_wrappers(canonical)
    if not canonical:
        return MemoryCompilation("noop", kind_hint, scope_kind, reason="empty_after_cleanup")
    if re.search(r"(?:再教给你一个知识|学习固化|教给你)", canonical, re.IGNORECASE):
        return MemoryCompilation("noop", kind_hint, scope_kind, reason="vague_learning")
    if (
        len(canonical) < 8
        or (
            not _ACTION_SIGNAL_RE.search(canonical)
            and not _PREFERENCE_SIGNAL_RE.search(raw)
            and not _CONCRETE_SUBJECT_RE.search(canonical)
        )
    ):
        return MemoryCompilation("noop", kind_hint, scope_kind, reason="not_executable")

    if kind_hint in {"candidate_experience", "validated_experience", "verified_experience"}:
        kind = "candidate_experience" if kind_hint == "candidate_experience" else kind_hint
        target_scope = "professional"
        prefix = "professional.experience"
    elif kind_hint == "project_fact" or scope_kind == "project":
        kind = "project_fact"
        target_scope = "project"
        prefix = "project.fact"
    elif kind_hint == "preference" or _PREFERENCE_SIGNAL_RE.search(raw):
        kind = "preference"
        target_scope = "user"
        prefix = "user.preference"
    else:
        kind = "learned_rule"
        target_scope = "user"
        prefix = "user.rule"

    content = _finalize_sentence(canonical)
    avoid = tuple(
        fragment
        for fragment in re.split(r"[；;]", canonical)
        if re.search(r"(?:^|[，,])(?:禁止|不要|避免)", fragment)
    )
    applies_when: dict[str, Any] = {}
    if task_stage and target_scope != "user":
        applies_when["task_stage"] = task_stage
    key_material = (
        f"{task_stage}:{content}" if task_stage and target_scope == "professional" else content
    )
    return MemoryCompilation(
        "add",
        kind,
        target_scope,
        memory_key=_memory_key(prefix, key_material),
        content=content,
        applies_when=applies_when,
        action=(content.rstrip("。"),),
        avoid=avoid,
        confidence=0.95 if target_scope in {"user", "project"} else 0.45,
        reason="explicit_executable_rule",
        rule_type="general_rule",
    )


__all__ = [
    "MEMORY_COMPILER_VERSION",
    "MEMORY_SCHEMA_VERSION",
    "MemoryCompilation",
    "compile_memory",
]
