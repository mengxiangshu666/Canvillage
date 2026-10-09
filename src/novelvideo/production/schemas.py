"""Typed contracts for the canonical production registry."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EntityKind = Literal[
    "series",
    "episode",
    "scene",
    "beat",
    "shot",
    "panel",
    "character",
    "costume",
    "prop",
    "voice",
    "style",
    "asset",
]
VersionStatus = Literal[
    "draft",
    "submitted",
    "approved",
    "selected",
    "published",
    "delivered",
    "locked",
    "superseded",
]
ProjectionRole = Literal[
    "character_identity",
    "character_appearance",
    "costume",
    "scene_layout",
    "prop_identity",
    "style",
    "composition",
    "pose",
    "motion",
    "first_frame",
    "last_frame",
    "voice",
    "audio_rhythm",
    "reference",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProductionEntityCreate(StrictModel):
    kind: EntityKind
    display_name: str = Field(min_length=1, max_length=200)
    source_kind: str = Field(default="native", min_length=1, max_length=80)
    source_id: str = Field(default="", max_length=300)
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(default="", max_length=200)


class WorkVersionCreate(StrictModel):
    artifact_url: str = Field(default="", max_length=4000)
    artifact_path: str = Field(default="", max_length=4000)
    sha256: str = Field(default="", max_length=64)
    dependency_fingerprint: str = Field(default="", max_length=128)
    # Personal power-user mode: status is never an enterprise approval gate.
    status: VersionStatus = "draft"
    metadata: dict[str, Any] = Field(default_factory=dict)
    expected_revision: int | None = Field(default=None, ge=0)
    idempotency_key: str = Field(default="", max_length=200)

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if value and (
            len(value) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in value)
        ):
            raise ValueError("sha256 must be a 64-character hexadecimal digest")
        return value.lower()


class CanvasProjectionCreate(StrictModel):
    canvas_id: str = Field(min_length=1, max_length=200)
    node_id: str = Field(min_length=1, max_length=200)
    entity_id: str = Field(min_length=1, max_length=80)
    version_id: str = Field(default="", max_length=80)
    role: ProjectionRole = "reference"
    last_seen_revision: int = Field(default=0, ge=0)
    local_overrides: dict[str, Any] = Field(default_factory=dict)
    expected_projection_revision: int | None = Field(default=None, ge=0)


class PromotionPreviewRequest(StrictModel):
    target_entity_id: str = Field(default="", max_length=80)
    target_role: ProjectionRole = "reference"
    expected_entity_revision: int | None = Field(default=None, ge=0)


class LibTVCanvasLink(StrictModel):
    canvas_uuid: str = Field(min_length=16, max_length=100)
    display_name: str = Field(default="", max_length=200)


class ProductionControlStart(StrictModel):
    """Start the best-result pipeline or execute only its next real stage."""

    mode: Literal["best", "next"] = "best"
    entry_mode: Literal["novel_adapt", "original"] = "novel_adapt"
    uploaded_filename: str = Field(default="", max_length=500)
    target_episodes: int = Field(default=1, ge=1, le=100)
    episode: int | None = Field(default=None, ge=0)
    image_model: str = Field(default="", max_length=200)
    video_backend: str = Field(default="", max_length=200)
    model_bindings: dict[str, str] = Field(default_factory=dict, max_length=7)
    aspect_ratio: Literal["9:16", "16:9", "2:3", "1:1"] | None = None
    auto_generate_paid_media: bool = False
    confirmed_paid_media: bool = False
    canvas_id: str = Field(default="", max_length=200)
    goal: str = Field(default="", max_length=12000)
    success_criteria: list[str] = Field(default_factory=list, max_length=100)
    assumptions: list[str] = Field(default_factory=list, max_length=50)
    constraints: list[str] = Field(default_factory=list, max_length=50)
    output_spec: dict[str, Any] = Field(default_factory=dict)
    world_state_ref: str = Field(default="", max_length=300)
    asset_plan: dict[str, Any] = Field(default_factory=dict)
    episode_plan: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    quality_gates: list[str] = Field(default_factory=list, max_length=50)
    budget: dict[str, Any] = Field(default_factory=dict)
    canvas_skeleton_refs: list[str] = Field(default_factory=list, max_length=500)
    concurrency_policy: dict[str, Any] = Field(default_factory=dict)
    director_plan: dict[str, Any] | None = None
    director_intent_contract: dict[str, Any] | None = None
    director_clarification_answers: dict[str, str] = Field(default_factory=dict, max_length=16)
    interaction_mode: Literal["discuss", "plan", "execute"] = "execute"
    target_strategy: Literal["reuse_existing", "create_missing"] = "create_missing"
    target_node_ids: list[str] = Field(default_factory=list, max_length=500)
    canvas_nodes: list[dict[str, Any]] = Field(default_factory=list, max_length=2000)
    canvas_snapshot: dict[str, Any] = Field(default_factory=dict)
    original_script: str = Field(default="", max_length=30000)
    auto_pass_gate_a: bool = False
    auto_pass_gate_b: bool = False
    existing_run_id: str = Field(default="", max_length=200)
    idempotency_key: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def validate_episode_scope(self) -> "ProductionControlStart":
        if self.entry_mode == "novel_adapt" and self.episode == 0:
            raise ValueError("novel_adapt 不允许使用 episode=0")
        if self.entry_mode == "original" and self.episode not in (None, 0):
            raise ValueError("original 模式固定使用唯一 ep000")
        return self

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


class ProductionControlCommand(StrictModel):
    command: Literal[
        "pause",
        "resume",
        "confirm_gate",
        "cancel",
        "retry",
        "skip",
        "take_over",
    ]
    confirmed_paid_media: bool = False


class ProductionOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    generated_at: str
    counts: dict[str, int]
    task_summary: dict[str, int]
    stage_summary: list[dict[str, Any]]
    blockers: list[dict[str, Any]]
    attention: list[dict[str, Any]]
    power_user_mode: bool
    next_actions: list[dict[str, Any]]
