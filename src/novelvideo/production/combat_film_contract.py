"""Read-only combat-film contract: structure, declarations, damage continuity.

This module answers one narrow question for a fight short: *is the shot package
structurally legal and are its combat declarations complete and mutually
consistent?*  It is pure data and pure functions.  It does not call a model, a
network, or ffmpeg, and it does not generate media.

Honest limits (measured on this repository, 2026-09-22):

* The contract judges *declarations*, never pixels.  Six of the seven external
  action rules (who moves, how, who falls, two-shot framing, on-screen physical
  causality, sound narration) need motion understanding this repository does not
  have.  A ``passed`` result here means "the package is legal and its claims do
  not contradict each other", never "the fight looks good".
* Spoken lines are judged only as a declaration (``cinematic.combat.
  dialogue_lines``).  The seven-step production chain has no dialogue carrier:
  ``create_video_prompt_node`` has no dialogue field, ``executor`` never passes
  lines into that command, and ``StoryboardShot`` has no dialogue field.  A
  non-empty declaration therefore does **not** prove the line reaches the video
  model.
* Total duration **is** judged, but only when the request names a number of
  seconds, and only where a plan's own total is the number that ships.  An
  earlier revision of this module refused to judge it on the grounds that
  ``_normalize_plan_duration`` always converges the total -- that is true on the
  ``one-click-film`` chain (measured: 34.02 s -> 30.0 s) and false on the
  ``freezone-*`` chain, which never calls that normalizer and never compared its
  own ``total_duration_seconds`` to the request.  Measured on that chain: a
  request for a 30 s film produced a 2.041 s MP4 with no gate objecting.  See
  :func:`requested_duration_seconds` and ``_duration_issues``.
* Per-shot windows and shot count are judged instead of total duration *when no
  duration is named*; see :func:`combat_structure_capacity` for the arithmetic
  that keeps a target and the 4 s per-shot floor from contradicting each other.
* The per-shot upper bound ``<= 12`` shots is defense in depth for direct
  callers: at the executor boundary ``StoryboardPlan.shots`` already caps at 12,
  so a 13th shot can never reach this function through that path.
* The gates are **not** registered in ``quality_stage.CROSS_SHOT_GATES``.
  That set is consulted only when ``visual_continuity_applicable`` is false,
  and that flag is derived from ``minimum_shots``, which is 1 for the whole
  ``idea`` tier.  Registering the combat gates there would erase them for
  exactly the flagship request ("30 秒极限打斗"), because
  ``infer_delivery_level`` maps that sentence to ``idea``.  A single-shot fight
  is reported as ``combat.structure.shot_count_below_min`` instead.
* These gates have **no producer today**.  ``executor``'s storyboard prompt
  lists no ``combat`` key and nothing in the repository writes
  ``duel_scope`` / ``combatant_count`` / ``causality_chain`` / ``damage_state``.
  Because the contract is fail-closed, a real fight request that reaches the
  quality stage without hand-authored declarations *will fail*.  That is the
  intended fail-closed behaviour, not a passing gate: shipping a producer needs
  a prompt/whitelist/transport change outside this module.
* ``freezone-*`` runs the final-film chain without a ``quality_review`` step
  (``definitions.py`` ``_final_film_steps``), so these gates never execute
  there.  The one part of this module that does fire on that chain is
  :func:`requested_duration_seconds`, wired into ``production_plan``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from novelvideo.production.cinematic_contract import audit_cinematic_contracts
from novelvideo.production.combat_prompt_kb import (
    _COMBAT_TERMS,  # 复用语料词表，避免第二套战斗词汇漂移
)
from novelvideo.production.dialogue_sound_contract import (
    audit_dialogue_sound_contract,
    build_dialogue_sound_contract,
)


COMBAT_FILM_SCHEMA = "combat_film_contract.v1"
COMBAT_FILM_STRUCTURE_GATE = "combat_film_structure_ready"
COMBAT_FILM_DECLARATIONS_GATE = "combat_film_declarations_complete"
COMBAT_FILM_GATES: tuple[str, ...] = (
    COMBAT_FILM_STRUCTURE_GATE,
    COMBAT_FILM_DECLARATIONS_GATE,
)

# Video-model capability window (direct_video_profiles.duration starts at 4 s).
MIN_SHOT_SECONDS = 4.0
MAX_SHOT_SECONDS = 15.0
# StoryboardPlan.shots is Field(min_length=1, max_length=12); 2 is this
# contract's own floor because a duel needs at least two beats.
MIN_SHOTS = 2
MAX_SHOTS = 12

DAMAGE_STATES: tuple[str, ...] = ("intact", "minor", "severe", "down")
CAUSALITY_CHAIN: tuple[str, ...] = ("contact", "compression", "failure", "no_recovery")
REQUIRED_DUEL_SCOPE = "1v1"
REQUIRED_COMBATANT_COUNT = 2
CINEMATIC_FAMILIES: tuple[str, ...] = (
    "lighting",
    "color_look",
    "screen_direction",
    "edit",
    "sound",
)
# Each declaration family maps to the gate the *shared* cinematic audit uses for
# it (``cinematic_contract.CINEMATIC_QUALITY_GATES``).  The mapping is what makes
# "declared" mean "the shared audit actually claimed this family": that audit
# decides applicability from known *field names* and leaves the observation at
# ``None`` for a family it does not claim, so a placeholder object such as
# ``{"placeholder": True}`` normalizes to ``{}`` and is never claimed.  Measured
# before this mapping existed: four placeholder families plus a placeholder
# sound string returned ``passed=True, issues=0, ready_for_media=True``.
CINEMATIC_FAMILY_GATES: tuple[tuple[str, str], ...] = (
    ("lighting", "lighting_continuity_consistent"),
    ("color_look", "color_look_consistent"),
    ("screen_direction", "screen_direction_consistent"),
    ("edit", "edit_rhythm_ready"),
    ("sound", "sound_design_ready"),
)

# --- Combat-intent detection -------------------------------------------------
#
# ``combat_prompt_kb.is_combat_intent`` is a bare substring test and is used by
# the prompt injector, where a false positive only adds writing guidance.  A
# gate cannot afford that: a false positive is fail-closed and kills a normal
# run.  Measured false positives of the bare test: "一个关于反击命运的故事"
# and "把这段格挡式教育故事拍成短片".  The tiering below keeps the vocabulary
# but splits it by what the word actually names:
#
#   tier 1  a staged fight is *named* ("打斗" / "武戏" / "动作戏" / "拳击比赛"),
#           unless the name only modifies a non-fight programme form (see
#           :data:`_NON_FIGHT_PROGRAM_HEADS`);
#   tier 2  a *match-up* noun ("格斗" / "对决" / "交手" / "拳击" / "比武"),
#           ambiguous on its own, so it needs one corroborator in the clause;
#   tier 3  combat verbs: two distinct ones, or one beside an opponent marker.
#
# Measured reason for tier 2: the six ordinary requests that the flat
# strong-term rule turned into fail-closed fight runs -- "拳击手套产品广告，30秒",
# "记录一场村际篮球对决，30秒", "拍一个拳击馆的宣传片",
# "这次是两个团队的对决，30秒纪录片", "一个人与命运搏斗的励志短片",
# "拍一段师徒交手切磋的纪录片" -- all carry a tier-2 noun and nothing else,
# while "拍一段拳击比赛纪录短片" and "拍一段武侠对打" name the fight itself.

_STRONG_TERMS: tuple[str, ...] = (
    "打斗",
    "打戏",
    "武戏",
    "格斗",
    "搏斗",
    "肉搏",
    "对战",
    "对决",
    "决斗",
    "对打",
    "交手",
    "比武",
    "拳击",
    "短兵相接",
)
# Tier 1a: terms that name a staged fight on their own.  The split against the
# match-up nouns is drawn on what the word denotes: 打斗/武戏/肉搏 describe the
# film's violence, while 对决/交手/拳击 only label two sides or a sport -- and a
# product ad, a venue promo, a team match-up, a training demo or a metaphor can
# label those without asking for a staged fight.
_EXPLICIT_FIGHT_TERMS: tuple[str, ...] = (
    "打斗",
    "打戏",
    "武戏",
    "肉搏",
    "短兵相接",
)
# Tier 1b: the staged-fight reading lives in the phrase rather than in a tier-1a
# term ("动作戏" / "拳击比赛" contain none).  Scanned with the same
# negation/metaphor filter as the terms: a raw ``phrase in text`` test let
# "不要拍打斗场面，只拍师徒交手切磋的纪录片" through on the *negated* phrase.
_EXPLICIT_FIGHT_PHRASES: tuple[str, ...] = (
    "打斗片",
    "打斗戏",
    "打斗场面",
    "打斗镜头",
    "动作戏",
    "武打",
    "肉搏战",
    "肉搏戏",
    "拳击比赛",
    "拳击赛",
    "拳击对抗",
    "格斗比赛",
    "格斗对抗",
    "格斗赛",
    "综合格斗",
    "自由搏击",
    "散打比赛",
    "搏击比赛",
    "搏斗场面",
    "扭打场面",
)
# One scan list for tier 1, so a phrase and a term cannot drift apart in how
# they are filtered.
_EXPLICIT_FIGHT_NAMES: tuple[str, ...] = _EXPLICIT_FIGHT_TERMS + _EXPLICIT_FIGHT_PHRASES
# Tier 2: match-up nouns.  Every one of them is measured inside a normal,
# non-fight request (see the module comment above), so none may fire alone.
_AMBIGUOUS_STRONG_TERMS: tuple[str, ...] = tuple(
    term for term in _STRONG_TERMS if term not in _EXPLICIT_FIGHT_TERMS
)
# Fight verbs measured *missing* from the shared KB vocabulary: real fights the
# detector failed to recognise ("两人贴身缠斗", "两人扭打在一起", "两人挥拳互殴"
# all returned False).  Kept here rather than in ``combat_prompt_kb`` because
# that module is a distilled archive with pinned sha256 provenance, and this is
# a gate-side recall fix, not archive content.
_CONTRACT_ACTION_TERMS: tuple[str, ...] = (
    "缠斗",
    "扭打",
    "厮打",
    "互殴",
    "挥拳",
    "近身格斗",
)
_ACTION_TERMS: tuple[str, ...] = tuple(
    term for term in _COMBAT_TERMS if term not in _STRONG_TERMS
) + _CONTRACT_ACTION_TERMS
# Combat verbs that also carry a productive everyday reading, so one of them
# alone must not carry the "action + opponent" rule.  Measured false positives
# that this tuple removes: "两人在咖啡馆重逢，互相闪避对方的目光" (two people
# avoiding each other's eyes) and "两个同事…互相反击对方的质疑" (colleagues
# rebutting each other's arguments) were both read as a staged fight and turned
# a normal run into a fail-closed fight gate.  They still count for the
# two-distinct-actions rule, which is deliberately left unchanged.
_WEAK_ACTION_TERMS: tuple[str, ...] = (
    "闪避",
    "反击",
)
_OPPONENT_MARKERS: tuple[str, ...] = (
    "双方",
    "两人",
    "二人",
    "一对一",
    "1v1",
    "对手",
    "贴身",
    "近身",
    "互殴",
    "互相",
)
_NEGATION_MARKERS: tuple[str, ...] = (
    "不是",
    "不要",
    "不用",
    "没有",
    "并非",
    "无需",
    "避免",
    "禁止",
    "别",
    "不",
    "无",
)
# Phrases that *contain* a negation character without negating the fight verb.
# Measured reason: "两人不停交手" and "双方不断对打" were read as negations and
# dropped a real fight; "不但打斗精彩" likewise.  Double negatives count too:
# "不得不打斗" ("have no choice but to fight") and "无不惊叹的打斗" both name a
# fight, and both used to be read as negations.
_NON_NEGATING_PHRASES: tuple[str, ...] = (
    "不但",
    "不仅",
    "不只",
    "不光",
    "不单",
    "不停",
    "不断",
    "不住",
    "不止",
    "不得不",
    "无不",
    "无比",
    "无法避免",
)
# "反击命运" / "格挡式教育" style metaphor: a combat verb reused as an
# adjective for an abstract noun does not describe a fight.  The abstract noun
# can sit on either side of the verb: "反击命运" puts it after, "与命运搏斗"
# puts it before, and the tail check alone missed the second shape -- which is
# exactly the shape a continuity modifier must not turn into a fight
# ("一个人与命运不断搏斗的励志短片").
_METAPHOR_SUFFIXES: tuple[str, ...] = ("式", "型", "性", "化", "般的", "似的")
_METAPHOR_CONNECTIVES: tuple[str, ...] = ("与", "同", "和", "跟", "向", "对")
_ABSTRACT_OBJECTS: tuple[str, ...] = (
    "命运",
    "人生",
    "宿命",
    "偏见",
    "质疑",
    "目光",
    "视线",
    "生活",
    "制度",
    "教育",
    "传统",
    "习惯",
    "规则",
    "秩序",
    "叙事",
    "套路",
    "情绪",
    "命运",
)
_EXPLICIT_GENRES: frozenset[str] = frozenset(
    {"combat", "fight", "wuxia", "wuxi", "武戏", "打戏", "战斗", "打斗"}
)
_CLAUSE_BOUNDARIES: tuple[str, ...] = ("，", "。", "！", "？", "；", "、", ",", ".", "!", "?", ";", ":", "\n")
# How far back inside one clause a negation may reach.  Measured need: in
# "不希望出现任何格斗镜头" the negation sits 7 characters before the term; a
# 3-character lookback (the previous rule) missed it and read the sentence as a
# fight request.
_NEGATION_WINDOW = 12
# Tier-2 corroborators.  A match-up noun is a staged fight when the same clause
# says the combat is *sustained* ("两人不停交手" / "双方不断对打" -- the reading
# ``_NON_NEGATING_PHRASES`` already existed to protect) or stages it in fight
# vocabulary ("拍一段武侠对打" / "一场擂台比武").  Training and sport vocabulary
# ("师徒交手切磋" / "村际篮球对决") is neither, and that is the measured
# difference between the two groups.
_CONTINUITY_MARKERS: tuple[str, ...] = (
    "不停",
    "不断",
    "不住",
    "接连",
    "连续",
    "轮番",
    "反复",
    "来回",
)
# The modifier directly in front of the noun is the one that governs it.
_CONTINUITY_WINDOW = 6
_FIGHT_CONTEXT_TERMS: tuple[str, ...] = (
    "武侠",
    "江湖",
    "门派",
    "侠客",
    "刀客",
    "剑客",
    "刺客",
    "招式",
    "武馆",
    "擂台",
    "兵器",
    "刀剑",
)
# Tier-1 names that are only *modifying* a non-fight programme form.  Measured
# (2026-09-27): five ordinary requests were fail-closed as fights through
# ``build_cinematic_review`` -- "拍一段武打片的幕后花絮",
# "综合格斗选手的成长纪录片", "散打比赛的赛前采访",
# "自由搏击健身房的招生广告", "拳击比赛的赛后采访".  All five share one shape:
# the fight word says *what the programme is about* or *who is in it*, and the
# head noun after it names the programme that actually ships.  The head list is
# deliberately short -- every entry is one of the forms measured above, and each
# extra entry would cost a real fight request (a "打斗预告片" is still a fight).
_NON_FIGHT_PROGRAM_HEADS: tuple[str, ...] = (
    "花絮",
    "幕后",
    "纪录片",
    "采访",
    "访谈",
    "广告",
    "招生",
    "影评",
    "集锦",
    "探班",
)
# How far past the name the head noun may sit.  Sized on the longest measured
# cases: "综合格斗选手的成长纪录片" and "武打片导演的工作访谈" need 9
# characters.  Clause-scoped, so a head in the *next* clause does not count.
_ATTRIBUTIVE_WINDOW = 9
# Training/demo language can contain two combat vocabulary items without
# asking for a staged fight ("武术教学，示范格挡" / "学员近身练习擒拿").
# It only suppresses the tier-3 action rules; explicit fight names and staged
# tier-2 context still win.
_NON_FIGHT_TRAINING_MARKERS: tuple[str, ...] = (
    "教学",
    "学员",
    "练习",
    "训练",
    "示范",
)
# Plain "打" is intentionally absent from the action vocabulary because it is
# too broad.  These are the measured sustained-fight constructions that make
# it safe: a fight moves between places or explicitly becomes a physical mêlée.
_SUSTAINED_MOTION_RE = re.compile(
    r"(?:打起来|打在一起|打成一团|从[^，。！？；：:]{1,12}打到)"
)

# --- Requested duration ------------------------------------------------------
#
# Matches the delivery-QC default tolerance (delivery_qc_contract.py:187).
DURATION_TOLERANCE_SECONDS = 1.0


def _text(value: object, *, limit: int = 2000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _list(value: object) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _number(value: object) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _shot_id(shot: Mapping[str, Any], index: int) -> str:
    return _text(shot.get("shot_id") or shot.get("shotId"), limit=120) or f"S{index:02d}"


def _shot_seconds(shot: Mapping[str, Any]) -> float | None:
    for key in ("duration_seconds", "durationSeconds", "duration"):
        value = _number(shot.get(key))
        if value is not None:
            return value
    return None


def requested_duration_seconds(request: object) -> float | None:
    """Return the seconds named in a request, or ``None`` when none is named.

    Deterministic and model-free.  ``None`` means *not judged*: a request that
    never names a duration must not be failed for not matching one.  The window
    (2..240 s) mirrors the executor's parser so the two cannot disagree.
    """

    from novelvideo.utils.requested_duration import (
        requested_duration_seconds as parse_requested_duration,
    )

    return parse_requested_duration(request)


def _clause_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Return the clause around ``text[start:end]``, bounded by the shared set."""

    left = max((text.rfind(char, 0, start) for char in _CLAUSE_BOUNDARIES), default=-1)
    rights = [text.find(char, end) for char in _CLAUSE_BOUNDARIES]
    right = min((index for index in rights if index >= 0), default=len(text))
    return left + 1, right


