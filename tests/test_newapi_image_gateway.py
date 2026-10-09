import base64
import importlib
import logging
from types import SimpleNamespace

import pytest

from novelvideo.shared.billing_errors import InsufficientCreditsError

pytestmark = pytest.mark.m04


def _isolate_settings_db(monkeypatch, tmp_path):
    import novelvideo.config as config

    state_dir = str(tmp_path / "state")
    monkeypatch.delenv("MODEL_GATEWAY_MODE", raising=False)
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", state_dir)
    monkeypatch.setattr(config, "STATE_DIR", state_dir)


@pytest.fixture(autouse=True)
def _isolated_model_gateway(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    # This module tests low-level environment-driven gateway adapters. CE
    # database precedence is covered in test_model_gateway_settings.py.
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "postgresql://test-control-plane")


def _patch_scene_newapi_gateway(
    monkeypatch,
    *,
    api_key: str = "newapi-token",
    base_url: str = "http://newapi.test/v1",
) -> None:
    import novelvideo.config as config

    monkeypatch.setattr(
        config,
        "get_effective_newapi_gateway_config",
        lambda: SimpleNamespace(api_key=api_key, base_url=base_url),
    )


def test_dc_image_2_selection_maps_to_newapi_gpt_image2(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_API_KEY", "newapi-token")
    monkeypatch.setenv("NEWAPI_BASE_URL", "http://newapi.test/v1")
    monkeypatch.setenv("NEWAPI_IMAGE_MODEL", "LingShan-G2")
    monkeypatch.setenv("DEFAULT_CHARACTER_IMAGE_SELECTION", "newapi_gpt_image2")

    import novelvideo.config as config

    config = importlib.reload(config)

    assert config.character_image_selection_options()["newapi_gpt_image2"] == "Village Infinite Canvas Image"
    assert config.get_character_image_selection() == "newapi_gpt_image2"

    image_config = config.get_grid_generation_config(selection_override="newapi_gpt_image2")
    assert image_config["provider"] == "newapi"
    assert image_config["api_key"] == "newapi-token"
    assert image_config["base_url"] == "http://newapi.test/v1"
    assert image_config["model"] == "LingShan-G2"


def test_dc_banana_2_selection_maps_to_newapi_nanobanana2(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_API_KEY", "newapi-token")
    monkeypatch.setenv("NEWAPI_BASE_URL", "http://newapi.test/v1")
    monkeypatch.setenv("NEWAPI_NANOBANANA2_MODEL", "LingShan-NB-2")
    monkeypatch.setenv("NEWAPI_NANOBANANA2_ENABLED", "true")
    monkeypatch.setenv("DEFAULT_CHARACTER_IMAGE_SELECTION", "newapi_nanobanana2")

    import novelvideo.config as config

    config = importlib.reload(config)

    assert config.character_image_selection_options()["newapi_nanobanana2"] == "LingShan-NB-2"
    assert config.get_character_image_selection() == "newapi_nanobanana2"

    image_config = config.get_grid_generation_config(selection_override="newapi_nanobanana2")
    assert image_config["provider"] == "newapi"
    assert image_config["api_key"] == "newapi-token"
    assert image_config["base_url"] == "http://newapi.test/v1"
    assert image_config["model"] == "LingShan-NB-2"


def test_fixed_asset_image_providers_default_to_newapi_when_env_is_empty(monkeypatch):
    for key in (
        "PROP_REF_IMAGE_PROVIDER",
        "SCENE_MASTER_IMAGE_PROVIDER",
        "SCENE_REVERSE_MASTER_IMAGE_PROVIDER",
        "SCENE_360_IMAGE_PROVIDER",
    ):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("NEWAPI_IMAGE_MODEL", "LingShan-G2")

    import novelvideo.config as config

    config = importlib.reload(config)

    assert config.PROP_REF_IMAGE_PROVIDER == "newapi"
    assert config.SCENE_MASTER_IMAGE_PROVIDER == "newapi"
    assert config.SCENE_REVERSE_MASTER_IMAGE_PROVIDER == "newapi"
    assert config.SCENE_360_IMAGE_PROVIDER == "newapi"

    from novelvideo.generators import nanobanana_prop, scene_reference_images

    nanobanana_prop = importlib.reload(nanobanana_prop)
    scene_reference_images = importlib.reload(scene_reference_images)

    assert nanobanana_prop.resolve_prop_reference_image_model() == "LingShan-G2"
    assert scene_reference_images._scene_image_provider("master", None) == "newapi"
    assert scene_reference_images._scene_image_provider("reverse_master", None) == "newapi"


def test_newapi_sketch_config_defaults_to_dc_image2_low_quality(monkeypatch):
    import httpx
    import novelvideo.config as config
    from novelvideo.generators import nanobanana_grid

    posted = {}

    class FakeResponse:
        headers = {"x-newapi-request-id": "req-sketch"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "resp-sketch",
                "data": [{"b64_json": base64.b64encode(b"sketch").decode()}],
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            posted["timeout"] = kwargs.get("timeout")
            posted["trust_env"] = kwargs.get("trust_env")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["json"] = json
            return FakeResponse()

    monkeypatch.setenv("NEWAPI_API_KEY", "newapi-token")
    monkeypatch.setenv("NEWAPI_BASE_URL", "http://newapi.test/v1")
    monkeypatch.setenv("NEWAPI_IMAGE_MODEL", "LingShan-G2")
    monkeypatch.setenv("DEFAULT_SKETCH_IMAGE_SELECTION", "newapi_gpt_image2")
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    config = importlib.reload(config)
    sketch_config = config.get_sketch_generation_config()

    assert sketch_config["provider"] == "newapi"
    assert sketch_config["model"] == "LingShan-G2"
    assert sketch_config["image_size"] == "1K"
    assert sketch_config["openai_image_quality"] == "low"

    trace = {}
    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key=sketch_config["api_key"],
            model=sketch_config["model"],
            prompt="sketch prompt",
            image_config={
                "aspect_ratio": "2:3",
                "image_size": sketch_config["image_size"],
                "quality": sketch_config["openai_image_quality"],
            },
            base_url=sketch_config["base_url"],
            trace=trace,
        )
    )

    assert image_bytes == b"sketch"
    assert error == ""
    # 2026-10-03 起本地不再给同步出图设读取上限：中转站提前放弃（5xx）和本地
    # 等得不够是两回事，本地先掐断只会把已经付费、上游仍在渲染的图丢掉。
    assert nanobanana_grid.NEWAPI_IMAGE_HTTP_TIMEOUT_SECONDS is None
    assert posted["timeout"].read is None
    assert posted["timeout"].connect and posted["timeout"].connect > 0
    assert posted["trust_env"] is False
    assert posted["json"]["model"] == "LingShan-G2"
    assert posted["json"]["quality"] == "low"
    assert posted["json"]["extra_fields"] == {
        "aspect_ratio": "2:3",
        "image_size": "1K",
        "resolution": "1k",
        "quality": "low",
    }
    assert trace == {"request_id": "req-sketch", "response_id": "resp-sketch"}


def test_newapi_sketch_explicit_banana2_selection_preserves_selected_model(monkeypatch):
    import httpx
    import novelvideo.config as config
    from novelvideo.generators import nanobanana_grid

    posted = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"sketch").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["json"] = json
            return FakeResponse()

    monkeypatch.setenv("NEWAPI_API_KEY", "newapi-token")
    monkeypatch.setenv("NEWAPI_BASE_URL", "http://newapi.test/v1")
    monkeypatch.setenv("NEWAPI_NANOBANANA2_MODEL", "LingShan-NB-2")
    monkeypatch.setenv("NEWAPI_NANOBANANA2_ENABLED", "true")
    monkeypatch.setenv("DEFAULT_SKETCH_IMAGE_SELECTION", "newapi_nanobanana2")
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    config = importlib.reload(config)
    sketch_config = config.get_sketch_generation_config()

    assert sketch_config["provider"] == "newapi"
    assert sketch_config["model"] == "LingShan-NB-2"
    assert sketch_config["image_size"] == "1K"

    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key=sketch_config["api_key"],
            model=sketch_config["model"],
            prompt="sketch prompt",
            image_config={
                "aspect_ratio": "2:3",
                "image_size": sketch_config["image_size"],
                "quality": sketch_config["openai_image_quality"],
            },
            base_url=sketch_config["base_url"],
        )
    )

    assert image_bytes == b"sketch"
    assert error == ""
    assert posted["json"]["model"] == "LingShan-NB-2"
    assert "quality" not in posted["json"]
    assert posted["json"]["extra_fields"] == {
        "aspect_ratio": "2:3",
        "image_size": "1K",
        "resolution": "1k",
    }


