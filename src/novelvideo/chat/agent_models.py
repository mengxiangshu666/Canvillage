"""Direct model catalog for the in-process Village Canvas Agent."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from novelvideo.chat.context_budget import budget_from_metadata
from novelvideo.generators.direct_model_capability_cache import (
    get_cached_direct_model_capability,
)
from novelvideo.generators.direct_models import (
    is_direct_model_runtime_ready,
    list_direct_models,
    resolve_direct_model,
)
from novelvideo.generators.model_contracts import (
    DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES,
    DIRECT_MODEL_PROTOCOL_GEMINI,
    DIRECT_MODEL_PROTOCOL_OLLAMA_OPENAI,
    DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE,
)


_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,191}$")
_DIRECT_AGENT_PREFIX = "direct-agent:"


@dataclass(frozen=True, slots=True)
class VillageAgentModel:
    id: str
    label: str
    description: str
    tier: str
    reasoning: bool = False
    default: bool = False
    context_length: int = 65_536
    max_output_tokens: int = 3_072
    context_source: str = "default"

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "tier": self.tier,
            "reasoning": self.reasoning,
            "default": self.default,
            "contextLength": self.context_length,
            "maxOutputTokens": self.max_output_tokens,
            "contextSource": self.context_source,
        }


@dataclass(frozen=True, slots=True)
class DirectVillageAgentModelConfig:
    """One resolved Agent model; credentials never enter chat history."""

    id: str
    label: str
    model_id: str
    base_url: str
    api_key: str
    protocol: str
    context_length: int = 65_536
    max_output_tokens: int = 3_072
    context_source: str = "default"

    def fingerprint_material(self) -> str:
        return (
            f"{self.id}\n{self.model_id}\n{self.base_url}\n{self.api_key}\n"
            f"{self.protocol}\n{self.context_length}\n{self.max_output_tokens}\n"
            f"{self.context_source}"
        )


def _clean_model_id(value: object) -> str:
    model_id = str(value or "").strip()
    return model_id if _MODEL_ID_RE.fullmatch(model_id) else ""


def _agent_tool_evidence_ok(model) -> bool:
    cached = get_cached_direct_model_capability(
        base_url=model.base_url,
        kind="agent",
        upstream_model=model.upstream_model,
    )
    return cached.get("toolCallingVerified") is not False


def _runtime_ready_models():
    return tuple(
        item
        for item in list_direct_models("agent", include_disabled=False)
        if is_direct_model_runtime_ready(item) and _agent_tool_evidence_ok(item)
    )


def _resolve_runtime_ready_model(model_id: str | None):
    direct = resolve_direct_model("agent", model_id or None)
    if (
        direct is not None
        and is_direct_model_runtime_ready(direct)
        and _agent_tool_evidence_ok(direct)
    ):
        return direct
    return None


def list_village_agent_models() -> list[VillageAgentModel]:
    models: list[VillageAgentModel] = []
    direct_models = _runtime_ready_models()
    direct_default_id = next(
        (item.catalog_id for item in direct_models if item.is_default),
        "",
    )
    for direct in direct_models:
        cached = get_cached_direct_model_capability(
            base_url=direct.base_url,
            kind="agent",
            upstream_model=direct.upstream_model,
        )
        budget = budget_from_metadata(
            cached.get("modelMetadata"),
            model_id=direct.upstream_model,
        )
        models.append(
            VillageAgentModel(
                id=direct.catalog_id,
                label=direct.label,
                description=f"直连 Agent 模型 · {direct.upstream_model}",
                tier="direct",
                reasoning=True,
                default=direct.catalog_id == direct_default_id,
                context_length=budget.model_context_length,
                max_output_tokens=budget.max_output_tokens,
                context_source=budget.source,
            )
        )
    if models and not any(model.default for model in models):
        first = models[0]
        models[0] = VillageAgentModel(
            id=first.id,
            label=first.label,
            description=first.description,
            tier=first.tier,
            reasoning=first.reasoning,
            default=True,
            context_length=first.context_length,
            max_output_tokens=first.max_output_tokens,
            context_source=first.context_source,
        )
    return models


def resolve_village_agent_model(model_id: str | None) -> str:
    requested = _clean_model_id(model_id)
    models = list_village_agent_models()
    if not models:
        raise ValueError("尚未配置可用的小树模型，请先在模型中心配置 Agent 模型并检测。")
    if not requested:
        return next(model.id for model in models if model.default)
    if any(model.id == requested for model in models):
        return requested
    raise ValueError("所选小树模型当前不可用，请刷新模型列表后重试。")


def resolve_village_direct_agent_model(
    model_id: str | None,
    config: Mapping[str, object] | None,
) -> tuple[str, DirectVillageAgentModelConfig | None]:
    """Resolve a server-owned direct model and ignore legacy browser payloads."""

    del config
    requested = _clean_model_id(model_id)
    direct = _resolve_runtime_ready_model(requested or None)
    if direct is None:
        if requested.startswith(("direct/", _DIRECT_AGENT_PREFIX)):
            raise ValueError(
                "所选小树模型当前不可用（可能已停用或尚未通过检测），"
                "请在模型中心确认该模型可用后重试。"
            )
        raise ValueError("尚未配置可用的小树模型，请先在模型中心配置 Agent 模型并检测。")
    if direct.protocol not in {
        DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE,
        DIRECT_MODEL_PROTOCOL_OLLAMA_OPENAI,
        DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES,
        DIRECT_MODEL_PROTOCOL_GEMINI,
    }:
        raise ValueError(f"小树当前未接通该 Agent 协议：{direct.protocol}")
    cached = get_cached_direct_model_capability(
        base_url=direct.base_url,
        kind="agent",
        upstream_model=direct.upstream_model,
    )
    budget = budget_from_metadata(
        cached.get("modelMetadata"),
        model_id=direct.upstream_model,
    )
    return direct.catalog_id, DirectVillageAgentModelConfig(
        id=direct.catalog_id,
        label=direct.label[:120],
        model_id=direct.upstream_model,
        base_url=direct.base_url,
        api_key=direct.api_key,
        protocol=direct.protocol,
        context_length=budget.model_context_length,
        max_output_tokens=budget.max_output_tokens,
        context_source=budget.source,
    )


__all__ = [
    "DirectVillageAgentModelConfig",
    "VillageAgentModel",
    "list_village_agent_models",
    "resolve_village_agent_model",
    "resolve_village_direct_agent_model",
]
