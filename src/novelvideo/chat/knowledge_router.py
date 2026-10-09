r"""Federated, read-only knowledge retrieval for the Village Canvas Agent.

The router keeps three evidence layers separate:

* durable project/user memory, ranked by the existing semantic memory index;
* the checked-in local knowledge corpus under ``D:\codex cli\knowledge``;
* the user's Obsidian vault, mounted read-only through a configurable path.

Notes are references, not executable instructions.  Every result carries its
source and a bounded snippet so the Agent can cite provenance without dumping
an entire vault into the prompt.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from novelvideo.chat.memory_index import build_knowledge_packet, get_memory
from novelvideo.chat.evidence import build_evidence_packet


_TEXT_SUFFIXES = {".md", ".markdown", ".txt"}
_SKIP_DIRS = {".git", ".obsidian", "node_modules", "__pycache__", ".venv"}
_SENSITIVE_NAME = re.compile(r"(?:token|secret|password|passwd|cookie|private[-_ ]?key|api[-_ ]?key)", re.I)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_WORD_RE = re.compile(r"[a-z0-9_:-]{2,}", re.I)
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
_TAG_RE = re.compile(r"(?<![\w])#[\w\-/\u3400-\u9fff]+")
_MAX_FILE_BYTES = 1_500_000
_MAX_FILES_PER_ROOT = 2_500
_MAX_RESULTS_PER_SOURCE = 8
_MAX_SNIPPET_CHARS = 900
_MAX_REFERENCE_CHARS = 50_000
_COGNEE_TIMEOUT_SECONDS = 2.0
_COGNEE_CLOSE_TIMEOUT_SECONDS = 0.5
_COGNEE_REFERENCE_TIMEOUT_SECONDS = 2.0
_COGNEE_EMPTY_GRAPH_MARKERS = (
    "nodataerror",
    "no data found in the system",
    "暂无相关数据",
    "请先运行 cognee-ingest",
    "datasetnotfounderror",
)
_COGNEE_SOFT_ERROR_MARKERS = ("timeouterror",)
_COGNEE_URI_RE = re.compile(
    r"^cognee://(?P<project>[^/]+)/chunk/(?P<chunk>[A-Za-z0-9_.:-]{1,200})$",
    re.IGNORECASE,
)


def _clean(value: object, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _tokens(value: object) -> set[str]:
    text = _clean(value, 2_000).casefold()
    tokens = set(_WORD_RE.findall(text))
    cjk = "".join(_CJK_RE.findall(text))
    tokens.update(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    if text:
        tokens.add(text)
    return {token for token in tokens if token}


def _knowledge_root() -> Path:
    configured = str(os.environ.get("VILLAGE_CANVAS_KNOWLEDGE_ROOT") or "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "docs"


def _vault_root() -> Path:
    configured = str(os.environ.get("VILLAGE_CANVAS_OBSIDIAN_VAULT") or "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "knowledge" / "obsidian"


def _iter_text_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return ()
    files: list[Path] = []
    stack = [root]
    while stack and len(files) < _MAX_FILES_PER_ROOT:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith(".") or entry.name in _SKIP_DIRS:
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False) and Path(entry.name).suffix.lower() in _TEXT_SUFFIXES:
                    if not _SENSITIVE_NAME.search(entry.name):
                        files.append(Path(entry.path))
            except OSError:
                continue
    return files


def _snippet(text: str, query_tokens: set[str]) -> str:
    compact = text.strip()
    if len(compact) <= _MAX_SNIPPET_CHARS:
        return compact
    lower = compact.casefold()
    positions = [lower.find(token.casefold()) for token in query_tokens if len(token) > 1]
    positions = [position for position in positions if position >= 0]
    start = max(0, min(positions) - 180) if positions else 0
    return compact[start : start + _MAX_SNIPPET_CHARS].strip()


def _is_empty_cognee_result(value: object) -> bool:
    """Treat an unbuilt project graph as an empty optional source, not a fault."""
    text = str(value or "").casefold()
    return bool(text) and any(marker in text for marker in _COGNEE_EMPTY_GRAPH_MARKERS)


def _is_soft_cognee_error(value: object) -> bool:
    """Keep optional graph latency visible without blocking unrelated writes."""
    text = str(value or "").casefold()
    return bool(text) and any(marker in text for marker in _COGNEE_SOFT_ERROR_MARKERS)


def _obsidian_metadata(text: str) -> dict[str, Any]:
    """Extract bounded Obsidian-native structure without evaluating plugins."""
    frontmatter: dict[str, str] = {}
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for line in lines[1:80]:
            if line.strip() == "---":
                break
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and value and len(frontmatter) < 16:
                frontmatter[key[:80]] = value[:240]
    links = sorted({match.strip() for match in _WIKILINK_RE.findall(text) if match.strip()})[:32]
    tags = {match.lstrip("#") for match in _TAG_RE.findall(text)}
    frontmatter_tags = frontmatter.get("tags", "")
    tags.update(
        item.strip().lstrip("#")
        for item in re.split(r"[,\[\]]", frontmatter_tags)
        if item.strip()
    )
    tags = sorted(tags)[:32]
    return {"frontmatter": frontmatter, "links": links, "tags": tags}


def _search_root(root: Path, query: str, source: str, limit: int) -> list[dict[str, Any]]:
    query_tokens = _tokens(query)
    if not query_tokens:
        return []
    ranked: list[tuple[float, Path, str]] = []
    for path in _iter_text_files(root):
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        haystack = f"{path.stem} {text}".casefold()
        matched = {token for token in query_tokens if token.casefold() in haystack}
        if not matched:
            continue
        filename_score = sum(1 for token in query_tokens if token.casefold() in path.stem.casefold())
        score = min(1.0, len(matched) / max(1, len(query_tokens)) * 0.78 + filename_score * 0.08)
        ranked.append((score, path, text))
    ranked.sort(key=lambda item: (-item[0], str(item[1])))
    results: list[dict[str, Any]] = []
    for score, path, text in ranked[: max(1, min(limit, _MAX_RESULTS_PER_SOURCE))]:
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            relative = path.name
        result: dict[str, Any] = {
                "source": source,
                "title": path.stem,
                "path": relative,
                "uri": f"{source}://{relative}",
                "score": round(score, 4),
                "snippet": _snippet(text, _tokens(query)),
                "provenance": "local_read_only_note",
        }
        if source == "obsidian":
            result.update(_obsidian_metadata(text))
        results.append(result)
    return results


def _reference_path(uri: object) -> tuple[str, Path, str]:
    """Resolve a search result URI inside one of the two read-only roots."""
    raw_uri = _clean(uri, 2_000)
    parsed = urlsplit(raw_uri)
    source = parsed.scheme.casefold()
    if source not in {"knowledge", "obsidian"}:
        raise ValueError("reference uri must use knowledge:// or obsidian://")
    if parsed.query or parsed.fragment:
        raise ValueError("reference uri must not contain a query or fragment")
    relative_uri = f"{parsed.netloc}{parsed.path}".replace("\\", "/")
    parts = [part for part in relative_uri.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError("reference path must stay within the configured root")
    if any("\x00" in part for part in parts):
        raise ValueError("reference path contains an invalid character")
    relative = "/".join(parts)
    root = (_knowledge_root() if source == "knowledge" else _vault_root()).resolve()
    target = (root / Path(*parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("reference path escapes the configured root") from exc
    if not target.is_file():
        raise FileNotFoundError(relative)
    if target.suffix.casefold() not in _TEXT_SUFFIXES:
        raise ValueError("only Markdown and text references can be loaded")
    if _SENSITIVE_NAME.search(target.name):
        raise ValueError("sensitive reference names are not loadable")
    if target.stat().st_size > _MAX_FILE_BYTES:
        raise ValueError("reference exceeds the maximum size")
    return source, target, relative


def _memory_evidence(record: object, project_id: object, *, limit: int = 4) -> list[dict[str, Any]]:
    try:
        metadata = json.loads(str(getattr(record, "metadata_json", "{}") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        metadata = {}
    raw_items = metadata.get("evidence") if isinstance(metadata, dict) else None
    clean_project = _clean(project_id, 256)
    results: list[dict[str, Any]] = []
    for item in list(raw_items or [])[-12:]:
        if not isinstance(item, dict):
            continue
        evidence_project = _clean(item.get("project"), 256)
        if evidence_project and evidence_project != clean_project:
            continue
        evidence_ref = _clean(item.get("ref") or item.get("evidence_ref"), 600)
        if not evidence_ref:
            continue
        results.append(
            {
                "evidence_ref": evidence_ref,
                "project_id": evidence_project,
                "task_id": _clean(item.get("task_id"), 256),
                "outcome": _clean(item.get("outcome"), 80),
                "notes": _clean(item.get("notes"), 500),
            }
        )
    return results[-max(1, min(int(limit), 8)) :]


def _load_memory_reference(
    uri: object,
    *,
    username: object,
    project_id: object,
) -> dict[str, Any]:
    parsed = urlsplit(_clean(uri, 2_000))
    if parsed.scheme.casefold() != "memory" or parsed.query or parsed.fragment:
        raise ValueError("memory reference must use a plain memory://<id> URI")
    raw_id = f"{parsed.netloc}{parsed.path}".strip("/")
    if not re.fullmatch(r"[1-9]\d{0,18}", raw_id):
        raise ValueError("memory reference id is invalid")
    clean_username = _clean(username, 256)
    if not clean_username:
        raise ValueError("memory reference requires an authenticated user")
    record = get_memory(clean_username, int(raw_id))
    clean_project = _clean(project_id, 256)
    if record is None or (
        record.scope_kind == "project" and record.scope_id != clean_project
    ):
        raise FileNotFoundError(raw_id)
    evidence = _memory_evidence(record, clean_project, limit=8)
    return {
        "schema": "knowledge.reference.v1",
        "uri": f"memory://{record.id}",
        "source": "memory",
        "title": record.kind or "memory",
        "content": _clean(record.content, _MAX_REFERENCE_CHARS),
        "text": _clean(record.content, _MAX_REFERENCE_CHARS),
        "project_id": clean_project,
        "scope_kind": record.scope_kind,
        "memory_id": record.id,
        "evidence_count": int(getattr(record, "evidence_count", 0) or 0),
        "evidence": evidence,
        "provenance": "durable_semantic_memory",
        "citation": f"memory:{record.id}",
        "truncated": len(record.content) > _MAX_REFERENCE_CHARS,
        "max_chars": _MAX_REFERENCE_CHARS,
    }


def _load_cognee_reference(
    uri: object,
    *,
    username: object,
    project_id: object,
) -> dict[str, Any]:
    """Load one project-scoped Cognee chunk without exposing source paths."""

    raw_uri = _clean(uri, 2_000)
    match = _COGNEE_URI_RE.fullmatch(raw_uri)
    if not match:
        raise ValueError("cognee reference must use cognee://<project>/chunk/<id>")
    uri_project = match.group("project")
    clean_username = _clean(username, 256)
    if not clean_username:
        raise ValueError("cognee reference requires an authenticated user")
    clean_project = _clean(project_id, 256)
    if not clean_project or uri_project != clean_project:
        raise ValueError("cognee reference project mismatch")

    async def _load() -> dict[str, Any] | None:
        from novelvideo.services.project_resources import make_cognee_store

        store = await asyncio.wait_for(
            make_cognee_store(clean_username, clean_project),
            timeout=_COGNEE_REFERENCE_TIMEOUT_SECONDS,
        )
        try:
            loader = getattr(store, "load_chunk_reference", None)
            if not callable(loader):
                return None
            return await asyncio.wait_for(
                loader(match.group("chunk")),
                timeout=_COGNEE_REFERENCE_TIMEOUT_SECONDS,
            )
        finally:
            close = getattr(store, "close", None)
            if callable(close):
                result = close()
                if hasattr(result, "__await__"):
                    await asyncio.wait_for(
                        result,
                        timeout=_COGNEE_CLOSE_TIMEOUT_SECONDS,
                    )

    # API callers invoke this function from asyncio.to_thread; keeping the
    # router synchronous also preserves the existing direct unit-test API.
    loaded = asyncio.run(_load())
    if not loaded:
        raise FileNotFoundError(match.group("chunk"))
    content = str(loaded.get("text") or "")[:_MAX_REFERENCE_CHARS]
    return {
        "schema": "knowledge.reference.v1",
        "uri": raw_uri,
        "source": "cognee",
        "title": str(loaded.get("document_name") or "Cognee chunk"),
        "content": content,
        "text": content,
        "project_id": clean_project,
        "chunk_id": match.group("chunk"),
        "provenance": (
            "project_cognee_chunk_document"
            if loaded.get("document_id")
            else "project_cognee_chunk"
        ),
        "citation": raw_uri,
        "truncated": len(str(loaded.get("text") or "")) > _MAX_REFERENCE_CHARS,
        "max_chars": _MAX_REFERENCE_CHARS,
        **{
            key: loaded[key]
            for key in (
                "document_id",
                "source_uri",
                "chunk_index",
                "relationship",
            )
            if loaded.get(key) not in (None, "")
        },
    }


def load_reference(
    uri: object,
    *,
    project_id: object = "",
    username: object = "",
) -> dict[str, Any]:
    """Load one bounded note, memory, or project-scoped Cognee chunk."""
    scheme = urlsplit(_clean(uri, 2_000)).scheme.casefold()
    if scheme == "memory":
        return _load_memory_reference(
            uri,
            username=username,
            project_id=project_id,
        )
    if scheme == "cognee":
        return _load_cognee_reference(
            uri,
            username=username,
            project_id=project_id,
        )
    source, path, relative = _reference_path(uri)
    text = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > _MAX_REFERENCE_CHARS
    content = text[:_MAX_REFERENCE_CHARS]
    canonical_uri = f"{source}://{relative}"
    metadata = _obsidian_metadata(text)
    return {
        "schema": "knowledge.reference.v1",
        "uri": canonical_uri,
        "source": source,
        "relative_path": relative,
        "title": path.stem,
        "content": content,
        "text": content,
        "frontmatter": metadata["frontmatter"],
        "links": metadata["links"],
        "tags": metadata["tags"],
        "provenance": "local_read_only_reference",
        "citation": f"{source}:{relative}",
        "project_id": _clean(project_id, 256),
        "truncated": truncated,
        "max_chars": _MAX_REFERENCE_CHARS,
    }


def _memory_results(
    packet: dict[str, Any],
    limit: int,
    *,
    project_id: object = "",
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for record in list(packet.get("records") or [])[: max(1, min(limit, _MAX_RESULTS_PER_SOURCE))]:
        result = {
                "source": "memory",
                "title": str(getattr(record, "kind", "memory") or "memory"),
                "path": str(getattr(record, "source_id", "") or ""),
                "uri": f"memory://{getattr(record, 'id', 0)}",
                "score": round(float(getattr(record, "confidence", 0.0) or 0.0), 4),
                "snippet": _clean(getattr(record, "content", ""), _MAX_SNIPPET_CHARS),
                "memory_id": int(getattr(record, "id", 0) or 0),
                "scope_kind": str(getattr(record, "scope_kind", "") or ""),
                "provenance": "durable_semantic_memory",
                "evidence_count": int(getattr(record, "evidence_count", 0) or 0),
            }
        evidence = _memory_evidence(record, project_id)
        if evidence:
            result["evidence"] = evidence
        results.append(result)
    return results


async def _search_cognee(
    username: str,
    project: str,
    query: str,
    limit: int,
) -> tuple[list[dict[str, Any]], str]:
    """Query the optional graph without holding up the other evidence layers."""
    try:
        from novelvideo.services.project_resources import make_cognee_store

        scoped_project = _clean(project, 256)
        store = await asyncio.wait_for(
            make_cognee_store(username, scoped_project),
            timeout=_COGNEE_TIMEOUT_SECONDS,
        )
        try:
            structured_search = getattr(store, "search_with_provenance", None)
            if callable(structured_search):
                raw = await asyncio.wait_for(
                    structured_search(query, mode="chunks", top_k=limit),
                    timeout=_COGNEE_TIMEOUT_SECONDS,
                )
            else:
                raw = await asyncio.wait_for(
                    store.search(query, mode="chunks", top_k=limit),
                    timeout=_COGNEE_TIMEOUT_SECONDS,
                )
        finally:
            close = getattr(store, "close", None)
            if close:
                closed = close()
                if hasattr(closed, "__await__"):
                    await asyncio.wait_for(closed, timeout=_COGNEE_CLOSE_TIMEOUT_SECONDS)
        if isinstance(raw, list) and any(isinstance(item, dict) for item in raw):
            rows = []
            for index, item in enumerate(raw[:limit], start=1):
                if not isinstance(item, dict):
                    continue
                snippet = _clean(
                    item.get("snippet") or item.get("text") or item.get("content"),
                    _MAX_SNIPPET_CHARS,
                )
                if not snippet:
                    continue
                chunk_id = _clean(item.get("chunk_id"), 200)
                source_uri = _clean(item.get("source_uri"), 2_000)
                document_name = _clean(item.get("document_name"), 500)
                row = {
                    "source": "cognee",
                    "title": document_name or _clean(item.get("title"), 300) or f"project_graph_{index}",
                    "path": source_uri or scoped_project,
                    # Keep the stable chunk URI as the loadable reference;
                    # source_uri remains metadata/citation, not a filesystem
                    # escape hatch for the Agent.
                    "uri": (
                        f"cognee://{scoped_project}/chunk/{chunk_id}"
                        if chunk_id
                        else source_uri or f"cognee://{scoped_project}/{index}"
                    ),
                    "snippet": snippet,
                    "provenance": _clean(
                        item.get("provenance") or "project_cognee_chunk",
                        160,
                    ),
                    "mode": _clean(item.get("mode") or "chunks", 80),
                }
                if source_uri:
                    row["source_uri"] = source_uri
                if document_name:
                    row["document_name"] = document_name
                if item.get("score") not in (None, ""):
                    row["score"] = item.get("score")
                for key in (
                    "chunk_id",
                    "document_id",
                    "dataset_id",
                    "dataset_name",
                    "chunk_index",
                    "relationship",
                ):
                    if item.get(key) not in (None, ""):
                        row[key] = item[key]
                rows.append(row)
            return rows, ""

        raw_text = str(raw or "").strip()
        if not raw_text or _is_empty_cognee_result(raw_text):
            return [], ""
        if raw_text.startswith("搜索出错"):
            return [], raw_text[:300]
        rows = []
        for index, fragment in enumerate(
            (line.strip() for line in raw_text.splitlines() if line.strip()),
            start=1,
        ):
            if index > limit:
                break
            rows.append(
                {
                    "source": "cognee",
                    "title": f"project_graph_{index}",
                    "path": scoped_project,
                    "uri": f"cognee://{scoped_project}/{index}",
                    "score": round(max(0.0, 0.9 - index * 0.03), 4),
                    "snippet": _clean(fragment, _MAX_SNIPPET_CHARS),
                    "provenance": "project_cognee_graph",
                    "mode": "chunks",
                }
            )
        return rows, ""
    except Exception as exc:  # noqa: BLE001 - optional graph must not block other sources
        return [], f"{type(exc).__name__}: {exc}"


def _dedupe_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse mirrored notes while retaining all source provenance."""
    priority = {"memory": 3, "obsidian": 2, "knowledge": 1}
    selected: dict[str, dict[str, Any]] = {}
    for item in results:
        fingerprint = re.sub(
            r"\s+",
            " ",
            f"{item.get('title', '')}\n{item.get('snippet', '')}".casefold(),
        ).strip()[:360]
        if not fingerprint:
            continue
        current = selected.get(fingerprint)
        if current is None:
            selected[fingerprint] = {**item, "source_aliases": [item.get("source")]}
            continue
        aliases = list(current.get("source_aliases") or [])
        source = item.get("source")
        if source not in aliases:
            aliases.append(source)
        if priority.get(str(source), 0) > priority.get(str(current.get("source")), 0):
            merged = {
                **item,
                "source_aliases": aliases,
                "provenance": "federated_deduplicated",
            }
            selected[fingerprint] = merged
        else:
            current["source_aliases"] = aliases
            current["provenance"] = "federated_deduplicated"
    return list(selected.values())


