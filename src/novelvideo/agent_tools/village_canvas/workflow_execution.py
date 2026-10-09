from __future__ import annotations

import hashlib
import json
import time
from typing import Any
from urllib.parse import quote

from .canvas_writes_impl import (
    _canvas_command_created_ids,
    _canvas_command_receipt,
    _canvas_command_receipts,
)
from .contracts import _canvas_payload_from_response
from .core import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_PAID_MEDIA_STARTS_PER_TURN,
    _approval_canvas_id,
    _freezone_tool_error,
    _paid_media_authorization,
    _with_tool_trace,
)
from .runtime import runtime_handler, runtime_proxy

_await_paid_media_authorization = runtime_proxy("_await_paid_media_authorization")
_canvas_id_from_args = runtime_proxy("_canvas_id_from_args")
_continue_existing_workflow_run = runtime_proxy("_continue_existing_workflow_run")
_handle_run_canvas_node = runtime_proxy("_handle_run_canvas_node")
_project_from_args = runtime_proxy("_project_from_args")
_request = runtime_proxy("_request")
_request_with_timeout = runtime_proxy("_request_with_timeout")
_server_apply_canvas_structure = runtime_proxy("_server_apply_canvas_structure")
tool_error = runtime_proxy("tool_error")
tool_result = runtime_proxy("tool_result")


def _handle_wait_canvas_receipt(args: dict[str, Any], **_: Any) -> str:
    """Wait briefly for one persisted command receipt without re-reading full history."""
    t0 = time.perf_counter()
    try:
        project = _project_from_args(args)
        canvas_id = _canvas_id_from_args(args)
        command_id = str(args.get("command_id") or "").strip()
        if not command_id or len(command_id) > 512:
            raise ValueError(
                "command_id is required and must be at most 512 characters"
            )
        timeout_ms = args.get("timeout_ms", 3000)
        poll_interval_ms = args.get("poll_interval_ms", 100)
        if (
            not isinstance(timeout_ms, int)
            or isinstance(timeout_ms, bool)
            or not 0 <= timeout_ms <= 10_000
        ):
            raise ValueError("timeout_ms must be an integer between 0 and 10000")
        if (
            not isinstance(poll_interval_ms, int)
            or isinstance(poll_interval_ms, bool)
            or not 50 <= poll_interval_ms <= 1_000
        ):
            raise ValueError("poll_interval_ms must be an integer between 50 and 1000")

        deadline = time.monotonic() + timeout_ms / 1000
        doc: dict[str, Any] = {}
        found = False
        created_ids: list[str] = []
        receipt: dict[str, Any] = {}
        first_attempt = True
        request_timed_out = False
        while True:
            now = time.monotonic()
            if not first_attempt and now >= deadline:
                break
            remaining = max(0.01, deadline - now) if timeout_ms else 0.01
            raw = _request_with_timeout(
                "GET",
                f"/api/v1/projects/{project}/freezone/canvases/{quote(canvas_id, safe='')}",
                timeout_seconds=min(float(DEFAULT_TIMEOUT_SECONDS), remaining),
            )
            request_timed_out = (
                isinstance(raw, dict) and raw.get("error_code") == "CANVAS_HTTP_TIMEOUT"
            )
            doc = (
                _canvas_payload_from_response(
                    raw if isinstance(raw, dict) else {"data": raw}
                )
                or {}
            )
            created_ids = _canvas_command_created_ids(doc, command_id)
            receipt = _canvas_command_receipt(doc, command_id)
            found = (
                bool(receipt)
                or command_id in _canvas_command_receipts(doc)
                or bool(created_ids)
            )
            first_attempt = False
            if found or request_timed_out or time.monotonic() >= deadline:
                break
            time.sleep(
                min(poll_interval_ms / 1000, max(0.0, deadline - time.monotonic()))
            )

        result = {
            "schema": "village_canvas_receipt.v1",
            "project_id": project,
            "canvas_id": canvas_id,
            "command_id": command_id,
            "stage": "result",
            "success": found,
            "receipt_found": found,
            "timed_out": not found,
            "revision": doc.get("revision"),
            "created_node_ids": created_ids,
            "receipt": receipt,
            "node_count": len(doc.get("nodes") or []),
            "edge_count": len(doc.get("edges") or []),
            "snapshot_required": not found,
            "error_code": None if found else "CANVAS_RECEIPT_TIMEOUT",
            "retryable": not found,
            "verification": {
                "contract": "canvas_command_receipt.v2",
                "status": (
                    "receipt_verified"
                    if receipt.get("server_applied") is True
                    and isinstance(receipt.get("revision"), int)
                    and isinstance(receipt.get("applied_ops"), int)
                    else "receipt_observed"
                    if found
                    else "receipt_missing"
                ),
                "required_fields": ["server_applied", "revision", "applied_ops"],
            },
        }
        return tool_result(
            _with_tool_trace(
                result,
                tool="village_canvas_wait_receipt",
                t0=t0,
                command_id=command_id,
                receipt_found=found,
                revision=doc.get("revision"),
            )
        )
    except Exception as exc:
        return _freezone_tool_error(exc, tool="village_canvas_wait_receipt", t0=t0)


