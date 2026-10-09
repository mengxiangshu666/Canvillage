"""Run the no-media growth-memory loop against the portable Python runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args()
    state_root = Path(args.state_dir).resolve()
    shutil.rmtree(state_root, ignore_errors=True)
    os.environ["NOVELVIDEO_STATE_DIR"] = str(state_root)
    os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")
    root = Path(__file__).resolve().parents[1]
    runtime_env = (root / "runtime" / "env").resolve()
    source_root = (root / "src").resolve()
    if not (runtime_env / "novelvideo" / "chat" / "memory_index.py").is_file():
        raise RuntimeError(f"runtime package is missing: {runtime_env}")
    # This is a deployment proof, so source must never shadow the portable runtime.
    sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != source_root]
    sys.path.insert(0, str(runtime_env))

    from novelvideo.chat import growth_distiller, memory_index
    from novelvideo.workflow_runtime.learning_bridge import (
        record_workflow_verification_learning,
    )

    memory_index.schedule_memory_embedding = lambda *_: False
    rule_id = memory_index.upsert_memory(
        "smoke",
        scope_kind="user",
        project=None,
        kind="learned_rule",
        source="smoke",
        content="执行后核对真实回执。",
        status="confirmed",
    )
    first = memory_index.record_execution_episode(
        "smoke",
        project="smoke-project-a",
        conversation_id="smoke-conversation",
        canvas_id="smoke-canvas",
        turn_id="smoke-turn-a",
        objective="新增一个文字节点并核对真实回执",
        response_summary="已写入节点，revision=2。",
        verified=True,
        outcome="verified_success",
        memory_ids=[rule_id],
        evidence_ref="command:smoke",
    )
    feedback = memory_index.apply_execution_feedback(
        "smoke",
        project="smoke-project-a",
        conversation_id="smoke-conversation",
        canvas_id="smoke-canvas",
        feedback_turn_id="smoke-turn-b",
        feedback_text="这个很好，保留这个",
    )
    late = asyncio.run(
        record_workflow_verification_learning(
            run={
                "id": "smoke-run",
                "project_id": "smoke-project-a",
                "source_turn_id": "smoke-turn-a",
                "project_context": {"requester_username": "smoke"},
            },
            event_id="smoke-event",
            event_type="verification_passed",
            step_id="verify",
            payload={"canvas_revision": 2},
            error="",
        )
    )
    rule = memory_index.get_memory("smoke", rule_id)

    class _StructuredGrowthAgent:
        async def run(self, _prompt: str):
            return type(
                "Response",
                (),
                {
                    "output": growth_distiller.GrowthDistillationResult.model_validate(
                        {
                            "decision": "candidate",
                            "feedback_kind": "positive",
                            "reason": "用户确认方法有效，提炼为可迁移候选。",
                            "project_facts": ["origin 项目中的具体资产"],
                            "candidate": {
                                "title": "连续动作镜头配方",
                                "task_family": "short_action_video",
                                "summary": "用时序动作因果、运镜约束和终态检查组织短视频提示词。",
                                "reusable_principles": ["每个动作都要改变下一步身体状态"],
                                "trigger_conditions": ["需要短时长连续动作镜头"],
                                "prompt_structure": ["总纲", "时间轴", "运镜", "动力学", "验收"],
                                "slots": [
                                    {
                                        "name": "characters",
                                        "purpose": "可替换角色与参考资产",
                                        "required": True,
                                    }
                                ],
                                "execution_actions": ["先固定角色和镜头约束，再生成动作链"],
                                "avoid": ["不要把项目专属姓名写死"],
                                "validation_checks": [
                                    {
                                        "check_id": "timeline.complete",
                                        "condition": "时间轴覆盖完整时长",
                                        "pass_when": "每段动作存在明确起止和下一状态",
                                    }
                                ],
                                "transferable_elements": ["时序", "动作因果", "终态验收"],
                                "project_specific_elements": ["origin 项目中的具体资产"],
                                "confidence": 0.9,
                                "evidence_basis": ["用户明确采用并有执行回执"],
                            },
                        }
                    ),
                },
            )()

    distilled = asyncio.run(
        growth_distiller.distill_growth_memory(
            {
                "raw_user_prompt": "做一个 5 秒连续动作镜头",
                "assistant_output": "已按时间轴、运镜和动力学完成",
                "user_feedback": "这版很好，记住这个框架",
                "execution_result": "WorkflowRun verifier=passed",
                "project_context": "origin 项目中的具体资产",
                "task_family_hint": "short_action_video",
            },
            agent=_StructuredGrowthAgent(),
        )
    )
    candidate = memory_index.save_growth_distillation_candidate(
        "smoke",
        project="origin-project",
        turn_id="origin-turn",
        result=distilled,
        event_id=101,
        conversation_id="origin-conversation",
    )
    assert candidate is not None
    assert "记住这个框架" not in candidate.content

    first_candidate = memory_index.record_memory_evidence(
        "smoke",
        candidate.id,
        outcome="positive",
        evidence_ref="workflow:origin-run:origin-event:verify:passed",
        project="origin-project",
        task_id="origin-turn",
    )
    assert first_candidate is not None
    assert json.loads(first_candidate.metadata_json)["candidate_recall"] is True
    cross_project_recall = asyncio.run(
        memory_index.recall_memories(
            "smoke",
            "new-project",
            "短时长连续动作镜头 时间轴 运镜 动作因果",
            task_stage="media_generation",
            conversation_id="new-conversation",
        )
    )
    assert any(item.id == candidate.id for item in cross_project_recall)

    # A second independent verified task promotes the candidate to a user rule.
    promoted = memory_index.record_memory_evidence(
        "smoke",
        candidate.id,
        outcome="positive",
        evidence_ref="workflow:new-run:new-event:verify:passed",
        project="new-project",
        task_id="new-turn",
    )
    assert promoted is not None
    assert promoted.status == "confirmed"
    assert promoted.kind == "learned_rule"
    assert promoted.scope_kind == "user"

    # A separate candidate demonstrates negative verifier evidence withdrawing
    # trial recall before promotion, without mutating the promoted rule above.
    revoked_result = distilled.model_copy(
        deep=True,
        update={
            "candidate": distilled.candidate.model_copy(
                update={
                    "title": "待撤回连续动作配方",
                    "summary": "用于验证负向证据撤回候选召回资格。",
                }
            )
        },
    )
    revoked = memory_index.save_growth_distillation_candidate(
        "smoke",
        project="origin-project",
        turn_id="origin-turn-revoked",
        result=revoked_result,
        event_id=102,
        conversation_id="origin-conversation",
    )
    assert revoked is not None
    memory_index.record_memory_evidence(
        "smoke",
        revoked.id,
        outcome="positive",
        evidence_ref="workflow:origin-run-2:origin-event:verify:passed",
        project="origin-project",
        task_id="origin-turn-revoked",
    )
    negative = memory_index.record_memory_evidence(
        "smoke",
        revoked.id,
        outcome="negative",
        evidence_ref="workflow:new-run-2:new-event:verify:failed",
        project="new-project",
        task_id="new-turn-2",
    )
    assert negative is not None
    assert json.loads(negative.metadata_json)["candidate_recall"] is False
    revoked_recall = asyncio.run(
        memory_index.recall_memories(
            "smoke",
            "third-project",
            "短时长连续动作镜头 时间轴 运镜 动作因果",
            task_stage="media_generation",
            conversation_id="third-conversation",
        )
    )
    assert all(item.id != revoked.id for item in revoked_recall)

    result = {
        "runtime_memory_index": str(Path(memory_index.__file__).resolve()),
        "first": first,
        "feedback": feedback,
        "late_verifier": late,
        "rule": {"positive": rule.positive_count, "negative": rule.negative_count},
        "growth": {
            "candidate_id": candidate.id,
            "cross_project_recall": any(item.id == candidate.id for item in cross_project_recall),
            "promoted_id": promoted.id,
            "promoted_status": promoted.status,
            "revoked_candidate_id": revoked.id,
            "revoked_recalled": any(item.id == revoked.id for item in revoked_recall),
        },
        "stats": memory_index.memory_stats("smoke"),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    expected_runtime_index = runtime_env / "novelvideo" / "chat" / "memory_index.py"
    if Path(memory_index.__file__).resolve() != expected_runtime_index:
        raise AssertionError(
            f"runtime smoke imported {memory_index.__file__!s}, expected {expected_runtime_index!s}"
        )
    stats = result["stats"]
    assert first["status"] == "recorded"
    assert feedback["status"] == "recorded"
    assert late["status"] == "recorded"
    assert stats["episode_count"] == 1
    assert stats["feedback_count"] == 1
    assert stats["positive_feedback"] == 1
    assert stats["workflow_evidence"] == 5
    assert result["rule"]["positive"] == 2
    shutil.rmtree(state_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
