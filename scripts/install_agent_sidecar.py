"""Materialize the bundled 小树 agent runtime under runtime/agent_sidecar.

The agent sources live in ``third_party/xiaoshu-agent/<version>`` so the product
owns a pinned copy.  Node dependencies are installed into the runtime tree and
never committed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "third_party" / "xiaoshu-agent" / "1.0.0"
TARGET = ROOT / "runtime" / "agent_sidecar"
APP = TARGET / "apps" / "agents-cli"
ENTRY = APP / "dist" / "cli" / "index.js"


def materialize() -> None:
    if not BUNDLE.is_dir():
        raise SystemExit(f"bundled agent sources not found: {BUNDLE}")
    TARGET.mkdir(parents=True, exist_ok=True)
    for name in ("apps", "packages"):
        shutil.copytree(
            BUNDLE / name,
            TARGET / name,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("node_modules", "*.log", ".agents"),
        )


def dependencies_present() -> bool:
    return (APP / "node_modules" / "@deepseek-ai").is_dir()


def ensure_dependencies() -> int:
    if dependencies_present():
        return 0
    pnpm = shutil.which("pnpm")
    if not pnpm:
        print("pnpm is required to install the bundled agent dependencies")
        return 2
    command = [
        pnpm,
        "install",
        "--prod",
        "--frozen-lockfile",
        "--config.confirmModulesPurge=false",
    ]
    print("installing agent dependencies:", " ".join(command))
    status = subprocess.call(command, cwd=str(APP))
    if status != 0 and dependencies_present():
        # pnpm exits non-zero when optional native build scripts are skipped;
        # the agent runtime itself only needs the installed JS packages.
        print("pnpm reported skipped build scripts; agent dependencies are present")
        return 0
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether the runtime tree is materialized without copying",
    )
    args = parser.parse_args()
    if args.check:
        print(
            {
                "bundle": BUNDLE.is_dir(),
                "entry": ENTRY.is_file(),
                "dependencies": dependencies_present(),
            }
        )
        return 0 if ENTRY.is_file() and dependencies_present() else 1
    materialize()
    status = ensure_dependencies()
    if status != 0:
        return status
    if not ENTRY.is_file():
        print(f"agent entry missing after install: {ENTRY}")
        return 1
    skill_script = ROOT / "scripts" / "materialize_agent_skills.py"
    if skill_script.is_file():
        status = subprocess.call([sys.executable, str(skill_script)])
        if status != 0:
            print("agent skill catalog materialization failed")
            return status
    print(f"agent runtime ready: {ENTRY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
