"""Regression coverage for NewAPI video diagnostic helpers."""

from novelvideo.generators.video.newapi_video_diagnostics import (
    NewApiVideoError,
    extract_request_id,
    safe_url_for_log,
    transport_error_message,
)


def test_newapi_error_exposes_actionable_credential_safe_contract() -> None:
    error = NewApiVideoError(
        "forbidden",
        request_id="req-403",
        http_status=403,
        stage="submit",
        url_path="/v1/videos",
    )

    assert error.diagnostic_contract(protocol="openai-video") == {
        "error_code": "VIDEO_AUTH_REJECTED",
        "http_status": 403,
        "stage": "submit",
        "protocol": "openai-video",
        "endpoint_class": "video-submit",
        "retryable": False,
        "suggested_action": "检查该模型的 URL、Key、鉴权协议和上游账号权限后重新检测。",
        "request_id": "req-403",
    }


def test_newapi_error_classifies_complete_body_canonicalize_failure_as_relay_issue() -> None:
    error = NewApiVideoError(
        'HTTP 400 {"code":"invalid_request","message":"canonicalize JSON request body: unexpected end of JSON input"}',
        http_status=400,
        response_text=(
            '{"code":"invalid_request","message":"canonicalize JSON request body: '
            'unexpected end of JSON input"}'
        ),
        stage="submit",
        url_path="/v1/videos",
        request_contract={
            "body_bytes": 232,
            "content_length": 232,
            "body_sha256": "0123456789abcdef",
        },
    )

    contract = error.diagnostic_contract(protocol="openai-video")

    assert contract["error_code"] == "VIDEO_RELAY_JSON_BODY_REJECTED"
    assert contract["retryable"] is False
    assert "本地已生成完整 JSON 请求体" in str(contract["suggested_action"])


def test_newapi_error_keeps_generic_json_body_class_without_local_length_proof() -> None:
    error = NewApiVideoError(
        "canonicalize JSON request body: unexpected end of JSON input",
        http_status=400,
        response_text="canonicalize JSON request body: unexpected end of JSON input",
        stage="submit",
        url_path="/v1/videos",
    )

    assert error.diagnostic_contract(protocol="openai-video")["error_code"] == (
        "VIDEO_JSON_BODY_REJECTED"
    )


def test_newapi_error_classifies_html_management_page_response() -> None:
    error = NewApiVideoError(
        "视频提交响应不是有效 JSON；content_type=text/html",
        http_status=200,
        response_text="<!doctype html><title>New API</title>",
        stage="submit",
        url_path="/v1/videos",
    )

    contract = error.diagnostic_contract(protocol="openai-video")
    assert contract["error_code"] == "VIDEO_ENDPOINT_HTML_RESPONSE"
    assert contract["retryable"] is False


def test_newapi_error_classifies_empty_success_response() -> None:
    error = NewApiVideoError(
        "视频提交响应为空",
        http_status=200,
        response_text="",
        stage="submit",
        url_path="/v1/videos",
    )

    assert error.diagnostic_contract(protocol="openai-video")["error_code"] == (
        "VIDEO_EMPTY_RESPONSE"
    )


def test_newapi_error_classifies_html_result_download_separately() -> None:
    error = NewApiVideoError(
        "视频结果下载地址返回了 HTML 页面",
        http_status=200,
        response_text="<!doctype html><title>管理页面</title>",
        stage="download",
        url_path="/videos/task-1/content",
    )

    contract = error.diagnostic_contract(protocol="openai-video")
    assert contract["error_code"] == "VIDEO_RESULT_HTML_RESPONSE"
    assert contract["endpoint_class"] == "video-result-download"


def test_newapi_error_classifies_missing_result_endpoint_as_download_failure() -> None:
    error = NewApiVideoError(
        "视频结果下载失败：HTTP 404",
        http_status=404,
        response_text="not found",
        stage="download",
        url_path="/videos/task-1/content",
    )

    assert error.diagnostic_contract(protocol="openai-video")["error_code"] == (
        "VIDEO_RESULT_DOWNLOAD_FAILED"
    )


def test_extract_request_id_prefers_gateway_header_over_error_body() -> None:
    assert extract_request_id(
        '{"error":{"request_id":"body-request"}}',
        {"x-newapi-request-id": "header-request"},
    ) == "header-request"


def test_extract_request_id_supports_json_and_plain_text_formats() -> None:
    assert extract_request_id('{"error":{"requestId":"json-request"}}') == "json-request"
    assert extract_request_id("gateway unavailable; Request ID: text-request_01") == (
        "text-request_01"
    )


def test_safe_url_for_log_redacts_credentials_but_keeps_actionable_location() -> None:
    safe_url = safe_url_for_log(
        "https://gateway.invalid/v1/videos?id=task-1&access_token=secret&sig=abc#anchor"
    )

    assert safe_url.startswith("https://gateway.invalid/v1/videos?id=task-1&")
    assert "secret" not in safe_url
    assert "abc" not in safe_url
    assert "access_token=%2A%2A%2A" in safe_url
    assert "sig=%2A%2A%2A" in safe_url
    assert safe_url.endswith("#***")


def test_transport_error_message_uses_the_safe_url_representation() -> None:
    message = transport_error_message(
        "task query",
        "https://gateway.invalid/v1/videos/task-1?api_key=secret",
        TimeoutError("deadline"),
    )

    assert message == (
        "Village Infinite Canvas API task query transport failed: TimeoutError: deadline; "
        "url=https://gateway.invalid/v1/videos/task-1?api_key=%2A%2A%2A"
    )


def test_channelless_group_is_reported_as_provisioning_not_a_transient_503() -> None:
    """A 503 that names a group is the relay's channel assignment, not an outage.

    Retrying cannot change it: the model is in the relay's directory, so nothing
    on our side is wrong, and the missing piece is the account's group.  A plain
    503 branch used to answer "稍后重试", which sent the operator in a circle.
    """

    error = NewApiVideoError(
        "Village Infinite Canvas API submit failed: HTTP 503",
        http_status=503,
        response_text=(
            '{"error":{"code":"model_not_found","message":"No available channel for '
            'model MiniMax-H3 under group CN-H3 (distributor) (request id: 2026abc)",'
            '"type":"new_api_error"}}'
        ),
        stage="submit",
        url_path="/v1/video/generations",
    )

    contract = error.diagnostic_contract(protocol="openai-video")

    assert contract["error_code"] == "VIDEO_CHANNEL_UNAVAILABLE"
    assert contract["retryable"] is False
    assert contract["channel_group"] == "CN-H3"
    assert "CN-H3" in str(contract["suggested_action"])
    assert "重试和改参数都不会改变结果" in str(contract["suggested_action"])


def test_a_503_without_a_channel_claim_keeps_the_transient_branch() -> None:
    error = NewApiVideoError(
        "Village Infinite Canvas API submit failed: HTTP 503",
        http_status=503,
        response_text='{"error":{"message":"upstream temporarily unavailable"}}',
        stage="submit",
        url_path="/v1/videos",
    )

    contract = error.diagnostic_contract(protocol="openai-video")

    assert contract["error_code"] == "VIDEO_UPSTREAM_UNAVAILABLE"
    assert contract["retryable"] is True
    assert "channel_group" not in contract
