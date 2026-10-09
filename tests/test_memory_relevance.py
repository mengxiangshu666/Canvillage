from types import SimpleNamespace

import pytest

from novelvideo import config
from novelvideo.chat import memory_index, memory_relevance


def test_focus_uses_only_one_server_node():
    from novelvideo.chat.shared_context import build_canvas_observation

    facts = {
        "focus": {"selected_node_ids": ["target"]},
        "nodes": [
            {
                "id": "target",
                "type": "videoNode",
                "model_id": "h3",
                "generation_mode": "imageToVideo",
            },
            {"id": "reference", "type": "imageNode", "model_id": "image-model"},
        ],
    }
    assert memory_relevance.focused_memory_context(facts) == {
        "node_type": "videoNode",
        "model_id": "h3",
        "modality": "video",
        "generation_mode": "imageToVideo",
    }
    facts["focus"]["selected_node_ids"].append("reference")
    assert memory_relevance.focused_memory_context(facts) == {}
    observation = build_canvas_observation(
        [
            {
                "id": "target",
                "type": "videoNode",
                "selected": True,
                "data": {"model": "h3", "generationMode": "imageToVideo"},
            }
        ],
        [],
        project_id="p",
        canvas_id="c",
    )
    assert (
        memory_relevance.focused_memory_context(observation)["generation_mode"]
        == "imageToVideo"
    )


@pytest.mark.asyncio
async def test_judge_failure_keeps_preference_and_rejects_recipe():
    class BrokenAgent:
        async def run(self, _prompt):
            raise RuntimeError("offline")

    records = [
        SimpleNamespace(
            id=1,
            kind="preference",
            applies_when='{"interaction":"all"}',
            metadata_json="{}",
            content="回复中文",
        ),
        SimpleNamespace(
            id=2,
            kind="learned_rule",
            applies_when="{}",
            metadata_json="{}",
            content="打戏保持动作因果",
        ),
    ]
    assert await memory_relevance.select_applicable_memories(
        "做产品广告",
        records,
        {},
        agent=BrokenAgent(),
    ) == {1}


@pytest.mark.asyncio
async def test_judge_cannot_invent_memory_ids():
    class Agent:
        async def run(self, prompt):
            assert "产品广告" in prompt
            return SimpleNamespace(output={"applicable_ids": [2, 999]})

    record = SimpleNamespace(
        id=2,
        kind="learned_rule",
        applies_when="{}",
        metadata_json="{}",
        content="产品展示",
    )
    assert await memory_relevance.select_applicable_memories(
        "做产品广告",
        [record],
        {},
        agent=Agent(),
    ) == {2}


@pytest.mark.asyncio
async def test_packet_filters_before_injection_and_receipts(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(memory_index, "schedule_memory_embedding", lambda *_: False)
    monkeypatch.setattr(memory_index, "resolve_direct_model", lambda *_: None)
    monkeypatch.setattr(memory_index, "sync_profile_sources", lambda *_: None)

    def save(content, conditions):
        return memory_index.upsert_memory(
            "local",
            scope_kind="user",
            project=None,
            kind="learned_rule",
            source="test",
            content=content,
            status="confirmed",
            applies_when=conditions,
            metadata={
                "memory_schema": "xiaoshu.memory.v3",
                "rule_type": "general_rule",
            },
        )

    combat = save("视频提示词必须保持动作因果", {"task_family": "short_action_video"})
    wrong_model = save("视频提示词只能使用H3", {"model_id": "h3"})
    product = save("产品广告视频提示词必须突出产品细节", {})

    async def judge(query, records, context):
        assert wrong_model not in {record.id for record in records}
        assert combat in {record.id for record in records}
        return {product}

    monkeypatch.setattr(memory_relevance, "select_applicable_memories", judge)
    packet = await memory_index.build_knowledge_packet(
        "local",
        "project-a",
        "视频提示词 产品广告",
        semantic_applicability=True,
    )
    assert packet["memory_ids"] == [product]
    assert combat not in packet["used_memory_ids"]
    assert "动作因果" not in packet["context"] + packet["execution_context"]
    conn = memory_index._connect("local")
    try:
        assert {
            row[0]
            for row in conn.execute("SELECT memory_id FROM memory_influence_receipts")
        } == {product}
    finally:
        conn.close()
