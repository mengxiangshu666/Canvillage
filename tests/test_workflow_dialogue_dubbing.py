"""Workflow speech belongs to the video model; no automatic TTS or cache reads."""
from types import SimpleNamespace

import pytest

from novelvideo.workflow_runtime import dialogue_dubbing, media_dispatch
from novelvideo.workflow_runtime.dialogue_dubbing import extract_dialogue_lines


@pytest.mark.parametrize("prompt,expected", [
    ('人物说：“别回头。”，镜头缓慢推近。', ('别回头。',)),
    ('[对话台词与语气：你终于来了] 镜头推近', ('你终于来了',)),
    ('[台词：“你终于来了”]', ('你终于来了',)),
    ('台词：你好吗？随后转身', ('你好吗',)),
    ('「我不走。」她说。「那就留下。」他回答。', ('我不走。', '那就留下。')),
    ('无台词，只有雨声', ()),
    ('角色沉默不语，镜头缓慢推近', ()),
])
def test_extract_dialogue_lines_matches_canvas_contract(prompt, expected):
    assert extract_dialogue_lines(prompt) == expected


def test_declared_dialogue_is_preserved_and_deduplicated():
    assert extract_dialogue_lines('台词：我知道了', declared_dialogue=('我知道了', '第二句')) == ('我知道了', '第二句')


@pytest.mark.parametrize('backend', ['legacy/video-test', 'direct/audio-unsupported', 'direct/native'])
async def test_video_speech_never_creates_external_audio(backend, tmp_path):
    # A trap context proves no output directory, speech cache or TTS service is consulted.
    class Context:
        @property
        def output_dir(self):
            raise AssertionError('video must not access TTS output')
    result = await dialogue_dubbing.prepare_dialogue_dubbing(
        ctx=Context(), prompt='人物说：“别回头。”', backend=backend,
        declared_dialogue=('第二句',), references=[{'type': 'image', 'path': 'first.png'}],
    )
    assert result.lines == ('别回头。', '第二句')
    assert result.applied is False
    assert result.reference_applied is False
    assert result.audio_path == ''
    assert result.reason == 'native_video_audio'
    assert list(tmp_path.iterdir()) == []


async def test_workflow_dispatch_keeps_native_speech_and_does_not_attach_tts(monkeypatch, tmp_path):
    ctx = SimpleNamespace(output_dir=tmp_path, state_dir=tmp_path, project_id='p', owner_username='local', project_name='test')
    queued = []
    async def context(_run):
        return ctx
    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            queued.append(kwargs['payload'])
            return SimpleNamespace(task_state=SimpleNamespace(status='queued', progress=0.0, task_id='video-task'))
    async def patch(*args, **kwargs):
        return {'ok': True}
    monkeypatch.setattr(media_dispatch, 'resolve_workflow_project_context', context)
    monkeypatch.setattr(media_dispatch, 'get_task_backend', lambda: Backend())
    monkeypatch.setattr(media_dispatch, 'get_task_manager', lambda: SimpleNamespace(
        get_task_for_project=lambda *_args, **_kwargs: None,
    ))
    monkeypatch.setattr(media_dispatch, '_patch_canvas_nodes', patch)
    monkeypatch.setattr(media_dispatch, 'read_canvas_snapshot', lambda *args: {'nodes': [{
        'id': 'video', 'type': 'videoNode', 'data': {
            'prompt': '人物说：“别回头。”', 'model': 'legacy-test', 'genMode': 'textToVideo',
            'durationSec': 5, 'aspectRatio': '16:9', 'quality': '720p',
            'generateAudio': False, 'generateAudioUserSet': False, 'nativeAudioStrategy': 'external',
        },
    }]})
    from novelvideo.generators.video import direct_models
    monkeypatch.setattr(direct_models, 'resolve_direct_video_model', lambda _backend: None)
    await media_dispatch.dispatch_workflow_video_batch(
        {'id': 'run', 'project_id': 'p', 'canvas_id': 'canvas', 'inputs': {}},
        state_dir=tmp_path, step_id='media_generation', node_ids=['video'], model_ref='legacy-test',
    )
    assert len(queued) == 1
    assert queued[0]['generate_audio'] is True
    assert queued[0]['native_audio_strategy'] == 'native'
    assert queued[0]['spoken_dialogue'] == ['别回头。']
    assert not queued[0]['dialogue_audio']
    assert not any(item.get('type') == 'audio' for item in queued[0]['reference_items'])
