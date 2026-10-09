from novelvideo.chat.context_budget import (
    DEFAULT_CONTEXT_LENGTH,
    MAX_CONTEXT_LENGTH,
    budget_from_metadata,
    budget_from_model_config,
    build_context_budget,
)


def test_metadata_context_and_output_limits_are_used() -> None:
    budget = budget_from_metadata(
        {
            "inputTokenLimit": 1_048_576,
            "outputTokenLimit": 16_384,
        }
    )

    assert budget.model_context_length == 1_048_576
    assert budget.max_output_tokens == 16_384
    assert budget.source == "metadata:inputTokenLimit,outputTokenLimit"
    assert budget.working_budget == 500_000
    assert budget.soft_threshold == 524_288
    assert budget.checkpoint_threshold == 734_003
    assert budget.emergency_threshold == 859_832


def test_metadata_aliases_and_model_config_contract_are_supported() -> None:
    metadata_budget = budget_from_metadata(
        {"context_window": 131_072, "max_output_tokens": 8_192}
    )
    config_budget = budget_from_model_config(
        type(
            "ModelConfig",
            (),
            {
                "context_length": 131_072,
                "max_output_tokens": 8_192,
                "context_source": "metadata:context_window",
            },
        )()
    )

    assert metadata_budget.model_context_length == config_budget.model_context_length
    assert metadata_budget.max_output_tokens == config_budget.max_output_tokens
    assert metadata_budget.working_budget == config_budget.working_budget


def test_missing_or_invalid_metadata_uses_compatible_baseline() -> None:
    missing = budget_from_metadata({})
    invalid = budget_from_metadata({"inputTokenLimit": "not-a-number"})

    assert missing.model_context_length == DEFAULT_CONTEXT_LENGTH
    assert invalid.model_context_length == DEFAULT_CONTEXT_LENGTH
    assert missing.source == "default"
    assert invalid.source == "default"


def test_unknown_model_uses_conservative_500k_long_context_baseline() -> None:
    budget = build_context_budget()

    assert budget.model_context_length == 500_000
    assert budget.working_budget == 488_736
    assert budget.soft_threshold == 250_000
    assert budget.checkpoint_threshold == 350_000
    assert budget.emergency_threshold == 410_000


def test_missing_metadata_does_not_infer_context_from_model_name() -> None:
    gemini = budget_from_metadata({}, model_id="gemini-3.7-flash")
    deepseek = budget_from_metadata({}, model_id="deepseek-v4-flash")

    assert gemini.model_context_length == DEFAULT_CONTEXT_LENGTH
    assert deepseek.model_context_length == DEFAULT_CONTEXT_LENGTH
    assert gemini.source == "default"
    assert deepseek.source == "default"


def test_context_and_output_values_are_bounded() -> None:
    budget = build_context_budget(
        context_length=9_999_999,
        max_output_tokens=9_999_999,
        source="metadata:test",
    )

    assert budget.model_context_length == MAX_CONTEXT_LENGTH
    assert budget.max_output_tokens == 32_768
    assert budget.working_budget == 500_000


def test_small_context_keeps_thresholds_ordered_and_reserves_output() -> None:
    budget = build_context_budget(context_length=16_384, max_output_tokens=8_000)

    assert budget.model_context_length == 16_384
    assert budget.soft_threshold == 16_384
    assert budget.checkpoint_threshold == 16_384
    assert budget.emergency_threshold == 16_384
    assert budget.working_budget == 4_096
