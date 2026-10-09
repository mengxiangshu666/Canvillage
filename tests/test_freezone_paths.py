from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.freezone.paths import (
    ensure_video_source_path,
    resolve_static_url_to_path,
)


def test_resolve_project_static_url_decodes_quoted_relpath(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    source = project_dir / "assets" / "characters" / "陈默" / "portrait.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"png")

    resolved = resolve_static_url_to_path(
        "/static/projects/01KSEFAPS6DM42P0HPASKYR4GM/"
        "assets/characters/%E9%99%88%E9%BB%98/portrait.png?v=123",
        project_dir,
    )

    assert resolved == source.resolve()
    assert resolved.exists()


def test_resolve_project_relative_url_decodes_quoted_relpath(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    mask = project_dir / "freezone" / "_uploads" / "遮罩.png"
    mask.parent.mkdir(parents=True)
    mask.write_bytes(b"png")

    resolved = resolve_static_url_to_path(
        "/freezone/_uploads/%E9%81%AE%E7%BD%A9.png#mask",
        project_dir,
    )

    assert resolved == mask.resolve()


def test_resolve_static_url_still_rejects_escaped_encoded_paths(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    with pytest.raises(ValueError, match="outside project"):
        resolve_static_url_to_path(
            "/static/projects/proj_123/%2E%2E/secret.png",
            project_dir,
        )


def test_ensure_video_source_path_accepts_every_container_the_uploader_allows(
    tmp_path: Path,
) -> None:
    """The browser accepts more containers than the committable slot list."""

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    for name in ("clip.mp4", "clip.MOV", "clip.mkv", "clip.m2ts", "clip.mxf"):
        path = project_dir / name
        path.write_bytes(b"\x00")
        assert ensure_video_source_path(path, project_dir=project_dir) == path


def test_ensure_video_source_path_names_the_json_that_was_passed_as_video(
    tmp_path: Path,
) -> None:
    """A ``freezone_text_translate`` JSON was fed to ffmpeg as a video input."""

    project_dir = tmp_path / "project"
    translated = (
        project_dir
        / "freezone"
        / "_outputs"
        / "freezone_text_translate"
        / "d8bddde239d64d00.json"
    )
    translated.parent.mkdir(parents=True)
    translated.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        ensure_video_source_path(translated, project_dir=project_dir)

    message = str(excinfo.value)
    assert "抽帧需要一个视频文件" in message
    assert ".json" in message
    # The report shows the project-relative path, not the absolute one.
    assert "freezone_text_translate/d8bddde239d64d00.json" in message


def test_ensure_video_source_path_rejects_extensionless_files(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    blob = project_dir / "blob"
    blob.write_bytes(b"\x00")

    with pytest.raises(ValueError, match="无扩展名文件"):
        ensure_video_source_path(blob, project_dir=project_dir)
