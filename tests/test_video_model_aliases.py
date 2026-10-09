from novelvideo.generators.video.builtin_catalog import (
    normalize_newapi_video_model_id,
    resolve_newapi_video_upstream_model,
)


def test_card_face_model_uses_gateway_ascii_id_not_display_alias() -> None:
    assert normalize_newapi_video_model_id("sd2.0满血933卡脸版") == "sd2.0-full-933-face"
    assert resolve_newapi_video_upstream_model("sd2.0满血933卡脸版") == "sd2.0-full-933-face"
    assert resolve_newapi_video_upstream_model("sd2.0-full-933-face") == "sd2.0-full-933-face"
