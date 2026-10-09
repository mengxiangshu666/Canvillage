from __future__ import annotations

from types import SimpleNamespace

import pytest

from novelvideo.models import NovelScene

ENRICHED_ENVIRONMENT_PROMPT = "正面：临街玻璃窗\n左侧：咖啡吧台\n右侧：木质书架\n背面：入口木门"


class _FakeSQLiteStore:
    def __init__(self, scenes: list[NovelScene] | None = None):
        self.scenes = {scene.name: scene for scene in scenes or []}
        self.added: list[NovelScene] = []
        self.updated: list[tuple[str, dict]] = []

    async def get_scene(self, name: str):
        return self.scenes.get(name)

    async def list_scenes(self):
        return list(self.scenes.values())

    async def add_scene(self, scene: NovelScene):
        self.added.append(scene)
        self.scenes[scene.name] = scene

    async def update_scene(self, name: str, **updates):
        self.updated.append((name, updates))
        scene = self.scenes[name]
        for key, value in updates.items():
            setattr(scene, key, value)
        return True


class _FakeCogneeStore:
    def __init__(
        self,
        scenes: list[NovelScene] | None = None,
        *,
        raw_content: str = "",
        project_dir: str = "",
    ):
        self.sqlite_store = _FakeSQLiteStore(scenes)
        self.project_dir = project_dir
        self.raw_content = raw_content
        self.updated: list[tuple[int, dict]] = []

    async def load_episode_content(self, ep_num: int):
        return self.raw_content

    async def update_episode(self, episode_number: int, **updates):
        self.updated.append((episode_number, updates))
        return None


def _block(location: str = "咖啡馆", time_of_day: str = "夜"):
    return SimpleNamespace(
        location=location,
        interior_exterior="内",
        time_of_day=time_of_day,
        characters=["陆辰", "沈月白"],
        header_line=f"场次（1）地点：{location}，{time_of_day}，内；出场人物：陆辰、沈月白",
        lines=[
            "窗外暴雨如注，雨滴重重砸在玻璃上。",
            "咖啡馆内只有他们这一桌，昏黄的灯光将两人的影子拉得很长。",
            "沈月白将自己的手机推到桌子中央。",
        ],
    )


@pytest.mark.asyncio
async def test_compile_episode_scenes_reconciles_base_scene_before_planning(monkeypatch):
    import novelvideo.agents.asset_compiler as asset_compiler

    async def fake_enrich(**kwargs):
        return NovelScene(
            name=kwargs["scene_name"],
            aliases=[],
            scene_type=kwargs["scene_type"],
            environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
            description="雨夜咖啡馆",
        )

    async def fake_derived(self, scene_name, block):
        return []

    async def fake_reconcile(self, source_text, episode, log):
        scene = await fake_enrich(
            scene_name="咖啡馆",
            scene_type="interior",
            context_lines=["窗外暴雨如注。"],
        )
        await self.cognee_store.sqlite_store.add_scene(scene)
        log("  AI补全基础场景: 咖啡馆")
        return ["咖啡馆"]

    async def fake_load_scene_blocks(self, episode):
        return [_block()]

    monkeypatch.setattr(asset_compiler, "enrich_scene_environment_from_context", fake_enrich)
    monkeypatch.setattr(asset_compiler.AssetCompiler, "_analyze_derived_scenes", fake_derived)
    monkeypatch.setattr(asset_compiler.AssetCompiler, "_load_scene_blocks", fake_load_scene_blocks)
    monkeypatch.setattr(
        asset_compiler.AssetCompiler,
        "_reconcile_base_scenes_from_text",
        fake_reconcile,
    )

    store = _FakeCogneeStore()
    compiler = asset_compiler.AssetCompiler(store)

    scene_menu, new_count = await compiler.compile_episode_scenes(
        SimpleNamespace(number=1, title="第一集", beat_source_text="场次（1）地点：咖啡馆"),
        lambda _message: None,
    )

    assert scene_menu[0].scene_id == "咖啡馆"
    assert new_count == 0
    assert [scene.name for scene in store.sqlite_store.added] == ["咖啡馆"]
    assert store.sqlite_store.scenes["咖啡馆"].environment_prompt.startswith("正面：临街玻璃窗")


