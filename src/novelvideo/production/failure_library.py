"""Queryable failure library shared by the canvas, workflows and the Agent.

The library is data, not prose: every case answers the same seven questions
(symptom, aliases, root cause, threshold, prescription, disproved fixes and
evidence).  Search is deliberately lexical and deterministic; a missing match
is reported as a miss instead of being filled with a plausible-sounding answer.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

FAILURE_LIBRARY_SCHEMA = "village.failure-library.v1"
FAILURE_LIBRARY_VERSION = 1
_DATA_PATH = Path(__file__).with_name("failure_cases.json")

_REQUIRED_FIELDS = (
    "id",
    "stage",
    "symptom",
    "severity",
    "threshold",
    "cause",
    "rules",
    "fix",
    "avoid",
    "evidence",
)
_SEVERITY_ORDER = {"致命": 0, "严重": 1, "一般": 2}
_NORMALIZE_RE = re.compile(r"[\s,，。;；!?！？、：:\"'“”‘’()（）【】\[\]<>《》]+")


class FailureLibraryError(ValueError):
    """The bundled failure library is missing or structurally invalid."""


def _text(value: object, *, limit: int = 4000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _normalize(value: object) -> str:
    return _NORMALIZE_RE.sub("", str(value or "").casefold())


def _path_key(path: Path | str | None) -> str:
    return str(path or _DATA_PATH)


@lru_cache(maxsize=4)
def _load_payload_cached(path_key: str) -> dict[str, Any]:
    path = Path(path_key)
    if not path.is_file():
        raise FailureLibraryError(f"failure library not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FailureLibraryError(f"failure library is unreadable: {exc}") from exc
    return validate_failure_library(payload)


def validate_failure_library(payload: object) -> dict[str, Any]:
    """Validate the complete library once and return a detached copy."""

    if not isinstance(payload, Mapping):
        raise FailureLibraryError("failure library must be an object")
    data = json.loads(json.dumps(payload, ensure_ascii=False))
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise FailureLibraryError("failure library has no cases")
    stages = data.get("stages")
    if not isinstance(stages, Mapping) or not stages:
        raise FailureLibraryError("failure library has no stage map")
    seen: set[str] = set()
    for position, raw in enumerate(cases, start=1):
        if not isinstance(raw, Mapping):
            raise FailureLibraryError(f"case #{position} is not an object")
        for field in _REQUIRED_FIELDS:
            if field not in raw:
                raise FailureLibraryError(f"case #{position} is missing {field}")
        case_id = _text(raw.get("id"), limit=80)
        if not case_id:
            raise FailureLibraryError(f"case #{position} has no id")
        if case_id in seen:
            raise FailureLibraryError(f"duplicate case id: {case_id}")
        seen.add(case_id)
        if _text(raw.get("stage")) not in stages:
            raise FailureLibraryError(
                f"case {case_id} uses unknown stage: {raw.get('stage')}"
            )
        if not isinstance(raw.get("rules"), list) or not isinstance(raw.get("fix"), list):
            raise FailureLibraryError(f"case {case_id} rules/fix must be lists")
    data["schema"] = FAILURE_LIBRARY_SCHEMA
    data["version"] = FAILURE_LIBRARY_VERSION
    data["count"] = len(cases)
    return data


def load_failure_library(path: Path | str | None = None) -> dict[str, Any]:
    """Load and validate the library; callers may pass a test fixture path."""

    return _load_payload_cached(_path_key(path))


def list_failure_cases(
    *,
    stage: str | None = None,
    severity: str | None = None,
    limit: int | None = None,
    path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Return cases in stable severity/id order with optional filters."""

    payload = load_failure_library(path)
    stage_key = _text(stage)
    severity_key = _text(severity)
    cases = [
        case
        for case in payload["cases"]
        if (not stage_key or case.get("stage") == stage_key)
        and (not severity_key or case.get("severity") == severity_key)
    ]
    cases.sort(
        key=lambda case: (
            _SEVERITY_ORDER.get(_text(case.get("severity")), 99),
            _text(case.get("id")),
        )
    )
    if limit is not None and limit >= 0:
        cases = cases[:limit]
    return [json.loads(json.dumps(case, ensure_ascii=False)) for case in cases]


def get_failure_case(case_id: str, *, path: Path | str | None = None) -> dict[str, Any] | None:
    wanted = _text(case_id).upper()
    if not wanted:
        return None
    for case in load_failure_library(path)["cases"]:
        if _text(case.get("id")).upper() == wanted:
            return json.loads(json.dumps(case, ensure_ascii=False))
    return None


