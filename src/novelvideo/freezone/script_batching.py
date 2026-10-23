"""Deterministic batching for long, explicitly numbered scripts.

Only sources with explicit ``镜N`` headings are batched. The planner never splits
a shot, keeps source order, and leaves short/creative inputs on the single-call
path. Orchestration/checkpointing stays in the job runner.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

_EXPLICIT_SHOT_RE = re.compile(r"^\s*镜\s*(\d+)\s*(?:[：:、.\-]|$)")


def explicit_shot_numbers(source_text: str) -> list[int]:
    numbers: list[int] = []
    for line in (source_text or "").splitlines():
        match = _EXPLICIT_SHOT_RE.match(line)
        if match:
            numbers.append(int(match.group(1)))
    return numbers


def plan_source_batches(
    source_text: str,
    expected_shot_numbers: Sequence[int],
    *,
    max_shots: int = 8,
    max_chars: int = 12_000,
    threshold: int = 16,
) -> list[dict[str, Any]]:
    """Return batches; a short or unnumbered source returns one whole batch."""
    text = source_text or ""
    numbers = [int(number) for number in expected_shot_numbers]
    if len(numbers) < threshold:
        return [
            {
                "batch_id": "all",
                "shot_numbers": numbers,
                "line_start": 1,
                "line_end": len(text.splitlines()) or 1,
                "source_text": text,
                "global_context": text[:1200],
            }
        ]
    lines = text.splitlines()
    starts: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        match = _EXPLICIT_SHOT_RE.match(line)
        if match:
            starts.append((int(match.group(1)), index))
    units: dict[int, tuple[int, int, str]] = {}
    for position, (shot_no, line_start) in enumerate(starts):
        line_end = starts[position + 1][1] if position + 1 < len(starts) else len(lines)
        units[shot_no] = (
            line_start + 1,
            line_end,
            "\n".join(lines[line_start:line_end]).strip(),
        )
    missing = [number for number in numbers if number not in units]
    if missing:
        raise ValueError(f"来源缺少明确镜号：{missing[:12]}；不能假装分批覆盖")
    batches: list[dict[str, Any]] = []
    current_numbers: list[int] = []
    current_texts: list[str] = []
    current_lines: list[int] = []
    current_chars = 0

    def flush() -> None:
        nonlocal current_numbers, current_texts, current_lines, current_chars
        if not current_numbers:
            return
        batches.append(
            {
                "batch_id": f"shot-{current_numbers[0]}-{current_numbers[-1]}",
                "shot_numbers": list(current_numbers),
                "line_start": min(current_lines),
                "line_end": max(current_lines),
                "source_text": "\n\n".join(current_texts),
                "global_context": text[:1200],
            }
        )
        current_numbers = []
        current_texts = []
        current_lines = []
        current_chars = 0

    for number in numbers:
        line_start, line_end, unit_text = units[number]
        if current_numbers and (
            len(current_numbers) >= max_shots
            or current_chars + len(unit_text) > max_chars
            or number != current_numbers[-1] + 1
        ):
            flush()
        current_numbers.append(number)
        current_texts.append(unit_text)
        current_lines.extend([line_start, line_end])
        current_chars += len(unit_text)
    flush()
    return batches
