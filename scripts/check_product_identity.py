#!/usr/bin/env python3
"""Reject retired product identity from active Village Infinite Canvas files."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
TEXT_SUFFIXES = {
    "",
    ".bat",
    ".conf",
    ".css",
    ".csv",
    ".env",
    ".example",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".ps1",
    ".rst",
    ".service",
    ".sh",
    ".template",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vbs",
    ".yaml",
    ".yml",
}
DISTRIBUTION_PATTERN = re.compile(r"supertale", re.IGNORECASE)
FORBIDDEN = (
    re.compile(r"drama[ _-]?claw", re.IGNORECASE),
    re.compile(r"relayclaw", re.IGNORECASE),
    re.compile(r"虾(?:导|画|塘|编|料|集)"),
    DISTRIBUTION_PATTERN,
    # Generation-1 identity: the DirectorWorld 3D editor the scene engine was
    # ported from. Nothing in the shipped product may name it.
    re.compile(r"buildergpt", re.IGNORECASE),
    # Generation-3 identity, romanised. The alternation is the romanisation of
    # 虾导/虾画/虾集/虾塘/虾料/虾编; `xiaoshu` (小树), the Azure `zh-CN-XiaoxiaoNeural`
    # voices and the `xianxia` style word all fall outside it, so a bare
    # `xia`-prefix match is deliberately not used.
    re.compile(r"xia(?:dao|hua|ji|jing|liao|tang|ge)", re.IGNORECASE),
    re.compile(r"xia[ _-]+(?:director|style)", re.IGNORECASE),
)
DEPLOYED_DISTRIBUTION_METADATA_GLOB = "runtime/env/supertale_ce-*.dist-info/METADATA"
# Line-level escape hatch, mirroring the `banned-word-allow` convention of the
# ee-terms and banned-words linters. Preferred over a file-level compatibility
# entry when a single line must name the retired identity: the rest of the file
# stays under the gate.
ALLOW_MARKER = "identity-allow"
PORTABLE_RUNTIME_PREFIXES = (
    "agent_skills/",
    "frontend/src/",
    "scripts/",
    "src/",
)
PORTABLE_RUNTIME_FILES = {
    "village_canvas_launch.py",
    "village_canvas_run_api.py",
    "启动村长无限画布.bat",
    "启动村长无限画布.vbs",
    "村长无限画布-Start.bat",
}
HOST_PATH_PATTERNS = (
    re.compile(r"(?i)(?:r|f|u|b)?[\"'][A-Z]:[\\/]"),
    re.compile(r"(?i)(?:r|f|u|b)?[\"']\/(?:Users|home)\/[^/\s]+\/"),
    re.compile(r"(?i)LobsterAI|dramaclaw-mod-dev|DramaClaw-魔改版"),
)

# Legacy identifiers are confined to the explicit read-old/write-new migration
# boundary. The active runtime and product assets use Village Canvas names only.
COMPATIBILITY_PREFIXES = (
    # Research evidence may name upstream projects; it is not imported by the
    # shipped runtime and must remain auditable instead of being rewritten.
    "docs/research/",
)
COMPATIBILITY_FILES = {
    "src/novelvideo/chat/identity_compat.py",
    "scripts/migrate_identity_data.py",
    "tests/test_identity_compat.py",
    "tests/test_identity_data_migration.py",
    # Legacy fixtures remain in focused regression tests only.
    "tests/test_chat_replay_strip.py",
    "tests/test_chat_service_user_agent_scope.py",
}

# `supertale-ce` is the name this distribution is published under, and the
# Elastic License 2.0 forbids stripping the licensor's notices: the upstream
# project has to stay credited in the distributed copy. Renaming the package or
# rewriting the notices is a licence question, not a branding one, so these
# artifacts keep the name. JSON / CSV / lock files / plain-text notices have no
# comment syntax to hang a line marker on, which is why these are file-scoped.
DISTRIBUTION_ATTRIBUTION_FILES = {
    "NOTICE",
    "REUSE.toml",
    "frontend/NOTICE",
    "frontend/THIRD-PARTY-LICENSES.txt",
    "uv.lock",
    "sbom.spdx.json",
    "license-inventory.csv",
    "scripts/compliance/generate_p0b_artifacts.py",
    "tests/compliance/test_generate_p0b_artifacts.py",
    "tests/test_p0b_compliance_generator.py",
    "tests/test_release_feed.py",
}

# These lint the *external* EE repository and name it as their target
# (`supertale_admin`, the `SuperTale2` checkout) — banned-word-allow: erasing
# the name would erase the thing under audit, so it stays.
UPSTREAM_AUDIT_FILES = {
    ".github/workflows/ce-import-lint.yml",
    "scripts/check_ce_port_closure.py",
    "scripts/check_env_config.py",
    "scripts/lint_ce_imports.py",
    "scripts/lint_ee_terms.py",
    "tests/test_ce_imports_lint.py",
    "tests/test_ce_port_closure.py",
    "tests/test_env_config_ratchet.py",
}

# The change ledgers record the rename work itself, including the paths it
# rewrote, so they name the retired identity as history.
IDENTITY_LEDGER_FILES = {
    "MODIFICATIONS.md",
    "STATUS.md",
    "docs/status/STATUS-ARCHIVE-2026-08-and-earlier.md",
}


# The deployment surface is a gitignored build output: `git ls-files` cannot see
# it, yet `runtime/env/novelvideo` and `runtime/agent_skills` are byte-for-byte
# copies of `src/novelvideo` and `agent_skills` produced by
# `scripts/Deploy-VillageInfiniteCanvas.ps1`. A stale copy left behind by an
# interrupted deploy would otherwise ship the retired identity unseen, so the
# owned subtrees are read straight off disk.
#
# `runtime/env` also holds ~1.9 GB of vendored site-packages whose own upstream
# provenance (CI runner paths, upstream project names) is not ours to rewrite.
# Those trees are therefore out of scope, and `DEPLOYED_VENDOR_MARKERS` is the
# rail that fails the run if the walk ever wanders into one.
DEPLOYMENT_TREES = (
    "runtime/env/novelvideo",
    "runtime/agent_skills",
)
DEPLOYED_VENDOR_MARKERS = (
    "runtime/env/site-packages/",
    "runtime/env/torch",
    "runtime/env/torchvision",
    "runtime/env/python",
    "runtime/env/node",
    "runtime/python/",
    "runtime/node/",
    "runtime/ffmpeg/",
    "runtime/deploy/",
)
DEPLOYMENT_SCAN_FILE_LIMIT = 20000


def is_compatibility_path(relative: str) -> bool:
    return (
        relative in COMPATIBILITY_FILES
        or relative in DISTRIBUTION_ATTRIBUTION_FILES
        or relative in UPSTREAM_AUDIT_FILES
        or relative in IDENTITY_LEDGER_FILES
        or any(relative.startswith(prefix) for prefix in COMPATIBILITY_PREFIXES)
    )


def is_portable_runtime_path(relative: str) -> bool:
    if (
        "/__tests__/" in relative
        or ".test." in relative
        or relative.startswith("scripts/ci/")
    ):
        return False
    return relative in PORTABLE_RUNTIME_FILES or any(
        relative.startswith(prefix) for prefix in PORTABLE_RUNTIME_PREFIXES
    )


def repository_files() -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return [ROOT / item.decode("utf-8") for item in output.split(b"\0") if item]


def staged_repository_files() -> list[Path]:
    output = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMRTUXB", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return [ROOT / item.decode("utf-8") for item in output.split(b"\0") if item]


def _deployment_candidate(path: Path) -> bool:
    relative = path.relative_to(ROOT).as_posix()
    if any(relative.startswith(marker) for marker in DEPLOYED_VENDOR_MARKERS):
        return False
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return False
    return not any(part == "__pycache__" for part in path.parts)


def mirrored_source_path(relative: str) -> str:
    """Map a deployed copy back to the repository path it was copied from."""

    for tree, source in (
        ("runtime/env/novelvideo/", "src/novelvideo/"),
        ("runtime/env/", "src/"),
        ("runtime/agent_skills/", "agent_skills/"),
        ("runtime/", ""),
    ):
        if relative.startswith(tree):
            return source + relative[len(tree) :]
    return relative


def deployment_files() -> list[Path]:
    """Text files under the gitignored deployment trees, verbatim scans only."""

    found: list[Path] = []
    for tree in DEPLOYMENT_TREES:
        root = ROOT / tree
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(ROOT).as_posix()
            if any(relative.startswith(marker) for marker in DEPLOYED_VENDOR_MARKERS):
                raise RuntimeError(
                    f"deployment walk cannot be pointed at a vendored tree: {relative}"
                )
            if _deployment_candidate(path):
                found.append(path)
    return found


def check_deployment_surface() -> list[str]:
    """Gate the deploy output, including the mirror-compat allowance."""

    files = deployment_files()
    if len(files) > DEPLOYMENT_SCAN_FILE_LIMIT:
        raise RuntimeError(
            f"deployment surface grew to {len(files)} files, above the "
            f"{DEPLOYMENT_SCAN_FILE_LIMIT} rail"
        )
    offenders: list[str] = []
    for path in sorted(files):
        relative = path.relative_to(ROOT).as_posix()
        # A deployed mirror inherits the compatibility scope of the file it
        # mirrors, so `runtime/env/novelvideo/chat/identity_compat.py` stays
        # exempt for the same reason `src/novelvideo/chat/identity_compat.py`
        # does.
        if is_compatibility_path(mirrored_source_path(relative)):
            continue
        if any(pattern.search(relative) for pattern in FORBIDDEN):
            offenders.append(f"path: {relative}")
        text = path.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), 1):
            if ALLOW_MARKER in line:
                continue
            if any(pattern.search(line) for pattern in FORBIDDEN):
                offenders.append(f"{relative}:{number}: {line.strip()[:160]}")
    return offenders


def check_distribution_metadata_surface() -> list[str]:
    """Reject retired brands in installed distribution metadata.

    The package name itself is a required Elastic License 2.0 attribution and
    remains allowed here. Everything else that this gate retires is scanned.
    """

    offenders: list[str] = []
    for path in sorted(ROOT.glob(DEPLOYED_DISTRIBUTION_METADATA_GLOB)):
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), 1):
            if ALLOW_MARKER in line:
                continue
            if any(
                pattern is not DISTRIBUTION_PATTERN and pattern.search(line)
                for pattern in FORBIDDEN
            ):
                offenders.append(f"{relative}:{number}: {line.strip()[:160]}")
    return offenders


def main() -> int:
    offenders: list[str] = []
    paths = {path.relative_to(ROOT).as_posix(): path for path in repository_files()}
    for path in staged_repository_files():
        paths[path.relative_to(ROOT).as_posix()] = path

    for relative, path in sorted(paths.items()):
        if path.resolve() == SELF:
            continue
        # The inventory is an audit artifact and intentionally records the
        # compatibility paths that this gate must allow.
        if relative == "license-inventory.csv":
            continue
        if any(
            part in {".git", "node_modules", "dist", "项目资产"} for part in path.parts
        ):
            continue
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if is_portable_runtime_path(relative):
                for number, line in enumerate(text.splitlines(), 1):
                    if any(pattern.search(line) for pattern in HOST_PATH_PATTERNS):
                        offenders.append(
                            f"non-portable path {relative}:{number}: {line.strip()[:160]}"
                        )
        else:
            text = ""
        if is_compatibility_path(relative):
            continue
        if any(pattern.search(relative) for pattern in FORBIDDEN):
            offenders.append(f"path: {relative}")
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if ALLOW_MARKER in line:
                continue
            if any(pattern.search(line) for pattern in FORBIDDEN):
                offenders.append(f"{relative}:{number}: {line.strip()[:160]}")

    offenders.extend(check_deployment_surface())
    offenders.extend(check_distribution_metadata_surface())

    if offenders:
        print("Retired product identity found:", file=sys.stderr)
        print("\n".join(offenders), file=sys.stderr)
        return 1
    print("Product identity check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
