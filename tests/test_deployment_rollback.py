"""Exercise rollback filesystem changes with process/network calls stubbed."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(SHELL is None, reason="PowerShell is unavailable")


def _source_gate_fixture(target):
    architecture = target / "scripts/architecture"
    architecture.mkdir(parents=True, exist_ok=True)
    for name in ("check_file_sizes.py", "file_size_baseline.json"):
        shutil.copyfile(ROOT / "scripts/architecture" / name, architecture / name)
    python = target / ".venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    shutil.copy2(sys.executable, python)
    (target / ".venv/pyvenv.cfg").write_text(
        f"home = {sys.base_prefix}\ninclude-system-site-packages = false\n",
        encoding="utf-8",
    )


def _fixture(tmp_path, new_files):
    target = tmp_path / "product"
    backup = tmp_path / "backup"
    for relative in ("_stop.ps1", "village_canvas_launch.py", "runtime/python/python.exe"):
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    backup.mkdir()
    (backup / "rollback-manifest.json").write_text(
        json.dumps({"schema": "village_deployment_rollback.v1", "newFiles": new_files}),
        encoding="utf-8",
    )
    return target, backup


def _run(tmp_path, target, backup, *, plan=False):
    harness = tmp_path / "harness.ps1"
    harness.write_text(
        """param($Script, $Target, $Backup, [switch]$Plan)
$script:started = $false
function Invoke-WebRequest {
    if (-not $script:started) { throw 'fixture offline' }
    return @{ StatusCode = 200; Content = '{"status":"ok"}' }
}
function Start-Process { $script:started = $true }
try {
    $result = & $Script -TargetRoot $Target -BackupRoot $Backup -PlanOnly:$Plan
    $result | ConvertTo-Json -Depth 5
} catch { Write-Output $_.Exception.Message; exit 1 }
""",
        encoding="utf-8",
    )
    command = [SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness),
               "-Script", str(ROOT / "scripts/Rollback-VillageInfiniteCanvas.ps1"),
               "-Target", str(target), "-Backup", str(backup)]
    if plan:
        command.append("-Plan")
    return subprocess.run(command, capture_output=True, text=True, timeout=30)


def test_rollback_restores_all_product_areas_and_removes_only_manifest_additions(tmp_path):
    new = [{"area": "backend", "relative": "services/_video_request_contract.py"}]
    target, backup = _fixture(tmp_path, new)
    added = target / "runtime/env/novelvideo/services/_video_request_contract.py"
    added.parent.mkdir(parents=True)
    added.write_text("new module", encoding="utf-8")
    private = target / "项目资产/private.txt"
    private.parent.mkdir()
    private.write_text("untouched", encoding="utf-8")
    pairs = [("backend", "runtime/env/novelvideo", "freezone/contract.py"),
             ("frontend", "frontend/dist", "index.html"),
             ("agent-skills", "agent_skills", "example/SKILL.md")]
    for area, destination, relative in pairs:
        saved = backup / area / relative
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text("old", encoding="utf-8")
        current = target / destination / relative
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_text("new", encoding="utf-8")
    completed = _run(tmp_path, target, backup)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not added.exists()
    assert private.read_text(encoding="utf-8") == "untouched"
    for _, destination, relative in pairs:
        assert (target / destination / relative).read_text(encoding="utf-8") == "old"


@pytest.mark.parametrize("entry", [
    {"area": "backend", "relative": "../../../../项目资产/private.txt"},
    {"area": "private", "relative": "private.txt"},
    {"area": "backend", "relative": ""},
])
def test_invalid_manifest_is_rejected_before_file_changes(tmp_path, entry):
    target, backup = _fixture(tmp_path, [entry])
    saved = backup / "backend/keep.py"
    saved.parent.mkdir()
    saved.write_text("old", encoding="utf-8")
    completed = _run(tmp_path, target, backup)
    assert completed.returncode != 0
    assert not (target / "runtime/env/novelvideo/keep.py").exists()


def test_rollback_plan_does_not_modify_files_or_start_service(tmp_path):
    target, backup = _fixture(tmp_path, [{"area": "backend", "relative": "added.py"}])
    added = target / "runtime/env/novelvideo/added.py"
    added.parent.mkdir(parents=True)
    added.write_text("new", encoding="utf-8")
    completed = _run(tmp_path, target, backup, plan=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert added.exists()
    assert json.loads(completed.stdout)["planned"] is True


def test_deploy_generated_manifest_supports_roundtrip_rollback(tmp_path):
    target, _ = _fixture(tmp_path, [])
    scripts = target / "scripts"
    scripts.mkdir()
    deploy = scripts / "Deploy-VillageInfiniteCanvas.ps1"
    shutil.copyfile(ROOT / "scripts/Deploy-VillageInfiniteCanvas.ps1", deploy)
    _source_gate_fixture(target)
    for area in ("src/novelvideo", "runtime/env/novelvideo", "frontend/src",
                 "frontend/dist", "agent_skills"):
        (target / area).mkdir(parents=True, exist_ok=True)
    (target / "frontend/dist/index.html").write_text("fixture", encoding="utf-8")
    original = target / "runtime/env/novelvideo/contract.py"
    original.write_text("old", encoding="utf-8")
    (target / "src/novelvideo/contract.py").write_text("new", encoding="utf-8")
    (target / "src/novelvideo/added.py").write_text("addition", encoding="utf-8")
    stale = target / "runtime/env/novelvideo/retired.py"
    stale.write_text("retired content", encoding="utf-8")
    harness = tmp_path / "deploy-harness.ps1"
    harness.write_text(
        """param($Script)
