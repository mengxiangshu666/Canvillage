"""Per-node Freezone generation history storage.

The canvas graph is still owned by the frontend.  This module keeps a small
backend-side append-only history keyed by (canvas_id, node_id) so a frontend can
later recover completed/failed generation attempts without bloating canvas JSON.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from novelvideo.freezone.paths import CANVAS_ID_RE, freezone_root, resolve_static_url_to_path
from novelvideo.utils import thumbnails

_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_DEFAULT_LIMIT = 100
logger = logging.getLogger(__name__)

# Cap the prompt stored per history attempt. History is append-only JSONL read
# whole into memory, so an uncapped prompt would bloat disk + the read response
# on heavily-regenerated nodes. The frontend only needs enough to identify the
# preview. A full digest is retained before truncation for provenance.
MAX_HISTORY_PROMPT_CHARS = 4000


def build_node_history_record(
    *,
    task_type: str,
    job_id: str,
    task_key: str,
    status: str,
    media_type: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    prompt: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical per-node generation-history record.

    Single owner of the record schema so every producer (image/text/video
    runners, the API-route helper, 3GS) stays consistent — a new field is added
    here once instead of in each call site. ``prompt`` is the text that produced
    this version; it is capped (see ``MAX_HISTORY_PROMPT_CHARS``) and is the
    field the frontend reads to show each version's own prompt.
    """
    record: dict[str, Any] = {
        "id": f"{task_type}:{job_id}",
        "task_type": task_type,
        "task_key": task_key,
        "job_id": job_id,
        "status": status,
        "media_type": media_type,
        **(extra or {}),
        "result": copy.deepcopy(result) if result else None,
        "error": error,
    }
    prompt = str(prompt or "").strip()
    if prompt:
        record["prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        record["prompt"] = prompt[:MAX_HISTORY_PROMPT_CHARS]
    return record


def _safe_part(value: str) -> str:
    text = _SAFE_ID_RE.sub("_", str(value or "").strip()).strip("._-")
    return text[:128] or "unknown"


def generation_history_dir(project_dir: Path) -> Path:
    return freezone_root(project_dir) / "_generation_history"


def generation_history_path(project_dir: Path, canvas_id: str, node_id: str) -> Path:
    canvas = canvas_id.strip() or "default"
    if not CANVAS_ID_RE.match(canvas):
        raise ValueError(f"invalid canvas_id: {canvas_id!r}")
    node = _safe_part(node_id)
    return generation_history_dir(project_dir) / canvas / f"{node}.jsonl"


def append_generation_history(
    *,
    project_dir: Path,
    canvas_id: str | None,
    node_id: str | None,
    record: dict[str, Any],
) -> dict[str, Any] | None:
    """Append one generation attempt for a canvas node.

    Returns the normalized record, or None when no node_id is supplied.  Missing
    node_id means the caller is an older frontend or a non-node job.
    """

    if not node_id:
        return None
    normalized = {
        "schema_version": 1,
        "canvas_id": canvas_id or "default",
        "node_id": node_id,
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        **record,
    }
    path = generation_history_path(project_dir, normalized["canvas_id"], node_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(normalized, ensure_ascii=False, separators=(",", ":")) + "\n")
    try:
        from novelvideo.freezone import provenance

        relative_history_path = path.relative_to(project_dir).as_posix()
        record_id = str(normalized.get("id") or "").strip()
        prompt_ref = relative_history_path
        if record_id:
            prompt_ref = f"{prompt_ref}#{record_id}"
        provenance.record_generation_provenance(
            project_dir=project_dir,
            history_record=normalized,
            prompt_ref=prompt_ref,
        )
    except Exception:
        logger.warning("asset provenance write failed", exc_info=True)
    _prewarm_history_thumbnail(project_dir, normalized)
    return normalized


_IMAGE_OUTPUT_KEYS = ("output_url", "image_url", "master_url", "url")


def _prewarm_history_thumbnail(project_dir: Path, record: dict[str, Any]) -> int:
    """Queue one bounded image thumbnail after a successful history write."""
    if record.get("status") not in {"completed", "succeeded"} or record.get("media_type") != "image":
        return 0
    result = record.get("result")
    if not isinstance(result, dict):
        return 0
    try:
        for key in _IMAGE_OUTPUT_KEYS:
            value = result.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            source = resolve_static_url_to_path(value, project_dir)
            return thumbnails.prewarm(project_dir, source, ["thumb"])
    except (OSError, ValueError):
        return 0
    except Exception:
        logger.debug("thumbnail prewarm skipped for %s", record.get("id"), exc_info=True)
    return 0


def _read_history_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def read_generation_history(
    *,
    project_dir: Path,
    canvas_id: str,
    node_id: str,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    records = _read_history_file(generation_history_path(project_dir, canvas_id, node_id))
    if limit <= 0:
        return records
    return records[-limit:]


def read_canvas_generation_history(
    *,
    project_dir: Path,
    canvas_id: str,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Aggregate every node's generation history for a whole canvas.

    Reads all per-node JSONL files under the canvas history dir and merges them,
    newest first. Unlike the per-node read, this is *not* scoped to nodes still
    present on the canvas — a node deleted from the canvas keeps its history file,
    so its past attempts stay recoverable here. Malformed lines/files are skipped.
    """
    canvas = (canvas_id or "").strip() or "default"
    if not CANVAS_ID_RE.match(canvas):
        raise ValueError(f"invalid canvas_id: {canvas_id!r}")
    canvas_dir = generation_history_dir(project_dir) / canvas
    if not canvas_dir.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in canvas_dir.glob("*.jsonl"):
        records.extend(_read_history_file(path))
    # Newest first; records without a usable timestamp sort last (empty string).
    records.sort(key=lambda record: str(record.get("recorded_at") or ""), reverse=True)
    if limit <= 0:
        return records
    return records[:limit]
