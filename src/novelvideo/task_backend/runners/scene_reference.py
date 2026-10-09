"""Celery runner for canonical scene reference images."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


class ReverseNeedDecision(BaseModel):
    """文本模型对「该场景要不要背面图」的判断。"""

    need_reverse: bool = Field(description="是否需要生成该场景的背面图")
    confidence: float = Field(default=0.5, description="判断置信度 0-1")
    reason: str = Field(default="", description="一句话理由，引用镜头里的朝向证据")


REVERSE_NEED_PROMPT = """你是分镜导演助理。判断这个场景是否需要生成「背面图」——
即站在正面机位原地转身 180 度后看到的那一面（不是换个机位）。

判据：
1. 本集该场景的镜头是否会拍到正面图之外的方向：转身、反打、人物背对原机位、
   朝向门口/窗外/远处看。
2. 环境描述里背面一侧是否有实际内容（空白则没有拍的必要）。
3. 该场景镜头数量越多，出现反向镜头的概率越高。
4. 只判断「要不要背面图」，不要考虑 360 全景。

## 场景
- 名称: {name}
- 环境描述: {environment}
- 本集绑定镜头数: {beat_count}
- 各镜头视觉描述:
{beat_lines}
"""


def build_reverse_need_prompt(
    scene_name: str,
    environment_prompt: str,
    beat_descriptions: list[str],
) -> str:
    beat_lines = "\n".join(
        f"{index + 1}. {text[:80]}" for index, text in enumerate(beat_descriptions[:30])
    ) or "（本集无绑定镜头）"
    return REVERSE_NEED_PROMPT.format(
        name=scene_name,
        environment=(environment_prompt or "").strip()[:500] or "（未填写）",
        beat_count=len(beat_descriptions),
        beat_lines=beat_lines,
    )


async def judge_reverse_master_need(
    scene_name: str,
    environment_prompt: str,
    beat_descriptions: list[str],
) -> ReverseNeedDecision:
    """用默认文本模型判断该场景是否需要背面图；判断不出来时抛错由调用方兜底。"""

    from novelvideo.config import (
        get_newapi_text_pydantic_model,
        get_newapi_text_pydantic_model_settings,
    )

    agent = Agent(
        get_newapi_text_pydantic_model("NARRATED_SCENE_ASSET_MODEL", ""),
        output_type=ReverseNeedDecision,
        retries=2,
        model_settings=get_newapi_text_pydantic_model_settings(
            "NARRATED_SCENE_ASSET_THINKING_LEVEL", "low"
        ),
        name="背面图需求判断师",
    )
    result = await agent.run(
        build_reverse_need_prompt(scene_name, environment_prompt, beat_descriptions)
    )
    return result.output


def should_chain_reverse_master(
    *,
    kind: str,
    auto_enabled: bool,
    reverse_exists: bool,
    decision: ReverseNeedDecision | None,
) -> bool:
    """正面图成功后，是否在本次任务内接着生成背面图。"""

    if kind != "master" or not auto_enabled or reverse_exists:
        return False
    return decision is not None and decision.need_reverse


async def _scene_beat_descriptions(store: Any, scene_name: str, limit: int = 30) -> list[str]:
    """该场景在本集绑定镜头的视觉描述，供需求判断引用。"""

    from novelvideo.models import beat_scene_id

    descriptions: list[str] = []
    for beat in await store.sqlite_store.list_visual_beats():
        if beat_scene_id(beat) != scene_name:
            continue
        text = str(
            getattr(beat, "visual_description", "")
            or getattr(beat, "narration", "")
            or getattr(beat, "narration_segment", "")
            or ""
        ).strip()
        if text:
            descriptions.append(text)
        if len(descriptions) >= limit:
            break
    return descriptions


async def _decide_reverse_need_via_jev(
    scene_name: str,
    environment_prompt: str,
    beat_descriptions: list[str],
) -> ReverseNeedDecision | None:
    """jev 主判背面需求。返回 None 表示 jev 不可用或拿不准，调用方退回文本模型。"""

    import asyncio

    from novelvideo.services import judgment as jev

    state: list[str] = [f"场景「{scene_name}」环境四向：{environment_prompt or '（未填写）'}"]
    state.extend(f"镜头{i + 1}：{text}" for i, text in enumerate(beat_descriptions[:30]))
    probability = await asyncio.to_thread(
        jev.judge_yes_no,
        state,
        "该场景需要生成背面图，供拍到背面方向的镜头当参考。",
        "至少有一个镜头会拍到背面方向（转身、反打、看门口、人物背对原机位）",
        "所有镜头都只拍正面方向",
    )
    if 0.3 < probability < 0.7:
        return None
    return ReverseNeedDecision(
        need_reverse=probability >= 0.7,
        confidence=round(abs(probability - 0.5) * 2, 2),
        reason=f"jev 判断概率 {probability:.2f}",
    )


def run_scene_reference_asset(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_scene_reference_asset(envelope, ctx),
            envelope,
            task_type="scene_reference_asset",
        )
    )


async def _run_scene_reference_asset(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any] | None:
    from novelvideo.cognee import CogneeStore
    from novelvideo.config import (
        IMAGE_GENERATION_SELECTIONS,
        SCENE_REVERSE_MASTER_AUTO,
        normalize_image_generation_selection,
    )
    from novelvideo.generators.direct_image_models import (
        resolve_direct_image_model,
    )
    from novelvideo.generators.scene_reference_images import generate_scene_reference_image
    from novelvideo.styles.project_style import build_project_style_snapshot
    from novelvideo.utils.path_resolver import canonical_scene_reverse_master_path

    payload = envelope.get("payload") or {}
    scene_name = str(payload["scene_name"])
    kind = str(payload["kind"])
    style = str(payload.get("style") or "")
    model_selection = str(payload.get("model") or "").strip()
    scope = envelope.get("scope")
    output_dir = Path(str(payload.get("output_dir") or ctx.output_dir))
    manager = get_task_manager()

    if kind not in {"master", "spatial_layout", "reverse_master"}:
        raise ValueError(f"Unsupported scene reference kind: {kind}")

    def update(progress: float, current_task: str) -> None:
        manager.update_progress_for_project(
            ctx,
            "scene_reference_asset",
            0,
            scope=scope,
            progress=progress,
            current_task=current_task,
            logs=[current_task],
        )

    update(0.10, "加载场景数据...")
    store = CogneeStore(ctx.owner_project_label, output_dir=str(output_dir))
    await store.initialize()
    try:
        scene = await store.sqlite_store.get_scene(scene_name)
        if scene is None:
            raise RuntimeError(f"找不到场景: {scene_name}")
        base_scene = None
        base_scene_id = str(getattr(scene, "base_scene_id", "") or "").strip()
        if base_scene_id and base_scene_id != scene.name:
            base_scene = await store.sqlite_store.get_scene(base_scene_id)

        style_snapshot = build_project_style_snapshot(
            style,
            username=ctx.owner_username,
            project=ctx.project_name,
            project_dir=str(output_dir),
            image_model=model_selection,
        )
        style_id = str(style_snapshot["style_id"])
        style_prompt = str(style_snapshot["image_prompt"])
        avoid_instructions = str(style_snapshot["negative_prompt"])
        style_name = (
            f"{style_snapshot['label']} ({style_id})"
            if style_snapshot["mode"] == "locked"
            else ""
        )

        update(0.40, f"调用图像模型生成 {kind}...")
        direct_model = resolve_direct_image_model(model_selection)
        provider = None
        model = None
        if model_selection and direct_model is None:
            normalized_selection = normalize_image_generation_selection(model_selection)
            selected_image_source = IMAGE_GENERATION_SELECTIONS[normalized_selection]
            provider = selected_image_source["provider"]
            model = selected_image_source["model"]
        if direct_model is None and not model_selection:
            raise ValueError("场景参考图未配置图片模型；请先在模型中心添加并检测生图模型。")
        output_path = await generate_scene_reference_image(
            project_dir=output_dir,
            scene=scene,
            kind=kind,  # type: ignore[arg-type]
            provider=provider,
            model=model_selection if direct_model is not None else model,
            style_name=style_name,
            style_prompt=style_prompt,
            avoid_instructions=avoid_instructions,
            base_scene=base_scene,
        )
        if not Path(output_path).is_file() or Path(output_path).stat().st_size <= 0:
            raise RuntimeError(
                f"场景参考图生成未产出有效文件: scene={scene_name}, kind={kind}, path={output_path}"
            )
        from novelvideo.styles.project_style import write_artifact_style_evidence

        write_artifact_style_evidence(output_path, style_snapshot)

        # 正面图落地后，按需补背面图：文本模型判断本集镜头是否会拍到背面。
        # 判断或生成失败都只记日志，不拖垮已经成功的正面图。
        reverse_master_path = ""
        reverse_decision: ReverseNeedDecision | None = None
        reverse_error = ""
        reverse_exists = canonical_scene_reverse_master_path(output_dir, scene.name).exists()
        if kind == "master" and SCENE_REVERSE_MASTER_AUTO:
            if reverse_exists:
                update(0.80, "背面图已存在，跳过自动补齐")
            else:
                update(0.70, "判断本集镜头是否会拍到背面...")
                descriptions = await _scene_beat_descriptions(store, scene.name)
                environment_text = str(getattr(scene, "environment_prompt", "") or "")
                # jev 主判（快、便宜、带概率）；不可用或拿不准时退回文本模型。
                try:
                    reverse_decision = await _decide_reverse_need_via_jev(
                        scene.name, environment_text, descriptions
                    )
                except Exception:  # noqa: BLE001 - jev 缺席不改变原有行为
                    reverse_decision = None
                if reverse_decision is None:
                    try:
                        reverse_decision = await judge_reverse_master_need(
                            scene.name, environment_text, descriptions
                        )
                    except Exception as exc:  # noqa: BLE001 - 判断失败退回默认行为
                        update(0.80, f"背面需求判断失败，跳过背面图: {str(exc)[:120]}")
                if reverse_decision is not None:
                    update(
                        0.75,
                        f"背面图判断: 需要={reverse_decision.need_reverse} "
                        f"({reverse_decision.reason[:60]})",
                    )
        if should_chain_reverse_master(
            kind=kind,
            auto_enabled=SCENE_REVERSE_MASTER_AUTO,
            reverse_exists=reverse_exists,
            decision=reverse_decision,
        ):
            update(0.80, "生成背面图...")
            try:
                reverse_output = await generate_scene_reference_image(
                    project_dir=output_dir,
                    scene=scene,
                    kind="reverse_master",
                    provider=provider,
                    model=model_selection if direct_model is not None else model,
                    style_name=style_name,
                    style_prompt=style_prompt,
                    avoid_instructions=avoid_instructions,
                    base_scene=base_scene,
                )
                if not Path(reverse_output).is_file() or Path(reverse_output).stat().st_size <= 0:
                    raise RuntimeError(f"背面图生成未产出有效文件: {reverse_output}")
                write_artifact_style_evidence(reverse_output, style_snapshot)
                reverse_master_path = str(reverse_output)
            except Exception as exc:  # noqa: BLE001 - 背面失败不拖垮正面
                reverse_error = str(exc)[:200]
                update(0.90, f"背面图生成失败（正面图不受影响）: {reverse_error}")

        if kind == "spatial_layout":
            rel_path = str(Path(output_path).relative_to(output_dir))
            await store.sqlite_store.update_scene(scene_name, spatial_layout_image=rel_path)
        return {
            "scene_name": scene_name,
            "kind": kind,
            "path": str(output_path),
            "style": style_name,
            "reverse_master": reverse_master_path,
            "reverse_decision": (
                reverse_decision.model_dump() if reverse_decision is not None else None
            ),
            "reverse_error": reverse_error,
        }
    finally:
        await store.close()


register_project_task_runner("scene_reference_asset", run_scene_reference_asset)
