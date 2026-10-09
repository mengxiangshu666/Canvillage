"""Project-local canonical production registry.

The registry is intentionally additive: it lives in the existing project
``data.db`` but owns only ``production_*`` tables. Existing character, scene,
prop, episode and beat tables remain authoritative during the migration.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

from novelvideo.sqlite_pragmas import configure_sqlite_connection_async

from .schemas import (
    CanvasProjectionCreate,
    ProductionEntityCreate,
    WorkVersionCreate,
)

PRODUCTION_SCHEMA_VERSION = "production-registry.v2"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS production_entities (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    display_name TEXT NOT NULL,
    source_kind TEXT NOT NULL DEFAULT 'native',
    source_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_production_entity_source
    ON production_entities(kind, source_kind, source_id)
    WHERE source_id IS NOT NULL AND source_id != '';
CREATE INDEX IF NOT EXISTS idx_production_entity_kind
    ON production_entities(kind, updated_at DESC);

CREATE TABLE IF NOT EXISTS production_work_versions (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES production_entities(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    artifact_url TEXT NOT NULL DEFAULT '',
    artifact_path TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    dependency_fingerprint TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE(entity_id, revision)
);
CREATE INDEX IF NOT EXISTS idx_production_version_entity
    ON production_work_versions(entity_id, revision DESC);

CREATE TABLE IF NOT EXISTS production_canvas_projections (
    id TEXT PRIMARY KEY,
    canvas_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    entity_id TEXT NOT NULL REFERENCES production_entities(id) ON DELETE CASCADE,
    version_id TEXT REFERENCES production_work_versions(id) ON DELETE SET NULL,
    role TEXT NOT NULL DEFAULT 'reference',
    last_seen_revision INTEGER NOT NULL DEFAULT 0,
    projection_revision INTEGER NOT NULL DEFAULT 1,
    local_overrides_json TEXT NOT NULL DEFAULT '{}',
    stale_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(canvas_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_production_projection_entity
    ON production_canvas_projections(entity_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS production_schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class RegistryConflictError(RuntimeError):
    def __init__(self, code: str, message: str, *, current_revision: int | None = None):
        super().__init__(message)
        self.code = code
        self.current_revision = current_revision


class RegistryNotFoundError(LookupError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode_json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _asset_item(value: dict[str, Any]) -> dict[str, Any]:
    media = str(value.get("media") or "image").strip().lower()
    if media not in {"image", "video", "audio"}:
        raise ValueError(f"unsupported asset media: {media}")
    item_id = str(value.get("id") or "").strip() or f"asset_{uuid.uuid4().hex}"
    name = str(value.get("name") or "").strip() or item_id
    image_urls = [
        str(url).strip()
        for url in value.get("image_urls") or []
        if str(url).strip()
    ]
    video_url = str(value.get("video_url") or "").strip()
    audio_url = str(value.get("audio_url") or "").strip()
    if media == "image" and not image_urls:
        fallback = str(value.get("url") or "").strip()
        if fallback:
            image_urls = [fallback]
    if media == "video" and not video_url:
        video_url = str(value.get("url") or "").strip()
    if media == "audio" and not audio_url:
        audio_url = str(value.get("url") or "").strip()
    return {
        "id": item_id,
        "name": name,
        "media": media,
        "source": str(value.get("source") or "upload").strip() or "upload",
        "image_urls": image_urls,
        "video_url": video_url,
        "audio_url": audio_url,
    }


def _asset_fingerprint(item: dict[str, Any]) -> str:
    return hashlib.sha256(_json(item).encode("utf-8")).hexdigest()


def _row_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for key in ("metadata_json", "local_overrides_json"):
        if key in result:
            result[key.removesuffix("_json")] = _decode_json(result.pop(key))
    return result


class ProductionRegistry:
    """Append-friendly production state shared by mainline and canvas routes."""

    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.db_path = self.state_dir / "data.db"

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self.db_path)
        db.row_factory = aiosqlite.Row
        await configure_sqlite_connection_async(db)
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(_SCHEMA_SQL)
        now = _utc_now()
        await db.execute(
            """
            INSERT INTO production_schema_meta(key, value, updated_at)
            VALUES('schema_version', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (PRODUCTION_SCHEMA_VERSION, now),
        )
        await db.commit()
        try:
            yield db
        finally:
            await db.close()

    async def initialize(self) -> None:
        async with self._connect():
            return

    async def list_asset_library_items(self) -> list[dict[str, Any]]:
        """Return the canonical multi-media asset library from Registry versions."""
        async with self._connect() as db:
            rows = await self._fetchall(
                db,
                """SELECT e.source_id, e.display_name, e.metadata_json,
                          v.artifact_url, v.metadata_json AS version_metadata_json
                   FROM production_entities e
                   LEFT JOIN production_work_versions v ON v.id=(
                       SELECT latest.id FROM production_work_versions latest
                       WHERE latest.entity_id=e.id
                       ORDER BY latest.revision DESC LIMIT 1
                   )
                   WHERE e.kind='asset' AND e.source_kind='freezone_asset_library'
                   ORDER BY e.updated_at DESC, e.id""",
            )
        items: list[dict[str, Any]] = []
        for row in rows:
            entity_meta = _decode_json(row["metadata_json"])
            version_meta = _decode_json(row["version_metadata_json"])
            media = str(version_meta.get("media") or entity_meta.get("media") or "image")
            artifact_url = str(row["artifact_url"] or "")
            item = _asset_item(
                {
                    "id": row["source_id"],
                    "name": row["display_name"],
                    "media": media,
                    "source": version_meta.get("source") or entity_meta.get("source"),
                    "image_urls": version_meta.get("image_urls")
                    or ([artifact_url] if media == "image" and artifact_url else []),
                    "video_url": version_meta.get("video_url")
                    or (artifact_url if media == "video" else ""),
                    "audio_url": version_meta.get("audio_url")
                    or (artifact_url if media == "audio" else ""),
                }
            )
            items.append(item)
        return items

    async def upsert_asset_library_item(self, value: dict[str, Any]) -> dict[str, Any]:
        item = _asset_item(value)
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            result = await self._upsert_asset_library_item(db, item)
            await db.commit()
        if result is None:
            raise AssertionError("direct asset upsert cannot be silently skipped")
        return result

    async def sync_asset_library_items(
        self, values: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            for value in values:
                await self._upsert_asset_library_item(
                    db, _asset_item(value), skip_tombstone=True
                )
            await db.commit()
        return await self.list_asset_library_items()

    async def migrate_legacy_asset_library(
        self, values: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Import the old JSON library once; deletion cannot resurrect old rows."""
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            marker = await self._fetchone(
                db,
                "SELECT value FROM production_schema_meta WHERE key='asset_library_migrated_v1'",
            )
            if not marker:
                for value in values:
                    await self._upsert_asset_library_item(
                        db, _asset_item(value), skip_tombstone=True
                    )
                now = _utc_now()
                await db.execute(
                    """INSERT INTO production_schema_meta(key, value, updated_at)
                       VALUES('asset_library_migrated_v1', '1', ?)
                       ON CONFLICT(key) DO UPDATE SET value='1', updated_at=excluded.updated_at""",
                    (now,),
                )
            await db.commit()
        return await self.list_asset_library_items()

    async def delete_asset_library_item(self, item_id: str) -> bool:
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            existing = await self._fetchone(
                db,
                """SELECT id FROM production_entities
                   WHERE kind='asset' AND source_kind='freezone_asset_library' AND source_id=?""",
                (item_id,),
            )
            if not existing:
                await db.rollback()
                return False
            await db.execute(
                """INSERT INTO production_schema_meta(key, value, updated_at)
                   VALUES(?, '1', ?)
                   ON CONFLICT(key) DO UPDATE SET value='1', updated_at=excluded.updated_at""",
                (f"asset_tombstone:{item_id}", _utc_now()),
            )
            cursor = await db.execute(
                "DELETE FROM production_entities WHERE id=?",
                (str(existing["id"]),),
            )
            await db.commit()
            return bool(cursor.rowcount)

    async def _upsert_asset_library_item(
        self,
        db: aiosqlite.Connection,
        item: dict[str, Any],
        *,
        skip_tombstone: bool = False,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        tombstone = await self._fetchone(
            db,
            "SELECT value FROM production_schema_meta WHERE key=?",
            (f"asset_tombstone:{item['id']}",),
        )
        if tombstone:
            if skip_tombstone:
                return None
            raise RegistryConflictError(
                "asset_tombstoned",
                f"asset '{item['id']}' was explicitly deleted; create a new id to restore it",
            )
        row = await self._fetchone(
            db,
            """SELECT * FROM production_entities
               WHERE kind='asset' AND source_kind='freezone_asset_library' AND source_id=?""",
            (item["id"],),
        )
        entity_meta = {"media": item["media"], "source": item["source"]}
        if row:
            entity_id = str(row["id"])
            await db.execute(
                """UPDATE production_entities
                   SET display_name=?, metadata_json=?, updated_at=? WHERE id=?""",
                (item["name"], _json(entity_meta), now, entity_id),
            )
        else:
            entity_id = _new_id("ent")
            await db.execute(
                """INSERT INTO production_entities(
                       id, kind, display_name, source_kind, source_id, metadata_json,
                       idempotency_key, created_at, updated_at
                   ) VALUES(?, 'asset', ?, 'freezone_asset_library', ?, ?, ?, ?, ?)""",
                (
                    entity_id,
                    item["name"],
                    item["id"],
                    _json(entity_meta),
                    f"asset-library:{item['id']}",
                    now,
                    now,
                ),
            )
        fingerprint = _asset_fingerprint(item)
        latest = await self._fetchone(
            db,
            """SELECT * FROM production_work_versions
               WHERE entity_id=? ORDER BY revision DESC LIMIT 1""",
            (entity_id,),
        )
        if not latest or str(latest["dependency_fingerprint"] or "") != fingerprint:
            revision = int(latest["revision"] if latest else 0) + 1
            artifact_url = (
                (item["image_urls"][0] if item["image_urls"] else "")
                if item["media"] == "image"
                else item["video_url"]
                if item["media"] == "video"
                else item["audio_url"]
            )
            await db.execute(
                """INSERT INTO production_work_versions(
                       id, entity_id, revision, status, artifact_url, artifact_path,
                       sha256, dependency_fingerprint, metadata_json, idempotency_key, created_at
                   ) VALUES(?, ?, ?, 'selected', ?, '', '', ?, ?, ?, ?)""",
                (
                    _new_id("ver"),
                    entity_id,
                    revision,
                    artifact_url,
                    fingerprint,
                    _json(item),
                    f"asset-library:{item['id']}:{fingerprint}",
                    now,
                ),
            )
        return item

    async def create_entity(self, payload: ProductionEntityCreate) -> dict[str, Any]:
        now = _utc_now()
        entity_id = _new_id("ent")
        source_id = payload.source_id or None
        idem = payload.idempotency_key or None
        async with self._connect() as db:
            if idem:
                row = await self._fetchone(
                    db,
                    "SELECT * FROM production_entities WHERE idempotency_key=?",
                    (idem,),
                )
                if row:
                    return _row_dict(row) or {}
            if source_id:
                row = await self._fetchone(
                    db,
                    """SELECT * FROM production_entities
                       WHERE kind=? AND source_kind=? AND source_id=?""",
                    (payload.kind, payload.source_kind, source_id),
                )
                if row:
                    return _row_dict(row) or {}
            try:
                await db.execute(
                    """INSERT INTO production_entities(
                           id, kind, display_name, source_kind, source_id, metadata_json,
                           idempotency_key, created_at, updated_at
                       ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        entity_id,
                        payload.kind,
                        payload.display_name,
                        payload.source_kind,
                        source_id,
                        _json(payload.metadata),
                        idem,
                        now,
                        now,
                    ),
                )
                await db.commit()
            except sqlite3.IntegrityError as exc:
                raise RegistryConflictError("entity_conflict", str(exc)) from exc
            return await self._require_entity(db, entity_id)

    async def list_entities(self, *, kind: str = "") -> list[dict[str, Any]]:
        async with self._connect() as db:
            params: tuple[Any, ...] = ()
            where = ""
            if kind:
                where = "WHERE e.kind=?"
                params = (kind,)
            rows = await self._fetchall(
                db,
                f"""SELECT e.*,
                           COALESCE(MAX(v.revision), 0) AS current_revision,
                           COUNT(v.id) AS version_count
                    FROM production_entities e
                    LEFT JOIN production_work_versions v ON v.entity_id=e.id
                    {where}
                    GROUP BY e.id
                    ORDER BY e.updated_at DESC, e.id""",
                params,
            )
            return [_row_dict(row) or {} for row in rows]

    async def get_entity(self, entity_id: str) -> dict[str, Any]:
        async with self._connect() as db:
            entity = await self._require_entity(db, entity_id)
            versions = await self._fetchall(
                db,
                """SELECT * FROM production_work_versions
                   WHERE entity_id=? ORDER BY revision DESC""",
                (entity_id,),
            )
            entity["versions"] = [_row_dict(row) or {} for row in versions]
            return entity

    async def create_work_version(
        self, entity_id: str, payload: WorkVersionCreate
    ) -> dict[str, Any]:
        async with self._connect() as db:
            await self._require_entity(db, entity_id)
            if payload.idempotency_key:
                row = await self._fetchone(
                    db,
                    "SELECT * FROM production_work_versions WHERE idempotency_key=?",
                    (payload.idempotency_key,),
                )
                if row:
                    if row["entity_id"] != entity_id:
                        raise RegistryConflictError(
                            "idempotency_conflict",
                            "idempotency key belongs to another entity",
                        )
                    return _row_dict(row) or {}
            row = await self._fetchone(
                db,
                "SELECT COALESCE(MAX(revision), 0) AS revision FROM production_work_versions WHERE entity_id=?",
                (entity_id,),
            )
            current = int(row["revision"] if row else 0)
            if (
                payload.expected_revision is not None
                and payload.expected_revision != current
            ):
                raise RegistryConflictError(
                    "revision_conflict",
                    f"expected entity revision {payload.expected_revision}, current is {current}",
                    current_revision=current,
                )
            version_id = _new_id("ver")
            revision = current + 1
            now = _utc_now()
            try:
                await db.execute(
                    """INSERT INTO production_work_versions(
                           id, entity_id, revision, status, artifact_url, artifact_path,
                           sha256, dependency_fingerprint, metadata_json, idempotency_key, created_at
                       ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        version_id,
                        entity_id,
                        revision,
                        payload.status,
                        payload.artifact_url,
                        payload.artifact_path,
                        payload.sha256,
                        payload.dependency_fingerprint,
                        _json(payload.metadata),
                        payload.idempotency_key or None,
                        now,
                    ),
                )
                await db.execute(
                    "UPDATE production_entities SET updated_at=? WHERE id=?",
                    (now, entity_id),
                )
                await self._mark_entity_projections_stale(db, entity_id, revision)
                await db.commit()
            except sqlite3.IntegrityError as exc:
                raise RegistryConflictError("version_conflict", str(exc)) from exc
            row = await self._fetchone(
                db, "SELECT * FROM production_work_versions WHERE id=?", (version_id,)
            )
            return _row_dict(row) or {}

    async def upsert_projection(
        self, payload: CanvasProjectionCreate
    ) -> dict[str, Any]:
        async with self._connect() as db:
            await self._require_entity(db, payload.entity_id)
            if payload.version_id:
                version = await self._fetchone(
                    db,
                    """SELECT * FROM production_work_versions
                       WHERE id=? AND entity_id=?""",
                    (payload.version_id, payload.entity_id),
                )
                if not version:
                    raise RegistryNotFoundError(
                        "projection work version not found for entity"
                    )
            current = await self._fetchone(
                db,
                """SELECT * FROM production_canvas_projections
                   WHERE canvas_id=? AND node_id=?""",
                (payload.canvas_id, payload.node_id),
            )
            current_revision = int(current["projection_revision"]) if current else 0
            if (
                payload.expected_projection_revision is not None
                and payload.expected_projection_revision != current_revision
            ):
                raise RegistryConflictError(
                    "projection_revision_conflict",
                    f"expected projection revision {payload.expected_projection_revision}, current is {current_revision}",
                    current_revision=current_revision,
                )
            now = _utc_now()
            projection_id = str(current["id"]) if current else _new_id("projection")
            next_revision = current_revision + 1
            entity_revision = await self._current_entity_revision(db, payload.entity_id)
            stale_reason = (
                f"entity_revision:{payload.last_seen_revision}->{entity_revision}"
                if payload.last_seen_revision < entity_revision
                else ""
            )
            await db.execute(
                """INSERT INTO production_canvas_projections(
                       id, canvas_id, node_id, entity_id, version_id, role,
                       last_seen_revision, projection_revision, local_overrides_json,
                       stale_reason, created_at, updated_at
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(canvas_id, node_id) DO UPDATE SET
                       entity_id=excluded.entity_id,
                       version_id=excluded.version_id,
                       role=excluded.role,
                       last_seen_revision=excluded.last_seen_revision,
                       projection_revision=excluded.projection_revision,
                       local_overrides_json=excluded.local_overrides_json,
                       stale_reason=excluded.stale_reason,
                       updated_at=excluded.updated_at""",
                (
                    projection_id,
                    payload.canvas_id,
                    payload.node_id,
                    payload.entity_id,
                    payload.version_id or None,
                    payload.role,
                    payload.last_seen_revision,
                    next_revision,
                    _json(payload.local_overrides),
                    stale_reason,
                    str(current["created_at"]) if current else now,
                    now,
                ),
            )
            await db.commit()
            row = await self._fetchone(
                db,
                "SELECT * FROM production_canvas_projections WHERE canvas_id=? AND node_id=?",
                (payload.canvas_id, payload.node_id),
            )
            result = _row_dict(row) or {}
            result["current_entity_revision"] = entity_revision
            result["stale"] = bool(result.get("stale_reason"))
            return result

    async def list_projections(
        self, *, canvas_id: str = "", entity_id: str = ""
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if canvas_id:
            clauses.append("p.canvas_id=?")
            params.append(canvas_id)
        if entity_id:
            clauses.append("p.entity_id=?")
            params.append(entity_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self._connect() as db:
            rows = await self._fetchall(
                db,
                f"""SELECT p.*,
                           COALESCE((SELECT MAX(v.revision) FROM production_work_versions v
                                     WHERE v.entity_id=p.entity_id), 0) AS current_entity_revision
                    FROM production_canvas_projections p
                    {where}
                    ORDER BY p.updated_at DESC, p.id""",
                tuple(params),
            )
            results = [_row_dict(row) or {} for row in rows]
            for result in results:
                result["stale"] = int(result.get("last_seen_revision") or 0) < int(
                    result.get("current_entity_revision") or 0
                )
            return results

    async def promotion_preview(
        self,
        *,
        canvas_id: str,
        node_id: str,
        target_entity_id: str = "",
        expected_entity_revision: int | None = None,
    ) -> dict[str, Any]:
        async with self._connect() as db:
            projection = await self._fetchone(
                db,
                """SELECT * FROM production_canvas_projections
                   WHERE canvas_id=? AND node_id=?""",
                (canvas_id, node_id),
            )
            source_entity_id = str(projection["entity_id"]) if projection else ""
            resolved_target = target_entity_id or source_entity_id
            current_revision = 0
            affected = 0
            if resolved_target:
                await self._require_entity(db, resolved_target)
                current_revision = await self._current_entity_revision(
                    db, resolved_target
                )
                row = await self._fetchone(
                    db,
                    "SELECT COUNT(*) AS count FROM production_canvas_projections WHERE entity_id=?",
                    (resolved_target,),
                )
                affected = int(row["count"] if row else 0)
            conflicts: list[dict[str, Any]] = []
            if (
                expected_entity_revision is not None
                and expected_entity_revision != current_revision
            ):
                conflicts.append(
                    {
                        "code": "entity_revision_conflict",
                        "expected_revision": expected_entity_revision,
                        "current_revision": current_revision,
                    }
                )
            return {
                "can_commit": not conflicts,
                "can_force_commit": bool(resolved_target),
                "force_hint": (
                    "omit expected_entity_revision to overwrite directly"
                    if conflicts
                    else "direct use is available"
                ),
                "source": {
                    "canvas_id": canvas_id,
                    "node_id": node_id,
                    "projection_exists": bool(projection),
                },
                "target_entity_id": resolved_target,
                "current_entity_revision": current_revision,
                "affected_canvas_projections": affected,
                "conflicts": conflicts,
                "actions": [
                    "use_directly",
                    "publish_directly",
                    "force_overwrite_without_expected_revision",
                ],
            }

    async def registry_counts(self) -> dict[str, int]:
        async with self._connect() as db:
            result: dict[str, int] = {}
            for key, table in (
                ("production_entities", "production_entities"),
                ("work_versions", "production_work_versions"),
                ("canvas_projections", "production_canvas_projections"),
            ):
                row = await self._fetchone(db, f"SELECT COUNT(*) AS count FROM {table}")
                result[key] = int(row["count"] if row else 0)
            row = await self._fetchone(
                db,
                "SELECT COUNT(*) AS count FROM production_canvas_projections WHERE stale_reason != ''",
            )
            result["stale_projections"] = int(row["count"] if row else 0)
            return result

    async def legacy_counts(self) -> dict[str, int]:
        async with self._connect() as db:
            result: dict[str, int] = {}
            for table in ("characters", "scenes", "props", "episodes", "beats"):
                exists = await self._fetchone(
                    db,
                    "SELECT 1 AS present FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                )
                if not exists:
                    result[table] = 0
                    continue
                row = await self._fetchone(db, f"SELECT COUNT(*) AS count FROM {table}")
                result[table] = int(row["count"] if row else 0)
            return result

    async def _require_entity(
        self, db: aiosqlite.Connection, entity_id: str
    ) -> dict[str, Any]:
        row = await self._fetchone(
            db, "SELECT * FROM production_entities WHERE id=?", (entity_id,)
        )
        if not row:
            raise RegistryNotFoundError(f"production entity '{entity_id}' not found")
        return _row_dict(row) or {}

    async def _current_entity_revision(
        self, db: aiosqlite.Connection, entity_id: str
    ) -> int:
        row = await self._fetchone(
            db,
            "SELECT COALESCE(MAX(revision), 0) AS revision FROM production_work_versions WHERE entity_id=?",
            (entity_id,),
        )
        return int(row["revision"] if row else 0)

    async def _mark_entity_projections_stale(
        self, db: aiosqlite.Connection, entity_id: str, revision: int
    ) -> None:
        await db.execute(
            """UPDATE production_canvas_projections
               SET stale_reason='entity_revision:' || last_seen_revision || '->' || ?,
                   updated_at=?
               WHERE entity_id=? AND last_seen_revision < ?""",
            (revision, _utc_now(), entity_id, revision),
        )

    @staticmethod
    async def _fetchone(
        db: aiosqlite.Connection, sql: str, params: tuple[Any, ...] = ()
    ) -> aiosqlite.Row | None:
        async with db.execute(sql, params) as cursor:
            return await cursor.fetchone()

    @staticmethod
    async def _fetchall(
        db: aiosqlite.Connection, sql: str, params: tuple[Any, ...] = ()
    ) -> list[aiosqlite.Row]:
        async with db.execute(sql, params) as cursor:
            return list(await cursor.fetchall())
