from pathlib import Path

from novelvideo.models import VisualBeat
from novelvideo.workflows.literal_script_writing import (
    _load_script_checkpoint,
    _save_script_checkpoint,
    _script_checkpoint_fingerprint,
    _script_checkpoint_path,
)
from novelvideo.workflows.script_writing import create_script_writing_workflow


def _beat(number: int) -> VisualBeat:
    return VisualBeat(
        beat_number=number,
        narration_segment=f"第{number}行",
        visual_description=f"{{{{角色_身份}}}}完成第{number}个动作",
    )


def test_literal_checkpoint_round_trip_and_atomic_cleanup(tmp_path: Path):
    path = _script_checkpoint_path(tmp_path, 2)
    fingerprint = _script_checkpoint_fingerprint({"source": "same"})

    _save_script_checkpoint(
        path,
        episode_num=2,
        fingerprint=fingerprint,
        beats=[_beat(1), _beat(2)],
    )
    restored = _load_script_checkpoint(path, fingerprint=fingerprint, total=3)

    assert path is not None and path.exists()
    assert [beat.beat_number for beat in restored] == [1, 2]
    assert not list(path.parent.glob("*.tmp"))


def test_literal_checkpoint_rejects_changed_source_and_non_contiguous_beats(tmp_path: Path):
    path = _script_checkpoint_path(tmp_path, 1)
    original = _script_checkpoint_fingerprint({"source": "old"})
    changed = _script_checkpoint_fingerprint({"source": "new"})
    _save_script_checkpoint(
        path,
        episode_num=1,
        fingerprint=original,
        beats=[_beat(1)],
    )

    assert _load_script_checkpoint(path, fingerprint=changed, total=2) == []

    _save_script_checkpoint(
        path,
        episode_num=1,
        fingerprint=original,
        beats=[_beat(2)],
    )
    assert _load_script_checkpoint(path, fingerprint=original, total=2) == []


def test_literal_checkpoint_ignores_corruption_and_disables_without_output_dir(tmp_path: Path):
    path = _script_checkpoint_path(tmp_path, 1)
    assert path is not None
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")

    assert _load_script_checkpoint(path, fingerprint="x", total=1) == []
    assert _script_checkpoint_path("", 1) is None


def test_script_factory_uses_cognee_project_dir_for_live_checkpoints(tmp_path: Path):
    class _CogneeStore:
        project_dir = str(tmp_path)

    workflow = create_script_writing_workflow(_CogneeStore())

    assert workflow.output_dir == str(tmp_path)
    assert _script_checkpoint_path(workflow.output_dir, 1) is not None
