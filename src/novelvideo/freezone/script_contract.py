"""分镜脚本表的可判定合同：8 段式解析、校验与自动修复。

检查角色卡逐字一致、全片视觉风格和提示词结构。技术参数按各镜观看目的选择，
不要求全片焦段、光圈和景深相同。

本模块把那些约束变成可判定的规则，并提供**只做机械修复**的修复器：能靠"取首次出现"消掉的
差异（角色卡、风格段、时长）就地修好；需要人来决定的一律只报不改。

规则 ID 沿用 `filmcraft_kb` 的 `*.v1` 风格，便于后续把重复出现的失败晋升成规则。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

SCRIPT_CONTRACT_SCHEMA = "freezone.script-contract.v1"

# 参考图配额：与前端 `SCRIPT_REFERENCE_IMAGE_CAP` 同一口径。
SCRIPT_REFERENCE_IMAGE_CAP = 9

SCRIPT_MEDIA_ACTION_STORYBOARD_IMAGES = "storyboard-images"
SCRIPT_MEDIA_ACTION_SHOT_VIDEOS = "shot-videos"

# These shape and continuity checks remain part of WorkflowRun's generated-script
# contract, but must not prevent a user from submitting a valid Canvas prompt.
CANVAS_NON_BLOCKING_SCRIPT_RULES = frozenset(
    {
        "script.shot_prompt.segments.v1",
        "script.shot_prompt.order.v1",
        "script.character_card.verbatim.v1",
        "script.style.singleton.v1",
        "script.technical.singleton.v1",
        "script.motion.segments.v1",
        "script.camera.single.v1",
    }
)

SHOT_PROMPT_SEGMENT_COUNT = 8
MOTION_PROMPT_SEGMENT_COUNT = 6

# --------------------------------------------------------------------------- #
# 可看性提醒阈值（剧本层）
# --------------------------------------------------------------------------- #
#
# 上面那些规则管的是**对不对**（段数、段序、角色卡逐字、运镜唯一）。它们全绿，片子照样
# 可能没法看：实测一部 2 分钟 28 镜的成片，台词镜占 89%（时长占 91%）、胸像类景别 21 镜、
# 开场 47 秒静态对峙、高潮镜里对手根本不在画面——技术上全合规，观众看到的是"配了插图的
# 广播剧"。这批阈值管的是**能不能看**，但按 libtv / TapNow / oiioii 的生成前行为，它们
# 只给提醒，不挡生成：创作取舍由用户决定。
#
# 阈值是**起点不是定论**：真实数据积累后按项目调整。
VIEWABILITY_DIALOGUE_SHARE_ADVISORY = 0.60

#: 贴身景别（中景 / 近景 / 特写）占比。整片都是胸像，观众永远在同一种距离上看人。
VIEWABILITY_PORTRAIT_SHARE_ADVISORY = 0.50

#: 连续「有台词且无动作」的段落时长。对峙是戏，但堆到几十秒就只是把同一件事说很多遍。
VIEWABILITY_STANDOFF_SECONDS_ADVISORY = 20.0

#: 动作镜占比。低到某个程度，打戏就只是台词之间的过场。
VIEWABILITY_ACTION_SHARE_ADVISORY = 0.25

#: 单镜最长时长。爆点需要 1–2 秒的快切；一镜超过这个长度就没有长短对比。
VIEWABILITY_MAX_SHOT_SECONDS_ADVISORY = 5.0

#: 景别至少要跨到几档。
VIEWABILITY_FRAMING_FAMILIES_MIN = 3

#: 纯画面（无台词）镜至少几个——它们是呼吸点，也是观众看清空间的机会。
VIEWABILITY_BREATH_SHOTS_MIN = 3

#: 可看性只在"一部片子"的量级上判定。两三行的夹具谈不上台词占比与景别多样性，
#: 在那里报百分比只会制造噪音。
VIEWABILITY_MIN_SHOTS = 8

#: 景别 → 档位。判定规则是**最先出现的档位优先**，同位置时长词优先：
#: shot 列按惯例是「起幅 / 落幅」或「主景别 / 角度」，起幅写在最前面，
#: 所以 `特写转中近景` 记特写、`大远景 / 俯角` 记大远景（不会被 `远景` 吃掉）。
FRAMING_FAMILY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("大特写", "特写"),
    ("大远景", "大远景"),
    ("中远景", "中远景"),
    ("中全景", "全景"),
    ("中近景", "近景"),
    ("远景", "远景"),
    ("全景", "全景"),
    ("中景", "中景"),
    ("近景", "近景"),
    ("特写", "特写"),
)

# 相邻镜头的景别至少跨两档；同档和相邻档都报问题。大远景到远景、近景到特写都属于
# 相邻档，不能只靠换一个角度假装产生了剪辑变化。
FRAMING_FAMILY_ORDER: tuple[str, ...] = (
    "大远景", "远景", "全景", "中远景", "中景", "近景", "特写"
)

#: 贴身景别档位——占比过高就是"胸像广播剧"。
PORTRAIT_FRAMING_FAMILIES = frozenset({"中景", "近景", "特写"})

#: 动作判据只扫 `character_action` 与 `visual_description` 两列。
#: 刻意不扫运镜列（推/摇/移是镜头在动，不是画面里有人在做动作），也不扫情绪列。
ACTION_KEYWORDS: tuple[str, ...] = (
    "交手", "出剑", "拔剑", "挥剑", "举剑", "劈", "斩", "刺", "挥", "砍", "戳",
    "撞", "砸", "摔", "踹", "踢", "扑", "夺", "抢", "抓", "扯", "推", "拉", "掀",
    "奔", "冲", "跑", "跃", "跳", "滚", "逃", "追", "拦", "挡", "格挡", "招架",
    "倒地", "跪下", "跪地", "震退", "掀翻", "炸", "爆", "轰", "喷", "溅", "扑倒",
)

#: 对白列的"没有台词"占位写法，与前端 `scriptRowsToText.ts` 同一口径。
NO_DIALOGUE_VALUES = frozenset(
    {"", "无", "没有", "无台词", "无对白", "没有台词", "没有对白", "none", "n/a"}
)

# 段序规范：`shot_prompt` 第 1-8 段。识别用左侧标签关键词，按顺序首个命中即定角色，
# 所以识别是**顺序敏感**的——段序被打乱会被 `script.shot_prompt.order.v1` 抓到。
SHOT_SEGMENT_ORDER: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "composition",
        ("画面构图", "构图"),
    ),
    (
        "character_card",
        (
            "角色卡",
            "主体描述",
            "角色描述",
            # 无角色镜头的合法写法：「如果没有角色，则写主体/核心对象描述」（见
            # `FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT` 第 2 段）。漏掉它会把空镜判成段序错误。
            "核心对象",
            "对象描述",
        ),
    ),
    ("spatial", ("主体/人物空间", "人物空间", "空间与互动", "互动关系")),
    ("micro", ("微表情", "主体状态", "关键视觉信息")),
    ("environment", ("场景环境", "前景/背景道具", "背景道具", "环境元素")),
    ("lighting", ("光影几何", "大气效果", "光影")),
    ("style", ("视觉风格", "质感")),
    ("technical", ("技术参数", "镜头焦段", "焦段")),
)

SHOT_SEGMENT_LABELS_ZH: dict[str, str] = {
    "composition": "第 1 段 · 画面构图",
    "character_card": "第 2 段 · 角色卡/主体描述",
    "spatial": "第 3 段 · 主体空间与互动关系",
    "micro": "第 4 段 · 微表情与主体状态",
    "environment": "第 5 段 · 场景环境与前景背景道具",
    "lighting": "第 6 段 · 光影几何与大气效果",
    "style": "第 7 段 · 视觉风格/质感",
    "technical": "第 8 段 · 技术参数",
}

# 运动稿段序：第 1-6 段。
MOTION_SEGMENT_ORDER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("camera", ("摄影机运镜", "运镜轨迹", "运镜", "摄影机安排", "camera")),
    ("subject_action", ("主体", "物理动作", "状态变化")),
    ("environment_motion", ("环境物理动态", "环境动态")),
    ("sound", ("音效", "氛围描述")),
    ("dialogue", ("对话台词", "台词", "语气")),
    ("duration", ("时长",)),
)

MOTION_SEGMENT_LABELS_ZH: dict[str, str] = {
    "camera": "运动稿第 1 段 · 运镜轨迹与速度",
    "subject_action": "运动稿第 2 段 · 主体物理动作",
    "environment_motion": "运动稿第 3 段 · 环境物理动态",
    "sound": "运动稿第 4 段 · 音效与氛围",
    "dialogue": "运动稿第 5 段 · 对话台词与语气",
    "duration": "运动稿第 6 段 · 时长",
}

# 角色卡段里的角色卡块：`[角色ID: 描述]`。经 `split_prompt_segments` 剥掉最外层括号后，
# 一段里不会再有嵌套括号，所以这里按"不含括号的最短块"取即可。
_CARD_BLOCK_RE = re.compile(r"\[([^\[\]]{1,300}?)\]")
_CARD_NAME_RE = re.compile(r"^([^:：]{1,40})[:：]")


def card_blocks(segment: str) -> list[tuple[str, str]]:
    """把角色卡段拆成 `(角色名, 整块文本)` 列表。

    **必须按块比较与替换，不能按整段比较**：一个镜头里出现两个角色时，卡片段是
    `角色卡/主体描述：[A: …] / [B: …]`——同一段里装着两张卡。按整段比对会把
    「A 单角色行」与「A+B 双角色行」判成 A 不一致，按整段替换更会把 B 的卡整张删掉
    （2026-09-18 真机首次生成就复现了这条数据丢失）。
    """

    blocks: list[tuple[str, str]] = []
    for match in _CARD_BLOCK_RE.finditer(str(segment or "")):
        inner = match.group(1)
        name_match = _CARD_NAME_RE.match(inner)
        if not name_match:
            continue
        name = name_match.group(1).strip()
        if name:
            blocks.append((name, match.group(0)))
    return blocks

# 参考图编号：`@图片3` 与 `图片3` 两种写法都认（与前端 preflight 同一口径）。
_IMAGE_REF_RE = re.compile(r"@?图片\s*(\d+)")
# 运动稿第 6 段的 `[时长：4.0s]` / `[时长：4秒]`。单位可选，改写时按原样保留，
# 否则 `4.0s` 会被换成 `5ss`。
_DURATION_SEGMENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(s|秒)?")
# 段分隔符：规范是 ` + `；容错地接受只有一侧带空白的加号。**两侧都没有空白时不切**——
# 段内容里的「冷蓝A+B主调」是文字，不是分隔符。
_SEGMENT_SPLIT_RE = re.compile(r"\s+\+\s*|\s*\+\s+")

SEVERITY_BLOCKING = "blocking"
SEVERITY_ADVISORY = "advisory"

RULE_CATALOG: dict[str, dict[str, str]] = {
    "script.duration.total_budget.v1": {
        "severity": SEVERITY_ADVISORY, "summary": "整片时长与用户目标的实际差异，仅提醒",
    },
    "script.plan.sequence_membership.v1": {
        "severity": SEVERITY_ADVISORY, "summary": "核对逐镜归属与导演段落镜号",
    },
    "script.keyframe.duplicate_plan.v1": {
        "severity": SEVERITY_ADVISORY, "summary": "拒绝同镜内状态、职责、用途及构图均重复的补图计划",
    },
    "script.keyframe.input.v1": {
        "severity": SEVERITY_BLOCKING, "summary": "独立构图须有取景关系及新增信息，生成策略必须有效",
    },
    "script.continuity.character_state.v1": {
        "severity": SEVERITY_ADVISORY, "summary": "核对同场连续人物的服装装备接续",
    },
    "script.shot_prompt.segments.v1": {
        "severity": SEVERITY_BLOCKING,
        "summary": "分镜提示词必须是 8 段式",
    },
    "script.shot_prompt.order.v1": {
        "severity": SEVERITY_BLOCKING,
        "summary": "分镜提示词的段序必须与规范一致",
    },
    "script.character_card.verbatim.v1": {
        "severity": SEVERITY_BLOCKING,
        "summary": "同一角色的角色卡必须逐字一致",
    },
    "script.style.singleton.v1": {
        "severity": SEVERITY_BLOCKING,
        "summary": "第 7 段（视觉风格）全篇只能有一份",
    },
    "script.assets.definition_consistency.v1": {
        "severity": SEVERITY_ADVISORY,
        "summary": "同名场景与道具的基准设计说明需要核对",
    },
    "script.motion.segments.v1": {
        "severity": SEVERITY_BLOCKING,
        "summary": "视频运动提示词必须是 6 段式",
    },
    "script.camera.single.v1": {
        "severity": SEVERITY_ADVISORY,
        "summary": "多个运镜名称仅提示核对时序与路线，允许有理由的复合运动",
    },
    "script.camera.contradiction.v1": {
        "severity": SEVERITY_ADVISORY,
        "summary": "提示明确全程移动或静止与相反摄影安排的可能冲突",
    },
    "script.motion.duration_match.v1": {
        "severity": SEVERITY_ADVISORY,
        "summary": "运动稿写的时长必须与时长列一致",
    },
    "script.reference.budget.v1": {
        "severity": SEVERITY_ADVISORY,
        "summary": f"单镜参考图不超过 {SCRIPT_REFERENCE_IMAGE_CAP} 张且编号连续",
    },
    "script.shot_no.sequence.v1": {
        "severity": SEVERITY_ADVISORY,
        "summary": "镜号必须从 1 起连续",
    },
    "script.viewability.dialogue_ratio.v1": {
        "severity": SEVERITY_ADVISORY,
        "summary": f"台词镜占比：建议 ≤{VIEWABILITY_DIALOGUE_SHARE_ADVISORY:.0%}",
    },
    "script.viewability.framing_mix.v1": {
        "severity": SEVERITY_ADVISORY,
        "summary": f"景别至少跨 {VIEWABILITY_FRAMING_FAMILIES_MIN} 档，且不要整片都是胸像",
    },
    "script.viewability.static_standoff.v1": {
        "severity": SEVERITY_ADVISORY,
        "summary": "连续「有台词且无动作」的段落不宜过长",
    },
    "script.viewability.action_share.v1": {
        "severity": SEVERITY_ADVISORY,
        "summary": f"动作镜占比建议 ≥{VIEWABILITY_ACTION_SHARE_ADVISORY:.0%}",
    },
    "script.viewability.pacing.v1": {
        "severity": SEVERITY_ADVISORY,
        "summary": "单镜时长要有长短对比，且保留足量纯画面呼吸镜",
    },
    "script.continuity.adjacent_framing.v1": {
        "severity": SEVERITY_ADVISORY,
        "summary": "相邻同场镜头景别相同或相邻，需要检查是否有意保持",
    },
    "script.continuity.repeated_visual_event.v1": {
        "severity": SEVERITY_ADVISORY,
        "summary": "相邻镜头不应重复同一个视觉事件",
    },
    "script.continuity.missing_states.v1": {
        "severity": SEVERITY_ADVISORY,
        "summary": "明确连续动作需要上一镜结束状态与下一镜开始状态",
    },
    "script.planning.framing_run.v1": {
        "severity": SEVERITY_ADVISORY,
        "summary": "连续三镜以上同景别需要检查信息与节奏变化",
    },
    "script.planning.opening_dialogue.v1": {
        "severity": SEVERITY_ADVISORY,
        "summary": "开场前三秒只有静态对白，需要检查视觉吸引力",
    },
    "script.continuity.screen_direction.v1": {
        "severity": SEVERITY_ADVISORY,
        "summary": "同场相邻镜头的明确左右方向不能无解释反转",
    },
}


@dataclass
class ScriptIssue:
    """一条合同缺陷。`fixed=True` 表示修复器已经就地改正。"""

    rule_id: str
    severity: str
    message: str
    row_index: int
    shot_no: str
    field: str
    fixed: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
            "row_index": self.row_index,
            "shot_no": self.shot_no,
            "field": self.field,
            "fixed": self.fixed,
            "detail": dict(self.detail),
        }


@dataclass
class ScriptContractReport:
    """整表校验结果。`rows` 为修复后的表（未修复时与输入等值）。

    `metrics` 是可看性口径的实测值（台词占比、景别分布、动作占比……）。它不进任何判定，
    只用来把"这部片子长什么样"如实报给下游——前端显示、验收对账、跨项目比较都读它。
    """

    schema: str = SCRIPT_CONTRACT_SCHEMA
    rows: list[dict[str, Any]] = field(default_factory=list)
    issues: list[ScriptIssue] = field(default_factory=list)
    rules_checked: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    #: 这份报告对应的整表内容指纹。它只用于判断报告是否仍然新鲜，不参与任何业务判定。
    rows_fingerprint: str = ""
    planning_fingerprint: str = ""

    @property
    def fixed_count(self) -> int:
        return sum(1 for issue in self.issues if issue.fixed)

    @property
    def blocking(self) -> list[ScriptIssue]:
        return [
            issue
            for issue in self.issues
            if issue.severity == SEVERITY_BLOCKING and not issue.fixed
        ]

    @property
    def advisory(self) -> list[ScriptIssue]:
        return [
            issue
            for issue in self.issues
            if issue.severity == SEVERITY_ADVISORY and not issue.fixed
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "rules_checked": list(self.rules_checked),
            "issue_count": len(self.issues),
            "fixed_count": self.fixed_count,
            "blocking_count": len(self.blocking),
            "advisory_count": len(self.advisory),
            "metrics": dict(self.metrics),
            "rows_fingerprint": self.rows_fingerprint,
            "planning_fingerprint": self.planning_fingerprint,
            "issues": [issue.as_dict() for issue in self.issues],
        }


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


def split_prompt_segments(text: str) -> list[str]:
    """按 ` + ` 切段并去掉每段最外层的方括号。

    刻意不要求每段都带方括号：模型偶尔漏掉一对，那是段序/段数规则该报的事，
    不该让解析器在这里抛错。**括号里的 ` + ` 不是段分隔符**：两人同框时
    角色卡会长成 `[A: …] + [B: …]`，那是同一段里的两张卡（2026-09-29 真机
    生成里一镜被切成 9 段，就是这里误判）。
    """

    raw = str(text or "").strip()
    if not raw:
        return []

    chunks: list[str] = []
    depth = 0
    start = 0
    last = len(raw) - 1
    for index, char in enumerate(raw):
        if char == "[":
            depth += 1
            continue
        if char == "]":
            depth = max(0, depth - 1)
            continue
        # 分隔符必须是括号外的加号，且至少一侧带空白（`A+B` 是正文）。
        if char != "+" or depth:
            continue
        before = raw[index - 1] if index > 0 else ""
        after = raw[index + 1] if index < last else ""
        if before.isspace() or after.isspace():
            chunks.append(raw[start:index])
            start = index + 1
    chunks.append(raw[start:])

    parts: list[str] = []
    for chunk in chunks:
        cleaned = chunk.strip()
        if not cleaned:
            continue
        if cleaned.startswith("[") and cleaned.endswith("]"):
            cleaned = cleaned[1:-1].strip()
        parts.append(cleaned)
    return parts


def _segment_label(segment: str) -> str:
    """取段内第一个冒号之前的标签文字。"""

    head = re.split(r"[:：]", segment, maxsplit=1)[0]
    return head.strip()


_CAMERA_LABEL_RE = re.compile(r"^(运镜|摄影机运镜|明确的摄影机运镜|摄影机安排|镜头安排|camera)", re.I)
_CAMERA_BODY_RE = re.compile(
    r"^(?:(?:前半段|后半段|起始|开始时|先)\s*)?"
    r"(?:固定(?:在|观察|镜头|机位|(?:的)?[^。；，\n]{0,12}机位)|静止机位|锁定机位|摄影机|镜头|机位|跟拍|推轨|摇镜|横移|环绕|locked\s+off|static\s+camera|tracking\s+shot)", re.I,
)


def _camera_segment_text(segment: str) -> str:
    labelled = re.match(r"^\[([^\[\]]+)\]\s*[:：]?\s*(.*)$", segment, re.S)
    if labelled and _CAMERA_LABEL_RE.match(labelled[1]):
        return labelled[2].strip()
    parts = re.split(r"[:：]", segment, maxsplit=1)
    if _CAMERA_LABEL_RE.match(parts[0]):
        return parts[1].strip() if len(parts) == 2 else ""
    return segment if _CAMERA_BODY_RE.match(segment) else ""


def camera_direction_text(prompt: str) -> str:
    for segment in split_prompt_segments(prompt):
        text = _camera_segment_text(segment)
        if text:
            return text
    return ""


def camera_direction_needs_review(text: str) -> bool:
    # shortcut: explicit Chinese contradictions only; ambiguous routes require director review.
    authored = re.sub(r"(?:不要|禁止|不得|不是|并非)[^。；;，,\n]*", "", text)
    authored = re.sub(r"(?:前半段|后半段|[^。；;，,\n]*期间)[^。；;，,\n]*", "", authored)
    authored = re.sub(r"[^。；;，,\n]*(?:停下|停住|停止|站定)(?:后|时)[^。；;，,\n]*", "", authored)
    always_moving = re.search(r"(?:摄影机|镜头|机位)(?:始终|全程|一直)(?:保持)?(?:跟随|跟拍|移动|横移|平移|推进|推近|拉远|环绕)", authored)
    stopped = re.search(r"(?:摄影机|镜头|机位)(?:随后|此时|保持)?(?:不再移动|不移动|静止|固定不动|停住|停止移动)", authored)
    always_still = re.search(r"(?:摄影机|镜头|机位)(?:始终|全程|一直)(?:保持)?(?:静止|不动|固定(?!构图|景别|中景|背影))", authored)
    moving = re.search(r"(?:摄影机|镜头|机位)(?:缓慢|快速|随后|开始|向左|向右|向前|向后)*(?:跟随|跟拍|移动|横移|平移|推进|推近|拉远|环绕)", authored)
    return bool((always_moving and stopped) or (always_still and moving))


def classify_segments(
    text: str,
    order: Sequence[tuple[str, tuple[str, ...]]],
) -> list[str | None]:
    """把每段判定成一个角色；判不出来（或与前段重复）的记为 `None`。

    顺序敏感：同一角色只认第一次出现，重复出现即视为段序错误。
    """

    roles: list[str | None] = []
    used: set[str] = set()
    for segment in split_prompt_segments(text):
        label = _segment_label(segment)
        matched: str | None = None
        for role, keywords in order:
            if role in used:
                continue
            if (role == "camera" and (_CAMERA_LABEL_RE.match(label.lstrip("[")) or _camera_segment_text(segment))) or (
                role != "camera" and any(keyword in label for keyword in keywords)
            ):
                matched = role
                break
        if matched is not None:
            used.add(matched)
        roles.append(matched)
    return roles


def character_ids_in_card(segment: str) -> list[str]:
    """从角色卡段里取出全部 `[角色ID: ...]` 的 ID，按出现顺序去重。"""

    seen: list[str] = []
    for name, _block in card_blocks(segment):
        if name and name not in seen:
            seen.append(name)
    return seen


def _row_text(row: Mapping[str, Any], key: str) -> str:
    return str(row.get(key) or "").strip()


def _shot_no_of(row: Mapping[str, Any], index: int) -> str:
    for key in ("display_shot_no", "shot_no"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return str(index + 1)


def _coerce_seconds(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _DURATION_SEGMENT_RE.search(str(value or ""))
    return float(match.group(1)) if match else None


def script_row_key(row: Mapping[str, Any], index: int) -> str:
    """Return the stable identity shared with the canvas node derivation."""

    shot_id = _row_text(row, "shot_id")
    if shot_id:
        return shot_id
    shot_no = _row_text(row, "shot_no")
    if shot_no:
        return f"shot:{shot_no}"
    keyframe = row.get("keyframe_index")
    if isinstance(keyframe, (int, float)) and not isinstance(keyframe, bool):
        value = int(keyframe) if float(keyframe).is_integer() else keyframe
        return f"kf:{value}"
    return f"idx:{index}"


def _stable_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_ROW_FINGERPRINT_METADATA_KEYS = frozenset(
    {
        "shot_id",
        "shot_order",
        "shot_no",
        "display_shot_no",
        "keyframe_index",
    }
)


def script_row_fingerprint(row: Mapping[str, Any], index: int = 0) -> str:
    """Return a stable production-content fingerprint shared with the web client.

    Identity and display fields are deliberately excluded: array position is the
    authoritative order, while ``script_row_key`` owns identity.  This keeps the
    fingerprint stable whether a row has just been loaded (and normalised with
    the three identity fields) or directly edited in the client (raw row).
    """

    def canonical_numbers(value: Any) -> Any:
        # JavaScript JSON serializes integral floats as integers.
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, dict):
            return {key: canonical_numbers(item) for key, item in value.items()}
        if isinstance(value, list):
            return [canonical_numbers(item) for item in value]
        return value

    production = {
        key: canonical_numbers(value)
        for key, value in dict(row).items()
        if key not in _ROW_FINGERPRINT_METADATA_KEYS
    }
    return _stable_json_sha256({"order": index + 1, "row": production})


def script_rows_fingerprint(rows: Iterable[Mapping[str, Any]]) -> str:
    """给脚本行做稳定内容指纹。

    这份指纹只解决一个问题：报告落盘之后，用户或外部工具又改了行，旧报告还能不能信。
    因此它不参与合同判定，也不进入提示词；只按排序后的 JSON 内容计算，键顺序变化不算修改，
    值变化一定算修改。
    """

    return _stable_json_sha256([dict(row) for row in rows])


def script_planning_fingerprint(director_plan: Mapping[str, Any] | None, target_duration_seconds: float | None = None) -> str:
    return _stable_json_sha256({"plan": director_plan or {}, "target": target_duration_seconds})


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #


def validate_script_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    camera_names: Sequence[str] | None = None,
    director_plan: Mapping[str, Any] | None = None,
    target_duration_seconds: float | None = None,
) -> ScriptContractReport:
    """跑全部合同规则。不做任何修改。"""

    table = [dict(row) for row in rows]
    report = ScriptContractReport(
        rows=table,
        rules_checked=list(RULE_CATALOG),
        rows_fingerprint=script_rows_fingerprint(table),
        planning_fingerprint=script_planning_fingerprint(director_plan, target_duration_seconds),
    )

    if not table:
        return report

    parsed: list[dict[str, Any]] = [_parse_row(row, index) for index, row in enumerate(table)]

    _check_shot_prompt_shape(table, parsed, report)
    _check_character_cards(table, parsed, report)
    _check_asset_definitions(table, report)
    _check_singleton(table, parsed, report, role="style", field="shot_prompt",
                     rule_id="script.style.singleton.v1", label=SHOT_SEGMENT_LABELS_ZH["style"])
    _check_motion_shape(table, parsed, report)
    _check_camera_single(table, parsed, report, camera_names=camera_names)
    _check_duration_match(table, parsed, report)
    _check_reference_budget(table, report)
    _check_shot_no_sequence(table, report)
    _check_viewability(table, report)
    _check_continuity(table, report)
    _check_shot_planning(table, report)
    _check_film_plan(table, director_plan or {}, target_duration_seconds, report)
    _check_character_states(table, report)
    for index, row in enumerate(table):
        report.issues.extend(_keyframe_duplicate_issues(row, index))
        report.issues.extend(_keyframe_input_issues(row, index))
    return report


def _keyframe_text_key(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKC", value).strip().lower()
    if value in {"", "无", "没有", "none", "n/a", "-", "—"}:
        return ""
    return re.sub(r"\.+$", "", re.sub(r"[\s,，。;；!?！？、]+", "", value))


def _keyframe_duplicate_issues(row: Mapping[str, Any], row_index: int) -> list[ScriptIssue]:
    # ponytail: text repeats only; real visual equivalence needs image review.
    plan = row.get("keyframe_plan")
    if not isinstance(plan, list):
        return []
    start_key = _keyframe_text_key(row.get("start_state"))
    seen: dict[tuple[str, str, str, str, str], int] = {}
    issues: list[ScriptIssue] = []
    for index, item in enumerate(plan):
        if not isinstance(item, Mapping):
            continue
        state_key = _keyframe_text_key(item.get("state"))
        if not state_key:
            continue
        purpose_key = _keyframe_text_key(item.get("purpose"))
        role = item.get("role") if isinstance(item.get("role"), str) else "action_state"
        framing_key = _keyframe_text_key(item.get("framing"))
        strategy = item.get("generation_strategy") or "state_edit"
        signature = (_keyframe_text_key(role), state_key, purpose_key, framing_key,
                     strategy if isinstance(strategy, str) else "")
        detail: dict[str, Any] = {"keyframe_index": index}
        if state_key == start_key and not purpose_key and not framing_key and signature[0] == "action_state" and strategy != "independent":
            detail["reason"] = "opening_state"
            message = f"状态画面 {index + 1} 与首帧文字状态相同且没有新增用途，已拒绝重复补图；真实画面差异未检查"
        elif signature in seen:
            previous = seen[signature]
            detail.update(reason="duplicate_plan", duplicate_of=previous)
            message = f"状态画面 {index + 1} 的状态、职责和用途与状态画面 {previous + 1} 相同，已拒绝重复补图；真实画面差异未检查"
        else:
            seen[signature] = index
            continue
        issues.append(ScriptIssue(
            rule_id="script.keyframe.duplicate_plan.v1", severity=SEVERITY_ADVISORY,
            message=message, row_index=row_index, shot_no=_shot_no_of(row, row_index),
            field="keyframe_plan", detail=detail,
        ))
    return issues


def _keyframe_input_issues(row: Mapping[str, Any], row_index: int) -> list[ScriptIssue]:
    plan = row.get("keyframe_plan")
    if not isinstance(plan, list):
        return []
    issues: list[ScriptIssue] = []
    for index, item in enumerate(plan):
        if not isinstance(item, Mapping) or not _keyframe_text_key(item.get("state")):
            continue
        strategy = item.get("generation_strategy")
        if strategy not in (None, "", "independent", "state_edit"):
            message = "关键画面的生成方式无效，请选择独立构图或动作改图"
        elif strategy == "independent" and not _keyframe_text_key(item.get("framing")):
            message = "独立构图缺少景别、视点和取景关系，请补充后再出图"
        elif strategy == "independent" and not _keyframe_text_key(item.get("purpose")):
            message = "独立构图缺少新增信息的说明，请补充本图用途后再出图"
        else:
            continue
        issues.append(ScriptIssue(
            rule_id="script.keyframe.input.v1", severity=SEVERITY_BLOCKING,
            message=message, row_index=row_index, shot_no=_shot_no_of(row, row_index),
            field="keyframe_plan", detail={"keyframe_index": index},
        ))
    return issues


def _check_film_plan(table: list[dict[str, Any]], plan: Mapping[str, Any], target: float | None, report: ScriptContractReport) -> None:
    target = target if target is not None else plan.get("target_duration_seconds")
    if isinstance(target, (int, float)) and not isinstance(target, bool) and math.isfinite(target) and target > 0:
        total = sum(_coerce_seconds(row.get("duration")) or 0 for row in table)
        report.metrics.update(target_duration_seconds=target, total_seconds=total)
        if abs(total - target) > 1.0 + 1e-9:
            report.issues.append(ScriptIssue(
                rule_id="script.duration.total_budget.v1", severity=SEVERITY_ADVISORY,
                message=f"整片目标{target:g}秒，当前脚本合计{total:g}秒；允许为连续表演合理超时，此项仅提醒，不拦截生成、不自动裁剪",
                row_index=-1, shot_no="", field="duration", detail={"target_seconds": target, "actual_seconds": total, "tolerance_seconds": 1},
            ))
    sequences = plan.get("sequences")
    if not isinstance(sequences, list):
        return
    for index, row in enumerate(table):
        declared = row.get("sequence_ids")
        if not declared:  # Historical rows have no ownership declaration.
            continue
        expected = {str(sequence["sequence_id"]) for sequence in sequences if isinstance(sequence, Mapping) and sequence.get("sequence_id") and str(row.get("shot_no")) in {str(number) for number in (sequence.get("shot_nos") or [])}}
        if not isinstance(declared, list) or not all(isinstance(value, str) for value in declared) or set(declared) != expected:
            report.issues.append(ScriptIssue(
                rule_id="script.plan.sequence_membership.v1", severity=SEVERITY_ADVISORY,
                message="本镜声明的段落归属与导演规划镜号不一致，请核对实际内容并同步段落",
                row_index=index, shot_no=_shot_no_of(row, index), field="sequence_ids",
                detail={"declared": declared, "expected": sorted(expected)},
            ))


def _check_character_states(table: Sequence[Mapping[str, Any]], report: ScriptContractReport) -> None:
    for index in range(1, len(table)):
        previous, current = table[index - 1], table[index]
        transition = _row_text(previous, "transition_plan")
        continuous = bool(re.search(r"continuous[_-]action|连续动作|动作衔接", transition, re.IGNORECASE))
        same_scene = bool(_continuity_scene(previous)) and _continuity_scene(previous) == _continuity_scene(current)
        # ponytail: explicit ellipsis cues only; semantic legitimacy still needs film review.
        ellipsis = bool(re.search(r"时间省略|换装省略|时空跳跃|蒙太奇|ellipsis|montage", transition, re.IGNORECASE))
        if not continuous and (not same_scene or ellipsis):
            continue
        before, after = previous.get("character_state_end"), current.get("character_state_start")
        if not isinstance(before, Mapping) or not isinstance(after, Mapping):
            continue
        for name in before.keys() & after.keys():
            if re.sub(r"\s+", "", str(before[name])) == re.sub(r"\s+", "", str(after[name])):
                continue
            report.issues.append(ScriptIssue(
                rule_id="script.continuity.character_state.v1", severity=SEVERITY_ADVISORY,
                message=f"角色「{name}」的服装装备没有接住前镜结束状态；保持状态，或在授权范围内交代变化与时间省略",
                row_index=index, shot_no=_shot_no_of(current, index), field="character_state_start",
                detail={"character": name, "previous_row_index": index - 1, "before": before[name], "after": after[name]},
            ))


def _inject_character_states(row: dict[str, Any]) -> None:
    """Compile typed current states into the existing image/motion segments."""
    for field_name, order, role, include_end in (
        ("shot_prompt", SHOT_SEGMENT_ORDER, "spatial", False),
        ("video_motion_prompt", MOTION_SEGMENT_ORDER, "subject_action", True),
    ):
        prompt = _row_text(row, field_name)
        if not row.get("character_state_start") and "<character_state>" not in prompt:
            continue
        roles = classify_segments(prompt, order)
        if role not in roles:
            continue
        segments = split_prompt_segments(prompt)
        position = roles.index(role)
        body = re.sub(r"\s*<character_state>[\s\S]*?</character_state>", "", segments[position]).strip()
        states = row.get("character_state_start") or {}
        if states:
            start = "；".join(f"{name}：{state}" for name, state in sorted(states.items()))
            state_text = f"镜头开始时，{start}。"
            if include_end and row.get("character_state_end"):
                end = "；".join(f"{name}：{state}" for name, state in sorted(row["character_state_end"].items()))
                state_text += f"镜头结束时，{end}。"
            body += f"\n<character_state>{state_text}本镜状态优先于角色卡和参考图中的基准服装；身份特征保持一致。</character_state>"
        segments[position] = body
        row[field_name] = " + ".join(f"[{segment}]" for segment in segments)


def _check_asset_definitions(table: list[dict[str, Any]], report: ScriptContractReport) -> None:
    for definitions_field, tags_field, label in (
        ("scene_descriptions", "scene_tags", "场景"),
        ("prop_descriptions", "prop_tags", "道具"),
    ):
        baseline: dict[str, tuple[str, int]] = {}
        for index, row in enumerate(table):
            definitions = row.get(definitions_field)
            if not isinstance(definitions, Mapping):
                continue
            tags = {tag.strip() for tag in re.split(r"[、，,；;\n]+", str(row.get(tags_field) or "")) if tag.strip()}
            for name, value in definitions.items():
                if name not in tags or not isinstance(value, str) or not value.strip():
                    continue
                normalized = re.sub(r"\s+", " ", value.strip())
                previous = baseline.setdefault(name, (normalized, index))
                if normalized == previous[0]:
                    continue
                report.issues.append(ScriptIssue(
                    rule_id="script.assets.definition_consistency.v1",
                    severity=SEVERITY_ADVISORY,
                    message=f"{label}「{name}」的基准说明与第 {_shot_no_of(table[previous[1]], previous[1])} 镜不同；核对是否只是补充描述，或把本镜状态写进了基准设计",
                    row_index=index, shot_no=_shot_no_of(row, index), field=definitions_field,
                    detail={"asset": name, "baseline_row_index": previous[1],
                            "baseline": previous[0][:1600], "actual": normalized[:1600]},
                ))


def _parse_row(row: Mapping[str, Any], index: int) -> dict[str, Any]:
    shot_prompt = _row_text(row, "shot_prompt")
    motion_prompt = _row_text(row, "video_motion_prompt")
    shot_segments = split_prompt_segments(shot_prompt)
    motion_segments = split_prompt_segments(motion_prompt)
    return {
        "index": index,
        "shot_no": _shot_no_of(row, index),
        "shot_segments": shot_segments,
        "shot_roles": classify_segments(shot_prompt, SHOT_SEGMENT_ORDER),
        "motion_segments": motion_segments,
        "motion_roles": classify_segments(motion_prompt, MOTION_SEGMENT_ORDER),
    }


def _check_shot_prompt_shape(
    table: list[dict[str, Any]],
    parsed: list[dict[str, Any]],
    report: ScriptContractReport,
) -> None:
    for item in parsed:
        segments = item["shot_segments"]
        # 空提示词不算违约，算**还没写完**：用户插的新行、只出资产的行都长这样。
        # 「这一行还不能开拍」由前端的开拍条件检查负责，合同只管已经写了内容的行。
        if not segments:
            continue
        count = len(segments)
        if count != SHOT_PROMPT_SEGMENT_COUNT:
            report.issues.append(
                ScriptIssue(
                    rule_id="script.shot_prompt.segments.v1",
                    severity=SEVERITY_BLOCKING,
                    message=(
                        f"分镜提示词有 {count} 段，规范要求 {SHOT_PROMPT_SEGMENT_COUNT} 段"
                    ),
                    row_index=item["index"],
                    shot_no=item["shot_no"],
                    field="shot_prompt",
                )
            )
            # 段数都不对时，段序判定只会产生噪声，直接跳过。
            continue
        roles = item["shot_roles"]
        expected = [role for role, _ in SHOT_SEGMENT_ORDER]
        if roles != expected:
            report.issues.append(
                ScriptIssue(
                    rule_id="script.shot_prompt.order.v1",
                    severity=SEVERITY_BLOCKING,
                    message="分镜提示词的段序与规范不一致：" + _describe_role_mismatch(roles),
                    row_index=item["index"],
                    shot_no=item["shot_no"],
                    field="shot_prompt",
                    detail={"actual": roles, "expected": expected},
                )
            )


def _describe_role_mismatch(roles: Sequence[str | None]) -> str:
    """把「第几段本该是什么、实际是什么」说成一句人话，供 UI 直接显示。"""

    expected = [role for role, _ in SHOT_SEGMENT_ORDER]
    parts: list[str] = []
    for position, (role, actual) in enumerate(zip(expected, roles), start=1):
        if role == actual:
            continue
        label = SHOT_SEGMENT_LABELS_ZH.get(role, role)
        got = SHOT_SEGMENT_LABELS_ZH.get(actual or "", actual or "无法识别")
        parts.append(f"第 {position} 段应为「{label}」，实际是「{got}」")
    return "；".join(parts[:3]) or "段序偏离"


def _check_character_cards(
    table: list[dict[str, Any]],
    parsed: list[dict[str, Any]],
    report: ScriptContractReport,
) -> None:
    """同一角色的**角色卡块**必须逐字一致，否则跨镜必然换脸。

    比较单位是卡片块（`[角色ID: …]`），不是整张卡片段——一张段里可以装两个角色。
    """

    canonical: dict[str, str] = {}
    for item in parsed:
        roles = item["shot_roles"]
        if "character_card" not in roles:
            continue
        segment = item["shot_segments"][roles.index("character_card")]
        for name, block in card_blocks(segment):
            reference = canonical.get(name)
            if reference is None:
                canonical[name] = block
                continue
            if block != reference:
                report.issues.append(
                    ScriptIssue(
                        rule_id="script.character_card.verbatim.v1",
                        severity=SEVERITY_BLOCKING,
                        message=f"角色「{name}」的角色卡与本篇首次出现不一致（模型改写或压缩了它）",
                        row_index=item["index"],
                        shot_no=item["shot_no"],
                        field="character_description_1",
                        detail={
                            "character": name,
                            "canonical": reference[:200],
                            "actual": block[:200],
                        },
                    )
                )


def _check_singleton(
    table: list[dict[str, Any]],
    parsed: list[dict[str, Any]],
    report: ScriptContractReport,
    *,
    role: str,
    field: str,
    rule_id: str,
    label: str,
) -> None:
    """第 7 / 8 段必须全篇只有一份：逐镜换焦段或调色读起来就是两部作品。"""

    canonical: str | None = None
    for item in parsed:
        roles = item["shot_roles"]
        if role not in roles:
            continue
        segment = item["shot_segments"][roles.index(role)]
        if canonical is None:
            canonical = segment
            continue
        if segment != canonical:
            report.issues.append(
                ScriptIssue(
                    rule_id=rule_id,
                    severity=SEVERITY_BLOCKING,
                    message=f"{label}逐镜改写；全篇只允许一份，其余行必须逐字照抄",
                    row_index=item["index"],
                    shot_no=item["shot_no"],
                    field=field,
                    detail={"canonical": canonical[:200], "actual": segment[:200]},
                )
            )


def _check_motion_shape(
    table: list[dict[str, Any]],
    parsed: list[dict[str, Any]],
    report: ScriptContractReport,
) -> None:
    for item in parsed:
        if not item["motion_segments"]:
            continue
        count = len(item["motion_segments"])
        if count == MOTION_PROMPT_SEGMENT_COUNT:
            continue
        report.issues.append(
            ScriptIssue(
                rule_id="script.motion.segments.v1",
                severity=SEVERITY_BLOCKING,
                message=(
                    f"视频运动提示词有 {count} 段，规范要求 {MOTION_PROMPT_SEGMENT_COUNT} 段"
                ),
                row_index=item["index"],
                shot_no=item["shot_no"],
                field="video_motion_prompt",
            )
        )


def _camera_vocabulary() -> tuple[str, ...]:
    """本仓 23 条运镜名称。延迟导入，避免模块级循环依赖。"""

    try:
        from novelvideo.freezone.video_node import VIDEO_CAMERA_TEMPLATES
    except Exception:  # pragma: no cover - 只会在极端裁剪环境下发生
        return ()
    return tuple(str(item.get("name") or "") for item in VIDEO_CAMERA_TEMPLATES if item.get("name"))


def _check_camera_single(
    table: list[dict[str, Any]],
    parsed: list[dict[str, Any]],
    report: ScriptContractReport,
    *,
    camera_names: Sequence[str] | None,
) -> None:
    """Multiple named moves need review; lexical matches cannot prove a conflict."""

    names = tuple(camera_names) if camera_names is not None else _camera_vocabulary()
    for item in parsed:
        camera_segment = camera_direction_text(_row_text(table[item["index"]], "video_motion_prompt"))
        if not camera_segment:
            continue
        if camera_direction_needs_review(camera_segment):
            report.issues.append(ScriptIssue(
                rule_id="script.camera.contradiction.v1", severity=SEVERITY_ADVISORY,
                message="摄影指令需确认：全程移动或静止与另一处安排可能冲突；请明确移动阶段、停止触发和结束构图",
                row_index=item["index"], shot_no=item["shot_no"], field="video_motion_prompt",
                detail={"camera": camera_segment},
            ))
        matched = [name for name in names if name and name in camera_segment]
        if len(matched) > 1:
            report.issues.append(
                ScriptIssue(
                    rule_id="script.camera.single.v1",
                    severity=SEVERITY_ADVISORY,
                    message="检测到多个运镜：" + "、".join(matched) + "；请核对先后时序与轨迹是否清楚，有理由的复合运镜可以保留",
                    row_index=item["index"],
                    shot_no=item["shot_no"],
                    field="video_motion_prompt",
                    detail={"matched": matched},
                )
            )


def _check_duration_match(
    table: list[dict[str, Any]],
    parsed: list[dict[str, Any]],
    report: ScriptContractReport,
) -> None:
    """运动稿里写的时长要与时长列一致，否则提交给模型的是自相矛盾的两个数。"""

    for item in parsed:
        roles = item["motion_roles"]
        if "duration" not in roles:
            continue
        segment = item["motion_segments"][roles.index("duration")]
        stated = _coerce_seconds(segment)
        declared = _coerce_seconds(table[item["index"]].get("duration"))
        if stated is None or declared is None or abs(stated - declared) < 1e-6:
            continue
        report.issues.append(
            ScriptIssue(
                rule_id="script.motion.duration_match.v1",
                severity=SEVERITY_ADVISORY,
                message=f"运动稿写 {stated:g}s，时长列是 {declared:g}s",
                row_index=item["index"],
                shot_no=item["shot_no"],
                field="duration",
                detail={"stated": stated, "declared": declared},
            )
        )


def _check_reference_budget(
    table: list[dict[str, Any]],
    report: ScriptContractReport,
) -> None:
    for index, row in enumerate(table):
        for field_name in ("shot_prompt", "video_motion_prompt", "reference"):
            numbers = [int(value) for value in _IMAGE_REF_RE.findall(_row_text(row, field_name))]
            if not numbers:
                continue
            distinct = sorted(set(numbers))
            if len(distinct) > SCRIPT_REFERENCE_IMAGE_CAP:
                report.issues.append(
                    ScriptIssue(
                        rule_id="script.reference.budget.v1",
                        severity=SEVERITY_ADVISORY,
                        message=(
                            f"{field_name} 引用了 {len(distinct)} 张参考图，"
                            f"单镜上限 {SCRIPT_REFERENCE_IMAGE_CAP} 张"
                        ),
                        row_index=index,
                        shot_no=_shot_no_of(row, index),
                        field=field_name,
                        detail={"numbers": distinct},
                    )
                )
            elif distinct != list(range(1, len(distinct) + 1)):
                report.issues.append(
                    ScriptIssue(
                        rule_id="script.reference.budget.v1",
                        severity=SEVERITY_ADVISORY,
                        message=f"{field_name} 的参考图编号不连续：" + "、".join(
                            f"图片{value}" for value in distinct
                        ),
                        row_index=index,
                        shot_no=_shot_no_of(row, index),
                        field=field_name,
                        detail={"numbers": distinct},
                    )
                )


def _check_shot_no_sequence(
    table: list[dict[str, Any]],
    report: ScriptContractReport,
) -> None:
    numbers: list[tuple[int, int, str]] = []
    for index, row in enumerate(table):
        value = str(row.get("shot_no") or "").strip()
        if not value.isdigit():
            continue
        numbers.append((index, int(value), _shot_no_of(row, index)))
    if not numbers:
        return
    actual = [value for _, value, _ in numbers]
    if actual == list(range(1, len(actual) + 1)):
        return
    for position, (index, value, shot_no) in enumerate(numbers, start=1):
        if value != position:
            report.issues.append(
                ScriptIssue(
                    rule_id="script.shot_no.sequence.v1",
                    severity=SEVERITY_ADVISORY,
                    message=f"第 {position} 行的镜号是 {value}；镜号应从 1 起连续",
                    row_index=index,
                    shot_no=shot_no,
                    field="shot_no",
                    detail={"expected": position, "actual": value},
                )
            )


# --------------------------------------------------------------------------- #
# 可看性检查
# --------------------------------------------------------------------------- #


def framing_family(value: object) -> str | None:
    """把景别写法归到档位（大远景/远景/全景/中景/近景/特写）。认不出返回 None。

    **最先出现的档位优先**：shot 列写的是「起幅 / 落幅」或「主景别 / 角度」，
    起幅在前，所以 `特写转中近景` 记特写、`大远景 / 俯角` 记大远景。
    """

    text = str(value or "")
    best: tuple[int, int, str] | None = None
    for keyword, family in FRAMING_FAMILY_KEYWORDS:
        position = text.find(keyword)
        if position < 0:
            continue
        # 先比出现位置，再比词长（同一位置时长词更具体，例如 `大特写` 胜过 `特写`）。
        rank = (position, -len(keyword), family)
        if best is None or rank < best:
            best = rank
    return best[2] if best else None


def framing_family_distance(left: str | None, right: str | None) -> int | None:
    """Return the distance between two recognized framing families."""

    if not left or not right:
        return None
    try:
        return abs(FRAMING_FAMILY_ORDER.index(left) - FRAMING_FAMILY_ORDER.index(right))
    except ValueError:
        return None


def has_dialogue(row: Mapping[str, Any]) -> bool:
    """对白列是否有真台词（"无 / 没有 / 无台词" 这类占位不算）。"""

    value = str(row.get("dialogue") or "").strip()
    return bool(value) and value.casefold() not in NO_DIALOGUE_VALUES


def is_action_row(row: Mapping[str, Any]) -> bool:
    """这一镜的画面里有没有物理动作。

    只扫 `character_action` 与 `visual_description` 两列：运镜列说的是镜头在动，
    情绪列说的是人是什么心情，两者都不等于"画面里有人在做动作"。
    """

    for key in ("character_action", "visual_description"):
        text = str(row.get(key) or "")
        if any(keyword in text for keyword in ACTION_KEYWORDS):
            return True
    return False


def _continuity_scene(row: Mapping[str, Any]) -> str:
    return re.sub(r"\s+", " ", str(row.get("scene_tags") or "").strip()).casefold()


def _camera_angle(row: Mapping[str, Any]) -> str | None:
    text = str(row.get("shot") or "")
    for keyword in ("平视", "俯视", "仰视", "正面", "侧面", "背面", "顶视"):
        if keyword in text:
            return keyword
    return None


def _visual_event(row: Mapping[str, Any]) -> str:
    value = row.get("character_action") or row.get("visual_description") or ""
    return re.sub(r"\s+", " ", str(value).strip()).casefold()


def _screen_direction(row: Mapping[str, Any]) -> str | None:
    """Movement only: placement, camera travel and eyelines are different evidence."""

    text = _row_text(row, "character_action") or _row_text(row, "visual_description")
    text = "；".join(clause for clause in re.split(r"[，,；;。\n]", text) if not re.search(r"镜头|摄影机|camera", clause, re.IGNORECASE))
    # ponytail: conservative literal motion cues; semantic attribution needs model review.
    movement = r"(?:奔跑|跑|行走|走|滑行|滑动|移动|冲刺|跳跃|飞行|飞|滚动|爬行)"
    left = bool(re.search(r"(?:向|朝)(?:画面)?左(?:侧|边)?(?:缓慢|快速|继续|持续|加速)?" + movement, text))
    right = bool(re.search(r"(?:向|朝)(?:画面)?右(?:侧|边)?(?:缓慢|快速|继续|持续|加速)?" + movement, text))
    if left == right:
        return None
    return "left" if left else "right"


def _has_continuity_state(row: Mapping[str, Any], field: str) -> bool:
    return _row_text(row, field).casefold() not in {"", "无", "没有", "none", "n/a", "null", "未知", "待定", "待补", "待补充"}


def _single_continuity_character(row: Mapping[str, Any]) -> str:
    names = {_row_text(row, field) for field in ("character_1", "character_2")}
    names -= {"", "无", "没有", "none", "n/a"}
    return next(iter(names)) if len(names) == 1 else ""


def _check_continuity(table: Sequence[Mapping[str, Any]], report: ScriptContractReport) -> None:
    """Report obvious adjacent repetition without pretending to solve blocking continuity.

    This is intentionally advisory: a deliberate held shot can be valid, but the user should
    see the repetition before paying for storyboard or video generation.
    """

    for index in range(1, len(table)):
        previous = table[index - 1]
        current = table[index]
        # The outgoing transition belongs to the previous shot, including location changes.
        if re.search(
            r"continuous_action|continuous-action|动作衔接|连续动作",
            _row_text(previous, "transition_plan"), re.IGNORECASE,
        ):
            missing = []
            if not _has_continuity_state(previous, "end_state"):
                missing.append({"row_index": index - 1, "field": "end_state"})
            if not _has_continuity_state(current, "start_state"):
                missing.append({"row_index": index, "field": "start_state"})
            for missing_state in missing:
                report.issues.append(ScriptIssue(
                    rule_id="script.continuity.missing_states.v1",
                    severity=SEVERITY_ADVISORY,
                    message=(
                        f"第 {index}→{index + 1} 镜要求连续动作，但缺少"
                        + "、".join(
                            f"第 {item['row_index'] + 1} 镜"
                            + ("结束状态" if item["field"] == "end_state" else "开始状态")
                            for item in missing
                        )
                        + "；生成前请写清切点姿态、位置、运动方向与关键道具状态"
                    ),
                    row_index=missing_state["row_index"],
                    shot_no=_shot_no_of(table[missing_state["row_index"]], missing_state["row_index"]),
                    field=missing_state["field"],
                    detail={"previous_row_index": index - 1, "missing_states": missing},
                ))
        previous_scene = _continuity_scene(previous)
        current_scene = _continuity_scene(current)
        if not previous_scene or previous_scene != current_scene:
            continue
        previous_family = framing_family(previous.get("shot"))
        current_family = framing_family(current.get("shot"))
        previous_angle = _camera_angle(previous)
        current_angle = _camera_angle(current)
        framing_distance = framing_family_distance(previous_family, current_family)
        if framing_distance is not None and framing_distance <= 1 and not _row_text(current, "cut_reason") and not _row_text(previous, "cut_reason"):
            report.issues.append(
                ScriptIssue(
                    rule_id="script.continuity.adjacent_framing.v1",
                    severity=SEVERITY_ADVISORY,
                    message=(
                        f"第 {index}、{index + 1} 镜同场连续使用"
                        f"{previous_family}→{current_family}/{current_angle}，"
                        "景别相同或相邻；请核对观看重点、机位和动作匹配，补充切镜理由。"
                        "正反打、换主体或有意保持时不必改变景别"
                    ),
                    row_index=index,
                    shot_no=_shot_no_of(current, index),
                    field="shot",
                    detail={
                        "previous_row_index": index - 1,
                        "framing": current_family,
                        "previous_framing": previous_family,
                        "framing_distance": framing_distance,
                        "previous_angle": previous_angle,
                        "angle": current_angle,
                    },
                )
            )
        previous_event = _visual_event(previous)
        current_event = _visual_event(current)
        if previous_event and len(previous_event) >= 8 and previous_event == current_event:
            report.issues.append(
                ScriptIssue(
                    rule_id="script.continuity.repeated_visual_event.v1",
                    severity=SEVERITY_ADVISORY,
                    message=f"第 {index}、{index + 1} 镜使用相同视觉事件描述；请核对动作匹配、回放、不同视点或有意重复的表达目的，不强制新增事件",
                    row_index=index,
                    shot_no=_shot_no_of(current, index),
                    field="character_action",
                    detail={"previous_row_index": index - 1},
                )
            )
        previous_direction = _screen_direction(previous)
        current_direction = _screen_direction(current)
        subject = _single_continuity_character(previous)
        if subject and subject == _single_continuity_character(current) and previous_direction and current_direction and previous_direction != current_direction:
            report.issues.append(
                ScriptIssue(
                    rule_id="script.continuity.screen_direction.v1",
                    severity=SEVERITY_ADVISORY,
                    message=(
                        f"第 {index}、{index + 1} 镜同一角色 {subject} 的明确运动方向从"
                        f"{previous_direction}反转为{current_direction}，请确认是否有切轴或转场依据"
                    ),
                    row_index=index,
                    shot_no=_shot_no_of(current, index),
                    field="shot",
                    detail={
                        "previous_row_index": index - 1,
                        "previous_direction": previous_direction,
                        "direction": current_direction,
                        "subject": subject,
                    },
                )
            )


def _check_shot_planning(
    table: Sequence[Mapping[str, Any]], report: ScriptContractReport,
) -> None:
    # These are observable warning signs, not a verdict on dramatic quality.
    start = 0
    while start < len(table):
        family = framing_family(table[start].get("shot"))
        end = start + 1
        while (
            family
            and end < len(table)
            and framing_family(table[end].get("shot")) == family
            and _continuity_scene(table[end]) == _continuity_scene(table[start])
        ):
            end += 1
        if family and end - start >= 3:
            target = (start + end - 1) // 2
            report.issues.append(ScriptIssue(
                rule_id="script.planning.framing_run.v1",
                severity=SEVERITY_ADVISORY,
                message=f"第 {start + 1}–{end} 镜连续使用{family}，请检查是否需要空间镜或细节镜",
                row_index=target,
                shot_no=_shot_no_of(table[target], target),
                field="shot",
                detail={"row_indices": list(range(start, end)), "framing": family},
            ))
        start = end

    opening_seconds = 0.0
    opening_indices: list[int] = []
    for index, row in enumerate(table):
        seconds = _coerce_seconds(row.get("duration")) or 0.0
        if seconds <= 0 or not has_dialogue(row) or is_action_row(row):
            break
        opening_indices.append(index)
        opening_seconds += seconds
        if opening_seconds >= 3:
            report.issues.append(ScriptIssue(
                rule_id="script.planning.opening_dialogue.v1",
                severity=SEVERITY_ADVISORY,
                message="开场前三秒有对白，但未识别到可见动作；请检查是否需要视觉事件。静态画面也可能有吸引力，此项仅供复核",
                row_index=0,
                shot_no=_shot_no_of(table[0], 0),
                field="visual_description",
                detail={"row_indices": opening_indices, "checked_seconds": 3},
            ))
            break


def viewability_metrics(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """按可看性口径实测一张分镜表。纯计算，不做任何判定。

    这是"这部片子长什么样"的一次快照：台词密度、景别分布、动作占比、静戏最长段、
    最长单镜、呼吸镜数量。阈值判定在 `_check_viewability`，这里只负责如实报数。
    """

    table = [dict(row) for row in rows]
    if not table:
        return {}

    total_seconds = 0.0
    dialogue_seconds = 0.0
    dialogue_shots = 0
    action_shots = 0
    breath_shots = 0
    longest_shot = 0.0
    families: list[str] = []
    standoff = 0.0
    longest_standoff = 0.0

    for row in table:
        seconds = _coerce_seconds(row.get("duration")) or 0.0
        total_seconds += seconds
        longest_shot = max(longest_shot, seconds)
        talks = has_dialogue(row)
        acts = is_action_row(row)
        family = framing_family(row.get("shot"))
        if family:
            families.append(family)
        if talks:
            dialogue_shots += 1
            dialogue_seconds += seconds
        else:
            breath_shots += 1
        if acts:
            action_shots += 1
        # 静戏段 = 连续「有台词 + 无动作」。中间插进任何一个动作镜就重新计数——
        # 这正是"43 秒里两个人只是站着说话"与"对话中夹着打斗"的区别。
        if talks and not acts:
            standoff += seconds
            longest_standoff = max(longest_standoff, standoff)
        else:
            standoff = 0.0

    portrait_shots = sum(
        1 for row in table if framing_family(row.get("shot")) in PORTRAIT_FRAMING_FAMILIES
    )

    def share(part: float, whole: float) -> float:
        return round(part / whole, 4) if whole else 0.0

    return {
        "shot_count": len(table),
        "total_seconds": round(total_seconds, 3),
        "dialogue_shot_count": dialogue_shots,
        "dialogue_shot_share": share(dialogue_shots, len(table)),
        "dialogue_seconds": round(dialogue_seconds, 3),
        "dialogue_seconds_share": share(dialogue_seconds, total_seconds),
        "action_shot_count": action_shots,
        "action_shot_share": share(action_shots, len(table)),
        "breath_shot_count": breath_shots,
        "longest_shot_seconds": round(longest_shot, 3),
        "longest_standoff_seconds": round(longest_standoff, 3),
        "framing_families": sorted(set(families)),
        "portrait_shot_count": portrait_shots,
        "portrait_share": share(portrait_shots, len(table)),
    }


def _check_viewability(
    table: list[dict[str, Any]],
    report: ScriptContractReport,
) -> None:
    """在花钱之前回答「这部片子能不能看」。

    每条都只报不改：这些是创作决定，机器不该替作者改剧本，但必须让他看见实测值。
    超到 `BLOCKING` 档说明这已经不是这个体裁了（例如台词镜占九成＝配了插图的广播剧）。
    """

    metrics = viewability_metrics(table)
    if not metrics:
        return
    report.metrics = metrics
    intents = [str(row.get("content_intent") or "other") for row in table]
    action_focused = intents.count("action") > len(intents) / 2
    narrative_focused = action_focused or intents.count("narrative") > len(intents) / 2
    # Unknown/legacy intent is not evidence that a film should meet action-film ratios.
    metrics["content_intents"] = sorted(set(intents))
    if not narrative_focused:
        return
    if metrics["shot_count"] < VIEWABILITY_MIN_SHOTS:
        return

    def flag(
        rule_id: str,
        severity: str,
        message: str,
        field: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        report.issues.append(
            ScriptIssue(
                rule_id=rule_id,
                severity=severity,
                message=message,
                row_index=-1,  # 全表级：没有哪一行可以怪
                shot_no="",
                field=field,
                detail=detail or {},
            )
        )

    # ① 台词密度。两个口径都算：镜数与时长。只提醒，不挡生成。
    duration_share = metrics["dialogue_seconds_share"]
    count_share = metrics["dialogue_shot_share"]
    if duration_share > VIEWABILITY_DIALOGUE_SHARE_ADVISORY:
        flag(
            "script.viewability.dialogue_ratio.v1",
            SEVERITY_ADVISORY,
            (
                f"台词镜占时长 {duration_share:.0%}（{metrics['dialogue_seconds']:.1f}s / "
                f"{metrics['total_seconds']:.1f}s），建议不超过 "
                f"{VIEWABILITY_DIALOGUE_SHARE_ADVISORY:.0%}："
                "请按叙事目的复核是否有可交给画面的信息；有意义的对白应保留。"
            ),
            "dialogue",
            {"dialogueSecondsShare": duration_share, "dialogueShotShare": count_share},
        )

    # ② 景别。既要跨档，也不能整片都是胸像。只提醒，不挡生成。
    families = metrics["framing_families"]
    if families and len(families) < VIEWABILITY_FRAMING_FAMILIES_MIN:
        flag(
            "script.viewability.framing_mix.v1",
            SEVERITY_ADVISORY,
            (
                f"景别只用了 {'、'.join(families)}（{len(families)} 档），"
                "请按观看目的复核空间与信息是否清楚；有理由的同景别可以保留"
            ),
            "shot",
            {"framingFamilies": families},
        )
    portrait_share = metrics["portrait_share"]
    if portrait_share > VIEWABILITY_PORTRAIT_SHARE_ADVISORY:
        flag(
            "script.viewability.framing_mix.v1",
            SEVERITY_ADVISORY,
            (
                f"贴身景别（中景/近景/特写）占 {portrait_share:.0%}"
                f"（{metrics['portrait_shot_count']}/{metrics['shot_count']} 镜），"
                "请复核人物关系与空间是否已交代；占比不决定拍法，不强制补远景"
            ),
            "shot",
            {"portraitShare": portrait_share},
        )

    # ③ 静戏最长段。对峙是戏，堆到几十秒就只是把同一件事说很多遍。只提醒，不挡生成。
    standoff = metrics["longest_standoff_seconds"]
    if standoff > VIEWABILITY_STANDOFF_SECONDS_ADVISORY:
        flag(
            "script.viewability.static_standoff.v1",
            SEVERITY_ADVISORY,
            (
                f"连续 {standoff:.1f} 秒是「有台词、无动作」"
                f"（建议不超过 {VIEWABILITY_STANDOFF_SECONDS_ADVISORY:.0f} 秒）："
                "动作词检测不等于表演判断；请复核对白、视线、关系变化与停留是否有意义，不强制插入动作"
            ),
            "character_action",
            {"standoffSeconds": standoff},
        )

    # ④ 动作占比。低到某个程度，打戏就只是台词之间的过场。只提醒，不挡生成。
    action_share = metrics["action_shot_share"]
    if action_focused and action_share < VIEWABILITY_ACTION_SHARE_ADVISORY:
        flag(
            "script.viewability.action_share.v1",
            SEVERITY_ADVISORY,
            (
                f"动作镜只占 {action_share:.0%}"
                f"（{metrics['action_shot_count']}/{metrics['shot_count']} 镜），"
                f"建议不低于 {VIEWABILITY_ACTION_SHARE_ADVISORY:.0%}："
                "全片几乎没有物理事件。动作不必是套招——"
                "刀锋、火星、脚步、眼睛、兵器脱手、落点，每个都能独立成镜"
            ),
            "character_action",
            {"actionShotShare": action_share},
        )

    # ⑤ 节奏：最长单镜与呼吸镜。
    longest = metrics["longest_shot_seconds"]
    if longest > VIEWABILITY_MAX_SHOT_SECONDS_ADVISORY:
        flag(
            "script.viewability.pacing.v1",
            SEVERITY_ADVISORY,
            (
                f"最长单镜 {longest:.1f} 秒，超过 {VIEWABILITY_MAX_SHOT_SECONDS_ADVISORY:.0f} 秒。"
                "请按完整表演与观看目的复核时长；长镜可以承担行动、对白或观察，不强制快切对比"
            ),
            "duration",
            {"longestShotSeconds": longest},
        )
    breath = metrics["breath_shot_count"]
    if breath < VIEWABILITY_BREATH_SHOTS_MIN:
        flag(
            "script.viewability.pacing.v1",
            SEVERITY_ADVISORY,
            (
                f"纯画面镜（无台词）只有 {breath} 个；请复核声音与画面是否重复解释，"
                "保留必要对白，不要求固定无台词镜数"
            ),
            "dialogue",
            {"breathShotCount": breath},
        )


# --------------------------------------------------------------------------- #
# 修复
# --------------------------------------------------------------------------- #


def repair_script_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    camera_names: Sequence[str] | None = None,
    director_plan: Mapping[str, Any] | None = None,
    target_duration_seconds: float | None = None,
) -> ScriptContractReport:
    """先校验、再就地修掉**机械可修**的差异，最后用修复后的表重跑校验。

    机械修复不需要创作决定：
      1. 角色卡与本篇首次出现不一致  → 换回首次出现的那张卡（整段，含方括号）；
      2. 第 7 段（视觉风格）逐镜改写  → 全篇统一到首行那一份；
      3. 运动稿写的时长与时长列不一致 → 运动稿向时长列看齐。
      4. 完全重复的关键帧职责/状态/用途 → 保留首次项，合并 required，并记录拒绝原因。

    修不了的一律只报不改：段数不足、段序错乱、一镜多运镜、参考图超配——这些要么需要
    补写内容，要么需要人来决定取舍，机器改只会把问题藏起来。
    """

    table = [dict(row) for row in rows]
    if not table:
        return ScriptContractReport(rows=table, rules_checked=list(RULE_CATALOG))

    fixed_issues: list[ScriptIssue] = []

    # 第一遍：以首次出现为基准，记录要统一的段。
    parsed = [_parse_row(row, index) for index, row in enumerate(table)]
    card_canonical: dict[str, str] = {}
    style_canonical: str | None = None
    for item in parsed:
        roles = item["shot_roles"]
        if "character_card" in roles:
            segment = item["shot_segments"][roles.index("character_card")]
            for name, block in card_blocks(segment):
                card_canonical.setdefault(name, block)
        if style_canonical is None and "style" in roles:
            style_canonical = item["shot_segments"][roles.index("style")]

    # 第二遍：按基准改写每一行。
    for item in parsed:
        index = item["index"]
        table_row = table[index]
        roles = item["shot_roles"]
        segments = list(item["shot_segments"])
        if not segments:
            continue
        changed = False

        if "character_card" in roles:
            position = roles.index("character_card")
            current = segments[position]
            rebuilt = current
            replaced_any = False
            for name, block in card_blocks(current):
                canonical_block = card_canonical.get(name)
                if not canonical_block or canonical_block == block:
                    continue
                # **只换这一个角色块**：同一段里的其它角色卡原样保留。
                rebuilt = rebuilt.replace(block, canonical_block, 1)
                replaced_any = True
                fixed_issues.append(
                    ScriptIssue(
                        rule_id="script.character_card.verbatim.v1",
                        severity=SEVERITY_BLOCKING,
                        message=f"角色「{name}」的角色卡已按本篇首次出现改回逐字一致",
                        row_index=index,
                        shot_no=item["shot_no"],
                        field="character_description_1",
                        fixed=True,
                        detail={"character": name},
                    )
                )
            if replaced_any:
                segments[position] = rebuilt
                changed = True

        for role, canonical, rule_id, field_name in (
            ("style", style_canonical, "script.style.singleton.v1", "shot_prompt"),
        ):
            if canonical is None or role not in roles:
                continue
            position = roles.index(role)
            if segments[position] == canonical:
                continue
            label = SHOT_SEGMENT_LABELS_ZH[role]
            fixed_issues.append(
                ScriptIssue(
                    rule_id=rule_id,
                    severity=SEVERITY_BLOCKING,
                    message=f"{label}已统一到首行那一份",
                    row_index=index,
                    shot_no=item["shot_no"],
                    field=field_name,
                    fixed=True,
                )
            )
            segments[position] = canonical
            changed = True

        if changed:
            table_row["shot_prompt"] = " + ".join(f"[{segment}]" for segment in segments)

        # 运动稿时长向时长列看齐。
        motion_segments = list(item["motion_segments"])
        motion_roles = item["motion_roles"]
        if "duration" in motion_roles and motion_segments:
            position = motion_roles.index("duration")
            declared = _coerce_seconds(table_row.get("duration"))
            stated = _coerce_seconds(motion_segments[position])
            if declared is not None and stated is not None and abs(declared - stated) >= 1e-6:
                motion_segments[position] = _rewrite_duration_segment(
                    motion_segments[position], declared
                )
                table_row["video_motion_prompt"] = " + ".join(
                    f"[{segment}]" for segment in motion_segments
                )
                fixed_issues.append(
                    ScriptIssue(
                        rule_id="script.motion.duration_match.v1",
                        severity=SEVERITY_ADVISORY,
                        message=f"运动稿时长已改为与时长列一致（{declared:g}s）",
                        row_index=index,
                        shot_no=item["shot_no"],
                        field="duration",
                        fixed=True,
                        detail={"stated": stated, "declared": declared},
                    )
                )

    for index, row in enumerate(table):
        duplicates = _keyframe_duplicate_issues(row, index)
        if duplicates:
            plan = list(row["keyframe_plan"])
            for issue in duplicates:
                duplicate_index = issue.detail["keyframe_index"]
                previous = issue.detail.get("duplicate_of")
                if previous is not None and plan[duplicate_index].get("required") is True:
                    plan[previous] = {**plan[previous], "required": True}
                issue.fixed = True
            rejected = {issue.detail["keyframe_index"] for issue in duplicates}
            row["keyframe_plan"] = [item for position, item in enumerate(plan) if position not in rejected]
            fixed_issues.extend(duplicates)
        _inject_character_states(row)
    # 修复后重跑，剩下的是真正需要人处理的问题。
    final = validate_script_rows(table, camera_names=camera_names, director_plan=director_plan, target_duration_seconds=target_duration_seconds)
    final.issues = fixed_issues + final.issues
    return final


def _rewrite_duration_segment(segment: str, seconds: float) -> str:
    """把 `时长：4.0s` 里的数字换成新的，**保留原来的单位写法**（`s` / `秒` / 无单位）。"""

    rendered = f"{seconds:g}"
    match = _DURATION_SEGMENT_RE.search(segment)
    if not match:
        return f"{segment}（{rendered}s）"
    unit = match.group(2) or ""
    replaced = segment[: match.start()] + rendered + unit + segment[match.end() :]
    return replaced


def enforce_story_script_contract(
    data: dict[str, Any], *, target_index: int | None = None,
    target_indices: Sequence[int] | None = None,
) -> dict[str, Any]:
    """把一份脚本结果过一遍合同：能机械修的就修，剩下的照实报出来。

    这一步是「生成完就算成功」与「生成完是可用的」之间的那道分界。角色卡逐字一致、
    视觉风格一致这些要求不能只写在系统提示词里；模型一旦违反，
    下游拿到的是会逐镜漂移的表，而漂移要到出图之后才被看见。

    就地更新 `data["rows"]`（落盘与返回给前端的都是这一份），返回合同报告。
    """

    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        return {}
    table = [dict(row) for row in rows if isinstance(row, dict)]
    if target_index is not None and target_indices is not None:
        raise ValueError("use target_index or target_indices, not both")
    scope = {target_index} if target_index is not None else set(target_indices) if target_indices is not None else None
    if scope is not None and any(isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(table) for index in scope):
        raise ValueError("target_index out of range")
    director_plan = data.get("director_plan")
    director_plan = director_plan if isinstance(director_plan, Mapping) else None
    report = repair_script_rows(table, director_plan=director_plan)
    if scope is not None:
        for index in scope:
            table[index] = report.rows[index]
        fixed = [issue for issue in report.issues if issue.fixed and issue.row_index in scope]
        report = validate_script_rows(table, director_plan=director_plan)
        report.issues.extend(fixed)
    data["rows"] = report.rows
    return report.as_dict()


def script_media_action_gate(
    rows: Iterable[Mapping[str, Any]],
    *,
    action: str,
    director_plan: Mapping[str, Any] | None = None,
    target_duration_seconds: float | None = None,
) -> dict[str, Any]:
    """重算一份脚本表能否进入付费媒体动作。

    前端门禁读的是已经落盘的报告和画布派生状态；Agent / 兼容工作流写入
    `canvas_auto_generate_once=true` 时，服务端必须按**当前行**再算一次，不能相信旧报告。
    这里只做合同层能确定的事实：合同硬阻塞与缺提示词。逐镜 stale 与资产版本属于画布
    派生状态，由前端和 T-058 的 Workflow 门禁分别处理。
    """

    if action not in {
        SCRIPT_MEDIA_ACTION_STORYBOARD_IMAGES,
        SCRIPT_MEDIA_ACTION_SHOT_VIDEOS,
    }:
        raise ValueError(f"unsupported script media action: {action}")

    table = [dict(row) for row in rows]
    if not table:
        return {
            "allowed": False,
            "action": action,
            "reason_code": "script_media_empty",
            "reason": "脚本表里还没有分镜行，不能提交付费媒体。",
            "blocking_count": 0,
            "missing_prompt_count": 0,
        }

    report = validate_script_rows(table, director_plan=director_plan, target_duration_seconds=target_duration_seconds)
    blocking = [
        issue.as_dict()
        for issue in report.blocking
        if issue.rule_id not in CANVAS_NON_BLOCKING_SCRIPT_RULES
    ]
    canvas_advisories = [
        issue.as_dict()
        for issue in report.issues
        if issue.rule_id in CANVAS_NON_BLOCKING_SCRIPT_RULES
    ]
    if action == SCRIPT_MEDIA_ACTION_STORYBOARD_IMAGES:
        missing_prompt = [
            index
            for index, row in enumerate(table, start=1)
            if not (_row_text(row, "shot_prompt") or _row_text(row, "visual_description"))
        ]
    else:
        missing_prompt = [
            index
            for index, row in enumerate(table, start=1)
            if not (
                _row_text(row, "video_motion_prompt")
                or _row_text(row, "shot_prompt")
                or _row_text(row, "visual_description")
            )
        ]

    if blocking:
        reason_code = "script_media_contract_blocking"
        reason = f"脚本合同仍有 {len(blocking)} 项硬阻塞，先修正后再提交付费媒体。"
    elif missing_prompt:
        reason_code = "script_media_prompt_missing"
        label = "图片提示词" if action == SCRIPT_MEDIA_ACTION_STORYBOARD_IMAGES else "视频提示词"
        reason = (
            f"第 {', '.join(str(index) for index in missing_prompt[:8])} 行缺少可用{label}，"
            "不能提交付费媒体。"
        )
    else:
        reason_code = ""
        reason = ""

    return {
        "allowed": not blocking and not missing_prompt,
        "action": action,
        "reason_code": reason_code,
        "reason": reason,
        "blocking_count": len(blocking),
        "missing_prompt_count": len(missing_prompt),
        "blocking_issues": blocking,
        "advisory_count": len(canvas_advisories),
        "advisory_issues": canvas_advisories,
        "missing_prompt_rows": missing_prompt,
        "rows_fingerprint": report.rows_fingerprint,
    }


__all__ = [
    "ACTION_KEYWORDS",
    "CANVAS_NON_BLOCKING_SCRIPT_RULES",
    "FRAMING_FAMILY_KEYWORDS",
    "FRAMING_FAMILY_ORDER",
    "MOTION_PROMPT_SEGMENT_COUNT",
    "MOTION_SEGMENT_LABELS_ZH",
    "MOTION_SEGMENT_ORDER",
    "NO_DIALOGUE_VALUES",
    "PORTRAIT_FRAMING_FAMILIES",
    "RULE_CATALOG",
    "SCRIPT_CONTRACT_SCHEMA",
    "SCRIPT_MEDIA_ACTION_SHOT_VIDEOS",
    "SCRIPT_MEDIA_ACTION_STORYBOARD_IMAGES",
    "SCRIPT_REFERENCE_IMAGE_CAP",
    "SHOT_PROMPT_SEGMENT_COUNT",
    "SHOT_SEGMENT_LABELS_ZH",
    "SHOT_SEGMENT_ORDER",
    "SEVERITY_ADVISORY",
    "SEVERITY_BLOCKING",
    "VIEWABILITY_MIN_SHOTS",
    "ScriptContractReport",
    "ScriptIssue",
    "card_blocks",
    "character_ids_in_card",
    "classify_segments",
    "enforce_story_script_contract",
    "framing_family",
    "framing_family_distance",
    "has_dialogue",
    "is_action_row",
    "repair_script_rows",
    "script_media_action_gate",
    "script_row_fingerprint",
    "script_row_key",
    "script_rows_fingerprint",
    "split_prompt_segments",
    "validate_script_rows",
    "viewability_metrics",
]
