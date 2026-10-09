def test_scene_360_provider_defaults_to_newapi_when_env_is_empty(monkeypatch):
    from novelvideo import stage_asset_tasks

    monkeypatch.setenv("SCENE_360_IMAGE_PROVIDER", "")
    monkeypatch.setenv("SCENE_360_PROVIDER", "")
    monkeypatch.setenv("NANOBANANA_PROVIDER", "")

    assert stage_asset_tasks.resolve_scene_360_image_provider() == "newapi"


def test_scene_360_prefers_the_explicit_direct_image_registry(monkeypatch):
    from novelvideo import stage_asset_tasks
    from novelvideo.generators import direct_image_models

    configured = type("ConfiguredImage", (), {"catalog_id": "direct/image-test"})()
    monkeypatch.setattr(
        direct_image_models,
        "resolve_direct_image_model",
        lambda _model: configured,
    )

    assert stage_asset_tasks.resolve_scene_360_image_provider("openrouter") == "direct"
    assert stage_asset_tasks.resolve_scene_360_image_model("openrouter") == "direct/image-test"


def test_scene_360_legacy_provider_does_not_invent_a_model(monkeypatch):
    from novelvideo import stage_asset_tasks
    from novelvideo.generators import direct_image_models

    monkeypatch.setattr(direct_image_models, "resolve_direct_image_model", lambda _model: None)
    for name in (
        "SCENE_360_HUIMENG_MODEL",
        "HUIMENG_IMAGE_MODEL",
        "OPENAI_IMAGE_MODEL",
        "SCENE_360_IMAGE_MODEL",
        "NEWAPI_IMAGE_MODEL",
        "SCENE_360_OPENROUTER_MODEL",
        "OPENROUTER_GPT_IMAGE2_MODEL",
    ):
        monkeypatch.setenv(name, "")

    assert stage_asset_tasks.resolve_scene_360_image_model("openai") == ""
    assert stage_asset_tasks.resolve_scene_360_image_model("newapi") == ""
    assert stage_asset_tasks.resolve_scene_360_image_model("openrouter") == ""


def test_scene_360_error_excerpt_removes_subprocess_traceback() -> None:
    from novelvideo.stage_asset_tasks import _scene_360_error_excerpt

    message = (
        'Traceback (most recent call last):\n'
        '  File "C:\\Users\\example\\Desktop\\村长无限画布\\scene_360_builder.py", line 1\n'
        'RuntimeError: reference image preparation failed: Cloudinary media relay upload rejected '
        '(HTTP 401): Invalid cloud_name demo'
    )

    excerpt = _scene_360_error_excerpt(message)

    assert excerpt.startswith("reference image preparation failed:")
    assert "Traceback" not in excerpt
    assert "scene_360_builder.py" not in excerpt
