from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic_ai.output import PromptedOutput

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.freezone import prompt_optimizer
from novelvideo.freezone.prompt_optimizer import (
    PromptOptimizerLLMOutput,
    build_prompt_optimization_task,
    compile_prompt_strategy_contract,
    optimize_freezone_prompt,
    resolve_prompt_knowledge_profiles,
)


def _assert_bundled_sources(sources: list[str]) -> None:
    project_root = prompt_optimizer._PROJECT_ROOT.resolve()
    for source in sources:
        path = Path(source).resolve()
        assert path.is_relative_to(project_root)
        assert path.is_file()


def test_optimizer_payload_carries_open_directing_rules_and_identity_guards():
    task, _, _ = build_prompt_optimization_task(
        text="人物倾听对白，克制表演；组合运镜后跳时空回放。",
        node_type="video", target_model_id="seedance-2.0",
        target_api_model="seedance-2.0", target_model_label="Seedance 2.0",
        params={"creationStage": "storyboard", "shot_count": 4, "duration": 10},
        references=[{"name": "@Image1", "role": "identity"}],
        director_vision={"continuity_locks": {"characters": ["hero"]}},
        guidance="保留倾听时的停顿，摄影机绕行后停住",
    )
    for fact in (
        "不强制景别数量、比例或全景开场",
        "允许固定观察、组合运镜", "第 8 段按本镜观看目的选择焦段",
        "不强制半秒句间停顿", "不要求每镜填齐所有细节",
        "换场、跳时、回放或平行叙事", "角色卡逐字复制",
        "保留逐字台词", "保持干净成像与资产身份",
        "动作按发力、接触、受力、重心变化、结果反馈表达，保留真实时间顺序。",
        "无意重复上一镜、未说明的支撑或道具归属跳变",
    ):
        assert fact in task
    assert task.rindex("保留倾听时的停顿，摄影机绕行后停住") > task.index("</source_prompt>")
    assert '"characters": ["hero"]' in task
    assert "0–5 / 5–10 / 10–15s" not in task
    assert "prefer 4–5 total" not in task
    for old in (
        "每个镜头只写一个可观察的主要动作", "每片 1–2 秒快速剪接",
        "贴身景别不超过全片一半", "多句之间至少留 0.5 秒反应停顿",
        "任何镜头都不重新定义焦段、光圈和调色",
    ):
        assert old not in task


def test_live_knowledge_retrieval_reads_and_ranks_selected_source(tmp_path: Path) -> None:
    source = tmp_path / "seedance-notes.md"
    source.write_text(
        "# 无关说明\n这是一段与测试无关的普通介绍，长度足够但没有目标词。\n\n"
        "# Seedance 首帧规则\nSeedance 图生视频把首帧视为 t=0，提示词应描述之后的动作、镜头和时间段，并明确参考素材角色。",
        encoding="utf-8",
    )
    profile = prompt_optimizer.PromptKnowledgeProfile(
        profile_id="seedance_2_prompt_profile_v1",
        label="test",
        rules="test",
        sources=(str(source),),
    )
    excerpts = prompt_optimizer.retrieve_live_knowledge_excerpts([profile])
    assert excerpts
    assert excerpts[0]["source"] == str(source)
    assert "首帧视为 t=0" in excerpts[0]["excerpt"]


def test_seedance_profile_retrieves_local_knowledge_and_first_frame_rules() -> None:
    profiles = resolve_prompt_knowledge_profiles(
        node_type="video",
        target_model_id="seedance-2.0",
        target_api_model="doubao-seedance-2-0",
        params={"mode": "first_frame", "duration": 10, "camera_movement": "slow_push"},
    )
    ids = [item.profile_id for item in profiles]
    assert ids == [
        "aigc_prompt_core_v1",
        "seedance_2_prompt_profile_v2_official_lark",
        "first_last_frame_bridge_v1",
        "camera_motion_decision_v1",
        "aigc_failure_diagnosis_v1",
    ]
    seedance_profile = profiles[1]
    assert seedance_profile.authority == "official-derived"
    assert "after t=0" in seedance_profile.rules
    sources = [source for item in profiles for source in item.sources]
    assert any("03_视频大模型与提示词_AI版.md" in source for source in sources)
    assert any("Seedance-2.0-Skill-OS.md" in source for source in sources)
    _assert_bundled_sources(sources)


def test_image_to_video_mode_retrieves_first_frame_continuity_rules() -> None:
    profiles = resolve_prompt_knowledge_profiles(
        node_type="video",
        target_model_id="seedance-2.0",
        params={"mode": "imageToVideo"},
    )
    ids = [item.profile_id for item in profiles]
    assert "seedance_2_prompt_profile_v2_official_lark" in ids
    assert "first_last_frame_bridge_v1" in ids


def test_prompt_strategy_contract_compiles_seedance_narrative_budget() -> None:
    contract = compile_prompt_strategy_contract(
        node_type="video",
        target_model_id="seedance-2.0",
        params={"mode": "textToVideo", "duration": 10},
    )

    assert contract.model_dump(by_alias=True)["schema"] == "prompt_strategy_contract.v1"
    assert contract.workflow == "text_to_video"
    assert contract.strategy == "narrative"
    assert contract.max_temporal_beats is None
    assert contract.max_camera_moves is None


