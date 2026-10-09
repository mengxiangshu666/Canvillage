"""Taste engines — the active checks that turn the taste KB into a verdict.

The KB in :mod:`taste_kb` is data: what is banned, what the quotas are, what a
good line looks like next to a bad one.  This module is the part that *runs*.
Each engine takes a shot plan (the hand-authored JSON a production is built
from) and returns findings.  Findings are evidence, not opinions: every one
carries the offending text and a concrete replacement direction.

Two severities only:

* ``hard`` — the plan may not go to paid generation;
* ``soft`` — shippable, but recorded so it can be weighed.

Pure functions, no I/O, no subprocesses.  The CLI in ``scripts/taste_audit.py``
wires them to files and to the rotation ledger.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from novelvideo.production.taste_kb import (
    BANNED_CLICHES,
    ENGINE_SPECS,
    PAIRED_SAMPLES,
    QUOTAS,
)

TASTE_AUDIT_SCHEMA = "taste_audit.v1"


# --------------------------------------------------------------------------- #
# word lists
# --------------------------------------------------------------------------- #

#: Words a model can only guess at.  Each one has to become a number or a
#: visible thing before the line is executable.
VAGUE_ADJECTIVES: tuple[str, ...] = (
    "美丽", "漂亮", "绝美", "惊艳", "震撼", "壮丽", "宏大", "史诗", "大气",
    "高级", "高级感", "唯美", "优雅", "精致", "完美", "深邃", "灵魂",
    "有力", "张力", "冲击力", "感染力", "氛围感", "电影感", "质感",
    "情绪饱满", "呼吸感", "故事感", "戏剧性", "迅速", "快速", "慢慢变得",
)

#: Purpose phrasing that means "this shot informs" rather than "this shot
#: changes something".  A shot that only informs is replaceable.
GENERIC_PURPOSE_MARKERS: tuple[str, ...] = (
    "交代", "展示", "表现", "体现", "突出", "营造", "说明", "用来介绍",
)

#: On-the-nose dialogue: stating the emotion instead of acting on it.
ON_THE_NOSE_MARKERS: tuple[str, ...] = (
    "我很伤心", "我很难过", "我恨你", "我爱你", "我很生气", "我害怕",
    "我很痛苦", "我对不起你", "我原谅你", "我很孤独",
)

#: Framing words, used for the adjacent-framing rule and the close-up quota.
CLOSE_UP_MARKERS: tuple[str, ...] = ("特写", "极特写", "近景", "大特写")

STATIC_MARKERS: tuple[str, ...] = ("静止", "完全静止", "不动", "固定机位")

#: A beat line looks like ``0.0-1.4s 他抬手`` — the timecode is formatting, not
#: content.  Comparing raw prefixes makes every shot look identical.
BEAT_TIMECODE = re.compile(r"^\s*\d+(?:\.\d+)?\s*[-–~至]\s*\d+(?:\.\d+)?\s*s?\s*")

#: Ratio quotas need a denominator worth measuring.  In a two-shot plan any
#: single choice is 50%, which says nothing about design — it is arithmetic.
MIN_QUOTA_SAMPLE = 4


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _shots(plan: dict[str, Any]) -> list[dict[str, Any]]:
    value = plan.get("shots")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(shot: dict[str, Any], *keys: str) -> str:
    return " ".join(str(shot.get(key) or "") for key in keys)


def finding(
    engine_id: str,
    *,
    severity: str,
    code: str,
    message: str,
    evidence: str,
    fix: str,
    shot_index: int | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "engine": engine_id,
        "severity": severity,
        "code": code,
        "message": message,
        "evidence": evidence,
        "fix": fix,
    }
    if shot_index is not None:
        item["shot"] = shot_index
    return item


def _engine_weight(engine_id: str) -> int:
    for spec in ENGINE_SPECS:
        if spec["engine_id"] == engine_id:
            return int(spec["weight"])
    return 1


# --------------------------------------------------------------------------- #
# engine 1 — 一镜一件事
# --------------------------------------------------------------------------- #

def audit_single_focus(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """A shot must answer: what must the eye find first here, and why not something else?"""
    engine = "engine.single_focus.v1"
    found: list[dict[str, Any]] = []
    for shot in _shots(plan):
        index = shot.get("index")
        purpose = str(shot.get("purpose") or "").strip()
        action = str(shot.get("action") or "").strip()
        if not purpose:
            found.append(finding(
                engine, severity="hard", code="purpose_missing",
                message="这一镜没有写「为什么需要它」——答不出就是填充物。",
                evidence=f"镜头 {index} 的 purpose 为空",
                fix="写清：观众这一刻必须先看到什么，以及它替后面的哪一下做了准备。",
                shot_index=index,
            ))
            continue
        if any(marker in purpose for marker in GENERIC_PURPOSE_MARKERS):
            found.append(finding(
                engine, severity="hard", code="purpose_is_informational",
                message="这一镜的目的写成「交代 / 展示 / 营造」——那是说明，不是功能。",
                evidence=f"镜头 {index}：{purpose}",
                fix="改成它在结构上承担的作用：让观众先看见什么、替哪一下做准备。",
                shot_index=index,
            ))
        if ("和" in purpose or "同时" in purpose or "并且" in purpose) and len(purpose) > 24:
            found.append(finding(
                engine, severity="soft", code="purpose_maybe_plural",
                message="这一镜的目的里出现了并列，可能一镜在做两件事。",
                evidence=f"镜头 {index}：{purpose}",
                fix="如果确实是两件事，拆成两镜；一镜只做一件事。",
                shot_index=index,
            ))
        if len(action) < 8:
            found.append(finding(
                engine, severity="soft", code="action_too_thin",
                message="动作写得太短，难以确认这一镜有没有内容。",
                evidence=f"镜头 {index}：{action}",
                fix="补上方向、接触与可见结果。",
                shot_index=index,
            ))
    return found


# --------------------------------------------------------------------------- #
# engine 2 — 形容词死刑
# --------------------------------------------------------------------------- #

def audit_adjectives(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Every word the model can only guess at must become a number or a visible thing."""
    engine = "engine.adjective_ban.v1"
    found: list[dict[str, Any]] = []
    scan_fields = (
        "lighting", "color", "style", "sceneLine",
    )
    for field in scan_fields:
        text = str(plan.get(field) or "")
        hits = [word for word in VAGUE_ADJECTIVES if word in text]
        if hits:
            found.append(finding(
                engine, severity="hard", code="vague_word_in_plan",
                message="整片设定里出现了模型只能猜的词。",
                evidence=f"{field}：命中 {hits}",
                fix="每个词换成数字或可见物——色温、光比、材质、尺寸、可见结果。",
            ))
    for shot in _shots(plan):
        index = shot.get("index")
        text = _text(shot, "action", "beats", "performance", "cameraMove",
                     "composition", "optics", "wardrobe", "environment", "physics")
        hits = sorted({word for word in VAGUE_ADJECTIVES if word in text})
        if hits:
            found.append(finding(
                engine, severity="hard", code="vague_word_in_shot",
                message="镜头描述里有靠猜的词。",
                evidence=f"镜头 {index}：命中 {hits}",
                fix="换成可测量或可见的写法；「快」不要用，写时速；「强」写力度与方向。",
                shot_index=index,
            ))
    return found


