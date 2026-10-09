"""台词时长只做诊断，不再拦下视频提交。

2026-09-15 的真实语境：用户那条 15 秒镜头里只写了三十几个字，模型却念满了全程；
把台词关进槽位之后，时长与台词量的匹配曾经被加回成硬闸门。

2026-10-02 用户明确要求取消画布上的这条硬拦截。现在的行为：
- 台词语速只用 `4.0` 字/秒的规划值写服务端日志；
- 台词说不完不再产生 `VideoRequestIssue`，也不会禁用提交；
- 提示词时长不一致、引用素材缺失两条真实合同校验保持不变；
- 台词仍不会自动截断。
"""

import pytest

from novelvideo.freezone.video_request_contract import (
    DIALOGUE_PLANNING_CHARS_PER_SECOND,
    dialogue_duration_target,
    dialogue_speech_char_count,
    validate_video_request_contract,
)


def _codes(issues) -> list[str]:
    return [issue.code for issue in issues]


def test_speech_char_count_counts_cjk_per_character_and_latin_per_word():
    assert dialogue_speech_char_count(["我不回去了"]) == 5
    assert dialogue_speech_char_count(["你终于来了。", "我们走吧。"]) == 9
    # 标点不计。
    assert dialogue_speech_char_count(["你好，世界！"]) == 4
    # 西文按词计，数字串算一个词。
    assert dialogue_speech_char_count(["I will be back in 5 minutes."]) == 6


def test_speech_char_count_accepts_a_plain_string():
    assert dialogue_speech_char_count("我不回去了") == 5
    assert dialogue_speech_char_count("") == 0


def test_duration_budget_uses_only_the_planning_rate_for_diagnostics():
    chars = 24

    assert DIALOGUE_PLANNING_CHARS_PER_SECOND == 4.0
    assert dialogue_duration_target(chars) == pytest.approx(6.0)
    # 没台词就不该占用任何时长。
    assert dialogue_duration_target(0) == 0.0


def test_a_line_that_cannot_finish_no_longer_blocks_submission():
    """50 字塞进 5 秒：仍然偏紧，但用户要求取消这条硬闸门。"""

    issues = validate_video_request_contract(
        prompt="男人站在雨里，镜头缓慢推近。",
        duration_seconds=5,
        spoken_dialogue=["这是一段非常长的独白" * 5],
    )

    assert _codes(issues) == []


def test_a_line_that_fits_comfortably_passes():
    issues = validate_video_request_contract(
        prompt="男人站在雨里，镜头缓慢推近。",
        duration_seconds=6,
        spoken_dialogue=["我不回去了。"],
    )

    assert issues == ()


def test_a_tight_but_possible_line_is_not_blocked():
    """24 字在 5 秒里说完：比 4 字/秒紧，但 5 秒 >= 6 字/秒的下限，只警告不拦。"""

    line = "这是一段二十四字左右的台词内容"
    issues = validate_video_request_contract(
        prompt="男人站在雨里，镜头缓慢推近。",
        duration_seconds=5,
        spoken_dialogue=[line],
    )

    # 15 字：下限 2.5 秒、舒服值 3.75 秒，5 秒都够，所以既不拦也不警告。
    assert dialogue_speech_char_count([line]) == 15
    assert _codes(issues) == []


def test_a_line_at_the_planning_boundary_is_still_allowed():
    """正好卡在规划语速上：一秒不多也一秒不少，不该被拦。"""

    line = "这是一段二十四字左右的台词内容"  # 15 字
    assert dialogue_duration_target(15) == pytest.approx(3.75)
    issues = validate_video_request_contract(
        prompt="男人站在雨里。",
        duration_seconds=4,
        spoken_dialogue=[line],
    )
    # 4 秒 > 3.75 秒的舒服值，仍有余量。
    assert _codes(issues) == []

    # 3 秒：低于舒服值 3.75，但高于下限 2.5，只警告不拦。
    tight = validate_video_request_contract(
        prompt="男人站在雨里。",
        duration_seconds=3,
        spoken_dialogue=[line],
    )
    assert _codes(tight) == []


def test_the_real_15_second_shot_is_not_blocked_by_this_gate():
    """2026-09-15 那条事故镜头的形状：15 秒 + 三十几个字。

    它的问题从来不是「台词说不完」，而是「提示词整段被念」。这条用例钉住预算门
    不会误伤真实可用的镜头。
    """

    line = "我会一直等你回来，不管要多久。"  # 14 字
    issues = validate_video_request_contract(
        prompt="雨夜街头，男人撑着伞。",
        duration_seconds=15,
        spoken_dialogue=[line],
    )

    assert _codes(issues) == []


def test_no_dialogue_and_unknown_duration_never_trip_the_gate():
    assert validate_video_request_contract(
        prompt="风吹过草地，主角沉默不语。",
        duration_seconds=6,
    ) == ()
    assert validate_video_request_contract(
        prompt="男人站在雨里。",
        duration_seconds=0,
        spoken_dialogue=["这是一段很长的独白" * 20],
    ) == ()


def test_removed_dialogue_budget_also_leaves_prompt_duration_mentions_as_diagnostics():
    """台词字数与文案秒数都只做诊断，节点时长才是提交参数。"""

    problems = validate_video_request_contract(
        prompt="生成 5 秒视频，男人站在雨里说话。",
        duration_seconds=3,
        spoken_dialogue=["这是一段很长的独白" * 5],
    )

    assert _codes(problems) == []
