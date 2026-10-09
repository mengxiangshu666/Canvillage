"""LLM-backed Story Lab stage generation service."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent

from novelvideo.generators.direct_models import (
    get_direct_pydantic_model,
    is_direct_model_runtime_ready,
    resolve_direct_model,
)
from novelvideo.project_context import ProjectContext
from novelvideo.story_lab.models import (
    STAGE_RESULT_MODELS,
    StoryLabStage,
    StoryLabStageArtifact,
)
from novelvideo.story_lab.persistence import StoryLabRepository, StoryLabStageMissingError
from novelvideo.story_lab.prompts import PROMPT_VERSIONS, SYSTEM_PROMPTS, build_stage_request

class StoryLabStageDependencyError(ValueError):
    pass


def _required_upstream(stage: StoryLabStage) -> tuple[StoryLabStage, ...]:
    return {
        StoryLabStage.BIBLE: (),
        StoryLabStage.OUTLINE: (StoryLabStage.BIBLE,),
        StoryLabStage.DRAFT: (StoryLabStage.BIBLE, StoryLabStage.OUTLINE),
        StoryLabStage.AUDIT: (
            StoryLabStage.BIBLE,
            StoryLabStage.OUTLINE,
            StoryLabStage.DRAFT,
        ),
    }[stage]


def _load_upstream(repository: StoryLabRepository, stage: StoryLabStage) -> dict[str, Any]:
    upstream: dict[str, Any] = {}
    for dependency in _required_upstream(stage):
        try:
            upstream[dependency.value] = repository.require_artifact(dependency).result
        except StoryLabStageMissingError as exc:
            raise StoryLabStageDependencyError(
                f"Generate '{dependency.value}' before '{stage.value}'"
            ) from exc
    return upstream


async def _run_structured_agent(
    *, stage: StoryLabStage, request: str, output_type: type[BaseModel]
) -> tuple[BaseModel, str]:
    direct_model = resolve_direct_model("text", None)
    if direct_model is None or not is_direct_model_runtime_ready(direct_model):
        raise RuntimeError("故事阶段需要一个已启用并通过检测的直连文字模型")
    runtime_model = get_direct_pydantic_model(
        "text", f"direct/{direct_model.registry_id}"
    )
    if runtime_model is None:
        raise RuntimeError("直连文字模型运行时初始化失败")
    agent = Agent(
        runtime_model,
        system_prompt=SYSTEM_PROMPTS[stage],
        output_type=output_type,
        output_retries=3,
        name=f"故事 Agent-{stage.value}",
    )
    response = await agent.run(request)
    return response.output, direct_model.catalog_id


async def generate_story_lab_stage(
    ctx: ProjectContext,
    stage: StoryLabStage | str,
    *,
    instructions: str = "",
) -> StoryLabStageArtifact:
    validated_stage = StoryLabStage(stage)
    repository = StoryLabRepository(ctx)
    config = repository.require_config()
    upstream = _load_upstream(repository, validated_stage)
    request = build_stage_request(
        stage=validated_stage,
        config=config,
        upstream=upstream,
        instructions=instructions,
    )
    output, model_id = await _run_structured_agent(
        stage=validated_stage,
        request=request,
        output_type=STAGE_RESULT_MODELS[validated_stage],
    )
    artifact = StoryLabStageArtifact(
        stage=validated_stage,
        prompt_version=PROMPT_VERSIONS[validated_stage],
        model=model_id,
        result=output.model_dump(mode="json"),
        instructions=instructions,
    )
    return repository.save_artifact(artifact)
