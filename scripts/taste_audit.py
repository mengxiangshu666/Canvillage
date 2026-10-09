"""Audit a hand-authored shot plan against the taste KB, and keep the rotation ledger.

Run this **before** any paid generation.  It answers one question: does this
plan look like a film someone designed, or like the model's defaults wearing a
different plot?

Zero cost by construction — it reads a JSON file and does arithmetic.  The
ledger records which aesthetic families and which named clichés each film
spent, so the next film can be blocked from spending them again.  Without that
memory, every project re-derives its look and lands back on the same defaults.

Usage::

    .venv\\Scripts\\python.exe scripts\\taste_audit.py ^
        --plan workspace/artifacts/film-source/喂招-shots.json

    .venv\\Scripts\\python.exe scripts\\taste_audit.py ^
        --plan ... --record --title 喂招 --output-dir E:/村长无限画布_产出
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from novelvideo.production.taste_engines import (  # noqa: E402
    audit_plan,
    detect_cliches,
)
from novelvideo.production.taste_kb import (  # noqa: E402
    AESTHETIC_PROFILE_FIELDS,
    BANNED_CLICHES,
    QUOTAS,
)

LEDGER_SCHEMA = "taste_ledger.v1"
DEFAULT_LEDGER = ROOT / "workspace" / "artifacts" / "taste" / "ledger.json"


# --------------------------------------------------------------------------- #
# ledger
# --------------------------------------------------------------------------- #

def load_ledger(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema": LEDGER_SCHEMA, "projects": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": LEDGER_SCHEMA, "projects": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("projects"), list):
        return {"schema": LEDGER_SCHEMA, "projects": []}
    return payload


def previous_families(ledger: dict[str, Any]) -> dict[str, list[str]]:
    """Map each earlier film to the families and clichés it spent."""
    out: dict[str, list[str]] = {}
    for entry in ledger.get("projects", []):
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or entry.get("plan") or "未命名")
        spent: list[str] = []
        for value in (entry.get("families") or {}).values():
            if value:
                spent.append(str(value))
        spent += [str(item) for item in (entry.get("cliches") or [])]
        out[title] = spent
    return out


def record(ledger: dict[str, Any], *, title: str, plan: dict[str, Any], families: dict[str, str]) -> dict[str, Any]:
    hits = sorted(detect_cliches(plan))
    entry = {
        "title": title,
        "recordedAt": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "families": {key: value for key, value in families.items() if value},
        "cliches": hits,
    }
    projects = [p for p in ledger.get("projects", []) if str(p.get("title")) != title]
    projects.append(entry)
    ledger["projects"] = projects
    return ledger


def profile_families(plan: dict[str, Any]) -> dict[str, str]:
    """Read declared families; fall back to the cliché families actually present.

    A plan that never declared its families still has them — they are visible in
    what it wrote.  Reading them from the cliché hits is what makes rotation work
    for plans authored before the profile existed.
    """
    out: dict[str, str] = {}
    for field in AESTHETIC_PROFILE_FIELDS:
        name = field["field"]
        if name.endswith("_family"):
            value = plan.get(name)
            if isinstance(value, str) and value.strip():
                out[name] = value.strip()
    if not out:
        by_id = {item["cliche_id"]: item["label"] for item in BANNED_CLICHES}
        for cliche_id in detect_cliches(plan):
            out[f"cliche:{cliche_id}"] = by_id.get(cliche_id, cliche_id)
    return out


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #

def render(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"评分 {report['score']}/100 ｜ 判定 {report['verdict'].upper()}")
    lines.append(
        f"硬缺陷 {report['hardCount']} ｜ 软提醒 {report['softCount']} ｜ 镜头 {report['shotCount']}"
    )
    lines.append("")
    if report["hardFindings"]:
        lines.append("=== 硬缺陷（不修不许进付费生成）===")
        for item in report["hardFindings"]:
            where = f"镜头 {item['shot']}" if "shot" in item else "整片"
            lines.append(f"  [{item['code']}] {where}：{item['message']}")
            lines.append(f"      证据：{item['evidence']}")
            lines.append(f"      改法：{item['fix']}")
        lines.append("")
    if report["softFindings"]:
        lines.append("=== 软提醒 ===")
        for item in report["softFindings"]:
            where = f"镜头 {item['shot']}" if "shot" in item else "整片"
            lines.append(f"  [{item['code']}] {where}：{item['message']}")
            lines.append(f"      改法：{item['fix']}")
        lines.append("")
    lines.append(report["readThis"])
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--plan", required=True, help="hand-authored shot plan (JSON)")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--title", default="", help="film title for the ledger (default: from the plan)")
    parser.add_argument("--record", action="store_true", help="write this film's families to the ledger")
    parser.add_argument("--json", action="store_true", help="emit the raw report as JSON")
    parser.add_argument(
        "--output-dir", default="",
        help="also write report.json here (e.g. the film's delivery folder)",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    plan_path = Path(args.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    title = args.title or str(plan.get("title") or plan_path.stem)

    ledger_path = Path(args.ledger)
    ledger = load_ledger(ledger_path)
    families = profile_families(plan)
    report = audit_plan(
        plan,
        previous_families=previous_families(ledger),
        profile_families=families,
    )
    report["title"] = title
    report["plan"] = plan_path.name
    report["families"] = families
    report["quotaTable"] = QUOTAS

    if args.record:
        ledger = record(ledger, title=title, plan=plan, families=families)
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"已记账：{title} -> {ledger_path}")

    if args.output_dir:
        target = Path(args.output_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "审美审计.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"已写出：{target / '审美审计.json'}")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))
    return 0 if report["verdict"] != "blocked" else 1


if __name__ == "__main__":
    sys.exit(main())
