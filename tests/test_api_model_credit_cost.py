import pytest
from fastapi import HTTPException
from types import SimpleNamespace


def patch_direct_video_models(monkeypatch):
    from novelvideo.generators.video import direct_models
    from novelvideo.generators.video.direct_models import DirectVideoModel

    models = (
        DirectVideoModel(
            registry_id="wokey-fast",
            label="即梦极速",
            upstream_model="jimeng-seedance-2.0-fast",
            base_url="https://video.example/v1",
            api_key="secret-must-not-leak",
            enabled=True,
        ),
    )
    monkeypatch.setattr(direct_models, "list_direct_video_models", lambda: models)
    return models


def patch_quote(monkeypatch, model_credits, *, expected_model: str, cost: int) -> None:
    from novelvideo.ports.credit_quote import CreditQuote
    from novelvideo.ports.registry import register_port

    class FakeCreditQuotePort:
        async def generation_credit_quote(
            self,
            *,
            kind: str,
            model: str,
            params=None,
            quantity=1,
        ):
            del kind, params, quantity
            assert model == expected_model
            return CreditQuote(total_cost=cost, display=str(cost))

    register_port("credit_quote", FakeCreditQuotePort())


def patch_quote_expect(
    monkeypatch,
    model_credits,
    *,
    expected_kind: str,
    expected_model: str,
    expected_params: dict,
    expected_quantity: int,
    cost: int,
) -> None:
    from novelvideo.ports.credit_quote import CreditQuote
    from novelvideo.ports.registry import register_port

    class FakeCreditQuotePort:
        async def generation_credit_quote(
            self,
            *,
            kind: str,
            model: str,
            params=None,
            quantity=1,
        ):
            assert kind == expected_kind
            assert model == expected_model
            assert params == expected_params
            assert quantity == expected_quantity
            return CreditQuote(total_cost=cost, display=str(cost))

    register_port("credit_quote", FakeCreditQuotePort())


def patch_quote_display_mismatch(cost: int, display: str) -> None:
    from novelvideo.ports.credit_quote import CreditQuote
    from novelvideo.ports.registry import register_port

    class FakeCreditQuotePort:
        async def generation_credit_quote(
            self,
            *,
            kind: str,
            model: str,
            params=None,
            quantity=1,
        ):
            return CreditQuote(total_cost=cost, display=display)

    register_port("credit_quote", FakeCreditQuotePort())