@pytest.mark.asyncio
async def test_compile_episode_scenes_backfills_existing_empty_scene_prompt(monkeypatch):
    import novelvideo.agents.asset_compiler as asset_compiler

    existing = NovelScene(name="咖啡馆", scene_type="interior", environment_prompt="")

    async def fake_enrich(**kwargs):
        return NovelScene(
            name=kwargs["scene_name"],
            aliases=[],
            scene_type="interior",
            environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
            description="雨夜咖啡馆",
        )

    async def fake_derived(self, scene_name, block):
        return []

    monkeypatch.setattr(asset_compiler, "enrich_scene_environment_from_context", fake_enrich)
    monkeypatch.setattr(asset_compiler.AssetCompiler, "_analyze_derived_scenes", fake_derived)

    store = _FakeCogneeStore([existing])
    compiler = asset_compiler.AssetCompiler(store)

    _scene_menu, pending_scenes = await compiler._compile_scenes(
        [_block()],
        SimpleNamespace(number=1),
        lambda _message: None,
    )

    assert pending_scenes == []
    assert store.sqlite_store.updated == [
        (
            "咖啡馆",
            {
                "scene_type": "interior",
                "environment_prompt": ENRICHED_ENVIRONMENT_PROMPT,
                "description": "雨夜咖啡馆",
            },
        )
    ]
    assert existing.environment_prompt.startswith("正面：临街玻璃窗")


@pytest.mark.asyncio
async def test_narrated_recompile_appends_evidence_without_touching_scene(monkeypatch):
    import novelvideo.agents.asset_compiler as asset_compiler

    existing = NovelScene(
        name="正魔边境",
        scene_type="exterior",
        environment_prompt="已有场景画面",
        description="已有描述",
        notes="observed_times: 夜晚×1",
    )

    async def fake_extract(self, source_text, episode, log):
        return [
            NovelScene(
                name="正魔边境",
                scene_type="exterior",
                environment_prompt="新跑出来的画面，不该覆盖",
                notes="原文证据：边境上狼群已经围上来",
            )
        ]

    monkeypatch.setattr(
        asset_compiler.AssetCompiler, "_extract_narrated_episode_scenes", fake_extract
    )

    store = _FakeCogneeStore([existing])
    compiler = asset_compiler.AssetCompiler(store)

    scene_menu, pending_scenes = await compiler._compile_narrated_scenes(
        "边境上狼群已经围上来。",
        SimpleNamespace(number=1),
        lambda _message: None,
    )

    assert [item.scene_id for item in scene_menu] == ["正魔边境"]
    assert pending_scenes == []
    assert existing.environment_prompt == "已有场景画面"
    assert existing.description == "已有描述"
    assert existing.notes == "observed_times: 夜晚×1\n原文证据：边境上狼群已经围上来"


@pytest.mark.asyncio
async def test_headerless_blocks_still_get_narrated_scenes(monkeypatch):
    """有场景但没有地点的场次时，不许把本集场景清单收成只剩有地点的那一个。"""

    import novelvideo.agents.asset_compiler as asset_compiler

    async def fake_narrated(self, source_text, episode, log):
        from novelvideo.models import SceneMenuItem as Menu

        return [Menu(scene_id="落凡宗")], []

    monkeypatch.setattr(
        asset_compiler.AssetCompiler, "_compile_narrated_scenes", fake_narrated
    )

    located = SimpleNamespace(location="正魔边境")
    headerless = SimpleNamespace(location="")
    store = _FakeCogneeStore()
    compiler = asset_compiler.AssetCompiler(store)

    scene_menu, pending = await compiler._fill_headerless_scenes(
        [located, headerless],
        [type("Menu", (), {"scene_id": "正魔边境"})()],
        [],
        "原文",
        SimpleNamespace(number=1),
        lambda _m: None,
    )

    assert [item.scene_id for item in scene_menu] == ["正魔边境", "落凡宗"]
    assert pending == []

    all_located_menu, _ = await compiler._fill_headerless_scenes(
        [located],
        [type("Menu", (), {"scene_id": "正魔边境"})()],
        [],
        "原文",
        SimpleNamespace(number=1),
        lambda _m: None,
    )
    assert [item.scene_id for item in all_located_menu] == ["正魔边境"]


