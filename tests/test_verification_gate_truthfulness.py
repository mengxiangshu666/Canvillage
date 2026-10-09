"""T-216 回归：验证器与门禁不得把「没证据/没法验」当成「通过」。

审计发现的三类假绿灯：
1. 颜色门在「找不到草图 / 没有颜色映射 / 抽不到角色」时报 pass；
2. 视觉门在「VLM 没给 JSON / 调用失败 / 没有候选格」时也不过是 pass；
3. 工作流预算截断把「闭合目标集」也跟着缩小，于是少交镜头照样判闭合。

这三点都与项目自己的合同「没有观测证据的 gate 必须保持 not_run」相冲突。
"""

from __future__ import annotations

from pathlib import Path

from novelvideo.verification.models import ColorVerifyResult
from novelvideo.verification.report_formatter import format_color_verify_report
from novelvideo.verification.sketch_color_verifier import verify_episode_sketch_colors
from novelvideo.verification.sketch_visual_gate import CellVerdict, GateResult


def _beat(number: int, visual: str, **extra: object) -> dict:
    return {"beat_number": number, "visual_description": visual, **extra}


# --- 颜色门 ---------------------------------------------------------------


def test_color_gate_skips_beats_it_cannot_verify(tmp_path: Path):
    beats = [
        _beat(1, "SCENE: 无角色可抽取的纯环境镜头"),          # 抽不到角色 -> skipped
        _beat(2, "角色1 走进房间"),                            # 抽到角色但没颜色映射 -> skipped
    ]
    result = verify_episode_sketch_colors(
        tmp_path, 1, beats, sketch_colors={}
    )

    assert isinstance(result, ColorVerifyResult)
    assert result.passed_beats == 0
    assert result.skipped_beats == 2
    assert result.failed_beats == 0
    # 「没能核验」不得算通过 —— 这是本次修复的核心断言。
    assert result.overall_passed is False
    assert all(item.status == "skipped" for item in result.beat_results)


def test_color_gate_report_marks_skipped_and_blocks_pass(tmp_path: Path):
    beats = [_beat(1, "角色1 走进房间")]
    result = verify_episode_sketch_colors(tmp_path, 1, beats, sketch_colors={})
    report = format_color_verify_report(result.model_dump(), 1)

    assert "未核验" in report
    assert "⏭️" in report


def test_color_gate_with_no_beats_is_still_not_a_pass(tmp_path: Path):
    result = verify_episode_sketch_colors(tmp_path, 1, [], sketch_colors={})
    assert result.total_beats == 0
    assert result.skipped_beats == 0
    # 空输入既不失败也不通过；overall_passed 仍必须是 False（没有证据）。
    assert result.overall_passed is False


# --- 视觉门 ---------------------------------------------------------------


def test_visual_gate_cell_without_verdict_is_not_run_not_passed():
    # 未评判任何模式 = not_run，绝不能算 passed。
    unjudged = CellVerdict(beat_number=1, cell_path="x.png")
    assert unjudged.not_run is True
    assert unjudged.passed is False

    errored = CellVerdict(beat_number=2, cell_path="y.png", error="vlm call failed: boom")
    assert errored.not_run is True
    assert errored.passed is False

    judged_clean = CellVerdict(
        beat_number=3, cell_path="z.png", evaluated_codes=["a", "b"]
    )
    assert judged_clean.not_run is False
    assert judged_clean.passed is True

    judged_hit = CellVerdict(
        beat_number=4, cell_path="w.png", evaluated_codes=["a"], hits=["a"]
    )
    assert judged_hit.passed is False


def test_visual_gate_no_candidates_is_flagged_not_run(tmp_path: Path):
    result = GateResult(summary_path=tmp_path / "s.json", audit_path=tmp_path / "a.json")
    result.no_candidates = True
    assert result.passed_beats == []
    assert result.not_run_beats == []
    assert result.no_candidates is True


# --- 闭合目标集（executor）------------------------------------------------


def test_closed_target_set_keeps_full_authorised_batch():
    """闭合目标不得随提交预算缩小 —— 见 executor 的 T-216 注释。

    这里只钉住语义：给定完整目标与受限提交子集，闭合检查必须以完整目标为准，
    少交一个就报未闭合，而不是因为「目标也被截断了」而放过。
    """

    from novelvideo.workflow_runtime.quality_stage import auto_media_batch_complete

    full_targets = ["shot-1", "shot-2", "shot-3"]
    produced_two = [
        {"node_id": "shot-1"},
        {"node_id": "shot-2"},
    ]

    # 正确口径：目标集完整 -> 少交 shot-3 必须被判未闭合。
    from novelvideo.workflow_runtime.quality_stage import WorkflowStepExecutionError

    try:
        auto_media_batch_complete(
            {"target_node_ids": full_targets}, produced_two
        )
    except WorkflowStepExecutionError as exc:
        assert exc.code == "workflow_media_batch_incomplete"
    else:  # pragma: no cover - 未闭合必须抛错
        raise AssertionError("未闭合的媒体批次被当成完成了")

    # 全部交出 -> 闭合。
    produced_all = [{"node_id": node} for node in full_targets]
    assert auto_media_batch_complete({"target_node_ids": full_targets}, produced_all) is True
