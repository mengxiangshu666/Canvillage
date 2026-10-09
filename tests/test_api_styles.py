import io
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

pytestmark = pytest.mark.m04


def _client():
    from novelvideo.api.routes import styles

    app = FastAPI()
    app.include_router(styles.router)
    app.dependency_overrides[styles.get_api_user] = lambda: {"username": "alice"}
    return TestClient(app)


def _png_bytes(color=(120, 80, 200)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_style_preview_get_returns_image_without_generation():
    response = _client().get("/styles/anime/preview")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")
    assert response.content.startswith((b"RIFF", b"\x89PNG\r\n\x1a\n"))


def test_style_preview_post_route_still_exists(monkeypatch, tmp_path):
    from novelvideo import config as app_config
    from novelvideo.generators import nanobanana_grid

    monkeypatch.setitem(
        app_config.IMAGE_GENERATION_SELECTIONS["newapi_gpt_image2"],
        "model",
        "test-image-model",
    )

    class FakeGenerator:
        async def generate_single_preview(self, **kwargs):
            assert kwargs["prompt"]
            assert kwargs["style_config"]["style_instructions"]
            return b"\x89PNG\r\n\x1a\n"

    monkeypatch.setattr(
        nanobanana_grid, "create_grid_generator", lambda **_: FakeGenerator()
    )

    response = _client().post(
        "/styles/anime/preview",
        json={"model": "newapi_gpt_image2"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_style_preview_get_supports_optimized_webp_assets():
    response = _client().get("/styles/neo_noir/preview")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/webp")
    assert response.headers["cache-control"] == "public, max-age=86400, immutable"
    assert response.content.startswith(b"RIFF")


def test_guoman_fantasy_is_listed_as_3d_animation_preset():
    response = _client().get("/styles")

    assert response.status_code == 200
    styles = response.json()["data"]
    guoman = next(style for style in styles if style["id"] == "guoman_fantasy")
    assert guoman["label"] == "3D玄幻国漫"
    assert guoman["style_family"] == "animation"
    assert guoman["animation_subtype"] == "3d"


def test_create_style_accepts_frontend_payload_with_top_level_id_and_name(
    monkeypatch, tmp_path
):
    from novelvideo.api.deps import ProjectResolution
    from novelvideo.api.routes import styles
    from novelvideo.services.style_service import StyleService

    saved = []

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        assert project == "demo"
        assert user == {"username": "alice"}
        assert required_role == "editor"
        return ProjectResolution(
            ctx=None,
            username="alice",
            project_name="demo",
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    def fake_save_custom_style(style_id, config, **kwargs):
        saved.append((style_id, config, kwargs))
        return True

    monkeypatch.setattr(styles, "resolve_project_scope", fake_resolve_project_scope)
    monkeypatch.setattr(StyleService, "save_custom_style", fake_save_custom_style)

    response = _client().post(
        "/styles",
        json={
            "id": "custom_drama",
            "name": "自定义剧集风格",
            "project": "demo",
            "config": {
                "label": "自定义剧集风格",
                "style_instructions": "cinematic live action",
                "avoid_instructions": "anime",
                "style_tag": "LIVE-ACTION",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert len(saved) == 1
    style_id, config, kwargs = saved[0]
    assert style_id == "custom_drama"
    assert config.id == "custom_drama"
    assert config.name == "自定义剧集风格"
    assert config.label == "自定义剧集风格"
    assert kwargs == {"username": "alice", "project": "demo", "project_dir": tmp_path}


def test_selected_custom_style_cannot_change_during_an_active_run(
    monkeypatch, tmp_path
):
    from novelvideo.api.deps import ProjectResolution
    from novelvideo.api.routes import styles
    from novelvideo.services.style_service import StyleService

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        return ProjectResolution(
            ctx=None,
            username="alice",
            project_name="demo",
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    save_style = AsyncMock()
    monkeypatch.setattr(styles, "resolve_project_scope", fake_resolve_project_scope)
    monkeypatch.setattr(
        styles, "_selected_style_has_active_run", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(StyleService, "save_custom_style", save_style)

    response = _client().post(
        "/styles",
        json={
            "id": "custom_drama",
            "name": "运行中风格",
            "project": "demo",
            "config": {"style_instructions": "new instructions"},
        },
    )

    assert response.status_code == 409
    assert "总控运行期间" in response.json()["error"]
    save_style.assert_not_called()


def test_style_preview_service_stages_validates_and_finalizes(tmp_path):
    from novelvideo.services.style_service import StyleService

    token = StyleService.stage_style_preview(
        tmp_path,
        "cinematic_custom",
        "reference.png",
        _png_bytes(),
        "image/png",
    )
    staged = tmp_path / token
    assert staged.is_file()

    preview_path = StyleService.finalize_style_preview(
        tmp_path,
        "cinematic_custom",
        token,
    )
    published = tmp_path / preview_path
    assert published.is_file()
    assert not staged.exists()
    assert (
        StyleService.validate_style_preview_path(
            tmp_path,
            "cinematic_custom",
            preview_path,
        )
        == preview_path
    )
    assert StyleService.find_style_preview(tmp_path, "cinematic_custom").is_file()
    with Image.open(StyleService.find_style_preview(tmp_path, "cinematic_custom")) as image:
        assert image.size == (768, 432)


def test_style_preview_service_rejects_fake_images_and_path_escape(tmp_path):
    from novelvideo.services.style_service import StyleService

    with pytest.raises(ValueError, match="valid image"):
        StyleService.stage_style_preview(
            tmp_path,
            "custom",
            "fake.png",
            b"not an image",
            "image/png",
        )
    with pytest.raises(ValueError, match="Invalid style id"):
        StyleService.stage_style_preview(
            tmp_path,
            "../escape",
            "reference.png",
            _png_bytes(),
            "image/png",
        )


def test_custom_style_preview_persists_through_api_and_delete_cleans_assets(
    monkeypatch,
    tmp_path,
):
    from novelvideo import project_config
    from novelvideo.api.deps import ProjectResolution
    from novelvideo.api.routes import styles

    output_dir = tmp_path / "output"
    state_dir = tmp_path / "state"
    output_dir.mkdir()
    state_dir.mkdir()

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        assert project == "demo"
        return ProjectResolution(
            ctx=None,
            username="alice",
            project_name="demo",
            project_dir=output_dir,
            output_dir=str(output_dir),
            state_dir=str(state_dir),
            runtime_dir=str(tmp_path / "runtime"),
        )

    monkeypatch.setattr(styles, "resolve_project_scope", fake_resolve_project_scope)
    monkeypatch.setattr(project_config, "OUTPUT_DIR", state_dir.parent)

    client = _client()
    upload = client.post(
        "/projects/demo/styles/preview-upload",
        data={"style_id": "cinematic_custom"},
        files={"file": ("reference.png", _png_bytes(), "image/png")},
    )
    assert upload.status_code == 200
    assert upload.json()["ok"] is True
    token = upload.json()["data"]["preview_token"]

    created = client.post(
        "/styles",
        json={
            "id": "cinematic_custom",
            "name": "电影自定义",
            "project": "demo",
            "preview_token": token,
            "config": {"style_instructions": "cinematic"},
        },
    )
    assert created.json()["ok"] is True

    listed = client.get("/styles", params={"project": "demo"}).json()["data"]
    custom = next(item for item in listed if item["id"] == "cinematic_custom")
    assert custom["preview_path"].startswith("assets/styles/cinematic_custom/reference")
    assert custom["preview_url"].startswith("/api/v1/projects/demo/media/")

    preview = client.get(
        "/styles/cinematic_custom/preview",
        params={"project": "demo"},
    )
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("image/")

    deleted = client.delete(
        "/styles/cinematic_custom",
        params={"project": "demo"},
    )
    assert deleted.json()["ok"] is True
    assert not (output_dir / "assets" / "styles" / "cinematic_custom").exists()


def test_config_style_helpers_do_not_fallback_to_hardcoded_presets(monkeypatch):
    from novelvideo import config

    class BrokenStyleService:
        @staticmethod
        def get_style(*args, **kwargs):
            raise RuntimeError("style service unavailable")

        @staticmethod
        def get_style_labels(*args, **kwargs):
            raise RuntimeError("style service unavailable")

        @staticmethod
        def list_all_styles(*args, **kwargs):
            raise RuntimeError("style service unavailable")

    real_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "novelvideo.services.style_service":

            class Module:
                StyleService = BrokenStyleService

            return Module
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    with pytest.raises(RuntimeError, match="style service unavailable"):
        config.get_style_preset("chinese_period_drama")
    with pytest.raises(RuntimeError, match="style service unavailable"):
        config.get_style_labels()
    with pytest.raises(RuntimeError, match="style service unavailable"):
        config.list_available_styles()
