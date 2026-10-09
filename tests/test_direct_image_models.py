import json

import pytest

from novelvideo.generators.direct_image_models import (
    _DIRECT_IMAGE_SAFE_SUBMIT_RETRY_EXCEPTIONS,
    DirectImageModel,
    DirectImageUpstreamError,
    _gemini_image_request_fields,
    _direct_image_request_fields,
    _validate_direct_image_parameters,
    bind_direct_image_generator_config,
)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_submit_read_budget_outlasts_a_synchronous_render():
    """本机不再给一次同步出图设读取上限，否则慢镜会被算成失败项。

    2026-09-21 的 12 镜真机批次在 120s 读超时下丢了 2/12 张分镜图
    （``direct image API submit result is unknown: ReadTimeout``）。这类错误被
    刻意排除在可重放集合之外，因为上游可能已经接单计费，所以能防住它的只有
    不设读预算。2026-10-03 角色资产重跑全部拿到中转站的 504，而本地 300s 上限
    还没到期，说明「本地等得不够」和「中转站提前放弃」是两回事，前者不该再发生。
    """

    from novelvideo.generators.image_request_policy import image_submit_timeout

    timeout = image_submit_timeout()
    assert timeout.read is None
    assert timeout.connect and timeout.connect > 0
    assert "ReadTimeout" not in _DIRECT_IMAGE_SAFE_SUBMIT_RETRY_EXCEPTIONS


def test_read_budget_can_be_restored_by_operator(monkeypatch):
    from novelvideo.generators.image_request_policy import image_submit_timeout

    monkeypatch.setenv("VILLAGE_CANVAS_IMAGE_READ_TIMEOUT_SECONDS", "300")

    assert image_submit_timeout().read == 300.0


def test_relay_side_400_counts_as_transient_for_the_shared_retry_policy():
    """分镜草图走的是 NewAPI 图像路径，共用这一份「值不值得重发」判定。"""

    from novelvideo.generators.image_request_policy import is_transient_upstream_error

    error_text = (
        "Village Infinite Canvas API Images 未返回图像数据: HTTP 400: "
        'body={"error":{"message":"由于我这边发生了错误，我未能生成图片。"}}'
    )

    assert is_transient_upstream_error(error_text) is True
    assert is_transient_upstream_error("HTTP 400: prompt too long") is False


def _model(
    upstream_model: str,
    *,
    protocol: str = "openai-images",
) -> DirectImageModel:
    return DirectImageModel(
        registry_id="image-test",
        label=upstream_model,
        upstream_model=upstream_model,
        base_url="https://image.test/v1",
        api_key="test-key",
        protocol=protocol,
        enabled=True,
        is_default=True,
    )


def test_gemini_image_request_uses_native_inline_data_contract(tmp_path):
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"reference")

    fields = _gemini_image_request_fields(
        model=_model("gemini-3-pro-image", protocol="gemini-image"),
        prompt="电影感古寺",
        aspect_ratio="16:9",
        image_size="2K",
        reference_paths=(str(reference),),
    )

    parts = fields["contents"][0]["parts"]
    assert parts[0]["inlineData"]["mimeType"] == "image/png"
    assert parts[0]["inlineData"]["data"] == "cmVmZXJlbmNl"
    assert parts[1] == {"text": "电影感古寺"}
    assert fields["generationConfig"] == {
        "responseModalities": ["TEXT", "IMAGE"],
        "imageConfig": {"aspectRatio": "16:9", "imageSize": "2K"},
    }


def test_gemini_image_execution_posts_generate_content_and_writes_inline_result(
    monkeypatch,
    tmp_path,
):
    import httpx
    import respx

    import novelvideo.generators.direct_image_models as direct_images

    model = _model("gemini-3-pro-image", protocol="gemini-image")
    output = tmp_path / "gemini.png"
    captured: dict[str, object] = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "done"},
                                {
                                    "inlineData": {
                                        "mimeType": "image/png",
                                        "data": "aGVsbG8=",
                                    }
                                },
                            ]
                        }
                    }
                ]
            },
        )

    with respx.mock(assert_all_called=False) as router:
        router.post(
            "https://image.test/v1beta/models/gemini-3-pro-image:generateContent"
        ).mock(side_effect=handler)
        path = _run(
            direct_images.generate_direct_image(
                model=model,
                prompt="古寺",
                output_path=output,
                aspect_ratio="16:9",
                image_size="2K",
                quality=None,
            )
        )

    assert path.read_bytes() == b"hello"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["payload"]["generationConfig"]["imageConfig"] == {
        "aspectRatio": "16:9",
        "imageSize": "2K",
    }


