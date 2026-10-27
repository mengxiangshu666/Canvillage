"""角色档案解析任务 —— 一句话/参考图 → CharacterProfile（异步）。

解析是 20-40 秒的重 LLM 调用（与参考产品 parse 同量级），不能挂在 HTTP
请求里；注册成 project task，由任务中心统一排队/进度/取消。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager

logger = logging.getLogger(__name__)

TASK_TYPE = "character_profile_parse"


def run_character_profile_parse(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_character_profile_parse(envelope, ctx),
            envelope,
            task_type=TASK_TYPE,
        )
    )


async def _run_character_profile_parse(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any] | None:
    from novelvideo.agents.character_profiler import (
        build_generation_prompt,
        parse_character_profile,
        profile_to_character_fields,
        profile_to_identity_fields,
    )

    payload = envelope.get("payload") or {}
    description = str(payload.get("description") or "").strip()
    image_path = str(payload.get("image_path") or "").strip()
    scope = envelope.get("scope")
    manager = get_task_manager()

    def update(progress: float, current_task: str) -> None:
        manager.update_progress_for_project(
            ctx,
            TASK_TYPE,
            0,
            scope=scope,
            progress=progress,
            current_task=current_task,
        )

    update(0.05, "解析角色档案")
    try:
        profile = await parse_character_profile(
            description=description or None,
            image_path=image_path or None,
        )
    except ValueError as exc:
        logger.warning("character_profile_parse rejected: %s", exc)
        update(1.0, "解析失败")
        raise
    update(0.9, "档案生成完成")

    # 字段映射留在后端：前端只把 character_fields 填进创建表单，不再复制一遍
    # 「签名特征怎么拼进 face_prompt」的规则。
    result = {
        "ok": True,
        "profile": profile.model_dump(),
        "character_fields": profile_to_character_fields(profile),
        "identity_fields": profile_to_identity_fields(profile),
        "generation_prompt": build_generation_prompt(profile),
    }
    update(1.0, "完成")
    return result


register_project_task_runner(TASK_TYPE, run_character_profile_parse)
