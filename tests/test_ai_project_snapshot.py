from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ai_project_snapshot import build_id_commit, build_provenance  # noqa: E402


HEAD = "f734afb4c15737c7ba95c512983c9aa1b3312719"
DIRTY_BUILD = "20260916T172912594Z-v1.1.42-13-gf734afb-dirty-mu4dkuj6"
CLEAN_BUILD = "20260916T172912594Z-v1.1.42-13-gf734afb-mu4dkuj6"


def test_build_id_commit_reads_tagged_and_untagged_describe_output() -> None:
    assert build_id_commit(DIRTY_BUILD) == "f734afb"
    assert build_id_commit("20260916T172912594Z-f734afb-abc123") == "f734afb"
    assert build_id_commit("20260916T172912594Z-nogit-abc123") is None


def test_clean_matching_dist_and_runtime_are_reproducible() -> None:
    provenance = build_provenance(
        head=HEAD,
        worktree_clean=True,
        dist_version={"version": "v1.1.42", "buildId": CLEAN_BUILD},
        runtime_version={"version": "v1.1.42", "buildId": CLEAN_BUILD},
    )

    assert provenance["worktree_clean"] is True
    assert provenance["dist_dirty"] is False
    assert provenance["runtime_dirty"] is False
    assert provenance["runtime_matches_dist"] is True
    assert provenance["dist_matches_head"] is True
    assert provenance["runtime_matches_head"] is True
    assert provenance["reproducible_source"] is True
    assert provenance["reproducible_runtime"] is True
    assert provenance["warnings"] == []


def test_dirty_or_mismatched_runtime_is_not_reproducible() -> None:
    provenance = build_provenance(
        head=HEAD,
        worktree_clean=False,
        dist_version={"version": "v1.1.42", "buildId": DIRTY_BUILD},
        runtime_version={
            "version": "v1.1.42",
            "buildId": "20260916T172912594Z-v1.1.42-12-g1234567-abc123",
        },
    )

    assert provenance["dist_dirty"] is True
    assert provenance["runtime_dirty"] is False
    assert provenance["runtime_matches_dist"] is False
    assert provenance["runtime_matches_head"] is False
    assert provenance["reproducible_source"] is False
    assert provenance["reproducible_runtime"] is False
    assert len(provenance["warnings"]) == 4
