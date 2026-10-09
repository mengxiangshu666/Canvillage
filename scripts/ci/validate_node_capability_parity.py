#!/usr/bin/env python3
"""Check that executable node capabilities agree across UI, Agent, and backend.

The frontend catalog and the Agent capability index intentionally remain owned by
their respective layers.  This gate only compares their executable contract, so
it catches drift without introducing a second runtime registry.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "village_canvas.node_capability_parity.v1"

# These are stable cross-layer IDs.  Frontend IDs are deliberately explicit:
# their human-facing names use kebab case while Agent cards use canonical IDs.
FRONTEND_TO_AGENT = {
    "image-crop": "canvas.node.image.crop",
    "image-annotate": "canvas.node.image.annotate",
    "storyboard-split-input": "canvas.node.storyboard.split_input",
    "video-capture-first-frame": "canvas.node.video.capture_first_frame",
    "video-capture-last-frame": "canvas.node.video.capture_last_frame",
    "video-capture-current-frame": "canvas.node.video.capture_current_frame",
    "video-clip": "canvas.node.video.clip",
    "video-subtitle-erase-smart": "canvas.node.video.subtitle_erase_smart",
    "video-subtitle-erase-box": "canvas.node.video.subtitle_erase_box",
    "video-story-analysis": "canvas.node.video.story_analysis",
    "video-upscale": "canvas.node.video.upscale",
    "video-audio-separate": "canvas.node.video.audio_separate",
    "video-download": "canvas.node.video.download",
    "video-fullscreen": "canvas.node.video.fullscreen",
}


class ParityError(ValueError):
    """Raised when a source contract cannot be inspected."""


def _assignment_value(tree: ast.AST, name: str) -> Any:
    for node in getattr(tree, "body", []):
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = node.targets
            if any(isinstance(item, ast.Name) and item.id == name for item in targets):
                value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name:
                value = node.value
        if value is not None:
            try:
                return ast.literal_eval(value)
            except (ValueError, TypeError, SyntaxError) as exc:
                raise ParityError(f"{name} is not a literal source contract") from exc
    raise ParityError(f"missing {name}")


def _load_python(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise ParityError(f"cannot parse {path}: {exc}") from exc


def _agent_source_files(path: Path) -> tuple[Path, ...]:
    if path.is_dir():
        parts = tuple(
            path / name
            for name in (
                "core.py",
                "canvas_reads.py",
                "canvas_writes_impl.py",
                "workflow_dispatch.py",
                "workflow_execution.py",
                "capability_broker.py",
                "native_registry.py",
            )
            if (path / name).is_file()
        )
        if not parts:
            raise ParityError(
                f"missing Village Agent implementation modules under {path}"
            )
        return parts
    return (path,)


def _load_agent_python(path: Path) -> ast.Module:
    body: list[ast.stmt] = []
    for part in _agent_source_files(path):
        body.extend(_load_python(part).body)
    return ast.Module(body=body, type_ignores=[])


def _frontend_capabilities(path: Path) -> dict[str, dict[str, str | None]]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ParityError(f"cannot read {path}: {exc}") from exc

    id_re = re.compile(r"^\s+id:\s*'([^']+)',\s*$", re.MULTILINE)
    matches = list(id_re.finditer(source))
    capabilities: dict[str, dict[str, str | None]] = {}
    for index, match in enumerate(matches):
        capability_id = match.group(1)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        block = source[match.start() : end]
        execution = re.search(
            r"execution:\s*\{\s*kind:\s*'([^']+)'(?P<fields>.*?)\}",
            block,
            re.DOTALL,
        )
        if execution is None:
            continue
        fields = execution.group("fields")
        field_match = re.search(r"(?:taskType|action|event):\s*'([^']+)'", fields)
        capabilities.setdefault(
            capability_id,
            {
                "kind": execution.group(1),
                "target": field_match.group(1) if field_match else None,
            },
        )
    return capabilities


def _agent_capabilities(path: Path) -> dict[str, dict[str, Any]]:
    tree = _load_agent_python(path)
    cards: list[dict[str, Any]] = []
    for name in (
        "_CORE_CAPABILITY_INDEX",
        "_CREATIVE_CAPABILITY_INDEX",
        "_SKILL_CAPABILITY_INDEX",
    ):
        try:
            value = _assignment_value(tree, name)
        except ParityError:
            continue
        if isinstance(value, tuple):
            cards.extend(item for item in value if isinstance(item, dict))
    if path.is_dir():
        for declaration_path in sorted((path / "tools").glob("*.py")):
            declaration_tree = _load_python(declaration_path)
            for node in ast.walk(declaration_tree):
                if not isinstance(node, ast.Call):
                    continue
                if not (isinstance(node.func, ast.Name) and node.func.id == "ToolSpec"):
                    continue
                card_node = next(
                    (
                        keyword.value
                        for keyword in node.keywords
                        if keyword.arg == "card"
                    ),
                    None,
                )
                if card_node is None:
                    continue
                try:
                    card = ast.literal_eval(card_node)
                except (ValueError, TypeError, SyntaxError) as exc:
                    raise ParityError(
                        f"ToolSpec card in {declaration_path} is not literal"
                    ) from exc
                if isinstance(card, dict):
                    cards.append(card)
    return {
        str(card["id"]): card
        for card in cards
        if isinstance(card.get("id"), str)
        and str(card["id"]).startswith("canvas.node.")
    }


def _task_labels(path: Path) -> dict[str, str]:
    value = _assignment_value(_load_python(path), "_TASK_TYPE_LABELS")
    if not isinstance(value, dict):
        raise ParityError("_TASK_TYPE_LABELS must be a dictionary")
    return {str(key): str(label) for key, label in value.items()}


def _task_runners(root: Path) -> set[str]:
    registration = re.compile(r"register_project_task_runner\(\s*['\"]([^'\"]+)['\"]")
    runners: set[str] = set()
    runner_root = root / "src" / "novelvideo" / "task_backend" / "runners"
    for path in sorted(runner_root.glob("*.py")):
        try:
            runners.update(registration.findall(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError) as exc:
            raise ParityError(f"cannot read {path}: {exc}") from exc
    return runners


def _agent_ui_actions(path: Path) -> dict[str, str]:
    """Read the browser action fixed by each Agent node capability declaration."""

    declaration_paths = (
        sorted((path / "tools").glob("*.py")) if path.is_dir() else (path,)
    )
    actions: dict[str, str] = {}
    for declaration_path in declaration_paths:
        tree = _load_python(declaration_path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Name) and node.func.id == "ToolSpec"):
                continue
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            card_node = keywords.get("card")
            defaults_node = keywords.get("handler_defaults")
            if card_node is None or defaults_node is None:
                continue
            try:
                card = ast.literal_eval(card_node)
                defaults = ast.literal_eval(defaults_node)
            except (ValueError, TypeError, SyntaxError) as exc:
                raise ParityError(
                    f"ToolSpec bridge declaration in {declaration_path} is not literal"
                ) from exc
            if not isinstance(card, dict) or not isinstance(defaults, dict):
                continue
            capability_id = str(card.get("id") or "")
            action = defaults.get("action")
            if capability_id.startswith("canvas.node.") and isinstance(action, str):
                actions[capability_id] = action
    return actions


def validate(root: Path = REPO_ROOT) -> dict[str, Any]:
    root = root.resolve()
    frontend = _frontend_capabilities(
        root
        / "frontend"
        / "src"
        / "features"
        / "canvas"
        / "domain"
        / "nodeCapabilityCatalog.ts"
    )
    agent_path = root / "src" / "novelvideo" / "agent_tools" / "village_canvas"
    agent = _agent_capabilities(agent_path)
    agent_ui_actions = _agent_ui_actions(agent_path)
    labels = _task_labels(root / "src" / "novelvideo" / "api" / "routes" / "tasks.py")
    runners = _task_runners(root)
    errors: list[dict[str, str]] = []

    for frontend_id, agent_id in FRONTEND_TO_AGENT.items():
        ui = frontend.get(frontend_id)
        card = agent.get(agent_id)
        if ui is None:
            errors.append({"code": "frontend_missing", "frontend_id": frontend_id})
            continue
        if card is None:
            errors.append({"code": "agent_missing", "agent_id": agent_id})
            continue

        task_type = str(card.get("task_type") or "").strip()
        side_effect = str(card.get("side_effect") or "").strip()
        ui_action = agent_ui_actions.get(agent_id)
        if side_effect == "browser_ui":
            if ui_action is None:
                errors.append(
                    {"code": "ui_bridge_mapping_missing", "agent_id": agent_id}
                )
            elif ui["kind"] == "browser_ui" and ui["target"] != ui_action:
                errors.append(
                    {
                        "code": "ui_action_mismatch",
                        "frontend_id": frontend_id,
                        "expected": ui_action,
                        "actual": str(ui["target"]),
                    }
                )
            elif ui["kind"] == "canvas_event":
                expected_event = {
                    "open_tool_dialog": "tool-dialog/open",
                    "video_capture_frame": "video-node/capture-frame",
                    "video_set_operation": "video-node/set-operation",
                }.get(ui_action)
                if expected_event != ui["target"]:
                    errors.append(
                        {
                            "code": "ui_event_mismatch",
                            "frontend_id": frontend_id,
                            "expected": expected_event or "browser_ui",
                            "actual": str(ui["target"]),
                        }
                    )
            elif ui["kind"] == "async_task":
                errors.append(
                    {
                        "code": "browser_ui_declared_as_task",
                        "frontend_id": frontend_id,
                        "agent_id": agent_id,
                    }
                )
        if task_type:
            if ui["kind"] != "async_task":
                errors.append(
                    {
                        "code": "execution_kind_mismatch",
                        "frontend_id": frontend_id,
                        "expected": "async_task",
                        "actual": str(ui["kind"]),
                    }
                )
            elif ui["target"] != task_type:
                errors.append(
                    {
                        "code": "task_type_mismatch",
                        "frontend_id": frontend_id,
                        "expected": task_type,
                        "actual": str(ui["target"]),
                    }
                )
            if task_type not in runners:
                errors.append({"code": "runner_missing", "task_type": task_type})
            if task_type not in labels:
                errors.append({"code": "task_label_missing", "task_type": task_type})

    return {
        "schema": SCHEMA,
        "ok": not errors,
        "frontend_capabilities": len(frontend),
        "agent_capabilities": len(agent),
        "checked": len(FRONTEND_TO_AGENT),
        "task_runners": len(runners),
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args(argv)
    try:
        result = validate(args.root)
    except ParityError as exc:
        result = {
            "schema": SCHEMA,
            "ok": False,
            "errors": [{"code": "inspection_failed", "error": str(exc)}],
        }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
