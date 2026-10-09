"""DirectorWorld persistence helpers.

Python owns paths, schema validation, and disk IO. The Vite editor remains the
live runtime for viewport interaction.
"""

from .paths import (
    actor_state_registry_path,
    beat_blocking_path,
    blockings_dir,
    director_blocking_ref_path,
    director_ref_dir,
    safe_name,
    scene_assets_dir,
    scene_gaussian_splat_collision_glb_path,
    scene_gaussian_splat_ply_path,
    shape_hint_registry_path,
    shape_hints_dir,
    session_id,
    session_target_name,
    states_dir,
    world_path,
)
from .service import DirectorWorldService
from .libtv_scene_contract import (
    SCENE_CONTRACT_REVISION_PREFIX,
    SCENE_CONTRACT_SCHEMA,
    compile_libtv_scene_contract,
    validate_libtv_scene_contract,
)
from .store import (
    load_beat_blocking,
    load_world,
    save_beat_blocking,
    save_beat_blocking_file,
)

__all__ = [
    "DirectorWorldService",
    "SCENE_CONTRACT_REVISION_PREFIX",
    "SCENE_CONTRACT_SCHEMA",
    "compile_libtv_scene_contract",
    "validate_libtv_scene_contract",
    "actor_state_registry_path",
    "beat_blocking_path",
    "blockings_dir",
    "director_blocking_ref_path",
    "director_ref_dir",
    "load_beat_blocking",
    "load_world",
    "save_beat_blocking",
    "save_beat_blocking_file",
    "safe_name",
    "scene_assets_dir",
    "scene_gaussian_splat_collision_glb_path",
    "scene_gaussian_splat_ply_path",
    "session_id",
    "session_target_name",
    "shape_hint_registry_path",
    "shape_hints_dir",
    "states_dir",
    "world_path",
]
