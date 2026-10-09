from __future__ import annotations

import json
from pathlib import Path

import pytest

from novelvideo.agents.beat_shot_contracts import (
    SEAM_CONTINUOUS,
    SEAM_CUT,
    compile_beat_contracts,
    decode_planned_fields,
    ensure_beat_shot_contracts,
    plan_beat_shot_contracts,
)
from novelvideo.production.shot_contract import shot_contract_ready


def _beat(number: int, **overrides) -> dict:
    beat = {
        "beat_number": number,
        "visual_description": f"第{number}镜：人物向前推进，动作完成。",
        "narrative_function": "action",
        "pace": "medium",
        "target_duration_seconds": 4.0,
        "detected_identities": ["厉无赦", "应苍极"],
        "scene_ref": {"scene_id": "钧天圣殿"},
    }
    beat.update(overrides)
    return beat


def _planned(**fields) -> dict:
    return {key: value for key, value in fields.items() if value is not None}


def _episode() -> list[dict]:
    return [
        _beat(1, visual_description="厉无赦抬剑，剑锋指向应苍极。"),
        _beat(2, visual_description="厉无赦向前踏出一步，剑锋压下。"),
        _beat(3, visual_description="应苍极横剑格挡，火星四溅。"),
    ]


def _full_plan() -> dict:
    return {
        1: _planned(
            subject="厉无赦",
            primary_action="厉无赦抬剑指向应苍极",
            camera_motion="推近",
            start_state="厉无赦站在殿心，剑在身侧",
            end_state="剑锋停在应苍极胸前一尺",
            seam=SEAM_CONTINUOUS,
        ),
        2: _planned(
            subject="厉无赦",
            primary_action="厉无赦向前踏出一步并压下剑锋",
            camera_motion="跟拍",
            start_state="两人对峙，剑锋悬在半空",
            end_state="剑锋压到应苍极肩前",
            seam=SEAM_CUT,
        ),
        3: _planned(
            subject="应苍极",
            primary_action="应苍极横剑格挡并震开剑锋",
            camera_motion="摇镜",
            start_state="应苍极侧身，长剑横在胸前",
            end_state="两剑相交，火星溅向画面右侧",
            seam=SEAM_CUT,
        ),
    }


def test_compile_closes_continuous_seam_and_every_contract_is_ready() -> None:
    beats = _episode()

    plan = compile_beat_contracts(beats, planned=_full_plan())

    assert sorted(plan.contracts) == [1, 2, 3]
    assert all(shot_contract_ready(contract) for contract in plan.contracts.values())
    # 接缝账本：连续接缝的下一镜起始状态逐字等于上一镜结束状态。
    assert plan.contracts[1]["end_state"] == "剑锋停在应苍极胸前一尺"
    assert plan.contracts[2]["start_state"] == plan.contracts[1]["end_state"]
    assert plan.contracts[2]["continuity_in"]["seam"] == SEAM_CONTINUOUS
    assert plan.contracts[2]["continuity_out"]["to_beat"] == 3
    # 硬切不继承上一镜结束状态，保留自己的起始状态。
    assert plan.contracts[3]["start_state"] == "应苍极侧身，长剑横在胸前"
    assert plan.contracts[3]["continuity_in"]["seam"] == SEAM_CUT


def test_runner_reads_handoff_seam_from_persisted_contract() -> None:
    from novelvideo.task_backend.runners.video import _contract_handoff_seam

    contract = compile_beat_contracts(_episode(), planned=_full_plan()).contracts[1]

    assert _contract_handoff_seam({"shot_contract": contract}) == SEAM_CONTINUOUS
    assert _contract_handoff_seam({"shot_contract_json": json.dumps(contract)}) == (
        SEAM_CONTINUOUS
    )
    # 没有合同的旧 Beat 与坏 JSON 一律返回空，保持原 first_frame 行为。
    assert _contract_handoff_seam({"beat_number": 1}) == ""
    assert _contract_handoff_seam({"shot_contract_json": "{not json"}) == ""
    assert _contract_handoff_seam(None) == ""


