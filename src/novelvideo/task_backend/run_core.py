"""Backend-neutral project task execution core."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from novelvideo.ports import get_usage_meter
from novelvideo.services.task_failures import (
    classify_optional_dependency_failure,
    classify_video_pending,
)
from novelvideo.shared.billing_errors import (
    INSUFFICIENT_CREDITS_MESSAGE,
    insufficient_credits_payload,
    is_insufficient_credits_error,
)
from novelvideo.task_backend.cancel import TaskCancelled, TaskTimedOut, is_cancel_requested
from novelvideo.task_backend.registry import get_project_task_runner
from novelvideo.task_backend.subprocesses import project_task_subprocess_context
from novelvideo.task_state import project_task_run_context
from novelvideo.production.cost_receipt import (
    build_production_cost_receipt,
    finalize_production_cost_receipt,
    with_production_cost_receipt,
)

logger = logging.getLogger(__name__)

_TASK_OUTPUT_PATH_KEYS = frozenset(
    {
        "path",
        "output_path",
        "video_path",
        "image_path",
        "audio_path",
        "file_path",
        "media_path",
        "result_path",
    }
)

_PROJECT_TASK_RESOURCE_KINDS = {
    "ingest_fast": "ingest",
    "build_characters": "script",
    "build_scenes": "script",
    "build_props": "script",
    "build_episodes": "script",
    "content_rewrite": "script",
    "episode_plan_review": "script",
    "episode_plan_fix": "script",
    "character_review": "script",
    "character_fix": "script",
    "script_writer": "script",
    "beat_video_prompt": "script",
    "identity_planner": "portrait",
    "episode_scene_planner": "script",
    "episode_prop_planner": "script",
    "character_portrait": "portrait",
    "identity_image": "portrait",
    "scene_reference_asset": "render",
    "prop_reference_asset": "render",
    "batch_prop_ref": "render",
    "stage_asset": "render",
    "freezone_image_to_3gs": "render",
    "sketch_generation": "sketch",
    "sketch_regen": "sketch",
    "mainline_sketch_from_context": "sketch",
    "mainline_frame_from_context": "render",
    "sketch_edit_execute": "sketch",
    "action_sketch": "sketch",
    "selected_regen": "render",
    "grid_regenerate": "render",
    "single_video": "video",
    "compose_episode": "video",
    "global_optimize_video": "script",
    "audio_generation": "tts",
    "indextts2_audio_generation": "tts",
    "audio_generation_indextts2": "tts",
    "freezone_video_gen": "video",
    "freezone_video_cut": "video",
    "freezone_analyze": "video",
    "freezone_video_story": "video",
    "freezone_image_reverse_prompt": "script",
    "freezone_text_prepare": "script",
    "freezone_story_script": "script",
    "story_lab_bible": "script",
    "story_lab_outline": "script",
    "story_lab_draft": "script",
    "story_lab_audit": "script",
}


def _resource_kind_for_task(task_type: str) -> str:
    return _PROJECT_TASK_RESOURCE_KINDS.get(task_type, "")


def _metrics_user_id_for_project_context(ctx: Any) -> str:
    requester_user_id = str(getattr(ctx, "requester_user_id", "") or "").strip()
    if requester_user_id:
        return requester_user_id
    return str(getattr(ctx, "owner_id", "") or "").strip()


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _episode_ref(episode: int) -> str:
    return f"ep{episode:03d}" if episode > 0 else "project"


def _beat_ref(episode: int, beat_num: int, *, scope: Any = None) -> str:
    ref = f"{_episode_ref(episode)}:beat{beat_num:03d}"
    clean_scope = str(scope or "").strip()
    return f"{ref}:{clean_scope}" if clean_scope else ref


def _int_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        values: list[Any] = [value]
    else:
        try:
            values = list(value)
        except TypeError:
            values = [value]
    out: list[int] = []
    for item in values:
        parsed = _positive_int(item)
        if parsed is not None and parsed not in out:
            out.append(parsed)
    return out


def _beat_numbers_from_result(result: Any) -> list[int]:
    if not isinstance(result, dict):
        return []
    for key in ("beat_numbers", "updated_beats", "generated_beats"):
        beats = _int_list(result.get(key))
        if beats:
            return beats
    beat_num = _positive_int(result.get("beat_num") or result.get("beat"))
    if beat_num:
        return [beat_num]
    items = result.get("items")
    if isinstance(items, list):
        beats: list[int] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in ("beat_num", "beat"):
                parsed = _positive_int(item.get(key))
                if parsed is not None and parsed not in beats:
                    beats.append(parsed)
        return beats
    return []


def _resource_refs_for_task_success(
    *,
    task_type: str,
    episode: int,
    beat_num: Any = None,
    scope: Any = None,
    result: Any = None,
) -> list[str]:
    kind = _resource_kind_for_task(task_type)
    if not kind:
        return []
    if kind == "ingest":
        return []
    if kind == "script":
        return [_episode_ref(episode)]

    explicit_beat = _positive_int(beat_num)
    if explicit_beat is not None:
        return [_beat_ref(episode, explicit_beat, scope=scope)]

    beats = _beat_numbers_from_result(result)
    if beats:
        return [_beat_ref(episode, beat, scope=scope) for beat in beats]

    clean_scope = str(scope or "").strip()
    if clean_scope:
        return [f"{_episode_ref(episode)}:{clean_scope}"]
    return [f"{_episode_ref(episode)}:{task_type}"]


def _clean_billing_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        clean_key = str(key or "").strip()
        if not clean_key or item is None:
            continue
        if isinstance(item, str):
            clean_item = item.strip()
            if not clean_item:
                continue
            cleaned[clean_key] = clean_item
        else:
            cleaned[clean_key] = item
    return cleaned


def _set_project_task_metrics_context(
    ctx: Any,
    task_type: str,
    billing_metadata: dict[str, Any] | None = None,
) -> None:
    billing_user_id = _metrics_user_id_for_project_context(ctx)
    context_metadata = {
        "billing_user_id": billing_user_id,
        "requester_user_id": str(getattr(ctx, "requester_user_id", "") or "").strip(),
        "project_owner_id": str(getattr(ctx, "owner_id", "") or "").strip(),
        "billing_task_type": task_type,
    }
    context_metadata.update(_clean_billing_metadata(billing_metadata))
    get_usage_meter().set_llm_usage_context(
        billing_user_id,
        project_id=str(getattr(ctx, "project_id", "") or ""),
        resource_kind=_resource_kind_for_task(task_type),
        billing_metadata={key: value for key, value in context_metadata.items() if value},
    )


def _clear_project_task_metrics_context() -> None:
    get_usage_meter().clear_llm_usage_context()


def _feature_credit_reservation_id(metadata: dict[str, Any]) -> str:
    return str(
        metadata.get("feature_credit_reservation_id")
        or metadata.get("feature_credit_charge_id")
        or ""
    ).strip()


async def _confirm_feature_credit_reservation(
    reservation_id: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not reservation_id:
        return
    try:
        await get_usage_meter().confirm_feature_credit_reservation(
            reservation_id,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("feature credit confirmation failed: %s", exc)


async def _refund_feature_credit_reservation(
    reservation_id: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not reservation_id:
        return
    try:
        await get_usage_meter().refund_feature_credit_reservation(
            reservation_id,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("feature credit refund failed: %s", exc)


async def _emit_project_task_metrics(
    ctx: Any,
    task_type: str,
    *,
    episode: int,
    beat_num: Any = None,
    scope: Any = None,
    result: Any = None,
    outcome: str = "success",
) -> None:
    try:
        usage_meter = get_usage_meter()
        user_id = _metrics_user_id_for_project_context(ctx)
        project_id = str(getattr(ctx, "project_id", "") or "")
        kind = _resource_kind_for_task(task_type)
        clean_outcome = "failed" if outcome == "failed" else "success"

        if task_type == "ingest_fast":
            model = os.environ.get("COGNEE_LLM_MODEL", "").strip()
            if clean_outcome == "success":
                await usage_meter.bump_content_counter(
                    user_id=user_id,
                    metric="ingests_completed",
                    value=1,
                    model=model,
                    project_id=project_id,
                    resource_kind="ingest",
                )
            await usage_meter.log_resource_attempts(
                user_id=user_id,
                project_id=project_id,
                kind="ingest",
                refs=[f"project:{project_id}"],
                outcome=clean_outcome,
                model=model,
            )
            return

        if clean_outcome == "success" and task_type == "script_writer":
            beats = _positive_int((result or {}).get("beats") if isinstance(result, dict) else None)
            await usage_meter.bump_content_counter(
                user_id=user_id,
                metric="scripts_written",
                value=1,
                project_id=project_id,
            )
            if beats:
                await usage_meter.bump_content_counter(
                    user_id=user_id,
                    metric="beats_written",
                    value=beats,
                    project_id=project_id,
                )

        refs = _resource_refs_for_task_success(
            task_type=task_type,
            episode=episode,
            beat_num=beat_num,
            scope=scope,
            result=result,
        )
        if not refs or not kind:
            return
        model = ""
        if isinstance(result, dict):
            model = str(result.get("model") or "").strip()
        await usage_meter.log_resource_attempts(
            user_id=user_id,
            project_id=project_id,
            kind=kind,
            refs=refs,
            outcome=clean_outcome,
            model=model,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("project task metrics emit failed: %s", exc)


def _project_task_timeout_seconds(task_type: str | None = None) -> int:
    is_long_video_task = str(task_type or "").strip() == "freezone_video_gen"
    raw_value = (
        os.environ.get("ST_PROJECT_VIDEO_TASK_TIMEOUT_S")
        if is_long_video_task
        else None
    ) or os.environ.get("ST_PROJECT_TASK_TIMEOUT_S")
    if raw_value:
        try:
            return int(raw_value)
        except ValueError:
            logger.warning("Invalid project task timeout=%r; using default", raw_value)
    return 60 * 60 if is_long_video_task else 30 * 60


def _project_task_failure_for_exception(exc: BaseException) -> tuple[str, dict[str, Any], bool]:
    from novelvideo.cognee.gateway_health import CogneeGatewayUnavailable
    from novelvideo.novel_source import NovelImportRequiredError
    from pydantic_ai.exceptions import ModelHTTPError

    if isinstance(exc, ModelHTTPError) and exc.status_code == 524:
        message = "模型通道等待超时（524），请至少等 2 分钟后重试；反复失败请更换通道。"
        metadata = {
            "error_code": "MODEL_CHANNEL_TIMEOUT",
            "http_status": 524,
            "retryable": True,
            "suggested_action": message,
        }
        body = exc.body if isinstance(exc.body, dict) else {}
        request_id = body.get("ray_id")
        if isinstance(request_id, str) and request_id.isascii() and request_id.isalnum() and len(request_id) <= 80:
            metadata["request_id"] = request_id
        return message, metadata, True

    provider_error_metadata = getattr(exc, "provider_error_metadata", None)
    if isinstance(provider_error_metadata, dict) and provider_error_metadata:
        safe_metadata = {
            key: value
            for key, value in provider_error_metadata.items()
            if key
            in {
                "endpoint_class",
                "error_code",
                "http_status",
                "protocol",
                "request_contract",
                "request_id",
                "retryable",
                "stage",
                "suggested_action",
                "verification_stage",
            }
            and value not in (None, "")
        }
        raw_request_contract = safe_metadata.pop("request_contract", None)
        if isinstance(raw_request_contract, dict):
            safe_request_contract: dict[str, object] = {}
            content_type = str(raw_request_contract.get("content_type") or "")[:80]
            if content_type.startswith("application/json"):
                safe_request_contract["content_type"] = content_type
            for key in (
                "payload_keys",
                "metadata_keys",
                "response_keys",
                "response_data_keys",
            ):
                values = raw_request_contract.get(key)
                if isinstance(values, list):
                    safe_request_contract[key] = [
                        str(value)[:80] for value in values[:32]
                    ]
            for key in ("response_code", "response_data_type", "response_message"):
                value = raw_request_contract.get(key)
                if value not in (None, ""):
                    safe_request_contract[key] = str(value)[:80]
            response_message_present = raw_request_contract.get(
                "response_message_present"
            )
            if isinstance(response_message_present, bool):
                safe_request_contract["response_message_present"] = (
                    response_message_present
                )
            media_counts = raw_request_contract.get("media_counts")
            if isinstance(media_counts, dict):
                safe_media_counts: dict[str, int] = {}
                for key in ("image", "video", "audio"):
                    if media_counts.get(key) in (None, ""):
                        continue
                    try:
                        safe_media_counts[key] = max(
                            0, min(100, int(media_counts[key]))
                        )
                    except (TypeError, ValueError):
                        continue
                safe_request_contract["media_counts"] = safe_media_counts
            for key in ("body_bytes", "content_length"):
                body_bytes = raw_request_contract.get(key)
                if isinstance(body_bytes, int) and 0 <= body_bytes <= 100_000_000:
                    safe_request_contract[key] = body_bytes
            violations = raw_request_contract.get("violations")
            if isinstance(violations, list):
                safe_violations: list[dict[str, object]] = []
                for violation in violations[:16]:
                    if not isinstance(violation, dict):
                        continue
                    code = str(violation.get("code") or "").strip()[:80]
                    if not code:
                        continue
                    details = violation.get("details")
                    safe_details: dict[str, object] = {}
                    if isinstance(details, dict):
                        for detail_key in (
                            "selectedDuration",
                            "requestedDuration",
                            "supportedDurations",
                            "requestedResolution",
                            "supportedResolutions",
                            "requestedAspectRatio",
                            "supportedAspectRatios",
                            "requestedNativeAudio",
                            "nativeAudio",
                            "promptDurations",
                            "referenceTokens",
                            "referenceCounts",
                            "referenceLimits",
                        ):
                            value = details.get(detail_key)
                            if detail_key == "referenceCounts" and isinstance(value, dict):
                                safe_counts: dict[str, int] = {}
                                for kind, count in value.items():
                                    if str(kind) not in {
                                        "image",
                                        "video",
                                        "audio",
                                        "input_images",
                                        "reference_images",
                                        "reference_videos",
                                        "reference_audios",
                                    }:
                                        continue
                                    try:
                                        if not isinstance(count, (int, float)):
                                            continue
                                        safe_counts[str(kind)] = max(0, min(100, int(count)))
                                    except (TypeError, ValueError, OverflowError):
                                        continue
                                if safe_counts:
                                    safe_details[detail_key] = safe_counts
                            elif detail_key in {
                                "promptDurations",
                                "referenceTokens",
                                "supportedDurations",
                                "supportedResolutions",
                                "supportedAspectRatios",
                            } and isinstance(value, list):
                                safe_details[detail_key] = [str(item)[:80] for item in value[:16]]
                            elif detail_key in {"selectedDuration", "requestedDuration"} and isinstance(value, (int, float)):
                                safe_details[detail_key] = max(0, min(3600, int(value)))
                            elif detail_key in {"requestedResolution", "requestedAspectRatio", "nativeAudio"}:
                                text_value = str(value or "").strip()
                                if text_value:
                                    safe_details[detail_key] = text_value[:80]
                            elif detail_key == "requestedNativeAudio" and isinstance(value, bool):
                                safe_details[detail_key] = value
                            elif detail_key == "referenceLimits" and isinstance(value, dict):
                                safe_limits: dict[str, int] = {}
                                for kind, limit in value.items():
                                    if str(kind) not in {
                                        "input_images",
                                        "reference_images",
                                        "reference_videos",
                                        "reference_audios",
                                    }:
                                        continue
                                    try:
                                        if not isinstance(limit, (int, float)):
                                            continue
                                        safe_limits[str(kind)] = max(0, min(100, int(limit)))
                                    except (TypeError, ValueError, OverflowError):
                                        continue
                                if safe_limits:
                                    safe_details[detail_key] = safe_limits
                    safe_violations.append({"code": code, "details": safe_details})
                if safe_violations:
                    safe_request_contract["violations"] = safe_violations
            body_sha256 = str(raw_request_contract.get("body_sha256") or "")
            if len(body_sha256) == 16 and body_sha256.isalnum():
                safe_request_contract["body_sha256"] = body_sha256
            if safe_request_contract:
                safe_metadata["request_contract"] = safe_request_contract
        return str(exc), safe_metadata, True

    if isinstance(exc, CogneeGatewayUnavailable):
        metadata: dict[str, object] = {
            "error_code": exc.error_code,
            "model": exc.model,
            "component": exc.component,
        }
        if exc.reason:
            metadata["reason"] = exc.reason
        if exc.status_code is not None:
            metadata["http_status"] = exc.status_code
        return (
            str(exc),
            metadata,
            True,
        )

    if isinstance(exc, NovelImportRequiredError):
        return str(exc), {"error_code": exc.error_code}, True

    if isinstance(exc, TaskTimedOut):
        timeout_seconds = int(getattr(exc, "timeout_seconds", None) or 30 * 60)
        timeout_minutes = max(round(timeout_seconds / 60), 1)
        return (
            f"任务超过 {timeout_minutes} 分钟未完成，已自动放弃",
            {"error_code": "TASK_TIMEOUT", "timeout_seconds": timeout_seconds},
            True,
        )

    try:
        from celery.exceptions import SoftTimeLimitExceeded

        if isinstance(exc, SoftTimeLimitExceeded):
            timeout_seconds = _project_task_timeout_seconds()
            timeout_minutes = max(round(timeout_seconds / 60), 1)
            return (
                f"任务超过 {timeout_minutes} 分钟未完成，已自动放弃",
                {"error_code": "TASK_TIMEOUT", "timeout_seconds": timeout_seconds},
                True,
            )
    except Exception:
        pass

    if is_insufficient_credits_error(exc):
        return INSUFFICIENT_CREDITS_MESSAGE, insufficient_credits_payload(exc), True

    optional_dependency_failure = classify_optional_dependency_failure(exc)
    if optional_dependency_failure is not None:
        return optional_dependency_failure

    try:
        from novelvideo.shared.provider_errors import (
            content_moderation_payload,
            is_content_moderation_error,
        )

        if is_content_moderation_error(exc):
            payload = content_moderation_payload(exc)
            return str(payload.get("message") or ""), payload, True
    except Exception:
        pass

    if not isinstance(exc, Exception):
        raise exc
    from novelvideo.utils.error_redaction import safe_exception_message

    return safe_exception_message(exc), {}, False


def _completion_metadata_with_provider_task_id(
    metadata: dict[str, Any],
    result: Any,
) -> dict[str, Any]:
    completion_metadata = dict(metadata)
    if isinstance(result, dict):
        provider_task_id = (
            result.get("provider_task_id")
            or result.get("huimeng_task_id")
            or result.get("newapi_task_id")
        )
        if provider_task_id:
            completion_metadata["provider_task_id"] = str(provider_task_id)
    return completion_metadata


def _task_output_paths(result: object, output_dir: Path) -> list[Path]:
    """Find completed local output files without trusting provider URLs.

    Task results are heterogeneous, so this deliberately accepts only a small
    allowlist of output-path keys, existing files, and files that resolve below
    the current project's output root. A signed URL or an arbitrary local path
    is never materialized into the shared artifact store.
    """

    try:
        root = output_dir.resolve()
    except OSError:
        return []
    paths: list[Path] = []
    seen_objects: set[int] = set()
    seen_paths: set[Path] = set()

    def add(value: object) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        candidate = Path(value)
        if not candidate.is_absolute():
            return
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            return
        try:
            if not resolved.is_file() or resolved.stat().st_size <= 0:
                return
        except OSError:
            return
        if resolved not in seen_paths and len(paths) < 8:
            seen_paths.add(resolved)
            paths.append(resolved)

    def visit(value: object, depth: int = 0) -> None:
        if depth > 4 or len(paths) >= 8:
            return
        if isinstance(value, Mapping):
            identity = id(value)
            if identity in seen_objects:
                return
            seen_objects.add(identity)
            for key, child in list(value.items())[:64]:
                if str(key).casefold() in _TASK_OUTPUT_PATH_KEYS:
                    if isinstance(child, (list, tuple)):
                        for item in child[:16]:
                            add(item)
                    else:
                        add(child)
                elif isinstance(child, (Mapping, list, tuple)):
                    visit(child, depth + 1)
        elif isinstance(value, (list, tuple)):
            for child in value[:32]:
                visit(child, depth + 1)

    visit(result)
    return paths


def _attach_task_output_artifacts(
    *,
    ctx: Any,
    task_type: str,
    task_id: str,
    result: object,
) -> object:
    """Attach verified content-addressed output refs before task completion.

    The task row remains authoritative. This adds only references to bytes
    already generated under the project output tree, allowing ``task.get`` and
    Agent handoffs to distinguish a finished media artifact from a task receipt
    or a provider acknowledgement.
    """

    if not isinstance(result, dict):
        return result
    existing = result.get("agent_artifacts")
    if isinstance(existing, list) and existing:
        return result
    try:
        from novelvideo.utils.project_paths import ProjectPaths
        from novelvideo.verification.artifact_store import copy_file_in

        project_paths = ProjectPaths.from_context(ctx)
        output_paths = _task_output_paths(result, Path(ctx.output_dir))
    except (AttributeError, OSError, TypeError, ValueError):
        return result
    if not output_paths:
        return result

    source_refs = [
        {"kind": "task", "id": str(task_id)},
        {"kind": "project", "id": str(getattr(ctx, "project_id", ""))},
    ]
    artifacts: list[dict[str, object]] = []
    for output_path in output_paths:
        try:
            stored = copy_file_in(project_paths.global_shared_artifacts_dir, output_path)
            artifact = stored.to_agent_artifact(
                kind=task_type or "task_output",
                status="verified",
                producer_agent_id="production_executor",
                source_refs=source_refs,
            )
            artifact["task_id"] = str(task_id)
            artifact["verification"] = {
                "schema": "task_output_store.v1",
                "status": "verified",
                "result": f"bytes:{stored.size_bytes}",
            }
            artifacts.append(artifact)
        except (OSError, TypeError, ValueError):
            logger.warning("Task output artifact materialization skipped: %s", output_path)
    if not artifacts:
        return result
    enriched = dict(result)
    enriched["agent_artifacts"] = artifacts
    enriched["agent_artifact"] = artifacts[0]
    return enriched


def _ensure_builtin_runners_registered() -> None:
    from novelvideo.task_backend.runners import (  # noqa: F401
        audio,
        character_image,
        content,
        episode_assets,
        freezone,
        graph_build,
        identity,
        ingest,
        prop_reference,
        render,
        scene_reference,
        script,
        sketch,
        sketch_edit_execute,
        stage_asset,
        video,
    )


def run_project_task_core_sync(
    envelope: dict[str, Any],
    ctx: Any,
    manager: Any,
    *,
    run_task_id: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_type = str(envelope["task_type"])
    episode = int(envelope.get("episode") or 0)
    beat_num = envelope.get("beat_num")
    scope = envelope.get("scope")
    billing_metadata = _clean_billing_metadata(envelope.get("billing_metadata"))
    run_metadata = {**dict(metadata or {}), **billing_metadata}
    cost_receipt = build_production_cost_receipt(
        envelope,
        task_id=run_task_id,
        metadata=run_metadata,
        status="starting",
    )
    run_metadata = with_production_cost_receipt(run_metadata, cost_receipt)
    feature_reservation_id = _feature_credit_reservation_id(run_metadata)
    timeout_seconds = _project_task_timeout_seconds(task_type)
    deadline_monotonic = time.monotonic() + timeout_seconds if timeout_seconds > 0 else None

    _clear_project_task_metrics_context()

    if asyncio.run(
        is_cancel_requested(
            project_id=str(envelope["project_id"]),
            task_type=task_type,
            episode=episode,
            task_id=run_task_id,
            beat_num=beat_num,
            scope=scope,
        )
    ):
        asyncio.run(
            _refund_feature_credit_reservation(
                feature_reservation_id,
                metadata={"source": "task_cancelled_before_start"},
            )
        )
        manager.update_progress_for_project(
            ctx,
            task_type,
            episode,
            beat_num=beat_num,
            scope=scope,
            progress=0.0,
            current_task="任务已取消",
            metadata=with_production_cost_receipt(
                run_metadata,
                finalize_production_cost_receipt(
                    cost_receipt,
                    status="cancelled",
                    metadata=run_metadata,
                ),
            ),
            status="cancelled",
            expected_task_id=run_task_id,
        )
        return {"cancelled": True}

    try:
        with project_task_run_context(run_task_id), project_task_subprocess_context(
            project_id=str(envelope["project_id"]),
            task_type=task_type,
            episode=episode,
            task_id=run_task_id,
            beat_num=beat_num,
            scope=scope,
            deadline_monotonic=deadline_monotonic,
            timeout_seconds=timeout_seconds,
        ):
            _set_project_task_metrics_context(
                ctx,
                task_type,
                billing_metadata=billing_metadata,
            )
            manager.update_progress_for_project(
                ctx,
                task_type,
                episode,
                beat_num=beat_num,
                scope=scope,
                progress=0.01,
                current_task="任务已开始",
                metadata=run_metadata,
            )

            _ensure_builtin_runners_registered()
            runner = get_project_task_runner(task_type)
            if runner is None:
                error = f"No project task runner registered for task_type={task_type}"
                asyncio.run(
                    _refund_feature_credit_reservation(
                        feature_reservation_id,
                        metadata={"source": "task_runner_missing", "error": error},
                    )
                )
                manager.fail_task_for_project(
                    ctx,
                    task_type,
                    episode,
                    beat_num=beat_num,
                    scope=scope,
                    error=error,
                    metadata=with_production_cost_receipt(
                        run_metadata,
                        finalize_production_cost_receipt(
                            cost_receipt,
                            status="failed",
                            metadata=run_metadata,
                        ),
                    ),
                    expected_task_id=run_task_id,
                )
                raise RuntimeError(error)

            try:
                envelope = {**envelope, "__run_task_id": run_task_id}
                if deadline_monotonic is not None:
                    envelope["__deadline_monotonic"] = deadline_monotonic
                    envelope["__timeout_seconds"] = timeout_seconds
                result = runner(envelope, ctx)
            except BaseException as exc:
                if isinstance(exc, TaskCancelled):
                    asyncio.run(
                        _refund_feature_credit_reservation(
                            feature_reservation_id,
                            metadata={"source": "task_cancelled"},
                        )
                    )
                    manager.update_progress_for_project(
                        ctx,
                        task_type,
                        episode,
                        beat_num=beat_num,
                        scope=scope,
                        progress=0.0,
                        current_task="任务已取消",
                        metadata=with_production_cost_receipt(
                            run_metadata,
                            finalize_production_cost_receipt(
                                cost_receipt,
                                status="cancelled",
                                metadata=run_metadata,
                            ),
                        ),
                        status="cancelled",
                        expected_task_id=run_task_id,
                    )
                    return {"cancelled": True}
                video_pending = classify_video_pending(exc)
                if video_pending is not None and video_pending.kind == "download":
                    provider_task_id = video_pending.provider_task_id
                    recovery_metadata: dict[str, object] = {
                        **run_metadata,
                        "provider": "newapi",
                        "provider_task_id": provider_task_id or None,
                        "provider_stage": "download_retry_required",
                        "error_code": "provider_result_download_pending",
                    }
                    manager.update_progress_for_project(
                        ctx,
                        task_type,
                        episode,
                        beat_num=beat_num,
                        scope=scope,
                        progress=0.95,
                        current_task="上游视频已完成，正在自动恢复下载",
                        logs=[video_pending.message],
                        metadata=with_production_cost_receipt(
                            recovery_metadata,
                            {**cost_receipt, "result_status": "waiting"},
                        ),
                        status="waiting",
                        expected_task_id=run_task_id,
                    )
                    return {
                        "recovery_pending": True,
                        "provider_task_id": provider_task_id,
                    }
                if video_pending is not None and video_pending.kind == "submission":
                    pending_metadata: dict[str, object] = {
                        **run_metadata,
                        "provider": "newapi",
                        "provider_stage": "submit_result_unknown",
                        "error_code": "VIDEO_SUBMIT_RESULT_UNKNOWN",
                        "idempotency_key": video_pending.idempotency_key,
                    }
                    manager.update_progress_for_project(
                        ctx,
                        task_type,
                        episode,
                        beat_num=beat_num,
                        scope=scope,
                        progress=0.1,
                        current_task="视频提交结果待确认，保留任务等待恢复",
                        logs=[video_pending.message],
                        metadata=with_production_cost_receipt(
                            pending_metadata,
                            {**cost_receipt, "result_status": "waiting"},
                        ),
                        status="waiting",
                        expected_task_id=run_task_id,
                    )
                    return {
                        "submission_pending": True,
                        "idempotency_key": video_pending.idempotency_key,
                    }
                error, failure_payload, handled = _project_task_failure_for_exception(exc)
                asyncio.run(
                    _refund_feature_credit_reservation(
                        feature_reservation_id,
                        metadata={
                            "source": "task_failed",
                            "error": error,
                            **failure_payload,
                        },
                    )
                )
                manager.fail_task_for_project(
                    ctx,
                    task_type,
                    episode,
                    beat_num=beat_num,
                    scope=scope,
                    error=error,
                    metadata=with_production_cost_receipt(
                        {**run_metadata, **failure_payload},
                        finalize_production_cost_receipt(
                            cost_receipt,
                            status="failed",
                            metadata={**run_metadata, **failure_payload},
                        ),
                    ),
                    expected_task_id=run_task_id,
                )
                asyncio.run(
                    _emit_project_task_metrics(
                        ctx,
                        task_type,
                        episode=episode,
                        beat_num=beat_num,
                        scope=scope,
                        outcome="failed",
                    )
                )
                if handled:
                    return {"failed": True, **failure_payload}
                raise

            asyncio.run(
                _emit_project_task_metrics(
                    ctx,
                    task_type,
                    episode=episode,
                    beat_num=beat_num,
                    scope=scope,
                    result=result,
                )
            )
            result = _attach_task_output_artifacts(
                ctx=ctx,
                task_type=task_type,
                task_id=run_task_id,
                result=result,
            )
            asyncio.run(
                _confirm_feature_credit_reservation(
                    feature_reservation_id,
                    metadata={"source": "task_completed"},
                )
            )
            manager.complete_task_for_project(
                ctx,
                task_type,
                episode,
                beat_num=beat_num,
                scope=scope,
                result=result or {"ok": True},
                current_task="完成",
                logs=["完成"],
                metadata=with_production_cost_receipt(
                    _completion_metadata_with_provider_task_id(run_metadata, result),
                    finalize_production_cost_receipt(
                        cost_receipt,
                        status="completed",
                        result=result,
                        metadata=run_metadata,
                    ),
                ),
                expected_task_id=run_task_id,
            )
        return result or {"ok": True}
    finally:
        _clear_project_task_metrics_context()
