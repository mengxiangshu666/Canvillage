from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from pydantic_ai.output import PromptedOutput

from novelvideo.chat import growth_distiller


def _candidate_payload() -> dict:
    return {
        "decision": "candidate",
        "feedback_kind": "positive",
        "reason": "用户明确采用了本次短时长打戏提示词框架。",
        "project_facts": ["当前样例发生在雨夜古刹"],
        "candidate": {
            "title": "短时长电影化近身打戏",
            "task_family": "short_action_video",
            "summary": "用时间轴、动作因果链和连续性约束组织短时长打戏。",
            "reusable_principles": ["每个动作必须导致下一步身体状态变化"],
            "trigger_conditions": ["3-12 秒双角色近身冲突视频"],
            "prompt_structure": ["时长与叙事总纲", "逐秒动作链", "机位和动力学", "负面规约"],
            "slots": [
                {
                    "name": "character_a",
                    "purpose": "进攻或防守角色 A",
                    "required": True,
                }
            ],
            "execution_actions": ["先建立角色与参考资产显式映射"],
            "avoid": ["把雨夜古刹等样例内容固化到其他项目"],
            "validation_checks": [
                {
                    "check_id": "timeline.coverage",
                    "condition": "提示词声明了总时长",
                    "pass_when": "时序事件覆盖完整时长且没有空白段",
                }
            ],
            "transferable_elements": ["时间轴", "动力学反馈", "一致性约束"],
            "project_specific_elements": ["雨夜古刹", "黑袍刀客"],
            "confidence": 0.91,
            "evidence_basis": ["用户明确表示本次结果不错并要求记住框架"],
        },
    }


def test_candidate_contract_rejects_unverifiable_recipe():
    payload = _candidate_payload()
    payload["candidate"]["validation_checks"] = []

    with pytest.raises(ValidationError, match="validation_checks"):
        growth_distiller.GrowthDistillationResult.model_validate(payload)


def test_growth_distiller_role_uses_dedicated_text_model_without_new_channel(monkeypatch):
    captured: dict[str, object] = {}

    def fake_model(model_env, default_model, **kwargs):
        captured["model_call"] = (model_env, default_model, kwargs)
        return "growth-model"

    def fake_settings(thinking_env, default_level):
        captured["settings_call"] = (thinking_env, default_level)
        return {"openai_reasoning_effort": "high"}

    class FakeAgent:
        def __init__(self, model, **kwargs):
            captured["model"] = model
            captured["agent_kwargs"] = kwargs

    monkeypatch.setenv("GROWTH_DISTILLER_MODEL", "direct/growth-text")
    monkeypatch.setattr(
        "novelvideo.config.get_newapi_text_pydantic_model",
        fake_model,
    )
    monkeypatch.setattr(
        "novelvideo.config.get_newapi_text_pydantic_model_settings",
        fake_settings,
    )
    monkeypatch.setattr(growth_distiller, "Agent", FakeAgent)

    growth_distiller.create_growth_distiller_agent()

    assert captured["model_call"] == (
        "GROWTH_DISTILLER_MODEL",
        "",
        {
            "model_name_override": "direct/growth-text",
            "timeout_seconds_override": 180.0,
        },
    )
    assert captured["settings_call"] == ("GROWTH_DISTILLER_THINKING_LEVEL", "high")
    assert captured["model"] == "growth-model"
    kwargs = captured["agent_kwargs"]
    assert kwargs["name"] == "成长记忆蒸馏师"
    assert kwargs["model_settings"] == {"openai_reasoning_effort": "high"}
    assert isinstance(kwargs["output_type"], PromptedOutput)
    assert kwargs["output_type"].outputs is growth_distiller.GrowthDistillationResult

    contract = growth_distiller.growth_distiller_contract()
    assert contract["transport"] == "existing_text_model_registry"
    assert contract["credentialSource"] == "existing_model_gateway"
    assert contract["sideEffects"] == ["returns_candidate_only"]


