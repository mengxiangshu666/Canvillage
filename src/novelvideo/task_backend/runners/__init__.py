"""Project task runner registration package.

Importing this package registers every built-in project task runner.
"""

from novelvideo.task_backend.runners import (  # noqa: F401
    audio,
    character_quality,
    character_image,
    content,
    episode_assets,
    episode_quality,
    freezone,
    graph_build,
    identity,
    ingest,
    prop_reference,
    render,
    scene_reference,
    script,
    sketch,
    sketch_edit_execute,
    stage_asset,
    story_lab,
    video,
)
