"""Shared intent classification for Agent planning and execution gates.

The classifier is deliberately small and deterministic.  It separates an
action word (for example, "create") from an actual submission request, so a
discussion such as "how to create a character" cannot accidentally acquire a
write policy merely because it contains the same verb as a real action.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


IntentKind = Literal[
    "discussion",
    "observation",
    "plan",
    "canvas_mutation",
    "media_submission",
    "workflow_resume",
    "memory_teaching",
    "user_authorization",
]

_ACTION_RE = re.compile(
    r"生成|生图|出图|渲染|提交|执行|运行|开始|重试|恢复|重新获取|合成|导出|制作|"
    # 「出片/出成片/出视频/出出来」是成片生产的常用说法。「出+片/成片/视频/来」
    # 而不是裸「出」，否则「出现」「出发」这类会被误当成动作。
    # 「出片/出个成片/出一条短片/把片子出出来」这一族。已知代价：「看不出来」也会
    # 命中（出…来）。判错方向是「把只读提问当成可写」，而 2026-09-30 之后画布写入
    # 可撤销、不再被只读合同拦截（见 T-194），所以这个方向的错代价可控。
    # 枚举中文动宾形态是无底洞，真正的解法是守卫测试盯住这一族，见
    # tests/test_intent_contract.py::test_action_verb_families_stay_recognized。
    r"出.{0,4}?(?:成片|片|视频)|出.{0,3}?来|"
    # 「帮我做/帮我写/帮我建…」这一族要收，但**不能**用「帮我」前缀整体识别——
    # 「帮我看看」「帮我查一下」「网络连接失败帮我看看」都是只读请求，整体识别会误判。
    r"帮我(?:做|写|建|弄|搞|加|改|删|连|接|生成|出|画|设计|安排|整|重做|拆)|替我(?:做|写|建|弄|搞|加|改|删|连|接)|"
    r"创建|新建|新增|添加|插入|修改|更新|调整|移动|删除|重命名|替换|优化|"
    r"generate|render|submit|execute|run|start|retry|resume|compose|export|make|"
    r"create|add|insert|edit|update|move|delete|rename|replace|optimi[sz]e",
    re.IGNORECASE,
)
_MEDIA_ACTION_RE = re.compile(
    r"生成|生图|出图|渲染|提交|执行|运行|开始|重试|重新获取|合成|导出|制作|"
    r"generate|render|submit|execute|run|start|retry|compose|export|make",
    re.IGNORECASE,
)
_DISCUSSION_RE = re.compile(
    r"讨论|分析|了解|查看|检查|解释|为什么|怎么(?:选|办|做|弄)?|如何|对比|评估|规划|"
    r"写提示词|优化提示词|聊聊|介绍|原理|支持什么|哪个模型|是否|能否|how|why|"
    r"explain|compare|assess|plan|discuss",
    re.IGNORECASE,
)
_RESUME_RE = re.compile(
    r"继续(?:刚才|上次|之前|当前)?(?:任务|制作|工作流|流程|处理)?|"
    r"接着(?:做|处理|制作)|恢复(?:任务|工作流|流程)|重试(?:任务|工作流|失败)?|"
    r"重新获取(?:结果|任务)?|resume|retry|continue",
    re.IGNORECASE,
)
_MUTATION_RE = re.compile(
    r"修改|更新|调整|移动|删除|重命名|替换|优化(?:这个|当前|该)?(?:节点|镜头|提示词)?|"
    # 连线类动作要收祈使形态（连起来/接上/接入），单写「连接」会把
    # 「网络连接失败」这类报错陈述也当成变更请求。
    r"连线|断开|移除|连(?:起来|上|到|入)|接(?:起来|上|到|入)|并入|挂(?:上|载|接)|绑(?:上|定)|"
    r"装配|装载|接入|对接|串(?:起来|联)|"
    r"edit|update|move|delete|rename|replace|optimi[sz]e",
    re.IGNORECASE,
)
_CREATE_RE = re.compile(
    r"创建|新建|新增|添加|插入|create|add|insert|new",
    re.IGNORECASE,
)
_MEMORY_RE = re.compile(
    r"记住|记忆|沉淀|学习|复盘|经验|成长|蒸馏|memory|learn|distill|retrospective",
    re.IGNORECASE,
)
# 用户在批准上一轮提议。这些话本身不含任何动作动词，正则会判成 observation，
# 于是回合合同冻结成只读，agent 刚要落地的写操作被自己人拦下，用户只看到一句
# 「已保留恢复点」——实际什么都没发生。批准就是授权，不能按"只是观察"处理。
_APPROVAL_LEAD_RE = re.compile(
    r"^(?:"
    r"好(?:的|吧|嘞|啦|哦|呀)?|行(?:吗|吧)?|嗯+|可以|可|没问题|没意见|同意|认可|"
    r"批准|通过|就这样|ok|okay|yes|yep|yeah|sure|alright|right|"
    r"go\s*ahead|go\s*on|sounds?\s*good|approved|do\s*it|proceed|"
    r"那就|那|就|那么"
    r")\s*(?:[!！。,.，、~～:：?？]\s*)*",
    re.IGNORECASE,
)
_APPROVAL_TAIL_RE = re.compile(
    r"(?:[!！。,.，、~～:：?？\s]*"
    r"(?:吧|就行|好了|即可|就成|了|啦|哦|呀|嘛|thanks|thank\s*you)"
    r")*\s*$",
    re.IGNORECASE,
)
# 剥掉客套话之后剩下的这些，才算授权：「剩下的」本身不含动作动词。
_COURTESY_RE = re.compile(
    r"^\s*(?:(?:谢谢|多谢|感谢|辛苦了|辛苦|麻烦你了|thanks|thank\s*you|thx|ty)"
    r"[\s!！。.．,，、~～]*)+$",
    re.IGNORECASE,
)
_APPROVAL_CORE_RE = re.compile(
    r"^(?:"
    r"你(?:看着|决定|来|说|安排|做主|定)(?:办|来|就行)?|随你|听你(?:的)?|由你|"
    r"按(?:你|这个|此|照)(?:说的|说的做|来|做|办)?|照(?:这个|此|着|你)(?:来|做|办)?|"
    r"这么(?:办|做|来|着|样|办吧)|这么样(?:办|做)|这样(?:办|做|来)|"
    r"这个|此|那|"
    r"动手|开始|执行|做|弄|搞|干|来|搞定|"
    r"随便|就这样|这样|这个|此|"
    r"剩下的|后面的|其他|其它|后面"
    r")"
    r"(?:剩下|后面|其他|其它)?"
    r"(?:的)?"
    r"(?:都|就|先|由|全部|统统)?"
    r"(?:你|由你|听你)?"
    r"(?:看着办|看着来|决定|安排|做主|看着|办|做|来|搞定)?"
    r"\s*$",
    re.IGNORECASE,
)


_APPROVAL_MAX_BARE_LEN = 16
_QUESTION_RE = re.compile(
    r"[?？]|吗|呢|什么|怎么|如何|为什么|为啥|哪|是否|多少|几个|哪个|如何|"
    r"how|why|what|which|whether",
    re.IGNORECASE,
)
_TOPIC_NOUN_RE = re.compile(
    r"节点|镜头|分镜|提示词|图片|图|视频|工作流|流程|模型|素材|画布|连线|"
    r"node|shot|storyboard|prompt|image|video|workflow|model|canvas|asset",
    re.IGNORECASE,
)


def _is_bare_approval(text: str) -> bool:
    """A turn that is nothing but consent carries no action verb but is consent."""

    body = text.strip()
    if not body or _COURTESY_RE.match(body):
        # 纯客套不是授权：用户道谢通常是要收尾，不是要 agent 继续动手。
        return False
    stripped = _APPROVAL_LEAD_RE.sub("", body).strip()
    stripped = _APPROVAL_TAIL_RE.sub("", stripped).strip()
    if not stripped:
        # 整句就是一个「可以」「行」「OK」「可以了」，剥完只剩语气词。
        return True
    # 不要用「短句 + 非疑问 + 不提具体对象」来兜底。那条启发式在 2026-09-30 被
    # 实测证伪：它把「做一个20秒的企业宣传片」「帮我做一集短剧」「按刚才说的改」
    # 「再来一版」这些**新任务请求**全判成了授权——它们短、不是疑问、也不提
    # 「节点/镜头/提示词」这类词，于是被放行。授权判错的方向是「把新请求当批准」，
    # 和 2026-09-30 修掉的那批问题是同一个方向上的错。
    # 漏掉的形态补进枚举，别用长度把它们糊过去。
    return bool(_APPROVAL_CORE_RE.match(stripped))


_NEGATED_ACTION_RE = re.compile(
    r"(?:不要|不必|无需|别|禁止|勿|不要去|don't|do\s+not|without)\s*"
    r"(?:现在|直接|马上|立刻|开始|去)?\s*"
    r"(?:生成|生图|出图|渲染|提交|执行|运行|开始|重试|恢复|合成|导出|制作|"
    r"创建|新建|新增|添加|插入|修改|更新|调整|移动|删除|重命名|替换|优化|"
    r"generate|render|submit|execute|run|start|retry|resume|compose|export|make|"
    r"create|add|insert|edit|update|move|delete|rename|replace|optimi[sz]e)",
    re.IGNORECASE,
)
_FORCE_ACTION_RE = re.compile(
    r"(?:现在|直接|马上|立刻|开始|立即|帮我|替我|请)\s*"
    r"(?:生成|生图|出图|渲染|提交|执行|运行|开始|重试|恢复|合成|导出|制作|"
    r"创建|新建|新增|添加|插入|修改|更新|调整|移动|删除|重命名|替换|优化|"
    r"generate|render|submit|execute|run|start|retry|resume|compose|export|make|"
    r"create|add|insert|edit|update|move|delete|rename|replace|optimi[sz]e)",
    re.IGNORECASE,
)


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())[:2_000]


def strip_negated_action_clauses(value: object) -> str:
    """Remove negated action verbs without discarding affirmative requests."""

    return _NEGATED_ACTION_RE.sub(" ", _clean(value))


@dataclass(frozen=True, slots=True)
class AgentIntent:
    """One shared, serializable interpretation of a user request."""

    kind: IntentKind
    action_requested: bool
    media_submission_requested: bool
    resume_requested: bool
    target_mutation_requested: bool
    explicit_creation_requested: bool
    negated_action: bool
    discussion_requested: bool
    reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "agent_intent.v1",
            "kind": self.kind,
            "action_requested": self.action_requested,
            "media_submission_requested": self.media_submission_requested,
            "resume_requested": self.resume_requested,
            "target_mutation_requested": self.target_mutation_requested,
            "explicit_creation_requested": self.explicit_creation_requested,
            "negated_action": self.negated_action,
            "discussion_requested": self.discussion_requested,
            "reason_codes": list(self.reason_codes),
        }


def classify_agent_intent(value: object) -> AgentIntent:
    """Classify action semantics once for fleet, planner, and checkpoint code."""

    text = _clean(value)
    negated = bool(_NEGATED_ACTION_RE.search(text))
    # Negative constraints such as "不要创建新节点" must not erase the
    # affirmative request that precedes them. Remove only the negated action
    # verbs, then classify what the user actually asked to do.
    positive_text = strip_negated_action_clauses(text)
    # 变更动词本身就是动作。原先只查 _ACTION_RE，而它漏了「连线/连接/断开/移除」——
    # 这四个都在 _MUTATION_RE 里。后果是「把这两个节点连起来」被判成 action_requested=False，
    # 于是整条写侧路线降级成 canvas.snapshot 只读，再到写检查点时被自己拒掉：
    # 2026-09-30 真机 `execution_context_write_policy_missing` → dynamic_checkpoint_blocked，
    # 用户看到的现象是「改已有节点总是失败」。改这里而不是再往 _ACTION_RE 里补词：
    # 变更动词按定义就是动作，两张表手工同步必然会再次漂移。
    has_action = bool(
        _ACTION_RE.search(positive_text) or _MUTATION_RE.search(positive_text)
    )
    discussion = bool(_DISCUSSION_RE.search(positive_text))
    force_action = bool(_FORCE_ACTION_RE.search(positive_text))
    resume = bool(_RESUME_RE.search(positive_text))
    explicit_creation = bool(_CREATE_RE.search(positive_text)) and not re.search(
        r"(?:怎么|如何|讨论|聊聊|分析|了解|how|how to)\s*"
        r"(?:去|来)?\s*(?:创建|新建|新增|添加|插入|create|add|insert)",
        positive_text,
        flags=re.IGNORECASE,
    )
    first_action = next(
        (match.start() for match in _ACTION_RE.finditer(positive_text)),
        None,
    )
    first_discussion = next(
        (match.start() for match in _DISCUSSION_RE.finditer(positive_text)),
        None,
    )
    discussion_after_action = first_action is not None and (
        first_discussion is None or first_action < first_discussion
    )
    action_requested = bool(
        (has_action or resume)
        and (not discussion or force_action or resume or discussion_after_action)
    )
    media_submission = bool(
        action_requested
        and _MEDIA_ACTION_RE.search(text)
        and not (discussion and not (force_action or resume or discussion_after_action))
    )
    target_mutation = bool(
        action_requested
        and (
            _MUTATION_RE.search(text)
            or re.search(r"(?:这个|当前|该)\s*(?:节点|镜头|任务)", text)
        )
        and not (media_submission and explicit_creation)
    )

    if _MEMORY_RE.search(text) and not action_requested:
        kind: IntentKind = "memory_teaching"
        reason_codes = ("memory_language",)
    elif resume:
        kind = "workflow_resume"
        reason_codes = ("resume_language",)
    elif media_submission:
        kind = "media_submission"
        reason_codes = ("media_action",)
    elif target_mutation or explicit_creation:
        kind = "canvas_mutation"
        reason_codes = ("canvas_action",)
    elif discussion:
        kind = "discussion"
        reason_codes = ("discussion_language",)
    elif has_action:
        kind = "plan"
        reason_codes = ("action_language_without_submission",)
    elif _is_bare_approval(text):
        # 放在最后：只有讨论、动作、媒体、续跑都不成立时才轮到它。
        # 放前面会抢走「介绍一下这个功能」这类问句的只读判定。
        kind = "user_authorization"
        reason_codes = ("explicit_approval_language",)
    else:
        kind = "observation"
        reason_codes = ("no_action_language",)

    if negated:
        reason_codes = (*reason_codes, "negated_action")
    if force_action:
        reason_codes = (*reason_codes, "explicit_action_modifier")
    return AgentIntent(
        kind=kind,
        action_requested=action_requested,
        media_submission_requested=media_submission,
        resume_requested=resume,
        target_mutation_requested=target_mutation,
        explicit_creation_requested=explicit_creation,
        negated_action=negated,
        discussion_requested=discussion,
        reason_codes=reason_codes,
    )


__all__ = [
    "AgentIntent",
    "IntentKind",
    "classify_agent_intent",
    "strip_negated_action_clauses",
]
