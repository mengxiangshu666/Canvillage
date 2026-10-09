"""Plan bounded, contract-driven story-script repairs.

The current contract report selects bounded rewrite scopes. Authored sequences and
reported continuity dependencies keep a causal group in one model call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from novelvideo.freezone.script_video_duration import SCRIPT_CONTENT_DURATION_GUIDANCE
from novelvideo.freezone.script_contract import (
    PORTRAIT_FRAMING_FAMILIES,
    framing_family,
    has_dialogue,
    is_action_row,
)

MAX_REPAIR_TARGETS = 10
MAX_TARGETS_PER_GLOBAL_ISSUE = 4
# 全片级问题（景别单一、台词过密、静戏过长……）是统计结论，一次点击只该在少数几镜上
# 推动它，而不是把同一条要求复制进每一条指令。上限既控制单次改动量，也控制模型调用量。
MAX_GLOBAL_REPAIR_TARGETS = 4

# These rules are either already fixed deterministically by ``repair_script_rows`` or
# cannot be changed through the row-rewrite contract. Sending them to a text model
# would spend a call without changing the fields that the rule actually measures.
NON_MODEL_REPAIR_RULES = frozenset(
    {
        "script.character_card.verbatim.v1",
        "script.style.singleton.v1",
        "script.technical.singleton.v1",
        "script.motion.duration_match.v1",
        "script.reference.budget.v1",
        "script.shot_no.sequence.v1",
        "script.duration.total_budget.v1",
        "script.plan.sequence_membership.v1",
        "script.keyframe.duplicate_plan.v1",
    }
)

#: 全片级问题的处理顺序：先改「整片看起来都一样」的根因（景别、台词密度），再处理
#: 由它们派生出来的静戏、动作与节奏读数。集合与顺序同源，避免两处清单漂移。
GLOBAL_VIEWABILITY_RULE_ORDER = (
    "script.viewability.framing_mix.v1",
    "script.viewability.dialogue_ratio.v1",
    "script.viewability.static_standoff.v1",
    "script.viewability.action_share.v1",
    "script.viewability.pacing.v1",
)

GLOBAL_VIEWABILITY_RULES = frozenset(GLOBAL_VIEWABILITY_RULE_ORDER)

_DURATION_RE = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass(frozen=True)
class ScriptRepairTarget:
    """One rewrite scope that can address one or more contract issues."""

    row_index: int
    rule_ids: tuple[str, ...]
    instruction: str
    row_indices: tuple[int, ...] = ()
    sequence_ids: tuple[str, ...] = ()


def is_model_repairable_issue(issue: Mapping[str, Any]) -> bool:
    """Return whether the row-rewrite contract can materially repair an issue."""

    if bool(issue.get("fixed")):
        return False
    rule_id = str(issue.get("rule_id") or "").strip()
    if issue.get("severity") == "advisory" and rule_id in GLOBAL_VIEWABILITY_RULES:
        return False
    return bool(rule_id) and rule_id not in NON_MODEL_REPAIR_RULES


def plan_script_contract_repairs(
    rows: Sequence[Mapping[str, Any]],
    issues: Sequence[Mapping[str, Any]],
    *,
    max_targets: int = MAX_REPAIR_TARGETS,
    attempted: set[tuple[int, str]] | None = None,
    director_plan: Mapping[str, Any] | None = None,
) -> list[ScriptRepairTarget]:
    """Resolve contract issues into a bounded set of rows to rewrite.

    Row-level issues go directly to their row. Full-table viewability issues are
    translated into representative rows: portrait-heavy rows for framing, long
    dialogue rows for dialogue density, and so on.

    Full-table issues are deliberately **not** copied onto every representative
    row: each one claims at most one row, the rows it may claim are distinct from
    the rows already carrying a row-level blocker, and the whole category is
    capped by ``MAX_GLOBAL_REPAIR_TARGETS``. Otherwise one click would hand a
    single shot a five-issue "fix the entire film" instruction, and rows that
    actually violate a row-level rule would lose the target budget to rows that
    are statistically representative but individually valid.

    The output stays capped so one click remains an incremental repair rather
    than a hidden full regeneration.
    """

    table = [dict(row) for row in rows]
    if not table:
        return []

    row_level: dict[int, list[Mapping[str, Any]]] = {}
    global_by_rule: dict[str, Mapping[str, Any]] = {}
    for issue in issues:
        if not isinstance(issue, Mapping) or not is_model_repairable_issue(issue):
            continue
        rule_id = str(issue.get("rule_id") or "").strip()
        if rule_id in GLOBAL_VIEWABILITY_RULES:
            # 同一规则在报告里通常只有一条；去重后一条规则只推动一镜。
            global_by_rule.setdefault(rule_id, issue)
            continue
        row_index = _resolve_issue_row_index(table, issue)
        if row_index is not None and (row_index, rule_id) not in (attempted or set()):
            row_level.setdefault(row_index, []).append(issue)

    blocking_row_indices = {
        row_index
        for row_index, row_issues in row_level.items()
        if any(str(issue.get("severity") or "") == "blocking" for issue in row_issues)
    }

    global_targets: dict[int, list[Mapping[str, Any]]] = {}
    for rule_id in _ordered_global_rules(global_by_rule):
        if len(global_targets) >= MAX_GLOBAL_REPAIR_TARGETS:
            break
        for target_index in _select_global_issue_rows(table, rule_id):
            if (target_index, rule_id) in (attempted or set()):
                continue
            if target_index in global_targets or target_index in blocking_row_indices:
                continue
            global_targets[target_index] = [global_by_rule[rule_id]]
            break

    grouped: dict[int, list[Mapping[str, Any]]] = {
        row_index: list(row_issues) for row_index, row_issues in row_level.items()
    }
    for row_index, row_issues in global_targets.items():
        grouped.setdefault(row_index, []).extend(row_issues)

    if not grouped:
        return []

    limit = max(1, min(int(max_targets), MAX_REPAIR_TARGETS))
    ranked = sorted(
        grouped.items(),
        key=lambda item: (
            0
            if item[0] in blocking_row_indices
            else 1
            if item[0] in global_targets
            else 2,
            -len(item[1]),
            item[0],
        ),
    )

    if director_plan and director_plan.get("sequences"):
        return _group_sequence_repairs(table, ranked, issues, director_plan)[:limit]

    return [
        ScriptRepairTarget(
            row_index=row_index,
            rule_ids=tuple(
                dict.fromkeys(
                    str(issue.get("rule_id") or "").strip()
                    for issue in row_issues
                    if str(issue.get("rule_id") or "").strip()
                )
            ),
            instruction=build_script_repair_instruction(row_issues),
        )
        for row_index, row_issues in ranked[:limit]
    ]


def _group_sequence_repairs(
    rows: Sequence[Mapping[str, Any]],
    ranked: Sequence[tuple[int, list[Mapping[str, Any]]]],
    issues: Sequence[Mapping[str, Any]],
    director_plan: Mapping[str, Any],
) -> list[ScriptRepairTarget]:
    from novelvideo.freezone.sequence_rewrite import resolve_sequence_indices
    from novelvideo.ports.story_script import FreezoneStoryDirectorPlan

    plan = FreezoneStoryDirectorPlan.model_validate(director_plan)
    scopes = {
        sequence.sequence_id: set(resolve_sequence_indices(rows, plan, sequence.sequence_id))
        for sequence in plan.sequences
    }
    groups: list[tuple[set[int], set[str], list[Mapping[str, Any]]]] = []
    for row_index, row_issues in ranked:
        dependencies = {row_index}
        for issue in issues:
            if not isinstance(issue, Mapping) or not str(issue.get("rule_id") or "").startswith("script.continuity."):
                continue
            detail = issue.get("detail")
            if not isinstance(detail, Mapping):
                continue
            previous = detail.get("previous_row_index")
            current = _resolve_issue_row_index(rows, issue)
            if issue.get("rule_id") == "script.continuity.missing_states.v1" and issue.get("field") == "end_state" and previous == current and current is not None and current + 1 < len(rows):
                current += 1
            if isinstance(previous, int) and not isinstance(previous, bool) and 0 <= previous < len(rows) and current is not None:
                if row_index in {previous, current}:
                    dependencies.update((previous, current))
        selected = {identity for identity, members in scopes.items() if members & dependencies}
        members = set().union(*(scopes[identity] for identity in selected)) if selected else {row_index}
        # Overlapping authored sequences form one atomic rewrite; never rewrite shared shots twice.
        while selected:
            expanded = {identity for identity, indices in scopes.items() if indices & members}
            if expanded == selected:
                break
            selected = expanded
            members.update(set().union(*(scopes[identity] for identity in selected)))
        merged_issues = list(row_issues)
        overlapping = [group for group in groups if members & group[0]]
        while overlapping:
            for group in overlapping:
                members.update(group[0])
                selected.update(group[1])
                merged_issues.extend(group[2])
                groups.remove(group)
            overlapping = [group for group in groups if members & group[0]]
        groups.append((members, selected, merged_issues))
    return [
        ScriptRepairTarget(
            row_index=min(indices), row_indices=tuple(sorted(indices)),
            sequence_ids=tuple(sequence.sequence_id for sequence in plan.sequences if sequence.sequence_id in identities),
            rule_ids=tuple(dict.fromkeys(str(issue["rule_id"]) for issue in group_issues)),
            instruction=build_script_repair_instruction(group_issues, sequence_scope=bool(identities)),
        )
        for indices, identities, group_issues in groups
    ]


def _ordered_global_rules(global_by_rule: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Return the reported global rules in the declared handling order."""

    ordered = [rule_id for rule_id in GLOBAL_VIEWABILITY_RULE_ORDER if rule_id in global_by_rule]
    ordered.extend(sorted(set(global_by_rule) - set(ordered)))
    return ordered


