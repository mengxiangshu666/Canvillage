"""The imported libtv image-style library stays loadable and keeps its preview."""

from __future__ import annotations

from novelvideo.services.style_service import StyleService

LIBTV_STYLE_IDS = (
    "libtv_irasutoya",
    "libtv_toy_package",
    "libtv_arcade_pixel",
    "libtv_emoji_icon",
    "libtv_riso_print",
    "libtv_grain_film",
    "libtv_emoji_oil_painting",
    "libtv_scifi_3d",
    "libtv_clay_animation",
    "libtv_tiny_world",
    "libtv_lego",
    "libtv_candid_photo",
    "libtv_jellycat",
    "libtv_retro_american_comic",
    "libtv_european_storybook",
    "libtv_vector_line_icon",
    "libtv_retro_anime",
    "libtv_polaroid",
    "libtv_puppet",
    "libtv_sanrio_girl",
)


def test_libtv_presets_load_and_have_previews() -> None:
    # 预览图是 libtv 官方风格示例，第三方作者保留权利；公开源码包按
    # .gitattributes 排除这些 webp，所以只在本地素材存在时校验文件。
    presets_dir = StyleService.PRESETS_DIR
    has_previews = any(presets_dir.glob("libtv_*.webp"))
    for style_id in LIBTV_STYLE_IDS:
        config = StyleService.get_preset(style_id)
        assert config is not None, style_id
        assert config.is_preset is True
        assert config.style_instructions.strip(), style_id
        assert config.label.strip(), style_id
        if has_previews:
            assert (presets_dir / f"{style_id}.webp").is_file(), style_id


def test_libtv_presets_appear_in_the_preset_list() -> None:
    styles = StyleService.list_preset_styles()
    by_id = {item["id"]: item for item in styles}
    assert set(LIBTV_STYLE_IDS) <= set(by_id)
    for style_id in LIBTV_STYLE_IDS:
        assert by_id[style_id]["type"] == "preset"
        assert "libtv" in (by_id[style_id].get("tags") or [])


def test_preset_names_stay_unique() -> None:
    names = [item["name"] for item in StyleService.list_preset_styles()]
    assert len(names) == len(set(names))
