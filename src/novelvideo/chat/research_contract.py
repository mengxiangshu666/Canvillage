"""Small, explicit contracts for project-scoped web research.

The web provider remains a transport detail.  This module describes *why* a
query was issued and whether the returned evidence is sufficient for the
requested mode.  It is deliberately pure so it can be used by the API route,
the capability broker, and replay/evaluation fixtures without network calls.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit


RESEARCH_CONTRACT_SCHEMA = "research.contract.v1"
RESEARCH_ASSESSMENT_SCHEMA = "research.assessment.v1"
RESEARCH_MODES = frozenset({"quick", "standard", "deep"})
COUNTER_SEARCH_MODES = frozenset({"off", "one_round", "two_rounds"})


@dataclass(frozen=True, slots=True)
class ResearchContract:
    """Normalized, serializable policy for one research request."""

    query: str
    mode: str = "standard"
    counter_search: str = "off"
    min_sources: int = 2
    independent_domains_required: int = 2
    citation_required: bool = True

    @property
    def query_roles(self) -> tuple[str, ...]:
        roles = ["primary"]
        if self.counter_search in {"one_round", "two_rounds"}:
            roles.append("risk_check")
        if self.counter_search == "two_rounds":
            roles.append("reverse_check")
        return tuple(roles)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RESEARCH_CONTRACT_SCHEMA,
            "query": self.query,
            "mode": self.mode,
            "counter_search": self.counter_search,
            "min_sources": self.min_sources,
            "independent_domains_required": self.independent_domains_required,
            "citation_required": self.citation_required,
            "query_roles": list(self.query_roles),
        }


def _clean(value: object, limit: int = 4_000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def normalize_research_contract(
    query: object,
    *,
    mode: object = "standard",
    counter_search: object = "off",
    min_sources: object = None,
    independent_domains_required: object = None,
    citation_required: object = True,
) -> ResearchContract:
    """Normalize untrusted API/tool fields without guessing creative intent."""

    clean_query = _clean(query)
    if not clean_query:
        raise ValueError("query is required")
    clean_mode = str(mode or "standard").strip().lower()
    if clean_mode not in RESEARCH_MODES:
        clean_mode = "standard"
    clean_counter = str(counter_search or "off").strip().lower()
    if clean_counter not in COUNTER_SEARCH_MODES:
        clean_counter = "off"

    default_sources = {"quick": 1, "standard": 2, "deep": 4}[clean_mode]
    default_domains = {"quick": 1, "standard": 2, "deep": 3}[clean_mode]
    sources = _bounded_int(min_sources, default_sources, 1, 8)
    domains = _bounded_int(
        independent_domains_required,
        default_domains,
        1,
        8,
    )
    # A quick lookup is explicitly allowed to use one source.  Other modes
    # retain the independent-domain gate unless the caller lowers it.
    if clean_mode == "quick" and min_sources is None:
        sources = 1
    if clean_mode == "quick" and independent_domains_required is None:
        domains = 1
    return ResearchContract(
        query=clean_query,
        mode=clean_mode,
        counter_search=clean_counter,
        min_sources=sources,
        independent_domains_required=domains,
        citation_required=bool(citation_required),
    )


def build_counter_queries(contract: ResearchContract) -> list[tuple[str, str]]:
    """Build bounded counter-search prompts with stable roles.

    These are search prompts, not instructions for the model.  Keeping them
    in the research layer avoids embedding a brittle keyword policy in the
    Agent's system prompt.
    """

    query = contract.query
    if contract.counter_search == "off":
        return []
    # The bilingual suffixes keep the query useful for Chinese user requests
    # while still retrieving English primary documentation when available.
    risk = f"{query} 失败 风险 局限性 问题 坑 counterevidence"
    result = [("risk_check", risk)]
    if contract.counter_search == "two_rounds":
        result.append(
            (
                "reverse_check",
                f"为什么 {query} 可能不成立 反例 反驳 何时不适用 limitations",
            )
        )
    return result


def build_research_plan(
    query: object,
    *,
    mode: object = "standard",
    counter_search: object = None,
    counter_queries: Iterable[object] = (),
    min_sources: object = None,
    independent_domains_required: object = None,
    citation_required: object = True,
) -> dict[str, Any]:
    """Compile the bounded query plan sent to the provider.

    ``counter_search=None`` intentionally selects a mode default: standard
    and deep research run both a risk and a reverse query, while quick mode
    remains one inexpensive lookup.
    """

    clean_mode = str(mode or "standard").strip().lower()
    default_counter = {"quick": "off", "standard": "two_rounds", "deep": "two_rounds"}.get(
        clean_mode,
        "two_rounds",
    )
    contract = normalize_research_contract(
        query,
        mode=clean_mode,
        counter_search=default_counter if counter_search is None else counter_search,
        min_sources=min_sources,
        independent_domains_required=independent_domains_required,
        citation_required=citation_required,
    )
    rounds: list[dict[str, str]] = [
        {"role": "primary", "query": contract.query},
    ]
    rounds.extend(
        {"role": role, "query": text}
        for role, text in build_counter_queries(contract)
    )
    for value in counter_queries:
        custom = _clean(value, 2_000)
        if custom and custom not in {item["query"] for item in rounds} and len(rounds) < 5:
            rounds.append({"role": "custom_counter", "query": custom})
    return {
        **contract.to_dict(),
        "rounds": rounds,
        "recommended_search_depth": {"quick": "basic", "standard": "basic", "deep": "advanced"}[contract.mode],
        "recommended_include_raw_content": contract.mode == "deep",
        "recommended_results_per_round": max(2, min(8, (8 + len(rounds) - 1) // len(rounds))),
    }


def merge_research_results(
    plan: Mapping[str, Any],
    round_results: Iterable[Mapping[str, Any]],
    *,
    max_results: int = 8,
) -> dict[str, Any]:
    """Merge provider rounds while retaining the query provenance of each hit."""

    merged: dict[str, dict[str, Any]] = {}
    rounds: list[dict[str, Any]] = []
    primary_answer = ""
    all_cached = True
    for raw_round in round_results:
        role = _clean(raw_round.get("query_role") or "primary", 40)
        query = _clean(raw_round.get("query") or "", 2_000)
        error = _clean(raw_round.get("error") or "", 500)
        result = raw_round.get("result")
        if not isinstance(result, Mapping):
            all_cached = False
            rounds.append({"role": role, "query": query, "ok": False, "error": error or "no result"})
            continue
        ok = result.get("ok") is not False
        all_cached = all_cached and bool(result.get("cached"))
        if role == "primary" and _clean(result.get("answer"), 4_000):
            primary_answer = _clean(result.get("answer"), 4_000)
        rounds.append(
            {
                "role": role,
                "query": query,
                "ok": ok,
                "cached": bool(result.get("cached")),
                "result_count": int(result.get("result_count") or len(result.get("results") or [])),
                **({"error": error} if error else {}),
            }
        )
        if not ok:
            all_cached = False
            continue
        for item in result.get("results") or []:
            if not isinstance(item, Mapping):
                continue
            projected = dict(item)
            projected["query_role"] = role
            projected["query_variant"] = query
            url = _clean(projected.get("url") or projected.get("uri"), 2_000).casefold()
            title = _clean(projected.get("title"), 300).casefold()
            content = _clean(projected.get("content") or projected.get("raw_content"), 600).casefold()
            fingerprint = url or f"{title}:{hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]}"
            current = merged.get(fingerprint)
            if current is None:
                projected["query_roles"] = [role]
                merged[fingerprint] = projected
                continue
            roles = list(current.get("query_roles") or [])
            if role not in roles:
                roles.append(role)
            current["query_roles"] = roles[:8]
            # Keep the first (usually primary) URL/content, but retain all
            # query roles so the evidence assessor sees the cross-check.
    rows = list(merged.values())
    rows.sort(
        key=lambda item: (
            -float(item.get("score") or 0.0),
            0 if str(item.get("query_role") or "") == "primary" else 1,
            str(item.get("url") or item.get("title") or ""),
        )
    )
    bounded = max(1, min(int(max_results or 8), 20))
    errors = {item["role"]: item["error"] for item in rounds if item.get("error")}
    return {
        "ok": bool(rows) or any(item.get("ok") for item in rounds),
        "query": _clean(plan.get("query"), 2_000),
        "answer": primary_answer or None,
        "results": rows[:bounded],
        "result_count": min(len(rows), bounded),
        "rounds": rounds,
        "round_errors": errors,
        "cached": bool(all_cached and rounds),
        "scope": {},
        "research_contract": dict(plan),
    }


def _domain(value: object) -> str:
    raw = _clean(value, 2_000)
    if not raw:
        return ""
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def _item_role(item: Mapping[str, Any]) -> str:
    return _clean(item.get("query_role") or "primary", 40).lower()


def _item_roles(item: Mapping[str, Any]) -> list[str]:
    values = item.get("query_roles")
    roles = [
        _clean(value, 40).lower()
        for value in (values if isinstance(values, (list, tuple, set)) else [_item_role(item)])
        if _clean(value, 40)
    ]
    return list(dict.fromkeys(roles)) or ["primary"]


def assess_research_evidence(
    items: Iterable[object],
    contract: ResearchContract,
    *,
    conflicts: Iterable[object] = (),
    source_errors: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Assess source sufficiency without judging the truth of a claim."""

    normalized = [item for item in items if isinstance(item, Mapping)]
    domains = {
        domain
        for item in normalized
        for domain in (_domain(item.get("url") or item.get("uri") or item.get("citation")),)
        if domain
    }
    roles = Counter(role for item in normalized for role in _item_roles(item))
    cited_count = sum(
        1
        for item in normalized
        if _clean(item.get("url") or item.get("uri") or item.get("citation"), 2_000)
    )
    conflict_count = len([item for item in conflicts if item])
    errors = {str(key): _clean(value, 500) for key, value in (source_errors or {}).items() if _clean(value, 500)}
    expected_roles = {
        str(item).strip()
        for item in (contract.query_roles or ())
        if str(item).strip()
    }
    checked_roles = set(roles)
    has_counter = bool((expected_roles - {"primary"}) & checked_roles) or any(
        role != "primary" for role in checked_roles
    )
    missing: list[str] = []
    if len(normalized) < contract.min_sources:
        missing.append("minimum_sources")
    if len(domains) < contract.independent_domains_required:
        missing.append("independent_domains")
    if contract.citation_required and cited_count < len(normalized):
        missing.append("citations")
    if contract.counter_search != "off" and not has_counter:
        missing.append("counter_search")
    if conflict_count:
        missing.append("unresolved_conflicts")
    if errors:
        missing.append("source_errors")

    if not normalized:
        quality = "insufficient"
        confidence = "極低"
    elif missing:
        quality = "degraded"
        confidence = "低"
    elif contract.mode == "quick":
        quality = "adequate"
        confidence = "中"
    elif contract.mode == "standard":
        quality = "strong"
        confidence = "中"
    else:
        quality = "strong"
        confidence = "高"
    return {
        "schema": RESEARCH_ASSESSMENT_SCHEMA,
        "mode": contract.mode,
        "min_sources": contract.min_sources,
        "independent_domains_required": contract.independent_domains_required,
        "citation_required": contract.citation_required,
        "quality": quality,
        "confidence": confidence,
        "sufficient": not missing,
        "missing_checks": missing,
        "result_count": len(normalized),
        "cited_result_count": cited_count,
        "independent_domain_count": len(domains),
        "independent_domains": sorted(domains)[:16],
        "query_roles": dict(sorted(roles.items())),
        "expected_query_roles": sorted(expected_roles),
        "counter_search_checked": has_counter,
        "conflict_count": conflict_count,
        "source_errors": errors,
        "next_action": "answer_with_citations" if not missing else "report_evidence_gaps",
    }


__all__ = [
    "COUNTER_SEARCH_MODES",
    "RESEARCH_ASSESSMENT_SCHEMA",
    "RESEARCH_CONTRACT_SCHEMA",
    "RESEARCH_MODES",
    "ResearchContract",
    "assess_research_evidence",
    "build_counter_queries",
    "build_research_plan",
    "merge_research_results",
    "normalize_research_contract",
]