def _handle_propose_generation(args: dict[str, Any], **_: Any) -> str:
    """Propose a media generation plan without starting any paid task (stronger than LibTV auto-gen)."""
    t0 = time.perf_counter()
    try:
        project = _project_from_args(args)
        node_id = str(args.get("node_id") or "").strip()
        kind = str(args.get("kind") or "image").strip().lower()
        if kind not in {"image", "video", "audio"}:
            raise ValueError("kind must be image|video|audio")
        summary = str(args.get("summary") or "").strip()
        if not summary:
            raise ValueError("summary is required")
        if len(summary) > 20_000:
            raise ValueError("summary is too long")
        model = str(args.get("model") or "").strip()[:200]
        prompt_preview = str(args.get("prompt_preview") or "").strip()[:50_000]
        paid_media_authorized, authorization_source = _paid_media_authorization(
            args, kind=kind
        )
        return tool_result(
            _with_tool_trace(
                {
                    "schema": "canvas_generation_proposal.v1",
                    "project_id": project,
                    "node_id": node_id or None,
                    "kind": kind,
                    "model": model or None,
                    "summary": summary,
                    "prompt_preview": prompt_preview or None,
                    "requires_user_confirmation": not paid_media_authorized,
                    "authorization_source": authorization_source,
                    "paid_media_likely": True,
                    "generation_started": False,
                    "handoff_level": "L2",
                    "error_code": None,
                    "next_step": (
                        "Current-turn auto authorization is active. Continue with the matching real "
                        "generation or production tool without asking again; pass task_authorization "
                        "through unchanged. Handoff stays L2 until media URL/task success (L3)."
                        if paid_media_authorized
                        else (
                            "Show this proposal to the user. Video requires explicit confirmation "
                            "before any real generation starts. Do not treat task_authorization as "
                            "video confirmation. Handoff stays L2 until media URL/task success (L3)."
                            if authorization_source == "video_confirmation_required"
                            else "Show this proposal to the user. Do not claim generation started. "
                            "Switch to auto mode or obtain explicit confirmation before starting paid media. "
                            "Handoff stays L2 until media URL/task success (L3)."
                        )
                    ),
                },
                tool="freezone_propose_generation",
                t0=t0,
                kind=kind,
                node_id=node_id or None,
            )
        )
    except Exception as exc:
        return _freezone_tool_error(exc, tool="freezone_propose_generation", t0=t0)


