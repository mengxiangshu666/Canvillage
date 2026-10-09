from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_ROOT = REPO_ROOT
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "Deploy-VillageInfiniteCanvas.ps1"


def _load_run_api_module(monkeypatch):
    # The root runner is the only shipped/runtime entrypoint.  The historical
    # scripts/portable copy was removed to prevent two implementations from
    # drifting and making tests exercise a path the product never starts.
    monkeypatch.syspath_prepend(str(RUNNER_ROOT))
    module_path = RUNNER_ROOT / "village_canvas_run_api.py"
    spec = importlib.util.spec_from_file_location("village_canvas_run_api", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_rotates_all_backend_logs_before_open(monkeypatch, tmp_path):
    run_api = _load_run_api_module(monkeypatch)
    monkeypatch.setenv("VILLAGE_CANVAS_LOG_MAX_BYTES", "16")
    monkeypatch.setenv("VILLAGE_CANVAS_LOG_BACKUPS", "2")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    expected_tails: dict[str, bytes] = {}
    for index, name in enumerate(run_api.ROTATED_LOG_NAMES):
        content = (f"{index}-".encode() + bytes(range(64)))
        (log_dir / name).write_bytes(content)
        expected_tails[name] = content[-16:]

    rotated = run_api._rotate_logs(log_dir)

    assert [path.name for path in rotated] == list(run_api.ROTATED_LOG_NAMES)
    for name in run_api.ROTATED_LOG_NAMES:
        assert (log_dir / name).read_bytes() == b""
        assert (log_dir / f"{name}.1").read_bytes() == expected_tails[name]


def test_runner_uses_safe_defaults_for_invalid_log_limits(monkeypatch):
    run_api = _load_run_api_module(monkeypatch)
    monkeypatch.setenv("VILLAGE_CANVAS_LOG_MAX_BYTES", "not-an-int")
    monkeypatch.setenv("VILLAGE_CANVAS_LOG_BACKUPS", "0")

    assert run_api._positive_env_int(
        "VILLAGE_CANVAS_LOG_MAX_BYTES", run_api.DEFAULT_LOG_MAX_BYTES
    ) == run_api.DEFAULT_LOG_MAX_BYTES
    assert run_api._positive_env_int(
        "VILLAGE_CANVAS_LOG_BACKUPS", run_api.DEFAULT_LOG_BACKUPS
    ) == run_api.DEFAULT_LOG_BACKUPS


def test_runner_direct_invocation_uses_project_asset_tree(monkeypatch, tmp_path):
    run_api = _load_run_api_module(monkeypatch)
    # The runner writes defaults directly. Restore the whole mapping even for
    # keys that were absent before the test (delenv alone cannot track those).
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for key in (
        "NOVELVIDEO_DATA_ROOT",
        "NOVELVIDEO_STATE_DIR",
        "NOVELVIDEO_OUTPUT_DIR",
        "NOVELVIDEO_RUNTIME_DIR",
    ):
        monkeypatch.delenv(key, raising=False)

    data = tmp_path / "项目资产"
    run_api._apply_data_root_defaults(data)

    assert os.environ["NOVELVIDEO_DATA_ROOT"] == str(data)
    assert os.environ["NOVELVIDEO_STATE_DIR"] == str(data / "state")
    assert os.environ["NOVELVIDEO_OUTPUT_DIR"] == str(data / "output")
    assert os.environ["NOVELVIDEO_RUNTIME_DIR"] == str(data / "runtime")


def test_runner_disables_optional_pydantic_plugins_before_app_import():
    source = (RUNNER_ROOT / "village_canvas_run_api.py").read_text(encoding="utf-8")
    disable_at = source.index('PYDANTIC_DISABLE_PLUGINS", "__all__"')
    app_import_at = source.index("from novelvideo.api.app import app as asgi_app")

    assert disable_at < app_import_at


def test_deploy_syncs_the_native_skill_tree_without_hermes_runtime():
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "$sourceAgentSkills" in source
    assert "$targetAgentSkills" in source
    assert "Area 'agent-skills'" in source
    assert "Get-TreeChanges -Source $sourceHermes" not in source
    assert "Get-TreeChanges -Source $sourceHermesRuntime" not in source
    assert "$migratedHermesWorkspaces = Sync-ExistingHermesWorkspaces" not in source
    assert "$migratedCustomSkills = Move-RetiredCustomSkillStore" not in source


def test_rotation_keeps_only_the_configured_backup_chain(monkeypatch, tmp_path):
    _load_run_api_module(monkeypatch)
    from village_canvas_rotate_log import rotate_log

    log_path = tmp_path / "backend-daemon.err.log"
    for marker in (b"a", b"b", b"c"):
        log_path.write_bytes(marker * 20)
        assert rotate_log(log_path, max_bytes=16, backups=2) is True

    assert (tmp_path / "backend-daemon.err.log.1").read_bytes() == b"c" * 16
    assert (tmp_path / "backend-daemon.err.log.2").read_bytes() == b"b" * 16
    assert not (tmp_path / "backend-daemon.err.log.3").exists()
