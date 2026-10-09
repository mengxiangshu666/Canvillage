"""Canvas command boundary shared by workflow code and concrete adapters."""

from __future__ import annotations

from typing import Any, Protocol, TypedDict


class CanvasCommandReceipt(TypedDict, total=False):
    """Minimum receipt shape returned by a canvas command adapter."""

    schema: str
    command_id: str
    revision: int
    normalized_envelope: dict[str, Any]
    expectation: dict[str, Any]
    created_node_ids: list[str]
    affected_node_ids: list[str]
    op_results: list[dict[str, Any]]
    idempotent_replay: bool


class CanvasCommandPortError(RuntimeError):
    """Stable, adapter-neutral command failure exposed to workflow callers."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        op_index: int | None = None,
        current_revision: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.op_index = op_index
        self.current_revision = current_revision
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": False,
            "error": str(self),
            "error_code": self.code,
            "op_index": self.op_index,
            "current_revision": self.current_revision,
            "details": self.details,
        }


class CanvasCommandPort(Protocol):
    """Apply a validated command envelope to the authoritative canvas."""

    def apply(
        self,
        *,
        canvas_id: str,
        envelope: dict[str, Any],
        expected_canvas_revision: int | None = None,
    ) -> CanvasCommandReceipt | dict[str, Any]:
        """Apply one command batch and return its durable receipt."""


__all__ = [
    "CanvasCommandPort",
    "CanvasCommandPortError",
    "CanvasCommandReceipt",
]
