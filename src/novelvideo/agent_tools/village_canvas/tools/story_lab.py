"""Declared direct tools migrated from the ordered source parts."""

from __future__ import annotations

from .spec import ToolSpec


STORY_LAB_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="direct.village_canvas_story_lab_get",
        handler_name="_handle_story_lab_get",
        tool_name="village_canvas_story_lab_get",
        description="Read the current project's 故事 Agent/Story Lab brief, generated stages and latest persisted results. Use before continuing or revising story creation.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            }
        },
        required=(),
    ),
    ToolSpec(
        id="direct.village_canvas_story_lab_save",
        handler_name="_handle_story_lab_save",
        tool_name="village_canvas_story_lab_save",
        description="Save the current project's 故事 Agent creative brief without starting model generation.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "title": {"type": "string"},
            "logline": {"type": "string"},
            "work_type": {
                "type": "string",
                "enum": [
                    "long_novel",
                    "short_novel",
                    "screenplay",
                    "micro_drama",
                    "comic_narration",
                ],
            },
            "genre": {"type": "string"},
            "theme": {"type": "string"},
            "target_units": {"type": "integer", "minimum": 1, "maximum": 200},
            "target_length": {"type": "integer", "minimum": 100, "maximum": 100000},
            "point_of_view": {"type": "string"},
            "audience": {"type": "string"},
            "style_mode": {"type": "string", "enum": ["infer", "confirmed"]},
            "style_id": {"type": "string"},
            "style_name": {"type": "string"},
            "style_prompt": {"type": "string"},
        },
        required=("title", "logline", "work_type"),
    ),
    ToolSpec(
        id="direct.village_canvas_story_lab_generate",
        handler_name="_handle_story_lab_generate",
        tool_name="village_canvas_story_lab_generate",
        description="Start exactly one 故事 Agent generation stage. On the first stage, pass the creative brief in the same call; later stages reuse persisted state. Poll task_type='story_lab_<stage>' with episode=0.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "stage": {"type": "string", "enum": ["bible", "outline", "draft", "audit"]},
            "instructions": {
                "type": "string",
                "description": "Optional revision or generation direction for this "
                "stage.",
            },
            "title": {"type": "string"},
            "logline": {"type": "string"},
            "work_type": {
                "type": "string",
                "enum": [
                    "long_novel",
                    "short_novel",
                    "screenplay",
                    "micro_drama",
                    "comic_narration",
                ],
            },
            "genre": {"type": "string"},
            "theme": {"type": "string"},
            "target_units": {"type": "integer", "minimum": 1, "maximum": 200},
            "target_length": {"type": "integer", "minimum": 100, "maximum": 100000},
            "point_of_view": {"type": "string"},
            "audience": {"type": "string"},
            "style_mode": {"type": "string", "enum": ["infer", "confirmed"]},
            "style_id": {"type": "string"},
            "style_name": {"type": "string"},
            "style_prompt": {"type": "string"},
        },
        required=("stage",),
    ),
    ToolSpec(
        id="direct.village_canvas_story_lab_publish",
        handler_name="_handle_story_lab_publish",
        tool_name="village_canvas_story_lab_publish",
        description="Publish the approved 故事 Agent draft into 项目素材: export UTF-8 text, copy it to the project upload area, and enqueue the existing ingest task.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "filename": {
                "type": "string",
                "description": "Optional .txt export filename.",
            },
            "rebuild": {
                "type": "boolean",
                "description": "Rebuild an existing ingest graph when true.",
            },
        },
        required=(),
    ),
)
