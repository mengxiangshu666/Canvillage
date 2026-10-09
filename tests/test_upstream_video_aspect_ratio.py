from novelvideo.generators.video_generator import _normalize_video_aspect_ratio
from novelvideo.task_backend.runners.video import _resolve_video_aspect_ratio


def test_generator_preserves_adaptive_and_fixed_ratios():
    assert _normalize_video_aspect_ratio("adaptive") == "adaptive"
    assert _normalize_video_aspect_ratio(" ADAPTIVE ") == "adaptive"
    assert _normalize_video_aspect_ratio("16:9") == "16:9"
    assert _normalize_video_aspect_ratio("2:3") == "2:3"
    assert _normalize_video_aspect_ratio(None) == "9:16"
    assert _normalize_video_aspect_ratio("auto") == "9:16"


def test_runner_uses_adaptive_mode_for_unpinned_first_frame():
    assert _resolve_video_aspect_ratio("auto", "/tmp/first.png") == "adaptive"
    assert _resolve_video_aspect_ratio(None, "/tmp/first.png") == "adaptive"
    assert _resolve_video_aspect_ratio("9:16", "/tmp/first.png") == "9:16"
    assert _resolve_video_aspect_ratio(None, None) == "9:16"

