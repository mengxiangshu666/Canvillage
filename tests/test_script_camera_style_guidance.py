"""Authored examples and repair guidance must agree with cinematic intent."""

from copy import deepcopy
import json
from pathlib import Path
import re

from pydantic_ai.messages import SystemPromptPart
from pydantic_ai.models.test import TestModel
import pytest

from novelvideo.freezone import sequence_rewrite, text_node
from novelvideo.freezone.film_prompt_contract import audit_film_prompt_bundle, build_film_prompt_bundle
from novelvideo.freezone.script_contract import MOTION_SEGMENT_ORDER, SHOT_SEGMENT_ORDER, classify_segments, split_prompt_segments
from tests.test_film_production_contracts import _shot
from tests.test_freezone_story_script_runner_contract import _project_context
from tests.test_script_director_validation import complete_script


def _examples():
    prompt = text_node.FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT
    image_section = prompt.split('Example style for `shot_prompt`:', 1)[1].split('`video_motion_prompt` describes', 1)[0]
    motion_section = prompt.split('Example style for `video_motion_prompt`:', 1)[1].split('## Quality bar', 1)[0]
    return re.findall(r'`(\[画面构图：[^`]+)`', image_section), re.findall(r'`(\[明确的摄影机[^`]+)`', motion_section)


def _script(moving: bool):
    images, motions = _examples()
    data = complete_script()
    data.director_plan.sequences = data.director_plan.sequences[:1]
    row = data.rows[0]
    row.sequence_ids = ['S1']
    row.shot_id = 'camera-style'
    row.shot_prompt = images[int(moving)]
    row.video_motion_prompt = motions[int(moving)]
    row.duration = float(re.search(r'时长：(\d+(?:\.\d+)?)s', row.video_motion_prompt)[1])
    row.shot = '中近景'
    row.character_action = '手接近信封后撤回，信封保持原位'
    row.emotion = '犹疑'
    row.dialogue = '无'
    row.start_state = '双手放在信封两侧'
    row.end_state = '右手撤回桌沿，信封保持原位'
    row.shot_purpose = '看见她有机会拿取信封却主动撤回'
    row.cut_reason = '保留未拿取的结果与人物关系'
    return data


def test_missing_camera_and_duration_guidance_preserves_creative_choices():
    row = _shot()
    row['video_motion_prompt'] = '[主体物理动作：他转身] + [环境物理动态：雨滴下落] + [音效：雨声] + [对白：无]'
    report = audit_film_prompt_bundle(build_film_prompt_bundle([row]))
    issues = {issue['code']: issue for issue in report['issues']}
    assert report['passed'] is False
    camera = issues['prompt.camera_missing']
    assert '固定观察' in camera['fix'] and '观看目的' in camera['fix']
    assert '触发' in camera['fix'] and '结束构图' in camera['fix']
    assert '切镜头' not in camera['fix'] and '唯一主运镜' not in camera['message']
    assert '4.0s' not in issues['prompt.duration_missing']['fix']


def test_examples_show_observable_style_and_two_purposeful_camera_options():
    images, motions = _examples()
    assert len(images) == len(motions) == 2
    style = []
    for image in images:
        roles = classify_segments(image, SHOT_SEGMENT_ORDER)
        assert roles == [role for role, _ in SHOT_SEGMENT_ORDER]
        style.append(split_prompt_segments(image)[roles.index('style')])
    assert style[0] == style[1]
    assert '哑光' in style[0] and '纸纤维' in style[0]
    assert '电影感' not in style[0]
    for motion in motions:
        assert classify_segments(motion, MOTION_SEGMENT_ORDER) == [role for role, _ in MOTION_SEGMENT_ORDER]
        assert '信封' in motion and '撤回' in motion
        assert not any(filler in motion for filler in ['指尖抽动', '屏幕闪烁', '空调风'])
    assert '固定' in motions[0] and '全程' in motions[0]
    assert '触发' in motions[1] and '结束构图' in motions[1]
    assert '示例秒数只展示格式' in text_node.FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT


