"""Materialize the curated agent skill catalog into the sidecar runtime.

The sidecar is pointed at ``runtime/agent_sidecar/skills`` through
``AGENTS_SKILLS_DIR``.  Only skills listed in ``config/agent_skills_exposed.json``
are copied, so the model never sees personal/host skills or skills that would
call tools this product does not expose.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "agent_skills_exposed.json"
TARGET = ROOT / "runtime" / "agent_sidecar" / "skills"
CATALOG = ROOT / "runtime" / "agent_sidecar" / "agent-skills-catalog.json"

MAX_SKILLS = 40
MAX_DESCRIPTION_CHARS = 800
EXPOSED_TOOLS = frozenset(
    {
        "village_canvas_read_compact",
        "village_canvas_dispatch_action",
        "village_canvas_apply_commands",
        "freezone_run_node",
        "freezone_stop_task",
        "village_canvas_start_workflow_run",
        "village_canvas_command_workflow_run",
        "village_canvas_get_workflow_run",
        "village_canvas_list_tasks",
        "village_canvas_get_task",
    }
)
_TOOL_REF = re.compile(
    r"\b(?:mcp__tapcanvas__)?((?:village_canvas|freezone|tapcanvas)_[a-z0-9_]+)\b"
)
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def _parse_frontmatter(text: str) -> dict[str, str]:
    match = _FRONTMATTER.match(text)
    if not match:
        return {}
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def _load_manifest() -> dict:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if payload.get("version") != 1:
        raise SystemExit("unsupported agent skill manifest version")
    return payload


def _source_root(manifest: dict, source: str) -> Path:
    root = ROOT / str(manifest["sources"][source])
    if not root.is_dir():
        raise SystemExit(f"skill source missing: {root}")
    return root


def _collect(manifest: dict) -> list[dict]:
    entries: list[dict] = []
    names: set[str] = set()
    for source, skills in manifest["skills"].items():
        root = _source_root(manifest, source)
        for name in skills:
            bundle = root / name
            skill_file = bundle / "SKILL.md"
            if not skill_file.is_file():
                raise SystemExit(f"skill missing: {bundle}")
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
                raise SystemExit(f"invalid skill name: {name}")
            if name in names:
                raise SystemExit(f"duplicate skill name: {name}")
            names.add(name)
            text = skill_file.read_text(encoding="utf-8")
            meta = _parse_frontmatter(text)
            description = meta.get("description", "").strip()
            if not description:
                raise SystemExit(f"skill has no description: {name}")
            if str(meta.get("disable-model-invocation", "")).strip().lower() in {
                "true",
                "yes",
                "on",
                "1",
            }:
                raise SystemExit(
                    f"skill {name} is not model-invocable; remove it from the manifest"
                )
            if len(description) > MAX_DESCRIPTION_CHARS:
                raise SystemExit(
                    f"skill description too long ({len(description)} chars): {name}"
                )
            unexposed = sorted(
                ref for ref in set(_TOOL_REF.findall(text)) if ref not in EXPOSED_TOOLS
            )
            if unexposed:
                raise SystemExit(
                    f"skill {name} references tools this product does not expose: "
                    + ", ".join(unexposed[:6])
                )
            entries.append(
                {
                    "name": name,
                    "source": source,
                    "path": str(bundle),
                    "description": description,
                }
            )
    if len(entries) > MAX_SKILLS:
        raise SystemExit(
            f"catalog has {len(entries)} skills; keep it at or below {MAX_SKILLS}"
        )
    return sorted(entries, key=lambda item: item["name"])


def materialize(*, dry_run: bool = False) -> list[dict]:
    entries = _collect(_load_manifest())
    if dry_run:
        return entries
    # The running sidecar watches this root; delete its children instead of
    # replacing the root directory, which Windows keeps locked.
    TARGET.mkdir(parents=True, exist_ok=True)
    for child in TARGET.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    for entry in entries:
        shutil.copytree(Path(entry["path"]), TARGET / entry["name"])
    CATALOG.write_text(
        json.dumps(
            {
                "version": 1,
                "count": len(entries),
                "skills": [
                    {
                        "name": entry["name"],
                        "source": entry["source"],
                        "description": entry["description"],
                    }
                    for entry in entries
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    entries = materialize(dry_run=args.dry_run or args.check)
    if args.check:
        print(
            {
                "manifest": MANIFEST.exists(),
                "materialized": TARGET.is_dir(),
                "skills": len(entries),
                "target": str(TARGET),
            }
        )
        return 0 if TARGET.is_dir() else 1
    print(f"materialized {len(entries)} skills into {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