def test_newapi_image_call_sends_gpt_image2_params(monkeypatch):
    import httpx
    from novelvideo.generators import nanobanana_grid

    posted = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"image-bytes").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["url"] = url
            posted["headers"] = headers
            posted["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-G2",
            prompt="portrait prompt",
            image_config={
                "aspect_ratio": "3:4",
                "image_size": "0.5K",
                "quality": "medium",
            },
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes == b"image-bytes"
    assert error == ""
    assert posted["url"] == "http://newapi.test/v1/images/generations"
    assert posted["headers"]["Authorization"] == "Bearer newapi-token"
    assert posted["json"]["model"] == "LingShan-G2"
    assert posted["json"]["prompt"] == "portrait prompt"
    assert posted["json"]["quality"] == "medium"
    assert posted["json"]["extra_fields"] == {
        "aspect_ratio": "3:4",
        "image_size": "1K",
        "resolution": "1k",
        "quality": "medium",
    }


def test_newapi_image_call_reports_transport_exception_type(monkeypatch):
    import httpx
    from novelvideo.generators import nanobanana_grid

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            raise httpx.ReadTimeout("")

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-G2",
            prompt="portrait prompt",
            image_config={"aspect_ratio": "16:9", "image_size": "1K"},
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes is None
    assert "请求异常: ReadTimeout" in error
    assert "endpoint=http://newapi.test/v1" in error
    assert "model=LingShan-G2" in error


def test_newapi_image_call_reraises_insufficient_credit(monkeypatch):
    from novelvideo.generators import nanobanana_grid

    class FakeUsageMeter:
        async def reserve_current_model_call_credit(self, **_kwargs):
            raise InsufficientCreditsError(user_id="usr_1", cost=5, balance=0)

    monkeypatch.setattr(nanobanana_grid, "get_usage_meter", lambda: FakeUsageMeter())

    with pytest.raises(InsufficientCreditsError):
        run_async(
            nanobanana_grid._call_newapi_image_api(
                api_key="newapi-token",
                model="LingShan-G2",
                prompt="portrait prompt",
                base_url="http://newapi.test/v1",
            )
        )


def test_newapi_sketch_grid_reraises_insufficient_credit(monkeypatch, tmp_path):
    from novelvideo.generators import nanobanana_grid

    async def fake_call_newapi_image_api(**_kwargs):
        raise InsufficientCreditsError(user_id="usr_1", cost=5, balance=0)

    monkeypatch.setattr(
        nanobanana_grid,
        "_call_newapi_image_api",
        fake_call_newapi_image_api,
    )

    generator = nanobanana_grid.NanoBananaGridGenerator(
        api_key="newapi-token",
        config={
            "provider": "newapi",
            "api_key": "newapi-token",
            "base_url": "http://newapi.test/v1",
            "model": "LingShan-G2",
            "rows": 1,
            "cols": 1,
            "batch_size": 1,
            "total_panels": 1,
            "mode": "1x1",
            "image_size": "1K",
            "openai_sketch_image_quality": "low",
        },
    )

    with pytest.raises(InsufficientCreditsError):
        run_async(
            generator.generate_grid(
                beats=[
                    {
                        "beat_number": 3,
                        "visual_description": "女主站在竹林中回头。",
                        "narration": "她终于察觉身后有人。",
                    }
                ],
                character_map={},
                style="chinese_period_drama",
                output_path=str(tmp_path / "sketch.png"),
                rows=1,
                cols=1,
                sketch=True,
                mode_key="1x1_2-3_sketch",
                location_beat_numbers=[3],
            )
        )


def test_newapi_image_call_preserves_explicit_nanobanana2_with_quality(
    monkeypatch,
):
    import httpx
    from novelvideo.generators import nanobanana_grid

    posted = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"image-bytes").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setenv("NEWAPI_NANOBANANA2_ENABLED", "true")

    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-NB-2",
            prompt="portrait prompt",
            image_config={
                "aspect_ratio": "3:4",
                "image_size": "1K",
                "quality": "medium",
            },
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes == b"image-bytes"
    assert error == ""
    assert posted["json"]["model"] == "LingShan-NB-2"
    assert "quality" not in posted["json"]
    assert posted["json"]["extra_fields"] == {
        "aspect_ratio": "3:4",
        "image_size": "1K",
        "resolution": "1k",
    }


def test_newapi_image_call_relays_reference_images(monkeypatch):
    import httpx
    from novelvideo.generators import nanobanana_grid

    posted = {}
    relayed = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"image-bytes").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["json"] = json
            return FakeResponse()

    def fake_upload_image_bytes(data, *, ext="png", ttl=None, image_transform=None):
        relayed.append((data, ext, ttl, image_transform))
        return f"https://relay.test/{len(relayed)}.png"

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(nanobanana_grid, "upload_image_bytes", fake_upload_image_bytes)
    monkeypatch.setenv("NEWAPI_NANOBANANA2_ENABLED", "true")

    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-NB-2",
            prompt="identity prompt",
            reference_images=[b"ref-a", b"ref-b"],
            image_config={"aspect_ratio": "3:4", "image_size": "1K", "quality": "medium"},
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes == b"image-bytes"
    assert error == ""
    assert relayed == [
        (b"ref-a", "png", None, nanobanana_grid.IMAGE_TRANSFORM_AI_REFERENCE_JPEG),
        (b"ref-b", "png", None, nanobanana_grid.IMAGE_TRANSFORM_AI_REFERENCE_JPEG),
    ]
    assert posted["json"]["images"] == [
        "https://relay.test/1.png",
        "https://relay.test/2.png",
    ]


def test_newapi_image_size_supports_3k_dynamic_and_minimum_pixels():
    """Keep HK's generations contract while accepting upstream size capabilities."""
    from novelvideo.generators.nanobanana_grid import resolve_openai_image_size

    assert (
        resolve_openai_image_size(
            "16:9",
            "3K",
            allow_dynamic_resolution=True,
        )
        == "3072x1728"
    )
    assert (
        resolve_openai_image_size(
            "3:2",
            "2048x1376",
            allow_dynamic_resolution=True,
        )
        == "2048x1376"
    )

    width, height = (
        int(value)
        for value in resolve_openai_image_size(
            "16:9",
            "2K",
            allow_dynamic_resolution=True,
            min_pixels=3_686_400,
        ).split("x")
    )
    assert width * height >= 3_686_400


def test_newapi_reference_request_keeps_generations_images_contract_with_min_pixels(monkeypatch):
    import httpx
    from novelvideo.generators import nanobanana_grid

    posted = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"image-bytes").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["url"] = url
            posted["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(
        nanobanana_grid,
        "upload_image_bytes",
        lambda data, **_kwargs: f"https://relay.test/{len(data)}.jpg",
    )

    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-G2",
            prompt="portrait prompt",
            reference_images=[b"reference"],
            image_config={
                "aspect_ratio": "16:9",
                "image_size": "2K",
                "request_schema": {"minPixels": 3_686_400},
            },
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes == b"image-bytes"
    assert error == ""
    assert posted["url"] == "http://newapi.test/v1/images/generations"
    assert posted["json"]["images"] == ["https://relay.test/9.jpg"]
    assert "image" not in posted["json"]
    assert "watermark" not in posted["json"]
    width, height = (int(value) for value in posted["json"]["size"].split("x"))
    assert width * height >= 3_686_400


