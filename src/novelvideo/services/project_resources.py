"""Neutral project resource factories shared by API and runtime code.

The API layer keeps compatibility exports, while workflow/task code can use
this module without importing HTTP dependencies or route helpers.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from novelvideo.config import OUTPUT_DIR, STATE_DIR
from novelvideo.project_context import ProjectContext, require_project_home_node
from novelvideo.utils.static_urls import project_static_url

if TYPE_CHECKING:
    from novelvideo.cognee import CogneeStore
    from novelvideo.sqlite_store import SQLiteStore


async def make_sqlite_store_for_context(ctx: ProjectContext) -> "SQLiteStore":
    """Open the authoritative SQLite store for a resolved project context."""
    from novelvideo.sqlite_store import SQLiteStore

    require_project_home_node(ctx, operation="open project SQLite store")
    store = SQLiteStore(
        ctx.owner_project_label,
        output_dir=str(ctx.output_dir),
        state_dir=str(ctx.state_dir),
    )
    await store.initialize()
    await store.load_graph_state()
    return store


async def make_sqlite_store(username: str, project: str) -> "SQLiteStore":
    """Open a legacy username/project store without importing the API layer."""
    from novelvideo.sqlite_store import SQLiteStore

    project_name = f"{username}/{project}"
    store = SQLiteStore(
        project_name,
        output_dir=str(Path(OUTPUT_DIR) / username / project),
        state_dir=str(Path(STATE_DIR) / username / project),
    )
    await store.initialize()
    await store.load_graph_state()
    return store


async def make_cognee_store_for_context(ctx: ProjectContext) -> "CogneeStore":
    """Open the project-scoped Cognee store for a resolved context."""
    from novelvideo.cognee import CogneeStore

    require_project_home_node(ctx, operation="open project graph store")
    store = CogneeStore(
        ctx.owner_project_label,
        output_dir=str(ctx.output_dir),
        state_dir=str(ctx.state_dir),
    )
    await store.initialize()
    return store


async def make_cognee_store(username: str, project: str) -> "CogneeStore":
    """Open a legacy username/project graph store without API coupling."""
    from novelvideo.cognee import CogneeStore

    project_name = f"{username}/{project}"
    store = CogneeStore(
        project_name,
        output_dir=str(Path(OUTPUT_DIR) / username / project),
        state_dir=str(Path(STATE_DIR) / username / project),
    )
    await store.initialize()
    return store


def make_static_url_for_context(
    ctx: ProjectContext,
    relative_path: str,
    local_path: str | Path | None = None,
) -> str:
    """Build the canonical protected project static URL."""
    resolved_local_path = (
        local_path if local_path is not None else Path(ctx.output_dir) / relative_path
    )
    return project_static_url(
        ctx.project_id,
        relative_path,
        local_path=resolved_local_path,
    )


def resolve_static_url_for_context(
    url: str,
    project_dir: str | Path,
) -> Path:
    """Resolve a protected project static URL without exposing canvas internals."""

    from novelvideo.freezone.paths import resolve_static_url_to_path

    return resolve_static_url_to_path(url, Path(project_dir))


__all__ = [
    "make_cognee_store",
    "make_cognee_store_for_context",
    "make_sqlite_store",
    "make_sqlite_store_for_context",
    "make_static_url_for_context",
    "resolve_static_url_for_context",
]
