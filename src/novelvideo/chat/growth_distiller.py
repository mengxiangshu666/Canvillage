"""LLM-assisted distillation of reusable director recipes.

The distiller is deliberately a proposal boundary: it analyses a completed
turn and returns a typed candidate.  Persistence, promotion, and execution
hooks remain owned by ``memory_index`` and the workflow contracts.  This keeps
model output reviewable and makes a failed provider request non-destructive.

The model is selected through ``GROWTH_DISTILLER_MODEL`` and uses the existing
text-model registry/gateway.  No provider key or channel is created here.
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_ai import Agent
from pydantic_ai.output import PromptedOutput

GROWTH_DISTILLER_MODEL_ENV = "GROWTH_DISTILLER_MODEL"
GROWTH_DISTILLER_THINKING_LEVEL_ENV = "GROWTH_DISTILLER_THINKING_LEVEL"
GROWTH_DISTILLER_AUTO_TEXT_MODEL_ENV = "GROWTH_DISTILLER_AUTO_TEXT_MODEL"
GROWTH_DISTILLER_SCHEMA_VERSION = "xiaoshu.director_recipe_candidate.v1"


def _clean(value: object, limit: int = 2_000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _clean_list(value: object, *, limit: int = 12, item_limit: int = 360) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        text = _clean(item, item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


class RecipeSlot(BaseModel):
    """A project-specific value slot retained by a reusable recipe."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=80)
    purpose: str = Field(default="", max_length=360)
    required: bool = True
    example: str = Field(default="", max_length=360)

    @field_validator("name", "purpose", "example", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> str:
        return _clean(value, 360)


class RecipeCheck(BaseModel):
    """One deterministic check that can later be attached to an execution hook."""

    model_config = ConfigDict(extra="ignore")

    check_id: str = Field(min_length=1, max_length=80)
    condition: str = Field(min_length=1, max_length=360)
    pass_when: str = Field(min_length=1, max_length=360)

    @field_validator("check_id", "condition", "pass_when", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> str:
        return _clean(value, 360)


class DirectorRecipeCandidate(BaseModel):
    """Structured, cross-project candidate distilled from one teaching turn."""

    model_config = ConfigDict(extra="ignore")

    schema_version: str = GROWTH_DISTILLER_SCHEMA_VERSION
    title: str = Field(min_length=1, max_length=160)
    task_family: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=800)
    reusable_principles: list[str] = Field(default_factory=list)
    trigger_conditions: list[str] = Field(default_factory=list)
    prompt_structure: list[str] = Field(default_factory=list)
    slots: list[RecipeSlot] = Field(default_factory=list)
    execution_actions: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    validation_checks: list[RecipeCheck] = Field(default_factory=list)
    transferable_elements: list[str] = Field(default_factory=list)
    project_specific_elements: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_basis: list[str] = Field(default_factory=list)

    @field_validator(
        "reusable_principles",
        "trigger_conditions",
        "prompt_structure",
        "execution_actions",
        "avoid",
        "transferable_elements",
        "project_specific_elements",
        "evidence_basis",
        mode="before",
    )
    @classmethod
    def normalize_lists(cls, value: object) -> list[str]:
        return _clean_list(value)

    @model_validator(mode="after")
    def require_executable_recipe(self) -> "DirectorRecipeCandidate":
        if not self.trigger_conditions:
            raise ValueError("candidate recipe requires trigger_conditions")
        if not self.prompt_structure and not self.execution_actions:
            raise ValueError("candidate recipe requires prompt_structure or execution_actions")
        if not self.validation_checks:
            raise ValueError("candidate recipe requires validation_checks")
        if not self.evidence_basis:
            raise ValueError("candidate recipe requires evidence_basis")
        return self


class GrowthDistillationRequest(BaseModel):
    """Input contract for one LLM-assisted growth-memory pass."""

    model_config = ConfigDict(extra="ignore")

    raw_user_prompt: str = Field(default="", max_length=20_000)
    assistant_output: str = Field(default="", max_length=20_000)
    user_feedback: str = Field(default="", max_length=8_000)
    execution_result: str = Field(default="", max_length=8_000)
    project_context: str = Field(default="", max_length=8_000)
    task_family_hint: str = Field(default="", max_length=160)
    # When the current turn is feedback-only, the preceding execution is the
    # actual teaching example. Keep it typed and bounded instead of stuffing
    # an opaque transcript into raw_user_prompt.
    preceding_episode: dict[str, Any] = Field(default_factory=dict)
    existing_recipe_summaries: list[str] = Field(default_factory=list)

    @field_validator(
        "raw_user_prompt",
        "assistant_output",
        "user_feedback",
        "execution_result",
        "project_context",
        "task_family_hint",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value: object) -> str:
        return _clean(value, 20_000)

    @field_validator("preceding_episode", mode="before")
    @classmethod
    def normalize_preceding_episode(cls, value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        allowed = {
            "episode_id",
            "turn_id",
            "objective",
            "response_summary",
            "task_stage",
            "outcome",
            "verified",
            "run_id",
            "evidence_ref",
            "memory_ids",
        }
        normalized: dict[str, Any] = {}
        for key in allowed:
            if key not in value:
                continue
            item = value[key]
            if key in {"objective", "response_summary", "task_stage", "outcome", "run_id", "evidence_ref"}:
                item = _clean(item, 1_600 if key == "response_summary" else 1_200)
            elif key in {"episode_id"}:
                try:
                    item = int(item)
                except (TypeError, ValueError):
                    continue
            elif key == "verified":
                item = bool(item)
            elif key == "memory_ids":
                item = [int(entry) for entry in item if str(entry).isdigit()][:24] if isinstance(item, list) else []
            if item not in ("", [], None):
                normalized[key] = item
        return normalized

    @field_validator("existing_recipe_summaries", mode="before")
    @classmethod
    def normalize_existing(cls, value: object) -> list[str]:
        return _clean_list(value, limit=8, item_limit=700)


class GrowthDistillationResult(BaseModel):
    """Safe result envelope returned to the memory pipeline."""

    schema_version: str = GROWTH_DISTILLER_SCHEMA_VERSION
    decision: Literal["candidate", "noop", "needs_review"]
    feedback_kind: Literal["positive", "negative", "instruction", "mixed", "none"] = "none"
    reason: str = Field(default="", max_length=800)
    project_facts: list[str] = Field(default_factory=list)
    candidate: DirectorRecipeCandidate | None = None

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: object) -> str:
        return _clean(value, 800)

    @field_validator("project_facts", mode="before")
    @classmethod
    def normalize_facts(cls, value: object) -> list[str]:
        return _clean_list(value, limit=12)

    @model_validator(mode="after")
    def validate_decision_payload(self) -> "GrowthDistillationResult":
        if self.decision == "candidate" and self.candidate is None:
            raise ValueError("candidate decision requires candidate payload")
        if self.decision != "candidate" and self.candidate is not None:
            raise ValueError("non-candidate decision cannot include candidate payload")
        return self


GROWTH_DISTILLER_SYSTEM_PROMPT = """你是村长无限画布的成长记忆蒸馏师。

你负责把一次真实的导演交流、提示词修改和执行反馈提炼成“可跨项目复用的导演配方候选”，不是复述聊天，也不是抓关键词。

严格遵守：
1. 先区分原始反馈、项目事实和可复用方法；雨夜古刹、角色姓名、具体资产等项目内容只能放 project_specific_elements 或 project_facts。
2. “这次很好/不错/继续”只能作为正向证据；只有用户明确采用某种方法，或上下文能抽象出可执行方法时，才输出 candidate。
3. 可复用方法必须同时具备触发条件、具体动作和可验证结果；缺任何一项就 decision=noop 或 needs_review。
4. 不要编造没有出现在输入中的执行结果、模型能力、用户偏好或证据。
5. prompt_structure 要描述结构和顺序，slots 要把作品内容抽象成可替换变量；不要复制一整段原始 Prompt。
6. validation_checks 必须是将来可以由程序或执行器检查的条件。候选只提议，不直接晋升、不直接写画布、不启动媒体任务。
7. 当 user_feedback 是对上一轮结果的评价时，优先使用 preceding_episode 还原“用户认可/否定的具体做法”；不要把反馈短句误当成完整原始任务。

只输出符合 JSON Schema 的 JSON 对象，不要 Markdown、解释或额外文字。
"""


def build_growth_distillation_prompt(request: GrowthDistillationRequest) -> str:
    payload = {
        "raw_user_prompt": request.raw_user_prompt,
        "assistant_output": request.assistant_output,
        "user_feedback": request.user_feedback,
        "execution_result": request.execution_result,
        "project_context": request.project_context,
        "task_family_hint": request.task_family_hint,
        "preceding_episode": request.preceding_episode,
        "existing_recipe_summaries": request.existing_recipe_summaries,
        "required_output": GrowthDistillationResult.model_json_schema(),
    }
    return f"输入与输出合同：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


def growth_distiller_model_ref() -> str:
    """Return the logical model selection without exposing credentials.

    A dedicated ``GROWTH_DISTILLER_MODEL`` still wins.  When it is omitted,
    the separately configured default text model is the deterministic fallback
    for this role.  This keeps growth distillation on the text-model lane (not
    the Agent or media lane) while preventing durable teaching events from
    remaining pending forever simply because the optional override was never
    copied into the launcher environment.
    """

    explicit = str(os.environ.get(GROWTH_DISTILLER_MODEL_ENV, "")).strip()
    if explicit:
        return explicit
    auto_text = str(
        os.environ.get(GROWTH_DISTILLER_AUTO_TEXT_MODEL_ENV, "1")
    ).strip().lower()
    if auto_text in {"0", "false", "no", "off"}:
        return ""
    try:
        from novelvideo.generators.direct_models import resolve_direct_model

        model = resolve_direct_model("text")
    except Exception:  # pragma: no cover - configuration probe is best effort
        return ""
    return str(getattr(model, "catalog_id", "") or "").strip()


def growth_distiller_contract() -> dict[str, Any]:
    """Expose the role contract for model-center/UI inspection."""

    return {
        "role": "growth_distiller",
        "modelEnv": GROWTH_DISTILLER_MODEL_ENV,
        "thinkingLevelEnv": GROWTH_DISTILLER_THINKING_LEVEL_ENV,
        "modelRef": growth_distiller_model_ref(),
        "modelSource": (
            "explicit"
            if str(os.environ.get(GROWTH_DISTILLER_MODEL_ENV, "")).strip()
            else "default_text_model"
            if growth_distiller_model_ref()
            else "unconfigured"
        ),
        "autoTextModelEnv": GROWTH_DISTILLER_AUTO_TEXT_MODEL_ENV,
        "transport": "existing_text_model_registry",
        "credentialSource": "existing_model_gateway",
        "sideEffects": ["returns_candidate_only"],
        "schemaVersion": GROWTH_DISTILLER_SCHEMA_VERSION,
    }


def create_growth_distiller_agent() -> Agent:
    """Create the dedicated role on the existing text-model route."""

    from novelvideo.config import (
        get_newapi_text_pydantic_model,
        get_newapi_text_pydantic_model_settings,
    )

    model_ref = growth_distiller_model_ref() or None
    model_settings = get_newapi_text_pydantic_model_settings(
        GROWTH_DISTILLER_THINKING_LEVEL_ENV,
        "high",
    )
    kwargs: dict[str, Any] = {}
    if model_settings is not None:
        kwargs["model_settings"] = model_settings
    return Agent(
        get_newapi_text_pydantic_model(
            GROWTH_DISTILLER_MODEL_ENV,
            "",
            model_name_override=model_ref,
            timeout_seconds_override=float(
                os.environ.get("GROWTH_DISTILLER_TIMEOUT_SECONDS", "180")
            ),
        ),
        system_prompt=GROWTH_DISTILLER_SYSTEM_PROMPT,
        output_type=PromptedOutput(
            GrowthDistillationResult,
            name="成长记忆候选配方",
            description="只返回候选、项目事实或无需沉淀的结构化结论。",
            template=(
                "只输出一个有效 JSON 对象，不要 Markdown、解释或额外文字。\n"
                "JSON Schema:\n{schema}"
            ),
        ),
        output_retries=2,
        name="成长记忆蒸馏师",
        **kwargs,
    )


async def distill_growth_memory(
    request: GrowthDistillationRequest | dict[str, Any],
    *,
    agent: Any = None,
) -> GrowthDistillationResult:
    """Run one bounded model pass and return a typed candidate envelope.

    ``agent`` is injectable for deterministic tests. Provider/parse errors
    become ``needs_review`` and never mutate memory or trigger execution.
    """

    normalized = (
        request
        if isinstance(request, GrowthDistillationRequest)
        else GrowthDistillationRequest.model_validate(request)
    )
    if not any(
        (
            normalized.raw_user_prompt,
            normalized.assistant_output,
            normalized.user_feedback,
            normalized.execution_result,
        )
    ):
        return GrowthDistillationResult(decision="noop", reason="empty_teaching_episode")

    try:
        runtime_agent = agent or create_growth_distiller_agent()
        response = await runtime_agent.run(build_growth_distillation_prompt(normalized))
        output = response.output
        return (
            output
            if isinstance(output, GrowthDistillationResult)
            else GrowthDistillationResult.model_validate(output)
        )
    except Exception as exc:  # noqa: BLE001 - provider failures are reviewable state
        return GrowthDistillationResult(
            decision="needs_review",
            reason=f"growth_distiller_failed:{type(exc).__name__}",
        )


__all__ = [
    "DirectorRecipeCandidate",
    "GROWTH_DISTILLER_MODEL_ENV",
    "GROWTH_DISTILLER_AUTO_TEXT_MODEL_ENV",
    "GROWTH_DISTILLER_SCHEMA_VERSION",
    "GrowthDistillationRequest",
    "GrowthDistillationResult",
    "RecipeCheck",
    "RecipeSlot",
    "build_growth_distillation_prompt",
    "create_growth_distiller_agent",
    "distill_growth_memory",
    "growth_distiller_contract",
    "growth_distiller_model_ref",
]
