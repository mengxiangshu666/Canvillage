"""Deterministic story contract for films and long-form episodic production.

The director model may invent prose, but long-form quality depends on facts
that can be checked between episodes: what changed, what the audience still
wants to know, who knows what, and how the next episode starts.  This module
owns that bounded contract without replacing the existing episode planner.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any


STORY_CONTRACT_SCHEMA = "series_story_contract.v1"
STORY_AUDIT_SCHEMA = "series_story_audit.v1"
STORY_REVISION_PREFIX = "series-story.v1:"

HookKind = str
_REAL_HOOK_KINDS = {"real", "true", "payoff", "cliffhanger"}
_FAKE_HOOK_MARKERS = ("突然", "没想到", "竟然", "震惊", "神秘人出现")


def _text(value: object, *, limit: int = 4000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _list(value: object, *, limit: int = 100, item_limit: int = 800) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item, limit=item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _canonical_payload(value: Mapping[str, Any]) -> str:
    return json.dumps(
        {key: value[key] for key in sorted(value) if key != "contract_revision"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_story_contract_revision(value: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_payload(value).encode("utf-8")).hexdigest()[:20]
    return f"{STORY_REVISION_PREFIX}{digest}"


def _knowledge(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[dict[str, Any]] = []
    for raw in value[:200]:
        if not isinstance(raw, Mapping):
            continue
        item = deepcopy(dict(raw))
        subject = _text(
            item.get("subject") or item.get("character") or item.get("id"),
            limit=160,
        )
        if not subject:
            continue
        result.append(
            {
                "subject": subject,
                "knows": _list(item.get("knows"), limit=30),
                "believes": _list(item.get("believes"), limit=30),
                "does_not_know": _list(
                    item.get("does_not_know") or item.get("not_knows"),
                    limit=30,
                ),
                "episode_index": _positive_int(item.get("episode_index"), default=0),
            }
        )
    return result


def _positive_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(parsed, 100_000))


def _episode(value: object, index: int) -> dict[str, Any]:
    raw = _mapping(value)
    hook = _mapping(raw.get("hook") or raw.get("ending_hook") or raw.get("cliffhanger"))
    hook_text = _text(hook.get("text") or hook.get("question") or raw.get("hook"))
    hook_kind = _text(hook.get("kind") or hook.get("type"), limit=40).casefold()
    if not hook_kind:
        hook_kind = "real"
    return {
        "episode_index": _positive_int(raw.get("episode_index") or raw.get("episode"), default=index),
        "title": _text(raw.get("title"), limit=300),
        "dramatic_question": _text(
            raw.get("dramatic_question") or raw.get("episode_question"),
            limit=1000,
        ),
        "entering_state": _text(raw.get("entering_state") or raw.get("enter_state"), limit=1200),
        "main_conflict": _text(raw.get("main_conflict") or raw.get("conflict"), limit=1200),
        "turning_point": _text(raw.get("turning_point") or raw.get("turn"), limit=1200),
        "exit_state": _text(raw.get("exit_state") or raw.get("leaving_state"), limit=1200),
        "state_change": _text(raw.get("state_change") or raw.get("change"), limit=1200),
        "hook": {
            "text": hook_text,
            "kind": hook_kind,
            "consequence": _text(
                hook.get("consequence") or raw.get("hook_consequence"),
                limit=800,
            ),
            "answer_episode": _positive_int(
                hook.get("answer_episode") or raw.get("hook_answer_episode"),
                default=0,
            ),
        },
        "information": _knowledge(raw.get("information") or raw.get("information_permissions")),
        "setup": _list(raw.get("setup"), limit=30),
        "payoff": _list(raw.get("payoff"), limit=30),
        "next_episode_handoff": _text(
            raw.get("next_episode_handoff") or raw.get("handoff"),
            limit=1200,
        ),
    }


def build_series_story_contract(
    *,
    series_id: object,
    title: object = "",
    format_kind: object = "series",
    theme: object = "",
    dramatic_promise: object = "",
    conflict_engine: object = "",
    episodes: object = None,
) -> dict[str, Any]:
    """Compile the story facts that must survive beyond one episode."""

    normalized_id = _text(series_id, limit=160)
    if not normalized_id:
        raise ValueError("series story contract requires series_id")
    raw_episodes = episodes if isinstance(episodes, (list, tuple)) else []
    normalized_episodes = [
        _episode(item, index)
        for index, item in enumerate(raw_episodes, start=1)
    ]
    result: dict[str, Any] = {
        "schema": STORY_CONTRACT_SCHEMA,
        "series_id": normalized_id,
        "title": _text(title, limit=300),
        "format_kind": _text(format_kind, limit=80) or "series",
        "theme": _text(theme, limit=1000),
        "dramatic_promise": _text(dramatic_promise, limit=1200),
        "conflict_engine": _text(conflict_engine, limit=1200),
        "episodes": normalized_episodes,
    }
    result["contract_revision"] = compute_story_contract_revision(result)
    return result


def validate_series_story_contract(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("series_story_contract must be an object")
    contract = deepcopy(dict(value))
    if contract.get("schema") != STORY_CONTRACT_SCHEMA:
        raise ValueError("series_story_contract schema is unsupported")
    if not _text(contract.get("series_id")):
        raise ValueError("series_story_contract series_id is required")
    episodes = contract.get("episodes")
    if not isinstance(episodes, list):
        raise ValueError("series_story_contract episodes must be a list")
    seen: set[int] = set()
    for episode in episodes:
        if not isinstance(episode, Mapping):
            raise ValueError("series_story_contract episode must be an object")
        index = episode.get("episode_index")
        if not isinstance(index, int) or index < 1 or index in seen:
            raise ValueError("series_story_contract episode_index is invalid")
        seen.add(index)
    expected = compute_story_contract_revision(contract)
    if _text(contract.get("contract_revision"), limit=100) != expected:
        raise ValueError("series_story_contract contract_revision does not match its contents")
    return contract


def _issue(
    code: str,
    message: str,
    *,
    episode_index: int = 0,
    fix: str = "",
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        **({"episode_index": episode_index} if episode_index else {}),
        **({"fix": fix} if fix else {}),
    }


def _same_state(left: object, right: object) -> bool:
    left_text = _text(left).casefold()
    right_text = _text(right).casefold()
    return bool(left_text and right_text and left_text == right_text)


def audit_series_story_contract(value: object) -> dict[str, Any]:
    """Check long-form causality without claiming unstated creative facts."""

    try:
        contract = validate_series_story_contract(value)
    except ValueError as exc:
        return {
            "schema": STORY_AUDIT_SCHEMA,
            "passed": False,
            "issues": [_issue("story.contract_invalid", str(exc))],
            "gate_observations": {"series_story_contract_valid": False},
        }

    issues: list[dict[str, Any]] = []
    if not _text(contract.get("dramatic_promise")):
        issues.append(
            _issue(
                "story.promise_missing",
                "系列缺少戏剧承诺，观众不知道这部作品长期要兑现什么",
                fix="写清主角要付出什么代价、追求什么结果，以及失败会发生什么。",
            )
        )
    if not _text(contract.get("conflict_engine")):
        issues.append(
            _issue(
                "story.conflict_engine_missing",
                "系列缺少持续制造冲突的引擎",
                fix="明确外部阻力、内部缺陷和关系压力如何反复升级。",
            )
        )

    episodes = [
        dict(item)
        for item in contract.get("episodes", [])
        if isinstance(item, Mapping)
    ]
    for position, episode in enumerate(episodes):
        index = _positive_int(episode.get("episode_index"), default=position + 1)
        required = (
            ("dramatic_question", "集级戏剧问题"),
            ("entering_state", "进入状态"),
            ("main_conflict", "主要冲突"),
            ("turning_point", "转折点"),
            ("exit_state", "退出状态"),
        )
        for field, label in required:
            if not _text(episode.get(field)):
                issues.append(
                    _issue(
                        f"story.episode.{field}_missing",
                        f"第 {index} 集缺少{label}",
                        episode_index=index,
                        fix=f"补写 {field}，并确保它是可见、可演、可验证的事实。",
                    )
                )
        if _same_state(episode.get("entering_state"), episode.get("exit_state")):
            issues.append(
                _issue(
                    "story.episode.state_unchanged",
                    f"第 {index} 集进入状态与退出状态相同，整集没有完成状态变换",
                    episode_index=index,
                    fix="让关键关系、目标、资源、认知或风险至少一项发生不可逆变化。",
                )
            )
        hook = _mapping(episode.get("hook"))
        hook_text = _text(hook.get("text"))
        hook_kind = _text(hook.get("kind")).casefold()
        if not hook_text:
            issues.append(
                _issue(
                    "story.episode.hook_missing",
                    f"第 {index} 集结尾没有可追踪的钩子",
                    episode_index=index,
                    fix="结尾必须留下一个具体未决问题，并说明它造成的后果。",
                )
            )
        elif hook_kind not in _REAL_HOOK_KINDS and not _text(hook.get("consequence")):
            issues.append(
                _issue(
                    "story.episode.fake_hook",
                    f"第 {index} 集钩子更像悬念词，没有已经发生的代价",
                    episode_index=index,
                    fix="先让角色为此付出可见代价，再留下下一集必须回答的问题。",
                )
            )
        if _text(hook.get("text")) and any(
            marker in _text(hook.get("text")) for marker in _FAKE_HOOK_MARKERS
        ) and not _text(hook.get("consequence")):
            issues.append(
                _issue(
                    "story.episode.hook_effect_without_cause",
                    f"第 {index} 集用突然事件充当钩子，但没有因果后果",
                    episode_index=index,
                    fix="把突然事件接到已有欲望、秘密或资源上，并写出它改变了什么。",
                )
            )
        if position + 1 < len(episodes):
            next_episode = episodes[position + 1]
            if _text(episode.get("exit_state")) and _text(next_episode.get("entering_state")):
                if not _same_state(episode.get("exit_state"), next_episode.get("entering_state")):
                    issues.append(
                        _issue(
                            "story.episode.handoff_mismatch",
                            f"第 {index} 集退出状态与第 {index + 1} 集进入状态不一致",
                            episode_index=index,
                            fix="只允许有动机的时间跳跃或状态变化，并写明过渡事实。",
                        )
                    )

    permissions: dict[str, set[str]] = {}
    for episode in episodes:
        index = _positive_int(episode.get("episode_index"))
        for item in _knowledge(episode.get("information")):
            subject = item["subject"].casefold()
            permissions.setdefault(subject, set()).update(
                _text(value).casefold() for value in item.get("knows", [])
            )
    for episode in episodes:
        index = _positive_int(episode.get("episode_index"))
        for item in _knowledge(episode.get("information")):
            subject = item["subject"].casefold()
            for fact in item.get("does_not_know", []):
                if _text(fact).casefold() in permissions.get(subject, set()):
                    issues.append(
                        _issue(
                            "story.information_contradiction",
                            f"{item['subject']} 在第 {index} 集被声明为不知道一个已经知道的事实",
                            episode_index=index,
                            fix="按时间顺序拆分知情状态，或补充遗忘、误信和谎言的可见原因。",
                        )
                    )

    failed = bool(issues)
    return {
        "schema": STORY_AUDIT_SCHEMA,
        "passed": not failed,
        "issues": issues,
        "episode_count": len(episodes),
        "gate_observations": {"series_story_contract_valid": not failed},
    }


__all__ = [
    "STORY_AUDIT_SCHEMA",
    "STORY_CONTRACT_SCHEMA",
    "STORY_REVISION_PREFIX",
    "audit_series_story_contract",
    "build_series_story_contract",
    "compute_story_contract_revision",
    "validate_series_story_contract",
]
