from __future__ import annotations

import json
from pathlib import Path

from scripts.architecture.architecture_manifest import load_manifest, resolve_owner
from scripts.architecture.validate_architecture_manifest import (
    _file_path_exists,
    _repository_visible_files,
    _tree_path_exists,
    validate_manifest,
)


ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_repository_manifest_is_current() -> None:
    report = validate_manifest(repo_root=ROOT)
    assert report["finding_count"] == 0
    assert report["owner_count"] == 19
    assert report["backend_domain_count"] == 8
    assert report["frontend_feature_count"] == 8
    assert report["critical_contract_count"] >= 10


def test_manifest_path_checks_ignore_local_ignored_directories() -> None:
    visible_files = _repository_visible_files(ROOT)

    assert visible_files is not None
    assert _tree_path_exists(ROOT, "frontend/src/features/canvas", visible_files) is True
    assert _tree_path_exists(ROOT, "frontend/src/.mimosa", visible_files) is False
    assert _file_path_exists(ROOT, "frontend/src/.mimosa", visible_files) is False


def test_owner_resolution_uses_the_most_specific_path() -> None:
    manifest = load_manifest()
    assert resolve_owner(manifest, "frontend/src/stores/canvasStore.ts")["id"] == (
        "frontend.canvas"
    )
    assert resolve_owner(manifest, "frontend/src/stores/settingsStore.ts")["id"] == (
        "frontend.shared"
    )
    assert (
        resolve_owner(
            manifest,
            "frontend/src/features/workflow-runs/workflow-run-overview.tsx",
        )["id"]
        == "frontend.workflow_runs"
    )
    assert resolve_owner(manifest, "src/novelvideo/api/routes/freezone.py")["id"] == (
        "backend.api"
    )
    assert (
        resolve_owner(
            manifest,
            "scripts/architecture/validate_architecture_manifest.py",
        )["id"]
        == "governance.engineering"
    )


def test_validator_rejects_an_undeclared_frontend_feature(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "scripts/architecture/domain_boundaries.json",
        {"domains": {}},
    )
    _write_json(
        tmp_path / "scripts/architecture/feature_boundaries.json",
        {"baseline": {}},
    )
    _write_json(
        tmp_path / "scripts/architecture/file_size_baseline.json",
        {"threshold": 3000, "baseline": {}},
    )
    _write_json(
        tmp_path / "scripts/architecture/gate.json",
        {},
    )
    (tmp_path / "frontend/src/shared").mkdir(parents=True)
    (tmp_path / "frontend/src/features/undeclared").mkdir(parents=True)
    manifest_path = tmp_path / "scripts/architecture/architecture_manifest.json"
    _write_json(
        manifest_path,
        {
            "schema_version": 1,
            "source_configs": {
                "backend_domains": "scripts/architecture/domain_boundaries.json",
                "frontend_features": "scripts/architecture/feature_boundaries.json",
                "file_sizes": "scripts/architecture/file_size_baseline.json",
            },
            "owners": [
                {
                    "id": "frontend.shared",
                    "platform": "frontend",
                    "paths": ["frontend/src/shared"],
                    "responsibility": "Test fixture shared frontend owner.",
                    "gates": ["dummy"],
                }
            ],
            "critical_contracts": [],
            "invariants": [],
            "gates": {
                "dummy": {
                    "script": "scripts/architecture/gate.json",
                    "args": [],
                }
            },
        },
    )

    report = validate_manifest(repo_root=tmp_path, manifest_path=manifest_path)
    kinds = {finding["kind"] for finding in report["findings"]}
    assert "unowned-frontend-feature" in kinds
