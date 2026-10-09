from __future__ import annotations

from novelvideo.research.aigc_director_recipes import (
    LIBTV_DIRECTOR_STYLE_PACK_VERSION,
    build_director_research_context,
    select_director_style,
)


def test_libtv_style_is_opt_in_and_normalized():
    assert select_director_style({}) is None
    style = select_director_style({"director_style": "TVC 广告"})

    assert style is not None
    assert style["style_id"] == "tvc"
    assert style["style_pack_version"] == LIBTV_DIRECTOR_STYLE_PACK_VERSION
    assert style["shot_size"] and style["camera_move"]


def test_libtv_style_enters_research_context_without_becoming_a_hidden_default():
    context = build_director_research_context(
        model_kind="video",
        creation_stage="prompt",
        request_params={"director_style": "悬疑短剧"},
        beat={"visual_description": "雨夜走廊"},
    )

    assert "[LIBTV_DIRECTOR_STYLE]" in context
    assert "悬疑短剧" in context
    assert "景别=" in context and "运镜=" in context
    assert "不得凭风格名新增角色" in context

    neutral = build_director_research_context(
        model_kind="video",
        creation_stage="prompt",
        request_params={},
        beat={"visual_description": "雨夜走廊"},
    )
    assert "[LIBTV_DIRECTOR_STYLE]" not in neutral

