from pathlib import Path

from novelvideo.api.routes import generation
from novelvideo.services import mainline_generation_context


def test_generation_route_preserves_mainline_context_compatibility_aliases():
    assert (
        generation._build_character_map
        is mainline_generation_context.build_character_map
    )
    assert (
        generation._episode_from_store_or_none
        is mainline_generation_context.episode_from_store_or_none
    )
    assert (
        generation._resolve_render_bool_setting
        is mainline_generation_context.resolve_render_bool_setting
    )
    assert (
        generation._resolve_render_image_selection
        is mainline_generation_context.resolve_render_image_selection
    )
    assert (
        generation._resolve_sketch_image_selection
        is mainline_generation_context.resolve_sketch_image_selection
    )
    assert (
        generation._runtime_prop_menu_with_global_props
        is mainline_generation_context.runtime_prop_menu_with_global_props
    )


def test_director_control_scope_keeps_existing_task_identity_format():
    assert (
        mainline_generation_context.director_control_scope(3, 7)
        == "director_control_to_sketch:ep003:beat_07"
    )


def test_freezone_mainline_flow_no_longer_imports_generation_route():
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "novelvideo"
        / "api"
        / "routes"
        / "freezone.py"
    ).read_text(encoding="utf-8")

    assert "novelvideo.api.routes.generation" not in source
