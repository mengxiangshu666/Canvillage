"""Dream B-class corpus must stay out of product sources and skills.

The CSV classifies 293 third-party/uncleared references as ``reference_only``.
They are not needed by the shipped product: the distilled rules and 60 failure
cases are already independent artifacts.  This test is a guard against a later
session copying one of those paths (or its text file) into the product.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "dream_reference_exclusions.json"
SCAN_ROOTS = (
    ROOT / "src" / "novelvideo",
    ROOT / "frontend" / "src",
    ROOT / "agent_skills",
    ROOT / "scripts",
)
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".md", ".json", ".toml", ".yaml", ".yml", ".txt"}
SKIP = {
    MANIFEST,
    ROOT / "config" / "dream_reference_exclusions.json",
}


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_covers_every_b_class_reference() -> None:
    payload = _manifest()
    assert payload["count"] == 293
    assert len(payload["paths"]) == 293
    assert len(set(payload["paths"])) == 293
    assert all(path.startswith("references/") for path in payload["paths"])


def test_product_sources_do_not_reference_b_class_paths() -> None:
    paths = _manifest()["paths"]
    hits: list[str] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for file in root.rglob("*"):
            if not file.is_file() or file.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if file in SKIP:
                continue
            try:
                text = file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for path in paths:
                if path in text:
                    hits.append(f"{file.relative_to(ROOT)} -> {path}")
                    break
    assert hits == [], "B-class Dream references leaked into product sources:\n" + "\n".join(hits[:20])


def test_repo_has_no_copied_b_class_files() -> None:
    paths = _manifest()["paths"]
    existing = [path for path in paths if (ROOT / path).exists()]
    assert existing == []