def _canvas_node_generation_context(
    *, project: str, canvas_id: str, node_id: str
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    raw = _request(
        "GET",
        f"/api/v1/projects/{project}/freezone/canvases/{quote(canvas_id, safe='')}",
    )
    doc = _canvas_payload_from_response(raw) or {}
    nodes = [item for item in (doc.get("nodes") or []) if isinstance(item, dict)]
    node = next((item for item in nodes if str(item.get("id") or "") == node_id), None)
    if node is None:
        raise ValueError(f"canvas node not found: {node_id}")

    by_id = {
        str(item.get("id") or ""): item for item in nodes if str(item.get("id") or "")
    }
    incoming_ids = [
        str(edge.get("source") or "")
        for edge in (doc.get("edges") or [])
        if isinstance(edge, dict) and str(edge.get("target") or "") == node_id
    ]
    references: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source_id in incoming_ids:
        source = by_id.get(source_id)
        if source is None:
            continue
        source_type = str(source.get("type") or "")
        data = source.get("data") if isinstance(source.get("data"), dict) else {}
        candidates: list[tuple[str, object]] = []
        if source_type in {
            "imageGenNode",
            "imageEditNode",
            "exportImageNode",
            "uploadImageNode",
        }:
            candidates.extend(
                ("image", data.get(key))
                for key in ("imageUrl", "previewImageUrl", "referenceImageUrl")
            )
        elif source_type == "videoNode":
            candidates.append(("video", data.get("videoUrl")))
        elif source_type == "audioNode":
            candidates.append(("audio", data.get("audioUrl")))
        elif source_type in {"textAnnotationNode", "scriptNode"}:
            candidates.extend(
                ("text", data.get(key)) for key in ("content", "text", "prompt")
            )
        for media_type, value in candidates:
            text = str(value or "").strip()
            key = (media_type, text)
            if not text or key in seen:
                continue
            seen.add(key)
            references.append(
                {
                    "type": media_type,
                    "url": text if media_type != "text" else "",
                    "text": text if media_type == "text" else "",
                    "role": str(data.get("displayName") or source_id),
                }
            )
    return doc, node, references


def _job_ref_from_response(response: object) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise RuntimeError("generation endpoint returned an invalid response")
    if response.get("ok") is False:
        raise RuntimeError(
            str(
                response.get("error")
                or response.get("detail")
                or "generation start failed"
            )
        )
    payload = (
        response.get("data") if isinstance(response.get("data"), dict) else response
    )
    task_type = str(payload.get("task_type") or "").strip()
    job_id = str(payload.get("job_id") or payload.get("scope") or "").strip()
    task_key = str(payload.get("task_key") or "").strip()
    if not task_type or not job_id or not task_key:
        raise RuntimeError(
            "generation endpoint did not return task_type/job_id/task_key"
        )
    return {
        "task_type": task_type,
        "job_id": job_id,
        "task_key": task_key,
        "task_id": str(payload.get("task_id") or "").strip() or None,
        "backend": payload.get("backend"),
        "queue": payload.get("queue"),
    }


def _normalize_video_resolution(value: object) -> str:
    normalized = str(value or "720p").strip().lower()
    aliases = {
        "4k": "2k",
        "2160p": "2k",
        "2k": "2k",
        "1080p": "1080p",
        "768p": "768p",
        "720p": "720p",
        "480p": "480p",
    }
    return aliases.get(normalized, "720p")


def _generation_request_for_canvas_node(
    *,
    canvas_id: str,
    node_id: str,
    node: dict[str, Any],
    incoming: list[dict[str, str]],
    args: dict[str, Any],
) -> tuple[str, dict[str, Any], str]:
    node_type = str(node.get("type") or "")
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    text_refs = [
        item["text"]
        for item in incoming
        if item.get("type") == "text" and item.get("text")
    ]
    prompt = str(
        args.get("prompt")
        or data.get("prompt")
        or data.get("text")
        or data.get("content")
        or (text_refs[0] if text_refs else "")
    ).strip()
    model = str(args.get("model") or data.get("model") or "").strip()

    explicit_refs = args.get("references")
    references = (
        [dict(item) for item in explicit_refs if isinstance(item, dict)]
        if isinstance(explicit_refs, list)
        else incoming
    )
    explicit_urls = args.get("reference_urls")
    if isinstance(explicit_urls, list):
        references = [
            {"type": "image", "url": str(url), "text": "", "role": "Agent reference"}
            for url in explicit_urls
            if str(url or "").strip()
        ]

    if node_type == "imageGenNode":
        if not prompt:
            raise ValueError("image node prompt is required")
        image_refs = [
            str(item.get("url") or "").strip()
            for item in references
            if str(item.get("type") or "image") == "image"
            and str(item.get("url") or "").strip()
        ]
        direct_ref = str(data.get("referenceImageUrl") or "").strip()
        if direct_ref and direct_ref not in image_refs:
            image_refs.append(direct_ref)
        aspect_ratio = str(
            args.get("aspect_ratio")
            or data.get("requestAspectRatio")
            or data.get("aspectRatio")
            or "1:1"
        )
        if aspect_ratio == "auto":
            aspect_ratio = str(data.get("aspectRatio") or "1:1")
        if aspect_ratio == "auto":
            aspect_ratio = "1:1"
        camera = (
            data.get("cameraSelection")
            if isinstance(data.get("cameraSelection"), dict)
            else None
        )
        style_id = str(data.get("styleTemplateId") or "").strip()
        body = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "image_size": str(args.get("image_size") or data.get("size") or "2K"),
            "reference_urls": image_refs,
            "camera": (
                {
                    "camera_body": camera.get("cameraBodyId") or "",
                    "lens": camera.get("lensId") or "",
                    "focal_length_mm": camera.get("focalLengthMm") or 0,
                    "aperture": camera.get("aperture") or "",
                }
                if camera
                else None
            ),
            "style": {"template_id": style_id} if style_id else None,
            "model": model or None,
            "model_id": model or None,
            "gen_mode": str(args.get("generation_mode") or data.get("genMode") or "")
            or None,
            "quality": str(args.get("quality") or data.get("quality") or "medium"),
            "canvas_id": canvas_id,
            "node_id": node_id,
        }
        return "/freezone/gen", body, "image"

    if node_type == "videoNode":
        if not prompt:
            raise ValueError("video node prompt is required")
        mode = str(args.get("generation_mode") or data.get("genMode") or "textToVideo")
        aspect_ratio = str(
            args.get("aspect_ratio") or data.get("aspectRatio") or "16:9"
        )
        duration = args.get("duration_sec", data.get("durationSec", 5))
        try:
            duration_seconds = max(1, int(duration))
        except (TypeError, ValueError):
            duration_seconds = 5
        shared = {
            "prompt": prompt,
            "camera_template_id": str(data.get("cameraMovement") or "") or None,
            "marks": data.get("marks") if isinstance(data.get("marks"), list) else [],
            "aspect_ratio": aspect_ratio,
            "resolution": _normalize_video_resolution(
                args.get("video_quality") or data.get("quality")
            ),
            "duration_seconds": duration_seconds,
            "generate_audio": bool(data.get("generateAudio", True)),
            "model": model or "direct_default",
            "model_id": model or None,
            "gen_mode": mode,
            "human_review": bool(data.get("humanReview", False)),
            "scene_optimize": data.get("sceneOptimize") or None,
            "canvas_id": canvas_id,
            "node_id": node_id,
        }
        image_urls = [
            str(item.get("url") or "").strip()
            for item in references
            if str(item.get("type") or "") == "image"
            and str(item.get("url") or "").strip()
        ]
        video_urls = [
            str(item.get("url") or "").strip()
            for item in references
            if str(item.get("type") or "") == "video"
            and str(item.get("url") or "").strip()
        ]
        if mode == "firstLastFrame":
            if not image_urls:
                raise ValueError(
                    "firstLastFrame mode requires at least one upstream image"
                )
            return (
                "/freezone/video/keyframes",
                {
                    **shared,
                    "first_frame_url": image_urls[0],
                    "last_frame_url": image_urls[1] if len(image_urls) > 1 else None,
                },
                "video",
            )
        if mode in {"imageToVideo", "imageReference"}:
            if not image_urls:
                raise ValueError(f"{mode} mode requires at least one upstream image")
            return (
                "/freezone/video/i2v",
                {**shared, "image_urls": image_urls[:9]},
                "video",
            )
        if mode == "allReference":
            media_refs = [
                {
                    "type": str(item.get("type") or "image"),
                    "url": str(item.get("url") or ""),
                    "role": str(item.get("role") or ""),
                    "label": str(item.get("role") or ""),
                }
                for item in references
                if str(item.get("type") or "") in {"image", "video", "audio"}
                and str(item.get("url") or "").strip()
            ]
            return (
                "/freezone/video/omni-gen",
                {**shared, "theme": "", "references": media_refs},
                "video",
            )
        if mode == "videoEdit":
            if not video_urls:
                raise ValueError("videoEdit mode requires one upstream video")
            return (
                "/freezone/video/video-edit",
                {
                    **shared,
                    "video_url": video_urls[0],
                    "image_urls": image_urls[:5],
                    "audio_setting": str(data.get("audioSetting") or "auto"),
                },
                "video",
            )
        return "/freezone/video/gen", {**shared, "character_ids": []}, "video"

    if node_type == "audioNode":
        if not prompt:
            raise ValueError("audio node text/prompt is required")
        audio_kind = str(args.get("audio_kind") or data.get("audioKind") or "speech")
        if audio_kind == "music":
            length = data.get("musicLengthMs", 30_000)
            try:
                music_length_ms = min(600_000, max(3_000, int(length)))
            except (TypeError, ValueError):
                music_length_ms = 30_000
            return (
                "/freezone/audio/eleven-music",
                {
                    "input": prompt,
                    "model": model or "LingShan-MU-11",
                    "music_length_ms": music_length_ms,
                    "force_instrumental": bool(data.get("forceInstrumental", True)),
                    "respect_sections_durations": bool(
                        data.get("respectSectionsDurations", True)
                    ),
                },
                "audio",
            )
        voice = data.get("voiceRef") if isinstance(data.get("voiceRef"), dict) else None
        voice_ref = None
        if voice:
            voice_ref = {
                "scope": voice.get("scope"),
                "character_name": voice.get("characterName") or "",
                "identity_id": voice.get("identityId") or "",
                "slot": voice.get("slot") or "",
                "voice_id": voice.get("voiceId") or "",
            }
        return (
            "/freezone/audio/speech",
            {
                "text": prompt,
                "emotion_prompt": str(data.get("emotionPrompt") or ""),
                "voice_ref": voice_ref,
                "model": model,
            },
            "audio",
        )

    raise ValueError(f"node type does not support direct generation: {node_type}")


