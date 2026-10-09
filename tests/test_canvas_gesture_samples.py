from scripts.architecture.measure_canvas_browser_interaction import summarize_gesture_frames, version_metadata
import pytest


def test_nearest_rank_p95_reports_actual_frame_count():
    result = summarize_gesture_frames(list(range(1, 21)), before='translate(0)', after='translate(10)')
    assert result['frame_count'] == 20
    assert result['rAF_interval_p95_ms'] == 19
    assert result['sampling'] == 'gesture_in_progress_rAF'


@pytest.mark.parametrize('frames', [[], [1, 2], [1, 2, 3, 4, 0], [1, 2, 3, 4, float('nan')]])
def test_bad_frames_rejected(frames):
    with pytest.raises(RuntimeError):
        summarize_gesture_frames(frames, before='a', after='b')


def test_unchanged_transform_rejected():
    with pytest.raises(RuntimeError, match='move viewport'):
        summarize_gesture_frames([1] * 10, before='same', after='same')


def test_backend_and_static_frontend_builds_are_distinct():
    metadata = version_metadata({'buildId': 'old-static'}, {'buildId': 'current-api'})
    assert metadata['backend_build_id'] == 'current-api'
    assert metadata['frontend_build_id'] == 'old-static'
    assert metadata['build_id'] == 'old-static'
    assert version_metadata({}, None)['backend_build_id'] is None