def test_prompt_strategy_contract_compiles_i2v_as_t0_control() -> None:
    contract = compile_prompt_strategy_contract(
        node_type="video",
        target_model_id="seedance-2.0",
        params={"mode": "imageToVideo", "duration": 5},
        references=[{"name": "@Image1", "kind": "image", "role": "first_frame"}],
    )

    assert contract.workflow == "image_to_video"
    assert contract.strategy == "control"
    assert contract.reference_semantics == "t0_facts_only"
    assert contract.max_camera_moves is None


def test_prompt_strategy_contract_requires_two_anchors_for_first_last_frame() -> None:
    contract = compile_prompt_strategy_contract(
        node_type="video",
        target_model_id="seedance-2.0",
        params={"mode": "first_last_frame", "duration_seconds": 8},
        references=[{"name": "@Image1", "kind": "image", "role": "first_frame"}],
    )

    assert contract.workflow == "first_last_frame"
    assert contract.reference_semantics == "bridge_only"
    assert any("缺少两张图片参考" in warning for warning in contract.warnings)


def test_prompt_strategy_contract_uses_positive_constraints_for_runway() -> None:
    contract = compile_prompt_strategy_contract(
        node_type="video",
        target_model_id="runway-gen4",
        params={"mode": "textToVideo", "duration": 6},
    )

    assert contract.strategy == "control"
    assert contract.negative_prompt_policy == "positive_only"


def test_prompt_strategy_contract_defaults_unknown_video_to_control_with_warning() -> None:
    contract = compile_prompt_strategy_contract(
        node_type="video",
        target_model_id="unknown-video-model",
        params={},
    )

    assert contract.strategy == "control"
    assert contract.duration_seconds is None
    assert any("明确时长" in warning for warning in contract.warnings)


def test_minimax_h3_uses_short_clip_director_contract() -> None:
    profiles = resolve_prompt_knowledge_profiles(
        node_type="video",
        target_model_id="direct_video-3157c550f1188060",
        target_api_model="minimax_h3",
        target_model_label="minimax_h3",
        params={"mode": "textToVideo", "duration": 6, "camera_movement": "tracking"},
    )
    ids = [item.profile_id for item in profiles]
    assert ids[1] == "minimax_h3_prompt_profile_v1"
    assert "minimax_h3_prompt_profile_v1" in ids
    assert "short-clip" in profiles[1].rules
    task, _, _ = build_prompt_optimization_task(
        text="雨夜檐廊，刀客格挡刺客后形成对峙",
        node_type="video",
        target_model_id="direct_video-3157c550f1188060",
        target_api_model="minimax_h3",
        target_model_label="minimax_h3",
        params={"mode": "textToVideo", "duration": 6},
        references=[],
    )
    assert "MODEL-SPECIFIC VIDEO COMPILATION CONTRACT (MiniMax H3)" in task
    assert "do not impose a fixed beat count" in task
    assert "at most three temporal beats" not in task
    assert "For <=8 seconds use at most 3 beats" not in task


@pytest.mark.asyncio
async def test_minimax_h3_local_fallback_keeps_causal_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prompt_optimizer, "resolve_optimizer_text_model_chain", lambda **_kwargs: [])
    result = await optimize_freezone_prompt(
        text="雨夜古刹，刺客突袭，刀客格挡后刺客失衡后退",
        node_type="video",
        target_model_id="direct_video-3157c550f1188060",
        target_api_model="minimax_h3",
        target_model_label="minimax_h3",
        params={"mode": "textToVideo", "duration": 6},
    )
    assert result["target_model_profile"] == "minimax_h3_prompt_profile_v1"
    assert "按本镜内容安排的因果动作过程" in result["optimized_prompt"]
    assert "准备→接触→受力传导" in result["optimized_prompt"]
    assert result["strategy_contract"]["workflow"] == "text_to_video"
    assert result["strategy_contract"]["strategy"] == "control"
    assert result["strategy_contract"]["max_temporal_beats"] is None
    assert "最多" not in result["optimized_prompt"]


@pytest.mark.parametrize(
    ("model_id", "expected_profile"),
    [
        ("seedream-4.0", "seedream_image_profile_v1"),
        ("gpt-image-2", "gpt_image_instruction_profile_v1"),
        ("qwen-image", "qwen_image_zh_instruction_profile_v1"),
        ("gemini-3-pro-image", "gemini_image_conversational_profile_v1"),
        ("flux-1.1-pro", "flux_concrete_visual_profile_v1"),
        ("midjourney-v7", "midjourney_compact_visual_profile_v1"),
    ],
)
def test_image_models_retrieve_distinct_prompt_profiles(
    model_id: str,
    expected_profile: str,
) -> None:
    profiles = resolve_prompt_knowledge_profiles(
        node_type="image",
        target_model_id=model_id,
    )
    assert expected_profile in [item.profile_id for item in profiles]


def test_optimizer_text_model_has_no_hidden_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FREEZONE_PROMPT_OPTIMIZER_MODEL", raising=False)
    monkeypatch.delenv("SEEDANCE2_PROMPT_COMPOSER_MODEL", raising=False)
    env_key, model = prompt_optimizer.resolve_optimizer_text_model(
        target_model_id="seedance-2.0",
    )
    assert env_key == "FREEZONE_PROMPT_OPTIMIZER_MODEL"
    assert model == ""


def test_optimizer_text_model_honors_explicit_seedance_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FREEZONE_PROMPT_OPTIMIZER_MODEL", raising=False)
    monkeypatch.setenv("SEEDANCE2_PROMPT_COMPOSER_MODEL", "my-seedance-composer")
    env_key, model = prompt_optimizer.resolve_optimizer_text_model(
        target_model_id="seedance-2.0",
    )
    assert env_key == "SEEDANCE2_PROMPT_COMPOSER_MODEL"
    assert model == "my-seedance-composer"