def _canvas_generation_receipt(
    *,
    project: str,
    canvas_id: str,
    node_id: str,
    command_id: str,
    node_data: dict[str, Any],
    job_ref: dict[str, Any] | None,
    generation_started: bool,
    operation: str,
    source_turn_id: str = "",
    action_profile: dict[str, Any] | None = None,
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    command = {
        "type": "update_node_data",
        "node_id": node_id,
        "node_data": node_data,
    }
    server_meta = _server_apply_canvas_structure(
        project=project,
        canvas_id=canvas_id,
        command_id=command_id,
        commands=[command],
        source_turn_id=source_turn_id,
        action_profile=action_profile,
        execution_context=execution_context,
    )
    return {
        "schema": "canvas_chat_commands.v1",
        "project_id": project,
        "canvas_id": canvas_id,
        "command_id": command_id,
        "commands": [command],
        "canvas_command_emitted": True,
        "auto_apply_expected": True,
        "generation_started": generation_started,
        "operation": operation,
        "job": job_ref,
        "snapshot_required": not bool(server_meta.get("server_applied")),
        "ui_reconcile_required": True,
        **server_meta,
    }


def _handle_run_canvas_node(args: dict[str, Any], **_: Any) -> str:
    """Start or retry one generation-capable canvas node through its real task API."""
    t0 = time.perf_counter()
    try:
        project = _project_from_args(args)
        canvas_id = _canvas_id_from_args(args)
        node_id = str(args.get("node_id") or "").strip()
        command_id = str(args.get("command_id") or "").strip()
        operation = str(args.get("operation") or "start").strip().lower()
        if not node_id or not command_id:
            raise ValueError("node_id and command_id are required")
        if operation not in {"start", "retry"}:
            raise ValueError("operation must be start|retry")
        authorization = args.get("task_authorization")
        if isinstance(authorization, dict) and (
            str(authorization.get("run_mode") or "").strip() == "draft"
            or authorization.get("allow_paid_media") is False
        ):
            raise ValueError(
                "draft mode only builds canvas structure; switch to auto mode to run media nodes"
            )

        doc, node, incoming = _canvas_node_generation_context(
            project=project, canvas_id=canvas_id, node_id=node_id
        )
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        if str(data.get("agent_generation_command_id") or "") == command_id:
            existing = {
                "task_type": data.get("generationTaskType"),
                "job_id": data.get("generationTaskJobId"),
                "task_key": data.get("generationTaskKey"),
            }
            return tool_result(
                {
                    "schema": "canvas_chat_commands.v1",
                    "project_id": project,
                    "canvas_id": canvas_id,
                    "command_id": command_id,
                    "commands": [],
                    "canvas_command_emitted": True,
                    "server_applied": True,
                    "revision": doc.get("revision"),
                    "applied_ops": 0,
                    "idempotent_replay": True,
                    "generation_started": bool(data.get("isGenerating")),
                    "job": existing,
                }
            )

        active_task_type = str(data.get("generationTaskType") or "").strip()
        active_job_id = str(data.get("generationTaskJobId") or "").strip()
        if active_task_type and active_job_id and bool(data.get("isGenerating")):
            task_response = _request(
                "GET",
                f"/api/v1/projects/{project}/tasks/{quote(active_task_type, safe='')}/0",
                query={"scope": active_job_id},
            )
            task_payload = (
                task_response.get("data")
                if isinstance(task_response, dict)
                and isinstance(task_response.get("data"), dict)
                else task_response
            )
            status = (
                str(task_payload.get("status") or "").lower()
                if isinstance(task_payload, dict)
                else ""
            )
            if status not in {
                "completed",
                "failed",
                "cancelled",
                "canceled",
                "not_found",
            }:
                raise ValueError(
                    f"node already has an active generation task: {active_task_type}:{active_job_id}"
                )

        route, body, media_type = _generation_request_for_canvas_node(
            canvas_id=canvas_id,
            node_id=node_id,
            node=node,
            incoming=incoming,
            args=args,
        )
        paid_media_authorized, authorization_source = _await_paid_media_authorization(
            args,
            project=project,
            canvas_id=canvas_id,
            kind=media_type,
            action=f"{operation}_canvas_node",
            title=("重试媒体节点" if operation == "retry" else "生成媒体节点"),
            description=f"{media_type} 节点 {node_id} 将启动真实生成任务。",
        )
        if not paid_media_authorized:
            raise ValueError(f"paid media action denied: {authorization_source}")
        response = _request("POST", f"/api/v1/projects/{project}{route}", body=body)
        job_ref = _job_ref_from_response(response)
        now_ms = int(time.time() * 1000)
        retry_count = int(data.get("generationRetryCount") or 0) + (
            1 if operation == "retry" else 0
        )
        patch = {
            "isGenerating": True,
            "generationStartedAt": now_ms,
            "generationError": None,
            "generationTaskKey": job_ref["task_key"],
            "generationTaskType": job_ref["task_type"],
            "generationTaskJobId": job_ref["job_id"],
            "generationTaskRefs": [
                {
                    "taskKey": job_ref["task_key"],
                    "taskType": job_ref["task_type"],
                    "jobId": job_ref["job_id"],
                }
            ],
            "agent_generation_command_id": command_id,
            "generationStartedBy": "agent",
            "generationMediaType": media_type,
            "generationRetryCount": retry_count,
        }
        receipt = _canvas_generation_receipt(
            project=project,
            canvas_id=canvas_id,
            node_id=node_id,
            command_id=command_id,
            node_data=patch,
            job_ref=job_ref,
            generation_started=True,
            operation=operation,
            source_turn_id=str(args.get("source_turn_id") or "").strip(),
            action_profile=(
                dict(args["action_profile"])
                if isinstance(args.get("action_profile"), dict)
                else None
            ),
            execution_context=(
                dict(args["execution_context"])
                if isinstance(args.get("execution_context"), dict)
                else None
            ),
        )
        return tool_result(
            _with_tool_trace(
                receipt,
                tool="freezone_run_node",
                t0=t0,
                node_id=node_id,
                task_type=job_ref["task_type"],
                job_id=job_ref["job_id"],
            )
        )
    except Exception as exc:
        return _freezone_tool_error(exc, tool="freezone_run_node", t0=t0)


def _handle_retry_canvas_node(args: dict[str, Any], **kwargs: Any) -> str:
    return runtime_handler("_handle_run_canvas_node")(
        {**args, "operation": "retry"}, **kwargs
    )


def _handle_stop_canvas_task(args: dict[str, Any], **_: Any) -> str:
    """Cancel the active task attached to one canvas node and clear its live handle."""
    t0 = time.perf_counter()
    try:
        project = _project_from_args(args)
        canvas_id = _canvas_id_from_args(args)
        node_id = str(args.get("node_id") or "").strip()
        command_id = str(args.get("command_id") or "").strip()
        if not node_id or not command_id:
            raise ValueError("node_id and command_id are required")
        _doc, node, _incoming = _canvas_node_generation_context(
            project=project, canvas_id=canvas_id, node_id=node_id
        )
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        task_type = str(
            args.get("task_type") or data.get("generationTaskType") or ""
        ).strip()
        job_id = str(
            args.get("job_id") or data.get("generationTaskJobId") or ""
        ).strip()
        if not task_type or not job_id:
            raise ValueError("node has no active generation task")
        response = _request(
            "DELETE",
            f"/api/v1/projects/{project}/tasks/{quote(task_type, safe='')}/0",
            query={"scope": job_id},
        )
        if isinstance(response, dict) and response.get("ok") is False:
            raise RuntimeError(str(response.get("error") or "task cancellation failed"))
        patch = {
            "isGenerating": False,
            "generationStartedAt": None,
            "generationTaskKey": None,
            "generationTaskType": None,
            "generationTaskJobId": None,
            "generationTaskRefs": None,
            "generationError": None,
            "generationCancelledAt": int(time.time() * 1000),
            "agent_generation_command_id": command_id,
        }
        receipt = _canvas_generation_receipt(
            project=project,
            canvas_id=canvas_id,
            node_id=node_id,
            command_id=command_id,
            node_data=patch,
            job_ref={"task_type": task_type, "job_id": job_id},
            generation_started=False,
            operation="stop",
            source_turn_id=str(args.get("source_turn_id") or "").strip(),
            action_profile=(
                dict(args["action_profile"])
                if isinstance(args.get("action_profile"), dict)
                else None
            ),
        )
        return tool_result(
            _with_tool_trace(
                receipt,
                tool="freezone_stop_task",
                t0=t0,
                node_id=node_id,
                task_type=task_type,
                job_id=job_id,
            )
        )
    except Exception as exc:
        return _freezone_tool_error(exc, tool="freezone_stop_task", t0=t0)


def _handle_get_production_control(args: dict[str, Any], **_: Any) -> str:
    """Read the durable one-click production driver and its exact current stage."""
    try:
        project = _project_from_args(args)
        return tool_result(
            _request("GET", f"/api/v1/projects/{project}/production/control")
        )
    except Exception as exc:
        return tool_error(str(exc))


def _coerce_production_int(
    value: Any,
    *,
    default: int,
    name: str,
    minimum: int = 1,
    maximum: int = 100,
) -> int:
    """Accept a model's scalar, numeric string, or one-item list for an int field."""
    candidate = value
    if isinstance(candidate, (list, tuple)):
        if len(candidate) != 1:
            raise ValueError(f"{name} must be a single integer")
        candidate = candidate[0]
    if candidate in (None, ""):
        candidate = default
    if isinstance(candidate, bool):
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    try:
        parsed = int(candidate)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{name} must be an integer between {minimum} and {maximum}"
        ) from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return parsed