def test_lingshan_g2_retries_missing_references_once_with_fresh_urls(monkeypatch):
    import httpx
    from novelvideo.generators import nanobanana_grid

    posted = []
    uploads = []
    refunds = []
    confirmations = []

    class FakeResponse:
        def __init__(self, attempt):
            self.attempt = attempt
            self.status_code = 400 if attempt == 1 else 200
            self.text = (
                '{"error":{"message":"当前对话里没有可用的参考图像文件（Image 1 和 Image 2）"}}'
                if attempt == 1
                else ""
            )
            self.headers = {"x-oneapi-request-id": f"req-{attempt}"}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "bad response",
                    request=httpx.Request("POST", "http://newapi.test/v1/images/generations"),
                    response=self,
                )

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"image-bytes").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted.append(json)
            return FakeResponse(len(posted))

    class FakeUsageMeter:
        async def reserve_current_model_call_credit(self, **_kwargs):
            return f"reservation-{len(posted) + 1}"

        async def refund_model_call_credit_reservation(self, reservation_id, *, metadata=None):
            refunds.append((reservation_id, metadata or {}))

        async def bump_model_call(self, **kwargs):
            confirmations.append(kwargs)

    def fake_upload_image_bytes(data, *, ext="png", ttl=None, image_transform=None):
        uploads.append((data, ext))
        return f"https://relay.test/fresh-{len(uploads)}.{ext}"

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(nanobanana_grid, "upload_image_bytes", fake_upload_image_bytes)
    monkeypatch.setattr(nanobanana_grid, "get_usage_meter", lambda: FakeUsageMeter())

    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-G2",
            prompt="edit Image 1 using expression geometry from Image 2",
            reference_images=[b"source-reference", b"expression-control"],
            image_config={"aspect_ratio": "9:16", "image_size": "2K", "quality": "medium"},
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes == b"image-bytes"
    assert error == ""
    assert len(posted) == 2
    assert posted[0]["images"] == [
        "https://relay.test/fresh-1.png",
        "https://relay.test/fresh-2.png",
    ]
    assert posted[1]["images"] == [
        "https://relay.test/fresh-3.png",
        "https://relay.test/fresh-4.png",
    ]
    assert len(refunds) == 1
    assert refunds[0][1]["http_status"] == 400
    assert len(confirmations) == 1


def test_newapi_image_call_preserves_reference_image_extensions(monkeypatch):
    import httpx
    from novelvideo.generators import nanobanana_grid

    relayed = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"image-bytes").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            return FakeResponse()

    def fake_upload_image_bytes(data, *, ext="png", ttl=None, image_transform=None):
        relayed.append((data, ext, ttl, image_transform))
        return f"https://relay.test/{len(relayed)}.{ext}"

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(nanobanana_grid, "upload_image_bytes", fake_upload_image_bytes)
    monkeypatch.setenv("NEWAPI_NANOBANANA2_ENABLED", "true")

    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-NB-2",
            prompt="identity prompt",
            reference_images=[
                ("face.jpg", b"jpg-bytes", "image/jpeg"),
                (b"webp-bytes", "image/webp"),
            ],
            image_config={"aspect_ratio": "3:4", "image_size": "1K", "quality": "medium"},
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes == b"image-bytes"
    assert error == ""
    assert relayed == [
        (b"jpg-bytes", "jpg", None, nanobanana_grid.IMAGE_TRANSFORM_AI_REFERENCE_JPEG),
        (b"webp-bytes", "webp", None, nanobanana_grid.IMAGE_TRANSFORM_AI_REFERENCE_JPEG),
    ]


def test_newapi_image_reference_relay_failure_falls_back_to_inline(monkeypatch):
    import httpx
    from novelvideo.generators import nanobanana_grid
    from novelvideo.storage.media_relay import MediaRelayTransientError

    posted = {}
    inline = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"image-bytes").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["json"] = json
            return FakeResponse()

    def fail_upload(*_args, **_kwargs):
        raise MediaRelayTransientError("relay temporarily unavailable")

    def fake_inline(data, *, ext="png", image_transform=None):
        inline.append((data, ext, image_transform))
        return f"data:image/{ext};base64,inline-{len(inline)}"

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(nanobanana_grid, "upload_image_bytes", fail_upload)
    monkeypatch.setattr(nanobanana_grid, "build_inline_media_url", fake_inline)

    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-G2",
            prompt="edit with one reference",
            reference_images=[("face.jpg", b"jpg-bytes", "image/jpeg")],
            image_config={"aspect_ratio": "9:16", "image_size": "1K", "quality": "medium"},
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes == b"image-bytes"
    assert error == ""
    assert posted["json"]["images"] == ["data:image/jpg;base64,inline-1"]
    assert inline == [
        (b"jpg-bytes", "jpg", nanobanana_grid.IMAGE_TRANSFORM_AI_REFERENCE_JPEG)
    ]


def test_newapi_image_reference_non_relay_error_is_not_swallowed(monkeypatch):
    from novelvideo.generators import nanobanana_grid

    def fail_upload(*_args, **_kwargs):
        raise ValueError("invalid image bytes")

    monkeypatch.setattr(nanobanana_grid, "upload_image_bytes", fail_upload)

    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-G2",
            prompt="edit with one reference",
            reference_images=[b"bad-reference"],
            image_config={"aspect_ratio": "1:1", "image_size": "1K", "quality": "medium"},
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes is None
    assert "reference image preparation failed: invalid image bytes" in error


def test_newapi_image_http_error_logs_redacted_request_context(monkeypatch, caplog):
    import httpx
    from novelvideo.generators import nanobanana_grid

    posted = {}
    refunds = []

    class FakeResponse:
        status_code = 400
        text = '{"error":{"message":"openai_error","type":"bad_response_status_code"}}'
        headers = {
            "x-newapi-request-id": "req-123",
            "cf-ray": "cf-ray-456",
            "date": "Fri, 22 May 2026 03:00:00 GMT",
            "authorization": "Bearer should-not-leak",
        }

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "bad response",
                request=httpx.Request("POST", "http://newapi.test/v1/images/generations"),
                response=self,
            )

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["url"] = url
            posted["headers"] = headers
            posted["json"] = json
            return FakeResponse()

    def fake_upload_image_bytes(data, *, ext="png", ttl=None, image_transform=None):
        return f"https://relay.test/signed-{data.decode()}?token=secret"

    class FakeUsageMeter:
        async def reserve_current_model_call_credit(self, **_kwargs):
            return "reservation_1"

        async def refund_model_call_credit_reservation(self, reservation_id, *, metadata=None):
            refunds.append({"reservation_id": reservation_id, "metadata": metadata or {}})

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(nanobanana_grid, "upload_image_bytes", fake_upload_image_bytes)
    monkeypatch.setattr(nanobanana_grid, "get_usage_meter", lambda: FakeUsageMeter())
    caplog.set_level(logging.WARNING, logger="novelvideo.generators.nanobanana_grid")

    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-G2",
            prompt="sensitive prompt body",
            reference_images=[b"ref-a"],
            image_config={"aspect_ratio": "2:1", "image_size": "2K", "quality": "medium"},
            base_url="http://newapi.test/v1",
        )
    )

    log_text = "\n".join(record.getMessage() for record in caplog.records)

    assert image_bytes is None
    assert "request_id=req-123" in error
    assert "cf-ray-456" in error
    assert "model=LingShan-G2" in error
    assert "extra_fields" in error
    assert "reference_image_count=1" in error
    assert "request_id=req-123" in log_text
    assert "http://newapi.test/v1/images/generations" in log_text
    assert "prompt_sha256=" in log_text
    assert "sensitive prompt body" not in error
    assert "sensitive prompt body" not in log_text
    assert "newapi-token" not in error
    assert "newapi-token" not in log_text
    assert "token=secret" not in error
    assert "token=secret" not in log_text
    assert refunds == [
        {
            "reservation_id": "reservation_1",
            "metadata": {
                "source": "newapi_image_api",
                "error": "HTTP 400",
                "request_id": "req-123",
                "http_status": 400,
                "response_headers": {
                    "x-newapi-request-id": "req-123",
                    "cf-ray": "cf-ray-456",
                    "date": "Fri, 22 May 2026 03:00:00 GMT",
                },
            },
        }
    ]


def test_newapi_reference_requests_use_unified_image_route_by_default(monkeypatch):
    from novelvideo.generators.nanobanana_grid import resolve_newapi_image_request_model

    monkeypatch.delenv("NEWAPI_NANOBANANA2_ENABLED", raising=False)
    monkeypatch.setenv("VILLAGE_CANVAS_IMAGE_EDIT_MODEL_FALLBACK", "village-canvas-image-reference")

    assert (
        resolve_newapi_image_request_model("LingShan-G2", reference_count=0)
        == "LingShan-G2"
    )
    assert (
        resolve_newapi_image_request_model("LingShan-G2", reference_count=1)
        == "village-canvas-image-reference"
    )
    assert (
        resolve_newapi_image_request_model("LingShan-NB-2", reference_count=2)
        == "village-canvas-image-reference"
    )


