"""AutoDL.Art ComfyUI workflow adapter for reference-conditioned speech."""

from __future__ import annotations

import asyncio
import mimetypes
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urljoin

from novelvideo.generators.direct_model_capabilities import normalize_direct_model_base_url
from novelvideo.storage.media_relay import upload_media_bytes


class AutoDLAudioError(RuntimeError):
    """A bounded, provider-neutral AutoDL execution error."""

    def __init__(self, message: str, *, code: str, details: Mapping[str, Any] | None = None):
        self.code = code
        self.details = dict(details or {})
        super().__init__(message)


def _workflow_endpoint(base_url: str, workflow_id: str) -> str:
    base = normalize_direct_model_base_url(base_url).rstrip("/")
    return f"{base}/api/v1/comfyui/comfyui_workflow/{quote(workflow_id, safe='')}"


def _result_endpoint(base_url: str, task_id: str) -> str:
    base = normalize_direct_model_base_url(base_url).rstrip("/")
    return f"{base}/api/v1/comfyui/comfyui_workflow/result/{quote(task_id, safe='')}"


def _metadata_field(mapping: Mapping[str, Any], *names: str) -> str:
    """Resolve a logical input name from either flat or nested provider maps."""

    for name in names:
        raw = mapping.get(name)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if isinstance(raw, Mapping):
            for key in ("field", "parameter", "key", "providerKey", "provider_key"):
                value = raw.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    # Accept the inverse shape: {provider_field: "prompt_text"}.
    wanted = {name.casefold() for name in names}
    for provider_field, logical in mapping.items():
        if isinstance(logical, str) and logical.strip().casefold() in wanted:
            return str(provider_field).strip()
    return ""


def _parameter_field(mapping: Mapping[str, Any], key: str) -> str:
    raw = mapping.get(key)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, Mapping):
        for name in ("field", "parameter", "key", "providerKey", "provider_key"):
            value = raw.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return str(key or "").strip()


def _safe_payload_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(k): _safe_payload_value(v) for k, v in list(value.items())[:64]}
    if isinstance(value, (list, tuple)):
        return [_safe_payload_value(item) for item in list(value)[:64]]
    return str(value)


def _response_data(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    data = payload.get("data")
    return data if isinstance(data, Mapping) else payload


_TASK_ID_KEYS = ("task_id", "taskId", "job_id", "jobId", "taskID")
_TASK_CONTAINER_KEYS = frozenset({"data", "result", "task", "job", "payload"})


def _extract_task_id(value: Any, *, allow_plain_id: bool = False, depth: int = 0) -> str:
    """Find a provider task identifier without treating request IDs as tasks."""

    if depth > 5:
        return ""
    if isinstance(value, Mapping):
        for key in _TASK_ID_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, (str, int)) and str(candidate).strip():
                return str(candidate).strip()
        if allow_plain_id:
            candidate = value.get("id")
            if isinstance(candidate, (str, int)) and str(candidate).strip():
                return str(candidate).strip()
        for key, child in value.items():
            if str(key).casefold() in _TASK_CONTAINER_KEYS:
                found = _extract_task_id(child, allow_plain_id=True, depth=depth + 1)
                if found:
                    return found
    elif isinstance(value, (list, tuple)):
        for child in list(value)[:16]:
            found = _extract_task_id(child, allow_plain_id=allow_plain_id, depth=depth + 1)
            if found:
                return found
    return ""


def _response_summary(payload: Any) -> dict[str, Any]:
    """Keep submit diagnostics useful while excluding response bodies and secrets."""

    if not isinstance(payload, Mapping):
        return {"response_type": type(payload).__name__}
    summary: dict[str, Any] = {
        "response_keys": [str(key)[:80] for key in list(payload.keys())[:32]],
    }
    for key in ("code", "msg", "message", "status"):
        value = payload.get(key)
        if isinstance(value, (str, int, float, bool)) and str(value).strip():
            summary[key] = str(value)[:240]
    data = payload.get("data")
    if isinstance(data, Mapping):
        summary["data_keys"] = [str(key)[:80] for key in list(data.keys())[:32]]
    elif data is not None:
        summary["data_type"] = type(data).__name__
    return summary


def _provider_error(response: Any, *, stage: str) -> AutoDLAudioError:
    status = int(getattr(response, "status_code", 0) or 0)
    return AutoDLAudioError(
        f"AutoDL 音频 {stage} 请求失败（HTTP {status}）",
        code=f"AUTODL_AUDIO_{stage.upper()}_HTTP_ERROR",
        details={"http_status": status, "stage": stage},
    )


