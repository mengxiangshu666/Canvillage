"""Run-scoped production authorization for one final-film workflow."""

from __future__ import annotations

from typing import Any, Mapping


PRODUCTION_AUTHORIZATION_SCHEMA = "workflow_production_authorization.v1"
PRODUCTION_AUTHORIZATION_SCOPE = "workflow_run"
PRODUCTION_AUTHORIZATION_WORKFLOW_ID = "freezone-final-film"
PRODUCTION_AUTHORIZATION_SOURCES = frozenset(
    {"server_turn_grant", "user_approval"}
)


def _text(value: object, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _bounded_int(
    value: object,
    *,
    minimum: int,
    maximum: int,
    field: str,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return value


def normalize_production_authorization(
    value: object,
    *,
    workflow_id: str,
    run_mode: str,
    project_id: str,
    canvas_id: str,
    source_turn_id: str,
    auto_generate_paid_media: bool,
) -> dict[str, Any] | None:
    """Validate one server-owned authorization envelope before run creation."""

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("production authorization must be an object")
    if _text(value.get("schema")) != PRODUCTION_AUTHORIZATION_SCHEMA:
        raise ValueError("production authorization schema is invalid")
    if _text(workflow_id) != PRODUCTION_AUTHORIZATION_WORKFLOW_ID:
        raise ValueError("production authorization is only valid for final film")
    if _text(run_mode) != "auto" or auto_generate_paid_media is not True:
        raise ValueError("production authorization requires automatic paid media")
    if _text(value.get("scope")) != PRODUCTION_AUTHORIZATION_SCOPE:
        raise ValueError("production authorization scope is invalid")
    if _text(value.get("project_id")) != _text(project_id):
        raise ValueError("production authorization project does not match")
    if _text(value.get("canvas_id")) != _text(canvas_id):
        raise ValueError("production authorization canvas does not match")
    expected_turn = _text(source_turn_id)
    marker_turn = _text(value.get("source_turn_id"))
    if not expected_turn:
        raise ValueError("production authorization requires a source turn")
    if marker_turn != expected_turn:
        raise ValueError("production authorization source turn does not match")
    source = _text(value.get("source"))
    if source not in PRODUCTION_AUTHORIZATION_SOURCES:
        raise ValueError("production authorization source is invalid")
    if value.get("allow_final_film") is not True:
        raise ValueError("production authorization must allow final film")
    return {
        "schema": PRODUCTION_AUTHORIZATION_SCHEMA,
        "scope": PRODUCTION_AUTHORIZATION_SCOPE,
        "workflow_id": PRODUCTION_AUTHORIZATION_WORKFLOW_ID,
        "project_id": _text(project_id),
        "canvas_id": _text(canvas_id),
        "source_turn_id": marker_turn,
        "source": source,
        "max_paid_starts": _bounded_int(
            value.get("max_paid_starts"),
            minimum=1,
            maximum=64,
            field="max_paid_starts",
        ),
        "max_shots": _bounded_int(
            value.get("max_shots"),
            minimum=1,
            maximum=120,
            field="max_shots",
        ),
        "max_reference_images": _bounded_int(
            value.get("max_reference_images"),
            minimum=1,
            maximum=1080,
            field="max_reference_images",
        ),
        "max_duration_seconds": _bounded_int(
            value.get("max_duration_seconds"),
            minimum=1,
            maximum=7200,
            field="max_duration_seconds",
        ),
        "allow_final_film": True,
    }


def production_authorization_from_run(
    run: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return the canonical envelope stored on one automatic final-film run."""

    inputs = run.get("inputs") if isinstance(run.get("inputs"), Mapping) else {}
    marker = (
        inputs.get("production_authorization")
        if isinstance(inputs, Mapping)
        else None
    )
    if not isinstance(marker, Mapping):
        return None
    try:
        normalized = normalize_production_authorization(
            marker,
            workflow_id=_text(run.get("workflow_id")),
            run_mode=_text(run.get("run_mode")),
            project_id=_text(run.get("project_id")),
            canvas_id=_text(run.get("canvas_id")),
            source_turn_id=_text(run.get("source_turn_id")),
            auto_generate_paid_media=(
                inputs.get("auto_generate_paid_media") is True
                if isinstance(inputs, Mapping)
                else False
            ),
        )
    except ValueError:
        return None
    return normalized


def production_authorization_allows_final_film(
    run: Mapping[str, Any],
    *,
    shot_count: int,
    duration_seconds: float,
) -> tuple[bool, str]:
    """Check that final composition stays inside the authorized production."""

    marker = production_authorization_from_run(run)
    if marker is None:
        return False, "production_authorization_missing"
    if shot_count > int(marker["max_shots"]):
        return False, "production_authorization_shot_limit_exceeded"
    if duration_seconds > float(marker["max_duration_seconds"]) + 0.001:
        return False, "production_authorization_duration_limit_exceeded"
    return True, "production_authorization"


def production_authorization_allows_storyboard(
    run: Mapping[str, Any],
    *,
    shot_count: int,
    reference_count: int,
) -> tuple[bool, str]:
    """Check that storyboard work stays inside one Run production envelope."""

    marker = production_authorization_from_run(run)
    if marker is None:
        return True, "production_authorization_not_required"
    if shot_count > int(marker["max_shots"]):
        return False, "workflow_production_authorization_shot_limit_exceeded"
    if reference_count > int(marker["max_reference_images"]):
        return (
            False,
            "workflow_production_authorization_reference_limit_exceeded",
        )
    return True, "production_authorization"


__all__ = [
    "PRODUCTION_AUTHORIZATION_SCHEMA",
    "PRODUCTION_AUTHORIZATION_SCOPE",
    "PRODUCTION_AUTHORIZATION_SOURCES",
    "PRODUCTION_AUTHORIZATION_WORKFLOW_ID",
    "normalize_production_authorization",
    "production_authorization_allows_final_film",
    "production_authorization_allows_storyboard",
    "production_authorization_from_run",
]