def test_auto_keyframe_reserves_endpoints_for_continuous_non_dialogue_seams() -> None:
    """首尾帧只发两张端点图，因此只给连续接缝的无台词镜头用。"""

    from novelvideo.task_backend.runners.video import _auto_keyframe_eligible

    contract = compile_beat_contracts(_episode(), planned=_full_plan()).contracts[1]
    continuous = {"beat_number": 1, "shot_contract": contract}
    cut_contract = compile_beat_contracts(_episode(), planned=_full_plan()).contracts[3]
    cut = {"beat_number": 3, "shot_contract": cut_contract}
    dialogue = {
        "beat_number": 1,
        "shot_contract": contract,
        "audio_type": "dialogue",
        "dialogue_text": "别回头。",
    }

    assert _auto_keyframe_eligible(
        continuous, contract_ready=True, keyframe_supported=True, has_next_frame=True
    )
    # 硬切：保留 first_frame 才能继续挂人物/场景/道具参考。
    assert not _auto_keyframe_eligible(
        cut, contract_ready=True, keyframe_supported=True, has_next_frame=True
    )
    # 对白镜头：音频通道优先，不被两张端点图挤掉。
    assert not _auto_keyframe_eligible(
        dialogue, contract_ready=True, keyframe_supported=True, has_next_frame=True
    )
    # 合同缺失 / 模型不支持 / 末镜没有下一帧，一律不猜。
    assert not _auto_keyframe_eligible(
        {"beat_number": 1}, contract_ready=False, keyframe_supported=True, has_next_frame=True
    )
    assert not _auto_keyframe_eligible(
        continuous, contract_ready=True, keyframe_supported=False, has_next_frame=True
    )
    assert not _auto_keyframe_eligible(
        continuous, contract_ready=True, keyframe_supported=True, has_next_frame=False
    )


def test_seam_landing_reference_anchors_only_continuous_seams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """多参考模型用「下一镜首帧」当落点锚，硬切绝不注入。"""

    from novelvideo.generators.video_generator import ShotReference
    from novelvideo.task_backend.runners import video as video_runner
    from novelvideo.task_backend.runners.video import (
        SEAM_LANDING_ROLE,
        _append_seam_landing_reference,
    )

    monkeypatch.setattr(
        video_runner,
        "_direct_canvas_modes",
        lambda backend: frozenset({"allReference"}),
    )
    monkeypatch.setattr(video_runner, "direct_video_family", lambda backend: "")
    landing = tmp_path / "beat_03.png"
    landing.write_bytes(b"frame")
    contracts = compile_beat_contracts(_episode(), planned=_full_plan()).contracts

    def anchor(beat: dict, references=(), **overrides):
        kwargs = {
            "beat": beat,
            "landing_path": landing,
            "image_limit": 9,
            "generation_mode": "",
            "backend": "direct_demo",
            "prompt": "镜头从首帧推进。",
            "reference_factory": ShotReference,
        }
        kwargs.update(overrides)
        return _append_seam_landing_reference(list(references), **kwargs)

    references, prompt = anchor({"shot_contract": contracts[1]})

    assert len(references) == 1
    assert references[0].role == SEAM_LANDING_ROLE
    assert references[0].path == str(landing)
    assert "参考图第1张是本镜的落点目标" in prompt

    # 硬切：不注入，避免把下一镜构图拉进本镜。
    assert anchor({"shot_contract": contracts[3]}) == ([], "镜头从首帧推进。")
    # 没有下一镜首帧 / 没有参考位 / 首尾帧互斥模式 / 已经挂过同一张，都不注入。
    assert anchor({"shot_contract": contracts[1]}, landing_path=tmp_path / "none.png")[0] == []
    assert anchor({"shot_contract": contracts[1]}, image_limit=0)[0] == []
    assert anchor({"shot_contract": contracts[1]}, generation_mode="firstLastFrame")[0] == []
    # 已经挂过同一张图时保持原样，不重复注入。
    unchanged = anchor(
        {"shot_contract": contracts[1]},
        references=[ShotReference("image", str(landing), "已有参考")],
    )
    assert len(unchanged[0]) == 1
    assert unchanged[0][0].role == "已有参考"

    # 只声明首帧/首尾帧、没有普通参考通道的后端（MiniMax H3）不注入：
    # 上游会把 first_frame 与参考图混用判成 400 拒单。
    monkeypatch.setattr(
        video_runner,
        "_direct_canvas_modes",
        lambda backend: frozenset({"imageToVideo"}),
    )
    assert anchor({"shot_contract": contracts[1]}) == ([], "镜头从首帧推进。")
    # 能力解析不出来时保持历史形状，不靠猜。
    monkeypatch.setattr(video_runner, "_direct_canvas_modes", lambda backend: frozenset())
    assert anchor({"shot_contract": contracts[1]})[0][0].role == SEAM_LANDING_ROLE
    # H3 这条渠道的能力表写着「支持多图参考」，但适配器实测拒绝首帧混参考
    # （真机 400：first_frame/last_frame cannot be mixed with reference media），
    # 所以家族级的禁令优先于能力表。
    monkeypatch.setattr(
        video_runner,
        "_direct_canvas_modes",
        lambda backend: frozenset({"allReference"}),
    )
    monkeypatch.setattr(
        video_runner, "direct_video_family", lambda backend: "minimax-h3"
    )
    assert anchor({"shot_contract": contracts[1]}) == ([], "镜头从首帧推进。")