@pytest.mark.asyncio
async def test_compile_episode_scenes_keeps_existing_prompt(monkeypatch):
    import novelvideo.agents.asset_compiler as asset_compiler

    existing = NovelScene(
        name="咖啡馆",
        scene_type="interior",
        environment_prompt="已有完整空间合同",
        description="已有描述",
    )
    enrich_calls = []

    async def fake_enrich(**kwargs):
        enrich_calls.append(kwargs)
        return NovelScene(name=kwargs["scene_name"], environment_prompt="不应使用")

    async def fake_derived(self, scene_name, block):
        return []

    monkeypatch.setattr(asset_compiler, "enrich_scene_environment_from_context", fake_enrich)
    monkeypatch.setattr(asset_compiler.AssetCompiler, "_analyze_derived_scenes", fake_derived)

    store = _FakeCogneeStore([existing])
    compiler = asset_compiler.AssetCompiler(store)

    _scene_menu, pending_scenes = await compiler._compile_scenes(
        [_block()],
        SimpleNamespace(number=1),
        lambda _message: None,
    )

    assert pending_scenes == []
    assert store.sqlite_store.updated == []
    assert enrich_calls == []
    assert existing.environment_prompt == "已有完整空间合同"


@pytest.mark.asyncio
async def test_compile_episode_scenes_creates_empty_time_plate_slot_for_repeated_time(
    monkeypatch,
):
    import novelvideo.agents.asset_compiler as asset_compiler

    async def fake_enrich(**kwargs):
        return NovelScene(
            name=kwargs["scene_name"],
            aliases=[],
            scene_type="interior",
            environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
            description="咖啡馆",
        )

    async def fake_derived(self, scene_name, block):
        return []

    monkeypatch.setattr(asset_compiler, "enrich_scene_environment_from_context", fake_enrich)
    monkeypatch.setattr(asset_compiler.AssetCompiler, "_analyze_derived_scenes", fake_derived)

    store = _FakeCogneeStore(
        [
            NovelScene(
                name="咖啡馆",
                scene_type="interior",
                environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
                description="咖啡馆",
            )
        ]
    )
    compiler = asset_compiler.AssetCompiler(store)

    scene_menu, pending_scenes = await compiler._compile_scenes(
        [_block(time_of_day="夜"), _block(time_of_day="夜")],
        SimpleNamespace(number=1),
        lambda _message: None,
    )

    assert [item.scene_id for item in scene_menu] == ["咖啡馆", "咖啡馆_夜晚"]
    assert scene_menu[1].base_scene_id == "咖啡馆"
    assert scene_menu[1].variant_id == ""
    assert scene_menu[1].time_of_day == "夜晚"
    time_plate = next(scene for scene in pending_scenes if scene.name == "咖啡馆_夜晚")
    assert time_plate.base_scene_id == "咖啡馆"
    assert time_plate.variant_id == ""
    assert time_plate.time_of_day == "夜晚"
    assert "空 plate 槽位" in time_plate.notes


@pytest.mark.asyncio
async def test_compile_episode_scenes_uses_narrated_fallback_without_scene_headers(
    monkeypatch, tmp_path
):
    import novelvideo.agents.asset_compiler as asset_compiler

    async def fake_extract(self, source_text, episode, log):
        assert "医院走廊" in source_text
        return [
            NovelScene(
                name="医院走廊",
                scene_type="interior",
                environment_prompt="正面：护士站与急诊指示牌\n左侧：病房门\n右侧：候诊椅\n背面：电梯间",
                description="急诊楼医院走廊",
            )
        ]

    async def fake_reconcile(self, source_text, episode, log):
        return []

    monkeypatch.setattr(
        asset_compiler.AssetCompiler,
        "_extract_narrated_episode_scenes",
        fake_extract,
        raising=False,
    )
    monkeypatch.setattr(
        asset_compiler.AssetCompiler,
        "_reconcile_base_scenes_from_text",
        fake_reconcile,
    )

    store = _FakeCogneeStore(
        raw_content="林晚冲进医院走廊，护士站前的灯牌闪烁。",
        project_dir=str(tmp_path),
    )
    compiler = asset_compiler.AssetCompiler(store)
    episode = SimpleNamespace(number=1, title="第一集", beat_source_text="")

    scene_menu, new_count = await compiler.compile_episode_scenes(episode, lambda _message: None)

    assert new_count == 1
    assert scene_menu[0].scene_id == "医院走廊"
    assert store.sqlite_store.added[0].name == "医院走廊"
    assert store.updated == [(1, {"scene_menu": scene_menu})]