@pytest.mark.parametrize('moving', [False, True])
def test_film_audit_keeps_authored_fixed_and_triggered_camera_examples(moving):
    row = _script(moving).rows[0].model_dump()
    original = deepcopy(row)
    bundle = build_film_prompt_bundle([row])
    report = audit_film_prompt_bundle(bundle)
    assert report['passed'] is True
    assert bundle['rows'][0]['video_motion_prompt'] == row['video_motion_prompt']
    assert row == original


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['story', 'vision', 'shot', 'sequence'])
async def test_all_real_agent_paths_receive_same_concrete_examples(monkeypatch, kind):
    data = _script(moving=True)
    row = data.rows[0].model_dump()
    if kind in {'story', 'vision'}:
        output = data.model_dump_json()
    elif kind == 'shot':
        output = text_node.FreezoneShotRewriteRow.model_validate(row).model_dump_json()
    else:
        output = sequence_rewrite.SequenceRewriteResult.model_validate({'rows': [row], 'diagnosis': '保留撤回与风格'}).model_dump_json()
    model = TestModel(custom_output_text=output)
    for module in (text_node, sequence_rewrite):
        monkeypatch.setattr(module, '_direct_or_newapi_text_model', lambda **_kwargs: (model, 'test'))
        monkeypatch.setattr(module, 'resolve_freezone_story_script_model', lambda _model: {'id': 'test', 'provider': 'newapi', 'model': 'test'})
    agent = {
        'story': text_node.create_freezone_story_script_agent,
        'vision': text_node.create_freezone_vision_story_script_agent,
        'shot': text_node.create_freezone_shot_rewrite_agent,
        'sequence': sequence_rewrite.create_sequence_rewrite_agent,
    }[kind]('test')
    result = await agent.run('保留同一纸张材质，手撤回后才改变观看角度')
    system = '\n'.join(part.content for message in result.all_messages() for part in message.parts if isinstance(part, SystemPromptPart))
    images, motions = _examples()
    assert all(example in system for example in [*images, *motions])
    assert '第 1 段区分固定构图与固定世界机位' in system
    assert '先跟随后停写清移动阶段与停止触发' in system
    returned = result.output.rows[0] if kind != 'shot' else result.output
    assert returned.video_motion_prompt == row['video_motion_prompt']
    assert returned.shot_prompt == row['shot_prompt']


@pytest.mark.asyncio
@pytest.mark.parametrize('moving', [False, True], ids=['fixed-observation', 'triggered-move'])
async def test_isolated_runner_persists_camera_and_style_without_fixed_duration(monkeypatch, tmp_path: Path, moving):
    from novelvideo.task_backend.runners import freezone as runner

    data = _script(moving)
    original = deepcopy(data.model_dump())
    model = TestModel(custom_output_text=data.model_dump_json())
    monkeypatch.setattr(text_node, '_direct_or_newapi_text_model', lambda **_kwargs: (model, 'test'))
    monkeypatch.setattr(text_node, 'resolve_freezone_story_script_model', lambda _model: {'id': 'test', 'provider': 'newapi', 'model': 'test'})
    monkeypatch.setattr(text_node, 'get_freezone_story_script_agent', text_node.create_freezone_story_script_agent)
    monkeypatch.setattr(runner, '_update', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, '_append_node_history', lambda **_kwargs: None)
    result = await runner._run_freezone_story_script_async({'payload': {
        'job_id': 'guidance-test', 'project_dir': str(tmp_path), 'source_text': '她决定不拿取桌上的信封',
    }}, _project_context(tmp_path))
    saved = json.loads(Path(result['output_path']).read_text(encoding='utf-8'))
    assert len(saved['rows']) == 1
    row = saved['rows'][0]
    assert row['video_motion_prompt'] == original['rows'][0]['video_motion_prompt']
    assert row['shot_prompt'] == original['rows'][0]['shot_prompt']
    assert row['duration'] == original['rows'][0]['duration']
    assert saved['contract_report']['blocking_count'] == 0
    assert data.model_dump() == original
