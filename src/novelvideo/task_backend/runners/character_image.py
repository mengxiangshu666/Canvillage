"""Celery runner for character portrait and identity image assets."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager
from novelvideo.utils.path_resolver import canonical_character_four_view_path

logger = logging.getLogger(__name__)


def _safe_asset_name(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|]', "_", str(name or "").strip()) or "untitled"


def _strip_known_style_prefix(prompt: str) -> str:
    text = str(prompt or "").strip()
    prefixes = [
        "写实古装剧风格，",
        "写实古装剧风格,",
        "anime style,",
        "anime风格，",
        "动漫风格，",
        "蜘蛛宇宙风格，",
        "蜘蛛宇宙风格,",
        "realistic style,",
        "chinese period drama style,",
    ]
    for prefix in prefixes:
        if text.lower().startswith(prefix.lower()):
            return text[len(prefix) :].strip()
    return text


def _asset_suffix() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S%f")


def _archive_existing_asset(path: Path) -> Path | None:
    if not path.exists():
        return None
    archived = path.with_name(f"{path.stem}_{_asset_suffix()}{path.suffix}")
    path.replace(archived)
    return archived


def _install_canonical_asset(
    source_path: Path, target_path: Path
) -> tuple[Path, Path | None]:
    if not source_path.exists() or source_path.stat().st_size <= 0:
        raise RuntimeError("图像模型未返回有效文件")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    archived = _archive_existing_asset(target_path)
    try:
        shutil.move(str(source_path), str(target_path))
    except Exception:
        if archived is not None and archived.exists() and not target_path.exists():
            archived.replace(target_path)
        raise
    return target_path, archived


def _replace_canonical_asset(source_path: Path, target_path: Path) -> Path:
    target, _archived = _install_canonical_asset(source_path, target_path)
    return target


def _rollback_canonical_asset(target_path: Path, archived_path: Path | None) -> None:
    target_path.unlink(missing_ok=True)
    if archived_path is not None and archived_path.exists():
        archived_path.replace(target_path)


def _find_identity(character, identity_id: str, identity_name: str):
    for identity in character.identities or []:
        if identity_id and identity.identity_id == identity_id:
            return identity
        if identity_name and identity.identity_name == identity_name:
            return identity
    return None


def _is_image_safety_rejection(message: object) -> bool:
    """图像平台把提示词当安全风险拒绝（HTTP 400 + 安全字样）。"""

    text = str(message or "")
    lowered = text.casefold()
    return "direct image api http 400" in lowered and (
        "安全" in text or "safety" in lowered
    )


async def _rewrite_rejected_prompt(prompt_text: str, update) -> str:
    from novelvideo.agents.identity_planner import rewrite_image_prompt_for_policy

    return await rewrite_image_prompt_for_policy(
        prompt_text,
        on_log=lambda line: update(0.49, str(line)),
    )


def _provenance_task_id(envelope: dict[str, Any]) -> str:
    """Resolve the durable task id injected by the task backend."""

    return str(
        envelope.get("__run_task_id")
        or envelope.get("task_id")
        or envelope.get("id")
        or envelope.get("task_key")
        or ""
    )


def run_character_image(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_character_image(envelope, ctx),
            envelope,
            task_type=str(envelope.get("task_type") or "character_portrait"),
        )
    )


async def _run_character_image(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any] | None:
    from novelvideo.cognee import CogneeStore
    from novelvideo.project_config import load_project_config_file

    payload = envelope.get("payload") or {}
    mode = str(payload["mode"])
    character_name = str(payload["character_name"])
    identity_id = str(payload.get("identity_id") or "")
    identity_name = str(payload.get("identity_name") or "")
    style = str(payload.get("style") or "")
    model = str(payload.get("model") or "")
    output_dir = Path(str(payload.get("output_dir") or ctx.output_dir))
    task_type = str(
        envelope.get("task_type") or payload.get("task_type") or "character_portrait"
    )
    scope = envelope.get("scope") or payload.get("scope")
    manager = get_task_manager()

    def update(progress: float, current_task: str) -> None:
        manager.update_progress_for_project(
            ctx,
            task_type,
            0,
            scope=scope,
            progress=progress,
            current_task=current_task,
            logs=[current_task],
        )

    update(0.10, "加载角色数据...")
    store = CogneeStore(ctx.owner_project_label, output_dir=str(output_dir))
    await store.initialize()
    await store.load_graph_state()
    try:
        character = await store.get_character_from_graph(character_name)
        if character is None:
            raise RuntimeError(f"找不到角色: {character_name}")
        project_config = load_project_config_file(ctx.owner_username, ctx.project_name)
        ethnicity = project_config.get("ethnicity", "Chinese")

        update(0.25, "准备生成参数...")
        if mode == "portrait":
            output_path = await _generate_character_portrait(
                character=character,
                ethnicity=ethnicity,
                output_dir=output_dir,
                style=style,
                model=model,
                task_type=task_type,
                scope=str(scope or ""),
                update=update,
            )
        elif mode == "identity_portrait":
            output_path = await _generate_identity_portrait(
                store=store,
                character=character,
                ethnicity=ethnicity,
                identity_id=identity_id,
                identity_name=identity_name,
                output_dir=output_dir,
                style=style,
                model=model,
                task_type=task_type,
                scope=str(scope or ""),
                update=update,
                expected_identity_snapshot=(
                    dict(payload.get("identity_snapshot") or {})
                ),
            )
        elif mode == "identity_image":
            identity_audit: dict[str, Any] = {}
            output_path = await _generate_identity_image(
                store=store,
                character=character,
                ethnicity=ethnicity,
                identity_id=identity_id,
                identity_name=identity_name,
                output_dir=output_dir,
                style=style,
                model=model,
                task_type=task_type,
                scope=str(scope or ""),
                update=update,
                expected_reference_snapshot=(
                    dict(payload.get("reference_snapshot") or {})
                ),
                audit=identity_audit,
            )
        else:
            raise RuntimeError(f"未知角色图像生成模式: {mode}")
        from novelvideo.styles.project_style import (
            build_project_style_snapshot,
            write_artifact_style_evidence,
        )

        style_snapshot = build_project_style_snapshot(
            style,
            username=ctx.owner_username,
            project=ctx.project_name,
            project_dir=str(output_dir),
            image_model=model,
        )
        write_artifact_style_evidence(output_path, style_snapshot)
        four_view_output: Path | None = None
        if mode == "portrait":
            # 四视图设定表也是本步骤的正式产物，同样要留风格指纹；
            # 否则「角色资产完成」的判据会把它当成缺失。
            four_view_output = canonical_character_four_view_path(
                output_dir, character.name
            )
            if four_view_output.exists():
                write_artifact_style_evidence(four_view_output, style_snapshot)
        result = {
            "mode": mode,
            "character_name": character.name,
            "identity_id": identity_id,
            "identity_name": identity_name,
            "path": str(output_path),
            "four_view_path": str(four_view_output) if four_view_output else "",
        }
        if mode == "identity_image":
            result.update(identity_audit)
            try:
                from novelvideo.services.asset_provenance import (
                    record_generation_provenance,
                )

                record_generation_provenance(
                    project_dir=output_dir,
                    history_record={
                        "status": "completed",
                        "media_type": "image",
                        "task_type": task_type,
                        "task_id": _provenance_task_id(envelope),
                        "project_id": ctx.project_id,
                        "model": model,
                        "parameters": {
                            "character_name": character.name,
                            "identity_id": identity_id,
                            "identity_name": identity_name,
                            "identity_revision": identity_audit.get(
                                "identity_revision", ""
                            ),
                        },
                        "result": result,
                    },
                )
            except Exception:
                logger.warning(
                    "identity generation provenance write failed", exc_info=True
                )
        return result
    finally:
        await store.close()


async def _generate_character_portrait(
    *,
    character,
    ethnicity: str,
    output_dir: Path,
    style: str,
    model: str,
    task_type: str,
    scope: str,
    update,
) -> Path:
    from novelvideo.generators import generate_character_reference_unified

    face_prompt = str(character.face_prompt or "").strip()
    if not face_prompt:
        raise RuntimeError("请先设置面部特征 (face_prompt)")
    char_assets_dir = output_dir / "assets" / "characters" / character.name
    portrait_path = char_assets_dir / "portrait.png"
    four_view_path = canonical_character_four_view_path(output_dir, character.name)
    temp_dir = char_assets_dir / f".tmp_portrait_{_asset_suffix()}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        appearance_prompt = _strip_known_style_prefix(face_prompt)
        # 面部描述不含时代信息，时代线索在角色描述里（修士、宗门、灵根）。
        # 不带上它，模型会把没写服装的角色画成现代装。
        story_context = str(getattr(character, "description", "") or "").strip()
        if story_context:
            appearance_prompt = f"{appearance_prompt}。角色背景：{story_context}"

        async def call_generation(
            prompt_text: str,
            prompt_template: str,
            slot_dir: Path,
            reference_image_path: str,
        ):
            return await generate_character_reference_unified(
                character_name=character.name,
                appearance_prompt=prompt_text,
                output_dir=str(slot_dir),
                count=1,
                use_mock=False,
                style=style,
                ethnicity=ethnicity,
                model=model,
                project_dir=str(output_dir),
                usage_task_type=task_type,
                usage_scope=scope,
                raise_on_error=True,
                prompt_template=prompt_template,
                reference_image_path=reference_image_path,
            )

        async def generate_slot(
            prompt_template: str,
            progress: float,
            label: str,
            reference_image_path: str = "",
        ) -> Path:
            """按指定口径出一张图；被安全系统拒绝时改写提示词重试一次。"""
            slot_dir = temp_dir / prompt_template
            slot_dir.mkdir(parents=True, exist_ok=True)
            update(progress, f"调用图像模型生成{label}...")
            try:
                paths = await call_generation(
                    appearance_prompt, prompt_template, slot_dir, reference_image_path
                )
            except RuntimeError as exc:
                if not _is_image_safety_rejection(str(exc)):
                    raise
                update(
                    progress + 0.02, "提示词被图像平台安全系统拒绝，自动改写后重试..."
                )
                try:
                    rewritten = await _rewrite_rejected_prompt(
                        appearance_prompt, update
                    )
                except Exception as rewrite_error:
                    raise RuntimeError(
                        "图像平台安全拒绝且提示词自动改写失败，请在角色页调整面部描述后重试: "
                        f"{rewrite_error}"
                    ) from exc
                paths = await call_generation(
                    rewritten, prompt_template, slot_dir, reference_image_path
                )
            if not paths:
                raise RuntimeError(f"角色{label}生成失败")
            return Path(paths[0])

        # 两张图各司其职，缺一不可：正面全身照带面部，是下游唯一的「脸锚」；
        # 四视图设定表右侧是严格无头的三视图，只提供服装、体型与发型的多面参考。
        portrait_source = await generate_slot("portrait", 0.45, "角色正面全身照")
        four_view_source = await generate_slot(
            "four_view", 0.62, "角色四视图设定表", str(portrait_source)
        )
        # Install the pair together; a failed second move must not mix two generations.
        _, archived = _install_canonical_asset(four_view_source, four_view_path)
        try:
            return _replace_canonical_asset(portrait_source, portrait_path)
        except Exception:
            _rollback_canonical_asset(four_view_path, archived)
            raise
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def _generate_identity_portrait(
    *,
    store,
    character,
    ethnicity: str,
    identity_id: str,
    identity_name: str,
    output_dir: Path,
    style: str,
    model: str,
    task_type: str,
    scope: str,
    update,
    expected_identity_snapshot: dict[str, Any] | None = None,
) -> Path:
    from novelvideo.generators import generate_character_reference_unified
    from novelvideo.utils.identity_binding import validate_identity_editor_snapshot

    identity = _find_identity(character, identity_id, identity_name)
    if identity is None:
        raise RuntimeError(f"找不到身份: {identity_id or identity_name}")
    validate_identity_editor_snapshot(
        expected_identity_snapshot,
        character,
        identity,
    )
    face_prompt = str(identity.face_prompt or "").strip()
    if not face_prompt:
        raise RuntimeError("该身份无 face_prompt，无需独立 Portrait")
    safe_name = _safe_asset_name(identity.identity_name)
    id_dir = output_dir / "assets" / "characters" / character.name / "identities"
    portrait_path = id_dir / f"{character.name}_{safe_name}_portrait.png"
    temp_dir = id_dir / f".tmp_identity_portrait_{safe_name}_{_asset_suffix()}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        update(0.45, "调用图像模型生成身份 Portrait...")
        paths = await generate_character_reference_unified(
            character_name=character.name,
            appearance_prompt=_strip_known_style_prefix(face_prompt),
            output_dir=str(temp_dir),
            count=1,
            use_mock=False,
            style=style,
            ethnicity=ethnicity,
            model=model,
            project_dir=str(output_dir),
            usage_task_type=task_type,
            usage_scope=scope,
            identity_name=identity.identity_name,
            raise_on_error=True,
        )
        if not paths:
            raise RuntimeError("身份 Portrait 生成失败")
        _replace_canonical_asset(Path(paths[0]), portrait_path)
        await store.update_character_identity(
            character.name,
            identity.identity_id,
            portrait_image=str(portrait_path),
        )
        return portrait_path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def _generate_identity_image(
    *,
    store,
    character,
    ethnicity: str,
    identity_id: str,
    identity_name: str,
    output_dir: Path,
    style: str,
    model: str,
    task_type: str,
    scope: str,
    update,
    expected_reference_snapshot: dict[str, Any] | None = None,
    audit: dict[str, Any] | None = None,
) -> Path:
    from novelvideo.generators import generate_identity_image_unified
    from novelvideo.utils.identity_binding import (
        file_sha256,
        resolve_identity_generation_binding,
        validate_identity_reference_snapshot,
        write_identity_generation_evidence,
    )

    identity = _find_identity(character, identity_id, identity_name)
    if identity is None:
        raise RuntimeError(f"找不到身份: {identity_id or identity_name}")

    try:
        binding = resolve_identity_generation_binding(
            project_dir=output_dir,
            character=character,
            identity=identity,
        )
    except ValueError as exc:
        if "身份缺少造型描述或服装参考图" not in str(exc):
            raise
        # 断点自愈：造型文字缺失时用默认文本模型补全并持久化，让重试不再空转。
        update(0.30, "造型描述缺失，调用默认文本模型自动补全...")
        from novelvideo.agents.identity_planner import (
            generate_identity_appearance_details,
        )

        try:
            appearance = await generate_identity_appearance_details(
                character_name=character.name,
                visual_state=str(identity.identity_name or ""),
                reason=(
                    "一键成片运行时自动补全：该身份缺少造型描述且没有服装参考图，"
                    "由断点自愈补齐后继续生成身份图"
                ),
                character=character,
                on_log=lambda line: update(0.32, str(line)),
            )
        except Exception as remediation_error:
            raise RuntimeError(
                "造型描述自动补全失败（默认文本模型不可用或调用失败），"
                f"请在角色页手动补写造型描述后重试: {remediation_error}"
            ) from exc
        identity_updates: dict[str, Any] = {
            "appearance_details": appearance.appearance_details,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if (
            not str(getattr(identity, "body_type", "") or "").strip()
            and appearance.body_type
        ):
            identity_updates["body_type"] = appearance.body_type
        if (
            not str(getattr(identity, "face_prompt", "") or "").strip()
            and appearance.face_description
        ):
            identity_updates["face_prompt"] = appearance.face_description
        await store.update_character_identity(
            character.name,
            identity.identity_id,
            **identity_updates,
        )
        identity.appearance_details = str(identity_updates["appearance_details"])
        if "body_type" in identity_updates:
            identity.body_type = identity_updates["body_type"]
        if "face_prompt" in identity_updates:
            identity.face_prompt = identity_updates["face_prompt"]
        update(
            0.35,
            f"已自动补全造型描述：{appearance.appearance_details[:40]}",
        )
        if audit is not None:
            audit["auto_remediation"] = {
                "trigger": str(exc),
                "source": "default_text_model",
                "fields": sorted(identity_updates),
            }
        binding = resolve_identity_generation_binding(
            project_dir=output_dir,
            character=character,
            identity=identity,
        )
        # 补救是显式授权的变更：刷新期望快照，避免被陈旧快照闸门再次拒掉。
        if expected_reference_snapshot:
            expected_reference_snapshot = binding.to_payload(output_dir)
    validate_identity_reference_snapshot(
        expected_reference_snapshot,
        binding,
        output_dir,
    )

    safe_name = _safe_asset_name(identity.identity_name)
    char_assets_dir = output_dir / "assets" / "characters" / character.name
    identity_dir = char_assets_dir / "identities"
    identity_dir.mkdir(parents=True, exist_ok=True)
    output_path = identity_dir / f"{safe_name}.png"
    temp_output_path = identity_dir / f".tmp_{safe_name}_{_asset_suffix()}.png"

    update(0.45, "调用图像模型生成身份图...")
    effective_prompt = _strip_known_style_prefix(binding.identity_prompt)

    async def call_identity_image(prompt_text: str):
        return await generate_identity_image_unified(
            character_name=character.name,
            identity_prompt=prompt_text,
            reference_image_path=str(binding.face_anchor.path),
            output_path=str(temp_output_path),
            character_tag=binding.character_tag,
            ethnicity=ethnicity,
            style=style,
            model=model,
            project_dir=str(output_dir),
            costume_image_path=(
                str(binding.costume_reference.path)
                if binding.costume_reference is not None
                else ""
            ),
            usage_task_type=task_type,
            usage_scope=scope,
            identity_name=identity.identity_name,
            raise_on_error=True,
        )

    try:
        try:
            result = await call_identity_image(effective_prompt)
        except RuntimeError as exc:
            if not _is_image_safety_rejection(str(exc)):
                raise
            update(0.48, "提示词被图像平台安全系统拒绝，自动改写后重试...")
            try:
                rewritten_prompt = await _rewrite_rejected_prompt(
                    effective_prompt, update
                )
            except Exception as rewrite_error:
                raise RuntimeError(
                    "图像平台安全拒绝且提示词自动改写失败，请在角色页调整造型描述后重试: "
                    f"{rewrite_error}"
                ) from exc
            if audit is not None:
                audit["prompt_safety_rewrite"] = {
                    "trigger": "image_api_safety_400",
                    "original_chars": len(effective_prompt),
                    "rewritten_chars": len(rewritten_prompt),
                }
            result = await call_identity_image(rewritten_prompt)
        success = (
            bool(result.get("success", False))
            if isinstance(result, dict)
            else bool(result)
        )
        if not success:
            raise RuntimeError("身份图生成失败")
        canonical_output, archived_output = _install_canonical_asset(
            temp_output_path,
            output_path,
        )
        evidence_path = canonical_output.with_suffix(".identity.json")
        archived_evidence = _archive_existing_asset(evidence_path)
        reference_payload = binding.to_payload(output_dir)
        reference_images = [
            str(item["path"]) for item in reference_payload["references"]
        ]
        identity_updates: dict[str, Any] = {
            "character_tag": binding.character_tag,
            "reference_images": reference_images,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if binding.face_anchor.role == "identity_portrait":
            identity_updates["portrait_image"] = reference_images[0]
        if binding.costume_reference is not None:
            identity_updates["costume_image"] = reference_images[-1]
        try:
            evidence_path = write_identity_generation_evidence(
                output_path=canonical_output,
                project_dir=output_dir,
                binding=binding,
                model=model,
            )
            await store.update_character_identity(
                character.name,
                identity.identity_id,
                **identity_updates,
            )
        except Exception:
            evidence_path.unlink(missing_ok=True)
            if archived_evidence is not None and archived_evidence.exists():
                archived_evidence.replace(evidence_path)
            _rollback_canonical_asset(canonical_output, archived_output)
            raise
        if audit is not None:
            try:
                relative_evidence = evidence_path.relative_to(output_dir).as_posix()
            except ValueError:
                relative_evidence = str(evidence_path)
            try:
                relative_output = canonical_output.relative_to(output_dir).as_posix()
            except ValueError:
                relative_output = str(canonical_output)
            parent_assets = [ref.to_payload(output_dir) for ref in binding.references]
            audit.update(
                {
                    "identity_revision": binding.revision,
                    "reference_snapshot": reference_payload,
                    "reference_image_count": len(binding.references),
                    "reference_images": parent_assets,
                    "parent_assets": parent_assets,
                    "output_path": relative_output,
                    "output_sha256": file_sha256(canonical_output),
                    "identity_evidence_path": relative_evidence,
                }
            )
        return canonical_output
    finally:
        temp_output_path.unlink(missing_ok=True)
        temp_body_path = temp_output_path.with_name(
            f"{temp_output_path.stem}_body_temp.png"
        )
        temp_body_path.unlink(missing_ok=True)


register_project_task_runner("character_portrait", run_character_image)
register_project_task_runner("identity_image", run_character_image)
