"""Project-scoped creative contract: one durable answer sheet for the Agent.

The chat Agent used to re-ask the same creative questions in every new
conversation because the answers only lived inside one conversation's events.
This module keeps them next to the project instead, and derives the fields the
project already knows (locked visual style, aspect ratio) so the clarification
gate can stop interviewing and start executing.

Storage is the existing project config file, so nothing new has to be
migrated and the contract travels with the project.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from novelvideo.project_config import (
    load_project_config_file_from_state_dir,
    update_project_config_file_in_state_dir,
)


CREATIVE_CONTRACT_SCHEMA = "creative_contract.v1"
CREATIVE_CONTRACT_CONFIG_KEY = "creative_contract"

#: Contract fields in the order the gate asks about them.
CONTRACT_FIELDS = (
    "creative_subject",
    "scene_or_environment",
    "visual_style",
    "aspect_ratio",
    "characters_and_reference_assets",
    "audio",
)

#: Which contract field each project-config key can satisfy on its own.
_PROJECT_CONFIG_DEFAULTS = {
    "visual_style": "visual_style",
    "aspect_ratio": "aspect_ratio",
}

#: Operator-facing labels used when the contract is rendered into a prompt.
CONTRACT_FIELD_LABELS = {
    "creative_subject": "创作主体/事件",
    "scene_or_environment": "场景/环境",
    "visual_style": "视觉风格",
    "aspect_ratio": "画幅",
    "characters_and_reference_assets": "角色与参考素材",
    "audio": "声音",
}

CONTRACT_PROMPT_OPEN = "[CREATIVE_CONTRACT]"
CONTRACT_PROMPT_CLOSE = "[/CREATIVE_CONTRACT]"


def _text(value: object, *, limit: int = 600) -> str:
    return str(value or "").strip()[:limit]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_fields(value: object) -> dict[str, dict[str, str]]:
    fields: dict[str, dict[str, str]] = {}
    if not isinstance(value, Mapping):
        return fields
    for field in CONTRACT_FIELDS:
        entry = value.get(field)
        if not isinstance(entry, Mapping):
            continue
        text = _text(entry.get("value"))
        if not text:
            continue
        fields[field] = {
            "value": text,
            "source": _text(entry.get("source"), limit=40) or "user",
            "updated_at": _text(entry.get("updated_at"), limit=40) or _now(),
        }
    return fields


def load_creative_contract(state_dir: str | Path) -> dict[str, Any]:
    """Return the stored contract for one project (never raises on bad data)."""

    if not state_dir:
        return {"schema": CREATIVE_CONTRACT_SCHEMA, "fields": {}}
    try:
        config = load_project_config_file_from_state_dir(state_dir)
    except (OSError, ValueError):
        return {"schema": CREATIVE_CONTRACT_SCHEMA, "fields": {}}
    raw = config.get(CREATIVE_CONTRACT_CONFIG_KEY)
    if not isinstance(raw, Mapping):
        return {"schema": CREATIVE_CONTRACT_SCHEMA, "fields": {}}
    return {
        "schema": CREATIVE_CONTRACT_SCHEMA,
        "updated_at": _text(raw.get("updated_at"), limit=40) or _now(),
        "fields": _clean_fields(raw.get("fields")),
    }


def save_creative_contract(
    state_dir: str | Path,
    answers: Mapping[str, object],
    *,
    source: str = "user",
) -> dict[str, Any]:
    """Merge answered fields into the project contract and return it."""

    incoming = {
        field: _text(answers.get(field))
        for field in CONTRACT_FIELDS
        if _text(answers.get(field))
    }
    if not state_dir or not incoming:
        return load_creative_contract(state_dir)
    stamp = _now()

    def _apply(config: dict[str, Any]) -> None:
        stored = config.get(CREATIVE_CONTRACT_CONFIG_KEY)
        current = _clean_fields(
            stored.get("fields") if isinstance(stored, Mapping) else {}
        )
        for field, value in incoming.items():
            current[field] = {"value": value, "source": source, "updated_at": stamp}
        config[CREATIVE_CONTRACT_CONFIG_KEY] = {
            "schema": CREATIVE_CONTRACT_SCHEMA,
            "updated_at": stamp,
            "fields": current,
        }

    update_project_config_file_in_state_dir(state_dir, _apply)
    return load_creative_contract(state_dir)


def derive_project_contract_defaults(
    *,
    project_config: Mapping[str, Any] | None,
    canvas_nodes: list[Mapping[str, Any]] | None = None,
) -> dict[str, str]:
    """Derive the contract fields the project itself already decides.

    A stored project style or aspect ratio is an explicit operator decision, so
    it must satisfy the gate instead of being re-asked in every conversation.
    Pass the raw ``project_config.json`` contents, not the effective config: the
    effective loader injects ``script_auto``/``2:3`` defaults for every project,
    and treating those as an operator decision would silently drop the question
    from the interview.
    """

    config = project_config if isinstance(project_config, Mapping) else {}
    defaults: dict[str, str] = {}
    for field, config_key in _PROJECT_CONFIG_DEFAULTS.items():
        value = _text(config.get(config_key))
        if value:
            defaults[field] = value
    for node in canvas_nodes or []:
        if not isinstance(node, Mapping):
            continue
        data = node.get("data") if isinstance(node.get("data"), Mapping) else {}
        if defaults.get("scene_or_environment"):
            break
        scene = _text(data.get("scene") or data.get("environment"))
        if scene:
            defaults["scene_or_environment"] = scene
    return defaults


def contract_answer_map(contract: Mapping[str, Any] | None) -> dict[str, str]:
    """Flatten a stored contract into the ``answers`` map the gate consumes."""

    if not isinstance(contract, Mapping):
        return {}
    fields = contract.get("fields")
    if not isinstance(fields, Mapping):
        return {}
    return {
        field: _text(entry.get("value"))
        for field, entry in fields.items()
        if isinstance(entry, Mapping) and _text(entry.get("value"))
    }


def merge_project_contract_answers(
    answers: Mapping[str, Any] | None,
    *,
    state_dir: str | Path | None,
) -> dict[str, Any]:
    """Overlay the project's durable creative contract onto one turn's answers.

    Operator answers come from the stored contract so a new conversation never
    re-asks them.  Project-owned values (locked style, aspect ratio) are derived
    on every call instead of stored, so a style change takes effect at once.
    """

    merged = dict(answers or {})
    if not state_dir:
        return merged
    for field, value in contract_answer_map(load_creative_contract(state_dir)).items():
        merged.setdefault(field, value)
    try:
        project_config = load_project_config_file_from_state_dir(state_dir)
    except (OSError, ValueError):
        project_config = {}
    for field, value in derive_project_contract_defaults(
        project_config=project_config
    ).items():
        merged.setdefault(field, value)
    return merged


def resolve_creative_contract(
    state_dir: str | Path | None,
) -> tuple[dict[str, str], list[str]]:
    """Return ``(known_fields, missing_fields)`` for one project."""

    if not state_dir:
        return {}, list(CONTRACT_FIELDS)
    known = contract_answer_map(load_creative_contract(state_dir))
    try:
        project_config = load_project_config_file_from_state_dir(state_dir)
    except (OSError, ValueError):
        project_config = {}
    for field, value in derive_project_contract_defaults(
        project_config=project_config
    ).items():
        known.setdefault(field, value)
    missing = [field for field in CONTRACT_FIELDS if not known.get(field)]
    return known, missing


def render_creative_contract_prompt(
    state_dir: str | Path | None,
    *,
    max_chars: int = 1_400,
) -> str:
    """Render the durable contract as a bounded prompt block.

    A rotated Agent session only keeps four recent messages, so the contract has
    to travel in the prompt itself: it is the one place that tells a fresh
    session which creative decisions are already settled and must not be asked
    again.  Returns an empty string when the project has nothing settled yet, so
    the first conversation still starts from a clean question.
    """

    if not state_dir:
        return ""
    try:
        known, missing = resolve_creative_contract(state_dir)
    except (OSError, ValueError):
        return ""
    if not known:
        return ""
    lines = [
        CONTRACT_PROMPT_OPEN,
        "项目创作契约（已确认事实，禁止重复询问；按这些事实直接执行）",
    ]
    for field in CONTRACT_FIELDS:
        value = known.get(field)
        if not value:
            continue
        label = CONTRACT_FIELD_LABELS.get(field, field)
        lines.append(f"- {label}: {value}")
    if missing:
        gap_labels = "、".join(CONTRACT_FIELD_LABELS.get(f, f) for f in missing)
        lines.append(
            f"尚未对齐: {gap_labels}。一次问完（每个问题带一键默认值），"
            "或用户说“你定/按默认”时按合理默认补齐并立即执行。"
        )
    lines.append(CONTRACT_PROMPT_CLOSE)
    block = "\n".join(lines)
    if len(block) > max_chars:
        block = block[: max(0, max_chars - len(CONTRACT_PROMPT_CLOSE) - 1)].rstrip()
        block = f"{block}\n{CONTRACT_PROMPT_CLOSE}"
    return block


__all__ = [
    "CONTRACT_FIELDS",
    "CONTRACT_FIELD_LABELS",
    "CONTRACT_PROMPT_CLOSE",
    "CONTRACT_PROMPT_OPEN",
    "CREATIVE_CONTRACT_CONFIG_KEY",
    "CREATIVE_CONTRACT_SCHEMA",
    "contract_answer_map",
    "derive_project_contract_defaults",
    "load_creative_contract",
    "merge_project_contract_answers",
    "render_creative_contract_prompt",
    "resolve_creative_contract",
    "save_creative_contract",
]
