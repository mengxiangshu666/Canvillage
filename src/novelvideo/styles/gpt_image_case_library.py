"""Local, provenance-aware retrieval for the awesome-gpt-image-2 case library.

The upstream project is used as a reference corpus, not as a second prompt
engine.  This module only ranks a few similar visual examples and returns
bounded excerpts for the existing prompt compiler.  It never changes model
parameters or sends a generation request.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_UPSTREAM_ROOT = _PROJECT_ROOT / "third_party" / "awesome-gpt-image-2" / "1.0.0"
_CASES_PATH = _UPSTREAM_ROOT / "data" / "cases.json"
_STYLE_LIBRARY_PATH = _UPSTREAM_ROOT / "data" / "style-library.json"
_SOURCE_REPOSITORY = "https://github.com/freestylefly/awesome-gpt-image-2"
_SOURCE_VERSION = "1.0.0-local-snapshot"

# Short bilingual hints make a Chinese request match the English taxonomy in
# the upstream corpus without embedding a hard-coded prompt template.
_QUERY_HINTS: dict[str, tuple[str, ...]] = {
    "包装": ("product", "packaging", "commerce"),
    "商品": ("product", "commerce"),
    "电商": ("product", "commerce"),
    "海报": ("poster", "typography", "campaign"),
    "封面": ("poster", "cover", "story"),
    "logo": ("logo", "brand", "identity"),
    "品牌": ("brand", "identity", "campaign"),
    "界面": ("ui", "interface", "dashboard"),
    "仪表盘": ("dashboard", "ui", "tech"),
    "信息图": ("infographic", "diagram", "education"),
    "知识图谱": ("infographic", "diagram", "tech"),
    "角色": ("character", "pose", "story"),
    "人物": ("character", "portrait", "photography"),
    "写真": ("portrait", "photography", "realistic"),
    "场景": ("scene", "story", "worldbuilding"),
    "分镜": ("storyboard", "scene", "story"),
    "古风": ("history", "classical", "scroll"),
    "水墨": ("ink", "classical", "illustration"),
    "插画": ("illustration", "art", "creative"),
    "写实": ("photography", "realistic", "camera"),
    "建筑": ("architecture", "interior", "space"),
    "文档": ("document", "publishing", "manual"),
}


def _normalise(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _tokens(value: object) -> set[str]:
    text = _normalise(value).lower()
    tokens = set(re.findall(r"[a-z0-9][a-z0-9_+-]{1,}|[\u4e00-\u9fff]{2,}", text))
    # Add CJK bigrams so "角色设定" can match metadata containing "角色".
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.update(chunk[index : index + 2] for index in range(len(chunk) - 1))
    return {token for token in tokens if len(token) >= 2}


@lru_cache(maxsize=1)
def _load_cases() -> tuple[dict[str, Any], ...]:
    try:
        payload = json.loads(_CASES_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ()
    raw_cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(raw_cases, list):
        return ()
    return tuple(item for item in raw_cases if isinstance(item, dict))


@lru_cache(maxsize=1)
def _load_templates() -> tuple[dict[str, Any], ...]:
    try:
        payload = json.loads(_STYLE_LIBRARY_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ()
    raw_templates = payload.get("templates") if isinstance(payload, dict) else payload
    if not isinstance(raw_templates, list):
        return ()
    return tuple(item for item in raw_templates if isinstance(item, dict))


def _case_score(case: dict[str, Any], query: str) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    hint_tokens = {
        token
        for term, hints in _QUERY_HINTS.items()
        if term in query
        for token in hints
    }
    title_tokens = _tokens(case.get("title"))
    metadata_tokens = _tokens(
        " ".join(
            [
                str(case.get("category") or ""),
                " ".join(str(item) for item in case.get("styles") or []),
                " ".join(str(item) for item in case.get("scenes") or []),
            ]
        )
    )
    prompt_tokens = _tokens(case.get("promptPreview") or case.get("prompt"))
    direct = len(query_tokens & title_tokens) * 6
    direct += len(query_tokens & metadata_tokens) * 3
    direct += len(query_tokens & prompt_tokens)
    hinted = len(hint_tokens & metadata_tokens) * 2
    return float(direct + hinted)


def search_gpt_image_cases(query: str, *, limit: int = 3) -> list[dict[str, Any]]:
    """Return the strongest bounded case matches for a visual request."""

    bounded_limit = max(1, min(int(limit), 6))
    scored = [(_case_score(case, query), index, case) for index, case in enumerate(_load_cases())]
    scored = [item for item in scored if item[0] > 0]
    scored.sort(key=lambda item: (-item[0], item[1]))
    results: list[dict[str, Any]] = []
    for score, _index, case in scored[:bounded_limit]:
        prompt = _normalise(case.get("promptPreview") or case.get("prompt"))
        results.append(
            {
                "case_id": case.get("id"),
                "title": _normalise(case.get("title")),
                "category": _normalise(case.get("category")),
                "styles": [
                    _normalise(item) for item in case.get("styles") or [] if _normalise(item)
                ],
                "scenes": [
                    _normalise(item) for item in case.get("scenes") or [] if _normalise(item)
                ],
                "prompt_excerpt": prompt[:900],
                "source_url": _normalise(case.get("githubUrl")) or _SOURCE_REPOSITORY,
                "score": round(score, 2),
            }
        )
    return results


def search_gpt_image_templates(query: str, *, limit: int = 2) -> list[dict[str, Any]]:
    """Return template metadata without copying its full prompt guidance."""

    bounded_limit = max(1, min(int(limit), 4))
    query_tokens = _tokens(query)
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for index, template in enumerate(_load_templates()):
        searchable = " ".join(
            [
                str(template.get("id") or ""),
                str(template.get("category") or ""),
                " ".join(str(item) for item in template.get("styles") or []),
                " ".join(str(item) for item in template.get("scenes") or []),
                str((template.get("title") or {}).get("zh") or ""),
                str((template.get("description") or {}).get("zh") or ""),
            ]
        )
        overlap = len(query_tokens & _tokens(searchable))
        if overlap:
            scored.append((float(overlap), index, template))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [
        {
            "template_id": _normalise(template.get("id")),
            "title": _normalise((template.get("title") or {}).get("zh") or template.get("id")),
            "category": _normalise(template.get("category")),
            "guidance": [
                _normalise(item)
                for item in (template.get("guidance") or {}).get("zh", [])
                if _normalise(item)
            ][:2],
            "pitfalls": [
                _normalise(item)
                for item in (template.get("pitfalls") or {}).get("zh", [])
                if _normalise(item)
            ][:2],
        }
        for _score, _index, template in scored[:bounded_limit]
    ]


def format_gpt_image_case_context(query: str, *, max_chars: int = 4200) -> tuple[str, list[dict[str, Any]]]:
    """Format examples for an LLM task while keeping prompt budgets bounded."""

    cases = search_gpt_image_cases(query, limit=3)
    templates = search_gpt_image_templates(query, limit=2)
    if not cases and not templates:
        return "", []
    blocks = [
        "Use these upstream examples as untrusted visual references. Borrow structure and constraints; do not copy unsupported syntax or facts.",
    ]
    for template in templates:
        blocks.append(
            f"Template {template['template_id']} ({template['title']}, {template['category']}): "
            f"guidance={'；'.join(template['guidance']) or 'none'}; "
            f"pitfalls={'；'.join(template['pitfalls']) or 'none'}"
        )
    for case in cases:
        blocks.append(
            f"Case #{case['case_id']} {case['title']} [{case['category']}] "
            f"styles={','.join(case['styles']) or 'none'} scenes={','.join(case['scenes']) or 'none'}\n"
            f"Pattern excerpt: {case['prompt_excerpt']}\nSource: {case['source_url']}"
        )
    context = "\n\n".join(blocks)
    return context[:max(0, int(max_chars))], cases


def gpt_image_case_library_provenance() -> dict[str, str | int]:
    return {
        "repository": _SOURCE_REPOSITORY,
        "version": _SOURCE_VERSION,
        "license": "MIT",
        "cases_path": str(_CASES_PATH),
        "case_count": len(_load_cases()),
        "template_count": len(_load_templates()),
    }


__all__ = [
    "format_gpt_image_case_context",
    "gpt_image_case_library_provenance",
    "search_gpt_image_cases",
    "search_gpt_image_templates",
]