def build_script_repair_instruction(
    issues: Sequence[Mapping[str, Any]],
    *, sequence_scope: bool = False,
) -> str:
    """Compile contract issues into one bounded, row-local rewrite instruction."""

    lines = [
        (
            "这是系统根据当前脚本检查生成的一键优化任务。先诊断目标段落的触发、人物选择、"
            "行动结果、观众新增信息、空间与状态交接，再联合修改同一因果链的受影响镜头和段落计划。"
            "诊断说明写入diagnosis，以当前镜头与故事为依据；有意省略、重复和停留可以成立。"
            "保持镜头身份、数量、片序、角色卡、共同视觉风格及段外镜头，不强制无关字段变化。"
            if sequence_scope else
            "这是系统根据合同检查生成的一键优化任务。只改当前这一镜，不改其它镜头，"
            "也不改变角色卡和共同视觉风格。"
        ),
        "本镜技术参数不是冻结项：焦段、光圈与景深按观看目的及需要修复的问题决定，"
        "与本镜景别、机位、空间关系一致；没有改动理由时保留，不照抄第一镜。",
        "先结合用户原意和 content_intent 判断本场目的；展示、对白、抒情、纪实和教程"
        "各有合理节奏。不要为清除提醒添加打斗、危机，或删掉必要讲解；不确定时保留原意。",
        "先对照导演规划的段落目的、空间调度、表演推进与节奏曲线。统计提醒只是观察线索，"
        "不是必须达到的比例或拍法；已有选择有表达理由时保留，超出授权范围的问题不要硬改。",
        SCRIPT_CONTENT_DURATION_GUIDANCE,
        "请把问题落到对应的画面设计或表演安排，只修改有证据关联及受影响的字段。"
        "有依赖变化时同步动作、构图、时间或台词；没有关联的导演选择保留，"
        "不强制所有字段一起调整，也不用无意义换词假装修复。",
        "视频运动提示词按 MCSLA 组织：摄影机（景别/机位/主要轨迹与有理由的变化）→主体→风格→连续表演；"
        "相邻镜头按观看目的和切镜理由选择景别、机位与构图；同景别正反打、换主体、"
        "连续动作和有意省略均可成立，不强制跨两档景别。",
        "目标范围需要核对的问题：" if sequence_scope else "本镜需要解决的问题：",
    ]
    for issue in issues:
        rule_id = str(issue.get("rule_id") or "").strip()
        message = str(issue.get("message") or "").strip()
        field = str(issue.get("field") or "").strip()
        label = f"{rule_id}（字段 {field}）" if field else rule_id
        lines.append(f"- 镜号 {issue.get('shot_no') or '全片'} {label}：{message}")
        guidance = _RULE_GUIDANCE.get(rule_id)
        if sequence_scope and rule_id in {"script.continuity.missing_states.v1", "script.continuity.repeated_visual_event.v1"}:
            guidance = (
                "结合段落因果与前后镜，联合核对动作落点、反应、起止状态、观看目的与切镜理由；"
                "只补确实缺失的交接，保留成立的匹配、回放、不同视点和有意停留，不凭空增加事件。"
                "段外镜为冻结边界，不能靠修改段外内容清除问题；不能解决时在诊断里说明原因。"
            )
        if guidance:
            lines.append(f"  处理要求：{guidance}")
        if rule_id == "script.assets.definition_consistency.v1":
            detail = issue.get("detail")
            if isinstance(detail, Mapping):
                lines.append(f"  同名资产此前的基准（上下文，不代表自动批准）：{str(detail.get('baseline') or '')[:1600]}")

    if any(str(issue.get("rule_id") or "") in GLOBAL_VIEWABILITY_RULES for issue in issues):
        lines.append(
            "这些整片统计不能单独证明本镜有错。结合前后镜和导演规划核对信息是否重复、"
            "空间是否清楚、表演时间是否足够；有必要才调整，不能为了改变读数制造差异。"
        )
    return "\n".join(line for line in lines if line)


