"""Side-effect-free production start policy boundary."""

from __future__ import annotations

from typing import Any

from novelvideo.creative_execution.director_clarification import (
    require_director_clarification_ready,
)


class ProductionControlService:
    """Own the creative-start gate before the legacy orchestration path."""

    def __init__(self, *, project_id: str) -> None:
        self.project_id = project_id

    def require_start_ready(
        self,
        *,
        payload: Any,
        project_id: str,
        canvas_nodes: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        answers = getattr(payload, "director_clarification_answers", {})
        contract = getattr(payload, "director_intent_contract", None)
        task = {
            "interaction_mode": getattr(payload, "interaction_mode", "execute"),
            "target_strategy": getattr(payload, "target_strategy", "create_missing"),
            "target_node_ids": getattr(payload, "target_node_ids", []),
            "existing_run_id": getattr(payload, "existing_run_id", ""),
            "idempotency_key": getattr(payload, "idempotency_key", ""),
        }
        return require_director_clarification_ready(
            request=str(getattr(payload, "goal", "") or "生成一支影片"),
            goal=str(getattr(payload, "goal", "") or ""),
            run_mode="auto" if getattr(payload, "auto_generate_paid_media", False) else "draft",
            director_intent_contract=contract if isinstance(contract, dict) else None,
            canvas_nodes=canvas_nodes or [],
            answers=answers if isinstance(answers, dict) else None,
            commands=[],
            task=task,
        )


__all__ = ["ProductionControlService"]
