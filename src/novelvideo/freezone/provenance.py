"""Project-local provenance for generated image, video, and audio assets.

The Freezone JSONL history remains the user-facing version log.  This module
adds a queryable, idempotent SQLite projection for audit and lineage without
making media generation depend on provenance availability.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from novelvideo.sqlite_pragmas import configure_sqlite_connection

_TABLE = "asset_provenance"
_SUCCESS_STATUSES = frozenset({"completed", "done", "success", "succeeded"})
_MEDIA_TYPES = frozenset({"image", "video", "audio"})
_PROJECT_TASK_KEY_RE = re.compile(r"^task:[^:]+:project:([^:]+):")
_PROJECT_MEDIA_URL_RE = re.compile(r"^/api/v1/projects/([^/]+)/media/")
_PROJECT_STATIC_URL_RE = re.compile(r"^/static/projects/([^/]+)/")
_HASH_CHUNK_SIZE = 1024 * 1024
_REDACTED = "[REDACTED]"
_SECRET_KEY_PARTS = (
    "apikey",
    "accesstoken",
    "refreshtoken",
    "authorization",
    "bearer",
    "credential",
    "password",
    "privatekey",
    "secret",
    "sessioncookie",
    "signature",
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:^|\s)(?:bearer\s+\S+|sk-[a-z0-9_-]{12,})(?:$|\s)"
)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS asset_provenance (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    project_id TEXT NOT NULL DEFAULT '',
    canvas_id TEXT NOT NULL DEFAULT '',
    node_id TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    job_id TEXT NOT NULL DEFAULT '',
    task_type TEXT NOT NULL DEFAULT '',
    media_type TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    prompt_sha256 TEXT NOT NULL DEFAULT '',
    prompt_ref TEXT NOT NULL DEFAULT '',
    parameters_json TEXT NOT NULL DEFAULT '{}',
    seed TEXT NOT NULL DEFAULT '',
    parent_assets_json TEXT NOT NULL DEFAULT '[]',
    output_path TEXT NOT NULL DEFAULT '',
    output_url TEXT NOT NULL DEFAULT '',
    output_sha256 TEXT NOT NULL DEFAULT '',
    usage_json TEXT NOT NULL DEFAULT '{}',
    cost_json TEXT NOT NULL DEFAULT '{}',
    quality_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
)
"""

_COLUMN_DEFINITIONS = {
    "id": "TEXT NOT NULL DEFAULT ''",
    "idempotency_key": "TEXT NOT NULL DEFAULT ''",
    "project_id": "TEXT NOT NULL DEFAULT ''",
    "canvas_id": "TEXT NOT NULL DEFAULT ''",
    "node_id": "TEXT NOT NULL DEFAULT ''",
    "task_id": "TEXT NOT NULL DEFAULT ''",
    "job_id": "TEXT NOT NULL DEFAULT ''",
    "task_type": "TEXT NOT NULL DEFAULT ''",
    "media_type": "TEXT NOT NULL DEFAULT ''",
    "model": "TEXT NOT NULL DEFAULT ''",
    "provider": "TEXT NOT NULL DEFAULT ''",
    "channel": "TEXT NOT NULL DEFAULT ''",
    "prompt_sha256": "TEXT NOT NULL DEFAULT ''",
    "prompt_ref": "TEXT NOT NULL DEFAULT ''",
    "parameters_json": "TEXT NOT NULL DEFAULT '{}'",
    "seed": "TEXT NOT NULL DEFAULT ''",
    "parent_assets_json": "TEXT NOT NULL DEFAULT '[]'",
    "output_path": "TEXT NOT NULL DEFAULT ''",
    "output_url": "TEXT NOT NULL DEFAULT ''",
    "output_sha256": "TEXT NOT NULL DEFAULT ''",
    "usage_json": "TEXT NOT NULL DEFAULT '{}'",
    "cost_json": "TEXT NOT NULL DEFAULT '{}'",
    "quality_json": "TEXT NOT NULL DEFAULT '{}'",
    "created_at": "TEXT NOT NULL DEFAULT ''",
    "updated_at": "TEXT NOT NULL DEFAULT ''",
}

