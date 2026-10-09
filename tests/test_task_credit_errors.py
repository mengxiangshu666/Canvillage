import importlib
import sys

import pytest

from novelvideo.cognee.gateway_health import CogneeGatewayUnavailable
from novelvideo.novel_source import (
    NOVEL_IMPORT_REQUIRED_CODE,
    NOVEL_IMPORT_REQUIRED_MESSAGE,
    NovelImportRequiredError,
)
from novelvideo.shared.billing_errors import (
    INSUFFICIENT_CREDITS_CODE,
    INSUFFICIENT_CREDITS_MESSAGE,
    InsufficientCreditsStop,
)
from novelvideo.shared.provider_errors import (
    CONTENT_MODERATION_FAILED_CODE,
    CONTENT_MODERATION_FAILED_MESSAGE,
    INPUT_IMAGE_POLICY_FAILED_MESSAGE,
    OUTPUT_VIDEO_POLICY_FAILED_MESSAGE,
)

pytestmark = pytest.mark.m07


@pytest.mark.parametrize("body", [
    {"ray_id": "a467e75abe27ccc8", "detail": "private-provider-body"},
    {"ray_id": "https://private.example/?key=secret"},
    "private-provider-body",
])
def test_task_failure_maps_model_524_without_exposing_body(monkeypatch, body):
    from pydantic_ai.exceptions import ModelHTTPError

    runner = _import_celery_tasks(monkeypatch)
    exc = ModelHTTPError(status_code=524, model_name="gemini-3.8-flash", body=body)
    error, metadata, handled = runner._project_task_failure_for_exception(exc)
    assert handled is True
    assert "2 分钟" in error
    assert metadata["http_status"] == 524
    assert metadata["retryable"] is True
    assert "private" not in str((error, metadata))
    assert "secret" not in str((error, metadata))
    assert metadata.get("request_id") == (body.get("ray_id") if isinstance(body, dict) and body.get("ray_id") == "a467e75abe27ccc8" else None)


def _import_celery_tasks(monkeypatch):
    monkeypatch.delenv("ST_PROJECT_TASK_TIMEOUT_S", raising=False)
    monkeypatch.delenv("ST_PROJECT_VIDEO_TASK_TIMEOUT_S", raising=False)
    sys.modules.pop("novelvideo.task_backend.run_core", None)
    return importlib.import_module("novelvideo.task_backend.run_core")


def test_task_serialization_exposes_error_code() -> None:
    from novelvideo.api.routes.tasks import _serialize_task
    from novelvideo.task_state import TaskState

    task = TaskState(
        task_id="task_1",
        task_type="build_characters",
        username="a5",
        project="kk",
        episode=0,
        status="failed",
        metadata={"error_code": INSUFFICIENT_CREDITS_CODE},
    )

    assert _serialize_task(task)["error_code"] == INSUFFICIENT_CREDITS_CODE


def test_celery_task_failure_maps_insufficient_credits_stop(monkeypatch) -> None:
    celery_tasks = _import_celery_tasks(monkeypatch)

    stop = InsufficientCreditsStop(user_id="usr_1", cost=2, balance=1)

    error, metadata, handled = celery_tasks._project_task_failure_for_exception(stop)

    assert handled is True
    assert error == INSUFFICIENT_CREDITS_MESSAGE
    assert metadata["error_code"] == INSUFFICIENT_CREDITS_CODE
    assert metadata["required"] == 2
    assert metadata["balance"] == 1


def test_task_failure_maps_novel_import_prerequisite(monkeypatch) -> None:
    celery_tasks = _import_celery_tasks(monkeypatch)

    error, metadata, handled = celery_tasks._project_task_failure_for_exception(
        NovelImportRequiredError()
    )

    assert handled is True
    assert error == NOVEL_IMPORT_REQUIRED_MESSAGE
    assert metadata == {"error_code": NOVEL_IMPORT_REQUIRED_CODE}


def test_task_failure_maps_cognee_gateway_unavailable(monkeypatch) -> None:
    celery_tasks = _import_celery_tasks(monkeypatch)
    exc = CogneeGatewayUnavailable(model="DC-cognee-LLM", component="chat")

    error, metadata, handled = celery_tasks._project_task_failure_for_exception(exc)

    assert handled is True
    assert error == str(exc)
    assert metadata == {
        "error_code": "MODEL_CHANNEL_UNAVAILABLE",
        "model": "DC-cognee-LLM",
        "component": "chat",
    }


def test_task_failure_keeps_safe_cognee_gateway_diagnostics(monkeypatch) -> None:
    celery_tasks = _import_celery_tasks(monkeypatch)
    exc = CogneeGatewayUnavailable(
        model="DC-cognee-LLM",
        component="chat",
        reason="model_not_found",
        status_code=503,
    )

    _error, metadata, handled = celery_tasks._project_task_failure_for_exception(exc)

    assert handled is True
    assert metadata == {
        "error_code": "MODEL_CHANNEL_UNAVAILABLE",
        "model": "DC-cognee-LLM",
        "component": "chat",
        "reason": "model_not_found",
        "http_status": 503,
    }


