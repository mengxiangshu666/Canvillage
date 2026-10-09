"""Copy, normalize and map the three external product corpora.

The tool intentionally lives outside the runtime package. The product does not
depend on the reference drives; this command materializes a bounded source
snapshot under the ignored ``workspace/`` tree and emits the normalized facts
used by later integration work.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SNAPSHOT_SCHEMA = "village_reference_source_snapshot.v1"
CAPABILITY_LEDGER_SCHEMA = "village_reference_capability_ledger.v1"

_SOURCE_EXTENSIONS = frozenset(
    {
        ".cjs",
        ".csv",
        ".graphql",
        ".js",
        ".json",
        ".md",
        ".mjs",
        ".py",
        ".sql",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)
_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".next",
        ".pnpm-store",
        ".venv",
        "__pycache__",
        "coverage",
        "dist",
        "node_modules",
        "test-results",
    }
)
_CORPUS_PATHS: dict[str, tuple[str, ...]] = {
    "tapcanvas": (
        "apps/agents-cli",
        "apps/hono-api",
        "AI_RUNTIME_ARCHITECTURE.md",
        "LICENSE",
        "README.md",
    ),
    "libtv": (
        "00_入口",
        "10_规格",
        "20_语料",
        "30_证据",
        "40_前端源码",
    ),
    "oiioii": (
        "_EXTERNAL/connector",
        "cli_src",
        "fe_chunks/all",
        "agent_registry.json",
        "actor_taxonomy.json",
        "OIIOII_*.md",
        "MODEL_CATALOG_LIVE.md",
    ),
}

_TAPCANVAS_V90_RELATIVE = (
    "apps/hono-api/sql/releases/20260920_video_production_v90.sql"
)


@dataclass(frozen=True, slots=True)
class ReferenceCorpus:
    alias: str
    root: Path


@dataclass(frozen=True, slots=True)
class CopiedFile:
    relative_path: str
    bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "bytes": self.bytes,
            "sha256": self.sha256,
        }


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def load_corpus_registry(project_root: Path) -> dict[str, ReferenceCorpus]:
    """Read the alias-to-root mapping from the repository's registry."""

    registry_path = project_root / "docs" / "REFERENCE_CORPUS_REGISTRY.md"
    text = registry_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^\|\s*`(?P<alias>libtv|oiioii|tapcanvas)`\s*\|\s*`(?P<root>[^`]+)`",
        re.MULTILINE,
    )
    corpora: dict[str, ReferenceCorpus] = {}
    for match in pattern.finditer(text):
        alias = match.group("alias")
        corpora[alias] = ReferenceCorpus(
            alias=alias,
            root=Path(match.group("root")),
        )
    if not corpora:
        raise ValueError(f"No corpus aliases found in {registry_path}")
    return corpora


def _resolve_corpus_paths(
    root: Path,
    patterns: Iterable[str],
) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        matches = list(root.glob(pattern))
        if not matches and not any(char in pattern for char in "*?[]"):
            matches = [root / pattern]
        for match in matches:
            if not match.exists():
                continue
            resolved = match.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(match)
    return paths


def _iter_source_files(paths: Iterable[Path]) -> Iterable[tuple[Path, Path]]:
    """Yield ``(path, copy_root)`` for source-like files only."""

    for selected in paths:
        if selected.is_symlink():
            continue
        if selected.is_file():
            if selected.suffix.casefold() in _SOURCE_EXTENSIONS:
                yield selected, selected
            continue
        for child in sorted(selected.rglob("*")):
            if child.is_symlink() or not child.is_file():
                continue
            relative_parts = child.relative_to(selected).parts
            if any(part in _IGNORED_DIRS for part in relative_parts):
                continue
            if child.suffix.casefold() not in _SOURCE_EXTENSIONS:
                continue
            yield child, selected


def _snapshot_id(
    alias: str,
    source_files: list[tuple[Path, Path, str]],
) -> str:
    payload = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": sha,
        }
        for path, root, sha in source_files
    ]
    digest = _sha256_bytes(_canonical_json(payload).encode("utf-8"))[:16]
    return f"{alias}-{digest}"


def snapshot_corpus(
    corpus: ReferenceCorpus,
    *,
    output_root: Path,
) -> dict[str, Any]:
    """Copy one corpus's authoritative source surface into the local workspace."""

    if not corpus.root.is_dir():
        return {
            "schema": SNAPSHOT_SCHEMA,
            "alias": corpus.alias,
            "status": "unavailable",
            "root": str(corpus.root),
            "file_count": 0,
            "total_bytes": 0,
            "snapshot_id": "",
            "snapshot_root": "",
            "files": [],
        }
    selected_paths = _resolve_corpus_paths(
        corpus.root,
        _CORPUS_PATHS.get(corpus.alias, ()),
    )
    source_files: list[tuple[Path, Path, str]] = []
    for path, _selected_root in _iter_source_files(selected_paths):
        source_files.append((path, corpus.root, _sha256_file(path)))
    source_files.sort(key=lambda item: item[0].as_posix())
    snapshot_id = _snapshot_id(corpus.alias, source_files)
    snapshot_root = output_root / "sources" / corpus.alias / snapshot_id
    copied: list[CopiedFile] = []
    for path, copy_root, file_sha in source_files:
        relative = path.relative_to(copy_root)
        destination = snapshot_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.is_file() or _sha256_file(destination) != file_sha:
            shutil.copy2(path, destination)
        copied.append(
            CopiedFile(
                relative_path=relative.as_posix(),
                bytes=path.stat().st_size,
                sha256=file_sha,
            )
        )
    manifest = {
        "schema": SNAPSHOT_SCHEMA,
        "alias": corpus.alias,
        "status": "ready",
        "root_alias": corpus.alias,
        "snapshot_id": snapshot_id,
        "snapshot_root": snapshot_root.relative_to(output_root).as_posix(),
        "file_count": len(copied),
        "total_bytes": sum(item.bytes for item in copied),
        "aggregate_sha256": _sha256_bytes(
            _canonical_json([item.to_dict() for item in copied]).encode("utf-8")
        ),
        "files": [item.to_dict() for item in copied],
    }
    manifest_path = output_root / "sources" / corpus.alias / f"{snapshot_id}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _extract_sql_json(
    text: str,
    marker: str,
    *,
    opening_quote_offset: int = 1,
) -> Any:
    """Decode one SQL single-quoted JSON literal without guessing its terminator."""

    start = text.find(marker)
    if start < 0:
        raise ValueError(f"JSON marker not found: {marker}")
    start += opening_quote_offset
    decoded: list[str] = []
    index = start
    while index < len(text):
        char = text[index]
        if char != "'":
            decoded.append(char)
            index += 1
            continue
        if index + 1 < len(text) and text[index + 1] == "'":
            decoded.append("'")
            index += 2
            continue
        return json.loads("".join(decoded))
    raise ValueError(f"Unterminated SQL JSON literal after: {marker}")


def extract_tapcanvas_v90(sql_text: str) -> dict[str, Any]:
    """Parse the generated v90 SQL into a normalized graph and descriptors."""

    try:
        flow = _extract_sql_json(sql_text, '\'{"nodes":')
    except ValueError as exc:
        raise ValueError("TapCanvas v90 flow JSON not found") from exc
    nodes = flow.get("nodes") if isinstance(flow, dict) else None
    edges = flow.get("edges") if isinstance(flow, dict) else None
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("TapCanvas v90 flow is missing nodes/edges")

    stages: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        data = node.get("data")
        if not isinstance(data, dict) or not data.get("workflowNodeKind"):
            continue
        stages.append(
            {
                "node_id": str(node.get("id") or ""),
                "label": str(data.get("label") or ""),
                "operation": str(data.get("workflowNodeKind") or ""),
                "category": str(
                    (data.get("workflowAtomicSpec") or {}).get("category") or ""
                ),
                "executor_ref": str(
                    (data.get("workflowAtomicSpec") or {}).get("executorRef") or ""
                ),
                "execution_mode": str(
                    (data.get("workflowAtomicSpec") or {}).get("executionMode") or ""
                ),
                "input_ports": list(data.get("workflowInputPorts") or []),
                "output_ports": list(data.get("workflowOutputPorts") or []),
                "required_skills": list(data.get("workflowRequiredSkills") or []),
                "required_tools": list(data.get("workflowRequiredTools") or []),
                "output_artifact_type": str(
                    data.get("workflowOutputArtifactType") or ""
                ),
                "agent_output_artifact_type": str(
                    data.get("workflowAgentOutputArtifactType") or ""
                ),
                "instruction": str(data.get("workflowInstruction") or ""),
            }
        )

    descriptor = _extract_sql_json(
        sql_text,
        '\'{"protocolVersion":"tapcanvas.agent-capability/v1"',
    )
    operations = sorted(
        {
            str(operation)
            for operation in descriptor.get("operations") or []
            if str(operation)
        }
    )
    return {
        "schema": "tapcanvas_video_production_v90.v1",
        "workflow_key": "tapcanvas.builtin.video-production/v90",
        "node_count": int(descriptor.get("nodeCount") or len(nodes)),
        "operations": operations,
        "required_skills": list(descriptor.get("requiredSkills") or []),
        "required_tools": list(descriptor.get("requiredTools") or []),
        "input_artifacts": list(descriptor.get("inputArtifacts") or []),
        "output_artifacts": list(descriptor.get("outputArtifacts") or []),
        "trigger_fields": list(
            ((descriptor.get("invocation") or {}).get("requiredTriggerPayloadFields"))
            or []
        ),
        "stages": stages,
        "edges": [
            {
                "source": str(edge.get("source") or ""),
                "target": str(edge.get("target") or ""),
                "source_handle": str(edge.get("sourceHandle") or ""),
                "target_handle": str(edge.get("targetHandle") or ""),
            }
            for edge in edges
            if isinstance(edge, dict)
        ],
    }


def _read_snapshot_manifest(
    output_root: Path,
    alias: str,
) -> dict[str, Any] | None:
    manifests = sorted(
        (output_root / "sources" / alias).glob("*.json"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    )
    for path in manifests:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and value.get("status") == "ready":
            return value
    return None


def _snapshot_file(
    snapshot: dict[str, Any],
    output_root: Path,
    relative_path: str,
) -> Path:
    return (
        output_root
        / str(snapshot.get("snapshot_root") or "")
        / Path(relative_path)
    )


def _document_record(
    *,
    corpus: str,
    path: Path,
    snapshot_root: Path,
    capability_id: str,
) -> dict[str, Any]:
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    headings = [
        line.strip()
        for line in text.splitlines()
        if line.lstrip().startswith("#")
    ][:64]
    title = next((line.lstrip("# ").strip() for line in headings if line.strip("# ")), "")
    return {
        "schema": CAPABILITY_LEDGER_SCHEMA,
        "corpus": corpus,
        "capability_id": capability_id,
        "kind": "document",
        "label": title or path.stem,
        "source": path.relative_to(snapshot_root).as_posix(),
        "sha256": _sha256_bytes(raw),
        "bytes": len(raw),
        "headings": headings,
    }


_TAPCANVAS_NATIVE_BINDINGS: dict[str, dict[str, Any]] = {
    "asset_consumer_bind": {
        "status": "implemented",
        "native": ["src/novelvideo/workflow_runtime/canvas_asset_binding.py"],
        "note": "Canvas asset binding resolves exact object references before media.",
    },
    "asset_coverage": {
        "status": "implemented",
        "native": ["src/novelvideo/workflow_runtime/script_asset_ledger.py"],
        "note": "Asset ledger exposes required assets and readiness.",
    },
    "asset_fan_out": {
        "status": "partial",
        "native": ["src/novelvideo/workflow_runtime/media_dispatch.py"],
        "note": "Media dispatch is itemized, but asset fan-out is not its own durable step.",
    },
    "beat_sheet_assemble": {
        "status": "partial",
        "native": ["src/novelvideo/workflow_runtime/freezone_script.py"],
        "note": "Script rows and contract exist; a normalized chapter beat artifact does not.",
    },
    "beat_sheet_authoring": {
        "status": "partial",
        "native": ["src/novelvideo/workflow_runtime/freezone_script.py"],
        "note": "The structured script task authors shots, but not the full v90 beat schema.",
    },
    "blocking_background_split": {
        "status": "missing",
        "native": [],
        "note": "Scene background plans are not yet a first-class run artifact.",
    },
    "blocking_diagram_materialize": {
        "status": "missing",
        "native": [],
        "note": "Per-clip blocking diagrams are not yet materialized.",
    },
    "canvas_source": {
        "status": "implemented",
        "native": ["src/novelvideo/workflow_runtime/freezone_script.py"],
        "note": "The run reads the authoritative canvas revision and script node.",
    },
    "chapter_asset_authoring": {
        "status": "partial",
        "native": ["src/novelvideo/workflow_runtime/script_asset_ledger.py"],
        "note": "Identity and readiness are derived, but authored asset plans are not persisted.",
    },
    "chapter_asset_prepare": {
        "status": "partial",
        "native": ["src/novelvideo/workflow_runtime/freezone_asset_references.py"],
        "note": "Asset reference tasks exist; chapter-level planning is still implicit.",
    },
    "clip_design": {
        "status": "partial",
        "native": ["src/novelvideo/workflow_runtime/freezone_storyboard.py"],
        "note": "Storyboard images carry per-shot design but no explicit clip-design artifact.",
    },
    "clip_design_inputs": {
        "status": "partial",
        "native": ["src/novelvideo/workflow_runtime/freezone_storyboard.py"],
        "note": "Per-shot inputs are derived at dispatch time rather than frozen first.",
    },
    "clip_writer": {
        "status": "partial",
        "native": ["src/novelvideo/workflow_runtime/freezone_storyboard.py"],
        "note": "Image prompt compilation exists; v90 clip prompt governance is not yet separate.",
    },
    "concat": {
        "status": "implemented",
        "native": ["src/novelvideo/workflow_runtime/freezone_final_film.py"],
        "note": "Final film composition is durable and resumable.",
    },
    "condition": {
        "status": "implemented",
        "native": ["src/novelvideo/workflow_runtime/definitions.py"],
        "note": "Workflow definitions and run_mode choose media or structure paths.",
    },
    "delivery_contract": {
        "status": "implemented",
        "native": [
            "src/novelvideo/production/film_production_contract.py",
            "src/novelvideo/workflow_runtime/director_inputs.py",
        ],
        "note": "Delivery level and production gates are frozen before media.",
    },
    "delivery_verify": {
        "status": "implemented",
        "native": [
            "src/novelvideo/workflow_runtime/final_film_qc.py",
            "src/novelvideo/workflow_runtime/release_readiness.py",
        ],
        "note": "Engineering QC and release readiness are explicit gates.",
    },
    "estimate": {
        "status": "implemented",
        "native": ["src/novelvideo/workflow_runtime/paid_media_budget.py"],
        "note": "Paid media starts are budgeted and authorization-bound.",
    },
    "fan_out": {
        "status": "implemented",
        "native": ["src/novelvideo/workflow_runtime/media_dispatch.py"],
        "note": "Itemized workflow execution is supported.",
    },
    "image_generate": {
        "status": "implemented",
        "native": [
            "src/novelvideo/workflow_runtime/freezone_storyboard.py",
            "src/novelvideo/workflow_runtime/media_dispatch.py",
        ],
        "note": "Image batches are submitted and reconciled through TaskBackend.",
    },
    "max_clip": {
        "status": "implemented",
        "native": ["src/novelvideo/workflow_runtime/freezone_storyboard.py"],
        "note": "Shot and batch bounds are enforced before dispatch.",
    },
    "production_handoff": {
        "status": "partial",
        "native": ["src/novelvideo/workflow_runtime/media_dispatch.py"],
        "note": "Dispatch consumes ready artifacts, but no single production-plan handoff artifact exists.",
    },
    "prompt_package": {
        "status": "partial",
        "native": ["src/novelvideo/workflow_runtime/freezone_storyboard.py"],
        "note": "Prompts are attached to jobs; a typed aggregate package is not persisted.",
    },
    "text_expansion": {
        "status": "partial",
        "native": ["src/novelvideo/workflow_runtime/freezone_script.py"],
        "note": "Script generation can rewrite source text, but optional lossless expansion is not isolated.",
    },
    "video_prepare": {
        "status": "implemented",
        "native": ["src/novelvideo/workflow_runtime/freezone_videos.py"],
        "note": "Per-shot video preparation consumes first-frame and prompt receipts.",
    },
    "video_result": {
        "status": "implemented",
        "native": ["src/novelvideo/workflow_runtime/freezone_videos.py"],
        "note": "Video outputs are reconciled into durable artifacts.",
    },
    "video_submission": {
        "status": "implemented",
        "native": ["src/novelvideo/workflow_runtime/media_dispatch.py"],
        "note": "Paid video submission is server-owned and idempotent.",
    },
    "voice_manifest_empty": {
        "status": "implemented",
        "native": ["src/novelvideo/production/director_intent.py"],
        "note": "Native-audio and no-dialogue output are represented by the delivery contract.",
    },
}

_DOCUMENT_NATIVE_BINDINGS: dict[str, dict[str, Any]] = {
    "libtv.agent_protocol": {
        "status": "implemented",
        "native": ["src/novelvideo/chat/village_harness.py"],
    },
    "libtv.agent_behavior": {
        "status": "partial",
        "native": ["agent_skills/village-canvas-one-click-film/SKILL.md"],
    },
    "libtv.skill_system": {
        "status": "implemented",
        "native": ["src/novelvideo/agent_tools/skills.py"],
    },
    "libtv.prompt_factory": {
        "status": "implemented",
        "native": ["agent_skills/village-canvas-prompt-director/SKILL.md"],
    },
    "libtv.asset_system": {
        "status": "implemented",
        "native": ["src/novelvideo/workflow_runtime/script_asset_ledger.py"],
    },
    "libtv.ref_protocol": {
        "status": "implemented",
        "native": ["src/novelvideo/workflow_runtime/reference_resolver.py"],
    },
    "oiioii.skill_protocol": {
        "status": "implemented",
        "native": ["src/novelvideo/agent_tools/skills.py"],
    },
    "oiioii.asset_grant": {
        "status": "partial",
        "native": ["src/novelvideo/workflow_runtime/canvas_asset_binding.py"],
    },
    "oiioii.agent_deep_arch": {
        "status": "partial",
        "native": ["src/novelvideo/chat/agent_fleet.py"],
    },
}

_DOCUMENT_KEYS: dict[str, tuple[str, tuple[str, ...]]] = {
    "libtv": (
        "libtv",
        (
            "DISTILL/05_AGENT_PROTOCOL_SPEC.md",
            "DISTILL/06_AGENT_BEHAVIOR_SPEC.md",
            "DISTILL/07_SKILL_SYSTEM_SPEC.md",
            "DISTILL/08_PROMPT_FACTORY_SPEC.md",
            "DISTILL/11_ASSET_SYSTEM_SPEC.md",
            "DISTILL/12_PROMPT_REFERENCE_PROTOCOL.md",
        ),
    ),
    "oiioii": (
        "oiioii",
        (
            "OIIOII_AGENT_DEEP_ARCH.md",
            "OIIOII_ASSET_GRANT_SYSTEM.md",
            "OIIOII_SKILL_AGENT_PROTOCOL.md",
        ),
    ),
}


def native_capability_map(
    *,
    tapcanvas_v90: dict[str, Any],
    document_capabilities: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Map reference capabilities onto native Village implementations."""

    mapped: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for operation in tapcanvas_v90.get("operations") or []:
        binding = _TAPCANVAS_NATIVE_BINDINGS.get(str(operation))
        if binding is None:
            unresolved.append(
                {
                    "corpus": "tapcanvas",
                    "capability_id": f"tapcanvas.v90.{operation}",
                    "reason": "no_native_binding",
                }
            )
            continue
        mapped.append(
            {
                "corpus": "tapcanvas",
                "capability_id": f"tapcanvas.v90.{operation}",
                **binding,
            }
        )
    for document in document_capabilities:
        capability_id = str(document.get("capability_id") or "")
        binding = _DOCUMENT_NATIVE_BINDINGS.get(capability_id)
        if binding is None:
            continue
        mapped.append(
            {
                "corpus": str(document.get("corpus") or ""),
                "capability_id": capability_id,
                **binding,
            }
        )
    status_counts = Counter(str(item.get("status") or "unknown") for item in mapped)
    return {
        "schema": "village_reference_native_capability_map.v1",
        "tapcanvas_v90_node_count": int(tapcanvas_v90.get("node_count") or 0),
        "tapcanvas_v90_operation_count": len(tapcanvas_v90.get("operations") or []),
        "mapped_count": len(mapped),
        "status_counts": dict(sorted(status_counts.items())),
        "bindings": mapped,
        "unresolved": unresolved,
    }


def _document_capabilities(
    *,
    snapshots: dict[str, dict[str, Any]],
    output_root: Path,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for corpus, (_alias, relative_paths) in _DOCUMENT_KEYS.items():
        snapshot = snapshots.get(corpus)
        if not snapshot or snapshot.get("status") != "ready":
            continue
        snapshot_root = output_root / str(snapshot["snapshot_root"])
        for relative in relative_paths:
            path = _snapshot_file(snapshot, output_root, relative)
            if not path.is_file():
                continue
            key = Path(relative).stem.casefold().replace("_", "").replace(".md", "")
            if relative.startswith("DISTILL/"):
                suffix = Path(relative).stem.split("_", 1)[-1].casefold()
                if "agent" in suffix and "protocol" in suffix:
                    capability_id = "libtv.agent_protocol"
                elif "agent" in suffix and "behavior" in suffix:
                    capability_id = "libtv.agent_behavior"
                elif "skill" in suffix:
                    capability_id = "libtv.skill_system"
                elif "prompt" in suffix and "factory" in suffix:
                    capability_id = "libtv.prompt_factory"
                elif "asset" in suffix:
                    capability_id = "libtv.asset_system"
                elif "reference" in suffix:
                    capability_id = "libtv.ref_protocol"
                else:
                    capability_id = f"libtv.{key}"
            elif "asset" in relative.casefold():
                capability_id = "oiioii.asset_grant"
            elif "skill" in relative.casefold():
                capability_id = "oiioii.skill_protocol"
            else:
                capability_id = "oiioii.agent_deep_arch"
            records.append(
                _document_record(
                    corpus=corpus,
                    path=path,
                    snapshot_root=snapshot_root,
                    capability_id=capability_id,
                )
            )
    return records


def build_reference_distillation(
    *,
    project_root: Path,
    output_root: Path,
    corpora: Iterable[str] = ("tapcanvas", "libtv", "oiioii"),
) -> dict[str, Any]:
    """Copy selected source surfaces, normalize them and emit a native map."""

    registry = load_corpus_registry(project_root)
    selected = tuple(dict.fromkeys(str(item) for item in corpora))
    unknown = [alias for alias in selected if alias not in registry]
    if unknown:
        raise ValueError(f"Unknown corpus aliases: {', '.join(unknown)}")
    output_root.mkdir(parents=True, exist_ok=True)
    snapshots = {
        alias: snapshot_corpus(registry[alias], output_root=output_root)
        for alias in selected
    }

    tapcanvas = snapshots.get("tapcanvas")
    if not tapcanvas or tapcanvas.get("status") != "ready":
        raise ValueError("tapcanvas snapshot is required to extract video production v90")
    v90_path = _snapshot_file(tapcanvas, output_root, _TAPCANVAS_V90_RELATIVE)
    if not v90_path.is_file():
        raise ValueError(f"TapCanvas v90 SQL is missing from snapshot: {v90_path}")
    tapcanvas_v90 = extract_tapcanvas_v90(v90_path.read_text(encoding="utf-8"))
    documents = _document_capabilities(
        snapshots=snapshots,
        output_root=output_root,
    )
    capability_records: list[dict[str, Any]] = [
        {
            "schema": CAPABILITY_LEDGER_SCHEMA,
            "corpus": "tapcanvas",
            "capability_id": "tapcanvas.video_production_v90",
            "kind": "workflow",
            "label": "TapCanvas v90 video production",
            **tapcanvas_v90,
        },
        *documents,
    ]
    ledger_path = output_root / "capabilities.jsonl"
    ledger_path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in capability_records
        ),
        encoding="utf-8",
    )
    native_map = native_capability_map(
        tapcanvas_v90=tapcanvas_v90,
        document_capabilities=documents,
    )
    native_map_path = output_root / "native_capability_map.json"
    native_map_path.write_text(
        json.dumps(native_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = {
        "schema": "village_reference_distillation.v1",
        "output_root": str(output_root),
        "snapshots": snapshots,
        "tapcanvas_v90": {
            "node_count": tapcanvas_v90["node_count"],
            "operation_count": len(tapcanvas_v90["operations"]),
            "required_skills": tapcanvas_v90["required_skills"],
            "required_tools": tapcanvas_v90["required_tools"],
        },
        "capability_ledger": {
            "schema": CAPABILITY_LEDGER_SCHEMA,
            "path": ledger_path.relative_to(output_root).as_posix(),
            "record_count": len(capability_records),
        },
        "native_map": {
            "path": native_map_path.relative_to(output_root).as_posix(),
            **native_map,
        },
    }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


__all__ = [
    "CAPABILITY_LEDGER_SCHEMA",
    "SNAPSHOT_SCHEMA",
    "ReferenceCorpus",
    "build_reference_distillation",
    "extract_tapcanvas_v90",
    "load_corpus_registry",
    "native_capability_map",
    "snapshot_corpus",
]