def _match_fraction(needle: str, haystack: str) -> float:
    """Return how much of a short query appears in a field.

    Full substring matches are exact; otherwise Chinese queries are compared as
    two-character chunks so ``接缝瞬移`` still matches a symptom that says
    ``接缝处像瞬移``.  A single shared character never counts.
    """

    if not needle or not haystack:
        return 0.0
    if needle in haystack:
        return 1.0
    if len(needle) < 2:
        return 0.0
    chunks = [needle[index : index + 2] for index in range(len(needle) - 1)]
    hits = sum(1 for chunk in chunks if chunk in haystack)
    return hits / len(chunks) if chunks else 0.0


def _case_score(case: Mapping[str, Any], query: str) -> tuple[int, list[str]]:
    needle = _normalize(query)
    if not needle:
        return 0, []
    matched: list[str] = []
    score = 0
    if needle == _normalize(case.get("id")):
        score += 100
        matched.append("id")
    fields = (
        ("symptom", 12),
        ("also", 10),
        ("rules", 7),
        ("cause", 4),
        ("threshold", 3),
        ("fix", 2),
        ("avoid", 1),
        ("evidence", 1),
    )
    for field, weight in fields:
        value = case.get(field)
        if isinstance(value, list):
            haystack = " ".join(_normalize(item) for item in value)
        else:
            haystack = _normalize(value)
        fraction = _match_fraction(needle, haystack)
        if fraction >= 0.5:
            score += max(1, int(round(weight * fraction)))
            matched.append(field)
    return score, matched


def search_failure_cases(
    query: str,
    *,
    stage: str | None = None,
    severity: str | None = None,
    limit: int = 10,
    path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Search by plain-language symptom, rule id or stage label.

    Results include a deterministic ``score`` and ``matched_fields``; an empty
    list means the library has no matching evidence and the caller must say so.
    """

    stage_key = _text(stage)
    severity_key = _text(severity)
    scored: list[tuple[int, list[str], dict[str, Any]]] = []
    for case in load_failure_library(path)["cases"]:
        if stage_key and case.get("stage") != stage_key:
            continue
        if severity_key and case.get("severity") != severity_key:
            continue
        score, matched = _case_score(case, query)
        if score <= 0:
            continue
        item = json.loads(json.dumps(case, ensure_ascii=False))
        item["score"] = score
        item["matched_fields"] = matched
        scored.append((score, matched, item))
    scored.sort(
        key=lambda entry: (
            -entry[0],
            _SEVERITY_ORDER.get(_text(entry[2].get("severity")), 99),
            _text(entry[2].get("id")),
        )
    )
    return [entry[2] for entry in scored[: max(0, limit)]]


def failure_stages(*, path: Path | str | None = None) -> dict[str, str]:
    return dict(load_failure_library(path).get("stages") or {})


def failure_rules(*, path: Path | str | None = None) -> dict[str, list[str]]:
    """Map every hard rule to the sample ids that support it."""

    rule_map: dict[str, list[str]] = {}
    for case in load_failure_library(path)["cases"]:
        for rule in case.get("rules") or []:
            key = _text(rule)
            if key:
                rule_map.setdefault(key, []).append(_text(case.get("id")))
    return {key: sorted(values) for key, values in sorted(rule_map.items())}


def failure_decision_trees(*, path: Path | str | None = None) -> list[dict[str, Any]]:
    return json.loads(
        json.dumps(
            load_failure_library(path).get("global_decision_trees") or [],
            ensure_ascii=False,
        )
    )


def failure_library_summary(*, path: Path | str | None = None) -> dict[str, Any]:
    payload = load_failure_library(path)
    counts: dict[str, int] = {}
    severity: dict[str, int] = {}
    for case in payload["cases"]:
        stage = _text(case.get("stage"))
        counts[stage] = counts.get(stage, 0) + 1
        level = _text(case.get("severity"))
        severity[level] = severity.get(level, 0) + 1
    return {
        "schema": FAILURE_LIBRARY_SCHEMA,
        "version": FAILURE_LIBRARY_VERSION,
        "count": payload["count"],
        "product_case_count": int(payload.get("product_cases") or 0),
        "stages": counts,
        "severities": severity,
        "rule_count": len(failure_rules(path=path)),
        "decision_tree_count": len(failure_decision_trees(path=path)),
        "imported_at": _text(payload.get("imported_at")),
        "source_package": _text(payload.get("source_package")),
    }


__all__ = [
    "FAILURE_LIBRARY_SCHEMA",
    "FAILURE_LIBRARY_VERSION",
    "FailureLibraryError",
    "failure_decision_trees",
    "failure_library_summary",
    "failure_rules",
    "failure_stages",
    "get_failure_case",
    "list_failure_cases",
    "load_failure_library",
    "search_failure_cases",
    "validate_failure_library",
]