$script:started = $false
function Invoke-WebRequest {
    if (-not $script:started) { throw 'fixture offline' }
    return @{ StatusCode = 200; Content = '{"status":"ok"}' }
}
function Start-Process { $script:started = $true }
& $Script -SkipBuild -ForceRestart | ConvertTo-Json -Depth 5
""", encoding="utf-8",
    )
    completed = subprocess.run(
        [SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness),
         "-Script", str(deploy)], capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    backup = Path(json.loads(completed.stdout)["backup"])
    manifest = json.loads((backup / "rollback-manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["newFiles"] == [{"area": "backend", "relative": "added.py"}]
    assert original.read_text(encoding="utf-8") == "new"
    assert not stale.exists()
    rolled_back = _run(tmp_path, target, backup)
    assert rolled_back.returncode == 0, rolled_back.stdout + rolled_back.stderr
    assert original.read_text(encoding="utf-8") == "old"
    assert stale.read_text(encoding="utf-8") == "retired content"
    assert not (target / "runtime/env/novelvideo/added.py").exists()


@pytest.mark.parametrize("build_fails", [False, True])
def test_local_build_preserves_prebuild_dist_even_when_builder_fails(tmp_path, build_fails):
    target, _ = _fixture(tmp_path, [])
    scripts = target / "scripts"
    scripts.mkdir()
    deploy = scripts / "Deploy-VillageInfiniteCanvas.ps1"
    shutil.copyfile(ROOT / "scripts/Deploy-VillageInfiniteCanvas.ps1", deploy)
    _source_gate_fixture(target)
    for relative in ("src/novelvideo", "runtime/env/novelvideo", "frontend/src",
                     "frontend/dist/assets", "agent_skills"):
        (target / relative).mkdir(parents=True, exist_ok=True)
    dist = target / "frontend/dist"
    (dist / "index.html").write_text("old index", encoding="utf-8")
    (dist / "assets/old.js").write_text("old chunk", encoding="utf-8")
    (dist / "assets/shared.js").write_text("old shared", encoding="utf-8")
    original = {p.relative_to(dist).as_posix(): p.read_bytes() for p in dist.rglob("*") if p.is_file()}
    harness = tmp_path / "local-build.ps1"
    harness.write_text(
        """param($Script, [int]$BuildExit)
$script:started = $false
function Invoke-WebRequest {
    if (-not $script:started) { throw 'fixture offline' }
    return @{ StatusCode = 200; Content = '{"status":"ok"}' }
}
function Start-Process { $script:started = $true }
function pnpm {
    Set-Content -LiteralPath 'dist/index.html' -Value 'new index'
    Set-Content -LiteralPath 'dist/assets/shared.js' -Value 'new shared'
    Set-Content -LiteralPath 'dist/assets/new.js' -Value 'new chunk'
    Remove-Item -LiteralPath 'dist/assets/old.js'
    $global:LASTEXITCODE = $BuildExit
}
try { & $Script -ForceRestart | ConvertTo-Json -Depth 5 }
catch { Write-Output $_.Exception.Message; exit 1 }
""", encoding="utf-8",
    )
    completed = subprocess.run(
        [SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness),
         "-Script", str(deploy), "-BuildExit", "1" if build_fails else "0"],
        capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == (1 if build_fails else 0), completed.stdout + completed.stderr
    backups = list((target / "_deploy_backups").iterdir())
    assert len(backups) == 1
    backup = backups[0]
    manifest = json.loads((backup / "rollback-manifest.json").read_text(encoding="utf-8-sig"))
    assert len(manifest["newFiles"]) == 1
    assert manifest["newFiles"][0]["area"] == "frontend"
    assert manifest["newFiles"][0]["relative"].replace("\\", "/") == "assets/new.js"
    saved = {p.relative_to(backup / "frontend").as_posix(): p.read_bytes()
             for p in (backup / "frontend").rglob("*") if p.is_file()}
    assert saved == original
    if not build_fails:
        assert json.loads(completed.stdout)["frontendBuiltFiles"] == 3
    rolled_back = _run(tmp_path, target, backup)
    assert rolled_back.returncode == 0, rolled_back.stdout + rolled_back.stderr
    restored = {p.relative_to(dist).as_posix(): p.read_bytes() for p in dist.rglob("*") if p.is_file()}
    assert restored == original


def test_deploy_plan_only_does_not_build_or_backup(tmp_path):
    target, _ = _fixture(tmp_path, [])
    scripts = target / "scripts"
    scripts.mkdir()
    deploy = scripts / "Deploy-VillageInfiniteCanvas.ps1"
    shutil.copyfile(ROOT / "scripts/Deploy-VillageInfiniteCanvas.ps1", deploy)
    _source_gate_fixture(target)
    for relative in ("src/novelvideo", "runtime/env/novelvideo", "frontend/src",
                     "frontend/dist", "agent_skills"):
        (target / relative).mkdir(parents=True, exist_ok=True)
    index = target / "frontend/dist/index.html"
    index.write_text("old", encoding="utf-8")
    harness = tmp_path / "plan.ps1"
    harness.write_text(
        """param($Script)
