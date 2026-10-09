"""Execute explicit local rollback and verify complete frontend byte parity."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def hashes(directory):
    return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


def snapshot():
    return json.loads(subprocess.check_output(
        [sys.executable, str(ROOT / "scripts/ai_project_snapshot.py")], cwd=ROOT,
        text=True, encoding="utf-8",
    ))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("backup", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    backup = args.backup.resolve()
    backup.relative_to((ROOT / "_deploy_backups").resolve())
    saved = backup / "frontend"
    if not saved.joinpath("index.html").is_file():
        raise SystemExit("requires a complete structured frontend backup")
    manifest = json.loads((backup / "rollback-manifest.json").read_text(encoding="utf-8-sig"))
    if manifest.get("schema") != "village_deployment_rollback.v1":
        raise SystemExit("unsupported backup manifest")
    expected = hashes(saved)
    before = snapshot()
    expected_version = json.loads((saved / "version.json").read_text(encoding="utf-8"))
    if not args.execute:
        raise SystemExit("--execute is required to stop and restore 8784")
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        raise SystemExit("PowerShell unavailable")
    result = {"schema": "production_frontend_rollback.v1", "backup": str(backup),
              "before": before, "expected_version": expected_version}
    try:
        command = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                   str(ROOT / "scripts/Rollback-VillageInfiniteCanvas.ps1"),
                   "-BackupRoot", str(backup)]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=150)
        result["rollback_exit_code"] = completed.returncode
        result["rollback_stdout"] = completed.stdout
        result["rollback_stderr"] = completed.stderr
        after = snapshot()
        actual = hashes(ROOT / "frontend/dist")
        result.update({
            "after": after, "expected_files": len(expected), "actual_files": len(actual),
            "frontend_byte_parity": actual == expected,
            "runtime_version_restored": after["runtime"]["version"] == expected_version,
            "healthy": after["runtime"]["health"].get("status") == "ok",
            "process_replaced": before["runtime"]["pid_file"] != after["runtime"]["pid_file"],
            "new_files_removed": all(not (ROOT / "frontend/dist" / entry["relative"]).exists()
                                     for entry in manifest["newFiles"] if entry["area"] == "frontend"),
            "mismatched_paths": sorted(path for path in expected.keys() | actual.keys()
                                       if expected.get(path) != actual.get(path)),
            "limitations": [
                "Restores the preceding frontend build of the same behavior, not an older feature release.",
                "No private database or media inspection; health/version/file parity are the live checks.",
                "Does not prove recovery of an active paid provider task.",
            ],
        })
        result["ok"] = completed.returncode == 0 and all(result[key] for key in (
            "frontend_byte_parity", "runtime_version_restored", "healthy",
            "process_replaced", "new_files_removed",
        ))
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in ("before", "after")},
                     ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
