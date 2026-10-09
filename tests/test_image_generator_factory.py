from __future__ import annotations

import pytest


def test_image_generator_factory_requires_real_provider_credentials(monkeypatch):
    from novelvideo.generators import image_generator

    class MissingCredentials:
        def __init__(self):
            raise ValueError("API key not set")

    monkeypatch.setattr(image_generator, "VolcengineImageGenerator", MissingCredentials)

    with pytest.raises(ValueError, match="API key not set"):
        image_generator.create_image_generator()


def test_image_generator_factory_allows_only_explicit_mock(monkeypatch):
    from novelvideo.generators import image_generator

    class ShouldNotConstruct:
        def __init__(self):
            raise AssertionError("real generator should not be constructed")

    monkeypatch.setattr(image_generator, "VolcengineImageGenerator", ShouldNotConstruct)

    result = image_generator.create_image_generator(use_mock=True)

    assert isinstance(result, image_generator.MockImageGenerator)


def test_legacy_provider_argument_does_not_enable_mock(monkeypatch):
    from novelvideo.generators import image_generator

    sentinel = object()
    monkeypatch.setattr(
        image_generator,
        "VolcengineImageGenerator",
        lambda: sentinel,
    )

    assert image_generator.create_image_generator("volcengine") is sentinel


def test_unknown_image_provider_fails_before_generation():
    from novelvideo.generators import image_generator

    with pytest.raises(ValueError, match="Unsupported image generator provider"):
        image_generator.create_image_generator(provider="unknown")