function pnpm { throw 'PlanOnly attempted build' }
function Start-Process { throw 'PlanOnly attempted start' }
function Invoke-WebRequest { return @{ StatusCode = 200; Content = '{"status":"ok"}' } }
& $Script -PlanOnly | ConvertTo-Json -Depth 5
""", encoding="utf-8",
    )
    completed = subprocess.run(
        [SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness),
         "-Script", str(deploy)], capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["planned"] and result["buildRequested"] and not result["buildExecuted"]
    assert index.read_text(encoding="utf-8") == "old"
    assert not (target / "_deploy_backups").exists()


@pytest.mark.parametrize("mode", [[], ["-SkipBuild"], ["-PlanOnly"]])
def test_oversized_source_blocks_deployment_before_any_side_effect(tmp_path, mode):
    target, _ = _fixture(tmp_path, [])
    scripts = target / "scripts"
    scripts.mkdir()
    deploy = scripts / "Deploy-VillageInfiniteCanvas.ps1"
    shutil.copyfile(ROOT / "scripts/Deploy-VillageInfiniteCanvas.ps1", deploy)
    _source_gate_fixture(target)
    for relative in ("src/novelvideo", "runtime/env/novelvideo", "frontend/dist",
                     "agent_skills"):
        (target / relative).mkdir(parents=True, exist_ok=True)
    (target / "src/novelvideo/oversized.py").write_text("# line\n" * 3001, encoding="utf-8")
    index = target / "frontend/dist/index.html"
    index.write_text("old frontend", encoding="utf-8")
    backend = target / "runtime/env/novelvideo/keep.py"
    backend.write_text("old backend", encoding="utf-8")
    harness = tmp_path / "gate.ps1"
    harness.write_text(
        """param($Script, $Mode)
function pnpm { throw 'unexpected build' }
function Start-Process { throw 'unexpected start' }
try {
    if ($Mode -eq 'skip') { & $Script -SkipBuild }
    elseif ($Mode -eq 'plan') { & $Script -PlanOnly }
    else { & $Script }
} catch { Write-Output $_.Exception.Message; exit 1 }
""", encoding="utf-8",
    )
    mode_name = "skip" if "-SkipBuild" in mode else "plan" if mode else "build"
    completed = subprocess.run(
        [SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness),
         "-Script", str(deploy), "-Mode", mode_name],
        capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 1
    assert "Source file size gate failed" in completed.stdout + completed.stderr
    assert "oversized.py" in completed.stdout + completed.stderr
    assert index.read_text(encoding="utf-8") == "old frontend"
    assert backend.read_text(encoding="utf-8") == "old backend"
    assert not (target / "_deploy_backups").exists()
    assert not (target / "runtime/env/novelvideo/oversized.py").exists()


def test_backup_retention_plan_preserves_recent_custom_and_invalid_backups(tmp_path):
    root = tmp_path / "product"
    backups = root / "_deploy_backups"
    for index in range(8):
        directory = backups / f"village-canvas-20261007-00000{index}-001"
        directory.mkdir(parents=True)
        (directory / "rollback-manifest.json").write_text(json.dumps({
            "schema": "village_deployment_rollback.v1", "newFiles": [],
        }), encoding="utf-8")
    custom = backups / "operator-special"
    custom.mkdir()
    invalid = backups / "village-canvas-20261006-000000"
    invalid.mkdir()
    (invalid / "rollback-manifest.json").write_text("invalid", encoding="utf-8")
    harness = tmp_path / "retention.ps1"
    harness.write_text(
        "param($Script, $Root)\n& $Script -Root $Root -PlanOnly | ConvertTo-Json -Depth 5\n",
        encoding="utf-8",
    )
    completed = subprocess.run([
        SHELL, "-NoProfile", "-File", str(harness), "-Script",
        str(ROOT / "scripts/maintenance/recycle_deployment_backups.ps1"), "-Root", str(root),
    ], capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert len(result["retained"]) == 5
    assert set(result["candidates"]) == {
        f"village-canvas-20261007-00000{index}-001" for index in range(3)
    }
    assert not result["recycled"]
    assert len(list(backups.iterdir())) == 10


def test_deployment_recycles_backups_only_after_successful_health_check():
    source = (ROOT / "scripts/Deploy-VillageInfiniteCanvas.ps1").read_text(encoding="utf-8")
    assert source.index("recycle_deployment_backups.ps1") > source.index("failed to become healthy")
    assert "Remove-Item" not in (
        ROOT / "scripts/maintenance/recycle_deployment_backups.ps1"
    ).read_text(encoding="utf-8")
