from copy import deepcopy
from types import SimpleNamespace

import pytest

from novelvideo.freezone import sequence_rewrite
from novelvideo.freezone.sequence_rewrite import SequenceRewriteShot, resolve_sequence_indices
from novelvideo.ports.story_script import FreezoneStoryDirectorPlan, FreezoneStorySequencePlan


def fixture_rows():
    return [dict(shot_no=index + 1, shot_id=f"id-{index}", duration=4,
                 visual_description=f"原镜{index}", reference=f"frame-{index}.png",
                 character_image_1="locked.png") for index in range(4)]


def plan():
    return FreezoneStoryDirectorPlan(sequences=[dict(sequence_id="S1", shot_nos=[3, 2])])


def shot(shot_id):
    return SequenceRewriteShot(shot_id=shot_id, duration=3.4, visual_description="联合修改",
        shot="全景", character_action="保持停留", emotion="宁静", shot_prompt="", video_motion_prompt="",
        start_state="雨中", end_state="雨停", transition_plan="椭圆省略")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["diagnosis", "order", "remove", "membership", "foreign_plan", "duplicate_plan"])
async def test_automatic_sequence_rewrite_refuses_missing_diagnosis_and_structural_or_plan_changes(monkeypatch, failure):
    rows = fixture_rows()
    before = deepcopy(rows)
    output = dict(rows=[shot("id-1"), shot("id-2")], diagnosis="观察雨停后作出决定。")
    if failure == "diagnosis":
        output["diagnosis"] = ""
    elif failure == "order":
        output["shot_order_ids"] = ["id-2", "id-1"]
    elif failure == "remove":
        output["removed_shot_ids"] = ["id-1"]
    elif failure == "membership":
        output["sequence_plan"] = {"sequence_id": "S1", "shot_nos": [999]}
    elif failure == "foreign_plan":
        output["sequence_plans"] = [{"sequence_id": "S2"}]
    else:
        output["sequence_plan"] = {"sequence_id": "S1"}
        output["sequence_plans"] = [{"sequence_id": "S1"}]

    async def run(_task):
        return SimpleNamespace(output=sequence_rewrite.SequenceRewriteResult(**output))

    monkeypatch.setattr(sequence_rewrite, "create_sequence_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    with pytest.raises(ValueError):
        await sequence_rewrite.generate_sequence_rewrite(
            rows=rows, sequence_id="S1", director_plan=plan().model_dump(), instruction="联合修复", preserve_structure=True,
        )
    assert rows == before


@pytest.mark.asyncio
async def test_joint_overlapping_sequences_update_only_selected_plans_once(monkeypatch):
    rows = fixture_rows()
    before = deepcopy(rows)
    director = FreezoneStoryDirectorPlan(sequences=[
        {"sequence_id": "S1", "shot_nos": [1, 2]}, {"sequence_id": "S2", "shot_nos": [2, 3]},
        {"sequence_id": "S3", "shot_nos": [4], "performance_plan": "保持段外决定"},
    ])
    calls = []

    async def run(task):
        calls.append(task)
        return SimpleNamespace(output=sequence_rewrite.SequenceRewriteResult(
            rows=[shot("id-2"), shot("id-1"), shot("id-0")], diagnosis="两段共享的动作只修改一次。",
            sequence_plans=[{"sequence_id": "S1", "performance_plan": "看清触发"},
                            {"sequence_id": "S2", "performance_plan": "回应结果"}],
        ))

    monkeypatch.setattr(sequence_rewrite, "create_sequence_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    result, report = await sequence_rewrite.generate_sequence_rewrite(
        rows=rows, sequence_id="S1", related_sequence_ids=["S2"], director_plan=director.model_dump(),
        instruction="联合修复", preserve_structure=True,
    )
    assert len(calls) == 1 and result[3] == before[3] and rows == before
    receipt = report["sequence_rewrite"]
    assert receipt["sequence_ids"] == ["S1", "S2"]
    assert receipt["target_indices"] == [0, 1, 2]
    assert receipt["director_plan"]["sequences"][2] == director.sequences[2].model_dump()
    assert receipt["director_plan"]["sequences"][1]["shot_nos"] == [2, 3]


@pytest.mark.asyncio
async def test_sequence_rewrite_can_change_target_lens_for_new_viewing_purpose(monkeypatch):
    rows = fixture_rows()
    before = deepcopy(rows)
    technical = "技术参数：24mm镜头，f/8，深景深"
    source = "铺垫。" * 2000 + "结尾：雨停后，她仍在屋檐下听最后一滴雨。"
    instruction = "让观众看清远处的落点，改广角深景深"
    prompt = " + ".join(f"[{segment}]" for segment in [
        "画面构图：全景", "角色卡/主体描述：屋顶雨水", "主体/人物空间与互动关系：屋檐位于左侧",
        "极具体的微表情、主体状态或关键视觉信息：雨滴落下", "明确的场景环境元素与前景/背景道具：屋顶栏杆",
        "光影几何与大气效果：阴天柔光", "视觉风格/质感：剪纸动画", technical,
    ])

    async def run(task):
        assert "需要改变景别、机位或空间揭示时可同步调整" in task
        assert source in task
        assert task.rindex(instruction) > task.index(source)
        assert "id-0" in task and "id-3" in task
        return SimpleNamespace(output=sequence_rewrite.SequenceRewriteResult(rows=[
            shot("id-1").model_copy(update={"shot_prompt": prompt}),
            shot("id-2").model_copy(update={"shot_prompt": prompt}),
        ]))

    monkeypatch.setattr(sequence_rewrite, "create_sequence_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    result, _report = await sequence_rewrite.generate_sequence_rewrite(
        rows=rows, director_plan=plan(), sequence_id="S1", instruction=instruction, source_text=source,
    )
    assert technical in result[1]["shot_prompt"]
    assert technical in result[2]["shot_prompt"]
    assert result[0] == before[0] and result[3] == before[3]
    assert rows == before


@pytest.mark.asyncio
@pytest.mark.parametrize('identity', ['S1', 'other'])
async def test_rewrite_updates_only_selected_sequence_plan_atomically(monkeypatch, identity):
    rows = fixture_rows()
    original = deepcopy(rows)
    director = plan()
    director.sequences[0].staging_plan = '原空间安排'
    director.sequences[0].performance_plan = '原表演安排'
    director.sequences.append(FreezoneStorySequencePlan(sequence_id='S2', shot_nos=[1, 4], performance_plan='保持段外表演'))

    async def run(_task):
        return SimpleNamespace(output=sequence_rewrite.SequenceRewriteResult(
            rows=[shot('id-1'), shot('id-2')], shot_order_ids=['id-2', 'id-1'],
            sequence_plan={'sequence_id': identity, 'shot_nos': [999], 'performance_plan': '先看到雨停，再放下雨伞'},
        ))

    monkeypatch.setattr(sequence_rewrite, 'create_sequence_rewrite_agent', lambda _model: SimpleNamespace(run=run))
    if identity != 'S1':
        with pytest.raises(ValueError, match='规划编号'):
            await sequence_rewrite.generate_sequence_rewrite(rows=rows, sequence_id='S1', director_plan=director.model_dump(), instruction='重新安排反应')
    else:
        _, report = await sequence_rewrite.generate_sequence_rewrite(rows=rows, sequence_id='S1', director_plan=director.model_dump(), instruction='重新安排反应')
        updated = report['sequence_rewrite']['director_plan']['sequences'][0]
        assert updated['performance_plan'] == '先看到雨停，再放下雨伞'
        assert updated['staging_plan'] == '原空间安排'
        assert updated['shot_nos'] == [3, 2]
        assert report['sequence_rewrite']['director_plan']['sequences'][1] == director.sequences[1].model_dump()
    assert rows == original
    assert director.sequences[0].performance_plan == '原表演安排'


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["order", "foreign_source", "collision", "duplicate", "reason", "prompt"])
async def test_invalid_added_shot_is_rejected_before_mutation(monkeypatch, failure):
    rows = fixture_rows()
    before = deepcopy(rows)
    added = sequence_rewrite.SequenceAddedShot(
        **shot("id-1").model_dump(exclude={"shot_id", "duration_reason"}), new_key="new_1", source_shot_id="id-1", duration_reason="反应需要三秒",
    )
    order = ["id-1", "new_1", "id-2"]
    additions = [added]
    if failure == "order":
        order = None
    elif failure == "foreign_source":
        added.source_shot_id = "id-3"
    elif failure == "collision":
        added.new_key = "id-3"
    elif failure == "duplicate":
        additions.append(added.model_copy())
    elif failure == "reason":
        added.duration_reason = ""

    async def run(_task):
        return SimpleNamespace(output=sequence_rewrite.SequenceRewriteResult(
            rows=[shot("id-1"), shot("id-2")], added_shots=additions, shot_order_ids=order,
        ))

    monkeypatch.setattr(sequence_rewrite, "create_sequence_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    with pytest.raises(ValueError, match="新增镜头"):
        await sequence_rewrite.generate_sequence_rewrite(rows=rows, sequence_id="S1", director_plan=plan().model_dump(), instruction="补充反应镜头")
    assert rows == before


@pytest.mark.asyncio
async def test_joint_rewrite_is_one_call_and_merges_reversed_output_by_identity(monkeypatch):
    rows = fixture_rows()
    before = deepcopy(rows)
    captured = []

    async def run(task):
        captured.append(task)
        return SimpleNamespace(output=sequence_rewrite.SequenceRewriteResult(rows=[shot("id-2"), shot("id-1")]))

    monkeypatch.setattr(sequence_rewrite, "create_sequence_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    result, _report = await sequence_rewrite.generate_sequence_rewrite(
        rows=rows, sequence_id="S1", director_plan=plan().model_dump(), instruction="一起设计雨停后的省略",
    )
    assert len(captured) == 1
    assert "原镜0" in captured[0] and "原镜3" in captured[0]
    assert "一起设计雨停后的省略" in captured[0]
    assert result[0] == before[0] and result[3] == before[3] and rows == before
    for index in [1, 2]:
        assert result[index]["duration"] == 3.4
        assert result[index]["shot_id"] == before[index]["shot_id"]
        assert result[index]["reference"] == before[index]["reference"]
        assert result[index]["character_image_1"] == "locked.png"


@pytest.mark.asyncio
async def test_explicit_sequence_order_moves_stable_identity_and_assets_only_inside_block(monkeypatch):
    rows = fixture_rows()
    before = deepcopy(rows)

    async def run(_task):
        return SimpleNamespace(output=sequence_rewrite.SequenceRewriteResult(
            rows=[shot("id-1"), shot("id-2")], shot_order_ids=["id-2", "id-1"],
        ))

    monkeypatch.setattr(sequence_rewrite, "create_sequence_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    result, report = await sequence_rewrite.generate_sequence_rewrite(
        rows=rows, sequence_id="S1", director_plan=plan().model_dump(), instruction="先展示结果，再揭示原因",
    )
    from novelvideo.freezone.script_contract import script_rows_fingerprint
    receipt = report['sequence_rewrite']
    assert receipt['sequence_id'] == 'S1'
    assert receipt['before_shot_order_ids'] == [row['shot_id'] for row in before]
    assert receipt['after_shot_order_ids'] == [row['shot_id'] for row in result]
    assert receipt['input_rows_fingerprint'] == script_rows_fingerprint(before)
    assert receipt['output_rows_fingerprint'] == script_rows_fingerprint(result)
    assert receipt['input_rows_fingerprint'] != receipt['output_rows_fingerprint']
    assert receipt['added_shot_ids'] == receipt['removed_shot_ids'] == []
    assert [row["shot_id"] for row in result] == ["id-0", "id-2", "id-1", "id-3"]
    assert [result[index]["shot_order"] for index in [1, 2]] == [2, 3]
    assert result[1]["reference"] == before[2]["reference"]
    assert result[2]["reference"] == before[1]["reference"]
    assert result[0] == before[0] and result[3] == before[3] and rows == before


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [[], ["id-1"], ["id-1", "id-1"], ["id-1", "id-3"]])
async def test_invalid_explicit_order_is_rejected_atomically(monkeypatch, order):
    rows = fixture_rows()
    before = deepcopy(rows)

    async def run(_task):
        return SimpleNamespace(output=sequence_rewrite.SequenceRewriteResult(
            rows=[shot("id-1"), shot("id-2")], shot_order_ids=order,
        ))

    monkeypatch.setattr(sequence_rewrite, "create_sequence_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    with pytest.raises(ValueError, match="新片序"):
        await sequence_rewrite.generate_sequence_rewrite(rows=rows, sequence_id="S1", director_plan=plan().model_dump(), instruction="调序")
    assert rows == before


@pytest.mark.asyncio
async def test_noncontiguous_sequence_cannot_move_outside_shots(monkeypatch):
    rows = fixture_rows()
    before = deepcopy(rows)

    async def run(_task):
        return SimpleNamespace(output=sequence_rewrite.SequenceRewriteResult(
            rows=[shot("id-0"), shot("id-2")], shot_order_ids=["id-2", "id-0"],
        ))

    monkeypatch.setattr(sequence_rewrite, "create_sequence_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    with pytest.raises(ValueError, match="不连续"):
        await sequence_rewrite.generate_sequence_rewrite(rows=rows, sequence_id="S1",
            director_plan={"sequences": [{"sequence_id": "S1", "shot_nos": [1, 3]}]}, instruction="调序")
    assert rows == before


@pytest.mark.asyncio
async def test_explicit_removal_updates_sequence_and_preserves_media(monkeypatch):
    rows = fixture_rows()
    before = deepcopy(rows)

    async def run(_task):
        return SimpleNamespace(output=sequence_rewrite.SequenceRewriteResult(
            rows=[shot("id-2")], removed_shot_ids=["id-1"], shot_order_ids=["id-2"],
        ))

    monkeypatch.setattr(sequence_rewrite, "create_sequence_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    result, report = await sequence_rewrite.generate_sequence_rewrite(
        rows=rows, sequence_id="S1", director_plan=plan().model_dump(), instruction="合并重复的镜头，保留反应",
    )
    assert [row["shot_id"] for row in result] == ["id-0", "id-2", "id-3"]
    assert result[1]["reference"] == before[2]["reference"]
    assert [row["shot_order"] for row in result] == [1, 2, 3]
    assert report["sequence_rewrite"]["director_plan"]["sequences"][0]["shot_nos"] == [3]
    assert report["sequence_rewrite"]["target_indices"] == [1]
    assert rows == before


@pytest.mark.asyncio
@pytest.mark.parametrize("removed", [["foreign"], ["id-1", "id-1"], ["id-1", "id-2"]])
async def test_invalid_removal_is_atomic(monkeypatch, removed):
    rows = fixture_rows()
    before = deepcopy(rows)

    async def run(_task):
        return SimpleNamespace(output=sequence_rewrite.SequenceRewriteResult(rows=[shot("id-2")], removed_shot_ids=removed))

    monkeypatch.setattr(sequence_rewrite, "create_sequence_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    with pytest.raises(ValueError, match="删镜身份"):
        await sequence_rewrite.generate_sequence_rewrite(rows=rows, sequence_id="S1", director_plan=plan().model_dump(), instruction="删镜")
    assert rows == before


@pytest.mark.asyncio
async def test_removal_cannot_break_another_sequence(monkeypatch):
    rows = fixture_rows()
    director = plan()
    director.sequences.append(type(director.sequences[0])(sequence_id="S2", shot_nos=[2]))

    async def run(_task):
        return SimpleNamespace(output=sequence_rewrite.SequenceRewriteResult(rows=[shot("id-2")], removed_shot_ids=["id-1"]))

    monkeypatch.setattr(sequence_rewrite, "create_sequence_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    with pytest.raises(ValueError, match="其他段落"):
        await sequence_rewrite.generate_sequence_rewrite(rows=rows, sequence_id="S1", director_plan=director.model_dump(), instruction="删镜")


@pytest.mark.asyncio
@pytest.mark.parametrize("returned", [["id-1"], ["id-1", "id-1"], ["id-1", "id-3"], ["id-1", "id-2", "id-3"]])
async def test_joint_rewrite_rejects_wrong_identity_set_without_mutation(monkeypatch, returned):
    rows = fixture_rows()
    before = deepcopy(rows)

    async def run(_task):
        return SimpleNamespace(output=sequence_rewrite.SequenceRewriteResult(rows=[shot(identity) for identity in returned]))

    monkeypatch.setattr(sequence_rewrite, "create_sequence_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    with pytest.raises(ValueError, match="身份集合"):
        await sequence_rewrite.generate_sequence_rewrite(rows=rows, sequence_id="S1", director_plan=plan().model_dump(), instruction="改")
    assert rows == before


def test_sequence_resolution_requires_unique_shots_and_identity():
    rows = fixture_rows()
    assert resolve_sequence_indices(rows, plan(), "S1") == [1, 2]
    with pytest.raises(ValueError, match="不存在或重复"):
        resolve_sequence_indices(rows, plan(), "unknown")
    for bad in [dict(sequence_id="S1", shot_nos=[2, 2]), dict(sequence_id="S1", shot_nos=[9])]:
        with pytest.raises(ValueError):
            resolve_sequence_indices(rows, FreezoneStoryDirectorPlan(sequences=[bad]), "S1")
    rows[0]["shot_id"] = rows[1]["shot_id"]
    with pytest.raises(ValueError, match="稳定身份"):
        resolve_sequence_indices(rows, plan(), "S1")
    rows = fixture_rows()
    rows[0]["shot_id"] = " padded "
    with pytest.raises(ValueError, match="稳定身份"):
        resolve_sequence_indices(rows, plan(), "S1")


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [None, {"rewrite_shot_id": "id-1"}, {"rewrite_index": 0},
    {"repair_mode": "script-contract"}, {"current_rows": []}, {"director_plan": None},
    {"rewrite_sequence_id": "missing"}, {"prompt": " "}])
async def test_sequence_route_validates_before_queueing(tmp_path, monkeypatch, changes):
    from fastapi import HTTPException
    from novelvideo.api.routes import freezone as routes
    from novelvideo.api.schemas import FreezoneStoryScriptGenerateRequest

    captured = []

    async def resolve(*_args, **_kwargs):
        return object(), "tester", "project", tmp_path, str(tmp_path)

    async def enqueue(**kwargs):
        captured.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(routes, "_resolve_freezone_project", resolve)
    monkeypatch.setattr(routes, "_enqueue_freezone_background_job", enqueue)
    request = dict(current_rows=fixture_rows(), director_plan=plan().model_dump(),
                   rewrite_sequence_id="S1", prompt="共同设计省略")
    request.update(changes or {})
    body = FreezoneStoryScriptGenerateRequest(**request)
    if changes:
        with pytest.raises(HTTPException) as error:
            await routes.freezone_story_script_generate(project="project", body=body, user={})
        assert error.value.status_code == 400
        assert not captured
    else:
        assert (await routes.freezone_story_script_generate(project="project", body=body, user={}))["ok"]
        assert len(captured) == 1
        assert captured[0]["payload"]["rewrite_sequence_id"] == "S1"
        assert captured[0]["payload"]["director_plan"] == plan().model_dump()
