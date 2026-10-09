"""Build a deterministic, narrative-safe subset of video-shotcraft."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from urllib.parse import quote


ALLOWED_CATEGORIES = (
    "camera",
    "transition",
    "rhythm",
    "effects",
    "opening",
    "outro",
)
CORE_REFERENCES = (
    "aesthetic-rules.md",
    "music-beat-sync.md",
    "sound-design.md",
    "final-review.md",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_source(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError(f"source path escapes upstream root: {relative}")
    if candidate.suffix.lower() not in {".md", ".txt"} and candidate.name != "LICENSE":
        raise ValueError(f"unsupported source type: {relative}")
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


def _reset_owned_output(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for name in ("cards", "core"):
        target = output / name
        if target.exists():
            shutil.rmtree(target)
    for name in ("catalog.json", "manifest.json", "INDEX.md", "UPSTREAM_LICENSE.txt"):
        target = output / name
        if target.exists():
            target.unlink()


def build(upstream: Path, output: Path, *, revision: str) -> dict:
    upstream = upstream.resolve()
    output = output.resolve()
    library_path = upstream / "gallery" / "api" / "library.json"
    library = json.loads(library_path.read_text(encoding="utf-8"))
    cards = sorted(
        (
            card
            for card in library.get("cards", [])
            if str(card.get("category") or "") in ALLOWED_CATEGORIES
        ),
        key=lambda card: (str(card.get("category") or ""), str(card.get("name") or "")),
    )
    if not cards:
        raise ValueError("no allowlisted shot cards found")

    _reset_owned_output(output)
    records: list[dict] = []
    catalog_cards: list[dict] = []

    def copy_text(source: Path, relative_output: Path, *, source_name: str) -> None:
        data = source.read_bytes()
        data.decode("utf-8")
        destination = output / relative_output
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        records.append(
            {
                "source": source_name,
                "bundle_path": relative_output.as_posix(),
                "sha256": _sha256(data),
                "size": len(data),
            }
        )

    for card in cards:
        name = str(card.get("name") or "").strip()
        category = str(card.get("category") or "").strip()
        source_name = str(card.get("source") or "").strip()
        if not name or not source_name:
            raise ValueError(f"invalid card metadata: {card!r}")
        source = _safe_source(upstream, source_name)
        bundled = Path("cards") / category / f"{name}.md"
        copy_text(source, bundled, source_name=source_name)
        catalog_cards.append(
            {
                "name": name,
                "category": category,
                "summary": str(card.get("summary") or "").strip(),
                "use": str(card.get("use") or "").strip(),
                "duration": str(card.get("duration") or "").strip(),
                "energy": str(card.get("energy") or "").strip(),
                "intention": str(card.get("intention") or "").strip(),
                "tags": list(card.get("tags") or []),
                "source": bundled.as_posix(),
            }
        )

    for name in CORE_REFERENCES:
        source_name = f"references/{name}"
        copy_text(
            _safe_source(upstream, source_name),
            Path("core") / name,
            source_name=source_name,
        )

    license_source = _safe_source(upstream, "LICENSE")
    copy_text(license_source, Path("UPSTREAM_LICENSE.txt"), source_name="LICENSE")

    catalog = {
        "schema_version": 1,
        "owner": "Village Infinite Canvas",
        "scope": "narrative_shotcraft_adapter",
        "upstream": "Vincentwei1021/video-shotcraft",
        "upstream_revision": revision,
        "upstream_catalog_revision": str(library.get("revision") or ""),
        "allowed_categories": list(ALLOWED_CATEGORIES),
        "cards": catalog_cards,
    }
    catalog_bytes = (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    (output / "catalog.json").write_bytes(catalog_bytes)
    records.append(
        {
            "source": "gallery/api/library.json#allowlisted-metadata",
            "bundle_path": "catalog.json",
            "sha256": _sha256(catalog_bytes),
            "size": len(catalog_bytes),
        }
    )

    hash_rows = [
        "\0".join((item["bundle_path"], item["sha256"], str(item["size"])))
        for item in sorted(records, key=lambda item: item["bundle_path"])
    ]
    manifest = {
        "schema_version": 1,
        "scope": "narrative_shotcraft_adapter",
        "owner": "Village Infinite Canvas",
        "upstream": "Vincentwei1021/video-shotcraft",
        "upstream_revision": revision,
        "allowed_categories": list(ALLOWED_CATEGORIES),
        "summary": {
            "cards": len(catalog_cards),
            "core_references": len(CORE_REFERENCES),
            "files": len(records),
        },
        "files": sorted(records, key=lambda item: item["bundle_path"]),
        "bundle_hash": _sha256("\n".join(hash_rows).encode("utf-8")),
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    (output / "manifest.json").write_bytes(manifest_bytes)

    counts = {
        category: sum(1 for card in catalog_cards if card["category"] == category)
        for category in ALLOWED_CATEGORIES
    }
    lines = [
        "# Village Infinite Canvas Shotcraft 精简索引",
        "",
        f"- 上游：`Vincentwei1021/video-shotcraft@{revision}`",
        f"- 镜头卡：{len(catalog_cards)}",
        f"- 分类：{', '.join(f'{key}={value}' for key, value in counts.items())}",
        "",
    ]
    for category in ALLOWED_CATEGORIES:
        lines.extend((f"## {category}", ""))
        for card in (item for item in catalog_cards if item["category"] == category):
            path = quote(card["source"], safe="/")
            lines.append(f"- [{card['name']}]({path}) — {card['summary']}")
        lines.append("")
    (output / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    result = build(args.upstream, args.output, revision=args.revision)
    print(json.dumps(result["summary"], ensure_ascii=False))