def _clause_head(text: str, start: int, window: int) -> str:
    """The last ``window`` characters before ``start`` inside its own clause."""

    left, _ = _clause_bounds(text, start, start)
    return text[max(left, start - window) : start]


def _sustained(text: str, start: int) -> bool:
    """True when a sustained-combat modifier directly precedes ``start``."""

    head = _clause_head(text, start, _CONTINUITY_WINDOW)
    return any(marker in head for marker in _CONTINUITY_MARKERS)


def _staged_context(text: str, start: int, end: int) -> bool:
    """True when the same clause stages the fight in fight vocabulary."""

    left, right = _clause_bounds(text, start, end)
    clause = text[left:right]
    return any(term in clause for term in _FIGHT_CONTEXT_TERMS)


def _sustained_motion(text: str) -> bool:
    """True for a narrow, clause-local physical fight-motion construction."""

    for marker in _OPPONENT_MARKERS:
        cursor = text.find(marker)
        while cursor >= 0:
            left, right = _clause_bounds(text, cursor, cursor + len(marker))
            clause = text[left:right]
            if _SUSTAINED_MOTION_RE.search(clause):
                return True
            cursor = text.find(marker, cursor + len(marker))
    return False


def _training_context(text: str) -> bool:
    """True when a candidate is explicitly framed as training or a demo."""

    return any(marker in text for marker in _NON_FIGHT_TRAINING_MARKERS)


