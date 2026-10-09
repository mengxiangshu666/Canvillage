"""Freeze the model-visible Village canvas agent surface.

``src/novelvideo/agent_tools/village_canvas`` is loaded by exec-ing seven ordered
source parts into one namespace, so the three faces an agent actually sees are
built in three different files with nothing keeping them in step:

* ``TOOLS`` -- ``(name, schema, handler)`` for every directly exposed tool
* ``_CORE_CAPABILITY_INDEX`` -- the core cards the capability broker searches
* ``_capability_handler()`` -- capability id -> handler

The same capability therefore gets written down up to three times: the schema can
drift from the server constant (``max_paid_starts`` said 4 while the server said
64, so the model was rejected before it ever reached the route), and a card can
name a handler that lives somewhere else entirely.

This gate is the target that refactor is shot at. The recorded surface is frozen,
so a migration that quietly renames a tool, reorders the model-visible list, drops
a parameter or points a capability at a different handler fails here instead of
in a live agent turn. Read-only: ``--print-baseline`` is the only way the surface
may change, and that change has to be an explicit, reviewable diff.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_BASELINE = Path(__file__).with_name("agent_tool_surface.json")
SURFACE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str
    path: str
    reason: str


def _handler_name(value: object) -> str | None:
    """Record which callable answers a capability, not the object itself."""

    if value is None:
        return None
    return (
        getattr(value, "__qualname__", None)
        or getattr(value, "__name__", None)
        or type(value).__name__
    )


def build_surface() -> dict[str, Any]:
    """Read the live surface out of the plugin package."""

    from novelvideo.agent_tools import village_canvas as surface

    direct_tools = [
        {"name": str(name), "handler": _handler_name(handler), "schema": schema}
        for name, schema, handler in surface.TOOLS
    ]
    core_cards = [dict(card) for card in surface._CORE_CAPABILITY_INDEX]
    capability_handlers = {
        str(card["id"]): _handler_name(surface._capability_handler(str(card["id"])))
        for card in surface._CAPABILITY_INDEX
    }
    return {
        "schema_version": SURFACE_SCHEMA_VERSION,
        "direct_tools": direct_tools,
        "core_capability_cards": core_cards,
        "capability_handlers": dict(sorted(capability_handlers.items())),
    }


def _first_difference(expected: Any, observed: Any, path: str = "") -> str | None:
    """Return the first differing leaf, so a failure names what moved."""

    if type(expected) is not type(observed):
        return (
            f"{path}: type {type(expected).__name__} -> "
            f"{type(observed).__name__}"
        )
    if isinstance(expected, dict):
        for key in sorted(set(expected) | set(observed)):
            child = f"{path}.{key}" if path else str(key)
            if key not in expected:
                return f"{child}: added"
            if key not in observed:
                return f"{child}: removed"
            found = _first_difference(expected[key], observed[key], child)
            if found is not None:
                return found
        return None
    if isinstance(expected, list):
        if len(expected) != len(observed):
            return f"{path}: length {len(expected)} -> {len(observed)}"
        for index, (left, right) in enumerate(zip(expected, observed)):
            found = _first_difference(left, right, f"{path}[{index}]")
            if found is not None:
                return found
        return None
    if expected != observed:
        return f"{path}: {expected!r} -> {observed!r}"
    return None


def _compare_entries(
    findings: list[Finding],
    *,
    section: str,
    identity: str,
    expected: list[dict[str, Any]],
    observed: list[dict[str, Any]],
) -> None:
    expected_by_id = {str(item[identity]): item for item in expected}
    observed_by_id = {str(item[identity]): item for item in observed}

    for key in sorted(expected_by_id.keys() - observed_by_id.keys()):
        findings.append(
            Finding(
                kind="surface-entry-removed",
                severity="high",
                path=f"{section}[{key}]",
                reason=(
                    f"{section} 里登记的 {identity}={key} 在代码里找不到了；"
                    "迁移可以搬走条目，但不能让它消失。"
                ),
            )
        )
    for key in sorted(observed_by_id.keys() - expected_by_id.keys()):
        findings.append(
            Finding(
                kind="surface-entry-added",
                severity="high",
                path=f"{section}[{key}]",
                reason=(
                    f"代码里多出 {section} 条目 {identity}={key}，基线里没有；"
                    "加能力要显式写进基线，不能顺手带出来。"
                ),
            )
        )
    for key in sorted(expected_by_id.keys() & observed_by_id.keys()):
        difference = _first_difference(
            expected_by_id[key], observed_by_id[key], f"{section}[{key}]"
        )
        if difference is not None:
            findings.append(
                Finding(
                    kind="surface-entry-changed",
                    severity="high",
                    path=f"{section}[{key}]",
                    reason=f"{identity}={key} 的内容变了：{difference}",
                )
            )

    expected_order = [str(item[identity]) for item in expected]
    observed_order = [str(item[identity]) for item in observed]
    if expected_order != observed_order:
        difference = _first_difference(
            expected_order, observed_order, f"{section}.order"
        )
        findings.append(
            Finding(
                kind="surface-order-changed",
                severity="high",
                path=f"{section}.order",
                reason=(
                    "模型可见顺序变了（顺序是产品决定，不是加载顺序的副产物）："
                    f"{difference}"
                ),
            )
        )


def collect_findings(
    observed: dict[str, Any], expected: dict[str, Any]
) -> list[Finding]:
    findings: list[Finding] = []
    if observed.get("schema_version") != expected.get("schema_version"):
        findings.append(
            Finding(
                kind="baseline-schema-version",
                severity="high",
                path="schema_version",
                reason=(
                    "基线文件的 schema_version 与门禁不一致；"
                    "重新生成基线而不是手改版本号。"
                ),
            )
        )
        return findings

    _compare_entries(
        findings,
        section="direct_tools",
        identity="name",
        expected=list(expected.get("direct_tools") or []),
        observed=list(observed.get("direct_tools") or []),
    )
    _compare_entries(
        findings,
        section="core_capability_cards",
        identity="id",
        expected=list(expected.get("core_capability_cards") or []),
        observed=list(observed.get("core_capability_cards") or []),
    )

    expected_handlers = dict(expected.get("capability_handlers") or {})
    observed_handlers = dict(observed.get("capability_handlers") or {})
    for key in sorted(set(expected_handlers) | set(observed_handlers)):
        if key not in observed_handlers:
            findings.append(
                Finding(
                    kind="capability-handler-removed",
                    severity="high",
                    path=f"capability_handlers.{key}",
                    reason=f"能力 {key} 不再解析到 handler。",
                )
            )
        elif key not in expected_handlers:
            findings.append(
                Finding(
                    kind="capability-handler-added",
                    severity="high",
                    path=f"capability_handlers.{key}",
                    reason=(
                        f"新出现的能力 {key} 解析到了 "
                        f"{observed_handlers[key]!r}，基线里没有。"
                    ),
                )
            )
        elif expected_handlers[key] != observed_handlers[key]:
            findings.append(
                Finding(
                    kind="capability-handler-changed",
                    severity="high",
                    path=f"capability_handlers.{key}",
                    reason=(
                        f"能力 {key} 的 handler 由 {expected_handlers[key]!r} "
                        f"变成 {observed_handlers[key]!r}。"
                    ),
                )
            )
    return findings


def load_baseline(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("baseline must be a JSON object")
    return payload


def build_report(*, baseline_path: Path) -> dict[str, Any]:
    observed = build_surface()
    expected = load_baseline(baseline_path)
    findings = collect_findings(observed, expected)
    severity_counts = dict(Counter(item.severity for item in findings))
    return {
        "schema_version": SURFACE_SCHEMA_VERSION,
        "counts": {
            "direct_tools": len(observed["direct_tools"]),
            "core_capability_cards": len(observed["core_capability_cards"]),
            "capability_handlers": len(observed["capability_handlers"]),
        },
        "findings": [item.__dict__ for item in findings],
        "severity_counts": severity_counts,
    }


def render_text(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "agent tool surface: "
        f"{counts['direct_tools']} direct tools / "
        f"{counts['core_capability_cards']} core cards / "
        f"{counts['capability_handlers']} capability handlers",
    ]
    for item in report["findings"]:
        lines.append(
            f"[{item['severity']}] {item['kind']} {item['path']}: {item['reason']}"
        )
    if not report["findings"]:
        lines.append("finding_count=0")
    return "\n".join(lines)


def render_surface() -> str:
    payload = build_surface()
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--fail-on",
        default="",
        help="comma-separated severities that should return exit code 1",
    )
    parser.add_argument(
        "--print-baseline",
        action="store_true",
        help="print the recorded surface for the current tree and exit 0",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help=(
            "rewrite the baseline file from the current tree (UTF-8, LF) and "
            "exit 0; this is the only sanctioned way the surface may change"
        ),
    )
    args = parser.parse_args(argv)

    if args.write_baseline:
        args.baseline.write_text(render_surface(), encoding="utf-8", newline="\n")
        print(f"wrote {args.baseline}")
        return 0
    if args.print_baseline:
        print(render_surface(), end="")
        return 0

    baseline_path = args.baseline.resolve()
    if not baseline_path.is_file():
        parser.error(f"baseline does not exist: {baseline_path}")
    try:
        report = build_report(baseline_path=baseline_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report))

    fail_on = {value.strip() for value in args.fail_on.split(",") if value.strip()}
    return int(bool(fail_on.intersection(report["severity_counts"])))


if __name__ == "__main__":
    raise SystemExit(main())
