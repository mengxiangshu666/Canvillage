from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from novelvideo.task_backend.run_core import _attach_task_output_artifacts


def _context(tmp_path: Path) -> SimpleNamespace:
    output_dir = tmp_path / "output"
    return SimpleNamespace(
        project_id="project-a",
        project_name="project-a",
        owner_username="owner-a",
        output_dir=output_dir,
        state_dir=tmp_path / "state" / "owner-a" / "project-a",
        runtime_dir=tmp_path / "runtime" / "owner-a" / "project-a",
    )


def test_completed_media_output_is_materialized_as_a_stable_agent_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from novelvideo.utils import project_paths

    monkeypatch.setattr(project_paths, "STATE_DIR", tmp_path / "state")
    ctx = _context(tmp_path)
    source = ctx.output_dir / "videos" / "take.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"real-media-bytes")

    result = _attach_task_output_artifacts(
        ctx=ctx,
        task_type="freezone_video_gen",
        task_id="task-video-a",
        result={"output_path": str(source), "output_url": "https://provider.example.invalid/signed"},
    )

    assert isinstance(result, dict)
    artifact = result["agent_artifact"]
    assert artifact["kind"] == "freezone_video_gen"
    assert artifact["status"] == "verified"
    assert artifact["task_id"] == "task-video-a"
    assert len(artifact["artifact_sha256"]) == 64
    assert artifact["verification"]["schema"] == "task_output_store.v1"
    root = tmp_path / "state" / "_shared" / "artifacts"
    assert list(root.rglob("*.mp4"))
    assert "provider.example.invalid" not in str(artifact)


def test_task_output_artifacts_ignore_urls_and_paths_outside_project(tmp_path: Path, monkeypatch) -> None:
    from novelvideo.utils import project_paths

    monkeypatch.setattr(project_paths, "STATE_DIR", tmp_path / "state")
    ctx = _context(tmp_path)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")

    result = _attach_task_output_artifacts(
        ctx=ctx,
        task_type="freezone_video_gen",
        task_id="task-video-a",
        result={
            "output_path": str(outside),
            "output_url": "https://provider.example.invalid/signed",
        },
    )

    assert result == {
        "output_path": str(outside),
        "output_url": "https://provider.example.invalid/signed",
    }
