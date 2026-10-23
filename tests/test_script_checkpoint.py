from pathlib import Path

from novelvideo.freezone.script_checkpoint import (
    checkpoint_fingerprint,
    checkpoint_path,
    discard_story_script_checkpoint,
    load_story_script_checkpoint,
    save_story_script_checkpoint,
)


def test_checkpoint_round_trip_and_atomic_replacement(tmp_path: Path):
    fingerprint = checkpoint_fingerprint(
        source_text="镜1：推门", prompt="", model="m", video_model="v"
    )
    path = save_story_script_checkpoint(
        tmp_path,
        fingerprint,
        stage="bound",
        data={
            "title": "测试",
            "director_plan": {},
            "rows": [{"shot_no": 1, "duration": 4}],
        },
        frame_urls=["/a.png"],
    )
    assert path == checkpoint_path(tmp_path, fingerprint)
    loaded = load_story_script_checkpoint(tmp_path, fingerprint)
    assert loaded is not None
    assert loaded["stage"] == "bound"
    assert loaded["frame_urls"] == ["/a.png"]
    assert loaded["data"]["rows"][0]["shot_no"] == 1

    save_story_script_checkpoint(
        tmp_path,
        fingerprint,
        stage="contract",
        data={
            "title": "测试",
            "director_plan": {},
            "rows": [{"shot_no": 1, "duration": 4}],
        },
        contract_report={"blocking_count": 0},
    )
    replaced = load_story_script_checkpoint(tmp_path, fingerprint)
    assert replaced is not None
    assert replaced["stage"] == "contract"
    assert replaced["contract_report"]["blocking_count"] == 0
    assert not list(path.parent.glob("*.tmp"))


def test_changed_inputs_do_not_reuse_old_checkpoint(tmp_path: Path):
    first = checkpoint_fingerprint(
        source_text="原文A", prompt="", model="m", video_model="v"
    )
    second = checkpoint_fingerprint(
        source_text="原文B", prompt="", model="m", video_model="v"
    )
    save_story_script_checkpoint(
        tmp_path,
        first,
        stage="bound",
        data={
            "title": "A",
            "director_plan": {},
            "rows": [{"shot_no": 1, "duration": 4}],
        },
    )
    assert load_story_script_checkpoint(tmp_path, second) is None
    assert load_story_script_checkpoint(tmp_path, first) is not None


def test_corrupt_or_incomplete_checkpoint_is_ignored(tmp_path: Path):
    fingerprint = checkpoint_fingerprint(
        source_text="原文", prompt="", model=None, video_model=None
    )
    path = checkpoint_path(tmp_path, fingerprint)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert load_story_script_checkpoint(tmp_path, fingerprint) is None

    path.write_text(
        '{"schema": "freezone-story-script-checkpoint.v1"}', encoding="utf-8"
    )
    assert load_story_script_checkpoint(tmp_path, fingerprint) is None

    discard_story_script_checkpoint(tmp_path, fingerprint)
    assert not path.exists()