def test_task_failure_keeps_structured_provider_diagnostics(monkeypatch) -> None:
    celery_tasks = _import_celery_tasks(monkeypatch)
    exc = RuntimeError("video submit failed")
    exc.provider_error_metadata = {
        "error_code": "VIDEO_ENDPOINT_NOT_FOUND",
        "http_status": 404,
        "stage": "submit",
        "protocol": "openai-video",
        "endpoint_class": "video-submit",
        "retryable": False,
        "suggested_action": "重新检测模型协议与提交路径，保存识别结果后再生成。",
        "request_id": "req-404",
        "request_contract": {
            "content_type": "application/json; charset=utf-8",
            "body_bytes": 321,
            "content_length": 321,
            "body_sha256": "0123456789abcdef",
            "payload_keys": ["model", "prompt", "metadata"],
            "metadata_keys": ["reference_images"],
            "media_counts": {"image": 2, "video": "invalid"},
            "prompt": "must-not-leak",
        },
        "secret": "must-not-leak",
    }

    error, metadata, handled = celery_tasks._project_task_failure_for_exception(exc)

    assert handled is True
    assert error == "video submit failed"
    assert metadata["error_code"] == "VIDEO_ENDPOINT_NOT_FOUND"
    assert metadata["request_id"] == "req-404"
    assert metadata["request_contract"] == {
        "content_type": "application/json; charset=utf-8",
        "payload_keys": ["model", "prompt", "metadata"],
        "metadata_keys": ["reference_images"],
        "media_counts": {"image": 2},
        "body_bytes": 321,
        "content_length": 321,
        "body_sha256": "0123456789abcdef",
    }
    assert "secret" not in metadata


def test_celery_task_failure_maps_output_moderation(monkeypatch) -> None:
    celery_tasks = _import_celery_tasks(monkeypatch)

    exc = RuntimeError(
        'HTTP 400: body={"error":{"message":"output_moderation",'
        '"code":"output_moderation"}}'
    )

    error, metadata, handled = celery_tasks._project_task_failure_for_exception(exc)

    assert handled is True
    assert error == CONTENT_MODERATION_FAILED_MESSAGE
    assert metadata["error_code"] == CONTENT_MODERATION_FAILED_CODE
    assert "output_moderation" in metadata["provider_error"]


def test_celery_task_failure_maps_output_video_policy_violation(monkeypatch) -> None:
    celery_tasks = _import_celery_tasks(monkeypatch)

    for request_id in ("request-id-a", "request-id-b"):
        exc = RuntimeError(
            "[OutputVideoSensitiveContentDetected.PolicyViolation] The request failed because "
            "the output video may be related to copyright restrictions. "
            f"Request id: {request_id}"
        )

        error, metadata, handled = celery_tasks._project_task_failure_for_exception(exc)

        assert handled is True
        assert error == OUTPUT_VIDEO_POLICY_FAILED_MESSAGE
        assert metadata["message"] == OUTPUT_VIDEO_POLICY_FAILED_MESSAGE
        assert metadata["error_code"] == CONTENT_MODERATION_FAILED_CODE
        assert "OutputVideoSensitiveContentDetected.PolicyViolation" in metadata["provider_error"]
        assert request_id in metadata["provider_error"]


def test_celery_task_failure_maps_input_image_policy_violation(monkeypatch) -> None:
    celery_tasks = _import_celery_tasks(monkeypatch)

    exc = RuntimeError(
        "[InputImageSensitiveContentDetected.PolicyViolation] The request failed because "
        "the input image may be related to copyright restrictions. "
        "Request id: dynamic-request-id"
    )

    error, metadata, handled = celery_tasks._project_task_failure_for_exception(exc)

    assert handled is True
    assert error == INPUT_IMAGE_POLICY_FAILED_MESSAGE
    assert metadata["message"] == INPUT_IMAGE_POLICY_FAILED_MESSAGE
    assert metadata["error_code"] == CONTENT_MODERATION_FAILED_CODE
    assert "InputImageSensitiveContentDetected.PolicyViolation" in metadata["provider_error"]
    assert "dynamic-request-id" in metadata["provider_error"]


def test_celery_task_failure_maps_soft_time_limit(monkeypatch) -> None:
    celery_tasks = _import_celery_tasks(monkeypatch)
    from celery.exceptions import SoftTimeLimitExceeded

    error, metadata, handled = celery_tasks._project_task_failure_for_exception(
        SoftTimeLimitExceeded()
    )

    assert handled is True
    assert error == "任务超过 30 分钟未完成，已自动放弃"
    assert metadata == {"error_code": "TASK_TIMEOUT", "timeout_seconds": 30 * 60}


def test_project_task_timeout_is_independent_from_celery_hard_limit(monkeypatch) -> None:
    celery_tasks = _import_celery_tasks(monkeypatch)

    assert celery_tasks._project_task_timeout_seconds() == 30 * 60


def test_freezone_video_task_has_a_longer_provider_completion_window(monkeypatch) -> None:
    celery_tasks = _import_celery_tasks(monkeypatch)

    assert celery_tasks._project_task_timeout_seconds("freezone_video_gen") == 60 * 60

    monkeypatch.setenv("ST_PROJECT_VIDEO_TASK_TIMEOUT_S", "5400")
    assert celery_tasks._project_task_timeout_seconds("freezone_video_gen") == 90 * 60


def test_celery_task_failure_maps_cooperative_task_timeout(monkeypatch) -> None:
    celery_tasks = _import_celery_tasks(monkeypatch)
    from novelvideo.task_backend.cancel import TaskTimedOut

    error, metadata, handled = celery_tasks._project_task_failure_for_exception(
        TaskTimedOut(timeout_seconds=30 * 60)
    )

    assert handled is True
    assert error == "任务超过 30 分钟未完成，已自动放弃"
    assert metadata == {"error_code": "TASK_TIMEOUT", "timeout_seconds": 30 * 60}


def test_celery_task_failure_reraises_non_business_base_exception(monkeypatch) -> None:
    celery_tasks = _import_celery_tasks(monkeypatch)

    with pytest.raises(KeyboardInterrupt):
        celery_tasks._project_task_failure_for_exception(KeyboardInterrupt())
