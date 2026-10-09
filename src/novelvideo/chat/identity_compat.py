"""Identity compatibility helpers for the Village Canvas migration.

The runtime writes canonical Village Canvas identifiers.  These helpers keep
legacy DramaClaw identifiers readable while the workspace, event and session
migrations are introduced in separate, verifiable steps.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CANONICAL_PRODUCT_ID = "village-canvas"
LEGACY_PRODUCT_ID = "dramaclaw"
CANONICAL_WORKFLOW_SCHEMA = "village_canvas.workflow.v2"
CANONICAL_CHAT_RECOVERY_SCHEMA = "village_canvas.chat_recovery.v2"

LEGACY_TOOL_NAME_MAP: dict[str, str] = {
    "dramaclaw_get": "village_canvas_get",
    "dramaclaw_post": "village_canvas_post",
    "dramaclaw_patch": "village_canvas_patch",
    "dramaclaw_delete": "village_canvas_delete",
    "dramaclaw_tavily_search": "village_canvas_tavily_search",
    "dramaclaw_pipeline_status": "village_canvas_pipeline_status",
    "dramaclaw_story_lab_get": "village_canvas_story_lab_get",
    "dramaclaw_build_characters": "village_canvas_build_characters",
    "dramaclaw_plan_episodes": "village_canvas_plan_episodes",
    "dramaclaw_generate_script": "village_canvas_generate_script",
    "dramaclaw_plan_identities": "village_canvas_plan_identities",
    "dramaclaw_plan_scenes": "village_canvas_plan_scenes",
    "dramaclaw_plan_props": "village_canvas_plan_props",
    "dramaclaw_generate_scene_master": "village_canvas_generate_scene_master",
    "dramaclaw_generate_scene_reverse": "village_canvas_generate_scene_reverse",
    "dramaclaw_generate_sketches": "village_canvas_generate_sketches",
    "dramaclaw_detect_sketch_identities": "village_canvas_detect_sketch_identities",
    "dramaclaw_optimize_video_global": "village_canvas_optimize_video_global",
    "dramaclaw_generate_audio": "village_canvas_generate_audio",
    "dramaclaw_render_first_frames": "village_canvas_render_first_frames",
    "dramaclaw_compose_episode": "village_canvas_compose_episode",
    "dramaclaw_generate_portrait": "village_canvas_generate_portrait",
    "dramaclaw_generate_identity_image": "village_canvas_generate_identity_image",
    "dramaclaw_start_single_video": "village_canvas_start_single_video",
    "dramaclaw_start_production_run": "village_canvas_start_production_run",
    "dramaclaw_command_production_run": "village_canvas_command_production_run",
    "dramaclaw_get_production_control": "village_canvas_get_production_control",
    "dramaclaw_list_workflows": "village_canvas_list_workflows",
    "dramaclaw_list_workflow_runs": "village_canvas_list_workflow_runs",
    "dramaclaw_start_workflow_run": "village_canvas_start_workflow_run",
    "dramaclaw_get_workflow_run": "village_canvas_get_workflow_run",
    "dramaclaw_update_workflow_run": "village_canvas_update_workflow_run",
    "dramaclaw_command_workflow_run": "village_canvas_command_workflow_run",
    "dramaclaw_list_tasks": "village_canvas_list_tasks",
    "dramaclaw_get_task": "village_canvas_get_task",
    "dramaclaw_get_episode_script": "village_canvas_get_episode_script",
    "dramaclaw_list_ingest_uploads": "village_canvas_list_ingest_uploads",
    "dramaclaw_story_lab_save": "village_canvas_story_lab_save",
    "dramaclaw_story_lab_generate": "village_canvas_story_lab_generate",
    "dramaclaw_story_lab_publish": "village_canvas_story_lab_publish",
    "dramaclaw_get_sketches": "village_canvas_get_sketches",
    "dramaclaw_get_sketch_candidates": "village_canvas_get_sketch_candidates",
    "dramaclaw_get_first_frames": "village_canvas_get_first_frames",
    "dramaclaw_get_scene_images": "village_canvas_get_scene_images",
    "dramaclaw_get_character_media": "village_canvas_get_character_media",
    "dramaclaw_get_episode_media": "village_canvas_get_episode_media",
    "dramaclaw_get_final_video": "village_canvas_get_final_video",
}

PROVIDER_NAME_MAP: dict[str, str] = {
    "dramaclaw": "village-canvas",
    "dramaclaw-vision": "village-canvas-vision",
}

EVENT_SCHEMA_MAP: dict[str, str] = {
    "dramaclaw.chat_recovery.v1": CANONICAL_CHAT_RECOVERY_SCHEMA,
    "dramaclaw.workflow.v1": CANONICAL_WORKFLOW_SCHEMA,
}

CANONICAL_RECOVERY_OPEN_MARKER = "[VILLAGE_CANVAS_RECOVERY_PACKET]"
CANONICAL_RECOVERY_CLOSE_MARKER = "[/VILLAGE_CANVAS_RECOVERY_PACKET]"
LEGACY_RECOVERY_OPEN_MARKER = "[DRAMACLAW_RECOVERY_PACKET]"
LEGACY_RECOVERY_CLOSE_MARKER = "[/DRAMACLAW_RECOVERY_PACKET]"

ENV_NAME_MAP: dict[str, str] = {
    "DRAMACLAW_API_URL": "VILLAGE_CANVAS_API_URL",
    "DRAMACLAW_USER": "VILLAGE_CANVAS_USER",
    "DRAMACLAW_AGENT_TOKEN": "VILLAGE_CANVAS_AGENT_TOKEN",
    "DRAMACLAW_AGENT_SCOPE": "VILLAGE_CANVAS_AGENT_SCOPE",
    "DRAMACLAW_PROJECT_ID": "VILLAGE_CANVAS_PROJECT_ID",
    "DRAMACLAW_PROJECT": "VILLAGE_CANVAS_PROJECT",
    "DRAMACLAW_PROJECT_NAME": "VILLAGE_CANVAS_PROJECT_NAME",
    "DRAMACLAW_PROJECT_OWNER": "VILLAGE_CANVAS_PROJECT_OWNER",
    "DRAMACLAW_PROJECT_OUTPUT_DIR": "VILLAGE_CANVAS_PROJECT_OUTPUT_DIR",
    "DRAMACLAW_PROJECT_STATE_DIR": "VILLAGE_CANVAS_PROJECT_STATE_DIR",
    "DRAMACLAW_PROJECT_RUNTIME_DIR": "VILLAGE_CANVAS_PROJECT_RUNTIME_DIR",
    "DRAMACLAW_NOVELVIDEO_IMPORT_ROOT": "VILLAGE_CANVAS_NOVELVIDEO_IMPORT_ROOT",
    "DRAMACLAW_HERMES_COCKPIT_CONFIG": "VILLAGE_CANVAS_HERMES_COCKPIT_CONFIG",
    "DRAMACLAW_HERMES_BASE_URL": "VILLAGE_CANVAS_HERMES_BASE_URL",
    "DRAMACLAW_HERMES_API_KEY": "VILLAGE_CANVAS_HERMES_API_KEY",
    "DRAMACLAW_HERMES_COCKPIT_LOCAL": "VILLAGE_CANVAS_HERMES_COCKPIT_LOCAL",
    "DRAMACLAW_HERMES_FAST_URL": "VILLAGE_CANVAS_HERMES_FAST_URL",
    "DRAMACLAW_HERMES_FAST_KEY": "VILLAGE_CANVAS_HERMES_FAST_KEY",
    "DRAMACLAW_HERMES_PYTHON": "VILLAGE_CANVAS_HERMES_PYTHON",
    "DRAMACLAW_HERMES_RUNTIME_ROOT": "VILLAGE_CANVAS_HERMES_RUNTIME_ROOT",
    "DRAMACLAW_TAVILY_MCP_ROOT": "VILLAGE_CANVAS_TAVILY_MCP_ROOT",
    "DRAMACLAW_HERMES_DIRECT_AGENT_MODEL_ID": "VILLAGE_CANVAS_DIRECT_AGENT_MODEL_ID",
    "DRAMACLAW_DIRECT_AGENT_MODEL_ID": "VILLAGE_CANVAS_DIRECT_AGENT_MODEL_ID",
    "DRAMACLAW_DIRECT_AGENT_MODEL_LABEL": "VILLAGE_CANVAS_DIRECT_AGENT_MODEL_LABEL",
    "DRAMACLAW_DIRECT_AGENT_BASE_URL": "VILLAGE_CANVAS_DIRECT_AGENT_BASE_URL",
    "DRAMACLAW_DIRECT_AGENT_PROTOCOL": "VILLAGE_CANVAS_DIRECT_AGENT_PROTOCOL",
    "DRAMACLAW_DIRECT_AGENT_API_MODE": "VILLAGE_CANVAS_DIRECT_AGENT_API_MODE",
}

PROMPT_MARKER_MAP: dict[str, str] = {
    "[DRAMACLAW_USER_CONTEXT]": "[VILLAGE_CANVAS_USER_CONTEXT]",
    "[DRAMACLAW_RECOVERY_PACKET]": "[VILLAGE_CANVAS_RECOVERY_PACKET]",
    "[/DRAMACLAW_RECOVERY_PACKET]": "[/VILLAGE_CANVAS_RECOVERY_PACKET]",
    "[DRAMACLAW_REINGEST_CONFIRMATION]": "[VILLAGE_CANVAS_REINGEST_CONFIRMATION]",
    "[/DRAMACLAW_REINGEST_CONFIRMATION]": "[/VILLAGE_CANVAS_REINGEST_CONFIRMATION]",
    "[DRAMACLAW_REINGEST_CANCELLED]": "[VILLAGE_CANVAS_REINGEST_CANCELLED]",
    "[/DRAMACLAW_REINGEST_CANCELLED]": "[/VILLAGE_CANVAS_REINGEST_CANCELLED]",
    "[DRAMACLAW_INGEST_AUTOMATION]": "[VILLAGE_CANVAS_INGEST_AUTOMATION]",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_tool_name(raw_name: Any) -> str:
    """Return the canonical tool name, preserving unknown names verbatim."""

    name = _text(raw_name)
    mapped = LEGACY_TOOL_NAME_MAP.get(name)
    if mapped is not None:
        return mapped
    if name.startswith("dramaclaw_"):
        return f"village_canvas_{name.removeprefix('dramaclaw_')}"
    return name


def normalize_provider_name(raw_name: Any) -> str:
    """Normalize bare and ``custom:`` provider names without guessing aliases."""

    name = _text(raw_name)
    if name.startswith("custom:"):
        provider_name = name.removeprefix("custom:")
        return f"custom:{PROVIDER_NAME_MAP.get(provider_name, provider_name)}"
    return PROVIDER_NAME_MAP.get(name, name)


def normalize_event_schema(raw_schema: Any) -> str:
    """Map legacy event schemas to the canonical write-side schema."""

    schema = _text(raw_schema)
    return EVENT_SCHEMA_MAP.get(schema, schema)


def normalize_env_name(raw_name: Any) -> str:
    """Return the canonical environment variable name for read/write adapters."""

    name = _text(raw_name)
    return ENV_NAME_MAP.get(name, name)


def read_compat_env(
    environ: Mapping[str, Any],
    canonical_name: str,
    default: Any = None,
) -> Any:
    """Read a canonical env var first, then its legacy aliases.

    An explicitly populated canonical value always wins.  The helper does not
    mutate ``environ`` and therefore is safe to use before the single-write
    migration is wired into the process launcher.
    """

    canonical = normalize_env_name(canonical_name)
    names = [canonical, *legacy_env_names(canonical)]
    for name in names:
        value = environ.get(name)
        if value is not None and _text(value):
            return value
    return default


def legacy_env_names(canonical_name: str) -> tuple[str, ...]:
    """Return legacy aliases for a canonical environment variable."""

    canonical = normalize_env_name(canonical_name)
    return tuple(
        legacy for legacy, target in ENV_NAME_MAP.items() if target == canonical
    )


def normalize_prompt_marker(raw_marker: Any) -> str:
    """Normalize one exact prompt marker while preserving free-form text."""

    marker = _text(raw_marker)
    return PROMPT_MARKER_MAP.get(marker, marker)


def normalize_prompt_text(raw_text: Any) -> str:
    """Normalize known legacy markers inside a prompt or recovery packet."""

    text = str(raw_text or "")
    for legacy, canonical in PROMPT_MARKER_MAP.items():
        text = text.replace(legacy, canonical)
    return text


def normalize_payload_schema(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a structured payload and normalize only known identity fields."""

    normalized = dict(payload)
    for key in ("schema", "event_schema", "workflow_schema"):
        if key in normalized:
            normalized[key] = normalize_event_schema(normalized[key])
    for key in ("tool", "tool_name", "active_tool", "failed_tool", "pending_tool"):
        if key in normalized and isinstance(normalized[key], str):
            normalized[key] = normalize_tool_name(normalized[key])
    for key in ("provider", "provider_name"):
        if key in normalized and isinstance(normalized[key], str):
            normalized[key] = normalize_provider_name(normalized[key])
    return normalized


def normalize_identity_tree(value: Any) -> Any:
    """Recursively normalize identity fields in persisted structured payloads.

    Only allowlisted keys are rewritten. IDs, free-form messages, user content
    and arbitrary ``name`` fields remain byte-for-byte equivalent values.
    """

    if isinstance(value, list):
        return [normalize_identity_tree(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized: dict[Any, Any] = {}
    for key, item in value.items():
        nested = normalize_identity_tree(item)
        if key in {"schema", "event_schema", "workflow_schema"}:
            nested = normalize_event_schema(nested)
        elif key in {
            "tool",
            "tool_name",
            "active_tool",
            "failed_tool",
            "pending_tool",
        }:
            nested = normalize_tool_name(nested)
        elif key in {"provider", "provider_name"}:
            nested = normalize_provider_name(nested)
        normalized[key] = nested
    return normalized