def test_optimizer_text_model_honors_explicit_global_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FREEZONE_PROMPT_OPTIMIZER_MODEL", "my-strong-text-model")
    env_key, model = prompt_optimizer.resolve_optimizer_text_model(
        target_model_id="seedance-2.0",
    )
    assert env_key == "FREEZONE_PROMPT_OPTIMIZER_MODEL"
    assert model == "my-strong-text-model"


def test_optimizer_chain_prefers_default_direct_text_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.generators.direct_models import DirectModel

    direct = DirectModel(
        kind="text",
        registry_id="text-primary",
        label="我的文字模型",
        upstream_model="vendor-text",
        base_url="https://example.invalid/v1",
        api_key="test-key",
        enabled=True,
        is_default=True,
    )
    monkeypatch.setattr(
        "novelvideo.generators.direct_models.resolve_direct_model",
        lambda kind, _model=None: direct if kind == "text" else None,
    )

    chain = prompt_optimizer.resolve_optimizer_text_model_chain(
        target_model_id="direct/video-primary",
    )

    assert chain[0] == ("DIRECT_TEXT_MODEL", "direct/text-primary")


def test_optimizer_chain_without_config_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FREEZONE_PROMPT_OPTIMIZER_MODEL", raising=False)
    monkeypatch.delenv("SEEDANCE2_PROMPT_COMPOSER_MODEL", raising=False)
    monkeypatch.delenv("FREEZONE_PROMPT_OPTIMIZER_FALLBACK_MODELS", raising=False)
    monkeypatch.setattr(
        "novelvideo.generators.direct_models.resolve_direct_model",
        lambda _kind, _model=None: None,
    )

    assert (
        prompt_optimizer.resolve_optimizer_text_model_chain(
            target_model_id="seedance-2.0",
        )
        == []
    )


def test_explicit_optimizer_override_precedes_direct_text_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.generators.direct_models import DirectModel

    direct = DirectModel(
        kind="text",
        registry_id="text-primary",
        label="模型中心文字模型",
        upstream_model="vendor-text",
        base_url="https://example.invalid/v1",
        api_key="test-key",
        enabled=True,
        is_default=True,
    )
    monkeypatch.setenv("FREEZONE_PROMPT_OPTIMIZER_MODEL", "explicit-optimizer")
    monkeypatch.setattr(
        "novelvideo.generators.direct_models.resolve_direct_model",
        lambda kind, _model=None: direct if kind == "text" else None,
    )

    chain = prompt_optimizer.resolve_optimizer_text_model_chain(
        target_model_id="direct/video-primary",
    )

    assert chain[0] == ("FREEZONE_PROMPT_OPTIMIZER_MODEL", "explicit-optimizer")
    assert chain[1] == ("DIRECT_TEXT_MODEL", "direct/text-primary")


def test_prompt_research_mode_normalizes_deep_alias() -> None:
    assert prompt_optimizer.normalize_prompt_research_mode("deep") == "expert"
    assert prompt_optimizer.normalize_prompt_research_mode("invalid") == "standard"


@pytest.mark.asyncio
async def test_prompt_research_returns_auditable_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePool:
        def search(self, query: str, **_kwargs):
            return {
                "ok": True,
                "query": query,
                "results": [
                    {
                        "title": "Official model guide",
                        "url": "https://docs.example.invalid/model",
                        "content": "Supported image references and camera controls.",
                        "score": 0.9,
                    }
                ],
                "result_count": 1,
                "cached": True,
            }

    monkeypatch.setattr(
        "novelvideo.chat.tavily_pool.get_tavily_pool",
        lambda: FakePool(),
    )
    result = await prompt_optimizer.retrieve_prompt_research(
        mode="expert",
        trigger_reasons=["用户明确要求最新或官方资料"],
        node_type="video",
        target_model_id="seedance-2.5",
        target_api_model="seedance-2.5",
        target_model_label="Seedance 2.5",
        params={"duration": 8},
        source_text="角色从首帧向前一步",
        project_dir=tmp_path,
    )

    assert result["research_used"] is True
    assert result["research_status"] in {"completed", "degraded"}
    assert result["research_sources"][0]["url"].endswith("/model")
    assert "Official model guide" in result["research_context"]


def test_prompt_optimizer_agent_uses_direct_text_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, model, **kwargs):
            captured["model"] = model
            captured["kwargs"] = kwargs

    monkeypatch.setattr(
        "novelvideo.generators.direct_models.get_direct_pydantic_model",
        lambda kind, model: f"resolved:{kind}:{model}",
    )
    monkeypatch.setattr(prompt_optimizer, "Agent", FakeAgent)

    prompt_optimizer.create_prompt_optimizer_agent(
        "DIRECT_TEXT_MODEL",
        "direct/text-primary",
    )

    assert captured["model"] == "resolved:text:direct/text-primary"


