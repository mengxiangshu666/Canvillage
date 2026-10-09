"""Project tasks for reviewing and minimally repairing character records.

The specialist agents produce proposals; this runner owns the project boundary.
Only an explicit ``apply=true`` may mutate SQLite, and every mutation is
limited to fields that already exist in the character API contract.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


_UPDATE_FIELDS = frozenset(
    {
        "face_prompt",
        "description",
        "gender",
        "age_group",
        "is_main",
        "role",
        "body_type",
        "fish_voice_id",
        "aliases",
        "appearance_details",
    }
)


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


def _character_payload(character: Any, *, store: Any) -> dict[str, Any]:
    """Keep model context complete without sending audio or binary contents."""

    identities = list(getattr(character, "identities", []) or [])
    reference_images = list(getattr(character, "reference_images", []) or [])
    character_dir = Path(str(getattr(store, "project_dir", ""))) / "assets" / "characters" / str(
        getattr(character, "name", "")
    )
    has_asset_files = character_dir.exists() and any(character_dir.rglob("*"))
    return {
        "name": str(getattr(character, "name", "") or ""),
        "aliases": list(getattr(character, "aliases", []) or []),
        "role": str(getattr(character, "role", "") or ""),
        "is_main": bool(getattr(character, "is_main", False)),
        "gender": str(getattr(character, "gender", "") or ""),
        "age_group": str(getattr(character, "age_group", "") or ""),
        "body_type": str(getattr(character, "body_type", "") or ""),
        "description": str(getattr(character, "description", "") or ""),
        "face_prompt": str(getattr(character, "face_prompt", "") or ""),
        "appearance_details": str(getattr(character, "appearance_details", "") or ""),
        "identity_count": len(identities),
        "reference_image_count": len(reference_images),
        "has_asset_files": has_asset_files,
    }


def _model_output(result: Any) -> Any:
    output = getattr(result, "output", result)
    return output.model_dump() if hasattr(output, "model_dump") else output


async def _review_characters(characters: list[dict[str, Any]]) -> Any:
    from novelvideo.agents.character_reviewer import CharacterReviewReport, create_character_reviewer_agent

    task = (
        "请审核以下当前项目角色快照。只依据提供的数据输出 CharacterReviewReport，"
        "不要调用写入工具，不要臆造不存在的角色。\n"
        "角色快照:\n"
        f"{json.dumps(characters, ensure_ascii=False, indent=2)}"
    )
    result = await create_character_reviewer_agent().run(task)
    output = _model_output(result)
    report = CharacterReviewReport.model_validate(output)
    if report.reviewed_count <= 0:
        report.reviewed_count = len(characters)
    return report


async def _fix_characters(
    characters: list[dict[str, Any]],
    report: Any,
) -> Any:
    from novelvideo.agents.character_fixer import CharacterFixReport, create_character_fixer_agent

    task = (
        "请根据当前角色快照和审核报告生成最小角色修复提案。\n"
        "update 的 target 必须严格使用 `角色名|字段名|新值`；delete 使用 `角色名`；"
        "merge 使用 `主角色名 <- 别名1,别名2`。字段只能是 face_prompt、description、gender、"
        "age_group、is_main、role、body_type、fish_voice_id、aliases、appearance_details。\n"
        "审核报告:\n"
        f"{json.dumps(report.model_dump() if hasattr(report, 'model_dump') else report, ensure_ascii=False, indent=2)}\n"
        "当前角色快照:\n"
        f"{json.dumps(characters, ensure_ascii=False, indent=2)}"
    )
    result = await create_character_fixer_agent().run(task)
    return CharacterFixReport.model_validate(_model_output(result))


def _parse_update_target(target: str) -> tuple[str, str, Any] | None:
    parts = [part.strip() for part in str(target or "").split("|", 2)]
    if len(parts) != 3 or not all(parts):
        return None
    name, field, value = parts
    if field not in _UPDATE_FIELDS:
        return None
    if field == "is_main":
        normalized = value.casefold()
        if normalized in {"true", "1", "yes", "是"}:
            value = True
        elif normalized in {"false", "0", "no", "否"}:
            value = False
        else:
            return None
    elif field == "aliases":
        value = [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]
    return name, field, value


def _has_protected_assets(character: Any, store: Any) -> bool:
    if bool(getattr(character, "is_main", False)):
        return True
    if list(getattr(character, "identities", []) or []):
        return True
    if list(getattr(character, "reference_images", []) or []):
        return True
    directory = Path(str(getattr(store, "project_dir", ""))) / "assets" / "characters" / str(
        getattr(character, "name", "")
    )
    return directory.exists() and any(directory.rglob("*"))


async def _apply_actions(report: Any, store: Any) -> tuple[list[dict[str, Any]], int]:
    changes: list[dict[str, Any]] = []
    applied_count = 0
    for action in list(getattr(report, "fixed", []) or []):
        operation = str(getattr(action, "action", "") or "").strip().casefold()
        target = str(getattr(action, "target", "") or "").strip()
        item: dict[str, Any] = {
            "action": operation,
            "target": target,
            "result": str(getattr(action, "result", "") or ""),
            "applied": False,
        }
        if getattr(action, "success", True) is False:
            item["result"] = "未应用：审核 Agent 已将该动作标记为失败"
            changes.append(item)
            continue
        if operation == "update":
            parsed = _parse_update_target(target)
            if parsed is None:
                item["result"] = "未应用：update target 格式或字段不在白名单"
            else:
                name, field, value = parsed
                character = store.get_character(name)
                if character is None:
                    item["result"] = f"未应用：角色 {name} 不存在"
                elif field == "is_main" and value is True:
                    for candidate in store.get_all_characters() or []:
                        if candidate.name != character.name and getattr(candidate, "is_main", False):
                            await store.update_character(candidate.name, is_main=False)
                    await store.update_character(character.name, **{field: value})
                    item["applied"] = True
                    applied_count += 1
                else:
                    await store.update_character(character.name, **{field: value})
                    item["applied"] = True
                    applied_count += 1
        elif operation == "delete":
            character = store.get_character(target)
            if character is None:
                item["result"] = f"未应用：角色 {target} 不存在"
            elif _has_protected_assets(character, store):
                item["result"] = "未应用：角色存在身份或资产，需人工确认后再删除"
            else:
                await store.delete_character(character.name)
                item["applied"] = True
                applied_count += 1
        elif operation == "merge":
            item["result"] = "未应用：merge 保留为预览，需确认主角色、别名及资产引用后执行"
        else:
            item["result"] = f"未应用：不支持的操作 {operation or '<empty>'}"
        changes.append(item)
    return changes, applied_count


async def _run_character_quality(
    envelope: dict[str, Any],
    ctx: ProjectContext,
    *,
    mode: str,
) -> dict[str, Any]:
    task_type = str(envelope.get("task_type") or "")
    scope = str(envelope.get("scope") or "")
    payload = envelope.get("payload") or {}
    _progress(ctx, task_type, 0.05, "加载角色快照...", scope)
    store = await _open_store(ctx)
    try:
        characters = [_character_payload(item, store=store) for item in store.get_all_characters() or []]
        if not characters:
            raise ValueError("当前项目没有可审核的角色")
        _progress(ctx, task_type, 0.2, "审核角色质量...", scope)
        report_payload = payload.get("report") if isinstance(payload, dict) else None
        if isinstance(report_payload, dict):
            from novelvideo.agents.character_reviewer import CharacterReviewReport

            review = CharacterReviewReport.model_validate(report_payload)
        else:
            review = await _review_characters(characters)
        review_data = review.model_dump()
        review_data.update({"has_issues": review.has_issues, "reviewed_count": len(characters)})
        if mode == "review":
            _progress(ctx, task_type, 1.0, "角色审核完成", scope)
            return {"mode": mode, "report": review_data, "characters": len(characters)}

        _progress(ctx, task_type, 0.5, "生成角色修复预览...", scope)
        fix_report = await _fix_characters(characters, review)
        fix_data = fix_report.model_dump()
        apply = bool(payload.get("apply")) if isinstance(payload, dict) else False
        changes, applied_count = await _apply_actions(fix_report, store) if apply else (
            [
                {
                    "action": str(getattr(action, "action", "") or ""),
                    "target": str(getattr(action, "target", "") or ""),
                    "result": str(getattr(action, "result", "") or ""),
                    "applied": False,
                }
                for action in list(getattr(fix_report, "fixed", []) or [])
            ],
            0,
        )
        _progress(ctx, task_type, 1.0, "角色修复已写回" if apply else "角色修复预览完成", scope)
        return {
            "mode": mode,
            "applied": apply,
            "characters": len(characters),
            "report": review_data,
            "fix_report": fix_data,
            "changes": changes,
            "changed_count": len(changes),
            "applied_count": applied_count,
        }
    finally:
        await store.close()


def run_character_review(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_character_quality(envelope, ctx, mode="review"),
            envelope,
            task_type="character_review",
        )
    )


def run_character_fix(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_character_quality(envelope, ctx, mode="fix"),
            envelope,
            task_type="character_fix",
        )
    )


register_project_task_runner("character_review", run_character_review)
register_project_task_runner("character_fix", run_character_fix)