# --------------------------------------------------------------------------- #
# engine 3 — 可替换性
# --------------------------------------------------------------------------- #

def audit_replaceability(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """If swapping the action or line costs the story nothing, the shot is filler.

    Fully automating "what would be lost" is not possible; this engine catches
    the two patterns that made shots replaceable in practice — an action that
    repeats a previous shot, and an action with no physical content at all.
    """
    engine = "engine.replaceability.v1"
    found: list[dict[str, Any]] = []
    shots = _shots(plan)
    if not shots:
        return found

    # Normalised actions that appear more than once are, by definition,
    # interchangeable across those shots.
    seen: dict[str, list[Any]] = {}
    for shot in shots:
        key = str(shot.get("action") or "").strip()
        if len(key) >= 6:
            seen.setdefault(key, []).append(shot.get("index"))
    for action, indices in seen.items():
        if len(indices) > 1:
            found.append(finding(
                engine, severity="hard", code="duplicate_action",
                message="同一个动作在多个镜头里重复——这些镜头之间的差异不来自内容。",
                evidence=f"镜头 {indices} 共用动作：{action[:60]}",
                fix="至少改掉其中一个；重复的动作等于承认它可被替换。",
            ))

    for shot in shots:
        index = shot.get("index")
        action = str(shot.get("action") or "")
        beats = str(shot.get("beats") or "")
        if action and not any(ch in beats for ch in ("0.0", "s ")):
            found.append(finding(
                engine, severity="soft", code="no_time_beats",
                message="动作没有拆成时间节拍，无法判断拿掉它损失什么。",
                evidence=f"镜头 {index}：beats 未按时间写",
                fix="按 0.3–0.8 秒一拍拆开，让每一拍都有可见结果。",
                shot_index=index,
            ))
    return found


# --------------------------------------------------------------------------- #
# engine 4 — 潜台词
# --------------------------------------------------------------------------- #

def audit_subtext(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """A line without subtext is a broadcast, not a scene."""
    engine = "engine.subtext.v1"
    found: list[dict[str, Any]] = []
    for shot in _shots(plan):
        dialogue = str(shot.get("dialogue") or "").strip()
        if not dialogue:
            continue
        index = shot.get("index")
        subtext = str(shot.get("subtext") or shot.get("dialogue_subtext") or "").strip()
        if not subtext:
            found.append(finding(
                engine, severity="hard", code="dialogue_without_subtext",
                message="这句台词没有写「他没说出口的是什么」。",
                evidence=f"镜头 {index}：{dialogue}",
                fix="补 subtext 字段。写不出潜台词，说明这句只是在交代信息，应当改写或删除。",
                shot_index=index,
            ))
        on_the_nose = [word for word in ON_THE_NOSE_MARKERS if word in dialogue]
        if on_the_nose:
            found.append(finding(
                engine, severity="hard", code="emotion_stated_in_dialogue",
                message="台词直接把情绪说出来了。",
                evidence=f"镜头 {index}：命中 {on_the_nose}",
                fix="改成行动：试探、掩饰、退让、挑衅。情绪交给身体。",
                shot_index=index,
            ))
        if len(dialogue) > 24:
            found.append(finding(
                engine, severity="soft", code="line_maybe_too_long",
                message="这句台词偏长，五秒镜头可能说不完（上限按 6.0 字/秒）。",
                evidence=f"镜头 {index}：{len(dialogue)} 字",
                fix="拆句或砍到 18–22 字以内。",
                shot_index=index,
            ))
    return found


# --------------------------------------------------------------------------- #
# engine 5 — 反套路隔离（含跨项目轮换）
# --------------------------------------------------------------------------- #

def detect_cliches(plan: dict[str, Any]) -> dict[str, list[int | str]]:
    """Return ``{cliche_id: [where it was hit]}`` for one plan."""
    corpus = [
        (None, " ".join(str(plan.get(field) or "") for field in
                        ("logline", "promise", "engine", "sceneLine", "lighting", "color", "style")))
    ]
    for shot in _shots(plan):
        corpus.append((shot.get("index"), _text(
            shot, "action", "beats", "performance", "cameraMove", "composition",
            "environment", "physics", "wardrobe", "purpose", "lighting",
        )))
    hits: dict[str, list[int | str]] = {}
    for cliche in BANNED_CLICHES:
        where: list[int | str] = []
        for index, text in corpus:
            if any(pattern in text for pattern in cliche["patterns"]):
                where.append(index if index is not None else "plan")
        if where:
            hits[cliche["cliche_id"]] = where
    return hits


def audit_cliches(
    plan: dict[str, Any],
    *,
    previous_families: dict[str, list[str]] | None = None,
    profile_families: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Flag named clichés, and flag any family already spent on a previous film."""
    engine = "engine.cliche_quarantine.v1"
    found: list[dict[str, Any]] = []
    hits = detect_cliches(plan)
    by_id = {cliche["cliche_id"]: cliche for cliche in BANNED_CLICHES}

    for cliche_id, where in hits.items():
        cliche = by_id[cliche_id]
        found.append(finding(
            engine, severity="hard", code="cliche_hit",
            message=f"命中被点名的套路：{cliche['label']}",
            evidence=f"出现在 {where}；原因：{cliche['why']}",
            fix=cliche["instead"],
        ))

    budget = int(QUOTAS.get("clicheHitsPerFilmMax", 1))
    if len(hits) > budget:
        found.append(finding(
            engine, severity="hard", code="cliche_budget_exceeded",
            message=f"全片命中 {len(hits)} 个套路，超过上限 {budget}。",
            evidence="命中列表：" + "、".join(by_id[key]["label"] for key in hits),
            fix="全片最多留一个套路。其余换成能带信息的具体设定。",
        ))

    # Cross-project rotation: a family already spent does not get spent again.
    previous = previous_families or {}
    for project, families in previous.items():
        for field, value in (profile_families or {}).items():
            if value and value in families:
                found.append(finding(
                    engine, severity="hard", code="family_reused",
                    message=f"「{field}」这一族与上一部「{project}」重复。",
                    evidence=f"本部 {field}={value}；上一部也用过",
                    fix="换族。同一族连用两次，等于没有做视觉设计。",
                ))
    return found


# --------------------------------------------------------------------------- #
# engine 6 — 多样性配额
# --------------------------------------------------------------------------- #

def audit_diversity(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Distribution checks: motion, framing, lighting runs, and rewarded shots."""
    engine = "engine.diversity_quota.v1"
    found: list[dict[str, Any]] = []
    shots = _shots(plan)
    total = len(shots)
    if total == 0:
        return found

    static = sum(1 for s in shots if any(m in str(s.get("cameraMove") or "") for m in STATIC_MARKERS))
    ratio = static / total
    limit = float(QUOTAS["staticShotRatioMax"])
    if total >= MIN_QUOTA_SAMPLE and ratio > limit:
        found.append(finding(
            engine, severity="hard", code="static_ratio_exceeded",
            message=f"静止镜头占 {ratio:.0%}，超过上限 {limit:.0%}。",
            evidence=f"{static}/{total} 镜是静止的",
            fix="运动是设计出来的，不是默认省掉的。给该动的镜头设计动机明确的运动。",
        ))

    moves = Counter(
        str(s.get("cameraMove") or "").strip()[:24]
        for s in shots
        if str(s.get("cameraMove") or "").strip()
        and not any(m in str(s.get("cameraMove") or "") for m in STATIC_MARKERS)
    )
    if moves and total >= MIN_QUOTA_SAMPLE:
        top_move, top_count = moves.most_common(1)[0]
        move_ratio = top_count / total
        if move_ratio > float(QUOTAS["singleCameraMoveRatioMax"]):
            found.append(finding(
                engine, severity="hard", code="single_move_dominates",
                message=f"「{top_move}」占了 {move_ratio:.0%} 的镜头。",
                evidence=f"{top_count}/{total} 镜用同一种运动",
                fix="换运动方式；只有一种运动的片子，等于没有运动设计。",
            ))

    if not QUOTAS.get("adjacentSameFramingAllowed", False):
        for prev, cur in zip(shots, shots[1:]):
            prev_framing = _framing(str(prev.get("composition") or ""))
            cur_framing = _framing(str(cur.get("composition") or ""))
            if prev_framing and prev_framing == cur_framing:
                found.append(finding(
                    engine, severity="hard", code="adjacent_same_framing",
                    message="相邻两镜景别相同。",
                    evidence=f"镜头 {prev.get('index')} 与 {cur.get('index')} 都是「{prev_framing}」",
                    fix="至少改掉一个的景别。相邻同景别是最容易被看出的业余痕迹。",
                    shot_index=cur.get("index"),
                ))

    close_ups = sum(
        1 for s in shots
        if any(marker in str(s.get("composition") or "") for marker in CLOSE_UP_MARKERS)
    )
    close_ratio = close_ups / total
    if close_ratio > float(QUOTAS["mustRewardShotRatioMax"]):
        found.append(finding(
            engine, severity="soft", code="closeup_ratio_high",
            message=f"特写 / 近景占 {close_ratio:.0%}，超过「必须有理由才给」的上限。",
            evidence=f"{close_ups}/{total} 镜是近距",
            fix="特写是奖励，不是默认。把一部分降到中景或全景，让空间交代清楚。",
        ))

    return found


def _framing(composition: str) -> str:
    """Extract a coarse framing word so two shots can be compared."""
    for word in ("极特写", "特写", "近景", "中近景", "中景", "全景", "大全景", "远景"):
        if word in composition:
            return word
    return ""


# --------------------------------------------------------------------------- #
# engine 7 — 不可预测性（软性代理）
# --------------------------------------------------------------------------- #

def _beat_opening(beats: str) -> str:
    """First real beat content, with the timecode stripped off.

    Also collapses a leading ``0.0-…`` opener so two shots that both begin at
    zero seconds are compared on *what happens*, not on the fact that they
    both start at the top.
    """
    first = beats.strip().split("；")[0].split(";")[0].strip()
    stripped = BEAT_TIMECODE.sub("", first).strip()
    return stripped[:10]


def audit_unpredictability(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Proxy for "can the audience guess this?": repeated shapes read as guessable."""
    engine = "engine.unpredictability.v1"
    found: list[dict[str, Any]] = []
    shots = _shots(plan)
    if len(shots) < 3:
        return found

    openings = Counter(
        _beat_opening(str(s.get("beats") or "")) for s in shots if str(s.get("beats") or "").strip()
    )
    if openings:
        top_open, count = openings.most_common(1)[0]
        if count >= max(3, len(shots) // 2):
            found.append(finding(
                engine, severity="soft", code="uniform_beat_shape",
                message="多数镜头以同样的方式开局，节奏可被预测。",
                evidence=f"{count} 个镜头都以「{top_open}」开头",
                fix="让镜头以不同的方式进入：有的从结果开始，有的从环境开始，有的从中途开始。",
            ))

    if all(str(s.get("purpose") or "").startswith("让观众") for s in shots[:4]):
        found.append(finding(
            engine, severity="soft", code="uniform_purpose_phrasing",
            message="前几镜的目的用了同一个句式，说明是按模板在填。",
            evidence="前 4 镜 purpose 均以「让观众」开头",
            fix="每镜先说清它在结构上的位置，再谈观众。句式一致往往意味着内容也一致。",
        ))
    return found


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #

def score_report(findings: list[dict[str, Any]], *, shot_count: int) -> dict[str, Any]:
    """Turn findings into a verdict.  Scores, not a pass/fail bit.

    A pass/fail gate cannot express "it is finished but it is ordinary", which
    is exactly the failure mode this whole module exists to catch.
    """
    hard = [item for item in findings if item["severity"] == "hard"]
    soft = [item for item in findings if item["severity"] == "soft"]

    penalty = 0
    for item in hard:
        penalty += 12 * _engine_weight(item["engine"])
    for item in soft:
        penalty += 3 * _engine_weight(item["engine"])
    score = max(0, 100 - penalty)

    if hard:
        verdict = "blocked"
    elif score >= 85:
        verdict = "ship"
    elif score >= 70:
        verdict = "revise"
    else:
        verdict = "blocked"

    per_engine: dict[str, int] = Counter(item["engine"] for item in findings)
    return {
        "schema": TASTE_AUDIT_SCHEMA,
        "score": score,
        "verdict": verdict,
        "shotCount": shot_count,
        "hardCount": len(hard),
        "softCount": len(soft),
        "findingsPerEngine": dict(per_engine),
        "hardFindings": hard,
        "softFindings": soft,
        "readThis": (
            "此分数只衡量「有没有滑向默认值」，不衡量「好不好看」。"
            "好看与否必须靠人看，机器证明不了。"
        ),
    }


#: Garments that a shot list must track explicitly once the plan mentions them
#: anywhere.  The failure this prevents is invisible to every other engine: a
#: character wore a coat in the arrival shot, took it off to be measured, and
#: then appeared wearing it again in the shot where his shirt's shoulder fit is
#: being checked.  No aesthetic rule catches that, because each shot is fine on
#: its own — the defect only exists *between* shots.
TRACKED_GARMENTS: tuple[str, ...] = ("外套", "大衣", "围巾", "帽子", "领带", "手套", "眼镜")

#: Words that state a garment's state in this shot.  A declaration without one
#: of these does not tell the model (or the reader) whether it is worn.
GARMENT_STATE_MARKERS: tuple[str, ...] = (
    "穿", "上身", "搭在", "脱下", "不在画面", "没有", "外搭", "披",
)


def audit_wardrobe_continuity(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Require every shot to declare each tracked garment's state.

    This is deliberately a *plan completeness* check rather than an image check:
    it does not claim to see the picture.  What it can prove is whether the shot
    list leaves a garment's presence ambiguous — and an ambiguous wardrobe is
    exactly what the video model resolves inconsistently from shot to shot.
    """
    shots = _shots(plan)
    if not shots:
        return []

    mentioned = {
        garment
        for garment in TRACKED_GARMENTS
        if any(garment in _text(shot, "wardrobe") for shot in shots)
    }
    if not mentioned:
        return []

    findings: list[dict[str, Any]] = []
    for shot in shots:
        # An empty frame has no wardrobe: requiring a coat's state in a shot
        # with nobody in it is a false positive, and a check that cries wolf
        # on the first shot gets ignored by the time it matters.
        cast = shot.get("cast")
        if isinstance(cast, list) and not cast:
            continue
        wardrobe = _text(shot, "wardrobe")
        for garment in sorted(mentioned):
            if garment not in wardrobe:
                findings.append(
                    finding(
                        "wardrobe-continuity",
                        severity="hard",
                        code="wardrobe.untracked-garment",
                        message=f"第 {shot.get('index')} 镜没有交代「{garment}」在不在身上",
                        evidence=f"wardrobe={wardrobe[:60]}",
                        fix=(
                            f"在本镜 wardrobe 里写明「{garment}」的状态"
                            "（穿在身上／已脱下搭在某处／不在画面里），"
                            "否则模型会在镜与镜之间自行决定它穿没穿"
                        ),
                        shot_index=_as_index(shot.get("index")),
                    )
                )
            elif not any(marker in wardrobe for marker in GARMENT_STATE_MARKERS):
                findings.append(
                    finding(
                        "wardrobe-continuity",
                        severity="soft",
                        code="wardrobe.ambiguous-state",
                        message=f"第 {shot.get('index')} 镜提到了「{garment}」但没说清状态",
                        evidence=f"wardrobe={wardrobe[:60]}",
                        fix=f"补一个状态词（{'／'.join(GARMENT_STATE_MARKERS[:5])}）",
                        shot_index=_as_index(shot.get("index")),
                    )
                )
    return findings


def _as_index(value: Any) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def audit_plan(
    plan: dict[str, Any],
    *,
    previous_families: dict[str, list[str]] | None = None,
    profile_families: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run every engine over one plan and return the scored report."""
    findings: list[dict[str, Any]] = []
    findings += audit_single_focus(plan)
    findings += audit_adjectives(plan)
    findings += audit_replaceability(plan)
    findings += audit_subtext(plan)
    findings += audit_cliches(
        plan, previous_families=previous_families, profile_families=profile_families
    )
    findings += audit_diversity(plan)
    findings += audit_unpredictability(plan)
    findings += audit_wardrobe_continuity(plan)
    return score_report(findings, shot_count=len(_shots(plan)))


def paired_sample_for(slot: str) -> dict[str, str] | None:
    """Return the WEAK/STRONG pair for a slot, for teaching rather than telling."""
    for sample in PAIRED_SAMPLES:
        if sample["slot"] == slot:
            return sample
    return None


__all__ = [
    "TASTE_AUDIT_SCHEMA",
    "VAGUE_ADJECTIVES",
    "audit_plan",
    "audit_single_focus",
    "audit_adjectives",
    "audit_replaceability",
    "audit_subtext",
    "audit_cliches",
    "detect_cliches",
    "audit_diversity",
    "audit_unpredictability",
    "audit_wardrobe_continuity",
    "TRACKED_GARMENTS",
    "score_report",
    "paired_sample_for",
]
