"""HTTP contracts for the durable canvas workflow runtime."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AgentActionProfileCreate(StrictModel):
    operation: str = Field(min_length=1, max_length=200)
    interaction_mode: Literal["discuss", "plan", "execute"]
    target_strategy: Literal["reuse_existing", "create_missing"]
    target_node_ids: list[str] = Field(max_length=500)
    existing_run_id: str = Field(default="", max_length=200)
    creation_reason: str = Field(default="", max_length=1000)
    step_count: int = Field(ge=1, le=1000)
    item_count: int = Field(ge=1, le=50_000)
    dependency_count: int = Field(ge=0, le=200_000)
    estimated_duration_seconds: float = Field(ge=0, le=604_800)
    requires_recovery: bool
    requires_delivery: bool
    contains_paid_media: bool
    commands: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    # Immutable decision receipt carried alongside the route; execution stores
    # remain authoritative for canvas and task facts.
    goal: str = Field(default="", max_length=12000)
    success_criteria: list[str] = Field(default_factory=list, max_length=100)
    assumptions: list[str] = Field(default_factory=list, max_length=50)
    constraints: list[str] = Field(default_factory=list, max_length=50)
    unknowns: list[str] = Field(default_factory=list, max_length=50)
    director_ledger: dict[str, Any] | None = None
    director_intent_contract: dict[str, Any] | None = None
    director_clarification_answers: dict[str, str] = Field(
        default_factory=dict, max_length=16
    )
    # One planning pass owns this identity all the way to the executor.  The
    # field is optional for legacy clients, but when present it is validated at
    # the write gateway instead of being silently rebuilt by a downstream hop.
    execution_context: dict[str, Any] | None = None

    @field_validator("director_clarification_answers")
    @classmethod
    def validate_clarification_answers(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, answer in value.items():
            clean_key = str(key).strip()[:120]
            clean_answer = " ".join(str(answer or "").split())[:2000]
            if clean_key and clean_answer:
                normalized[clean_key] = clean_answer
        return normalized


class WorkflowRunCreate(StrictModel):
    workflow_id: str = Field(min_length=1, max_length=120)
    canvas_id: str = Field(min_length=1, max_length=200)
    run_mode: Literal["draft", "auto"] = "draft"
    inputs: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=240)
    contract_version: Literal[1, 2] = 2
    goal: str = Field(default="", max_length=12000)
    success_criteria: list[str] = Field(default_factory=list, max_length=100)
    source_turn_id: str = Field(default="", max_length=240)
    canvas_revision: int | None = Field(default=None, ge=0)
    selected_node_ids: list[str] = Field(default_factory=list, max_length=500)
    pinned_node_ids: list[str] = Field(default_factory=list, max_length=500)
    model_bindings: dict[str, str] = Field(default_factory=dict)
    action_profile: AgentActionProfileCreate | None = None
    parent_run_id: str = Field(default="", max_length=200)
    director_plan_revision: str = Field(default="", max_length=100)
    episode_scope: int | None = Field(default=None, ge=1, le=1000)
    concurrency_policy: dict[str, Any] = Field(default_factory=dict)
    director_ledger: dict[str, Any] | None = None
    director_intent_contract: dict[str, Any] | None = None
    director_clarification_answers: dict[str, str] = Field(
        default_factory=dict, max_length=16
    )
    production_metadata: dict[str, Any] | None = None
    execution_context: dict[str, Any] | None = None

    @field_validator("director_clarification_answers")
    @classmethod
    def validate_clarification_answers(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, answer in value.items():
            clean_key = str(key).strip()[:120]
            clean_answer = " ".join(str(answer or "").split())[:2000]
            if clean_key and clean_answer:
                normalized[clean_key] = clean_answer
        return normalized


WorkflowEventType = Literal[
    "run_started",
    "step_started",
    "step_output_ready",
    "step_progress",
    "step_items_updated",
    "step_completed",
    "step_failed",
    "canvas_applied",
    "receipt_recorded",
    "verification_passed",
    "verification_failed",
    "steering_added",
    "run_paused",
    "run_resumed",
    "run_cancelled",
    "step_retried",
    "visual_preflight_decided",
]


class WorkflowRunEventCreate(StrictModel):
    event_id: str = Field(min_length=1, max_length=240)
    type: WorkflowEventType
    step_id: str = Field(default="", max_length=120)
    success: bool | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    error: str = Field(default="", max_length=4000)
    expected_revision: int | None = Field(default=None, ge=0)


class WorkflowComposeAuthorizationRef(StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )

    schema_name: Literal["workflow_compose_authorization.v1"] = Field(alias="schema")
    authorization_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(min_length=1, max_length=200)
    canvas_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    step_id: Literal["final_film"] = "final_film"
    source_result_signature: str = Field(min_length=64, max_length=64)
    consume_key: str = Field(min_length=1, max_length=240)


class WorkflowMediaAuthorizationRef(StrictModel):
    schema_name: Literal["workflow_media_authorization.v1"] = Field(alias="schema")
    authorization_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(min_length=1, max_length=200)
    canvas_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    step_id: Literal["storyboard_images", "shot_videos"]
    error_code: Literal[
        "workflow_storyboard_paid_media_not_authorized",
        "workflow_storyboard_image_failed",
        "workflow_shot_video_paid_media_not_authorized",
        "workflow_shot_video_failed",
    ]
    recovery_action: Literal[
        "request_media_authorization",
        "retry_failed_items",
    ] = "request_media_authorization"
    retry_scope: Literal["whole_step", "failed_items_only"] = "whole_step"
    item_ids: list[str] = Field(default_factory=list, max_length=500)
    consume_key: str = Field(min_length=1, max_length=240)
    source_revision: int = Field(ge=0)


class WorkflowRunCommand(StrictModel):
    command: Literal[
        "pause",
        "resume",
        "cancel",
        "retry",
        "steer",
        "dismiss_failed_items",
    ]
    step_id: str = Field(default="", max_length=120)
    direction: str = Field(default="", max_length=8000)
    retry_scope: Literal["whole_step", "failed_items_only"] = "whole_step"
    item_ids: list[str] = Field(default_factory=list, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=240)
    expected_revision: int | None = Field(default=None, ge=0)
    execution_context: dict[str, Any] | None = None
    compose_authorization: WorkflowComposeAuthorizationRef | None = None
    media_authorization: WorkflowMediaAuthorizationRef | None = None


class WorkflowComposeAuthorizationCreate(StrictModel):
    canvas_id: str = Field(min_length=1, max_length=200)
    step_id: Literal["final_film"] = "final_film"
    ttl_seconds: int = Field(default=15 * 60, ge=60, le=30 * 60)


class WorkflowComposeAuthorizationConsume(StrictModel):
    canvas_id: str = Field(min_length=1, max_length=200)
    step_id: Literal["final_film"] = "final_film"
    authorization_id: str = Field(min_length=1, max_length=200)
    source_result_signature: str = Field(min_length=64, max_length=64)
    consume_key: str = Field(min_length=1, max_length=240)


class WorkflowCanvasAssetBindingRepair(StrictModel):
    canvas_id: str = Field(min_length=1, max_length=200)
    step_id: str = Field(min_length=1, max_length=120)
    command_id: str = Field(min_length=1, max_length=512)
    source_turn_id: str = Field(default="", max_length=240)
    expected_run_revision: int | None = Field(default=None, ge=0)


class WorkflowVisualPreflightDecision(StrictModel):
    canvas_id: str = Field(min_length=1, max_length=200)
    shot_id: str = Field(min_length=1, max_length=200)
    input_fingerprint: str = Field(min_length=64, max_length=64)
    decision: Literal["keep_original", "accept_suggestion"]
    command_id: str = Field(min_length=1, max_length=240)
    expected_run_revision: int = Field(ge=0)


class WorkflowCanvasAssetBindingReadiness(StrictModel):
    canvas_id: str = Field(min_length=1, max_length=200)
    step_id: str = Field(min_length=1, max_length=120)
    command_id: str = Field(min_length=1, max_length=512)
    source_turn_id: str = Field(default="", max_length=240)
    expected_run_revision: int | None = Field(default=None, ge=0)


class CanvasCommandApply(StrictModel):
    command_id: str = Field(min_length=1, max_length=512)
    commands: list[dict[str, Any]] = Field(min_length=1, max_length=100)
    expected_canvas_revision: int | None = Field(default=None, ge=0)
    source_turn_id: str = Field(default="", max_length=240)
    action_profile: AgentActionProfileCreate | None = None
    execution_context: dict[str, Any] | None = None
