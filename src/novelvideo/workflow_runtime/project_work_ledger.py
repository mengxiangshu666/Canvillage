"""Persistent per-project work ledger for the Agent's cross-session memory.

The ledger answers four questions without replaying a transcript: which stage are
we on, what has actually been produced, what failed, and what was superseded.

It is deliberately a *projection*, not a new authority.  Canvas, WorkflowRun and
task receipts stay authoritative for their own results; this module only collects
their outcome into one bounded, append-only record that survives a session.

Three rules come from the libtv corpus (see ``docs/ai/specs/T-216-*.md``) and from
our own false-completion incidents:

1. a stage cannot be marked ``done`` without a verified, non-deprecated artifact;
2. deprecating an artifact keeps the entry — an erased old value is a lost audit
   trail, so the old node key is marked, never deleted;
3. every mutation appends an event and recomputes a content revision, so a
   hand-edited ledger fails validation instead of being silently trusted.
"""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


LEDGER_SCHEMA = "project_work_ledger.v1"
LEDGER_REVISION_PREFIX = "project-work-ledger.v1:"
LEDGER_FILE_NAME = "project_work_ledger.json"

#: The script-to-film chain.  Stage chains are never hardcoded here — they are
#: read from ``workflow_runtime.definitions`` so the ledger and the workflow can
#: not drift into two vocabularies.
DEFAULT_WORKFLOW_ID = "freezone-final-film"

STAGE_STATUSES = ("pending", "in_progress", "blocked", "done")
ARTIFACT_KINDS = ("text", "image", "video", "audio", "file", "run", "report")
EVENT_TYPES = (
    "stage_status",
    "artifact",
    "artifact_deprecated",
    "failure",
    "note",
)

MAX_EVENTS = 400
MAX_ARTIFACTS_PER_STAGE = 200
MAX_FAILURES_PER_STAGE = 100