def test_seam_reference_promotion_upgrades_only_legacy_continuous_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有首尾帧的直连模型：连续接缝才把首帧请求升级成多图参考请求。"""

    from novelvideo.task_backend.runners import video

    landing = tmp_path / "beat_03.png"
    landing.write_bytes(b"frame")
    contracts = compile_beat_contracts(_episode(), planned=_full_plan()).contracts
    continuous = {"shot_contract": contracts[1]}
    cut = {"shot_contract": contracts[3]}
    monkeypatch.setattr(
        video, "_direct_canvas_modes", lambda backend: frozenset({"allReference"})
    )

    def promote(beat: dict, **overrides) -> str:
        kwargs = {
            "beat": beat,
            "generation_mode": "imageToVideo",
            "backend": "direct_demo",
            "frame_path": str(tmp_path / "beat_02.png"),
            "landing_path": landing,
        }
        kwargs.update(overrides)
        return video._prefer_seam_reference_mode(**kwargs)

    assert promote(continuous) == "allReference"
    # 硬切：不升级，保持历史单帧请求。
    assert promote(cut) == ""
    # 显式选过的模式永远是操作者的决定，不被自动升级覆盖。
    assert promote(continuous, generation_mode="allReference") == ""
    assert promote(continuous, generation_mode="firstLastFrame") == ""
    assert promote(continuous, generation_mode="") == ""
    # 没有首帧 / 没有落点 / 落点文件不存在，都不升级。
    assert promote(continuous, frame_path="") == ""
    assert promote(continuous, landing_path="") == ""
    assert promote(continuous, landing_path=tmp_path / "none.png") == ""
    # 后端不声明多图参考（或能力解析不了）时不升级。
    monkeypatch.setattr(video, "_direct_canvas_modes", lambda backend: frozenset())
    assert promote(continuous) == ""


def test_compile_derives_traceable_fields_without_model() -> None:
    beats = [
        _beat(1, visual_description="【极速变焦推近 / 0-3秒】镜头极速向前变焦推近。天泉金液失去重力。"),
        _beat(2, visual_description="厉无赦向前踏出一步，剑锋压下。"),
        _beat(3, visual_description="应苍极横剑格挡。"),
    ]

    plan = compile_beat_contracts(beats)

    assert plan.source == "deterministic_partial"
    # 第 2、3 镜的剧本里没有可追溯的运镜，宁可保持 first_frame，也不猜运镜。
    assert sorted(plan.contracts) == [1]
    contract = plan.contracts[1]
    assert contract["primary_camera_motion"] == "极速变焦推近"
    assert contract["subject"] == "厉无赦、应苍极"
    # 连续接缝：落点直接借下一镜的可见起点，与执行层「下一镜首帧当尾帧」一致。
    assert contract["end_state"] == "厉无赦向前踏出一步，剑锋压下"
    assert any(note.startswith("beat_2:missing:") for note in plan.notes)
    assert any(note.startswith("beat_3:missing:") for note in plan.notes)


def test_compile_skips_beat_without_duration() -> None:
    beats = [
        _beat(1, target_duration_seconds=None, duration_seconds=None),
        _beat(2),
    ]

    plan = compile_beat_contracts(beats, planned=_full_plan())

    assert 1 not in plan.contracts
    assert "beat_1:missing:duration_seconds" in plan.notes


def test_decode_planned_fields_accepts_fenced_array_and_positional_fallback() -> None:
    beats = _episode()
    raw = (
        "```json\n"
        + json.dumps(
            [
                {"subject": "甲", "camera_motion": "推近", "seam": "CONTINUOUS"},
                {"subject": "乙", "primary_action": "乙转身", "seam": "cut"},
            ],
            ensure_ascii=False,
        )
        + "\n```"
    )

    planned = decode_planned_fields(raw, beats)

    assert planned[1]["subject"] == "甲"
    assert planned[1]["seam"] == SEAM_CONTINUOUS
    # 第二条没有 beat_number，按顺序落到第二个 Beat。
    assert planned[2]["primary_action"] == "乙转身"


def test_decode_planned_fields_rejects_garbage() -> None:
    assert decode_planned_fields("模型今天不想输出 JSON", _episode()) == {}
    assert decode_planned_fields("", _episode()) == {}


@pytest.mark.asyncio
async def test_plan_uses_agent_runner_and_reports_source() -> None:
    async def runner(request: str, *, beat_count: int) -> str:
        assert "镜头总数：3" in request
        assert beat_count == 3
        return json.dumps(
            [
                {
                    "beat_number": number,
                    **fields,
                }
                for number, fields in _full_plan().items()
            ],
            ensure_ascii=False,
        )

    plan = await plan_beat_shot_contracts(beats=_episode(), agent_runner=runner)

    assert plan.source == "planned"
    assert sorted(plan.contracts) == [1, 2, 3]


@pytest.mark.asyncio
async def test_plan_degrades_when_agent_unavailable() -> None:
    logs: list[str] = []

    async def runner(request: str, *, beat_count: int) -> str:
        raise RuntimeError("no text model")

    plan = await plan_beat_shot_contracts(
        beats=_episode(),
        agent_runner=runner,
        on_log=logs.append,
    )

    assert plan.source == "unavailable"
    assert plan.contracts == {}
    assert logs and "不可用" in logs[0]


@pytest.mark.asyncio
async def test_plan_skips_single_beat_episode() -> None:
    async def runner(request: str, *, beat_count: int) -> str:
        raise AssertionError("single-beat episode must not call the planner")

    plan = await plan_beat_shot_contracts(beats=[_beat(1)], agent_runner=runner)

    assert plan.source == "skipped"


@pytest.mark.asyncio
async def test_ensure_persists_once_then_reuses_cached_contracts() -> None:
    beats = _episode()
    calls: list[dict] = []

    class FakeStore:
        async def update_beat_asset(self, **kwargs):
            calls.append(kwargs)
            return True

    async def runner(request: str, *, beat_count: int) -> str:
        return json.dumps(
            [{"beat_number": number, **fields} for number, fields in _full_plan().items()],
            ensure_ascii=False,
        )

    first = await ensure_beat_shot_contracts(
        beats=beats,
        episode=1,
        store=FakeStore(),
        agent_runner=runner,
    )
    second = await ensure_beat_shot_contracts(
        beats=beats,
        episode=1,
        store=FakeStore(),
        agent_runner=runner,
    )

    assert first.persisted == 3
    assert len(calls) == 3
    assert all(item["episode_number"] == 1 for item in calls)
    assert shot_contract_ready(beats[0]["shot_contract"])
    assert second.source == "cached"
    assert second.contracts == {}
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_ensure_keeps_contracts_in_memory_when_store_write_fails() -> None:
    beats = _episode()

    class BrokenStore:
        async def update_beat_asset(self, **kwargs):
            raise RuntimeError("db locked")

    async def runner(request: str, *, beat_count: int) -> str:
        return json.dumps(
            [{"beat_number": number, **fields} for number, fields in _full_plan().items()],
            ensure_ascii=False,
        )

    plan = await ensure_beat_shot_contracts(
        beats=beats,
        episode=1,
        store=BrokenStore(),
        agent_runner=runner,
    )

    assert plan.persisted == 0
    assert shot_contract_ready(beats[1]["shot_contract"])


@pytest.mark.asyncio
async def test_global_optimizer_run_persists_contracts_and_enables_keyframe(
    monkeypatch, tmp_path: Path
) -> None:
    """合同一旦就绪，同一批次里 keyframe 准入必须真的打开。"""

    from novelvideo import cognee
    from novelvideo.agents import global_video_optimizer
    from novelvideo.agents import beat_shot_contracts
    from novelvideo.project_context import ProjectContext
    from novelvideo.task_backend.runners import video
    from novelvideo.utils.path_resolver import PathResolver

    paths = PathResolver(str(tmp_path), 1)
    for beat_num in (1, 2, 3):
        sketch = paths.sketch(beat_num)
        sketch.parent.mkdir(parents=True, exist_ok=True)
        sketch.write_bytes(b"fake-png")

    updates: list[dict] = []

    class FakeTaskManager:
        def update_progress_for_project(self, *args, **kwargs):
            return None

    class FakeCogneeStore:
        def __init__(self, *args, **kwargs):
            pass

        async def initialize(self):
            pass

        async def load_graph_state(self):
            pass

        async def update_beat_asset(self, **kwargs):
            updates.append(kwargs)
            return True

        async def close(self):
            pass

    class FakeOptimizer:
        async def optimize_single_beat(self, **kwargs):
            mode = "keyframe" if kwargs.get("allow_keyframe") else "first_frame"
            return {"prompt": f"{mode} 提示词", "video_mode": mode}

    async def fake_agent(request: str, *, beat_count: int) -> str:
        return json.dumps(
            [{"beat_number": number, **fields} for number, fields in _full_plan().items()],
            ensure_ascii=False,
        )

    monkeypatch.setattr(video, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(cognee, "CogneeStore", FakeCogneeStore)
    monkeypatch.setattr(beat_shot_contracts, "_run_agent", fake_agent)
    monkeypatch.setattr(
        global_video_optimizer,
        "prepare_global_optimizer_input",
        lambda **kwargs: (["grid.png"], {}, 3),
    )
    monkeypatch.setattr(
        global_video_optimizer,
        "resolve_video_strategy_capabilities",
        lambda backend: (frozenset({"first_frame", "keyframe"}), True),
    )
    monkeypatch.setattr(
        global_video_optimizer,
        "get_global_video_optimizer",
        lambda: FakeOptimizer(),
    )

    beats = _episode()
    ctx = ProjectContext(
        project_id="proj_1",
        project_name="demo",
        owner_type="user",
        owner_id="user_owner",
        owner_username="admin",
        requester_user_id="user_editor",
        requester_username="admin",
        requester_principals=(("user", "user_editor"),),
        effective_role="editor",
        home_node_id="node_a",
        output_dir=tmp_path,
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "runtime",
        is_home_node=True,
    )

    result = await video._run_global_optimize_video_async(
        {
            "episode": 1,
            "payload": {
                "episode": 1,
                "video_backend": "direct_demo",
                "beats": beats,
                "characters": [],
                "output_dir": str(tmp_path),
            },
        },
        ctx,
    )

    assert result["optimized"] == 3
    contract_writes = [item for item in updates if item.get("shot_contract_json")]
    assert len(contract_writes) == 3
    # 连续接缝（第 1 镜）走首尾帧；硬切（第 2 镜）与末镜（第 3 镜）守住
    # first_frame——首尾帧只发两张端点图，硬切镜头必须保留参考图与音频通道。
    assert beats[0]["video_mode"] == "keyframe"
    assert beats[0]["keyframe_prompt"] == "keyframe 提示词"
    assert beats[1]["video_mode"] == "first_frame"
    assert beats[1]["video_prompt"] == "first_frame 提示词"
    assert beats[2]["video_mode"] == "first_frame"