_RULE_GUIDANCE: dict[str, str] = {
    "script.assets.definition_consistency.v1": (
        "结合用户原意与导演规划核对同名资产。只是改写措辞时，本镜复用已有基准说明；"
        "临时开合、握持、磨损或破损变化应放入状态字段，同步本镜图像和运动提示词。"
        "保留用户明确的新设计，不能为了消除提醒擅自改回首次说明；涉及全片资产改设定时"
        "不要假装只改当前一镜就已解决，更不能将设计字段清空或改名来躲避检查。"
    ),
    "script.continuity.missing_states.v1": (
        "只补本镜缺少的 start_state 或 end_state，写明切点可见的姿态、位置、运动方向与关键道具状态；"
        "结合前后镜已有状态保持动作因果，不修改邻镜、不复制整段剧情、不凭空增加动作。"
        "保留既有状态与导演选择；若上下文不足以确定交接，不要假装已经解决。"
    ),
    "script.planning.framing_run.v1": (
        "先检查连续同景别是否在表达有效的信息或表演变化；有理由的保持和正反打应保留。"
        "仅在信息无意义重复或空间不清楚时调整构图、视点或景别，并同步提示词。"
    ),
    "script.planning.opening_dialogue.v1": (
        "结合本场目的检查开场信息是否清楚；产品细节、人物反应与有意义的静态画面"
        "也能成立。保留必要对白，不凭空加入危机或战斗，不增加总时长。"
    ),
    "script.continuity.screen_direction.v1": (
        "保留动作因果；如果不是有意切轴，请统一主体的屏幕方向，"
        "或明确加入转身、绕轴、反打等能解释方向变化的动作。"
    ),
    "script.shot_prompt.segments.v1": "补回缺失提示词段，保持固定 8 段结构。",
    "script.shot_prompt.order.v1": "按合同固定顺序重排 8 段提示词。",
    "script.motion.segments.v1": "补回缺失运动稿段，保持固定 6 段结构。",
    "script.camera.single.v1": "检查轨迹是否可执行；先后发生或协调的复合运镜可以保留，明确时间与目的。仅修正互相矛盾的同时指令，不因出现多个运镜词而强制切镜。",
    "script.camera.contradiction.v1": (
        "先区分固定构图与固定世界机位，固定景别的跟拍可以成立。"
        "核对全程跟随与停止是否在说同一时间：主体停住后镜头随之停住可以保留；"
        "分阶段移动与停留应写清阶段和触发。若确实矛盾，按本镜观看目的选择并同步摄影正文，"
        "不擅自改变剧情、强制拆镜或把跟拍改成静止。"
    ),
    "script.viewability.dialogue_ratio.v1": (
        "复核是否有重复解释可交给画面；理解与表演所需的台词保留，不为降低台词占比删对白。"
    ),
    "script.viewability.framing_mix.v1": (
        "先核对观众是否看清空间、人物关系与关键细节；景别少或贴身镜多不等于错误。"
        "只有信息缺失或无意义重复时改变视点、构图或景别，不设档数、占比或相邻跨档要求。"
    ),
    "script.viewability.static_standoff.v1": (
        "检查对白、视线、判断、关系变化与停留是否有表达价值；安静表演可以成立。"
        "只修正无意义重复或无法读懂的表演，不强加物理事件。"
    ),
    "script.viewability.action_share.v1": (
        "仅动作戏加入与原剧情相符、可拍到的动作节拍，"
        "不要用情绪词代替动作。"
    ),
    "script.viewability.pacing.v1": (
        "按动作、完整对白、反应和观众理解所需时间核对本镜；长镜、短镜与匀速都可以成立。"
        "只调整不够完成表演或无意义拖延的时间，不强制快慢对比；同步 duration_reason 与运动稿。"
    ),
    "script.continuity.adjacent_framing.v1": (
        "先核对观看目的和切镜理由；正反打、动作匹配、换主体和有意保持可以同景别。"
        "仅在信息无意义重复时调整机位、构图或景别，不强制跨两档；把选择写进 cut_reason。"
    ),
    "script.continuity.repeated_visual_event.v1": (
        "先核对重复是否用于动作匹配、回放、不同视点、喜剧重复或有意停留；"
        "相同动作描述不能单独证明重复无效，有理由的重复应保留。"
        "仅无意义重复时调整本镜观看重点、信息或动作落点，不凭空增加道具变化或新事件；"
        "将重复或调整的目的写入 shot_purpose 与 cut_reason，单镜无法解决时说明限制。"
    ),
}


