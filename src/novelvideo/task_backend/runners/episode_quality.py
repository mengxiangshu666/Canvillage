"""Project tasks for reviewing and minimally repairing an episode plan."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from novelvideo.cognee.chapter_detector import ChapterDetector
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


def _plan_from_store(store: Any) -> tuple[Any, set[str], int]:
    episodes = []
    for episode in store.get_all_episodes() or []:
        episodes.append(
            SimpleNamespace(
                number=int(getattr(episode, "number", 0) or 0),
                title=str(getattr(episode, "title", "") or ""),
                chapter_start=int(getattr(episode, "chapter_start", 0) or 0),
                chapter_end=int(getattr(episode, "chapter_end", 0) or 0),
                summary=str(
                    getattr(episode, "content_summary", "")
                    or getattr(episode, "summary", "")
                    or ""
                ),
                key_events=list(getattr(episode, "key_events", []) or []),
                characters=list(
                    getattr(episode, "character_names", [])
                    or getattr(episode, "characters", [])
                    or []
                ),
                cliffhanger=str(getattr(episode, "cliffhanger", "") or ""),
            )
        )
    plan = SimpleNamespace(episodes=episodes)
    characters = {
        str(getattr(character, "name", "") or "").strip()
        for character in store.get_all_characters() or []
        if str(getattr(character, "name", "") or "").strip()
    }
    novel_text = str(store.load_novel_content() or "")
    detected_chapters = ChapterDetector().get_chapter_count(novel_text) if novel_text else 0
    planned_chapters = max((episode.chapter_end for episode in episodes), default=0)
    return plan, characters, max(detected_chapters, planned_chapters)


def _episode_payload(episode: Any) -> dict[str, Any]:
    return {
        "number": int(getattr(episode, "number", 0) or 0),
        "title": str(getattr(episode, "title", "") or ""),
        "chapter_start": int(getattr(episode, "chapter_start", 0) or 0),
        "chapter_end": int(getattr(episode, "chapter_end", 0) or 0),
        "summary": str(getattr(episode, "summary", "") or ""),
        "key_events": list(getattr(episode, "key_events", []) or []),
        "characters": list(getattr(episode, "characters", []) or []),
        "cliffhanger": str(getattr(episode, "cliffhanger", "") or ""),
    }


async def _open_store(ctx: ProjectContext):
    from novelvideo.sqlite_store import SQLiteStore

    store = SQLiteStore(
        ctx.owner_project_label,
        output_dir=str(ctx.output_dir),
        state_dir=str(ctx.state_dir),
    )
    await store.initialize()
    await store.load_graph_state()
    return store


def _progress(ctx: ProjectContext, task_type: str, progress: float, task: str, scope: str) -> None:
    get_task_manager().update_progress_for_project(
        ctx,
        task_type,
        0,
        scope=scope,
        progress=progress,
        current_task=task,
        logs=[task],
    )


async def _run_episode_plan_quality(
    envelope: dict[str, Any],
    ctx: ProjectContext,
    *,
    mode: str,
) -> dict[str, Any]:
    task_type = str(envelope.get("task_type") or "")
    scope = str(envelope.get("scope") or "")
    payload = envelope.get("payload") or {}
    _progress(ctx, task_type, 0.05, "加载分集规划...", scope)
    store = await _open_store(ctx)
    try:
        plan, available_characters, total_chapters = _plan_from_store(store)
        if not plan.episodes:
            raise ValueError("当前项目没有可审核的分集规划")

        from novelvideo.agents.episode_reviewer import create_episode_plan_reviewer

        reviewer = create_episode_plan_reviewer()
        report = reviewer.review(plan, available_characters, total_chapters)
        report_data = report.model_dump()
        report_data.update(
            {
                "passed": report.passed,
                "critical_issues": len(report.critical_issues),
                "warning_issues": len(report.warnings),
            }
        )
        if mode == "review":
            _progress(ctx, task_type, 1.0, "分集规划审查完成", scope)
            return {"mode": mode, "report": report_data, "episodes": len(plan.episodes)}

        from novelvideo.agents.episode_fixer import create_episode_plan_fixer

        fixer = create_episode_plan_fixer()
        fixed = fixer.fix(
            plan,
            report.issues,
            available_characters,
            total_chapters=total_chapters or None,
        )
        apply = bool(payload.get("apply"))
        changed = []
        current_by_number = {int(ep.number): ep for ep in store.get_all_episodes() or []}
        for fixed_episode in fixed.episodes:
            current = current_by_number.get(int(fixed_episode.number))
            if current is None:
                continue
            update = {
                "title": fixed_episode.title,
                "content_summary": fixed_episode.summary,
                "chapter_start": fixed_episode.chapter_start,
                "chapter_end": fixed_episode.chapter_end,
                "key_events": fixed_episode.key_events,
                "character_names": fixed_episode.characters,
                "cliffhanger": fixed_episode.cliffhanger,
            }
            before = _episode_payload(
                SimpleNamespace(
                    number=current.number,
                    title=current.title,
                    chapter_start=current.chapter_start,
                    chapter_end=current.chapter_end,
                    summary=current.content_summary,
                    key_events=current.key_events,
                    characters=current.character_names,
                    cliffhanger=current.cliffhanger,
                )
            )
            after = _episode_payload(fixed_episode)
            if before != after:
                changed.append({"episode": int(fixed_episode.number), "before": before, "after": after})
                if apply:
                    await store.update_episode(int(fixed_episode.number), **update)

        _progress(ctx, task_type, 1.0, "分集规划修复预览完成" if not apply else "分集规划修复已写回", scope)
        return {
            "mode": mode,
            "applied": apply,
            "changed_count": len(changed),
            "changes": changed,
            "report": report_data,
        }
    finally:
        await store.close()


def run_episode_plan_review(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_episode_plan_quality(envelope, ctx, mode="review"),
            envelope,
            task_type="episode_plan_review",
        )
    )


def run_episode_plan_fix(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_episode_plan_quality(envelope, ctx, mode="fix"),
            envelope,
            task_type="episode_plan_fix",
        )
    )


register_project_task_runner("episode_plan_review", run_episode_plan_review)
register_project_task_runner("episode_plan_fix", run_episode_plan_fix)
