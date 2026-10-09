"""重画任务对临时故障自动重试，且缺镜头时不许报完成。

实践里翻过车：上游 504 把 6 张重画中的一张弄丢，任务却报完成，43 号
镜头被留在旧图上没人知道。地基规则：
1. 上游超时/限流/5xx → 原样重试，最多三次；
2. 内容性失败（安全拒绝）不重试，省冤枉钱；
3. 只要有没有生成的镜头，任务必须失败并写明缺哪几张。
"""

import asyncio

import pytest

from novelvideo.generators import nanobanana_grid
from novelvideo.task_backend.runners import render


class _ScriptedGenerator:
    """按脚本逐次返回结果，记录调用次数；字符串判失败，True 判成功。"""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def generate_grid(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if outcome is True:
            return nanobanana_grid.GridGenerationResult(
                success=True, grid_image_path=kwargs["output_path"]
            )
        return nanobanana_grid.GridGenerationResult(success=False, error=outcome)


def _run_regen(monkeypatch, tmp_path, generator):
    monkeypatch.setattr(
        nanobanana_grid, "create_grid_generator", lambda *a, **k: generator
    )

    async def run():
        return await nanobanana_grid.regenerate_selected_beats(
            selected_beats=[{"beat_number": 43}],
            mode_key="1x1_2-3",
            character_map={},
            style="xianxia",
            output_dir=str(tmp_path),
            is_sketch=True,
        )

    return asyncio.run(run())


def _fast_sleep(monkeypatch):
    async def fast_sleep(seconds):
        pass

    monkeypatch.setattr(nanobanana_grid.asyncio, "sleep", fast_sleep)


def test_transient_upstream_failure_is_retried_until_success(monkeypatch, tmp_path):
    _fast_sleep(monkeypatch)
    generator = _ScriptedGenerator(["HTTP 504: 上游图像服务暂时不可用", True])
    results = _run_regen(monkeypatch, tmp_path, generator)
    assert generator.calls == 2
    assert results[0].success is True


def test_permanent_content_failure_is_not_retried(monkeypatch, tmp_path):
    generator = _ScriptedGenerator(["提示词被安全系统拒绝，改写重试后仍被拒绝"])
    results = _run_regen(monkeypatch, tmp_path, generator)
    assert generator.calls == 1
    assert results[0].success is False
    assert "安全系统拒绝" in results[0].error


def test_two_panel_still_retries(monkeypatch, tmp_path):
    _fast_sleep(monkeypatch)
    generator = _ScriptedGenerator(
        ["单格回图被画成了上下两格，与「一个镜头一个画面」不符。已拒绝落盘，请重试。", True]
    )
    results = _run_regen(monkeypatch, tmp_path, generator)
    assert generator.calls == 2
    assert results[0].success is True


def test_missing_beats_fail_the_task():
    with pytest.raises(RuntimeError) as excinfo:
        render._raise_on_missing_beats(
            "Render 重生",
            [43, 51, 54, 78, 79, 80],
            [51, 54, 78, 79, 80],
            ["HTTP 504: 上游图像服务暂时不可用"],
        )
    message = str(excinfo.value)
    assert "43" in message
    assert "没生成成功" in message
    # 全部成功时不拦。
    render._raise_on_missing_beats("草图重生", [2], [2], [])
