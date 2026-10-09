"""Structured judgment capability backed by the JEV (SystemOne) service.

The cards are frozen verbatim by ``check_agent_tool_surface.py``.
"""

from __future__ import annotations

from .spec import ToolSpec


JUDGMENT_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="judgment.ask",
        handler_name="_handle_jev_judge",
        card={
            "cost": "external_request",
            "domain": "judgment",
            "id": "judgment.ask",
            "purpose": "向 JEV 判断服务批量提交是非题、选择题与打分题，返回概率、置信度与加权分，用于拿不准时的结构化裁决。",
            "required_args": ["state", "questions"],
            "search_terms": "判断 裁决 是非 选择 打分 评估 概率 置信度 jev 批量 审核 需不需要 哪个更好",
            "side_effect": "read",
        },
        tool_name="village_canvas_jev_judge",
        description=(
            "Ask the JEV judgment service one batch of named yes/no, choice, "
            "and score questions about shared content. Returns probabilities, "
            "selected choices with confidence, and rubric-weighted scores. "
            "Use it when a call needs a structured verdict instead of a guess. "
            "JEV is a judge, not a writer: it never drafts text. API keys never "
            "appear in output."
        ),
        properties={
            "state": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "The shared content every question refers to, e.g. scene "
                    "environment contract plus per-beat visual descriptions."
                ),
            },
            "questions": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["noul", "choice", "score"],
                            "description": (
                                "noul = yes/no with probability; choice = pick "
                                "one option; score = rate on an ordered rubric."
                            ),
                        },
                        "instructions": {
                            "type": "string",
                            "description": "The question or statement to evaluate.",
                        },
                        "criteria": {
                            "description": (
                                "noul: {true, false} descriptions. choice: "
                                "{optionName: when it applies}. score: ordered "
                                "level descriptions from low to high (position "
                                "is the score, starting at 0)."
                            ),
                        },
                    },
                    "required": ["type", "instructions"],
                },
                "description": (
                    "Named questions, e.g. "
                    '{"need_reverse": {"type": "noul", "instructions": "...", '
                    '"criteria": {"true": "...", "false": "..."}}}.'
                ),
            },
        },
        required=("state", "questions"),
    ),
)
