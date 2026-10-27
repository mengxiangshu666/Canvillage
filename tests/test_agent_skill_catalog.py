"""The model-facing skill catalog must stay curated, real, and tool-safe."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "agent_skills_exposed.json"
MATERIALIZER = ROOT / "scripts" / "materialize_agent_skills.py"
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---", re.S)
_TOOL_REF = re.compile(
    r"\b(?:mcp__tapcanvas__)?((?:village_canvas|freezone|tapcanvas)_[a-z0-9_]+)\b"
)


def _materializer():
    spec = importlib.util.spec_from_file_location("materialize_agent_skills", MATERIALIZER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    if not match:
        return {}
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def _entries():
    manifest = _manifest()
    for source, names in manifest["skills"].items():
        root = ROOT / manifest["sources"][source]
        for name in names:
            yield source, root, name


def test_every_manifest_skill_exists_and_is_model_invocable() -> None:
    names: set[str] = set()
    for source, root, name in _entries():
        bundle = root / name
        skill_file = bundle / "SKILL.md"
        assert skill_file.is_file(), f"{source}/{name} has no SKILL.md"
        meta = _frontmatter(skill_file)
        assert meta.get("name") == name, f"{source}/{name} frontmatter name mismatch"
        assert meta.get("description", "").strip(), f"{source}/{name} has no description"
        assert str(meta.get("disable-model-invocation", "")).lower() not in {
            "true",
            "yes",
            "on",
            "1",
        }, f"{source}/{name} is not model-invocable"
        assert name not in names, f"duplicate skill name: {name}"
        names.add(name)
    assert len(names) <= 40


def test_manifest_skills_do_not_call_unexposed_tools() -> None:
    materializer = _materializer()
    for _source, root, name in _entries():
        text = (root / name / "SKILL.md").read_text(encoding="utf-8")
        refs = set(_TOOL_REF.findall(text))
        unexposed = sorted(ref for ref in refs if ref not in materializer.EXPOSED_TOOLS)
        assert not unexposed, f"{name} references unavailable tools: {unexposed}"


def test_host_and_dev_skills_are_not_exposed() -> None:
    names = {name for _source, _root, name in _entries()}
    forbidden = {
        "skill-router",
        "agent-reach",
        "impeccable",
        "quarkclouddrive",
        "reverse-engineer-anything",
        "0-1-navigate-to-freezone",
        "code-review",
        "mcp-builder",
        "agent-builder",
        "pdf",
        "agents-team",
        "skill-creator",
    }
    assert not (names & forbidden)


def test_materializer_dry_run_matches_manifest() -> None:
    materializer = _materializer()
    entries = materializer.materialize(dry_run=True)
    expected = {name for _source, _root, name in _entries()}
    assert {entry["name"] for entry in entries} == expected


@pytest.mark.skipif(
    not (ROOT / "runtime" / "agent_sidecar" / "skills").is_dir(),
    reason="sidecar runtime is not materialized on this machine",
)
def test_materialized_runtime_catalog_matches_manifest() -> None:
    expected = {name for _source, _root, name in _entries()}
    actual = {
        path.name
        for path in (ROOT / "runtime" / "agent_sidecar" / "skills").iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    }
    assert actual == expected
