"""VILLAGE_CANVAS_FRONTEND_DIST 门控的 SPA 静态伺服契约。

Starlette StaticFiles 未命中时是 raise HTTPException(404) 而非返回 404
响应,SPA 回落必须捕获它;深链接/刷新客户端路由要拿到 index.html,
而缺失的静态资产(带扩展名)仍应如实 404。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.m07


def _assert_no_store_shell_cache(cache_control: str):
    tokens = {part.strip() for part in cache_control.split(",")}
    assert {"no-store", "no-cache", "must-revalidate", "max-age=0"} <= tokens


@pytest.fixture()
def spa_client(tmp_path: Path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>SPA-SHELL</html>", encoding="utf-8")
    (dist / "version.json").write_text(
        '{"version":"test","buildId":"build-test"}', encoding="utf-8"
    )
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setenv("VILLAGE_CANVAS_FRONTEND_DIST", str(dist))

    from starlette.testclient import TestClient

    from novelvideo.api.app import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def test_root_serves_index(spa_client):
    r = spa_client.get("/")
    assert r.status_code == 200
    assert "SPA-SHELL" in r.text


def test_real_asset_is_served(spa_client):
    r = spa_client.get("/assets/app.js")
    assert r.status_code == 200
    assert r.text == "console.log(1)"


def test_deep_link_falls_back_to_index(spa_client):
    r = spa_client.get("/projects/abc/ingest")
    assert r.status_code == 200
    assert "SPA-SHELL" in r.text


def test_version_manifest_is_not_cached(spa_client):
    r = spa_client.get("/version.json")
    assert r.status_code == 200
    _assert_no_store_shell_cache(r.headers["Cache-Control"])
    assert r.headers["Pragma"] == "no-cache"
    assert r.headers["Expires"] == "0"


def test_spa_shell_is_not_cached(spa_client):
    r = spa_client.get("/")
    assert r.status_code == 200
    _assert_no_store_shell_cache(r.headers["Cache-Control"])
    assert r.headers["Pragma"] == "no-cache"
    assert r.headers["Expires"] == "0"

    deep_link = spa_client.get("/projects/abc/freezone")
    assert deep_link.status_code == 200
    _assert_no_store_shell_cache(deep_link.headers["Cache-Control"])
    assert deep_link.headers["Pragma"] == "no-cache"
    assert deep_link.headers["Expires"] == "0"


def test_hashed_assets_keep_normal_static_cache_behavior(spa_client):
    r = spa_client.get("/assets/app.js")
    assert r.status_code == 200
    assert "no-store" not in r.headers.get("Cache-Control", "")


def test_missing_asset_with_extension_stays_404(spa_client):
    assert spa_client.get("/missing.js").status_code == 404


def test_api_routes_take_precedence(spa_client):
    r = spa_client.get("/api/v1/config")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_unknown_api_routes_remain_json_404_instead_of_spa_shell(spa_client):
    r = spa_client.get("/api/v1/does-not-exist")
    assert r.status_code == 404
    assert "SPA-SHELL" not in r.text
    assert r.headers["content-type"].startswith("application/json")


def test_known_post_api_wrong_method_is_not_rewritten_to_spa_shell(spa_client):
    r = spa_client.get("/api/v1/model-gateway/direct-models/image")
    # Depending on optional router registration this is either a method error
    # or a missing endpoint, but neither case may fall through to the SPA.
    assert r.status_code in {404, 405}
    assert "SPA-SHELL" not in r.text
