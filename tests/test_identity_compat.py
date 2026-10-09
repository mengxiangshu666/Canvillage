from novelvideo.chat.identity_compat import (
    ENV_NAME_MAP,
    EVENT_SCHEMA_MAP,
    LEGACY_TOOL_NAME_MAP,
    normalize_env_name,
    normalize_event_schema,
    normalize_prompt_marker,
    normalize_prompt_text,
    normalize_payload_schema,
    normalize_provider_name,
    normalize_tool_name,
    read_compat_env,
)


def test_all_known_legacy_tools_map_to_village_canvas_names():
    assert LEGACY_TOOL_NAME_MAP
    assert all(name.startswith("village_canvas_") for name in LEGACY_TOOL_NAME_MAP.values())
    for legacy, canonical in LEGACY_TOOL_NAME_MAP.items():
        assert normalize_tool_name(legacy) == canonical


def test_tool_normalization_preserves_canonical_and_unknown_names():
    assert normalize_tool_name("village_canvas_post") == "village_canvas_post"
    assert normalize_tool_name("some_future_tool") == "some_future_tool"
    assert normalize_tool_name("  dramaclaw_post  ") == "village_canvas_post"
    assert normalize_tool_name(None) == ""


def test_provider_normalization_handles_bare_and_custom_names():
    assert normalize_provider_name("dramaclaw") == "village-canvas"
    assert normalize_provider_name("custom:dramaclaw") == "custom:village-canvas"
    assert normalize_provider_name("custom:dramaclaw-vision") == "custom:village-canvas-vision"
    assert normalize_provider_name("custom:village-canvas") == "custom:village-canvas"
    assert normalize_provider_name("custom:future-provider") == "custom:future-provider"


def test_event_schema_normalization_preserves_unknown_schemas():
    for legacy, canonical in EVENT_SCHEMA_MAP.items():
        assert normalize_event_schema(legacy) == canonical
    assert normalize_event_schema("village_agent_event.v1") == "village_agent_event.v1"
    assert normalize_event_schema("future.event.v1") == "future.event.v1"


def test_environment_names_normalize_and_read_canonical_first():
    for legacy, canonical in ENV_NAME_MAP.items():
        assert normalize_env_name(legacy) == canonical

    assert read_compat_env(
        {"DRAMACLAW_HERMES_BASE_URL": "https://legacy.example/v1"},
        "VILLAGE_CANVAS_HERMES_BASE_URL",
    ) == "https://legacy.example/v1"
    assert read_compat_env(
        {
            "DRAMACLAW_HERMES_BASE_URL": "https://legacy.example/v1",
            "VILLAGE_CANVAS_HERMES_BASE_URL": "https://canonical.example/v1",
        },
        "VILLAGE_CANVAS_HERMES_BASE_URL",
    ) == "https://canonical.example/v1"
    assert read_compat_env(
        {"VILLAGE_CANVAS_HERMES_BASE_URL": "  "},
        "VILLAGE_CANVAS_HERMES_BASE_URL",
        default="fallback",
    ) == "fallback"


def test_prompt_markers_support_exact_and_embedded_legacy_reads():
    assert normalize_prompt_marker("[DRAMACLAW_RECOVERY_PACKET]") == "[VILLAGE_CANVAS_RECOVERY_PACKET]"
    assert normalize_prompt_marker("[UNKNOWN_MARKER]") == "[UNKNOWN_MARKER]"
    assert normalize_prompt_text(
        "before [DRAMACLAW_USER_CONTEXT] after [DRAMACLAW_INGEST_AUTOMATION]"
    ) == "before [VILLAGE_CANVAS_USER_CONTEXT] after [VILLAGE_CANVAS_INGEST_AUTOMATION]"


def test_structured_payload_normalization_is_shallow_and_preserves_ids():
    payload = normalize_payload_schema(
        {
            "schema": "dramaclaw.workflow.v1",
            "tool_name": "dramaclaw_post",
            "provider": "custom:dramaclaw",
            "run_id": "run-1",
            "nested": {"tool_name": "dramaclaw_patch"},
        }
    )
    assert payload == {
        "schema": "village_canvas.workflow.v2",
        "tool_name": "village_canvas_post",
        "provider": "custom:village-canvas",
        "run_id": "run-1",
        "nested": {"tool_name": "dramaclaw_patch"},
    }