def test_prompt_optimizer_cache_rebuilds_after_direct_model_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.generators.direct_models import DirectModel

    current = DirectModel(
        kind="text",
        registry_id="text-primary",
        label="文字模型",
        upstream_model="vendor-text-v1",
        base_url="https://one.example/v1",
        api_key="first-key",
        enabled=True,
        is_default=True,
    )
    monkeypatch.setattr(
        "novelvideo.generators.direct_models.resolve_direct_model",
        lambda _kind, _model=None: current,
    )
    monkeypatch.setattr(prompt_optimizer, "_optimizer_agents", {})
    created: list[object] = []

    def fake_create(*_args) -> object:
        agent = object()
        created.append(agent)
        return agent

    monkeypatch.setattr(prompt_optimizer, "create_prompt_optimizer_agent", fake_create)

    first = prompt_optimizer.get_prompt_optimizer_agent(
        "DIRECT_TEXT_MODEL", "direct/text-primary"
    )
    current = DirectModel(
        kind="text",
        registry_id="text-primary",
        label="文字模型",
        upstream_model="vendor-text-v2",
        base_url="https://two.example/v1",
        api_key="second-key",
        enabled=True,
        is_default=True,
    )
    second = prompt_optimizer.get_prompt_optimizer_agent(
        "DIRECT_TEXT_MODEL", "direct/text-primary"
    )

    assert first is not second
    assert len(created) == 2


def test_optimizer_uses_prompted_json_instead_of_forced_tool_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, model, **kwargs):
            captured["model"] = model
            captured.update(kwargs)

    monkeypatch.setattr(
        "novelvideo.config.get_newapi_text_pydantic_model",
        lambda *_args, **_kwargs: "configured-model",
    )
    monkeypatch.setattr(prompt_optimizer, "Agent", FakeAgent)

    prompt_optimizer.create_prompt_optimizer_agent(
        "FREEZONE_PROMPT_OPTIMIZER_MODEL",
        "deepseek-v4-flash",
    )

    assert captured["model"] == "configured-model"
    assert isinstance(captured["output_type"], PromptedOutput)


def test_create_optimizer_agent_forces_candidate_model_over_stale_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, model, **kwargs):
            captured["model"] = model
            captured.update(kwargs)

    def fake_model(*args, **kwargs):
        captured["model_args"] = args
        captured["model_kwargs"] = kwargs
        return "configured-model"

    monkeypatch.setenv("FREEZONE_PROMPT_OPTIMIZER_MODEL", "DC-deepseek-v4-pro-LLM")
    monkeypatch.setattr("novelvideo.config.get_newapi_text_pydantic_model", fake_model)
    monkeypatch.setattr(prompt_optimizer, "Agent", FakeAgent)

    prompt_optimizer.create_prompt_optimizer_agent(
        "FREEZONE_PROMPT_OPTIMIZER_MODEL",
        "deepseek-v4-flash",
    )

    assert captured["model_kwargs"] == {"model_name_override": "deepseek-v4-flash"}


def test_image_profile_does_not_pretend_to_be_seedance() -> None:
    task, profiles, live_excerpts = build_prompt_optimization_task(
        text="一个女孩在雨夜街头，要电影感",
        node_type="image",
        target_model_id="gpt-image-2",
        target_api_model="gpt-image-2",
        target_model_label="GPT Image 2",
        params={"aspect_ratio": "9:16", "size": "2K"},
        references=[{"name": "@Image1", "role": "identity"}],
    )
    ids = [item.profile_id for item in profiles]
    assert "image_prompt_optimizer_v1" in ids
    assert "gpt_image_instruction_profile_v1" in ids
    assert "seedance_2_prompt_profile_v1" not in ids
    assert "AVAILABLE REFERENCES" in task
    assert "gpt-image-2" in task
    assert "9:16" in task


def test_prompt_optimizer_injects_bounded_director_recipe_for_image_node() -> None:
    task, _profiles, _live_excerpts = build_prompt_optimization_task(
        text="参考图中的角色保持身份，只更换为雨夜街景",
        node_type="image",
        target_model_id="gpt-image-2",
        target_api_model="gpt-image-2",
        target_model_label="GPT Image 2",
        params={"creationStage": "prompt", "aspect_ratio": "16:9"},
        references=[{"name": "图片1", "role": "identity"}],
    )

    assert "DIRECTOR RESEARCH RECIPES" in task
    assert "director.image.reference_preservation.v1" in task
    assert "director.first_frame_forward_chain.v1" not in task
    assert "TARGET MODEL CAPABILITY EVIDENCE" in task
    assert "REFERENCE MANIFEST" in task
    assert "DIRECTOR RECIPE RECEIPT" in task


def test_gpt_image_target_injects_upstream_case_references() -> None:
    task, profiles, _live_excerpts = build_prompt_optimization_task(
        text="古风角色海报，突出人物与标题",
        node_type="image",
        target_model_id="gpt-image-2",
        target_api_model="gpt-image-2",
        target_model_label="GPT Image 2",
        params={"aspect_ratio": "9:16"},
        references=[],
    )

    assert "UPSTREAM GPT-IMAGE-2 CASE REFERENCES" in task
    assert "untrusted visual references" in task
    assert any("gpt_image_instruction_profile_v1" == item.profile_id for item in profiles)
    assert "Case #" in task


def test_non_gpt_image_target_does_not_receive_external_case_block() -> None:
    task, _profiles, _live_excerpts = build_prompt_optimization_task(
        text="一只橘猫的角色设定图",
        node_type="image",
        target_model_id="flux-1",
        target_api_model="flux-1",
        target_model_label="FLUX",
        params={},
        references=[],
    )

    # The shared task schema keeps the labeled block, but the non-matching
    # branch must remain empty and never include a retrieved case.
    assert "No matching upstream case was retrieved" in task
    assert "Case #" not in task


