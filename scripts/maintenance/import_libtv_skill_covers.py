"""Import libtv skill covers into the bundled Skill Store catalog.

The bundled catalog (``src/novelvideo/skills_distill/catalog/libtv_skills.json``)
carries 127 libtv reference skills whose ``skillId`` keeps the platform's
``<slug>-<32 hex>`` shape. The r77 market snapshot
(``20_语料/skill_market/SKILL_MARKET_20261027.json``) has the same
``skillKey`` for 119 of them plus a locally archived cover per skill. This tool
matches on the normalized key, writes a 640x360 WebP cover into
``frontend/public/images/skill-covers/libtv/`` and stamps
``optimized.cover_image`` back onto the catalog entry.

Usage:
    .venv/Scripts/python.exe scripts/maintenance/import_libtv_skill_covers.py \
        --source E:/AI影视研究/libtv爬取/20_语料/skill_market/SKILL_MARKET_20261027.json

The snapshot is read-only. Cover images are third-party platform material; see
docs/ai/acceptance/skill-store-hover-card-20261027.md for the rights caveat
before publishing outside this local workspace.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from PIL import Image, ImageOps

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG = REPO_ROOT / "src" / "novelvideo" / "skills_distill" / "catalog" / "libtv_skills.json"
COVER_DIR = REPO_ROOT / "frontend" / "public" / "images" / "skill-covers" / "libtv"
PUBLIC_PREFIX = "/images/skill-covers/libtv"
COVER_SIZE = (640, 360)
TRAILING_UUID = re.compile(r"-[0-9a-f]{32}$")


def normalize_key(value: object) -> str:
    return TRAILING_UUID.sub("", str(value or "").strip().lower())


def unique_name_index(items: list[dict]) -> dict[str, dict]:
    """Only keep names that map to exactly one market row."""
    seen: dict[str, dict | None] = {}
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        seen[name] = None if name in seen else item
    return {name: item for name, item in seen.items() if item is not None}


def write_cover(source: Path, target: Path) -> bool:
    try:
        with Image.open(source) as image:
            rendered = ImageOps.fit(image.convert("RGB"), COVER_SIZE, method=Image.LANCZOS)
            target.parent.mkdir(parents=True, exist_ok=True)
            rendered.save(target, "WEBP", quality=82, method=4)
        return True
    except (OSError, ValueError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="libtv skill market snapshot json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source_path = Path(args.source)
    market = json.loads(source_path.read_text(encoding="utf-8"))["items"]
    corpus_root = source_path.parents[2]

    by_key = {normalize_key(item.get("skillKey")): item for item in market if item.get("skillKey")}
    by_name = unique_name_index(market)

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    matched = covered = 0
    skipped: list[str] = []

    for entry in catalog:
        key = normalize_key(entry.get("skillId"))
        row = by_key.get(key)
        if row is None:
            row = by_name.get(str((entry.get("optimized") or {}).get("name") or entry.get("name") or "").strip())
        if row is None or not row.get("coverLocal"):
            skipped.append(str(entry.get("skillId")))
            continue
        matched += 1

        slug = key or normalize_key(row.get("skillKey"))
        if not slug:
            skipped.append(str(entry.get("skillId")))
            continue
        source_image = corpus_root / str(row["coverLocal"]).replace("\\", "/")
        target = COVER_DIR / f"{slug}.webp"
        if not args.dry_run:
            if not target.exists() and not write_cover(source_image, target):
                skipped.append(str(entry.get("skillId")))
                continue
            optimized = entry.setdefault("optimized", {})
            optimized["cover_image"] = f"{PUBLIC_PREFIX}/{slug}.webp"
        covered += 1

    if not args.dry_run:
        CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (COVER_DIR / "SOURCE.md").write_text(
            "# Skill 封面来源\n\n"
            "这些封面由 `scripts/maintenance/import_libtv_skill_covers.py` 从本地只读语料\n"
            "`libtv` 的 r77 技能市场快照（`20_语料/skill_market/`）按 skillKey 匹配后导出，\n"
            "统一裁成 640×360 WebP，随产品落盘，运行时不访问 libtv 或任何外部 CDN。\n\n"
            "封面是 libtv 平台上的第三方创作者素材，本仓仅作本地参考技能列表的预览用。\n"
            "对外分发前必须重新确认授权：`docs/ai/acceptance/skill-store-hover-card-20261027.md`。\n",
            encoding="utf-8",
        )

    print(f"catalog entries: {len(catalog)}")
    print(f"matched with local cover: {matched}")
    print(f"covers written/stamped: {covered}")
    print(f"without cover: {len(skipped)}")
    for skill_id in skipped[:10]:
        print("  -", skill_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
