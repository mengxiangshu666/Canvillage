from pathlib import Path
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.m09


class _FakeTaskManager:
    def __init__(self) -> None:
        self.updates: list[tuple[tuple, dict]] = []

    def update_progress_for_project(self, *args, **kwargs) -> None:
        self.updates.append((args, kwargs))


def _ctx(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        output_dir=str(tmp_path),
        state_dir=tmp_path / "state",
        owner_username="local",
        project_name="compose-test",
    )


def _beat_video(
    project_dir: Path, episode: int, beat_num: int, data: bytes = b"video"
) -> Path:
    path = (
        project_dir
        / "videos"
        / "beats"
        / f"ep{episode:03d}"
        / f"beat_{beat_num:02d}.mp4"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _final_video(project_dir: Path, episode: int) -> Path:
    return project_dir / "videos" / "episodes" / f"ep{episode:03d}_final.mp4"


def _success_result(*, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)


def _media_command_name(command: list[str]) -> str:
    return Path(command[0]).stem


def _write_fake_ffmpeg_output(cmd: list[str], data: bytes = b"generated") -> None:
    output_path = Path(cmd[-1])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)


@pytest.mark.parametrize(
    ("missing_beat", "empty"),
    [(2, False), (2, True)],
)
def test_compose_requires_every_positive_beat_mp4_before_running_ffmpeg(
    monkeypatch,
    tmp_path,
    missing_beat: int,
    empty: bool,
):
    from novelvideo.task_backend.runners import video

    _beat_video(tmp_path, 1, 1)
    if empty:
        _beat_video(tmp_path, 1, missing_beat, b"")
    final_path = _final_video(tmp_path, 1)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"previous-final")
    commands: list[list[str]] = []

    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(
        video,
        "run_project_subprocess",
        lambda cmd, **_kwargs: commands.append(cmd),
    )

    with pytest.raises(RuntimeError, match=r"Beat.*2.*MP4"):
        video.run_compose_episode(
            {
                "episode": 1,
                "payload": {
                    "output_dir": str(tmp_path),
                    "beats": [{"beat_number": 1}, {"beat_number": missing_beat}],
                },
            },
            _ctx(tmp_path),
        )

    assert commands == []
    assert final_path.read_bytes() == b"previous-final"


def test_compose_fails_whole_episode_when_any_beat_transcode_fails(
    monkeypatch, tmp_path
):
    from novelvideo.task_backend.runners import video

    _beat_video(tmp_path, 1, 1)
    _beat_video(tmp_path, 1, 2)
    final_path = _final_video(tmp_path, 1)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"previous-final")

    def fake_run(cmd, **_kwargs):
        if _media_command_name(cmd) == "ffprobe":
            return _success_result()
        if _media_command_name(cmd) == "ffmpeg" and str(cmd[-1]).endswith("beat_0002.mp4"):
            return SimpleNamespace(returncode=1, stdout="", stderr="broken beat")
        _write_fake_ffmpeg_output(cmd)
        return _success_result()

    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    with pytest.raises(RuntimeError, match=r"Beat 2.*转码失败"):
        video.run_compose_episode(
            {
                "episode": 1,
                "payload": {
                    "output_dir": str(tmp_path),
                    "beats": [{"beat_number": 1}, {"beat_number": 2}],
                },
            },
            _ctx(tmp_path),
        )

    assert final_path.read_bytes() == b"previous-final"


def test_compose_requires_independent_audio_for_dialogue_beats(monkeypatch, tmp_path):
    from novelvideo.task_backend.runners import video

    _beat_video(tmp_path, 1, 1)
    final_path = _final_video(tmp_path, 1)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"previous-final")
    commands: list[list[str]] = []

    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(
        video,
        "run_project_subprocess",
        lambda cmd, **_kwargs: commands.append(cmd),
    )

    with pytest.raises(RuntimeError, match=r"Beat 1.*dialogue.*缺少独立音频"):
        video.run_compose_episode(
            {
                "episode": 1,
                "payload": {
                    "output_dir": str(tmp_path),
                    "beats": [
                        {
                            "beat_number": 1,
                            "audio_type": "dialogue",
                            "dialogue_text": "台词",
                        }
                    ],
                },
            },
            _ctx(tmp_path),
        )

    assert commands == []
    assert final_path.read_bytes() == b"previous-final"


