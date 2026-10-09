from __future__ import annotations

import pytest


def test_image_generation_selection_requires_an_explicit_legacy_model(monkeypatch):
    from novelvideo import config

    monkeypatch.setenv("NEWAPI_NANOBANANA2_ENABLED", "false")
    monkeypatch.setitem(config.IMAGE_GENERATION_SELECTIONS["newapi_gpt_image2"], "model", "")
    monkeypatch.setitem(config.IMAGE_GENERATION_SELECTIONS["newapi_nanobanana2"], "model", "")
    options = config.image_generation_selection_options()

    assert options == {}
    with pytest.raises(ValueError, match="未配置图片模型"):
        config.normalize_image_generation_selection("unknown")


def test_explicit_image_selection_rejects_unknown_without_default_switch(monkeypatch):
    from novelvideo import config

    monkeypatch.setenv("NEWAPI_NANOBANANA2_ENABLED", "false")
    monkeypatch.setitem(config.IMAGE_GENERATION_SELECTIONS["newapi_gpt_image2"], "model", "")

    with pytest.raises(ValueError, match="图片模型选择不可用：unknown-model"):
        config.normalize_explicit_image_generation_selection("unknown-model")

    monkeypatch.setitem(
        config.IMAGE_GENERATION_SELECTIONS["newapi_gpt_image2"],
        "model",
        "configured-image-model",
    )
    assert config.normalize_explicit_image_generation_selection("openrouter_gpt_image2") == (
        "newapi_gpt_image2"
    )


def test_image_generation_selection_exposes_nb2_only_when_explicitly_enabled(monkeypatch):
    from novelvideo import config

    monkeypatch.setenv("NEWAPI_NANOBANANA2_ENABLED", "true")
    monkeypatch.setitem(
        config.IMAGE_GENERATION_SELECTIONS["newapi_gpt_image2"], "model", "configured-g2"
    )
    monkeypatch.setitem(
        config.IMAGE_GENERATION_SELECTIONS["newapi_nanobanana2"], "model", "configured-nb2"
    )

    assert config.image_generation_selection_options() == {
        "newapi_gpt_image2": "Village Infinite Canvas Image",
        "newapi_nanobanana2": "LingShan-NB-2",
    }
    assert config.normalize_image_generation_selection("huimeng_nanobanana2") == (
        "newapi_nanobanana2"
    )
    assert config.normalize_image_generation_selection("openrouter_nanobanana2") == (
        "newapi_nanobanana2"
    )


def test_character_image_selection_hides_disabled_nb2_and_falls_back_to_g2(monkeypatch):
    from novelvideo import config

    monkeypatch.setenv("NEWAPI_NANOBANANA2_ENABLED", "false")
    monkeypatch.setitem(
        config.IMAGE_GENERATION_SELECTIONS["newapi_gpt_image2"], "model", "configured-g2"
    )
    monkeypatch.setattr(config, "CHARACTER_IMAGE_SELECTION", "newapi_gpt_image2")
    options = config.character_image_selection_options()

    assert options == {"newapi_gpt_image2": "Village Infinite Canvas Image"}
    assert config.normalize_character_image_selection("huimeng_gpt_image2") == (
        "newapi_gpt_image2"
    )
    assert config.normalize_character_image_selection("huimeng_nanobanana2") == (
        "newapi_gpt_image2"
    )
    assert config.normalize_character_image_selection("seedream") in options
