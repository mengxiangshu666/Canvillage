"""Read-only state database endpoints.

These routes exist so the canvas Agent can answer cross-table questions about
the local project state (script rows without videos, paid starts without takes,
workflow runs that stopped early) by writing the query itself, instead of
waiting for a dedicated read route per question.

The surface is deliberately narrow and enforced in :mod:`novelvideo.state_sql`:
logical database names only, ``SELECT``/``WITH`` only, bounded rows and bytes,
and no access to the credential store.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import resolve_project_scope
from novelvideo.config import STATE_DIR
from novelvideo.state_sql import (
    DEFAULT_MAX_ROWS,
    StateSqlError,
    describe_database,
    list_databases,
    resolve_database_path,
    run_readonly_query,
)

router = APIRouter()

_MAX_SQL_CHARS = 20_000


class StateQueryRequest(BaseModel):
    """One bounded read query against one allowlisted state database."""

    database: str = Field(..., max_length=64)
    sql: str = Field(..., max_length=_MAX_SQL_CHARS)
    project_id: str = Field("", max_length=200)
    max_rows: int | None = Field(None, ge=1, le=1000)


def _installation_state_dir() -> Path:
    return Path(STATE_DIR)


async def _project_state_dir(project_id: str, user: dict) -> Path | None:
    project = str(project_id or "").strip()
    if not project:
        return None
    resolved = await resolve_project_scope(project, user, required_role="viewer")
    return Path(resolved.state_dir)


def _resolved_path(
    database: str,
    project_state_dir: Path | None,
) -> Path:
    path = resolve_database_path(
        database,
        project_state_dir=project_state_dir,
        installation_state_dir=_installation_state_dir(),
    )
    if path is None:
        raise StateSqlError("project_id is required for this database")
    return path


@router.get("/state/databases")
async def list_state_databases(
    project: str = Query("", max_length=200),
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """List the readable databases, their scope and whether they exist yet."""

    project_state_dir = await _project_state_dir(project, user)
    return {
        "ok": True,
        "databases": list_databases(
            project_state_dir=project_state_dir,
            installation_state_dir=_installation_state_dir(),
        ),
    }


@router.get("/state/schema")
async def read_state_schema(
    database: str = Query(..., max_length=64),
    table: str = Query("", max_length=200),
    project: str = Query("", max_length=200),
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Return tables, views and columns so callers stop guessing names.

    ``table`` narrows the answer to one named object and lifts the per-object
    column cap while doing it, so a table too wide for the whole-file listing
    can still be read in full instead of being unknowable.
    """

    project_state_dir = await _project_state_dir(project, user)
    try:
        path = _resolved_path(database, project_state_dir)
        return describe_database(path, table=table)
    except StateSqlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/state/query")
async def query_state_database(
    body: StateQueryRequest,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Run one read-only statement and return JSON-ready rows."""

    project_state_dir = await _project_state_dir(body.project_id, user)
    try:
        path = _resolved_path(body.database, project_state_dir)
        return run_readonly_query(
            path,
            body.sql,
            max_rows=DEFAULT_MAX_ROWS if body.max_rows is None else body.max_rows,
        )
    except StateSqlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
