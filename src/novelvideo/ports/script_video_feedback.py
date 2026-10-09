"""Bounded user observations; never proof of media repair or QC."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ScriptVideoFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    issue_id: str = Field(min_length=1, max_length=200)
    shot_id: str = Field(min_length=1, max_length=200)
    video_node_id: str = Field(min_length=1, max_length=200)
    row_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    timestamp_seconds: float = Field(ge=0)
    category: Literal["artifact", "continuity", "identity", "action", "performance",
                      "composition", "prop_state", "lighting", "sound", "story_clarity"]
    description: str = Field(min_length=1, max_length=2000)
    audience_effect: str = Field(default="", max_length=2000)
    repair_direction: str = Field(default="", max_length=2000)
