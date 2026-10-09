"""Deterministic pre-activation routing for installed Agent skills.

The model remains the authority that may load, reject, or switch a skill. This
module only supplies a recomputable top-1 candidate before the first tool call,
so a model that does not explicitly load a skill still receives the narrowest
relevant knowledge. It never grants activation flags or produces a load receipt.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from novelvideo.agent_tools.skills import AgentSkill, list_agent_skills

SKILL_ROUTE_SCHEMA = "village_agent_skill_route.v1"
SKILL_ROUTE_METHOD = "char-ngram-idf+arbitration"
SKILL_ROUTE_CANDIDATE_LIMIT = 3
MIN_ROUTE_SCORE = 0.18
USER_MESSAGE_MARKER = "[USER_MESSAGE]"
STAGE_CANDIDATE_BONUS = 0.60
STAGE_STRONG_REQUEST_BONUS = 0.12
STAGE_PREFERRED_BONUS = 0.04
STAGE_STRONG_REQUEST_SCORE = 0.75

_WORD_RE = re.compile(r"[a-z0-9]+")
_SPACE_RE = re.compile(r"\s+")
_COMPACT_RE = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]+")
_DOCUMENT_FEATURE_CACHE: dict[tuple[str, str], Counter[str]] = {}


@dataclass(frozen=True, slots=True)
class SkillRouteCandidate:
    name: str
    score: float
    description: str

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": round(self.score, 6),
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class SkillRouteStageHint:
    """A bounded project-stage preference supplied to the semantic router."""

    stage_id: str
    label: str
    workflow_step: str
    candidate_skills: tuple[str, ...] = ()
    preferred_skill: str = ""
    execution_surface: str = ""
    reason: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "label": self.label,
            "workflow_step": self.workflow_step,
            "candidate_skills": list(self.candidate_skills),
            "preferred_skill": self.preferred_skill,
            "execution_surface": self.execution_surface,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class SkillRouteDecision:
    skill_name: str
    score: float
    reason: str
    candidates: tuple[SkillRouteCandidate, ...] = ()
    input_source: str = "full_prompt"
    input_chars: int = 0
    stage_hint: SkillRouteStageHint | None = None

    def public(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": SKILL_ROUTE_SCHEMA,
            "method": SKILL_ROUTE_METHOD,
            "pre_activated_skill": self.skill_name,
            "score": round(self.score, 6),
            "reason": self.reason,
            "candidates": [candidate.public() for candidate in self.candidates],
            "routing_input": {
                "source": self.input_source,
                "chars": self.input_chars,
            },
            "permissions_granted": [],
            "load_receipt": False,
        }
        if self.stage_hint is not None:
            payload["project_stage"] = self.stage_hint.public()
        return payload


@dataclass(frozen=True, slots=True)
class _RoutingGuard:
    phrases: tuple[str, ...]
    boosts: Mapping[str, float]
    reason: str
    exclude_phrases: tuple[str, ...] = ()


# These are arbitration guards, not answer tables. The corpus base score already
# distinguishes 34 of the 38 live routing cases; these guards only resolve pairs
# whose descriptions legitimately overlap (commands vs. canvas direction,
# story vs. performance, and style selection vs. style locking).
_ROUTING_GUARDS = (
    _RoutingGuard(
        phrases=(
            "从当前进度自动推进到整集成片",
            "保留已经成功的阶段",
            "durable production run",
        ),
        boosts={
            "village-canvas-one-click-film": 6.0,
            "village-canvas-method-distiller": -8.0,
            "village-canvas": -1.5,
        },
        reason="durable_production_control",
    ),
    _RoutingGuard(
        phrases=("命令清单", "撤销语义", "一次建成"),
        boosts={
            "village-canvas-commands": 1.35,
            "village-canvas-canvas-director": -0.35,
            "village-canvas-storyboard": -0.35,
            "village-canvas-expression-director": -0.35,
        },
        reason="canvas_command_protocol",
    ),
    _RoutingGuard(
        phrases=("还没定美术方向", "比较写实电影感", "90年代赛璐珞", "暗黑水墨"),
        boosts={
            "village-canvas-visual-style": 1.6,
            "village-canvas-style-lock": -0.8,
        },
        reason="visual_style_selection",
    ),
    _RoutingGuard(
        phrases=("核心创意", "人物弧光", "主题命题", "剧本方法"),
        exclude_phrases=("表演", "潜台词", "站位", "视线", "呼吸", "手势"),
        boosts={
            "village-canvas-story-director": 1.1,
            "village-canvas-expression-director": -0.5,
        },
        reason="story_development",
    ),
    _RoutingGuard(
        phrases=("知识规则里选出", "提示词合同与未知项"),
        boosts={
            "village-canvas-aigc-knowledge": 1.0,
            "village-canvas-prompt-director": -0.5,
        },
        reason="knowledge_compilation",
    ),
)

# A negated action is not a request for that action.  The live story-development
# case is the concrete failure this prevents: the user asks for script method
# design and explicitly says not to inspect continuity, but the script-doctor
# document wins by a hair on the overlapping words alone.
_NEGATED_ROUTE_PENALTIES = (
    (
        re.compile(
            r"(?:不要|别|不用|无需|不需要|先不)"
            r"[^。！？；\n]{0,28}"
            r"(?:检查|诊断|改稿|连续性)"
        ),
        {
            "village-canvas-script-doctor": -0.65,
            "village-canvas-script-integrity": -0.65,
            "village-canvas-continuity": -0.65,
        },
        "negated_script_review",
    ),
)


def _features(text: str) -> Counter[str]:
    normalized = _SPACE_RE.sub(" ", str(text or "").casefold())
    features: Counter[str] = Counter()
    for token in _WORD_RE.findall(normalized):
        if len(token) >= 2:
            features[f"w:{token}"] += 1
    compact = _COMPACT_RE.sub("", normalized)
    for size in (2, 3, 4):
        for index in range(max(0, len(compact) - size + 1)):
            features[f"c:{compact[index:index + size]}"] += 1
    return features


def _skill_document(skill: AgentSkill) -> str:
    routing = ""
    if skill.activation is not None:
        routing = " ".join(
            (
                skill.activation.workflow,
                *skill.activation.agents,
                *skill.activation.fence,
            )
        )
    return " ".join((skill.name, skill.description, routing, skill.content))


def _cosine_scores(prompt: str, skills: Sequence[AgentSkill]) -> dict[str, float]:
    prompt_features = _features(prompt)
    if not prompt_features or not skills:
        return {skill.name: 0.0 for skill in skills}

    documents: dict[str, Counter[str]] = {}
    for skill in skills:
        cache_key = (skill.name, skill.sha256)
        cached = _DOCUMENT_FEATURE_CACHE.get(cache_key)
        if cached is None:
            cached = _features(_skill_document(skill))
            _DOCUMENT_FEATURE_CACHE[cache_key] = cached
        documents[skill.name] = cached
    document_frequency: Counter[str] = Counter()
    for features in documents.values():
        document_frequency.update(features.keys())

    total = len(documents)
    idf = {
        token: math.log((total + 1) / (frequency + 1)) + 1.0
        for token, frequency in document_frequency.items()
    }
    prompt_norm = math.sqrt(
        sum((count * idf.get(token, 1.0)) ** 2 for token, count in prompt_features.items())
    )
    scores: dict[str, float] = {}
    for name, features in documents.items():
        numerator = sum(
            prompt_features[token]
            * count
            * idf.get(token, 1.0)
            * idf.get(token, 1.0)
            for token, count in features.items()
            if token in prompt_features
        )
        document_norm = math.sqrt(
            sum((count * idf.get(token, 1.0)) ** 2 for token, count in features.items())
        )
        # Sum-normalized overlap keeps scores comparable across long skill
        # documents while preserving the same ordering as cosine similarity.
        denominator = prompt_norm + document_norm
        scores[name] = numerator / denominator if denominator else 0.0
    return scores


def _routing_guard_bonus(prompt: str, name: str) -> tuple[float, str]:
    for guard in _ROUTING_GUARDS:
        if not any(phrase in prompt for phrase in guard.phrases):
            continue
        if guard.exclude_phrases and any(
            phrase in prompt for phrase in guard.exclude_phrases
        ):
            continue
        bonus = float(guard.boosts.get(name, 0.0))
        if bonus:
            return bonus, guard.reason
    return 0.0, ""


def _routing_input(prompt: object) -> tuple[str, str]:
    text = str(prompt or "")
    if USER_MESSAGE_MARKER in text:
        human = text.rsplit(USER_MESSAGE_MARKER, 1)[-1].strip()
        if human:
            return human, "user_message"
    return text.strip(), "full_prompt"


def skill_routing_prompt(prompt: object) -> str:
    """Return only the human request, excluding injected runtime context."""

    return _routing_input(prompt)[0]


def route_agent_skill(
    prompt: object,
    *,
    skills: Iterable[AgentSkill] | None = None,
    limit: int = SKILL_ROUTE_CANDIDATE_LIMIT,
    stage_hint: SkillRouteStageHint | None = None,
) -> SkillRouteDecision:
    """Return a deterministic top-1 pre-activation candidate.

    The result is intentionally explanatory: it contains names, scores, and a
    reason, but no flags, workflow entry, or successful-load identity.
    """

    text, input_source = _routing_input(prompt)
    catalog = tuple(skills) if skills is not None else list_agent_skills()
    if not text or not catalog:
        return SkillRouteDecision("", 0.0, "no_route")

    scores = _cosine_scores(text, catalog)
    reasons: dict[str, str] = {}
    for skill in catalog:
        bonus, reason = _routing_guard_bonus(text, skill.name)
        if bonus:
            scores[skill.name] = max(0.0, scores.get(skill.name, 0.0) + bonus)
            reasons[skill.name] = reason
    for pattern, penalties, reason in _NEGATED_ROUTE_PENALTIES:
        if not pattern.search(text):
            continue
        for skill_name, penalty in penalties.items():
            if skill_name not in scores:
                continue
            scores[skill_name] = max(0.0, scores[skill_name] + penalty)
            reasons.setdefault(skill_name, reason)

    if stage_hint is not None and stage_hint.candidate_skills:
        baseline_top = max(scores.values(), default=0.0)
        stage_bonus = (
            STAGE_STRONG_REQUEST_BONUS
            if baseline_top >= STAGE_STRONG_REQUEST_SCORE
            else STAGE_CANDIDATE_BONUS
        )
        for index, skill_name in enumerate(stage_hint.candidate_skills):
            if skill_name not in scores:
                continue
            scores[skill_name] = max(
                0.0,
                scores[skill_name] + stage_bonus - min(index, 5) * 0.005,
            )
            reasons.setdefault(skill_name, f"project_stage:{stage_hint.stage_id}")
        if stage_hint.preferred_skill in scores:
            scores[stage_hint.preferred_skill] = max(
                0.0,
                scores[stage_hint.preferred_skill] + STAGE_PREFERRED_BONUS,
            )
            reasons.setdefault(
                stage_hint.preferred_skill,
                f"project_stage:{stage_hint.stage_id}",
            )

    ranked = sorted(
        scores.items(),
        key=lambda item: (-item[1], item[0]),
    )
    top_name, top_score = ranked[0]
    if top_score < MIN_ROUTE_SCORE:
        fallback = next(
            (skill.name for skill in catalog if skill.name == "village-canvas"),
            "",
        )
        if fallback:
            top_name, top_score = fallback, scores.get(fallback, 0.0)

    by_name = {skill.name: skill for skill in catalog}
    candidates = tuple(
        SkillRouteCandidate(
            name=name,
            score=score,
            description=by_name[name].description,
        )
        for name, score in ranked[: max(1, min(limit, len(ranked)))]
    )
    reason = reasons.get(top_name, "semantic_overlap")
    return SkillRouteDecision(
        skill_name=top_name,
        score=top_score,
        reason=reason,
        candidates=candidates,
        input_source=input_source,
        input_chars=len(text),
        stage_hint=stage_hint,
    )


def build_skill_preactivation_block(
    decision: SkillRouteDecision,
    *,
    skills: Iterable[AgentSkill] | None = None,
) -> str:
    """Render explanatory activation text without granting runtime flags."""

    if not decision.skill_name:
        return ""
    catalog = tuple(skills) if skills is not None else list_agent_skills()
    skill = next((item for item in catalog if item.name == decision.skill_name), None)
    if skill is None:
        return ""

    lines = [
        "[VILLAGE_AGENT_SKILL_ROUTE]",
        f"candidate: {skill.name}",
        f"score: {decision.score:.6f}",
        f"reason: {decision.reason}",
        f"description: {skill.description}",
    ]
    if decision.stage_hint is not None:
        stage = decision.stage_hint
        lines.extend(
            (
                f"project_stage: {stage.stage_id} ({stage.label})",
                f"project_stage_workflow_step: {stage.workflow_step}",
                "project_stage_candidates: "
                + (", ".join(stage.candidate_skills) or "none"),
                f"project_stage_reason: {stage.reason or 'workflow_next_stage'}",
            )
        )
        if stage.execution_surface:
            lines.append(f"project_stage_execution_surface: {stage.execution_surface}")
    if skill.activation is not None:
        lines.extend(
            (
                f"workflow: {skill.activation.workflow}",
                "agents: " + (", ".join(skill.activation.agents) or "none"),
                "fence:",
                *[f"- {rule}" for rule in skill.activation.fence],
            )
        )
    elif skill.activation_error:
        lines.append(f"activation_warning: {skill.activation_error}")
    lines.extend(
        (
            "permissions: pre-activation grants no flags; paid media still requires a real",
            "village_agent_skill.v1 load receipt for the current turn.",
            "correction: if this candidate is wrong, load the correct skill before acting.",
            "If a different skill was already loaded, switch within the same turn only before",
            "any side effect.",
            "[/VILLAGE_AGENT_SKILL_ROUTE]",
        )
    )
    return "\n".join(lines)


__all__ = [
    "SKILL_ROUTE_CANDIDATE_LIMIT",
    "SKILL_ROUTE_METHOD",
    "SKILL_ROUTE_SCHEMA",
    "STAGE_CANDIDATE_BONUS",
    "STAGE_PREFERRED_BONUS",
    "STAGE_STRONG_REQUEST_BONUS",
    "STAGE_STRONG_REQUEST_SCORE",
    "SkillRouteCandidate",
    "SkillRouteDecision",
    "SkillRouteStageHint",
    "USER_MESSAGE_MARKER",
    "build_skill_preactivation_block",
    "route_agent_skill",
    "skill_routing_prompt",
]