@pytest.mark.asyncio
async def test_compile_scenes_promotes_stable_visual_states_to_pending_scenes(monkeypatch):
    import novelvideo.agents.asset_compiler as asset_compiler

    async def fake_enrich(**kwargs):
        return NovelScene(
            name=kwargs["scene_name"],
            scene_type=kwargs["scene_type"],
            environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
            description="雨夜咖啡馆",
        )

    async def fake_derived(self, scene_name, block):
        return [
            asset_compiler.DerivedSceneRequirement(
                label="暴雨版",
                description="窗外暴雨，玻璃挂满水痕",
                lighting="昏黄灯光",
                atmosphere="潮湿雨夜空气",
            )
        ]

    monkeypatch.setattr(asset_compiler, "enrich_scene_environment_from_context", fake_enrich)
    monkeypatch.setattr(asset_compiler.AssetCompiler, "_analyze_derived_scenes", fake_derived)

    store = _FakeCogneeStore(
        [
            NovelScene(
                name="咖啡馆",
                scene_type="interior",
                environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
                description="雨夜咖啡馆",
            )
        ]
    )
    compiler = asset_compiler.AssetCompiler(store)

    scene_menu, pending_scenes = await compiler._compile_scenes(
        [_block()],
        SimpleNamespace(number=1),
        lambda _message: None,
    )

    assert [item.scene_id for item in scene_menu] == ["咖啡馆", "咖啡馆_暴雨版"]
    assert scene_menu[1].base_scene_id == "咖啡馆"
    assert scene_menu[1].variant_id == "暴雨版"
    assert [scene.name for scene in pending_scenes] == ["咖啡馆_暴雨版"]
    derived_scene = pending_scenes[0]
    assert derived_scene.name == "咖啡馆_暴雨版"
    assert not hasattr(derived_scene, "base_scene")
    assert derived_scene.aliases == ["咖啡馆"]
    assert derived_scene.environment_prompt == ""
    assert derived_scene.description == "窗外暴雨，玻璃挂满水痕"
    assert "窗外暴雨，玻璃挂满水痕" in derived_scene.variant_prompt
    assert "昏黄灯光" in derived_scene.variant_prompt
    assert "潮湿雨夜空气" in derived_scene.variant_prompt
    assert "正面：临街玻璃窗" not in derived_scene.variant_prompt


@pytest.mark.asyncio
async def test_compile_scenes_reuses_existing_derived_scene(monkeypatch):
    import novelvideo.agents.asset_compiler as asset_compiler

    async def fake_derived(self, scene_name, block):
        return [
            asset_compiler.DerivedSceneRequirement(
                label="暴雨版",
                description="窗外暴雨，玻璃挂满水痕",
                lighting="昏黄灯光",
                atmosphere="潮湿雨夜空气",
            )
        ]

    monkeypatch.setattr(asset_compiler.AssetCompiler, "_analyze_derived_scenes", fake_derived)

    store = _FakeCogneeStore(
        [
            NovelScene(
                name="咖啡馆",
                scene_type="interior",
                environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
            ),
            NovelScene(
                name="咖啡馆_暴雨版",
                scene_type="interior",
                base_scene_id="咖啡馆",
                variant_id="暴雨版",
                variant_prompt="已有暴雨版",
            ),
        ]
    )
    compiler = asset_compiler.AssetCompiler(store)

    scene_menu, pending_scenes = await compiler._compile_scenes(
        [_block()],
        SimpleNamespace(number=1),
        lambda _message: None,
    )

    assert [item.scene_id for item in scene_menu] == ["咖啡馆", "咖啡馆_暴雨版"]
    assert pending_scenes == []


