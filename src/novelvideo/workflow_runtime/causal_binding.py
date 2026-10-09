"""Credential-free causal identifiers shared by Agent, workflow and canvas."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal


CAUSAL_BINDING_SCHEMA = "canvas_causal_binding.v1"
CausalOrigin = Literal["manual", "agent", "workflow", "system"]


def _text(value: object, *, limit: int = 512) -> str:
    return str(value or "").strip()[:limit]


def _revision(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


@dataclass(frozen=True, slots=True)
class CausalBinding:
    project_id: str
    canvas_id: str
    origin: CausalOrigin
    source_turn_id: str = ""
    workflow_run_id: str = ""
    workflow_step_id: str = ""
    workflow_item_id: str = ""
    command_id: str = ""
    task_id: str = ""
    canvas_revision: int | None = None
    input_revision: int | None = None
    model_plan_revision: str = ""

    def with_updates(self, **updates: Any) -> CausalBinding:
        return replace(self, **updates)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": CAUSAL_BINDING_SCHEMA,
            "origin": self.origin,
            "project_id": _text(self.project_id, limit=240),
            "canvas_id": _text(self.canvas_id, limit=240),
        }
        optional_text = {
            "source_turn_id": self.source_turn_id,
            "workflow_run_id": self.workflow_run_id,
            "workflow_step_id": self.workflow_step_id,
            "workflow_item_id": self.workflow_item_id,
            "command_id": self.command_id,
            "task_id": self.task_id,
            "model_plan_revision": self.model_plan_revision,
        }
        payload.update(
            {
                key: _text(value)
                for key, value in optional_text.items()
                if _text(value)
            }
        )
        for key, value in (
            ("canvas_revision", _revision(self.canvas_revision)),
            ("input_revision", _revision(self.input_revision)),
        ):
            if value is not None:
                payload[key] = value
        return payload


def binding_from_run(
    run: dict[str, Any],
    *,
    step_id: str = "",
    item_id: str = "",
    command_id: str = "",
    task_id: str = "",
) -> CausalBinding:
    context = run.get("project_context")
    project_context = context if isinstance(context, dict) else {}
    return CausalBinding(
        project_id=_text(run.get("project_id") or project_context.get("project_id")),
        canvas_id=_text(run.get("canvas_id") or project_context.get("canvas_id")),
        origin="workflow",
        source_turn_id=_text(
            run.get("source_turn_id") or project_context.get("source_turn_id")
        ),
        workflow_run_id=_text(run.get("id") or project_context.get("workflow_run_id")),
        workflow_step_id=_text(step_id),
        workflow_item_id=_text(item_id),
        command_id=_text(command_id),
        task_id=_text(task_id),
        canvas_revision=_revision(
            project_context.get("observed_canvas_revision")
            if project_context.get("observed_canvas_revision") is not None
            else project_context.get("canvas_revision")
        ),
        input_revision=_revision(project_context.get("canvas_revision")),
        model_plan_revision=_text(
            run.get("model_plan_revision")
            or project_context.get("model_plan_revision")
        ),
    )


def binding_from_envelope(
    envelope: dict[str, Any],
    *,
    default_project_id: str = "",
    default_canvas_id: str = "",
) -> CausalBinding:
    workflow_run_id = _text(
        envelope.get("run_id") or envelope.get("workflow_run_id")
    )
    origin: CausalOrigin = "workflow" if workflow_run_id else "agent"
    existing = envelope.get("causal_binding")
    existing_binding = existing if isinstance(existing, dict) else {}
    return CausalBinding(
        project_id=_text(
            envelope.get("project_id")
            or existing_binding.get("project_id")
            or default_project_id
        ),
        canvas_id=_text(
            envelope.get("canvas_id")
            or existing_binding.get("canvas_id")
            or default_canvas_id
        ),
        origin=origin,
        source_turn_id=_text(
            envelope.get("turn_id") or existing_binding.get("source_turn_id")
        ),
        workflow_run_id=workflow_run_id,
        workflow_step_id=_text(
            envelope.get("step_id") or existing_binding.get("workflow_step_id")
        ),
        workflow_item_id=_text(existing_binding.get("workflow_item_id")),
        command_id=_text(envelope.get("command_id")),
        task_id=_text(existing_binding.get("task_id")),
        canvas_revision=_revision(existing_binding.get("canvas_revision")),
        input_revision=_revision(existing_binding.get("input_revision")),
        model_plan_revision=_text(existing_binding.get("model_plan_revision")),
    )


__all__ = [
    "CAUSAL_BINDING_SCHEMA",
    "CausalBinding",
    "CausalOrigin",
    "binding_from_envelope",
    "binding_from_run",
]