def _handle_start_production_run(args: dict[str, Any], **_: Any) -> str:
    """Start/reuse the durable best-result production driver as one write operation."""
    try:
        project = _project_from_args(args)
        auto_generate_paid_media = bool(args.get("auto_generate_paid_media", False))
        paid_media_authorized = False
        if auto_generate_paid_media:
            paid_media_authorized, authorization_source = (
                _await_paid_media_authorization(
                    args,
                    project=project,
                    canvas_id=_approval_canvas_id(args),
                    kind="video",
                    action="start_production_run",
                    title="启动一键成片",
                    description="该工作流将启动真实图片、音频或视频生成任务。",
                )
            )
            if not paid_media_authorized:
                raise ValueError(f"paid media action denied: {authorization_source}")
        body: dict[str, Any] = {
            "mode": str(args.get("mode") or "best"),
            "target_episodes": _coerce_production_int(
                args.get("target_episodes"),
                default=1,
                name="target_episodes",
            ),
            "auto_generate_paid_media": auto_generate_paid_media,
            "confirmed_paid_media": paid_media_authorized,
            "goal": str(args.get("goal") or args.get("request") or "").strip(),
            "success_criteria": [
                str(item).strip()
                for item in (args.get("success_criteria") or [])
                if str(item).strip()
            ],
        }
        episode = args.get("episode")
        if episode not in (None, ""):
            body["episode"] = _coerce_production_int(
                episode,
                default=1,
                name="episode",
            )
        for key in (
            "uploaded_filename",
            "image_model",
            "video_backend",
            "aspect_ratio",
            "canvas_id",
            "world_state_ref",
        ):
            value = args.get(key)
            if value not in (None, ""):
                body[key] = value
        for key in (
            "assumptions",
            "constraints",
            "output_spec",
            "asset_plan",
            "episode_plan",
            "quality_gates",
            "budget",
            "canvas_skeleton_refs",
            "concurrency_policy",
            "director_plan",
            "director_intent_contract",
            "director_clarification_answers",
        ):
            value = args.get(key)
            if value not in (None, "", [], {}):
                body[key] = value
        return tool_result(
            _request(
                "POST", f"/api/v1/projects/{project}/production/control/runs", body=body
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_command_production_run(args: dict[str, Any], **_: Any) -> str:
    """Pause, resume, retry, skip, take over, or cancel a production run."""
    try:
        project = _project_from_args(args)
        run_id = str(args.get("run_id") or "").strip()
        command = str(args.get("command") or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        if command not in {"pause", "resume", "cancel", "retry", "skip", "take_over"}:
            raise ValueError("invalid production command")
        paid_media_authorized = False
        if command in {"resume", "retry"}:
            paid_media_authorized, authorization_source = (
                _await_paid_media_authorization(
                    args,
                    project=project,
                    canvas_id=_approval_canvas_id(args),
                    kind="video",
                    action=f"{command}_production_run",
                    title="继续生产工作流",
                    description=f"将 {command} 真实媒体生产任务 {run_id}。",
                )
            )
            if not paid_media_authorized:
                raise ValueError(f"paid media action denied: {authorization_source}")
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/production/control/runs/{quote(run_id, safe='')}/command",
                body={
                    "command": command,
                    "confirmed_paid_media": paid_media_authorized,
                },
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_workflow_run(args: dict[str, Any], **_: Any) -> str:
    """Read one durable canvas workflow run and its current checkpoint."""
    try:
        project = _project_from_args(args)
        run_id = str(args.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        return tool_result(
            _request(
                "GET",
                f"/api/v1/projects/{project}/workflow-runs/{quote(run_id, safe='')}",
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _workflow_canvas_id(args: dict[str, Any]) -> str:
    return _canvas_id_from_args(args)


def _handle_list_workflows(args: dict[str, Any], **_: Any) -> str:
    """List the server-owned durable workflow definitions."""
    try:
        project = _project_from_args(args)
        return tool_result(_request("GET", f"/api/v1/projects/{project}/workflows"))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_list_workflow_runs(args: dict[str, Any], **_: Any) -> str:
    """List durable runs on the current canvas before creating another one."""
    try:
        project = _project_from_args(args)
        canvas_id = _workflow_canvas_id(args)
        limit = int(args.get("limit") or 20)
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return tool_result(
            _request(
                "GET",
                f"/api/v1/projects/{project}/workflow-runs",
                query={"canvas_id": canvas_id, "limit": limit},
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _workflow_start_idempotency_key(
    args: dict[str, Any],
    *,
    project: str,
    canvas_id: str,
    workflow_id: str,
    request: str,
) -> str:
    explicit = str(args.get("idempotency_key") or "").strip()
    if explicit:
        return explicit[:240]
    material = json.dumps(
        {
            "project_id": project,
            "canvas_id": canvas_id,
            "workflow_id": workflow_id,
            "request": request,
            "source_turn_id": str(args.get("source_turn_id") or "").strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return f"agent-workflow-start:{digest}"


def _handle_start_workflow_run(args: dict[str, Any], **_: Any) -> str:
    """Start or idempotently reuse a V2 durable workflow run."""
    try:
        project = _project_from_args(args)
        canvas_id = _workflow_canvas_id(args)
        workflow_id = str(args.get("workflow_id") or "").strip()
        request = str(args.get("request") or args.get("goal") or "").strip()
        run_mode = str(args.get("run_mode") or "draft").strip().lower()
        if not workflow_id:
            raise ValueError("workflow_id is required")
        if not request:
            raise ValueError("request is required")
        if run_mode not in {"draft", "auto"}:
            raise ValueError("run_mode must be draft or auto")

        inputs = (
            dict(args.get("inputs")) if isinstance(args.get("inputs"), dict) else {}
        )
        inputs.update({"request": request, "run_mode": run_mode})
        if str(args.get("director_mode") or "").strip() == "production":
            inputs["director_mode"] = "production"
        if args.get("use_starter_workflow") is True:
            inputs["use_starter_workflow"] = True
        for key in (
            "assumptions",
            "constraints",
            "output_spec",
            "world_state_ref",
            "asset_plan",
            "episode_plan",
            "quality_gates",
            "budget",
            "canvas_skeleton_refs",
            "concurrency_policy",
            "director_plan",
            "director_intent_contract",
            "director_clarification_answers",
        ):
            if args.get(key) not in (None, "", [], {}):
                inputs[key] = args[key]
        action_profile = (
            dict(args["action_profile"])
            if isinstance(args.get("action_profile"), dict)
            else {}
        )
        execution_context = args.get("execution_context")
        if not isinstance(execution_context, dict):
            execution_context = (
                dict(action_profile.get("execution_context"))
                if isinstance(action_profile.get("execution_context"), dict)
                else None
            )
        if isinstance(execution_context, dict):
            inputs["execution_context"] = dict(execution_context)
        for key in (
            "target_strategy",
            "target_node_ids",
            "creation_reason",
            "director_ledger",
        ):
            if key in action_profile:
                inputs[key] = action_profile[key]
        starter_workflow_id = str(args.get("starter_workflow_id") or "").strip()
        if starter_workflow_id:
            inputs["starter_workflow_id"] = starter_workflow_id

        authorization = args.get("task_authorization")
        source_turn_id = str(args.get("source_turn_id") or "").strip()
        if isinstance(authorization, dict):
            source_turn_id = (
                source_turn_id or str(authorization.get("turn_id") or "").strip()
            )
        if run_mode == "auto":
            raw_budget = (
                authorization.get("max_paid_starts")
                if isinstance(authorization, dict)
                else MAX_PAID_MEDIA_STARTS_PER_TURN
            )
            media_start_budget = (
                raw_budget
                if isinstance(raw_budget, int) and not isinstance(raw_budget, bool)
                else MAX_PAID_MEDIA_STARTS_PER_TURN
            )
            intent_contract = inputs.get("director_intent_contract")
            intent_contract = (
                intent_contract if isinstance(intent_contract, dict) else {}
            )
            try:
                shot_count = int(intent_contract.get("shot_count"))
            except (TypeError, ValueError):
                shot_count = 0
            max_shots = shot_count if 1 <= shot_count <= 120 else 12
            # 一镜一次分镜图 + 一次逐镜视频：按真实需求预留，既不截断多镜短片，
            # 也不会把整轮授权一次性压在一次启动上。
            requested_budget = min(
                max(media_start_budget, 1),
                max(max_shots * 2, 1),
                MAX_PAID_MEDIA_STARTS_PER_TURN,
            )
            base_key = _workflow_start_idempotency_key(
                args,
                project=project,
                canvas_id=canvas_id,
                workflow_id=workflow_id,
                request=request,
            )
            reserved_budget = 0
            granted_source = ""
            for slot in range(requested_budget):
                slot_args = {
                    **args,
                    "idempotency_key": f"{base_key}:media:{slot + 1}"[:240],
                }
                allowed, authorization_source = _await_paid_media_authorization(
                    slot_args,
                    project=project,
                    canvas_id=canvas_id,
                    kind="media",
                    action="start_workflow_run",
                    title="启动自动创作工作流",
                    description=(
                        "工作流将按本轮确认启动真实图片任务，"
                        f"本次最多 {requested_budget} 个媒体任务。"
                    ),
                )
                if not allowed:
                    if reserved_budget == 0:
                        raise ValueError(
                            f"paid media action denied: {authorization_source}"
                        )
                    break
                granted_source = granted_source or authorization_source
                if not authorization_source.startswith("server_turn_grant"):
                    # One explicit UI approval covers the described batch.
                    reserved_budget = requested_budget
                    break
                reserved_budget += 1
            inputs["media_start_budget"] = reserved_budget
            if workflow_id == "freezone-final-film" and reserved_budget > 0:
                authenticated_source = (
                    "server_turn_grant"
                    if granted_source.startswith("server_turn_grant")
                    else "user_approval"
                )
                inputs["auto_generate_paid_media"] = True
                inputs["production_authorization"] = {
                    "schema": "workflow_production_authorization.v1",
                    "scope": "workflow_run",
                    "project_id": project,
                    "canvas_id": canvas_id,
                    "source_turn_id": source_turn_id,
                    "source": authenticated_source,
                    "max_paid_starts": reserved_budget,
                    "max_shots": max_shots,
                    "max_reference_images": min(1080, max(12, max_shots * 9)),
                    "max_duration_seconds": min(7200, max(60, max_shots * 15)),
                    "allow_final_film": True,
                }

        raw_criteria = args.get("success_criteria")
        success_criteria = (
            [str(item).strip() for item in raw_criteria if str(item or "").strip()]
            if isinstance(raw_criteria, list)
            else []
        )
        if not success_criteria:
            success_criteria = [
                "画布结构真实落盘并通过 revision 验收",
                "工作流产物存在并可继续编辑",
            ]
        body = {
            "workflow_id": workflow_id,
            "canvas_id": canvas_id,
            "run_mode": run_mode,
            "inputs": inputs,
            "idempotency_key": _workflow_start_idempotency_key(
                args,
                project=project,
                canvas_id=canvas_id,
                workflow_id=workflow_id,
                request=request,
            ),
            "contract_version": 2,
            "goal": str(args.get("goal") or request).strip(),
            "success_criteria": success_criteria[:100],
            "source_turn_id": source_turn_id,
            "canvas_revision": args.get("canvas_revision"),
            "selected_node_ids": (
                args.get("selected_node_ids")
                or action_profile.get("target_node_ids")
                or []
            ),
            "pinned_node_ids": args.get("pinned_node_ids") or [],
            "model_bindings": args.get("model_bindings") or {},
            "parent_run_id": str(args.get("parent_run_id") or "").strip(),
            "director_plan_revision": str(
                args.get("director_plan_revision") or ""
            ).strip(),
            "episode_scope": args.get("episode_scope"),
            "concurrency_policy": (
                args.get("concurrency_policy")
                if isinstance(args.get("concurrency_policy"), dict)
                else {}
            ),
            **({"action_profile": action_profile} if action_profile else {}),
            **(
                {"execution_context": dict(execution_context)}
                if isinstance(execution_context, dict)
                else {}
            ),
        }
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/workflow-runs",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_update_workflow_run(args: dict[str, Any], **_: Any) -> str:
    """Compatibility path for manually driven V1 workflow checkpoints."""
    try:
        project = _project_from_args(args)
        run_id = str(args.get("run_id") or "").strip()
        event_id = str(args.get("event_id") or "").strip()
        event_type = str(args.get("event_type") or "").strip()
        step_id = str(args.get("step_id") or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        if not event_id:
            raise ValueError("event_id is required")
        if event_type not in {
            "step_started",
            "step_completed",
            "step_failed",
            "steering_added",
        }:
            raise ValueError("invalid workflow event_type")
        if event_type.startswith("step_") and not step_id:
            raise ValueError("step_id is required for step events")
        body: dict[str, Any] = {
            "event_id": event_id,
            "type": event_type,
            "step_id": step_id,
            "payload": args.get("payload")
            if isinstance(args.get("payload"), dict)
            else {},
            "error": str(args.get("error") or ""),
        }
        expected_revision = args.get("expected_revision")
        if expected_revision is not None:
            body["expected_revision"] = int(expected_revision)
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/workflow-runs/{quote(run_id, safe='')}/events",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_command_workflow_run(args: dict[str, Any], **_: Any) -> str:
    """Pause, resume, cancel, retry or steer one durable canvas workflow."""
    try:
        project = _project_from_args(args)
        run_id = str(args.get("run_id") or "").strip()
        command = str(args.get("command") or "").strip()
        idempotency_key = str(args.get("idempotency_key") or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        if command not in {"pause", "resume", "cancel", "retry", "steer"}:
            raise ValueError("invalid workflow command")
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        authorization = args.get("task_authorization")
        compose_authorization_id = (
            str(authorization.get("compose_authorization_id") or "").strip()
            if isinstance(authorization, dict)
            else ""
        )
        grant_id = (
            str(authorization.get("grant_id") or "").strip()
            if isinstance(authorization, dict)
            else ""
        )
        step_id = str(args.get("step_id") or "").strip()
        if command == "retry" and (
            (compose_authorization_id and step_id in {"", "final_film"})
            or (
                grant_id.startswith("pmg_")
                and step_id in {"", "storyboard_images", "shot_videos"}
            )
        ):
            execution_context = args.get("execution_context")
            return _continue_existing_workflow_run(
                project=project,
                canvas_id=_workflow_canvas_id(args),
                run_id=run_id,
                execution_context=(
                    dict(execution_context)
                    if isinstance(execution_context, dict)
                    else None
                ),
                task_authorization=(
                    dict(authorization) if isinstance(authorization, dict) else None
                ),
            )
        body: dict[str, Any] = {
            "command": command,
            "step_id": step_id,
            "direction": str(args.get("direction") or ""),
            "idempotency_key": idempotency_key,
        }
        if command == "retry":
            retry_scope = str(args.get("retry_scope") or "whole_step").strip()
            if retry_scope not in {"whole_step", "failed_items_only"}:
                raise ValueError("retry_scope must be whole_step or failed_items_only")
            raw_item_ids = args.get("item_ids")
            item_ids = (
                [
                    str(item_id).strip()
                    for item_id in raw_item_ids[:500]
                    if str(item_id or "").strip()
                ]
                if isinstance(raw_item_ids, list)
                else []
            )
            if retry_scope == "failed_items_only" and not item_ids:
                raise ValueError(
                    "failed_items_only retry requires at least one item_id"
                )
            body["retry_scope"] = retry_scope
            body["item_ids"] = item_ids
        expected_revision = args.get("expected_revision")
        if expected_revision is not None:
            body["expected_revision"] = int(expected_revision)
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/workflow-runs/{quote(run_id, safe='')}/command",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_start_single_video(args: dict[str, Any], **_: Any) -> str:
    """Generate one beat's video (单 beat 视频, single_video task).

    POST /projects/{project}/episodes/{episode}/beats/{beat}/video. The beat's
    prompt is taken from its stored ``video_prompt`` (set by the script step) —
    you do NOT pass a prompt. Requires the beat's first frame to exist already
    (otherwise the API returns "首帧不存在") and the beat to have a non-empty
    video_prompt (otherwise the backend returns "prompt is required"). Only
    video_backend / duration / resolution / mode are accepted request fields.
    """
    try:
        project = _project_from_args(args)
        episode = int(args.get("episode") or 1)
        beat = int(args.get("beat") or args.get("beat_number") or 0)
        if beat <= 0:
            raise ValueError("beat must be a positive integer")
        paid_media_authorized, authorization_source = _await_paid_media_authorization(
            args,
            project=project,
            canvas_id=_approval_canvas_id(args),
            kind="video",
            action="start_single_video",
            title="生成单镜视频",
            description=f"第 {episode} 集第 {beat} 个镜头将启动真实视频任务。",
        )
        if not paid_media_authorized:
            raise ValueError(f"paid media action denied: {authorization_source}")
        body: dict[str, Any] = {"confirmed_paid_media": True}
        for key in ("video_backend", "duration", "resolution", "mode"):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/episodes/{episode}/beats/{beat}/video",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_image_camera_options(args: dict[str, Any], **_: Any) -> str:
    project = _project_from_args(args)
    return tool_result(
        _request(
            "GET",
            f"/api/v1/projects/{project}/freezone/image/camera-options",
        )
    )


def _handle_get_video_camera_templates(args: dict[str, Any], **_: Any) -> str:
    project = _project_from_args(args)
    return tool_result(
        _request(
            "GET",
            f"/api/v1/projects/{project}/freezone/video/camera-templates",
        )
    )


def _handle_resolve_camera_control(args: dict[str, Any], **_: Any) -> str:
    project = _project_from_args(args)
    node_type = str(args.get("node_type") or "").strip()
    if node_type == "imageGenNode":
        catalog = _request(
            "GET",
            f"/api/v1/projects/{project}/freezone/image/camera-options",
        )
        camera = args.get("camera")
        if not isinstance(camera, dict) or not camera:
            raise ValueError("camera.resolve requires a non-empty camera object")
        return tool_result(
            {
                "ok": True,
                "node_type": node_type,
                "catalog": catalog.get("data", catalog)
                if isinstance(catalog, dict)
                else catalog,
                "command": {"type": "update_node_camera", "camera": dict(camera)},
            }
        )
    if node_type == "videoNode":
        catalog = _request(
            "GET",
            f"/api/v1/projects/{project}/freezone/video/camera-templates",
        )
        movement = str(args.get("camera_movement") or "").strip()
        if not movement:
            raise ValueError("camera.resolve requires camera_movement")
        return tool_result(
            {
                "ok": True,
                "node_type": node_type,
                "catalog": catalog.get("data", catalog)
                if isinstance(catalog, dict)
                else catalog,
                "command": {
                    "type": "update_node_camera",
                    "camera_movement": movement,
                },
            }
        )
    raise ValueError("camera.resolve supports imageGenNode or videoNode")


def _handle_libtv_scene_compile(args: dict[str, Any], **_: Any) -> dict[str, Any]:
    """Compile director-console scene data without touching the canvas."""

    from novelvideo.director_world.libtv_scene_contract import (
        compile_libtv_scene_contract,
    )

    scene = args.get("scene")
    if scene is None:
        scene = {
            key: args[key]
            for key in ("characters", "characterGroups", "props", "cameras")
            if key in args
        }
    if not isinstance(scene, dict):
        raise ValueError("director.scene.compile requires a scene object")
    result = compile_libtv_scene_contract(
        scene,
        prompt=args.get("prompt"),
        scene_id=args.get("scene_id") or args.get("sceneId"),
    )
    return {
        **result,
        "capability_id": "director.scene.compile",
        "side_effect": "none",
        "canvas_write_required": False,
    }