@pytest.mark.asyncio
async def test_compile_episode_scenes_persists_base_and_derived_as_normal_scenes(
    monkeypatch, tmp_path
):
    import novelvideo.agents.asset_compiler as asset_compiler

    async def fake_load_scene_blocks(self, episode):
        return [_block()]

    async def fake_enrich(**kwargs):
        return NovelScene(
            name=kwargs["scene_name"],
            scene_type=kwargs["scene_type"],
            environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
            description="雨夜咖啡馆",
        )

    async def fake_derived(self, scene_name, block):
        return [
            asset_compiler.DerivedSceneRequirement(
                label="暴雨版",
                description="窗外暴雨，玻璃挂满水痕",
                lighting="昏黄灯光",
                atmosphere="潮湿雨夜空气",
            )
        ]

    async def fake_reconcile(self, source_text, episode, log):
        scene = await fake_enrich(
            scene_name="咖啡馆",
            scene_type="interior",
            context_lines=["窗外暴雨如注。"],
        )
        await self.cognee_store.sqlite_store.add_scene(scene)
        return ["咖啡馆"]

    monkeypatch.setattr(asset_compiler.AssetCompiler, "_load_scene_blocks", fake_load_scene_blocks)
    monkeypatch.setattr(asset_compiler, "enrich_scene_environment_from_context", fake_enrich)
    monkeypatch.setattr(asset_compiler.AssetCompiler, "_analyze_derived_scenes", fake_derived)
    monkeypatch.setattr(
        asset_compiler.AssetCompiler,
        "_reconcile_base_scenes_from_text",
        fake_reconcile,
    )

    store = _FakeCogneeStore(project_dir=str(tmp_path))
    compiler = asset_compiler.AssetCompiler(store)
    scene_menu, new_count = await compiler.compile_episode_scenes(
        SimpleNamespace(number=1, title="第一集"),
        lambda _message: None,
    )

    assert new_count == 1
    assert [scene.name for scene in store.sqlite_store.added] == ["咖啡馆", "咖啡馆_暴雨版"]
    assert [item.scene_id for item in scene_menu] == ["咖啡馆", "咖啡馆_暴雨版"]
    assert scene_menu[1].base_scene_id == "咖啡馆"
    assert scene_menu[1].variant_id == "暴雨版"
    assert store.updated == [(1, {"scene_menu": scene_menu})]


@pytest.mark.asyncio
async def test_base_scene_reconcile_does_not_create_existing_alias():
    import novelvideo.agents.asset_compiler as asset_compiler

    store = _FakeCogneeStore(
        [
            NovelScene(
                name="医院走廊",
                aliases=["急诊走廊"],
                scene_type="interior",
                environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
            )
        ]
    )
    compiler = asset_compiler.AssetCompiler(store)

    created = await compiler._apply_base_scene_reconcile_output(
        asset_compiler.EpisodeBaseSceneReconcileOutput(
            scenes=[
                asset_compiler.BaseSceneReconcileDecision(
                    action="create",
                    scene_name="急诊走廊",
                    scene_type="interior",
                    evidence_lines=["急诊走廊里灯牌闪烁。"],
                )
            ]
        ),
        "急诊走廊里灯牌闪烁。",
        SimpleNamespace(number=1),
        lambda _message: None,
    )

    assert created == []
    assert sorted(store.sqlite_store.scenes) == ["医院走廊"]


@pytest.mark.asyncio
async def test_find_matching_scene_treats_independent_underscore_scene_as_base_candidate():
    import novelvideo.agents.asset_compiler as asset_compiler

    store = _FakeCogneeStore(
        [
            NovelScene(name="地下", aliases=[], scene_type="interior"),
            NovelScene(name="地下_主控室", aliases=["主控室"], scene_type="interior"),
            NovelScene(
                name="地下_漏水",
                base_scene_id="地下",
                variant_id="漏水",
                scene_type="interior",
            ),
        ]
    )
    compiler = asset_compiler.AssetCompiler(store)

    matched = await compiler._find_matching_scene("主控室")

    assert matched is not None
    assert matched.name == "地下_主控室"


