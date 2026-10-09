"""持久化工作流 reducer 共用的条目状态归一化工具。"""

from __future__ import annotations

import math
from typing import Any


_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "dismissed"})
_STATUS_ALIASES = {
    "success": "completed",
    "succeeded": "completed",
    "done": "completed",
    "error": "failed",
    "failure": "failed",
    "cancel": "cancelled",
}


def item_id(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("item_id", "itemId", "node_id", "nodeId", "id"):
            candidate = value.get(key)
            if candidate is not None and str(candidate).strip():
                return str(candidate).strip()
        return ""
    return str(value).strip() if value is not None else ""


def progress_value(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("进度必须是 0 到 1 之间的数字")
    progress = float(value)
    if not math.isfinite(progress) or not 0 <= progress <= 1:
        raise ValueError("进度必须是 0 到 1 之间的数字")
    return progress


def merge_artifact(
    previous: dict[str, Any] | None,
    update: dict[str, Any],
) -> dict[str, Any]:
    previous_kind = (
        str(previous.get("kind") or "").strip()
        if isinstance(previous, dict)
        else ""
    )
    update_kind = str(update.get("kind") or "").strip()
    if previous_kind and update_kind and previous_kind != update_kind:
        # A step can materialize one artifact kind as proof before replacing it
        # with the artifact it actually owns.  Never leak fields between kinds.
        return dict(update)
    merged = {**(previous if isinstance(previous, dict) else {}), **update}
    for field in ("jobs", "media_assets"):
        old_items = previous.get(field) if isinstance(previous, dict) else None
        new_items = update.get(field)
        if not isinstance(old_items, list) or not isinstance(new_items, list):
            continue
        combined: list[Any] = []
        positions: dict[str, int] = {}
        for item in [*old_items, *new_items]:
            key = item_id(item)
            if not key:
                combined.append(item)
                continue
            if key in positions:
                combined[positions[key]] = item
            else:
                positions[key] = len(combined)
                combined.append(item)
        merged[field] = combined
    return merged


def summarize_item_states(
    states: dict[str, dict[str, Any]],
) -> dict[str, int]:
    summary = {
        "total": len(states),
        "completed": sum(
            state.get("status") == "completed" for state in states.values()
        ),
        "failed": sum(state.get("status") == "failed" for state in states.values()),
        "running": sum(
            state.get("status") == "running" for state in states.values()
        ),
        "pending": sum(
            state.get("status") == "pending" for state in states.values()
        ),
        "cancelled": sum(
            state.get("status") == "cancelled" for state in states.values()
        ),
    }
    dismissed = sum(
        state.get("status") == "dismissed" for state in states.values()
    )
    if dismissed:
        summary["dismissed"] = dismissed
    return summary


def item_states_from_payload(
    previous: dict[str, Any] | None,
    payload: dict[str, Any],
    *,
    step_attempt: int,
    now: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]] | None:
    previous_kind = (
        str(previous.get("kind") or "").strip()
        if isinstance(previous, dict)
        else ""
    )
    payload_kind = str(payload.get("kind") or "").strip()
    kind_changed = bool(
        previous_kind and payload_kind and previous_kind != payload_kind
    )
    prior_states = (
        previous.get("item_states")
        if isinstance(previous, dict) and not kind_changed
        else None
    )
    states: dict[str, dict[str, Any]] = (
        {}
        if kind_changed
        else {
            str(state_id): dict(item_state)
            for state_id, item_state in (
                prior_states.items() if isinstance(prior_states, dict) else []
            )
            if str(state_id).strip() and isinstance(item_state, dict)
        }
    )
    item_ids: set[str] = set(states)

    def add_id(value: Any) -> str:
        candidate = item_id(value)
        if candidate:
            item_ids.add(candidate)
        return candidate

    previous_targets = (
        previous.get("target_node_ids", [])
        if isinstance(previous, dict) and not kind_changed
        else []
    )
    for value in previous_targets:
        add_id(value)
    for field in (
        "target_node_ids",
        "item_ids",
        "completed_item_ids",
        "failed_item_ids",
        "cancelled_item_ids",
    ):
        raw_values = payload.get(field)
        if isinstance(raw_values, list):
            for value in raw_values:
                add_id(value)
    for field in ("item_id", "failed_item_id", "failed_node_id"):
        add_id(payload.get(field))
    raw_states = payload.get("item_states")
    if isinstance(raw_states, dict):
        for value in raw_states:
            add_id(value)
    for field in ("items", "jobs", "media_assets"):
        raw_items = payload.get(field)
        if isinstance(raw_items, list):
            for value in raw_items:
                add_id(value)
    if not item_ids:
        return None

    for state_id in item_ids:
        current = dict(states.get(state_id) or {})
        current.setdefault("id", state_id)
        current.setdefault("status", "pending")
        current.setdefault("attempt", max(1, step_attempt))
        current.setdefault("progress", 0.0)
        current.setdefault("error", "")
        current["updated_at"] = now
        states[state_id] = current

    def apply(
        value: Any,
        *,
        fallback_status: str = "pending",
        output_key: str = "output",
    ) -> None:
        state_id = add_id(value)
        if not state_id:
            return
        current = states[state_id]
        raw = value if isinstance(value, dict) else {}
        status = str(raw.get("status") or fallback_status).strip().lower()
        status = _STATUS_ALIASES.get(status, status) if status else fallback_status
        if status not in _TERMINAL_STATUSES | {"pending", "running"}:
            status = fallback_status
        current["status"] = status
        raw_attempt = raw.get("attempt")
        if (
            isinstance(raw_attempt, int)
            and not isinstance(raw_attempt, bool)
            and raw_attempt > 0
        ):
            current["attempt"] = max(
                int(current.get("attempt") or 1), raw_attempt
            )
        else:
            current["attempt"] = max(
                int(current.get("attempt") or 1), step_attempt
            )
        if "progress" in raw:
            try:
                current["progress"] = max(
                    float(current.get("progress") or 0.0),
                    progress_value(raw["progress"]),
                )
            except ValueError:
                pass
        if status == "completed":
            current["progress"] = 1.0
        if raw.get("error"):
            current["error"] = str(raw["error"])
        if output_key in raw:
            current[output_key] = raw[output_key]
        elif raw and output_key == "output":
            current["output"] = {
                key: item
                for key, item in raw.items()
                if key not in {"status", "attempt", "progress", "error"}
            }
        current["updated_at"] = now

    if isinstance(raw_states, dict):
        for state_id, value in raw_states.items():
            apply({"id": state_id, **(value if isinstance(value, dict) else {})})
    for value in payload.get("items", []) if isinstance(payload.get("items"), list) else []:
        apply(value)
    for value in payload.get("jobs", []) if isinstance(payload.get("jobs"), list) else []:
        apply(value, fallback_status="running", output_key="job")
    for value in (
        payload.get("media_assets", [])
        if isinstance(payload.get("media_assets"), list)
        else []
    ):
        apply(value, fallback_status="completed", output_key="output")
    for value in (
        payload.get("completed_item_ids", [])
        if isinstance(payload.get("completed_item_ids"), list)
        else []
    ):
        apply(value, fallback_status="completed")
    for value in (
        payload.get("cancelled_item_ids", [])
        if isinstance(payload.get("cancelled_item_ids"), list)
        else []
    ):
        apply(value, fallback_status="cancelled")
    failed_ids = (
        list(payload.get("failed_item_ids", []))
        if isinstance(payload.get("failed_item_ids"), list)
        else []
    )
    for value in (
        payload.get("failed_item_id"),
        payload.get("failed_node_id"),
        *failed_ids,
    ):
        state_id = add_id(value)
        if state_id:
            apply(
                {
                    "id": state_id,
                    "status": "failed",
                    "error": payload.get("error")
                    or payload.get("message")
                    or "item_failed",
                },
                fallback_status="failed",
            )
    if payload.get("item_id") and payload.get("status"):
        apply(
            {
                "id": payload["item_id"],
                "status": payload["status"],
                "progress": payload.get("progress"),
            },
            fallback_status="running",
        )

    return states, summarize_item_states(states)