def test_newapi_nanobanana2_route_requires_explicit_enable(monkeypatch):
    from novelvideo.generators.nanobanana_grid import resolve_newapi_image_request_model

    monkeypatch.setenv("NEWAPI_NANOBANANA2_ENABLED", "true")

    assert (
        resolve_newapi_image_request_model("LingShan-NB-2", reference_count=2)
        == "LingShan-NB-2"
    )


def test_newapi_fast_reference_mode_stabilizes_before_post(monkeypatch):
    import base64
    import httpx
    from novelvideo.generators import nanobanana_grid

    posted: list[dict] = []

    class OkResponse:
        status_code = 200
        text = ""
        headers = {"x-oneapi-request-id": "req-stable-edit"}

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"stable-edit").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted.append(json)
            return OkResponse()

    async def fake_relay(_references):
        return ["https://relay.test/a.png"]

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(nanobanana_grid, "_relay_reference_images_for_newapi", fake_relay)
    monkeypatch.setenv("NEWAPI_FAST_REFERENCE_MODE", "true")

    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-G2",
            prompt="edit with one reference",
            reference_images=[b"a"],
            image_config={"aspect_ratio": "9:16", "image_size": "4K", "quality": "high"},
            base_url="http://newapi.test/v1",
        )
    )

    assert error == ""
    assert image_bytes == b"stable-edit"
    assert len(posted) == 1
    assert posted[0]["extra_fields"]["image_size"] == "1K"
    assert posted[0]["extra_fields"]["resolution"] == "1k"
    assert posted[0]["quality"] == "low"


@pytest.mark.parametrize("status_code", [502, 504, 524])
def test_newapi_http_transient_failure_degrades_high_request_once(monkeypatch, status_code):
    import base64
    import httpx
    from novelvideo.generators import nanobanana_grid

    posted: list[dict] = []
    response_status = status_code

    class TimeoutResponse:
        status_code = response_status
        text = '{"error":{"message":"图片生成超时"}}'
        headers = {"x-oneapi-request-id": "req-timeout"}

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "gateway timeout",
                request=httpx.Request("POST", "http://newapi.test/v1/images/generations"),
                response=self,
            )

    class OkResponse:
        status_code = 200
        text = ""
        headers = {"x-oneapi-request-id": "req-degraded-ok"}

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"degraded-ok").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted.append(json)
            return TimeoutResponse() if len(posted) == 1 else OkResponse()

    async def fake_relay(_references):
        return ["https://relay.test/a.png"]

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(nanobanana_grid, "_relay_reference_images_for_newapi", fake_relay)
    monkeypatch.setattr(nanobanana_grid, "NEWAPI_IMAGE_FAILOVER_CHANNEL_ID", "")

    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-G2",
            prompt="edit with one reference",
            reference_images=[b"a"],
            image_config={"aspect_ratio": "9:16", "image_size": "4K", "quality": "high"},
            base_url="http://newapi.test/v1",
        )
    )

    assert error == ""
    assert image_bytes == b"degraded-ok"
    assert len(posted) == 2
    assert posted[0]["extra_fields"]["image_size"] == "4K"
    assert posted[0]["quality"] == "high"
    assert posted[1]["extra_fields"]["image_size"] == "1K"
    assert posted[1]["quality"] == "medium"


def test_newapi_image_http_502_fails_over_to_yunfei_channel(monkeypatch):
    import base64
    import httpx
    from novelvideo.generators import nanobanana_grid

    monkeypatch.setattr(nanobanana_grid, "NEWAPI_IMAGE_FAILOVER_CHANNEL_ID", "8")

    attempts = 0
    authorization: list[str] = []

    class FailingResponse:
        status_code = 502
        text = ""
        headers = {"x-oneapi-request-id": "req-primary-fail"}

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "bad gateway",
                request=httpx.Request("POST", "http://newapi.test/v1/images/generations"),
                response=self,
            )

    class OkResponse:
        status_code = 200
        text = ""
        headers = {"x-oneapi-request-id": "req-yunfei-ok"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "img-yunfei",
                "data": [{"b64_json": base64.b64encode(b"yunfei-image").decode()}],
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            nonlocal attempts
            attempts += 1
            authorization.append(headers["Authorization"])
            return FailingResponse() if attempts == 1 else OkResponse()

    class FakeUsageMeter:
        async def reserve_current_model_call_credit(self, **_kwargs):
            return "reservation_1"

        async def refund_model_call_credit_reservation(self, *_args, **_kwargs):
            return None

        async def bump_model_call(self, **_kwargs):
            return None

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(nanobanana_grid, "get_usage_meter", lambda: FakeUsageMeter())

    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-G2",
            prompt="simple text to image",
            image_config={"image_size": "2K", "quality": "medium"},
            base_url="http://newapi.test/v1",
        )
    )

    assert error == ""
    assert image_bytes == b"yunfei-image"
    assert attempts == 2
    assert authorization == ["Bearer newapi-token", "Bearer newapi-token-8"]


def test_newapi_image_http_5xx_retries_then_fails(monkeypatch):
    import httpx
    from novelvideo.generators import nanobanana_grid

    monkeypatch.setattr(nanobanana_grid, "NEWAPI_IMAGE_FAILOVER_CHANNEL_ID", "8")

    attempts = 0
    sleeps: list[float] = []

    class FailingResponse:
        status_code = 502
        text = '{"error":{"message":"error","type":"bad_response"}}'
        headers = {"x-oneapi-request-id": "req-fail"}

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "bad gateway",
                request=httpx.Request("POST", "http://newapi.test/v1/images/generations"),
                response=self,
            )

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            nonlocal attempts
            attempts += 1
            return FailingResponse()

    async def fake_sleep(delay):
        sleeps.append(float(delay))

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(nanobanana_grid.asyncio, "sleep", fake_sleep)

    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-G2",
            prompt="retry prompt",
            image_config={"aspect_ratio": "2:1", "image_size": "2K", "quality": "medium"},
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes is None
    assert "HTTP 502" in error
    assert "request_id=req-fail" in error
    assert attempts == nanobanana_grid.NEWAPI_IMAGE_TRANSIENT_MAX_ATTEMPTS
    # Primary -> Yunfei failover is immediate and consumes the second attempt;
    # only the final same-channel retry sleeps.
    assert len(sleeps) == nanobanana_grid.NEWAPI_IMAGE_TRANSIENT_MAX_ATTEMPTS - 2


def test_newapi_image_empty_body_retries_and_normalizes_model_alias(monkeypatch):
    import httpx
    from novelvideo.generators import nanobanana_grid

    attempts = 0
    posted_models: list[str] = []

    class EmptyBodyResponse:
        status_code = 400
        text = ""
        headers = {"x-oneapi-request-id": "req-empty"}

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "bad request",
                request=httpx.Request("POST", "http://newapi.test/v1/images/generations"),
                response=self,
            )

    class OkResponse:
        status_code = 200
        headers = {"x-oneapi-request-id": "req-ok"}
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            import base64

            return {
                "id": "img-ok",
                "data": [{"b64_json": base64.b64encode(b"ok-image").decode("ascii")}],
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            nonlocal attempts
            attempts += 1
            posted_models.append(str(json.get("model") or ""))
            if attempts < 2:
                return EmptyBodyResponse()
            return OkResponse()

    async def fake_sleep(_delay):
        return None

    class FakeUsageMeter:
        async def reserve_current_model_call_credit(self, **_kwargs):
            return "reservation_1"

        async def refund_model_call_credit_reservation(self, reservation_id, *, metadata=None):
            return None

        async def bump_model_call(self, **_kwargs):
            return None

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(nanobanana_grid.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(nanobanana_grid, "get_usage_meter", lambda: FakeUsageMeter())

    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="灵山-G2",
            prompt="short prompt",
            image_config={"aspect_ratio": "3：4", "image_size": "2K", "quality": "medium"},
            base_url="http://newapi.test/v1",
        )
    )

    assert error == ""
    assert image_bytes == b"ok-image"
    assert attempts == 2
    assert posted_models == ["LingShan-G2", "LingShan-G2"]