def test_growth_distiller_uses_default_text_model_when_override_is_missing(monkeypatch):
    monkeypatch.delenv("GROWTH_DISTILLER_MODEL", raising=False)
    monkeypatch.delenv("GROWTH_DISTILLER_AUTO_TEXT_MODEL", raising=False)

    class DefaultTextModel:
        catalog_id = "direct/text-default"

    monkeypatch.setattr(
        "novelvideo.generators.direct_models.resolve_direct_model",
        lambda kind: DefaultTextModel() if kind == "text" else None,
    )

    assert growth_distiller.growth_distiller_model_ref() == "direct/text-default"
    contract = growth_distiller.growth_distiller_contract()
    assert contract["modelRef"] == "direct/text-default"
    assert contract["modelSource"] == "default_text_model"


def test_growth_distiller_auto_text_fallback_can_be_disabled(monkeypatch):
    monkeypatch.delenv("GROWTH_DISTILLER_MODEL", raising=False)
    monkeypatch.setenv("GROWTH_DISTILLER_AUTO_TEXT_MODEL", "0")
    monkeypatch.setattr(
        "novelvideo.generators.direct_models.resolve_direct_model",
        lambda _kind: SimpleNamespace(catalog_id="direct/text-default"),
    )

    assert growth_distiller.growth_distiller_model_ref() == ""


def test_distillation_task_keeps_recipe_and_project_details_separate():
    payload = growth_distiller.GrowthDistillationResult.model_validate(_candidate_payload())
    captured: dict[str, str] = {}

    class FakeAgent:
        async def run(self, prompt):
            captured["prompt"] = prompt
            return SimpleNamespace(output=payload)

    result = asyncio.run(
        growth_distiller.distill_growth_memory(
            {
                "raw_user_prompt": "5 秒雨夜古刹双人打戏，逐秒写动作链和负面规约。",
                "assistant_output": "已按时序、机位、动力学和一致性约束生成。",
                "user_feedback": "这次不错，记住这个框架。",
                "execution_result": "用户确认采用。",
                "project_context": "黑袍刀客和白面锦衣刺客。",
            },
            agent=FakeAgent(),
        )
    )

    assert result.decision == "candidate"
    assert result.candidate is not None
    assert "时间轴" in result.candidate.transferable_elements
    assert "雨夜古刹" in result.candidate.project_specific_elements
    assert "project_specific_elements" in captured["prompt"]
    assert "validation_checks" in captured["prompt"]


def test_distillation_prompt_preserves_preceding_episode_for_feedback_teaching():
    request = growth_distiller.GrowthDistillationRequest.model_validate(
        {
            "user_feedback": "这版不错，记住这个框架",
            "preceding_episode": {
                "episode_id": 7,
                "turn_id": "turn-previous",
                "objective": "5 秒雨夜古刹双人打戏",
                "response_summary": "按时序、镜头、动力学和负面规约编排",
                "task_stage": "media_generation",
                "outcome": "verified_success",
                "verified": True,
            },
        }
    )

    prompt = growth_distiller.build_growth_distillation_prompt(request)

    assert '"preceding_episode"' in prompt
    assert "5 秒雨夜古刹双人打戏" in prompt
    assert "按时序、镜头、动力学和负面规约编排" in prompt


def test_distillation_failure_is_non_destructive_review_state():
    class FailingAgent:
        async def run(self, _prompt):
            raise RuntimeError("fixture provider down")

    result = asyncio.run(
        growth_distiller.distill_growth_memory(
            {"user_feedback": "以后所有短打都采用这一套结构。"},
            agent=FailingAgent(),
        )
    )

    assert result.decision == "needs_review"
    assert result.candidate is None
    assert result.reason == "growth_distiller_failed:RuntimeError"


def test_empty_teaching_episode_is_noop_without_model_call():
    result = asyncio.run(growth_distiller.distill_growth_memory({}))
    assert result.decision == "noop"
    assert result.reason == "empty_teaching_episode"
