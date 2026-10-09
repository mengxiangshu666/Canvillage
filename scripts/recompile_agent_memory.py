"""Plan or apply the Xiaoshu v2 memory recompilation migration."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json

from novelvideo.chat.memory_migration import (
    apply_memory_recompile,
    plan_memory_recompile,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recompile raw Agent memories into executable Xiaoshu memory records."
    )
    parser.add_argument("--user", default="local")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the migration after creating an online SQLite backup.",
    )
    args = parser.parse_args()

    if args.apply:
        result = apply_memory_recompile(args.user)
    else:
        actions = plan_memory_recompile(args.user)
        counts: dict[str, int] = {}
        reasons: dict[str, int] = {}
        for action in actions:
            counts[action.action] = counts.get(action.action, 0) + 1
            reasons[action.reason] = reasons.get(action.reason, 0) + 1
        result = {
            "mode": "dry-run",
            "user": args.user,
            "action_count": len(actions),
            "actions": counts,
            "reasons": reasons,
            "action_details": [asdict(action) for action in actions[:500]],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
