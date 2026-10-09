"""桌面启动器打开页面时必须击穿旧首页缓存。"""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_village_canvas_launch_open_url_is_cache_busted(monkeypatch):
    module = _load_module("village_canvas_launch", ROOT / "village_canvas_launch.py")
    monkeypatch.setattr(module.time, "time", lambda: 1234.567)

    assert (
        module._with_launch_cache_bust("http://127.0.0.1:8784/")
        == "http://127.0.0.1:8784/?__village_canvas_launch=1234567"
    )


def test_open_when_ready_preserves_route_and_replaces_old_launch_token(monkeypatch):
    module = _load_module(
        "village_canvas_open_when_ready", ROOT / "village_canvas_open_when_ready.py"
    )
    monkeypatch.setattr(module.time, "time", lambda: 2000.0)

    url = module._with_launch_cache_bust(
        "http://127.0.0.1:8784/projects/p1/freezone?canvas=c1&__village_canvas_launch=old#node"
    )

    assert (
        url
        == "http://127.0.0.1:8784/projects/p1/freezone?canvas=c1&__village_canvas_launch=2000000#node"
    )


def test_launcher_evaluates_canonical_conditional_overrides(monkeypatch, tmp_path):
    module = _load_module("village_canvas_launch_env", ROOT / "village_canvas_launch.py")
    start_bat = tmp_path / "start.bat"
    start_bat.write_text(
        "\n".join(
            (
                'set "HK_API_PRIMARY=https://public.example/v1"',
                'set "HK_API_FALLBACK=http://10.0.0.1:3000/v1"',
                'set "HK_API_BASE=%HK_API_FALLBACK%"',
                'if /I "%VILLAGE_CANVAS_HK_API_FORCE%"=="public" set "HK_API_BASE=%HK_API_PRIMARY%"',
                'if not defined NEWAPI_TEXT_TRUST_ENV set "NEWAPI_TEXT_TRUST_ENV=false"',
                'set "NEWAPI_BASE_URL=%HK_API_BASE%"',
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "START_BAT", start_bat)
    monkeypatch.setenv("VILLAGE_CANVAS_HK_API_FORCE", "public")
    monkeypatch.delenv("NEWAPI_TEXT_TRUST_ENV", raising=False)

    env = module._read_startbat_env()

    assert env["HK_API_BASE"] == "https://public.example/v1"
    assert env["NEWAPI_BASE_URL"] == "https://public.example/v1"
    assert env["NEWAPI_TEXT_TRUST_ENV"] == "false"


def test_launcher_ignores_dead_configuration_after_endlocal(monkeypatch, tmp_path):
    module = _load_module("village_canvas_launch_dead_code", ROOT / "village_canvas_launch.py")
    start_bat = tmp_path / "start.bat"
    start_bat.write_text(
        "\n".join(
            (
                'set "ACTIVE_ROUTE=https://active.example/v1"',
                "endlocal",
                'set "ACTIVE_ROUTE=https://dead.example/v1"',
                'set "LEGACY_FALLBACK=should-not-leak"',
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "START_BAT", start_bat)

    env = module._read_startbat_env()

    assert env["ACTIVE_ROUTE"] == "https://active.example/v1"
    assert "LEGACY_FALLBACK" not in env