def _negated(text: str, start: int) -> bool:
    """True when a negation marker governs the term starting at ``start``.

    Clause-scoped on purpose.  The previous rule looked at a fixed 3-character
    window and measured two ways wrong: it missed ``没有`` in
    "这个片子完全没有任何打斗" (the marker ends two characters before the term)
    and it read ``不`` inside ``不停``/``不断``/``不但`` as a negation, turning
    real fights into non-fights.

    A marker occurrence is *covered* when it sits inside a longer phrase that
    does not negate (``不停``, ``不得不``, ``无不``).  Coverage is checked per
    occurrence, not per phrase: in "不得不打斗" the plain ``不`` at the end of
    ``不得不`` must not be read on its own, or the double negative is lost.
    """

    left, _ = _clause_bounds(text, start, start)
    clause = text[left:start][-_NEGATION_WINDOW:]
    if not clause:
        return False
    covered: set[int] = set()
    for phrase in _NON_NEGATING_PHRASES:
        cursor = clause.find(phrase)
        while cursor >= 0:
            covered.update(range(cursor, min(cursor + len(phrase), len(clause))))
            cursor = clause.find(phrase, cursor + 1)
    for marker in _NEGATION_MARKERS:
        cursor = clause.find(marker)
        while cursor >= 0:
            if not any(index in covered for index in range(cursor, cursor + len(marker))):
                return True
            cursor = clause.find(marker, cursor + 1)
    return False


