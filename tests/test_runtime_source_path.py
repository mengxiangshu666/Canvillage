"""便携运行版必须优先加载本仓 src，而不是旧 runtime/env 包。"""

from __future__ import annotations

from pathlib import Path


def test_launcher_prefers_source_tree_for_api_imports():
    runner = Path(__file__).resolve().parents[1] / "village_canvas_run_api.py"
    source = runner.read_text(encoding="utf-8")

    assert 'source_path = root / "src"' in source
    assert 'sys.path.insert(0, source)' in source


def test_canonical_start_bat_puts_source_before_runtime_env():
    root = Path(__file__).resolve().parents[1]
    expected = (
        r'set "PYTHONPATH=%ROOT%src;%ROOT%runtime\env;'
        r"%ROOT%runtime\env\win32;%ROOT%runtime\env\win32\lib;"
        r'%ROOT%runtime\env\Pythonwin"'
    )
    assert expected in (root / "启动村长无限画布.bat").read_text(encoding="utf-8")


def test_canonical_launchers_delegate_to_the_python_runtime_owner():
    root = Path(__file__).resolve().parents[1]
    bat = (root / "启动村长无限画布.bat").read_text(encoding="utf-8")
    vbs = (root / "启动村长无限画布.vbs").read_text(encoding="utf-8")

    assert "VILLAGE_CANVAS_LAUNCHER_ACTIVE" in bat
    assert r'"%~dp0runtime\python\python.exe" "%~dp0village_canvas_launch.py" %*' in bat
    assert "village_canvas_launch.py" in vbs
    assert "启动村长无限画布.bat" not in vbs


def test_compatibility_start_bat_delegates_to_the_canonical_launcher():
    root = Path(__file__).resolve().parents[1]
    alias_text = (root / "村长无限画布-Start.bat").read_text(encoding="utf-8")

    assert r'"%ROOT%runtime\python\python.exe" "%ROOT%village_canvas_launch.py" %*' in alias_text
    assert "PYTHONPATH=" not in alias_text


def test_python_launcher_fallback_data_path_is_package_relative():
    runner = Path(__file__).resolve().parents[1] / "village_canvas_launch.py"
    source = runner.read_text(encoding="utf-8")

    assert 'env.setdefault("DATA_DIR", str(ROOT / "项目资产"))' in source
    assert r"C:\Users\example\Desktop\村长无限画布" not in source
