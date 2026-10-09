"""CLI for the reference corpus source snapshot and wash pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.reference_distill.core import build_reference_distillation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Snapshot the authoritative source surfaces of tapcanvas, libtv and "
            "oiioii, then emit a normalized native capability ledger."
        )
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Village Infinite Canvas repository root.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output root. Defaults to <project-root>/workspace/reference_distill.",
    )
    parser.add_argument(
        "--corpus",
        action="append",
        choices=("tapcanvas", "libtv", "oiioii"),
        default=None,
        help="Corpus to snapshot. Repeat to select more than one.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the generated summary as JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project_root = args.project_root.resolve()
    output_root = (
        args.output.resolve()
        if args.output is not None
        else project_root / "workspace" / "reference_distill"
    )
    selected = args.corpus or ["tapcanvas", "libtv", "oiioii"]
    summary = build_reference_distillation(
        project_root=project_root,
        output_root=output_root,
        corpora=selected,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"output: {summary['output_root']}")
        for alias, item in summary["snapshots"].items():
            print(
                f"{alias}: files={item['file_count']} "
                f"bytes={item['total_bytes']} snapshot={item['snapshot_id']}"
            )
        native = summary["native_map"]
        print(
            "native map: "
            + ", ".join(
                f"{status}={count}" for status, count in native["status_counts"].items()
            )
        )
        print(f"unresolved: {len(native['unresolved'])}")
    return 0


__all__ = ["main"]
