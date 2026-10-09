"""Neutral selection facade for declarative starter workflow contracts."""

from __future__ import annotations

from typing import Any, Mapping


def select_starter_workflow_id(intent_contract: object, *, fallback: str) -> str:
    from novelvideo.freezone.starter_workflow_contract import (
        select_starter_workflow_id as select_workflow,
    )

    return select_workflow(intent_contract, fallback=fallback)


def validate_starter_workflow(value: Mapping[str, Any]) -> dict[str, Any]:
    from novelvideo.freezone.starter_workflow_contract import (
        validate_starter_workflow as validate_workflow,
    )

    return validate_workflow(value)


__all__ = ["select_starter_workflow_id", "validate_starter_workflow"]
