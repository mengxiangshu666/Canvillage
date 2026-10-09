"""Regression coverage for filesystem-backed production pipeline status."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from novelvideo import project_config
from novelvideo.api.routes import pipeline as pipeline_route
from novelvideo.production.foundation_evidence import record_foundation_stage_complete
from novelvideo.production.stage_evidence import record_sketch_detection_complete
from novelvideo.styles.project_style import (
    AUTO_VISUAL_STYLE,
    build_project_style_snapshot,
    write_artifact_style_evidence,
    write_stage_style_evidence,
)
from novelvideo.utils.path_resolver import (
    canonical_character_four_view_path,
    canonical_identity_path,
    canonical_portrait_path,
)


class _Store:
    def __init__(self, *, characters: list, episode: object, beats: list[dict]):
        self._characters = characters
        self._episode = episode
        self._beats = beats

    def get_all_characters(self):
        return list(self._characters)

    def get_all_episodes(self):
        return [self._episode]

    def get_episode(self, number: int):
        return self._episode if int(number) == int(self._episode.number) else None

    async def get_beats_as_dicts(self, number: int):
        return list(self._beats) if int(number) == int(self._episode.number) else []


def _identity(identity_id: str, identity_name: str):
    return SimpleNamespace(identity_id=identity_id, identity_name=identity_name)


def _character(name: str, *, is_main: bool, identities: list | None = None):
    return SimpleNamespace(
        name=name,
        is_main=is_main,
        identities=list(identities or []),
    )


def _write_character_assets(project_dir: Path, name: str) -> tuple[Path, Path]:
    """写出该角色的两张正式产物：正面全身照与四视图设定表。

    两张缺一不可，只写一张时 ``portraits_done`` 必须为 False。
    """
    portrait = canonical_portrait_path(project_dir, name)
    portrait.parent.mkdir(parents=True, exist_ok=True)
    portrait.write_bytes(b"portrait")
    four_view = canonical_character_four_view_path(project_dir, name)
    four_view.parent.mkdir(parents=True, exist_ok=True)
    four_view.write_bytes(b"four-view")
    return portrait, four_view


async def _status(
    monkeypatch,
    project_dir: Path,
    store: _Store,
    *,
    expected_style_snapshot: dict | None = None,
) -> dict:
    state_dir = project_dir / "state"
    evidence = {
        "build_characters": {"characters": len(store.get_all_characters())},
        "build_scenes": {"scenes": 1},
        "build_props": {"props": 1},
    }
    for task_type, result in evidence.items():
        record_foundation_stage_complete(state_dir, task_type, result)
    resolved = SimpleNamespace(
        username="local",
        project_name="pipeline-test",
        project_dir=project_dir,
        ctx=SimpleNamespace(project_id="project-test", state_dir=state_dir),
    )
    manager = SimpleNamespace(get_task_for_project=lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        pipeline_route,
        "resolve_project_scope",
        AsyncMock(return_value=resolved),
    )
    monkeypatch.setattr(pipeline_route, "get_task_manager", lambda: manager)
    monkeypatch.setattr(pipeline_route, "_user_has_configured", lambda *_args: True)
    snapshot = dict(expected_style_snapshot or {})
    response = await pipeline_route.pipeline_status(
        "project-test",
        episode=1,
        expected_style_fingerprint=str(snapshot.get("fingerprint") or "") or None,
        expected_style_mode=str(snapshot.get("mode") or "") or None,
        expected_style_id=str(snapshot.get("style_id") or "") or None,
        user={"username": "local"},
        store=store,
    )
    return response["data"]


def test_new_project_defaults_do_not_fake_completed_configuration(monkeypatch):
    monkeypatch.setattr(
        project_config,
        "load_project_config_file",
        lambda *_args: {"user": "local", "project_uuid": "uuid"},
    )
    monkeypatch.setattr(
        project_config,
        "load_project_config",
        lambda *_args: {
            "ethnicity": "Chinese",
            "visual_style": "chinese_period_drama",
        },
    )

    assert pipeline_route._user_has_configured("local", "pipeline-test") is False


@pytest.mark.asyncio
async def test_portraits_fall_back_to_all_characters_without_main_cast(
    monkeypatch, tmp_path
):
    supporting = _character("店小二", is_main=False)
    _write_character_assets(tmp_path, supporting.name)
    store = _Store(
        characters=[supporting],
        episode=SimpleNamespace(number=1, identity_ids=[]),
        beats=[],
    )

    state = await _status(monkeypatch, tmp_path, store)

    assert state["global"]["portraits_done"] is True
    assert state["next_step"] == "identity_planner"


@pytest.mark.asyncio
async def test_portraits_need_the_four_view_sheet_to_count_as_done(monkeypatch, tmp_path):
    """只出正面全身照不算完成：四视图缺失时必须停在 portraits 步骤。"""
    lead = _character("主角", is_main=True)
    portrait = canonical_portrait_path(tmp_path, lead.name)
    portrait.parent.mkdir(parents=True, exist_ok=True)
    portrait.write_bytes(b"portrait")
    store = _Store(
        characters=[lead],
        episode=SimpleNamespace(number=1, identity_ids=[]),
        beats=[],
    )

    state = await _status(monkeypatch, tmp_path, store)

    assert state["global"]["portraits_done"] is False
    assert state["next_step"] == "portraits"

    canonical_character_four_view_path(tmp_path, lead.name).write_bytes(b"four-view")

    state = await _status(monkeypatch, tmp_path, store)

    assert state["global"]["portraits_done"] is True
    assert state["next_step"] == "identity_planner"


@pytest.mark.asyncio
async def test_required_supporting_identity_image_is_not_skipped(monkeypatch, tmp_path):
    lead = _character("主角", is_main=True)
    supporting_identity = _identity("店小二_日常", "日常")
    supporting = _character(
        "店小二",
        is_main=False,
        identities=[supporting_identity],
    )
    _write_character_assets(tmp_path, lead.name)
    _write_character_assets(tmp_path, supporting.name)
    store = _Store(
        characters=[lead, supporting],
        episode=SimpleNamespace(
            number=1,
            identity_ids=[supporting_identity.identity_id],
        ),
        beats=[],
    )

    state = await _status(monkeypatch, tmp_path, store)

    assert state["episode_status"]["identity_images"] is False
    assert state["next_step"] == "identity_images"


def _ready_identity_assets(project_dir: Path):
    identity = _identity("主角_日常", "日常")
    lead = _character("主角", is_main=True, identities=[identity])
    _write_character_assets(project_dir, lead.name)
    identity_path = canonical_identity_path(
        project_dir,
        lead.name,
        identity.identity_name,
    )
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    identity_path.write_bytes(b"identity")
    return lead, identity


def _ready_episode_assets(project_dir: Path):
    lead, identity = _ready_identity_assets(project_dir)
    beat = {
        "beat_number": 1,
        "visual_description": "镜头一",
        "video_mode": "first_frame",
        "video_prompt": "缓慢推进",
    }
    for relative_path in (
        "sketches/ep001/beat_01.png",
        "frames/ep001/beat_01.png",
        "videos/beats/ep001/beat_01.mp4",
        "videos/episodes/ep001_final.mp4",
    ):
        path = project_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"asset")
    record_sketch_detection_complete(project_dir, 1, [1])
    store = _Store(
        characters=[lead],
        episode=SimpleNamespace(number=1, identity_ids=[identity.identity_id]),
        beats=[beat],
    )
    return store


def _write_locked_episode_style_evidence(project_dir: Path, snapshot: dict) -> None:
    write_artifact_style_evidence(
        canonical_portrait_path(project_dir, "主角"), snapshot
    )
    write_artifact_style_evidence(
        canonical_character_four_view_path(project_dir, "主角"), snapshot
    )
    write_artifact_style_evidence(
        canonical_identity_path(project_dir, "主角", "日常"), snapshot
    )
    write_artifact_style_evidence(
        project_dir / "videos/beats/ep001/beat_01.mp4", snapshot
    )
    for stage in ("script", "sketches", "video_prompts", "frames"):
        write_stage_style_evidence(project_dir, stage, snapshot, episode=1)


@pytest.mark.asyncio
async def test_partial_sketch_series_does_not_advance_episode(monkeypatch, tmp_path):
    lead, identity = _ready_identity_assets(tmp_path)
    sketches = tmp_path / "sketches" / "ep001"
    sketches.mkdir(parents=True, exist_ok=True)
    (sketches / "beat_01.png").write_bytes(b"sketch")
    beats = [
        {
            "beat_number": number,
            "visual_description": f"镜头{number}",
            "detected_identities": ["__NO_CHARACTER__"],
            "detected_props": ["__NO_PROP__"],
        }
        for number in (1, 2)
    ]
    store = _Store(
        characters=[lead],
        episode=SimpleNamespace(number=1, identity_ids=[identity.identity_id]),
        beats=beats,
    )

    state = await _status(monkeypatch, tmp_path, store)

    assert state["episode_status"]["sketches"] is False
    assert state["next_step"] == "sketch_generation"


@pytest.mark.asyncio
async def test_keyframe_beats_count_as_global_optimize_done(monkeypatch, tmp_path):
    """首尾帧模式的镜头提示词存在 ``keyframe_prompt`` 里，也是合法的完成态。

    完成判据以前只认 ``video_prompt``，于是整集已经优化完、只是选了首尾帧模式的
    剧集永远判不合格，生产总控会以「产物尚未满足下一阶段；已停止以避免重复付费」
    停住。2026-10-03 运行版那条 Run 就是 80 个 first_frame + 55 个 keyframe，
    55 个 keyframe 的 ``video_prompt`` 为空。
    """

    lead, identity = _ready_identity_assets(tmp_path)
    sketches = tmp_path / "sketches" / "ep001"
    sketches.mkdir(parents=True, exist_ok=True)
    beats = []
    for number in (1, 2):
        (sketches / f"beat_{number:02d}.png").write_bytes(b"sketch")
        beats.append(
            {
                "beat_number": number,
                "visual_description": f"镜头{number}",
                "detected_identities": ["__NO_CHARACTER__"],
                "detected_props": ["__NO_PROP__"],
            }
        )
    record_sketch_detection_complete(tmp_path, 1, [1, 2])
    beats[0]["video_mode"] = "first_frame"
    beats[0]["video_prompt"] = "缓慢推进"
    beats[1]["video_mode"] = "keyframe"
    beats[1]["keyframe_prompt"] = "从站立到转身"
    store = _Store(
        characters=[lead],
        episode=SimpleNamespace(number=1, identity_ids=[identity.identity_id]),
        beats=beats,
    )
    monkeypatch.setattr(
        project_config,
        "load_project_config",
        lambda *_args: {"visual_style": AUTO_VISUAL_STYLE},
    )

    state = await _status(monkeypatch, tmp_path, store)

    assert state["episode_status"]["global_optimize"] is True
    assert state["next_step"] == "selected_regen"


@pytest.mark.asyncio
async def test_keyframe_beat_without_its_prompt_still_blocks(monkeypatch, tmp_path):
    """首尾帧镜头缺的必须是它自己那个字段：``keyframe_prompt`` 为空仍算未完成。"""

    lead, identity = _ready_identity_assets(tmp_path)
    sketches = tmp_path / "sketches" / "ep001"
    sketches.mkdir(parents=True, exist_ok=True)
    (sketches / "beat_01.png").write_bytes(b"sketch")
    record_sketch_detection_complete(tmp_path, 1, [1])
    store = _Store(
        characters=[lead],
        episode=SimpleNamespace(number=1, identity_ids=[identity.identity_id]),
        beats=[
            {
                "beat_number": 1,
                "visual_description": "镜头1",
                "detected_identities": ["__NO_CHARACTER__"],
                "detected_props": ["__NO_PROP__"],
                "video_mode": "keyframe",
                "video_prompt": "只写了首帧字段",
            }
        ],
    )
    monkeypatch.setattr(
        project_config,
        "load_project_config",
        lambda *_args: {"visual_style": AUTO_VISUAL_STYLE},
    )

    state = await _status(monkeypatch, tmp_path, store)

    assert state["episode_status"]["global_optimize"] is False
    assert state["next_step"] == "global_optimize_video"


@pytest.mark.asyncio
async def test_sketch_change_invalidates_detection_evidence(monkeypatch, tmp_path):
    lead, identity = _ready_identity_assets(tmp_path)
    sketches = tmp_path / "sketches" / "ep001"
    sketches.mkdir(parents=True, exist_ok=True)
    sketch = sketches / "beat_01.png"
    sketch.write_bytes(b"sketch")
    beats = [
        {
            "beat_number": 1,
            "visual_description": "镜头一",
            "detected_identities": ["__NO_CHARACTER__"],
            "detected_props": ["__NO_PROP__"],
        }
    ]
    store = _Store(
        characters=[lead],
        episode=SimpleNamespace(number=1, identity_ids=[identity.identity_id]),
        beats=beats,
    )

    initial = await _status(monkeypatch, tmp_path, store)
    assert initial["episode_status"]["coloring"] is False
    assert initial["next_step"] == "coloring"

    record_sketch_detection_complete(tmp_path, 1, [1])
    detected = await _status(monkeypatch, tmp_path, store)
    assert detected["episode_status"]["coloring"] is True

    sketch.write_bytes(b"changed-sketch")
    stale = await _status(monkeypatch, tmp_path, store)
    assert stale["episode_status"]["coloring"] is False
    assert stale["next_step"] == "coloring"


@pytest.mark.asyncio
async def test_locked_style_requires_matching_final_video_evidence(monkeypatch, tmp_path):
    store = _ready_episode_assets(tmp_path)
    config = {
        "visual_style": "paper_cut_folk",
        "video_backend": "seedance-2.0",
    }
    monkeypatch.setattr(project_config, "load_project_config", lambda *_args: config)
    snapshot = build_project_style_snapshot(
        "paper_cut_folk",
        username="local",
        project="pipeline-test",
        project_dir=str(tmp_path),
        video_model="seedance-2.0",
    )
    _write_locked_episode_style_evidence(tmp_path, snapshot)

    missing_evidence = await _status(monkeypatch, tmp_path, store)
    assert missing_evidence["next_step"] == "compose_episode"

    old_snapshot = build_project_style_snapshot("chinese_period_drama")
    final_path = tmp_path / "videos/episodes/ep001_final.mp4"
    write_artifact_style_evidence(final_path, old_snapshot)
    stale_evidence = await _status(monkeypatch, tmp_path, store)
    assert stale_evidence["next_step"] == "compose_episode"

    write_artifact_style_evidence(final_path, snapshot)
    current_evidence = await _status(monkeypatch, tmp_path, store)
    assert current_evidence["next_step"] == "done"


@pytest.mark.asyncio
async def test_script_auto_accepts_legacy_final_video_without_evidence(
    monkeypatch, tmp_path
):
    store = _ready_episode_assets(tmp_path)
    monkeypatch.setattr(
        project_config,
        "load_project_config",
        lambda *_args: {"visual_style": AUTO_VISUAL_STYLE},
    )

    state = await _status(monkeypatch, tmp_path, store)

    assert state["next_step"] == "done"


@pytest.mark.asyncio
async def test_pipeline_can_evaluate_against_a_frozen_run_style_snapshot(
    monkeypatch, tmp_path
):
    store = _ready_episode_assets(tmp_path)
    monkeypatch.setattr(
        project_config,
        "load_project_config",
        lambda *_args: {
            "visual_style": "paper_cut_folk",
            "video_backend": "seedance-2.0",
        },
    )
    frozen_snapshot = build_project_style_snapshot("chinese_period_drama")
    _write_locked_episode_style_evidence(tmp_path, frozen_snapshot)
    write_artifact_style_evidence(
        tmp_path / "videos/episodes/ep001_final.mp4", frozen_snapshot
    )

    current_project_state = await _status(monkeypatch, tmp_path, store)
    frozen_run_state = await _status(
        monkeypatch,
        tmp_path,
        store,
        expected_style_snapshot=frozen_snapshot,
    )

    assert current_project_state["next_step"] == "portraits"
    assert frozen_run_state["next_step"] == "done"