def test_compose_allows_dialogue_without_mp3_when_video_has_native_audio(
    monkeypatch, tmp_path
):
    """视频模型自带音轨时，对白 Beat 缺独立 MP3 也能合成。"""

    from novelvideo.task_backend.runners import video

    _beat_video(tmp_path, 1, 1)
    commands: list[list[str]] = []

    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(video, "_select_compose_video_encoder", lambda: "libx264")
    monkeypatch.setattr(
        video, "_video_has_audio_stream", lambda *_args, **_kwargs: True
    )

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        if _media_command_name(cmd) == "ffprobe":
            return _success_result()
        _write_fake_ffmpeg_output(cmd)
        return _success_result()

    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    result = video.run_compose_episode(
        {
            "episode": 1,
            "payload": {
                "output_dir": str(tmp_path),
                "allow_native_audio_fallback": True,
                "beats": [
                    {
                        "beat_number": 1,
                        "audio_type": "dialogue",
                        "dialogue_text": "台词",
                    }
                ],
            },
        },
        _ctx(tmp_path),
    )

    assert commands
    assert Path(result["video_path"]).is_file()
    clip_command = next(
        command
        for command in commands
        if _media_command_name(command) == "ffmpeg"
        and any("0:a:0" == str(item) for item in command)
    )
    # 使用视频内置音轨，而不是静音占位轨。
    assert "anullsrc" not in " ".join(clip_command)


def test_compose_native_audio_fallback_still_blocks_silent_dialogue(
    monkeypatch, tmp_path
):
    """放开闸门后，视频若既没有独立音频也没有内置音轨，仍然拒绝合成。"""

    from novelvideo.task_backend.runners import video

    _beat_video(tmp_path, 1, 1)
    final_path = _final_video(tmp_path, 1)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"previous-final")
    commands: list[list[str]] = []

    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(video, "_select_compose_video_encoder", lambda: "libx264")
    monkeypatch.setattr(
        video, "_video_has_audio_stream", lambda *_args, **_kwargs: False
    )

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        if _media_command_name(cmd) == "ffprobe":
            return _success_result()
        _write_fake_ffmpeg_output(cmd)
        return _success_result()

    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    with pytest.raises(RuntimeError, match=r"Beat 1.*内置音轨"):
        video.run_compose_episode(
            {
                "episode": 1,
                "payload": {
                    "output_dir": str(tmp_path),
                    "allow_native_audio_fallback": True,
                    "beats": [
                        {
                            "beat_number": 1,
                            "audio_type": "dialogue",
                            "dialogue_text": "台词",
                        }
                    ],
                },
            },
            _ctx(tmp_path),
        )

    assert final_path.read_bytes() == b"previous-final"


def test_compose_does_not_treat_unquoted_visual_prose_as_dialogue(
    monkeypatch, tmp_path
):
    from novelvideo.task_backend.runners import video

    _beat_video(tmp_path, 1, 1)
    commands: list[list[str]] = []

    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(video, "_select_compose_video_encoder", lambda: "libx264")
    monkeypatch.setattr(video, "_video_has_audio_stream", lambda *_args, **_kwargs: False)

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        if _media_command_name(cmd) == "ffprobe":
            return _success_result()
        _write_fake_ffmpeg_output(cmd)
        return _success_result()

    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    result = video.run_compose_episode(
        {
            "episode": 1,
            "payload": {
                "output_dir": str(tmp_path),
                "beats": [
                    {
                        "beat_number": 1,
                        "audio_type": "dialogue",
                        "narration_segment": "角色抬头，望向窗外",
                    }
                ],
            },
        },
        _ctx(tmp_path),
    )

    assert commands
    assert Path(result["video_path"]).is_file()