@pytest.mark.asyncio
async def test_optimizer_calls_configured_text_agent_and_returns_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FREEZONE_PROMPT_OPTIMIZER_MODEL", "configured-optimizer")
    monkeypatch.delenv("SEEDANCE2_PROMPT_COMPOSER_MODEL", raising=False)
    captured: dict[str, str] = {}

    class FakeAgent:
        async def run(self, task: str):
            captured["task"] = task

            class Response:
                output = PromptOptimizerLLMOutput(
                    optimized_prompt="@Image1 作为首帧。0-5秒，35mm固定中景，角色抬眼后向前一步。",
                    preserved_intent="角色从首帧开始向前行动。",
                    changes=["补充时间段和可执行动作"],
                    applied_rules=["首帧是 t=0", "一个时间段一个主动作"],
                    warnings=[],
                    output_language="zh",
                )

            return Response()

    monkeypatch.setattr(
        prompt_optimizer,
        "get_prompt_optimizer_agent",
        lambda *_args: FakeAgent(),
    )
    result = await optimize_freezone_prompt(
        text="让她动起来，电影感",
        node_type="video",
        target_model_id="seedance-2.0",
        target_api_model="seedance-2.0",
        target_model_label="Seedance 2.0",
        params={"mode": "first_frame", "duration": 5, "aspect_ratio": "9:16"},
        references=[{"name": "@Image1", "role": "first_frame"}],
        director_vision={
            "schema": "director_vision.v1",
            "vision_revision": "test-revision",
            "visual_motifs": ["雨幕"],
            "continuity_locks": {"characters": ["hero"]},
        },
    )
    assert "CURATED RETRIEVAL PROFILES" in captured["task"]
    assert "LIVE RETRIEVED EXCERPTS" in captured["task"]
    assert "FILMCRAFT ATOMIC RULE INJECTION" in captured["task"]
    assert "craft.single_primary_action.v1" in captured["task"]
    assert "PROMPT STRATEGY CONTRACT" in captured["task"]
    assert '"reference_semantics": "t0_facts_only"' in captured["task"]
    assert "@Image1" in captured["task"]
    assert result["optimizer_model"] == "configured-optimizer"
    assert result["target_model_profile"] == "seedance_2_prompt_profile_v2_official_lark"
    assert "seedance_2_prompt_profile_v2_official_lark" in result["knowledge_profiles"]
    assert any(
        "03_视频大模型与提示词_AI版.md" in source
        for source in result["knowledge_sources"]
    )
    assert any(
        "Seedance-2.0-Skill-OS.md" in source for source in result["knowledge_sources"]
    )
    assert "craft.single_primary_action.v1" in result["craft_rule_ids"]
    assert "craft.screen_direction_axis.v1" in result["craft_rule_ids"]
    assert "craft.sound_layers.v1" in result["craft_rule_ids"]
    assert "craft.positive_constraint_rewrite.v1" in result["craft_rule_ids"]
    assert result["strategy_contract"]["schema"] == "prompt_strategy_contract.v1"
    assert result["strategy_contract"]["workflow"] == "image_to_video"
    assert result["strategy_contract"]["duration_seconds"] == 5.0


@pytest.mark.asyncio
async def test_optimizer_runs_reference_vision_before_text_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    image_path = project_dir / "freezone" / "_uploads" / "face.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"fake-png-bytes")
    prompt_optimizer._reference_vision_cache.clear()
    captured: dict[str, str] = {}
    monkeypatch.setenv("FREEZONE_PROMPT_OPTIMIZER_MODEL", "configured-optimizer")
    monkeypatch.setenv("FREEZONE_VISION_MODEL", "configured-vision-model")

    async def fake_vision_model(*, prompt, images, timeout_seconds=0, **kwargs):
        del images, timeout_seconds, kwargs
        captured["vision_prompt"] = prompt
        return "mock-vision", "主体是一只正面橘猫，脸部位于画面中央偏上，圆眼，室内暖光。"

    class FakeAgent:
        async def run(self, task: str):
            captured["task"] = task

            class Response:
                output = PromptOptimizerLLMOutput(
                    optimized_prompt="@图片1 保持橘猫身份和中央脸部位置，慢慢眨眼。",
                    preserved_intent="橘猫眨眼",
                    changes=["使用视觉识别结果锁定主体"],
                    applied_rules=["参考图视觉识别"],
                    warnings=[],
                    output_language="zh",
                )

            return Response()

    monkeypatch.setattr(
        "novelvideo.freezone.vision_gateway.call_freezone_vision_model",
        fake_vision_model,
    )
    monkeypatch.setattr(
        prompt_optimizer,
        "get_prompt_optimizer_agent",
        lambda *_args: FakeAgent(),
    )

    result = await optimize_freezone_prompt(
        text="让它眨眼，别丢脸",
        node_type="video",
        target_model_id="kling-v3",
        params={"mode": "imageToVideo"},
        references=[
            {
                "name": "@图片1",
                "kind": "image",
                "role": "first_frame",
                "url": "/static/user/project/freezone/_uploads/face.png",
            }
        ],
        project_dir=project_dir,
    )

    assert "参考图识别器" in captured["vision_prompt"]
    assert "visual_summary" in captured["task"]
    assert "主体是一只正面橘猫" in captured["task"]
    assert result["reference_vision_used"] == [{"name": "@图片1", "model": "mock-vision"}]
    assert result["reference_vision_failed"] == []


