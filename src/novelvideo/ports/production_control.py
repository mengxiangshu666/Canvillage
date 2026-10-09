"""Minimal production-control boundary consumed by workflow runtime."""

from __future__ import annotations

from typing import Any, Protocol


class ProductionControlPort(Protocol):
    async def register_child_execution(
        self,
        *,
        parent_run_id: str,
        stage_id: str,
        child_type: str,
        child_id: str,
        task_type: str = "",
        correlation_id: str = "",
        status: str = "queued",
        progress: float = 0.0,
        summary: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        """Persist one workflow-to-production child relation."""

    async def create_or_reuse_active(
        self,
        *,
        mode: str,
        settings: dict[str, Any],
        existing_run_id: str = "",
        idempotency_key: str = "",
    ) -> tuple[dict[str, Any], bool]:
        """Create or reuse the explicitly identified production run."""


__all__ = ["ProductionControlPort"]
