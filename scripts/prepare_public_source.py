"""Export reviewed source files without private Git history or local data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import tomllib
import zipfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NEW_FILES = (
    ".github/workflows/quality-source.yml",
    "scripts/prepare_public_source.py",
    "tests/test_public_source_export.py",
    "docs/PUBLIC_SOURCE_RELEASE.md",
)


def public_files(root: Path) -> list[str]:
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
    paths = sorted(set(tracked.decode("utf-8").split("\0")[:-1]) | {
        name for name in NEW_FILES if (root / name).is_file()
    })
    attributes = subprocess.check_output(
        ["git", "check-attr", "-z", "--stdin", "export-ignore"],
        input=("\0".join(paths) + "\0").encode("utf-8"),
        cwd=root,
    ).decode("utf-8").split("\0")[:-1]
    return [
        attributes[i] for i in range(0, len(attributes), 3)
        if attributes[i + 2] != "set"
    ]


def export_source(root: Path, destination: Path) -> dict[str, str]:
    destination.resolve().relative_to(root.resolve() / "workspace" / "artifacts")
    if destination.exists():
        raise FileExistsError("Public source output already exists; choose a new directory")
    paths = public_files(root)
    sources = []
    for name in paths:
        source = root / name
        source.resolve().relative_to(root.resolve())
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Export requires an ordinary tracked file: {name}")
        sources.append((name, source))
    destination.mkdir(parents=True)
    for name, source in sources:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    config_path = destination / ".gitleaks.toml"
    if config_path.exists():
        config_text = config_path.read_text(encoding="utf-8")
        expected = tomllib.loads(config_text)
        expected.get("allowlist", {}).pop("commits", None)
        config_text = re.sub(r"(?m)^commits\s*=\s*\[[\s\S]*?\]\s*\n", "", config_text)
        if tomllib.loads(config_text) != expected:
            raise ValueError("Removing commit exemptions changed other secret scan rules")
        config_path.write_text(config_text, encoding="utf-8")

    inventory = destination / "license-inventory.csv"
    if inventory.exists():
        with inventory.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = reader.fieldnames
            rows = [row for row in reader if row["path"] in paths]
        missing_new_files = set(paths).intersection(NEW_FILES) - {row["path"] for row in rows}
        if missing_new_files:
            reuse = tomllib.loads((destination / "REUSE.toml").read_text(encoding="utf-8"))
            annotation = next(row for row in reuse["annotations"] if row["path"] == ["**"])
            rows.extend({
                "path": name,
                "license_expression": annotation["SPDX-License-Identifier"],
                "copyright": annotation["SPDX-FileCopyrightText"],
                "evidence": "REUSE.toml aggregate annotation",
            } for name in sorted(missing_new_files))
        with inventory.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    sbom_path = destination / "sbom.spdx.json"
    if sbom_path.exists():
        sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
        removed_ids = {
            item["SPDXID"] for item in sbom.get("files", [])
            if item["fileName"].removeprefix("./") not in paths
        }
        sbom["files"] = [item for item in sbom.get("files", []) if item["SPDXID"] not in removed_ids]
        sbom["relationships"] = [
            item for item in sbom.get("relationships", [])
            if item["spdxElementId"] not in removed_ids and item["relatedSpdxElement"] not in removed_ids
        ]
        sbom_path.write_text(json.dumps(sbom, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        name: hashlib.sha256((destination / name).read_bytes()).hexdigest()
        for name in paths
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    destination = args.output or ROOT / "workspace" / "artifacts" / (
        "public-source-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    for path in (destination, destination.with_suffix(".manifest.json"), Path(str(destination) + ".zip")):
        if path.exists():
            raise FileExistsError("Public source output already exists; choose a new name")
    hashes = export_source(ROOT, destination)
    destination.with_suffix(".manifest.json").write_text(
        json.dumps({"files": hashes, "git_history_included": False}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    entries = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=ROOT)
    modes = {
        record.split("\t", 1)[1]: int(record.split(" ", 1)[0], 8)
        for record in entries.decode("utf-8").split("\0") if record
    }
    archive_path = Path(str(destination) + ".zip")
    with zipfile.ZipFile(archive_path, "x") as archive:
        for name in hashes:
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = modes.get(name, 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (destination / name).read_bytes())
    print(json.dumps({
        "directory": str(destination), "archive": str(archive_path),
        "file_count": len(hashes),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
