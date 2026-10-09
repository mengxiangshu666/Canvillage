from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "ci" / "validate_quality_targets.py"
SPEC = importlib.util.spec_from_file_location("validate_quality_targets", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
quality_targets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quality_targets)


def _manifest_path() -> Path:
    return REPO_ROOT / "scripts" / "ci" / "quality_targets.json"


def _write_manifest(root: Path, groups: dict[str, list[str]]) -> Path:
    path = root / "quality_targets.json"
    path.write_text(
        json.dumps(
            {
                "schema": quality_targets.MANIFEST_SCHEMA,
                "groups": groups,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_checked_in_quality_targets_resolve_to_real_tests():
    manifest = _manifest_path().read_text(encoding="utf-8")
    declared = json.loads(manifest)["groups"]
    summary = quality_targets.validate_manifest(root=REPO_ROOT)

    # Every declared target must resolve; comparing against the manifest itself
    # keeps this honest without a magic number that has to be bumped by hand.
    for group, targets in declared.items():
        assert summary["groups"][group]["targets"] == len(targets)
    assert summary["groups"]["backend_core"]["targets"] >= 15
    assert summary["groups"]["backend_core"]["test_entries"] > 0
    assert summary["groups"]["frontend_core"]["targets"] >= 9
    assert summary["groups"]["frontend_core"]["test_entries"] > 0


def test_frontend_targets_can_be_listed_relative_to_frontend():
    targets = quality_targets.list_targets(
        "frontend_core",
        root=REPO_ROOT,
        relative_to="frontend",
    )

    assert targets
    assert all(target.startswith("src/") for target in targets)
    assert "src/features/village-workflow" not in targets


def test_manifest_rejects_existing_directories_without_tests(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_real.py").write_text(
        "def test_real():\n    assert True\n",
        encoding="utf-8",
    )
    (tmp_path / "frontend" / "src" / "empty").mkdir(parents=True)
    manifest = _write_manifest(
        tmp_path,
        {
            "backend_core": ["tests/test_real.py"],
            "frontend_core": ["frontend/src/empty"],
        },
    )

    with pytest.raises(
        quality_targets.QualityTargetError,
        match="resolves to no test files",
    ):
        quality_targets.validate_manifest(manifest, root=tmp_path)


def test_manifest_rejects_generated_or_backup_boundaries(tmp_path: Path):
    manifest = _write_manifest(
        tmp_path,
        {
            "backend_core": ["tests/example.py.bak"],
            "frontend_core": ["frontend/dist/example.test.ts"],
        },
    )

    with pytest.raises(
        quality_targets.QualityTargetError,
        match="banned boundary",
    ):
        quality_targets.validate_manifest(manifest, root=tmp_path)