def _resolve_issue_row_index(
    table: Sequence[Mapping[str, Any]],
    issue: Mapping[str, Any],
) -> int | None:
    try:
        row_index = int(issue.get("row_index", -1))
    except (TypeError, ValueError):
        row_index = -1
    if 0 <= row_index < len(table):
        return row_index

    shot_no = str(issue.get("shot_no") or "").strip()
    if not shot_no:
        return None
    for index, row in enumerate(table):
        display = str(row.get("display_shot_no") or "").strip()
        canonical = str(row.get("shot_no") or "").strip()
        if shot_no in {display, canonical}:
            return index
    return None


def _select_global_issue_rows(
    table: Sequence[Mapping[str, Any]],
    rule_id: str,
) -> list[int]:
    if rule_id == "script.viewability.framing_mix.v1":
        portrait = [
            index
            for index, row in enumerate(table)
            if framing_family(row.get("shot")) in PORTRAIT_FRAMING_FAMILIES
        ]
        if portrait:
            return _spread_indices(
                portrait,
                min(MAX_TARGETS_PER_GLOBAL_ISSUE, max(1, len(portrait) // 2)),
            )
        return _spread_indices(list(range(len(table))), 3)

    if rule_id == "script.viewability.dialogue_ratio.v1":
        dialogue = [index for index, row in enumerate(table) if has_dialogue(row)]
        return _spread_indices(
            sorted(dialogue, key=lambda index: _seconds(table[index]), reverse=True),
            MAX_TARGETS_PER_GLOBAL_ISSUE,
        )

    if rule_id == "script.viewability.static_standoff.v1":
        run = _longest_standoff_run(table)
        return _spread_indices(run, MAX_TARGETS_PER_GLOBAL_ISSUE)

    if rule_id == "script.viewability.action_share.v1":
        candidates = [
            index for index, row in enumerate(table) if not is_action_row(row)
        ]
        return _spread_indices(
            sorted(candidates, key=lambda index: _seconds(table[index]), reverse=True),
            MAX_TARGETS_PER_GLOBAL_ISSUE,
        )

    if rule_id == "script.viewability.pacing.v1":
        longest = sorted(
            range(len(table)),
            key=lambda index: _seconds(table[index]),
            reverse=True,
        )[:2]
        breathing = [
            index
            for index, row in enumerate(table)
            if has_dialogue(row) and not is_action_row(row)
        ]
        ranked = [*longest, *sorted(breathing, key=lambda index: _seconds(table[index]), reverse=True)]
        return list(dict.fromkeys(ranked))[:MAX_TARGETS_PER_GLOBAL_ISSUE]

    return _spread_indices(list(range(len(table))), 3)


def _longest_standoff_run(table: Sequence[Mapping[str, Any]]) -> list[int]:
    best: list[int] = []
    current: list[int] = []
    for index, row in enumerate(table):
        if has_dialogue(row) and not is_action_row(row):
            current.append(index)
            if sum(_seconds(table[item]) for item in current) > sum(
                _seconds(table[item]) for item in best
            ):
                best = list(current)
            continue
        current = []
    return best


def _spread_indices(indices: Sequence[int], count: int) -> list[int]:
    values = list(dict.fromkeys(int(index) for index in indices))
    if not values or count <= 0:
        return []
    if len(values) <= count:
        return values
    if count == 1:
        return [values[len(values) // 2]]
    return list(
        dict.fromkeys(
            values[round(position * (len(values) - 1) / (count - 1))]
            for position in range(count)
        )
    )


def _seconds(row: Mapping[str, Any]) -> float:
    value = row.get("duration")
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    match = _DURATION_RE.search(str(value or ""))
    return max(0.0, float(match.group(0))) if match else 0.0


__all__ = [
    "GLOBAL_VIEWABILITY_RULE_ORDER",
    "GLOBAL_VIEWABILITY_RULES",
    "MAX_GLOBAL_REPAIR_TARGETS",
    "MAX_REPAIR_TARGETS",
    "MAX_TARGETS_PER_GLOBAL_ISSUE",
    "NON_MODEL_REPAIR_RULES",
    "ScriptRepairTarget",
    "build_script_repair_instruction",
    "is_model_repairable_issue",
    "plan_script_contract_repairs",
]
