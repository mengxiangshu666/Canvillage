from __future__ import annotations

from typing import Any

import pytest

from novelvideo.production import filmcraft_kb
from novelvideo.production.filmcraft_kb import inject_filmcraft_rules
from novelvideo.production.director_vision import build_director_vision


def test_filmcraft_injector_is_triggered_by_director_vision() -> None:
    vision = build_director_vision(
        project_goal="三镜头雨夜对峙",
        output_spec={
            "visual_motifs": ["雨幕"],
            "emotional_arc": {
                "opening": "等待",
                "turning_point": "拔刀",
                "ending": "停手",
                "audience_feeling": "紧张",
            },
        },
    )
    rules = inject_filmcraft_rules(
        node_type="video",
        params={"shot_count": 3, "camera_movement": "slow_push"},
        director_vision=vision,
    )
    ids = {item["rule_id"] for item in rules}
    assert "craft.single_primary_action.v1" in ids
    assert "craft.continuity_handoff.v1" in ids
    assert "craft.visual_motif_recur.v1" in ids
    assert all(item["instruction"] and item["avoid"] for item in rules)


def test_filmcraft_injector_does_not_claim_untriggered_locks() -> None:
    rules = inject_filmcraft_rules(node_type="image", params={}, director_vision={})
    ids = {item["rule_id"] for item in rules}
    assert "craft.single_primary_action.v1" in ids
    assert "craft.continuity_handoff.v1" not in ids
    assert "craft.visual_motif_recur.v1" not in ids


def test_filmcraft_injector_adds_cinematic_rules_only_for_matching_context() -> None:
    rules = inject_filmcraft_rules(
        node_type="video",
        params={"shot_count": 3},
        director_vision={
            "schema": "director_vision.v1",
            "vision_revision": "test",
            "style_anchor": {
                "lighting": "左后侧窗光",
                "color_palette": "冷灰配暖木",
            },
            "cinematic": {
                "lighting": {"source_direction": "左后侧窗光"},
                "color_look": {"look_id": "window-warm-v1"},
            },
            "continuity_locks": {"characters": ["hero"]},
        },
        project_dna={"project_id": "project-1"},
        source_text="夜里两人在窗边争执后追逐",
        creation_stage="shot_prompt",
    )
    ids = {item["rule_id"] for item in rules}

    assert "craft.lighting_motivation.v1" in ids
    assert "craft.lighting_continuity.v1" in ids
    assert "craft.screen_direction_axis.v1" in ids
    assert "craft.color_quota_60_30_10.v1" in ids
    assert "craft.edit_on_action.v1" in ids
    assert "craft.sound_layers.v1" in ids
    assert "craft.positive_constraint_rewrite.v1" in ids
    assert "craft.cross_episode_continuity.v1" not in ids
    assert "craft.final_delivery_qc.v1" not in ids


def test_filmcraft_injector_adds_series_and_delivery_rules_only_when_declared() -> None:
    rules = inject_filmcraft_rules(
        node_type="video",
        params={},
        director_vision={
            "schema": "director_vision.v1",
            "vision_revision": "test",
            "cinematic": {
                "continuity": {
                    "series_id": "series-1",
                    "episode_index": 2,
                    "asset_revisions": {"hero": 4},
                }
            },
        },
        creation_stage="delivery",
    )
    ids = {item["rule_id"] for item in rules}

    assert "craft.cross_episode_continuity.v1" in ids
    assert "craft.final_delivery_qc.v1" in ids


async def _capture_storyboard_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
    *,
    request: str,
    inputs: dict[str, Any] | None = None,
) -> str:
    """跑一次分镜编译器，把真正交给模型的 system_prompt 截下来。

    分镜编译器的提示词只在构造 pydantic_ai.Agent 的那一刻存在，
    所以这里换掉 Agent 与直连模型，在构造函数里取提示词，再用一个
    哨兵异常终止后续的出片流程。
    """

    import pydantic_ai

    from novelvideo.generators import direct_models
    from novelvideo.workflow_runtime import executor as workflow_executor

    monkeypatch.setattr(
        direct_models,
        "get_direct_pydantic_model",
        lambda *_args, **_kwargs: object(),
    )
    captured: dict[str, str] = {}

    class _PromptCaptured(Exception):
        pass

    class _FakeAgent:
        def __init__(self, *_args: Any, **kwargs: Any) -> None:
            captured["system_prompt"] = str(kwargs.get("system_prompt") or "")

        async def run(self, _request: str) -> Any:
            raise _PromptCaptured

    monkeypatch.setattr(pydantic_ai, "Agent", _FakeAgent)
    run: dict[str, Any] = {
        "contract_version": 1,
        "inputs": inputs if inputs is not None else {"request": request},
        "artifacts": {},
    }
    with pytest.raises(_PromptCaptured):
        await workflow_executor._storyboard_handler(run, {"id": "story_and_shots"})
    return captured["system_prompt"]


@pytest.mark.asyncio
async def test_storyboard_handler_prompt_carries_filmcraft_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """北极星链的分镜编译器必须带上 filmcraft_kb 的手艺规则。"""

    request = "夜里两人在窗边争执后追逐的三镜短片"
    prompt = await _capture_storyboard_system_prompt(monkeypatch, request=request)

    # 具体规则文本：删掉注入就会失败，不是只看有没有标题。
    assert "craft.single_primary_action.v1" in prompt
    assert "每镜明确当前叙事重点" in prompt
    assert "禁忌：无因果的动作堆叠、遗漏接触与结果、用形容词代替可见行为。" in prompt
    # 这条只在 creation_stage="storyboard" 时触发，证明编译阶段被真的传了进去。
    assert "craft.prompt_array_order.v1" in prompt

    expected = inject_filmcraft_rules(
        node_type="video",
        params={},
        director_vision={},
        project_dna={},
        source_text=request,
        creation_stage="storyboard",
    )
    assert expected
    for item in expected:
        header = f"[{item['rule_id']} | stage={item['stage']} | trigger={item['trigger']}]"
        assert header in prompt
        assert item["instruction"] in prompt
        assert item["avoid"] in prompt

    # 规则为空时不得留下空段落或残留标题。
    monkeypatch.setattr(filmcraft_kb, "inject_filmcraft_rules", lambda **_kwargs: [])
    empty_prompt = await _capture_storyboard_system_prompt(monkeypatch, request=request)
    assert "手艺规则" not in empty_prompt


