"""Declared direct tools migrated from the ordered source parts."""

from __future__ import annotations

from .spec import ToolSpec


CREATIVE_PLANNING_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="direct.village_canvas_build_characters",
        handler_name="_handle_build_characters",
        tool_name="village_canvas_build_characters",
        description="Extract characters from the project's knowledge graph (async build_characters task, episode 0). Requires ingest to be complete first. Use THIS instead of guessing a path. Poll with village_canvas_get_task(task_type='build_characters', episode=0); read results with village_canvas_get('/projects/{project}/characters').",
        properties={
            "project_id": {
                "type": "string",
                "description": "Project id. Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            }
        },
        required=(),
    ),
    ToolSpec(
        id="direct.village_canvas_plan_episodes",
        handler_name="_handle_plan_episodes",
        tool_name="village_canvas_plan_episodes",
        description="Plan/generate episodes (分集规划, async build_episodes task, episode 0). Requires ingest + character extraction done first. Use THIS instead of guessing a path — the real endpoint is POST /projects/{project}/episodes/plan (NOT /episodes, /tasks/..., /build_episodes or /start_pipeline). Poll with village_canvas_get_task(task_type='build_episodes', episode=0); read with village_canvas_get('/projects/{project}/episodes').",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "target_episodes": {
                "type": "integer",
                "description": "How many episodes to plan (default 10).",
            },
            "planning_mode": {
                "type": "string",
                "description": "Planning mode (default 'chapters').",
            },
        },
        required=(),
    ),
    ToolSpec(
        id="direct.village_canvas_generate_script",
        handler_name="_handle_generate_script",
        tool_name="village_canvas_generate_script",
        description="Generate the screenplay for one episode (脚本生成, script_writer task). Use THIS instead of guessing — the real endpoint is POST /projects/{project}/episodes/{episode}/script/generate. Requires the episode's character identities planned first (else returns code 'identity_plan_required'). Poll with village_canvas_get_task(task_type='script_writer', episode=N); read with village_canvas_get_episode_script(episode=N).",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "episode": {
                "type": "integer",
                "description": "Episode number (1-based, required).",
            },
        },
        required=("episode",),
    ),
    ToolSpec(
        id="direct.village_canvas_update_character_face_prompt",
        handler_name="_handle_update_character_face_prompt",
        tool_name="village_canvas_update_character_face_prompt",
        description="Set or repair one character's face_prompt (面部特征) before portrait generation. Use this after character extraction if a core character has an empty face_prompt, or when character_portrait fails with '请先设置面部特征 (face_prompt)'. Real endpoint PATCH /projects/{project}/characters/{name} with {face_prompt: ...}. After this succeeds, retry village_canvas_generate_portrait for that character.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "name": {"type": "string", "description": "Character name (required)."},
            "character": {"type": "string", "description": "Alias of name."},
            "face_prompt": {
                "type": "string",
                "description": "Concrete facial features: hairstyle, face shape, "
                "eyes, skin tone, age cues; no clothing.",
            },
        },
        required=("name", "face_prompt"),
    ),
    ToolSpec(
        id="direct.village_canvas_plan_identities",
        handler_name="_handle_plan_identities",
        tool_name="village_canvas_plan_identities",
        description="Plan character identities for one episode (身份规划, identity_planner task). Use THIS instead of guessing — real endpoint POST /projects/{project}/episodes/{episode}/identities/plan-async. This is a PREREQUISITE for village_canvas_generate_script. Poll village_canvas_get_task(task_type='identity_planner', episode=N).",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "episode": {"type": "integer", "description": "Episode number (required)."},
        },
        required=("episode",),
    ),
    ToolSpec(
        id="direct.village_canvas_plan_scenes",
        handler_name="_handle_plan_scenes",
        tool_name="village_canvas_plan_scenes",
        description="Plan the scene menu for one episode (场景规划, episode_scene_planner task). Use THIS after script generation and before sketch generation when the pipeline needs scene context. Real endpoint POST /projects/{project}/episodes/{episode}/scenes/plan. Poll with village_canvas_get_task(task_type='episode_scene_planner', episode=N).",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "episode": {"type": "integer", "description": "Episode number (required)."},
        },
        required=("episode",),
    ),
    ToolSpec(
        id="direct.village_canvas_plan_props",
        handler_name="_handle_plan_props",
        tool_name="village_canvas_plan_props",
        description="Plan the prop menu for one episode (道具规划, episode_prop_planner task). Use THIS after script generation and before sketch generation when the pipeline needs prop context. Real endpoint POST /projects/{project}/episodes/{episode}/props/plan. Poll with village_canvas_get_task(task_type='episode_prop_planner', episode=N).",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "episode": {"type": "integer", "description": "Episode number (required)."},
        },
        required=("episode",),
    ),
)