def test_openai_image_execution_emits_explicit_provider_cost(monkeypatch, tmp_path):
    import httpx
    import respx

    import novelvideo.generators.direct_image_models as direct_images

    model = _model("gpt-image-2")
    output = tmp_path / "openai.png"
    events: list[dict[str, object]] = []

    with respx.mock(assert_all_called=True) as router:
        router.post("https://image.test/v1/images/generations").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [{"b64_json": "aGVsbG8="}],
                    "usage": {"cost": {"credits": 2}},
                },
            )
        )
        path = _run(
            direct_images.generate_direct_image(
                model=model,
                prompt="古寺",
                output_path=output,
                aspect_ratio="16:9",
                image_size="2K",
                quality=None,
                on_provider_event=events.append,
            )
        )

    assert path.read_bytes() == b"hello"
    assert events == [
        {
            "stage": "provider_cost",
            "actual_cost": {"credits": 2},
            "cost_source": "result.usage.cost",
        }
    ]


def test_gpt_image_2_direct_request_preserves_canvas_resolution_contract():
    fields = _direct_image_request_fields(
        model=_model("gpt-image-2"),
        prompt="电影感古寺",
        aspect_ratio="16:9",
        image_size="2K",
        quality="medium",
    )

    assert fields["size"] == "2048x1152"
    assert fields["quality"] == "medium"
    assert fields["extra_fields"] == {
        "aspect_ratio": "16:9",
        "image_size": "2K",
        "resolution": "2k",
        "quality": "medium",
    }


def test_direct_image_picker_contract_exposes_model_center_default():
    from novelvideo.generators.direct_image_models import direct_image_model_option

    option = direct_image_model_option(_model("gpt-image-2"))

    assert option["isDefault"] is True
    assert option["is_default"] is True


def test_official_openai_image_request_omits_provider_extensions():
    fields = _direct_image_request_fields(
        model=_model("gpt-image-1"),
        prompt="cinematic hallway",
        aspect_ratio="16:9",
        image_size="2K",
        quality="high",
    )

    assert fields["size"] == "2048x1152"
    assert "extra_fields" not in fields


def test_direct_image_request_preserves_explicit_dimensions():
    fields = _direct_image_request_fields(
        model=_model("gpt-image-2"),
        prompt="wide concept frame",
        aspect_ratio="7:5",
        image_size="2048x1376",
        quality="auto",
    )

    assert fields["size"] == "2048x1376"
    assert fields["extra_fields"]["image_size"] == "2048x1376"


def test_direct_image_contract_rejects_custom_values_for_fixed_model():
    with pytest.raises(ValueError, match="不支持图片比例"):
        _validate_direct_image_parameters(
            model=_model("dall-e-2"),
            aspect_ratio="7:5",
            image_size="1K",
        )


def test_direct_image_execution_uses_cached_upstream_custom_contract(monkeypatch):
    import novelvideo.generators.direct_image_models as direct_images

    monkeypatch.setattr(
        "novelvideo.generators.direct_model_capability_cache.get_cached_direct_model_capability",
        lambda **_kwargs: {
            "modelMetadata": {
                "supported_modes": ["textToImage", "imageToImage"],
                "supported_sizes": ["2048x1376"],
                "supports_any_size": True,
                "aspect_ratio_options": ["2.39:1"],
                "supports_arbitrary_aspect_ratio": True,
            }
        },
    )
    model = _model("vendor-image-vNext")

    direct_images._validate_direct_image_parameters(
        model=model,
        aspect_ratio="7:5",
        image_size="3072x2194",
    )
    assert "image_to_image" in model.profile.modes


def test_explicit_empty_image_capabilities_do_not_restore_static_profile(monkeypatch):
    import novelvideo.generators.direct_image_models as direct_images

    monkeypatch.setattr(
        "novelvideo.generators.direct_model_capability_cache.get_cached_direct_model_capability",
        lambda **_kwargs: {
            "modelMetadata": {
                "supportedModes": [],
                "resolutionOptions": [],
                "aspectRatioOptions": [],
                "qualityOptions": [],
            }
        },
    )
    summary = direct_images.direct_image_model_option(_model("gpt-image-2"))

    assert summary["supportedModes"] == []
    assert summary["resolutionOptions"] == []
    assert summary["aspectRatioOptions"] == []
    assert summary["qualityOptions"] == []
    assert summary["enabled"] is False


def test_explicit_empty_image_shape_capabilities_are_omitted_at_transport(
    monkeypatch,
):
    import novelvideo.generators.direct_image_models as direct_images

    monkeypatch.setattr(
        "novelvideo.generators.direct_model_capability_cache.get_cached_direct_model_capability",
        lambda **_kwargs: {
            "modelMetadata": {
                "supported_modes": ["textToImage"],
                "resolutionOptions": [],
                "aspectRatioOptions": [],
            }
        },
    )
    model = _model("gpt-image-2")

    # A stale node may still carry the old preset values.  The selected model's
    # explicit empty contract makes those values optional instead of invalid.
    direct_images._validate_direct_image_parameters(
        model=model,
        aspect_ratio="16:9",
        image_size="2K",
    )
    fields = direct_images._direct_image_request_fields(
        model=model,
        prompt="provider decides",
        aspect_ratio="16:9",
        image_size="2K",
        quality="medium",
    )

    assert "size" not in fields
    assert fields.get("extra_fields") == {"quality": "medium"}


