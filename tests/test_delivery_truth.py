from novelvideo.chat.service import _delivery_truth, _enforce_delivery_truth
from novelvideo.production.delivery_qc_contract import (
    DELIVERY_QC_RELEASE_CHECKS,
)


def _workflow_with_qc(
    *,
    failed: set[str] | None = None,
    not_run: set[str] | None = None,
    artifact_hash: str = "a" * 64,
    qc_hash: str = "a" * 64,
) -> dict:
    failed = failed or set()
    not_run = not_run or set()
    checks = {
        name: {
            "name": name,
            "status": (
                "failed" if name in failed else "not_run" if name in not_run else "passed"
            ),
            "passed": False if name in failed else None if name in not_run else True,
            "evidence": None if name in not_run else qc_hash if name == "sha256" else {},
        }
        for name in DELIVERY_QC_RELEASE_CHECKS
    }
    passed = False if failed else None if not_run else True
    return {
        "id": "wfr-release-gate",
        "status": "completed",
        "artifacts": {
            "final_film": {
                "status": "completed",
                "final_compose_artifact": {"sha256": artifact_hash},
                "delivery_qc": {
                    "schema": "delivery_qc_contract.v1",
                    "checks": checks,
                    "passed": passed,
                },
            }
        },
    }


def test_emit_only_receipt_cannot_keep_success_text():
    verification = _delivery_truth(
        write_attempted=True,
        receipt={
            "server_applied": False,
            "revision": None,
            "applied_ops": 0,
            "structure_status": "emit_only",
        },
        workflow_run={},
    )

    assert verification["status"] == "incomplete"
    text = _enforce_delivery_truth("第一集已完成并已核验。", verification=verification)
    assert "没有真正写入画布" in text
    assert "已完成并已核验" not in text
    assert "server_applied" not in text
    assert "applied_ops" not in text


def test_completed_workflow_preserves_model_summary():
    verification = _delivery_truth(
        write_attempted=True,
        receipt={},
        workflow_run={"id": "wfr-1", "status": "completed"},
    )

    assert verification["status"] == "verified_success"
    assert _enforce_delivery_truth("导演计划已落地。", verification=verification) == "导演计划已落地。"


def test_completed_film_with_failed_qc_cannot_claim_release_readiness():
    verification = _delivery_truth(
        write_attempted=False,
        receipt={},
        workflow_run=_workflow_with_qc(
            failed={"freeze_frames", "audio_activity"}
        ),
    )

    assert verification["status"] == "release_blocked"
    assert verification["workflow_verified"] is True
    assert verification["delivery_success"] is False
    rendered = _enforce_delivery_truth(
        "成片已经完成并通过检查，可以发布了。",
        verification=verification,
    )
    assert "可以发布" not in rendered
    assert "发布门未通过" in rendered
    assert "freeze_frames" in rendered
    assert "audio_activity" in rendered


def test_completed_film_with_unrun_qc_cannot_claim_release_readiness():
    verification = _delivery_truth(
        write_attempted=False,
        receipt={},
        workflow_run=_workflow_with_qc(not_run={"frame_rate", "loudness"}),
    )

    assert verification["status"] == "release_unverified"
    assert verification["delivery_success"] is False
    rendered = _enforce_delivery_truth(
        "成片文件已经生成。",
        verification=verification,
    )
    assert rendered.startswith("成片文件已经生成。")
    assert "发布门尚未通过" in rendered
    assert "frame_rate" in rendered


def test_completed_film_with_passed_qc_keeps_release_ready_success():
    verification = _delivery_truth(
        write_attempted=False,
        receipt={},
        workflow_run=_workflow_with_qc(),
    )

    assert verification["status"] == "verified_success"
    assert verification["release_status"] == "ready"
    assert verification["delivery_success"] is True
    assert _enforce_delivery_truth(
        "成片已通过工程检查，可以进入发布流程。",
        verification=verification,
    ) == "成片已通过工程检查，可以进入发布流程。"


def test_passing_qc_without_final_compose_artifact_cannot_claim_release():
    run = _workflow_with_qc()
    del run["artifacts"]["final_film"]["final_compose_artifact"]
    verification = _delivery_truth(write_attempted=False, receipt={}, workflow_run=run)
    assert verification["status"] == "release_unverified"
    assert verification["delivery_success"] is False
    rendered = _enforce_delivery_truth("可以发布了。", verification=verification)
    assert "可以发布" not in rendered
    assert "final_compose_artifact" in rendered


def test_passing_qc_for_another_film_cannot_claim_release():
    verification = _delivery_truth(
        write_attempted=False, receipt={},
        workflow_run=_workflow_with_qc(artifact_hash="a" * 64, qc_hash="b" * 64),
    )
    assert verification["status"] == "release_blocked"
    assert verification["delivery_success"] is False
    rendered = _enforce_delivery_truth("可以发布了。", verification=verification)
    assert "可以发布" not in rendered
    assert "artifact_sha256_match" in rendered


def test_read_only_turn_is_not_marked_incomplete():
    verification = _delivery_truth(
        write_attempted=False,
        receipt={},
        workflow_run={},
    )

    assert verification["status"] == "not_applicable"
    assert _enforce_delivery_truth("当前画布共有 10 个节点。", verification=verification) == "当前画布共有 10 个节点。"