@pytest.mark.asyncio
async def test_optimizer_falls_back_when_primary_model_balance_is_insufficient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FREEZONE_PROMPT_OPTIMIZER_MODEL", "DC-deepseek-v4-pro-LLM")
    monkeypatch.setenv("FREEZONE_PROMPT_OPTIMIZER_FALLBACK_MODELS", "deepseek-v4-flash")
    called: list[str] = []

    class FakeAgent:
        def __init__(self, model: str):
            self.model = model

        async def run(self, task: str):
            del task
            called.append(self.model)
            if self.model == "DC-deepseek-v4-pro-LLM":
                raise RuntimeError(
                    "status_code: 402, model_name: DC-deepseek-v4-pro-LLM, 正文：{'message':'平衡不足'}"
                )

            class Response:
                output = PromptOptimizerLLMOutput(
                    optimized_prompt="备用模型优化后的提示词",
                    preserved_intent="保留原意",
                    changes=["降级成功"],
                    applied_rules=["fallback"],
                    warnings=[],
                    output_language="zh",
                )

            return Response()

    monkeypatch.setattr(
        prompt_optimizer,
        "get_prompt_optimizer_agent",
        lambda _env, model: FakeAgent(model),
    )

    result = await optimize_freezone_prompt(
        text="水果短剧，角色开心地跳起来",
        node_type="video",
        target_model_id="seedance-2.0",
    )

    assert called == ["DC-deepseek-v4-pro-LLM", "deepseek-v4-flash"]
    assert result["optimized_prompt"] == "备用模型优化后的提示词"
    assert result["optimizer_model"] == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_optimizer_returns_local_fallback_when_all_text_models_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FREEZONE_PROMPT_OPTIMIZER_MODEL", "DC-deepseek-v4-pro-LLM")
    monkeypatch.setenv("FREEZONE_PROMPT_OPTIMIZER_FALLBACK_MODELS", "deepseek-v4-flash")

    class FailingAgent:
        async def run(self, task: str):
            del task
            raise RuntimeError("status_code: 402, 正文：{'message':'平衡不足'}")

    monkeypatch.setattr(
        prompt_optimizer,
        "get_prompt_optimizer_agent",
        lambda *_args: FailingAgent(),
    )

    result = await optimize_freezone_prompt(
        text="一只橘猫看向镜头，然后慢慢眨眼",
        node_type="video",
        target_model_id="kling-v3",
        params={"mode": "imageToVideo", "duration": 5},
        references=[{"name": "@图片1", "role": "first_frame"}],
    )

    assert result["optimizer_model"] == prompt_optimizer.LOCAL_PROMPT_OPTIMIZER_MODEL
    assert result["optimizer_fallback_used"] is True
    assert "本地确定性优化" in result["warnings"][0]
    assert "@图片1(first_frame)" in result["optimized_prompt"]
    assert "负面约束" in result["optimized_prompt"]
    assert result["strategy_contract"]["schema"] == "prompt_strategy_contract.v1"
    assert result["strategy_contract"]["workflow"] == "image_to_video"
    assert result["strategy_contract"]["reference_semantics"] == "t0_facts_only"


@pytest.mark.asyncio
async def test_local_prompt_fallback_preserves_continuity_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prompt_optimizer, "resolve_optimizer_text_model_chain", lambda **_kwargs: [])

    result = await optimize_freezone_prompt(
        text="女孩沿月台向右跑去",
        node_type="video",
        target_model_id="seedance-2.0",
        params={
            "mode": "imageToVideo",
            "duration": 5,
            "continuity_in": {"action_state": "伞面半开，右手握伞柄"},
            "continuity_out": {"action_state": "跑出画面右侧，伞面完全打开", "z_ending": "保持材质" * 180 + "右手松开栏杆后停稳"},
        },
    )

    assert "连续性输入状态" in result["optimized_prompt"]
    assert "连续性输出状态" in result["optimized_prompt"]
    assert "不重演上一镜" in result["optimized_prompt"]
    assert "右手松开栏杆后停稳" in result["optimized_prompt"]
    assert "每段只保留一个主动作" not in result["optimized_prompt"]
    assert "主体身份与位置 → 一个主要动作" not in result["optimized_prompt"]


@pytest.mark.asyncio
async def test_local_fallback_does_not_certify_undeclared_reference(monkeypatch):
    monkeypatch.setattr(prompt_optimizer, "resolve_optimizer_text_model_chain", lambda **_kwargs: [])
    result = await optimize_freezone_prompt(
        text="@Video2 中的人物抬眼", node_type="video", target_model_id="seedance-2.0",
        params={"mode": "imageToVideo", "duration": 5},
        references=[{"name": "@Image1", "role": "first_frame"}],
    )
    assert result["optimizer_fallback_used"] is True
    assert result["critic_passed"] is False
    assert "提示词出现未在请求中声明的引用标记" in result["critic_issues"]


def _patch_project_resolution(
    monkeypatch: pytest.MonkeyPatch,
    project_dir: Path,
) -> None:
    async def _fake_resolve(project: str, user: dict, *, required_role: str = "editor"):
        del user, required_role
        return None, "admin", project, project_dir, str(project_dir)

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", _fake_resolve)