def _convert_audio_bytes_to_mp3(data: bytes, output_path: Path) -> None:
    """Convert provider output to the project's stable MP3 artifact format."""

    if not data:
        raise AutoDLAudioError(
            "AutoDL 音频结果为空",
            code="AUTODL_AUDIO_OUTPUT_EMPTY",
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Keep already encoded MP3 responses usable on hosts without ffmpeg.
    if data.startswith(b"ID3"):
        output_path.write_bytes(data)
        return
    with tempfile.TemporaryDirectory(prefix="autodl-audio-") as temp_dir:
        source = Path(temp_dir) / "source.audio"
        source.write_bytes(data)
        try:
            completed = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(source),
                    "-codec:a",
                    "libmp3lame",
                    "-q:a",
                    "2",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise AutoDLAudioError(
                "AutoDL 返回的音频需要 ffmpeg 转换为 MP3",
                code="AUTODL_AUDIO_FFMPEG_MISSING",
            ) from exc
        if completed.returncode != 0 or not output_path.exists():
            raise AutoDLAudioError(
                "AutoDL 音频格式转换失败",
                code="AUTODL_AUDIO_FORMAT_CONVERSION_FAILED",
            )


async def generate_autodl_audio(
    *,
    api_key: str,
    base_url: str,
    workflow_id: str,
    input_text: str,
    voice_path: Path,
    output_path: Path,
    provider_mapping: Mapping[str, Any],
    parameters: Mapping[str, Any] | None = None,
    timeout_seconds: float = 900.0,
    poll_interval: float = 3.0,
) -> dict[str, Any]:
    """Submit one AutoDL workflow and persist its result as an MP3 file."""

    clean_text = str(input_text or "").strip()
    if not clean_text:
        raise AutoDLAudioError("音频文本不能为空", code="AUTODL_AUDIO_TEXT_REQUIRED")
    if not voice_path or not Path(voice_path).is_file():
        raise AutoDLAudioError("参考音频文件不存在", code="AUTODL_AUDIO_REFERENCE_REQUIRED")
    key = str(api_key or "").strip()
    if not key:
        raise AutoDLAudioError("AutoDL API Key 未配置", code="AUTODL_AUDIO_AUTH_REQUIRED")
    workflow = str(workflow_id or "").strip()
    if not workflow:
        raise AutoDLAudioError("AutoDL Workflow ID 未配置", code="AUTODL_AUDIO_WORKFLOW_REQUIRED")

    mapping = dict(provider_mapping or {})
    text_field = _metadata_field(mapping, "prompt_text", "text", "input", "prompt")
    voice_field = _metadata_field(
        mapping,
        "prompt_simple",
        "voice_reference",
        "reference_audio",
        "voice",
    )
    if not text_field:
        raise AutoDLAudioError(
            "AutoDL Workflow 未声明文本输入字段",
            code="AUTODL_AUDIO_TEXT_MAPPING_MISSING",
        )
    if not voice_field:
        raise AutoDLAudioError(
            "AutoDL Workflow 未声明参考音频字段",
            code="AUTODL_AUDIO_REFERENCE_MAPPING_MISSING",
        )

    suffix = Path(voice_path).suffix.lower().lstrip(".") or "wav"
    try:
        relay_url = await asyncio.to_thread(
            upload_media_bytes,
            Path(voice_path).read_bytes(),
            ext=suffix,
        )
    except Exception as exc:
        raise AutoDLAudioError(
            "参考音频上传到媒体中转失败",
            code="AUTODL_AUDIO_REFERENCE_RELAY_FAILED",
            details={"error_type": type(exc).__name__},
        ) from exc
    if not str(relay_url or "").startswith(("http://", "https://")):
        raise AutoDLAudioError(
            "媒体中转未返回 AutoDL 可访问的 HTTP(S) URL",
            code="AUTODL_AUDIO_REFERENCE_RELAY_URL_INVALID",
        )

    payload: dict[str, Any] = {text_field: clean_text, voice_field: relay_url}
    # IndexTTS2 accepts the same reference for optional emotion conditioning.
    emotion_reference = _metadata_field(mapping, "emo_ref_audio", "emotion_reference")
    if emotion_reference:
        payload[emotion_reference] = relay_url
    declared_parameter_fields = {
        _parameter_field(mapping, str(logical_key))
        for logical_key in mapping
        if str(logical_key).strip()
    }
    for logical_key, value in dict(parameters or {}).items():
        field = _parameter_field(mapping, str(logical_key))
        if (
            field
            and field in declared_parameter_fields
            and field not in payload
            and field not in {"model", "input", "response_format"}
        ):
            payload[field] = _safe_payload_value(value)

    import httpx

    headers = {
        "Authorization": key,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    submit_endpoint = _workflow_endpoint(base_url, workflow)
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=max(1.0, float(timeout_seconds)), follow_redirects=True) as client:
        response = await client.post(submit_endpoint, headers=headers, json=payload)
        if response.status_code >= 400:
            raise _provider_error(response, stage="submit")
        try:
            submit_body = response.json()
        except (TypeError, ValueError) as exc:
            raise AutoDLAudioError(
                "AutoDL 提交响应不是有效 JSON",
                code="AUTODL_AUDIO_SUBMIT_INVALID_JSON",
            ) from exc
        submit_data = _response_data(submit_body)
        task_id = _extract_task_id(submit_body)
        if not task_id:
            summary = _response_summary(submit_body)
            response_code = str(summary.get("code") or "unknown")
            response_message = str(summary.get("msg") or summary.get("message") or "").strip()
            suffix = f"（code={response_code}"
            if response_message:
                suffix += f"; msg={response_message[:160]}"
            suffix += ")"
            raise AutoDLAudioError(
                f"AutoDL 提交响应缺少任务 ID{suffix}",
                code="AUTODL_AUDIO_TASK_ID_MISSING",
                details={"stage": "submit", **summary},
            )

        deadline = time.perf_counter() + max(1.0, float(timeout_seconds))
        final_data: Mapping[str, Any] = {}
        status = str(submit_data.get("status") or "QUEUED").upper()
        while True:
            if time.perf_counter() >= deadline:
                raise AutoDLAudioError(
                    "AutoDL 音频任务轮询超时",
                    code="AUTODL_AUDIO_POLL_TIMEOUT",
                    details={"status": status},
                )
            result_response = await client.get(
                _result_endpoint(base_url, task_id),
                headers=headers,
            )
            if result_response.status_code >= 400:
                raise _provider_error(result_response, stage="poll")
            try:
                result_body = result_response.json()
            except (TypeError, ValueError) as exc:
                raise AutoDLAudioError(
                    "AutoDL 轮询响应不是有效 JSON",
                    code="AUTODL_AUDIO_POLL_INVALID_JSON",
                ) from exc
            final_data = _response_data(result_body)
            status = str(final_data.get("status") or "").upper()
            if status in {"FAILED", "ERROR", "CANCELLED"}:
                raise AutoDLAudioError(
                    "AutoDL 音频任务执行失败",
                    code="AUTODL_AUDIO_TASK_FAILED",
                    details={"status": status},
                )
            if status in {"SUCCESS", "SUCCEEDED", "COMPLETED", "DONE"}:
                break
            await asyncio.sleep(max(0.1, float(poll_interval)))

        results = final_data.get("results")
        if not isinstance(results, list):
            results = final_data.get("outputs")
        if not isinstance(results, list):
            results = []
        result_url = ""
        output_type = ""
        for item in results:
            if not isinstance(item, Mapping):
                continue
            candidate_type = str(item.get("type") or "").strip().lower()
            candidate_url = str(
                item.get("url")
                or item.get("audio_url")
                or item.get("audioUrl")
                or item.get("download_url")
                or item.get("path")
                or ""
            ).strip()
            if candidate_url and (not result_url or candidate_type in {"audio", "wav", "mp3"}):
                result_url = candidate_url
                output_type = candidate_type or "audio"
                if candidate_type in {"audio", "wav", "mp3"}:
                    break
        if not result_url:
            raise AutoDLAudioError(
                "AutoDL 成功响应缺少音频结果 URL",
                code="AUTODL_AUDIO_RESULT_URL_MISSING",
            )
        download_url = urljoin(f"{normalize_direct_model_base_url(base_url).rstrip('/')}/", result_url)
        audio_response = await client.get(download_url, headers={"Accept": "*/*"})
        if audio_response.status_code >= 400:
            raise _provider_error(audio_response, stage="download")
        audio_bytes = bytes(audio_response.content or b"")

    await asyncio.to_thread(_convert_audio_bytes_to_mp3, audio_bytes, Path(output_path))
    if not Path(output_path).is_file() or Path(output_path).stat().st_size <= 0:
        raise AutoDLAudioError(
            "AutoDL 音频结果落盘为空",
            code="AUTODL_AUDIO_OUTPUT_EMPTY",
        )
    return {
        "endpoint": submit_endpoint,
        "workflow_id": workflow,
        "task_id": task_id[:12],
        "status": status,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "output_type": output_type or "audio",
        "output_size": Path(output_path).stat().st_size,
        "output_mime_type": mimetypes.guess_type(str(output_path))[0] or "audio/mpeg",
    }


__all__ = ["AutoDLAudioError", "generate_autodl_audio"]