def _abstract_object_subject(text: str, start: int) -> bool:
    """True when the clause aims the combat word at an abstraction.

    "与命运搏斗" / "与自己的命运搏斗" / "不断与命运搏斗" all put the abstract
    noun *before* the verb, with the connective somewhere earlier in the same
    clause, so the tail check in :func:`_metaphorical` cannot see them.
    """

    left, _ = _clause_bounds(text, start, start)
    head = text[left:start]
    if not head:
        return False
    for noun in _ABSTRACT_OBJECTS:
        cursor = head.find(noun)
        while cursor >= 0:
            if any(connective in head[:cursor] for connective in _METAPHOR_CONNECTIVES):
                return True
            cursor = head.find(noun, cursor + 1)
    return False


def _metaphorical(text: str, start: int, end: int) -> bool:
    """True when the combat word is an adjective or is aimed at an abstraction."""

    tail = text[end : end + 4]
    if any(tail.startswith(suffix) for suffix in _METAPHOR_SUFFIXES):
        return True
    if any(tail.startswith(noun) for noun in _ABSTRACT_OBJECTS):
        return True
    return _abstract_object_subject(text, start)


def _attributive_modifier(text: str, start: int, end: int) -> bool:
    """True when a tier-1 name only modifies a non-fight programme form.

    "武打片**的幕后花絮**" / "综合格斗**选手的成长纪录片**" /
    "拳击比赛**的赛后采访**": the film is a making-of, a documentary or an
    interview *about* the sport, not a staged fight.  Without this filter all
    three fired as fights and were fail-closed at the quality step.

    The lookahead is clause-scoped and short: "打斗片" / "打斗场面" /
    "拳击比赛纪录短片" have no programme head after the name, so they still
    fire; a head noun in the next clause ("拍一段打斗片，幕后花絮另说") cannot
    reach back and silence the fight.
    """

    _, right = _clause_bounds(text, start, end)
    tail = text[end : min(right, end + _ATTRIBUTIVE_WINDOW)]
    return any(head in tail for head in _NON_FIGHT_PROGRAM_HEADS)


def _surviving_terms(
    text: str,
    terms: tuple[str, ...],
    *,
    filter_attributive: bool = False,
) -> tuple[list[str], list[str], list[str], list[int]]:
    """Scan ``terms`` in order and return the ones surviving the filters.

    A term contributes at most one surviving entry -- its first occurrence that
    is neither negated, metaphorical, nor (when ``filter_attributive`` is set)
    merely modifying a non-fight programme form -- and the start index of that
    occurrence, so the caller can look at what modifies it.  Occurrences before
    that one are still reported as ``negated`` / ``attributive`` evidence, which
    is what lets a test prove the vocabulary *did* fire and the rule, not a
    vocabulary gap, rejected the sentence.
    """

    matched: list[str] = []
    negated: list[str] = []
    attributive: list[str] = []
    starts: list[int] = []
    for term in terms:
        cursor = text.find(term)
        while cursor >= 0:
            end = cursor + len(term)
            if _negated(text, cursor):
                negated.append(term)
            elif _metaphorical(text, cursor, end):
                pass
            elif filter_attributive and _attributive_modifier(text, cursor, end):
                attributive.append(term)
            else:
                matched.append(term)
                starts.append(cursor)
                break
            cursor = text.find(term, end)
    return matched, negated, attributive, starts


def combat_intent_matches(text: object) -> dict[str, list[str]]:
    """Return the combat vocabulary that actually fired, for evidence.

    Deterministic and model-free: identical input always yields identical
    output.  Every bucket is filtered by the same negation/metaphor rules.
    ``explicit`` lists staged-fight names (tier 1), ``strong`` lists the
    match-up nouns among them (tier 2), ``action`` lists combat verbs (tier 3)
    and ``sustained`` lists match-up nouns that a corroborator in the same
    clause turns into a staged fight.  ``attributive`` lists tier-1 names that
    were *dropped* because they only modify a non-fight programme form.
    """

    normalized = _text(text, limit=4000)
    if not normalized:
        return {
            "explicit": [],
            "strong": [],
            "action": [],
            "opponent": [],
            "sustained": [],
            "attributive": [],
            "negated": [],
        }
    explicit, explicit_negated, explicit_attributive, _ = _surviving_terms(
        normalized,
        _EXPLICIT_FIGHT_NAMES,
        filter_attributive=True,
    )
    strong, strong_negated, _, strong_starts = _surviving_terms(normalized, _STRONG_TERMS)
    action, action_negated, _, _ = _surviving_terms(normalized, _ACTION_TERMS)
    ambiguous = frozenset(_AMBIGUOUS_STRONG_TERMS)
    sustained = [
        term
        for term, start in zip(strong, strong_starts)
        if term in ambiguous
        and (
            _sustained(normalized, start)
            or _staged_context(normalized, start, start + len(term))
        )
    ]
    if _sustained_motion(normalized):
        sustained.append("持续动作")
    opponent = [marker for marker in _OPPONENT_MARKERS if marker in normalized]
    return {
        "explicit": list(dict.fromkeys(explicit)),
        "strong": list(dict.fromkeys(strong)),
        "action": list(dict.fromkeys(action)),
        "opponent": opponent,
        "sustained": list(dict.fromkeys(sustained)),
        "attributive": list(dict.fromkeys(explicit_attributive)),
        "negated": list(
            dict.fromkeys([*explicit_negated, *strong_negated, *action_negated])
        ),
    }


