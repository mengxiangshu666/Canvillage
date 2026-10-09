from pathlib import Path

from novelvideo.chat.tool_allowlist import MAX_DYNAMIC_CAPABILITIES


SKILL_PATH = (
    Path(__file__).resolve().parents[1]
    / "agent_skills"
    / "village-canvas"
    / "SKILL.md"
)


def test_village_canvas_skill_contract_matches_indexed_runtime_contracts():
    text = SKILL_PATH.read_text(encoding="utf-8")

    assert (
        f"`context.tool_allowlist` | `context.tool_allowlist` | 根据 shadow plan 注入最多 "
        f"{MAX_DYNAMIC_CAPABILITIES} 个本轮能力"
    ) in text
    assert (
        "`creative.optimize_prompt` | `creative.optimize_prompt` | "
        "调用 PromptOptimizer 编译模型专属提示词"
    ) in text
    assert "`task_type=freezone_prompt_optimize`" in text
    assert "轮询/回读使用 `task.get`" in text
