"""Shared result contracts for workflow step handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True, slots=True)
class StepResult:
    event_type: str
    payload: dict[str, Any]


class WorkflowStepExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "workflow_step_failed",
        details: dict[str, Any] | None = None,
    ):
        self.code = code
        self.details = dict(details or {})
        super().__init__(message)


StepHandler = Callable[[dict[str, Any], dict[str, Any]], Awaitable[StepResult]]


__all__ = [
    "StepHandler",
    "StepResult",
    "WorkflowStepExecutionError",
]