def is_combat_film_request(intent: object = None, request: object = "") -> bool:
    """True only for a request that actually asks for a staged fight.

    ``intent`` may be a director-intent contract, a DirectorPlan, or any mapping
    carrying ``project_goal`` / ``genre`` / ``combat_mode``.  ``request`` is the
    raw user sentence.  A false positive here is fail-closed, so the rule is
    deliberately narrow: a staged-fight name, two distinct combat actions, one
    combat action beside an opponent marker, or a match-up noun corroborated by
    sustained combat / fight staging in its own clause.  A bare match-up noun
    (``拳击`` / ``对决`` / ``交手``) is not enough: products, sports match-ups,
    training demos, metaphors and documentaries mention them without asking for
    a staged fight.  A tier-1 name is not enough either when it only modifies a
    non-fight programme form ("武打片的幕后花絮" is a making-of).  The opponent
    rule also excludes weak everyday verbs such as ``闪避`` / ``反击`` unless
    paired with another action.
    """

    contract = _mapping(intent)
    declared = _text(
        contract.get("combat_mode")
        or contract.get("action_domain")
        or contract.get("genre"),
        limit=80,
    ).casefold()
    if declared in _EXPLICIT_GENRES:
        return True
    candidates = [
        request,
        contract.get("project_goal"),
        contract.get("logline"),
        contract.get("creative_subject"),
        _mapping(contract.get("director_vision")).get("project_goal"),
        _mapping(contract.get("director_vision")).get("logline"),
    ]
    for candidate in candidates:
        matched = combat_intent_matches(candidate)
        if matched["explicit"]:
            return True
        if matched["sustained"]:
            return True
        if not _training_context(_text(candidate, limit=4000)):
            if len(matched["action"]) >= 2:
                return True
            if matched["opponent"] and any(
                term not in _WEAK_ACTION_TERMS for term in matched["action"]
            ):
                return True
    return False


def _requests_spoken_lines(*values: object) -> bool:
    markers = ("台词", "对白", "旁白", "配音", "开口说话", "喊话")
    for value in values:
        text = _text(value, limit=4000)
        if text and any(marker in text for marker in markers):
            return True
    return False


# --- Structure ---------------------------------------------------------------


