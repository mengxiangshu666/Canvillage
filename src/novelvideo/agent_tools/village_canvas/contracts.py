"""Small response and capability contracts shared by implementation modules."""

from __future__ import annotations

from typing import Any

from novelvideo.agent_tools.tool_contract import tool_result


def _response_payload(response: object) -> dict[str, Any]:
    """Normalize plugin HTTP responses that may be wrapped or top-level JSON."""

    if not isinstance(response, dict):
        return {}
    data = response.get("data")
    return data if isinstance(data, dict) else response


def _canvas_payload_from_response(
    response: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the canvas document shape from a wrapped or direct response."""

    if not isinstance(response, dict):
        return None
    data = response.get("data")
    if isinstance(data, dict) and (
        "nodes" in data or "edges" in data or "revision" in data
    ):
        return data
    if "nodes" in response or "edges" in response or "revision" in response:
        return response
    return None


def _skill_contract_error(
    capability_id: str,
    error_code: str,
    message: str,
    *,
    missing_args: list[str] | None = None,
) -> Any:
    """Return the stable failure receipt expected by the capability broker."""

    return tool_result(
        {
            "ok": False,
            "capability_id": capability_id,
            "skill_capability": True,
            "contract_version": "skill_capability.v1",
            "error_code": error_code,
            "error": message,
            "missing_args": missing_args or [],
            "retryable": False,
        }
    )
