import pytest

from novelvideo.task_backend.runners.scene_reference import (
    ReverseNeedDecision,
    build_reverse_need_prompt,
    should_chain_reverse_master,
)


def test_reverse_need_prompt_carries_scene_env_and_beat_evidence():
    prompt = build_reverse_need_prompt(
        "正魔边境",
        "正面：荒漠古道\n背面：仙道山麓",
        ["孟叔回头看向山麓方向", "狼群从背后包抄上来"],
    )

    assert "正魔边境" in prompt
    assert "仙道山麓" in prompt
    assert "孟叔回头看向山麓方向" in prompt
    # 明确禁止模型把 360 全景混进判断
    assert "360" in prompt


def test_reverse_need_prompt_without_beats_says_so():
    prompt = build_reverse_need_prompt("空场景", "正面：空屋", [])

    assert "本集无绑定镜头" in prompt


def test_chain_only_for_master_when_auto_and_needed_and_missing():
    needed = ReverseNeedDecision(need_reverse=True, confidence=0.9, reason="有反打")
    not_needed = ReverseNeedDecision(need_reverse=False, confidence=0.9, reason="纯正面")

    assert should_chain_reverse_master(
        kind="master", auto_enabled=True, reverse_exists=False, decision=needed
    )
    # 不需要 / 已有背面图 / 开关关闭 / 判断失败 / 不是正面图任务，都不补
    assert not should_chain_reverse_master(
        kind="master", auto_enabled=True, reverse_exists=False, decision=not_needed
    )
    assert not should_chain_reverse_master(
        kind="master", auto_enabled=True, reverse_exists=True, decision=needed
    )
    assert not should_chain_reverse_master(
        kind="master", auto_enabled=False, reverse_exists=False, decision=needed
    )
    assert not should_chain_reverse_master(
        kind="master", auto_enabled=True, reverse_exists=False, decision=None
    )
    assert not should_chain_reverse_master(
        kind="reverse_master", auto_enabled=True, reverse_exists=False, decision=needed
    )


@pytest.mark.asyncio
async def test_jev_reverse_need_maps_probability_to_decision(monkeypatch):
    """jev 高概率判需要、低概率判不需要、中间地带交回文本模型。"""

    import novelvideo.services.judgment as jev
    import novelvideo.task_backend.runners.scene_reference as runner

    def patch_jev(probability: float):
        def _judge(*args, **kwargs):
            return probability

        monkeypatch.setattr(jev, "judge_yes_no", _judge)

    patch_jev(0.9)
    decision = await runner._decide_reverse_need_via_jev("办公室", "背面有门", ["回头看向门"])
    assert decision is not None and decision.need_reverse and decision.confidence == 0.8

    patch_jev(0.1)
    decision = await runner._decide_reverse_need_via_jev("办公室", "背面有门", [])
    assert decision is not None and not decision.need_reverse

    patch_jev(0.5)
    decision = await runner._decide_reverse_need_via_jev("办公室", "背面有门", [])
    assert decision is None  # 拿不准，交回文本模型


@pytest.mark.asyncio
async def test_jev_failure_propagates_for_runner_fallback(monkeypatch):
    """jev 不可用（比如没配密钥）时异常向外抛，由任务运行器退回文本模型。"""

    import pytest

    import novelvideo.services.judgment as jev
    import novelvideo.task_backend.runners.scene_reference as runner

    def _no_jev(*args, **kwargs):
        raise jev.JudgmentError("JEV 判断不可用：未配置密钥")

    monkeypatch.setattr(jev, "judge_yes_no", _no_jev)

    with pytest.raises(jev.JudgmentError):
        await runner._decide_reverse_need_via_jev("办公室", "背面有门", [])
