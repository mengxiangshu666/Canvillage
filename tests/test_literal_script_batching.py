import ast
import re
from types import SimpleNamespace

import pytest

from novelvideo.workflows.literal_script_writing import (
    LiteralBeatMetaBatchOutput,
    LiteralScriptWritingWorkflow,
    compact_scene_lines_for_production,
)


class _Store:
    def __init__(self, episode):
        self.episode = episode
        self._props = {}
        self.persisted = None

    async def load_graph_state(self):
        return None

    async def get_episode_from_graph(self, episode_num):
        assert episode_num == self.episode.number
        return self.episode

    def get_episode(self, episode_num):
        assert episode_num == self.episode.number
        return self.episode

    def get_all_characters(self):
        return []

    async def persist_narration_script(self, script):
        self.persisted = script


def _episode(source_text: str):
    return SimpleNamespace(
        number=1,
        title="批处理回归",
        beat_source_text=source_text,
        content_summary="",
        identity_ids=[],
        identity_default_map={},
        scene_menu=[],
        prop_menu=[],
    )


def _batch(indices: list[int]) -> LiteralBeatMetaBatchOutput:
    return LiteralBeatMetaBatchOutput.model_validate(
        {
            "items": [
                {
                    "content_index": index,
                    "audio_type": "silence",
                    "speaker": "",
                    "speaker_kind": "character",
                    "visual_description": f"第{index}行角色完成明确动作",
                    "scene_id": "",
                }
                for index in indices
            ]
        }
    )


def _prompt_indices(prompt: str) -> list[int]:
    match = re.search(r"数量和顺序完全一致：(\[[^\n]+\])", prompt)
    assert match is not None
    return list(ast.literal_eval(match.group(1)))


class _EchoBatchAgent:
    def __init__(self, *, malformed_first: bool = False):
        self.malformed_first = malformed_first
        self.prompts: list[str] = []

    async def run(self, prompt: str):
        self.prompts.append(prompt)
        indices = _prompt_indices(prompt)
        if self.malformed_first and len(self.prompts) == 1:
            indices = list(reversed(indices))
        return SimpleNamespace(output=_batch(indices))


class _Workflow(LiteralScriptWritingWorkflow):
    def __init__(self, store, agent, output_dir=""):
        super().__init__(store, output_dir=output_dir)
        self._test_agent = agent

    @property
    def agent(self):
        return self._test_agent


@pytest.mark.asyncio
async def test_literal_run_batches_per_scene_without_crossing_blocks(monkeypatch):
    monkeypatch.setenv("LITERAL_BEAT_META_BATCH_SIZE", "4")
    source = "\n".join(
        [
            "第1场 房间 日 内",
            "△阿文站起身来。",
            "△阿文走到门边。",
            "第2场 街道 日 外",
            "△车辆驶过街口。",
            "△阿文停在人行道。",
        ]
    )
    episode = _episode(source)
    store = _Store(episode)
    agent = _EchoBatchAgent()
    workflow = _Workflow(store, agent)

    script = await workflow.run(episode_num=1, source_text=source)

    assert len(script.beats) == 4
    assert [_prompt_indices(prompt) for prompt in agent.prompts] == [[1, 2], [3, 4]]
    assert [beat.beat_number for beat in script.beats] == [1, 2, 3, 4]
    assert store.persisted is script


@pytest.mark.asyncio
async def test_literal_run_downgrades_invalid_batch_to_single_line(monkeypatch, tmp_path):
    monkeypatch.setenv("LITERAL_BEAT_META_BATCH_SIZE", "4")
    source = "\n".join(
        [
            "第1场 房间 日 内",
            "△阿文站起身来。",
            "△阿文走到门边。",
        ]
    )
    episode = _episode(source)
    store = _Store(episode)
    agent = _EchoBatchAgent(malformed_first=True)
    workflow = _Workflow(store, agent, output_dir=tmp_path)
    logs: list[str] = []

    script = await workflow.run(episode_num=1, source_text=source, on_log=logs.append)

    assert len(script.beats) == 2
    assert [_prompt_indices(prompt) for prompt in agent.prompts] == [[1, 2], [1], [2]]
    assert any("批响应无效，自动降级单行" in message for message in logs)
    assert not list(tmp_path.rglob("episode-001.json"))


