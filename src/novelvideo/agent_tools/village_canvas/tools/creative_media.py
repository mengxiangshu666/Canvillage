"""Declared direct tools migrated from the ordered source parts."""

from __future__ import annotations

from .spec import ToolSpec


CREATIVE_MEDIA_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="direct.village_canvas_generate_scene_master",
        handler_name="_handle_generate_scene_master",
        tool_name="village_canvas_generate_scene_master",
        description="Generate one scene's canonical master reference image (场景正向参考图, scene_reference_asset task). Real endpoint POST /projects/{project}/scenes/{name}/master/generate-async. Use scene names from village_canvas_get(path='/projects/{project}/scenes') or the episode scene menu. Poll with village_canvas_get_task(task_type='scene_reference_asset', episode=0, scope=<returned scope>).",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "name": {"type": "string", "description": "Scene name (required)."},
            "scene_name": {"type": "string", "description": "Alias of name."},
        },
        required=("name",),
    ),
    ToolSpec(
        id="direct.village_canvas_generate_scene_reverse",
        handler_name="_handle_generate_scene_reverse",
        tool_name="village_canvas_generate_scene_reverse",
        description="Generate one scene's reverse master reference image (场景反向参考图, scene_reference_asset task). Real endpoint POST /projects/{project}/scenes/{name}/reverse/generate-async. Poll with village_canvas_get_task(task_type='scene_reference_asset', episode=0, scope=<returned scope>).",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "name": {"type": "string", "description": "Scene name (required)."},
            "scene_name": {"type": "string", "description": "Alias of name."},
        },
        required=("name",),
    ),
    ToolSpec(
        id="direct.village_canvas_generate_portrait",
        handler_name="_handle_generate_portrait",
        tool_name="village_canvas_generate_portrait",
        description="Generate one character's portrait (肖像生成, character_portrait task). Real endpoint POST /projects/{project}/characters/{name}/portrait-async. Call once per character. Poll village_canvas_get_task(task_type='character_portrait').",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "name": {
                "type": "string",
                "description": "Character name (required; from the character list).",
            },
        },
        required=("name",),
    ),
    ToolSpec(
        id="direct.village_canvas_generate_identity_image",
        handler_name="_handle_generate_identity_image",
        tool_name="village_canvas_generate_identity_image",
        description="Generate a character identity image (身份图生成, identity_image task). Real endpoint POST /projects/{project}/characters/{name}/identities/{identity_id}/generate-async. Poll village_canvas_get_task(task_type='identity_image').",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "name": {"type": "string", "description": "Character name (required)."},
            "identity_id": {
                "type": "string",
                "description": "Identity id from the character's identity list "
                "(required).",
            },
        },
        required=("name", "identity_id"),
    ),
    ToolSpec(
        id="direct.village_canvas_generate_sketches",
        handler_name="_handle_generate_sketches",
        tool_name="village_canvas_generate_sketches",
        description="Generate beat sketches for one episode (草图生成, sketch_generation task). Real endpoint POST /projects/{project}/episodes/{episode}/sketches/generate with a canonical body. This tool automatically runs assign-colors first by default and fills safe defaults: model='nanobanana', grid_index=-1 (all grids), sketch_scene_grouping=true, aspect_ratio='2:3'. Use THIS instead of village_canvas_post or guessing the body. Runs after the script exists. Poll village_canvas_get_task(task_type='sketch_generation', episode=N).",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "episode": {"type": "integer", "description": "Episode number (required)."},
            "style": {
                "type": "string",
                "description": "Optional visual style override.",
            },
            "model": {
                "type": "string",
                "description": "Sketch model. Default: nanobanana.",
            },
            "grid_index": {
                "type": "integer",
                "description": "Grid index to generate. Use -1 to generate all grids. "
                "Default: -1.",
            },
            "sketch_scene_grouping": {
                "type": "boolean",
                "description": "Group sketch grids by scene. Default: true.",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": ["2:3", "16:9"],
                "description": "Sketch aspect ratio. Default: 2:3.",
            },
            "image_generation_selection": {
                "type": "string",
                "description": "Optional backend/provider selection "
                "from sketch settings.",
            },
            "auto_assign_colors": {
                "type": "boolean",
                "description": "Run /sketches/assign-colors before generation. "
                "Default: true.",
            },
            "body": {
                "type": "object",
                "description": "Advanced override merged into the canonical generate body.",
            },
        },
        required=("episode",),
    ),
    ToolSpec(
        id="direct.village_canvas_detect_sketch_identities",
        handler_name="_handle_detect_sketch_identities",
        tool_name="village_canvas_detect_sketch_identities",
        description="Run AI detection on one episode's generated sketches and persist detected identities and props to each beat. Real endpoint POST /projects/{project}/episodes/{episode}/sketches/detect-identities. Requires sketches to exist and sketch colors to be assigned; if colors are missing, run village_canvas_generate_sketches or POST assign-colors first. Use THIS when the user asks to run AI 检测 / identity detection for sketches. If the tool returns a timeout or retryable=false, do not call it again in the same turn; report the timeout and stop.",
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
        id="direct.village_canvas_get_sketches",
        handler_name="_handle_get_sketches",
        tool_name="village_canvas_get_sketches",
        description="Get display-ready official sketch URLs for an episode, to SHOW the user. This tool returns only current sketch_url media. It does not fall back to grids/epNNN/sketch/beat_XX_t* pool candidates and never substitutes first frames. Use village_canvas_get_first_frames only when the user explicitly asks for 首帧/first frames. Do NOT read sketch_path from a task result, and do NOT use vision_analyze to 'show' images. After calling this tool, do not write markdown images, raw URLs, http/static paths, or HTML media tags.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "episode": {"type": "integer", "description": "Episode number (required)."},
            "beat": {"type": "integer", "description": "Show only one beat's sketch."},
            "beat_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Show only these beat numbers, in episode order.",
            },
            "offset": {
                "type": "integer",
                "description": "Zero-based media offset after beat filtering. Use with "
                "limit for paging.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum media items to return. Default/max: 12.",
            },
        },
        required=("episode",),
    ),
    ToolSpec(
        id="direct.village_canvas_get_first_frames",
        handler_name="_handle_get_first_frames",
        tool_name="village_canvas_get_first_frames",
        description="Get display-ready first-frame URLs for an episode, to SHOW the user. This tool returns only frame_url media. Use this only when the user explicitly asks for 首帧/first frames. Use village_canvas_get_sketches for sketches. After calling this tool, do not write markdown images, raw URLs, http/static paths, or HTML media tags.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "episode": {"type": "integer", "description": "Episode number (required)."},
            "beat": {
                "type": "integer",
                "description": "Show only one beat's first frame.",
            },
            "beat_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Show only these beat numbers, in episode order.",
            },
            "offset": {
                "type": "integer",
                "description": "Zero-based media offset after beat filtering. Use with "
                "limit for paging.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum media items to return. Default/max: 12.",
            },
        },
        required=("episode",),
    ),
    ToolSpec(
        id="direct.village_canvas_get_sketch_candidates",
        handler_name="_handle_get_sketch_candidates",
        tool_name="village_canvas_get_sketch_candidates",
        description="Get display-ready sketch pool candidates for one beat. This tool shows grids/epNNN/sketch/beat_XX_t* candidates and is separate from current sketch_url. Use village_canvas_get_sketches when the user asks for the official/current sketch.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "episode": {"type": "integer", "description": "Episode number (required)."},
            "beat": {"type": "integer", "description": "Beat number (required)."},
            "offset": {
                "type": "integer",
                "description": "Zero-based candidate offset. Use with limit for paging.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum media items to return. Default/max: 12.",
            },
        },
        required=("episode", "beat"),
    ),
    ToolSpec(
        id="direct.village_canvas_get_scene_images",
        handler_name="_handle_get_scene_images",
        tool_name="village_canvas_get_scene_images",
        description="Get display-ready scene image URLs for a project, to SHOW the user. Returns per-scene servable master_url/reverse_master_url/pano_url/custom_scene_url and prepared media data. Do NOT use local *_path fields, task result paths, or synthesized download URLs. After calling this tool, do not write markdown images, raw URLs, http/static paths, or HTML media tags.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "include_reverse": {
                "type": "boolean",
                "description": "Include reverse_master_url entries. Default: true.",
            },
            "include_pano": {
                "type": "boolean",
                "description": "Include pano_url entries. Default: false.",
            },
            "include_custom": {
                "type": "boolean",
                "description": "Include custom_scene_url entries. Default: false.",
            },
            "name": {
                "type": "string",
                "description": "Show scenes whose name contains this text.",
            },
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Show scenes whose name contains any of these texts.",
            },
            "scene_name": {
                "type": "string",
                "description": "Alias of name; fuzzy contains match.",
            },
            "scene_type": {
                "type": "string",
                "description": "Show only scenes with this scene_type.",
            },
            "index": {
                "type": "integer",
                "description": "Show only the Nth scene from the API scene list, 1-based.",
            },
            "scene_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Show only these 1-based scene indexes from the API "
                "scene list.",
            },
            "offset": {
                "type": "integer",
                "description": "Zero-based media offset after scene filtering. Use with "
                "limit for paging.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum media items to return. Default/max: 12.",
            },
        },
        required=(),
    ),
    ToolSpec(
        id="direct.village_canvas_get_character_media",
        handler_name="_handle_get_character_media",
        tool_name="village_canvas_get_character_media",
        description="Get display-ready character portrait/identity image URLs and prepared media data. After calling this tool, do not write markdown images, raw URLs, http/static paths, or HTML media tags; the backend renders the returned media automatically.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "include_identities": {
                "type": "boolean",
                "description": "Include identity images. Default: true.",
            },
            "media_kind": {
                "type": "string",
                "enum": ["all", "portrait", "identity"],
                "description": "all=portraits plus identity images; portrait=only "
                "character portraits; identity=only identity images.",
            },
            "name": {
                "type": "string",
                "description": "Show character media whose character name, aliases, or "
                "identity name/id contains this text.",
            },
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Show character media whose character name, aliases, or "
                "identity name/id contains any of these texts.",
            },
            "query": {
                "type": "string",
                "description": "Broad fuzzy text query over character name, "
                "role/description, identity names, and identity "
                "descriptions.",
            },
            "identity_name": {
                "type": "string",
                "description": "Fuzzy text query over identity image names/ids.",
            },
            "offset": {
                "type": "integer",
                "description": "Zero-based media offset after character filtering. Use "
                "with limit for paging.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum media items to return. Default/max: 12.",
            },
        },
        required=(),
    ),
    ToolSpec(
        id="direct.village_canvas_get_episode_media",
        handler_name="_handle_get_episode_media",
        tool_name="village_canvas_get_episode_media",
        description="Get display-ready episode beat video/audio URLs and prepared media data. media_type='video' returns video previews; media_type='audio' returns audio items. After calling this tool, do not write markdown images, raw URLs, http/static paths, or HTML media tags; the backend renders the returned media automatically.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "episode": {"type": "integer", "description": "Episode number (required)."},
            "media_type": {
                "type": "string",
                "enum": ["video", "audio"],
                "description": "Default: video.",
            },
            "beat": {
                "type": "integer",
                "description": "Show only one beat's video/audio.",
            },
            "beat_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Show only these beat numbers, in episode order.",
            },
            "query": {
                "type": "string",
                "description": "Fuzzy text query over beat title, description, "
                "narration/dialogue, speaker, characters, and scene.",
            },
            "search": {"type": "string", "description": "Alias of query."},
            "offset": {
                "type": "integer",
                "description": "Zero-based media offset after beat filtering. Use with "
                "limit for paging.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum media items to return. Video max 6; audio max 20.",
            },
        },
        required=("episode",),
    ),
    ToolSpec(
        id="direct.village_canvas_render_first_frames",
        handler_name="_handle_render_first_frames",
        tool_name="village_canvas_render_first_frames",
        description="Generate first frames for an episode (首帧生成, selected_regen task). Real endpoint POST /projects/{project}/episodes/{episode}/beats/regenerate with {beat_indices:[...]}. Omit beat_indices to render ALL beats of the episode (resolved automatically). Requires sketches first. Poll village_canvas_get_task(task_type='selected_regen', episode=N).",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "episode": {"type": "integer", "description": "Episode number (required)."},
            "beat_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Beat numbers to render. Omit to render all beats of "
                "the episode.",
            },
            "style": {
                "type": "string",
                "description": "Optional visual style override.",
            },
        },
        required=("episode",),
    ),
)