def test_compose_uses_runtime_video_encoder_for_clip_and_concat(monkeypatch, tmp_path):
    from novelvideo.task_backend.runners import video

    _beat_video(tmp_path, 1, 1)
    commands: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        if _media_command_name(cmd) == "ffprobe":
            return _success_result()
        _write_fake_ffmpeg_output(cmd)
        return _success_result()

    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(video, "run_project_subprocess", fake_run)
    monkeypatch.setattr(video, "_select_compose_video_encoder", lambda: "h264_mf")

    video.run_compose_episode(
        {
            "episode": 1,
            "payload": {
                "output_dir": str(tmp_path),
                "beats": [{"beat_number": 1}],
            },
        },
        _ctx(tmp_path),
    )

    ffmpeg_commands = [command for command in commands if _media_command_name(command) == "ffmpeg"]
    assert len(ffmpeg_commands) == 2
    assert all(
        command[command.index("-c:v") + 1] == "h264_mf"
        for command in ffmpeg_commands
    )


def test_compose_explicit_dissolve_uses_xfade_and_acrossfade(monkeypatch, tmp_path):
    from novelvideo.task_backend.runners import video

    _beat_video(tmp_path, 1, 1)
    _beat_video(tmp_path, 1, 2)
    commands: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        if _media_command_name(cmd) == "ffprobe":
            if "-select_streams" in cmd:
                return _success_result()
            durations = {"beat_0001.mp4": "2\n", "beat_0002.mp4": "3\n"}
            return _success_result(stdout=durations.get(Path(cmd[-1]).name, "5\n"))
        _write_fake_ffmpeg_output(cmd)
        return _success_result()

    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    video.run_compose_episode(
        {
            "episode": 1,
            "payload": {
                "output_dir": str(tmp_path),
                "beats": [
                    {"beat_number": 1},
                    {
                        "beat_number": 2,
                        "transition": {"kind": "dissolve", "duration_seconds": 0.8},
                    },
                ],
            },
        },
        _ctx(tmp_path),
    )

    compose_cmd = next(
        cmd for cmd in commands if _media_command_name(cmd) == "ffmpeg" and "-filter_complex" in cmd
    )
    filter_graph = compose_cmd[compose_cmd.index("-filter_complex") + 1]
    assert "xfade=transition=dissolve:duration=0.800:offset=1.200" in filter_graph
    assert "acrossfade=d=0.800:c1=tri:c2=tri" in filter_graph
    assert "concat=n=2:v=1:a=1" not in filter_graph


def test_compose_transition_duration_is_bounded_by_rendered_clip_lengths():
    from novelvideo.task_backend.runners.video import (
        _bound_transition_plan,
        resolve_transition_plan,
    )

    plan = resolve_transition_plan(
        {"beat_number": 1, "target_duration_seconds": 1.25},
        {
            "beat_number": 2,
            "target_duration_seconds": 3.0,
            "transition": {"kind": "crossfade", "duration_seconds": 4.0},
        },
    )
    bounded = _bound_transition_plan(plan, 1.25, 3.0)
    assert bounded["transition_kind"] == "crossfade"
    assert bounded["duration_seconds"] == 1.25
    assert bounded["audio_transition_kind"] == "acrossfade"


