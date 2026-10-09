"""Celery runner for episode identity planning."""

from __future__ import annotations

import asyncio
from typing import Any

from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


def _build_identity_planner_result(
    *,
    episode: int,
    new_count: int,
    resolved_count: int,
    identities: list[dict[str, str]],
    auto_promoted_characters: list[str],
) -> dict[str, Any]:
    return {
        "episode": episode,
        "new_count": new_count,
        "resolved_count": resolved_count,
        "identities": identities,
        "auto_promoted_characters": auto_promoted_characters,
    }


def run_identity_planner(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_identity_planner(envelope, ctx),
            envelope,
            task_type="identity_planner",
        )
    )


async def _run_identity_planner(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    from novelvideo.agents.identity_planner import IdentityPlanner
    from novelvideo.cognee import CogneeStore
    from novelvideo.sqlite_store import SQLiteStore

    payload = envelope.get("payload") or {}
    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    original_mode = bool(payload.get("original_mode"))
    manager = get_task_manager()

    def update(
        progress: float | None = None, task: str | None = None, log: str | None = None
    ) -> None:
        manager.update_progress_for_project(
            ctx,
            "identity_planner",
            episode,
            progress=progress,
            current_task=task,
            logs=[log] if log else None,
        )

    update(0.05, "加载项目数据...")
    sqlite_store = SQLiteStore(
        ctx.owner_project_label,
        output_dir=str(ctx.output_dir),
        state_dir=str(ctx.state_dir),
    )
    cognee_store = None
    try:
        await sqlite_store.initialize()
        await sqlite_store.load_graph_state()

        cognee_store = CogneeStore(
            ctx.owner_project_label,
            output_dir=str(ctx.output_dir),
            state_dir=str(ctx.state_dir),
            sqlite_store=sqlite_store,
        )
        await cognee_store.initialize()
        await cognee_store.load_graph_state()

        episode_obj = cognee_store.get_episode(episode)
        if episode_obj is None:
            raise ValueError(f"Episode {episode} not found")

        update(0.10, "分析身份需求...")

        # original_seed has already created the canonical ep000 identities.
        # Reusing them keeps the original entry deterministic and prevents an
        # empty Cognee graph from re-running the novel Pass 0 detector.
        if original_mode:
            episode_identity_ids = set(getattr(episode_obj, "identity_ids", []) or [])
            character_source = sqlite_store.get_all_characters()
            identities = [
                {
                    "character_name": character.name,
                    "identity_id": identity.identity_id,
                    "identity_name": getattr(identity, "identity_name", "") or identity.identity_id,
                    "appearance_details": getattr(identity, "appearance_details", "") or "",
                }
                for character in character_source
                for identity in getattr(character, "identities", []) or []
                if getattr(identity, "identity_id", "") in episode_identity_ids
            ]
            if not identities:
                # Some runtime versions expose the episode row before the
                # character projection is hydrated. The seed's identity IDs
                # are still durable and canonical, so preserve them directly
                # instead of sending original mode through novel Pass 0.
                identities = [
                    {
                        "character_name": identity_id.rsplit("_", 1)[0] or identity_id,
                        "identity_id": identity_id,
                        "identity_name": identity_id.rsplit("_", 1)[-1] or "默认",
                        "appearance_details": "",
                    }
                    for identity_id in sorted(episode_identity_ids)
                    if identity_id
                ]
            if not identities:
                raise ValueError("原创种子未形成可复用的身份 ID")
            update(0.95, "复用原创种子身份", f"复用 {len(identities)} 个身份")
            return _build_identity_planner_result(
                episode=episode,
                new_count=0,
                resolved_count=len(identities),
                identities=identities,
                auto_promoted_characters=[],
            )

        planner = IdentityPlanner(cognee_store)

        def on_log(message: str) -> None:
            update(log=message)

        new_count, resolved_count = await planner.plan_single_episode(episode_obj, on_log=on_log)
        refreshed = cognee_store.get_episode(episode) or episode_obj

        identities: list[dict[str, str]] = []
        episode_identity_ids = set(getattr(refreshed, "identity_ids", []) or [])
        for character in cognee_store.get_all_characters():
            for identity in getattr(character, "identities", []) or []:
                identity_id = getattr(identity, "identity_id", "") or ""
                if not identity_id or identity_id not in episode_identity_ids:
                    continue
                identities.append(
                    {
                        "character_name": character.name,
                        "identity_id": identity_id,
                        "identity_name": getattr(identity, "identity_name", "") or identity_id,
                        "appearance_details": getattr(identity, "appearance_details", "") or "",
                    }
                )

        update(0.95, "身份规划完成", f"新增 {new_count} 个身份，复用 {resolved_count} 个身份")
        return _build_identity_planner_result(
            episode=episode,
            new_count=new_count,
            resolved_count=resolved_count,
            identities=identities,
            auto_promoted_characters=list(getattr(planner, "auto_promoted_characters", []) or []),
        )
    finally:
        try:
            if cognee_store is not None:
                await cognee_store.close()
        finally:
            await sqlite_store.close()


register_project_task_runner("identity_planner", run_identity_planner)