@pytest.mark.asyncio
async def test_storyboard_prompt_ignores_ordinary_motion_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """温情片里的「推开门、走到窗边」不是打戏，不得注入打斗专用规则。

    旧关键词表把单字 `推/拉/走/跑/摔` 当成动作信号，于是「母亲推开门，
    走到窗边坐下」这种日常走位也会把 `craft.action_fragment_shots.v1`
    （碎片打斗镜）塞进分镜提示词。
    """

    request = "三镜温情短片：母亲推开门，走到窗边坐下，慢慢说了一会儿话"
    prompt = await _capture_storyboard_system_prompt(monkeypatch, request=request)

    # 先证明规则块本身渲染了（否则下面两条断言会因为空串而假绿）。
    assert "全片手艺规则" in prompt
    assert "craft.action_fragment_shots.v1" not in prompt
    assert "根据动作关系、表演重点与模型能力" not in prompt


@pytest.mark.asyncio
async def test_storyboard_prompt_keeps_combat_rule_for_explicit_action_scene(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """明确的打戏措辞仍然必须带出打斗专用规则。"""

    request = "三镜追逐打斗短片：两人在狭窄巷口短兵相接"
    prompt = await _capture_storyboard_system_prompt(monkeypatch, request=request)

    assert "craft.action_fragment_shots.v1" in prompt
    assert "根据动作关系、表演重点与模型能力" in prompt


@pytest.mark.asyncio
async def test_storyboard_prompt_ignores_negated_combat_wording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「全片不要打斗，也不要追逐」是禁止项，不得当成打戏请求。"""

    request = "三镜温情短片：母亲推开门，走到窗边坐下，全片不要打斗，也不要追逐"
    prompt = await _capture_storyboard_system_prompt(monkeypatch, request=request)

    # 先证明规则块本身渲染了，否则下面两条断言会因为空串而假绿。
    assert "全片手艺规则" in prompt
    assert "craft.action_fragment_shots.v1" not in prompt
    assert "根据动作关系、表演重点与模型能力" not in prompt


def test_inject_filmcraft_rules_honors_explicit_action_context() -> None:
    """显式 `params["action_context"]` 覆盖关键词路径，真值假值都生效。"""

    rule_id = "craft.action_fragment_shots.v1"

    def ids(params: dict[str, Any], source_text: str = "三镜追逐打斗") -> set[str]:
        return {
            item["rule_id"]
            for item in inject_filmcraft_rules(
                node_type="video",
                params=params,
                director_vision={},
                project_dna={},
                source_text=source_text,
                creation_stage="storyboard",
            )
        }

    warm = "三镜温情短片：母亲推开门，走到窗边坐下，慢慢说了一会儿话"
    assert rule_id not in ids({"action_context": False})
    assert rule_id in ids({"action_context": True})
    # 显式 True 在温情文本上也要生效：它压过关键词路径，不是「和关键词取交集」。
    assert rule_id in ids({"action_context": True}, source_text=warm)
    # 不传该键的旧调用方走关键词路径，安全措辞列表里仍有「打斗」。
    assert rule_id in ids({})


@pytest.mark.parametrize("value", ["false", "true", "no", 0, 1, "", None, [], {}])
def test_inject_filmcraft_rules_never_enables_on_non_bool_action_context(value: Any) -> None:
    """非 bool 的 `action_context` 一律不激活规则。

    `params` 可以来自客户端 JSON（`dict[str, Any]`），`bool("false")` 为真，
    旧实现会把字符串 `"false"` 当成「要打戏」。现在只有真正的 bool 算决策，
    其余值（包括 `"true"`）都按关闭处理。
    """

    ids = {
        item["rule_id"]
        for item in inject_filmcraft_rules(
            node_type="video",
            params={"action_context": value},
            director_vision={},
            project_dna={},
            source_text="三镜追逐打斗",
            creation_stage="storyboard",
        )
    }
    assert "craft.action_fragment_shots.v1" not in ids


@pytest.mark.parametrize(
    "wording",
    [
        "三镜动作片：主角在仓库里与人对决",
        "第三镜是武打动作",
        "两人在雨里摔跤",
        "一个动作短片：夜里的巷口",
        "动作剧的第三集",
        "两人扭打在一起",
    ],
)
def test_legacy_keyword_path_keeps_clear_combat_wording(wording: str) -> None:
    """不传 `action_context` 的旧调用方（画布侧）仍要认出明确的打戏措辞。

    R4 把词表收窄成 18 个短语后，`动作片/武打动作/摔跤/对决/扭打` 这类
    明确的打戏说法被漏掉了；这里逐个钉住召回。
    """

    ids = {
        item["rule_id"]
        for item in inject_filmcraft_rules(
            node_type="video",
            params={},
            director_vision={},
            project_dna={},
            source_text=wording,
            creation_stage="storyboard",
        )
    }
    assert "craft.action_fragment_shots.v1" in ids, wording


def test_legacy_keyword_path_ignores_warm_ordinary_blocking() -> None:
    """旧调用方的温情走位不得命中打斗规则（R4 的原始误报）。"""

    ids = {
        item["rule_id"]
        for item in inject_filmcraft_rules(
            node_type="video",
            params={},
            director_vision={},
            project_dna={},
            source_text="三镜温情短片：母亲推开门，走到窗边坐下，慢慢说了一会儿话",
            creation_stage="storyboard",
        )
    }
    assert "craft.action_fragment_shots.v1" not in ids


def test_action_context_decision_ignores_negated_combat_wording() -> None:
    """门面决策必须读懂「不要打斗，也不要追逐」，而不是纯子串命中。"""

    from novelvideo.services.production_contracts import has_filmcraft_action_context

    negated = "三镜温情短片：母亲推开门，走到窗边坐下，全片不要打斗，也不要追逐"
    warm = "三镜温情短片：母亲推开门，走到窗边坐下"
    action = "三镜动作片：主角在仓库里与人对决"

    assert has_filmcraft_action_context(negated) is False
    assert has_filmcraft_action_context(warm) is False
    assert has_filmcraft_action_context(action) is True
    # R6：否定只作用于自己所在的小句。下一句里的打斗措辞仍算命中，
    # 所以「不要打斗，要看追逐」由 R5 的 False 更正为 True（见 R6 回归测试）。
    assert has_filmcraft_action_context("不要打斗，要看追逐") is True
    assert has_filmcraft_action_context("不要温情；第三镜两人追逐打斗") is True


def test_storyboard_contract_follows_the_facade_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分镜侧不再自带词表：门面决策换成什么，合同就跟着变。

    这条是漂移守卫的行为级版本——不去 import production 的私有词表，
    直接改门面决策的返回值，看分镜合同是否跟着变。
    """

    from novelvideo.services import production_contracts
    from novelvideo.workflow_runtime.storyboard_prompt import (
        build_storyboard_filmcraft_contract,
    )

    action_request = "三镜追逐打斗短片：两人在狭窄巷口短兵相接"
    warm_request = "三镜温情短片：母亲推开门，走到窗边坐下"

    # 门面告诉它「不是打戏」，即使请求文本本身含明确打斗词也不注入。
    monkeypatch.setattr(
        production_contracts,
        "has_filmcraft_action_context",
        lambda _source_text: False,
    )
    assert "craft.action_fragment_shots.v1" not in build_storyboard_filmcraft_contract(
        action_request
    )

    # 反过来，门面说是打戏，温情文本也会带上打斗规则——证明分镜侧真的在问门面。
    monkeypatch.setattr(
        production_contracts,
        "has_filmcraft_action_context",
        lambda _source_text: True,
    )
    warm_contract = build_storyboard_filmcraft_contract(warm_request)
    assert "craft.action_fragment_shots.v1" in warm_contract
    assert "根据动作关系、表演重点与模型能力" in warm_contract


@pytest.mark.asyncio
async def test_storyboard_handler_selects_rules_for_reuse_existing_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """复用现有节点时，分镜编译器必须把选中节点数当成多镜上下文。

    ``craft.continuity_handoff.v1`` 的触发条件是
    ``director_vision or multi_shot``：这里既没有 DirectorVision，又没有
    直接调注入器，只有 ``target_strategy="reuse_existing"`` 分支真的把
    ``shot_count=3`` 传进规则选择器，它才会出现在提示词里。
    """

    request = "夜里两人在窗边争执后追逐的三镜短片"
    baseline = await _capture_storyboard_system_prompt(monkeypatch, request=request)
    assert "craft.continuity_handoff.v1" not in baseline

    prompt = await _capture_storyboard_system_prompt(
        monkeypatch,
        request=request,
        inputs={
            "request": request,
            "target_strategy": "reuse_existing",
            "target_node_ids": ["node-1", "node-2", "node-3"],
        },
    )

    assert "craft.continuity_handoff.v1" in prompt
    # 三个目标节点被真的读成了镜头数，而不是只换了个分支。
    assert "必须输出恰好 3 个 shots" in prompt

    expected = inject_filmcraft_rules(
        node_type="video",
        params={"shot_count": 3},
        director_vision={},
        project_dna={},
        source_text=request,
        creation_stage="storyboard",
    )
    expected_ids = {item["rule_id"] for item in expected}
    assert "craft.continuity_handoff.v1" in expected_ids
    for item in expected:
        header = f"[{item['rule_id']} | stage={item['stage']} | trigger={item['trigger']}]"
        assert header in prompt
        assert item["instruction"] in prompt
        assert item["avoid"] in prompt


# ―――― G-01-R6：小句级否定、补齐打斗措辞、统一门面调用路径、共享文本截断 ――――

_R6_ACTION_RULE_ID = "craft.action_fragment_shots.v1"

_R6_FORBIDDEN_NEGATIONS = (
    "全片不要打斗，也不要追逐",
    "全片不得出现打斗",
    "全片严禁打斗",
    "不可有打斗",
    "不准打斗",
    "不许打斗",
    "不出现打斗",
)

_R6_AFFIRMATIVE_AFTER_NEGATION = (
    "不要温情，要打斗",
    "不要慢动作，两人打斗",
    "不要打斗，要看追逐",
    "不要温情；第三镜两人追逐打斗",
)

_R6_ADDED_COMBAT_CUES = (
    "第三镜两人摔打在一起",
    "两人近身肉搏",
    "第三镜两人对打",
    "两人一言不合打起来",
    "两人开打",
    "多人群殴",
    "最终决战",
    "双方交战",
)

_R6_CLAUSE_BOUNDARIES = ("，", "。", "；", "！", "？", "、", ",", ".", ";", "!", "?", "\n")

_R6_NEGATION_PHRASES = (
    "不要",
    "不用",
    "无需",
    "没有",
    "不含",
    "禁止",
    "避免",
    "别",
    "勿",
    "不得",
    "严禁",
    "不可",
    "不准",
    "不许",
    "不出现",
    "不再",
    "切勿",
    "莫",
)


def _r6_facade_injection(source_text: str) -> set[str]:
    """经门面注入器取一次规则 id 集合（与分镜侧同一条调用路径）。"""

    from novelvideo.services import production_contracts

    return {
        item["rule_id"]
        for item in production_contracts.inject_filmcraft_rules(
            node_type="video",
            params={},
            director_vision={},
            project_dna={},
            source_text=source_text,
            creation_stage="storyboard",
        )
    }


def _r6_direct_injection(source_text: str) -> set[str]:
    """直接用 production 实现取一次规则 id 集合（旧调用方的路径）。"""

    return {
        item["rule_id"]
        for item in inject_filmcraft_rules(
            node_type="video",
            params={},
            director_vision={},
            project_dna={},
            source_text=source_text,
            creation_stage="storyboard",
        )
    }


@pytest.mark.parametrize("wording", _R6_FORBIDDEN_NEGATIONS)
def test_action_context_decision_rejects_every_forbidden_negation(wording: str) -> None:
    """R6：评审点名的七种禁止说法都必须判 False，分镜合同也不得带打斗规则。"""

    from novelvideo.services.production_contracts import has_filmcraft_action_context
    from novelvideo.workflow_runtime.storyboard_prompt import (
        build_storyboard_filmcraft_contract,
    )

    assert has_filmcraft_action_context(wording) is False, wording
    assert _R6_ACTION_RULE_ID not in build_storyboard_filmcraft_contract(wording), wording
    assert "根据动作关系、表演重点与模型能力" not in build_storyboard_filmcraft_contract(wording), wording


@pytest.mark.parametrize("wording", _R6_AFFIRMATIVE_AFTER_NEGATION)
def test_action_context_decision_keeps_affirmative_clause_after_negation(wording: str) -> None:
    """R6：否定只作用于自己所在的小句；后面肯定的小句要带出分镜打斗规则。"""

    from novelvideo.services.production_contracts import has_filmcraft_action_context
    from novelvideo.workflow_runtime.storyboard_prompt import (
        build_storyboard_filmcraft_contract,
    )

    assert has_filmcraft_action_context(wording) is True, wording
    assert _R6_ACTION_RULE_ID in build_storyboard_filmcraft_contract(wording), wording
    assert "根据动作关系、表演重点与模型能力" in build_storyboard_filmcraft_contract(wording), wording


@pytest.mark.parametrize("boundary", _R6_CLAUSE_BOUNDARIES)
def test_negation_never_crosses_a_clause_boundary(boundary: str) -> None:
    """R6：标点即小句边界，否定不得越界（也不得被越界忽略）。"""

    from novelvideo.services.production_contracts import has_filmcraft_action_context

    assert has_filmcraft_action_context(f"不要温情{boundary}要打斗") is True, boundary
    assert has_filmcraft_action_context(f"不要打斗{boundary}要温情") is False, boundary


@pytest.mark.parametrize("negation", _R6_NEGATION_PHRASES)
def test_every_required_negation_phrase_controls_its_own_clause(negation: str) -> None:
    """R6：要求识别的否定短语逐个生效，且只在自己小句里生效。"""

    from novelvideo.services.production_contracts import has_filmcraft_action_context

    assert has_filmcraft_action_context(f"{negation}打斗") is False, negation
    assert has_filmcraft_action_context(f"{negation}温情，要打斗") is True, negation


def test_clause_scope_keeps_the_ordinary_word_exception_for_bie() -> None:
    """R6：`别` 的普通词例外仍在，不能把「特别激烈的打斗」读成否定。"""

    from novelvideo.services.production_contracts import has_filmcraft_action_context

    assert has_filmcraft_action_context("特别激烈的打斗") is True
    assert has_filmcraft_action_context("别打斗") is False


@pytest.mark.parametrize("wording", _R6_ADDED_COMBAT_CUES)
def test_r6_combat_cues_reach_legacy_callers_and_storyboard(wording: str) -> None:
    """R6：补回的八个明确打斗措辞对旧调用方与分镜侧都要命中。"""

    from novelvideo.services.production_contracts import has_filmcraft_action_context
    from novelvideo.workflow_runtime.storyboard_prompt import (
        build_storyboard_filmcraft_contract,
    )

    assert has_filmcraft_action_context(wording) is True, wording
    # 旧调用方（不传 action_context 键）：直接注入与门面注入都要命中。
    assert _R6_ACTION_RULE_ID in _r6_direct_injection(wording), wording
    assert _R6_ACTION_RULE_ID in _r6_facade_injection(wording), wording
    # 分镜合同走的是门面决策，也必须带上打斗规则。
    assert _R6_ACTION_RULE_ID in build_storyboard_filmcraft_contract(wording), wording


def test_storyboard_contract_follows_the_facade_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R6：分镜侧按模块属性调用门面注入器，monkeypatch 必须能控制合同。

    旧实现把 `inject_filmcraft_rules` 在 import 期绑定成函数对象，改门面属性
    对分镜注入毫无影响；现在两处都走同一个门面模块路径。
    """

    from novelvideo.services import production_contracts
    from novelvideo.workflow_runtime.storyboard_prompt import (
        build_storyboard_filmcraft_contract,
    )

    request = "三镜追逐打斗短片：两人在狭窄巷口短兵相接"
    assert _R6_ACTION_RULE_ID in build_storyboard_filmcraft_contract(request)

    calls: list[dict[str, Any]] = []

    def fake_inject_filmcraft_rules(**kwargs: Any) -> list[dict[str, str]]:
        calls.append(kwargs)
        return [
            {
                "rule_id": "craft.r6_sentinel.v1",
                "stage": "shot_prompt",
                "trigger": "action_context",
                "instruction": "哨兵指令：门面注入器被调用",
                "avoid": "哨兵禁忌：不得出现",
            }
        ]

    monkeypatch.setattr(
        production_contracts, "inject_filmcraft_rules", fake_inject_filmcraft_rules
    )
    contract = build_storyboard_filmcraft_contract(request)

    assert calls, "分镜侧没有调用门面注入器"
    assert calls[0]["creation_stage"] == "storyboard"
    assert calls[0]["params"]["action_context"] is True
    assert "craft.r6_sentinel.v1" in contract
    assert "哨兵指令：门面注入器被调用" in contract
    # 真的走了被替换的注入器，而不是 import 期绑定的原函数。
    assert _R6_ACTION_RULE_ID not in contract


def test_action_context_text_limit_is_shared_by_decision_and_injection() -> None:
    """R6：门面决策与规则选择必须读同一段文本（同样截断到 12,000 字）。"""

    from novelvideo.production import filmcraft_kb
    from novelvideo.services import production_contracts

    limit = filmcraft_kb.ACTION_CONTEXT_TEXT_LIMIT
    assert limit == 12_000
    assert filmcraft_kb.normalize_action_context_text("  abc  ") == "abc"
    assert len(filmcraft_kb.normalize_action_context_text("甲" * 20_000)) == limit

    filler = "三镜温情短片：母亲推开门，走到窗边坐下。" * 800
    assert len(filler) > limit
    assert _R6_ACTION_RULE_ID not in _r6_facade_injection(filler)

    beyond = filler + "两人近身肉搏"
    within = "两人近身肉搏" + filler

    # 词条落在 12,000 字之后：决策、门面注入、直接注入都必须看不见它。
    assert production_contracts.has_filmcraft_action_context(beyond) is False
    assert _R6_ACTION_RULE_ID not in _r6_facade_injection(beyond)
    assert _R6_ACTION_RULE_ID not in _r6_direct_injection(beyond)

    # 词条落在 12,000 字之内：三条路径都必须看得见它。
    assert production_contracts.has_filmcraft_action_context(within) is True
    assert _R6_ACTION_RULE_ID in _r6_facade_injection(within)
    assert _R6_ACTION_RULE_ID in _r6_direct_injection(within)


# ―――― G-01-R7：否定必须附着在词条上，`、` 是列举分隔符而不是小句边界 ――――

# 六个「明确要打、但另有被否定的名词」的输入：必须判 True 并带出打斗规则。
_R7_CLEAR_COMBAT_WITH_OTHER_NEGATION = (
    "不需要对白的三镜打斗短片",
    "避免煽情的三镜打斗短片",
    "不做慢动作的肉搏戏",
    "没有武器的两人对打",
    "没有台词的巷口决战",
    "不要温情，没有台词的打斗",
)

# 三个用 `、` 列举的纯禁止：一个否定管住整串列举项，必须判 False。
_R7_ENUMERATION_PROHIBITIONS = (
    "全片不要打斗，也不要追逐",
    "全片不要打斗、追逐",
    "不要出现打斗、爆炸、群殴",
)

# 直接附着形式必须继续生效（否定与词条之间只有功能词/桥接词）。
_R7_DIRECT_ATTACHMENTS = (
    "不要打斗",
    "全片不得出现打斗",
    "全片不要有打斗",
    "不可有打斗",
    "不出现打斗",
    "不要打斗，也不要追逐",
    "不要打斗、追逐",
    "禁止任何打斗",
)

# 其他名词的修饰语不得否掉后面的词条（否定与词条之间夹着实词）。
_R7_NEGATED_OTHER_NOUN_MODIFIERS = (
    "没有台词的三镜打斗",
    "没有武器的两人对打",
    "不需要对白的三镜打斗",
    "避免煽情的三镜打斗",
    "不做慢动作的肉搏戏",
)

# 肯定标记把同一列举/小句里的词条重新打开。
_R7_ENUMERATION_AFFIRMATIVE_REOPEN = (
    "不要温情、要打斗",
    "不要打斗、要看追逐",
    "不要温情，而是两人追逐打斗",
)


@pytest.mark.parametrize("wording", _R7_CLEAR_COMBAT_WITH_OTHER_NEGATION)
def test_r7_other_negated_noun_does_not_suppress_the_combat_cue(wording: str) -> None:
    """R7：否定的是另一个名词时，后面的明确打斗词条仍然算命中。

    旧语义只问「否定与词条是否同小句」，于是「没有台词的三镜打斗」被判成
    禁止打斗；R7 要求否定必须真的附着在词条上。
    """

    from novelvideo.services.production_contracts import has_filmcraft_action_context
    from novelvideo.workflow_runtime.storyboard_prompt import (
        build_storyboard_filmcraft_contract,
    )

    assert has_filmcraft_action_context(wording) is True, wording
    # 旧调用方（画布侧）的两条路径也要看得见。
    assert _R6_ACTION_RULE_ID in _r6_direct_injection(wording), wording
    assert _R6_ACTION_RULE_ID in _r6_facade_injection(wording), wording
    contract = build_storyboard_filmcraft_contract(wording)
    assert _R6_ACTION_RULE_ID in contract, wording
    assert "根据动作关系、表演重点与模型能力" in contract, wording


@pytest.mark.parametrize("wording", _R7_ENUMERATION_PROHIBITIONS)
def test_r7_enumeration_prohibition_covers_every_listed_cue(wording: str) -> None:
    """R7：`、` 不是小句边界，一个禁止词管住整串列举项。"""

    from novelvideo.services.production_contracts import has_filmcraft_action_context
    from novelvideo.workflow_runtime.storyboard_prompt import (
        build_storyboard_filmcraft_contract,
    )

    assert has_filmcraft_action_context(wording) is False, wording
    assert _R6_ACTION_RULE_ID not in _r6_facade_injection(wording), wording
    assert _R6_ACTION_RULE_ID not in _r6_direct_injection(wording), wording
    assert _R6_ACTION_RULE_ID not in build_storyboard_filmcraft_contract(wording), wording


@pytest.mark.parametrize("wording", _R7_DIRECT_ATTACHMENTS)
def test_r7_direct_attachment_still_negates(wording: str) -> None:
    """R7：直接附着（中间只有功能词）与跨 `、` 的列举仍算禁止。"""

    from novelvideo.services.production_contracts import has_filmcraft_action_context

    assert has_filmcraft_action_context(wording) is False, wording


@pytest.mark.parametrize("wording", _R7_NEGATED_OTHER_NOUN_MODIFIERS)
def test_r7_reports_the_negated_other_noun_not_the_cue(wording: str) -> None:
    """R7：否定与词条之间夹着实词时，说明否定修饰的是那个实词。"""

    from novelvideo.production import filmcraft_kb

    lowered = wording.casefold()
    cue_index = next(
        lowered.find(cue.casefold())
        for cue in filmcraft_kb._ACTION_CONTEXT_CUES
        if cue.casefold() in lowered
    )
    assert filmcraft_kb._negated_action_cue(lowered, cue_index) is False, wording


@pytest.mark.parametrize("wording", _R7_ENUMERATION_AFFIRMATIVE_REOPEN)
def test_r7_enumeration_affirmative_marker_reopens_the_cue(wording: str) -> None:
    """R7：同一列举段里出现肯定标记时，否定不再跨 `、` 生效。"""

    from novelvideo.services.production_contracts import has_filmcraft_action_context
    from novelvideo.workflow_runtime.storyboard_prompt import (
        build_storyboard_filmcraft_contract,
    )

    assert has_filmcraft_action_context(wording) is True, wording
    assert _R6_ACTION_RULE_ID in build_storyboard_filmcraft_contract(wording), wording


def test_r7_enumeration_separator_is_not_a_clause_boundary() -> None:
    """R7：`、` 归列举分隔符，`，。；！？,.;!?\\n` 仍是小句边界。"""

    from novelvideo.production import filmcraft_kb

    assert "、" not in filmcraft_kb._CLAUSE_BOUNDARIES
    assert filmcraft_kb._ENUMERATION_SEPARATORS == ("、",)
    # R6 那条参数化用例仍然成立：`、` 两侧的肯定/否定语义不变。
    assert filmcraft_kb._has_filmcraft_action_context("不要温情、要打斗") is True
    assert filmcraft_kb._has_filmcraft_action_context("不要打斗、要温情") is False


# ―――― G-01-R8：直接禁止的功能词脚手架；列举项可带数量/景别描述 ――――

# 六条被评审复现的误报：都是纯禁止，必须判 False 且不带出打斗规则。
_R8_SCAFFOLDED_PROHIBITIONS = (
    "不要中景打斗",
    "不要特写打斗",
    "不要在画面里打斗",
    "不要在画面里出现打斗",
    "不要出现打斗、爆炸、两人群殴",
    "不要出现打斗、爆炸、多人群殴",
    "全片不要打斗、三人追逐",
)

# 列举项带描述语的纯禁止：一个是清单里已有词条，另一个是被禁止的那一项带描述语。
_R8_ENUMERATION_ITEMS_WITH_DESCRIPTORS = (
    "不要打斗、近身肉搏",
    "不要出现打斗、爆炸、特写群殴",
)

# 肯定重开：否定不跨列举/小句压掉后面的肯定项。
_R8_AFFIRMATIVE_KEPT = (
    "不要温情、要打斗",
    "不要打斗、要看追逐",
    "不要温情，而是两人追逐打斗",
)


def _r8_rule_present(wording: str) -> bool:
    """分镜合同里是否带出打斗专用规则（与 R6/R7 用的是同一条门面路径）。"""

    from novelvideo.workflow_runtime.storyboard_prompt import (
        build_storyboard_filmcraft_contract,
    )

    return _R6_ACTION_RULE_ID in build_storyboard_filmcraft_contract(wording)


@pytest.mark.parametrize("wording", _R8_SCAFFOLDED_PROHIBITIONS)
def test_r8_scaffolded_prohibition_suppresses_the_cue(wording: str) -> None:
    """R8：`中景/特写/在画面里/两人` 是禁止项的脚手架，不是被否定的实词。

    旧桥接词表把 `画面/镜头/场面/里/中` 当功能词全局替换，于是「中景」被削成
    「景」、「在画面里」被整段抹掉；`_only_bridge_tokens_or_cues` 又要求列举的
    最后一项也只有功能词，于是「两人群殴」「三人追逐」这种带数量词的列举项
    被当成实词，纯禁止漏判成 True。
    """

    from novelvideo.services.production_contracts import has_filmcraft_action_context

    assert has_filmcraft_action_context(wording) is False, wording
    assert _R6_ACTION_RULE_ID not in _r6_facade_injection(wording), wording
    assert _R6_ACTION_RULE_ID not in _r6_direct_injection(wording), wording
    assert _r8_rule_present(wording) is False, wording


@pytest.mark.parametrize("wording", _R8_SCAFFOLDED_PROHIBITIONS)
def test_r8_every_cue_occurrence_in_a_prohibition_is_negated(wording: str) -> None:
    """R8：单元级钉法——禁止项里出现的**每个**词条都被否定，不只是第一个。"""

    from novelvideo.production import filmcraft_kb

    lowered = wording.casefold()
    found = False
    for cue in filmcraft_kb._ACTION_CONTEXT_CUES:
        needle = cue.casefold()
        start = lowered.find(needle)
        while start >= 0:
            found = True
            assert filmcraft_kb._negated_action_cue(lowered, start) is True, (
                wording,
                cue,
            )
            start = lowered.find(needle, start + 1)
    assert found, wording


@pytest.mark.parametrize(
    "wording", _R8_ENUMERATION_ITEMS_WITH_DESCRIPTORS
)
def test_r8_enumeration_item_may_carry_descriptor_wording(wording: str) -> None:
    """R8：一个禁止词已经压住前面的列举词条时，后一项可以带描述语。

    判据是「列举的前几项必须是词条」——那是这段文字属于同一个禁止清单的证据；
    最后一项不再重新审查实词，否则「近身肉搏」「特写群殴」会再次漏判。
    """

    from novelvideo.services.production_contracts import has_filmcraft_action_context

    assert has_filmcraft_action_context(wording) is False, wording
    assert _r8_rule_present(wording) is False, wording


def test_r8_enumeration_without_a_listed_cue_stays_affirmative() -> None:
    """R8：列举的第一项不是词条时，后面的打斗措辞仍是肯定请求。

    `不要温情、两人打斗` 的 `温情` 不是词条，所以这段文字不是禁止清单，
    `、` 之后是肯定的打斗请求（True，并带出打斗规则）。这是 R7 就有的
    「`、` 之后按肯定读」语义，R8 原样保留。
    """

    from novelvideo.services.production_contracts import has_filmcraft_action_context

    assert has_filmcraft_action_context("不要温情、两人打斗") is True
    assert _r8_rule_present("不要温情、两人打斗") is True
    # 反例对照：第一项是词条时，同一形状就是禁止清单。
    assert has_filmcraft_action_context("不要打斗、两人追逐") is False
    assert _r8_rule_present("不要打斗、两人追逐") is False


@pytest.mark.parametrize("wording", _R8_AFFIRMATIVE_KEPT)
def test_r8_affirmative_marker_still_reopens_the_cue(wording: str) -> None:
    """R8：肯定标记（`要/而是`）仍然重新打开同一列举段里的词条。"""

    from novelvideo.services.production_contracts import has_filmcraft_action_context

    assert has_filmcraft_action_context(wording) is True, wording
    assert _r8_rule_present(wording) is True, wording


def test_r8_bridge_tokens_keep_no_content_nouns() -> None:
    """R8：内容名词不再进全局桥接词表；它们只在结构判据里被放行。"""

    from novelvideo.production import filmcraft_kb

    for token in ("画面", "镜头", "场面", "里", "中"):
        assert token not in filmcraft_kb._NEGATION_BRIDGE_TOKENS, token
    # 结构判据：介词框架与景别/数量脚手架放行，实词不放行。
    assert filmcraft_kb._only_bridge_tokens("在画面里") is True
    assert filmcraft_kb._only_bridge_tokens("中景") is True
    assert filmcraft_kb._only_bridge_tokens("两人") is True
    assert filmcraft_kb._only_bridge_tokens("画面") is False
    assert filmcraft_kb._only_bridge_tokens("台词") is False
    assert filmcraft_kb._only_bridge_tokens("慢动作") is False


def test_r8_keeps_the_r6_cue_and_negation_vocabulary() -> None:
    """R8：37 条词条与全部否定短语一条未动（不重加裸 `推/拉/走/跑/摔/动作`）。"""

    from novelvideo.production import filmcraft_kb

    assert len(filmcraft_kb._ACTION_CONTEXT_CUES) == 37
    for removed in ("推", "拉", "走", "跑", "摔", "动作"):
        assert removed not in filmcraft_kb._ACTION_CONTEXT_CUES, removed
    for phrase in _R6_NEGATION_PHRASES:
        assert phrase in filmcraft_kb._ACTION_CONTEXT_NEGATIONS, phrase
    # 小句边界与列举分隔符也不变。
    assert "、" not in filmcraft_kb._CLAUSE_BOUNDARIES
    assert filmcraft_kb._ENUMERATION_SEPARATORS == ("、",)


# ―――― G-01-R9：词条最长匹配 + 无介词场景框架 ――――

# 六条被独立评审复现的误报：都是纯禁止，必须判 False 且不带出打斗规则。
# 前两条是景别词被短词吃掉（特写/近景 先于 大特写/中近景），中间一条是列举项
# 武打动作 被 武打 削成 动作，后三条是无介词场景框架（镜头里/画面里/场面）。
_R9_NO_PREPOSITION_PROHIBITIONS = (
    "不要大特写打斗",
    "不要中近景打斗",
    "不要打斗、武打动作、三人群殴",
    "不要镜头里出现打斗",
    "不要画面里出现打斗",
    "不要场面打斗",
)

# 明确打斗仍是明确打斗：R7/R8 的读法一条不改。
_R9_CLEAR_COMBAT_KEPT = (
    "没有台词的三镜打斗短片",
    "没有武器的两人对打",
    "不要温情，没有台词的打斗",
)

# 多字脚手架必须整体识别（最长匹配），碎片不得留成实词。
_R9_LONGEST_MATCH_ADJUNCTS = ("大特写", "中近景")

# 无介词场景框架：场景名词 + 方位后缀 + 可选桥接词，以及裸 场面。
_R9_SCENE_FRAMES = ("镜头里", "镜头里出现", "画面里", "画面里出现", "场面")

# 有界性对照：内容名词本身、超出框架长度的整句、以及内含场景名词的词条。
_R9_NOT_SCENE_FRAMES = ("镜头", "画面", "台词", "慢动作")
_R9_SCENE_FRAME_TOO_LONG = ("镜头里出现打斗", "画面里出现一群人打斗")


@pytest.mark.parametrize("wording", _R9_NO_PREPOSITION_PROHIBITIONS)
def test_r9_prohibition_suppresses_the_cue_and_the_storyboard_rule(wording: str) -> None:
    """R9：六条复现全部判 False，且分镜合同不带出 `craft.action_fragment_shots.v1`。"""

    from novelvideo.services.production_contracts import has_filmcraft_action_context
    from novelvideo.workflow_runtime.storyboard_prompt import (
        build_storyboard_filmcraft_contract,
    )

    assert has_filmcraft_action_context(wording) is False, wording
    assert _R6_ACTION_RULE_ID not in build_storyboard_filmcraft_contract(wording), wording
    assert _R6_ACTION_RULE_ID not in _r6_facade_injection(wording), wording
    assert _R6_ACTION_RULE_ID not in _r6_direct_injection(wording), wording


@pytest.mark.parametrize("wording", _R9_NO_PREPOSITION_PROHIBITIONS)
def test_r9_every_cue_occurrence_in_a_prohibition_is_negated(wording: str) -> None:
    """R9：单元级钉法——禁止项里出现的每个词条都被 `_negated_action_cue` 判为已否定。"""

    from novelvideo.production import filmcraft_kb

    lowered = wording.casefold()
    found = False
    for cue in filmcraft_kb._ACTION_CONTEXT_CUES:
        needle = cue.casefold()
        start = lowered.find(needle)
        while start >= 0:
            found = True
            assert filmcraft_kb._negated_action_cue(lowered, start) is True, (
                wording,
                cue,
            )
            start = lowered.find(needle, start + 1)
    assert found, wording


@pytest.mark.parametrize("adjunct", _R9_LONGEST_MATCH_ADJUNCTS)
def test_r9_longest_match_keeps_multi_character_adjuncts_whole(adjunct: str) -> None:
    """R9：脚手架整段删除，短词不再先把长词吃掉一半。

    修复前实跑：`_strip_negation_scaffolding("大特写")` 返回 `"大"`、
    `("中近景")` 返回 `"中"`，残留被当成实词，纯禁止因此判 `True`。
    """

    from novelvideo.production import filmcraft_kb

    assert filmcraft_kb._strip_negation_scaffolding(adjunct) == "", adjunct
    assert filmcraft_kb._only_bridge_tokens(adjunct) is True, adjunct


def test_r9_longest_match_applies_to_cue_removal_too() -> None:
    """R9：列举项的词条删除同样最长匹配——`武打动作` 先于 `武打`。"""

    from novelvideo.production import filmcraft_kb

    assert "武打动作" in filmcraft_kb._ACTION_CONTEXT_CUES
    assert "武打" in filmcraft_kb._ACTION_CONTEXT_CUES
    # 短词在表里排在长词前面：只有最长匹配才能把整条列举项删干净。
    assert filmcraft_kb._ACTION_CONTEXT_CUES.index("武打") < (
        filmcraft_kb._ACTION_CONTEXT_CUES.index("武打动作")
    )
    assert filmcraft_kb._strip_tokens("武打动作", filmcraft_kb._ACTION_CONTEXT_CUES) == ""
    assert filmcraft_kb._only_bridge_tokens_or_cues("武打动作") is True


@pytest.mark.parametrize("frame", _R9_SCENE_FRAMES)
def test_r9_no_preposition_scene_frame_is_scaffolding(frame: str) -> None:
    """R9：无介词场景框架（`镜头里`/`画面里出现`/裸 `场面`）按脚手架放行。"""

    from novelvideo.production import filmcraft_kb

    assert filmcraft_kb._only_bridge_tokens(frame) is True, frame


@pytest.mark.parametrize("text", _R9_NOT_SCENE_FRAMES)
def test_r9_content_nouns_are_still_not_scaffolding(text: str) -> None:
    """R9：内容名词没有回到全局桥接词表，裸 `画面`/`镜头` 仍是实词。"""

    from novelvideo.production import filmcraft_kb

    assert filmcraft_kb._only_bridge_tokens(text) is False, text


@pytest.mark.parametrize("text", _R9_SCENE_FRAME_TOO_LONG)
def test_r9_scene_frame_is_bounded(text: str) -> None:
    """R9：场景框架有定长上限，不能吞掉整个小句或任意名词短语。"""

    from novelvideo.production import filmcraft_kb

    assert filmcraft_kb._only_bridge_tokens(text) is False, text
    assert filmcraft_kb._NEGATION_SCENE_FRAME_MAX <= 5


def test_r9_scene_nouns_stay_out_of_the_bridge_table() -> None:
    """R9：场景名词只在结构判据里放行，没有被重新写进全局桥接词表。"""

    from novelvideo.production import filmcraft_kb

    for token in ("镜头", "画面", "场面", "里", "中"):
        assert token not in filmcraft_kb._NEGATION_BRIDGE_TOKENS, token
    # 场景名词是词条的一部分时，词条必须保持可读（不得被框架削掉半截）。
    assert filmcraft_kb._only_bridge_tokens_or_cues("动作场面") is True
    assert filmcraft_kb._strip_negation_scaffolding("动作场面") == "动作场面"


@pytest.mark.parametrize("wording", _R9_CLEAR_COMBAT_KEPT)
def test_r9_clear_combat_keeps_the_rule(wording: str) -> None:
    """R9：无介词框架没有误伤明确打斗——三条仍判 True 并带出打斗规则。"""

    from novelvideo.services.production_contracts import has_filmcraft_action_context

    assert has_filmcraft_action_context(wording) is True, wording
    assert _r8_rule_present(wording) is True, wording


def test_r9_warm_ordinary_stays_without_the_rule() -> None:
    """R9：与打斗无关的温情描述仍判 False，不带出打斗规则。"""

    from novelvideo.services.production_contracts import has_filmcraft_action_context

    assert has_filmcraft_action_context("温情短片") is False
    assert _r8_rule_present("温情短片") is False