def test_compose_transition_subtitles_follow_net_timeline(monkeypatch, tmp_path):
    from novelvideo.export import episode_export
    from novelvideo.task_backend.runners import video

    _beat_video(tmp_path, 1, 1)
    _beat_video(tmp_path, 1, 2)
    commands: list[list[str]] = []
    captured_entries: list[tuple[int, float, float, str]] = []
    render_srt = episode_export.build_srt_content_from_entries

    def capture_srt(entries):
        captured_entries.extend(entries)
        return render_srt(entries)

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        if _media_command_name(cmd) == "ffprobe":
            if "-select_streams" in cmd:
                return _success_result()
            durations = {"beat_0001.mp4": "2\n", "beat_0002.mp4": "3\n"}
            return _success_result(stdout=durations.get(Path(cmd[-1]).name, "5\n"))
        _write_fake_ffmpeg_output(cmd)
        return _success_result()

    monkeypatch.setattr(episode_export, "build_srt_content_from_entries", capture_srt)
    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    video.run_compose_episode(
        {
            "episode": 1,
            "payload": {
                "output_dir": str(tmp_path),
                "add_subtitles": True,
                "beats": [
                    {"beat_number": 1, "narration_segment": "第一句"},
                    {
                        "beat_number": 2,
                        "narration_segment": "第二句",
                        "transition": {"kind": "fade", "duration_seconds": 0.5},
                    },
                ],
            },
        },
        _ctx(tmp_path),
    )

    assert captured_entries == [
        (1, 0.0, 1.5, "第一句"),
        (2, 2.0, 4.5, "第二句"),
    ]
    compose_cmd = next(
        cmd for cmd in commands if _media_command_name(cmd) == "ffmpeg" and "-filter_complex" in cmd
    )
    filter_graph = compose_cmd[compose_cmd.index("-filter_complex") + 1]
    assert "xfade=transition=fade:duration=0.500:offset=1.500" in filter_graph


def test_compose_transition_failure_preserves_previous_final_and_cleans_partial(
    monkeypatch, tmp_path
):
    from novelvideo.task_backend.runners import video

    _beat_video(tmp_path, 1, 1)
    _beat_video(tmp_path, 1, 2)
    final_path = _final_video(tmp_path, 1)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"previous-final")

    def fake_run(cmd, **_kwargs):
        if _media_command_name(cmd) == "ffprobe":
            if "-select_streams" in cmd:
                return _success_result()
            return _success_result(stdout="2\n")
        if "-filter_complex" in cmd:
            Path(cmd[-1]).write_bytes(b"partial-final")
            return SimpleNamespace(returncode=1, stdout="", stderr="xfade exploded")
        _write_fake_ffmpeg_output(cmd)
        return _success_result()

    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    with pytest.raises(RuntimeError, match=r"拼接失败"):
        video.run_compose_episode(
            {
                "episode": 1,
                "payload": {
                    "output_dir": str(tmp_path),
                    "beats": [
                        {"beat_number": 1},
                        {
                            "beat_number": 2,
                            "transition": {"kind": "crossfade", "duration_seconds": 0.5},
                        },
                    ],
                },
            },
            _ctx(tmp_path),
        )

    assert final_path.read_bytes() == b"previous-final"
    assert list(final_path.parent.glob("*.partial*.mp4")) == []


def test_compose_applies_director_trim_window_to_generated_source(monkeypatch, tmp_path):
    from novelvideo.task_backend.runners import video

    _beat_video(tmp_path, 1, 1)
    commands: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        if _media_command_name(cmd) == "ffprobe":
            return _success_result(stdout="4\n")
        _write_fake_ffmpeg_output(cmd)
        return _success_result()

    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    video.run_compose_episode(
        {
            "episode": 1,
            "payload": {
                "output_dir": str(tmp_path),
                "beats": [
                    {
                        "beat_number": 1,
                        "target_duration_seconds": 1.8,
                        "generation_duration_seconds": 4.0,
                        "trim_in_seconds": 0.4,
                        "trim_out_seconds": 2.2,
                    }
                ],
            },
        },
        _ctx(tmp_path),
    )

    clip_cmd = next(cmd for cmd in commands if _media_command_name(cmd) == "ffmpeg" and "beat_0001.mp4" in cmd[-1])
    assert clip_cmd[clip_cmd.index("-ss") + 1] == "0.400"
    assert clip_cmd[clip_cmd.index("-t") + 1] == "1.800"


