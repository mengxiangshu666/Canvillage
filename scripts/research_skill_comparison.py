"""Read-only census of reference skills; emits metadata, never installs skills."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    registry = root / "docs/REFERENCE_CORPUS_REGISTRY.md"
    # The registry is the sole path mapping. External locations stay out of output.
    mappings = {}
    for line in registry.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip().strip("`") for cell in line.split("|")]
        if len(cells) > 3 and cells[1] in {"libtv", "oiioii", "tapcanvas"}:
            mappings[cells[1]] = Path(cells[2])
    corpus = mappings["libtv"]
    archive = corpus / "20_语料/_skill_md_ALL.json"
    rows = load(archive)
    detail_dir = corpus / "30_证据/refresh_20261005/skill_details"
    latest = {}
    errors = []
    for path in sorted(detail_dir.glob("*.json")):
        value = load(path)
        skill = value.get("data", {}).get("skill")
        if value.get("code") != 0 or not isinstance(skill, dict):
            errors.append(path.name)
            continue
        latest[skill["templateUuid"]] = skill
    names = {
        "电影级 AI 分镜导演", "AI导演-剧本诊断改稿", "AI 视频光影提示词",
        "去AI感真实影像导演", "AI影视角色表演导演", "服装品牌宣传TVC导演",
        "产品广告导演一键成片", "电影级 AI 视觉资产导演", "短剧全流程系统",
        "S+短剧质量评分器 ·", "电影级 AI 视频提示词导演",
    }
    inventory = []
    samples = []
    body_hashes = Counter()
    for row in rows:
        files = row.get("skillFiles") or []
        present = [f for f in files if str(f.get("content") or "").strip()]
        paths = {f.get("path") for f in present}
        primary = row.get("primarySkillFilePath") or "SKILL.md"
        current = latest.get(row.get("templateUuid"))
        summary = {
            "name": row.get("name"), "template_uuid": row.get("templateUuid"),
            "body_version": row.get("version"),
            "current_version": current.get("version") if current else None,
            "current_detail_available": current is not None,
            "version_match": current.get("version") == row.get("version") if current else None,
            "body_files": len(present), "body_chars": sum(len(f["content"]) for f in present),
            "primary_present": primary in paths,
            "source": "libtv §20_语料/_skill_md_ALL.json",
        }
        inventory.append(summary)
        for file in present:
            body_hashes[hashlib.sha256(file["content"].encode()).hexdigest()] += 1
        if row.get("name") in names:
            samples.append({**summary, "files": [
                {"path": f["path"], "chars": len(f["content"]),
                 "sha256": hashlib.sha256(f["content"].encode()).hexdigest(),
                 "headings": re.findall(r"^#{1,4}\s+(.+)$", f["content"], re.M)[:35]}
                for f in present
            ]})
    village = []
    for path in sorted((root / "agent_skills").glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8-sig")
        village.append({"path": path.relative_to(root).as_posix(), "sha256": digest(path),
                        "chars": len(text), "headings": re.findall(r"^#{1,3}\s+(.+)$", text, re.M)[:20]})
    report = {
        "scope": "metadata census plus explicitly selected body samples; not quality ranking",
        "sources": {"library": {"alias": "libtv §20_语料/_skill_md_ALL.json", "sha256": digest(archive)},
                    "details": "libtv §30_证据/refresh_20261005/skill_details"},
        "counts": {"latest_detail_unique": len(latest), "detail_errors": len(errors),
                   "body_records": len(rows), "body_unique_uuid": len({r.get('templateUuid') for r in rows}),
                   "body_records_matching_current_version": sum(r['version_match'] is True for r in inventory),
                   "body_records_with_changed_version": sum(r['version_match'] is False for r in inventory),
                   "body_records_without_current_detail": sum(not r['current_detail_available'] for r in inventory),
                   "primary_missing": sum(not r['primary_present'] for r in inventory),
                   "distinct_file_bodies": len(body_hashes), "file_body_occurrences": sum(body_hashes.values()),
                   "village_skill_files": len(village)},
        "detail_errors": errors, "selected_samples": samples, "body_inventory": inventory,
        "village_skills": village,
        "limits": ["version equality does not prove current body equality",
                   "public metadata is not executable body", "counts do not establish craft quality",
                   "no runtime activation or paid media was tested"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
