from __future__ import annotations

from pathlib import Path

from novelvideo.freezone import canvas_store
from novelvideo.freezone.paths import canvas_path
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.runners.canvas_media import (
    commit_media_result_to_canvas,
    media_file_metadata,
)


def _context(tmp_path: Path) -> ProjectContext:
    output_dir = tmp_path / "output"
    state_dir = tmp_path / "state"
    runtime_dir = tmp_path / "runtime"
    for path in (output_dir, state_dir, runtime_dir):
        path.mkdir()
    return ProjectContext(
        project_id="project-1",
        project_name="canvas-media-test",
        owner_type="user",
        owner_id="local",
        owner_username="local",
        requester_user_id="local",
        requester_username="local",
        requester_principals=(("user", "local"),),
        effective_role="owner",
        home_node_id="local",
        output_dir=output_dir,
        state_dir=state_dir,
        runtime_dir=runtime_dir,
        is_home_node=True,
    )


def test_video_result_commit_reconciles_visible_request_controls(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    payload = canvas_store.default_canvas_payload(
        project_id=ctx.project_id,
        actor_id="local",
    )
    payload.update(
        canvas_id="canvas-1",
        nodes=[
            {
                "id": "video-1",
                "type": "videoNode",
                "position": {"x": 0, "y": 0},
                "data": {
                    "videoUrl": None,
                    "quality": "2K",
                    "resolution": "2k",
                    "durationSec": 10,
                    "generateAudio": True,
                    "aspectRatio": "16:9",
                    "genMode": "textToVideo",
                    "model": "old-model",
                },
            }
        ],
        edges=[],
    )
    target = canvas_path(ctx.state_dir, "canvas-1")
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas_store.atomic_write_json(target, payload)

    receipt = commit_media_result_to_canvas(
        ctx=ctx,
        payload={
            "canvas_id": "canvas-1",
            "node_id": "video-1",
            "resolution": "768p",
            "duration_seconds": 5,
            "generate_audio": False,
            "aspect_ratio": "16:9",
            "gen_mode": "textToVideo",
            "model_id": "direct_video-fixture",
            "preview_url": "/static/video/job-1.preview.jpg",
        },
        task_type="freezone_video_gen",
        job_id="job-1",
        output_url="/static/video/job-1.mp4",
        media_type="video",
        media_metadata={"width": 720, "height": 1280},
    )

    assert receipt is not None
    snapshot = canvas_store.read_canvas(ctx.state_dir, "canvas-1")
    assert snapshot is not None
    data = next(node for node in snapshot["nodes"] if node["id"] == "video-1")["data"]
    assert data["videoUrl"] == "/static/video/job-1.mp4"
    assert data["previewImageUrl"] == "/static/video/job-1.preview.jpg"
    assert data["quality"] == "768P"
    assert data["resolution"] == "768p"
    assert data["durationSec"] == 5
    assert data["generateAudio"] is False
    assert data["lastRequestedResolution"] == "768p"
    assert data["lastRequestedDurationSeconds"] == 5
    assert data["lastRequestedGenerateAudio"] is False
    assert data["model"] == "direct_video-fixture"
    assert data["widthPx"] == 720
    assert data["heightPx"] == 1280
    assert data["actualWidth"] == 720
    assert data["actualHeight"] == 1280
    assert data["actualAspectRatio"] == "9:16"
    assert data["aspectRatioMismatch"] is True


def _canvas_with_node(ctx: ProjectContext, node_data: dict) -> None:
    payload = canvas_store.default_canvas_payload(
        project_id=ctx.project_id,
        actor_id="local",
    )
    payload.update(
        canvas_id="canvas-1",
        nodes=[
            {
                "id": "media-1",
                "type": "videoNode",
                "position": {"x": 0, "y": 0},
                "data": node_data,
            }
        ],
        edges=[],
    )
    target = canvas_path(ctx.state_dir, "canvas-1")
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas_store.atomic_write_json(target, payload)


def test_video_commit_keeps_actual_source_and_clears_unknown_replacement(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    source = {"schema": "video_generation_source.v1", "task_type": "freezone_video_gen", "job_id": "job",
              "output_url": "/video.mp4", "execution_prompt_sha256": "a" * 64}
    _canvas_with_node(ctx, {"prompt": "later edit", "videoGenerationSource": source})
    for receipt in (source, None):
        commit_media_result_to_canvas(ctx=ctx, payload={"canvas_id": "canvas-1", "node_id": "media-1"},
            task_type="freezone_video_gen", job_id="job" if receipt else "old-job", output_url="/video.mp4",
            media_type="video", media_metadata={"video_generation_source": receipt})
        data = canvas_store.read_canvas(ctx.state_dir, "canvas-1")["nodes"][0]["data"]
        assert data["videoGenerationSource"] == receipt
        assert data["prompt"] == "later edit"


def _node_data(ctx: ProjectContext) -> dict:
    snapshot = canvas_store.read_canvas(ctx.state_dir, "canvas-1")
    assert snapshot is not None
    return next(node for node in snapshot["nodes"] if node["id"] == "media-1")["data"]


def test_media_file_metadata_reports_bytes_and_container_type(tmp_path: Path) -> None:
    artifact = tmp_path / "clip.mp4"
    artifact.write_bytes(b"0123456789")

    metadata = media_file_metadata(artifact)

    assert metadata == {"byteSize": 10, "mimeType": "video/mp4"}


def test_media_file_metadata_is_empty_for_a_missing_or_empty_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")

    assert media_file_metadata(None) == {}
    assert media_file_metadata(empty) == {}
    assert media_file_metadata(tmp_path / "never-written.mp4") == {}


def test_commit_records_resource_meta_facts_under_the_node(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    _canvas_with_node(ctx, {"videoUrl": None, "aspectRatio": "16:9"})
    artifact = ctx.output_dir / "job-2.mp4"
    artifact.write_bytes(b"x" * 2048)

    receipt = commit_media_result_to_canvas(
        ctx=ctx,
        payload={"canvas_id": "canvas-1", "node_id": "media-1"},
        task_type="freezone_video_gen",
        job_id="job-2",
        output_url="/static/video/job-2.mp4",
        media_type="video",
        media_metadata={"width": 1280, "height": 720, "durationSec": 5.125},
        local_path=artifact,
    )

    assert receipt is not None
    meta = _node_data(ctx)["resourceMeta"]
    assert meta == {
        "byteSize": 2048,
        "durationSec": 5.125,
        "width": 1280,
        "height": 720,
        "mimeType": "video/mp4",
        "kind": "video",
    }


def test_commit_keeps_the_actual_length_off_the_requested_duration_field(
    tmp_path: Path,
) -> None:
    """The panel's `durationSec` is the request; the probe is the artifact."""
    ctx = _context(tmp_path)
    _canvas_with_node(ctx, {"videoUrl": None, "aspectRatio": "16:9"})
    artifact = ctx.output_dir / "job-3.mp4"
    artifact.write_bytes(b"x" * 16)

    commit_media_result_to_canvas(
        ctx=ctx,
        payload={
            "canvas_id": "canvas-1",
            "node_id": "media-1",
            "duration_seconds": 10,
        },
        task_type="freezone_video_gen",
        job_id="job-3",
        output_url="/static/video/job-3.mp4",
        media_type="video",
        media_metadata={"durationSec": 4.0},
        local_path=artifact,
    )

    data = _node_data(ctx)
    assert data["durationSec"] == 10
    assert data["resourceMeta"]["durationSec"] == 4.0


def test_commit_clears_stale_resource_meta_when_nothing_is_known(tmp_path: Path) -> None:
    """A commit with no provable facts must not leave the previous file's numbers.

    A remote-only artifact (no local bytes probed) replaces whatever the node
    held; keeping the old byte size would show a size that belongs to a file the
    node no longer points at.
    """
    ctx = _context(tmp_path)
    _canvas_with_node(
        ctx,
        {
            "videoUrl": None,
            "aspectRatio": "16:9",
            "resourceMeta": {"byteSize": 4096, "kind": "video"},
        },
    )

    commit_media_result_to_canvas(
        ctx=ctx,
        payload={"canvas_id": "canvas-1", "node_id": "media-1"},
        task_type="freezone_video_gen",
        job_id="job-4",
        output_url="https://cdn.example.com/remote.mp4",
        media_type="video",
    )

    assert _node_data(ctx)["resourceMeta"] is None