def test_compose_fails_when_ffmpeg_reports_success_without_writing_clip(
    monkeypatch, tmp_path
):
    from novelvideo.task_backend.runners import video

    _beat_video(tmp_path, 1, 1)
    final_path = _final_video(tmp_path, 1)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"previous-final")

    def fake_run(cmd, **_kwargs):
        if _media_command_name(cmd) == "ffprobe":
            return _success_result()
        return _success_result()

    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    with pytest.raises(RuntimeError, match=r"Beat 1.*未产出"):
        video.run_compose_episode(
            {
                "episode": 1,
                "payload": {
                    "output_dir": str(tmp_path),
                    "beats": [{"beat_number": 1}],
                },
            },
            _ctx(tmp_path),
        )

    assert final_path.read_bytes() == b"previous-final"


def test_compose_concat_failure_preserves_previous_final(monkeypatch, tmp_path):
    from novelvideo.task_backend.runners import video

    _beat_video(tmp_path, 1, 1)
    final_path = _final_video(tmp_path, 1)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"previous-final")

    def fake_run(cmd, **_kwargs):
        if _media_command_name(cmd) == "ffprobe":
            return _success_result(stdout="5\n")
        if "-filter_complex" in cmd:
            Path(cmd[-1]).write_bytes(b"partial-final")
            return SimpleNamespace(returncode=1, stdout="", stderr="concat exploded")
        _write_fake_ffmpeg_output(cmd)
        return _success_result()

    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    with pytest.raises(RuntimeError, match=r"拼接失败"):
        video.run_compose_episode(
            {
                "episode": 1,
                "payload": {
                    "output_dir": str(tmp_path),
                    "beats": [{"beat_number": 1}],
                },
            },
            _ctx(tmp_path),
        )

    assert final_path.read_bytes() == b"previous-final"
    assert list(final_path.parent.glob("*.partial*.mp4")) == []


@pytest.mark.parametrize("style_source", ["run", "project"])
def test_compose_applies_subtitles_and_explicit_bgm_then_atomically_replaces_final(
    monkeypatch,
    tmp_path,
    style_source: str,
):
    from novelvideo.export import episode_export
    from novelvideo.styles.project_style import (
        artifact_matches_style,
        build_project_style_snapshot,
    )
    from novelvideo.task_backend.runners import video

    beats = [
        {
            "beat_number": 2,
            "shot_order": 30,
            "narration_segment": "第二句",
        },
        {
            "beat_number": 1,
            "shot_order": 10,
            "narration_segment": "第一句",
        },
        {
            "beat_number": 41,
            "shot_order": 20,
            "narration_segment": "",
            "duration_seconds": 3.0,
            "audio_type": "silence",
        },
    ]
    for beat_num in (1, 41, 2):
        _beat_video(tmp_path, 1, beat_num)
    bgm_path = tmp_path / "assets" / "music" / "theme.mp3"
    bgm_path.parent.mkdir(parents=True)
    bgm_path.write_bytes(b"music")
    final_path = _final_video(tmp_path, 1)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"previous-final")
    style_snapshot = build_project_style_snapshot(
        "paper_cut_folk",
        username="local",
        project="compose-test",
        project_dir=str(tmp_path),
        video_model="seedance-2.0",
    )
    payload_style = {}
    if style_source == "run":
        payload_style["style_snapshot"] = style_snapshot
    else:
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "project_config.json").write_text(
            '{"visual_style":"paper_cut_folk","video_backend":"seedance-2.0"}',
            encoding="utf-8",
        )
    commands: list[list[str]] = []
    captured_entries: list[tuple[int, float, float, str]] = []
    render_srt = episode_export.build_srt_content_from_entries

    def capture_srt(entries):
        captured_entries.extend(entries)
        return render_srt(entries)

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        if _media_command_name(cmd) == "ffprobe":
            if "-select_streams" in cmd:
                return _success_result()
            duration_by_name = {
                "beat_0001.mp4": "2\n",
                "beat_0041.mp4": "3\n",
                "beat_0002.mp4": "4\n",
            }
            return _success_result(
                stdout=duration_by_name.get(Path(cmd[-1]).name, "5\n")
            )
        _write_fake_ffmpeg_output(
            cmd,
            b"new-final" if "-filter_complex" in cmd else b"normalized-clip",
        )
        return _success_result()

    monkeypatch.setattr(episode_export, "build_srt_content_from_entries", capture_srt)
    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    result = video.run_compose_episode(
        {
            "episode": 1,
            "payload": {
                "output_dir": str(tmp_path),
                "beats": beats,
                "add_subtitles": True,
                "add_bgm": True,
                "bgm_path": "assets/music/theme.mp3",
                **payload_style,
            },
        },
        _ctx(tmp_path),
    )

    concat_cmd = next(cmd for cmd in commands if "-filter_complex" in cmd)
    filter_graph = concat_cmd[concat_cmd.index("-filter_complex") + 1]
    assert captured_entries == [
        (1, 0.0, 2.0, "第一句"),
        (2, 5.0, 9.0, "第二句"),
    ]
    assert "subtitles=" in filter_graph
    assert "amix=" in filter_graph
    assert str(bgm_path) in concat_cmd
    assert final_path.read_bytes() == b"new-final"
    assert artifact_matches_style(final_path, style_snapshot)
    assert result == {
        "video_path": final_path.as_posix(),
        "add_subtitles_requested": True,
        "add_bgm_requested": True,
        "subtitles_applied": True,
        "bgm_applied": True,
        "fps": 30,
        "delivery_fps": {
            "schema": "delivery_fps_contract.v1",
            "fps": 30,
            "source": "server_default",
        },
    }
    assert list(final_path.parent.glob("*.partial*.mp4")) == []