_OUTPUT_PATH_KEYS = (
    "output_path",
    "image_path",
    "video_path",
    "audio_path",
    "file_path",
)
_OUTPUT_URL_KEYS = (
    "output_url",
    "image_url",
    "video_url",
    "audio_url",
    "url",
)
_OUTPUT_HASH_KEYS = (
    "output_sha256",
    "image_sha256",
    "video_sha256",
    "audio_sha256",
    "voice_sha256",
)
_PARENT_COLLECTION_KEYS = (
    "parent_assets",
    "parents",
    "references",
    "reference_paths",
    "reference_items",
)
_PARENT_PATH_KEYS = (
    "parent_path",
    "source_path",
    "base_path",
    "input_path",
    "reference_path",
    "first_frame_path",
    "last_frame_path",
    "mask_path",
)
_PARAMETER_KEYS = (
    "aspect_ratio",
    "image_size",
    "resolution",
    "duration",
    "duration_seconds",
    "fps",
    "steps",
    "guidance_scale",
    "strength",
    "gen_mode",
    "generate_audio",
    "human_review",
    "response_format",
    "output_format",
    "quality",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _first_text(sources: tuple[Mapping[str, Any], ...], keys: tuple[str, ...]) -> str:
    for source in sources:
        for key in keys:
            value = _text(source.get(key))
            if value:
                return value
    return ""


def _json_dump(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if value in (None, ""):
        return {}
    return {"value": value}


def _is_secret_key(key: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def _sanitize_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"}:
        return value
    hostname = parsed.hostname or ""
    if parsed.port:
        hostname = f"{hostname}:{parsed.port}"
    return urlunsplit((parsed.scheme.lower(), hostname, parsed.path, "", ""))


def _sanitize_value(value: object, *, key: object = "") -> object:
    """Remove credentials and signed URL material from durable provenance."""

    if _is_secret_key(key):
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(item_key): _sanitize_value(item_value, key=item_key)
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        text = value.strip()
        if _SECRET_VALUE_RE.search(text):
            return _REDACTED
        return _sanitize_url(text)
    return value


def _sanitize_label(value: object) -> str:
    sanitized = _sanitize_value(_text(value))
    return _text(sanitized)


def _first_value(sources: tuple[Mapping[str, Any], ...], key: str) -> object:
    for source in sources:
        if key in source and source[key] not in (None, ""):
            return source[key]
    return None


def get_asset_provenance_db_path(project_output_dir: str | Path) -> Path:
    """Resolve the project's state ``data.db`` with legacy-layout fallback."""

    from novelvideo import config

    project_output = Path(project_output_dir).resolve()
    output_root = Path(config.OUTPUT_DIR).resolve()
    state_root = Path(config.STATE_DIR).resolve()
    try:
        relative = project_output.relative_to(output_root)
    except ValueError:
        return (project_output / "data.db").resolve()
    if len(relative.parts) < 2:
        return (project_output / "data.db").resolve()

    from novelvideo.utils.project_paths import ProjectPaths

    username, project_name = relative.parts[:2]
    ProjectPaths(username, project_name).bootstrap_from_legacy_output()
    return (state_root / username / project_name / "data.db").resolve()


def sha256_file(path: str | Path) -> str:
    """Hash a local file without loading it all into memory."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _add_column(conn: sqlite3.Connection, name: str, definition: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {_TABLE} ADD COLUMN {name} {definition}")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise


def _ensure_unique_legacy_keys(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        f"SELECT rowid, id, idempotency_key FROM {_TABLE} ORDER BY rowid"
    ).fetchall()
    used_ids: set[str] = set()
    used_keys: set[str] = set()
    for rowid, raw_id, raw_key in rows:
        record_id = _text(raw_id) or f"legacy:{rowid}"
        if record_id in used_ids:
            record_id = f"{record_id}:legacy:{rowid}"
        used_ids.add(record_id)

        key = _text(raw_key) or f"legacy:{record_id}"
        if key in used_keys:
            key = f"{key}:legacy:{rowid}"
        used_keys.add(key)
        if record_id != _text(raw_id) or key != _text(raw_key):
            conn.execute(
                f"UPDATE {_TABLE} SET id = ?, idempotency_key = ? WHERE rowid = ?",
                (record_id, key, rowid),
            )


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_TABLE_SQL)
    existing = {
        str(row[1]) for row in conn.execute(f"PRAGMA table_info({_TABLE})").fetchall()
    }
    for name, definition in _COLUMN_DEFINITIONS.items():
        if name not in existing:
            _add_column(conn, name, definition)
    indexes = {
        str(row[1]) for row in conn.execute(f"PRAGMA index_list({_TABLE})").fetchall()
    }
    if "idx_asset_provenance_idempotency" not in indexes:
        _ensure_unique_legacy_keys(conn)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_asset_provenance_idempotency "
            "ON asset_provenance(idempotency_key)"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_asset_provenance_canvas "
        "ON asset_provenance(project_id, canvas_id, node_id, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_asset_provenance_job "
        "ON asset_provenance(job_id, created_at DESC)"
    )


@contextmanager
def _connect(project_output_dir: str | Path) -> Iterator[sqlite3.Connection]:
    db_path = get_asset_provenance_db_path(project_output_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        configure_sqlite_connection(conn)
        _ensure_schema(conn)
        conn.commit()
        yield conn
        conn.commit()
    finally:
        conn.close()


def _project_id(record: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    explicit = _first_text((record, result), ("project_id",))
    if explicit:
        return explicit
    task_key = _text(record.get("task_key"))
    match = _PROJECT_TASK_KEY_RE.match(task_key)
    if match:
        return unquote(match.group(1))
    output_url = _first_text((result, record), _OUTPUT_URL_KEYS)
    path = urlsplit(output_url).path
    for pattern in (_PROJECT_MEDIA_URL_RE, _PROJECT_STATIC_URL_RE):
        match = pattern.match(path)
        if match:
            return unquote(match.group(1))
    return ""


def _parameters(record: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    for source in (result, record):
        for key in ("params", "parameters", "generation_parameters"):
            value = source.get(key)
            if isinstance(value, Mapping):
                parameters.update(value)
        for key in _PARAMETER_KEYS:
            value = source.get(key)
            if value not in (None, "") and not isinstance(value, Mapping):
                parameters.setdefault(key, value)
    sanitized = _sanitize_value(parameters)
    return dict(sanitized) if isinstance(sanitized, Mapping) else {}


def _local_path(value: str, project_dir: Path) -> Path | None:
    text = value.strip()
    if not text or urlsplit(text).scheme in {"http", "https", "data"}:
        return None
    if text.startswith(("/api/v1/projects/", "/static/")):
        try:
            from novelvideo.freezone.paths import resolve_static_url_to_path

            return resolve_static_url_to_path(text, project_dir)
        except ValueError:
            return None
    path = Path(text)
    return path if path.is_absolute() else project_dir / path


def _hash_if_file(value: str, project_dir: Path) -> str:
    path = _local_path(value, project_dir)
    if path is None or not path.is_file():
        return ""
    return sha256_file(path)


def _parent_item(value: object, project_dir: Path) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        item = {str(key): item_value for key, item_value in value.items()}
        path = _first_text((item,), ("path", "local_path", "source_path"))
        url = _first_text((item,), ("url", "source_url"))
        if path:
            item["path"] = path
        if url:
            item["url"] = url
    elif isinstance(value, (str, Path)):
        path = _text(value)
        item = {"path": path} if path else {}
    else:
        return None
    if not item:
        return None

    path = _text(item.get("path"))
    existing_hash = _text(item.get("sha256"))
    if not existing_hash and path:
        existing_hash = _hash_if_file(path, project_dir)
    if existing_hash:
        item["sha256"] = existing_hash
    sanitized = _sanitize_value(item)
    return dict(sanitized) if isinstance(sanitized, Mapping) else None


def _parent_assets(
    record: Mapping[str, Any],
    result: Mapping[str, Any],
    project_dir: Path,
) -> list[dict[str, Any]]:
    values: list[object] = []
    for source in (record, result):
        for key in _PARENT_COLLECTION_KEYS:
            value = source.get(key)
            if isinstance(value, (list, tuple)):
                values.extend(value)
            elif value not in (None, ""):
                values.append(value)
        for key in _PARENT_PATH_KEYS:
            value = source.get(key)
            if value not in (None, ""):
                values.append({"path": value, "role": key.removesuffix("_path")})

    assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        item = _parent_item(value, project_dir)
        if item is None:
            continue
        identity = _json_dump(item)
        if identity in seen:
            continue
        seen.add(identity)
        assets.append(item)
    return assets


def _canonical_output_path(output_path: str, project_dir: Path) -> str:
    path = _local_path(output_path, project_dir)
    if path is None:
        return output_path
    return os.path.normcase(str(path.resolve(strict=False)))


def _canonical_output_url(output_url: str) -> str:
    parsed = urlsplit(output_url.strip())
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", "")
    )


def _idempotency_key(
    *,
    project_id: str,
    task_type: str,
    task_id: str,
    job_id: str,
    output_path: str,
    output_url: str,
    output_sha256: str,
    project_dir: Path,
) -> str:
    identity = "\0".join(
        (
            project_id,
            task_type,
            task_id,
            job_id,
            _canonical_output_path(output_path, project_dir),
            _canonical_output_url(output_url),
            output_sha256 if not (output_path or output_url) else "",
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _provenance_values(
    project_dir: Path,
    history_record: Mapping[str, Any],
    *,
    prompt_ref: str | None,
) -> dict[str, str] | None:
    status = _text(history_record.get("status")).lower()
    media_type = _text(history_record.get("media_type")).lower()
    if status not in _SUCCESS_STATUSES or media_type not in _MEDIA_TYPES:
        return None

    result = _mapping(history_record.get("result"))
    sources = (history_record, result)
    output_path = _first_text((result, history_record), _OUTPUT_PATH_KEYS)
    output_url = _sanitize_url(
        _first_text((result, history_record), _OUTPUT_URL_KEYS)
    )
    output_sha256 = _first_text((result, history_record), _OUTPUT_HASH_KEYS)
    if not output_sha256 and output_path:
        output_sha256 = _hash_if_file(output_path, project_dir)
    if not output_sha256 and output_url:
        output_sha256 = _hash_if_file(output_url, project_dir)
    if not (output_path or output_url or output_sha256):
        return None

    parameters = _parameters(history_record, result)
    seed_value = _first_value(sources, "seed")
    if seed_value in (None, ""):
        seed_value = parameters.get("seed")
    prompt = _text(history_record.get("prompt"))
    prompt_sha256 = _text(history_record.get("prompt_sha256"))
    if not prompt_sha256 and prompt:
        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    project_id = _project_id(history_record, result)
    task_type = _text(history_record.get("task_type"))
    task_id = _first_text(sources, ("task_id",))
    job_id = _first_text(sources, ("job_id",))
    key = _idempotency_key(
        project_id=project_id,
        task_type=task_type,
        task_id=task_id,
        job_id=job_id,
        output_path=output_path,
        output_url=output_url,
        output_sha256=output_sha256,
        project_dir=project_dir,
    )
    now = _utc_now()
    created_at = _first_text(sources, ("created_at", "recorded_at")) or now
    return {
        "id": key,
        "idempotency_key": key,
        "project_id": project_id,
        "canvas_id": _text(history_record.get("canvas_id")),
        "node_id": _text(history_record.get("node_id")),
        "task_id": task_id,
        "job_id": job_id,
        "task_type": task_type,
        "media_type": media_type,
        "model": _first_text(sources, ("model", "model_id", "model_name")),
        "provider": _sanitize_label(_first_text(sources, ("provider",))),
        "channel": _sanitize_label(_first_text(sources, ("channel",))),
        "prompt_sha256": prompt_sha256,
        "prompt_ref": _text(history_record.get("prompt_ref")) or _text(prompt_ref),
        "parameters_json": _json_dump(parameters),
        "seed": _text(seed_value),
        "parent_assets_json": _json_dump(
            _parent_assets(history_record, result, project_dir)
        ),
        "output_path": output_path,
        "output_url": output_url,
        "output_sha256": output_sha256,
        "usage_json": _json_dump(
            _sanitize_value(_json_object(_first_value(sources, "usage")))
        ),
        "cost_json": _json_dump(
            _sanitize_value(_json_object(_first_value(sources, "cost")))
        ),
        "quality_json": _json_dump(
            _sanitize_value(_json_object(_first_value(sources, "quality")))
        ),
        "created_at": created_at,
        "updated_at": now,
    }


def record_generation_provenance(
    *,
    project_dir: str | Path,
    history_record: Mapping[str, Any],
    prompt_ref: str | None = None,
) -> str | None:
    """Upsert one successful media result and return its deterministic row id."""

    project_path = Path(project_dir)
    values = _provenance_values(project_path, history_record, prompt_ref=prompt_ref)
    if values is None:
        return None
    columns = tuple(values)
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(
        f"{column} = excluded.{column}"
        for column in columns
        if column not in {"id", "idempotency_key", "created_at"}
    )
    with _connect(project_path) as conn:
        conn.execute(
            f"""
            INSERT INTO {_TABLE} ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(idempotency_key) DO UPDATE SET {updates}
            """,
            tuple(values[column] for column in columns),
        )
    return values["id"]


def read_asset_provenance(project_dir: str | Path) -> list[dict[str, Any]]:
    """Read normalized provenance rows, primarily for audit/export callers."""

    with _connect(project_dir) as conn:
        rows = conn.execute(
            f"SELECT * FROM {_TABLE} ORDER BY created_at ASC, rowid ASC"
        ).fetchall()
    records: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        for column, public_name, default in (
            ("parameters_json", "parameters", {}),
            ("parent_assets_json", "parent_assets", []),
            ("usage_json", "usage", {}),
            ("cost_json", "cost", {}),
            ("quality_json", "quality", {}),
        ):
            try:
                record[public_name] = json.loads(record.pop(column) or "null")
            except (TypeError, json.JSONDecodeError):
                record[public_name] = default
            if record[public_name] is None:
                record[public_name] = default
        records.append(record)
    return records
