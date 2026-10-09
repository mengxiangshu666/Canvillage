"""Dry-run a deployment backup restore without touching the running service."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shutil
import tempfile
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("backup", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.backup.is_dir():
        raise SystemExit(f"backup not found: {args.backup}")
    restored = 0
    records: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="village-rollback-") as temp:
        root = Path(temp)
        for source in args.backup.rglob("*"):
            if not source.is_file():
                continue
            relative = source.relative_to(args.backup)
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            source_hash = _sha256(source)
            restored_hash = _sha256(destination)
            if source_hash != restored_hash:
                raise SystemExit(f"rollback hash mismatch: {relative}")
            records.append(
                {
                    "path": relative.as_posix(),
                    "sha256": restored_hash,
                }
            )
            restored += 1
        overlay = root / "novelvideo"
        for source in root.joinpath("backend").rglob("*.py"):
            relative = source.relative_to(root / "backend")
            destination = overlay / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        import sys

        sys.path.insert(0, str(root))
        imported_modules = []
        for module_name in (
            "novelvideo.services.video_request_contract",
            "novelvideo.task_backend.runners.video_request_keys",
            "novelvideo.task_backend.runners.video",
        ):
            importlib.import_module(module_name)
            imported_modules.append(module_name)
        from novelvideo.api.app import create_app
        from fastapi.testclient import TestClient

        os.environ.setdefault("ST_EDITION", "ce")
        with TestClient(create_app()) as client:
            health = client.get("/healthz")
            if health.status_code != 200 or health.json().get("status") != "ok":
                raise SystemExit(f"rollback health smoke failed: {health.status_code}")
    result = {
        "measurement": "deployment_backup_restore_dry_run",
        "backup": str(args.backup),
        "restored_files": restored,
        "hash_verified": True,
        "import_smoke": imported_modules,
        "health_smoke": {"status_code": 200, "status": "ok"},
        "service_touched": False,
        "records": records,
        "limitations": [
            "The live runtime was not stopped or replaced.",
            "This verifies backup completeness and byte integrity, not application behavior after rollback.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