async def search_knowledge(
    username: str,
    project: str,
    query: object,
    *,
    sources: Iterable[str] = ("memory", "knowledge", "obsidian", "cognee"),
    limit: int = 8,
) -> dict[str, Any]:
    clean_query = _clean(query, 2_000)
    selected_sources = {
        str(source).strip().casefold()
        for source in sources
        if str(source).strip().casefold() in {"memory", "knowledge", "obsidian", "cognee"}
    }
    if not selected_sources:
        selected_sources = {"memory", "knowledge", "obsidian", "cognee"}
    bounded_limit = max(1, min(int(limit or 8), 20))
    results: list[dict[str, Any]] = []
    packet: dict[str, Any] = {
        "stage": "knowledge_search",
        "records": [],
        "memory_ids": [],
        "used_count": 0,
    }
    cognee_task = None
    if "cognee" in selected_sources and _clean(project, 256):
        cognee_task = asyncio.create_task(
            _search_cognee(username, _clean(project, 256), clean_query, bounded_limit)
        )
    if "memory" in selected_sources:
        packet = await build_knowledge_packet(username, project, clean_query)
        results.extend(
            _memory_results(packet, bounded_limit, project_id=project)
        )
    lexical_jobs: list[tuple[Path, str]] = []
    if "knowledge" in selected_sources:
        lexical_jobs.append((_knowledge_root(), "knowledge"))
    if "obsidian" in selected_sources:
        lexical_jobs.append((_vault_root(), "obsidian"))
    if lexical_jobs:
        lexical_results = await asyncio.gather(
            *(
                asyncio.to_thread(_search_root, root, clean_query, source, bounded_limit)
                for root, source in lexical_jobs
            )
        )
        for rows in lexical_results:
            results.extend(rows)
    cognee_error = ""
    cognee_warning = ""
    if cognee_task is not None:
        done, _pending = await asyncio.wait(
            {cognee_task},
            timeout=_COGNEE_TIMEOUT_SECONDS,
        )
        if done:
            cognee_results, cognee_error = cognee_task.result()
            results.extend(cognee_results)
        else:
            # Some third-party database calls delay cancellation while closing
            # a connection. Return the other sources on the deadline instead
            # of waiting for that cleanup path on the chat request.
            cognee_task.cancel()
            cognee_task.add_done_callback(
                lambda task: task.exception()
                if not task.cancelled()
                else None
            )
            cognee_error = f"TimeoutError: source exceeded {_COGNEE_TIMEOUT_SECONDS:.1f}s"
    if cognee_error and _is_soft_cognee_error(cognee_error):
        cognee_warning = cognee_error
        cognee_error = ""
    results = _dedupe_results(results)
    results.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("source") or ""), str(item.get("uri") or "")))
    evidence_packet = build_evidence_packet(
        clean_query,
        results[:bounded_limit],
        sources_requested=selected_sources,
        source_errors={"cognee": cognee_error} if cognee_error else {},
        source_warnings={"cognee": cognee_warning} if cognee_warning else {},
        retrieval={
            "memory": "semantic_plus_lexical_with_evidence" if "memory" in selected_sources else "disabled",
            "knowledge": "bounded_lexical_note_search" if "knowledge" in selected_sources else "disabled",
            "obsidian": "bounded_lexical_read_only_search" if "obsidian" in selected_sources else "disabled",
            "cognee": "project_graph_chunks" if "cognee" in selected_sources and project else "disabled",
        },
    )
    return {
        "schema": "knowledge.search.v1",
        "query": clean_query,
        "project_id": _clean(project, 256),
        "sources_requested": sorted(selected_sources),
        "sources_used": sorted({str(item.get("source") or "") for item in results}),
        "results": results[:bounded_limit],
        "count": min(len(results), bounded_limit),
        "memory_ids": list(packet.get("memory_ids") or []),
        "memory_stage": str(packet.get("stage") or ""),
        "retrieval": {
            "memory": "semantic_plus_lexical_with_evidence" if "memory" in selected_sources else "disabled",
            "knowledge": "bounded_lexical_note_search" if "knowledge" in selected_sources else "disabled",
            "obsidian": "bounded_lexical_read_only_search" if "obsidian" in selected_sources else "disabled",
            "cognee": "project_graph_chunks" if "cognee" in selected_sources and project else "disabled",
            "instructions_in_notes": "reference_only",
        },
        "source_errors": ({"cognee": cognee_error} if cognee_error else {}),
        "source_warnings": ({"cognee": cognee_warning} if cognee_warning else {}),
        "evidence_packet": evidence_packet,
    }


__all__ = ["load_reference", "search_knowledge"]