def test_compact_prompt_for_newapi_upstream_keeps_locks_and_clips_body():
    from novelvideo.generators import nanobanana_grid

    long_body = "A Korean actress in soft window light. " * 80
    full = (
        "REFERENCE PRIORITY — Image 1 is the absolute authority for identity, composition, "
        "medium, rendering technique, color treatment, texture and lighting. "
        "Do not reinterpret anything.\n"
        "STYLE LOCK — reproduce Image 1 pixel-faithfully outside the selected facial region. "
        "Do not reinterpret, redraw, repaint, recolor or restyle any part of the image.\n"
        "IDENTITY LOCK — preserve exact facial identity, body proportions, age, hairstyle, "
        "costume and accessories from the reference.\n"
        f"{long_body}\n"
        "AVOID: anime, cartoon, illustration, plastic skin, CGI look, deformed hands, "
        "extra fingers, bad anatomy, watermark, unrequested text, random props, neon soup."
    )
    assert len(full) > nanobanana_grid.NEWAPI_UPSTREAM_PROMPT_SOFT_LIMIT

    uplink, meta = nanobanana_grid.compact_prompt_for_newapi_upstream(
        full,
        max_chars=nanobanana_grid.NEWAPI_UPSTREAM_PROMPT_SOFT_LIMIT,
        reference_count=2,
    )

    assert meta["upstream_prompt_compacted"] is True
    assert meta["expression_edit_mode"] is False
    assert meta["local_prompt_chars"] == len(full)
    assert len(uplink) <= nanobanana_grid.NEWAPI_UPSTREAM_PROMPT_SOFT_LIMIT
    assert "REFERENCE PRIORITY" in uplink
    assert "IDENTITY LOCK" in uplink
    assert "Korean actress" in uplink
    # Full local text is not silently destroyed by the helper — only uplink is short.
    assert len(full) > len(uplink)


def test_compact_storyboard_grid_keeps_every_panel_and_marker_contract():
    from novelvideo.generators import nanobanana_grid

    panels = "\n".join(
        f"- **Panel {index}**: 会议室动作 {index}，"
        "[DSZ_aa0e] (FLUORESCENT LIME) 将文件推向 [SW_0667] (FLUORESCENT CYAN)"
        for index in range(1, 19)
    )
    blanks = "\n".join(
        f"- **Panel {index}** [BLANK PLACEHOLDER]: A completely blank unused panel. "
        "Pure white background only."
        for index in range(19, 26)
    )
    full = f"""Generate a 5x5 storyboard grid. Each panel MUST be 2:3 PORTRAIT.
!!! MANDATORY GRID FORMAT: 5 ROWS × 5 COLUMNS !!!
STYLE: COLOR-CODED DIRECTIONAL STORYBOARD MANNEQUIN on pure white background.
- [DSZ_aa0e] — **FLUORESCENT LIME (#CCFF00)** featureless identity proxy.
- [SW_0667] — **FLUORESCENT CYAN (#00FFFF)** featureless identity proxy.
{('generic directing prose ' * 120)}
{panels}
{blanks}
"""

    uplink, meta = nanobanana_grid.compact_prompt_for_newapi_upstream(
        full,
        max_chars=900,
        reference_count=1,
    )

    assert meta["storyboard_grid_mode"] is True
    assert len(uplink) <= 900
    assert "EXACT 5x5 grid" in uplink
    assert "PURE WHITE" in uplink
    assert "A=[DSZ_aa0e]=FLUORESCENT LIME #CCFF00" in uplink
    assert "B=[SW_0667]=FLUORESCENT CYAN #00FFFF" in uplink
    assert "P1=会议室动作 1" in uplink
    assert "P18=会议室动作 18" in uplink
    assert "P19-P25=BLANK pure white only" in uplink
    assert "NO photorealism" in uplink


def test_compact_render_colorization_keeps_all_reference_roles_and_action():
    from novelvideo.generators import nanobanana_grid

    full = """Colorize this 1×1 storyboard SKETCH (first attached image / Image 1) into a full-color continuous image. Each panel MUST be 2:3 PORTRAIT.
STYLE: Refined Chinese folk paper-cut graphic style, layered silhouettes, handcrafted edges, decorative symmetry.
Image 1 / SKETCH IS the base drawing — preserve ALL composition, crop, poses, and camera angles exactly.
REFERENCE IMAGES:
  Image 1 = SKETCH TO COLORIZE
  Image 2 = [DSZ_aa0e]: multi-view character reference sheet.
  Image 3 = [SW_0667]: multi-view character reference sheet.
  Image 4 = Scene "会议室": environment reference asset.
  Image 5 = Prop "协议" prop identity reference.
THIS IS A COLORIZATION TASK — the sketch is the BASE DRAWING, NOT just a reference.
- Visual description: 俯拍会议桌，[DSZ_aa0e]把协议[XY_faa1]推向[SW_0667]。
""" + ("verbose render lock contract " * 300)

    soft, soft_meta = nanobanana_grid.compact_prompt_for_newapi_upstream(
        full,
        max_chars=900,
        reference_count=5,
    )
    hard, hard_meta = nanobanana_grid.compact_prompt_for_newapi_upstream(
        full,
        max_chars=480,
        reference_count=5,
    )

    for compacted, meta, limit in ((soft, soft_meta, 900), (hard, hard_meta, 480)):
        assert meta["render_colorization_mode"] is True
        assert len(compacted) <= limit
        assert "COLORIZE Image1 SKETCH" in compacted
        assert "I2[DSZ_aa0e]=person identity/outfit/body" in compacted
        assert "I3[SW_0667]=person identity/outfit/body" in compacted
        assert "I4=会议室 scene materials/set" in compacted
        assert "I5=协议 prop identity/material" in compacted
        assert "Action: 俯拍会议桌" in compacted
        assert "[XY_faa1]" not in compacted


def test_compact_prompt_for_expression_edit_keeps_face_change_before_locks():
    from novelvideo.generators import nanobanana_grid

    # Short modern expression brief (may still be padded by older lock wrappers).
    short = (
        "Change only 人物 1's facial expression in Image 1 to 决绝震怒 · 激动 · 负向 · 愤怒 (clear).\n"
        "Cues: brows hard-knit, piercing narrowed eyes, sneering mouth, jaw advanced.\n"
        "Image 2 is a 3D face-geometry guide for brows/eyes/mouth/jaw only — ignore gray material "
        "and bald head. Match Image 2 geometry: brows pulled down; mouth corners down.\n"
        "Keep the same person, hair, costume, pose, camera, lighting and background. "
        "Do not restyle or return a near-copy of Image 1."
    )
    # Also cover legacy long expression prompts that still hit the compact path.
    legacy_pad = (
        "IDENTITY LOCK — preserve exact facial identity, face shape, age, skin tone, hairstyle.\n"
        "SCENE LOCK — preserve composition, camera, pose, body, hands, lighting, background.\n"
        "STYLE LOCK — match Image 1 style outside the edited region; do not restyle.\n"
        "REFERENCE PRIORITY — Image 1 is the absolute visual authority for identity and lighting.\n"
    ) * 8
    full = f"{short}\n{legacy_pad}"
    assert len(full) > nanobanana_grid.NEWAPI_UPSTREAM_PROMPT_SOFT_LIMIT

    uplink, meta = nanobanana_grid.compact_prompt_for_newapi_upstream(
        full,
        max_chars=nanobanana_grid.NEWAPI_UPSTREAM_PROMPT_SOFT_LIMIT,
        reference_count=2,
    )

    assert meta["upstream_prompt_compacted"] is True
    assert meta["expression_edit_mode"] is True
    assert len(uplink) <= nanobanana_grid.NEWAPI_UPSTREAM_PROMPT_SOFT_LIMIT
    assert "Change only" in uplink
    assert "3D face-geometry" in uplink or "Image 2" in uplink
    assert "brows hard-knit" in uplink
    change_at = uplink.upper().find("CHANGE ONLY")
    identity_at = uplink.upper().find("IDENTITY LOCK")
    assert change_at >= 0
    if identity_at >= 0:
        assert change_at < identity_at
    assert "pixel-faithfully" not in uplink.lower()


