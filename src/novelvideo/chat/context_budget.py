"""Model-aware context budgets for the Village Canvas Agent.

The upstream model catalog is the preferred source of truth.  Environment
values and the compatibility default are only used when a model has not yet
returned usable metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


MIN_CONTEXT_LENGTH = 16_384
# Upstream relays often omit context metadata even when the selected model has
# a much larger window. Use a conservative long-context baseline for unknown
# Agent models; explicit catalog metadata still wins and smaller advertised
# windows remain honored exactly.
DEFAULT_CONTEXT_LENGTH = 500_000
MAX_CONTEXT_LENGTH = 1_048_576
DEFAULT_MAX_OUTPUT_TOKENS = 3_072
MAX_OUTPUT_TOKENS = 32_768
OPERATIONAL_WORKING_CAP = 500_000
FIXED_OVERHEAD_TOKENS = 4_096
SAFETY_MARGIN_TOKENS = 4_096

_CONTEXT_KEYS = (
    "inputTokenLimit",
    "contextLength",
    "context_length",
    "contextWindow",
    "context_window",
    "maxContextTokens",
    "max_context_tokens",
    "input_token_limit",
)
_OUTPUT_KEYS = (
    "outputTokenLimit",
    "maxOutputTokens",
    "max_output_tokens",
    "output_token_limit",
)

def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _bounded_context_length(value: object) -> int | None:
    parsed = _positive_int(value)
    if parsed is None:
        return None
    return min(max(parsed, MIN_CONTEXT_LENGTH), MAX_CONTEXT_LENGTH)


def _bounded_output_tokens(value: object, context_length: int) -> int | None:
    parsed = _positive_int(value)
    if parsed is None:
        return None
    context_cap = max(256, context_length // 4)
    return min(parsed, MAX_OUTPUT_TOKENS, context_cap)


def _first_metadata_value(metadata: Mapping[str, object], keys: tuple[str, ...]) -> tuple[object, str]:
    for key in keys:
        if key in metadata:
            return metadata[key], key
    return None, ""


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """Safe runtime limits derived from one model's advertised contract."""

    model_context_length: int
    max_output_tokens: int
    source: str
    working_budget: int
    soft_threshold: int
    checkpoint_threshold: int
    emergency_threshold: int

    @property
    def compression_threshold_ratio(self) -> float:
        return self.soft_threshold / self.model_context_length

    @property
    def compression_target_ratio(self) -> float:
        return 0.20

    def to_dict(self) -> dict[str, int | str | float]:
        return {
            "model_context_length": self.model_context_length,
            "max_output_tokens": self.max_output_tokens,
            "source": self.source,
            "working_budget": self.working_budget,
            "soft_threshold": self.soft_threshold,
            "checkpoint_threshold": self.checkpoint_threshold,
            "emergency_threshold": self.emergency_threshold,
            "compression_threshold_ratio": self.compression_threshold_ratio,
            "compression_target_ratio": self.compression_target_ratio,
        }


def build_context_budget(
    *,
    context_length: object = None,
    max_output_tokens: object = None,
    source: str = "default",
) -> ContextBudget:
    """Build a bounded budget without trusting arbitrary provider values."""

    normalized_context = _bounded_context_length(context_length)
    if normalized_context is None:
        normalized_context = DEFAULT_CONTEXT_LENGTH
        source = "default"
    normalized_output = _bounded_output_tokens(max_output_tokens, normalized_context)
    if normalized_output is None:
        normalized_output = min(
            DEFAULT_MAX_OUTPUT_TOKENS,
            max(256, normalized_context // 4),
        )
    soft_threshold = max(
        MIN_CONTEXT_LENGTH,
        int(normalized_context * 0.50),
    )
    checkpoint_threshold = min(
        normalized_context,
        max(soft_threshold + 1, int(normalized_context * 0.70)),
    )
    emergency_threshold = min(
        normalized_context,
        max(checkpoint_threshold + 1, int(normalized_context * 0.82)),
    )
    available = max(
        256,
        normalized_context
        - normalized_output
        - FIXED_OVERHEAD_TOKENS
        - SAFETY_MARGIN_TOKENS,
    )
    working_budget = min(available, OPERATIONAL_WORKING_CAP)
    return ContextBudget(
        model_context_length=normalized_context,
        max_output_tokens=normalized_output,
        source=source or "default",
        working_budget=working_budget,
        soft_threshold=soft_threshold,
        checkpoint_threshold=checkpoint_threshold,
        emergency_threshold=emergency_threshold,
    )


def budget_from_metadata(
    metadata: Mapping[str, object] | None,
    *,
    model_id: object = None,
) -> ContextBudget:
    """Resolve limits from cached upstream metadata, never from model names."""

    values = metadata if isinstance(metadata, Mapping) else {}
    context_value, context_key = _first_metadata_value(values, _CONTEXT_KEYS)
    output_value, output_key = _first_metadata_value(values, _OUTPUT_KEYS)
    del model_id
    context = _bounded_context_length(context_value)
    if context is None:
        return build_context_budget()
    source = f"metadata:{context_key}"
    if output_key:
        source = f"{source},{output_key}"
    return build_context_budget(
        context_length=context,
        max_output_tokens=output_value,
        source=source,
    )


def budget_from_model_config(config: object | None) -> ContextBudget:
    """Resolve the already-normalized contract attached to a model config."""

    context = getattr(config, "context_length", None)
    output = getattr(config, "max_output_tokens", None)
    source = str(getattr(config, "context_source", "") or "configured")
    if _bounded_context_length(context) is None:
        return build_context_budget()
    return build_context_budget(
        context_length=context,
        max_output_tokens=output,
        source=source,
    )


__all__ = [
    "ContextBudget",
    "DEFAULT_CONTEXT_LENGTH",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "MAX_CONTEXT_LENGTH",
    "MIN_CONTEXT_LENGTH",
    "OPERATIONAL_WORKING_CAP",
    "budget_from_metadata",
    "budget_from_model_config",
    "build_context_budget",
]