def test_readback_failure_cannot_be_reported_as_verified_success():
    verification = _delivery_truth(
        write_attempted=True,
        receipt={
            "server_applied": True,
            "readback_verified": False,
            "revision": 8,
            "applied_ops": 1,
            "structure_status": "server_applied_readback_failed",
        },
        workflow_run={},
    )

    assert verification["status"] == "incomplete"
    rendered = _enforce_delivery_truth("画布调整已真正保存。", verification=verification)
    assert "没有真正写入画布" in rendered


def test_blocked_authorization_is_not_reported_as_canvas_write_failure():
    verification = _delivery_truth(
        write_attempted=True,
        receipt={},
        workflow_run={},
        dispatch_category="blocked_authorization",
        dispatch_error="当前交互只允许讨论或规划，不写画布",
    )

    assert verification["status"] == "blocked_authorization"
    rendered = _enforce_delivery_truth(
        "当前交互只允许讨论或规划，不写画布。",
        verification=verification,
    )
    assert "没有真正写入画布" not in rendered


def test_missing_canvas_receipt_is_not_reported_as_confirmed_write_failure():
    verification = _delivery_truth(
        write_attempted=True,
        receipt={"structure_status": "receipt_missing"},
        workflow_run={},
        dispatch_category="receipt_missing",
    )

    assert verification["status"] == "receipt_missing"
    rendered = _enforce_delivery_truth("画布写入已提交。", verification=verification)
    assert "没有真正写入画布" not in rendered
    assert "回执" in rendered


def test_verified_or_blocked_dispatch_removes_stale_unverified_guard_text():
    success = _delivery_truth(
        write_attempted=True,
        receipt={
            "server_applied": True,
            "revision": 7,
            "applied_ops": 1,
            "command_id": "cmd-success",
        },
        workflow_run={},
    )
    assert "尚未确认保存" not in _enforce_delivery_truth(
        "本次画布调度尚未确认保存，系统已停止重复写入。",
        verification=success,
    )

    blocked = _delivery_truth(
        write_attempted=True,
        receipt={},
        workflow_run={},
        dispatch_category="blocked_authorization",
        dispatch_error="当前交互只允许讨论或规划，不写画布。",
    )
    rendered = _enforce_delivery_truth(
        "本次画布调度尚未确认保存，系统已停止重复写入。",
        verification=blocked,
    )
    assert rendered == "当前交互只允许讨论或规划，不写画布。"


def test_in_progress_preserves_model_narration_and_appends_honest_note():
    """T-213（JEV 判定 guard_only）：后台运行中保留 Agent 原文，只追加诚实说明。"""

    verification = _delivery_truth(
        write_attempted=True,
        receipt={},
        workflow_run={"id": "wfr-running", "status": "running"},
    )
    assert verification["status"] == "in_progress"

    narration = "我已经把镜 1 的画面调整好了，接着连第 2 镜。"
    rendered = _enforce_delivery_truth(narration, verification=verification)

    assert narration in rendered
    assert "目前仍在进行中" in rendered
    # 原文没有被整段顶替
    assert rendered != (
        "任务已经进入后台执行，目前仍在进行中。"
        "我会沿着现有任务继续，不会把它误报为已经完成。"
    )


def test_in_progress_corrects_a_claimed_delivery():
    verification = _delivery_truth(
        write_attempted=True,
        receipt={},
        workflow_run={"id": "wfr-running", "status": "running"},
    )

    rendered = _enforce_delivery_truth("全部镜头已经完成并交付。", verification=verification)

    assert "已经完成并交付" not in rendered
    assert "不会把它误报为已经完成" in rendered


def test_canvas_failure_never_echoes_an_internal_error_code():
    """T-213（JEV：内部错误码不许进用户可见文本）。"""

    verification = _delivery_truth(
        write_attempted=True,
        receipt={},
        workflow_run={},
        dispatch_category="canvas_failed",
        dispatch_error_code="FZ_EMIT_UNVERIFIED",
        dispatch_error="命令批次只修改已有节点或已有连线，不创建替代工作流",
    )
    assert verification["status"] == "canvas_failed"

    rendered = _enforce_delivery_truth("这轮我先检查一下，然后继续。", verification=verification)
    assert "FZ_EMIT_UNVERIFIED" not in rendered
    assert "命令批次" not in rendered
    assert "没" in rendered and "保持不变" in rendered
    # 模型原文仍保留
    assert "这轮我先检查一下" in rendered

    claimed = _enforce_delivery_truth("已经写入画布。", verification=verification)
    assert "已经写入画布" not in claimed


def test_blocked_code_like_reason_is_replaced_by_plain_language():
    verification = _delivery_truth(
        write_attempted=True,
        receipt={},
        workflow_run={},
        dispatch_category="blocked_authorization",
        dispatch_error_code="execution_not_authorized",
        dispatch_error="execution_not_authorized",
    )
    assert verification["status"] == "blocked_authorization"

    rendered = _enforce_delivery_truth(
        "本次画布调度尚未确认保存，系统已停止重复写入。",
        verification=verification,
    )

    assert "execution_not_authorized" not in rendered
    assert "未进入画布写入" in rendered


def test_canvas_failure_status_helper_is_reachable():
    verification = _delivery_truth(
        write_attempted=True,
        receipt={},
        workflow_run={},
        dispatch_category="canvas_failed",
        dispatch_error="boom",
    )
    assert verification["status"] == "canvas_failed"