def test_compose_blocks_requested_bgm_without_source_contract(monkeypatch, tmp_path):
    from novelvideo.task_backend.runners import video

    _beat_video(tmp_path, 1, 1)
    commands: list[list[str]] = []
    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(
        video,
        "run_project_subprocess",
        lambda cmd, **_kwargs: commands.append(cmd),
    )

    with pytest.raises(RuntimeError, match=r"bgm_path.*阻止"):
        video.run_compose_episode(
            {
                "episode": 1,
                "payload": {
                    "output_dir": str(tmp_path),
                    "beats": [{"beat_number": 1}],
                    "add_bgm": True,
                },
            },
            _ctx(tmp_path),
        )

    assert commands == []


async def test_compose_route_forwards_explicit_bgm_path(monkeypatch, tmp_path):
    from novelvideo.api.routes import generation
    from novelvideo.api.schemas import VideoComposeRequest

    ctx = SimpleNamespace(project_id="project-1")
    store = SimpleNamespace()
    store.close_calls = 0

    async def get_beats(_episode):
        return [{"beat_number": 1}]

    async def close():
        store.close_calls += 1

    store.get_beats_as_dicts = get_beats
    store.close = close

    async def resolve(*_args, **_kwargs):
        return SimpleNamespace(
            ctx=ctx,
            username="alice",
            project_name="demo",
            project_dir=tmp_path,
            output_dir=str(tmp_path),
        )

    async def make_store(_ctx):
        return store

    queued_payload = {}

    async def enqueue(_ctx, **kwargs):
        queued_payload.update(kwargs["payload"])
        return SimpleNamespace(
            task_state=SimpleNamespace(task_id="task-1"),
            backend="inline",
            queue="ffmpeg",
        )

    monkeypatch.setattr(generation, "_resolve_generation_project", resolve)
    monkeypatch.setattr(generation, "make_sqlite_store_for_context", make_store)
    monkeypatch.setattr(
        generation,
        "get_task_backend",
        lambda: SimpleNamespace(enqueue_project_task=enqueue),
    )

    style_snapshot = {
        "mode": "locked",
        "style_id": "paper_cut_folk",
        "fingerprint": "paper-cut-fingerprint",
    }
    response = await generation.compose_video(
        "demo",
        1,
        VideoComposeRequest(
            add_bgm=True,
            bgm_path="assets/music/theme.mp3",
            style_snapshot=style_snapshot,
        ),
        user={"username": "alice"},
    )

    assert response["ok"] is True
    assert queued_payload["bgm_path"] == "assets/music/theme.mp3"
    assert queued_payload["style_snapshot"] == style_snapshot
    assert store.close_calls == 1
