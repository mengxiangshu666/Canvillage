"""Declared direct tools migrated from the ordered source parts."""

from __future__ import annotations

from .spec import ToolSpec


TASK_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="direct.village_canvas_list_tasks",
        handler_name="_handle_list_tasks",
        tool_name="village_canvas_list_tasks",
        description="List Village Infinite Canvas tasks for the current or specified project.",
        properties={
            "project_id": {"type": "string"},
            "episode": {"type": "integer"},
            "task_type": {"type": "string"},
            "status": {"type": "string"},
        },
        required=(),
    ),
    ToolSpec(
        id="direct.village_canvas_get_task",
        handler_name="_handle_get_task",
        tool_name="village_canvas_get_task",
        description="Get one Village Infinite Canvas task status by task type, episode, and optional beat/scope.",
        properties={
            "project_id": {"type": "string"},
            "task_type": {"type": "string"},
            "episode": {"type": "integer"},
            "beat": {"type": "integer"},
            "beat_num": {"type": "integer"},
            "scope": {"type": "string"},
        },
        required=("task_type", "episode"),
    ),
    ToolSpec(
        id="direct.village_canvas_get_episode_script",
        handler_name="_handle_get_episode_script",
        tool_name="village_canvas_get_episode_script",
        description="Get one episode script for the current or specified project.",
        properties={"project_id": {"type": "string"}, "episode": {"type": "integer"}},
        required=("episode",),
    ),
    ToolSpec(
        id="direct.village_canvas_list_ingest_uploads",
        handler_name="_handle_list_ingest_uploads",
        tool_name="village_canvas_list_ingest_uploads",
        description="List files already uploaded to the current project's ingest script directory. Use this when the user asks which files are currently uploaded, or before starting video/short-drama ingest from a previously uploaded script.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Project id. Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            }
        },
        required=(),
    ),
)
