"""Structured contracts for the Story Lab generation stages."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class StoryLabStage(str, Enum):
    BIBLE = "bible"
    OUTLINE = "outline"
    DRAFT = "draft"
    AUDIT = "audit"


class StoryLabWorkType(str, Enum):
    LONG_NOVEL = "long_novel"
    SHORT_NOVEL = "short_novel"
    SCREENPLAY = "screenplay"
    MICRO_DRAMA = "micro_drama"
    COMIC_NARRATION = "comic_narration"


class StoryLabConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    logline: str = Field(min_length=8, max_length=4000)
    work_type: StoryLabWorkType
    genre: str = Field(default="", max_length=200)
    theme: str = Field(default="", max_length=500)
    target_units: int = Field(default=12, ge=1, le=200)
    target_length: int = Field(default=1500, ge=100, le=100_000)
    target_duration_seconds: float | None = Field(default=None, ge=10, le=7200)
    point_of_view: str = Field(default="", max_length=100)
    audience: str = Field(default="", max_length=200)
    style_mode: Literal["infer", "confirmed"] = "infer"
    style_id: str = Field(default="", max_length=200)
    style_name: str = Field(default="", max_length=200)
    style_prompt: str = Field(default="", max_length=8000)

    @model_validator(mode="after")
    def require_confirmed_style(self) -> "StoryLabConfig":
        if self.style_mode == "confirmed" and not any(
            (self.style_id, self.style_name, self.style_prompt)
        ):
            raise ValueError("confirmed style requires style_id, style_name, or style_prompt")
        return self


class BibleCharacter(BaseModel):
    name: str
    role: str = ""
    goal: str = ""
    conflict: str = ""
    traits: list[str] = Field(default_factory=list)
    arc: str = ""
    visual_identity: str = ""


class BibleLocation(BaseModel):
    name: str
    function: str = ""
    atmosphere: str = ""
    visual_features: list[str] = Field(default_factory=list)


class BibleRelationship(BaseModel):
    source: str
    target: str
    relationship: str
    tension: str = ""


class StoryBibleResult(BaseModel):
    premise: str
    world: str
    core_conflict: str
    characters: list[BibleCharacter] = Field(default_factory=list)
    locations: list[BibleLocation] = Field(default_factory=list)
    relationships: list[BibleRelationship] = Field(default_factory=list)
    world_rules: list[str] = Field(default_factory=list)
    taboos: list[str] = Field(default_factory=list)
    tone_and_style: str = ""


class ActOutline(BaseModel):
    number: int = Field(ge=1)
    title: str
    purpose: str
    turning_point: str = ""
    tension_target: int = Field(default=50, ge=0, le=100)


class Storyline(BaseModel):
    id: str
    name: str
    kind: Literal["main", "sub"] = "sub"
    objective: str = ""
    dependencies: list[str] = Field(default_factory=list)


class ForeshadowingItem(BaseModel):
    id: str
    setup_unit: int = Field(ge=1)
    payoff_unit: int = Field(ge=1)
    description: str
    status: Literal["planned", "planted", "paid_off"] = "planned"


class CausalLink(BaseModel):
    cause: str
    effect: str
    bridge: str = ""


class TensionPoint(BaseModel):
    unit: int = Field(ge=1)
    score: int = Field(ge=0, le=100)
    reason: str = ""


class UnitCard(BaseModel):
    number: int = Field(ge=1)
    title: str
    purpose: str
    conflict: str = ""
    turn: str = ""
    climax: str = ""
    hook: str = ""
    characters: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    props: list[str] = Field(default_factory=list)


class OutlineResult(BaseModel):
    synopsis: str
    acts: list[ActOutline] = Field(default_factory=list)
    storylines: list[Storyline] = Field(default_factory=list)
    foreshadowing: list[ForeshadowingItem] = Field(default_factory=list)
    causal_links: list[CausalLink] = Field(default_factory=list)
    tension_curve: list[TensionPoint] = Field(default_factory=list)
    units: list[UnitCard] = Field(default_factory=list)


class DraftUnit(BaseModel):
    number: int = Field(ge=1)
    title: str
    content: str


class DraftResult(BaseModel):
    title: str
    format: Literal[
        "novel_chapter",
        "scene_screenplay",
        "episode_script",
        "comic_narration",
    ]
    units: list[DraftUnit] = Field(default_factory=list)
    full_text: str = ""

    @model_validator(mode="after")
    def ensure_text(self) -> "DraftResult":
        if not self.full_text.strip() and not self.units:
            raise ValueError("draft requires full_text or at least one unit")
        return self


class AuditFinding(BaseModel):
    severity: Literal["info", "warning", "error"] = "warning"
    category: str
    unit: int | None = Field(default=None, ge=1)
    description: str
    suggestion: str = ""


class AuditScore(BaseModel):
    dimension: str
    score: int = Field(ge=0, le=100)
    rationale: str = ""


class AuditResult(BaseModel):
    summary: str
    ready_for_production: bool = False
    findings: list[AuditFinding] = Field(default_factory=list)
    scores: list[AuditScore] = Field(default_factory=list)
    character_consistency: list[str] = Field(default_factory=list)
    world_conflicts: list[str] = Field(default_factory=list)
    missing_foreshadowing: list[str] = Field(default_factory=list)
    broken_causality: list[str] = Field(default_factory=list)
    style_drift: list[str] = Field(default_factory=list)
    visual_production_risks: list[str] = Field(default_factory=list)
    downstream_asset_gaps: list[str] = Field(default_factory=list)


STAGE_RESULT_MODELS: dict[StoryLabStage, type[BaseModel]] = {
    StoryLabStage.BIBLE: StoryBibleResult,
    StoryLabStage.OUTLINE: OutlineResult,
    StoryLabStage.DRAFT: DraftResult,
    StoryLabStage.AUDIT: AuditResult,
}


def validate_stage_result(stage: StoryLabStage, value: Any) -> dict[str, Any]:
    model = STAGE_RESULT_MODELS[stage].model_validate(value)
    return model.model_dump(mode="json")


class StoryLabStageArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: StoryLabStage
    prompt_version: str
    model: str
    result: dict[str, Any]
    source: Literal["generated", "manual"] = "generated"
    instructions: str = ""
    revision: int = Field(default=1, ge=1)
    updated_at: str = Field(default_factory=utc_now_iso)

    @model_validator(mode="after")
    def validate_result_contract(self) -> "StoryLabStageArtifact":
        self.result = validate_stage_result(self.stage, self.result)
        return self


class StoryLabState(BaseModel):
    config: StoryLabConfig | None = None
    stages: dict[StoryLabStage, StoryLabStageArtifact] = Field(default_factory=dict)
    updated_at: str = Field(default_factory=utc_now_iso)


class StoryLabGenerateRequest(BaseModel):
    stage: StoryLabStage
    instructions: str = Field(default="", max_length=8000)


class StoryLabStageEditRequest(BaseModel):
    result: dict[str, Any]
    editor_note: str = Field(
        default="",
        max_length=2000,
        validation_alias=AliasChoices("editor_note", "note"),
    )


class StoryLabExportRequest(BaseModel):
    filename: str = Field(default="", max_length=240)


class StoryLabPublishRequest(StoryLabExportRequest):
    rebuild: bool = True