@pytest.mark.asyncio
async def test_generation_credit_cost_route_keeps_local_display_helper(monkeypatch):
    from novelvideo.api.routes import model_credits

    patch_quote_display_mismatch(cost=8, display="different")

    result = await model_credits.get_generation_credit_cost(
        kind="model",
        value="gpt-image-2",
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 8, "display": "8"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_uses_ce_zero_quote_port(monkeypatch):
    from novelvideo.api.routes import model_credits
    from novelvideo.ports.local.credit_quote import LocalCreditQuote
    from novelvideo.ports.registry import register_port

    register_port("credit_quote", LocalCreditQuote())

    result = await model_credits.get_generation_credit_cost(
        kind="model",
        value="gpt-image-2",
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 0, "display": "0"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_resolves_model_kind(monkeypatch):
    from novelvideo.api.routes import model_credits

    patch_quote(monkeypatch, model_credits, expected_model="gpt-image-2", cost=5)

    result = await model_credits.get_generation_credit_cost(
        kind="model",
        value=" gpt-image-2 ",
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 5, "display": "5"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_passes_params_and_quantity(monkeypatch):
    from novelvideo.api.routes import model_credits

    patch_quote_expect(
        monkeypatch,
        model_credits,
        expected_kind="image",
        expected_model="gpt-image-2",
        expected_params={"quality": "high", "size": "2k"},
        expected_quantity=3,
        cost=24,
    )
    monkeypatch.setattr(
        model_credits,
        "_image_selection_cost_model",
        lambda selection: "gpt-image-2",
    )

    result = await model_credits.get_generation_credit_cost(
        kind="image_selection",
        value="newapi_gpt_image2",
        params='{"size":"2k","quality":"high"}',
        quantity=3,
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 24, "display": "24"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_rejects_blank_model():
    from novelvideo.api.routes import model_credits

    with pytest.raises(HTTPException) as exc_info:
        await model_credits.get_generation_credit_cost(
            kind="model",
            value="   ",
            user={"user_id": "usr_1"},
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "model is required"


@pytest.mark.asyncio
async def test_generation_credit_cost_route_resolves_beat_tts(monkeypatch):
    from novelvideo.api.routes import model_credits

    patch_quote(monkeypatch, model_credits, expected_model="index-tts-2", cost=3)

    result = await model_credits.get_generation_credit_cost(
        kind="beat_tts",
        value="index-tts-2",
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 3, "display": "3"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_resolves_beat_tts_from_direct_audio(monkeypatch):
    from novelvideo.api.routes import model_credits
    from novelvideo.generators import direct_models

    monkeypatch.setattr(
        direct_models,
        "resolve_direct_model",
        lambda kind, _ref=None: (
            SimpleNamespace(upstream_model="configured-speech-model")
            if kind == "audio"
            else None
        ),
    )
    patch_quote(monkeypatch, model_credits, expected_model="configured-speech-model", cost=3)

    result = await model_credits.get_generation_credit_cost(
        kind="beat_tts",
        value="direct/speech-model",
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 3, "display": "3"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_rejects_unconfigured_blank_beat_tts(monkeypatch):
    from novelvideo.api.routes import model_credits
    from novelvideo.generators import direct_models

    monkeypatch.setattr(direct_models, "resolve_direct_model", lambda *_args: None)

    with pytest.raises(HTTPException) as exc_info:
        await model_credits.get_generation_credit_cost(
            kind="beat_tts",
            value="",
            user={"user_id": "usr_1"},
        )

    assert exc_info.value.status_code == 400
    assert "未配置音频模型" in exc_info.value.detail


@pytest.mark.asyncio
async def test_beat_tts_quote_never_raises_on_the_withdrawn_audio_family(monkeypatch, tmp_path):
    """7e92f79 撤下音频族后，``kind="audio"`` 仍从节点绑定和额度报价里进来。

    当时 ``canonical_direct_model_kind`` 直接抛 ``ValueError``，价位接口没有兜住，
    整条 ``GET /api/v1/generation-credit-cost?kind=beat_tts`` 变成 HTTP 500
    （2026-09-16 02:07 真机 3 次）。这里不 mock 解析函数：走真实注册表，确认它只
    返回空并落进既有的 400 分支。
    """

    from novelvideo import config

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    from novelvideo.api.routes import model_credits

    with pytest.raises(HTTPException) as exc_info:
        await model_credits.get_generation_credit_cost(
            kind="beat_tts",
            value="",
            user={"user_id": "usr_1"},
        )

    assert exc_info.value.status_code == 400
    assert "未配置音频模型" in exc_info.value.detail


@pytest.mark.asyncio
async def test_generation_credit_cost_route_resolves_freezone_audio_music(monkeypatch):
    from novelvideo.api.routes import model_credits
    from novelvideo.generators import direct_models

    monkeypatch.setattr(
        direct_models,
        "resolve_direct_model",
        lambda kind, _ref=None: (
            SimpleNamespace(upstream_model="configured-audio-model")
            if kind == "audio"
            else None
        ),
    )

    patch_quote_expect(
        monkeypatch,
        model_credits,
        expected_kind="audio",
        expected_model="configured-audio-model",
        expected_params={},
        expected_quantity=30,
        cost=90,
    )

    result = await model_credits.get_generation_credit_cost(
        kind="freezone_audio_music",
        value="",
        quantity=30,
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 90, "display": "90"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_resolves_freezone_story_script(monkeypatch):
    from novelvideo.api.routes import model_credits

    patch_quote(
        monkeypatch,
        model_credits,
        expected_model="configured-story-model",
        cost=4,
    )

    result = await model_credits.get_generation_credit_cost(
        kind="freezone_story_script",
        value="configured-story-model",
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 4, "display": "4"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_resolves_freezone_image_reverse_prompt(monkeypatch):
    from novelvideo.api.routes import model_credits
    from novelvideo.generators import direct_models

    monkeypatch.setattr(
        direct_models,
        "resolve_direct_model",
        lambda kind, _ref=None: (
            SimpleNamespace(upstream_model="freezone-vision-model")
            if kind == "vision"
            else None
        ),
    )
    patch_quote_expect(
        monkeypatch,
        model_credits,
        expected_kind="text",
        expected_model="freezone-vision-model",
        expected_params={},
        expected_quantity=1,
        cost=6,
    )

    result = await model_credits.get_generation_credit_cost(
        kind="freezone_image_reverse_prompt",
        value="",
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 6, "display": "6"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_resolves_style_analyzer(monkeypatch):
    from novelvideo.api.routes import model_credits
    from novelvideo.generators import direct_models

    monkeypatch.setattr(
        direct_models,
        "resolve_direct_model",
        lambda kind, _ref=None: (
            SimpleNamespace(upstream_model="style-analyzer-model")
            if kind == "vision"
            else None
        ),
    )
    patch_quote_expect(
        monkeypatch,
        model_credits,
        expected_kind="text",
        expected_model="style-analyzer-model",
        expected_params={},
        expected_quantity=1,
        cost=7,
    )

    result = await model_credits.get_generation_credit_cost(
        kind="style_analyzer",
        value="",
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 7, "display": "7"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_resolves_image_selection(monkeypatch):
    from novelvideo.api.routes import model_credits
    from novelvideo import config

    monkeypatch.setitem(
        config.IMAGE_GENERATION_SELECTIONS["newapi_gpt_image2"],
        "model",
        "configured-image-model",
    )
    expected_model = config.IMAGE_GENERATION_SELECTIONS["newapi_gpt_image2"]["model"]

    patch_quote(monkeypatch, model_credits, expected_model=expected_model, cost=7)

    result = await model_credits.get_generation_credit_cost(
        kind="image_selection",
        value="newapi_gpt_image2",
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 7, "display": "7"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_resolves_direct_image_selection(monkeypatch):
    from novelvideo.api.routes import model_credits
    from novelvideo.generators import direct_image_models

    monkeypatch.setattr(
        direct_image_models,
        "resolve_direct_image_model",
        lambda _value: SimpleNamespace(upstream_model="configured-image-model"),
    )
    patch_quote(monkeypatch, model_credits, expected_model="configured-image-model", cost=7)

    result = await model_credits.get_generation_credit_cost(
        kind="image_selection",
        value="direct/portrait-model",
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 7, "display": "7"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_resolves_image_selection_label(monkeypatch):
    from novelvideo.api.routes import model_credits
    from novelvideo import config

    monkeypatch.setitem(
        config.IMAGE_GENERATION_SELECTIONS["newapi_gpt_image2"],
        "model",
        "configured-image-model",
    )
    expected_model = config.IMAGE_GENERATION_SELECTIONS["newapi_gpt_image2"]["model"]

    patch_quote(monkeypatch, model_credits, expected_model=expected_model, cost=7)

    result = await model_credits.get_generation_credit_cost(
        kind="image_selection",
        value=config.character_image_selection_options()["newapi_gpt_image2"],
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 7, "display": "7"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_resolves_fixed_image(monkeypatch):
    from novelvideo.api.routes import model_credits

    monkeypatch.setattr(
        model_credits,
        "_fixed_image_cost_model",
        lambda kind: "scene-fixed-model" if kind == "scene_master" else "",
    )

    patch_quote(monkeypatch, model_credits, expected_model="scene-fixed-model", cost=9)

    result = await model_credits.get_generation_credit_cost(
        kind="fixed_image",
        value="scene_master",
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 9, "display": "9"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_adds_scene_pano_params(monkeypatch):
    from novelvideo.api.routes import model_credits

    monkeypatch.setenv("SCENE_360_IMAGE_SIZE", "2K")
    monkeypatch.setenv("SCENE_360_IMAGE_QUALITY", "medium")
    monkeypatch.setattr(
        model_credits,
        "_fixed_image_cost_model",
        lambda kind: "gpt-image-2" if kind == "scene_pano" else "",
    )
    patch_quote_expect(
        monkeypatch,
        model_credits,
        expected_kind="image",
        expected_model="gpt-image-2",
        expected_params={"size": "2K", "quality": "medium"},
        expected_quantity=1,
        cost=18,
    )

    result = await model_credits.get_generation_credit_cost(
        kind="fixed_image",
        value="scene_pano",
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 18, "display": "18"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_adds_image_mode_params(monkeypatch):
    from novelvideo import config
    from novelvideo.api.routes import model_credits

    monkeypatch.setattr(config, "OPENAI_IMAGE_QUALITY", "medium")
    monkeypatch.setattr(
        model_credits,
        "_image_selection_cost_model",
        lambda selection: "gpt-image-2",
    )
    patch_quote_expect(
        monkeypatch,
        model_credits,
        expected_kind="image",
        expected_model="gpt-image-2",
        expected_params={"size": "2K", "quality": "medium"},
        expected_quantity=1,
        cost=11,
    )

    result = await model_credits.get_generation_credit_cost(
        kind="image_selection",
        value="newapi_gpt_image2",
        mode_key="2x2_1-1",
        image_role="render",
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 11, "display": "11"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_canvas_uses_only_explicit_params(monkeypatch):
    from novelvideo.api.routes import model_credits

    monkeypatch.setattr(
        model_credits,
        "_image_selection_cost_model",
        lambda selection: "gpt-image-2",
    )
    patch_quote_expect(
        monkeypatch,
        model_credits,
        expected_kind="image",
        expected_model="gpt-image-2",
        expected_params={"size": "2K"},
        expected_quantity=2,
        cost=16,
    )

    result = await model_credits.get_generation_credit_cost(
        kind="image_selection",
        surface="canvas",
        value="newapi_gpt_image2",
        params='{"size":"2K"}',
        quantity=2,
        mode_key="2x2_1-1",
        image_role="character",
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 16, "display": "16"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_adds_character_image_params(monkeypatch):
    from novelvideo import config
    from novelvideo.api.routes import model_credits

    monkeypatch.setattr(config, "OPENAI_IMAGE_QUALITY", "medium")
    monkeypatch.setattr(
        model_credits,
        "_image_selection_cost_model",
        lambda selection: "gpt-image-2",
    )
    patch_quote_expect(
        monkeypatch,
        model_credits,
        expected_kind="image",
        expected_model="gpt-image-2",
        expected_params={"size": "1K", "quality": "medium"},
        expected_quantity=1,
        cost=13,
    )

    result = await model_credits.get_generation_credit_cost(
        kind="image_selection",
        value="newapi_gpt_image2",
        image_role="character",
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 13, "display": "13"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_keeps_video_params_and_quantity(monkeypatch):
    from novelvideo.api.routes import model_credits

    patch_quote_expect(
        monkeypatch,
        model_credits,
        expected_kind="video",
        expected_model="seedance-1.0-pro-fast",
        expected_params={"resolution": "720p"},
        expected_quantity=5,
        cost=25,
    )

    result = await model_credits.get_generation_credit_cost(
        kind="video_backend",
        value="newapi_seedance-1.0-pro-fast",
        params='{"resolution":"720p"}',
        quantity=5,
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 25, "display": "25"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_resolves_newapi_video_backend(monkeypatch):
    from novelvideo.api.routes import model_credits

    patch_quote(monkeypatch, model_credits, expected_model="seedance-1.0-pro-fast", cost=12)

    result = await model_credits.get_generation_credit_cost(
        kind="video_backend",
        value="newapi_seedance-1.0-pro-fast",
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 12, "display": "12"}}


@pytest.mark.parametrize(
    "value",
    [
        "jimeng-seedance-2.0-fast",
        "direct_wokey-fast",
        "wokey-fast",
        "即梦极速",
    ],
)
@pytest.mark.asyncio
async def test_generation_credit_cost_route_resolves_direct_video_model(
    monkeypatch,
    value,
):
    from novelvideo.api.routes import model_credits

    patch_direct_video_models(monkeypatch)
    patch_quote(
        monkeypatch,
        model_credits,
        expected_model="jimeng-seedance-2.0-fast",
        cost=2,
    )

    result = await model_credits.get_generation_credit_cost(
        kind="video_backend",
        value=value,
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 2, "display": "2"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_does_not_expose_direct_video_secret(
    monkeypatch,
):
    from novelvideo.api.routes import model_credits

    models = patch_direct_video_models(monkeypatch)

    assert model_credits._video_backend_cost_model(models[0].upstream_model) == (
        "jimeng-seedance-2.0-fast"
    )
    assert "secret-must-not-leak" not in model_credits._video_backend_cost_model(
        models[0].backend
    )


@pytest.mark.asyncio
async def test_generation_credit_cost_route_resolves_newapi_video_backend_label(monkeypatch):
    from novelvideo import config
    from novelvideo.api.routes import model_credits
    from novelvideo.generators.video_generator import newapi_video_backend_options

    monkeypatch.setattr(config, "NEWAPI_VIDEO_MODELS", ["seedance-1.0-pro-fast"])
    monkeypatch.setattr(config, "NEWAPI_VIDEO_AUDIO_MODELS", [])
    monkeypatch.setattr(config, "NEWAPI_VIDEO_DURATION_BOUNDS", "")
    patch_quote(monkeypatch, model_credits, expected_model="seedance-1.0-pro-fast", cost=12)

    result = await model_credits.get_generation_credit_cost(
        kind="video_backend",
        value=newapi_video_backend_options()["newapi_seedance-1.0-pro-fast"],
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 12, "display": "12"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_resolves_huimeng_video_backend(monkeypatch):
    from novelvideo.api.routes import model_credits

    patch_quote(monkeypatch, model_credits, expected_model="seedance-2.0-fast", cost=15)

    result = await model_credits.get_generation_credit_cost(
        kind="video_backend",
        value="huimeng_seedance-2.0-fast",
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 15, "display": "15"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_resolves_huimeng_video_backend_label(monkeypatch):
    from novelvideo.api.routes import model_credits
    from novelvideo.generators.huimengi import huimeng_video_backend_options

    patch_quote(monkeypatch, model_credits, expected_model="seedance-2.0-fast", cost=15)

    result = await model_credits.get_generation_credit_cost(
        kind="video_backend",
        value=huimeng_video_backend_options()["huimeng_seedance-2.0-fast"],
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 15, "display": "15"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_keeps_legacy_video_backend_values(monkeypatch):
    from novelvideo import config
    from novelvideo.api.routes import model_credits

    monkeypatch.setattr(config, "SEEDANCE_FAST_MODEL", "doubao-fast")

    patch_quote(monkeypatch, model_credits, expected_model="doubao-fast", cost=10)

    result = await model_credits.get_generation_credit_cost(
        kind="video_backend",
        value="seedance_fast",
        user={"user_id": "usr_1"},
    )

    assert result == {"ok": True, "data": {"cost": 10, "display": "10"}}


@pytest.mark.asyncio
async def test_generation_credit_cost_route_rejects_unknown_image_selection():
    from novelvideo.api.routes import model_credits

    with pytest.raises(HTTPException) as exc_info:
        await model_credits.get_generation_credit_cost(
            kind="image_selection",
            value="unknown",
            user={"user_id": "usr_1"},
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "invalid image selection"


@pytest.mark.asyncio
async def test_generation_credit_cost_route_rejects_unknown_video_backend():
    from novelvideo.api.routes import model_credits

    with pytest.raises(HTTPException) as exc_info:
        await model_credits.get_generation_credit_cost(
            kind="video_backend",
            value="unknown_video_backend",
            user={"user_id": "usr_1"},
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "invalid video backend"


@pytest.mark.asyncio
async def test_generation_credit_cost_route_rejects_unconfigured_fixed_image_model(monkeypatch):
    from novelvideo.api.routes import model_credits

    monkeypatch.setattr(model_credits, "_fixed_image_cost_model", lambda kind: "")

    with pytest.raises(HTTPException) as exc_info:
        await model_credits.get_generation_credit_cost(
            kind="fixed_image",
            value="prop_reference",
            user={"user_id": "usr_1"},
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "generation model is not configured"
