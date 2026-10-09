"""Validated data models used by the durable workflow executor."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StoryboardShot(BaseModel):
    shot_id: str = ""
    title: str = Field(min_length=1, max_length=120)
    duration_seconds: float = Field(ge=1, le=20)
    prompt: str = Field(min_length=8, max_length=20_000)
    transition: str = Field(default="", max_length=160)
    shot_type: str = Field(default="", max_length=120)
    lens: str = Field(default="", max_length=120)
    camera_position: str = Field(default="", max_length=500)
    camera_motion: str = Field(default="", max_length=500)
    subject: str = Field(default="", max_length=500)
    action: str = Field(default="", max_length=1000)
    prompt_source: str = Field(default="", max_length=20_000)
    first_frame: str = Field(default="", max_length=500)
    last_frame: str = Field(default="", max_length=500)
    reference_bindings: dict[str, list[str]] = Field(default_factory=dict)
    continuity_in: dict[str, Any] = Field(default_factory=dict)
    continuity_out: dict[str, Any] = Field(default_factory=dict)
    video_mode: str = Field(default="", max_length=80)
    sound_cues: list[str] = Field(default_factory=list, max_length=20)
    cinematic: dict[str, Any] = Field(default_factory=dict)
    shot_contract: dict[str, Any] = Field(default_factory=dict)
    inferred_defaults: list[str] = Field(default_factory=list, max_length=20)


class StoryboardPlan(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    creative_direction: str = Field(min_length=1, max_length=1200)
    # 全片共用的角色/场景/画风描述。会逐字注入每一镜的生成提示词，这样各镜引用
    # 的是同一段文字而不是各写各的。留空表示本片没有需要跨镜冻结的主体，
    # 此时不注入任何东西，各镜提示词保持原样。
    identity_lock: str = Field(default="", max_length=1200)
    shots: list[StoryboardShot] = Field(min_length=1, max_length=12)


class WorkflowVisualContinuityReport(BaseModel):
    passed: bool
    score: float = Field(ge=0, le=1)
    identity_consistent: bool
    wardrobe_consistent: bool
    style_consistent: bool
    prop_consistent: bool
    scene_consistent: bool
    needs_human_review: bool = False
    issues: list[str] = Field(default_factory=list, max_length=20)
    summary: str = Field(min_length=1, max_length=1200)


__all__ = [
    "StoryboardPlan",
    "StoryboardShot",
    "WorkflowVisualContinuityReport",
]
