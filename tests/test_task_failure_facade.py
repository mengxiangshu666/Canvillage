from __future__ import annotations

from novelvideo.services.task_failures import (
    VideoPendingResolution,
    classify_optional_dependency_failure,
    classify_video_pending,
)


def test_optional_dependency_failure_contracts() -> None:
    from novelvideo.director_world.block_world_builder import BlockWorldUnavailable
    from novelvideo.director_world.pano_sharp import Sharp3DUnavailable

    sharp = classify_optional_dependency_failure(Sharp3DUnavailable())
    block = classify_optional_dependency_failure(BlockWorldUnavailable())

    assert sharp is not None
    assert sharp[1] == {"error_code": "SHARP_3D_UNAVAILABLE"}
    assert sharp[2] is True
    assert block is not None
    assert block[1] == {"error_code": "BLOCK_WORLD_UNAVAILABLE"}
    assert block[2] is True


def test_video_pending_contracts() -> None:
    from novelvideo.freezone.jobs import (
        CompletedVideoDownloadPending,
        VideoSubmissionPending,
    )

    download = classify_video_pending(
        CompletedVideoDownloadPending(
            "download later",
            provider_task_id="provider-task-1",
        )
    )
    submission = classify_video_pending(
        VideoSubmissionPending(
            "submit later",
            idempotency_key="idem-1",
        )
    )

    assert download == VideoPendingResolution(
        kind="download",
        message="download later",
        provider_task_id="provider-task-1",
    )
    assert submission == VideoPendingResolution(
        kind="submission",
        message="submit later",
        idempotency_key="idem-1",
    )


def test_unknown_exception_is_not_reclassified() -> None:
    error = RuntimeError("ordinary failure")

    assert classify_optional_dependency_failure(error) is None
    assert classify_video_pending(error) is None