def test_derived_scene_normalization_filters_plain_time_but_keeps_stable_light_plate():
    import novelvideo.agents.asset_compiler as asset_compiler

    normalized = asset_compiler.AssetCompiler._build_derived_scene_specs(
        [
            asset_compiler.DerivedSceneRequirement(label="夜晚", description="普通夜间时段"),
            asset_compiler.DerivedSceneRequirement(
                label="暴雨夜霓虹版",
                description="暴雨夜晚，霓虹灯在积水中反射",
                lighting="高对比霓虹反光",
                atmosphere="雨幕和湿冷空气",
            ),
            asset_compiler.DerivedSceneRequirement(label="特写", description="镜头语言"),
        ]
    )

    assert [item.label for item in normalized] == ["暴雨夜霓虹版"]


def _reconcile_output(*decisions):
    import novelvideo.agents.asset_compiler as asset_compiler

    return asset_compiler.EpisodeBaseSceneReconcileOutput(
        scenes=[
            asset_compiler.BaseSceneReconcileDecision(**decision)
            for decision in decisions
        ]
    )


def _patch_jev(monkeypatch, handler):
    """把 asset_compiler 里的 JEV 判断换成给定实现，返回收到的调用记录。"""

    from novelvideo.services import judgment as jev

    calls: list[dict] = []

    def fake_ask(state, questions, **kwargs):
        calls.append({"state": state, "questions": questions})
        return handler(questions)

    monkeypatch.setattr(jev, "ask_questions", fake_ask)
    return calls


def _patch_enrich(monkeypatch):
    """记录被真正新建的场景名，避免真去调文本模型。"""

    import novelvideo.agents.asset_compiler as asset_compiler

    enriched: list[str] = []

    async def fake_enrich(**kwargs):
        enriched.append(kwargs["scene_name"])
        return NovelScene(
            name=kwargs["scene_name"],
            aliases=[],
            scene_type=kwargs.get("scene_type") or "interior",
            environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
        )

    monkeypatch.setattr(asset_compiler, "enrich_scene_environment_from_context", fake_enrich)
    return enriched


@pytest.mark.asyncio
async def test_reconcile_registers_new_call_name_as_alias_when_jev_says_same_place(monkeypatch):
    """「急诊部走廊」在已有「医院走廊」时不该新建，而应登记成别名。"""

    import novelvideo.agents.asset_compiler as asset_compiler
    from novelvideo.services import judgment as jev

    store = _FakeCogneeStore(
        [NovelScene(name="医院走廊", aliases=[], scene_type="interior")]
    )
    compiler = asset_compiler.AssetCompiler(store)
    enriched = _patch_enrich(monkeypatch)
    _patch_jev(
        monkeypatch,
        lambda questions: {
            name: jev.ChoiceAnswer(choice="医院走廊", confidence=0.93, probabilities={})
            for name in questions
        },
    )

    created = await compiler._apply_base_scene_reconcile_output(
        _reconcile_output(
            {
                "action": "create",
                "scene_name": "急诊部走廊",
                "scene_type": "interior",
                "evidence_lines": ["急诊部走廊里灯牌闪烁。"],
            }
        ),
        "急诊部走廊里灯牌闪烁。",
        SimpleNamespace(number=1),
        lambda _message: None,
    )

    assert created == []
    assert enriched == []
    assert store.sqlite_store.scenes["医院走廊"].aliases == ["急诊部走廊"]


@pytest.mark.asyncio
async def test_reconcile_still_creates_when_jev_says_not_same_place(monkeypatch):
    import novelvideo.agents.asset_compiler as asset_compiler
    from novelvideo.services import judgment as jev

    store = _FakeCogneeStore(
        [NovelScene(name="医院走廊", aliases=[], scene_type="interior")]
    )
    compiler = asset_compiler.AssetCompiler(store)
    enriched = _patch_enrich(monkeypatch)
    _patch_jev(
        monkeypatch,
        lambda questions: {
            name: jev.ChoiceAnswer(
                choice="以上都不是（应当新建基础场景）",
                confidence=0.97,
                probabilities={},
            )
            for name in questions
        },
    )

    created = await compiler._apply_base_scene_reconcile_output(
        _reconcile_output(
            {
                "action": "create",
                "scene_name": "地下停车场",
                "scene_type": "interior",
                "evidence_lines": ["地下停车场里只剩几辆车。"],
            }
        ),
        "地下停车场里只剩几辆车。",
        SimpleNamespace(number=1),
        lambda _message: None,
    )

    assert created == ["地下停车场"]
    assert enriched == ["地下停车场"]


