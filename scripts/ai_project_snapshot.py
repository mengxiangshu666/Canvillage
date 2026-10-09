"""Emit a small, read-only project/runtime snapshot for future AI tasks."""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_BASE = "http://127.0.0.1:8784"


def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def read_json_file(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def get_json(path: str) -> object | None:
    try:
        with urllib.request.urlopen(f"{RUNTIME_BASE}{path}", timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None


def payload_build_id(payload: object | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("buildId")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def build_id_is_dirty(build_id: str | None) -> bool | None:
    if not build_id:
        return None
    return any(part == "dirty" for part in build_id.split("-"))


def build_id_commit(build_id: str | None) -> str | None:
    """Extract the abbreviated Git commit from the Vite build-id format.

    ``composeBuildId`` makes ``<timestamp>-<git describe>-<base36 suffix>``.  A
    tagged build therefore contains ``-g<sha>``; an untagged build contains a
    bare abbreviated SHA.  The base36 uniqueness suffix can also contain hex
    characters, so prefer the explicit ``g`` segment and reject all-digit
    lookalikes from the timestamp.
    """

    if not build_id:
        return None
    parts = build_id.split("-")[1:]
    explicit = [part[1:] for part in parts if part.startswith("g")]
    for candidate in explicit:
        if re.fullmatch(r"[0-9a-f]{7,40}", candidate):
            return candidate
    for candidate in parts:
        if candidate == "dirty" or candidate.isdigit():
            continue
        if re.fullmatch(r"[0-9a-f]{7,40}", candidate):
            return candidate
    return None


def build_provenance(
    *,
    head: str,
    worktree_clean: bool,
    dist_version: object | None,
    runtime_version: object | None,
) -> dict[str, object]:
    dist_build_id = payload_build_id(dist_version)
    runtime_build_id = payload_build_id(runtime_version)
    dist_commit = build_id_commit(dist_build_id)
    runtime_commit = build_id_commit(runtime_build_id)
    dist_dirty = build_id_is_dirty(dist_build_id)
    runtime_dirty = build_id_is_dirty(runtime_build_id)
    dist_matches_head = (
        bool(head and dist_commit and head.startswith(dist_commit))
        if dist_commit
        else None
    )
    runtime_matches_head = (
        bool(head and runtime_commit and head.startswith(runtime_commit))
        if runtime_commit
        else None
    )
    runtime_matches_dist = (
        bool(dist_build_id and runtime_build_id and dist_build_id == runtime_build_id)
        if dist_build_id and runtime_build_id
        else None
    )
    reproducible_source = bool(
        worktree_clean and dist_dirty is False and dist_matches_head is True
    )
    reproducible_runtime = bool(
        reproducible_source and runtime_matches_dist is True and runtime_dirty is False
    )

    warnings: list[str] = []
    if not worktree_clean:
        warnings.append("worktree is dirty; committed HEAD does not identify this source")
    if dist_dirty:
        warnings.append("frontend/dist was built from a dirty source tree")
    if runtime_dirty:
        warnings.append("the running service was built from a dirty source tree")
    if dist_matches_head is False:
        warnings.append("frontend/dist commit does not match Git HEAD")
    if runtime_matches_head is False:
        warnings.append("running service commit does not match Git HEAD")
    if runtime_matches_dist is False:
        warnings.append("running service does not match frontend/dist")

    return {
        "worktree_clean": worktree_clean,
        "dist_dirty": dist_dirty,
        "runtime_dirty": runtime_dirty,
        "runtime_matches_dist": runtime_matches_dist,
        "dist_matches_head": dist_matches_head,
        "runtime_matches_head": runtime_matches_head,
        "reproducible_source": reproducible_source,
        "reproducible_runtime": reproducible_runtime,
        "dist_build_id": dist_build_id,
        "runtime_build_id": runtime_build_id,
        "dist_commit": dist_commit,
        "runtime_commit": runtime_commit,
        "warnings": warnings,
    }


def main() -> int:
    status_lines = [line for line in run_git("status", "--short").splitlines() if line]
    head = run_git("rev-parse", "HEAD")
    dist_version = read_json_file(ROOT / "frontend" / "dist" / "version.json")
    runtime_version = get_json("/version.json")
    pid_path = ROOT / "项目资产" / "logs" / "api.pid"
    pid = None
    try:
        pid = pid_path.read_text(encoding="ascii").strip() or None
    except OSError:
        pass

    openapi = get_json("/openapi.json")
    snapshot = {
        "root": str(ROOT),
        "git": {
            "head": head,
            "branch": run_git("branch", "--show-current"),
            "dirty_entries": len(status_lines),
            "status_sample": status_lines[:20],
        },
        "provenance": build_provenance(
            head=head,
            worktree_clean=not status_lines,
            dist_version=dist_version,
            runtime_version=runtime_version,
        ),
        "dist": {"version": dist_version},
        "runtime": {
            "base_url": RUNTIME_BASE,
            "pid_file": pid,
            "health": get_json("/healthz"),
            "version": runtime_version,
            "openapi_path_count": len(openapi.get("paths", {})) if isinstance(openapi, dict) else None,
        },
        "boundaries": {
            "data_root": str(ROOT / "项目资产"),
            "state_root": str(ROOT / "项目资产" / "state"),
            "frontend_dist": str(ROOT / "frontend" / "dist"),
            "portable_runtime": str(ROOT / "runtime"),
        },
    }
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
