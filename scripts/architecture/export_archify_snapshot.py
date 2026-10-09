"""Export a revision-pinned Archify architecture snapshot for the project.

The exporter is intentionally read-only with respect to the repository. It
collects only source paths, Git identity, and the public frontend build ID;
private project assets and runtime state are never traversed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "workspace" / "artifacts" / "architecture" / "village-canvas.architecture.json"
GITHUB_REPOSITORY = "https://github.com/mengxiangshu666/infinite-canvas"
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


def _run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown git error"
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def _canonical_repository(origin: str) -> str:
    value = origin.strip()
    if value.startswith("git@github.com:"):
        value = "https://github.com/" + value.split(":", 1)[1]
    elif value.startswith("ssh://git@github.com/"):
        value = "https://github.com/" + value.split("github.com/", 1)[1]
    if value.endswith("/"):
        value = value[:-1]
    if value.endswith(".git"):
        value = value[:-4]
    if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise RuntimeError(f"unsupported GitHub origin: {origin!r}")
    return value


def _read_frontend_version() -> dict[str, str]:
    path = REPO_ROOT / "frontend" / "dist" / "version.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"could not read frontend build identity: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("frontend version.json must contain an object")
    version = str(payload.get("version") or "").strip()
    build_id = str(payload.get("buildId") or "").strip()
    if not version or not build_id:
        raise RuntimeError("frontend version.json must contain version and buildId")
    return {"version": version, "buildId": build_id}


def _source(path: str, label: str) -> dict[str, str]:
    candidate = REPO_ROOT / Path(path)
    if not candidate.is_file():
        raise RuntimeError(f"source evidence path does not exist: {path}")
    return {"path": Path(path).as_posix(), "label": label}


def _component(
    component_id: str,
    component_type: str,
    label: str,
    sublabel: str,
    pos: tuple[int, int],
    source_path: str,
    source_label: str,
) -> dict[str, Any]:
    return {
        "id": component_id,
        "type": component_type,
        "label": label,
        "sublabel": sublabel,
        "pos": list(pos),
        "size": [170, 76],
        "sources": [_source(source_path, source_label)],
    }


def build_snapshot() -> dict[str, Any]:
    """Build an Archify architecture document from current repository facts."""

    origin = _run_git("config", "--get", "remote.origin.url") or GITHUB_REPOSITORY
    repository = _canonical_repository(origin)
    revision = _run_git("rev-parse", "HEAD").lower()
    if not REVISION_PATTERN.fullmatch(revision):
        raise RuntimeError(f"unexpected Git revision: {revision!r}")
    frontend = _read_frontend_version()
    display_version = frontend["version"]
    build_id = frontend["buildId"]

    components = [
        _component("frontend-canvas", "frontend", "XYFlow Canvas", "Interactive canvas UI", (20, 70), "frontend/src/stores/canvasStore.ts", "Canvas state"),
        _component("village-agent", "backend", "Village Agent", "Conversation + tools", (220, 70), "src/novelvideo/chat/village_harness.py", "Village Agent session"),
        _component("canvas-gateway", "backend", "Canvas Command Gateway", "Typed command boundary", (420, 70), "src/novelvideo/freezone/canvas_command_gateway.py", "Canvas command gateway"),
        _component("workflow-run", "backend", "WorkflowRun", "Durable runs + state", (620, 70), "src/novelvideo/workflow_runtime/service.py", "Workflow runtime"),
        _component("verifier", "security", "Verifier", "Evidence + quality gates", (820, 70), "src/novelvideo/workflow_runtime/verifier.py", "Workflow verifier"),
        _component("runtime-8784", "cloud", "Runtime :8784", "Packaged API boundary", (1020, 70), "src/novelvideo/api/wsgi.py", "API entrypoint"),
        _component("project-assets", "database", "Project Assets / State", "Projects + outputs", (20, 280), "src/novelvideo/services/project_resources.py", "Project resources"),
        _component("cognee", "backend", "Cognee Knowledge", "Indexed retrieval graph", (220, 280), "src/novelvideo/cognee/store.py", "Cognee store"),
        _component("growth-memory", "database", "Growth Memory", "Distilled experience", (420, 280), "src/novelvideo/chat/growth_distiller.py", "Growth distillation"),
        _component("prompt-compiler", "backend", "Prompt Compiler", "Model-aware contracts", (620, 280), "src/novelvideo/generators/prompt_builder.py", "Prompt builder"),
        _component("model-gateway", "backend", "Model Gateway", "Capability routing", (820, 280), "src/novelvideo/model_gateway_runtime.py", "Model gateway runtime"),
        _component("upstream-providers", "external", "Upstream Providers", "Image/video/audio/text", (1020, 280), "src/novelvideo/config.py", "Provider configuration"),
    ]

    connections = [
        {"id": "canvas-to-village-agent", "from": "frontend-canvas", "to": "village-agent", "label": "intent + state", "variant": "emphasis", "labelDy": -60},
        {"id": "village-agent-to-gateway", "from": "village-agent", "to": "canvas-gateway", "label": "typed commands", "variant": "emphasis", "labelDy": -60},
        {"id": "gateway-to-workflow", "from": "canvas-gateway", "to": "workflow-run", "label": "execution request", "variant": "emphasis", "labelDy": -60},
        {"id": "workflow-to-verifier", "from": "workflow-run", "to": "verifier", "label": "receipt + state", "variant": "security", "labelDy": -60},
        {"id": "workflow-to-model", "from": "workflow-run", "to": "model-gateway", "fromSide": "bottom", "toSide": "top"},
        {"id": "prompt-to-model", "from": "prompt-compiler", "to": "model-gateway", "label": "compiled contract", "labelDy": -60},
        {"id": "model-to-providers", "from": "model-gateway", "to": "upstream-providers"},
        {"id": "assets-to-canvas", "from": "project-assets", "to": "frontend-canvas", "fromSide": "top", "toSide": "bottom"},
        {"id": "assets-to-cognee", "from": "project-assets", "to": "cognee", "label": "ingest", "variant": "dashed", "labelDy": -60},
        {"id": "cognee-to-memory", "from": "cognee", "to": "growth-memory", "label": "retrieval + distill", "variant": "dashed", "labelDy": -60},
        {"id": "memory-to-village-agent", "from": "growth-memory", "to": "village-agent", "fromSide": "top", "toSide": "bottom", "variant": "dashed"},
    ]

    return {
        "schema_version": 1,
        "diagram_type": "architecture",
        "meta": {
            "title": "村长无限画布 · Agent 创作运行架构",
            "locale": "zh-CN",
            "subtitle": f"Product v{display_version} · Frontend build {build_id}",
            "visual_preset": "signal-flow",
            "animation": "trace",
            "quality_profile": "showcase",
            "repository": {"url": repository, "revision": revision},
            "viewBox": [1220, 520],
            "views": [
                {"id": "execution-path", "label": "执行主链", "focus": ["frontend-canvas", "village-agent", "canvas-gateway", "workflow-run", "verifier"], "note": "从画布意图到可验证执行回执。"},
                {"id": "knowledge-models", "label": "知识与模型", "focus": ["project-assets", "cognee", "growth-memory", "prompt-compiler", "model-gateway", "upstream-providers"], "note": "查看知识沉淀、提示词编译与上游模型路由。"},
                {"id": "runtime-boundary", "label": "运行边界", "focus": ["runtime-8784", "frontend-canvas", "project-assets", "workflow-run"], "note": "源码、运行服务与项目数据的边界。"},
            ],
        },
        "components": components,
        "connections": connections,
        "cards": [
            {"dot": "cyan", "title": "Verified source identity", "items": [f"Git revision {revision[:12]}", f"Frontend build {build_id}", "Every primary component carries repository-relative evidence"]},
            {"dot": "emerald", "title": "Deterministic execution", "items": ["Hermes delegates through Canvas Command Gateway", "WorkflowRun records state and receipts", "Verifier gates completion before delivery"]},
            {"dot": "violet", "title": "Knowledge-to-action loop", "items": ["Cognee indexes project knowledge", "Growth Memory stores distilled experience", "Prompt Compiler and Model Gateway preserve provider capabilities"]},
        ],
    }


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="output JSON path")
    args = parser.parse_args(argv)
    output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    snapshot = build_snapshot()
    _atomic_write(output.resolve(), snapshot)
    print(json.dumps({"output": str(output.resolve()), "revision": snapshot["meta"]["repository"]["revision"], "components": len(snapshot["components"]), "connections": len(snapshot["connections"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