def test_direct_image_request_preserves_cached_custom_dimensions_and_ratio(monkeypatch):
    monkeypatch.setattr(
        "novelvideo.generators.direct_model_capability_cache.get_cached_direct_model_capability",
        lambda **_kwargs: {
            "modelMetadata": {
                "supports_any_size": True,
                "supports_arbitrary_aspect_ratio": True,
            }
        },
    )
    fields = _direct_image_request_fields(
        model=_model("vendor-image-vNext"),
        prompt="custom upstream frame",
        aspect_ratio="2.39:1",
        image_size="8192x3428",
        quality=None,
    )

    assert fields["size"] == "8192x3428"


def test_fixed_upstream_size_metadata_rejects_custom_dimension(monkeypatch):
    import novelvideo.generators.direct_image_models as direct_images

    monkeypatch.setattr(
        "novelvideo.generators.direct_model_capability_cache.get_cached_direct_model_capability",
        lambda **_kwargs: {
            "modelMetadata": {
                "supported_image_sizes": ["4K"],
                "supported_aspect_ratios": ["1:1", "16:9"],
            }
        },
    )
    model = _model("gpt-image2-4K-Native")

    with pytest.raises(ValueError, match="不支持图片尺寸"):
        direct_images._validate_direct_image_parameters(
            model=model,
            aspect_ratio="16:9",
            image_size="2048x1376",
        )


def test_direct_grid_binding_replaces_hidden_gateway(monkeypatch):
    import novelvideo.generators.direct_models as direct_models

    monkeypatch.setattr(
        direct_models, "ensure_direct_model_runtime_ready", lambda _model: None
    )
    result = bind_direct_image_generator_config(
        _model("gpt-image-2"),
        {
            "provider": "newapi",
            "api_key": "legacy-key",
            "model": "village-canvas-image",
            "base_url": "http://10.66.66.1:3000/v1",
            "rows": 3,
            "cols": 3,
        },
    )

    assert result == {
        "provider": "newapi",
        "api_key": "test-key",
        "model": "gpt-image-2",
        "base_url": "https://image.test/v1",
        "preserve_model_id": True,
        "rows": 3,
        "cols": 3,
    }