def combat_structure_capacity(target_seconds: object) -> dict[str, Any]:
    """Report the shot-count window a target duration can actually carry.

    A 30 s target with a 4 s per-shot floor allows at most 7 shots (8 shots
    would force 3.75 s each and break the floor).  ``max_shots`` is clamped to
    the plan's own cap (:data:`MAX_SHOTS`): measured before the clamp, a 180 s
    target reported ``max_shots=45`` -- a window the structure gate itself
    rejects, so a caller following this helper was told to build 45 shots and
    then failed ``combat.structure.shot_count_above_max``.  The real ceiling is
    12 shots x 15 s = 180 s; beyond that the target is unreachable and
    ``feasible`` is False rather than silently reported as possible.
    """

    target = _number(target_seconds)
    if target is None or target <= 0:
        return {"feasible": False, "reason": "combat.structure.target_missing"}
    maximum = max(MIN_SHOTS, int(target // MIN_SHOT_SECONDS))
    minimum = max(MIN_SHOTS, -(-int(target) // int(MAX_SHOT_SECONDS)))
    capped = min(maximum, MAX_SHOTS)
    feasible = minimum <= capped
    return {
        "feasible": feasible,
        "target_seconds": round(target, 2),
        "min_shots": minimum,
        "max_shots": capped,
        "plan_cap_applied": maximum > MAX_SHOTS,
        "reason": "" if feasible else "combat.structure.target_unreachable",
    }


def _duration_issues(
    shots: list[dict[str, Any]],
    *,
    request: object,
    tolerance: float,
) -> list[dict[str, str]]:
    """Judge the package total against the seconds the request named.

    Skipped entirely when the request names no duration: *not judged*, never a
    silent pass and never a free fail.  This is the criterion whose absence was
    measured -- a request for a 30 s film produced a 2.041 s cut on the
    ``freezone-*`` chain with no gate objecting.  On ``one-click-film`` the plan
    has already been rescaled onto the requested total, so a mismatch there
    means the rescale did not happen, which is itself worth reporting.
    """

    requested = requested_duration_seconds(request)
    if requested is None:
        return []
    total = sum(
        float(seconds)
        for seconds in (_shot_seconds(shot) for shot in shots)
        if seconds is not None
    )
    if abs(total - requested) <= tolerance:
        return []
    return [
        _issue(
            "combat.structure.total_duration_mismatch",
            f"镜头总时长 {total:g}s 与请求点名的 {requested:g}s 相差超过 {tolerance:g}s",
            fix="按目标总时长重新分配每镜时长，或改掉请求里的时长。",
        )
    ]


def audit_combat_structure(
    shots: object,
    *,
    request: object = "",
    tolerance: float = DURATION_TOLERANCE_SECONDS,
) -> dict[str, Any]:
    """Audit shot count, per-shot duration windows, and named total duration."""

    items = [dict(shot) for shot in _list(shots) if isinstance(shot, Mapping)]
    ignored = len(_list(shots)) - len(items)
    issues: list[dict[str, str]] = []
    durations: list[dict[str, Any]] = []
    if ignored > 0:
        # A dropped entry is evidence that was not judged; silently judging only
        # the parseable shots is the green-light-without-evidence pattern this
        # module exists to remove.
        issues.append(
            _issue(
                "combat.structure.shot_unreadable",
                f"有 {ignored} 个镜头条目不是对象，没有参与审计",
                fix="把每个镜头写成对象；无法解析的条目不能算通过。",
            )
        )
    if len(items) < MIN_SHOTS:
        issues.append(
            _issue(
                "combat.structure.shot_count_below_min",
                f"打斗片至少需要 {MIN_SHOTS} 个镜头，当前 {len(items)} 个",
                fix="把因果链拆成至少两次可见接触，而不是把整场对打塞进一镜。",
            )
        )
    if len(items) > MAX_SHOTS:
        issues.append(
            _issue(
                "combat.structure.shot_count_above_max",
                f"打斗片镜头数不能超过 {MAX_SHOTS}，当前 {len(items)} 个",
                fix="合并重复的攻防来回，保留因果链上的必要节点。",
            )
        )
    for index, shot in enumerate(items, 1):
        identifier = _shot_id(shot, index)
        seconds = _shot_seconds(shot)
        if seconds is None:
            issues.append(
                _issue(
                    "combat.structure.duration_missing",
                    "镜头缺少 duration_seconds",
                    shot_id=identifier,
                    fix="写入该镜的可见时长，单位秒。",
                )
            )
            durations.append({"shot_id": identifier, "duration_seconds": None})
            continue
        durations.append({"shot_id": identifier, "duration_seconds": seconds})
        if seconds < MIN_SHOT_SECONDS:
            issues.append(
                _issue(
                    "combat.structure.shot_too_short",
                    f"镜头 {seconds:g}s 短于 {MIN_SHOT_SECONDS:g}s，装不下一段完整因果",
                    shot_id=identifier,
                    fix="把该镜并入相邻的因果段，或延长到模型可生成的时长区间。",
                )
            )
        if seconds > MAX_SHOT_SECONDS:
            issues.append(
                _issue(
                    "combat.structure.shot_too_long",
                    f"镜头 {seconds:g}s 超过 {MAX_SHOT_SECONDS:g}s，单一主动作链会被稀释",
                    shot_id=identifier,
                    fix="在动作节拍处切开，拆成两个镜头。",
                )
            )
    total_seconds = round(
        sum(float(item["duration_seconds"]) for item in durations if item["duration_seconds"]),
        2,
    )
    issues.extend(_duration_issues(items, request=request, tolerance=tolerance))
    return {
        "schema": "combat_film_structure_audit.v1",
        "passed": not issues,
        "issues": issues,
        "shot_count": len(items),
        "unreadable_shot_count": max(0, ignored),
        "total_seconds": total_seconds,
        "requested_seconds": requested_duration_seconds(request),
        "duration_tolerance_seconds": tolerance,
        "durations": durations,
        "window": [
            {"shot_id": item["shot_id"], "duration_seconds": item["duration_seconds"]}
            for item in durations
        ],
    }


# --- Declarations ------------------------------------------------------------


def _issue(code: str, message: str, *, shot_id: str = "", fix: str = "") -> dict[str, str]:
    return {
        "code": code,
        "message": message,
        **({"shot_id": shot_id} if shot_id else {}),
        **({"fix": fix} if fix else {}),
    }


def combat_declaration(shot: Mapping[str, Any]) -> dict[str, Any]:
    """Read ``shots[i]['cinematic']['combat']`` without inventing a carrier."""

    cinematic = shot.get("cinematic")
    if not isinstance(cinematic, Mapping):
        return {}
    combat = cinematic.get("combat")
    return deepcopy(dict(combat)) if isinstance(combat, Mapping) else {}


def _declared_lines(shot: Mapping[str, Any]) -> list[str]:
    combat = combat_declaration(shot)
    return [
        _text(item, limit=200)
        for item in _list(combat.get("dialogue_lines"))
        if isinstance(item, str) and _text(item, limit=200)
    ]


def audit_combat_dialogue_declarations(shots: object) -> dict[str, Any]:
    """Judge the *declared* lines with the existing dialogue-sound contract.

    ``audit_dialogue_sound_contract`` returns ``turns=[]`` and ``passed=True``
    when no shot carries a top-level ``dialogue`` field -- a hollow green light
    on this chain.  Feeding it the declared lines makes the check real: the
    same text hygiene, per-line duration budget, and silence policy that the
    freezone chain enforces.  It still judges declarations, not delivery: the
    seven-step chain has no carrier that puts a line into the video model.
    """

    items = [dict(shot) for shot in _list(shots) if isinstance(shot, Mapping)]
    if not any(_declared_lines(shot) for shot in items):
        return {"passed": True, "issues": [], "applied": False, "reason": "no_declared_lines"}
    materialized = [
        {
            "shot_id": _shot_id(shot, index),
            "duration_seconds": _shot_seconds(shot),
            "dialogue": _declared_lines(shot),
            "sound": _mapping(_mapping(shot.get("cinematic")).get("sound")),
        }
        for index, shot in enumerate(items, 1)
    ]
    contract = build_dialogue_sound_contract(shots=materialized)
    report = audit_dialogue_sound_contract(contract)
    return {
        "passed": bool(report.get("passed")),
        "issues": [dict(item) for item in _list(report.get("issues")) if isinstance(item, Mapping)],
        "applied": True,
        "reason": "",
    }


def audit_combat_damage_continuity(shots: object) -> dict[str, Any]:
    """Audit damage state across adjacent shots.

    Reads ``cinematic.combat.damage_state`` on purpose.  ``continuity_in`` /
    ``continuity_out`` cannot be used: the continuity compiler can copy the
    previous shot's ``continuity_out`` into the next shot's ``continuity_in``,
    so a comparison over that carrier would compare a value with itself and
    always pass.
    """

    items = [dict(shot) for shot in _list(shots) if isinstance(shot, Mapping)]
    issues: list[dict[str, str]] = []
    states: list[dict[str, Any]] = []
    if not items:
        # Measured before this guard: ``audit_combat_damage_continuity([])``
        # returned ``passed=True``, contradicting the module's own policy that
        # missing evidence is never a pass.
        issues.append(
            _issue(
                "combat.damage.evidence_missing",
                "没有任何镜头可供战损连续性审计",
                fix="先提供镜头表；空输入不是通过。",
            )
        )
    for index, shot in enumerate(items, 1):
        identifier = _shot_id(shot, index)
        raw = combat_declaration(shot).get("damage_state")
        state = _text(raw, limit=40)
        states.append({"shot_id": identifier, "damage_state": state or None})
        if state not in DAMAGE_STATES:
            issues.append(
                _issue(
                    "combat.damage.state_invalid",
                    f"战损状态 {state or '（缺失）'} 不在 {'/'.join(DAMAGE_STATES)} 内",
                    shot_id=identifier,
                    fix="按 intact→minor→severe→down 的顺序声明该镜结束时的战损。",
                )
            )
    for index in range(1, len(states)):
        previous = states[index - 1]["damage_state"]
        current = states[index]["damage_state"]
        if previous not in DAMAGE_STATES or current not in DAMAGE_STATES:
            continue
        if DAMAGE_STATES.index(current) < DAMAGE_STATES.index(previous):
            issues.append(
                _issue(
                    "combat.damage.regressed",
                    f"战损从 {previous} 退回 {current}，前一镜的破坏被复原",
                    shot_id=states[index]["shot_id"],
                    fix="战损只能保持或加重；复原必须作为显式剧情事件另写合同。",
                )
            )
    return {
        "schema": "combat_film_damage_audit.v1",
        "passed": not issues,
        "issues": issues,
        "states": states,
    }


def _claimed_families(
    shot: Mapping[str, Any],
    *,
    director_vision: Mapping[str, Any] | None = None,
    project_dna: Mapping[str, Any] | None = None,
) -> set[str]:
    """Families the shared cinematic audit actually claims for one shot.

    ``audit_cinematic_contracts`` judges a family applicable by *known field
    names* and leaves ``gate_observations[gate]`` at ``None`` when no field it
    recognises is present, so a non-empty placeholder object normalizes to
    ``{}`` and is never claimed.  Run per shot rather than once over the whole
    list: applicability there is per-shot, and one shot declaring a family must
    not make another shot's placeholder look declared.
    """

    observations = (
        audit_cinematic_contracts(
            [shot],
            director_vision=director_vision,
            project_dna=project_dna,
        ).get("gate_observations")
        or {}
    )
    return {
        family
        for family, gate in CINEMATIC_FAMILY_GATES
        if observations.get(gate) is not None
    }


def audit_combat_cinematic_consistency(
    shots: object,
    *,
    director_vision: Mapping[str, Any] | None = None,
    project_dna: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Require the five cinematic families, then reuse the canonical audit.

    ``audit_cinematic_contracts`` treats an undeclared family as *not
    applicable* and returns ``passed=True`` with ``applicable_gates=[]``.
    Presence is therefore checked first: without it the shared audit is a
    silent green light.  An empty shot list is reported as missing evidence:
    measured before this guard, ``audit_combat_cinematic_consistency([])``
    returned ``passed=True``.

    A family counts as declared only when both hold: the shot carries a
    non-empty block for it *and* the shared audit claims the family (its gate
    observation is not ``None``).  Measured gap this closes: four families
    filled with an arbitrary single key (``{"placeholder": True}``,
    ``{"status": "todo"}``, ``{"tbd": 1}``, ``{"note": "占位"}``) plus a
    placeholder sound string passed the declarations gate with ``issues=0``.
    """

    items = [dict(shot) for shot in _list(shots) if isinstance(shot, Mapping)]
    issues: list[dict[str, str]] = []
    if not items:
        issues.append(
            _issue(
                "combat.cinematic.evidence_missing",
                "没有任何镜头可供跨镜电影声明审计",
                fix="先提供镜头表；空输入不是通过。",
            )
        )
    for index, shot in enumerate(items, 1):
        identifier = _shot_id(shot, index)
        cinematic = shot.get("cinematic")
        block = dict(cinematic) if isinstance(cinematic, Mapping) else {}
        claimed = _claimed_families(
            shot,
            director_vision=director_vision,
            project_dna=project_dna,
        )
        missing = [
            family
            for family in CINEMATIC_FAMILIES
            if not _mapping(block.get(family)) or family not in claimed
        ]
        if missing:
            issues.append(
                _issue(
                    "combat.cinematic.family_missing",
                    "镜头缺少电影声明族：" + "、".join(missing),
                    shot_id=identifier,
                    fix="补齐 lighting/color_look/screen_direction/edit/sound 五族声明。",
                )
            )
    audit: dict[str, Any] = {"passed": False, "issues": [], "gate_observations": {}}
    if items:
        audit = audit_cinematic_contracts(
            items,
            director_vision=director_vision,
            project_dna=project_dna,
        )
        for gate, value in (audit.get("gate_observations") or {}).items():
            if value is None:
                continue
            if value is False:
                issues.append(
                    _issue(
                        "combat.cinematic.gate_failed",
                        f"电影声明族 gate 未通过：{gate}",
                        fix="按 cinematic_contract 的 issue 逐条修正跨镜声明。",
                    )
                )
    issues.extend(
        dict(item)
        for item in _list(audit.get("issues"))
        if isinstance(item, Mapping)
    )
    return {
        "schema": "combat_film_cinematic_audit.v1",
        "passed": not issues,
        "issues": issues,
        "cinematic": {
            "passed": audit.get("passed"),
            "applicable_gates": list(audit.get("applicable_gates") or []),
            "failed_gates": list(audit.get("failed_gates") or []),
        },
    }


def audit_combat_declarations(
    shots: object,
    *,
    director_vision: Mapping[str, Any] | None = None,
    project_dna: Mapping[str, Any] | None = None,
    require_dialogue: bool = False,
) -> dict[str, Any]:
    """Audit every per-shot combat declaration and its cross-shot agreement."""

    items = [dict(shot) for shot in _list(shots) if isinstance(shot, Mapping)]
    issues: list[dict[str, str]] = []
    if len(items) < MIN_SHOTS:
        issues.append(
            _issue(
                "combat.declarations.cross_shot_evidence_missing",
                f"跨镜声明至少需要 {MIN_SHOTS} 个镜头才能比较，当前 {len(items)} 个",
                fix="补齐镜头后再谈人数、战损与因果链的一致性。",
            )
        )
    declared_pairs: list[frozenset[str]] = []
    dialogue_lines = 0
    for index, shot in enumerate(items, 1):
        identifier = _shot_id(shot, index)
        combat = combat_declaration(shot)
        if not combat:
            issues.append(
                _issue(
                    "combat.declarations.missing",
                    "镜头没有 cinematic.combat 打斗声明",
                    shot_id=identifier,
                    fix="声明 duel_scope/combatant_count/combatant_ids/causality_chain/damage_state。",
                )
            )
            continue
        duel_scope = _text(combat.get("duel_scope"), limit=40)
        if duel_scope != REQUIRED_DUEL_SCOPE:
            issues.append(
                _issue(
                    "combat.declarations.duel_scope_invalid",
                    f"duel_scope={duel_scope or '（缺失）'}，1v1 打斗必须声明 {REQUIRED_DUEL_SCOPE}",
                    shot_id=identifier,
                    fix="改成一镜对一人的拍法；一对多必须另立合同。",
                )
            )
        count = combat.get("combatant_count")
        count_value = count if isinstance(count, int) and not isinstance(count, bool) else None
        if count_value != REQUIRED_COMBATANT_COUNT:
            issues.append(
                _issue(
                    "combat.declarations.combatant_count_invalid",
                    f"combatant_count={count!r}，1v1 必须为 {REQUIRED_COMBATANT_COUNT}",
                    shot_id=identifier,
                    fix="只保留两名在场战斗者，其余角色退出画面。",
                )
            )
        pair = {
            _text(item, limit=120)
            for item in _list(combat.get("combatant_ids"))
            if _text(item, limit=120)
        }
        if len(pair) != REQUIRED_COMBATANT_COUNT:
            issues.append(
                _issue(
                    "combat.declarations.combatant_ids_invalid",
                    "combatant_ids 必须是两名互不相同的战斗者",
                    shot_id=identifier,
                    fix="用稳定的资产 ID 点名双方，逐镜点名可防止身份漂移。",
                )
            )
        else:
            declared_pairs.append(frozenset(pair))
        chain = [
            _text(item, limit=40)
            for item in _list(combat.get("causality_chain"))
            if _text(item, limit=40)
        ]
        if chain != list(CAUSALITY_CHAIN):
            issues.append(
                _issue(
                    "combat.declarations.causality_chain_invalid",
                    "causality_chain 必须是 "
                    + "→".join(CAUSALITY_CHAIN)
                    + f"，当前 {'→'.join(chain) or '（缺失）'}",
                    shot_id=identifier,
                    fix="按接触→身体压缩→功能失效→恢复失败声明本镜承担的因果段。",
                )
            )
        lines = [
            _text(item, limit=200)
            for item in _list(combat.get("dialogue_lines"))
            if isinstance(item, str) and _text(item, limit=200)
        ]
        empty_slots = len(_list(combat.get("dialogue_lines"))) - len(lines)
        if empty_slots > 0:
            issues.append(
                _issue(
                    "combat.dialogue.empty_line",
                    f"有 {empty_slots} 个台词槽位是空的",
                    shot_id=identifier,
                    fix="删除空槽位，或补上必须逐字说出的原文。",
                )
            )
        dialogue_lines += len(lines)
    if len(declared_pairs) > 1 and len(set(declared_pairs)) > 1:
        issues.append(
            _issue(
                "combat.declarations.combatant_identity_drift",
                "跨镜战斗者身份不一致："
                + " / ".join("、".join(sorted(pair)) for pair in declared_pairs),
                fix="锁定双方资产 ID；换人等于换了一场决斗。",
            )
        )
    damage = audit_combat_damage_continuity(items)
    issues.extend(damage["issues"])
    cinematic = audit_combat_cinematic_consistency(
        items,
        director_vision=director_vision,
        project_dna=project_dna,
    )
    issues.extend(item for item in cinematic["issues"] if isinstance(item, Mapping))
    dialogue = audit_combat_dialogue_declarations(items)
    issues.extend(
        dict(item) for item in dialogue["issues"] if isinstance(item, Mapping)
    )
    if require_dialogue and dialogue_lines == 0:
        issues.append(
            _issue(
                "combat.dialogue.missing",
                "请求里点了台词，但没有一个镜头声明任何台词",
                fix="在需要说话的镜头上声明 cinematic.combat.dialogue_lines。",
            )
        )
    return {
        "schema": "combat_film_declaration_audit.v1",
        "passed": not issues,
        "issues": issues,
        "shot_count": len(items),
        "dialogue_line_count": dialogue_lines,
        "require_dialogue": bool(require_dialogue),
        "damage": damage,
        "cinematic": cinematic,
        "dialogue": dialogue,
    }


def _gate_ready(observations: Mapping[str, Any], gates: tuple[str, ...]) -> bool:
    """Fail-closed, matching film_production_contract's ``_gate_ready``."""

    return all(observations.get(gate) is True for gate in gates)


def compile_combat_film_contract(
    *,
    shots: object,
    request: object = "",
    intent: object = None,
    director_vision: Mapping[str, Any] | None = None,
    project_dna: Mapping[str, Any] | None = None,
    require_dialogue: object = None,
) -> dict[str, Any]:
    """Aggregate the two combat gates, fail-closed.

    Missing evidence is recorded as ``False``, never ``None``: an absent
    declaration is a failed declaration.  When the request is not a combat
    request and no shot declares combat, ``required_gates`` stays empty so
    unrelated runs are not judged by this contract.
    """

    items = [dict(shot) for shot in _list(shots) if isinstance(shot, Mapping)]
    contract = _mapping(intent)
    declared_combat = any(combat_declaration(shot) for shot in items)
    matched = combat_intent_matches(request)
    requested = is_combat_film_request(contract, request)
    applies = bool(requested or declared_combat)
    evidence = {
        "requested": requested,
        "declared_in_shots": declared_combat,
        "matched_terms": matched,
        "request_terms": (
            list(matched["explicit"]) + list(matched["strong"]) + list(matched["action"])
        ),
    }
    if not applies:
        return {
            "schema": COMBAT_FILM_SCHEMA,
            "applies": False,
            "gate_observations": {},
            "required_gates": [],
            "issues": [],
            "evidence": evidence,
            "policy": _policy(),
        }
    dialogue_required = (
        bool(require_dialogue)
        if require_dialogue is not None
        else _requests_spoken_lines(request, contract.get("project_goal"))
    )
    structure = audit_combat_structure(items, request=request)
    declarations = audit_combat_declarations(
        items,
        director_vision=director_vision,
        project_dna=project_dna,
        require_dialogue=dialogue_required,
    )
    observations: dict[str, bool] = {
        COMBAT_FILM_STRUCTURE_GATE: bool(structure["passed"]),
        COMBAT_FILM_DECLARATIONS_GATE: bool(declarations["passed"]),
    }
    issues = [*structure["issues"], *declarations["issues"]]
    # Both gates are always requested once the contract applies.  A single-shot
    # stage is *not* deferred: "a 30 s 1v1 fight told in one shot" is a real
    # defect (``combat.structure.shot_count_below_min``), not a physically
    # impossible predicate -- the author can add shots.  Hiding it would be the
    # same silent green light this contract exists to remove.
    return {
        "schema": COMBAT_FILM_SCHEMA,
        "applies": True,
        "gate_observations": observations,
        "required_gates": list(COMBAT_FILM_GATES),
        "ready_for_media": _gate_ready(observations, COMBAT_FILM_GATES),
        "issues": issues,
        "structure": structure,
        "declarations": declarations,
        "evidence": evidence,
        "policy": _policy(),
    }


def _policy() -> dict[str, Any]:
    return {
        "missing_evidence_is_not_passed": True,
        "judges_declarations_not_pixels": True,
        "dialogue_is_declaration_only": True,
        "does_not_gate_paid_media": True,
        "total_duration_judged_when_request_names_one": True,
        "total_duration_grounded_on_the_freezone_chain": True,
    }


def combat_film_quantity_gates() -> tuple[str, ...]:
    """Canonical gate names, readable without importing this module eagerly."""

    return COMBAT_FILM_GATES


__all__ = [
    "CAUSALITY_CHAIN",
    "CINEMATIC_FAMILIES",
    "CINEMATIC_FAMILY_GATES",
    "COMBAT_FILM_DECLARATIONS_GATE",
    "COMBAT_FILM_GATES",
    "COMBAT_FILM_SCHEMA",
    "COMBAT_FILM_STRUCTURE_GATE",
    "DAMAGE_STATES",
    "DURATION_TOLERANCE_SECONDS",
    "MAX_SHOTS",
    "MAX_SHOT_SECONDS",
    "MIN_SHOTS",
    "MIN_SHOT_SECONDS",
    "REQUIRED_COMBATANT_COUNT",
    "REQUIRED_DUEL_SCOPE",
    "audit_combat_cinematic_consistency",
    "audit_combat_damage_continuity",
    "audit_combat_declarations",
    "audit_combat_dialogue_declarations",
    "audit_combat_structure",
    "combat_declaration",
    "combat_film_quantity_gates",
    "combat_intent_matches",
    "combat_structure_capacity",
    "compile_combat_film_contract",
    "is_combat_film_request",
    "requested_duration_seconds",
]
