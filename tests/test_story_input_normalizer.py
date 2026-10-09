from __future__ import annotations

from novelvideo.story_input_normalizer import merge_story_fragments, normalize_story_input


def test_normalizer_removes_preamble_and_keeps_story_beats() -> None:
    result = normalize_story_input(
        """《雨夜最后一班车》

        类型：都市奇幻短片
        目标：一集，约六十秒，16:9
        主角林澈，短黑发，深灰风衣。
        关键道具是一张黄色烧焦车票。

        第一幕：暴雨中的17号站牌
        林澈从积水边捡起车票。
        第二幕：无人公交驶入总站
        车门在雨幕中打开。
        """
    )

    assert result.metadata["作品标题"] == "雨夜最后一班车"
    assert result.metadata["类型"] == "都市奇幻短片"
    assert result.beat_lines == (
        "第一幕：暴雨中的17号站牌",
        "林澈从积水边捡起车票。",
        "第二幕：无人公交驶入总站",
        "车门在雨幕中打开。",
    )


def test_normalizer_removes_repeated_metadata_inside_story() -> None:
    result = normalize_story_input(
        """第一幕：雨夜站牌
        林澈等待末班车。
        作品标题：雨夜最后一班车
        类型：都市奇幻短片
        目标：一集，约六十秒，16:9
        第二幕：公交车进站
        无人公交驶入雨幕。
        作品标题：雨夜最后一班车
        类型：都市奇幻短片
        目标：一集，约六十秒，16:9
        第三幕：时间碎裂
        车窗中的时间同时碎裂。
        """
    )

    assert result.beat_lines == (
        "第一幕：雨夜站牌",
        "林澈等待末班车。",
        "第二幕：公交车进站",
        "无人公交驶入雨幕。",
        "第三幕：时间碎裂",
        "车窗中的时间同时碎裂。",
    )
    assert result.beat_source_text.count("作品标题") == 0
    assert len(result.normalized_content_hash) == 64


def test_normalizer_does_not_delete_a_normal_plot_sentence_with_metadata_word() -> None:
    result = normalize_story_input(
        """第一幕：废弃车站
        他的目标终于从逃跑变成留下。
        她问：“你的目标是什么？”
        """
    )

    assert result.beat_lines == (
        "第一幕：废弃车站",
        "他的目标终于从逃跑变成留下。",
        "她问：“你的目标是什么？”",
    )


def test_normalizer_preserves_a_standalone_quoted_dialogue_line() -> None:
    result = normalize_story_input(
        """《车站》
第一幕：林澈听见身后传来声音。
“别让这辆车开过第三个路口。”
"""
    )

    assert result.beat_lines == (
        "第一幕：林澈听见身后传来声音。",
        "“别让这辆车开过第三个路口。”",
    )


def test_normalizer_is_empty_for_metadata_only_input() -> None:
    result = normalize_story_input(
        """《只有设定》
        类型：悬疑
        风格：写实
        目标：一集
        """
    )

    assert result.beat_source_text == ""
    assert result.beat_lines == ()


def test_normalizer_splits_long_story_paragraphs_and_deduplicates_import_blocks() -> None:
    result = normalize_story_input(
        """《雨夜最后一班车》
        类型：都市奇幻短片
        目标：一集，约六十秒，16:9
        主角林澈，短黑发，深灰风衣。
        第一幕：林澈在站牌下等车。他捡起烧焦车票。电子钟跳到00:17。
        第二幕：无人公交驶入总站。车门自动打开。林澈登上公交。
        连续性要求：林澈始终穿深灰风衣。
        ---
        无人公交驶入总站。车门自动打开。林澈登上公交。
        ---
        《雨夜最后一班车》
        类型：都市奇幻短片
        目标：一集，约六十秒，16:9
        主角林澈，短黑发，深灰风衣。
        第一幕：林澈在站牌下等车。他捡起烧焦车票。电子钟跳到00:17。
        第二幕：无人公交驶入总站。车门自动打开。林澈登上公交。
        """
    )

    assert result.beat_lines == (
        "第一幕：林澈在站牌下等车。",
        "他捡起烧焦车票。",
        "电子钟跳到00:17。",
        "第二幕：无人公交驶入总站。",
        "车门自动打开。",
        "林澈登上公交。",
    )


def test_normalizer_excludes_script_appendix_and_title_cards_but_keeps_story() -> None:
    source = """第一场｜天灵根
镜 1（0-4 秒）：小臣抬头看向父亲。
父亲说：“这才是全片的笑点。”
末拍停在纯黑背景上呈现第一场文字。
## 六、台词量核对（本版）
| 场次 | 镜数 | 判定 |
| 一 天灵根 | 3 | 通过 |
## 七、自检（对照剧本闸门）
☑ 故事完整
## 八、下一步
继续修改剧本
"""

    result = normalize_story_input(source)

    assert result.beat_lines == (
        "小臣抬头看向父亲。",
        "父亲说：“这才是全片的笑点。”",
    )
    assert "## 六、台词量核对（本版）" in result.removed_lines
    assert "| 一 天灵根 | 3 | 通过 |" in result.removed_lines
    assert "末拍停在纯黑背景上呈现第一场文字。" in result.removed_lines


def test_normalizer_keeps_short_filmable_text_after_shot_header() -> None:
    result = normalize_story_input(
        """第一场｜雨夜
**镜 1（0-4 秒）** 林澈抬眼。
"""
    )

    assert result.beat_lines == ("林澈抬眼。",)


def test_merge_story_fragments_prefers_one_complete_source_over_overlapping_excerpts() -> None:
    full = "第一幕：林澈等车。\n第二幕：无人公交进站。\n第三幕：时间碎裂。"
    assert merge_story_fragments(
        (
            "第二幕：无人公交进站。",
            "第三幕：时间碎裂。",
            full,
            "第二幕：无人公交进站。",
        )
    ) == full
