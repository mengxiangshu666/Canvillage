"""Deterministic attribution for natural-language feedback about the last Agent turn."""

from __future__ import annotations

import re
from dataclasses import dataclass


_POSITIVE_FEEDBACK_RE = re.compile(
    r"^(?:这个|这版|这次|刚才(?:那个|这套|的)?|当前(?:这个|这版)?|你刚才(?:的)?)?"
    r"(?:做得)?(?:很好|非常好|太好了|不错|很棒|特别棒|棒极了|完美|优秀|对了|就对了|正是这样|就是这样|符合预期|满意|喜欢)"
    r"(?:了|啊|呀|，|,|。|！|!|\s|$)|"
    r"^(?:保留这个|保留这版|就按这个|采用这个|采用这版|以后照这个|下次照这个|"
    r"这个可以保留|这版可以保留|就这个了|这版定了)(?:，|,|。|！|!|\s|$)",
    re.IGNORECASE,
)
_POSITIVE_EVALUATION_RE = re.compile(
    r"^(?:这个|这版|这次|刚才(?:那个|这套|的)?|当前(?:这个|这版)?)?"
    r"(?:生成的?|写的?|做的?)?(?:提示词(?:框架)?|框架|视频|图片|输出|结果|效果|质量)?"
    r"(?:真的|确实|非常|特别|很|太)?"
    r"(?:好|好了|不错|棒|棒极了|完美|优秀|符合预期|满意|喜欢)"
    r"(?:了|啊|呀|，|,|。|！|!|\s|$)|"
    r"^我(?:很|非常|特别)?(?:满意|喜欢)(?:了|啊|呀|，|,|。|！|!|\s|$)",
    re.IGNORECASE,
)
_ACCEPTANCE_FEEDBACK_RE = re.compile(
    r"^(?:以后都这样|下次都这样|按这个来|就按这套|这个框架可以|这个方法可以)"
    r"(?:，|,|。|！|!|\s|$)",
    re.IGNORECASE,
)
_NEGATIVE_FEEDBACK_RE = re.compile(
    r"^(?:这个|这版|这次|刚才(?:那个|这套|的)?|当前(?:这个|这版)?|你刚才(?:的)?)?"
    r"(?:不是这样|不对|错了|有问题|不满意|不喜欢|不符合预期|没生效|没有生效|"
    r"参数不对|方向不对|理解错了)(?:，|,|。|！|!|\s|$)|"
    r"^(?:重新做|重做|再做一遍|别这样做|不要这样做|撤回刚才|撤销刚才)"
    r"(?:，|,|。|！|!|\s|$)|"
    r"^(?:你)?(?:怎么又|又)(?:忘了|记不住|犯(?:了)?(?:这个|同样|类似)?错误)|"
    r"^(?:之前|前面)(?:不是|明明|已经)(?:已经)?(?:教过你|说过了?|讲过了?)|"
    r"^刚才(?:不是|明明|已经)?(?:教过你|说过了?|讲过了?)|"
    r"^(?:老是|总是|一直)(?:忘记|记不住|犯错)|"
    r"^(?:这版|这次|还是|仍然).{0,24}(?:不对|不行|很差|垃圾|没理解|不符合)",
    re.IGNORECASE,
)
_CONCRETE_SUBJECT_RE = re.compile(
    r"画布|节点|连线|模型|参数|提示词|剧本|分镜|镜头|角色|场景|道具|"
    r"图片|视频|音频|工作流|任务|回执|生成|回复|回答|记忆|框架|方法|"
    r"打戏|动作|连续性|参考资产|资产",
    re.IGNORECASE,
)
_CORRECTION_ACTION_RE = re.compile(
    r"应该|应当|需要|必须|先|再|改成|换成|保持|避免|不要|以后|下次|正确|"
    r"按|按照|采用|使用|拆解|包含|建立|固定|检查|保证|确保|优化|补上|补齐",
    re.IGNORECASE,
)

# A positive acknowledgement is evidence about the preceding result, not a
# durable rule.  It only becomes instructional when it names a reusable
# subject and an explicit action/persistence signal.
_TEACHING_SUBJECT_RE = re.compile(
    r"提示词|prompt|框架|方法|规则|经验|镜头|分镜|打戏|动作|连续性|"
    r"角色|场景|道具|参考图|图片|资产|模型|参数|画布|节点|工作流|质量|风格|中文",
    re.IGNORECASE,
)
_TEACHING_ACTION_RE = re.compile(
    r"必须|应该|应当|需要|不要|禁止|避免|保持|采用|按(?:照)?|使用|拆解|拆分|包含|建立|固定|检查|"
    r"保证|确保|优化|补上|补齐|改成|换成",
    re.IGNORECASE,
)
_TEACHING_ADOPTION_RE = re.compile(
    r"(?:提示词)?(?:框架|方法|规则|经验|配方).{0,24}(?:记住|保留|采用|采纳|以后|下次)|"
    r"(?:记住|保留|采用|采纳).{0,16}(?:提示词)?(?:框架|方法|规则|经验|配方)",
    re.IGNORECASE,
)


def has_actionable_teaching_signal(value: object) -> bool:
    """Return whether text contains a reusable instruction, not just praise."""

    content = _compact(value)
    persistence = re.search(
        r"以后|下次|每次|所有项目|跨项目|长期|默认|记住|规则|经验|方法|框架",
        content,
        re.IGNORECASE,
    )
    explicit_adoption = bool(_TEACHING_ADOPTION_RE.search(content))
    return bool(
        content
        and (
            explicit_adoption
            or (
                _TEACHING_SUBJECT_RE.search(content)
                and persistence
                and _TEACHING_ACTION_RE.search(content)
            )
        )
    )


def is_non_actionable_positive_feedback(value: object) -> bool:
    """Recognize praise that must remain feedback evidence only."""

    signal = classify_execution_feedback(value)
    return bool(
        signal
        and signal.outcome == "positive"
        and not has_actionable_teaching_signal(signal.content)
    )


@dataclass(frozen=True, slots=True)
class ExecutionFeedback:
    outcome: str
    content: str
    confidence: float
    actionable_correction: bool = False


def _compact(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def classify_execution_feedback(value: object) -> ExecutionFeedback | None:
    """Recognize explicit feedback that refers to the immediately preceding result.

    Deliberately excludes acknowledgement-only messages such as ``可以`` and ``好的``:
    those frequently authorize a next action instead of evaluating the previous one.
    """

    content = _compact(value)
    if not content or len(content) > 800:
        return None
    if _NEGATIVE_FEEDBACK_RE.search(content):
        return ExecutionFeedback(
            outcome="negative",
            content=content,
            confidence=0.95,
            actionable_correction=bool(
                _CONCRETE_SUBJECT_RE.search(content)
                and _CORRECTION_ACTION_RE.search(content)
            ),
        )
    if (
        _POSITIVE_FEEDBACK_RE.search(content)
        or _POSITIVE_EVALUATION_RE.search(content)
        or _ACCEPTANCE_FEEDBACK_RE.search(content)
    ):
        return ExecutionFeedback(
            outcome="positive",
            content=content,
            confidence=0.95,
        )
    return None


__all__ = [
    "ExecutionFeedback",
    "classify_execution_feedback",
    "has_actionable_teaching_signal",
    "is_non_actionable_positive_feedback",
]