@pytest.mark.asyncio
async def test_prompt_optimize_route_forwards_exact_target_model_and_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    _patch_project_resolution(monkeypatch, project_dir)
    captured: dict[str, object] = {}

    def _fake_start(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(freezone_routes, "_start_freezone_prompt_optimize_task", _fake_start)
    result = await freezone_routes.freezone_prompt_optimize(
        project="58",
        body=freezone_routes.FreezonePromptOptimizeRequest(
            text="女孩从首帧向前一步",
            node_type="video",
            target_model_id="seedance-catalog-id",
            target_api_model="doubao-seedance-2-0-pro",
            target_model_label="Seedance 2.0 Pro",
            params={"mode": "imageToVideo", "duration_seconds": 8},
            references=[{"name": "@图片1", "role": "first_frame"}],
        ),
        user={"username": "admin"},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_prompt_optimize"
    body = captured["body"]
    assert isinstance(body, freezone_routes.FreezonePromptOptimizeRequest)
    assert body.target_model_id == "seedance-catalog-id"
    assert body.target_api_model == "doubao-seedance-2-0-pro"
    assert body.params["mode"] == "imageToVideo"
    assert body.references[0]["name"] == "@图片1"


@pytest.mark.asyncio
async def test_prompt_optimize_result_returns_provenance_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    job_id = "optimizerjob1"
    out = freezone_routes._prompt_optimize_output_path(project_dir, job_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "optimized_prompt": "0-4秒，角色抬眼；4-8秒，缓慢前推。",
        "optimizer_model": "DC-video-prompt-optimizer-LLM",
        "target_model_profile": "seedance_2_prompt_profile_v1",
        "knowledge_profiles": ["aigc_prompt_core_v1", "seedance_2_prompt_profile_v1"],
        "knowledge_sources": ["Seedance2-Official-SKILL.md"],
        "changes": [],
        "applied_rules": [],
        "warnings": [],
        "preserved_intent": "角色行动",
        "output_language": "zh",
        "revision": "xiaoshu-rag-model-optimizer.v2",
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    class FakeManager:
        def get_task(self, *args, **kwargs):
            return None

    _patch_project_resolution(monkeypatch, project_dir)
    monkeypatch.setattr(freezone_routes, "get_task_manager", lambda: FakeManager())
    result = await freezone_routes.freezone_job_result(
        project="58",
        task_type="freezone_prompt_optimize",
        job_id=job_id,
        user={"username": "admin"},
    )

    assert result["ok"] is True
    assert result["data"]["optimizer_model"] == "DC-video-prompt-optimizer-LLM"
    assert result["data"]["target_model_profile"] == "seedance_2_prompt_profile_v1"


# ── 联网研究参与 + 瞬时错误重试（2026-09-09）──────────────────────────────────


def test_prompt_research_trigger_reasons_flag_external_style_mentions() -> None:
    profiles = resolve_prompt_knowledge_profiles(
        node_type="image",
        target_model_id="direct/image-msfkiqdq-ed3hhy",
        target_model_label="gpt-image-2",
    )
    contract = compile_prompt_strategy_contract(
        node_type="image",
        target_model_id="direct/image-msfkiqdq-ed3hhy",
        target_model_label="gpt-image-2",
        params={},
    )
    common = dict(
        node_type="image",
        target_model_id="direct/image-msfkiqdq-ed3hhy",
        target_api_model="",
        target_model_label="gpt-image-2",
        params={},
        profiles=profiles,
        strategy_contract=contract,
        guidance="",
    )
    flagged = prompt_optimizer._prompt_research_trigger_reasons(
        text="减少噪点，使用mj图片模型的风格，我要一个美丽的宫崎骏风格的超帅剑客",
        **common,
    )
    assert "提示词点名了其他模型或外部风格" in flagged

    plain = prompt_optimizer._prompt_research_trigger_reasons(
        text="一只橘猫坐在窗台上看夕阳",
        **common,
    )
    assert "提示词点名了其他模型或外部风格" not in plain


@pytest.mark.asyncio
async def test_optimizer_retries_transient_upstream_failure_before_degrading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prompt_optimizer, "OPTIMIZER_RETRY_BACKOFF_SECONDS", 0.0)
    called: list[str] = []

    class FlakyAgent:
        def __init__(self, model: str):
            self.model = model

        async def run(self, task: str):
            del task
            called.append(self.model)
            if len(called) == 1:
                raise RuntimeError(
                    "ModelHTTPError: status_code: 503, model_name: gemini-3.8-flash, "
                    "body: {'message': 'auth_unavailable: no auth available'}"
                )

            class Response:
                output = PromptOptimizerLLMOutput(
                    optimized_prompt="重试成功后的提示词",
                    preserved_intent="保留原意",
                    changes=[],
                    applied_rules=[],
                    warnings=[],
                    output_language="zh",
                )

            return Response()

    monkeypatch.setattr(
        prompt_optimizer,
        "get_prompt_optimizer_agent",
        lambda _env, model: FlakyAgent(model),
    )
    monkeypatch.setenv("FREEZONE_PROMPT_OPTIMIZER_MODEL", "flaky-text-model")
    monkeypatch.setenv("FREEZONE_PROMPT_OPTIMIZER_FALLBACK_MODELS", "")

    result = await optimize_freezone_prompt(
        text="一只橘猫坐在窗台上看夕阳",
        node_type="image",
        target_model_id="direct/image-x",
    )

    assert called == ["flaky-text-model", "flaky-text-model"]
    assert result["optimized_prompt"] == "重试成功后的提示词"
    assert result["optimizer_fallback_used"] is False
    assert result["optimizer_failed_models"] == []


@pytest.mark.asyncio
async def test_optimizer_records_single_failure_after_transient_retries_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prompt_optimizer, "OPTIMIZER_RETRY_BACKOFF_SECONDS", 0.0)
    called: list[str] = []

    class AlwaysUnavailableAgent:
        def __init__(self, model: str):
            self.model = model

        async def run(self, task: str):
            del task
            called.append(self.model)
            raise RuntimeError(
                "ModelHTTPError: status_code: 503, body: {'message': 'auth_unavailable'}"
            )

    monkeypatch.setattr(
        prompt_optimizer,
        "get_prompt_optimizer_agent",
        lambda _env, model: AlwaysUnavailableAgent(model),
    )
    monkeypatch.setenv("FREEZONE_PROMPT_OPTIMIZER_MODEL", "always-503-model")
    monkeypatch.setenv("FREEZONE_PROMPT_OPTIMIZER_FALLBACK_MODELS", "")

    result = await optimize_freezone_prompt(
        text="一只橘猫坐在窗台上看夕阳",
        node_type="image",
        target_model_id="direct/image-x",
    )

    assert called == ["always-503-model"] * prompt_optimizer.OPTIMIZER_MAX_ATTEMPTS
    assert result["optimizer_fallback_used"] is True
    assert len(result["optimizer_failed_models"]) == 1
    assert result["optimizer_model"] == prompt_optimizer.LOCAL_PROMPT_OPTIMIZER_MODEL


@pytest.mark.asyncio
async def test_optimizer_reports_missing_project_context_for_research(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OkAgent:
        def __init__(self, model: str):
            self.model = model

        async def run(self, task: str):
            del task

            class Response:
                output = PromptOptimizerLLMOutput(
                    optimized_prompt="ok",
                    preserved_intent="ok",
                    changes=[],
                    applied_rules=[],
                    warnings=[],
                    output_language="zh",
                )

            return Response()

    monkeypatch.setattr(
        prompt_optimizer,
        "get_prompt_optimizer_agent",
        lambda _env, model: OkAgent(model),
    )
    monkeypatch.setenv("FREEZONE_PROMPT_OPTIMIZER_MODEL", "text-model")
    monkeypatch.setenv("FREEZONE_PROMPT_OPTIMIZER_FALLBACK_MODELS", "")

    result = await optimize_freezone_prompt(
        text="一只橘猫坐在窗台上看夕阳",
        node_type="image",
        target_model_id="direct/image-x",
    )

    assert result["research_status"] == "not_requested"
    assert result["research_skip_reason"] == "缺少项目上下文"


@pytest.mark.asyncio
async def test_optimizer_feeds_researched_evidence_into_the_text_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    class FakePool:
        def search(self, query: str, **_kwargs):
            return {
                "ok": True,
                "query": query,
                "results": [
                    {
                        "title": "Official model guide",
                        "url": "https://docs.example.invalid/model",
                        "content": "Supported image references and camera controls.",
                        "score": 0.9,
                    }
                ],
                "result_count": 1,
                "cached": False,
            }

    class CapturingAgent:
        def __init__(self, model: str):
            self.model = model

        async def run(self, task: str):
            captured["task"] = task

            class Response:
                output = PromptOptimizerLLMOutput(
                    optimized_prompt="联网后的提示词",
                    preserved_intent="保留原意",
                    changes=[],
                    applied_rules=[],
                    warnings=[],
                    output_language="zh",
                )

            return Response()

    monkeypatch.setattr("novelvideo.chat.tavily_pool.get_tavily_pool", lambda: FakePool())
    monkeypatch.setattr(
        prompt_optimizer,
        "get_prompt_optimizer_agent",
        lambda _env, model: CapturingAgent(model),
    )
    monkeypatch.setenv("FREEZONE_PROMPT_OPTIMIZER_MODEL", "text-model")
    monkeypatch.setenv("FREEZONE_PROMPT_OPTIMIZER_FALLBACK_MODELS", "")

    result = await optimize_freezone_prompt(
        text="用 Seedance 2.5 生成角色向前一步的镜头",
        node_type="video",
        target_model_id="seedance-2.5",
        project_dir=tmp_path,
        research_mode="expert",
    )

    assert result["research_used"] is True
    assert result["research_sources"][0]["url"].endswith("/model")
    assert "Official model guide" in captured["task"]


@pytest.mark.asyncio
async def test_prompt_optimize_runner_forwards_project_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.project_context import ProjectContext
    from novelvideo.task_backend.runners import freezone as runner

    captured: dict[str, object] = {}

    async def fake_optimize(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"optimized_prompt": "ok"}

    monkeypatch.setattr(
        "novelvideo.services.freezone_content.optimize_freezone_prompt",
        fake_optimize,
    )
    monkeypatch.setattr(runner, "_update", lambda *args, **kwargs: None)

    ctx = ProjectContext(
        project_id="project-1",
        project_name="demo",
        owner_type="user",
        owner_id="user-1",
        owner_username="tester",
        requester_user_id="user-1",
        requester_username="tester",
        requester_principals=(),
        effective_role="owner",
        home_node_id="home",
        output_dir=tmp_path,
        state_dir=tmp_path,
        runtime_dir=tmp_path,
        is_home_node=True,
    )
    envelope = {
        "payload": {
            "job_id": "job-1",
            "text": "一只橘猫坐在窗台上看夕阳",
            "node_type": "image",
            "target_model_id": "direct/image-x",
            "project_dir": str(tmp_path),
        }
    }

    await runner._run_freezone_prompt_optimize_async(envelope, ctx)

    assert captured["project_dir"] == tmp_path
    assert captured["research_mode"] == "standard"