@pytest.mark.asyncio
async def test_reconcile_falls_back_to_create_when_jev_is_undecided(monkeypatch):
    """置信度不足＝拿不准，必须退回原来的新建行为，不能凭低置信判断改数据。"""

    import novelvideo.agents.asset_compiler as asset_compiler
    from novelvideo.services import judgment as jev

    store = _FakeCogneeStore(
        [NovelScene(name="医院走廊", aliases=[], scene_type="interior")]
    )
    compiler = asset_compiler.AssetCompiler(store)
    enriched = _patch_enrich(monkeypatch)
    _patch_jev(
        monkeypatch,
        lambda questions: {
            name: jev.ChoiceAnswer(choice="医院走廊", confidence=0.41, probabilities={})
            for name in questions
        },
    )

    created = await compiler._apply_base_scene_reconcile_output(
        _reconcile_output(
            {
                "action": "create",
                "scene_name": "急诊部走廊",
                "scene_type": "interior",
                "evidence_lines": ["急诊部走廊里灯牌闪烁。"],
            }
        ),
        "急诊部走廊里灯牌闪烁。",
        SimpleNamespace(number=1),
        lambda _message: None,
    )

    assert created == ["急诊部走廊"]
    assert enriched == ["急诊部走廊"]
    assert store.sqlite_store.scenes["医院走廊"].aliases == []


@pytest.mark.asyncio
async def test_reconcile_creates_normally_when_jev_unavailable(monkeypatch):
    """JEV 失败（没配密钥/网络不通）不得改变原有行为。"""

    import novelvideo.agents.asset_compiler as asset_compiler
    from novelvideo.services import judgment as jev

    store = _FakeCogneeStore(
        [NovelScene(name="医院走廊", aliases=[], scene_type="interior")]
    )
    compiler = asset_compiler.AssetCompiler(store)
    enriched = _patch_enrich(monkeypatch)

    def boom(state, questions, **kwargs):
        raise jev.JudgmentError("JEV 判断失败：连接不上判断服务")

    monkeypatch.setattr(jev, "ask_questions", boom)

    created = await compiler._apply_base_scene_reconcile_output(
        _reconcile_output(
            {
                "action": "create",
                "scene_name": "急诊部走廊",
                "scene_type": "interior",
                "evidence_lines": ["急诊部走廊里灯牌闪烁。"],
            }
        ),
        "急诊部走廊里灯牌闪烁。",
        SimpleNamespace(number=1),
        lambda _message: None,
    )

    assert created == ["急诊部走廊"]
    assert enriched == ["急诊部走廊"]


@pytest.mark.asyncio
async def test_reconcile_skips_jev_when_no_existing_base_scene(monkeypatch):
    """没有已有场景时没什么可匹配的，不该白花一次判断。"""

    import novelvideo.agents.asset_compiler as asset_compiler

    store = _FakeCogneeStore([])
    compiler = asset_compiler.AssetCompiler(store)
    enriched = _patch_enrich(monkeypatch)
    calls = _patch_jev(monkeypatch, lambda questions: {})

    created = await compiler._apply_base_scene_reconcile_output(
        _reconcile_output(
            {
                "action": "create",
                "scene_name": "咖啡馆",
                "scene_type": "interior",
                "evidence_lines": ["咖啡馆内只有他们这一桌。"],
            }
        ),
        "咖啡馆内只有他们这一桌。",
        SimpleNamespace(number=1),
        lambda _message: None,
    )

    assert created == ["咖啡馆"]
    assert enriched == ["咖啡馆"]
    assert calls == []