def test_newapi_image_posts_compact_prompt_while_local_sha_differs(monkeypatch):
    import httpx
    from novelvideo.generators import nanobanana_grid

    posted = {}

    class FakeResponse:
        headers = {"x-newapi-request-id": "req-compact"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "resp-compact",
                "data": [{"b64_json": base64.b64encode(b"ok").decode()}],
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["json"] = json
            return FakeResponse()

    class FakeUsageMeter:
        async def reserve_current_model_call_credit(self, **_kwargs):
            return "reservation_compact"

        async def bump_model_call(self, **_kwargs):
            return None

        async def refund_model_call_credit_reservation(self, reservation_id, *, metadata=None):
            return None

    def fake_upload_image_bytes(data, *, ext="png", ttl=None, image_transform=None):
        return f"https://relay.test/{len(data)}.jpg"

    long_prompt = (
        "REFERENCE PRIORITY — Image 1 is the absolute authority for identity.\n"
        + ("detailed cinematic portrait of a calm woman, natural window light, " * 100)
        + "\nAVOID: watermark, text, extra fingers, identity drift."
    )
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(nanobanana_grid, "upload_image_bytes", fake_upload_image_bytes)
    monkeypatch.setattr(nanobanana_grid, "get_usage_meter", lambda: FakeUsageMeter())

    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-G2",
            prompt=long_prompt,
            reference_images=[b"ref-a", b"ref-b"],
            image_config={"aspect_ratio": "1:1", "image_size": "2K", "quality": "medium"},
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes == b"ok"
    assert error == ""
    uplink = posted["json"]["prompt"]
    assert len(uplink) < len(long_prompt)
    assert len(uplink) <= nanobanana_grid.NEWAPI_UPSTREAM_PROMPT_SOFT_LIMIT
    assert "REFERENCE PRIORITY" in uplink


def test_newapi_generic_safety_400_retries_with_hard_short_prompt(monkeypatch):
    import httpx
    from novelvideo.generators import nanobanana_grid

    attempts: list[str] = []

    class SafetyResponse:
        status_code = 400
        text = (
            "您的请求无法用于生成图像。该请求可能因安全政策被拦截，"
            "或不适合进行图像生成。"
        )
        headers = {"x-newapi-request-id": "req-safety"}

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "bad request",
                request=httpx.Request("POST", "http://newapi.test/v1/images/generations"),
                response=self,
            )

    class OkResponse:
        status_code = 200
        headers = {"x-newapi-request-id": "req-retry-ok"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "resp-retry-ok",
                "data": [{"b64_json": base64.b64encode(b"recovered").decode()}],
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            attempts.append(json["prompt"])
            if len(attempts) == 1:
                return SafetyResponse()
            return OkResponse()

    class FakeUsageMeter:
        async def reserve_current_model_call_credit(self, **_kwargs):
            return "reservation_safety"

        async def bump_model_call(self, **_kwargs):
            return None

        async def refund_model_call_credit_reservation(self, reservation_id, *, metadata=None):
            return None

    def fake_upload_image_bytes(data, *, ext="png", ttl=None, image_transform=None):
        return f"https://relay.test/{len(data)}.jpg"

    long_prompt = (
        "REFERENCE PRIORITY — Image 1 is the absolute authority for identity, composition, "
        "medium, rendering technique, color treatment, texture and lighting.\n"
        "STYLE LOCK — reproduce Image 1 pixel-faithfully outside the selected facial region.\n"
        "IDENTITY LOCK — preserve exact facial identity and costume.\n"
        + ("soft portrait, cinematic grade, calm expression, window light, " * 120)
        + "\nAVOID: anime, cartoon, deformed hands, watermark, unrequested text."
    )
    assert len(long_prompt) > nanobanana_grid.NEWAPI_UPSTREAM_PROMPT_HARD_LIMIT

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(nanobanana_grid, "upload_image_bytes", fake_upload_image_bytes)
    monkeypatch.setattr(nanobanana_grid, "get_usage_meter", lambda: FakeUsageMeter())

    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-G2",
            prompt=long_prompt,
            reference_images=[b"ref-a", b"ref-b"],
            image_config={"aspect_ratio": "1:1", "image_size": "2K", "quality": "medium"},
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes == b"recovered"
    assert error == ""
    assert len(attempts) == 2
    assert len(attempts[0]) <= nanobanana_grid.NEWAPI_UPSTREAM_PROMPT_SOFT_LIMIT
    assert len(attempts[1]) <= nanobanana_grid.NEWAPI_UPSTREAM_PROMPT_HARD_LIMIT
    assert len(attempts[1]) <= len(attempts[0])


def test_newapi_generic_safety_400_humanizes_error_after_retries_exhausted(monkeypatch):
    import httpx
    from novelvideo.generators import nanobanana_grid

    class SafetyResponse:
        status_code = 400
        text = (
            "您的请求无法用于生成图像。该请求可能因安全政策被拦截，"
            "或不适合进行图像生成。"
        )
        headers = {"x-newapi-request-id": "req-safety-final"}

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "bad request",
                request=httpx.Request("POST", "http://newapi.test/v1/images/generations"),
                response=self,
            )

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            return SafetyResponse()

    class FakeUsageMeter:
        async def reserve_current_model_call_credit(self, **_kwargs):
            return "reservation_safety_final"

        async def refund_model_call_credit_reservation(self, reservation_id, *, metadata=None):
            return None

    def fake_upload_image_bytes(data, *, ext="png", ttl=None, image_transform=None):
        return "https://relay.test/x.jpg"

    long_prompt = "REFERENCE PRIORITY — Image 1 authority.\n" + ("long creative body " * 200)

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(nanobanana_grid, "upload_image_bytes", fake_upload_image_bytes)
    monkeypatch.setattr(nanobanana_grid, "get_usage_meter", lambda: FakeUsageMeter())

    image_bytes, _text, error = run_async(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-G2",
            prompt=long_prompt,
            reference_images=[b"ref-a"],
            image_config={"aspect_ratio": "1:1", "image_size": "2K", "quality": "medium"},
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes is None
    assert "HTTP 400" in error
    assert "通用「安全政策」" in error
    assert "本地长提示词" in error
    assert "本地节点仍保留完整提示词" in error
    assert long_prompt not in error


def test_newapi_identity_image_sends_portrait_then_costume_references(
    monkeypatch,
    tmp_path,
):
    from novelvideo.generators import nanobanana_character

    captured = {}

    async def fake_call_newapi_image_api(**kwargs):
        captured.update(kwargs)
        return b"identity-image", "", ""

    monkeypatch.setattr(
        nanobanana_character,
        "_call_newapi_image_api",
        fake_call_newapi_image_api,
    )

    generator = nanobanana_character.NanoBananaCharacterGenerator(
        config={
            "provider": "newapi",
            "api_key": "newapi-token",
            "model": "LingShan-NB-2",
            "base_url": "http://newapi.test/v1",
        }
    )
    output_path = tmp_path / "identity_body_temp.png"

    image_bytes = run_async(
        generator._generate_with_reference(
            client=None,
            prompt="identity prompt",
            reference_image=None,
            output_path=str(output_path),
            reference_image_bytes=b"portrait-bytes",
            reference_image_name="/project/characters/李雷/reference_portrait.jpg",
            aspect_ratio="16:9",
            image_size="1K",
            additional_image_bytes=[b"costume-bytes"],
            additional_image_names=["/project/characters/李雷/学生_costume.png"],
        )
    )

    assert image_bytes == b"identity-image"
    assert output_path.read_bytes() == b"identity-image"
    assert captured["model"] == "LingShan-NB-2"
    assert captured["base_url"] == "http://newapi.test/v1"
    assert captured["image_config"] == {
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "quality": "medium",
    }
    assert captured["reference_images"] == [
        ("reference_portrait.jpg", b"portrait-bytes", "image/jpeg"),
        ("学生_costume.png", b"costume-bytes", "image/png"),
    ]


def test_newapi_character_portrait_reraises_insufficient_credit(monkeypatch, tmp_path):
    from novelvideo.generators import nanobanana_character

    async def fake_call_newapi_image_api(**_kwargs):
        raise InsufficientCreditsError(user_id="usr_1", cost=5, balance=0)

    monkeypatch.setattr(
        nanobanana_character,
        "_call_newapi_image_api",
        fake_call_newapi_image_api,
    )

    generator = nanobanana_character.NanoBananaCharacterGenerator(
        config={
            "provider": "newapi",
            "api_key": "newapi-token",
            "model": "LingShan-G2",
            "base_url": "http://newapi.test/v1",
        }
    )

    with pytest.raises(InsufficientCreditsError):
        run_async(
            generator.generate_character_portrait(
                character_name="李雷",
                character_prompt="young man",
                output_dir=str(tmp_path),
            )
        )


def test_newapi_character_portrait_raise_on_error_preserves_provider_detail(monkeypatch, tmp_path):
    import novelvideo.config as config
    from novelvideo.generators import image_generator, nanobanana_character

    async def fake_call_newapi_image_api(**_kwargs):
        return None, "", "HTTP 504: request_id=req-123; body=provider timeout"

    monkeypatch.setenv("NEWAPI_API_KEY", "newapi-token")
    monkeypatch.setenv("NEWAPI_BASE_URL", "http://newapi.test/v1")
    monkeypatch.setenv("NEWAPI_IMAGE_MODEL", "LingShan-G2")
    monkeypatch.setenv("DEFAULT_CHARACTER_IMAGE_SELECTION", "newapi_gpt_image2")
    importlib.reload(config)
    monkeypatch.setattr(
        nanobanana_character,
        "_call_newapi_image_api",
        fake_call_newapi_image_api,
    )

    with pytest.raises(RuntimeError, match="HTTP 504: request_id=req-123"):
        run_async(
            image_generator.generate_character_reference_unified(
                character_name="李雷",
                appearance_prompt="young man",
                output_dir=str(tmp_path),
                count=1,
                model="newapi_gpt_image2",
                raise_on_error=True,
            )
        )


def test_newapi_scene_master_uses_text_only_nanobanana2(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    from novelvideo.generators import scene_reference_images
    from novelvideo.models import NovelScene

    captured = {}

    async def fake_call_newapi_image_api(**kwargs):
        captured.update(kwargs)
        return b"scene-master", "", ""

    monkeypatch.setenv("NEWAPI_API_KEY", "newapi-token")
    monkeypatch.setenv("NEWAPI_BASE_URL", "http://newapi.test/v1")
    monkeypatch.setenv("SCENE_MASTER_IMAGE_PROVIDER", "newapi")
    monkeypatch.setenv("SCENE_MASTER_IMAGE_MODEL", "LingShan-NB-2")
    _patch_scene_newapi_gateway(monkeypatch)
    monkeypatch.setattr(
        scene_reference_images,
        "_call_newapi_image_api",
        fake_call_newapi_image_api,
    )
    monkeypatch.setattr(scene_reference_images, "SCENE_MASTER_IMAGE_PROVIDER", "newapi")
    monkeypatch.setattr(scene_reference_images, "SCENE_MASTER_IMAGE_MODEL", "LingShan-NB-2")

    scene = NovelScene(
        name="古董店",
        scene_type="interior",
        environment_prompt="从店门可以直接看到收银台，周围堆放着一些古董",
    )

    output_path = run_async(
        scene_reference_images.generate_scene_reference_image(
            project_dir=tmp_path,
            scene=scene,
            kind="master",
            style_name="live_action",
            style_prompt="grounded realism",
            avoid_instructions="no people",
        )
    )

    assert output_path == tmp_path / "assets" / "scenes" / "古董店" / "master.png"
    assert output_path.read_bytes() == b"scene-master"
    assert captured["api_key"] == "newapi-token"
    assert captured["base_url"] == "http://newapi.test/v1"
    assert captured["model"] == "LingShan-NB-2"
    assert captured["reference_images"] is None
    assert captured["image_config"] == {
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "output_format": "png",
    }
    assert "SCENE NAME: 古董店" in captured["prompt"]
    assert "从店门可以直接看到收银台" in captured["prompt"]


def test_newapi_scene_time_plate_master_injects_time_and_base_reference(monkeypatch, tmp_path):
    from novelvideo.generators import scene_reference_images
    from novelvideo.models import NovelScene

    captured = {}

    async def fake_call_newapi_image_api(**kwargs):
        captured.update(kwargs)
        return b"scene-night-master", "", ""

    base_master_path = tmp_path / "assets" / "scenes" / "古董店" / "master.png"
    base_master_path.parent.mkdir(parents=True)
    base_master_path.write_bytes(b"base-master-bytes")

    monkeypatch.setattr(
        scene_reference_images,
        "_call_newapi_image_api",
        fake_call_newapi_image_api,
    )
    _patch_scene_newapi_gateway(monkeypatch)
    monkeypatch.setattr(scene_reference_images, "SCENE_MASTER_IMAGE_PROVIDER", "newapi")
    monkeypatch.setattr(scene_reference_images, "SCENE_MASTER_IMAGE_MODEL", "LingShan-NB-2")

    scene = NovelScene(
        name="古董店_夜晚",
        base_scene_id="古董店",
        time_of_day="夜晚",
        scene_type="interior",
        environment_prompt="正面：收银台与古董柜\n光源：中性基础光",
    )

    output_path = run_async(
        scene_reference_images.generate_scene_reference_image(
            project_dir=tmp_path,
            scene=scene,
            kind="master",
        )
    )

    assert output_path == tmp_path / "assets" / "scenes" / "古董店_夜晚" / "master.png"
    assert output_path.read_bytes() == b"scene-night-master"
    assert captured["reference_images"] == [
        ("base_scene_master_master.png", b"base-master-bytes", "image/png")
    ]
    assert "TARGET TIME-OF-DAY PLATE: 夜晚" in captured["prompt"]
    assert "overall lighting must read as 夜晚" in captured["prompt"]
    assert "Keep the same architecture" in captured["prompt"]


def test_newapi_scene_variant_plate_master_keeps_described_lighting(monkeypatch, tmp_path):
    from novelvideo.generators import scene_reference_images
    from novelvideo.models import NovelScene

    captured = {}

    async def fake_call_newapi_image_api(**kwargs):
        captured.update(kwargs)
        return b"scene-variant-master", "", ""

    base_master_path = tmp_path / "assets" / "scenes" / "城市街道" / "master.png"
    base_master_path.parent.mkdir(parents=True)
    base_master_path.write_bytes(b"base-master-bytes")

    monkeypatch.setattr(
        scene_reference_images,
        "_call_newapi_image_api",
        fake_call_newapi_image_api,
    )
    _patch_scene_newapi_gateway(monkeypatch)
    monkeypatch.setattr(scene_reference_images, "SCENE_MASTER_IMAGE_PROVIDER", "newapi")
    monkeypatch.setattr(scene_reference_images, "SCENE_MASTER_IMAGE_MODEL", "LingShan-NB-2")

    scene = NovelScene(
        name="城市街道_雨夜版",
        base_scene_id="城市街道",
        variant_id="雨夜版",
        scene_type="exterior",
        environment_prompt="正面：湿漉沥青马路\n光源：路灯昏暗，积水反光，雨夜氛围",
    )

    output_path = run_async(
        scene_reference_images.generate_scene_reference_image(
            project_dir=tmp_path,
            scene=scene,
            kind="master",
        )
    )

    assert output_path == tmp_path / "assets" / "scenes" / "城市街道_雨夜版" / "master.png"
    assert captured["reference_images"] == [
        ("base_scene_master_master.png", b"base-master-bytes", "image/png")
    ]
    assert "STRUCTURED VARIANT PLATE" in captured["prompt"]
    assert "variant_id=雨夜版" in captured["prompt"]
    assert "do NOT neutralize" in captured["prompt"]
    # The base-scene neutralizer must not fire for variant plates.
    assert "IGNORE mood/time-of-day phrases" not in captured["prompt"]


def test_newapi_reverse_master_uses_master_reference_nanobanana2(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    from novelvideo.generators import scene_reference_images
    from novelvideo.models import NovelScene

    captured = {}

    async def fake_call_newapi_image_api(**kwargs):
        captured.update(kwargs)
        return b"scene-reverse", "", ""

    master_path = tmp_path / "assets" / "scenes" / "古董店" / "master.png"
    master_path.parent.mkdir(parents=True)
    master_path.write_bytes(b"master-bytes")

    monkeypatch.setattr(
        scene_reference_images,
        "_call_newapi_image_api",
        fake_call_newapi_image_api,
    )
    _patch_scene_newapi_gateway(monkeypatch)
    monkeypatch.setattr(scene_reference_images, "SCENE_REVERSE_MASTER_IMAGE_PROVIDER", "newapi")
    monkeypatch.setattr(
        scene_reference_images,
        "SCENE_REVERSE_MASTER_IMAGE_MODEL",
        "LingShan-NB-2",
    )

    scene = NovelScene(
        name="古董店",
        scene_type="interior",
        environment_prompt="从店门可以直接看到收银台，周围堆放着一些古董",
    )

    output_path = run_async(
        scene_reference_images.generate_scene_reference_image(
            project_dir=tmp_path,
            scene=scene,
            kind="reverse_master",
            style_name="live_action",
            style_prompt="grounded realism",
            avoid_instructions="no people",
        )
    )

    assert output_path == tmp_path / "assets" / "scenes" / "古董店" / "reverse_master.png"
    assert output_path.read_bytes() == b"scene-reverse"
    assert captured["api_key"] == "newapi-token"
    assert captured["base_url"] == "http://newapi.test/v1"
    assert captured["model"] == "LingShan-NB-2"
    assert captured["reference_images"] == [
        ("scene_master_master.png", b"master-bytes", "image/png")
    ]
    assert captured["image_config"] == {
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "output_format": "png",
    }
    assert "REFERENCE 1 = the scene's FRONT-FACING master" in captured["prompt"]


def test_newapi_reverse_master_can_use_gpt_image2_quality_low(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    from novelvideo.generators import scene_reference_images
    from novelvideo.models import NovelScene

    captured = {}

    async def fake_call_newapi_image_api(**kwargs):
        captured.update(kwargs)
        return b"scene-reverse", "", ""

    master_path = tmp_path / "assets" / "scenes" / "古董店" / "master.png"
    master_path.parent.mkdir(parents=True)
    master_path.write_bytes(b"master-bytes")

    monkeypatch.setattr(
        scene_reference_images,
        "_call_newapi_image_api",
        fake_call_newapi_image_api,
    )
    _patch_scene_newapi_gateway(monkeypatch)
    monkeypatch.setattr(scene_reference_images, "SCENE_REVERSE_MASTER_IMAGE_PROVIDER", "newapi")
    monkeypatch.setattr(
        scene_reference_images,
        "SCENE_REVERSE_MASTER_IMAGE_MODEL",
        "LingShan-G2",
    )

    scene = NovelScene(
        name="古董店",
        scene_type="interior",
        environment_prompt="从店门可以直接看到收银台，周围堆放着一些古董",
    )

    run_async(
        scene_reference_images.generate_scene_reference_image(
            project_dir=tmp_path,
            scene=scene,
            kind="reverse_master",
        )
    )

    assert captured["model"] == "LingShan-G2"
    assert captured["reference_images"] == [
        ("scene_master_master.png", b"master-bytes", "image/png")
    ]
    assert captured["image_config"] == {
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "output_format": "png",
        "quality": "low",
    }


def test_newapi_prop_reference_gpt_image2_sends_quality_medium(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    import httpx
    import novelvideo.config as config
    from novelvideo.generators import nanobanana_prop

    posted = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"prop-ref").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["url"] = url
            posted["headers"] = headers
            posted["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setenv("NEWAPI_API_KEY", "newapi-token")
    monkeypatch.setenv("NEWAPI_BASE_URL", "http://newapi.test/v1")
    monkeypatch.setenv("PROP_REF_IMAGE_PROVIDER", "newapi")
    monkeypatch.setenv("PROP_REF_IMAGE_MODEL", "LingShan-G2")
    importlib.reload(config)
    nanobanana_prop = importlib.reload(nanobanana_prop)
    monkeypatch.setattr(
        nanobanana_prop,
        "get_grid_generation_config",
        lambda: {"openai_image_quality": "medium"},
    )

    output_path = tmp_path / "assets" / "props" / "玉佩" / "reference_3view.png"
    result = run_async(
        nanobanana_prop.generate_prop_reference(
            visual_prompt="青绿色玉佩，边缘有金色纹路",
            output_path=str(output_path),
        )
    )

    assert result == str(output_path)
    assert output_path.read_bytes() == b"prop-ref"
    assert posted["url"] == "http://newapi.test/v1/images/generations"
    assert posted["headers"]["Authorization"] == "Bearer newapi-token"
    assert posted["json"]["model"] == "LingShan-G2"
    assert posted["json"]["quality"] == "medium"
    assert posted["json"]["extra_fields"] == {
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "resolution": "1k",
        "quality": "medium",
    }


def test_newapi_prop_reference_preserves_explicit_nanobanana2(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    import httpx
    import novelvideo.config as config
    from novelvideo.generators import nanobanana_prop

    posted = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"prop-ref").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setenv("NEWAPI_API_KEY", "newapi-token")
    monkeypatch.setenv("NEWAPI_BASE_URL", "http://newapi.test/v1")
    monkeypatch.setenv("PROP_REF_IMAGE_PROVIDER", "newapi")
    monkeypatch.setenv("PROP_REF_IMAGE_MODEL", "LingShan-NB-2")
    monkeypatch.setenv("NEWAPI_NANOBANANA2_ENABLED", "true")
    importlib.reload(config)
    nanobanana_prop = importlib.reload(nanobanana_prop)
    monkeypatch.setattr(
        nanobanana_prop,
        "get_grid_generation_config",
        lambda: {"openai_image_quality": "medium"},
    )

    output_path = tmp_path / "assets" / "props" / "玉佩" / "reference_3view.png"
    result = run_async(
        nanobanana_prop.generate_prop_reference(
            visual_prompt="青绿色玉佩，边缘有金色纹路",
            output_path=str(output_path),
        )
    )

    assert result == str(output_path)
    assert output_path.read_bytes() == b"prop-ref"
    assert posted["json"]["model"] == "LingShan-NB-2"
    assert "quality" not in posted["json"]
    assert posted["json"]["extra_fields"] == {
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "resolution": "1k",
    }


def test_newapi_prop_reference_reraises_insufficient_credit(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    import novelvideo.config as config
    from novelvideo.generators import nanobanana_prop

    async def fake_call_newapi_image_api(**_kwargs):
        raise InsufficientCreditsError(user_id="usr_1", cost=5, balance=0)

    monkeypatch.setenv("NEWAPI_API_KEY", "newapi-token")
    monkeypatch.setenv("NEWAPI_BASE_URL", "http://newapi.test/v1")
    monkeypatch.setenv("PROP_REF_IMAGE_PROVIDER", "newapi")
    monkeypatch.setenv("PROP_REF_IMAGE_MODEL", "LingShan-G2")
    importlib.reload(config)
    nanobanana_prop = importlib.reload(nanobanana_prop)
    monkeypatch.setattr(nanobanana_prop, "_call_newapi_image_api", fake_call_newapi_image_api)
    monkeypatch.setattr(
        nanobanana_prop,
        "get_grid_generation_config",
        lambda: {"openai_image_quality": "medium"},
    )

    with pytest.raises(InsufficientCreditsError):
        run_async(
            nanobanana_prop.generate_prop_reference(
                visual_prompt="青绿色玉佩，边缘有金色纹路",
                output_path=str(tmp_path / "reference_3view.png"),
            )
        )


def test_freezone_single_image_generation_routes_newapi(monkeypatch, tmp_path):
    from novelvideo.generators import nanobanana_grid

    captured = {}

    async def fake_call_newapi_image_api(**kwargs):
        captured.update(kwargs)
        return b"freezone-image", "", ""

    monkeypatch.setattr(nanobanana_grid, "_call_newapi_image_api", fake_call_newapi_image_api)

    output_path = tmp_path / "freezone.png"
    image_path = run_async(
        nanobanana_grid.generate_text_to_image(
            prompt="freezone prompt",
            output_path=str(output_path),
            aspect_ratio="1:1",
            image_size="2K",
            quality="medium",
            config={
                "provider": "newapi",
                "api_key": "newapi-token",
                "model": "LingShan-G2",
                "base_url": "http://newapi.test/v1",
                "openai_image_quality": "medium",
                "openai_sketch_image_quality": "low",
                "image_size": "2K",
                "mode": "1x1",
                "rows": 1,
                "cols": 1,
                "total_panels": 1,
            },
        )
    )

    assert image_path == output_path
    assert output_path.read_bytes() == b"freezone-image"
    assert captured["api_key"] == "newapi-token"
    assert captured["model"] == "LingShan-G2"
    assert captured["prompt"] == "freezone prompt"
    assert captured["reference_images"] is None
    assert captured["base_url"] == "http://newapi.test/v1"
    assert captured["image_config"] == {
        "aspect_ratio": "1:1",
        "image_size": "2K",
        "quality": "medium",
    }


def run_async(coro):
    import asyncio

    return asyncio.run(coro)
