"""Small, auditable first-pass contract for a director's emotional direction.

This is a planning signal, not a creative template. Explicit user/project
values win; values inferred from the request are marked as such and remain
visible in ``clarification_needed`` until a director confirms them.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any


EMOTION_DIRECTION_SCHEMA = "director_emotion_direction.v1"
EMOTION_DIRECTION_REVISION_PREFIX = "director-emotion.v1:"

_EMOTION_LEXICON: tuple[tuple[str, str], ...] = (
    ("悬疑", "suspense"),
    ("紧张", "tension"),
    ("压迫", "oppression"),
    ("温暖", "warmth"),
    ("治愈", "healing"),
    ("浪漫", "romance"),
    ("悲伤", "grief"),
    ("孤独", "loneliness"),
    ("愤怒", "anger"),
    ("恐惧", "fear"),
    ("惊喜", "wonder"),
    ("热血", "exhilaration"),
    ("诡异", "uncanny"),
)


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _text(value: object, limit: int = 800) -> str:
    return str(value or "").strip()[:limit]


def _first(*values: object) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _first_value(*values: object) -> object:
    """Return the first non-empty value without stringifying lists."""

    for value in values:
        if isinstance(value, (list, tuple)):
            if value:
                return value
        elif _text(value):
            return value
    return ""


def _tokens(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        raw = [item for item in value]
    else:
        raw = re.split(r"[,，、;；/|\n]+", _text(value, 2_000))
    result: list[str] = []
    for item in raw:
        text = _text(item, 120)
        if text and text not in result:
            result.append(text)
    return result[:12]


def _duration(value: object, goal: str) -> tuple[float | None, str]:
    if value is not None and not isinstance(value, bool):
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        if 0 < number <= 3_600:
            return number, "explicit"
    # ``\b`` is not a usable boundary after Chinese ``秒`` because both
    # adjacent characters are Unicode word characters.  Use an ASCII-only
    # tail guard so forms like "30秒短片" and "30 seconds" both match.
    match = re.search(
        r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:秒|s|sec|seconds)(?![A-Za-z0-9_])",
        goal,
        re.IGNORECASE,
    )
    if match:
        number = float(match.group(1))
        if 0 < number <= 3_600:
            return number, "goal_inferred"
    return None, "missing"


def _revision(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        {key: value[key] for key in sorted(value) if key != "revision"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return EMOTION_DIRECTION_REVISION_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def compile_emotion_direction(
    *,
    project_goal: object,
    output_spec: object = None,
    director_directives: object = None,
    clarification_answers: object = None,
) -> dict[str, Any]:
    """Compile explicit and inferred emotional direction without side effects."""

    goal = _text(project_goal, 12_000)
    spec = _mapping(output_spec)
    directives = _mapping(director_directives)
    answers = _mapping(clarification_answers)
    nested = _mapping(spec.get("emotion_direction") or directives.get("emotion_direction"))

    explicit = _tokens(
        _first_value(
            nested.get("emotion_keywords"),
            nested.get("keywords"),
            spec.get("emotion_keywords"),
            spec.get("emotion"),
            answers.get("emotion_keywords"),
            answers.get("emotion"),
            answers.get("tone"),
        )
    )
    inferred = [english for marker, english in _EMOTION_LEXICON if marker in goal]
    keywords = explicit or inferred[:4]
    keyword_source = "explicit" if explicit else ("goal_inferred" if inferred else "missing")

    aspect_ratio = _first(
        nested.get("aspect_ratio"),
        spec.get("aspect_ratio"),
        spec.get("ratio"),
        answers.get("aspect_ratio"),
    )
    aspect_source = "explicit" if aspect_ratio else "missing"
    duration_seconds, duration_source = _duration(
        nested.get("duration_seconds") or spec.get("duration_seconds") or spec.get("duration"),
        goal,
    )
    language = _first(nested.get("language"), spec.get("language"), answers.get("language"))
    if not language:
        language = "zh-CN" if re.search(r"[\u3400-\u9fff]", goal) else ("en-US" if goal else "")
    language_source = "explicit" if any(
        _text(value) for value in (nested.get("language"), spec.get("language"), answers.get("language"))
    ) else ("goal_inferred" if language else "missing")

    primary = _first(nested.get("primary"), spec.get("primary_emotion"), keywords[0] if keywords else "")
    secondary = keywords[1:]
    clarification_needed: list[str] = []
    if not explicit:
        clarification_needed.append("emotion_keywords")
    if not aspect_ratio:
        clarification_needed.append("aspect_ratio")
    if duration_seconds is None:
        clarification_needed.append("duration_seconds")
    return {
        "schema": EMOTION_DIRECTION_SCHEMA,
        "primary": primary,
        "secondary": secondary,
        "emotion_keywords": keywords,
        "keyword_source": keyword_source,
        "aspect_ratio": aspect_ratio,
        "aspect_ratio_source": aspect_source,
        "duration_seconds": duration_seconds,
        "duration_source": duration_source,
        "language": language,
        "language_source": language_source,
        "clarification_needed": clarification_needed,
        "ready": bool(primary and aspect_ratio and duration_seconds is not None),
        "provenance": {"source": "director_request_contract", "inference_is_not_confirmation": True},
    }


def finalize_emotion_direction(value: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(value))
    result["revision"] = _revision(result)
    return result


def validate_emotion_direction(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema") != EMOTION_DIRECTION_SCHEMA:
        raise ValueError("emotion direction schema is unsupported")
    result = deepcopy(dict(value))
    required = {"primary", "secondary", "emotion_keywords", "clarification_needed", "provenance", "revision"}
    missing = sorted(required - set(result))
    if missing:
        raise ValueError("emotion direction missing fields: " + ", ".join(missing))
    if result.get("revision") != _revision(result):
        raise ValueError("emotion direction revision does not match its contents")
    return result


def build_emotion_direction(**kwargs: Any) -> dict[str, Any]:
    return finalize_emotion_direction(compile_emotion_direction(**kwargs))


__all__ = [
    "EMOTION_DIRECTION_REVISION_PREFIX",
    "EMOTION_DIRECTION_SCHEMA",
    "build_emotion_direction",
    "compile_emotion_direction",
    "finalize_emotion_direction",
    "validate_emotion_direction",
]