def test_direct_character_reference_bypasses_legacy_selection(monkeypatch, tmp_path):
    import novelvideo.generators.direct_image_models as direct_images
    import novelvideo.generators.image_generator as image_generator

    model = _model("gpt-image-2")
    calls = []

    async def fake_generate_direct_image(**kwargs):
        calls.append(kwargs)
        kwargs["output_path"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["output_path"].write_bytes(b"portrait")
        return kwargs["output_path"]

    monkeypatch.setattr(
        direct_images, "resolve_direct_image_model", lambda _value: model
    )
    monkeypatch.setattr(
        direct_images, "generate_direct_image", fake_generate_direct_image
    )
    monkeypatch.setattr(
        image_generator,
        "normalize_character_image_selection",
        lambda _value: (_ for _ in ()).throw(AssertionError("legacy path used")),
    )
    monkeypatch.setattr(
        "novelvideo.styles.project_style.build_project_style_snapshot",
        lambda *_args, **_kwargs: {
            "image_prompt": "新黑色电影",
            "negative_prompt": "低清晰度",
        },
    )

    paths = _run(
        image_generator.generate_character_reference_unified(
            character_name="林澈",
            appearance_prompt="深灰风衣，左手旧银表，疲惫但坚定",
            output_dir=str(tmp_path / "out"),
            count=1,
            model="direct/image-test",
            project_dir=str(tmp_path),
            raise_on_error=True,
        )
    )

    assert paths == [str(tmp_path / "out" / "reference_portrait.png")]
    assert calls[0]["aspect_ratio"] == "3:4"
    assert calls[0]["reference_paths"] == ()
    assert "深灰风衣" in calls[0]["prompt"]
    assert (tmp_path / "prompts/characters/林澈_portrait.prompt.txt").is_file()


def test_four_view_generation_sends_front_photo_to_provider(monkeypatch, tmp_path):
    import novelvideo.generators.direct_image_models as direct_images
    import novelvideo.generators.image_generator as image_generator

    model = _model("gpt-image-2")
    reference = tmp_path / "front.png"
    reference.write_bytes(b"front-photo")
    calls = []

    async def generate(**kwargs):
        calls.append(kwargs)
        kwargs["output_path"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["output_path"].write_bytes(b"sheet")

    monkeypatch.setattr(
        direct_images, "resolve_direct_image_model", lambda _value: model
    )
    monkeypatch.setattr(direct_images, "generate_direct_image", generate)
    monkeypatch.setattr(
        "novelvideo.styles.project_style.build_project_style_snapshot",
        lambda *_args, **_kwargs: {"image_prompt": "", "negative_prompt": ""},
    )
    _run(
        image_generator.generate_character_reference_unified(
            character_name="hero",
            appearance_prompt="dark coat",
            output_dir=str(tmp_path / "out"),
            count=1,
            model="direct/image-test",
            project_dir=str(tmp_path),
            prompt_template="four_view",
            reference_image_path=str(reference),
            raise_on_error=True,
        )
    )
    assert calls[0]["reference_paths"] == (str(reference),)
    assert calls[0]["aspect_ratio"] == "4:3"
    assert "Preserve the face" in calls[0]["prompt"]


def test_missing_front_reference_stops_before_provider_resolution(
    monkeypatch, tmp_path
):
    import novelvideo.generators.direct_image_models as direct_images
    import novelvideo.generators.image_generator as image_generator

    monkeypatch.setattr(
        direct_images,
        "resolve_direct_image_model",
        lambda _value: pytest.fail("provider must not be resolved"),
    )
    with pytest.raises(ValueError, match="参考图不存在或为空"):
        _run(
            image_generator.generate_character_reference_unified(
                character_name="hero",
                appearance_prompt="dark coat",
                output_dir=str(tmp_path),
                model="direct/image-test",
                prompt_template="four_view",
                reference_image_path=str(tmp_path / "missing.png"),
            )
        )


def test_direct_identity_keeps_face_and_costume_references(monkeypatch, tmp_path):
    import novelvideo.generators.direct_image_models as direct_images
    import novelvideo.generators.image_generator as image_generator

    model = _model("gpt-image-2")
    face = tmp_path / "face.png"
    costume = tmp_path / "costume.png"
    face.write_bytes(b"face")
    costume.write_bytes(b"costume")
    output = tmp_path / "identity.png"
    calls = []

    async def fake_generate_direct_image(**kwargs):
        calls.append(kwargs)
        kwargs["output_path"].write_bytes(b"identity")
        return kwargs["output_path"]

    monkeypatch.setattr(
        direct_images, "resolve_direct_image_model", lambda _value: model
    )
    monkeypatch.setattr(
        direct_images, "generate_direct_image", fake_generate_direct_image
    )
    monkeypatch.setattr(
        "novelvideo.styles.project_style.build_project_style_snapshot",
        lambda *_args, **_kwargs: {"image_prompt": "新黑色电影", "negative_prompt": ""},
    )

    result = _run(
        image_generator.generate_identity_image_unified(
            character_name="林澈",
            identity_prompt="公交司机身份，深绿色制服",
            reference_image_path=str(face),
            costume_image_path=str(costume),
            output_path=str(output),
            model="direct/image-test",
            project_dir=str(tmp_path),
            identity_name="公交司机",
            raise_on_error=True,
        )
    )

    assert result is True
    assert calls[0]["aspect_ratio"] == "3:4"
    assert calls[0]["reference_paths"] == (str(face), str(costume))
    assert output.read_bytes() == b"identity"


def test_direct_character_error_redacts_api_key(monkeypatch, tmp_path):
    import novelvideo.generators.direct_image_models as direct_images
    import novelvideo.generators.image_generator as image_generator

    model = _model("gpt-image-2")

    async def fake_generate_direct_image(**_kwargs):
        raise RuntimeError("upstream body leaked test-key")

    monkeypatch.setattr(
        direct_images, "resolve_direct_image_model", lambda _value: model
    )
    monkeypatch.setattr(
        direct_images, "generate_direct_image", fake_generate_direct_image
    )
    monkeypatch.setattr(
        "novelvideo.styles.project_style.build_project_style_snapshot",
        lambda *_args, **_kwargs: {"image_prompt": "", "negative_prompt": ""},
    )

    import pytest

    with pytest.raises(RuntimeError) as caught:
        _run(
            image_generator.generate_character_reference_unified(
                character_name="林澈",
                appearance_prompt="portrait",
                output_dir=str(tmp_path / "out"),
                count=1,
                model="direct/image-test",
                project_dir=str(tmp_path),
                raise_on_error=True,
            )
        )
    assert "test-key" not in str(caught.value)


def test_direct_character_error_still_carries_the_diagnostic(monkeypatch, tmp_path):
    """Redacting the message must not drop the structured provider diagnosis."""

    import novelvideo.generators.direct_image_models as direct_images
    import novelvideo.generators.image_generator as image_generator

    model = _model("gpt-image-2")

    async def fake_generate_direct_image(**_kwargs):
        raise DirectImageUpstreamError(
            "direct image API HTTP 400: leaked test-key",
            error_code="DIRECT_IMAGE_CONTENT_MODERATION_FAILED",
            stage="submit",
            retryable=False,
            suggested_action="上游内容审核拦截（category=provider_moderation）。",
            http_status=400,
        )

    monkeypatch.setattr(
        direct_images, "resolve_direct_image_model", lambda _value: model
    )
    monkeypatch.setattr(
        direct_images, "generate_direct_image", fake_generate_direct_image
    )
    monkeypatch.setattr(
        "novelvideo.styles.project_style.build_project_style_snapshot",
        lambda *_args, **_kwargs: {"image_prompt": "", "negative_prompt": ""},
    )

    with pytest.raises(RuntimeError) as caught:
        _run(
            image_generator.generate_character_reference_unified(
                character_name="林澈",
                appearance_prompt="portrait",
                output_dir=str(tmp_path / "out"),
                count=1,
                model="direct/image-test",
                project_dir=str(tmp_path),
                raise_on_error=True,
            )
        )

    assert "test-key" not in str(caught.value)
    assert caught.value.provider_error_metadata["error_code"] == (
        "DIRECT_IMAGE_CONTENT_MODERATION_FAILED"
    )


def test_disabled_direct_image_model_is_not_resolvable(monkeypatch):
    import novelvideo.generators.direct_image_models as direct_images

    disabled = _model("gpt-image-2")
    disabled = disabled.__class__(
        registry_id=disabled.registry_id,
        label=disabled.label,
        upstream_model=disabled.upstream_model,
        base_url=disabled.base_url,
        api_key=disabled.api_key,
        protocol=disabled.protocol,
        enabled=False,
        is_default=disabled.is_default,
    )
    monkeypatch.setattr(direct_images, "list_direct_image_models", lambda: (disabled,))

    assert direct_images.resolve_direct_image_model("direct/image-test") is None


def test_midjourney_contract_exposes_advanced_switches():
    from novelvideo.generators.direct_image_models import direct_image_model_option

    option = direct_image_model_option(_model("mj-v8.2"))

    assert option["profile"] == "midjourney"
    schema = {item["key"]: item for item in option["advancedParamsSchema"]}
    assert set(schema) == {"personalisation", "stylize", "weird", "chaos"}
    assert schema["stylize"]["max"] == 1000
    assert schema["stylize"]["step"] == 50
    assert schema["weird"]["defaultValue"] == 50
    assert option["advancedParamDefaults"]["chaos"] == 5
    # A Midjourney contract frames through its ratio setting only.
    assert option["resolutionOptions"] == []


def test_midjourney_advanced_settings_render_as_prompt_flags():
    fields = _direct_image_request_fields(
        model=_model("mj-v8.2"),
        prompt="赛博禅意庭院",
        aspect_ratio="16:9",
        image_size="",
        quality="auto",
        advanced_settings={"stylize": 400, "chaos": 10, "personalisation": "abc123"},
    )

    assert fields["prompt"] == "赛博禅意庭院 --p abc123 --stylize 400 --chaos 10"
    # Only flag-backed parameters are switches; nothing leaks into the body.
    assert "size" not in fields
    assert "stylize" not in fields
    assert "chaos" not in fields


def test_unknown_advanced_settings_never_reach_the_upstream():
    from novelvideo.generators.direct_image_models import _apply_advanced_settings

    fields = {"prompt": "庭院"}
    _apply_advanced_settings(fields, _model("mj-v8.2").profile, {"nope": "x"})

    assert fields == {"prompt": "庭院"}


def test_advanced_values_are_clamped_and_snapped_to_the_declared_contract():
    from novelvideo.generators.direct_image_capabilities import (
        DIRECT_IMAGE_MJ_ADVANCED_PARAMS,
    )

    profile = _model("mj-v8.2").profile
    resolved = profile.resolve_advanced_settings({"stylize": "1234", "weird": 47})

    # stylize clamps to its maximum, weird snaps to the declared 50 step.
    assert resolved["stylize"] == 1000
    assert resolved["weird"] == 50
    # Untouched parameters still carry their declared default, because
    # Midjourney's own defaults differ from the panel's.
    assert resolved["chaos"] == 5
    # An empty string is not a value: it must not become a ``--p`` switch.
    assert "personalisation" not in resolved
    assert "stylize" in {param.key for param in DIRECT_IMAGE_MJ_ADVANCED_PARAMS}


def test_gpt_image_contract_declares_no_advanced_parameters():
    from novelvideo.generators.direct_image_models import direct_image_model_option

    option = direct_image_model_option(_model("gpt-image-2"))

    assert option["advancedParamsSchema"] == []
    assert option["advancedParamDefaults"] == {}


# --- direct-channel content-policy handling ---------------------------------
# HK relays flatten length/format/reference failures into one Chinese "安全政策"
# sentence.  The direct path used to raise a bare RuntimeError for it, so the
# canvas showed a machine blob and no retry ever happened.

SAFETY_BLOCK_BODY = (
    '{"error":{"message":"您的请求无法用于生成图像。该请求可能因安全政策被拦截，'
    '或不适合进行图像生成。","type":"invalid_request_error","param":"","code":400}}'
)
SAFETY_BLOCK_ESCAPED_BODY = json.dumps(
    {
        "error": {
            "message": "您的请求无法用于生成图像。该请求可能因安全政策被拦截，或不适合进行图像生成。",
            "type": "invalid_request_error",
            "param": "",
            "code": 400,
        }
    }
)
EXPLICIT_MODERATION_BODY = (
    '{"error":{"message":"request blocked","code":"moderation_blocked",'
    '"safety_violations":["sexual"]}}'
)
IMAGE_OK_BODY = '{"data":[{"b64_json":"aGVsbG8="}]}'


def _run_direct_image_against(
    monkeypatch, bodies, tmp_path, *, prompt="古寺", references=()
):
    """Serve ``bodies`` in order from a fake direct endpoint and return the outcome."""

    import httpx
    import respx

    import novelvideo.generators.direct_image_models as direct_images

    model = _model("gpt-image-2")
    calls: list[int] = []

    def handler(_request):
        index = len(calls)
        calls.append(index)
        body = bodies[index] if index < len(bodies) else bodies[-1]
        if body is None:
            return httpx.Response(
                200, text=IMAGE_OK_BODY, headers={"content-type": "application/json"}
            )
        status, text = body if isinstance(body, tuple) else (400, body)
        return httpx.Response(
            status, text=text, headers={"content-type": "application/json"}
        )

    monkeypatch.setattr(
        direct_images, "resolve_direct_image_model", lambda _value: model
    )
    # 5xx 重发之间的等待是给中转站留恢复时间，测试里不需要真的等。
    monkeypatch.setattr(
        direct_images, "DIRECT_IMAGE_RESPONSE_RETRY_BACKOFF_SECONDS", (0.0, 0.0)
    )
    output = tmp_path / "direct.png"

    with respx.mock(assert_all_called=False) as router:
        router.post("https://image.test/v1/images/generations").mock(
            side_effect=handler
        )
        try:
            path = _run(
                direct_images.generate_direct_image(
                    model=model,
                    prompt=prompt,
                    output_path=output,
                    aspect_ratio="16:9",
                    image_size="2K",
                    quality=None,
                    reference_paths=references,
                )
            )
        except direct_images.DirectImageUpstreamError as exc:
            return {"error": exc, "calls": len(calls)}
        except RuntimeError as exc:
            return {"error": exc, "calls": len(calls)}
    return {"path": path, "calls": len(calls)}


def test_generic_relay_safety_block_retries_once_and_can_recover(monkeypatch, tmp_path):
    outcome = _run_direct_image_against(
        monkeypatch, [SAFETY_BLOCK_BODY, None], tmp_path
    )

    assert outcome["calls"] == 2
    assert outcome["path"].read_bytes() == b"hello"


def test_generic_relay_safety_block_reports_retryable_diagnostic(monkeypatch, tmp_path):
    outcome = _run_direct_image_against(
        monkeypatch, [SAFETY_BLOCK_BODY, SAFETY_BLOCK_BODY], tmp_path
    )

    error = outcome["error"]
    assert outcome["calls"] == 2
    assert error.error_code == "DIRECT_IMAGE_SAFETY_BLOCK"
    metadata = error.provider_error_metadata
    assert metadata["retryable"] is True
    assert metadata["stage"] == "submit"
    assert metadata["http_status"] == 400
    assert "安全政策" in str(metadata["suggested_action"])


def test_ascii_escaped_safety_wrapper_is_still_classified(monkeypatch, tmp_path):
    outcome = _run_direct_image_against(
        monkeypatch, [SAFETY_BLOCK_ESCAPED_BODY, None], tmp_path
    )

    assert outcome["calls"] == 2
    assert outcome["path"].read_bytes() == b"hello"


def test_explicit_upstream_moderation_never_resends(monkeypatch, tmp_path):
    outcome = _run_direct_image_against(
        monkeypatch, [EXPLICIT_MODERATION_BODY, None], tmp_path
    )

    error = outcome["error"]
    assert outcome["calls"] == 1
    assert error.error_code == "DIRECT_IMAGE_CONTENT_MODERATION_FAILED"
    assert error.provider_error_metadata["retryable"] is False
    assert "sexual" in str(error.provider_error_metadata["suggested_action"])


def test_safety_retry_can_be_disabled_by_operator(monkeypatch, tmp_path):
    monkeypatch.setenv("VILLAGE_CANVAS_DIRECT_IMAGE_SAFETY_RETRY", "0")

    outcome = _run_direct_image_against(
        monkeypatch, [SAFETY_BLOCK_BODY, None], tmp_path
    )

    assert outcome["calls"] == 1
    assert outcome["error"].error_code == "DIRECT_IMAGE_SAFETY_BLOCK_RETRY"


def test_unrelated_failure_keeps_the_plain_http_error(monkeypatch, tmp_path):
    outcome = _run_direct_image_against(
        monkeypatch, ['{"error":{"message":"internal explosion"}}'], tmp_path
    )

    assert outcome["calls"] == 1
    error = outcome["error"]
    assert type(error) is RuntimeError


# --- 中转站提前放弃（5xx）-----------------------------------------------------
# 2026-10-03：角色资产重跑时 5 个角色全部拿到中转站的
# ``HTTP 504 图片生成超时``，而本地读取上限（当时 300s）根本没到期 —— 是中转站
# 自己提前放弃，不是我们等得不够。这类 5xx 是「还没出结果」，按 T-165 已对
# NewAPI 图像路径生效的同一条策略原样重发，间隔 20s/60s，共 3 次付费上限。

RELAY_TIMEOUT_BODY = (
    '{"error":{"message":"图片生成超时，请稍后再试。",'
    '"type":"server_error","param":"","code":504}}'
)


def test_upstream_504_is_resent_and_can_recover(monkeypatch, tmp_path):
    outcome = _run_direct_image_against(
        monkeypatch, [(504, RELAY_TIMEOUT_BODY), None], tmp_path
    )

    assert outcome["calls"] == 2
    assert outcome["path"].read_bytes() == b"hello"


def test_upstream_504_gives_up_after_the_bounded_resends(monkeypatch, tmp_path):
    outcome = _run_direct_image_against(
        monkeypatch,
        [(504, RELAY_TIMEOUT_BODY)] * 3,
        tmp_path,
    )

    assert outcome["calls"] == 3
    error = outcome["error"]
    assert type(error) is RuntimeError
    assert "HTTP 504" in str(error)


def test_upstream_5xx_resend_can_be_disabled_by_operator(monkeypatch, tmp_path):
    monkeypatch.setenv("VILLAGE_CANVAS_DIRECT_IMAGE_5XX_RETRY", "0")

    outcome = _run_direct_image_against(
        monkeypatch, [(504, RELAY_TIMEOUT_BODY), None], tmp_path
    )

    assert outcome["calls"] == 1
    assert "HTTP 504" in str(outcome["error"])


def test_explicit_moderation_still_never_resends_alongside_5xx_retry(
    monkeypatch, tmp_path
):
    outcome = _run_direct_image_against(
        monkeypatch, [EXPLICIT_MODERATION_BODY, None], tmp_path
    )

    assert outcome["calls"] == 1
    assert outcome["error"].error_code == "DIRECT_IMAGE_CONTENT_MODERATION_FAILED"


# --- 中转站自认内部出错（HTTP 400）---------------------------------------------
# 2026-10-03 身份参考图现场：``400 {"message":"由于我这边发生了错误，我未能生成图片。"}``
# 这条不是我们的请求有问题，重发一次就过了。按同一套有限次策略原样重发。

RELAY_SIDE_400_BODY = (
    '{"error":{"message":"由于我这边发生了错误，我未能生成图片。",'
    '"type":"invalid_request_error","param":"","code":"bad_request"}}'
)


def test_relay_side_400_is_resent_and_can_recover(monkeypatch, tmp_path):
    outcome = _run_direct_image_against(
        monkeypatch, [(400, RELAY_SIDE_400_BODY), None], tmp_path
    )

    assert outcome["calls"] == 2
    assert outcome["path"].read_bytes() == b"hello"


def test_relay_side_400_gives_up_after_the_bounded_resends(monkeypatch, tmp_path):
    outcome = _run_direct_image_against(
        monkeypatch, [(400, RELAY_SIDE_400_BODY)] * 3, tmp_path
    )

    assert outcome["calls"] == 3
    assert "HTTP 400" in str(outcome["error"])


def _transient_transport_failure(
    exc: BaseException,
    *,
    attempts: int,
    monkeypatch,
    tmp_path,
):
    """Submit ``attempts`` times raising ``exc``, then succeed."""

    import httpx
    import respx

    import novelvideo.generators.direct_image_models as direct_images

    model = _model("gpt-image-2")
    calls: list[int] = []

    def handler(_request):
        calls.append(len(calls))
        if len(calls) <= attempts:
            raise exc
        return httpx.Response(
            200, text=IMAGE_OK_BODY, headers={"content-type": "application/json"}
        )

    # Retry backoff is real sleeping; keep the test at milliseconds.
    monkeypatch.setattr(
        direct_images, "DIRECT_IMAGE_TRANSIENT_BACKOFF_SECONDS", (0.0, 0.0, 0.0)
    )
    output = tmp_path / "direct-transient.png"

    with respx.mock(assert_all_called=False) as router:
        router.post("https://image.test/v1/images/generations").mock(
            side_effect=handler
        )
        try:
            path = _run(
                direct_images.generate_direct_image(
                    model=model,
                    prompt="古寺",
                    output_path=output,
                    aspect_ratio="16:9",
                    image_size="2K",
                    quality=None,
                )
            )
        except direct_images.DirectImageUpstreamError as error:
            return {"error": error, "calls": len(calls)}
    return {"path": path, "calls": len(calls)}


def test_server_disconnect_is_not_retried_after_ambiguous_submit(monkeypatch, tmp_path):
    """提交中途断线可能已经扣费，不能自动重发一份逐字节相同的付费请求。"""

    import httpx

    outcome = _transient_transport_failure(
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        attempts=2,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )

    error = outcome["error"]
    assert outcome["calls"] == 1
    assert error.error_code == "DIRECT_IMAGE_SUBMIT_RESULT_UNKNOWN"
    metadata = error.provider_error_metadata
    assert metadata["retryable"] is False
    assert metadata["stage"] == "submit"
    assert "无法确认" in str(metadata["suggested_action"])


def test_connect_failure_is_retried_before_failing(monkeypatch, tmp_path):
    """连接尚未建立时没有上游请求可计费，重试是安全的。"""

    import httpx

    outcome = _transient_transport_failure(
        httpx.ConnectError("connection refused"),
        attempts=2,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )

    assert outcome["calls"] == 3
    assert outcome["path"].read_bytes() == b"hello"


def test_exhausted_transport_retry_reports_a_task_facing_diagnostic(
    monkeypatch, tmp_path
):
    import httpx

    outcome = _transient_transport_failure(
        httpx.ConnectError("connection refused"),
        attempts=99,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )

    error = outcome["error"]
    assert outcome["calls"] == 3
    assert error.error_code == "DIRECT_IMAGE_TRANSPORT_FAILURE"
    metadata = error.provider_error_metadata
    assert metadata["retryable"] is True
    assert metadata["stage"] == "submit"
    assert "重试" in str(metadata["suggested_action"])
    assert "connection refused" in str(error)


def test_local_protocol_error_is_never_retried(monkeypatch, tmp_path):
    """``LocalProtocolError`` 是「我们自己的请求写错了」，重发不会变好。"""

    import httpx

    outcome = _transient_transport_failure(
        httpx.LocalProtocolError("too many redirects"),
        attempts=99,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )

    error = outcome["error"]
    assert outcome["calls"] == 1
    assert error.error_code == "DIRECT_IMAGE_TRANSPORT_FAILURE"
    assert error.provider_error_metadata["stage"] == "submit"


def test_channel_fallback_stays_off_without_an_operator_pin(monkeypatch, tmp_path):
    monkeypatch.delenv("VILLAGE_CANVAS_DIRECT_IMAGE_FALLBACK", raising=False)

    outcome = _run_direct_image_against(
        monkeypatch, [SAFETY_BLOCK_BODY, SAFETY_BLOCK_BODY, None], tmp_path
    )

    # Two attempts on the primary only: a fallback is a second paid request, so
    # it must never happen by default.
    assert outcome["calls"] == 2
    assert outcome["error"].error_code == "DIRECT_IMAGE_SAFETY_BLOCK"


def test_channel_fallback_uses_the_pinned_backup_slot(monkeypatch, tmp_path):
    import dataclasses

    import httpx
    import respx

    import novelvideo.generators.direct_image_models as direct_images

    primary = _model("gpt-image-2")
    backup = dataclasses.replace(
        primary,
        registry_id="image-backup",
        label="gpt-image-2.5-sunburst-4K-Native",
        base_url="https://backup.test/v1",
        is_default=False,
    )
    monkeypatch.setenv("VILLAGE_CANVAS_DIRECT_IMAGE_FALLBACK", backup.registry_id)
    monkeypatch.setattr(
        direct_images,
        "resolve_direct_image_model",
        lambda value: backup if value == backup.registry_id else primary,
    )

    seen: list[str] = []

    def primary_handler(_request):
        seen.append("primary")
        return httpx.Response(
            400, text=SAFETY_BLOCK_BODY, headers={"content-type": "application/json"}
        )

    def backup_handler(_request):
        seen.append("backup")
        return httpx.Response(
            200, text=IMAGE_OK_BODY, headers={"content-type": "application/json"}
        )

    output = tmp_path / "fallback.png"
    with respx.mock(assert_all_called=False) as router:
        router.post("https://image.test/v1/images/generations").mock(
            side_effect=primary_handler
        )
        router.post("https://backup.test/v1/images/generations").mock(
            side_effect=backup_handler
        )
        path = _run(
            direct_images.generate_direct_image(
                model=primary,
                prompt="古寺",
                output_path=output,
                aspect_ratio="16:9",
                image_size="2K",
                quality=None,
            )
        )

    assert seen == ["primary", "primary", "backup"]
    assert path.read_bytes() == b"hello"


def test_mask_edit_wrapper_keeps_the_direct_channel_diagnostic(monkeypatch, tmp_path):
    """jobs.py re-wraps the failure, so it must carry the metadata forward."""

    import novelvideo.freezone.jobs as freezone_jobs
    import novelvideo.generators.direct_image_models as direct_images

    base = tmp_path / "base.png"
    mask = tmp_path / "mask.png"
    base.write_bytes(b"base")
    mask.write_bytes(b"mask")

    def _raise(**_kwargs):
        raise DirectImageUpstreamError(
            "direct image API HTTP 400: 您的请求无法用于生成图像。",
            error_code="DIRECT_IMAGE_CONTENT_MODERATION_FAILED",
            stage="submit",
            retryable=False,
            suggested_action="上游内容审核拦截（category=provider_moderation）。",
            http_status=400,
        )

    monkeypatch.setattr(
        direct_images,
        "resolve_direct_image_model",
        lambda _value: _model("gpt-image-2"),
    )
    monkeypatch.setattr(direct_images, "generate_direct_image", _raise)

    with pytest.raises(RuntimeError) as excinfo:
        _run(
            freezone_jobs.run_freezone_mask_edit(
                project_dir=tmp_path,
                job_id="mask-1",
                base_path=str(base),
                mask_path=str(mask),
                prompt="擦除招牌",
                provider="direct",
                model="direct/image-test",
            )
        )

    metadata = excinfo.value.provider_error_metadata
    assert metadata["error_code"] == "DIRECT_IMAGE_CONTENT_MODERATION_FAILED"
    assert metadata["retryable"] is False
    assert "图像擦除失败" in str(excinfo.value)