def _text(value: object, *, limit: int = 2_000) -> str:
    return str(value or "").strip()[:limit]


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _text_list(value: object, *, limit: int = 100, item_limit: int = 500) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        item_text = _text(item, limit=item_limit)
        if item_text and item_text not in result:
            result.append(item_text)
        if len(result) >= limit:
            break
    return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical_payload(ledger: Mapping[str, Any]) -> str:
    return json.dumps(
        {key: ledger[key] for key in sorted(ledger) if key != "ledger_revision"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_ledger_revision(ledger: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_payload(ledger).encode("utf-8")).hexdigest()[:20]
    return f"{LEDGER_REVISION_PREFIX}{digest}"


def stage_chain_for_workflow(workflow_id: object) -> list[str]:
    """Return the real step ids of one workflow, in order."""

    # Imported lazily: ``definitions`` pulls in the execution semantics modules,
    # and this helper is called from tests that only need the ledger.
    from novelvideo.workflow_runtime.definitions import list_workflow_definitions

    wanted = _text(workflow_id, limit=120)
    for definition in list_workflow_definitions():
        if str(definition.id) == wanted:
            return [str(step.id) for step in definition.steps]
    raise ValueError(f"unknown workflow id for project work ledger: {wanted!r}")


def _blank_stage(step_id: str) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "status": "pending",
        "attempts": 0,
        "artifacts": [],
        "failures": [],
        "updated_at": "",
    }


def build_project_work_ledger(
    *,
    project_id: object,
    goal: object,
    workflow_id: object = DEFAULT_WORKFLOW_ID,
    canvas_id: object = "",
    stage_ids: object = None,
    source_turn_id: object = "",
    now: object = "",
) -> dict[str, Any]:
    """Compile an empty ledger whose stages mirror a real workflow definition."""

    clean_project = _text(project_id, limit=240)
    if not clean_project:
        raise ValueError("project work ledger requires a project id")
    clean_goal = _text(goal, limit=12_000)
    if not clean_goal:
        raise ValueError("project work ledger requires a goal")
    clean_workflow = _text(workflow_id, limit=120) or DEFAULT_WORKFLOW_ID
    chain = _text_list(stage_ids, limit=64, item_limit=120)
    if not chain:
        chain = stage_chain_for_workflow(clean_workflow)
    timestamp = _text(now, limit=60) or _utc_now()
    ledger: dict[str, Any] = {
        "schema": LEDGER_SCHEMA,
        "project_id": clean_project,
        "canvas_id": _text(canvas_id, limit=240),
        "workflow_id": clean_workflow,
        "goal": clean_goal,
        "source_turn_id": _text(source_turn_id, limit=240),
        "created_at": timestamp,
        "updated_at": timestamp,
        "stages": [_blank_stage(step_id) for step_id in chain],
        "events": [],
    }
    ledger["ledger_revision"] = compute_ledger_revision(ledger)
    return ledger


_REQUIRED_LEDGER_FIELDS = {
    "schema",
    "project_id",
    "canvas_id",
    "workflow_id",
    "goal",
    "source_turn_id",
    "created_at",
    "updated_at",
    "stages",
    "events",
    "ledger_revision",
}
_REQUIRED_STAGE_FIELDS = {
    "step_id",
    "status",
    "attempts",
    "artifacts",
    "failures",
    "updated_at",
}


def validate_project_work_ledger(value: object) -> dict[str, Any]:
    """Validate a ledger without silently repairing it."""

    if not isinstance(value, Mapping):
        raise ValueError("project_work_ledger must be an object")
    ledger = deepcopy(dict(value))
    if ledger.get("schema") != LEDGER_SCHEMA:
        raise ValueError("project_work_ledger schema is unsupported")
    missing = sorted(_REQUIRED_LEDGER_FIELDS - set(ledger))
    if missing:
        raise ValueError("project_work_ledger missing fields: " + ", ".join(missing))
    if not _text(ledger.get("project_id"), limit=240):
        raise ValueError("project_work_ledger project_id is required")
    if not _text(ledger.get("goal"), limit=12_000):
        raise ValueError("project_work_ledger goal is required")
    stages = ledger.get("stages")
    if not isinstance(stages, list) or not stages:
        raise ValueError("project_work_ledger stages must be a non-empty list")
    for index, stage in enumerate(stages):
        if not isinstance(stage, Mapping):
            raise ValueError(f"project_work_ledger stage {index} must be an object")
        stage_missing = sorted(_REQUIRED_STAGE_FIELDS - set(stage))
        if stage_missing:
            raise ValueError(
                f"project_work_ledger stage {index} missing fields: "
                + ", ".join(stage_missing)
            )
        if not _text(stage.get("step_id"), limit=120):
            raise ValueError(f"project_work_ledger stage {index} step_id is required")
        if stage.get("status") not in STAGE_STATUSES:
            raise ValueError(
                f"project_work_ledger stage {index} status is unsupported"
            )
        if not isinstance(stage.get("artifacts"), list):
            raise ValueError(f"project_work_ledger stage {index} artifacts must be a list")
        if not isinstance(stage.get("failures"), list):
            raise ValueError(f"project_work_ledger stage {index} failures must be a list")
    events = ledger.get("events")
    if not isinstance(events, list):
        raise ValueError("project_work_ledger events must be a list")
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            raise ValueError(f"project_work_ledger event {index} must be an object")
        if event.get("type") not in EVENT_TYPES:
            raise ValueError(f"project_work_ledger event {index} type is unsupported")
    expected = compute_ledger_revision(ledger)
    if _text(ledger.get("ledger_revision"), limit=120) != expected:
        raise ValueError(
            "project_work_ledger ledger_revision does not match its contents"
        )
    return ledger


def _stage(ledger: dict[str, Any], step_id: object) -> dict[str, Any]:
    wanted = _text(step_id, limit=120)
    for stage in ledger["stages"]:
        if str(stage.get("step_id")) == wanted:
            return stage
    raise ValueError(f"project_work_ledger has no stage {wanted!r}")


def _verified_artifact(stage: Mapping[str, Any]) -> bool:
    return any(
        isinstance(item, Mapping)
        and item.get("verified") is True
        and item.get("deprecated") is not True
        for item in stage.get("artifacts") or []
    )


def _apply_event(ledger: dict[str, Any], entry: Mapping[str, Any], at: str) -> None:
    event_type = str(entry.get("type"))
    if event_type == "stage_status":
        status = _text(entry.get("status"), limit=20)
        if status not in STAGE_STATUSES:
            raise ValueError(f"unsupported stage status: {status!r}")
        stage = _stage(ledger, entry.get("step_id"))
        if status == "done" and not _verified_artifact(stage):
            raise ValueError(
                "stage cannot be marked done without a verified, non-deprecated artifact"
            )
        stage["status"] = status
        stage["updated_at"] = at
        return
    if event_type == "artifact":
        kind = _text(entry.get("kind"), limit=30)
        if kind not in ARTIFACT_KINDS:
            raise ValueError(f"unsupported artifact kind: {kind!r}")
        node_key = _text(entry.get("node_key"), limit=200)
        if not node_key:
            raise ValueError("artifact node_key is required")
        stage = _stage(ledger, entry.get("step_id"))
        artifacts = stage["artifacts"]
        existing = next(
            (item for item in artifacts if _text(item.get("node_key"), limit=200) == node_key),
            None,
        )
        if existing is not None:
            # Re-recording a node key updates its evidence in place; it never
            # creates a second row for the same node, and it never silently
            # un-deprecates a superseded value.
            existing["kind"] = kind
            existing["verified"] = entry.get("verified") is True
            note = _text(entry.get("note"), limit=1_000)
            if note:
                existing["note"] = note
            existing["updated_at"] = at
            stage["updated_at"] = at
            return
        artifacts.append(
            {
                "node_key": node_key,
                "kind": kind,
                "verified": entry.get("verified") is True,
                "deprecated": False,
                "deprecated_reason": "",
                "note": _text(entry.get("note"), limit=1_000),
                "recorded_at": at,
                "updated_at": at,
            }
        )
        if len(artifacts) > MAX_ARTIFACTS_PER_STAGE:
            del artifacts[:-MAX_ARTIFACTS_PER_STAGE]
        stage["updated_at"] = at
        return
    if event_type == "artifact_deprecated":
        node_key = _text(entry.get("node_key"), limit=200)
        if not node_key:
            raise ValueError("artifact_deprecated node_key is required")
        for stage in ledger["stages"]:
            for item in stage["artifacts"]:
                if _text(item.get("node_key"), limit=200) != node_key:
                    continue
                item["deprecated"] = True
                item["deprecated_reason"] = _text(entry.get("reason"), limit=1_000)
                item["updated_at"] = at
                stage["updated_at"] = at
                return
        raise ValueError(f"project_work_ledger has no artifact {node_key!r}")
    if event_type == "failure":
        stage = _stage(ledger, entry.get("step_id"))
        attempt = entry.get("attempt")
        clean_attempt = (
            max(0, int(attempt))
            if isinstance(attempt, int) and not isinstance(attempt, bool)
            else 0
        )
        stage["failures"].append(
            {
                "tool": _text(entry.get("tool"), limit=160),
                "error_code": _text(entry.get("error_code"), limit=160),
                "disposition": _text(entry.get("disposition"), limit=40),
                "attempt": clean_attempt,
                "at": at,
            }
        )
        if len(stage["failures"]) > MAX_FAILURES_PER_STAGE:
            del stage["failures"][:-MAX_FAILURES_PER_STAGE]
        stage["attempts"] = max(int(stage.get("attempts") or 0), clean_attempt) + (
            0 if clean_attempt else 1
        )
        stage["updated_at"] = at
        return
    if event_type == "note":
        # Notes carry no projection of their own; they exist so the event log
        # stays a complete record of what was appended.
        return
    raise ValueError(f"unsupported project work ledger event: {event_type!r}")


def apply_event(ledger: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    """Append one event and return a new, re-sealed ledger."""

    current = validate_project_work_ledger(ledger)
    entry = _mapping(event)
    event_type = _text(entry.get("type"), limit=40)
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unsupported project work ledger event: {event_type!r}")
    at = _text(entry.get("at"), limit=60) or _utc_now()
    _apply_event(current, entry, at)
    recorded = {
        key: value
        for key, value in entry.items()
        if key != "at" and key != "type"
    }
    current["events"].append({"type": event_type, "at": at, **recorded})
    if len(current["events"]) > MAX_EVENTS:
        del current["events"][:-MAX_EVENTS]
    current["updated_at"] = at
    current["ledger_revision"] = compute_ledger_revision(current)
    return current


def set_stage_status(
    ledger: Mapping[str, Any],
    *,
    step_id: object,
    status: object,
    now: object = "",
) -> dict[str, Any]:
    return apply_event(
        ledger,
        {
            "type": "stage_status",
            "step_id": step_id,
            "status": status,
            "at": now,
        },
    )


def record_artifact(
    ledger: Mapping[str, Any],
    *,
    step_id: object,
    kind: object,
    node_key: object,
    verified: bool = False,
    note: object = "",
    now: object = "",
) -> dict[str, Any]:
    return apply_event(
        ledger,
        {
            "type": "artifact",
            "step_id": step_id,
            "kind": kind,
            "node_key": node_key,
            "verified": bool(verified),
            "note": note,
            "at": now,
        },
    )


def deprecate_artifact(
    ledger: Mapping[str, Any],
    *,
    node_key: object,
    reason: object = "",
    now: object = "",
) -> dict[str, Any]:
    return apply_event(
        ledger,
        {
            "type": "artifact_deprecated",
            "node_key": node_key,
            "reason": reason,
            "at": now,
        },
    )


def record_failure(
    ledger: Mapping[str, Any],
    *,
    step_id: object,
    tool: object = "",
    error_code: object = "",
    disposition: object = "",
    attempt: int = 0,
    now: object = "",
) -> dict[str, Any]:
    return apply_event(
        ledger,
        {
            "type": "failure",
            "step_id": step_id,
            "tool": tool,
            "error_code": error_code,
            "disposition": disposition,
            "attempt": attempt,
            "at": now,
        },
    )


def next_stage(ledger: Mapping[str, Any]) -> str:
    """Return the first stage that is not ``done``; empty when the chain is done."""

    for stage in ledger.get("stages") or []:
        if isinstance(stage, Mapping) and stage.get("status") != "done":
            return str(stage.get("step_id") or "")
    return ""


def render_ledger_briefing(ledger: Mapping[str, Any], *, max_chars: int = 2_400) -> str:
    """Render a bounded, deterministic briefing for prompt injection."""

    current = validate_project_work_ledger(ledger)
    limit = max(200, int(max_chars))
    lines = [
        f"项目工作账本（{LEDGER_SCHEMA}）",
        f"目标：{_text(current.get('goal'), limit=400)}",
        f"工作流：{_text(current.get('workflow_id'), limit=120)}",
        "阶段：",
    ]
    for stage in current["stages"]:
        artifacts = stage.get("artifacts") or []
        live = [
            item
            for item in artifacts
            if isinstance(item, Mapping) and item.get("deprecated") is not True
        ]
        lines.append(
            "- {step}: {status}（产物 {total}，其中有效 {live}，失败 {fails}）".format(
                step=_text(stage.get("step_id"), limit=120),
                status=_text(stage.get("status"), limit=20),
                total=len(artifacts),
                live=len(live),
                fails=len(stage.get("failures") or []),
            )
        )
    recorded = [
        (str(stage.get("step_id") or ""), item)
        for stage in current["stages"]
        for item in (stage.get("artifacts") or [])
        if isinstance(item, Mapping)
    ]
    if recorded:
        lines.append("已记录产物：")
        for step_id, item in recorded[-12:]:
            flag = "已验证" if item.get("verified") is True else "未验证"
            if item.get("deprecated") is True:
                flag = "已废弃"
            lines.append(
                f"- {step_id} · {_text(item.get('kind'), limit=30)} · "
                f"node={_text(item.get('node_key'), limit=120)} · {flag}"
            )
    failures = [
        (str(stage.get("step_id") or ""), item)
        for stage in current["stages"]
        for item in (stage.get("failures") or [])
        if isinstance(item, Mapping)
    ]
    if failures:
        lines.append("失败记录：")
        for step_id, item in failures[-6:]:
            lines.append(
                f"- {step_id} · tool={_text(item.get('tool'), limit=80)} · "
                f"code={_text(item.get('error_code'), limit=80)} · "
                f"处置={_text(item.get('disposition'), limit=40)}"
            )
    target = next_stage(current)
    lines.append(f"下一步：{target or '全部阶段已完成'}")
    rendered = "\n".join(lines)
    if len(rendered) <= limit:
        return rendered
    return rendered[: limit - 1].rstrip() + "…"


def ledger_path(state_dir: str | os.PathLike[str]) -> Path:
    return Path(state_dir) / LEDGER_FILE_NAME


def load_project_work_ledger(
    state_dir: str | os.PathLike[str],
) -> dict[str, Any] | None:
    path = ledger_path(state_dir)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return validate_project_work_ledger(payload)


def save_project_work_ledger(
    state_dir: str | os.PathLike[str],
    ledger: Mapping[str, Any],
) -> Path:
    """Write the ledger with an atomic replace so a crash cannot truncate it."""

    current = validate_project_work_ledger(ledger)
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = ledger_path(directory)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


__all__ = [
    "ARTIFACT_KINDS",
    "DEFAULT_WORKFLOW_ID",
    "EVENT_TYPES",
    "LEDGER_FILE_NAME",
    "LEDGER_REVISION_PREFIX",
    "LEDGER_SCHEMA",
    "MAX_EVENTS",
    "STAGE_STATUSES",
    "apply_event",
    "build_project_work_ledger",
    "compute_ledger_revision",
    "deprecate_artifact",
    "ledger_path",
    "load_project_work_ledger",
    "next_stage",
    "record_artifact",
    "record_failure",
    "render_ledger_briefing",
    "save_project_work_ledger",
    "set_stage_status",
    "stage_chain_for_workflow",
    "validate_project_work_ledger",
]
