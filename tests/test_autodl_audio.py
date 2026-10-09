from pathlib import Path
from types import SimpleNamespace
import pytest

from novelvideo.generators.autodl_audio_adapter import AutoDLAudioError, generate_autodl_audio


@pytest.mark.asyncio
async def test_autodl_audio_submits_raw_token_and_downloads_mp3(monkeypatch, tmp_path: Path):
    reference = tmp_path / "voice.wav"
    reference.write_bytes(b"reference")
    output = tmp_path / "audio" / "beat_01.mp3"
    calls: list[tuple[str, str, dict]] = []

    class Response:
        def __init__(self, status_code: int, payload=None, content: bytes = b""):
            self.status_code = status_code
            self._payload = payload
            self.content = content

        def json(self):
            return self._payload

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, *, headers, json):
            calls.append(("POST", url, {"headers": headers, "json": json}))
            return Response(200, {"code": "Success", "data": {"task_id": "task-123", "status": "QUEUED"}})

        async def get(self, url, *, headers):
            calls.append(("GET", url, {"headers": headers}))
            if "/result/" in url:
                return Response(200, {"code": "Success", "data": {"status": "SUCCESS", "results": [{"type": "audio", "url": "https://cdn.example/audio.wav"}]}})
            return Response(200, content=b"ID3fake-mp3")

    monkeypatch.setattr("httpx.AsyncClient", Client)
    monkeypatch.setattr(
        "novelvideo.generators.autodl_audio_adapter.upload_media_bytes",
        lambda data, *, ext: "https://relay.example/reference." + ext,
    )

    evidence = await generate_autodl_audio(
        api_key="raw-token",
        base_url="https://autodl.art",
        workflow_id="indextts2-v1",
        input_text="你好，测试音频",
        voice_path=reference,
        output_path=output,
        provider_mapping={
            "prompt_text": "prompt_text",
            "prompt_simple": "prompt_simple",
            "emo_ref_audio": "emo_ref_audio",
            "emo_control_method": "emo_control_method",
        },
        parameters={
            "emo_control_method": "与音色参考音频相同",
            "format": "mp3",
            "timeoutSeconds": 180,
            "strategy": "balanced",
        },
        poll_interval=0,
    )

    submit = next(item for item in calls if item[0] == "POST")
    assert submit[2]["headers"]["Authorization"] == "raw-token"
    assert not submit[2]["headers"]["Authorization"].startswith("Bearer ")
    assert submit[2]["json"] == {
        "prompt_text": "你好，测试音频",
        "prompt_simple": "https://relay.example/reference.wav",
        "emo_ref_audio": "https://relay.example/reference.wav",
        "emo_control_method": "与音色参考音频相同",
    }
    assert evidence["status"] == "SUCCESS"
    assert evidence["task_id"] == "task-123"
    assert output.read_bytes().startswith(b"ID3")


@pytest.mark.asyncio
async def test_autodl_audio_requires_task_id(monkeypatch, tmp_path: Path):
    reference = tmp_path / "voice.wav"
    reference.write_bytes(b"reference")

    class Response:
        status_code = 200
        content = b""

        def json(self):
            return {"code": "Success", "data": {"status": "QUEUED"}}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr("httpx.AsyncClient", Client)
    monkeypatch.setattr(
        "novelvideo.generators.autodl_audio_adapter.upload_media_bytes",
        lambda *_args, **_kwargs: "https://relay.example/reference.wav",
    )

    with pytest.raises(AutoDLAudioError, match="缺少任务 ID") as error:
        await generate_autodl_audio(
            api_key="raw-token",
            base_url="https://autodl.art",
            workflow_id="indextts2-v1",
            input_text="测试",
            voice_path=reference,
            output_path=tmp_path / "audio.mp3",
            provider_mapping={"prompt_text": "prompt_text", "prompt_simple": "prompt_simple"},
        )
    assert error.value.code == "AUTODL_AUDIO_TASK_ID_MISSING"
    assert error.value.details["stage"] == "submit"
    assert error.value.details["data_keys"] == ["status"]


@pytest.mark.asyncio
async def test_autodl_audio_accepts_nested_task_identifier(monkeypatch, tmp_path: Path):
    reference = tmp_path / "voice.wav"
    reference.write_bytes(b"reference")
    output = tmp_path / "audio.mp3"

    class Response:
        status_code = 200
        content = b"ID3nested"

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            return Response({"code": "Success", "data": {"task": {"id": "nested-task"}}})

        async def get(self, url, *, headers):
            if "/result/" in url:
                return Response({"code": "Success", "data": {"status": "SUCCESS", "results": [{"type": "audio", "url": "https://cdn.example/audio.wav"}]}})
            return Response(None)

    monkeypatch.setattr("httpx.AsyncClient", Client)
    monkeypatch.setattr(
        "novelvideo.generators.autodl_audio_adapter.upload_media_bytes",
        lambda *_args, **_kwargs: "https://relay.example/reference.wav",
    )

    evidence = await generate_autodl_audio(
        api_key="raw-token",
        base_url="https://autodl.art",
        workflow_id="indextts2-v1",
        input_text="测试",
        voice_path=reference,
        output_path=output,
        provider_mapping={"prompt_text": "prompt_text", "prompt_simple": "prompt_simple"},
        poll_interval=0,
    )

    assert evidence["task_id"] == "nested-task"
    assert output.read_bytes().startswith(b"ID3")


@pytest.mark.asyncio
async def test_direct_audio_speech_routes_autodl_without_openai_audio(monkeypatch, tmp_path: Path):
    from novelvideo.freezone import audio_node

    reference = tmp_path / "voice.wav"
    reference.write_bytes(b"reference")
    output = tmp_path / "audio.mp3"
    direct = SimpleNamespace(
        kind="audio",
        catalog_id="direct/audio-autodl",
        registry_id="audio-autodl",
        upstream_model="indextts2-v1",
        base_url="https://autodl.art",
        api_key="raw-token",
        protocol="autodl-comfyui",
    )
    calls: dict[str, object] = {}

    monkeypatch.setattr("novelvideo.generators.direct_models.require_direct_model", lambda *_args: direct)
    monkeypatch.setattr("novelvideo.generators.direct_models.ensure_direct_model_runtime_ready", lambda model: model)
    monkeypatch.setattr("novelvideo.generators.direct_models.ensure_direct_model_supports_mode", lambda model, _mode: model)
    monkeypatch.setattr(
        "novelvideo.generators.direct_models.direct_model_option",
        lambda _model: {
            "providerMapping": {"prompt_text": "prompt_text", "prompt_simple": "prompt_simple"},
            "parameterDefaults": {"emo_control_method": "与音色参考音频相同"},
        },
    )

    async def fake_generate(**kwargs):
        calls.update(kwargs)
        Path(kwargs["output_path"]).write_bytes(b"ID3audio")
        return {"status": "SUCCESS"}

    monkeypatch.setattr("novelvideo.generators.autodl_audio_adapter.generate_autodl_audio", fake_generate)
    monkeypatch.setattr(audio_node, "_duration_ms", lambda _path: 321)

    result = await audio_node.generate_direct_audio_speech(
        output_path=output,
        input_text="你好",
        model_ref="direct/audio-autodl",
        voice_path=reference,
        voice_source="character",
    )

    assert result.model == "direct/audio-autodl"
    assert result.duration_ms == 321
    assert calls["workflow_id"] == "indextts2-v1"
    assert calls["voice_path"] == reference
    assert calls["parameters"] == {"emo_control_method": "与音色参考音频相同"}