@pytest.mark.asyncio
async def test_literal_run_removes_story_metadata_and_overlapping_import_fragments(
    monkeypatch,
):
    monkeypatch.setenv("LITERAL_BEAT_META_BATCH_SIZE", "4")
    source = """《雨夜最后一班车》
类型：都市奇幻短片
目标：一集，约六十秒，16:9
主角林澈，短黑发，深灰风衣。
关键场景是17号站牌。
第一幕：林澈独自在站牌下等车。他捡起烧焦车票。电子钟跳到00:17。
第二幕：无人公交驶入总站。车门自动打开。林澈登上公交。
第三幕：录音机播放妹妹留言。林澈拉下紧急制动。时间同时碎裂。
结尾：林澈回到17号站牌。电子钟停在00:17。车票出现一行新字。
连续性要求：林澈始终穿深灰风衣。
---
无人公交驶入总站。车门自动打开。林澈登上公交。
---
《雨夜最后一班车》
类型：都市奇幻短片
目标：一集，约六十秒，16:9
第一幕：林澈独自在站牌下等车。他捡起烧焦车票。电子钟跳到00:17。
第二幕：无人公交驶入总站。车门自动打开。林澈登上公交。"""
    store = _Store(_episode(source))
    agent = _EchoBatchAgent()
    workflow = _Workflow(store, agent)
    logs: list[str] = []

    script = await workflow.run(episode_num=1, source_text=source, on_log=logs.append)

    assert len(script.beats) == 12
    assert [_prompt_indices(prompt) for prompt in agent.prompts] == [
        [1, 2, 3, 4],
        [5, 6, 7, 8],
        [9, 10, 11, 12],
    ]
    combined_prompts = "\n".join(agent.prompts)
    assert "作品标题" not in combined_prompts
    assert "都市奇幻短片" not in combined_prompts
    assert "---" not in combined_prompts
    assert any("标题、设定或重复内容" in message for message in logs)


def test_compaction_keeps_scenes_and_dialogue_but_merges_adjacent_action_lines() -> None:
    source = """1-1 车厢 夜 内
雨点击打车窗。
林澈打开工具箱。
林澈：北侧第三井。
电子屏闪烁红光。
1-2 管道井 夜 外
林澈跳下侧梯。
泥浆漫过她的小腿。
小满：姐姐？
林澈：抓紧我。"""
    blocks = LiteralScriptWritingWorkflow._build_scene_blocks(source.splitlines())

    compacted = compact_scene_lines_for_production(blocks, target_duration=20)

    assert len(compacted) == 2
    assert compacted[0].header_line.startswith("1-1")
    assert compacted[1].header_line.startswith("1-2")
    assert compacted[0].lines == [
        "雨点击打车窗。林澈打开工具箱。",
        "林澈：北侧第三井。",
        "电子屏闪烁红光。",
    ]
    assert compacted[1].lines == [
        "林澈跳下侧梯。泥浆漫过她的小腿。",
        "小满：姐姐？",
        "林澈：抓紧我。",
    ]


def test_compaction_stays_disabled_without_an_explicit_duration() -> None:
    blocks = LiteralScriptWritingWorkflow._build_scene_blocks(
        ["1-1 车厢 夜 内", "林澈开门。", "她走入雨中。"]
    )

    assert compact_scene_lines_for_production(blocks, target_duration=None) is blocks


def test_short_fixture_compacts_to_production_sized_beats() -> None:
    source = """第1集 最后一班回声列车
1-1 末班车 夜 内
暴雨砸在车窗上。
林澈打开工具箱。
林澈检查制动阀。
林澈：北侧第三座管道井。
1-2 塌方段 夜 外
林澈跳下侧梯。
她踩进泥水。
她按六拍一停凿向井壁。
小满：姐姐？
林澈：抓紧我。
1-3 云桥站 夜 内
列车缓缓刹停。
林澈抱着小满走上站台。
林澈转动腕上的旧金属链。
画面切黑。"""
    blocks = LiteralScriptWritingWorkflow._build_scene_blocks(source.splitlines())

    compacted = compact_scene_lines_for_production(blocks, target_duration=30)

    assert len(compacted) == 3
    assert sum(len(block.lines) for block in compacted) == 6
    assert [block.header_line for block in compacted] == [
        "1-1 末班车 夜 内",
        "1-2 塌方段 夜 外",
        "1-3 云桥站 夜 内",
    ]


def test_ninety_second_ten_scene_script_stays_within_a_practical_beat_range() -> None:
    scene_lines = []
    for scene in range(1, 11):
        scene_lines.extend(
            [
                f"1-{scene} 场景{scene} 夜 内",
                f"角色进入场景{scene}。",
                "角色观察环境。",
                "角色完成关键动作。",
                f"角色：第{scene}个线索已经确认。",
                "角色离开镜头。",
            ]
        )
    blocks = LiteralScriptWritingWorkflow._build_scene_blocks(scene_lines)

    compacted = compact_scene_lines_for_production(blocks, target_duration=90)

    assert len(compacted) == 10
    assert 12 <= sum(len(block.lines) for block in compacted) <= 30
    assert all(block.lines for block in compacted)
